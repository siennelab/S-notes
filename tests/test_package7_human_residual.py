from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QPalette, QTextCursor, QTextFormat
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from src.snotes.bootstrap import build_application
from src.snotes.domain.models import NoteLocation
from src.snotes.domain.relations import RelatedNote
from src.snotes.ui.editor import NotesEditor
from src.snotes.ui.editor_toolbar import EditorToolbar
from src.snotes.ui.link_drawer import LinkDrawer
from src.snotes.ui.shell import SnotesShell
from src.snotes.ui.theme import THEMES, apply_theme, build_app_style


class Package7HumanResidualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        apply_theme(self.app, "light-slate")
        self.app.processEvents()

    @staticmethod
    def _find_highlights(editor: NotesEditor, query: str | None = None) -> list:
        return [
            selection
            for selection in editor.extraSelections()
            if selection.format.hasProperty(QTextFormat.BackgroundBrush)
            and (query is None or selection.cursor.selectedText() == query)
        ]

    def test_toolbar_url_is_absent_but_existing_hyperlink_html_round_trips(
        self,
    ) -> None:
        editor = NotesEditor()
        toolbar = EditorToolbar(editor)
        with tempfile.TemporaryDirectory(prefix="snotes-p7-link-shell-") as temp:
            shell = SnotesShell(Path(temp) / "ui.ini")
            self.addCleanup(shell.close)
            toolbar_buttons = [
                group.layout().itemAt(index).widget()
                for group in toolbar.toolbar_groups
                for index in range(group.layout().count())
                if group.layout().itemAt(index).widget() is not None
            ]
            self.assertNotIn("URL", [button.text() for button in toolbar_buttons])
            self.assertNotIn(
                "给选中文字添加正文链接",
                [button.toolTip() for button in toolbar_buttons],
            )
            self.assertNotIn(
                "link.svg",
                [str(button.property("iconFile")) for button in toolbar_buttons],
            )
            self.assertEqual(shell.links_button.toolTip(), "打开关联抽屉")
            self.assertFalse(shell.links_button.icon().isNull())

        editor.set_note_content(
            '<p><a href="https://example.invalid/package7">fictional link</a></p>'
        )
        saved_html = editor.note_content_html()
        self.assertIn('href="https://example.invalid/package7"', saved_html)
        editor.set_note_content(saved_html)
        linked = QTextCursor(editor.document())
        linked.setPosition(0)
        linked.setPosition(1, QTextCursor.KeepAnchor)
        self.assertTrue(linked.charFormat().isAnchor())
        self.assertEqual(
            linked.charFormat().anchorHref(),
            "https://example.invalid/package7",
        )
        toolbar.close()
        editor.close()

    def test_related_drawer_uses_three_theme_surfaces_and_selection_tokens(
        self,
    ) -> None:
        drawer = LinkDrawer()
        drawer.resize(320, 260)
        drawer.set_results(
            [
                RelatedNote(
                    note_id=1,
                    title="Fictional related note",
                    path="Fictional / Path",
                    updated_at="2026-08-10T00:00:00",
                    reasons=("同目录",),
                )
            ]
        )
        drawer.show()
        self.addCleanup(drawer.close)

        for theme_name, tokens in THEMES.items():
            with self.subTest(theme=theme_name):
                apply_theme(self.app, theme_name)
                self.app.processEvents()
                self.assertEqual(
                    drawer.palette().color(QPalette.Window),
                    QColor(tokens.sidebar),
                )
                self.assertEqual(
                    drawer.results.viewport().palette().color(QPalette.Base),
                    QColor(tokens.sidebar),
                )
                self.assertEqual(
                    drawer.results.palette().color(QPalette.Text),
                    QColor(tokens.sidebar_text),
                )
                style = build_app_style(theme_name)
                self.assertIn(
                    f"background: {tokens.sidebar_hover}; color: {tokens.sidebar_text};",
                    style,
                )
                self.assertIn(
                    f"background: {tokens.sidebar_selected}; color: {tokens.sidebar_text};",
                    style,
                )

    def test_find_highlight_is_visible_temporary_and_not_document_content(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="snotes-p7-find-window-") as temp:
            _application, window = build_application(
                [], data_root=Path(temp) / "data"
            )
            self.addCleanup(window.close)
            note = window.note_service.create_note(NoteLocation())
            note = window.note_service.save_note(
                note.id,
                title="Fictional find highlight",
                content_html=(
                    "<p>边界是第一行的虚构查找词</p>"
                    "<p>第二行也有边界</p>"
                ),
                location=NoteLocation(),
                expected_revision=note.revision,
            )
            window.refresh_note_list(note.id)
            window.load_note(note.id)
            window.resize(1200, 800)
            window.set_theme("dark")
            window.show()
            self.app.processEvents()

            editor = window.body_edit
            toolbar = window.shell.editor_toolbar
            before_html = editor.toHtml()
            editor.document().setModified(False)
            changed = QSignalSpy(editor.textChanged)
            toolbar.find_toggle_button.click()
            toolbar.find_input.setFocus()
            toolbar.find_input.setText("边界")
            self.app.processEvents()

            self.assertTrue(toolbar.find_input.hasFocus())
            self.assertFalse(editor.textCursor().hasSelection())
            initial_highlights = self._find_highlights(editor, "边界")
            self.assertEqual(len(initial_highlights), 1)
            first_range = (
                initial_highlights[0].cursor.selectionStart(),
                initial_highlights[0].cursor.selectionEnd(),
            )
            self.assertEqual(first_range, (0, 2))
            match_range: tuple[int, int] | None = None
            for theme_name, tokens in THEMES.items():
                with self.subTest(theme=theme_name):
                    window.set_theme(theme_name)
                    self.app.processEvents()
                    highlights = self._find_highlights(editor, "边界")
                    self.assertEqual(len(highlights), 1)
                    highlight = highlights[0]
                    current_range = (
                        highlight.cursor.selectionStart(),
                        highlight.cursor.selectionEnd(),
                    )
                    if match_range is None:
                        match_range = current_range
                    self.assertEqual(current_range, match_range)
                    background = highlight.format.background().color()
                    self.assertEqual(background, QColor(tokens.accent))
                    self.assertEqual(background.alpha(), 255)
                    self.assertNotEqual(background, QColor(tokens.editor))
                    self.assertNotEqual(background, QColor(tokens.editor_paper))
                    image = editor.viewport().grab().toImage()
                    visible_pixels = sum(
                        image.pixelColor(x, y) == background
                        for y in range(image.height())
                        for x in range(image.width())
                    )
                    self.assertGreater(visible_pixels, 0)
                    self.assertTrue(toolbar.find_input.hasFocus())

            toolbar.find_next_button.click()
            self.app.processEvents()
            next_highlights = self._find_highlights(editor, "边界")
            self.assertEqual(len(next_highlights), 1)
            next_range = (
                next_highlights[0].cursor.selectionStart(),
                next_highlights[0].cursor.selectionEnd(),
            )
            self.assertNotEqual(next_range, first_range)
            self.assertGreater(next_range[0], first_range[0])
            self.assertTrue(toolbar.find_input.hasFocus())
            self.assertEqual(editor.toHtml(), before_html)
            self.assertFalse(editor.document().isModified())
            self.assertEqual(changed.count(), 0)

    def test_find_replace_ui_label_navigation_and_status_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snotes-p7-find-status-") as temp:
            _application, window = build_application(
                [], data_root=Path(temp) / "data"
            )
            self.addCleanup(window.close)
            first = window.note_service.create_note(NoteLocation())
            first = window.note_service.save_note(
                first.id,
                title="Fictional replace status",
                content_html="<p>alpha alpha alpha</p>",
                location=NoteLocation(),
                expected_revision=first.revision,
            )
            second = window.note_service.create_note(NoteLocation())
            second = window.note_service.save_note(
                second.id,
                title="Fictional second note",
                content_html="<p>second note only</p>",
                location=NoteLocation(),
                expected_revision=second.revision,
            )
            window.refresh_note_list(first.id)
            window.load_note(first.id)
            window.resize(1200, 800)
            window.show()
            self.app.processEvents()

            editor = window.body_edit
            toolbar = window.shell.editor_toolbar
            toolbar.find_toggle_button.click()
            toolbar.find_input.setText("alpha")
            toolbar.replace_input.setText("omega")
            self.app.processEvents()

            self.assertEqual(toolbar.replace_all_button.text(), "替换全部")
            self.assertEqual(len(self._find_highlights(editor, "alpha")), 1)
            toolbar.replace_button.click()
            self.app.processEvents()
            self.assertEqual(editor.toPlainText(), "omega alpha alpha")
            remaining = self._find_highlights(editor, "alpha")
            self.assertEqual(len(remaining), 1)
            self.assertEqual(
                (
                    remaining[0].cursor.selectionStart(),
                    remaining[0].cursor.selectionEnd(),
                ),
                (6, 11),
            )

            toolbar.replace_all_button.click()
            self.app.processEvents()
            self.assertEqual(editor.toPlainText(), "omega omega omega")
            self.assertEqual(toolbar.find_status.text(), "已替换 2 处")
            editor.undo()
            self.app.processEvents()
            self.assertEqual(toolbar.find_status.text(), "")

            toolbar.find_status.setText("已替换 2 处")
            editor.redo()
            self.app.processEvents()
            self.assertEqual(toolbar.find_status.text(), "")
            toolbar.find_status.setText("已替换 2 处")
            edit_cursor = editor.textCursor()
            edit_cursor.movePosition(QTextCursor.End)
            edit_cursor.insertText("!")
            self.app.processEvents()
            self.assertEqual(toolbar.find_status.text(), "")
            toolbar.find_status.setText("已替换 2 处")
            toolbar.replace_input.setText("delta")
            self.assertEqual(toolbar.find_status.text(), "")
            toolbar.find_status.setText("已替换 2 处")
            toolbar.find_input.setText("missing")
            self.app.processEvents()
            self.assertEqual(toolbar.find_status.text(), "未找到")
            self.assertEqual(self._find_highlights(editor), [])

            toolbar.find_input.setText("omega")
            self.app.processEvents()
            self.assertEqual(len(self._find_highlights(editor, "omega")), 1)
            toolbar.find_status.setText("已替换 2 处")
            window.switch_note(second.id)
            self.app.processEvents()
            self.assertEqual(toolbar.find_status.text(), "")
            self.assertEqual(self._find_highlights(editor), [])

            toolbar.find_status.setText("已替换 2 处")
            toolbar.toggle_find_panel()
            self.assertTrue(toolbar.find_panel.isHidden())
            self.assertEqual(toolbar.find_status.text(), "")
            self.assertEqual(self._find_highlights(editor), [])

    def test_replace_current_highlights_next_and_missing_clears_highlight(
        self,
    ) -> None:
        apply_theme(self.app, "light-slate")
        editor = NotesEditor()
        editor.setPlainText("alpha beta alpha")
        self.assertTrue(editor.find_next("alpha"))
        self.assertTrue(editor.replace_current("alpha", "omega"))
        self.assertEqual(editor.toPlainText(), "omega beta alpha")
        highlights = [
            selection
            for selection in editor.extraSelections()
            if selection.format.hasProperty(QTextFormat.BackgroundBrush)
        ]
        self.assertEqual(len(highlights), 1)
        self.assertEqual(highlights[0].cursor.selectedText(), "alpha")
        self.assertFalse(editor.textCursor().hasSelection())
        self.assertEqual(
            (
                highlights[0].cursor.selectionStart(),
                highlights[0].cursor.selectionEnd(),
            ),
            (11, 16),
        )
        self.assertFalse(editor.find_next("missing"))
        self.assertFalse(
            any(
                selection.format.hasProperty(QTextFormat.BackgroundBrush)
                for selection in editor.extraSelections()
            )
        )
        editor.close()


if __name__ == "__main__":
    unittest.main()
