from __future__ import annotations


AI_VISIBILITY_NO_EXPORT = "不导出给 AI"
AI_VISIBILITY_DIRECT = "可直接给 AI"
AI_VISIBILITY_REDACTED = "脱敏后给 AI"

AI_VISIBILITY_OPTIONS = (
    AI_VISIBILITY_NO_EXPORT,
    AI_VISIBILITY_DIRECT,
    AI_VISIBILITY_REDACTED,
)

AI_TARGET_COMMON = "通用"
AI_TARGETS = (
    AI_TARGET_COMMON,
    "Gale",
    "Clawbot",
    "Codex",
    "ke-den",
    "Gemini",
)

AI_TARGET_SLUGS = {
    AI_TARGET_COMMON: "general",
    "Gale": "gale",
    "Clawbot": "clawbot",
    "Codex": "codex",
    "ke-den": "ke_den",
    "Gemini": "gemini",
}

LEGACY_AI_TARGET_RENAMES = {
    "G经理": "Gale",
    "微信 clawde": "Clawbot",
    "ke-den Claude": "ke-den",
}


def validate_ai_visibility(value: str) -> str:
    if value not in AI_VISIBILITY_OPTIONS:
        raise ValueError(f"未知 AI 可见性：{value}")
    return value


def normalize_ai_targets(values: list[str] | tuple[str, ...]) -> list[str]:
    normalized: list[str] = []
    seen = set()
    for value in values:
        value = LEGACY_AI_TARGET_RENAMES.get(value, value)
        if value not in AI_TARGETS:
            raise ValueError(f"未知 AI 适用对象：{value}")
        if value not in seen:
            normalized.append(value)
            seen.add(value)
    return normalized


def target_slug(value: str) -> str:
    if value not in AI_TARGET_SLUGS:
        raise ValueError(f"未知 AI 适用对象：{value}")
    return AI_TARGET_SLUGS[value]
