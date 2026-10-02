from __future__ import annotations

from datetime import datetime, timezone
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import gc35_step07_update_staging as deploy


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeSystemctl:
    def __init__(self, runtime_root: Path, *, fail_api_start_once: bool = False,
                 fail_timer_start_once: bool = False):
        self.runtime_root = runtime_root
        self.states = {
            deploy.API_UNIT: "active",
            deploy.SCHEDULER_UNIT: "inactive",
            deploy.SCHEDULER_TIMER: "active",
        }
        self.calls: list[tuple[str, ...]] = []
        self.fail_api_start_once = fail_api_start_once
        self.fail_timer_start_once = fail_timer_start_once

    def __call__(self, command, **kwargs):
        self.calls.append(tuple(command))
        action, unit = command[1], command[2]
        if action == "show":
            verb = "serve" if unit == deploy.API_UNIT else "schedule"
            values = {
                "User": "snotes" if unit != deploy.SCHEDULER_TIMER else "",
                "WorkingDirectory": str(self.runtime_root) if unit != deploy.SCHEDULER_TIMER else "",
                "ExecStart": (
                    f"/srv/snotes/runtime/.venv/bin/python -B -m {deploy.ENTRYPOINT} {verb}"
                    if unit != deploy.SCHEDULER_TIMER
                    else ""
                ),
                "Type": "oneshot" if unit == deploy.SCHEDULER_UNIT else "simple",
                "ActiveState": self.states[unit],
            }
            stdout = "".join(f"{key}={value}\n" for key, value in values.items())
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")
        if action == "start" and unit == deploy.API_UNIT and self.fail_api_start_once:
            self.fail_api_start_once = False
            raise subprocess.CalledProcessError(1, command)
        if action == "start" and unit == deploy.SCHEDULER_TIMER and self.fail_timer_start_once:
            self.fail_timer_start_once = False
            raise subprocess.CalledProcessError(1, command)
        if action == "stop":
            self.states[unit] = "inactive"
        elif action == "start":
            self.states[unit] = "active"
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def staging_fixture(root: Path) -> tuple[dict[str, object], dict[str, bytes], bytes]:
    old_sources: dict[str, bytes] = {}
    patches: dict[str, dict[str, str | None]] = {}
    expected: dict[str, str] = {}
    contract = "src/snotes/domain/reminder_remote_contract.py"
    for index, name in enumerate(sorted(deploy.PATCH_FILES)):
        old = f"VALUE = 'old-{index}'\n".encode()
        new = f"VALUE = 'new-{index}'\n".encode()
        if name != contract:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(old)
            old_sources[name] = old
        patches[name] = {
            "before": None if name == contract else digest(old),
            "after": digest(new),
            "source": base64.b64encode(new).decode("ascii"),
        }
        expected[name] = digest(new)
    for index in range(deploy.EXPECTED_CLOSURE_SIZE - len(deploy.PATCH_FILES)):
        name = f"src/snotes/fixture/dependency_{index:02d}.py"
        source = f"VALUE = {index}\n".encode()
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source)
        expected[name] = digest(source)
    old_manifest = {
        "commit": "b" * 40,
        "entrypoint": deploy.ENTRYPOINT,
        "files": {
            "src/reminder_cloud.py": "1" * 64,
            "deploy/greencloud/reminder/install-first.sh": "2" * 64,
            "deploy/greencloud/reminder/snotes-reminder-api.service": "3" * 64,
        },
    }
    manifest_bytes = (json.dumps(old_manifest, sort_keys=True) + "\n").encode()
    (root / deploy.MANIFEST_NAME).write_bytes(manifest_bytes)
    payload: dict[str, object] = {
        "schema": 1,
        "source_revision": "7" * 40,
        "entrypoint": deploy.ENTRYPOINT,
        "expected_files": expected,
        "patches": patches,
    }
    return payload, old_sources, manifest_bytes


