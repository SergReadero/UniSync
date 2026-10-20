import os
import time
import logging
import threading
import multiprocessing
from multiprocessing import Process
from queue import Empty
import shutil
import hashlib
from enums import FileOperation
from custom_types.copy_move_types import FileMetadataKey

logger = logging.getLogger(__name__)

class FileManagerBase:
    def __init__(self, directory_path, shared_results=None, plugin_manager=None,
                 active_filters=None, batch_queue=None):
        self.directory_path = directory_path
        # Shared results dictionary (thread-safe)
        self.shared_results = shared_results if shared_results is not None else {}
        self.results_lock = threading.Lock()

        # Batch queue for incremental updates
        self.batch_queue = batch_queue
        self.batch_size = 100  # Flush every N files
        self.batch_timeout = 2.0  # Flush every T seconds

        # Unified pause/resume with Condition Variable
        self._pause_condition = threading.Condition()
        self._paused = False
        self._stop = False

        self.plugin_manager = plugin_manager
        self.active_filters = active_filters or []
        self.processing_thread = None

    def _get_file_hash(self, file_path, chunk_size=8192):
        """Hash file"""
        hasher = hashlib.sha256()
        try:
            with open(file_path, 'rb') as f:
                chunk = f.read(chunk_size)
                while chunk:
                    hasher.update(chunk)
                    chunk = f.read(chunk_size)
            return hasher.hexdigest()
        except Exception as e:
            logger.error(f"Error hashing {file_path}: {str(e)}")
            return None

    def _handle_pause(self):
        """Block until resumed or stopped."""
        with self._pause_condition:
            while self._paused and not self._stop:
                logger.debug(f">>> WAITING in pause loop at {threading.current_thread().name}")
                self._pause_condition.wait(timeout=0.5)

    def _worker(self):
        """The function that runs in a separate thread to process files."""
        logger.info(f"Worker thread started for {self.directory_path}")

        batch = {}  # Local batch to avoid lock contention
        last_flush_time = time.time()
        files_processed = 0
        paths_to_migrate = []

        try:
            for root, dirs, files in os.walk(self.directory_path):
                # Check stop before directory processing
                with self._pause_condition:
                    if self._stop:
                        logger.debug("Stop signal received. Exiting worker.")
                        return

                for file_name in files:
                    # Handle pause with stop check inside
                    self._handle_pause()

                    # Check stop before processing file
                    with self._pause_condition:
                        if self._stop:
                            logger.debug("Stop signal received immediately before processing. Exiting worker.")
                            return

                    file_path = os.path.join(root, file_name)
                    file_path = os.path.normpath(file_path)

                    try:
                        file_size = os.path.getsize(file_path)

                        # Quick filter checking before hash (cheap operation first)
                        if self.plugin_manager and hasattr(self, 'active_filters') and self.active_filters:
                            if not self._passes_quick_filters(file_path, file_name, file_size):
                                logger.warning(f"[{self.directory_path}] Rejected by filters: {file_name}")
                                continue  # Skip this file entirely

                        if file_size is not None:
                            # Check collision in both batch and shared_results
                            file_hash = None
                            with self.results_lock:
                                collision_detected = any(
                                    k[0] == file_name and k[1] == file_size
                                    for k in self.shared_results.keys()
                                )
                                # Check local batch for pending collisions
                                batch_collision_detected = any(
                                    k[0] == file_name and k[1] == file_size
                                    for k in batch.keys()
                                )

                            # Hash outside lock
                            if collision_detected or batch_collision_detected:
                                file_hash = self._get_file_hash(file_path)

                            # Update dict (brief lock)
                            with self.results_lock:
                                # Re-check collision in shared_results and batch
                                collision_detected = any(
                                    k[0] == file_name and k[1] == file_size
                                    for k in self.shared_results.keys()
                                )
                                batch_collision_detected = any(
                                    k[0] == file_name and k[1] == file_size
                                    for k in batch.keys()
                                )

                                # DEBUG: Log what we're checking
                                logger.debug(f"\n[{self.directory_path}] Processing: {file_name} ({file_size}B)")
                                logger.debug(f"Collision detected: {collision_detected}")
                                logger.debug(f"Batch collision detected: {batch_collision_detected}")

                                if (not collision_detected) and (not batch_collision_detected):
                                    # No collision in shared_results and batch: store with None hash
                                    # Checking if already in batch with hash
                                    key = (file_name, file_size, None)
                                    batch.setdefault(key, []).append(file_path)
                                    self.shared_results.setdefault(key, []).append(file_path)
                                    files_processed += 1
                                    logger.debug(f"No collision. Added current file: {file_path} as {key}")
                                else:
                                    # Collision with shared_results or batch
                                    full_key = (file_name, file_size, file_hash)
                                    logger.debug(f"Collision with shared_results or batch! Hash: {file_hash[:8]}...")

                                    # Collect paths to migrate, then hash them outside for shared_results
                                    none_key = (file_name, file_size, None)
                                    if none_key in self.shared_results:
                                        paths_to_migrate = self.shared_results.pop(none_key)
                                        logger.debug(
                                            f"Will migrate from shared_results {len(paths_to_migrate)} file(s)... Pop executed!"
                                        )
                                    batch_paths = []
                                    if none_key in batch:
                                        batch_paths = batch.pop(none_key)
                                        logger.debug(
                                            f"Will migrate from batch {len(batch_paths)} file(s)... Pop executed!"
                                        )
                                    paths_to_migrate.extend(batch_paths)

                            # Hash migrated files outside lock
                            if paths_to_migrate and len(paths_to_migrate) > 0:
                                migration_entries = []
                                for original_path in paths_to_migrate:
                                    if original_path == file_path:  # Skip current file
                                        continue
                                    # Get the original file_name and file_size
                                    original_file_name = os.path.basename(original_path)
                                    original_file_size = os.path.getsize(original_path)
                                    original_hash = self._get_file_hash(original_path)
                                    original_full_key = (original_file_name, original_file_size, original_hash)
                                    migration_entries.append((original_full_key, original_path))

                                # Insert migrated entries in a single lock
                                with self.results_lock:
                                    for original_full_key, original_path in migration_entries:
                                        self.shared_results.setdefault(original_full_key, []).append(original_path)
                                        batch.setdefault(original_full_key, []).append(original_path)
                                        files_processed += 1
                                        logger.debug(f"Migrated {original_path} -> {original_full_key}")

                                    # Add current file with actual metadata
                                    self.shared_results.setdefault(full_key, []).append(file_path)
                                    batch.setdefault(full_key, []).append(file_path)
                                    files_processed += 1
                                    logger.debug(f"Added current file: {file_path}")

                                paths_to_migrate.clear()

                            # Check if we should flush: either batch size reached or timeout
                            current_time = time.time()

                            if (
                                (files_processed >= self.batch_size) or (
                                    self.batch_timeout > 0 and (current_time - last_flush_time) >= self.batch_timeout)
                            ):
                                self._flush_batch(batch)
                                batch.clear()
                                files_processed = 0
                                last_flush_time = current_time

                    except Exception as e:
                        logger.error(f"Exception processing {file_path}: {e}", exc_info=True)

            # Flush remaining batch
            if batch:
                self._flush_batch(batch)

        except Exception as e:
            logger.error(f"Worker exception: {e}", exc_info=True)
        finally:
            logger.debug(f"Worker thread finished for: {self.directory_path}")

    def _flush_batch(self, batch):
        """
        Merge local batch into shared results (brief lock) and emit to queue.
        """
        if not batch:
            return

        # Post to queue for UI updates (no lock needed)
        if self.batch_queue is not None:
            try:
                self.batch_queue.put(dict(batch), block=False)
                logger.debug(f"[{self.directory_path}] Flushed batch to queue: {len(batch)} keys")
            except Exception as e:
                logger.warning(f"Failed to post batch to queue: {e}")

    def start_processing(self):
        """Starts the file processing task in a new thread."""
        if self.processing_thread is None or not self.processing_thread.is_alive():
            with self._pause_condition:
                self._paused = False
                self._stop = False
            self.processing_thread = threading.Thread(
                target=self._worker,
                daemon=False,
                name=f"UniSync-{os.path.basename(self.directory_path)}"
            )
            self.processing_thread.start()
            logger.info(f"Processing started with name {self.processing_thread.name}.")
            return self.shared_results
        else:
            logger.info("Processing is already running.")
            return self.shared_results

    def pause_processing(self):
        """Pauses the file processing task."""
        if self.processing_thread and self.processing_thread.is_alive() and not self._stop:
            with self._pause_condition:
                if self._paused or self._stop:
                    logger.info("Task is already paused or stopped.")
                    return
                self._paused = True
                logger.info("Sending pause signal...")

    def resume_processing(self):
        """Resumes the file processing task if it was paused."""
        if self.processing_thread and self.processing_thread.is_alive() and not self._stop:
            with self._pause_condition:
                if not self._paused or self._stop:
                    logger.info("Task is not paused or is stopped.")
                    return
                self._paused = False
                self._pause_condition.notify_all()
                logger.info("Sending resume signal...")

    def stop_processing(self):
        """Stops the file processing task."""
        if self.processing_thread and self.processing_thread.is_alive():
            with self._pause_condition:
                self._stop = True
                self._paused = False
                self._pause_condition.notify_all()

            self.processing_thread.join(timeout=5)
            logger.info("Processing stopped.")
            self.processing_thread = None
        else:
            logger.info("No active processing to stop.")

    def get_results(self):
        """Safely retrieve accumulated results."""
        with self.results_lock:
            return dict(self.shared_results)

    def _passes_quick_filters(self, file_path: str, file_name: str, file_size: int) -> bool:
        """Check lightweight filters (name, size, extension) before hashing."""
        for filter_config in self.active_filters:
            plugin_key = filter_config.get('plugin_key')
            params = filter_config.get('params', {})

            # Applying quick filters
            if plugin_key in ['extension_filter', 'filesize_filter', 'filename_filter']:
                result = self.plugin_manager.execute(plugin_key, [file_path], **params)
                if not result or file_path not in result:
                    return False  # Rejected by this filter

        return True  # Passed all quick filters

