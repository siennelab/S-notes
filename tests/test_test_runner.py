from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import run_tests


class TestRunnerTests(unittest.TestCase):
    def test_parses_success_and_skips(self) -> None:
        self.assertEqual(
            run_tests._module_result(
                "Ran 4 tests in 0.1s\n\nOK (skipped=1)\n",
                0,
            ),
            (4, 0, 0, 1),
        )

    def test_parses_failures_and_errors(self) -> None:
        self.assertEqual(
            run_tests._module_result(
                "Ran 5 tests in 0.1s\n\n"
                "FAILED (failures=2, errors=1, skipped=1)\n",
                1,
            ),
            (5, 2, 1, 1),
        )

    def test_crash_missing_summary_and_zero_discovery_are_errors(self) -> None:
        self.assertEqual(
            run_tests._module_result("Ran 1 test in 0.1s\n\nOK\n", 134),
            (1, 0, 1, 0),
        )
        self.assertEqual(run_tests._module_result("", 0), (1, 0, 1, 0))
        self.assertEqual(
            run_tests._module_result("Ran 0 tests in 0.0s\n\nOK\n", 0),
            (1, 0, 1, 0),
        )

    def test_command_preserves_venv_path_and_environment_is_isolated(self) -> None:
        test_file = run_tests.TEST_ROOT / "test_fixture.py"
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = Path(temporary)
            venv_python = root / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True)
            venv_python.symlink_to(root / "base-python")
            resolved_python = venv_python.resolve()
            with patch.object(run_tests.sys, "executable", str(venv_python)):
                command = run_tests._module_command(test_file)

            first = root / "first-home"
            second = root / "second-home"
            first_environment = run_tests._module_environment(first)
            second_environment = run_tests._module_environment(second)

        self.assertEqual(command[0], os.path.abspath(venv_python))
        self.assertNotEqual(command[0], str(resolved_python))
        self.assertEqual(command[1:4], ["-B", "-m", "unittest"])
        self.assertEqual(command[-3:], ["-p", "test_fixture.py", "-v"])
        self.assertEqual(first_environment["HOME"], str(first))
        self.assertEqual(
            first_environment["S_NOTES_DATA_ROOT"],
            str(first / "data"),
        )
        self.assertNotEqual(first_environment["HOME"], second_environment["HOME"])
        self.assertEqual(first_environment["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(first_environment["QT_QPA_PLATFORM"], "offscreen")
        self.assertEqual(
            first_environment["QTWEBENGINE_CHROMIUM_FLAGS"],
            "--disable-gpu",
        )
        if "PATH" in os.environ:
            self.assertEqual(first_environment["PATH"], os.environ["PATH"])


if __name__ == "__main__":
    unittest.main()
