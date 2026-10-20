from pathlib import Path
from typing import List, Tuple
import os

class PathFileLoader:
    """Loads and validates paths from text files."""

    @staticmethod
    def load_paths_from_file(file_path: str) -> Tuple[List[str], List[str]]:
        """
        Load paths from a text file. Each line = one path

        Args:
            file_path: Path to the .txt file

        Returns:
            Tuple of (valid_paths, error_messages)
        """
        valid_paths = []
        errors = []

        try:
            file = Path(file_path)
            if not file.exists():
                errors.append(f"File not found: {file_path}")
                return valid_paths, errors

            if not file.is_file():
                errors.append(f"Not a file: {file_path}")
                return valid_paths, errors

            with file.open("r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    path_str = line.strip()

                    # Skip empty lines and comments
                    if not path_str or path_str.startswith("#"):
                        continue

                    # Validate path
                    try:
                        path_obj = Path(path_str)

                        # Warn if path doesn't exist (but don't fail)
                        if not path_obj.exists():
                            errors.append(
                                f"Line {line_num}: Path does not exist: {path_str}"
                            )

                        # Check if it's a directory
                        if path_obj.exists() and not path_obj.is_dir():
                            errors.append(
                                f"Line {line_num}: Not a directory: {path_str}"
                            )
                            continue

                        # Normalize and add
                        normalized = os.path.normpath(str(path_obj))
                        if normalized not in valid_paths:
                            valid_paths.append(normalized)

                    except (OSError, ValueError) as e:
                        errors.append(f"Line {line_num}: Invalid path: {path_str} (str{e})")

        except IOError as e:
            errors.append(f"Failed to read file: {str(e)}")

        return valid_paths, errors

    @staticmethod
    def save_paths_to_file(file_path: str, paths: List[str]) -> Tuple[bool, str]:
        """
        Save paths to a text file (one per line).

        Args:
            file_path: Destination file path
            paths: List of paths to save

        Returns:
             Tuple of (success, message)
        """
        try:
            file = Path(file_path)
            file.parent.mkdir(parents=True, exist_ok=True)

            with file.open("w", encoding="utf-8") as f:
                for path in paths:
                    f.write(f"{path}\n")

            return True, f"Saved {len(paths)} paths to {file_path}"

        except IOError as e:
            return False, f"Failed to save file: {str(e)}"
