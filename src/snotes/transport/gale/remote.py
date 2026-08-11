"""Narrow SSH/SCP adapters for the frozen Oracle Gale protocols."""

from __future__ import annotations

import json
import re
import subprocess
import uuid
from pathlib import Path

from src.snotes.application.ai_actor_registry import AI_ACTOR_GALE_ID
from src.snotes.application.ports import GaleOutboxTransport, GalePublishTransport
from src.snotes.domain.gale import (
    GALE_STAGING_SOURCE_ACTOR,
    OUTBOX_SCHEMA_VERSION,
    GaleActivationResult,
    GaleError,
    GaleSubmission,
)


REMOTE_HOST = "oracle-a1"
REMOTE_INCOMING = "/var/lib/snotes-gale-mcp/mirror/incoming"
REMOTE_ACTIVATOR = "/usr/local/sbin/snotes-gale-activate"
REMOTE_OUTBOX = "/usr/local/sbin/snotes-gale-r1b-production-outbox"
REMOTE_STAGING_OUTBOX = "/usr/local/sbin/snotes-gale-r1b-outbox"
MAX_TRANSPORT_BYTES = 6 * 1024 * 1024
MAX_EXPORT_COUNT = 100
IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
ISOLATED_INCOMING_PATTERN = re.compile(r"^/tmp/snotes-p6-gateb-[A-Za-z0-9._-]+$")
OUTBOX_MODES = {
    "production": (REMOTE_OUTBOX, AI_ACTOR_GALE_ID),
    "staging": (REMOTE_STAGING_OUTBOX, GALE_STAGING_SOURCE_ACTOR),
}


class SystemOraclePublishTransport(GalePublishTransport):
    def __init__(
        self,
        *,
        upload_timeout_seconds: int = 60,
        activation_timeout_seconds: int = 120,
        incoming_directory: str = REMOTE_INCOMING,
    ) -> None:
        self.upload_timeout_seconds = int(upload_timeout_seconds)
        self.activation_timeout_seconds = int(activation_timeout_seconds)
        if (
            incoming_directory != REMOTE_INCOMING
            and not ISOLATED_INCOMING_PATTERN.fullmatch(incoming_directory)
        ):
            raise GaleError("remote_incoming_invalid")
        self.incoming_directory = incoming_directory

    def upload_candidate(self, mirror_path: Path, sha256: str) -> None:
        _validate_hash(sha256)
        completed = _run(
            [
                "/usr/bin/scp", "-q", "-p", "--", str(mirror_path),
                f"{REMOTE_HOST}:{self.incoming_directory}/{sha256}.db",
            ],
            timeout=self.upload_timeout_seconds,
        )
        if completed.returncode != 0:
            raise GaleError("upload_failed")

    def activate_candidate(
        self, sha256: str, note_count: int
    ) -> GaleActivationResult:
        _validate_hash(sha256)
        if not 0 <= int(note_count) <= 9_223_372_036_854_775_807:
            raise GaleError("invalid_note_count")
        completed = _run(
            [
                "/usr/bin/ssh", "-o", "BatchMode=yes", "-o",
                "ConnectTimeout=10", REMOTE_HOST, "sudo", "-n",
                REMOTE_ACTIVATOR, sha256, str(int(note_count)),
            ],
            timeout=self.activation_timeout_seconds,
        )
        mapping = {
            0: GaleActivationResult("success", "none"),
            3: GaleActivationResult("duplicate", "same_hash"),
            10: GaleActivationResult("failed", "publisher_busy"),
            20: GaleActivationResult("failed", "remote_validation_failed"),
            30: GaleActivationResult(
                "failed", "activation_failed_rolled_back", rolled_back=True
            ),
            31: GaleActivationResult(
                "failed", "rollback_failed", rolled_back=True,
                previous_preserved=False,
            ),
        }
        if completed.returncode not in mapping:
            raise GaleError("activation_unreachable")
        return mapping[completed.returncode]

    def health(self, sha256: str) -> bool:
        _validate_hash(sha256)
        # The frozen activator returns success only after its own read/search probe.
        return True

    def rollback(
        self,
        expected_current_sha256: str,
        expected_previous_sha256: str,
    ) -> GaleActivationResult:
        _validate_hash(expected_current_sha256)
        _validate_hash(expected_previous_sha256)
        completed = _run(
            [
                "/usr/bin/ssh", "-o", "BatchMode=yes", "-o",
                "ConnectTimeout=10", REMOTE_HOST, "sudo", "-n",
                REMOTE_ACTIVATOR, "rollback", "--expected-current-sha256",
                expected_current_sha256, "--expected-previous-sha256",
                expected_previous_sha256,
            ],
            timeout=self.activation_timeout_seconds,
        )
        mapping = {
            0: GaleActivationResult("success", "operator_rollback"),
            10: GaleActivationResult("failed", "publisher_busy"),
            20: GaleActivationResult("failed", "remote_validation_failed"),
            30: GaleActivationResult(
                "failed", "health_check_failed_restored", rolled_back=True
            ),
            31: GaleActivationResult(
                "failed", "rollback_failed", previous_preserved=False
            ),
        }
        if completed.returncode not in mapping:
            raise GaleError("rollback_unreachable")
        return mapping[completed.returncode]


