"""Narrow authenticated HTTP boundary for the Cloud Reminder service.

The transport deliberately exposes named Reminder commands instead of a generic
mutation endpoint.  Authentication fixes the caller identity before request
JSON is considered, so an actor can never select or impersonate another actor.
"""

from __future__ import annotations

import hmac
import json
import socket
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Mapping
from urllib.parse import urlsplit

from src.snotes.application.reminder_service import ReminderService
from src.snotes.domain.reminders import (
    REMINDER_ACTIVE,
    ReminderDraft,
    ReminderQuery,
    ReminderSettings,
)
from src.snotes.storage.schema import REMINDER_SCHEMA_VERSION


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766
DEFAULT_MAX_BODY_BYTES = 64 * 1024
DEFAULT_REQUEST_TIMEOUT = 10.0
STAGING_TOPIC = "s-step06"

OWNER_ROLE = "owner"
ACTOR_ROLE = "actor"
ALLOWED_ACTORS = frozenset({"yan", "claude-code", "clawbot"})
IDENTITY_FIELDS = frozenset(
    {"actor", "actor_id", "actor_target_id", "identity", "principal", "role"}
)

ACTOR_COMMANDS = frozenset(
    {
        "list_reminder_labels",
        "list_reminder_tables",
        "get_reminder_table",
        "list_reminder_items",
        "get_reminder_item",
        "create_reminder_label",
        "create_reminder_table",
        "update_reminder_table",
        "create_reminder_item",
        "update_reminder_item",
        "complete_reminder_item",
        "pause_reminder_item",
        "resume_reminder_item",
    }
)

OWNER_ONLY_COMMANDS = frozenset(
    {
        "ensure_default_table",
        "create_reminder",
        "update_reminder",
        "delete_reminder",
        "complete_occurrence",
        "complete_reminder",
        "pause_reminder",
        "resume_reminder",
        "create_label",
        "rename_label",
        "reorder_labels",
        "delete_label",
        "create_table",
        "update_table_metadata",
        "set_table_ai_visible",
        "delete_table",
        "restore_table",
        "permanently_delete_table",
        "save_settings",
        "send_test_notification",
        "list_ai_operation_logs",
    }
)


@dataclass(frozen=True, slots=True)
class ReminderCredential:
    identity: str
    role: str

    def __post_init__(self) -> None:
        if self.role == OWNER_ROLE:
            if not self.identity.strip():
                raise ValueError("owner_identity_required")
            return
        if self.role != ACTOR_ROLE or self.identity not in ALLOWED_ACTORS:
            raise ValueError("unsupported_reminder_credential")


@dataclass(frozen=True, slots=True)
class ReminderHttpResponse:
    status: int
    body: dict
    headers: tuple[tuple[str, str], ...] = ()


