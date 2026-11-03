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

    def test_current_production_boundaries_pass(self) -> None:
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

    def test_application_cannot_import_flat_concrete_publisher(self) -> None:
        for source in (
            "from src.services.actor_mirror_publish_service import ActorMirrorPublisher\n",
            "from src.services import actor_mirror_publish_service\n",
            "import src.services.actor_mirror_publish_service as publisher\n",
            "from src import cloud_outbox\n",
        ):
            with self.subTest(source=source):
                errors, _ = self.inspect_fixture({"application/bad.py": source})
                self.assertTrue(any(error.code == "dependency-direction" for error in errors))

    def test_application_cannot_import_unknown_project_module_or_bootstrap(self) -> None:
        for module in ("src.services.future_publisher", "src.snotes.gale_bootstrap"):
            with self.subTest(module=module):
                errors, _ = self.inspect_fixture({"application/bad.py": f"import {module}\n"})
                self.assertTrue(any(error.code == "dependency-direction" for error in errors))

    def test_external_production_module_is_checked(self) -> None:
        errors, _ = self.inspect_fixture({
            "../gale_remote_mcp.py": (
                "import sqlite3\n"
                "from src.services.gale_oauth import _private_helper\n"
            ),
        })
        self.assertTrue(any(error.code == "sql-outside-storage" for error in errors))
        self.assertTrue(any(error.code == "private-import" for error in errors))

    def test_deployed_runtime_dispatcher_is_checked(self) -> None:
        errors, _ = self.inspect_fixture({
            "../../deploy/greencloud/outbox/transport.py": "import sqlite3\n",
        })
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "sql-outside-storage")

    def test_remote_persistence_cannot_import_ui(self) -> None:
        for module in ("src.snotes.ui.shell", "src.snotes.application.note_service", "src.snotes.transport.reader_web"):
            with self.subTest(module=module):
                errors, _ = self.inspect_fixture({
                    "../services/gale_submission_outbox.py": f"import {module}\n",
                })
                self.assertTrue(any(error.code == "dependency-direction" for error in errors))

    def test_remote_persistence_owns_sql_without_weakening_r2(self) -> None:
        errors, _ = self.inspect_fixture({
            "../services/gale_submission_outbox.py": "import sqlite3\nQUERY = 'SELECT title FROM submissions'\n",
            "application/bad.py": "import sqlite3\nQUERY = 'SELECT title FROM notes'\n",
        })
        sql_errors = [error for error in errors if error.code == "sql-outside-storage"]
        self.assertEqual(len(sql_errors), 2)
        self.assertTrue(all(error.path.name == "bad.py" for error in sql_errors))

    def test_new_modules_cannot_hide_outside_known_layers(self) -> None:
        for path in ("../services/new_service.py", "../new_server.py", "unknown/bad.py", "new_root.py"):
            with self.subTest(path=path):
                errors, _ = self.inspect_fixture({path: "VALUE = 1\n"})
                self.assertTrue(any(error.code == "unclassified-production-module" for error in errors))

    def test_frozen_reference_is_not_a_new_r2_write_path(self) -> None:
        errors, _ = self.inspect_fixture({
            "../database.py": "import sqlite3\nfrom src.reference import _old_helper\n",
            "application/bad.py": "from src import database\n",
        })
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "dependency-direction")

    def test_live_compatibility_edge_does_not_allow_new_legacy_consumers(self) -> None:
        errors, _ = self.inspect_fixture({
            "../services/gale_mirror_service.py": "from src.services.ai_access_service import materialize_ai_safe_notes_for_target\n",
            "../gale_remote_mcp.py": "from src.services.ai_access_service import ai_create_note\n",
        })
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].path.name, "gale_remote_mcp.py")
        errors, _ = self.inspect_fixture({
            "../services/gale_mirror_service.py": "from src.services.ai_access_service import ai_create_note\n",
        })
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "dependency-direction")


if __name__ == "__main__":
    unittest.main()
