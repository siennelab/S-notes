from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject, QThread, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_receipt_inbox_service import AiReceiptInboxService
from src.snotes.application.note_service import NoteService
from src.snotes.application.tag_service import TagService
from src.snotes.domain.ai_receipts import GaleReceiptImport
from src.snotes.domain.models import NoteLocation
from src.snotes.storage.repositories.ai_receipts import SQLiteAiReceiptInboxRepository
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.ui.ai_center_dialog import AiCenterDialog
from src.snotes.ui.theme import THEMES, apply_theme


class FakeGaleControl:
    def __init__(self) -> None:
        self.manual_calls = 0
        self.pull_calls = 0
        self.auto_publish = False
        self.auto_pull = True
        self.publish_at = ""
        self.pull_at = ""
        self.pull_started = threading.Event()
        self.pull_release = threading.Event()
        self.pull_release.set()

    def manual_update(self):
        self.manual_calls += 1
        self.publish_at = "2026-08-10T12:34:00+00:00"
        return object()

    def check_submissions_now(self):
        self.pull_started.set()
        self.pull_release.wait(timeout=2)
        self.pull_calls += 1
        self.pull_at = "2026-08-10T12:35:00+00:00"
        return object()

    def last_successful_publish_at(self):
        return self.publish_at

    def last_completed_pull_at(self):
        return self.pull_at

    def auto_publish_enabled(self):
        return self.auto_publish

    def set_auto_publish_enabled(self, value):
        self.auto_publish = bool(value)

    def auto_pull_enabled(self):
        return self.auto_pull

    def set_auto_pull_enabled(self, value):
        self.auto_pull = bool(value)


class WindowLifecycleWatch(QObject):
    def __init__(self, application, host, dialog) -> None:
        super().__init__()
        self.application = application
        self.host = host
        self.dialog = dialog
        self.center_hidden = 0
        self.center_closed = 0
        self.center_deactivated = 0
        self.center_activated = 0
        self.main_activated = 0
        self.top_level_receipt_button_shows = 0
        self.samples = []

    def eventFilter(self, watched, event):
        if (
            isinstance(watched, QPushButton)
            and watched.objectName() == "receiptAcceptButton"
            and event.type() == QEvent.Show
            and watched.isWindow()
        ):
            self.top_level_receipt_button_shows += 1
        if watched is self.dialog:
            if event.type() == QEvent.Hide:
                self.center_hidden += 1
            elif event.type() == QEvent.Close:
                self.center_closed += 1
            elif event.type() == QEvent.WindowDeactivate:
                self.center_deactivated += 1
            elif event.type() == QEvent.WindowActivate:
                self.center_activated += 1
        elif watched is self.host and event.type() == QEvent.WindowActivate:
            self.main_activated += 1
        return False

    def sample(self) -> None:
        self.samples.append((
            self.dialog.isVisible(),
            self.dialog.isActiveWindow(),
            self.application.activeWindow() is self.dialog,
        ))


