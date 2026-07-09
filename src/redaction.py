from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from src.database import PROJECT_ROOT


REDACTION_RULES_PATH = PROJECT_ROOT / "config" / "local_redaction_rules.json"


@dataclass
class RedactionRules:
    replacements: list[tuple[str, str]]
    warning: str = ""


def load_redaction_rules(path: Path = REDACTION_RULES_PATH) -> RedactionRules:
    if not path.exists():
        return RedactionRules(
            replacements=[],
            warning="没有本地替换表，因此没有执行替换。",
        )

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"本地替换表 JSON 格式错误：{error}") from error

    replacements: list[tuple[str, str]] = []
    if isinstance(payload, dict) and "replacements" in payload:
        items = payload["replacements"]
        if not isinstance(items, list):
            raise ValueError("本地替换表 replacements 必须是列表。")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("本地替换表 replacements 里的每项必须是对象。")
            source = item.get("from")
            target = item.get("to")
            _append_rule(replacements, source, target)
    elif isinstance(payload, dict):
        for source, target in payload.items():
            _append_rule(replacements, source, target)
    else:
        raise ValueError("本地替换表必须是对象，或包含 replacements 列表。")

    return RedactionRules(replacements=replacements)


def _append_rule(
    replacements: list[tuple[str, str]], source: object, target: object
) -> None:
    if not isinstance(source, str) or not isinstance(target, str):
        raise ValueError("本地替换表必须使用字符串形式的 真实值 -> 占位符。")
    if source:
        replacements.append((source, target))


def apply_redactions(text: str, replacements: list[tuple[str, str]]) -> tuple[str, int]:
    redacted = text
    count = 0
    for source, target in replacements:
        occurrences = redacted.count(source)
        if occurrences:
            redacted = redacted.replace(source, target)
            count += occurrences
    return redacted, count
