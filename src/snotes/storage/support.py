"""Shared frozen-R1 normalization and timestamp behavior for storage."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
TAG_SPLIT_RE = re.compile(r"[,\s，、]+")
Clock = Callable[[], datetime]


def now_shanghai() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def timestamp_seconds(clock: Clock = now_shanghai) -> str:
    return clock().isoformat(timespec="seconds")


def next_note_updated_at(previous: str, clock: Clock = now_shanghai) -> str:
    timestamp = clock()
    try:
        prior = datetime.fromisoformat(previous)
        if prior.tzinfo is None:
            prior = prior.replace(tzinfo=timestamp.tzinfo)
        if timestamp <= prior:
            try:
                timestamp = prior + timedelta(microseconds=1)
            except OverflowError as error:
                raise RuntimeError(
                    "笔记更新时间已达到可表示上限，无法继续保存"
                ) from error
    except (TypeError, ValueError):
        pass
    return timestamp.isoformat(timespec="microseconds")


def normalize_tag(tag: str) -> str:
    return tag.strip().lstrip("#").lower()


def normalize_tags(tags: Sequence[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_tag in tags:
        tag = normalize_tag(raw_tag)
        if tag and tag not in seen:
            normalized.append(tag)
            seen.add(tag)
    return normalized
