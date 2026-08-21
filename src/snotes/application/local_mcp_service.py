"""Application read facade for the frozen-R1-compatible local MCP."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_actor_registry import AI_ACTOR_IDS
from src.snotes.application.ai_audit_service import AiAuditService
from src.snotes.application.ai_receipt_inbox_service import AiReceiptInboxService
from src.snotes.application.ai_safe_read_service import (
    AiSafeReadResult,
    AiSafeReadService,
    AiSafeReadSource,
    RedactionUnavailableError as SafeReadUnavailableError,
)
from src.snotes.application.directory_service import DirectoryService
from src.snotes.application.local_mcp_write_service import (
    LocalMcpWriteService,
    note_version_token,
)
from src.snotes.application.note_service import NoteService
from src.snotes.application.ports import TagRecord
from src.snotes.application.tag_service import TagService
from src.snotes.domain.models import Note
from src.snotes.domain.search_query import match_search_fields, parse_search_query


@dataclass(frozen=True, slots=True)
class SafeNoteContext:
    note: Note
    safe: AiSafeReadResult
    tag_records: tuple[TagRecord, ...]


class LocalMcpService:
    """Expose safe read/search and delegate write receipt orchestration."""

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
        if actor_target_id not in AI_ACTOR_IDS:
            raise ValueError("未知或不可用的 AI 身份。")
        self.actor_target_id = actor_target_id
        self._notes = notes
        self._tags = tags
        self._ai_metadata = ai_metadata
        self._safe_reads = safe_reads
        self._audit = audit
        self._writes = LocalMcpWriteService(
            actor_target_id=actor_target_id,
            notes=notes,
            directories=directories,
            tags=tags,
            ai_metadata=ai_metadata,
            safe_reads=safe_reads,
            audit=audit,
            receipt_inbox=receipt_inbox,
            create_confirmation_required=create_confirmation_required,
        )

    def search_notes(
        self,
        query: str,
        *,
        page: int = 1,
        page_size: int = 10,
        directory_id: int | None = None,
        tag_id: int | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        request_id = uuid.uuid4().hex
        contexts, warning = self._safe_contexts()
        parsed = parse_search_query(query)
        requested_tags = {
            str(value).strip().casefold()
            for value in (tags or [])
            if str(value).strip()
        }
        results: list[dict] = []
        for context in contexts:
            safe = context.safe
            tag_ids = {record[0] for record in context.tag_records}
            if directory_id is not None and context.note.directory_id != directory_id:
                continue
            if tag_id is not None and tag_id not in tag_ids:
                continue
            if requested_tags and not requested_tags.issubset(
                {tag.casefold() for tag in safe.tags}
            ):
                continue
            match = self._search_match(safe, parsed)
            if match is None:
                continue
            results.append(
                {
                    "note_id": context.note.id,
                    "title": safe.title,
                    "directory": safe.directory,
                    "tags": list(safe.tags),
                    "updated_at": context.note.updated_at,
                    **match,
                }
            )
        results.sort(
            key=lambda item: (item["updated_at"], item["note_id"]),
            reverse=True,
        )
        results.sort(
            key=lambda item: item["relevance"]["score"], reverse=True
        )
        offset = (page - 1) * page_size
        selected = results[offset : offset + page_size]
        self._audit.record_event(
            self.actor_target_id,
            "search",
            request_id=request_id,
            result_count=len(selected),
            status="success",
            failure_reason="redaction_unavailable" if warning else "",
        )
        return {
            "status": "ok",
            "query": query,
            "page": page,
            "page_size": page_size,
            "total_visible_matches": len(results),
            "has_more": offset + len(selected) < len(results),
            "results": selected,
            "reason": warning,
        }

    def get_note(
        self,
        note_id: int,
        *,
        offset: int = 0,
        max_chars: int = 4_000,
    ) -> dict:
        request_id = uuid.uuid4().hex
        note = self._notes.get_note(note_id)
        safe = None
        try:
            if note is not None:
                safe = self._materialize(note)[0]
        except SafeReadUnavailableError:
            self._audit.record_event(
                self.actor_target_id,
                "get_note",
                request_id=request_id,
                note_id=note_id,
                status="failed",
                failure_reason="redaction_unavailable",
            )
            return {
                "status": "failed",
                "note_id": note_id,
                "reason": "本地数据库暂时不可用；没有返回笔记数据。",
            }
        self._audit.record_event(
            self.actor_target_id,
            "get_note",
            request_id=request_id,
            note_id=note_id,
            result_count=1 if safe is not None else 0,
            status="success",
        )
        if note is None or safe is None:
            return {
                "status": "not_found",
                "note_id": note_id,
                "reason": "未找到可访问的笔记。",
            }
        metadata = self._ai_metadata.get_note_metadata(note.id)
        total = len(safe.content)
        start = min(offset, total)
        end = min(start + max_chars, total)
        directory_fields = _directory_fields(safe.directory)
        return {
            "status": "ok",
            "note_id": note.id,
            "title": safe.title,
            "directory": safe.directory,
            **directory_fields,
            "tags": list(safe.tags),
            "ai_visibility": metadata.visibility,
            "ai_targets": list(metadata.target_ids),
            "updated_at": note.updated_at,
            "version": note_version_token(note.id, note.updated_at),
            "content": safe.content[start:end],
            "returned_range": {"start": start, "end": end},
            "has_more": end < total,
            "next_offset": end if end < total else None,
            "total_safe_chars": total,
        }

    def list_directories(self) -> dict:
        request_id = uuid.uuid4().hex
        contexts, warning = self._safe_contexts()
        records = {
            (context.note.directory_id, context.safe.directory): {
                "directory_id": context.note.directory_id,
                "directory": context.safe.directory,
                **_directory_fields(context.safe.directory),
                "writable": True,
            }
            for context in contexts
            if context.note.directory_id is not None
        }
        result = sorted(
            records.values(),
            key=lambda item: (item["directory"], item["directory_id"]),
        )
        self._audit.record_event(
            self.actor_target_id,
            "list_directories",
            request_id=request_id,
            result_count=len(result),
            status="success",
            failure_reason="redaction_unavailable" if warning else "",
        )
        return {"status": "ok", "directories": result, "reason": warning}

    def list_tags(self) -> dict:
        request_id = uuid.uuid4().hex
        contexts, warning = self._safe_contexts()
        records: dict[tuple[int, str], dict] = {}
        for context in contexts:
            for (tag_id, _raw_name), safe_name in zip(
                context.tag_records, context.safe.tags, strict=True
            ):
                records[(tag_id, safe_name)] = {
                    "tag_id": tag_id,
                    "name": safe_name,
                }
        result = sorted(
            records.values(), key=lambda item: (item["name"], item["tag_id"])
        )
        self._audit.record_event(
            self.actor_target_id,
            "list_tags",
            request_id=request_id,
            result_count=len(result),
            status="success",
            failure_reason="redaction_unavailable" if warning else "",
        )
        return {"status": "ok", "tags": result, "reason": warning}

    def list_ai_metadata_options(self) -> dict:
        request_id = uuid.uuid4().hex
        result = {
            "status": "ok",
            "ai_visibility_options": [
                {"value": value, "label": label}
                for value, label in self._ai_metadata.available_visibility_options()
            ],
            "ai_targets": [
                {
                    "target_id": target.target_id,
                    "display_name": target.display_name,
                }
                for target in self._ai_metadata.available_targets()
            ],
            "reason": "",
        }
        self._audit.record_event(
            self.actor_target_id,
            "list_ai_metadata_options",
            request_id=request_id,
            result_count=len(result["ai_targets"]),
            status="success",
        )
        return result

    def create_note(self, *arguments, **keywords) -> dict:
        return self._writes.create_note(*arguments, **keywords)

    def prepend_note(self, **arguments) -> dict:
        return self._writes.prepend_note(**arguments)

    def append_note(self, **arguments) -> dict:
        return self._writes.append_note(**arguments)

    def update_note_metadata(self, **arguments) -> dict:
        return self._writes.update_note_metadata(**arguments)

    def get_operation_status(self, request_id: str) -> dict:
        return self._writes.get_operation_status(request_id)

    def _safe_contexts(self) -> tuple[list[SafeNoteContext], str]:
        contexts: list[SafeNoteContext] = []
        warning = ""
        for note in self._notes.list_active_notes():
            try:
                safe, tag_records = self._materialize(note)
            except SafeReadUnavailableError as error:
                warning = str(error)
                continue
            if safe is not None:
                contexts.append(SafeNoteContext(note, safe, tag_records))
        return contexts, warning

    def _materialize(
        self, note: Note
    ) -> tuple[AiSafeReadResult | None, tuple[TagRecord, ...]]:
        metadata = self._ai_metadata.get_note_metadata(note.id)
        tag_records = tuple(self._tags.get_note_tag_records(note.id))
        source = AiSafeReadSource(
            note_id=note.id,
            title=note.title,
            content=note.content_html,
            visibility=metadata.visibility,
            targets=metadata.target_ids,
            directory=" / ".join(note.location.values()),
            tags=tuple(name for _tag_id, name in tag_records),
        )
        return self._safe_reads.read_note(self.actor_target_id, source), tag_records

    @staticmethod
    def _search_match(safe: AiSafeReadResult, query) -> dict | None:
        if query.is_blank:
            return {
                "snippet": _snippet(safe.content, ""),
                "matched_field": "all",
                "relevance": {"score": 1, "explanation": "recent_safe_note"},
            }
        match = match_search_fields(
            query,
            title=safe.title,
            tags=safe.tags,
            directory=safe.directory,
            body=safe.content,
        )
        if match is None:
            return None
        first = query.tokens[0]
        if match.matched_in == "title":
            snippet = _snippet(safe.title, first)
        elif match.matched_in in {"tags", "title_tags"}:
            snippet = " | ".join(
                part for part in (safe.title, ", ".join(safe.tags)) if part
            )
        elif match.matched_in == "directory":
            snippet = safe.directory
        else:
            snippet = _snippet(safe.content, first)
        return {
            "snippet": snippet,
            "matched_field": match.matched_in,
            "relevance": {"score": match.score, "explanation": match.reason},
        }


def _snippet(text: str, needle: str, radius: int = 80) -> str:
    if not text:
        return ""
    index = text.casefold().find(needle) if needle else 0
    index = max(index, 0)
    start = max(0, index - radius)
    end = min(len(text), index + max(len(needle), 1) + radius)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


def _directory_fields(directory: str) -> dict:
    parts = [part.strip() for part in str(directory or "").split("/") if part.strip()]
    return {
        "main_category": parts[0] if parts else "",
        "sub_category": parts[1] if len(parts) > 1 else "",
    }
