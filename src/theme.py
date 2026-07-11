from pathlib import Path


SIDEBAR_BACKGROUND = "#1C1C1E"
SIDEBAR_SURFACE = "#2C2C2E"
SIDEBAR_TEXT = "#8E8E93"
NOTE_LIST_TEXT = "#A6A6AB"
LINK_DRAWER_BACKGROUND = "#44403C"
SELECTED_BACKGROUND = "#E1D8CC"
SELECTED_TEXT = "#1C1C1E"

EDITOR_BACKGROUND = "#F9F9FB"
EDITOR_TEXT = "#333333"
TOOLBAR_ICON = "#555555"
SUBTLE_BACKGROUND = "#F0F0F0"

DARK_DIVIDER = "#3A3A3C"
LIGHT_DIVIDER = "#E5E5EA"

ICON_DIR = Path(__file__).with_name("icons").as_posix()


APP_STYLE = f"""
QMainWindow,
QDialog,
QMessageBox,
QInputDialog {{
    background: {EDITOR_BACKGROUND};
}}
QWidget {{
    color: {EDITOR_TEXT};
    font-size: 14px;
    font-family: 'PingFang SC', 'SF Pro Text', -apple-system, 'Helvetica Neue', sans-serif;
}}
QLineEdit {{
    border: 0;
    border-bottom: 1px solid {LIGHT_DIVIDER};
    border-radius: 0;
    padding: 6px 2px;
    background: {EDITOR_BACKGROUND};
    color: {EDITOR_TEXT};
    selection-background-color: {SELECTED_BACKGROUND};
    selection-color: {SELECTED_TEXT};
    placeholder-text-color: {SIDEBAR_TEXT};
}}
QLineEdit:hover,
QLineEdit:focus {{ border-bottom-color: {TOOLBAR_ICON}; }}
QComboBox {{
    border: 0;
    border-bottom: 1px solid {LIGHT_DIVIDER};
    border-radius: 0;
    padding: 4px 18px 4px 2px;
    background: {EDITOR_BACKGROUND};
    color: {EDITOR_TEXT};
    combobox-popup: 0;
}}
QComboBox:hover,
QComboBox:focus {{ border-bottom-color: {TOOLBAR_ICON}; }}
QComboBox:disabled {{ color: {SIDEBAR_TEXT}; }}
QComboBox::drop-down {{ border: 0; width: 20px; }}
QComboBox::down-arrow {{
    image: url({ICON_DIR}/combo-chevron.svg);
    width: 12px;
    height: 12px;
}}
#categoryFilter::down-arrow {{ image: url({ICON_DIR}/combo-chevron-sidebar.svg); }}
#floatingComboPopup {{ background: transparent; border: 0; }}
#comboPopupView {{
    border: 1px solid {LIGHT_DIVIDER};
    border-radius: 8px;
    background: {EDITOR_BACKGROUND};
    color: {EDITOR_TEXT};
    outline: 0;
    padding: 0;
    selection-background-color: transparent;
    selection-color: {EDITOR_TEXT};
}}
#comboPopupView::item {{
    min-height: 40px;
    padding: 0;
    background: transparent;
    color: {EDITOR_TEXT};
}}
#comboPopupView::item:selected {{
    background: transparent;
    color: {EDITOR_TEXT};
}}
QPushButton {{
    border: 0;
    border-radius: 4px;
    padding: 5px 8px;
    background: transparent;
    color: {EDITOR_TEXT};
}}
QPushButton:hover {{ background: {LIGHT_DIVIDER}; }}
QPushButton:focus {{ outline: 0; }}
QTextEdit {{
    border: 0;
    border-radius: 0;
    background: {EDITOR_BACKGROUND};
    padding: 0;
    color: {EDITOR_TEXT};
    font-size: 15px;
    selection-background-color: {SELECTED_BACKGROUND};
    selection-color: {SELECTED_TEXT};
}}
QTextEdit:focus {{ border: 0; }}
QMenu {{
    border: 1px solid {DARK_DIVIDER};
    border-radius: 6px;
    padding: 5px;
    background: {SIDEBAR_SURFACE};
    color: {SIDEBAR_TEXT};
}}
QMenu::separator {{
    height: 1px;
    background: {DARK_DIVIDER};
    margin: 5px 6px;
}}
QMenu::item {{ border-radius: 4px; padding: 6px 22px 6px 10px; }}
QMenu::item:selected {{
    background: {SELECTED_BACKGROUND};
    color: {SELECTED_TEXT};
}}
QMenu::item:disabled {{ color: {SIDEBAR_TEXT}; }}
QToolTip {{
    border: 1px solid {DARK_DIVIDER};
    background: {SIDEBAR_SURFACE};
    color: {EDITOR_BACKGROUND};
    padding: 4px 6px;
}}

#sidebar {{
    background: {SIDEBAR_BACKGROUND};
    border-right: 1px solid {DARK_DIVIDER};
}}
#sidebarTitle {{ color: {EDITOR_BACKGROUND}; font-size: 26px; font-weight: 600; }}
#sidebarSearch {{
    border: 1px solid {DARK_DIVIDER};
    border-radius: 7px;
    background: {SIDEBAR_SURFACE};
    color: {SIDEBAR_TEXT};
    padding: 5px 9px;
    selection-background-color: {SELECTED_BACKGROUND};
    selection-color: {SELECTED_TEXT};
    placeholder-text-color: {SIDEBAR_TEXT};
}}
#sidebarSearch:focus {{ border-color: {SIDEBAR_TEXT}; }}
#sidebarSearch QToolButton {{ background: transparent; border: 0; padding: 1px; }}
#categoryFilter {{
    border: 1px solid {DARK_DIVIDER};
    border-radius: 7px;
    background: {SIDEBAR_SURFACE};
    color: {SIDEBAR_TEXT};
    padding: 5px 18px 5px 9px;
}}
#categoryFilter:hover,
#categoryFilter:focus {{ border-color: {SIDEBAR_TEXT}; }}
#categoryFilter:disabled {{ color: {SIDEBAR_TEXT}; background: {SIDEBAR_SURFACE}; }}
#sidebarComboPopupView {{
    border: 1px solid {DARK_DIVIDER};
    border-radius: 8px;
    background: {SIDEBAR_SURFACE};
    color: {SIDEBAR_TEXT};
    outline: 0;
    padding: 0;
    selection-background-color: transparent;
    selection-color: {SIDEBAR_TEXT};
}}
#sidebarComboPopupView::item {{
    min-height: 40px;
    padding: 0;
    background: transparent;
    color: {SIDEBAR_TEXT};
}}
#sidebarComboPopupView::item:selected {{
    background: transparent;
    color: {SIDEBAR_TEXT};
}}
#smartTabButton {{
    border: 0;
    border-radius: 6px;
    padding: 4px 8px;
    background: transparent;
    color: {SIDEBAR_TEXT};
    font-size: 11px;
}}
#smartTabButton:hover {{ background: {SIDEBAR_SURFACE}; color: {EDITOR_BACKGROUND}; }}
#smartTabButton[active="true"] {{ background: {SELECTED_BACKGROUND}; color: {SELECTED_TEXT}; }}
#categoryManagerButton {{
    border: 0;
    border-radius: 6px;
    color: {SIDEBAR_TEXT};
    padding: 4px 6px;
}}
#categoryManagerButton:hover {{ background: {SIDEBAR_SURFACE}; color: {EDITOR_BACKGROUND}; }}
#mainSplitter::handle {{ background: {DARK_DIVIDER}; }}
#mainSplitter::handle:hover {{ background: {SIDEBAR_TEXT}; }}
#noteTable {{
    border: 0;
    background: {SIDEBAR_BACKGROUND};
    alternate-background-color: {SIDEBAR_BACKGROUND};
    color: {NOTE_LIST_TEXT};
    outline: 0;
    gridline-color: transparent;
    selection-background-color: {SELECTED_BACKGROUND};
    selection-color: {SELECTED_TEXT};
}}
#noteTable::item {{ border: 0; padding: 5px 6px; color: {NOTE_LIST_TEXT}; }}
#noteTable::item:hover {{ background: {SIDEBAR_SURFACE}; color: {NOTE_LIST_TEXT}; }}
#noteTable::item:selected,
#noteTable::item:selected:active,
#noteTable::item:selected:!active {{ background: {SELECTED_BACKGROUND}; color: {SELECTED_TEXT}; }}
#noteTable QHeaderView {{ background: {SIDEBAR_BACKGROUND}; }}
#noteTable QHeaderView::section {{
    border: 0;
    border-bottom: 1px solid {DARK_DIVIDER};
    background: {SIDEBAR_BACKGROUND};
    color: {SIDEBAR_TEXT};
    padding: 6px;
    font-size: 12px;
    font-weight: 500;
}}
#trashDivider {{ background: {DARK_DIVIDER}; max-height: 1px; }}
#trashTitle {{ color: {SIDEBAR_TEXT}; }}
#trashButton {{
    border: 0;
    border-radius: 6px;
    padding: 8px 12px;
    background: transparent;
    color: {SIDEBAR_TEXT};
    text-align: left;
}}
#trashButton:hover {{ background: {SIDEBAR_SURFACE}; color: {EDITOR_BACKGROUND}; }}
#trashCloseButton {{
    border: 0;
    border-radius: 6px;
    padding: 0;
    color: {SIDEBAR_TEXT};
    background: transparent;
    font-size: 15px;
    outline: 0;
}}
#trashCloseButton:hover {{ background: {SIDEBAR_SURFACE}; color: {EDITOR_BACKGROUND}; }}
#trashTree {{
    border: 0;
    outline: 0;
    padding: 0;
    background: {SIDEBAR_BACKGROUND};
    color: {SIDEBAR_TEXT};
}}
#trashTree::item {{ border: 0; margin: 1px 0; padding: 7px 8px; color: {SIDEBAR_TEXT}; }}
#trashTree::item:hover {{ background: {SIDEBAR_SURFACE}; }}
#trashTree::item:selected,
#trashTree::item:selected:active,
#trashTree::item:selected:!active {{ background: {SELECTED_BACKGROUND}; color: {SELECTED_TEXT}; }}
#trashTree::branch {{ background: transparent; border: 0; }}

#editor,
#editorSheet {{ background: {EDITOR_BACKGROUND}; }}
#titleInput {{
    border: 0;
    background: {EDITOR_BACKGROUND};
    color: {EDITOR_TEXT};
    font-size: 26px;
    font-weight: 600;
    padding: 0 0 8px 0;
}}
#titleDivider {{
    min-height: 2px;
    max-height: 2px;
    background: {SIDEBAR_BACKGROUND};
    border: 0;
}}
#metadataSummaryButton {{
    border: 0;
    padding: 0 0 16px 0;
    background: {EDITOR_BACKGROUND};
    color: {SIDEBAR_TEXT};
    font-size: 13px;
    text-align: left;
}}
#metadataSummaryButton:hover {{ background: {EDITOR_BACKGROUND}; color: {TOOLBAR_ICON}; }}
#bodyEdit,
#bodyEdit:focus {{
    border: 1px solid {LIGHT_DIVIDER};
    border-radius: 8px;
    background: {EDITOR_BACKGROUND};
}}
#metadataPanel {{
    background: {SUBTLE_BACKGROUND};
    color: {EDITOR_TEXT};
    border-radius: 8px;
    max-height: 175px;
}}
#metadataFieldLabel {{ color: {SIDEBAR_TEXT}; font-size: 12px; padding-top: 0; }}
#metadataPanel QComboBox,
#metadataPanel QLineEdit {{
    min-height: 24px;
    max-height: 26px;
    background: {EDITOR_BACKGROUND};
    color: {EDITOR_TEXT};
    border-bottom-color: {LIGHT_DIVIDER};
    padding-top: 2px;
    padding-bottom: 2px;
}}
#metadataPanel #metadataLabel {{ min-height: 28px; max-height: 32px; padding-top: 6px; }}
#aiTargetCheck {{ color: {TOOLBAR_ICON}; font-size: 12px; spacing: 6px; padding: 1px 0; }}
#aiTargetCheck::indicator {{ width: 12px; height: 12px; }}
#metadataCollapseButton {{ color: {SIDEBAR_TEXT}; font-size: 12px; padding: 1px 6px; }}
#metadataCollapseButton:hover {{ background: {LIGHT_DIVIDER}; color: {EDITOR_TEXT}; }}
#editorToolbar,
#findPanel {{ background: {SUBTLE_BACKGROUND}; border-radius: 6px; }}
#editorToolbar {{ padding: 2px 4px; }}
#toolbarButton {{
    border: 0;
    border-radius: 4px;
    padding: 3px 6px;
    background: transparent;
    color: {TOOLBAR_ICON};
    font-size: 12px;
}}
#toolbarButton:hover {{ background: {LIGHT_DIVIDER}; color: {EDITOR_TEXT}; }}
#toolbarButton::menu-indicator {{ image: none; width: 0; }}
#toolbarSeparator {{
    color: {LIGHT_DIVIDER};
    background: {LIGHT_DIVIDER};
    min-height: 16px;
    max-height: 16px;
    max-width: 1px;
}}
#toolbarInput {{
    border: 0;
    border-bottom: 1px solid {LIGHT_DIVIDER};
    padding: 4px 2px;
    background: {SUBTLE_BACKGROUND};
    color: {EDITOR_TEXT};
    font-size: 12px;
    min-width: 80px;
    placeholder-text-color: {SIDEBAR_TEXT};
}}
#toolbarInput:focus {{ border-bottom-color: {TOOLBAR_ICON}; }}
#deleteNoteButton {{
    border: 0;
    border-radius: 8px;
    padding: 0;
    background: transparent;
    color: {TOOLBAR_ICON};
}}
#deleteNoteButton:hover {{ background: {LIGHT_DIVIDER}; }}
#deleteNoteButton:disabled {{ background: transparent; color: {SIDEBAR_TEXT}; }}
#editorActionBar {{
    background: {SUBTLE_BACKGROUND};
    border: 1px solid {LIGHT_DIVIDER};
    border-radius: 11px;
}}
#actionBarButton {{
    border: 0;
    border-radius: 8px;
    padding: 0;
    background: transparent;
}}
#actionBarButton:hover {{ background: {LIGHT_DIVIDER}; }}
#actionBarButton:pressed {{ background: {SELECTED_BACKGROUND}; }}
#actionBarButton[active="true"] {{ background: {SELECTED_BACKGROUND}; }}
#actionBarButton:disabled {{ background: transparent; }}
#newNoteFab {{
    border: 0;
    border-radius: 22px;
    padding: 0;
    background: {SELECTED_BACKGROUND};
    color: {SELECTED_TEXT};
}}
#newNoteFab:hover {{ background: {LIGHT_DIVIDER}; }}
#newNoteFab:pressed {{ background: {SIDEBAR_TEXT}; }}
#editorEmptyState {{ color: {SIDEBAR_TEXT}; font-size: 15px; }}
#statusLabel {{ background: transparent; color: {SIDEBAR_TEXT}; padding: 4px 6px; font-size: 12px; }}
#metadataLabel,
#sectionLabel,
#filterLabel {{ color: {SIDEBAR_TEXT}; font-size: 12px; }}
#tagChipButton,
#tagMoreButton {{
    border: 0;
    border-radius: 6px;
    padding: 2px 7px;
    background: {SUBTLE_BACKGROUND};
    color: {SIDEBAR_TEXT};
    font-size: 11px;
}}
#tagChipButton:hover,
#tagMoreButton:hover {{ background: {LIGHT_DIVIDER}; color: {EDITOR_TEXT}; }}
#tagMoreButton::menu-indicator {{ image: none; width: 0; }}

#linkDrawer {{
    background: {LINK_DRAWER_BACKGROUND};
    border-left: 1px solid {DARK_DIVIDER};
}}
#linkDrawerTitle {{ color: {EDITOR_BACKGROUND}; font-size: 15px; font-weight: 600; }}
#linkDrawerCloseButton {{
    border: 0;
    border-radius: 6px;
    padding: 0;
    background: transparent;
    color: {SIDEBAR_TEXT};
    font-size: 14px;
    outline: 0;
}}
#linkDrawerCloseButton:hover {{ background: {SIDEBAR_SURFACE}; color: {EDITOR_BACKGROUND}; }}
#linkDrawerEmptyState,
#linkEmptyText,
#linkDrawerSummary {{ color: {SIDEBAR_TEXT}; font-size: 12px; }}
#linkDrawerScroll,
#linkDrawerScroll QWidget {{ background: {LINK_DRAWER_BACKGROUND}; }}
#linkSectionHeader {{
    border: 0;
    border-radius: 4px;
    padding: 6px 4px;
    background: transparent;
    color: {SIDEBAR_TEXT};
    font-size: 12px;
    font-weight: 600;
    text-align: left;
}}
#linkSectionHeader:hover {{ background: {SIDEBAR_SURFACE}; }}
#linkResult {{ border: 0; border-radius: 4px; background: transparent; }}
#linkResult:hover {{ background: {SIDEBAR_SURFACE}; }}
#linkResultTitle {{ color: {EDITOR_BACKGROUND}; font-size: 13px; font-weight: 600; }}
#linkResultPath,
#linkResultUpdated {{ color: {SIDEBAR_TEXT}; font-size: 11px; }}
#linkReasonTags {{ background: transparent; }}
#linkReasonTag {{
    border-radius: 7px;
    padding: 2px 6px;
    background: {SIDEBAR_SURFACE};
    color: {SIDEBAR_TEXT};
    font-size: 11px;
}}
#linkReasonTag[important="true"] {{ background: {SELECTED_BACKGROUND}; color: {SELECTED_TEXT}; }}

#categoryManagerDialog,
#exportDialog,
#receiptInboxDialog {{ background: {EDITOR_BACKGROUND}; }}
#categoryColumn,
#exportDynamicArea,
#aiSafeExportSection {{ background: {SUBTLE_BACKGROUND}; border-radius: 8px; }}
#categoryManagerTitle {{ color: {SIDEBAR_TEXT}; font-size: 12px; }}
#categoryManagerList {{
    border: 0;
    border-radius: 5px;
    background: transparent;
    color: {EDITOR_TEXT};
    outline: 0;
}}
#categoryManagerList::item {{ padding: 7px 8px; }}
#categoryManagerList::item:hover {{ background: {LIGHT_DIVIDER}; }}
#categoryManagerList::item:selected {{ background: {SELECTED_BACKGROUND}; color: {SELECTED_TEXT}; }}
#categoryNewButton {{ color: {TOOLBAR_ICON}; text-align: left; padding: 6px 7px; }}
#categoryNewButton:hover {{ background: {LIGHT_DIVIDER}; }}
#categoryNewButton:disabled {{ color: {SIDEBAR_TEXT}; background: transparent; }}
#categoryManagerClose {{ padding: 5px 12px; }}
#exportTitle,
#receiptInboxTitle {{ color: {EDITOR_TEXT}; font-size: 18px; font-weight: 600; }}
#exportSectionTitle {{ color: {EDITOR_TEXT}; font-size: 14px; font-weight: 600; }}
#exportDialog QPushButton {{ min-height: 30px; }}
#exportInfoLabel {{ color: {SIDEBAR_TEXT}; padding: 12px; }}
#exportSelectionTree {{
    border: 1px solid {LIGHT_DIVIDER};
    border-radius: 8px;
    background: {EDITOR_BACKGROUND};
    color: {EDITOR_TEXT};
    padding: 4px;
    outline: 0;
}}
#exportSelectionTree::item {{ padding: 6px 8px; }}
#exportSelectionTree::item:hover {{ background: {LIGHT_DIVIDER}; }}
#exportSelectionTree::item:selected {{ background: {SELECTED_BACKGROUND}; color: {SELECTED_TEXT}; }}
#receiptTabButton {{ border-radius: 7px; color: {SIDEBAR_TEXT}; padding: 5px 9px; }}
#receiptTabButton:hover {{ background: {LIGHT_DIVIDER}; }}
#receiptTabButton[active="true"] {{ background: {SELECTED_BACKGROUND}; color: {SELECTED_TEXT}; }}
#receiptImportButton {{ color: {TOOLBAR_ICON}; padding: 5px 8px; }}
#receiptEmptyState {{ color: {SIDEBAR_TEXT}; font-size: 13px; }}
#receiptScroll,
#receiptScrollContent {{ border: 0; background: {EDITOR_BACKGROUND}; }}
#receiptCard {{
    border: 1px solid {LIGHT_DIVIDER};
    border-radius: 8px;
    background: {EDITOR_BACKGROUND};
}}
#receiptCard[expanded="true"],
#receiptCard:hover {{ background: {SUBTLE_BACKGROUND}; }}
#receiptCard[expanded="true"] {{ border-color: {SELECTED_BACKGROUND}; }}
#receiptCardTitle {{ color: {EDITOR_TEXT}; font-size: 14px; font-weight: 600; }}
#receiptCardSummary,
#receiptFieldLabel {{ color: {SIDEBAR_TEXT}; font-size: 11px; }}
#receiptCardDetails {{ background: transparent; }}
#receiptAcceptButton {{ background: {SELECTED_BACKGROUND}; color: {SELECTED_TEXT}; padding: 5px 9px; }}
#receiptAcceptButton:hover {{ background: {SIDEBAR_SURFACE}; color: {EDITOR_BACKGROUND}; }}
#receiptAcceptButton:pressed {{ background: {SIDEBAR_BACKGROUND}; color: {EDITOR_BACKGROUND}; }}
#receiptAcceptButton:disabled {{ background: {SUBTLE_BACKGROUND}; color: {SIDEBAR_TEXT}; }}
#receiptRejectButton,
#receiptCloseButton {{ color: {TOOLBAR_ICON}; background: transparent; padding: 5px 8px; }}
#receiptRejectButton:hover,
#receiptCloseButton:hover {{ background: {LIGHT_DIVIDER}; }}

QAbstractScrollArea::corner {{ background: transparent; }}
QScrollBar:vertical {{ border: 0; background: transparent; width: 7px; margin: 0; }}
QScrollBar:horizontal {{ border: 0; background: transparent; height: 7px; margin: 0; }}
QScrollBar::handle:vertical,
QScrollBar::handle:horizontal {{
    background: {SIDEBAR_TEXT};
    border-radius: 3px;
    min-height: 24px;
    min-width: 24px;
}}
QScrollBar::handle:vertical:hover,
QScrollBar::handle:horizontal:hover {{ background: {SELECTED_BACKGROUND}; }}
QScrollBar::add-line,
QScrollBar::sub-line {{ width: 0; height: 0; background: transparent; }}
QScrollBar::add-page,
QScrollBar::sub-page {{ background: transparent; }}
"""
