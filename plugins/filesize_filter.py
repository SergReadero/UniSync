from typing import List, Optional
import os
import logging
from filter_plugins import FilterPlugin

logger = logging.getLogger(__name__)

class FilesizeFilterPlugin(FilterPlugin):
    """Filter files by size range (min/max bytes)"""

    def __init__(self):
        super().__init__()
        self.name = "FilesizeFilter"
        self.filter_type = "filesize"
        self.version = "1.0.0"

    def initialize(self) -> bool:
        logger.info(f"Initializing {self.name}")
        return True

    def shutdown(self) -> bool:
        logger.info(f"Shutting down {self.name}")
        return True

    @staticmethod
    def _parse_size(size_str: str) -> Optional[int]:
        """
        Parse human-readable size to bytes.

        Examples:
            '1MB' -> 1048576
            '512KB' -> 524288
            '1GB' -> 1073741824
            '1024' -> 1024
        """
        if isinstance(size_str, int):
            return size_str

        size_str = str(size_str).strip().upper()

        units = {
            'B': 1,
            'KB': 1024,
            'MB': 1024 ** 2,
            'GB': 1024 ** 3,
            'TB': 1024 ** 4
        }

        for unit, multiplier in units.items():
            if size_str.endswith(unit):
                try:
                    number = float(size_str[:-len(unit)].strip())
                    return int(number * multiplier)
                except ValueError:
                    logger.error(f"Invalid size format: {size_str}")
                    return None

        # Try as raw bytes
        try:
            return int(size_str)
        except ValueError:
            logger.error(f"Invalid size format: {size_str}")
            return None

    def apply_filter(self, file_paths: List[str],
                     min_size: str = None,
                     max_size: str = None,
                     exact_size: str = None, **kwargs) -> List[str]:
        """
        Filter files by size range.

        Args:
            file_paths: List of file paths to filter
            min_size: Minimum file size (e.g., '1KB', '1048576')
            max_size: Maximum file size (e.g., '100MB', '1073741824')
            exact_size: Exact file size (e.g., '1MB')
            **kwargs: Additional arguments (ignored)

        Returns:
            Filtered list of file paths

        Example:
            # Files between 1KB and 100MB
            plugin.apply_filter(files, min_size='1KB', max_size='100MB')

            # Files exactly 5MB
            plugin.apply_filter(files, exact_size='5MB')

            # Files larger than 1GB
            plugin.apply_filter(files, min_size='1GB')
        """
        if exact_size:
            exact_bytes = self._parse_size(exact_size)
            if exact_bytes is None:
                return file_paths
            min_size = exact_bytes
            max_size = exact_bytes
        else:
            min_bytes = self._parse_size(min_size) if min_size else 0
            max_bytes = self._parse_size(max_size) if max_size else float('inf')

            if min_bytes is None or max_bytes is None:
                return file_paths

            min_size = min_bytes
            max_size = max_bytes

        filtered = []
        for file_path in file_paths:
            try:
                file_size = os.path.getsize(file_path)
                if min_size <= file_size <= max_size:
                    filtered.append(file_path)
            except OSError as e:
                logger.warning(f"Could not get size of {file_path}: {str(e)}")

        logger.info(
            f"FilesizeFilter: {len(file_paths)} -> {len(filtered)} files "
            f"(size: {min_size} - {max_size} bytes"
        )
        return filtered
