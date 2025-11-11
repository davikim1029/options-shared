# utils.py
from datetime import datetime,timedelta
from shared_options.models.OptionFeature import OptionFeature
from shared_options.models.option import OptionContract, OptionGreeks
import json
import os
import time
from dataclasses import is_dataclass, fields, is_dataclass
from typing import get_type_hints, List, Union, TypeVar, Dict, Any, Type, Union
import tempfile
from pathlib import Path
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from shared_options.log.logger_singleton import getLogger
import sys
import time as pyTime
import requests
import pandas_market_calendars as mcal
import pytz


def extract_features_from_snapshot(snapshot: Dict) -> OptionFeature:
    """Convert raw snapshot JSON to OptionFeature dataclass."""
    expiry_str = snapshot.get("expiryDate")
    timestamp_str = snapshot.get("timestamp")

    days_to_exp = 0
    if expiry_str and timestamp_str:
        try:
            expiry_dt = datetime.fromisoformat(expiry_str)
            timestamp_dt = datetime.fromisoformat(timestamp_str)
            days_to_exp = (expiry_dt - timestamp_dt).days
        except:
            days_to_exp = 0

    bid = float(snapshot.get("bid", 0))
    ask = float(snapshot.get("ask", 0))
    near = float(snapshot.get("nearPrice", 0))
    strike = float(snapshot.get("strikePrice", 0))
    mid_price = (bid + ask) / 2 if bid and ask else None
    spread = ask - bid if bid and ask else None
    moneyness = (near - strike) / near if near else None

    g = snapshot.get("greeks", {}) or {}

    return OptionFeature(
        symbol=snapshot.get("symbol", ""),
        osiKey=snapshot.get("osiKey", ""),
        optionType=1 if str(snapshot.get("optionType", "CALL")).upper() == "CALL" else 0,
        strikePrice=strike,
        lastPrice=float(snapshot.get("lastPrice", 0)),
        bid=bid,
        ask=ask,
        bidSize=float(snapshot.get("bidSize", 0)),
        askSize=float(snapshot.get("askSize", 0)),
        volume=float(snapshot.get("volume", 0)),
        openInterest=float(snapshot.get("openInterest", 0)),
        nearPrice=near,
        inTheMoney=1 if str(snapshot.get("inTheMoney", "n")).lower().startswith("y") else 0,
        delta=float(g.get("delta", 0)),
        gamma=float(g.get("gamma", 0)),
        theta=float(g.get("theta", 0)),
        vega=float(g.get("vega", 0)),
        rho=float(g.get("rho", 0)),
        iv=float(g.get("iv", 0)),
        daysToExpiration=days_to_exp,
        spread=spread,
        midPrice=mid_price,
        moneyness=moneyness
    )

def features_to_array(feature: OptionFeature):
    """Convert OptionFeature into a numeric array for ML models."""
    return [
        feature.optionType,
        feature.strikePrice,
        feature.lastPrice,
        feature.bid,
        feature.ask,
        feature.bidSize,
        feature.askSize,
        feature.volume,
        feature.openInterest,
        feature.nearPrice,
        feature.inTheMoney,
        feature.delta,
        feature.gamma,
        feature.theta,
        feature.vega,
        feature.rho,
        feature.iv,
        feature.spread or 0,
        feature.midPrice or 0,
        feature.moneyness or 0,
        feature.daysToExpiration
    ]
    



