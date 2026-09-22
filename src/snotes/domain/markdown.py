"""Pure Markdown → visible text derivation; never a body authority."""
from __future__ import annotations

import re
from markdown_it import MarkdownIt

_SIZE_ATTRIBUTES = re.compile(r'\{size=(?:"(15|20)"|\'(15|20)\'|(15|20))\}')


def _size_directive(state, silent: bool) -> bool:
    start = state.pos
    if not state.src.startswith(':size[', start):
        return False
    end = state.md.helpers.parseLinkLabel(state, start + 5, False)
    if end < 0:
        return False
    attributes = _SIZE_ATTRIBUTES.match(state.src, end + 1)
    if attributes is None:
        return False
    if not silent:
        token = state.push('body_size', '', 0)
        token.children = state.md.parseInline(state.src[start + 6:end], state.env)[0].children
    state.pos = attributes.end()
    return True


def _visible(tokens) -> str:
    parts = []
    for token in tokens:
        if token.children is not None:
            parts.append(_visible(token.children))
        elif token.type in {'text', 'code_inline', 'code_block', 'fence', 'html_inline', 'html_block'}:
            parts.append(token.content)
        elif token.type in {'softbreak', 'hardbreak'}:
            parts.append('\n')
        elif token.type in {'paragraph_close', 'heading_close', 'blockquote_close', 'list_item_close', 'tr_close'}:
            parts.append('\n')
        elif token.type in {'td_close', 'th_close'}:
            parts.append('\t')
    return ''.join(parts)


def markdown_to_plain_text(markdown: str) -> str:
    parser = MarkdownIt('commonmark', {'html': False}).enable(['strikethrough', 'table'])
    parser.inline.ruler.before('link', 'body_size', _size_directive)
    tokens = parser.parse(markdown)
    # Block source ranges retain paragraph spacing without adding a second
    # newline merely because a paragraph is wrapped in a list or quote.
    leaves = [token for token in tokens if token.type in {'inline', 'fence', 'code_block', 'html_block'}]
    if any(token.map is None for token in leaves):
        # Table cell tokens have no block range; keep the existing cell/tab path.
        return _visible(tokens).strip()
    parts = []
    previous = None
    for token in leaves:
        if previous is not None:
            gap = max(0, token.map[0] - previous.map[1])
            parts.append('\n' * (gap + 1))
        value = _visible(token.children) if token.children is not None else token.content.removesuffix('\n')
        parts.append(value)
        previous = token
    return ''.join(parts).strip()
