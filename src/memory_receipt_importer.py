from __future__ import annotations

import re
from dataclasses import dataclass

from src.ai_targets import normalize_ai_targets, validate_ai_visibility
from src.database import (
    DEFAULT_AI_VISIBILITY,
    DEFAULT_MAIN_CATEGORY,
    DEFAULT_SUB_CATEGORY,
    DEFAULT_SUB_SUB_CATEGORY,
)
from src.tag_manager import format_tags, parse_tags
from src.time_utils import now_shanghai


START_MARKER = "[S-notes 记忆回执]"
END_MARKER = "[/S-notes 记忆回执]"

FIELD_ALIASES = {
    "来源": "source",
    "动作": "action",
    "标题": "title",
    "主分类": "main_category",
    "子分类": "sub_category",
    "子子分类": "sub_sub_category",
    "标签": "tags",
    "AI可见性": "ai_visibility",
    "AI 可见性": "ai_visibility",
    "适用对象": "ai_targets",
    "正文": "body",
}

DEFAULT_SOURCE = "未知"
DEFAULT_ACTION = "新增"


@dataclass
class ParsedMemoryReceipt:
    raw_text: str
    parse_status: str
    parse_error: str
    source: str
    action: str
    title: str
    main_category: str
    sub_category: str
    sub_sub_category: str
    tags: str
    ai_visibility: str
    ai_targets: str
    body: str

    def to_record(self) -> dict:
        return {
            "raw_text": self.raw_text,
            "parse_status": self.parse_status,
            "parse_error": self.parse_error,
            "source": self.source,
            "action": self.action,
            "title": self.title,
            "main_category": self.main_category,
            "sub_category": self.sub_category,
            "sub_sub_category": self.sub_sub_category,
            "tags": self.tags,
            "ai_visibility": self.ai_visibility,
            "ai_targets": self.ai_targets,
            "body": self.body,
        }


def parse_memory_receipt(text: str) -> ParsedMemoryReceipt:
    raw_text = text or ""
    defaults = _default_values(raw_text)
    content, marker_error = _extract_marked_content(raw_text)
    if marker_error:
        return ParsedMemoryReceipt(
            raw_text=raw_text,
            parse_status="failed",
            parse_error=marker_error,
            **defaults,
        )

    try:
        fields = _parse_fields(content)
        normalized = _normalize_fields(fields, raw_text)
    except Exception as error:
        values = _default_values(raw_text)
        values["body"] = raw_text
        return ParsedMemoryReceipt(
            raw_text=raw_text,
            parse_status="failed",
            parse_error=str(error),
            **values,
        )

    return ParsedMemoryReceipt(
        raw_text=raw_text,
        parse_status="parsed",
        parse_error="",
        **normalized,
    )


def _default_values(raw_text: str) -> dict:
    timestamp = now_shanghai().strftime("%Y-%m-%d %H:%M")
    return {
        "source": DEFAULT_SOURCE,
        "action": DEFAULT_ACTION,
        "title": f"未命名 AI 回执 {timestamp}",
        "main_category": DEFAULT_MAIN_CATEGORY,
        "sub_category": DEFAULT_SUB_CATEGORY,
        "sub_sub_category": DEFAULT_SUB_SUB_CATEGORY,
        "tags": "",
        "ai_visibility": DEFAULT_AI_VISIBILITY,
        "ai_targets": "",
        "body": raw_text,
    }


def _extract_marked_content(raw_text: str) -> tuple[str, str]:
    start = raw_text.find(START_MARKER)
    end = raw_text.find(END_MARKER)
    if start < 0 or end < 0 or end <= start:
        return raw_text, "未找到完整的 S-notes 记忆回执头尾标记。"
    content = raw_text[start + len(START_MARKER):end]
    return content.strip(), ""


FIELD_RE = re.compile(r"^\s*([^：:\n]{1,20})\s*[：:]\s*(.*)$")


def _parse_fields(content: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_key, current_lines
        if current_key is not None:
            fields[current_key] = "\n".join(current_lines).strip()
        current_key = None
        current_lines = []

    for line in content.splitlines():
        match = FIELD_RE.match(line)
        key = _field_key(match.group(1)) if match else None
        if key is not None:
            flush()
            current_key = key
            current_lines = [match.group(2)]
            continue
        if current_key is None:
            continue
        current_lines.append(line)
    flush()
    return fields


def _field_key(label: str) -> str | None:
    compact = re.sub(r"\s+", "", label)
    return FIELD_ALIASES.get(label.strip()) or FIELD_ALIASES.get(compact)


def _normalize_fields(fields: dict[str, str], raw_text: str) -> dict:
    values = _default_values(raw_text)
    for key in (
        "source",
        "action",
        "title",
        "main_category",
        "sub_category",
        "sub_sub_category",
        "body",
    ):
        value = fields.get(key, "").strip()
        if value:
            values[key] = value

    values["action"] = values["action"] or DEFAULT_ACTION
    if values["action"] not in {"新增", "更新"}:
        values["action"] = values["action"].strip() or DEFAULT_ACTION

    values["tags"] = _normalize_tags(fields.get("tags", ""))
    values["ai_visibility"] = _normalize_ai_visibility(fields.get("ai_visibility", ""))
    values["ai_targets"] = format_ai_targets(fields.get("ai_targets", ""))
    return values


def _normalize_ai_visibility(value: str) -> str:
    compact = re.sub(r"\s+", "", value or "")
    if not compact:
        return DEFAULT_AI_VISIBILITY
    if compact in {"不导出", "不给AI", "不导出给AI"}:
        return DEFAULT_AI_VISIBILITY
    if compact in {"可直接给AI", "直接给AI"}:
        return "可直接给 AI"
    if compact in {"脱敏", "脱敏后给AI"}:
        return "脱敏后给 AI"
    try:
        return validate_ai_visibility(value.strip())
    except ValueError:
        return DEFAULT_AI_VISIBILITY


def format_ai_targets(value: str | list[str]) -> str:
    if isinstance(value, str):
        raw_targets = _split_targets(value)
    else:
        raw_targets = value
    if not raw_targets:
        return ""
    return ", ".join(normalize_ai_targets(raw_targets))


def parse_ai_targets_text(value: str) -> list[str]:
    if not value.strip():
        return []
    return normalize_ai_targets(_split_targets(value))


def _normalize_tags(value: str) -> str:
    compact = re.sub(r"\s+", "", value or "").lower()
    if compact in {"", "无", "none", "null"}:
        return ""
    return format_tags(parse_tags(value))


def _split_targets(value: str) -> list[str]:
    compact = re.sub(r"\s+", "", value or "").lower()
    if compact in {"", "无", "none", "null"}:
        return []
    normalized = value.replace("，", ",").replace("、", ",")
    parts: list[str] = []
    for chunk in normalized.split(","):
        parts.extend(part for part in chunk.split() if part)
    return [part.strip() for part in parts if part.strip()]
