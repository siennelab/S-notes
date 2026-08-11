from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from src.app_paths import (
    AI_READBOX_DIR,
    APP_SETTINGS_PATH,
    APP_NAME,
    BACKUPS_DIR,
    CONFIG_DIR,
    DATA_ROOT_ENV_NAME,
    DATABASE_PATH,
    DataRootError,
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


class AppPathsTest(unittest.TestCase):
    def test_default_data_root_uses_macos_application_support(self) -> None:
        fake_home = Path("/Users/example")
        self.assertEqual(
            default_data_root(fake_home, environ={}),
            fake_home / "Library" / "Application Support" / APP_NAME,
        )

    def test_explicit_override_cannot_name_canonical_formal_root(self) -> None:
        fake_home = Path("/Users/example")
        formal_root = default_data_root(fake_home, environ={})
        with self.assertRaisesRegex(DataRootError, "formal S-notes data root"):
            default_data_root(
                fake_home,
                environ={DATA_ROOT_ENV_NAME: str(formal_root)},
            )

    def test_current_working_directory_does_not_change_default_path(self) -> None:
        original_cwd = Path.cwd()
        expected = default_data_root(environ={})
        with tempfile.TemporaryDirectory() as temporary_directory:
            try:
                os.chdir(temporary_directory)
                self.assertEqual(default_data_root(environ={}), expected)
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
                paths.app_settings,
                root / "config" / "app_settings.ini",
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
            APP_SETTINGS_PATH,
            CONFIG_DIR / "app_settings.ini",
        )
        self.assertEqual(
            GALE_PUBLISH_STATE_PATH,
            CONFIG_DIR / "gale_publish_state.json",
        )
        self.assertEqual(
            GALE_PUBLISH_LOCK_PATH,
            CONFIG_DIR / "gale_publish.lock",
        )

    def test_invalid_environment_override_fails_without_default_fallback(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            blocker = Path(temporary_directory) / "not-a-directory"
            blocker.write_text("fictional blocker", encoding="utf-8")
            invalid_root = blocker / "child"

            with self.assertRaisesRegex(
                DataRootError,
                "not creatable and writable",
            ):
                default_data_root(
                    environ={DATA_ROOT_ENV_NAME: str(invalid_root)}
                )

            self.assertNotEqual(
                invalid_root,
                default_data_root(
                    Path("/Users/example"),
                    environ={},
                ),
            )

    def test_empty_or_relative_environment_override_fails_closed(self) -> None:
        for value in ("", "relative/path"):
            with self.subTest(value=value):
                with self.assertRaises(DataRootError):
                    default_data_root(
                        environ={DATA_ROOT_ENV_NAME: value}
                    )

if __name__ == "__main__":
    unittest.main()
