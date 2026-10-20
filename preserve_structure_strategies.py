from abc import ABC, abstractmethod
import os

class PreserveStructureStrategy(ABC):
    """Abstract base for preserve structure strategies."""

    @abstractmethod
    def compute_target_dir(self, src_file: str, dest_dir: str) -> str:
        """
        Compute the target directory based on the strategy.

        Args:
            :param src_file: Full path to source file
            :param dest_dir: Destination root directory

        Returns:
             Target directory path where file should be copied
        """
        pass

class PSSourceParentRest(PreserveStructureStrategy):
    """Preserve DEST + SOURCE_PARENT_REST structure."""

    def compute_target_dir(self, src_file: str, dest_dir: str) -> str:
        source_rest_parent = os.path.basename(os.path.dirname(os.path.dirname(src_file)))
        source_rest = os.path.basename(os.path.dirname(src_file))
        return os.path.join(dest_dir, source_rest_parent, source_rest)

class PSFullSource(PreserveStructureStrategy):
    """Preserve DEST + full SOURCE directory structure."""

    def compute_target_dir(self, src_file: str, dest_dir: str) -> str:
        source_dir = os.path.dirname(src_file)
        source_path_parts = source_dir.split(os.sep)
        source_path_parts = [p for p in source_path_parts if p and ':' not in p]
        return  os.path.join(dest_dir, *source_path_parts)

class PSNone(PreserveStructureStrategy):
    """Flatten to destination root (no structure preservation)."""

    def compute_target_dir(self, src_file: str, dest_dir: str) -> str:
        return dest_dir
