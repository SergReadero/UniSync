import sys
import os
import platform
import logging

logger = logging.getLogger(__name__)

def is_windows7_x32():
    """Check if OS is Windows 7 32-bit"""
    if sys.platform != 'win32':
        return False
    win_version = platform.win32_ver()
    if win_version[0] == '7':  # Windows 7
        if sys.maxsize == 2147483647:  # 32-bit Python
            return True
    return False


if is_windows7_x32():
    logger.info("[PyQt5 Loader] Windows 7 x32 detected. Patching os.add_dll_directory...")

    # Simple patch: replace with no-op lambda
    os.add_dll_directory = lambda x: None

    logger.info("[PyQt5 Loader] Patch applied successfully")

else:
    logger.info(f"[PyQt5 Loader] Running on {platform.platform()} - no patch needed")
