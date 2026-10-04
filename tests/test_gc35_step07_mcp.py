from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.snotes.application.local_mcp_service import LocalMcpService
from src.snotes.application.remote_mcp_reminder import (
    ACTOR_CREDENTIAL_DIRECTORY,
    RemoteMcpReminderAdapter,
    default_actor_credential_path,
)
from src.snotes.bootstrap import build_local_mcp_service
from src.snotes.reminder_authority import ReminderAuthority
from src.snotes.ui.reminder_owner import OwnerSnapshotCache


class _RecordingCommandClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def command(self, name: str, payload: dict) -> dict:
        self.calls.append((name, payload))
        if name in {"list_reminder_tables", "list_reminder_items"}:
            return {"status": "ok", "results": []}
        return {"status": "ok"}


class Step07RemoteMcpReminderTests(unittest.TestCase):
    def test_real_cloud_item_payloads_fit_existing_mcp_schema(self) -> None:
        from src.snotes.application.reminder_service import ReminderService
        from src.snotes.domain.reminders import ReminderDraft
        from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
        from src.snotes.storage.schema import initialize_database
        from src.snotes.transport.reminder_http import (
            ACTOR_ROLE, ReminderCredential, ReminderHttpApplication,
        )
        from src.snotes.transport.mcp.schemas import (
            GetReminderItemResponse, ListReminderItemsResponse,
        )

        with tempfile.TemporaryDirectory(prefix="snotes-step08d-mcp-") as root:
            service = ReminderService(SQLiteReminderRepository(
                initialize_database(Path(root) / "fictional-cloud.db")
            ))
            item = service.create_reminder(ReminderDraft(
                "FICTIONAL item", "active", {"type": "one_time", "date": "2099-01-01"}
            ))
            table = service.get_reminder(item.id).table
            api = ReminderHttpApplication(service, {
                "fictional": ReminderCredential("yan", ACTOR_ROLE),
            })

            class Client:
                def command(self, name, payload):
                    response = api.handle(
                        "POST", "/v1/reminder/commands/" + name,
                        headers={"Authorization": "Bearer fictional"},
                        body=json.dumps(payload).encode(),
                    )
                    self_test.assertEqual(response.status, 200)
                    return response.body

            self_test = self
            adapter = RemoteMcpReminderAdapter(Client())
            listed = ListReminderItemsResponse.model_validate(adapter.list_reminder_items(table.id))
            fetched = GetReminderItemResponse.model_validate(adapter.get_reminder_item(item.id))
            self.assertEqual(listed.results[0].table_title, table.title)
            self.assertEqual(fetched.item.table_title, table.title)
            service.set_table_ai_visible(table.id, False)
            hidden = GetReminderItemResponse.model_validate(adapter.get_reminder_item(item.id))
            self.assertEqual(hidden.status, "not_found")
            self.assertIsNone(hidden.item)

    def test_table_lookup_failure_never_returns_partial_item_data(self) -> None:
        client = Mock()
        adapter = RemoteMcpReminderAdapter(client)
        item = {"item_id": 1, "table_id": 2, "title": "FICTIONAL"}
        client.command.side_effect = [
            {"status": "ok", "results": [item]}, {"status": "not_found"},
        ]
        listed = adapter.list_reminder_items(2)
        self.assertEqual(listed["status"], "failed")
        self.assertEqual(listed["results"], [])
        client.command.side_effect = [
            {"status": "ok", "item": item}, {"status": "not_found"},
        ]
        fetched = adapter.get_reminder_item(1)
        self.assertEqual(fetched["status"], "not_found")
        self.assertNotIn("item", fetched)

    def test_actor_credential_uses_host_home_outside_candidate_home(self) -> None:
        with patch.dict(
            os.environ,
            {
                "HOME": "/private/tmp/step07-isolated-home",
                "S_NOTES_STEP07_HOST_HOME": "/private/tmp/step07-host-home",
            },
        ):
            self.assertEqual(
                default_actor_credential_path("yan"),
                Path("/private/tmp/step07-host-home")
                / ACTOR_CREDENTIAL_DIRECTORY
                / "yan",
            )

    def test_adapter_routes_every_actor_reminder_operation_to_cloud_commands(self) -> None:
        client = _RecordingCommandClient()
        adapter = RemoteMcpReminderAdapter(client)

        adapter.list_reminder_labels()
        adapter.create_reminder_label("r-label", "Work")
        adapter.list_reminder_tables("find", 2, 7, 3, "visible")
        adapter.get_reminder_table(11)
        adapter.create_reminder_table("r-table", "Plan", 3)
        adapter.update_reminder_table("r-table-update", 11, "Next", 4)
        adapter.list_reminder_items(
            11, "needle", 3, 5, "active", "2026-09-01", "2026-09-30"
        )
        adapter.get_reminder_item(21)
        adapter.create_reminder_item(
            "r-item",
            11,
            "Ship",
            {"kind": "date", "date": "2026-10-01"},
            {"kind": "date", "date": "2026-10-02"},
            "note",
            "active",
        )
        adapter.update_reminder_item(
            "r-item-update",
            21,
            "Shipped",
            "done",
            {"kind": "none"},
            None,
            True,
            "active",
            False,
        )
        adapter.complete_reminder_item("r-complete", 21, False)
        adapter.pause_reminder_item("r-pause", 21)
        adapter.resume_reminder_item("r-resume", 21)

        self.assertEqual(
            [name for name, _payload in client.calls],
            [
                "list_reminder_labels",
                "create_reminder_label",
                "list_reminder_tables",
                "get_reminder_table",
                "create_reminder_table",
                "update_reminder_table",
                "list_reminder_items",
                "get_reminder_item",
                "create_reminder_item",
                "update_reminder_item",
                "complete_reminder_item",
                "pause_reminder_item",
                "resume_reminder_item",
            ],
        )
        self.assertEqual(client.calls[2][1]["label_id"], 3)
        self.assertNotIn("page", client.calls[2][1])
        self.assertEqual(client.calls[6][1]["table_id"], 11)
        self.assertNotIn("page_size", client.calls[6][1])
        self.assertEqual(client.calls[9][1]["clear_deadline"], True)
        self.assertEqual(client.calls[10][1]["current_occurrence_only"], False)

    def test_local_facade_never_falls_back_when_remote_adapter_is_present(self) -> None:
        remote = Mock(spec=RemoteMcpReminderAdapter)
        service = object.__new__(LocalMcpService)
        service._remote_reminders = remote
        service._reminders = Mock()

        service.list_reminder_labels()
        service.create_reminder_label("r1", "Work")
        service.list_reminder_tables()
        service.get_reminder_table(11)
        service.create_reminder_table("r2")
        service.update_reminder_table("r3", 11)
        service.list_reminder_items(11)
        service.get_reminder_item(21)
        service.create_reminder_item("r4", 11, "Ship", {"kind": "none"})
        service.update_reminder_item("r5", 21)
        service.complete_reminder_item("r6", 21)
        service.pause_reminder_item("r7", 21)
        service.resume_reminder_item("r8", 21)

        self.assertEqual(
            [call[0] for call in remote.mock_calls],
            [
                "list_reminder_labels",
                "create_reminder_label",
                "list_reminder_tables",
                "get_reminder_table",
                "create_reminder_table",
                "update_reminder_table",
                "list_reminder_items",
                "get_reminder_item",
                "create_reminder_item",
                "update_reminder_item",
                "complete_reminder_item",
                "pause_reminder_item",
                "resume_reminder_item",
            ],
        )
        self.assertEqual(service._reminders.mock_calls, [])

    def test_non_reminder_actors_build_and_read_notes_with_remote_authority(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snotes-step07-notes-") as temporary:
            root = Path(temporary)
            with patch.dict(os.environ, {"HOME": str(root / "home")}):
                for actor_target_id in ("antigravity", "clawbot"):
                    with self.subTest(actor_target_id=actor_target_id):
                        service = build_local_mcp_service(
                            actor_target_id=actor_target_id,
                            resolved_data_root=root / actor_target_id,
                            reminder_authority=ReminderAuthority(
                                "remote", "http://127.0.0.1:9"
                            ),
                        )
                        note = service._notes.create_note()
                        note = service._notes.save_note(
                            note.id,
                            title="FICTIONAL_REMOTE_BOOTSTRAP_NOTE",
                            content_markdown=f"readable by {actor_target_id}",
                            location=note.location,
                            expected_revision=note.revision,
                        )
                        service._ai_metadata.set_note_visibility(note.id, "full")
                        service._ai_metadata.set_note_targets(note.id, [actor_target_id])

                        discovered = service.search_notes("REMOTE_BOOTSTRAP_NOTE")
                        self.assertEqual(discovered["status"], "ok")
                        self.assertEqual(
                            [item["note_id"] for item in discovered["results"]],
                            [note.id],
                        )
                        read = service.get_note(note.id)
                        self.assertEqual(read["status"], "ok")
                        self.assertEqual(read["content"], f"readable by {actor_target_id}")

    def test_missing_actor_credential_fails_closed_without_local_reminder_or_gui_cache(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snotes-step07-mcp-") as temporary:
            root = Path(temporary)
            cache_load = Mock(side_effect=AssertionError("GUI cache accessed"))
            with (
                patch.dict(
                    os.environ,
                    {
                        "S_NOTES_STEP07_HOST_HOME": str(root / "host-home"),
                    },
                ),
                patch(
                    "src.snotes.bootstrap.SQLiteReminderRepository",
                    side_effect=AssertionError("local Reminder repository created"),
                ) as local_repository,
                patch.object(OwnerSnapshotCache, "load", cache_load),
                patch(
                    "src.snotes.ui.reminder_owner.owner_credential",
                    side_effect=AssertionError("GUI owner credential accessed"),
                ) as owner_credential,
                patch(
                    "src.snotes.transport.remote_reminder_client."
                    "RemoteReminderClient._new_http_client",
                    side_effect=AssertionError("Reminder HTTP client created"),
                ) as http_client,
            ):
                for actor_target_id in (
                    "yan",
                    "claude-code",
                    "antigravity",
                    "clawbot",
                ):
                    with self.subTest(actor_target_id=actor_target_id):
                        service = build_local_mcp_service(
                            actor_target_id=actor_target_id,
                            resolved_data_root=root / actor_target_id,
                            reminder_authority=ReminderAuthority(
                                "remote", "http://127.0.0.1:9"
                            ),
                        )
                        read = service.list_reminder_labels()
                        write = service.create_reminder_label(
                            f"fixture-{actor_target_id}", "FICTIONAL"
                        )

                        self.assertEqual(read["status"], "failed")
                        self.assertEqual(read["labels"], [])
                        self.assertIn("actor credential", read["reason"])
                        self.assertEqual(write["status"], "failed")
                        self.assertFalse(write["modified_formal_reminder"])
                        self.assertFalse(write["entered_confirmation"])
                        self.assertIn("actor credential", write["reason"])

            local_repository.assert_not_called()
            owner_credential.assert_not_called()
            cache_load.assert_not_called()
            http_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
