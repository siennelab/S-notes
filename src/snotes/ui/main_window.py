"""Desktop coordination owner for Package 2.

The window owns selected-note UI state and autosave timing. All note business
actions go through ``NoteService``; this module has no storage dependency.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMainWindow

from src.snotes.application.note_service import NoteService
from src.snotes.domain.errors import NoteRevisionConflictError
from src.snotes.domain.models import Note
from src.snotes.ui.shell import SnotesShell
from src.snotes.ui.theme import (
    apply_theme,
    load_saved_theme,
    normalize_theme_name,
    repolish,
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
    ) -> None:
        super().__init__()
        self.note_service = note_service
        self.theme_settings_path = Path(theme_settings_path)
        self.current_note: Note | None = None
        self.is_loading_note = False
        self.has_unsaved_changes = False
        self.conflict_active = False
        self.autosave_retry_count = 0

        self.setWindowTitle("S-notes")
        self.resize(1360, 860)
        self.shell = SnotesShell()
        self.setCentralWidget(self.shell)

        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(self.AUTOSAVE_DELAY_MS)
        self.save_timer.timeout.connect(self.save_current_note)

        self.shell.sidebar.newNoteRequested.connect(self.create_note)
        self.shell.sidebar.noteSelected.connect(self.switch_note)
        self.shell.sidebar.themeSelected.connect(self.set_theme)
        self.shell.title_input.textChanged.connect(self.mark_note_dirty)
        self.shell.body_editor.textChanged.connect(self.mark_note_dirty)
        self.shell.reload_button.clicked.connect(self.reload_after_conflict)

        self.current_theme_name = load_saved_theme(self.theme_settings_path)
        application = QApplication.instance()
        if application is not None:
            apply_theme(application, self.current_theme_name)
        self.shell.enforce_single_surface_backgrounds()
        self.shell.sidebar.set_current_theme(self.current_theme_name)
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

    def refresh_note_list(self, selected_note_id: int | None = None) -> None:
        if selected_note_id is None and self.current_note is not None:
            selected_note_id = self.current_note.id
        self.shell.sidebar.set_notes(
            self.note_service.list_active_notes(), selected_note_id
        )

    def create_note(self) -> None:
        if not self.force_save_current_note():
            self._restore_sidebar_selection()
            return
        note = self.note_service.create_note()
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
            self.has_unsaved_changes = False
            self.conflict_active = False
            self.autosave_retry_count = 0
            self.save_timer.stop()
            self.shell.set_editor_available(True)
            self._set_normal_status("已保存")
            self.shell.sidebar.select_note(note.id)
        finally:
            self.is_loading_note = False
        return True

    def mark_note_dirty(self) -> None:
        if self.is_loading_note or self.current_note is None:
            return
        self.has_unsaved_changes = True
        self.autosave_retry_count = 0
        if self.conflict_active:
            self._set_conflict_status()
            return
        self._set_normal_status("未保存")
        self.save_timer.start(self.AUTOSAVE_DELAY_MS)

    def force_save_current_note(self) -> bool:
        self.save_timer.stop()
        return self.save_current_note()

    def save_current_note(self) -> bool:
        if self.current_note is None or not self.has_unsaved_changes:
            return True
        if self.conflict_active:
            self._set_conflict_status()
            return False

        self._set_normal_status("保存中…")
        try:
            saved = self.note_service.save_note(
                self.current_note.id,
                title=self.title_input.text().strip() or "未命名笔记",
                content_html=self.body_edit.note_content_html(),
                location=self.current_note.location,
                expected_revision=self.current_note.revision,
            )
        except NoteRevisionConflictError:
            self.has_unsaved_changes = True
            self.conflict_active = True
            self.save_timer.stop()
            self._set_conflict_status()
            return False
        except Exception as error:
            self.has_unsaved_changes = True
            self._set_normal_status(f"保存失败：{error}")
            self._schedule_autosave_retry()
            return False

        self.current_note = saved
        self.has_unsaved_changes = False
        self.autosave_retry_count = 0
        self.body_edit.document().setModified(False)
        self._set_normal_status("已保存")
        self.refresh_note_list(saved.id)
        return True

    def _schedule_autosave_retry(self) -> None:
        if self.autosave_retry_count >= len(self.AUTOSAVE_RETRY_DELAYS_MS):
            return
        delay = self.AUTOSAVE_RETRY_DELAYS_MS[self.autosave_retry_count]
        self.autosave_retry_count += 1
        self.save_timer.start(delay)

    def reload_after_conflict(self) -> None:
        if self.current_note is not None:
            self.load_note(self.current_note.id)

    def set_theme(self, name: str) -> None:
        theme_name = normalize_theme_name(name)
        application = QApplication.instance()
        if application is not None:
            apply_theme(application, theme_name)
        self.shell.enforce_single_surface_backgrounds()
        self.current_theme_name = save_theme(
            self.theme_settings_path, theme_name
        )
        self.body_edit.apply_theme()
        self.shell.editor_toolbar.refresh_theme()
        self.shell.sidebar.set_current_theme(theme_name)

    def _restore_sidebar_selection(self) -> None:
        if self.current_note is not None:
            self.shell.sidebar.select_note(self.current_note.id)

    def _set_normal_status(self, text: str) -> None:
        self.status_label.setProperty("conflict", False)
        self.status_label.setText(text)
        self.shell.reload_button.setVisible(False)
        repolish(self.status_label)

    def _set_conflict_status(self) -> None:
        self.status_label.setProperty("conflict", True)
        self.status_label.setText(CONFLICT_MESSAGE)
        self.shell.reload_button.setVisible(True)
        repolish(self.status_label)

    def closeEvent(self, event) -> None:
        if self.force_save_current_note():
            event.accept()
        else:
            event.ignore()
