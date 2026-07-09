APP_STYLE = """
QMainWindow {
    background: #fbfbfd;
}
QWidget {
    color: #374151;
    font-size: 14px;
}
QLineEdit {
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    padding: 9px 11px;
    background: #ffffff;
    selection-background-color: #dde8ef;
}
QLineEdit:focus {
    border-color: #8fbc8f;
}
QComboBox {
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    padding: 7px 30px 7px 10px;
    background: #ffffff;
    color: #374151;
}
QComboBox:focus {
    border-color: #8fbc8f;
}
QComboBox::drop-down {
    border: 0;
    width: 26px;
}
QComboBox::down-arrow {
    image: none;
    width: 0;
    height: 0;
}
QComboBox QAbstractItemView {
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    padding: 4px;
    background: #ffffff;
    selection-background-color: #e6efea;
    selection-color: #374151;
}
QPushButton {
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    padding: 8px 12px;
    background: #ffffff;
    color: #374151;
}
QPushButton:hover {
    background: #f7faf8;
    border-color: #d1d5db;
}
#chipButton {
    border: 0;
    border-radius: 10px;
    padding: 6px 9px;
    background: #e6efea;
    color: #4b5563;
    font-size: 12px;
}
#chipButton:hover {
    background: #dde8ef;
}
#trashButton {
    border: 0;
    border-radius: 10px;
    padding: 9px 12px;
    background: #eef2f5;
    color: #6b7280;
    text-align: left;
}
#trashButton:hover {
    background: #e5e7eb;
}
#clearFilterButton {
    border: 0;
    border-radius: 9px;
    padding: 2px 7px;
    background: #eef2f5;
    color: #9ca3af;
    font-size: 13px;
}
#clearFilterButton:hover {
    background: #dde8ef;
    color: #6b7280;
}
QTextEdit {
    border: 1px solid #e5e7eb;
    border-radius: 12px;
    background: #ffffff;
    padding: 14px;
    color: #374151;
    selection-background-color: #dde8ef;
}
QTextEdit:focus {
    border-color: #8fbc8f;
}
QTreeWidget {
    border: 0;
    border-radius: 12px;
    background: #f9fafb;
    padding: 6px;
    outline: 0;
}
QTreeWidget::item {
    border-radius: 8px;
    padding: 7px 8px;
    margin: 1px 0;
    color: #374151;
}
QTreeWidget::item:hover {
    background: #eef2f5;
}
QTreeWidget::item:selected {
    background: #e6efea;
    color: #374151;
}
QMenu {
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    padding: 6px;
    background: #ffffff;
}
QMenu::item {
    border-radius: 7px;
    padding: 7px 24px 7px 12px;
}
QMenu::item:selected {
    background: #e6efea;
}
QMessageBox,
QInputDialog {
    background: #fbfbfd;
}
#sidebar {
    background: #f3f4f6;
}
#editor {
    background: #fbfbfd;
}
#titleInput {
    border: 0;
    border-radius: 12px;
    background: #fbfbfd;
    font-size: 22px;
    font-weight: 600;
    padding: 10px 2px;
}
#metadataLabel,
#statusLabel,
#sectionLabel,
#filterLabel {
    color: #9ca3af;
    font-size: 12px;
}
#filterLabel {
    padding: 2px 2px 0 2px;
}
"""
