from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.app_paths import (
    BACKUPS_DIR as SQLITE_BACKUP_DIR,
    EXPORTS_ROOT,
    JSON_EXPORT_DIR,
    MARKDOWN_EXPORT_DIR,
)
from src.ai_safe_export import AiSafePackageExporter
from src.ai_targets import AI_TARGETS
from src.database import (
    DATABASE_PATH,
    get_note_for_export,
    list_directories,
    list_notes_by_ids_for_export,
    list_notes_for_export,
    list_notes_under_path_for_export,
)
from src.floating_combo import FloatingComboBox
from src.tag_manager import parse_tags
from src.time_utils import format_display_datetime, now_shanghai
from src.sensitive_scan import findings_to_dialog_text


SCOPE_CURRENT_NOTE = "current_note"
SCOPE_CURRENT_DIRECTORY = "current_directory"
SCOPE_SEARCH = "search"
SCOPE_TAG = "tag"
SCOPE_SMART_VIEW = "smart_view"
SCOPE_SELECTED_NOTES = "selected_notes"
SCOPE_SELECTED_DIRECTORIES = "selected_directories"
SCOPE_ALL_NOTES = "all_notes"
SCOPE_SQLITE_BACKUP = "sqlite_backup"

FORMAT_MARKDOWN = "markdown"
FORMAT_JSON = "json"
FORMAT_SQLITE = "sqlite"


SCOPE_LABELS = {
    SCOPE_CURRENT_NOTE: "当前笔记",
    SCOPE_CURRENT_DIRECTORY: "当前目录",
    SCOPE_SEARCH: "当前搜索结果",
    SCOPE_TAG: "当前标签筛选结果",
    SCOPE_SMART_VIEW: "当前智能视图结果",
    SCOPE_SELECTED_NOTES: "多选笔记",
    SCOPE_SELECTED_DIRECTORIES: "多选目录",
    SCOPE_ALL_NOTES: "全部未删除笔记",
    SCOPE_SQLITE_BACKUP: "SQLite 数据库备份",
}

FORMAT_LABELS = {
    FORMAT_MARKDOWN: "Markdown",
    FORMAT_JSON: "JSON",
    FORMAT_SQLITE: "SQLite 备份",
}


@dataclass
class ExportContext:
    current_note_id: int | None
    current_directory_path: tuple[str, ...]
    sidebar_mode: str
    filter_value: str
    search_query: str
    visible_note_ids: list[int]


@dataclass
class ExportResult:
    path: Path
    note_count: int


def configure_combo_popup(
    combo: FloatingComboBox, minimum_popup_width: int
) -> None:
    combo.configure_popup(minimum_popup_width)


