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
    font-size: 14px;
    font-weight: 600;
}
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
    border-radius: 16px;
    padding: 4px 8px;
    background: transparent;
    color: #8A8078;
    font-size: 11px;
}
#smartTabButton:hover {
    background: #E8DFD3;
}
#smartTabButton[active="true"] {
    background: #C4956A;
    color: #FFFFFF;
}
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
    background: #F3EDE4;
    color: #8A8078;
    padding: 4px 6px;
    border-radius: 6px;
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
    border-radius: 6px;
    background: #FAF7F2;
}
#linkResult:hover {
    background: #EDE6DB;
}
#linkResultTitle {
    color: #2C2926;
    font-size: 13px;
    font-weight: 600;
}
#linkResultSubtitle {
    color: #8A8078;
    font-size: 11px;
}
"""
