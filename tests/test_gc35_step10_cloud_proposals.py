"""Focused Step 10 Cloud proposal application/HTTP/MCP verification.

All Notes and outbox data in this file is fictional and lives under one
temporary directory.  No production path, credential, or database is opened.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.services.gale_submission_outbox import (
    acknowledge_submission,
    export_unacknowledged,
    get_operation_status,
    initialize_outbox,
    record_terminal_receipt,
)
from src.snotes.application.cloud_proposal_service import CloudProposalService
from src.snotes.application.cloud_read_service import CloudReadService
from src.snotes.domain.actor_mirror import ActorMirrorRecord
from src.snotes.storage.actor_mirror import write_actor_mirror
from src.snotes.storage.cloud_read import SQLiteCloudMirrorReader
from src.snotes.transport.cloud_outbox_http import (
    DEFAULT_MAX_BODY_BYTES,
    CloudOutboxHttpApplication,
    _ActorOutboxGateway,
)
from src.snotes.transport.mcp.cloud_read_server import (
    call_cloud_read_tool,
    cloud_read_tools,
)


CLAUDE = "claude-code"
CLAWBOT = "clawbot"
TOKENS = {"fictional-claude-token": CLAUDE, "fictional-clawbot-token": CLAWBOT}
FULL_ID = 101
REDACTED_ID = 102
CLAUDE_ONLY_ID = 103
CLAWBOT_ONLY_ID = 104
HIDDEN_ID = 999
FULL_VERSION = "snotes-v1-" + "1" * 64
REDACTED_VERSION = "snotes-v1-" + "2" * 64
CLAUDE_VERSION = "snotes-v1-" + "3" * 64
CLAWBOT_VERSION = "snotes-v1-" + "4" * 64
FULL_MARKDOWN = "# Full\n\nalpha unique beta\n\nrepeat then repeat\n"


class Step10CloudProposalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-step10-cloud-proposals-"
        )
        self.root = Path(self.temporary.name)
        self.outbox = self.root / "outbox.db"
        initialize_outbox(self.outbox, generalized=True)
        self.mirror_paths = {
            CLAUDE: self.root / "mirror" / CLAUDE / "current.db",
            CLAWBOT: self.root / "mirror" / CLAWBOT / "current.db",
        }
        for path in self.mirror_paths.values():
            path.parent.mkdir(parents=True)
        common = self._record(
            FULL_ID,
            "Fictional full",
            "alpha unique beta repeat then repeat",
            FULL_MARKDOWN,
            FULL_VERSION,
            "full",
        )
        redacted = self._record(
            REDACTED_ID,
            "Fictional redacted",
            "masked [REDACTED]",
            None,
            REDACTED_VERSION,
            "redacted",
        )
        write_actor_mirror(
            (
                common,
                redacted,
                self._record(
                    CLAUDE_ONLY_ID,
                    "Claude only",
                    "claude comet",
                    "# Claude\n\nclaude comet",
                    CLAUDE_VERSION,
                    "full",
                ),
            ),
            self.mirror_paths[CLAUDE],
            actor=CLAUDE,
            source_schema="snotes-markdown-v1",
        )
        write_actor_mirror(
            (
                common,
                redacted,
                self._record(
                    CLAWBOT_ONLY_ID,
                    "Clawbot only",
                    "clawbot comet",
                    "# Clawbot\n\nclawbot comet",
                    CLAWBOT_VERSION,
                    "full",
                ),
            ),
            self.mirror_paths[CLAWBOT],
            actor=CLAWBOT,
            source_schema="snotes-markdown-v1",
        )
        self.http = CloudOutboxHttpApplication(
            self.outbox,
            TOKENS,
            reader_factory=lambda actor: SQLiteCloudMirrorReader(
                self.mirror_paths[actor]
            ),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_application_queues_five_operations_and_reuses_existing_request_first(self) -> None:
        reader = SQLiteCloudMirrorReader(self.mirror_paths[CLAUDE])
        service = CloudProposalService(
            reader, _ActorOutboxGateway(self.outbox, CLAUDE)
        )
        created = service.create_note(
            request_id="app-create",
            title="Fictional create",
            content="# Created\n",
            main_category="Work",
            tags=["fictional"],
        )
        appended = service.append_note(
            request_id="app-append-redacted",
            note_id=REDACTED_ID,
            content="redacted-safe append",
            expected_version=REDACTED_VERSION,
        )
        prepended = service.prepend_note(
            request_id="app-prepend-redacted",
            note_id=REDACTED_ID,
            content="redacted-safe prepend",
            expected_version=REDACTED_VERSION,
        )
        metadata = service.update_note_metadata(
            request_id="app-metadata",
            note_id=FULL_ID,
            expected_version=FULL_VERSION,
            title="Renamed exactly",
            directory_id=77,
            add_tags=["add exact"],
            remove_tags=["remove exact"],
        )
        edited = service.edit_note(
            request_id="app-edit",
            note_id=FULL_ID,
            expected_version=FULL_VERSION,
            edits=[{"old_markdown": "unique", "new_markdown": "changed"}],
        )
        self.assertEqual(
            [
                created["status"], prepended["status"], appended["status"],
                metadata["status"], edited["status"],
            ],
            ["queued", "queued", "queued", "queued", "queued"],
        )
        self.assertEqual(
            [
                created["operation_type"], prepended["operation_type"],
                appended["operation_type"], metadata["operation_type"],
                edited["operation_type"],
            ],
            ["create", "prepend", "append", "update_note_metadata", "edit"],
        )
        stored_metadata = get_operation_status(
            self.outbox, source_actor=CLAUDE, request_id="app-metadata"
        )
        self.assertIsNotNone(stored_metadata)
        assert stored_metadata is not None
        self.assertEqual(
            stored_metadata.operation_payload,
            {
                "title": "Renamed exactly",
                "directory_id": 77,
                "add_tags": ["add exact"],
                "remove_tags": ["remove exact"],
            },
        )

        # Idempotency lookup must happen before the mirror is opened and before
        # the request is prechecked again.
        self.mirror_paths[CLAUDE].unlink()
        duplicate = service.append_note(
            request_id="app-append-redacted",
            note_id=HIDDEN_ID,
            content="now invalid and invisible",
            expected_version="snotes-v1-" + "f" * 64,
        )
        self.assertEqual(duplicate["status"], "queued")
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["note_id"], REDACTED_ID)
        metadata_duplicate = service.update_note_metadata(
            request_id="app-metadata",
            note_id=HIDDEN_ID,
            expected_version="snotes-v1-" + "f" * 64,
            title="would otherwise fail",
        )
        self.assertEqual(metadata_duplicate["status"], "queued")
        self.assertTrue(metadata_duplicate["duplicate"])
        self.assertEqual(metadata_duplicate["note_id"], FULL_ID)

    def test_raw_http_cannot_bypass_actor_mirror_or_operation_prechecks(self) -> None:
        create = self._enqueue(
            CLAUDE,
            "http-create",
            "create",
            payload=self._create_payload("# New\n"),
        )
        append_full = self._enqueue(
            CLAUDE,
            "http-append-full",
            "append",
            note_id=FULL_ID,
            expected_version=FULL_VERSION,
            payload={"content": "full append"},
        )
        append_redacted = self._enqueue(
            CLAUDE,
            "http-append-redacted",
            "append",
            note_id=REDACTED_ID,
            expected_version=REDACTED_VERSION,
            payload={"content": "redacted append"},
        )
        prepend_redacted = self._enqueue(
            CLAUDE,
            "http-prepend-redacted",
            "prepend",
            note_id=REDACTED_ID,
            expected_version=REDACTED_VERSION,
            payload={"content": "redacted prepend"},
        )
        metadata = self._enqueue(
            CLAUDE,
            "http-metadata",
            "update_note_metadata",
            note_id=FULL_ID,
            expected_version=FULL_VERSION,
            payload={"title": "HTTP title", "add_tags": ["new"]},
        )
        edit = self._enqueue(
            CLAUDE,
            "http-edit",
            "edit",
            note_id=FULL_ID,
            expected_version=FULL_VERSION,
            payload={
                "edits": [
                    {"old_markdown": "alpha", "new_markdown": "bravo"}
                ]
            },
        )
        self.assertEqual(
            [
                create.body["status"], prepend_redacted.body["status"],
                append_full.body["status"], append_redacted.body["status"],
                metadata.body["status"], edit.body["status"],
            ],
            ["queued", "queued", "queued", "queued", "queued", "queued"],
        )

        stale = self._enqueue(
            CLAUDE,
            "http-stale",
            "append",
            note_id=FULL_ID,
            expected_version="snotes-v1-" + "0" * 64,
            payload={"content": "stale append"},
        )
        redacted_edit = self._enqueue(
            CLAUDE,
            "http-redacted-edit",
            "edit",
            note_id=REDACTED_ID,
            expected_version=REDACTED_VERSION,
            payload={"edits": [{"old_markdown": "masked", "new_markdown": "x"}]},
        )
        hidden = self._enqueue(
            CLAUDE,
            "http-hidden",
            "append",
            note_id=HIDDEN_ID,
            expected_version=FULL_VERSION,
            payload={"content": "hidden"},
        )
        other_actor = self._enqueue(
            CLAWBOT,
            "http-other-actor-note",
            "append",
            note_id=CLAUDE_ONLY_ID,
            expected_version=CLAUDE_VERSION,
            payload={"content": "cross actor"},
        )
        self.assertEqual(stale.body["status"], "conflict")
        self.assertEqual(redacted_edit.body["status"], "not_found")
        self.assertEqual(hidden.body["status"], "not_found")
        self.assertEqual(other_actor.body["status"], "not_found")
        for request_id, actor in (
            ("http-stale", CLAUDE),
            ("http-redacted-edit", CLAUDE),
            ("http-hidden", CLAUDE),
            ("http-other-actor-note", CLAWBOT),
        ):
            self.assertIsNone(
                get_operation_status(
                    self.outbox, source_actor=actor, request_id=request_id
                )
            )

    def test_new_operations_reject_stale_extra_and_privilege_fields(self) -> None:
        stale_prepend = self._enqueue(
            CLAUDE,
            "stale-prepend",
            "prepend",
            note_id=FULL_ID,
            expected_version="snotes-v1-" + "0" * 64,
            payload={"content": "not queued"},
        )
        stale_metadata = self._enqueue(
            CLAUDE,
            "stale-metadata",
            "update_note_metadata",
            note_id=FULL_ID,
            expected_version="snotes-v1-" + "0" * 64,
            payload={"title": "not queued"},
        )
        self.assertEqual(stale_prepend.body["status"], "conflict")
        self.assertEqual(stale_metadata.body["status"], "conflict")

        forbidden_payloads = (
            {"title": "x", "ai_visibility": "full"},
            {"title": "x", "ai_targets": ["common"]},
            {"title": "x", "main_category": "private"},
            {"title": "x", "tags": ["replacement"]},
        )
        for index, operation_payload in enumerate(forbidden_payloads):
            response = self._enqueue(
                CLAUDE,
                f"metadata-forbidden-{index}",
                "update_note_metadata",
                note_id=FULL_ID,
                expected_version=FULL_VERSION,
                payload=operation_payload,
            )
            self.assertEqual(response.status, 400)
            self.assertEqual(response.body, {"error": "payload_invalid"})

        prepend_extra = self._enqueue(
            CLAUDE,
            "prepend-extra",
            "prepend",
            note_id=FULL_ID,
            expected_version=FULL_VERSION,
            payload={"content": "x", "title": "spoof"},
        )
        self.assertEqual(prepend_extra.status, 400)
        self.assertEqual(prepend_extra.body, {"error": "payload_invalid"})
        delete = self._enqueue(
            CLAUDE,
            "delete-still-blocked",
            "delete",
            note_id=FULL_ID,
            expected_version=FULL_VERSION,
            payload={},
        )
        self.assertEqual(delete.status, 403)
        self.assertEqual(delete.body, {"error": "operation_not_allowed"})
        for request_id in (
            "stale-prepend", "stale-metadata", "prepend-extra",
            "delete-still-blocked",
            *(f"metadata-forbidden-{index}" for index in range(4)),
        ):
            self.assertIsNone(
                get_operation_status(
                    self.outbox, source_actor=CLAUDE, request_id=request_id
                )
            )

    def test_nullable_metadata_fields_are_preserved_in_the_cloud_payload(self) -> None:
        metadata_schema = {
            tool.name: tool for tool in cloud_read_tools(proposal_enabled=True)
        }["update_note_metadata"].inputSchema
        expected_types = {
            "title": {"string", "null"},
            "directory_id": {"integer", "null"},
            "add_tags": {"array", "null"},
            "remove_tags": {"array", "null"},
        }
        for field, field_types in expected_types.items():
            self.assertEqual(
                set(metadata_schema["properties"][field]["type"]),
                field_types,
            )
        self.assertFalse({"anyOf", "oneOf", "allOf"} & set(metadata_schema))

        response = self._enqueue(
            CLAUDE,
            "nullable-metadata",
            "update_note_metadata",
            note_id=FULL_ID,
            expected_version=FULL_VERSION,
            payload={
                "title": None,
                "directory_id": None,
                "add_tags": None,
                "remove_tags": None,
            },
        )
        self.assertEqual(response.body["status"], "queued")
        stored = get_operation_status(
            self.outbox, source_actor=CLAUDE, request_id="nullable-metadata"
        )
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(
            stored.operation_payload,
            {
                "title": None,
                "directory_id": None,
                "add_tags": None,
                "remove_tags": None,
            },
        )

    def test_raw_http_actor_spoofing_and_status_cross_actor_lookup_are_blocked(self) -> None:
        top_level = self._request(
            CLAUDE,
            "/v1/notes/proposals/enqueue",
            {
                **self._envelope(
                    "spoof-top", "create", payload=self._create_payload("body")
                ),
                "source_actor": CLAWBOT,
            },
        )
        nested_payload = self._create_payload("body")
        nested_payload["actor"] = CLAWBOT
        nested = self._enqueue(
            CLAUDE,
            "spoof-nested",
            "create",
            payload=nested_payload,
        )
        self.assertEqual(top_level.body, {"error": "identity_in_body_forbidden"})
        self.assertEqual(nested.body, {"error": "identity_in_body_forbidden"})
        self.assertIsNone(
            get_operation_status(
                self.outbox, source_actor=CLAUDE, request_id="spoof-top"
            )
        )

        queued = self._enqueue(
            CLAUDE,
            "actor-private-status",
            "create",
            payload=self._create_payload("private fictional body"),
        )
        own = self._status(CLAUDE, "actor-private-status")
        other = self._status(CLAWBOT, "actor-private-status")
        self.assertEqual(own.body["submission_uuid"], queued.body["submission_uuid"])
        self.assertEqual(other.body, {"status": "not_found", "request_id": "actor-private-status"})

    def test_idempotency_precedes_vanished_mirror_and_full_queue(self) -> None:
        first = self._enqueue(
            CLAUDE,
            "duplicate-at-cap",
            "append",
            note_id=FULL_ID,
            expected_version=FULL_VERSION,
            payload={"content": "first append"},
        )
        self.assertEqual(first.body["status"], "queued")
        for index in range(49):
            response = self._enqueue(
                CLAUDE,
                f"cap-create-{index}",
                "create",
                payload=self._create_payload(f"fictional {index}"),
            )
            self.assertEqual(response.body["status"], "queued")
        self.mirror_paths[CLAUDE].unlink()
        duplicate = self._enqueue(
            CLAUDE,
            "duplicate-at-cap",
            "append",
            note_id=HIDDEN_ID,
            expected_version="snotes-v1-" + "f" * 64,
            payload={"content": "would fail precheck"},
        )
        self.assertEqual(duplicate.body["status"], "queued")
        self.assertTrue(duplicate.body["duplicate"])
        self.assertEqual(duplicate.body["submission_uuid"], first.body["submission_uuid"])
        over_limit = self._enqueue(
            CLAUDE,
            "cap-overflow",
            "create",
            payload=self._create_payload("one too many"),
        )
        self.assertEqual(over_limit.body["status"], "queue_full")
        self.assertIsNone(
            get_operation_status(
                self.outbox, source_actor=CLAUDE, request_id="cap-overflow"
            )
        )

    def test_exact_edit_uniqueness_overlap_and_dialect_prechecks(self) -> None:
        repeated = self._enqueue(
            CLAUDE,
            "edit-repeated",
            "edit",
            note_id=FULL_ID,
            expected_version=FULL_VERSION,
            payload={"edits": [{"old_markdown": "repeat", "new_markdown": "once"}]},
        )
        overlap = self._enqueue(
            CLAUDE,
            "edit-overlap",
            "edit",
            note_id=FULL_ID,
            expected_version=FULL_VERSION,
            payload={
                "edits": [
                    {"old_markdown": "alpha unique", "new_markdown": "one"},
                    {"old_markdown": "unique beta", "new_markdown": "two"},
                ]
            },
        )
        raw_html = self._enqueue(
            CLAUDE,
            "dialect-html",
            "create",
            payload=self._create_payload("<div>unsupported</div>"),
        )
        invalid_size = self._enqueue(
            CLAUDE,
            "dialect-size",
            "create",
            payload=self._create_payload(':size[text]{size="19"}'),
        )
        for response in (repeated, overlap, raw_html, invalid_size):
            self.assertEqual(response.body["status"], "rejected")

        code_literal = self._enqueue(
            CLAUDE,
            "dialect-code",
            "create",
            payload=self._create_payload(
                '`<div>:size[text]{size="19"}</div>`\n\n'
                '```html\n<div>:size[text]{size="19"}</div>\n```'
            ),
        )
        escaped_literal = self._enqueue(
            CLAUDE,
            "dialect-escaped",
            "create",
            payload=self._create_payload(
                '\\<div>literal\\</div> and \\:size[text]{size="19"}'
            ),
        )
        self.assertEqual(code_literal.body["status"], "queued")
        self.assertEqual(escaped_literal.body["status"], "queued")

    def test_mcp_contract_exposes_five_proposals_and_actor_scoped_status(self) -> None:
        tools = cloud_read_tools(proposal_enabled=True)
        self.assertEqual(
            [tool.name for tool in tools],
            [
                "search_notes",
                "get_note",
                "get_note_markdown",
                "list_directories",
                "list_tags",
                "list_ai_metadata_options",
                "create_note",
                "prepend_note",
                "append_note",
                "update_note_metadata",
                "edit_note",
                "get_operation_status",
            ],
        )
        by_name = {tool.name: tool for tool in tools}
        self.assertEqual(
            set(by_name["prepend_note"].inputSchema["required"]),
            {"request_id", "note_id", "content", "expected_version"},
        )
        self.assertEqual(
            set(by_name["append_note"].inputSchema["required"]),
            {"request_id", "note_id", "content", "expected_version"},
        )
        self.assertEqual(
            set(by_name["update_note_metadata"].inputSchema["required"]),
            {"request_id", "note_id", "expected_version"},
        )
        self.assertEqual(
            set(by_name["update_note_metadata"].inputSchema["properties"]),
            {
                "request_id", "note_id", "expected_version", "title",
                "directory_id", "add_tags", "remove_tags",
            },
        )
        self.assertEqual(
            set(by_name["edit_note"].inputSchema["required"]),
            {"request_id", "note_id", "expected_version", "edits"},
        )
        for name in (
            "create_note", "prepend_note", "append_note",
            "update_note_metadata", "edit_note", "get_operation_status",
        ):
            schema = by_name[name].inputSchema
            self.assertFalse(schema["additionalProperties"])
            self.assertFalse({"anyOf", "oneOf", "allOf"} & set(schema))
            self.assertFalse(
                {"actor", "source_actor", "ai_visibility", "ai_targets"}
                & set(schema["properties"])
            )
        self.assertFalse(by_name["create_note"].annotations.readOnlyHint)
        self.assertTrue(by_name["get_operation_status"].annotations.readOnlyHint)

        reader = SQLiteCloudMirrorReader(self.mirror_paths[CLAUDE])
        proposal_service = CloudProposalService(
            reader, _ActorOutboxGateway(self.outbox, CLAUDE)
        )
        read_service = CloudReadService(reader)
        prepended = call_cloud_read_tool(
            read_service,
            "prepend_note",
            {
                "request_id": "mcp-prepend",
                "note_id": REDACTED_ID,
                "content": "prepend exact",
                "expected_version": REDACTED_VERSION,
            },
            proposal_service=proposal_service,
        )
        metadata = call_cloud_read_tool(
            read_service,
            "update_note_metadata",
            {
                "request_id": "mcp-metadata",
                "note_id": FULL_ID,
                "expected_version": FULL_VERSION,
                "title": "MCP exact title",
                "add_tags": ["one"],
            },
            proposal_service=proposal_service,
        )
        self.assertEqual(prepended["operation_type"], "prepend")
        self.assertEqual(metadata["operation_type"], "update_note_metadata")
        spoofed = call_cloud_read_tool(
            read_service,
            "update_note_metadata",
            {
                "request_id": "mcp-metadata-spoofed",
                "note_id": FULL_ID,
                "expected_version": FULL_VERSION,
                "title": "not queued",
                "source_actor": CLAWBOT,
            },
            proposal_service=proposal_service,
        )
        self.assertEqual(spoofed["status"], "rejected")
        self.assertEqual(spoofed["reason"], "identity_in_body_forbidden")
        stored_metadata = get_operation_status(
            self.outbox, source_actor=CLAUDE, request_id="mcp-metadata"
        )
        self.assertIsNotNone(stored_metadata)
        assert stored_metadata is not None
        self.assertEqual(
            stored_metadata.operation_payload,
            {"title": "MCP exact title", "add_tags": ["one"]},
        )
        result = call_cloud_read_tool(
            read_service,
            "delete_note",
            {"request_id": "blocked-delete_note"},
            proposal_service=proposal_service,
        )
        self.assertEqual(result["status"], "rejected")
        self.assertIn("operation_not_allowed", result["reason"])
        self.assertIsNone(
            get_operation_status(
                self.outbox,
                source_actor=CLAUDE,
                request_id="blocked-delete_note",
            )
        )

    def test_terminal_receipt_is_visible_only_to_own_actor(self) -> None:
        queued = self._enqueue(
            CLAUDE,
            "terminal-status",
            "create",
            payload=self._create_payload("terminal fictional body"),
        )
        exported = export_unacknowledged(self.outbox)
        selected = next(
            item for item in exported if item.submission_uuid == queued.body["submission_uuid"]
        )
        acknowledge_submission(
            self.outbox,
            submission_uuid=selected.submission_uuid,
            local_receipt_id="fictional-local-receipt",
        )
        record_terminal_receipt(
            self.outbox,
            source_actor=CLAUDE,
            request_id="terminal-status",
            terminal_state="accepted",
            receipt_payload={"formal_note_id": 4242},
        )
        own = self._status(CLAUDE, "terminal-status")
        other = self._status(CLAWBOT, "terminal-status")
        self.assertEqual(own.body["status"], "accepted")
        self.assertEqual(own.body["receipt"], {"formal_note_id": 4242})
        self.assertEqual(other.body["status"], "not_found")

    def test_fifty_thousand_unicode_characters_fit_the_http_contract(self) -> None:
        content = "星" * 50_000
        envelope = self._envelope(
            "unicode-limit", "create", payload=self._create_payload(content)
        )
        encoded = json.dumps(
            envelope, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.assertGreater(len(encoded), 128 * 1024)
        self.assertLess(len(encoded), DEFAULT_MAX_BODY_BYTES)
        response = self.http.handle(
            "POST",
            "/v1/notes/proposals/enqueue",
            headers={"Authorization": "Bearer fictional-claude-token"},
            body=encoded,
        )
        self.assertEqual(response.body["status"], "queued")
        stored = get_operation_status(
            self.outbox, source_actor=CLAUDE, request_id="unicode-limit"
        )
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.operation_payload["content"], content)

    def _enqueue(
        self,
        actor: str,
        request_id: str,
        operation_type: str,
        *,
        note_id: int | None = None,
        expected_version: str | None = None,
        payload: dict,
    ):
        return self._request(
            actor,
            "/v1/notes/proposals/enqueue",
            self._envelope(
                request_id,
                operation_type,
                note_id=note_id,
                expected_version=expected_version,
                payload=payload,
            ),
        )

    def _status(self, actor: str, request_id: str):
        return self._request(
            actor,
            "/v1/notes/proposals/status",
            {"request_id": request_id},
        )

    def _request(self, actor: str, path: str, payload: dict):
        token = next(token for token, mapped in TOKENS.items() if mapped == actor)
        return self.http.handle(
            "POST",
            path,
            headers={"Authorization": f"Bearer {token}"},
            body=json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8"),
        )

    @staticmethod
    def _envelope(
        request_id: str,
        operation_type: str,
        *,
        note_id: int | None = None,
        expected_version: str | None = None,
        payload: dict,
    ) -> dict:
        return {
            "request_id": request_id,
            "operation_type": operation_type,
            "note_id": note_id,
            "expected_version": expected_version,
            "payload": payload,
        }

    @staticmethod
    def _create_payload(content: str) -> dict:
        return {
            "title": "Fictional Cloud proposal",
            "content": content,
            "directory_id": None,
            "main_category": None,
            "sub_category": None,
            "tags": [],
        }

    @staticmethod
    def _record(
        note_id: int,
        title: str,
        safe_text: str,
        safe_markdown: str | None,
        version: str,
        visibility: str,
    ) -> ActorMirrorRecord:
        return ActorMirrorRecord(
            source_note_id=note_id,
            title=title,
            safe_text=safe_text,
            safe_markdown=safe_markdown,
            directory="Fictional / Step10",
            tags=("fictional",),
            source_updated_at="2026-10-04T12:00:00+08:00",
            source_version=version,
            visibility=visibility,
            redaction_count=1 if visibility == "redacted" else 0,
            ai_visibility=visibility,
            ai_targets=("common",),
        )


if __name__ == "__main__":
    unittest.main()
