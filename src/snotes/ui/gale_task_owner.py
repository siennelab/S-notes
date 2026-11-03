"""Qt lifetime coordination shared by AI Center and the startup Gale pull."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot


class GaleWorker(QObject):
    finished = Signal(object, object, bool, str)

    def __init__(self, action, *, refresh_receipts: bool, action_kind: str) -> None:
        super().__init__()
        self.action = action
        self.refresh_receipts = refresh_receipts
        self.action_kind = action_kind

    @Slot()
    def run(self) -> None:
        try:
            result = self.action()
        except Exception as error:
            reason = str(getattr(error, "code", "unexpected_failure"))
            self.finished.emit(None, reason, self.refresh_receipts, self.action_kind)
            return
        self.finished.emit(result, None, self.refresh_receipts, self.action_kind)


class GaleTaskOwner(QObject):
    """Retain native workers, drain one coalesced followup, then report idle.

    Startup pulls retain their existing independence from dialog actions. Their
    lifetime still participates in the shared application's close barrier.
    Actions and their business results belong to callers, not this owner.
    """

    actionFinished = Signal(object, object, bool, str)
    idle = Signal()
    dialogIdle = Signal()
    followupStarted = Signal()
    dialogCloseReady = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._workers: dict[QThread, GaleWorker] = {}
        self._queued_followup: Callable[[], object] | None = None
        self._dialog_close_pending = False

    @property
    def has_tasks(self) -> bool:
        return bool(self._workers) or self.followup_queued

    @property
    def has_dialog_tasks(self) -> bool:
        return self.followup_queued or any(
            worker.action_kind != "startup_pull" for worker in self._workers.values()
        )

    @property
    def followup_queued(self) -> bool:
        return self._queued_followup is not None

    @property
    def dialog_close_pending(self) -> bool:
        return self._dialog_close_pending

    def is_running(self, action_kind: str) -> bool:
        return any(worker.action_kind == action_kind for worker in self._workers.values())

    def start(
        self,
        action: Callable[[], object],
        *,
        refresh_receipts: bool = False,
        action_kind: str = "controls",
    ) -> None:
        thread = QThread(self)
        worker = GaleWorker(
            action, refresh_receipts=refresh_receipts, action_kind=action_kind,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._action_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._thread_finished)
        self._workers[thread] = worker
        thread.start()

    def queue_followup(self, action: Callable[[], object]) -> bool:
        """Return whether the action was coalesced behind existing dialog work."""
        if self.has_dialog_tasks:
            self._queued_followup = action
            return True
        self._start_followup(action)
        return False

    def request_dialog_close(self) -> bool:
        """Defer dialog destruction while its own running/queued work drains."""
        self._dialog_close_pending = self.has_dialog_tasks
        return not self._dialog_close_pending

    def cancel_dialog_close(self) -> None:
        self._dialog_close_pending = False

    @Slot(object, object, bool, str)
    def _action_finished(self, result, error, refresh_receipts, action_kind) -> None:
        self.actionFinished.emit(result, error, refresh_receipts, action_kind)

    @Slot()
    def _thread_finished(self) -> None:
        thread = self.sender()
        worker = self._workers.get(thread)
        if worker is None:
            return
        # QThread.finished precedes native DeferredDelete cleanup. Join before
        # releasing either wrapper or announcing that closing is safe.
        thread.wait()
        self._workers.pop(thread)
        thread.deleteLater()
        if worker.action_kind != "startup_pull":
            if self.followup_queued:
                QTimer.singleShot(0, self._drain_followup)
            elif not self.has_dialog_tasks:
                self.dialogIdle.emit()
                if self._dialog_close_pending:
                    QTimer.singleShot(0, self._finish_dialog_close)
        if not self.has_tasks:
            self.idle.emit()

    def _start_followup(self, action: Callable[[], object]) -> None:
        self.followupStarted.emit()
        self.start(action, action_kind="greencloud_followup")

    def _drain_followup(self) -> None:
        if any(worker.action_kind != "startup_pull" for worker in self._workers.values()):
            return
        action = self._queued_followup
        if action is not None:
            self._queued_followup = None
            self._start_followup(action)

    def _finish_dialog_close(self) -> None:
        if self._dialog_close_pending and not self.has_dialog_tasks:
            self._dialog_close_pending = False
            self.dialogCloseReady.emit()
