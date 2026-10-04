from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import stat
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OUTBOX_SCHEMA_VERSION = 2
GENERALIZED_OUTBOX_SCHEMA_VERSION = 3
GALE_EXPORT_SCHEMA_VERSION = OUTBOX_SCHEMA_VERSION
GALE_STAGING_ACTOR = "gale-staging"
GALE_PRODUCTION_ACTOR = "gale"
CLAUDE_CODE_ACTOR = "claude-code"
CLAWBOT_ACTOR = "clawbot"
SOURCE_ACTORS = frozenset(
    {
        GALE_STAGING_ACTOR,
        GALE_PRODUCTION_ACTOR,
        CLAUDE_CODE_ACTOR,
        CLAWBOT_ACTOR,
    }
)
GALE_SOURCE_ACTORS = frozenset({GALE_STAGING_ACTOR, GALE_PRODUCTION_ACTOR})
TERMINAL_STATES = frozenset({"accepted", "rejected", "conflict"})
DELIVERY_STATES = frozenset(
    {"queued", "delivered", "acknowledged", *TERMINAL_STATES}
)
MAX_TITLE_LENGTH = 200
MAX_CONTENT_LENGTH = 50_000
MAX_DIRECTORY_LENGTH = 200
MAX_TAG_LENGTH = 50
MAX_TAG_COUNT = 20
MAX_AI_TARGET_COUNT = 20
MAX_IDEMPOTENCY_KEY_LENGTH = 200
MAX_REQUEST_ID_LENGTH = 128
MAX_OPERATION_TYPE_LENGTH = 64
MAX_NOTE_ID_LENGTH = 200
MAX_EXPECTED_VERSION_LENGTH = 200
MAX_OPERATION_PAYLOAD_BYTES = 512 * 1024
MAX_RECEIPT_PAYLOAD_BYTES = 50_000
DEFAULT_NON_TERMINAL_LIMIT = 50
MAX_EXPORT_COUNT = 100
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
REQUEST_ID_PATTERN = re.compile(r"^\S+$")
OPERATION_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


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
    ai_visibility: str
    ai_targets: tuple[str, ...]
    main_category: str
    sub_category: str
    tags: tuple[str, ...]
    created_at: str
    delivery_state: str
    delivered_at: str | None
    acknowledged_at: str | None
    local_receipt_id: str | None
    operation_type: str = "create"
    note_id: str | None = None
    expected_version: str | None = None
    operation_payload: dict[str, Any] | None = None
    terminal_at: str | None = None
    receipt_payload: dict[str, Any] | None = None

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
            "ai_visibility": self.ai_visibility,
            "ai_targets": list(self.ai_targets),
            "main_category": self.main_category,
            "sub_category": self.sub_category,
            "tags": list(self.tags),
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

    def operation_transport_payload(self) -> dict[str, Any]:
        """Return the generalized proposal contract used by Cloud writeback."""
        return {
            "submission_uuid": self.submission_uuid,
            "source_actor": self.source_actor,
            "request_id": self.idempotency_key,
            "operation_type": self.operation_type,
            "note_id": self.note_id,
            "expected_version": self.expected_version,
            "payload": dict(self.operation_payload or {}),
            "created_at": self.created_at,
            "delivery_state": self.delivery_state,
            "delivered_at": self.delivered_at,
        }

    def operation_status_payload(self, *, duplicate: bool = False) -> dict[str, Any]:
        """Return status metadata without exposing an operation's private payload."""
        return {
            "submission_uuid": self.submission_uuid,
            "request_id": self.idempotency_key,
            "operation_type": self.operation_type,
            "note_id": self.note_id,
            "delivery_state": self.delivery_state,
            "duplicate": bool(duplicate),
            "created_at": self.created_at,
            "delivered_at": self.delivered_at,
            "acknowledged_at": self.acknowledged_at,
            "terminal_at": self.terminal_at,
            "receipt": dict(self.receipt_payload or {}),
        }


LEGACY_SUBMISSIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS submissions (
    submission_uuid TEXT PRIMARY KEY,
    source_actor TEXT NOT NULL
        CHECK (source_actor IN ('gale-staging', 'gale')),
    external_submission_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL
        CHECK (length(title) BETWEEN 1 AND 200),
    content TEXT NOT NULL
        CHECK (length(content) BETWEEN 1 AND 50000),
    suggested_directory TEXT NOT NULL DEFAULT ''
        CHECK (length(suggested_directory) <= 200),
    suggested_tags_json TEXT NOT NULL DEFAULT '[]',
    proposal_metadata_json TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(proposal_metadata_json)),
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

