"""Single application owner for the complete AI receipt inbox lifecycle."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

from src.snotes.application.ai_acceptance_outcome import (
    AcceptanceKind, AcceptanceOutcome, operation_outcome, normalize_operation_outcome,
)
from src.snotes.domain.notes_operation_result import OperationStatus, HandlingMode, notes_operation_result
from src.snotes.application.ai_write_authorization import AiWriteAuthorization, AiWriteExecutionContext
from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_actor_registry import (
    AI_ACTOR_IDS,
    resolve_target_id,
    target_definition,
)
from src.snotes.application.note_service import NoteService
from src.snotes.application.note_canonical_candidate import (
    build_canonical_edit_candidate,
    build_canonical_note_candidate,
    canonicalize_new_note,
)
from src.snotes.application.ai_pending_body import parse_pending_body_payload
from src.snotes.application.ai_pending_metadata import parse_pending_metadata_payload
from src.snotes.application.ai_safe_read_service import (
    AiSafeReadSource,
)
from src.snotes.application.ports import (
    AiMcpOperationRepository,
    AcceptanceCommitFact,
    AiNoteFingerprint,
    AiReceiptInboxRepository,
    CanonicalizationError,
    MarkdownCanonicalizer,
)
from src.snotes.application.tag_service import TagService
from src.snotes.domain.ai_receipts import (
    RECEIPT_STATUS_ACCEPTED,
    RECEIPT_STATUS_DISCARDED,
    AiReceiptAcceptCommand,
    AiReceiptInboxItem,
    GaleReceiptImport,
)
from src.snotes.domain.models import Directory, Note, NoteLocation


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
    receipt: AiReceiptInboxItem | None
    note: Note | None
    created: bool
    already_processed: bool
    outcome: AcceptanceOutcome = AcceptanceOutcome(AcceptanceKind.SUCCESS)


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


class StaleNoteProposalError(ValueError):
    """Preview is unavailable, but Accept must still record the version conflict."""


CREATE_CONFIRMATION_KEY = "ai_create_requires_confirmation"
MODIFY_CONFIRMATION_KEY = "ai_modify_requires_confirmation"
DEFAULT_CONFIRMATION = {
    CREATE_CONFIRMATION_KEY: False,
    MODIFY_CONFIRMATION_KEY: True,
}


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
        canonicalizer: MarkdownCanonicalizer | None = None,
        authorization: AiWriteAuthorization | None = None,
    ) -> None:
        self._receipts = receipts
        self._notes = notes
        self._tags = tags
        self._ai_metadata = ai_metadata
        self._mcp_operations = mcp_operations
        self._canonicalizer = canonicalizer
        self._authorization = authorization or AiWriteAuthorization()

    def canonicalize_markdown(self, markdown: str) -> str:
        canonicalizer = self._canonicalizer
        if canonicalizer is None:
            raise RuntimeError("Markdown 规范化服务不可用。")
        return canonicalizer.canonicalize_markdown(markdown)

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
        if receipt.operation_type not in {"append", "prepend", "edit"}:
            raise UnsupportedReceiptOperationError("该回执不是正文修改提案。")
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
            raise StaleNoteProposalError("笔记已被更新，提案版本冲突。")

        if receipt.operation_type == "edit":
            metadata = self._ai_metadata
            if metadata is None:
                raise RuntimeError("笔记权限服务不可用。")
            if not self._fingerprint_can_accept(self._note_fingerprint(note), receipt.actor_target_id, edit=True):
                raise ValueError("当前 AI 身份已无权修改此笔记。")
            edits = self.pending_note_edits(receipt_id)
            candidate = build_canonical_edit_candidate(
                note.content_markdown,
                edits,
                canonicalize=self.canonicalize_markdown,
            )
        else:
            addition = receipt.body if addition_markdown is None else addition_markdown
            candidate = build_canonical_note_candidate(
                note.content_markdown,
                addition,
                receipt.operation_type,
                canonicalize=self.canonicalize_markdown,
            )

        # Milkdown runs a nested Qt event loop. During that time the receipt or
        # note can change; never show a result for a stale snapshot.
        current_receipt = self._receipts.get(receipt_id)
        current_note = notes.get_note(receipt.target_note_id)
        if current_note is not None and current_note.updated_at != receipt.expected_updated_at:
            raise StaleNoteProposalError("提案或笔记已被更新，请刷新后重新预览。")
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

    def pending_note_edits(self, receipt_id: int) -> list[dict[str, str]]:
        """Read stored proposal evidence for the local human UI, even after rejection.

        This is not an AI note read and does not grant write permission. Current
        note/version/grants are checked separately by preview and acceptance.
        """
        receipt = self._receipts.get(receipt_id)
        if receipt is None or receipt.operation_type != "edit":
            raise ValueError("该回执不是正文修改提案。")
        raw = self._require_mcp_operations().pending_operation_payload(receipt_id)
        if raw is None:
            raise ValueError("正文修改提案内容无效。")
        edits = parse_pending_body_payload(raw, "edit")
        assert isinstance(edits, list)
        return edits

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
        key = (
            CREATE_CONFIRMATION_KEY
            if operation_type == "create"
            else MODIFY_CONFIRMATION_KEY
        )
        stored = self._require_mcp_operations().confirmation_setting(key)
        return DEFAULT_CONFIRMATION[key] if stored is None else stored

    def mcp_get_operation(
        self,
        request_id: str,
        actor_target_id: str | None = None,
    ) -> dict | None:
        result = self._require_mcp_operations().get_operation(request_id, actor_target_id)
        return normalize_operation_outcome(result) if result is not None else None

    def mcp_store_terminal(
        self,
        actor_target_id: str,
        operation_type: str,
        result: dict,
    ) -> dict:
        return normalize_operation_outcome(self._require_mcp_operations().store_terminal(
            actor_target_id, operation_type, result
        ))

    def mcp_create_direct(self, **values) -> dict:
        return normalize_operation_outcome(self._require_mcp_operations().create_direct(**values))

    def mcp_modify_direct(self, **values) -> dict:
        terminal_results = {
            AcceptanceCommitFact.NOTE_MISSING: notes_operation_result(
                OperationStatus.NOT_FOUND, values["request_id"], values["note_id"], "",
                HandlingMode.DIRECT, False, False, "未找到笔记。",
            ),
            AcceptanceCommitFact.NOTE_CHANGED: notes_operation_result(
                OperationStatus.CONFLICT, values["request_id"], values["note_id"], "",
                HandlingMode.NONE, False, False, "笔记已被更新。",
            ),
        }
        return normalize_operation_outcome(self._require_mcp_operations().modify_direct(
            **values, terminal_results=terminal_results,
        ))

    def pending_metadata_changes(self, receipt_id: int) -> dict[str, object]:
        """Return only validated changes from the stored pending payload."""
        receipt = self._receipts.get(receipt_id)
        if receipt is None or not receipt.pending or receipt.operation_type != "metadata":
            raise ValueError("该回执不是待确认的元数据提案。")
        raw = self._require_mcp_operations().pending_operation_payload(receipt_id)
        if raw is None:
            raise ValueError("元数据提案内容不存在。")
        return parse_pending_metadata_payload(raw)

    def pending_metadata_display_changes(self, receipt_id: int) -> dict[str, object]:
        """Describe the stored target; infer tag deltas only at its exact version."""
        changes = self.pending_metadata_changes(receipt_id)
        if "tags" not in changes or self._notes is None or self._tags is None:
            return changes
        receipt = self._receipts.get(receipt_id)
        if receipt is None or receipt.target_note_id is None:
            return changes
        note = self._notes.get_note(receipt.target_note_id)
        if note is None or note.updated_at != receipt.expected_updated_at:
            # Legacy payload stores final tags, not historical add/remove intent.
            return changes
        current_tags = self._tags.get_note_tags(note.id)
        if self._notes.get_note(note.id) != note or self._receipts.get(receipt_id) != receipt:
            return changes
        desired_tags = changes["tags"]
        return {
            **changes,
            "added_tags": tuple(tag for tag in desired_tags if tag not in current_tags),
            "removed_tags": tuple(tag for tag in current_tags if tag not in desired_tags),
        }

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

    def _note_fingerprint(self, note: Note) -> AiNoteFingerprint:
        metadata = self._require_lifecycle_dependencies()[2].get_note_metadata(note.id)
        return AiNoteFingerprint(
            note_id=note.id,
            revision=note.revision,
            updated_at=note.updated_at,
            content_markdown=note.content_markdown,
            ai_visibility=note.ai_visibility,
            target_ids=tuple(sorted(metadata.target_ids)),
            directory_id=note.directory_id,
        )

    def _fingerprint_can_accept(
        self,
        fingerprint: AiNoteFingerprint,
        actor_target_id: str,
        *,
        edit: bool,
    ) -> bool:
        return self._authorization.can_write_note(
            resolve_target_id(actor_target_id),
            AiSafeReadSource(
                note_id=fingerprint.note_id, title="", content=fingerprint.content_markdown,
                visibility=fingerprint.ai_visibility, targets=fingerprint.target_ids,
            ),
            context=AiWriteExecutionContext.HUMAN_ACCEPTANCE,
            exact_edit=edit,
        )

    def _accept_pending_modification(
        self,
        receipt: AiReceiptInboxItem,
        fields: AiReceiptAcceptFields,
    ) -> dict:
        notes, _tags, _metadata = self._require_lifecycle_dependencies()
        operations = self._require_mcp_operations()
        raw_payload = operations.pending_operation_payload(receipt.id)
        operation_type = receipt.operation_type
        accepted_body = fields.body if operation_type in {"append", "prepend"} else None
        current_note = (
            notes.get_note(receipt.target_note_id)
            if receipt.target_note_id is not None
            else None
        )
        outcome_status = OperationStatus.UPDATED
        failure_reason: str | None = None
        canonical_markdown: str | None = None
        desired_title: str | None = None
        location: NoteLocation | None = None
        desired_tags: tuple[str, ...] | None = None
        directory_id: int | None = None
        directory_snapshot: tuple[Directory, ...] = ()
        current_fingerprint = (
            self._note_fingerprint(current_note) if current_note is not None else None
        )

        actor_id = resolve_target_id(receipt.actor_target_id)
        if (
            receipt.parse_status != "parsed"
            or not receipt.request_id
            or actor_id not in AI_ACTOR_IDS
            or receipt.target_note_id is None
            or not receipt.expected_updated_at
        ):
            outcome_status = OperationStatus.REJECTED
            failure_reason = "提案缺少必要的身份、目标或版本信息。"
        elif current_note is None:
            outcome_status = OperationStatus.REJECTED
            failure_reason = "目标笔记不存在。"
        elif current_fingerprint is None or not self._fingerprint_can_accept(
            current_fingerprint,
            receipt.actor_target_id,
            edit=operation_type == "edit",
        ):
            outcome_status = OperationStatus.REJECTED
            failure_reason = "当前 AI 身份已无权修改此笔记。"
        elif current_note.updated_at != receipt.expected_updated_at:
            outcome_status = OperationStatus.CONFLICT
            failure_reason = "笔记已被更新。"
        elif raw_payload is None:
            outcome_status = OperationStatus.REJECTED
            failure_reason = (
                "元数据提案内容不存在。"
                if operation_type == "metadata"
                else "正文提案内容不存在。"
            )
        else:
            try:
                if operation_type == "metadata":
                    changes = parse_pending_metadata_payload(raw_payload)
                    desired_title = changes.get("title")
                    desired_tags = changes.get("tags")
                    if "directory" in changes:
                        location = NoteLocation(*changes["directory"])
                        selected_directory = self._authorization.resolve_directory(location=location)
                        if selected_directory is None:
                            outcome_status = OperationStatus.REJECTED
                            failure_reason = "目标目录不可用；只能选择有效的已有目录。"
                        else:
                            directory_id = selected_directory.id
                            directory_snapshot = selected_directory.snapshot
                elif operation_type == "edit":
                    edits = parse_pending_body_payload(raw_payload, operation_type)
                    assert isinstance(edits, list)
                    candidate = build_canonical_edit_candidate(
                        current_note.content_markdown,
                        edits,
                        canonicalize=self.canonicalize_markdown,
                    )
                    canonical_markdown = candidate.final_markdown
                else:
                    parse_pending_body_payload(raw_payload, operation_type)
                    candidate = build_canonical_note_candidate(
                        current_note.content_markdown,
                        accepted_body,
                        operation_type,
                        canonicalize=self.canonicalize_markdown,
                    )
                    canonical_markdown = candidate.final_markdown
                if operation_type != "metadata" and not canonical_markdown.strip():
                    raise CanonicalizationError("Empty canonical body")
            except CanonicalizationError:
                outcome_status = OperationStatus.REJECTED
                failure_reason = "无法生成稳定的 Markdown 正文。"
            except (TypeError, ValueError) as error:
                outcome_status = OperationStatus.REJECTED
                failure_reason = str(error)

        # Canonicalization may run a nested Qt loop. Re-read every business
        # input before handing a prepared commit to persistence.
        latest_receipt = self._receipts.get(receipt.id)
        latest_payload = operations.pending_operation_payload(receipt.id)
        latest_note = (
            notes.get_note(receipt.target_note_id)
            if receipt.target_note_id is not None
            else None
        )
        latest_fingerprint = (
            self._note_fingerprint(latest_note) if latest_note is not None else None
        )
        expected_fingerprint = latest_fingerprint
        if latest_receipt != receipt or latest_payload != raw_payload:
            outcome_status = OperationStatus.CONFLICT
            failure_reason = "提案已被更新，请刷新后重试。"
            expected_fingerprint = (
                current_fingerprint
            )
        elif latest_fingerprint is not None and not self._fingerprint_can_accept(
            latest_fingerprint,
            receipt.actor_target_id,
            edit=operation_type == "edit",
        ):
            outcome_status = OperationStatus.REJECTED
            failure_reason = "当前 AI 身份已无权修改此笔记。"
            expected_fingerprint = latest_fingerprint
        elif current_note is not None and latest_note != current_note:
            outcome_status = OperationStatus.CONFLICT
            failure_reason = "笔记已被更新。"
            expected_fingerprint = current_fingerprint
        elif location is not None and outcome_status == OperationStatus.UPDATED:
            selected_directory = self._authorization.resolve_directory(location=location)
            if selected_directory is None:
                outcome_status = OperationStatus.REJECTED
                failure_reason = "目标目录不可用；只能选择有效的已有目录。"
                directory_snapshot = ()
            else:
                directory_id = selected_directory.id
                directory_snapshot = selected_directory.snapshot

        def terminal(status, reason):
            return notes_operation_result(
                status, receipt.request_id or "", receipt.target_note_id, receipt.title,
                HandlingMode.NONE if status == OperationStatus.CONFLICT else HandlingMode.HUMAN_REJECTED,
                False, False, reason, proposal_id=receipt.id,
            )

        terminal_results = {
            AcceptanceCommitFact.PREPARED_DECISION: terminal(outcome_status, failure_reason or "接受修改提案失败。"),
            AcceptanceCommitFact.RECEIPT_CHANGED: terminal(OperationStatus.CONFLICT, "提案已被更新，请刷新后重试。"),
            AcceptanceCommitFact.OPERATION_MISSING: terminal(OperationStatus.REJECTED, "提案的操作记录不存在。"),
            AcceptanceCommitFact.OPERATION_CHANGED: terminal(OperationStatus.CONFLICT, "提案的操作记录已改变。"),
            AcceptanceCommitFact.NOTE_MISSING: terminal(OperationStatus.REJECTED, "目标笔记不存在。"),
            AcceptanceCommitFact.NOTE_CHANGED: terminal(OperationStatus.CONFLICT, "笔记已被更新。"),
            AcceptanceCommitFact.DIRECTORY_CHANGED: terminal(OperationStatus.CONFLICT, "目标目录已被更新，请刷新后重试。"),
        }
        committed = operations.accept_pending_modification(
            receipt_id=receipt.id,
            expected_receipt=receipt,
            terminal_results=terminal_results,
            expected_payload=raw_payload,
            accepted_body=accepted_body,
            canonical_markdown=canonical_markdown,
            expected_note_fingerprint=expected_fingerprint,
            expected_directory_id=directory_id,
            expected_directory_snapshot=directory_snapshot,
            desired_title=desired_title,
            location=location,
            tags=desired_tags,
            apply_modification=outcome_status == OperationStatus.UPDATED,
        )
        return normalize_operation_outcome(committed.operation)

    def receipt_outcome(self, receipt: AiReceiptInboxItem) -> AcceptanceOutcome:
        if receipt.pending:
            return AcceptanceOutcome(AcceptanceKind.PENDING)
        operation = self.mcp_get_operation(receipt.request_id, receipt.actor_target_id) if receipt.request_id else None
        return operation_outcome(operation or {"status": receipt.status})

    def accept_receipt(
        self, receipt_id: int, fields: AiReceiptAcceptFields,
    ) -> AcceptReceiptResult:
        try:
            return self._accept_receipt(receipt_id, fields)
        except (ValueError, CanonicalizationError) as error:
            return AcceptReceiptResult(
                self._receipts.get(receipt_id), None, False, False,
                AcceptanceOutcome(AcceptanceKind.REJECTED, str(error)),
            )

    def _accept_receipt(
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
            return AcceptReceiptResult(receipt, None, False, True, self.receipt_outcome(receipt))
        if receipt.parse_status != "parsed" and not fields.allow_failed_parse:
            raise ValueError("该回执解析失败，需要人工确认后才能入库。")
        if receipt.parse_status != "parsed" and fields.allow_failed_parse:
            if not fields.title.strip():
                raise ValueError("人工确认入库前，标题不能为空。")
            if not fields.main_category.strip():
                raise ValueError("人工确认入库前，主分类不能为空。")

        if receipt.operation_type in {
            "append", "prepend", "edit", "metadata"
        }:
            result = self._accept_pending_modification(receipt, fields)
            outcome = operation_outcome(result)
            if not outcome.accepted:
                return AcceptReceiptResult(self._receipts.get(receipt.id), None, False, False, outcome)
            persisted_receipt = self._receipts.get(receipt.id)
            note = notes.get_note(result["note_id"])
            if persisted_receipt is None or note is None:
                raise RuntimeError("接受修改提案后读取正式笔记失败。")
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
            if self._authorization.resolve_directory(location=location) is None:
                raise ValueError("目标目录不可用；只能选择有效的已有目录。")
        visibility, target_ids = ai_metadata.prepare_values(
            fields.visibility,
            fields.target_ids,
        )

        command = AiReceiptAcceptCommand(
            title=fields.title.strip() or "未命名 AI 回执",
            content_markdown=canonicalize_new_note(
                fields.body,
                canonicalize=self.canonicalize_markdown,
            ),
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
        if not text or text.casefold() in {"无", HandlingMode.NONE, "null"}:
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
