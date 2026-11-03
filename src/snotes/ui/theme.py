"""Three-theme UI owner for the R2 desktop shell.

Theme persistence is explicitly rooted by the bootstrap candidate data path.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSettings, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
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

    @property
    def selection_background(self) -> str:
        return self.accent_soft

    @property
    def selection_text(self) -> str:
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
UI_FONT_FAMILY = "'PingFang SC', 'SF Pro Text', 'Helvetica Neue', sans-serif"
_current_theme_name = DEFAULT_THEME
THEME_SWATCH_COLORS = {
    "dark": "#1B1D1E",
    "light-slate": "#5F768B",
    "light-clay": "#A84B2F",
}
THEME_SWATCH_RING_COLORS = {
    "dark": "#555B60",
    "light-slate": "#8FA1B1",
    "light-clay": "#C6816A",
}


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
QMainWindow, QDialog, QMessageBox, QInputDialog, QWidget#shellRoot {{ background: {t.editor}; color: {t.text}; }}
QWidget {{ color: {t.text}; font-size: 13px; font-family: {UI_FONT_FAMILY}; }}
QLabel {{ background: transparent; }}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {{
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 4px 8px;
    background: {t.surface};
    color: {t.text};
    selection-background-color: {t.selection_background};
    selection-color: {t.selection_text};
    placeholder-text-color: {t.text_subtle};
}}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover, QComboBox:hover {{ background: {t.surface_hover}; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
    border-color: {t.focus}; background: {t.surface};
}}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled {{
    color: {t.disabled}; background: {t.surface};
}}
QComboBox {{ padding-right: 24px; combobox-popup: 0; }}
QComboBox::drop-down {{ border: 0; width: 24px; }}
QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
#sidebar {{ background: {t.sidebar}; }}
QSplitter::handle {{ background: {t.sidebar_separator}; }}
QSplitter::handle:hover {{ background: {t.accent}; }}
#brandLabel {{ color: {t.sidebar_text}; font-size: 15px; font-weight: 600; }}
#sidebarClockTime, #sidebarClockDate {{ color: {t.sidebar_text}; font-size: 15px; font-weight: 500; }}
#sidebarSearch, #categoryFilter, #aiActorFilterCombo {{
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 4px 8px;
    background: {t.sidebar_control};
    color: {t.sidebar_text};
    selection-background-color: {t.sidebar_selected};
    selection-color: {t.sidebar_text};
}}
#sidebarSearch:hover, #categoryFilter:hover, #aiActorFilterCombo:hover {{ background: {t.sidebar_hover}; }}
#sidebarSearch:focus, #categoryFilter:focus, #aiActorFilterCombo:focus {{ border-color: {t.focus}; background: {t.sidebar_control}; }}
#aiActorFilter {{ background: transparent; }}
#aiActorFilterLabel {{
    color: {t.sidebar_text_muted}; font-size: 12px; font-weight: 600; padding-left: 2px;
}}
#aiActorFilterCombo {{ border-color: {t.sidebar_separator}; padding: 3px 26px 3px 9px; }}
#aiActorFilterCombo:disabled {{
    border-color: transparent; background: {t.sidebar_control}; color: {t.sidebar_text_subtle};
}}
#sidebarSearch QToolButton {{ background: transparent; border: 0; padding: 0; }}
#sidebar QComboBox QAbstractItemView {{
    border: 1px solid {t.sidebar_separator};
    background: {t.sidebar};
    color: {t.sidebar_text};
    selection-background-color: {t.sidebar_selected};
    selection-color: {t.sidebar_text};
    outline: 0;
}}
#sidebarComboPopupView, #comboPopupView {{
    border: 0;
    border-radius: 10px;
    outline: 0;
    padding: 4px;
    selection-background-color: transparent;
}}
#sidebarComboPopupView {{ background: {t.sidebar}; color: {t.sidebar_text}; selection-color: {t.sidebar_text}; }}
#comboPopupView {{ background: {t.surface}; color: {t.text}; selection-color: {t.text}; }}
#sidebarComboPopupView::item, #comboPopupView::item {{ min-height: 32px; padding: 0; background: transparent; }}
#sidebarComboPopupView::item:hover, #sidebarComboPopupView::item:selected {{
    background: {t.sidebar_selected}; color: {t.sidebar_text};
}}
#sidebarComboPopupView::item:disabled {{ color: {t.sidebar_text_subtle}; }}
#comboPopupView::item:hover, #comboPopupView::item:selected {{ background: {t.surface_hover}; color: {t.text}; }}
#comboPopupView::item:disabled {{ color: {t.disabled}; }}
#floatingComboPopup {{ background: transparent; border: 0; }}
#directoryDropPopup {{
    border: 1px solid {t.sidebar_separator};
    border-radius: 8px;
    background: {t.sidebar};
    color: {t.sidebar_text};
    outline: 0;
}}
#directoryDropPopup::item {{ min-height: 30px; padding: 0 8px; }}
#directoryDropPopup::item:hover, #directoryDropPopup::item:selected {{
    background: {t.sidebar_selected}; color: {t.sidebar_text};
}}
#smartTabButton, #categoryManagerButton, #categoryFilter, #aiActorFilterCombo, #noteList, #sidebarComboPopupView {{
    font-family: {UI_FONT_FAMILY}; font-size: 13px;
}}
#smartTabButton, #categoryManagerButton {{ color: {t.sidebar_text_muted}; }}
#smartTabButton:hover, #categoryManagerButton:hover {{ background: {t.sidebar_hover}; color: {t.sidebar_text}; }}
#smartTabButton:pressed, #categoryManagerButton:pressed {{ background: {t.sidebar_selected}; color: {t.sidebar_text}; }}
#smartTabButton[active='true'] {{ background: {t.sidebar_selected}; color: {t.sidebar_text}; font-weight: 600; }}
#smartTabButton:disabled {{ color: {t.sidebar_text_subtle}; background: transparent; }}
#noteList {{ border: 0; outline: 0; background: transparent; color: {t.sidebar_text_muted}; }}
#noteList QWidget {{ background: transparent; }}
#noteList::item {{ min-height: 32px; max-height: 32px; border-radius: 6px; padding: 0; }}
#noteList::item:hover {{ background: {t.sidebar_hover}; color: {t.sidebar_text}; }}
#noteList::item:selected {{ background: {t.sidebar_selected}; color: {t.sidebar_text}; }}
#trashTree {{ border: 0; outline: 0; background: transparent; color: {t.sidebar_text_muted}; }}
#trashTree::item {{ min-height: 30px; border-radius: 6px; padding: 0 8px; color: {t.sidebar_text_muted}; }}
#trashTree::item:hover {{ background: {t.sidebar_hover}; color: {t.sidebar_text}; }}
#trashTree::item:selected {{ background: {t.sidebar_selected}; color: {t.sidebar_text}; }}
QPushButton, QToolButton {{ border: 1px solid transparent; border-radius: 6px; padding: 4px 8px; background: transparent; color: {t.text}; }}
QPushButton:hover, QToolButton:hover {{ background: {t.surface_hover}; }}
QPushButton:pressed, QToolButton:pressed, QToolButton:checked {{ background: {t.surface_selected}; }}
QPushButton:focus, QToolButton:focus {{ border-color: {t.focus}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
#newNoteButton {{ background: {t.accent}; color: {t.accent_text}; padding: 0; }}
#newNoteButton:hover {{ background: {t.focus}; }}
#newNoteButton:pressed {{ background: {t.accent_soft}; color: {t.accent}; }}
#trashButton {{ color: {t.sidebar_text_muted}; padding: 0; }}
#trashButton:hover {{ background: {t.sidebar_hover}; color: {t.sidebar_text}; }}
#trashButton:pressed {{ background: {t.sidebar_selected}; color: {t.sidebar_text}; }}
#themeSwatch {{ border: 1px solid transparent; border-radius: 7px; padding: 0; }}
#themeSwatch[themeName='dark'] {{ background: {THEME_SWATCH_COLORS['dark']}; }}
#themeSwatch[themeName='light-slate'] {{ background: {THEME_SWATCH_COLORS['light-slate']}; }}
#themeSwatch[themeName='light-clay'] {{ background: {THEME_SWATCH_COLORS['light-clay']}; }}
#themeSwatch:hover {{ border-color: {t.sidebar_text_subtle}; }}
#themeSwatch:pressed {{ border-color: {t.sidebar_text_muted}; }}
#themeSwatch[active='true'] {{ border-width: 1px; border-radius: 8px; }}
#themeSwatch[active='true'][themeName='dark'] {{ border-color: {THEME_SWATCH_RING_COLORS['dark']}; }}
#themeSwatch[active='true'][themeName='light-slate'] {{ border-color: {THEME_SWATCH_RING_COLORS['light-slate']}; }}
#themeSwatch[active='true'][themeName='light-clay'] {{ border-color: {THEME_SWATCH_RING_COLORS['light-clay']}; }}
#themeButton {{ color: {t.sidebar_text_muted}; padding: 4px; }}
#themeButton:hover {{ background: {t.sidebar_hover}; color: {t.sidebar_text}; }}
#themeButton[active='true'] {{ background: {t.sidebar_selected}; color: {t.sidebar_text}; }}
#mainContentArea, #editorPane {{ background: {t.editor}; }}
#titleInput {{ border: 0; border-radius: 0; padding: 0; background: {t.editor}; color: {t.text}; font-size: 26px; font-weight: 600; }}
#titleInput:focus {{ border: 0; background: {t.editor}; }}
#titleSeparator {{ border: 0; min-height: 2px; max-height: 2px; background: {t.title_separator}; }}
#metadataSummaryButton {{
    border: 0; border-radius: 0;
    padding: 8px 0; background: {t.editor}; color: {t.text_muted};
    font-size: 12.5px; text-align: left;
}}
#metadataSummaryButton:hover, #metadataSummaryButton:focus {{ background: {t.editor}; color: {t.text}; }}
#metadataSummaryButton:pressed {{ background: {t.surface}; }}
#metadataPanel {{ background: {t.surface}; color: {t.text}; border-radius: 8px; }}
#metadataFieldLabel {{ color: {t.text_muted}; font-size: 12.5px; }}
#metadataPanel QComboBox, #metadataPanel QLineEdit {{
    background: {t.editor_paper}; color: {t.text}; placeholder-text-color: {t.text_subtle};
}}
#metadataPanel QComboBox:disabled, #metadataPanel QLineEdit:disabled {{
    background: {t.editor_paper}; color: {t.disabled};
}}
#metadataCollapseButton {{ color: {t.text_muted}; padding: 0; }}
#metadataCollapseButton:hover {{ background: {t.surface_hover}; color: {t.text}; }}
#actionBarButton, #deleteNoteButton {{ padding: 0; color: {t.text_muted}; }}
#actionBarButton:hover, #deleteNoteButton:hover {{ background: {t.surface_hover}; color: {t.text}; }}
#actionBarButton:pressed, #deleteNoteButton:pressed,
#actionBarButton[active='true'] {{ background: {t.surface_selected}; color: {t.text}; }}
#linkDrawer {{
    border-left: 1px solid {t.sidebar_separator};
    background: {t.sidebar};
    color: {t.sidebar_text};
}}
#linkDrawerCloseButton {{
    background: transparent; color: {t.sidebar_text_muted}; text-align: left;
}}
#linkDrawerCloseButton:hover {{ background: {t.sidebar_hover}; color: {t.sidebar_text}; }}
#linkDrawerCloseButton:pressed {{ background: {t.sidebar_selected}; color: {t.sidebar_text}; }}
#linkDrawerSummary {{ color: {t.sidebar_text_muted}; font-size: 12.5px; }}
#linkResults {{
    border: 0; outline: 0; background: {t.sidebar}; color: {t.sidebar_text};
    selection-background-color: {t.sidebar_selected};
    selection-color: {t.sidebar_text};
}}
#linkResults::item {{
    min-height: 42px; border: 0; border-radius: 6px; padding: 6px 8px;
    background: transparent; color: {t.sidebar_text_muted};
}}
#linkResults::item:hover {{ background: {t.sidebar_hover}; color: {t.sidebar_text}; }}
#linkResults::item:selected {{ background: {t.sidebar_selected}; color: {t.sidebar_text}; }}
#editorToolbar {{ background: transparent; border: 0; border-radius: 0; }}
#findPanel {{ background: transparent; border: 0; border-radius: 0; }}
#toolbarRow, #toolbarGroup {{ background: transparent; }}
#toolbarSeparator {{ background: {t.editor_border}; margin: 4px 0; }}
#toolbarButton {{ padding: 4px; color: {t.text_muted}; }}
#toolbarButton:hover {{ background: {t.surface_hover}; color: {t.text}; }}
#toolbarButton:checked, #toolbarButton:pressed {{ background: {t.surface_selected}; color: {t.text}; }}
#toolbarInput {{ border: 1px solid {t.editor_border}; border-radius: 6px; padding: 4px 8px; background: {t.surface}; color: {t.text}; }}
#bodyEdit {{
    border: 1px solid {t.editor_border}; border-radius: 4px; background: transparent;
    padding: 8px; color: {t.text}; font-size: 15px;
    selection-background-color: {t.selection_background}; selection-color: {t.selection_text};
}}
#bodyEdit QWidget {{ background: transparent; }}
#bodyEdit:focus {{ border: 1px solid {t.focus}; }}
#statusLabel, #noteIdLabel {{ background: transparent; color: {t.text_subtle}; padding: 4px 0; font-size: 12.5px; }}
#statusLabel[conflict='true'] {{ color: {t.warning}; }}
#aiCenterDialog {{ background: {t.window}; color: {t.text}; }}
#receiptInboxTitle {{ color: {t.text}; font-size: 15px; font-weight: 600; }}
#aiCenterTabs::pane {{ border: 0; top: 0; background: {t.window}; }}
#aiCenterTabs QTabBar::tab {{
    border: 0; border-radius: 6px; padding: 7px 14px;
    background: transparent; color: {t.text_muted};
}}
#aiCenterTabs QTabBar::tab:hover {{ background: {t.surface_hover}; color: {t.text}; }}
#aiCenterTabs QTabBar::tab:selected {{ background: {t.surface_selected}; color: {t.text}; font-weight: 600; }}
#aiCallLogPage, #aiReminderLogPage {{ background: {t.window}; }}
#aiReminderLogEmpty {{ color: {t.text_muted}; }}
#aiCallLogFilter {{
    border: 1px solid {t.editor_border}; border-radius: 6px;
    padding: 4px 26px 4px 9px; background: {t.surface}; color: {t.text};
}}
#aiCallLogFilter:hover {{ background: {t.surface_hover}; }}
#aiCallLogFilter:focus {{ border-color: {t.focus}; background: {t.surface}; }}
#aiCallLogFilter:disabled {{ color: {t.disabled}; background: {t.surface}; }}
#aiCallLogTable, #aiReminderLogTable {{
    border: 0; border-radius: 8px; background: {t.surface}; color: {t.text};
    alternate-background-color: {t.surface_hover};
    gridline-color: {t.editor_border};
    selection-background-color: {t.surface_selected};
    selection-color: {t.text};
    outline: 0;
}}
#aiCallLogTable QWidget, #aiReminderLogTable QWidget {{
    background: {t.surface};
    color: {t.text};
}}
#aiCallLogTable::item, #aiReminderLogTable::item {{ padding: 5px 7px; }}
#aiCallLogTable::item:selected, #aiReminderLogTable::item:selected {{
    background: {t.surface_selected};
    color: {t.text};
}}
#aiCallLogTable QHeaderView, #aiReminderLogTable QHeaderView {{
    background: {t.surface};
}}
#aiCallLogTable QHeaderView::section, #aiReminderLogTable QHeaderView::section {{
    border: 0; border-bottom: 1px solid {t.editor_border};
    padding: 6px 8px; background: {t.surface}; color: {t.text_muted};
}}
#aiCallLogTable QTableCornerButton::section, #aiReminderLogTable QTableCornerButton::section {{
    border: 0; border-bottom: 1px solid {t.editor_border};
    background: {t.surface};
}}
#reminderCenter {{
    background: {t.editor}; color: {t.text};
}}
#reminderToolbar, #reminderSettings {{
    background: transparent; color: {t.text};
}}
#reminderTableTitleInput {{
    border: 0;
    border-radius: 0;
    padding: 0;
    background: {t.editor};
    color: {t.text};
    font-size: 26px;
    font-weight: 600;
}}
#reminderTableTitleInput:focus {{ border: 0; background: {t.editor}; }}
#reminderTitleSeparator {{
    border: 0;
    min-height: 2px;
    max-height: 2px;
    background: {t.title_separator};
}}
#reminderMetadataChips {{ background: transparent; }}
#reminderTableLabelCombo, #reminderTableAiVisibleCombo {{
    border: 1px solid {t.editor_border};
    border-radius: 11px;
    padding: 2px 25px 2px 9px;
    min-height: 20px;
    max-height: 20px;
    background: {t.surface};
    color: {t.text_muted};
    font-size: 12.5px;
}}
#reminderTableLabelCombo:hover, #reminderTableAiVisibleCombo:hover {{
    background: {t.surface_hover};
    color: {t.text};
}}
#reminderTableLabelCombo:focus, #reminderTableAiVisibleCombo:focus {{
    border-color: {t.focus};
    background: {t.surface};
}}
#reminderTableLabelCombo:disabled, #reminderTableAiVisibleCombo:disabled {{
    border-color: transparent;
    background: {t.editor};
    color: {t.disabled};
}}
#reminderSettingsHint, #reminderSettingsLabel {{
    color: {t.text_muted}; font-size: 12.5px;
}}
#reminderTable {{
    border: 1px solid {t.editor_border};
    border-radius: 8px;
    background: {t.surface};
    alternate-background-color: {t.surface_hover};
    color: {t.text};
    gridline-color: {t.editor_border};
    selection-background-color: {t.accent_soft};
    selection-color: {t.text};
    outline: 0;
}}
#reminderTable QWidget {{
    background: {t.surface};
    color: {t.text};
}}
#reminderTable::item {{
    padding: 6px 8px;
    border: 0;
}}
#reminderTable::item:hover {{
    background: {t.surface_hover};
    color: {t.text};
}}
#reminderTable::item:selected {{
    background: {t.accent_soft};
    color: {t.text};
    border-left: 3px solid {t.accent};
}}
#reminderTable QHeaderView {{
    background: {t.surface};
}}
#reminderTable QHeaderView::section {{
    border: 0;
    border-bottom: 1px solid {t.editor_border};
    border-right: 1px solid {t.editor_border};
    padding: 7px 8px;
    background: {t.surface};
    color: {t.text_muted};
    font-weight: 600;
}}
#reminderTable QTableCornerButton::section {{
    border: 0;
    border-bottom: 1px solid {t.editor_border};
    background: {t.surface};
}}
#reminderDeleteTableButton {{
    padding: 0;
    color: {t.text_muted};
}}
#reminderDeleteTableButton:hover {{
    background: {t.surface_hover};
    color: {t.text};
}}
#reminderDeleteTableButton:pressed {{
    background: {t.surface_selected};
    color: {t.text};
}}
#reminderDialog {{
    background: {t.window}; color: {t.text};
}}
#reminderDialog QFormLayout QLabel, #reminderDialogFieldLabel {{
    color: {t.text_muted};
}}
#reminderDateEdit, #reminderTimeEdit, #reminderTopicInput, #reminderDialog QLineEdit,
#reminderDialog QTextEdit, #reminderDialog QComboBox, #reminderDialog QSpinBox {{
    border: 1px solid {t.editor_border};
    border-radius: 6px;
    background: {t.surface};
    color: {t.text};
    selection-background-color: {t.selection_background};
    selection-color: {t.selection_text};
}}
#reminderDateEdit:hover, #reminderTimeEdit:hover, #reminderTopicInput:hover,
#reminderDialog QLineEdit:hover, #reminderDialog QTextEdit:hover,
#reminderDialog QComboBox:hover, #reminderDialog QSpinBox:hover {{
    background: {t.surface_hover};
}}
#reminderDateEdit:focus, #reminderTimeEdit:focus, #reminderTopicInput:focus,
#reminderDialog QLineEdit:focus, #reminderDialog QTextEdit:focus,
#reminderDialog QComboBox:focus, #reminderDialog QSpinBox:focus {{
    border-color: {t.focus}; background: {t.surface};
}}
#reminderCalendar {{
    border: 1px solid {t.editor_border};
    border-radius: 8px;
    background: {t.surface};
    color: {t.text};
}}
#reminderCalendar QWidget, #reminderCalendar QWidget#qt_calendar_navigationbar {{
    background: {t.surface};
    color: {t.text};
}}
#reminderCalendar QToolButton,
#reminderCalendar QToolButton#qt_calendar_prevmonth,
#reminderCalendar QToolButton#qt_calendar_nextmonth,
#reminderCalendar QToolButton#qt_calendar_monthbutton,
#reminderCalendar QToolButton#qt_calendar_yearbutton {{
    color: {t.text};
    background: transparent;
    border: 0;
    border-radius: 6px;
    padding: 4px 8px;
}}
#reminderCalendar QToolButton:hover,
#reminderCalendar QToolButton#qt_calendar_prevmonth:hover,
#reminderCalendar QToolButton#qt_calendar_nextmonth:hover,
#reminderCalendar QToolButton#qt_calendar_monthbutton:hover,
#reminderCalendar QToolButton#qt_calendar_yearbutton:hover {{
    background: {t.surface_hover};
}}
#reminderCalendar QSpinBox#qt_calendar_yearedit {{
    border: 1px solid {t.editor_border};
    border-radius: 6px;
    background: {t.surface};
    color: {t.text};
    selection-background-color: {t.selection_background};
    selection-color: {t.selection_text};
}}
#reminderCalendar QMenu, #reminderCalendarMonthMenu {{
    border: 1px solid {t.editor_border};
    border-radius: 8px;
    background: {t.surface};
    color: {t.text};
    padding: 4px;
}}
#reminderCalendar QMenu::item, #reminderCalendarMonthMenu::item {{
    min-height: 26px;
    padding: 4px 18px;
    border-radius: 6px;
    background: transparent;
    color: {t.text};
}}
#reminderCalendar QMenu::item:selected, #reminderCalendarMonthMenu::item:selected {{
    background: {t.surface_hover};
    color: {t.text};
}}
#reminderCalendar QMenu::item:disabled, #reminderCalendarMonthMenu::item:disabled {{
    color: {t.disabled};
}}
#reminderCalendar QAbstractItemView, #reminderCalendarView {{
    border: 0;
    outline: 0;
    background: {t.surface};
    color: {t.text};
    selection-background-color: {t.surface_selected};
    selection-color: {t.text};
}}
#reminderCalendar QAbstractItemView:item, #reminderCalendarView::item {{
    background: {t.surface};
    color: {t.text};
}}
#reminderCalendar QAbstractItemView:item:hover, #reminderCalendarView::item:hover {{
    background: {t.surface_hover};
    color: {t.text};
}}
#reminderCalendar QAbstractItemView:item:selected, #reminderCalendarView::item:selected {{
    background: {t.surface_selected};
    color: {t.text};
}}
#reminderCalendar QAbstractItemView:item:disabled, #reminderCalendarView::item:disabled {{
    color: {t.disabled};
    background: {t.surface};
}}
#exportDialog {{ background: {t.window}; }}
#aiSafeExportSection {{
    border: 1px solid {t.editor_border}; border-radius: 8px;
    background: {t.surface};
}}
#exportSectionTitle {{ color: {t.text}; font-weight: 600; }}
#receiptTabButton {{ color: {t.text_muted}; padding: 4px 8px; }}
#receiptTabButton:hover {{ background: {t.surface_hover}; color: {t.text}; }}
#receiptTabButton[active='true'] {{ background: {t.surface_selected}; color: {t.text}; font-weight: 600; }}
#receiptEmptyState {{ color: {t.text_subtle}; font-size: 13px; }}
#receiptScroll, #receiptScrollContent {{ border: 0; background: {t.window}; }}
#receiptCard {{ border: 0; border-radius: 8px; background: {t.surface}; }}
#receiptCard[expanded='true'], #receiptCard:hover {{ background: {t.surface_hover}; }}
#receiptCard:focus {{ border: 1px solid {t.focus}; }}
#receiptCardTitle {{ color: {t.text}; font-size: 13px; font-weight: 600; }}
#receiptCardSummary, #receiptFieldLabel {{ color: {t.text_muted}; font-size: 11px; }}
#receiptParseErrorDetail {{ color: {t.warning}; font-size: 11px; }}
#receiptCardDetails {{ background: transparent; }}
#receiptAcceptButton {{ background: {t.accent}; color: {t.accent_text}; padding: 4px 8px; }}
#receiptAcceptButton:hover {{ background: {t.focus}; }}
#receiptRejectButton, #aiCenterCloseButton {{ color: {t.text_muted}; background: transparent; padding: 4px 8px; }}
#receiptRejectButton:hover, #aiCenterCloseButton:hover {{ background: {t.surface_hover}; color: {t.text}; }}
#receiptLinkedNote {{ color: {t.accent}; font-size: 12px; }}
#categoryManagerDialog {{ background: {t.window}; color: {t.text}; }}
#categoryColumn {{ background: {t.surface}; border-radius: 8px; }}
#categoryManagerTitle {{ color: {t.text_muted}; font-size: 12.5px; font-weight: 600; }}
#categoryManagerList {{ border: 0; border-radius: 6px; background: transparent; color: {t.text}; outline: 0; }}
#categoryManagerList::item {{ padding: 0 8px; }}
#categoryManagerList::item:hover {{ background: {t.surface_hover}; }}
#categoryManagerList::item:selected {{ background: {t.surface_selected}; color: {t.text}; }}
#categoryNewButton {{ color: {t.text_muted}; text-align: left; padding: 4px 8px; }}
#categoryNewButton:hover {{ background: {t.surface_hover}; color: {t.text}; }}
#reminderLabelManagerDialog {{ background: {t.window}; color: {t.text}; }}
#reminderLabelManagerList {{
    border: 0;
    border-radius: 8px;
    background: {t.surface};
    color: {t.text};
    outline: 0;
}}
#reminderLabelManagerList::item {{
    min-height: 30px;
    padding: 4px 8px;
    border-radius: 6px;
}}
#reminderLabelManagerList::item:hover {{ background: {t.surface_hover}; }}
#reminderLabelManagerList::item:selected {{
    background: {t.surface_selected};
    color: {t.text};
}}
#reminderLabelNameInput {{
    border: 1px solid {t.editor_border};
    border-radius: 6px;
    padding: 5px 8px;
    background: {t.surface};
    color: {t.text};
}}
#reminderLabelNameInput:hover {{ background: {t.surface_hover}; }}
#reminderLabelNameInput:focus {{ border-color: {t.focus}; background: {t.surface}; }}
#reminderLabelManagerStatus {{ color: {t.text_muted}; font-size: 12.5px; }}
QMenu {{ background-color: {t.sidebar}; color: {t.sidebar_text}; border: 1px solid {t.sidebar_separator}; border-radius: 10px; padding: 8px; }}
QMenu::item {{ background-color: transparent; color: {t.sidebar_text}; border-radius: 6px; padding: 8px 24px 8px 12px; }}
QMenu::item:disabled {{ color: {t.sidebar_text_subtle}; }}
QMenu::item:selected:enabled {{ background-color: {t.sidebar_selected}; color: {t.sidebar_text}; }}
QMenu::separator {{ height: 1px; background-color: {t.sidebar_separator}; margin: 4px 8px; }}
QScrollBar:vertical {{ width: 8px; background: transparent; }}
QScrollBar::handle:vertical {{ min-height: 24px; margin: 0 1px; border-radius: 3px; background: {t.scrollbar}; }}
QScrollBar::handle:vertical:disabled {{ background: transparent; }}
QScrollBar[scrollable='false']::handle:vertical {{ background: transparent; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ height: 8px; background: transparent; }}
QScrollBar::handle:horizontal {{ min-width: 24px; margin: 1px 0; border-radius: 3px; background: {t.scrollbar}; }}
QScrollBar::handle:horizontal:disabled {{ background: transparent; }}
QScrollBar[scrollable='false']::handle:horizontal {{ background: transparent; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
"""


def apply_theme(app: QApplication, name: str) -> ThemeTokens:
    tokens = set_current_theme(name)
    app.setStyleSheet(build_app_style(tokens.name))
    apply_selection_palette(app, tokens)
    return tokens


def apply_selection_palette(widget, tokens: ThemeTokens | None = None) -> None:
    """Keep active and inactive text selections inside the current theme."""

    tokens = tokens or current_theme()
    palette = QPalette(widget.palette())
    for group in (QPalette.Active, QPalette.Inactive):
        palette.setColor(group, QPalette.Highlight, QColor(tokens.selection_background))
        palette.setColor(group, QPalette.HighlightedText, QColor(tokens.selection_text))
    palette.setColor(QPalette.Disabled, QPalette.Highlight, QColor(tokens.surface_selected))
    palette.setColor(QPalette.Disabled, QPalette.HighlightedText, QColor(tokens.disabled))
    widget.setPalette(palette)


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
