"""Migration-only Qt HTML conversion, using the retired editor's geometry.

Code: left=16/24, top=bottom=8, or background with positive left.
Quote: left>=20, tested AFTER code, matching editor recovery precedence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
import re


@dataclass
class Element:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list = field(default_factory=list)


class Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Element('root')
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Element(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {'br', 'img', 'meta', 'link', 'hr', 'input'}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, value):
        self.stack[-1].children.append(value)


@dataclass(frozen=True)
class Conversion:
    markdown: str
    known_losses: tuple[str, ...]


def _escape(value):
    return re.sub(r'([\\`*_{}\[\]<>~#!|+\-.=:&])', r'\\\1', value)


def legacy_qt_html_to_markdown(source: str) -> Conversion:
    tree = Tree()
    if not re.search(r'</?[A-Za-z][^>]*>', source):
        # Plain legacy text has no Markdown syntax semantics. Use the same
        # escaping and paragraph normalization as Qt's inline HTML content.
        children = []
        for line in source.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
            if children:
                children.append(Element('br'))
            children.append(line)
        tree.root.children = [Element('p', children=children)]
    else:
        tree.feed(source)
        tree.close()
    losses = set()

    def style(node):
        result = {}
        for entry in (node.attrs.get('style') or '').split(';'):
            if ':' in entry:
                key, value = entry.split(':', 1)
                result[key.strip().lower()] = value.strip().lower()
        return result

    def number(styles, name):
        value = re.match(r'-?[\d.]+', styles.get(name, '0'))
        return float(value[0]) if value else 0

    def kind(node):
        st = style(node)
        left = number(st, 'margin-left')
        if node.tag == 'pre' or (left in {16, 24} and number(st, 'margin-top') == 8 and number(st, 'margin-bottom') == 8) or (left > 0 and ('background-color' in st or 'background' in st)):
            return 'code'
        if node.tag == 'blockquote' or left >= 20:
            return 'quote'
        return node.tag

    def audit(node):
        st = style(node)
        for key in ('color', 'background-color', 'background', 'font-family'):
            if key in st and not (key.startswith('background') and kind(node) == 'code'):
                losses.add(f'{key}: {st[key]}')
        if node.tag == 'u' or 'underline' in st.get('text-decoration', ''):
            losses.add('underline')
        if 'font-size' in st and st['font-size'] not in {'15px', '17px', '20px'}:
            losses.add('font-size: ' + st['font-size'])
        if node.tag in {'img', 'table', 'video', 'iframe'}:
            losses.add('unsupported element: ' + node.tag)

    def raw(node):
        if isinstance(node, str):
            return node
        audit(node)
        if node.tag == 'br':
            return '\n'
        return ''.join(raw(child) for child in node.children)

    def marks(node):
        st = style(node)
        result = []
        if node.tag in {'b', 'strong'} or st.get('font-weight') in {'bold', '600', '700', '800', '900'}:
            result.append('bold')
        if node.tag in {'i', 'em'} or st.get('font-style') == 'italic':
            result.append('italic')
        if node.tag in {'s', 'strike', 'del'} or 'line-through' in st.get('text-decoration', ''):
            result.append('strike')
        if st.get('font-size') == '20px':
            result.append('size20')
        return tuple(result)

    def coalesce(nodes):
        # Qt may split one mark into adjacent spans (e.g. differing retired CSS).
        # Merge equivalent HTML siblings BEFORE serialization, not Markdown regex.
        result = []
        inline_tags = {'span', 'b', 'strong', 'em', 'i', 's', 'strike', 'del'}
        for node in nodes:
            if isinstance(node, Element):
                audit(node)
                node.children = coalesce(node.children)
            if (result and isinstance(node, Element) and isinstance(result[-1], Element)
                    and node.tag in inline_tags and result[-1].tag in inline_tags
                    and marks(node) == marks(result[-1])):
                result[-1].children.extend(node.children)
            else:
                result.append(node)
        return result

    def wrap(value, start, end):
        leading = value[:len(value) - len(value.lstrip())]
        trailing = value[len(value.rstrip()):]
        core = value.strip()
        return leading + start + core + end + trailing if core else value

    def inline(node, inherited=()):
        if isinstance(node, str):
            return _escape(node).replace('\xa0', '&#160;').replace('\t', '&#9;')
        if node.tag in {'head', 'style', 'script', 'meta', 'title'}:
            return ''
        audit(node)
        if node.tag == 'br':
            return '\\\n'
        if node.tag == 'code':
            value = raw(node)
            fence = '`' * (max([len(x) for x in re.findall(r'`+', value)] or [0]) + 1)
            return fence + ' ' + value + ' ' + fence
        own = marks(node)
        value = ''.join(inline(child, inherited + own) for child in node.children)
        for mark in own:
            if mark in inherited:
                continue
            if mark == 'bold':
                value = wrap(value, '**', '**')
            elif mark == 'italic':
                value = wrap(value, '*', '*')
            elif mark == 'strike':
                value = wrap(value, '~~', '~~')
            elif mark == 'size20':
                value = ':size[' + value + ']{size="20"}' if value.strip() else ''
        if node.tag == 'a' and node.attrs.get('href'):
            href = node.attrs['href'].replace(' ', '%20').replace('<', '%3C').replace('>', '%3E')
            value = '[' + value + '](<' + href + '>)'
        return value

    def paragraph(value):
        # Markdown consumes line indentation and dangling hardbreaks. Encode
        # meaningful indentation as entities, retaining the original characters.
        while value.startswith('\\\n'):
            value = value[2:]
        value = re.sub(r'(?:\\\n){3,}', lambda _: '\\\n' * 2, value)
        while value.endswith('\\\n'):
            value = value[:-2]
        value = re.sub(r'(?m)^ +', lambda m: '&#32;' * len(m[0]), value)
        return value

    def paragraph_segments(node):
        # Split the HTML tree BEFORE Markdown serialization, so marks/links
        # spanning a blank line are reopened in each paragraph and literal
        # block markers still pass through normal escaping. Single breaks stay
        # inside their block. Code, headings and lists use their own semantics.
        text = raw(node)
        gaps = list(re.finditer(r'\n[^\S\n]*\n(?:[^\S\n]*\n)*', text))
        if not gaps:
            return [node]

        def clip(current, start, end):
            if isinstance(current, str):
                return current[start:end]
            if current.tag == 'br':
                return Element('br') if start < end else ''
            children = []
            offset = 0
            for child in current.children:
                length = len(raw(child))
                left, right = max(0, start - offset), min(length, end - offset)
                if left < right:
                    children.append(clip(child, left, right))
                offset += length
            return Element(current.tag, current.attrs.copy(), children)

        bounds = [0] + [point for gap in gaps for point in (gap.start(), gap.end())] + [len(text)]
        return [clip(node, start, end) for start, end in zip(bounds[::2], bounds[1::2])
                if text[start:end].strip()]

    def blocks(nodes):
        result = []
        code_lines = []
        pre_endings = set()
        def flush_code(*, pre=False):
            if code_lines:
                value = '\n'.join(code_lines)
                fence = '`' * max(3, 1 + max([len(x) for x in re.findall(r'`+', value)] or [0]))
                result.append(fence + '\n' + value + '\n' + fence)
                if pre:
                    pre_endings.add(len(result) - 1)
                code_lines.clear()
        for node in nodes:
            if isinstance(node, str):
                if node.strip():
                    flush_code()
                    result.append(_escape(node.strip()))
                continue
            if node.tag in {'head', 'style', 'script', 'meta', 'title'}:
                continue
            audit(node)
            if node.tag == 'p' and not raw(node).strip('\r\n'):
                continue
            category = kind(node)
            if category == 'code':
                if node.tag == 'pre':
                    flush_code()
                code_lines.append(raw(node).rstrip('\n'))
                if node.tag == 'pre':
                    flush_code(pre=True)
                continue
            flush_code()
            if node.tag in {'root', 'html', 'body', 'div'}:
                result.append(blocks(node.children))
            elif node.tag in {'ul', 'ol'}:
                items = []
                for index, child in enumerate(c for c in node.children if isinstance(c, Element) and c.tag == 'li'):
                    nested = [c for c in child.children if isinstance(c, Element) and c.tag in {'ul', 'ol'}]
                    text = ''.join(inline(c) for c in child.children if c not in nested).strip('\r\n')
                    marker = f'{index + 1}. ' if node.tag == 'ol' else '- '
                    items.append(marker + text.replace('\n', '\n' + ' ' * len(marker)))
                    if nested:
                        items.append('\n'.join(' ' * len(marker) + line for line in blocks(nested).splitlines()))
                result.append(('\n\n' if any(isinstance(c, str) and '\n' in c for c in node.children) else '\n').join(items))
            elif category == 'quote':
                text = blocks(node.children) if node.tag == 'blockquote' else inline(node).strip()
                result.append('\n'.join('> ' + line for line in text.split('\n')))
            elif node.tag in {'h1', 'h2', 'h3'}:
                text = inline(node).strip() if raw(node).strip() else ''
                result.append('#' * int(node.tag[1]) + ' ' + text)
            else:
                result.extend(paragraph(inline(segment)) for segment in paragraph_segments(node))
        flush_code()
        parts = []
        separator = ''
        for index, part in enumerate(result):
            if not part.strip():
                continue
            parts.append(separator + part)
            # Legacy export adds a newline for </p>, but not for </pre>.
            separator = '\n' if index in pre_endings else '\n\n'
        return ''.join(parts)

    return Conversion(blocks(coalesce(tree.root.children)).strip(), tuple(sorted(losses)))
