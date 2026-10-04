from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.snotes.application.note_service import NoteService
from src.snotes.application.reminder_service import ReminderService
from src.snotes.bootstrap import build_application
from src.snotes.domain.models import NoteLocation
from src.snotes.domain.reminders import ReminderDraft
from src.snotes.reminder_authority import REMOTE, ReminderAuthority
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.transport.reminder_http import (
    OWNER_ROLE,
    ReminderCredential,
    ReminderHttpApplication,
)
from tests.test_gc35_step07_gui import Client


class Step08ReminderNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    @classmethod
    def tearDownClass(cls) -> None:
        QTimer.singleShot(0, cls.app.quit)
        cls.app.exec()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-step08-navigation-"
        )
        self.root = Path(self.temporary.name)
        self.app_data = self.root / "app-data"
        app_connections = initialize_database(self.app_data / "s_notes.db")
        directories = SQLiteDirectoriesRepository(app_connections)
        notes = NoteService(SQLiteNotesRepository(app_connections, directories))
        self.first_note = notes.create_note(NoteLocation())
        self.first_note = notes.save_note(
            self.first_note.id,
            title="FICTIONAL Step 8 Note A",
            content_markdown="<p>FICTIONAL cold note body</p>",
            location=self.first_note.location,
            expected_revision=self.first_note.revision,
        )
        self.second_note = notes.create_note(NoteLocation())
        self.second_note = notes.save_note(
            self.second_note.id,
            title="FICTIONAL Step 8 Note B",
            content_markdown="<p>FICTIONAL warm note body</p>",
            location=self.second_note.location,
            expected_revision=self.second_note.revision,
        )
        cloud_connections = initialize_database(self.root / "cloud-fixture.db")
        self.cloud_service = ReminderService(
            SQLiteReminderRepository(cloud_connections)
        )
        self.label = self.cloud_service.create_label("FICTIONAL Step 8 label")
        self.table = self.cloud_service.create_table(
            "FICTIONAL Step 8 table",
            label_id=self.label.id,
            ai_visible=False,
        )
        self.cloud_service.create_reminder(
            ReminderDraft(
                "FICTIONAL Step 8 reminder",
                "active",
                {"type": "one_time", "date": "2099-01-01"},
                table_id=self.table.id,
            )
        )
        api = ReminderHttpApplication(
            self.cloud_service,
            {"fictional": ReminderCredential("owner", OWNER_ROLE)},
        )
        self.client = Client(api)
        authority = ReminderAuthority(
            REMOTE,
            "https://reminder.invalid",
            helper_retired=True,
        )
        with (
            patch(
                "src.snotes.transport.remote_reminder_client.RemoteReminderClient",
                return_value=self.client,
            ),
            patch("src.snotes.ui.reminder_legacy_check.start_legacy_check"),
        ):
            _application, self.window = build_application(
                [],
                data_root=self.app_data,
                reminder_authority=authority,
            )
        self.controller = self.window.remote_reminder_controller

    def tearDown(self) -> None:
        self.controller.begin_shutdown()
        self.wait_until(lambda: not self.controller._threads)
        self.window.receipt_refresh_timer.stop()
        self.window.shell.sidebar.clock_timer.stop()
        self.window.hide()
        self.window.deleteLater()
        self.app.processEvents()
        self.temporary.cleanup()

    def wait_until(self, condition, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(3)
        self.assertTrue(condition())

    def wait_for_refresh_after(self, call_count: int) -> None:
        self.wait_until(
            lambda: len(self.client.calls) > call_count
            and not self.controller.pending
            and self.controller.state == "online"
        )

    def assert_notes_surface(self) -> None:
        sidebar = self.window.shell.sidebar
        self.assertEqual(
            (self.window.current_mode, self.window.current_filter),
            ("directory", ""),
        )
        self.assertEqual(sidebar.mode, "directory")
        self.assertFalse(sidebar.smart_buttons["reminder"].isChecked())
        self.assertIs(
            self.window.shell.content_stack.currentWidget(),
            self.window.shell.main_content,
        )

    def click_note(self, note_id: int) -> None:
        note_list = self.window.shell.sidebar.note_list
        item = next(
            (
                note_list.item(row)
                for row in range(note_list.count())
                if note_list.item(row).data(Qt.UserRole) == note_id
            ),
            None,
        )
        self.assertIsNotNone(item)
        rectangle = note_list.visualItemRect(item)
        self.assertTrue(rectangle.isValid())
        QTest.mouseClick(
            note_list.viewport(),
            Qt.LeftButton,
            pos=rectangle.center(),
        )
        self.wait_until(
            lambda: self.window.current_note is not None
            and self.window.current_note.id == note_id
            and self.window.body_edit.can_save,
            timeout=15,
        )

    def test_remote_snapshots_preserve_notes_and_manual_reminder_navigation(self) -> None:
        sidebar = self.window.shell.sidebar
        center = self.window.shell.reminder_center
        self.assertIsNotNone(center)
        self.window.resize(1420, 880)
        self.window.show()
        self.app.processEvents()

        # The first Cloud snapshot is still in flight while startup renders Notes.
        self.assertTrue(self.controller.pending)
        self.assert_notes_surface()
        self.wait_until(
            lambda: self.controller.state == "online"
            and not self.controller.pending
        )
        self.assert_notes_surface()
        self.click_note(self.first_note.id)
        self.assertEqual(self.window.title_input.text(), self.first_note.title)

        # A deliberate click is the only transition into the Reminder surface.
        QTest.mouseClick(sidebar.smart_buttons["reminder"], Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(
            (self.window.current_mode, self.window.current_filter),
            ("smart_view", "reminder"),
        )
        self.assertEqual(sidebar.mode, "reminder")
        self.assertIs(self.window.shell.content_stack.currentWidget(), center)
        self.assertGreaterEqual(sidebar.main_filter_combo.findData(self.label.id), 0)
        self.assertIn(
            self.table.id,
            {
                sidebar.note_list.item(index).data(Qt.UserRole)
                for index in range(sidebar.note_list.count())
            },
        )
        self.assertTrue(sidebar._reminder_writable)
        self.assertTrue(sidebar.new_note_button.isEnabled())

        # Labels, tables, and filters continue to synchronize in Reminder mode.
        sidebar.main_filter_combo.setCurrentIndex(
            sidebar.main_filter_combo.findData(self.label.id)
        )
        sidebar.sub_filter_combo.setCurrentIndex(
            sidebar.sub_filter_combo.findData("hidden")
        )
        self.app.processEvents()
        self.assertEqual(center.label_id, self.label.id)
        self.assertEqual(center.ai_visibility, "hidden")
        self.assertEqual(sidebar.note_list.count(), 1)

        # Remote pending/read-only and online/writable states still reach Sidebar.
        calls = len(self.client.calls)
        self.controller.refresh()
        self.assertTrue(self.controller.pending)
        self.assertFalse(sidebar._reminder_writable)
        self.assertFalse(sidebar.new_note_button.isEnabled())
        self.wait_for_refresh_after(calls)
        self.assertTrue(sidebar._reminder_writable)
        self.assertTrue(sidebar.new_note_button.isEnabled())

        # A real offline transition keeps the active Reminder surface read-only;
        # a successful snapshot restores both Sidebar and center write controls.
        self.client.error = "offline"
        calls = len(self.client.calls)
        self.controller.refresh()
        self.wait_until(
            lambda: len(self.client.calls) > calls
            and not self.controller.pending
            and self.controller.state == "offline"
        )
        self.assertFalse(sidebar._reminder_writable)
        self.assertFalse(sidebar.new_note_button.isEnabled())
        self.assertFalse(center.new_button.isEnabled())
        self.client.error = None
        calls = len(self.client.calls)
        self.controller.refresh()
        self.wait_for_refresh_after(calls)
        self.assertTrue(sidebar._reminder_writable)
        self.assertTrue(sidebar.new_note_button.isEnabled())
        self.assertTrue(center.new_button.isEnabled())

        # Clicking the active Reminder tab returns to Notes. A later snapshot
        # updates remote state and badge data without changing navigation.
        QTest.mouseClick(sidebar.smart_buttons["reminder"], Qt.LeftButton)
        self.app.processEvents()
        self.assert_notes_surface()
        self.click_note(self.second_note.id)
        self.assertEqual(self.window.title_input.text(), self.second_note.title)
        snapshot = self.client.snapshot

        def snapshot_with_badge():
            payload = snapshot()
            payload["badge_count"] = 1
            return payload

        self.client.snapshot = snapshot_with_badge
        calls = len(self.client.calls)
        self.controller.refresh()
        self.assertTrue(self.controller.pending)
        self.assertFalse(sidebar._reminder_writable)
        self.assertTrue(sidebar.new_note_button.isEnabled())
        self.wait_for_refresh_after(calls)
        self.assertEqual(self.controller.badge_count(), 1)
        self.assertEqual(sidebar.smart_buttons["reminder"].text(), "提醒 (1)")
        self.assertTrue(sidebar._reminder_writable)
        self.assertTrue(sidebar.new_note_button.isEnabled())
        self.assert_notes_surface()
        self.click_note(self.first_note.id)
        self.assertEqual(self.window.title_input.text(), self.first_note.title)

    def test_background_snapshot_preserves_other_navigation_modes(self) -> None:
        self.wait_until(
            lambda: self.controller.state == "online"
            and not self.controller.pending
        )
        cases = (
            ("search", lambda: self.window.show_search("FICTIONAL query")),
            ("tag", lambda: self.window.show_tag("fictional-tag")),
            ("smart_view", lambda: self.window.show_smart_view("todo")),
        )
        for expected_mode, navigate in cases:
            with self.subTest(mode=expected_mode):
                self.window.show_directory()
                navigate()
                self.app.processEvents()
                sidebar = self.window.shell.sidebar
                before = (
                    self.window.current_mode,
                    self.window.current_filter,
                    sidebar.mode,
                    self.window.shell.content_stack.currentWidget(),
                )
                self.assertEqual(self.window.current_mode, expected_mode)
                self.assertIs(
                    self.window.shell.content_stack.currentWidget(),
                    self.window.shell.main_content,
                )

                calls = len(self.client.calls)
                self.controller.refresh()
                self.wait_for_refresh_after(calls)

                self.assertEqual(
                    (
                        self.window.current_mode,
                        self.window.current_filter,
                        sidebar.mode,
                        self.window.shell.content_stack.currentWidget(),
                    ),
                    before,
                )
                self.assertFalse(sidebar.smart_buttons["reminder"].isChecked())


if __name__ == "__main__":
    unittest.main()
