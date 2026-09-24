"""Validate the untrusted stored payload for a pending metadata change."""

from __future__ import annotations

import json


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