def option_contract_to_feature(opt: OptionContract) -> OptionFeature:
    """
    Convert an OptionContract instance into a shared OptionFeature Pydantic model.
    """
    # Compute days to expiration
    days_to_exp = None
    if opt.expiryDate:
        days_to_exp = (opt.expiryDate.astimezone() - datetime.now().astimezone()).total_seconds() / 86400.0

    # Spread and mid price
    spread = None
    mid_price = None
    if opt.bid is not None and opt.ask is not None:
        spread = float(opt.ask) - float(opt.bid)
        mid_price = (float(opt.ask) + float(opt.bid)) / 2.0

    # Moneyness
    moneyness = None
    if opt.nearPrice is not None and opt.strikePrice is not None:
        moneyness = (float(opt.nearPrice) - float(opt.strikePrice)) / float(opt.nearPrice)

    # Greeks
    greeks = opt.OptionGreeks or OptionGreeks()

    feature = OptionFeature(
        symbol=opt.symbol,
        displayName=opt.displaySymbol,
        osiKey=opt.osiKey,
        optionType=1 if opt.optionType.upper() == "CALL" else 0,
        strikePrice=float(opt.strikePrice),
        lastPrice=float(opt.lastPrice) if opt.lastPrice is not None else 0.0,
        bid=float(opt.bid) if opt.bid is not None else 0.0,
        ask=float(opt.ask) if opt.ask is not None else 0.0,
        bidSize=float(opt.bidSize) if opt.bidSize is not None else 0.0,
        askSize=float(opt.askSize) if opt.askSize is not None else 0.0,
        volume=float(opt.volume) if opt.volume is not None else 0.0,
        openInterest=float(opt.openInterest) if opt.openInterest is not None else 0.0,
        nearPrice=float(opt.nearPrice) if opt.nearPrice is not None else 0.0,
        inTheMoney=1 if (opt.inTheMoney or "").lower().startswith("y") else 0,
        delta=float(greeks.delta) if greeks.delta is not None else 0.0,
        gamma=float(greeks.gamma) if greeks.gamma is not None else 0.0,
        theta=float(greeks.theta) if greeks.theta is not None else 0.0,
        vega=float(greeks.vega) if greeks.vega is not None else 0.0,
        rho=float(greeks.rho) if greeks.rho is not None else 0.0,
        iv=float(greeks.iv) if greeks.iv is not None else 0.0,
        daysToExpiration=float(days_to_exp) if days_to_exp is not None else 0.0,
        spread=spread,
        midPrice=mid_price,
        moneyness=moneyness,
        )

    return feature

def wait_until_market_open(stop_event=None):
    # Create NYSE calendar
    nyse = mcal.get_calendar('NYSE')
    eastern = pytz.timezone("America/New_York")

    """Waits until the next NYSE market open if currently closed."""
    now = datetime.now(tz=eastern)
    # Get trading schedule for today and tomorrow
    schedule = nyse.schedule(start_date=now.date(), end_date=(now + timedelta(days=1)).date())

    if schedule.empty:
        # Market closed today (holiday or weekend)
        next_valid_day = nyse.valid_days(start_date=now.date(), end_date=now + timedelta(days=7))[0]
        next_open_utc = nyse.schedule(start_date=next_valid_day, end_date=next_valid_day).iloc[0]['market_open'].to_pydatetime()
        next_open = next_open_utc.astimezone(eastern)
    else:
        today_open = schedule.iloc[0]['market_open'].to_pydatetime().astimezone(eastern)
        today_close = schedule.iloc[0]['market_close'].to_pydatetime().astimezone(eastern)

        if now < today_open:
            next_open = today_open
        elif now > today_close:
            # After close — find next open day
            now = datetime.now(tz=eastern)
            start_date = (now + timedelta(days=1)).date()
            end_date = (now + timedelta(days=7)).date()
            next_valid_day = nyse.valid_days(start_date=start_date, end_date=end_date)[0]
            next_open_utc = nyse.schedule(start_date=next_valid_day, end_date=next_valid_day).iloc[0]['market_open'].to_pydatetime()
            next_open = next_open_utc.astimezone(eastern)
        else:
            return True


    # Calculate wait time
    wait_seconds = (next_open - now).total_seconds()
    hours, rem = divmod(wait_seconds, 3600)
    mins, secs = divmod(rem, 60)
    logger = getLogger()
    logger.logMessage(f"[Market Hours] Market closed — waiting {int(hours)}h {int(mins)}m {int(secs)}s until next open at {next_open}.")

    wait_interruptible(stop_event, wait_seconds)

    return True


def load_json_cache(file_path, max_age_seconds=86400):
    if not os.path.exists(file_path):
        return None
    stat = os.stat(file_path)
    if time.time() - stat.st_mtime > max_age_seconds:
        return None
    with open(file_path, "r") as f:
        return json.load(f)

def save_json_cache(file_path, data):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w") as f:
        json.dump(data, f)

