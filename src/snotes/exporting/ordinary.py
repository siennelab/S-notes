"""Filesystem owner for frozen-R1 ordinary Markdown, JSON, and DB backup exports."""

from __future__ import annotations

import html
import json
import re
import shutil
from collections.abc import Callable, Sequence
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

from src.snotes.domain.models import ExportResult, Note
from src.snotes.domain.tags import parse_tags


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class OrdinaryExportWriter:
    def __init__(
        self,
        output_root: Path,
        database_path: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.output_root = Path(output_root)
        self.database_path = Path(database_path)
        self.clock = clock or (lambda: datetime.now(SHANGHAI_TZ))

    def write_markdown(self, notes: Sequence[Note], scope: str) -> ExportResult:
        directory = self.output_root / "markdown"
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = self.clock().strftime("%Y%m%d_%H%M%S")
        if len(notes) == 1:
            filename = f"{safe_filename(notes[0].title or '未命名笔记')}_{timestamp}.md"
        else:
            filename = f"s_notes_{scope}_{timestamp}.md"
        path = directory / filename
        content = "\n\n---\n\n".join(note_to_markdown(note) for note in notes)
        path.write_text(content + "\n", encoding="utf-8")
        return ExportResult(str(path), len(notes))

    def write_json(self, notes: Sequence[Note], scope: str) -> ExportResult:
        directory = self.output_root / "json"
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = self.clock().strftime("%Y%m%d_%H%M%S")
        path = directory / f"s_notes_{scope}_{timestamp}.json"
        payload = {
            "exported_at": self.clock().strftime("%Y-%m-%d %H:%M"),
            "export_type": "ordinary",
            "scope": scope,
            "note_count": len(notes),
            "notes": [note_to_json(note) for note in notes],
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return ExportResult(str(path), len(notes))

    def write_database_backup(self) -> ExportResult:
        if not self.database_path.exists():
            raise FileNotFoundError(f"数据库文件不存在：{self.database_path}")
        directory = self.output_root / "backups"
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = self.clock().strftime("%Y%m%d_%H%M%S")
        path = directory / f"s_notes_backup_{timestamp}.db"
        shutil.copy2(self.database_path, path)
        return ExportResult(str(path), 0)


def note_to_markdown(note: Note) -> str:
    tags = parse_tags(note.tags)
    tag_text = " ".join(f"#{tag}" for tag in tags) if tags else "无"
    body = html_to_markdown(note.content_html)
    return "\n".join(
        [
            f"# {note.title or '未命名笔记'}",
            "",
            f"- 创建时间：{format_display_datetime(note.created_at)}",
            f"- 更新时间：{format_display_datetime(note.updated_at)}",
            f"- 目录：{' / '.join(note.location.values())}",
            f"- 标签：{tag_text}",
            f"- AI 可见性：{note.ai_visibility}",
            "",
            "---",
            "",
            body.strip(),
        ]
    ).rstrip()


def note_to_json(note: Note) -> dict:
    return {
        "id": note.id,
        "title": note.title,
        "content_html": note.content_html,
        "content_text": html_to_text(note.content_html),
        "main_category": note.main_category,
        "sub_category": note.sub_category,
        "sub_sub_category": note.sub_sub_category,
        "tags": parse_tags(note.tags),
        "ai_visibility": note.ai_visibility,
        "is_pinned": note.is_pinned,
        "created_at": format_display_datetime(note.created_at),
        "updated_at": format_display_datetime(note.updated_at),
    }


def safe_filename(value: str, max_length: int = 80) -> str:
    cleaned = re.sub(r'[/:*?"<>|\\]+', "", value).strip()
    cleaned = re.sub(r"\s+", " ", cleaned) or "未命名笔记"
    return cleaned[:max_length].rstrip()


def format_display_datetime(value: str) -> str:
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=SHANGHAI_TZ)
    return timestamp.astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M")


def clean_export_html(content_html: str) -> str:
    text = content_html or ""
    text = re.sub(r"(?is)<head\b[^>]*>.*?</head\s*>", "", text)
    text = re.sub(r"(?is)<style\b[^>]*>.*?</style\s*>", "", text)
    text = re.sub(r"(?is)<script\b[^>]*>.*?</script\s*>", "", text)
    text = re.sub(r"(?is)<meta\b[^>]*>", "", text)
    text = re.sub(r"(?is)<title\b[^>]*>.*?</title\s*>", "", text)
    body_match = re.search(r"(?is)<body\b[^>]*>(.*?)</body\s*>", text)
    return body_match.group(1) if body_match else text


class MarkdownHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.list_stack: list[str] = []
        self.ordered_counts: list[int] = []
        self.in_pre = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"h1", "h2", "h3"}:
            self.ensure_blank_line()
            self.parts.append({"h1": "# ", "h2": "## ", "h3": "### "}[tag])
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
        elif tag in {"ul", "ol"}:
            self.list_stack.append(tag)
            if tag == "ol":
                self.ordered_counts.append(1)
            self.ensure_blank_line()
        elif tag == "li":
            self.parts.append("\n")
            if self.list_stack and self.list_stack[-1] == "ol":
                count = self.ordered_counts[-1]
                self.parts.append(f"{count}. ")
                self.ordered_counts[-1] += 1
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
        elif tag in {"ul", "ol"}:
            if self.list_stack:
                self.list_stack.pop()
            if tag == "ol" and self.ordered_counts:
                self.ordered_counts.pop()
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data if self.in_pre else re.sub(r"\s+", " ", data))

    def ensure_blank_line(self) -> None:
        text = "".join(self.parts)
        if text and not text.endswith("\n\n"):
            self.parts.append("\n\n")

    def markdown(self) -> str:
        text = html.unescape("".join(self.parts))
        text = re.sub(r"\n{3,}", "\n\n", text)
        return re.sub(r"[ \t]+\n", "\n", text).strip()


def html_to_markdown(content_html: str) -> str:
    if not content_html:
        return ""
    parser = MarkdownHTMLParser()
    try:
        parser.feed(clean_export_html(content_html))
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
    return re.sub(r"\n{3,}", "\n\n", text).strip()
