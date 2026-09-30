"""Cloud Reminder adapter for the Mac-local MCP process.

This module deliberately owns only actor credentials.  It never reads the GUI
owner credential or the GUI offline snapshot cache.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Callable, Protocol

from src.snotes.transport.remote_reminder_client import (
    RemoteReminderClient,
    RemoteReminderError,
)


MAC_LOCAL_REMINDER_ACTORS = frozenset({"yan", "claude-code"})
ACTOR_CREDENTIAL_DIRECTORY = Path(
    "Library/Application Support/S-notes-Step07/actor-credentials"
)


class RemoteReminderCommandClient(Protocol):
    def command(self, name: str, payload: dict) -> dict: ...


class ActorReminderCredentialError(RuntimeError):
    """Raised without including credential contents or an owner-cache path."""


class ActorReminderCredentialProvider:
    """Read one fixed actor token from a private, dedicated file."""

    def __init__(
        self,
        actor_target_id: str,
        *,
        credential_path: Path | None = None,
    ) -> None:
        if actor_target_id not in MAC_LOCAL_REMINDER_ACTORS:
            raise ValueError("unsupported_mac_reminder_actor")
        self.actor_target_id = actor_target_id
        self.path = credential_path or default_actor_credential_path(actor_target_id)

    def __call__(self) -> str:
        try:
            metadata = self.path.lstat()
        except OSError as error:
            raise ActorReminderCredentialError(
                f"Reminder actor credential unavailable for {self.actor_target_id}."
            ) from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ActorReminderCredentialError(
                f"Reminder actor credential is not a private file for {self.actor_target_id}."
            )
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise ActorReminderCredentialError(
                f"Reminder actor credential permissions are unsafe for {self.actor_target_id}."
            )
        if metadata.st_size > 4096:
            raise ActorReminderCredentialError(
                f"Reminder actor credential is invalid for {self.actor_target_id}."
            )
        try:
            token = self.path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as error:
            raise ActorReminderCredentialError(
                f"Reminder actor credential unavailable for {self.actor_target_id}."
            ) from error
        if not token or any(character.isspace() for character in token):
            raise ActorReminderCredentialError(
                f"Reminder actor credential is invalid for {self.actor_target_id}."
            )
        return token


def default_actor_credential_path(actor_target_id: str) -> Path:
    if actor_target_id not in MAC_LOCAL_REMINDER_ACTORS:
        raise ValueError("unsupported_mac_reminder_actor")
    host_home = Path(
        os.environ.get("S_NOTES_STEP07_HOST_HOME", str(Path.home()))
    )
    return host_home / ACTOR_CREDENTIAL_DIRECTORY / actor_target_id


class RemoteMcpReminderAdapter:
    """Preserve LocalMcpService's Reminder result contract over Cloud commands."""

    def __init__(self, client: RemoteReminderCommandClient) -> None:
        self._client = client

    def list_reminder_labels(self) -> dict:
        return self._read(
            "list_reminder_labels",
            {},
            {"status": "failed", "labels": []},
        )

    def create_reminder_label(self, request_id: str, name: str) -> dict:
        return self._write(
            "create_reminder_label",
            request_id,
            {"request_id": request_id, "name": name},
        )

    def list_reminder_tables(
        self,
        query: str = "",
        page: int = 1,
        page_size: int = 10,
        label_id: int | None = None,
        ai_visibility: str = "all",
    ) -> dict:
        failure = {
            "status": "failed",
            "page": page,
            "page_size": page_size,
            "total_visible_matches": 0,
            "has_more": False,
            "results": [],
        }
        result = self._read(
            "list_reminder_tables",
            {
                "query": query,
                "label_id": label_id,
                "ai_visibility": ai_visibility,
            },
            failure,
        )
        return _paginate(result, page=page, page_size=page_size)

    def get_reminder_table(self, table_id: int) -> dict:
        return self._read(
            "get_reminder_table",
            {"table_id": table_id},
            {"status": "failed", "table_id": table_id},
        )

    def create_reminder_table(
        self,
        request_id: str,
        title: str | None = None,
        label_id: int | None = None,
    ) -> dict:
        return self._write(
            "create_reminder_table",
            request_id,
            {"request_id": request_id, "title": title, "label_id": label_id},
        )

    def update_reminder_table(
        self,
        request_id: str,
        table_id: int,
        title: str | None = None,
        label_id: int | None = None,
    ) -> dict:
        return self._write(
            "update_reminder_table",
            request_id,
            {
                "request_id": request_id,
                "table_id": table_id,
                "title": title,
                "label_id": label_id,
            },
            table_id=table_id,
        )

    def list_reminder_items(
        self,
        table_id: int,
        query: str = "",
        page: int = 1,
        page_size: int = 10,
        lifecycle: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict:
        failure = {
            "status": "failed",
            "table_id": table_id,
            "page": page,
            "page_size": page_size,
            "total_visible_matches": 0,
            "has_more": False,
            "results": [],
        }
        result = self._read(
            "list_reminder_items",
            {
                "table_id": table_id,
                "query": query,
                "lifecycle": lifecycle,
                "date_from": date_from,
                "date_to": date_to,
            },
            failure,
        )
        return _paginate(result, page=page, page_size=page_size)

    def get_reminder_item(self, item_id: int) -> dict:
        return self._read(
            "get_reminder_item",
            {"item_id": item_id},
            {"status": "failed", "item_id": item_id},
        )

    def create_reminder_item(
        self,
        request_id: str,
        table_id: int,
        title: str,
        action_spec: dict,
        deadline_spec: dict | None = None,
        note: str = "",
        lifecycle: str = "active",
    ) -> dict:
        return self._write(
            "create_reminder_item",
            request_id,
            {
                "request_id": request_id,
                "table_id": table_id,
                "title": title,
                "action_spec": action_spec,
                "deadline_spec": deadline_spec,
                "note": note,
                "lifecycle": lifecycle,
            },
            table_id=table_id,
        )

    def update_reminder_item(
        self,
        request_id: str,
        item_id: int,
        title: str | None = None,
        note: str | None = None,
        action_spec: dict | None = None,
        deadline_spec: dict | None = None,
        clear_deadline: bool = False,
        lifecycle: str | None = None,
        multi_notify: bool | None = None,
    ) -> dict:
        return self._write(
            "update_reminder_item",
            request_id,
            {
                "request_id": request_id,
                "item_id": item_id,
                "title": title,
                "note": note,
                "action_spec": action_spec,
                "deadline_spec": deadline_spec,
                "clear_deadline": clear_deadline,
                "lifecycle": lifecycle,
                "multi_notify": multi_notify,
            },
            item_id=item_id,
        )

    def complete_reminder_item(
        self,
        request_id: str,
        item_id: int,
        current_occurrence_only: bool = True,
    ) -> dict:
        return self._write(
            "complete_reminder_item",
            request_id,
            {
                "request_id": request_id,
                "item_id": item_id,
                "current_occurrence_only": current_occurrence_only,
            },
            item_id=item_id,
        )

    def pause_reminder_item(self, request_id: str, item_id: int) -> dict:
        return self._write(
            "pause_reminder_item",
            request_id,
            {"request_id": request_id, "item_id": item_id},
            item_id=item_id,
        )

    def resume_reminder_item(self, request_id: str, item_id: int) -> dict:
        return self._write(
            "resume_reminder_item",
            request_id,
            {"request_id": request_id, "item_id": item_id},
            item_id=item_id,
        )

    def _read(self, command: str, payload: dict, failure: dict) -> dict:
        try:
            return self._client.command(command, payload)
        except RemoteReminderError as error:
            return {**failure, "reason": _safe_remote_reason(error.kind)}

    def _write(
        self,
        command: str,
        request_id: str,
        payload: dict,
        *,
        table_id: int | None = None,
        item_id: int | None = None,
    ) -> dict:
        try:
            return self._client.command(command, payload)
        except RemoteReminderError as error:
            return _write_failure(
                request_id,
                table_id=table_id,
                item_id=item_id,
                reason=_safe_remote_reason(error.kind),
            )


def compose_remote_mcp_reminder(
    actor_target_id: str,
    endpoint: str,
    *,
    credential_path: Path | None = None,
    timeout_seconds: float = 4.0,
    client_factory: Callable[..., RemoteReminderCommandClient] = RemoteReminderClient,
) -> RemoteMcpReminderAdapter:
    provider = ActorReminderCredentialProvider(
        actor_target_id,
        credential_path=credential_path,
    )
    client = client_factory(
        endpoint,
        provider,
        timeout_seconds=timeout_seconds,
    )
    return RemoteMcpReminderAdapter(client)


def _paginate(result: dict, *, page: int, page_size: int) -> dict:
    if result.get("status") != "ok":
        return result
    items = list(result.get("results", []))
    offset = (page - 1) * page_size
    selected = items[offset : offset + page_size]
    return {
        **result,
        "page": page,
        "page_size": page_size,
        "total_visible_matches": len(items),
        "has_more": offset + len(selected) < len(items),
        "results": selected,
    }


def _safe_remote_reason(kind: str) -> str:
    return {
        "offline": "Cloud Reminder 暂时不可达。",
        "auth": "Reminder actor credential 无效或无权访问。",
        "schema_mismatch": "Reminder API 版本不匹配。",
        "server": "Cloud Reminder 服务暂时不可用。",
    }.get(kind, "Cloud Reminder 服务暂时不可用。")


def _write_failure(
    request_id: str,
    *,
    table_id: int | None = None,
    item_id: int | None = None,
    reason: str,
) -> dict:
    return {
        "status": "failed",
        "request_id": request_id,
        "table_id": table_id,
        "item_id": item_id,
        "reminder_id": item_id,
        "title": "",
        "handling_mode": "none",
        "modified_formal_reminder": False,
        "entered_confirmation": False,
        "reason": reason,
    }
