from __future__ import annotations

import os
import json
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

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
    def __init__(self, actor_state_path: Path) -> None:
        self.actor_state_path = Path(actor_state_path)
        self.publish_calls = 0
        self.publish_status = "published"
        self.publish_reason = "none"
        self.publish_error: Exception | None = None
        self.publish_at = "2026-08-10T12:34:00+00:00"
        self.publish_started = threading.Event()
        self.publish_release = threading.Event()
        self.publish_release.set()
        self.publish_thread = None
        self.pull_calls = 0
        self.auto_pull = True
        self.pull_at = ""
        self.pull_started = threading.Event()
        self.pull_release = threading.Event()
        self.pull_release.set()
        self.pull_thread = None

    def manual_update(self):
        raise AssertionError("retired Oracle publish must not be called by the UI")

    def publish_greencloud_mirror_now(self):
        self.publish_thread = QThread.currentThread()
        self.publish_started.set()
        self.publish_release.wait(timeout=2)
        self.publish_calls += 1
        if self.publish_error is not None:
            raise self.publish_error
        if self.publish_status == "published":
            self.actor_state_path.write_text(
                json.dumps({
                    "schema_version": 1,
                    "actors": {
                        "gale": {"last_success_time": self.publish_at}
                    },
                }),
                encoding="utf-8",
            )
        return SimpleNamespace(
            actor="gale",
            status=self.publish_status,
            reason=self.publish_reason,
        )

    def check_submissions_now(self):
        self.pull_thread = QThread.currentThread()
        self.pull_started.set()
        self.pull_release.wait(timeout=2)
        self.pull_calls += 1
        self.pull_at = "2026-08-10T12:35:00+00:00"
        return object()

    def last_successful_publish_at(self):
        raise AssertionError("Oracle runtime publish status must not be read")

    def last_completed_pull_at(self):
        return self.pull_at

    def auto_publish_enabled(self):
        raise AssertionError("retired Oracle publish setting must not be read")

    def set_auto_publish_enabled(self, value):
        raise AssertionError("retired Oracle publish setting must not be written")

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
        self.actor_state_path = Path(self.temporary.name) / "actor-state.json"
        self.actor_state_path.write_text(
            '{"schema_version":1,"actors":{"gale":{}}}',
            encoding="utf-8",
        )
        self.gale = FakeGaleControl(self.actor_state_path)
        self.host = QMainWindow()
        self.dialog = AiCenterDialog(
            receipts,
            metadata,
            parent=self.host,
            gale=self.gale,
            actor_mirror_state_path=self.actor_state_path,
        )
        self.addCleanup(self._close_dialog)

    def _close_dialog(self) -> None:
        for _attempt in range(100):
            if not self.dialog.gale_tasks.has_dialog_tasks:
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
        self.assertFalse(hasattr(self.dialog, "gale_auto_publish"))
        self.assertEqual(
            self.dialog.gale_last_publish.text(),
            "Gale / GreenCloud 上次成功发布：尚无发布记录",
        )
        self.assertEqual(
            self.dialog.findChild(
                type(self.dialog.gale_status), "galeMirrorHelp"
            ).text(),
            "Gale / GreenCloud 安全镜像由后台自动维护，每约 5 分钟检查一次；"
            "内容无变化时不会重复上传。",
        )
        self.assertEqual(self.dialog.gale_pull_button.text(), "立即检查投稿")
        self.assertEqual(
            self.dialog.gale_last_pull.text(),
            "上次投稿检查（多来源）：尚未检查",
        )
        self.assertEqual(self.dialog.gale_auto_pull.text(), "打开应用时检查投稿")
        self.assertEqual(
            self.dialog.findChild(
                type(self.dialog.gale_status), "galeAutoPullHelp"
            ).text(),
            "仅控制打开应用时的检查；后台已有约 5 分钟检查与回执同步"
            "不受此开关影响。",
        )
        self.assertTrue(self.dialog.gale_auto_pull.isChecked())
        self.dialog.gale_auto_pull.setChecked(False)
        self.assertFalse(self.gale.auto_pull)

        self.gale.pull_release.clear()
        self.dialog.gale_pull_button.click()
        self._wait_for(self.gale.pull_started.is_set)
        self.assertFalse(self.dialog.gale_publish_button.isEnabled())
        self.assertFalse(self.dialog.gale_pull_button.isEnabled())
        self.assertIsNot(self.gale.pull_thread, self.application.thread())
        self.gale.pull_release.set()
        self._wait_for(lambda: self.gale.pull_calls == 1)
        self._wait_for(lambda: not self.dialog.gale_tasks.has_dialog_tasks)
        self.assertTrue(self.dialog.gale_publish_button.isEnabled())
        self.assertTrue(self.dialog.gale_pull_button.isEnabled())
        self.assertRegex(
            self.dialog.gale_last_pull.text(),
            r"^上次投稿检查（多来源）：\d{4}-\d{2}-\d{2} \d{2}:\d{2}$",
        )
        self.assertEqual(self.dialog.gale_status.text(), "操作完成")

    def test_publish_runs_off_main_disables_controls_and_refreshes_status(self) -> None:
        self.gale.publish_release.clear()

        self.dialog.gale_publish_button.click()
        self._wait_for(self.gale.publish_started.is_set)

        self.assertFalse(self.dialog.gale_publish_button.isEnabled())
        self.assertFalse(self.dialog.gale_pull_button.isEnabled())
        self.assertIsNot(self.gale.publish_thread, self.application.thread())
        self.gale.publish_release.set()
        self._wait_for(lambda: self.gale.publish_calls == 1)
        self._wait_for(lambda: not self.dialog.gale_tasks.has_dialog_tasks)

        expected = datetime.fromisoformat(self.gale.publish_at).astimezone().strftime(
            "%Y-%m-%d %H:%M"
        )
        self.assertEqual(
            self.dialog.gale_last_publish.text(),
            f"Gale / GreenCloud 上次成功发布：{expected}",
        )
        self.assertEqual(
            self.dialog.gale_status.text(),
            "Gale / GreenCloud 镜像更新成功",
        )
        self.assertTrue(self.dialog.gale_publish_button.isEnabled())
        self.assertTrue(self.dialog.gale_pull_button.isEnabled())

    def test_publish_unchanged_failed_and_exception_keep_previous_success(self) -> None:
        previous = "2026-08-09T10:20:00+00:00"
        self.actor_state_path.write_text(
            json.dumps({
                "schema_version": 1,
                "actors": {"gale": {"last_success_time": previous}},
            }),
            encoding="utf-8",
        )
        self.dialog.refresh_gale_runtime_status()
        previous_label = self.dialog.gale_last_publish.text()

        self.gale.publish_status = "unchanged"
        self.dialog.gale_publish_button.click()
        self._wait_for(lambda: self.gale.publish_calls == 1)
        self._wait_for(lambda: not self.dialog.gale_tasks.has_dialog_tasks)
        self.assertEqual(self.dialog.gale_status.text(), "内容未变化，已跳过上传")
        self.assertEqual(self.dialog.gale_last_publish.text(), previous_label)

        self.gale.publish_status = "failed"
        self.gale.publish_reason = "transport_unavailable"
        self.dialog.gale_publish_button.click()
        self._wait_for(lambda: self.gale.publish_calls == 2)
        self._wait_for(lambda: not self.dialog.gale_tasks.has_dialog_tasks)
        self.assertEqual(
            self.dialog.gale_status.text(),
            "Gale / GreenCloud 镜像更新失败"
            "（安全错误：transport_unavailable）",
        )
        self.assertEqual(self.dialog.gale_last_publish.text(), previous_label)

        self.gale.publish_error = RuntimeError("FICTIONAL_PRIVATE_EXCEPTION")
        self.dialog.gale_publish_button.click()
        self._wait_for(lambda: self.gale.publish_calls == 3)
        self._wait_for(lambda: not self.dialog.gale_tasks.has_dialog_tasks)
        self.assertEqual(
            self.dialog.gale_status.text(),
            "Gale / GreenCloud 镜像更新失败"
            "（安全错误：unexpected_failure）",
        )
        self.assertNotIn("FICTIONAL_PRIVATE_EXCEPTION", self.dialog.gale_status.text())
        self.assertEqual(self.dialog.gale_last_publish.text(), previous_label)
        self.assertTrue(self.dialog.gale_publish_button.isEnabled())
        self.assertTrue(self.dialog.gale_pull_button.isEnabled())

    def test_publish_status_refresh_never_falls_back_to_oracle_runtime(self) -> None:
        timestamp = "2026-08-10T12:34:00+00:00"
        expected = datetime.fromisoformat(timestamp).astimezone().strftime(
            "%Y-%m-%d %H:%M"
        )
        self.actor_state_path.write_text(
            '{"schema_version":1,"actors":{"gale":{'
            f'"last_success_time":"{timestamp}"'
            "}}}",
            encoding="utf-8",
        )

        self.dialog.refresh_gale_runtime_status()
        self.assertEqual(
            self.dialog.gale_last_publish.text(),
            f"Gale / GreenCloud 上次成功发布：{expected}",
        )

        self.actor_state_path.unlink()
        self.dialog.refresh_gale_runtime_status()
        self.assertEqual(
            self.dialog.gale_last_publish.text(),
            "Gale / GreenCloud 上次成功发布：状态文件不存在",
        )

        self.actor_state_path.write_text("{broken", encoding="utf-8")
        self.dialog.refresh_gale_runtime_status()
        self.assertEqual(
            self.dialog.gale_last_publish.text(),
            "Gale / GreenCloud 上次成功发布：状态读取失败",
        )

        self.actor_state_path.unlink()
        self.actor_state_path.mkdir()
        self.dialog.refresh_gale_runtime_status()
        self.assertEqual(
            self.dialog.gale_last_publish.text(),
            "Gale / GreenCloud 上次成功发布：状态读取失败",
        )

    def test_controls_form_four_row_two_column_responsive_grid(self) -> None:
        grid = self.dialog.gale_controls_layout
        mirror_help = self.dialog.findChild(
            type(self.dialog.gale_status), "galeMirrorHelp"
        )
        auto_pull_help = self.dialog.findChild(
            type(self.dialog.gale_status), "galeAutoPullHelp"
        )
        positions = (
            (self.dialog.gale_publish_button, (0, 0, 1, 1)),
            (self.dialog.gale_last_publish, (0, 1, 1, 1)),
            (mirror_help, (1, 0, 1, 2)),
            (self.dialog.gale_pull_button, (2, 0, 1, 1)),
            (self.dialog.gale_last_pull, (2, 1, 1, 1)),
            (self.dialog.gale_auto_pull, (3, 0, 1, 1)),
            (auto_pull_help, (3, 1, 1, 1)),
        )
        for widget, expected in positions:
            index = grid.indexOf(widget)
            self.assertEqual(grid.getItemPosition(index), expected)

        labels = (
            self.dialog.gale_last_publish,
            mirror_help,
            self.dialog.gale_last_pull,
            auto_pull_help,
        )
        for label in labels:
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
                pairs = (
                    (self.dialog.gale_publish_button, self.dialog.gale_last_publish),
                    (self.dialog.gale_pull_button, self.dialog.gale_last_pull),
                    (self.dialog.gale_auto_pull, auto_pull_help),
                )
                for left, right in pairs:
                    left_center = left.mapTo(
                        self.dialog.gale_page, left.rect().center()
                    ).y()
                    right_center = right.mapTo(
                        self.dialog.gale_page, right.rect().center()
                    ).y()
                    self.assertLessEqual(abs(left_center - right_center), 1)

        self.dialog.resize(720, 520)
        self.application.processEvents()
        for left, right in (
            (self.dialog.gale_publish_button, self.dialog.gale_last_publish),
            (self.dialog.gale_pull_button, self.dialog.gale_last_pull),
            (self.dialog.gale_auto_pull, auto_pull_help),
        ):
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
            self._wait_for(lambda: not self.dialog.gale_tasks.has_dialog_tasks)
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