class Step07SelectiveDeploymentTests(unittest.TestCase):
    def test_remote_bootstrap_does_not_require_local_file_path(self):
        namespace = {"__name__": "remote_bootstrap_test"}
        source = Path(deploy.__file__).read_text()
        exec(compile(source, "<remote-worker>", "exec"), namespace)
        self.assertEqual(namespace["ROOT"], deploy.RUNTIME_ROOT)

    def test_payload_uses_packager_closure_and_sends_only_fixed_allowlist(self):
        payload = deploy.build_payload(require_clean=False)
        self.assertEqual(set(payload["patches"]), deploy.PATCH_FILES)
        self.assertEqual(len(payload["expected_files"]), 25)
        self.assertEqual(set(deploy._source_files()), set(payload["expected_files"]))
        contract = payload["patches"]["src/snotes/domain/reminder_remote_contract.py"]
        self.assertIsNone(contract["before"])
        self.assertEqual(payload["source_revision"], subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=deploy.ROOT, text=True
        ).strip())

    def test_success_writes_actual_closure_manifest_and_historical_assets(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            runtime, backups = base / "runtime", base / "backups"
            runtime.mkdir()
            payload, _, _ = staging_fixture(runtime)
            systemctl = FakeSystemctl(runtime)
            outcome = deploy.apply_update(
                payload,
                runtime_root=runtime,
                backup_root=backups,
                run=systemctl,
                now=lambda: datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
            )

            manifest = json.loads((runtime / deploy.MANIFEST_NAME).read_text())
            actual = {
                name: digest((runtime / name).read_bytes())
                for name in payload["expected_files"]
            }
            self.assertEqual(manifest["deployment_type"], "selective-files")
            self.assertEqual(manifest["source_revision"], "7" * 40)
            self.assertEqual(manifest["revision_scope"], "runtime-source-closure")
            self.assertEqual(manifest["files"], actual)
            self.assertEqual(manifest["replaced_files"], sorted(deploy.PATCH_FILES))
            history = manifest["historical_package"]
            self.assertEqual(history["commit"], "b" * 40)
            self.assertFalse(history["deploy_assets_present_in_runtime"])
            self.assertEqual(len(history["deploy_asset_hashes"]), 2)
            backup = Path(outcome["backup"])
            rollback = json.loads((backup / "rollback.json").read_text())
            self.assertEqual(rollback["added_files"], ["src/snotes/domain/reminder_remote_contract.py"])
            self.assertFalse((backup / "src/snotes/domain/reminder_remote_contract.py").exists())
            self.assertEqual(outcome["scheduler_reload"], "next-timer-pass")
            self.assertNotIn(("systemctl", "start", deploy.SCHEDULER_UNIT), systemctl.calls)

    def test_stale_unchanged_dependency_rejects_before_backup_or_systemctl(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            runtime, backups = base / "runtime", base / "backups"
            runtime.mkdir()
            payload, old_sources, manifest = staging_fixture(runtime)
            unchanged = next(name for name in payload["expected_files"] if name not in deploy.PATCH_FILES)
            (runtime / unchanged).write_bytes(b"stale\n")
            systemctl = FakeSystemctl(runtime)
            with self.assertRaisesRegex(deploy.DeploymentError, "staging_unchanged_source_mismatch"):
                deploy.apply_update(payload, runtime_root=runtime, backup_root=backups, run=systemctl)
            self.assertFalse(backups.exists())
            self.assertEqual(systemctl.calls, [])
            self.assertEqual((runtime / deploy.MANIFEST_NAME).read_bytes(), manifest)
            for name, source in old_sources.items():
                self.assertEqual((runtime / name).read_bytes(), source)

    def test_failure_restores_sources_manifest_and_removes_added_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            runtime, backups = base / "runtime", base / "backups"
            runtime.mkdir()
            payload, old_sources, manifest = staging_fixture(runtime)
            systemctl = FakeSystemctl(runtime, fail_api_start_once=True)
            with self.assertRaisesRegex(deploy.DeploymentError, "patch_failed_rolled_back"):
                deploy.apply_update(
                    payload,
                    runtime_root=runtime,
                    backup_root=backups,
                    run=systemctl,
                    now=lambda: datetime(2026, 10, 1, 12, 1, tzinfo=timezone.utc),
                )
            for name, source in old_sources.items():
                self.assertEqual((runtime / name).read_bytes(), source)
            self.assertFalse((runtime / "src/snotes/domain/reminder_remote_contract.py").exists())
            self.assertEqual((runtime / deploy.MANIFEST_NAME).read_bytes(), manifest)
            self.assertEqual(systemctl.states[deploy.API_UNIT], "active")
            self.assertEqual(systemctl.states[deploy.SCHEDULER_TIMER], "active")

    def test_late_failure_stops_loaded_api_before_restoring_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            runtime = base / "runtime"
            runtime.mkdir()
            payload, old_sources, manifest = staging_fixture(runtime)
            systemctl = FakeSystemctl(runtime, fail_timer_start_once=True)
            original_write = deploy._atomic_write
            restored = []

            def guarded_write(path, data, mode=0o644):
                name = str(path.relative_to(runtime))
                if old_sources.get(name) == data or (name == deploy.MANIFEST_NAME and data == manifest):
                    self.assertEqual(systemctl.states[deploy.API_UNIT], "inactive")
                    self.assertEqual(systemctl.states[deploy.SCHEDULER_TIMER], "inactive")
                    restored.append(name)
                original_write(path, data, mode)

            with patch.object(deploy, "_atomic_write", side_effect=guarded_write):
                with self.assertRaisesRegex(deploy.DeploymentError, "patch_failed_rolled_back"):
                    deploy.apply_update(payload, runtime_root=runtime,
                                        backup_root=base / "backups", run=systemctl)
            self.assertEqual(set(restored), set(old_sources) | {deploy.MANIFEST_NAME})
            self.assertFalse((runtime / "src/snotes/domain/reminder_remote_contract.py").exists())
            self.assertEqual(systemctl.states[deploy.API_UNIT], "active")
            self.assertEqual(systemctl.states[deploy.SCHEDULER_TIMER], "active")


if __name__ == "__main__":
    unittest.main()
