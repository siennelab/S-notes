from __future__ import annotations

import http.client
import json
import socket
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.snotes.application.reminder_service import ReminderService
from src.snotes.domain.reminders import ReminderSettings
from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.transport.reminder_http import (
    ACTOR_COMMANDS,
    ACTOR_ROLE,
    OWNER_ONLY_COMMANDS,
    OWNER_ROLE,
    ReminderCredential,
    ReminderHttpApplication,
    create_server,
)


TEST_TOPIC = "s-step06"


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def send(self, *, topic: str, title: str, message: str) -> None:
        self.calls.append({"topic": topic, "title": title, "message": message})


class Step06ReminderApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-step06-api-")
        connections = initialize_database(Path(self.temporary.name) / "staging.db")
        self.transport = FakeTransport()
        self.service = ReminderService(
            SQLiteReminderRepository(connections), transport=self.transport
        )
        current = self.service.settings()
        self.service.save_settings(
            ReminderSettings(False, "10:00", "https://ntfy.ke-den.de", TEST_TOPIC)
        )
        self.credentials = {
            "owner-token": ReminderCredential("owner", OWNER_ROLE),
            "yan-token": ReminderCredential("yan", ACTOR_ROLE),
            "claude-token": ReminderCredential("claude-code", ACTOR_ROLE),
            "clawbot-token": ReminderCredential("clawbot", ACTOR_ROLE),
            "mac-clawbot-token": ReminderCredential("clawbot", ACTOR_ROLE),
            "antigravity-token": ReminderCredential("antigravity", ACTOR_ROLE),
        }
        self.app = ReminderHttpApplication(
            self.service,
            self.credentials,
            clock=lambda: datetime(2026, 9, 29, 18, 20, tzinfo=timezone.utc),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request(
        self,
        token: str | None,
        method: str,
        path: str,
        payload: dict | None = None,
    ):
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        body = b"" if payload is None else json.dumps(payload).encode()
        return self.app.handle(method, path, headers=headers, body=body)

    def command(self, token: str, name: str, payload: dict):
        return self.request(
            token, "POST", f"/v1/reminder/commands/{name}", payload
        )

    def seed_table_and_item(self, *, visible: bool, title: str) -> tuple[int, int]:
        table = self.command(
            "owner-token",
            "create_table",
            {"title": f"{title} table", "ai_visible": visible},
        )
        table_id = table.body["table_id"]
        item = self.command(
            "owner-token",
            "create_reminder",
            {
                "table_id": table_id,
                "title": title,
                "note": f"{title} note",
                "action_spec": {"type": "one_time", "date": "2026-10-10"},
            },
        )
        return table_id, item.body["item_id"]

    def test_authentication_fixed_identity_and_body_identity_rejection(self) -> None:
        missing = self.request(None, "GET", "/v1/reminder/snapshot")
        self.assertEqual(missing.status, 401)
        self.assertIn(("WWW-Authenticate", "Bearer"), missing.headers)
        self.assertEqual(
            self.request("wrong", "GET", "/v1/reminder/snapshot").status, 403
        )

        table_id, _item_id = self.seed_table_and_item(visible=True, title="FICTIONAL")
        spoof = self.command(
            "yan-token",
            "create_reminder_item",
            {
                "actor_target_id": "owner",
                "request_id": "spoof-1",
                "table_id": table_id,
                "title": "spoof",
                "action_spec": {"type": "one_time", "date": "2026-10-11"},
            },
        )
        self.assertEqual(spoof.status, 400)
        self.assertEqual(spoof.body["error"], "identity_in_body_forbidden")
        with self.assertRaisesRegex(ValueError, "unsupported_reminder_credential"):
            ReminderCredential("unknown", ACTOR_ROLE)

    def test_local_actor_tokens_keep_actor_permissions_and_audit_identity(self) -> None:
        table_id, _item_id = self.seed_table_and_item(
            visible=True,
            title="LOCAL ACTOR VISIBLE",
        )
        cases = (
            ("clawbot-token", "cloud-clawbot", "clawbot"),
            ("mac-clawbot-token", "mac-clawbot", "clawbot"),
            ("antigravity-token", "mac-antigravity", "antigravity"),
        )
        for token, request_id, expected_actor in cases:
            with self.subTest(token=token):
                created = self.command(
                    token,
                    "create_reminder_item",
                    {
                        "request_id": request_id,
                        "table_id": table_id,
                        "title": request_id,
                        "action_spec": {
                            "type": "one_time",
                            "date": "2026-10-21",
                        },
                    },
                )
                self.assertEqual(created.status, 200)
                self.assertEqual(created.body["status"], "created")
                operation = next(
                    row
                    for row in self.service.list_ai_operation_logs().rows
                    if row.request_id == request_id
                )
                self.assertEqual(operation.actor_target_id, expected_actor)
                forbidden = self.command(
                    token,
                    "delete_reminder",
                    {"item_id": created.body["item_id"]},
                )
                self.assertEqual(forbidden.status, 403)
                self.assertEqual(forbidden.body["error"], "command_forbidden")

        spoof = self.command(
            "antigravity-token",
            "create_reminder_item",
            {
                "request_id": "antigravity-spoof",
                "table_id": table_id,
                "title": "spoof",
                "action_spec": {"type": "one_time", "date": "2026-10-22"},
                "actor": "owner",
            },
        )
        self.assertEqual(spoof.status, 400)
        self.assertEqual(spoof.body["error"], "identity_in_body_forbidden")

    def test_snapshot_contract_and_actor_visibility_are_fail_closed(self) -> None:
        visible_table, visible_item = self.seed_table_and_item(
            visible=True, title="VISIBLE FICTIONAL"
        )
        hidden_label = self.command(
            "owner-token", "create_label", {"name": "HIDDEN LABEL"}
        ).body["label"]["label_id"]
        hidden_table = self.command(
            "owner-token",
            "create_table",
            {
                "title": "HIDDEN TABLE",
                "label_id": hidden_label,
                "ai_visible": False,
            },
        ).body["table_id"]
        hidden_item = self.command(
            "owner-token",
            "create_reminder",
            {
                "table_id": hidden_table,
                "title": "HIDDEN SECRET ITEM",
                "note": "HIDDEN SECRET NOTE",
                "action_spec": {"type": "one_time", "date": "2026-10-12"},
            },
        ).body["item_id"]

        owner = self.request("owner-token", "GET", "/v1/reminder/snapshot")
        actor = self.request("yan-token", "GET", "/v1/reminder/snapshot")
        self.assertEqual(owner.status, 200)
        self.assertEqual(actor.status, 200)
        self.assertEqual(
            set(owner.body),
            {
                "schema_version",
                "server_time",
                "snapshot_time",
                "labels",
                "tables",
                "items",
                "settings",
                "badge_count",
                "trash",
            },
        )
        self.assertEqual(owner.body["schema_version"], 4)
        self.assertEqual(owner.body["server_time"], "2026-09-29T18:20:00Z")
        self.assertNotIn("ntfy_endpoint", owner.body["settings"])
        self.assertEqual(owner.body["settings"]["ntfy_topic"], TEST_TOPIC)
        self.assertNotIn("ntfy_endpoint", actor.body["settings"])
        self.assertNotIn("ntfy_topic", actor.body["settings"])
        self.assertNotIn("trash", actor.body)
        actor_table_ids = {item["table_id"] for item in actor.body["tables"]}
        self.assertIn(visible_table, actor_table_ids)
        self.assertNotIn(hidden_table, actor_table_ids)
        self.assertEqual(
            {item["item_id"] for item in actor.body["items"]}, {visible_item}
        )
        self.assertNotIn(hidden_label, {item["label_id"] for item in actor.body["labels"]})
        serialized = json.dumps(actor.body, ensure_ascii=False)
        self.assertNotIn("HIDDEN", serialized)

        for command, key, value in (
            ("get_reminder_table", "table_id", hidden_table),
            ("get_reminder_item", "item_id", hidden_item),
        ):
            response = self.command("yan-token", command, {key: value})
            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["status"], "not_found")

    def test_actor_commands_reuse_service_allowlist_and_cannot_guess_hidden_ids(self) -> None:
        visible_table, visible_item = self.seed_table_and_item(
            visible=True, title="ACTOR VISIBLE"
        )
        hidden_table, hidden_item = self.seed_table_and_item(
            visible=False, title="ACTOR HIDDEN"
        )
        created = self.command(
            "yan-token",
            "create_reminder_item",
            {
                "request_id": "yan-create-1",
                "table_id": visible_table,
                "title": "YAN ITEM",
                "action_spec": {"type": "one_time", "date": "2026-10-20"},
            },
        )
        self.assertEqual(created.body["status"], "created")
        actor_item = created.body["item_id"]
        updated = self.command(
            "yan-token",
            "update_reminder_item",
            {
                "request_id": "yan-update-1",
                "item_id": actor_item,
                "title": "YAN ITEM UPDATED",
                "multi_notify": True,
            },
        )
        self.assertEqual(updated.body["status"], "updated")
        paused = self.command(
            "yan-token",
            "pause_reminder_item",
            {"request_id": "yan-pause-1", "item_id": visible_item},
        )
        self.assertEqual(paused.body["status"], "updated")
        resumed = self.command(
            "yan-token",
            "resume_reminder_item",
            {"request_id": "yan-resume-1", "item_id": visible_item},
        )
        self.assertEqual(resumed.body["status"], "updated")

        hidden_update = self.command(
            "yan-token",
            "update_reminder_item",
            {
                "request_id": "yan-hidden-1",
                "item_id": hidden_item,
                "title": "STOLEN",
            },
        )
        self.assertEqual(hidden_update.body["status"], "not_found")
        self.assertEqual(
            self.command(
                "yan-token",
                "update_reminder_table",
                {
                    "request_id": "yan-hidden-table-1",
                    "table_id": hidden_table,
                    "title": "STOLEN TABLE",
                },
            ).body["status"],
            "not_found",
        )
        self.assertEqual(
            self.command("owner-token", "get_reminder_item", {"item_id": hidden_item})
            .body["item"]["title"],
            "ACTOR HIDDEN",
        )
        self.assertEqual(
            self.command("yan-token", "delete_reminder", {"item_id": visible_item}).status,
            403,
        )
        self.assertEqual(
            self.command(
                "yan-token",
                "set_table_ai_visible",
                {"table_id": hidden_table, "ai_visible": True},
            ).status,
            403,
        )
        self.assertNotIn("delete_reminder", ACTOR_COMMANDS)
        self.assertIn("delete_reminder", OWNER_ONLY_COMMANDS)

    def test_owner_commands_cover_current_ui_and_staging_settings_are_pinned(self) -> None:
        label = self.command(
            "owner-token", "create_label", {"name": "OWNER LABEL"}
        ).body["label"]
        renamed = self.command(
            "owner-token",
            "rename_label",
            {"label_id": label["label_id"], "name": "OWNER LABEL 2"},
        )
        self.assertEqual(renamed.body["label"]["name"], "OWNER LABEL 2")
        labels = self.service.list_labels()
        reordered = self.command(
            "owner-token",
            "reorder_labels",
            {"label_ids": list(reversed([item.id for item in labels]))},
        )
        self.assertEqual(reordered.body["status"], "updated")

        table_id, item_id = self.seed_table_and_item(visible=True, title="OWNER ITEM")
        self.assertEqual(
            self.command(
                "owner-token",
                "update_table_metadata",
                {"table_id": table_id, "title": "OWNER TABLE UPDATED"},
            ).body["status"],
            "updated",
        )
        self.assertEqual(
            self.command(
                "owner-token",
                "update_reminder",
                {
                    "item_id": item_id,
                    "table_id": table_id,
                    "title": "OWNER ITEM UPDATED",
                    "note": "updated",
                    "action_spec": {"type": "one_time", "date": "2026-11-01"},
                    "multi_notify": True,
                },
            ).body["status"],
            "updated",
        )
        for command in ("pause_reminder", "resume_reminder", "complete_occurrence"):
            self.assertEqual(
                self.command("owner-token", command, {"item_id": item_id}).status, 200
            )

        existing_settings = self.service.settings()
        bad_topic = self.command(
            "owner-token",
            "save_settings",
            {"notifications_enabled": True, "ntfy_topic": "s-other"},
        )
        self.assertEqual(bad_topic.status, 400)
        self.assertEqual(bad_topic.body["error"], "ntfy_topic_immutable")
        self.assertEqual(self.service.settings(), existing_settings)
        actor_settings = self.command(
            "yan-token", "save_settings", {"notifications_enabled": True}
        )
        self.assertEqual(actor_settings.status, 403)
        self.assertEqual(self.service.settings(), existing_settings)
        bad_time = self.command(
            "owner-token", "save_settings", {"daily_time": "25:90"}
        )
        self.assertEqual(bad_time.status, 400)
        endpoint_edit = self.command(
            "owner-token",
            "save_settings",
            {"ntfy_endpoint": "https://ntfy.sh"},
        )
        self.assertEqual(endpoint_edit.status, 400)
        saved = self.command(
            "owner-token",
            "save_settings",
            {
                "notifications_enabled": True,
                "daily_time": "10:00",
                "ntfy_topic": TEST_TOPIC,
            },
        )
        self.assertEqual(saved.status, 200)
        self.assertEqual(saved.body["status"], "updated")
        self.assertTrue(self.service.settings().notifications_enabled)
        self.assertEqual(self.service.settings().daily_time, "10:00")
        self.assertEqual(self.service.settings().ntfy_endpoint, "https://ntfy.ke-den.de")
        self.assertEqual(self.service.settings().ntfy_topic, TEST_TOPIC)
        self.assertEqual(
            self.command("owner-token", "send_test_notification", {}).body["status"],
            "sent",
        )
        self.assertEqual(self.transport.calls[0]["topic"], TEST_TOPIC)
        self.assertEqual(
            self.command(
                "owner-token", "list_ai_operation_logs", {"offset": 0, "limit": 20}
            ).body["status"],
            "ok",
        )

        self.command("owner-token", "delete_reminder", {"item_id": item_id})
        self.command("owner-token", "delete_table", {"table_id": table_id})
        trash = self.request("owner-token", "GET", "/v1/reminder/snapshot").body["trash"]
        self.assertIn(table_id, {item["table_id"] for item in trash["tables"]})
        self.assertEqual(
            self.command("owner-token", "restore_table", {"table_id": table_id}).status,
            200,
        )
        self.command("owner-token", "delete_table", {"table_id": table_id})
        self.assertEqual(
            self.command(
                "owner-token", "permanently_delete_table", {"table_id": table_id}
            ).status,
            200,
        )

    def test_http_server_enforces_loopback_body_limit_and_content_length(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            create_server(self.app, host="0.0.0.0", port=0)

        server = create_server(
            self.app, port=0, max_body_bytes=32, request_timeout=0.15
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        try:
            connection = http.client.HTTPConnection(host, port, timeout=2)
            connection.request(
                "POST",
                "/v1/reminder/commands/send_test_notification",
                body=b"{}",
                headers={
                    "Authorization": "Bearer owner-token",
                    "Content-Length": "33",
                },
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 413)
            response.read()
            connection.close()

            raw = socket.create_connection((host, port), timeout=2)
            raw.sendall(
                b"POST /v1/reminder/commands/send_test_notification HTTP/1.1\r\n"
                + f"Host: {host}\r\n".encode()
                + b"Authorization: Bearer owner-token\r\nConnection: close\r\n\r\n"
            )
            self.assertIn(b" 411 ", _read_all(raw))
            raw.close()

            slow = socket.create_connection((host, port), timeout=2)
            slow.sendall(
                b"POST /v1/reminder/commands/send_test_notification HTTP/1.1\r\n"
                + f"Host: {host}\r\n".encode()
                + b"Authorization: Bearer owner-token\r\n"
                + b"Content-Length: 2\r\nConnection: close\r\n\r\n{"
            )
            self.assertIn(b" 408 ", _read_all(slow))
            slow.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def _read_all(sock: socket.socket) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


if __name__ == "__main__":
    unittest.main()
