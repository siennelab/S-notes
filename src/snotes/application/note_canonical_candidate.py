"""Build the complete Markdown that a note content operation would save.

This module is read-only. Write paths can call the same function before their
transaction, then check the note version again inside that transaction.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CanonicalNoteCandidate:
    before_markdown: str
    final_markdown: str
    legacy_canonicalization_changed: bool
    operation_type: str


def build_canonical_note_candidate(
    before_markdown: str,
    addition_markdown: str,
    operation_type: str = "append",
    *,
    canonicalize: Callable[[str], str] | None = None,
) -> CanonicalNoteCandidate:
    """Return the Milkdown fixed point for a full append/prepend candidate.

    The legacy flag concerns the existing body alone. This allows a reviewer to
    distinguish changes to historical Markdown from the requested new block.
    No result is returned when either canonicalization fails.
    """
    if operation_type not in {"append", "prepend"}:
        raise ValueError("Unsupported note body operation")
    if not isinstance(before_markdown, str) or not isinstance(addition_markdown, str):
        raise TypeError("Markdown bodies must be strings")
    if not addition_markdown:
        raise ValueError("追加内容不能为空。")
    if canonicalize is None:
        # One offscreen Milkdown session handles the old body and full candidate.
        from src.snotes.ui.markdown_canonicalizer import MarkdownCanonicalizer

        with MarkdownCanonicalizer() as milkdown:
            return build_canonical_note_candidate(
                before_markdown,
                addition_markdown,
                operation_type,
                canonicalize=milkdown.canonicalize_markdown,
            )

    separator = "\n\n" if before_markdown else ""
    if operation_type == "append":
        candidate = before_markdown + separator + addition_markdown
    else:
        candidate = addition_markdown + separator + before_markdown

    canonical_before = canonicalize(before_markdown)
    final_markdown = canonicalize(candidate)
    return CanonicalNoteCandidate(
        before_markdown=before_markdown,
        final_markdown=final_markdown,
        legacy_canonicalization_changed=canonical_before != before_markdown,
        operation_type=operation_type,
    )
