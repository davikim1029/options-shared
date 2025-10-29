"""
shared_options
==============

A shared library of data models and utilities for options analytics.
Includes:
- Typed data classes for option features
- Helper functions for feature extraction and processing
"""

from .models import OptionFeature
from .constants import FEATURE_COLS
from .utils import extract_features_from_snapshot, features_to_array

__all__ = [
    "OptionFeature",
    "FEATURE_COLS",
    "extract_features_from_snapshot",
    "features_to_array"
]
