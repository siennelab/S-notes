from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
from src.services.gale_mirror_service import (
    CATALOG_TABLES,
    MIRROR_SCHEMA_VERSION,
    GaleMirrorError,
    GaleMirrorSafeRecord,
    _write_gale_mirror_database,
    export_gale_mirror,
    search_gale_mirror,
    verify_gale_mirror,
)
from src.services.ai_safe_materialization import materialize_ai_safe_note
from src.time_utils import now_shanghai_iso


RAW_CANARIES = (
    "RAW_TITLE_SECRET",
    "RAW_BODY_SECRET",
    "RAW_DIR_SECRET",
    "RAW_TAG_SECRET",
)


class GaleMirrorExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.source_database = self.root / "source.db"
        self.rules_path = self.root / "fictional_rules.json"
        self.output_path = self.root / "gale-mirror.db"
        initialize_database(self.source_database)
        self.rules_path.write_text(
            json.dumps(
                {
                    "RAW_TITLE_SECRET": "MASKED_TITLE_TERM",
                    "RAW_BODY_SECRET": "MASKED_BODY_TERM",
                    "RAW_DIR_SECRET": "MASKED_DIR_TERM",
                    "RAW_TAG_SECRET": "MASKED_TAG_TERM",
                }
            ),
            encoding="utf-8",
        )
        self.note_ids = self._seed_fictional_matrix()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _create_note(
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
        connection = _connect(self.source_database)
        try:
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
            connection.commit()
            return note_id
        finally:
            connection.close()

    def _seed_fictional_matrix(self) -> dict[str, int]:
        return {
            "common_full": self._create_note(
                title="FULL_TITLE_TERM",
                body="FULL_BODY_TERM",
                visibility=AI_VISIBILITY_FULL,
                targets=["common"],
                tags=["FULL_TAG_TERM"],
                directory=("FULL_DIR_TERM", "无", "无"),
            ),
            "gale_full": self._create_note(
                title="GALE_TITLE_TERM",
                body="GALE_BODY_TERM",
                visibility=AI_VISIBILITY_FULL,
                targets=["gale"],
                tags=["GALE_TAG_TERM"],
                directory=("GALE_DIR_TERM", "无", "无"),
            ),
            "gale_redacted": self._create_note(
                title="RAW_TITLE_SECRET",
                body="RAW_BODY_SECRET",
                visibility=AI_VISIBILITY_REDACTED,
                targets=["gale"],
                tags=["RAW_TAG_SECRET"],
                directory=("RAW_DIR_SECRET", "无", "无"),
            ),
            "codex_full": self._create_note(
                title="CODEX_ONLY_TITLE_TERM",
                body="CODEX_ONLY_BODY_TERM",
                visibility=AI_VISIBILITY_FULL,
                targets=["codex"],
                tags=["CODEX_ONLY_TAG_TERM"],
                directory=("CODEX_ONLY_DIR_TERM", "无", "无"),
            ),
            "keden_redacted": self._create_note(
                title="KEDEN_ONLY_RAW_TERM",
                body="KEDEN_ONLY_RAW_TERM",
                visibility=AI_VISIBILITY_REDACTED,
                targets=["ke-den"],
                tags=["KEDEN_ONLY_RAW_TERM"],
                directory=("KEDEN_ONLY_RAW_TERM", "无", "无"),
            ),
            "hidden": self._create_note(
                title="HIDDEN_TITLE_TERM",
                body="HIDDEN_BODY_TERM",
                visibility=AI_VISIBILITY_HIDDEN,
                targets=["gale"],
                tags=["HIDDEN_TAG_TERM"],
                directory=("HIDDEN_DIR_TERM", "无", "无"),
            ),
            "deleted": self._create_note(
                title="DELETED_TITLE_TERM",
                body="DELETED_BODY_TERM",
                visibility=AI_VISIBILITY_FULL,
                targets=["gale"],
                tags=["DELETED_TAG_TERM"],
                directory=("DELETED_DIR_TERM", "无", "无"),
                deleted=True,
            ),
        }

    def _source_snapshot(self) -> dict:
        connection = _connect(self.source_database)
        try:
            notes = [
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT id, title, content_html, main_category,
                           sub_category, sub_sub_category, ai_visibility,
                           is_deleted, updated_at
                    FROM notes
                    ORDER BY id
                    """
                ).fetchall()
            ]
            tags = [
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT note_tags.note_id, tags.name
                    FROM note_tags
                    JOIN tags ON tags.id=note_tags.tag_id
                    ORDER BY note_tags.note_id, tags.name
                    """
                ).fetchall()
            ]
            targets = [
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT note_id, target_id
                    FROM note_ai_targets
                    ORDER BY note_id, target_id
                    """
                ).fetchall()
            ]
            side_effect_counts = {
                table: int(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
                for table in (
                    "ai_call_logs",
                    "ai_operations",
                    "ai_receipt_inbox",
                )
            }
            return {
                "notes": notes,
                "tags": tags,
                "targets": targets,
                "side_effect_counts": side_effect_counts,
            }
        finally:
            connection.close()

    def _assert_no_temporary_mirror_files(self) -> None:
        self.assertEqual(
            list(self.root.glob(f".{self.output_path.name}.*.tmp*")),
            [],
        )
        for suffix in ("-journal", "-wal", "-shm"):
            self.assertFalse(Path(f"{self.output_path}{suffix}").exists())

    def test_export_matrix_redaction_search_and_source_are_safe(self) -> None:
        source_hash_before = _sha256(self.source_database)
        source_snapshot_before = self._source_snapshot()

        exported = export_gale_mirror(
            source_database=self.source_database,
            output_path=self.output_path,
            redaction_rules_path=self.rules_path,
            actor="gale",
            generator_app_version="fictional-remote-contract-test",
        )
        self.assertEqual(exported.note_count, 3)
        self.assertEqual(exported.schema_version, MIRROR_SCHEMA_VERSION)
        self.assertEqual(exported.target_actor, "gale")
        self.assertEqual(exported.file_mode, "0600")

        verification = verify_gale_mirror(
            self.output_path,
            expected_note_count=3,
        )
        self.assertEqual(verification.note_count, 3)
        self.assertEqual(verification.fts_count, 3)
        self.assertEqual(verification.full_note_count, 2)
        self.assertEqual(verification.redacted_note_count, 1)
        self.assertEqual(verification.file_mode, "0600")
        self.assertEqual(
            stat.S_IMODE(self.output_path.stat().st_mode),
            0o600,
        )
        connection = sqlite3.connect(
            f"{self.output_path.resolve().as_uri()}?mode=ro",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        try:
            mirrored_rows = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM mirror_notes ORDER BY source_note_id"
                ).fetchall()
            ]
            fts_rows = [
                tuple(row)
                for row in connection.execute(
                    """
                    SELECT title, body, directory, tags
                    FROM mirror_notes_fts
                    ORDER BY rowid
                    """
                ).fetchall()
            ]
            metadata = {
                row["meta_key"]: row["meta_value"]
                for row in connection.execute(
                    "SELECT meta_key, meta_value FROM mirror_metadata"
                ).fetchall()
            }
            table_names = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type='table' AND name NOT LIKE 'sqlite_%'
                    """
                ).fetchall()
            }
        finally:
            connection.close()

        self.assertEqual(
            {row["source_note_id"] for row in mirrored_rows},
            {
                self.note_ids["common_full"],
                self.note_ids["gale_full"],
                self.note_ids["gale_redacted"],
            },
        )
        by_source_id = {
            row["source_note_id"]: row for row in mirrored_rows
        }
        common = by_source_id[self.note_ids["common_full"]]
        self.assertEqual(common["title"], "FULL_TITLE_TERM")
        self.assertEqual(common["body"], "FULL_BODY_TERM")
        self.assertEqual(common["directory"], "FULL_DIR_TERM / 无 / 无")
        self.assertEqual(json.loads(common["tags_json"]), ["FULL_TAG_TERM"])
        self.assertEqual(common["visibility"], "full")
        self.assertEqual(common["redacted"], 0)

        gale_full = by_source_id[self.note_ids["gale_full"]]
        self.assertEqual(gale_full["title"], "GALE_TITLE_TERM")
        self.assertEqual(gale_full["body"], "GALE_BODY_TERM")
        self.assertEqual(gale_full["directory"], "GALE_DIR_TERM / 无 / 无")
        self.assertEqual(json.loads(gale_full["tags_json"]), ["GALE_TAG_TERM"])
        self.assertEqual(gale_full["visibility"], "full")
        self.assertEqual(gale_full["redacted"], 0)

        redacted = by_source_id[self.note_ids["gale_redacted"]]
        self.assertEqual(redacted["title"], "MASKED_TITLE_TERM")
        self.assertEqual(redacted["body"], "MASKED_BODY_TERM")
        self.assertEqual(
            redacted["directory"],
            "RAW_DIR_SECRET / 无 / 无",
        )
        self.assertEqual(
            json.loads(redacted["tags_json"]),
            ["RAW_TAG_SECRET"],
        )
        self.assertEqual(redacted["visibility"], "redacted")
        self.assertEqual(redacted["redacted"], 1)

        serialized_mirror = json.dumps(
            {
                "notes": mirrored_rows,
                "fts": fts_rows,
                "metadata": metadata,
            },
            ensure_ascii=False,
        )
        mirror_bytes = self.output_path.read_bytes()
        for raw_canary in RAW_CANARIES[:2]:
            self.assertNotIn(raw_canary, serialized_mirror)
            self.assertNotIn(raw_canary.encode("utf-8"), mirror_bytes)
        for excluded_canary in (
            "CODEX_ONLY_TITLE_TERM",
            "KEDEN_ONLY_RAW_TERM",
            "HIDDEN_TITLE_TERM",
            "DELETED_TITLE_TERM",
        ):
            self.assertNotIn(excluded_canary, serialized_mirror)

        self.assertEqual(
            table_names,
            {
                "mirror_metadata",
                "mirror_notes",
                "mirror_notes_fts",
                "mirror_notes_fts_data",
                "mirror_notes_fts_idx",
                "mirror_notes_fts_content",
                "mirror_notes_fts_docsize",
                "mirror_notes_fts_config",
            }
            | CATALOG_TABLES,
        )
        self.assertEqual(metadata["target_actor"], "gale")
        self.assertEqual(metadata["note_count"], "3")
        self.assertEqual(metadata["mirror_schema_version"], MIRROR_SCHEMA_VERSION)

        self.assertEqual(
            _result_ids(search_gale_mirror(self.output_path, "FULL_TITLE_TERM")),
            {self.note_ids["common_full"]},
        )
        self.assertEqual(
            _result_ids(search_gale_mirror(self.output_path, "FULL_BODY_TERM")),
            {self.note_ids["common_full"]},
        )
        self.assertEqual(
            _result_ids(search_gale_mirror(self.output_path, "FULL_DIR_TERM")),
            {self.note_ids["common_full"]},
        )
        self.assertEqual(
            _result_ids(search_gale_mirror(self.output_path, "FULL_TAG_TERM")),
            {self.note_ids["common_full"]},
        )
        self.assertEqual(
            _result_ids(search_gale_mirror(self.output_path, "MASKED_BODY_TERM")),
            {self.note_ids["gale_redacted"]},
        )
        for safe_redacted_query in (
            "MASKED_TITLE_TERM",
            "MASKED_BODY_TERM",
            "RAW_DIR_SECRET",
            "RAW_TAG_SECRET",
        ):
            self.assertEqual(
                _result_ids(
                    search_gale_mirror(
                        self.output_path,
                        safe_redacted_query,
                    )
                ),
                {self.note_ids["gale_redacted"]},
            )
        for raw_canary in RAW_CANARIES[:2]:
            self.assertEqual(
                search_gale_mirror(self.output_path, raw_canary),
                [],
            )
        for raw_canary in RAW_CANARIES[2:]:
            self.assertEqual(
                _result_ids(search_gale_mirror(self.output_path, raw_canary)),
                {self.note_ids["gale_redacted"]},
            )
        for excluded_query in (
            "CODEX_ONLY_TITLE_TERM",
            "KEDEN_ONLY_RAW_TERM",
            "HIDDEN_TITLE_TERM",
            "DELETED_TITLE_TERM",
        ):
            self.assertEqual(
                search_gale_mirror(self.output_path, excluded_query),
                [],
            )

        self.assertEqual(_sha256(self.source_database), source_hash_before)
        self.assertEqual(self._source_snapshot(), source_snapshot_before)
        self.assertEqual(
            source_snapshot_before["side_effect_counts"],
            {
                "ai_call_logs": 0,
                "ai_operations": 0,
                "ai_receipt_inbox": 0,
            },
        )
        self._assert_no_temporary_mirror_files()

    def test_atomic_replace_repeat_and_read_only_source_connection(self) -> None:
        self.output_path.write_bytes(b"OLD_MIRROR_BYTES")
        old_bytes = self.output_path.read_bytes()
        real_connect = sqlite3.connect
        connect_calls: list[tuple[object, dict]] = []

        def recording_connect(database, *args, **kwargs):
            connect_calls.append((database, dict(kwargs)))
            return real_connect(database, *args, **kwargs)

        with patch(
            "src.services.ai_access_service.sqlite3.connect",
            side_effect=recording_connect,
        ):
            first = export_gale_mirror(
                source_database=self.source_database,
                output_path=self.output_path,
                redaction_rules_path=self.rules_path,
            )

        self.assertNotEqual(self.output_path.read_bytes(), old_bytes)
        self.assertEqual(first.note_count, 3)
        source_uri = f"{self.source_database.resolve().as_uri()}?mode=ro"
        self.assertTrue(
            any(
                database == source_uri and kwargs.get("uri") is True
                for database, kwargs in connect_calls
            ),
            connect_calls,
        )

        second = export_gale_mirror(
            source_database=self.source_database,
            output_path=self.output_path,
            redaction_rules_path=self.rules_path,
        )
        verification = verify_gale_mirror(
            self.output_path,
            expected_note_count=3,
        )
        self.assertEqual(second.note_count, 3)
        self.assertEqual(verification.note_count, 3)
        self.assertEqual(verification.fts_count, 3)
        self.assertEqual(
            len(search_gale_mirror(self.output_path, "FULL_TITLE_TERM")),
            1,
        )
        self._assert_no_temporary_mirror_files()

    def test_failed_validation_preserves_old_mirror_and_same_path_is_rejected(
        self,
    ) -> None:
        export_gale_mirror(
            source_database=self.source_database,
            output_path=self.output_path,
            redaction_rules_path=self.rules_path,
        )
        old_hash = _sha256(self.output_path)

        with patch(
            "src.services.gale_mirror_service.verify_gale_mirror",
            side_effect=GaleMirrorError("forced_validation_failure"),
        ):
            with self.assertRaisesRegex(
                GaleMirrorError,
                "forced_validation_failure",
            ):
                export_gale_mirror(
                    source_database=self.source_database,
                    output_path=self.output_path,
                    redaction_rules_path=self.rules_path,
                )

        self.assertEqual(_sha256(self.output_path), old_hash)
        self._assert_no_temporary_mirror_files()

        with self.assertRaisesRegex(
            GaleMirrorError,
            "source_output_conflict",
        ):
            export_gale_mirror(
                source_database=self.source_database,
                output_path=self.source_database,
                redaction_rules_path=self.rules_path,
            )
        self.assertTrue(self.source_database.is_file())

    def test_verify_rejects_non_0600_mode_and_rowid_set_mismatch(self) -> None:
        export_gale_mirror(
            source_database=self.source_database,
            output_path=self.output_path,
            redaction_rules_path=self.rules_path,
        )

        os.chmod(self.output_path, 0o640)
        with self.assertRaisesRegex(
            GaleMirrorError,
            "mirror_permissions_unsafe",
        ):
            verify_gale_mirror(self.output_path)
        os.chmod(self.output_path, 0o600)

        connection = sqlite3.connect(self.output_path)
        try:
            original = connection.execute(
                """
                SELECT title, body, directory, tags
                FROM mirror_notes_fts
                WHERE rowid=1
                """
            ).fetchone()
            self.assertIsNotNone(original)
            connection.execute("DELETE FROM mirror_notes_fts WHERE rowid=1")
            connection.execute(
                """
                INSERT INTO mirror_notes_fts(rowid, title, body, directory, tags)
                VALUES (9999, ?, ?, ?, ?)
                """,
                original,
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(GaleMirrorError, "fts_rowid_mismatch"):
            verify_gale_mirror(self.output_path)

    def test_writer_rejects_raw_mapping_and_materialization_is_non_destructive(
        self,
    ) -> None:
        raw_note = {
            "id": 99,
            "directory_id": 7,
            "title": "RAW_TITLE_SECRET",
            "content_html": "RAW_BODY_SECRET",
            "main_category": "RAW_DIR_SECRET",
            "sub_category": "无",
            "sub_sub_category": "无",
            "tags": "RAW_TAG_SECRET",
            "ai_visibility": AI_VISIBILITY_REDACTED,
            "created_at": "2026-01-01T00:00:00+08:00",
            "updated_at": "2026-01-01T00:00:00+08:00",
        }
        tag_records = [{"tag_id": 3, "name": "RAW_TAG_SECRET"}]
        raw_before = copy.deepcopy(raw_note)
        tags_before = copy.deepcopy(tag_records)

        safe_note = materialize_ai_safe_note(
            raw_note,
            authorized=True,
            replacements=[
                ("RAW_TITLE_SECRET", "MASKED_TITLE_TERM"),
                ("RAW_BODY_SECRET", "MASKED_BODY_TERM"),
                ("RAW_DIR_SECRET", "MASKED_DIR_TERM"),
                ("RAW_TAG_SECRET", "MASKED_TAG_TERM"),
            ],
            body_renderer=lambda value: value,
            redaction_transform=apply_redactions,
            tag_records=tag_records,
        )
        self.assertIsNotNone(safe_note)
        GaleMirrorSafeRecord.from_materialized(safe_note)
        self.assertEqual(raw_note, raw_before)
        self.assertEqual(tag_records, tags_before)

        rejected_path = self.root / "raw-rejected.db"
        with self.assertRaisesRegex(GaleMirrorError, "raw_record_rejected"):
            _write_gale_mirror_database(
                rejected_path,
                [raw_note],
                actor="gale",
                generator_app_version="fictional-test",
            )
        self.assertFalse(rejected_path.exists())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _result_ids(results: list[dict]) -> set[int]:
    return {int(item["source_note_id"]) for item in results}


if __name__ == "__main__":
    unittest.main()
