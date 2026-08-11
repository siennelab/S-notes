"""Package 3 desktop coordinator; UI contains no storage or SQL access."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QInputDialog, QMainWindow, QMessageBox

from src.snotes.application.ai_access_metadata_service import (
    AiAccessMetadata,
    AiAccessMetadataService,
)
from src.snotes.application.ai_receipt_inbox_service import AiReceiptInboxService
from src.snotes.application.ai_audit_service import AiAuditService
from src.snotes.application.ai_safe_export_service import AiSafeExportService
from src.snotes.application.gale_service import GaleControlService
from src.snotes.application.directory_service import DirectoryService
from src.snotes.application.export_service import ExportService
from src.snotes.application.note_service import NoteService
from src.snotes.application.tag_service import TagService
from src.snotes.domain.errors import NoteRevisionConflictError
from src.snotes.domain.models import Note, NoteLocation
from src.snotes.domain.tags import format_tags, parse_tags
from src.snotes.ui.directory_dialog import DirectoryUiWorkflow
from src.snotes.ui.ai_center_dialog import AiCenterDialog
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
    EXTERNAL_RECEIPT_REFRESH_INTERVAL_MS = 5_000

    def __init__(
        self,
        note_service: NoteService,
        *,
        theme_settings_path: Path,
        directory_service: DirectoryService | None = None,
        tag_service: TagService | None = None,
        export_service: ExportService | None = None,
        ai_access_metadata_service: AiAccessMetadataService | None = None,
        receipt_inbox_service: AiReceiptInboxService | None = None,
        ai_safe_export_service: AiSafeExportService | None = None,
        ai_audit_service: AiAuditService | None = None,
        gale_control_service: GaleControlService | None = None,
    ) -> None:
        super().__init__()
        self.note_service = note_service
        self.directory_service = directory_service
        self.tag_service = tag_service
        self.export_service = export_service
        self.ai_access_metadata_service = ai_access_metadata_service
        self.receipt_inbox_service = receipt_inbox_service
        self.ai_safe_export_service = ai_safe_export_service
        self.ai_audit_service = ai_audit_service
        self.gale_control_service = gale_control_service
        self.ai_center_dialog: AiCenterDialog | None = None
        self.theme_settings_path = Path(theme_settings_path)
        self.current_note: Note | None = None
        self.current_mode = "directory"
        self.current_filter = ""
        self.current_directory_path: tuple[str, ...] = ()
        self.ai_materials_actor_id: str | None = None
        self.ai_materials_query = ""
        self.visible_notes: list[Note] = []
        self.is_loading_note = False
        self.has_unsaved_changes = False
        self.conflict_active = False
        self.autosave_retry_count = 0

        self.setWindowTitle("S-notes")
        self.resize(1420, 880)
        self.shell = SnotesShell(self.theme_settings_path)
        self.shell.sidebar.set_ai_center_available(
            self.receipt_inbox_service is not None
        )
        self.refresh_ai_center_badge()
        if self.ai_access_metadata_service is not None:
            target_options = tuple(
                (item.target_id, item.display_name)
                for item in self.ai_access_metadata_service.available_targets()
            )
            self.shell.configure_ai_metadata(
                self.ai_access_metadata_service.available_visibility_options(),
                target_options,
            )
            self.shell.sidebar.configure_ai_actor_filters(target_options)
        self.directory_workflow = DirectoryUiWorkflow(self.directory_service, self)
        self.setCentralWidget(self.shell)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(self.AUTOSAVE_DELAY_MS)
        self.save_timer.timeout.connect(self.save_current_note)
        self.receipt_refresh_timer = QTimer(self)
        self.receipt_refresh_timer.setInterval(
            self.EXTERNAL_RECEIPT_REFRESH_INTERVAL_MS
        )
        self.receipt_refresh_timer.timeout.connect(
            self.refresh_external_receipts
        )
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
        sidebar.trashNoteSelected.connect(self.load_deleted_note)
        sidebar.restoreTrashRequested.connect(self.restore_trash_items)
        sidebar.permanentDeleteTrashRequested.connect(self.permanently_delete_trash_items)
        sidebar.emptyTrashRequested.connect(self.empty_trash)
        sidebar.aiCenterRequested.connect(self.open_ai_center)
        sidebar.aiActorFilterRequested.connect(self.set_ai_materials_actor_filter)
        self.title_input.textChanged.connect(self.mark_note_dirty)
        self.body_edit.textChanged.connect(self.mark_note_dirty)
        self.shell.tags_input.textChanged.connect(self.mark_note_dirty)
        for combo in (self.shell.main_combo, self.shell.sub_combo):
            combo.currentTextChanged.connect(self._location_changed)
        if self.ai_access_metadata_service is not None:
            self.shell.ai_visibility_combo.currentIndexChanged.connect(
                self._ai_visibility_changed
            )
            for checkbox in self.shell.ai_target_checks.values():
                checkbox.stateChanged.connect(self._ai_targets_changed)
        self.shell.reload_button.clicked.connect(self.reload_after_conflict)
        self.shell.pin_button.clicked.connect(self.toggle_current_pin)
        self.shell.links_button.clicked.connect(self.toggle_links)
        self.shell.export_button.clicked.connect(self.open_export_dialog)
        self.shell.delete_button.clicked.connect(self.delete_current_note)
        self.shell.link_drawer.closeRequested.connect(self.toggle_links)
        self.shell.link_drawer.noteSelected.connect(self.open_related_note)

    def open_ai_center(self) -> None:
        if (
            self.receipt_inbox_service is None
            or self.ai_access_metadata_service is None
        ):
            return
        if self.ai_center_dialog is not None:
            self.ai_center_dialog.receipt_view.refresh_receipts()
            self.ai_center_dialog.show()
            self.ai_center_dialog.raise_()
            self.ai_center_dialog.activateWindow()
            return
        dialog = AiCenterDialog(
            self.receipt_inbox_service,
            self.ai_access_metadata_service,
            self.ai_audit_service,
            self,
            gale=self.gale_control_service,
        )
        self.ai_center_dialog = dialog
        dialog.receiptAccepted.connect(self._receipt_note_created)
        dialog.pendingCountChanged.connect(
            self.shell.sidebar.set_ai_center_pending_count
        )
        dialog.destroyed.connect(lambda: setattr(self, "ai_center_dialog", None))
        self.refresh_ai_center_badge()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _receipt_note_created(self, note_id: int) -> None:
        self.refresh_note_list(note_id)

    def refresh_ai_center_badge(self) -> None:
        pending_count = (
            len(self.receipt_inbox_service.list_pending_receipts())
            if self.receipt_inbox_service is not None
            else 0
        )
        self.shell.sidebar.set_ai_center_pending_count(pending_count)

    def refresh_external_receipts(self) -> None:
        """Refresh receipt UI after a headless helper writes the shared inbox."""
        self.refresh_ai_center_badge()
        dialog = self.ai_center_dialog
        if dialog is None:
            return
        try:
            dialog.receipt_view.refresh_receipts()
            dialog.refresh_gale_runtime_status()
        except RuntimeError:
            self.ai_center_dialog = None

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
            _title, self.visible_notes = self.note_service.smart_view(
                self.current_filter,
                actor_target_id=(
                    self.ai_materials_actor_id
                    if self.current_filter == "ai"
                    else None
                ),
                query=(
                    self.ai_materials_query
                    if self.current_filter == "ai"
                    else ""
                ),
            )
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
        metadata = None
        if self.ai_access_metadata_service is not None:
            try:
                metadata = self.ai_access_metadata_service.get_note_metadata(note_id)
            except Exception as error:
                self.shell.set_normal_status(f"AI 元数据加载失败：{error}")
                return False
        self.is_loading_note = True
        try:
            self.current_note = note
            self.title_input.setText(note.title)
            self.body_edit.set_note_content(note.content_html)
            self.body_edit.document().setModified(False)
            self.shell.tags_input.setText(format_tags(parse_tags(note.tags)))
            populate_location_controls(self.shell, note, self.directory_service)
            if metadata is not None:
                self.shell.set_ai_metadata_values(
                    metadata.visibility,
                    metadata.target_ids,
                    metadata.legacy_target_names,
                )
            self.shell.update_metadata_summary()
            self.has_unsaved_changes = False
            self.conflict_active = False
            self.autosave_retry_count = 0
            self.save_timer.stop()
            self.shell.set_editor_available(True)
            self.shell.set_editor_read_only(False)
            self.shell.set_normal_status("已保存")
            self.shell.sidebar.select_note(note.id)
            self._refresh_links()
            self._refresh_action_states()
        finally:
            self.is_loading_note = False
        return True

    def load_deleted_note(self, note_id: int) -> bool:
        if not self.force_save_current_note():
            return False
        note = self.note_service.get_note(note_id, include_deleted=True)
        if note is None or not note.is_deleted:
            self.show_trash()
            return False
        metadata = None
        if self.ai_access_metadata_service is not None:
            try:
                metadata = self.ai_access_metadata_service.get_deleted_note_metadata(
                    note_id
                )
            except Exception as error:
                self.shell.set_normal_status(f"AI 元数据加载失败：{error}")
                return False
        self.is_loading_note = True
        try:
            self.current_note = None
            self.title_input.setText(note.title)
            self.body_edit.set_note_content(note.content_html)
            self.body_edit.document().setModified(False)
            self.shell.tags_input.setText(format_tags(parse_tags(note.tags)))
            populate_location_controls(self.shell, note, self.directory_service)
            if metadata is not None:
                self.shell.set_ai_metadata_values(
                    metadata.visibility,
                    metadata.target_ids,
                    metadata.legacy_target_names,
                )
            self.shell.update_metadata_summary()
            self.has_unsaved_changes = False
            self.conflict_active = False
            self.autosave_retry_count = 0
            self.save_timer.stop()
            self.shell.set_editor_available(True)
            self.shell.set_editor_read_only(True)
            self.shell.set_normal_status("回收站中 · 只读")
            self.shell.link_drawer.set_results([])
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

    def _ai_visibility_changed(self, _index: int = -1) -> None:
        if (
            self.is_loading_note
            or self.current_note is None
            or self.ai_access_metadata_service is None
        ):
            return
        visibility = self.shell.ai_visibility_combo.currentData()
        if visibility is None:
            return
        try:
            metadata = self.ai_access_metadata_service.set_note_visibility(
                self.current_note.id,
                visibility,
            )
        except Exception as error:
            self._restore_ai_metadata_after_failure(error)
            return
        self._accept_ai_metadata_write(metadata)

    def _ai_targets_changed(self, _state: int = 0) -> None:
        if (
            self.is_loading_note
            or self.current_note is None
            or self.ai_access_metadata_service is None
        ):
            return
        try:
            metadata = self.ai_access_metadata_service.set_note_targets(
                self.current_note.id,
                self.shell.selected_ai_target_ids(),
            )
        except Exception as error:
            self._restore_ai_metadata_after_failure(error)
            return
        self._accept_ai_metadata_write(metadata)

    def _accept_ai_metadata_write(self, metadata: AiAccessMetadata) -> None:
        if self.current_note is None:
            return
        self.shell.set_ai_metadata_values(
            metadata.visibility,
            metadata.target_ids,
            metadata.legacy_target_names,
        )
        refreshed = self.note_service.get_note(self.current_note.id)
        if refreshed is None:
            self.shell.set_normal_status("AI 元数据已保存，但笔记重载失败")
            return
        self.current_note = refreshed
        self.shell.set_normal_status("未保存" if self.has_unsaved_changes else "已保存")
        self.refresh_note_list(refreshed.id)

    def _restore_ai_metadata_after_failure(self, error: Exception) -> None:
        if self.current_note is not None and self.ai_access_metadata_service is not None:
            try:
                metadata = self.ai_access_metadata_service.get_note_metadata(
                    self.current_note.id
                )
                self.shell.set_ai_metadata_values(
                    metadata.visibility,
                    metadata.target_ids,
                    metadata.legacy_target_names,
                )
            except Exception:
                pass
        self.shell.set_normal_status(f"AI 元数据保存失败：{error}")

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
        if self.current_mode == "smart_view" and self.current_filter == "ai":
            self.ai_materials_query = query
            self.refresh_note_list()
            return
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
        self.ai_materials_query = ""
        if self.current_mode == "smart_view" and self.current_filter == name:
            self.current_directory_path = ()
            self.show_directory()
            return
        self.current_mode = "smart_view"
        self.current_filter = name
        self.shell.sidebar.set_active_smart_view(name)
        self.refresh_note_list()

    def set_ai_materials_actor_filter(self, actor_target_id: object) -> None:
        self.ai_materials_actor_id = (
            str(actor_target_id) if actor_target_id is not None else None
        )
        if self.current_mode == "smart_view" and self.current_filter == "ai":
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
        if not ids:
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
        restored_note_ids = tuple(result.note_ids)
        if len(restored_note_ids) == 1 and not result.directory_ids:
            self.current_mode = "directory"
            self.load_note(restored_note_ids[0])
            self.refresh_note_list(restored_note_ids[0])
        else:
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
        ExportDialog(
            self.export_service,
            context,
            self,
            ai_safe_export_service=self.ai_safe_export_service,
        ).exec()

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
        self.shell.clear_ai_metadata_values()
        self.shell.update_metadata_summary()
        self.has_unsaved_changes = False
        self.save_timer.stop()
        self.shell.set_editor_read_only(False)
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

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.receipt_inbox_service is not None:
            self.receipt_refresh_timer.start()

    def closeEvent(self, event) -> None:
        if self.force_save_current_note():
            self.receipt_refresh_timer.stop()
            event.accept()
        else:
            event.ignore()
