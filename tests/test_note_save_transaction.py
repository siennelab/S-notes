"""Atomic ordinary note body-and-tags save regressions."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.snotes.application.note_service import NoteService
from src.snotes.application.tag_service import TagService
from src.snotes.domain.errors import NoteRevisionConflictError
from src.snotes.domain.models import NoteLocation
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database


class NoteSaveTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="snotes-note-save-transaction-")
        self.addCleanup(temporary.cleanup)
        self.connections = initialize_database(Path(temporary.name) / "fixture.db")
        self.now = datetime.fromisoformat("2026-09-28T09:00:00+08:00")
        clock = lambda: self.now
        directories = SQLiteDirectoriesRepository(self.connections, clock=clock)
        self.repository = SQLiteNotesRepository(
            self.connections,
            directories,
            clock=clock,
        )
        self.tags_repository = SQLiteTagsRepository(self.connections, clock=clock)
        self.notes = NoteService(self.repository)
        self.tags = TagService(self.tags_repository)

    def advance_clock(self) -> None:
        self.now += timedelta(seconds=1)

    def database_snapshot(self) -> str:
        with self.connections.read() as connection:
            return "\n".join(connection.iterdump())

    def test_body_and_changed_tags_commit_together(self) -> None:
        note = self.notes.create_note()
        self.advance_clock()

        saved = self.notes.save_note(
            note.id,
            title="Atomic",
            content_markdown="saved body\n",
            location=NoteLocation("Project", "Ticket 2"),
            expected_revision=note.revision,
            tags_text="One, #two，one",
        )

        self.assertEqual(saved.content_markdown, "saved body\n")
        self.assertEqual(saved.tags, "one, two")
        self.assertEqual(self.tags.get_note_tags(note.id), ["one", "two"])
        self.assertEqual(saved.revision, note.revision + 2)
        self.assertGreater(saved.updated_at, note.updated_at)

    def test_partial_tag_write_failure_rolls_back_and_same_revision_retries(self) -> None:
        note = self.notes.create_note()
        note = self.notes.save_note(
            note.id,
            title="Before",
            content_markdown="before body\n",
            location=note.location,
            expected_revision=note.revision,
            tags_text="before, keep",
        )
        before_tags = self.tags.get_note_tags(note.id)
        before_database = self.database_snapshot()
        original_replace = SQLiteTagsRepository.replace_for_note_in_connection

        def fail_after_replacing(connection, note_id, tags, timestamp):
            original_replace(connection, note_id, tags, timestamp)
            raise RuntimeError("synthetic tag write failure")

        self.advance_clock()
        with patch.object(
            SQLiteTagsRepository,
            "replace_for_note_in_connection",
            side_effect=fail_after_replacing,
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic tag write failure"):
                self.notes.save_note(
                    note.id,
                    title="After",
                    content_markdown="after body\n",
                    location=NoteLocation("Must roll back", "Ticket 2"),
                    expected_revision=note.revision,
                    tags_text="after, keep",
                )

        self.assertEqual(self.notes.get_note(note.id), note)
        self.assertEqual(self.tags.get_note_tags(note.id), before_tags)
        self.assertEqual(self.database_snapshot(), before_database)

        retried = self.notes.save_note(
            note.id,
            title="After",
            content_markdown="after body\n",
            location=NoteLocation("Must roll back", "Ticket 2"),
            expected_revision=note.revision,
            tags_text="after, keep",
        )
        self.assertEqual(retried.content_markdown, "after body\n")
        self.assertEqual(self.tags.get_note_tags(note.id), ["after", "keep"])
        self.assertEqual(retried.revision, note.revision + 2)

    def test_real_stale_revision_still_rejects_body_and_tags(self) -> None:
        original = self.notes.create_note()
        remote = self.notes.save_note(
            original.id,
            title="Remote",
            content_markdown="remote body\n",
            location=original.location,
            expected_revision=original.revision,
            tags_text="remote",
        )

        with self.assertRaises(NoteRevisionConflictError):
            self.notes.save_note(
                original.id,
                title="Stale",
                content_markdown="stale body\n",
                location=NoteLocation("Must roll back", "Stale"),
                expected_revision=original.revision,
                tags_text="stale",
            )

        self.assertEqual(self.notes.get_note(original.id), remote)
        self.assertEqual(self.tags.get_note_tags(original.id), ["remote"])

    def test_optional_empty_and_unchanged_tags_preserve_existing_semantics(self) -> None:
        created = self.notes.create_note()
        tagged = self.notes.save_note(
            created.id,
            title="Tagged",
            content_markdown="tagged\n",
            location=created.location,
            expected_revision=created.revision,
            tags_text="alpha, beta",
        )
        self.assertEqual(tagged.revision, created.revision + 2)

        unchanged = self.notes.save_note(
            tagged.id,
            title="Unchanged order",
            content_markdown="body changed\n",
            location=tagged.location,
            expected_revision=tagged.revision,
            tags_text="beta, alpha",
        )
        self.assertEqual(unchanged.revision, tagged.revision + 1)
        self.assertEqual(unchanged.tags, tagged.tags)

        preserved = self.notes.save_note(
            unchanged.id,
            title="No tags argument",
            content_markdown="body changed again\n",
            location=unchanged.location,
            expected_revision=unchanged.revision,
        )
        self.assertEqual(preserved.revision, unchanged.revision + 1)
        self.assertEqual(preserved.tags, tagged.tags)

        cleared = self.notes.save_note(
            preserved.id,
            title="Cleared",
            content_markdown="body and tags changed\n",
            location=preserved.location,
            expected_revision=preserved.revision,
            tags_text="",
        )
        self.assertEqual(cleared.revision, preserved.revision + 2)
        self.assertEqual(cleared.tags, "")
        self.assertEqual(self.tags.get_note_tags(cleared.id), [])
