# **Complete PyQt5 GUI Implementation**

import sys
import os
from pathlib import Path
from typing import Dict, List, Optional
import logging, logging.handlers
import threading

logger = logging.getLogger(__name__)

from resource_monitor import ResourceMetrics, MetricsSignalEmitter
from preserve_structure_strategies import (
    PreserveStructureStrategy, PSSourceParentRest, PSFullSource, PSNone
)
from enums import FileOperation, ProcessingMode
from handle_exceptions import handle_exceptions
from processing_thread import (
    ProcessingThread, ProcessingThreadCopyMove,
    ProcessingThreadResultAbstract, ProcessingThreadResult, ProcessingThreadCopyMoveResult
)
from undo_redo_manager import UndoRedoManager, PreserveStructureType
from resource_monitor import ResourceMonitor
from plugin_manager import PluginManager
from path_file_loader import PathFileLoader
from styled_dialog import StyledDialog
from directory_picker_dialog import DirectoryPickerDialog
from file_picker_dialog import FilePickerDialog
from file_comparison_model import FileComparisonModel, RowNumberHeader

import pyqt5_loader

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLayout, QVBoxLayout, QHBoxLayout,
    QPushButton, QRadioButton, QButtonGroup, QCheckBox, QLabel, QLineEdit,
    QSpinBox, QProgressBar, QGroupBox, QHeaderView, QScrollArea, QGridLayout,
    QMenu, QStyledItemDelegate, QFrame, QDialog, QTextEdit, QTableView
)

from PyQt5.QtCore import Qt, QModelIndex, QPoint
from PyQt5.QtGui import QFont

class ElidedDelegate(QStyledItemDelegate):
    """Custom delegate to display elided (truncated) text with ellipsis and tooltip."""

    def displayText(self, value, locale):
        """Truncate text with ellipsis if too long."""
        max_length = 100
        if len(value) > max_length:
            return value[:max_length - 3] + "..."
        return value

