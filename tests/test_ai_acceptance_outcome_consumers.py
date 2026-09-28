"""Consumer contracts for application-owned AI acceptance outcomes."""

from __future__ import annotations

import inspect
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from src.snotes.application.ai_acceptance_outcome import (
    AcceptanceKind,
    AcceptanceOutcome,
)
from src.snotes.application.ai_receipt_inbox_service import ReceiptTargetState
from src.snotes.domain.ai_receipts import AiReceiptInboxItem
from src.snotes.transport.mcp.tools import _operation_response, _write_response
from src.snotes.ui.ai_center_dialog import AiCenterDialog, ReceiptCard, ReceiptInboxView


class TransportOutcomeConsumerTests(unittest.TestCase):
    def test_legacy_conflict_is_normalized_before_write_and_query_serialization(self) -> None:
        historical = {
            "status": "already_processed",
            "previous_status": "conflict",
            "request_id": "historical-conflict",
            "operation_type": "edit",
            "handling_mode": "human_rejected",
            "reason": "the note changed",
        }

        write = _write_response(historical)
        query = _operation_response(historical)

        self.assertEqual(write.status, "already_processed")
        self.assertEqual(write.previous_status, "conflict")
        self.assertEqual(write.processing_mode, "none")
        self.assertEqual(query.status, "already_processed")
        self.assertEqual(query.previous_status, "conflict")
        self.assertEqual(query.processing_mode, "none")
        self.assertEqual(query.failure_or_conflict_reason, "the note changed")

    def test_nonconflict_processing_mode_is_preserved(self) -> None:
        current = {
            "status": "updated",
            "request_id": "successful-write",
            "operation_type": "append",
            "handling_mode": "direct",
        }

        self.assertEqual(_write_response(current).processing_mode, "direct")
        self.assertEqual(_operation_response(current).processing_mode, "direct")

    def test_consumers_do_not_reimplement_conflict_classification(self) -> None:
        write_source = inspect.getsource(_write_response)
        operation_source = inspect.getsource(_operation_response)
        card_source = inspect.getsource(ReceiptCard)
        accept_source = inspect.getsource(ReceiptInboxView.accept_receipt)
        audit_source = inspect.getsource(AiCenterDialog.refresh_logs)

        for source in (write_source, operation_source):
            self.assertNotIn('== "conflict"', source)
            self.assertNotIn('result.get("previous_status") ==', source)
        self.assertNotIn("mcp_get_operation", card_source)
        self.assertNotIn('== "conflict"', card_source)
        self.assertNotIn("mcp_get_operation", accept_source)
        self.assertNotIn('== "conflict"', accept_source)
        self.assertNotIn("operation_status", audit_source)
        self.assertIn("row.outcome.is_conflict", audit_source)


class _FakeReceiptService:
    def __init__(self, outcome: AcceptanceOutcome) -> None:
        self.outcome = outcome
        self.operation_queries = 0

    def receipt_outcome(self, _receipt: AiReceiptInboxItem) -> AcceptanceOutcome:
        return self.outcome

    def receipt_target_state(self, _receipt: AiReceiptInboxItem) -> ReceiptTargetState:
        return ReceiptTargetState((), (), (), ())

    def mcp_get_operation(self, *_args, **_kwargs):
        self.operation_queries += 1
        raise AssertionError("UI must not query operation records to infer conflict")


class _FakeMetadata:
    @staticmethod
    def available_visibility_options() -> tuple[tuple[str, str], ...]:
        return (("full", "Full"),)

    @staticmethod
    def available_targets() -> tuple[object, ...]:
        return ()


class _AcceptanceCard(QWidget):
    def __init__(self, receipt: AiReceiptInboxItem) -> None:
        super().__init__()
        self.receipt = receipt
        self.has_final_preview = False
        self.is_pending_metadata = False
        self.processing_status = QLabel(self)
        self.accept_button = QPushButton(self)
        self.shown_error = ""

    def set_processing(self, _processing: bool) -> None:
        pass

    def form_fields(self) -> object:
        return object()

    def show_accept_error(self, reason: str) -> None:
        self.shown_error = reason


