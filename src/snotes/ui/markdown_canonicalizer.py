"""Canonical Markdown through the App's bundled Milkdown editor.

This module has no persistence dependency. A session reuses one off-the-record
WebEngine editor for multiple bodies; callers must use it on the Qt main thread.
"""
from __future__ import annotations

import threading
import time
from uuid import uuid4

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, Qt
from PySide6.QtWidgets import QApplication

from src.snotes.application.ports import CanonicalizationError
from src.snotes.ui.markdown_editor import BUNDLE, MarkdownEditorWidget


MAX_PASSES = 4
_WAIT_SECONDS = 30
_OWNED_APP: QApplication | None = None


class MarkdownCanonicalizer:
    """Reusable, synchronous Milkdown session for main-thread callers.

    Use as a context manager when canonicalizing more than one body. A single
    standalone method call also opens and closes its own session.
    """

    def __init__(self, *, max_passes: int = MAX_PASSES):
        if not isinstance(max_passes, int) or isinstance(max_passes, bool) or max_passes < 1:
            raise ValueError("max_passes must be a positive integer")
        self.max_passes = max_passes
        self._editor: MarkdownEditorWidget | None = None
        self._app: QApplication | None = None
        self._entered = False
        self._busy = False

    def __enter__(self) -> MarkdownCanonicalizer:
        if self._entered:
            raise CanonicalizationError("Canonicalizer session is already open")
        self._require_main_thread()
        if not BUNDLE.is_file():
            raise CanonicalizationError("Bundled Milkdown editor is unavailable")
        existing = QCoreApplication.instance()
        if existing is not None and not isinstance(existing, QApplication):
            raise CanonicalizationError("Milkdown canonicalization requires QApplication")
        try:
            global _OWNED_APP
            if existing is None:
                _OWNED_APP = QApplication(["snotes-markdown-canonicalizer"])
                existing = _OWNED_APP
            self._app = existing
            self._editor = MarkdownEditorWidget()
            self._editor.setAttribute(Qt.WA_DontShowOnScreen)
            self._editor.setAttribute(Qt.WA_QuitOnClose, False)
            self._editor.resize(900, 700)
            self._editor.show()  # Logically shown, without a user-visible window.
            self._entered = True
            return self
        except Exception as error:
            self._close_editor()
            raise CanonicalizationError("Could not start bundled Milkdown editor") from error

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._require_main_thread()
        try:
            self._close_editor()
        finally:
            self._entered = False
            self._app = None

    @staticmethod
    def _require_main_thread() -> None:
        if threading.current_thread() is not threading.main_thread():
            raise CanonicalizationError("Milkdown canonicalization requires the Qt main thread")

    def _wait(self, predicate, *, operation: str) -> None:
        app = self._app
        if app is None:
            raise CanonicalizationError("Milkdown session is not open")
        deadline = time.monotonic() + _WAIT_SECONDS
        while not predicate():
            if time.monotonic() >= deadline:
                raise CanonicalizationError(f"Milkdown {operation} timed out")
            app.processEvents(QEventLoop.AllEvents, 50)
            time.sleep(0.01)

    def _close_editor(self) -> None:
        editor = self._editor
        if editor is None:
            return
        try:
            closed: list[bool] = []
            editor.closeReady.connect(lambda: closed.append(True))
            editor.prepareClose()
            self._wait(lambda: bool(closed), operation="close")
        finally:
            editor.close()
            editor.page.deleteLater()
            editor.deleteLater()
            self._editor = None
            app = self._app
            if app is not None:
                app.processEvents()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def _serialize_once(self, markdown: str) -> str:
        """Load once in the shipped Milkdown editor and return its serializer output."""
        self._require_main_thread()
        editor = self._editor
        if editor is None:
            raise CanonicalizationError("Milkdown session is not open")
        load_id = uuid4().hex
        editor.setMarkdown(markdown, load_id)
        self._wait(lambda: editor.state in {"loaded", "failed"}, operation="load")
        if editor.state != "loaded" or editor.current_load_id != load_id:
            # Do not include WebEngine errors: parser messages may contain body text.
            raise CanonicalizationError("Milkdown load failed")
        return editor.latest_markdown

    def canonicalize_markdown(self, markdown: str) -> str:
        """Return only a Milkdown fixed point, or raise CanonicalizationError."""
        self._require_main_thread()
        if not isinstance(markdown, str):
            raise TypeError("markdown must be a string")
        if not self._entered:
            with self:
                return self.canonicalize_markdown(markdown)
        if self._busy:
            raise CanonicalizationError("Canonicalizer session is already processing a body")
        self._busy = True
        try:
            current = markdown
            for _ in range(self.max_passes):
                try:
                    next_markdown = self._serialize_once(current)
                except CanonicalizationError:
                    raise
                except Exception as error:
                    raise CanonicalizationError("Milkdown serialization failed") from error
                if not isinstance(next_markdown, str):
                    raise CanonicalizationError("Milkdown returned an invalid serialization")
                if next_markdown == current:
                    return current
                current = next_markdown
            raise CanonicalizationError(
                f"Milkdown did not converge within {self.max_passes} passes"
            )
        finally:
            self._busy = False

    def canonicalization_changes(self, markdown: str) -> bool:
        """Whether this body differs byte-for-byte from its stable form."""
        return self.canonicalize_markdown(markdown) != markdown


def canonicalize_markdown(markdown: str) -> str:
    """Canonicalize one body using the App's bundled Milkdown runtime."""
    with MarkdownCanonicalizer() as canonicalizer:
        return canonicalizer.canonicalize_markdown(markdown)


def canonicalization_changes(markdown: str) -> bool:
    """Return whether canonicalization would change an existing body."""
    with MarkdownCanonicalizer() as canonicalizer:
        return canonicalizer.canonicalization_changes(markdown)
