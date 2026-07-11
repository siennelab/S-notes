from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.ai_targets import AI_TARGETS, AI_VISIBILITY_OPTIONS
from src.database import (
    accept_ai_receipt_inbox_item,
    create_ai_receipt_inbox_item,
    delete_ai_receipt_inbox_item,
    get_ai_receipt_inbox_item,
    list_ai_receipt_inbox_items,
    reject_ai_receipt_inbox_item,
)
from src.memory_receipt_importer import parse_ai_targets_text, parse_memory_receipt
from src.tag_manager import format_tags, parse_tags
from src.time_utils import format_display_datetime


class ReceiptElidedLabel(QLabel):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self.full_text = text
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        text = self.fontMetrics().elidedText(self.full_text, Qt.ElideRight, self.width())
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(self.rect(), Qt.AlignLeft | Qt.AlignVCenter, text)


class ReceiptCard(QFrame):
    toggleRequested = Signal(int)
    acceptRequested = Signal(int)
    rejectRequested = Signal(int)
    deleteRequested = Signal(int)

    def __init__(self, receipt: dict, parent=None) -> None:
        super().__init__(parent)
        self.receipt = receipt
        self.receipt_id = int(receipt["id"])
        self.expanded = False
        self.pending = receipt["status"] == "pending"
        self.setObjectName("receiptCard")
        self.setProperty("expanded", False)
        self.setCursor(Qt.PointingHandCursor)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._open_context_menu)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(7)

        top = QHBoxLayout()
        top.setSpacing(8)
        title_area = QVBoxLayout()
        title_area.setSpacing(3)
        self.title_label = ReceiptElidedLabel(receipt["title"] or "未命名回执")
        self.title_label.setObjectName("receiptCardTitle")
        title_area.addWidget(self.title_label)
        summary = ReceiptElidedLabel(
            f"来源: {receipt['source']} · 动作: {receipt['action']} · "
            f"{format_display_datetime(receipt['created_at'])}"
        )
        summary.setObjectName("receiptCardSummary")
        title_area.addWidget(summary)
        top.addLayout(title_area, stretch=1)
        self.accept_button = QPushButton("确认")
        self.accept_button.setObjectName("receiptAcceptButton")
        self.accept_button.clicked.connect(lambda: self.acceptRequested.emit(self.receipt_id))
        self.reject_button = QPushButton("拒绝")
        self.reject_button.setObjectName("receiptRejectButton")
        self.reject_button.clicked.connect(lambda: self.rejectRequested.emit(self.receipt_id))
        self.accept_button.setVisible(self.pending)
        self.reject_button.setVisible(self.pending)
        top.addWidget(self.accept_button)
        top.addWidget(self.reject_button)
        layout.addLayout(top)

        self.details = QWidget()
        self.details.setObjectName("receiptCardDetails")
        details_layout = QVBoxLayout(self.details)
        details_layout.setContentsMargins(0, 6, 0, 0)
        details_layout.setSpacing(7)

        self.title_input = QLineEdit(receipt["title"])
        details_layout.addWidget(self._row("标题", self.title_input))

        category_row = QHBoxLayout()
        category_row.setSpacing(6)
        self.main_input = QLineEdit(receipt["main_category"])
        self.sub_input = QLineEdit(receipt["sub_category"])
        self.sub_sub_input = QLineEdit(receipt["sub_sub_category"])
        for widget, placeholder in (
            (self.main_input, "主分类"),
            (self.sub_input, "子分类"),
            (self.sub_sub_input, "子子分类"),
        ):
            widget.setPlaceholderText(placeholder)
            category_row.addWidget(widget, stretch=1)
        details_layout.addLayout(category_row)

        self.tags_input = QLineEdit(format_tags(parse_tags(receipt["tags"])))
        self.tags_input.setPlaceholderText("标签")
        details_layout.addWidget(self._row("标签", self.tags_input))

        ai_row = QHBoxLayout()
        ai_row.setSpacing(8)
        ai_label = QLabel("AI 可见性")
        ai_label.setObjectName("receiptFieldLabel")
        ai_row.addWidget(ai_label)
        self.ai_visibility_combo = QComboBox()
        self.ai_visibility_combo.addItems(AI_VISIBILITY_OPTIONS)
        index = self.ai_visibility_combo.findText(receipt["ai_visibility"])
        self.ai_visibility_combo.setCurrentIndex(index if index >= 0 else 0)
        ai_row.addWidget(self.ai_visibility_combo, stretch=1)
        details_layout.addLayout(ai_row)

        targets_row = QHBoxLayout()
        targets_row.setSpacing(8)
        targets_label = QLabel("AI 对象")
        targets_label.setObjectName("receiptFieldLabel")
        targets_row.addWidget(targets_label)
        targets_widget = QWidget()
        targets_widget.setObjectName("receiptTargets")
        targets_layout = QGridLayout(targets_widget)
        targets_layout.setContentsMargins(0, 0, 0, 0)
        targets_layout.setHorizontalSpacing(8)
        targets_layout.setVerticalSpacing(4)
        self.ai_target_checks: dict[str, QCheckBox] = {}
        selected_targets = set(parse_ai_targets_text(receipt["ai_targets"]))
        for index, target in enumerate(AI_TARGETS):
            checkbox = QCheckBox(target)
            checkbox.setChecked(target in selected_targets)
            self.ai_target_checks[target] = checkbox
            targets_layout.addWidget(checkbox, index // 3, index % 3)
        targets_row.addWidget(targets_widget, stretch=1)
        details_layout.addLayout(targets_row)

        body_label = QLabel("正文预览")
        body_label.setObjectName("receiptFieldLabel")
        details_layout.addWidget(body_label)
        self.body_edit = QTextEdit()
        self.body_edit.setPlainText(receipt["body"])
        self.body_edit.setPlaceholderText("暂无正文内容")
        self.body_edit.setMinimumHeight(240)
        self.body_edit.setMaximumHeight(260)
        details_layout.addWidget(self.body_edit)

        editable = self.pending
        for widget in (
            self.title_input,
            self.main_input,
            self.sub_input,
            self.sub_sub_input,
            self.tags_input,
            self.ai_visibility_combo,
            self.body_edit,
        ):
            widget.setEnabled(editable)
        for checkbox in self.ai_target_checks.values():
            checkbox.setEnabled(editable)

        self.details.setVisible(False)
        layout.addWidget(self.details)

    def _row(self, label: str, widget: QWidget) -> QWidget:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        text = QLabel(label)
        text.setObjectName("receiptFieldLabel")
        text.setFixedWidth(42)
        row_layout.addWidget(text)
        row_layout.addWidget(widget, stretch=1)
        return row

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = expanded
        self.details.setVisible(expanded)
        self.setProperty("expanded", expanded)
        self.style().unpolish(self)
        self.style().polish(self)

    def form_fields(self) -> dict:
        return {
            "title": self.title_input.text().strip(),
            "source": self.receipt["source"].strip() or "未知",
            "action": self.receipt["action"].strip() or "新增",
            "main_category": self.main_input.text().strip() or "临时",
            "sub_category": self.sub_input.text().strip() or "无",
            "sub_sub_category": self.sub_sub_input.text().strip() or "无",
            "tags": format_tags(parse_tags(self.tags_input.text())),
            "ai_visibility": self.ai_visibility_combo.currentText(),
            "ai_targets": ", ".join(
                target for target, checkbox in self.ai_target_checks.items() if checkbox.isChecked()
            ),
            "body": self.body_edit.toPlainText(),
        }

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.toggleRequested.emit(self.receipt_id)
        super().mouseReleaseEvent(event)

    def _open_context_menu(self, position) -> None:
        menu = QMenu(self)
        delete_action = menu.addAction("删除")
        delete_action.setEnabled(self.receipt["status"] in {"pending", "rejected"})
        delete_action.triggered.connect(
            lambda checked=False, receipt_id=self.receipt_id: self.deleteRequested.emit(
                receipt_id
            )
        )
        menu.exec(self.mapToGlobal(position))


class ReceiptInboxDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.list_filter = "pending"
        self.expanded_receipt_id: int | None = None
        self.cards: dict[int, ReceiptCard] = {}
        self.setWindowTitle("AI 待确认收件箱")
        self.setModal(False)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.resize(600, 700)
        self.setMinimumSize(520, 420)
        self.setObjectName("receiptInboxDialog")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title = QLabel("AI 待确认收件箱")
        title.setObjectName("receiptInboxTitle")
        layout.addWidget(title)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.pending_filter_button = QPushButton("待确认（0）")
        self.pending_filter_button.setObjectName("receiptTabButton")
        self.pending_filter_button.clicked.connect(lambda: self.set_list_filter("pending"))
        self.processed_filter_button = QPushButton("已处理")
        self.processed_filter_button.setObjectName("receiptTabButton")
        self.processed_filter_button.clicked.connect(lambda: self.set_list_filter("processed"))
        toolbar.addWidget(self.pending_filter_button)
        toolbar.addWidget(self.processed_filter_button)
        toolbar.addStretch(1)
        self.import_button = QPushButton("导入剪贴板回执")
        self.import_button.setObjectName("receiptImportButton")
        self.import_button.clicked.connect(self.import_from_clipboard)
        toolbar.addWidget(self.import_button)
        layout.addLayout(toolbar)

        self.empty_state = QLabel("")
        self.empty_state.setObjectName("receiptEmptyState")
        self.empty_state.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.empty_state, stretch=1)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("receiptScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.cards_content = QWidget()
        self.cards_content.setObjectName("receiptScrollContent")
        self.cards_content.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.cards_layout = QVBoxLayout(self.cards_content)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(8)
        self.cards_layout.addStretch(1)
        self.scroll.setWidget(self.cards_content)
        layout.addWidget(self.scroll, stretch=1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.close_button = QPushButton("关闭")
        self.close_button.setObjectName("receiptCloseButton")
        self.close_button.clicked.connect(self.close)
        footer.addWidget(self.close_button)
        layout.addLayout(footer)

        self.refresh_receipt_list()

    def import_from_clipboard(self) -> None:
        text = QApplication.clipboard().text()
        if not text.strip():
            QMessageBox.information(self, "没有剪贴板文本", "剪贴板里没有可导入的文本。")
            return
        preview = ReceiptImportPreviewDialog(text, self)
        if preview.exec() != QDialog.Accepted:
            return
        import_text = preview.receipt_text()
        if not import_text.strip():
            QMessageBox.information(self, "没有可导入的文本", "预览框为空，不能解析导入。")
            return
        parsed = parse_memory_receipt(import_text)
        receipt = create_ai_receipt_inbox_item(parsed.to_record())
        self.set_list_filter("pending", refresh=False)
        self.expanded_receipt_id = receipt["id"]
        self.refresh_receipt_list()
        self.show()
        self.raise_()
        self.activateWindow()

    def set_list_filter(self, value: str, *, refresh: bool = True) -> None:
        self.list_filter = value
        self.expanded_receipt_id = None
        if refresh:
            self.refresh_receipt_list()

    def refresh_receipt_list(self) -> None:
        pending_receipts = list_ai_receipt_inbox_items(status="pending")
        pending_count = len(pending_receipts)
        self.pending_filter_button.setText(f"待确认（{pending_count}）")
        self._set_tab_state(self.pending_filter_button, self.list_filter == "pending")
        self._set_tab_state(self.processed_filter_button, self.list_filter == "processed")
        if self.list_filter == "pending":
            receipt_summaries = pending_receipts
        else:
            receipt_summaries = [
                receipt
                for receipt in list_ai_receipt_inbox_items(status=None)
                if receipt["status"] != "pending"
            ]
        receipts = []
        for summary in receipt_summaries:
            receipt = get_ai_receipt_inbox_item(summary["id"])
            if receipt is not None:
                receipts.append(receipt)

        self._clear_cards()
        for receipt in receipts:
            card = ReceiptCard(receipt, self.cards_content)
            card.toggleRequested.connect(self.toggle_card)
            card.acceptRequested.connect(self.accept_receipt)
            card.rejectRequested.connect(self.reject_receipt)
            card.deleteRequested.connect(self.confirm_delete_receipt)
            card.set_expanded(receipt["id"] == self.expanded_receipt_id)
            self.cards[receipt["id"]] = card
            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)

        empty = not receipts
        self.scroll.setVisible(not empty)
        self.empty_state.setVisible(empty)
        self.empty_state.setText(
            "当前没有待确认回执" if self.list_filter == "pending" else "当前没有已处理回执"
        )

    def _clear_cards(self) -> None:
        old_cards = list(self.cards.values())
        self.cards.clear()
        for card in old_cards:
            card.toggleRequested.disconnect(self.toggle_card)
            card.acceptRequested.disconnect(self.accept_receipt)
            card.rejectRequested.disconnect(self.reject_receipt)
            card.deleteRequested.disconnect(self.confirm_delete_receipt)
            self.cards_layout.removeWidget(card)
            card.setParent(None)
            card.deleteLater()

    def _set_tab_state(self, button: QPushButton, active: bool) -> None:
        button.setProperty("active", active)
        button.style().unpolish(button)
        button.style().polish(button)

    def toggle_card(self, receipt_id: int) -> None:
        self.expanded_receipt_id = None if self.expanded_receipt_id == receipt_id else receipt_id
        for card_id, card in self.cards.items():
            card.set_expanded(card_id == self.expanded_receipt_id)

    def accept_receipt(self, receipt_id: int) -> None:
        card = self.cards.get(receipt_id)
        if card is None:
            return
        try:
            accept_ai_receipt_inbox_item(receipt_id, card.form_fields())
        except Exception as error:
            QMessageBox.warning(self, "确认入库失败", str(error))
            self.refresh_receipt_list()
            return
        self.expanded_receipt_id = None
        self.refresh_receipt_list()
        QMessageBox.information(self, "已确认入库", "已确认入库。")

    def reject_receipt(self, receipt_id: int) -> None:
        try:
            reject_ai_receipt_inbox_item(receipt_id)
        except Exception as error:
            QMessageBox.warning(self, "拒绝失败", str(error))
            self.refresh_receipt_list()
            return
        self.expanded_receipt_id = None
        self.refresh_receipt_list()
        QMessageBox.information(self, "已拒绝", "已拒绝。")

    def confirm_delete_receipt(self, receipt_id: int) -> None:
        receipt = get_ai_receipt_inbox_item(receipt_id)
        if receipt is None:
            self.refresh_receipt_list()
            return
        message_box = QMessageBox(self)
        message_box.setWindowTitle("删除 AI 回执")
        message_box.setText("确定删除这条 AI 回执记录吗？这不会删除任何正式笔记。")
        delete_button = message_box.addButton("删除", QMessageBox.AcceptRole)
        message_box.addButton("取消", QMessageBox.RejectRole)
        message_box.setDefaultButton(delete_button)
        message_box.exec()
        if message_box.clickedButton() != delete_button:
            return
        try:
            delete_ai_receipt_inbox_item(receipt_id)
        except Exception as error:
            QMessageBox.warning(self, "删除失败", str(error))
            return
        self.expanded_receipt_id = None
        self.refresh_receipt_list()


class ReceiptImportPreviewDialog(QDialog):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("导入剪贴板回执")
        self.setModal(True)
        self.resize(720, 520)
        self.setMinimumSize(640, 420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title = QLabel("导入剪贴板回执")
        title.setObjectName("exportTitle")
        layout.addWidget(title)
        description = QLabel(
            "请确认下面是要导入的 S-notes 记忆回执。可以先修改，再解析导入。"
        )
        description.setWordWrap(True)
        description.setObjectName("metadataLabel")
        layout.addWidget(description)

        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(text)
        layout.addWidget(self.text_edit, stretch=1)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(cancel_button)
        import_button = QPushButton("解析并导入")
        import_button.clicked.connect(self.accept_if_not_empty)
        button_row.addWidget(import_button)
        layout.addLayout(button_row)

    def receipt_text(self) -> str:
        return self.text_edit.toPlainText()

    def accept_if_not_empty(self) -> None:
        if not self.receipt_text().strip():
            QMessageBox.information(self, "没有可导入的文本", "预览框为空，不能解析导入。")
            return
        self.accept()