class FileManagerCopyMove(FileManagerBase):
    """Specialized file manager for copy operations."""

    _global_dest_contents = {}  # Tracking dest contents to do the proper renaming on disk

    def __init__(self, source_dir, operation: FileOperation = FileOperation.COPY, shared_results=None):
        super().__init__(source_dir, shared_results)

        if shared_results is not None:
            self.shared_results = shared_results
        else:
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
        self.operation = operation
        self.source_dir = source_dir
        self.processing_thread = None
        self.processed_files = None
        self.results_lock = None
        self.is_running = False
        self.global_dest_lock = {}  # Shared across all processing threads

    @classmethod
    def reset_global_dest_contents(cls):
        """Reset global state before starting a new operation batch."""
        cls._global_dest_contents.clear()

    def _generate_unique_filename(self, dest_dir, file_name, file_size):
        """
        Generate a unique filename in destination directory.
        If conflict exists, append _1, _2, etc. before extension.

        Examples:
            1.txt -> 1.txt (if not exists)
            1.txt -> 1_1.txt (if 1.txt exists with different size)
            1.txt -> 1_2.txt (if 1_1.txt exists too)
        """
        dest_path = os.path.join(dest_dir, file_name)

        if dest_dir not in FileManagerCopyMove._global_dest_contents:
            FileManagerCopyMove._global_dest_contents[dest_dir] = set()

        # Checking both filesystem AND what is currently being processed
        exists_on_disk = os.path.exists(dest_path)
        exists_in_memory = dest_path in FileManagerCopyMove._global_dest_contents[dest_dir]

        # No conflict - return original filename
        if not exists_on_disk and not exists_in_memory:
            return file_name, dest_path

        # Different file with same name - auto-rename
        name_parts = os.path.splitext(file_name)  # ('1', '.txt')
        base_name = name_parts[0]
        extension = name_parts[1]

        counter = 1
        while True:
            new_name = f"{base_name}_{counter}{extension}"
            new_path = os.path.join(dest_dir, new_name)

            exists_on_disk = os.path.exists(new_path)
            exists_in_memory = new_path in FileManagerCopyMove._global_dest_contents[dest_dir]

            if not exists_on_disk and not exists_in_memory:
                return new_name, new_path

            counter += 1

    def _get_or_create_dest_lock(self, dest_dir):
        """Get or create a lock for a destination directory (thread-safe)."""
        if dest_dir not in self.global_dest_lock:
            self.global_dest_lock[dest_dir] = threading.Lock()
        return self.global_dest_lock[dest_dir]

    # Non-blocking copy starter
    def start_operation_thread(self, filtered_files_dict, dest_dirs, processed_files, preserve_structure):
        """
        Start file operation (copy/move) in a background thread (non-blocking).

        Args:
            filtered_files_dict: Dictionary with files to process
            dest_dirs: Destination directories
            processed_files: Set to track processed files
            preserve_structure: Structure preservation strategy

        After calling this, use:
        - pause_processing() to pause
        - resume_processing() to resume
        - stop_processing() to stop
        - get_results() to check progress
        """
        try:
            if self.processing_thread is None or not self.processing_thread.is_alive():
                with self._pause_condition:
                    self._paused = False
                    self._stop = False

                self.processed_files = processed_files
                self.is_running = True

                self.processing_thread = threading.Thread(
                    target=self.execute_operation,
                    args=(filtered_files_dict, dest_dirs, preserve_structure),
                    daemon=False,
                    name=f"FileOp-{self.operation.value}-{os.path.basename(self.source_dir)}"
                )
                self.processing_thread.start()
                logger.debug(f"{self.operation.value.capitalize()} thread started for {self.source_dir}")
            else:
                logger.debug(f"{self.operation.value.capitalize()} thread is already running.")
        except Exception as e:
            logger.error(f"Failed to start operation thread: {e}", exc_info=True)
            self.processing_thread = None  # Ensure it's None if creation fails
            raise

    def execute_operation(self, filtered_files_dict, dest_dirs, preserve_structure):
        """
        Execute file operation (copy or move) on processed files.

        Args:
            filtered_files_dict: Dictionary from shared_results with key=(file_name, file_size, file_hash)
            dest_dirs: Destination/destinations directory path
            preserve_structure: If 0 - recreate folder structure DEST + SOURCE_PARENT_REST;
                                1 - recreate folder structure DEST + SOURCE; ELSE -1 flatten to root

        This method now runs in a background thread when called via start_operation_thread().
        It checks pause/stop events to allow control.
        """

        try:
            # Get files from this source directory only
            source_files = [
                src_path for (file_name, file_size, file_hash), src_path in filtered_files_dict.items()
                if os.path.dirname(src_path) == self.source_dir
            ]

            if not source_files:
                logger.debug(f"No files found in {self.source_dir}")
                return

            # Create a mapping for quick metadata lookup
            # Key: source_path, Value: (file_name, file_size, file_hash)
            # Then using at redo operations for copy/move without comparison_table
            file_metadata_map = {}
            for (file_name, file_size, file_hash), src_path in filtered_files_dict.items():
                if os.path.dirname(src_path) == self.source_dir:
                    file_metadata_map[src_path] = (file_name, file_size, file_hash)

            # For MOVE with multiple dests, using copy and then delete strategy
            is_multiple_move = (
                self.operation == FileOperation.MOVE and len(dest_dirs) > 1
            )

            logger.debug(f"Found {len(source_files)} files in {self.source_dir}")
            logger.debug(f"Using copy-delete strategy for multiple moves: {is_multiple_move}")
            # logger.debug(f"file_metadata_map = {file_metadata_map}")

            # Delete all files after copying completes
            files_to_delete_multiple_move = []

            # Process each destination (sequential within this thread)
            for dest_idx, dest_dir in enumerate(dest_dirs):
                # Check pause before starting a new destination
                self._handle_pause()

                # Check stop before processing
                with self._pause_condition:
                    if self._stop:
                        logger.debug("Stop signal received before processing destination.")
                        break

                is_last_destination = (dest_idx == len(dest_dirs) - 1)

                # Get GLOBAL lock for this destination (shared across all threads)
                dest_lock = self._get_or_create_dest_lock(dest_dir)

                for src_file in source_files:
                    # Handle pause with stop check inside
                    self._handle_pause()

                    # Check stop before processing file
                    with self._pause_condition:
                        if self._stop:
                            logger.debug("Stop signal received. Exiting operation.")
                            return self.shared_results

                    # Skip if already processed
                    operation_key = (src_file, dest_dir)
                    with self.results_lock:
                        if operation_key in self.processed_files:
                            logger.debug(f"Skipping {src_file} -> {dest_dir} (already {self.operation.value}d)")
                            continue
                        self.processed_files.add(operation_key)

                    try:
                        target_dir = preserve_structure.compute_target_dir(src_file, dest_dir)
                        os.makedirs(target_dir, exist_ok=True)

                        # Process file with rename handling
                        file_name = os.path.basename(src_file)
                        file_size = os.path.getsize(src_file)
                        file_hash = file_metadata_map.get(src_file, (file_name, file_size, None))[2]

                        # Lock before checking file_name uniqueness to prevent races
                        with dest_lock:
                            unique_name, dest_path = self._generate_unique_filename(
                                target_dir, file_name, file_size
                            )

                            # Registering this path immediately to prevent other threads from using it
                            if target_dir not in FileManagerCopyMove._global_dest_contents:
                                FileManagerCopyMove._global_dest_contents[target_dir] = set()
                            FileManagerCopyMove._global_dest_contents[target_dir].add(dest_path)

                            # For multi-destination MOVE: always copy first
                            if not is_multiple_move:
                                # Execute the appropriate operation
                                if self.operation == FileOperation.COPY:
                                    shutil.copy2(src_file, dest_path)
                                elif self.operation == FileOperation.MOVE:
                                    shutil.move(src_file, dest_path)
                            else:
                                if not is_last_destination:
                                    shutil.copy2(src_file, dest_path)
                                else:
                                    shutil.move(src_file, dest_path)
                                    files_to_delete_multiple_move.append(src_file)

                        # Using lock when updating shared_results
                        with self.results_lock:
                            self.shared_results["total_processed"] += 1
                            self.shared_results["total_size"] += file_size
                            self.shared_results["unique_dest_paths"].add(dest_path)
                            self.shared_results["source_file_paths"].add(src_file)

                            # Storing metadata for this file operation
                            if "processed_files_metadata" not in self.shared_results:
                                self.shared_results["processed_files_metadata"] = self.shared_results.get(
                                    "processed_files_metadata", {}
                                )
                            operation_src_dest_key: FileMetadataKey = (src_file, dest_path)
                            self.shared_results["processed_files_metadata"][operation_src_dest_key] = {
                                "file_name": file_name,
                                "file_size": file_size,
                                "file_hash": file_hash,
                                "source_path": src_file,
                                "destination_path": dest_path,
                                "renamed": unique_name != file_name
                            }

                            if unique_name != file_name:
                                self.shared_results["renamed_files"].append({
                                    "original": src_file,
                                    "renamed_to": unique_name,
                                    "destination": dest_path,
                                    "size": file_size
                                })

                    except Exception as e:
                        with self.results_lock:
                            self.shared_results["errors"].append({
                                "file": src_file,
                                "destination": dest_dir,
                                "error": str(e),
                                "operation": self.operation.value

                            })
                        logger.error(f"Failed to {self.operation.value} {src_file}: {e}")

                # Check stop before moving to next destination
                with self._pause_condition:
                    if self._stop:
                        logger.debug("Stop signal received between destinations.")
                        break

            # Delete source files after all copies complete (for multi-destination MOVE)
            if is_multiple_move:
                with self._pause_condition:
                    should_delete = not self._stop

                if should_delete:
                    logger.debug(f"Deleting {len(files_to_delete_multiple_move)} source files after multi-destination move")
                    for src_file in files_to_delete_multiple_move:
                        try:
                            if os.path.exists(src_file):
                                os.remove(src_file)
                                logger.debug(f"Deleted source file: {src_file}")
                        except Exception as e:
                            with self.results_lock:
                                self.shared_results["errors"].append({
                                    "file": src_file,
                                    "error": f"Failed to delete source after multiple move: {str(e)}",
                                    "operation": "cleanup"
                                })
                            logger.error(f"Failed to delete {src_file}: {e}")

            logger.debug(f"{self.operation.value.capitalize()} operation completed.")
            return self.shared_results

        except Exception as e:
            with self.results_lock:
                self.shared_results["errors"].append({
                    "file": "operation",
                    "error": f"Critical error during {self.operation.value}: {str(e)}"
                })
            logger.error(f"Critical error in execute_operation: {e}", exc_info=True)
            return self.shared_results

        finally:
            self.is_running = False
            logger.debug(f"[{self.processing_thread.name}] Finished")

    def get_results(self):
        """Safely retrieve accumulated results."""
        with self.results_lock:
            return self.shared_results