class Package6GaleUiWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-r2-p6-ui-")
        self.addCleanup(self.temporary.cleanup)
        connections = initialize_database(Path(self.temporary.name) / "fixture.db")
        directories = SQLiteDirectoriesRepository(connections)
        notes_repository = SQLiteNotesRepository(connections, directories)
        tags_repository = SQLiteTagsRepository(connections)
        notes = NoteService(notes_repository)
        tags = TagService(tags_repository)
        metadata = AiAccessMetadataService(notes_repository)
        receipts = AiReceiptInboxService(
            SQLiteAiReceiptInboxRepository(connections, directories, tags_repository),
            notes=notes,
            tags=tags,
            ai_metadata=metadata,
        )
        self.receipts = receipts
        self.gale = FakeGaleControl()
        self.host = QMainWindow()
        self.dialog = AiCenterDialog(
            receipts, metadata, parent=self.host, gale=self.gale
        )
        self.addCleanup(self._close_dialog)

    def _close_dialog(self) -> None:
        for _attempt in range(100):
            if not self.dialog._gale_threads:
                break
            QTest.qWait(10)
        self.dialog.close()
        self.host.close()
        self.application.processEvents()

    def _wait_for(self, predicate) -> None:
        for _attempt in range(100):
            self.application.processEvents()
            if predicate():
                return
            QTest.qWait(10)
        self.fail("Gale UI worker did not finish")

    def test_controls_only_call_application_owner(self) -> None:
        self.assertEqual(self.dialog.gale_publish_button.text(), "立即更新镜像")
        self.assertEqual(self.dialog.gale_last_publish.text(), "上次发布：尚未发布")
        self.assertEqual(self.dialog.gale_last_pull.text(), "上次检查：尚未检查")
        self.assertIn("15 分钟", self.dialog.findChild(
            type(self.dialog.gale_status), "galeAutoPublishHelp"
        ).text())
        self.assertIn("30 分钟", self.dialog.findChild(
            type(self.dialog.gale_status), "galeAutoPublishHelp"
        ).text())
        self.assertIn("每 5 分钟", self.dialog.findChild(
            type(self.dialog.gale_status), "galeAutoPullHelp"
        ).text())
        self.assertFalse(self.dialog.gale_auto_publish.isChecked())
        self.assertTrue(self.dialog.gale_auto_pull.isChecked())
        self.dialog.gale_auto_publish.setChecked(True)
        self.dialog.gale_auto_pull.setChecked(False)
        self.assertTrue(self.gale.auto_publish)
        self.assertFalse(self.gale.auto_pull)

        self.dialog.gale_publish_button.click()
        self._wait_for(lambda: self.gale.manual_calls == 1)
        self._wait_for(lambda: not self.dialog._gale_threads)
        self.assertRegex(
            self.dialog.gale_last_publish.text(),
            r"^上次发布：\d{4}-\d{2}-\d{2} \d{2}:\d{2}$",
        )
        self.dialog.gale_pull_button.click()
        self._wait_for(lambda: self.gale.pull_calls == 1)
        self._wait_for(lambda: not self.dialog._gale_threads)
        self.assertRegex(
            self.dialog.gale_last_pull.text(),
            r"^上次检查：\d{4}-\d{2}-\d{2} \d{2}:\d{2}$",
        )
        self.assertEqual(self.dialog.gale_status.text(), "操作完成")

    def test_controls_form_four_row_two_column_responsive_grid(self) -> None:
        grid = self.dialog.gale_controls_layout
        widgets = (
            self.dialog.gale_publish_button,
            self.dialog.gale_last_publish,
            self.dialog.gale_pull_button,
            self.dialog.gale_last_pull,
            self.dialog.gale_auto_publish,
            self.dialog.findChild(
                type(self.dialog.gale_status), "galeAutoPublishHelp"
            ),
            self.dialog.gale_auto_pull,
            self.dialog.findChild(
                type(self.dialog.gale_status), "galeAutoPullHelp"
            ),
        )
        expected = (
            (0, 0), (0, 1),
            (1, 0), (1, 1),
            (2, 0), (2, 1),
            (3, 0), (3, 1),
        )
        for widget, position in zip(widgets, expected):
            index = grid.indexOf(widget)
            row, column, row_span, column_span = grid.getItemPosition(index)
            self.assertEqual(
                (row, column, row_span, column_span),
                (*position, 1, 1),
            )

        for label in widgets[1::2]:
            self.assertTrue(label.alignment() & Qt.AlignLeft)
            self.assertFalse(label.alignment() & Qt.AlignRight)
            self.assertTrue(label.alignment() & Qt.AlignVCenter)
            self.assertTrue(label.wordWrap())
        self.assertEqual(grid.columnStretch(0), 0)
        self.assertEqual(grid.columnStretch(1), 1)

        self.dialog.show()
        for theme_name in THEMES:
            with self.subTest(theme=theme_name):
                apply_theme(self.application, theme_name)
                self.dialog.resize(900, 700)
                self.application.processEvents()
                left_x = {
                    widget.mapTo(self.dialog.gale_page, widget.rect().topLeft()).x()
                    for widget in widgets[::2]
                }
                right_x = {
                    widget.mapTo(self.dialog.gale_page, widget.rect().topLeft()).x()
                    for widget in widgets[1::2]
                }
                self.assertEqual(len(left_x), 1)
                self.assertEqual(len(right_x), 1)
                for left, right in zip(widgets[::2], widgets[1::2]):
                    left_center = left.mapTo(
                        self.dialog.gale_page, left.rect().center()
                    ).y()
                    right_center = right.mapTo(
                        self.dialog.gale_page, right.rect().center()
                    ).y()
                    self.assertLessEqual(abs(left_center - right_center), 1)
                    text_height = right.fontMetrics().boundingRect(
                        right.rect(),
                        Qt.TextWordWrap | Qt.AlignLeft,
                        right.text(),
                    ).height()
                    one_line_height = right.fontMetrics().boundingRect(
                        right.text()
                    ).height()
                    self.assertLessEqual(text_height, one_line_height + 1)

        self.dialog.resize(720, 520)
        self.application.processEvents()
        for left, right in zip(widgets[::2], widgets[1::2]):
            left_right = left.mapTo(
                self.dialog.gale_page, left.rect().topRight()
            ).x()
            right_left = right.mapTo(
                self.dialog.gale_page, right.rect().topLeft()
            ).x()
            self.assertLess(left_right, right_left)

    def test_pull_keeps_same_host_visible_and_active_continuously(self) -> None:
        original_pull = self.gale.check_submissions_now

        def pull_with_new_receipt():
            self.receipts.import_gale_submission(
                GaleReceiptImport(
                    external_submission_id="window-lifecycle-submission",
                    idempotency_key="window-lifecycle-key",
                    title="Window lifecycle receipt",
                    body="Temporary test receipt",
                    location=NoteLocation("临时", "Gale 投稿"),
                    tags=("window-lifecycle",),
                )
            )
            return original_pull()

        self.gale.check_submissions_now = pull_with_new_receipt
        self.host.show()
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
        self.application.processEvents()
        original_dialog = self.dialog
        refresh_threads = []
        activation_threads = []
        original_refresh = self.dialog.receipt_view.refresh_receipts

        def refresh_receipts() -> None:
            refresh_threads.append(
                QThread.currentThread() is self.application.thread()
            )
            original_refresh()

        def activate_host() -> None:
            activation_threads.append(
                QThread.currentThread() is self.application.thread()
            )

        self.dialog.receipt_view.refresh_receipts = refresh_receipts
        self.dialog.receipt_view._activate_host_window = activate_host
        watch = WindowLifecycleWatch(
            self.application, self.host, self.dialog
        )
        self.application.installEventFilter(watch)
        self.addCleanup(self.application.removeEventFilter, watch)
        timer = QTimer(self.dialog)
        timer.setInterval(1)
        timer.timeout.connect(watch.sample)
        timer.start()

        for expected_calls in range(1, 4):
            self.gale.pull_started.clear()
            self.gale.pull_release.clear()
            self.dialog.gale_pull_button.click()
            self._wait_for(self.gale.pull_started.is_set)
            self.dialog.receipt_view.refresh_receipts()
            self.application.processEvents()
            QTest.qWait(20)
            self.assertTrue(self.dialog.isVisible())
            self.assertTrue(self.dialog.isActiveWindow())
            self.assertIs(self.application.activeWindow(), self.dialog)
            self.gale.pull_release.set()
            self._wait_for(lambda: self.gale.pull_calls == expected_calls)
            self._wait_for(lambda: not self.dialog._gale_threads)
            self.assertTrue(self.dialog.isVisible())
            self.assertTrue(self.dialog.isActiveWindow())
            self.assertIs(self.application.activeWindow(), self.dialog)

        timer.stop()
        self.assertEqual(refresh_threads, [True] * 6)
        self.assertEqual(activation_threads, [])
        self.assertGreater(len(watch.samples), 0)
        self.assertTrue(all(all(sample) for sample in watch.samples))
        self.assertEqual(watch.center_hidden, 0)
        self.assertEqual(watch.center_closed, 0)
        self.assertEqual(watch.center_deactivated, 0)
        self.assertEqual(watch.center_activated, 0)
        self.assertEqual(watch.top_level_receipt_button_shows, 0)
        self.assertEqual(watch.main_activated, 0)
        self.assertIs(self.dialog, original_dialog)
        self.assertEqual(len(self.receipts.list_pending_receipts()), 1)
        self.assertTrue(self.dialog.isVisible())
        self.assertTrue(self.dialog.isEnabled())
        self.assertTrue(self.dialog.gale_pull_button.isEnabled())
        self.assertEqual(self.dialog.gale_status.text(), "操作完成")


if __name__ == "__main__":
    unittest.main()
