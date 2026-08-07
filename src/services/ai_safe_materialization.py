from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from src.ai_targets import (
    AI_VISIBILITY_FULL,
    AI_VISIBILITY_REDACTED,
    normalize_ai_visibility,
)
from src.redaction import RedactionUnavailableError
from src.tag_manager import parse_tags


RedactionTransform = Callable[
    [str, list[tuple[str, str]]],
    tuple[str, int],
]


def materialize_ai_safe_note(
    note: Mapping[str, Any],
    *,
    authorized: bool,
    replacements: list[tuple[str, str]],
    body_renderer: Callable[[str], str],
    redaction_transform: RedactionTransform,
    tag_records: Sequence[Mapping[str, Any]] | None = None,
) -> dict | None:
    """Derive a new AI-safe record without mutating the source note."""
    visibility = normalize_ai_visibility(_note_value(note, "ai_visibility"))
    if not authorized or visibility not in {
        AI_VISIBILITY_FULL,
        AI_VISIBILITY_REDACTED,
    }:
        return None
    if visibility == AI_VISIBILITY_REDACTED and not replacements:
        raise RedactionUnavailableError(
            "本地脱敏规则不可用：没有有效 replacement。"
        )

    source_tag_records = (
        [
            {
                "tag_id": int(item.get("tag_id", 0)),
                "name": str(item.get("name", "")),
            }
            for item in tag_records
        ]
        if tag_records is not None
        else [
            {"tag_id": 0, "name": tag}
            for tag in parse_tags(str(_note_value(note, "tags") or ""))
        ]
    )
    note_id = int(_note_value(note, "id"))
    values = {
        "id": note_id,
        "note_id": note_id,
        "directory_id": _note_value(note, "directory_id"),
        "title": str(_note_value(note, "title") or ""),
        "directory": " / ".join(
            str(_note_value(note, key) or "")
            for key in ("main_category", "sub_category", "sub_sub_category")
        ),
        "tags": [item["name"] for item in source_tag_records],
        "tag_ids": [item["tag_id"] for item in source_tag_records],
        "tag_records": [dict(item) for item in source_tag_records],
        "body": body_renderer(str(_note_value(note, "content_html") or "")),
        "created_at": _note_value(note, "created_at"),
        "updated_at": _note_value(note, "updated_at"),
        "ai_visibility": visibility,
        "visibility": visibility,
        "redacted": visibility == AI_VISIBILITY_REDACTED,
        "redaction_count": 0,
    }

    if visibility == AI_VISIBILITY_REDACTED:
        redaction_count = 0
        for key in ("title", "directory", "body"):
            values[key], count = redaction_transform(values[key], replacements)
            redaction_count += count

        redacted_tags = []
        redacted_tag_records = []
        for item in source_tag_records:
            redacted_name, count = redaction_transform(item["name"], replacements)
            redaction_count += count
            redacted_tags.append(redacted_name)
            redacted_tag_records.append(
                {"tag_id": item["tag_id"], "name": redacted_name}
            )
        values["tags"] = redacted_tags
        values["tag_records"] = redacted_tag_records
        values["redaction_count"] = redaction_count

    return values


def _note_value(note: Mapping[str, Any], key: str) -> Any:
    try:
        return note[key]
    except (KeyError, IndexError):
        return None
