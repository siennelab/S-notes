from __future__ import annotations

import json
from collections.abc import Callable

from PySide6.QtCore import QMetaObject, QSize, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QSizePolicy,
    QTabBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.ai_targets import AI_TARGET_DISPLAY_BY_ID
from src.receipt_inbox_dialog import ReceiptInboxDialog
from src.services.ai_access_service import (
    CREATE_CONFIRMATION_KEY,
    MODIFY_CONFIRMATION_KEY,
    get_ai_settings,
    list_ai_call_logs,
    list_ai_note_history,
    restore_ai_note_history,
    set_ai_setting,
)
from src.time_utils import format_display_datetime


class EqualWidthTabBar(QTabBar):
    """Keep all AI Center primary tabs equal across the available width."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setDrawBase(False)
        self.setExpanding(True)
        self.setUsesScrollButtons(False)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def tabSizeHint(self, index: int) -> QSize:
        hint = super().tabSizeHint(index)
        tab_count = self.count()
        if tab_count:
            available_width = self.width()
            if self.parentWidget() is not None:
                available_width = max(available_width, self.parentWidget().width())
            hint.setWidth(max(1, available_width // tab_count))
        return hint

    def minimumTabSizeHint(self, index: int) -> QSize:
        return self.tabSizeHint(index)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.updateGeometry()


class AiCenterDialog(QWidget):
    finished = Signal(int)

    def __init__(
        self,
        parent=None,
        *,
        auto_publish_enabled: bool = False,
        set_auto_publish_enabled: Callable[[bool], None] | None = None,
        publish_now: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent, Qt.Window)
        self._set_auto_publish_enabled = set_auto_publish_enabled
        self._publish_now = publish_now
        self._initial_auto_publish_enabled = bool(auto_publish_enabled)
        self.setObjectName("aiCenterDialog")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowTitle("AI中心")
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.resize(900, 700)
        self.setMinimumSize(720, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        title = QLabel("AI中心")
        title.setObjectName("receiptInboxTitle")
        layout.addWidget(title)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("aiCenterTabs")
        self.tabs.setTabBar(EqualWidthTabBar(self.tabs))
        self.tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.pending_page = ReceiptInboxDialog(self, embedded=True)
        self.tabs.addTab(self.pending_page, "待确认")
        self.tabs.addTab(self._build_history_page(), "修改历史")
        self.tabs.addTab(self._build_log_page(), "调用记录")
        self.tabs.addTab(self._build_settings_page(), "设置")
        self.tabs.currentChanged.connect(self.refresh_current_tab)
        layout.addWidget(self.tabs, stretch=1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.close_button = QPushButton("关闭")
        self.close_button.setObjectName("aiCenterCloseButton")
        self.close_button.clicked.connect(self.close)
        footer.addWidget(self.close_button)
        layout.addLayout(footer)

    def closeEvent(self, event) -> None:
        self.finished.emit(0)
        super().closeEvent(event)

    def refresh_current_tab(self, index: int) -> None:
        if index == 0:
            QMetaObject.invokeMethod(
                self,
                "_refresh_receipt_page",
                Qt.QueuedConnection,
            )
        elif index == 1:
            self.refresh_history()
        elif index == 2:
            self.refresh_logs()
        elif index == 3:
            self.refresh_settings()

    @Slot()
    def _refresh_receipt_page(self) -> None:
        try:
            self.pending_page.refresh_receipt_list()
        except Exception as error:
            QMessageBox.warning(self, "刷新待确认失败", str(error))
        finally:
            self._restore_top_level_activation()

    def _restore_top_level_activation(self) -> None:
        if not self.isVisible() or self.isActiveWindow():
            return
        self.raise_()
        self.activateWindow()

    def _build_history_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(8)
        notice = QLabel("恢复前会二次确认，并先保存当前版本。")
        notice.setObjectName("statusLabel")
        layout.addWidget(notice)

        splitter = QSplitter(Qt.Vertical)
        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels(
            ["时间", "笔记", "修改类型", "来源", "处理方式"]
        )
        self.history_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.history_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.history_table.itemSelectionChanged.connect(self.show_selected_history)
        splitter.addWidget(self.history_table)
        self.history_detail = QTextEdit()
        self.history_detail.setReadOnly(True)
        self.history_detail.setPlaceholderText("选择一条历史记录查看修改前内容")
        splitter.addWidget(self.history_detail)
        splitter.setSizes([320, 240])
        layout.addWidget(splitter, stretch=1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(self.refresh_history)
        actions.addWidget(refresh_button)
        self.restore_button = QPushButton("恢复选中版本")
        self.restore_button.clicked.connect(self.confirm_restore)
        actions.addWidget(self.restore_button)
        layout.addLayout(actions)
        self.history_rows: list[dict] = []
        self.refresh_history()
        return page

    def refresh_history(self) -> None:
        self.history_rows = list_ai_note_history()
        self.history_table.setRowCount(len(self.history_rows))
        for row_index, row in enumerate(self.history_rows):
            actor = AI_TARGET_DISPLAY_BY_ID.get(row["actor_target_id"], row["actor_target_id"])
            values = (
                format_display_datetime(row["modified_at"]), row["note_title"],
                row["modification_type"], actor, row["handling_mode"],
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.UserRole, row["id"])
                self.history_table.setItem(row_index, column, item)
        self.history_table.resizeColumnsToContents()
        self.history_detail.clear()

    def selected_history(self) -> dict | None:
        row_index = self.history_table.currentRow()
        if row_index < 0 or row_index >= len(self.history_rows):
            return None
        return self.history_rows[row_index]

    def show_selected_history(self) -> None:
        row = self.selected_history()
        if row is None:
            self.history_detail.clear()
            return
        snapshot = json.loads(row["before_snapshot"])
        metadata = (
            f"修改类型：{row['modification_type']}\n"
            f"请求 ID：{row['request_id']}\n"
            f"原目录：{snapshot.get('main_category', '')} / "
            f"{snapshot.get('sub_category', '')} / {snapshot.get('sub_sub_category', '')}\n"
            f"原标签：{', '.join(snapshot.get('tags', []))}\n\n"
        )
        self.history_detail.setPlainText(metadata)
        self.history_detail.append("—— 修改前正文 ——")
        self.history_detail.insertHtml(snapshot.get("content_html", ""))

    def confirm_restore(self) -> None:
        row = self.selected_history()
        if row is None:
            QMessageBox.information(self, "未选择", "请先选择一条修改历史。")
            return
        box = QMessageBox(self)
        box.setWindowTitle("恢复历史版本")
        box.setText(f"确定恢复“{row['note_title']}”的这个旧版本吗？")
        box.setInformativeText("恢复前会先保存当前版本，恢复失败不会改动当前笔记。")
        restore_button = box.addButton("恢复", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() != restore_button:
            return
        try:
            restore_ai_note_history(int(row["id"]))
        except Exception as error:
            QMessageBox.warning(self, "恢复失败", str(error))
            return
        self.refresh_history()
        QMessageBox.information(self, "恢复成功", "已恢复旧版本，并保留了恢复前快照。")

    def _build_log_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 12, 8, 8)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("操作类型"))
        self.log_filter = QComboBox()
        self.log_filter.addItems(
            ["全部", "search", "get_note", "list_directories", "list_tags", "create", "prepend", "append", "metadata", "restore"]
        )
        self.log_filter.currentTextChanged.connect(self.refresh_logs)
        toolbar.addWidget(self.log_filter)
        toolbar.addStretch(1)
        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(self.refresh_logs)
        toolbar.addWidget(refresh_button)
        layout.addLayout(toolbar)

        self.log_table = QTableWidget(0, 8)
        self.log_table.setHorizontalHeaderLabels(
            ["时间", "来源", "操作", "请求 ID", "笔记 ID", "结果数", "处理方式", "状态/原因"]
        )
        self.log_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.log_table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.log_table, stretch=1)
        self.refresh_logs()
        return page

    def refresh_logs(self, _value: str | None = None) -> None:
        selected = self.log_filter.currentText() if hasattr(self, "log_filter") else "全部"
        rows = list_ai_call_logs(operation_type=None if selected == "全部" else selected)
        self.log_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            actor = AI_TARGET_DISPLAY_BY_ID.get(row["actor_target_id"], row["actor_target_id"])
            status = row["status"]
            if row["failure_reason"]:
                status += f" · {row['failure_reason']}"
            if row["is_duplicate"]:
                status += " · 重复请求"
            values = (
                format_display_datetime(row["called_at"]), actor, row["operation_type"],
                row["request_id"], row["note_id"] or "", row["result_count"],
                row["handling_mode"], status,
            )
            for column, value in enumerate(values):
                self.log_table.setItem(row_index, column, QTableWidgetItem(str(value)))
        self.log_table.resizeColumnsToContents()

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 20, 16, 16)
        layout.setSpacing(16)
        self.create_confirmation = QCheckBox("AI新增笔记是否需人工确认")
        self.modify_confirmation = QCheckBox("AI修改笔记是否需人工确认")
        self.create_confirmation.toggled.connect(
            lambda enabled: set_ai_setting(CREATE_CONFIRMATION_KEY, enabled)
        )
        self.modify_confirmation.toggled.connect(
            lambda enabled: set_ai_setting(MODIFY_CONFIRMATION_KEY, enabled)
        )
        layout.addWidget(self.create_confirmation)
        layout.addWidget(self.modify_confirmation)
        self.confirmation_note = QLabel(
            "默认：AI 新增直接入库；AI 修改进入待确认。设置保存在本地 SQLite 数据库。"
        )
        self.confirmation_note.setWordWrap(True)
        self.confirmation_note.setObjectName("settingsHelpText")
        layout.addWidget(self.confirmation_note)

        remote_group = QWidget()
        remote_group.setObjectName("remoteAiAccessGroup")
        remote_layout = QVBoxLayout(remote_group)
        remote_layout.setContentsMargins(0, 0, 0, 0)
        remote_layout.setSpacing(10)
        self.remote_title = QLabel("远程 AI 访问")
        self.remote_title.setObjectName("settingsSectionTitle")
        remote_layout.addWidget(self.remote_title)

        self.remote_auto_update = QCheckBox("自动更新")
        self.remote_auto_update.setObjectName("remoteAutoUpdate")
        self.remote_auto_update.setChecked(self._initial_auto_publish_enabled)
        self.remote_auto_update.toggled.connect(self.handle_remote_auto_update)
        remote_layout.addWidget(self.remote_auto_update)

        update_now_row = QHBoxLayout()
        update_now_row.setContentsMargins(0, 0, 0, 0)
        update_now_row.setSpacing(16)
        update_now_row.addWidget(QLabel("立即更新"))
        update_now_row.addStretch(1)
        self.remote_update_now = QPushButton("立即更新")
        self.remote_update_now.setObjectName("remoteUpdateNow")
        self.remote_update_now.clicked.connect(self.handle_remote_update_now)
        update_now_row.addWidget(self.remote_update_now)
        remote_layout.addLayout(update_now_row)

        self.remote_note = QLabel("仅更新符合 AI 对象与 AI 可见性规则的内容")
        self.remote_note.setObjectName("settingsHelpText")
        self.remote_note.setWordWrap(True)
        remote_layout.addWidget(self.remote_note)
        layout.addWidget(remote_group)
        layout.addStretch(1)
        self.refresh_settings()
        return page

    def handle_remote_auto_update(self, enabled: bool) -> None:
        if self._set_auto_publish_enabled is not None:
            self._set_auto_publish_enabled(bool(enabled))

    def handle_remote_update_now(self) -> None:
        if self._publish_now is not None:
            self._publish_now()

    def set_remote_publish_enabled(self, enabled: bool) -> None:
        self.remote_update_now.setEnabled(bool(enabled))

    def refresh_settings(self) -> None:
        values = get_ai_settings()
        for checkbox, key in (
            (self.create_confirmation, CREATE_CONFIRMATION_KEY),
            (self.modify_confirmation, MODIFY_CONFIRMATION_KEY),
        ):
            checkbox.blockSignals(True)
            checkbox.setChecked(values[key])
            checkbox.blockSignals(False)
