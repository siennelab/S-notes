"""Authenticated localhost HTTP boundary for GreenCloud Notes proposals."""

from __future__ import annotations

import hmac
import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlsplit

from src.snotes.application.cloud_proposal_service import CloudProposalService
from src.services.gale_submission_outbox import (
    GaleSubmissionOutboxError,
    enqueue_proposal,
    get_operation_status,
)
from src.snotes.storage.cloud_read import SQLiteCloudMirrorReader


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8767
DEFAULT_MAX_BODY_BYTES = 640 * 1024
ALLOWED_ACTORS = frozenset({"claude-code", "clawbot"})
ALLOWED_OPERATIONS = frozenset({"create", "append", "edit"})
IDENTITY_FIELDS = frozenset(
    {"actor", "source_actor", "actor_id", "actor_target_id", "identity", "principal", "role"}
)


@dataclass(frozen=True, slots=True)
class CloudOutboxResponse:
    status: int
    body: dict
    headers: tuple[tuple[str, str], ...] = ()


class CloudOutboxHttpApplication:
    """Fix source_actor from the authenticated credential before DB access."""

    def __init__(
        self,
        database_path: Path,
        credentials: Mapping[str, str],
        *,
        reader_factory: Callable[[str], object] | None = None,
    ) -> None:
        normalized: dict[str, str] = {}
        for token, actor in credentials.items():
            if (
                not isinstance(token, str)
                or not token
                or any(character.isspace() for character in token)
                or actor not in ALLOWED_ACTORS
            ):
                raise ValueError("cloud_outbox_credentials_invalid")
            normalized[token] = actor
        if set(normalized.values()) != ALLOWED_ACTORS:
            raise ValueError("cloud_outbox_credentials_incomplete")
        self._database_path = Path(database_path)
        self._credentials = normalized
        self._reader_factory = reader_factory or _production_reader
        self._request_lock = threading.RLock()

    def handle(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes = b"",
    ) -> CloudOutboxResponse:
        with self._request_lock:
            try:
                return self._handle(method, path, headers=headers or {}, body=body)
            except Exception:
                return _error(500, "internal_error")

    def _handle(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
    ) -> CloudOutboxResponse:
        actor, auth_error = self._authenticate(headers)
        if auth_error is not None:
            return auth_error
        assert actor is not None
        parsed = urlsplit(path)
        if parsed.query or parsed.fragment:
            return _error(400, "query_not_supported")
        if method != "POST":
            return _error(404, "not_found")
        if parsed.path not in {
            "/v1/notes/proposals/enqueue",
            "/v1/notes/proposals/status",
        }:
            return _error(404, "not_found")
        payload, failure = _decode_json_object(body)
        if failure is not None:
            return failure
        assert payload is not None
        if IDENTITY_FIELDS.intersection(payload):
            return _error(400, "identity_in_body_forbidden")
        try:
            if parsed.path.endswith("/status"):
                if set(payload) != {"request_id"}:
                    raise ValueError("request_invalid")
                service = self._proposal_service(actor)
                return CloudOutboxResponse(
                    200, service.get_operation_status(payload["request_id"])
                )

            required = {
                "request_id",
                "operation_type",
                "note_id",
                "expected_version",
                "payload",
            }
            if set(payload) != required:
                raise ValueError("request_invalid")
            operation_type = payload["operation_type"]
            if operation_type not in ALLOWED_OPERATIONS:
                return _error(403, "operation_not_allowed")
            operation_payload = payload["payload"]
            if not isinstance(operation_payload, dict):
                raise ValueError("payload_invalid")
            if IDENTITY_FIELDS.intersection(operation_payload):
                return _error(400, "identity_in_body_forbidden")
            service = self._proposal_service(actor)
            request_id = payload["request_id"]
            if operation_type == "create":
                if payload["note_id"] is not None or payload["expected_version"] is not None:
                    raise ValueError("request_invalid")
                allowed = {
                    "title", "content", "directory_id", "main_category",
                    "sub_category", "tags",
                }
                if set(operation_payload) != allowed:
                    raise ValueError("payload_invalid")
                result = service.create_note(
                    request_id=request_id,
                    title=operation_payload["title"],
                    content=operation_payload["content"],
                    directory_id=operation_payload["directory_id"],
                    main_category=operation_payload["main_category"],
                    sub_category=operation_payload["sub_category"],
                    tags=operation_payload["tags"],
                )
            elif operation_type == "append":
                if set(operation_payload) != {"content"}:
                    raise ValueError("payload_invalid")
                result = service.append_note(
                    request_id=request_id,
                    note_id=payload["note_id"],
                    content=operation_payload["content"],
                    expected_version=payload["expected_version"],
                )
            else:
                if set(operation_payload) != {"edits"}:
                    raise ValueError("payload_invalid")
                result = service.edit_note(
                    request_id=request_id,
                    note_id=payload["note_id"],
                    expected_version=payload["expected_version"],
                    edits=operation_payload["edits"],
                )
            return CloudOutboxResponse(200, result)
        except (KeyError, TypeError, ValueError) as error:
            return _error(400, str(error) or "invalid_request")
        except GaleSubmissionOutboxError as error:
            code = error.code
            return _error(429 if code == "queue_full" else 400, code)

    def _authenticate(
        self, headers: Mapping[str, str]
    ) -> tuple[str | None, CloudOutboxResponse | None]:
        authorization = _header(headers, "authorization")
        if authorization is None:
            return None, CloudOutboxResponse(
                401,
                {"error": "credential_required"},
                (("WWW-Authenticate", "Bearer"),),
            )
        scheme, separator, token = authorization.partition(" ")
        if separator != " " or scheme.casefold() != "bearer" or not token:
            return None, _error(403, "credential_invalid")
        actor: str | None = None
        token_bytes = token.encode("utf-8")
        for candidate, candidate_actor in self._credentials.items():
            if hmac.compare_digest(token_bytes, candidate.encode("utf-8")):
                actor = candidate_actor
        if actor is None:
            return None, _error(403, "credential_invalid")
        return actor, None

    def _proposal_service(self, actor: str) -> CloudProposalService:
        return CloudProposalService(
            self._reader_factory(actor),
            _ActorOutboxGateway(self._database_path, actor),
        )


