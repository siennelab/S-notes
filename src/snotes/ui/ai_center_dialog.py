"""R1-shaped AI Center receipt lifecycle over the R2 application owner."""

from __future__ import annotations

from datetime import datetime
from collections.abc import Sequence
from dataclasses import replace
from difflib import unified_diff
from shiboken6 import isValid

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QCloseEvent, QFontDatabase, QKeyEvent, QMouseEvent, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.snotes.application.ai_audit_service import AiAuditService
from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_receipt_inbox_service import (
    AiReceiptAcceptFields,
    AiReceiptInboxService,
)
from src.snotes.application.gale_service import GaleControlService
from src.snotes.application.reminder_service import ReminderService
from src.snotes.domain.ai_receipts import AiReceiptInboxItem
from src.snotes.domain.tags import format_tags, parse_tags
from src.snotes.ui.floating_combo import FloatingComboBox
from src.snotes.ui.theme import apply_selection_palette, current_theme, repolish


def _display_timestamp(value: str, empty_text: str) -> str:
    if not value:
        return empty_text
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return empty_text
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.strftime("%Y-%m-%d %H:%M")


AUDIT_OPERATION_LABELS = {
    "export": "导出",
    "read": "读取",
    "search": "搜索",
    "get_note": "读取笔记",
    "list_directories": "查看目录",
    "list_tags": "查看标签",
    "create": "新建笔记",
    "prepend": "前置追加",
    "append": "追加内容",
    "metadata": "修改资料",
    "restore": "恢复笔记",
    "operation_status": "查询操作状态",
}

AUDIT_STATUS_LABELS = {
    "success": "成功",
    "cancelled": "已取消",
    "failed": "失败",
    "created": "已创建",
    "updated": "已更新",
    "not_found": "未找到",
    "conflict": "发生冲突",
    "rejected": "已拒绝",
    "accepted": "已接受",
    "pending_confirmation": "待人工确认",
    "no_change": "无变化",
}

AUDIT_HANDLING_MODE_LABELS = {
    "read": "读取",
    "direct": "直接执行",
    "pending": "待人工确认",
    "human_approved": "人工确认",
    "human_rejected": "人工拒绝",
    "none": "无",
}

AUDIT_FAILURE_REASON_LABELS = {
    "unknown_actor": "未知 AI 对象",
    "redaction_unavailable": "脱敏规则不可用",
    "sensitive_confirmation_cancelled": "人工确认已取消",
    "package_write_failed": "资料包写入失败",
}


def _audit_display_text(
    value: object,
    labels: dict[str, str],
    *,
    unknown_label: str,
) -> str:
    text = str(value or "").strip()
    if not text:
        return "—"
    if text in labels:
        return labels[text]
    if any("\u4e00" <= character <= "\u9fff" for character in text):
        return text
    return f"{unknown_label}（{text}）"