GENERALIZED_SUBMISSIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS submissions (
    submission_uuid TEXT PRIMARY KEY,
    source_actor TEXT NOT NULL,
    external_submission_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL,
    operation_type TEXT NOT NULL,
    note_id TEXT,
    expected_version TEXT,
    operation_payload_json TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(operation_payload_json)),
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    suggested_directory TEXT NOT NULL DEFAULT ''
        CHECK (length(suggested_directory) <= 200),
    suggested_tags_json TEXT NOT NULL DEFAULT '[]',
    proposal_metadata_json TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(proposal_metadata_json)),
    created_at TEXT NOT NULL,
    delivery_state TEXT NOT NULL DEFAULT 'queued'
        CHECK (delivery_state IN (
            'queued', 'delivered', 'acknowledged',
            'accepted', 'rejected', 'conflict'
        )),
    delivered_at TEXT,
    acknowledged_at TEXT,
    local_receipt_id TEXT,
    terminal_at TEXT,
    receipt_payload_json TEXT,
    UNIQUE (source_actor, idempotency_key),
    CHECK (
        (delivery_state = 'queued'
            AND delivered_at IS NULL
            AND acknowledged_at IS NULL
            AND local_receipt_id IS NULL
            AND terminal_at IS NULL
            AND receipt_payload_json IS NULL)
        OR
        (delivery_state = 'delivered'
            AND delivered_at IS NOT NULL
            AND acknowledged_at IS NULL
            AND local_receipt_id IS NULL
            AND terminal_at IS NULL
            AND receipt_payload_json IS NULL)
        OR
        (delivery_state = 'acknowledged'
            AND delivered_at IS NOT NULL
            AND acknowledged_at IS NOT NULL
            AND local_receipt_id IS NOT NULL
            AND length(local_receipt_id) > 0
            AND terminal_at IS NULL
            AND receipt_payload_json IS NULL)
        OR
        (delivery_state IN ('accepted', 'rejected', 'conflict')
            AND delivered_at IS NOT NULL
            AND acknowledged_at IS NOT NULL
            AND local_receipt_id IS NOT NULL
            AND length(local_receipt_id) > 0
            AND terminal_at IS NOT NULL
            AND receipt_payload_json IS NOT NULL
            AND json_valid(receipt_payload_json))
    )
)
"""
DELIVERY_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_submissions_delivery
ON submissions(delivery_state, created_at, submission_uuid)
"""
ACTOR_STATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_submissions_actor_state
ON submissions(source_actor, delivery_state, created_at, submission_uuid)
"""


def initialize_outbox(
    database_path: Path,
    *,
    generalized: bool = False,
) -> None:
    """Initialize an outbox.

    The default remains the frozen Oracle Gale schema.  Generalized proposal
    storage is deliberately opt-in so deploying this module cannot silently
    migrate either Oracle database.
    """
    path = _validated_database_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink():
        raise GaleSubmissionOutboxError("outbox_parent_symlink")

    connection = _connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        maximum_version = GENERALIZED_OUTBOX_SCHEMA_VERSION
        if schema_version > maximum_version:
            raise GaleSubmissionOutboxError("outbox_schema_too_new")
        if generalized:
            if not _submissions_table_exists(connection):
                connection.execute(GENERALIZED_SUBMISSIONS_TABLE_SQL)
            else:
                if not _is_generalized_schema(connection):
                    _migrate_legacy_metadata(connection)
                    _rebuild_generalized_schema(connection)
            connection.execute(ACTOR_STATE_INDEX_SQL)
            target_version = GENERALIZED_OUTBOX_SCHEMA_VERSION
        else:
            if schema_version == GENERALIZED_OUTBOX_SCHEMA_VERSION:
                # Generalized outboxes remain usable by legacy Gale helpers,
                # but default initialization never rewrites them.
                target_version = GENERALIZED_OUTBOX_SCHEMA_VERSION
            else:
                connection.execute(LEGACY_SUBMISSIONS_TABLE_SQL)
                _migrate_legacy_metadata(connection)
                target_version = OUTBOX_SCHEMA_VERSION
        connection.execute(DELIVERY_INDEX_SQL)
        if schema_version < target_version:
            connection.execute(f"PRAGMA user_version = {target_version}")
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
    ai_visibility: str | None = None,
    ai_targets: list[str] | tuple[str, ...] | None = None,
    main_category: str | None = None,
    sub_category: str | None = None,
    tags: list[str] | tuple[str, ...] | None = None,
    idempotency_key: str | None = None,
    source_actor: str = GALE_STAGING_ACTOR,
) -> tuple[GaleSubmission, bool]:
    path = _validated_database_path(database_path)
    normalized_title = _required_text(title, "title", MAX_TITLE_LENGTH)
    normalized_content = _required_text(content, "content", MAX_CONTENT_LENGTH)
    normalized_directory = _normalize_directory(suggested_directory)
    normalized_tags = _normalize_tags(suggested_tags or ())
    proposal_metadata = _normalize_proposal_metadata(
        ai_visibility=ai_visibility,
        ai_targets=ai_targets,
        main_category=main_category,
        sub_category=sub_category,
        tags=tags,
    )
    normalized_actor = _validated_gale_source_actor(source_actor)
    normalized_key = _optional_text(
        idempotency_key,
        "idempotency_key",
        MAX_IDEMPOTENCY_KEY_LENGTH,
    )
    if normalized_key and not IDEMPOTENCY_KEY_PATTERN.fullmatch(normalized_key):
        raise GaleSubmissionOutboxError("idempotency_key_invalid")

    submission_uuid = str(uuid.uuid4())
    stable_key = normalized_key or submission_uuid
    created_at = _utc_now()
    connection = _connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        generalized = _is_generalized_schema(connection)
        idempotency_where = (
            "source_actor = ? AND idempotency_key = ?"
            if generalized
            else "idempotency_key = ?"
        )
        idempotency_parameters: tuple[object, ...] = (
            (normalized_actor, stable_key)
            if generalized
            else (stable_key,)
        )
        existing = connection.execute(
            f"""
            SELECT *
            FROM submissions
            WHERE {idempotency_where}
            """,
            idempotency_parameters,
        ).fetchone()
        if existing is not None:
            if str(existing["source_actor"]) != normalized_actor:
                raise GaleSubmissionOutboxError("idempotency_actor_conflict")
            connection.commit()
            return _submission_from_row(existing), True
        try:
            columns = """
                submission_uuid, source_actor, external_submission_id,
                idempotency_key, title, content, suggested_directory,
                suggested_tags_json, proposal_metadata_json, created_at,
                delivery_state
            """
            placeholders = "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued'"
            values: tuple[object, ...] = (
                submission_uuid,
                normalized_actor,
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
                json.dumps(
                    proposal_metadata,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                created_at,
            )
            if generalized:
                columns = f"operation_type, operation_payload_json, {columns}"
                placeholders = f"?, ?, {placeholders}"
                values = (
                    "create",
                    json.dumps(
                        {
                            "title": normalized_title,
                            "content": normalized_content,
                            "suggested_directory": normalized_directory,
                            "suggested_tags": list(normalized_tags),
                            **proposal_metadata,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    *values,
                )
            connection.execute(
                f"INSERT INTO submissions ({columns}) VALUES ({placeholders})",
                values,
            )
        except sqlite3.IntegrityError:
            existing = connection.execute(
                f"""
                SELECT *
                FROM submissions
                WHERE {idempotency_where}
                """,
                idempotency_parameters,
            ).fetchone()
            if existing is None:
                raise
            if str(existing["source_actor"]) != normalized_actor:
                raise GaleSubmissionOutboxError("idempotency_actor_conflict")
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


def enqueue_proposal(
    database_path: Path,
    *,
    source_actor: str,
    request_id: str,
    operation_type: str,
    payload: dict[str, Any],
    note_id: str | int | None = None,
    expected_version: str | None = None,
    queue_limit: int = DEFAULT_NON_TERMINAL_LIMIT,
) -> tuple[GaleSubmission, bool]:
    """Atomically deduplicate and enqueue a generalized proposal.

    Idempotency is actor-scoped and is checked before the actor's queue cap.
    Operation allowlists and operation-specific prechecks belong to the caller;
    deliberately keeping the schema open avoids another table rebuild when new
    proposal operations are added.
    """
    path = _validated_database_path(database_path)
    normalized_actor = _validated_source_actor(source_actor)
    normalized_request = _validated_request_id(request_id)
    normalized_operation = _required_text(
        operation_type,
        "operation_type",
        MAX_OPERATION_TYPE_LENGTH,
    )
    if not OPERATION_TYPE_PATTERN.fullmatch(normalized_operation):
        raise GaleSubmissionOutboxError("operation_type_invalid")
    normalized_note_id = _normalize_note_id(note_id)
    normalized_version = _optional_nullable_text(
        expected_version,
        "expected_version",
        MAX_EXPECTED_VERSION_LENGTH,
    )
    payload_json, normalized_payload = _normalize_json_object(
        payload,
        field="payload",
        maximum_bytes=MAX_OPERATION_PAYLOAD_BYTES,
    )
    if isinstance(queue_limit, bool) or not isinstance(queue_limit, int):
        raise GaleSubmissionOutboxError("queue_limit_invalid")
    if not 1 <= queue_limit <= 1_000_000:
        raise GaleSubmissionOutboxError("queue_limit_invalid")

    submission_uuid = str(uuid.uuid4())
    created_at = _utc_now()
    connection = _connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        if not _is_generalized_schema(connection):
            raise GaleSubmissionOutboxError("proposal_schema_required")
        existing = connection.execute(
            """
            SELECT * FROM submissions
            WHERE source_actor = ? AND idempotency_key = ?
            """,
            (normalized_actor, normalized_request),
        ).fetchone()
        if existing is not None:
            connection.commit()
            return _submission_from_row(existing), True
        non_terminal_count = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM submissions
                WHERE source_actor = ?
                  AND delivery_state NOT IN ('accepted', 'rejected', 'conflict')
                """,
                (normalized_actor,),
            ).fetchone()[0]
        )
        if non_terminal_count >= queue_limit:
            raise GaleSubmissionOutboxError("queue_full")
        try:
            connection.execute(
                """
                INSERT INTO submissions (
                    submission_uuid, source_actor, external_submission_id,
                    idempotency_key, operation_type, note_id,
                    expected_version, operation_payload_json, created_at,
                    delivery_state
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued')
                """,
                (
                    submission_uuid,
                    normalized_actor,
                    submission_uuid,
                    normalized_request,
                    normalized_operation,
                    normalized_note_id,
                    normalized_version,
                    payload_json,
                    created_at,
                ),
            )
        except sqlite3.IntegrityError:
            existing = connection.execute(
                """
                SELECT * FROM submissions
                WHERE source_actor = ? AND idempotency_key = ?
                """,
                (normalized_actor, normalized_request),
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
    submission = _submission_from_row(row)
    if submission.operation_payload != normalized_payload:
        raise GaleSubmissionOutboxError("stored_payload_invalid")
    return submission, False


def get_operation_status(
    database_path: Path,
    *,
    source_actor: str,
    request_id: str,
) -> GaleSubmission | None:
    """Look up a request only within the authenticated actor's namespace."""
    path = _validated_database_path(database_path)
    normalized_actor = _validated_source_actor(source_actor)
    normalized_request = _validated_request_id(request_id)
    connection = _connect(path)
    try:
        if not _is_generalized_schema(connection):
            raise GaleSubmissionOutboxError("proposal_schema_required")
        row = connection.execute(
            """
            SELECT * FROM submissions
            WHERE source_actor = ? AND idempotency_key = ?
            """,
            (normalized_actor, normalized_request),
        ).fetchone()
    finally:
        connection.close()
    return _submission_from_row(row) if row is not None else None


def record_terminal_receipt(
    database_path: Path,
    *,
    source_actor: str,
    request_id: str,
    terminal_state: str,
    receipt_payload: dict[str, Any] | None = None,
) -> tuple[GaleSubmission, bool]:
    """Record an idempotent Mac decision after import acknowledgement."""
    path = _validated_database_path(database_path)
    normalized_actor = _validated_source_actor(source_actor)
    normalized_request = _validated_request_id(request_id)
    if terminal_state not in TERMINAL_STATES:
        raise GaleSubmissionOutboxError("terminal_state_invalid")
    receipt_json, _normalized_receipt = _normalize_json_object(
        {} if receipt_payload is None else receipt_payload,
        field="receipt_payload",
        maximum_bytes=MAX_RECEIPT_PAYLOAD_BYTES,
    )
    terminal_at = _utc_now()
    connection = _connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        if not _is_generalized_schema(connection):
            raise GaleSubmissionOutboxError("proposal_schema_required")
        row = connection.execute(
            """
            SELECT * FROM submissions
            WHERE source_actor = ? AND idempotency_key = ?
            """,
            (normalized_actor, normalized_request),
        ).fetchone()
        if row is None:
            raise GaleSubmissionOutboxError("submission_not_found")
        if str(row["delivery_state"]) in TERMINAL_STATES:
            if (
                str(row["delivery_state"]) != terminal_state
                or str(row["receipt_payload_json"]) != receipt_json
            ):
                raise GaleSubmissionOutboxError("terminal_receipt_conflict")
            connection.commit()
            return _submission_from_row(row), True
        if row["delivery_state"] != "acknowledged":
            raise GaleSubmissionOutboxError("submission_not_acknowledged")
        connection.execute(
            """
            UPDATE submissions
            SET delivery_state = ?, terminal_at = ?, receipt_payload_json = ?
            WHERE submission_uuid = ? AND delivery_state = 'acknowledged'
            """,
            (
                terminal_state,
                terminal_at,
                receipt_json,
                str(row["submission_uuid"]),
            ),
        )
        updated = connection.execute(
            "SELECT * FROM submissions WHERE submission_uuid = ?",
            (str(row["submission_uuid"]),),
        ).fetchone()
        connection.commit()
    except sqlite3.OperationalError as error:
        connection.rollback()
        if "locked" in str(error).lower():
            raise GaleSubmissionOutboxError("outbox_busy") from error
        raise GaleSubmissionOutboxError("outbox_receipt_failed") from error
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    if updated is None:
        raise GaleSubmissionOutboxError("terminal_receipt_failed")
    return _submission_from_row(updated), False


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
        if row["delivery_state"] == "acknowledged" or str(
            row["delivery_state"]
        ) in TERMINAL_STATES:
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
    if not path.is_file():
        raise GaleSubmissionOutboxError("outbox_missing")
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


def _migrate_legacy_metadata(connection: sqlite3.Connection) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(submissions)").fetchall()
    }
    if "proposal_metadata_json" not in columns:
        connection.execute(
            """
            ALTER TABLE submissions
            ADD COLUMN proposal_metadata_json TEXT NOT NULL DEFAULT '{}'
                CHECK (json_valid(proposal_metadata_json))
            """
        )


