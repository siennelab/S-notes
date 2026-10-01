from __future__ import annotations

from contextlib import contextmanager
import json
import socket
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from src.snotes.application.reminder_service import ReminderService
from src.snotes.domain.reminders import DEFAULT_REMINDER_TABLE_TITLE, ReminderQuery
from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.transport.reminder_http import (
    ACTOR_ROLE,
    OWNER_ROLE,
    ReminderCredential,
    ReminderHttpApplication,
    create_server,
)
from src.snotes.transport.remote_reminder_client import (
    DEFAULT_TIMEOUT_SECONDS,
    RemoteReminderClient,
    RemoteReminderError,
)


class _QuietThreadingHTTPServer(ThreadingHTTPServer):
    def handle_error(self, _request, _client_address) -> None:
        return


def _serve(handler):
    server = _QuietThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, thread, f"http://{host}:{port}"


def _closing(test: unittest.TestCase, client: RemoteReminderClient) -> RemoteReminderClient:
    test.addCleanup(client.close)
    return client


def _stop_server(server, thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


class Step07DefaultTableContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-step07-contract-")
        connections = initialize_database(Path(self.temporary.name) / "contract.db")
        self.service = ReminderService(SQLiteReminderRepository(connections))
        self.app = ReminderHttpApplication(
            self.service,
            {
                "owner-token": ReminderCredential("owner", OWNER_ROLE),
                "yan-token": ReminderCredential("yan", ACTOR_ROLE),
            },
            clock=lambda: datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _request(
        self,
        token: str,
        method: str,
        path: str,
        payload: dict | None = None,
    ):
        return self.app.handle(
            method,
            path,
            headers={"Authorization": f"Bearer {token}"},
            body=b"" if payload is None else json.dumps(payload).encode("utf-8"),
        )

    def _command(self, token: str, name: str, payload: dict):
        return self._request(
            token, "POST", f"/v1/reminder/commands/{name}", payload
        )

    def test_ensure_default_table_preserves_rename_restores_and_is_idempotent(self) -> None:
        original = self.service.default_table()
        self.service.update_table_metadata(original.id, title="RENAMED DEFAULT")
        self.service.delete_table(original.id)

        first_snapshot = self._request(
            "owner-token", "GET", "/v1/reminder/snapshot"
        )
        second_snapshot = self._request(
            "owner-token", "GET", "/v1/reminder/snapshot"
        )
        self.assertEqual(first_snapshot.body["tables"], [])
        self.assertEqual(second_snapshot.body["tables"], [])
        deleted = self.service.get_table(original.id, include_deleted=True)
        self.assertIsNotNone(deleted)
        self.assertTrue(deleted.table.is_deleted)

        actor = self._command("yan-token", "ensure_default_table", {})
        self.assertEqual(actor.status, 403)
        self.assertTrue(
            self.service.get_table(original.id, include_deleted=True).table.is_deleted
        )

        first = self._command("owner-token", "ensure_default_table", {})
        second = self._command("owner-token", "ensure_default_table", {})
        self.assertEqual(first.status, 200)
        self.assertEqual(first.body["status"], "ok")
        self.assertEqual(first.body["table"]["table_id"], original.id)
        self.assertEqual(first.body["table"]["title"], "RENAMED DEFAULT")
        self.assertEqual(second.body["table"]["table_id"], original.id)
        defaults = [
            item
            for item in self.service.list_tables(
                ReminderQuery(include_deleted_tables=True)
            )
            if item.table.is_default
        ]
        self.assertEqual(len(defaults), 1)
        self.assertFalse(defaults[0].table.is_deleted)

    def test_ensure_default_table_recreates_missing_default_with_existing_semantics(self) -> None:
        original = self.service.default_table()
        self.service.delete_table(original.id)
        self.service.permanently_delete_table(original.id)
        self.assertEqual(self.service.list_tables(), [])

        response = self._command("owner-token", "ensure_default_table", {})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body["table"]["title"], DEFAULT_REMINDER_TABLE_TITLE)
        recreated = self.service.default_table()
        self.assertEqual(response.body["table"]["table_id"], recreated.id)
        self.assertTrue(recreated.is_default)
        self.assertEqual(len(self.service.list_tables()), 1)


class Step07RemoteReminderClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-step07-client-")
        connections = initialize_database(Path(self.temporary.name) / "client.db")
        service = ReminderService(SQLiteReminderRepository(connections))
        application = ReminderHttpApplication(
            service,
            {"owner-token": ReminderCredential("owner", OWNER_ROLE)},
        )
        self.server = create_server(application, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.endpoint = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def test_snapshot_and_command_use_four_second_timeout_and_fresh_credentials(self) -> None:
        calls = []

        def credential() -> str:
            calls.append("called")
            return "owner-token"

        client = _closing(self, RemoteReminderClient(self.endpoint, credential))
        self.assertEqual(client.timeout_seconds, DEFAULT_TIMEOUT_SECONDS)
        self.assertEqual(client.snapshot()["schema_version"], 4)
        ensured = client.command("ensure_default_table", {})
        self.assertEqual(ensured["status"], "ok")
        # One direct snapshot, then command's schema preflight and POST.
        self.assertEqual(len(calls), 3)

    def test_error_kinds_cover_auth_and_server(self) -> None:
        unauthorized = _closing(self, RemoteReminderClient(self.endpoint, lambda: "wrong-token"))
        with self.assertRaises(RemoteReminderError) as auth:
            unauthorized.snapshot()
        self.assertEqual(auth.exception.kind, "auth")
        self.assertEqual(auth.exception.status, 403)

        valid = _closing(self, RemoteReminderClient(self.endpoint, lambda: "owner-token"))
        with self.assertRaises(RemoteReminderError) as server:
            valid.command("not_a_real_command", {})
        self.assertEqual(server.exception.kind, "server")
        self.assertEqual(server.exception.status, 404)
        self.assertEqual(server.exception.code, "unknown_command")

    def test_higher_schema_blocks_post_after_read_only_preflight(self) -> None:
        requests: list[str] = []

        class FutureSchemaHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                requests.append("GET")
                self._write({"schema_version": 999})

            def do_POST(self) -> None:  # noqa: N802
                requests.append("POST")
                self._write({"status": "unexpected"})

            def _write(self, payload: dict) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), FutureSchemaHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            client = _closing(self, RemoteReminderClient(
                f"http://{host}:{port}", lambda: "fictional-token"
            ))
            with self.assertRaises(RemoteReminderError) as mismatch:
                client.command("create_table", {"title": "BLOCKED"})
            self.assertEqual(mismatch.exception.kind, "schema_mismatch")
            self.assertEqual(requests, ["GET"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_operations_reuse_http11_connection_and_keep_fresh_credentials(self) -> None:
        requests: list[tuple[str, int, str | None]] = []

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                requests.append(
                    ("GET", self.client_address[1], self.headers.get("Authorization"))
                )
                self._write({"schema_version": 4})

            def do_POST(self) -> None:  # noqa: N802
                requests.append(
                    ("POST", self.client_address[1], self.headers.get("Authorization"))
                )
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                self._write({"status": "ok"})

            def _write(self, payload: dict) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        server, thread, endpoint = _serve(Handler)
        credentials: list[str] = []
        try:
            client = _closing(self, RemoteReminderClient(
                endpoint,
                lambda: credentials.append("fresh") or "fictional-token",
            ))
            self.assertEqual(client.command("create_reminder", {})["status"], "ok")
            self.assertEqual(client.snapshot()["schema_version"], 4)
            # The next operation reuses the same warm connection.
            self.assertEqual(client.command("pause_reminder", {})["status"], "ok")
            self.assertEqual(
                [method for method, _, _ in requests],
                ["GET", "POST", "GET", "GET", "POST"],
            )
            self.assertEqual(len({port for _, port, _ in requests}), 1)
            self.assertEqual(credentials, ["fresh"] * 5)
            self.assertTrue(
                all(auth == "Bearer fictional-token" for _, _, auth in requests)
            )
        finally:
            _stop_server(server, thread)

    def test_post_connection_drop_is_offline_and_never_retried(self) -> None:
        post_count = 0

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                body = b'{"schema_version":4}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                nonlocal post_count
                post_count += 1
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()

            def log_message(self, _format: str, *_args: object) -> None:
                return

        server, thread, endpoint = _serve(Handler)
        try:
            client = _closing(self, RemoteReminderClient(endpoint, lambda: "fictional-token"))
            with self.assertRaises(RemoteReminderError) as failure:
                client.command("create_reminder", {})
            self.assertEqual(failure.exception.kind, "offline")
            self.assertEqual(post_count, 1)
        finally:
            _stop_server(server, thread)

    def test_real_read_timeout_remains_offline(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                time.sleep(0.2)
                body = b'{"schema_version":4}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        server, thread, endpoint = _serve(Handler)
        try:
            client = _closing(self, RemoteReminderClient(
                endpoint, lambda: "fictional-token", timeout_seconds=0.05
            ))
            with self.assertRaises(RemoteReminderError) as failure:
                client.snapshot()
            self.assertEqual(failure.exception.kind, "offline")
            self.assertEqual(failure.exception.code, "unreachable")
        finally:
            _stop_server(server, thread)

    def test_redirect_is_not_followed_or_sent_the_credential(self) -> None:
        redirected_headers: list[str | None] = []

        class TargetHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                redirected_headers.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, _format: str, *_args: object) -> None:
                return

        target, target_thread, target_endpoint = _serve(TargetHandler)

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self.send_response(302)
                self.send_header("Location", target_endpoint + "/capture")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, _format: str, *_args: object) -> None:
                return

        redirect, redirect_thread, endpoint = _serve(RedirectHandler)
        try:
            client = _closing(self, RemoteReminderClient(endpoint, lambda: "fictional-token"))
            with self.assertRaises(RemoteReminderError) as failure:
                client.snapshot()
            self.assertEqual(failure.exception.kind, "server")
            self.assertEqual(failure.exception.status, 302)
            self.assertEqual(redirected_headers, [])
        finally:
            _stop_server(redirect, redirect_thread)
            _stop_server(target, target_thread)

    def test_shared_pool_is_created_once_and_close_releases_it(self) -> None:
        client = _closing(self, RemoteReminderClient(self.endpoint, lambda: "owner-token"))
        sessions: list[object] = []
        original = client._new_http_client

        def create_session():
            session = original()
            sessions.append(session)
            return session

        with patch.object(client, "_new_http_client", side_effect=create_session):
            workers = [
                threading.Thread(target=client.snapshot) for _ in range(4)
            ]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=4)
            self.assertEqual(len(sessions), 1)
            client.close()
            self.assertTrue(sessions[0].is_closed)
            self.assertEqual(client.snapshot()["schema_version"], 4)
            self.assertEqual(len(sessions), 2)
        client.close()

    def test_stale_pooled_connection_resends_read_only_get_once(self) -> None:
        gets = 0

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                nonlocal gets
                gets += 1
                if gets == 2:
                    # Simulate a keep-alive connection dropped while idle.
                    self.close_connection = True
                    return
                body = b'{"schema_version":4}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        server, thread, endpoint = _serve(Handler)
        try:
            client = _closing(self, RemoteReminderClient(endpoint, lambda: "fictional-token"))
            self.assertEqual(client.snapshot()["schema_version"], 4)
            self.assertEqual(client.snapshot()["schema_version"], 4)
            self.assertEqual(gets, 3)
            client.close()
        finally:
            _stop_server(server, thread)

if __name__ == "__main__":
    unittest.main()
