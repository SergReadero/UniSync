# OperationHistory (Undo/Redo) Implementation

from dataclasses import  dataclass, field
from typing import Dict, List, Tuple, Any, Optional, Set
from copy import deepcopy
import logging
import time
import os
import shutil
from enums import FileOperation, PreserveStructureType
from custom_types.copy_move_types import FileMetadataKey, FileMetadataValue

logger = logging.getLogger(__name__)

@dataclass
class FileStateEntry:
    """Represents a unique file by name only."""
    file_name: str
    file_size: int
    file_hash: str = None

    def __hash__(self):
        return hash((self.file_name, self.file_size, self.file_hash))

    def __eq__(self, other):
        if not isinstance(other, FileStateEntry):
            return False
        return (
            (self.file_name == other.file_name) and
            (self.file_size == other.file_size) and
            (self.file_hash == other.file_hash)
        )

@dataclass
class OperationSnapshot:
    """Complete state snapshot before/after operation with factual metadata."""
    before_state: Dict[FileStateEntry, List[str]] # FileStateEntry => list of paths
    after_state: Dict[FileStateEntry, List[str]]
    file_operation: FileOperation
    operation_id: str
    renamed_files: List[Dict[str, Any]] # Track renames for restoration
    preserve_structure_type: PreserveStructureType  # Track which strategy was used
    # Actual metadata to avoid recalculation during redo
    processed_files_metadata: Dict[FileMetadataKey, FileMetadataValue] = field(default_factory=dict)