def _is_generalized_schema(connection: sqlite3.Connection) -> bool:
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(submissions)").fetchall()
    }
    return {
        "operation_type",
        "note_id",
        "expected_version",
        "operation_payload_json",
        "terminal_at",
        "receipt_payload_json",
    }.issubset(columns)


def _submissions_table_exists(connection: sqlite3.Connection) -> bool:
    return connection.execute(
        """
        SELECT 1 FROM sqlite_schema
        WHERE type = 'table' AND name = 'submissions'
        """
    ).fetchone() is not None


def _rebuild_generalized_schema(connection: sqlite3.Connection) -> None:
    """Perform the single v2 -> v3 rebuild, only after explicit opt-in."""
    connection.execute("ALTER TABLE submissions RENAME TO submissions_legacy")
    connection.execute(GENERALIZED_SUBMISSIONS_TABLE_SQL)
    connection.execute(
        """
        INSERT INTO submissions (
            submission_uuid, source_actor, external_submission_id,
            idempotency_key, operation_type, note_id, expected_version,
            operation_payload_json, title, content, suggested_directory,
            suggested_tags_json, proposal_metadata_json, created_at,
            delivery_state, delivered_at, acknowledged_at, local_receipt_id,
            terminal_at, receipt_payload_json
        )
        SELECT
            submission_uuid, source_actor, external_submission_id,
            idempotency_key, 'create', NULL, NULL,
            json_patch(json_object(
                'title', title,
                'content', content,
                'suggested_directory', suggested_directory,
                'suggested_tags', json(suggested_tags_json)
            ), proposal_metadata_json),
            title, content, suggested_directory, suggested_tags_json,
            proposal_metadata_json, created_at, delivery_state, delivered_at,
            acknowledged_at, local_receipt_id, NULL, NULL
        FROM submissions_legacy
        """
    )
    connection.execute("DROP TABLE submissions_legacy")


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
    if not isinstance(value, str):
        raise GaleSubmissionOutboxError(f"{field}_invalid")
    text = value.strip()
    if not text:
        raise GaleSubmissionOutboxError(f"{field}_required")
    if len(text) > maximum:
        raise GaleSubmissionOutboxError(f"{field}_too_long")
    return text


