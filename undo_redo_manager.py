from enum import Enum
from enums import ProcessingMode, FileOperation, PreserveStructureType
from operation_history import OperationSnapshot, OperationHistory, FileStateEntry
from typing import List, Dict, Optional
from processing_thread import ProcessingThreadCopyMove, ProcessingThreadCopyMoveResult
import os
import shutil
import logging
import pyqt5_loader
from PyQt5.QtCore import QObject, pyqtSignal, QEventLoop, QCoreApplication, Qt, QThread, pyqtSlot
import sys

logger = logging.getLogger(__name__)

class UndoRedoManager(QObject):
    """
    Handles undo/redo operations using OperationHistory for state management.
    Emits signals when operations complete.
    """

    # Signals for async notification
    operation_started = pyqtSignal(str)  # "Redoing COPY..."
    operation_completed = pyqtSignal(ProcessingThreadCopyMoveResult)  # results ProcessingThreadCopyMoveResult
    operation_failed = pyqtSignal(str)  # error message

    def __init__(self, source_dirs: List[str], dest_dirs: List[str], base_dest_paths: List[str],
                 preserve_structure_enum: PreserveStructureType, processing_mode: ProcessingMode):
        super().__init__()
        self.source_dirs = source_dirs
        self.dest_dirs = dest_dirs
        self._base_dest_paths = base_dest_paths
        self.preserve_structure_enum = preserve_structure_enum
        self.processing_mode = processing_mode
        self.processing_thread = None
        self.operation_history = OperationHistory()
        self.active_threads = []  # Track all running threads

    @property
    def base_dest_paths(self) -> List[str]:
        return self._base_dest_paths

    @base_dest_paths.setter
    def base_dest_paths(self, base_dest_paths: List[str]):
        self._base_dest_paths = base_dest_paths

    def record_operation(self, processing_result) -> str:
        """
        Record a new operation via OperationHistory.

        Args:
            processing_result: ProcessingThreadCopyMoveResult with operation details

        Returns:
            Operation ID
        """
        return self.operation_history.record_operation_from_result(processing_result)

    def undo(self) -> bool:
        """
        Undo the last operation.

        Returns:
             True if undo was performed, False if nothing to undo
        """
        snapshot = self.operation_history.undo()
        if snapshot is None:
            self.operation_failed.emit("Nothing to undo")
            return False

        self._execute_undo(snapshot)
        return True
    
    def redo(self) -> bool:
        """
        Redo the last undone operation.
        
        Returns:
            True if redo was performed, False if nothing to redo
        """
        snapshot = self.operation_history.redo()
        if snapshot is None:
            self.operation_failed.emit("Nothing to redo")
            return False

        self._execute_redo(snapshot)
        return True

    def can_undo(self) -> bool:
        """Check if undo is available."""
        return self.operation_history.can_undo()

    def can_redo(self) -> bool:
        """Check if redo is available."""
        return self.operation_history.can_redo()

    def get_undo_description(self) -> Optional[str]:
        """Get description of next undo operation."""
        return self.operation_history.get_undo_description()

    def get_redo_description(self) -> Optional[str]:
        """Get description of next redo operation."""
        return self.operation_history.get_redo_description()

    def clear_history(self) -> None:
        """Clear all undo/redo history."""
        self.operation_history.clear()

    def _execute_undo(self, snapshot: OperationSnapshot) -> None:
        """Execute the undo operation based on snapshot."""
        try:
            if snapshot.file_operation == FileOperation.COPY:
                self._undo_copy_operation(snapshot)
            elif snapshot.file_operation == FileOperation.MOVE:
                self._undo_move_operation(snapshot)
            else:
                self.operation_failed.emit(
                    f"Unknown operation type: {snapshot.file_operation}"
                )
        except Exception as e:
            logger.error(f"Failed to execute undo: {str(e)}", exc_info=True)
            self.operation_failed.emit(f"Failed to undo: {str(e)}")

    def _execute_redo(self, snapshot: OperationSnapshot) -> None:
        """Execute the redo operation based on snapshot."""
        try:
            if snapshot.file_operation == FileOperation.COPY:
                self._redo_copy_operation(snapshot)
            elif snapshot.file_operation == FileOperation.MOVE:
                self._redo_move_operation(snapshot)
            else:
                self.operation_failed.emit(
                    f"Unknown operation type: {snapshot.file_operation}"
                )
        except Exception as e:
            logger.error(f"Failed to execute redo: {str(e)}", exc_info=True)
            self.operation_failed.emit(f"Failed to redo: {str(e)}")

    def _redo_copy_operation(self, snapshot: OperationSnapshot) -> None:
        """Redo a COPY operation."""
        logger.info(">>> _redo_copy_operation START")
        try:
            logger.info(">>> Reconstructing files dict...")
            selected_files_dict = self._reconstruct_files_dict(snapshot)
            logger.debug(f">>> Files dict reconstructed: {len(selected_files_dict)} items")

            logger.info(">>> Extracting dest dirs...")
            dest_dirs = self.base_dest_paths
            logger.debug(f">>> Dest dirs extracted: {dest_dirs}")

            logger.info(">>> Instantiating preserve structure...")
            preserve_structure = snapshot.preserve_structure_type.instantiate_strategy()
            logger.debug(f">>> Preserve structure: {preserve_structure}")

            if not selected_files_dict or not dest_dirs:
                logger.error(">>> Invalid snapshot data")
                self.operation_failed.emit(f"Invalid snapshot data for COPY redo")
                return

            logger.info(">>> Creating ProcessingThreadCopyMove...")
            num_workers = 2 if self.processing_mode == ProcessingMode.PARALLEL else 1

            self.processing_thread = ProcessingThreadCopyMove(
                self.processing_mode,
                selected_files_dict,
                dest_dirs,
                FileOperation.COPY,
                num_workers,
                preserve_structure
            )
            logger.info(">>> ProcessingThreadCopyMove created successfully")

            self.active_threads.append(self.processing_thread)
            logger.info(">>> Thread appended to active_threads")

            # Connect to methods instead of lambdas
            logger.info(">>> Connecting processing_finished signal...")
            self.processing_thread.processing_finished.connect(
                self._on_redo_copy_finished,
                Qt.ConnectionType.QueuedConnection  # Safe from worker thread
            )
            logger.info(">>> Signal connected")

            logger.info(">>> Connecting processing_failed signal...")
            if hasattr(self.processing_thread, 'processing_failed'):
                self.processing_thread.processing_failed.connect(
                    self._handle_thread_error
                )
            logger.info(">>> Error signal connected")

            logger.info(">>> Emitting operation_started...")
            self.operation_started.emit(f"Redoing COPY operation...")
            logger.info(">>> operation_started emitted")

            logger.info(">>> Starting thread...")
            self.processing_thread.start()
            logger.info(">>> Thread started successfully")

        except Exception as e:
            logger.error(f"Failed to start COPY redo: {str(e)}", exc_info=True)
            self.operation_failed.emit(f"Failed to start redo: {str(e)}")

    @pyqtSlot(object)
    def _on_redo_copy_finished(self, result):
        """Handle redo copy completion."""
        logger.info("<<< _on_redo_copy_finished - entered")
        logger.debug(f"<<< result type: {type(result)}")
        logger.debug(f"<<< result: {result}")
        try:
            logger.info("<<< Before operation_completed.emit(result)")
            self.operation_completed.emit(result)
            # self._on_operation_finished(result, 'COPY', is_redo=True)
            logger.info("<<< After operation_completed.emit(result)")
        except Exception as e:
            logger.error(f"<<< Exception in operation_completed.emit(result): {e}")
            raise
        finally:
            if hasattr(self, 'processing_thread') and self.processing_thread:
                # self._cleanup_thread(self.processing_thread)
                logger.info("<<< begin if self.processing_thread in self.active_threads:")
                if self.processing_thread in self.active_threads:
                    self.active_threads.remove(self.processing_thread)
                logger.info("<<< after if self.processing_thread in self.active_threads:")
                self.processing_thread.deleteLater()  # Schedule deletion
                logger.info("<<< after self.processing_thread.deleteLater()")
                logger.info("Redo thread scheduled for deletion, copy operation")

    def _cleanup_thread(self, thread: QThread):
        """Remove thread reference after it finishes."""
        try:
            # thread.wait() # Ensure thread has fully terminated but the other side it can cause the deadlock
            if thread in self.active_threads:
                self.active_threads.remove(thread)
            thread.deleteLater() # Schedule deletion
        except Exception as e:
            logger.error(f"Error cleaning up thread: {str(e)}")

    @pyqtSlot()
    def _handle_thread_error(self, error: str) -> None:
        """Handle errors from worker thread."""
        logger.error(f"Processing thread error: {error}")
        self.operation_failed.emit(error)

    def _redo_move_operation(self, snapshot: OperationSnapshot) -> None:
        """Redo a MOVE operation."""
        try:
            selected_files_dict = self._reconstruct_files_dict(snapshot)
            dest_dirs = self.base_dest_paths
            preserve_structure = snapshot.preserve_structure_type.instantiate_strategy()

            if not selected_files_dict or not dest_dirs:
                self.operation_failed.emit(f"Invalid snapshot data for MOVE redo")
                return

            num_workers = 2 if self.processing_mode == ProcessingMode.PARALLEL else 1

            self.processing_thread = ProcessingThreadCopyMove(
                self.processing_mode,
                selected_files_dict,
                dest_dirs,
                FileOperation.MOVE,
                num_workers,
                preserve_structure
            )

            self.active_threads.append(self.processing_thread)

            self.processing_thread.processing_finished.connect(
                self._on_redo_move_finished,
                Qt.ConnectionType.QueuedConnection  # Safe from worker thread
            )

            if hasattr(self.processing_thread, 'processing_failed'):
                self.processing_thread.processing_failed.connect(
                    self._handle_thread_error
                )

            self.operation_started.emit(f"Redoing MOVE operation...")
            self.processing_thread.start()

        except Exception as e:
            logger.error(f"Failed to start MOVE redo: {str(e)}", exc_info=True)
            self.operation_failed.emit(f"Failed to start redo: {str(e)}")

    @pyqtSlot(object)
    def _on_redo_move_finished(self, result):
        """Handle redo move completion."""
        try:
            self.operation_completed.emit(result)
        except Exception as e:
            raise
        finally:
            if hasattr(self, 'processing_thread') and self.processing_thread:
                if self.processing_thread in self.active_threads:
                    self.active_threads.remove(self.processing_thread)
                self.processing_thread.deleteLater()  # Schedule deletion
                logger.info("Redo thread scheduled for deletion, move operation")

    def _undo_copy_operation(self, snapshot: OperationSnapshot) -> None:
        """Undo a COPY operation by deleting copied files."""
        total_processed = 0
        total_skipped = 0
        errors = []
        unique_folders = set()
        our_destination_files = set()  # Track all files we're deleting, to not deleting folder if there are not ours

        try:
            # Step 1: Delete only the files that were copied, collect their parent folders
            self.preserve_structure_enum = snapshot.preserve_structure_type
            for file_entry, dest_paths in snapshot.after_state.items():
                for dest_path in dest_paths:
                    try:
                        if os.path.exists(dest_path):
                            os.remove(dest_path)
                            total_processed += 1
                            our_destination_files.add(dest_path)
                            logger.info(f"Undo COPY: deleted {dest_path}")

                            if self.preserve_structure_enum != PreserveStructureType.NONE:
                                unique_folders = self._collect_folder_for_cleanup(
                                    dest_path=dest_path, unique_folders=unique_folders
                                )

                    except Exception as e:
                        total_skipped += 1
                        error_msg = f"Failed to delete {dest_path}: {str(e)}"
                        errors.append(error_msg)
                        logger.error(error_msg)

            # Step 2: Safely delete only EMPTY parent directories
            if unique_folders:
                try:
                    self._delete_empty_folders_batch(
                        folders_to_delete=unique_folders,
                        our_destination_files=our_destination_files,
                        base_dest_paths=self.base_dest_paths)
                except Exception as e:
                    error_msg = f"Some folders could not be deleted: {str(e)}"
                    errors.append(error_msg)
                    logger.warning(error_msg)

            result = ProcessingThreadCopyMoveResult(
                operation_type=FileOperation.COPY_UNDO,
                total_processed=total_processed,
                total_skipped=total_skipped,
                errors=errors
            )

            self.operation_started.emit(f"Undo COPY completed: {total_processed} files deleted")
            self.operation_completed.emit(result)

        except Exception as e:
            logger.error(f"Critical error during COPY undo: {str(e)}", exc_info=True)
            self.operation_failed.emit(str(e))

    def _delete_empty_folders_batch(
            self,
            folders_to_delete: set,
            our_destination_files: set,
            base_dest_paths: list
    ) -> None:
        """
        Safely delete only folders that contain NO FILES (empty folders and their empty subfolders).
        Preserves the root destination folders that users explicitly configured.

        Args:
            folders_to_delete: Set of folder paths to attempt deletion
            our_destination_files: Pre-compute set of all destination files we moved (O(1) lookup)
            base_dest_paths: List of base destination paths to preserve
        """
        # Converting for faster lookup
        base_dest_paths_set = set(base_dest_paths)

        def contains_only_our_files(folder_path: str) -> bool:
            """Check if folder contains ONLY files we moved.

            Returns True if safe to delete (empty OR only our files).
            Returns False if contains files we didn't move.
            """
            try:
                for root, dirs, files in os.walk(folder_path):
                    for file_name in files:
                        file_path = os.path.join(root, file_name)

                        # If even ONE file isn't in our destination set, we can't delete
                        if file_path not in our_destination_files:
                            logger.info(f"File not created by us: {file_path} - preserving folder")
                            return False  # Contains files we didn't create

                return True  # All files were ours (or folder is empty)

            except Exception as e:
                logger.error(f"Error checking folder contents: {folder_path}, {str(e)}")
                return False  # Assume it has files if we didn't create (safer)

        # Sort by depth (deepest first) to delete nested empty folders first
        sorted_folders = sorted(folders_to_delete, key=lambda x: x.count(os.sep), reverse=True)

        for folder in sorted_folders:
            try:
                # Skip if this folder is one of the configured destinations
                if folder in base_dest_paths_set:
                    logger.info(f"Skipping base destination: {folder}")
                    continue

                if os.path.exists(folder):
                    # Only delete if the folder contains ONLY our files
                    if contains_only_our_files(folder):
                        shutil.rmtree(folder)
                        logger.info(f"Deleted folder (contained only our files): {folder}")
                    else:
                        logger.info(f"Folder contains files not created by us, skipping: {folder}")
                else:
                    logger.info(f"Folder does not exist: {folder}")
            except Exception as e:
                logger.error(f"Could not delete folder {folder}: {str(e)}")

    def _get_first_level_below_base(self, file_path: str, base_dest_paths: list) -> str:
        """
        Get the first folder level below any base_dest_path.

        Example:
            - file_path: D:\Dest1\Any\Builder_book\Source\C1\file.txt
            - base_dest_paths: [D:\Dest1]
            - Returns: D:\Dest1\Any (the first folder created under base)
        """
        for base_path in base_dest_paths:
            if file_path.startswith(base_path):
                # Remove the base path, get the remainder
                remainder = file_path[len(base_path):].lstrip(os.sep)

                # Split into parts and take the first folder
                parts = remainder.split(os.sep)
                if parts:
                    first_folder = os.path.join(base_path, parts[0])
                    return first_folder

        return None

    def _undo_move_operation(self, snapshot: OperationSnapshot) -> None:
        """Undo a MOVE operation by moving files back to their sources and restoring original names."""
        total_processed = 0
        failed = 0
        total_skipped = 0
        deleted = 0  # Tracking deleted duplicate files
        errors = []
        unique_folders = set()
        # restored_sources = set() # Track which sources have been restored

        try:
            # Using processed_files_metadata to handle renamed files correctly
            self.preserve_structure_enum = snapshot.preserve_structure_type
            if hasattr(snapshot, "processed_files_metadata") and snapshot.processed_files_metadata:
                for (source_path, dest_path), file_meta in snapshot.processed_files_metadata.items():
                    try:
                        if os.path.exists(dest_path):
                            # Check if source was already restored by a previous operation
                            if os.path.exists(source_path):
                                # File already restored from another destination-this is a duplicate
                                if self.preserve_structure_enum == PreserveStructureType.NONE:
                                    try:
                                        os.remove(dest_path)
                                        logger.info(f"Undo MOVE (deleted duplicate): {dest_path}")
                                        deleted += 1
                                    except Exception as e:
                                        logger.error(f"Failed to delete duplicate {dest_path}: {str(e)}")
                                        failed += 1
                                else:
                                    # With preserved structure, skip (folder cleanup will handle it)
                                    logger.info(f"Undo MOVE (skipped): source {source_path} already exists, "
                                                f"skipping {dest_path}")
                                    total_skipped += 1

                                    # Collect folder for cleanup
                                    unique_folders = self._collect_folder_for_cleanup(
                                        dest_path=dest_path, unique_folders=unique_folders
                                    )
                                continue

                            # Ensure source directory exists
                            source_dir = os.path.dirname(source_path)
                            os.makedirs(source_dir, exist_ok=True)

                            # Move file back to source
                            shutil.move(dest_path, source_path)

                            # Check if file was renamed during the move operation
                            was_renamed = file_meta.get('renamed', False)
                            if was_renamed:
                                # File was renamed: need to restore original name
                                # dest_path has the renamed name (e.g., Dest1/1_1.txt
                                # source_path is the original location (e.g., C2/1.txt)
                                # file_meta['file_name'] is the original filename (e.g., 1.txt)

                                logger.info(f"Undo MOVE (renamed): moved {dest_path} => {source_path} "
                                            f"(restored original name: {file_meta['file_name']})")

                            else:
                                # File was not renamed: simple move back
                                logger.info(f"Undo MOVE: moved {dest_path} => {source_path}")

                            total_processed += 1

                            # Collect parent folder of destination for cleanup
                            if self.preserve_structure_enum != PreserveStructureType.NONE:
                                unique_folders = self._collect_folder_for_cleanup(
                                    dest_path=dest_path, unique_folders=unique_folders
                                )

                    except Exception as e:
                        failed += 1
                        error_msg = f"Failed to move {dest_path} back to {source_path}: {str(e)}"
                        errors.append(error_msg)
                        logger.error(error_msg)
            else:
                logger.error("processed_files_metadata not available-cannot undo MOVE operation properly")
                failed += 1

            # Step 2: Delete empty folders at destinations
            if self.preserve_structure_enum != PreserveStructureType.NONE:
                if unique_folders:
                    try:
                        # Pre-compute set of all destination files we moved (O(1) lookup)
                        our_destination_files = {
                            dest_path for (source_path, dest_path) in snapshot.processed_files_metadata.keys()
                        }
                        self._delete_empty_folders_batch(unique_folders, our_destination_files, self.base_dest_paths)
                    except Exception as e:
                        error_msg = f"Some folders could not be deleted: {str(e)}"
                        errors.append(error_msg)
                        logger.warning(error_msg)

            result = ProcessingThreadCopyMoveResult(
                operation_type=FileOperation.MOVE_UNDO,
                total_processed=total_processed,
                total_skipped=total_skipped,
                errors=errors
            )

            self.operation_started.emit(f"Undo MOVE completed: {total_processed} files restored, {deleted} deleted (duplicates_, "
                                        f"{total_skipped} skipped")
            self.operation_completed.emit(result)

        except Exception as e:
            logger.error(f"Critical error during MOVE undo: {str(e)}", exc_info=True)
            self.operation_failed.emit(str(e))

    def _collect_folder_for_cleanup(self, dest_path: str, unique_folders: set) -> set:
        """
        Collect folder for cleanup

        Args:
            dest_path: Path to the deleted file
            unique_folders: Set to accumulate folders (passed by reference, also returned)

        Returns:
            Updated set of unique folders to cleanup
        """
        try:
            dest_parent = os.path.dirname(dest_path)
            if self.preserve_structure_enum == PreserveStructureType.SOURCE_PARENT_REST:
                intermediate_folder = os.path.dirname(dest_parent)
                if intermediate_folder not in self.base_dest_paths:
                    unique_folders.add(intermediate_folder)
                    logger.debug(f"Added for cleanup (SOURCE_PARENT_REST): {intermediate_folder}")

            elif self.preserve_structure_enum == PreserveStructureType.FULL_SOURCE:
                first_level = self._get_first_level_below_base(dest_path, self.base_dest_paths)
                if first_level and first_level not in self.base_dest_paths:
                    unique_folders.add(first_level)
                    logger.debug(f"Added for cleanup (FULL_SOURCE): {first_level}")

        except Exception as e:
            logger.error(f"Error happened during collecting folder for cleanup: {str(e)}", exc_info=True)

        return unique_folders

    def _reconstruct_files_dict(self, snapshot: OperationSnapshot) -> Dict:
        """
        Reconstruct files dictionary for redo operation.

        Converts FileStateEntry keys to (file_name, file_size, file_hash) tuples
        to match the expected format for ProcessingThreadCopyMove.

        Args:
            snapshot: The operation snapshot

        Returns:
            Dict with tuple keys: {(file_name, file_size, file_hash): [source_paths]}
        """
        selected_files_dict = {}

        for (src_path, dest_path), metadata in snapshot.processed_files_metadata.items():
            # Reconstruct FileStateEntry from stored metadata (factual data)
            key_tuple = (
                metadata.get("file_name", ""),
                metadata.get("file_size", 0),
                metadata.get("file_hash", None)
            )

            if key_tuple not in selected_files_dict:
                selected_files_dict[key_tuple] = []

            if src_path not in selected_files_dict[key_tuple]:
                selected_files_dict[key_tuple].append(src_path)

        return selected_files_dict

