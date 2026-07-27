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


def _run_real_gui_format_and_markdown_probe() -> None:
    import sqlite3

    from src.app_paths import DATABASE_PATH
    from src.database import (
        create_note,
        get_note,
        initialize_database,
        update_note,
    )
    from src.main_window import MainWindow

    initialize_database()
    format_note = create_note()
    second_note = create_note()
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.load_note(format_note["id"])

    pasted_plain_text = (
        "虚构普通中文段落\n\n\n"
        "### 1. 外部粘贴与清除格式\n"
        "###1、第一项\n"
        "###2、第二项"
    )
    mime = QMimeData()
    mime.setText(pasted_plain_text)
    mime.setHtml(
        '<div style="line-height:240%; font-family:Courier New">'
        '<p style="margin-top:24px; margin-bottom:18px">'
        '<b style="color:#ff0000">虚构普通中文段落</b></p>'
        "<p><br></p>"
        "<p>### 1. 外部粘贴与清除格式</p>"
        "<p>###1、第一项</p>"
        "<p>###2、第二项</p>"
        "</div>"
    )
    window.body_edit.insertFromMimeData(mime)
    _assert(
        window.body_edit.toPlainText() == pasted_plain_text,
        "external rich paste changed the exact visible text",
    )
    QTest.qWait(window.save_timer.interval() + 150)
    formatted_note = get_note(format_note["id"])
    _assert(formatted_note is not None, "formatted note was not autosaved")
    formatted_html = formatted_note["content_html"]
    _assert(
        formatted_html == window.body_edit.note_content_html(),
        "formatted editor and SQLite HTML differed before clear formatting",
    )
    _assert(
        "font-weight:700" in formatted_html.replace(" ", "").lower(),
        "rich paste did not establish a persisted format for the regression",
    )

    cursor = window.body_edit.textCursor()
    cursor.select(QTextCursor.Document)
    window.body_edit.setTextCursor(cursor)
    window.editor_toolbar.clear_format_button.click()
    _assert(
        window.body_edit.toPlainText() == pasted_plain_text,
        "clear-format toolbar action changed the exact plain text",
    )
    _assert(
        window.has_unsaved_changes
        and window.body_edit.document().isModified()
        and window.save_timer.isActive(),
        "clear-format toolbar action did not arm the real autosave path",
    )
    cleared_editor_html = window.body_edit.note_content_html()
    _assert(
        cleared_editor_html != formatted_html,
        "clear-format HTML-only change collapsed to the old save representation",
    )
    QTest.qWait(window.save_timer.interval() + 150)

    cleared_note = get_note(format_note["id"])
    _assert(cleared_note is not None, "cleared note disappeared")
    cleared_database_html = cleared_note["content_html"]
    _assert(
        cleared_database_html == cleared_editor_html,
        "real autosave did not persist the exact cleared editor HTML",
    )
    _assert(
        window.current_note is not None
        and window.current_note["content_html"] == cleared_database_html,
        "successful clear-format save did not refresh the current-note cache",
    )
    _assert(
        window.body_edit.toPlainText() == pasted_plain_text,
        "clear-format autosave changed the visible text",
    )
    compact_cleared_html = cleared_database_html.replace(" ", "").lower()
    for forbidden in (
        "font-weight:700",
        "couriernew",
        "#ff0000",
        "line-height:240%",
        "line-height:180%",
        "margin-top:24px",
        "margin-bottom:18px",
    ):
        _assert(
            forbidden not in compact_cleared_html,
            f"cleared SQLite HTML retained external formatting: {forbidden}",
        )
    with sqlite3.connect(DATABASE_PATH) as connection:
        stored_columns = connection.execute(
            "SELECT content_html, body_rich_text FROM notes WHERE id = ?",
            (format_note["id"],),
        ).fetchone()
    _assert(
        stored_columns is not None
        and stored_columns[0] == stored_columns[1] == cleared_database_html,
        "cleared SQLite body columns diverged",
    )

    window.open_linked_note(second_note["id"])
    window.open_linked_note(format_note["id"])
    _assert(
        window.body_edit.note_content_html() == cleared_database_html
        and window.body_edit.toPlainText() == pasted_plain_text,
        "switch reload revived the pre-clear HTML",
    )

    with patch.object(window, "confirm_continue", return_value=True):
        window.confirm_delete_note(format_note["id"])
    deleted_note = get_note(format_note["id"], include_deleted=True)
    _assert(
        deleted_note is not None
        and deleted_note["content_html"] == cleared_database_html,
        "moving the cleared note to trash changed its HTML",
    )
    window.restore_deleted_note(format_note["id"])
    restored_note = get_note(format_note["id"])
    _assert(
        restored_note is not None
        and restored_note["content_html"] == cleared_database_html
        and window.body_edit.note_content_html() == cleared_database_html
        and window.body_edit.toPlainText() == pasted_plain_text,
        "trash restore revived the pre-clear HTML",
    )

    restarted = MainWindow()
    restarted.load_note(format_note["id"])
    _assert(
        restarted.body_edit.note_content_html() == cleared_database_html
        and restarted.body_edit.toPlainText() == pasted_plain_text,
        "new-window reload revived the pre-clear HTML",
    )
    restarted.close()

    markdown_source = (
        "### 1. 外部粘贴与清除格式\n"
        "###1、第一项\n"
        "###2、第二项\n"
        "#标签\n"
        "C#\n"
        "A#B\n"
        "正文里的 #号\n"
        "https://example.test/page#section"
    )
    markdown_expected = (
        "1. 外部粘贴与清除格式\n"
        "1、第一项\n"
        "2、第二项\n"
        "#标签\n"
        "C#\n"
        "A#B\n"
        "正文里的 #号\n"
        "https://example.test/page#section"
    )
    markdown_note = create_note()
    update_note(
        markdown_note["id"],
        title="虚构 Markdown 工具栏验收",
        content_html=markdown_source,
    )
    window.load_note(markdown_note["id"])
    window.editor_toolbar.clear_markdown_button.click()
    _assert(
        window.body_edit.toPlainText() == markdown_expected,
        "real Markdown toolbar action changed numbering or protected hashes",
    )
    _assert(
        window.has_unsaved_changes and window.save_timer.isActive(),
        "Markdown toolbar action did not arm autosave",
    )
    markdown_editor_html = window.body_edit.note_content_html()
    QTest.qWait(window.save_timer.interval() + 150)
    markdown_saved = get_note(markdown_note["id"])
    _assert(
        markdown_saved is not None
        and markdown_saved["content_html"] == markdown_editor_html,
        "Markdown toolbar result was not persisted exactly",
    )
    markdown_reload = MainWindow()
    markdown_reload.load_note(markdown_note["id"])
    _assert(
        markdown_reload.body_edit.toPlainText() == markdown_expected
        and markdown_reload.body_edit.note_content_html() == markdown_editor_html,
        "Markdown toolbar result changed after new-window reload",
    )
    markdown_reload.close()

    window.clear_editor()
    window.close()
    app.processEvents()