class SystemOracleOutboxTransport(GaleOutboxTransport):
    def __init__(
        self, *, timeout_seconds: int = 30, mode: str = "production"
    ) -> None:
        self.timeout_seconds = int(timeout_seconds)
        try:
            self.remote_outbox, self.expected_source_actor = OUTBOX_MODES[mode]
        except KeyError as error:
            raise GaleError("outbox_mode_invalid") from error
        self.mode = mode

    def export_unacknowledged(self) -> tuple[GaleSubmission, ...]:
        return parse_export_payload(
            self._run_remote("export"),
            expected_source_actor=self.expected_source_actor,
        )

    def acknowledge(self, submission_uuid: str, local_receipt_id: str) -> None:
        normalized = str(uuid.UUID(str(submission_uuid)))
        if not re.fullmatch(r"[1-9][0-9]{0,18}", str(local_receipt_id)):
            raise GaleError("local_receipt_id_invalid")
        self._run_remote("ack", normalized, str(local_receipt_id))

    def status(self) -> dict[str, int]:
        payload = _decode_json(self._run_remote("status"), "remote_status_invalid")
        if not isinstance(payload, dict) or set(payload) != {
            "queued", "delivered", "acknowledged"
        }:
            raise GaleError("remote_status_invalid")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in payload.values()
        ):
            raise GaleError("remote_status_invalid")
        return {key: int(value) for key, value in payload.items()}

    def _run_remote(self, *arguments: str) -> bytes:
        completed = _run(
            [
                "/usr/bin/ssh", "-o", "BatchMode=yes", "-o",
                "ConnectTimeout=10", REMOTE_HOST, "sudo", "-n", "-u",
                "snotes-gale", self.remote_outbox, *arguments,
            ],
            timeout=self.timeout_seconds,
        )
        if completed.returncode != 0:
            raise GaleError("remote_command_failed")
        if len(completed.stdout) > MAX_TRANSPORT_BYTES:
            raise GaleError("remote_payload_too_large")
        return completed.stdout


def parse_export_payload(
    raw: bytes, *, expected_source_actor: str = AI_ACTOR_GALE_ID
) -> tuple[GaleSubmission, ...]:
    payload = _decode_json(raw, "remote_payload_invalid")
    if not isinstance(payload, dict) or payload.get("schema_version") != OUTBOX_SCHEMA_VERSION:
        raise GaleError("remote_schema_unsupported")
    values = payload.get("submissions")
    if not isinstance(values, list) or len(values) > MAX_EXPORT_COUNT:
        raise GaleError("remote_payload_invalid")
    if expected_source_actor not in {AI_ACTOR_GALE_ID, GALE_STAGING_SOURCE_ACTOR}:
        raise GaleError("remote_actor_invalid")
    return tuple(_submission(item, expected_source_actor) for item in values)


def _submission(value: object, expected_source_actor: str) -> GaleSubmission:
    required = {
        "submission_uuid", "source_actor", "external_submission_id",
        "idempotency_key", "title", "content", "suggested_directory",
        "suggested_tags", "created_at", "delivery_state", "delivered_at",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise GaleError("remote_submission_invalid")
    submission_uuid = _uuid(value["submission_uuid"])
    if value["source_actor"] != expected_source_actor:
        raise GaleError("remote_actor_invalid")
    if value["external_submission_id"] != submission_uuid:
        raise GaleError("external_submission_id_invalid")
    key = str(value["idempotency_key"] or "")
    if not IDEMPOTENCY_PATTERN.fullmatch(key):
        raise GaleError("idempotency_key_invalid")
    title = _text(value["title"], 200, "title")
    content = _text(value["content"], 50_000, "content")
    directory = _text(value["suggested_directory"], 200, "suggested_directory", required=False)
    tags = value["suggested_tags"]
    if (
        not isinstance(tags, list) or len(tags) > 20
        or any(not isinstance(tag, str) or not tag.strip() or len(tag.strip()) > 50 for tag in tags)
    ):
        raise GaleError("suggested_tags_invalid")
    if value["delivery_state"] != "delivered":
        raise GaleError("remote_delivery_state_invalid")
    return GaleSubmission(
        submission_uuid, expected_source_actor, submission_uuid, key, title, content,
        directory, tuple(dict.fromkeys(tag.strip() for tag in tags)),
        _text(value["created_at"], 40, "created_at"), "delivered",
        _text(value["delivered_at"], 40, "delivered_at"),
    )


def _run(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GaleError("transport_unavailable") from error


def _decode_json(raw: bytes, code: str):
    if len(raw) > MAX_TRANSPORT_BYTES:
        raise GaleError("remote_payload_too_large")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GaleError(code) from error


def _uuid(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as error:
        raise GaleError("submission_uuid_invalid") from error


def _text(value: object, maximum: int, field: str, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise GaleError(f"{field}_invalid")
    text = value.strip()
    if required and not text:
        raise GaleError(f"{field}_required")
    if len(text) > maximum:
        raise GaleError(f"{field}_too_long")
    return text


def _validate_hash(value: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", str(value)):
        raise GaleError("invalid_sha256")
