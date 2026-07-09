from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QMenu,
    QSplitter,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
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


class ReceiptInboxDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.current_receipt_id: int | None = None
        self.list_filter = "pending"
        self.setWindowTitle("AI 待确认收件箱")
        self.setModal(True)
        self.resize(1040, 680)
        self.setMinimumSize(920, 600)
        self.setObjectName("receiptInboxDialog")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("AI 待确认收件箱")
        title.setObjectName("exportTitle")
        header.addWidget(title, stretch=1)
        self.import_button = QPushButton("导入剪贴板回执")
        self.import_button.clicked.connect(self.import_from_clipboard)
        header.addWidget(self.import_button)
        layout.addLayout(header)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        filter_row.addWidget(QLabel("列表"))
        self.pending_filter_button = QPushButton("待确认")
        self.pending_filter_button.setCheckable(True)
        self.pending_filter_button.setChecked(True)
        self.pending_filter_button.clicked.connect(lambda: self.set_list_filter("pending"))
        filter_row.addWidget(self.pending_filter_button)
        self.all_filter_button = QPushButton("全部")
        self.all_filter_button.setCheckable(True)
        self.all_filter_button.clicked.connect(lambda: self.set_list_filter("all"))
        filter_row.addWidget(self.all_filter_button)
        filter_row.addStretch(1)
        layout.addLayout(filter_row)

        splitter = QSplitter(Qt.Horizontal)
        self.receipt_tree = QTreeWidget()
        self.receipt_tree.setHeaderLabels(["标题", "来源", "动作", "创建时间", "解析", "状态"])
        self.receipt_tree.setRootIsDecorated(False)
        self.receipt_tree.itemClicked.connect(self.handle_receipt_selected)
        self.receipt_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.receipt_tree.customContextMenuRequested.connect(
            self.open_receipt_context_menu
        )
        splitter.addWidget(self.receipt_tree)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(12, 0, 0, 0)
        detail_layout.setSpacing(8)

        self.status_label = QLabel("未选择回执")
        self.status_label.setObjectName("metadataLabel")
        detail_layout.addWidget(self.status_label)

        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("标题")
        detail_layout.addWidget(self.labeled_row("标题", self.title_input))

        source_action_row = QHBoxLayout()
        self.source_input = QLineEdit()
        self.action_input = QLineEdit()
        source_action_row.addWidget(self.labeled_inline("来源", self.source_input))
        source_action_row.addWidget(self.labeled_inline("动作", self.action_input))
        detail_layout.addLayout(source_action_row)

        category_row = QHBoxLayout()
        self.main_input = QLineEdit()
        self.sub_input = QLineEdit()
        self.sub_sub_input = QLineEdit()
        category_row.addWidget(self.labeled_inline("主分类", self.main_input))
        category_row.addWidget(self.labeled_inline("子分类", self.sub_input))
        category_row.addWidget(self.labeled_inline("子子分类", self.sub_sub_input))
        detail_layout.addLayout(category_row)

        self.tags_input = QLineEdit()
        self.tags_input.setPlaceholderText("todo, vps, ai-ok")
        detail_layout.addWidget(self.labeled_row("标签", self.tags_input))

        ai_row = QHBoxLayout()
        self.ai_visibility_combo = QComboBox()
        self.ai_visibility_combo.addItems(AI_VISIBILITY_OPTIONS)
        self.ai_visibility_combo.setMinimumWidth(170)
        ai_row.addWidget(self.labeled_inline("AI 可见性", self.ai_visibility_combo))
        ai_targets_widget = QWidget()
        ai_targets_layout = QHBoxLayout(ai_targets_widget)
        ai_targets_layout.setContentsMargins(0, 0, 0, 0)
        ai_targets_layout.setSpacing(12)
        self.ai_target_checks: dict[str, QCheckBox] = {}
        for target in AI_TARGETS:
            checkbox = QCheckBox(target)
            self.ai_target_checks[target] = checkbox
            ai_targets_layout.addWidget(checkbox)
        ai_targets_layout.addStretch(1)
        ai_row.addWidget(QLabel("AI 对象"))
        ai_row.addWidget(ai_targets_widget, stretch=1)
        detail_layout.addLayout(ai_row)

        self.body_edit = QTextEdit()
        self.body_edit.setPlaceholderText("正文")
        detail_layout.addWidget(QLabel("正文"))
        detail_layout.addWidget(self.body_edit, stretch=2)

        self.raw_text_edit = QTextEdit()
        self.raw_text_edit.setReadOnly(True)
        self.raw_text_edit.setPlaceholderText("原始粘贴文本")
        detail_layout.addWidget(QLabel("原始粘贴文本"))
        detail_layout.addWidget(self.raw_text_edit, stretch=1)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.accept_button = QPushButton("确认入库")
        self.accept_button.clicked.connect(self.accept_receipt)
        action_row.addWidget(self.accept_button)
        self.reject_button = QPushButton("拒绝")
        self.reject_button.clicked.connect(self.reject_receipt)
        action_row.addWidget(self.reject_button)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.accept)
        action_row.addWidget(close_button)
        detail_layout.addLayout(action_row)

        splitter.addWidget(detail)
        splitter.setSizes([410, 630])
        layout.addWidget(splitter, stretch=1)

        self.refresh_receipt_list()
        self.set_detail_enabled(False)

    def labeled_row(self, label: str, widget: QWidget) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        text = QLabel(label)
        text.setMinimumWidth(72)
        layout.addWidget(text)
        layout.addWidget(widget, stretch=1)
        return row

    def labeled_inline(self, label: str, widget: QWidget) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        text = QLabel(label)
        layout.addWidget(text)
        layout.addWidget(widget, stretch=1)
        return row

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
        self.refresh_receipt_list(receipt["id"])
        QMessageBox.information(self, "已导入", "回执已进入 AI 待确认收件箱。")

    def set_list_filter(self, value: str, *, refresh: bool = True) -> None:
        self.list_filter = value
        self.pending_filter_button.setChecked(value == "pending")
        self.all_filter_button.setChecked(value == "all")
        if refresh:
            self.refresh_receipt_list()

    def refresh_receipt_list(self, selected_id: int | None = None) -> None:
        self.receipt_tree.clear()
        selected_item: QTreeWidgetItem | None = None
        status = "pending" if self.list_filter == "pending" else None
        for receipt in list_ai_receipt_inbox_items(status=status):
            item = QTreeWidgetItem(
                [
                    receipt["title"],
                    receipt["source"],
                    receipt["action"],
                    format_display_datetime(receipt["created_at"]),
                    receipt["parse_status"],
                    receipt["status"],
                ]
            )
            item.setData(0, Qt.UserRole, receipt["id"])
            self.receipt_tree.addTopLevelItem(item)
            if selected_id == receipt["id"]:
                selected_item = item
        for column in range(self.receipt_tree.columnCount()):
            self.receipt_tree.resizeColumnToContents(column)
        if selected_item is not None:
            self.receipt_tree.setCurrentItem(selected_item)
            self.load_receipt(selected_id)
        elif self.receipt_tree.topLevelItemCount() == 0:
            self.current_receipt_id = None
            self.clear_detail()

    def handle_receipt_selected(self, item: QTreeWidgetItem) -> None:
        receipt_id = item.data(0, Qt.UserRole)
        self.load_receipt(receipt_id)

    def open_receipt_context_menu(self, position) -> None:
        item = self.receipt_tree.itemAt(position)
        if item is None:
            return
        receipt_id = item.data(0, Qt.UserRole)
        receipt = get_ai_receipt_inbox_item(receipt_id)
        if receipt is None:
            self.refresh_receipt_list()
            return
        menu = QMenu(self)
        delete_action = menu.addAction("删除")
        delete_action.setEnabled(receipt["status"] in {"pending", "rejected"})
        chosen = menu.exec(self.receipt_tree.viewport().mapToGlobal(position))
        if chosen == delete_action:
            self.confirm_delete_receipt(receipt_id)

    def load_receipt(self, receipt_id: int) -> None:
        receipt = get_ai_receipt_inbox_item(receipt_id)
        if receipt is None:
            self.refresh_receipt_list()
            return
        self.current_receipt_id = receipt_id
        self.title_input.setText(receipt["title"])
        self.source_input.setText(receipt["source"])
        self.action_input.setText(receipt["action"])
        self.main_input.setText(receipt["main_category"])
        self.sub_input.setText(receipt["sub_category"])
        self.sub_sub_input.setText(receipt["sub_sub_category"])
        self.tags_input.setText(format_tags(parse_tags(receipt["tags"])))
        self.set_ai_visibility(receipt["ai_visibility"])
        self.set_ai_targets(parse_ai_targets_text(receipt["ai_targets"]))
        self.body_edit.setPlainText(receipt["body"])
        self.raw_text_edit.setPlainText(receipt["raw_text"])
        error = f" · {receipt['parse_error']}" if receipt.get("parse_error") else ""
        self.status_label.setText(
            f"解析：{receipt['parse_status']}{error} · 状态：{receipt['status']}"
        )
        self.set_detail_enabled(receipt["status"] == "pending")

    def clear_detail(self) -> None:
        self.status_label.setText("未选择回执")
        for widget in (
            self.title_input,
            self.source_input,
            self.action_input,
            self.main_input,
            self.sub_input,
            self.sub_sub_input,
            self.tags_input,
        ):
            widget.clear()
        self.ai_visibility_combo.setCurrentIndex(0)
        self.set_ai_targets([])
        self.body_edit.clear()
        self.raw_text_edit.clear()
        self.set_detail_enabled(False)

    def set_detail_enabled(self, enabled: bool) -> None:
        for widget in (
            self.title_input,
            self.source_input,
            self.action_input,
            self.main_input,
            self.sub_input,
            self.sub_sub_input,
            self.tags_input,
            self.ai_visibility_combo,
            self.body_edit,
        ):
            widget.setEnabled(enabled)
        for checkbox in self.ai_target_checks.values():
            checkbox.setEnabled(enabled)
        self.accept_button.setEnabled(enabled)
        self.reject_button.setEnabled(enabled)

    def set_ai_visibility(self, visibility: str) -> None:
        index = self.ai_visibility_combo.findText(visibility)
        self.ai_visibility_combo.setCurrentIndex(index if index >= 0 else 0)

    def set_ai_targets(self, targets: list[str]) -> None:
        target_set = set(targets)
        for target, checkbox in self.ai_target_checks.items():
            checkbox.setChecked(target in target_set)

    def selected_ai_targets(self) -> list[str]:
        return [
            target
            for target, checkbox in self.ai_target_checks.items()
            if checkbox.isChecked()
        ]

    def form_fields(self) -> dict:
        return {
            "title": self.title_input.text().strip(),
            "source": self.source_input.text().strip() or "未知",
            "action": self.action_input.text().strip() or "新增",
            "main_category": self.main_input.text().strip() or "临时",
            "sub_category": self.sub_input.text().strip() or "无",
            "sub_sub_category": self.sub_sub_input.text().strip() or "无",
            "tags": format_tags(parse_tags(self.tags_input.text())),
            "ai_visibility": self.ai_visibility_combo.currentText(),
            "ai_targets": ", ".join(self.selected_ai_targets()),
            "body": self.body_edit.toPlainText(),
        }

    def accept_receipt(self) -> None:
        if self.current_receipt_id is None:
            return
        try:
            result = accept_ai_receipt_inbox_item(
                self.current_receipt_id,
                self.form_fields(),
            )
        except Exception as error:
            QMessageBox.warning(self, "确认入库失败", str(error))
            self.load_receipt(self.current_receipt_id)
            return
        self.refresh_receipt_list(result["receipt"]["id"])
        if self.list_filter == "pending":
            self.clear_detail()
        QMessageBox.information(self, "已确认入库", "已确认入库。")

    def reject_receipt(self) -> None:
        if self.current_receipt_id is None:
            return
        try:
            receipt = reject_ai_receipt_inbox_item(self.current_receipt_id)
        except Exception as error:
            QMessageBox.warning(self, "拒绝失败", str(error))
            self.load_receipt(self.current_receipt_id)
            return
        self.refresh_receipt_list(receipt["id"])
        if self.list_filter == "pending":
            self.clear_detail()
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
        if self.current_receipt_id == receipt_id:
            self.clear_detail()
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
