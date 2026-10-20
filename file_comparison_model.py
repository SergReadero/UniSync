# Switching from QTableWidget to QAbstractTableModel + TableView for more performance
from typing import Dict, List, Tuple, FrozenSet, Optional, Set
import pyqt5_loader
from PyQt5.QtCore import Qt, QAbstractTableModel, QModelIndex, QVariant, pyqtSignal, QRect, QSize
from PyQt5.QtWidgets import QHeaderView
from PyQt5.QtGui import QPainter, QFont, QPalette

class FileComparisonModel(QAbstractTableModel):
    """
    Model for file comparison table.

    Data structure:
        self.files = {
            (file_name: str, file_size: int, file_hash: str): frozenset(paths)
        }

    Filtering:
        self.filters = {
            col_index: set([filter_value1, filter_value2, ...]) or None
        }

    This uses immutable types for stable, hashable keys.
    frozenset ensures path comparison is order-independent.
    """

    rowCountChanged = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.files: Dict[Tuple[str, int, str], FrozenSet[str]] = {}
        self.filtered_keys: List[Tuple[str, int, str]] = []
        self.headers = [
            "File Name",
            "Size (bytes)",
            "Hash",
            "Sources (Count)",
            "Sources (Path)"
        ]
        # Support multiple values per column
        self.filters: Dict[int, Optional[Set[str]]] = {i: None for i in range(5)}
        self.sort_column = 0
        self.sort_order = Qt.AscendingOrder

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self.filtered_keys)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.headers)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.headers[section]
        return QVariant()

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        """Return data for display and sorting."""
        if not index.isValid() or index.row() >= len(self.filtered_keys):
            return QVariant()

        key = self.filtered_keys[index.row()]
        file_name, file_size, file_hash = key
        sources = self.files[key]
        col = index.column()

        if role == Qt.DisplayRole:
            if col == 0:
                return file_name
            elif col == 1:
                return str(file_size)
            elif col == 2:
                return file_hash if file_hash else ""
            elif col == 3:
                return str(len(sources))
            elif col == 4:
                # Format paths for display (ElidedDelegate will truncate)
                return ", ".join(sorted(sources))

        elif role == Qt.UserRole:  # For sorting (preserve types)
            if col == 1:
                return file_size  # Return int, not string
            elif col == 3:
                return len(sources)  # Return int
            else:
                return str(key[col]) if col < 3 else ""

        elif role == Qt.TextAlignmentRole:
            # Right-align numeric columns
            if col in (1, 3):
                return Qt.AlignRight | Qt.AlignVCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        return QVariant()

    def flags(self, index: QModelIndex):
        """Make cells non-editable and selectable."""
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def set_files(self, files: Dict) -> None:
        """
        Batch-populate model with final results.

        Args:
            files: {(file_name, file_size, file_hash): [path1, path2, ...]}
        """
        self.beginResetModel()

        # Convert lists to frozensets for stable, hashable storage
        self.files = {
            key: frozenset(paths) if isinstance(paths, (list, tuple)) else paths
            for key, paths in files.items()
        }

        self._rebuild_filtered_keys()
        self.endResetModel()
        self.rowCountChanged.emit(len(self.filtered_keys))

    def add_file(self, key: Tuple, sources: List[str]) -> None:
        """
        Add a single file (for lazy/incremental loading during discovery).

        Args:
            key: (file_name, file_size, file_hash)
            sources: List of source paths
        """
        if key not in self.files:
            start_row = len(self.filtered_keys)
            self.files[key] = frozenset(sources)

            if self._matches_filters(key):
                self.filtered_keys.append(key)
                self.beginInsertRows(QModelIndex(), start_row, start_row)
                self.endInsertRows()
                self.rowCountChanged.emit(len(self.filtered_keys))

    def apply_filter(self, column: int, filter_values: Optional[List[str]] = None) -> None:
        """
        Apply filter(s) to a column.

        Args:
            column: Column index (0-4)
            filter_value: List of values to filter by, or None to clear filter
                        Example: ['2', '3'] to show only records with 2 or 3 sources
        """
        if filter_values is None or len(filter_values) == 0:
            # Clear filter
            self.filters[column] = None
        else:
            # Store as set for fast lookup
            self.filters[column] = set(str(v) for v in filter_values)

        # Use layoutAboutToBeChanged for incremental updates (faster than reset)
        self.layoutAboutToBeChanged.emit()
        self._rebuild_filtered_keys()
        self.layoutChanged.emit()

        self.rowCountChanged.emit(len(self.filtered_keys))

    def add_filter_value(self, column: int, value: str) -> None:
        """
        Add a single value to the existing filter for a column.

        Args:
            column: Column index (0-4)
            value: Value to add to the filter
        """
        if self.filters[column] is None:
            self.filters[column] = {str(value)}
        else:
            self.filters[column].add(str(value))

        self.layoutAboutToBeChanged.emit()
        self._rebuild_filtered_keys()
        self.layoutChanged.emit()

        self.rowCountChanged.emit(len(self.filtered_keys))

    def remove_filter_value(self, column: int, value: str) -> None:
        """
        Remove a single value from the filter for a column

        Args:
            column: Column index (0-4)
            value: Value to remove from the filter
        """
        if self.filters[column] and str(value) in self.filters[column]:
            self.filters[column].discard(str(value))

            if len(self.filters[column]) == 0:
                self.filters[column] = None

            self.layoutAboutToBeChanged.emit()
            self._rebuild_filtered_keys()
            self.layoutChanged.emit()

            self.rowCountChanged.emit(len(self.filtered_keys))

    def get_filter_values(self, column: int) -> Optional[Set[str]]:
        """
        Get the current filter values for a column.

        Args:
            column: Column index (0-4)

        Returns:
            Set of filter values, or None of no filter is active
        """
        return self.filters[column]

    def sort(self, column: int, order: Qt.SortOrder) -> None:
        """
        Sort the model by a column

        Args:
            column: Column index
            order: Qt.AscendingOrder or Qt.DescendingOrder
        """
        self.layoutAboutToBeChanged.emit()

        self.sort_column = column
        self.sort_order = order

        reverse = order == Qt.DescendingOrder

        # Sort by the appropriate column, preserving data types
        if column == 1:  # file_size (numeric)
            self.filtered_keys.sort(
                key=lambda k: k[1],
                reverse=reverse
            )
        elif column == 3:  # Source count (numeric)
            self.filtered_keys.sort(
                key=lambda k: len(self.files[k]),
                reverse=reverse
            )
        else:  # file_name, file_hash, Paths (lexicographic)
            sort_key = column
            self.filtered_keys.sort(
                key=lambda k: (str(k[sort_key]) if sort_key < 3 else
                               ", ".join(sorted(self.files[k]))),
                reverse=reverse
            )

        self.layoutChanged.emit()

    def calculate_total_size(self) -> str:
        """
        Calculate the total size of all filtered records.

        Returns:
            Formatted string with automatic unit selection (B, MB, GB, TB)
            Example: "2.45 GB" or "512 MB"
        """
        total_bytes = sum(key[1] for key in self.filtered_keys)
        return self._format_size(total_bytes)

    def _format_size(self, bytes_size: int) -> str:
        """
        Format bytes to human-readable size with automatic unit selection.

        Args:
            bytes_size: Size in bytes

        Returns:
            Formatted string (e.g., "2.45 GB", "512 MB", "1024 B")
        """
        # Define size thresholds
        units = [
            (1_099_511_627_776, "TB"),  # 1 TB
            (1_073_741_824, "GB"),  # 1 GB
            (1_048_576, "MB"),  # 1 MB
            (1_024, "KB"),  # 1 KB
            (1, "B")  # 1 Byte
        ]

        for threshold, unit in units:
            if bytes_size >= threshold:
                size = bytes_size / threshold
                # Show 2 decimal places for GB/MB/KB, none for bytes
                if unit == "B":
                    return f"{int(size)} {unit}"
                else:
                    # Remove trailing zeros and unnecessary decimal point
                    formatted = f"{size:.2f}".rstrip('0').rstrip('.')
                    return f"{formatted} {unit}"

        return "0 B"

    def _rebuild_filtered_keys(self) -> None:
        """Rebuild the filtered key list based on current filters."""
        self.filtered_keys = [
            k for k in self.files.keys()
            if self._matches_filters(k)
        ]

        # Re-apply current sort after filtering
        if self.files:
            self.sort(self.sort_column, self.sort_order)

    def _matches_filters(self, key: Tuple) -> bool:
        """Check if a key matches all active filters (multi-value support)."""
        file_name, file_size, file_hash = key
        sources = self.files[key]

        for col, filter_vals in self.filters.items():
            if filter_vals is None:
                continue

            # filter_vals is now a set of values
            matched = False

            if col == 0:  # file_name
                matched = file_name in filter_vals

            elif col == 1:  # file_size
                try:
                    matched =  str(file_size) in filter_vals
                except (ValueError, TypeError):
                    matched = False

            elif col == 2:  # file_hash
                matched = file_hash in filter_vals

            elif col == 3:  # Source count
                try:
                    matched = str(len(sources)) in filter_vals
                except (ValueError, TypeError):
                    matched = False

            elif col == 4:  # Source paths
                # Check if any of the filter values is in the sources
                matched = any(path in sources for path in filter_vals)

            if not matched:
                return False

        return True

    def get_selected_files(self, selected_rows: List[int], value_type: int = 0) -> Dict:
        """
        Extract files from selected rows.

        Args:
            selected_rows: List of row indices from view
            value_type: 0 - List, 1 - frozenset

        Returns:
            {(file_name, file_size, file_hash): sorted(list(paths)), ...} or
            {(file_name, file_size, file_hash): frozenset(paths), ...}
        """
        selected = {}
        for row in selected_rows:
            if 0 <= row < len(self.filtered_keys):
                key = self.filtered_keys[row]
                if value_type == 0:
                    selected[key] = sorted(list(self.files[key]))
                else:
                    selected[key] = self.files[key]
        return selected

class RowNumberHeader(QHeaderView):
    """Custom vertical header that displays row numbers."""

    def __init__(self, parent=None):
        super().__init__(Qt.Vertical, parent)
        self.setDefaultSectionSize(24)
        self.setSectionResizeMode(QHeaderView.Fixed)

    def paintSection(self, painter: QPainter, rect: QRect, logicalIndex: int) -> None:
        """Paint row number in the vertical header."""

        # Paint row number matching the horizontal header style.
        horizontal_header = self.parent().horizontalHeader()
        palette = horizontal_header.palette()

        bg_color = palette.color(QPalette.Button)
        painter.fillRect(rect, bg_color)

        # Draw row number (1-indexed)
        painter.setFont(QFont("Arial", 9))
        painter.drawText(rect, Qt.AlignCenter, str(logicalIndex + 1))

    def sizeHint(self) -> QSize:
        """Return the optimal size for the header."""
        # Calculate width based on max row count
        row_count = self.count()
        width = len(str(row_count)) * 8 + 10
        return QSize(max(width, 35), self.defaultSectionSize())
