"""Step 4 regression against the frozen production Gale baseline."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


BASELINE_COMMIT = "46a02082f698f1989defa3f80530edf03120a6d1"


class Step04GaleProductionRegressionTests(unittest.TestCase):
    def test_current_gale_schema_and_payload_match_step04_baseline(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        runner = repository / "tests" / "gc35_step04_gale_fixture_runner.py"
        with tempfile.TemporaryDirectory(prefix="snotes-gc35-gale-regression-") as name:
            root = Path(name)
            baseline_repository = root / "baseline"
            baseline_repository.mkdir()
            archive = subprocess.run(
                ["git", "archive", "--format=tar", BASELINE_COMMIT],
                cwd=repository,
                check=True,
                stdout=subprocess.PIPE,
            ).stdout
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
                bundle.extractall(baseline_repository, filter="data")

            baseline = self._run_fixture(
                runner,
                source_root=baseline_repository,
                run_root=root / "baseline-run",
            )
            current = self._run_fixture(
                runner,
                source_root=repository,
                run_root=root / "current-run",
            )

        self.assertEqual(current["schema"], baseline["schema"])
        self.assertEqual(
            current["metadata_without_generated_at"],
            baseline["metadata_without_generated_at"],
        )
        self.assertEqual(current["payload"], baseline["payload"])
        self.assertEqual(current["result"], baseline["result"])
        self.assertEqual(current["verification"], baseline["verification"])

    def _run_fixture(
        self,
        runner: Path,
        *,
        source_root: Path,
        run_root: Path,
    ) -> dict[str, object]:
        run_root.mkdir()
        temporary_home = run_root / "home"
        temporary_home.mkdir()
        snapshot = run_root / "snapshot.json"
        environment = os.environ.copy()
        environment["HOME"] = str(temporary_home)
        environment["PYTHONPATH"] = str(source_root)
        subprocess.run(
            [
                str(Path(sys.executable).absolute()),
                "-B",
                str(runner),
                "--work-root",
                str(run_root / "fixture"),
                "--snapshot",
                str(snapshot),
            ],
            cwd=source_root,
            env=environment,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return json.loads(snapshot.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
