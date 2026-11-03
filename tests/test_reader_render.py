"""Reader fragments stay useful without fetching images or executing markup."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import unittest

from src.snotes.transport.reader_render import render_markdown, render_redacted


class _Elements(HTMLParser):
    def __init__(self, html: str):
        super().__init__()
        self.elements: list[tuple[str, dict[str, str | None]]] = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class ReaderRenderTests(unittest.TestCase):
    def test_rich_markdown(self):
        result = render_markdown(
            "# 标题\n\n**粗体** *斜体* ~~删除~~ `code`\n\n"
            "> 引用\n\n- first\n- second\n\n1. ordered\n\n---\n\n"
            "```python\nprint('<safe>')\n```\n\n"
            "| a | b |\n| :-- | --: |\n| 左 | 右 |\n"
        )
        for tag in ("h1", "strong", "em", "s", "code", "blockquote", "ul", "ol", "hr", "pre", "table"):
            self.assertIn(tag, [name for name, _ in _Elements(result).elements])
        self.assertIn("&lt;safe&gt;", result)
        self.assertIn('class="align-right"', result)
        self.assertNotIn("style=", result)

    def test_size_directives_keep_only_legal_sizes_and_support_inline_markdown(self):
        result = render_markdown(
            ':size[small]{size=15} :size[normal]{size="17"} '
            ":size[**large**]{size='20'} :size[largest]{size=24} "
            ':size[invalid]{size=19} :size[bad]{size="24px"} '
            ':size[escape]{size="24; color:red"}'
        )
        spans = [attrs for tag, attrs in _Elements(result).elements if tag == "span"]
        self.assertEqual(spans, [{"class": f"size-{size}"} for size in (15, 17, 20, 24)])
        self.assertIn('<span class="size-20"><strong>large</strong></span>', result)
        self.assertIn(":size[invalid]", result)
        self.assertIn(":size[bad]", result)
        self.assertNotIn("style=", result)

    def test_safe_links(self):
        urls = ("https://example.invalid/a?q=1&b=2", "http://example.invalid", "mailto:s@example.invalid", "/reader/note/1", "../next", "#heading", "?q=word")
        result = render_markdown("\n\n".join(f"[link]({url})" for url in urls))
        anchors = [attrs for tag, attrs in _Elements(result).elements if tag == "a"]
        self.assertEqual([attrs["href"] for attrs in anchors], list(urls))
        self.assertTrue(all(attrs.get("rel") == "noopener noreferrer nofollow" for attrs in anchors[:3]))
        self.assertTrue(all("rel" not in attrs for attrs in anchors[3:]))

    def test_unsafe_protocols_and_obfuscation_produce_no_links(self):
        urls = (
            "javascript:alert(1)", "JaVaScRiPt:alert(1)", "jav&#x61;script:alert(1)",
            "javascript&#58;alert(1)", "javascript%3Aalert(1)",
            "java%73cript%253Aalert(1)", "java%09script:alert(1)",
            "java%0ascript:alert(1)", "data:text/html,hello", "data:image/png;base64,AAAA",
            "file:///etc/passwd", "vbscript:msgbox(1)", "ftp://example.invalid/a",
            "//example.invalid/a", "%2f%2fexample.invalid/a", "\\\\example.invalid/a",
        )
        for url in urls:
            with self.subTest(url=url):
                elements = _Elements(render_markdown(f"[unsafe](<{url}>)")).elements
                self.assertFalse(any(tag == "a" for tag, _ in elements))

    def test_raw_html_and_attribute_injection_are_escaped(self):
        result = render_markdown(
            '<script>alert(1)</script>\n\n<img src=x onerror="alert(2)">\n\n'
            '[safe](https://example.invalid "\\\" onmouseover=\\\"alert(3)")\n\n'
            ':size[<svg onload=alert(4)>]{size=24}'
        )
        elements = _Elements(result).elements
        self.assertFalse(any(tag in {"script", "img", "svg", "iframe", "style"} for tag, _ in elements))
        self.assertFalse(any(name.startswith("on") for _, attrs in elements for name in attrs))
        self.assertIn("&lt;script&gt;", result)

    def test_protocol_policy_applies_to_autolinks_references_and_size_children(self):
        result = render_markdown(
            "<javascript:alert(1)> <file:///private> <https://example.invalid>\n\n"
            "[unsafe][ref]\n\n[ref]: javascript%3Aalert(1)\n\n"
            ":size[[hidden](data:text/html,hello)]{size=20}\n\n"
            ":size[![pixel](https://tracker.invalid/pixel)]{size=17}"
        )
        anchors = [attrs for tag, attrs in _Elements(result).elements if tag == "a"]
        self.assertEqual([attrs["href"] for attrs in anchors], ["https://example.invalid", "https://tracker.invalid/pixel"])
        self.assertTrue(all(attrs.get("rel") == "noopener noreferrer nofollow" for attrs in anchors))
        self.assertNotIn("<img", result)

    def test_images_are_explicit_links_never_automatic_requests(self):
        result = render_markdown(
            '![private <alt>](https://tracker.invalid/pixel.png "ignored title")\n\n'
            '![local](/private.png)\n\n![bad](data:image/png;base64,AAAA)\n\n'
            '![reference][image]\n\n[image]: https://images.invalid/a.jpg'
        )
        elements = _Elements(result).elements
        self.assertFalse(any(tag in {"img", "source", "iframe", "object", "embed"} for tag, _ in elements))
        anchors = [attrs for tag, attrs in elements if tag == "a"]
        self.assertEqual([attrs["href"] for attrs in anchors], ["https://tracker.invalid/pixel.png", "/private.png", "https://images.invalid/a.jpg"])
        self.assertEqual(result.count("外部图片"), 3)
        self.assertIn("private &lt;alt&gt;", result)

    def test_redacted_projection_is_only_escaped_text(self):
        safe_text = '# **safe**\n\n[link](https://example.invalid)\n:size[text]{size=24}\n<script>bad</script> & "quoted"'
        result = render_redacted(safe_text)
        self.assertEqual(_Elements(result).elements, [])
        self.assertIn("# **safe**\n\n[link]", result)
        self.assertIn(":size[text]{size=24}", result)
        self.assertIn("&lt;script&gt;bad&lt;/script&gt; &amp; &quot;quoted&quot;", result)
        css = (Path(__file__).parents[1] / "src/snotes/transport/reader.css").read_text()
        self.assertIn(".redacted { white-space: pre-wrap; }", css)
        self.assertNotIn("@import", css)
        self.assertNotIn("url(", css)


if __name__ == "__main__":
    unittest.main()
