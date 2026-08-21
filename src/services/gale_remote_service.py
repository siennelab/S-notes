from __future__ import annotations

import json
import os
import sqlite3
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from src.services.gale_mirror_service import (
    GALE_ACTOR_ID,
    MIRROR_SCHEMA_VERSION,
    GaleMirrorError,
    GaleMirrorVerification,
    search_gale_mirror_rows,
    verify_gale_mirror,
)
from src.search_query import parse_search_query


GALE_READ_SCOPE = "gale.read"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8767
DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 20
MAX_QUERY_LENGTH = 500
EXPECTED_MIRROR_NAME = "gale-mirror.db"
EXPECTED_PRODUCTION_OUTBOX_ROOT = Path(
    "/var/lib/snotes-gale-r1b-production"
)
EXPECTED_PRODUCTION_OUTBOX_PATH = (
    EXPECTED_PRODUCTION_OUTBOX_ROOT / "outbox.db"
)


class GaleRemoteError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class GaleRemoteConfig:
    mirror_path: Path
    mirror_root: Path
    issuer_url: str
    resource_url: str
    audience: str
    jwks_url: str
    allowed_subjects: frozenset[str]
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    expected_owner_uid: int = 0
    outbox_root: Path | None = None
    outbox_path: Path | None = None


@dataclass(frozen=True)
class GaleRemoteHealth:
    schema_version: str
    note_count: int
    fts_count: int
    file_mode: str


def load_gale_remote_config(
    environment: dict[str, str] | None = None,
) -> GaleRemoteConfig:
    values = os.environ if environment is None else environment
    mirror_path = _required_path(values, "SNOTES_GALE_MIRROR_PATH")
    mirror_root = _required_path(values, "SNOTES_GALE_MIRROR_ROOT")
    issuer_url = _required_https_url(values, "SNOTES_GALE_ISSUER_URL")
    resource_url = _required_https_url(values, "SNOTES_GALE_RESOURCE_URL")
    audience = _required_https_url(values, "SNOTES_GALE_AUDIENCE")
    jwks_url = _required_https_url(values, "SNOTES_GALE_JWKS_URL")
    allowed_subjects = frozenset(
        item.strip()
        for item in _required_value(
            values, "SNOTES_GALE_ALLOWED_SUBJECTS"
        ).split(",")
        if item.strip()
    )
    if not allowed_subjects:
        raise GaleRemoteError("allowed_subjects_missing")
    outbox_root = _required_path(values, "SNOTES_GALE_R1B_OUTBOX_ROOT")
    outbox_path = _required_path(values, "SNOTES_GALE_R1B_OUTBOX_PATH")
    if (
        outbox_root != EXPECTED_PRODUCTION_OUTBOX_ROOT
        or outbox_path != EXPECTED_PRODUCTION_OUTBOX_PATH
    ):
        raise GaleRemoteError("production_outbox_path_not_allowed")

    host = values.get("SNOTES_GALE_HOST", DEFAULT_HOST).strip()
    if host != DEFAULT_HOST:
        raise GaleRemoteError("host_must_be_loopback")
    try:
        port = int(values.get("SNOTES_GALE_PORT", str(DEFAULT_PORT)))
    except ValueError as error:
        raise GaleRemoteError("invalid_port") from error
    if not 1 <= port <= 65535:
        raise GaleRemoteError("invalid_port")

    try:
        expected_owner_uid = int(
            values.get("SNOTES_GALE_EXPECTED_OWNER_UID", str(os.geteuid()))
        )
    except ValueError as error:
        raise GaleRemoteError("invalid_owner_uid") from error
    if expected_owner_uid < 0:
        raise GaleRemoteError("invalid_owner_uid")

    config = GaleRemoteConfig(
        mirror_path=mirror_path,
        mirror_root=mirror_root,
        issuer_url=issuer_url,
        resource_url=resource_url,
        audience=audience,
        jwks_url=jwks_url,
        allowed_subjects=allowed_subjects,
        host=host,
        port=port,
        expected_owner_uid=expected_owner_uid,
        outbox_root=outbox_root,
        outbox_path=outbox_path,
    )
    validate_remote_auth_binding(config)
    validate_production_outbox_location(config)
    return config


def validate_remote_auth_binding(config: GaleRemoteConfig) -> None:
    if config.audience != config.resource_url:
        raise GaleRemoteError("audience_resource_mismatch")


