"""Narrow SSH adapter for the Step 10 GreenCloud proposal outbox."""

from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Mapping

from src.snotes.application.ports import GreenCloudOutboxTransport
from src.snotes.domain.greencloud import (
    GREEN_CLOUD_ACTORS,
    GREEN_CLOUD_OPERATIONS,
    GREEN_CLOUD_TERMINAL_STATES,
    GreenCloudError,
    GreenCloudSubmission,
)


DEFAULT_SSH_HOST = "optimal"
DEFAULT_SSH_ACCOUNT = "snotes-publish"
DEFAULT_SSH_IDENTITY = Path.home() / ".ssh" / "snotes-greencloud-mirror"
MAX_TRANSPORT_BYTES = 64 * 1024 * 1024
MAX_EXPORT_COUNT = 100
MAX_REQUEST_ID_LENGTH = 128
MAX_RECEIPT_BYTES = 50_000
_REQUEST_ID = re.compile(r"^\S{1,128}$")
_VERSION = re.compile(r"^snotes-v1-[0-9a-f]{64}$")


class SystemGreenCloudOutboxTransport(GreenCloudOutboxTransport):
    """Use the existing mirror key and its three fixed forced commands."""

    def __init__(
        self,
        *,
        host: str = DEFAULT_SSH_HOST,
        account: str = DEFAULT_SSH_ACCOUNT,
        identity_file: Path = DEFAULT_SSH_IDENTITY,
        timeout_seconds: int = 30,
    ) -> None:
        self.host = str(host)
        self.account = str(account)
        self.identity_file = Path(identity_file)
        self.timeout_seconds = int(timeout_seconds)

    def export_unacknowledged(self) -> tuple[GreenCloudSubmission, ...]:
        response = self._request("notes-outbox-export", {})
        if (
            set(response) != {"status", "source", "schema_version", "submissions"}
            or response.get("status") != "ok"
            or response.get("source") != "greencloud"
            or response.get("schema_version") != 3
        ):
            raise GreenCloudError("remote_export_invalid")
        values = response.get("submissions")
        if not isinstance(values, list) or len(values) > MAX_EXPORT_COUNT:
            raise GreenCloudError("remote_export_invalid")
        return tuple(_submission(value) for value in values)

    def acknowledge(self, submission_uuid: str, local_receipt_id: str) -> None:
        normalized_uuid = _uuid(submission_uuid)
        if not re.fullmatch(r"[1-9][0-9]{0,18}", str(local_receipt_id)):
            raise GreenCloudError("local_receipt_id_invalid")
        response = self._request(
            "notes-outbox-acknowledge",
            {
                "submission_uuid": normalized_uuid,
                "local_receipt_id": str(local_receipt_id),
            },
        )
        _validate_mutation_response(response, expected_uuid=normalized_uuid)

    def write_terminal_receipt(
        self,
        source_actor: str,
        request_id: str,
        terminal_state: str,
        receipt_payload: Mapping[str, object] | None = None,
    ) -> None:
        if source_actor not in GREEN_CLOUD_ACTORS:
            raise GreenCloudError("source_actor_invalid")
        if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
            raise GreenCloudError("request_id_invalid")
        if terminal_state not in GREEN_CLOUD_TERMINAL_STATES:
            raise GreenCloudError("terminal_state_invalid")
        payload = dict(receipt_payload or {})
        try:
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        except (TypeError, ValueError) as error:
            raise GreenCloudError("receipt_payload_invalid") from error
        if len(encoded) > MAX_RECEIPT_BYTES:
            raise GreenCloudError("receipt_payload_too_large")
        response = self._request(
            "notes-outbox-receipt",
            {
                "source_actor": source_actor,
                "request_id": request_id,
                "terminal_state": terminal_state,
                "receipt_payload": payload,
            },
        )
        _validate_mutation_response(
            response,
            expected_request_id=request_id,
            expected_state=terminal_state,
        )

    def _request(self, command: str, payload: dict) -> dict:
        self._validate_identity()
        raw = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        try:
            completed = subprocess.run(
                [
                    "/usr/bin/ssh", "-T", "-o", "BatchMode=yes", "-o",
                    "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes", "-o",
                    "ConnectTimeout=10", "-o", "RequestTTY=no", "-i",
                    str(self.identity_file), f"{self.account}@{self.host}", command,
                ],
                input=raw,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise GreenCloudError("transport_timeout") from error
        except OSError as error:
            raise GreenCloudError("transport_unavailable") from error
        if completed.returncode != 0:
            raise GreenCloudError("remote_command_failed")
        if len(completed.stdout) > MAX_TRANSPORT_BYTES:
            raise GreenCloudError("remote_payload_too_large")
        try:
            response = json.loads(completed.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GreenCloudError("remote_payload_invalid") from error
        if not isinstance(response, dict):
            raise GreenCloudError("remote_payload_invalid")
        return response

    def _validate_identity(self) -> None:
        try:
            metadata = self.identity_file.stat()
        except OSError as error:
            raise GreenCloudError("ssh_identity_unavailable") from error
        if self.identity_file.is_symlink() or not self.identity_file.is_file():
            raise GreenCloudError("ssh_identity_unavailable")
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
            raise GreenCloudError("ssh_identity_unsafe")


def parse_export_payload(raw: bytes) -> tuple[GreenCloudSubmission, ...]:
    """Parse a captured response without invoking SSH (used by focused tests)."""
    if len(raw) > MAX_TRANSPORT_BYTES:
        raise GreenCloudError("remote_payload_too_large")
    try:
        response = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GreenCloudError("remote_payload_invalid") from error
    if (
        not isinstance(response, dict)
        or set(response) != {"status", "source", "schema_version", "submissions"}
        or response.get("status") != "ok"
        or response.get("source") != "greencloud"
        or response.get("schema_version") != 3
        or not isinstance(response.get("submissions"), list)
        or len(response["submissions"]) > MAX_EXPORT_COUNT
    ):
        raise GreenCloudError("remote_export_invalid")
    return tuple(_submission(value) for value in response["submissions"])


def _submission(value: object) -> GreenCloudSubmission:
    required = {
        "submission_uuid", "source_actor", "request_id", "operation_type",
        "note_id", "expected_version", "payload", "created_at",
        "delivery_state", "delivered_at",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise GreenCloudError("remote_submission_invalid")
    submission_uuid = _uuid(value["submission_uuid"])
    actor = value["source_actor"]
    request_id = value["request_id"]
    operation_type = value["operation_type"]
    if actor not in GREEN_CLOUD_ACTORS:
        raise GreenCloudError("source_actor_invalid")
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        raise GreenCloudError("request_id_invalid")
    if operation_type not in GREEN_CLOUD_OPERATIONS:
        raise GreenCloudError("operation_type_invalid")
    if value["delivery_state"] != "delivered":
        raise GreenCloudError("remote_delivery_state_invalid")
    note_id = value["note_id"]
    expected_version = value["expected_version"]
    if operation_type == "create":
        if note_id is not None or expected_version is not None:
            raise GreenCloudError("operation_envelope_invalid")
        normalized_note_id = None
    else:
        if (
            not isinstance(note_id, str)
            or not note_id.isascii()
            or not note_id.isdigit()
            or not 1 <= int(note_id) <= 9_223_372_036_854_775_807
            or not isinstance(expected_version, str)
            or not _VERSION.fullmatch(expected_version)
        ):
            raise GreenCloudError("operation_envelope_invalid")
        normalized_note_id = int(note_id)
    payload = value["payload"]
    if not isinstance(payload, dict):
        raise GreenCloudError("operation_payload_invalid")
    try:
        payload_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    except (TypeError, ValueError) as error:
        raise GreenCloudError("operation_payload_invalid") from error
    if len(payload_bytes) > 512 * 1024:
        raise GreenCloudError("operation_payload_too_large")
    return GreenCloudSubmission(
        submission_uuid=submission_uuid,
        source_actor=actor,
        request_id=request_id,
        operation_type=operation_type,
        note_id=normalized_note_id,
        expected_version=expected_version,
        payload=dict(payload),
        created_at=_text(value["created_at"], 40, "created_at"),
        delivery_state="delivered",
        delivered_at=_text(value["delivered_at"], 40, "delivered_at"),
    )


def _validate_mutation_response(
    response: dict,
    *,
    expected_uuid: str | None = None,
    expected_request_id: str | None = None,
    expected_state: str | None = None,
) -> None:
    if response.get("status") != "ok":
        raise GreenCloudError("remote_mutation_failed")
    if expected_uuid is not None and response.get("submission_uuid") != expected_uuid:
        raise GreenCloudError("remote_receipt_mismatch")
    if expected_request_id is not None and response.get("request_id") != expected_request_id:
        raise GreenCloudError("remote_receipt_mismatch")
    if expected_state is not None and response.get("delivery_state") != expected_state:
        raise GreenCloudError("remote_receipt_mismatch")


def _uuid(value: object) -> str:
    try:
        normalized = str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as error:
        raise GreenCloudError("submission_uuid_invalid") from error
    if value != normalized:
        raise GreenCloudError("submission_uuid_invalid")
    return normalized


def _text(value: object, maximum: int, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise GreenCloudError(f"{field}_invalid")
    return value
