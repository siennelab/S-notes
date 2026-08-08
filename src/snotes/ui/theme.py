"""Three-theme UI owner for the R2 desktop shell.

Theme persistence is explicitly rooted by the bootstrap candidate data path.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSettings, Qt
from PySide6.QtGui import QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QMenu, QWidget


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    name: str
    window: str
    sidebar: str
    sidebar_control: str
    sidebar_separator: str
    sidebar_hover: str
    sidebar_selected: str
    sidebar_text: str
    sidebar_text_muted: str
    sidebar_text_subtle: str
    editor: str
    editor_paper: str
    editor_border: str
    title_separator: str
    surface: str
    surface_hover: str
    surface_selected: str
    text: str
    text_muted: str
    text_subtle: str
    accent: str
    accent_soft: str
    accent_text: str
    warning: str
    danger: str
    focus: str
    scrollbar: str
    disabled: str

    @property
    def document_text(self) -> str:
        return self.text

    @property
    def document_secondary(self) -> str:
        return self.text_muted

    @property
    def document_link(self) -> str:
        return self.accent

    @property
    def document_code(self) -> str:
        return self.text


THEMES: dict[str, ThemeTokens] = {
    "dark": ThemeTokens(
        "dark", "#171819", "#202224", "#292C2E", "#292C2E",
        "#292C2E", "#303438", "#E7E9E8", "#A4AAA7", "#707774",
        "#1B1D1E", "#1B1D1E", "#393D3B", "#76503E", "#242729",
        "#2C3032", "#32383A", "#E7E9E8", "#A4AAA7", "#707774",
        "#B97855", "#3D2D27", "#FFF8F3", "#D8A45B", "#D87171",
        "#CB8A67", "#515855", "#5E6562",
    ),
    "light-slate": ThemeTokens(
        "light-slate", "#F0F2F5", "#394653", "#465563", "#536170",
        "#465563", "#526170", "#F2F5F7", "#CAD2D9", "#9EABB5",
        "#F0F2F5", "#F0F2F5", "#DCDCDC", "#B9C0C6", "#E7EBEE",
        "#DDE3E7", "#D2DBE1", "#23292E", "#68737C", "#929CA4",
        "#5F768B", "#CAD6DF", "#FFFFFF", "#9A6A22", "#A34444",
        "#526C83", "#AEB7BE", "#AAB2B8",
    ),
    "light-clay": ThemeTokens(
        "light-clay", "#E8E6E2", "#F3F2EF", "#EAE7E3", "#EAE7E3",
        "#EAE7E3", "#E2DDD7", "#2A2723", "#716C65", "#9B958C",
        "#FFFFFF", "#FFFFFF", "#D6CEC5", "#CFC5BA", "#F7F6F4",
        "#EEEAE6", "#E6E0DA", "#2A2723", "#716C65", "#9B958C",
        "#A84B2F", "#F0DDD6", "#FFFFFF", "#966A2B", "#A23E37",
        "#A84B2F", "#BBB5AD", "#AEA8A0",
    ),
}

DEFAULT_THEME = "light-slate"
THEME_LABELS = (("dark", "夜间"), ("light-slate", "石板"), ("light-clay", "陶土"))
THEME_SETTINGS_KEY = "theme"
ICON_DIR = Path(__file__).resolve().parents[2] / "icons"
_current_theme_name = DEFAULT_THEME


def normalize_theme_name(value: object) -> str:
    name = str(value or "")
    return name if name in THEMES else DEFAULT_THEME


def set_current_theme(name: str) -> ThemeTokens:
    global _current_theme_name
    _current_theme_name = normalize_theme_name(name)
    return THEMES[_current_theme_name]


def current_theme() -> ThemeTokens:
    return THEMES[_current_theme_name]


def load_saved_theme(settings_path: Path) -> str:
    settings = QSettings(str(settings_path), QSettings.IniFormat)
    return normalize_theme_name(settings.value(THEME_SETTINGS_KEY, DEFAULT_THEME))


def save_theme(settings_path: Path, name: str) -> str:
    theme_name = normalize_theme_name(name)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings = QSettings(str(settings_path), QSettings.IniFormat)
    settings.setValue(THEME_SETTINGS_KEY, theme_name)
    settings.sync()
    return theme_name


def editor_document_style() -> str:
    return "body { font-size: 15px; } p { margin: 0 0 8px 0; }"


def build_app_style(name: str) -> str:
    t = THEMES[normalize_theme_name(name)]
    return f"""
