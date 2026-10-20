from enums import DialogType
import logging
import os
import pyqt5_loader
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QScrollArea, QTextEdit, QWidget
)
from PyQt5.QtGui import QPixmap
from PyQt5.QtCore import Qt

logger = logging.getLogger(__name__)

class StyledDialog(QDialog):
    """A custom dialog with improved visuals and type-specific styling and scrollable content."""

    # Dialog result constants (like QMessageBox)
    YES = 1
    NO = 2
    OK = 3
    CANCEL = 4
    SAVE = 5
    DISCARD = 6

    # Style configuration for each dialog type
    STYLES = {
        DialogType.INFO: {
            "bg_color": "#E3F2FD",
            "border_color": "#2196F3",
            "text_color": "#1565C0",
            "icon_color": "#2196F3"
        },
        DialogType.WARNING: {
            "bg_color": "#FFF3E0",
            "border_color": "#FF9800",
            "text_color": "#E65100",
            "icon_color": "#FF9800"
        },
        DialogType.ERROR: {
            "bg_color": "#FFEBEE",
            "border_color": "#F44336",
            "text_color": "#C62828",
            "icon_color": "#F44336"
        },
        DialogType.SUCCESS: {
            "bg_color": "#E8F5E9",
            "border_color": "#4CAF50",
            "text_color": "#2E7D32",
            "icon_color": "#4CAF50"
        }
    }

    def __init__(
            self, title: str, message: str, dialog_type: str = "info",
            buttons: list = None, default_button: int = None, parent=None,
            scrollable: bool = True, min_width: int = 500, min_height: int = 300
    ):
        """
        Initialize a styled dialog.

        Args:
            title: Window title
            message: Message text
            dialog_type: "info", "warning", "error" or "success"
            buttons: List of tuples [(button_id, button_text), ...] or use present constants
                if None, defaults to [OK]
            default_button: Which button should be focused by default (button_id)
            parent: Parent widget
            scrollable: Enable scrollable content for large messages (auto-enabled if message > 500 chars)
            min_width: Minimum dialog width (pixels)
            min_height: Minimum dialog height (pixels)
        """
        super().__init__(parent)

        # Validate and set dialog type
        try:
            self.dialog_type = DialogType(dialog_type.lower())
        except ValueError:
            self.dialog_type = DialogType.INFO

        # Set default buttons if not provided
        if buttons is None:
            buttons = [(self.OK, "OK")]

        self.buttons = buttons
        self.default_button = default_button
        self._result = None
        self._button_widgets = {}
        self.content_layout = None

        # Auto-enable scrolling for large messages
        self.scrollable = scrollable and len(message) > 500
        self.min_width = min_width
        self.min_height = min_height

        self._setup_ui(title, message)
        self._apply_styling()

        # Set focus to default button
        if self.default_button and self.default_button in self._button_widgets:
            self._button_widgets[self.default_button].setFocus()

    def _setup_ui(self, title: str, message: str):
        """Set up the user interface."""
        self.setWindowTitle(title)
        self.setGeometry(100, 100, self.min_width, self.min_height)
        self.setMinimumWidth(self.min_width)
        self.setMinimumHeight(self.min_height)

        screen_geometry = self.screen().geometry()
        window_rect = self.frameGeometry()
        window_rect.moveCenter(screen_geometry.center())
        self.move(window_rect.topLeft())

        # Main layout
        main_layout = QVBoxLayout()
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(20, 20, 20, 20)

        # Content frame (icon + message)
        content_frame = QFrame()
        self.content_layout = QHBoxLayout()
        self.content_layout.setSpacing(15)

        # Icon
        icon_label = QLabel()
        icon_pixmap = self._get_icon_pixmap()
        icon_label.setPixmap(icon_pixmap)
        icon_label.setFixedSize(48, 48)
        icon_label.setAlignment(Qt.AlignTop)
        self.content_layout.addWidget(icon_label)

        # Message area (scrollable or simple label)
        if self.scrollable:
            message_widget = self._create_scrollable_text_area(message)
        else:
            message_widget = self._create_simple_message_label(message)

        self.content_layout.addWidget(message_widget, 1)
        content_frame.setLayout(self.content_layout)
        main_layout.addWidget(content_frame, 1)

        # Button layout
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        # Create buttons dynamically
        for button_id, button_text in self.buttons:
            btn = QPushButton(button_text)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumWidth(100)
            btn.setMinimumHeight(36)

            # Create callback that captures button_id
            btn.clicked.connect(lambda checked=False, bid=button_id: self._on_button_clicked(bid))

            button_layout.addWidget(btn)
            self._button_widgets[button_id] = btn

        main_layout.addLayout(button_layout)
        self.setLayout(main_layout)

    def _create_simple_message_label(self, message: str) -> QLabel:
        """Create a simple non-scrollable message label for short messages."""
        message_label = QLabel(message)
        message_label.setWordWrap(True)
        message_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        message_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        # Style the message text
        font = message_label.font()
        font.setPointSize(10)
        message_label.setFont(font)

        return message_label

    def _create_scrollable_text_area(self, message: str) -> QWidget:
        """Create a scrollable text area for large messages."""
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("""
            QScrollArea {
                border: 1px solid rgba(0, 0, 0, 0.1);
                border-radius: 4px;
                background-color: rgba(255, 255, 255, 0.5);
            }
            QScrollBar:vertical {
                border: none;
                background: #f0f0f0;
                width: 12px;
                border-radius: 6px;
                margin: 0px 0px 0px 0px;
            }
            QScrollBar::handle:vertical {
                background: #999;
                border-radius: 6px;
                min-height: 20px;
                margin: 2px 2px 2px 2px;
            }
            QScrollBar::handle:vertical:hover {
                background: #777;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                border: none;
                background: none;
            }
        """)

        text_edit = QTextEdit()
        text_edit.setPlainText(message)
        text_edit.setReadOnly(True)
        text_edit.setWordWrapMode(True)
        text_edit.setStyleSheet("""
            QTextEdit {
                border: none;
                background-color: transparent;
                padding: 10px;
                font-family: 'Courier New', 'Monaco', monospace;
                font-size: 10pt;
                line-height: 1.4; 
            }
        """)

        scroll_area.setWidget(text_edit)
        return scroll_area

    def _on_button_clicked(self, button_id: int):
        """Handle button click and set result."""
        self._result = button_id
        self.done(button_id)

    def _get_icon_pixmap(self) -> QPixmap:
        """Get the appropriate icon for the dialog type."""
        try:
            # Get the directory where this script is located
            script_dir = os.path.dirname(os.path.abspath(__file__))

            icon_paths = {
                DialogType.INFO: os.path.join(script_dir, "assets", "icon_info.png"),
                DialogType.WARNING: os.path.join(script_dir, "assets", "icon_warning.png"),
                DialogType.ERROR: os.path.join(script_dir, "assets", "icon_error.png"),
                DialogType.SUCCESS: os.path.join(script_dir, "assets", "icon_success.png")
            }

            path = icon_paths.get(self.dialog_type)
            logger.debug(f"Looking for icon at: {path}")
            logger.debug(f"Path exists: {os.path.exists(path)}")

            if path and os.path.exists(path):
                pixmap = QPixmap(path)

                # Debug: logging the original size
                logger.debug(f"Loaded {path}: original size {pixmap.width()}x{pixmap.height()}")

                # Only scale if necessary and maintain aspect ration
                if pixmap.width() < 48 or pixmap.height() < 48:
                    # Scale Up if too small
                    pixmap = pixmap.scaledToWidth(48, Qt.SmoothTransformation)
                elif pixmap.width() > 96 or pixmap.height() > 96:
                    # Scale down of too large
                    pixmap = pixmap.scaledToWidth(96, Qt.SmoothTransformation)

                logger.debug(f"Scaled to: {pixmap.width()}x{pixmap.height()}")
                return pixmap

        except Exception as e:
            logger.error(f"Error loading icon: {e}")

        # Fallback: return empty pixmap
        return QPixmap(48, 48)

    def _apply_styling(self):
        """Apply type-specific styling to the dialog."""
        style_config = self.STYLES[self.dialog_type]

        # Main dialog styling
        stylesheet = f"""
            QDialog {{
                background-color: {style_config['bg_color']};
                border: 2px solid {style_config['border_color']};
                border-radius: 8px;
            }}

            QLabel {{
                color: {style_config['text_color']};
            }}

            QPushButton {{
                background-color: {style_config['border_color']};
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 16px;
                font-weight: bold;
                font-size: 10pt;
            }}

            QPushButton:hover {{
                background-color: {self._darken_color(style_config['border_color'])};
            }}

            QPushButton:pressed {{
                background-color: {self._darken_color(style_config['border_color'], 20)};
            }}

            QPushButton:focus {{
                outline: 2px solid {self._darken_color(style_config['border_color'])};
            }}
        """

        self.setStyleSheet(stylesheet)

    @staticmethod
    def _darken_color(hex_color: str, amount: int = 10) -> str:
        """Darken a hex color by a given amount."""
        hex_color = hex_color.lstrip('#')
        rgb = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
        darkened = tuple(max(0, c - amount) for c in rgb)
        return f"#{darkened[0]:02x}{darkened[1]:02x}{darkened[2]:02x}"
