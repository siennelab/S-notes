"""Shared, fail-closed configuration helpers for remote MCP authentication."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import stat
from typing import Any
from urllib.parse import urlsplit


__all__ = [
    "RemoteAuthError",
    "load_json_file",
    "require_exact_keys",
    "required_string",
    "required_string_list",
    "validated_https_url",
]


class RemoteAuthError(RuntimeError):
    """A stable, non-secret startup or validation failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def load_json_file(
    path: Path,
    *,
    maximum_bytes: int,
    error_prefix: str,
    allowed_modes: set[int] | None,
    allowed_owner_uids: set[int] | None,
) -> dict[str, Any]:
    """Read one bounded regular JSON file without following path replacements."""
    path = Path(path)
    try:
        before = path.lstat()
    except OSError as error:
        raise RemoteAuthError(f"{error_prefix}_unavailable") from error
    if stat.S_ISLNK(before.st_mode):
        raise RemoteAuthError(f"{error_prefix}_symlink_rejected")
    if not stat.S_ISREG(before.st_mode):
        raise RemoteAuthError(f"{error_prefix}_not_regular")
    if allowed_modes is not None and stat.S_IMODE(before.st_mode) not in allowed_modes:
        raise RemoteAuthError(f"{error_prefix}_permissions_unsafe")
    if (
        error_prefix == "credential"
        and stat.S_IMODE(before.st_mode) == 0o440
        and (before.st_uid != 0 or before.st_gid != 0)
    ):
        raise RemoteAuthError("credential_permissions_unsafe")
    if allowed_owner_uids is not None and before.st_uid not in allowed_owner_uids:
        raise RemoteAuthError(f"{error_prefix}_owner_unsafe")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_dev != before.st_dev
                or opened.st_ino != before.st_ino
            ):
                raise RemoteAuthError(f"{error_prefix}_changed_during_read")
            payload = os.read(descriptor, maximum_bytes + 1)
        finally:
            os.close(descriptor)
    except RemoteAuthError:
        raise
    except OSError as error:
        raise RemoteAuthError(f"{error_prefix}_unavailable") from error
    if len(payload) > maximum_bytes:
        raise RemoteAuthError(f"{error_prefix}_too_large")
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RemoteAuthError(f"{error_prefix}_json_invalid") from error
    if not isinstance(document, dict):
        raise RemoteAuthError(f"{error_prefix}_json_invalid")
    return document


def validated_https_url(value: str, code: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RemoteAuthError(code)
    return value


def required_string(values: Mapping[str, Any], name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise RemoteAuthError(f"{name}_invalid")
    return value


def required_string_list(
    values: Mapping[str, Any],
    name: str,
    code: str,
) -> tuple[str, ...]:
    value = values.get(name)
    if not isinstance(value, list) or not value:
        raise RemoteAuthError(code)
    if any(
        not isinstance(item, str) or not item.strip() or item != item.strip()
        for item in value
    ):
        raise RemoteAuthError(code)
    result = tuple(value)
    if len(result) != len(set(result)):
        raise RemoteAuthError(code)
    return result


def require_exact_keys(
    values: Mapping[str, Any],
    expected: frozenset[str],
    code: str,
) -> None:
    if frozenset(values) != expected:
        raise RemoteAuthError(code)
