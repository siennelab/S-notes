"""Read-only Reader HTML rendering; never a Markdown authority or sanitizer."""
from __future__ import annotations

from html import escape, unescape
import re
import unicodedata
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML


# Keep this grammar aligned with domain.markdown's visible-text derivation.
_SIZE_ATTRIBUTES = re.compile(r'\{size=(?:"(15|17|20|24)"|\'(15|17|20|24)\'|(15|17|20|24))\}')
_SIZES = frozenset({"15", "17", "20", "24"})
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_EXTERNAL_SCHEMES = frozenset({"http", "https", "mailto"})


def _safe_link(url: str) -> bool:
    """Allow a small URL vocabulary, including after decoding obfuscation.

    The parser normalizes URLs before this check. Decode a second time only
    for inspection, never for output, so encoded schemes and controls cannot
    disguise an otherwise rejected destination.
    """
    probe = url
    # Stop at a fixed point; each changed pass shortens encoded sequences.
    while True:
        decoded = unicodedata.normalize("NFKC", unescape(unquote(probe)))
        if decoded == probe:
            break
        probe = decoded
    if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in probe):
        return False
    if "\\" in probe or probe != probe.strip() or probe.startswith("//"):
        return False
    try:
        parsed = urlsplit(probe)
    except ValueError:
        return False
    if parsed.scheme:
        if parsed.scheme.lower() not in _EXTERNAL_SCHEMES:
            return False
        if parsed.scheme.lower() in {"http", "https"}:
            return bool(parsed.netloc and parsed.hostname)
        return bool(parsed.path)
    # A colon in a relative path's first segment cannot introduce a scheme.
    first_segment = re.split(r"[/#?]", probe, maxsplit=1)[0]
    return ":" not in first_segment


def _size_directive(state, silent: bool) -> bool:
    start = state.pos
    if not state.src.startswith(":size[", start):
        return False
    end = state.md.helpers.parseLinkLabel(state, start + 5, False)
    if end < 0:
        return False
    attributes = _SIZE_ATTRIBUTES.match(state.src, end + 1)
    if attributes is None:
        return False
    if not silent:
        token = state.push("body_size", "", 0)
        token.meta["size"] = next(value for value in attributes.groups() if value)
        token.children = state.md.parseInline(state.src[start + 6:end], state.env)[0].children
    state.pos = attributes.end()
    return True


class _ReaderRenderer(RendererHTML):
    def body_size(self, tokens, idx, options, env) -> str:
        token = tokens[idx]
        size = token.meta.get("size")
        children = self.renderInline(token.children or [], options, env)
        if size not in _SIZES:
            return children
        return f'<span class="size-{size}">{children}</span>'

    def link_open(self, tokens, idx, options, env) -> str:
        token = tokens[idx]
        href = token.attrGet("href") or ""
        if _SCHEME.match(href):
            token.attrSet("rel", "noopener noreferrer nofollow")
        return self.renderToken(tokens, idx, options, env)

    def image(self, tokens, idx, options, env) -> str:
        token = tokens[idx]
        alt = self.renderInlineAsText(token.children, options, env)
        label = "外部图片" + (f"：{alt}" if alt else "")
        href = token.attrGet("src") or ""
        if not _safe_link(href):
            return escape(label)
        rel = ' rel="noopener noreferrer nofollow"' if _SCHEME.match(href) else ""
        return f'<a href="{escape(href, quote=True)}"{rel}>{escape(label)}</a>'

    def _table_cell(self, tokens, idx, options, env) -> str:
        token = tokens[idx]
        alignment = token.attrs.pop("style", "")
        if alignment in {"text-align:left", "text-align:center", "text-align:right"}:
            token.attrSet("class", "align-" + alignment.removeprefix("text-align:"))
        return self.renderToken(tokens, idx, options, env)

    th_open = _table_cell
    td_open = _table_cell


def render_markdown(markdown: str) -> str:
    """Render authorized Markdown into an inert, image-free HTML fragment."""
    parser = MarkdownIt("commonmark", {"html": False}, renderer_cls=_ReaderRenderer)
    parser.enable(["strikethrough", "table"])
    parser.validateLink = _safe_link
    parser.inline.ruler.before("link", "body_size", _size_directive)
    return parser.render(markdown)


def render_redacted(safe_text: str) -> str:
    """Escape an already authorized safe projection without parsing Markdown."""
    return escape(safe_text, quote=True)
