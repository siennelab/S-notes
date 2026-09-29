from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import patch

from src import database as r1_database
from src.snotes.application.directory_service import DirectoryService
from src.snotes.application.export_service import ExportService
from src.snotes.application.note_service import NoteService
from src.snotes.application.tag_service import TagService
from src.snotes.domain.models import Note, NoteLocation
from src.snotes.domain.search_query import parse_search_query
from src.snotes.exporting.ordinary import OrdinaryExportWriter
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database


class ClosingR1Connection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def open_closing_r1_connection(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path, timeout=5.0, factory=ClosingR1Connection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


class Package3OrdinaryProductParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.r1_connect_patch = patch.object(
            r1_database,
            "_connect",
            open_closing_r1_connection,
        )
        self.r1_connect_patch.start()
        self.addCleanup(self.r1_connect_patch.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.r2_path = self.root / "r2.db"
        connections = initialize_database(self.r2_path)
        directories = SQLiteDirectoriesRepository(connections)
        notes = SQLiteNotesRepository(connections, directories)
        tags = SQLiteTagsRepository(connections)
        self.notes = NoteService(notes)
        self.directories = DirectoryService(directories, notes)
        self.tags = TagService(tags)
        self.exports = ExportService(
            notes,
            OrdinaryExportWriter(
                self.root / "exports",
                self.r2_path,
                clock=lambda: datetime(2026, 8, 7, 12, 34, 56, tzinfo=ZoneInfo("Asia/Shanghai")),
            ),
        )

    def create_note(
        self,
        title: str,
        body: str,
        location: NoteLocation = NoteLocation(),
        tags: tuple[str, ...] = (),
    ) -> Note:
        note = self.notes.create_note(location)
        note = self.notes.save_note(
            note.id,
            title=title,
            content_markdown=body,
            location=location,
            expected_revision=note.revision,
        )
        if tags:
            self.tags.set_note_tags(note.id, tags)
            note = self.notes.get_note(note.id) or note
        return note

    def test_search_query_contract_is_exact_copy_of_frozen_r1(self) -> None:
        samples = (
            "  Alpha，中文 alpha  ",
            "v2rayN wg-hi node.js C++ C# 10.0.0.1 2026-08-07",
            "【项目】 标签/正文",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                r2 = parse_search_query(sample)
                r1 = r1_database.parse_search_query(sample)
                self.assertEqual((r2.normalized, r2.tokens), (r1.normalized, r1.tokens))

    def test_same_fixture_queries_return_same_order_as_frozen_r1(self) -> None:
        r1_path = self.root / "r1.db"
        r1_database.initialize_database(r1_path)
        fixtures = (
            ("Alpha 中文", "<p>正文 node.js</p>", NoteLocation("项目", "网络"), ("vps",)),
            ("目录命中", "<p>普通</p>", NoteLocation("Alpha", "中文"), ()),
            ("正文命中", "<p>Alpha 与 中文</p>", NoteLocation(), ("misc",)),
            ("技术词", "<p>C++ C# wg-hi</p>", NoteLocation(), ()),
        )
        for title, body, location, tags in fixtures:
            r1_note = r1_database.create_note_in_directory(
                location.main, location.sub, "无", r1_path
            )
            r1_database.update_note(
                r1_note["id"],
                title=title,
                content_html=body,
                main_category=location.main,
                sub_category=location.sub,
                sub_sub_category="无",
                database_path=r1_path,
            )
            if tags:
                r1_database.set_note_tags(r1_note["id"], list(tags), r1_path)
            self.create_note(title, body, location, tags)
        for query in ("中文 alpha", "node.js vps", "C++ wg-hi", "不存在"):
            with self.subTest(query=query):
                r1_titles = [row["title"] for row in r1_database.search_notes(query, r1_path)]
                r2_titles = [note.title for note in self.notes.search_notes(query)]
                self.assertEqual(r2_titles, r1_titles)

    def test_search_tag_and_smart_view_product_semantics(self) -> None:
        exact = self.create_note(
            "Alpha 中文",
            "<p>正文 C++ node.js</p>",
            NoteLocation("项目", "网络"),
            ("todo", "vps"),
        )
        directory_match = self.create_note(
            "目录命中",
            "<p>普通正文</p>",
            NoteLocation("Alpha", "中文"),
        )
        body_match = self.create_note(
            "正文命中",
            "<p>Alpha 与 中文 分散在正文</p>",
            tags=("misc",),
        )
        unrelated = self.create_note("无关", "<p>none</p>")
        keyword_only = self.create_note("TODO 标题而非标签", "<p>none</p>")
        self.notes.set_note_pinned(exact.id, True)

        self.assertEqual(
            [note.id for note in self.notes.search_notes("中文 alpha")],
            [exact.id, directory_match.id, body_match.id],
        )
        self.assertEqual([note.id for note in self.notes.search_notes("C++ node.js")], [exact.id])
        self.assertEqual([note.id for note in self.notes.search_notes("#TODO")], [exact.id])
        self.assertEqual([note.id for note in self.notes.notes_for_tag("#TODO")], [exact.id])
        self.assertEqual([note.id for note in self.notes.smart_view("pinned")[1]], [exact.id])
        self.assertIn(exact.id, [note.id for note in self.notes.smart_view("todo")[1]])
        self.assertEqual(len(self.notes.smart_view("recent")[1]), 5)
        self.assertNotIn(unrelated.id, [note.id for note in self.notes.search_notes("alpha")])
        self.assertNotIn(keyword_only.id, [note.id for note in self.notes.search_notes("#todo")])

    def test_tag_text_owner_skips_unchanged_write_and_persists_changes(self) -> None:
        note = self.create_note("标签甲", "<p>甲</p>", tags=("alpha", "中文"))
        before = self.notes.get_note(note.id)
        unchanged = self.tags.set_note_tags_from_text(
            note.id,
            "  #中文，ALPHA alpha  ",
        )
        after_unchanged = self.notes.get_note(note.id)
        self.assertEqual(set(unchanged), {"alpha", "中文"})
        self.assertEqual(after_unchanged.revision, before.revision)

        changed = self.tags.set_note_tags_from_text(note.id, "alpha, beta")
        after_changed = self.notes.get_note(note.id)
        self.assertEqual(changed, ["alpha", "beta"])
        self.assertEqual(after_changed.revision, before.revision + 1)
        self.assertEqual(self.tags.get_note_tags(note.id), ["alpha", "beta"])

    def test_directory_multi_move_drag_target_and_pin_round_trip(self) -> None:
        root = self.directories.create_directory("客户")
        child = self.directories.create_directory("虚构项目", root.id)
        target = NoteLocation("客户", "虚构项目")
        first = self.create_note("批量甲", "<p>甲</p>")
        second = self.create_note("批量乙", "<p>乙</p>")
        moved = self.notes.move_notes([first.id, second.id, first.id], target)
        self.assertEqual([note.id for note in moved], [first.id, second.id])
        self.assertTrue(all(note.location == target for note in moved))
        self.assertEqual(self.directories.get_directory_path(child.id), ("客户", "虚构项目"))
        pinned = self.notes.set_note_pinned(first.id, True)
        unpinned = self.notes.set_note_pinned(first.id, False)
        self.assertTrue(pinned.is_pinned)
        self.assertFalse(unpinned.is_pinned)

    def test_deleted_directory_keeps_expandable_note_and_allows_single_note_restore(self) -> None:
        root = self.directories.create_directory("待删除树")
        child = self.directories.create_directory("子层", root.id)
        note = self.create_note(
            "树内可单独恢复",
            "<p>虚构</p>",
            NoteLocation("待删除树", "子层"),
        )
        group = self.directories.soft_delete_directory(root.id)
        snapshot = self.directories.list_trash()
        self.assertEqual({row.id for row in snapshot.directories}, {root.id, child.id})
        self.assertEqual(snapshot.notes[0].id, note.id)
        self.assertEqual(snapshot.notes[0].trash_group_id, group)
        result = self.directories.restore_trash_items([note.id], [])
        self.assertEqual(result.note_ids, (note.id,))
        self.assertIsNotNone(self.notes.get_note(note.id))
        remaining = self.directories.list_trash()
        self.assertEqual({row.id for row in remaining.directories}, {root.id, child.id})
        self.assertEqual(remaining.notes, ())

    def test_batch_restore_permanent_delete_and_empty_trash(self) -> None:
        restore_root = self.directories.create_directory("恢复树")
        restore_child = self.directories.create_directory("恢复子层", restore_root.id)
        restored_note = self.create_note("随树恢复", "<p>恢复</p>", NoteLocation("恢复树", "恢复子层"))
        self.directories.soft_delete_directory(restore_root.id)
        result = self.directories.restore_trash_items(
            [restored_note.id],
            [restore_root.id, restore_child.id],
        )
        self.assertEqual(result.directory_ids, (restore_root.id,))
        self.assertEqual(result.note_ids, ())
        self.assertIsNotNone(self.notes.get_note(restored_note.id))

        permanent = self.create_note("永久删除", "<p>删</p>")
        self.notes.soft_delete_note(permanent.id)
        deleted = self.directories.permanently_delete_trash_items([permanent.id], [])
        self.assertEqual(deleted.note_ids, (permanent.id,))
        self.assertIsNone(self.notes.get_note(permanent.id, include_deleted=True))

        independent = self.create_note("待清空", "<p>空</p>")
        tree = self.directories.create_directory("待清空树")
        self.notes.soft_delete_note(independent.id)
        self.directories.soft_delete_directory(tree.id)
        emptied = self.directories.empty_trash()
        self.assertEqual((emptied.note_count, emptied.directory_count), (1, 1))
        self.assertEqual(self.directories.list_trash().notes, ())

    def test_ordinary_relation_sections_and_priority(self) -> None:
        current = self.create_note(
            "当前标题",
            "<p>请看 [[手动目标]]，另有 正文目标。</p>",
            NoteLocation("项目", "共同目录"),
            ("共享",),
        )
        manual = self.create_note("手动目标", "<p>内容</p>")
        backlink = self.create_note("反向目标", "<p>[[当前标题]]</p>")
        same = self.create_note(
            "同目录标签",
            "<p>内容</p>",
            NoteLocation("项目", "共同目录"),
            ("共享",),
        )
        text = self.create_note("正文目标", "<p>内容</p>")
        results = self.notes.related_notes(current.id)
        by_id = {row.note_id: row for row in results}
        self.assertIn("引用", by_id[manual.id].reasons)
        self.assertIn("被引用", by_id[backlink.id].reasons)
        self.assertEqual(set(by_id[same.id].reasons), {"同目录", "同标签"})
        self.assertIn("正文", by_id[text.id].reasons)
        self.assertIn(results[0].note_id, {manual.id, backlink.id})

    def test_markdown_json_and_sqlite_backup_match_frozen_ordinary_shape(self) -> None:
        note = self.create_note(
            '导出:/测试?"',
            "**粗体**\n\n- 项目",
            NoteLocation("资料", "导出"),
            ("Alpha", "中文"),
        )
        markdown = self.exports.export_current(note.id, "markdown")
        markdown_path = Path(markdown.path)
        self.assertEqual(markdown_path.name, "导出测试_20260807_123456.md")
        markdown_text = markdown_path.read_text(encoding="utf-8")
        self.assertIn("# 导出:/测试?\"", markdown_text)
        self.assertIn("- 目录：资料 / 导出", markdown_text)
        self.assertIn("- 标签：#alpha #中文", markdown_text)
        self.assertIn("**粗体**", markdown_text)
        self.assertIn("- 项目", markdown_text)

        exported_json = self.exports.export_all("json")
        payload = json.loads(Path(exported_json.path).read_text(encoding="utf-8"))
        self.assertEqual(payload["export_type"], "ordinary")
        self.assertEqual(payload["scope"], "all_notes")
        self.assertEqual(payload["note_count"], 1)
        self.assertEqual(payload["notes"][0]["tags"], ["alpha", "中文"])
        # f2e280f preserves spacing between Markdown blocks in plain-text exports.
        self.assertEqual(payload["notes"][0]["content_text"], "粗体\n\n项目")
        self.assertNotIn("sub_sub_category", payload["notes"][0])

        backup = self.exports.backup_database()
        connection = sqlite3.connect(backup.path)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 1)
        finally:
            connection.close()

    def test_export_scope_directory_and_selected_ids_are_deduplicated(self) -> None:
        first = self.create_note("范围甲", "<p>甲</p>", NoteLocation("范围", "一"))
        second = self.create_note("范围乙", "<p>乙</p>", NoteLocation("范围", "二"))
        self.create_note("范围外", "<p>外</p>")
        selected = self.exports.export_note_ids([first.id, second.id, first.id], "selected_notes", "json")
        self.assertEqual(selected.note_count, 2)
        directories = self.exports.export_directories(
            [NoteLocation("范围", "无"), NoteLocation("范围", "一")],
            "json",
        )
        self.assertEqual(directories.note_count, 2)
        directory_payload = json.loads(Path(directories.path).read_text(encoding="utf-8"))
        application_ids = {
            note.id
            for note in self.notes.notes_for_directory(NoteLocation("范围", "无"))
        }
        self.assertEqual(
            {row["id"] for row in directory_payload["notes"]},
            application_ids,
        )


if __name__ == "__main__":
    unittest.main()
