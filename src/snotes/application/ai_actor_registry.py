"""Single truth source for stable AI identities and their current UI labels."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AiTargetDefinition:
    """A stable identity; display names and selectability may change over time."""

    target_id: str
    display_name: str
    slug: str
    selectable: bool = True
    system: bool = False
    aliases: tuple[str, ...] = ()


AI_TARGET_COMMON_ID = "common"
AI_ACTOR_GALE_ID = "gale"
AI_ACTOR_YAN_ID = "yan"

# Append new identities with a new target_id. Retired identities stay registered
# so persisted grants remain readable and can never be reassigned.
AI_TARGET_REGISTRY = (
    AiTargetDefinition("common", "通用", "general", system=True),
    AiTargetDefinition(AI_ACTOR_GALE_ID, "Gale", "gale", aliases=("G经理",)),
    AiTargetDefinition(AI_ACTOR_YAN_ID, "砚", "yan"),
    AiTargetDefinition(
        "clawbot",
        "Clawbot",
        "clawbot",
        aliases=("微信 clawde",),
    ),
    AiTargetDefinition("antigravity", "Antigravity", "antigravity"),
    AiTargetDefinition("claude-code", "Claude", "claude_code"),
    AiTargetDefinition(
        "ke-den",
        "Ke",
        "ke_den",
        aliases=("ke-den Claude",),
    ),
    AiTargetDefinition("codex", "Codex", "codex", selectable=False),
    AiTargetDefinition("gemini", "Gemini", "gemini", selectable=False),
)

AI_TARGET_DEFINITIONS = AI_TARGET_REGISTRY
AI_TARGET_IDS = tuple(item.target_id for item in AI_TARGET_REGISTRY)
AI_ACTOR_IDS = tuple(
    item.target_id
    for item in AI_TARGET_REGISTRY
    if item.target_id != AI_TARGET_COMMON_ID
)

_TARGET_BY_ID = {item.target_id: item for item in AI_TARGET_REGISTRY}
_TARGET_ID_BY_NAME = {
    item.display_name: item.target_id for item in AI_TARGET_REGISTRY
}
_TARGET_ID_BY_ALIAS = {
    alias: item.target_id
    for item in AI_TARGET_REGISTRY
    for alias in item.aliases
}


def selectable_target_definitions() -> tuple[AiTargetDefinition, ...]:
    return tuple(item for item in AI_TARGET_REGISTRY if item.selectable)


def target_definition(target_id: str) -> AiTargetDefinition | None:
    return _TARGET_BY_ID.get(target_id)


def resolve_target_id(value: object) -> str | None:
    text = str(value or "").strip()
    if text in _TARGET_BY_ID:
        return text
    if text in _TARGET_ID_BY_NAME:
        return _TARGET_ID_BY_NAME[text]
    return _TARGET_ID_BY_ALIAS.get(text)


def normalize_target_ids(values: Sequence[str]) -> list[str]:
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
