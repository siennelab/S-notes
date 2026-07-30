from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OUTBOX_SCHEMA_VERSION = 1
GALE_STAGING_ACTOR = "gale-staging"
DELIVERY_STATES = frozenset({"queued", "delivered", "acknowledged"})
MAX_TITLE_LENGTH = 200
MAX_CONTENT_LENGTH = 50_000
MAX_DIRECTORY_LENGTH = 200
MAX_TAG_LENGTH = 50
MAX_TAG_COUNT = 20
MAX_IDEMPOTENCY_KEY_LENGTH = 200
MAX_EXPORT_COUNT = 100


class GaleSubmissionOutboxError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class GaleSubmission:
    submission_uuid: str
    source_actor: str
    external_submission_id: str
    idempotency_key: str
    title: str
    content: str
    suggested_directory: str
    suggested_tags: tuple[str, ...]
    created_at: str
    delivery_state: str
    delivered_at: str | None
    acknowledged_at: str | None
    local_receipt_id: str | None

    def transport_payload(self) -> dict[str, Any]:
        return {
            "submission_uuid": self.submission_uuid,
            "source_actor": self.source_actor,
            "external_submission_id": self.external_submission_id,
            "idempotency_key": self.idempotency_key,
            "title": self.title,
            "content": self.content,
            "suggested_directory": self.suggested_directory,
            "suggested_tags": list(self.suggested_tags),
            "created_at": self.created_at,
            "delivery_state": self.delivery_state,
            "delivered_at": self.delivered_at,
        }

    def safe_payload(self, *, duplicate: bool = False) -> dict[str, Any]:
        return {
            "submission_uuid": self.submission_uuid,
            "external_submission_id": self.external_submission_id,
            "delivery_state": self.delivery_state,
            "duplicate": bool(duplicate),
            "created_at": self.created_at,
            "delivered_at": self.delivered_at,
            "acknowledged_at": self.acknowledged_at,
            "local_receipt_id": self.local_receipt_id,
        }


SUBMISSIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS submissions (
    submission_uuid TEXT PRIMARY KEY,
    source_actor TEXT NOT NULL
        CHECK (source_actor = 'gale-staging'),
    external_submission_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL
        CHECK (length(title) BETWEEN 1 AND 200),
    content TEXT NOT NULL
        CHECK (length(content) BETWEEN 1 AND 50000),
    suggested_directory TEXT NOT NULL DEFAULT ''
        CHECK (length(suggested_directory) <= 200),
    suggested_tags_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    delivery_state TEXT NOT NULL DEFAULT 'queued'
        CHECK (delivery_state IN ('queued', 'delivered', 'acknowledged')),
    delivered_at TEXT,
    acknowledged_at TEXT,
    local_receipt_id TEXT,
    CHECK (
        (delivery_state = 'queued'
            AND delivered_at IS NULL
            AND acknowledged_at IS NULL
            AND local_receipt_id IS NULL)
        OR
        (delivery_state = 'delivered'
            AND delivered_at IS NOT NULL
            AND acknowledged_at IS NULL
            AND local_receipt_id IS NULL)
        OR
        (delivery_state = 'acknowledged'
            AND delivered_at IS NOT NULL
            AND acknowledged_at IS NOT NULL
            AND local_receipt_id IS NOT NULL
            AND length(local_receipt_id) > 0)
    )
)
"""
DELIVERY_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_submissions_delivery
ON submissions(delivery_state, created_at, submission_uuid)
"""


