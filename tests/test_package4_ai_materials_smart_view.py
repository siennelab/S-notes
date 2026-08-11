from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_receipt_inbox_service import AiReceiptAcceptFields
from src.snotes.application.note_service import NoteService
from src.snotes.application.tag_service import TagService
from src.snotes.bootstrap import build_application
from src.snotes.storage.connection import SQLiteConnectionFactory
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.ui.floating_combo import FloatingComboBox
from src.snotes.ui.sidebar_widgets import DirectoryFilterCombo
from src.snotes.ui.theme import THEMES, build_app_style


class Package4AiMaterialsQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-r2-package4-ai-materials-core-"
        )
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        connections = initialize_database(root / "s_notes.db")
        directories = SQLiteDirectoriesRepository(connections)
        notes = SQLiteNotesRepository(connections, directories)
        self.note_service = NoteService(notes)
        self.metadata = AiAccessMetadataService(notes)
        self.tags = TagService(SQLiteTagsRepository(connections))

    def add_note(
        self,
        title: str,
        *,
        body: str = "<p>FICTIONAL MATERIAL BODY</p>",
        visibility: str = "full",
        targets: tuple[str, ...] = (),
        tags: tuple[str, ...] = (),
    ):
        note = self.note_service.create_note()
        note = self.note_service.save_note(
            note.id,
            title=title,
            content_html=body,
            location=note.location,
            expected_revision=note.revision,
        )
        if tags:
            self.tags.set_note_tags(note.id, tags)
        self.metadata.set_note_visibility(note.id, visibility)
        if targets:
            self.metadata.set_note_targets(note.id, targets)
        return self.note_service.get_note(note.id)

    def test_inclusion_active_filters_multi_target_and_legacy_identity(self) -> None:
        common = self.add_note("Common material", targets=("common",))
        gale = self.add_note("Gale material", targets=("gale",))
        clawbot = self.add_note("Clawbot material", targets=("clawbot",))
        claude = self.add_note("Claude material", targets=("claude-code",))
        ke = self.add_note("Ke material", targets=("ke-den",))
        multi = self.add_note(
            "Shared material",
            visibility="redacted",
            targets=("gale", "claude-code", "ke-den"),
        )
        legacy_codex = self.add_note("Legacy Codex", targets=("codex",))
        legacy_gemini = self.add_note("Legacy Gemini", targets=("gemini",))
        targetless = self.add_note("Visible without target")
        hidden = self.add_note(
            "Hidden Gale",
            visibility="hidden",
            targets=("gale",),
        )
        deleted = self.add_note("Deleted Gale", targets=("gale",))
        self.note_service.soft_delete_note(deleted.id)

        all_ids = {note.id for note in self.note_service.ai_materials()}
        self.assertEqual(
            all_ids,
            {
                common.id,
                gale.id,
                clawbot.id,
                claude.id,
                ke.id,
                multi.id,
                legacy_codex.id,
                legacy_gemini.id,
                targetless.id,
            },
        )
        self.assertNotIn(hidden.id, all_ids)
        self.assertNotIn(deleted.id, all_ids)
        expected = {
            "common": {common.id},
            "gale": {gale.id, multi.id},
            "clawbot": {clawbot.id},
            "claude-code": {claude.id, multi.id},
            "ke-den": {ke.id, multi.id},
            "codex": {legacy_codex.id},
            "gemini": {legacy_gemini.id},
        }
        for actor_id, note_ids in expected.items():
            with self.subTest(actor_id=actor_id):
                self.assertEqual(
                    {
                        note.id
                        for note in self.note_service.ai_materials(
                            actor_target_id=actor_id
                        )
                    },
                    note_ids,
                )

    def test_search_is_anded_with_actor_and_clear_keeps_actor_semantics(self) -> None:
        wanted = self.add_note(
            "Alpha shared needle",
            body="<p>fictional search body</p>",
            targets=("gale", "ke-den"),
            tags=("research",),
        )
        other_actor = self.add_note(
            "Alpha shared needle",
            targets=("clawbot",),
        )
        other_text = self.add_note("Gale unrelated", targets=("gale",))

        results = self.note_service.ai_materials(
            actor_target_id="gale",
            query="shared needle",
        )
        self.assertEqual([note.id for note in results], [wanted.id])
        self.assertNotIn(other_actor.id, [note.id for note in results])
        self.assertNotIn(other_text.id, [note.id for note in results])
        cleared = self.note_service.ai_materials(actor_target_id="gale", query="")
        self.assertEqual({note.id for note in cleared}, {wanted.id, other_text.id})
        self.assertEqual(
            self.note_service.ai_materials(actor_target_id="gale", query="***"),
            [],
        )

    def test_delete_and_restore_recompute_ai_material_eligibility(self) -> None:
        note = self.add_note("Restorable Gale", targets=("gale",))
        self.assertEqual(
            [row.id for row in self.note_service.ai_materials(actor_target_id="gale")],
            [note.id],
        )
        self.note_service.soft_delete_note(note.id)
        self.assertEqual(
            self.note_service.ai_materials(actor_target_id="gale"),
            [],
        )
        self.note_service.restore_note(note.id)
        self.assertEqual(
            [row.id for row in self.note_service.ai_materials(actor_target_id="gale")],
            [note.id],
        )


