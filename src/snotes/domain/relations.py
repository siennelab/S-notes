"""Pure ordinary-note relation semantics from frozen R1."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

from src.snotes.domain.models import Note
from src.snotes.domain.tags import parse_tags


WIKI_LINK_RE = re.compile(r"\[\[\s*([^\[\]\n]+?)\s*\]\]")


class PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        clean_tag = tag.lower()
        if clean_tag in {"head", "style", "script"}:
            self.skip_depth += 1
        elif not self.skip_depth and clean_tag in {
            "br", "p", "div", "li", "tr", "h1", "h2", "h3"
        }:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        clean_tag = tag.lower()
        if clean_tag in {"head", "style", "script"} and self.skip_depth:
            self.skip_depth -= 1
        elif not self.skip_depth and clean_tag in {
            "p", "div", "li", "tr", "h1", "h2", "h3"
        }:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"\s+", " ", "".join(self.parts)).strip()


def html_to_visible_text(value: str) -> str:
    parser = PlainTextParser()
    parser.feed(value or "")
    parser.close()
    return parser.text()


def extract_manual_reference_titles(text: str) -> list[str]:
    titles: list[str] = []
    for match in WIKI_LINK_RE.finditer(text or ""):
        title = match.group(1).strip()
        if title and title not in titles:
            titles.append(title)
    return titles


@dataclass(frozen=True, slots=True)
class RelatedNote:
    note_id: int
    title: str
    path: str
    updated_at: str
    reasons: tuple[str, ...]


def build_related_notes(current_note_id: int | None, notes: list[Note]) -> list[RelatedNote]:
    if current_note_id is None:
        return []
    current = next((note for note in notes if note.id == current_note_id), None)
    if current is None:
        return []
    candidates = [note for note in notes if note.id != current_note_id]
    text_by_id = {note.id: html_to_visible_text(note.content_html) for note in notes}
    reasons_by_id: dict[int, list[str]] = {}

    def add(note: Note, reason: str) -> None:
        reasons = reasons_by_id.setdefault(note.id, [])
        if reason not in reasons:
            reasons.append(reason)

    title_lookup: dict[str, list[Note]] = {}
    for note in candidates:
        title_lookup.setdefault(note.title, []).append(note)
    manual_ids: set[int] = set()
    for title in extract_manual_reference_titles(text_by_id[current.id]):
        for note in title_lookup.get(title, []):
            add(note, "引用")
            manual_ids.add(note.id)
    if current.title:
        for note in candidates:
            if current.title in extract_manual_reference_titles(text_by_id[note.id]):
                add(note, "被引用")
    for note in candidates:
        if note.location == current.location:
            add(note, "同目录")
    current_tags = set(parse_tags(current.tags))
    if current_tags:
        for note in candidates:
            if current_tags.intersection(parse_tags(note.tags)):
                add(note, "同标签")
    current_title = current.title.strip()
    current_text = text_by_id[current.id]
    for note in candidates:
        other_title = note.title.strip()
        related = bool(
            (current_title and (current_title in note.title or current_title in text_by_id[note.id]))
            or (other_title and other_title in current_text)
            or (
                current_title
                and other_title
                and (current_title in other_title or other_title in current_title)
            )
        )
        if related and note.id not in manual_ids:
            add(note, "正文")
    result = [
        RelatedNote(
            note_id=note.id,
            title=note.title or "未命名笔记",
            path=" / ".join(part for part in note.location.values() if part != "无"),
            updated_at=note.updated_at,
            reasons=tuple(reasons_by_id[note.id]),
        )
        for note in candidates
        if note.id in reasons_by_id
    ]
    result.sort(key=lambda item: item.updated_at, reverse=True)
    result.sort(key=lambda item: len(item.reasons), reverse=True)
    result.sort(
        key=lambda item: any(reason in {"引用", "被引用"} for reason in item.reasons),
        reverse=True,
    )
    return result
