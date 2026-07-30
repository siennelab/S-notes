from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from src.services.gale_submission_outbox import (
    GALE_STAGING_ACTOR,
    MAX_CONTENT_LENGTH,
    MAX_DIRECTORY_LENGTH,
    MAX_EXPORT_COUNT,
    MAX_IDEMPOTENCY_KEY_LENGTH,
    MAX_TAG_COUNT,
    MAX_TAG_LENGTH,
    MAX_TITLE_LENGTH,
    OUTBOX_SCHEMA_VERSION,
)


REMOTE_SSH_HOST = "oracle-a1"
REMOTE_OUTBOX_COMMAND = "/usr/local/sbin/snotes-gale-r1b-outbox"
MAX_TRANSPORT_BYTES = 6 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 30
ISOLATED_ROOT_PREFIX = "snotes-r1b-"
LOCAL_RECEIPT_ID_PATTERN = re.compile(r"^[1-9][0-9]{0,18}$")


class GaleSubmissionPullError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class PullResult:
    exported_count: int
    imported_count: int
    acknowledged_count: int
    duplicate_count: int


class SubmissionTransport(Protocol):
    def export_unacknowledged(self) -> bytes: ...

    def acknowledge(
        self,
        submission_uuid: str,
        local_receipt_id: str,
    ) -> None: ...

    def status(self) -> bytes: ...


