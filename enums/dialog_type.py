from enum import Enum

class DialogType(Enum):
    """Enum for dialog types with associated styling."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    SUCCESS = "success"
