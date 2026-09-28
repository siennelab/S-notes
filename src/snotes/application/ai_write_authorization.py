"""Single application owner for AI note-write and directory authorization."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.snotes.application.ai_safe_read_service import (
    AI_VISIBILITY_FULL,
    AI_VISIBILITY_HIDDEN,
    AiSafeReadService,
    AiSafeReadSource,
    RedactionUnavailableError,
    is_actor_authorized,
    normalize_ai_visibility,
)
from src.snotes.application.directory_service import DirectoryService
from src.snotes.domain.models import DEFAULT_SUB_CATEGORY, Directory, NoteLocation


class AiWriteExecutionContext(str, Enum):
    """Execution paths whose additional safety requirements intentionally differ."""

    HUMAN_ACCEPTANCE = "human_acceptance"
    MCP_DIRECT_WRITE = "mcp_direct_write"


@dataclass(frozen=True, slots=True)
class AuthorizedDirectory:
    id: int
    location: NoteLocation
    snapshot: tuple[Directory, ...]


class AiWriteAuthorization:
    """Answer every application-level AI write authorization question."""

    def __init__(
        self,
        directories: DirectoryService | None = None,
        safe_reads: AiSafeReadService | None = None,
    ) -> None:
        self._directories = directories
        self._safe_reads = safe_reads

    def can_write_note(
        self,
        actor_target_id: str,
        source: AiSafeReadSource,
        *,
        context: AiWriteExecutionContext,
        exact_edit: bool = False,
    ) -> bool:
        """Authorize a note and apply only the selected path's safety conditions."""
        if not self.can_access_note(
            actor_target_id, source, full_only=exact_edit
        ):
            return False
        if context == AiWriteExecutionContext.HUMAN_ACCEPTANCE:
            return True
        if context != AiWriteExecutionContext.MCP_DIRECT_WRITE:
            return False
        if self._safe_reads is None:
            return False
        try:
            return self._safe_reads.read_note(actor_target_id, source) is not None
        except RedactionUnavailableError:
            return False

    @staticmethod
    def can_access_note(
        actor_target_id: str,
        source: AiSafeReadSource,
        *,
        full_only: bool = False,
    ) -> bool:
        """Check note visibility and actor grants without materialization."""
        visibility = normalize_ai_visibility(source.visibility)
        return (
            visibility != AI_VISIBILITY_HIDDEN
            and (not full_only or visibility == AI_VISIBILITY_FULL)
            and is_actor_authorized(actor_target_id, source.targets)
        )

    def resolve_directory(
        self,
        *,
        directory_id: int | None = None,
        location: NoteLocation | None = None,
    ) -> AuthorizedDirectory | None:
        """Resolve either selector against the same active note-directory tree."""
        if directory_id is None and location is None:
            return None
        if self._directories is None:
            return None
        selected = (
            self._directory_by_id(directory_id)
            if directory_id is not None
            else self._directory_by_location(location)
        )
        if selected is None:
            return None
        if location is not None and selected.location != NoteLocation(*location.values()):
            return None
        return selected

    def _directory_by_location(
        self, location: NoteLocation | None
    ) -> AuthorizedDirectory | None:
        if location is None:
            return None
        normalized = NoteLocation(*location.values())
        assert self._directories is not None
        directory_id = self._directories.find_active_location_id(normalized)
        if directory_id is None:
            return None
        selected = self._directory_by_id(directory_id)
        return selected if selected is not None and selected.location == normalized else None

    def _directory_by_id(self, directory_id: int | None) -> AuthorizedDirectory | None:
        if directory_id is None:
            return None
        if self._directories is None:
            return None
        directories = {item.id: item for item in self._directories.list_directories()}
        directory = directories.get(directory_id)
        if directory is None:
            return None
        if directory.parent_id is None:
            location = NoteLocation(directory.name, DEFAULT_SUB_CATEGORY)
            snapshot = (directory,)
        else:
            parent = directories.get(directory.parent_id)
            if parent is None or parent.parent_id is not None:
                return None
            location = NoteLocation(parent.name, directory.name)
            snapshot = (parent, directory)
        return AuthorizedDirectory(directory.id, location, snapshot)
