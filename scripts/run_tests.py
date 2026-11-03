#!/usr/bin/env python3
"""Run every test module in an isolated process and report one suite summary."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / "tests"
HOME_PREFIXES = {
    "test_markdown_body_size_ui.py": "snotes-body-size-ui-",
    "test_markdown_editor_search_ui.py": "snotes-search-ui-",
}
RAN_PATTERN = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
RESULT_DETAILS_PATTERN = re.compile(r"^(?:FAILED|OK) \(([^\n]+)\)", re.MULTILINE)
COUNT_PATTERN = re.compile(r"(failures|errors|skipped)=(\d+)")


def _module_result(output: str, returncode: int) -> tuple[int, int, int, int]:
    ran_matches = RAN_PATTERN.findall(output)
    if not ran_matches:
        # Keep aggregate counts internally consistent while making a process
        # crash, missing discovery, or malformed output visible as one
        # suite-level error.
        return 1, 0, 1, 0

    ran = int(ran_matches[-1])
    if ran == 0:
        return 1, 0, 1, 0
    failures = errors = skipped = 0
    result_details = RESULT_DETAILS_PATTERN.findall(output)
    if result_details:
        for name, value in COUNT_PATTERN.findall(result_details[-1]):
            if name == "failures":
                failures = int(value)
            elif name == "errors":
                errors = int(value)
            else:
                skipped = int(value)
    if returncode and not failures and not errors:
        errors = 1
    return ran, failures, errors, skipped


def _module_environment(home: Path) -> dict[str, str]:
    return dict(
        os.environ,
        HOME=str(home),
        S_NOTES_DATA_ROOT=str(home / "data"),
        PYTHONDONTWRITEBYTECODE="1",
        QT_QPA_PLATFORM="offscreen",
        QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu",
    )


def _module_command(test_file: Path) -> list[str]:
    return [
        os.path.abspath(sys.executable),
        "-B",
        "-m",
        "unittest",
        "discover",
        "-s",
        str(TEST_ROOT),
        "-p",
        test_file.name,
        "-v",
    ]


def main() -> int:
    test_files = sorted(TEST_ROOT.glob("test_*.py"))
    if not test_files:
        print("TEST_SUITE=FAIL reason=no_tests_found")
        return 1

    total = failures = errors = skipped = 0
    failed_modules: list[str] = []
    with tempfile.TemporaryDirectory(
        prefix="snotes-test-suite-", dir="/private/tmp"
    ) as suite_directory:
        suite_root = Path(suite_directory)
        for test_file in test_files:
            home = Path(
                tempfile.mkdtemp(
                    prefix=HOME_PREFIXES.get(test_file.name, "snotes-test-home-"),
                    dir=suite_root,
                )
            )
            environment = _module_environment(home)
            completed = subprocess.run(
                _module_command(test_file),
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            print(f"\n===== {test_file.name} =====")
            print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
            ran, module_failures, module_errors, module_skipped = _module_result(
                completed.stdout, completed.returncode
            )
            total += ran
            failures += module_failures
            errors += module_errors
            skipped += module_skipped
            if completed.returncode or module_failures or module_errors:
                failed_modules.append(test_file.stem)

    passed = total - failures - errors - skipped
    status = "PASS" if not failed_modules else "FAIL"
    print(
        f"TEST_SUITE={status} passed={passed} failed={failures} "
        f"errors={errors} skipped={skipped} total={total} "
        f"modules={len(test_files)}"
    )
    if failed_modules:
        print("FAILED_MODULES=" + ",".join(failed_modules))
    return 0 if not failed_modules else 1


if __name__ == "__main__":
    raise SystemExit(main())
