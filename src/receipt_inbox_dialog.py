from __future__ import annotations

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QCloseEvent, QKeyEvent, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.ai_targets import AI_TARGETS, AI_VISIBILITY_OPTIONS, visibility_display
from src.database import (
    _connect,
    accept_ai_receipt_inbox_item,
    create_ai_receipt_inbox_item,
    get_ai_receipt_inbox_item,
    list_ai_receipt_inbox_items,
    reject_ai_receipt_inbox_item,
)
from src.floating_combo import FloatingComboBox
from src.memory_receipt_importer import parse_ai_targets_text, parse_memory_receipt
from src.tag_manager import format_tags, parse_tags
from src.time_utils import format_display_datetime
from src.theme import repolish
from src.services.ai_access_service import accept_ai_proposal, reject_ai_proposal


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
        self.parse_failed = receipt["parse_status"] != "parsed"
        self.is_gale_submission = (
            str(receipt.get("source", "")).strip().casefold() == "gale"
            and bool(receipt.get("external_submission_id") or receipt.get("idempotency_key"))
        )

        self.setObjectName("receiptCard")
        self.setProperty("expanded", False)
        self.setProperty("pressed", False)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        title_area = QVBoxLayout()
        title_area.setSpacing(4)
        self.title_label = ReceiptElidedLabel(receipt["title"] or "未命名回执")
        self.title_label.setObjectName("receiptCardTitle")
        title_area.addWidget(self.title_label)
        if self.parse_failed:
            parse_status = QLabel("解析失败")
            parse_status.setObjectName("receiptParseErrorStatus")
            title_area.addWidget(parse_status)
        if self.is_gale_submission:
            suggested_directory = " / ".join(
                str(receipt.get(key) or "无")
                for key in ("main_category", "sub_category", "sub_sub_category")
            )
            suggested_tags = format_tags(parse_tags(receipt.get("tags", ""))) or "无"
            summary_text = (
                f"来源: Gale · 投稿时间: {format_display_datetime(receipt['created_at'])} · "
                f"建议目录: {suggested_directory} · 建议标签: {suggested_tags}"
            )
        else:
            summary_text = (
                f"来源: {receipt['source']} · 动作: {receipt['action']} · "
                f"{format_display_datetime(receipt['created_at'])}"
            )
        self.summary_label = ReceiptElidedLabel(summary_text)
        self.summary_label.setObjectName("receiptCardSummary")
        title_area.addWidget(self.summary_label)
        top.addLayout(title_area, stretch=1)

        self.accept_button = QPushButton("接受" if self.is_gale_submission else "确认")
        self.accept_button.setObjectName("receiptAcceptButton")
        self.accept_button.clicked.connect(
            lambda: self.acceptRequested.emit(self.receipt_id)
        )
        self.reject_button = QPushButton("丢弃" if self.is_gale_submission else "拒绝")
        self.reject_button.setObjectName("receiptRejectButton")
        self.reject_button.clicked.connect(
            lambda: self.rejectRequested.emit(self.receipt_id)
        )
        self.delete_button = QPushButton("删除")
        self.delete_button.setObjectName("receiptRejectButton")
        self.delete_button.setToolTip("删除回执；不会删除正式笔记")
        self.delete_button.clicked.connect(
            lambda: self.deleteRequested.emit(self.receipt_id)
        )

        self.accept_button.setVisible(self.pending)
        self.reject_button.setVisible(self.pending)
        self.delete_button.setVisible(not self.pending)
        top.addWidget(self.accept_button)
        top.addWidget(self.reject_button)
        top.addWidget(self.delete_button)
        layout.addLayout(top)

        self.details = QWidget()
        self.details.setObjectName("receiptCardDetails")
        details_layout = QVBoxLayout(self.details)
        details_layout.setContentsMargins(0, 8, 0, 0)
        details_layout.setSpacing(8)

        if self.parse_failed:
            parse_error = QLabel(
                "解析错误：" + (receipt.get("parse_error") or "未提供具体错误")
            )
            parse_error.setObjectName("receiptParseErrorDetail")
            parse_error.setWordWrap(True)
            details_layout.addWidget(parse_error)

        self.title_input = QLineEdit(receipt["title"])
        details_layout.addWidget(self._row("标题", self.title_input))

        category_row = QHBoxLayout()
        category_row.setSpacing(8)
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
        self.ai_visibility_combo = FloatingComboBox()
        self.ai_visibility_combo.addItems(AI_VISIBILITY_OPTIONS)
        self.ai_visibility_combo.configure_popup(220)
        index = self.ai_visibility_combo.findText(
            visibility_display(receipt["ai_visibility"])
        )
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

        body_label = QLabel("正文" if self.is_gale_submission else "正文预览")
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
        text.setFixedWidth(40)
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
                target
                for target, checkbox in self.ai_target_checks.items()
                if checkbox.isChecked()
            ),
            "body": self.body_edit.toPlainText(),
        }

    def mouseReleaseEvent(self, event) -> None:
        self.setProperty("pressed", False)
        repolish(self)
        if event.button() == Qt.LeftButton:
            self.toggleRequested.emit(self.receipt_id)
        super().mouseReleaseEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.setProperty("pressed", True)
            repolish(self)
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in {Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space}:
            self.toggleRequested.emit(self.receipt_id)
            event.accept()
            return
        super().keyPressEvent(event)


