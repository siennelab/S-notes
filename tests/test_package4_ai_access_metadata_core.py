from __future__ import annotations

import tempfile
import unittest
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from src.snotes.application.ai_access_metadata_service import (
    AiAccessMetadataService,
)
from src.snotes.application.note_service import NoteService
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.schema import initialize_database


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(
            2026,
            8,
            9,
            10,
            0,
            0,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        )

    def __call__(self) -> datetime:
        return self.value

    def advance(self) -> None:
        self.value += timedelta(seconds=1)


class Package4AiAccessMetadataCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-r2-package4-metadata-"
        )
        self.addCleanup(self.temporary.cleanup)
        self.database_path = Path(self.temporary.name) / "fictional.db"
        self.connections = initialize_database(self.database_path)
        self.clock = MutableClock()
        directories = SQLiteDirectoriesRepository(
            self.connections,
            clock=self.clock,
        )
        repository = SQLiteNotesRepository(
            self.connections,
            directories,
            clock=self.clock,
            uuid_factory=lambda: uuid.UUID(int=41, version=4),
        )
        self.notes = NoteService(repository)
        self.metadata = AiAccessMetadataService(repository)
        self.note = self.notes.create_note()

    def test_reads_default_visibility_and_empty_targets(self) -> None:
        metadata = self.metadata.get_note_metadata(self.note.id)

        self.assertEqual(metadata.visibility, "hidden")
        self.assertEqual(metadata.target_ids, ())
        self.assertEqual(metadata.target_names, ())
        self.assertEqual(metadata.legacy_target_ids, ())
        self.assertEqual(metadata.legacy_target_names, ())
        self.assertEqual(metadata.revision, 1)

    def test_visibility_preserves_r1_codes_aliases_and_fail_closed_default(self) -> None:
        self.clock.advance()
        full = self.metadata.set_note_visibility(self.note.id, "AI完整可见")
        self.assertEqual(full.visibility, "full")
        self.assertEqual(full.revision, 2)

        self.clock.advance()
        redacted = self.metadata.set_note_visibility(self.note.id, "redacted")
        self.assertEqual(redacted.visibility, "redacted")
        self.assertEqual(redacted.revision, 3)

        self.clock.advance()
        unknown = self.metadata.set_note_visibility(
            self.note.id,
            "FICTIONAL_UNKNOWN_VISIBILITY",
        )
        self.assertEqual(unknown.visibility, "hidden")
        self.assertEqual(unknown.revision, 4)

    def test_five_active_targets_round_trip_in_registry_order(self) -> None:
        cases = (
            (("通用",), ("common",), ("通用",)),
            (("Ke",), ("ke-den",), ("Ke",)),
            (
                ("Claude Code", "Gale", "Clawbot", "Ke"),
                ("gale", "clawbot", "claude-code", "ke-den"),
                ("Gale", "Clawbot", "Claude Code", "Ke"),
            ),
        )
        revision = self.note.revision
        for values, expected_ids, expected_names in cases:
            with self.subTest(values=values):
                self.clock.advance()
                metadata = self.metadata.set_note_targets(self.note.id, values)
                revision += 1
                self.assertEqual(metadata.target_ids, expected_ids)
                self.assertEqual(metadata.target_names, expected_names)
                self.assertEqual(metadata.revision, revision)
                self.assertEqual(
                    self.metadata.get_note_metadata(self.note.id),
                    metadata,
                )

    def test_unknown_target_is_rejected_without_partial_write(self) -> None:
        self.clock.advance()
        before = self.metadata.set_note_targets(self.note.id, ("gale",))

        self.clock.advance()
        with self.assertRaisesRegex(ValueError, "未知 AI 适用对象"):
            self.metadata.set_note_targets(
                self.note.id,
                ("gale", "deepseek"),
            )

        self.assertEqual(self.metadata.get_note_metadata(self.note.id), before)

    def test_each_successful_metadata_write_advances_revision_and_timestamp(self) -> None:
        initial = self.metadata.get_note_metadata(self.note.id)
        self.clock.advance()
        visibility = self.metadata.set_note_visibility(self.note.id, "hidden")
        self.clock.advance()
        targets = self.metadata.set_note_targets(self.note.id, ())

        self.assertEqual(visibility.revision, initial.revision + 1)
        self.assertEqual(targets.revision, visibility.revision + 1)
        self.assertGreater(visibility.updated_at, initial.updated_at)
        self.assertGreater(targets.updated_at, visibility.updated_at)
        self.assertEqual(self.notes.get_note(self.note.id).revision, targets.revision)

    def test_available_values_are_the_single_shared_r1_catalog(self) -> None:
        self.assertEqual(
            self.metadata.available_visibilities(),
            ("hidden", "full", "redacted"),
        )
        self.assertEqual(
            tuple(item.target_id for item in self.metadata.available_targets()),
            ("common", "gale", "clawbot", "claude-code", "ke-den"),
        )

    def test_legacy_targets_remain_visible_and_survive_active_edits(self) -> None:
        legacy = self.metadata.set_note_targets(
            self.note.id,
            ("codex", "gemini"),
        )
        self.assertEqual(legacy.legacy_target_ids, ("codex", "gemini"))
        self.assertEqual(legacy.legacy_target_names, ("Codex", "Gemini"))

        self.clock.advance()
        visible = self.metadata.set_note_visibility(self.note.id, "full")
        self.assertEqual(visible.target_ids, ("codex", "gemini"))

        self.clock.advance()
        edited = self.metadata.set_note_targets(self.note.id, ("gale",))
        self.assertEqual(edited.target_ids, ("gale", "codex", "gemini"))
        self.assertEqual(edited.legacy_target_ids, ("codex", "gemini"))

    def test_ke_display_change_updates_catalog_without_changing_stable_id(self) -> None:
        timestamp = self.clock.value.isoformat(timespec="seconds")
        with self.connections.write() as connection:
            connection.execute(
                """
                INSERT INTO ai_targets (
                    target_id, display_name, slug, is_system, is_enabled,
                    created_at, updated_at
                ) VALUES ('ke-den', 'ke-den', 'ke_den', 0, 1, ?, ?)
                """,
                (timestamp, timestamp),
            )

        updated = self.metadata.set_note_targets(self.note.id, ("Ke",))

        self.assertEqual(updated.target_ids, ("ke-den",))
        self.assertEqual(updated.target_names, ("Ke",))
        with self.connections.read() as connection:
            row = connection.execute(
                "SELECT target_id, display_name FROM ai_targets WHERE target_id='ke-den'"
            ).fetchone()
        self.assertEqual((row["target_id"], row["display_name"]), ("ke-den", "Ke"))


if __name__ == "__main__":
    unittest.main()
