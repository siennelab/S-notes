from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import unittest
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

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
        get_note_ai_targets,
        get_note_tags,
        initialize_database,
        rename_note,
        save_editor_state,
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

    window.body_edit.moveCursor(QTextCursor.End)
    window.body_edit.insertPlainText("\n虚构重命名前保存 3、")
    renamed_expected_html = window.body_edit.note_content_html()
    renamed_expected_text = window.body_edit.toPlainText()
    rename_versions: list[tuple[str, str]] = []

    def tracked_rename(note_id: int, title: str) -> dict:
        before = get_note(note_id)["updated_at"]
        result = rename_note(note_id, title)
        rename_versions.append((before, result["updated_at"]))
        return result

    with (
        patch(
            "src.main_window.QInputDialog.getText",
            return_value=("虚构重命名后标题", True),
        ),
        patch("src.main_window.rename_note", side_effect=tracked_rename),
    ):
        window.prompt_rename_note(first["id"])
    renamed_first = get_note(first["id"])
    _assert(renamed_first is not None, "renamed note disappeared")
    _assert(renamed_first["title"] == "虚构重命名后标题", "rename did not persist")
    _assert(
        len(rename_versions) == 1
        and datetime.fromisoformat(rename_versions[0][1])
        > datetime.fromisoformat(rename_versions[0][0]),
        "rename did not advance the just-saved timestamp",
    )
    _assert(
        renamed_first["content_html"] == renamed_expected_html,
        "rename lost the exact pending body in SQLite",
    )
    _assert(
        window.body_edit.note_content_html() == renamed_expected_html
        and window.body_edit.toPlainText() == renamed_expected_text,
        "rename reload changed the pending editor body",
    )
    _assert(
        not window.has_unsaved_changes
        and not window.save_timer.isActive()
        and not window.body_edit.document().isModified(),
        "rename left an incorrect dirty state",
    )
    window.open_linked_note(second["id"])
    window.open_linked_note(first["id"])
    _assert(
        window.body_edit.note_content_html() == renamed_expected_html,
        "rename body did not survive switch and reload",
    )

    window.body_edit.moveCursor(QTextCursor.End)
    window.body_edit.insertPlainText("\n虚构保存失败保留 4、")
    failed_expected_html = window.body_edit.note_content_html()
    failed_expected_text = window.body_edit.toPlainText()
    with (
        patch(
            "src.main_window.QInputDialog.getText",
            return_value=("不应写入的标题", True),
        ),
        patch(
            "src.main_window.save_editor_state",
            side_effect=RuntimeError("虚构保存失败"),
        ),
        patch("src.main_window.rename_note", wraps=rename_note) as rename_spy,
        patch.object(window, "load_note", wraps=window.load_note) as load_spy,
    ):
        window.prompt_rename_note(first["id"])
    failed_stored = get_note(first["id"])
    _assert(failed_stored is not None, "note disappeared after failed rename save")
    _assert(
        failed_stored["title"] == "虚构重命名后标题"
        and failed_stored["content_html"] == renamed_expected_html,
        "failed pre-rename save changed SQLite",
    )
    _assert(not rename_spy.called and not load_spy.called, "rename continued after save failure")
    _assert(
        window.body_edit.note_content_html() == failed_expected_html
        and window.body_edit.toPlainText() == failed_expected_text,
        "failed pre-rename save changed the editor body",
    )
    _assert(window.has_unsaved_changes, "failed pre-rename save cleared dirty state")
    _assert(
        window.status_label.text().startswith("保存失败："),
        "failed pre-rename save did not show the existing failure status",
    )
    _assert(
        window.save_current_note(refresh_tree=False),
        "retry after failed pre-rename save failed",
    )
    _assert(
        get_note(first["id"])["content_html"] == failed_expected_html,
        "retry did not save the exact pending body",
    )

    window.open_linked_note(second["id"])
    window.body_edit.moveCursor(QTextCursor.End)
    window.body_edit.insertPlainText("\n虚构自动保存 5、")
    autosave_expected_html = window.body_edit.note_content_html()
    _assert(window.has_unsaved_changes, "ordinary edit did not become dirty")
    QTest.qWait(window.save_timer.interval() + 150)
    _assert(not window.has_unsaved_changes, "autosave did not finish")
    autosaved_second = get_note(second["id"])
    _assert(autosaved_second is not None, "autosaved note disappeared")
    _assert(
        autosaved_second["content_html"]
        == window.body_edit.note_content_html()
        == autosave_expected_html,
        "autosave SQLite/editor mismatch",
    )

    window.open_linked_note(first["id"])
    window.body_edit.moveCursor(QTextCursor.End)
    window.body_edit.insertPlainText("\n虚构切换前保存 6、")
    switched_expected_html = window.body_edit.note_content_html()
    window.open_linked_note(second["id"])
    switched_first = get_note(first["id"])
    _assert(switched_first is not None, "switched note disappeared")
    _assert(
        switched_first["content_html"] == switched_expected_html,
        "switch changed the exact pending body",
    )
    _assert(
        get_note(second["id"])["content_html"] == autosaved_second["content_html"],
        "switch wrote the first note into the second note",
    )

    window.body_edit.moveCursor(QTextCursor.End)
    window.body_edit.insertPlainText("\n虚构退出回收站前保存 7、")
    trash_exit_expected_html = window.body_edit.note_content_html()
    window.show_trash()
    window.exit_trash()
    _assert(
        get_note(second["id"])["content_html"] == trash_exit_expected_html,
        "exit_trash lost the exact pending body",
    )
    _assert(
        window.current_note["id"] == second["id"]
        and window.body_edit.note_content_html() == trash_exit_expected_html
        and not window.has_unsaved_changes,
        "exit_trash reload changed the editor state",
    )

    window.body_edit.moveCursor(QTextCursor.End)
    window.body_edit.insertPlainText("\n虚构关闭前保存 8、")
    close_expected_html = window.body_edit.note_content_html()
    window.close()
    closed_second = get_note(second["id"])
    _assert(closed_second is not None, "closed note disappeared")
    _assert(
        closed_second["content_html"] == close_expected_html,
        "closeEvent changed the exact pending body",
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

    rollback_time = datetime.fromisoformat("2026-01-01T00:00:00+08:00")
    future_timestamp = "2030-01-01T00:00:00+08:00"
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            "UPDATE notes SET updated_at = ? WHERE id = ?",
            (future_timestamp, first["id"]),
        )
    rollback_body = "<p>虚构回拨正文</p>"
    with patch("src.database.now_shanghai", return_value=rollback_time):
        rollback_saved = save_editor_state(
            first["id"],
            title="虚构回拨标题",
            content_html=rollback_body,
            main_category="临时",
            sub_category="无",
            sub_sub_category="无",
            tags=["回拨标签"],
            ai_visibility="AI完整可见",
            ai_targets=["Gale"],
        )
    _assert(
        datetime.fromisoformat(rollback_saved["updated_at"])
        > datetime.fromisoformat(future_timestamp),
        "clock rollback made updated_at regress",
    )
    rollback_stored = get_note(first["id"])
    with sqlite3.connect(DATABASE_PATH) as connection:
        rollback_columns = connection.execute(
            "SELECT content_html, body_rich_text FROM notes WHERE id = ?",
            (first["id"],),
        ).fetchone()
    _assert(
        rollback_stored["title"] == "虚构回拨标题"
        and rollback_stored["content_html"] == rollback_body
        and rollback_columns == (rollback_body, rollback_body)
        and get_note_tags(first["id"]) == ["回拨标签"]
        and rollback_stored["ai_visibility"] == "full"
        and get_note_ai_targets(first["id"]) == ["Gale"],
        "clock rollback save was not one complete editor state",
    )

    rapid_timestamps: list[datetime] = []
    with patch("src.database.now_shanghai", return_value=rollback_time):
        for index in range(3):
            rapid_saved = save_editor_state(
                first["id"],
                title=f"虚构快速保存 {index}",
                content_html=f"<p>虚构快速正文 {index}</p>",
                main_category="临时",
                sub_category="无",
                sub_sub_category="无",
                tags=[f"快速标签{index}"],
                ai_visibility="AI脱敏后可见",
                ai_targets=["Codex"],
            )
            rapid_timestamps.append(datetime.fromisoformat(rapid_saved["updated_at"]))
    _assert(
        all(
            earlier < later
            for earlier, later in zip(rapid_timestamps, rapid_timestamps[1:])
        ),
        "rapid saves did not produce strictly increasing timestamps",
    )

    concurrent_note = create_note()
    concurrent_states = [
        {
            "title": "虚构并发甲",
            "content": "<p>虚构并发正文甲</p>",
            "tag": "并发甲",
            "visibility_label": "AI不可见",
            "visibility_code": "hidden",
            "target": "Gale",
        },
        {
            "title": "虚构并发乙",
            "content": "<p>虚构并发正文乙</p>",
            "tag": "并发乙",
            "visibility_label": "AI完整可见",
            "visibility_code": "full",
            "target": "Codex",
        },
        {
            "title": "虚构并发丙",
            "content": "<p>虚构并发正文丙</p>",
            "tag": "并发丙",
            "visibility_label": "AI脱敏后可见",
            "visibility_code": "redacted",
            "target": "通用",
        },
        {
            "title": "虚构并发丁",
            "content": "<p>虚构并发正文丁</p>",
            "tag": "并发丁",
            "visibility_label": "AI完整可见",
            "visibility_code": "full",
            "target": "ke-den",
        },
    ]
    start_barrier = threading.Barrier(len(concurrent_states))

    def save_concurrent_state(state: dict) -> tuple[dict, dict]:
        start_barrier.wait()
        saved = save_editor_state(
            concurrent_note["id"],
            title=state["title"],
            content_html=state["content"],
            main_category="临时",
            sub_category="无",
            sub_sub_category="无",
            tags=[state["tag"]],
            ai_visibility=state["visibility_label"],
            ai_targets=[state["target"]],
        )
        return state, saved

    with (
        patch("src.database.now_shanghai", return_value=rollback_time),
        ThreadPoolExecutor(max_workers=len(concurrent_states)) as executor,
    ):
        concurrent_results = list(executor.map(save_concurrent_state, concurrent_states))
    committed_times = [
        datetime.fromisoformat(saved["updated_at"])
        for _, saved in concurrent_results
    ]
    _assert(
        len(set(committed_times)) == len(concurrent_states),
        "concurrent saves reused an updated_at value",
    )
    _assert(
        all(
            earlier < later
            for earlier, later in zip(
                sorted(committed_times), sorted(committed_times)[1:]
            )
        ),
        "concurrent committed timestamps were not strictly ordered",
    )
    final_concurrent = get_note(concurrent_note["id"])
    final_tags = get_note_tags(concurrent_note["id"])
    final_targets = get_note_ai_targets(concurrent_note["id"])
    matching_states = [
        state
        for state in concurrent_states
        if final_concurrent["title"] == state["title"]
        and final_concurrent["content_html"] == state["content"]
        and final_tags == [state["tag"]]
        and final_concurrent["ai_visibility"] == state["visibility_code"]
        and final_targets == [state["target"]]
    ]
    _assert(
        len(matching_states) == 1,
        "concurrent final row mixed body and metadata from different saves",
    )
    _assert(
        datetime.fromisoformat(final_concurrent["updated_at"])
        == max(committed_times),
        "concurrent final row did not retain the latest committed timestamp",
    )


class EditorDataIntegrityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def paste_editor(markup: str, plain_text: str) -> NotesEditor:
        mime = QMimeData()
        mime.setHtml(markup)
        mime.setText(plain_text)
        editor = NotesEditor()
        editor.insertFromMimeData(mime)
        return editor

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

    def test_paste_normalizes_entities_nbsp_breaks_and_unicode_equivalence(self) -> None:
        equivalent_cases = (
            ("<p><b>甲 &amp; 乙</b></p>", "甲 & 乙", "甲 & 乙"),
            ("<p><b>甲&nbsp;乙</b></p>", "甲 乙", "甲 乙"),
            ("<p><b>甲<br>乙</b></p>", "甲\n乙", "甲\n乙"),
            ("<p><b>甲</b></p>", "甲\n", "甲"),
        )
        for markup, plain_text, expected in equivalent_cases:
            with self.subTest(markup=markup):
                editor = self.paste_editor(markup, plain_text)
                self.assertEqual(editor.toPlainText(), expected)
                self.assertIn(
                    "font-weight:700",
                    editor.toHtml().replace(" ", "").lower(),
                )

        decomposed = "Cafe\u0301"
        editor = self.paste_editor(f"<p><b>{decomposed}</b></p>", "Café")
        self.assertEqual(editor.toPlainText(), decomposed)
        self.assertEqual(unicodedata.normalize("NFC", editor.toPlainText()), "Café")
        self.assertIn(
            "font-weight:700",
            editor.toHtml().replace(" ", "").lower(),
        )

    def test_paste_keeps_all_visible_text_from_incomplete_mime_representations(self) -> None:
        html_has_extra = self.paste_editor(
            "<p>虚构第一段</p><p><b>虚构第二段 2、</b></p>",
            "虚构第一段",
        )
        self.assertEqual(
            html_has_extra.toPlainText(),
            "虚构第一段\n虚构第二段 2、",
        )
        self.assertIn(
            "font-weight:700",
            html_has_extra.toHtml().replace(" ", "").lower(),
        )

        plain_has_extra = self.paste_editor(
            "<p><b>虚构第一段</b></p>",
            "虚构第一段\n虚构第二段 2、",
        )
        self.assertEqual(
            plain_has_extra.toPlainText(),
            "虚构第一段\n虚构第二段 2、",
        )

        conflicting = self.paste_editor(
            "<p>虚构共同段</p><p>虚构 HTML 独有段</p>",
            "虚构共同段\n虚构 plain 独有段",
        )
        self.assertEqual(
            conflicting.toPlainText(),
            "虚构共同段\n虚构 HTML 独有段\n虚构 plain 独有段",
        )

        paragraph_boundary = self.paste_editor(
            "<p><b>虚构甲</b></p><p>虚构乙</p>",
            "虚构甲虚构乙",
        )
        self.assertEqual(paragraph_boundary.toPlainText(), "虚构甲虚构乙")

    def test_paste_preserves_lists_nested_divs_empty_lines_and_empty_fallbacks(self) -> None:
        list_editor = self.paste_editor(
            "<ol><li><b>虚构甲</b></li><li>虚构乙</li></ol>",
            "1. 虚构甲\n2. 虚构乙",
        )
        self.assertEqual(list_editor.toPlainText(), "虚构甲\n虚构乙")
        self.assertIsNotNone(list_editor.document().begin().textList())
        self.assertIn(
            "font-weight:700",
            list_editor.toHtml().replace(" ", "").lower(),
        )

        numbered_plain = self.paste_editor(
            "<p>虚构甲</p>",
            "1、虚构甲",
        )
        self.assertEqual(numbered_plain.toPlainText(), "1、虚构甲")

        nested = self.paste_editor(
            "<div><div>虚构第一段</div><div><br></div>"
            "<div>虚构第三段</div></div>",
            "虚构第一段\n\n虚构第三段",
        )
        self.assertEqual(nested.toPlainText(), "虚构第一段\n\n虚构第三段")

        empty_html = self.paste_editor("", "虚构纯文本保留\n\n虚构末段")
        self.assertEqual(empty_html.toPlainText(), "虚构纯文本保留\n\n虚构末段")

        empty_plain = self.paste_editor(
            "<h2>虚构标题</h2><p><b>虚构 HTML 正文</b></p>",
            "",
        )
        self.assertEqual(empty_plain.toPlainText(), "虚构标题\n虚构 HTML 正文")
        self.assertIn(
            "font-weight:700",
            empty_plain.toHtml().replace(" ", "").lower(),
        )

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