def get_boolean_input(prompt_message: str,defaultValue: bool = False, defaultOnEnter:bool = True):
    while True:
        user_input = input(prompt_message + "\n").lower()
        if user_input == "true":
            return True
        elif user_input == "false":
            return False
        elif user_input in ("", None) and defaultOnEnter:
            validate = yes_no(f"Take the default value ({defaultValue})?")
            if validate:
                return defaultValue
        else:
            print("Invalid input. Please enter 'True' or 'False'.")
            
            
def yes_no(prompt: str, defaultResponse: bool = True, defaultOnEnter:bool = True) -> bool:
    defaultYn = "Yes" if defaultResponse else "No"
    while True:
        response = input(f"{prompt} (y/n): ").strip().lower()
        if response in ("y", "yes"):
            return True
        elif response in ("n", "no"):
            return False
        elif response in ("",None):
            return defaultResponse
        else:
            print("Please enter 'y' or 'n'.")
            

T = TypeVar("T")

def from_dict(cls: Type[T], data: Union[Dict[str, Any], List[Any]]) -> T:
    """
    Recursively converts a dict (or list of dicts) into dataclass instances.
    Handles nested dataclasses, lists, and Optional fields.
    """
    # If data is a list, convert each element
    if isinstance(data, list):
        # Attempt to get the inner type if cls is typing.List
        if hasattr(cls, "__origin__") and cls.__origin__ == list and hasattr(cls, "__args__"):
            inner_type = cls.__args__[0]
            return [from_dict(inner_type, item) for item in data]
        else:
            # Fallback: just return the list as-is
            return data

    # If cls is not a dataclass, return data directly
    if not is_dataclass(cls):
        return data

    # cls is a dataclass, get type hints
    type_hints = get_type_hints(cls)

    # Build a dict of field values
    init_values = {}
    for f in fields(cls):
        field_name = f.name
        field_type = type_hints.get(field_name, f.type)

        if field_name not in data or data[field_name] is None:
            init_values[field_name] = None
            continue

        value = data[field_name]

        # Handle Optional[T]
        origin = getattr(field_type, "__origin__", None)
        args = getattr(field_type, "__args__", ())

        if origin is Union and type(None) in args:
            # Optional[T] -> unwrap the inner type
            inner_type = args[0] if args[0] != type(None) else args[1]
            init_values[field_name] = from_dict(inner_type, value)
        # Handle List[T]
        elif origin is list and args:
            inner_type = args[0]
            init_values[field_name] = [from_dict(inner_type, v) for v in value]
        # Handle nested dataclass
        elif is_dataclass(field_type):
            init_values[field_name] = from_dict(field_type, value)
        else:
            # Primitive type, assign directly
            init_values[field_name] = value

    return cls(**init_values)



DEFAULT_FLAG = Path.cwd() / ".scanner_reload"

def _resolve_path(path: Union[str, Path, None]) -> Path:
    return Path(path).expanduser() if path else DEFAULT_FLAG

def set_reload_flag(path: Union[str, Path, None] = None, content: str = "1") -> bool:
    """
    Create or overwrite the flag file atomically.
    Returns True on success, False on error.
    """
    flag_path = _resolve_path(path)
    flag_dir = flag_path.parent
    try:
        flag_dir.mkdir(parents=True, exist_ok=True)
        # Create a temp file in the same directory to ensure atomic move/replace works across filesystems.
        with tempfile.NamedTemporaryFile("w", delete=False, dir=str(flag_dir), prefix=".tmp_flag_") as tf:
            tf.write(content)
            tf.flush()
            os.fsync(tf.fileno())
            tmpname = tf.name
        # Atomic replace (works on Windows and Unix)
        os.replace(tmpname, str(flag_path))
        return True
    except Exception as e:
        # Log or handle as you prefer; return False for caller to react
        # print("Failed to set reload flag:", e)
        try:
            # best-effort cleanup of temp file
            if 'tmpname' in locals() and os.path.exists(tmpname):
                os.remove(tmpname)
        except Exception:
            pass
        return False

def clear_reload_flag(path: Union[str, Path, None] = None) -> bool:
    """
    Remove the flag file if present.
    Returns True if removed or didn't exist, False on error.
    """
    flag_path = _resolve_path(path)
    try:
        if flag_path.exists():
            flag_path.unlink()
        return True
    except Exception:
        return False

