"""Validate the untrusted stored payload for a pending metadata change."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from src.snotes.domain.models import NoteLocation


@dataclass(frozen=True, slots=True)
class PendingMetadataPlan:
    payload: dict[str, object]
    desired_title: str | None
    desired_location: NoteLocation | None
    desired_tags: tuple[str, ...] | None
    changed: bool


def prepare_pending_metadata_plan(
    *,
    current_title: str,
    current_location: NoteLocation,
    current_tags: Sequence[str],
    title: str | None = None,
    location: NoteLocation | None = None,
    add_tags: Sequence[str] | None = None,
    remove_tags: Sequence[str] | None = None,
) -> PendingMetadataPlan:
    """Prepare the one internal metadata proposal shape used by local and Cloud."""

    desired_title = (
        title.strip() or "未命名笔记" if title is not None else None
    )
    additions = set(normalize_metadata_tags(add_tags or ()))
    removals = set(normalize_metadata_tags(remove_tags or ()))
    if additions & removals:
        raise ValueError("同一标签不能同时添加和移除。")

    existing_tags = list(current_tags)
    desired_tags: tuple[str, ...] | None = None
    if additions or removals:
        prepared_tags = [tag for tag in existing_tags if tag not in removals]
        prepared_tags.extend(
            tag for tag in sorted(additions) if tag not in prepared_tags
        )
        desired_tags = tuple(prepared_tags)

    changed = any(
        (
            desired_title is not None and desired_title != current_title,
            location is not None and location != current_location,
            desired_tags is not None and set(desired_tags) != set(existing_tags),
        )
    )
    payload: dict[str, object] = {}
    if desired_title is not None:
        payload["title"] = desired_title
    if location is not None:
        payload["directory"] = list(location.values())
    if desired_tags is not None:
        payload["tags"] = list(desired_tags)
    return PendingMetadataPlan(
        payload=payload,
        desired_title=desired_title,
        desired_location=location,
        desired_tags=desired_tags,
        changed=changed,
    )


def normalize_metadata_tags(values: Sequence[str]) -> tuple[str, ...]:
    """Preserve the established local MCP case-preserving tag behavior."""

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value).strip().lstrip("#").strip()
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            normalized.append(clean)
    return tuple(normalized)


def parse_pending_metadata_payload(raw: str) -> dict[str, object]:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise ValueError("元数据提案内容无效。") from error
    if not isinstance(payload, dict) or not payload or not set(payload) <= {
        "title", "directory", "tags"
    }:
        raise ValueError("元数据提案包含不允许修改的字段。")

    clean: dict[str, object] = {}
    if "title" in payload:
        title = payload["title"]
        if not isinstance(title, str) or not title.strip() or title != title.strip():
            raise ValueError("元数据提案的标题无效。")
        clean["title"] = title
    if "directory" in payload:
        directory = payload["directory"]
        if (
            not isinstance(directory, list)
            or len(directory) != 2
            or any(not isinstance(part, str) or not part.strip() for part in directory)
        ):
            raise ValueError("元数据提案的目录无效。")
        clean["directory"] = tuple(directory)
    if "tags" in payload:
        tags = payload["tags"]
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise ValueError("元数据提案的标签无效。")
        clean["tags"] = tuple(tags)
    return clean


def parse_cloud_pending_metadata_payload(raw: str) -> dict[str, object]:
    """Validate the public Cloud metadata contract without rewriting it."""

    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise ValueError("Cloud 资料提案内容无效。") from error
    allowed = {"title", "directory_id", "add_tags", "remove_tags"}
    if not isinstance(payload, dict) or not set(payload) <= allowed:
        raise ValueError("Cloud 资料提案包含不允许修改的字段。")
    clean: dict[str, object] = {}
    if "title" in payload:
        title = payload["title"]
        if title is not None and (
            not isinstance(title, str) or not 1 <= len(title) <= 500
        ):
            raise ValueError("Cloud 资料提案的标题无效。")
        clean["title"] = title
    if "directory_id" in payload:
        directory_id = payload["directory_id"]
        if directory_id is not None and (
            type(directory_id) is not int
            or not 1 <= directory_id <= 9_223_372_036_854_775_807
        ):
            raise ValueError("Cloud 资料提案的目录无效。")
        clean["directory_id"] = directory_id
    for key in ("add_tags", "remove_tags"):
        if key not in payload:
            continue
        values = payload[key]
        if values is not None and (
            not isinstance(values, list)
            or not 1 <= len(values) <= 32
            or any(
                not isinstance(value, str) or not 1 <= len(value) <= 80
                for value in values
            )
        ):
            raise ValueError("Cloud 资料提案的标签无效。")
        clean[key] = None if values is None else tuple(values)
    return clean
