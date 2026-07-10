from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from html.parser import HTMLParser

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.ai_targets import AI_TARGETS
from src.database import list_notes_for_link_drawer
from src.tag_manager import parse_tags
from src.time_utils import format_display_datetime


WIKI_LINK_RE = re.compile(r"\[\[\s*([^\[\]\n]+?)\s*\]\]")
SECTION_TITLES = (
    ("manual_references", "手动引用", "暂无手动引用"),
    ("backlinks", "被谁引用", "暂无被谁引用"),
    ("same_directory", "同目录", "暂无同目录笔记"),
    ("same_tags", "同标签", "暂无同标签笔记"),
    ("text_related", "标题 / 正文相关", "暂无标题 / 正文相关"),
    ("ai_related", "AI 资料相关", "暂无 AI 资料相关"),
)


class _PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        clean_tag = tag.lower()
        if clean_tag in {"head", "style", "script"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if clean_tag in {"br", "p", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        clean_tag = tag.lower()
        if clean_tag in {"head", "style", "script"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if clean_tag in {"p", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"\s+", " ", "".join(self.parts)).strip()


def html_to_visible_text(value: str) -> str:
    if not value:
        return ""
    parser = _PlainTextParser()
    parser.feed(value)
    parser.close()
    return parser.text()


def extract_manual_reference_titles(text: str) -> list[str]:
    titles: list[str] = []
    seen: set[str] = set()
    for match in WIKI_LINK_RE.finditer(text or ""):
        title = match.group(1).strip()
        if title and title not in seen:
            titles.append(title)
            seen.add(title)
    return titles


def contains_manual_reference(text: str, title: str) -> bool:
    return title in extract_manual_reference_titles(text)


@dataclass
class LinkResult:
    note_id: int
    title: str
    subtitle: str = ""
    reason: str = ""


@dataclass
class LinkDrawerData:
    sections: dict[str, list[LinkResult]] = field(default_factory=dict)


def _path_text(note: dict) -> str:
    parts = [note["main_category"]]
    if note["sub_category"] != "无":
        parts.append(note["sub_category"])
    if note["sub_sub_category"] != "无":
        parts.append(note["sub_sub_category"])
    return " / ".join(parts)


def _subtitle(note: dict, *, reason: str = "", shared: list[str] | None = None) -> str:
    details: list[str] = []
    if reason:
        details.append(reason)
    if shared:
        details.append("共同：" + "、".join(shared))
    details.append(_path_text(note))
    details.append(format_display_datetime(note["updated_at"]))
    return " · ".join(part for part in details if part)


def _add_unique(results: list[LinkResult], note: dict, subtitle: str, reason: str = "") -> None:
    if any(result.note_id == note["id"] for result in results):
        return
    results.append(
        LinkResult(
            note_id=note["id"],
            title=note["title"] or "未命名笔记",
            subtitle=subtitle,
            reason=reason,
        )
    )


def build_link_drawer_data(
    current_note_id: int | None, database_path: Path | None = None
) -> LinkDrawerData:
    sections = {key: [] for key, _title, _empty in SECTION_TITLES}
    if current_note_id is None:
        return LinkDrawerData(sections=sections)

    notes = list_notes_for_link_drawer(database_path) if database_path else list_notes_for_link_drawer()
    for note in notes:
        note["plain_text"] = html_to_visible_text(note.get("content_html", ""))
        note["tag_list"] = parse_tags(note.get("tags", ""))

    current = next((note for note in notes if note["id"] == current_note_id), None)
    if current is None:
        return LinkDrawerData(sections=sections)

    candidates = [note for note in notes if note["id"] != current_note_id]
    title_lookup: dict[str, list[dict]] = {}
    for note in candidates:
        title_lookup.setdefault(note["title"], []).append(note)

    manual_note_ids: set[int] = set()
    for title in extract_manual_reference_titles(current["plain_text"]):
        for note in title_lookup.get(title, []):
            _add_unique(
                sections["manual_references"],
                note,
                _subtitle(note, reason="正文手动引用"),
                "正文手动引用",
            )
            manual_note_ids.add(note["id"])

    if current["title"]:
        for note in candidates:
            if contains_manual_reference(note["plain_text"], current["title"]):
                _add_unique(
                    sections["backlinks"],
                    note,
                    _subtitle(note, reason="对方手动引用当前标题"),
                    "对方手动引用当前标题",
                )

    current_path = (
        current["main_category"],
        current["sub_category"],
        current["sub_sub_category"],
    )
    for note in sorted(candidates, key=lambda item: (item["updated_at"], item["id"]), reverse=True):
        note_path = (
            note["main_category"],
            note["sub_category"],
            note["sub_sub_category"],
        )
        if note_path == current_path:
            _add_unique(
                sections["same_directory"],
                note,
                _subtitle(note, reason="目录相同"),
                "目录相同",
            )

    current_tags = set(current["tag_list"])
    if current_tags:
        for note in sorted(candidates, key=lambda item: (item["updated_at"], item["id"]), reverse=True):
            shared_tags = sorted(current_tags.intersection(note["tag_list"]))
            if shared_tags:
                _add_unique(
                    sections["same_tags"],
                    note,
                    _subtitle(note, reason="共享标签", shared=shared_tags),
                    "共享标签",
                )

    current_title = current["title"].strip()
    current_text = current["plain_text"]
    for note in candidates:
        other_title = note["title"].strip()
        reasons: list[str] = []
        if current_title and (
            current_title in note["title"] or current_title in note["plain_text"]
        ):
            reasons.append("对方提到当前标题")
        if other_title and other_title in current_text:
            reasons.append("正文提到该标题")
        if current_title and other_title:
            if current_title in other_title or other_title in current_title:
                reasons.append("标题包含")
        if reasons and note["id"] not in manual_note_ids:
            reason = "、".join(dict.fromkeys(reasons))
            _add_unique(
                sections["text_related"],
                note,
                _subtitle(note, reason=reason),
                reason,
            )

    current_targets = [target for target in AI_TARGETS if target in current["ai_targets"]]
    if current_targets:
        current_target_set = set(current_targets)
        for note in sorted(candidates, key=lambda item: (item["updated_at"], item["id"]), reverse=True):
            shared_targets = [
                target for target in AI_TARGETS if target in current_target_set and target in note["ai_targets"]
            ]
            if shared_targets:
                _add_unique(
                    sections["ai_related"],
                    note,
                    _subtitle(note, reason="共享 AI 对象", shared=shared_targets),
                    "共享 AI 对象",
                )

    return LinkDrawerData(sections=sections)


class LinkSectionWidget(QWidget):
    noteSelected = Signal(int)

    def __init__(self, title: str, empty_text: str, parent=None) -> None:
        super().__init__(parent)
        self.title = title
        self.empty_text = empty_text
        self.expanded = True
        self.result_count = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.header_button = QPushButton()
        self.header_button.setObjectName("linkSectionHeader")
        self.header_button.setCursor(Qt.PointingHandCursor)
        self.header_button.clicked.connect(self.toggle_expanded)
        layout.addWidget(self.header_button)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 8)
        self.body_layout.setSpacing(6)
        layout.addWidget(self.body)
        self.set_results([])

    def toggle_expanded(self) -> None:
        self.expanded = not self.expanded
        self.body.setVisible(self.expanded)
        self.update_header()

    def update_header(self) -> None:
        marker = "▾" if self.expanded else "▸"
        self.header_button.setText(f"{marker} {self.title} · {self.result_count}")

    def set_results(self, results: list[LinkResult]) -> None:
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        self.result_count = len(results)
        self.update_header()
        if not results:
            empty = QLabel(self.empty_text)
            empty.setObjectName("linkEmptyText")
            empty.setWordWrap(True)
            self.body_layout.addWidget(empty)
            return

        for result in results:
            row = LinkResultWidget(result)
            row.noteSelected.connect(self.noteSelected)
            self.body_layout.addWidget(row)


class LinkResultWidget(QFrame):
    noteSelected = Signal(int)

    def __init__(self, result: LinkResult, parent=None) -> None:
        super().__init__(parent)
        self.result = result
        self.setObjectName("linkResult")
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        title = QLabel(result.title)
        title.setObjectName("linkResultTitle")
        title.setWordWrap(True)
        layout.addWidget(title)

        if result.subtitle:
            subtitle = QLabel(result.subtitle)
            subtitle.setObjectName("linkResultSubtitle")
            subtitle.setWordWrap(True)
            layout.addWidget(subtitle)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.noteSelected.emit(self.result.note_id)
        super().mouseReleaseEvent(event)


class LinkDrawer(QWidget):
    noteSelected = Signal(int)
    closeRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("linkDrawer")
        self.setFixedWidth(340)
        self.sections: dict[str, LinkSectionWidget] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("关联")
        title.setObjectName("linkDrawerTitle")
        header.addWidget(title, stretch=1)
        close_button = QPushButton("×")
        close_button.setObjectName("linkDrawerCloseButton")
        close_button.setToolTip("关闭关联抽屉")
        close_button.setFixedSize(24, 22)
        close_button.clicked.connect(self.closeRequested)
        header.addWidget(close_button)
        layout.addLayout(header)

        self.empty_state = QLabel("未选择笔记")
        self.empty_state.setObjectName("linkDrawerEmptyState")
        self.empty_state.setWordWrap(True)
        layout.addWidget(self.empty_state)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("linkDrawerScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        self.sections_layout = QVBoxLayout(content)
        self.sections_layout.setContentsMargins(0, 0, 0, 0)
        self.sections_layout.setSpacing(8)
        for key, section_title, empty_text in SECTION_TITLES:
            section = LinkSectionWidget(section_title, empty_text)
            section.noteSelected.connect(self.noteSelected)
            self.sections[key] = section
            self.sections_layout.addWidget(section)
        self.sections_layout.addStretch(1)
        self.scroll.setWidget(content)
        layout.addWidget(self.scroll, stretch=1)

    def refresh(self, current_note_id: int | None) -> None:
        self.empty_state.setVisible(current_note_id is None)
        self.scroll.setVisible(current_note_id is not None)
        data = build_link_drawer_data(current_note_id)
        for key, _title, _empty in SECTION_TITLES:
            self.sections[key].set_results(data.sections.get(key, []))
