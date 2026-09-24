"""Local-MCP write orchestration behind Package 4 owners."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_actor_registry import target_definition
from src.snotes.application.ai_audit_service import AiAuditService
from src.snotes.application.ai_receipt_inbox_service import AiReceiptInboxService
from src.snotes.application.ai_safe_read_service import (
    AI_VISIBILITY_FULL,
    AiSafeReadService,
    AiSafeReadSource,
    RedactionUnavailableError as SafeReadUnavailableError,
    normalize_ai_visibility,
)
from src.snotes.application.directory_service import DirectoryService
from src.snotes.application.note_service import NoteService
from src.snotes.application.note_canonical_candidate import (
    apply_exact_markdown_edits,
    build_canonical_edit_candidate,
    build_canonical_note_candidate,
    canonicalize_new_note,
)
from src.snotes.application.ports import TagRecord
from src.snotes.application.tag_service import TagService
from src.snotes.domain.models import DEFAULT_SUB_CATEGORY, Note, NoteLocation


@dataclass(frozen=True, slots=True)
class WritableNoteContext:
    note: Note
    tag_records: tuple[TagRecord, ...]


class LocalMcpWriteService:
    """Validate via Package 4, then delegate atomic changes to receipt owner."""

    def __init__(
        self,
        *,
        actor_target_id: str,
        notes: NoteService,
        directories: DirectoryService,
        tags: TagService,
        ai_metadata: AiAccessMetadataService,
        safe_reads: AiSafeReadService,
        audit: AiAuditService,
        receipt_inbox: AiReceiptInboxService,
        create_confirmation_required: bool = False,
    ) -> None:
        self.actor_target_id = actor_target_id
        self._notes = notes
        self._directories = directories
        self._tags = tags
        self._ai_metadata = ai_metadata
        self._safe_reads = safe_reads
        self._audit = audit
        self._receipt_inbox = receipt_inbox
        self._create_confirmation_required = create_confirmation_required

    def create_note(
        self,
        request_id: str,
        title: str,
        content: str,
        directory_id: int | None = None,
        main_category: str | None = None,
        sub_category: str | None = None,
        tags: list[str] | None = None,
        ai_visibility: object | None = None,
        ai_targets: list[str] | None = None,
    ) -> dict:
        duplicate = self._duplicate(request_id, "create")
        if duplicate is not None:
            return duplicate
        clean_title = title.strip() or "未命名 AI 笔记"
        clean_tags = self._normalize_tags(tags or [])
        if main_category is None and sub_category is not None:
            return self._terminal(
                "create",
                _result(
                    "rejected", request_id, None, "", "none", False, False,
                    "提供子分类时必须同时提供主分类。",
                ),
            )
        if main_category is not None and not str(main_category).strip():
            return self._terminal(
                "create",
                _result(
                    "rejected", request_id, None, "", "none", False, False,
                    "主分类不能为空。",
                ),
            )
        prepared_metadata = self._prepare_create_metadata(
            request_id, ai_visibility, ai_targets
        )
        if isinstance(prepared_metadata, dict):
            return self._terminal("create", prepared_metadata)
        visibility, targets = prepared_metadata
        selected = self._create_location(
            directory_id,
            main_category=main_category,
            sub_category=sub_category,
        )
        if selected is None:
            return self._terminal(
                "create",
                _result(
                    "rejected", request_id, None, "", "none", False, False,
                    "目标目录不可用；只能选择当前身份可见的已有目录。",
                ),
            )
        target_directory_id, location = selected
        if self._receipt_inbox.mcp_confirmation_required(
            "create",
            required_by_profile=self._create_confirmation_required,
        ):
            result = self._receipt_inbox.mcp_create_pending(
                actor_target_id=self.actor_target_id,
                actor_display=self._actor_display(),
                request_id=request_id,
                operation_type="create",
                title=clean_title,
                note_id=None,
                expected_updated_at=None,
                payload={
                    "title": clean_title,
                    "body": content,
                    "tags": clean_tags,
                    "main_category": location.main,
                    "sub_category": location.sub,
                },
                location=location,
                tags=clean_tags,
                body=content,
                visibility=visibility,
                target_ids=targets,
            )
        else:
            from src.snotes.ui.markdown_canonicalizer import CanonicalizationError

            try:
                canonical_content = canonicalize_new_note(content)
            except CanonicalizationError:
                return self._terminal(
                    "create",
                    _result(
                        "failed", request_id, None, clean_title, "none", False,
                        False, "无法生成稳定的 Markdown 正文。",
                    ),
                )
            result = self._receipt_inbox.mcp_create_direct(
                actor_target_id=self.actor_target_id,
                request_id=request_id,
                title=clean_title,
                content_markdown=canonical_content,
                directory_id=target_directory_id,
                location=location,
                tags=clean_tags,
                visibility=visibility,
                target_ids=targets,
                target_catalog=self._ai_metadata.target_catalog(),
            )
        self._audit_write("create", result)
        return result

    def prepend_note(self, **arguments) -> dict:
        return self._modify("prepend", **arguments)

    def append_note(self, **arguments) -> dict:
        return self._modify("append", **arguments)

    def edit_note(
        self,
        *,
        request_id: str,
        note_id: int,
        expected_version: str,
        edits: list[dict[str, str]],
    ) -> dict:
        operation_type = "edit"
        duplicate = self._duplicate(request_id, operation_type)
        if duplicate is not None:
            return duplicate
        note = self._notes.get_note(note_id)
        if note is None:
            return self._terminal(operation_type, _result(
                "not_found", request_id, note_id, "", "none", False, False,
                "未找到笔记。",
            ))
        metadata = self._ai_metadata.get_note_metadata(note.id)
        if normalize_ai_visibility(metadata.visibility) != AI_VISIBILITY_FULL:
            return self._terminal(operation_type, _result(
                "not_found", request_id, note_id, "", "none", False, False,
                "未找到笔记。",
            ))
        context = self._materialize(note)
        if context is None:
            return self._terminal(operation_type, _result(
                "not_found", request_id, note_id, "", "none", False, False,
                "未找到笔记。",
            ))
        if not str(expected_version or "").strip():
            return self._terminal(operation_type, _result(
                "rejected", request_id, note_id, note.title, "none", False,
                False, "缺少版本凭证；请先重新读取笔记。",
            ))
        if not _version_credentials_match(
            note.id, note.updated_at,
            expected_updated_at=None, expected_version=expected_version,
        ):
            return self._terminal(operation_type, _result(
                "conflict", request_id, note_id, note.title, "none", False,
                False, "笔记已被更新。",
            ))
        try:
            raw_candidate = apply_exact_markdown_edits(note.content_markdown, edits)
        except (TypeError, ValueError) as error:
            return self._terminal(operation_type, _result(
                "rejected", request_id, note_id, note.title, "none", False,
                False, str(error),
            ))
        if raw_candidate == note.content_markdown:
            return self._terminal(operation_type, _result(
                "no_change", request_id, note_id, note.title, "none", False,
                False, "请求没有产生实际变化。",
            ))
        from src.snotes.ui.markdown_canonicalizer import CanonicalizationError

        try:
            candidate = build_canonical_edit_candidate(note.content_markdown, edits)
        except (CanonicalizationError, ValueError, TypeError):
            return self._terminal(operation_type, _result(
                "failed", request_id, note_id, note.title, "none", False,
                False, "无法生成稳定的 Markdown 正文。",
            ))
        needs_confirmation = self._receipt_inbox.mcp_confirmation_required(operation_type)
        needs_confirmation |= candidate.legacy_canonicalization_changed
        if needs_confirmation:
            result = self._receipt_inbox.mcp_create_pending(
                actor_target_id=self.actor_target_id,
                actor_display=self._actor_display(),
                request_id=request_id,
                operation_type=operation_type,
                title=note.title,
                note_id=note_id,
                expected_updated_at=note.updated_at,
                payload={"edits": edits},
                location=note.location,
                tags=[name for _tag_id, name in context.tag_records],
                body="\n\n".join(
                    f"[{index}] {item['old_markdown']} → {item['new_markdown']}"
                    for index, item in enumerate(edits, 1)
                ),
                visibility=metadata.visibility,
                target_ids=metadata.target_ids,
            )
        else:
            result = self._receipt_inbox.mcp_modify_direct(
                actor_target_id=self.actor_target_id,
                request_id=request_id,
                note_id=note_id,
                operation_type=operation_type,
                expected_revision=note.revision,
                expected_updated_at=note.updated_at,
                expected_content_markdown=note.content_markdown,
                raw_candidate_markdown=raw_candidate,
                canonical_markdown=candidate.final_markdown,
                edits=edits,
            )
        self._audit_write(operation_type, result)
        return result

    def update_note_metadata(self, **arguments) -> dict:
        return self._modify("metadata", **arguments)

    def get_operation_status(self, request_id: str) -> dict:
        result = self._receipt_inbox.mcp_get_operation(
            request_id, self.actor_target_id
        )
        self._audit.record_event(
            self.actor_target_id,
            "operation_status",
            result_count=1 if result else 0,
            status="success",
        )
        return (
            result
            if result is not None
            else {"status": "not_found", "request_id": request_id}
        )

    def _modify(
        self,
        operation_type: str,
        *,
        request_id: str,
        note_id: int,
        expected_version: str | None = None,
        expected_updated_at: str | None = None,
        content: str = "",
        title: str | None = None,
        directory_id: int | None = None,
        add_tags: list[str] | None = None,
        remove_tags: list[str] | None = None,
    ) -> dict:
        duplicate = self._duplicate(request_id, operation_type)
        if duplicate is not None:
            return duplicate
        context = self._safe_context(note_id)
        if context is None:
            return self._terminal(
                operation_type,
                _result(
                    "not_found", request_id, note_id, "", "none", False,
                    False, "未找到笔记。",
                ),
            )
        note = context.note
        if not _credential_supplied(expected_updated_at, expected_version):
            return self._terminal(
                operation_type,
                _result(
                    "rejected", request_id, note_id, note.title, "none", False,
                    False, "缺少版本凭证；请先重新读取笔记。",
                ),
            )
        if not _version_credentials_match(
            note.id,
            note.updated_at,
            expected_updated_at=expected_updated_at,
            expected_version=expected_version,
        ):
            return self._terminal(
                operation_type,
                _result(
                    "conflict", request_id, note_id, note.title, "none", False,
                    False, "笔记已被更新。",
                ),
            )

        payload: dict = {}
        desired_title = None
        desired_directory_id = None
        desired_location = None
        desired_tags = None
        if operation_type in {"prepend", "append"}:
            if not content:
                return self._terminal(
                    operation_type,
                    _result(
                        "failed", request_id, note_id, note.title, "none", False,
                        False, "追加内容不能为空。",
                    ),
                )
            payload["content"] = content
        else:
            desired_title = (
                title.strip() or "未命名笔记" if title is not None else None
            )
            selected_directory = self._visible_location(directory_id)
            if directory_id is not None and selected_directory is None:
                return self._terminal(
                    operation_type,
                    _result(
                        "rejected", request_id, note_id, note.title, "none", False,
                        False, "目标目录不可用；只能选择当前身份可见的已有目录。",
                    ),
                )
            if selected_directory is not None:
                desired_directory_id, desired_location = selected_directory
            additions = set(self._normalize_tags(add_tags or []))
            removals = set(self._normalize_tags(remove_tags or []))
            if additions & removals:
                return self._terminal(
                    operation_type,
                    _result(
                        "rejected", request_id, note_id, note.title, "none", False,
                        False, "同一标签不能同时添加和移除。",
                    ),
                )
            current_tags = [name for _tag_id, name in context.tag_records]
            if additions or removals:
                desired_tags = [tag for tag in current_tags if tag not in removals]
                desired_tags.extend(
                    tag for tag in sorted(additions) if tag not in desired_tags
                )
            changed = any(
                (
                    desired_title is not None and desired_title != note.title,
                    desired_location is not None and desired_location != note.location,
                    desired_tags is not None and set(desired_tags) != set(current_tags),
                )
            )
            if not changed:
                return self._terminal(
                    operation_type,
                    _result(
                        "no_change", request_id, note_id, note.title, "none", False,
                        False, "请求没有产生实际变化。",
                    ),
                )
            if desired_title is not None:
                payload["title"] = desired_title
            if desired_location is not None:
                payload["directory"] = list(desired_location.values())
            if desired_tags is not None:
                payload["tags"] = desired_tags

        canonical_candidate = None
        canonicalization_failed = False
        if operation_type in {"prepend", "append"}:
            from src.snotes.ui.markdown_canonicalizer import CanonicalizationError

            try:
                canonical_candidate = build_canonical_note_candidate(
                    note.content_markdown, content, operation_type
                )
            except CanonicalizationError:
                # A body whose safety cannot be established may only be reviewed.
                canonicalization_failed = True

        requires_confirmation = self._receipt_inbox.mcp_confirmation_required(
            operation_type
        )
        if canonical_candidate is not None:
            requires_confirmation |= canonical_candidate.legacy_canonicalization_changed
        requires_confirmation |= canonicalization_failed

        if requires_confirmation:
            metadata = self._ai_metadata.get_note_metadata(note.id)
            result = self._receipt_inbox.mcp_create_pending(
                actor_target_id=self.actor_target_id,
                actor_display=self._actor_display(),
                request_id=request_id,
                operation_type=operation_type,
                title=desired_title or note.title,
                note_id=note_id,
                expected_updated_at=note.updated_at,
                payload=payload,
                location=desired_location or note.location,
                tags=desired_tags or [name for _tag_id, name in context.tag_records],
                body=content,
                visibility=metadata.visibility,
                target_ids=metadata.target_ids,
            )
        else:
            result = self._receipt_inbox.mcp_modify_direct(
                actor_target_id=self.actor_target_id,
                request_id=request_id,
                note_id=note_id,
                operation_type=operation_type,
                expected_revision=note.revision,
                expected_updated_at=note.updated_at,
                addition_markdown=(
                    content
                    if operation_type in {"prepend", "append"}
                    else ""
                ),
                canonical_markdown=(
                    canonical_candidate.final_markdown
                    if canonical_candidate is not None
                    else None
                ),
                expected_content_markdown=(
                    note.content_markdown
                    if operation_type in {"prepend", "append"}
                    else None
                ),
                title=desired_title,
                directory_id=desired_directory_id,
                location=desired_location,
                tags=desired_tags,
            )
        self._audit_write(operation_type, result)
        return result

    def _safe_context(self, note_id: int) -> WritableNoteContext | None:
        note = self._notes.get_note(note_id)
        return self._materialize(note) if note is not None else None

    def _materialize(self, note: Note) -> WritableNoteContext | None:
        metadata = self._ai_metadata.get_note_metadata(note.id)
        tag_records = tuple(self._tags.get_note_tag_records(note.id))
        source = AiSafeReadSource(
            note_id=note.id,
            title=note.title,
            content=note.content_markdown,
            visibility=metadata.visibility,
            targets=metadata.target_ids,
            directory=" / ".join(note.location.values()),
            tags=tuple(name for _tag_id, name in tag_records),
        )
        try:
            safe = self._safe_reads.read_note(self.actor_target_id, source)
        except SafeReadUnavailableError:
            return None
        return WritableNoteContext(note, tag_records) if safe is not None else None

    def _create_location(
        self,
        directory_id: int | None,
        *,
        main_category: str | None = None,
        sub_category: str | None = None,
    ) -> tuple[int, NoteLocation] | None:
        requested_location = self._requested_location(main_category, sub_category)
        if directory_id is not None:
            selected = self._visible_location(directory_id)
            if selected is None:
                return None
            if requested_location is not None and selected[1] != requested_location:
                return None
            return selected
        if requested_location is not None:
            directory_id = self._directories.find_active_location_id(
                requested_location
            )
            return (
                (directory_id, requested_location)
                if directory_id is not None
                else None
            )
        directory = next(
            (
                item
                for item in self._directories.list_directories()
                if item.parent_id is None and item.name == "临时"
            ),
            None,
        )
        return (directory.id, NoteLocation()) if directory is not None else None

    def _requested_location(
        self,
        main_category: str | None,
        sub_category: str | None,
    ) -> NoteLocation | None:
        if main_category is None and sub_category is None:
            return None
        main = str(main_category or "").strip()
        sub = str(sub_category or DEFAULT_SUB_CATEGORY).strip() or DEFAULT_SUB_CATEGORY
        if not main:
            return None
        return NoteLocation(main, sub)

    def _prepare_create_metadata(
        self,
        request_id: str,
        visibility: object | None,
        targets: list[str] | None,
    ) -> tuple[str, tuple[str, ...]] | dict:
        try:
            return self._ai_metadata.prepare_values(
                "full" if visibility is None else visibility,
                (self.actor_target_id,) if targets is None else targets,
            )
        except ValueError as error:
            return _result(
                "rejected", request_id, None, "", "none", False, False, str(error)
            )

    def _visible_location(
        self, directory_id: int | None
    ) -> tuple[int, NoteLocation] | None:
        if directory_id is None:
            return None
        for note in self._notes.list_active_notes():
            if note.directory_id == directory_id and self._materialize(note) is not None:
                return directory_id, note.location
        return None

    def _duplicate(self, request_id: str, operation_type: str) -> dict | None:
        existing = self._receipt_inbox.mcp_get_operation(request_id)
        if existing is None:
            return None
        result = {
            **existing,
            "status": "already_processed",
            "previous_status": existing.get("status"),
        }
        self._audit_write(operation_type, result, duplicate=True)
        return result

    def _terminal(self, operation_type: str, result: dict) -> dict:
        stored = self._receipt_inbox.mcp_store_terminal(
            self.actor_target_id, operation_type, result
        )
        self._audit_write(operation_type, stored)
        return stored

    def _audit_write(
        self, operation_type: str, result: dict, *, duplicate: bool = False
    ) -> None:
        self._audit.record_event(
            self.actor_target_id,
            operation_type,
            request_id=result["request_id"],
            note_id=result.get("note_id"),
            result_count=1 if result.get("modified_formal_note") else 0,
            handling_mode=result.get("handling_mode", "none"),
            status=result["status"],
            failure_reason=result.get("reason", ""),
            is_duplicate=duplicate or result["status"] == "already_processed",
            entered_confirmation=bool(result.get("entered_confirmation")),
        )

    def _actor_display(self) -> str:
        definition = target_definition(self.actor_target_id)
        return definition.display_name if definition is not None else self.actor_target_id

    @staticmethod
    def _normalize_tags(values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            clean = str(value).strip().lstrip("#").strip()
            key = clean.casefold()
            if clean and key not in seen:
                seen.add(key)
                normalized.append(clean)
        return normalized


def note_version_token(note_id: int, updated_at: str) -> str:
    material = f"{int(note_id)}\0{updated_at}".encode()
    return "snotes-v1-" + hashlib.sha256(material).hexdigest()




def _credential_supplied(
    expected_updated_at: str | None, expected_version: str | None
) -> bool:
    return bool(str(expected_updated_at or "").strip() or str(expected_version or "").strip())


def _version_credentials_match(
    note_id: int,
    current_updated_at: str,
    *,
    expected_updated_at: str | None,
    expected_version: str | None,
) -> bool:
    if expected_version is not None:
        supplied = str(expected_version).strip()
        if not supplied or not hmac.compare_digest(
            note_version_token(note_id, current_updated_at), supplied
        ):
            return False
    if expected_updated_at is not None:
        current = _parse_timestamp(current_updated_at)
        expected = _parse_timestamp(expected_updated_at)
        if current is None or expected is None or current != expected:
            return False
        if (current.tzinfo is None) != (expected.tzinfo is None):
            return False
    return _credential_supplied(expected_updated_at, expected_version)


def _parse_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _result(
    status: str,
    request_id: str,
    note_id: int | None,
    title: str,
    handling_mode: str,
    changed: bool,
    pending: bool,
    reason: str,
) -> dict:
    return {
        "status": status,
        "request_id": request_id,
        "note_id": note_id,
        "title": title,
        "handling_mode": handling_mode,
        "modified_formal_note": changed,
        "entered_confirmation": pending,
        "reason": reason,
    }
