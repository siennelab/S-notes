from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.app_paths import (
    AI_READBOX_DIR,
    APP_NAME,
    BACKUPS_DIR,
    CONFIG_DIR,
    DATABASE_PATH,
    EXPORTS_ROOT,
    GALE_EXPORT_DIR,
    GALE_MIRROR_PATH,
    GALE_PUBLISH_LOCK_PATH,
    GALE_PUBLISH_STATE_PATH,
    IMPORTS_DIR,
    JSON_EXPORT_DIR,
    MARKDOWN_EXPORT_DIR,
    REDACTION_RULES_PATH,
    USER_DATA_ROOT,
    app_paths,
    default_data_root,
)
from src.database import initialize_database


class AppPathsTest(unittest.TestCase):
    def test_default_data_root_uses_macos_application_support(self) -> None:
        fake_home = Path("/Users/example")
        self.assertEqual(
            default_data_root(fake_home),
            fake_home / "Library" / "Application Support" / APP_NAME,
        )

    def test_current_working_directory_does_not_change_default_path(self) -> None:
        original_cwd = Path.cwd()
        expected = default_data_root()
        with tempfile.TemporaryDirectory() as temporary_directory:
            try:
                os.chdir(temporary_directory)
                self.assertEqual(default_data_root(), expected)
            finally:
                os.chdir(original_cwd)

    def test_injected_root_provides_all_user_writable_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "S-notes-test-data"
            paths = app_paths(root)

            self.assertEqual(paths.database, root / "s_notes.db")
            self.assertEqual(paths.backups, root / "backups")
            self.assertEqual(paths.exports, root / "exports")
            self.assertEqual(paths.markdown_exports, root / "exports" / "markdown")
            self.assertEqual(paths.json_exports, root / "exports" / "json")
            self.assertEqual(paths.ai_readbox, root / "exports" / "ai_readbox")
            self.assertEqual(paths.gale_exports, root / "exports" / "gale")
            self.assertEqual(
                paths.gale_mirror,
                root / "exports" / "gale" / "gale-mirror.db",
            )
            self.assertEqual(paths.imports, root / "imports")
            self.assertEqual(paths.config, root / "config")
            self.assertEqual(
                paths.redaction_rules,
                root / "config" / "local_redaction_rules.json",
            )
            self.assertEqual(
                paths.gale_publish_state,
                root / "config" / "gale_publish_state.json",
            )
            self.assertEqual(
                paths.gale_publish_lock,
                root / "config" / "gale_publish.lock",
            )
            self.assertFalse(root.exists())

    def test_runtime_constants_share_the_default_data_root(self) -> None:
        self.assertEqual(DATABASE_PATH, USER_DATA_ROOT / "s_notes.db")
        self.assertEqual(BACKUPS_DIR, USER_DATA_ROOT / "backups")
        self.assertEqual(EXPORTS_ROOT, USER_DATA_ROOT / "exports")
        self.assertEqual(MARKDOWN_EXPORT_DIR, EXPORTS_ROOT / "markdown")
        self.assertEqual(JSON_EXPORT_DIR, EXPORTS_ROOT / "json")
        self.assertEqual(AI_READBOX_DIR, EXPORTS_ROOT / "ai_readbox")
        self.assertEqual(GALE_EXPORT_DIR, EXPORTS_ROOT / "gale")
        self.assertEqual(
            GALE_MIRROR_PATH,
            GALE_EXPORT_DIR / "gale-mirror.db",
        )
        self.assertEqual(IMPORTS_DIR, USER_DATA_ROOT / "imports")
        self.assertEqual(CONFIG_DIR, USER_DATA_ROOT / "config")
        self.assertEqual(
            REDACTION_RULES_PATH,
            CONFIG_DIR / "local_redaction_rules.json",
        )
        self.assertEqual(
            GALE_PUBLISH_STATE_PATH,
            CONFIG_DIR / "gale_publish_state.json",
        )
        self.assertEqual(
            GALE_PUBLISH_LOCK_PATH,
            CONFIG_DIR / "gale_publish.lock",
        )

    def test_runtime_modules_use_the_central_paths(self) -> None:
        from src import ai_safe_export, export_center, redaction

        self.assertEqual(export_center.MARKDOWN_EXPORT_DIR, MARKDOWN_EXPORT_DIR)
        self.assertEqual(export_center.JSON_EXPORT_DIR, JSON_EXPORT_DIR)
        self.assertEqual(export_center.SQLITE_BACKUP_DIR, BACKUPS_DIR)
        self.assertEqual(ai_safe_export.AI_READBOX_DIR, AI_READBOX_DIR)
        self.assertEqual(redaction.REDACTION_RULES_PATH, REDACTION_RULES_PATH)

    def test_database_initialization_uses_only_injected_temporary_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            paths = app_paths(Path(temporary_directory) / "isolated-data")
            result = initialize_database(paths.database)

            self.assertEqual(result, paths.database)
            self.assertTrue(paths.database.is_file())
            with sqlite3.connect(paths.database) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
            self.assertTrue(
                {
                    "directories",
                    "notes",
                    "tags",
                    "note_tags",
                    "note_ai_targets",
                    "memory_receipts",
                    "ai_receipt_inbox",
                }.issubset(tables)
            )


if __name__ == "__main__":
    unittest.main()
