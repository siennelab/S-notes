"""Qt Reminder Center for the Reminder R1 product surface."""

from __future__ import annotations

from datetime import date, datetime
from urllib.parse import urlparse

from PySide6.QtCore import QDate, QLocale, QSize, Qt, QTime, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCalendarWidget,
    QCheckBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTimeEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.snotes.application.reminder_service import ReminderService
from src.snotes.domain.reminder_schedule import (
    SHANGHAI,
    deadline_for,
    describe_schedule,
    is_recurring,
    next_action_date,
)
from src.snotes.domain.reminders import (
    REMINDER_ACTIVE,
    REMINDER_COMPLETED,
    REMINDER_PAUSED,
    Reminder,
    ReminderDraft,
    ReminderLabel,
    ReminderListItem,
    ReminderQuery,
    ReminderSettings,
    ReminderTableListItem,
)
from src.snotes.ui.floating_combo import FloatingComboBox
from src.snotes.ui.theme import apply_selection_palette, current_theme, repolish, themed_icon


class ReminderCenter(QWidget):
    changed = Signal()
    testNotificationRequested = Signal()
    filterChanged = Signal(object, str)

    def __init__(self, service: ReminderService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.items: list[ReminderListItem] = []
        self.trash_tables: list[ReminderTableListItem] = []
        self.selected_table_id: int | None = None
        self.pending_highlight_item_id: int | None = None
        self.label_id: int | None = None
        self.ai_visibility = "all"
        self.search_query = ""
        self.trash_mode = False
        self.setObjectName("reminderCenter")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 12)
        layout.setSpacing(10)

        self.table_meta = QWidget()
        self.table_meta.setObjectName("reminderTableMeta")
        meta_layout = QVBoxLayout(self.table_meta)
        meta_layout.setContentsMargins(0, 0, 0, 0)
        meta_layout.setSpacing(6)
        self.table_title_input = QLineEdit()
        self.table_title_input.setObjectName("reminderTableTitleInput")
        self.table_title_input.setPlaceholderText("Reminder Table")
        meta_layout.addWidget(self.table_title_input)
        self.title_separator = QWidget()
        self.title_separator.setObjectName("reminderTitleSeparator")
        meta_layout.addWidget(self.title_separator)
        self.meta_toggle = QToolButton()
        self.meta_toggle.setObjectName("reminderMetaSummaryButton")
        self.meta_toggle.setCheckable(True)
        self.meta_toggle.setChecked(False)
        self.meta_toggle.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.meta_summary = self.meta_toggle
        meta_layout.addWidget(self.meta_toggle)
        self.meta_panel = QWidget()
        self.meta_panel.setObjectName("reminderMetaPanel")
        panel_row = QHBoxLayout(self.meta_panel)
        panel_row.setContentsMargins(0, 0, 0, 0)
        panel_row.setSpacing(6)
        self.table_label_combo = FloatingComboBox()
        self.table_label_combo.setObjectName("reminderTableLabelCombo")
        self.table_label_combo.configure_popup(220)
        self.table_ai_combo = FloatingComboBox()
        self.table_ai_combo.setObjectName("reminderTableAiVisibleCombo")
        self.table_ai_combo.configure_popup(160)
        self.table_ai_combo.addItem("AI 可见", True)
        self.table_ai_combo.addItem("AI 不可见", False)
        panel_row.addWidget(_settings_label("所属标签"))
        panel_row.addWidget(self.table_label_combo)
        panel_row.addWidget(_settings_label("AI 可见性"))
        panel_row.addWidget(self.table_ai_combo)
        panel_row.addStretch(1)
        meta_layout.addWidget(self.meta_panel)
        self.meta_panel.setVisible(False)
        layout.addWidget(self.table_meta)

        self.toolbar = QWidget()
        self.toolbar.setObjectName("reminderToolbar")
        action_row = QHBoxLayout(self.toolbar)
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(6)
        self.new_button = QPushButton("新建")
        self.edit_button = QPushButton("编辑")
        self.delete_button = QPushButton("删除")
        self.restore_button = QPushButton("恢复")
        self.permanent_delete_button = QPushButton("永久删除")
        self.pause_button = QPushButton("暂停")
        self.resume_button = QPushButton("恢复")
        self.complete_occurrence_button = QPushButton("完成本次")
        self.complete_button = QPushButton("完成")
        for button in (
            self.new_button,
            self.edit_button,
            self.delete_button,
            self.restore_button,
            self.permanent_delete_button,
            self.pause_button,
            self.resume_button,
            self.complete_occurrence_button,
            self.complete_button,
        ):
            action_row.addWidget(button)
        action_row.addStretch(1)
        layout.addWidget(self.toolbar)

        self.table = QTableWidget(0, 6)
        self.table.setObjectName("reminderTable")
        self.table.setHorizontalHeaderLabels(
            ["事项", "状态", "开始处理日", "重复", "最晚处理日", "备注"]
        )
        self.table.horizontalHeader().setDefaultAlignment(
            Qt.AlignLeft | Qt.AlignVCenter
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setStretchLastSection(False)
        for column in range(6):
            self.table.horizontalHeader().setSectionResizeMode(
                column,
                QHeaderView.Interactive,
            )
        self.table.currentCellChanged.connect(lambda *_args: self._update_action_states())
        layout.addWidget(self.table, stretch=1)

        self.settings_widget = QWidget()
        self.settings_widget.setObjectName("reminderSettings")
        settings = QHBoxLayout(self.settings_widget)
        settings.setContentsMargins(0, 2, 0, 0)
        settings.setSpacing(6)
        self.enabled_check = QCheckBox("通知：开启")
        self.time_edit = QTimeEdit()
        self.time_edit.setObjectName("reminderTimeEdit")
        self.time_edit.setDisplayFormat("HH:mm")
        self.topic_input = QLineEdit()
        self.topic_input.setObjectName("reminderTopicInput")
        self.topic_input.setPlaceholderText("Reminder ntfy topic")
        self.topic_input.setReadOnly(True)
        self.topic_input.setMinimumWidth(420)
        self.copy_topic_button = QPushButton("复制 Topic")
        self.delete_table_button = QToolButton()
        self.delete_table_button.setObjectName("reminderDeleteTableButton")
        self.delete_table_button.setToolTip("删除表格")
        self.delete_table_button.setFixedSize(QSize(28, 28))
        self.delete_table_button.setIconSize(QSize(14, 14))
        settings.addWidget(self.enabled_check)
        settings.addWidget(_settings_label("时间"))
        settings.addWidget(self.time_edit)
        settings.addWidget(_settings_label("Topic"))
        settings.addWidget(self.topic_input, stretch=1)
        settings.addWidget(self.copy_topic_button)
        settings.addWidget(self.delete_table_button)
        layout.addWidget(self.settings_widget)

        self.new_button.clicked.connect(self._new)
        self.edit_button.clicked.connect(self._edit)
        self.delete_button.clicked.connect(self._delete)
        self.restore_button.clicked.connect(self._restore)
        self.permanent_delete_button.clicked.connect(self._permanently_delete)
        self.pause_button.clicked.connect(self._pause)
        self.resume_button.clicked.connect(self._resume)
        self.complete_occurrence_button.clicked.connect(self._complete_occurrence)
        self.complete_button.clicked.connect(self._complete)
        self.meta_toggle.toggled.connect(self.meta_panel.setVisible)
        self.table_title_input.editingFinished.connect(self._save_table_title)
        self.table_label_combo.currentIndexChanged.connect(self._table_label_changed)
        self.table_ai_combo.currentIndexChanged.connect(self._table_ai_changed)
        self.delete_table_button.clicked.connect(self._delete_current_table)
        self.enabled_check.stateChanged.connect(self._save_settings)
        self.time_edit.timeChanged.connect(self._save_settings)
        self.copy_topic_button.clicked.connect(self._copy_topic)
        self.refresh_theme()
        self.refresh()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_table_widths()

    def refresh(self) -> None:
        if self.trash_mode:
            snapshot = self.service.list_trash()
            self.items = []
            self.trash_tables = list(snapshot.tables)
            self.table_meta.setVisible(False)
            self.delete_table_button.setVisible(False)
            self.table.setColumnCount(6)
            self.table.setHorizontalHeaderLabels(
                ["Table", "标签", "AI 权限", "事项数", "删除时间", ""]
            )
            self.table.setRowCount(len(self.trash_tables))
            for row, item in enumerate(self.trash_tables):
                table = item.table
                values = [
                    table.title,
                    item.label.name,
                    "AI 可见" if table.ai_visible else "AI 不可见",
                    item.item_count,
                    table.deleted_at or "",
                    "",
                ]
                for column, value in enumerate(values):
                    cell = QTableWidgetItem(str(value))
                    cell.setData(Qt.UserRole, table.id)
                    cell.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                    self.table.setItem(row, column, cell)
        else:
            self.table_meta.setVisible(True)
            self.delete_table_button.setVisible(True)
            if self.selected_table_id is None:
                tables = self.service.list_tables(
                    ReminderQuery(
                        label_id=self.label_id,
                        ai_visibility=self.ai_visibility,
                    )
                )
                if tables:
                    self.selected_table_id = tables[0].table.id
                else:
                    self.selected_table_id = self.service.default_table().id
            self._load_table_metadata()
            self.items = self.service.list_reminders(
                ReminderQuery(
                    table_id=self.selected_table_id,
                )
            )
            self.table.setColumnCount(6)
            self.table.setHorizontalHeaderLabels(
                ["事项", "状态", "开始处理日", "重复", "最晚处理日", "备注"]
            )
            self.table.setRowCount(len(self.items))
            highlight_row = -1
            for row, item in enumerate(self.items):
                reminder = item.reminder
                if self.pending_highlight_item_id == reminder.id:
                    highlight_row = row
                values = [
                    reminder.title,
                    _status_text(item),
                    item.next_action_date or "",
                    describe_schedule(reminder.action_spec),
                    item.next_deadline_date or "",
                    reminder.note,
                ]
                for column, value in enumerate(values):
                    cell = QTableWidgetItem(str(value))
                    cell.setData(Qt.UserRole, reminder.id)
                    cell.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                    self.table.setItem(row, column, cell)
            if highlight_row >= 0:
                self.table.setCurrentCell(highlight_row, 0)
                self.pending_highlight_item_id = None
        self._load_settings()
        self._apply_table_widths()
        self._update_action_states()

    def enter_default(self) -> None:
        self.trash_mode = False
        self.label_id = None
        self.ai_visibility = "all"
        self.search_query = ""
        self.selected_table_id = None
        self.refresh()
        self.filterChanged.emit(self.label_id, self.ai_visibility)

    def set_filter(self, label_id: int | None, ai_visibility: str) -> None:
        self.trash_mode = False
        self.label_id = label_id
        self.ai_visibility = ai_visibility if ai_visibility in {"all", "visible", "hidden"} else "all"
        self.selected_table_id = None
        self.refresh()

    def set_search(self, query: str) -> None:
        self.search_query = query
        if not self.trash_mode:
            self.refresh()

    def show_trash(self) -> None:
        self.trash_mode = True
        self.refresh()

    def select_table(self, table_id: int, matching_item_id: int | None = None) -> None:
        self.trash_mode = False
        self.selected_table_id = table_id
        self.pending_highlight_item_id = matching_item_id
        self.refresh()

    def labels(self) -> list[ReminderLabel]:
        return self.service.list_labels()

    def refresh_theme(self) -> None:
        tokens = current_theme()
        palette = QPalette(self.table.palette())
        for role in (QPalette.Base, QPalette.Window):
            palette.setColor(role, QColor(tokens.surface))
        palette.setColor(QPalette.Text, QColor(tokens.text))
        palette.setColor(QPalette.WindowText, QColor(tokens.text))
        palette.setColor(QPalette.Highlight, QColor(tokens.surface_selected))
        palette.setColor(QPalette.HighlightedText, QColor(tokens.text))
        self.table.setPalette(palette)
        self.table.viewport().setPalette(palette)
        apply_selection_palette(self.table)
        apply_selection_palette(self.table.viewport())
        self.delete_table_button.setIcon(themed_icon("trash.svg"))
        self.table.viewport().update()

    def _selected_id(self) -> int | None:
        row = self.table.currentRow()
        if self.trash_mode:
            if row < 0 or row >= len(self.trash_tables):
                return None
            return self.trash_tables[row].table.id
        if row < 0 or row >= len(self.items):
            return None
        return self.items[row].reminder.id

    def _selected_reminder(self) -> Reminder | None:
        reminder_id = self._selected_id()
        if reminder_id is None:
            return None
        return next(
            (item.reminder for item in self.items if item.reminder.id == reminder_id),
            None,
        )

    def _selected_item(self) -> ReminderListItem | None:
        reminder_id = self._selected_id()
        if reminder_id is None:
            return None
        return next(
            (item for item in self.items if item.reminder.id == reminder_id),
            None,
        )

    def _new(self) -> None:
        table_id = self.selected_table_id or self.service.default_table().id
        dialog = ReminderDialog(
            parent=self,
            table_id=table_id,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        self.service.create_reminder(dialog.draft())
        self._after_change()

    def _edit(self) -> None:
        reminder = self._selected_reminder()
        if reminder is None:
            return
        dialog = ReminderDialog(reminder, self, table_id=reminder.table_id)
        if dialog.exec() != QDialog.Accepted:
            return
        self.service.update_reminder(reminder.id, dialog.draft())
        self._after_change()

    def _delete(self) -> None:
        reminder_id = self._selected_id()
        if reminder_id is None:
            return
        self.service.delete_reminder(reminder_id)
        self._after_change()

    def _restore(self) -> None:
        table_id = self._selected_id()
        if table_id is not None:
            self.service.restore_table(table_id)
            self._after_change()

    def _permanently_delete(self) -> None:
        table_id = self._selected_id()
        if table_id is None:
            return
        if QMessageBox.question(self, "永久删除 Table", "确定永久删除这个 Reminder Table 及其中所有事项吗？此操作无法撤销。") != QMessageBox.Yes:
            return
        self.service.permanently_delete_table(table_id)
        self._after_change()

    def _pause(self) -> None:
        reminder_id = self._selected_id()
        if reminder_id is not None:
            self.service.pause_reminder(reminder_id)
            self._after_change()

    def _resume(self) -> None:
        reminder_id = self._selected_id()
        if reminder_id is not None:
            self.service.resume_reminder(reminder_id)
            self._after_change()

    def _complete_occurrence(self) -> None:
        reminder_id = self._selected_id()
        if reminder_id is not None:
            self.service.complete_occurrence(reminder_id)
            self._after_change()

    def _complete(self) -> None:
        reminder_id = self._selected_id()
        if reminder_id is not None:
            self.service.complete_reminder(reminder_id)
            self._after_change()

    def _after_change(self) -> None:
        self.refresh()
        self.changed.emit()

    def _load_table_metadata(self) -> None:
        if self.selected_table_id is None:
            return
        item = self.service.get_table(self.selected_table_id)
        if item is None:
            self.table_title_input.setText("")
            self.meta_summary.setText("")
            return
        self.table_title_input.blockSignals(True)
        self.table_label_combo.blockSignals(True)
        self.table_ai_combo.blockSignals(True)
        self.table_title_input.setText(item.table.title)
        self.table_label_combo.clear()
        for label in self.service.list_labels():
            self.table_label_combo.addItem(label.name, label.id)
        label_index = self.table_label_combo.findData(item.label.id)
        self.table_label_combo.setCurrentIndex(max(0, label_index))
        ai_index = self.table_ai_combo.findData(item.table.ai_visible)
        self.table_ai_combo.setCurrentIndex(max(0, ai_index))
        self.meta_summary.setText(
            f"{item.label.name} · {'AI 可见' if item.table.ai_visible else 'AI 不可见'}"
        )
        self.table_title_input.blockSignals(False)
        self.table_label_combo.blockSignals(False)
        self.table_ai_combo.blockSignals(False)

    def _save_table_title(self) -> None:
        if self.trash_mode or self.selected_table_id is None:
            return
        title = self.table_title_input.text().strip()
        if not title:
            self._load_table_metadata()
            return
        self.service.update_table_metadata(self.selected_table_id, title=title)
        self._after_change()

    def _table_label_changed(self, _index: int = -1) -> None:
        if self.trash_mode or self.selected_table_id is None or self.table_label_combo.signalsBlocked():
            return
        label_id = self.table_label_combo.currentData()
        if label_id is None:
            return
        self.service.update_table_metadata(self.selected_table_id, label_id=int(label_id))
        self._after_change()

    def _table_ai_changed(self, _index: int = -1) -> None:
        if self.trash_mode or self.selected_table_id is None or self.table_ai_combo.signalsBlocked():
            return
        self.service.set_table_ai_visible(
            self.selected_table_id,
            bool(self.table_ai_combo.currentData()),
        )
        self._after_change()

    def _delete_current_table(self) -> None:
        if self.trash_mode or self.selected_table_id is None:
            return
        self.service.delete_table(self.selected_table_id)
        self.selected_table_id = None
        self._after_change()

    def _load_settings(self) -> None:
        settings = self.service.settings()
        self.enabled_check.blockSignals(True)
        self.time_edit.blockSignals(True)
        self.topic_input.blockSignals(True)
        self.enabled_check.setText("通知：开启" if settings.notifications_enabled else "通知：关闭")
        self.enabled_check.setChecked(settings.notifications_enabled)
        parsed_time = QTime.fromString(settings.daily_time, "HH:mm")
        self.time_edit.setTime(parsed_time if parsed_time.isValid() else QTime(10, 0))
        self.topic_input.setText(settings.ntfy_topic)
        self.enabled_check.blockSignals(False)
        self.time_edit.blockSignals(False)
        self.topic_input.blockSignals(False)

    def _save_settings(self) -> None:
        self.enabled_check.setText(
            "通知：开启" if self.enabled_check.isChecked() else "通知：关闭"
        )
        self.service.save_settings(
            ReminderSettings(
                self.enabled_check.isChecked(),
                self.time_edit.time().toString("HH:mm"),
                self.service.settings().ntfy_endpoint,
                self.topic_input.text().strip(),
            )
        )

    def _test_notification(self) -> None:
        self._save_settings()
        self.testNotificationRequested.emit()

    def _copy_topic(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.topic_input.text().strip())

    def _apply_table_widths(self) -> None:
        width = max(1, self.table.viewport().width())
        proportions = (0.24, 0.16, 0.14, 0.20, 0.12, 0.22)
        minimums = (160, 160, 112, 180, 112, 240)
        for column, proportion in enumerate(proportions):
            self.table.setColumnWidth(
                column,
                max(minimums[column], int(width * proportion)),
            )

    def _update_action_states(self) -> None:
        item = self._selected_item()
        selected = item is not None
        table_selected = self.trash_mode and self._selected_id() is not None
        lifecycle = item.reminder.lifecycle if item is not None else ""
        active = lifecycle == REMINDER_ACTIVE
        paused = lifecycle == REMINDER_PAUSED
        completed = lifecycle == REMINDER_COMPLETED or (
            item is not None and item.status == "completed"
        )
        recurring = item is not None and is_recurring(item.reminder.action_spec)
        occurrence_completed = item is not None and item.status == "occurrence_completed"
        self.edit_button.setEnabled(selected)
        self.delete_button.setEnabled(selected)
        self.pause_button.setEnabled(selected and active and not completed)
        self.resume_button.setEnabled(selected and paused)
        self.complete_occurrence_button.setEnabled(
            selected and active and recurring and not occurrence_completed
        )
        self.complete_button.setEnabled(
            selected and active and not recurring and not completed
        )
        self.restore_button.setVisible(self.trash_mode)
        self.permanent_delete_button.setVisible(self.trash_mode)
        self.new_button.setVisible(not self.trash_mode)
        for button in (
            self.edit_button,
            self.delete_button,
            self.pause_button,
            self.resume_button,
            self.complete_occurrence_button,
            self.complete_button,
        ):
            button.setVisible(not self.trash_mode)
        self.restore_button.setEnabled(table_selected)
        self.permanent_delete_button.setEnabled(table_selected)


class ReminderLabelManagerDialog(QDialog):
    def __init__(self, service: ReminderService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.labels: list[ReminderLabel] = []
        self.setObjectName("reminderLabelManagerDialog")
        self.setWindowTitle("Reminder 标签管理")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel("Reminder 标签")
        title.setObjectName("categoryManagerTitle")
        layout.addWidget(title)

        body = QHBoxLayout()
        body.setSpacing(10)
        self.label_list = QListWidget()
        self.label_list.setObjectName("reminderLabelManagerList")
        self.label_list.setMinimumHeight(260)
        body.addWidget(self.label_list, stretch=1)

        controls = QVBoxLayout()
        controls.setSpacing(8)
        self.name_input = QLineEdit()
        self.name_input.setObjectName("reminderLabelNameInput")
        self.name_input.setPlaceholderText("标签名称")
        controls.addWidget(self.name_input)
        self.create_button = QPushButton("新增")
        self.rename_button = QPushButton("重命名")
        self.delete_button = QPushButton("删除")
        self.move_up_button = QPushButton("上移")
        self.move_down_button = QPushButton("下移")
        for button in (
            self.create_button,
            self.rename_button,
            self.delete_button,
            self.move_up_button,
            self.move_down_button,
        ):
            controls.addWidget(button)
        controls.addStretch(1)
        body.addLayout(controls)
        layout.addLayout(body)

        self.status_label = QLabel("")
        self.status_label.setObjectName("reminderLabelManagerStatus")
        layout.addWidget(self.status_label)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.label_list.currentItemChanged.connect(lambda *_args: self._selection_changed())
        self.create_button.clicked.connect(self._create_label)
        self.rename_button.clicked.connect(self._rename_label)
        self.delete_button.clicked.connect(self._delete_label)
        self.move_up_button.clicked.connect(lambda: self._move_selected(-1))
        self.move_down_button.clicked.connect(lambda: self._move_selected(1))
        self.refresh()

    def refresh(self, selected_id: int | None = None) -> None:
        previous = selected_id if selected_id is not None else self._selected_label_id()
        self.labels = self.service.list_labels()
        self.label_list.blockSignals(True)
        self.label_list.clear()
        selected_row = 0
        for row, label in enumerate(self.labels):
            suffix = "  默认" if label.is_default else ""
            item = QListWidgetItem(f"{label.name}{suffix}")
            item.setData(Qt.UserRole, label.id)
            self.label_list.addItem(item)
            if label.id == previous:
                selected_row = row
        if self.labels:
            self.label_list.setCurrentRow(selected_row)
        self.label_list.blockSignals(False)
        self._selection_changed()

    def _selection_changed(self) -> None:
        label = self._selected_label()
        selected = label is not None
        self.name_input.setText(label.name if label else "")
        self.rename_button.setEnabled(selected)
        self.delete_button.setEnabled(selected and not label.is_default)
        row = self.label_list.currentRow()
        self.move_up_button.setEnabled(selected and row > 0)
        self.move_down_button.setEnabled(selected and row < len(self.labels) - 1)

    def _selected_label_id(self) -> int | None:
        item = self.label_list.currentItem()
        return int(item.data(Qt.UserRole)) if item is not None else None

    def _selected_label(self) -> ReminderLabel | None:
        label_id = self._selected_label_id()
        if label_id is None:
            return None
        return next((label for label in self.labels if label.id == label_id), None)

    def _clean_name(self) -> str:
        return self.name_input.text().strip()

    def _create_label(self) -> None:
        name = self._clean_name()
        if not name:
            self.status_label.setText("请输入标签名称")
            return
        label = self.service.create_label(name)
        self.status_label.setText("已新增标签")
        self.refresh(label.id)

    def _rename_label(self) -> None:
        label = self._selected_label()
        name = self._clean_name()
        if label is None or not name:
            self.status_label.setText("请选择标签并输入名称")
            return
        self.service.rename_label(label.id, name)
        self.status_label.setText("已重命名标签")
        self.refresh(label.id)

    def _delete_label(self) -> None:
        label = self._selected_label()
        if label is None:
            return
        if label.is_default:
            self.status_label.setText("默认标签不可删除")
            return
        self.service.delete_label(label.id)
        self.status_label.setText("标签及其表格已移入 Reminder 回收站")
        self.refresh()

    def _move_selected(self, direction: int) -> None:
        label = self._selected_label()
        if label is None:
            return
        ids = [item.id for item in self.labels]
        index = ids.index(label.id)
        target = index + direction
        if target < 0 or target >= len(ids):
            return
        ids[index], ids[target] = ids[target], ids[index]
        self.service.reorder_labels(ids)
        self.status_label.setText("已更新排序")
        self.refresh(label.id)


class ReminderDialog(QDialog):
    def __init__(
        self,
        reminder: Reminder | None = None,
        parent: QWidget | None = None,
        *,
        table_id: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.reminder = reminder
        self.table_id = table_id if table_id is not None else (reminder.table_id if reminder else None)
        self.setObjectName("reminderDialog")
        self.setWindowTitle("编辑提醒" if reminder else "新建提醒")
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        self.form = QFormLayout()
        form = self.form
        self.title_input = QLineEdit()
        self.lifecycle_combo = FloatingComboBox()
        self.lifecycle_combo.setObjectName("reminderLifecycleCombo")
        self.lifecycle_combo.configure_popup(180)
        self.lifecycle_combo.setMinimumWidth(140)
        self.lifecycle_combo.addItem("启用", REMINDER_ACTIVE)
        self.lifecycle_combo.addItem("暂停", REMINDER_PAUSED)
        self.action_date = QDateEdit()
        _configure_date_edit(self.action_date)
        self.action_date.setDate(QDate.currentDate())
        self.repeat_combo = FloatingComboBox()
        self.repeat_combo.setObjectName("reminderRepeatCombo")
        self.repeat_combo.configure_popup(320)
        self.repeat_combo.setMinimumWidth(320)
        self.repeat_combo.addItem("不重复", "one_time")
        self.repeat_combo.addItem("每月固定日", "monthly_day")
        self.repeat_combo.addItem("每年固定公历日期", "yearly_date")
        self.repeat_combo.addItem("每 N 天", "interval_days")
        self.repeat_combo.addItem("完成后 + N 天", "interval_from_completion")
        self.repeat_combo.addItem("每月第 N 个星期几", "nth_weekday_monthly")
        self.repeat_combo.addItem("每年某月第 N 个星期几", "nth_weekday_yearly")
        self.repeat_combo.addItem("每年农历某月某日", "lunar_yearly")
        self.repeat_combo.addItem("A股每月某日或之后首个交易日", "cn_a_share_on_or_after")
        self.repeat_number = QSpinBox()
        self.repeat_number.setRange(1, 3660)
        self.repeat_number.setValue(1)
        self.repeat_param_label = QLabel()
        self.repeat_param_label.setObjectName("reminderDialogFieldLabel")
        self.repeat_param_unit = QLabel()
        self.repeat_param_unit.setObjectName("reminderDialogFieldLabel")
        self.repeat_param_widget = QWidget()
        repeat_param_layout = QHBoxLayout(self.repeat_param_widget)
        repeat_param_layout.setContentsMargins(0, 0, 0, 0)
        repeat_param_layout.setSpacing(6)
        repeat_param_layout.addWidget(self.repeat_number)
        repeat_param_layout.addWidget(self.repeat_param_unit)
        repeat_param_layout.addStretch(1)

        self.deadline_combo = FloatingComboBox()
        self.deadline_combo.setObjectName("reminderDeadlineCombo")
        self.deadline_combo.configure_popup(200)
        self.deadline_combo.setMinimumWidth(180)
        self.deadline_combo.addItem("无", "none")
        self.deadline_combo.addItem("设置最晚处理日", "set")
        self.deadline_date = QDateEdit()
        _configure_date_edit(self.deadline_date)
        self.deadline_date.setDate(QDate.currentDate())
        self.deadline_day = QSpinBox()
        self.deadline_day.setObjectName("reminderDeadlineDay")
        self.deadline_day.setRange(1, 31)
        self.deadline_day.setValue(self.action_date.date().day())
        self.deadline_prefix = QLabel("每月第")
        self.deadline_prefix.setObjectName("reminderDialogFieldLabel")
        self.deadline_unit = QLabel("日")
        self.deadline_unit.setObjectName("reminderDialogFieldLabel")
        self.deadline_date_label = QLabel("具体日期")
        self.deadline_date_label.setObjectName("reminderDialogFieldLabel")
        self.deadline_widget = QWidget()
        deadline_layout = QHBoxLayout(self.deadline_widget)
        deadline_layout.setContentsMargins(0, 0, 0, 0)
        deadline_layout.setSpacing(6)
        deadline_layout.addWidget(self.deadline_combo)
        deadline_layout.addWidget(self.deadline_date_label)
        deadline_layout.addWidget(self.deadline_date)
        deadline_layout.addWidget(self.deadline_prefix)
        deadline_layout.addWidget(self.deadline_day)
        deadline_layout.addWidget(self.deadline_unit)
        deadline_layout.addStretch(1)

        self.repeat_until_combo = FloatingComboBox()
        self.repeat_until_combo.setObjectName("reminderRepeatUntilCombo")
        self.repeat_until_combo.configure_popup(180)
        self.repeat_until_combo.setMinimumWidth(150)
        self.repeat_until_combo.addItem("永久重复", "never")
        self.repeat_until_combo.addItem("指定日期", "date")
        self.repeat_until_date = QDateEdit()
        _configure_date_edit(self.repeat_until_date)
        self.repeat_until_date.setDate(QDate.currentDate())
        self.repeat_until_date_label = QLabel("结束日期")
        self.repeat_until_date_label.setObjectName("reminderDialogFieldLabel")
        self.repeat_until_widget = QWidget()
        repeat_until_layout = QHBoxLayout(self.repeat_until_widget)
        repeat_until_layout.setContentsMargins(0, 0, 0, 0)
        repeat_until_layout.setSpacing(6)
        repeat_until_layout.addWidget(self.repeat_until_combo)
        repeat_until_layout.addWidget(self.repeat_until_date_label)
        repeat_until_layout.addWidget(self.repeat_until_date)
        repeat_until_layout.addStretch(1)

        self.note_input = QTextEdit()
        self.note_input.setFixedHeight(80)
        self.title_label = _dialog_label("事项名称")
        self.lifecycle_label = _dialog_label("状态")
        self.action_label = _dialog_label("什么时候开始管")
        self.repeat_label = _dialog_label("是否重复")
        self.deadline_label = _dialog_label("最晚处理日（可选）")
        self.repeat_until_label = _dialog_label("重复至（可选）")
        self.note_label = _dialog_label("备注")
        form.addRow(self.title_label, self.title_input)
        form.addRow(self.lifecycle_label, self.lifecycle_combo)
        form.addRow(self.action_label, self.action_date)
        form.addRow(self.repeat_label, self.repeat_combo)
        form.addRow(self.repeat_param_label, self.repeat_param_widget)
        form.addRow(self.deadline_label, self.deadline_widget)
        form.addRow(self.repeat_until_label, self.repeat_until_widget)
        form.addRow(self.note_label, self.note_input)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.repeat_combo.currentIndexChanged.connect(self._update_repeat_parameter)
        self.repeat_combo.currentIndexChanged.connect(self._update_deadline_controls)
        self.repeat_combo.currentIndexChanged.connect(self._update_repeat_until_controls)
        self.action_date.dateChanged.connect(self._update_deadline_controls)
        self.deadline_combo.currentIndexChanged.connect(self._update_deadline_controls)
        self.repeat_until_combo.currentIndexChanged.connect(self._update_repeat_until_controls)
        if reminder is not None:
            self._load(reminder)
        self._update_repeat_parameter()
        self._update_deadline_controls()
        self._update_repeat_until_controls()

    def draft(self) -> ReminderDraft:
        action = self._pydate(self.action_date.date())
        action_spec = self._action_spec(action)
        repeat_until = self._repeat_until()
        if repeat_until is not None and is_recurring(action_spec):
            action_spec = {**action_spec, "repeat_until": repeat_until.isoformat()}
        return ReminderDraft(
            title=self.title_input.text(),
            lifecycle=str(self.lifecycle_combo.currentData()),
            action_spec=action_spec,
            deadline_spec=self._deadline_spec(action_spec),
            note=self.note_input.toPlainText(),
            table_id=self.table_id,
        )

    def _action_spec(self, action: date) -> dict:
        mode = str(self.repeat_combo.currentData())
        value = int(self.repeat_number.value())
        if mode == "one_time":
            return {"type": "one_time", "date": action.isoformat()}
        if mode == "monthly_day":
            return {"type": "monthly_day", "day": value}
        if mode == "yearly_date":
            return {"type": "yearly_date", "month": action.month, "day": action.day}
        if mode == "interval_days":
            return {"type": "interval_days", "anchor_date": action.isoformat(), "days": value}
        if mode == "interval_from_completion":
            return {"type": "interval_from_completion", "anchor_date": action.isoformat(), "days": value}
        if mode == "nth_weekday_monthly":
            nth = ((action.day - 1) // 7) + 1
            return {"type": "nth_weekday", "nth": nth, "weekday": action.weekday()}
        if mode == "nth_weekday_yearly":
            nth = ((action.day - 1) // 7) + 1
            return {
                "type": "nth_weekday",
                "nth": nth,
                "weekday": action.weekday(),
                "month": action.month,
            }
        if mode == "lunar_yearly":
            return {"type": "lunar_yearly", "month": action.month, "day": action.day, "leap_month": False}
        return {"type": "cn_a_share_on_or_after", "day": value}

    def _load(self, reminder: Reminder) -> None:
        self.title_input.setText(reminder.title)
        self.note_input.setPlainText(reminder.note)
        lifecycle_index = self.lifecycle_combo.findData(reminder.lifecycle)
        self.lifecycle_combo.setCurrentIndex(max(0, lifecycle_index))
        spec = reminder.action_spec
        spec_type = str(spec.get("type", "one_time"))
        mode = spec_type
        if spec_type == "nth_weekday":
            mode = "nth_weekday_yearly" if spec.get("month") else "nth_weekday_monthly"
        index = self.repeat_combo.findData(mode)
        self.repeat_combo.setCurrentIndex(max(0, index))
        self.repeat_number.setValue(int(spec.get("days", spec.get("day", 1))))
        action = _date_from_spec(spec)
        self.action_date.setDate(QDate(action.year, action.month, action.day))
        repeat_until = spec.get("repeat_until")
        if repeat_until:
            self.repeat_until_combo.setCurrentIndex(
                max(0, self.repeat_until_combo.findData("date"))
            )
            until = date.fromisoformat(str(repeat_until))
            self.repeat_until_date.setDate(QDate(until.year, until.month, until.day))
        else:
            self.repeat_until_combo.setCurrentIndex(
                max(0, self.repeat_until_combo.findData("never"))
            )
        self._load_deadline(reminder.deadline_spec, action)

    def _update_repeat_parameter(self) -> None:
        mode = str(self.repeat_combo.currentData())
        if mode == "one_time":
            self._set_repeat_parameter("", "", False)
        elif mode == "monthly_day":
            self.repeat_number.setRange(1, 31)
            if self.repeat_number.value() == 1:
                self.repeat_number.setValue(self.action_date.date().day())
            self._set_repeat_parameter("每月第", "日", True)
        elif mode == "interval_days":
            self.repeat_number.setRange(1, 3660)
            self._set_repeat_parameter("间隔天数", "天", True)
        elif mode == "interval_from_completion":
            self.repeat_number.setRange(1, 3660)
            self._set_repeat_parameter("实际完成后再间隔", "天", True)
        elif mode == "cn_a_share_on_or_after":
            self.repeat_number.setRange(1, 31)
            if self.repeat_number.value() == 1:
                self.repeat_number.setValue(self.action_date.date().day())
            self._set_repeat_parameter("每月第", "日或之后首个交易日", True)
        else:
            self._set_repeat_parameter("", "", False)

    def _set_repeat_parameter(
        self,
        label: str,
        unit: str,
        visible: bool,
    ) -> None:
        self.repeat_param_label.setText(label)
        self.repeat_param_unit.setText(unit)
        self.form.setRowVisible(self.repeat_param_label, visible)
        self._fit_dynamic_layout()

    def _deadline_spec(self, action_spec: dict) -> dict | None:
        if str(self.deadline_combo.currentData()) != "set":
            return None
        action = self._pydate(self.action_date.date())
        deadline = self._pydate(self.deadline_date.date())
        offset_days = max(0, (deadline - action).days)
        mode = str(self.repeat_combo.currentData())
        if mode == "one_time":
            return {"type": "one_time", "date": deadline.isoformat()}
        if offset_days == 0:
            return {"type": "same_day"}
        return {"type": "offset_days", "days": offset_days}

    def _repeat_until(self) -> date | None:
        if str(self.repeat_until_combo.currentData()) != "date":
            return None
        return self._pydate(self.repeat_until_date.date())

    def _load_deadline(self, spec: dict | None, action: date) -> None:
        if not spec:
            self.deadline_combo.setCurrentIndex(
                max(0, self.deadline_combo.findData("none"))
            )
            return
        kind = str(spec.get("type", ""))
        if kind == "one_time":
            self.deadline_combo.setCurrentIndex(
                max(0, self.deadline_combo.findData("set"))
            )
            deadline = _date_from_spec(spec)
            self.deadline_date.setDate(QDate(deadline.year, deadline.month, deadline.day))
        elif kind in {"same_day", "offset_days", "monthly_day", "yearly_date"}:
            self.deadline_combo.setCurrentIndex(
                max(0, self.deadline_combo.findData("set"))
            )
            deadline = deadline_for(action, spec) or action
            self.deadline_date.setDate(QDate(deadline.year, deadline.month, deadline.day))
        else:
            self.deadline_combo.setCurrentIndex(
                max(0, self.deadline_combo.findData("none"))
            )

    def _update_deadline_controls(self) -> None:
        action = self._pydate(self.action_date.date())
        q_action = QDate(action.year, action.month, action.day)
        self.deadline_date.setMinimumDate(q_action)
        if self.deadline_date.date() < q_action:
            self.deadline_date.setDate(q_action)
        setting = str(self.deadline_combo.currentData()) == "set"
        self.deadline_combo.setEnabled(True)
        self.deadline_date_label.setVisible(setting)
        self.deadline_date.setVisible(setting)
        monthly = False
        self.deadline_prefix.setVisible(monthly)
        self.deadline_day.setVisible(monthly)
        self.deadline_unit.setVisible(monthly)
        self._fit_dynamic_layout()

    def _update_repeat_until_controls(self) -> None:
        recurring = str(self.repeat_combo.currentData()) != "one_time"
        self.form.setRowVisible(self.repeat_until_label, recurring)
        date_visible = recurring and str(self.repeat_until_combo.currentData()) == "date"
        self.repeat_until_date_label.setVisible(date_visible)
        self.repeat_until_date.setVisible(date_visible)
        self._fit_dynamic_layout()

    def _fit_dynamic_layout(self) -> None:
        self.layout().activate()
        self.adjustSize()

    @staticmethod
    def _pydate(value: QDate) -> date:
        return date(value.year(), value.month(), value.day())


def _date_from_spec(spec: dict) -> date:
    if spec.get("date"):
        return date.fromisoformat(str(spec["date"]))
    if spec.get("anchor_date"):
        return date.fromisoformat(str(spec["anchor_date"]))
    if str(spec.get("type", "")) == "nth_weekday":
        # nth/weekday 由日期框反推，必须回填一个真实落在“第 N 个星期几”上的日期。
        uncapped = {key: value for key, value in spec.items() if key != "repeat_until"}
        return next_action_date(
            uncapped, on_or_after=datetime.now(SHANGHAI).date()
        ) or date.today()
    year = date.today().year
    return date(year, int(spec.get("month", date.today().month)), int(spec.get("day", 1)))


def _configure_date_edit(edit: QDateEdit) -> None:
    edit.setObjectName("reminderDateEdit")
    edit.setDisplayFormat("yyyy-MM-dd")
    edit.setCalendarPopup(True)
    locale = QLocale(QLocale.Chinese, QLocale.China)
    edit.setLocale(locale)
    calendar = edit.calendarWidget()
    if calendar is None:
        calendar = QCalendarWidget(edit)
        edit.setCalendarWidget(calendar)
    calendar.setObjectName("reminderCalendar")
    calendar.setLocale(locale)
    calendar.setFirstDayOfWeek(Qt.Monday)
    calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
    calendar.setSelectionMode(QCalendarWidget.SingleSelection)
    _apply_calendar_palette(calendar)
    view = calendar.findChild(QAbstractItemView)
    if view is not None:
        view.setObjectName("reminderCalendarView")
        view.setAutoFillBackground(True)
        view.viewport().setAutoFillBackground(True)
        view.viewport().setPalette(calendar.palette())
    month_button = calendar.findChild(QToolButton, "qt_calendar_monthbutton")
    if month_button is not None and month_button.menu() is not None:
        month_button.menu().setObjectName("reminderCalendarMonthMenu")
        month_button.menu().setPalette(calendar.palette())


def _apply_calendar_palette(calendar: QCalendarWidget) -> None:
    tokens = current_theme()
    palette = QPalette(calendar.palette())
    palette.setColor(QPalette.Window, QColor(tokens.surface))
    palette.setColor(QPalette.Base, QColor(tokens.surface))
    palette.setColor(QPalette.AlternateBase, QColor(tokens.surface_hover))
    palette.setColor(QPalette.Text, QColor(tokens.text))
    palette.setColor(QPalette.WindowText, QColor(tokens.text))
    palette.setColor(QPalette.Button, QColor(tokens.surface))
    palette.setColor(QPalette.ButtonText, QColor(tokens.text))
    palette.setColor(QPalette.Highlight, QColor(tokens.surface_selected))
    palette.setColor(QPalette.HighlightedText, QColor(tokens.text))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(tokens.disabled))
    palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor(tokens.disabled))
    calendar.setAutoFillBackground(True)
    calendar.setPalette(palette)


def _status_text(item: ReminderListItem) -> str:
    if item.status == "occurrence_completed" and item.next_action_date:
        return f"本次已完成 · 下次 {item.next_action_date}"
    return item.status_label


def _endpoint_host(endpoint: str) -> str:
    if not endpoint.strip():
        return "ntfy.sh"
    parsed = urlparse(endpoint if "://" in endpoint else f"https://{endpoint}")
    return parsed.netloc or parsed.path or "ntfy.sh"


def _settings_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("reminderSettingsLabel")
    return label


def _dialog_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("reminderDialogFieldLabel")
    return label
