import logging
import os
from pathlib import Path
import pyqt5_loader
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QTreeWidget, QApplication,
    QTreeWidgetItem, QLabel, QAbstractItemView, QComboBox
)
from PyQt5.QtCore import Qt, QTimer
from styled_dialog import StyledDialog

logger = logging.getLogger(__name__)

class FilePickerDialog(StyledDialog):
    """
    Custom file picker dialog.
    Browse filesystem tree, filter by file types, or paste/type path manually.
    Works on main thread, no threading issues.
    No system icon caching problems (uses custom StyledDialog).
    """

    def __init__(self, title="Select File", initial_path=None, file_filters=None, parent=None):
        """
        Initialize file picker.

        Args:
            title: Dialog title
            initial_path: Starting directory path
            file_filters: List of tuples: [("Text Files", "*.txt"), ("All Files", "*")]
            parent: Parent widget
        """
        self.initial_path = initial_path or str(Path.home())
        self.selected_file = None
        self.tree = None

        # Default to text files and all files if not specified
        if file_filters is None:
            file_filters = [
                ("Text Files", "*.txt"),
                ("All Files", "*")
            ]
        self.file_filters = file_filters
        self.current_filter = file_filters[0] if file_filters else ("All Files", "*")

        # Initialize parent dialog
        super().__init__(title=title, message="", dialog_type="info", buttons=[], parent=parent)

        self.setMinimumSize(700, 600)
        self._setup_picker_ui()

        # Center dialog on screen after setup
        self._center_on_screen()

    def _center_on_screen(self):
        """Center the dialog on the screen."""
        screen_geometry = QApplication.desktop().screenGeometry()
        dialog_geomety = self.frameGeometry()
        center_x = (screen_geometry.width() - dialog_geomety.width()) // 2
        center_y = (screen_geometry.height() - dialog_geomety.height()) // 2
        self.move(center_x, center_y)
        logger.debug(f"Dialog centered at: ({center_x}, {center_y})")

    def _matches_filter(self, filename):
        """Check if filename matches current filter pattern."""
        filter_name, pattern = self.current_filter

        if pattern == "*":
            return True

        # Handle patterns like "*.txt"
        if pattern.startswith("*."):
            ext = pattern[1:]  # Get ".txt"
            return filename.lower().endswith(ext.lower())

        return filename == pattern

    def _expand_tree_to_path(self, target_path):
        """
        Auto-expand tree items to show the target path.
        Expands each parent directory up to the target path.
        """
        try:
            target_path = str(Path(target_path).resolve())

            if not os.path.isdir(target_path):
                logger.warning(f"Target path is not a valid directory: {target_path}")
                return

            # Split path into components
            path_parts = Path(target_path).parts

            # Start from the drive (e.g., 'C:\\')
            if os.name == 'nt':
                current_item = None

                drive_letter = path_parts[0]
                for i in range(self.tree.topLevelItemCount()):
                    item = self.tree.topLevelItem(i)
                    if item.text(0).startswith(drive_letter):
                        current_item = item
                        break

                if not current_item:
                    logger.warning(f"Drive not found in tree: {drive_letter}")
                    return

                # Expand drive and load its children
                self.tree.expandItem(current_item)
                self._load_children(current_item)

                # Traverse remaining path components
                for part in path_parts[1:]:
                    found = False
                    for i in range(current_item.childCount()):
                        child = current_item.child(i)
                        if child.text(0) == part:
                            current_item = child
                            self.tree.expandItem(current_item)
                            self._load_children(current_item)
                            found = True
                            break

                    if not found:
                        logger.debug(f"Path component not found in tree: {part}")
                        break

        except Exception as e:
            logger.error(f"Error expanding tree to path: {e}")

    def _setup_picker_ui(self):
        """Replace default content with custom file picker."""
        # Clear the default content layout
        while self.content_layout.count():
            widget = self.content_layout.takeAt(0).widget()
            if widget:
                widget.deleteLater()

        # Create picker layout
        picker_layout = QVBoxLayout()
        picker_layout.setSpacing(5)
        picker_layout.setContentsMargins(5, 5, 5, 5)

        # Add title/instruction at top
        title_label = QLabel("Select a file:")
        title_label.setStyleSheet("font-weight: bold; font-size: 12px; margin-bottom: 5px; margin-left: 35px;")
        picker_layout.addWidget(title_label)

        # File filter dropdown
        if len(self.file_filters) > 1:
            filter_label = QLabel("File type:")
            self.filter_combo = QComboBox()
            self.filter_combo.setMaximumHeight(32)

            for filter_name, pattern in self.file_filters:
                self.filter_combo.addItem(filter_name, pattern)

            self.filter_combo.currentIndexChanged.connect(self._on_filter_changed)

            filter_layout = QHBoxLayout()
            filter_layout.addWidget(filter_label, 0)
            filter_layout.addWidget(self.filter_combo, 1)
            filter_layout.setContentsMargins(0, 0, 0, 0)

            picker_layout.addLayout(filter_layout)

        # Current path display and manual input with copy button
        path_label = QLabel("Current path:")
        path_label.setStyleSheet("margin-top: 10px; margin-bottom: 0px;")

        # Create horizontal layout for path input and copy button
        path_input_layout = QHBoxLayout()
        path_input_layout.setContentsMargins(0, 0, 0, 0)
        path_input_layout.setSpacing(5)

        self.path_input = QLineEdit()
        self.path_input.setText(self.initial_path)
        self.path_input.setPlaceholderText("Or type/paste path here...")
        self.path_input.setMinimumHeight(25)
        self.path_input.returnPressed.connect(self._on_path_entered)
        # Connect text change to auto-expand tree
        self.path_input.textChanged.connect(self._on_path_input_changed)
        # self.path_input.setStyleSheet("margin-top: 0px; margin-bottom: 20px;")

        copy_btn = QPushButton("Copy")
        copy_btn.setMaximumWidth(80)
        copy_btn.setMinimumHeight(25)
        copy_btn.setCursor(Qt.PointingHandCursor)
        copy_btn.clicked.connect(self._on_copy_path_clicked)
        # copy_btn.setStyleSheet("margin-top: 0px; margin-bottom: 20px;")

        path_input_layout.addWidget(self.path_input, 1)
        path_input_layout.addWidget(copy_btn)

        picker_layout.addWidget(path_label)
        picker_layout.addLayout(path_input_layout)

        # Spacing
        picker_layout.addSpacing(10)

        # Tree browser
        tree_label = QLabel("Browse directories and files:")
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name"])
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        # Connect to expansion signal instead of double-click
        self.tree.itemExpanded.connect(self._on_tree_item_expanded)
        self.tree.itemClicked.connect(self._on_tree_item_selected)
        self.tree.itemDoubleClicked.connect(self._on_tree_item_double_clicked)
        self.tree.setMinimumHeight(300)

        picker_layout.addWidget(tree_label)
        picker_layout.addWidget(self.tree, 1)

        # Replace the content_layout's main content
        self.content_layout.addLayout(picker_layout, 1)

        # Button layout (custom buttons at bottom)
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        select_btn = QPushButton("Select")
        select_btn.setMinimumWidth(100)
        select_btn.setMinimumHeight(36)
        select_btn.setCursor(Qt.PointingHandCursor)
        select_btn.clicked.connect(self._on_select_clicked)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setMinimumWidth(100)
        cancel_btn.setMinimumHeight(36)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self._on_cancel_clicked)

        button_layout.addWidget(select_btn)
        button_layout.addWidget(cancel_btn)

        # Find the main layout and add button layout to it
        main_layout = self.layout()
        if main_layout:
            main_layout.addLayout(button_layout)

        # Populate initial tree with drives and their first-level children
        self._populate_tree()

    def _populate_tree(self):
        """Populate tree widget with directories and files."""
        self.tree.clear()

        try:
            # On Windows, list all drives (C:, D:, etc.)
            if os.name == 'nt':
                self._add_drives_to_tree()

        except Exception as e:
            logger.error(f"Error populating tree: {e}")

    def _add_drives_to_tree(self):
        """Add all Windows drives to tree root."""
        try:
            import string
            for drive in string.ascii_uppercase:
                drive_path = f"{drive}:\\"
                if os.path.exists(drive_path):
                    logger.debug(f"Adding drive: {drive_path}")
                    # Add drive to tree
                    drive_item = QTreeWidgetItem()
                    drive_item.setText(0, f"{drive}:\\")
                    drive_item.setData(0, Qt.UserRole, drive_path)
                    drive_item.setData(0, Qt.UserRole + 1, False)  # Not yet fully expanded
                    drive_item.setData(0, Qt.UserRole + 2, True)  # Is directory
                    self.tree.addTopLevelItem(drive_item)

                    # Add dummy child so drive shows expand arrow
                    dummy = QTreeWidgetItem()
                    dummy.setText(0, "")
                    drive_item.addChild(dummy)

        except Exception as e:
            logger.error(f"Error adding drives: {e}")

    def _load_children(self, parent_item):
        """Lazy-load children (directories and files) for a tree item on expansion."""
        parent_path = parent_item.data(0, Qt.UserRole)
        is_expanded = parent_item.data(0, Qt.UserRole + 1)

        # Only load if not already loaded
        if is_expanded or not parent_path:
            return

        logger.debug(f"Loading children for: {parent_path}")

        try:
            # Remove all dummy child/existing children
            while parent_item.childCount() > 0:
                parent_item.removeChild(parent_item.child(0))

            # Get subdirectories and files
            try:
                entries = sorted(os.listdir(parent_path))
            except (PermissionError, OSError) as e:
                logger.debug(f"Cannot read {parent_path}: {e}")
                return

            # Add subdirectories first
            subdirs_added = 0
            for entry in entries:
                full_path = os.path.join(parent_path, entry)
                try:
                    # Check if it is a directory
                    if os.path.isdir(full_path) and not os.path.islink(full_path):
                        child_item = QTreeWidgetItem()
                        child_item.setText(0, entry)
                        child_item.setData(0, Qt.UserRole, full_path)
                        child_item.setData(0, Qt.UserRole + 1, False)
                        child_item.setData(0, Qt.UserRole + 2, True)  # Is directory
                        parent_item.addChild(child_item)

                        # Add dummy to show expand arrow
                        dummy = QTreeWidgetItem()
                        dummy.setText(0, "")
                        child_item.addChild(dummy)

                        subdirs_added += 1

                except (OSError, PermissionError):
                    pass  # Skip inaccessible directories

            # Add files that match filter
            files_added = 0
            for entry in entries:
                full_path = os.path.join(parent_path, entry)
                try:
                    if os.path.isfile(full_path) and self._matches_filter(entry):
                        file_item = QTreeWidgetItem()
                        file_item.setText(0, entry)
                        file_item.setData(0, Qt.UserRole, full_path)
                        file_item.setData(0, Qt.UserRole + 1, True)  # Files do not expand
                        file_item.setData(0, Qt.UserRole + 2, False)  # Is not directory
                        parent_item.addChild(file_item)
                        files_added += 1

                except (OSError, PermissionError):
                    pass  # Skip inaccessible files

            # Mark as expanded
            parent_item.setData(0, Qt.UserRole + 1, True)
            logger.debug(f"Loaded {subdirs_added} subdirs and {files_added} files for: {parent_path}")

        except Exception as e:
            logger.error(f"Error loading children for {parent_path}: {e}")

    def _on_filter_changed(self, index):
        """Handle file filter change."""
        filter_name, pattern = self.file_filters[index]
        self.current_filter = (filter_name, pattern)
        logger.info(f"Filter changed to: {filter_name}")

        # Reload the current directory to apply new filter
        current_path = self.path_input.text().strip()
        if current_path and os.path.isdir(current_path):
            QTimer.singleShot(100, lambda: self._expand_tree_to_path(current_path))

    def _on_path_input_changed(self):
        """Handle path input changes and auto-expand tree."""
        path = self.path_input.text().strip()

        # Only auto-expand if it is a valid directory
        if path and os.path.isdir(path):
            logger.info(f"Valid path detected, auto-expanding tree: {path}")
            # Use a small delay to avoid interfering with typing
            QTimer.singleShot(300, lambda: self._expand_tree_to_path(path))

    def _on_copy_path_clicked(self):
        """Copy the current path to clipboard."""
        path = self.path_input.text().strip()
        if path:
            clipboard = QApplication.clipboard()
            clipboard.setText(path)
            logger.info(f"Path copied to clipboard: {path}")
        else:
            logger.warning("No path to copy")

    def _on_tree_item_selected(self, item, column):
        """Update path input when tree item is selected."""
        path = item.data(0, Qt.UserRole)
        if path:
            self.path_input.setText(path)
            # Only set selected_file if it is a file, not a directory
            is_dir = item.data(0, Qt.UserRole + 2)
            if not is_dir:
                self.selected_file = path
            logger.debug(f"Tree item selected: {path}")

    def _on_tree_item_double_clicked(self, item, column):
        """Navigate into directory or select file on double-click."""
        path = item.data(0, Qt.UserRole)
        is_dir = item.data(0, Qt.UserRole + 2)

        if is_dir:
            # Navigate into directory
            self.path_input.setText(path)
        else:
            # Select file and accept
            self.path_input.setText(path)
            self.selected_file = path
            logger.info(f"File double-clicked, accepting: {path}")
            self.done(self.OK)

    def _on_tree_item_expanded(self, item):
        """Load children when expand arrow is clicked."""
        logger.debug(f"Tree item expanded: {item.text(0)}")
        self._load_children(item)

    def _on_path_entered(self):
        """Handle manual path entry."""
        path = self.path_input.text().strip()

        # Check if it is a file
        if path and os.path.isfile(path):
            if self._matches_filter(os.path.basename(path)):
                self.selected_file = path
                logger.info(f"File path entered manually: {path}")
            else:
                logger.warning(f"File does not match filter: {path}")
        # Check if it is a directory
        elif path and os.path.isdir(path):
            logger.info(f"Directory path entered manually: {path}")
        else:
            logger.warning(f"Invalid path: {path}")

    def _on_select_clicked(self):
        """Confirm file selection."""
        path = self.path_input.text().strip()

        # If path is a file and matches filter, accept it
        if path and os.path.isfile(path) and self._matches_filter(os.path.basename(path)):
            self.selected_file = path
            logger.info(f"File confirmed: {path}")
            self.done(self.OK)
        else:
            logger.warning(f"Invalid or unfiltered file: {path}")

    def _on_cancel_clicked(self):
        """Cancel selection."""
        self.selected_file = None
        logger.info("File selection cancelled")
        self.done(self.CANCEL)

    def get_file(self):
        """Return the selected file path or None if cancelled."""
        return self.selected_file
