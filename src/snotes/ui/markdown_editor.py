"""Standalone Markdown component. No NoteService, persistence, or document model."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import QVBoxLayout, QWidget
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from src.snotes.ui.theme import ThemeTokens, current_theme

BUNDLE = Path(__file__).resolve().parents[3] / "web" / "markdown-editor" / "dist" / "index.html"


class MarkdownBridge(QObject):
    setMarkdown = Signal(str, str)
    prepareClose = Signal()
    setTheme = Signal('QVariantMap')
    setReadOnly = Signal(bool)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner

    @Slot()
    def editorReady(self):
        self.owner._ready()

    @Slot(str, str)
    def loadComplete(self, load_id, markdown):
        self.owner._loaded(load_id, markdown)

    @Slot(str, str)
    def loadFailed(self, load_id, reason):
        self.owner._failed(load_id, reason)

    @Slot(str, str)
    def markdownChanged(self, markdown, load_id):
        self.owner._changed(markdown, load_id)

    @Slot()
    def closeReady(self):
        if self.owner.closing:
            self.owner.closeReady.emit()


class EditorPage(QWebEnginePage):
    def __init__(self, profile, parent, url):
        super().__init__(profile, parent)
        self.editor_url = url
        self.newWindowRequested.connect(lambda request: self._external(request.requestedUrl()))

    @staticmethod
    def _external(url):
        if url.scheme() in {"https", "http", "mailto"}:
            QDesktopServices.openUrl(url)

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        if is_main_frame and url == self.editor_url and navigation_type != QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            return True
        if navigation_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            self._external(url)
        return False


class MarkdownEditorWidget(QWidget):
    """Consumers may save latest_markdown only while can_save is true.

    Supply a fresh load_id on EVERY load, including identical bodies. loaded is
    a baseline notification, never a save request. changed represents accepted
    user edits. This component performs no saves and changes no revision.
    """
    editorReady = Signal()
    loaded = Signal(str, str)
    loadFailed = Signal(str, str)
    changed = Signal(str, str)
    stateChanged = Signal()
    closeReady = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_load_id = None
        self.baseline = self.latest_markdown = ""
        self.state = "starting"
        self.error = ""
        self.dirty = False
        self.read_only = False
        self.closing = False
        self.ready = False
        self._pending = None
        self._theme = current_theme()
        self._used_ids = set()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = QWebEngineView(self)
        self.view.setAcceptDrops(False)
        self.profile = QWebEngineProfile(self)  # Unnamed profile is off-the-record.
        self.page = EditorPage(self.profile, self.view, QUrl.fromLocalFile(str(BUNDLE)))
        self.page.setBackgroundColor(QColor(self._theme.editor_paper))
        self.page.settings().setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, False)
        self.view.setPage(self.page)
        self.channel = QWebChannel(self.page)
        self.bridge = MarkdownBridge(self)
        self.channel.registerObject("host", self.bridge)
        self.page.setWebChannel(self.channel)
        self.page.renderProcessTerminated.connect(lambda *_: self._unavailable("编辑器进程已停止"))
        self.view.loadFinished.connect(lambda ok: None if ok else self._unavailable("本地资源加载失败"))
        layout.addWidget(self.view, 1)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._unavailable("编辑器加载超时，禁止保存"))
        self._timer.start(20000)
        self.view.load(self.page.editor_url)

    @property
    def can_save(self):
        return self.state == "loaded" and not self.read_only

    def _notify(self):
        self.stateChanged.emit()

    def setMarkdown(self, markdown: str, load_id: str):
        if not isinstance(markdown, str) or not isinstance(load_id, str) or not load_id:
            raise ValueError("Markdown and a non-empty string load_id are required")
        if load_id in self._used_ids:
            raise ValueError("Every load requires a fresh load_id")
        if self.closing:
            raise RuntimeError("Editor is closing")
        self._used_ids.add(load_id)
        # The view stays visible: a hidden QWebEngineView throttles renderer
        # timers to ~1 s. The Web side keeps the previous document locked and
        # replaces it in one step; hosts commit title/metadata on loaded.
        self.baseline = self.latest_markdown = ""
        self.dirty = False
        self.current_load_id = load_id
        self.state, self.error, self.dirty = "loading", "", False
        self._pending = (markdown, load_id)
        self._timer.start(20000)
        self._notify()
        if self.ready:
            self.bridge.setMarkdown.emit(markdown, load_id)

    def setTheme(self, theme: ThemeTokens):
        self._theme = theme
        self.page.setBackgroundColor(QColor(theme.editor_paper))
        if self.ready:
            self.bridge.setTheme.emit(asdict(theme))

    def setReadOnly(self, read_only: bool):
        self.read_only = bool(read_only)
        if self.ready:
            self.bridge.setReadOnly.emit(self.read_only)
        self._notify()

    def prepareClose(self):
        if self.closing:
            return  # One composition/delivery barrier at a time.
        self.closing = True
        if self.ready:
            self.bridge.prepareClose.emit()
        else:
            # No editor could have accepted input yet.
            self.closeReady.emit()

    def _ready(self):
        if self.ready:
            return
        self.ready = True
        self._timer.stop()
        self.bridge.setTheme.emit(asdict(self._theme))
        self.bridge.setReadOnly.emit(self.read_only)
        if self._pending:
            self._timer.start(20000)
            self.bridge.setMarkdown.emit(*self._pending)
        self.editorReady.emit()

    def _loaded(self, load_id, markdown):
        if load_id != self.current_load_id or self.state != "loading":
            return
        self._timer.stop()
        self._pending = None
        self.baseline = self.latest_markdown = markdown
        self.dirty = False
        self.state, self.error = "loaded", ""
        self._notify()
        self.loaded.emit(load_id, markdown)

    def _failed(self, load_id, reason):
        if load_id != self.current_load_id:
            return
        self._timer.stop()
        self.state, self.error = "failed", reason
        self.baseline = self.latest_markdown = ""
        self.dirty = False
        self._pending = None
        self._notify()
        self.loadFailed.emit(load_id, reason)

    def _changed(self, markdown, load_id):
        if load_id != self.current_load_id or not self.can_save:
            return
        if markdown == self.latest_markdown:
            return
        self.latest_markdown = markdown
        self.dirty = markdown != self.baseline
        self._notify()
        self.changed.emit(markdown, load_id)

    def _unavailable(self, reason):
        self.ready = False
        self._failed(self.current_load_id, reason)
