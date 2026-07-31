from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCENARIOS = (
    "ordinary_note",
    "note_without_internal_links",
    "note_with_internal_links",
    "repeat_open_close",
    "switch_notes_while_open",
    "switch_while_closed_then_open",
    "tag_search_result",
    "clear_search_then_open",
    "empty_current_note",
    "invalid_current_note",
    "schema_v2_identity",
    "single_drawer_and_signal",
    "event_loop_responsive",
    "quit_with_drawer_open",
    "drawer_query_is_read_only",
)


class RightSidebarFreezeTest(unittest.TestCase):
    def run_scenario(self, scenario: str) -> None:
        with tempfile.TemporaryDirectory(
            prefix=f"snotes-sidebar-{scenario}-",
            dir="/private/tmp",
        ) as temporary_directory:
            data_root = Path(temporary_directory)
            environment = os.environ.copy()
            environment.update(
                {
                    "NO_PROXY": "127.0.0.1,localhost",
                    "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                    "QT_QPA_PLATFORM": "offscreen",
                    "S_NOTES_DATA_ROOT": str(data_root),
                }
            )
            try:
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--worker",
                        scenario,
                        str(data_root),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    env=environment,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                self.fail(f"{scenario} froze past the 10 second watchdog: {error}")
            self.assertEqual(
                completed.returncode,
                0,
                msg=(
                    f"scenario={scenario}\n"
                    f"stdout={completed.stdout}\n"
                    f"stderr={completed.stderr}"
                ),
            )


def _add_scenario_test(scenario: str) -> None:
    def test(self: RightSidebarFreezeTest) -> None:
        self.run_scenario(scenario)

    test.__name__ = f"test_{scenario}"
    setattr(RightSidebarFreezeTest, test.__name__, test)


for _scenario in SCENARIOS:
    _add_scenario_test(_scenario)


def _seed_database(database: Path) -> dict[str, int]:
    from src.database import initialize_database

    initialize_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        directory_id = int(
            connection.execute(
                "SELECT id FROM directories WHERE name='临时' AND parent_id IS NULL"
            ).fetchone()[0]
        )
        notes = (
            (
                "00000000-0000-4000-8000-000000000001",
                1,
                "虚构普通笔记一",
                "<p>虚构正文一，包含 [[虚构普通笔记二]]。</p>",
                "pixel, fictional",
            ),
            (
                "00000000-0000-4000-8000-000000000002",
                2,
                "虚构普通笔记二",
                "<p>虚构正文二，没有内部链接。</p>",
                "fictional",
            ),
            (
                "00000000-0000-4000-8000-000000000003",
                3,
                "虚构普通笔记三",
                "<p>虚构正文三，用于切换。</p>",
                "pixel",
            ),
            (
                "00000000-0000-4000-8000-000000000004",
                4,
                "虚构无关联笔记",
                "<p>独立的虚构正文。</p>",
                "isolated",
            ),
            (
                "00000000-0000-4000-8000-000000000005",
                5,
                "虚构反向链接笔记",
                "<p>反向提及 [[虚构普通笔记一]]。</p>",
                "fictional, pixel",
            ),
        )
        for note_uuid, revision, title, content_html, tags in notes:
            connection.execute(
                """
                INSERT INTO notes(
                    note_uuid, revision, directory_id, title, body_rich_text,
                    content_html, main_category, sub_category, sub_sub_category,
                    tags, ai_visibility, is_pinned, is_deleted, created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, '', ?, '临时', '无', '无', ?, 'hidden',
                          0, 0, ?, ?)
                """,
                (
                    note_uuid,
                    revision,
                    directory_id,
                    title,
                    content_html,
                    tags,
                    "2026-01-01T00:00:00+08:00",
                    f"2026-01-01T00:00:0{revision}+08:00",
                ),
            )
        for tag_name in ("fictional", "isolated", "pixel"):
            connection.execute(
                "INSERT INTO tags(name, created_at) VALUES (?, ?)",
                (tag_name, "2026-01-01T00:00:00+08:00"),
            )
        note_ids = {
            str(row[1]): int(row[0])
            for row in connection.execute("SELECT id, note_uuid FROM notes")
        }
        tag_ids = {
            str(row[1]): int(row[0])
            for row in connection.execute("SELECT id, name FROM tags")
        }
        assignments = {
            "00000000-0000-4000-8000-000000000001": ("fictional", "pixel"),
            "00000000-0000-4000-8000-000000000002": ("fictional",),
            "00000000-0000-4000-8000-000000000003": ("pixel",),
            "00000000-0000-4000-8000-000000000004": ("isolated",),
            "00000000-0000-4000-8000-000000000005": ("fictional", "pixel"),
        }
        for note_uuid, tag_names in assignments.items():
            for tag_name in tag_names:
                connection.execute(
                    "INSERT INTO note_tags(note_id, tag_id) VALUES (?, ?)",
                    (note_ids[note_uuid], tag_ids[tag_name]),
                )
        connection.commit()
    return note_ids


