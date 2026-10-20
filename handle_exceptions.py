import logging
from typing import Callable, Any
import functools

def handle_exceptions(logger: logging.Logger) -> Callable:
    """
    Decorator that wraps functions with try-except and logs errors with function name.

    Usage:
        @handle_exceptions(logger)
        def my_function():
            # your code here

    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.error(
                    f"Error in {func.__module__} -> {func.__name__} -> {type(e).__name__}: {e}",
                    exc_info=True # Includes full traceback
                )
                raise
        return wrapper
    return decorator
