from __future__ import annotations

import sys
import time

from PySide6.QtCore import QEvent, QSettings, QSize, QThread, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtNetwork import QNetworkInformation
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QLayout,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QAbstractItemView,
    QHeaderView,
    QStackedWidget,
    QSplitter,
    QSpacerItem,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.ai_targets import (
    AI_TARGET_DEFINITIONS,
    AI_TARGETS,
    AI_VISIBILITY_OPTIONS,
    visibility_display,
)
from src.ai_center_dialog import AiCenterDialog
from src.app_paths import (
    APP_SETTINGS_PATH,
    DATA_ROOT_OVERRIDDEN,
    GALE_MIRROR_PATH,
    GALE_PUBLISH_LOCK_PATH,
    GALE_PUBLISH_STATE_PATH,
)
from src.batch_note_operations import (
    empty_trash,
    permanently_delete_trash_items,
    restore_trash_items,
)
from src.category_manager import CategoryManagerDialog
from src.database import (
    DEFAULT_MAIN_CATEGORY,
    DEFAULT_SUB_CATEGORY,
    DEFAULT_SUB_SUB_CATEGORY,
    create_directory,
    create_note_in_directory,
    delete_directory,
    get_note,
    get_note_ai_targets,
    get_note_tags,
    list_ai_receipt_inbox_items,
    list_directories,
    list_deleted_notes,
    list_notes,
    list_notes_by_tag,
    list_recent_or_common_tags,
    list_trash_tree,
    permanently_delete_note,
    permanently_delete_trash_directory,
    rename_note,
    rename_directory,
    restore_note,
    restore_trash_directory,
    save_editor_state,
    search_notes,
    set_note_pinned,
    soft_delete_note,
)
from src.editor import NotesEditor
from src.editor_toolbar import EditorToolbar
from src.export_center import ExportCenterDialog, ExportContext
from src.floating_combo import FloatingComboBox
from src.gale_production_background import (
    AUTO_PULL_SETTING,
    BackgroundPullError,
    background_paths,
    background_status_text,
    record_background_error,
    start_bundled_installer,
)
from src.link_drawer import LinkDrawer
from src.sidebar_interactions import (
    TrashTreeWidget,
    configure_trash_item,
    is_multi_select_click,
    item_data,
    selected_items_without_selected_ancestors,
)
from src.smart_views import SMART_VIEWS, get_smart_view_notes
from src.services.gale_publish_service import (
    GaleAutoPublishScheduler,
    GalePublishConfig,
    GalePublishError,
    GalePublishResult,
    load_publish_state,
    publish_gale_mirror,
    sha256_file,
)
from src.tag_manager import format_tags, parse_tags
from src.theme import (
    THEME_LABELS,
    apply_theme,
    current_theme,
    load_saved_theme,
    normalize_theme_name,
    repolish,
    save_theme,
    themed_icon,
)
from src.time_utils import format_display_datetime, format_sidebar_clock


class NoteTableDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        if index.column() == 1:
            option.textElideMode = Qt.ElideNone
        else:
            option.textElideMode = Qt.ElideRight

    def paint(self, painter: QPainter, option, index) -> None:
        selected = bool(option.state & QStyle.State_Selected)
        if selected and index.column() == 0:
            option.font.setWeight(QFont.DemiBold)
        super().paint(painter, option, index)
        if selected and index.column() == 0:
            painter.save()
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(current_theme().accent))
            marker = option.rect.adjusted(0, 8, -(option.rect.width() - 3), -8)
            painter.drawRect(marker)
            painter.restore()


class EditorSheet(QWidget):
    def __init__(self, preferred_width: int, parent=None) -> None:
        super().__init__(parent)
        self.preferred_width = preferred_width
        self.setMinimumWidth(0)
        self.setMaximumWidth(preferred_width)

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(self.preferred_width, hint.height())

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())


EDITOR_BASELINE_MAX_WIDTH = 800
EDITOR_ACCEPTED_MAX_WIDTH = 920
EDITOR_TARGET_MAX_WIDTH = 1280
EDITOR_WIDE_SIDE_MARGIN = 24
EDITOR_MEDIUM_SIDE_MARGIN = 16
EDITOR_NARROW_SIDE_MARGIN = 12
EDITOR_BOTTOM_MARGIN = 12
TITLE_SEPARATOR_HEIGHT = 2
THEME_SWATCH_SIZE = 14
THEME_SWATCH_ACTIVE_SIZE = 16
NOTE_DATE_HORIZONTAL_PADDING = 32
SIDEBAR_CLOCK_WIDEST_TIME = "88:88:88"


def note_date_column_width(font_metrics: QFontMetrics) -> int:
    return font_metrics.horizontalAdvance("00-00") + NOTE_DATE_HORIZONTAL_PADDING


def sidebar_clock_time_width(font_metrics: QFontMetrics) -> int:
    return font_metrics.horizontalAdvance(SIDEBAR_CLOCK_WIDEST_TIME)


def compact_ai_target_summary(targets: list[str], limit: int = 3) -> str:
    if not targets:
        return "无"
    if len(targets) <= limit:
        return " / ".join(targets)
    return f"{' / '.join(targets[:limit])} 等 {len(targets)} 项"


def editor_horizontal_metrics(width: int) -> tuple[int, int, int]:
    side_margin = (
        EDITOR_WIDE_SIDE_MARGIN
        if width >= 896
        else EDITOR_MEDIUM_SIDE_MARGIN
        if width >= 640
        else EDITOR_NARROW_SIDE_MARGIN
    )
    return side_margin, side_margin, 0


def application_settings() -> QSettings:
    if DATA_ROOT_OVERRIDDEN:
        APP_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        return QSettings(str(APP_SETTINGS_PATH), QSettings.IniFormat)
    return QSettings("S-notes", "S-notes")


class GalePublishThread(QThread):
    def __init__(self, config: GalePublishConfig, *, manual: bool) -> None:
        super().__init__()
        self.config = config
        self.manual = manual
        self.result: GalePublishResult | None = None

    def run(self) -> None:
        try:
            self.result = publish_gale_mirror(self.config)
        except GalePublishError as error:
            self.result = GalePublishResult(
                status="failed",
                sha256="",
                note_count=0,
                reason=error.code,
                previous_preserved=error.previous_preserved,
            )
        except Exception:
            self.result = GalePublishResult(
                status="failed",
                sha256="",
                note_count=0,
                reason="unexpected_failure",
                previous_preserved=False,
            )


