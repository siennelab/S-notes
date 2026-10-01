from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtTest import QTest
import time
from PySide6.QtGui import QColor, QFontInfo, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QComboBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QTreeWidget,
)

from src.snotes.bootstrap import build_application
from src.snotes.reminder_authority import LOCAL_REMINDER_AUTHORITY
from src.snotes.domain.reminders import REMINDER_ACTIVE, ReminderDraft
from src.snotes.domain.models import NoteLocation
from src.snotes.ui.reminder_center import ReminderDialog, ReminderLabelManagerDialog
from src.snotes.ui.sidebar import PAYLOAD_ROLE
from src.snotes.ui.directory_dialog import DirectoryManagerDialog
from src.snotes.ui.floating_combo import FloatingComboBox
from src.snotes.ui.sidebar_widgets import NOTE_TITLE_ROLE, sidebar_clock_time_width
from src.snotes.ui.theme import (
    THEMES,
    THEME_SWATCH_COLORS,
    THEME_SWATCH_RING_COLORS,
    apply_theme,
    build_app_style,
)


class Package3UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _close_window(self):
        self.window.has_unsaved_changes = False
        self.window.conflict_active = False
        self.window.close()
        deadline = time.monotonic() + 15
        while (not self.window._close_ready or self.window.receipt_refresh_timer.isActive()) and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertTrue(self.window._close_ready)
        QTest.qWait(20)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data_root = Path(self.temporary.name) / "data"
        _application, self.window = build_application([], data_root=self.data_root, reminder_authority=LOCAL_REMINDER_AUTHORITY)
        self.addCleanup(self._close_window)

    def wait_note(self, note_id: int, timeout: float = 15.0) -> None:
        """The editor commits title/metadata/body together on loadComplete."""
        deadline = time.monotonic() + timeout
        window = self.window
        while not (window.current_note is not None and window.current_note.id == note_id
                   and window.body_edit.can_save) and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertIsNotNone(window.current_note)
        self.assertEqual(window.current_note.id, note_id)
        self.assertTrue(window.body_edit.can_save)

    def load_note(self, note_id: int) -> bool:
        result = self.window.load_note(note_id)
        if result:
            self.wait_note(note_id)
        return result

    def create_note(self, title: str, body: str, location=NoteLocation(), tags=()):
        note = self.window.note_service.create_note(location)
        note = self.window.note_service.save_note(
            note.id,
            title=title,
            content_markdown=body,
            location=location,
            expected_revision=note.revision,
        )
        if tags:
            self.window.tag_service.set_note_tags(note.id, tags)
            note = self.window.note_service.get_note(note.id)
        return note

    def all_trash_items(self):
        items = []
        tree = self.window.shell.sidebar.trash_tree

        def visit(item):
            items.append(item)
            for index in range(item.childCount()):
                visit(item.child(index))

        for index in range(tree.topLevelItemCount()):
            visit(tree.topLevelItem(index))
        return items

    def note_list_item(self, note_id: int):
        note_list = self.window.shell.sidebar.note_list
        for row in range(note_list.count()):
            item = note_list.item(row)
            if int(item.data(Qt.UserRole)) == note_id:
                return item
        raise AssertionError(f"note item not found: {note_id}")

    def reminder_table_sidebar_item(self, table_id: int):
        note_list = self.window.shell.sidebar.note_list
        for row in range(note_list.count()):
            item = note_list.item(row)
            payload = item.data(PAYLOAD_ROLE) or {}
            if payload.get("type") == "reminder_table" and int(payload["id"]) == table_id:
                return item
        raise AssertionError(f"reminder table item not found: {table_id}")

    def test_search_tag_smart_views_and_multi_selection_are_operable(self) -> None:
        first = self.create_note("Alpha 中文", "<p>正文</p>", tags=("todo",))
        second = self.create_note("第二篇", "<p>Alpha 中文</p>")
        self.window.note_service.set_note_pinned(first.id, True)
        self.window.refresh_note_list(first.id)

        self.window.shell.sidebar.search_input.setText("中文 alpha")
        self.app.processEvents()
        self.assertEqual(self.window.current_mode, "search")
        self.assertEqual({note.id for note in self.window.visible_notes}, {first.id, second.id})

        raw_tag_query = "  #TODO  "
        self.window.shell.sidebar.search_input.setText(raw_tag_query)
        self.app.processEvents()
        self.assertEqual(self.window.current_mode, "search")
        self.assertEqual(
            [note.id for note in self.window.visible_notes],
            [note.id for note in self.window.note_service.search_notes(raw_tag_query)],
        )
        self.assertEqual([note.id for note in self.window.visible_notes], [first.id])

        self.window.show_tag("todo")
        self.assertEqual([note.id for note in self.window.visible_notes], [first.id])
        self.window.show_smart_view("pinned")
        self.assertEqual([note.id for note in self.window.visible_notes], [first.id])

        self.window.show_directory()
        note_list = self.window.shell.sidebar.note_list
        for row in range(note_list.count()):
            note_list.item(row).setSelected(True)
        self.assertEqual(set(self.window.shell.sidebar.selected_note_ids()), {first.id, second.id})

    def test_reminder_note_navigation_is_single_click_and_stays_in_editor(self) -> None:
        note = self.create_note("导航 A", "<p>虚构正文</p>")
        self.create_note("导航 B", "<p>其他正文</p>")
        self.window.refresh_note_list(note.id)
        self.load_note(note.id)
        self.window.show_smart_view("reminder")
        self.app.processEvents()
        self.assertIs(
            self.window.shell.content_stack.currentWidget(),
            self.window.shell.reminder_center,
        )
        self.assertEqual(self.window.shell.sidebar.mode, "reminder")

        self.window.show_directory()
        self.app.processEvents()
        item = self.note_list_item(note.id)
        self.window.shell.sidebar.note_list.itemClicked.emit(item)
        self.app.processEvents()
        self.assertIs(
            self.window.shell.content_stack.currentWidget(),
            self.window.shell.main_content,
        )
        self.assertEqual(self.window.current_mode, "directory")

        deadline = time.monotonic() + 15
        while not self.window.body_edit.can_save and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertTrue(self.window.body_edit.can_save)
        self.window.body_edit.bridge.markdownChanged(
            self.window.body_edit.latest_markdown + " 输入",
            self.window.body_edit.current_load_id,
        )
        self.window.save_timer.stop()
        self.assertTrue(self.window.save_current_note())
        self.assertIs(
            self.window.shell.content_stack.currentWidget(),
            self.window.shell.main_content,
        )

        self.window.show_smart_view("reminder")
        self.app.processEvents()
        self.assertIs(
            self.window.shell.content_stack.currentWidget(),
            self.window.shell.reminder_center,
        )

        self.window.show_directory()
        self.app.processEvents()
        item = self.note_list_item(note.id)
        self.window.shell.sidebar.note_list.itemClicked.emit(item)
        self.app.processEvents()
        self.assertIs(
            self.window.shell.content_stack.currentWidget(),
            self.window.shell.main_content,
        )

        self.window.show_smart_view("reminder")
        self.app.processEvents()
        self.assertIs(
            self.window.shell.content_stack.currentWidget(),
            self.window.shell.reminder_center,
        )

        self.window.show_directory()
        self.app.processEvents()
        self.window.shell.sidebar.note_list.itemClicked.emit(item)
        self.app.processEvents()
        self.assertIs(
            self.window.shell.content_stack.currentWidget(),
            self.window.shell.main_content,
        )


    def test_metadata_hierarchy_theme_swatches_and_selection_palette(self) -> None:
        note = self.create_note("层级对照", "<p>这是用于主题选择的虚构正文</p>")
        self.load_note(note.id)
        shell = self.window.shell
        shell.set_metadata_expanded(True)
        self.window.resize(1420, 880)
        self.window.show()
        self.app.processEvents()
        self.assertEqual(shell.title_separator.height(), 2)
        self.assertLess(shell.title_band.geometry().top(), shell.title_separator.geometry().top())
        self.assertLess(shell.title_separator.geometry().top(), shell.metadata_section.geometry().top())
        self.assertLess(shell.metadata_section.geometry().top(), shell.body_editor.geometry().top())
        self.assertEqual(shell.metadata_panel.layout().count(), 2)
        first_row_y = {
            widget.mapTo(shell.metadata_panel, widget.rect().topLeft()).y()
            for widget in (shell.main_combo, shell.sub_combo, shell.tags_input)
        }
        second_row_y = {
            widget.mapTo(shell.metadata_panel, widget.rect().topLeft()).y()
            for widget in (shell.ai_visibility_combo, shell.ai_targets_widget)
        }
        self.assertEqual(len(first_row_y), 1)
        self.assertEqual(len(second_row_y), 1)
        self.assertLess(next(iter(first_row_y)), next(iter(second_row_y)))
        self.assertTrue(shell.ai_visibility_combo.isEnabled())
        self.assertTrue(shell.ai_targets_widget.isEnabled())
        self.assertEqual(len(shell.ai_target_checks), 7)
        self.assertIsInstance(shell.ai_visibility_combo, FloatingComboBox)
        self.assertIsInstance(shell.main_combo, FloatingComboBox)
        self.assertIsInstance(shell.sub_combo, FloatingComboBox)

        for theme_name, tokens in THEMES.items():
            with self.subTest(theme=theme_name):
                self.window.set_theme(theme_name)
                self.app.processEvents()
                body_palette = shell.body_editor.palette()
                for group in (QPalette.Active, QPalette.Inactive):
                    self.assertEqual(
                        body_palette.color(group, QPalette.Highlight),
                        QColor(tokens.selection_background),
                    )
                    self.assertEqual(
                        body_palette.color(group, QPalette.HighlightedText),
                        QColor(tokens.selection_text),
                    )
                self.assertEqual(shell.body_editor._theme.name, theme_name)
                self.assertIn(
                    f"placeholder-text-color: {tokens.text_subtle};",
                    build_app_style(theme_name),
                )
                active = sidebar_button = shell.sidebar.theme_buttons[theme_name]
                self.assertEqual((active.width(), active.height()), (16, 16))
                self.assertEqual(THEME_SWATCH_COLORS[theme_name], {
                    "dark": "#1B1D1E",
                    "light-slate": "#5F768B",
                    "light-clay": "#A84B2F",
                }[theme_name])
                self.assertTrue(THEME_SWATCH_RING_COLORS[theme_name].startswith("#"))


    def test_drag_move_signal_pin_and_metadata_tags_use_services(self) -> None:
        root = self.window.directory_service.create_directory("客户")
        child = self.window.directory_service.create_directory("项目甲", root.id)
        note = self.create_note("待整理", "<p>正文</p>")
        self.window.refresh_note_list(note.id)
        sidebar = self.window.shell.sidebar
        sidebar.main_filter_combo.setCurrentIndex(sidebar.main_filter_combo.findData(root.id))
        sidebar.sub_filter_combo.setCurrentIndex(sidebar.sub_filter_combo.findData(child.id))
        popup_targets = {
            sidebar.category_manager_button.popup.item(row).data(Qt.UserRole)
            for row in range(sidebar.category_manager_button.popup.count())
        }
        self.assertIn(child.id, popup_targets)
        sidebar.category_manager_button.notesDropped.emit([note.id], child.id)
        self.app.processEvents()
        moved = self.window.note_service.get_note(note.id)
        self.assertEqual(moved.location, NoteLocation("客户", "项目甲"))

        self.create_note("范围外", "<p>虚构</p>")
        self.window.show_directory_filter(("客户", "项目甲"))
        self.assertEqual(
            [row.id for row in self.window.visible_notes],
            [
                row.id
                for row in self.window.note_service.notes_for_directory(
                    NoteLocation("客户", "项目甲")
                )
            ],
        )

        self.window.set_note_pinned(note.id, True)
        self.assertTrue(self.window.note_service.get_note(note.id).is_pinned)
        self.load_note(note.id)
        self.window.shell.tags_input.setText("One, #two，one")
        self.window.save_timer.stop()
        self.assertTrue(self.window.save_current_note())
        self.assertEqual(self.window.tag_service.get_note_tags(note.id), ["one", "two"])

    def test_trash_tree_preserves_deleted_directory_note_child_and_restore(self) -> None:
        root = self.window.directory_service.create_directory("删除树")
        child = self.window.directory_service.create_directory("子层", root.id)
        note = self.create_note("树内笔记", "<p>虚构</p>", NoteLocation("删除树", "子层"))
        self.window.directory_service.soft_delete_directory(root.id)
        self.window.show_trash()
        payloads = [item.data(0, PAYLOAD_ROLE) or {} for item in self.all_trash_items()]
        self.assertEqual(
            {payload.get("type") for payload in payloads},
            {"deleted_directory", "deleted_note"},
        )
        note_item = next(item for item in self.all_trash_items() if (item.data(0, PAYLOAD_ROLE) or {}).get("type") == "deleted_note")
        self.assertIsNotNone(note_item.parent())
        self.window.restore_trash_items([note.id], [])
        self.assertIsNotNone(self.window.note_service.get_note(note.id))
        self.assertEqual(len(self.window.directory_service.list_trash().directories), 2)

    def test_relation_drawer_opens_related_note(self) -> None:
        current = self.create_note("当前", "<p>[[目标]]</p>")
        target = self.create_note("目标", "<p>内容</p>")
        self.load_note(current.id)
        self.window.toggle_links()
        self.app.processEvents()
        self.assertFalse(self.window.shell.link_drawer.isHidden())
        self.assertEqual(self.window.shell.link_drawer.results.count(), 1)
        item = self.window.shell.link_drawer.results.item(0)
        self.assertEqual(item.data(Qt.UserRole), target.id)

    def test_sidebar_restores_r1_information_structure(self) -> None:
        sidebar = self.window.shell.sidebar
        self.assertEqual(sidebar.search_input.placeholderText(), "搜索笔记或 #标签")
        self.assertEqual(set(sidebar.smart_buttons), {"reminder", "todo", "ai"})
        self.assertTrue(sidebar.ai_center_button.isEnabled())
        self.assertEqual(sidebar.ai_center_button.toolTip(), "打开 AI 中心")
        self.assertFalse(hasattr(sidebar, "tagRequested"))
        self.assertEqual(len(sidebar.findChildren(QComboBox, "tagBrowser")), 0)
        self.assertEqual(sidebar.findChildren(QTreeWidget), [sidebar.trash_tree])
        self.assertIs(sidebar.sidebar_stack.currentWidget(), sidebar.note_list)
        self.assertFalse(hasattr(sidebar, "export_button"))
        self.assertEqual(sidebar.trash_button.text(), "")
        self.assertEqual(sidebar.trash_button.toolTip(), "回收站")
        self.assertFalse(sidebar.trash_button.icon().isNull())
        self.assertTrue(sidebar.clock_time_label.text())
        self.assertTrue(sidebar.clock_date_label.text())

        note = self.create_note("位置对照", "<p>虚构正文</p>")
        self.load_note(note.id)
        self.window.show()
        self.app.processEvents()
        shell = self.window.shell
        self.assertLess(shell.pin_button.geometry().top(), shell.body_editor.geometry().top())
        self.assertLess(shell.links_button.geometry().top(), shell.body_editor.geometry().top())
        self.assertLess(shell.export_button.geometry().top(), shell.body_editor.geometry().top())
        self.assertGreater(shell.delete_button.mapTo(shell.editor_pane, shell.delete_button.rect().topLeft()).y(), shell.body_editor.geometry().top())


    def test_reminder_tab_toggles_back_to_directory_notes(self) -> None:
        note = self.create_note("Reminder toggle note", "<p>虚构正文</p>")
        sidebar = self.window.shell.sidebar
        reminder_button = sidebar.smart_buttons["reminder"]

        self.window.refresh_note_list(note.id)
        reminder_button.click()
        self.app.processEvents()
        self.assertIs(
            self.window.shell.content_stack.currentWidget(),
            self.window.shell.reminder_center,
        )
        self.assertEqual(self.window.current_mode, "smart_view")
        self.assertEqual(self.window.current_filter, "reminder")

        reminder_button.click()
        self.app.processEvents()

        self.assertEqual(self.window.current_mode, "directory")
        self.assertIs(
            self.window.shell.content_stack.currentWidget(),
            self.window.shell.main_content,
        )
        self.assertEqual(sidebar.mode, "directory")
        self.assertGreater(sidebar.note_list.count(), 0)
        current_payload = sidebar.note_list.item(0).data(PAYLOAD_ROLE) or {}
        self.assertEqual(current_payload.get("type"), "note")

    def test_note_selection_to_reminder_click_rebuilds_sidebar_without_crash(self) -> None:
        note_a = self.create_note("Reminder 切换 A", "<p>虚构正文 A</p>")
        note_b = self.create_note("Reminder 切换 B", "<p>虚构正文 B</p>")
        sidebar = self.window.shell.sidebar
        reminder_button = sidebar.smart_buttons["reminder"]

        self.window.refresh_note_list(note_a.id)
        self.load_note(note_a.id)
        self.window.show()
        self.app.processEvents()
        self.assertIsNotNone(sidebar.note_list.currentItem())

        reminder_button.click()
        self.app.processEvents()
        self.assertIs(
            self.window.shell.content_stack.currentWidget(),
            self.window.shell.reminder_center,
        )
        self.assertEqual(sidebar.mode, "reminder")
        self.assertTrue(self.window.shell.reminder_center.isVisible())

        self.window.show_directory()
        self.app.processEvents()
        self.assertIs(
            self.window.shell.content_stack.currentWidget(),
            self.window.shell.main_content,
        )

        item_b = self.note_list_item(note_b.id)
        sidebar.note_list.itemClicked.emit(item_b)
        self.app.processEvents()
        self.wait_note(note_b.id)
        self.assertEqual(self.window.current_note.id, note_b.id)

        reminder_button.click()
        self.app.processEvents()
        self.assertIs(
            self.window.shell.content_stack.currentWidget(),
            self.window.shell.reminder_center,
        )

        for _index in range(5):
            self.window.show_directory()
            self.app.processEvents()
            item_a = self.note_list_item(note_a.id)
            sidebar.note_list.itemClicked.emit(item_a)
            self.app.processEvents()
            reminder_button.click()
            self.app.processEvents()
            self.assertIs(
                self.window.shell.content_stack.currentWidget(),
                self.window.shell.reminder_center,
            )

    def test_reminder_sidebar_table_title_rename_and_delete_stay_synced(self) -> None:
        note = self.create_note("已有 Note selection", "<p>虚构正文</p>")
        self.window.refresh_note_list(note.id)
        self.load_note(note.id)
        self.app.processEvents()

        service = self.window.reminder_service
        table_a = service.create_table("新提醒表 08-24 15:00")
        table_b = service.create_table("备用提醒表")
        sidebar = self.window.shell.sidebar
        center = self.window.shell.reminder_center

        self.window.show_smart_view("reminder")
        self.window.select_reminder_table(table_a.id)
        self.app.processEvents()
        item_a = self.reminder_table_sidebar_item(table_a.id)
        self.assertEqual(item_a.text(), "新提醒表 08-24 15:00")
        self.assertEqual(item_a.data(NOTE_TITLE_ROLE), "新提醒表 08-24 15:00")
        self.assertNotIn(
            "未命名笔记",
            [sidebar.note_list.item(row).data(NOTE_TITLE_ROLE) for row in range(sidebar.note_list.count())],
        )

        self.window.create_reminder()
        self.app.processEvents()
        created_table = service.get_table(center.selected_table_id).table
        created_item = self.reminder_table_sidebar_item(created_table.id)
        self.assertEqual(created_item.data(NOTE_TITLE_ROLE), created_table.title)
        self.assertNotEqual(created_item.data(NOTE_TITLE_ROLE), "未命名笔记")

        center.table_title_input.setText("重命名提醒表")
        center._save_table_title()
        self.app.processEvents()
        renamed_item = self.reminder_table_sidebar_item(created_table.id)
        self.assertEqual(renamed_item.text(), "重命名提醒表")
        self.assertEqual(renamed_item.data(NOTE_TITLE_ROLE), "重命名提醒表")

        deleted_id = center.selected_table_id
        center.delete_table_button.click()
        self.app.processEvents()
        self.assertIsNotNone(center.selected_table_id)
        self.assertNotEqual(center.selected_table_id, deleted_id)
        visible_table_ids = [
            int((sidebar.note_list.item(row).data(PAYLOAD_ROLE) or {})["id"])
            for row in range(sidebar.note_list.count())
            if (sidebar.note_list.item(row).data(PAYLOAD_ROLE) or {}).get("type") == "reminder_table"
        ]
        self.assertNotIn(deleted_id, visible_table_ids)
        current_payload = sidebar.note_list.currentItem().data(PAYLOAD_ROLE)
        self.assertEqual(int(current_payload["id"]), center.selected_table_id)

        self.window.select_reminder_table(table_b.id)
        self.app.processEvents()
        self.assertEqual(
            self.reminder_table_sidebar_item(table_b.id).data(NOTE_TITLE_ROLE),
            "备用提醒表",
        )

        for _index in range(3):
            self.window.show_directory()
            self.app.processEvents()
            self.window.show_smart_view("reminder")
            self.app.processEvents()
            self.assertIs(
                self.window.shell.content_stack.currentWidget(),
                center,
            )
            self.assertEqual(
                sidebar.note_list.currentItem().data(PAYLOAD_ROLE)["type"],
                "reminder_table",
            )

    def test_frozen_r1_geometry_and_approved_two_level_classification(self) -> None:
        shell = self.window.shell
        sidebar = shell.sidebar
        self.window.resize(1420, 880)
        self.window.show()
        self.app.processEvents()
        self.assertEqual(shell.DEFAULT_SIDEBAR_WIDTH, 280)
        self.assertEqual((sidebar.minimumWidth(), sidebar.maximumWidth()), (280, 480))
        self.assertEqual(shell.splitter.handleWidth(), 4)
        margins = sidebar.layout().contentsMargins()
        self.assertEqual(
            (margins.left(), margins.top(), margins.right(), margins.bottom()),
            (16, 12, 16, 12),
        )
        self.assertEqual(sidebar.layout().spacing(), 8)
        self.assertEqual(
            sidebar.clock_time_label.width(),
            sidebar_clock_time_width(sidebar.clock_time_label.fontMetrics()),
        )
        self.assertEqual(sidebar.new_note_button.size().width(), 28)
        self.assertEqual(sidebar.new_note_button.size().height(), 28)
        smart_labels = [
            button.text()
            for button in sidebar.findChildren(QPushButton, "smartTabButton")
        ]
        self.assertEqual(smart_labels, ["提醒", "待办", "AI资料", "AI中心"])
        self.assertEqual(sidebar.main_filter_combo.itemText(0), "全部")
        self.assertEqual(sidebar.sub_filter_combo.itemText(0), "-")
        self.assertEqual(len(sidebar._filter_combos), 2)
        self.assertIsInstance(sidebar.main_filter_combo, FloatingComboBox)
        self.assertEqual(sidebar.main_filter_combo.view().objectName(), "sidebarComboPopupView")
        self.assertEqual(sidebar.category_manager_button.width(), 48)
        shell.restore_sidebar_width(360)
        self.app.processEvents()
        self.assertEqual(sidebar.width(), 360)
        self.window.set_theme("dark")
        self.app.processEvents()
        self.assertEqual(sidebar.width(), 360)

        dialog = DirectoryManagerDialog(lambda: [], lambda _parent: None, lambda _id: None, lambda _id: None)
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.windowTitle(), "分类管理")
        self.assertFalse(hasattr(dialog, "sub_sub_list"))
        titles = [label.text() for label in dialog.findChildren(type(sidebar.clock_time_label), "categoryManagerTitle")]
        self.assertEqual(titles, ["主分类", "子分类"])

    def test_sidebar_text_controls_share_note_list_font_and_smart_views_toggle_off(self) -> None:
        all_notes = {
            self.create_note("普通样本", "<p>虚构</p>").id,
            self.create_note("待办样本", "<p>虚构</p>", tags=("todo",)).id,
        }
        self.window.refresh_note_list()
        sidebar = self.window.shell.sidebar
        self.window.show()
        self.app.processEvents()
        controls = [
            *sidebar.smart_buttons.values(),
            sidebar.ai_center_button,
            sidebar.main_filter_combo,
            sidebar.sub_filter_combo,
            sidebar.category_manager_button,
            sidebar.note_list,
        ]
        font_signatures = {
            (QFontInfo(widget.font()).family(), widget.font().pixelSize())
            for widget in controls
        }
        self.assertEqual(len(font_signatures), 1)
        self.assertEqual(next(iter(font_signatures))[1], 13)

        for name in ("todo", "ai"):
            with self.subTest(view=name):
                sidebar.smart_buttons[name].click()
                self.app.processEvents()
                self.assertEqual((self.window.current_mode, self.window.current_filter), ("smart_view", name))
                self.assertTrue(sidebar.smart_buttons[name].property("active"))
                sidebar.smart_buttons[name].click()
                self.app.processEvents()
                self.assertEqual((self.window.current_mode, self.window.current_filter), ("directory", ""))
                self.assertEqual(self.window.current_directory_path, ())
                self.assertEqual({note.id for note in self.window.visible_notes}, all_notes)
                self.assertFalse(any(button.property("active") for button in sidebar.smart_buttons.values()))

    def test_reminder_columns_ready_on_first_entry_and_resize(self) -> None:
        for index in range(35):
            self.window.reminder_service.create_reminder(ReminderDraft(
                title=f"FICTIONAL width {index}", lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "one_time", "date": "2027-10-01"},
                note="FICTIONAL layout fixture",
            ))
        self.window.resize(1800, 900)
        self.window.show()
        self.window.shell.show_notes_surface()
        QTest.qWait(150)
        self.window.shell.show_reminder_surface()
        QTest.qWait(150)
        table = self.window.shell.reminder_center.table

        def widths():
            return tuple(table.columnWidth(column) for column in range(6))

        def expected():
            width = table.viewport().width()
            return tuple(max(minimum, int(width * ratio)) for ratio, minimum in zip(
                (0.24, 0.16, 0.14, 0.20, 0.12, 0.22), (160, 160, 112, 180, 112, 240),
            ))

        first, first_expected = widths(), expected()
        self.assertFalse(table.selectedItems())
        QTest.mouseClick(table.viewport(), Qt.LeftButton, pos=table.visualItemRect(table.item(0, 0)).center())
        QTest.qWait(150)
        clicked = widths()
        self.window.resize(1500, 800)
        QTest.qWait(150)
        resized, resized_expected = widths(), expected()
        self.window.shell.show_notes_surface()
        QTest.qWait(50)
        self.window.resize(1700, 900)
        QTest.qWait(50)
        self.window.shell.show_reminder_surface()
        QTest.qWait(150)
        returned, returned_expected = widths(), expected()
        print(f"Reminder widths: first={first}, expected={first_expected}, clicked={clicked}, "
              f"resized={resized}, resized_expected={resized_expected}", flush=True)
        self.assertEqual(first, first_expected)
        self.assertEqual(clicked, first)
        self.assertEqual(resized, resized_expected)
        self.assertEqual(returned, returned_expected)

    def test_reminder_dialog_uses_localized_dates_and_dynamic_repeat_parameters(self) -> None:
        dialog = ReminderDialog(parent=self.window)
        self.addCleanup(dialog.close)
        self.assertIsInstance(dialog.lifecycle_combo, FloatingComboBox)
        self.assertIsInstance(dialog.repeat_combo, FloatingComboBox)
        self.assertIsInstance(dialog.repeat_until_combo, FloatingComboBox)
        self.assertEqual(dialog.repeat_combo.view().objectName(), "comboPopupView")
        self.assertEqual(dialog.action_date.displayFormat(), "yyyy-MM-dd")
        self.assertEqual(dialog.deadline_date.displayFormat(), "yyyy-MM-dd")
        self.assertEqual(dialog.repeat_until_date.displayFormat(), "yyyy-MM-dd")
        self.assertEqual(dialog.action_date.locale().name(), "zh_CN")
        self.assertFalse(dialog.form.isRowVisible(dialog.repeat_param_label))
        self.assertFalse(dialog.form.isRowVisible(dialog.repeat_until_label))
        self.assertEqual(dialog.deadline_combo.currentText(), "无")
        self.assertIsNone(dialog.draft().deadline_spec)
        self.assertGreaterEqual(dialog.lifecycle_combo.minimumWidth(), 140)
        self.assertGreaterEqual(dialog.repeat_combo.minimumWidth(), 320)
        self.assertGreaterEqual(dialog.repeat_until_combo.minimumWidth(), 150)

        dialog.repeat_combo.setCurrentIndex(
            dialog.repeat_combo.findData("monthly_day")
        )
        dialog.action_date.setDate(QDate(2026, 8, 24))
        self.assertTrue(dialog.form.isRowVisible(dialog.repeat_param_label))
        self.assertTrue(dialog.form.isRowVisible(dialog.repeat_until_label))
        self.assertEqual(dialog.repeat_param_label.text(), "每月第")
        self.assertEqual(dialog.repeat_param_unit.text(), "日")
        dialog.repeat_number.setValue(10)
        dialog.deadline_combo.setCurrentIndex(dialog.deadline_combo.findData("set"))
        dialog.deadline_date.setDate(QDate(2026, 8, 29))
        self.assertTrue(dialog.deadline_day.isHidden())
        self.assertFalse(dialog.deadline_date.isHidden())
        dialog.repeat_until_combo.setCurrentIndex(
            dialog.repeat_until_combo.findData("date")
        )
        dialog.repeat_until_date.setDate(dialog.action_date.date().addYears(1))
        self.assertEqual(dialog.repeat_until_combo.currentText(), "指定日期")
        self.assertFalse(dialog.repeat_until_date.isHidden())
        monthly_draft = dialog.draft()
        self.assertEqual(monthly_draft.action_spec["day"], 10)
        self.assertEqual(monthly_draft.deadline_spec, {"type": "offset_days", "days": 5})
        self.assertIn("repeat_until", monthly_draft.action_spec)
        dialog.repeat_until_combo.setCurrentIndex(
            dialog.repeat_until_combo.findData("never")
        )
        self.assertEqual(dialog.repeat_until_combo.currentText(), "永久重复")
        self.assertTrue(dialog.repeat_until_date.isHidden())

        dialog.repeat_combo.setCurrentIndex(
            dialog.repeat_combo.findData("interval_days")
        )
        dialog.deadline_combo.setCurrentIndex(dialog.deadline_combo.findData("set"))
        dialog.deadline_date.setDate(dialog.action_date.date().addDays(7))
        dialog.repeat_until_combo.setCurrentIndex(
            dialog.repeat_until_combo.findData("never")
        )
        dialog.recurrence_mode_combo.setCurrentIndex(1)
        dialog.repeat_number.setValue(90)
        self.assertEqual(dialog.repeat_param_label.text(), "间隔天数")
        self.assertEqual(dialog.draft().recurrence_mode, "completion_date")
        self.assertEqual(dialog.repeat_param_unit.text(), "天")
        self.assertEqual(dialog.draft().action_spec["days"], 90)
        self.assertEqual(dialog.draft().deadline_spec, {"type": "offset_days", "days": 7})
        self.assertEqual(dialog.deadline_combo.currentText(), "设置最晚处理日")
        self.assertTrue(dialog.deadline_combo.isEnabled())

        for mode in (
            "yearly_date",
            "interval_days",
            "nth_weekday_monthly",
            "nth_weekday_yearly",
            "lunar_yearly",
            "cn_a_share_on_or_after",
        ):
            with self.subTest(mode=mode):
                dialog.repeat_combo.setCurrentIndex(dialog.repeat_combo.findData(mode))
                dialog.action_date.setDate(QDate(2026, 9, 10))
                dialog.deadline_combo.setCurrentIndex(dialog.deadline_combo.findData("set"))
                dialog.deadline_date.setDate(QDate(2026, 9, 12))
                self.assertTrue(dialog.deadline_combo.isEnabled())
                self.assertEqual(dialog.draft().deadline_spec, {"type": "offset_days", "days": 2})

    def test_reminder_multi_notify_and_recurrence_controls_persist(self) -> None:
        service = self.window.reminder_service
        reminder = service.create_reminder(ReminderDraft(
            title="FICTIONAL ctexcel UI", lifecycle=REMINDER_ACTIVE,
            action_spec={"type": "interval_days", "anchor_date": "2026-10-01", "days": 90},
        ))
        before = service.get_reminder(reminder.id).occurrence
        dialog = ReminderDialog(reminder, self.window)
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.multi_notify_check.isChecked())
        self.assertEqual(dialog.recurrence_mode_combo.currentData(), "due_date")
        self.assertGreaterEqual(dialog.recurrence_mode_combo.minimumWidth(), 320)
        dialog.multi_notify_check.setChecked(True)
        dialog.recurrence_mode_combo.setCurrentIndex(1)
        saved = service.update_reminder(reminder.id, dialog.draft())
        self.assertEqual(service.get_reminder(reminder.id).occurrence, before)
        reopened = ReminderDialog(saved, self.window)
        self.addCleanup(reopened.close)
        self.assertTrue(reopened.multi_notify_check.isChecked())
        self.assertEqual(reopened.recurrence_mode_combo.currentData(), "completion_date")
        reopened.multi_notify_check.setChecked(False)
        reopened.recurrence_mode_combo.setCurrentIndex(0)
        saved = service.update_reminder(reminder.id, reopened.draft())
        self.assertFalse(saved.multi_notify)
        self.assertEqual(saved.recurrence_mode, "due_date")
        # Old completion-based records round-trip without changing their anchor/spec.
        legacy = service.create_reminder(ReminderDraft(
            title="FICTIONAL legacy", lifecycle=REMINDER_ACTIVE,
            action_spec={"type": "interval_from_completion", "anchor_date": "2026-10-01", "days": 90},
        ))
        legacy_dialog = ReminderDialog(legacy, self.window)
        self.addCleanup(legacy_dialog.close)
        legacy_dialog.multi_notify_check.setChecked(True)
        draft = legacy_dialog.draft()
        self.assertEqual(draft.action_spec, legacy.action_spec)
        self.assertIsNone(draft.recurrence_mode)
        self.assertEqual(legacy_dialog.recurrence_mode_combo.currentData(), "completion_date")
        legacy_dialog.repeat_number.setValue(60)
        self.assertEqual(legacy_dialog.draft().recurrence_mode, "completion_date")
        legacy_dialog.repeat_combo.setCurrentIndex(legacy_dialog.repeat_combo.findData("monthly_day"))
        self.assertFalse(legacy_dialog.form.isRowVisible(legacy_dialog.recurrence_mode_label))
        self.assertIsNone(legacy_dialog.draft().recurrence_mode)

    def test_reminder_dialog_nth_weekday_yearly_and_monthly_round_trip(self) -> None:
        dialog = ReminderDialog(parent=self.window)
        self.addCleanup(dialog.close)
        dialog.title_input.setText("FICTIONAL_FATHERS_DAY_UI")
        dialog.repeat_combo.setCurrentIndex(
            dialog.repeat_combo.findData("nth_weekday_yearly")
        )
        dialog.action_date.setDate(QDate(2027, 6, 20))
        draft = dialog.draft()
        self.assertEqual(
            draft.action_spec,
            {"type": "nth_weekday", "nth": 3, "weekday": 6, "month": 6},
        )
        yearly = self.window.reminder_service.create_reminder(draft)
        edit_dialog = ReminderDialog(yearly, self.window)
        self.addCleanup(edit_dialog.close)
        self.assertEqual(edit_dialog.repeat_combo.currentData(), "nth_weekday_yearly")
        self.assertEqual(edit_dialog.draft().action_spec, draft.action_spec)

        monthly = self.window.reminder_service.create_reminder(
            ReminderDraft(
                title="FICTIONAL_NTH_MONTHLY_UI",
                lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "nth_weekday", "nth": 2, "weekday": 0},
                deadline_spec=None,
                note="",
            )
        )
        monthly_dialog = ReminderDialog(monthly, self.window)
        self.addCleanup(monthly_dialog.close)
        self.assertEqual(monthly_dialog.repeat_combo.currentData(), "nth_weekday_monthly")
        self.assertEqual(
            monthly_dialog.draft().action_spec,
            {"type": "nth_weekday", "nth": 2, "weekday": 0},
        )

    def test_reminder_label_manager_crud_reorder_and_default_guard(self) -> None:
        service = self.window.reminder_service
        dialog = ReminderLabelManagerDialog(service, self.window)
        self.addCleanup(dialog.close)

        default = service.default_label()
        dialog.label_list.setCurrentRow(0)
        dialog.name_input.setText("默认提醒")
        dialog._rename_label()
        self.assertEqual(service.default_label().name, "默认提醒")
        dialog._delete_label()
        self.assertIn(default.id, [label.id for label in service.list_labels()])
        self.assertIn("不可删除", dialog.status_label.text())

        dialog.name_input.setText("网络维护")
        dialog._create_label()
        network = next(label for label in service.list_labels() if label.name == "网络维护")
        dialog.name_input.setText("订阅续费")
        dialog._create_label()
        renewal = next(label for label in service.list_labels() if label.name == "订阅续费")
        dialog.refresh(network.id)
        dialog.name_input.setText("网络维护改名")
        dialog._rename_label()
        self.assertIn("网络维护改名", [label.name for label in service.list_labels()])

        dialog.refresh(renewal.id)
        dialog._move_selected(-1)
        dialog._move_selected(-1)
        self.assertEqual(service.list_labels()[0].id, renewal.id)

        dialog.refresh(network.id)
        dialog._delete_label()
        self.assertNotIn(network.id, [label.id for label in service.list_labels()])

    def test_reminder_table_context_menu_uses_labels_not_note_categories(self) -> None:
        service = self.window.reminder_service
        label = service.create_label("订阅续费")
        table = service.create_table("FICTIONAL_CONTEXT_TABLE", label_id=label.id)
        self.window.show_smart_view("reminder")
        self.app.processEvents()
        sidebar = self.window.shell.sidebar
        menu = sidebar._build_reminder_table_context_menu(table.id)
        self.addCleanup(menu.deleteLater)
        action_texts = [action.text() for action in menu.actions()]
        self.assertEqual(action_texts, ["重命名", "移动到", "移入回收站"])
        move_menu = next(action.menu() for action in menu.actions() if action.text() == "移动到")
        move_labels = [action.text() for action in move_menu.actions()]
        self.assertIn("未归类", move_labels)
        self.assertIn("订阅续费", move_labels)
        self.assertNotIn("固定笔记", action_texts)
        self.assertNotIn("临时", move_labels)
        self.assertNotIn("S-notes", move_labels)

    def test_reminder_dialog_calendar_and_combo_follow_themes(self) -> None:
        for theme_name, tokens in THEMES.items():
            with self.subTest(theme=theme_name):
                apply_theme(self.app, theme_name)
                dialog = ReminderDialog(parent=self.window)
                self.addCleanup(dialog.close)
                calendar = dialog.action_date.calendarWidget()
                self.app.processEvents()
                self.assertEqual(
                    calendar.palette().color(QPalette.Base).name().lower(),
                    QColor(tokens.surface).name().lower(),
                )
                self.assertIsNotNone(
                    calendar.findChild(QAbstractItemView, "reminderCalendarView")
                )
                month_button = calendar.findChild(QToolButton, "qt_calendar_monthbutton")
                self.assertIsNotNone(month_button)
                self.assertEqual(month_button.menu().objectName(), "reminderCalendarMonthMenu")
                self.assertEqual(
                    month_button.menu().palette().color(QPalette.WindowText).name().lower(),
                    QColor(tokens.text).name().lower(),
                )
                self.assertEqual(dialog.repeat_combo.view().objectName(), "comboPopupView")

    def test_ai_center_reminder_operation_log_table_follows_live_theme(self) -> None:
        service = self.window.reminder_service
        table = service.create_table("FICTIONAL_LOG_THEME_TABLE")
        service.ai_create_item(
            actor_target_id="clawbot",
            request_id="fictional-ui-reminder-log-theme",
            table_id=table.id,
            draft=ReminderDraft(
                title="FICTIONAL_LOG_THEME_ITEM",
                lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "one_time", "date": "2026-09-10"},
                table_id=table.id,
            ),
        )

        self.window.set_theme("dark")
        self.window.open_ai_center()
        center = self.window.ai_center_dialog
        center.tabs.setCurrentWidget(center.reminder_log_page)
        center.refresh_reminder_logs(reset=True)
        self.app.processEvents()
        self.assertGreaterEqual(center.reminder_log_table.rowCount(), 1)

        for theme_name, tokens in THEMES.items():
            with self.subTest(theme=theme_name):
                self.window.set_theme(theme_name)
                self.app.processEvents()
                table_widget = center.reminder_log_table
                self.assertEqual(
                    table_widget.viewport().palette().color(QPalette.Base).name().lower(),
                    QColor(tokens.surface).name().lower(),
                )
                self.assertEqual(
                    table_widget.horizontalHeader().palette().color(QPalette.Window).name().lower(),
                    QColor(tokens.surface).name().lower(),
                )
                self.assertEqual(
                    table_widget.palette().color(QPalette.Highlight).name().lower(),
                    QColor(tokens.surface_selected).name().lower(),
                )
                self.assertIn("#aiReminderLogTable", build_app_style(theme_name))

    def test_reminder_center_button_states_and_completed_occurrence_text(self) -> None:
        service = self.window.reminder_service
        reminder = service.create_reminder(
            ReminderDraft(
                title="FICTIONAL_MONTHLY_UI",
                lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "monthly_day", "day": 20},
                deadline_spec=None,
                note="",
            )
        )
        center = self.window.shell.reminder_center
        self.window.set_theme("light-slate")
        center.refresh()
        self.window.show()
        self.app.processEvents()
        self.assertIsInstance(center.topic_input, QLineEdit)
        self.assertTrue(center.topic_input.isReadOnly())
        self.assertTrue(center.topic_input.text().startswith("snotes-reminder-"))
        self.assertGreaterEqual(
            center.topic_input.width(),
            center.topic_input.fontMetrics().horizontalAdvance(center.topic_input.text()),
        )
        self.assertEqual(center.copy_topic_button.text(), "复制 Topic")
        self.assertEqual(center.table_title_input.objectName(), "reminderTableTitleInput")
        self.assertEqual(center.title_separator.height(), 2)
        self.assertNotEqual(center.table_title_input.palette().color(QPalette.Text), QColor())
        self.assertEqual(center.meta_summary.text(), "未归类 · AI 可见")
        self.assertTrue(center.meta_panel.isHidden())
        center.meta_summary.click()
        self.app.processEvents()
        self.assertFalse(center.meta_panel.isHidden())
        self.assertFalse(any(label.text() == "元数据" for label in center.findChildren(QLabel)))
        self.assertIsInstance(center.table_label_combo, FloatingComboBox)
        self.assertIsInstance(center.table_ai_combo, FloatingComboBox)
        self.assertIsInstance(center.delete_table_button, QToolButton)
        self.assertEqual(center.delete_table_button.text(), "")
        self.assertEqual(center.delete_table_button.toolTip(), "删除表格")
        self.assertNotIn(
            "发送测试通知",
            [button.text() for button in center.findChildren(QPushButton)],
        )
        self.assertNotIn(
            "删除当前 Table",
            [button.text() for button in center.findChildren(QPushButton)],
        )
        self.assertFalse(
            any("时区" in label.text() or "服务器" in label.text() for label in center.findChildren(QLabel))
        )
        self.assertEqual(center.findChildren(QLabel, "reminderSettingsHint"), [])
        self.assertEqual(
            [center.table.horizontalHeaderItem(i).text() for i in range(6)],
            ["事项", "状态", "开始处理日", "重复", "最晚处理日", "备注"],
        )
        self.assertEqual(center.table_title_input.text(), "原有提醒")
        self.assertIn("AI 可见", center.meta_summary.text())
        self.assertEqual(
            center.table.horizontalHeader().defaultAlignment(),
            Qt.AlignLeft | Qt.AlignVCenter,
        )
        center.table.resize(1000, center.table.height())
        center._apply_table_widths()
        self.assertGreaterEqual(center.table.columnWidth(1), 160)
        self.assertGreaterEqual(center.table.columnWidth(3), 180)
        self.assertGreaterEqual(center.table.columnWidth(5), 240)
        self.assertGreater(
            sum(center.table.columnWidth(column) for column in range(6)),
            center.table.viewport().width(),
        )
        self.assertEqual(
            center.table.item(0, 0).textAlignment(),
            Qt.AlignLeft | Qt.AlignVCenter,
        )
        center.table.setCurrentCell(0, 0)
        self.app.processEvents()
        self.assertEqual(
            center.table.palette().color(QPalette.Highlight),
            QColor(THEMES["light-slate"].accent_soft),
        )
        self.assertTrue(center.pause_button.isEnabled())
        self.assertFalse(center.resume_button.isEnabled())
        self.assertTrue(center.complete_occurrence_button.isEnabled())
        self.assertFalse(center.complete_button.isEnabled())

        service.complete_occurrence(reminder.id)
        center.refresh()
        self.assertIn("本次已完成 · 下次", center.table.item(0, 1).text())
        center.table.setCurrentCell(0, 0)
        self.app.processEvents()
        self.assertFalse(center.resume_button.isEnabled())
        self.assertFalse(center.complete_occurrence_button.isEnabled())

        service.pause_reminder(reminder.id)
        center.refresh()
        center.table.setCurrentCell(0, 0)
        self.app.processEvents()
        self.assertFalse(center.pause_button.isEnabled())
        self.assertTrue(center.resume_button.isEnabled())


    def test_all_sidebar_controls_use_sidebar_tokens_in_three_themes(self) -> None:
        sidebar = self.window.shell.sidebar
        self.window.show()
        for theme_name in ("dark", "light-slate", "light-clay"):
            with self.subTest(theme=theme_name):
                self.window.set_theme(theme_name)
                self.app.processEvents()
                tokens = THEMES[theme_name]
                palette = sidebar.note_list.palette()
                self.assertEqual(palette.color(QPalette.Text), QColor(tokens.sidebar_text_muted))
                self.assertEqual(palette.color(QPalette.Highlight), QColor(tokens.sidebar_selected))
                self.assertEqual(palette.color(QPalette.HighlightedText), QColor(tokens.sidebar_text))
                style = build_app_style(theme_name)
                self.assertIn(f"color: {tokens.sidebar_text};", style)
                self.assertIn(f"color: {tokens.sidebar_text_muted};", style)
                self.assertIn(f"color: {tokens.sidebar_text_subtle};", style)
                self.assertIn("#sidebarSearch, #categoryFilter", style)
                self.assertIn("#trashTree::item:selected", style)
                self.assertIn(
                    f"QScrollBar::handle:vertical {{ min-height: 24px; margin: 0 1px; border-radius: 3px; background: {tokens.scrollbar}; }}",
                    style,
                )
                self.assertIn(
                    f"QScrollBar::handle:horizontal {{ min-width: 24px; margin: 1px 0; border-radius: 3px; background: {tokens.scrollbar}; }}",
                    style,
                )


if __name__ == "__main__":
    unittest.main()
