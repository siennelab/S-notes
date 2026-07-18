from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.database import (
    _connect,
    _ensure_directory_path,
    _plain_text_to_html,
    _set_note_ai_targets_with_connection,
    _set_note_tags_with_connection,
    initialize_database,
)
from src.services import ai_access_service as service
from src.time_utils import now_shanghai_iso


class AiTask13BServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "s_notes.db"
        self.rules_path = self.root / "rules.json"
        initialize_database(self.database_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def create_note(
        self,
        title: str,
        *,
        body: str = "body",
        visibility: str = "full",
        targets: list[str] | None = None,
        tags: list[str] | None = None,
        directory: tuple[str, str, str] = ("临时", "无", "无"),
    ) -> int:
        timestamp = now_shanghai_iso()
        with _connect(self.database_path) as connection:
            directory_id = _ensure_directory_path(connection, *directory)
            content = _plain_text_to_html(body)
            cursor = connection.execute(
                """
                INSERT INTO notes (
                    directory_id, title, body_rich_text, content_html,
                    main_category, sub_category, sub_sub_category, tags,
                    ai_visibility, is_pinned, is_deleted, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, 0, 0, ?, ?)
                """,
                (
                    directory_id, title, content, content,
                    directory[0], directory[1], directory[2], visibility,
                    timestamp, timestamp,
                ),
            )
            note_id = int(cursor.lastrowid)
            _set_note_tags_with_connection(connection, note_id, tags or [], timestamp)
            _set_note_ai_targets_with_connection(connection, note_id, targets or ["gale"], timestamp)
        return note_id

    def test_version_credentials_normalize_representation_without_losing_precision(self) -> None:
        current = "2026-07-18T21:14:02.123456+08:00"
        self.assertTrue(service._timestamp_credentials_match(current, current))
        self.assertTrue(
            service._timestamp_credentials_match(
                "2026-07-18T21:14:02+08:00",
                '  "2026-07-18T13:14:02.000000Z"  ',
            )
        )
        self.assertFalse(
            service._timestamp_credentials_match(
                current,
                "2026-07-18T21:14:02+08:00",
            )
        )
        self.assertFalse(
            service._timestamp_credentials_match(
                current,
                "2026-07-18T21:14:02.123456",
            )
        )
        token = service.note_version_token(7, current)
        self.assertRegex(token, r"^snotes-v1-[0-9a-f]{64}$")
        self.assertNotEqual(token, service.note_version_token(7, current[:-6] + "+00:00"))

    def test_structured_facets_and_filters_use_only_safe_visible_notes(self) -> None:
        visible_id = self.create_note(
            "Visible", tags=["alpha"], directory=("项目", "可见", "无")
        )
        self.create_note(
            "Hidden", visibility="hidden", tags=["hidden-only"],
            directory=("项目", "隐藏", "无"),
        )
        self.create_note(
            "Codex", targets=["codex"], tags=["codex-only"],
            directory=("项目", "Codex", "无"),
        )
        directories = service.safe_list_directory_records(
            "gale", database_path=self.database_path, redaction_rules_path=self.rules_path
        )
        tags = service.safe_list_tag_records(
            "gale", database_path=self.database_path, redaction_rules_path=self.rules_path
        )
        self.assertEqual(len(directories), 1)
        self.assertTrue(directories[0]["writable"])
        self.assertEqual([item["name"] for item in tags], ["alpha"])

        by_directory = service.safe_search_notes(
            "", "gale", directory_id=directories[0]["directory_id"],
            database_path=self.database_path, redaction_rules_path=self.rules_path,
        )
        by_tag = service.safe_search_notes(
            "", "gale", tag_id=tags[0]["tag_id"],
            database_path=self.database_path, redaction_rules_path=self.rules_path,
        )
        self.assertEqual([item["note_id"] for item in by_directory["items"]], [visible_id])
        self.assertEqual([item["note_id"] for item in by_tag["items"]], [visible_id])
        self.assertEqual(
            service.safe_search_notes(
                "", "gale", directory_id=999999, database_path=self.database_path,
                redaction_rules_path=self.rules_path,
            )["total_count"],
            0,
        )

    def test_directory_id_create_metadata_deltas_and_no_change(self) -> None:
        note_id = self.create_note(
            "Base", tags=["keep", "remove"], directory=("项目", "目标", "无")
        )
        directory = service.safe_list_directory_records(
            "gale", database_path=self.database_path, redaction_rules_path=self.rules_path
        )[0]
        created = service.ai_create_note(
            "gale", "13b-create-dir", title="Created", body="plain <script>x</script>",
            directory_id=directory["directory_id"], database_path=self.database_path,
        )
        self.assertEqual(created["status"], "created")
        created_note = service.safe_get_note_by_id(
            created["note_id"], "gale", database_path=self.database_path,
            redaction_rules_path=self.rules_path,
        )
        self.assertEqual(created_note["directory"], "项目 / 目标 / 无")
        self.assertIn("<script>x</script>", created_note["body"])

        service.set_ai_setting(service.MODIFY_CONFIRMATION_KEY, False, self.database_path)
        note = service.safe_get_note_by_id(
            note_id, "gale", database_path=self.database_path,
            redaction_rules_path=self.rules_path,
        )
        no_change = service.ai_update_note_metadata(
            "gale", "13b-no-change", note_id, expected_updated_at=note["updated_at"],
            title="Base", database_path=self.database_path,
        )
        self.assertEqual(no_change["status"], "no_change")
        changed = service.ai_update_note_metadata(
            "gale", "13b-tag-delta", note_id, expected_updated_at=note["updated_at"],
            add_tags=["added"], remove_tags=["remove"], database_path=self.database_path,
        )
        self.assertEqual(changed["status"], "updated")
        after = service.safe_get_note_by_id(
            note_id, "gale", database_path=self.database_path,
            redaction_rules_path=self.rules_path,
        )
        self.assertEqual(set(after["tags"]), {"keep", "added"})
        self.assertNotEqual(after["updated_at"], note["updated_at"])

    def test_create_transaction_failure_rolls_back_note_operation_and_log(self) -> None:
        with patch.object(
            service,
            "_set_note_tags_with_connection",
            side_effect=RuntimeError("injected transaction failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected transaction failure"):
                service.ai_create_note(
                    "gale", "13b-rollback", title="Must rollback", body="body",
                    tags=["explode"], database_path=self.database_path,
                )
        with _connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM notes WHERE title='Must rollback'").fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM ai_operations WHERE request_id='13b-rollback'").fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM ai_call_logs WHERE request_id='13b-rollback'").fetchone()[0],
                0,
            )

    def test_read_only_runtime_health_requires_enabled_actor_and_exact_schema(self) -> None:
        healthy = service.inspect_ai_runtime("gale", database_path=self.database_path)
        self.assertEqual(healthy["status"], "ok")
        with _connect(self.database_path) as connection:
            connection.execute("UPDATE ai_targets SET is_enabled=0 WHERE target_id='gale'")
        disabled = service.inspect_ai_runtime("gale", database_path=self.database_path)
        self.assertEqual(disabled["reason"], "actor_unavailable")


if __name__ == "__main__":
    unittest.main()
