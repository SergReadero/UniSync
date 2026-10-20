import psutil
import threading
import time
from dataclasses import dataclass
from typing import Optional
import logging

import pyqt5_loader
from PyQt5.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

@dataclass
class ResourceMetrics:
    """Holds current system resource metrics."""
    cpu_percent: float # 0-100
    memory_percent: float # 0-100
    memory_mb_used: float
    memory_mb_available: float
    process_cpu_percent: float # Current process CPU
    process_memory_mb: float # Current process memory
    timestamp: float

class MetricsSignalEmitter(QObject):
    """Emits signals from background thread to GUI thread."""
    metrics_updated = pyqtSignal(ResourceMetrics)

class ResourceMonitor:
    """
    Monitors CPU and memory usage in real-time.
    Integrates with ProcessController and GUI for resource visualization.

    Usage:
        monitor = ResourceMonitor(update_interval=1.0)
        monitor.start()
        metrics = monitor.get_metrics()
        logger.debug(f"CPU: {metrics.cpu_percent}%, Memory: {metrics.memory_percent}%")
        monitor.stop()
    """

    def __init__(self, update_interval: float = 1.0, max_history: int = 60):
        """
        Initialize the ResourceMonitor

        Args:
        :param update_interval: How often to sample metrics (seconds)
        :param max_history: Keep last N samples for trend analysis
        """

        self.update_interval = update_interval
        self.max_history = max_history

        # self._monitoring = False
        self._stop_event = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        self._metrics_lock = threading.Lock()
        self.current_metrics: Optional[ResourceMetrics] = None
        self.metrics_history = []

        # Qt signal emitter
        self._metrics_emitter: Optional[MetricsSignalEmitter] = None

        # Process reference
        self._process = psutil.Process()
        self._last_cpu_check = 0.0

        logger.info("ResourceMonitor initialized")

    def start(self) -> bool:
        """Start monitoring resources in background thread."""
        if self._monitor_thread and self._monitor_thread.is_alive():
            logger.warning("ResourceMonitor already running")
            return False

        try:
            # self._monitoring = True
            self._stop_event.clear()  # Reset the stop event
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop,
                daemon=True,
                name="ResourceMonitor"
            )
            self._monitor_thread.start()
            logger.info("ResourceMonitor started")
            return True
        except Exception as e:
            logger.error(f"Failed to start ResourceMonitor: {e}")
            return False

    def stop(self) -> bool:
        """Stop monitoring resources."""
        if not self._monitor_thread or not self._monitor_thread.is_alive():
            logger.warning("ResourceMonitor not running")
            return False

        try:
            # self._monitoring = False
            self._stop_event.set()  # Signal thread to stop
            self._monitor_thread.join(timeout=2)

            if self._monitor_thread.is_alive():
                logger.error("ResourceMonitor did not stop within timeout!")
                return False

            logger.info("ResourceMonitor stopped")
            return True
        except Exception as e:
            logger.error(f"Error stopping ResourceMonitor: {e}")
            return False

    def _monitor_loop(self) -> None:
        """Background loop that continuously samples metrics."""
        logger.debug("Monitor loop started")

        while not self._stop_event.is_set():
            try:
                # Collect metrics
                metrics = self._collect_metrics()

                with self._metrics_lock:
                    self.current_metrics = metrics
                    self.metrics_history.append(metrics)

                    # Keep only last N samples
                    if len(self.metrics_history) > self.max_history:
                        self.metrics_history.pop(0)

                # Emit signal instead of calling callback directly
                if hasattr(self, '_metrics_emitter') and self._metrics_emitter:
                    try:
                        self._metrics_emitter.metrics_updated.emit(metrics)
                    except Exception as e:
                        logger.error(f"Error in metrics emitter: {e}")

            except Exception as e:
                logger.error(f"Error in monitor loop: {e}")
                self._stop_event.wait(self.update_interval)

            # Sleep with early exit capability
            # wait() returns False if timeout, True id event was set
            self._stop_event.wait(self.update_interval)

        logger.debug("Monitor loop exited")

    def _collect_metrics(self) -> ResourceMetrics:
        """Collect current system and process metrics."""
        try:
            # System-wide metrics
            cpu_percent = psutil.cpu_percent(interval=0.1)
            memory_info = psutil.virtual_memory()

            # Process-specific metrics
            with self._process.oneshot():
                try:
                    process_cpu = self._process.cpu_percent(interval=None)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    process_cpu = 0.0

                try:
                    process_memory_mb = self._process.memory_info().rss / 1024 / 1024
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    process_memory_mb = 0.0

            return ResourceMetrics(
                cpu_percent=cpu_percent,
                memory_percent=memory_info.percent,
                memory_mb_used=memory_info.used / 1024 / 1024,
                memory_mb_available=memory_info.available / 1024 / 1024,
                process_cpu_percent=process_cpu,
                process_memory_mb=process_memory_mb,
                timestamp=time.time()
            )
        except Exception as e:
            logger.error(f"Error collecting metrics: {e}")
            return ResourceMetrics(
                cpu_percent=0.0,
                memory_percent=0.0,
                memory_mb_used=0.0,
                memory_mb_available=0.0,
                process_cpu_percent=0.0,
                process_memory_mb=0.0,
                timestamp=time.time()
            )

    def get_metrics(self) -> Optional[ResourceMetrics]:
        """Get the latest collected metrics."""
        with self._metrics_lock:
            return self.current_metrics

    def get_cpu_usage(self) -> float:
        """Get current system CPU usage (0-100)."""
        with self._metrics_lock:
            return self.current_metrics.cpu_percent if self.current_metrics else 0.0

    def get_memory_usage(self) -> float:
        """Get current system memory usage percentage (0-100)."""
        with self._metrics_lock:
            return self.current_metrics.memory_percent if self.current_metrics else 0.0

    def get_process_cpu_usage(self) -> float:
        """Get current process CPU usage."""
        with self._metrics_lock:
            return self.current_metrics.process_cpu_percent if self.current_metrics else 0.0

    def get_process_memory_usage(self) -> float:
        """Get current process memory usage in MB."""
        with self._metrics_lock:
            return self.current_metrics.process_memory_mb if self.current_metrics else 0.0

    def get_average_cpu(self, samples: int = 10) -> float:
        """Get average CPU usage over last N samples."""
        with self._metrics_lock:
            if not self.metrics_history or len(self.metrics_history) < samples:
                samples = len(self.metrics_history)

            if samples == 0:
                return 0.0

            recent = self.metrics_history[-samples:]
            return sum(m.cpu_percent for m in recent) / samples

    def get_average_memory(self, samples: int = 10) -> float:
        """Get average memory usage over last N samples."""
        with self._metrics_lock:
            if not self.metrics_history or len(self.metrics_history) < samples:
                samples = len(self.metrics_history)

            if samples == 0:
                return 0.0

            recent = self.metrics_history[-samples:]
            return sum(m.memory_percent for m in recent) / samples

    def set_update_callback(self, emitter: MetricsSignalEmitter) -> None:
        """
        Register callback to be invoked when metrics are updated.
        Useful for GUI updates.

        Args:
        :param callback: Function that accepts ResourceMetrics
        :return:
        """
        self._metrics_emitter = emitter

    def is_running(self) -> bool:
        """Check if monitor is currently running."""
        return self._monitor_thread is not None and self._monitor_thread.is_alive()

if __name__ == "__main__":
    monitor = ResourceMonitor(update_interval=1.0)
    monitor.start()
    # Wait for metrics to be available
    for attempt in range(10):
        time.sleep(0.1)
        metrics = monitor.get_metrics()
        if metrics is not None:
            break
    if metrics is not None:
        logger.debug(f"CPU: {metrics.cpu_percent}%, Memory: {metrics.memory_percent}%")
    else:
        logger.info("Failed to collect metrics")
    monitor.stop()