class FileManagerParallel:
    def __init__(self, directory_path, num_workers=2):
        """
        Initialize the parallel file manager.

        Args:
            directory_path: Path to the directory containing files to process
            num_workers: Number of worker processes (default 2)
        """
        self.directory_path = directory_path
        self.num_workers = num_workers

        # IPC mechanisms for inter-process communication
        self.task_queue = multiprocessing.Queue()
        self.result_queue = multiprocessing.Queue()
        self.pause_event = multiprocessing.Event()
        self.stop_event = multiprocessing.Event()
        self.resume_event = multiprocessing.Event()
        self.resume_event.set()  # Allow processing to start unpaused

        self.worker_processes = []
        self.coordinator_thread = None
        self.is_paused = False
        self.is_running = False

    @staticmethod
    def _worker_process(task_queue, result_queue, pause_event, stop_event, resume_event):
        """
        Worker process that consumes tasks from the queue and processes files.
        Runs independently in its own process (no GIL constraints).
        """
        logger.info(f"Worker process {os.getpid()} started.")

        try:
            while not stop_event.is_set():
                try:
                    # Non-blocking get with timeout to allow periodic stop checks
                    file_path = task_queue.get(timeout=0.5)
                    file_path = os.path.normpath(file_path)  # Normalize to OS standard
                    # Check stop before processing
                    if stop_event.is_set():
                        break

                    # Handle pause
                    while pause_event.is_set():
                        if stop_event.is_set():
                            break
                        # Short sleep to reduce CPU spinning
                        time.sleep(0.1)

                    if stop_event.is_set():
                        break

                    # Process the file
                    FileManagerThree._process_file(file_path)
                    result_queue.put(("success", file_path))

                except Empty:
                    # Queue is empty, continue checking for stop/pause
                    continue
                except Exception as e:
                    result_queue.put(("error", str(e)))

        except Exception as exc:
            logger.error(f"Worker {os.getpid()} exception: {exc}")
        finally:
            logger.debug(f"Worker process {os.getpid()} finished.")

    @staticmethod
    def _process_file(file_path):
        """Simulates processing a single file (runs in worker process)."""
        logger.debug(f"[PID {os.getpid()}] Processing file: {os.path.basename(file_path)}")
        time.sleep(1)  # Simulate work
        file_path = os.path.normpath(file_path)  # Normalize to OS standard
        logger.debug(f"[PID {os.getpid()}] Finished processing: {os.path.basename(file_path)}")

    def _coordinator_worker(self):
        """
        Runs in the main process to coordinate worker processes and handle results.
        """
        logger.info(f"Coordinator thread started (PID {os.getpid()}).")

        try:
            files = [f for f in os.listdir(self.directory_path)
                     if os.path.isfile(os.path.join(self.directory_path, f))]

            # Enqueue all files
            for file_name in files:
                if self.stop_event.is_set():
                    break
                file_path = os.path.join(self.directory_path, file_name)
                file_path = os.path.normpath(file_path)  # Normalize to OS standard
                self.task_queue.put(file_path)

            # Monitor results until all files are processed or stop is called
            results_received = 0
            total_files = len(files)

            while results_received < total_files and not self.stop_event.is_set():
                try:
                    status, data = self.result_queue.get(timeout=1)
                    results_received += 1
                    if status == "error":
                        logger.error(f"Error processing file: {data}")
                    else:
                        logger.debug(f"Coordinator: Processed {results_received}/{total_files}. Time {time.perf_counter()}")

                except Empty:
                    # Check if stop was called while waiting
                    if self.stop_event.is_set():
                        break
                    continue

        except Exception as e:
            logger.error(f"Coordinator exception: {e}")
        finally:
            logger.debug("Coordinator finished.")

    def start_processing(self):
        """Starts the parallel file processing with worker processes."""
        if self.is_running:
            logger.info("Processing is already running.")
            return

        # Reset events
        self.stop_event.clear()
        self.pause_event.clear()
        self.resume_event.set()
        self.is_paused = False
        self.is_running = True

        # Start worker processes
        self.worker_processes = []
        for i in range(self.num_workers):
            p = Process(
                target=self._worker_process,
                args=(
                    self.task_queue,
                    self.result_queue,
                    self.pause_event,
                    self.stop_event,
                    self.resume_event,
                ),
                daemon=False,
                name=f"FileWorker-{i + 1}"
            )
            p.start()
            self.worker_processes.append(p)

        # Start coordinator thread
        import threading
        self.coordinator_thread = threading.Thread(
            target=self._coordinator_worker,
            daemon=False,
            name="Coordinator"
        )
        self.coordinator_thread.start()

        logger.debug(f"Processing started with {self.num_workers} worker processes.")

    def pause_processing(self):
        """Pauses all worker processes."""
        if not self.is_running or self.is_paused:
            logger.info("No active processing to pause or already paused.")
            return

        logger.info("Sending pause signal to all workers...")
        self.pause_event.set()
        self.is_paused = True
        logger.info("Processing paused.")

    def resume_processing(self):
        """Resumes all worker processes."""
        if not self.is_running or not self.is_paused:
            logger.info("Processing is not paused.")
            return

        logger.info("Sending resume signal to all workers...")
        self.resume_event.set()
        self.pause_event.clear()
        self.is_paused = False
        logger.info("Processing resumed.")

    def stop_processing(self):
        """Stops all worker processes and the coordinator."""
        if not self.is_running:
            logger.info("No active processing to stop.")
            return

        logger.info("Sending stop signal to all workers...")
        self.stop_event.set()

        # Unblock paused workers if necessary
        if self.is_paused:
            self.pause_event.clear()
            self.resume_event.set()

        # Wait for all worker processes to terminate
        for p in self.worker_processes:
            p.join(timeout=5)
            if p.is_alive():
                logger.warning(f"Worker {p.name} did not terminate; terminating forcefully.")
                p.terminate()
                p.join()

        # Wait for coordinator thread
        if self.coordinator_thread:
            self.coordinator_thread.join(timeout=5)
            if self.coordinator_thread.is_alive():
                logger.warning("Coordinator thread did not terminate.")

        self.worker_processes = []
        self.coordinator_thread = None
        self.is_running = False
        self.is_paused = False

        logger.info("Processing stopped.")

    def cleanup(self):
        """Ensure all processes are cleaned up."""
        if self.is_running:
            self.stop_processing()

