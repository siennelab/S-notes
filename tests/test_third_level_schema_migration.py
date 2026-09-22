from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.snotes.application.note_service import NoteService
from src.snotes.domain.models import NoteLocation
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.schema import (
    CATEGORY_SCHEMA_VERSION,
    CATEGORY_SCHEMA_VERSION_KEY,
    initialize_database,
)


class ThirdLevelSchemaMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-third-level-schema-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "fictional.db"

    def test_new_database_has_only_two_category_columns(self) -> None:
        connections = initialize_database(self.path)
        with connections.read() as connection:
            self.assertNotIn("sub_sub_category", self._columns(connection, "notes"))
            self.assertNotIn(
                "sub_sub_category",
                self._columns(connection, "ai_receipt_inbox"),
            )
            version = connection.execute(
                "SELECT meta_value FROM schema_meta WHERE meta_key = ?",
                (CATEGORY_SCHEMA_VERSION_KEY,),
            ).fetchone()[0]
        self.assertEqual(version, CATEGORY_SCHEMA_VERSION)

    def test_old_columns_are_dropped_without_changing_other_rows_or_relations(self) -> None:
        connections = initialize_database(self.path)
        directories = SQLiteDirectoriesRepository(connections)
        notes = NoteService(SQLiteNotesRepository(connections, directories))
        note = notes.create_note(NoteLocation("虚构主分类", "虚构子分类"))
        note = notes.save_note(
            note.id,
            title="FICTIONAL MIGRATION NOTE",
            content_markdown="<p>FICTIONAL MIGRATION BODY</p>",
            location=NoteLocation("虚构主分类", "虚构子分类"),
            expected_revision=note.revision,
        )
        with connections.write() as connection:
            main_id = int(
                connection.execute(
                    "SELECT id FROM directories WHERE name = '虚构主分类' AND parent_id IS NULL"
                ).fetchone()[0]
            )
            sub_id = int(
                connection.execute(
                    "SELECT id FROM directories WHERE name = '虚构子分类' AND parent_id = ?",
                    (main_id,),
                ).fetchone()[0]
            )
            empty_third_id = self._insert_directory(
                connection, "FICTIONAL EMPTY THIRD LEVEL", sub_id
            )
            connection.execute(
                "ALTER TABLE notes ADD COLUMN sub_sub_category TEXT NOT NULL DEFAULT '无'"
            )
            connection.execute(
                "ALTER TABLE ai_receipt_inbox ADD COLUMN sub_sub_category TEXT NOT NULL DEFAULT '无'"
            )
            connection.execute(
                "UPDATE notes SET sub_sub_category = 'FICTIONAL_DEPRECATED_NOTE_VALUE' WHERE id = ?",
                (note.id,),
            )
            connection.execute(
                """
                INSERT INTO ai_receipt_inbox (
                    raw_text, parse_status, status, source, action, title,
                    main_category, sub_category, tags, ai_visibility, ai_targets,
                    body, note_id, created_at, updated_at, sub_sub_category
                ) VALUES (
                    'FICTIONAL LEGACY INPUT', 'parsed', 'accepted', 'Fictional',
                    '新增', 'FICTIONAL RECEIPT', '虚构主分类', '虚构子分类',
                    'fixture', 'full', 'fictional-target', 'FICTIONAL BODY', ?,
                    '2026-08-11T00:00:00', '2026-08-11T00:00:00',
                    'FICTIONAL_DEPRECATED_RECEIPT_VALUE'
                )
                """,
                (note.id,),
            )
            connection.execute(
                "INSERT INTO tags (name, created_at) VALUES ('fictional-migration-tag', '2026-08-11T00:00:00')"
            )
            tag_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
            connection.execute(
                "INSERT INTO note_tags (note_id, tag_id) VALUES (?, ?)",
                (note.id, tag_id),
            )
            connection.execute(
                """
                INSERT INTO ai_targets (
                    target_id, display_name, slug, created_at, updated_at
                ) VALUES (
                    'fictional-migration-target', 'Fictional Migration Target',
                    'fictional-migration-target', '2026-08-11T00:00:00',
                    '2026-08-11T00:00:00'
                )
                """
            )
            connection.execute(
                """
                INSERT INTO note_ai_targets (note_id, target_id, created_at)
                VALUES (?, 'fictional-migration-target', '2026-08-11T00:00:00')
                """,
                (note.id,),
            )
            connection.execute(
                "DELETE FROM schema_meta WHERE meta_key = ?",
                (CATEGORY_SCHEMA_VERSION_KEY,),
            )

        before = self._anonymous_snapshot(connections)
        initialize_database(self.path)
        after = self._anonymous_snapshot(connections)

        self.assertEqual(after, before)
        with connections.read() as connection:
            self.assertNotIn("sub_sub_category", self._columns(connection, "notes"))
            self.assertNotIn(
                "sub_sub_category",
                self._columns(connection, "ai_receipt_inbox"),
            )
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertIsNone(
                connection.execute(
                    "SELECT id FROM directories WHERE id = ?", (empty_third_id,)
                ).fetchone()
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM directories WHERE id IN (?, ?)",
                    (main_id, sub_id),
                ).fetchone()[0],
                2,
            )
            stored = connection.execute(
                "SELECT title, main_category, sub_category, revision FROM notes WHERE id = ?",
                (note.id,),
            ).fetchone()
            self.assertEqual(
                tuple(stored),
                ("FICTIONAL MIGRATION NOTE", "虚构主分类", "虚构子分类", 2),
            )

    def test_nonempty_third_level_directory_blocks_migration(self) -> None:
        for case in ("linked_note", "child_directory"):
            with self.subTest(case=case):
                path = Path(self.temporary.name) / f"{case}.db"
                connections = initialize_database(path)
                with connections.write() as connection:
                    root_id = self._insert_directory(connection, "FICTIONAL ROOT", None)
                    sub_id = self._insert_directory(connection, "FICTIONAL SUB", root_id)
                    third_id = self._insert_directory(
                        connection, "FICTIONAL NONEMPTY THIRD", sub_id
                    )
                    if case == "linked_note":
                        connection.execute(
                            """
                            INSERT INTO notes (
                                directory_id, title, main_category, sub_category,
                                created_at, updated_at
                            ) VALUES (
                                ?, 'FICTIONAL LINKED NOTE', 'FICTIONAL ROOT',
                                'FICTIONAL SUB', '2026-08-11T00:00:00',
                                '2026-08-11T00:00:00'
                            )
                            """,
                            (third_id,),
                        )
                    else:
                        self._insert_directory(
                            connection, "FICTIONAL CHILD", third_id
                        )
                    connection.execute(
                        "ALTER TABLE notes ADD COLUMN sub_sub_category TEXT NOT NULL DEFAULT '无'"
                    )
                    connection.execute(
                        "ALTER TABLE ai_receipt_inbox ADD COLUMN sub_sub_category TEXT NOT NULL DEFAULT '无'"
                    )
                    connection.execute(
                        "DELETE FROM schema_meta WHERE meta_key = ?",
                        (CATEGORY_SCHEMA_VERSION_KEY,),
                    )

                with self.assertRaisesRegex(RuntimeError, "无法自动删除"):
                    initialize_database(path)

                with connections.read() as connection:
                    self.assertIn(
                        "sub_sub_category", self._columns(connection, "notes")
                    )
                    self.assertIn(
                        "sub_sub_category",
                        self._columns(connection, "ai_receipt_inbox"),
                    )
                    self.assertIsNotNone(
                        connection.execute(
                            "SELECT id FROM directories WHERE id = ?", (third_id,)
                        ).fetchone()
                    )

    @staticmethod
    def _columns(connection, table: str) -> tuple[str, ...]:
        return tuple(str(row["name"]) for row in connection.execute(f'PRAGMA table_info("{table}")'))

    @staticmethod
    def _insert_directory(connection, name: str, parent_id: int | None) -> int:
        return int(
            connection.execute(
                """
                INSERT INTO directories (name, parent_id, created_at, updated_at)
                VALUES (?, ?, '2026-08-11T00:00:00', '2026-08-11T00:00:00')
                RETURNING id
                """,
                (name, parent_id),
            ).fetchone()[0]
        )

    @staticmethod
    def _anonymous_snapshot(connections) -> tuple[int, ...]:
        with connections.read() as connection:
            return (
                int(connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0]),
                int(connection.execute("SELECT COUNT(*) FROM notes WHERE is_deleted = 0").fetchone()[0]),
                int(connection.execute("SELECT COUNT(*) FROM ai_receipt_inbox").fetchone()[0]),
                int(connection.execute("SELECT COUNT(*) FROM ai_receipt_inbox WHERE status = 'accepted'").fetchone()[0]),
                int(connection.execute("SELECT COUNT(*) FROM note_tags").fetchone()[0]),
                int(connection.execute("SELECT COUNT(*) FROM note_ai_targets").fetchone()[0]),
                int(connection.execute("SELECT COUNT(*) FROM ai_receipt_inbox WHERE note_id IS NOT NULL").fetchone()[0]),
            )


if __name__ == "__main__":
    unittest.main()
