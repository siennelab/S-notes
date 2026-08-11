from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.app_paths import APP_NAME, DATA_ROOT_ENV_NAME, default_data_root
from src.snotes.bootstrap import resolve_rebuild_data_root
from src.snotes.gale_bootstrap import resolve_gale_data_root
from src.snotes.transport.mcp.server import build_server
from src.snotes.transport.mcp.runtime import (
    RuntimeConfigurationError,
    load_runtime_config,
)


ROOT = Path(__file__).resolve().parents[1]


class Package8ProductionBootstrapTest(unittest.TestCase):
    def test_gui_no_override_uses_canonical_home_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory) / "home"
            home.mkdir()
            with patch.dict(os.environ, {"HOME": str(home)}, clear=True):
                self.assertEqual(
                    resolve_rebuild_data_root(),
                    home / "Library" / "Application Support" / APP_NAME,
                )

    def test_gui_explicit_isolated_override_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "candidate"
            self.assertEqual(resolve_rebuild_data_root(root), root)
            self.assertTrue(root.is_dir())

    def test_gui_explicit_formal_override_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory) / "home"
            formal_root = default_data_root(home, environ={})
            with patch.dict(os.environ, {"HOME": str(home)}, clear=True):
                with self.assertRaisesRegex(
                    RuntimeError, "formal S-notes data root"
                ):
                    resolve_rebuild_data_root(formal_root)

    def test_mcp_no_override_uses_canonical_home_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory) / "home"
            home.mkdir()
            environment = {
                "HOME": str(home),
                "SNOTES_MCP_READ_ONLY": "1",
            }
            with patch.dict(os.environ, environment, clear=True):
                config = load_runtime_config()
            self.assertEqual(
                config.data_root,
                home / "Library" / "Application Support" / APP_NAME,
            )
            self.assertEqual(config.actor_target_id, "clawbot")
            self.assertTrue(config.read_only)
            server = build_server(config)
            self.assertEqual(
                [tool.name for tool in server._tool_manager.list_tools()],
                ["search_notes", "get_note"],
            )

    def test_mcp_explicit_isolated_override_builds_server(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory) / "home"
            isolated = Path(temporary_directory) / "candidate"
            environment = {
                "HOME": str(home),
                DATA_ROOT_ENV_NAME: str(isolated),
                "SNOTES_MCP_READ_ONLY": "1",
            }
            with patch.dict(os.environ, environment, clear=True):
                config = load_runtime_config()
                server = build_server(config)
            self.assertEqual(config.data_root, isolated)
            self.assertEqual(
                [tool.name for tool in server._tool_manager.list_tools()],
                ["search_notes", "get_note"],
            )

    def test_mcp_explicit_formal_override_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory) / "home"
            formal_root = default_data_root(home, environ={})
            environment = {
                "HOME": str(home),
                DATA_ROOT_ENV_NAME: str(formal_root),
                "SNOTES_MCP_READ_ONLY": "1",
            }
            with patch.dict(os.environ, environment, clear=True):
                with self.assertRaisesRegex(
                    RuntimeConfigurationError, "formal S-notes data root"
                ):
                    load_runtime_config()

    def test_gale_no_override_uses_existing_canonical_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory) / "home"
            formal_root = default_data_root(home, environ={})
            formal_root.mkdir(parents=True)
            (formal_root / "s_notes.db").touch()
            with patch.dict(os.environ, {"HOME": str(home)}, clear=True):
                self.assertEqual(resolve_gale_data_root(None), formal_root)

    def test_gale_explicit_formal_override_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory) / "home"
            formal_root = default_data_root(home, environ={})
            with patch.dict(os.environ, {"HOME": str(home)}, clear=True):
                with self.assertRaisesRegex(
                    RuntimeError, "formal S-notes data root"
                ):
                    resolve_gale_data_root(formal_root)

    def test_app_invalid_override_keeps_clean_exit_two(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory) / "home"
            blocker = Path(temporary_directory) / "not-a-directory"
            blocker.write_text("fictional blocker", encoding="utf-8")
            environment = os.environ.copy()
            environment.update(
                {
                    "HOME": str(home),
                    DATA_ROOT_ENV_NAME: str(blocker / "child"),
                    "QT_QPA_PLATFORM": "offscreen",
                    "PYTHONPYCACHEPREFIX": str(
                        Path(temporary_directory) / "pycache"
                    ),
                }
            )
            completed = subprocess.run(
                [sys.executable, "app.py"],
                cwd=ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("S-notes startup failed", completed.stderr)
            self.assertNotIn("Traceback", completed.stderr)

    def test_isolated_home_production_default_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory) / "home"
            home.mkdir()
            expected = home / "Library" / "Application Support" / APP_NAME
            environment = os.environ.copy()
            environment.pop(DATA_ROOT_ENV_NAME, None)
            environment.pop("SNOTES_MCP_DATA_ROOT", None)
            environment.pop("S_NOTES_CANDIDATE_LABEL", None)
            environment.update(
                {
                    "HOME": str(home),
                    "QT_QPA_PLATFORM": "offscreen",
                    "PYTHONPYCACHEPREFIX": str(
                        Path(temporary_directory) / "pycache"
                    ),
                }
            )
            script = """
from pathlib import Path
from PySide6.QtCore import QTimer
from src.snotes.bootstrap import build_application

application, window = build_application([])
expected = Path.home() / "Library" / "Application Support" / "S-notes"
assert (expected / "s_notes.db").is_file()
window.show()
QTimer.singleShot(0, application.quit)
raise SystemExit(application.exec())
"""
            completed = subprocess.run(
                [sys.executable, "-c", script],
                cwd=ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((expected / "s_notes.db").is_file())


if __name__ == "__main__":
    unittest.main()
