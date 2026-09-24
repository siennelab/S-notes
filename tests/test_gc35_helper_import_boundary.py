"""Headless receipt imports and helper version startup must not load GUI code."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class HelperImportBoundaryTests(unittest.TestCase):
    def test_receipt_import_and_helper_version_without_gui_modules(self):
        with tempfile.TemporaryDirectory(prefix="snotes-import-boundary-") as root:
            environment = dict(os.environ, HOME=root, S_NOTES_DATA_ROOT=root)
            result = subprocess.run(
                [sys.executable, "-c", '''
import importlib.abc
import sys
class NoGui(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {
            "src.snotes.ui.markdown_canonicalizer",
            "src.snotes.ui.markdown_editor",
            "PySide6.QtWidgets",
            "PySide6.QtWebEngineWidgets",
        }:
            raise AssertionError("Unexpected GUI import: " + fullname)
sys.meta_path.insert(0, NoGui())
import src.snotes.application.ai_receipt_inbox_service
import gale_production_pull_helper
assert gale_production_pull_helper.main(["--version"]) == 0
'''], cwd=Path(__file__).resolve().parents[1], env=environment,
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("snotes-gale-production-pull 1", result.stdout)
