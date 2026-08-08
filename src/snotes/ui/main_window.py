"""Package 3 desktop coordinator; UI contains no storage or SQL access."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QInputDialog, QMainWindow, QMessageBox

from src.snotes.application.directory_service import DirectoryService
from src.snotes.application.export_service import ExportService
from src.snotes.application.note_service import NoteService
from src.snotes.application.tag_service import TagService
from src.snotes.domain.errors import NoteRevisionConflictError
from src.snotes.domain.models import Note, NoteLocation
from src.snotes.domain.tags import format_tags, parse_tags
from src.snotes.ui.directory_dialog import DirectoryUiWorkflow
from src.snotes.ui.export_dialog import ExportContext, ExportDialog
from src.snotes.ui.location_controls import (
    location_from_path,
    ordered_directories,
    populate_dependent_location_controls,
    populate_location_controls,
    selected_location,
)
from src.snotes.ui.shell import SnotesShell
from src.snotes.ui.theme import (
    apply_theme,
    load_saved_theme,
    normalize_theme_name,
    save_theme,
)


CONFLICT_MESSAGE = "这篇笔记已被其他来源修改，请重新加载后处理。"

class MainWindow(QMainWindow):
    AUTOSAVE_DELAY_MS = 1000
    AUTOSAVE_RETRY_DELAYS_MS = (3000, 10000, 30000)

    def __init__(
        self,
        note_service: NoteService,
        *,
        theme_settings_path: Path,
        directory_service: DirectoryService | None = None,
        tag_service: TagService | None = None,
        export_service: ExportService | None = None,
    ) -> None:
        super().__init__()
        self.note_service = note_service
        self.directory_service = directory_service
        self.tag_service = tag_service
        self.export_service = export_service
        self.theme_settings_path = Path(theme_settings_path)
        self.current_note: Note | None = None
        self.current_mode = "directory"
        self.current_filter = ""
        self.current_directory_path: tuple[str, ...] = ()
        self.visible_notes: list[Note] = []
        self.is_loading_note = False
        self.has_unsaved_changes = False
        self.conflict_active = False
        self.autosave_retry_count = 0

        self.setWindowTitle("S-notes")
        self.resize(1420, 880)
        self.shell = SnotesShell(self.theme_settings_path)
        self.directory_workflow = DirectoryUiWorkflow(self.directory_service, self)
        self.setCentralWidget(self.shell)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(self.AUTOSAVE_DELAY_MS)
        self.save_timer.timeout.connect(self.save_current_note)
        self._connect_ui()

        self.current_theme_name = load_saved_theme(self.theme_settings_path)
        application = QApplication.instance()
        if application is not None:
            apply_theme(application, self.current_theme_name)
        self.shell.enforce_single_surface_backgrounds()
        self.shell.sidebar.set_current_theme(self.current_theme_name)
        self.shell.refresh_theme_icons()
        self.shell.set_editor_available(False)
        self.refresh_note_list()

    @property
    def title_input(self):
        return self.shell.title_input

    @property
    def body_edit(self):
        return self.shell.body_editor
    @property
    def status_label(self):
        return self.shell.status_label
    def _connect_ui(self) -> None:
        sidebar = self.shell.sidebar
        sidebar.newNoteRequested.connect(self.create_note)
        sidebar.noteSelected.connect(self.switch_note)
        sidebar.themeSelected.connect(self.set_theme)
        sidebar.searchRequested.connect(self.show_search)
        sidebar.directoryFilterRequested.connect(self.show_directory_filter)
        sidebar.smartViewRequested.connect(self.show_smart_view)
        sidebar.pinNoteRequested.connect(self.set_note_pinned)
        sidebar.renameNoteRequested.connect(self.rename_note)
        sidebar.deleteNotesRequested.connect(self.delete_notes)
        sidebar.moveNotesRequested.connect(self.move_notes)
        sidebar.manageDirectoriesRequested.connect(self.directory_workflow.open)
        sidebar.trashRequested.connect(self.show_trash)
        sidebar.exitTrashRequested.connect(self.show_directory)
        sidebar.restoreTrashRequested.connect(self.restore_trash_items)
        sidebar.permanentDeleteTrashRequested.connect(self.permanently_delete_trash_items)
        sidebar.emptyTrashRequested.connect(self.empty_trash)
        self.title_input.textChanged.connect(self.mark_note_dirty)
        self.body_edit.textChanged.connect(self.mark_note_dirty)
        self.shell.tags_input.textChanged.connect(self.mark_note_dirty)
        for combo in (self.shell.main_combo, self.shell.sub_combo):
            combo.currentTextChanged.connect(self._location_changed)
        self.shell.reload_button.clicked.connect(self.reload_after_conflict)
        self.shell.pin_button.clicked.connect(self.toggle_current_pin)
        self.shell.links_button.clicked.connect(self.toggle_links)
        self.shell.export_button.clicked.connect(self.open_export_dialog)
        self.shell.delete_button.clicked.connect(self.delete_current_note)
        self.shell.link_drawer.closeRequested.connect(self.toggle_links)
        self.shell.link_drawer.noteSelected.connect(self.open_related_note)

    def refresh_note_list(self, selected_note_id: int | None = None) -> None:
        if selected_note_id is None and self.current_note is not None:
            selected_note_id = self.current_note.id
        directories = (
            ordered_directories(self.directory_service)
            if self.directory_service is not None
            else []
        )
        if directories:
            available_paths = {
                self.directory_service.get_directory_path(row.id)
                for row in directories
            }
            if self.current_directory_path and self.current_directory_path not in available_paths:
                self.current_directory_path = ()
        self.shell.sidebar.set_directories(directories, self.current_directory_path)
        if self.current_mode == "search":
            self.visible_notes = self.note_service.search_notes(self.current_filter)
            self.shell.sidebar.show_flat(self.visible_notes, mode="search", selected_note_id=selected_note_id)
        elif self.current_mode == "tag":
            self.visible_notes = self.note_service.notes_for_tag(self.current_filter)
            self.shell.sidebar.show_flat(self.visible_notes, mode="tag", selected_note_id=selected_note_id)
        elif self.current_mode == "smart_view":
            _title, self.visible_notes = self.note_service.smart_view(self.current_filter)
            self.shell.sidebar.show_flat(self.visible_notes, mode="smart_view", selected_note_id=selected_note_id)
        else:
            self.current_mode = "directory"
            location = (
                location_from_path(self.current_directory_path)
                if self.current_directory_path
                else None
            )
            self.visible_notes = self.note_service.notes_for_directory(location)
            self.shell.sidebar.mode = "directory"
            self.shell.sidebar.set_notes(self.visible_notes, selected_note_id)

    def create_note(self) -> None:
        if not self.force_save_current_note():
            return
        if self.current_note is not None:
            location = self.current_note.location
        elif self.current_directory_path:
            location = location_from_path(self.current_directory_path)
        else:
            location = NoteLocation()
        note = self.note_service.create_note(location)
        self.current_mode = "directory"
        self.load_note(note.id)
        self.refresh_note_list(note.id)
        self.title_input.setFocus()
        self.title_input.selectAll()

    def switch_note(self, note_id: int) -> None:
        if self.current_note is not None and note_id == self.current_note.id:
            return
        if not self.force_save_current_note():
            self._restore_sidebar_selection()
            return
        self.load_note(note_id)

    def load_note(self, note_id: int) -> bool:
        note = self.note_service.get_note(note_id)
        if note is None:
            self.status_label.setText("无法加载：笔记不存在或已删除")
            self.refresh_note_list()
            return False
        self.is_loading_note = True
        try:
            self.current_note = note
            self.title_input.setText(note.title)
            self.body_edit.set_note_content(note.content_html)
            self.body_edit.document().setModified(False)
            self.shell.tags_input.setText(format_tags(parse_tags(note.tags)))
            populate_location_controls(self.shell, note, self.directory_service)
            self.shell.update_metadata_summary()
            self.has_unsaved_changes = False
            self.conflict_active = False
            self.autosave_retry_count = 0
            self.save_timer.stop()
            self.shell.set_editor_available(True)
            self.shell.set_normal_status("已保存")
            self.shell.sidebar.select_note(note.id)
            self._refresh_links()
            self._refresh_action_states()
        finally:
            self.is_loading_note = False
        return True

    def mark_note_dirty(self) -> None:
        if self.is_loading_note or self.current_note is None:
            return
        self.has_unsaved_changes = True
        self.autosave_retry_count = 0
        if self.conflict_active:
            self.shell.set_conflict_status(CONFLICT_MESSAGE)
            return
        self.shell.set_normal_status("未保存")
        self.save_timer.start(self.AUTOSAVE_DELAY_MS)

    def force_save_current_note(self) -> bool:
        self.save_timer.stop()
        return self.save_current_note()

    def save_current_note(self) -> bool:
        if self.current_note is None or not self.has_unsaved_changes:
            return True
        if self.conflict_active:
            self.shell.set_conflict_status(CONFLICT_MESSAGE)
            return False
        self.shell.set_normal_status("保存中…")
        try:
            saved = self.note_service.save_note(
                self.current_note.id,
                title=self.title_input.text().strip() or "未命名笔记",
                content_html=self.body_edit.note_content_html(),
                location=selected_location(self.shell),
                expected_revision=self.current_note.revision,
            )
            if self.tag_service is not None:
                self.tag_service.set_note_tags_from_text(
                    saved.id,
                    self.shell.tags_input.text(),
                )
                saved = self.note_service.get_note(saved.id) or saved
        except NoteRevisionConflictError:
            self.has_unsaved_changes = True
            self.conflict_active = True
            self.save_timer.stop()
            self.shell.set_conflict_status(CONFLICT_MESSAGE)
            return False
        except Exception as error:
            self.has_unsaved_changes = True
            self.shell.set_normal_status(f"保存失败：{error}")
            self._schedule_autosave_retry()
            return False
        self.current_note = saved
        self.has_unsaved_changes = False
        self.autosave_retry_count = 0
        self.body_edit.document().setModified(False)
        self.shell.set_normal_status("已保存")
        self.refresh_note_list(saved.id)
        self._refresh_links()
        return True

    def _schedule_autosave_retry(self) -> None:
        if self.autosave_retry_count < len(self.AUTOSAVE_RETRY_DELAYS_MS):
            delay = self.AUTOSAVE_RETRY_DELAYS_MS[self.autosave_retry_count]
            self.autosave_retry_count += 1
            self.save_timer.start(delay)

    def reload_after_conflict(self) -> None:
        if self.current_note is not None:
            self.load_note(self.current_note.id)

    def show_directory(self) -> None:
        self.current_mode = "directory"
        self.current_filter = ""
        self.refresh_note_list()

    def show_directory_filter(self, path) -> None:
        self.shell.sidebar.clear_search()
        self.current_directory_path = tuple(str(value) for value in path)
        self.show_directory()

    def show_search(self, query: str) -> None:
        if not query.strip():
            self.show_directory()
        else:
            self.current_mode = "search"
            self.current_filter = query
            self.refresh_note_list()

    def show_tag(self, tag: str) -> None:
        self.current_mode = "tag"
        self.current_filter = tag
        self.refresh_note_list()
    def show_smart_view(self, name: str) -> None:
        self.shell.sidebar.clear_search()
        if self.current_mode == "smart_view" and self.current_filter == name:
            self.current_directory_path = ()
            self.show_directory()
            return
        self.current_mode = "smart_view"
        self.current_filter = name
        self.shell.sidebar.set_active_smart_view(name)
        self.refresh_note_list()

    def set_note_pinned(self, note_id: int, pinned: bool) -> None:
        if not self.force_save_current_note():
            return
        updated = self.note_service.set_note_pinned(note_id, pinned)
        if self.current_note is not None and self.current_note.id == note_id:
            self.current_note = updated
        self.refresh_note_list(note_id)
        self._refresh_action_states()

    def toggle_current_pin(self) -> None:
        if self.current_note is not None:
            self.set_note_pinned(self.current_note.id, not self.current_note.is_pinned)

    def rename_note(self, note_id: int) -> None:
        note = self.note_service.get_note(note_id)
        if note is None:
            return
        title, accepted = QInputDialog.getText(self, "重命名笔记", "笔记标题", text=note.title)
        if accepted:
            self.note_service.rename_note(note_id, title)
            if self.current_note is not None and self.current_note.id == note_id:
                self.load_note(note_id)
            self.refresh_note_list(note_id)

    def delete_current_note(self) -> None:
        if self.current_note is not None:
            self.delete_notes([self.current_note.id])

    def delete_notes(self, note_ids) -> None:
        ids = list(dict.fromkeys(int(value) for value in note_ids))
        if not ids or not self.confirm("移入回收站", f"确定将 {len(ids)} 篇笔记移入回收站吗？"):
            return
        if not self.force_save_current_note():
            return
        current_deleted = self.current_note is not None and self.current_note.id in ids
        for note_id in ids:
            self.note_service.soft_delete_note(note_id)
        if current_deleted:
            self.clear_editor()
        self.refresh_note_list()

    def move_notes(self, note_ids, directory_id: int) -> None:
        if self.directory_service is None or not self.force_save_current_note():
            return
        location = location_from_path(self.directory_service.get_directory_path(directory_id))
        ids = list(dict.fromkeys(int(value) for value in note_ids))
        self.note_service.move_notes(ids, location)
        if self.current_note is not None and self.current_note.id in ids:
            self.load_note(self.current_note.id)
        self.refresh_note_list(self.current_note.id if self.current_note else None)

    def show_trash(self) -> None:
        if self.directory_service is None:
            return
        if not self.force_save_current_note():
            return
        snapshot = self.directory_service.list_trash()
        paths = {row.id: self.directory_service.get_directory_path(row.id) for row in snapshot.directories}
        self.current_mode = "trash"
        self.shell.sidebar.show_trash(snapshot, paths)

    def restore_trash_items(self, note_ids, directory_ids) -> None:
        if self.directory_service is None:
            return
        try:
            result = self.directory_service.restore_trash_items(note_ids, directory_ids)
        except Exception as error:
            QMessageBox.warning(self, "恢复失败", str(error))
            return
        self.show_trash()
        self.status_label.setText(f"已恢复 {result.processed_count} 个项目")

    def permanently_delete_trash_items(self, note_ids, directory_ids) -> None:
        count = len(note_ids) + len(directory_ids)
        if self.directory_service is None or not count or not self.confirm("永久删除", f"确定永久删除所选 {count} 个项目吗？此操作无法撤销。"):
            return
        try:
            result = self.directory_service.permanently_delete_trash_items(note_ids, directory_ids)
        except Exception as error:
            QMessageBox.warning(self, "永久删除失败", str(error))
            return
        self.show_trash()
        self.status_label.setText(f"已永久删除 {result.processed_count} 个项目")

    def empty_trash(self) -> None:
        if self.directory_service is None:
            return
        snapshot = self.directory_service.list_trash()
        if not snapshot.notes and not snapshot.directories:
            self.status_label.setText("回收站为空")
            return
        if not self.confirm("清空回收站", f"将永久删除 {len(snapshot.notes)} 条笔记和 {len(snapshot.directories)} 个目录。此操作无法撤销。"):
            return
        result = self.directory_service.empty_trash()
        self.show_trash()
        self.status_label.setText(f"已清空回收站：{result.note_count} 条笔记、{result.directory_count} 个目录")

    def toggle_links(self) -> None:
        visible = self.shell.link_drawer.isHidden()
        self.shell.link_drawer.setVisible(visible)
        self.shell.links_button.setProperty("active", visible)
        self.shell.links_button.setToolTip("关闭关联抽屉" if visible else "打开关联抽屉")
        self.shell.repolish_action(self.shell.links_button)
        if visible:
            self._refresh_links()

    def _refresh_links(self) -> None:
        if not self.shell.link_drawer.isHidden():
            note_id = self.current_note.id if self.current_note is not None else None
            self.shell.link_drawer.set_results(self.note_service.related_notes(note_id))

    def open_related_note(self, note_id: int) -> None:
        if self.force_save_current_note():
            self.load_note(note_id)
            self.show_directory()

    def open_export_dialog(self) -> None:
        if self.export_service is None:
            return
        selected_locations = tuple(
            location_from_path(self.directory_service.get_directory_path(value))
            for value in self.shell.sidebar.selected_directory_ids()
        ) if self.directory_service is not None else ()
        current_directory = (
            location_from_path(self.current_directory_path)
            if self.current_directory_path
            else (self.current_note.location if self.current_note else None)
        )
        context = ExportContext(
            current_note_id=self.current_note.id if self.current_note else None,
            current_directory=current_directory,
            sidebar_mode=self.current_mode,
            visible_note_ids=tuple(note.id for note in self.visible_notes),
            selected_note_ids=tuple(self.shell.sidebar.selected_note_ids()),
            selected_directories=selected_locations,
        )
        ExportDialog(self.export_service, context, self).exec()

    def _location_changed(self) -> None:
        if self.is_loading_note or self.current_note is None:
            return
        if self.directory_service is not None:
            populate_dependent_location_controls(self.shell, self.directory_service)
        self.mark_note_dirty()

    def set_theme(self, name: str) -> None:
        theme_name = normalize_theme_name(name)
        sidebar_width = self.shell.sidebar.width()
        application = QApplication.instance()
        if application is not None:
            apply_theme(application, theme_name)
        self.shell.enforce_single_surface_backgrounds()
        self.current_theme_name = save_theme(self.theme_settings_path, theme_name)
        self.body_edit.apply_theme()
        self.shell.editor_toolbar.refresh_theme()
        self.shell.sidebar.set_current_theme(theme_name)
        self.shell.refresh_theme_icons()
        self.shell.restore_sidebar_width(sidebar_width)
        QTimer.singleShot(0, lambda width=sidebar_width: self.shell.restore_sidebar_width(width))

    def clear_editor(self) -> None:
        self.is_loading_note = True
        self.current_note = None
        self.title_input.clear()
        self.body_edit.clear()
        self.shell.tags_input.clear()
        self.shell.update_metadata_summary()
        self.has_unsaved_changes = False
        self.save_timer.stop()
        self.shell.set_editor_available(False)
        self.shell.set_normal_status("未选择笔记")
        self.shell.link_drawer.set_results([])
        self.is_loading_note = False

    def _restore_sidebar_selection(self) -> None:
        if self.current_note is not None:
            self.shell.sidebar.select_note(self.current_note.id)

    def _refresh_action_states(self) -> None:
        has_note = self.current_note is not None
        active = bool(has_note and self.current_note.is_pinned)
        self.shell.pin_button.setProperty("active", active)
        self.shell.pin_button.setToolTip("取消固定" if active else "固定笔记")
        self.shell.repolish_action(self.shell.pin_button)

    def confirm(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def closeEvent(self, event) -> None:
        event.accept() if self.force_save_current_note() else event.ignore()
