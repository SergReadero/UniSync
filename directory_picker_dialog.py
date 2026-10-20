import logging
import os
from pathlib import Path
import pyqt5_loader
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QTreeWidget,
    QApplication, QTreeWidgetItem, QLabel, QAbstractItemView
)
from PyQt5.QtCore import Qt, QTimer
from styled_dialog import StyledDialog

logger = logging.getLogger(__name__)

class DirectoryPickerDialog(StyledDialog):
    """
    Custom directory picker dialog.
    Browse filesystem tree or paste/type path manually.
    Works on main thread, no threading issues.
    """
    def __init__(self, title="Select Directory", initial_path=None, parent=None):
        """
        Initialize directory picker.

        Args:
            title: Dialog title
            initial_path: Starting directory path
            parent: Parent widget
        """
        self.initial_path = initial_path or str(Path.home())
        self.selected_directory = None
        self.tree = None

        # Initialize parent dialog
        super().__init__(
            title=title,
            message="",
            dialog_type="info",
            buttons=[],
            parent=parent
        )

        self.setMinimumSize(700, 600)
        self._setup_picker_ui()

        # Center dialog on screen after setup
        self._center_on_screen()

    def _center_on_screen(self):
        """Center the dialog on the screen."""
        screen_geometry = QApplication.desktop().screenGeometry()
        dialog_geometry = self.frameGeometry()
        center_x = (screen_geometry.width() - dialog_geometry.width()) // 2
        center_y = (screen_geometry.height() - dialog_geometry.height()) // 2
        self.move(center_x, center_y)
        logger.debug(f"Dialog centered at: ({center_x}, {center_y})")

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
        """Replace default content with custom directory picker."""
        # Clear the default content layout
        while self.content_layout.count():
            widget = self.content_layout.takeAt(0).widget()
            if widget:
                widget.deleteLater()

        # Create picker layout
        picker_layout = QVBoxLayout()
        picker_layout.setSpacing(10)
        picker_layout.setContentsMargins(10, 10, 10, 10)

        # Add title/instruction at top
        title_label = QLabel("Select a directory:")
        title_label.setStyleSheet("font-weight: bold; font-size: 12px; margin-bottom: 5px; margin-left: 30px;")
        picker_layout.addWidget(title_label)

        # Current path display and manual input with copy button
        path_label = QLabel("Current path:")

        # Create horizontal layout for path input and copy button
        path_input_layout = QHBoxLayout()
        path_input_layout.setContentsMargins(0, 0, 0, 0)
        path_input_layout.setSpacing(5)

        self.path_input = QLineEdit()
        self.path_input.setText(self.initial_path)
        self.path_input.setPlaceholderText("Or type/paste path here...")
        self.path_input.returnPressed.connect(self._on_path_entered)
        # Connect text change to auto-expand tree
        self.path_input.textChanged.connect(self._on_path_input_changed)

        copy_btn = QPushButton("Copy")
        copy_btn.setMaximumWidth(80)
        copy_btn.setMinimumHeight(32)
        copy_btn.setCursor(Qt.PointingHandCursor)
        copy_btn.clicked.connect(self._on_copy_path_clicked)

        path_input_layout.addWidget(self.path_input, 1)
        path_input_layout.addWidget(copy_btn)

        picker_layout.addWidget(path_label)
        picker_layout.addLayout(path_input_layout)

        # Spacing
        picker_layout.addSpacing(10)

        # Tree browser
        tree_label = QLabel("Browse directories:")
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name"])
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        # Connect to expansion signal instead of double-click
        self.tree.itemExpanded.connect(self._on_tree_item_expanded)
        self.tree.itemClicked.connect(self._on_tree_item_selected)
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
        """Populate tree widget with directories."""
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
                    self.tree.addTopLevelItem(drive_item)

                    # Add dummy child so drive shows expand arrow
                    dummy = QTreeWidgetItem()
                    dummy.setText(0, "")
                    drive_item.addChild(dummy)

        except Exception as e:
            logger.error(f"Error adding drives: {e}")

    def _add_path_to_tree(self, parent_item, path):
        """Add a single path as a tree item with children."""
        try:
            path = str(Path(path).resolve())

            if not os.path.isdir(path):
                return

            item = QTreeWidgetItem()
            display_name = os.path.basename(path) or path
            item.setText(0, display_name)  # Show drive letter or folder name
            item.setData(0, Qt.UserRole, path)
            item.setData(0, Qt.UserRole + 1, False)  # Mark as not yet expanded

            if parent_item is None:
                self.tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)

            # Add a dummy child to show expand arrow
            dummy = QTreeWidgetItem()
            dummy.setText(0, "")
            item.addChild(dummy)

            logger.debug(f"Added tree item: {path}")

        except Exception as e:
            logger.error(f"Error adding path {path} to tree: {e}")

    def _load_children(self, parent_item):
        """Lazy-load children for a tree item on expansion."""
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

            # Get subdirectories
            try:
                entries = sorted(os.listdir(parent_path))
            except (PermissionError, OSError) as e:
                logger.debug(f"Cannot read {parent_path}: {e}")
                return

            # Add subdirectories
            subdirs_added = 0
            for entry in entries:
                full_path = os.path.join(parent_path, entry)
                try:
                    # Check if it's a directory
                    if os.path.isdir(full_path) and not os.path.islink(full_path):
                        child_item = QTreeWidgetItem()
                        child_item.setText(0, entry)
                        child_item.setData(0, Qt.UserRole, full_path)
                        child_item.setData(0, Qt.UserRole + 1, False)
                        parent_item.addChild(child_item)

                        # Add dummy to show expand arrow
                        dummy = QTreeWidgetItem()
                        dummy.setText(0, "")
                        child_item.addChild(dummy)

                        subdirs_added += 1

                except (OSError, PermissionError):
                    pass  # Skip inaccessible directories

            # Mark as expanded
            parent_item.setData(0, Qt.UserRole + 1, True)
            logger.debug(f"Loaded {subdirs_added} subdirectories for: {parent_path}")

        except Exception as e:
            logger.error(f"Error loading children for {parent_path}: {e}")

    def _on_path_input_changed(self):
        """Handle path input changes and auto-expand tree."""
        path = self.path_input.text().strip()

        # Only auto-expand if it's a valid directory
        if path and os.path.isdir(path):
            self.selected_directory = path
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
            self.selected_directory = path
            logger.debug(f"Tree item selected: {path}")

    def _on_tree_item_expanded(self, item):
        """Load children when expand arrow is clicked."""
        logger.debug(f"Tree item expanded: {item.text(0)}")
        self._load_children(item)

    def _on_path_entered(self):
        """Handle manual path entry."""
        path = self.path_input.text().strip()
        if path and os.path.isdir(path):
            self.selected_directory = path
            logger.info(f"Path entered manually: {path}")
        else:
            logger.warning(f"Invalid directory: {path}")

    def _on_select_clicked(self):
        """Confirm selection."""
        path = self.path_input.text().strip()
        if path and os.path.isdir(path):
            self.selected_directory = path
            logger.info(f"Directory confirmed: {path}")
            self.done(self.OK)
        else:
            logger.warning(f"Invalid directory: {path}")

    def _on_cancel_clicked(self):
        """Cancel selection."""
        self.selected_directory = None
        logger.info("Directory selection cancelled")
        self.done(self.CANCEL)

    def get_directory(self):
        """Return the selected directory or None if cancelled."""
        return self.selected_directory