class ReceiptCard(QFrame):
    toggleRequested = Signal(int)
    acceptRequested = Signal(int)
    discardRequested = Signal(int)
    deleteRequested = Signal(int)

    def __init__(
        self,
        receipt: AiReceiptInboxItem,
        service: AiReceiptInboxService,
        metadata: AiAccessMetadataService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.receipt = receipt
        self._preview_service = service
        self._preview_busy = False
        self.receipt_id = receipt.id
        self.setObjectName("receiptCard")
        self.setProperty("expanded", False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        top = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(3)
        self.title_label = QLabel(receipt.title or "未命名回执")
        self.title_label.setObjectName("receiptCardTitle")
        heading.addWidget(self.title_label)
        summary = (
            f"来源：{receipt.source or '未知'} · 状态：{receipt.status_label} · "
            f"{receipt.created_at.replace('T', ' ')[:19]}"
        )
        self.summary_label = QLabel(summary)
        self.summary_label.setObjectName("receiptCardSummary")
        self.summary_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        heading.addWidget(self.summary_label)
        top.addLayout(heading, stretch=1)

        self.accept_button = QPushButton("接受", self)
        self.accept_button.setObjectName("receiptAcceptButton")
        self.accept_button.clicked.connect(
            lambda: self.acceptRequested.emit(self.receipt_id)
        )
        self.discard_button = QPushButton("丢弃", self)
        self.discard_button.setObjectName("receiptRejectButton")
        self.discard_button.clicked.connect(
            lambda: self.discardRequested.emit(self.receipt_id)
        )
        self.delete_button = QPushButton("删除", self)
        self.delete_button.setObjectName("receiptRejectButton")
        self.delete_button.setToolTip("仅删除回执，不删除正式笔记")
        self.delete_button.clicked.connect(
            lambda: self.deleteRequested.emit(self.receipt_id)
        )
        self.accept_button.setVisible(receipt.pending)
        self.discard_button.setVisible(receipt.pending)
        self.delete_button.setVisible(receipt.processed)
        top.addWidget(self.accept_button)
        top.addWidget(self.discard_button)
        top.addWidget(self.delete_button)
        layout.addLayout(top)

        self.details = QWidget()
        self.details.setObjectName("receiptCardDetails")
        details = QVBoxLayout(self.details)
        details.setContentsMargins(0, 8, 0, 0)
        details.setSpacing(8)
        if receipt.parse_status != "parsed":
            parse_error = QLabel(
                "解析错误：" + (receipt.parse_error or "未提供具体错误")
            )
            parse_error.setObjectName("receiptParseErrorDetail")
            parse_error.setWordWrap(True)
            details.addWidget(parse_error)

        self.title_input = QLineEdit(receipt.title)
        details.addWidget(self._field("标题", self.title_input))
        category_row = QHBoxLayout()
        category_row.setSpacing(8)
        self.main_input = QLineEdit(receipt.main_category)
        self.main_input.setPlaceholderText("主分类")
        self.sub_input = QLineEdit(receipt.sub_category)
        self.sub_input.setPlaceholderText("子分类")
        category_row.addWidget(QLabel("分类"))
        category_row.addWidget(self.main_input, stretch=1)
        category_row.addWidget(self.sub_input, stretch=1)
        details.addLayout(category_row)

        self.tags_input = QLineEdit(format_tags(parse_tags(receipt.tags)))
        self.tags_input.setPlaceholderText("标签")
        details.addWidget(self._field("标签", self.tags_input))

        visibility_row = QHBoxLayout()
        visibility_row.addWidget(QLabel("AI 可见性"))
        self.visibility_combo = FloatingComboBox()
        self.visibility_combo.configure_popup(220)
        for code, label in metadata.available_visibility_options():
            self.visibility_combo.addItem(label, code)
        visibility_index = self.visibility_combo.findData(receipt.ai_visibility)
        self.visibility_combo.setCurrentIndex(
            visibility_index if visibility_index >= 0 else 0
        )
        visibility_row.addWidget(self.visibility_combo, stretch=1)
        details.addLayout(visibility_row)

        target_state = service.receipt_target_state(receipt)
        targets_row = QHBoxLayout()
        targets_row.setSpacing(10)
        targets_row.addWidget(QLabel("AI 对象"))
        self.target_checks: dict[str, QCheckBox] = {}
        for definition in metadata.available_targets():
            checkbox = QCheckBox(definition.display_name)
            checkbox.setProperty("targetId", definition.target_id)
            checkbox.setChecked(definition.target_id in target_state.active_target_ids)
            self.target_checks[definition.target_id] = checkbox
            targets_row.addWidget(checkbox)
        self.legacy_target_ids = target_state.legacy_target_ids
        self.legacy_targets_label = QLabel(
            "历史授权：" + " / ".join(target_state.legacy_target_names)
            if target_state.legacy_target_names
            else ""
        )
        self.legacy_targets_label.setObjectName("aiLegacyTargetsLabel")
        self.legacy_targets_label.setVisible(bool(target_state.legacy_target_names))
        targets_row.addWidget(self.legacy_targets_label)
        targets_row.addStretch(1)
        details.addLayout(targets_row)

        self.has_final_preview = bool(
            receipt.pending and receipt.request_id and receipt.operation_type == "append"
        )
        body_label = QLabel("追加内容" if self.has_final_preview else ("正文" if receipt.pending else "正文记录"))
        body_label.setObjectName("receiptFieldLabel")
        details.addWidget(body_label)
        self.body_edit = QTextEdit()
        self.body_edit.setPlainText(receipt.body)
        self.body_edit.setMinimumHeight(210)
        details.addWidget(self.body_edit)

        if self.has_final_preview:
            self._preview_timer = QTimer(self)
            self._preview_timer.setSingleShot(True)
            self._preview_timer.timeout.connect(self.refresh_final_preview)
            preview_label = QLabel("最终 canonical 落库预览（写入接入待 2C）")
            details.addWidget(preview_label)
            self.preview_status = QLabel("展开后生成完整正文差异；修改追加内容后请刷新预览。")
            self.preview_status.setWordWrap(True)
            details.addWidget(self.preview_status)
            self.legacy_warning = QLabel(
                "此最终结果除追加内容外，还会规范化这篇旧笔记的既有 Markdown。"
            )
            self.legacy_warning.setObjectName("receiptLegacyCanonicalizationWarning")
            self.legacy_warning.setWordWrap(True)
            self.legacy_warning.hide()
            details.addWidget(self.legacy_warning)
            self.preview_refresh = QPushButton("刷新最终预览")
            self.preview_refresh.clicked.connect(self.refresh_final_preview)
            details.addWidget(self.preview_refresh)
            self.final_preview = QTextEdit()
            self.final_preview.setObjectName("receiptFinalCanonicalDiff")
            self.final_preview.setReadOnly(True)
            self.final_preview.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
            self.final_preview.setLineWrapMode(QTextEdit.NoWrap)
            self.final_preview.setMinimumHeight(240)
            details.addWidget(self.final_preview)
            self.body_edit.textChanged.connect(self.invalidate_final_preview)

        if receipt.note_id is not None:
            linked = QLabel(f"关联正式笔记：#{receipt.note_id}")
            linked.setObjectName("receiptLinkedNote")
            details.addWidget(linked)

        for widget in (
            self.title_input,
            self.main_input,
            self.sub_input,
            self.tags_input,
            self.visibility_combo,
            self.body_edit,
            *self.target_checks.values(),
        ):
            widget.setEnabled(receipt.pending)
        self.details.setVisible(False)
        layout.addWidget(self.details)

    @staticmethod
    def _field(label: str, widget: QWidget) -> QWidget:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        text = QLabel(label)
        text.setObjectName("receiptFieldLabel")
        text.setFixedWidth(70)
        row_layout.addWidget(text)
        row_layout.addWidget(widget, stretch=1)
        return row

    def set_expanded(self, expanded: bool) -> None:
        self.details.setVisible(expanded)
        self.setProperty("expanded", expanded)
        repolish(self)
        if self.has_final_preview:
            if expanded:
                # Finish building/replacing cards before Milkdown processes Qt events.
                self._preview_timer.start(0)
            else:
                self._preview_timer.stop()

    def invalidate_final_preview(self) -> None:
        self.final_preview.clear()
        self.legacy_warning.hide()
        self.preview_status.setText("追加内容已改变，请刷新最终预览。")

    def refresh_final_preview(self) -> None:
        if self._preview_busy:
            return
        self._preview_timer.stop()
        self._preview_busy = True
        self.preview_refresh.setEnabled(False)
        self.final_preview.clear()
        self.legacy_warning.hide()
        self.preview_status.setText("正在生成完整正文差异…")
        fragment = self.body_edit.toPlainText()
        try:
            preview = self._preview_service.build_pending_note_preview(
                self.receipt_id, addition_markdown=fragment,
            )
            if not isValid(self):
                return
            if fragment != self.body_edit.toPlainText():
                self.invalidate_final_preview()
                return
            # Retain line terminators so a missing final newline is a visible diff.
            chunks = unified_diff(
                preview.before_markdown.splitlines(keepends=True),
                preview.final_markdown.splitlines(keepends=True),
                fromfile="当前完整正文", tofile="最终 canonical 正文",
                n=max(len(preview.before_markdown.splitlines()), len(preview.final_markdown.splitlines())),
            )
            diff = "".join(
                line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                for line in chunks
            )
            self.final_preview.setPlainText(diff or "正文无差异。")
            self.legacy_warning.setVisible(preview.legacy_canonicalization_changed)
            self.preview_status.setText("完整正文差异：− 为删除，+ 为新增。预览不会保存正文。")
        except ValueError as error:
            if isValid(self):
                self.preview_status.setText("预览不可用：" + str(error))
        except Exception:
            if isValid(self):
                self.preview_status.setText("预览失败：无法生成稳定的 canonical 正文，请刷新后重试。")
        finally:
            self._preview_busy = False
            if isValid(self):
                self.preview_refresh.setEnabled(True)

    def form_fields(self) -> AiReceiptAcceptFields:
        active_ids = tuple(
            target_id
            for target_id, checkbox in self.target_checks.items()
            if checkbox.isChecked()
        )
        return AiReceiptAcceptFields(
            title=self.title_input.text(),
            body=self.body_edit.toPlainText(),
            main_category=self.main_input.text(),
            sub_category=self.sub_input.text(),
            tags=self.tags_input.text(),
            visibility=self.visibility_combo.currentData(),
            target_ids=active_ids + self.legacy_target_ids,
        )

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.toggleRequested.emit(self.receipt_id)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in {Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space}:
            self.toggleRequested.emit(self.receipt_id)
            event.accept()
            return
        super().keyPressEvent(event)


class ReceiptInboxView(QWidget):
    receiptAccepted = Signal(int)
    pendingCountChanged = Signal(int)

    def __init__(
        self,
        service: AiReceiptInboxService,
        metadata: AiAccessMetadataService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.metadata = metadata
        self.list_filter = "pending"
        self.expanded_receipt_id: int | None = None
        self.cards: dict[int, ReceiptCard] = {}
        self.scroll: QScrollArea | None = None
        self._delete_message_box: QMessageBox | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        toolbar = QHBoxLayout()
        self.pending_button = QPushButton("待确认（0）")
        self.pending_button.setObjectName("receiptTabButton")
        self.processed_button = QPushButton("已处理")
        self.processed_button.setObjectName("receiptTabButton")
        self.pending_button.clicked.connect(lambda: self.set_filter("pending"))
        self.processed_button.clicked.connect(lambda: self.set_filter("processed"))
        toolbar.addWidget(self.pending_button)
        toolbar.addWidget(self.processed_button)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)
        self.stack = QStackedWidget()
        self.empty_state = QLabel()
        self.empty_state.setObjectName("receiptEmptyState")
        self.empty_state.setAlignment(Qt.AlignCenter)
        self.stack.addWidget(self.empty_state)
        layout.addWidget(self.stack, stretch=1)
        self.refresh_receipts()

    @property
    def delete_confirmation_box(self) -> QMessageBox | None:
        return self._delete_message_box

    def set_filter(self, value: str) -> None:
        self.list_filter = value
        self.expanded_receipt_id = None
        self.refresh_receipts()
        self._activate_host_window()

    def refresh_receipts(self) -> None:
        pending = self.service.list_pending_receipts()
        self.pending_button.setText(f"待确认（{len(pending)}）")
        self.pendingCountChanged.emit(len(pending))
        self._set_active(self.pending_button, self.list_filter == "pending")
        self._set_active(self.processed_button, self.list_filter == "processed")
        receipts = (
            pending
            if self.list_filter == "pending"
            else self.service.list_processed_receipts()
        )
        if not receipts:
            self._show_empty()
            return
        if self._matches_rendered_receipts(receipts):
            return
        new_scroll, new_cards = self._build_cards(receipts)
        old_scroll = self.scroll
        self.scroll = new_scroll
        self.cards = new_cards
        self.stack.addWidget(new_scroll)
        self.stack.setCurrentWidget(new_scroll)
        if old_scroll is not None:
            self.stack.removeWidget(old_scroll)
            old_scroll.setEnabled(False)
            QTimer.singleShot(0, old_scroll.deleteLater)

    def _matches_rendered_receipts(
        self,
        receipts: Sequence[AiReceiptInboxItem],
    ) -> bool:
        ids = [receipt.id for receipt in receipts]
        return (
            self.scroll is not None
            and list(self.cards) == ids
            and all(
                self.cards[receipt_id].receipt == receipt
                for receipt_id, receipt in zip(ids, receipts, strict=True)
            )
        )

    def _build_cards(
        self,
        receipts: Sequence[AiReceiptInboxItem],
    ) -> tuple[QScrollArea, dict[int, ReceiptCard]]:
        scroll = QScrollArea()
        scroll.setObjectName("receiptScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        content.setObjectName("receiptScrollContent")
        content.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        cards: dict[int, ReceiptCard] = {}
        for receipt in receipts:
            card = ReceiptCard(receipt, self.service, self.metadata, content)
            card.toggleRequested.connect(self.toggle_card)
            card.acceptRequested.connect(self.accept_receipt)
            card.discardRequested.connect(self.discard_receipt)
            card.deleteRequested.connect(self.confirm_delete_receipt)
            card.set_expanded(receipt.id == self.expanded_receipt_id)
            cards[receipt.id] = card
            layout.addWidget(card)
        layout.addStretch(1)
        scroll.setWidget(content)
        return scroll, cards

    def _show_empty(self) -> None:
        old_scroll = self.scroll
        self.scroll = None
        self.cards = {}
        self.empty_state.setText(
            "当前没有待确认回执"
            if self.list_filter == "pending"
            else "当前没有已处理回执"
        )
        self.stack.setCurrentWidget(self.empty_state)
        if old_scroll is not None:
            self.stack.removeWidget(old_scroll)
            old_scroll.setEnabled(False)
            QTimer.singleShot(0, old_scroll.deleteLater)

    @staticmethod
    def _set_active(button: QPushButton, active: bool) -> None:
        button.setProperty("active", active)
        repolish(button)

    def toggle_card(self, receipt_id: int) -> None:
        self.expanded_receipt_id = (
            None if self.expanded_receipt_id == receipt_id else receipt_id
        )
        for card_id, card in self.cards.items():
            card.set_expanded(card_id == self.expanded_receipt_id)

    def accept_receipt(self, receipt_id: int) -> None:
        card = self.cards.get(receipt_id)
        if card is None:
            return
        fields = card.form_fields()
        if card.receipt.parse_status != "parsed":
            box = QMessageBox(self)
            box.setWindowTitle("人工确认解析失败回执")
            box.setText(
                "这条回执解析失败。请确认你已经检查标题、分类、正文和 AI 设置。"
            )
            continue_button = box.addButton("仍然入库", QMessageBox.AcceptRole)
            box.addButton("取消", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is not continue_button:
                return
            fields = replace(fields, allow_failed_parse=True)
        try:
            result = self.service.accept_receipt(receipt_id, fields)
        except Exception as error:
            QMessageBox.warning(self.window(), "接受失败", str(error))
            self._activate_host_window()
            return
        self.expanded_receipt_id = None
        self.refresh_receipts()
        self.receiptAccepted.emit(result.note.id)
        self._activate_host_window()

    def discard_receipt(self, receipt_id: int) -> None:
        try:
            self.service.discard_receipt(receipt_id)
        except Exception as error:
            QMessageBox.warning(self.window(), "丢弃失败", str(error))
            self._activate_host_window()
            return
        self.expanded_receipt_id = None
        self.refresh_receipts()
        self._activate_host_window()

    def confirm_delete_receipt(self, receipt_id: int) -> None:
        if self._delete_message_box is not None:
            self._delete_message_box.raise_()
            self._delete_message_box.activateWindow()
            return
        box = QMessageBox(self)
        box.setWindowTitle("删除 AI 回执")
        box.setText("确定删除这条 AI 回执吗？这不会删除任何正式笔记。")
        delete_button = box.addButton("删除", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(delete_button)
        box.setWindowModality(Qt.WindowModal)
        self._delete_message_box = box

        def finish(_result: int) -> None:
            should_delete = box.clickedButton() is delete_button
            self._delete_message_box = None
            box.deleteLater()
            if not should_delete:
                return
            result = self.service.delete_processed_receipt(receipt_id)
            if not result.deleted:
                QMessageBox.warning(self.window(), "删除失败", result.message)
                self._activate_host_window()
                return
            self.expanded_receipt_id = None
            self.refresh_receipts()
            self._activate_host_window()

        box.finished.connect(finish)
        box.open()

    def _activate_host_window(self) -> None:
        host = self.window()
        if host.isVisible():
            host.raise_()
            host.activateWindow()


class _GaleWorker(QObject):
    finished = Signal(object, object, bool)

    def __init__(self, action, *, refresh_receipts: bool) -> None:
        super().__init__()
        self.action = action
        self.refresh_receipts = refresh_receipts

    @Slot()
    def run(self) -> None:
        try:
            result = self.action()
        except Exception as error:
            self.finished.emit(
                None,
                str(getattr(error, "code", "unexpected_failure")),
                self.refresh_receipts,
            )
            return
        self.finished.emit(result, None, self.refresh_receipts)


class AiCenterDialog(QWidget):
    finished = Signal(int)
    receiptAccepted = Signal(int)
    pendingCountChanged = Signal(int)

    def __init__(
        self,
        service: AiReceiptInboxService,
        metadata: AiAccessMetadataService,
        audit: AiAuditService | None = None,
        reminders: ReminderService | None = None,
        parent: QWidget | None = None,
        *,
        gale: GaleControlService | None = None,
    ) -> None:
        super().__init__(parent, Qt.Window)
        self.setObjectName("aiCenterDialog")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowTitle("AI中心")
        self.resize(900, 700)
        self.setMinimumSize(720, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self.audit = audit
        self.reminders = reminders
        self.reminder_log_offset = 0
        self.reminder_log_limit = 50
        self.gale = gale
        self._gale_threads: list[QThread] = []
        self._gale_workers: list[_GaleWorker] = []
        title = QLabel("AI中心")
        title.setObjectName("receiptInboxTitle")
        layout.addWidget(title)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("aiCenterTabs")
        self.receipt_view = ReceiptInboxView(service, metadata, self)
        self.receipt_view.receiptAccepted.connect(self.receiptAccepted)
        self.receipt_view.pendingCountChanged.connect(self.pendingCountChanged)
        self.tabs.addTab(self.receipt_view, "待确认 / 已处理")
        self.log_page = self._build_log_page()
        self.tabs.addTab(self.log_page, "笔记调用记录")
        self.reminder_log_page = self._build_reminder_log_page()
        self.tabs.addTab(self.reminder_log_page, "提醒操作记录")
        self.gale_page = None
        self.tabs.currentChanged.connect(self._tab_changed)
        layout.addWidget(self.tabs, stretch=1)
        if self.gale is not None:
            self.gale_page = self._build_gale_page()
            layout.addWidget(self.gale_page)
            self.refresh_gale_runtime_status()
        footer = QHBoxLayout()
        footer.addStretch(1)
        self.close_button = QPushButton("关闭")
        self.close_button.setObjectName("aiCenterCloseButton")
        self.close_button.clicked.connect(self.close)
        footer.addWidget(self.close_button)
        layout.addLayout(footer)
        self.refresh_theme()

    def _build_gale_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("galeSettingsPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(12)
        self.gale_status = QLabel("Gale 后台接线未启用" if self.gale is None else "就绪")
        self.gale_status.setObjectName("galeStatusLabel")
        layout.addWidget(self.gale_status)
        controls = QGridLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setHorizontalSpacing(16)
        controls.setVerticalSpacing(10)
        controls.setColumnStretch(0, 0)
        controls.setColumnStretch(1, 1)
        self.gale_controls_layout = controls
        self.gale_publish_button = QPushButton("立即更新镜像")
        self.gale_last_publish = QLabel("上次发布：尚未发布")
        self.gale_last_publish.setObjectName("galeLastPublishLabel")
        self.gale_pull_button = QPushButton("立即检查投稿")
        self.gale_last_pull = QLabel("上次检查：尚未检查")
        self.gale_last_pull.setObjectName("galeLastPullLabel")
        self.gale_auto_publish = QCheckBox("自动更新 Gale 安全镜像")
        self.gale_auto_pull = QCheckBox("自动接收 Gale 投稿")
        auto_publish_help = QLabel(
            "内容停止变化约 15 分钟后发布；持续变化时最长约 30 分钟"
        )
        auto_publish_help.setObjectName("galeAutoPublishHelp")
        auto_pull_help = QLabel(
            "后台每 5 分钟检查；登录时启动，睡眠期间暂停并在唤醒后按系统计划继续"
        )
        auto_pull_help.setObjectName("galeAutoPullHelp")
        for label in (
            self.gale_last_publish,
            self.gale_last_pull,
            auto_publish_help,
            auto_pull_help,
        ):
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            label.setWordWrap(True)
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        left_alignment = Qt.AlignLeft | Qt.AlignVCenter
        controls.addWidget(self.gale_publish_button, 0, 0, left_alignment)
        controls.addWidget(self.gale_last_publish, 0, 1)
        controls.addWidget(self.gale_pull_button, 1, 0, left_alignment)
        controls.addWidget(self.gale_last_pull, 1, 1)
        controls.addWidget(self.gale_auto_publish, 2, 0, left_alignment)
        controls.addWidget(auto_publish_help, 2, 1)
        controls.addWidget(self.gale_auto_pull, 3, 0, left_alignment)
        controls.addWidget(auto_pull_help, 3, 1)
        layout.addLayout(controls)
        layout.addStretch(1)
        enabled = self.gale is not None
        for widget in (
            self.gale_publish_button,
            self.gale_pull_button,
            self.gale_auto_publish,
            self.gale_auto_pull,
        ):
            widget.setEnabled(enabled)
        if self.gale is not None:
            self.gale_auto_publish.setChecked(self.gale.auto_publish_enabled())
            self.gale_auto_pull.setChecked(self.gale.auto_pull_enabled())
            self.gale_publish_button.clicked.connect(
                lambda: self._start_gale_action(
                    self.gale.manual_update, "正在更新 Gale 安全镜像…"
                )
            )
            self.gale_pull_button.clicked.connect(
                lambda: self._start_gale_action(
                    self.gale.check_submissions_now, "正在检查 Gale 投稿…",
                    refresh_receipts=True,
                )
            )
            self.gale_auto_publish.toggled.connect(
                self.gale.set_auto_publish_enabled
            )
            self.gale_auto_pull.toggled.connect(
                self.gale.set_auto_pull_enabled
            )
        return page

    def refresh_gale_runtime_status(self) -> None:
        if self.gale is None or self.gale_page is None:
            return
        self.gale_last_publish.setText(
            "上次发布："
            + _display_timestamp(
                self.gale.last_successful_publish_at(), "尚未发布"
            )
        )
        self.gale_last_pull.setText(
            "上次检查："
            + _display_timestamp(
                self.gale.last_completed_pull_at(), "尚未检查"
            )
        )

    def _start_gale_action(
        self,
        action,
        pending_text: str,
        *,
        refresh_receipts: bool = False,
    ) -> None:
        self.gale_status.setText(pending_text)
        self.gale_publish_button.setEnabled(False)
        self.gale_pull_button.setEnabled(False)
        thread = QThread(self)
        worker = _GaleWorker(
            action,
            refresh_receipts=refresh_receipts,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        worker.finished.connect(self._gale_action_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        def release() -> None:
            if thread in self._gale_threads:
                self._gale_threads.remove(thread)
            if worker in self._gale_workers:
                self._gale_workers.remove(worker)

        thread.finished.connect(release)
        self._gale_threads.append(thread)
        self._gale_workers.append(worker)
        thread.start()

    @Slot(object, object, bool)
    def _gale_action_finished(
        self,
        _result,
        error,
        refresh_receipts: bool,
    ) -> None:
        self.gale_status.setText(
            "操作完成" if error is None else f"操作失败：{error}"
        )
        self.gale_publish_button.setEnabled(True)
        self.gale_pull_button.setEnabled(True)
        if error is None and refresh_receipts:
            self.receipt_view.refresh_receipts()
        self.refresh_gale_runtime_status()

    def _build_log_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("aiCallLogPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(10)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("操作类型"))
        self.log_filter = FloatingComboBox()
        self.log_filter.setObjectName("aiCallLogFilter")
        self.log_filter.setFixedHeight(32)
        self.log_filter.setMinimumWidth(180)
        self.log_filter.configure_popup(200)
        self.log_filter.addItem("全部", None)
        if self.audit is not None:
            for operation_type in self.audit.operation_filter_options():
                self.log_filter.addItem(
                    _audit_display_text(
                        operation_type,
                        AUDIT_OPERATION_LABELS,
                        unknown_label="其他操作",
                    ),
                    operation_type,
                )
        self.log_filter.currentIndexChanged.connect(self.refresh_logs)
        toolbar.addWidget(self.log_filter)
        toolbar.addStretch(1)
        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(self.refresh_logs)
        toolbar.addWidget(refresh_button)
        layout.addLayout(toolbar)

        self.log_table = QTableWidget(0, 8)
        self.log_table.setObjectName("aiCallLogTable")
        self.log_table.setHorizontalHeaderLabels(
            [
                "时间",
                "来源",
                "操作",
                "请求 ID",
                "笔记 ID",
                "结果数",
                "处理方式",
                "状态/原因",
            ]
        )
        self.log_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.log_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.log_table.setAlternatingRowColors(False)
        self.log_table.verticalHeader().setVisible(False)
        self.log_table.horizontalHeader().setStretchLastSection(True)
        self.log_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Interactive
        )
        layout.addWidget(self.log_table, stretch=1)
        self.refresh_logs()
        return page

    def _tab_changed(self, index: int) -> None:
        if index == self.tabs.indexOf(self.log_page):
            self.refresh_logs()
        elif index == self.tabs.indexOf(self.reminder_log_page):
            self.refresh_reminder_logs(reset=True)
        else:
            self.receipt_view.refresh_receipts()

    def refresh_logs(self, *_args) -> None:
        rows = (
            self.audit.list_call_logs(self.log_filter.currentData())
            if self.audit is not None
            else ()
        )
        self.log_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            status = _audit_display_text(
                row.status,
                AUDIT_STATUS_LABELS,
                unknown_label="其他状态",
            )
            if row.failure_reason:
                status += " · " + _audit_display_text(
                    row.failure_reason,
                    AUDIT_FAILURE_REASON_LABELS,
                    unknown_label="其他原因",
                )
            if row.is_duplicate:
                status += " · 重复请求"
            values = (
                row.called_at.replace("T", " ")[:19],
                row.actor_display,
                _audit_display_text(
                    row.operation_type,
                    AUDIT_OPERATION_LABELS,
                    unknown_label="其他操作",
                ),
                row.request_id,
                row.note_id or "",
                row.result_count,
                _audit_display_text(
                    row.handling_mode,
                    AUDIT_HANDLING_MODE_LABELS,
                    unknown_label="其他方式",
                ),
                status,
            )
            for column, value in enumerate(values):
                self.log_table.setItem(
                    row_index,
                    column,
                    QTableWidgetItem(str(value)),
                )
        self.log_table.resizeColumnsToContents()

    def refresh_theme(self) -> None:
        for table in (self.log_table, self.reminder_log_table):
            self._refresh_log_table_theme(table)
        repolish(self)

    @staticmethod
    def _refresh_log_table_theme(table: QTableWidget) -> None:
        tokens = current_theme()
        palette = QPalette(table.palette())
        for role in (QPalette.Base, QPalette.Window, QPalette.Button):
            palette.setColor(role, QColor(tokens.surface))
        for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
            palette.setColor(role, QColor(tokens.text))
        palette.setColor(QPalette.AlternateBase, QColor(tokens.surface_hover))
        table.setPalette(palette)
        table.viewport().setAutoFillBackground(True)
        table.viewport().setPalette(palette)
        table.horizontalHeader().setPalette(palette)
        apply_selection_palette(table, tokens)
        apply_selection_palette(table.viewport(), tokens)
        repolish(table)
        repolish(table.viewport())
        repolish(table.horizontalHeader())

    def _build_reminder_log_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("aiReminderLogPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(10)
        toolbar = QHBoxLayout()
        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(lambda: self.refresh_reminder_logs(reset=True))
        toolbar.addWidget(refresh_button)
        self.reminder_log_more_button = QPushButton("加载更多")
        self.reminder_log_more_button.clicked.connect(lambda: self.refresh_reminder_logs(reset=False))
        toolbar.addWidget(self.reminder_log_more_button)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)
        self.reminder_log_empty = QLabel("暂无 Reminder 操作记录")
        self.reminder_log_empty.setObjectName("aiReminderLogEmpty")
        layout.addWidget(self.reminder_log_empty)
        self.reminder_log_table = QTableWidget(0, 10)
        self.reminder_log_table.setObjectName("aiReminderLogTable")
        self.reminder_log_table.setHorizontalHeaderLabels(
            ["时间", "来源", "动作", "Table ID", "Table", "Item ID", "Item", "标签", "变化字段", "请求 ID"]
        )
        self.reminder_log_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.reminder_log_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.reminder_log_table.setAlternatingRowColors(False)
        self.reminder_log_table.verticalHeader().setVisible(False)
        self.reminder_log_table.horizontalHeader().setStretchLastSection(True)
        self.reminder_log_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Interactive
        )
        layout.addWidget(self.reminder_log_table, stretch=1)
        self.refresh_reminder_logs(reset=True)
        return page

    def refresh_reminder_logs(self, *, reset: bool) -> None:
        if reset:
            self.reminder_log_offset = 0
            self.reminder_log_table.setRowCount(0)
        if self.reminders is None:
            self.reminder_log_more_button.setEnabled(False)
            return
        page = self.reminders.list_ai_operation_logs(
            offset=self.reminder_log_offset,
            limit=self.reminder_log_limit,
        )
        start = self.reminder_log_table.rowCount()
        self.reminder_log_table.setRowCount(start + len(page.rows))
        for index, row in enumerate(page.rows, start=start):
            values = (
                row.happened_at.replace("T", " ")[:19],
                row.actor_target_id,
                row.action,
                row.table_id or "",
                row.table_title,
                row.item_id or "",
                row.item_title,
                row.label_name,
                ", ".join(row.changed_fields),
                row.request_id,
            )
            for column, value in enumerate(values):
                self.reminder_log_table.setItem(
                    index,
                    column,
                    QTableWidgetItem(str(value)),
                )
        self.reminder_log_offset += len(page.rows)
        self.reminder_log_more_button.setEnabled(page.has_more)
        self.reminder_log_empty.setVisible(self.reminder_log_table.rowCount() == 0)
        self.reminder_log_table.setVisible(self.reminder_log_table.rowCount() > 0)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.finished.emit(0)
        super().closeEvent(event)
