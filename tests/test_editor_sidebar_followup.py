from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QFont, QTextCursor, QTextFormat
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem

from src.database import (
    create_note,
    initialize_database,
    list_ai_visible_notes,
    rename_note,
    set_note_ai_targets,
    set_note_ai_visibility,
)
from src.editor import NotesEditor
from src.editor_toolbar import FONT_SIZE_OPTIONS, EditorToolbar
from src.main_window import (
    MainWindow,
    compact_ai_target_summary,
    note_date_column_width,
)
from src.theme import THEMES, build_app_style
from src.time_utils import CHINESE_WEEKDAYS, SHANGHAI_TZ, format_sidebar_clock


def _format_at(editor: NotesEditor, position: int = 0):
    cursor = QTextCursor(editor.document())
    cursor.setPosition(position)
    cursor.setPosition(position + 1, QTextCursor.KeepAnchor)
    return cursor.charFormat()


def _select_all(editor: NotesEditor) -> None:
    cursor = editor.textCursor()
    cursor.select(QTextCursor.Document)
    editor.setTextCursor(cursor)


def _chatgpt_style_mime() -> QMimeData:
    mime = QMimeData()
    mime.setText(
        "虚构粗体 与 斜体 链接\n"
        "虚构列表\n"
        "虚构引用\n"
        "fictional_code()"
    )
    mime.setHtml(
        '<div class="response" style="font-family:Arial;font-size:10px">'
        '<p><span style="font-size:10px"><strong>虚构粗体</strong> 与 '
        '<em>斜体</em> <a href="https://example.invalid/safe">链接</a></span></p>'
        "<ul><li>虚构列表</li></ul>"
        "<blockquote>虚构引用</blockquote>"
        "<pre><code>fictional_code()</code></pre>"
        "</div>"
    )
    return mime


class SidebarClockFormatTest(unittest.TestCase):
    def test_shanghai_format_and_all_weekdays_are_fixed(self) -> None:
        utc_value = datetime(2026, 7, 27, 13, 48, 39, tzinfo=timezone.utc)
        self.assertEqual(
            format_sidebar_clock(utc_value),
            "21:48:39 2026/07/27 周一",
        )
        monday = datetime(2026, 7, 27, 8, 0, tzinfo=SHANGHAI_TZ)
        self.assertEqual(
            [
                format_sidebar_clock(monday + timedelta(days=index)).split()[-1]
                for index in range(7)
            ],
            list(CHINESE_WEEKDAYS),
        )


class AiActorDatabaseFilterTest(unittest.TestCase):
    def test_exact_actor_and_common_filters_preserve_existing_meaning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "fictional.db"
            initialize_database(database_path)

            def add(title: str, targets: list[str], visibility: str = "full"):
                note = create_note(database_path)
                rename_note(note["id"], title, database_path)
                set_note_ai_visibility(note["id"], visibility, database_path)
                set_note_ai_targets(note["id"], targets, database_path)
                return note["id"]

            common_id = add("虚构通用资料", ["common"])
            gale_id = add("虚构 Gale 资料", ["gale"])
            shared_id = add("虚构共享资料", ["common", "gale"])
            codex_id = add("虚构 Codex 资料", ["codex"])
            add("虚构隐藏资料", ["gale"], "hidden")

            all_ids = {note["id"] for note in list_ai_visible_notes(database_path)}
            common_ids = {
                note["id"]
                for note in list_ai_visible_notes(
                    database_path,
                    actor_target_id="common",
                )
            }
            gale_ids = {
                note["id"]
                for note in list_ai_visible_notes(
                    database_path,
                    actor_target_id="gale",
                )
            }
            queried_ids = {
                note["id"]
                for note in list_ai_visible_notes(
                    database_path,
                    actor_target_id="gale",
                    query="共享",
                )
            }

            self.assertEqual(all_ids, {common_id, gale_id, shared_id, codex_id})
            self.assertEqual(common_ids, {common_id, shared_id})
            self.assertEqual(gale_ids, {gale_id, shared_id})
            self.assertEqual(queried_ids, {shared_id})


class EditorSidebarUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        initialize_database()

    def setUp(self) -> None:
        self.common_note = create_note()
        rename_note(self.common_note["id"], "虚构通用筛选样本")
        set_note_ai_visibility(self.common_note["id"], "full")
        set_note_ai_targets(self.common_note["id"], ["common"])

        self.gale_note = create_note()
        rename_note(
            self.gale_note["id"],
            f"虚构 Gale Alpha 筛选样本 {self.gale_note['id']}",
        )
        set_note_ai_visibility(self.gale_note["id"], "full")
        set_note_ai_targets(self.gale_note["id"], ["gale", "codex"])

        self.gale_second_note = create_note()
        rename_note(self.gale_second_note["id"], "虚构 Gale Beta 筛选样本")
        set_note_ai_visibility(self.gale_second_note["id"], "redacted")
        set_note_ai_targets(self.gale_second_note["id"], ["gale"])

        self.window = MainWindow()
        self.window.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.window.close()
        self.app.processEvents()

    def test_clock_timer_refreshes_and_stops_on_close(self) -> None:
        self.assertTrue(self.window.sidebar_clock_timer.isActive())
        self.assertEqual(self.window.sidebar_clock_timer.interval(), 1000)
        self.assertEqual(self.window.sidebar_clock_label.font().pixelSize(), 15)
        self.assertEqual(self.window.sidebar_clock_label.height(), 32)
        self.assertGreaterEqual(
            self.window.sidebar_clock_label.width(),
            self.window.sidebar_clock_label.fontMetrics().horizontalAdvance(
                self.window.sidebar_clock_label.text()
            ),
        )
        self.assertTrue(self.window.new_note_button.isVisible())
        timeout_spy = QSignalSpy(self.window.sidebar_clock_timer.timeout)
        self.assertTrue(timeout_spy.wait(1500))
        self.assertGreaterEqual(timeout_spy.count(), 1)
        with patch(
            "src.main_window.format_sidebar_clock",
            return_value="01:02:03 2026/07/28 周二",
        ):
            self.window.update_sidebar_clock()
        self.assertEqual(
            self.window.sidebar_clock_label.text(),
            "01:02:03 2026/07/28 周二",
        )
        self.window.close()
        self.app.processEvents()
        self.assertFalse(self.window.sidebar_clock_timer.isActive())

    def test_date_column_never_elides_and_extra_width_goes_to_title(self) -> None:
        self.window.populate_note_table(
            [
                {
                    "id": 900001,
                    "title": "这是一个用于验证标题优先省略的非常非常长的虚构标题",
                    "main_category": "临时",
                    "sub_category": "无",
                    "sub_sub_category": "无",
                    "updated_at": "2026-07-27T21:48:39+08:00",
                    "is_pinned": 0,
                }
            ]
        )
        date_index = self.window.note_table.model().index(0, 1)
        date_option = QStyleOptionViewItem()
        self.window.note_table.itemDelegate().initStyleOption(
            date_option,
            date_index,
        )
        self.assertEqual(date_option.text, "07-27")
        self.assertEqual(date_option.textElideMode, Qt.ElideNone)
        self.assertEqual(
            self.window.note_table.columnWidth(1),
            note_date_column_width(self.window.note_table.fontMetrics()),
        )

        title_index = self.window.note_table.model().index(0, 0)
        title_option = QStyleOptionViewItem()
        self.window.note_table.itemDelegate().initStyleOption(
            title_option,
            title_index,
        )
        self.assertEqual(title_option.textElideMode, Qt.ElideRight)

        self.window.note_table.setFixedWidth(220)
        self.app.processEvents()
        narrow_title_width = self.window.note_table.columnWidth(0)
        stable_date_width = self.window.note_table.columnWidth(1)
        self.window.note_table.setFixedWidth(420)
        self.app.processEvents()
        self.assertEqual(self.window.note_table.columnWidth(1), stable_date_width)
        self.assertGreater(self.window.note_table.columnWidth(0), narrow_title_width)
        self.assertIn(
            "…",
            self.window.note_table.fontMetrics().elidedText(
                title_option.text,
                Qt.ElideRight,
                narrow_title_width,
            ),
        )

    def test_ai_filter_combines_with_search_and_hides_on_other_views(self) -> None:
        self.window.show_smart_view("ai")
        gale_index = self.window.ai_actor_filter_combo.findData("gale")
        self.window.ai_actor_filter_combo.setCurrentIndex(gale_index)
        self.app.processEvents()
        visible_ids = set(self.window.visible_note_ids())
        self.assertIn(self.gale_note["id"], visible_ids)
        self.assertIn(self.gale_second_note["id"], visible_ids)
        self.assertNotIn(self.common_note["id"], visible_ids)
        self.assertTrue(self.window.ai_actor_filter_widget.isVisible())

        self.window.search_input.setText(
            f"Alpha 筛选样本 {self.gale_note['id']}"
        )
        self.window.apply_search_filter()
        self.assertEqual(
            set(self.window.visible_note_ids()),
            {self.gale_note["id"]},
        )
        self.assertEqual(
            (self.window.current_sidebar_mode, self.window.current_filter_value),
            ("smart_view", "ai"),
        )

        self.window.show_smart_view("recent")
        self.app.processEvents()
        self.assertFalse(self.window.ai_actor_filter_widget.isVisible())

    def test_actor_summary_refreshes_immediately_and_does_not_leak_between_notes(self) -> None:
        self.window.load_note(self.common_note["id"])
        self.assertIn("AI对象：通用", self.window.metadata_summary_button.text())
        self.assertNotIn("Codex", self.window.metadata_summary_button.text())

        self.window.load_note(self.gale_note["id"])
        self.assertIn(
            "AI对象：Gale / Codex",
            self.window.metadata_summary_button.text(),
        )

        self.window.load_note(self.common_note["id"])
        self.assertNotIn("Codex", self.window.metadata_summary_button.text())
        self.window.ai_target_checks["Gale"].setChecked(True)
        self.app.processEvents()
        self.assertIn(
            "AI对象：通用 / Gale",
            self.window.metadata_summary_button.text(),
        )
        self.assertEqual(
            compact_ai_target_summary(
                ["通用", "Gale", "Codex", "ke-den"]
            ),
            "通用 / Gale / Codex 等 4 项",
        )


class EditorToolbarGroupingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.editor = NotesEditor()
        self.toolbar = EditorToolbar(self.editor)
        self.toolbar.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.toolbar.close()
        self.editor.close()
        self.app.processEvents()

    @staticmethod
    def _tooltips(group) -> list[str]:
        return [
            item.widget().toolTip()
            for index in range(group.layout().count())
            if (item := group.layout().itemAt(index)).widget() is not None
        ]

    def test_groups_have_the_approved_order_and_code_is_structural(self) -> None:
        self.assertEqual(
            self.toolbar.toolbar_group_names,
            ("history", "appearance", "cleanup", "structure", "tools"),
        )
        groups = self.toolbar.toolbar_groups
        self.assertEqual(self._tooltips(groups[0]), ["撤销", "重做"])
        self.assertEqual(
            self._tooltips(groups[1]),
            ["文字大小", "加粗", "斜体", "删除线"],
        )
        self.assertEqual(
            self._tooltips(groups[2]),
            ["清除选区或全文格式", "清除 Markdown 标记", "压缩空行"],
        )
        self.assertEqual(
            self._tooltips(groups[3]),
            ["标题层级", "无序列表", "有序列表", "引用", "代码块"],
        )
        self.assertEqual(
            self._tooltips(groups[4]),
            ["展开或收起查找替换"],
        )

    def test_narrow_toolbar_wraps_without_hiding_any_group(self) -> None:
        self.toolbar.resize(260, 180)
        self.app.processEvents()
        self.assertGreater(
            sum(row.isVisible() for row in self.toolbar.toolbar_rows),
            1,
        )
        self.assertTrue(all(group.isVisible() for group in self.toolbar.toolbar_groups))
        for group in self.toolbar.toolbar_groups:
            row = group.parentWidget()
            self.assertIn(row, self.toolbar.toolbar_rows)
            self.assertLessEqual(
                group.geometry().right(),
                row.contentsRect().right(),
            )

        self.toolbar.resize(800, 100)
        self.app.processEvents()
        self.assertEqual(
            sum(row.isVisible() for row in self.toolbar.toolbar_rows),
            1,
        )
        self.assertEqual(
            sum(separator.isVisible() for separator in self.toolbar.toolbar_separators),
            4,
        )

    def test_theme_styles_cover_clock_filter_and_lightweight_separators(self) -> None:
        for theme_name in THEMES:
            style = build_app_style(theme_name)
            self.assertIn(
                (
                    f"#sidebarClock {{ color: {THEMES[theme_name].sidebar_text}; "
                    "font-size: 15px; font-weight: 500; }"
                ),
                style,
            )
            self.assertIn("#aiActorFilterCombo", style)
            self.assertIn("#toolbarSeparator", style)


class RichTextPasteFontTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        initialize_database()

    def test_chatgpt_html_inherits_editor_font_and_preserves_semantics(self) -> None:
        editor = NotesEditor()
        editor.insertFromMimeData(_chatgpt_style_mime())
        self.assertEqual(
            editor.toPlainText(),
            "虚构粗体 与 斜体 链接\n虚构列表\n虚构引用\nfictional_code()",
        )
        saved_html = editor.toHtml()
        self.assertNotIn("Arial", saved_html)
        self.assertNotIn("font-size:10", saved_html.replace(" ", "").lower())
        self.assertIn("https://example.invalid/safe", saved_html)
        first_format = _format_at(editor)
        self.assertEqual(first_format.font().pixelSize(), 15)
        self.assertFalse(first_format.hasProperty(QTextFormat.FontPointSize))
        self.assertEqual(first_format.fontWeight(), QFont.Bold)

        anchor_found = False
        list_found = False
        fixed_pitch_found = False
        block = editor.document().begin()
        while block.isValid():
            list_found = list_found or block.textList() is not None
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid():
                    char_format = fragment.charFormat()
                    anchor_found = anchor_found or (
                        char_format.isAnchor()
                        and char_format.anchorHref()
                        == "https://example.invalid/safe"
                    )
                    fixed_pitch_found = (
                        fixed_pitch_found
                        or editor._is_inline_code_format(char_format)
                    )
                iterator += 1
            block = block.next()
        self.assertTrue(anchor_found)
        self.assertTrue(list_found)
        self.assertTrue(fixed_pitch_found)
        editor.close()

    def test_legacy_font_tags_and_unsafe_link_styles_do_not_lock_font(self) -> None:
        mime = QMimeData()
        mime.setText("虚构旧式字号 安全链接 危险链接")
        mime.setHtml(
            '<p style="font-family:Georgia;font-size:8pt">'
            '<font face="Times" size="1">虚构旧式字号</font> '
            '<a style="font-size:9px" href="https://example.invalid/kept">'
            "安全链接</a> "
            '<a href="javascript:alert(1)">危险链接</a></p>'
        )
        editor = NotesEditor()
        editor.insertFromMimeData(mime)

        self.assertEqual(editor.toPlainText(), mime.text())
        saved_html = editor.toHtml()
        self.assertNotIn("Georgia", saved_html)
        self.assertNotIn("Times", saved_html)
        self.assertNotIn("javascript:", saved_html)
        self.assertIn("https://example.invalid/kept", saved_html)
        self.assertEqual(_format_at(editor).font().pixelSize(), 15)
        _select_all(editor)
        editor.apply_font_size(11)
        self.assertEqual(_format_at(editor).font().pixelSize(), 11)
        editor.close()

    def test_every_font_size_overrides_pasted_inline_point_size_and_round_trips(self) -> None:
        editor = NotesEditor()
        editor.insertFromMimeData(_chatgpt_style_mime())
        for _label, pixel_size in FONT_SIZE_OPTIONS:
            _select_all(editor)
            editor.apply_font_size(pixel_size)
            char_format = _format_at(editor)
            self.assertFalse(char_format.hasProperty(QTextFormat.FontPointSize))
            self.assertEqual(
                char_format.property(QTextFormat.FontPixelSize),
                pixel_size,
            )
            self.assertEqual(char_format.font().pixelSize(), pixel_size)

        saved_html = editor.note_content_html()
        restored = NotesEditor()
        restored.set_note_content(saved_html)
        self.assertEqual(restored.toPlainText(), editor.toPlainText())
        self.assertEqual(_format_at(restored).font().pixelSize(), 26)
        self.assertIn("https://example.invalid/safe", restored.toHtml())
        editor.close()
        restored.close()

    def test_plain_text_paste_keeps_default_size(self) -> None:
        mime = QMimeData()
        mime.setText("虚构纯文本")
        editor = NotesEditor()
        editor.insertFromMimeData(mime)
        char_format = _format_at(editor)
        self.assertEqual(char_format.font().pixelSize(), 15)
        self.assertFalse(char_format.hasProperty(QTextFormat.FontPointSize))
        editor.close()

    def test_saved_pasted_size_survives_switch_and_new_window(self) -> None:
        first = create_note()
        second = create_note()
        window = MainWindow()
        window.show()
        window.load_note(first["id"])
        window.body_edit.insertFromMimeData(_chatgpt_style_mime())
        _select_all(window.body_edit)
        window.body_edit.apply_font_size(20)
        self.assertTrue(window.force_save_current_note(refresh_tree=False))
        window.load_note(second["id"])
        window.load_note(first["id"])
        self.assertEqual(_format_at(window.body_edit).font().pixelSize(), 20)
        window.close()
        self.app.processEvents()

        restored_window = MainWindow()
        restored_window.show()
        restored_window.load_note(first["id"])
        self.assertEqual(
            _format_at(restored_window.body_edit).font().pixelSize(),
            20,
        )
        restored_window.close()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
