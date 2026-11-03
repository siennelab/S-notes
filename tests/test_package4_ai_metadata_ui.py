from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.snotes.bootstrap import build_application
from src.snotes.reminder_authority import LOCAL_REMINDER_AUTHORITY


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
        _application, self.window = build_application([], data_root=self.data_root, reminder_authority=LOCAL_REMINDER_AUTHORITY)
        self.addCleanup(self.window.close)

    def create_note(self):
        return self.window.note_service.create_note()

    def wait_note(self, note_id: int, timeout: float = 15.0) -> None:
        """The editor commits title/metadata/body together on loadComplete."""
        deadline = time.monotonic() + timeout
        window = self.window
        while not (window.current_note is not None and window.current_note.id == note_id
                   and window.body_edit.can_save) and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertIsNotNone(window.current_note)
        self.assertEqual(window.current_note.id, note_id)
        self.assertTrue(window.body_edit.can_save)

    def load_note(self, note_id: int) -> bool:
        result = self.window.load_note(note_id)
        if result:
            self.wait_note(note_id)
        return result

    def switch_note(self, note_id: int) -> None:
        self.window.switch_note(note_id)
        self.wait_note(note_id)

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

        self.assertTrue(self.load_note(empty.id))
        self.assertEqual(
            self.window.shell.ai_visibility_combo.currentData(),
            "hidden",
        )
        self.assertEqual(self.checked_target_ids(), ())
        self.assertIn("AI对象：无", self.window.shell.metadata_summary_button.text())

        self.assertTrue(self.load_note(configured.id))
        self.assertEqual(
            self.window.shell.ai_visibility_combo.currentData(),
            "redacted",
        )
        self.assertEqual(self.checked_target_ids(), ("common", "gale"))
        self.assertEqual(
            self.window.shell.ai_legacy_targets_label.text(),
            "历史授权：Codex",
        )

    def test_visibility_edit_calls_service_persists_and_syncs_revision(self) -> None:
        note = self.create_note()
        self.load_note(note.id)
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
        self.load_note(note.id)
        wrapped = Mock(wraps=self.window.ai_access_metadata_service)
        self.window.ai_access_metadata_service = wrapped

        for expected in (
            ("common",),
            ("claude-code",),
            ("gale", "clawbot", "claude-code", "ke-den"),
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

        self.load_note(first.id)
        self.assertEqual(self.checked_target_ids(), ("common",))
        self.switch_note(second.id)
        self.assertEqual(
            self.window.shell.ai_visibility_combo.currentData(),
            "redacted",
        )
        self.assertEqual(self.checked_target_ids(), ("ke-den",))
        self.assertEqual(
            self.window.shell.ai_legacy_targets_label.text(),
            "历史授权：Gemini",
        )
        self.switch_note(first.id)
        self.assertEqual(
            self.window.shell.ai_visibility_combo.currentData(),
            "full",
        )
        self.assertEqual(self.checked_target_ids(), ("common",))
        self.assertFalse(self.window.shell.ai_legacy_targets_label.isVisible())

    def test_chooser_has_eight_active_targets_and_preserves_legacy_on_edit(self) -> None:
        self.assertEqual(
            tuple(
                (target_id, checkbox.text())
                for target_id, checkbox in self.window.shell.ai_target_checks.items()
            ),
            (
                ("common", "通用"),
                ("s", "S"),
                ("gale", "Gale"),
                ("yan", "砚"),
                ("clawbot", "Clawbot"),
                ("antigravity", "Antigravity"),
                ("claude-code", "Claude"),
                ("ke-den", "Ke"),
            ),
        )
        note = self.create_note()
        service = self.window.ai_access_metadata_service
        service.set_note_targets(note.id, ("codex",))
        self.load_note(note.id)

        self.window.shell.ai_target_checks["gale"].setChecked(True)
        self.app.processEvents()

        stored = service.get_note_metadata(note.id)
        self.assertEqual(stored.target_ids, ("gale", "codex"))
        self.assertEqual(stored.legacy_target_names, ("Codex",))
        self.assertIn("Codex（历史）", self.window.shell.metadata_summary_button.text())

    def test_failed_write_restores_persisted_control_and_reports_failure(self) -> None:
        note = self.create_note()
        self.load_note(note.id)
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
