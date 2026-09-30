"""Authenticated HTTP client for the Cloud Reminder command boundary."""

from __future__ import annotations

import json
import re
import socket
import ssl
import certifi
import threading
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from src.snotes.storage.schema import REMINDER_SCHEMA_VERSION


DEFAULT_TIMEOUT_SECONDS = 4.0
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
ERROR_KINDS = frozenset({"offline", "auth", "schema_mismatch", "server"})
_COMMAND_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class RemoteReminderError(RuntimeError):
    """Stable client error classification for GUI and MCP adapters."""

    def __init__(
        self,
        kind: str,
        *,
        code: str | None = None,
        status: int | None = None,
    ) -> None:
        if kind not in ERROR_KINDS:
            raise ValueError("unsupported_remote_reminder_error_kind")
        self.kind = kind
        self.code = code
        self.status = status
        super().__init__(kind if code is None else f"{kind}:{code}")


class _RejectRedirects(HTTPRedirectHandler):
    """Keep bearer credentials on their configured origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class RemoteReminderClient:
    """Small transport client; callers own threading, caching, and UI state."""

    def __init__(
        self,
        endpoint: str,
        credential_provider: Callable[[], str],
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        supported_schema_version: int = int(REMINDER_SCHEMA_VERSION),
    ) -> None:
        clean_endpoint = endpoint.strip().rstrip("/")
        parsed = urlsplit(clean_endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("remote_reminder_endpoint_invalid")
        if parsed.scheme == "http" and parsed.hostname.casefold() not in {
            "127.0.0.1",
            "::1",
            "localhost",
        }:
            raise ValueError("remote_reminder_https_endpoint_required")
        if not callable(credential_provider):
            raise TypeError("remote_reminder_credential_provider_required")
        timeout = float(timeout_seconds)
        if timeout <= 0:
            raise ValueError("remote_reminder_timeout_must_be_positive")
        if (
            isinstance(supported_schema_version, bool)
            or int(supported_schema_version) < 1
        ):
            raise ValueError("remote_reminder_schema_version_invalid")

        self.endpoint = clean_endpoint
        self.timeout_seconds = timeout
        self.supported_schema_version = int(supported_schema_version)
        self._credential_provider = credential_provider
        self._opener = build_opener(_RejectRedirects(), HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where())))
        self._state_lock = threading.Lock()
        self._schema_incompatible = False

    def snapshot(self) -> dict:
        payload = self._request("GET", "/v1/reminder/snapshot")
        version = payload.get("schema_version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise RemoteReminderError("server", code="schema_version_invalid")
        incompatible = version > self.supported_schema_version
        with self._state_lock:
            self._schema_incompatible = incompatible
        if incompatible:
            raise RemoteReminderError(
                "schema_mismatch", code=f"unsupported_schema_version:{version}"
            )
        return payload

    def command(self, name: str, payload: dict) -> dict:
        if not isinstance(name, str) or _COMMAND_PATTERN.fullmatch(name) is None:
            raise ValueError("remote_reminder_command_invalid")
        if not isinstance(payload, dict):
            raise TypeError("remote_reminder_command_payload_must_be_object")

        # A command always checks the server schema first. This GET is strictly
        # read-only, and prevents a newly upgraded server from receiving a write
        # from an older client even when an earlier snapshot was compatible.
        self.snapshot()
        with self._state_lock:
            if self._schema_incompatible:
                raise RemoteReminderError("schema_mismatch")
        return self._request(
            "POST",
            f"/v1/reminder/commands/{name}",
            payload=payload,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
    ) -> dict:
        token = self._credential()
        data = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        if payload is not None:
            data = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = Request(
            f"{self.endpoint}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                status = int(response.status)
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            code = _http_error_code(error)
            kind = "auth" if error.code in {401, 403} else "server"
            raise RemoteReminderError(kind, code=code, status=error.code) from error
        except (URLError, TimeoutError, socket.timeout, OSError) as error:
            raise RemoteReminderError("offline", code="unreachable") from error

        if status in {401, 403}:
            raise RemoteReminderError("auth", status=status)
        if status < 200 or status >= 300:
            raise RemoteReminderError("server", status=status)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise RemoteReminderError("server", code="response_too_large")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RemoteReminderError("server", code="response_invalid") from error
        if not isinstance(decoded, dict):
            raise RemoteReminderError("server", code="response_invalid")
        return decoded

    def _credential(self) -> str:
        try:
            token = self._credential_provider()
        except RemoteReminderError:
            raise
        except Exception as error:
            raise RemoteReminderError("auth", code="credential_unavailable") from error
        if (
            not isinstance(token, str)
            or not token
            or any(character.isspace() for character in token)
        ):
            raise RemoteReminderError("auth", code="credential_unavailable")
        return token


def _http_error_code(error: HTTPError) -> str | None:
    try:
        raw = error.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            return "response_too_large"
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    value = payload.get("error") if isinstance(payload, dict) else None
    return value if isinstance(value, str) and value else None
