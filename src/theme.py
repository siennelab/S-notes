from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSettings, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QWidget

from src.app_paths import CONFIG_DIR


@dataclass(frozen=True)
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
        name="dark",
        window="#171819",
        sidebar="#202224",
        sidebar_control="#292C2E",
        sidebar_separator="#292C2E",
        sidebar_hover="#292C2E",
        sidebar_selected="#303438",
        sidebar_text="#E7E9E8",
        sidebar_text_muted="#A4AAA7",
        sidebar_text_subtle="#707774",
        editor="#1B1D1E",
        editor_paper="#222526",
        editor_border="#393D3B",
        title_separator="#76503E",
        surface="#242729",
        surface_hover="#2C3032",
        surface_selected="#32383A",
        text="#E7E9E8",
        text_muted="#A4AAA7",
        text_subtle="#707774",
        accent="#B97855",
        accent_soft="#3D2D27",
        accent_text="#FFF8F3",
        warning="#D8A45B",
        danger="#D87171",
        focus="#CB8A67",
        scrollbar="#515855",
        disabled="#5E6562",
    ),
    "light-slate": ThemeTokens(
        name="light-slate",
        window="#D9DEE3",
        sidebar="#394653",
        sidebar_control="#465563",
        sidebar_separator="#536170",
        sidebar_hover="#465563",
        sidebar_selected="#526170",
        sidebar_text="#F2F5F7",
        sidebar_text_muted="#CAD2D9",
        sidebar_text_subtle="#9EABB5",
        editor="#D9DEE3",
        editor_paper="#F0F2F3",
        editor_border="#C5CBD0",
        title_separator="#B9C0C6",
        surface="#E7EBEE",
        surface_hover="#DDE3E7",
        surface_selected="#D2DBE1",
        text="#23292E",
        text_muted="#68737C",
        text_subtle="#929CA4",
        accent="#5F768B",
        accent_soft="#CAD6DF",
        accent_text="#FFFFFF",
        warning="#9A6A22",
        danger="#A34444",
        focus="#526C83",
        scrollbar="#AEB7BE",
        disabled="#AAB2B8",
    ),
    "light-clay": ThemeTokens(
        name="light-clay",
        window="#E8E6E2",
        sidebar="#F3F2EF",
        sidebar_control="#EAE7E3",
        sidebar_separator="#EAE7E3",
        sidebar_hover="#EAE7E3",
        sidebar_selected="#E2DDD7",
        sidebar_text="#2A2723",
        sidebar_text_muted="#716C65",
        sidebar_text_subtle="#9B958C",
        editor="#E8E3DD",
        editor_paper="#FAF9F7",
        editor_border="#D6CEC5",
        title_separator="#CFC5BA",
        surface="#F7F6F4",
        surface_hover="#EEEAE6",
        surface_selected="#E6E0DA",
        text="#2A2723",
        text_muted="#716C65",
        text_subtle="#9B958C",
        accent="#A84B2F",
        accent_soft="#F0DDD6",
        accent_text="#FFFFFF",
        warning="#966A2B",
        danger="#A23E37",
        focus="#A84B2F",
        scrollbar="#BBB5AD",
        disabled="#AEA8A0",
    ),
}

DEFAULT_THEME = "light-slate"
THEME_SETTINGS_KEY = "theme"
THEME_SETTINGS_PATH = CONFIG_DIR / "ui_settings.ini"
THEME_LABELS = (
    ("dark", "夜间"),
    ("light-slate", "石板"),
    ("light-clay", "陶土"),
)
ICON_DIR = Path(__file__).with_name("icons")
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


def load_saved_theme() -> str:
    settings = QSettings(str(THEME_SETTINGS_PATH), QSettings.IniFormat)
    return normalize_theme_name(settings.value(THEME_SETTINGS_KEY, DEFAULT_THEME))