def _optional_text(value: object, field: str, maximum: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise GaleSubmissionOutboxError(f"{field}_invalid")
    text = value.strip()
    if len(text) > maximum:
        raise GaleSubmissionOutboxError(f"{field}_too_long")
    return text


def _optional_nullable_text(
    value: object,
    field: str,
    maximum: int,
) -> str | None:
    text = _optional_text(value, field, maximum)
    return text or None


def _normalize_note_id(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise GaleSubmissionOutboxError("note_id_invalid")
    text = str(value).strip()
    if not text:
        raise GaleSubmissionOutboxError("note_id_invalid")
    if len(text) > MAX_NOTE_ID_LENGTH:
        raise GaleSubmissionOutboxError("note_id_too_long")
    if any(ord(character) < 32 for character in text):
        raise GaleSubmissionOutboxError("note_id_invalid")
    return text


def _normalize_json_object(
    value: object,
    *,
    field: str,
    maximum_bytes: int,
) -> tuple[str, dict[str, Any]]:
    if not isinstance(value, dict):
        raise GaleSubmissionOutboxError(f"{field}_invalid")
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise GaleSubmissionOutboxError(f"{field}_invalid") from error
    if len(encoded.encode("utf-8")) > maximum_bytes:
        raise GaleSubmissionOutboxError(f"{field}_too_large")
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise GaleSubmissionOutboxError(f"{field}_invalid")
    return encoded, decoded


def _normalize_directory(value: object) -> str:
    text = _optional_text(
        value,
        "suggested_directory",
        MAX_DIRECTORY_LENGTH,
    )
    if not text:
        return ""
    if any(ord(character) < 32 for character in text):
        raise GaleSubmissionOutboxError("suggested_directory_invalid")
    parts = text.split("/")
    if len(parts) > 3 or any(not part.strip() for part in parts):
        raise GaleSubmissionOutboxError("suggested_directory_invalid")
    return "/".join(part.strip() for part in parts)


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


def _normalize_proposal_metadata(
    *,
    ai_visibility: str | None,
    ai_targets: list[str] | tuple[str, ...] | None,
    main_category: str | None,
    sub_category: str | None,
    tags: list[str] | tuple[str, ...] | None,
) -> dict[str, object]:
    metadata: dict[str, object] = {}
    visibility = _optional_text(ai_visibility, "ai_visibility", 20)
    if visibility:
        metadata["ai_visibility"] = visibility
    targets = _normalize_target_ids(ai_targets or ())
    if targets:
        metadata["ai_targets"] = list(targets)
    main = _optional_text(main_category, "main_category", MAX_DIRECTORY_LENGTH)
    if main:
        if any(ord(character) < 32 for character in main):
            raise GaleSubmissionOutboxError("main_category_invalid")
        metadata["main_category"] = main
    sub = _optional_text(sub_category, "sub_category", MAX_DIRECTORY_LENGTH)
    if sub:
        if any(ord(character) < 32 for character in sub):
            raise GaleSubmissionOutboxError("sub_category_invalid")
        metadata["sub_category"] = sub
    normalized_tags = _normalize_tags(tags or ())
    if normalized_tags:
        metadata["tags"] = list(normalized_tags)
    return metadata


def _normalize_target_ids(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise GaleSubmissionOutboxError("ai_targets_invalid")
    if len(values) > MAX_AI_TARGET_COUNT:
        raise GaleSubmissionOutboxError("too_many_ai_targets")
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise GaleSubmissionOutboxError("ai_target_invalid")
        target_id = value.strip()
        if not target_id:
            raise GaleSubmissionOutboxError("ai_target_empty")
        if len(target_id) > MAX_TAG_LENGTH:
            raise GaleSubmissionOutboxError("ai_target_too_long")
        if target_id not in seen:
            seen.add(target_id)
            normalized.append(target_id)
    return tuple(normalized)


def _stored_proposal_metadata(row: sqlite3.Row) -> dict[str, object]:
    try:
        raw = row["proposal_metadata_json"]
    except (KeyError, IndexError):
        return {}
    try:
        value = json.loads(str(raw or "{}"))
    except json.JSONDecodeError as error:
        raise GaleSubmissionOutboxError("stored_metadata_invalid") from error
    if not isinstance(value, dict):
        raise GaleSubmissionOutboxError("stored_metadata_invalid")
    return value


def _validated_uuid(value: object) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise GaleSubmissionOutboxError("invalid_submission_uuid") from error
    return str(parsed)


def _validated_request_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_REQUEST_ID_LENGTH
        or REQUEST_ID_PATTERN.fullmatch(value) is None
    ):
        raise GaleSubmissionOutboxError("request_id_invalid")
    return value


def _validated_source_actor(value: object) -> str:
    if not isinstance(value, str) or value not in SOURCE_ACTORS:
        raise GaleSubmissionOutboxError("source_actor_invalid")
    return value


def _validated_gale_source_actor(value: object) -> str:
    if not isinstance(value, str) or value not in GALE_SOURCE_ACTORS:
        raise GaleSubmissionOutboxError("source_actor_invalid")
    return value


def _submission_from_row(row: sqlite3.Row) -> GaleSubmission:
    try:
        tags = json.loads(str(row["suggested_tags_json"]))
    except json.JSONDecodeError as error:
        raise GaleSubmissionOutboxError("stored_tags_invalid") from error
    normalized_tags = _normalize_tags(tags)
    metadata = _stored_proposal_metadata(row)
    proposal_visibility = str(metadata.get("ai_visibility") or "")
    proposal_targets = _normalize_target_ids(metadata.get("ai_targets") or ())
    proposal_main = str(metadata.get("main_category") or "")
    proposal_sub = str(metadata.get("sub_category") or "")
    proposal_tags = _normalize_tags(metadata.get("tags") or ())
    state = str(row["delivery_state"])
    if state not in DELIVERY_STATES:
        raise GaleSubmissionOutboxError("stored_state_invalid")
    row_keys = set(row.keys())
    operation_type = (
        str(row["operation_type"])
        if "operation_type" in row_keys
        else "create"
    )
    if not OPERATION_TYPE_PATTERN.fullmatch(operation_type):
        raise GaleSubmissionOutboxError("stored_operation_type_invalid")
    operation_payload: dict[str, Any] = {}
    if "operation_payload_json" in row_keys:
        try:
            stored_payload = json.loads(str(row["operation_payload_json"] or "{}"))
        except json.JSONDecodeError as error:
            raise GaleSubmissionOutboxError("stored_payload_invalid") from error
        if not isinstance(stored_payload, dict):
            raise GaleSubmissionOutboxError("stored_payload_invalid")
        operation_payload = stored_payload
    receipt_payload: dict[str, Any] = {}
    if "receipt_payload_json" in row_keys and row["receipt_payload_json"] is not None:
        try:
            stored_receipt = json.loads(str(row["receipt_payload_json"]))
        except json.JSONDecodeError as error:
            raise GaleSubmissionOutboxError("stored_receipt_invalid") from error
        if not isinstance(stored_receipt, dict):
            raise GaleSubmissionOutboxError("stored_receipt_invalid")
        receipt_payload = stored_receipt
    return GaleSubmission(
        submission_uuid=_validated_uuid(row["submission_uuid"]),
        source_actor=str(row["source_actor"]),
        external_submission_id=str(row["external_submission_id"]),
        idempotency_key=str(row["idempotency_key"]),
        title=str(row["title"]),
        content=str(row["content"]),
        suggested_directory=str(row["suggested_directory"]),
        suggested_tags=normalized_tags,
        ai_visibility=proposal_visibility,
        ai_targets=proposal_targets,
        main_category=proposal_main,
        sub_category=proposal_sub,
        tags=proposal_tags,
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
        operation_type=operation_type,
        note_id=(
            str(row["note_id"])
            if "note_id" in row_keys and row["note_id"] is not None
            else None
        ),
        expected_version=(
            str(row["expected_version"])
            if "expected_version" in row_keys
            and row["expected_version"] is not None
            else None
        ),
        operation_payload=operation_payload,
        terminal_at=(
            str(row["terminal_at"])
            if "terminal_at" in row_keys and row["terminal_at"] is not None
            else None
        ),
        receipt_payload=receipt_payload,
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
        if args.command == "status":
            if not args.database.is_file():
                raise GaleSubmissionOutboxError("outbox_missing")
        else:
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