class ExportCenterDialog(QDialog):
    def __init__(self, context: ExportContext, parent=None) -> None:
        super().__init__(parent)
        self.context = context
        self.last_export_path: Path | None = None
        self.setWindowTitle("导出中心")
        self.setModal(True)
        self.resize(720, 460)
        self.setMinimumSize(680, 420)
        self.setObjectName("exportDialog")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(12)

        title = QLabel("普通导出")
        title.setObjectName("exportTitle")
        layout.addWidget(title)

        form_row = QHBoxLayout()
        form_row.setSpacing(16)
        scope_form = QFormLayout()
        scope_form.setContentsMargins(0, 0, 0, 0)
        scope_form.setSpacing(8)
        self.scope_combo = FloatingComboBox()
        configure_combo_popup(self.scope_combo, 300)
        for scope in self.available_scopes():
            self.scope_combo.addItem(SCOPE_LABELS[scope], scope)
        self.scope_combo.setMinimumWidth(260)
        self.scope_combo.currentIndexChanged.connect(self.handle_scope_changed)
        scope_form.addRow("范围", self.scope_combo)
        form_row.addLayout(scope_form)

        format_form = QFormLayout()
        format_form.setContentsMargins(0, 0, 0, 0)
        format_form.setSpacing(8)
        self.format_combo = FloatingComboBox()
        configure_combo_popup(self.format_combo, 260)
        self.format_combo.addItem(FORMAT_LABELS[FORMAT_MARKDOWN], FORMAT_MARKDOWN)
        self.format_combo.addItem(FORMAT_LABELS[FORMAT_JSON], FORMAT_JSON)
        self.format_combo.addItem("SQLite 数据库备份", FORMAT_SQLITE)
        self.format_combo.setMinimumWidth(220)
        format_form.addRow("格式", self.format_combo)
        form_row.addLayout(format_form)
        form_row.addStretch(1)
        layout.addLayout(form_row)

        self.dynamic_area = QWidget()
        self.dynamic_area.setObjectName("exportDynamicArea")
        self.dynamic_layout = QVBoxLayout(self.dynamic_area)
        self.dynamic_layout.setContentsMargins(0, 0, 0, 0)
        self.dynamic_layout.setSpacing(8)
        layout.addWidget(self.dynamic_area, stretch=1)

        self.summary_label = QLabel("")
        self.summary_label.setObjectName("metadataLabel")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        ai_section = QWidget()
        ai_section.setObjectName("aiSafeExportSection")
        ai_layout = QHBoxLayout(ai_section)
        ai_layout.setContentsMargins(12, 12, 12, 12)
        ai_layout.setSpacing(8)
        ai_title = QLabel("AI 安全导出")
        ai_title.setObjectName("exportSectionTitle")
        ai_layout.addWidget(ai_title)
        self.ai_target_combo = FloatingComboBox()
        configure_combo_popup(self.ai_target_combo, 200)
        for target_name in AI_TARGETS:
            self.ai_target_combo.addItem(target_name, target_name)
        self.ai_target_combo.setMinimumWidth(180)
        ai_layout.addWidget(QLabel("对象"))
        ai_layout.addWidget(self.ai_target_combo)
        self.ai_export_button = QPushButton("生成 AI 安全包")
        self.ai_export_button.clicked.connect(self.export_ai_safe_package)
        ai_layout.addWidget(self.ai_export_button)
        ai_layout.addStretch(1)
        layout.addWidget(ai_section)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)
        button_row.setAlignment(Qt.AlignVCenter)
        button_row.addStretch(1)
        self.finder_button = QPushButton("在 Finder 中显示")
        self.finder_button.setFixedHeight(32)
        self.finder_button.setEnabled(False)
        self.finder_button.clicked.connect(self.reveal_last_export)
        button_row.addWidget(self.finder_button)

        self.cancel_button = QPushButton("取消")
        self.cancel_button.setFixedHeight(32)
        self.cancel_button.clicked.connect(self.reject)
        button_row.addWidget(self.cancel_button)

        self.export_button = QPushButton("导出")
        self.export_button.setFixedHeight(32)
        self.export_button.clicked.connect(self.export_selected)
        button_row.addWidget(self.export_button)
        layout.addLayout(button_row)

        self.selection_notes = list_notes_for_export()
        self.selection_directories = list_directories()
        self.note_selection_tree: QTreeWidget | None = None
        self.directory_selection_tree: QTreeWidget | None = None

        self.handle_scope_changed()

    def available_scopes(self) -> list[str]:
        scopes = []
        if self.context.current_note_id is not None:
            scopes.append(SCOPE_CURRENT_NOTE)
        if self.context.current_directory_path:
            scopes.append(SCOPE_CURRENT_DIRECTORY)
        if self.context.sidebar_mode == "search":
            scopes.append(SCOPE_SEARCH)
        if self.context.sidebar_mode == "tag":
            scopes.append(SCOPE_TAG)
        if self.context.sidebar_mode == "smart_view":
            scopes.append(SCOPE_SMART_VIEW)
        scopes.extend([
            SCOPE_SELECTED_NOTES,
            SCOPE_SELECTED_DIRECTORIES,
            SCOPE_ALL_NOTES,
            SCOPE_SQLITE_BACKUP,
        ])
        return scopes

    def handle_scope_changed(self) -> None:
        scope = self.current_scope()
        self.rebuild_dynamic_area(scope)
        if scope == SCOPE_SQLITE_BACKUP:
            self.format_combo.setCurrentIndex(self.format_combo.findData(FORMAT_SQLITE))
            self.format_combo.setEnabled(False)
            self.summary_label.setText("复制当前 SQLite 数据库到 S-notes 用户数据备份目录。")
        else:
            self.format_combo.setEnabled(True)
            sqlite_index = self.format_combo.findData(FORMAT_SQLITE)
            if self.format_combo.currentData() == FORMAT_SQLITE:
                self.format_combo.setCurrentIndex(self.format_combo.findData(FORMAT_MARKDOWN))
            self.summary_label.setText(f"导出范围：{SCOPE_LABELS[scope]}")
            self.format_combo.model().item(sqlite_index).setEnabled(False)
            for index in (
                self.format_combo.findData(FORMAT_MARKDOWN),
                self.format_combo.findData(FORMAT_JSON),
            ):
                self.format_combo.model().item(index).setEnabled(True)
        self.update_export_button_state()

    def rebuild_dynamic_area(self, scope: str) -> None:
        while self.dynamic_layout.count():
            item = self.dynamic_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.note_selection_tree = None
        self.directory_selection_tree = None

        if scope == SCOPE_SELECTED_NOTES:
            self.note_selection_tree = self.build_note_selection_tree()
            self.dynamic_layout.addWidget(self.note_selection_tree)
            return
        if scope == SCOPE_SELECTED_DIRECTORIES:
            self.directory_selection_tree = self.build_directory_selection_tree()
            self.dynamic_layout.addWidget(self.directory_selection_tree)
            return

        label = QLabel(self.scope_description(scope))
        label.setObjectName("exportInfoLabel")
        label.setWordWrap(True)
        self.dynamic_layout.addWidget(label)
        self.dynamic_layout.addStretch(1)

    def scope_description(self, scope: str) -> str:
        if scope == SCOPE_CURRENT_NOTE:
            if self.context.current_note_id is None:
                return "当前没有打开的笔记。"
            note = get_note_for_export(self.context.current_note_id)
            return f"当前笔记：{note['title'] if note else '无'}"
        if scope == SCOPE_CURRENT_DIRECTORY:
            return "当前目录：" + (" / ".join(self.context.current_directory_path) or "未选择目录")
        if scope == SCOPE_SEARCH:
            return f"搜索：{self.context.search_query}，共 {len(self.context.visible_note_ids)} 条"
        if scope == SCOPE_TAG:
            return f"标签：#{self.context.filter_value}，共 {len(self.context.visible_note_ids)} 条"
        if scope == SCOPE_SMART_VIEW:
            return f"智能视图：{self.context.filter_value}，共 {len(self.context.visible_note_ids)} 条"
        if scope == SCOPE_ALL_NOTES:
            return f"全部未删除笔记，共 {len(self.selection_notes)} 条"
        if scope == SCOPE_SQLITE_BACKUP:
            return f"数据库：{DATABASE_PATH}"
        return ""

    def build_note_selection_tree(self) -> QTreeWidget:
        tree = QTreeWidget()
        tree.setObjectName("exportSelectionTree")
        tree.setHeaderLabels(["笔记", "目录", "更新时间"])
        tree.itemChanged.connect(lambda *_: self.update_export_button_state())
        for note in self.selection_notes:
            item = QTreeWidgetItem(
                [
                    note["title"] or "未命名笔记",
                    note_path_label(note),
                    format_display_datetime(note["updated_at"]),
                ]
            )
            item.setData(0, Qt.UserRole, note["id"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            checked = (
                self.context.current_note_id is not None
                and note["id"] == self.context.current_note_id
            )
            item.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
            tree.addTopLevelItem(item)
        tree.resizeColumnToContents(0)
        tree.resizeColumnToContents(1)
        return tree

    def build_directory_selection_tree(self) -> QTreeWidget:
        tree = QTreeWidget()
        tree.setObjectName("exportSelectionTree")
        tree.setHeaderLabels(["目录"])
        tree.itemChanged.connect(lambda *_: self.update_export_button_state())
        items_by_id: dict[int, QTreeWidgetItem] = {}
        for directory in sorted(
            self.selection_directories,
            key=lambda item: (directory_depth(item, self.selection_directories), item["name"]),
        ):
            item = QTreeWidgetItem([directory["name"]])
            item.setData(0, Qt.UserRole, directory["id"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Unchecked)
            items_by_id[directory["id"]] = item
            parent_item = items_by_id.get(directory["parent_id"])
            if parent_item is None:
                tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
        tree.expandAll()
        return tree

    def selected_note_ids(self) -> list[int]:
        if self.note_selection_tree is None:
            return []
        note_ids: list[int] = []
        for index in range(self.note_selection_tree.topLevelItemCount()):
            item = self.note_selection_tree.topLevelItem(index)
            if item.checkState(0) == Qt.Checked:
                note_ids.append(item.data(0, Qt.UserRole))
        return note_ids

    def selected_directory_paths(self) -> list[tuple[str, ...]]:
        if self.directory_selection_tree is None:
            return []
        rows_by_id = {row["id"]: row for row in self.selection_directories}
        paths: list[tuple[str, ...]] = []

        def visit(item: QTreeWidgetItem) -> None:
            if item.checkState(0) == Qt.Checked:
                directory_id = item.data(0, Qt.UserRole)
                paths.append(directory_path_from_rows(directory_id, rows_by_id))
            for child_index in range(item.childCount()):
                visit(item.child(child_index))

        for index in range(self.directory_selection_tree.topLevelItemCount()):
            visit(self.directory_selection_tree.topLevelItem(index))
        return paths

    def update_export_button_state(self) -> None:
        if not hasattr(self, "export_button"):
            return
        scope = self.current_scope()
        if scope == SCOPE_SELECTED_NOTES:
            self.export_button.setEnabled(bool(self.selected_note_ids()))
        elif scope == SCOPE_SELECTED_DIRECTORIES:
            self.export_button.setEnabled(bool(self.selected_directory_paths()))
        else:
            self.export_button.setEnabled(True)

    def current_scope(self) -> str:
        return self.scope_combo.currentData()

    def current_format(self) -> str:
        return self.format_combo.currentData()

    def export_selected(self) -> None:
        try:
            scope = self.current_scope()
            export_format = self.current_format()
            if scope == SCOPE_SQLITE_BACKUP:
                result = SqliteBackupExporter().export()
            else:
                notes = self.notes_for_scope(scope)
                if not notes:
                    QMessageBox.information(self, "没有可导出的笔记", "当前范围没有可导出的笔记。")
                    return
                if export_format == FORMAT_MARKDOWN:
                    result = MarkdownExporter().export(notes, scope)
                elif export_format == FORMAT_JSON:
                    result = JsonExporter().export(notes, scope)
                else:
                    raise ValueError("请选择 Markdown 或 JSON 格式。")
        except Exception as error:
            QMessageBox.warning(self, "导出失败", str(error))
            return

        self.last_export_path = result.path
        self.finder_button.setEnabled(True)
        result_summary = (
            "完整数据库已备份"
            if scope == SCOPE_SQLITE_BACKUP
            else f"笔记数量：{result.note_count}"
        )
        QMessageBox.information(
            self,
            "导出成功",
            f"导出成功\n文件路径：{result.path}\n{result_summary}",
        )

    def reveal_last_export(self) -> None:
        if self.last_export_path is None:
            QMessageBox.information(self, "没有可显示的文件", "还没有成功导出或生成文件。")
            return
        if not self.last_export_path.exists():
            QMessageBox.warning(self, "无法打开 Finder", f"路径不存在：{self.last_export_path}")
            return
        try:
            subprocess.run(["open", "-R", str(self.last_export_path)], check=True)
        except Exception as error:
            QMessageBox.warning(self, "无法打开 Finder", str(error))

    def export_ai_safe_package(self) -> None:
        target = self.ai_target_combo.currentData()
        try:
            exporter = AiSafePackageExporter()
            plan = exporter.build_plan(target)
            if not plan.rendered_notes:
                QMessageBox.information(
                    self,
                    "没有可导出的笔记",
                    "当前 AI 对象没有可导出的安全资料。",
                )
                return
            if plan.has_sensitive_findings and not self.confirm_sensitive_export(plan):
                return
            result = exporter.write_plan(plan)
        except Exception as error:
            QMessageBox.warning(self, "AI 安全导出失败", str(error))
            return

        self.last_export_path = result.package_dir
        self.finder_button.setEnabled(True)
        warnings = f"\n提示：{'；'.join(plan.warnings)}" if plan.warnings else ""
        QMessageBox.information(
            self,
            "AI 安全导出成功",
            (
                "AI 安全导出成功\n"
                f"文件夹：{result.package_dir}\n"
                f"笔记数量：{result.note_count}"
                f"{warnings}"
            ),
        )

    def confirm_sensitive_export(self, plan) -> bool:
        message_box = QMessageBox(self)
        message_box.setWindowTitle("发现疑似敏感内容")
        message_box.setText(findings_to_dialog_text(plan.scan_summary))
        continue_button = message_box.addButton("继续导出", QMessageBox.AcceptRole)
        message_box.addButton("取消", QMessageBox.RejectRole)
        message_box.setDefaultButton(continue_button)
        message_box.exec()
        return message_box.clickedButton() == continue_button

    def notes_for_scope(self, scope: str) -> list[dict]:
        if scope == SCOPE_CURRENT_NOTE:
            if self.context.current_note_id is None:
                return []
            note = get_note_for_export(self.context.current_note_id)
            return [note] if note else []
        if scope == SCOPE_CURRENT_DIRECTORY:
            return list_notes_under_path_for_export(self.context.current_directory_path)
        if scope in {SCOPE_SEARCH, SCOPE_TAG, SCOPE_SMART_VIEW}:
            return list_notes_by_ids_for_export(self.context.visible_note_ids)
        if scope == SCOPE_SELECTED_NOTES:
            note_ids = self.selected_note_ids()
            if not note_ids:
                raise ValueError("请选择至少一条笔记。")
            return list_notes_by_ids_for_export(note_ids)
        if scope == SCOPE_SELECTED_DIRECTORIES:
            paths = self.selected_directory_paths()
            if not paths:
                raise ValueError("请选择至少一个目录。")
            return notes_for_directory_paths(paths)
        if scope == SCOPE_ALL_NOTES:
            return list_notes_for_export()
        return []


class MarkdownExporter:
    def export(self, notes: list[dict], scope: str) -> ExportResult:
        MARKDOWN_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        filename = self.output_filename(notes, scope)
        path = MARKDOWN_EXPORT_DIR / filename
        content = "\n\n---\n\n".join(note_to_markdown(note) for note in notes)
        path.write_text(content + "\n", encoding="utf-8")
        return ExportResult(path=path, note_count=len(notes))

    def output_filename(self, notes: list[dict], scope: str) -> str:
        timestamp = now_shanghai().strftime("%Y%m%d_%H%M%S")
        if len(notes) == 1:
            title = safe_filename(notes[0]["title"] or "未命名笔记")
            return f"{title}_{timestamp}.md"
        return f"s_notes_{scope}_{timestamp}.md"


class JsonExporter:
    def export(self, notes: list[dict], scope: str) -> ExportResult:
        JSON_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = now_shanghai().strftime("%Y%m%d_%H%M%S")
        path = JSON_EXPORT_DIR / f"s_notes_{scope}_{timestamp}.json"
        payload = {
            "exported_at": now_shanghai().strftime("%Y-%m-%d %H:%M"),
            "export_type": "ordinary",
            "scope": scope,
            "note_count": len(notes),
            "notes": [note_to_json(note) for note in notes],
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return ExportResult(path=path, note_count=len(notes))


class SqliteBackupExporter:
    def export(self) -> ExportResult:
        if not DATABASE_PATH.exists():
            raise FileNotFoundError(f"数据库文件不存在：{DATABASE_PATH}")
        SQLITE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = now_shanghai().strftime("%Y%m%d_%H%M%S")
        path = SQLITE_BACKUP_DIR / f"s_notes_backup_{timestamp}.db"
        shutil.copy2(DATABASE_PATH, path)
        return ExportResult(path=path, note_count=0)


def note_to_markdown(note: dict) -> str:
    tags = parse_tags(note.get("tags", ""))
    tag_text = " ".join(f"#{tag}" for tag in tags) if tags else "无"
    directory = " / ".join(
        [
            note.get("main_category", ""),
            note.get("sub_category", ""),
            note.get("sub_sub_category", ""),
        ]
    )
    body = html_to_markdown(note.get("content_html", ""))
    return "\n".join(
        [
            f"# {note.get('title') or '未命名笔记'}",
            "",
            f"- 创建时间：{format_display_datetime(note.get('created_at', ''))}",
            f"- 更新时间：{format_display_datetime(note.get('updated_at', ''))}",
            f"- 目录：{directory}",
            f"- 标签：{tag_text}",
            f"- AI 可见性：{note.get('ai_visibility', '')}",
            "",
            "---",
            "",
            body.strip(),
        ]
    ).rstrip()


def note_to_json(note: dict) -> dict:
    return {
        "id": note["id"],
        "title": note["title"],
        "content_html": note.get("content_html", ""),
        "content_text": html_to_text(note.get("content_html", "")),
        "main_category": note["main_category"],
        "sub_category": note["sub_category"],
        "sub_sub_category": note["sub_sub_category"],
        "tags": parse_tags(note.get("tags", "")),
        "ai_visibility": note["ai_visibility"],
        "is_pinned": note["is_pinned"],
        "created_at": format_display_datetime(note["created_at"]),
        "updated_at": format_display_datetime(note["updated_at"]),
    }


def safe_filename(value: str, max_length: int = 80) -> str:
    cleaned = re.sub(r'[/:*?"<>|\\]+', "", value).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = "未命名笔记"
    return cleaned[:max_length].rstrip()


def notes_for_directory_paths(paths: list[tuple[str, ...]]) -> list[dict]:
    notes_by_id: dict[int, dict] = {}
    for path in paths:
        for note in list_notes_under_path_for_export(path):
            notes_by_id[note["id"]] = note
    return sorted(
        notes_by_id.values(),
        key=lambda note: (note["updated_at"], note["created_at"], note["id"]),
        reverse=True,
    )


def note_path_label(note: dict) -> str:
    return " / ".join(
        [
            note.get("main_category", ""),
            note.get("sub_category", ""),
            note.get("sub_sub_category", ""),
        ]
    )


def directory_depth(directory: dict, directories: list[dict]) -> int:
    rows_by_id = {row["id"]: row for row in directories}
    depth = 0
    parent_id = directory["parent_id"]
    while parent_id is not None and parent_id in rows_by_id:
        depth += 1
        parent_id = rows_by_id[parent_id]["parent_id"]
    return depth


def directory_path_from_rows(
    directory_id: int, rows_by_id: dict[int, dict]
) -> tuple[str, ...]:
    names: list[str] = []
    current_id = directory_id
    while current_id is not None and current_id in rows_by_id:
        row = rows_by_id[current_id]
        names.append(row["name"])
        current_id = row["parent_id"]
    return tuple(reversed(names))


def clean_export_html(content_html: str) -> str:
    text = content_html or ""
    text = re.sub(r"(?is)<head\b[^>]*>.*?</head\s*>", "", text)
    text = re.sub(r"(?is)<style\b[^>]*>.*?</style\s*>", "", text)
    text = re.sub(r"(?is)<script\b[^>]*>.*?</script\s*>", "", text)
    text = re.sub(r"(?is)<meta\b[^>]*>", "", text)
    text = re.sub(r"(?is)<title\b[^>]*>.*?</title\s*>", "", text)
    body_match = re.search(r"(?is)<body\b[^>]*>(.*?)</body\s*>", text)
    if body_match:
        text = body_match.group(1)
    return text


class MarkdownHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.list_stack: list[str] = []
        self.ordered_counts: list[int] = []
        self.in_pre = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"h1", "h2", "h3"}:
            level = {"h1": "#", "h2": "##", "h3": "###"}[tag]
            self.ensure_blank_line()
            self.parts.append(f"{level} ")
        elif tag in {"p", "div"}:
            self.ensure_blank_line()
        elif tag == "br":
            self.parts.append("\n")
        elif tag in {"strong", "b"}:
            self.parts.append("**")
        elif tag in {"em", "i"}:
            self.parts.append("*")
        elif tag == "blockquote":
            self.ensure_blank_line()
            self.parts.append("> ")
        elif tag == "pre":
            self.ensure_blank_line()
            self.parts.append("```\n")
            self.in_pre = True
        elif tag == "code" and not self.in_pre:
            self.parts.append("`")
        elif tag == "ul":
            self.list_stack.append("ul")
            self.ensure_blank_line()
        elif tag == "ol":
            self.list_stack.append("ol")
            self.ordered_counts.append(1)
            self.ensure_blank_line()
        elif tag == "li":
            self.parts.append("\n")
            if self.list_stack and self.list_stack[-1] == "ol":
                count = self.ordered_counts[-1]
                self.parts.append(f"{count}. ")
                self.ordered_counts[-1] = count + 1
            else:
                self.parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h1", "h2", "h3", "p", "div", "blockquote"}:
            self.parts.append("\n\n")
        elif tag in {"strong", "b"}:
            self.parts.append("**")
        elif tag in {"em", "i"}:
            self.parts.append("*")
        elif tag == "pre":
            self.parts.append("\n```\n\n")
            self.in_pre = False
        elif tag == "code" and not self.in_pre:
            self.parts.append("`")
        elif tag == "ul":
            if self.list_stack:
                self.list_stack.pop()
            self.parts.append("\n")
        elif tag == "ol":
            if self.list_stack:
                self.list_stack.pop()
            if self.ordered_counts:
                self.ordered_counts.pop()
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.in_pre:
            self.parts.append(data)
        else:
            self.parts.append(re.sub(r"\s+", " ", data))

    def ensure_blank_line(self) -> None:
        text = "".join(self.parts)
        if text and not text.endswith("\n\n"):
            self.parts.append("\n\n")

    def markdown(self) -> str:
        text = "".join(self.parts)
        text = html.unescape(text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        return text.strip()


def html_to_markdown(content_html: str) -> str:
    if not content_html:
        return ""
    content_html = clean_export_html(content_html)
    parser = MarkdownHTMLParser()
    try:
        parser.feed(content_html)
        markdown = parser.markdown()
    except Exception:
        markdown = html_to_text(content_html)
    return markdown or html_to_text(content_html)


def html_to_text(content_html: str) -> str:
    text = clean_export_html(content_html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|h[1-6]|li|blockquote)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