def is_reload_flag_set(path: Union[str, Path, None] = None) -> bool:
    """
    Check whether the flag file exists and (optionally) has non-empty content.
    """
    flag_path = _resolve_path(path)
    try:
        if not flag_path.exists():
            return False
        # Optional: check content instead of mere existence
        content = flag_path.read_text().strip()
        return bool(content)
    except Exception:
        return False

def get_project_root_os():
    current_file_path = os.path.abspath(__file__)
    # Traverse up until a recognizable project root indicator is found
    # This example looks for a .git directory or a specific project file
    while True:
        parent_dir = os.path.dirname(current_file_path)
        if not parent_dir or parent_dir == current_file_path:
            # Reached the filesystem root or a loop
            return None
        if os.path.exists(os.path.join(parent_dir, '.git')) or \
           os.path.exists(os.path.join(parent_dir, 'pyproject.toml')) or \
           os.path.exists(os.path.join(parent_dir, 'setup.py')):
            return parent_dir
        current_file_path = parent_dir



def is_json(value):
    """
    Returns True if `value` is a JSON string (object or array), False otherwise.
    """
    if not isinstance(value, str):
        return False
    try:
        json.loads(value)
        return True
    except json.JSONDecodeError:
        return False


# Lock to ensure thread safety
_scratch_lock = threading.Lock()

# Directory to store scratch logs
SCRATCH_DIR = Path("scratch_logs")
SCRATCH_DIR.mkdir(exist_ok=True)

def write_scratch(message: str, filename: str = None):
    """
    Append a message to the daily scratch log in a thread-safe manner.
    
    :param message: Message to write.
    :param filename: Optional custom filename (defaults to date-based).
    """
    now = datetime.now()
    # Default filename: scratch_YYYY-MM-DD.log
    file_path = SCRATCH_DIR / (filename or f"scratch_{now.date()}.log")
    
    # Format the message with timestamp
    line = f"[{now.isoformat()}] {message}\n"
    
    # Thread-safe write
    with _scratch_lock:
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(line)



def get_job_count():
    cores = os.cpu_count() or 1
    # You can tune the scaling here:
    if cores <= 4:
        return cores  # Pi or small system → use all cores
    else:
        return min(8, cores)  # Mac or bigger system → cap at 8



# ------------------------- Generic parallel runner -------------------------
def run_parallel(fn, items, stop_event=None, collect_errors=True):
    results, errors = [], []
    lock = threading.Lock()
    logger = getLogger()
    
    max_workers = int(max(1,get_job_count()))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fn, item): item for item in items}
        for fut in as_completed(futures):
            if stop_event and stop_event.is_set():
                break
            try:
                res = fut.result()
                if res is not None:
                    with lock:
                        results.append(res)
            except Exception as e:
                logger.logMessage(f"[run_parallel] {e}")
                if collect_errors:
                    errors.append((futures[fut], e))
                else:
                    raise
    return results, errors

def is_interactive():
    """Detects whether this process is running interactively (e.g., not as a daemon)."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False



def wait_interruptible(stop_event, seconds):
    """Sleep in small chunks so stop_event can interrupt immediately."""
    end_time = pyTime.time() + seconds
    while pyTime.time() < end_time and not stop_event.is_set():
        pyTime.sleep(0.5)


def try_send(filepath: Path):
    logger = getLogger()
    try:
        #server_url = "http://<MACBOOK_IP>:8000/ingest"
        server_url="http://100.80.212.116:8000/api/upload_file"
        with open(filepath, "rb") as f:
            files = {"file": (filepath.name, f, "application/json")}
            resp = requests.post(server_url, files=files, timeout=900)
        if resp.status_code == 200:
            logger.logMessage(f"Sent {filepath.name} to server.")
            # Optionally delete after successful send
            filepath.unlink()
        else:
            logger.logMessage(f"[!] Server error {resp.status_code}: keeping file.")
    except Exception as e:
        logger.logMessage(f"[!] Network issue: could not send {filepath.name}. Error: {e}")


def send_existing_files():
    directory="data/option_data"
    path = Path(directory)

    if not path.exists():
        raise FileNotFoundError(f"Directory not found: {path}")

    # Filter for .json files that start with 'option_data_bundle_'
    bundle_files = [
        f
        for f in path.iterdir()
        if f.is_file()
        and f.name.startswith("option_data_bundle_")
        and f.suffix == ".json"
    ]
    for file in sorted(bundle_files):
        try_send(file)