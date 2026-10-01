"""Authenticated HTTP client for the Cloud Reminder command boundary."""

from __future__ import annotations

import json
import re
import ssl
import threading
from collections.abc import Callable
from urllib.parse import urlsplit

import certifi
import httpx

from src.snotes.domain.reminder_remote_contract import (
    OUTCOME_FAILED,
    OUTCOME_UNKNOWN,
    REMINDER_WIRE_SCHEMA_VERSION,
    REMOTE_ERROR_KINDS as ERROR_KINDS,
    RemoteReminderError,
)


DEFAULT_TIMEOUT_SECONDS = 4.0
# An idle connection is kept below the server's idle timeout, so consecutive
# operations skip a new TCP/TLS setup; it was verified reusable after 120s.
KEEPALIVE_SECONDS = 120.0
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_COMMAND_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
# Failures raised before a request can reach the server; any other transport
# failure after a write was dispatched leaves its Cloud outcome unknown.
_NOT_SENT_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.UnsupportedProtocol,
    httpx.LocalProtocolError,
)

__all__ = ["ERROR_KINDS", "RemoteReminderClient", "RemoteReminderError"]


class RemoteReminderClient:
    """Small transport client; callers own threading, caching, and UI state."""

    def __init__(
        self,
        endpoint: str,
        credential_provider: Callable[[], str],
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        supported_schema_version: int = REMINDER_WIRE_SCHEMA_VERSION,
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
        self._ssl_context = ssl.create_default_context(cafile=certifi.where())
        self._state_lock = threading.Lock()
        self._schema_incompatible = False
        self._http_client: httpx.Client | None = None

    def close(self) -> None:
        """Close pooled connections; a later request opens a new pool."""
        with self._state_lock:
            client, self._http_client = self._http_client, None
        if client is not None:
            client.close()

    def _http(self) -> httpx.Client:
        # One thread-safe pool for the client lifetime: a mutation, its
        # authoritative refresh, and the next operation share a warm connection.
        with self._state_lock:
            if self._http_client is None:
                self._http_client = self._new_http_client()
            return self._http_client

    def _new_http_client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.endpoint,
            verify=self._ssl_context,
            timeout=httpx.Timeout(self.timeout_seconds),
            limits=httpx.Limits(keepalive_expiry=KEEPALIVE_SECONDS),
            follow_redirects=False,
            headers={"Accept-Encoding": "identity"},
        )

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
        write = method != "GET"
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
        try:
            try:
                status, raw, response_too_large = _exchange(
                    self._http(), method, path, data, headers
                )
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError):
                # A pooled keep-alive connection can be closed by the network
                # path while idle. Only a read-only GET is resent, once; a write
                # is never retried. Timeouts are not retried either.
                if write:
                    raise
                status, raw, response_too_large = _exchange(
                    self._http(), method, path, data, headers
                )
        except httpx.RequestError as error:
            outcome = (
                OUTCOME_UNKNOWN
                if write and not isinstance(error, _NOT_SENT_ERRORS)
                else OUTCOME_FAILED
            )
            raise RemoteReminderError(
                "offline", code="unreachable", outcome=outcome
            ) from error

        # A 2xx write was executed; a 5xx write may have been. A 4xx response is
        # a definite rejection.
        succeeded = 200 <= status < 300
        failure_outcome = (
            OUTCOME_UNKNOWN if write and (succeeded or status >= 500) else OUTCOME_FAILED
        )
        if response_too_large:
            kind = "auth" if status in {401, 403} else "server"
            raise RemoteReminderError(
                kind,
                code="response_too_large",
                status=None if succeeded else status,
                outcome=failure_outcome,
            )
        if status in {401, 403}:
            raise RemoteReminderError(
                "auth", code=_response_error_code(raw), status=status
            )
        if not succeeded:
            raise RemoteReminderError(
                "server",
                code=_response_error_code(raw),
                status=status,
                outcome=failure_outcome,
            )
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RemoteReminderError(
                "server", code="response_invalid", outcome=failure_outcome
            ) from error
        if not isinstance(decoded, dict):
            raise RemoteReminderError(
                "server", code="response_invalid", outcome=failure_outcome
            )
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


def _exchange(
    client: httpx.Client,
    method: str,
    path: str,
    data: bytes | None,
    headers: dict[str, str],
) -> tuple[int, bytes, bool]:
    with client.stream(method, path, content=data, headers=headers) as response:
        raw, response_too_large = _read_response(response)
        return int(response.status_code), raw, response_too_large


def _read_response(response: httpx.Response) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            return b"", True
        chunks.append(chunk)
    return b"".join(chunks), False


def _response_error_code(raw: bytes) -> str | None:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    value = payload.get("error") if isinstance(payload, dict) else None
    return value if isinstance(value, str) and value else None