class UniSyncMainWindow(QMainWindow):
    """
    Main GUI window for UniSync file synchronization utility.
    Integrates all components: FileManager, OperationHistory, ResourceMonitor, PluginManager.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("UniSync - File Synchronization Utility")

        # To the screen center
        self.resize(800, 700)
        screen_geometry = self.screen().geometry()
        window_rect = self.frameGeometry()
        window_rect.moveCenter(screen_geometry.center())
        self.move(window_rect.topLeft())

        # Initialize components
        self.undo_redo_manager = None  # Creating after config is loaded
        self.resource_monitor = ResourceMonitor(update_interval=1.0)
        self.plugin_manager = PluginManager(None)
        self._load_default_plugins()

        self.processing_thread: Optional[ProcessingThread] = None
        self.processing_mode = ProcessingMode.SEQUENTIAL
        self.source_dirs: List[str] = []
        self.dest_dirs: List[str] = []
        self._init_undo_redo_manager()

        # Setup UI
        self._setup_ui()

        # Connect undo_redo_manager signals to UI updates
        self._setup_undo_redo_connections()

        # Start resource monitoring
        self.metrics_emitter = MetricsSignalEmitter()
        self.metrics_emitter.metrics_updated.connect(self._on_metrics_update)
        self.resource_monitor.set_update_callback(self.metrics_emitter)
        self.resource_monitor.start()

    @handle_exceptions(logger)
    def _setup_ui(self):
        """Build the main UI layout."""
        logger.debug("Setting up UI components")

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Scroll area as the main container for groups
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.StyledPanel)
        scroll_area.setFrameShadow(QFrame.Sunken)
        scroll_area.setLineWidth(2)

        content_widget = QWidget()
        main_layout = QVBoxLayout(content_widget)
        main_layout.setSpacing(4)
        main_layout.setContentsMargins(4, 4, 4, 4)

        # 1. Processing Mode Selection
        main_layout.addWidget(self._create_mode_selection_group())

        # 2. Source Configuration
        main_layout.addWidget(self._create_source_config_group())

        # 3. Destination Configuration
        main_layout.addWidget(self._create_destination_config_group())

        # 4. File Filters
        main_layout.addWidget(self._create_file_filter_group())

        # 5. Operation Controls
        main_layout.addWidget(self._create_operation_controls_group())

        # 6. File Selection & Operations
        main_layout.addWidget(self._create_file_operations_group())

        # 7. Resource Monitor Display
        main_layout.addWidget(self._create_resource_monitor_group())

        # To push groups to the top
        main_layout.addStretch()

        # Set the content widget to the scroll area
        scroll_area.setWidget(content_widget)

        # Creating a wrapper layout for the central widget
        wrapper_layout = QVBoxLayout(central_widget)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.addWidget(scroll_area)

        # 8. Status Bar with undo/redo buttons (outside scroll area)
        self._setup_status_bar()

        # Should not allow to be enabled some possibilities
        self._set_copy_move_preserve_structure_on_off(False)

    @handle_exceptions(logger)
    def _load_default_plugins(self):
        """Load default filter plugins at startup."""
        default_plugins = [
            "extension_filter.py",
            "filename_filter.py",
            "filesize_filter.py"
        ]

        # Get the absolute path to plugins directory
        current_dir = Path(__file__).parent.absolute()
        plugins_dir = current_dir / "plugins"

        logger.info(f"Current script location: {current_dir}")
        logger.info(f"Looking for plugins at: {plugins_dir}")
        logger.info(f"Plugins directory exists: {plugins_dir.exists()}")

        # List what's actually in the plugins directory
        if plugins_dir.exists():
            actual_files = list(plugins_dir.glob("*.py"))
            logger.info(f"Files found in plugins directory: {[f.name for f in actual_files]}")

        for plugin_file in default_plugins:
            success = self.plugin_manager.load_plugin(plugin_file)
            if success:
                plugin_key = plugin_file.replace(".py", "")
                logger.info(f"Loaded plugin: {plugin_key}")
            else:
                logger.warning(f"Failed to load plugin: {plugin_file}")

    @handle_exceptions(logger)
    def _create_mode_selection_group(self) -> QGroupBox:
        """Create processing mode selection (Sequential/Parallel)."""
        group = QGroupBox("Processing Mode")
        layout = QHBoxLayout()

        self.mode_group = QButtonGroup()

        # Sequential (Threading) Radio Button
        self.rb_sequential = QRadioButton("Sequential (Threading - GIL)")
        self.mode_group.addButton(self.rb_sequential, 0)
        self.rb_sequential.setChecked(True)
        self.rb_sequential.setToolTip("Uses FileManagerBase with threading. Good for I/O-bound operations.")
        self.rb_sequential.toggled.connect(self._on_mode_changed)

        # Parallel (Multiprocessing) Radio Button
        self.rb_parallel = QRadioButton("Parallel (Multiprocessing - No GIL)")
        self.mode_group.addButton(self.rb_parallel, 1)
        self.rb_parallel.setToolTip("Under revision! Uses FileManagerParallel with multiprocessing. Good for CPU-intensive operations.")
        self.rb_parallel.toggled.connect(self._on_mode_changed)
        self.rb_parallel.setEnabled(False)

        layout.addWidget(self.rb_sequential)
        layout.addWidget(self.rb_parallel)
        layout.addStretch()

        # Reduce vertical margins
        layout.setContentsMargins(2, 2, 2, 2)

        group.setLayout(layout)
        return group

    @handle_exceptions(logger)
    def _create_source_config_group(self) -> QGroupBox:
        """Create dynamic source configuration."""
        group = QGroupBox("Source Configuration")
        layout = QVBoxLayout()

        # Source count input
        count_layout = QHBoxLayout()
        count_layout.addWidget(QLabel("Number of Sources:"))

        self.source_count_spinbox = QSpinBox()
        self.source_count_spinbox.setMinimum(1)
        self.source_count_spinbox.setMaximum(20)
        self.source_count_spinbox.setValue(1)
        count_layout.addWidget(self.source_count_spinbox)

        btn_create_sources = QPushButton("Create Source Fields")
        btn_create_sources.clicked.connect(self._on_create_sources)
        count_layout.addWidget(btn_create_sources)

        # Load/Save buttons
        btn_load = QPushButton("Load from File")
        btn_load.clicked.connect(self._on_load_sources_from_file)
        count_layout.addWidget(btn_load)

        btn_save = QPushButton("Save to File")
        btn_save.clicked.connect(self._on_save_sources_to_file)
        count_layout.addWidget(btn_save)

        # Add reset sources button
        btn_reset_sources = QPushButton("Clear Sources")
        btn_reset_sources.clicked.connect(self._on_clear_sources)
        count_layout.addWidget(btn_reset_sources)

        count_layout.addStretch()
        layout.addLayout(count_layout)

        # Scroll area for source paths
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        self.sources_layout = QVBoxLayout(scroll_widget)
        self.sources_layout.addStretch() # Initial stretch for empty state
        scroll.setWidget(scroll_widget)

        layout.addWidget(scroll)
        group.setLayout(layout)
        self.source_widgets = []
        return group

    @handle_exceptions(logger)
    def _create_destination_config_group(self) -> QGroupBox:
        """Create dynamic destination configuration."""
        group = QGroupBox("Destination Configuration")
        layout = QVBoxLayout()

        # Destination count input
        count_layout = QHBoxLayout()
        count_layout.addWidget(QLabel("Number of Destinations:"))

        self.dest_count_spinbox = QSpinBox()
        self.dest_count_spinbox.setMinimum(1)
        self.dest_count_spinbox.setMaximum(20)
        self.dest_count_spinbox.setValue(1)
        count_layout.addWidget(self.dest_count_spinbox)

        btn_create_dests = QPushButton("Create Destination Fields")
        btn_create_dests.clicked.connect(self._on_create_destinations)
        count_layout.addWidget(btn_create_dests)

        # Load/Save buttons
        btn_load = QPushButton("Load from File")
        btn_load.clicked.connect(self._on_load_destinations_from_file)
        count_layout.addWidget(btn_load)

        btn_save = QPushButton("Save to File")
        btn_save.clicked.connect(self._on_save_destinations_to_file)
        count_layout.addWidget(btn_save)

        # Add reset destinations button
        btn_reset_dests = QPushButton("Clear Destinations")
        btn_reset_dests.clicked.connect(self._on_clear_destinations)
        count_layout.addWidget(btn_reset_dests)

        count_layout.addStretch()
        layout.addLayout(count_layout)

        # Scroll area for destination paths
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        self.destinations_layout = QVBoxLayout(scroll_widget)
        self.destinations_layout.addStretch()  # Initial stretch for empty state
        scroll.setWidget(scroll_widget)

        layout.addWidget(scroll)
        group.setLayout(layout)
        self.destination_widgets = []
        return group

    @handle_exceptions(logger)
    def _create_file_filter_group(self) -> QGroupBox:
        """Create file filtering controls using loaded plugins."""
        group = QGroupBox("File Filters On Discovery Stage")
        layout = QVBoxLayout()

        # Extension filter
        ext_layout = QHBoxLayout()
        ext_layout.addWidget(QLabel("Extensions:"))
        self.ext_filter_input = QLineEdit()
        self.ext_filter_input.setPlaceholderText(".txt, .log, .py (comma-separated)")
        self.ext_filter_checkbox = QCheckBox("Enable")
        ext_layout.addWidget(self.ext_filter_input)
        ext_layout.addWidget(self.ext_filter_checkbox)
        layout.addLayout(ext_layout)

        # Filename filter
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("Filename Pattern:"))
        self.name_filter_input = QLineEdit()
        self.name_filter_input.setPlaceholderText("backup_* or regex pattern")
        self.name_filter_checkbox = QCheckBox("Enable")
        self.name_filter_regex = QCheckBox("Regex")
        name_layout.addWidget(self.name_filter_input)
        name_layout.addWidget(self.name_filter_checkbox)
        name_layout.addWidget(self.name_filter_regex)
        layout.addLayout(name_layout)

        # File size filter
        size_layout = QHBoxLayout()
        size_layout.addWidget(QLabel("File Size:"))
        self.size_min_input = QLineEdit()
        self.size_min_input.setPlaceholderText("Min (e.g., 1KB)")
        self.size_max_input = QLineEdit()
        self.size_max_input.setPlaceholderText("Max (e.g., 100MB)")
        self.size_filter_checkbox = QCheckBox("Enable")
        size_layout.addWidget(self.size_min_input)
        size_layout.addWidget(self.size_max_input)
        size_layout.addWidget(self.size_filter_checkbox)
        layout.addLayout(size_layout)

        group.setLayout(layout)
        return group

    def _get_active_filters(self) -> List[dict]:
        """Compile active filter configurations from UI"""
        active_filters = []

        # Extension filter
        if self.ext_filter_checkbox.isChecked():
            ext_text = self.ext_filter_input.text().strip()
            if ext_text:
                extensions = [e.strip() for e in ext_text.split(',')]
                active_filters.append({
                    'plugin_key': 'extension_filter',
                    'params': {'extensions': extensions}
                })

        # Filename filter
        if self.name_filter_checkbox.isChecked():
            pattern = self.name_filter_input.text().strip()
            if pattern:
                active_filters.append({
                    'plugin_key': 'filename_filter',
                    'params': {
                        'pattern': pattern,
                        'regex': self.name_filter_regex.isChecked()
                    }
                })

        # File size filter
        if self.size_filter_checkbox.isChecked():
            min_size = self.size_min_input.text().strip() or None
            max_size = self.size_max_input.text().strip() or None

            if min_size or max_size:
                active_filters.append({
                    'plugin_key': 'filesize_filter',
                    'params': {
                        'min_size': min_size,
                        'max_size': max_size
                    }
                })

        return active_filters

    @handle_exceptions(logger)
    def _create_operation_controls_group(self) -> QGroupBox:
        """Create start/pause/resume/stop control buttons."""
        group = QGroupBox("Operation Controls")
        layout = QHBoxLayout()

        # Start Button
        self.btn_start = QPushButton("Start")
        self.btn_start.setStyleSheet("""        
                    QPushButton {
                        background-color: #FFC107;
                        color: white;
                        font-weight: bold;
                    }
                    QPushButton:disabled {
                        background-color: #CCCCCC;
                        color: #888888;
                    }
                """)

        self.btn_start.clicked.connect(self._on_start_processing)
        layout.addWidget(self.btn_start)

        # Pause Button
        self.btn_pause = QPushButton("Pause")
        self.btn_pause.setStyleSheet("""        
                    QPushButton {
                        background-color: #FFC107;
                        color: white;
                        font-weight: bold;
                    }
                    QPushButton:disabled {
                        background-color: #CCCCCC;
                        color: #888888;
                    }
                """)

        self.btn_pause.clicked.connect(self._on_pause_processing)
        self.btn_pause.setEnabled(False)
        layout.addWidget(self.btn_pause)

        # Resume Button
        self.btn_resume = QPushButton("Resume")
        self.btn_resume.setStyleSheet("""        
                    QPushButton {
                        background-color: #FFC107;
                        color: white;
                        font-weight: bold;
                    }
                    QPushButton:disabled {
                        background-color: #CCCCCC;
                        color: #888888;
                    }
                """)

        self.btn_resume.clicked.connect(self._on_resume_processing)
        self.btn_resume.setEnabled(False)
        layout.addWidget(self.btn_resume)

        # Stop Button
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setStyleSheet("""        
                    QPushButton {
                        background-color: #FFC107;
                        color: white;
                        font-weight: bold;
                    }
                    QPushButton:disabled {
                        background-color: #CCCCCC;
                        color: #888888;
                    }
                """)

        self.btn_stop.clicked.connect(self._on_stop_processing)
        self.btn_stop.setEnabled(False)
        layout.addWidget(self.btn_stop)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(100)
        layout.addWidget(self.progress_bar)

        group.setLayout(layout)
        return group

    @handle_exceptions(logger)
    def _create_file_operations_group(self) -> QGroupBox:
        """Create file selection and copy/move operations."""
        group = QGroupBox("File Operations")
        layout = QVBoxLayout()

        # First row: Operation radio buttons Copy/Move and Preserve Sources structure group
        control_layout = QHBoxLayout()

        # Copy/Move operations group
        operations_group = QGroupBox("Operation")
        operations_layout = QHBoxLayout()
        operations_layout.setContentsMargins(2, 2, 2, 2)
        operations_layout.setSpacing(10)

        self.mode_copy_move_group = QButtonGroup()

        # Copy mode radio button
        self.rb_copy_mode = QRadioButton("Copy to Destination(s)")
        self.mode_copy_move_group.addButton(self.rb_copy_mode, 0)
        operations_layout.addWidget(self.rb_copy_mode)

        # Move mode radio button
        self.rb_move_mode = QRadioButton("Move to Destination(s)")
        self.mode_copy_move_group.addButton(self.rb_move_mode, 1)
        operations_layout.addWidget(self.rb_move_mode)

        operations_group.setLayout(operations_layout)
        control_layout.addWidget(operations_group)

        # Preserve Source structure group with radio buttons
        preserve_structure_group = QGroupBox("Preserve Sources structure")
        preserve_layout = QHBoxLayout()
        preserve_layout.setContentsMargins(2, 2, 2, 2)
        preserve_layout.setSpacing(10)

        self.preserve_structure_group = QButtonGroup()

        # Option 1: Dest + Source_Parent_Rest + Rest
        self.rb_preserve_parent_rest = QRadioButton(
            "Dest + Source_Parent_Rest + Rest"
        )
        self.preserve_structure_group.addButton(self.rb_preserve_parent_rest, 0)
        preserve_layout.addWidget(self.rb_preserve_parent_rest)

        # Option 2: Dest + Source (full directory structure)
        self.rb_preserve_full_source = QRadioButton(
            "Dest + Source"
        )
        self.preserve_structure_group.addButton(self.rb_preserve_full_source, 1)
        preserve_layout.addWidget(self.rb_preserve_full_source)

        # Option 3: Dest (None)
        self.rb_preserve_none = QRadioButton(
            "None (Flatten)"
        )
        self.preserve_structure_group.addButton(self.rb_preserve_none, 2)
        preserve_layout.addWidget(self.rb_preserve_none)

        # Set default selection
        self.rb_preserve_full_source.setChecked(True)
        preserve_structure_group.setLayout(preserve_layout)
        control_layout.addWidget(preserve_structure_group)

        # Add stretch to push controls to the left
        control_layout.addStretch()
        layout.addLayout(control_layout)

        self.rb_copy_mode.setChecked(True)

        # Reduce vertical margins
        layout.setContentsMargins(2, 2, 2, 2)

        # Button row: Select All, Deselect All, Reset
        button_row_layout = QHBoxLayout()
        button_row_layout.setSpacing(10)

        self.btn_select_all = QPushButton("Select All")
        self.btn_select_all.clicked.connect(self._on_select_all_files)
        button_row_layout.addWidget(self.btn_select_all)

        self.btn_deselect_all = QPushButton("Deselect All")
        self.btn_deselect_all.clicked.connect(self._on_deselect_all_files)
        button_row_layout.addWidget(self.btn_deselect_all)

        self.btn_clear_table = QPushButton("Clear Table")
        self.btn_clear_table.clicked.connect(self._on_clear_comparison_table)
        button_row_layout.addWidget(self.btn_clear_table)

        button_row_layout.addStretch()
        layout.addLayout(button_row_layout)

        # File comparison table
        # Initialize model
        self.comparison_model = FileComparisonModel(self)

        # Create view
        self.comparison_table = QTableView()
        self.comparison_table.setModel(self.comparison_model)

        self.comparison_table.setVerticalHeader(RowNumberHeader(self.comparison_table))
        self.comparison_table.verticalHeader().setEnabled(True)

        # Selection behavior
        self.comparison_table.setSelectionBehavior(QTableView.SelectRows)
        self.comparison_table.setSelectionMode(QTableView.ExtendedSelection)

        # Sorting (automatic with model)
        self.comparison_table.setSortingEnabled(True)
        self.comparison_table.sortByColumn(0, Qt.AscendingOrder)

        # Header configuration
        header = self.comparison_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Interactive)  # file_name - manual resize
        header.setSectionResizeMode(1, QHeaderView.Interactive)  # Size - manual resize
        header.setSectionResizeMode(2, QHeaderView.Fixed)  # Hash - fixed
        header.resizeSection(2, 100)
        header.setSectionResizeMode(3, QHeaderView.Interactive)  # Count - manual resize
        header.setSectionResizeMode(4, QHeaderView.Stretch)  # Paths - stretches

        # The cursor will change to resize cursor when hovering over column borders
        header.setCursor(Qt.ArrowCursor)

        # Enable interactive column resizing
        header.setSectionsMovable(True)
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(
            self._show_column_filter_menu
        )

        # Track when user manually resizes columns
        header.sectionResized.connect(self._on_column_resized)

        # Apply ElidedDelegate to "Sources (Path)" column
        elided_delegate = ElidedDelegate(self.comparison_table)
        self.comparison_table.setItemDelegateForColumn(4, elided_delegate)

        # Selection change signal
        self.comparison_table.selectionModel().selectionChanged.connect(
            self._on_comparison_table_selection_changed
        )

        # Tooltips on hover
        self.comparison_table.setMouseTracking(True)
        self.comparison_table.entered.connect(self._on_table_item_entered)

        # Minimum height
        self.comparison_table.setMinimumHeight(250)

        # Add row numbers via vertical header (left panel)
        self._setup_vertical_header()

        layout.addWidget(self.comparison_table, stretch=1)

        # Status bar
        status_layout = QHBoxLayout()
        status_layout.setContentsMargins(5, 5, 5, 5)
        status_layout.setSpacing(10)

        self.table_status_label = QLabel("Total Records: 0")
        self.table_status_label.setStyleSheet(
            "QLabel { color: #555; font-weight: bold; padding: 5px; }"
        )
        status_layout.addStretch()
        status_layout.addWidget(self.table_status_label)
        status_layout.addStretch()

        layout.addLayout(status_layout)

        # Connect model's row count change signal to status update
        self.comparison_model.rowCountChanged.connect(self._update_table_status)

        group.setLayout(layout)
        return group

    @handle_exceptions(logger)
    def _setup_vertical_header(self, checked: bool = False) -> None:
        """
        Configure the vertical header (left panel) to show row numbers.

        The vertical header is the row number column on the left.
        """
        vertical_header = self.comparison_table.verticalHeader()
        vertical_header.setVisible(True)

        # Set consistent row height
        vertical_header.setDefaultSectionSize(24)

        # ResizeToContents to auto-fit width based on content
        vertical_header.setSectionResizeMode(QHeaderView.ResizeToContents)
        vertical_header.setMinimumWidth(35)

        # Connect model's rowsInserted signal to update row numbers
        self.comparison_model.rowsInserted.connect(self._update_row_numbers)
        self.comparison_model.rowsRemoved.connect(self._update_row_numbers)
        self.comparison_model.modelReset.connect(self._update_row_numbers)

    @handle_exceptions(logger)
    def _update_row_numbers(self, checked: bool = False):
        """Update the vertical header with row numbers."""
        vertical_header = self.comparison_table.verticalHeader()
        vertical_header.updateGeometry()

    @handle_exceptions(logger)
    def _on_column_resized(self, logical_index: int, old_size: int, new_size: int) -> None:
        """Called when user manually resizes a column."""
        header = self.comparison_table.horizontalHeader()

        # Prevent Hash column from being wider than 200px
        if logical_index == 2:
            max_width = 400
            if new_size > max_width:
                header.resizeSection(logical_index, max_width)
                logger.info(f"Hash column width capped at {max_width}px")

        # Log the resize
        logger.debug(f"Column {logical_index} resized: {old_size}px -> {new_size}px")

    @handle_exceptions(logger)
    def _update_table_status(self, visible_count: int = None) -> None:
        """Update the status bar with total visible and hidden record counts."""
        total_rows = len(self.comparison_model.files)
        filtered_rows = len(self.comparison_model.filtered_keys)

        # Calculate total size of filtered records
        total_size = self.comparison_model.calculate_total_size()

        if filtered_rows == total_rows:
            status_text = f"Total Records: {total_rows} | Total Size: {total_size}"
        else:
            status_text = f"Total Records: {filtered_rows} / {total_rows} (filtered) | Total Size: {total_size}"

        self.table_status_label.setText(status_text)
        logger.debug(status_text)

    @handle_exceptions(logger)
    def _on_clear_sources(self, checked: bool = False) -> None:
        """Clear all source path inputs."""
        reply = StyledDialog(
            "Clear Sources",
            "Remove all source path entries?",
            dialog_type="warning",
            buttons=[
                (StyledDialog.YES, "Yes"),
                (StyledDialog.NO, "No")
            ],
            default_button=StyledDialog.NO,
            parent=self,
            scrollable=True,
            min_width=500,
            min_height=200
        ).exec_()

        if reply == StyledDialog.YES:
            # Clear all source widgets from layout
            self._clear_layout(self.sources_layout)

            # Clear tracked widgets
            self.source_widgets = []
            self.source_dirs = []

            # Add stretch back for empty state
            self.sources_layout.addStretch()

            self._on_clear_comparison_table()

            logger.info("Source paths cleared")

    @handle_exceptions(logger)
    def _on_clear_destinations(self, checked: bool = False) -> None:
        """Clear all destination path inputs."""
        reply = StyledDialog(
            "Clear Destinations",
            "Remove all destination path entries?",
            dialog_type="warning",
            buttons=[
                (StyledDialog.YES, "Yes"),
                (StyledDialog.NO, "No")
            ],
            default_button=StyledDialog.NO,
            parent=self,
            scrollable=True,
            min_width=500,
            min_height=200
        ).exec_()

        if reply == StyledDialog.YES:
            # Clear all destination widgets from layout
            self._clear_layout(self.destinations_layout)

            # Clear tracked widgets
            self.destination_widgets = []
            self.dest_dirs = []

            # Add stretch back for empty state
            self.destinations_layout.addStretch()

            self._on_clear_comparison_table()

            logger.info("Destination paths cleared")

    @handle_exceptions(logger)
    def _on_load_sources_from_file(self, checked: bool = False):
        """Load source path from a .txt file."""
        file_path = None

        dialog = FilePickerDialog(
            title="Load Sources from File",
            initial_path=str(Path.home()),
            file_filters=[
                ("Text Files", "*.txt")
            ],
            parent=self
        )

        if dialog.exec_() == FilePickerDialog.OK:
            file_path = dialog.get_file()
            if file_path:
                logger.info(f"Selected file: {file_path}")
        else:
            logger.info("File selection cancelled")

        if not file_path:
            return

        paths, errors = PathFileLoader.load_paths_from_file(file_path)

        if errors:
            self._show_load_results_dialog("Source Load Results", paths, errors)

        if paths:
            # Update spinbox
            self.source_count_spinbox.setValue(len(paths))
            self._on_create_sources()

            # Populate fields
            for i, path in enumerate(paths):
                if i < len(self.source_widgets):
                    self.source_widgets[i].setText(path)
                    if path not in self.source_dirs:
                        self.source_dirs[i] = path

            if not errors:
                StyledDialog(
                    "Success",
                    f"Loaded {len(paths)} source path(s)",
                    dialog_type="info",
                    buttons=[(StyledDialog.OK, "OK")],
                    default_button=StyledDialog.OK,
                    parent=self,
                    scrollable=True,
                    min_width=500,
                    min_height=200
                ).exec_()

    @handle_exceptions(logger)
    def _on_load_destinations_from_file(self, checked: bool = False):
        """Load destination paths from a .txt file."""
        file_path = None

        dialog = FilePickerDialog(
            title="Load Destinations from File",
            initial_path=str(Path.home()),
            file_filters=[
                ("Text Files", "*.txt")
            ],
            parent=self
        )

        if dialog.exec_() == FilePickerDialog.OK:
            file_path = dialog.get_file()
            if file_path:
                logger.info(f"Selected file: {file_path}")
        else:
            logger.info("File selection cancelled")

        if not file_path:
            return

        paths, errors = PathFileLoader.load_paths_from_file(file_path)

        if errors:
            self._show_load_results_dialog("Destination Load Results", paths, errors)

        if paths:
            # Update spinbox
            self.dest_count_spinbox.setValue(len(paths))
            self._on_create_destinations()

            # Populate fields
            for i, path in enumerate(paths):
                if i < len(self.destination_widgets):
                    self.destination_widgets[i].setText(path)
                    if path not in self.dest_dirs:
                        self.dest_dirs[i] = path

            if not errors:
                StyledDialog(
                    "Success",
                    f"Loaded {len(paths)} destination path(s)",
                    dialog_type="info",
                    buttons=[(StyledDialog.OK, "OK")],
                    default_button=StyledDialog.OK,
                    parent=self,
                    scrollable=True,
                    min_width=500,
                    min_height=200
                ).exec_()

    @handle_exceptions(logger)
    def _on_save_sources_to_file(self, checked: bool = False):
        """Save current sources to a .txt file."""
        paths = [w.text().strip() for w in self.source_widgets if w.text().strip()]

        if not paths:
            StyledDialog(
                "Warning",
                "No source paths to save",
                dialog_type="warning",
                buttons=[(StyledDialog.OK, "OK")],
                default_button=StyledDialog.OK,
                parent=self,
                scrollable=True,
                min_width=500,
                min_height=200
            ).exec_()
            return

        file_path = None

        dialog = FilePickerDialog(
            title="Save Sources to File",
            initial_path=str(Path.home()),
            file_filters=[
                ("Text Files", "*.txt")
            ],
            parent=self
        )

        if dialog.exec_() == FilePickerDialog.OK:
            file_path = dialog.get_file()
            if file_path:
                logger.info(f"Selected file: {file_path}")
        else:
            logger.info("File selection cancelled")

        if not file_path:
            return

        success, message = PathFileLoader.save_paths_to_file(file_path, paths)

        if success:
            StyledDialog(
                "Success",
                message,
                dialog_type="info",
                buttons=[(StyledDialog.OK, "OK")],
                default_button=StyledDialog.OK,
                parent=self,
                scrollable=True,
                min_width=500,
                min_height=200
            ).exec_()

        else:
            StyledDialog(
                "Error",
                message,
                dialog_type="error",
                buttons=[(StyledDialog.OK, "OK")],
                default_button=StyledDialog.OK,
                parent=self,
                scrollable=True,
                min_width=500,
                min_height=200
            ).exec_()

    @handle_exceptions(logger)
    def _on_save_destinations_to_file(self, checked: bool = False):
        """Save current destinations to a .txt file."""
        paths = [w.text().strip() for w in self.destination_widgets if w.text().strip()]

        if not paths:
            StyledDialog(
                "Warning",
                "No destination paths to save",
                dialog_type="warning",
                buttons=[(StyledDialog.OK, "OK")],
                default_button=StyledDialog.OK,
                parent=self,
                scrollable=True,
                min_width=500,
                min_height=200
            ).exec_()
            return

        file_path = None

        dialog = FilePickerDialog(
            title="Save Destinations to File",
            initial_path=str(Path.home()),
            file_filters=[
                ("Text Files", "*.txt")
            ],
            parent=self
        )

        if dialog.exec_() == FilePickerDialog.OK:
            file_path = dialog.get_file()
            if file_path:
                logger.info(f"Selected file: {file_path}")
        else:
            logger.info("File selection cancelled")

        if not file_path:
            return

        success, message = PathFileLoader.save_paths_to_file(file_path, paths)

        if success:
            StyledDialog(
                "Success",
                message,
                dialog_type="info",
                buttons=[(StyledDialog.OK, "OK")],
                default_button=StyledDialog.OK,
                parent=self,
                scrollable=True,
                min_width=500,
                min_height=200
            ).exec_()

        else:
            StyledDialog(
                "Error",
                message,
                dialog_type="error",
                buttons=[(StyledDialog.OK, "OK")],
                default_button=StyledDialog.OK,
                parent=self,
                scrollable=True,
                min_width=500,
                min_height=200
            ).exec_()

    @handle_exceptions(logger)
    def _show_load_results_dialog(self, title: str, paths: List[str], errors: List[str]):
        """Show dialog with load results and any warnings/errors."""
        dialog = QDialog(self)
        dialog.setWindowTitle(title)

        layout = QVBoxLayout(dialog)

        # Results text
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)

        result_text = f"Loaded {len(paths)} valid path(s)\n"
        if paths:
            result_text += "\nValid Paths:\n"
            for p in paths:
                result_text += f"{p}\n"

        if errors:
            result_text += f"\n {len(errors)} warning(s)/error(s):\n"
            for err in errors:
                result_text += f"{err}\n"

        text_edit.setText(result_text)
        layout.addWidget(text_edit)

        # Buttons
        button_layout = QHBoxLayout()
        btn_ok = QPushButton("OK")
        btn_ok.clicked.connect(dialog.accept)
        button_layout.addStretch()
        button_layout.addWidget(btn_ok)
        layout.addLayout(button_layout)

        dialog.exec()

    @handle_exceptions(logger)
    def _on_clear_comparison_table(self, checked: bool = False) -> None:
        """Clear comparison table and reset filters."""
        if self.comparison_table:
            self.comparison_model.filters = {i: None for i in range(5)}
            self.comparison_model.set_files({})
            self.comparison_table.sortByColumn(0, Qt.AscendingOrder)
            self._set_copy_move_preserve_structure_on_off(flag=False)
            if self.undo_redo_manager:
                self.undo_redo_manager.clear_history()
                self._update_undo_redo_button_states()

            logger.info("Comparison table cleared")

    @handle_exceptions(logger)
    def _on_select_all_files(self, checked: bool = False) -> None:
        """Select all visible files in comparison table."""
        if self.comparison_model:
            if self.comparison_model.rowCount() == 0:
                StyledDialog(
                    "No Files",
                    "Comparison table is empty. Run Discovery/Start first.",
                    dialog_type="info",
                    buttons=[(StyledDialog.OK, "OK")],
                    default_button=StyledDialog.OK,
                    parent=self,
                    scrollable=True,
                    min_width=500,
                    min_height=200
                ).exec_()
                return

            self.comparison_table.selectAll()
            logger.info(f"Selected all {self.comparison_model.rowCount()} files")

    @handle_exceptions(logger)
    def _on_deselect_all_files(self, checked: bool = False) -> None:
        """Deselect all files in comparison table."""
        if self.comparison_table:
            self.comparison_table.clearSelection()
            logger.info("Deselected all files")

    @handle_exceptions(logger)
    def _on_table_item_entered(self, index: QModelIndex) -> None:
        """Show tooltip with full path when hovering over column 4 (Sources Path)."""
        if index.isValid() and index.column() == 4:
            key = self.comparison_model.filtered_keys[index.row()]
            sources = self.comparison_model.files[key]
            tooltip_text = "\n".join(sorted(sources))
            self.comparison_table.setToolTip(tooltip_text)
        else:
            self.comparison_table.setToolTip("")

    @handle_exceptions(logger)
    def _show_column_filter_menu(self, position: QPoint) -> None:
        """
        Show context menu on column header for filtering and column management.

        Users can:
        1. Filter by column values
        2. Reset column widths
        3. Auto-fit columns
        """
        header = self.comparison_table.horizontalHeader()
        column = header.logicalIndexAt(position)

        if column < 0:
            return

        menu = QMenu(self)

        # Filtering section
        menu.addSection("Filtering")

        # "Show All" option
        clear_action = menu.addAction("Show All (Clear Filter)")
        clear_action.triggered.connect(
            lambda: self.comparison_model.apply_filter(column, None)
        )

        menu.addSeparator()

        # Get unique values for this column (instant-no iteration)
        unique_values = self._get_unique_column_values(column)

        # Multi-value filtering
        # Show checkboxes for each value
        current_filters = self.comparison_model.get_filter_values(column)

        # Limit display to 100 items to avoid massive menus
        for value in sorted(unique_values)[:100]:
            # Truncate display text for readability
            display_text = value[:60] + "..." if len(value) > 60 else value

            action = menu.addAction(display_text)
            action.setCheckable(True)

            # Check if this value is already in the filter
            is_checked = bool(current_filters is not None) and (value in current_filters)
            action.setChecked(is_checked)

            action.triggered.connect(
                lambda checked, v=value, c=column: (
                    self._on_filter_value_toggled(c, v, checked)
                )
            )

        # If more than 100 unique values, show a note
        if len(unique_values) > 100:
            menu.addSeparator()
            note_action = menu.addAction(
                f"... and {len(unique_values) - 100} more"
            )
            note_action.setEnabled(False)

        # Column management section
        menu.addSeparator()
        menu.addSection("Column Width")

        # Auto-fit this column
        auto_fit_action = menu.addAction("Auto-Fit This Column")
        auto_fit_action.triggered.connect(
            lambda: header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        )

        # Reset this column to default width
        reset_action = menu.addAction("Reset Column Width")
        reset_action.triggered.connect(
            lambda: self._reset_column_width(column)
        )

        menu.addSeparator()

        # Auto-fit all columns
        auto_fit_all_action = menu.addAction("Auto-Fit All Columns")
        auto_fit_all_action.triggered.connect(self._auto_fit_all_columns)

        # Reset all columns
        reset_all_action = menu.addAction("Reset All Column Widths")
        reset_all_action.triggered.connect(self._reset_all_column_widths)

        menu.exec_(self.comparison_table.mapToGlobal(position))

    @handle_exceptions(logger)
    def _reset_column_width(self, column: int) -> None:
        """Reset a single column to its default width."""
        header = self.comparison_table.horizontalHeader()

        # Default widths
        default_widths = {
            0: 150,  # File Name
            1: 100,  # Size
            2: 100,   # Hash
            3: 100,  # Count
            4: 200,  # Paths
        }

        if column in default_widths:
            header.resizeSection(column, default_widths[column])
            # Set back to interactive mode
            header.setSectionResizeMode(column, QHeaderView.Interactive)
            logger.info(f"Reset column {column} to default width")

    @handle_exceptions(logger)
    def _reset_all_column_widths(self, checked: bool = False) -> None:
        """Reset all columns to default widths."""
        header = self.comparison_table.horizontalHeader()

        # Default widths
        default_widths = {
            0: 150,  # File Name
            1: 100,  # Size
            2: 100,  # Hash
            3: 100,  # Count
            4: 200,  # Paths
        }

        for column, width in default_widths.items():
            header.resizeSection(column, width)
            header.setSectionResizeMode(column, QHeaderView.Interactive)

        logger.info("Reset all column width to defaults")

    @handle_exceptions(logger)
    def _auto_fit_all_columns(self, checked: bool = False) -> None:
        """Auto-fit all columns to their content."""
        header = self.comparison_table.horizontalHeader()

        for col in range(self.comparison_model.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)

        logger.info("Auto-fitted all columns to content")

    @handle_exceptions(logger)
    def _on_filter_value_toggled(self, column: int, value: str, is_checked: bool) -> None:
        """
        Handle toggle of a filter value in the context menu.

        Now supports multiple values per filter.

        Args:
            column: Column index
            value: The value to toggle
            is_checked: True if checked, False if unchecked
        """
        current_filters = self.comparison_model.get_filter_values(column)

        if is_checked:
            # Add this value to the filter
            if current_filters is None:
                # First value for this column
                self.comparison_model.apply_filter(column, [value])
            else:
                # Add to existing filters
                self.comparison_model.add_filter_value(column, value)

            logger.debug(f"Added filter: Column {column} = {value}")
        else:
            # Remove this value from the filter
            if current_filters:
                self.comparison_model.remove_filter_value(column, value)

            logger.debug(f"Removed filter: Column {column} = {value}")

    @handle_exceptions(logger)
    def _get_unique_column_values(self, column: int) -> set:
        """
        Extract unique values from a column efficiently.

        This operates on the model's in-memory data, not the view,
        so it's instant even with 60k+ rows.
        """
        unique = set()

        if column == 0:  # file_name
            unique = {key[0] for key in self.comparison_model.files.keys()}

        elif column == 1:  # file_size
            unique = {str(key[1]) for key in self.comparison_model.files.keys()}

        elif column == 2:  # file_hash
            unique = {
                key[2] for key in self.comparison_model.files.keys()
                if key[2]
            }

        elif column == 3:  # Source count
            unique = {
                str(len(sources))
                for sources in self.comparison_model.files.values()
            }

        elif column == 4:  # Source paths (individual paths)
            for sources in self.comparison_model.files.values():
                unique.update(sources)

        return unique

    @handle_exceptions(logger)
    def _on_comparison_table_selection_changed(self, selected: 'QItemSelection',
                                               deselected: 'QItemSelection', checked: bool = False):
        """
        Handle custom multi-selection with Ctrl/Shift modifiers.

        # Users can now:
        - Ctrl+Click for multiple selection
        - Shift+Click for range selection
        - Ctrl+A for select All

        Args:
            selected: QItemSelection of newly selected items
            deselected: QItemSelection of newly deselected items
        """
        selected_indexes = self.comparison_table.selectionModel().selectedRows()
        selected_count = len(selected_indexes)

        # Update button states
        self._set_copy_move_preserve_structure_on_off(selected_count > 0)

        if selected_count > 0:
            logger.debug(f"Selected rows: {selected_count}")

    @handle_exceptions(logger)
    def _create_resource_monitor_group(self) -> QGroupBox:
        """Create resource usage display (CPU/Memory gauges)."""
        group = QGroupBox("System Resource Monitor")
        layout = QGridLayout()

        # CPU Label
        layout.addWidget(QLabel("CPU Usage:"), 0, 0)
        self.cpu_label = QLabel("0%")
        self.cpu_label.setFont(QFont("Arial", 10, QFont.Bold))
        self.cpu_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.cpu_label, 0, 1)

        # CPU Progress Bar
        self.cpu_progress = QProgressBar()
        self.cpu_progress.setMaximum(100)
        self.cpu_progress.setMinimumHeight(30) # Add minimal height
        self.cpu_progress.setTextVisible(True) # Show percentage text
        self.cpu_progress.setAlignment(Qt.AlignCenter)
        self.cpu_progress.setStyleSheet("""
            QProgressBar {
                border: 2px solid grey;
                border-radius: 5px;
                background-color: #e0e0e0;
                padding: 2px;
                color: #000;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background-color: #FF9800;
                border-radius: 3px;
            }
        """)
        layout.addWidget(self.cpu_progress, 0, 2)

        # Memory Label
        layout.addWidget(QLabel("Memory Usage:"), 1, 0)
        self.memory_label = QLabel("0%")
        self.memory_label.setFont(QFont("Arial", 10, QFont.Bold))
        self.memory_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.memory_label, 1, 1)

        # Memory Progress Bar
        self.memory_progress = QProgressBar()
        self.memory_progress.setMaximum(100)
        self.memory_progress.setMinimumHeight(30)  # Add minimal height
        self.memory_progress.setTextVisible(True)  # Show percentage text
        self.memory_progress.setAlignment(Qt.AlignCenter)
        self.memory_progress.setStyleSheet("""
            QProgressBar {
                border: 2px solid grey;
                border-radius: 5px;
                background-color: #e0e0e0;
                padding: 2px;
                color: #000;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background-color: #2196F3;
                border-radius: 3px;
            }
        """)
        layout.addWidget(self.memory_progress, 1, 2)

        # Process CPU
        layout.addWidget(QLabel("Process CPU:"), 2, 0)
        self.process_cpu_label = QLabel("0%")
        self.process_cpu_label.setFont(QFont("Arial", 10, QFont.Bold))
        self.process_cpu_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.process_cpu_label, 2, 1)

        # Process CPU Progress Bar
        self.process_cpu_progress = QProgressBar()
        self.process_cpu_progress.setMaximum(100)
        self.process_cpu_progress.setMinimumHeight(30)  # Add minimal height
        self.process_cpu_progress.setTextVisible(True)  # Show percentage text
        self.process_cpu_progress.setAlignment(Qt.AlignCenter)
        self.process_cpu_progress.setStyleSheet("""
                QProgressBar {
                    border: 2px solid grey;
                    border-radius: 5px;
                    background-color: #e0e0e0;
                    padding: 2px;
                    color: #000;
                    font-weight: bold;
                }
                QProgressBar::chunk {
                    background-color: #4CAF50;
                    border-radius: 3px;
                }
            """)
        layout.addWidget(self.process_cpu_progress, 2, 2)

        # Process Memory
        layout.addWidget(QLabel("Process Memory:"), 3, 0)
        self.process_memory_label = QLabel("0 MB")
        self.process_memory_label.setFont(QFont("Arial", 10, QFont.Bold))
        self.process_memory_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.process_memory_label, 3, 1)

        group.setLayout(layout)
        return group

    @handle_exceptions(logger)
    def _init_undo_redo_manager(self):
        """Initialize UndoRedoManager with current configuration."""
        self.undo_redo_manager = UndoRedoManager(
            source_dirs=self.source_dirs or [],
            dest_dirs=self.dest_dirs or [],
            base_dest_paths=self.dest_dirs or [],
            preserve_structure_enum=PreserveStructureType.NONE,
            processing_mode=self.processing_mode
        )

    @handle_exceptions(logger)
    def _setup_undo_redo_connections(self):
        """Connect manager signals to UI updates."""
        if self.undo_redo_manager:
            self.undo_redo_manager.operation_completed.connect(
                self._on_undo_redo_completed
            )

    @handle_exceptions(logger)
    def _on_undo_redo_completed(self, result: ProcessingThreadCopyMoveResult = None, checked: bool = False):
        """Handle completion of undo/redo operation."""
        try:
            self._update_undo_redo_button_states()
            self.status_label.setText("Undo/Redo operation completed.")
            StyledDialog(
                "Success",
                "Operation reversed successfully.",
                dialog_type="info",
                buttons=[(StyledDialog.OK, "OK")],
                default_button=StyledDialog.OK,
                parent=self,
                scrollable=True,
                min_width=500,
                min_height=200
            ).exec_()

        except Exception as e:
            logger.error(f"Error updating UI after undo/redo: {str(e)}", exc_info=True)
            StyledDialog(
                "UI Update Error",
                f"Operation completed but UI update failed: {str(e)}",
                dialog_type="warning",
                buttons=[(StyledDialog.OK, "OK")],
                default_button=StyledDialog.OK,
                parent=self,
                scrollable=True,
                min_width=500,
                min_height=200
            ).exec_()

    @handle_exceptions(logger)
    def _setup_status_bar(self):
        """Setup status bar with undo/redo controls and log file path."""
        statusbar = self.statusBar()

        # Apply status bar styling to create beveled/sunken appearance
        statusbar.setStyleSheet("""
            QStatusBar {
                border: 1px solid #999;
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #f0f0f0, stop:1 #e0e0e0);
            }
            QStatusBar::item {
                border: none;
            }
        """)

        # Create a horizontal layout for status bar widgets
        status_layout = QHBoxLayout()
        status_layout.setContentsMargins(5, 2, 5, 2)
        status_layout.setSpacing(10)

        # Undo Button
        self.btn_undo = QPushButton("Undo")
        self.btn_undo.setToolTip("Undo last operation")
        self.btn_undo.setMaximumWidth(100)
        self.btn_undo.setMinimumHeight(24)
        self.btn_undo.setStyleSheet("""
            QPushButton {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #e8e8e8, stop:1 #d0d0d0);
                border: 1px solid #999;
                border-radius: 3px;
                padding: 4px 8px;
            }
            QPushButton:hover {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #f0f0f0, stop:1 #d8d8d8);
            }
            QPushButton:pressed {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #d0d0d0, stop:1 #e8e8e8);
                border: 1px inset #666;
            }
        """)
        self.btn_undo.clicked.connect(self._on_undo_clicked)
        status_layout.addWidget(self.btn_undo)

        # Redo Button
        self.btn_redo = QPushButton("Redo")
        self.btn_redo.setToolTip("Redo last undone operation")
        self.btn_redo.setMaximumWidth(100)
        self.btn_redo.setMinimumHeight(24)
        self.btn_redo.setStyleSheet("""
            QPushButton {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #e8e8e8, stop:1 #d0d0d0);
                border: 1px solid #999;
                border-radius: 3px;
                padding: 4px 8px;
            }
            QPushButton:hover {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #f0f0f0, stop:1 #d8d8d8);
            }
            QPushButton:pressed {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #d0d0d0, stop:1 #e8e8e8);
                border: 1px inset #777;
            }
        """)
        self.btn_redo.clicked.connect(self._on_redo_clicked)
        status_layout.addWidget(self.btn_redo)

        self._update_undo_redo_button_states()

        # Status message
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("""
            QLabel {
                color: #333;
                padding: 0px 4px;
            }
        """)
        status_layout.addWidget(self.status_label)

        # Stretch to push log path to the right
        status_layout.addStretch()

        # Log file path (clickable label or read-only text)
        self.log_path_label = QLabel()
        self.log_path_label.setText(f"Log: {self._get_log_file_path()}")
        self.log_path_label.setToolTip("Click to open log file")
        self.log_path_label.setCursor(Qt.PointingHandCursor)
        self.log_path_label.setStyleSheet("""
            QLabel {
                color: #0066cc;
                padding: 0px 4px;
                text-decoration: underline;
            }
            QLabel:hover {
                color: #0044aa;
            }
        """)
        self.log_path_label.mousePressEvent = lambda event: self._open_log_file()
        status_layout.addWidget(self.log_path_label)

        # Create a container widget and set the layout on the status bar
        container = QWidget()
        container.setLayout(status_layout)
        statusbar.addWidget(container, 1)  # Stretch factor 1 to fill available space

        logger.info("Status bar initialized with undo, redo and log file controls")

    @handle_exceptions(logger)
    def _get_log_file_path(self):
        """Retrieve the log file path from the root logger's file handler."""
        root_logger = logging.getLogger()
        for handler in root_logger.handlers:
            if isinstance(handler, logging.handlers.RotatingFileHandler):
                return handler.baseFilename
        return "No log file found"
    
    @handle_exceptions(logger)
    def _open_log_file(self):
        """Open the log file in the default system viewer."""
        log_path = self._get_log_file_path()

        if not os.path.exists(log_path):
            self.status_label.setText(f"Log file not found: {log_path}")
            logger.warning(f"Attempted to open non-existent log: {log_path}")
            return

        try:
            os.startfile(log_path)
            self.status_label.setText(f"Opened log: {os.path.basename(log_path)}")
            logger.info(f"Log file opened: {log_path}")
        except Exception as e:
            logger.error(f"Failed to open log file: {e}", exc_info=True)
            self.status_label.setText(f"Failed to open log file")

    @handle_exceptions(logger)
    def _on_undo_clicked(self, checked: bool = False):
        """Handle undo button click."""
        if not self.undo_redo_manager:
            StyledDialog(
                "Error",
                "Undo/Redo manager not initialized.",
                dialog_type="error",
                buttons=[(StyledDialog.OK, "OK")],
                default_button=StyledDialog.OK,
                parent=self,
                scrollable=True,
                min_width=500,
                min_height=200
            ).exec_()
            return

        if not self.undo_redo_manager.can_undo():
            StyledDialog(
                "Info",
                "Nothing to undo.",
                dialog_type="info",
                buttons=[(StyledDialog.OK, "OK")],
                default_button=StyledDialog.OK,
                parent=self,
                scrollable=True,
                min_width=500,
                min_height=200
            ).exec_()
            return

        # Show what will be undone
        description = self.undo_redo_manager.get_undo_description()
        reply = StyledDialog(
            "Confirm Undo",
            f"Are you sure you want to undo?\n{description}",
            dialog_type="info",
            buttons=[(StyledDialog.YES, "Yes"), (StyledDialog.NO, "No")],
            default_button=StyledDialog.YES,
            parent=self,
            scrollable=True,
            min_width=500,
            min_height=200
        ).exec_()

        if reply == StyledDialog.YES:
            self.status_label.setText("Undoing operation...")
            self.undo_redo_manager.undo()

    @handle_exceptions(logger)
    def _on_redo_clicked(self, checked: bool = False):
        """Handle redo button click."""
        if not self.undo_redo_manager:
            StyledDialog(
                "Error",
                "Undo/Redo manager not initialized.",
                dialog_type="error",
                buttons=[(StyledDialog.OK, "OK")],
                default_button=StyledDialog.OK,
                parent=self,
                scrollable=True,
                min_width=500,
                min_height=200
            ).exec_()
            return

        if not self.undo_redo_manager.can_redo():
            StyledDialog(
                "Info",
                "Nothing to redo.",
                dialog_type="info",
                buttons=[(StyledDialog.OK, "OK")],
                default_button=StyledDialog.OK,
                parent=self,
                scrollable=True,
                min_width=500,
                min_height=200
            ).exec_()
            return

        # Show what will be redone
        description = self.undo_redo_manager.get_redo_description()
        reply = StyledDialog(
            "Confirm Redo",
            f"Are you sure you want to redo?\n{description}",
            dialog_type="warning",
            buttons=[(StyledDialog.YES, "Yes"), (StyledDialog.NO, "No")],
            default_button=StyledDialog.YES,
            parent=self,
            scrollable=True,
            min_width=500,
            min_height=200
        ).exec_()

        if reply == StyledDialog.YES:
            self.status_label.setText("Redoing operation...")
            self.undo_redo_manager.redo()

    @handle_exceptions(logger)
    def _update_undo_redo_button_states(self):
        """Update undo/redo button enabled states and tooltips."""
        if not self.undo_redo_manager:
            return

        # Update undo button
        can_undo = self.undo_redo_manager.can_undo()
        self.btn_undo.setEnabled(can_undo)
        if can_undo:
            undo_desc = self.undo_redo_manager.get_undo_description()
            self.btn_undo.setToolTip(f"Undo: {undo_desc}")
        else:
            self.btn_undo.setToolTip("Nothing to undo")

        # Update redo button
        can_redo = self.undo_redo_manager.can_redo()
        self.btn_redo.setEnabled(can_redo)
        if can_redo:
            redo_desc = self.undo_redo_manager.get_redo_description()
            self.btn_redo.setToolTip(f"Redo: {redo_desc}")
        else:
            self.btn_redo.setToolTip("Nothing to redo")

    # Slot methods for UI interactions
    @handle_exceptions(logger)
    def _on_mode_changed(self, checked: bool):
        """Handle processing mode change.

         Args:
             checked: Whether the radio button is now checked (True/False)
        """
        mode_id = self.mode_group.checkedId()
        self.processing_mode = ProcessingMode.PARALLEL if mode_id == 1 else ProcessingMode.SEQUENTIAL
        mode_name = self.processing_mode.value.upper()
        self.status_label.setText(f"Mode changed to: {mode_name}")

    @handle_exceptions(logger)
    def _clear_layout(self, layout: QLayout):
        """Recursively clear all widgets and sub-layouts."""
        while layout.count():
            item = layout.takeAt(0)

            # If it's a widget, delete it
            if item.widget():
                widget = item.widget()
                widget.setParent(None) # Remove from parent immediately
                widget.deleteLater()

            # If it's a layout, recursively clear it first, then delete
            elif item.layout():
                self._clear_layout(item.layout())
                layout_to_delete = item.layout()
                layout_to_delete.setParent(None) # Remove parent reference
                layout_to_delete.deleteLater()

    @handle_exceptions(logger)
    def _normalize_path(self, path: str) -> str:
        """
        Normalize a path to OS standards

        Args:
            :param path: Raw path string from user input

        Returns:
            Normalized path with OS standards
        """
        if not path:
            return  path
        return os.path.normpath(path)

    @handle_exceptions(logger)
    def _on_create_sources(self, checked: bool = None):
        """Create dynamic source path input fields.

        Args:
            checked: Button checked state (passed automatically by clicked signal, ignored)
        """

        # Disconnect all previous browse buttons before clearing
        if hasattr(self, 'source_browse_buttons'):
            for btn in self.source_browse_buttons:
                try:
                    btn.clicked.disconnect()
                except:
                    pass

        # Use the helper method
        self._clear_layout(self.sources_layout)

        num_sources = self.source_count_spinbox.value()
        self.source_dirs = [None] * num_sources  # Pre-allocate with None
        self.source_widgets = []
        self.source_browse_buttons = []  # Track buttons for cleanup

        for i in range(num_sources):
            source_layout = QHBoxLayout()

            label = QLabel(f"Source {i + 1}:")
            source_layout.addWidget(label)

            path_input = QLineEdit()
            path_input.setPlaceholderText(f"Enter path to Source {i + 1}")
            path_input.setReadOnly(True)
            path_input.setStyleSheet("QLineEdit {background-color: #f0f0f0; color: #555;}")
            source_layout.addWidget(path_input)

            btn_browse = QPushButton("Browse...")
            self.source_browse_buttons.append(btn_browse)
            btn_browse.clicked.connect(
                lambda checked, idx=i: self._browse_directory(f"source_{idx}")
            )
            source_layout.addWidget(btn_browse)

            self.sources_layout.addLayout(source_layout)
            self.source_widgets.append(path_input)

        self.sources_layout.addStretch()

    @handle_exceptions(logger)
    def _on_create_destinations(self, checked: bool = None):
        """Create dynamic destination path input fields.

        Args:
            checked: Button checked state (passed automatically by clicked signal, ignored)
        """

        # Disconnect all previous browse buttons before clearing
        if hasattr(self, 'dest_browse_buttons'):
            for btn in self.dest_browse_buttons:
                try:
                    btn.clicked.disconnect()
                except:
                    pass

        self._clear_layout(self.destinations_layout)

        num_destinations = self.dest_count_spinbox.value()
        self.dest_dirs = [None] * num_destinations  # Pre-allocate with None
        self.destination_widgets = []
        self.dest_browse_buttons = []  # Track buttons for cleanup

        for i in range(num_destinations):
            dest_layout = QHBoxLayout()

            label = QLabel(f"Destination {i + 1}:")
            dest_layout.addWidget(label)

            path_input = QLineEdit()
            path_input.setPlaceholderText(f"Enter path to Destination {i + 1}")
            path_input.setReadOnly(True)
            path_input.setStyleSheet("QLineEdit {background-color: #f0f0f0; color: #555;}")
            dest_layout.addWidget(path_input)

            btn_browse = QPushButton("Browse...")
            self.dest_browse_buttons.append(btn_browse)
            btn_browse.clicked.connect(
                lambda checked, idx=i: self._browse_directory(f"dest_{idx}")
            )
            dest_layout.addWidget(btn_browse)

            self.destinations_layout.addLayout(dest_layout)
            self.destination_widgets.append(path_input)

        self.destinations_layout.addStretch()

    @handle_exceptions(logger)
    def _browse_directory(self, field_type: str):
        """Open custom directory picker dialog on main thread."""
        logger.info(f"Directory browser opened for field: {field_type}")

        dialog = DirectoryPickerDialog(
            title=f"Select {field_type} Directory",
            initial_path=str(Path.home()),
            parent=self
        )

        result = dialog.exec_()

        if result == StyledDialog.OK:
            directory = dialog.get_directory()
            if directory:
                logger.info(f"Directory selected: {directory}")
                self._set_directory_path(field_type, directory)
            else:
                logger.warning("No directory selected")
        else:
            logger.info("Directory selection cancelled")

    @handle_exceptions(logger)
    def _set_directory_path(self, field_type: str, directory: str) -> bool:
        """Set the directory path in the appropriate widget."""
        if directory:
            normalized_path = self._normalize_path(directory)
            if normalized_path in self.source_dirs or normalized_path in self.dest_dirs:
                logger.warning(f"Path already in use: {normalized_path}")
                StyledDialog(
                    "Error",
                    f"Path already in use: {normalized_path}",
                    dialog_type="error",
                    buttons=[(StyledDialog.OK, "OK")],
                    default_button=StyledDialog.OK,
                    parent=self,
                    scrollable=True,
                    min_width=500,
                    min_height=200
                ).exec_()
                return False

            if field_type.startswith("source_"):
                idx = int(field_type.split("_")[1])
                if idx < len(self.source_widgets):
                    self.source_widgets[idx].setText(normalized_path)
                    self.source_dirs[idx] = normalized_path
                    logger.info(f"Source {idx} set to: {normalized_path}")
                    return True

            elif field_type.startswith("dest_"):
                idx = int(field_type.split("_")[1])
                if idx < len(self.destination_widgets):
                    self.destination_widgets[idx].setText(normalized_path)
                    self.dest_dirs[idx] = normalized_path
                    logger.info(f"Destination {idx} set to: {normalized_path}")
                    return True

        return False

    @handle_exceptions(logger)
    def _on_start_processing(self, checked: bool = None):
        """Start file processing in background thread."""
        # Validate inputs
        if not self.source_dirs:
            StyledDialog(
                "Validation Error",
                "Please configure at least one source.",
                dialog_type="error",
                buttons=[(StyledDialog.OK, "OK")],
                default_button=StyledDialog.OK,
                parent=self,
                scrollable=True,
                min_width=500,
                min_height=200
            ).exec_()
            return

        if not self.dest_dirs:
            StyledDialog(
                "Validation Error",
                "Please configure at least one destination.",
                dialog_type="error",
                buttons=[(StyledDialog.OK, "OK")],
                default_button=StyledDialog.OK,
                parent=self,
                scrollable=True,
                min_width=500,
                min_height=200
            ).exec_()
            return

        # Get correct sources for file manager
        for source_dir in self.source_dirs:
            if not os.path.isdir(source_dir):
                StyledDialog(
                    "Error",
                    f"Invalid source directory: {source_dir}",
                    dialog_type="error",
                    buttons=[(StyledDialog.OK, "OK")],
                    default_button=StyledDialog.OK,
                    parent=self,
                    scrollable=True,
                    min_width=500,
                    min_height=200
                ).exec_()
                return

        # Create and start processing thread
        num_workers = 2 if self.processing_mode == ProcessingMode.PARALLEL else 1

        # Clear history at undo_redo_manager
        if self.undo_redo_manager:
            self.undo_redo_manager.clear_history()
            self._update_undo_redo_button_states()

        if not self._should_copy_move:
            active_filters = self._get_active_filters()

            if active_filters:
                filter_summary = ", ".join([f['plugin_key'] for f in active_filters])
                logger.debug(f"Applying filters: {filter_summary}")

            self.processing_thread = ProcessingThread(
                processing_mode=self.processing_mode,
                source_dirs=self.source_dirs,
                num_workers=num_workers,
                plugin_manager=self.plugin_manager,
                active_filters=active_filters
            )
        else:

            # Determine operation mode from radio buttons
            is_copy_mode = self.rb_copy_mode.isChecked()
            is_move_mode = self.rb_move_mode.isChecked()
            preserve_structure = self._get_preserve_structure_option()

            # Validate that one mode is selected
            if not is_copy_mode and not is_move_mode:
                StyledDialog(
                    "Validation Error",
                    "Please select either Copy or Move mode.",
                    dialog_type="warning",
                    buttons=[(StyledDialog.OK, "OK")],
                    default_button=StyledDialog.OK,
                    parent=self,
                    scrollable=True,
                    min_width=500,
                    min_height=200
                ).exec_()
                return

            selected_files_dict = self._get_selected_files_from_table()
            if len(selected_files_dict.items()) == 0:
                StyledDialog(
                    "Validation Error",
                    "Please select some rows at the table.",
                    dialog_type="warning",
                    buttons=[(StyledDialog.OK, "OK")],
                    default_button=StyledDialog.OK,
                    parent=self,
                    scrollable=True,
                    min_width=500,
                    min_height=200
                ).exec_()
                return

            if is_copy_mode or is_move_mode:
                operation = FileOperation.COPY if is_copy_mode else FileOperation.MOVE

                logger.debug(f"Before ProcessingThreadCopyMove self.dest_dirs is {self.dest_dirs}")
                self.undo_redo_manager.base_dest_paths = self.dest_dirs

                self.processing_thread = ProcessingThreadCopyMove(
                    self.processing_mode,
                    selected_files_dict,
                    self.dest_dirs,
                    operation,
                    num_workers,
                    preserve_structure
                )

        # Not Allow Copy/Move/Preserve Sources Structure
        self._set_copy_move_preserve_structure_on_off(flag=False)

        self.processing_thread.progress_updated.connect(self._on_progress_update)
        if not self._should_copy_move:  # Filling data the comparison_table on Discovery phase
            self.processing_thread.comparison_table_final_results.connect(
                self._on_comparison_table_final_results
            )

        self.processing_thread.processing_finished.connect(self._on_processing_finished)
        self.processing_thread.start()

        # Update UI
        self.btn_start.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_stop.setEnabled(True)
        self.status_label.setText("Processing started...")

    @property
    @handle_exceptions(logger)
    def _should_copy_move(self) -> bool:
        return self.comparison_model.rowCount() > 0

    @handle_exceptions(logger)
    def _get_preserve_structure_option(self) -> PreserveStructureStrategy:
        """Map selected radio button to strategy instance."""
        checked_id = self.preserve_structure_group.checkedId()

        strategies = {
            0: PSSourceParentRest(),
            1: PSFullSource(),
            -1: PSNone()
        }

        return strategies.get(checked_id, PSNone())

    @handle_exceptions(logger)
    def _get_selected_files_from_table(self, checked: bool = False) -> Dict:
        """
        Extract selected rows from comparison table into the same format.

        Returns:
            {(file_name, file_size, file_hash): frozenset(paths), ...}
        """
        selected_indexes = self.comparison_table.selectionModel().selectedRows()
        selected_rows = [idx.row() for idx in selected_indexes]

        return self.comparison_model.get_selected_files(selected_rows, value_type=0)

    @handle_exceptions(logger)
    def _set_copy_move_preserve_structure_on_off(self, flag: bool = True):
        if (self.rb_copy_mode and self.rb_move_mode and
            self.rb_preserve_parent_rest and self.rb_preserve_full_source and
            self.rb_preserve_none and self.comparison_table and self.btn_select_all and
            self.btn_deselect_all and self.btn_clear_table
        ):
            self.rb_copy_mode.setEnabled(flag)
            self.rb_move_mode.setEnabled(flag)
            self.rb_preserve_parent_rest.setEnabled(flag)
            self.rb_preserve_full_source.setEnabled(flag)
            self.rb_preserve_none.setEnabled(flag)
            self.comparison_table.setEnabled(flag)
            self.btn_select_all.setEnabled(flag)
            self.btn_deselect_all.setEnabled(flag)
            self.btn_clear_table.setEnabled(flag)

    @handle_exceptions(logger)
    def _on_pause_processing(self, checked: bool = None):
        """Pause processing."""
        if self.processing_thread:
            self.processing_thread.pause()
            self.btn_pause.setEnabled(False)
            self.btn_resume.setEnabled(True)
            self.status_label.setText("Processing paused.")

    @handle_exceptions(logger)
    def _on_resume_processing(self, checked: bool = None):
        """Resume paused processing."""
        if self.processing_thread:
            self.processing_thread.resume()
            self.btn_resume.setEnabled(False)
            self.btn_pause.setEnabled(True)
            self.status_label.setText("Processing resumed.")

    @handle_exceptions(logger)
    def _on_stop_processing(self, checked: bool = None):
        """Stop processing."""
        if self.processing_thread:
            self.processing_thread.stop()
            self.processing_thread.wait()
            self.processing_thread = None

        # Update UI
        self.btn_start.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_resume.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.progress_bar.setValue(0)
        self.status_label.setText("Processing stopped.")

    @handle_exceptions(logger)
    def _on_progress_update(self, progress: int, message: str):
        """Handle progress updates from processing thread."""
        self.progress_bar.setValue(progress)
        self.status_label.setText(message)

    @handle_exceptions(logger)
    def _on_processing_finished(self, result: ProcessingThreadResultAbstract):
        """
        Handle completion of any processing operation (copy, move, discovery).

        Args:
            result: ProcessingThreadResultAbstract or subclass instance
            (ProcessingThreadResult or ProcessingThreadCopyMoveResult)
        """
        # Extract operation type and general fields
        operation_type = result.operation_type.value
        total_processed = result.total_processed
        total_size = result.total_size
        duration = result.duration
        operation_start = result.operation_start

        # Determine operation category
        is_discovery = isinstance(result, ProcessingThreadResult) and not isinstance(
            result, ProcessingThreadCopyMoveResult
        )
        is_copy_or_move = isinstance(result, ProcessingThreadCopyMoveResult)

        # Update UI
        self.btn_start.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_resume.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self._set_copy_move_preserve_structure_on_off(flag=True)
        self.progress_bar.setValue(100)
        self.status_label.setText(f"{operation_type.capitalize()} complete.")

        # Build message with operation-specific information
        message_parts = [
            f"Processing complete!",
            f"Operation: {operation_type.upper()}",
            f"Files processed: {total_processed}"
        ]

        # Add operation-specific fields
        if is_copy_or_move:
            total_skipped = result.total_skipped
            message_parts.append(f"Files skipped: {total_skipped}")

        message_parts.extend([
            f"Total size: {total_size / (1024 ** 2):.2f} MB",
            f"Duration: {duration:.2f}s",
            f"Operation start: {operation_start}"
        ])

        # Add renamed files info (only for copy/move)
        if is_copy_or_move and result.renamed_files:
            truncated_info = result.get_truncated_files(max_items=10)
            is_truncated = truncated_info.get("is_truncated", False)
            truncation_note = " (truncated)" if is_truncated else ""
            message_parts.append(f"\nRenamed files{truncation_note}:")
            for file in truncated_info["files"]:
                message_parts.append(f"  {file}")

        # Add discovered files info (only for discovery)
        if is_discovery and result.discovered_files:
            truncated_info = result.get_truncated_files(max_items=10)
            is_truncated = truncated_info.get("is_truncated", False)
            truncation_note = " (truncated)" if is_truncated else ""
            message_parts.append(f"\nDiscovered files{truncation_note}:")
            for file in truncated_info["files"]:
                message_parts.append(f"  {file}")

        # Add errors info (common to all operations)
        if result.errors:
            truncated_errors = result.get_truncated_errors(max_items=10)
            is_truncated = truncated_errors.get("is_truncated", False)
            truncation_note = " (truncated)" if is_truncated else ""
            message_parts.append(f"\nErrors{truncation_note}:")
            for error in truncated_errors["files"]:
                message_parts.append(f"  ✗ {error}")

        message = "\n".join(message_parts)

        # Show appropriate dialog
        if result.errors:
            StyledDialog(
                "Processing Complete with Errors",
                message,
                dialog_type="warning",
                buttons=[(StyledDialog.OK, "OK")],
                default_button=StyledDialog.OK,
                parent=self
            ).exec_()
        else:
            StyledDialog(
                "Success",
                message,
                dialog_type="success",
                buttons=[(StyledDialog.OK, "OK")],
                default_button=StyledDialog.OK,
                parent=self
            ).exec_()

        # Record operation with UndoRedoManager (only for copy/move)
        if is_copy_or_move:
            try:
                reply = StyledDialog(
                    "Undo/Redo operation",
                    "Would you like to have the Undo/Redo possibility for the executed operation?",
                    dialog_type="info",
                    buttons=[
                        (StyledDialog.YES, "Yes"),
                        (StyledDialog.NO, "No")
                    ],
                    default_button=StyledDialog.YES,
                    parent=self,
                    scrollable=True,
                    min_width=500,
                    min_height=200
                ).exec_()

                if reply == StyledDialog.YES:
                    operation_id = self.undo_redo_manager.record_operation(
                        processing_result=result
                    )
                    logger.info(f"Operation recorded with ID: {operation_id}")
                    self._update_undo_redo_button_states()
            except Exception as e:
                logger.error(f"Failed to record operation in undo/redo manager: {str(e)}", exc_info=True)
                StyledDialog(
                    "Undo/Redo Error",
                    f"Operation completed but could not be recorded for undo/redo: {str(e)}",
                    dialog_type="warning",
                    buttons=[(StyledDialog.OK, "OK")],
                    default_button=StyledDialog.OK,
                    parent=self,
                    scrollable=True,
                    min_width=500,
                    min_height=200
                ).exec_()

    @handle_exceptions(logger)
    def _on_comparison_table_final_results(self, final_results: dict):
        """
        Clear table and populate with final deduplicated results.
        Called once at the end of discovery.

        Args:
            final_results: Dict of {(file_name, file_size, file_hash): [paths], ...}
        """
        if not final_results:
            logger.info("No files to display in comparison_table")
            return

        # Populate model
        self.comparison_model.set_files(final_results)

        # Sort by file_name by default
        self.comparison_table.sortByColumn(0, Qt.AscendingOrder)

        logger.info(f"Comparison table populated: {len(final_results)} rows")

    @handle_exceptions(logger)
    def _cleanup_resources(self):
        """Clean up all resources before closing."""
        if self.processing_thread:
            if self.processing_thread.isRunning():
                logger.info("Stopping processing thread...")
                self.processing_thread.stop()

                # Waiting up to 3 seconds for graceful shutdown
                if not self.processing_thread.wait(3000):
                    logger.warning("Processing thread did not stop gracefully, terminating...")
                    self.processing_thread.terminate()
                    if not self.processing_thread.wait(1000):
                        logger.error("Processing thread failed to terminate")

            logger.debug("Processing thread cleanup complete")

    def closeEvent(self, event):
        """Handle application close event."""
        import gc

        logger.info("=== CLOSE EVENT START ===")
        logger.info(f"Active threads: {[t.name for t in threading.enumerate()]}")

        try:
            # Stop ResourceMonitor ONLY
            if hasattr(self, 'resource_monitor') and self.resource_monitor:
                if self.resource_monitor.is_running():
                    logger.info("Stopping ResourceMonitor...")
                    self.resource_monitor.stop()
                    self.resource_monitor = None

            # Calling the decorated cleanup method
            self._cleanup_resources()

            logger.info(f"Active threads after cleanup: {[t.name for t in threading.enumerate()]}")

        except Exception as e:
            logger.error(f"Error in closeEvent: {e}", exc_info=True)

        finally:
            # Force garbage collection before exit
            gc.collect()

            # Explicitly disconnect all tracked buttons
            for btn_list in [getattr(self, 'source_browse_buttons', []),
                            getattr(self, 'dest_browse_buttons', [])]:
                for btn in btn_list:
                    try:
                        btn.clicked.disconnect()
                        btn.deleteLater()
                    except:
                        pass

            self._clear_layout(self.sources_layout)
            self._clear_layout(self.destinations_layout)

            # Add stretch back for empty state
            self.sources_layout.addStretch()
            self.destinations_layout.addStretch()

            self._on_clear_comparison_table()

            # Give Qt time to process pending events
            QApplication.processEvents()

            # Disconnect all signals to prevent stray callbacks
            try:
                self.disconnect()
            except:
                pass

            # Delete child widgets explicitly
            for widget in self.findChildren(QWidget):
                try:
                    widget.deleteLater()
                except:
                    pass

            # Accept and close, even if cleanup fails
            event.accept()
            logger.info("=== CLOSE EVENT END ===")

            # Schedule application quit (not just window close)
            QApplication.instance().quit()

    @handle_exceptions(logger)
    def _on_metrics_update(self, metrics: ResourceMetrics):
        """Handle resource monitor updates from ResourceMonitor.

        Args:
            metrics: ResourceMetrics object containing CPU, memory, and process info
        """

        # Update CPU labels
        self.cpu_label.setText(f"{metrics.cpu_percent:.1f}%")
        self.process_cpu_label.setText(f"{metrics.process_cpu_percent:.1f}%")

        # Update Memory labels
        self.memory_label.setText(f"{metrics.memory_percent:.1f}%")
        self.process_memory_label.setText(f"{metrics.process_memory_mb:.1f} MB")

        # Update progress bars
        self.cpu_progress.setValue(int(metrics.cpu_percent))
        self.memory_progress.setValue(int(metrics.memory_percent))
        self.process_cpu_progress.setValue(int(metrics.process_cpu_percent))

if __name__ == "__main__":
    # import faulthandler
    # faulthandler.enable(file=open("C:\\fault.log", "w"))
    try:
        # logger = logging.getLogger(__name__)
        # logger.info("Application starting")

        app = QApplication(sys.argv)
        window = UniSyncMainWindow()
        window.showMaximized()
        sys.exit(app.exec_())
    except Exception as e:
        logger.error(
            f"Error in if __name__== '__main__': {type(e).__name__}: {str(e)}",
            exc_info=True  # Includes full traceback
        )
