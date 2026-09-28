"""Validate the stored payload for a pending note body change."""

from __future__ import annotations

import json

from src.snotes.application.note_canonical_candidate import validate_exact_markdown_edits


def parse_pending_body_payload(
    raw: str,
    operation_type: str,
) -> str | list[dict[str, str]]:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise ValueError("正文提案内容无效。") from error
    if operation_type == "edit":
        if not isinstance(payload, dict) or set(payload) != {"edits"}:
            raise ValueError("正文修改提案内容无效。")
        edits = payload["edits"]
        try:
            validate_exact_markdown_edits(edits)
        except ValueError as error:
            raise ValueError("正文修改提案内容无效。") from error
        return edits
    if operation_type in {"append", "prepend"}:
        if (
            not isinstance(payload, dict)
            or set(payload) != {"content"}
            or not isinstance(payload["content"], str)
            or not payload["content"]
        ):
            raise ValueError("正文提案内容无效。")
        return payload["content"]
    raise ValueError("该回执不是正文修改提案。")
