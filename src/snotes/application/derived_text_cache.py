"""Process-local derived text, bounded to the latest revision of each note."""
from src.snotes.domain.markdown import markdown_to_plain_text
from src.snotes.domain.models import Note


class DerivedTextCache:
    def __init__(self):
        self._values: dict[tuple[int, int], str] = {}
        self._revisions: dict[int, int] = {}
        self.parse_count = 0

    def get(self, note: Note) -> str:
        key = (note.id, note.revision)
        if key not in self._values:
            previous = self._revisions.get(note.id)
            if previous is not None:
                self._values.pop((note.id, previous), None)
            self._values[key] = markdown_to_plain_text(note.content_markdown)
            self._revisions[note.id] = note.revision
            self.parse_count += 1
        return self._values[key]