def _run_window_persistence_probe() -> None:
    from src.app_paths import DATABASE_PATH
    from src.database import (
        create_directory,
        create_note,
        create_note_in_directory,
        get_note,
        get_note_ai_targets,
        get_note_tags,
        initialize_database,
        rename_directory,
        rename_note,
        restore_note,
        restore_trash_directory,
        save_editor_state,
        set_note_pinned,
        soft_delete_directory_tree,
        soft_delete_note,
        update_note,
    )
    from src.batch_note_operations import restore_trash_items
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

    def set_note_timestamp(note_id: int, timestamp: str) -> None:
        with sqlite3.connect(DATABASE_PATH) as connection:
            connection.execute(
                "UPDATE notes SET updated_at = ? WHERE id = ?",
                (timestamp, note_id),
            )

    def note_timestamp(note_id: int) -> datetime:
        note = get_note(note_id, include_deleted=True)
        _assert(note is not None, f"missing note {note_id}")
        return datetime.fromisoformat(note["updated_at"])

    direct_writer_time = datetime.fromisoformat("2026-01-01T00:00:00+08:00")
    direct_writer_note = create_note()
    direct_writer_initial = "2030-01-01T00:00:00.000001+08:00"
    set_note_timestamp(direct_writer_note["id"], direct_writer_initial)
    with patch("src.database.now_shanghai", return_value=direct_writer_time):
        pinned = set_note_pinned(direct_writer_note["id"], True)
        pinned_time = datetime.fromisoformat(pinned["updated_at"])
        _assert(
            pinned_time > datetime.fromisoformat(direct_writer_initial),
            "pin clock rollback regressed updated_at",
        )
        saved_after_pin = save_editor_state(
            direct_writer_note["id"],
            title="虚构置顶后保存",
            content_html="<p>虚构置顶后正文</p>",
            main_category="临时",
            sub_category="无",
            sub_sub_category="无",
            tags=["置顶顺序"],
            ai_visibility="AI不可见",
            ai_targets=[],
        )
        saved_after_pin_time = datetime.fromisoformat(
            saved_after_pin["updated_at"]
        )
        _assert(
            saved_after_pin_time > pinned_time,
            "pin then editor save did not advance updated_at",
        )
        unpinned = set_note_pinned(direct_writer_note["id"], False)
        _assert(
            datetime.fromisoformat(unpinned["updated_at"]) > saved_after_pin_time,
            "editor save then pin did not advance updated_at",
        )

    same_second_note = create_note()
    same_second_before = "2026-01-01T00:00:00.500000+08:00"
    set_note_timestamp(same_second_note["id"], same_second_before)
    with patch("src.database.now_shanghai", return_value=direct_writer_time):
        same_second_pin = set_note_pinned(same_second_note["id"], True)
    _assert(
        datetime.fromisoformat(same_second_pin["updated_at"])
        > datetime.fromisoformat(same_second_before),
        "pin replaced a microsecond timestamp with an earlier whole second",
    )

    lifecycle_note = create_note()
    lifecycle_initial = "2031-01-01T00:00:00.000001+08:00"
    set_note_timestamp(lifecycle_note["id"], lifecycle_initial)
    with patch("src.database.now_shanghai", return_value=direct_writer_time):
        renamed_lifecycle = rename_note(
            lifecycle_note["id"], "虚构直接写入链路"
        )
        rename_time = datetime.fromisoformat(renamed_lifecycle["updated_at"])
        _assert(
            rename_time > datetime.fromisoformat(lifecycle_initial),
            "rename regressed updated_at",
        )
        unchanged = rename_note(
            lifecycle_note["id"], "虚构直接写入链路"
        )
        _assert(
            unchanged["updated_at"] == renamed_lifecycle["updated_at"],
            "same-title rename changed updated_at",
        )
        soft_delete_note(lifecycle_note["id"])
        delete_time = note_timestamp(lifecycle_note["id"])
        _assert(delete_time > rename_time, "soft delete regressed updated_at")
        restored_lifecycle = restore_note(lifecycle_note["id"])
        _assert(
            datetime.fromisoformat(restored_lifecycle["updated_at"]) > delete_time,
            "restore note regressed updated_at",
        )

    directory = create_directory("虚构单调目录")
    directory_note = create_note_in_directory("虚构单调目录")
    directory_initial = "2032-01-01T00:00:00.000001+08:00"
    set_note_timestamp(directory_note["id"], directory_initial)
    with patch("src.database.now_shanghai", return_value=direct_writer_time):
        rename_directory(directory["id"], "虚构单调目录改名")
        directory_rename_time = note_timestamp(directory_note["id"])
        _assert(
            directory_rename_time > datetime.fromisoformat(directory_initial),
            "directory rename regressed a note timestamp",
        )
        soft_delete_directory_tree(directory["id"])
        directory_delete_time = note_timestamp(directory_note["id"])
        _assert(
            directory_delete_time > directory_rename_time,
            "directory delete regressed a note timestamp",
        )
        restore_trash_directory(directory["id"])
        _assert(
            note_timestamp(directory_note["id"]) > directory_delete_time,
            "directory restore regressed a note timestamp",
        )

    batch_note = create_note()
    batch_initial = "2033-01-01T00:00:00.000001+08:00"
    set_note_timestamp(batch_note["id"], batch_initial)
    with patch("src.database.now_shanghai", return_value=direct_writer_time):
        soft_delete_note(batch_note["id"])
        batch_delete_time = note_timestamp(batch_note["id"])
        restore_trash_items([batch_note["id"]], [])
        _assert(
            note_timestamp(batch_note["id"]) > batch_delete_time,
            "batch trash restore regressed a note timestamp",
        )

    same_title_note = create_note()
    same_title_note = rename_note(same_title_note["id"], "虚构同名标题")
    same_title_before = get_note(same_title_note["id"])
    same_title_window = MainWindow()
    same_title_window.load_note(same_title_note["id"])
    same_title_editor_before = same_title_window.body_edit.note_content_html()
    with (
        patch(
            "src.main_window.QInputDialog.getText",
            return_value=("虚构同名标题", True),
        ),
        patch("src.main_window.rename_note", wraps=rename_note) as same_rename_spy,
        patch.object(
            same_title_window,
            "load_note",
            wraps=same_title_window.load_note,
        ) as same_load_spy,
    ):
        same_title_window.prompt_rename_note(same_title_note["id"])
    same_title_after = get_note(same_title_note["id"])
    _assert(
        same_title_after == same_title_before,
        "same-title real rename path changed database state",
    )
    _assert(
        not same_rename_spy.called and not same_load_spy.called,
        "same-title real rename path updated or reloaded the note",
    )
    _assert(
        same_title_window.body_edit.note_content_html()
        == same_title_editor_before,
        "same-title real rename path changed editor content",
    )
    same_title_window.close()

    def wait_until(predicate, timeout_ms: int = 1500) -> bool:
        waited = 0
        while waited < timeout_ms:
            if predicate():
                return True
            QTest.qWait(10)
            waited += 10
        return predicate()

    retry_note = create_note()
    retry_note = update_note(
        retry_note["id"],
        title="虚构重试笔记",
        content_html="<p>虚构重试旧正文</p>",
    )
    retry_window = MainWindow()
    retry_window.load_note(retry_note["id"])
    retry_window.AUTOSAVE_RETRY_DELAYS_MS = (20, 30, 40)
    retry_window.body_edit.setPlainText("虚构重试后完整正文")
    retry_expected_html = retry_window.body_edit.note_content_html()
    retry_attempts = 0
    real_save_editor_state = save_editor_state

    def fail_twice_then_save(*args, **kwargs):
        nonlocal retry_attempts
        retry_attempts += 1
        if retry_attempts <= 2:
            raise RuntimeError("虚构临时保存失败")
        return real_save_editor_state(*args, **kwargs)

    with patch(
        "src.main_window.save_editor_state",
        side_effect=fail_twice_then_save,
    ):
        _assert(
            not retry_window.force_save_current_note(refresh_tree=False),
            "initial retry test save unexpectedly succeeded",
        )
        _assert(
            retry_window.save_timer.isActive()
            and retry_window.autosave_retry_count == 1,
            "save failure did not schedule the first retry",
        )
        _assert(
            wait_until(lambda: not retry_window.has_unsaved_changes),
            "autosave retry did not eventually succeed",
        )
    _assert(retry_attempts == 3, "autosave retry success used wrong attempt count")
    _assert(
        get_note(retry_note["id"])["content_html"] == retry_expected_html,
        "successful autosave retry did not persist the exact body",
    )
    _assert(
        not retry_window.has_unsaved_changes
        and retry_window.autosave_retry_count == 0
        and not retry_window.save_timer.isActive(),
        "successful autosave retry did not reset dirty/timer state",
    )

    retry_window.load_note(retry_note["id"])
    retry_window.body_edit.setPlainText("虚构持续失败完整正文")
    retry_limit_expected_html = retry_window.body_edit.note_content_html()
    retry_limit_database_html = get_note(retry_note["id"])["content_html"]
    retry_limit_attempts = 0

    def always_fail(*_args, **_kwargs):
        nonlocal retry_limit_attempts
        retry_limit_attempts += 1
        raise RuntimeError("虚构持续保存失败")

    with patch("src.main_window.save_editor_state", side_effect=always_fail):
        _assert(
            not retry_window.force_save_current_note(refresh_tree=False),
            "retry limit initial save unexpectedly succeeded",
        )
        _assert(
            wait_until(
                lambda: retry_limit_attempts == 4
                and not retry_window.save_timer.isActive()
            ),
            "autosave retries did not stop at the approved limit",
        )
        QTest.qWait(100)
        _assert(
            retry_limit_attempts == 4,
            "autosave retries continued after the approved limit",
        )
    _assert(
        retry_window.has_unsaved_changes
        and retry_window.body_edit.note_content_html()
        == retry_limit_expected_html,
        "retry exhaustion lost or cleared the editor body",
    )
    _assert(
        get_note(retry_note["id"])["content_html"] == retry_limit_database_html,
        "retry exhaustion changed the database body",
    )

    max_note = create_note()
    max_note = update_note(
        max_note["id"],
        title="虚构最大时间笔记",
        content_html="<p>虚构最大时间旧正文</p>",
    )
    maximum_timestamp = "9999-12-31T23:59:59.999999+08:00"
    set_note_timestamp(max_note["id"], maximum_timestamp)
    max_before = get_note(max_note["id"])
    retry_window.load_note(max_note["id"])
    retry_window.AUTOSAVE_RETRY_DELAYS_MS = (10000, 20000, 30000)
    retry_window.body_edit.setPlainText("虚构最大时间待保存正文")
    max_editor_html = retry_window.body_edit.note_content_html()
    _assert(
        not retry_window.force_save_current_note(refresh_tree=False),
        "maximum datetime save unexpectedly succeeded",
    )
    _assert(
        "笔记更新时间已达到可表示上限" in retry_window.status_label.text(),
        "maximum datetime failure was not a controlled save error",
    )
    _assert(
        get_note(max_note["id"]) == max_before,
        "maximum datetime failure partially changed the database",
    )
    _assert(
        retry_window.body_edit.note_content_html() == max_editor_html
        and retry_window.has_unsaved_changes
        and retry_window.save_timer.isActive(),
        "maximum datetime failure did not preserve dirty editor state and retry",
    )
    retry_window.clear_editor()
    retry_window.close()
    app.processEvents()


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
            "虚构共同段\n虚构 plain 独有段",
        )

        paragraph_boundary = self.paste_editor(
            "<p><b>虚构甲</b></p><p>虚构乙</p>",
            "虚构甲虚构乙",
        )
        self.assertEqual(paragraph_boundary.toPlainText(), "虚构甲\n虚构乙")

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

    def test_paste_conflicts_choose_one_complete_representation_without_duplication(
        self,
    ) -> None:
        cases = (
            (
                "reversed_order",
                "<p>第一段</p><p>第二段</p>",
                "第二段\n第一段",
                "第二段\n第一段",
            ),
            (
                "inline_replace",
                "<p>版本 1.0</p>",
                "版本1.0",
                "版本1.0",
            ),
            (
                "cross_overlap",
                "<p>甲</p><p>乙</p><p>丙</p>",
                "乙\n丙\n丁",
                "乙\n丙\n丁",
            ),
            (
                "equal_information_conflict",
                "<p>共同段</p><p>HTML 独有段</p>",
                "共同段\nplain 独有段",
                "共同段\nplain 独有段",
            ),
            (
                "html_superset",
                "<p>甲</p><p>乙</p><p>丙</p>",
                "甲\n乙",
                "甲\n乙\n丙",
            ),
            (
                "plain_superset",
                "<p>甲</p><p>乙</p>",
                "甲\n乙\n丙",
                "甲\n乙\n丙",
            ),
        )
        for name, markup, plain_text, expected in cases:
            with self.subTest(name=name):
                editor = self.paste_editor(markup, plain_text)
                self.assertEqual(editor.toPlainText(), expected)
                self.assertEqual(editor.toPlainText().split("\n"), expected.split("\n"))
                for paragraph in expected.split("\n"):
                    if paragraph:
                        self.assertEqual(
                            editor.toPlainText().split("\n").count(paragraph),
                            expected.split("\n").count(paragraph),
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

    def test_real_toolbar_format_and_markdown_persistence_paths_use_temp_database(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="snotes-editor-real-gui-integrity-"
        ) as temporary_home:
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": temporary_home,
                    "QT_QPA_PLATFORM": "offscreen",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPYCACHEPREFIX": str(Path(temporary_home) / "pycache"),
                    "SNOTES_EDITOR_REAL_GUI_PROBE": "1",
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
    if os.environ.get("SNOTES_EDITOR_REAL_GUI_PROBE") == "1":
        _run_real_gui_format_and_markdown_probe()
    elif os.environ.get("SNOTES_EDITOR_INTEGRITY_PROBE") == "1":
        _run_window_persistence_probe()
    else:
        unittest.main()
