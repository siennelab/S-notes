"""Narrow localhost client for the GreenCloud Notes proposal outbox."""

from __future__ import annotations

import json
from http.client import HTTPConnection
from typing import Callable
from urllib.parse import urlsplit


DEFAULT_ENDPOINT = "http://127.0.0.1:8767"


class CloudProposalClient:
    def __init__(
        self,
        credential: Callable[[], str],
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout: float = 10.0,
    ) -> None:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port != 8767
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
        ):
            raise ValueError("cloud_outbox_endpoint_invalid")
        self._credential = credential
        self._timeout = float(timeout)
        self._connection: HTTPConnection | None = None

    def get_operation_status(self, request_id: str) -> dict | None:
        result = self._post(
            "/v1/notes/proposals/status", {"request_id": request_id}
        )
        return None if result.get("status") == "not_found" else result

    def enqueue_proposal(
        self,
        *,
        request_id: str,
        operation_type: str,
        note_id: int | None,
        expected_version: str | None,
        payload: dict,
    ) -> dict:
        return self._post(
            "/v1/notes/proposals/enqueue",
            {
                "request_id": request_id,
                "operation_type": operation_type,
                "note_id": note_id,
                "expected_version": expected_version,
                "payload": payload,
            },
        )

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _post(self, path: str, payload: dict) -> dict:
        token = self._credential()
        if not isinstance(token, str) or not token or any(c.isspace() for c in token):
            raise RuntimeError("cloud_outbox_credential_invalid")
        connection = self._connection
        if connection is None:
            connection = HTTPConnection("127.0.0.1", 8767, timeout=self._timeout)
            self._connection = connection
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        try:
            connection.request(
                "POST",
                path,
                body=encoded,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(encoded)),
                },
            )
            response = connection.getresponse()
            raw = response.read()
        except OSError as error:
            self.close()
            raise RuntimeError("cloud_outbox_unavailable") from error
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            self.close()
            raise RuntimeError("cloud_outbox_response_invalid") from error
        if not isinstance(result, dict):
            raise RuntimeError("cloud_outbox_response_invalid")
        if response.status != 200:
            reason = result.get("error", "cloud_outbox_request_failed")
            raise RuntimeError(str(reason))
        return result
