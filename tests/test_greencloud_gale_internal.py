"""Gale integration tests use fictional Notes, outbox, Reminder and credentials."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mcp import types
from src.cloud_read_mcp import binding_for_effective_uid
from src.cloud_outbox import load_actor_credentials
from src.reminder_cloud import load_api_credentials
from src.services.gale_submission_outbox import (initialize_outbox, get_operation_status, record_terminal_receipt,
                                                export_unacknowledged, acknowledge_submission)
from src.snotes.application.cloud_proposal_service import CloudProposalService
from src.snotes.application.cloud_read_service import CloudReadService
from src.snotes.application.reminder_service import ReminderService
from src.snotes.application.remote_mcp_reminder import RemoteMcpReminderAdapter
from src.snotes.storage.cloud_read import SQLiteCloudMirrorReader
from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.transport.cloud_outbox_http import CloudOutboxHttpApplication, _ActorOutboxGateway
from src.snotes.transport.mcp.cloud_read_server import cloud_read_tools
from src.snotes.transport.mcp.greencloud_internal_server import build_greencloud_internal_server
from src.snotes.transport.reminder_http import ACTOR_COMMANDS, ACTOR_ROLE, OWNER_ONLY_COMMANDS, ReminderCredential, ReminderHttpApplication
from tests.greencloud_gale_fixture import build_fixtures

CHECKS_PATH = Path(__file__).resolve().parents[1] / "deploy/greencloud/gale-internal/fixture_checks.py"
spec = importlib.util.spec_from_file_location("gale_fixture_checks", CHECKS_PATH)
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


class GaleInternalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-gale-targeted-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest = build_fixtures(self.root / "fixture")
        self.outbox = self.root / "outbox.db"
        initialize_outbox(self.outbox, generalized=True)
        self.reader = SQLiteCloudMirrorReader(self.root / "fixture/mirror/gale/current.db")
        self.reminder_service = ReminderService(SQLiteReminderRepository(initialize_database(self.root / "reminder.db")))
        self.api = ReminderHttpApplication(self.reminder_service, {"fictional-gale-reminder": ReminderCredential("gale", ACTOR_ROLE)})

        class Client:
            def command(_, name, payload):
                response = self.api.handle("POST", "/v1/reminder/commands/" + name,
                           headers={"Authorization": "Bearer fictional-gale-reminder"}, body=json.dumps(payload).encode())
                self.assertEqual(response.status, 200)
                return response.body
        self.server = build_greencloud_internal_server(CloudReadService(self.reader),
                      CloudProposalService(self.reader, _ActorOutboxGateway(self.outbox, "gale")), RemoteMcpReminderAdapter(Client()))

    async def call(self, name, arguments):
        result = await self.server.request_handlers[types.CallToolRequest](types.CallToolRequest(
            params=types.CallToolRequestParams(name=name, arguments=arguments)))
        return checks.result_body(result.root)

    def test_unified_tools_and_complete_fictional_workflow(self):
        async def run():
            result = await self.server.request_handlers[types.ListToolsRequest](types.ListToolsRequest())
            names = {tool.name for tool in result.root.tools}
            self.assertEqual(names, {tool.name for tool in cloud_read_tools(proposal_enabled=True)} | ACTOR_COMMANDS)
            self.assertEqual(len(names), 25)
            self.assertFalse(names & OWNER_ONLY_COMMANDS)
            await checks.exercise_tools(self.call, self.manifest)
        asyncio.run(run())
        proposal = get_operation_status(self.outbox, source_actor="gale", request_id="fictional-gale-create_note")
        self.assertEqual(proposal.source_actor, "gale")
        self.assertIsNone(get_operation_status(self.outbox, source_actor="claude-code", request_id="fictional-gale-create_note"))
        export_unacknowledged(self.outbox)
        acknowledge_submission(self.outbox, submission_uuid=proposal.submission_uuid, local_receipt_id="123")
        record_terminal_receipt(self.outbox, source_actor="gale", request_id="fictional-gale-create_note",
                                terminal_state="accepted", receipt_payload={"fictional": True})
        self.assertEqual(get_operation_status(self.outbox, source_actor="gale", request_id="fictional-gale-create_note").delivery_state, "accepted")

    def test_os_binding_is_fixed_and_resolves_installed_uid(self):
        account = SimpleNamespace(pw_name="snotes-gale", pw_uid=2017, pw_gid=2017, pw_shell="/usr/sbin/nologin")
        with patch("src.cloud_read_mcp.pwd.getpwnam", return_value=account):
            binding = binding_for_effective_uid(2017)
            self.assertEqual(binding.actor, "gale")
            self.assertEqual(str(binding.credential_path), "/srv/snotes/actor-credentials/gale/notes-proposal-token")
            for uid in (0, 2006, 9999):
                with self.assertRaisesRegex(RuntimeError, "unsupported_os_identity"):
                    binding_for_effective_uid(uid)
            self.assertEqual(binding_for_effective_uid(2001).actor, "claude-code")
            self.assertEqual(binding_for_effective_uid(2002).actor, "clawbot")

    def test_separate_service_tokens_and_missing_or_borrowed_token_fail_closed(self):
        mapping = {"owner": "fictional-owner", "yan": "fictional-yan", "claude-code": "fictional-claude",
                   "clawbot": "fictional-clawbot", "gale": "fictional-gale-reminder"}
        api_path = self.root / "reminder-api.json"
        api_path.write_text(json.dumps(mapping))
        api_path.chmod(0o600)
        notes_path = self.root / "gale-notes-proposal-token"
        notes_path.write_text("fictional-gale-notes")
        notes_path.chmod(0o600)
        with patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(self.root)}):
            credentials = load_actor_credentials()
            self.assertEqual(credentials["fictional-gale-notes"], "gale")
            self.assertNotIn("fictional-gale-reminder", credentials)
            reminder_credentials = load_api_credentials()
            self.assertEqual(reminder_credentials["fictional-gale-reminder"].role, ACTOR_ROLE)
            self.assertNotIn("fictional-gale-notes", reminder_credentials)
            notes_path.write_text("fictional-owner")
            with self.assertRaises(RuntimeError):
                load_actor_credentials()
            notes_path.unlink()
            with self.assertRaises(RuntimeError):
                load_actor_credentials()

    def test_http_credential_attribution_and_owner_denial(self):
        credentials = {"fictional-claude": "claude-code", "fictional-claw": "clawbot", "fictional-gale-notes": "gale"}
        app = CloudOutboxHttpApplication(self.outbox, credentials, reader_factory=lambda actor: SQLiteCloudMirrorReader(
              self.root / "fixture/mirror" / actor / "current.db"))
        response = app.handle("POST", "/v1/notes/proposals/status", headers={"Authorization": "Bearer fictional-gale-reminder"},
                              body=b'{"request_id":"fictional"}')
        self.assertEqual(response.status, 403)
        response = app.handle("POST", "/v1/notes/proposals/status", headers={"Authorization": "Bearer fictional-gale-notes"},
                              body=b'{"request_id":"fictional","actor":"owner"}')
        self.assertEqual(response.status, 400)
        for command in OWNER_ONLY_COMMANDS:
            response = self.api.handle("POST", "/v1/reminder/commands/" + command,
                                      headers={"Authorization": "Bearer fictional-gale-reminder"}, body=b"{}")
            self.assertEqual(response.status, 403)
        hidden = self.reminder_service.create_table("FICTIONAL hidden", ai_visible=False)
        response = self.api.handle("POST", "/v1/reminder/commands/get_reminder_table",
                                  headers={"Authorization": "Bearer fictional-gale-reminder"},
                                  body=json.dumps({"table_id": hidden.id}).encode())
        self.assertEqual(response.body["status"], "not_found")


if __name__ == "__main__":
    unittest.main()