def save_theme(name: str) -> str:
    theme_name = normalize_theme_name(name)
    THEME_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    settings = QSettings(str(THEME_SETTINGS_PATH), QSettings.IniFormat)
    settings.setValue(THEME_SETTINGS_KEY, theme_name)
    settings.sync()
    return theme_name


def build_app_style(name: str) -> str:
    t = THEMES[normalize_theme_name(name)]
    return f"""
QMainWindow,
QDialog,
QMessageBox,
QInputDialog,
QWidget#receiptInboxDialog,
QWidget#aiCenterDialog {{
    background: {t.window};
    color: {t.text};
}}
QWidget {{
    color: {t.text};
    font-size: 13px;
    font-family: 'PingFang SC', 'SF Pro Text', -apple-system, 'Helvetica Neue', sans-serif;
}}
QLabel {{ background: transparent; }}
QLineEdit,
QTextEdit,
QPlainTextEdit,
QComboBox {{
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 4px 8px;
    background: {t.surface};
    color: {t.text};
    selection-background-color: {t.accent_soft};
    selection-color: {t.text};
    placeholder-text-color: {t.text_subtle};
}}
QLineEdit:hover,
QTextEdit:hover,
QPlainTextEdit:hover,
QComboBox:hover {{ background: {t.surface_hover}; }}
QLineEdit:focus,
QTextEdit:focus,
QPlainTextEdit:focus,
QComboBox:focus {{
    border-color: {t.focus};
    background: {t.surface};
}}
QLineEdit:disabled,
QTextEdit:disabled,
QPlainTextEdit:disabled,
QComboBox:disabled {{ color: {t.disabled}; background: {t.surface}; }}
QComboBox {{ padding-right: 24px; combobox-popup: 0; }}
QComboBox::drop-down {{ border: 0; width: 24px; }}
QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
QPushButton,
QToolButton {{
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 4px 8px;
    background: transparent;
    color: {t.text};
}}
QPushButton:hover,
QToolButton:hover {{ background: {t.surface_hover}; }}
QPushButton:pressed,
QToolButton:pressed,
QPushButton:checked,
QToolButton:checked {{ background: {t.surface_selected}; color: {t.text}; }}
QPushButton:focus,
QToolButton:focus {{ border-color: {t.focus}; }}
QPushButton:disabled,
QToolButton:disabled {{ color: {t.disabled}; background: transparent; }}
QCheckBox {{ spacing: 8px; color: {t.text_muted}; }}
QCheckBox:hover {{ color: {t.text}; }}
QCheckBox:focus {{ color: {t.focus}; }}
QTabWidget::pane {{ border: 0; background: {t.window}; top: -1px; }}
QTabBar::tab {{
    border: 0;
    border-radius: 6px;
    padding: 6px 12px;
    margin-right: 4px;
    background: transparent;
    color: {t.text_muted};
}}
QTabBar::tab:hover {{ background: {t.surface_hover}; color: {t.text}; }}
QTabBar::tab:selected {{ background: {t.surface_selected}; color: {t.text}; font-weight: 600; }}
QMenu {{
    border: 0;
    border-radius: 10px;
    padding: 8px;
    background: {t.sidebar};
    color: {t.sidebar_text};
}}
QMenu::separator {{ height: 4px; background: transparent; margin: 4px 0; }}
QMenu::item {{ border-radius: 6px; padding: 8px 24px 8px 12px; }}
QMenu::item:selected {{ background: {t.sidebar_selected}; color: {t.sidebar_text}; }}
QMenu::item:pressed {{ background: {t.accent_soft}; color: {t.sidebar_text}; }}
QMenu::item:disabled {{ color: {t.disabled}; }}
QMenu::item:focus {{ border: 1px solid {t.focus}; }}
QToolTip {{
    border: 0;
    border-radius: 6px;
    padding: 4px 8px;
    background: {t.sidebar_selected};
    color: {t.sidebar_text};
}}
QAbstractItemView {{
    border: 0;
    outline: 0;
    background: {t.surface};
    color: {t.text};
    selection-background-color: {t.surface_selected};
    selection-color: {t.text};
}}
QAbstractItemView::item {{ min-height: 32px; border: 0; border-radius: 6px; padding: 0 8px; }}
QAbstractItemView::item:hover {{ background: {t.surface_hover}; }}
QAbstractItemView::item:selected {{ background: {t.surface_selected}; color: {t.text}; }}
QAbstractItemView:focus {{ border: 1px solid {t.focus}; border-radius: 6px; }}
QHeaderView {{ border: 0; background: {t.surface}; }}
QHeaderView::section {{
    border: 0;
    background: {t.surface};
    color: {t.text_muted};
    font-size: 11px;
    padding: 4px 8px;
}}

#mainContentArea {{ background: {t.editor}; }}
#sidebar {{ background: {t.sidebar}; }}
#sidebarTitle {{ color: {t.sidebar_text}; font-size: 15px; font-weight: 600; }}
#themeSwitch {{ background: {t.sidebar_control}; border-radius: 8px; }}
#themeButton {{
    color: {t.sidebar_text_muted};
    font-size: 11px;
    padding: 4px 8px;
}}
#themeButton:hover {{ background: {t.sidebar_selected}; color: {t.sidebar_text}; }}
#themeButton:pressed {{ background: {t.accent_soft}; color: {t.sidebar_text}; }}
#themeButton[active="true"] {{ background: {t.sidebar_selected}; color: {t.sidebar_text}; font-weight: 600; }}
#newNoteButton {{ background: {t.accent}; color: {t.accent_text}; padding: 0; }}
#newNoteButton:hover {{ background: {t.focus}; }}
#newNoteButton:pressed {{ background: {t.accent_soft}; color: {t.accent}; }}
#sidebarSearch,
#categoryFilter {{ background: {t.sidebar_control}; color: {t.sidebar_text}; }}
#sidebarSearch:hover,
#categoryFilter:hover {{ background: {t.sidebar_hover}; }}
#sidebarSearch:focus,
#categoryFilter:focus {{ border-color: {t.focus}; background: {t.sidebar_control}; }}
#sidebarSearch QToolButton {{ background: transparent; border: 0; padding: 0; }}
#smartTabButton,
#categoryManagerButton {{ color: {t.sidebar_text_muted}; font-size: 11px; }}
#smartTabButton:hover,
#categoryManagerButton:hover {{ background: {t.sidebar_hover}; color: {t.sidebar_text}; }}
#smartTabButton:pressed,
#categoryManagerButton:pressed {{ background: {t.accent_soft}; color: {t.sidebar_text}; }}
#smartTabButton[active="true"] {{ background: {t.sidebar_selected}; color: {t.sidebar_text}; font-weight: 600; }}
#sidebarComboPopupView,
#comboPopupView {{
    border: 0;
    border-radius: 10px;
    background: {t.sidebar};
    outline: 0;
    padding: 4px;
    selection-background-color: transparent;
}}
#sidebarComboPopupView {{
    color: {t.sidebar_text};
    selection-color: {t.sidebar_text};
}}
#comboPopupView {{
    background: {t.surface};
    color: {t.text};
    selection-color: {t.text};
}}
#sidebarComboPopupView::item,
#comboPopupView::item {{ min-height: 32px; padding: 0; background: transparent; }}
#sidebarComboPopupView::item {{ color: {t.sidebar_text}; }}
#comboPopupView::item {{ color: {t.text}; }}
#sidebarComboPopupView::item:selected {{ background: transparent; color: {t.sidebar_text}; }}
#comboPopupView::item:selected {{ background: transparent; color: {t.text}; }}
#floatingComboPopup {{ background: transparent; border: 0; }}
#mainSplitter::handle {{ background: {t.sidebar_separator}; }}
#mainSplitter::handle:hover {{ background: {t.accent}; }}
#noteTable {{
    border: 0;
    background: {t.sidebar};
    alternate-background-color: {t.sidebar};
    color: {t.sidebar_text_muted};
    outline: 0;
    gridline-color: transparent;
    selection-background-color: {t.sidebar_selected};
    selection-color: {t.sidebar_text};
}}
#noteTable::item {{ border: 0; border-radius: 6px; padding: 0 8px; color: {t.sidebar_text_muted}; }}
#noteTable::item:hover {{ background: {t.sidebar_hover}; color: {t.sidebar_text}; }}
#noteTable::item:selected {{ background: {t.sidebar_selected}; color: {t.sidebar_text}; }}
#trashTitle {{ color: {t.sidebar_text_muted}; font-size: 12.5px; }}
#trashButton {{ color: {t.sidebar_text_muted}; text-align: left; padding: 4px 8px; }}
#trashButton:hover {{ background: {t.sidebar_hover}; color: {t.sidebar_text}; }}
#trashButton:pressed {{ background: {t.sidebar_selected}; }}
#trashCloseButton {{ color: {t.sidebar_text_muted}; padding: 0; }}
#trashCloseButton:hover {{ background: {t.sidebar_hover}; color: {t.sidebar_text}; }}
#trashTree {{ border: 0; outline: 0; padding: 0; background: {t.sidebar}; color: {t.sidebar_text_muted}; }}
#trashTree::item {{ border: 0; margin: 0; padding: 0 8px; color: {t.sidebar_text_muted}; }}
#trashTree::item:hover {{ background: {t.sidebar_hover}; color: {t.sidebar_text}; }}
#trashTree::item:selected {{ background: {t.sidebar_selected}; color: {t.sidebar_text}; }}
#trashTree::branch {{ background: transparent; border: 0; }}

#editor,
#editorSheet {{ background: {t.editor}; }}
#titleInput {{
    border: 0;
    border-radius: 0;
    background: {t.editor};
    color: {t.text};
    font-size: 26px;
    font-weight: 600;
    padding: 0;
}}
#titleInput:focus {{ border: 0; background: {t.editor}; }}
#titleSeparator {{
    border: 0;
    min-height: 1px;
    max-height: 1px;
    background: {t.title_separator};
}}
#metadataSummaryButton {{
    padding: 8px 0;
    background: {t.editor};
    color: {t.text_muted};
    font-size: 12.5px;
    text-align: left;
}}
#metadataSummaryButton:hover {{ background: {t.editor}; color: {t.text}; }}
#metadataSummaryButton:pressed {{ background: {t.surface}; }}
#metadataPanel {{ background: {t.surface}; color: {t.text}; border-radius: 8px; }}
#metadataFieldLabel,
#metadataLabel,
#sectionLabel,
#filterLabel {{ color: {t.text_muted}; font-size: 12.5px; }}
#metadataPanel QComboBox,
#metadataPanel QLineEdit {{ background: {t.editor_paper}; color: {t.text}; }}
#aiTargetCheck {{ color: {t.text_muted}; font-size: 12.5px; spacing: 8px; padding: 0; }}
#metadataCollapseButton {{ color: {t.text_muted}; padding: 0; }}
#metadataCollapseButton:hover {{ background: {t.surface_hover}; color: {t.text}; }}
#editorToolbar,
#findPanel {{ background: {t.surface}; border-radius: 8px; }}
#editorActionBar {{ background: transparent; border-radius: 0; }}
#editorToolbar {{ padding: 4px 8px; }}
#toolbarButton {{ padding: 4px; color: {t.text_muted}; font-size: 12.5px; }}
#toolbarButton:hover {{ background: {t.surface_hover}; color: {t.text}; }}
#toolbarButton:pressed,
#toolbarButton:checked {{ background: {t.surface_selected}; color: {t.text}; }}
#toolbarButton:focus {{ border-color: {t.focus}; }}
#toolbarButton::menu-indicator {{ image: none; width: 0; }}
#toolbarInput {{ background: {t.editor_paper}; color: {t.text}; font-size: 12.5px; min-width: 80px; }}
#bodyEdit {{
    border: 1px solid {t.editor_border};
    border-radius: 3px;
    background: {t.editor_paper};
    padding: 10px 12px;
    color: {t.text};
    font-size: 15px;
}}
#bodyEdit:hover {{ background: {t.editor_paper}; }}
#bodyEdit:focus {{ border: 1px solid {t.editor_border}; background: {t.editor_paper}; }}
#deleteNoteButton,
#actionBarButton {{ padding: 0; color: {t.text_muted}; }}
#deleteNoteButton:hover,
#actionBarButton:hover {{ background: {t.surface_hover}; }}
#deleteNoteButton:pressed,
#actionBarButton:pressed,
#actionBarButton[active="true"] {{ background: {t.surface_selected}; }}
#editorEmptyState {{ color: {t.text_subtle}; font-size: 15px; }}
#statusLabel {{ color: {t.text_subtle}; padding: 4px 0; font-size: 12.5px; }}
#tagChipButton,
#tagMoreButton {{ background: {t.sidebar_hover}; color: {t.text_muted}; font-size: 11px; padding: 4px 8px; }}
#tagChipButton:hover,
#tagMoreButton:hover {{ background: {t.sidebar_selected}; color: {t.text}; }}
#tagMoreButton::menu-indicator {{ image: none; width: 0; }}

#linkDrawer {{ background: {t.sidebar}; }}
#linkDrawerTitle {{ color: {t.sidebar_text}; font-size: 15px; font-weight: 600; }}
#linkDrawerCloseButton {{ color: {t.sidebar_text_muted}; padding: 0; }}
#linkDrawerCloseButton:hover {{ background: {t.sidebar_hover}; color: {t.sidebar_text}; }}
#linkDrawerEmptyState,
#linkEmptyText,
#linkDrawerSummary {{ color: {t.sidebar_text_muted}; font-size: 12.5px; }}
#linkDrawerScroll,
#linkDrawerScroll QWidget {{ background: {t.sidebar}; }}
#linkSectionHeader {{ padding: 4px; background: transparent; color: {t.sidebar_text_muted}; font-size: 12.5px; font-weight: 600; text-align: left; }}
#linkSectionHeader:hover {{ background: {t.sidebar_hover}; }}
#linkResult {{ border: 0; border-radius: 8px; background: {t.sidebar_hover}; }}
#linkResult:hover {{ background: {t.sidebar_selected}; }}
#linkResult[pressed="true"] {{ background: {t.accent_soft}; }}
#linkResult:focus {{ border: 1px solid {t.focus}; }}
#linkResultTitle {{ color: {t.sidebar_text}; font-size: 13px; font-weight: 600; }}
#linkResultPath,
#linkResultUpdated {{ color: {t.sidebar_text_subtle}; font-size: 11px; }}
#linkReasonTags {{ background: transparent; }}
#linkReasonTag {{ border-radius: 6px; padding: 4px 8px; background: {t.sidebar}; color: {t.sidebar_text_muted}; font-size: 11px; }}
#linkReasonTag[important="true"] {{ background: {t.accent_soft}; color: {t.accent}; }}

#categoryManagerDialog,
#exportDialog {{ background: {t.window}; }}
#categoryColumn,
#exportDynamicArea,
#aiSafeExportSection {{ background: {t.surface}; border-radius: 8px; }}
#categoryManagerTitle {{ color: {t.text_muted}; font-size: 12.5px; font-weight: 600; }}
#categoryManagerList {{ border: 0; border-radius: 6px; background: transparent; color: {t.text}; outline: 0; }}
#categoryManagerList::item {{ padding: 0 8px; }}
#categoryManagerList::item:hover {{ background: {t.surface_hover}; }}
#categoryManagerList::item:selected {{ background: {t.surface_selected}; color: {t.text}; }}
#categoryNewButton {{ color: {t.text_muted}; text-align: left; padding: 4px 8px; }}
#categoryNewButton:hover {{ background: {t.surface_hover}; color: {t.text}; }}
#categoryManagerClose {{ padding: 4px 12px; }}
#exportTitle,
#receiptInboxTitle {{ color: {t.text}; font-size: 15px; font-weight: 600; }}
#aiCenterDialog {{ background: {t.window}; color: {t.text}; }}
QTabWidget#aiCenterTabs {{ background: transparent; color: {t.text}; }}
QTabWidget#aiCenterTabs::pane {{
    border: 1px solid {t.sidebar_separator};
    border-radius: 8px;
    background: {t.window};
    top: -1px;
}}
QTabWidget#aiCenterTabs QTabBar {{ background: transparent; }}
QTabWidget#aiCenterTabs QTabBar::tab {{
    border: 0;
    border-radius: 6px;
    margin: 0;
    padding: 7px 0;
    background: transparent;
    color: {t.text_muted};
    text-align: center;
}}
QTabWidget#aiCenterTabs QTabBar::tab:hover {{
    background: {t.surface_hover};
    color: {t.text};
}}
QTabWidget#aiCenterTabs QTabBar::tab:selected {{
    background: {t.surface_selected};
    color: {t.text};
    font-weight: 600;
}}
QTabWidget#aiCenterTabs QTabBar::tab:disabled {{
    background: transparent;
    color: {t.disabled};
}}
#aiCenterCloseButton {{
    border: 1px solid transparent;
    background: transparent;
    color: {t.text_muted};
    padding: 5px 12px;
}}
#aiCenterCloseButton:hover {{ background: {t.surface_hover}; color: {t.text}; }}
#aiCenterCloseButton:pressed {{ background: {t.surface_selected}; color: {t.text}; }}
#aiCenterCloseButton:focus {{ border-color: {t.focus}; color: {t.text}; }}
#aiCenterCloseButton:disabled {{ background: transparent; color: {t.disabled}; }}
#remoteAiAccessGroup {{
    border: 1px solid {t.editor_border};
    border-radius: 6px;
    background: {t.surface};
}}
#settingsSectionTitle {{ color: {t.text}; font-size: 13px; font-weight: 600; }}
#remoteAiAccessNote {{ color: {t.text_subtle}; font-size: 12px; }}
#remoteUpdateNow {{
    border: 1px solid {t.editor_border};
    background: transparent;
    color: {t.text};
    padding: 4px 12px;
}}
#remoteUpdateNow:hover {{ background: {t.surface_hover}; }}
#remoteUpdateNow:pressed {{ background: {t.surface_selected}; }}
#remoteUpdateNow:disabled {{ color: {t.disabled}; background: transparent; }}
#exportSectionTitle {{ color: {t.text}; font-size: 13px; font-weight: 600; }}
#exportInfoLabel {{ color: {t.text_muted}; padding: 12px; }}
#exportSelectionTree {{ border: 0; border-radius: 8px; background: {t.surface}; color: {t.text}; padding: 4px; outline: 0; }}
#exportSelectionTree::item {{ padding: 0 8px; }}
#exportSelectionTree::item:hover {{ background: {t.surface_hover}; }}
#exportSelectionTree::item:selected {{ background: {t.surface_selected}; color: {t.text}; }}
#receiptTabButton {{ color: {t.text_muted}; padding: 4px 8px; }}
#receiptTabButton:hover {{ background: {t.surface_hover}; color: {t.text}; }}
#receiptTabButton:pressed {{ background: {t.accent_soft}; }}
#receiptTabButton[active="true"] {{ background: {t.surface_selected}; color: {t.text}; font-weight: 600; }}
#receiptImportButton {{ color: {t.text_muted}; padding: 4px 8px; }}
#receiptEmptyState {{ color: {t.text_subtle}; font-size: 13px; }}
#receiptScroll,
#receiptScrollContent {{ border: 0; background: {t.window}; }}
#receiptCard {{ border: 0; border-radius: 8px; background: {t.surface}; }}
#receiptCard[expanded="true"],
#receiptCard:hover {{ background: {t.surface_hover}; }}
#receiptCard[pressed="true"] {{ background: {t.surface_selected}; }}
#receiptCard:focus {{ border: 1px solid {t.focus}; }}
#receiptCardTitle {{ color: {t.text}; font-size: 13px; font-weight: 600; }}
#receiptCardSummary,
#receiptFieldLabel {{ color: {t.text_muted}; font-size: 11px; }}
#receiptParseErrorStatus,
#receiptParseErrorDetail {{ color: {t.warning}; font-size: 11px; }}
#receiptCardDetails {{ background: transparent; }}
#receiptAcceptButton {{ background: {t.accent}; color: {t.accent_text}; padding: 4px 8px; }}
#receiptAcceptButton:hover {{ background: {t.focus}; }}
#receiptAcceptButton:pressed {{ background: {t.accent_soft}; color: {t.accent}; }}
#receiptRejectButton,
#receiptCloseButton {{ color: {t.text_muted}; background: transparent; padding: 4px 8px; }}
#receiptRejectButton:hover,
#receiptCloseButton:hover {{ background: {t.surface_hover}; color: {t.text}; }}

QAbstractScrollArea::corner {{ background: transparent; }}
QScrollBar:vertical {{ border: 0; background: transparent; width: 8px; margin: 0; }}
QScrollBar:horizontal {{ border: 0; background: transparent; height: 8px; margin: 0; }}
QScrollBar::handle:vertical,
QScrollBar::handle:horizontal {{ background: {t.scrollbar}; border-radius: 6px; min-height: 24px; min-width: 24px; }}
QScrollBar::handle:vertical:hover,
QScrollBar::handle:horizontal:hover {{ background: {t.accent}; }}
QScrollBar::handle:vertical:pressed,
QScrollBar::handle:horizontal:pressed {{ background: {t.focus}; }}
QScrollBar::add-line,
QScrollBar::sub-line {{ width: 0; height: 0; background: transparent; }}
QScrollBar::add-page,
QScrollBar::sub-page {{ background: transparent; }}
"""


