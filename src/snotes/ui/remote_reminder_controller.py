"""Asynchronous GUI adapter for the Cloud Reminder snapshot/command API.

This module deliberately is not a repository.  It keeps one immutable-ish
snapshot for rendering and sends every mutation through an explicit API
command on a worker thread.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from src.snotes.domain.reminders import (
    Reminder,
    ReminderLabel,
    ReminderListItem,
    ReminderOccurrence,
    ReminderOperationLog,
    ReminderOperationPage,
    ReminderQuery,
    ReminderSettings,
    ReminderTable,
    ReminderTableListItem,
    ReminderTrashSnapshot,
)


REMOTE_STATE_LOADING = "loading"
REMOTE_STATE_ONLINE = "online"
REMOTE_STATE_OFFLINE = "offline"
REMOTE_STATE_AUTH = "auth"
REMOTE_STATE_SCHEMA_MISMATCH = "schema_mismatch"
REMOTE_STATE_SERVER = "server"


@dataclass(slots=True)
class _Request:
    generation: int
    command: str | None
    payload: dict[str, Any]
    refresh: bool
    callback: Callable[[bool, object], None] | None


class _RemoteReminderWorker(QObject):
    finished = Signal(object, object, object)

    def __init__(self, client: object, request: _Request) -> None:
        super().__init__()
        self.client = client
        self.request = request

    @Slot()
    def run(self) -> None:
        try:
            result: object = None
            if self.request.command is not None:
                result = self.client.command(self.request.command, self.request.payload)
            snapshot = self.client.snapshot() if self.request.refresh else None
            if snapshot is not None:
                _validate_snapshot(snapshot)
            # The default-table rule belongs to the Cloud application service.
            # The GUI only observes the empty available-table condition and asks
            # the owner-only housekeeping command to enforce that rule.
            if (
                isinstance(snapshot, dict)
                and not snapshot.get("tables")
            ):
                self.client.command("ensure_default_table", {})
                snapshot = self.client.snapshot()
                _validate_snapshot(snapshot)
            self.finished.emit(self.request, result, snapshot)
        except Exception as error:  # delivered to the GUI thread
            self.finished.emit(self.request, error, None)


class RemoteReminderController(QObject):
    """Threaded GUI owner for remote Reminder state.

    ``client`` must expose synchronous ``snapshot`` and ``command`` methods.
    Those methods are only invoked by ``_RemoteReminderWorker`` on QThreads.
    ``cache`` is the GUI-only owner cache and may expose ``load``/``save``.
    """

    snapshotChanged = Signal()
    stateChanged = Signal(str, str)
    commandFinished = Signal(str, object)
    commandFailed = Signal(str, str, str)
    shutdownFinished = Signal()

    is_remote = True

    def __init__(self, client: object, *, cache: object | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.client = client
        self.cache = cache
        self._snapshot: dict[str, Any] | None = None
        self._generation = 0
        self._applied_generation = 0
        self._pending = 0
        self._state = REMOTE_STATE_LOADING
        self._detail = "正在连接…"
        self._threads: set[QThread] = set()
        self._workers: dict[QThread, _RemoteReminderWorker] = {}
        self._closing = False
        if cache is not None:
            try:
                cached = cache.load()
            except Exception:
                cached = None
            if isinstance(cached, dict):
                try:
                    _validate_snapshot(cached)
                    self._snapshot = cached
                except Exception:
                    pass

    @property
    def state(self) -> str:
        return self._state

    @property
    def state_detail(self) -> str:
        return self._detail

    @property
    def pending(self) -> bool:
        return self._pending > 0

    @property
    def read_only(self) -> bool:
        return self.pending or self._state != REMOTE_STATE_ONLINE

    @property
    def snapshot_time(self) -> str:
        if self._snapshot is None:
            return ""
        return str(self._snapshot.get("snapshot_time") or "")

    def refresh(self) -> None:
        if self._closing or self.pending:
            return
        self._start(None, {}, refresh=True, callback=None)

    def submit(
        self,
        command: str,
        payload: dict[str, Any],
        *,
        refresh: bool = True,
        callback: Callable[[bool, object], None] | None = None,
    ) -> bool:
        if self._closing or self.read_only:
            return False
        self._start(command, payload, refresh=refresh, callback=callback)
        return True

    def _start(
        self,
        command: str | None,
        payload: dict[str, Any],
        *,
        refresh: bool,
        callback: Callable[[bool, object], None] | None,
    ) -> None:
        self._generation += 1
        request = _Request(self._generation, command, dict(payload), refresh, callback)
        self._pending += 1
        if command is None and self._snapshot is None:
            self._set_state(REMOTE_STATE_LOADING, "正在连接…")
        else:
            self.stateChanged.emit(self._state, self._detail)
        thread = QThread(self)
        worker = _RemoteReminderWorker(self.client, request)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda current=thread: self._thread_finished(current))
        self._threads.add(thread)
        self._workers[thread] = worker
        thread.start()

    def _thread_finished(self, thread: QThread) -> None:
        self._threads.discard(thread)
        self._workers.pop(thread, None)
        if self._closing and not self._threads:
            self.shutdownFinished.emit()

    @Slot(object, object, object)
    def _finished(self, request: _Request, result: object, snapshot: object) -> None:
        self._pending = max(0, self._pending - 1)
        is_latest = request.generation == self._generation
        if isinstance(result, Exception):
            kind = _error_kind(result)
            detail = _state_text(kind, self.snapshot_time)
            if is_latest and request.generation >= self._applied_generation:
                self._applied_generation = request.generation
                self._set_state(kind, detail)
                self.snapshotChanged.emit()
            if request.command is not None:
                self.commandFailed.emit(request.command, kind, str(result))
            if request.callback is not None:
                request.callback(False, result)
            if not is_latest:
                self.stateChanged.emit(self._state, self._detail)
            return

        # A response from any superseded request may complete its command, but
        # must never replace state rendered for a newer request.
        if not is_latest or request.generation < self._applied_generation:
            if request.command is not None:
                self.commandFinished.emit(request.command, result)
            if request.callback is not None:
                request.callback(True, result)
            self.stateChanged.emit(self._state, self._detail)
            return
        self._applied_generation = request.generation
        if isinstance(snapshot, dict):
            self._snapshot = snapshot
            if self.cache is not None:
                try:
                    self.cache.save(snapshot)
                except Exception:
                    # A cache write failure must not turn a live Cloud response
                    # into an unavailable Reminder surface.
                    pass
        self._set_state(REMOTE_STATE_ONLINE, "已连接")
        self.snapshotChanged.emit()
        if request.command is not None:
            self.commandFinished.emit(request.command, result)
        if request.callback is not None:
            request.callback(True, result)

    def request_ai_operation_logs(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        callback: Callable[[bool, object], None] | None = None,
    ) -> bool:
        def parsed(success: bool, value: object) -> None:
            result: object = value
            if success:
                try:
                    result = _parse_operation_page(value, offset=offset, limit=limit)
                except Exception as error:
                    success = False
                    result = error
            if callback is not None:
                callback(success, result)

        return self.submit(
            "list_ai_operation_logs",
            {"offset": int(offset), "limit": int(limit)},
            refresh=False,
            callback=parsed,
        )

    def shutdown(self, *, timeout_ms: int = 5000) -> bool:
        """Stop accepting work and let in-flight network calls finish safely."""
        self._closing = True
        deadline = max(0, int(timeout_ms))
        threads = tuple(self._threads)
        if not threads:
            return True
        per_thread = max(1, deadline // len(threads)) if deadline else 0
        for thread in threads:
            thread.requestInterruption()
            thread.quit()
        return all(thread.wait(per_thread) for thread in threads)

    def begin_shutdown(self) -> None:
        """Stop new work; emit shutdownFinished after workers return naturally."""
        self._closing = True
        if not self._threads:
            QTimer.singleShot(0, self.shutdownFinished.emit)
            return
        for thread in tuple(self._threads):
            thread.requestInterruption()
            thread.quit()

    def _set_state(self, state: str, detail: str) -> None:
        self._state = state
        self._detail = detail
        self.stateChanged.emit(state, detail)

    # Snapshot view -----------------------------------------------------
    def list_labels(self, *, include_deleted: bool = False) -> list[ReminderLabel]:
        source = self._trash().get("labels", []) if include_deleted else self._data("labels")
        return [_parse_label(row) for row in source]

    def list_tables(self, query: ReminderQuery | None = None) -> list[ReminderTableListItem]:
        filters = query or ReminderQuery()
        source = self._trash().get("tables", []) if filters.include_deleted_tables else self._data("tables")
        items = [_parse_table_item(row, deleted=filters.include_deleted_tables) for row in source]
        result: list[ReminderTableListItem] = []
        for item in items:
            if filters.table_id is not None and item.table.id != filters.table_id:
                continue
            if filters.label_id is not None and item.table.label_id != filters.label_id:
                continue
            if filters.ai_visibility == "visible" and not item.table.ai_visible:
                continue
            if filters.ai_visibility == "hidden" and item.table.ai_visible:
                continue
            query_text = filters.query.strip().casefold()
            reminders = self.list_reminders(ReminderQuery(table_id=item.table.id))
            matching = [row for row in reminders if query_text and (query_text in row.reminder.title.casefold() or query_text in row.reminder.note.casefold())]
            title_match = bool(query_text and (query_text in item.table.title.casefold() or query_text in item.label.name.casefold()))
            if query_text and not matching and not title_match:
                continue
            result.append(
                ReminderTableListItem(
                    item.table,
                    item.label,
                    item.item_count,
                    matching[0].reminder.id if matching else None,
                    len(matching) + (1 if title_match else 0),
                )
            )
        return result

    def get_table(self, table_id: int, **_kwargs) -> ReminderTableListItem | None:
        return next((row for row in self.list_tables() if row.table.id == table_id), None)

    def list_reminders(self, query: ReminderQuery | None = None) -> list[ReminderListItem]:
        filters = query or ReminderQuery()
        tables = {row["table_id"]: _parse_table_item(row).table for row in self._data("tables")}
        items = [_parse_item(row) for row in self._data("items")]
        items = [replace(item, table=tables.get(item.table.id, item.table)) for item in items]
        result: list[ReminderListItem] = []
        for item in items:
            if filters.table_id is not None and item.reminder.table_id != filters.table_id:
                continue
            if filters.label_id is not None and item.table.label_id != filters.label_id:
                continue
            if filters.ai_visibility == "visible" and not item.table.ai_visible:
                continue
            if filters.ai_visibility == "hidden" and item.table.ai_visible:
                continue
            if filters.lifecycle is not None and filters.lifecycle not in {item.status, item.reminder.lifecycle}:
                continue
            text = filters.query.strip().casefold()
            if text and text not in item.reminder.title.casefold() and text not in item.reminder.note.casefold():
                continue
            result.append(item)
        return result

    def get_reminder(self, reminder_id: int, **_kwargs) -> ReminderListItem | None:
        return next((row for row in self.list_reminders() if row.reminder.id == reminder_id), None)

    def list_trash(self) -> ReminderTrashSnapshot:
        return ReminderTrashSnapshot(
            tuple(self.list_labels(include_deleted=True)),
            tuple(self.list_tables(ReminderQuery(include_deleted_tables=True))),
        )

    def settings(self) -> ReminderSettings:
        row = dict((self._snapshot or {}).get("settings") or {})
        return ReminderSettings(
            bool(row.get("notifications_enabled", False)),
            str(row.get("daily_time") or "10:00"),
            "https://ntfy.ke-den.de",
            str(row.get("ntfy_topic") or ""),
        )

    def badge_count(self) -> int:
        return int((self._snapshot or {}).get("badge_count") or 0)

    def list_ai_operation_logs(self, *, offset: int = 0, limit: int = 50) -> ReminderOperationPage:
        # Remote callers must use request_ai_operation_logs so the GUI thread
        # never performs network I/O.
        return ReminderOperationPage((), 0, offset, limit)

    def _data(self, key: str) -> list[dict[str, Any]]:
        value = (self._snapshot or {}).get(key, [])
        return list(value) if isinstance(value, list) else []

    def _trash(self) -> dict[str, Any]:
        value = (self._snapshot or {}).get("trash", {})
        return dict(value) if isinstance(value, dict) else {}


def _error_kind(error: Exception) -> str:
    kind = str(getattr(error, "kind", getattr(error, "code", "server")))
    if kind in {REMOTE_STATE_OFFLINE, REMOTE_STATE_AUTH, REMOTE_STATE_SCHEMA_MISMATCH, REMOTE_STATE_SERVER}:
        return kind
    return REMOTE_STATE_SERVER


def _state_text(kind: str, snapshot_time: str) -> str:
    suffix = f" · 快照时间 {snapshot_time}" if snapshot_time else ""
    if kind == REMOTE_STATE_SCHEMA_MISMATCH:
        return f"版本不匹配 · 只读{suffix}"
    if kind == REMOTE_STATE_OFFLINE:
        return f"离线 · 只读{suffix}"
    if kind == REMOTE_STATE_AUTH:
        return f"认证失败 · 只读{suffix}"
    return f"服务暂不可用 · 只读{suffix}"


def _parse_label(row: dict[str, Any]) -> ReminderLabel:
    now = str(row.get("updated_at") or "")
    return ReminderLabel(
        int(row["label_id"]),
        "default" if row.get("is_default") else f"remote-{int(row['label_id'])}",
        str(row.get("name") or ""),
        int(row.get("sort_order") or 0),
        False,
        now,
        now,
    )


def _parse_table_item(row: dict[str, Any], *, deleted: bool = False) -> ReminderTableListItem:
    label_row = dict(row.get("label") or {})
    label = ReminderLabel(
        int(label_row.get("label_id") or 0),
        f"remote-{int(label_row.get('label_id') or 0)}",
        str(label_row.get("name") or ""),
        0,
        False,
        "",
        "",
    )
    updated_at = str(row.get("updated_at") or "")
    table = ReminderTable(
        int(row["table_id"]),
        f"remote-{int(row['table_id'])}",
        str(row.get("title") or ""),
        label.id,
        bool(row.get("ai_visible", True)),
        int(row.get("sort_order") or 0),
        deleted,
        "",
        updated_at,
        # Step 6 exposes updated_at for trashed tables; deletion is the last
        # table mutation, so the current UI uses it as the displayed time.
        updated_at if deleted else None,
    )
    return ReminderTableListItem(
        table,
        label,
        int(row.get("item_count") or 0),
        _optional_int(row.get("matching_item_id")),
        int(row.get("match_count") or 0),
    )


def _parse_item(row: dict[str, Any]) -> ReminderListItem:
    label_row = dict(row.get("label") or {})
    label = ReminderLabel(
        int(label_row.get("label_id") or 0),
        f"remote-{int(label_row.get('label_id') or 0)}",
        str(label_row.get("name") or ""),
        0,
        False,
        "",
        "",
    )
    table = ReminderTable(
        int(row["table_id"]),
        f"remote-{int(row['table_id'])}",
        "",
        label.id,
        True,
        0,
        False,
        "",
        str(row.get("updated_at") or ""),
    )
    reminder = Reminder(
        int(row.get("item_id") or row.get("reminder_id")),
        table.id,
        str(row.get("title") or ""),
        str(row.get("lifecycle") or "active"),
        dict(row.get("action_spec") or {}),
        dict(row["deadline_spec"]) if isinstance(row.get("deadline_spec"), dict) else None,
        str(row.get("note") or ""),
        "",
        str(row.get("updated_at") or ""),
        multi_notify=bool(row.get("multi_notify", False)),
        recurrence_mode=str(row.get("recurrence_mode")) if row.get("recurrence_mode") else None,
    )
    occurrence_row = row.get("occurrence")
    occurrence = None
    if isinstance(occurrence_row, dict):
        occurrence = ReminderOccurrence(
            int(occurrence_row["occurrence_id"]),
            reminder.id,
            str(occurrence_row.get("occurrence_key") or ""),
            str(occurrence_row.get("action_date") or ""),
            str(occurrence_row.get("deadline_date")) if occurrence_row.get("deadline_date") else None,
            str(occurrence_row.get("status") or "pending"),
            str(occurrence_row.get("completed_at")) if occurrence_row.get("completed_at") else None,
            None,
            None,
            "",
            "",
        )
    return ReminderListItem(
        reminder,
        table,
        label,
        occurrence,
        str(row.get("status") or reminder.lifecycle),
        str(row.get("next_action_date")) if row.get("next_action_date") else None,
        str(row.get("next_deadline_date")) if row.get("next_deadline_date") else None,
    )


def _optional_int(value: object) -> int | None:
    return None if value is None else int(value)


def _parse_operation_page(value: object, *, offset: int, limit: int) -> ReminderOperationPage:
    if not isinstance(value, dict):
        raise ValueError("remote_operation_log_response_invalid")
    rows = value.get("results")
    if not isinstance(rows, list):
        raise ValueError("remote_operation_log_response_invalid")
    parsed: list[ReminderOperationLog] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("remote_operation_log_response_invalid")
        changed_fields = row.get("changed_fields") or []
        if not isinstance(changed_fields, list):
            raise ValueError("remote_operation_log_response_invalid")
        parsed.append(
            ReminderOperationLog(
                int(row["id"]),
                str(row.get("happened_at") or ""),
                str(row.get("actor_target_id") or ""),
                str(row.get("action") or ""),
                _optional_int(row.get("table_id")),
                str(row.get("table_title") or ""),
                _optional_int(row.get("item_id")),
                str(row.get("item_title") or ""),
                _optional_int(row.get("label_id")),
                str(row.get("label_name") or ""),
                str(row.get("request_id") or ""),
                tuple(str(item) for item in changed_fields),
                str(row.get("before_json") or ""),
                str(row.get("after_json") or ""),
            )
        )
    return ReminderOperationPage(
        tuple(parsed),
        int(value.get("total") or 0),
        int(value.get("offset", offset)),
        int(value.get("limit", limit)),
    )


def _validate_snapshot(snapshot):
    from src.snotes.storage.schema import REMINDER_SCHEMA_VERSION
    from src.snotes.transport.remote_reminder_client import RemoteReminderError
    if not isinstance(snapshot, dict) or type(snapshot.get("schema_version")) is not int:
        raise RemoteReminderError("server", code="snapshot_invalid")
    if snapshot["schema_version"] > int(REMINDER_SCHEMA_VERSION):
        raise RemoteReminderError("schema_mismatch")
    if not isinstance(snapshot.get("snapshot_time"), str) or not isinstance(snapshot.get("settings"), dict):
        raise RemoteReminderError("server", code="snapshot_invalid")
    for key, parser in (("labels", _parse_label), ("tables", _parse_table_item), ("items", _parse_item)):
        if not isinstance(snapshot.get(key), list):
            raise RemoteReminderError("server", code="snapshot_invalid")
        for row in snapshot[key]:
            parser(row)
