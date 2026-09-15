from __future__ import annotations

import html
import re
import unicodedata
from collections import Counter

from src.snotes.ui.html_sanitizer import sanitize_pasted_text


MARKDOWN_SIGNAL_RE = re.compile(
    r"(?m)^(#{1,3}\s+|[-*]\s+|\d+\.\s+|>\s+|```)|(\*\*[^*\n]+\*\*)|(`[^`\n]+`)"
)
PASTE_LIST_PREFIX_RE = re.compile(
    r"^[ \t]*(?:[-+*•◦▪‣][ \t]+|\d+[.)、．。][ \t]*)"
)


def looks_like_markdown(text: str) -> bool:
    return bool(MARKDOWN_SIGNAL_RE.search(text or ""))


def _comparison_text(text: str) -> str:
    normalized = sanitize_pasted_text(text)
    normalized = normalized.replace("\u2028", "\n").replace("\u2029", "\n")
    normalized = normalized.replace("\u00a0", " ")
    normalized = normalized.replace("\u2007", " ").replace("\u202f", " ")
    normalized = unicodedata.normalize("NFC", normalized)
    lines = [line.rstrip() for line in normalized.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _semantic_lines(
    text: str, *, strip_list_prefixes: bool = False
) -> list[str]:
    lines: list[str] = []
    for line in _comparison_text(text).split("\n"):
        clean_line = line.strip()
        if not clean_line:
            continue
        lines.append(
            PASTE_LIST_PREFIX_RE.sub("", clean_line)
            if strip_list_prefixes
            else clean_line
        )
    return lines


def _semantic_characters(
    text: str, *, strip_list_prefixes: bool = False
) -> str:
    return "".join(
        re.sub(r"\s+", "", line)
        for line in _semantic_lines(
            text, strip_list_prefixes=strip_list_prefixes
        )
    )


def _is_line_subsequence(needle: list[str], haystack: list[str]) -> bool:
    if not needle:
        return True
    position = 0
    for line in haystack:
        if line == needle[position]:
            position += 1
            if position == len(needle):
                return True
    return False


def _visible_information_score(text: str) -> tuple[int, int]:
    semantic_lines = _semantic_lines(text)
    visible_characters = sum(
        len(re.sub(r"\s+", "", line)) for line in semantic_lines
    )
    return len(semantic_lines), visible_characters


def select_paste_representation(
    normalized_html: str,
    html_text: str,
    plain_text: str,
) -> tuple[str, str]:
    if not html_text and plain_text:
        return "plain", plain_text
    if html_text and not plain_text:
        return "html", normalized_html
    if not html_text and not plain_text:
        return "plain", ""

    html_comparison = _comparison_text(html_text)
    plain_comparison = _comparison_text(plain_text)
    if html_comparison == plain_comparison:
        return "html", normalized_html

    html_has_list = bool(re.search(r"<(?:ol|ul)\b", normalized_html, re.I))
    html_semantic_lines = _semantic_lines(html_text)
    plain_semantic_lines = _semantic_lines(
        plain_text, strip_list_prefixes=html_has_list
    )
    if html_semantic_lines == plain_semantic_lines:
        return "html", normalized_html
    if _is_line_subsequence(plain_semantic_lines, html_semantic_lines):
        return "html", normalized_html
    if _is_line_subsequence(html_semantic_lines, plain_semantic_lines):
        return "plain", plain_text
    if Counter(html_semantic_lines) == Counter(plain_semantic_lines):
        return "plain", plain_text
    html_characters = _semantic_characters(html_text)
    plain_characters = _semantic_characters(
        plain_text, strip_list_prefixes=html_has_list
    )
    if html_characters == plain_characters:
        return "html", normalized_html
    if (
        plain_characters
        and plain_characters in html_characters
        and len(html_semantic_lines) == len(plain_semantic_lines)
    ):
        return "html", normalized_html
    if (
        html_characters
        and html_characters in plain_characters
        and len(html_semantic_lines) == len(plain_semantic_lines)
    ):
        return "plain", plain_text
    html_score = _visible_information_score(html_text)
    plain_score = _visible_information_score(plain_text)
    if html_score > plain_score:
        return "html", normalized_html
    return "plain", plain_text


def markdown_to_html(text: str) -> str:
    lines = sanitize_pasted_text(text).split("\n")
    output: list[str] = []
    paragraph: list[str] = []
    list_type: str | None = None
    in_code = False
    code_lines: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            output.append(
                "<p>"
                + "<br>".join(format_inline_markdown(line) for line in paragraph)
                + "</p>"
            )
            paragraph = []

    def close_list() -> None:
        nonlocal list_type
        if list_type:
            output.append(f"</{list_type}>")
            list_type = None

    for raw_line in lines:
        line = raw_line.rstrip()
        if line.strip().startswith("```"):
            flush_paragraph()
            close_list()
            if in_code:
                output.append(
                    "<pre><code>"
                    + html.escape("\n".join(code_lines))
                    + "</code></pre>"
                )
                code_lines = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue

        if not line.strip():
            flush_paragraph()
            close_list()
            continue

        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            flush_paragraph()
            close_list()
            level = len(heading.group(1))
            output.append(
                f"<h{level}>{format_inline_markdown(heading.group(2))}</h{level}>"
            )
            continue

        unordered = re.match(r"^\s*[-*]\s+(.+)$", line)
        ordered = re.match(r"^\s*\d+\.\s+(.+)$", line)
        if unordered or ordered:
            flush_paragraph()
            target_type = "ul" if unordered else "ol"
            if list_type != target_type:
                close_list()
                output.append(f"<{target_type}>")
                list_type = target_type
            content = (unordered or ordered).group(1)
            output.append(f"<li>{format_inline_markdown(content)}</li>")
            continue

        quote = re.match(r"^\s*>\s+(.+)$", line)
        if quote:
            flush_paragraph()
            close_list()
            output.append(
                f"<blockquote>{format_inline_markdown(quote.group(1))}</blockquote>"
            )
            continue

        close_list()
        paragraph.append(line)

    if in_code:
        output.append(
            "<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>"
        )
    flush_paragraph()
    close_list()
    return "".join(output)


def format_inline_markdown(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
