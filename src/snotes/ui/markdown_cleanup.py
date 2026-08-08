from __future__ import annotations

import re


MARKDOWN_FENCE_RE = re.compile(
    r"^[ \t]{0,3}(`{3,}|~{3,})(?:[ \t]*[A-Za-z0-9_+.-]+)?[ \t]*$"
)
MARKDOWN_HEADING_RE = re.compile(
    r"^[ \t]{0,3}(?:#{1,6}[ \t]+|#{2,6}(?=\S))"
)
MARKDOWN_QUOTE_RE = re.compile(r"^([ \t]{0,3})>[ \t]?")
MARKDOWN_TASK_RE = re.compile(r"^([ ]{0,3})[-+*][ \t]+\[[ xX]\][ \t]+")
MARKDOWN_UNORDERED_RE = re.compile(r"^([ ]{0,3})[-+*][ \t]+")
MARKDOWN_ORDERED_RE = re.compile(r"^([ ]{0,3})\d+[.)][ \t]+")
MARKDOWN_HORIZONTAL_RULE_RE = re.compile(
    r"^[ \t]{0,3}(?:-{3,}|\*{3,}|_{3,})[ \t]*$"
)
MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]\n]*)\]\(([^)\n]+)\)")
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]\n]+)\]\(([^)\n]+)\)")
MARKDOWN_AUTOLINK_RE = re.compile(r"<(https?://[^>\s]+)>")
MARKDOWN_INLINE_CODE_RE = re.compile(r"(?<![\\`])`([^`\n]+)`(?!`)")
MARKDOWN_STRONG_STAR_RE = re.compile(
    r"(?<![\\*])\*\*(?=\S)(.+?)(?<=\S)\*\*(?!\*)"
)
MARKDOWN_STRONG_UNDERSCORE_RE = re.compile(
    r"(?<![\\\w])__(?=\S)(.+?)(?<=\S)__(?!\w)"
)
MARKDOWN_STRIKE_RE = re.compile(r"(?<![\\~])~~(?=\S)(.+?)(?<=\S)~~(?!~)")
MARKDOWN_ITALIC_STAR_RE = re.compile(
    r"(?<![\\\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])"
)
MARKDOWN_ITALIC_UNDERSCORE_RE = re.compile(
    r"(?<![\\\w])_(?=\S)([^_\n]+?)(?<=\S)_(?!\w)"
)


def _line_body_and_ending(line: str) -> tuple[str, str]:
    for ending in ("\r\n", "\n", "\r"):
        if line.endswith(ending):
            return line[: -len(ending)], ending
    return line, ""


def _is_matching_fence(line: str, marker: str) -> bool:
    body, _ = _line_body_and_ending(line)
    match = re.fullmatch(r"[ \t]{0,3}(`{3,}|~{3,})[ \t]*", body)
    return bool(
        match
        and match.group(1)[0] == marker[0]
        and len(match.group(1)) >= len(marker)
    )


def _strip_inline_markdown(text: str) -> str:
    text = MARKDOWN_IMAGE_RE.sub(r"\1 (\2)", text)
    text = MARKDOWN_LINK_RE.sub(r"\1 (\2)", text)
    text = MARKDOWN_AUTOLINK_RE.sub(r"\1", text)
    text = MARKDOWN_INLINE_CODE_RE.sub(r"\1", text)
    text = MARKDOWN_STRONG_STAR_RE.sub(r"\1", text)
    text = MARKDOWN_STRONG_UNDERSCORE_RE.sub(r"\1", text)
    text = MARKDOWN_STRIKE_RE.sub(r"\1", text)
    text = MARKDOWN_ITALIC_STAR_RE.sub(r"\1", text)
    return MARKDOWN_ITALIC_UNDERSCORE_RE.sub(r"\1", text)


def _strip_markdown_line(line: str) -> str:
    body, ending = _line_body_and_ending(line)
    if MARKDOWN_HORIZONTAL_RULE_RE.fullmatch(body):
        return ""
    heading = MARKDOWN_HEADING_RE.match(body)
    if heading:
        body = body[heading.end() :]
        return _strip_inline_markdown(body) + ending
    body = MARKDOWN_QUOTE_RE.sub(r"\1", body, count=1)
    body = MARKDOWN_TASK_RE.sub(r"\1", body, count=1)
    body = MARKDOWN_UNORDERED_RE.sub(r"\1", body, count=1)
    body = MARKDOWN_ORDERED_RE.sub(r"\1", body, count=1)
    return _strip_inline_markdown(body) + ending


def strip_markdown_markup(text: str) -> str:
    """Remove conservative Markdown source markers while preserving content."""

    lines = text.splitlines(keepends=True)
    converted: list[str] = []
    index = 0
    while index < len(lines):
        body, _ = _line_body_and_ending(lines[index])
        fence_match = MARKDOWN_FENCE_RE.fullmatch(body)
        if fence_match:
            marker = fence_match.group(1)
            closing_index = index + 1
            while closing_index < len(lines) and not _is_matching_fence(
                lines[closing_index], marker
            ):
                closing_index += 1
            if closing_index < len(lines):
                converted.extend(lines[index + 1 : closing_index])
                index = closing_index + 1
                continue
            converted.extend(lines[index:])
            break
        converted.append(_strip_markdown_line(lines[index]))
        index += 1
    return "".join(converted)
