"""Enums for processing mode operations type."""

from enum import Enum

class ProcessingMode(Enum):
    """Processing mode selection"""
    SEQUENTIAL = "sequential" # Uses FileManagerBase (threading)
    PARALLEL = "parallel" # Uses FileManagerThree (multiprocessing)
