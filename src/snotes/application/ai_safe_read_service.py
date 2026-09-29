"""Single application owner for AI-authorized, safely materialized reads."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from src.snotes.application.ai_actor_registry import (
    AI_ACTOR_IDS,
    AI_TARGET_COMMON_ID,
    AI_TARGET_DEFINITIONS,
    AI_TARGET_IDS,
    AiTargetDefinition,
    normalize_target_ids as normalize_registry_target_ids,
    resolve_target_id,
)


AI_VISIBILITY_HIDDEN = "hidden"
AI_VISIBILITY_FULL = "full"
AI_VISIBILITY_REDACTED = "redacted"
AI_VISIBILITY_VALUES = (
    AI_VISIBILITY_HIDDEN,
    AI_VISIBILITY_FULL,
    AI_VISIBILITY_REDACTED,
)

AI_VISIBILITY_LABELS = {
    AI_VISIBILITY_HIDDEN: "AI不可见",
    AI_VISIBILITY_FULL: "AI完整可见",
    AI_VISIBILITY_REDACTED: "AI脱敏后可见",
}
_LEGACY_AI_VISIBILITY_CODES = {
    "不导出给 AI": AI_VISIBILITY_HIDDEN,
    "可直接给 AI": AI_VISIBILITY_FULL,
    "脱敏后给 AI": AI_VISIBILITY_REDACTED,
    **{label: code for code, label in AI_VISIBILITY_LABELS.items()},
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

    @property
    def was_transformed(self) -> bool:
        return self.redacted

    @property
    def transform_count(self) -> int:
        return self.redaction_count


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
        return self._read_materialized(actor_id, source, allow_common=False)

    def read_markdown_note(
        self, actor: object, source: AiSafeReadSource,
    ) -> AiSafeReadResult | None:
        """Authorize, render canonical Markdown once, then apply existing redaction."""
        from src.snotes.domain.markdown import markdown_to_plain_text

        return self._read_materialized(
            _validated_actor(actor), source, allow_common=False,
            body_renderer=markdown_to_plain_text,
        )

    def read_for_export_target(
        self,
        target: object,
        source: AiSafeReadSource,
    ) -> AiSafeReadResult | None:
        """Materialize for a selectable package target, including common-only."""
        target_id = resolve_target_id(target)
        if target_id not in AI_TARGET_IDS:
            target_id = None
        return self._read_materialized(target_id, source, allow_common=True)

    def _read_materialized(
        self,
        actor_id: str | None,
        source: AiSafeReadSource,
        *,
        allow_common: bool,
        body_renderer: BodyRenderer | None = None,
    ) -> AiSafeReadResult | None:
        visibility = normalize_ai_visibility(source.visibility)
        authorized = (
            _common_target_authorized(source.targets)
            if allow_common and actor_id == AI_TARGET_COMMON_ID
            else actor_id is not None
            and is_actor_authorized(actor_id, source.targets)
        )
        if (
            actor_id is None
            or visibility == AI_VISIBILITY_HIDDEN
            or not authorized
        ):
            return None

        replacements: Sequence[tuple[str, str]] = ()
        if visibility == AI_VISIBILITY_REDACTED:
            replacements = load_redaction_rules(
                self._redaction_rules_path
            ).replacements

        rendered_content = (body_renderer or self._body_renderer)(source.content)
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
    if text in AI_VISIBILITY_LABELS:
        return text
    return _LEGACY_AI_VISIBILITY_CODES.get(text, AI_VISIBILITY_HIDDEN)


def visibility_display(value: object) -> str:
    return AI_VISIBILITY_LABELS[normalize_ai_visibility(value)]


def normalize_target_ids(values: Sequence[str]) -> list[str]:
    """Compatibility export of the shared stable-identity normalizer."""
    return normalize_registry_target_ids(values)


def is_actor_authorized(actor_id: str, targets: Sequence[str]) -> bool:
    """Common grants every real actor; otherwise the actor must be targeted."""
    if actor_id not in AI_ACTOR_IDS:
        return False
    resolved_targets = {
        target_id
        for value in targets
        if (target_id := resolve_target_id(value)) is not None
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


def _validated_actor(value: object) -> str | None:
    actor_id = resolve_target_id(value)
    if actor_id in AI_ACTOR_IDS:
        return actor_id
    return None


def _common_target_authorized(targets: Sequence[str]) -> bool:
    return any(resolve_target_id(value) == AI_TARGET_COMMON_ID for value in targets)
