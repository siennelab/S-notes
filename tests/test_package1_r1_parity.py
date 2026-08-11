from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from src import database as r1_database
from src.snotes.application.directory_service import DirectoryService
from src.snotes.application.note_service import NoteService
from src.snotes.application.tag_service import TagService
from src.snotes.domain.models import NoteLocation
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database as initialize_r2_database


class ClosingR1Connection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def open_closing_r1_connection(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        database_path,
        timeout=5.0,
        factory=ClosingR1Connection,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


class Package1R1ParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_connect_patch = patch.object(
            r1_database, "_connect", open_closing_r1_connection
        )
        self.database_connect_patch.start()
        self.addCleanup(self.database_connect_patch.stop)
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-package1-parity-")
        root = Path(self.temporary.name)
        self.r1_path = root / "r1.db"
        self.r2_path = root / "r2.db"
        r1_database.initialize_database(self.r1_path)
        connections = initialize_r2_database(self.r2_path)
        directories = SQLiteDirectoriesRepository(connections)
        notes = SQLiteNotesRepository(connections, directories)
        tags = SQLiteTagsRepository(connections)
        self.r2_notes = NoteService(notes)
        self.r2_directories = DirectoryService(directories)
        self.r2_tags = TagService(tags)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_frozen_schema_objects_columns_and_foreign_keys_match(self) -> None:
        self.assertEqual(
            self.schema_signature(self.r1_path),
            self.schema_signature(self.r2_path),
        )
        self.assertEqual(
            self.initial_directory_projection(self.r1_path),
            self.initial_directory_projection(self.r2_path),
        )

    def test_note_directory_tag_pin_delete_restore_sequence_matches(self) -> None:
        location = NoteLocation("虚构对照主目录", "虚构对照子目录", "虚构对照末级")
        r1_note = r1_database.create_note_in_directory(*location.values(), self.r1_path)
        r2_note = self.r2_notes.create_note(location)
        r1_note = r1_database.update_note(
            r1_note["id"],
            title="虚构 parity 标题",
            content_html="<p>虚构 parity 正文</p>",
            database_path=self.r1_path,
        )
        r2_note = self.r2_notes.save_note(
            r2_note.id,
            title="虚构 parity 标题",
            content_html="<p>虚构 parity 正文</p>",
            location=location,
            expected_revision=r2_note.revision,
        )
        r1_database.set_note_tags(r1_note["id"], [" #Alpha ", "alpha", "中文"], self.r1_path)
        self.r2_tags.set_note_tags(r2_note.id, [" #Alpha ", "alpha", "中文"])
        r1_database.set_note_pinned(r1_note["id"], True, self.r1_path)
        self.r2_notes.set_note_pinned(r2_note.id, True)
        destination = NoteLocation("虚构移动主目录", "虚构移动子目录", "无")
        r1_database.update_note_directory(
            r1_note["id"], *destination.values(), self.r1_path
        )
        self.r2_notes.move_note(r2_note.id, destination)
        r1_database.soft_delete_note(r1_note["id"], self.r1_path)
        self.r2_notes.soft_delete_note(r2_note.id)
        self.assertEqual(
            self.note_projection(self.r1_path, r1_note["id"]),
            self.note_projection(self.r2_path, r2_note.id),
        )
        r1_database.restore_note(r1_note["id"], self.r1_path)
        self.r2_notes.restore_note(r2_note.id)
        self.assertEqual(
            self.note_projection(self.r1_path, r1_note["id"]),
            self.note_projection(self.r2_path, r2_note.id),
        )

    def test_directory_tree_trash_and_restore_matches(self) -> None:
        r1_root = r1_database.create_directory("虚构树主", database_path=self.r1_path)
        r1_child = r1_database.create_directory(
            "虚构树子", r1_root["id"], self.r1_path
        )
        r1_database.create_directory("虚构树末", r1_child["id"], self.r1_path)
        r2_root = self.r2_directories.create_directory("虚构树主")
        r2_child = self.r2_directories.create_directory("虚构树子", r2_root.id)
        self.r2_directories.create_directory("虚构树末", r2_child.id)
        r1_note = r1_database.create_note_in_directory(
            "虚构树主", "虚构树子", "虚构树末", self.r1_path
        )
        r2_note = self.r2_notes.create_note(
            NoteLocation("虚构树主", "虚构树子", "虚构树末")
        )
        r1_database.soft_delete_directory_tree(r1_root["id"], self.r1_path)
        self.r2_directories.soft_delete_directory(r2_root.id)
        self.assertEqual(
            self.trash_projection(self.r1_path), self.trash_projection(self.r2_path)
        )
        r1_database.restore_trash_directory(r1_root["id"], self.r1_path)
        self.r2_directories.restore_directory(r2_root.id)
        self.assertEqual(
            self.note_projection(self.r1_path, r1_note["id"]),
            self.note_projection(self.r2_path, r2_note.id),
        )
        self.assertEqual(
            self.active_directory_projection(self.r1_path),
            self.active_directory_projection(self.r2_path),
        )

    def test_empty_trash_matches_frozen_r1_result_and_clears_data(self) -> None:
        r2_independent = self.r2_notes.create_note()
        self.r2_notes.soft_delete_note(r2_independent.id)
        r2_root = self.r2_directories.create_directory("虚构清空树")
        self.r2_notes.create_note(NoteLocation("虚构清空树", "无", "无"))
        self.r2_directories.soft_delete_directory(r2_root.id)
        r2_result = self.r2_directories.empty_trash()
        self.assertEqual(
            {
                "note_count": r2_result.note_count,
                "directory_count": r2_result.directory_count,
                "root_count": r2_result.root_count,
            },
            {
                "note_count": 2,
                "directory_count": 1,
                "root_count": 2,
            },
        )
        self.assertEqual(self.trash_projection(self.r2_path), ((), ()))

    @staticmethod
    def schema_signature(path: Path):
        with closing(sqlite3.connect(path)) as connection:
            objects = tuple(
                connection.execute(
                    """
                    SELECT type, name, tbl_name FROM sqlite_master
                    WHERE name NOT LIKE 'sqlite_%'
                    ORDER BY type, name
                    """
                ).fetchall()
            )
            table_names = [
                row[1] for row in objects if row[0] == "table"
            ]
            columns = {
                table: tuple(connection.execute(f"PRAGMA table_info({table})").fetchall())
                for table in table_names
            }
            foreign_keys = {
                table: tuple(connection.execute(f"PRAGMA foreign_key_list({table})").fetchall())
                for table in table_names
            }
        return objects, columns, foreign_keys

    @staticmethod
    def initial_directory_projection(path: Path):
        with closing(sqlite3.connect(path)) as connection:
            return tuple(
                connection.execute(
                    """
                    SELECT name, parent_id, sort_order, is_deleted
                    FROM directories ORDER BY id
                    """
                ).fetchall()
            )

    @staticmethod
    def note_projection(path: Path, note_id: int):
        with closing(sqlite3.connect(path)) as connection:
            return connection.execute(
                """
                SELECT revision, title, body_rich_text, content_html,
                       main_category, sub_category, sub_sub_category, tags,
                       ai_visibility, is_pinned, is_deleted,
                       deleted_at IS NOT NULL, trash_group_id IS NOT NULL
                FROM notes WHERE id = ?
                """,
                (note_id,),
            ).fetchone()

    @staticmethod
    def trash_projection(path: Path):
        with closing(sqlite3.connect(path)) as connection:
            directories = tuple(
                connection.execute(
                    """
                    SELECT name, is_deleted, trash_group_id IS NOT NULL
                    FROM directories WHERE is_deleted = 1 ORDER BY name
                    """
                ).fetchall()
            )
            notes = tuple(
                connection.execute(
                    """
                    SELECT main_category, sub_category, sub_sub_category,
                           is_deleted, directory_id IS NULL, trash_group_id IS NOT NULL
                    FROM notes WHERE is_deleted = 1 ORDER BY id
                    """
                ).fetchall()
            )
        return directories, notes

    @staticmethod
    def active_directory_projection(path: Path):
        with closing(sqlite3.connect(path)) as connection:
            return tuple(
                connection.execute(
                    """
                    SELECT id, name, parent_id, sort_order
                    FROM directories WHERE is_deleted = 0 ORDER BY id
                    """
                ).fetchall()
            )


if __name__ == "__main__":
    unittest.main()
