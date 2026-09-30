from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

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

        client = RemoteReminderClient(self.endpoint, credential)
        self.assertEqual(client.timeout_seconds, DEFAULT_TIMEOUT_SECONDS)
        self.assertEqual(client.snapshot()["schema_version"], 4)
        ensured = client.command("ensure_default_table", {})
        self.assertEqual(ensured["status"], "ok")
        # One direct snapshot, then command's schema preflight and POST.
        self.assertEqual(len(calls), 3)

    def test_error_kinds_cover_auth_offline_and_server(self) -> None:
        unauthorized = RemoteReminderClient(self.endpoint, lambda: "wrong-token")
        with self.assertRaises(RemoteReminderError) as auth:
            unauthorized.snapshot()
        self.assertEqual(auth.exception.kind, "auth")
        self.assertEqual(auth.exception.status, 403)

        valid = RemoteReminderClient(self.endpoint, lambda: "owner-token")
        with patch.object(valid._opener, "open", side_effect=URLError("offline")):
            with self.assertRaises(RemoteReminderError) as offline:
                valid.snapshot()
        self.assertEqual(offline.exception.kind, "offline")

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
            client = RemoteReminderClient(
                f"http://{host}:{port}", lambda: "fictional-token"
            )
            with self.assertRaises(RemoteReminderError) as mismatch:
                client.command("create_table", {"title": "BLOCKED"})
            self.assertEqual(mismatch.exception.kind, "schema_mismatch")
            self.assertEqual(requests, ["GET"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