class MainWindow(QMainWindow):
    DEFAULT_SIDEBAR_WIDTH = 280
    MIN_SIDEBAR_WIDTH = 280
    MAX_SIDEBAR_WIDTH = 480
    SIDEBAR_WIDTH_SETTING = "ui/sidebar_width"
    EDITOR_MAX_WIDTH = EDITOR_TARGET_MAX_WIDTH
    AUTOSAVE_DELAY_MS = 1000
    AUTOSAVE_RETRY_DELAYS_MS = (3000, 10000, 30000)
    GALE_AUTO_PUBLISH_SETTING = "gale/auto_publish_enabled"
    GALE_WATCH_INTERVAL_MS = 60_000
    GALE_AUTO_PULL_SETTING = AUTO_PULL_SETTING
    GALE_PULL_STATE_WATCH_INTERVAL_MS = 5_000

    def __init__(self) -> None:
        super().__init__()
        self.settings = application_settings()
        self.current_theme_name = load_saved_theme()
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, self.current_theme_name)
        self.current_note: dict | None = None
        self.is_loading_note = False
        self.has_unsaved_changes = False
        self.category_controls_loading = False
        self.directory_rows: list[dict] = []
        self.children_by_parent: dict[int | None, list[dict]] = {}
        self.current_sidebar_mode = "directory"
        self.current_filter_value = ""
        self.metadata_expanded = False
        self.link_drawer_visible = False
        self.filter_controls_loading = False
        self.note_id_before_trash: int | None = None
        self.receipt_inbox_dialog: AiCenterDialog | None = None
        self.autosave_retry_count = 0
        self.save_timer = QTimer(self)
        self.save_timer.setInterval(self.AUTOSAVE_DELAY_MS)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self.save_current_note)
        self.search_timer = QTimer(self)
        self.search_timer.setInterval(300)
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self.apply_search_filter)
        self.sidebar_clock_timer = QTimer(self)
        self.sidebar_clock_timer.setInterval(1000)
        self.sidebar_clock_timer.timeout.connect(self.update_sidebar_clock)
        publish_state = load_publish_state(GALE_PUBLISH_STATE_PATH)
        self.gale_auto_scheduler = GaleAutoPublishScheduler(
            enabled=False,
            last_success_hash=publish_state.last_success_hash,
        )
        self.gale_publish_thread: GalePublishThread | None = None
        self.gale_close_after_publish = False
        self.gale_source_signature: tuple[int, int] | None = None
        self.gale_last_watch_wall_time = time.time()
        self.gale_network_online = False
        self.gale_network_information: QNetworkInformation | None = None
        self.gale_watch_timer = QTimer(self)
        self.gale_watch_timer.setInterval(self.GALE_WATCH_INTERVAL_MS)
        self.gale_watch_timer.timeout.connect(self.check_gale_auto_publish)
        self.gale_auto_pull_enabled = True
        self.gale_auto_pull_installer_pid: int | None = None
        self.gale_pull_state_signature: tuple[int, int] | None = None
        self.gale_pull_state_timer = QTimer(self)
        self.gale_pull_state_timer.setInterval(
            self.GALE_PULL_STATE_WATCH_INTERVAL_MS
        )
        self.gale_pull_state_timer.timeout.connect(
            self.refresh_gale_auto_pull_state
        )

        self.setWindowTitle("S-notes")
        self.resize(1360, 860)
        self.setCentralWidget(self._build_content())
        self.update_sidebar_clock()
        self.sidebar_clock_timer.start()
        self.reload_directory_cache()
        self.populate_filter_controls()
        self.refresh_directory_tree()
        self.ensure_startup_note_loaded()
        self.configure_gale_auto_publish()
        self.configure_gale_auto_pull()

    def _build_content(self) -> QWidget:
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = self._build_sidebar()
        sidebar.setMinimumWidth(self.MIN_SIDEBAR_WIDTH)
        sidebar.setMaximumWidth(self.MAX_SIDEBAR_WIDTH)

        content = QWidget()
        content.setObjectName("mainContentArea")
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._build_editor(), stretch=1)
        self.link_drawer = LinkDrawer()
        self.link_drawer.setVisible(False)
        self.link_drawer.noteSelected.connect(self.open_linked_note)
        self.link_drawer.closeRequested.connect(self.toggle_link_drawer)
        content_layout.addWidget(self.link_drawer)

        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setObjectName("mainSplitter")
        self.main_splitter.setHandleWidth(4)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setOpaqueResize(True)
        self.main_splitter.addWidget(sidebar)
        self.main_splitter.addWidget(content)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        sidebar_width = self._stored_sidebar_width()
        self.main_splitter.setSizes([sidebar_width, max(1, self.width() - sidebar_width)])
        self.main_splitter.splitterMoved.connect(self._save_sidebar_width)
        layout.addWidget(self.main_splitter)

        return root

    def _stored_sidebar_width(self) -> int:
        raw_value = self.settings.value(
            self.SIDEBAR_WIDTH_SETTING, self.DEFAULT_SIDEBAR_WIDTH
        )
        try:
            width = int(raw_value)
        except (TypeError, ValueError):
            width = self.DEFAULT_SIDEBAR_WIDTH
        return max(self.MIN_SIDEBAR_WIDTH, min(self.MAX_SIDEBAR_WIDTH, width))

    def _save_sidebar_width(self, _position: int = 0, _index: int = 0) -> None:
        width = max(
            self.MIN_SIDEBAR_WIDTH,
            min(self.MAX_SIDEBAR_WIDTH, self.sidebar.width()),
        )
        self.settings.setValue(self.SIDEBAR_WIDTH_SETTING, width)

    def set_theme(self, name: str) -> None:
        theme_name = normalize_theme_name(name)
        if theme_name == self.current_theme_name:
            return
        sidebar_width = self.sidebar.width() if hasattr(self, "sidebar") else None
        self.current_theme_name = theme_name
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, theme_name)
        save_theme(theme_name)
        if hasattr(self, "body_edit"):
            self.body_edit.apply_theme()
        if hasattr(self, "note_table"):
            self.update_note_date_column_width()
        if hasattr(self, "sidebar_clock_time_label"):
            self.update_sidebar_clock_metrics()
        self._update_theme_button_states()
        self._refresh_theme_icons()
        if sidebar_width is not None:
            self._restore_sidebar_width(sidebar_width)
            QTimer.singleShot(0, lambda width=sidebar_width: self._restore_sidebar_width(width))

    def _restore_sidebar_width(self, width: int) -> None:
        if not hasattr(self, "main_splitter"):
            return
        safe_width = max(self.MIN_SIDEBAR_WIDTH, min(self.MAX_SIDEBAR_WIDTH, width))
        self.main_splitter.setSizes([safe_width, max(1, self.main_splitter.width() - safe_width)])

    def _update_theme_button_states(self) -> None:
        for theme_name, button in getattr(self, "theme_buttons", {}).items():
            active = theme_name == self.current_theme_name
            button.setProperty("active", active)
            size = THEME_SWATCH_ACTIVE_SIZE if active else THEME_SWATCH_SIZE
            button.setFixedSize(size, size)
            repolish(button)

    def _refresh_theme_icons(self) -> None:
        if hasattr(self, "new_note_button"):
            self.new_note_button.setIcon(themed_icon("plus.svg", "accent_text"))
        if getattr(self, "search_clear_button", None) is not None:
            self.search_clear_button.setIcon(themed_icon("x.svg", "text_muted"))
        if hasattr(self, "trash_close_button"):
            self.trash_close_button.setIcon(themed_icon("x.svg", "text_muted"))
        if hasattr(self, "metadata_collapse_button"):
            self.metadata_collapse_button.setIcon(themed_icon("chevron-up.svg"))
        for button in (
            getattr(self, "pin_button", None),
            getattr(self, "link_drawer_button", None),
            getattr(self, "export_button", None),
            getattr(self, "delete_note_button", None),
        ):
            if button is not None and button.property("iconFile"):
                button.setIcon(themed_icon(str(button.property("iconFile"))))
        if hasattr(self, "editor_toolbar"):
            self.editor_toolbar.refresh_theme()
        if hasattr(self, "link_drawer"):
            self.link_drawer.refresh_theme()
        if hasattr(self, "note_table"):
            pin_icon = themed_icon("pin.svg", "accent")
            for row in range(self.note_table.rowCount()):
                item = self.note_table.item(row, 0)
                if item is not None and not item.icon().isNull():
                    item.setIcon(pin_icon)
            self.note_table.viewport().update()

    def update_sidebar_clock(self) -> None:
        if not hasattr(self, "sidebar_clock_time_label"):
            return
        time_text, date_text = format_sidebar_clock().split(" ", 1)
        self.sidebar_clock_time_label.setText(time_text)
        self.sidebar_clock_date_label.setText(date_text)

    def update_sidebar_clock_metrics(self) -> None:
        if not hasattr(self, "sidebar_clock_time_label"):
            return
        self.sidebar_clock_time_label.ensurePolished()
        self.sidebar_clock_time_label.setFixedWidth(
            sidebar_clock_time_width(
                self.sidebar_clock_time_label.fontMetrics()
            )
        )

    def eventFilter(self, watched, event) -> bool:
        if watched is getattr(self, "editor_root", None) and event.type() == QEvent.Resize:
            self._update_editor_margins(event.size().width())
        return super().eventFilter(watched, event)

    def _update_editor_margins(self, width: int) -> None:
        if not hasattr(self, "editor_outer_layout"):
            return
        left_margin, right_margin, leading_space = editor_horizontal_metrics(width)
        self.editor_outer_layout.setContentsMargins(
            left_margin,
            0,
            right_margin,
            0,
        )
        if hasattr(self, "editor_leading_spacer"):
            self.editor_leading_spacer.changeSize(
                leading_space,
                0,
                QSizePolicy.Fixed,
                QSizePolicy.Minimum,
            )
            self.editor_outer_layout.invalidate()

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        self.sidebar = sidebar
        sidebar.setObjectName("sidebar")
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        self.sidebar_clock_widget = QWidget()
        self.sidebar_clock_widget.setObjectName("sidebarClock")
        self.sidebar_clock_widget.setFixedHeight(32)
        sidebar_clock_layout = QHBoxLayout(self.sidebar_clock_widget)
        sidebar_clock_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_clock_layout.setSpacing(4)
        self.sidebar_clock_time_label = QLabel()
        self.sidebar_clock_time_label.setObjectName("sidebarClockTime")
        self.sidebar_clock_time_label.setAlignment(
            Qt.AlignVCenter | Qt.AlignLeft
        )
        self.sidebar_clock_time_label.setFixedHeight(32)
        sidebar_clock_layout.addWidget(self.sidebar_clock_time_label)
        self.sidebar_clock_date_label = QLabel()
        self.sidebar_clock_date_label.setObjectName("sidebarClockDate")
        self.sidebar_clock_date_label.setAlignment(
            Qt.AlignVCenter | Qt.AlignLeft
        )
        self.sidebar_clock_date_label.setFixedHeight(32)
        sidebar_clock_layout.addWidget(self.sidebar_clock_date_label)
        self.update_sidebar_clock_metrics()
        header.addWidget(self.sidebar_clock_widget)
        header.addStretch(1)

        self.new_note_button = QPushButton()
        self.new_note_button.setObjectName("newNoteButton")
        self.new_note_button.setIconSize(QSize(14, 14))
        self.new_note_button.setFixedSize(28, 28)
        self.new_note_button.setToolTip("新建笔记")
        self.new_note_button.clicked.connect(self.handle_create_note)
        header.addWidget(self.new_note_button)
        layout.addLayout(header)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("sidebarSearch")
        self.search_input.setPlaceholderText("搜索笔记或 #标签")
        self.search_input.setFixedHeight(32)
        self.search_input.setClearButtonEnabled(True)
        self.search_clear_button = self.search_input.findChild(QToolButton)
        self.search_input.textChanged.connect(self.handle_search_text_changed)
        layout.addWidget(self.search_input)

        smart_row = QHBoxLayout()
        smart_row.setSpacing(4)
        self.smart_buttons: dict[str, QPushButton] = {}
        for label, view_name in SMART_VIEWS:
            button = QPushButton(label)
            button.setObjectName("smartTabButton")
            button.setProperty("active", False)
            button.clicked.connect(lambda checked=False, name=view_name: self.show_smart_view(name))
            button.setCheckable(True)
            button.setFixedHeight(28)
            smart_row.addWidget(button, stretch=1)
            self.smart_buttons[view_name] = button
        self.receipt_inbox_button = QPushButton("AI中心")
        self.receipt_inbox_button.setObjectName("smartTabButton")
        self.receipt_inbox_button.setFixedHeight(28)
        self.receipt_inbox_button.setToolTip("打开 AI中心")
        self.receipt_inbox_button.clicked.connect(self.open_receipt_inbox)
        smart_row.addWidget(self.receipt_inbox_button, stretch=1)
        layout.addLayout(smart_row)

        self.ai_actor_filter_widget = QWidget()
        self.ai_actor_filter_widget.setObjectName("aiActorFilter")
        ai_actor_filter_layout = QHBoxLayout(self.ai_actor_filter_widget)
        ai_actor_filter_layout.setContentsMargins(0, 0, 0, 0)
        ai_actor_filter_layout.setSpacing(6)
        ai_actor_label = QLabel("AI 对象")
        ai_actor_label.setObjectName("aiActorFilterLabel")
        ai_actor_filter_layout.addWidget(ai_actor_label)
        self.ai_actor_filter_combo = FloatingComboBox(popup_theme="dark")
        self.ai_actor_filter_combo.setObjectName("aiActorFilterCombo")
        self.ai_actor_filter_combo.setFixedHeight(28)
        self.ai_actor_filter_combo.setMinimumWidth(0)
        self.ai_actor_filter_combo.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Fixed,
        )
        self.configure_combo_popup(self.ai_actor_filter_combo, 150)
        self.ai_actor_filter_combo.addItem("全部", None)
        for target in AI_TARGET_DEFINITIONS:
            self.ai_actor_filter_combo.addItem(target.display_name, target.target_id)
        self.ai_actor_filter_combo.currentIndexChanged.connect(
            self.handle_ai_actor_filter_changed
        )
        ai_actor_filter_layout.addWidget(self.ai_actor_filter_combo, stretch=1)
        layout.addWidget(self.ai_actor_filter_widget)
        self.ai_actor_filter_widget.setVisible(False)

        filters = QGridLayout()
        filters.setContentsMargins(0, 0, 0, 0)
        filters.setHorizontalSpacing(4)
        filters.setVerticalSpacing(4)
        self.main_filter_combo = FloatingComboBox(popup_theme="dark")
        self.sub_filter_combo = FloatingComboBox(popup_theme="dark")
        self.sub_sub_filter_combo = FloatingComboBox(popup_theme="dark")
        for combo, width in (
            (self.main_filter_combo, 160),
            (self.sub_filter_combo, 120),
            (self.sub_sub_filter_combo, 120),
        ):
            combo.setObjectName("categoryFilter")
            combo.setFixedHeight(32)
            combo.setMinimumWidth(0)
            combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            self.configure_combo_popup(combo, width)
        self.category_manager_button = QPushButton("管理")
        self.category_manager_button.setObjectName("categoryManagerButton")
        self.category_manager_button.setToolTip("分类管理")
        self.category_manager_button.setFixedSize(48, 32)
        self.category_manager_button.clicked.connect(self.open_category_manager)
        filters.addWidget(self.main_filter_combo, 0, 0, 1, 2)
        filters.addWidget(self.category_manager_button, 0, 2)
        filters.addWidget(self.sub_filter_combo, 1, 0)
        filters.addWidget(self.sub_sub_filter_combo, 1, 1, 1, 2)
        filters.setColumnStretch(0, 1)
        filters.setColumnStretch(1, 1)
        filters.setColumnStretch(2, 0)
        self.main_filter_combo.currentTextChanged.connect(self.handle_main_filter_changed)
        self.sub_filter_combo.currentTextChanged.connect(self.handle_sub_filter_changed)
        self.sub_sub_filter_combo.currentTextChanged.connect(self.apply_category_filters)
        layout.addLayout(filters)

        self.note_table = QTableWidget(0, 2)
        self.note_table.setObjectName("noteTable")
        self.note_table.setHorizontalHeaderLabels(["标题", "日期"])
        self.note_table.verticalHeader().hide()
        self.note_table.horizontalHeader().hide()
        self.note_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.note_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.note_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.note_table.setShowGrid(False)
        self.note_table.setTextElideMode(Qt.ElideRight)
        self.note_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.note_table.setVerticalScrollMode(QTableWidget.ScrollPerPixel)
        header = self.note_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.resizeSection(1, note_date_column_width(self.note_table.fontMetrics()))
        self.note_table.verticalHeader().setDefaultSectionSize(32)
        self.note_table.setItemDelegate(NoteTableDelegate(self.note_table))
        self.note_table.cellClicked.connect(self.handle_note_table_clicked)
        self.note_table.itemSelectionChanged.connect(self.update_delete_button_state)
        self.note_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.note_table.customContextMenuRequested.connect(self.open_note_table_context_menu)

        self.sidebar_stack = QStackedWidget()
        note_view = QWidget()
        note_view_layout = QVBoxLayout(note_view)
        note_view_layout.setContentsMargins(0, 0, 0, 0)
        note_view_layout.setSpacing(0)
        note_view_layout.addWidget(self.note_table, stretch=1)
        self.note_view = note_view
        self.sidebar_stack.addWidget(note_view)

        self.directory_tree = TrashTreeWidget()
        self.directory_tree.itemClicked.connect(self.handle_trash_item_clicked)
        self.directory_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.directory_tree.customContextMenuRequested.connect(
            self.open_trash_item_context_menu
        )
        self.directory_tree.setObjectName("trashTree")
        self.directory_tree.setRootIsDecorated(False)
        trash_view = QWidget()
        trash_view_layout = QVBoxLayout(trash_view)
        trash_view_layout.setContentsMargins(0, 0, 0, 0)
        trash_view_layout.setSpacing(4)
        trash_header = QHBoxLayout()
        trash_title = QLabel("回收站")
        trash_title.setObjectName("trashTitle")
        trash_header.addWidget(trash_title, stretch=1)
        self.trash_close_button = QPushButton()
        self.trash_close_button.setObjectName("trashCloseButton")
        self.trash_close_button.setToolTip("退出回收站")
        self.trash_close_button.setFixedSize(28, 28)
        self.trash_close_button.setIconSize(QSize(14, 14))
        self.trash_close_button.clicked.connect(self.exit_trash)
        trash_header.addWidget(self.trash_close_button)
        trash_view_layout.addLayout(trash_header)
        trash_view_layout.addWidget(self.directory_tree, stretch=1)
        self.trash_view = trash_view
        self.sidebar_stack.addWidget(trash_view)
        layout.addWidget(self.sidebar_stack, stretch=1)

        self.trash_button = QPushButton("回收站")
        self.trash_button.setObjectName("trashButton")
        self.trash_button.setFixedHeight(32)
        self.trash_button.clicked.connect(self.show_trash)
        self.trash_button.setContextMenuPolicy(Qt.CustomContextMenu)
        self.trash_button.customContextMenuRequested.connect(
            self.open_trash_root_context_menu
        )

        self.sidebar_footer = QWidget()
        self.sidebar_footer.setObjectName("sidebarFooter")
        self.sidebar_footer.setFixedHeight(32)
        footer_layout = QHBoxLayout(self.sidebar_footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(4)
        footer_layout.addWidget(self.trash_button, stretch=1)
        self.theme_buttons: dict[str, QPushButton] = {}
        for theme_name, label in THEME_LABELS:
            button = QPushButton()
            button.setObjectName("themeSwatch")
            button.setProperty("themeName", theme_name)
            button.setFixedSize(THEME_SWATCH_SIZE, THEME_SWATCH_SIZE)
            button.setToolTip(label)
            button.setAccessibleName(label)
            button.clicked.connect(
                lambda checked=False, name=theme_name: self.set_theme(name)
            )
            footer_layout.addWidget(button, alignment=Qt.AlignVCenter)
            self.theme_buttons[theme_name] = button
        layout.addWidget(self.sidebar_footer)

        self.refresh_ai_center_badge()
        self._update_theme_button_states()
        self._refresh_theme_icons()
        return sidebar

    def _build_editor(self) -> QWidget:
        editor = QWidget()
        self.editor_root = editor
        editor.setObjectName("editor")
        outer_layout = QHBoxLayout(editor)
        self.editor_outer_layout = outer_layout
        outer_layout.setContentsMargins(
            EDITOR_WIDE_SIDE_MARGIN,
            0,
            EDITOR_WIDE_SIDE_MARGIN,
            0,
        )
        outer_layout.setSpacing(0)
        editor.installEventFilter(self)

        editor_sheet = EditorSheet(self.EDITOR_MAX_WIDTH)
        self.editor_sheet = editor_sheet
        editor_sheet.setObjectName("editorSheet")
        editor_sheet.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = QVBoxLayout(editor_sheet)
        layout.setContentsMargins(0, 32, 0, EDITOR_BOTTOM_MARGIN)
        layout.setSpacing(0)
        self.editor_leading_spacer = QSpacerItem(
            0,
            0,
            QSizePolicy.Fixed,
            QSizePolicy.Minimum,
        )
        outer_layout.addSpacerItem(self.editor_leading_spacer)
        outer_layout.addWidget(editor_sheet)
        outer_layout.addStretch(1)

        self.title_band = QWidget()
        self.title_band.setObjectName("titleBand")
        title_band_layout = QHBoxLayout(self.title_band)
        title_band_layout.setContentsMargins(0, 0, 0, 0)
        title_band_layout.setSpacing(8)
        self.title_input = QLineEdit()
        self.title_input.setObjectName("titleInput")
        self.title_input.setPlaceholderText("标题")
        self.title_input.setMinimumWidth(0)
        self.title_input.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.title_input.textChanged.connect(self.mark_note_dirty)
        title_band_layout.addWidget(self.title_input, stretch=1, alignment=Qt.AlignBottom)
        self.editor_action_bar = QWidget()
        self.editor_action_bar.setObjectName("editorActionBar")
        self.editor_action_bar.setFixedHeight(36)
        action_bar_layout = QHBoxLayout(self.editor_action_bar)
        action_bar_layout.setContentsMargins(4, 4, 4, 4)
        action_bar_layout.setSpacing(4)
        self.pin_button = self._icon_button(
            "pin.svg",
            "固定笔记",
            self.toggle_current_note_pinned,
            object_name="actionBarButton",
            button_size=28,
            icon_size=14,
        )
        self.link_drawer_button = self._icon_button(
            "link.svg",
            "打开关联",
            self.toggle_link_drawer,
            object_name="actionBarButton",
            button_size=28,
            icon_size=14,
        )
        self.export_button = self._icon_button(
            "export.svg",
            "导出笔记",
            self.open_export_center,
            object_name="actionBarButton",
            button_size=28,
            icon_size=14,
        )
        action_bar_layout.addWidget(self.pin_button)
        action_bar_layout.addWidget(self.link_drawer_button)
        action_bar_layout.addWidget(self.export_button)
        title_band_layout.addWidget(
            self.editor_action_bar,
            alignment=Qt.AlignRight | Qt.AlignBottom,
        )
        layout.addWidget(self.title_band)
        self.title_separator = QFrame()
        self.title_separator.setObjectName("titleSeparator")
        self.title_separator.setFrameShape(QFrame.HLine)
        self.title_separator.setFixedHeight(TITLE_SEPARATOR_HEIGHT)
        layout.addWidget(self.title_separator)

        self.metadata_section = QWidget()
        self.metadata_section.setObjectName("metadataSection")
        metadata_section_layout = QVBoxLayout(self.metadata_section)
        metadata_section_layout.setContentsMargins(0, 8, 0, 4)
        metadata_section_layout.setSpacing(8)
        self.metadata_summary_button = QPushButton("")
        self.metadata_summary_button.setObjectName("metadataSummaryButton")
        self.metadata_summary_button.setToolTip("展开或收起笔记信息")
        self.metadata_summary_button.clicked.connect(self.toggle_metadata_panel)
        metadata_section_layout.addWidget(self.metadata_summary_button)

        self.metadata_panel = QWidget()
        self.metadata_panel.setObjectName("metadataPanel")
        metadata_panel_layout = QVBoxLayout(self.metadata_panel)
        metadata_panel_layout.setSizeConstraint(QLayout.SetNoConstraint)
        metadata_panel_layout.setContentsMargins(12, 12, 12, 12)
        metadata_panel_layout.setSpacing(8)

        metadata_top_row = QHBoxLayout()
        metadata_top_row.setContentsMargins(0, 0, 0, 0)
        metadata_top_row.setSpacing(12)

        main_label = QLabel("主分类")
        main_label.setObjectName("metadataFieldLabel")
        main_label.setAlignment(Qt.AlignVCenter)
        self.main_category_combo = FloatingComboBox()
        self.configure_combo_popup(self.main_category_combo, 180)
        self.main_category_combo.setMinimumWidth(0)
        self.main_category_combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.main_category_combo.setFixedHeight(28)
        self.main_category_combo.currentTextChanged.connect(
            self.handle_main_category_changed
        )
        metadata_top_row.addWidget(main_label)
        metadata_top_row.addWidget(self.main_category_combo, stretch=1)

        sub_label = QLabel("子分类")
        sub_label.setObjectName("metadataFieldLabel")
        sub_label.setAlignment(Qt.AlignVCenter)
        self.sub_category_combo = FloatingComboBox()
        self.configure_combo_popup(self.sub_category_combo, 190)
        self.sub_category_combo.setMinimumWidth(0)
        self.sub_category_combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.sub_category_combo.setFixedHeight(28)
        self.sub_category_combo.currentTextChanged.connect(
            self.handle_sub_category_changed
        )
        metadata_top_row.addWidget(sub_label)
        metadata_top_row.addWidget(self.sub_category_combo, stretch=1)

        sub_sub_label = QLabel("子子分类")
        sub_sub_label.setObjectName("metadataFieldLabel")
        sub_sub_label.setAlignment(Qt.AlignVCenter)
        self.sub_sub_category_combo = FloatingComboBox()
        self.configure_combo_popup(self.sub_sub_category_combo, 180)
        self.sub_sub_category_combo.setMinimumWidth(0)
        self.sub_sub_category_combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.sub_sub_category_combo.setFixedHeight(28)
        self.sub_sub_category_combo.currentTextChanged.connect(
            self.handle_sub_sub_category_changed
        )
        metadata_top_row.addWidget(sub_sub_label)
        metadata_top_row.addWidget(self.sub_sub_category_combo, stretch=1)
        metadata_panel_layout.addLayout(metadata_top_row)

        metadata_tags_row = QHBoxLayout()
        metadata_tags_row.setContentsMargins(0, 0, 0, 0)
        metadata_tags_row.setSpacing(12)
        tags_label = QLabel("标签")
        tags_label.setObjectName("metadataFieldLabel")
        tags_label.setAlignment(Qt.AlignVCenter)
        self.tags_input = QLineEdit()
        self.tags_input.setFixedHeight(28)
        self.tags_input.setPlaceholderText("todo, vps, ai-ok")
        self.tags_input.textChanged.connect(self.handle_tags_text_changed)
        metadata_tags_row.addWidget(tags_label)
        metadata_tags_row.addWidget(self.tags_input, stretch=1)
        ai_label = QLabel("AI 可见性")
        ai_label.setObjectName("metadataFieldLabel")
        ai_label.setAlignment(Qt.AlignVCenter)
        self.ai_visibility_combo = FloatingComboBox()
        self.configure_combo_popup(self.ai_visibility_combo, 210)
        self.ai_visibility_combo.addItems(AI_VISIBILITY_OPTIONS)
        self.ai_visibility_combo.setFixedWidth(200)
        self.ai_visibility_combo.setFixedHeight(28)
        self.ai_visibility_combo.currentTextChanged.connect(
            self.handle_ai_visibility_changed
        )
        metadata_tags_row.addWidget(ai_label)
        metadata_tags_row.addWidget(self.ai_visibility_combo)
        self.metadata_collapse_button = QPushButton()
        self.metadata_collapse_button.setObjectName("metadataCollapseButton")
        self.metadata_collapse_button.setFixedSize(28, 28)
        self.metadata_collapse_button.setIconSize(QSize(14, 14))
        self.metadata_collapse_button.clicked.connect(
            lambda: self.set_metadata_expanded(False)
        )
        metadata_tags_row.addWidget(self.metadata_collapse_button)
        metadata_panel_layout.addLayout(metadata_tags_row)

        metadata_targets_row = QHBoxLayout()
        metadata_targets_row.setContentsMargins(0, 0, 0, 0)
        metadata_targets_row.setSpacing(12)
        ai_targets_label = QLabel("AI 对象")
        ai_targets_label.setObjectName("metadataFieldLabel")
        ai_targets_label.setAlignment(Qt.AlignVCenter)
        metadata_targets_row.addWidget(ai_targets_label)
        ai_targets_widget = QWidget()
        ai_targets_widget.setObjectName("aiTargetsWidget")
        ai_targets_widget.setMinimumWidth(0)
        ai_targets_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        ai_targets_layout = QHBoxLayout(ai_targets_widget)
        ai_targets_layout.setContentsMargins(0, 0, 0, 0)
        ai_targets_layout.setSpacing(16)
        self.ai_target_checks: dict[str, QCheckBox] = {}
        for target_name in AI_TARGETS:
            checkbox = QCheckBox(target_name)
            checkbox.setObjectName("aiTargetCheck")
            checkbox.setFixedHeight(24)
            checkbox.setMinimumWidth(0)
            checkbox.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            checkbox.stateChanged.connect(self.handle_ai_targets_changed)
            self.ai_target_checks[target_name] = checkbox
            ai_targets_layout.addWidget(checkbox)
        ai_targets_layout.addStretch(1)
        metadata_targets_row.addWidget(ai_targets_widget, stretch=1)
        metadata_panel_layout.addLayout(metadata_targets_row)
        metadata_section_layout.addWidget(self.metadata_panel)
        layout.addWidget(self.metadata_section)
        self.set_metadata_expanded(False)

        self.body_section = QWidget()
        self.body_section.setObjectName("bodySection")
        body_section_layout = QVBoxLayout(self.body_section)
        body_section_layout.setContentsMargins(0, 0, 0, 0)
        body_section_layout.setSpacing(8)
        self.body_edit = NotesEditor()
        self.body_edit.setObjectName("bodyEdit")
        self.body_edit.setPlaceholderText("写正文")
        self.body_edit.textChanged.connect(self.mark_note_dirty)
        self.body_edit.document().modificationChanged.connect(
            self.handle_body_document_modification
        )
        self.editor_toolbar = EditorToolbar(self.body_edit)
        body_section_layout.addWidget(self.editor_toolbar)
        body_section_layout.addWidget(self.body_edit, stretch=1)
        layout.addWidget(self.body_section, stretch=1)

        self.empty_editor_label = QLabel("选择一篇笔记开始编辑")
        self.empty_editor_label.setObjectName("editorEmptyState")
        self.empty_editor_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.empty_editor_label, stretch=1)

        status_row = QHBoxLayout()
        status_row.setSpacing(12)
        self.delete_note_button = QPushButton()
        self.delete_note_button.setObjectName("deleteNoteButton")
        self.delete_note_button.setProperty("iconFile", "trash.svg")
        self.delete_note_button.setIconSize(QSize(14, 14))
        self.delete_note_button.setFixedSize(32, 32)
        self.delete_note_button.setToolTip("删除笔记")
        self.delete_note_button.clicked.connect(self.confirm_delete_selected_notes)
        status_row.addWidget(self.delete_note_button)
        self.status_label = QLabel("未选择笔记")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        status_row.addWidget(self.status_label, stretch=1)
        layout.addLayout(status_row)

        self.set_editor_enabled(False)
        self._refresh_theme_icons()
        return editor

    def _icon_button(
        self,
        filename: str,
        tooltip: str,
        callback,
        *,
        object_name: str = "iconButton",
        button_size: int = 28,
        icon_size: int = 14,
    ) -> QPushButton:
        button = QPushButton()
        button.setObjectName(object_name)
        button.setProperty("iconFile", filename)
        button.setIcon(themed_icon(filename))
        button.setIconSize(QSize(icon_size, icon_size))
        button.setFixedSize(button_size, button_size)
        button.setToolTip(tooltip)
        button.clicked.connect(callback)
        return button

    def ensure_startup_note_loaded(self) -> None:
        self.clear_editor()

    def refresh_tag_filter_row(self) -> None:
        if not hasattr(self, "tag_filter_row"):
            return
        while self.tag_filter_row.count():
            item = self.tag_filter_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        tags = list_recent_or_common_tags()
        for tag in tags[:2]:
            button = QPushButton(f"#{tag['name']}")
            button.setObjectName("tagChipButton")
            button.clicked.connect(
                lambda checked=False, tag_name=tag["name"]: self.show_tag_results(tag_name)
            )
            self.tag_filter_row.addWidget(button)

        if len(tags) > 2:
            more_button = QPushButton()
            more_button.setObjectName("tagMoreButton")
            more_button.setIcon(themed_icon("more-horizontal.svg"))
            more_button.setIconSize(QSize(14, 14))
            more_button.setFixedSize(28, 28)
            menu = QMenu(more_button)
            for tag in tags[2:]:
                menu.addAction(
                    f"#{tag['name']}",
                    lambda checked=False, tag_name=tag["name"]: self.show_tag_results(tag_name),
                )
            more_button.setMenu(menu)
            self.tag_filter_row.addWidget(more_button)
        self.tag_filter_row.addStretch(1)

    def reload_directory_cache(self) -> None:
        self.directory_rows = list_directories()
        self.children_by_parent = {}
        for row in self.directory_rows:
            self.children_by_parent.setdefault(row["parent_id"], []).append(row)
        for rows in self.children_by_parent.values():
            rows.sort(key=lambda item: (item["name"].casefold(), item["id"]))

    def display_children_for_directory(self, directory_id: int | None) -> list[dict]:
        return [
            row
            for row in self.children_by_parent.get(directory_id, [])
            if row["name"] != DEFAULT_SUB_CATEGORY
        ]

    def refresh_directory_tree(
        self,
        selected_note_id: int | None = None,
    ) -> None:
        self.set_sidebar_mode("directory")
        self.sidebar_stack.setCurrentWidget(self.note_view)
        self.apply_category_filters(selected_note_id=selected_note_id)

    def display_flat_notes(
        self,
        title: str,
        notes: list[dict],
        *,
        mode: str,
        value: str = "",
        deleted: bool = False,
        selected_note_id: int | None = None,
    ) -> None:
        self.set_sidebar_mode(mode, value, title)
        self.sidebar_stack.setCurrentWidget(self.note_view)
        self.populate_note_table(notes, selected_note_id)

    def populate_filter_controls(self) -> None:
        self.filter_controls_loading = True
        self.set_combo_items(self.main_filter_combo, ["全部", *self.directory_names(None)], "全部")
        self.set_combo_items(self.sub_filter_combo, ["-"], "-")
        self.set_combo_items(self.sub_sub_filter_combo, ["-"], "-")
        self.filter_controls_loading = False

    def handle_main_filter_changed(self) -> None:
        if self.filter_controls_loading:
            return
        self.filter_controls_loading = True
        main = self.main_filter_combo.currentText()
        main_id = self.directory_id_for_path((main,)) if main != "全部" else None
        self.set_combo_items(self.sub_filter_combo, ["-", *self.directory_names(main_id)] if main_id else ["-"], "-")
        self.set_combo_items(self.sub_sub_filter_combo, ["-"], "-")
        self.filter_controls_loading = False
        self.return_to_directory(keep_filters=True)

    def handle_sub_filter_changed(self) -> None:
        if self.filter_controls_loading:
            return
        self.filter_controls_loading = True
        main, sub = self.main_filter_combo.currentText(), self.sub_filter_combo.currentText()
        sub_id = self.directory_id_for_path((main, sub)) if main != "全部" and sub != "-" else None
        self.set_combo_items(self.sub_sub_filter_combo, ["-", *self.directory_names(sub_id)] if sub_id else ["-"], "-")
        self.filter_controls_loading = False
        self.return_to_directory(keep_filters=True)

    def apply_category_filters(self, _value: str = "", *, selected_note_id: int | None = None) -> None:
        if self.filter_controls_loading:
            return
        notes = list_notes()
        main, sub, sub_sub = self.main_filter_combo.currentText(), self.sub_filter_combo.currentText(), self.sub_sub_filter_combo.currentText()
        if main and main != "全部":
            notes = [note for note in notes if note["main_category"] == main]
        if sub and sub != "-":
            notes = [note for note in notes if note["sub_category"] == sub]
        if sub_sub and sub_sub != "-":
            notes = [note for note in notes if note["sub_sub_category"] == sub_sub]
        self.populate_note_table(notes, selected_note_id)

    def populate_note_table(self, notes: list[dict], selected_note_id: int | None = None) -> None:
        self.update_note_date_column_width()
        notes = sorted(notes, key=lambda note: (bool(note.get("is_pinned")), note["updated_at"], note["id"]), reverse=True)
        self.note_table.blockSignals(True)
        self.note_table.setRowCount(len(notes))
        if not notes:
            self.note_table.setRowCount(1)
            empty = QTableWidgetItem("这里还没有笔记")
            empty.setFlags(Qt.NoItemFlags)
            self.note_table.setItem(0, 0, empty)
            self.note_table.setRowHeight(0, 32)
            self.note_table.blockSignals(False)
            self.update_delete_button_state()
            return
        pin_icon = themed_icon("pin.svg", "accent")
        selected_row = None
        for row, note in enumerate(notes):
            date_text = str(note["updated_at"])[5:10]
            values = [note["title"] or "未命名笔记", date_text]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, note["id"])
                if column == 1:
                    item.setToolTip(
                        f"更新于 {format_display_datetime(note['updated_at'])}"
                    )
                else:
                    path = " / ".join(
                        part
                        for part in (
                            note["main_category"],
                            note["sub_category"],
                            note["sub_sub_category"],
                        )
                        if part and part != "无"
                    )
                    item.setToolTip(f"{value}\n{path}" if path else value)
                if column == 0 and note.get("is_pinned"):
                    item.setIcon(pin_icon)
                if column == 1:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.note_table.setItem(row, column, item)
            self.note_table.setRowHeight(row, 32)
            if note["id"] == selected_note_id:
                selected_row = row
        if selected_row is not None:
            self.note_table.selectRow(selected_row)
            self.note_table.scrollToItem(self.note_table.item(selected_row, 0))
        self.note_table.blockSignals(False)
        self.update_delete_button_state()

    def update_note_date_column_width(self) -> None:
        if not hasattr(self, "note_table"):
            return
        self.note_table.horizontalHeader().resizeSection(
            1,
            note_date_column_width(self.note_table.fontMetrics()),
        )

    def handle_note_table_clicked(self, row: int, _column: int) -> None:
        item = self.note_table.item(row, 0)
        if item is None or is_multi_select_click():
            self.update_delete_button_state()
            return
        if not self.force_save_current_note(refresh_tree=False):
            return
        self.load_note(int(item.data(Qt.UserRole)))

    def selected_note_ids(self) -> list[int]:
        rows = sorted({index.row() for index in self.note_table.selectionModel().selectedRows()})
        return [
            int(self.note_table.item(row, 0).data(Qt.UserRole))
            for row in rows
            if self.note_table.item(row, 0) is not None
            and self.note_table.item(row, 0).data(Qt.UserRole) is not None
        ]

    def update_delete_button_state(self) -> None:
        if not hasattr(self, "delete_note_button"):
            return
        count = len(self.selected_note_ids())
        in_trash = self.current_sidebar_mode == "trash"
        self.delete_note_button.setVisible(not in_trash)
        self.delete_note_button.setEnabled(not in_trash and count > 0)

    def open_note_table_context_menu(self, position) -> None:
        item = self.note_table.itemAt(position)
        if item is None or item.data(Qt.UserRole) is None:
            return
        row = item.row()
        note_id = int(self.note_table.item(row, 0).data(Qt.UserRole))
        selected_ids = self.selected_note_ids()
        if note_id not in selected_ids:
            self.note_table.clearSelection()
            self.note_table.selectRow(row)
            selected_ids = [note_id]
        menu = QMenu(self)
        if len(selected_ids) > 1:
            menu.addAction("移入回收站", self.confirm_delete_selected_notes)
        else:
            note = get_note(note_id)
            menu.addAction(
                "取消固定" if note and note["is_pinned"] else "固定笔记",
                lambda: self.toggle_note_pinned(note_id, not bool(note and note["is_pinned"])),
            )
            menu.addAction("重命名笔记", lambda: self.prompt_rename_note(note_id))
            menu.addAction("删除笔记", lambda: self.confirm_delete_note(note_id))
        menu.exec(self.note_table.viewport().mapToGlobal(position))

    def confirm_delete_selected_notes(self) -> None:
        note_ids = self.selected_note_ids()
        if not note_ids:
            return
        self.delete_notes_to_trash(note_ids)

    def handle_search_text_changed(self) -> None:
        self.search_timer.start()

    def stop_pending_search_timer(self) -> None:
        if self.search_timer.isActive():
            self.search_timer.stop()

    def apply_search_filter(self) -> None:
        query = self.search_input.text().strip()
        if (
            self.current_sidebar_mode == "smart_view"
            and self.current_filter_value == "ai"
        ):
            self.display_smart_view("ai")
            return
        if not query:
            self.refresh_directory_tree(self.current_note["id"] if self.current_note else None)
            return
        if query.startswith("#") and len(query) > 1:
            tag = query[1:].strip()
            self.display_flat_notes(
                f"标签：#{tag}",
                list_notes_by_tag(tag),
                mode="tag",
                value=tag,
                selected_note_id=self.current_note["id"] if self.current_note else None,
            )
            return
        self.display_flat_notes(
            f"搜索：{query}",
            search_notes(query),
            mode="search",
            value=query,
            selected_note_id=self.current_note["id"] if self.current_note else None,
        )

    def show_tag_results(self, tag: str) -> None:
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        self.display_flat_notes(
            f"标签：#{tag}",
            list_notes_by_tag(tag),
            mode="tag",
            value=tag,
            selected_note_id=self.current_note["id"] if self.current_note else None,
        )

    def show_smart_view(self, view_name: str) -> None:
        if self.current_sidebar_mode == "smart_view" and self.current_filter_value == view_name:
            self.return_to_directory()
            return
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        self.display_smart_view(view_name)

    def display_smart_view(self, view_name: str) -> None:
        actor_target_id = (
            self.selected_ai_actor_id()
            if view_name == "ai"
            else None
        )
        query = self.search_input.text().strip() if view_name == "ai" else ""
        title, notes = get_smart_view_notes(
            view_name,
            actor_target_id=actor_target_id,
            query=query,
        )
        self.display_flat_notes(
            f"智能视图：{title}",
            notes,
            mode="smart_view",
            value=view_name,
            selected_note_id=self.current_note["id"] if self.current_note else None,
        )

    def selected_ai_actor_id(self) -> str | None:
        actor = self.ai_actor_filter_combo.currentData()
        return str(actor) if actor else None

    def handle_ai_actor_filter_changed(self, _index: int = 0) -> None:
        if (
            self.current_sidebar_mode == "smart_view"
            and self.current_filter_value == "ai"
        ):
            self.display_smart_view("ai")

    def show_trash(self) -> None:
        if self.current_sidebar_mode != "trash":
            self.note_id_before_trash = self.current_note["id"] if self.current_note else None
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        self.set_sidebar_mode("trash", label="回收站")
        self.sidebar_stack.setCurrentWidget(self.trash_view)
        trash_tree = list_trash_tree()
        self.directory_tree.blockSignals(True)
        self.directory_tree.clear()
        self.populate_trash_tree(trash_tree)
        self.directory_tree.expandAll()
        self.directory_tree.blockSignals(False)
        self.update_delete_button_state()

    def exit_trash(self) -> None:
        if not self.force_save_current_note(refresh_tree=False):
            return
        note_id = self.note_id_before_trash
        self.note_id_before_trash = None
        self.return_to_directory()
        if note_id is not None and get_note(note_id) is not None:
            self.load_note(note_id)
            self.refresh_directory_tree(note_id)
        elif self.current_note is None:
            self.clear_editor()
        self.update_delete_button_state()

    def open_category_manager(self) -> None:
        if not self.force_save_current_note(refresh_tree=False):
            return
        dialog = CategoryManagerDialog(self)
        dialog.exec()

    def populate_trash_tree(self, trash_tree: dict) -> None:
        directories = trash_tree["directories"]
        notes = trash_tree["notes"]
        if not directories and not notes:
            empty_item = QTreeWidgetItem(["回收站为空"])
            empty_item.setFlags(Qt.ItemIsEnabled)
            self.directory_tree.addTopLevelItem(empty_item)
            return
        directory_items: dict[int, QTreeWidgetItem] = {}
        directory_paths = {tuple(directory["path"]): directory for directory in directories}

        for directory in sorted(directories, key=lambda item: (len(item["path"]), item["path"])):
            source = " / ".join(directory["source_path"])
            label = directory["name"]
            if source:
                label = f"{label} · 来自 {source}"
            item = QTreeWidgetItem([label])
            item.setData(
                0,
                Qt.UserRole,
                {"type": "deleted_directory", "id": directory["id"]},
            )
            configure_trash_item(item)
            directory_items[directory["id"]] = item
            parent_item = directory_items.get(directory["parent_id"])
            if parent_item is not None:
                parent_item.addChild(item)
            else:
                self.directory_tree.addTopLevelItem(item)

        for note in notes:
            note_item = QTreeWidgetItem([note["title"] or "未命名笔记"])
            note_item.setData(0, Qt.UserRole, {"type": "deleted_note", "id": note["id"]})
            configure_trash_item(note_item)
            parent_item = self.trash_parent_item_for_note(
                note, directory_paths, directory_items
            )
            if parent_item is None:
                self.directory_tree.addTopLevelItem(note_item)
            else:
                parent_item.addChild(note_item)

    def trash_parent_item_for_note(
        self,
        note: dict,
        directory_paths: dict[tuple[str, ...], dict],
        directory_items: dict[int, QTreeWidgetItem],
    ) -> QTreeWidgetItem | None:
        raw_path = note.get("path") or []
        candidate_paths = [
            tuple(raw_path[:3]),
            tuple(raw_path[:2]),
            tuple(raw_path[:1]),
        ]
        for path in candidate_paths:
            directory = directory_paths.get(path)
            if directory is not None:
                return directory_items.get(directory["id"])
        return None

    def set_sidebar_mode(
        self, mode: str, value: str = "", label: str | None = None
    ) -> None:
        self.stop_pending_search_timer()
        self.current_sidebar_mode = mode
        self.current_filter_value = value
        if hasattr(self, "ai_actor_filter_widget"):
            self.ai_actor_filter_widget.setVisible(
                mode == "smart_view" and value == "ai"
            )
        self.update_smart_tab_states()

    def update_smart_tab_states(self) -> None:
        if not hasattr(self, "smart_buttons"):
            return
        for view_name, button in self.smart_buttons.items():
            active = (
                self.current_sidebar_mode == "smart_view"
                and self.current_filter_value == view_name
            )
            button.setProperty("active", active)
            button.blockSignals(True)
            button.setChecked(active)
            button.blockSignals(False)
            button.style().unpolish(button)
            button.style().polish(button)

    def return_to_directory(self, keep_filters: bool = False) -> None:
        self.stop_pending_search_timer()
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        if not keep_filters:
            self.populate_filter_controls()
        selected_note_id = self.current_note["id"] if self.current_note else None
        if selected_note_id is not None and get_note(selected_note_id) is None:
            selected_note_id = None
        self.refresh_directory_tree(selected_note_id)

    def handle_trash_item_clicked(
        self, item: QTreeWidgetItem, column: int = 0
    ) -> None:
        data = item.data(0, Qt.UserRole)
        if not data:
            return
        if data["type"] == "deleted_note":
            if is_multi_select_click():
                return
            if not self.force_save_current_note(refresh_tree=False):
                return
            self.load_deleted_note(data["id"])
        if data["type"] == "deleted_directory":
            item.setExpanded(not item.isExpanded())

    def handle_create_note(self) -> None:
        if not self.force_save_current_note(refresh_tree=False):
            return
        note = create_note_in_directory(*self.selected_note_target_path())
        self.load_note(note["id"])
        self.refresh_directory_tree(note["id"])

    def selected_note_target_path(self) -> tuple[str, str, str]:
        if self.current_note is not None:
            return self.note_path_tuple(self.current_note) + (DEFAULT_SUB_CATEGORY,) * (3 - len(self.note_path_tuple(self.current_note)))
        main = self.main_filter_combo.currentText()
        if main and main != "全部":
            sub = self.sub_filter_combo.currentText()
            sub_sub = self.sub_sub_filter_combo.currentText()
            return (main, DEFAULT_SUB_CATEGORY if sub == "-" else sub, DEFAULT_SUB_SUB_CATEGORY if sub_sub == "-" else sub_sub)
        return (DEFAULT_MAIN_CATEGORY, DEFAULT_SUB_CATEGORY, DEFAULT_SUB_SUB_CATEGORY)

    def selected_export_directory_path(self) -> tuple[str, ...]:
        if self.current_sidebar_mode != "directory":
            return ()
        main = self.main_filter_combo.currentText()
        if not main or main == "全部":
            return ()
        path = [main]
        sub = self.sub_filter_combo.currentText()
        if not sub or sub == "-":
            return tuple(path)
        path.append(sub)
        sub_sub = self.sub_sub_filter_combo.currentText()
        if sub_sub and sub_sub != "-":
            path.append(sub_sub)
        return tuple(path)

    def note_path_tuple(self, note: dict) -> tuple[str, ...]:
        path = [note["main_category"]]
        if note["sub_category"] != DEFAULT_SUB_CATEGORY:
            path.append(note["sub_category"])
        if note["sub_sub_category"] != DEFAULT_SUB_SUB_CATEGORY:
            path.append(note["sub_sub_category"])
        return tuple(path)

    def visible_note_ids(self) -> list[int]:
        return [int(self.note_table.item(row, 0).data(Qt.UserRole)) for row in range(self.note_table.rowCount()) if self.note_table.item(row, 0) and self.note_table.item(row, 0).data(Qt.UserRole) is not None]

    def open_export_center(self) -> None:
        if not self.force_save_current_note(refresh_tree=False):
            return
        context = ExportContext(
            current_note_id=self.current_note["id"] if self.current_note else None,
            current_directory_path=self.selected_export_directory_path(),
            sidebar_mode=self.current_sidebar_mode,
            filter_value=self.current_filter_value,
            search_query=self.search_input.text().strip(),
            visible_note_ids=self.visible_note_ids(),
        )
        dialog = ExportCenterDialog(context, self)
        dialog.exec()

    def configure_gale_auto_publish(self) -> None:
        enabled = bool(
            self.settings.value(
                self.GALE_AUTO_PUBLISH_SETTING,
                False,
                type=bool,
            )
        )
        self.gale_auto_scheduler.enabled = enabled
        if enabled:
            self.gale_watch_timer.start()
            self.check_gale_auto_publish()
        else:
            self.gale_watch_timer.stop()
        try:
            QNetworkInformation.loadDefaultBackend()
            network_information = QNetworkInformation.instance()
            if network_information is not None:
                self.gale_network_information = network_information
                self.gale_network_online = (
                    network_information.reachability()
                    == QNetworkInformation.Reachability.Online
                )
                network_information.reachabilityChanged.connect(
                    self.handle_gale_network_reachability
                )
        except Exception:
            self.gale_network_information = None

    def set_gale_auto_publish_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        self.settings.setValue(
            self.GALE_AUTO_PUBLISH_SETTING,
            enabled,
        )
        self.settings.sync()
        if enabled == self.gale_auto_scheduler.enabled:
            return
        if enabled:
            self.gale_auto_scheduler.enable()
            self.gale_watch_timer.start()
            self.check_gale_auto_publish()
        else:
            self.gale_auto_scheduler.disable()
            self.gale_watch_timer.stop()

    def configure_gale_auto_pull(self) -> None:
        self.gale_auto_pull_enabled = bool(
            self.settings.value(
                self.GALE_AUTO_PULL_SETTING,
                True,
                type=bool,
            )
        )
        if DATA_ROOT_OVERRIDDEN or not getattr(sys, "frozen", False):
            return
        self.gale_pull_state_timer.start()
        QTimer.singleShot(0, self.start_gale_auto_pull_check)

    def set_gale_auto_pull_enabled(self, enabled: bool) -> None:
        self.gale_auto_pull_enabled = bool(enabled)
        self.settings.setValue(
            self.GALE_AUTO_PULL_SETTING,
            self.gale_auto_pull_enabled,
        )
        self.settings.sync()
        if (
            self.gale_auto_pull_enabled
            and not DATA_ROOT_OVERRIDDEN
            and getattr(sys, "frozen", False)
        ):
            self.start_gale_auto_pull_check()

    def start_gale_auto_pull_check(self) -> None:
        if DATA_ROOT_OVERRIDDEN or not getattr(sys, "frozen", False):
            return
        try:
            self.gale_auto_pull_installer_pid = start_bundled_installer()
        except BackgroundPullError as error:
            try:
                record_background_error(background_paths(), error.code)
            except BackgroundPullError:
                pass

    def gale_auto_pull_status_text(self) -> str:
        if DATA_ROOT_OVERRIDDEN:
            return "隔离数据环境中不启动生产自动接收"
        return background_status_text(background_paths().state)

    def refresh_gale_auto_pull_state(self, *, force: bool = False) -> None:
        if DATA_ROOT_OVERRIDDEN:
            return
        state_path = background_paths().state
        try:
            stat_result = state_path.stat()
            signature = (stat_result.st_mtime_ns, stat_result.st_size)
        except OSError:
            signature = None
        if not force and signature == self.gale_pull_state_signature:
            return
        self.gale_pull_state_signature = signature
        self.refresh_ai_center_badge()
        dialog = self.receipt_inbox_dialog
        if dialog is not None:
            try:
                dialog.refresh_pending_silently()
            except RuntimeError:
                self.receipt_inbox_dialog = None

    def publish_to_gale_now(self) -> None:
        self.start_gale_publish(manual=True)

    def set_gale_publish_control_enabled(self, enabled: bool) -> None:
        dialog = self.receipt_inbox_dialog
        if dialog is None:
            return
        try:
            dialog.set_remote_publish_enabled(enabled)
        except RuntimeError:
            self.receipt_inbox_dialog = None

    def start_gale_publish(self, *, manual: bool) -> None:
        if (
            self.gale_publish_thread is not None
            and self.gale_publish_thread.isRunning()
        ):
            if manual:
                self.status_label.setText("正在发布给 Gale")
            return
        self.gale_auto_scheduler.mark_started()
        self.set_gale_publish_control_enabled(False)
        thread = GalePublishThread(
            GalePublishConfig(
                mirror_path=GALE_MIRROR_PATH,
                state_path=GALE_PUBLISH_STATE_PATH,
                lock_path=GALE_PUBLISH_LOCK_PATH,
            ),
            manual=manual,
        )
        self.gale_publish_thread = thread
        thread.finished.connect(self.handle_gale_publish_finished)
        thread.start()
        self.status_label.setText("正在发布给 Gale")

    def handle_gale_publish_finished(self) -> None:
        thread = self.gale_publish_thread
        if thread is None:
            return
        result = thread.result or GalePublishResult(
            status="failed",
            sha256="",
            note_count=0,
            reason="unexpected_failure",
            previous_preserved=False,
        )
        manual = thread.manual
        thread.deleteLater()
        self.gale_publish_thread = None
        self.set_gale_publish_control_enabled(True)

        if result.status in {"success", "duplicate"}:
            if result.sha256:
                self.gale_auto_scheduler.mark_success(result.sha256)
            else:
                self.gale_auto_scheduler.running = False
            self.status_label.setText("已发布给 Gale")
        else:
            self.gale_auto_scheduler.mark_failure(time.time())
            friendly_reason = self.gale_publish_error_text(result.reason)
            if result.previous_preserved:
                message = f"发布失败：{friendly_reason}。继续使用上一版。"
            else:
                message = f"发布失败：{friendly_reason}。请停止并检查回滚状态。"
            self.status_label.setText(message)
            if manual:
                QMessageBox.warning(self, "发布失败", message)

        if self.gale_close_after_publish:
            self.gale_close_after_publish = False
            QTimer.singleShot(0, self.close)

    def check_gale_auto_publish(
        self,
        *,
        network_restored: bool = False,
    ) -> None:
        if not self.gale_auto_scheduler.enabled:
            return
        now = time.time()
        resumed = (
            now - self.gale_last_watch_wall_time
            > (self.GALE_WATCH_INTERVAL_MS / 1000) * 2
        )
        self.gale_last_watch_wall_time = now
        try:
            file_stat = GALE_MIRROR_PATH.stat()
            signature = (file_stat.st_mtime_ns, file_stat.st_size)
            if signature != self.gale_source_signature:
                digest = sha256_file(GALE_MIRROR_PATH)
                self.gale_source_signature = signature
                self.gale_auto_scheduler.observe(digest, now)
        except (FileNotFoundError, OSError):
            return

        if self.gale_auto_scheduler.should_publish(
            now,
            resumed=resumed,
            network_restored=network_restored,
        ):
            self.start_gale_publish(manual=False)

    def handle_gale_network_reachability(
        self,
        reachability: QNetworkInformation.Reachability,
    ) -> None:
        online = reachability == QNetworkInformation.Reachability.Online
        restored = online and not self.gale_network_online
        self.gale_network_online = online
        if restored:
            self.check_gale_auto_publish(network_restored=True)

    @staticmethod
    def gale_publish_error_text(reason: str) -> str:
        messages = {
            "local_validation_failed": "本地安全镜像验证未通过",
            "upload_failed": "无法上传",
            "activation_unreachable": "无法连接远端发布服务",
            "publisher_busy": "已有发布正在进行",
            "remote_validation_failed": "远端安全镜像验证未通过",
            "activation_failed_rolled_back": "新版本未能启动",
            "rollback_failed": "上一版恢复检查未通过",
            "unexpected_failure": "发生未预期错误",
        }
        return messages.get(reason, "发布未完成")

    def open_receipt_inbox(self) -> None:
        self.stop_pending_search_timer()
        if not self.force_save_current_note(refresh_tree=False):
            return
        if self.receipt_inbox_dialog is not None:
            try:
                self.receipt_inbox_dialog.show()
                self.receipt_inbox_dialog.raise_()
                self.receipt_inbox_dialog.activateWindow()
                return
            except RuntimeError:
                self.receipt_inbox_dialog = None
        dialog = AiCenterDialog(
            self,
            auto_publish_enabled=self.gale_auto_scheduler.enabled,
            set_auto_publish_enabled=self.set_gale_auto_publish_enabled,
            publish_now=self.publish_to_gale_now,
            auto_pull_enabled=self.gale_auto_pull_enabled,
            set_auto_pull_enabled=self.set_gale_auto_pull_enabled,
            auto_pull_status=self.gale_auto_pull_status_text,
        )
        self.receipt_inbox_dialog = dialog
        dialog.set_remote_publish_enabled(
            not (
                self.gale_publish_thread is not None
                and self.gale_publish_thread.isRunning()
            )
        )
        dialog.finished.connect(
            lambda _result, target=dialog: self.handle_receipt_inbox_closed(target)
        )
        dialog.destroyed.connect(
            lambda _object=None, target=dialog: self.clear_receipt_inbox_reference(target)
        )
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def handle_receipt_inbox_closed(self, dialog: AiCenterDialog) -> None:
        if self.receipt_inbox_dialog is dialog:
            self.receipt_inbox_dialog = None
        self.refresh_current_sidebar_view(
            self.current_note["id"] if self.current_note else None
        )
        self.refresh_ai_center_badge()

    def clear_receipt_inbox_reference(
        self, dialog: AiCenterDialog | None = None
    ) -> None:
        if dialog is not None and self.receipt_inbox_dialog is not dialog:
            return
        self.receipt_inbox_dialog = None
        try:
            self.receipt_inbox_button.setProperty("active", False)
            self.receipt_inbox_button.setChecked(False)
            self.receipt_inbox_button.style().unpolish(self.receipt_inbox_button)
            self.receipt_inbox_button.style().polish(self.receipt_inbox_button)
        except RuntimeError:
            pass

    def refresh_ai_center_badge(self) -> None:
        if not hasattr(self, "receipt_inbox_button"):
            return
        pending_count = len(list_ai_receipt_inbox_items(status="pending"))
        self.receipt_inbox_button.setText(
            f"AI中心 ({pending_count})" if pending_count else "AI中心"
        )

    def toggle_link_drawer(self) -> None:
        self.link_drawer_visible = not self.link_drawer_visible
        self.link_drawer.setVisible(self.link_drawer_visible)
        self.link_drawer_button.setProperty("active", self.link_drawer_visible)
        self.link_drawer_button.setToolTip(
            "关闭关联" if self.link_drawer_visible else "打开关联"
        )
        self.link_drawer_button.style().unpolish(self.link_drawer_button)
        self.link_drawer_button.style().polish(self.link_drawer_button)
        if self.link_drawer_visible:
            self.refresh_link_drawer()

    def toggle_current_note_pinned(self) -> None:
        if self.current_note is None:
            return
        self.toggle_note_pinned(self.current_note["id"], not bool(self.current_note["is_pinned"]))
        refreshed = get_note(self.current_note["id"])
        if refreshed is not None:
            self.current_note = refreshed
        self.update_note_action_states()

    def update_note_action_states(self) -> None:
        has_note = self.current_note is not None
        self.pin_button.setEnabled(has_note)
        self.link_drawer_button.setEnabled(has_note)
        self.export_button.setEnabled(has_note)
        self.pin_button.setProperty("active", has_note and bool(self.current_note.get("is_pinned")))
        self.pin_button.setToolTip(
            "取消固定" if has_note and self.current_note.get("is_pinned") else "固定笔记"
        )
        self.pin_button.style().unpolish(self.pin_button)
        self.pin_button.style().polish(self.pin_button)
        self.update_delete_button_state()

    def refresh_link_drawer(self) -> None:
        if not hasattr(self, "link_drawer"):
            return
        current_note_id = self.current_note["id"] if self.current_note else None
        self.link_drawer.refresh(current_note_id)

    def open_linked_note(self, note_id: int) -> None:
        if not self.force_save_current_note(refresh_tree=False):
            return
        self.load_note(note_id)
        self.refresh_directory_tree(note_id)
        self.refresh_link_drawer()

    def load_note(self, note_id: int) -> None:
        note = get_note(note_id)
        if note is None:
            self.status_label.setText("保存失败：笔记不存在")
            self.refresh_directory_tree()
            return

        self.is_loading_note = True
        self.current_note = note
        self.title_input.setText(note["title"])
        self.body_edit.set_note_content(note["content_html"])
        self.populate_category_controls(note)
        self.tags_input.setText(format_tags(get_note_tags(note_id)))
        self.set_ai_visibility_control(note["ai_visibility"])
        self.set_ai_target_controls(get_note_ai_targets(note_id))
        self.update_metadata_summary()
        self.set_metadata_expanded(False)
        self.status_label.setText(
            f"已保存 · 更新于 {format_display_datetime(note['updated_at'])}"
        )
        self.has_unsaved_changes = False
        self.autosave_retry_count = 0
        self.save_timer.stop()
        self.set_editor_enabled(True)
        self.update_note_action_states()
        self.is_loading_note = False
        self.refresh_link_drawer()

    def load_deleted_note(self, note_id: int) -> None:
        note = get_note(note_id, include_deleted=True)
        if note is None:
            self.show_trash()
            return
        self.is_loading_note = True
        self.current_note = None
        self.title_input.setText(note["title"])
        self.body_edit.set_note_content(note["content_html"])
        self.populate_category_controls(note)
        self.tags_input.setText(format_tags(get_note_tags(note_id)))
        self.set_ai_visibility_control(note["ai_visibility"])
        self.set_ai_target_controls(get_note_ai_targets(note_id))
        self.update_metadata_summary()
        self.set_metadata_expanded(False)
        self.status_label.setText("回收站中 · 只读")
        self.has_unsaved_changes = False
        self.autosave_retry_count = 0
        self.save_timer.stop()
        self.set_editor_enabled(False, show_content=True)
        self.is_loading_note = False
        self.refresh_link_drawer()

    def populate_category_controls(self, note: dict) -> None:
        self.reload_directory_cache()
        self.category_controls_loading = True
        self.set_combo_items(
            self.main_category_combo,
            self.directory_names(None),
            note["main_category"],
        )
        main_id = self.directory_id_for_path((self.main_category_combo.currentText(),))
        self.set_combo_items(
            self.sub_category_combo,
            [DEFAULT_SUB_CATEGORY, *self.directory_names(main_id)],
            note["sub_category"],
        )
        sub_id = None
        if self.sub_category_combo.currentText() != DEFAULT_SUB_CATEGORY:
            sub_id = self.directory_id_for_path(
                (
                    self.main_category_combo.currentText(),
                    self.sub_category_combo.currentText(),
                )
            )
        sub_sub_names = [DEFAULT_SUB_SUB_CATEGORY]
        if sub_id is not None:
            sub_sub_names.extend(self.directory_names(sub_id))
        self.set_combo_items(
            self.sub_sub_category_combo,
            sub_sub_names,
            note["sub_sub_category"],
        )
        self.category_controls_loading = False
        self.update_metadata_summary()

    def metadata_summary_text(self) -> str:
        main = self.main_category_combo.currentText() or DEFAULT_MAIN_CATEGORY
        sub = self.sub_category_combo.currentText() or DEFAULT_SUB_CATEGORY
        sub_sub = self.sub_sub_category_combo.currentText() or DEFAULT_SUB_SUB_CATEGORY
        path_parts = [main]
        if sub != DEFAULT_SUB_CATEGORY:
            path_parts.append(sub)
        if sub_sub != DEFAULT_SUB_SUB_CATEGORY:
            path_parts.append(sub_sub)
        tag_names = parse_tags(self.tags_input.text())
        tags = " ".join(f"#{tag}" for tag in tag_names) if tag_names else "无标签"
        targets = compact_ai_target_summary(self.selected_ai_targets())
        visibility = self.ai_visibility_combo.currentText()
        return (
            f"{' / '.join(path_parts)} · {tags} · "
            f"AI对象：{targets} · AI可见性：{visibility}"
        )

    def update_metadata_summary(self) -> None:
        if not hasattr(self, "metadata_summary_button"):
            return
        self.metadata_summary_button.setText(self.metadata_summary_text())

    def toggle_metadata_panel(self) -> None:
        self.set_metadata_expanded(not self.metadata_expanded)

    def set_metadata_expanded(self, expanded: bool) -> None:
        self.metadata_expanded = expanded
        if hasattr(self, "metadata_panel"):
            self.metadata_panel.setVisible(expanded)

    def configure_combo_popup(
        self, combo: FloatingComboBox, minimum_popup_width: int
    ) -> None:
        combo.configure_popup(minimum_popup_width)

    def set_combo_items(
        self, combo: FloatingComboBox, values: list[str], current_value: str
    ) -> None:
        combo.blockSignals(True)
        combo.clear()
        options = list(values)
        if current_value and current_value not in options:
            options.append(current_value)
            options.sort(key=str.casefold)
        combo.addItems(options)
        index = combo.findText(current_value)
        if index >= 0:
            combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def set_ai_visibility_control(self, visibility: str) -> None:
        self.ai_visibility_combo.blockSignals(True)
        index = self.ai_visibility_combo.findText(visibility_display(visibility))
        self.ai_visibility_combo.setCurrentIndex(index if index >= 0 else 0)
        self.ai_visibility_combo.blockSignals(False)

    def set_ai_target_controls(self, targets: list[str]) -> None:
        target_set = set(targets)
        for target_name, checkbox in self.ai_target_checks.items():
            checkbox.blockSignals(True)
            checkbox.setChecked(target_name in target_set)
            checkbox.blockSignals(False)

    def selected_ai_targets(self) -> list[str]:
        return [
            target_name
            for target_name, checkbox in self.ai_target_checks.items()
            if checkbox.isChecked()
        ]

    def directory_names(self, parent_id: int | None) -> list[str]:
        return [
            row["name"]
            for row in self.display_children_for_directory(parent_id)
            if row["name"] != DEFAULT_SUB_CATEGORY
        ]

    def directory_id_for_path(self, path: tuple[str, ...]) -> int | None:
        parent_id = None
        current_id = None
        for name in path:
            directory = self.find_directory_by_name(name, parent_id)
            if directory is None:
                return None
            current_id = directory["id"]
            parent_id = current_id
        return current_id

    def find_directory_by_name(
        self, name: str, parent_id: int | None
    ) -> dict | None:
        for row in self.children_by_parent.get(parent_id, []):
            if row["name"] == name:
                return row
        return None

    def handle_main_category_changed(self) -> None:
        if self.category_controls_loading or self.current_note is None:
            return
        self.category_controls_loading = True
        main_id = self.directory_id_for_path((self.main_category_combo.currentText(),))
        sub_names = [DEFAULT_SUB_CATEGORY, *self.directory_names(main_id)]
        self.set_combo_items(self.sub_category_combo, sub_names, DEFAULT_SUB_CATEGORY)
        sub_sub_names = [DEFAULT_SUB_SUB_CATEGORY]
        self.set_combo_items(
            self.sub_sub_category_combo, sub_sub_names, DEFAULT_SUB_SUB_CATEGORY
        )
        self.category_controls_loading = False
        self.update_metadata_summary()
        self.mark_note_dirty()

    def handle_sub_category_changed(self) -> None:
        if self.category_controls_loading or self.current_note is None:
            return
        self.category_controls_loading = True
        sub_id = None
        if self.sub_category_combo.currentText() != DEFAULT_SUB_CATEGORY:
            sub_id = self.directory_id_for_path(
                (
                    self.main_category_combo.currentText(),
                    self.sub_category_combo.currentText(),
                )
            )
        sub_sub_names = [DEFAULT_SUB_SUB_CATEGORY, *self.directory_names(sub_id)]
        self.set_combo_items(
            self.sub_sub_category_combo, sub_sub_names, DEFAULT_SUB_SUB_CATEGORY
        )
        self.category_controls_loading = False
        self.update_metadata_summary()
        self.mark_note_dirty()

    def handle_sub_sub_category_changed(self) -> None:
        if self.category_controls_loading or self.current_note is None:
            return
        self.update_metadata_summary()
        self.mark_note_dirty()

    def handle_tags_text_changed(self) -> None:
        self.update_metadata_summary()
        self.mark_note_dirty()

    def handle_ai_visibility_changed(self, _value: str = "") -> None:
        self.update_metadata_summary()
        self.mark_note_dirty()

    def handle_ai_targets_changed(self, _state: int = 0) -> None:
        self.update_metadata_summary()
        self.mark_note_dirty()

    def set_editor_enabled(self, enabled: bool, *, show_content: bool | None = None) -> None:
        if show_content is None:
            show_content = enabled
        self.title_input.setEnabled(enabled)
        self.main_category_combo.setEnabled(enabled)
        self.sub_category_combo.setEnabled(enabled)
        self.sub_sub_category_combo.setEnabled(enabled)
        self.ai_visibility_combo.setEnabled(enabled)
        for checkbox in self.ai_target_checks.values():
            checkbox.setEnabled(enabled)
        self.tags_input.setEnabled(enabled)
        self.body_edit.setEnabled(enabled)
        self.editor_toolbar.setEnabled(enabled)
        self.metadata_summary_button.setEnabled(enabled)
        self.metadata_panel.setEnabled(enabled)
        self.title_input.setVisible(show_content)
        self.editor_action_bar.setVisible(enabled)
        self.metadata_summary_button.setVisible(show_content)
        self.metadata_panel.setVisible(show_content and self.metadata_expanded)
        self.editor_toolbar.setVisible(show_content)
        self.body_edit.setVisible(show_content)
        self.title_band.setVisible(show_content)
        self.title_separator.setVisible(show_content)
        self.metadata_section.setVisible(show_content)
        self.body_section.setVisible(show_content)
        self.empty_editor_label.setVisible(not show_content)
        if hasattr(self, "pin_button"):
            self.update_note_action_states()

    def mark_note_dirty(self) -> None:
        if self.is_loading_note or self.current_note is None:
            return
        self.has_unsaved_changes = True
        self.autosave_retry_count = 0
        self.status_label.setText("未保存")
        self.save_timer.start(self.AUTOSAVE_DELAY_MS)

    def handle_body_document_modification(self, modified: bool) -> None:
        if modified:
            self.mark_note_dirty()

    def force_save_current_note(self, *, refresh_tree: bool = True) -> bool:
        self.save_timer.stop()
        return self.save_current_note(refresh_tree=refresh_tree)

    def schedule_autosave_retry(self) -> None:
        if (
            self.current_note is None
            or not self.has_unsaved_changes
            or self.autosave_retry_count >= len(self.AUTOSAVE_RETRY_DELAYS_MS)
        ):
            return
        delay = self.AUTOSAVE_RETRY_DELAYS_MS[self.autosave_retry_count]
        self.autosave_retry_count += 1
        self.save_timer.start(delay)

    def save_current_note(self, *, refresh_tree: bool = True) -> bool:
        if self.current_note is None or not self.has_unsaved_changes:
            return True

        self.status_label.setText("保存中...")
        QApplication.processEvents()
        try:
            note_id = self.current_note["id"]
            note = save_editor_state(
                note_id,
                title=self.title_input.text().strip() or "未命名笔记",
                content_html=self.body_edit.note_content_html(),
                main_category=self.main_category_combo.currentText()
                or DEFAULT_MAIN_CATEGORY,
                sub_category=self.sub_category_combo.currentText() or DEFAULT_SUB_CATEGORY,
                sub_sub_category=self.sub_sub_category_combo.currentText()
                or DEFAULT_SUB_SUB_CATEGORY,
                tags=parse_tags(self.tags_input.text()),
                ai_visibility=self.ai_visibility_combo.currentText(),
                ai_targets=self.selected_ai_targets(),
            )
        except Exception as error:
            self.status_label.setText(f"保存失败：{error}")
            self.has_unsaved_changes = True
            self.schedule_autosave_retry()
            return False

        self.current_note = note
        self.body_edit.document().setModified(False)
        self.has_unsaved_changes = False
        self.autosave_retry_count = 0
        self.save_timer.stop()
        self.status_label.setText(
            f"已保存 · 更新于 {format_display_datetime(note['updated_at'])}"
        )
        if refresh_tree:
            self.refresh_current_sidebar_view(note["id"])
        self.update_metadata_summary()
        self.refresh_tag_filter_row()
        self.refresh_link_drawer()
        return True

    def refresh_current_sidebar_view(self, selected_note_id: int | None = None) -> None:
        if self.current_sidebar_mode == "search":
            self.apply_search_filter()
        elif self.current_sidebar_mode == "tag":
            self.show_tag_results(self.current_filter_value)
        elif self.current_sidebar_mode == "smart_view":
            self.display_smart_view(self.current_filter_value)
        elif self.current_sidebar_mode == "trash":
            self.show_trash()
        else:
            self.refresh_directory_tree(selected_note_id)

    def open_trash_item_context_menu(self, position) -> None:
        item = self.directory_tree.itemAt(position)
        data = item.data(0, Qt.UserRole) if item is not None else None
        menu = QMenu(self)

        if data:
            selected_roots = selected_items_without_selected_ancestors(
                self.directory_tree.selectedItems()
            )
            selected_count = len(selected_roots)
            if selected_count:
                menu.addAction(
                    f"恢复所选（{selected_count}）",
                    self.restore_selected_trash_items,
                )
                menu.addAction(
                    f"永久删除所选（{selected_count}）",
                    self.confirm_permanent_delete_selected_trash_items,
                )
                menu.addSeparator()
        self.add_empty_trash_action(menu)

        menu.exec(self.directory_tree.viewport().mapToGlobal(position))

    def open_trash_root_context_menu(self, position) -> None:
        menu = QMenu(self)
        self.add_empty_trash_action(menu)
        menu.exec(self.trash_button.mapToGlobal(position))

    def add_empty_trash_action(self, menu: QMenu) -> None:
        trash_tree = list_trash_tree()
        action = menu.addAction("清空回收站", self.confirm_empty_trash)
        action.setEnabled(bool(trash_tree["notes"] or trash_tree["directories"]))

    def confirm_empty_trash(self) -> None:
        trash_tree = list_trash_tree()
        note_count = len(trash_tree["notes"])
        directory_count = len(trash_tree["directories"])
        if not note_count and not directory_count:
            self.status_label.setText("回收站为空")
            return

        message_box = QMessageBox(self)
        message_box.setWindowTitle("清空回收站")
        message_box.setText("确定清空回收站吗？")
        message_box.setInformativeText(
            "将永久删除回收站中的全部内容，共：\n"
            f"- {note_count} 条笔记\n"
            f"- {directory_count} 个目录\n\n"
            "此操作无法撤销。"
        )
        cancel_button = message_box.addButton("取消", QMessageBox.RejectRole)
        empty_button = message_box.addButton(
            "清空回收站", QMessageBox.DestructiveRole
        )
        message_box.setDefaultButton(cancel_button)
        message_box.setEscapeButton(cancel_button)
        message_box.exec()
        if message_box.clickedButton() != empty_button:
            return

        try:
            result = empty_trash()
        except Exception as error:
            QMessageBox.warning(self, "清空回收站失败", str(error))
            return
        if self.current_note is None:
            self.clear_editor()
        self.show_trash()
        self.status_label.setText(
            "已清空回收站：永久删除 "
            f"{result['note_count']} 条笔记、{result['directory_count']} 个目录"
        )

    def selected_trash_payloads(self) -> tuple[list[int], list[int]]:
        roots = selected_items_without_selected_ancestors(
            self.directory_tree.selectedItems()
        )
        note_ids: list[int] = []
        directory_ids: list[int] = []
        for item in roots:
            data = item_data(item)
            if data.get("type") == "deleted_note":
                note_ids.append(data["id"])
            elif data.get("type") == "deleted_directory":
                directory_ids.append(data["id"])
        return note_ids, directory_ids

    def restore_selected_trash_items(self) -> None:
        note_ids, directory_ids = self.selected_trash_payloads()
        if not note_ids and not directory_ids:
            return
        try:
            result = restore_trash_items(note_ids, directory_ids)
        except Exception as error:
            QMessageBox.warning(self, "恢复失败", str(error))
            return
        if self.current_note is None or get_note(
            self.current_note["id"], include_deleted=True
        ) is None:
            self.clear_editor()
        self.show_trash()
        self.status_label.setText(f"已恢复 {result['processed_count']} 个项目")

    def confirm_permanent_delete_selected_trash_items(self) -> None:
        note_ids, directory_ids = self.selected_trash_payloads()
        selected_count = len(note_ids) + len(directory_ids)
        if not selected_count:
            return
        if not self.confirm_continue(
            "永久删除所选",
            f"确定永久删除所选的 {selected_count} 个项目吗？\n此操作无法撤销。",
        ):
            return
        try:
            result = permanently_delete_trash_items(note_ids, directory_ids)
        except Exception as error:
            QMessageBox.warning(self, "永久删除失败", str(error))
            return
        if self.current_note is None or get_note(
            self.current_note["id"], include_deleted=True
        ) is None:
            self.clear_editor()
        self.show_trash()
        self.status_label.setText(f"已永久删除 {result['processed_count']} 个项目")

    def prompt_create_directory(self, parent_id: int | None) -> None:
        name, accepted = QInputDialog.getText(self, "新增目录", "目录名称")
        if not accepted:
            return
        try:
            create_directory(name, parent_id)
        except Exception as error:
            QMessageBox.warning(self, "新增目录失败", str(error))
            return
        self.refresh_after_directory_change()

    def prompt_rename_directory(self, directory_id: int) -> None:
        current_name = self.directory_name_by_id(directory_id)
        name, accepted = QInputDialog.getText(
            self, "重命名目录", "目录名称", text=current_name
        )
        if not accepted:
            return
        try:
            rename_directory(directory_id, name)
        except Exception as error:
            QMessageBox.warning(self, "重命名目录失败", str(error))
            return
        self.reload_current_note()
        self.refresh_after_directory_change()

    def prompt_rename_note(self, note_id: int) -> None:
        note = get_note(note_id)
        if note is None:
            self.refresh_directory_tree()
            return
        title, accepted = QInputDialog.getText(
            self, "重命名笔记", "笔记标题", text=note["title"]
        )
        if not accepted:
            return
        clean_title = title.strip() or "未命名笔记"
        is_current_note = bool(
            self.current_note and self.current_note["id"] == note_id
        )
        if clean_title == note["title"]:
            if (
                is_current_note
                and self.has_unsaved_changes
                and not self.force_save_current_note(refresh_tree=False)
            ):
                return
            return
        if (
            is_current_note
            and not self.force_save_current_note(refresh_tree=False)
        ):
            return
        try:
            renamed = rename_note(note_id, clean_title)
        except Exception as error:
            QMessageBox.warning(self, "重命名笔记失败", str(error))
            return
        if self.current_note and self.current_note["id"] == note_id:
            self.load_note(note_id)
        else:
            self.current_note = renamed if self.current_note is None else self.current_note
        self.refresh_directory_tree(note_id)

    def toggle_note_pinned(self, note_id: int, pinned: bool) -> None:
        try:
            note = set_note_pinned(note_id, pinned)
        except Exception as error:
            QMessageBox.warning(self, "固定笔记失败", str(error))
            return
        if self.current_note and self.current_note["id"] == note_id:
            self.current_note = note
        self.refresh_current_sidebar_view(note_id)

    def confirm_delete_note(self, note_id: int) -> None:
        note = get_note(note_id)
        if note is None:
            self.refresh_directory_tree()
            return
        self.delete_notes_to_trash([note_id])

    def delete_notes_to_trash(self, note_ids: list[int]) -> None:
        ordered_ids = list(dict.fromkeys(int(note_id) for note_id in note_ids))
        if not ordered_ids:
            return
        if not self.force_save_current_note(refresh_tree=False):
            return

        deleted_ids = set(ordered_ids)
        current_id = self.current_note["id"] if self.current_note else None
        visible_ids = self.visible_note_ids()
        adjacent_id = None
        if current_id in deleted_ids and current_id in visible_ids:
            current_index = visible_ids.index(current_id)
            remaining_ids = [
                note_id for note_id in visible_ids if note_id not in deleted_ids
            ]
            if remaining_ids:
                adjacent_id = remaining_ids[min(current_index, len(remaining_ids) - 1)]

        try:
            for target_id in ordered_ids:
                soft_delete_note(target_id)
        except Exception as error:
            QMessageBox.warning(self, "删除笔记失败", str(error))
            return

        if current_id in deleted_ids:
            if adjacent_id is not None:
                self.load_note(adjacent_id)
            else:
                self.clear_editor()

        selected_id = (
            adjacent_id
            if current_id in deleted_ids
            else (self.current_note["id"] if self.current_note else None)
        )
        self.refresh_current_sidebar_view(selected_id)
        if selected_id is not None:
            self.select_note_in_table(selected_id)
        self.show_transient_status("已移入回收站")

    def select_note_in_table(self, note_id: int) -> None:
        for row in range(self.note_table.rowCount()):
            item = self.note_table.item(row, 0)
            if item is not None and item.data(Qt.UserRole) == note_id:
                self.note_table.selectRow(row)
                self.note_table.scrollToItem(item)
                return

    def show_transient_status(self, message: str, duration_ms: int = 1800) -> None:
        self.status_label.setText(message)
        QTimer.singleShot(
            duration_ms,
            lambda expected=message: self.restore_status_after_transient(expected),
        )

    def restore_status_after_transient(self, expected: str) -> None:
        if self.status_label.text() != expected:
            return
        if self.current_note is None:
            self.status_label.setText("未选择笔记")
            return
        self.status_label.setText(
            f"已保存 · 更新于 {format_display_datetime(self.current_note['updated_at'])}"
        )

    def restore_deleted_note(self, note_id: int) -> None:
        try:
            note = restore_note(note_id)
        except Exception as error:
            QMessageBox.warning(self, "恢复笔记失败", str(error))
            return
        self.load_note(note["id"])
        self.refresh_directory_tree(note["id"])

    def restore_deleted_directory(self, directory_id: int) -> None:
        try:
            restore_trash_directory(directory_id)
        except Exception as error:
            QMessageBox.warning(self, "恢复目录失败", str(error))
            return
        self.show_trash()

    def confirm_permanent_delete_note(self, note_id: int) -> None:
        note = get_note(note_id, include_deleted=True)
        if note is None:
            self.show_trash()
            return
        if not self.confirm_continue(
            "永久删除笔记",
            f"将永久删除该笔记。此操作不可恢复。是否继续？",
        ):
            return
        try:
            permanently_delete_note(note_id)
        except Exception as error:
            QMessageBox.warning(self, "永久删除失败", str(error))
            return
        self.clear_editor()
        self.show_trash()

    def confirm_permanent_delete_directory(self, directory_id: int) -> None:
        if not self.confirm_continue(
            "永久删除目录",
            "将永久删除该目录及其所有子目录和笔记。此操作不可恢复。是否继续？",
        ):
            return
        try:
            permanently_delete_trash_directory(directory_id)
        except Exception as error:
            QMessageBox.warning(self, "永久删除目录失败", str(error))
            return
        self.clear_editor()
        self.show_trash()

    def confirm_delete_directory(self, directory_id: int) -> None:
        current_name = self.directory_name_by_id(directory_id)
        if not self.confirm_continue(
            "删除目录",
            f"删除“{current_name}”后，该目录及其所有子目录、笔记都会移入回收站。是否继续？",
        ):
            return
        try:
            delete_directory(directory_id)
        except Exception as error:
            QMessageBox.warning(self, "删除目录失败", str(error))
            return
        self.reload_current_note()
        self.refresh_after_directory_change()

    def confirm_continue(self, title: str, text: str) -> bool:
        message_box = QMessageBox(self)
        message_box.setWindowTitle(title)
        message_box.setText(text)
        continue_button = message_box.addButton("继续", QMessageBox.AcceptRole)
        message_box.addButton("取消", QMessageBox.RejectRole)
        message_box.setDefaultButton(continue_button)
        message_box.exec()
        return message_box.clickedButton() == continue_button

    def clear_editor(self) -> None:
        self.is_loading_note = True
        self.current_note = None
        self.title_input.clear()
        self.body_edit.clear()
        self.main_category_combo.clear()
        self.sub_category_combo.clear()
        self.sub_sub_category_combo.clear()
        self.tags_input.clear()
        self.set_ai_visibility_control(AI_VISIBILITY_OPTIONS[0])
        self.set_ai_target_controls([])
        self.update_metadata_summary()
        self.status_label.setText("未选择笔记")
        self.has_unsaved_changes = False
        self.autosave_retry_count = 0
        self.save_timer.stop()
        self.set_editor_enabled(False)
        self.update_note_action_states()
        self.is_loading_note = False
        self.refresh_link_drawer()

    def refresh_after_directory_change(self) -> None:
        previous_main = self.main_filter_combo.currentText()
        previous_sub = self.sub_filter_combo.currentText()
        previous_sub_sub = self.sub_sub_filter_combo.currentText()
        self.reload_directory_cache()

        main_names = self.directory_names(None)
        valid_main = previous_main if previous_main in main_names else "全部"
        main_id = self.directory_id_for_path((valid_main,)) if valid_main != "全部" else None
        sub_names = self.directory_names(main_id) if main_id is not None else []
        valid_sub = previous_sub if previous_sub in sub_names else "-"
        sub_id = (
            self.directory_id_for_path((valid_main, valid_sub))
            if valid_main != "全部" and valid_sub != "-"
            else None
        )
        sub_sub_names = self.directory_names(sub_id) if sub_id is not None else []
        valid_sub_sub = previous_sub_sub if previous_sub_sub in sub_sub_names else "-"

        self.filter_controls_loading = True
        self.set_combo_items(self.main_filter_combo, ["全部", *main_names], valid_main)
        self.set_combo_items(self.sub_filter_combo, ["-", *sub_names], valid_sub)
        self.set_combo_items(
            self.sub_sub_filter_combo, ["-", *sub_sub_names], valid_sub_sub
        )
        self.filter_controls_loading = False

        selected_note_id = self.current_note["id"] if self.current_note else None
        if selected_note_id is not None:
            if get_note(selected_note_id) is None:
                self.clear_editor()
                selected_note_id = None
            else:
                self.load_note(selected_note_id)
        self.set_sidebar_mode("directory")
        self.sidebar_stack.setCurrentWidget(self.note_view)
        self.apply_category_filters(selected_note_id=selected_note_id)

    def reload_current_note(self) -> None:
        if self.current_note is None:
            return
        note = get_note(self.current_note["id"])
        if note is not None:
            self.current_note = note

    def directory_name_by_id(self, directory_id: int) -> str:
        self.reload_directory_cache()
        for row in self.directory_rows:
            if row["id"] == directory_id:
                return row["name"]
        return ""

    def closeEvent(self, event) -> None:
        self.gale_watch_timer.stop()
        self.gale_pull_state_timer.stop()
        self.gale_auto_scheduler.disable()
        if (
            self.gale_publish_thread is not None
            and self.gale_publish_thread.isRunning()
        ):
            self.gale_close_after_publish = True
            self.status_label.setText("正在安全完成 Gale 发布，完成后关闭")
            event.ignore()
            return
        if self.force_save_current_note(refresh_tree=False):
            self.sidebar_clock_timer.stop()
            self._save_sidebar_width()
            self.settings.sync()
            event.accept()
        else:
            event.ignore()


def run_app() -> int:
    app = QApplication(sys.argv)
    apply_theme(app, load_saved_theme())
    window = MainWindow()
    window.show()
    return app.exec()
