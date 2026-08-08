"""Canonical frozen-R1 ordinary search contract."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Iterable


_ALWAYS_SEPARATORS = frozenset(
    ",，、。:：;；!?！？/\\|｜→←↔⇒⇐⇔"
    "()（）[]【】{}｛｝<>《》〈〉「」『』"
    "\"'“”‘’`*＊=＝~～·•●○▪▫…"
)


@dataclass(frozen=True)
class SearchQuery:
    source: str
    normalized: str
    tokens: tuple[str, ...]

    @property
    def canonical_phrase(self) -> str:
        return " ".join(self.tokens)

    @property
    def is_blank(self) -> bool:
        return not self.normalized.strip()


@dataclass(frozen=True)
class SearchMatch:
    score: int
    matched_in: str
    reason: str


def normalize_search_text(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold()


def parse_search_query(value: object) -> SearchQuery:
    if isinstance(value, SearchQuery):
        return value
    source = str(value or "").strip()
    normalized = normalize_search_text(source)
    tokens: list[str] = []
    current: list[str] = []

    def finish_token() -> None:
        if not current:
            return
        token = "".join(current)
        current.clear()
        if token and token not in tokens:
            tokens.append(token)

    for index, character in enumerate(normalized):
        previous = normalized[index - 1] if index else ""
        following = normalized[index + 1] if index + 1 < len(normalized) else ""
        if _is_token_character(character, previous, following, bool(current)):
            current.append(character)
        else:
            finish_token()
    finish_token()
    return SearchQuery(source=source, normalized=normalized, tokens=tuple(tokens))


def canonicalize_search_text(value: object) -> str:
    return parse_search_query(value).canonical_phrase


def match_search_fields(
    query: SearchQuery | object,
    *,
    title: object = "",
    body: object = "",
    directory: object = "",
    tags: Iterable[object] | object = (),
) -> SearchMatch | None:
    parsed = query if isinstance(query, SearchQuery) else parse_search_query(query)
    if not parsed.tokens:
        return None
    title_text = normalize_search_text(title)
    body_text = normalize_search_text(body)
    directory_text = normalize_search_text(directory)
    if isinstance(tags, str):
        tag_values: Iterable[object] = (tags,)
    else:
        try:
            tag_values = tuple(tags)  # type: ignore[arg-type]
        except TypeError:
            tag_values = (tags,)
    tags_text = "\n".join(normalize_search_text(tag) for tag in tag_values)
    searchable_text = "\n".join((title_text, tags_text, directory_text, body_text))
    if not _contains_all(searchable_text, parsed.tokens):
        return None
    if canonicalize_search_text(title) == parsed.canonical_phrase:
        return SearchMatch(500, "title", "exact_title")
    if _contains_all(title_text, parsed.tokens):
        return SearchMatch(400, "title", "all_keywords_in_title")
    if _contains_all(f"{title_text}\n{tags_text}", parsed.tokens):
        matched_in = (
            "tags"
            if not any(token in title_text for token in parsed.tokens)
            else "title_tags"
        )
        return SearchMatch(350, matched_in, "all_keywords_in_title_or_tags")
    if _contains_all(directory_text, parsed.tokens):
        return SearchMatch(250, "directory", "all_keywords_in_directory")
    if _contains_all(body_text, parsed.tokens):
        return SearchMatch(150, "body", "all_keywords_in_body")
    return SearchMatch(200, "multiple", "keywords_across_safe_fields")


def _contains_all(value: str, tokens: tuple[str, ...]) -> bool:
    return all(token in value for token in tokens)


def _is_token_character(
    character: str,
    previous: str,
    following: str,
    token_started: bool,
) -> bool:
    if character.isspace() or character in _ALWAYS_SEPARATORS:
        return False
    if character in {".", "-", "_"}:
        return previous.isalnum() and following.isalnum()
    if character == "+":
        return token_started and (previous.isalnum() or previous == "+")
    if character == "#":
        return token_started and previous.isalnum()
    category = unicodedata.category(character)
    return not category.startswith(("P", "S", "Z", "C"))
