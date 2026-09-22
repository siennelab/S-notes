"""Package 6 application orchestration for Gale mirror, publish, and pull."""

from __future__ import annotations

import uuid
import time
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from src.snotes.application.ai_audit_service import AiAuditService
from src.snotes.application.ai_actor_registry import AI_ACTOR_GALE_ID
from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_receipt_inbox_service import AiReceiptInboxService
from src.snotes.application.ai_safe_read_service import (
    AiSafeReadService,
    AiSafeReadSource,
    RedactionUnavailableError,
)
from src.snotes.application.directory_service import DirectoryService
from src.snotes.application.local_mcp_write_service import note_version_token
from src.snotes.application.ports import (
    GaleMirrorWriter,
    GaleOutboxTransport,
    GalePublishTransport,
    GaleRuntimeSettings,
    NotesRepository,
)
from src.snotes.domain.ai_receipts import GaleReceiptImport
from src.snotes.domain.gale import (
    GALE_STAGING_SOURCE_ACTOR,
    GaleError,
    GaleMirrorRecord,
    GaleMirrorResult,
    GalePublishResult,
    GalePullResult,
)
from src.snotes.domain.models import NoteLocation
from src.snotes.domain.tags import parse_tags
from src.snotes.application.tag_service import TagService


class GaleMirrorService:
    """Build the Oracle-compatible mirror only from Package 4 safe reads."""

    def __init__(
        self,
        notes: NotesRepository,
        safe_reads: AiSafeReadService,
        writer: GaleMirrorWriter,
        audit: AiAuditService,
        *,
        directories: DirectoryService | None = None,
        tags: TagService | None = None,
        ai_metadata: AiAccessMetadataService | None = None,
    ) -> None:
        self._notes = notes
        self._safe_reads = safe_reads
        self._writer = writer
        self._audit = audit
        self._directories = directories
        self._tags = tags
        self._ai_metadata = ai_metadata

    def generate(self, output_path: Path) -> GaleMirrorResult:
        request_id = uuid.uuid4().hex
        Path(output_path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        records, redaction_unavailable = self._safe_records()
        try:
            result = self._writer.write(
                records,
                Path(output_path),
                actor_target_id=AI_ACTOR_GALE_ID,
                **self._metadata_catalog(),
            )
        except Exception:
            self._audit.record_event(
                AI_ACTOR_GALE_ID,
                "gale_mirror",
                request_id=request_id,
                status="failed",
                failure_reason="mirror_generation_failed",
            )
            raise
        self._audit.record_event(
            AI_ACTOR_GALE_ID,
            "gale_mirror",
            request_id=request_id,
            result_count=result.note_count,
            status="success",
            failure_reason=(
                "redaction_unavailable" if redaction_unavailable else ""
            ),
        )
        return result

    def source_fingerprint(self) -> tuple[str, int]:
        records, _warning = self._safe_records()
        payload = json.dumps(
            [asdict(record) for record in records],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest(), len(records)

    def _safe_records(self) -> tuple[list[GaleMirrorRecord], bool]:
        records: list[GaleMirrorRecord] = []
        redaction_unavailable = False
        for note in self._notes.list_active():
            stored, target_ids = self._notes.get_ai_access_metadata(note.id)
            source = AiSafeReadSource(
                note_id=note.id,
                title=note.title,
                content=note.content_markdown,
                visibility=stored.ai_visibility,
                targets=tuple(target_ids),
                directory=" / ".join(note.location.values()),
                tags=tuple(parse_tags(note.tags)),
            )
            try:
                safe = self._safe_reads.read_note(AI_ACTOR_GALE_ID, source)
            except RedactionUnavailableError:
                redaction_unavailable = True
                continue
            if safe is None:
                continue
            records.append(
                GaleMirrorRecord(
                    source_note_id=note.id,
                    title=safe.title,
                    body=safe.content,
                    directory=" / ".join(note.location.values()),
                    tags=tuple(parse_tags(note.tags)),
                    source_updated_at=note.updated_at,
                    source_version=note_version_token(note.id, note.updated_at),
                    visibility=safe.visibility,
                    redacted=safe.redacted,
                    redaction_count=safe.redaction_count,
                    ai_visibility=stored.ai_visibility,
                    ai_targets=tuple(target_ids),
                    main_category=note.location.values()[0],
                    sub_category=note.location.values()[1],
                )
            )
        return records, redaction_unavailable

    def _metadata_catalog(self) -> dict:
        directories: list[dict] = []
        if self._directories is not None:
            for directory in self._directories.list_directories():
                path = self._directories.get_directory_path(directory.id)
                directories.append(
                    {
                        "directory_id": directory.id,
                        "name": directory.name,
                        "parent_id": directory.parent_id,
                        "main_category": path[0] if path else directory.name,
                        "sub_category": path[1] if len(path) > 1 else "",
                        "path": path,
                    }
                )
        tags = tuple(self._tags.list_tags()) if self._tags is not None else ()
        visibility_options = (
            self._ai_metadata.available_visibility_options()
            if self._ai_metadata is not None
            else ()
        )
        ai_targets = (
            tuple(
                (target.target_id, target.display_name)
                for target in self._ai_metadata.available_targets()
            )
            if self._ai_metadata is not None
            else ()
        )
        return {
            "directories": tuple(
                sorted(
                    directories,
                    key=lambda item: (
                        tuple(item["path"]),
                        int(item["directory_id"]),
                    ),
                )
            ),
            "tags": tuple(sorted(tags, key=str.casefold)),
            "visibility_options": visibility_options,
            "ai_targets": ai_targets,
        }

    def verify(self, path: Path, *, expected_note_count: int | None = None):
        return self._writer.verify(
            path,
            expected_note_count=expected_note_count,
            expected_actor=AI_ACTOR_GALE_ID,
        )


class GalePublishService:
    """One chain for manual and automatic Oracle candidate activation."""

    LEGACY_LAST_SUCCESS_HASH_KEY = "gale/last_success_hash"
    LAST_SUCCESSFUL_MIRROR_HASH_KEY = "gale/last_successful_mirror_hash"
    LAST_SUCCESSFUL_SOURCE_FINGERPRINT_KEY = (
        "gale/last_successful_source_fingerprint"
    )

    def __init__(
        self,
        mirror: GaleMirrorService,
        transport: GalePublishTransport,
        audit: AiAuditService,
        *,
        mirror_path: Path,
        settings: GaleRuntimeSettings | None = None,
        clock=time.time,
    ) -> None:
        self._mirror = mirror
        self._transport = transport
        self._audit = audit
        self._mirror_path = Path(mirror_path)
        self._settings = settings
        self._clock = clock

    def publish_manual(self) -> GalePublishResult:
        return self._publish("manual")

    def publish_automatic(self) -> GalePublishResult:
        return self._publish("automatic")

    def rollback(
        self,
        expected_current_sha256: str,
        expected_previous_sha256: str,
    ):
        return self._transport.rollback(
            expected_current_sha256, expected_previous_sha256
        )

    def _publish(self, mode: str) -> GalePublishResult:
        request_id = uuid.uuid4().hex
        source_fingerprint = ""
        result: GalePublishResult | None = None
        try:
            if mode == "automatic":
                source_fingerprint, source_count = (
                    self._mirror.source_fingerprint()
                )
                if (
                    source_fingerprint
                    == self._last_successful_source_fingerprint()
                ):
                    result = GalePublishResult(
                        "duplicate",
                        self._last_successful_mirror_hash(),
                        source_count,
                        reason="same_source",
                    )
                elif not self._automatic_due(source_fingerprint):
                    result = GalePublishResult(
                        "deferred", "", source_count, reason="quiet_period"
                    )
            if result is None:
                mirror = self._mirror.generate(self._mirror_path)
                verified = self._mirror.verify(
                    self._mirror_path, expected_note_count=mirror.note_count
                )
                if self._last_successful_mirror_hash() == mirror.sha256:
                    result = GalePublishResult(
                        "duplicate", mirror.sha256, verified.note_count,
                        reason="same_hash",
                    )
                else:
                    self._transport.upload_candidate(
                        self._mirror_path, mirror.sha256
                    )
                    activation = self._transport.activate_candidate(
                        mirror.sha256, verified.note_count
                    )
                    if activation.status == "duplicate":
                        result = GalePublishResult(
                            "duplicate", mirror.sha256, verified.note_count,
                            reason=activation.reason,
                        )
                    elif activation.status != "success":
                        result = GalePublishResult(
                            "failed", mirror.sha256, verified.note_count,
                            reason=activation.reason, uploaded=True,
                            rolled_back=activation.rolled_back,
                            previous_preserved=activation.previous_preserved,
                        )
                    else:
                        result = GalePublishResult(
                            "success", mirror.sha256, verified.note_count,
                            uploaded=True, activated=True,
                        )
        except GaleError as error:
            result = GalePublishResult(
                "failed", "", 0, reason=error.code,
                previous_preserved=error.previous_preserved,
            )
        except Exception:
            result = GalePublishResult(
                "failed", "", 0, reason="unexpected_failure"
            )
        if (
            result.status in {"success", "duplicate"}
            and result.sha256
            and self._settings is not None
        ):
            self._settings.set_text(
                self.LAST_SUCCESSFUL_MIRROR_HASH_KEY, result.sha256
            )
            self._settings.set_text(
                self.LEGACY_LAST_SUCCESS_HASH_KEY, result.sha256
            )
            if mode == "automatic" and source_fingerprint:
                self._settings.set_text(
                    self.LAST_SUCCESSFUL_SOURCE_FINGERPRINT_KEY,
                    source_fingerprint,
                )
            for key in (
                "gale/pending_hash", "gale/pending_first_at",
                "gale/pending_last_at", "gale/retry_not_before",
            ):
                self._settings.set_text(key, "")
            if result.status == "success":
                self._settings.set_text(
                    "gale/last_successful_publish_at",
                    _utc_timestamp(self._clock()),
                )
        elif result.status == "failed" and mode == "automatic" and self._settings:
            self._settings.set_text(
                "gale/retry_not_before", str(self._clock() + 5 * 60)
            )
        self._record_publish_audit(request_id, mode, result)
        return result

    def _record_publish_audit(
        self, request_id: str, mode: str, result: GalePublishResult
    ) -> None:
        self._audit.record_event(
            AI_ACTOR_GALE_ID,
            "gale_publish",
            request_id=request_id,
            result_count=result.note_count if result.status != "failed" else 0,
            handling_mode=mode,
            status=result.status,
            failure_reason="" if result.status != "failed" else result.reason,
            is_duplicate=result.status == "duplicate",
        )

    def _last_successful_mirror_hash(self) -> str:
        if self._settings is None:
            return ""
        return self._settings.get_text(
            self.LAST_SUCCESSFUL_MIRROR_HASH_KEY
        ) or self._settings.get_text(self.LEGACY_LAST_SUCCESS_HASH_KEY)

    def _last_successful_source_fingerprint(self) -> str:
        return (
            self._settings.get_text(
                self.LAST_SUCCESSFUL_SOURCE_FINGERPRINT_KEY
            )
            if self._settings is not None
            else ""
        )

    def _automatic_due(self, sha256: str) -> bool:
        if self._settings is None:
            return True
        now = float(self._clock())
        try:
            retry = float(self._settings.get_text("gale/retry_not_before") or 0)
        except ValueError:
            retry = 0
        if now < retry:
            return False
        pending = self._settings.get_text("gale/pending_hash")
        if pending != sha256:
            self._settings.set_text("gale/pending_hash", sha256)
            if not pending:
                self._settings.set_text("gale/pending_first_at", str(now))
            self._settings.set_text("gale/pending_last_at", str(now))
        try:
            first = float(self._settings.get_text("gale/pending_first_at"))
            last = float(self._settings.get_text("gale/pending_last_at"))
        except ValueError:
            self._settings.set_text("gale/pending_first_at", str(now))
            self._settings.set_text("gale/pending_last_at", str(now))
            return False
        return now - last >= 15 * 60 or now - first >= 30 * 60


class GalePullService:
    """Import pending receipts transactionally, then ACK the remote outbox."""

    def __init__(
        self,
        transport: GaleOutboxTransport,
        receipts: AiReceiptInboxService,
        audit: AiAuditService,
        *,
        expected_source_actor: str = AI_ACTOR_GALE_ID,
    ) -> None:
        if expected_source_actor not in {
            AI_ACTOR_GALE_ID,
            GALE_STAGING_SOURCE_ACTOR,
        }:
            raise GaleError("remote_actor_invalid")
        self._transport = transport
        self._receipts = receipts
        self._audit = audit
        self._expected_source_actor = expected_source_actor

    def pull_once(self) -> GalePullResult:
        request_id = uuid.uuid4().hex
        submissions = tuple(self._transport.export_unacknowledged())
        imported = acknowledged = duplicates = 0
        try:
            for submission in submissions:
                if (
                    submission.source_actor != self._expected_source_actor
                    or submission.delivery_state != "delivered"
                ):
                    raise GaleError("remote_submission_invalid")
                receipt, created = self._receipts.import_gale_submission(
                    GaleReceiptImport(
                        external_submission_id=submission.external_submission_id,
                        idempotency_key=submission.idempotency_key,
                        title=submission.title,
                        body=submission.content,
                        location=_submission_location(submission),
                        tags=_submission_tags(submission),
                        ai_visibility=submission.ai_visibility,
                        ai_targets=submission.ai_targets,
                    )
                )
                imported += int(created)
                duplicates += int(not created)
                try:
                    self._transport.acknowledge(
                        submission.submission_uuid, str(receipt.id)
                    )
                except Exception as error:
                    raise GaleError("ack_failed") from error
                acknowledged += 1
        except Exception as error:
            reason = error.code if isinstance(error, GaleError) else "local_import_failed"
            self._audit.record_event(
                AI_ACTOR_GALE_ID,
                "gale_pull",
                request_id=request_id,
                result_count=imported,
                status="failed",
                failure_reason=reason,
            )
            raise
        result = GalePullResult(
            len(submissions), imported, acknowledged, duplicates
        )
        self._audit.record_event(
            AI_ACTOR_GALE_ID,
            "gale_pull",
            request_id=request_id,
            result_count=imported,
            status="success",
            is_duplicate=bool(duplicates),
        )
        return result


def _location(value: str) -> NoteLocation:
    parts = [part.strip() for part in value.split("/") if part.strip()]
    if len(parts) > 3:
        raise GaleError("suggested_directory_invalid")
    return NoteLocation(
        parts[0] if parts else "临时",
        parts[1] if len(parts) > 1 else "Gale 投稿",
    )


def _submission_location(submission: GaleSubmission) -> NoteLocation:
    if submission.main_category.strip():
        return NoteLocation(
            submission.main_category.strip(),
            submission.sub_category.strip() or "无",
        )
    return _location(submission.suggested_directory)


def _submission_tags(submission: GaleSubmission) -> tuple[str, ...]:
    return submission.tags or submission.suggested_tags


class GaleControlService:
    """Thin UI/background facade; business work stays in the owners above."""

    AUTO_PUBLISH_KEY = "gale/auto_publish_enabled"
    AUTO_PULL_KEY = "gale/auto_pull_enabled"
    LAST_PUBLISH_AT_KEY = "gale/last_successful_publish_at"
    LAST_PULL_AT_KEY = "gale/last_completed_pull_at"

    def __init__(
        self,
        publish: GalePublishService,
        pull: GalePullService,
        settings: GaleRuntimeSettings,
        *,
        clock=time.time,
    ) -> None:
        self._publish = publish
        self._pull = pull
        self._settings = settings
        self._clock = clock

    def manual_update(self) -> GalePublishResult:
        return self._publish.publish_manual()

    def check_submissions_now(self) -> GalePullResult:
        result = self._pull.pull_once()
        self._settings.set_text(
            self.LAST_PULL_AT_KEY, _utc_timestamp(self._clock())
        )
        return result

    def last_successful_publish_at(self) -> str:
        return self._settings.get_text(self.LAST_PUBLISH_AT_KEY)

    def last_completed_pull_at(self) -> str:
        return self._settings.get_text(self.LAST_PULL_AT_KEY)

    def auto_publish_enabled(self) -> bool:
        return self._settings.get_bool(self.AUTO_PUBLISH_KEY, False)

    def set_auto_publish_enabled(self, enabled: bool) -> None:
        self._settings.set_bool(self.AUTO_PUBLISH_KEY, bool(enabled))

    def auto_pull_enabled(self) -> bool:
        return self._settings.get_bool(self.AUTO_PULL_KEY, True)

    def set_auto_pull_enabled(self, enabled: bool) -> None:
        self._settings.set_bool(self.AUTO_PULL_KEY, bool(enabled))

    def scheduled_once(self) -> tuple[GalePullResult | None, GalePublishResult | None]:
        pull_result = (
            self.check_submissions_now() if self.auto_pull_enabled() else None
        )
        publish_result = (
            self._publish.publish_automatic()
            if self.auto_publish_enabled()
            else None
        )
        return pull_result, publish_result


def _utc_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat(
        timespec="seconds"
    )