def validate_production_outbox_location(config: GaleRemoteConfig) -> Path:
    if config.outbox_root is None or config.outbox_path is None:
        raise GaleRemoteError("production_outbox_missing")
    root = Path(config.outbox_root)
    path = Path(config.outbox_path)
    if not root.is_absolute() or not path.is_absolute():
        raise GaleRemoteError("production_outbox_path_not_absolute")
    if path.name != "outbox.db":
        raise GaleRemoteError("unexpected_production_outbox_name")
    try:
        resolved_root = root.resolve(strict=False)
        resolved_path = path.resolve(strict=False)
        mirror_root = Path(config.mirror_root).resolve(strict=False)
    except OSError as error:
        raise GaleRemoteError("production_outbox_path_invalid") from error
    if resolved_path.parent != resolved_root:
        raise GaleRemoteError("production_outbox_outside_root")
    if (
        resolved_root == mirror_root
        or resolved_root.is_relative_to(mirror_root)
        or mirror_root.is_relative_to(resolved_root)
    ):
        raise GaleRemoteError("production_outbox_overlaps_mirror")
    return path


def validate_initialized_production_outbox(config: GaleRemoteConfig) -> Path:
    path = validate_production_outbox_location(config)
    root = Path(config.outbox_root)
    if root.is_symlink():
        raise GaleRemoteError("production_outbox_root_symlink")
    if path.is_symlink():
        raise GaleRemoteError("production_outbox_symlink")
    try:
        resolved_root = root.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
    except OSError as error:
        raise GaleRemoteError("production_outbox_unavailable") from error
    if not resolved_root.is_dir() or not resolved_path.is_file():
        raise GaleRemoteError("production_outbox_unavailable")
    root_stat = resolved_root.stat()
    path_stat = resolved_path.stat()
    if stat.S_IMODE(root_stat.st_mode) != 0o700:
        raise GaleRemoteError("production_outbox_root_permissions_unsafe")
    if stat.S_IMODE(path_stat.st_mode) != 0o600:
        raise GaleRemoteError("production_outbox_permissions_unsafe")
    if (
        root_stat.st_uid != config.expected_owner_uid
        or path_stat.st_uid != config.expected_owner_uid
    ):
        raise GaleRemoteError("production_outbox_owner_unsafe")
    return resolved_path


def validate_remote_mirror(config: GaleRemoteConfig) -> GaleRemoteHealth:
    mirror = _canonical_regular_file(config.mirror_path)
    root = _canonical_directory(config.mirror_root)
    if mirror.name != EXPECTED_MIRROR_NAME:
        raise GaleRemoteError("unexpected_mirror_name")
    if not mirror.is_relative_to(root):
        raise GaleRemoteError("mirror_outside_service_root")
    file_stat = mirror.stat()
    if stat.S_IMODE(file_stat.st_mode) != 0o600:
        raise GaleRemoteError("mirror_permissions_unsafe")
    if file_stat.st_uid != config.expected_owner_uid:
        raise GaleRemoteError("mirror_owner_unsafe")
    try:
        verification = verify_gale_mirror(
            mirror,
            expected_actor=GALE_ACTOR_ID,
        )
    except GaleMirrorError as error:
        raise GaleRemoteError(error.code) from error
    _validate_exact_fts_rowids(mirror)
    _prove_read_only_reopen(mirror)
    return _health_from_verification(verification)


