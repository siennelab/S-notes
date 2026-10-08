from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QKeyEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.snotes.bootstrap import build_application
from src.snotes.reminder_authority import LOCAL_REMINDER_AUTHORITY
from src.snotes.ui.theme import THEMES


class SingleLineMetadataScrollUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-single-line-metadata-",
            dir="/private/tmp",
        )
        _application, self.window = build_application(
            [],
            data_root=Path(self.temporary.name) / "data",
            reminder_authority=LOCAL_REMINDER_AUTHORITY,
        )
        self.window.resize(900, 700)
        self.window.show()
        QTest.qWait(20)

    def tearDown(self) -> None:
        self.window.has_unsaved_changes = False
        self.window.conflict_active = False
        self.window.close()
        deadline = time.monotonic() + 15
        while self.window.isVisible() and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertFalse(self.window.isVisible())
        self.window.body_edit.page.deleteLater()
        self.window.deleteLater()
        QTest.qWait(20)
        self.temporary.cleanup()

    def _wait(self, predicate, timeout: float = 15.0) -> None:
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertTrue(predicate())

    def _javascript(self, code: str):
        result: list[object] = []
        self.window.body_edit.page.runJavaScript(code, result.append)
        self._wait(lambda: bool(result))
        return result[0]

    def _create_note(self, *, body: str, tags: tuple[str, ...]):
        note = self.window.note_service.create_note()
        note = self.window.note_service.save_note(
            note.id,
            title="虚构 metadata 横向滚动验收",
            content_markdown=body,
            location=note.location,
            expected_revision=note.revision,
        )
        self.window.tag_service.set_note_tags(note.id, tags)
        return self.window.note_service.get_note(note.id)

    def _load_note(self, note_id: int, *, switch: bool = False) -> None:
        if switch:
            self.window.switch_note(note_id)
        else:
            self.assertTrue(self.window.load_note(note_id))
        self._wait(
            lambda: self.window.current_note is not None
            and self.window.current_note.id == note_id
            and self.window.body_edit.can_save
        )
        QApplication.processEvents()

    @staticmethod
    def _layout_minimum_widths(window) -> tuple[int, int, int]:
        window.layout().activate()
        window.shell.layout().activate()
        window.shell.editor_pane.layout().activate()
        return (
            window.minimumSizeHint().width(),
            window.shell.minimumSizeHint().width(),
            window.shell.editor_pane.minimumSizeHint().width(),
        )

    def test_link_drawer_restores_pre_open_width_for_each_close_path(self) -> None:
        note = self._create_note(body="drawer body", tags=("short",))
        self._load_note(note.id)
        shell = self.window.shell
        for expanded, close_path in (
            (False, "title_button"),
            (True, "drawer_button"),
            (True, "reminder"),
        ):
            with self.subTest(expanded=expanded, close_path=close_path):
                shell.set_metadata_expanded(expanded)
                QTest.qWait(20)
                self.window.resize(800, 700)
                QTest.qWait(20)
                before_width = self.window.width()
                before_minimum = self.window.minimumWidth()
                QTest.mouseClick(shell.links_button, Qt.LeftButton)
                QTest.qWait(20)
                self.assertFalse(shell.link_drawer.isHidden())
                self.assertGreaterEqual(self.window.width(), before_width)
                if expanded:
                    self.assertGreater(self.window.width(), before_width)
                # A height change while the drawer is open must survive closing.
                self.window.resize(self.window.width() + 80, 740)
                QTest.qWait(20)
                height_before_close = self.window.height()
                if close_path == "title_button":
                    QTest.mouseClick(shell.links_button, Qt.LeftButton)
                elif close_path == "drawer_button":
                    QTest.mouseClick(shell.link_drawer.close_button, Qt.LeftButton)
                else:
                    self.window.show_smart_view("reminder")
                self._wait(
                    lambda: shell.link_drawer.isHidden()
                    and self.window.width() == before_width
                )
                self.assertEqual(self.window.height(), height_before_close)
                self.assertEqual(self.window.minimumWidth(), before_minimum)

    def test_link_drawer_close_keeps_maximized_window_maximized(self) -> None:
        note = self._create_note(body="maximized drawer body", tags=("short",))
        self._load_note(note.id)
        self.window.toggle_links()
        QTest.qWait(20)
        self.window.showMaximized()
        QTest.qWait(20)
        self.assertTrue(self.window.isMaximized())
        self.window.toggle_links()
        QTest.qWait(20)
        self.assertTrue(self.window.isMaximized())
        self.assertIsNone(self.window._width_before_links)

    def test_long_metadata_stays_complete_without_growing_layout_minimum(self) -> None:
        short = self._create_note(body="short body", tags=("short",))
        sixty_tags = tuple(
            f"fictional-long-tag-{index:02d}-abcdefghij" for index in range(60)
        )
        one_hundred_twenty_tags = tuple(
            f"fictional-longer-tag-{index:03d}-abcdefghij" for index in range(120)
        )
        long_note = self._create_note(body="long body", tags=sixty_tags)
        longer_note = self._create_note(
            body="longer body",
            tags=one_hundred_twenty_tags,
        )
        target_ids = tuple(self.window.shell.ai_target_checks)
        target_names = tuple(
            checkbox.text()
            for checkbox in self.window.shell.ai_target_checks.values()
        )
        self.window.ai_access_metadata_service.set_note_targets(
            long_note.id,
            target_ids,
        )
        self.window.ai_access_metadata_service.set_note_targets(
            longer_note.id,
            target_ids,
        )

        self._load_note(short.id)
        short_collapsed_minimums = self._layout_minimum_widths(self.window)
        short_minimum_width = self.window.minimumWidth()
        self.window.resize(900, 700)
        QApplication.processEvents()
        short_window_width = self.window.width()
        short_button_height = self.window.shell.metadata_summary_button.height()

        self._load_note(long_note.id, switch=True)
        sixty_collapsed_minimums = self._layout_minimum_widths(self.window)
        self.window.resize(short_window_width, 700)
        QApplication.processEvents()

        self.assertEqual(sixty_collapsed_minimums, short_collapsed_minimums)
        self.assertEqual(self.window.minimumWidth(), short_minimum_width)
        self.assertEqual(self.window.width(), short_window_width)
        summary = self.window.shell.metadata_summary_button.text()
        self.assertIn(f"#{sixty_tags[-1]}", summary)
        for target_name in target_names:
            self.assertIn(target_name, summary)
        self.assertNotIn("\n", summary)
        self.assertNotIn("…", summary)
        self.assertNotIn("...", summary)
        self.assertGreaterEqual(
            self.window.shell.metadata_summary_button.width(),
            self.window.shell.metadata_summary_button.minimumSizeHint().width(),
        )
        self.assertEqual(
            self.window.shell.metadata_summary_button.height(),
            short_button_height,
        )
        self.assertGreater(
            self.window.shell.metadata_summary_scroll.horizontalScrollBar().maximum(),
            0,
        )

        self._load_note(longer_note.id, switch=True)
        longer_collapsed_minimums = self._layout_minimum_widths(self.window)
        self.window.resize(short_window_width, 700)
        QApplication.processEvents()
        self.assertEqual(longer_collapsed_minimums, short_collapsed_minimums)
        self.assertEqual(self.window.minimumWidth(), short_minimum_width)
        self.assertEqual(self.window.width(), short_window_width)
        longer_summary = self.window.shell.metadata_summary_button.text()
        self.assertIn(f"#{one_hundred_twenty_tags[-1]}", longer_summary)
        self.assertNotIn("\n", longer_summary)
        self.assertNotIn("…", longer_summary)
        self.assertNotIn("...", longer_summary)

        self.window.shell.set_metadata_expanded(True)
        longer_expanded_minimums = self._layout_minimum_widths(self.window)
        self._load_note(long_note.id, switch=True)
        sixty_expanded_minimums = self._layout_minimum_widths(self.window)
        self._load_note(short.id, switch=True)
        short_expanded_minimums = self._layout_minimum_widths(self.window)
        self.assertEqual(sixty_expanded_minimums[2], short_expanded_minimums[2])
        self.assertEqual(longer_expanded_minimums[2], short_expanded_minimums[2])
        for index in (0, 1):
            self.assertLessEqual(
                sixty_expanded_minimums[index],
                short_expanded_minimums[index],
            )
            self.assertLessEqual(
                longer_expanded_minimums[index],
                sixty_expanded_minimums[index],
            )

    def test_click_focus_arrow_repeat_boundaries_and_native_horizontal_wheel(
        self,
    ) -> None:
        long_tags = tuple(
            f"fictional-scroll-tag-{index:02d}-abcdefghij" for index in range(60)
        )
        note = self._create_note(body="scroll body", tags=long_tags)
        self._load_note(note.id)
        scroll = self.window.shell.metadata_summary_scroll
        button = self.window.shell.metadata_summary_button
        bar = scroll.horizontalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        self.assertEqual(
            scroll.horizontalScrollBarPolicy(),
            Qt.ScrollBarAlwaysOff,
        )
        self.assertFalse(scroll.horizontalScrollBar().isVisible())

        expanded_before_click = self.window.shell.metadata_expanded
        visible_center = scroll.viewport().rect().center()
        button_position = button.mapFrom(scroll.viewport(), visible_center)
        self.assertTrue(button.rect().contains(button_position))
        QTest.mouseClick(button, Qt.LeftButton, pos=button_position)
        QApplication.processEvents()
        self.assertTrue(scroll.hasFocus())
        self.assertNotEqual(self.window.shell.metadata_expanded, expanded_before_click)

        expanded_before_space = self.window.shell.metadata_expanded
        QTest.keyClick(scroll, Qt.Key_Space)
        self.assertNotEqual(self.window.shell.metadata_expanded, expanded_before_space)
        repeat_space = QKeyEvent(
            QEvent.KeyPress,
            Qt.Key_Space,
            Qt.NoModifier,
            " ",
            True,
            1,
        )
        QApplication.sendEvent(scroll, repeat_space)
        self.assertNotEqual(self.window.shell.metadata_expanded, expanded_before_space)
        expanded_before_enter = self.window.shell.metadata_expanded
        QTest.keyClick(scroll, Qt.Key_Return)
        self.assertNotEqual(self.window.shell.metadata_expanded, expanded_before_enter)

        QTest.keyClick(scroll, Qt.Key_Right)
        self.assertEqual(bar.value(), bar.singleStep())
        repeat = QKeyEvent(
            QEvent.KeyPress,
            Qt.Key_Right,
            Qt.NoModifier,
            "",
            True,
            1,
        )
        QApplication.sendEvent(scroll, repeat)
        self.assertEqual(bar.value(), bar.singleStep() * 2)

        bar.setValue(bar.maximum())
        QTest.keyClick(scroll, Qt.Key_Right)
        self.assertEqual(bar.value(), bar.maximum())
        QTest.keyClick(scroll, Qt.Key_Left)
        self.assertEqual(bar.value(), bar.maximum() - bar.singleStep())
        bar.setValue(0)
        QTest.keyClick(scroll, Qt.Key_Left)
        self.assertEqual(bar.value(), 0)

        local = QPointF(scroll.viewport().rect().center())
        global_position = QPointF(
            scroll.viewport().mapToGlobal(local.toPoint())
        )
        wheel = QWheelEvent(
            local,
            global_position,
            QPoint(),
            QPoint(-120, 0),
            Qt.NoButton,
            Qt.NoModifier,
            Qt.ScrollUpdate,
            False,
        )
        QApplication.sendEvent(scroll.viewport(), wheel)
        self.assertGreater(bar.value(), 0)

    def test_direction_keys_remain_local_to_search_and_body_editor(self) -> None:
        long_tags = tuple(
            f"fictional-focus-tag-{index:02d}-abcdefghij" for index in range(60)
        )
        note = self._create_note(body="abcdef", tags=long_tags)
        self._load_note(note.id)
        scroll = self.window.shell.metadata_summary_scroll
        bar = scroll.horizontalScrollBar()
        bar.setValue(min(bar.maximum(), bar.singleStep() * 3))
        unchanged_scroll = bar.value()

        search = self.window.shell.sidebar.search_input
        search.setText("abcdef")
        search.setCursorPosition(2)
        search.setFocus()
        QTest.keyClick(search, Qt.Key_Right)
        self.assertEqual(search.cursorPosition(), 3)
        QTest.keyClick(search, Qt.Key_Left)
        self.assertEqual(search.cursorPosition(), 2)
        self.assertEqual(bar.value(), unchanged_scroll)

        selection_script = """(() => {
            const editor = document.querySelector('.ProseMirror');
            const node = editor.querySelector('p').firstChild;
            editor.focus();
            const range = document.createRange();
            range.setStart(node, 2);
            range.collapse(true);
            const selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
            return selection.anchorOffset;
        })()"""
        self.assertEqual(self._javascript(selection_script), 2)
        self.window.body_edit.view.setFocus()
        self._javascript(selection_script)
        QTest.qWait(40)
        target = self.window.body_edit.view.focusProxy()
        self.assertIsNotNone(target)
        QTest.keyClick(target, Qt.Key_Right)
        QTest.qWait(40)
        self.assertEqual(
            self._javascript("window.getSelection().anchorOffset"),
            3,
        )
        QTest.keyClick(target, Qt.Key_Left)
        QTest.qWait(40)
        self.assertEqual(
            self._javascript("window.getSelection().anchorOffset"),
            2,
        )
        self.assertEqual(bar.value(), unchanged_scroll)

    def test_switching_notes_resets_metadata_to_the_left(self) -> None:
        long_tags = tuple(
            f"fictional-reset-tag-{index:02d}-abcdefghij" for index in range(60)
        )
        first = self._create_note(body="first", tags=long_tags)
        second = self._create_note(body="second", tags=("short",))
        self._load_note(first.id)
        bar = self.window.shell.metadata_summary_scroll.horizontalScrollBar()
        bar.setValue(bar.maximum())
        self.assertGreater(bar.value(), 0)

        self._load_note(second.id, switch=True)
        self.assertEqual(bar.value(), 0)
        self._load_note(first.id, switch=True)
        self.assertGreater(bar.maximum(), 0)
        self.assertEqual(bar.value(), 0)

    def test_metadata_edges_blend_into_each_theme_with_and_without_focus(self) -> None:
        note = self._create_note(
            body="theme fixture",
            tags=tuple(f"fictional-theme-tag-{index:03d}" for index in range(60)),
        )
        self._load_note(note.id)
        scroll = self.window.shell.metadata_summary_scroll
        for theme_name, tokens in THEMES.items():
            self.window.set_theme(theme_name)
            for expanded in (False, True):
                self.window.shell.set_metadata_expanded(expanded)
                for focused in (False, True):
                    with self.subTest(theme=theme_name, expanded=expanded, focused=focused):
                        if focused:
                            scroll.setFocus()
                        else:
                            self.window.title_input.setFocus()
                        QTest.qWait(20)
                        self.assertEqual(scroll.hasFocus(), focused)
                        image = self.window.grab().toImage()
                        viewport = scroll.viewport()
                        origin = viewport.mapTo(self.window, QPoint(0, 0))
                        scale = image.devicePixelRatio()
                        # Sample the rendered top/bottom edges and left corners.
                        # A transparent button over the default white viewport,
                        # or an inherited focus border, changes these pixels.
                        for x in (0, viewport.width() // 2, viewport.width() - 1):
                            for y in (0, viewport.height() - 1):
                                self.assertEqual(
                                    image.pixelColor(
                                        int((origin.x() + x) * scale),
                                        int((origin.y() + y) * scale),
                                    ),
                                    QColor(tokens.editor),
                                )


if __name__ == "__main__":
    unittest.main()