class ReceiptInboxDialog(QWidget):
    finished = Signal(int)

    def __init__(self, parent=None, *, embedded: bool = False) -> None:
        super().__init__(parent) if embedded else super().__init__(parent, Qt.Window)
        self.embedded = embedded
        self.list_filter = "pending"
        self.expanded_receipt_id: int | None = None
        self.cards: dict[int, ReceiptCard] = {}
        self.scroll: QScrollArea | None = None
        self._finished_emitted = False
        self._delete_message_box: QMessageBox | None = None

        self.setWindowTitle("AI 待确认收件箱")
        if not embedded:
            self.setWindowModality(Qt.NonModal)
            self.setAttribute(Qt.WA_DeleteOnClose, True)
            self.resize(600, 700)
            self.setMinimumSize(520, 420)
        self.setObjectName("receiptInboxDialog")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("待确认" if embedded else "AI 待确认收件箱")
        title.setObjectName("receiptInboxTitle")
        layout.addWidget(title)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.pending_filter_button = QPushButton("待确认（0）")
        self.pending_filter_button.setObjectName("receiptTabButton")
        self.pending_filter_button.clicked.connect(
            lambda: self.set_list_filter("pending")
        )
        self.processed_filter_button = QPushButton("已处理")
        self.processed_filter_button.setObjectName("receiptTabButton")
        self.processed_filter_button.clicked.connect(
            lambda: self.set_list_filter("processed")
        )
        toolbar.addWidget(self.pending_filter_button)
        toolbar.addWidget(self.processed_filter_button)
        toolbar.addStretch(1)
        self.import_button = QPushButton("导入剪贴板回执")
        self.import_button.setObjectName("receiptImportButton")
        self.import_button.clicked.connect(self.import_from_clipboard)
        toolbar.addWidget(self.import_button)
        layout.addLayout(toolbar)

        self.content_stack = QStackedWidget()
        self.content_stack.setObjectName("receiptContentStack")
        self.empty_state = QLabel("")
        self.empty_state.setObjectName("receiptEmptyState")
        self.empty_state.setAlignment(Qt.AlignCenter)
        self.content_stack.addWidget(self.empty_state)
        layout.addWidget(self.content_stack, stretch=1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.close_button = QPushButton("关闭")
        self.close_button.setObjectName("receiptCloseButton")
        self.close_button.clicked.connect(self.close)
        footer.addWidget(self.close_button)
        layout.addLayout(footer)
        self.close_button.setVisible(not embedded)

        self.refresh_receipt_list()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._finished_emitted:
            self._finished_emitted = True
            self.finished.emit(0)
        super().closeEvent(event)

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
        self.list_filter = "pending"
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
            self._activate_host_window()

    def _activate_host_window(self) -> None:
        host_window = self.window()
        if host_window.isVisible():
            host_window.raise_()
            host_window.activateWindow()

    def refresh_receipt_list(self) -> None:
        pending_receipts = list_ai_receipt_inbox_items(status="pending")
        pending_count = len(pending_receipts)
        self.pending_filter_button.setText(f"待确认（{pending_count}）")
        self._set_tab_state(
            self.pending_filter_button, self.list_filter == "pending"
        )
        self._set_tab_state(
            self.processed_filter_button, self.list_filter == "processed"
        )

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

        if not receipts:
            self._show_empty_state()
            return

        if self._matches_rendered_receipts(receipts):
            return

        new_scroll, new_cards = self._build_cards_page(receipts)
        old_scroll = self.scroll
        self.cards = new_cards
        self.scroll = new_scroll
        self.content_stack.addWidget(new_scroll)
        self.content_stack.setCurrentWidget(new_scroll)

        if old_scroll is not None:
            self.content_stack.removeWidget(old_scroll)
            old_scroll.setEnabled(False)
            QTimer.singleShot(0, old_scroll.deleteLater)

    def _matches_rendered_receipts(self, receipts: list[dict]) -> bool:
        receipt_ids = [int(receipt["id"]) for receipt in receipts]
        if self.scroll is None or list(self.cards) != receipt_ids:
            return False
        return all(
            self.cards[receipt_id].receipt == receipt
            for receipt_id, receipt in zip(receipt_ids, receipts, strict=True)
        )

    def _build_cards_page(
        self, receipts: list[dict]
    ) -> tuple[QScrollArea, dict[int, ReceiptCard]]:
        scroll = QScrollArea()
        scroll.setObjectName("receiptScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        content.setObjectName("receiptScrollContent")
        content.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        cards_layout = QVBoxLayout(content)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(8)

        cards: dict[int, ReceiptCard] = {}
        for receipt in receipts:
            card = ReceiptCard(receipt, content)
            card.toggleRequested.connect(self.toggle_card)
            card.acceptRequested.connect(self.accept_receipt)
            card.rejectRequested.connect(self.reject_receipt)
            card.deleteRequested.connect(self.confirm_delete_receipt)
            card.set_expanded(receipt["id"] == self.expanded_receipt_id)
            cards[receipt["id"]] = card
            cards_layout.addWidget(card)
        cards_layout.addStretch(1)
        scroll.setWidget(content)
        return scroll, cards

    def _show_empty_state(self) -> None:
        old_scroll = self.scroll
        self.scroll = None
        self.cards = {}
        self.empty_state.setText(
            "当前没有待确认回执"
            if self.list_filter == "pending"
            else "当前没有已处理回执"
        )
        self.content_stack.setCurrentWidget(self.empty_state)
        if old_scroll is not None:
            self.content_stack.removeWidget(old_scroll)
            old_scroll.setEnabled(False)
            QTimer.singleShot(0, old_scroll.deleteLater)

    def _set_tab_state(self, button: QPushButton, active: bool) -> None:
        button.setProperty("active", active)
        button.style().unpolish(button)
        button.style().polish(button)

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
        allow_failed_parse = False
        if card.parse_failed:
            if not self._confirm_failed_parse_acceptance():
                return
            allow_failed_parse = True
        try:
            if card.receipt.get("request_id"):
                accept_ai_proposal(receipt_id, card.form_fields())
            else:
                accept_ai_receipt_inbox_item(
                    receipt_id,
                    card.form_fields(),
                    allow_failed_parse=allow_failed_parse,
                )
        except Exception as error:
            QMessageBox.warning(self.window(), "确认入库失败", str(error))
            self.refresh_receipt_list()
            self._activate_host_window()
            return
        self.expanded_receipt_id = None
        self.refresh_receipt_list()
        self._activate_host_window()

    def _confirm_failed_parse_acceptance(self) -> bool:
        message_box = QMessageBox(self)
        message_box.setWindowTitle("人工确认解析失败回执")
        message_box.setText(
            "这条回执解析失败。请确认你已经检查并修正标题、分类、"
            "正文和 AI 设置。\n仍按当前内容入库吗？"
        )
        continue_button = message_box.addButton("仍然入库", QMessageBox.AcceptRole)
        cancel_button = message_box.addButton("取消", QMessageBox.RejectRole)
        message_box.setDefaultButton(cancel_button)
        message_box.setEscapeButton(cancel_button)
        message_box.exec()
        return message_box.clickedButton() == continue_button

    def reject_receipt(self, receipt_id: int) -> None:
        try:
            receipt = get_ai_receipt_inbox_item(receipt_id)
            if receipt and receipt.get("request_id"):
                reject_ai_proposal(receipt_id)
            else:
                reject_ai_receipt_inbox_item(receipt_id)
        except Exception as error:
            QMessageBox.warning(self.window(), "拒绝失败", str(error))
            self.refresh_receipt_list()
            self._activate_host_window()
            return
        self.expanded_receipt_id = None
        self.refresh_receipt_list()
        self._activate_host_window()

    def confirm_delete_receipt(self, receipt_id: int) -> None:
        receipt = get_ai_receipt_inbox_item(receipt_id)
        if receipt is None:
            self.refresh_receipt_list()
            return

        if self._delete_message_box is not None:
            self._delete_message_box.raise_()
            self._delete_message_box.activateWindow()
            return

        message_box = QMessageBox(self)
        message_box.setWindowTitle("删除 AI 回执")
        message_box.setText("确定删除这条 AI 回执记录吗？这不会删除任何正式笔记。")
        delete_button = message_box.addButton("删除", QMessageBox.AcceptRole)
        message_box.addButton("取消", QMessageBox.RejectRole)
        message_box.setDefaultButton(delete_button)
        message_box.setWindowModality(Qt.WindowModal)
        self._delete_message_box = message_box

        def finish_delete(_result: int) -> None:
            should_delete = message_box.clickedButton() is delete_button
            self._delete_message_box = None
            message_box.deleteLater()
            if not should_delete:
                return
            try:
                self._delete_receipt_record(receipt_id)
            except Exception as error:
                QMessageBox.warning(self, "删除失败", str(error))
                self._activate_host_window()
                return
            self.expanded_receipt_id = None
            self.refresh_receipt_list()
            self._activate_host_window()

        message_box.finished.connect(finish_delete)
        message_box.open()

    def _delete_receipt_record(self, receipt_id: int) -> None:
        current = get_ai_receipt_inbox_item(receipt_id)
        if current is None:
            raise ValueError(f"AI 回执不存在: {receipt_id}")
        with _connect() as connection:
            connection.execute("DELETE FROM ai_receipt_inbox WHERE id = ?", (receipt_id,))
            if connection.total_changes == 0:
                raise ValueError(f"AI 回执不存在: {receipt_id}")


class ReceiptImportPreviewDialog(QDialog):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("导入剪贴板回执")
        self.setModal(True)
        self.resize(720, 520)
        self.setMinimumSize(640, 420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

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
            QMessageBox.information(
                self, "没有可导入的文本", "预览框为空，不能解析导入。"
            )
            return
        self.accept()