def initialize_outbox(database_path: Path) -> None:
    path = _validated_database_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink():
        raise GaleSubmissionOutboxError("outbox_parent_symlink")

    connection = _connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if schema_version > OUTBOX_SCHEMA_VERSION:
            raise GaleSubmissionOutboxError("outbox_schema_too_new")
        connection.execute(SUBMISSIONS_TABLE_SQL)
        connection.execute(DELIVERY_INDEX_SQL)
        if schema_version < OUTBOX_SCHEMA_VERSION:
            connection.execute(f"PRAGMA user_version = {OUTBOX_SCHEMA_VERSION}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    os.chmod(path, 0o600)


def create_submission(
    database_path: Path,
    *,
    title: str,
    content: str,
    suggested_directory: str = "",
    suggested_tags: list[str] | tuple[str, ...] | None = None,
    idempotency_key: str | None = None,
) -> tuple[GaleSubmission, bool]:
    path = _validated_database_path(database_path)
    normalized_title = _required_text(title, "title", MAX_TITLE_LENGTH)
    normalized_content = _required_text(content, "content", MAX_CONTENT_LENGTH)
    normalized_directory = _optional_text(
        suggested_directory,
        "suggested_directory",
        MAX_DIRECTORY_LENGTH,
    )
    normalized_tags = _normalize_tags(suggested_tags or ())
    normalized_key = _optional_text(
        idempotency_key,
        "idempotency_key",
        MAX_IDEMPOTENCY_KEY_LENGTH,
    )

    submission_uuid = str(uuid.uuid4())
    stable_key = normalized_key or submission_uuid
    created_at = _utc_now()
    connection = _connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            """
            SELECT *
            FROM submissions
            WHERE idempotency_key = ?
            """,
            (stable_key,),
        ).fetchone()
        if existing is not None:
            connection.commit()
            return _submission_from_row(existing), True
        try:
            connection.execute(
                """
                INSERT INTO submissions (
                    submission_uuid, source_actor, external_submission_id,
                    idempotency_key, title, content, suggested_directory,
                    suggested_tags_json, created_at, delivery_state
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued')
                """,
                (
                    submission_uuid,
                    GALE_STAGING_ACTOR,
                    submission_uuid,
                    stable_key,
                    normalized_title,
                    normalized_content,
                    normalized_directory,
                    json.dumps(
                        normalized_tags,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    created_at,
                ),
            )
        except sqlite3.IntegrityError:
            existing = connection.execute(
                """
                SELECT *
                FROM submissions
                WHERE idempotency_key = ?
                """,
                (stable_key,),
            ).fetchone()
            if existing is None:
                raise
            connection.commit()
            return _submission_from_row(existing), True
        row = connection.execute(
            "SELECT * FROM submissions WHERE submission_uuid = ?",
            (submission_uuid,),
        ).fetchone()
        connection.commit()
    except sqlite3.OperationalError as error:
        connection.rollback()
        if "locked" in str(error).lower():
            raise GaleSubmissionOutboxError("outbox_busy") from error
        raise GaleSubmissionOutboxError("outbox_write_failed") from error
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    if row is None:
        raise GaleSubmissionOutboxError("submission_create_failed")
    return _submission_from_row(row), False


def export_unacknowledged(
    database_path: Path,
    *,
    limit: int = MAX_EXPORT_COUNT,
) -> list[GaleSubmission]:
    path = _validated_database_path(database_path)
    if not 1 <= int(limit) <= MAX_EXPORT_COUNT:
        raise GaleSubmissionOutboxError("invalid_export_limit")
    delivered_at = _utc_now()
    connection = _connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            """
            SELECT *
            FROM submissions
            WHERE delivery_state IN ('queued', 'delivered')
            ORDER BY created_at, submission_uuid
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        queued_ids = [
            str(row["submission_uuid"])
            for row in rows
            if row["delivery_state"] == "queued"
        ]
        if queued_ids:
            placeholders = ",".join("?" for _item in queued_ids)
            connection.execute(
                f"""
                UPDATE submissions
                SET delivery_state = 'delivered',
                    delivered_at = COALESCE(delivered_at, ?)
                WHERE submission_uuid IN ({placeholders})
                  AND delivery_state = 'queued'
                """,
                (delivered_at, *queued_ids),
            )
        refreshed = connection.execute(
            """
            SELECT *
            FROM submissions
            WHERE delivery_state = 'delivered'
              AND submission_uuid IN (
                  SELECT submission_uuid
                  FROM submissions
                  WHERE delivery_state = 'delivered'
                  ORDER BY created_at, submission_uuid
                  LIMIT ?
              )
            ORDER BY created_at, submission_uuid
            """,
            (int(limit),),
        ).fetchall()
        connection.commit()
    except sqlite3.OperationalError as error:
        connection.rollback()
        if "locked" in str(error).lower():
            raise GaleSubmissionOutboxError("outbox_busy") from error
        raise GaleSubmissionOutboxError("outbox_export_failed") from error
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return [_submission_from_row(row) for row in refreshed]


def acknowledge_submission(
    database_path: Path,
    *,
    submission_uuid: str,
    local_receipt_id: str,
) -> tuple[GaleSubmission, bool]:
    path = _validated_database_path(database_path)
    normalized_uuid = _validated_uuid(submission_uuid)
    receipt_id = _required_text(local_receipt_id, "local_receipt_id", 200)
    acknowledged_at = _utc_now()
    connection = _connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM submissions WHERE submission_uuid = ?",
            (normalized_uuid,),
        ).fetchone()
        if row is None:
            raise GaleSubmissionOutboxError("submission_not_found")
        if row["delivery_state"] == "acknowledged":
            if str(row["local_receipt_id"]) != receipt_id:
                raise GaleSubmissionOutboxError("ack_receipt_conflict")
            connection.commit()
            return _submission_from_row(row), True
        if row["delivery_state"] != "delivered":
            raise GaleSubmissionOutboxError("submission_not_delivered")
        connection.execute(
            """
            UPDATE submissions
            SET delivery_state = 'acknowledged',
                acknowledged_at = ?,
                local_receipt_id = ?
            WHERE submission_uuid = ?
              AND delivery_state = 'delivered'
            """,
            (acknowledged_at, receipt_id, normalized_uuid),
        )
        updated = connection.execute(
            "SELECT * FROM submissions WHERE submission_uuid = ?",
            (normalized_uuid,),
        ).fetchone()
        connection.commit()
    except sqlite3.OperationalError as error:
        connection.rollback()
        if "locked" in str(error).lower():
            raise GaleSubmissionOutboxError("outbox_busy") from error
        raise GaleSubmissionOutboxError("outbox_ack_failed") from error
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    if updated is None:
        raise GaleSubmissionOutboxError("ack_failed")
    return _submission_from_row(updated), False


def get_submission(
    database_path: Path,
    submission_uuid: str,
) -> GaleSubmission | None:
    path = _validated_database_path(database_path)
    normalized_uuid = _validated_uuid(submission_uuid)
    connection = _connect(path)
    try:
        row = connection.execute(
            "SELECT * FROM submissions WHERE submission_uuid = ?",
            (normalized_uuid,),
        ).fetchone()
    finally:
        connection.close()
    return _submission_from_row(row) if row is not None else None


def outbox_status(database_path: Path) -> dict[str, int]:
    path = _validated_database_path(database_path)
    connection = _connect(path)
    try:
        counts = {"queued": 0, "delivered": 0, "acknowledged": 0}
        for row in connection.execute(
            """
            SELECT delivery_state, COUNT(*) AS item_count
            FROM submissions
            GROUP BY delivery_state
            """
        ).fetchall():
            counts[str(row["delivery_state"])] = int(row["item_count"])
    finally:
        connection.close()
    return counts


def _connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path, timeout=0.25)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA trusted_schema = OFF")
    connection.execute("PRAGMA busy_timeout = 250")
    return connection


def _validated_database_path(database_path: Path) -> Path:
    path = Path(database_path)
    if not path.is_absolute():
        raise GaleSubmissionOutboxError("outbox_path_not_absolute")
    if path.name != "outbox.db":
        raise GaleSubmissionOutboxError("unexpected_outbox_name")
    if path.is_symlink():
        raise GaleSubmissionOutboxError("outbox_symlink")
    if path.exists():
        file_stat = path.lstat()
        if not stat.S_ISREG(file_stat.st_mode):
            raise GaleSubmissionOutboxError("outbox_not_regular")
    return path


def _required_text(value: object, field: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise GaleSubmissionOutboxError(f"{field}_required")
    if len(text) > maximum:
        raise GaleSubmissionOutboxError(f"{field}_too_long")
    return text


def _optional_text(value: object, field: str, maximum: int) -> str:
    text = str(value or "").strip()
    if len(text) > maximum:
        raise GaleSubmissionOutboxError(f"{field}_too_long")
    return text


def _normalize_tags(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise GaleSubmissionOutboxError("tags_invalid")
    if len(values) > MAX_TAG_COUNT:
        raise GaleSubmissionOutboxError("too_many_tags")
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise GaleSubmissionOutboxError("tag_invalid")
        tag = value.strip()
        if not tag:
            raise GaleSubmissionOutboxError("tag_empty")
        if len(tag) > MAX_TAG_LENGTH:
            raise GaleSubmissionOutboxError("tag_too_long")
        if tag not in seen:
            seen.add(tag)
            normalized.append(tag)
    return tuple(normalized)


def _validated_uuid(value: object) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise GaleSubmissionOutboxError("invalid_submission_uuid") from error
    return str(parsed)


def _submission_from_row(row: sqlite3.Row) -> GaleSubmission:
    try:
        tags = json.loads(str(row["suggested_tags_json"]))
    except json.JSONDecodeError as error:
        raise GaleSubmissionOutboxError("stored_tags_invalid") from error
    normalized_tags = _normalize_tags(tags)
    state = str(row["delivery_state"])
    if state not in DELIVERY_STATES:
        raise GaleSubmissionOutboxError("stored_state_invalid")
    return GaleSubmission(
        submission_uuid=_validated_uuid(row["submission_uuid"]),
        source_actor=str(row["source_actor"]),
        external_submission_id=str(row["external_submission_id"]),
        idempotency_key=str(row["idempotency_key"]),
        title=str(row["title"]),
        content=str(row["content"]),
        suggested_directory=str(row["suggested_directory"]),
        suggested_tags=normalized_tags,
        created_at=str(row["created_at"]),
        delivery_state=state,
        delivered_at=(
            str(row["delivered_at"]) if row["delivered_at"] is not None else None
        ),
        acknowledged_at=(
            str(row["acknowledged_at"])
            if row["acknowledged_at"] is not None
            else None
        ),
        local_receipt_id=(
            str(row["local_receipt_id"])
            if row["local_receipt_id"] is not None
            else None
        ),
    )


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(prog="snotes-gale-r1b-outbox")
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(
            os.environ.get(
                "SNOTES_GALE_R1B_OUTBOX_PATH",
                "/var/lib/snotes-gale-r1b-staging/outbox.db",
            )
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--limit", type=int, default=MAX_EXPORT_COUNT)
    ack_parser = subparsers.add_parser("ack")
    ack_parser.add_argument("submission_uuid")
    ack_parser.add_argument("local_receipt_id")
    subparsers.add_parser("status")
    args = parser.parse_args(argv)
    try:
        initialize_outbox(args.database)
        if args.command == "export":
            submissions = export_unacknowledged(
                args.database,
                limit=args.limit,
            )
            print(
                json.dumps(
                    {
                        "schema_version": OUTBOX_SCHEMA_VERSION,
                        "submissions": [
                            submission.transport_payload()
                            for submission in submissions
                        ],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        elif args.command == "ack":
            submission, duplicate = acknowledge_submission(
                args.database,
                submission_uuid=args.submission_uuid,
                local_receipt_id=args.local_receipt_id,
            )
            print(
                json.dumps(
                    submission.safe_payload(duplicate=duplicate),
                    ensure_ascii=True,
                    separators=(",", ":"),
                )
            )
        else:
            print(
                json.dumps(
                    outbox_status(args.database),
                    ensure_ascii=True,
                    separators=(",", ":"),
                )
            )
        return 0
    except GaleSubmissionOutboxError as error:
        sys.stderr.write(f"OUTBOX_STATUS=FAIL\nERROR_CODE={error.code}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
