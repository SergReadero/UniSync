from abc import abstractmethod
from typing import List, Any
from plugin_manager import PluginBase
import logging

logger = logging.getLogger(__name__)

class FilterPlugin(PluginBase):
    """
    Base class for file characteristic filters.
    All filter plugins inherit from this to maintain consistency.

    A filter plugin receives files and return a filtered subset.
    """

    def __init__(self):
        super().__init__()
        self.filter_type = "generic"  # Override in subclass
        self.enabled = True

    @abstractmethod
    def apply_filter(self, file_paths: List[str], **kwargs) -> List[str]:
        """
        Apply filter to file paths.

        Args:
            file_paths: List of file paths to filter
            **kwargs: Filter-specific parameters

        Returns:
            Filtered list of file paths
        """
        pass

    def execute(self, *args, **kwargs) -> Any:
        """
        Execute the filter. Delegates to apply_filter().
        Maintains compatibility with PluginBase interface.
        """
        if args:
            return self.apply_filter(args[0], **kwargs)
        return []