class ReminderHttpApplication:
    """Pure request dispatcher shared by the HTTP handler and focused tests."""

    def __init__(
        self,
        service: ReminderService,
        credentials: Mapping[str, ReminderCredential],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not credentials:
            raise ValueError("reminder_credentials_required")
        normalized: dict[str, ReminderCredential] = {}
        for token, credential in credentials.items():
            if not isinstance(token, str) or not token.strip():
                raise ValueError("empty_reminder_credential")
            normalized[token] = credential
        self._service = service
        self._credentials = normalized
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._request_lock = threading.RLock()

    def handle(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes = b"",
    ) -> ReminderHttpResponse:
        # SQLite writes and visibility checks form one authorization decision.
        # Serializing requests prevents a concurrent owner visibility change from
        # racing an actor's check-and-mutate sequence inside ReminderService.
        with self._request_lock:
            return self._handle(method, path, headers=headers, body=body)

    def _handle(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes = b"",
    ) -> ReminderHttpResponse:
        credential, auth_error = self._authenticate(headers or {})
        if auth_error is not None:
            return auth_error
        assert credential is not None

        parsed = urlsplit(path)
        if parsed.query or parsed.fragment:
            return _error(400, "query_not_supported")
        if method == "GET" and parsed.path == "/v1/reminder/snapshot":
            if body:
                return _error(400, "request_body_not_allowed")
            return ReminderHttpResponse(200, self._snapshot(credential))
        prefix = "/v1/reminder/commands/"
        if method != "POST" or not parsed.path.startswith(prefix):
            return _error(404, "not_found")
        command = parsed.path[len(prefix) :]
        if not command or "/" in command:
            return _error(404, "not_found")
        if command not in ACTOR_COMMANDS | OWNER_ONLY_COMMANDS:
            return _error(404, "unknown_command")
        if credential.role != OWNER_ROLE and command not in ACTOR_COMMANDS:
            return _error(403, "command_forbidden")

        payload, payload_error = _decode_json_object(body)
        if payload_error is not None:
            return payload_error
        assert payload is not None
        if IDENTITY_FIELDS.intersection(payload):
            return _error(400, "identity_in_body_forbidden")
        try:
            result = self._dispatch(command, credential, payload)
        except (KeyError, TypeError, ValueError) as error:
            return _error(400, str(error) or "invalid_request")
        except RuntimeError as error:
            return _error(503, str(error) or "service_unavailable")
        return ReminderHttpResponse(200, result)

    def _authenticate(
        self, headers: Mapping[str, str]
    ) -> tuple[ReminderCredential | None, ReminderHttpResponse | None]:
        authorization = _header(headers, "authorization")
        if authorization is None:
            return None, ReminderHttpResponse(
                401,
                {"error": "credential_required"},
                (("WWW-Authenticate", "Bearer"),),
            )
        scheme, separator, token = authorization.partition(" ")
        if separator != " " or scheme.casefold() != "bearer" or not token:
            return None, _error(403, "credential_invalid")
        matched: ReminderCredential | None = None
        token_bytes = token.encode("utf-8")
        for candidate, credential in self._credentials.items():
            if hmac.compare_digest(token_bytes, candidate.encode("utf-8")):
                matched = credential
        if matched is None:
            return None, _error(403, "credential_invalid")
        return matched, None

    def _snapshot(self, credential: ReminderCredential) -> dict:
        actor_access = credential.role == ACTOR_ROLE
        tables = self._service.list_tables(ReminderQuery(ai_access=actor_access))
        items = self._service.list_reminders(
            ReminderQuery(ai_access=actor_access),
            materialize_occurrences=False,
        )
        visible_label_ids = {item.table.label_id for item in tables}
        labels = [
            _label_payload(label)
            for label in self._service.list_labels()
            if not actor_access or label.id in visible_label_ids
        ]
        settings = self._service.settings()
        settings_payload = {
            "notifications_enabled": settings.notifications_enabled,
            "daily_time": settings.daily_time,
        }
        if not actor_access:
            settings_payload["ntfy_topic"] = settings.ntfy_topic
        instant = self._clock()
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        timestamp = instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        result = {
            "schema_version": int(REMINDER_SCHEMA_VERSION),
            "server_time": timestamp,
            "snapshot_time": timestamp,
            "labels": labels,
            "tables": [_table_payload(item) for item in tables],
            "items": [_item_payload(item) for item in items],
            "settings": settings_payload,
            "badge_count": sum(1 for item in items if item.status in {"due", "overdue"}),
        }
        if credential.role == OWNER_ROLE:
            trash = self._service.list_trash()
            result["trash"] = {
                "labels": [_label_payload(label) for label in trash.labels],
                "tables": [_table_payload(item) for item in trash.tables],
            }
        return result

    def _dispatch(
        self,
        command: str,
        credential: ReminderCredential,
        payload: dict,
    ) -> dict:
        if command in ACTOR_COMMANDS:
            return self._dispatch_shared(command, credential, payload)
        return self._dispatch_owner(command, payload)

    def _dispatch_shared(
        self,
        command: str,
        credential: ReminderCredential,
        payload: dict,
    ) -> dict:
        actor_access = credential.role == ACTOR_ROLE
        actor = credential.identity
        if command == "list_reminder_labels":
            _require_keys(payload, set())
            tables = self._service.list_tables(ReminderQuery(ai_access=actor_access))
            visible_label_ids = {item.table.label_id for item in tables}
            labels = [
                _label_payload(item)
                for item in self._service.list_labels()
                if not actor_access or item.id in visible_label_ids
            ]
            return {"status": "ok", "labels": labels, "reason": ""}
        if command == "list_reminder_tables":
            _require_keys(payload, {"query", "label_id", "ai_visibility"})
            items = self._service.list_tables(
                ReminderQuery(
                    query=_optional_str(payload, "query", ""),
                    label_id=_optional_int(payload, "label_id"),
                    ai_visibility=_optional_str(payload, "ai_visibility", "all"),
                    ai_access=actor_access,
                )
            )
            return {"status": "ok", "results": [_table_payload(item) for item in items], "reason": ""}
        if command == "get_reminder_table":
            _require_keys(payload, {"table_id"}, required={"table_id"})
            table_id = _int(payload, "table_id")
            item = self._service.get_table(table_id, ai_access=actor_access)
            if item is None:
                return {"status": "not_found", "table_id": table_id, "reason": "not_found"}
            return {"status": "ok", "table_id": table_id, "table": _table_payload(item), "reason": ""}
        if command == "list_reminder_items":
            _require_keys(
                payload,
                {"table_id", "query", "lifecycle", "date_from", "date_to"},
                required={"table_id"},
            )
            table_id = _int(payload, "table_id")
            if self._service.get_table(table_id, ai_access=actor_access) is None:
                return {"status": "not_found", "table_id": table_id, "results": [], "reason": "not_found"}
            items = self._service.list_reminders(
                ReminderQuery(
                    query=_optional_str(payload, "query", ""),
                    table_id=table_id,
                    lifecycle=_optional_str_or_none(payload, "lifecycle"),
                    date_from=_optional_str_or_none(payload, "date_from"),
                    date_to=_optional_str_or_none(payload, "date_to"),
                    ai_access=actor_access,
                )
            )
            return {"status": "ok", "table_id": table_id, "results": [_item_payload(item) for item in items], "reason": ""}
        if command == "get_reminder_item":
            _require_keys(payload, {"item_id"}, required={"item_id"})
            item_id = _int(payload, "item_id")
            item = self._service.get_reminder(item_id, ai_access=actor_access)
            if item is None:
                return {"status": "not_found", "item_id": item_id, "reason": "not_found"}
            return {"status": "ok", "item_id": item_id, "item": _item_payload(item), "reason": ""}

        if credential.role == ACTOR_ROLE:
            return self._dispatch_actor_write(command, actor, payload)
        return self._dispatch_owner_shared_write(command, payload)

    def _dispatch_actor_write(self, command: str, actor: str, payload: dict) -> dict:
        if command == "create_reminder_label":
            _require_keys(payload, {"request_id", "name"}, required={"request_id", "name"})
            return self._service.ai_create_label(
                actor_target_id=actor,
                request_id=_str(payload, "request_id"),
                name=_str(payload, "name"),
            )
        if command == "create_reminder_table":
            _require_keys(payload, {"request_id", "title", "label_id"}, required={"request_id"})
            return self._service.ai_create_table(
                actor_target_id=actor,
                request_id=_str(payload, "request_id"),
                title=_optional_str_or_none(payload, "title"),
                label_id=_optional_int(payload, "label_id"),
            )
        if command == "update_reminder_table":
            _require_keys(payload, {"request_id", "table_id", "title", "label_id"}, required={"request_id", "table_id"})
            return self._service.ai_update_table_metadata(
                actor_target_id=actor,
                request_id=_str(payload, "request_id"),
                table_id=_int(payload, "table_id"),
                title=_optional_str_or_none(payload, "title"),
                label_id=_optional_int(payload, "label_id"),
            )
        if command == "create_reminder_item":
            _require_keys(
                payload,
                {"request_id", "table_id", "title", "action_spec", "deadline_spec", "note", "lifecycle"},
                required={"request_id", "table_id", "title", "action_spec"},
            )
            table_id = _int(payload, "table_id")
            return self._service.ai_create_item(
                actor_target_id=actor,
                request_id=_str(payload, "request_id"),
                table_id=table_id,
                draft=_draft(payload, table_id=table_id),
            )
        if command == "update_reminder_item":
            _require_keys(
                payload,
                {"request_id", "item_id", "title", "note", "action_spec", "deadline_spec", "clear_deadline", "lifecycle", "multi_notify"},
                required={"request_id", "item_id"},
            )
            return self._service.ai_update_item(
                actor_target_id=actor,
                request_id=_str(payload, "request_id"),
                item_id=_int(payload, "item_id"),
                title=_optional_str_or_none(payload, "title"),
                note=_optional_str_or_none(payload, "note"),
                action_spec=_optional_dict(payload, "action_spec"),
                deadline_spec=_optional_dict(payload, "deadline_spec"),
                clear_deadline=_optional_bool(payload, "clear_deadline", False),
                lifecycle=_optional_str_or_none(payload, "lifecycle"),
                multi_notify=_optional_bool_or_none(payload, "multi_notify"),
            )
        _require_keys(payload, {"request_id", "item_id", "current_occurrence_only"}, required={"request_id", "item_id"})
        common = {
            "actor_target_id": actor,
            "request_id": _str(payload, "request_id"),
            "item_id": _int(payload, "item_id"),
        }
        if command == "complete_reminder_item":
            return self._service.ai_complete_item(
                **common,
                current_occurrence_only=_optional_bool(payload, "current_occurrence_only", True),
            )
        if command == "pause_reminder_item":
            return self._service.ai_pause_item(**common)
        if command == "resume_reminder_item":
            return self._service.ai_resume_item(**common)
        raise ValueError("unsupported_command")

    def _dispatch_owner_shared_write(self, command: str, payload: dict) -> dict:
        if command == "create_reminder_label":
            _require_keys(payload, {"request_id", "name"}, required={"name"})
            label = self._service.create_label(_str(payload, "name"))
            return {"status": "created", "label": _label_payload(label)}
        if command == "create_reminder_table":
            _require_keys(payload, {"request_id", "title", "label_id"})
            table = self._service.create_table(
                _optional_str_or_none(payload, "title"),
                label_id=_optional_int(payload, "label_id"),
            )
            return {"status": "created", "table_id": table.id}
        if command == "update_reminder_table":
            _require_keys(payload, {"request_id", "table_id", "title", "label_id"}, required={"table_id"})
            table = self._service.update_table_metadata(
                _int(payload, "table_id"),
                title=_optional_str_or_none(payload, "title"),
                label_id=_optional_int(payload, "label_id"),
            )
            return {"status": "updated", "table_id": table.id}
        if command == "create_reminder_item":
            _require_keys(
                payload,
                {"request_id", "table_id", "title", "action_spec", "deadline_spec", "note", "lifecycle", "multi_notify", "recurrence_mode"},
                required={"table_id", "title", "action_spec"},
            )
            item = self._service.create_reminder(_draft(payload, table_id=_int(payload, "table_id")))
            return {"status": "created", "item_id": item.id, "reminder_id": item.id}
        if command == "update_reminder_item":
            _require_keys(
                payload,
                {"request_id", "item_id", "table_id", "title", "action_spec", "deadline_spec", "note", "lifecycle", "multi_notify", "recurrence_mode"},
                required={"item_id", "table_id", "title", "action_spec"},
            )
            item = self._service.update_reminder(
                _int(payload, "item_id"), _draft(payload, table_id=_int(payload, "table_id"))
            )
            return {"status": "updated", "item_id": item.id, "reminder_id": item.id}
        _require_keys(payload, {"request_id", "item_id", "current_occurrence_only"}, required={"item_id"})
        item_id = _int(payload, "item_id")
        if command == "complete_reminder_item":
            if _optional_bool(payload, "current_occurrence_only", True):
                self._service.complete_occurrence(item_id)
            else:
                self._service.complete_reminder(item_id)
        elif command == "pause_reminder_item":
            self._service.pause_reminder(item_id)
        elif command == "resume_reminder_item":
            self._service.resume_reminder(item_id)
        else:
            raise ValueError("unsupported_command")
        return {"status": "updated", "item_id": item_id, "reminder_id": item_id}

    def _dispatch_owner(self, command: str, payload: dict) -> dict:
        if command == "ensure_default_table":
            _require_keys(payload, set())
            table = self._service.default_table()
            item = self._service.get_table(table.id)
            if item is None:
                raise RuntimeError("default_reminder_table_missing")
            return {"status": "ok", "table": _table_payload(item)}
        item_id_commands = {
            "delete_reminder": self._service.delete_reminder,
            "complete_occurrence": self._service.complete_occurrence,
            "complete_reminder": self._service.complete_reminder,
            "pause_reminder": self._service.pause_reminder,
            "resume_reminder": self._service.resume_reminder,
        }
        if command in item_id_commands:
            _require_keys(payload, {"item_id"}, required={"item_id"})
            item_id = _int(payload, "item_id")
            item_id_commands[command](item_id)
            return {"status": "updated", "item_id": item_id}
        if command in {"create_reminder", "update_reminder"}:
            allowed = {"item_id", "table_id", "title", "lifecycle", "action_spec", "deadline_spec", "note", "multi_notify", "recurrence_mode"}
            required = {"table_id", "title", "action_spec"}
            if command == "update_reminder":
                required.add("item_id")
            _require_keys(payload, allowed, required=required)
            draft = _draft(payload, table_id=_int(payload, "table_id"))
            item = (
                self._service.create_reminder(draft)
                if command == "create_reminder"
                else self._service.update_reminder(_int(payload, "item_id"), draft)
            )
            return {"status": "created" if command == "create_reminder" else "updated", "item_id": item.id}
        if command == "create_label":
            _require_keys(payload, {"name"}, required={"name"})
            return {"status": "created", "label": _label_payload(self._service.create_label(_str(payload, "name")))}
        if command == "rename_label":
            _require_keys(payload, {"label_id", "name"}, required={"label_id", "name"})
            return {"status": "updated", "label": _label_payload(self._service.rename_label(_int(payload, "label_id"), _str(payload, "name")))}
        if command == "reorder_labels":
            _require_keys(payload, {"label_ids"}, required={"label_ids"})
            label_ids = _int_list(payload, "label_ids")
            return {"status": "updated", "labels": [_label_payload(item) for item in self._service.reorder_labels(label_ids)]}
        if command == "delete_label":
            _require_keys(payload, {"label_id"}, required={"label_id"})
            self._service.delete_label(_int(payload, "label_id"))
            return {"status": "updated"}
        if command == "create_table":
            _require_keys(payload, {"title", "label_id", "ai_visible"})
            item = self._service.create_table(
                _optional_str_or_none(payload, "title"),
                label_id=_optional_int(payload, "label_id"),
                ai_visible=_optional_bool(payload, "ai_visible", True),
            )
            return {"status": "created", "table_id": item.id}
        if command == "update_table_metadata":
            _require_keys(payload, {"table_id", "title", "label_id"}, required={"table_id"})
            item = self._service.update_table_metadata(
                _int(payload, "table_id"),
                title=_optional_str_or_none(payload, "title"),
                label_id=_optional_int(payload, "label_id"),
            )
            return {"status": "updated", "table_id": item.id}
        if command == "set_table_ai_visible":
            _require_keys(payload, {"table_id", "ai_visible"}, required={"table_id", "ai_visible"})
            item = self._service.set_table_ai_visible(_int(payload, "table_id"), _bool(payload, "ai_visible"))
            return {"status": "updated", "table_id": item.id, "ai_visible": item.ai_visible}
        table_commands = {
            "delete_table": self._service.delete_table,
            "restore_table": self._service.restore_table,
            "permanently_delete_table": self._service.permanently_delete_table,
        }
        if command in table_commands:
            _require_keys(payload, {"table_id"}, required={"table_id"})
            table_id = _int(payload, "table_id")
            table_commands[command](table_id)
            return {"status": "updated", "table_id": table_id}
        if command == "save_settings":
            _require_keys(payload, {"notifications_enabled", "daily_time", "ntfy_topic"})
            existing = self._service.settings()
            topic = _optional_str(payload, "ntfy_topic", STAGING_TOPIC)
            if topic != STAGING_TOPIC:
                raise ValueError("staging_topic_required")
            daily_time = _optional_str(payload, "daily_time", existing.daily_time)
            _validate_daily_time(daily_time)
            self._service.save_settings(
                ReminderSettings(
                    _optional_bool(payload, "notifications_enabled", existing.notifications_enabled),
                    daily_time,
                    existing.ntfy_endpoint,
                    STAGING_TOPIC,
                )
            )
            return {"status": "updated"}
        if command == "list_ai_operation_logs":
            _require_keys(payload, {"offset", "limit"})
            offset = _optional_integer(payload, "offset", 0)
            limit = _optional_integer(payload, "limit", 50)
            if offset < 0 or limit < 1 or limit > 200:
                raise ValueError("invalid_operation_log_page")
            page = self._service.list_ai_operation_logs(offset=offset, limit=limit)
            return {
                "status": "ok",
                "total": page.total,
                "offset": page.offset,
                "limit": page.limit,
                "has_more": page.has_more,
                "results": [
                    {
                        "id": row.id,
                        "happened_at": row.happened_at,
                        "actor_target_id": row.actor_target_id,
                        "action": row.action,
                        "table_id": row.table_id,
                        "table_title": row.table_title,
                        "item_id": row.item_id,
                        "item_title": row.item_title,
                        "label_id": row.label_id,
                        "label_name": row.label_name,
                        "request_id": row.request_id,
                        "changed_fields": list(row.changed_fields),
                        "before_json": row.before_json,
                        "after_json": row.after_json,
                    }
                    for row in page.rows
                ],
            }
        if command == "send_test_notification":
            _require_keys(payload, set())
            self._service.send_test_notification()
            return {"status": "sent"}
        raise ValueError("unsupported_command")


class _ReminderHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_handler(
    application: ReminderHttpApplication,
    *,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> type[BaseHTTPRequestHandler]:
    if max_body_bytes < 2:
        raise ValueError("max_body_bytes_too_small")
    if request_timeout <= 0:
        raise ValueError("request_timeout_must_be_positive")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(request_timeout)

        def do_GET(self) -> None:  # noqa: N802
            self._serve(b"")

        def do_POST(self) -> None:  # noqa: N802
            if self.headers.get("Transfer-Encoding") is not None:
                self._write(_error(400, "transfer_encoding_not_supported"))
                return
            lengths = self.headers.get_all("Content-Length", failobj=[])
            if len(lengths) != 1:
                self._write(_error(411, "content_length_required"))
                return
            try:
                length = int(lengths[0], 10)
            except ValueError:
                self._write(_error(400, "content_length_invalid"))
                return
            if length < 0:
                self._write(_error(400, "content_length_invalid"))
                return
            if length > max_body_bytes:
                self._write(_error(413, "request_body_too_large"))
                return
            try:
                body = self.rfile.read(length)
            except (TimeoutError, socket.timeout):
                self.close_connection = True
                self._write(_error(408, "request_timeout"))
                return
            if len(body) != length:
                self.close_connection = True
                self._write(_error(400, "request_body_incomplete"))
                return
            self._serve(body)

        def _serve(self, body: bytes) -> None:
            response = application.handle(
                self.command,
                self.path,
                headers={key: value for key, value in self.headers.items()},
                body=body,
            )
            self._write(response)

        def _write(self, response: ReminderHttpResponse) -> None:
            encoded = json.dumps(
                response.body, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            self.send_response(response.status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for key, value in response.headers:
                self.send_header(key, value)
            self.end_headers()
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return Handler


def create_server(
    application: ReminderHttpApplication,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("reminder_http_must_bind_loopback")
    return _ReminderHttpServer(
        (host, port),
        make_handler(
            application,
            max_body_bytes=max_body_bytes,
            request_timeout=request_timeout,
        ),
    )


def serve(
    application: ReminderHttpApplication,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> None:
    with create_server(
        application,
        host=host,
        port=port,
        max_body_bytes=max_body_bytes,
        request_timeout=request_timeout,
    ) as server:
        server.serve_forever()


def _decode_json_object(body: bytes) -> tuple[dict | None, ReminderHttpResponse | None]:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, _error(400, "invalid_json")
    if not isinstance(value, dict):
        return None, _error(400, "json_object_required")
    return value, None


def _error(status: int, code: str) -> ReminderHttpResponse:
    return ReminderHttpResponse(status, {"error": code})


def _header(headers: Mapping[str, str], name: str) -> str | None:
    target = name.casefold()
    for key, value in headers.items():
        if key.casefold() == target:
            return value
    return None


def _require_keys(payload: dict, allowed: set[str], *, required: set[str] = set()) -> None:
    unknown = set(payload) - allowed
    missing = required - set(payload)
    if unknown:
        raise ValueError(f"unknown_fields:{','.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"missing_fields:{','.join(sorted(missing))}")


def _str(payload: dict, key: str) -> str:
    value = payload[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key}_must_be_nonempty_string")
    return value


def _optional_str(payload: dict, key: str, default: str) -> str:
    if key not in payload:
        return default
    value = payload[key]
    if not isinstance(value, str):
        raise ValueError(f"{key}_must_be_string")
    return value


def _optional_str_or_none(payload: dict, key: str) -> str | None:
    if key not in payload or payload[key] is None:
        return None
    if not isinstance(payload[key], str):
        raise ValueError(f"{key}_must_be_string")
    return payload[key]


def _int(payload: dict, key: str) -> int:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key}_must_be_integer")
    return value


def _optional_int(payload: dict, key: str) -> int | None:
    if key not in payload or payload[key] is None:
        return None
    return _int(payload, key)


def _optional_integer(payload: dict, key: str, default: int) -> int:
    return default if key not in payload else _int(payload, key)


def _bool(payload: dict, key: str) -> bool:
    value = payload[key]
    if not isinstance(value, bool):
        raise ValueError(f"{key}_must_be_boolean")
    return value


def _optional_bool(payload: dict, key: str, default: bool) -> bool:
    return default if key not in payload else _bool(payload, key)


def _optional_bool_or_none(payload: dict, key: str) -> bool | None:
    return None if key not in payload or payload[key] is None else _bool(payload, key)


def _optional_dict(payload: dict, key: str) -> dict | None:
    if key not in payload or payload[key] is None:
        return None
    value = payload[key]
    if not isinstance(value, dict):
        raise ValueError(f"{key}_must_be_object")
    return value


def _int_list(payload: dict, key: str) -> list[int]:
    values = payload[key]
    if not isinstance(values, list) or any(
        isinstance(value, bool) or not isinstance(value, int) for value in values
    ):
        raise ValueError(f"{key}_must_be_integer_array")
    return values


def _validate_daily_time(value: str) -> None:
    parts = value.split(":")
    if (
        len(parts) != 2
        or len(parts[0]) != 2
        or len(parts[1]) != 2
        or not all(part.isascii() and part.isdigit() for part in parts)
    ):
        raise ValueError("daily_time_must_be_hh_mm")
    hour, minute = (int(part) for part in parts)
    if not 0 <= hour < 24 or not 0 <= minute < 60:
        raise ValueError("daily_time_must_be_hh_mm")


def _draft(payload: dict, *, table_id: int) -> ReminderDraft:
    action_spec = payload["action_spec"]
    if not isinstance(action_spec, dict):
        raise ValueError("action_spec_must_be_object")
    deadline_spec = payload.get("deadline_spec")
    if deadline_spec is not None and not isinstance(deadline_spec, dict):
        raise ValueError("deadline_spec_must_be_object")
    return ReminderDraft(
        title=_str(payload, "title"),
        lifecycle=_optional_str(payload, "lifecycle", REMINDER_ACTIVE),
        action_spec=action_spec,
        deadline_spec=deadline_spec,
        note=_optional_str(payload, "note", ""),
        table_id=table_id,
        multi_notify=_optional_bool(payload, "multi_notify", False),
        recurrence_mode=_optional_str_or_none(payload, "recurrence_mode"),
    )


def _label_payload(label) -> dict:
    return {
        "label_id": label.id,
        "name": label.name,
        "is_default": label.is_default,
        "sort_order": label.sort_order,
    }


def _table_payload(item) -> dict:
    return {
        "table_id": item.table.id,
        "title": item.table.title,
        "label": {"label_id": item.label.id, "name": item.label.name},
        "ai_visible": item.table.ai_visible,
        "item_count": item.item_count,
        "matching_item_id": item.matching_item_id,
        "match_count": item.match_count,
        "updated_at": item.table.updated_at,
    }


def _item_payload(item) -> dict:
    occurrence = item.occurrence
    return {
        "item_id": item.reminder.id,
        "reminder_id": item.reminder.id,
        "table_id": item.table.id,
        "title": item.reminder.title,
        "note": item.reminder.note,
        "label": {"label_id": item.label.id, "name": item.label.name},
        "lifecycle": item.reminder.lifecycle,
        "status": item.status,
        "status_label": item.status_label,
        "action_spec": item.reminder.action_spec,
        "deadline_spec": item.reminder.deadline_spec,
        "multi_notify": item.reminder.multi_notify,
        "recurrence_mode": item.reminder.effective_recurrence_mode,
        "next_action_date": item.next_action_date,
        "next_deadline_date": item.next_deadline_date,
        "updated_at": item.reminder.updated_at,
        "occurrence": (
            {
                "occurrence_id": occurrence.id,
                "occurrence_key": occurrence.occurrence_key,
                "action_date": occurrence.action_date,
                "deadline_date": occurrence.deadline_date,
                "status": occurrence.status,
                "completed_at": occurrence.completed_at,
            }
            if occurrence is not None
            else None
        ),
    }
