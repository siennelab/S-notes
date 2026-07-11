APP_STYLE = """
QMainWindow {
    background: #FAF7F2;
}
QWidget {
    color: #2C2926;
    font-size: 14px;
    font-family: 'PingFang SC', 'SF Pro Text', -apple-system, 'Helvetica Neue', sans-serif;
}
QLineEdit {
    border: 0;
    border-bottom: 1px solid #D6CEC4;
    border-radius: 0;
    padding: 6px 2px;
    background: #FAF7F2;
    color: #2C2926;
    selection-background-color: #F0E6D8;
    placeholder-text-color: #B8AFA6;
}
QLineEdit:hover,
QLineEdit:focus {
    border-bottom-color: #C4956A;
}
QComboBox {
    border: 0;
    border-bottom: 1px solid #D6CEC4;
    border-radius: 0;
    padding: 4px 18px 4px 2px;
    background: #FAF7F2;
    color: #2C2926;
    combobox-popup: 0;
}
QComboBox:hover,
QComboBox:focus {
    border-bottom-color: #C4956A;
}
QComboBox::drop-down {
    border: 0;
    width: 20px;
}
QComboBox::down-arrow {
    image: none;
    width: 0;
    height: 0;
}
QComboBox QAbstractItemView,
QComboBox QListView,
#comboPopupView {
    border: 1px solid #D6CEC4;
    border-radius: 6px;
    background: #FAF7F2;
    color: #2C2926;
    outline: 0;
    selection-background-color: #E8DFD3;
    selection-color: #2C2926;
}
QComboBox QAbstractItemView::item,
QComboBox QListView::item,
#comboPopupView::item {
    min-height: 26px;
    padding: 5px 10px;
    background: #FAF7F2;
    color: #2C2926;
}
QComboBox QAbstractItemView::item:selected,
QComboBox QListView::item:selected,
#comboPopupView::item:selected {
    background: #E8DFD3;
    color: #2C2926;
}
QPushButton {
    border: 0;
    border-radius: 4px;
    padding: 5px 8px;
    background: transparent;
    color: #2C2926;
}
QPushButton:hover {
    background: #E8DFD3;
}
QTextEdit {
    border: 0;
    border-radius: 0;
    background: #FAF7F2;
    padding: 0;
    color: #2C2926;
    font-size: 15px;
    selection-background-color: #F0E6D8;
}
QTextEdit:focus {
    border: 0;
}
QTreeWidget {
    border: 0;
    border-radius: 6px;
    background: #F3EDE4;
    padding: 2px;
    outline: 0;
}
QTreeWidget::item {
    border-left: 3px solid transparent;
    border-radius: 6px;
    padding: 8px 12px;
    margin: 1px 0;
    color: #2C2926;
    font-size: 14px;
}
QTreeWidget::item:hover {
    background: #EDE6DB;
}
QTreeWidget::item:selected {
    border-left: 3px solid #C4956A;
    background: #E8DFD3;
    color: #2C2926;
}
QMenu {
    border: 1px solid #D6CEC4;
    border-radius: 6px;
    padding: 5px;
    background: #FAF7F2;
    color: #2C2926;
}
QMenu::separator {
    height: 1px;
    background: #E0D8CE;
    margin: 5px 6px;
}
QMenu::item {
    border-radius: 4px;
    padding: 6px 22px 6px 10px;
}
QMenu::item:selected {
    background: #E8DFD3;
}
QMessageBox,
QInputDialog {
    background: #FAF7F2;
}
#sidebar {
    background: #F3EDE4;
}
#sidebarTitle {
    color: #2C2926;
    font-size: 26px;
    font-weight: 600;
}
#sidebarSearch, #categoryFilter {
    border: 1px solid #D6CEC4;
    border-radius: 7px;
    background: #FAF7F2;
    padding: 5px 9px;
}
#categoryFilter { padding-right: 18px; }
#sidebarIcon {
    color: #8A8078;
    font-size: 12px;
}
#sidebarIcon:hover {
    color: #2C2926;
}
#sidebarIconButton {
    border: 0;
    border-radius: 6px;
    padding: 0;
    background: transparent;
    color: #8A8078;
    font-size: 13px;
}
#sidebarIconButton:hover {
    background: #E8DFD3;
    color: #2C2926;
}
#sidebarIconButton[active="true"] {
    background: #E8DFD3;
    color: #2C2926;
}
#editor,
#editorSheet {
    background: #FAF7F2;
}
#titleInput {
    border: 0;
    border-radius: 0;
    background: #FAF7F2;
    color: #2C2926;
    font-size: 26px;
    font-weight: 600;
    padding: 0 0 8px 0;
}
#metadataSummaryButton {
    border: 0;
    border-radius: 0;
    padding: 0 0 16px 0;
    background: #FAF7F2;
    color: #8A8078;
    font-size: 13px;
    text-align: left;
}
#metadataSummaryButton:hover {
    background: #FAF7F2;
    color: #2C2926;
}
#metadataPanel {
    background: #F0E6D8;
    border-radius: 8px;
    max-height: 175px;
}
#metadataFieldLabel {
    color: #8A8078;
    font-size: 12px;
    padding-top: 0;
}
#metadataPanel QComboBox,
#metadataPanel QLineEdit {
    min-height: 24px;
    max-height: 26px;
    background: #FAF7F2;
    padding-top: 2px;
    padding-bottom: 2px;
}
#metadataPanel #metadataLabel {
    min-height: 28px;
    max-height: 32px;
    padding-top: 6px;
}
#aiTargetCheck {
    color: #6B635B;
    font-size: 12px;
    spacing: 6px;
    padding-top: 1px;
    padding-bottom: 1px;
}
#aiTargetCheck::indicator {
    width: 12px;
    height: 12px;
}
#metadataCollapseButton {
    border: 0;
    border-radius: 4px;
    padding: 1px 6px;
    background: transparent;
    color: #8A8078;
    font-size: 12px;
}
#metadataCollapseButton:hover {
    background: #E8DFD3;
    color: #2C2926;
}
#editorToolbar,
#findPanel {
    background: #F3EDE4;
    border-radius: 6px;
}
#editorToolbar {
    padding: 2px 4px;
}
#toolbarButton {
    border: 0;
    border-radius: 4px;
    padding: 3px 6px;
    background: transparent;
    color: #6B635B;
    font-size: 12px;
}
#toolbarButton:hover {
    background: #E8DFD3;
}
#toolbarButton::menu-indicator {
    image: none;
    width: 0;
}
#toolbarSeparator {
    color: #D6CEC4;
    background: #D6CEC4;
    min-height: 16px;
    max-height: 16px;
    max-width: 1px;
}
#toolbarInput {
    border: 0;
    border-bottom: 1px solid #D6CEC4;
    border-radius: 0;
    padding: 4px 2px;
    background: #F3EDE4;
    color: #2C2926;
    font-size: 12px;
    min-width: 80px;
    placeholder-text-color: #B8AFA6;
}
#toolbarInput:focus {
    border-bottom-color: #C4956A;
}
#smartTabButton {
    border: 0;
    border-radius: 6px;
    padding: 4px 8px;
    background: transparent;
    color: #8A8078;
    font-size: 11px;
}
#smartTabButton:hover {
    background: #E8DFD3;
}
#smartTabButton[active="true"] {
    background: #E8DFD3;
    color: #2C2926;
}
#noteTable {
    border: 0;
    background: #F3EDE4;
    alternate-background-color: #F3EDE4;
    outline: 0;
}
#noteTable::item {
    border: 0;
    padding: 5px 6px;
}
#noteTable::item:hover { background: #EDE6DB; }
#noteTable::item:selected { background: #E8DFD3; color: #2C2926; }
#categoryManagerButton {
    border: 0;
    border-radius: 6px;
    color: #6B635B;
    padding: 4px 6px;
}
#categoryManagerButton:hover { background: #E8DFD3; }
#deleteNoteButton {
    border: 0;
    border-radius: 6px;
    color: #6B635B;
    padding: 4px 7px;
    background: transparent;
}
#deleteNoteButton:hover { background: #E8DFD3; }
#deleteNoteButton:disabled { color: #B8AFA6; background: transparent; }
#categoryManagerDialog { background: #FAF7F2; }
#categoryManagerTitle { color: #6B635B; font-size: 12px; }
#categoryColumn {
    background: #F3EDE4;
    border-radius: 8px;
}
#categoryManagerList {
    border: 0;
    border-radius: 5px;
    background: transparent;
    outline: 0;
}
#categoryManagerList::item { padding: 7px 8px; }
#categoryManagerList::item:hover { background: #EDE6DB; }
#categoryManagerList::item:selected { background: #E8DFD3; color: #2C2926; }
#categoryNewButton {
    border: 0;
    border-radius: 5px;
    color: #6B635B;
    text-align: left;
    padding: 6px 7px;
}
#categoryNewButton:hover { background: #E8DFD3; }
#categoryNewButton:disabled { color: #B8AFA6; background: transparent; }
#categoryManagerClose { padding: 5px 12px; }
#noteTable QHeaderView::section {
    border: 0;
    border-bottom: 1px solid #D6CEC4;
    background: #F3EDE4;
    color: #8A8078;
    padding: 6px;
    font-size: 12px;
    font-weight: 500;
}
#iconButton {
    border: 0;
    border-radius: 6px;
    padding: 5px;
    background: transparent;
}
#iconButton:hover, #iconButton[active="true"] { background: #E8DFD3; }
#iconButton:disabled { background: transparent; }
#editorEmptyState { color: #8A8078; font-size: 15px; }
QAbstractScrollArea::corner { background: transparent; }
QScrollBar:vertical {
    border: 0;
    background: transparent;
    width: 7px;
    margin: 0;
}
QScrollBar:horizontal {
    border: 0;
    background: transparent;
    height: 7px;
    margin: 0;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #D6CEC4;
    border-radius: 3px;
    min-height: 24px;
    min-width: 24px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background: #8A8078; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: transparent; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
#addNoteButton {
    border: 0;
    border-radius: 6px;
    padding: 0;
    background: transparent;
    color: #8A8078;
    font-size: 15px;
}
#addNoteButton:hover {
    background: #E8DFD3;
    color: #2C2926;
}
#tagChipButton {
    border: 0;
    border-radius: 6px;
    padding: 2px 7px;
    background: #E8DFD3;
    color: #8A8078;
    font-size: 11px;
}
#tagChipButton:hover {
    background: #F0E6D8;
    color: #2C2926;
}
#tagMoreButton {
    border: 0;
    border-radius: 6px;
    padding: 2px 7px;
    background: #F0E6D8;
    color: #8A8078;
    font-size: 11px;
}
#tagMoreButton:hover {
    background: #E8DFD3;
    color: #2C2926;
}
#tagMoreButton::menu-indicator {
    image: none;
    width: 0;
}
#trashDivider {
    background: #E0D8CE;
    max-height: 1px;
}
#trashButton {
    border: 0;
    border-radius: 6px;
    padding: 8px 12px;
    background: transparent;
    color: #8A8078;
    text-align: left;
}
#trashButton:hover {
    background: #EDE6DB;
    color: #2C2926;
}
#trashCloseButton {
    border: 0;
    border-radius: 6px;
    padding: 0;
    color: #8A8078;
    background: transparent;
    font-size: 15px;
}
#trashCloseButton:hover { background: #E8DFD3; color: #2C2926; }
#trashTree {
    border: 0;
    outline: 0;
    padding: 0;
}
#trashTree::item {
    border: 0;
    border-left: 0;
    margin: 1px 0;
    padding: 7px 8px;
}
#trashTree::item:selected { border: 0; background: #E8DFD3; color: #2C2926; }
#trashTree::branch { background: transparent; border: 0; }
#clearFilterButton {
    border: 0;
    border-radius: 4px;
    padding: 1px 6px;
    background: transparent;
    color: #8A8078;
    font-size: 13px;
}
#clearFilterButton:hover {
    background: #E8DFD3;
    color: #2C2926;
}
#metadataLabel,
#statusLabel,
#sectionLabel,
#filterLabel {
    color: #8A8078;
    font-size: 12px;
}
#sectionLabel {
    font-size: 12px;
}
#statusLabel {
    background: transparent;
    color: #8A8078;
    padding: 4px 6px;
}
#filterLabel {
    padding: 2px 2px 0 2px;
}
#exportDialog {
    background: #FAF7F2;
}
#exportTitle {
    color: #2C2926;
    font-size: 18px;
    font-weight: 600;
}
#exportDynamicArea {
    background: #F0E6D8;
    border-radius: 8px;
}
#aiSafeExportSection {
    background: #F3EDE4;
    border-radius: 8px;
}
#exportSectionTitle {
    color: #2C2926;
    font-size: 14px;
    font-weight: 600;
}
#exportDialog QPushButton {
    min-height: 30px;
}
#exportInfoLabel {
    color: #8A8078;
    padding: 12px;
}
#exportSelectionTree {
    border: 1px solid #D6CEC4;
    border-radius: 8px;
    background: #FAF7F2;
    color: #2C2926;
    padding: 4px;
}
#exportSelectionTree::item {
    padding: 6px 8px;
}
#exportSelectionTree::item:hover {
    background: #EDE6DB;
}
#linkDrawer {
    background: #F3EDE4;
    border-left: 1px solid #E0D8CE;
}
#linkDrawerTitle {
    color: #2C2926;
    font-size: 15px;
    font-weight: 600;
}
#linkDrawerCloseButton {
    border: 0;
    border-radius: 6px;
    padding: 0;
    background: transparent;
    color: #8A8078;
    font-size: 14px;
}
#linkDrawerCloseButton:hover {
    background: #E8DFD3;
    color: #2C2926;
}
#linkDrawerEmptyState,
#linkEmptyText {
    color: #8A8078;
    font-size: 12px;
}
#linkDrawerSummary {
    color: #8A8078;
    font-size: 12px;
}
#linkDrawerScroll {
    background: #F3EDE4;
}
#linkDrawerScroll QWidget {
    background: #F3EDE4;
}
#linkSectionHeader {
    border: 0;
    border-radius: 4px;
    padding: 6px 4px;
    background: transparent;
    color: #5D554D;
    font-size: 12px;
    font-weight: 600;
    text-align: left;
}
#linkSectionHeader:hover {
    background: #E8DFD3;
}
#linkResult {
    border: 0;
    border-radius: 4px;
    background: transparent;
}
#linkReasonTags { background: transparent; }
#linkReasonTag {
    border-radius: 7px;
    padding: 2px 6px;
    background: #F0E6D8;
    color: #6B635B;
    font-size: 11px;
}
#linkReasonTag[important="true"] {
    background: #C4956A;
    color: #FFFFFF;
}
#linkResult:hover {
    background: #EDE6DB;
}
#linkResultTitle {
    color: #2C2926;
    font-size: 13px;
    font-weight: 600;
}
#linkResultPath,
#linkResultUpdated {
    color: #8A8078;
    font-size: 11px;
}
#receiptInboxDialog { background: #FAF7F2; }
#receiptInboxTitle { color: #2C2926; font-size: 18px; font-weight: 600; }
#receiptTabButton {
    border-radius: 7px;
    color: #8A8078;
    padding: 5px 9px;
}
#receiptTabButton[active="true"] { background: #E8DFD3; color: #2C2926; }
#receiptImportButton { color: #6B635B; padding: 5px 8px; }
#receiptEmptyState { color: #8A8078; font-size: 13px; }
#receiptScroll { border: 0; background: #FAF7F2; }
#receiptScrollContent { background: #FAF7F2; }
#receiptCard {
    border: 1px solid #E0D8CE;
    border-radius: 8px;
    background: #FAF7F2;
}
#receiptCard[expanded="true"] { border-color: #C4956A; background: #F3EDE4; }
#receiptCard:hover { background: #F3EDE4; }
#receiptCardTitle { color: #2C2926; font-size: 14px; font-weight: 600; }
#receiptCardSummary { color: #8A8078; font-size: 11px; }
#receiptFieldLabel { color: #8A8078; font-size: 11px; }
#receiptCardDetails { background: transparent; }
#receiptAcceptButton {
    background: #C4956A;
    color: #FFFFFF;
    padding: 5px 9px;
}
#receiptAcceptButton:hover { background: #6B635B; }
#receiptAcceptButton:pressed { background: #2C2926; }
#receiptAcceptButton:disabled {
    background: #E8DFD3;
    color: #B8AFA6;
}
#receiptRejectButton {
    color: #6B635B;
    background: transparent;
    padding: 5px 8px;
}
#receiptRejectButton:hover { background: #E8DFD3; }
#receiptRejectButton:pressed { background: #D6CEC4; }
"""
