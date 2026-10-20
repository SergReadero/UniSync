import functools
import time
from datetime import datetime
import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

@dataclass
class TimedResult:
    """Result with timing information."""
    return_value: Any
    elapsed_time: float
    operation_start: datetime
    function_name: str
    success: bool = True
    error_message: Optional[str] = None

    def __str__(self) -> str:
        """Human-readable string representation."""
        status = "✓" if self.success else "✗"
        time_str = self._format_time(self.elapsed_time)
        if self.error_message:
            return f"{status} {self.function_name} failed in {time_str}: {self.error_message}"
        return f"{status} {self.function_name} completed in {time_str}"

    @staticmethod
    def _format_time(elapsed: float) -> str:
        """Format elapsed time as human-readable string."""
        if elapsed < 0.001:
            return f"{elapsed * 1_000_000:.2f} µs"
        elif elapsed < 1:
            return  f"{elapsed * 1_000:.2f} ms"
        else:
            return f"{elapsed:.2f} s"

def measure_time_with_result(func):
    """
    Decorator that measures execution time and returns it with the result.
    Wraps the original return value and timing data in a TimedResult object.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.perf_counter()
        operation_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            result = func(*args, **kwargs)
            elapsed_time = time.perf_counter() - start_time
            logger.debug(
                f"{func.__name__} executed in {TimedResult._format_time(elapsed_time)}"
            )
            return TimedResult(
                return_value=result,
                elapsed_time=elapsed_time,
                operation_start=operation_start,
                function_name=func.__name__,
                success=True
            )
        except Exception as e:
            elapsed_time = time.perf_counter() - start_time
            logger.error(
                f"{func.__name__} failed after {TimedResult._format_time(elapsed_time)}: {e}",
                exc_info=True
            )
            return TimedResult(
                return_value=None,
                elapsed_time=elapsed_time,
                operation_start=operation_start,
                function_name=func.__name__,
                success=False,
                error_message=str(e)
            )
    return wrapper
