"""Build the complete Markdown that a note content operation would save.

This module is read-only. Write paths can call the same function before their
transaction, then check the note version again inside that transaction.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
import re


_RAW_HTML_START = re.compile(
    r"(?<!\\)<(?:/?[A-Za-z][A-Za-z0-9-]*(?=[\s/>])|!--|![A-Za-z])"
)
_INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`).*?\1(?!`)", re.DOTALL)
_FENCE_START = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def _raw_html_starts_outside_code(markdown: str) -> Counter[str]:
    """Find HTML starts while ignoring ordinary fenced and inline code spans."""
    visible_lines: list[str] = []
    fence_character = ""
    fence_length = 0
    for line in markdown.splitlines(keepends=True):
        fence = _FENCE_START.match(line)
        if fence_character:
            if (
                fence is not None
                and fence.group(1)[0] == fence_character
                and len(fence.group(1)) >= fence_length
                and not line[fence.end():].strip()
            ):
                fence_character = ""
                fence_length = 0
            visible_lines.append("\n")
        elif fence is not None:
            fence_character = fence.group(1)[0]
            fence_length = len(fence.group(1))
            visible_lines.append("\n")
        else:
            visible_lines.append(line)
    visible = _INLINE_CODE.sub("", "".join(visible_lines))
    return Counter(_RAW_HTML_START.findall(visible))


@dataclass(frozen=True, slots=True)
class CanonicalNoteCandidate:
    before_markdown: str
    final_markdown: str
    legacy_canonicalization_changed: bool
    operation_type: str


def apply_exact_markdown_edits(before_markdown: str, edits: object) -> str:
    """Apply nonoverlapping, unique literal matches against the original body."""
    if not isinstance(before_markdown, str) or not isinstance(edits, list) or not edits:
        raise ValueError("修改提案必须包含至少一组有效的 Markdown 替换。")
    matches: list[tuple[int, int, str]] = []
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"old_markdown", "new_markdown"}:
            raise ValueError("每组修改必须只包含 old_markdown 和 new_markdown。")
        old, new = edit["old_markdown"], edit["new_markdown"]
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise ValueError("old_markdown 必须非空，new_markdown 必须为字符串。")
        if old == before_markdown:
            raise ValueError("不支持整篇正文替换；请选择较小的精确片段。")
        start = before_markdown.find(old)
        if start < 0 or before_markdown.find(old, start + 1) >= 0:
            raise ValueError("old_markdown 必须在当前 Markdown 中精确且唯一命中。")
        matches.append((start, start + len(old), new))
    matches.sort(key=lambda item: item[0])
    if any(left[1] > right[0] for left, right in zip(matches, matches[1:])):
        raise ValueError("多组修改的原文片段不能重叠。")
    position = 0
    pieces: list[str] = []
    for start, end, new in matches:
        pieces.extend((before_markdown[position:start], new))
        position = end
    pieces.append(before_markdown[position:])
    result = "".join(pieces)
    if _raw_html_starts_outside_code(result) - _raw_html_starts_outside_code(before_markdown):
        raise ValueError("new_markdown 含有不支持的原始 HTML。")
    if not result.strip():
        raise ValueError("修改后正文不能为空。")
    return result


def build_canonical_edit_candidate(
    before_markdown: str,
    edits: object,
    *,
    canonicalize: Callable[[str], str] | None = None,
) -> CanonicalNoteCandidate:
    raw_candidate = apply_exact_markdown_edits(before_markdown, edits)
    if canonicalize is None:
        from src.snotes.ui.markdown_canonicalizer import MarkdownCanonicalizer

        with MarkdownCanonicalizer() as milkdown:
            return build_canonical_edit_candidate(
                before_markdown, edits, canonicalize=milkdown.canonicalize_markdown
            )
    canonical_before = canonicalize(before_markdown)
    final_markdown = canonicalize(raw_candidate)
    if not final_markdown.strip():
        raise ValueError("修改后正文不能为空。")
    return CanonicalNoteCandidate(
        before_markdown=before_markdown,
        final_markdown=final_markdown,
        legacy_canonicalization_changed=canonical_before != before_markdown,
        operation_type="edit",
    )


def canonicalize_new_note(markdown: str) -> str:
    """Canonicalize a new AI body before any formal note is inserted."""
    if not isinstance(markdown, str):
        raise TypeError("Markdown body must be a string")
    from src.snotes.ui.markdown_canonicalizer import MarkdownCanonicalizer

    with MarkdownCanonicalizer() as milkdown:
        return milkdown.canonicalize_markdown(markdown)


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