class _ActorOutboxGateway:
    def __init__(self, database_path: Path, actor: str) -> None:
        self._database_path = database_path
        self._actor = actor

    def get_operation_status(self, request_id: str) -> dict | None:
        try:
            proposal = get_operation_status(
                self._database_path,
                source_actor=self._actor,
                request_id=request_id,
            )
        except GaleSubmissionOutboxError as error:
            raise RuntimeError(error.code) from error
        return None if proposal is None else _status_payload(proposal)

    def enqueue_proposal(
        self,
        *,
        request_id: str,
        operation_type: str,
        note_id: int | None,
        expected_version: str | None,
        payload: dict,
    ) -> dict:
        try:
            proposal, duplicate = enqueue_proposal(
                self._database_path,
                source_actor=self._actor,
                request_id=request_id,
                operation_type=operation_type,
                note_id=note_id,
                expected_version=expected_version,
                payload=payload,
                queue_limit=50,
            )
        except GaleSubmissionOutboxError as error:
            raise RuntimeError(error.code) from error
        return _status_payload(proposal, duplicate=duplicate)


def serve_cloud_outbox(
    application: CloudOutboxHttpApplication,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> None:
    class Handler(BaseHTTPRequestHandler):
        server_version = "SNotesCloudOutbox/1"

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch()

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch()

        def _dispatch(self) -> None:
            self.connection.settimeout(10.0)
            length = self.headers.get("Content-Length")
            try:
                size = int(length or "0")
            except ValueError:
                self._write(_error(400, "content_length_invalid"))
                return
            if size < 0 or size > max_body_bytes:
                self._write(_error(413, "request_too_large"))
                return
            body = self.rfile.read(size) if size else b""
            response = application.handle(
                self.command,
                self.path,
                headers=dict(self.headers.items()),
                body=body,
            )
            self._write(response)

        def _write(self, response: CloudOutboxResponse) -> None:
            encoded = json.dumps(
                response.body, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            self.send_response(response.status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            for name, value in response.headers:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer((host, int(port)), Handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _status_payload(proposal, *, duplicate: bool = False) -> dict:
    payload = proposal.operation_status_payload(duplicate=duplicate)
    note_id = payload.get("note_id")
    if isinstance(note_id, str) and note_id.isdecimal():
        payload["note_id"] = int(note_id)
    return {"status": payload["delivery_state"], **payload}


def _production_reader(actor: str) -> SQLiteCloudMirrorReader:
    if actor not in ALLOWED_ACTORS:
        raise RuntimeError("unsupported_actor")
    return SQLiteCloudMirrorReader(Path(f"/srv/snotes/mirror/{actor}/current.db"))


def _decode_json_object(
    body: bytes,
) -> tuple[dict | None, CloudOutboxResponse | None]:
    if not body:
        return None, _error(400, "request_body_required")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, _error(400, "invalid_json")
    if not isinstance(payload, dict):
        return None, _error(400, "request_object_required")
    return payload, None


def _header(headers: Mapping[str, str], name: str) -> str | None:
    expected = name.casefold()
    for key, value in headers.items():
        if key.casefold() == expected:
            return value
    return None


def _error(status: int, reason: str) -> CloudOutboxResponse:
    return CloudOutboxResponse(status, {"error": reason})