class _AcceptanceView(QWidget):
    def __init__(self, service: object, card: _AcceptanceCard) -> None:
        super().__init__()
        self.service = service
        self.cards = {card.receipt.id: card}
        self._accepting_receipt_id = None
        self.expanded_receipt_id = None
        self.list_filter = "pending"
        self.refresh_count = 0

    def refresh_receipts(self) -> None:
        self.refresh_count += 1

    def _activate_host_window(self) -> None:
        pass


class UiOutcomeConsumerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary_home = tempfile.TemporaryDirectory(
            prefix="snotes-outcome-consumer-home-"
        )
        cls.environment = patch.dict(
            os.environ,
            {
                "HOME": cls.temporary_home.name,
                "S_NOTES_DATA_ROOT": cls.temporary_home.name,
                "QT_QPA_PLATFORM": "offscreen",
                "QTWEBENGINE_CHROMIUM_FLAGS": "--disable-gpu",
                "PATH": "/usr/bin:/bin",
            },
        )
        cls.environment.start()
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    @classmethod
    def tearDownClass(cls) -> None:
        QTimer.singleShot(0, cls.app.quit)
        cls.app.exec()
        cls.environment.stop()
        cls.temporary_home.cleanup()

    @staticmethod
    def _receipt(*, status: str) -> AiReceiptInboxItem:
        return AiReceiptInboxItem(
            id=7,
            parse_status="parsed",
            status=status,
            source="test",
            action="edit",
            title="Fictional",
            main_category="Project",
            sub_category="Notes",
            tags="",
            ai_visibility="full",
            ai_targets="",
            body="proposed body",
            note_id=3,
            created_at="2026-09-28T12:00:00+00:00",
            updated_at="2026-09-28T12:00:00+00:00",
            parse_error=None,
            request_id="typed-conflict",
            actor_target_id="gale",
            operation_type="edit",
            external_submission_id=None,
            idempotency_key=None,
        )

    def test_historical_conflict_card_uses_service_outcome(self) -> None:
        service = _FakeReceiptService(
            AcceptanceOutcome(
                kind=AcceptanceKind.CONFLICT,
                reason="the note changed",
            )
        )
        card = ReceiptCard(
            self._receipt(status="rejected"),
            service,  # type: ignore[arg-type]
            _FakeMetadata(),  # type: ignore[arg-type]
        )
        self.addCleanup(card.close)

        self.assertIn("版本冲突 · 未写入", card.summary_label.text())
        reasons = [
            label.text()
            for label in card.findChildren(QLabel)
            if label.objectName() == "receiptOperationReason"
        ]
        self.assertEqual(reasons, ["版本冲突：the note changed"])
        self.assertEqual(service.operation_queries, 0)

    def test_accept_conflict_consumes_typed_result_without_exception_or_query(self) -> None:
        receipt = self._receipt(status="pending")
        card = _AcceptanceCard(receipt)
        self.addCleanup(card.close)
        service = _FakeReceiptService(
            AcceptanceOutcome(
                kind=AcceptanceKind.CONFLICT,
                reason="the note changed",
            )
        )
        service.accept_receipt = lambda *_args: SimpleNamespace(
            outcome=service.outcome,
            note=None,
        )
        view = _AcceptanceView(service, card)
        self.addCleanup(view.close)

        with patch("src.snotes.ui.ai_center_dialog.QMessageBox.warning") as warning:
            ReceiptInboxView.accept_receipt(view, receipt.id)  # type: ignore[arg-type]

        self.assertEqual(service.operation_queries, 0)
        self.assertEqual(view.list_filter, "processed")
        self.assertEqual(view.expanded_receipt_id, receipt.id)
        self.assertEqual(view.refresh_count, 1)
        self.assertIn("版本冲突", card.shown_error)
        self.assertIn("AI 修改未写入", card.shown_error)
        warning.assert_called_once()
        self.assertEqual(warning.call_args.args[1], "版本冲突")


if __name__ == "__main__":
    unittest.main()
