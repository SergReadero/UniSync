from typing import Dict, Tuple, Any

# Shared type aliases for copy/move operations
FileMetadataKey = Tuple[str, str]  # (source_path, destination_path)
FileMetadataValue = Dict[str, Any]  # file metadata: file_name, file_size, file_hash, etc.