class SystemSshSubmissionTransport:
    def __init__(
        self,
        *,
        host: str = REMOTE_SSH_HOST,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if host != REMOTE_SSH_HOST:
            raise GaleSubmissionPullError("ssh_host_not_allowed")
        self.host = host
        self.timeout_seconds = int(timeout_seconds)

    def export_unacknowledged(self) -> bytes:
        return self._run_remote("export")

    def acknowledge(
        self,
        submission_uuid: str,
        local_receipt_id: str,
    ) -> None:
        normalized_uuid = _validated_uuid(submission_uuid)
        receipt_id = _validated_local_receipt_id(local_receipt_id)
        self._run_remote("ack", normalized_uuid, receipt_id)

    def status(self) -> bytes:
        return self._run_remote("status")

    def _run_remote(self, *arguments: str) -> bytes:
        command = [
            "/usr/bin/ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            self.host,
            "sudo",
            "-n",
            "-u",
            "snotes-gale",
            REMOTE_OUTBOX_COMMAND,
            *arguments,
        ]
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise GaleSubmissionPullError("ssh_connection_failed") from error
        if completed.returncode != 0:
            raise GaleSubmissionPullError("remote_command_failed")
        if len(completed.stdout) > MAX_TRANSPORT_BYTES:
            raise GaleSubmissionPullError("remote_payload_too_large")
        return completed.stdout


def require_isolated_data_root(
    environment: dict[str, str] | None = None,
) -> Path:
    values = os.environ if environment is None else environment
    if "S_NOTES_DATA_ROOT" not in values:
        raise GaleSubmissionPullError("data_root_override_required")
    raw_root = str(values.get("S_NOTES_DATA_ROOT") or "").strip()
    if not raw_root:
        raise GaleSubmissionPullError("data_root_override_required")
    root = Path(raw_root)
    if not root.is_absolute():
        raise GaleSubmissionPullError("data_root_not_absolute")
    if root.is_symlink():
        raise GaleSubmissionPullError("data_root_symlink")
    resolved = root.resolve(strict=False)
    temporary_root = Path("/private/tmp").resolve()
    if temporary_root not in resolved.parents:
        raise GaleSubmissionPullError("data_root_not_private_tmp")
    if not resolved.name.startswith(ISOLATED_ROOT_PREFIX):
        raise GaleSubmissionPullError("data_root_name_unsafe")
    return resolved


def pull_submissions(
    transport: SubmissionTransport,
    *,
    data_root: Path,
) -> PullResult:
    root = Path(data_root)
    expected_root = require_isolated_data_root(
        {"S_NOTES_DATA_ROOT": str(root)}
    )
    expected_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(expected_root, 0o700)
    database_path = expected_root / "s_notes.db"

    from src.database import (
        create_gale_submission,
        initialize_database,
        list_ai_receipt_inbox_items,
    )

    payload = parse_export_payload(transport.export_unacknowledged())
    if not payload:
        return PullResult(0, 0, 0, 0)

    initialize_database(database_path)
    imported_count = 0
    acknowledged_count = 0
    duplicate_count = 0
    for submission in payload:
        existing = any(
            item["source"] == "Gale"
            and (
                item["external_submission_id"]
                == submission["external_submission_id"]
                or item["idempotency_key"] == submission["idempotency_key"]
            )
            for item in list_ai_receipt_inbox_items(
                status=None,
                database_path=database_path,
            )
        )
        receipt = create_gale_submission(
            external_submission_id=submission["external_submission_id"],
            idempotency_key=submission["idempotency_key"],
            title=submission["title"],
            body=submission["content"],
            main_category=submission["main_category"],
            sub_category=submission["sub_category"],
            sub_sub_category=submission["sub_sub_category"],
            tags=submission["suggested_tags"],
            database_path=database_path,
        )
        if (
            receipt["external_submission_id"]
            != submission["external_submission_id"]
            and receipt["idempotency_key"] != submission["idempotency_key"]
        ):
            raise GaleSubmissionPullError("local_idempotency_conflict")
        receipt_id = str(int(receipt["id"]))
        if existing:
            duplicate_count += 1
        else:
            imported_count += 1
        try:
            transport.acknowledge(
                submission["submission_uuid"],
                receipt_id,
            )
        except Exception as error:
            raise GaleSubmissionPullError("ack_failed") from error
        acknowledged_count += 1
    return PullResult(
        exported_count=len(payload),
        imported_count=imported_count,
        acknowledged_count=acknowledged_count,
        duplicate_count=duplicate_count,
    )


def parse_export_payload(raw_payload: bytes) -> list[dict[str, Any]]:
    if len(raw_payload) > MAX_TRANSPORT_BYTES:
        raise GaleSubmissionPullError("remote_payload_too_large")
    try:
        decoded = raw_payload.decode("utf-8")
        payload = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GaleSubmissionPullError("remote_payload_invalid") from error
    if not isinstance(payload, dict):
        raise GaleSubmissionPullError("remote_payload_invalid")
    if payload.get("schema_version") != OUTBOX_SCHEMA_VERSION:
        raise GaleSubmissionPullError("remote_schema_unsupported")
    submissions = payload.get("submissions")
    if not isinstance(submissions, list) or len(submissions) > MAX_EXPORT_COUNT:
        raise GaleSubmissionPullError("remote_payload_invalid")
    return [_validated_submission(item) for item in submissions]


def parse_status_payload(raw_payload: bytes) -> dict[str, int]:
    try:
        payload = json.loads(raw_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GaleSubmissionPullError("remote_status_invalid") from error
    if not isinstance(payload, dict):
        raise GaleSubmissionPullError("remote_status_invalid")
    expected = {"queued", "delivered", "acknowledged"}
    if set(payload) != expected:
        raise GaleSubmissionPullError("remote_status_invalid")
    counts: dict[str, int] = {}
    for key in expected:
        value = payload[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise GaleSubmissionPullError("remote_status_invalid")
        counts[key] = value
    return counts


def _validated_submission(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GaleSubmissionPullError("remote_submission_invalid")
    required_keys = {
        "submission_uuid",
        "source_actor",
        "external_submission_id",
        "idempotency_key",
        "title",
        "content",
        "suggested_directory",
        "suggested_tags",
        "created_at",
        "delivery_state",
        "delivered_at",
    }
    if set(value) != required_keys:
        raise GaleSubmissionPullError("remote_submission_invalid")
    submission_uuid = _validated_uuid(value["submission_uuid"])
    if value["source_actor"] != GALE_STAGING_ACTOR:
        raise GaleSubmissionPullError("remote_actor_invalid")
    external_id = _required_text(
        value["external_submission_id"],
        200,
        "external_submission_id",
    )
    if external_id != submission_uuid:
        raise GaleSubmissionPullError("external_submission_id_invalid")
    idempotency_key = _required_text(
        value["idempotency_key"],
        MAX_IDEMPOTENCY_KEY_LENGTH,
        "idempotency_key",
    )
    title = _required_text(value["title"], MAX_TITLE_LENGTH, "title")
    content = _required_text(value["content"], MAX_CONTENT_LENGTH, "content")
    directory = _optional_text(
        value["suggested_directory"],
        MAX_DIRECTORY_LENGTH,
        "suggested_directory",
    )
    tags = _validated_tags(value["suggested_tags"])
    created_at = _required_text(value["created_at"], 40, "created_at")
    if value["delivery_state"] != "delivered":
        raise GaleSubmissionPullError("remote_delivery_state_invalid")
    delivered_at = _required_text(value["delivered_at"], 40, "delivered_at")
    main_category, sub_category, sub_sub_category = _directory_parts(directory)
    return {
        "submission_uuid": submission_uuid,
        "external_submission_id": external_id,
        "idempotency_key": idempotency_key,
        "title": title,
        "content": content,
        "suggested_tags": tags,
        "created_at": created_at,
        "delivered_at": delivered_at,
        "main_category": main_category,
        "sub_category": sub_category,
        "sub_sub_category": sub_sub_category,
    }


def _directory_parts(value: str) -> tuple[str, str, str]:
    parts = [part.strip() for part in value.split("/") if part.strip()]
    if not parts:
        return "临时", "Gale 投稿", "待确认"
    if len(parts) > 3:
        raise GaleSubmissionPullError("suggested_directory_invalid")
    return (
        parts[0],
        parts[1] if len(parts) > 1 else "无",
        parts[2] if len(parts) > 2 else "无",
    )


def _validated_tags(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_TAG_COUNT:
        raise GaleSubmissionPullError("suggested_tags_invalid")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise GaleSubmissionPullError("suggested_tags_invalid")
        tag = item.strip()
        if not tag or len(tag) > MAX_TAG_LENGTH:
            raise GaleSubmissionPullError("suggested_tags_invalid")
        if tag not in seen:
            seen.add(tag)
            result.append(tag)
    return result


def _required_text(value: object, maximum: int, field: str) -> str:
    if not isinstance(value, str):
        raise GaleSubmissionPullError(f"{field}_invalid")
    text = value.strip()
    if not text:
        raise GaleSubmissionPullError(f"{field}_required")
    if len(text) > maximum:
        raise GaleSubmissionPullError(f"{field}_too_long")
    return text


def _optional_text(value: object, maximum: int, field: str) -> str:
    if not isinstance(value, str):
        raise GaleSubmissionPullError(f"{field}_invalid")
    text = value.strip()
    if len(text) > maximum:
        raise GaleSubmissionPullError(f"{field}_too_long")
    return text


def _validated_uuid(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as error:
        raise GaleSubmissionPullError("submission_uuid_invalid") from error


def _validated_local_receipt_id(value: object) -> str:
    if not isinstance(value, str) or not LOCAL_RECEIPT_ID_PATTERN.fullmatch(value):
        raise GaleSubmissionPullError("local_receipt_id_invalid")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="snotes-gale-r1b-pull")
    parser.add_argument(
        "command",
        choices=("pull", "status"),
        nargs="?",
        default="pull",
    )
    args = parser.parse_args(argv)
    try:
        data_root = require_isolated_data_root()
        transport = SystemSshSubmissionTransport()
        if args.command == "status":
            status = parse_status_payload(transport.status())
            print("GALE_SUBMISSION_PULL_STATUS=PASS")
            print(f"REMOTE_QUEUED_COUNT={status['queued']}")
            print(f"REMOTE_DELIVERED_COUNT={status['delivered']}")
            print(f"REMOTE_ACKNOWLEDGED_COUNT={status['acknowledged']}")
            return 0
        result = pull_submissions(transport, data_root=data_root)
        print("GALE_SUBMISSION_PULL_STATUS=PASS")
        print(f"EXPORTED_COUNT={result.exported_count}")
        print(f"IMPORTED_COUNT={result.imported_count}")
        print(f"ACKNOWLEDGED_COUNT={result.acknowledged_count}")
        print(f"DUPLICATE_COUNT={result.duplicate_count}")
        return 0
    except GaleSubmissionPullError as error:
        sys.stderr.write(
            "GALE_SUBMISSION_PULL_STATUS=FAIL\n"
            f"ERROR_CODE={error.code}\n"
        )
        return 2
    except Exception:
        sys.stderr.write(
            "GALE_SUBMISSION_PULL_STATUS=FAIL\n"
            "ERROR_CODE=unexpected_failure\n"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
