"""Focused UI coverage for the compact Reminder table metadata controls."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QStyle,
    QStyleOptionComboBox,
    QWidget,
)

from src.snotes.application.reminder_service import ReminderService
from src.snotes.domain.reminders import REMINDER_ACTIVE, ReminderDraft
from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.ui.reminder_center import ReminderCenter
from src.snotes.ui.theme import apply_theme


class ReminderMetadataChipsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-reminder-metadata-chips-"
        )
        self.addCleanup(self.temporary.cleanup)
        database = Path(self.temporary.name) / "fictional-reminders.db"
        self.service = ReminderService(
            SQLiteReminderRepository(initialize_database(database))
        )
        apply_theme(self.app, "light-slate")
        self.center = ReminderCenter(self.service)
        self.center.resize(1200, 760)
        self.center.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.center.close()
        self.center.deleteLater()
        self.app.processEvents()

    def test_default_metadata_is_two_independent_compact_controls(self) -> None:
        self.assertIsNot(self.center.table_label_combo, self.center.table_ai_combo)
        self.assertEqual(self.center.table_label_combo.currentText(), "未归类")
        self.assertEqual(self.center.table_ai_combo.currentText(), "AI 可见")
        self.assertIs(
            self.center.table_label_combo.parentWidget(), self.center.metadata_chips
        )
        self.assertIs(
            self.center.table_ai_combo.parentWidget(), self.center.metadata_chips
        )
        self.assertIsNone(
            self.center.findChild(QWidget, "reminderMetaSummaryButton")
        )
        self.assertIsNone(self.center.findChild(QWidget, "reminderMetaPanel"))
        self.assertEqual(
            [
                label.text()
                for label in self.center.findChildren(QLabel)
                if label.text() in {"所属标签", "AI 可见性"}
            ],
            [],
        )
        self.assertLessEqual(self.center.metadata_chips.height(), 28)
        self.assertLess(
            self.center.metadata_chips.geometry().bottom(),
            self.center.toolbar.geometry().top(),
        )
        for combo in (
            self.center.table_label_combo,
            self.center.table_ai_combo,
        ):
            option = QStyleOptionComboBox()
            combo.initStyleOption(option)
            edit_field = combo.style().subControlRect(
                QStyle.CC_ComboBox,
                option,
                QStyle.SC_ComboBoxEditField,
                combo,
            )
            self.assertGreaterEqual(
                edit_field.width(),
                combo.fontMetrics().horizontalAdvance(combo.currentText()),
            )

    def test_changes_save_immediately_and_keep_the_selected_reminder(self) -> None:
        work = self.service.create_label("工作")
        table = self.service.create_table("工作提醒")
        reminder = self.service.create_reminder(
            ReminderDraft(
                title="FICTIONAL chip selection",
                lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "one_time", "date": "2099-01-01"},
                table_id=table.id,
            )
        )
        self.center.select_table(table.id)
        self.center.table.setCurrentCell(0, 0)
        self.app.processEvents()

        label_index = self.center.table_label_combo.findData(work.id)
        self.assertGreaterEqual(label_index, 0)
        self.center.table_label_combo.setCurrentIndex(label_index)
        self.app.processEvents()
        saved = self.service.get_table(table.id)
        self.assertIsNotNone(saved)
        self.assertEqual(saved.label.id, work.id)
        self.assertEqual(self.center.table_label_combo.currentText(), "工作")
        self.assertEqual(self.center._selected_id(), reminder.id)

        hidden_index = self.center.table_ai_combo.findData(False)
        self.center.table_ai_combo.setCurrentIndex(hidden_index)
        self.app.processEvents()
        saved = self.service.get_table(table.id)
        self.assertIsNotNone(saved)
        self.assertFalse(saved.table.ai_visible)
        self.assertEqual(self.center.table_ai_combo.currentText(), "AI 不可见")
        self.assertEqual(self.center._selected_id(), reminder.id)

    def test_switching_tables_replaces_both_chip_values_without_stale_state(self) -> None:
        renewal = self.service.create_label("续费")
        categorized = self.service.create_table(
            "续费表", label_id=renewal.id, ai_visible=False
        )
        uncategorized = self.service.create_table("未归类表", ai_visible=True)

        self.center.select_table(categorized.id)
        self.assertEqual(self.center.table_label_combo.currentText(), "续费")
        self.assertEqual(self.center.table_ai_combo.currentText(), "AI 不可见")

        self.center.select_table(uncategorized.id)
        self.assertEqual(self.center.table_label_combo.currentText(), "未归类")
        self.assertEqual(self.center.table_ai_combo.currentText(), "AI 可见")

        self.center.selected_table_id = None
        self.center._load_table_metadata()
        self.center._update_action_states()
        self.assertEqual(self.center.table_label_combo.currentText(), "")
        self.assertEqual(self.center.table_ai_combo.currentText(), "")
        self.assertFalse(self.center.table_label_combo.isEnabled())
        self.assertFalse(self.center.table_ai_combo.isEnabled())


if __name__ == "__main__":
    unittest.main()
