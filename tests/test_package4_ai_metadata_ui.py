from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from PySide6.QtWidgets import QApplication

from src.snotes.bootstrap import build_application


class Package4AiMetadataUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-r2-package4-metadata-ui-"
        )
        self.addCleanup(self.temporary.cleanup)
        self.data_root = Path(self.temporary.name) / "data"
        _application, self.window = build_application([], data_root=self.data_root)
        self.addCleanup(self.window.close)

    def create_note(self):
        return self.window.note_service.create_note()

    def checked_target_ids(self) -> tuple[str, ...]:
        return self.window.shell.selected_ai_target_ids()

    def test_load_displays_visibility_targets_and_empty_r1_summary(self) -> None:
        empty = self.create_note()
        configured = self.create_note()
        self.window.ai_access_metadata_service.set_note_visibility(
            configured.id,
            "redacted",
        )
        self.window.ai_access_metadata_service.set_note_targets(
            configured.id,
            ("common", "gale", "codex"),
        )

        self.assertTrue(self.window.load_note(empty.id))
        self.assertEqual(
            self.window.shell.ai_visibility_combo.currentData(),
            "hidden",
        )
        self.assertEqual(self.checked_target_ids(), ())
        self.assertIn("AI对象：无", self.window.shell.metadata_summary_button.text())

        self.assertTrue(self.window.load_note(configured.id))
        self.assertEqual(
            self.window.shell.ai_visibility_combo.currentData(),
            "redacted",
        )
        self.assertEqual(self.checked_target_ids(), ("common", "gale", "codex"))

    def test_visibility_edit_calls_service_persists_and_syncs_revision(self) -> None:
        note = self.create_note()
        self.window.load_note(note.id)
        wrapped = Mock(wraps=self.window.ai_access_metadata_service)
        self.window.ai_access_metadata_service = wrapped

        combo = self.window.shell.ai_visibility_combo
        combo.setCurrentIndex(combo.findData("full"))
        self.app.processEvents()

        wrapped.set_note_visibility.assert_called_once_with(note.id, "full")
        stored = wrapped.get_note_metadata(note.id)
        self.assertEqual(stored.visibility, "full")
        self.assertEqual(self.window.current_note.revision, stored.revision)
        self.assertEqual(combo.currentText(), "AI完整可见")

    def test_target_edits_call_service_and_round_trip_common_single_multi(self) -> None:
        note = self.create_note()
        self.window.load_note(note.id)
        wrapped = Mock(wraps=self.window.ai_access_metadata_service)
        self.window.ai_access_metadata_service = wrapped

        for expected in (
            ("common",),
            ("gale",),
            ("gale", "codex", "gemini"),
        ):
            for target_id, checkbox in self.window.shell.ai_target_checks.items():
                checkbox.setChecked(target_id in expected)
            self.app.processEvents()
            stored = wrapped.get_note_metadata(note.id)
            self.assertEqual(stored.target_ids, expected)
            self.assertEqual(self.checked_target_ids(), expected)

        self.assertGreaterEqual(wrapped.set_note_targets.call_count, 3)

    def test_note_switch_reload_does_not_leak_metadata_between_notes(self) -> None:
        first = self.create_note()
        second = self.create_note()
        service = self.window.ai_access_metadata_service
        service.set_note_visibility(first.id, "full")
        service.set_note_targets(first.id, ("common",))
        service.set_note_visibility(second.id, "redacted")
        service.set_note_targets(second.id, ("ke-den", "gemini"))

        self.window.load_note(first.id)
        self.assertEqual(self.checked_target_ids(), ("common",))
        self.window.switch_note(second.id)
        self.assertEqual(
            self.window.shell.ai_visibility_combo.currentData(),
            "redacted",
        )
        self.assertEqual(self.checked_target_ids(), ("ke-den", "gemini"))
        self.window.switch_note(first.id)
        self.assertEqual(
            self.window.shell.ai_visibility_combo.currentData(),
            "full",
        )
        self.assertEqual(self.checked_target_ids(), ("common",))

    def test_failed_write_restores_persisted_control_and_reports_failure(self) -> None:
        note = self.create_note()
        self.window.load_note(note.id)
        wrapped = Mock(wraps=self.window.ai_access_metadata_service)
        wrapped.set_note_visibility.side_effect = RuntimeError("虚构保存故障")
        self.window.ai_access_metadata_service = wrapped

        combo = self.window.shell.ai_visibility_combo
        combo.setCurrentIndex(combo.findData("full"))
        self.app.processEvents()

        self.assertEqual(combo.currentData(), "hidden")
        self.assertIn("保存失败", self.window.status_label.text())
        self.assertIn("虚构保存故障", self.window.status_label.text())


if __name__ == "__main__":
    unittest.main()
