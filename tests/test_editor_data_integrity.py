from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.editor import NotesEditor


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _run_window_persistence_probe() -> None:
    from src.app_paths import DATABASE_PATH
    from src.database import (
        create_note,
        get_note,
        initialize_database,
        update_note,
    )
    from src.main_window import MainWindow

    initialize_database()
    first = create_note()
    first = update_note(
        first["id"],
        title="虚构完整性甲",
        content_html=(
            '<p><span style="color:#ff0000; font-weight:700">'
            "虚构编号 1、</span></p><p>虚构正文二</p>"
        ),
    )
    second = create_note()
    second = update_note(
        second["id"],
        title="虚构完整性乙",
        content_html="<p>虚构第二篇原文 2、</p>",
    )
    second_original = second["content_html"]

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.load_note(first["id"])
    initial_updated_at = first["updated_at"]

    _assert(window.body_edit.clear_formatting(), "clear formatting was a no-op")
    _assert(window.has_unsaved_changes, "clear formatting did not mark the note dirty")
    _assert(window.save_timer.isActive(), "clear formatting did not arm autosave")
    _assert(
        window.body_edit.document().isModified(),
        "clear formatting did not mark the document model modified",
    )
    clean_editor_html = window.body_edit.note_content_html()
    _assert("#ff0000" not in clean_editor_html.lower(), "legacy color survived")
    _assert(
        "font-weight:700" not in clean_editor_html.replace(" ", "").lower(),
        "legacy bold survived",
    )
    _assert(window.save_current_note(refresh_tree=False), "explicit save failed")
    _assert(
        not window.body_edit.document().isModified(),
        "successful save did not reset the document modified state",
    )

    stored_first = get_note(first["id"])
    _assert(stored_first is not None, "saved note disappeared")
    _assert(
        stored_first["content_html"] == clean_editor_html,
        "editor canonical HTML and SQLite content_html differ",
    )
    _assert(
        datetime.fromisoformat(stored_first["updated_at"])
        > datetime.fromisoformat(initial_updated_at),
        "updated_at did not advance",
    )

    import sqlite3

    with sqlite3.connect(DATABASE_PATH) as connection:
        stored_columns = connection.execute(
            "SELECT content_html, body_rich_text FROM notes WHERE id = ?",
            (first["id"],),
        ).fetchone()
    _assert(stored_columns is not None, "SQLite row is missing")
    _assert(
        stored_columns[0] == stored_columns[1] == clean_editor_html,
        "SQLite body columns diverged",
    )

    window.open_linked_note(second["id"])
    window.open_linked_note(first["id"])
    _assert(
        window.body_edit.note_content_html() == clean_editor_html,
        "switch reload changed the saved body",
    )
    _assert(
        window.body_edit.toPlainText() == "虚构编号 1、\n虚构正文二",
        "switch reload changed the visible text",
    )

    window.open_linked_note(second["id"])
    window.body_edit.moveCursor(QTextCursor.End)
    window.body_edit.insertPlainText("\n虚构自动保存 3、")
    _assert(window.has_unsaved_changes, "ordinary edit did not become dirty")
    QTest.qWait(window.save_timer.interval() + 150)
    _assert(not window.has_unsaved_changes, "autosave did not finish")
    autosaved_second = get_note(second["id"])
    _assert(autosaved_second is not None, "autosaved note disappeared")
    _assert(
        autosaved_second["content_html"] == window.body_edit.note_content_html(),
        "autosave SQLite/editor mismatch",
    )
    _assert(
        "虚构自动保存 3、" in autosaved_second["content_html"],
        "autosave lost the last edit",
    )

    window.open_linked_note(first["id"])
    window.body_edit.moveCursor(QTextCursor.End)
    window.body_edit.insertPlainText("\n虚构切换前保存 4、")
    window.open_linked_note(second["id"])
    switched_first = get_note(first["id"])
    _assert(switched_first is not None, "switched note disappeared")
    _assert(
        "虚构切换前保存 4、" in switched_first["content_html"],
        "switch lost the pending edit",
    )
    _assert(
        get_note(second["id"])["content_html"] == autosaved_second["content_html"],
        "switch wrote the first note into the second note",
    )

    window.body_edit.moveCursor(QTextCursor.End)
    window.body_edit.insertPlainText("\n虚构关闭前保存 5、")
    window.close()
    closed_second = get_note(second["id"])
    _assert(closed_second is not None, "closed note disappeared")
    _assert(
        "虚构关闭前保存 5、" in closed_second["content_html"],
        "closeEvent lost the pending edit",
    )
    _assert(
        second_original != closed_second["content_html"],
        "second note was never updated",
    )

    reloaded = MainWindow()
    reloaded.load_note(first["id"])
    _assert(
        reloaded.body_edit.note_content_html()
        == get_note(first["id"])["content_html"],
        "new window did not reload the first note exactly",
    )
    reloaded.load_note(second["id"])
    _assert(
        reloaded.body_edit.note_content_html() == closed_second["content_html"],
        "new window did not reload the second note exactly",
    )
    reloaded.close()
    app.processEvents()


class EditorDataIntegrityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_clear_formatting_marks_model_modified_and_serializes_stably(self) -> None:
        editor = NotesEditor()
        editor.set_note_content(
            '<p><span style="color:#ff0000; font-weight:700">'
            "虚构正文 1、</span></p>"
        )
        text_changes: list[bool] = []
        editor.textChanged.connect(lambda: text_changes.append(True))

        self.assertTrue(editor.clear_formatting())
        self.assertEqual(editor.toPlainText(), "虚构正文 1、")
        self.assertTrue(editor.document().isModified())
        self.assertTrue(text_changes)
        saved_html = editor.note_content_html()
        self.assertNotIn("#ff0000", saved_html.lower())
        self.assertNotIn("font-weight:700", saved_html.replace(" ", "").lower())

        restored = NotesEditor()
        restored.set_note_content(saved_html)
        self.assertEqual(restored.note_content_html(), saved_html)
        self.assertEqual(restored.toPlainText(), editor.toPlainText())

    def test_rich_text_paste_keeps_text_order_and_safe_inline_emphasis(self) -> None:
        expected = "标题：虚构计划\n普通中文段落\n1、甲\n2、乙\n3、丙"
        mime = QMimeData()
        mime.setText(expected)
        mime.setHtml(
            '<h2 style="color:#ff0000">标题：虚构计划</h2>'
            "<p>普通中文段落</p>"
            '<p><b style="font-weight:900">1、甲</b></p>'
            '<p><span style="color:blue">2、乙</span></p>'
            "<p>3、丙</p>"
        )
        editor = NotesEditor()
        editor.insertFromMimeData(mime)

        self.assertEqual(editor.toPlainText(), expected)
        html = editor.toHtml().replace(" ", "").lower()
        self.assertIn("font-weight:700", html)
        self.assertNotIn("#ff0000", html)
        self.assertNotIn("color:blue", html)

    def test_malformed_external_blocks_fall_back_to_exact_plain_text(self) -> None:
        expected = "第一段 1、\n第二段 2、\n\n第三段 3、"
        mime = QMimeData()
        mime.setText(expected)
        mime.setHtml(
            '<div style="line-height:2">'
            "<p>第一段 1、</p>"
            '<p><b style="color:red">第二段 2、</b></p>'
            "<p><br></p>"
            "<div>第三段 3、</div>"
            "</div>"
        )
        editor = NotesEditor()
        editor.insertFromMimeData(mime)

        self.assertEqual(editor.toPlainText(), expected)
        for fragment in ("第一段 1、", "第二段 2、", "第三段 3、"):
            self.assertEqual(editor.toPlainText().count(fragment), 1)

    def test_window_save_switch_autosave_close_and_restart_use_temp_database(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="snotes-editor-integrity-"
        ) as temporary_home:
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": temporary_home,
                    "QT_QPA_PLATFORM": "offscreen",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPYCACHEPREFIX": str(Path(temporary_home) / "pycache"),
                    "SNOTES_EDITOR_INTEGRITY_PROBE": "1",
                }
            )
            result = subprocess.run(
                [sys.executable, "-m", "tests.test_editor_data_integrity"],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        self.assertEqual(
            result.returncode,
            0,
            f"probe stdout:\n{result.stdout}\nprobe stderr:\n{result.stderr}",
        )


if __name__ == "__main__":
    if os.environ.get("SNOTES_EDITOR_INTEGRITY_PROBE") == "1":
        _run_window_persistence_probe()
    else:
        unittest.main()
