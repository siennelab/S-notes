"""Regressions for reachable metadata writes after a GUI body save."""
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.local_mcp_write_service import note_version_token
from src.snotes.application.note_service import NoteService
from src.snotes.application.tag_service import TagService
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database


class NoteTimestampTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="snotes-2d-timestamps-")
        self.addCleanup(temporary.cleanup)
        self.connections = initialize_database(Path(temporary.name) / "fixture.db")
        self.now = datetime.fromisoformat("2026-09-23T09:00:00+08:00")
        clock = lambda: self.now
        directories = SQLiteDirectoriesRepository(self.connections, clock=clock)
        repository = SQLiteNotesRepository(self.connections, directories, clock=clock)
        self.notes = NoteService(repository)
        self.tags = TagService(SQLiteTagsRepository(self.connections, clock=clock))
        self.metadata = AiAccessMetadataService(repository)

    def assert_progress(self, before, after):
        self.assertEqual(after.revision, before.revision + 1)
        self.assertGreater(datetime.fromisoformat(after.updated_at),
                           datetime.fromisoformat(before.updated_at))
        self.assertNotEqual(note_version_token(before.id, before.updated_at),
                            note_version_token(after.id, after.updated_at))

    def test_gui_like_body_then_tags_never_restores_initial_token(self):
        initial = self.notes.create_note()
        self.now += timedelta(microseconds=123456)
        body = self.notes.save_note(initial.id, title="Fixture", content_markdown="changed\n",
                                    location=initial.location, expected_revision=initial.revision)
        self.tags.set_note_tags_from_text(initial.id, "changed-tag")
        final = self.notes.get_note(initial.id)
        self.assertNotEqual(initial.content_markdown, final.content_markdown)
        self.assertEqual(final.content_markdown, body.content_markdown)
        self.assert_progress(initial, body)
        self.assert_progress(body, final)
        self.assertEqual(len({note_version_token(n.id, n.updated_at)
                              for n in (initial, body, final)}), 3)

    def test_rapid_metadata_updates_and_clock_rollback(self):
        for kind in ("tags", "visibility", "targets"):
            with self.subTest(kind=kind):
                before = self.notes.create_note()
                tokens = {note_version_token(before.id, before.updated_at)}
                for index in range(3):
                    # Same clock tick twice, then the wall clock goes backwards.
                    if index == 2:
                        self.now -= timedelta(seconds=1)
                    if kind == "tags":
                        self.tags.set_note_tags_from_text(before.id, f"tag-{index}")
                    elif kind == "visibility":
                        self.metadata.set_note_visibility(before.id, ("full", "hidden", "redacted")[index])
                    else:
                        self.metadata.set_note_targets(before.id, ("clawbot",) if index != 1 else ("yan",))
                    after = self.notes.get_note(before.id)
                    self.assert_progress(before, after)
                    self.assertEqual(after.content_markdown, before.content_markdown)
                    tokens.add(note_version_token(after.id, after.updated_at))
                    before = after
                self.assertEqual(len(tokens), 4)

    def test_unchanged_gui_tags_are_a_noop(self):
        note = self.notes.create_note()
        self.tags.set_note_tags_from_text(note.id, "")
        self.assertEqual(self.notes.get_note(note.id), note)
