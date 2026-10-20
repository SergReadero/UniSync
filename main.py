import sys
import os
import logging
from logging_config import setup_logging

# Initializing logging
setup_logging(log_dir="logs", level=logging.DEBUG)

from main_gui import UniSyncMainWindow
import pyqt5_loader
from PyQt5.QtWidgets import QApplication

logger = logging.getLogger(__name__)

def main():
    """Main application entry point."""
    logger.info("Application starting")

    try:
        # Create QApplication instance
        app = QApplication(sys.argv)

        # Create and display main window
        window = UniSyncMainWindow()
        window.showMaximized()
        logger.info("Main window displayed")

        # Run application event loop
        exit_code = app.exec_()
        logger.info(f"Application exited with code: {exit_code}")

        # Ensure all log handlers flush before exit to prevent hangs
        for handler in logger.handlers:
            handler.flush()

        return exit_code

    except Exception as e:
        logger.error(
            f"Critical error during application execution: {type(e).__name__}: {e}",
            exc_info=True
        )

        # Ensure logs are written before exit
        for handler in logger.handlers:
            handler.flush()

        return 1

if __name__ == "__main__":
    exit_code = main()

    # Force exit on Windows to prevent interpreter cleanup hangs
    sys.exit(exit_code)