class OperationHistory:
    """
    Manages undo/redo operations with state snapshots.

    Converts ProcessingThreadCopyMoveResult into state dictionaries for
    tracking file operations and enabling undo/redo functionality.
    """

    def __init__(self):
        self._undo_stack: List[OperationSnapshot] = []
        self._redo_stack: List[OperationSnapshot] = []
        self._operation_counter = 0

    @property
    def undo_stack(self) -> List[OperationSnapshot]:
        """Get the undo stack."""
        return self._undo_stack

    @undo_stack.setter
    def undo_stack(self, value: List[OperationSnapshot]) -> None:
        """Set the undo stack."""
        self._undo_stack = value

    @property
    def redo_stack(self) -> List[OperationSnapshot]:
        """Get the redo stack."""
        return self._redo_stack

    @redo_stack.setter
    def redo_stack(self, value: List[OperationSnapshot]) -> None:
        """Set the redo stack."""
        self._redo_stack = value

    def _build_state_from_paths(
        self,
        file_paths: List[str]
    ) -> Dict[FileStateEntry, List[str]]:
        """Build state dictionary from file paths.

        Groups files by FileStateEntry (file_name, file_size, file_hash) with all paths.
        Only includes files that currently exist on disk.
        Fallback method - prefer metadata-based building for MOVE.

        Args:
            file_paths: List of file paths to include in state

        Returns:
            State dict: {FileStateEntry: [path1, path2, ...]}
        """
        state: Dict[FileStateEntry, List[str]] = {}

        for file_path in file_paths:
            if not os.path.exists(file_path):
                logger.warning(f"File not found during state build: {file_path}")
                continue

            try:
                file_name = os.path.basename(file_path)
                file_size = os.path.getsize(file_path)
                file_hash = None

                entry = FileStateEntry(file_name=file_name, file_size=file_size, file_hash=file_hash)
                if entry not in state:
                    state[entry] = []
                state[entry].append(file_path)

            except Exception as e:
                logger.error(f"Error building state for {file_path}: {str(e)}")

        return state

    def record_operation_from_result(
        self,
        processing_result  # ProcessingThreadCopyMoveResult
    ) -> str:
        """
        Record operation directly from ProcessingThreadCopyMoveResult.

        Captures actual metadata from processed_files_metadata to avoid
        recalculation during redo operations.

        For MOVE operations, builds states from metadata since source files no longer
        exist on disk after the move completes.

        Args:
            processing_result: ProcessingThreadCopyMoveResult with operation details

        Returns:
            Operation ID for reference
        """

        # For MOVE operations using metadata
        if processing_result.operation_type == FileOperation.MOVE:
            before_state = self._build_state_from_metadata(
                processing_result.processed_files_metadata,
                use_destination=False  # Source paths in before state
            )
            after_state = self._build_state_from_metadata(
                processing_result.processed_files_metadata,
                use_destination=True  # Destination paths in after state
            )
        else:
            # For COPY, files exist at sources and destinations
            # Build before state from source paths
            before_state = self._build_state_from_paths(processing_result.source_paths)

            # Build after state from destination paths
            # For files that were renamed, using the actual destination paths
            after_state = self._build_state_from_paths(processing_result.destination_paths)

        # Capture the processed metadata for redo
        processed_metadata = deepcopy(
            processing_result.processed_files_metadata
        )

        self._operation_counter += 1
        operation_id = f"op_{self._operation_counter}_{int(time.time() * 1000)}"

        snapshot = OperationSnapshot(
            before_state=deepcopy(before_state),
            after_state=deepcopy(after_state),
            file_operation=processing_result.operation_type,
            operation_id=operation_id,
            renamed_files=processing_result.renamed_files,
            preserve_structure_type=processing_result.preserve_structure,
            processed_files_metadata=processed_metadata
        )

        self._undo_stack.append(snapshot)
        self._redo_stack.clear()

        logger.info(
            f"Operation recorded from result: {processing_result.operation_type.value} "
            f"(ID: {operation_id}, before_state: {len(before_state)} entries, "
            f"after_state: {len(after_state)} entries)"
        )

        return operation_id

    def _build_state_from_metadata(
        self,
        processed_files_metadata: Dict,
        use_destination: bool = False
    ) -> Dict[FileStateEntry, List[str]]:
        """
        Build state dictionary from processed_files_metadata.

        Args:
            :param processed_files_metadata: Dict with (source_path, dest_path) keys
            :param use_destination: if True, use dest paths; if False, use source paths

        Returns:
             State dict: {FileStateEntry: [path1, path2, ...]}
        """
        state: Dict[FileStateEntry, List[str]] = {}

        for (source_path, dest_path), metadata in processed_files_metadata.items():
            try:
                # Use the path based on whether we want before or after state
                relevant_path = dest_path if use_destination else source_path

                # Extract file info from metadata
                file_name = metadata.get("file_name", os.path.basename(relevant_path))
                file_size = metadata.get("file_size", 0)
                file_hash = metadata.get("file_hash", None)

                entry = FileStateEntry(
                    file_name=file_name,
                    file_size=file_size,
                    file_hash=file_hash
                )

                if entry not in state:
                    state[entry] = []

                if relevant_path not in state[entry]:
                    state[entry].append(relevant_path)

            except Exception as e:
                logger.error(f"Error building state from metadata: {str(e)}")

        return state

    def record_operation(
        self,
        file_operation: FileOperation,
        before_state: Dict[FileStateEntry, List[str]],
        after_state: Dict[FileStateEntry, List[str]],
        renamed_files: List[Dict[str, Any]] = None,
        preserve_structure_type: PreserveStructureType = PreserveStructureType.NONE,
        processed_files_metadata: Dict[FileMetadataKey, FileMetadataValue] = None
    ) -> str:
        """
        Record a new operation with before/after state snapshots.

        Args:
            file_operation: Type of operation (COPY, MOVE)
            before_state: State before operation
            after_state: State after operation
            renamed_files: List of files that were renamed
            preserve_structure_type: Type of preserve structure used
            processed_files_metadata: Actual file metadata from execution

        Returns:
            Operation ID for reference
        """
        self._operation_counter += 1
        operation_id = f"op_{self._operation_counter}_{int(time.time() * 1000)}"

        snapshot = OperationSnapshot(
            before_state=deepcopy(before_state),
            after_state=deepcopy(after_state),
            file_operation=file_operation,
            operation_id=operation_id,
            renamed_files=renamed_files or [],
            preserve_structure_type=preserve_structure_type,
            processed_files_metadata=deepcopy(processed_files_metadata or {})
        )

        self._undo_stack.append(snapshot)
        self._redo_stack.clear()

        logger.info(
            f"Operation recorded: {file_operation.value.capitalize()} "
            f"(ID: {operation_id}, metadata entries: {len(snapshot.processed_files_metadata)})"
        )

        return operation_id

    def undo(self) -> Optional[OperationSnapshot]:
        """
        Undo the last operation.

        Returns
            The operation snapshot that was undone, or None if stack is empty
        """
        if not self._undo_stack:
            logger.warning("Nothing to undo")
            return None

        snapshot = self._undo_stack.pop()
        self._redo_stack.append(snapshot)

        logger.info(
            f"Undo performed: {snapshot.file_operation.value.capitalize()} "
            f"(ID: {snapshot.operation_id})"
        )

        return snapshot

    def redo(self) -> Optional[OperationSnapshot]:
        """
        Redo the last undone operation.

        Returns:
            The operation snapshot that was redone, or None if stack is empty
        """
        if not self._redo_stack:
            logger.warning("Nothing to redo")
            return None

        snapshot = self._redo_stack.pop()
        self._undo_stack.append(snapshot)

        logger.info(
            f"Redo performed: {snapshot.file_operation.value.capitalize()} "
            f"(ID: {snapshot.operation_id})"
        )

        return snapshot

    def can_undo(self) -> bool:
        """Check if there are operations to undo."""
        return len(self._undo_stack) > 0

    def can_redo(self) -> bool:
        """Check if there are operations to redo."""
        return len(self._redo_stack) > 0

    def get_undo_description(self) -> Optional[str]:
        """Get human-readable description of next undo operation."""
        if not self._undo_stack:
            return None
        snapshot = self._undo_stack[-1]
        return (
            f"Undo: {snapshot.file_operation.value.capitalize()} "
            f"({len(snapshot.before_state)} unique files)"
        )

    def get_redo_description(self) -> Optional[str]:
        """Get human-readable description of next redo operation."""
        if not self._redo_stack:
            return None
        snapshot = self._redo_stack[-1]
        return (
            f"Redo: {snapshot.file_operation.value.capitalize()} "
            f"({len(snapshot.after_state)} unique files)"
        )

    def clear(self) -> None:
        """Clear all undo and redo history."""
        self._undo_stack.clear()
        self._redo_stack.clear()
        logger.info("History cleared")
