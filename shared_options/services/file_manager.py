import json
import threading
import time
import os
import shutil
from pathlib import Path
from datetime import datetime, timezone
from typing import Iterable, List, Optional

from shared_options.services.shutdown_handler import ShutdownManager
from shared_options.log.logger_singleton import getLogger
from shared_options.services.utils import try_send

# --- Tunables ---
DEFAULT_LOW_SPACE_BYTES = 200 * 1024 * 1024    # 200 MB - warning threshold
DEFAULT_CRITICAL_SPACE_BYTES = 50 * 1024 * 1024 # 50 MB - emergency cleanup threshold
DEFAULT_BACKOFF_SECONDS = 5                     # backoff after disk error

def _safe_iso_timestamp():
    # filesystem-safe timestamp: no colons
    return datetime.now(timezone.utc).isoformat().replace(":", "-")

def _has_free_space(path: Path, min_free: int) -> tuple[bool, int]:
    try:
        usage = shutil.disk_usage(path)
        return usage.free >= min_free, usage.free
    except Exception:
        # If we can't determine, assume it's okay (avoid false positives)
        return True, 10**12

class FileManager:
    """
    Thread-safe file manager that buffers entries into temp JSONL files.

    Features:
      * Writes batches to numbered .jsonl files under a temp dir (atomic writes).
      * Safely combines jsonl files into bundles and optionally sends them with try_send().
      * Resumes numbering from existing files on init.
      * Guards against disk-full hangs using pre-write checks, backoff, and emergency cleanup.
      * Non-blocking upload (sending happens in background to avoid blocking writer).
      * Integrates with ShutdownManager for graceful close.
    """

    def __init__(
        self,
        filepath: str,
        flush_interval: int = 10,
        max_buffer_size: int = 100,
        stop_event: Optional[threading.Event] = None,
        low_space_bytes: int = DEFAULT_LOW_SPACE_BYTES,
        critical_space_bytes: int = DEFAULT_CRITICAL_SPACE_BYTES,
        backoff_seconds: int = DEFAULT_BACKOFF_SECONDS,
    ):
        self.filepath = Path(filepath)
        self.temp_dir = self.filepath.parent / f"{self.filepath.stem}_tmp"
        self.flush_interval = flush_interval
        self.max_buffer_size = max_buffer_size
        self._buffer: List[dict] = []
        self._lock = threading.RLock()
        self._stop_event = stop_event or threading.Event()
        self.logger = getLogger()

        self.low_space_bytes = low_space_bytes
        self.critical_space_bytes = critical_space_bytes
        self.backoff_seconds = backoff_seconds

        # Ensure directories exist
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        # Continue numbering from existing files
        self._temp_file_counter = self._init_temp_counter()

        # Initialize main file if missing (keep prior behavior)
        if not self.filepath.exists():
            try:
                with open(self.filepath, "w", encoding="utf-8") as f:
                    f.write("[]")
            except Exception as e:
                self.logger.logMessage(f"[FileManager] Error initializing main file {self.filepath}: {e}")

        # Register with ShutdownManager (preserve original pattern)
        try:
            ShutdownManager.register(
                f"FileManager({self.filepath.name})",
                lambda reason=None: self.close()
            )
        except TypeError:
            ShutdownManager.init(error_logger=self.logger.logMessage)
            ShutdownManager.register(
                f"FileManager({self.filepath.name})",
                lambda reason=None: self.close()
            )

        # Background flush loop
        self._thread = threading.Thread(target=self._flush_loop, daemon=True, name=f"FileManagerFlush-{self.filepath.name}")
        self._thread.start()

        self.logger.logMessage(f"[FileManager] Initialized for {self.filepath} (temp_dir={self.temp_dir}, start_index={self._temp_file_counter})")

    # -------------------------
    # Initialization helpers
    # -------------------------
    def _init_temp_counter(self) -> int:
        existing_files = sorted(self.temp_dir.glob("*.jsonl"))
        if not existing_files:
            return 0
        max_index = 0
        for f in existing_files:
            stem = f.stem
            if stem.isdigit():
                try:
                    idx = int(stem)
                    if idx > max_index:
                        max_index = idx
                except Exception:
                    pass
        return max_index

    # -------------------------
    # Public interface (unchanged)
    # -------------------------
    def add_entry(self, entry):
        """Queue a Python object or dict for later write (same behavior as before)."""
        with self._lock:
            if hasattr(entry, "dict"):
                entry = entry.dict()
            elif not isinstance(entry, dict):
                entry = entry.__dict__

            self._buffer.append(entry)
            if len(self._buffer) >= self.max_buffer_size:
                # flush synchronously under lock to preserve ordering
                try:
                    self._flush_locked()
                except Exception as e:
                    # Catch to avoid bubbling to caller in threaded contexts
                    self.logger.logMessage(f"[FileManager] Error during add_entry flush: {e}")

    def close(self):
        """Flush remaining buffer and stop background thread."""
        with self._lock:
            if self._stop_event.is_set():
                return
            self._stop_event.set()

        self.logger.logMessage(f"[FileManager] Closing {self.filepath}...")
        # Attempt a final flush (best-effort)
        with self._lock:
            try:
                self._flush_locked()
            except Exception as e:
                self.logger.logMessage(f"[FileManager] Final flush error: {e}")

        # Wait for background thread to exit cleanly
        self._thread.join(timeout=5)
        self.logger.logMessage(f"[FileManager] Closed cleanly.")

    def combine_temp_files(self):
        """
        Combine *all* temp jsonl files into one JSON array and send it (non-blocking).
        Deletes the temp files only after they are read.
        """
        with self._lock:
            temp_files = sorted(self.temp_dir.glob("*.jsonl"))
        if not temp_files:
            return
        combined_path = self._combine_files(temp_files, delete=True)
        if combined_path:
            # send in background to avoid blocking
            threading.Thread(target=self._send_background, args=(combined_path,), daemon=True).start()
            self.logger.logMessage(f"[FileManager] Combined and queued send of {combined_path}")

    def combine_and_rotate(self, bundle_limit: int = 100):
        """
        Combine up to `bundle_limit` temp files into a bundle and upload.
        This does not block ongoing writes.
        """
        # pick a slice under lock
        with self._lock:
            temp_files = sorted(self.temp_dir.glob("*.jsonl"))
            if not temp_files:
                return
            bundle_files = temp_files[:bundle_limit]

        combined_path = self._combine_files(bundle_files, delete=True)
        if combined_path:
            threading.Thread(target=self._send_background, args=(combined_path,), daemon=True).start()
            self.logger.logMessage(f"[FileManager] Created bundle: {combined_path}")

    # -------------------------
    # Internal helpers
    # -------------------------
    def _flush_loop(self):
        while not self._stop_event.is_set():
            try:
                with self._lock:
                    self._flush_locked()
            except Exception as e:
                self.logger.logMessage(f"[FileManager] Background flush error: {e}")
            # Wait interruptible
            self._stop_event.wait(self.flush_interval)

    def _flush_locked(self):
        """
        Flush the in-memory buffer into a new numbered .jsonl file (atomic).
        This method is called while holding self._lock.
        """
        if not self._buffer:
            return

        # Snapshot buffer and clear it so producers don't block
        entries = self._buffer
        self._buffer = []

        # Safety: if disk critically low try cleanup & re-check
        ok, free = _has_free_space(self.filepath.parent, self.low_space_bytes)
        if not ok:
            self.logger.logMessage(f"[FileManager] Low disk space ({free/1024/1024:.1f} MB). Attempting emergency cleanup.")
            # Attempt to free space; if still low, buffer entries back and backoff
            self._emergency_cleanup()
            ok2, free2 = _has_free_space(self.filepath.parent, self.low_space_bytes)
            if not ok2:
                # Return entries to buffer (put them in front to preserve ordering)
                with self._lock:
                    self._buffer = entries + self._buffer
                self.logger.logMessage(f"[FileManager] Still low on disk after cleanup ({free2/1024/1024:.1f} MB). Backing off {self.backoff_seconds}s.")
                time.sleep(self.backoff_seconds)
                return

        # Attempt to write atomically
        try:
            self._write_temp_file_atomic(entries)
        except OSError as e:
            self.logger.logMessage(f"[FileManager] OSError during write: {e}. Returning entries to buffer and sleeping.")
            # Return entries to buffer so data isn't lost
            with self._lock:
                self._buffer = entries + self._buffer
            time.sleep(self.backoff_seconds)
        except Exception as e:
            # On unexpected errors we also return entries to buffer and continue
            self.logger.logMessage(f"[FileManager] Unexpected error writing temp file: {e}")
            with self._lock:
                self._buffer = entries + self._buffer

    def _write_temp_file_atomic(self, entries: Iterable[dict]):
        """
        Create a numbered temp file atomically:
          1) Write to a .tmp file in temp_dir
          2) fsync the file
          3) os.replace to final .jsonl name (atomic)
          4) fsync directory (best-effort)
        """
        # increment counter under lock to avoid collisions across threads
        self._temp_file_counter += 1
        index = self._temp_file_counter
        final_path = self.temp_dir / f"{index:06d}.jsonl"
        tmp_path = self.temp_dir / f".{index:06d}.jsonl.tmp"

        # Serialize & write
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                for entry in entries:
                    # Pydantic models handled if .json exists, otherwise default=str safe for datetimes
                    if hasattr(entry, "json"):
                        f.write(entry.json(separators=(",", ":")) + "\n")
                    else:
                        f.write(json.dumps(entry, separators=(",", ":"), default=str) + "\n")
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    # fsync can be expensive, but we attempt; if it fails keep going (best-effort)
                    self.logger.logMessage("[FileManager] fsync failed on temp file (non-fatal).")

            # Atomic move to final name
            os.replace(str(tmp_path), str(final_path))

            # Best-effort: fsync directory so rename is durable (may require root on some FS)
            try:
                dirfd = os.open(str(self.temp_dir), os.O_DIRECTORY)
                try:
                    os.fsync(dirfd)
                finally:
                    os.close(dirfd)
            except Exception:
                # Non-fatal; log and continue
                self.logger.logMessage("[FileManager] fsync on temp_dir failed (non-fatal).")

            self.logger.logMessage(f"[FileManager] Wrote temp file {final_path.name} ({final_path.stat().st_size/1024:.1f} KB)")

        except Exception as e:
            # Clean up tmp file if it exists and re-raise so caller handles buffering/backoff
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass
            raise

    def _combine_files(self, file_list: List[Path], delete: bool = False) -> Optional[Path]:
        """
        Combine multiple .jsonl files into a single JSON bundle.
        This reads files sequentially and thus can be memory intensive for very large bundles.
        The function writes the bundle atomically and returns the bundle path.
        """
        if not file_list:
            return None

        all_entries = []
        for temp_file in file_list:
            # Defensive read: a file may get removed by another process; skip on error
            try:
                with open(temp_file, "r", encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            all_entries.append(json.loads(line))
                        except Exception:
                            # If a single line fails to parse, log and continue
                            self.logger.logMessage(f"[FileManager] Skipping invalid JSON line in {temp_file}")
                            continue
                if delete:
                    try:
                        temp_file.unlink()
                    except Exception as e:
                        self.logger.logMessage(f"[FileManager] Could not delete temp file {temp_file}: {e}")
            except Exception as e:
                self.logger.logMessage(f"[FileManager] Error reading {temp_file}: {e}")

        if not all_entries:
            return None

        timestamp = _safe_iso_timestamp()
        bundle_name = f"{self.filepath.stem}_bundle_{timestamp}.json"
        bundle_tmp = self.filepath.parent / f".{bundle_name}.tmp"
        bundle_final = self.filepath.parent / bundle_name

        # Before writing bundle, check disk
        ok, free = _has_free_space(self.filepath.parent, self.low_space_bytes)
        if not ok:
            self.logger.logMessage(f"[FileManager] Low disk space before creating bundle ({free/1024/1024:.1f} MB). Attempting emergency cleanup.")
            self._emergency_cleanup()
            ok2, free2 = _has_free_space(self.filepath.parent, self.low_space_bytes)
            if not ok2:
                self.logger.logMessage(f"[FileManager] Still low on disk ({free2/1024/1024:.1f} MB). Aborting bundle creation.")
                return None

        # Write bundle atomically
        try:
            with open(bundle_tmp, "w", encoding="utf-8") as f:
                json.dump(all_entries, f, indent=2, default=str)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    self.logger.logMessage("[FileManager] fsync failed on bundle tmp (non-fatal).")
            os.replace(bundle_tmp, bundle_final)
            # fsync dir (best-effort)
            try:
                dirfd = os.open(str(self.filepath.parent), os.O_DIRECTORY)
                try:
                    os.fsync(dirfd)
                finally:
                    os.close(dirfd)
            except Exception:
                self.logger.logMessage("[FileManager] fsync on bundle dir failed (non-fatal).")

            self.logger.logMessage(f"[FileManager] Created bundle {bundle_final.name} ({bundle_final.stat().st_size/1024:.1f} KB)")
            return bundle_final
        except Exception as e:
            try:
                if bundle_tmp.exists():
                    bundle_tmp.unlink()
            except Exception:
                pass
            self.logger.logMessage(f"[FileManager] Failed to write bundle {bundle_final}: {e}")
            return None

    def _send_background(self, bundle_path: Path):
        """Send the given bundle using try_send() — run in a thread so uploads don't block."""
        try:
            try_send(bundle_path)
            self.logger.logMessage(f"[FileManager] Sent bundle: {bundle_path.name}")
        except Exception as e:
            self.logger.logMessage(f"[FileManager] Upload failed for {bundle_path}: {e}")

    # -------------------------
    # Emergency cleanup logic
    # -------------------------
    def _emergency_cleanup(self):
        """
        Attempt to free disk space aggressively:
          1) remove oldest bundle files in the parent directory
          2) remove oldest temp jsonl files
        Used when low/critical disk thresholds are hit.
        """
        try:
            # Step 1: remove oldest bundle files (bundle pattern: *_bundle_*.json)
            parent = self.filepath.parent
            bundles = sorted(parent.glob(f"{self.filepath.stem}_bundle_*.json"), key=lambda p: p.stat().st_mtime)
            freed = 0
            for b in bundles:
                try:
                    size = b.stat().st_size
                    b.unlink()
                    freed += size
                    self.logger.logMessage(f"[FileManager] Emergency cleanup removed bundle {b.name} ({size/1024:.1f} KB)")
                    ok, free = _has_free_space(parent, self.low_space_bytes)
                    if ok:
                        return
                except Exception as e:
                    self.logger.logMessage(f"[FileManager] Could not remove bundle {b}: {e}")

            # Step 2: remove oldest temp files
            temp_files = sorted(self.temp_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
            for t in temp_files:
                try:
                    size = t.stat().st_size
                    t.unlink()
                    freed += size
                    self.logger.logMessage(f"[FileManager] Emergency cleanup removed temp {t.name} ({size/1024:.1f} KB)")
                    ok, free = _has_free_space(parent, self.low_space_bytes)
                    if ok:
                        return
                except Exception as e:
                    self.logger.logMessage(f"[FileManager] Could not remove temp {t}: {e}")

            # If still not enough, log a critical message
            ok_final, free_final = _has_free_space(parent, self.low_space_bytes)
            self.logger.logMessage(f"[FileManager] Emergency cleanup finished; freed ~{freed/1024:.1f} KB. Free={free_final/1024/1024:.1f} MB (ok={ok_final})")
        except Exception as e:
            self.logger.logMessage(f"[FileManager] Emergency cleanup failed: {e}")