def _database_fingerprint(database: Path) -> str:
    hasher = hashlib.sha256()
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        for table, query in (
            (
                "notes",
                """
                SELECT id, note_uuid, revision, directory_id, title,
                       body_rich_text, content_html, main_category, sub_category,
                       sub_sub_category, tags, ai_visibility, is_pinned,
                       is_deleted, created_at, updated_at, deleted_at,
                       trash_group_id
                FROM notes ORDER BY id
                """,
            ),
            (
                "directories",
                """
                SELECT id, name, parent_id, sort_order, created_at, updated_at,
                       is_deleted, deleted_at, trash_group_id
                FROM directories ORDER BY id
                """,
            ),
            ("tags", "SELECT id, name, created_at FROM tags ORDER BY id"),
            (
                "note_tags",
                "SELECT note_id, tag_id FROM note_tags ORDER BY note_id, tag_id",
            ),
            (
                "note_ai_targets",
                "SELECT id, note_id, target_id, created_at FROM note_ai_targets ORDER BY id",
            ),
        ):
            for row in connection.execute(query):
                payload = json.dumps(
                    [table, list(row)],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                hasher.update(len(payload).to_bytes(8, "big"))
                hasher.update(payload)
    return hasher.hexdigest()


def _worker(scenario: str, data_root: Path) -> int:
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from src.link_drawer import LinkDrawer
    from src.main_window import MainWindow

    database = data_root / "s_notes.db"
    note_ids = _seed_database(database)
    first = note_ids["00000000-0000-4000-8000-000000000001"]
    second = note_ids["00000000-0000-4000-8000-000000000002"]
    third = note_ids["00000000-0000-4000-8000-000000000003"]
    no_links = note_ids["00000000-0000-4000-8000-000000000004"]

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow()
    window.show()
    QTest.qWait(100)

    def require(condition: bool, message: str) -> None:
        if not condition:
            raise AssertionError(message)

    def load(note_id: int) -> None:
        window.load_note(note_id)
        QTest.qWait(30)
        require(
            window.current_note is not None
            and int(window.current_note["id"]) == note_id,
            "note_not_loaded",
        )

    def click_drawer() -> None:
        QTest.mouseClick(window.link_drawer_button, Qt.LeftButton)
        QTest.qWait(80)

    try:
        if scenario == "ordinary_note":
            load(third)
            click_drawer()
            require(window.link_drawer.isVisible(), "drawer_not_visible")
        elif scenario == "note_without_internal_links":
            load(no_links)
            click_drawer()
            require(window.link_drawer.isVisible(), "drawer_not_visible")
        elif scenario == "note_with_internal_links":
            load(first)
            click_drawer()
            require(window.link_drawer.isVisible(), "drawer_not_visible")
            require(len(window.link_drawer.cards) > 0, "linked_results_missing")
        elif scenario == "repeat_open_close":
            load(first)
            for _index in range(5):
                click_drawer()
                require(window.link_drawer.isVisible(), "drawer_open_failed")
                click_drawer()
                require(not window.link_drawer.isVisible(), "drawer_close_failed")
        elif scenario == "switch_notes_while_open":
            load(first)
            click_drawer()
            for note_id in (first, second, third):
                load(note_id)
                require(window.link_drawer.isVisible(), "drawer_closed_during_switch")
        elif scenario == "switch_while_closed_then_open":
            for note_id in (first, second, third):
                load(note_id)
            click_drawer()
            require(window.link_drawer.isVisible(), "drawer_not_visible")
        elif scenario == "tag_search_result":
            window.search_input.setText("#pixel")
            window.apply_search_filter()
            QTest.qWait(80)
            item = window.note_table.item(0, 0)
            require(item is not None, "search_result_missing")
            load(int(item.data(Qt.UserRole)))
            click_drawer()
            require(window.link_drawer.isVisible(), "drawer_not_visible")
        elif scenario == "clear_search_then_open":
            window.search_input.setText("#pixel")
            window.apply_search_filter()
            QTest.qWait(80)
            window.search_input.clear()
            window.apply_search_filter()
            QTest.qWait(80)
            load(second)
            click_drawer()
            require(window.link_drawer.isVisible(), "drawer_not_visible")
        elif scenario == "empty_current_note":
            window.clear_editor()
            click_drawer()
            require(not window.link_drawer.isVisible(), "disabled_button_opened_drawer")
        elif scenario == "invalid_current_note":
            load(first)
            with sqlite3.connect(database) as connection:
                connection.execute("UPDATE notes SET is_deleted=1 WHERE id=?", (first,))
                connection.commit()
            click_drawer()
            require(window.link_drawer.isVisible(), "drawer_not_visible")
            require(len(window.link_drawer.cards) == 0, "invalid_note_has_results")
        elif scenario == "schema_v2_identity":
            with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
                connection.execute("PRAGMA query_only=ON")
                version = connection.execute(
                    "SELECT meta_value FROM schema_meta WHERE meta_key='ai_foundation_version'"
                ).fetchone()[0]
                identities = connection.execute(
                    "SELECT note_uuid, revision FROM notes ORDER BY id"
                ).fetchall()
            require(str(version) == "2", "schema_not_v2")
            require(all(row[0] and int(row[1]) >= 1 for row in identities), "identity_invalid")
            load(first)
            click_drawer()
            require(window.link_drawer.isVisible(), "drawer_not_visible")
        elif scenario == "single_drawer_and_signal":
            load(first)
            refresh_count = {"value": 0}
            original_refresh = window.refresh_link_drawer

            def counted_refresh() -> None:
                refresh_count["value"] += 1
                original_refresh()

            window.refresh_link_drawer = counted_refresh  # type: ignore[method-assign]
            for _index in range(5):
                click_drawer()
                click_drawer()
            require(refresh_count["value"] == 5, "duplicate_refresh_signal")
            require(len(window.findChildren(LinkDrawer)) == 1, "duplicate_drawer")
        elif scenario == "event_loop_responsive":
            load(first)
            fired = {"value": False}
            QTimer.singleShot(100, lambda: fired.__setitem__("value", True))
            click_drawer()
            QTest.qWait(250)
            require(fired["value"], "event_loop_stalled")
        elif scenario == "quit_with_drawer_open":
            load(first)
            click_drawer()
            QTimer.singleShot(50, window.close)
            QTest.qWait(200)
            require(not window.isVisible(), "window_did_not_close")
        elif scenario == "drawer_query_is_read_only":
            before = _database_fingerprint(database)
            load(first)
            click_drawer()
            after = _database_fingerprint(database)
            require(after == before, "drawer_modified_business_fields")
        else:
            raise AssertionError("unknown_scenario")
    finally:
        window.close()
        QTest.qWait(50)
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        raise SystemExit(_worker(sys.argv[2], Path(sys.argv[3]).resolve()))
    unittest.main()
