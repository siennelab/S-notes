"""Application contract for the actor-fixed GreenCloud read MCP."""

from __future__ import annotations

from typing import Protocol, Sequence

from src.snotes.domain.search_query import parse_search_query


DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 100
MAX_QUERY_LENGTH = 500
MAX_NOTE_ID = 9_223_372_036_854_775_807


class CloudMirrorReader(Protocol):
    def search(self, tokens: Sequence[str], *, limit: int) -> list[dict]: ...

    def get(self, note_id: int) -> dict | None: ...

    def get_markdown(self, note_id: int) -> dict | None: ...


class CloudReadService:
    """Expose only safe data already materialized into one actor mirror."""

    def __init__(self, reader: CloudMirrorReader) -> None:
        self._reader = reader

    def search_notes(self, query: str, *, limit: int = DEFAULT_SEARCH_LIMIT) -> dict:
        query_text = str(query or "")
        if not query_text.strip() or len(query_text) > MAX_QUERY_LENGTH:
            raise ValueError("invalid_query")
        if not 1 <= int(limit) <= MAX_SEARCH_LIMIT:
            raise ValueError("invalid_limit")
        parsed = parse_search_query(query_text)
        if not parsed.tokens:
            return {
                "status": "ok",
                "query": query_text,
                "results": [],
                "result_count": 0,
            }
        results = self._reader.search(parsed.tokens, limit=int(limit))
        return {
            "status": "ok",
            "query": query_text,
            "results": results,
            "result_count": len(results),
        }

    def get_note(self, note_id: int) -> dict:
        normalized_id = self._note_id(note_id)
        note = self._reader.get(normalized_id)
        if note is None:
            return {"status": "not_found", "note_id": normalized_id}
        return {"status": "ok", **note}

    def get_note_markdown(self, note_id: int) -> dict:
        normalized_id = self._note_id(note_id)
        note = self._reader.get_markdown(normalized_id)
        if note is None:
            return {"status": "not_found", "note_id": normalized_id}
        return {"status": "ok", **note}

    @staticmethod
    def _note_id(value: int) -> int:
        note_id = int(value)
        if not 1 <= note_id <= MAX_NOTE_ID:
            raise ValueError("invalid_note_id")
        return note_id
