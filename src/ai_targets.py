from __future__ import annotations

from dataclasses import dataclass


AI_VISIBILITY_HIDDEN = "hidden"
AI_VISIBILITY_FULL = "full"
AI_VISIBILITY_REDACTED = "redacted"

# Compatibility names used by older modules.  Their values are now stable codes.
AI_VISIBILITY_NO_EXPORT = AI_VISIBILITY_HIDDEN
AI_VISIBILITY_DIRECT = AI_VISIBILITY_FULL

AI_VISIBILITY_LABELS = {
    AI_VISIBILITY_HIDDEN: "AI不可见",
    AI_VISIBILITY_FULL: "AI完整可见",
    AI_VISIBILITY_REDACTED: "AI脱敏后可见",
}
AI_VISIBILITY_OPTIONS = tuple(AI_VISIBILITY_LABELS.values())
LEGACY_AI_VISIBILITY_CODES = {
    "不导出给 AI": AI_VISIBILITY_HIDDEN,
    "可直接给 AI": AI_VISIBILITY_FULL,
    "脱敏后给 AI": AI_VISIBILITY_REDACTED,
    **{label: code for code, label in AI_VISIBILITY_LABELS.items()},
}


@dataclass(frozen=True)
class AiTargetDefinition:
    target_id: str
    display_name: str
    slug: str
    system: bool = False


AI_TARGET_DEFINITIONS = (
    AiTargetDefinition("common", "通用", "general", True),
    AiTargetDefinition("gale", "Gale", "gale"),
    AiTargetDefinition("clawbot", "Clawbot", "clawbot"),
    AiTargetDefinition("antigravity", "Antigravity", "antigravity"),
    AiTargetDefinition("codex", "Codex", "codex"),
    AiTargetDefinition("ke-den", "ke-den", "ke_den"),
    AiTargetDefinition("gemini", "Gemini", "gemini"),
)
AI_TARGET_IDS = tuple(item.target_id for item in AI_TARGET_DEFINITIONS)
AI_TARGETS = tuple(item.display_name for item in AI_TARGET_DEFINITIONS)
AI_TARGET_COMMON_ID = "common"
AI_TARGET_COMMON = "通用"
AI_TARGET_DISPLAY_BY_ID = {
    item.target_id: item.display_name for item in AI_TARGET_DEFINITIONS
}
AI_TARGET_ID_BY_DISPLAY = {
    item.display_name: item.target_id for item in AI_TARGET_DEFINITIONS
}
AI_TARGET_SLUGS = {item.target_id: item.slug for item in AI_TARGET_DEFINITIONS}
LEGACY_AI_TARGET_RENAMES = {
    "G经理": "Gale",
    "微信 clawde": "Clawbot",
    "ke-den Claude": "ke-den",
}
LEGACY_AI_TARGET_IDS = {
    old_name: AI_TARGET_ID_BY_DISPLAY[new_name]
    for old_name, new_name in LEGACY_AI_TARGET_RENAMES.items()
}


def normalize_ai_visibility(value: object) -> str:
    text = str(value or "").strip()
    if text in AI_VISIBILITY_LABELS:
        return text
    return LEGACY_AI_VISIBILITY_CODES.get(text, AI_VISIBILITY_HIDDEN)


def validate_ai_visibility(value: object) -> str:
    """Return a stable code; unknown values fail closed to hidden."""
    return normalize_ai_visibility(value)


def visibility_display(value: object) -> str:
    return AI_VISIBILITY_LABELS[normalize_ai_visibility(value)]


def resolve_target_id(value: object) -> str | None:
    text = str(value or "").strip()
    if text in AI_TARGET_DISPLAY_BY_ID:
        return text
    if text in AI_TARGET_ID_BY_DISPLAY:
        return AI_TARGET_ID_BY_DISPLAY[text]
    return LEGACY_AI_TARGET_IDS.get(text)


def normalize_target_ids(values: list[str] | tuple[str, ...]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        target_id = resolve_target_id(value)
        if target_id is None:
            raise ValueError(f"未知 AI 适用对象：{value}")
        if target_id not in seen:
            normalized.append(target_id)
            seen.add(target_id)
    return normalized


def normalize_ai_targets(values: list[str] | tuple[str, ...]) -> list[str]:
    """Compatibility helper for UI/receipt display names."""
    return [AI_TARGET_DISPLAY_BY_ID[target_id] for target_id in normalize_target_ids(values)]


def target_display_name(value: object) -> str:
    target_id = resolve_target_id(value)
    if target_id is None:
        raise ValueError(f"未知 AI 适用对象：{value}")
    return AI_TARGET_DISPLAY_BY_ID[target_id]


def target_slug(value: object) -> str:
    target_id = resolve_target_id(value)
    if target_id is None:
        raise ValueError(f"未知 AI 适用对象：{value}")
    return AI_TARGET_SLUGS[target_id]