QMainWindow, QWidget#shellRoot {{ background: {t.editor}; color: {t.text}; }}
QWidget {{ color: {t.text}; font-size: 13px; font-family: 'PingFang SC', 'SF Pro Text', 'Helvetica Neue', sans-serif; }}
#sidebar {{ background: {t.sidebar}; }}
QSplitter::handle {{ background: {t.sidebar_separator}; }}
#brandLabel {{ color: {t.sidebar_text}; font-size: 15px; font-weight: 600; }}
#noteList {{ border: 0; outline: 0; background: transparent; color: {t.sidebar_text_muted}; }}
#noteList QWidget {{ background: transparent; }}
#noteList::item {{ min-height: 32px; max-height: 32px; border-radius: 6px; padding: 0; }}
#noteList::item:hover {{ background: {t.sidebar_hover}; color: {t.sidebar_text}; }}
#noteList::item:selected {{ background: {t.sidebar_selected}; color: {t.sidebar_text}; }}
QPushButton, QToolButton {{ border: 1px solid transparent; border-radius: 6px; padding: 4px 8px; background: transparent; color: {t.text}; }}
QPushButton:hover, QToolButton:hover {{ background: {t.surface_hover}; }}
QPushButton:pressed, QToolButton:pressed, QToolButton:checked {{ background: {t.surface_selected}; }}
QPushButton:focus, QToolButton:focus {{ border-color: {t.focus}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
#newNoteButton {{ background: {t.accent}; color: {t.accent_text}; }}
#newNoteButton:hover {{ background: {t.focus}; }}
#themeButton {{ color: {t.sidebar_text_muted}; padding: 4px; }}
#themeButton:hover {{ background: {t.sidebar_hover}; color: {t.sidebar_text}; }}
#themeButton[active='true'] {{ background: {t.sidebar_selected}; color: {t.sidebar_text}; }}
#mainContentArea, #editorPane {{ background: {t.editor}; }}
#titleInput {{ border: 0; border-bottom: 2px solid {t.editor_border}; border-radius: 0; padding: 8px; background: transparent; color: {t.text}; font-size: 26px; font-weight: 600; }}
#titleInput:focus {{ border: 0; border-bottom: 2px solid {t.focus}; }}
#editorToolbar {{ background: transparent; border: 0; border-radius: 0; }}
#findPanel {{ background: transparent; border: 0; border-radius: 0; }}
#toolbarRow, #toolbarGroup {{ background: transparent; }}
#toolbarSeparator {{ background: {t.editor_border}; margin: 4px 0; }}
#toolbarButton {{ padding: 4px; color: {t.text_muted}; }}
#toolbarButton:hover {{ background: {t.surface_hover}; color: {t.text}; }}
#toolbarButton:checked, #toolbarButton:pressed {{ background: {t.surface_selected}; color: {t.text}; }}
#toolbarInput {{ border: 1px solid {t.editor_border}; border-radius: 6px; padding: 4px 8px; background: {t.surface}; color: {t.text}; }}
#bodyEdit {{ border: 1px solid {t.editor_border}; border-radius: 4px; background: transparent; padding: 8px; color: {t.text}; font-size: 15px; }}
#bodyEdit QWidget {{ background: transparent; }}
#bodyEdit:focus {{ border: 1px solid {t.focus}; }}
#statusLabel {{ background: transparent; color: {t.text_subtle}; padding: 4px 0; font-size: 12.5px; }}
#statusLabel[conflict='true'] {{ color: {t.warning}; }}
QMenu {{ background-color: {t.sidebar}; color: {t.sidebar_text}; border: 1px solid {t.sidebar_separator}; border-radius: 10px; padding: 8px; }}
QMenu::item {{ background-color: transparent; color: {t.sidebar_text}; border-radius: 6px; padding: 8px 24px 8px 12px; }}
QMenu::item:disabled {{ color: {t.sidebar_text_subtle}; }}
QMenu::item:selected:enabled {{ background-color: {t.sidebar_selected}; color: {t.sidebar_text}; }}
QMenu::separator {{ height: 1px; background-color: {t.sidebar_separator}; margin: 4px 8px; }}
QScrollBar:vertical {{ width: 10px; background: transparent; }}
QScrollBar::handle:vertical {{ min-height: 24px; border-radius: 5px; background: {t.scrollbar}; }}
QScrollBar::handle:vertical:disabled {{ background: transparent; }}
QScrollBar[scrollable='false']::handle:vertical {{ background: transparent; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


def apply_theme(app: QApplication, name: str) -> ThemeTokens:
    tokens = set_current_theme(name)
    app.setStyleSheet(build_app_style(tokens.name))
    return tokens


def apply_popup_menu_theme(
    menu: QMenu,
    tokens: ThemeTokens | None = None,
) -> None:
    """Give child-owned popup menus an opaque theme palette."""

    tokens = tokens or current_theme()
    menu.setAttribute(Qt.WA_StyledBackground, True)
    menu.ensurePolished()
    menu.setAttribute(Qt.WA_TranslucentBackground, False)
    menu.setAttribute(Qt.WA_NoSystemBackground, False)
    menu.setAutoFillBackground(True)
    palette = QPalette(menu.palette())
    for role in (QPalette.Window, QPalette.Base, QPalette.Button):
        palette.setColor(role, tokens.sidebar)
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(role, tokens.sidebar_text)
    palette.setColor(QPalette.Disabled, QPalette.WindowText, tokens.sidebar_text_subtle)
    palette.setColor(QPalette.Disabled, QPalette.Text, tokens.sidebar_text_subtle)
    palette.setColor(QPalette.Highlight, tokens.sidebar_selected)
    palette.setColor(QPalette.HighlightedText, tokens.sidebar_text)
    menu.setPalette(palette)


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


@lru_cache(maxsize=256)
def _render_icon(theme_name: str, filename: str, role: str, size: int) -> QIcon:
    source = (ICON_DIR / filename).read_text(encoding="utf-8")
    color = getattr(THEMES[theme_name], role)
    renderer = QSvgRenderer(QByteArray(source.replace("currentColor", color).encode()))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return QIcon(pixmap)


def themed_icon(filename: str, role: str = "text_muted", size: int = 14) -> QIcon:
    return _render_icon(_current_theme_name, filename, role, size)