if __name__ == "__main__":
    # Create a dummy directory and some dummy files for testing
    dummy_dir = "test_files_for_processing"
    os.makedirs(dummy_dir, exist_ok=True)
    for i in range(10):
        with open(os.path.join(dummy_dir, f"file_{i+1}.txt"), "w") as f:
            f.write(f"Content of file {i+1}")

    file_manager = FileManagerParallel(dummy_dir, num_workers=2)

    logger.debug(f"Enter Starting processing. Time {time.perf_counter()}")
    logger.info("--- Starting processing ---")
    file_manager.start_processing()
    logger.debug(f"Starting processing time.sleep(3). Time {time.perf_counter()}\n")
    time.sleep(3)  # Let it process a few files in parallel
    logger.debug(f"Exit Starting processing. Time {time.perf_counter()}\n")

    logger.debug(f"Enter Pausing processing. Time {time.perf_counter()}")
    logger.info("--- Pausing processing ---")
    file_manager.pause_processing()
    logger.debug(f"Pausing processing time.sleep(3). Time {time.perf_counter()}\n")
    time.sleep(3)  # Keep it paused for a bit
    logger.debug(f"Exit Pausing processing. Time {time.perf_counter()}\n")

    logger.debug(f"Enter Resuming processing. Time {time.perf_counter()}")
    logger.info("--- Resuming processing ---")
    file_manager.resume_processing()
    logger.debug(f"Resuming processing time.sleep(4). Time {time.perf_counter()}\n")
    time.sleep(4)  # Let it finish
    logger.debug(f"Exit Resuming processing. Time {time.perf_counter()}\n")

    logger.debug(f"Enter Stopping processing. Time {time.perf_counter()}")
    logger.info("--- Stopping processing ---")
    logger.debug(f"Stopping processing. Time {time.perf_counter()}\n")
    file_manager.stop_processing()
    logger.debug(f"Exit Stopping processing. Time {time.perf_counter()}\n")

    # Clean up dummy files and directory
    for f_name in os.listdir(dummy_dir):
        os.remove(os.path.join(dummy_dir, f_name))
    os.rmdir(dummy_dir)
    logger.debug("Dummy files and directory cleaned up.")