def search_remote_mirror(
    config: GaleRemoteConfig,
    query: str,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> list[dict[str, Any]]:
    needle = str(query).strip()
    if not needle:
        return []
    if len(needle) > MAX_QUERY_LENGTH:
        raise GaleRemoteError("query_too_long")
    if not 1 <= int(limit) <= MAX_SEARCH_LIMIT:
        raise GaleRemoteError("invalid_limit")
    parsed_query = parse_search_query(needle)
    if not parsed_query.tokens:
        return []
    connection = _open_remote_mirror(config)
    try:
        rows = search_gale_mirror_rows(
            connection,
            parsed_query,
            limit=int(limit),
        )
    except sqlite3.Error as error:
        raise GaleRemoteError("search_failed") from error
    finally:
        connection.close()
    return [
        {
            "note_id": int(row["source_note_id"]),
            "title": str(row["title"]),
            "summary": _safe_summary(str(row["body"])),
            "directory": str(row["directory"]),
            "tags": _safe_tags(row["tags_json"]),
            "updated_at": str(row["source_updated_at"]),
        }
        for row in rows
    ]


def get_remote_note(
    config: GaleRemoteConfig,
    note_id: int,
) -> dict[str, Any] | None:
    if not 1 <= int(note_id) <= 9_223_372_036_854_775_807:
        raise GaleRemoteError("invalid_note_id")
    connection = _open_remote_mirror(config)
    try:
        has_metadata = _table_has_columns(
            connection,
            "mirror_notes",
            {
                "ai_visibility",
                "ai_targets_json",
                "main_category",
                "sub_category",
            },
        )
        metadata_columns = (
            ", ai_visibility, ai_targets_json, main_category, sub_category"
            if has_metadata
            else ""
        )
        row = connection.execute(
            f"""
            SELECT source_note_id, title, body, directory, tags_json,
                   source_updated_at, source_version
                   {metadata_columns}
            FROM mirror_notes
            WHERE source_note_id = ?
            """,
            (int(note_id),),
        ).fetchone()
    except sqlite3.Error as error:
        raise GaleRemoteError("read_failed") from error
    finally:
        connection.close()
    if row is None:
        return None
    result = {
        "note_id": int(row["source_note_id"]),
        "title": str(row["title"]),
        "content": str(row["body"]),
        "directory": str(row["directory"]),
        "tags": _safe_tags(row["tags_json"]),
        "updated_at": str(row["source_updated_at"]),
        "version": str(row["source_version"]),
    }
    if has_metadata:
        result.update(
            {
                "ai_visibility": str(row["ai_visibility"]),
                "ai_targets": _safe_tags(row["ai_targets_json"]),
                "main_category": str(row["main_category"]),
                "sub_category": str(row["sub_category"]),
            }
        )
    return result


def list_remote_directories(config: GaleRemoteConfig) -> dict[str, Any]:
    connection = _open_remote_mirror(config)
    try:
        if not _table_exists(connection, "mirror_directories"):
            return {
                "status": "unavailable",
                "directories": [],
                "reason": "catalog_unavailable",
            }
        rows = connection.execute(
            """
            SELECT directory_id, name, parent_id, main_category,
                   sub_category, path_json
            FROM mirror_directories
            ORDER BY path_json, directory_id
            """
        ).fetchall()
    except sqlite3.Error as error:
        raise GaleRemoteError("catalog_read_failed") from error
    finally:
        connection.close()
    return {
        "status": "ok",
        "directories": [
            {
                "directory_id": int(row["directory_id"]),
                "name": str(row["name"]),
                "parent_id": (
                    int(row["parent_id"]) if row["parent_id"] is not None else None
                ),
                "main_category": str(row["main_category"]),
                "sub_category": str(row["sub_category"]),
                "path": _safe_tags(row["path_json"]),
                "writable": True,
            }
            for row in rows
        ],
        "reason": "",
    }


def list_remote_tags(config: GaleRemoteConfig) -> dict[str, Any]:
    connection = _open_remote_mirror(config)
    try:
        if not _table_exists(connection, "mirror_tags"):
            return {"status": "unavailable", "tags": [], "reason": "catalog_unavailable"}
        rows = connection.execute(
            "SELECT name FROM mirror_tags ORDER BY name COLLATE NOCASE"
        ).fetchall()
    except sqlite3.Error as error:
        raise GaleRemoteError("catalog_read_failed") from error
    finally:
        connection.close()
    return {
        "status": "ok",
        "tags": [{"name": str(row["name"])} for row in rows],
        "reason": "",
    }


def list_remote_ai_metadata_options(config: GaleRemoteConfig) -> dict[str, Any]:
    connection = _open_remote_mirror(config)
    try:
        if not all(
            _table_exists(connection, table)
            for table in ("mirror_ai_visibility_options", "mirror_ai_targets")
        ):
            return {
                "status": "unavailable",
                "ai_visibility_options": [],
                "ai_targets": [],
                "reason": "catalog_unavailable",
            }
        visibility_rows = connection.execute(
            """
            SELECT value, label
            FROM mirror_ai_visibility_options
            ORDER BY rowid
            """
        ).fetchall()
        target_rows = connection.execute(
            """
            SELECT target_id, display_name
            FROM mirror_ai_targets
            ORDER BY rowid
            """
        ).fetchall()
    except sqlite3.Error as error:
        raise GaleRemoteError("catalog_read_failed") from error
    finally:
        connection.close()
    return {
        "status": "ok",
        "ai_visibility_options": [
            {"value": str(row["value"]), "label": str(row["label"])}
            for row in visibility_rows
        ],
        "ai_targets": [
            {
                "target_id": str(row["target_id"]),
                "display_name": str(row["display_name"]),
            }
            for row in target_rows
        ],
        "reason": "",
    }


def safe_request_id() -> str:
    return uuid.uuid4().hex


def monotonic_milliseconds(started_at: float) -> int:
    return max(0, round((time.monotonic() - started_at) * 1000))


def _open_remote_mirror(config: GaleRemoteConfig) -> sqlite3.Connection:
    mirror = _canonical_regular_file(config.mirror_path)
    root = _canonical_directory(config.mirror_root)
    if not mirror.is_relative_to(root) or mirror.name != EXPECTED_MIRROR_NAME:
        raise GaleRemoteError("unsafe_mirror_path")
    connection = sqlite3.connect(
        f"{mirror.as_uri()}?mode=ro&immutable=1",
        uri=True,
        timeout=5.0,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA trusted_schema = OFF")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table,),
    ).fetchone()
    return row is not None


def _table_has_columns(
    connection: sqlite3.Connection,
    table: str,
    columns: set[str],
) -> bool:
    existing = {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    return columns.issubset(existing)


def _validate_exact_fts_rowids(mirror: Path) -> None:
    connection = sqlite3.connect(
        f"{mirror.as_uri()}?mode=ro&immutable=1",
        uri=True,
        timeout=5.0,
    )
    try:
        connection.execute("PRAGMA query_only = ON")
        note_ids = {
            int(row[0])
            for row in connection.execute(
                "SELECT mirror_note_id FROM mirror_notes"
            ).fetchall()
        }
        fts_ids = {
            int(row[0])
            for row in connection.execute(
                "SELECT rowid FROM mirror_notes_fts"
            ).fetchall()
        }
        if note_ids != fts_ids:
            raise GaleRemoteError("fts_rowid_mismatch")
    except sqlite3.Error as error:
        raise GaleRemoteError("mirror_validation_failed") from error
    finally:
        connection.close()


def _prove_read_only_reopen(mirror: Path) -> None:
    connection = sqlite3.connect(
        f"{mirror.as_uri()}?mode=ro&immutable=1",
        uri=True,
        timeout=5.0,
    )
    try:
        connection.execute("PRAGMA query_only = ON")
        query_only = int(connection.execute("PRAGMA query_only").fetchone()[0])
        schema_version = connection.execute(
            """
            SELECT meta_value
            FROM mirror_metadata
            WHERE meta_key='mirror_schema_version'
            """
        ).fetchone()
        if query_only != 1 or schema_version is None:
            raise GaleRemoteError("read_only_reopen_failed")
        if str(schema_version[0]) not in {"1", MIRROR_SCHEMA_VERSION}:
            raise GaleRemoteError("schema_version_mismatch")
    except sqlite3.Error as error:
        raise GaleRemoteError("read_only_reopen_failed") from error
    finally:
        connection.close()


def _safe_tags(value: Any) -> list[str]:
    try:
        tags = json.loads(str(value))
    except (TypeError, ValueError) as error:
        raise GaleRemoteError("invalid_safe_tags") from error
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise GaleRemoteError("invalid_safe_tags")
    return tags


def _safe_summary(body: str) -> str:
    compact = " ".join(body.split())
    return compact[:280]


def _health_from_verification(
    verification: GaleMirrorVerification,
) -> GaleRemoteHealth:
    return GaleRemoteHealth(
        schema_version=getattr(verification, "schema_version", MIRROR_SCHEMA_VERSION),
        note_count=verification.note_count,
        fts_count=verification.fts_count,
        file_mode=verification.file_mode,
    )


def _required_value(values: dict[str, str] | os._Environ[str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise GaleRemoteError(f"{name.lower()}_missing")
    return value


def _required_path(
    values: dict[str, str] | os._Environ[str],
    name: str,
) -> Path:
    return Path(_required_value(values, name)).expanduser()


def _required_https_url(
    values: dict[str, str] | os._Environ[str],
    name: str,
) -> str:
    value = _required_value(values, name)
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise GaleRemoteError(f"{name.lower()}_invalid")
    return value


def _canonical_regular_file(path: Path) -> Path:
    original = Path(path)
    if original.is_symlink():
        raise GaleRemoteError("mirror_symlink_rejected")
    try:
        resolved = original.resolve(strict=True)
    except OSError as error:
        raise GaleRemoteError("mirror_unavailable") from error
    if not resolved.is_file():
        raise GaleRemoteError("mirror_unavailable")
    return resolved


def _canonical_directory(path: Path) -> Path:
    original = Path(path)
    if original.is_symlink():
        raise GaleRemoteError("mirror_root_symlink_rejected")
    try:
        resolved = original.resolve(strict=True)
    except OSError as error:
        raise GaleRemoteError("mirror_root_unavailable") from error
    if not resolved.is_dir():
        raise GaleRemoteError("mirror_root_unavailable")
    return resolved
