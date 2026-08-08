from __future__ import annotations

import html
import re
from urllib.parse import urlsplit


CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
HTML_MARKER_RE = re.compile(
    r"</?(html|body|p|div|br|ul|ol|li|h[1-6]|blockquote|pre|span)\b",
    re.I,
)
ALLOWED_HTML_TAGS = (
    "p",
    "br",
    "b",
    "strong",
    "i",
    "em",
    "s",
    "strike",
    "del",
    "ul",
    "ol",
    "li",
    "pre",
    "code",
    "blockquote",
    "a",
    "div",
    "h1",
    "h2",
    "h3",
)
UNSAFE_HTML_BLOCK_RE = re.compile(
    r"(?is)<(script|style|object|iframe|embed|link|meta)[^>]*>.*?</\1\s*>"
)
UNSAFE_HTML_SINGLE_RE = re.compile(
    r"(?is)<(script|style|object|iframe|embed|link|meta)[^>]*>"
)
HTML_ATTRIBUTE_RE = re.compile(
    r'''(?is)\s(?:style|class|id|on\w+|src|data-[\w-]+)=("[^"]*"|'[^']*'|[^\s>]+)'''
)
HREF_ATTRIBUTE_RE = re.compile(
    r'''(?is)\bhref\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)'''
)


def contains_html_markup(content: str) -> bool:
    return bool(HTML_MARKER_RE.search(content or ""))


def sanitize_pasted_text(text: str) -> str:
    clean_text = CONTROL_CHAR_RE.sub("", text or "")
    return clean_text.replace("\r\n", "\n").replace("\r", "\n")


def safe_link_target(raw_value: str) -> str:
    value = html.unescape((raw_value or "").strip().strip("\"'"))
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme.casefold() not in {"http", "https", "mailto"}:
        return ""
    return value


def sanitize_html(markup: str) -> str:
    clean = sanitize_pasted_text(markup or "")
    clean = UNSAFE_HTML_BLOCK_RE.sub("", clean)
    clean = UNSAFE_HTML_SINGLE_RE.sub("", clean)
    clean = re.sub(r"(?is)<!--.*?-->", "", clean)
    clean = re.sub(r"(?is)<\s*span[^>]*>", "", clean)
    clean = re.sub(r"(?is)</\s*span\s*>", "", clean)
    clean = HTML_ATTRIBUTE_RE.sub("", clean)

    allowed = "|".join(ALLOWED_HTML_TAGS)

    def normalize_tag(match: re.Match[str]) -> str:
        slash = "/" if match.group(1) else ""
        tag = match.group(2).lower()
        if tag == "br":
            return "<br>"
        if tag in {"s", "strike", "del"}:
            return f"<{slash}s>"
        if tag == "a" and not slash:
            href_match = HREF_ATTRIBUTE_RE.search(match.group(3) or "")
            href = safe_link_target(href_match.group(1) if href_match else "")
            if href:
                return f'<a href="{html.escape(href, quote=True)}">'
        return f"<{slash}{tag}>"

    clean = re.sub(
        rf"(?is)<\s*(/?)\s*({allowed})\b([^>]*)>",
        normalize_tag,
        clean,
    )
    return re.sub(rf"(?is)</?(?!{allowed}\b)[a-z][^>]*>", "", clean)
