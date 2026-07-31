from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QWidget

from src.ai_center_dialog import AiCenterDialog
from src.receipt_inbox_dialog import ReceiptCard, ReceiptInboxDialog
from src.database import (
    _connect,
    accept_ai_receipt_inbox_item,
    create_gale_submission,
    get_ai_receipt_inbox_item,
    initialize_database,
    list_ai_receipt_inbox_items,
    reject_ai_receipt_inbox_item,
)
from src.services.ai_access_service import (
    CREATE_CONFIRMATION_KEY,
    MODIFY_CONFIRMATION_KEY,
)
from src.theme import THEMES, apply_theme, build_app_style


class AiCenterUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def build_dialog(self, **kwargs) -> AiCenterDialog:
        patches = (
            patch("src.ai_center_dialog.list_ai_note_history", return_value=[]),
            patch("src.ai_center_dialog.list_ai_call_logs", return_value=[]),
            patch(
                "src.ai_center_dialog.get_ai_settings",
                return_value={
                    CREATE_CONFIRMATION_KEY: False,
                    MODIFY_CONFIRMATION_KEY: True,
                },
            ),
            patch(
                "src.receipt_inbox_dialog.list_ai_receipt_inbox_items",
                return_value=[],
            ),
        )
        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)
        return AiCenterDialog(**kwargs)

    def test_settings_contains_remote_ai_access_without_adding_a_tab(self) -> None:
        dialog = self.build_dialog()
        self.assertEqual(dialog.tabs.count(), 4)
        self.assertEqual(
            [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())],
            ["待确认", "修改历史", "调用记录", "设置"],
        )
        remote_group = dialog.findChild(QWidget, "remoteAiAccessGroup")
        self.assertIsNotNone(remote_group)
        margins = remote_group.layout().contentsMargins()
        self.assertEqual(
            (margins.left(), margins.top(), margins.right(), margins.bottom()),
            (0, 0, 0, 0),
        )
        self.assertFalse(dialog.remote_auto_update.isChecked())
        self.assertEqual(dialog.remote_auto_update.text(), "自动更新")
        self.assertEqual(dialog.remote_update_now.text(), "立即更新")
        auto_update_controls = [
            child
            for child in remote_group.findChildren(QCheckBox)
            if child.text() == "自动更新"
        ]
        self.assertEqual(auto_update_controls, [dialog.remote_auto_update])
        self.assertEqual(
            remote_group.layout().indexOf(dialog.remote_auto_update),
            1,
        )
        self.assertEqual(dialog.remote_title.objectName(), "settingsSectionTitle")
        self.assertEqual(dialog.confirmation_note.objectName(), "settingsHelpText")
        self.assertEqual(dialog.remote_note.objectName(), "settingsHelpText")
        labels = [
            child.text()
            for child in dialog.findChildren(QWidget)
            if hasattr(child, "text")
        ]
        self.assertIn("远程 AI 访问", labels)
        self.assertIn("仅更新符合 AI 对象与 AI 可见性规则的内容", labels)

    def test_remote_controls_call_injected_existing_controllers(self) -> None:
        set_auto = unittest.mock.Mock()
        publish_now = unittest.mock.Mock()
        dialog = self.build_dialog(
            set_auto_publish_enabled=set_auto,
            publish_now=publish_now,
        )
        dialog.remote_auto_update.setChecked(True)
        dialog.remote_update_now.click()
        set_auto.assert_called_once_with(True)
        publish_now.assert_called_once_with()

    def test_primary_tabs_fill_the_row_in_four_equal_responsive_sections(self) -> None:
        apply_theme(self.app, "dark")
        for window_width in (720, 901, 1123):
            dialog = self.build_dialog()
            dialog.resize(window_width, 700)
            dialog.show()
            self.app.processEvents()
            tab_bar = dialog.tabs.tabBar()
            widths = [tab_bar.tabRect(index).width() for index in range(4)]
            self.assertEqual(widths, [widths[0]] * 4)
            self.assertEqual(tab_bar.tabRect(0).left(), 0)
            self.assertEqual(tab_bar.tabRect(3).right(), tab_bar.width() - 1)
            dialog.close()
            self.app.processEvents()

    def test_all_three_themes_cover_ai_center_window_tabs_and_close_button(self) -> None:
        for theme_name, tokens in THEMES.items():
            style = build_app_style(theme_name)
            self.assertIn("QWidget#aiCenterDialog", style)
            self.assertIn("QTabWidget#aiCenterTabs::pane", style)
            pane_style = style.split(
                "QTabWidget#aiCenterTabs::pane {", 1
            )[1].split("}", 1)[0]
            self.assertIn("border: 0;", pane_style)
            self.assertIn("top: 0;", pane_style)
            self.assertNotIn(tokens.sidebar_separator, pane_style)
            self.assertNotIn("#remoteAiAccessGroup {", style)
            self.assertIn(
                f"#settingsSectionTitle {{ color: {tokens.text};",
                style,
            )
            self.assertIn(
                f"#settingsHelpText {{ color: {tokens.text_subtle};",
                style,
            )
            self.assertIn("#aiCenterCloseButton", style)
            self.assertIn(f"background: {tokens.window};", style)
            self.assertIn(f"color: {tokens.text};", style)
            self.assertIn(f"color: {tokens.disabled};", style)

            apply_theme(self.app, theme_name)
            dialog = self.build_dialog()
            dialog.resize(900, 700)
            dialog.show()
            self.app.processEvents()
            self.assertEqual(dialog.objectName(), "aiCenterDialog")
            self.assertEqual(dialog.close_button.objectName(), "aiCenterCloseButton")
            self.assertEqual(dialog.tabs.tabBar().count(), 4)
            dialog.close()
            self.app.processEvents()


class AiCenterSubmissionRefreshTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="snotes-ai-center-refresh-",
            dir="/private/tmp",
        )
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.database_path = self.root / "s_notes.db"
        initialize_database(self.database_path)
        self.dialogs: list[AiCenterDialog] = []

        environment_patch = patch.dict(
            os.environ,
            {
                "HOME": str(self.home),
                "S_NOTES_DATA_ROOT": str(self.root),
                "QT_QPA_PLATFORM": "offscreen",
            },
        )
        environment_patch.start()
        self.addCleanup(environment_patch.stop)

        def list_receipts(status: str | None = "pending") -> list[dict]:
            return list_ai_receipt_inbox_items(status, self.database_path)

        def get_receipt(receipt_id: int) -> dict | None:
            return get_ai_receipt_inbox_item(receipt_id, self.database_path)

        def accept_receipt(
            receipt_id: int,
            fields: dict,
            *,
            allow_failed_parse: bool = False,
        ) -> dict:
            return accept_ai_receipt_inbox_item(
                receipt_id,
                fields,
                self.database_path,
                allow_failed_parse=allow_failed_parse,
            )

        def reject_receipt(receipt_id: int) -> dict:
            return reject_ai_receipt_inbox_item(receipt_id, self.database_path)

        patches = (
            patch("src.ai_center_dialog.list_ai_note_history", return_value=[]),
            patch("src.ai_center_dialog.list_ai_call_logs", return_value=[]),
            patch(
                "src.ai_center_dialog.get_ai_settings",
                return_value={
                    CREATE_CONFIRMATION_KEY: False,
                    MODIFY_CONFIRMATION_KEY: True,
                },
            ),
            patch(
                "src.receipt_inbox_dialog.list_ai_receipt_inbox_items",
                side_effect=list_receipts,
            ),
            patch(
                "src.receipt_inbox_dialog.get_ai_receipt_inbox_item",
                side_effect=get_receipt,
            ),
            patch(
                "src.receipt_inbox_dialog.accept_ai_receipt_inbox_item",
                side_effect=accept_receipt,
            ),
            patch(
                "src.receipt_inbox_dialog.reject_ai_receipt_inbox_item",
                side_effect=reject_receipt,
            ),
            patch(
                "src.receipt_inbox_dialog._connect",
                new=lambda: _connect(self.database_path),
            ),
        )
        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)

    def tearDown(self) -> None:
        for dialog in reversed(self.dialogs):
            try:
                dialog.close()
            except RuntimeError:
                pass
        self.app.processEvents()

    def _submission(self, suffix: str) -> dict:
        return create_gale_submission(
            external_submission_id=f"FICTIONAL_AI_CENTER_REFRESH_{suffix}",
            idempotency_key=f"fictional-ai-center-refresh-{suffix}",
            title=f"Fictional AI Center refresh {suffix}",
            body=f"Fictional isolated refresh body {suffix}.",
            main_category="Fictional",
            sub_category="AI Center",
            sub_sub_category="Refresh",
            database_path=self.database_path,
        )

    def _build_dialog(self) -> AiCenterDialog:
        dialog = AiCenterDialog()
        self.dialogs.append(dialog)
        dialog.show()
        self.app.processEvents()
        return dialog

    def _status_count(self, status: str) -> int:
        return len(list_ai_receipt_inbox_items(status, self.database_path))

    def _note_count(self) -> int:
        with _connect(self.database_path) as connection:
            return int(connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0])

    def _click_filter(self, dialog: AiCenterDialog, value: str) -> None:
        button = (
            dialog.pending_page.pending_filter_button
            if value == "pending"
            else dialog.pending_page.processed_filter_button
        )
        QTest.mouseClick(button, Qt.LeftButton)

    def _lifecycle_spies(
        self, dialog: AiCenterDialog
    ) -> tuple[QSignalSpy, QSignalSpy]:
        return QSignalSpy(dialog.finished), QSignalSpy(dialog.destroyed)

    def _assert_open_without_lifecycle_signals(
        self,
        dialog: AiCenterDialog,
        finished: QSignalSpy,
        destroyed: QSignalSpy,
        baseline_result: object,
    ) -> None:
        self.assertTrue(dialog.isVisible())
        self.assertEqual(finished.count(), 0)
        self.assertEqual(destroyed.count(), 0)
        self.assertEqual(dialog.property("result"), baseline_result)
        self.assertFalse(hasattr(dialog, "accepted"))
        self.assertFalse(hasattr(dialog, "rejected"))

    def _build_hosted_dialog(self) -> AiCenterDialog:
        host = QWidget()
        host.setWindowTitle("Fictional AI Center host")
        host.show()
        self.app.processEvents()
        self.addCleanup(host.close)
        dialog = AiCenterDialog(host)
        self.dialogs.append(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        self.app.processEvents()
        return dialog

    def _click_top_tab(self, dialog: AiCenterDialog, index: int) -> None:
        tab_bar = dialog.tabs.tabBar()
        QTest.mouseClick(
            tab_bar,
            Qt.LeftButton,
            Qt.NoModifier,
            tab_bar.tabRect(index).center(),
        )
        self.app.processEvents()

    def _assert_top_tab_stays_front(
        self,
        dialog: AiCenterDialog,
        finished: QSignalSpy,
        destroyed: QSignalSpy,
        baseline_result: object,
        pending_identity: int,
    ) -> None:
        self.assertTrue(dialog.isVisible())
        self.assertFalse(dialog.isHidden())
        self.assertTrue(dialog.isActiveWindow())
        self.assertIs(QApplication.activeWindow(), dialog)
        self.assertEqual(dialog.tabs.currentIndex(), 0)
        self.assertEqual(finished.count(), 0)
        self.assertEqual(destroyed.count(), 0)
        self.assertEqual(dialog.property("result"), baseline_result)
        self.assertFalse(hasattr(dialog, "accepted"))
        self.assertFalse(hasattr(dialog, "rejected"))
        self.assertEqual(id(dialog.pending_page), pending_identity)
        self.assertEqual(
            len(dialog.findChildren(ReceiptInboxDialog)),
            1,
        )
        self.assertEqual(
            len(dialog.pending_page.cards),
            len(set(dialog.pending_page.cards)),
        )
        rendered_cards = dialog.pending_page.findChildren(ReceiptCard)
        self.assertEqual(len(rendered_cards), len(dialog.pending_page.cards))
        self.assertEqual(
            {id(card) for card in rendered_cards},
            {id(card) for card in dialog.pending_page.cards.values()},
        )

    def test_each_top_tab_returns_to_receipt_without_deactivating(self) -> None:
        self._submission("TOP_TAB_RETURN")
        dialog = self._build_hosted_dialog()
        finished, destroyed = self._lifecycle_spies(dialog)
        baseline_result = dialog.property("result")
        pending_identity = id(dialog.pending_page)

        for source_index, source_name in (
            (1, "history"),
            (2, "log"),
            (3, "settings"),
        ):
            with self.subTest(source=source_name):
                dialog.raise_()
                dialog.activateWindow()
                self.app.processEvents()
                self._click_top_tab(dialog, source_index)
                self.assertEqual(dialog.tabs.currentIndex(), source_index)
                self.assertTrue(dialog.isVisible())
                self.assertTrue(dialog.isActiveWindow())
                self._click_top_tab(dialog, 0)
                self._assert_top_tab_stays_front(
                    dialog,
                    finished,
                    destroyed,
                    baseline_result,
                    pending_identity,
                )

    def test_top_tab_cycle_ten_rounds_has_one_refresh_per_return(self) -> None:
        submission = self._submission("TOP_TAB_TEN_CYCLES")
        dialog = self._build_hosted_dialog()
        page = dialog.pending_page
        finished, destroyed = self._lifecycle_spies(dialog)
        changed = QSignalSpy(dialog.tabs.currentChanged)
        baseline_result = dialog.property("result")
        pending_identity = id(page)
        refresh_count = 0
        original_refresh = page.refresh_receipt_list

        def counted_refresh() -> None:
            nonlocal refresh_count
            refresh_count += 1
            original_refresh()

        page.refresh_receipt_list = counted_refresh
        with patch("src.ai_center_dialog.QMessageBox.warning") as warning:
            for _round in range(10):
                for index in (1, 0, 2, 0, 3, 0):
                    self._click_top_tab(dialog, index)
                    self.assertTrue(dialog.isVisible())
                    self.assertFalse(dialog.isHidden())
                    self.assertTrue(dialog.isActiveWindow())
                    if index == 0:
                        self._assert_top_tab_stays_front(
                            dialog,
                            finished,
                            destroyed,
                            baseline_result,
                            pending_identity,
                        )

        warning.assert_not_called()
        self.assertEqual(changed.count(), 60)
        self.assertEqual(refresh_count, 30)
        self.assertEqual(set(page.cards), {submission["id"]})
        self.assertEqual(len(page.cards), 1)

    def test_accept_and_discard_survive_top_tab_returns(self) -> None:
        discard_submission = self._submission("TOP_TAB_DISCARD")
        accept_submission = self._submission("TOP_TAB_ACCEPT")
        dialog = self._build_hosted_dialog()
        page = dialog.pending_page
        finished, destroyed = self._lifecycle_spies(dialog)
        baseline_result = dialog.property("result")
        pending_identity = id(page)

        with patch("src.receipt_inbox_dialog.QMessageBox.information") as information:
            QTest.mouseClick(
                page.cards[discard_submission["id"]].reject_button,
                Qt.LeftButton,
            )
            self.app.processEvents()
            self._click_top_tab(dialog, 1)
            self._click_top_tab(dialog, 0)
            self._assert_top_tab_stays_front(
                dialog,
                finished,
                destroyed,
                baseline_result,
                pending_identity,
            )

            QTest.mouseClick(
                page.cards[accept_submission["id"]].accept_button,
                Qt.LeftButton,
            )
            self.app.processEvents()
            self._click_filter(dialog, "processed")
            self.app.processEvents()
            self._click_top_tab(dialog, 2)
            self._click_top_tab(dialog, 0)
            self._assert_top_tab_stays_front(
                dialog,
                finished,
                destroyed,
                baseline_result,
                pending_identity,
            )

        information.assert_not_called()
        self.assertEqual(self._status_count("pending"), 0)
        self.assertEqual(self._status_count("discarded"), 1)
        self.assertEqual(self._status_count("accepted"), 1)
        self.assertEqual(self._note_count(), 1)
        self.assertEqual(
            set(page.cards),
            {discard_submission["id"], accept_submission["id"]},
        )
        self.assertEqual(len(page.cards), 2)

    def test_receipt_refresh_failure_keeps_top_window_and_existing_page(self) -> None:
        submission = self._submission("TOP_TAB_REFRESH_FAILURE")
        dialog = self._build_hosted_dialog()
        page = dialog.pending_page
        finished, destroyed = self._lifecycle_spies(dialog)
        baseline_result = dialog.property("result")
        pending_identity = id(page)
        original_cards = set(page.cards)
        self._click_top_tab(dialog, 1)

        with patch(
            "src.receipt_inbox_dialog.list_ai_receipt_inbox_items",
            side_effect=RuntimeError("forced receipt refresh failure"),
        ), patch("src.ai_center_dialog.QMessageBox.warning") as warning:
            self._click_top_tab(dialog, 0)

        warning.assert_called_once()
        self._assert_top_tab_stays_front(
            dialog,
            finished,
            destroyed,
            baseline_result,
            pending_identity,
        )
        self.assertEqual(original_cards, {submission["id"]})
        self.assertEqual(set(page.cards), original_cards)
        self.assertEqual(self._status_count("pending"), 1)

    def test_filter_switches_five_times_without_closing_ai_center(self) -> None:
        submission = self._submission("FILTER_LIFECYCLE")
        dialog = self._build_dialog()
        page = dialog.pending_page
        finished, destroyed = self._lifecycle_spies(dialog)
        baseline_result = dialog.property("result")

        for value in ("processed", "pending", "processed", "pending", "processed"):
            self._click_filter(dialog, value)
            self.app.processEvents()
            self._assert_open_without_lifecycle_signals(
                dialog, finished, destroyed, baseline_result
            )
            self.assertEqual(page.list_filter, value)

        self.assertEqual(page.cards, {})
        self._click_filter(dialog, "pending")
        self.app.processEvents()
        self.assertEqual(set(page.cards), {submission["id"]})
        self._assert_open_without_lifecycle_signals(
            dialog, finished, destroyed, baseline_result
        )

    def test_discard_has_no_success_box_and_keeps_ai_center_open(self) -> None:
        submission = self._submission("DISCARD_LIFECYCLE")
        dialog = self._build_dialog()
        page = dialog.pending_page
        finished, destroyed = self._lifecycle_spies(dialog)
        baseline_result = dialog.property("result")

        with patch("src.receipt_inbox_dialog.QMessageBox.information") as information:
            QTest.mouseClick(page.cards[submission["id"]].reject_button, Qt.LeftButton)
            self.app.processEvents()

        information.assert_not_called()
        self._assert_open_without_lifecycle_signals(
            dialog, finished, destroyed, baseline_result
        )
        self.assertEqual(self._status_count("pending"), 0)
        self.assertEqual(self._status_count("discarded"), 1)
        self._click_filter(dialog, "processed")
        self.app.processEvents()
        self.assertEqual(set(page.cards), {submission["id"]})
        self.assertEqual(len(page.cards), 1)

    def test_accept_has_no_success_box_and_keeps_ai_center_open(self) -> None:
        submission = self._submission("ACCEPT_LIFECYCLE")
        dialog = self._build_dialog()
        page = dialog.pending_page
        finished, destroyed = self._lifecycle_spies(dialog)
        baseline_result = dialog.property("result")

        with patch("src.receipt_inbox_dialog.QMessageBox.information") as information:
            QTest.mouseClick(page.cards[submission["id"]].accept_button, Qt.LeftButton)
            self.app.processEvents()

        information.assert_not_called()
        self._assert_open_without_lifecycle_signals(
            dialog, finished, destroyed, baseline_result
        )
        self.assertEqual(self._status_count("pending"), 0)
        self.assertEqual(self._status_count("accepted"), 1)
        self.assertEqual(self._note_count(), 1)
        self._click_filter(dialog, "processed")
        self.app.processEvents()
        self.assertEqual(set(page.cards), {submission["id"]})
        self.assertEqual(len(page.cards), 1)
        self.assertEqual(self._note_count(), 1)

    def test_failures_keep_ai_center_open_without_fake_state(self) -> None:
        submission = self._submission("FAILURE_LIFECYCLE")
        dialog = self._build_dialog()
        page = dialog.pending_page
        finished, destroyed = self._lifecycle_spies(dialog)
        baseline_result = dialog.property("result")

        with patch(
            "src.receipt_inbox_dialog.reject_ai_receipt_inbox_item",
            side_effect=RuntimeError("forced discard failure"),
        ), patch("src.receipt_inbox_dialog.QMessageBox.warning") as warning:
            QTest.mouseClick(page.cards[submission["id"]].reject_button, Qt.LeftButton)
            self.app.processEvents()
        warning.assert_called_once()
        self._assert_open_without_lifecycle_signals(
            dialog, finished, destroyed, baseline_result
        )
        self.assertEqual(self._status_count("pending"), 1)
        self.assertEqual(self._status_count("discarded"), 0)
        self.assertEqual(set(page.cards), {submission["id"]})

        with patch(
            "src.receipt_inbox_dialog.accept_ai_receipt_inbox_item",
            side_effect=RuntimeError("forced acceptance failure"),
        ), patch("src.receipt_inbox_dialog.QMessageBox.warning") as warning:
            QTest.mouseClick(page.cards[submission["id"]].accept_button, Qt.LeftButton)
            self.app.processEvents()
        warning.assert_called_once()
        self._assert_open_without_lifecycle_signals(
            dialog, finished, destroyed, baseline_result
        )
        self.assertEqual(self._status_count("pending"), 1)
        self.assertEqual(self._status_count("accepted"), 0)
        self.assertEqual(self._note_count(), 0)
        self.assertEqual(set(page.cards), {submission["id"]})

    def test_close_button_finishes_once_and_reopens_on_default_filter(self) -> None:
        self._submission("CLOSE_LIFECYCLE")
        dialog = self._build_dialog()
        finished = QSignalSpy(dialog.finished)

        QTest.mouseClick(dialog.close_button, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(finished.count(), 1)

        reopened = self._build_dialog()
        self.assertEqual(reopened.pending_page.list_filter, "pending")
        self.assertEqual(len(reopened.pending_page.cards), 1)

    def test_discard_refreshes_processed_in_same_window_and_reopens_once(self) -> None:
        submission = self._submission("DISCARD")
        dialog = self._build_dialog()
        page = dialog.pending_page
        self.assertEqual(page.pending_filter_button.text(), "待确认（1）")
        self.assertEqual(set(page.cards), {submission["id"]})

        self._click_filter(dialog, "processed")
        self.assertEqual(page.cards, {})
        self._click_filter(dialog, "pending")
        self.assertEqual(set(page.cards), {submission["id"]})

        with patch("src.receipt_inbox_dialog.QMessageBox.information"):
            QTest.mouseClick(page.cards[submission["id"]].reject_button, Qt.LeftButton)
        self.assertEqual(self._status_count("pending"), 0)
        self.assertEqual(self._status_count("discarded"), 1)
        self.assertEqual(page.pending_filter_button.text(), "待确认（0）")
        self.assertEqual(page.cards, {})

        self._click_filter(dialog, "processed")
        self.assertEqual(set(page.cards), {submission["id"]})
        self.assertEqual(len(page.cards), 1)
        self._click_filter(dialog, "pending")
        self.assertEqual(page.cards, {})
        self._click_filter(dialog, "processed")
        self.assertEqual(set(page.cards), {submission["id"]})

        dialog.close()
        self.app.processEvents()
        reopened = self._build_dialog()
        self._click_filter(reopened, "processed")
        self.assertEqual(set(reopened.pending_page.cards), {submission["id"]})
        self.assertEqual(len(reopened.pending_page.cards), 1)

        reopened.pending_page._delete_receipt_record(submission["id"])
        reopened.pending_page.refresh_receipt_list()
        self.assertEqual(reopened.pending_page.cards, {})
        self.assertEqual(list_ai_receipt_inbox_items(None, self.database_path), [])

    def test_accept_refreshes_processed_without_duplicate_note_or_card(self) -> None:
        submission = self._submission("ACCEPT")
        dialog = self._build_dialog()
        page = dialog.pending_page
        self.assertEqual(self._note_count(), 0)
        self.assertEqual(set(page.cards), {submission["id"]})

        with patch("src.receipt_inbox_dialog.QMessageBox.information"):
            QTest.mouseClick(page.cards[submission["id"]].accept_button, Qt.LeftButton)
        self.assertEqual(self._status_count("pending"), 0)
        self.assertEqual(self._status_count("accepted"), 1)
        self.assertEqual(self._note_count(), 1)
        self.assertEqual(page.pending_filter_button.text(), "待确认（0）")
        self.assertEqual(page.cards, {})

        self._click_filter(dialog, "processed")
        self.assertEqual(set(page.cards), {submission["id"]})
        self._click_filter(dialog, "pending")
        self.assertEqual(page.cards, {})
        self._click_filter(dialog, "processed")
        self.assertEqual(set(page.cards), {submission["id"]})
        self.assertEqual(self._note_count(), 1)

        dialog.close()
        self.app.processEvents()
        reopened = self._build_dialog()
        self._click_filter(reopened, "processed")
        self.assertEqual(set(reopened.pending_page.cards), {submission["id"]})
        self.assertEqual(len(reopened.pending_page.cards), 1)
        self.assertEqual(self._note_count(), 1)

    def test_failed_accept_and_discard_keep_pending_ui_and_database_state(self) -> None:
        submission = self._submission("FAILURE")
        dialog = self._build_dialog()
        page = dialog.pending_page

        with patch(
            "src.receipt_inbox_dialog.reject_ai_receipt_inbox_item",
            side_effect=RuntimeError("forced discard failure"),
        ), patch("src.receipt_inbox_dialog.QMessageBox.warning") as warning:
            QTest.mouseClick(page.cards[submission["id"]].reject_button, Qt.LeftButton)
        warning.assert_called_once()
        self.assertEqual(self._status_count("pending"), 1)
        self.assertEqual(self._status_count("discarded"), 0)
        self.assertEqual(set(page.cards), {submission["id"]})

        with patch(
            "src.receipt_inbox_dialog.accept_ai_receipt_inbox_item",
            side_effect=RuntimeError("forced acceptance failure"),
        ), patch("src.receipt_inbox_dialog.QMessageBox.warning") as warning:
            QTest.mouseClick(page.cards[submission["id"]].accept_button, Qt.LeftButton)
        warning.assert_called_once()
        self.assertEqual(self._status_count("pending"), 1)
        self.assertEqual(self._status_count("accepted"), 0)
        self.assertEqual(self._note_count(), 0)
        self.assertEqual(set(page.cards), {submission["id"]})
        self._click_filter(dialog, "processed")
        self.assertEqual(page.cards, {})


if __name__ == "__main__":
    unittest.main()