def editor_document_style(name: str | None = None) -> str:
    return f"""
body {{ font-size: 15px; line-height: 180%; }}
p {{ margin: 0 0 12px 0; }}
h1 {{ font-size: 26px; font-weight: 700; margin: 12px 0 8px 0; }}
h2 {{ font-size: 15px; font-weight: 700; margin: 12px 0 8px 0; }}
h3 {{ font-size: 13px; font-weight: 700; margin: 8px 0 4px 0; }}
blockquote {{ margin-left: 24px; font-style: italic; }}
pre {{ font-family: 'SF Mono', Menlo, Monaco, monospace; font-size: 12.5px; padding: 8px; }}
code {{ font-family: 'SF Mono', Menlo, Monaco, monospace; }}
"""


def apply_theme(app: QApplication, name: str) -> ThemeTokens:
    tokens = set_current_theme(name)
    app.setStyleSheet(build_app_style(tokens.name))
    return tokens


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


@lru_cache(maxsize=256)
def _render_icon(theme_name: str, filename: str, role: str, size: int) -> QIcon:
    tokens = THEMES[theme_name]
    color = getattr(tokens, role)
    source = (ICON_DIR / filename).read_text(encoding="utf-8")
    renderer = QSvgRenderer(QByteArray(source.replace("currentColor", color).encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return QIcon(pixmap)


def themed_icon(filename: str, role: str = "text_muted", size: int = 14) -> QIcon:
    return _render_icon(_current_theme_name, filename, role, size)
