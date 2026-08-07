from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from src.app_paths import REDACTION_RULES_PATH


@dataclass
class RedactionRules:
    replacements: list[tuple[str, str]]
    warning: str = ""


class RedactionUnavailableError(ValueError):
    """Raised when redacted content cannot be materialized safely."""


def load_redaction_rules(path: Path = REDACTION_RULES_PATH) -> RedactionRules:
    if not path.exists():
        raise RedactionUnavailableError(
            "本地脱敏规则不可用：规则文件不存在。"
        )

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RedactionUnavailableError(
            "本地脱敏规则不可用：规则文件无法解析。"
        ) from error

    replacements: list[tuple[str, str]] = []
    if isinstance(payload, dict) and "replacements" in payload:
        items = payload["replacements"]
        if not isinstance(items, list):
            raise RedactionUnavailableError(
                "本地脱敏规则不可用：replacements 必须是列表。"
            )
        for item in items:
            if not isinstance(item, dict):
                raise RedactionUnavailableError(
                    "本地脱敏规则不可用：replacement 必须是对象。"
                )
            source = item.get("from")
            target = item.get("to")
            _append_rule(replacements, source, target)
    elif isinstance(payload, dict):
        for source, target in payload.items():
            _append_rule(replacements, source, target)
    else:
        raise RedactionUnavailableError(
            "本地脱敏规则不可用：规则文件必须是对象。"
        )

    if not replacements:
        raise RedactionUnavailableError(
            "本地脱敏规则不可用：没有有效 replacement。"
        )

    return RedactionRules(replacements=replacements)


def _append_rule(
    replacements: list[tuple[str, str]], source: object, target: object
) -> None:
    if not isinstance(source, str) or not isinstance(target, str):
        raise RedactionUnavailableError(
            "本地脱敏规则不可用：replacement 必须使用字符串。"
        )
    if source:
        replacements.append((source, target))


def apply_redactions(text: str, replacements: list[tuple[str, str]]) -> tuple[str, int]:
    redacted = text
    count = 0
    for source, target in replacements:
        if source:
            redacted, occurrences = re.subn(
                re.escape(source),
                lambda _match: target,
                redacted,
                flags=re.IGNORECASE,
            )
            count += occurrences
    return redacted, count
