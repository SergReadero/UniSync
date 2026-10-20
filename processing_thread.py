"""
processing_thread.py

Contains all processing thread classes and their result dataclasses.
No GUI imports - pure threading logic.
"""

from abc import ABC
import queue
from dataclasses import dataclass, field
import time
from datetime import datetime, timedelta
from typing import List, Optional, Set, Dict, Tuple
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import os
from enums import FileOperation, ProcessingMode, PreserveStructureType
from preserve_structure_strategies import PreserveStructureStrategy
from measure_time_with_result import measure_time_with_result
from handle_exceptions import handle_exceptions
from file_manager import FileManagerBase, FileManagerCopyMove, FileManagerParallel
from custom_types.copy_move_types import FileMetadataKey, FileMetadataValue

import pyqt5_loader
from PyQt5.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)

@dataclass
class ProcessingThreadResultAbstract(ABC):
    """Base result class for all processing threads."""
    operation_type: FileOperation = FileOperation.COPY
    total_processed: int = 0
    total_skipped: int = 0
    renamed_files: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    total_size: int = 0 # in bytes
    duration: float = 0.0 # in seconds
    operation_start: datetime = field(default_factory=datetime.now)

    def get_truncated_files(self, max_items: int = 10,
                            file_list: Optional[List[str]] = None) -> dict:
        """
        Return truncated file list with ellipsis if exceeds max_items.

        Args:
            :param max_items: Maximum number of items to display (default: 10)
            :param file_list: The list to truncate (uses renamed_files if None)

        Returns:
            dict with 'files' and 'is_truncated' keys
        """
        target_list = file_list if file_list is not None else self.renamed_files

        if len(target_list) <= max_items:
            return {
                "files": target_list,
                "is_truncated": False,
                "total_count": len(target_list),
                "displayed_count": len(target_list)
            }
        else:
            return {
                "files": target_list[:max_items] + [f"...and {len(target_list) - max_items} more"],
                "is_truncated": True,
                "total_count": len(target_list),
                "displayed_count": max_items
            }

    def get_truncated_errors(self, max_items: int = 10) -> dict:
        """Return truncated error list."""
        return self.get_truncated_files(max_items, self.errors)

    def to_dict(self, truncate_files: bool = True, max_items: int = 10) -> dict:
        """
        Convert result to dictionary for signal emission.

        Args:
            :param truncate_files: Whether to truncate file lists
            :param max_items: Maximum items to display when truncating

        Returns:
            Dictionary suitable for signal emission
        """
        renamed_info = (
            self.get_truncated_files(max_items)
            if truncate_files
            else {
                "files": self.renamed_files,
                "is_truncated": False,
                "total_count": len(self.renamed_files),
                "displayed_count": len(self.renamed_files)
            }
        )

        errors_info = (
            self.get_truncated_errors(max_items)
            if truncate_files
            else {
                "files": self.errors,
                "is_truncated": False,
                "total_count": len(self.errors),
                "displayed_count": len(self.errors)
            }
        )

        return {
            "operation_type": self.operation_type.value,
            "total_processed": self.total_processed,
            "total_skipped": self.total_skipped,
            "renamed_files": renamed_info,
            "errors": errors_info,
            "total_size": self.total_size,
            "duration": self.duration,
            "operation_start": self.operation_start
        }

@dataclass
class ProcessingThreadCopyMoveResult(ProcessingThreadResultAbstract):
    """Result for copy operations."""
    operation_type: FileOperation = field(default=FileOperation.COPY)
    source_paths: List[str] = field(default_factory=list)
    destination_paths: List[str] = field(default_factory=list)
    base_dest_paths: List[str] = field(default_factory=list)
    preserve_structure: PreserveStructureType = field(default=PreserveStructureType.NONE)
    processed_files_metadata: Dict[FileMetadataKey, FileMetadataValue] = field(default_factory=dict)

@dataclass
class ProcessingThreadResult(ProcessingThreadResultAbstract):
    """Result for copy operations."""
    operation_type: FileOperation = field(default=FileOperation.DISCOVERY)
    discovered_files: List[str] = field(default_factory=list)
    # comparison_table_result: Dict[Tuple, List[str]] = field(default_factory=dict)

    def get_truncated_files(self, max_items: int = 10,
                            file_list: Optional[List[str]] = None) -> dict:
        """Override to use discovered_files as default."""
        target_list = file_list if file_list is not None else self.discovered_files
        return super().get_truncated_files(max_items, target_list)

