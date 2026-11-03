"""Application-owned read capabilities for the server-rendered Reader."""

from __future__ import annotations

from typing import Protocol, Sequence


class ReaderMirror(Protocol):
    """The read-only mirror operations required by the Reader product."""

    def search(self, tokens: Sequence[str], *, limit: int) -> list[dict]: ...

    def get(self, note_id: int, *, include_markdown: bool = False) -> dict | None: ...

    def metadata_catalog(self) -> dict: ...

    def list_recent(self, limit: int = 50) -> list[dict]: ...

    def list_by_location(
        self, main_category: str, sub_category: str, limit: int = 200,
    ) -> list[dict]: ...

    def snapshot_info(self) -> dict: ...


class ReaderService:
    """Own Reader browse queries over an already-authorized actor mirror."""

    def __init__(self, mirror: ReaderMirror) -> None:
        self._mirror = mirror

    def home(self) -> tuple[dict, list[dict], list[dict]]:
        catalog = self._mirror.metadata_catalog()
        return (
            self._mirror.snapshot_info(),
            catalog["directories"],
            self._mirror.list_recent(50),
        )

    def search(self, query: str) -> list[dict]:
        return self._mirror.search([query], limit=200)

    def directory(self, directory_id: int) -> tuple[dict, list[dict]] | None:
        item = next(
            (
                item
                for item in self._mirror.metadata_catalog()["directories"]
                if item["directory_id"] == directory_id
            ),
            None,
        )
        if item is None:
            return None
        return item, self._mirror.list_by_location(
            item["main_category"], item["sub_category"], 200
        )

    def note(self, note_id: int) -> dict | None:
        return self._mirror.get(note_id, include_markdown=True)
