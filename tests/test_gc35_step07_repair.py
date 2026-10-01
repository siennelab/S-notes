"""Step 7 Post-Vibe repair: P1-1, P1-2, P1-3 and P2-2 regressions."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from src.snotes.application.remote_mcp_reminder import RemoteMcpReminderAdapter
from src.snotes.application.reminder_service import ReminderService
from src.snotes.bootstrap import compose_reminder_service
from src.snotes.domain.reminder_remote_contract import (
    OUTCOME_FAILED,
    OUTCOME_UNKNOWN,
    REMINDER_WIRE_SCHEMA_VERSION,
    RemoteReminderError,
)
from src.snotes.domain.reminder_schedule import SHANGHAI, ScheduleRangeError
from src.snotes.domain.reminders import (
    OCCURRENCE_PENDING,
    REMINDER_ACTIVE,
    ReminderDraft,
    ReminderSettings,
)
from src.snotes.reminder_authority import (
    LOCAL_REMINDER_AUTHORITY,
    ReminderAuthority,
    ReminderAuthorityError,
    resolve_reminder_authority,
)
from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.transport.mcp.tools import _reminder_write_response
from src.snotes.transport.reminder_http import (
    ACTOR_ROLE,
    OWNER_ROLE,
    ReminderCredential,
    ReminderHttpApplication,
    create_server,
)
from src.snotes.transport.remote_reminder_client import RemoteReminderClient
from src.snotes.ui.remote_reminder_controller import RemoteReminderController


ROOT = Path(__file__).resolve().parents[1]
A_SHARE = {"type": "cn_a_share_on_or_after", "day": 15}


class _Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value.timestamp()


class _Transport:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def send(self, *, topic: str, title: str, message: str) -> None:
        self.calls.append(message)


class _CloudFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-step07-repair-")
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "cloud-fixture.db"
        self.clock = _Clock(datetime(2026, 12, 1, 9, 0, tzinfo=SHANGHAI))
        self.repository = SQLiteReminderRepository(
            initialize_database(self.database),
            clock=lambda: datetime.fromtimestamp(self.clock(), SHANGHAI),
        )
        self.transport = _Transport()
        self.service = ReminderService(
            self.repository, transport=self.transport, clock=self.clock
        )
        self.service.initialize_settings()
        self.app = ReminderHttpApplication(
            self.service,
            {
                "owner": ReminderCredential("owner", OWNER_ROLE),
                "yan": ReminderCredential("yan", ACTOR_ROLE),
            },
        )

    def at(self, *args: int) -> None:
        self.clock.value = datetime(*args, tzinfo=SHANGHAI)

    def call(self, token: str, method: str, path: str, payload: dict | None = None):
        body = b"" if payload is None else json.dumps(payload).encode()
        return self.app.handle(
            method, path, headers={"Authorization": f"Bearer {token}"}, body=body
        )

    def command(self, name: str, payload: dict, token: str = "owner"):
        return self.call(token, "POST", f"/v1/reminder/commands/{name}", payload)

    def snapshot(self, token: str = "owner") -> dict:
        response = self.call(token, "GET", "/v1/reminder/snapshot")
        self.assertEqual(response.status, 200, response.body)
        return response.body

    def counts(self) -> tuple[int, int]:
        with sqlite3.connect(self.database) as connection:
            return (
                connection.execute("SELECT COUNT(*) FROM reminders").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM reminder_occurrences").fetchone()[0],
            )

    def occurrence_rows(self) -> list[tuple]:
        with sqlite3.connect(self.database) as connection:
            return connection.execute(
                "SELECT occurrence_key, status, completed_at FROM reminder_occurrences ORDER BY id"
            ).fetchall()

    def draft(self, title: str, spec: dict) -> ReminderDraft:
        return ReminderDraft(
            title, REMINDER_ACTIVE, spec, table_id=self.service.default_table().id
        )


class UnsupportedCalendarYearTests(_CloudFixture):
    """P1-1: an uncomputable calendar year cannot poison the Reminder system."""

    def test_unsupported_year_create_is_rejected_before_any_row(self) -> None:
        table_id = self.service.default_table().id
        before = self.counts()
        self.at(2027, 1, 4, 9, 0)
        with self.assertRaises(ScheduleRangeError):
            self.service.create_reminder(self.draft("FICTIONAL 2027", A_SHARE))
        response = self.command(
            "create_reminder",
            {"table_id": table_id, "title": "FICTIONAL 2027", "action_spec": A_SHARE},
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(response.body["error"], "cn_a_share_calendar_year_not_supported:2027")
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.snapshot()["items"], [])

    def test_unsupported_year_update_and_resume_are_rejected_atomically(self) -> None:
        item = self.service.create_reminder(self.draft("FICTIONAL", {"type": "one_time", "date": "2027-02-01"}))
        before = (self.service.get_reminder(item.id).reminder, self.occurrence_rows())
        self.at(2026, 12, 20, 9, 0)
        response = self.command(
            "update_reminder",
            {"item_id": item.id, "table_id": item.table_id, "title": "FICTIONAL", "action_spec": A_SHARE},
        )
        self.assertEqual(response.status, 400)
        self.assertEqual((self.service.get_reminder(item.id).reminder, self.occurrence_rows()), before)

        poison = self.repository.create(self.draft("FICTIONAL paused", A_SHARE))
        self.service.pause_reminder(poison.id)
        response = self.command("resume_reminder", {"item_id": poison.id})
        self.assertEqual(response.status, 400)
        self.assertEqual(self.service.get_reminder(poison.id).reminder.lifecycle, "paused")

    def test_year_end_completion_is_rejected_without_partial_commit(self) -> None:
        item = self.service.create_reminder(self.draft("FICTIONAL year end", A_SHARE))
        self.assertEqual(self.occurrence_rows(), [("2026-12-15", OCCURRENCE_PENDING, None)])
        self.at(2026, 12, 20, 9, 0)
        response = self.command("complete_occurrence", {"item_id": item.id})
        self.assertEqual(response.status, 400)
        self.assertEqual(response.body["error"], "cn_a_share_calendar_year_not_supported:2027")
        # Still exactly the pending current occurrence: no completed state was
        # committed, and the series is still visible and not broken.
        self.assertEqual(self.occurrence_rows(), [("2026-12-15", OCCURRENCE_PENDING, None)])
        rendered = {row["item_id"]: row for row in self.snapshot()["items"]}
        self.assertEqual(rendered[item.id]["status"], "due")
        actor = self.command(
            "complete_reminder_item",
            {"request_id": "fictional-year-end", "item_id": item.id},
            token="yan",
        )
        self.assertEqual(actor.status, 400)
        self.assertEqual(self.occurrence_rows(), [("2026-12-15", OCCURRENCE_PENDING, None)])

    def test_existing_poison_row_is_isolated_repairable_and_skipped_by_scheduler(self) -> None:
        self.service.save_settings(ReminderSettings(True, "09:00", "", "fictional-topic"))
        healthy = self.service.create_reminder(
            self.draft("FICTIONAL healthy", {"type": "one_time", "date": "2027-01-04"})
        )
        self.at(2027, 1, 4, 10, 0)
        # Legacy/corrupt data written without the service preflight.
        poison = self.repository.create(self.draft("FICTIONAL poison", A_SHARE))

        owner = {row["item_id"]: row for row in self.snapshot()["items"]}
        self.assertEqual(owner[poison.id]["status"], "schedule_error")
        self.assertEqual(owner[poison.id]["status_label"], "日程无法计算")
        self.assertIsNone(owner[poison.id]["occurrence"])
        self.assertEqual(owner[healthy.id]["status"], "due")
        actor = {row["item_id"]: row for row in self.snapshot("yan")["items"]}
        self.assertEqual(actor[poison.id]["status"], "schedule_error")
        listed = self.command(
            "list_reminder_items", {"table_id": poison.table_id}, token="yan"
        )
        self.assertEqual(listed.status, 200)

        # The scheduler keeps serving healthy reminders.
        self.assertEqual(self.service.run_scheduled_once(), 1)
        self.assertEqual(len(self.transport.calls), 1)
        self.assertIn("FICTIONAL healthy", self.transport.calls[0])

        # Completion on the poison item is a definite rejection, not a no-op.
        response = self.command("complete_occurrence", {"item_id": poison.id})
        self.assertEqual(response.status, 400)
        # The owner can still repair it, or delete it.
        repaired = self.command(
            "update_reminder",
            {
                "item_id": poison.id,
                "table_id": poison.table_id,
                "title": "FICTIONAL poison",
                "action_spec": {"type": "monthly_day", "day": 15},
            },
        )
        self.assertEqual(repaired.status, 200, repaired.body)
        self.assertEqual(
            {row["item_id"]: row for row in self.snapshot()["items"]}[poison.id]["status"],
            "upcoming",
        )
        self.assertEqual(self.command("delete_reminder", {"item_id": poison.id}).status, 200)
        self.assertNotIn(poison.id, {row["item_id"] for row in self.snapshot()["items"]})

    def test_unexpected_snapshot_failure_is_a_controlled_500_not_a_dropped_socket(self) -> None:
        server = create_server(self.app, host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        host, port = server.server_address
        client = RemoteReminderClient(f"http://{host}:{port}", lambda: "owner")
        self.addCleanup(client.close)
        with patch.object(self.service, "list_tables", side_effect=KeyError("fictional")):
            with self.assertRaises(RemoteReminderError) as raised:
                client.snapshot()
        self.assertEqual((raised.exception.kind, raised.exception.status), ("server", 500))
        self.assertEqual(raised.exception.code, "internal_error")
        self.assertEqual(client.snapshot()["schema_version"], REMINDER_WIRE_SCHEMA_VERSION)


class WireVersionTests(_CloudFixture):
    """P1-2: the wire contract version is independent from the DB schema."""

    def test_db_schema_version_change_does_not_change_wire_compatibility(self) -> None:
        with patch("src.snotes.storage.schema.REMINDER_SCHEMA_VERSION", "99"):
            self.assertEqual(self.snapshot()["schema_version"], REMINDER_WIRE_SCHEMA_VERSION)
        self.assertEqual(
            RemoteReminderClient("https://example.invalid", lambda: "x").supported_schema_version,
            REMINDER_WIRE_SCHEMA_VERSION,
        )
        contract = (ROOT / "src/snotes/domain/reminder_remote_contract.py").read_text()
        self.assertNotIn("storage", contract.split('"""', 2)[2])

    def test_newer_wire_version_is_still_a_schema_mismatch(self) -> None:
        with patch(
            "src.snotes.transport.reminder_http.REMINDER_WIRE_SCHEMA_VERSION",
            REMINDER_WIRE_SCHEMA_VERSION + 1,
        ):
            payload = self.snapshot()
        client = RemoteReminderClient("https://example.invalid", lambda: "x")
        with patch.object(client, "_request", return_value=payload):
            with self.assertRaises(RemoteReminderError) as raised:
                client.snapshot()
        self.assertEqual(raised.exception.kind, "schema_mismatch")

    def test_architecture_gate_passes_without_checker_changes(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/check_architecture.py"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("ARCHITECTURE_CHECK=PASS", completed.stdout)


class ReminderAuthorityTests(unittest.TestCase):
    """P1-3: authority is explicit everywhere and never fails open to local."""

    def test_environment_profiles(self) -> None:
        local =resolve_reminder_authority({"S_NOTES_REMINDER_AUTHORITY": "local"})
        self.assertEqual(local, LOCAL_REMINDER_AUTHORITY)
        remote = resolve_reminder_authority(
            {
                "S_NOTES_REMINDER_AUTHORITY": "remote",
                "S_NOTES_REMOTE_REMINDER_ENDPOINT": "https://reminder.example.invalid",
            }
        )
        self.assertEqual((remote.mode, remote.endpoint), ("remote", "https://reminder.example.invalid"))
        for environment in (
            {},
            {"S_NOTES_REMOTE_REMINDER_ENDPOINT": "https://reminder.example.invalid"},
            {"S_NOTES_REMINDER_AUTHORITY": "cloud"},
            {"S_NOTES_REMINDER_AUTHORITY": "remote"},
            {"S_NOTES_REMINDER_AUTHORITY": "local", "S_NOTES_REMOTE_REMINDER_ENDPOINT": "https://x.invalid"},
            {"S_NOTES_REMINDER_AUTHORITY": "local", "S_NOTES_RETIRE_REMINDER_HELPER": "yes"},
        ):
            with self.subTest(environment=environment):
                with self.assertRaises(ReminderAuthorityError):
                    resolve_reminder_authority(environment)

    def test_packaged_info_plist_is_the_authority_and_rejects_conflicts(self) -> None:
        info = {
            "SNotesReminderAuthority": "local",
            "SNotesReminderHelperRetired": True,
        }
        packaged = resolve_reminder_authority({}, bundle_info=info)
        self.assertEqual((packaged.mode, packaged.helper_retired), ("local", True))
        with self.assertRaises(ReminderAuthorityError):
            resolve_reminder_authority({}, bundle_info={"SNotesReminderHelperRetired": False})
        with self.assertRaises(ReminderAuthorityError):
            resolve_reminder_authority({}, bundle_info={"SNotesReminderAuthority": "local"})
        with self.assertRaises(ReminderAuthorityError):
            resolve_reminder_authority(
                {
                    "S_NOTES_REMINDER_AUTHORITY": "remote",
                    "S_NOTES_REMOTE_REMINDER_ENDPOINT": "https://reminder.example.invalid",
                },
                bundle_info=info,
            )
        remote_info = {
            "SNotesReminderAuthority": "remote",
            "SNotesRemoteReminderEndpoint": "https://reminder.ke-den.de",
            "SNotesReminderHelperRetired": False,
        }
        same = resolve_reminder_authority(
            {
                "S_NOTES_REMINDER_AUTHORITY": "remote",
                "S_NOTES_REMOTE_REMINDER_ENDPOINT": "https://reminder.ke-den.de",
            },
            bundle_info=remote_info,
        )
        self.assertEqual(same.endpoint, "https://reminder.ke-den.de")

    def test_candidate_resolves_remote_staging(self) -> None:
        from src.snotes.step07_candidate import STAGING_ENDPOINT, configure_candidate

        with tempfile.TemporaryDirectory(prefix="snotes-step07-authority-") as temporary:
            with patch.dict(
                os.environ,
                {"S_NOTES_STEP07_CANDIDATE": "1", "S_NOTES_STEP07_HOST_HOME": temporary},
                clear=True,
            ):
                self.assertTrue(configure_candidate())
                authority = resolve_reminder_authority()
        self.assertEqual((authority.mode, authority.endpoint), ("remote", STAGING_ENDPOINT))

    def test_local_composition_requires_local_authority_and_honors_retirement(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snotes-step07-authority-") as temporary:
            with self.assertRaises(ReminderAuthorityError):
                compose_reminder_service(
                    Path(temporary), ReminderAuthority("remote", "https://x.invalid")
                )
            with self.assertRaisesRegex(RuntimeError, "private_reminder_endpoint_required"):
                compose_reminder_service(
                    Path(temporary), ReminderAuthority("local", helper_retired=True)
                )

    def test_every_entry_point_fails_closed_without_authority(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snotes-step07-entry-") as temporary:
            home = Path(temporary) / "home"
            home.mkdir()
            base = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": str(home),
                "S_NOTES_DATA_ROOT": str(Path(temporary) / "data"),
                "QT_QPA_PLATFORM": "offscreen",
                "PYTHONPYCACHEPREFIX": str(Path(temporary) / "pycache"),
                "SNOTES_MCP_ACTOR_TARGET_ID": "yan",
                "SNOTES_MCP_READ_ONLY": "1",
            }
            commands = {
                "gui": [sys.executable, "app.py"],
                "mcp-stdio": [sys.executable, "-m", "src.snotes.transport.mcp.server", "doctor"],
                "repo-mcp-script": ["scripts/run_snotes_mcp_stdio.sh", "doctor"],
                "legacy-helper": [sys.executable, "reminder_helper.py", "--scheduled"],
            }
            for name, command in commands.items():
                with self.subTest(entry=name):
                    completed = subprocess.run(
                        command, cwd=ROOT, env=base, capture_output=True, text=True,
                        check=False, timeout=60,
                    )
                    self.assertEqual(completed.returncode, 2, completed.stderr)
                    self.assertIn("reminder_authority_not_configured", completed.stderr)
            # Nothing reached a Reminder database without an authority.
            self.assertEqual(list(Path(temporary).rglob("s_notes.db")), [])

            helper = subprocess.run(
                [sys.executable, "reminder_helper.py", "--scheduled"], cwd=ROOT,
                env={**base, "S_NOTES_REMINDER_AUTHORITY": "remote",
                     "S_NOTES_REMOTE_REMINDER_ENDPOINT": "https://reminder.example.invalid"},
                capture_output=True, text=True, check=False, timeout=60,
            )
            self.assertEqual(helper.returncode, 2)
            self.assertIn("authority is remote", helper.stderr)
            self.assertEqual(list(Path(temporary).rglob("s_notes.db")), [])

            doctor = subprocess.run(
                [sys.executable, "-m", "src.snotes.transport.mcp.server", "doctor"], cwd=ROOT,
                env={**base, "S_NOTES_REMINDER_AUTHORITY": "local"},
                capture_output=True, text=True, check=False, timeout=60,
            )
            self.assertEqual(doctor.returncode, 0, doctor.stderr)

    def test_build_profiles_declare_authority_explicitly(self) -> None:
        script = (ROOT / "scripts/build_macos_app.sh").read_text()
        spec = (ROOT / "packaging/S-notes.spec").read_text()
        self.assertIn('REMINDER_AUTHORITY="local"', script)
        self.assertIn('REMINDER_AUTHORITY="remote"', script)
        self.assertIn("SNotesReminderHelperRetired", spec)
        self.assertIn("SNotesReminderAuthority", spec)
        for path in ("src/snotes/bootstrap.py", "src/snotes/ui/reminder_center.py"):
            source = (ROOT / path).read_text()
            self.assertNotIn("S_NOTES_RETIRE_REMINDER_HELPER", source, path)
            self.assertNotIn("S_NOTES_REMOTE_REMINDER_ENDPOINT", source, path)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    mode = "ok"
    posts: list[str] = []

    def log_message(self, *_args) -> None:
        return

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._json(200, {"schema_version": REMINDER_WIRE_SCHEMA_VERSION})

    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers["Content-Length"]))
        type(self).posts.append(self.path)
        if self.mode == "ok":
            self._json(200, {"status": "updated"})
        elif self.mode == "rejected":
            self._json(400, {"error": "fictional_rejection"})
        elif self.mode == "server":
            self._json(500, {"error": "internal_error"})
        elif self.mode == "timeout":
            time.sleep(0.8)
            self._json(200, {"status": "updated"})
        elif self.mode == "drop":
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, *_args) -> None:
        return