class ProcessingThread(QThread):
    """Background thread for file processing without blocking GUI."""

    # Signals
    progress_updated = pyqtSignal(int, str) # progress_percent, message
    processing_finished = pyqtSignal(ProcessingThreadResult) # success
    comparison_table_final_results = pyqtSignal(dict)  # Incremental batch updates
    # CORRECT{('2.txt', 10, hash_data): ['C1\2.txt', 'C2\2.txt']}

    def __init__(
            self, processing_mode: ProcessingMode, source_dirs: List, num_workers: int = 2,
            plugin_manager=None, active_filters: List[dict] = None
    ):
        super().__init__()
        self.processing_mode = processing_mode
        self.source_dirs = source_dirs
        self.num_workers = num_workers
        self.plugin_manager = plugin_manager
        self.active_filters = active_filters or []
        self.is_running = False
        self.file_managers = []
        self.shared_results = {}

    @measure_time_with_result
    def _execute_discovery_files(self):
        """Execute processing of discovering files in background."""
        try:
            # Create shared results dictionary
            results_lock = threading.Lock()
            self.is_running = True

            # Initialize result object
            result = ProcessingThreadResult(operation_type=FileOperation.DISCOVERY)

            # Queue for incremental batch updates
            batch_queue = queue.Queue()

            # Launch estimation in background
            estimated_files = [0]  # Mutable container for callback
            estimation_thread = threading.Thread(
                target=self._estimate_total_files_async,
                args=(estimated_files,),
                daemon=True
            )
            estimation_thread.start()

            # Create FileManager per source directory
            for source_dir in self.source_dirs:
                if self.processing_mode == ProcessingMode.SEQUENTIAL:
                    file_manager = FileManagerBase(
                        source_dir, self.shared_results, self.plugin_manager,
                        self.active_filters, batch_queue=batch_queue
                    )
                else:
                    file_manager = FileManagerParallel(source_dir, self.shared_results, self.num_workers)

                file_manager.results_lock = results_lock  # Use shared lock
                self.file_managers.append(file_manager)
                file_manager.start_processing()

            # Monitor batches for progress only (no table updates yet - Hybrid approach)
            total_files_discovered = 0
            active_managers = len(self.file_managers)
            last_progress_update = datetime.now()

            try:
                while active_managers > 0:
                    # Check if any manager is still alive
                    active_managers = sum(
                        1 for fm in self.file_managers
                        if fm.processing_thread and fm.processing_thread.is_alive()
                    )

                    try:
                        # Wait for batch with timeout (non-blocking)
                        batch = batch_queue.get(timeout=0.5)

                        # Count files from batch (not from shared_results - to avoid double-counting)
                        batch_file_count = sum(len(paths) for paths in batch.values())
                        total_files_discovered += batch_file_count

                        # Merge batch into result
                        for key, paths in batch.items():
                            result.total_size += key[1] * len(paths)  # key = (name, size, hash)

                        # Throttled progress update
                        current_time = datetime.now()
                        if current_time - last_progress_update >= timedelta(seconds=0.5):
                            # Linear progress based on estimate
                            est = estimated_files[0] or 1
                            progress_pct = min(int((total_files_discovered / est) * 100), 99)

                            message = (
                                f"Discovered {total_files_discovered} total files "
                                f"({result.total_size / (1024**3):.2f} GB)..."
                            )
                            self.progress_updated.emit(progress_pct, message)
                            last_progress_update = current_time

                            logger.debug(f"Batch emitted: {len(batch)} keys, "
                                         f"{total_files_discovered} total files, "
                                         f"Progress: {progress_pct}%")

                    except queue.Empty:
                        # No batch available yet, continue monitoring
                        pass

            finally:
                # Wait for all threads to finish
                for fm in self.file_managers:
                    if fm.processing_thread:
                        fm.processing_thread.join(timeout=10)

            # Finalize result
            result.total_processed = len(self.shared_results)

            # Emit signal to clear and refill table with shared_results
            self.comparison_table_final_results.emit(dict(self.shared_results))

            logger.debug(f"Discovery complete: {result.total_processed} unique files, "
                         f"{result.total_size / (1024**3):.2f} GB")
            return result

        except Exception as e:
            logger.error(f"Discovery files operation failed: {e}", exc_info=True)
            raise

    def run(self):
        """Main thread execution"""
        try:
            time_result = self._execute_discovery_files()

            # TimedResult object from the decorator
            logger.debug(f"Execution time: {time_result.elapsed_time:.2f} s")

            if time_result.success:
                discovery_result = time_result.return_value
                discovery_result.duration = time_result.elapsed_time
                discovery_result.operation_start = time_result.operation_start

                # Final progress
                self.progress_updated.emit(
                    100,
                    f"Discovery files completed: {discovery_result.total_processed} files discovered"
                )

                # Emit structured result dictionary
                self.processing_finished.emit(discovery_result)

            else:
                # Error handling
                error_result = ProcessingThreadResult(
                    total_processed=0,
                    errors=[time_result.error_message],
                    duration=time_result.elapsed_time
                )
                self.progress_updated.emit(0, f"Error: {time_result.error_message}")
                self.processing_finished.emit(error_result)

        except Exception as e:
            logger.error(f"Thread execution failed: {e}", exc_info=True)
            error_result = ProcessingThreadResult(
                errors=[str(e)],
                duration=0.0
            )
            self.progress_updated.emit(0, f"Error: {str(e)}")
            self.processing_finished.emit(error_result)

    @handle_exceptions(logger)
    def _estimate_total_files_async(self, result_container: list):
        """Estimate in background without blocking discovery."""
        count = 0
        last_update_time = datetime.now()
        update_interval = timedelta(milliseconds=500)

        for source_dir in self.source_dirs:
            for _, _, files in os.walk(source_dir):
                count += len(files)

                current_time = datetime.now()
                if current_time - last_update_time >= update_interval:
                    result_container[0] = count
                    last_update_time = current_time
                    logger.debug(f"Estimation: {count} files...")

                # Stop early if discovery already finished
                if not self.is_running:
                    return

        logger.debug(f"Estimation complete: {count} files")
        result_container[0] = count if count > 0 else 1

    @handle_exceptions(logger)
    def pause(self):
        """Pause processing."""
        if self.file_managers:
            for file_manager in self.file_managers:
                file_manager.pause_processing()

    @handle_exceptions(logger)
    def resume(self):
        """Resume processing."""
        if self.file_managers:
            for file_manager in self.file_managers:
                file_manager.resume_processing()

    @handle_exceptions(logger)
    def stop(self):
        """Stop processing"""
        if self.file_managers:
            for file_manager in self.file_managers:
                file_manager.stop_processing()
        self.is_running = False

