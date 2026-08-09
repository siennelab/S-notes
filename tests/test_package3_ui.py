from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontInfo, QPalette
from PySide6.QtWidgets import QApplication, QComboBox, QPushButton, QTreeWidget

from src.snotes.bootstrap import build_application
from src.snotes.domain.models import NoteLocation
from src.snotes.ui.sidebar import PAYLOAD_ROLE
from src.snotes.ui.directory_dialog import DirectoryManagerDialog
from src.snotes.ui.floating_combo import FloatingComboBox
from src.snotes.ui.sidebar_widgets import sidebar_clock_time_width
from src.snotes.ui.theme import (
    THEMES,
    THEME_SWATCH_COLORS,
    THEME_SWATCH_RING_COLORS,
    build_app_style,
)


class Package3UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data_root = Path(self.temporary.name) / "data"
        _application, self.window = build_application([], data_root=self.data_root)
        self.addCleanup(self.window.close)

    def create_note(self, title: str, body: str, location=NoteLocation(), tags=()):
        note = self.window.note_service.create_note(location)
        note = self.window.note_service.save_note(
            note.id,
            title=title,
            content_html=body,
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
        self.assertEqual(moved.location, NoteLocation("客户", "项目甲", "无"))

        self.create_note("范围外", "<p>虚构</p>")
        self.window.show_directory_filter(("客户", "项目甲"))
        self.assertEqual(
            [row.id for row in self.window.visible_notes],
            [
                row.id
                for row in self.window.note_service.notes_for_directory(
                    NoteLocation("客户", "项目甲", "无")
                )
            ],
        )

        self.window.set_note_pinned(note.id, True)
        self.assertTrue(self.window.note_service.get_note(note.id).is_pinned)
        self.window.load_note(note.id)
        self.window.shell.tags_input.setText("One, #two，one")
        self.window.save_timer.stop()
        self.assertTrue(self.window.save_current_note())
        self.assertEqual(self.window.tag_service.get_note_tags(note.id), ["one", "two"])

    def test_trash_tree_preserves_deleted_directory_note_child_and_restore(self) -> None:
        root = self.window.directory_service.create_directory("删除树")
        child = self.window.directory_service.create_directory("子层", root.id)
        note = self.create_note("树内笔记", "<p>虚构</p>", NoteLocation("删除树", "子层", "无"))
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
        self.window.load_note(current.id)
        self.window.toggle_links()
        self.app.processEvents()
        self.assertFalse(self.window.shell.link_drawer.isHidden())
        self.assertEqual(self.window.shell.link_drawer.results.count(), 1)
        item = self.window.shell.link_drawer.results.item(0)
        self.assertEqual(item.data(Qt.UserRole), target.id)

    def test_sidebar_restores_r1_information_structure(self) -> None:
        sidebar = self.window.shell.sidebar
        self.assertEqual(sidebar.search_input.placeholderText(), "搜索笔记或 #标签")
        self.assertEqual(set(sidebar.smart_buttons), {"recent", "todo", "ai"})
        self.assertFalse(sidebar.ai_center_button.isEnabled())
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
        self.window.load_note(note.id)
        self.window.show()
        self.app.processEvents()
        shell = self.window.shell
        self.assertLess(shell.pin_button.geometry().top(), shell.body_editor.geometry().top())
        self.assertLess(shell.links_button.geometry().top(), shell.body_editor.geometry().top())
        self.assertLess(shell.export_button.geometry().top(), shell.body_editor.geometry().top())
        self.assertGreater(shell.delete_button.mapTo(shell.editor_pane, shell.delete_button.rect().topLeft()).y(), shell.body_editor.geometry().top())

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
        self.assertEqual(smart_labels, ["最近", "待办", "AI资料", "AI中心"])
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

        for name in ("recent", "todo", "ai"):
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

    def test_metadata_hierarchy_theme_swatches_and_selection_palette(self) -> None:
        note = self.create_note("层级对照", "<p>这是用于主题选择的虚构正文</p>")
        self.window.load_note(note.id)
        shell = self.window.shell
        shell.set_metadata_expanded(True)
        self.window.resize(1420, 880)
        self.window.show()
        self.app.processEvents()
        self.assertEqual(shell.title_separator.height(), 2)
        self.assertLess(shell.title_band.geometry().top(), shell.title_separator.geometry().top())
        self.assertLess(shell.title_separator.geometry().top(), shell.metadata_section.geometry().top())
        self.assertLess(shell.metadata_section.geometry().top(), shell.editor_toolbar.geometry().top())
        self.assertLess(shell.editor_toolbar.geometry().top(), shell.body_editor.geometry().top())
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
        self.assertEqual(len(shell.ai_target_checks), 6)
        self.assertIsInstance(shell.ai_visibility_combo, FloatingComboBox)
        self.assertIsInstance(shell.main_combo, FloatingComboBox)
        self.assertIsInstance(shell.sub_combo, FloatingComboBox)

        for theme_name, tokens in THEMES.items():
            with self.subTest(theme=theme_name):
                self.window.set_theme(theme_name)
                self.app.processEvents()
                body_palette = shell.body_editor.palette()
                viewport_palette = shell.body_editor.viewport().palette()
                for group in (QPalette.Active, QPalette.Inactive):
                    self.assertEqual(
                        body_palette.color(group, QPalette.Highlight),
                        QColor(tokens.selection_background),
                    )
                    self.assertEqual(
                        viewport_palette.color(group, QPalette.Highlight),
                        QColor(tokens.selection_background),
                    )
                    self.assertEqual(
                        body_palette.color(group, QPalette.HighlightedText),
                        QColor(tokens.selection_text),
                    )
                self.assertIn(
                    f"#bodyEdit:focus {{ border: 1px solid {tokens.focus}; }}",
                    build_app_style(theme_name),
                )
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


if __name__ == "__main__":
    unittest.main()
