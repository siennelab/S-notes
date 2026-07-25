from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from src.ai_safe_export import AiSafePackageExporter
from src.ai_targets import (
    AI_VISIBILITY_FULL,
    AI_VISIBILITY_HIDDEN,
    AI_VISIBILITY_REDACTED,
)
from src.database import (
    _connect,
    _ensure_directory_path,
    _plain_text_to_html,
    _set_note_ai_targets_with_connection,
    _set_note_tags_with_connection,
    initialize_database,
)
from src.redaction import apply_redactions
from src.services.ai_access_service import materialize_ai_safe_notes_for_target
from src.services.ai_safe_materialization import materialize_ai_safe_note
from src.time_utils import now_shanghai_iso


class AiSafeMaterializationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "s_notes.db"
        self.rules_path = self.root / "redaction_rules.json"
        self.output_root = self.root / "exports"
        initialize_database(self.database_path)
        self.rules_path.write_text(
            json.dumps(
                {
                    "RAW_TITLE_SECRET": "[TITLE_REDACTED]",
                    "RAW_BODY_SECRET": "[BODY_REDACTED]",
                    "RAW_DIR_SECRET": "[DIR_REDACTED]",
                    "RAW_TAG_SECRET": "[TAG_REDACTED]",
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def create_note(
        self,
        *,
        title: str,
        body: str,
        visibility: str,
        targets: list[str],
        tags: list[str],
        directory: tuple[str, str, str],
        deleted: bool = False,
    ) -> int:
        timestamp = now_shanghai_iso()
        with _connect(self.database_path) as connection:
            directory_id = _ensure_directory_path(connection, *directory)
            content_html = _plain_text_to_html(body)
            cursor = connection.execute(
                """
                INSERT INTO notes (
                    directory_id, title, body_rich_text, content_html,
                    main_category, sub_category, sub_sub_category, tags,
                    ai_visibility, is_pinned, is_deleted, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, 0, ?, ?, ?)
                """,
                (
                    directory_id,
                    title,
                    content_html,
                    content_html,
                    directory[0],
                    directory[1],
                    directory[2],
                    visibility,
                    1 if deleted else 0,
                    timestamp,
                    timestamp,
                ),
            )
            note_id = int(cursor.lastrowid)
            _set_note_tags_with_connection(
                connection,
                note_id,
                tags,
                timestamp,
            )
            _set_note_ai_targets_with_connection(
                connection,
                note_id,
                targets,
                timestamp,
            )
        return note_id

    def test_actor_permissions_and_full_fields(self) -> None:
        common_id = self.create_note(
            title="FULL_TITLE_CANARY",
            body="FULL_BODY_CANARY",
            visibility=AI_VISIBILITY_FULL,
            targets=["common"],
            tags=["FULL_TAG_CANARY"],
            directory=("FULL_DIR_CANARY", "无", "无"),
        )
        gale_id = self.create_note(
            title="gale",
            body="gale body",
            visibility=AI_VISIBILITY_FULL,
            targets=["gale"],
            tags=["gale-tag"],
            directory=("gale-dir", "无", "无"),
        )
        self.create_note(
            title="codex only",
            body="codex body",
            visibility=AI_VISIBILITY_FULL,
            targets=["codex"],
            tags=["codex-tag"],
            directory=("codex-dir", "无", "无"),
        )
        self.create_note(
            title="hidden",
            body="hidden body",
            visibility=AI_VISIBILITY_HIDDEN,
            targets=["gale"],
            tags=["hidden-tag"],
            directory=("hidden-dir", "无", "无"),
        )
        self.create_note(
            title="deleted",
            body="deleted body",
            visibility=AI_VISIBILITY_FULL,
            targets=["gale"],
            tags=["deleted-tag"],
            directory=("deleted-dir", "无", "无"),
            deleted=True,
        )

        safe_notes, warning = materialize_ai_safe_notes_for_target(
            "gale",
            database_path=self.database_path,
            redaction_rules_path=self.rules_path,
        )

        self.assertEqual(warning, "")
        self.assertEqual(
            {note["note_id"] for note in safe_notes},
            {common_id, gale_id},
        )
        full = next(note for note in safe_notes if note["note_id"] == common_id)
        self.assertEqual(full["title"], "FULL_TITLE_CANARY")
        self.assertEqual(full["body"], "FULL_BODY_CANARY")
        self.assertEqual(full["directory"], "FULL_DIR_CANARY / 无 / 无")
        self.assertEqual(full["tags"], ["FULL_TAG_CANARY"])
        self.assertFalse(full["redacted"])

        plan = AiSafePackageExporter(
            database_path=self.database_path,
            output_root=self.output_root,
            redaction_rules_path=self.rules_path,
        ).build_plan("gale")
        rendered_full = next(
            item
            for item in plan.rendered_notes
            if item.safe_note["note_id"] == common_id
        )
        self.assertFalse(rendered_full.redacted)
        self.assertEqual(rendered_full.redaction_count, 0)
        for full_canary in (
            "FULL_TITLE_CANARY",
            "FULL_BODY_CANARY",
            "FULL_DIR_CANARY",
            "FULL_TAG_CANARY",
        ):
            self.assertIn(full_canary, rendered_full.markdown)

    def test_redacted_fields_manifest_and_source_are_non_destructive(self) -> None:
        redacted_id = self.create_note(
            title="RAW_TITLE_SECRET",
            body="RAW_BODY_SECRET",
            visibility=AI_VISIBILITY_REDACTED,
            targets=["gale"],
            tags=["RAW_TAG_SECRET"],
            directory=("RAW_DIR_SECRET", "无", "无"),
        )
        with _connect(self.database_path) as connection:
            source_row_before = dict(
                connection.execute(
                    "SELECT * FROM notes WHERE id=?",
                    (redacted_id,),
                ).fetchone()
            )
            relation_rows_before = [
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT note_id, tag_id FROM note_tags WHERE note_id=?
                    UNION ALL
                    SELECT note_id, target_id FROM note_ai_targets WHERE note_id=?
                    """,
                    (redacted_id, redacted_id),
                ).fetchall()
            ]

        raw_note = dict(source_row_before)
        raw_note_before = copy.deepcopy(raw_note)
        pure_safe = materialize_ai_safe_note(
            raw_note,
            authorized=True,
            replacements=[
                ("RAW_TITLE_SECRET", "[TITLE_REDACTED]"),
                ("RAW_BODY_SECRET", "[BODY_REDACTED]"),
                ("RAW_DIR_SECRET", "[DIR_REDACTED]"),
                ("RAW_TAG_SECRET", "[TAG_REDACTED]"),
            ],
            body_renderer=lambda value: value,
            redaction_transform=apply_redactions,
            tag_records=[{"tag_id": 1, "name": "RAW_TAG_SECRET"}],
        )
        self.assertEqual(raw_note, raw_note_before)
        self.assertIsNotNone(pure_safe)

        safe_notes, warning = materialize_ai_safe_notes_for_target(
            "gale",
            database_path=self.database_path,
            redaction_rules_path=self.rules_path,
        )
        self.assertEqual(warning, "")
        self.assertEqual(len(safe_notes), 1)
        safe_note = safe_notes[0]
        serialized_safe_note = json.dumps(safe_note, ensure_ascii=False)
        for raw_canary in (
            "RAW_TITLE_SECRET",
            "RAW_BODY_SECRET",
            "RAW_DIR_SECRET",
            "RAW_TAG_SECRET",
        ):
            self.assertNotIn(raw_canary, serialized_safe_note)
        self.assertEqual(safe_note["title"], "[TITLE_REDACTED]")
        self.assertEqual(safe_note["body"], "[BODY_REDACTED]")
        self.assertEqual(
            safe_note["directory"],
            "[DIR_REDACTED] / 无 / 无",
        )
        self.assertEqual(safe_note["tags"], ["[TAG_REDACTED]"])

        exporter = AiSafePackageExporter(
            database_path=self.database_path,
            output_root=self.output_root,
            redaction_rules_path=self.rules_path,
        )
        plan = exporter.build_plan("gale")
        self.assertEqual(len(plan.rendered_notes), 1)
        rendered = plan.rendered_notes[0]
        self.assertNotIn("RAW_", rendered.markdown)
        self.assertNotIn("RAW_TITLE_SECRET", rendered.filename)
        result = exporter.write_plan(plan)
        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["notes"][0]["title"], "[TITLE_REDACTED]")
        self.assertNotIn(
            "RAW_",
            json.dumps(manifest, ensure_ascii=False),
        )
        markdown = (
            result.package_dir / manifest["notes"][0]["file"]
        ).read_text(encoding="utf-8")
        self.assertNotIn("RAW_", markdown)

        with _connect(self.database_path) as connection:
            source_row_after = dict(
                connection.execute(
                    "SELECT * FROM notes WHERE id=?",
                    (redacted_id,),
                ).fetchone()
            )
            relation_rows_after = [
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT note_id, tag_id FROM note_tags WHERE note_id=?
                    UNION ALL
                    SELECT note_id, target_id FROM note_ai_targets WHERE note_id=?
                    """,
                    (redacted_id, redacted_id),
                ).fetchall()
            ]
            side_effect_counts = {
                table: connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in (
                    "ai_call_logs",
                    "ai_operations",
                    "ai_receipt_inbox",
                )
            }
        self.assertEqual(source_row_after, source_row_before)
        self.assertEqual(relation_rows_after, relation_rows_before)
        self.assertEqual(
            side_effect_counts,
            {
                "ai_call_logs": 0,
                "ai_operations": 0,
                "ai_receipt_inbox": 0,
            },
        )

    def test_hidden_or_unauthorized_raw_note_has_no_safe_record(self) -> None:
        raw_note = {
            "id": 7,
            "directory_id": 1,
            "title": "hidden",
            "content_html": "body",
            "main_category": "dir",
            "sub_category": "无",
            "sub_sub_category": "无",
            "tags": "tag",
            "ai_visibility": AI_VISIBILITY_HIDDEN,
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }
        options = {
            "replacements": [],
            "body_renderer": lambda value: value,
            "redaction_transform": apply_redactions,
        }
        self.assertIsNone(
            materialize_ai_safe_note(
                raw_note,
                authorized=True,
                **options,
            )
        )
        full_note = {**raw_note, "ai_visibility": AI_VISIBILITY_FULL}
        self.assertIsNone(
            materialize_ai_safe_note(
                full_note,
                authorized=False,
                **options,
            )
        )


if __name__ == "__main__":
    unittest.main()
