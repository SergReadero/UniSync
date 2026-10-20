from pathlib import Path
from typing import List
import re
import logging
from filter_plugins import FilterPlugin

logger = logging.getLogger(__name__)

class FilenameFilterPlugin(FilterPlugin):
    """Filter files by filename pattern (regex or simple wildcards)"""

    def __init__(self):
        super().__init__()
        self.name = "FilenameFilter"
        self.filter_type = "filename"
        self.version = "1.0.0"

    def initialize(self) -> bool:
        logger.info(f"Initializing {self.name}")
        return True

    def shutdown(self) -> bool:
        logger.info(f"Shutting down {self.name}")
        return True

    def apply_filter(self, file_paths: List[str],
                     pattern: str = None,
                     case_sensitive:bool = False,
                     regex: bool = False, **kwargs) -> List[str]:
        """
        Filter files by filename pattern.

        Args:
            file_paths: List of file paths to filter
            pattern:  Pattern to match (e.g., 'backup_*', '.*\d{4}\.log')
            case_sensitive: If True, match is case-sensitive
            regex: If True, pattern is treated as regex; if False, use simple wildcard
            **kwargs: Additional arguments (ignored)

        Returns:
            Filtered list of file paths

        Example:
            # Simple wildcard (default)
            plugin.apply_filter(files, pattern='backup_*')

            # Regex pattern
            plugin.apply_filter(files, pattern=r'.*\d{4}\.log', regex=True)
        """
        if not pattern:
            logger.warning("No pattern specified for FilenameFilterPlugin")
            return file_paths

        filtered = []

        try:
            if regex:
                # Compile regex pattern
                flags = 0 if case_sensitive else re.IGNORECASE
                compiled_pattern = re.compile(pattern, flags)

                for file_path in file_paths:
                    filename = Path(file_path).name
                    if compiled_pattern.match(filename):
                        filtered.append(file_path)
            else:
                # Simple wildcard matching
                import fnmatch

                for file_path in file_paths:
                    filename = Path(file_path).name

                    if case_sensitive:
                        if fnmatch.fnmatch(filename, pattern):
                            filtered.append(file_path)
                    else:
                        if fnmatch.fnmatch(filename.lower(), pattern.lower()):
                            filtered.append(file_path)

            logger.info(
                f"FilenameFilter: {len(file_paths)} -> {len(filtered)} files "
                f"(pattern: {pattern}, regex={regex})"
            )
            return filtered

        except re.error as e:
            logger.error(f"Invalid regex pattern '{pattern}': {e}")
            return file_paths