if __name__ == "__main__":
    from processing_thread import ProcessingThreadCopyMoveResult
    from datetime import datetime

    processing_result = ProcessingThreadCopyMoveResult(
        operation_type=FileOperation.COPY,
        total_processed=10,
        total_skipped=0,
        renamed_files=[
            {'original': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C1\\1.txt',
             'renamed_to': '1_1.txt',
             'destination': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\1_1.txt',
             'size': 5},
            {'original': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\3.txt',
             'renamed_to': '3_1.txt',
             'destination': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\3_1.txt',
             'size': 30},
            {'original': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C1\\1.txt',
             'renamed_to': '1_1.txt',
             'destination': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\1_1.txt',
             'size': 5},
            {'original': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\3.txt',
             'renamed_to': '3_1.txt',
             'destination': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\3_1.txt',
             'size': 30}
        ],
        errors=[],
        total_size=134,
        duration=0.0,
        operation_start=datetime(2026, 8, 18, 16, 5, 9, 393608),
        source_paths=[
            'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\2.txt',
            'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\1.txt',
            'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\3.txt',
            'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C1\\1.txt',
            'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C1\\3.txt'
        ],
        destination_paths=[
            'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\3.txt',
            'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\3.txt',
            'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\3_1.txt',
            'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\2.txt',
            'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\3_1.txt',
            'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\1.txt',
            'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\1.txt',
            'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\1_1.txt',
            'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\1_1.txt',
            'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\2.txt'
        ],
        base_dest_paths=[
            'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1',
            'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2'],
        preserve_structure=PreserveStructureType.NONE,
        processed_files_metadata={
            ('D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\1.txt',
             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\1.txt'):
                {'file_name': '1.txt', 'file_size': 7, 'file_hash': '',
                 'source_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\1.txt',
                 'destination_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\1.txt',
                 'renamed': False},
            ('D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C1\\1.txt',
             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\1_1.txt'):
                {'file_name': '1.txt', 'file_size': 5, 'file_hash': '',
                 'source_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C1\\1.txt',
                 'destination_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\1_1.txt',
                 'renamed': True},
            ('D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\2.txt',
             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\2.txt'):
                {'file_name': '2.txt', 'file_size': 10, 'file_hash': '648d700404bcc01e835287f8eb2418cf7018e0326592451a7a2d3672def8393f',
                 'source_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\2.txt',
                 'destination_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\2.txt',
                 'renamed': False},
            ('D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\3.txt',
             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\3_1.txt'):
                {'file_name': '3.txt', 'file_size': 30, 'file_hash': '',
                 'source_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\3.txt',
                 'destination_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\3_1.txt',
                 'renamed': True},
            ('D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C1\\3.txt',
             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\3.txt'):
                {'file_name': '3.txt', 'file_size': 15, 'file_hash': '',
                 'source_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C1\\3.txt',
                 'destination_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\3.txt',
                 'renamed': False},
            ('D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\1.txt',
             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\1.txt'):
                {'file_name': '1.txt', 'file_size': 7, 'file_hash': '',
                 'source_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\1.txt',
                 'destination_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\1.txt',
                 'renamed': False},
            ('D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C1\\1.txt',
             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\1_1.txt'):
                {'file_name': '1.txt', 'file_size': 5, 'file_hash': '',
                 'source_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C1\\1.txt',
                 'destination_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\1_1.txt',
                 'renamed': True},
            ('D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\2.txt',
             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\2.txt'):
                {'file_name': '2.txt', 'file_size': 10, 'file_hash': '648d700404bcc01e835287f8eb2418cf7018e0326592451a7a2d3672def8393f',
                 'source_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\2.txt',
                 'destination_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\2.txt',
                 'renamed': False},
            ('D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C1\\3.txt',
             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\3.txt'):
                {'file_name': '3.txt', 'file_size': 15, 'file_hash': '',
                 'source_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C1\\3.txt',
                 'destination_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\3.txt',
                 'renamed': False},
            ('D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\3.txt',
             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\3_1.txt'):
                {'file_name': '3.txt', 'file_size': 30, 'file_hash': '',
                 'source_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\3.txt',
                 'destination_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\3_1.txt',
                 'renamed': True}
        }
    )

    # processing_result = ProcessingThreadCopyMoveResult(
    #         operation_type=FileOperation.MOVE,
    #         total_processed=10,
    #         total_skipped=0,
    #         renamed_files=[
    #             {'original': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C1\\1.txt',
    #              'renamed_to': '1_1.txt',
    #              'destination': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\1_1.txt',
    #              'size': 5},
    #             {'original': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C1\\3.txt',
    #              'renamed_to': '3_1.txt',
    #              'destination': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\3_1.txt',
    #              'size': 15},
    #             {'original': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\1.txt',
    #              'renamed_to': '1_1.txt',
    #              'destination': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\1_1.txt',
    #              'size': 7},
    #             {'original': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\3.txt',
    #              'renamed_to': '3_1.txt',
    #              'destination': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\3_1.txt',
    #              'size': 30}
    #         ],
    #         errors=[],
    #         total_size=134,
    #         duration=0.0,
    #         operation_start=datetime(2026, 8, 17, 22, 52, 8, 20180),
    #         source_paths=[
    #             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\2.txt',
    #             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\1.txt',
    #             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\3.txt',
    #             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C1\\3.txt',
    #             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C1\\1.txt'
    #         ],
    #         destination_paths=[
    #             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\3.txt',
    #             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\3.txt',
    #             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\3_1.txt',
    #             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\1.txt',
    #             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\1.txt',
    #             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\2.txt',
    #             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\1_1.txt',
    #             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\3_1.txt',
    #             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\1_1.txt',
    #             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\2.txt'
    #         ],
    #         base_dest_paths=[
    #             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2',
    #             'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1'
    #         ],
    #         preserve_structure=PreserveStructureType.NONE,
    #         processed_files_metadata={
    #             ('D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\1.txt',
    #              'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\1.txt'):
    #                 {'file_name': '1.txt', 'file_size': 7, 'file_hash': '',
    #                  'source_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\1.txt',
    #                  'destination_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\1.txt',
    #                  'renamed': False},
    #             ('D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C1\\1.txt',
    #              'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\1_1.txt'):
    #                 {'file_name': '1.txt', 'file_size': 5, 'file_hash': '',
    #                  'source_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C1\\1.txt',
    #                  'destination_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\1_1.txt',
    #                  'renamed': True},
    #             ('D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C1\\3.txt',
    #              'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\3_1.txt'):
    #                 {'file_name': '3.txt', 'file_size': 15, 'file_hash': '',
    #                  'source_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C1\\3.txt',
    #                  'destination_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\3_1.txt',
    #                  'renamed': True},
    #             ('D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\3.txt',
    #              'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\3.txt'):
    #                 {'file_name': '3.txt', 'file_size': 30, 'file_hash': '',
    #                  'source_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\3.txt',
    #                  'destination_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\3.txt',
    #                  'renamed': False},
    #             ('D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C1\\1.txt',
    #              'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\1.txt'):
    #                 {'file_name': '1.txt', 'file_size': 5, 'file_hash': '',
    #                  'source_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C1\\1.txt',
    #                  'destination_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\1.txt',
    #                  'renamed': False},
    #             ('D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\2.txt',
    #              'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\2.txt'):
    #                 {'file_name': '2.txt', 'file_size': 10, 'file_hash': '648d700404bcc01e835287f8eb2418cf7018e0326592451a7a2d3672def8393f',
    #                  'source_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\2.txt',
    #                  'destination_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest2\\2.txt',
    #                  'renamed': False},
    #             ('D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\1.txt',
    #              'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\1_1.txt'):
    #                 {'file_name': '1.txt', 'file_size': 7, 'file_hash': '',
    #                  'source_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\1.txt',
    #                  'destination_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\1_1.txt',
    #                  'renamed': True},
    #             ('D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C1\\3.txt',
    #              'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\3.txt'):
    #                 {'file_name': '3.txt', 'file_size': 15, 'file_hash': '',
    #                  'source_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C1\\3.txt',
    #                  'destination_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\3.txt',
    #                  'renamed': False},
    #             ('D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\3.txt',
    #              'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\3_1.txt'):
    #                 {'file_name': '3.txt', 'file_size': 30, 'file_hash': '',
    #                  'source_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\3.txt',
    #                  'destination_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\3_1.txt',
    #                  'renamed': True},
    #             ('D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\2.txt',
    #              'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\2.txt'):
    #                 {'file_name': '2.txt', 'file_size': 10, 'file_hash': '648d700404bcc01e835287f8eb2418cf7018e0326592451a7a2d3672def8393f',
    #                  'source_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Sources\\C2\\2.txt',
    #                  'destination_path': 'D:\\Serg\\Builder_book_progs_etc\\Programming\\Python\\Prj\\UniSync\\Dests\\Dest1\\2.txt',
    #                  'renamed': False}
    #         }
    # )

    manager = UndoRedoManager(
        source_dirs=processing_result.source_paths,
        dest_dirs=processing_result.destination_paths,
        base_dest_paths=processing_result.base_dest_paths,
        preserve_structure_enum=processing_result.preserve_structure,
        processing_mode=ProcessingMode.SEQUENTIAL
    )
    operation_id = manager.record_operation(processing_result=processing_result)
    if manager.can_undo():
        print(manager.get_undo_description())
        manager.undo()

    app = QCoreApplication(sys.argv)
    loop = QEventLoop()
    manager.operation_completed.connect(loop.quit)
    if manager.can_redo():
        print(manager.get_redo_description())
        manager.redo()
        loop.exec()
        print("Redo completed")
