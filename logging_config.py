import logging
import logging.handlers
from pathlib import Path
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
import os

def setup_logging(log_dir: str = "logs", level=logging.DEBUG, days_to_keep: int = 5):
    """
    Configure logging for the entire application with timestamped files (one per session).
    Auto-cleanup: removes files older than days_to_keep.

    Args:
        log_dir: Directory to store log files
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        days_to_keep: Remove log files older than this many days
    """
    # Create logs directory if it doesn't exist
    log_path = Path(log_dir)
    log_path.mkdir(exist_ok=True)

    # Generate filename with current date and time: datetime.txt format
    log_filename = log_path / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

    # Create root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers to prevent duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Define log format with time, module, function, and message
    log_format = logging.Formatter(
        fmt='%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    file_handler = RotatingFileHandler(
        log_filename,
        maxBytes=5_000_000,  # 5MB
        backupCount=50  # Keep 50 backups of large sessions
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(log_format)
    root_logger.addHandler(file_handler)

    # Console handler (for PyCharm IDE visibility)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(log_format)
    root_logger.addHandler(console_handler)

    # Clean up old log files
    _cleanup_old_logs(log_path, days_to_keep)

    # Log the startup
    root_logger.info(f"Logging initialized. Log file: {os.path.abspath(log_filename)}")

    return log_filename

def _cleanup_old_logs(log_dir: Path, days_to_keep: int = 5):
    """
    Remove log files older than days_to_keep days.
    Only touches files matching the YYYYMMDD_HHMMSS.txt pattern.

    Args:
        log_dir: Path to logs directory
        days_to_keep: Number of days to retain
    """
    try:
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)

        for log_file in log_dir.glob("????????_??????.txt*"):  # Match YYYYMMDD_HHMMSS.txt*
            try:
                file_time = datetime.fromtimestamp(log_file.stat().st_mtime)
                if file_time < cutoff_date:
                    log_file.unlink()
                    logging.getLogger().debug(f"Removed old log file: {log_file.name}")
            except Exception as e:
                logging.getLogger().warning(f"Failed to delete {log_file.name}: {e}")
    except Exception as e:
        logging.getLogger().warning(f"Log cleanup failed: {e}")
