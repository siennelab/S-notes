#!/usr/bin/env python3
"""Selectively deploy the verified Step 7 Reminder repair to staging.

The local side derives the complete runtime source closure from the existing
packager, but sends source bytes for only the five authorized repair files.
The remote side rejects stale or mixed source trees before stopping services.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Callable


ROOT = (
    Path(__file__).resolve().parents[1]
    if "__file__" in globals()
    else Path("/srv/snotes/reminder-runtime")
)
BASELINE_REVISION = "0fd089d"
ENTRYPOINT = "src.reminder_cloud"
RUNTIME_ROOT = Path("/srv/snotes/reminder-runtime")
BACKUP_ROOT = Path("/root/greencloud-backups")
MANIFEST_NAME = "source-manifest.json"

PATCH_FILES = frozenset(
    {
        "src/snotes/domain/reminder_schedule.py",
        "src/snotes/domain/reminders.py",
        "src/snotes/domain/reminder_remote_contract.py",
        "src/snotes/application/reminder_service.py",
        "src/snotes/transport/reminder_http.py",
    }
)
EXPECTED_CLOSURE_SIZE = 25

API_UNIT = "snotes-reminder-api.service"
SCHEDULER_UNIT = "snotes-reminder-scheduler.service"
SCHEDULER_TIMER = "snotes-reminder-scheduler.timer"


class DeploymentError(RuntimeError):
    """A deployment gate failed or a started deployment was rolled back."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_bytes(revision: str, name: str, *, missing_ok: bool = False) -> bytes | None:
    result = subprocess.run(
        ["git", "show", f"{revision}:{name}"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode == 0:
        return result.stdout
    if missing_ok:
        return None
    raise DeploymentError(f"git_source_unavailable:{revision}:{name}")


def _source_files() -> list[str]:
    # Keep the dependency graph in one place. The fallback supports executing
    # this file directly, where sys.path starts at scripts/ rather than ROOT.
    try:
        from scripts.package_greencloud_reminder import source_closure
    except ModuleNotFoundError:
        from package_greencloud_reminder import source_closure

    return sorted(str(path.relative_to(ROOT)) for path in source_closure())


def build_payload(*, require_clean: bool = True) -> dict[str, object]:
    if require_clean:
        status = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=ROOT,
            text=True,
        )
        if status:
            raise DeploymentError("working_tree_not_clean")

    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    source_files = _source_files()
    if len(source_files) != EXPECTED_CLOSURE_SIZE:
        raise DeploymentError(f"unexpected_source_closure_size:{len(source_files)}")
    if not PATCH_FILES.issubset(source_files):
        raise DeploymentError("patch_file_missing_from_source_closure")

    expected_files: dict[str, str] = {}
    changed: set[str] = set()
    baseline: dict[str, bytes | None] = {}
    for name in source_files:
        current = (ROOT / name).read_bytes()
        expected_files[name] = _sha256(current)
        old = _git_bytes(BASELINE_REVISION, name, missing_ok=True)
        baseline[name] = old
        if old != current:
            changed.add(name)
    if changed != PATCH_FILES:
        unexpected = ",".join(sorted(changed ^ PATCH_FILES))
        raise DeploymentError(f"unexpected_baseline_delta:{unexpected}")

    patches: dict[str, dict[str, str | None]] = {}
    for name in sorted(PATCH_FILES):
        source = (ROOT / name).read_bytes()
        old = baseline[name]
        patches[name] = {
            "before": None if old is None else _sha256(old),
            "after": _sha256(source),
            "source": base64.b64encode(source).decode("ascii"),
        }
    return {
        "schema": 1,
        "source_revision": revision,
        "entrypoint": ENTRYPOINT,
        "expected_files": expected_files,
        "patches": patches,
    }


def _atomic_write(path: Path, data: bytes, mode: int = 0o644) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _properties(
    run: Callable[..., subprocess.CompletedProcess[str]], unit: str
) -> dict[str, str]:
    result = run(
        [
            "systemctl",
            "show",
            unit,
            "--property=User",
            "--property=WorkingDirectory",
            "--property=ExecStart",
            "--property=Type",
            "--property=ActiveState",
            "--no-pager",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return values


def _validate_unit_contract(
    run: Callable[..., subprocess.CompletedProcess[str]], runtime_root: Path
) -> None:
    api = _properties(run, API_UNIT)
    scheduler = _properties(run, SCHEDULER_UNIT)
    timer = _properties(run, SCHEDULER_TIMER)
    required_root = str(runtime_root)
    python = "/srv/snotes/runtime/.venv/bin/python"
    for name, values, command in (
        (API_UNIT, api, "serve"),
        (SCHEDULER_UNIT, scheduler, "schedule"),
    ):
        if values.get("User") != "snotes":
            raise DeploymentError(f"unit_user_mismatch:{name}")
        if values.get("WorkingDirectory") != required_root:
            raise DeploymentError(f"unit_working_directory_mismatch:{name}")
        exec_start = values.get("ExecStart", "")
        expected = (python, "-B", "-m", ENTRYPOINT, command)
        if not all(part in exec_start for part in expected):
            raise DeploymentError(f"unit_exec_start_mismatch:{name}")
    if api.get("ActiveState") != "active":
        raise DeploymentError("api_not_active_before_deploy")
    if scheduler.get("Type") != "oneshot" or scheduler.get("ActiveState") != "inactive":
        raise DeploymentError("scheduler_service_state_mismatch")
    if timer.get("ActiveState") != "active":
        raise DeploymentError("scheduler_timer_not_active_before_deploy")


def _decode_and_validate_payload(
    payload: dict[str, object], runtime_root: Path
) -> tuple[dict[str, bytes], dict[str, str], dict[str, object]]:
    if payload.get("schema") != 1 or payload.get("entrypoint") != ENTRYPOINT:
        raise DeploymentError("payload_header_invalid")
    revision = payload.get("source_revision")
    patches_value = payload.get("patches")
    expected_value = payload.get("expected_files")
    if not isinstance(revision, str) or len(revision) != 40:
        raise DeploymentError("source_revision_invalid")
    if not isinstance(patches_value, dict) or set(patches_value) != PATCH_FILES:
        raise DeploymentError("patch_allowlist_mismatch")
    if not isinstance(expected_value, dict) or len(expected_value) != EXPECTED_CLOSURE_SIZE:
        raise DeploymentError("expected_source_closure_invalid")
    if not PATCH_FILES.issubset(expected_value):
        raise DeploymentError("expected_source_closure_missing_patch")

    expected: dict[str, str] = {}
    for name, digest in expected_value.items():
        if (
            not isinstance(name, str)
            or not name.startswith("src/")
            or ".." in Path(name).parts
            or not isinstance(digest, str)
            or len(digest) != 64
        ):
            raise DeploymentError("expected_source_entry_invalid")
        expected[name] = digest

    decoded: dict[str, bytes] = {}
    patch_rows: dict[str, object] = patches_value
    for name in sorted(PATCH_FILES):
        row = patch_rows[name]
        if not isinstance(row, dict):
            raise DeploymentError(f"patch_entry_invalid:{name}")
        before = row.get("before")
        after = row.get("after")
        encoded = row.get("source")
        if before is not None and (not isinstance(before, str) or len(before) != 64):
            raise DeploymentError(f"patch_before_invalid:{name}")
        if not isinstance(after, str) or after != expected[name]:
            raise DeploymentError(f"patch_after_invalid:{name}")
        if not isinstance(encoded, str):
            raise DeploymentError(f"patch_source_invalid:{name}")
        try:
            source = base64.b64decode(encoded, validate=True)
            compile(source, name, "exec")
        except Exception as error:
            raise DeploymentError(f"patch_source_invalid:{name}") from error
        if _sha256(source) != after:
            raise DeploymentError(f"patch_source_hash_mismatch:{name}")
        decoded[name] = source

    # Every unchanged source must already equal HEAD. Existing patch files must
    # equal 0fd089d, while the one new contract must still be absent.
    for name, digest in expected.items():
        target = runtime_root / name
        if name in PATCH_FILES:
            row = patch_rows[name]
            assert isinstance(row, dict)
            before = row["before"]
            if before is None:
                if target.exists() or target.is_symlink():
                    raise DeploymentError(f"new_patch_file_already_exists:{name}")
            elif (
                not target.is_file()
                or target.is_symlink()
                or _sha256(target.read_bytes()) != before
            ):
                raise DeploymentError(f"staging_source_baseline_mismatch:{name}")
        elif (
            not target.is_file()
            or target.is_symlink()
            or _sha256(target.read_bytes()) != digest
        ):
            raise DeploymentError(f"staging_unchanged_source_mismatch:{name}")

    manifest_path = runtime_root / MANIFEST_NAME
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise DeploymentError("staging_manifest_invalid")
    try:
        old_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DeploymentError("staging_manifest_invalid") from error
    if (
        not isinstance(old_manifest, dict)
        or not isinstance(old_manifest.get("commit"), str)
        or old_manifest.get("entrypoint") != ENTRYPOINT
        or not isinstance(old_manifest.get("files"), dict)
    ):
        raise DeploymentError("staging_manifest_shape_invalid")
    return decoded, expected, old_manifest


def _new_manifest(
    payload: dict[str, object], expected: dict[str, str], old_manifest: dict[str, object]
) -> dict[str, object]:
    old_files = old_manifest["files"]
    assert isinstance(old_files, dict)
    deploy_assets = {
        name: digest
        for name, digest in sorted(old_files.items())
        if isinstance(name, str) and name.startswith("deploy/greencloud/reminder/")
    }
    return {
        "deployment_type": "selective-files",
        "source_revision": payload["source_revision"],
        "revision_scope": "runtime-source-closure",
        "entrypoint": ENTRYPOINT,
        "files": dict(sorted(expected.items())),
        "replaced_files": sorted(PATCH_FILES),
        "historical_package": {
            "commit": old_manifest["commit"],
            "deploy_asset_hashes": deploy_assets,
            "deploy_assets_present_in_runtime": False,
        },
    }


def apply_update(
    payload: dict[str, object],
    *,
    runtime_root: Path = RUNTIME_ROOT,
    backup_root: Path = BACKUP_ROOT,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, object]:
    decoded, expected, old_manifest = _decode_and_validate_payload(payload, runtime_root)
    _validate_unit_contract(run, runtime_root)

    stamp = now().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_root / f"step07-post-vibe-repair-{stamp}"
    backup.mkdir(parents=True, mode=0o700, exist_ok=False)
    manifest_path = runtime_root / MANIFEST_NAME
    patch_rows = payload["patches"]
    assert isinstance(patch_rows, dict)
    added_files = sorted(
        name
        for name in PATCH_FILES
        if isinstance(patch_rows[name], dict) and patch_rows[name]["before"] is None
    )
    existing_files = sorted(PATCH_FILES - set(added_files))
    for name in existing_files:
        saved = backup / name
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(runtime_root / name, saved)
    shutil.copy2(manifest_path, backup / MANIFEST_NAME)
    rollback_record = {
        "added_files": added_files,
        "backed_up_files": existing_files,
        "manifest": MANIFEST_NAME,
    }
    (backup / "rollback.json").write_text(
        json.dumps(rollback_record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    def systemctl(action: str, unit: str) -> None:
        run(["systemctl", action, unit], check=True, capture_output=True, text=True)

    try:
        systemctl("stop", SCHEDULER_TIMER)
        systemctl("stop", API_UNIT)
        systemctl("stop", SCHEDULER_UNIT)
        for name in sorted(PATCH_FILES):
            target = runtime_root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(target, decoded[name])

        actual = {
            name: _sha256((runtime_root / name).read_bytes()) for name in sorted(expected)
        }
        if actual != expected:
            raise DeploymentError("post_replace_source_closure_mismatch")
        manifest = _new_manifest(payload, actual, old_manifest)
        _atomic_write(
            manifest_path,
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
        systemctl("start", API_UNIT)
        if _properties(run, API_UNIT).get("ActiveState") != "active":
            raise DeploymentError("api_restart_failed")
        systemctl("start", SCHEDULER_TIMER)
        if _properties(run, SCHEDULER_TIMER).get("ActiveState") != "active":
            raise DeploymentError("scheduler_timer_restore_failed")
    except Exception as error:
        # The API may already have loaded the new modules. Stop it before
        # restoring source, even when the later timer restoration failed.
        try:
            for unit in (SCHEDULER_TIMER, API_UNIT, SCHEDULER_UNIT):
                systemctl("stop", unit)
        except Exception as stop_error:
            raise DeploymentError("patch_failed_rollback_services_not_stopped") from stop_error
        rollback_errors: list[str] = []
        for name in existing_files:
            try:
                _atomic_write(runtime_root / name, (backup / name).read_bytes())
            except Exception:
                rollback_errors.append(f"source:{name}")
        for name in added_files:
            try:
                (runtime_root / name).unlink(missing_ok=True)
            except Exception:
                rollback_errors.append(f"added:{name}")
        try:
            _atomic_write(manifest_path, (backup / MANIFEST_NAME).read_bytes())
        except Exception:
            rollback_errors.append("manifest")
        for unit in (API_UNIT, SCHEDULER_TIMER):
            try:
                systemctl("start", unit)
            except Exception:
                rollback_errors.append(f"unit:{unit}")
        suffix = "" if not rollback_errors else ":rollback_errors=" + ",".join(rollback_errors)
        raise DeploymentError(f"patch_failed_rolled_back:{error}{suffix}") from error

    return {
        "backup": str(backup),
        "source_revision": payload["source_revision"],
        "files": expected,
        "replaced_files": sorted(PATCH_FILES),
        "added_files": added_files,
        "scheduler_reload": "next-timer-pass",
        "unit_actions": {
            API_UNIT: ["stop", "start"],
            SCHEDULER_UNIT: ["stop"],
            SCHEDULER_TIMER: ["stop", "start"],
        },
    }


def remote_main() -> int:
    try:
        payload = json.load(sys.stdin)
        result = apply_update(payload)
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


def main() -> int:
    payload = build_payload(require_clean=True)
    source = Path(__file__).read_text(encoding="utf-8")
    command = (
        "/srv/snotes/runtime/.venv/bin/python -B -c "
        + shlex.quote(source)
        + " --remote-worker"
    )
    subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "optimal", command],
        input=json.dumps(payload).encode("utf-8"),
        check=True,
        timeout=90,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(remote_main() if sys.argv[1:] == ["--remote-worker"] else main())
