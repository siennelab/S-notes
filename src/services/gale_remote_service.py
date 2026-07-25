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
    verify_gale_mirror,
)


GALE_READ_SCOPE = "gale.read"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8767
DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 20
MAX_QUERY_LENGTH = 500
EXPECTED_MIRROR_NAME = "gale-mirror.db"


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
    audience = _required_value(values, "SNOTES_GALE_AUDIENCE")
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

    return GaleRemoteConfig(
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
    )


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
    match_expression = '"' + needle.replace('"', '""') + '"'
    connection = _open_remote_mirror(config)
    try:
        rows = connection.execute(
            """
            SELECT notes.source_note_id, notes.title, notes.body,
                   notes.directory, notes.tags_json, notes.source_updated_at
            FROM mirror_notes_fts AS search
            JOIN mirror_notes AS notes
              ON notes.mirror_note_id = search.rowid
            WHERE mirror_notes_fts MATCH ?
            ORDER BY bm25(mirror_notes_fts), notes.source_updated_at DESC
            LIMIT ?
            """,
            (match_expression, int(limit)),
        ).fetchall()
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
        row = connection.execute(
            """
            SELECT source_note_id, title, body, directory, tags_json,
                   source_updated_at, source_version
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
    return {
        "note_id": int(row["source_note_id"]),
        "title": str(row["title"]),
        "content": str(row["body"]),
        "directory": str(row["directory"]),
        "tags": _safe_tags(row["tags_json"]),
        "updated_at": str(row["source_updated_at"]),
        "version": str(row["source_version"]),
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
        if str(schema_version[0]) != MIRROR_SCHEMA_VERSION:
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
        schema_version=MIRROR_SCHEMA_VERSION,
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
