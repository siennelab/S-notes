from __future__ import annotations

import html
import re
from html.parser import HTMLParser
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


class _SemanticSpans(HTMLParser):
    """Keep CSS-encoded emphasis, never source typography or layout."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.output: list[str] = []
        self.spans: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        if tag != "span":
            self.output.append(self.get_starttag_text())
            return
        style = dict(attrs).get("style") or ""
        declarations = dict(
            (key.strip().lower(), value.strip().lower())
            for item in style.split(";") if ":" in item
            for key, value in [item.split(":", 1)]
        )
        tags = []
        weight = declarations.get("font-weight", "")
        if weight in {"bold", "bolder"} or (weight.isdigit() and int(weight) >= 600):
            tags.append("b")
        if declarations.get("font-style") in {"italic", "oblique"}:
            tags.append("i")
        if "line-through" in declarations.get("text-decoration", "") + declarations.get("text-decoration-line", ""):
            tags.append("s")
        self.spans.append(tags)
        self.output.extend(f"<{tag}>" for tag in tags)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in {"br", "hr", "img", "meta", "link", "input"}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "span":
            if self.spans:
                self.output.extend(f"</{name}>" for name in reversed(self.spans.pop()))
        else:
            self.output.append(f"</{tag}>")

    def handle_data(self, data):
        self.output.append(data)

    def handle_entityref(self, name):
        self.output.append(f"&{name};")

    def handle_charref(self, name):
        self.output.append(f"&#{name};")


def sanitize_html(markup: str) -> str:
    clean = sanitize_pasted_text(markup or "")
    clean = UNSAFE_HTML_BLOCK_RE.sub("", clean)
    clean = UNSAFE_HTML_SINGLE_RE.sub("", clean)
    clean = re.sub(r"(?is)<!--.*?-->", "", clean)
    semantics = _SemanticSpans()
    semantics.feed(clean)
    semantics.close()
    clean = "".join(semantics.output)
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
    clean = re.sub(rf"(?is)</?(?!{allowed}\b)[a-z][^>]*>", "", clean)
    # Qt needs this structural marker for one empty paragraph; a bare br
    # otherwise becomes an additional soft line break (including on copy).
    return re.sub(
        r"(?is)<(p|div)>\s*(?:<br>\s*)?</\1>",
        '<p style="-qt-paragraph-type:empty"><br></p>',
        clean,
    )
