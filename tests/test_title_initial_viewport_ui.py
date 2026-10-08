"""Long title hydration and editing against Qt and fictional Notes only."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from src.snotes.bootstrap import build_application
from src.snotes.reminder_authority import LOCAL_REMINDER_AUTHORITY


LONG_TITLE = "TITLE-BEGIN " + "Fictional long title " * 30 + " TITLE-END"


class TitleInitialViewportUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.home = tempfile.TemporaryDirectory(prefix="snotes-title-home-")
        cls.environment = patch.dict(os.environ, {
            "HOME": cls.home.name,
            "S_NOTES_DATA_ROOT": cls.home.name,
            "QT_QPA_PLATFORM": "offscreen",
            "QTWEBENGINE_CHROMIUM_FLAGS": "--disable-gpu",
        })
        cls.environment.start()
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    @classmethod
    def tearDownClass(cls) -> None:
        QTimer.singleShot(0, cls.app.quit)
        cls.app.exec()
        cls.environment.stop()
        cls.home.cleanup()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=self.home.name)
        _, self.window = build_application(
            [], data_root=Path(self.temporary.name),
            reminder_authority=LOCAL_REMINDER_AUTHORITY,
        )
        self.window.resize(900, 700)
        self.window.show()
        QTest.qWait(20)
        self.title = self.window.title_input

    def tearDown(self) -> None:
        self.window.has_unsaved_changes = False
        self.window.conflict_active = False
        self.window.close()
        self._wait(lambda: not self.window.isVisible())
        self.window.body_edit.page.deleteLater()
        self.window.deleteLater()
        QTest.qWait(20)
        self.temporary.cleanup()

    def _wait(self, predicate) -> None:
        deadline = time.monotonic() + 15
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertTrue(predicate())

    def _create(self, title: str):
        service = self.window.note_service
        note = service.create_note()
        return service.save_note(
            note.id, title=title, content_markdown="Fictional body\n",
            location=note.location, expected_revision=note.revision,
        )

    def _load(self, note, *, switch: bool = False) -> None:
        if switch:
            self.window.switch_note(note.id)
        else:
            self.assertTrue(self.window.load_note(note.id))
        self._wait(lambda: self.window.current_note is not None
                   and self.window.current_note.id == note.id
                   and self.window._pending_note is None
                   and self.window.body_edit.can_save)
        QTest.qWait(20)

    def _left_visible_position(self) -> int:
        return self.title.cursorPositionAt(QPoint(8, self.title.height() // 2))

    def _assert_start(self) -> None:
        self.assertEqual(self.title.text(), LONG_TITLE)
        self.assertGreater(self.title.fontMetrics().horizontalAdvance(LONG_TITLE),
                           self.title.width())
        self.assertEqual(self.title.cursorPosition(), 0)
        self.assertEqual(self._left_visible_position(), 0)

    def _edit_at(self, position: int) -> None:
        QTest.mouseClick(self.title, Qt.LeftButton)
        self.title.setCursorPosition(position)
        self.assertTrue(self.title.hasFocus())

    def test_first_long_title_load_starts_at_left_and_resize_keeps_it(self) -> None:
        self._load(self._create(LONG_TITLE))
        self.assertIsInstance(self.title, QLineEdit)
        self._assert_start()
        for width in (1200, 800, 900):
            self.window.resize(width, 700)
            QTest.qWait(20)
            self._assert_start()

    def test_short_to_long_sidebar_selection_starts_at_left(self) -> None:
        short, long = self._create("Short"), self._create(LONG_TITLE)
        self._load(short)
        self.window.refresh_note_list()
        listing = self.window.shell.sidebar.note_list
        item = next(listing.item(i) for i in range(listing.count())
                    if listing.item(i).data(Qt.UserRole) == long.id)
        listing.scrollToItem(item)
        QTest.mouseClick(listing.viewport(), Qt.LeftButton,
                         pos=listing.visualItemRect(item).center())
        self._wait(lambda: self.window.current_note.id == long.id
                   and self.window.body_edit.can_save)
        QTest.qWait(20)
        self._assert_start()

    def test_long_short_long_resets_previous_end_position(self) -> None:
        long, short = self._create(LONG_TITLE), self._create("Short")
        self._load(long)
        self._edit_at(len(LONG_TITLE))
        self._load(short, switch=True)
        self._load(long, switch=True)
        self._assert_start()

    def test_user_home_end_and_arrows_scroll_normally(self) -> None:
        self._load(self._create(LONG_TITLE))
        self._edit_at(0)
        QTest.keyClick(self.title, Qt.Key_End)
        QTest.qWait(20)
        self.assertEqual(self.title.cursorPosition(), len(LONG_TITLE))
        self.assertGreater(self._left_visible_position(), 0)
        QTest.keyClick(self.title, Qt.Key_Left)
        self.assertEqual(self.title.cursorPosition(), len(LONG_TITLE) - 1)
        QTest.keyClick(self.title, Qt.Key_Right)
        self.assertEqual(self.title.cursorPosition(), len(LONG_TITLE))
        QTest.keyClick(self.title, Qt.Key_Home)
        QTest.qWait(20)
        self._assert_start()

    def test_autosave_save_and_background_refresh_preserve_edit_cursor(self) -> None:
        self._load(self._create(LONG_TITLE))
        self._edit_at(300)
        QTest.keyClicks(self.title, "EDIT")
        position = self.title.cursorPosition()
        self._wait(lambda: not self.window.has_unsaved_changes)
        self.assertEqual(self.window.current_note.title, self.title.text())
        self.assertEqual(self.title.cursorPosition(), position)
        # An actual save caused by another field must also leave title alone.
        self.window.shell.tags_input.setText("fictional-tag")
        self.assertTrue(self.window.force_save_current_note())
        self.window.refresh_note_list()
        self.window.refresh_external_receipts()
        self.assertTrue(self.title.hasFocus())
        self.assertEqual(self.title.cursorPosition(), position)

    def test_unchanged_title_refresh_preserves_focused_cursor_and_selection(self) -> None:
        note = self._create(LONG_TITLE)
        self._load(note)
        self._edit_at(300)
        self.title.setSelection(300, 4)
        self.window.note_service.save_note(
            note.id, title=note.title, content_markdown="External body update\n",
            location=note.location, expected_revision=note.revision,
        )
        position = self.title.cursorPosition()
        self.window._receipt_note_created(note.id)
        self._wait(lambda: self.window._pending_note is None
                   and self.window.body_edit.can_save)
        self.assertTrue(self.title.hasFocus())
        self.assertEqual(self.title.cursorPosition(), position)
        self.assertEqual(self.title.selectionStart(), 300)
        self.assertEqual(self.title.selectedText(), LONG_TITLE[300:304])

    def test_idle_reload_and_changed_formal_title_refresh_start_at_left(self) -> None:
        note = self._create(LONG_TITLE)
        self._load(note)
        self._edit_at(len(LONG_TITLE))
        self.window.shell.sidebar.search_input.setFocus()
        self.window.reload_after_conflict()
        self._wait(lambda: self.window._pending_note is None
                   and self.window.body_edit.can_save)
        self._assert_start()
        self._edit_at(100)
        changed_title = "REFRESHED " + LONG_TITLE
        current = self.window.current_note
        self.window.note_service.save_note(
            current.id, title=changed_title, content_markdown=current.content_markdown,
            location=current.location, expected_revision=current.revision,
        )
        self.window._receipt_note_created(current.id)
        self._wait(lambda: self.window._pending_note is None
                   and self.window.body_edit.can_save)
        QTest.qWait(20)
        self.assertEqual(self.title.text(), changed_title)
        self.assertEqual(self.title.cursorPosition(), 0)
        self.assertEqual(self._left_visible_position(), 0)


if __name__ == "__main__":
    unittest.main()