class Package4AiMaterialsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-r2-package4-ai-materials-ui-"
        )
        self.addCleanup(self.temporary.cleanup)
        self.data_root = Path(self.temporary.name) / "data"
        _application, self.window = build_application([], data_root=self.data_root)
        self.addCleanup(self.window.close)
        self.connections = SQLiteConnectionFactory(self.data_root / "s_notes.db")

    def add_note(
        self,
        title: str,
        *,
        visibility: str = "full",
        targets: tuple[str, ...] = (),
    ):
        note = self.window.note_service.create_note()
        note = self.window.note_service.save_note(
            note.id,
            title=title,
            content_html=f"<p>{title} FICTIONAL BODY</p>",
            location=note.location,
            expected_revision=note.revision,
        )
        self.window.ai_access_metadata_service.set_note_visibility(
            note.id,
            visibility,
        )
        if targets:
            self.window.ai_access_metadata_service.set_note_targets(
                note.id,
                targets,
            )
        return self.window.note_service.get_note(note.id)

    def select_actor(self, actor_id: str | None) -> None:
        combo = self.window.shell.sidebar.ai_actor_filter_combo
        combo.setCurrentIndex(combo.findData(actor_id))
        self.app.processEvents()

    def test_registry_options_filter_search_clear_and_navigation_stability(self) -> None:
        gale = self.add_note("Gale Alpha", targets=("gale",))
        gale_other = self.add_note("Gale Beta", targets=("gale",))
        ke = self.add_note("Ke Alpha", targets=("ke-den",))
        sidebar = self.window.shell.sidebar
        self.assertEqual(
            tuple(
                (
                    sidebar.ai_actor_filter_combo.itemText(index),
                    sidebar.ai_actor_filter_combo.itemData(index),
                )
                for index in range(sidebar.ai_actor_filter_combo.count())
            ),
            (
                ("全部", None),
                ("通用", "common"),
                ("Gale", "gale"),
                ("Clawbot", "clawbot"),
                ("Claude Code", "claude-code"),
                ("Ke", "ke-den"),
            ),
        )

        self.window.show_smart_view("ai")
        self.select_actor("gale")
        self.assertFalse(sidebar.ai_actor_filter_widget.isHidden())
        self.assertEqual(
            {note.id for note in self.window.visible_notes},
            {gale.id, gale_other.id},
        )
        sidebar.search_input.setText("Alpha")
        self.app.processEvents()
        self.assertEqual(
            (self.window.current_mode, self.window.current_filter),
            ("smart_view", "ai"),
        )
        self.assertEqual([note.id for note in self.window.visible_notes], [gale.id])
        sidebar.search_input.clear()
        self.app.processEvents()
        self.assertEqual(self.window.ai_materials_actor_id, "gale")
        self.assertEqual(
            {note.id for note in self.window.visible_notes},
            {gale.id, gale_other.id},
        )

        sidebar.search_input.setText("Alpha")
        self.window.show_smart_view("recent")
        self.app.processEvents()
        self.assertEqual(
            (self.window.current_mode, self.window.current_filter),
            ("smart_view", "recent"),
        )
        self.assertFalse(sidebar.ai_actor_filter_widget.isVisible())
        self.assertFalse(hasattr(self.window, "search_timer"))
        self.assertIn(ke.id, {note.id for note in self.window.visible_notes})

    def test_slate_actor_filter_reuses_sidebar_surface_and_targeted_states(self) -> None:
        self.window.set_theme("light-slate")
        self.window.show_smart_view("ai")
        self.window.show()
        self.app.processEvents()
        sidebar = self.window.shell.sidebar
        combo = sidebar.ai_actor_filter_combo
        label = sidebar.ai_actor_filter_label
        tokens = THEMES["light-slate"]
        style = build_app_style("light-slate")

        self.assertIsInstance(combo, FloatingComboBox)
        self.assertNotIsInstance(combo, DirectoryFilterCombo)
        self.assertEqual(combo.surface_role, "sidebar")
        self.assertEqual(combo.height(), 28)
        self.assertEqual(label.height(), combo.height())
        self.assertEqual(combo.objectName(), "aiActorFilterCombo")
        self.assertIn("#aiActorFilterCombo {", style)
        self.assertIn("#aiActorFilterCombo:hover", style)
        self.assertIn("#aiActorFilterCombo:focus", style)
        self.assertIn("#aiActorFilterCombo:disabled", style)
        self.assertIn(f"background: {tokens.sidebar_control};", style)
        self.assertIn(f"color: {tokens.sidebar_text_subtle};", style)

        combo.setFocus()
        combo.showPopup()
        self.app.processEvents()
        self.assertTrue(combo.view().isVisible())
        self.assertEqual(combo.view().objectName(), "sidebarComboPopupView")
        combo.hidePopup()
        combo.setEnabled(False)
        self.app.processEvents()
        self.assertFalse(combo.isEnabled())
        combo.setEnabled(True)
        self.assertEqual(sidebar.main_filter_combo.objectName(), "categoryFilter")
        self.assertEqual(sidebar.sub_filter_combo.objectName(), "categoryFilter")

    def test_visibility_and_target_edits_refresh_current_ai_view(self) -> None:
        note = self.add_note("Editable Gale", targets=("gale",))
        self.window.show_smart_view("ai")
        self.select_actor("gale")
        self.window.load_note(note.id)
        self.assertIn(note.id, {row.id for row in self.window.visible_notes})

        visibility = self.window.shell.ai_visibility_combo
        visibility.setCurrentIndex(visibility.findData("hidden"))
        self.app.processEvents()
        self.assertNotIn(note.id, {row.id for row in self.window.visible_notes})
        visibility.setCurrentIndex(visibility.findData("redacted"))
        self.app.processEvents()
        self.assertIn(note.id, {row.id for row in self.window.visible_notes})

        self.window.shell.ai_target_checks["gale"].setChecked(False)
        self.window.shell.ai_target_checks["ke-den"].setChecked(True)
        self.app.processEvents()
        self.assertNotIn(note.id, {row.id for row in self.window.visible_notes})
        self.select_actor("ke-den")
        self.assertIn(note.id, {row.id for row in self.window.visible_notes})

    def test_accepted_gale_receipt_enters_current_ai_material_view(self) -> None:
        self.window.show_smart_view("ai")
        self.select_actor("gale")
        self._insert_receipt(901)
        result = self.window.receipt_inbox_service.accept_receipt(
            901,
            AiReceiptAcceptFields(
                title="Accepted Gale smart view",
                body="FICTIONAL ACCEPTED MATERIAL",
                main_category="项目",
                sub_category="Smart View",
                tags="accepted, smart-view",
                visibility="full",
                target_ids=("gale",),
            ),
        )
        self.window.refresh_note_list(result.note.id)
        self.assertEqual(result.receipt.status, "accepted")
        self.assertIn(result.note.id, {note.id for note in self.window.visible_notes})

    def _insert_receipt(self, receipt_id: int) -> None:
        with self.connections.write() as connection:
            connection.execute(
                """
                INSERT INTO ai_receipt_inbox (
                    id, raw_text, parse_status, status, source, action, title,
                    main_category, sub_category, tags,
                    ai_visibility, ai_targets, body, created_at, updated_at
                ) VALUES (
                    ?, 'FICTIONAL_SMART_VIEW_RAW', 'parsed', 'pending',
                    'Fictional Smart View', '新增', 'Pending Gale smart view',
                    '项目', 'Smart View', 'candidate', 'full', 'gale',
                    'FICTIONAL_SMART_VIEW_BODY', '2026-08-10T09:00:00',
                    '2026-08-10T09:00:00'
                )
                """,
                (receipt_id,),
            )


if __name__ == "__main__":
    unittest.main()
