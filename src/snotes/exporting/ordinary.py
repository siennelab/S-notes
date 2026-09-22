"""Filesystem owner for frozen-R1 ordinary Markdown, JSON, and DB backup exports."""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.snotes.domain.models import ExportResult, Note
from src.snotes.domain.tags import parse_tags
from src.snotes.domain.markdown import markdown_to_plain_text


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
    body = note.content_markdown
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
            body,
        ]
    )


def note_to_json(note: Note) -> dict:
    return {
        "id": note.id,
        "title": note.title,
        "content_markdown": note.content_markdown,
        "content_text": markdown_to_plain_text(note.content_markdown),
        "main_category": note.main_category,
        "sub_category": note.sub_category,
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