class WriteOutcomeClientTests(unittest.TestCase):
    """P2-2: a write failure says whether the Cloud may have changed."""

    def setUp(self) -> None:
        _Handler.posts = []
        self.server = _Server(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.client = RemoteReminderClient(
            f"http://{host}:{port}", lambda: "owner", timeout_seconds=0.4
        )

    def tearDown(self) -> None:
        self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def outcome(self, mode: str) -> RemoteReminderError:
        _Handler.mode = mode
        with self.assertRaises(RemoteReminderError) as raised:
            self.client.command("pause_reminder", {"item_id": 1})
        return raised.exception

    def test_outcomes(self) -> None:
        _Handler.mode = "ok"
        self.assertEqual(self.client.command("pause_reminder", {"item_id": 1}), {"status": "updated"})
        rejected = self.outcome("rejected")
        self.assertEqual((rejected.kind, rejected.status, rejected.outcome), ("server", 400, OUTCOME_FAILED))
        self.assertEqual(self.outcome("server").outcome, OUTCOME_UNKNOWN)
        timeout = self.outcome("timeout")
        self.assertEqual((timeout.kind, timeout.outcome), ("offline", OUTCOME_UNKNOWN))
        dropped = self.outcome("drop")
        self.assertEqual((dropped.kind, dropped.outcome), ("offline", OUTCOME_UNKNOWN))
        # Every write was dispatched exactly once: none was retried.
        self.assertEqual(len(_Handler.posts), 5)

    def test_unsent_write_is_a_definite_failure(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        client = RemoteReminderClient(f"http://127.0.0.1:{port}", lambda: "owner")
        self.addCleanup(client.close)
        with self.assertRaises(RemoteReminderError) as raised:
            client.command("pause_reminder", {"item_id": 1})
        self.assertEqual((raised.exception.kind, raised.exception.outcome), ("offline", OUTCOME_FAILED))


class _ScriptedClient:
    def __init__(self, snapshot: dict) -> None:
        self.base = snapshot
        self.command_error: Exception | None = None
        self.snapshot_error: Exception | None = None
        self.commands: list[str] = []

    def snapshot(self) -> dict:
        if self.snapshot_error is not None:
            raise self.snapshot_error
        return dict(self.base, snapshot_time=f"t{len(self.commands)}")

    def command(self, name: str, payload: dict) -> dict:
        self.commands.append(name)
        if self.command_error is not None:
            raise self.command_error
        return {"status": "updated"}


class WriteOutcomeControllerTests(unittest.TestCase):
    """P2-2: confirmed-but-stale and unknown outcomes are not 'failed'."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        snapshot = {
            "schema_version": REMINDER_WIRE_SCHEMA_VERSION,
            "snapshot_time": "t0",
            "labels": [],
            "tables": [{"table_id": 1, "title": "FICTIONAL", "label": {"label_id": 1, "name": "x"}}],
            "items": [],
            "settings": {},
            "badge_count": 0,
        }
        self.client = _ScriptedClient(snapshot)
        self.controller = RemoteReminderController(self.client)
        self.failed: list[tuple[str, str]] = []
        self.finished: list[str] = []
        self.callbacks: list[bool] = []
        self.controller.commandFailed.connect(lambda name, kind, _d: self.failed.append((name, kind)))
        self.controller.commandFinished.connect(lambda name, _r: self.finished.append(name))
        self.controller.refresh()
        self.settle()
        self.assertEqual(self.controller.state, "online")

    def tearDown(self) -> None:
        self.controller.begin_shutdown()
        self.settle()

    def settle(self) -> None:
        deadline = time.monotonic() + 5
        while (self.controller.pending or self.controller._threads) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        self.app.processEvents()

    def submit(self) -> None:
        self.assertTrue(
            self.controller.submit(
                "pause_reminder", {"item_id": 1}, callback=lambda ok, _r: self.callbacks.append(ok)
            )
        )
        self.settle()

    def test_confirmed_command_with_failed_refresh_is_stale_not_failed(self) -> None:
        self.client.snapshot_error = RemoteReminderError("offline", code="unreachable")
        # The command's own schema preflight is part of command(); only the
        # post-command refresh fails here.
        original = self.client.command

        def command(name: str, payload: dict) -> dict:
            self.client.snapshot_error = None
            result = original(name, payload)
            self.client.snapshot_error = RemoteReminderError("offline", code="unreachable")
            return result

        self.client.command = command
        self.submit()
        self.assertEqual(self.failed, [])
        self.assertEqual(self.finished, ["pause_reminder"])
        self.assertEqual(self.callbacks, [True])
        self.assertEqual(self.controller.state, "stale")
        self.assertIn("已被服务器确认", self.controller.state_detail)
        self.assertTrue(self.controller.read_only)
        self.assertEqual(self.controller.snapshot_time, "t0")
        self.client.snapshot_error = None
        self.controller.refresh()
        self.settle()
        self.assertEqual(self.controller.state, "online")

    def test_unknown_outcome_blocks_writes_until_refresh_and_never_resends(self) -> None:
        self.client.command_error = RemoteReminderError(
            "offline", code="unreachable", outcome=OUTCOME_UNKNOWN
        )
        self.submit()
        self.assertEqual(self.failed, [("pause_reminder", "outcome_unknown")])
        self.assertEqual(self.callbacks, [False])
        self.assertEqual(self.controller.state, "outcome_unknown")
        self.assertIn("结果未知", self.controller.state_detail)
        self.assertTrue(self.controller.read_only)
        self.assertFalse(self.controller.submit("pause_reminder", {"item_id": 1}))
        self.assertEqual(self.client.commands, ["pause_reminder"])
        self.client.command_error = None
        self.controller.refresh()
        self.settle()
        self.assertEqual(self.controller.state, "online")

    def test_definite_rejection_keeps_the_connection_online(self) -> None:
        self.client.command_error = RemoteReminderError(
            "server", code="fictional_rejection", status=400
        )
        self.submit()
        self.assertEqual(self.failed, [("pause_reminder", "rejected")])
        self.assertEqual(self.controller.state, "online")
        self.assertFalse(self.controller.read_only)

    def test_read_command_failure_is_not_an_unknown_write(self) -> None:
        self.client.command_error = RemoteReminderError(
            "offline", code="unreachable", outcome=OUTCOME_UNKNOWN
        )
        results: list[bool] = []
        self.assertTrue(
            self.controller.request_ai_operation_logs(callback=lambda ok, _r: results.append(ok))
        )
        self.settle()
        self.assertEqual(results, [False])
        self.assertEqual(self.controller.state, "offline")


class McpUnknownOutcomeTests(unittest.TestCase):
    """P2-2: MCP never claims an unknown write left Reminders unchanged."""

    class _Client:
        def __init__(self, error: RemoteReminderError) -> None:
            self.error = error

        def command(self, _name: str, _payload: dict) -> dict:
            raise self.error

    def test_unknown_and_definite_write_failures(self) -> None:
        unknown = RemoteMcpReminderAdapter(
            self._Client(RemoteReminderError("offline", outcome=OUTCOME_UNKNOWN))
        ).pause_reminder_item("fictional-request", 7)
        self.assertEqual(unknown["status"], "failed")
        self.assertIsNone(unknown["modified_formal_reminder"])
        self.assertTrue(unknown["outcome_unknown"])
        receipt = _reminder_write_response(unknown)
        self.assertIsNone(receipt.changed_formal_reminder)
        self.assertTrue(receipt.outcome_unknown)

        definite = RemoteMcpReminderAdapter(
            self._Client(RemoteReminderError("offline"))
        ).pause_reminder_item("fictional-request", 7)
        self.assertFalse(definite["modified_formal_reminder"])
        self.assertNotIn("outcome_unknown", definite)
        self.assertFalse(_reminder_write_response(definite).outcome_unknown)


class ActorIdempotencyTests(_CloudFixture):
    def test_same_request_id_is_not_applied_twice(self) -> None:
        table_id = self.service.default_table().id
        payload = {
            "request_id": "fictional-idempotent",
            "table_id": table_id,
            "title": "FICTIONAL actor",
            "action_spec": {"type": "one_time", "date": "2027-02-01"},
        }
        first = self.command("create_reminder_item", payload, token="yan")
        second = self.command("create_reminder_item", payload, token="yan")
        self.assertEqual(first.body["status"], "created")
        self.assertEqual(second.body["status"], "already_processed")
        self.assertEqual(self.counts()[0], 1)


if __name__ == "__main__":
    unittest.main()
