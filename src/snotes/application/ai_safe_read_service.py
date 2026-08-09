"""Single application owner for AI-authorized, safely materialized reads."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path


AI_VISIBILITY_HIDDEN = "hidden"
AI_VISIBILITY_FULL = "full"
AI_VISIBILITY_REDACTED = "redacted"

AI_TARGET_COMMON_ID = "common"


@dataclass(frozen=True, slots=True)
class AiTargetDefinition:
    target_id: str
    display_name: str
    slug: str
    system: bool = False


AI_TARGET_DEFINITIONS = (
    AiTargetDefinition("common", "通用", "general", True),
    AiTargetDefinition("gale", "Gale", "gale"),
    AiTargetDefinition("clawbot", "Clawbot", "clawbot"),
    AiTargetDefinition("codex", "Codex", "codex"),
    AiTargetDefinition("ke-den", "ke-den", "ke_den"),
    AiTargetDefinition("gemini", "Gemini", "gemini"),
)
AI_TARGET_IDS = tuple(item.target_id for item in AI_TARGET_DEFINITIONS)
AI_ACTOR_IDS = tuple(
    target_id for target_id in AI_TARGET_IDS if target_id != AI_TARGET_COMMON_ID
)

_AI_TARGET_DISPLAY_BY_ID = {
    item.target_id: item.display_name for item in AI_TARGET_DEFINITIONS
}
_AI_TARGET_ID_BY_DISPLAY = {
    item.display_name: item.target_id for item in AI_TARGET_DEFINITIONS
}
_LEGACY_AI_TARGET_IDS = {
    "G经理": "gale",
    "微信 clawde": "clawbot",
    "ke-den Claude": "ke-den",
}
_AI_VISIBILITY_LABELS = {
    AI_VISIBILITY_HIDDEN: "AI不可见",
    AI_VISIBILITY_FULL: "AI完整可见",
    AI_VISIBILITY_REDACTED: "AI脱敏后可见",
}
_LEGACY_AI_VISIBILITY_CODES = {
    "不导出给 AI": AI_VISIBILITY_HIDDEN,
    "可直接给 AI": AI_VISIBILITY_FULL,
    "脱敏后给 AI": AI_VISIBILITY_REDACTED,
    **{label: code for code, label in _AI_VISIBILITY_LABELS.items()},
}


class RedactionUnavailableError(ValueError):
    """Raised when redacted content cannot be materialized safely."""


@dataclass(frozen=True, slots=True)
class AiSafeReadSource:
    """Caller-provided note data; materialization never mutates this object."""

    note_id: int
    title: str
    content: str
    visibility: str
    targets: tuple[str, ...]
    directory: str = ""
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AiSafeReadResult:
    note_id: int
    title: str
    content: str
    directory: str
    tags: tuple[str, ...]
    visibility: str
    redacted: bool
    redaction_count: int


@dataclass(frozen=True, slots=True)
class RedactionRules:
    replacements: tuple[tuple[str, str], ...]


BodyRenderer = Callable[[str], str]


class AiSafeReadService:
    """Authorize first, then derive an AI-safe result from a source note."""

    def __init__(
        self,
        redaction_rules_path: Path,
        *,
        body_renderer: BodyRenderer | None = None,
    ) -> None:
        self._redaction_rules_path = Path(redaction_rules_path)
        self._body_renderer = body_renderer or (lambda value: value)

    def read_note(
        self,
        actor: object,
        source: AiSafeReadSource,
    ) -> AiSafeReadResult | None:
        """Return a derived safe record, or ``None`` when access is denied."""
        actor_id = _validated_actor(actor)
        visibility = normalize_ai_visibility(source.visibility)
        if (
            actor_id is None
            or visibility == AI_VISIBILITY_HIDDEN
            or not is_actor_authorized(actor_id, source.targets)
        ):
            return None

        replacements: Sequence[tuple[str, str]] = ()
        if visibility == AI_VISIBILITY_REDACTED:
            replacements = load_redaction_rules(
                self._redaction_rules_path
            ).replacements

        rendered_content = self._body_renderer(source.content)
        title = source.title
        directory = source.directory
        tags = tuple(source.tags)
        redaction_count = 0

        if visibility == AI_VISIBILITY_REDACTED:
            title, count = apply_redactions(title, replacements)
            redaction_count += count
            rendered_content, count = apply_redactions(
                rendered_content, replacements
            )
            redaction_count += count
            directory, count = apply_redactions(directory, replacements)
            redaction_count += count
            safe_tags: list[str] = []
            for tag in tags:
                safe_tag, count = apply_redactions(tag, replacements)
                safe_tags.append(safe_tag)
                redaction_count += count
            tags = tuple(safe_tags)

        return AiSafeReadResult(
            note_id=source.note_id,
            title=title,
            content=rendered_content,
            directory=directory,
            tags=tags,
            visibility=visibility,
            redacted=visibility == AI_VISIBILITY_REDACTED,
            redaction_count=redaction_count,
        )


def normalize_ai_visibility(value: object) -> str:
    """Preserve R1 visibility aliases; unknown values fail closed to hidden."""
    text = str(value or "").strip()
    if text in _AI_VISIBILITY_LABELS:
        return text
    return _LEGACY_AI_VISIBILITY_CODES.get(text, AI_VISIBILITY_HIDDEN)


def is_actor_authorized(actor_id: str, targets: Sequence[str]) -> bool:
    """Common grants every real actor; otherwise the actor must be targeted."""
    if actor_id not in AI_ACTOR_IDS:
        return False
    resolved_targets = {
        target_id
        for value in targets
        if (target_id := _resolve_target_id(value)) is not None
    }
    return AI_TARGET_COMMON_ID in resolved_targets or actor_id in resolved_targets


def load_redaction_rules(path: Path) -> RedactionRules:
    """Load the frozen R1 rule formats and fail closed on every unsafe state."""
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
            _append_rule(replacements, item.get("from"), item.get("to"))
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
    return RedactionRules(tuple(replacements))


def apply_redactions(
    text: str,
    replacements: Sequence[tuple[str, str]],
) -> tuple[str, int]:
    """Apply the sole public R1 redaction behavior, case-insensitively."""
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


def _append_rule(
    replacements: list[tuple[str, str]],
    source: object,
    target: object,
) -> None:
    if not isinstance(source, str) or not isinstance(target, str):
        raise RedactionUnavailableError(
            "本地脱敏规则不可用：replacement 必须使用字符串。"
        )
    if source:
        replacements.append((source, target))


def _resolve_target_id(value: object) -> str | None:
    text = str(value or "").strip()
    if text in _AI_TARGET_DISPLAY_BY_ID:
        return text
    if text in _AI_TARGET_ID_BY_DISPLAY:
        return _AI_TARGET_ID_BY_DISPLAY[text]
    return _LEGACY_AI_TARGET_IDS.get(text)


def _validated_actor(value: object) -> str | None:
    actor_id = _resolve_target_id(value)
    if actor_id in AI_ACTOR_IDS:
        return actor_id
    return None
