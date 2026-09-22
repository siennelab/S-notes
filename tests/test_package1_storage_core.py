from __future__ import annotations

import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.snotes.application.directory_service import DirectoryService
from src.snotes.application.note_service import NoteService
from src.snotes.application.tag_service import TagService
from src.snotes.domain.errors import NoteRevisionConflictError
from src.snotes.domain.models import NoteLocation
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database


FIXED_NOW = datetime(2026, 8, 7, 12, 34, 56, tzinfo=ZoneInfo("Asia/Shanghai"))


class Package1StorageCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-r2-package1-")
        self.database_path = Path(self.temporary.name) / "fictional.db"
        self.connections = initialize_database(self.database_path)
        clock = lambda: FIXED_NOW
        self.directories_repository = SQLiteDirectoriesRepository(
            self.connections, clock=clock
        )
        self.notes_repository = SQLiteNotesRepository(
            self.connections,
            self.directories_repository,
            clock=clock,
            uuid_factory=self.next_uuid,
        )
        self.tags_repository = SQLiteTagsRepository(self.connections, clock=clock)
        self.notes = NoteService(self.notes_repository)
        self.directories = DirectoryService(self.directories_repository)
        self.tags = TagService(self.tags_repository)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def next_uuid(self) -> uuid.UUID:
        value = getattr(self, "uuid_value", 0) + 1
        self.uuid_value = value
        return uuid.UUID(int=value, version=4)

    def test_create_get_and_connection_contract(self) -> None:
        note = self.notes.create_note()
        self.assertEqual(note.revision, 1)
        self.assertEqual(uuid.UUID(note.note_uuid).version, 4)
        self.assertEqual(note.location, NoteLocation())
        self.assertEqual(note.ai_visibility, "hidden")
        self.assertEqual(self.notes.get_note(note.id), note)
        with self.connections.read() as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertNotEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_save_advances_revision_and_preserves_unowned_state(self) -> None:
        note = self.notes.create_note()
        self.tags.set_note_tags(note.id, ["保留标签"])
        before = self.notes.get_note(note.id)
        self.assertIsNotNone(before)
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "UPDATE notes SET ai_visibility = ?, trash_group_id = ? WHERE id = ?",
                ("redacted", "FICTIONAL_SENTINEL", note.id),
            )
            connection.commit()
        saved = self.notes.save_note(
            note.id,
            title="虚构保存标题",
            content_markdown="<p>虚构保存正文</p>",
            location=NoteLocation("虚构项目", "子目录"),
            expected_revision=before.revision,
        )
        self.assertEqual(saved.revision, before.revision + 1)
        self.assertEqual(saved.content_markdown, "<p>虚构保存正文</p>")
        self.assertEqual(saved.tags, "保留标签")
        self.assertEqual(saved.ai_visibility, "redacted")
        self.assertEqual(saved.trash_group_id, "FICTIONAL_SENTINEL")
        self.assertEqual(saved.note_uuid, note.note_uuid)
        with closing(sqlite3.connect(self.database_path)) as connection:
            body_columns = connection.execute(
                "SELECT content_markdown FROM notes WHERE id = ?", (note.id,)
            ).fetchone()
        self.assertEqual(body_columns, (saved.content_markdown,))

    def test_stale_revision_is_rejected_without_side_effects(self) -> None:
        note = self.notes.create_note()
        first = self.notes.save_note(
            note.id,
            title="虚构较新标题",
            content_markdown="<p>虚构较新正文</p>",
            location=NoteLocation(),
            expected_revision=note.revision,
        )
        with self.assertRaises(NoteRevisionConflictError):
            self.notes.save_note(
                note.id,
                title="虚构过期标题",
                content_markdown="<p>虚构过期正文</p>",
                location=NoteLocation("不应创建的冲突目录", "无"),
                expected_revision=note.revision,
            )
        stored = self.notes.get_note(note.id)
        self.assertEqual(stored, first)
        self.assertNotIn(
            "不应创建的冲突目录",
            {directory.name for directory in self.directories.list_directories()},
        )

    def test_directory_create_and_note_move(self) -> None:
        root = self.directories.create_directory("虚构目录甲")
        child = self.directories.create_directory("虚构目录乙", root.id)
        with self.assertRaisesRegex(ValueError, "最多支持两层"):
            self.directories.create_directory("第三层", child.id)
        note = self.notes.create_note()
        moved = self.notes.move_note(note.id, NoteLocation("虚构目录甲", "虚构目录乙"))
        self.assertEqual(moved.location.values(), self.directories.get_directory_path(child.id))
        self.assertEqual(moved.revision, note.revision + 1)

    def test_tags_normalize_deduplicate_and_advance_revision(self) -> None:
        note = self.notes.create_note()
        normalized = self.tags.set_note_tags(
            note.id, [" #Alpha ", "alpha", "中文", "#中文", "BETA"]
        )
        self.assertEqual(normalized, ["alpha", "中文", "beta"])
        self.assertEqual(self.tags.get_note_tags(note.id), ["alpha", "beta", "中文"])
        stored = self.notes.get_note(note.id)
        self.assertEqual(stored.tags, "alpha, 中文, beta")
        self.assertEqual(stored.revision, note.revision + 1)

    def test_pin_unpin_and_note_trash_lifecycle(self) -> None:
        note = self.notes.create_note()
        pinned = self.notes.set_note_pinned(note.id, True)
        unpinned = self.notes.set_note_pinned(note.id, False)
        self.assertTrue(pinned.is_pinned)
        self.assertFalse(unpinned.is_pinned)
        self.assertEqual(unpinned.revision, note.revision + 2)
        deleted = self.notes.soft_delete_note(note.id)
        self.assertTrue(deleted.is_deleted)
        self.assertIsNotNone(deleted.trash_group_id)
        self.assertIsNone(self.notes.get_note(note.id))
        restored = self.notes.restore_note(note.id)
        self.assertFalse(restored.is_deleted)
        self.assertIsNone(restored.trash_group_id)
        self.notes.soft_delete_note(note.id)
        self.notes.permanently_delete_note(note.id)
        self.assertIsNone(self.notes.get_note(note.id, include_deleted=True))

    def test_directory_tree_trash_group_and_restore(self) -> None:
        root = self.directories.create_directory("虚构树甲")
        child = self.directories.create_directory("虚构树乙", root.id)
        note = self.notes.create_note(NoteLocation("虚构树甲", "虚构树乙"))
        group = self.directories.soft_delete_directory(root.id)
        trash = self.directories.list_trash()
        self.assertEqual(len(trash.directories), 2)
        self.assertEqual(len(trash.notes), 1)
        self.assertEqual({item.trash_group_id for item in trash.directories}, {group})
        self.assertEqual(trash.notes[0].trash_group_id, group)
        self.assertIsNone(trash.notes[0].directory_id)
        self.directories.restore_directory(root.id)
        restored = self.notes.get_note(note.id)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.location, note.location)
        self.assertIsNone(restored.trash_group_id)
        self.assertEqual(restored.revision, note.revision + 2)

    def test_empty_trash_removes_only_deleted_core_rows(self) -> None:
        active = self.notes.create_note()
        independent = self.notes.create_note()
        self.notes.soft_delete_note(independent.id)
        root = self.directories.create_directory("虚构待清空目录")
        nested = self.notes.create_note(NoteLocation("虚构待清空目录", "无"))
        self.directories.soft_delete_directory(root.id)
        result = self.directories.empty_trash()
        self.assertEqual(result.note_count, 2)
        self.assertEqual(result.directory_count, 1)
        self.assertEqual(result.root_count, 2)
        self.assertIsNone(self.notes.get_note(independent.id, include_deleted=True))
        self.assertIsNone(self.notes.get_note(nested.id, include_deleted=True))
        self.assertIsNotNone(self.notes.get_note(active.id))

    def test_batch_move_rolls_back_if_any_note_is_missing(self) -> None:
        first = self.notes.create_note()
        second = self.notes.create_note()
        with self.assertRaisesRegex(ValueError, "不存在或已删除"):
            self.notes.move_notes(
                [first.id, 999999, second.id], NoteLocation("不应留下的批量目录")
            )
        self.assertEqual(self.notes.get_note(first.id).location, NoteLocation())
        self.assertEqual(self.notes.get_note(second.id).location, NoteLocation())
        self.assertNotIn(
            "不应留下的批量目录",
            {directory.name for directory in self.directories.list_directories()},
        )


if __name__ == "__main__":
    unittest.main()
