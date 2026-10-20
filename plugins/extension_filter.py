from pathlib import Path
from typing import List
import logging
from filter_plugins import FilterPlugin

logger = logging.getLogger(__name__)

class ExtensionFilterPlugin(FilterPlugin):
    """Filter files by extension (e.g., .txt, .py, .jpg)"""

    def __init__(self):
        super().__init__()
        self.name = "ExtensionFilter"
        self.filter_type = "extension"
        self.version = "1.0.0"
        self.allowed_extensions = set()

    def initialize(self) -> bool:
        logger.info(f"Initializing {self.name}")
        self.allowed_extensions = set()
        return True

    def shutdown(self) -> bool:
        logger.info(f"Shutting down {self.name}")
        self.allowed_extensions.clear()
        return True

    def apply_filter(self, file_paths: List[str],
                     extensions: List[str] = None,
                     exclude: bool = False, **kwargs) -> List[str]:
        """
        Filter files by extension.

        Args:
            file_paths: List of file paths to filter
            extensions: List of extensions to include/exclude (e.g., ['.txt', '.py'])
            exclude: If True, exclude these extensions; if False, include only these
            **kwargs: Additional arguments (ignored)

        Returns:
            Filtered list of file paths

        Example:
            plugin.apply_filter(files, extensions=['.txt', '.log'], exclude=False)
            # Returns only .txt and .log files

            plugin.apply_filter(files, extensions=['.exe', '.dll'], exclude=True)
            # Returns all files EXCEPT .exe and .dll
        """
        if not extensions:
            logger.warning("No extensions specified for ExtensionFilterPlugin")
            return file_paths

        # Normalize extensions (ensure they start with '.')
        normalized_extensions = {
            ext if ext.startswith('.') else f'.{ext}'
            for ext in extensions
        }

        filtered = []
        for file_path in file_paths:
            file_ext = Path(file_path).suffix.lower()

            if exclude:
                # Exclude mode: keep files NOT in the list
                if file_ext not in normalized_extensions:
                    filtered.append(file_path)
            else:
                # Include mode: keep files IN the list
                if file_ext in normalized_extensions:
                    filtered.append(file_path)

        logger.info(
            f"ExtensionFilter: {len(file_paths)} -> {len(filtered)} files "
            f"({'excluded' if exclude else 'included'} {normalized_extensions}"
        )
        return filtered
