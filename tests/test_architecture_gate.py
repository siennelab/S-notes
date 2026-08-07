from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.check_architecture import DEFAULT_SOURCE_ROOT, inspect_tree


class ArchitectureGateTests(unittest.TestCase):
    def inspect_fixture(self, files: dict[str, str]):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        source_root = Path(temporary.name) / "src" / "snotes"
        for relative, content in files.items():
            path = source_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return inspect_tree(source_root)

    def test_current_r2_skeleton_passes(self) -> None:
        errors, _warnings = inspect_tree(DEFAULT_SOURCE_ROOT)
        self.assertEqual([], errors)

    def test_ui_importing_storage_fails(self) -> None:
        errors, _warnings = self.inspect_fixture(
            {"ui/bad.py": "from src.snotes.storage import connection\n"}
        )
        self.assertTrue(any(error.code == "dependency-direction" for error in errors))

    def test_ui_importing_storage_from_namespace_root_fails(self) -> None:
        errors, _warnings = self.inspect_fixture(
            {"ui/bad.py": "from src.snotes import storage\n"}
        )
        self.assertTrue(any(error.code == "dependency-direction" for error in errors))

    def test_application_importing_storage_fails(self) -> None:
        errors, _warnings = self.inspect_fixture(
            {"application/bad.py": "from src.snotes.storage import repository\n"}
        )
        self.assertTrue(any(error.code == "dependency-direction" for error in errors))

    def test_domain_importing_application_fails(self) -> None:
        errors, _warnings = self.inspect_fixture(
            {"domain/bad.py": "from src.snotes.application import ports\n"}
        )
        self.assertTrue(any(error.code == "dependency-direction" for error in errors))

    def test_storage_importing_application_ports_passes(self) -> None:
        errors, _warnings = self.inspect_fixture(
            {"storage/adapter.py": "from src.snotes.application.ports import NoteRepository\n"}
        )
        self.assertEqual([], errors)

    def test_sqlite_and_sql_outside_storage_fail(self) -> None:
        errors, _warnings = self.inspect_fixture(
            {
                "ui/sql.py": (
                    "import sqlite3\n"
                    "QUERY = 'SELECT title FROM notes'\n"
                )
            }
        )
        self.assertGreaterEqual(
            sum(error.code == "sql-outside-storage" for error in errors),
            2,
        )

    def test_cross_module_private_import_fails(self) -> None:
        errors, _warnings = self.inspect_fixture(
            {"ui/private.py": "from src.snotes.application.ports import _build\n"}
        )
        self.assertTrue(any(error.code == "private-import" for error in errors))


if __name__ == "__main__":
    unittest.main()
