"""Frozen R1 ordinary-tag parsing rules."""

from __future__ import annotations

import re


TAG_SPLIT_RE = re.compile(r"[,\s，、]+")


def normalize_tag(tag: str) -> str:
    return tag.strip().lstrip("#").lower()


def parse_tags(value: str) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for raw_tag in TAG_SPLIT_RE.split(value or ""):
        tag = normalize_tag(raw_tag)
        if tag and tag not in seen:
            tags.append(tag)
            seen.add(tag)
    return tags


def format_tags(tags: list[str]) -> str:
    return ", ".join(tags)
