"""Test double for the application Markdown canonicalizer port."""

from __future__ import annotations

from collections.abc import Callable


class CanonicalizerTestDouble:
    """Delegate normal calls while allowing tests to replace port behavior."""

    def __init__(self, canonicalize: Callable[[str], str]) -> None:
        self.canonicalize = canonicalize

    def canonicalize_markdown(self, markdown: str) -> str:
        return self.canonicalize(markdown)
