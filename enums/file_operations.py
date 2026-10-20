"""Enums for file operations type."""

from enum import Enum


class FileOperation(Enum):
    """Enumeration of supported file operations."""
    COPY = "copy"
    MOVE = "move"
    DISCOVERY = "discovery"
    COPY_UNDO = "copy_undo"
    COPY_REDO = "copy_redo"
    MOVE_UNDO = "move_undo"
    MOVE_REDO = "move_redo"

    def __str__(self) -> str:
        """Return the string value for logging."""
        return self.value
