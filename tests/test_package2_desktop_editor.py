from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QPalette, QTextCursor, QTextFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem

from src.app_paths import default_data_root
from src.snotes.application.note_service import NoteService
from src.snotes.bootstrap import resolve_rebuild_data_root
from src.snotes.domain.models import NoteLocation
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.ui.editor import CODE_BLOCK_PROPERTY, QUOTE_BLOCK_PROPERTY, NotesEditor
from src.snotes.ui.main_window import CONFLICT_MESSAGE, MainWindow
from src.snotes.ui.sidebar import NOTE_DATE_ROLE, NOTE_TITLE_ROLE, note_date_width
from src.snotes.ui.theme import THEMES, build_app_style


class Package2DesktopEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-package2-")
        self.data_root = Path(self.temporary.name)
        connections = initialize_database(self.data_root / "s_notes.db")
        directories = SQLiteDirectoriesRepository(connections)
        self.service = NoteService(SQLiteNotesRepository(connections, directories))
        self.window = MainWindow(
            self.service,
            theme_settings_path=self.data_root / "config" / "ui_settings.ini",
        )

    def tearDown(self) -> None:
        self.window.has_unsaved_changes = False
        self.window.close()
        self.app.processEvents()
        self.temporary.cleanup()

    def test_create_load_autosave_switch_and_html_reload(self) -> None:
        first = self.service.create_note()
        second = self.service.create_note()
        self.assertTrue(self.window.load_note(first.id))
        self.window.AUTOSAVE_DELAY_MS = 1
        self.window.title_input.setText("虚构自动保存标题")
        self.window.body_edit.setPlainText("虚构正文")
        cursor = self.window.body_edit.textCursor()
        cursor.select(QTextCursor.Document)
        self.window.body_edit.setTextCursor(cursor)
        self.window.body_edit.apply_bold()
        QTest.qWait(10)
        self.app.processEvents()

        stored = self.service.get_note(first.id)
        self.assertEqual(stored.title, "虚构自动保存标题")
        self.assertGreater(stored.revision, first.revision)
        self.assertIn("font-weight", stored.content_html)
        self.assertTrue(self.window.switch_note(second.id) is None)
        self.window.switch_note(first.id)
        self.assertEqual(self.window.title_input.text(), "虚构自动保存标题")
        self.assertEqual(self.window.body_edit.toPlainText(), "虚构正文")

        restarted = MainWindow(
            self.service,
            theme_settings_path=self.data_root / "config" / "ui_settings.ini",
        )
        try:
            restarted.load_note(first.id)
            self.assertEqual(restarted.body_edit.toPlainText(), "虚构正文")
            self.assertIn("font-weight", restarted.body_edit.note_content_html())
        finally:
            restarted.close()

    def test_new_note_button_creates_and_selects_note(self) -> None:
        self.assertEqual(self.service.list_active_notes(), [])
        self.window.shell.sidebar.new_note_button.click()
        self.assertIsNotNone(self.window.current_note)
        self.assertEqual(len(self.service.list_active_notes()), 1)
        self.assertEqual(
            self.window.shell.sidebar.note_list.currentItem().data(Qt.UserRole),
            self.window.current_note.id,
        )

    def test_revision_conflict_preserves_editor_and_database(self) -> None:
        note = self.service.create_note()
        self.window.load_note(note.id)
        self.window.body_edit.setPlainText("本窗口未保存内容")
        self.window.title_input.setText("本窗口未保存标题")
        self.window.save_timer.stop()
        cursor = self.window.body_edit.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.window.body_edit.setTextCursor(cursor)
        self.window.body_edit.insertPlainText("仍在")
        self.assertTrue(self.window.body_edit.document().isUndoAvailable())

        external = self.service.save_note(
            note.id,
            title="外部较新标题",
            content_html="<p>外部较新正文</p>",
            location=NoteLocation(),
            expected_revision=note.revision,
        )
        self.assertFalse(self.window.save_current_note())

        stored = self.service.get_note(note.id)
        self.assertEqual(stored, external)
        self.assertEqual(self.window.title_input.text(), "本窗口未保存标题")
        self.assertEqual(self.window.body_edit.toPlainText(), "本窗口未保存内容仍在")
        self.assertTrue(self.window.body_edit.document().isUndoAvailable())
        self.assertEqual(self.window.current_note.revision, note.revision)
        self.assertEqual(self.window.status_label.text(), CONFLICT_MESSAGE)
        self.assertTrue(self.window.shell.reload_button.isVisibleTo(self.window.shell))
        self.assertTrue(self.window.has_unsaved_changes)

    def test_conflict_blocks_note_switch_until_explicit_reload(self) -> None:
        first = self.service.create_note()
        second = self.service.create_note()
        self.window.load_note(first.id)
        self.window.title_input.setText("未保存冲突标题")
        self.window.save_timer.stop()
        self.service.save_note(
            first.id,
            title="外部版本",
            content_html="<p>外部</p>",
            location=first.location,
            expected_revision=first.revision,
        )

        self.window.switch_note(second.id)
        self.assertEqual(self.window.current_note.id, first.id)
        self.assertEqual(self.window.title_input.text(), "未保存冲突标题")
        self.window.reload_after_conflict()
        self.assertEqual(self.window.title_input.text(), "外部版本")
        self.window.switch_note(second.id)
        self.assertEqual(self.window.current_note.id, second.id)

    def test_note_list_uses_r1_order_and_excludes_deleted(self) -> None:
        first = self.service.create_note()
        second = self.service.create_note()
        pinned = self.service.set_note_pinned(first.id, True)
        self.service.soft_delete_note(second.id)
        active = self.service.list_active_notes()
        self.assertEqual([note.id for note in active], [pinned.id])

    def test_theme_switch_preserves_unsaved_html_selection_and_cursor(self) -> None:
        note = self.service.create_note()
        self.window.load_note(note.id)
        self.window.body_edit.setPlainText("虚构主题切换正文")
        cursor = self.window.body_edit.textCursor()
        cursor.setPosition(2)
        cursor.setPosition(7, QTextCursor.KeepAnchor)
        self.window.body_edit.setTextCursor(cursor)
        before_html = self.window.body_edit.note_content_html()

        self.window.set_theme("dark")

        after_cursor = self.window.body_edit.textCursor()
        self.assertEqual(self.window.body_edit.note_content_html(), before_html)
        self.assertEqual((after_cursor.selectionStart(), after_cursor.selectionEnd()), (2, 7))
        self.assertEqual(self.window.current_theme_name, "dark")
        self.assertIn("dark", (self.data_root / "config" / "ui_settings.ini").read_text())

    def test_eight_internal_font_sizes_survive_save_and_reload(self) -> None:
        for pixel_size in (11, 13, 15, 17, 20, 23, 26, 32):
            with self.subTest(pixel_size=pixel_size):
                note = self.service.create_note()
                self.window.load_note(note.id)
                self.window.body_edit.setPlainText(f"虚构 {pixel_size}px 正文")
                cursor = self.window.body_edit.textCursor()
                cursor.select(QTextCursor.Document)
                self.window.body_edit.setTextCursor(cursor)
                self.window.body_edit.apply_font_size(pixel_size)
                self.assertTrue(self.window.force_save_current_note())
                stored = self.service.get_note(note.id)
                self.assertIn(
                    f"font-size:{pixel_size}px",
                    stored.content_html.replace(" ", "").lower(),
                )
                self.window.load_note(note.id)
                restored = QTextCursor(self.window.body_edit.document())
                restored.setPosition(0)
                restored.setPosition(1, QTextCursor.KeepAnchor)
                self.assertEqual(
                    restored.charFormat().property(QTextFormat.FontPixelSize),
                    pixel_size,
                )

    def test_old_list_and_quote_html_still_load_without_toolbar_entries(self) -> None:
        editor = NotesEditor()
        try:
            editor.set_note_content(
                "<ul><li>虚构旧列表</li></ul><blockquote>虚构旧引用</blockquote>"
            )
            first = editor.document().firstBlock()
            second = first.next()
            self.assertIsNotNone(first.textList())
            self.assertTrue(second.blockFormat().property(QUOTE_BLOCK_PROPERTY))
        finally:
            editor.close()

    def test_sidebar_reserves_complete_date_column_and_elides_long_title(self) -> None:
        note = self.service.create_note()
        long_title = "虚构超长标题" * 30
        saved = self.service.save_note(
            note.id,
            title=long_title,
            content_html="<p>虚构正文</p>",
            location=note.location,
            expected_revision=note.revision,
        )
        self.window.resize(900, 520)
        self.window.show()
        self.window.refresh_note_list(saved.id)
        self.app.processEvents()
        note_list = self.window.shell.sidebar.note_list
        item = note_list.item(0)
        self.assertEqual(item.data(NOTE_TITLE_ROLE), long_title)
        self.assertEqual(item.data(NOTE_DATE_ROLE), saved.updated_at[5:10])
        option = QStyleOptionViewItem()
        delegate = note_list.itemDelegate()
        delegate.initStyleOption(option, note_list.indexFromItem(item))
        option.rect = QRect(0, 0, 248, 32)
        title_rect, date_rect = delegate.column_rects(option)
        self.assertGreaterEqual(date_rect.width(), note_date_width(option.fontMetrics))
        self.assertLess(title_rect.right(), date_rect.left())
        self.assertNotEqual(
            option.fontMetrics.elidedText(long_title, Qt.ElideRight, title_rect.width()),
            long_title,
        )

    def test_single_surface_menu_indicator_and_fine_focus_lines(self) -> None:
        expected = {
            "dark": ("#1B1D1E", "#393D3B", "#76503E", "#CB8A67"),
            "light-slate": ("#F0F2F5", "#DCDCDC", "#B9C0C6", "#526C83"),
            "light-clay": ("#FFFFFF", "#D6CEC5", "#CFC5BA", "#A84B2F"),
        }
        for theme_name, (editor, border, title_separator, focus) in expected.items():
            with self.subTest(theme=theme_name):
                style = "".join(build_app_style(theme_name).split())
                self.assertIn(
                    "QToolButton::menu-indicator{image:none;width:0;}",
                    style,
                )
                self.assertIn(
                    f"#mainContentArea,#editorPane{{background:{editor};}}",
                    style,
                )
                self.assertIn("QSplitter::handle{background:", style)
                self.assertIn("#editorToolbar{background:transparent;", style)
                self.assertIn("#findPanel{background:transparent;", style)
                self.assertIn(
                    f"#titleSeparator{{border:0;min-height:2px;max-height:2px;"
                    f"background:{title_separator};}}",
                    style,
                )
                self.assertIn(
                    f"#bodyEdit:focus{{border:1pxsolid{focus};}}",
                    style,
                )
                self.assertIn(
                    f"#bodyEdit{{border:1pxsolid{border};border-radius:4px;"
                    "background:transparent;",
                    style,
                )
                self.assertIn(
                    f"#bodyEdit:focus{{border:1pxsolid{focus};}}",
                    style,
                )
                self.assertIn(
                    f"#toolbarInput{{border:1pxsolid{border};",
                    style,
                )

        margins = self.window.shell.editor_pane.layout().contentsMargins()
        self.assertEqual(
            (margins.left(), margins.top(), margins.right(), margins.bottom()),
            (24, 16, 24, 12),
        )
        self.assertEqual(self.window.body_edit.document().documentMargin(), 2.0)
        self.assertEqual(self.window.shell.splitter.handleWidth(), 4)

    def test_three_themes_render_sidebar_and_editor_as_two_real_surfaces(self) -> None:
        note = self.service.create_note()
        self.assertTrue(self.window.load_note(note.id))
        self.window.resize(1180, 760)
        self.window.show()

        def rendered_color(widget, x: int, y: int) -> QColor:
            position = widget.mapTo(self.window, QPoint(x, y))
            return self.window.grab().toImage().pixelColor(position)

        for theme_name in ("dark", "light-slate", "light-clay"):
            with self.subTest(theme=theme_name):
                self.window.set_theme(theme_name)
                self.app.processEvents()
                tokens = THEMES[theme_name]
                sidebar_color = QColor(tokens.sidebar)
                editor_color = QColor(tokens.editor)
                sidebar = self.window.shell.sidebar
                note_viewport = sidebar.note_list.viewport()
                body_viewport = self.window.body_edit.viewport()
                toolbar_row = self.window.shell.editor_toolbar.toolbar_rows[0]

                self.assertEqual(
                    sidebar.palette().color(QPalette.Window),
                    sidebar_color,
                )
                self.assertEqual(
                    note_viewport.palette().color(QPalette.Base).alpha(),
                    0,
                )
                self.assertEqual(
                    self.window.shell.editor_pane.palette().color(QPalette.Window),
                    editor_color,
                )
                self.assertEqual(
                    body_viewport.palette().color(QPalette.Base).alpha(),
                    0,
                )

                sidebar_samples = (
                    rendered_color(sidebar, 4, 4),
                    rendered_color(
                        note_viewport,
                        4,
                        note_viewport.height() - 4,
                    ),
                    rendered_color(sidebar, 4, sidebar.height() - 4),
                )
                self.assertTrue(
                    all(color == sidebar_color for color in sidebar_samples),
                    sidebar_samples,
                )

                editor_samples = (
                    rendered_color(self.window.shell.editor_pane, 4, 4),
                    rendered_color(
                        self.window.title_input,
                        self.window.title_input.width() - 4,
                        4,
                    ),
                    rendered_color(
                        toolbar_row,
                        toolbar_row.width() - 4,
                        toolbar_row.height() // 2,
                    ),
                    rendered_color(
                        body_viewport,
                        body_viewport.width() // 2,
                        body_viewport.height() - 4,
                    ),
                    rendered_color(
                        self.window.status_label,
                        self.window.status_label.width() - 4,
                        self.window.status_label.height() // 2,
                    ),
                )
                self.assertTrue(
                    all(color == editor_color for color in editor_samples),
                    editor_samples,
                )

    def test_standard_editor_context_menu_is_opaque_in_all_themes(self) -> None:
        note = self.service.create_note()
        self.assertTrue(self.window.load_note(note.id))
        self.window.show()

        for theme_name in ("dark", "light-slate", "light-clay"):
            with self.subTest(theme=theme_name):
                self.window.set_theme(theme_name)
                self.app.processEvents()
                tokens = THEMES[theme_name]
                menu = self.window.body_edit.createStandardContextMenu()
                try:
                    menu.ensurePolished()
                    palette = menu.palette()
                    background = palette.color(QPalette.Window)
                    foreground = palette.color(QPalette.WindowText)
                    disabled = palette.color(
                        QPalette.Disabled,
                        QPalette.WindowText,
                    )
                    self.assertEqual(background, QColor(tokens.sidebar))
                    self.assertEqual(background.alpha(), 255)
                    self.assertEqual(foreground, QColor(tokens.sidebar_text))
                    self.assertNotEqual(foreground, background)
                    self.assertEqual(
                        disabled,
                        QColor(tokens.sidebar_text_subtle),
                    )
                    self.assertNotEqual(disabled, background)
                    self.assertEqual(
                        palette.color(QPalette.Highlight),
                        QColor(tokens.sidebar_selected),
                    )
                    self.assertTrue(menu.testAttribute(Qt.WA_StyledBackground))
                    self.assertFalse(
                        menu.testAttribute(Qt.WA_TranslucentBackground)
                    )
                    self.assertFalse(
                        menu.testAttribute(Qt.WA_NoSystemBackground)
                    )
                    self.assertTrue(menu.autoFillBackground())
                finally:
                    menu.close()
                    menu.deleteLater()

    def test_rebuild_data_root_defaults_to_formal_and_override_is_nonformal(
        self,
    ) -> None:
        previous = os.environ.pop("S_NOTES_DATA_ROOT", None)
        try:
            formal_root = default_data_root(environ={})
            self.assertEqual(resolve_rebuild_data_root(), formal_root)
            with tempfile.TemporaryDirectory() as temporary_directory:
                isolated = Path(temporary_directory) / "candidate"
                self.assertEqual(resolve_rebuild_data_root(isolated), isolated)
            with self.assertRaisesRegex(RuntimeError, "formal S-notes data root"):
                resolve_rebuild_data_root(formal_root)
        finally:
            if previous is not None:
                os.environ["S_NOTES_DATA_ROOT"] = previous

    def test_rich_paste_keeps_structure_strike_and_rejects_unsafe_markup(self) -> None:
        editor = NotesEditor()
        try:
            mime = QMimeData()
            mime.setText("Alpha\nBeta\n")
            mime.setHtml(
                "<script>bad()</script><p><b>Alpha</b></p>"
                "<p><s>Beta</s></p>"
            )
            editor.insertFromMimeData(mime)
            self.assertEqual(editor.toPlainText(), "Alpha\nBeta")
            html = editor.note_content_html().lower()
            self.assertNotIn("script", html)
            self.assertIn("line-through", html)
            self.assertIn("font-weight", html)
        finally:
            editor.close()

    def test_return_preserves_size_and_structures_then_exits_empty_block(self) -> None:
        for structure in ("list", "quote", "code"):
            with self.subTest(structure=structure):
                editor = NotesEditor()
                try:
                    editor.setPlainText("虚构段落")
                    cursor = editor.textCursor()
                    cursor.select(QTextCursor.Document)
                    editor.setTextCursor(cursor)
                    editor.apply_font_size(20)
                    cursor.movePosition(QTextCursor.End)
                    editor.setTextCursor(cursor)
                    if structure == "list":
                        editor.apply_list(False)
                    elif structure == "quote":
                        editor.apply_quote()
                    else:
                        editor.apply_code_block()
                    QTest.keyClick(editor, Qt.Key_Return)
                    block = editor.textCursor().block()
                    self.assertEqual(
                        editor.currentCharFormat().property(QTextFormat.FontPixelSize),
                        20,
                    )
                    if structure == "list":
                        self.assertIsNotNone(block.textList())
                    elif structure == "quote":
                        self.assertTrue(block.blockFormat().property(QUOTE_BLOCK_PROPERTY))
                    else:
                        self.assertTrue(block.blockFormat().property(CODE_BLOCK_PROPERTY))
                    QTest.keyClick(editor, Qt.Key_Return)
                    exited = editor.textCursor().block()
                    if structure == "list":
                        self.assertIsNone(exited.textList())
                    elif structure == "quote":
                        self.assertFalse(exited.blockFormat().property(QUOTE_BLOCK_PROPERTY))
                    else:
                        self.assertFalse(editor._is_code_block_format(exited.blockFormat()))
                finally:
                    editor.close()

    def test_link_is_applied_only_to_selection_with_safe_scheme(self) -> None:
        editor = NotesEditor()
        try:
            editor.setPlainText("链接文字")
            cursor = editor.textCursor()
            cursor.select(QTextCursor.Document)
            editor.setTextCursor(cursor)
            self.assertFalse(editor.apply_link("javascript:alert(1)"))
            self.assertTrue(editor.apply_link("https://example.invalid/path"))
            self.assertIn("https://example.invalid/path", editor.note_content_html())
        finally:
            editor.close()


if __name__ == "__main__":
    unittest.main()
