"""Single application owner for the complete AI receipt inbox lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_actor_registry import (
    AI_ACTOR_IDS,
    resolve_target_id,
    target_definition,
)
from src.snotes.application.note_service import NoteService
from src.snotes.application.note_canonical_candidate import (
    build_canonical_note_candidate,
    canonicalize_new_note,
)
from src.snotes.application.ports import (
    AiMcpOperationRepository,
    AiReceiptInboxRepository,
)
from src.snotes.application.tag_service import TagService
from src.snotes.domain.ai_receipts import (
    RECEIPT_STATUS_ACCEPTED,
    RECEIPT_STATUS_DISCARDED,
    AiReceiptAcceptCommand,
    AiReceiptInboxItem,
    GaleReceiptImport,
)
from src.snotes.domain.models import Note, NoteLocation


class ReceiptDeleteReason(str, Enum):
    DELETED = "deleted"
    NOT_FOUND = "not_found"
    NOT_PROCESSED = "not_processed"
    ACCEPTED_LINKED_NOTE = "accepted_linked_note"
    CONCURRENT_CHANGE = "concurrent_change"


@dataclass(frozen=True, slots=True)
class DeleteProcessedReceiptResult:
    receipt_id: int
    deleted: bool
    reason: ReceiptDeleteReason
    message: str


@dataclass(frozen=True, slots=True)
class AiReceiptAcceptFields:
    title: str
    body: str
    main_category: str
    sub_category: str
    tags: str
    visibility: object
    target_ids: tuple[str, ...]
    allow_failed_parse: bool = False


@dataclass(frozen=True, slots=True)
class AcceptReceiptResult:
    receipt: AiReceiptInboxItem
    note: Note
    created: bool
    already_processed: bool


@dataclass(frozen=True, slots=True)
class DiscardReceiptResult:
    receipt: AiReceiptInboxItem
    changed: bool
    already_processed: bool


@dataclass(frozen=True, slots=True)
class ReceiptTargetState:
    target_ids: tuple[str, ...]
    active_target_ids: tuple[str, ...]
    legacy_target_ids: tuple[str, ...]
    legacy_target_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PendingNotePreview:
    receipt_id: int
    note_id: int
    note_revision: int
    expected_updated_at: str
    operation_type: str
    before_markdown: str
    final_markdown: str
    legacy_canonicalization_changed: bool


class UnsupportedReceiptOperationError(ValueError):
    pass


class AiReceiptInboxService:
    """Own frozen-R1 reads, create acceptance, discard, and deletion policy."""

    def __init__(
        self,
        receipts: AiReceiptInboxRepository,
        *,
        notes: NoteService | None = None,
        tags: TagService | None = None,
        ai_metadata: AiAccessMetadataService | None = None,
        mcp_operations: AiMcpOperationRepository | None = None,
    ) -> None:
        self._receipts = receipts
        self._notes = notes
        self._tags = tags
        self._ai_metadata = ai_metadata
        self._mcp_operations = mcp_operations

    def list_pending_receipts(self) -> list[AiReceiptInboxItem]:
        return self._receipts.list_pending()

    def list_processed_receipts(self) -> list[AiReceiptInboxItem]:
        return self._receipts.list_processed()

    def get_receipt(self, receipt_id: int) -> AiReceiptInboxItem | None:
        return self._receipts.get(receipt_id)

    def build_pending_note_preview(
        self,
        receipt_id: int,
        *,
        addition_markdown: str | None = None,
    ) -> PendingNotePreview:
        """Preview a pending body operation against the current formal note.

        The edited fragment, when supplied, is exactly the one the existing
        acceptance UI would submit. Both reads and canonicalization are
        side-effect free. A second read catches changes during the Qt round trip.
        """
        notes = self._notes
        if notes is None:
            raise RuntimeError("笔记服务不可用，无法预览最终正文。")
        receipt = self._receipts.get(receipt_id)
        if receipt is None:
            raise ValueError(f"AI 回执不存在: {receipt_id}")
        if not receipt.pending:
            raise ValueError("该回执已处理，不能预览最终正文。")
        if receipt.parse_status != "parsed":
            raise ValueError("该回执解析失败，需要人工确认后才能入库。")
        if receipt.operation_type not in {"append", "prepend"}:
            raise UnsupportedReceiptOperationError("该回执不是正文追加提案。")
        if not receipt.request_id or not receipt.expected_updated_at:
            raise ValueError("追加提案缺少必要的身份或版本信息。")
        if resolve_target_id(receipt.actor_target_id) not in AI_ACTOR_IDS:
            raise ValueError("回执中的 AI 身份未知或不可用。")
        if receipt.target_note_id is None:
            raise ValueError("追加提案缺少目标笔记。")
        note = notes.get_note(receipt.target_note_id)
        if note is None:
            raise ValueError("目标笔记不存在。")
        if note.updated_at != receipt.expected_updated_at:
            raise ValueError("笔记已被更新。")

        addition = receipt.body if addition_markdown is None else addition_markdown
        candidate = build_canonical_note_candidate(
            note.content_markdown,
            addition,
            receipt.operation_type,
        )

        # Milkdown runs a nested Qt event loop. During that time the receipt or
        # note can change; never show a result for a stale snapshot.
        current_receipt = self._receipts.get(receipt_id)
        current_note = notes.get_note(receipt.target_note_id)
        if current_receipt != receipt or current_note != note:
            raise ValueError("提案或笔记已被更新，请刷新后重新预览。")
        return PendingNotePreview(
            receipt_id=receipt_id,
            note_id=note.id,
            note_revision=note.revision,
            expected_updated_at=receipt.expected_updated_at,
            operation_type=receipt.operation_type,
            before_markdown=candidate.before_markdown,
            final_markdown=candidate.final_markdown,
            legacy_canonicalization_changed=(
                candidate.legacy_canonicalization_changed
            ),
        )

    def import_gale_submission(
        self, submission: GaleReceiptImport
    ) -> tuple[AiReceiptInboxItem, bool]:
        """Idempotently create a pending receipt; never a formal note."""
        return self._receipts.import_gale_pending(submission)

    def mcp_confirmation_required(
        self,
        operation_type: str,
        *,
        required_by_profile: bool = False,
    ) -> bool:
        """Keep local-MCP confirmation policy behind the receipt owner."""
        if required_by_profile:
            return True
        return self._require_mcp_operations().confirmation_required(operation_type)

    def mcp_get_operation(
        self,
        request_id: str,
        actor_target_id: str | None = None,
    ) -> dict | None:
        return self._require_mcp_operations().get_operation(
            request_id, actor_target_id
        )

    def mcp_store_terminal(
        self,
        actor_target_id: str,
        operation_type: str,
        result: dict,
    ) -> dict:
        return self._require_mcp_operations().store_terminal(
            actor_target_id, operation_type, result
        )

    def mcp_create_direct(self, **values) -> dict:
        return self._require_mcp_operations().create_direct(**values)

    def mcp_modify_direct(self, **values) -> dict:
        return self._require_mcp_operations().modify_direct(**values)

    def mcp_accept_pending_append(self, **values) -> dict:
        return self._require_mcp_operations().accept_pending_append(**values)

    def mcp_create_pending(self, **values) -> dict:
        return self._require_mcp_operations().create_pending(**values)

    def receipt_target_state(
        self,
        receipt: AiReceiptInboxItem,
    ) -> ReceiptTargetState:
        target_ids = tuple(self._parse_target_ids(receipt.ai_targets))
        active_ids = {
            item.target_id
            for item in (
                self._ai_metadata.available_targets()
                if self._ai_metadata is not None
                else ()
            )
        }
        legacy_ids = tuple(
            target_id for target_id in target_ids if target_id not in active_ids
        )
        return ReceiptTargetState(
            target_ids=target_ids,
            active_target_ids=tuple(
                target_id for target_id in target_ids if target_id in active_ids
            ),
            legacy_target_ids=legacy_ids,
            legacy_target_names=tuple(
                (
                    definition.display_name
                    if (definition := target_definition(target_id)) is not None
                    else target_id
                )
                for target_id in legacy_ids
            ),
        )

    def accept_receipt(
        self,
        receipt_id: int,
        fields: AiReceiptAcceptFields,
    ) -> AcceptReceiptResult:
        notes, tags, ai_metadata = self._require_lifecycle_dependencies()
        receipt = self._receipts.get(receipt_id)
        if receipt is None:
            raise ValueError(f"AI 回执不存在: {receipt_id}")
        if receipt.status == RECEIPT_STATUS_ACCEPTED and receipt.note_id is not None:
            note = notes.get_note(receipt.note_id)
            if note is None:
                raise ValueError("回执关联的正式笔记不存在。")
            return AcceptReceiptResult(receipt, note, False, True)
        if not receipt.pending:
            raise ValueError("该回执已处理，不能重复接受。")
        if receipt.parse_status != "parsed" and not fields.allow_failed_parse:
            raise ValueError("该回执解析失败，需要人工确认后才能入库。")
        if receipt.parse_status != "parsed" and fields.allow_failed_parse:
            if not fields.title.strip():
                raise ValueError("人工确认入库前，标题不能为空。")
            if not fields.main_category.strip():
                raise ValueError("人工确认入库前，主分类不能为空。")

        if receipt.request_id and receipt.operation_type == "append":
            if not fields.body:
                raise ValueError("追加内容不能为空。")
            actor_id = resolve_target_id(receipt.actor_target_id)
            if actor_id not in AI_ACTOR_IDS:
                raise ValueError("回执中的 AI 身份未知或不可用。")
            if receipt.target_note_id is None or not receipt.expected_updated_at:
                raise ValueError("追加提案缺少目标笔记或版本信息。")
            current_note = notes.get_note(receipt.target_note_id)
            if current_note is None:
                raise ValueError("目标笔记不存在。")
            if current_note.updated_at != receipt.expected_updated_at:
                raise ValueError("笔记已被更新。")
            candidate = build_canonical_note_candidate(
                current_note.content_markdown, fields.body, "append"
            )
            result = self.mcp_accept_pending_append(
                receipt_id=receipt.id,
                addition_markdown=fields.body,
                accepted_body=fields.body,
                canonical_markdown=candidate.final_markdown,
                expected_revision=current_note.revision,
                expected_content_markdown=current_note.content_markdown,
            )
            if result["status"] == "conflict":
                raise ValueError(result["reason"])
            if result["status"] != "updated":
                raise RuntimeError(result.get("reason") or "接受追加提案失败。")
            persisted_receipt = self._receipts.get(receipt.id)
            note = notes.get_note(result["note_id"])
            if persisted_receipt is None or note is None:
                raise RuntimeError("接受追加提案后读取正式笔记失败。")
            return AcceptReceiptResult(
                persisted_receipt, note, False, False
            )

        tags_value = tags.normalize_tags_text(fields.tags)
        location = NoteLocation(
            fields.main_category.strip() or "临时",
            fields.sub_category.strip() or "无",
        )
        allow_directory_creation = True
        if receipt.request_id:
            if receipt.operation_type != "create":
                raise UnsupportedReceiptOperationError(
                    "本功能块不处理修改已有笔记的 AI proposal。"
                )
            actor_id = resolve_target_id(receipt.actor_target_id)
            if actor_id not in AI_ACTOR_IDS:
                raise ValueError("回执中的 AI 身份未知或不可用。")
            allow_directory_creation = False
        visibility, target_ids = ai_metadata.prepare_values(
            fields.visibility,
            fields.target_ids,
        )

        command = AiReceiptAcceptCommand(
            title=fields.title.strip() or "未命名 AI 回执",
            content_markdown=canonicalize_new_note(fields.body),
            location=location,
            tags=tags_value,
            visibility=visibility,
            target_ids=target_ids,
            allow_directory_creation=allow_directory_creation,
        )
        persisted = self._receipts.accept_pending(
            receipt_id,
            command,
            ai_metadata.target_catalog(),
        )
        note = notes.get_note(persisted.note_id)
        if note is None:
            raise RuntimeError("接受回执后读取正式笔记失败。")
        return AcceptReceiptResult(
            persisted.receipt,
            note,
            persisted.created,
            not persisted.created,
        )

    def discard_receipt(self, receipt_id: int) -> DiscardReceiptResult:
        receipt = self._receipts.get(receipt_id)
        if receipt is None:
            raise ValueError(f"AI 回执不存在: {receipt_id}")
        if receipt.status == RECEIPT_STATUS_DISCARDED:
            return DiscardReceiptResult(receipt, False, True)
        if not receipt.pending:
            raise ValueError("该回执已处理，不能重复丢弃。")
        persisted = self._receipts.discard_pending(receipt_id)
        return DiscardReceiptResult(
            persisted.receipt,
            persisted.changed,
            not persisted.changed,
        )

    def delete_processed_receipt(
        self,
        receipt_id: int,
    ) -> DeleteProcessedReceiptResult:
        receipt = self._receipts.get(receipt_id)
        if receipt is None:
            return DeleteProcessedReceiptResult(
                receipt_id,
                False,
                ReceiptDeleteReason.NOT_FOUND,
                "AI 回执不存在。",
            )
        if receipt.pending:
            return DeleteProcessedReceiptResult(
                receipt_id,
                False,
                ReceiptDeleteReason.NOT_PROCESSED,
                "待确认回执不能通过已处理删除入口删除。",
            )
        if receipt.status == RECEIPT_STATUS_ACCEPTED and receipt.note_id is not None:
            return DeleteProcessedReceiptResult(
                receipt_id,
                False,
                ReceiptDeleteReason.ACCEPTED_LINKED_NOTE,
                "已确认入库的回执保留用于查账，不能在这里删除。",
            )

        deleted = self._receipts.delete_if_unchanged(
            receipt.id,
            expected_status=receipt.status,
            expected_note_id=receipt.note_id,
        )
        if not deleted:
            return DeleteProcessedReceiptResult(
                receipt_id,
                False,
                ReceiptDeleteReason.CONCURRENT_CHANGE,
                "AI 回执状态已改变，请刷新后重试。",
            )
        return DeleteProcessedReceiptResult(
            receipt_id,
            True,
            ReceiptDeleteReason.DELETED,
            "AI 回执已删除。",
        )

    def _require_lifecycle_dependencies(
        self,
    ) -> tuple[NoteService, TagService, AiAccessMetadataService]:
        if self._notes is None or self._tags is None or self._ai_metadata is None:
            raise RuntimeError("Receipt lifecycle application dependencies are unavailable")
        return self._notes, self._tags, self._ai_metadata

    def _require_mcp_operations(self) -> AiMcpOperationRepository:
        if self._mcp_operations is None:
            raise RuntimeError("MCP operation persistence is unavailable")
        return self._mcp_operations

    @staticmethod
    def _parse_target_ids(value: str) -> list[str]:
        text = str(value or "").strip()
        if not text or text.casefold() in {"无", "none", "null"}:
            return []
        target_ids: list[str] = []
        seen: set[str] = set()
        for chunk in text.replace("，", ",").replace("、", ",").split(","):
            clean = chunk.strip()
            if not clean:
                continue
            resolved = resolve_target_id(clean)
            values = (resolved,) if resolved is not None else tuple(
                resolve_target_id(part) for part in clean.split()
            )
            for target_id in values:
                if target_id is None:
                    raise ValueError(f"未知 AI 适用对象：{clean}")
                if target_id not in seen:
                    seen.add(target_id)
                    target_ids.append(target_id)
        return target_ids
