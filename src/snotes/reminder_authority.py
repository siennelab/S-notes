"""The single, explicit Reminder authority selection for every runtime.

GUI, MCP stdio, the legacy Reminder helper and developer launches all resolve
authority here.  There is deliberately no default: a runtime without an
explicit authority fails closed instead of silently reviving the local
Reminder database as a second source of truth.

A packaged App carries its authority in Info.plist (written by the build), so
a Finder launch never depends on a build shell's environment.  A source run
must declare it in the environment.
"""

from __future__ import annotations

import os
import plistlib
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


AUTHORITY_ENV = "S_NOTES_REMINDER_AUTHORITY"
ENDPOINT_ENV = "S_NOTES_REMOTE_REMINDER_ENDPOINT"
HELPER_RETIRED_ENV = "S_NOTES_RETIRE_REMINDER_HELPER"
PLIST_AUTHORITY_KEY = "SNotesReminderAuthority"
PLIST_ENDPOINT_KEY = "SNotesRemoteReminderEndpoint"
PLIST_HELPER_RETIRED_KEY = "SNotesReminderHelperRetired"

LOCAL = "local"
REMOTE = "remote"


class ReminderAuthorityError(RuntimeError):
    """The runtime has no valid, explicit Reminder authority."""


@dataclass(frozen=True, slots=True)
class ReminderAuthority:
    mode: str
    endpoint: str | None = None
    helper_retired: bool = False

    def __post_init__(self) -> None:
        if self.mode == LOCAL:
            if self.endpoint:
                raise ReminderAuthorityError("reminder_authority_local_has_endpoint")
            return
        if self.mode != REMOTE:
            raise ReminderAuthorityError(
                f"reminder_authority_invalid: {AUTHORITY_ENV} must be "
                f"'{LOCAL}' or '{REMOTE}'"
            )
        if not self.endpoint or not self.endpoint.strip():
            raise ReminderAuthorityError(
                f"reminder_authority_remote_endpoint_required: set {ENDPOINT_ENV}"
            )

    @property
    def is_remote(self) -> bool:
        return self.mode == REMOTE


# The current formal (legacy) profile, for callers that explicitly mean it.
LOCAL_REMINDER_AUTHORITY = ReminderAuthority(LOCAL)


def resolve_reminder_authority(
    environ: Mapping[str, str] | None = None,
    *,
    bundle_info: Mapping[str, object] | None = None,
) -> ReminderAuthority:
    """Return the explicit authority, or raise ``ReminderAuthorityError``."""
    environment = os.environ if environ is None else environ
    info = _packaged_bundle_info() if bundle_info is None else bundle_info
    if info is not None:
        return _from_bundle(info, environment)
    return _from_environment(environment)


def _from_bundle(
    info: Mapping[str, object],
    environment: Mapping[str, str],
) -> ReminderAuthority:
    mode = info.get(PLIST_AUTHORITY_KEY)
    endpoint = info.get(PLIST_ENDPOINT_KEY) or None
    retired = info.get(PLIST_HELPER_RETIRED_KEY)
    if not isinstance(mode, str) or not mode:
        raise ReminderAuthorityError(
            f"reminder_authority_not_configured: packaged App lacks {PLIST_AUTHORITY_KEY}"
        )
    if endpoint is not None and not isinstance(endpoint, str):
        raise ReminderAuthorityError("reminder_authority_endpoint_invalid")
    if not isinstance(retired, bool):
        raise ReminderAuthorityError(
            f"reminder_authority_not_configured: packaged App lacks {PLIST_HELPER_RETIRED_KEY}"
        )
    authority = ReminderAuthority(mode, endpoint, retired)
    # An inherited environment must never silently switch a packaged App.
    requested = environment.get(AUTHORITY_ENV)
    if requested is not None and (
        requested != authority.mode
        or (authority.is_remote and environment.get(ENDPOINT_ENV) != authority.endpoint)
    ):
        raise ReminderAuthorityError(
            "reminder_authority_conflict: environment differs from the packaged App"
        )
    return authority


def _from_environment(environment: Mapping[str, str]) -> ReminderAuthority:
    mode = environment.get(AUTHORITY_ENV, "").strip()
    if not mode:
        raise ReminderAuthorityError(
            f"reminder_authority_not_configured: set {AUTHORITY_ENV}="
            f"{LOCAL}|{REMOTE}"
        )
    retired = environment.get(HELPER_RETIRED_ENV, "0")
    if retired not in {"0", "1"}:
        raise ReminderAuthorityError(f"{HELPER_RETIRED_ENV} must be 0 or 1")
    endpoint = environment.get(ENDPOINT_ENV, "").strip() or None
    if mode == LOCAL and endpoint:
        raise ReminderAuthorityError(
            f"reminder_authority_conflict: {ENDPOINT_ENV} set for local authority"
        )
    return ReminderAuthority(mode, endpoint, retired == "1")


def _packaged_bundle_info() -> Mapping[str, object] | None:
    if not getattr(sys, "frozen", False):
        return None
    # The App executable and its embedded helpers all live below Contents/.
    for parent in Path(sys.executable).resolve().parents:
        if parent.name == "Contents":
            try:
                info = plistlib.loads((parent / "Info.plist").read_bytes())
            except (OSError, ValueError, plistlib.InvalidFileException) as error:
                raise ReminderAuthorityError(
                    "reminder_authority_not_configured: packaged Info.plist unreadable"
                ) from error
            if not isinstance(info, dict):
                raise ReminderAuthorityError("reminder_authority_not_configured")
            return info
    raise ReminderAuthorityError(
        "reminder_authority_not_configured: packaged runtime outside an App bundle"
    )
