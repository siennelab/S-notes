"""Thin ordinary-export dialog over ExportService."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from src.snotes.application.ai_safe_export_service import AiSafeExportService
from src.snotes.application.export_service import ExportService
from src.snotes.domain.models import NoteLocation
from src.snotes.domain.sensitive_scan import findings_to_dialog_text
from src.snotes.ui.floating_combo import FloatingComboBox


@dataclass(frozen=True, slots=True)
class ExportContext:
    current_note_id: int | None
    current_directory: NoteLocation | None
    sidebar_mode: str
    visible_note_ids: tuple[int, ...]
    selected_note_ids: tuple[int, ...]
    selected_directories: tuple[NoteLocation, ...]


class ExportDialog(QDialog):
    def __init__(
        self,
        service: ExportService,
        context: ExportContext,
        parent=None,
        *,
        ai_safe_export_service: AiSafeExportService | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.ai_safe_export_service = ai_safe_export_service
        self.context = context
        self.last_path: Path | None = None
        self.setObjectName("exportDialog")
        self.setWindowTitle("导出中心")
        self.resize(680, 360)
        layout = QVBoxLayout(self)
        title = QLabel("普通导出（Markdown / JSON / SQLite 备份）")
        title.setObjectName("exportTitle")
        layout.addWidget(title)
        form = QFormLayout()
        self.scope_combo = FloatingComboBox()
        self.scope_combo.configure_popup(240)
        self.scope_combo.setMinimumWidth(240)
        self.scope_combo.setFixedHeight(32)
        if context.current_note_id is not None:
            self.scope_combo.addItem("当前笔记", "current")
        if context.current_directory is not None:
            self.scope_combo.addItem("当前目录", "current_directory")
        if context.sidebar_mode in {"search", "tag", "smart_view"}:
            self.scope_combo.addItem("当前筛选结果", "visible")
        if context.selected_note_ids:
            self.scope_combo.addItem("多选笔记", "selected_notes")
        if context.selected_directories:
            self.scope_combo.addItem("多选目录", "selected_directories")
        self.scope_combo.addItem("全部未删除笔记", "all")
        self.scope_combo.addItem("SQLite 数据库备份", "backup")
        self.format_combo = FloatingComboBox()
        self.format_combo.configure_popup(180)
        self.format_combo.setMinimumWidth(240)
        self.format_combo.setFixedHeight(32)
        self.format_combo.addItem("Markdown", "markdown")
        self.format_combo.addItem("JSON", "json")
        self.scope_combo.currentIndexChanged.connect(self._scope_changed)
        form.addRow("范围", self.scope_combo)
        form.addRow("格式", self.format_combo)
        layout.addLayout(form)
        self.summary = QLabel("导出写入隔离数据目录的 exports 文件夹。")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.ai_section = QFrame()
        self.ai_section.setObjectName("aiSafeExportSection")
        ai_layout = QHBoxLayout(self.ai_section)
        ai_layout.setContentsMargins(12, 12, 12, 12)
        ai_layout.setSpacing(8)
        ai_title = QLabel("AI 安全导出")
        ai_title.setObjectName("exportSectionTitle")
        ai_layout.addWidget(ai_title)
        ai_layout.addWidget(QLabel("对象"))
        self.ai_target_combo = FloatingComboBox()
        self.ai_target_combo.configure_popup(210)
        self.ai_target_combo.setMinimumWidth(180)
        if self.ai_safe_export_service is not None:
            for target in self.ai_safe_export_service.available_targets():
                self.ai_target_combo.addItem(target.display_name, target.target_id)
        ai_layout.addWidget(self.ai_target_combo)
        self.ai_export_button = QPushButton("生成 AI 安全包")
        self.ai_export_button.setEnabled(self.ai_safe_export_service is not None)
        self.ai_export_button.clicked.connect(self._export_ai_safe)
        ai_layout.addWidget(self.ai_export_button)
        ai_layout.addStretch(1)
        layout.addWidget(self.ai_section)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.reveal_button = QPushButton("在 Finder 中显示")
        self.reveal_button.setEnabled(False)
        self.reveal_button.clicked.connect(self._reveal)
        buttons.addWidget(self.reveal_button)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        export = QPushButton("导出")
        export.clicked.connect(self._export)
        buttons.addWidget(export)
        layout.addLayout(buttons)

    def _scope_changed(self) -> None:
        backup = self.scope_combo.currentData() == "backup"
        self.format_combo.setEnabled(not backup)

    def _export(self) -> None:
        scope = str(self.scope_combo.currentData())
        export_format = str(self.format_combo.currentData())
        try:
            if scope == "current":
                result = self.service.export_current(int(self.context.current_note_id), export_format)
            elif scope == "current_directory":
                result = self.service.export_directories([self.context.current_directory], export_format)
            elif scope == "visible":
                result = self.service.export_note_ids(self.context.visible_note_ids, self.context.sidebar_mode, export_format)
            elif scope == "selected_notes":
                result = self.service.export_note_ids(self.context.selected_note_ids, "selected_notes", export_format)
            elif scope == "selected_directories":
                result = self.service.export_directories(self.context.selected_directories, export_format)
            elif scope == "all":
                result = self.service.export_all(export_format)
            else:
                result = self.service.backup_database()
        except Exception as error:
            QMessageBox.warning(self, "导出失败", str(error))
            return
        self.last_path = Path(result.path)
        self.reveal_button.setEnabled(True)
        QMessageBox.information(self, "导出成功", f"文件路径：{result.path}\n笔记数量：{result.note_count}")

    def _reveal(self) -> None:
        if self.last_path is not None and self.last_path.exists():
            QProcess.startDetached("open", ["-R", str(self.last_path)])

    def _export_ai_safe(self) -> None:
        if self.ai_safe_export_service is None:
            return
        try:
            outcome = self.ai_safe_export_service.export(
                self.ai_target_combo.currentData(),
                confirm_sensitive=self._confirm_sensitive_export,
            )
        except Exception as error:
            QMessageBox.warning(self, "AI 安全导出失败", str(error))
            return
        if outcome.status == "cancelled":
            return
        if outcome.status == "no_data":
            detail = (
                "\n\n" + "；".join(outcome.plan.warnings)
                if outcome.plan.warnings
                else ""
            )
            QMessageBox.information(
                self,
                "没有可导出的笔记",
                "当前 AI 对象没有可导出的安全资料。" + detail,
            )
            return
        if outcome.result is None:
            return
        self.last_path = outcome.result.package_dir
        self.reveal_button.setEnabled(True)
        warning_text = (
            "\n提示：" + "；".join(outcome.plan.warnings)
            if outcome.plan.warnings
            else ""
        )
        QMessageBox.information(
            self,
            "AI 安全导出成功",
            (
                f"文件夹：{outcome.result.package_dir}\n"
                f"笔记数量：{outcome.result.note_count}{warning_text}"
            ),
        )

    def _confirm_sensitive_export(self, plan) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("发现疑似敏感内容")
        box.setText(findings_to_dialog_text(plan.scan_summary))
        continue_button = box.addButton("继续导出", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(continue_button)
        box.exec()
        return box.clickedButton() is continue_button
