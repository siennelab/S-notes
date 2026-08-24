"""Frozen-R1 desktop composition over the R2 application services."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QFrame,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.snotes.application.reminder_service import ReminderService
from src.snotes.domain.tags import parse_tags
from src.snotes.ui.editor import NotesEditor
from src.snotes.ui.editor_toolbar import EditorToolbar
from src.snotes.ui.floating_combo import FloatingComboBox
from src.snotes.ui.link_drawer import LinkDrawer
from src.snotes.ui.reminder_center import ReminderCenter
from src.snotes.ui.sidebar import NoteSidebar
from src.snotes.ui.theme import apply_selection_palette, repolish, themed_icon


class SnotesShell(QWidget):
    DEFAULT_SIDEBAR_WIDTH = 280
    MIN_SIDEBAR_WIDTH = 280
    MAX_SIDEBAR_WIDTH = 480
    SIDEBAR_WIDTH_SETTING = "ui/sidebar_width"

    def __init__(
        self,
        theme_settings_path: Path,
        *,
        reminder_service: ReminderService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = QSettings(str(theme_settings_path), QSettings.IniFormat)
        self.metadata_expanded = False
        self.ai_metadata_configured = False
        self.setObjectName("shellRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.sidebar = NoteSidebar()
        self.editor_pane = QWidget()
        self.editor_pane.setObjectName("editorPane")
        self.editor_pane.setAttribute(Qt.WA_StyledBackground, True)
        editor_layout = QVBoxLayout(self.editor_pane)
        editor_layout.setContentsMargins(24, 16, 24, 12)
        editor_layout.setSpacing(0)
        self._build_title(editor_layout)
        self._build_metadata(editor_layout)

        self.body_editor = NotesEditor()
        self.body_editor.setPlaceholderText("写正文")
        self.editor_toolbar = EditorToolbar(self.body_editor)
        editor_layout.addWidget(self.editor_toolbar)
        editor_layout.addSpacing(8)
        editor_layout.addWidget(self.body_editor, stretch=1)
        self.empty_label = QLabel("新建或选择一篇笔记开始编辑")
        self.empty_label.setAlignment(Qt.AlignCenter)
        editor_layout.addWidget(self.empty_label, stretch=1)
        self._build_status(editor_layout)

        self.link_drawer = LinkDrawer()
        self.link_drawer.setVisible(False)
        self.main_content = QWidget()
        self.main_content.setObjectName("mainContentArea")
        content_layout = QHBoxLayout(self.main_content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self.editor_pane, stretch=1)
        content_layout.addWidget(self.link_drawer)

        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self.main_content)
        self.reminder_center = (
            ReminderCenter(reminder_service) if reminder_service is not None else None
        )
        if self.reminder_center is not None:
            self.content_stack.addWidget(self.reminder_center)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setObjectName("mainSplitter")
        self.splitter.setHandleWidth(4)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setOpaqueResize(True)
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(self.content_stack)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([self._stored_sidebar_width(), 1140])
        self.splitter.splitterMoved.connect(self._save_sidebar_width)
        root_layout.addWidget(self.splitter)

    def show_notes_surface(self) -> None:
        self.content_stack.setCurrentWidget(self.main_content)

    def show_reminder_surface(self, *, reset_filters: bool = False) -> None:
        if self.reminder_center is not None:
            if reset_filters:
                self.reminder_center.enter_default()
            else:
                self.reminder_center.refresh()
            self.link_drawer.setVisible(False)
            self.links_button.setProperty("active", False)
            self.repolish_action(self.links_button)
            self.content_stack.setCurrentWidget(self.reminder_center)

    def _build_title(self, layout: QVBoxLayout) -> None:
        self.title_band = QWidget()
        self.title_band.setObjectName("titleBand")
        title_row = QHBoxLayout(self.title_band)
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        self.title_input = QLineEdit()
        self.title_input.setObjectName("titleInput")
        self.title_input.setPlaceholderText("标题")
        self.title_input.setMinimumWidth(0)
        self.title_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        title_row.addWidget(self.title_input, stretch=1, alignment=Qt.AlignBottom)
        self.action_bar = QWidget()
        self.action_bar.setObjectName("editorActionBar")
        self.action_bar.setFixedHeight(36)
        actions = QHBoxLayout(self.action_bar)
        actions.setContentsMargins(4, 4, 4, 4)
        actions.setSpacing(4)
        self.pin_button = self._action_button("pin.svg", "固定笔记")
        self.links_button = self._action_button("link.svg", "打开关联抽屉")
        self.export_button = self._action_button("export.svg", "导出笔记")
        for button in (self.pin_button, self.links_button, self.export_button):
            actions.addWidget(button)
        title_row.addWidget(self.action_bar, alignment=Qt.AlignRight | Qt.AlignBottom)
        layout.addWidget(self.title_band)
        self.title_separator = QFrame()
        self.title_separator.setObjectName("titleSeparator")
        self.title_separator.setFrameShape(QFrame.HLine)
        self.title_separator.setFixedHeight(2)
        layout.addWidget(self.title_separator)

    def _build_metadata(self, layout: QVBoxLayout) -> None:
        self.metadata_section = QWidget()
        metadata_layout = QVBoxLayout(self.metadata_section)
        metadata_layout.setContentsMargins(0, 8, 0, 4)
        metadata_layout.setSpacing(8)
        self.metadata_summary_button = QPushButton()
        self.metadata_summary_button.setObjectName("metadataSummaryButton")
        self.metadata_summary_button.setToolTip("展开或收起笔记信息")
        self.metadata_summary_button.clicked.connect(self.toggle_metadata_panel)
        metadata_layout.addWidget(self.metadata_summary_button)

        self.metadata_panel = QWidget()
        self.metadata_panel.setObjectName("metadataPanel")
        panel_layout = QVBoxLayout(self.metadata_panel)
        panel_layout.setContentsMargins(12, 12, 12, 12)
        panel_layout.setSpacing(8)

        primary_row = QHBoxLayout()
        primary_row.setSpacing(12)
        self.main_combo = FloatingComboBox()
        self.sub_combo = FloatingComboBox()
        self.main_combo.configure_popup(180)
        self.sub_combo.configure_popup(190)
        for label_text, combo in (("主分类", self.main_combo), ("子分类", self.sub_combo)):
            label = QLabel(label_text)
            label.setObjectName("metadataFieldLabel")
            primary_row.addWidget(label)
            combo.setFixedHeight(28)
            combo.setMinimumWidth(0)
            combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            primary_row.addWidget(combo, stretch=1)
        tags_label = QLabel("标签")
        tags_label.setObjectName("metadataFieldLabel")
        primary_row.addWidget(tags_label)
        self.tags_input = QLineEdit()
        self.tags_input.setObjectName("tagsInput")
        self.tags_input.setFixedHeight(28)
        self.tags_input.setPlaceholderText("todo, vps, ai-ok")
        primary_row.addWidget(self.tags_input, stretch=2)
        panel_layout.addLayout(primary_row)

        ai_row = QHBoxLayout()
        ai_row.setSpacing(12)
        visibility_label = QLabel("AI 可见性")
        visibility_label.setObjectName("metadataFieldLabel")
        ai_row.addWidget(visibility_label)
        self.ai_visibility_combo = FloatingComboBox()
        self.ai_visibility_combo.configure_popup(210)
        self.ai_visibility_combo.setFixedSize(180, 28)
        self.ai_visibility_combo.setEnabled(False)
        ai_row.addWidget(self.ai_visibility_combo)
        ai_label = QLabel("AI 对象")
        ai_label.setObjectName("metadataFieldLabel")
        ai_row.addWidget(ai_label)
        self.ai_targets_widget = QWidget()
        self.ai_targets_widget.setObjectName("aiTargetsWidget")
        self.ai_targets_widget.setFixedHeight(28)
        self.ai_targets_widget.setMinimumWidth(0)
        self.ai_targets_layout = QHBoxLayout(self.ai_targets_widget)
        self.ai_targets_layout.setContentsMargins(0, 0, 0, 0)
        self.ai_targets_layout.setSpacing(12)
        self.ai_target_checks: dict[str, QCheckBox] = {}
        self.ai_legacy_targets_label = QLabel()
        self.ai_legacy_targets_label.setObjectName("aiLegacyTargetsLabel")
        self.ai_legacy_targets_label.setVisible(False)
        ai_row.addWidget(self.ai_targets_widget, stretch=1)
        self.metadata_collapse_button = QPushButton()
        self.metadata_collapse_button.setObjectName("metadataCollapseButton")
        self.metadata_collapse_button.setFixedSize(28, 28)
        self.metadata_collapse_button.clicked.connect(lambda: self.set_metadata_expanded(False))
        ai_row.addWidget(self.metadata_collapse_button)
        panel_layout.addLayout(ai_row)
        metadata_layout.addWidget(self.metadata_panel)
        layout.addWidget(self.metadata_section)
        self.main_combo.currentTextChanged.connect(self.update_metadata_summary)
        self.sub_combo.currentTextChanged.connect(self.update_metadata_summary)
        self.tags_input.textChanged.connect(self.update_metadata_summary)
        self.set_metadata_expanded(False)

    def configure_ai_metadata(
        self,
        visibility_options: Sequence[tuple[str, str]],
        target_options: Sequence[tuple[str, str]],
    ) -> None:
        self.ai_visibility_combo.clear()
        for visibility, label in visibility_options:
            self.ai_visibility_combo.addItem(label, visibility)
        for target_id, display_name in target_options:
            checkbox = QCheckBox(display_name)
            checkbox.setObjectName("aiTargetCheck")
            checkbox.setFixedHeight(24)
            checkbox.setProperty("targetId", target_id)
            self.ai_target_checks[target_id] = checkbox
            self.ai_targets_layout.addWidget(checkbox)
        self.ai_targets_layout.addWidget(self.ai_legacy_targets_label)
        self.ai_targets_layout.addStretch(1)
        self.ai_metadata_configured = True
        self.ai_visibility_combo.setEnabled(True)
        for checkbox in self.ai_target_checks.values():
            checkbox.setEnabled(True)
        self.clear_ai_metadata_values()

    def set_ai_metadata_values(
        self,
        visibility: str,
        target_ids: Sequence[str],
        legacy_target_names: Sequence[str] = (),
    ) -> None:
        index = self.ai_visibility_combo.findData(visibility)
        self.ai_visibility_combo.blockSignals(True)
        self.ai_visibility_combo.setCurrentIndex(index if index >= 0 else 0)
        self.ai_visibility_combo.blockSignals(False)
        selected = set(target_ids)
        for target_id, checkbox in self.ai_target_checks.items():
            checkbox.blockSignals(True)
            checkbox.setChecked(target_id in selected)
            checkbox.blockSignals(False)
        legacy_names = tuple(legacy_target_names)
        self.ai_legacy_targets_label.setText(
            f"历史授权：{' / '.join(legacy_names)}" if legacy_names else ""
        )
        self.ai_legacy_targets_label.setToolTip(
            "历史授权仅为当前笔记保留，不在新授权列表中提供。"
            if legacy_names
            else ""
        )
        self.ai_legacy_targets_label.setVisible(bool(legacy_names))
        self.update_metadata_summary()

    def clear_ai_metadata_values(self) -> None:
        self.ai_visibility_combo.blockSignals(True)
        self.ai_visibility_combo.setCurrentIndex(
            0 if self.ai_visibility_combo.count() else -1
        )
        self.ai_visibility_combo.blockSignals(False)
        for checkbox in self.ai_target_checks.values():
            checkbox.blockSignals(True)
            checkbox.setChecked(False)
            checkbox.blockSignals(False)
        self.ai_legacy_targets_label.clear()
        self.ai_legacy_targets_label.setVisible(False)
        self.update_metadata_summary()

    def selected_ai_target_ids(self) -> tuple[str, ...]:
        return tuple(
            target_id
            for target_id, checkbox in self.ai_target_checks.items()
            if checkbox.isChecked()
        )

    def _build_status(self, layout: QVBoxLayout) -> None:
        status_row = QHBoxLayout()
        status_row.setSpacing(12)
        self.delete_button = self._action_button("trash.svg", "删除笔记")
        self.delete_button.setObjectName("deleteNoteButton")
        status_row.addWidget(self.delete_button)
        self.status_label = QLabel("未选择笔记")
        self.status_label.setObjectName("statusLabel")
        status_row.addWidget(self.status_label, stretch=1)
        self.reload_button = QPushButton("重新加载")
        self.reload_button.setVisible(False)
        status_row.addWidget(self.reload_button)
        layout.addLayout(status_row)

    @staticmethod
    def _action_button(filename: str, tooltip: str) -> QPushButton:
        button = QPushButton()
        button.setObjectName("actionBarButton")
        button.setProperty("iconFile", filename)
        button.setIcon(themed_icon(filename))
        button.setIconSize(QSize(14, 14))
        button.setFixedSize(28, 28)
        button.setToolTip(tooltip)
        return button

    def _stored_sidebar_width(self) -> int:
        try:
            width = int(self.settings.value(self.SIDEBAR_WIDTH_SETTING, self.DEFAULT_SIDEBAR_WIDTH))
        except (TypeError, ValueError):
            width = self.DEFAULT_SIDEBAR_WIDTH
        return max(self.MIN_SIDEBAR_WIDTH, min(self.MAX_SIDEBAR_WIDTH, width))

    def _save_sidebar_width(self, _position: int = 0, _index: int = 0) -> None:
        width = max(self.MIN_SIDEBAR_WIDTH, min(self.MAX_SIDEBAR_WIDTH, self.sidebar.width()))
        self.settings.setValue(self.SIDEBAR_WIDTH_SETTING, width)

    def restore_sidebar_width(self, width: int) -> None:
        """Preserve the user-adjusted R1 width through stylesheet changes."""

        safe_width = max(self.MIN_SIDEBAR_WIDTH, min(self.MAX_SIDEBAR_WIDTH, width))
        self.splitter.setSizes([safe_width, max(1, self.splitter.width() - safe_width)])

    def toggle_metadata_panel(self) -> None:
        self.set_metadata_expanded(not self.metadata_expanded)

    def set_metadata_expanded(self, expanded: bool) -> None:
        self.metadata_expanded = expanded
        self.metadata_panel.setVisible(expanded)

    def update_metadata_summary(self, *_args) -> None:
        main = self.main_combo.currentText() or "临时"
        sub = self.sub_combo.currentText() or "无"
        path = main if sub == "无" else f"{main} / {sub}"
        tags = parse_tags(self.tags_input.text())
        tag_text = " ".join(f"#{tag}" for tag in tags) if tags else "无标签"
        if self.ai_metadata_configured:
            target_names = [
                checkbox.text()
                for checkbox in self.ai_target_checks.values()
                if checkbox.isChecked()
            ]
            if not self.ai_legacy_targets_label.isHidden():
                legacy_text = self.ai_legacy_targets_label.text().removeprefix(
                    "历史授权："
                )
                target_names.extend(
                    f"{name}（历史）"
                    for name in legacy_text.split(" / ")
                    if name
                )
            if not target_names:
                targets_text = "无"
            elif len(target_names) <= 3:
                targets_text = " / ".join(target_names)
            else:
                targets_text = f"{' / '.join(target_names[:3])} 等 {len(target_names)} 项"
            visibility_text = self.ai_visibility_combo.currentText()
        else:
            targets_text = "未接线"
            visibility_text = "未接线"
        self.metadata_summary_button.setText(
            f"{path} · {tag_text} · AI对象：{targets_text} · "
            f"AI可见性：{visibility_text}"
        )

    @staticmethod
    def repolish_action(button: QPushButton) -> None:
        repolish(button)

    def refresh_theme_icons(self) -> None:
        for button in (self.pin_button, self.links_button, self.export_button, self.delete_button):
            button.setIcon(themed_icon(str(button.property("iconFile"))))
        self.metadata_collapse_button.setIcon(themed_icon("chevron-up.svg"))
        apply_selection_palette(self.body_editor)
        apply_selection_palette(self.body_editor.viewport())
        if self.reminder_center is not None:
            self.reminder_center.refresh_theme()

    @staticmethod
    def _make_surface_child_transparent(widget: QWidget) -> None:
        palette = QPalette(widget.palette())
        palette.setColor(QPalette.Window, Qt.transparent)
        palette.setColor(QPalette.Base, Qt.transparent)
        widget.setPalette(palette)
        widget.setAutoFillBackground(False)

    def enforce_single_surface_backgrounds(self) -> None:
        widgets = (
            self.sidebar.note_list,
            self.sidebar.note_list.viewport(),
            self.sidebar.trash_tree,
            self.sidebar.trash_tree.viewport(),
            self.editor_toolbar,
            *self.editor_toolbar.toolbar_rows,
            *self.editor_toolbar.toolbar_groups,
            self.editor_toolbar.find_panel,
            self.body_editor,
            self.body_editor.viewport(),
            self.empty_label,
            self.status_label,
        )
        for widget in widgets:
            self._make_surface_child_transparent(widget)
        apply_selection_palette(self.body_editor)
        apply_selection_palette(self.body_editor.viewport())

    def set_editor_available(self, available: bool) -> None:
        for widget in (
            self.title_band,
            self.title_separator,
            self.metadata_section,
            self.delete_button,
            self.editor_toolbar,
            self.body_editor,
        ):
            widget.setVisible(available)
        self.metadata_panel.setVisible(available and self.metadata_expanded)
        self.empty_label.setVisible(not available)
        self.ai_visibility_combo.setEnabled(
            available and self.ai_metadata_configured
        )
        for checkbox in self.ai_target_checks.values():
            checkbox.setEnabled(available and self.ai_metadata_configured)

    def set_editor_read_only(self, read_only: bool) -> None:
        """Keep a loaded note visible while disabling every mutation control."""
        editable = not read_only
        self.title_input.setReadOnly(read_only)
        self.body_editor.setReadOnly(read_only)
        self.tags_input.setReadOnly(read_only)
        for combo in (self.main_combo, self.sub_combo):
            combo.setEnabled(editable)
        self.ai_visibility_combo.setEnabled(
            editable and self.ai_metadata_configured
        )
        for checkbox in self.ai_target_checks.values():
            checkbox.setEnabled(editable and self.ai_metadata_configured)
        for widget in (
            self.editor_toolbar,
            self.metadata_summary_button,
            self.metadata_panel,
            self.pin_button,
            self.links_button,
            self.export_button,
            self.delete_button,
        ):
            widget.setEnabled(editable)

    def set_normal_status(self, text: str) -> None:
        self.status_label.setProperty("conflict", False)
        self.status_label.setText(text)
        self.reload_button.setVisible(False)
        repolish(self.status_label)

    def set_conflict_status(self, text: str) -> None:
        self.status_label.setProperty("conflict", True)
        self.status_label.setText(text)
        self.reload_button.setVisible(True)
        repolish(self.status_label)
