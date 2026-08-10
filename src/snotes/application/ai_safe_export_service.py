"""Application workflow for frozen-R1-compatible AI-safe packages."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from src.snotes.application.ai_actor_registry import (
    AI_TARGET_COMMON_ID,
    AiTargetDefinition,
    selectable_target_definitions,
    target_definition,
)
from src.snotes.application.ai_audit_service import AiAuditService
from src.snotes.application.ai_safe_read_service import (
    AiSafeReadService,
    AiSafeReadSource,
    RedactionUnavailableError as SafeMaterializationUnavailableError,
    visibility_display,
)
from src.snotes.application.ports import AiSafePackageWriter, NotesRepository
from src.snotes.domain.ai_safe_export import (
    AiSafeExportOutcome,
    AiSafeExportPlan,
    AiSafeRenderedNote,
)
from src.snotes.domain.sensitive_scan import (
    merge_sensitive_findings,
    scan_sensitive_text,
)
from src.snotes.domain.tags import parse_tags


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
ConfirmExport = Callable[[AiSafeExportPlan], bool]


class AiSafeExportService:
    """Use the sole safe-read owner, then scan, write, and audit one attempt."""

    def __init__(
        self,
        notes: NotesRepository,
        safe_reads: AiSafeReadService,
        writer: AiSafePackageWriter,
        audit: AiAuditService,
    ) -> None:
        self._notes = notes
        self._safe_reads = safe_reads
        self._writer = writer
        self._audit = audit

    def available_targets(self) -> tuple[AiTargetDefinition, ...]:
        return selectable_target_definitions()

    def build_plan(self, actor: object) -> AiSafeExportPlan:
        definition = self._selectable_actor(actor)
        rendered: list[AiSafeRenderedNote] = []
        warnings: list[str] = []
        active_notes = sorted(
            self._notes.list_active(),
            key=lambda note: (note.updated_at, note.created_at, note.id),
            reverse=True,
        )
        for note in active_notes:
            _stored_note, target_ids = self._notes.get_ai_access_metadata(note.id)
            source = AiSafeReadSource(
                note_id=note.id,
                title=note.title,
                content=note.content_html,
                visibility=note.ai_visibility,
                targets=tuple(target_ids),
                directory=" / ".join(note.location.values()),
                tags=tuple(parse_tags(note.tags)),
            )
            try:
                safe = self._safe_reads.read_for_export_target(
                    definition.target_id,
                    source,
                )
            except SafeMaterializationUnavailableError as error:
                message = str(error)
                if message not in warnings:
                    warnings.append(message)
                continue
            if safe is None:
                continue
            target_names = tuple(
                (
                    item.display_name
                    if (item := target_definition(target_id)) is not None
                    else target_id
                )
                for target_id in target_ids
            )
            markdown = _note_markdown(
                safe.title,
                safe.content,
                safe.directory,
                safe.tags,
                safe.visibility,
                target_names,
                note.created_at,
                note.updated_at,
            )
            rendered.append(
                AiSafeRenderedNote(
                    note_id=note.id,
                    title=safe.title,
                    filename=(
                        f"{len(rendered) + 1:03d}_"
                        f"{_safe_filename(safe.title or '未命名笔记')}.md"
                    ),
                    markdown=markdown,
                    visibility=safe.visibility,
                    target_names=target_names,
                    was_transformed=safe.was_transformed,
                    transform_count=safe.transform_count,
                    scan_summary=scan_sensitive_text(markdown),
                    created_at=note.created_at,
                    updated_at=note.updated_at,
                )
            )
        return AiSafeExportPlan(
            actor_id=definition.target_id,
            actor_name=definition.display_name,
            actor_slug=definition.slug,
            exported_at=datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M"),
            rendered_notes=tuple(rendered),
            scan_summary=merge_sensitive_findings(
                [item.scan_summary for item in rendered]
            ),
            warnings=tuple(warnings),
        )

    def export(
        self,
        actor: object,
        *,
        confirm_sensitive: ConfirmExport | None = None,
    ) -> AiSafeExportOutcome:
        request_id = uuid.uuid4().hex
        try:
            plan = self.build_plan(actor)
        except ValueError:
            self._audit.record_event(
                actor,
                "export",
                request_id=request_id,
                status="failed",
                failure_reason="unknown_actor",
            )
            raise

        warning_reason = "redaction_unavailable" if plan.warnings else ""
        if not plan.rendered_notes:
            self._audit.record_event(
                plan.actor_id,
                "export",
                request_id=request_id,
                result_count=0,
                status="success",
                failure_reason=warning_reason,
            )
            return AiSafeExportOutcome("no_data", plan)

        entered_confirmation = plan.has_sensitive_findings
        if entered_confirmation and (
            confirm_sensitive is None or not confirm_sensitive(plan)
        ):
            self._audit.record_event(
                plan.actor_id,
                "export",
                request_id=request_id,
                result_count=plan.note_count,
                status="cancelled",
                failure_reason="sensitive_confirmation_cancelled",
                entered_confirmation=True,
            )
            return AiSafeExportOutcome("cancelled", plan)

        try:
            result = self._writer.write_plan(plan)
        except Exception:
            self._audit.record_event(
                plan.actor_id,
                "export",
                request_id=request_id,
                result_count=0,
                status="failed",
                failure_reason="package_write_failed",
                entered_confirmation=entered_confirmation,
            )
            raise
        self._audit.record_event(
            plan.actor_id,
            "export",
            request_id=request_id,
            result_count=result.note_count,
            status="success",
            failure_reason=warning_reason,
            entered_confirmation=entered_confirmation,
        )
        return AiSafeExportOutcome("exported", plan, result)

    @staticmethod
    def _selectable_actor(actor: object) -> AiTargetDefinition:
        text = str(actor or "").strip()
        definition = target_definition(text)
        if definition is None:
            definition = next(
                (
                    item
                    for item in selectable_target_definitions()
                    if item.display_name == text or text in item.aliases
                ),
                None,
            )
        if definition is None or not definition.selectable:
            raise ValueError(f"未知 AI 导出对象：{actor}")
        return definition


def _note_markdown(
    title: str,
    content: str,
    directory: str,
    tags: tuple[str, ...],
    visibility: str,
    target_names: tuple[str, ...],
    created_at: str,
    updated_at: str,
) -> str:
    tag_text = " ".join(f"#{tag}" for tag in tags) if tags else "无"
    target_text = "、".join(target_names) if target_names else "未设置"
    return "\n".join(
        [
            f"# {title or '未命名笔记'}",
            "",
            f"- 目录：{directory}",
            f"- 标签：{tag_text}",
            f"- AI 可见性：{visibility_display(visibility)}",
            f"- AI 适用对象：{target_text}",
            f"- 创建时间：{_display_datetime(created_at)}",
            f"- 更新时间：{_display_datetime(updated_at)}",
            "",
            "---",
            "",
            content.strip(),
        ]
    ).rstrip()


def _safe_filename(value: str, max_length: int = 80) -> str:
    cleaned = re.sub(r'[/:*?"<>|\\]+', "", value).strip()
    cleaned = re.sub(r"\s+", " ", cleaned) or "未命名笔记"
    return cleaned[:max_length].rstrip()


def _display_datetime(value: str) -> str:
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=SHANGHAI_TZ)
    return timestamp.astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M")
