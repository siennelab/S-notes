"""Targeted fictional coverage for GreenCloud metadata discovery."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from src.services.actor_mirror_publish_service import (
    ActorMirrorPublisher,
    PublishReceipt,
)
from src.snotes.application.ai_access_metadata_service import (
    AiAccessMetadataService,
)
from src.snotes.application.ai_actor_registry import AiTargetDefinition
from src.snotes.application.directory_service import DirectoryService
from src.snotes.application.local_mcp_write_service import note_version_token
from src.snotes.application.note_service import NoteService
from src.snotes.application.tag_service import TagService
from src.snotes.domain.models import NoteLocation
from src.snotes.domain.actor_mirror import ActorMirrorError
from src.snotes.storage.actor_mirror import verify_actor_mirror
from src.snotes.storage.cloud_read import SQLiteCloudMirrorReader
from src.snotes.storage.repositories.directories import (
    SQLiteDirectoriesRepository,
)
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.application.cloud_read_service import CloudReadService
from src.snotes.transport.mcp.cloud_read_server import (
    call_cloud_read_tool,
    cloud_read_tools,
)


class _FixtureTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def publish(
        self,
        *,
        actor: str,
        mirror_path: Path,
        file_sha256: str,
        content_hash: str,
    ) -> PublishReceipt:
        del file_sha256
        self.calls.append((actor, content_hash))
        with closing(sqlite3.connect(mirror_path)) as connection:
            note_count = int(
                connection.execute(
                    "SELECT meta_value FROM mirror_metadata "
                    "WHERE meta_key = 'note_count'"
                ).fetchone()[0]
            )
        return PublishReceipt(actor, content_hash, note_count)


class GreenCloudMetadataDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(
            prefix="snotes-gc35-metadata-discovery-"
        )
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / "fictional-source.db"
        self.connections = initialize_database(self.database)
        directory_repository = SQLiteDirectoriesRepository(self.connections)
        note_repository = SQLiteNotesRepository(
            self.connections, directory_repository
        )
        tag_repository = SQLiteTagsRepository(self.connections)
        self.notes = NoteService(note_repository)
        self.tags = TagService(tag_repository)
        self.metadata = AiAccessMetadataService(note_repository)
        self.directories = DirectoryService(directory_repository, note_repository)

        self.rules = self.root / "fictional-redaction-rules.json"
        self.rules.write_text(
            json.dumps({"FICTIONAL_SECRET": "[MASKED]"}),
            encoding="utf-8",
        )
        self.empty_directory = self.directories.create_directory(
            "Empty catalog directory"
        )
        self._insert_unused_tag("unused-catalog-tag")

        self.full = self._note(
            title="Fictional common full",
            markdown="# Full\n\nfull metadata body",
            location=NoteLocation("Fictional Formal", "Leaf"),
            tags=("fictional-full",),
            visibility="full",
            targets=("common",),
        )
        self.redacted = self._note(
            title="Fictional common redacted",
            markdown="FICTIONAL_SECRET redacted metadata body",
            location=NoteLocation("Fictional Formal", "Redacted Leaf"),
            tags=("fictional-redacted",),
            visibility="redacted",
            targets=("common",),
        )
        self.hidden = self._note(
            title="Fictional hidden discovery canary",
            markdown="hidden discovery canary body",
            location=NoteLocation("Private Formal", "Hidden"),
            tags=("hidden-catalog-tag",),
            visibility="hidden",
            targets=("common",),
        )
        self.other_actor = self._note(
            title="Fictional Clawbot-only discovery canary",
            markdown="clawbot exclusive discovery body",
            location=NoteLocation("Private Formal", "Other actor"),
            tags=("other-actor-catalog-tag",),
            visibility="full",
            targets=("clawbot",),
        )
        self.delimiter_category = self._note(
            title="Fictional direct category source",
            markdown="category delimiter preservation body",
            location=NoteLocation(
                "Formal / Main kept whole", "Leaf / kept whole"
            ),
            tags=("delimiter-category",),
            visibility="full",
            targets=("common",),
        )

        self.transport = _FixtureTransport()
        self.publisher = ActorMirrorPublisher(
            source_database=self.database,
            redaction_rules=self.rules,
            mirror_directory=self.root / "mirrors",
            state_path=self.root / "publisher-state.json",
            lock_path=self.root / "publisher.lock",
            transport=self.transport,
        )

    def _note(
        self,
        *,
        title: str,
        markdown: str,
        location: NoteLocation,
        tags: tuple[str, ...],
        visibility: str,
        targets: tuple[str, ...],
    ):
        blank = self.notes.create_note(location)
        saved = self.notes.save_note(
            blank.id,
            title=title,
            content_markdown=markdown,
            location=location,
            expected_revision=blank.revision,
        )
        self.tags.set_note_tags(saved.id, tags)
        self.metadata.set_note_visibility(saved.id, visibility)
        self.metadata.set_note_targets(saved.id, targets)
        return self.notes.get_note(saved.id)

    def _insert_unused_tag(self, name: str) -> None:
        with self.connections.write() as connection:
            connection.execute(
                "INSERT INTO tags(name, created_at) VALUES (?, ?)",
                (name, "2026-10-04T12:00:00+08:00"),
            )

    def _claude_service(self) -> CloudReadService:
        return CloudReadService(
            SQLiteCloudMirrorReader(
                self.root / "mirrors" / "claude-code.db"
            )
        )

    def test_actor_read_metadata_redaction_and_non_disclosure(self) -> None:
        results = self.publisher.publish()
        self.assertEqual(
            [result.status for result in results],
            ["published", "published", "published"],
        )
        service = self._claude_service()

        full = service.get_note(self.full.id)
        self.assertEqual(full["status"], "ok")
        self.assertEqual(full["main_category"], "Fictional Formal")
        self.assertEqual(full["sub_category"], "Leaf")
        self.assertEqual(full["tags"], ["fictional-full"])
        self.assertEqual(full["ai_visibility"], "full")
        self.assertEqual(full["ai_targets"], ["common"])
        self.assertIn("full metadata body", full["safe_text"])
        self.assertEqual(
            full["version"], note_version_token(self.full.id, self.full.updated_at)
        )
        full_search = service.search_notes("full metadata body")
        self.assertEqual(full_search["result_count"], 1)
        self.assertEqual(
            {
                key: full_search["results"][0][key]
                for key in (
                    "main_category",
                    "sub_category",
                    "tags",
                    "ai_visibility",
                    "ai_targets",
                    "version",
                )
            },
            {
                "main_category": "Fictional Formal",
                "sub_category": "Leaf",
                "tags": ["fictional-full"],
                "ai_visibility": "full",
                "ai_targets": ["common"],
                "version": full["version"],
            },
        )
        markdown = service.get_note_markdown(self.full.id)
        self.assertEqual(markdown["status"], "ok")
        self.assertEqual(markdown["markdown"], "# Full\n\nfull metadata body")
        self.assertEqual(markdown["version"], full["version"])

        delimiter = service.get_note(self.delimiter_category.id)
        self.assertEqual(delimiter["status"], "ok")
        self.assertEqual(delimiter["main_category"], "Formal / Main kept whole")
        self.assertEqual(delimiter["sub_category"], "Leaf / kept whole")
        self.assertEqual(
            delimiter["directory"],
            "Formal / Main kept whole / Leaf / kept whole",
        )

        redacted = service.get_note(self.redacted.id)
        self.assertEqual(redacted["status"], "ok")
        self.assertEqual(redacted["visibility"], "redacted")
        self.assertEqual(redacted["main_category"], "Fictional Formal")
        self.assertEqual(redacted["sub_category"], "Redacted Leaf")
        self.assertEqual(redacted["tags"], ["fictional-redacted"])
        self.assertEqual(redacted["ai_visibility"], "redacted")
        self.assertEqual(redacted["ai_targets"], ["common"])
        self.assertNotIn("FICTIONAL_SECRET", redacted["safe_text"])
        self.assertIn("[MASKED]", redacted["safe_text"])
        self.assertEqual(
            service.get_note_markdown(self.redacted.id),
            {"status": "not_found", "note_id": self.redacted.id},
        )

        for note, query in (
            (self.hidden, "hidden discovery canary"),
            (self.other_actor, "clawbot exclusive discovery"),
        ):
            self.assertEqual(
                service.get_note(note.id),
                {"status": "not_found", "note_id": note.id},
            )
            search = service.search_notes(query)
            self.assertEqual(search["results"], [])
            self.assertEqual(search["result_count"], 0)

    def test_catalog_uses_formal_source_and_mcp_tools_reject_identity_input(self) -> None:
        self.publisher.publish()
        service = self._claude_service()

        directories = call_cloud_read_tool(service, "list_directories", {})
        self.assertEqual(directories["status"], "ok")
        empty = next(
            item
            for item in directories["directories"]
            if item["directory_id"] == self.empty_directory.id
        )
        self.assertEqual(
            empty,
            {
                "directory_id": self.empty_directory.id,
                "name": "Empty catalog directory",
                "parent_id": None,
                "main_category": "Empty catalog directory",
                "sub_category": "",
                "path": ["Empty catalog directory"],
                "writable": True,
            },
        )

        tags = call_cloud_read_tool(service, "list_tags", {})
        self.assertEqual(tags["status"], "ok")
        self.assertIn({"name": "unused-catalog-tag"}, tags["tags"])

        options = call_cloud_read_tool(
            service, "list_ai_metadata_options", {}
        )
        self.assertEqual(options["status"], "ok")
        self.assertEqual(
            options["ai_visibility_options"],
            [
                {"value": value, "label": label}
                for value, label in self.metadata.available_visibility_options()
            ],
        )
        self.assertEqual(
            options["ai_targets"],
            [
                {
                    "target_id": target.target_id,
                    "display_name": target.display_name,
                }
                for target in self.metadata.available_targets()
            ],
        )

        tools = {tool.name: tool for tool in cloud_read_tools()}
        for name in (
            "list_directories",
            "list_tags",
            "list_ai_metadata_options",
        ):
            self.assertEqual(tools[name].inputSchema["properties"], {})
            self.assertFalse(tools[name].inputSchema["additionalProperties"])
            self.assertTrue(tools[name].annotations.readOnlyHint)
            self.assertEqual(
                call_cloud_read_tool(service, name, {"actor": "clawbot"}),
                {"status": "failed", "reason": "invalid_request"},
            )

    def test_registry_only_change_republishes_without_changing_note_version(self) -> None:
        first = self.publisher.publish()
        first_hashes = [result.content_hash for result in first]
        version_before = note_version_token(self.full.id, self.full.updated_at)

        dynamic = AiTargetDefinition(
            "fixture-dynamic", "Fixture Dynamic", "fixture_dynamic"
        )
        available_targets = self.metadata.available_targets() + (dynamic,)
        with patch.object(
            AiAccessMetadataService,
            "available_targets",
            return_value=available_targets,
        ):
            second = self.publisher.publish()
        self.assertEqual(
            [result.status for result in second],
            ["published", "published", "published"],
        )
        self.assertNotEqual(
            [result.content_hash for result in second], first_hashes
        )
        current = self.notes.get_note(self.full.id)
        self.assertEqual(
            note_version_token(current.id, current.updated_at), version_before
        )
        options = self._claude_service().list_ai_metadata_options()
        self.assertIn(
            {
                "target_id": "fixture-dynamic",
                "display_name": "Fixture Dynamic",
            },
            options["ai_targets"],
        )

    def test_structurally_valid_catalog_tamper_fails_hash_verification(self) -> None:
        self.publisher.publish()
        mirror = self.root / "mirrors" / "claude-code.db"
        with closing(sqlite3.connect(mirror)) as connection, connection:
            row = connection.execute(
                "SELECT meta_value FROM mirror_metadata "
                "WHERE meta_key = 'metadata_catalog'"
            ).fetchone()
            catalog = json.loads(row[0])
            catalog["tags"].append("valid-but-unhashed-tamper")
            connection.execute(
                "UPDATE mirror_metadata SET meta_value = ? "
                "WHERE meta_key = 'metadata_catalog'",
                (
                    json.dumps(
                        catalog,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )

        with self.assertRaises(ActorMirrorError) as caught:
            verify_actor_mirror(mirror, expected_actor="claude-code")
        self.assertEqual(caught.exception.code, "hash_mismatch")


if __name__ == "__main__":
    unittest.main()