class ProcessingThreadCopyMove(QThread):
    """Background thread for copy operations from comparison table"""

    progress_updated = pyqtSignal(int, str)
    processing_finished = pyqtSignal(ProcessingThreadCopyMoveResult)

    def __init__(
            self, processing_mode: ProcessingMode, selected_files_dict: dict, dest_dirs: List,
            operation: FileOperation = FileOperation.COPY, num_workers: int = 2,
            preserve_structure: Optional[PreserveStructureStrategy] = None
    ):
        super().__init__()
        self.selected_files_dict = selected_files_dict  # From comparison_table selection
        self.dest_dirs = dest_dirs
        self.operation = operation
        self.num_workers = num_workers
        self.preserve_structure = preserve_structure
        self.is_running = False
        self._is_paused = False
        self.file_managers_copy_move = []
        self.processed_files = set()

        # Progress tracking
        self.total_operations = len(self.selected_files_dict) * len(self.dest_dirs)

        # shared_results dict for all managers
        self.shared_results = {
            "processed_files_metadata": {},
            "total_processed": 0,
            "total_skipped": 0,
            "renamed_files": [],
            "errors": [],
            "total_size": 0,
            "unique_dest_paths": set(),
            "source_file_paths": set()
        }
        self.results_lock = threading.Lock()

    @measure_time_with_result
    def _execute_operation(self):
        """Execute copy operation in background with bounded thread pool for source directories"""
        try:
            self.is_running = True

            # Create FileManagerCopy for each unique source directory
            source_dirs = set()
            for (file_name, file_size, file_hash), source_paths in self.selected_files_dict.items():
                first_src_path = source_paths[0]
                source_dir = os.path.dirname(first_src_path)
                source_dirs.add(source_dir)
            logger.debug(f"Found {len(source_dirs)} source directories")
            logger.debug(f"Processing {len(self.selected_files_dict)} unique files to {len(self.dest_dirs)} "
                         f"destinations")

            # Filtered dict with only first paths
            filtered_files_dict = {}
            for (file_name, file_size, file_hash), source_paths in self.selected_files_dict.items():
                filtered_files_dict[(file_name, file_size, file_hash)] = source_paths[0]

            # Reset Global State to do proper copy/move, rechanging/renaming
            FileManagerCopyMove.reset_global_dest_contents()

            # Use ThreadPoolExecutor to limit concurrency
            max_workers = self._calculate_optimal_workers(len(source_dirs))
            logger.info(f"Using {max_workers} worker threads for {len(source_dirs)} source directories")

            # Launch progress monitor in separate daemon thread
            self.monitor_stop_event = threading.Event()
            monitor_thread = threading.Thread(
                target=self._monitor_progress,
                daemon=True,
                name="ProgressMonitor"
            )
            monitor_thread.start()

            # Process source directories with thread pool
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="SourceOp") as executor:
                future_to_manager = {}

                # Submit all source directory tasks
                for source_dir in source_dirs:
                    # Check if paused/stopped before submitting
                    while self.is_running and not hasattr(self, '_is_paused'):
                        pass

                    if not self.is_running:
                        logger.info("Operation cancelled before submitting all tasks")
                        break

                    file_manager_copy_move = FileManagerCopyMove(
                        source_dir, self.operation, self.shared_results
                    )
                    file_manager_copy_move.results_lock = self.results_lock
                    file_manager_copy_move.processed_files = self.processed_files

                    self.file_managers_copy_move.append(file_manager_copy_move)

                    # Submit task to thread pool
                    future = executor.submit(
                        self._process_source_directory,
                        file_manager_copy_move,
                        filtered_files_dict,
                        source_dir
                    )
                    future_to_manager[future] = file_manager_copy_move
                    logger.debug(f"Submitted task for source_dir={source_dir}")

                # Monitor futures as they complete
                try:
                    for future in as_completed(future_to_manager, timeout=3600):
                        manager = future_to_manager[future]
                        try:
                            result = future.result()
                            logger.debug(f"Completed source_dir={manager.source_dir}")
                        except Exception as e:
                            logger.error(f"Task failed for {manager.source_dir}: {e}", exc_info=True)
                            with self.results_lock:
                                self.shared_results["errors"].append({
                                    "file": "operation",
                                    "error": f"Failed to process {manager.source_dir}: {e}"
                                })
                except Exception as e:
                    logger.error(f"Error waiting for futures: {e}", exc_info=True)

            # Stop monitor thread
            self.monitor_stop_event.set()
            monitor_thread.join(timeout=5)

            # Build final result
            copy_move_result = ProcessingThreadCopyMoveResult(operation_type=self.operation)
            with self.results_lock:
                copy_move_result.total_processed = self.shared_results["total_processed"]
                copy_move_result.total_skipped = self.shared_results["total_skipped"]
                copy_move_result.total_size = self.shared_results["total_size"]
                copy_move_result.renamed_files.extend(self.shared_results["renamed_files"])
                copy_move_result.errors.extend(self.shared_results["errors"])
                copy_move_result.source_paths.extend(list(self.shared_results["source_file_paths"]))
                copy_move_result.destination_paths.extend(list(self.shared_results["unique_dest_paths"]))
                copy_move_result.base_dest_paths.extend(list(self.dest_dirs))
                copy_move_result.preserve_structure = PreserveStructureType(self.preserve_structure.__class__.__name__)
                copy_move_result.processed_files_metadata = self.shared_results.get("processed_files_metadata", {})

            logger.debug(f"Final ProcessingThreadCopyMoveResult: {copy_move_result}")
            logger.info(f"{self.operation.value.capitalize()} operation completed.")

            # return ProcessingThreadCopyResult
            return copy_move_result

        except Exception as e:
            logger.error(f"{self.operation.value.capitalize()} operation failed: {e}", exc_info=True)
            raise
        finally:
            self.is_running = False

    def _process_source_directory(self, file_manager_copy_move, filtered_files_dict, source_dir):
        """
        Process a single source directory (runs in ThreadPoolExecutor).
        This replaces the direct start_operation_thread() call.
        """
        try:
            logger.debug(f"Starting operation for source_dir={source_dir}")

            # Start the operation thread within this executor task
            file_manager_copy_move.start_operation_thread(
                filtered_files_dict,
                self.dest_dirs,
                self.processed_files,
                preserve_structure=self.preserve_structure
            )

            # Wait for this file manager's thread to complete
            if file_manager_copy_move.processing_thread:
                file_manager_copy_move.processing_thread.join()
                logger.debug(f"Completed {file_manager_copy_move.processing_thread.name}")

            return True

        except Exception as e:
            logger.error(f"Failed to process {source_dir}: {e}", exc_info=True)
            return False

    def _calculate_optimal_workers(self, num_tasks: int) -> int:
        """Calculate optimal worker thread count based on machine resources"""

        cpu_count = os.cpu_count() or 4

        # For I/O-bound operations (copy/move):
        # - Use 2-3x CPU count (I/O operations allow context switching
        # - Cap at 32 to avoid excessive threading overhead
        # - At least 2 threads
        optimal = min(max(cpu_count * 2, 2), 32)

        # Should not exceed number of tasks
        optimal = min(optimal, max(num_tasks, 1))

        logger.info(f"Machine has {cpu_count} CPUs, {num_tasks} source directories, using {optimal} workers")
        return optimal

    def _monitor_progress(self):
        """
        Real-time progress monitoring (runs in daemon thread).
        More frequent updates than the main loop.
        """
        last_processed = 0
        last_emitted_time = datetime.now()
        update_interval = timedelta(milliseconds=200)

        while self.is_running and not self.monitor_stop_event.is_set():
            try:
                current_time = datetime.now()

                with self.results_lock:
                    processed = self.shared_results["total_processed"]
                    total_size = self.shared_results["total_size"]

                # Emit if there is progress or enough time has passed
                if processed != last_processed or (current_time - last_emitted_time >= update_interval):
                    progress_pct = int((processed / self.total_operations) * 100) if self.total_operations > 0 else 0
                    progress_pct = min(progress_pct, 99)  # Cap at 99%

                    message = (
                        f"Processing {processed}/{self.total_operations} operations "
                        f"({total_size / (1024**3):.2f} GB)..."
                    )

                    self.progress_updated.emit(progress_pct, message)
                    last_processed = processed
                    last_emitted_time = current_time

                self.monitor_stop_event.wait(timeout=0.2)

            except Exception as e:
                logger.error(f"Monitor thread error: {e}")
                self.monitor_stop_event.wait(timeout=0.2)

    def run(self):
        """Main thread execution"""
        logger.info(f">>> ProcessingThreadCopyMove.run() START")
        try:
            time_result = self._execute_operation()

            # TimedResult object from the decorator
            logger.debug(f"Execution time: {time_result.elapsed_time:.2f} s")

            if time_result.success:
                copy_move_result = time_result.return_value
                logger.debug(f">>> Result created: {type(copy_move_result)}")
                copy_move_result.duration = time_result.elapsed_time
                copy_move_result.operation_start = time_result.operation_start

                self.progress_updated.emit(100,
                    f"{self.operation.value.capitalize()} completed: "
                    f"{copy_move_result.total_processed} files processed"
                )

                logger.info(f">>> About to emit processing_finished")
                self.processing_finished.emit(copy_move_result)
                logger.info(f">>> processing_finished emitted successfully")
            else:
                # Create error result
                error_result = ProcessingThreadCopyMoveResult(
                    operation_type=self.operation,
                    total_processed=0,
                    total_skipped=0,
                    errors=[time_result.error_message],
                    duration=time_result.elapsed_time
                )
                self.progress_updated.emit(0, f"Error: {time_result.error_message}")
                self.processing_finished.emit(error_result)

        except Exception as e:
            logger.error(f"Thread execution failed: {str(e)}", exc_info=True)
            error_result = ProcessingThreadCopyMoveResult(
                operation_type=self.operation,
                errors=[str(e)],
                duration=0.0
            )
            self.progress_updated.emit(0, f"Error: {str(e)}")
            self.processing_finished.emit(error_result)

    @handle_exceptions(logger)
    def pause(self):
        """Pause all copy/move operations."""
        self.is_running = False
        self._is_paused = True
        logger.info("Pause requested for all operations")

        if self.file_managers_copy_move:
            for file_manager_copy_move in self.file_managers_copy_move:
                try:
                    file_manager_copy_move.pause_processing()
                except Exception as e:
                    logger.error(f"Error pausing {file_manager_copy_move.source_dir}: {e}")

    @handle_exceptions(logger)
    def resume(self):
        """Resume all copy/move operations."""
        self.is_running = True
        self._is_paused = False
        logger.info("Resume requested for all operations")

        if self.file_managers_copy_move:
            for file_manager_copy_move in self.file_managers_copy_move:
                try:
                    file_manager_copy_move.resume_processing()
                except Exception as e:
                    logger.error(f"Error resuming {file_manager_copy_move.source_dir}: {e}")

    @handle_exceptions(logger)
    def stop(self):
        """Stop all copy/move operations."""
        self.is_running = False
        logger.info("Stop requested for all operations")

        if self.file_managers_copy_move:
            for file_manager_copy_move in self.file_managers_copy_move:
                try:
                    file_manager_copy_move.stop_processing()
                except Exception as e:
                    logger.error(f"Error stopping {file_manager_copy_move.source_dir}: {e}")
