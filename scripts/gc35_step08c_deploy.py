#!/usr/bin/env python3
"""Prepare, activate, or roll back the GreenCloud Reminder production runtime.

The normal entry point runs from the repository and sends this worker plus the
packaged Reminder archive to the existing ``optimal`` host.  The remote worker
must run as root.  Scheduler activation is deliberately a separate command.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import BinaryIO


FORMAL_DB = Path("/srv/snotes/data/reminder-production.db")
PRODUCTION_BACKUPS = Path("/srv/snotes/backups/reminder-production")
RUNTIME = Path("/srv/snotes/reminder-runtime")
PYTHON = Path("/srv/snotes/runtime/.venv/bin/python")
PRODUCTION_CREDENTIAL = Path(
    "/root/greencloud-secrets/reminder-ntfy-production-token"
)
UNITS = (
    "snotes-reminder-api.service",
    "snotes-reminder-scheduler.service",
    "snotes-reminder-scheduler.timer",
    "snotes-reminder-backup.service",
    "snotes-reminder-backup.timer",
)
API_UNIT = UNITS[0]
SCHEDULER_SERVICE = UNITS[1]
SCHEDULER_TIMER = UNITS[2]
BACKUP_SERVICE = UNITS[3]
BACKUP_TIMER = UNITS[4]
WRAPPER = Path("/usr/local/sbin/snotes-reminder-backup-export")
DEPLOY_PREFIX = "deploy/greencloud/reminder/production/"
STATE_NAME = "step08c-state.json"


class DeploymentError(RuntimeError):
    pass


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=check, capture_output=True, text=True)


def _systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run("systemctl", *args, check=check)


def _status(kind: str, unit: str) -> str:
    result = _systemctl(f"is-{kind}", unit, check=False)
    return result.stdout.strip() or result.stderr.strip() or "unknown"


def _require_root() -> None:
    if os.geteuid() != 0:
        raise DeploymentError("root_required")


def _validate_rollback_dir(path: Path, *, fresh: bool) -> None:
    if not path.is_absolute() or not str(path).startswith(
        "/root/greencloud-backups/step08c-"
    ):
        raise DeploymentError("rollback_dir_must_be_explicit_step08c_path")
    if fresh and path.exists():
        raise DeploymentError("rollback_dir_must_be_fresh")
    if not fresh and (not path.is_dir() or path.is_symlink()):
        raise DeploymentError("rollback_dir_invalid")


def _state_path(rollback_dir: Path) -> Path:
    return rollback_dir / STATE_NAME


def _write_state(rollback_dir: Path, state: dict[str, object]) -> None:
    target = _state_path(rollback_dir)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)


def _read_state(rollback_dir: Path) -> dict[str, object]:
    _validate_rollback_dir(rollback_dir, fresh=False)
    target = _state_path(rollback_dir)
    if not target.is_file() or target.is_symlink():
        raise DeploymentError("step08c_state_missing")
    state = json.loads(target.read_text())
    if state.get("rollback_dir") != str(rollback_dir):
        raise DeploymentError("step08c_state_path_mismatch")
    return state


def _safe_regular(path: Path, *, mode: int | None = None) -> os.stat_result:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise DeploymentError(f"regular_file_required:{path}")
    if mode is not None and stat.S_IMODE(metadata.st_mode) != mode:
        raise DeploymentError(f"unsafe_file_mode:{path}")
    return metadata


def _sqlite_backup(source: Path, target: Path) -> None:
    _safe_regular(source, mode=0o600)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        raise DeploymentError(f"backup_target_exists:{target}")
    uri = f"file:{source}?mode=ro"
    with sqlite3.connect(uri, uri=True) as source_connection:
        source_connection.execute("PRAGMA query_only = ON")
        with sqlite3.connect(target) as target_connection:
            source_connection.backup(target_connection)
            result = target_connection.execute("PRAGMA integrity_check").fetchone()
    os.chmod(target, 0o600)
    if result != ("ok",):
        raise DeploymentError("formal_database_backup_integrity_failed")


def _archive_files(stream: BinaryIO) -> dict[str, bytes]:
    payload = stream.read()
    if not payload:
        raise DeploymentError("source_archive_required")
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        for member in archive.getmembers():
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise DeploymentError("unsafe_archive_path")
            if member.isdir():
                continue
            if not member.isfile():
                raise DeploymentError("archive_regular_files_only")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise DeploymentError("archive_member_unreadable")
            files[str(name)] = extracted.read()
    try:
        manifest = json.loads(files["source-manifest.json"])
        hashes = manifest["files"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise DeploymentError("source_manifest_invalid") from error
    if not isinstance(hashes, dict):
        raise DeploymentError("source_manifest_invalid")
    actual = {
        name: hashlib.sha256(data).hexdigest()
        for name, data in files.items()
        if name != "source-manifest.json"
    }
    if hashes != actual:
        raise DeploymentError("source_archive_manifest_mismatch")
    required = {DEPLOY_PREFIX + unit for unit in UNITS}
    required.add(DEPLOY_PREFIX + WRAPPER.name)
    if not required.issubset(files) or not any(name.startswith("src/") for name in files):
        raise DeploymentError("source_archive_incomplete")
    files["__archive__"] = payload
    return files


def _write_tree(root: Path, files: dict[str, bytes]) -> None:
    for name, data in files.items():
        if name == "source-manifest.json" or name.startswith("src/"):
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            os.chmod(target, 0o644)
    for directory, _, _ in os.walk(root):
        os.chmod(directory, 0o755)


def _save_existing(path: Path, destination: Path) -> dict[str, object]:
    if not path.exists() and not path.is_symlink():
        return {"exists": False}
    metadata = _safe_regular(path)
    shutil.copy2(path, destination)
    return {
        "exists": True,
        "mode": stat.S_IMODE(metadata.st_mode),
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
    }


def _install_bytes(target: Path, data: bytes, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        os.chmod(temporary_name, mode)
        os.chown(temporary_name, 0, 0)
        os.replace(temporary_name, target)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def prepare(rollback_dir: Path, archive_stream: BinaryIO) -> dict[str, object]:
    _require_root()
    _validate_rollback_dir(rollback_dir, fresh=True)
    credential = _safe_regular(PRODUCTION_CREDENTIAL, mode=0o600)
    if (credential.st_uid, credential.st_gid, credential.st_size > 0) != (0, 0, True):
        raise DeploymentError("production_ntfy_credential_invalid")
    database = _safe_regular(FORMAL_DB, mode=0o600)
    if database.st_uid != 2006 or database.st_gid != 2006:
        raise DeploymentError("formal_database_owner_invalid")
    if not PYTHON.is_file() or not RUNTIME.is_dir() or RUNTIME.is_symlink():
        raise DeploymentError("existing_runtime_invalid")

    files = _archive_files(archive_stream)
    prepared = Path("/srv/snotes") / (
        "reminder-runtime.step08c-prepared-" + rollback_dir.name.removeprefix("step08c-")
    )
    if prepared.exists() or prepared.is_symlink():
        raise DeploymentError("prepared_runtime_already_exists")

    rollback_dir.mkdir(mode=0o700)
    (rollback_dir / "config.before").mkdir(mode=0o700)
    (rollback_dir / "config.production").mkdir(mode=0o700)
    try:
        (rollback_dir / "source-package.tar.gz").write_bytes(files.pop("__archive__"))
        os.chmod(rollback_dir / "source-package.tar.gz", 0o600)
        shutil.copytree(RUNTIME, rollback_dir / "runtime.before", symlinks=True)
        for path in (rollback_dir / "runtime.before").rglob("*"):
            if path.is_symlink():
                raise DeploymentError("existing_runtime_symlink_forbidden")
        before: dict[str, object] = {}
        for unit in UNITS:
            target = Path("/etc/systemd/system") / unit
            before[unit] = _save_existing(target, rollback_dir / "config.before" / unit)
            production = files[DEPLOY_PREFIX + unit]
            (rollback_dir / "config.production" / unit).write_bytes(production)
        before[str(WRAPPER)] = _save_existing(
            WRAPPER, rollback_dir / "config.before" / WRAPPER.name
        )
        (rollback_dir / "config.production" / WRAPPER.name).write_bytes(
            files[DEPLOY_PREFIX + WRAPPER.name]
        )
        services = {
            unit: {"active": _status("active", unit), "enabled": _status("enabled", unit)}
            for unit in UNITS
        }
        _sqlite_backup(FORMAL_DB, rollback_dir / "formal-db.before.db")
        prepared.mkdir(mode=0o755)
        _write_tree(prepared, files)
        state: dict[str, object] = {
            "phase": "prepared",
            "rollback_dir": str(rollback_dir),
            "prepared_runtime": str(prepared),
            "prior_files": before,
            "prior_services": services,
            "old_runtime_moved": False,
            "runtime_swapped": False,
            "production_config_installed": False,
        }
        _write_state(rollback_dir, state)
    except Exception:
        if not _state_path(rollback_dir).exists():
            shutil.rmtree(prepared, ignore_errors=True)
        raise
    return {"phase": "prepared", "rollback_dir": str(rollback_dir)}


def _assert_inactive(*units: str) -> None:
    active = [unit for unit in units if _status("active", unit) not in {"inactive", "failed"}]
    if active:
        raise DeploymentError("units_not_inactive:" + ",".join(active))


def _restore_file(target: Path, saved: Path, metadata: dict[str, object]) -> None:
    if metadata.get("exists") is True:
        data = saved.read_bytes()
        _install_bytes(target, data, int(metadata["mode"]))
        os.chown(target, int(metadata["uid"]), int(metadata["gid"]))
    elif target.exists() or target.is_symlink():
        target.unlink()


def _restore_unit_states(state: dict[str, object]) -> None:
    services = state["prior_services"]
    assert isinstance(services, dict)
    for unit in UNITS:
        row = services[unit]
        assert isinstance(row, dict)
        enabled = row["enabled"]
        if enabled == "enabled":
            _systemctl("enable", unit)
        elif enabled == "disabled":
            _systemctl("disable", unit)
    for unit in (API_UNIT, SCHEDULER_TIMER, BACKUP_TIMER, SCHEDULER_SERVICE, BACKUP_SERVICE):
        row = services[unit]
        assert isinstance(row, dict)
        if row["active"] == "active":
            _systemctl("start", unit)


def rollback(rollback_dir: Path) -> dict[str, object]:
    _require_root()
    state = _read_state(rollback_dir)
    if state.get("phase") == "rolled-back":
        return {"phase": "rolled-back", "rollback_dir": str(rollback_dir)}
    for unit in (SCHEDULER_TIMER, SCHEDULER_SERVICE, BACKUP_TIMER, BACKUP_SERVICE, API_UNIT):
        _systemctl("stop", unit, check=False)
    _systemctl("disable", SCHEDULER_TIMER, check=False)
    _systemctl("disable", BACKUP_TIMER, check=False)
    _assert_inactive(SCHEDULER_TIMER, SCHEDULER_SERVICE, BACKUP_TIMER, BACKUP_SERVICE, API_UNIT)

    preserved = rollback_dir / "formal-db.at-rollback.db"
    if not preserved.exists():
        _sqlite_backup(FORMAL_DB, preserved)
    staging_retired = rollback_dir / "runtime.staging-retired"
    runtime_was_moved = (
        state.get("runtime_swapped") is True
        or state.get("old_runtime_moved") is True
        or (not RUNTIME.exists() and staging_retired.is_dir())
    )
    if runtime_was_moved:
        retired = rollback_dir / "runtime.production-retired"
        if RUNTIME.exists() or RUNTIME.is_symlink():
            if retired.exists() or retired.is_symlink():
                raise DeploymentError("retired_runtime_already_exists")
            os.replace(RUNTIME, retired)
        shutil.copytree(rollback_dir / "runtime.before", RUNTIME, symlinks=False)
    else:
        prepared = Path(str(state["prepared_runtime"]))
        if prepared.exists():
            shutil.rmtree(prepared)

    before = state["prior_files"]
    assert isinstance(before, dict)
    for unit in UNITS:
        target = Path("/etc/systemd/system") / unit
        metadata = before[unit]
        assert isinstance(metadata, dict)
        _restore_file(target, rollback_dir / "config.before" / unit, metadata)
    wrapper_metadata = before[str(WRAPPER)]
    assert isinstance(wrapper_metadata, dict)
    _restore_file(
        WRAPPER, rollback_dir / "config.before" / WRAPPER.name, wrapper_metadata
    )
    _systemctl("daemon-reload")
    _restore_unit_states(state)
    state["phase"] = "rolled-back"
    _write_state(rollback_dir, state)
    return {
        "phase": "rolled-back",
        "rollback_dir": str(rollback_dir),
        "formal_data_restored": False,
        "ntfy_auth_restored": False,
    }


def cutover(rollback_dir: Path) -> dict[str, object]:
    _require_root()
    state = _read_state(rollback_dir)
    if state.get("phase") != "prepared":
        raise DeploymentError("cutover_requires_prepared_state")
    prepared = Path(str(state["prepared_runtime"]))
    if not prepared.is_dir() or prepared.is_symlink():
        raise DeploymentError("prepared_runtime_missing")
    try:
        # Stop the staging writer before changing shared runtime or unit files.
        _systemctl("disable", "--now", SCHEDULER_TIMER)
        _systemctl("stop", SCHEDULER_SERVICE)
        _assert_inactive(SCHEDULER_TIMER, SCHEDULER_SERVICE)
        if _status("enabled", SCHEDULER_TIMER) != "disabled":
            raise DeploymentError("staging_scheduler_timer_not_disabled")
        for unit in (API_UNIT, BACKUP_TIMER, BACKUP_SERVICE):
            _systemctl("stop", unit)
        _assert_inactive(API_UNIT, BACKUP_TIMER, BACKUP_SERVICE)

        retired = rollback_dir / "runtime.staging-retired"
        if retired.exists() or retired.is_symlink():
            raise DeploymentError("staging_runtime_retirement_target_exists")
        os.replace(RUNTIME, retired)
        state["old_runtime_moved"] = True
        _write_state(rollback_dir, state)
        os.replace(prepared, RUNTIME)
        state["runtime_swapped"] = True
        _write_state(rollback_dir, state)

        production = rollback_dir / "config.production"
        for unit in UNITS:
            _install_bytes(
                Path("/etc/systemd/system") / unit,
                (production / unit).read_bytes(),
                0o644,
            )
        _install_bytes(WRAPPER, (production / WRAPPER.name).read_bytes(), 0o755)
        state["production_config_installed"] = True
        _write_state(rollback_dir, state)

        PRODUCTION_BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chown(PRODUCTION_BACKUPS, 2006, 2006)
        os.chmod(PRODUCTION_BACKUPS, 0o700)
        _systemctl("daemon-reload")
        _systemctl("enable", "--now", API_UNIT)
        _systemctl("start", BACKUP_SERVICE)
        _systemctl("enable", "--now", BACKUP_TIMER)
        _systemctl("disable", SCHEDULER_TIMER, check=False)
        _assert_inactive(SCHEDULER_TIMER, SCHEDULER_SERVICE)
        if _status("active", API_UNIT) != "active" or _status("active", BACKUP_TIMER) != "active":
            raise DeploymentError("production_api_or_backup_not_active")
        latest = PRODUCTION_BACKUPS / "reminder-latest.db"
        _safe_regular(latest, mode=0o600)
        with sqlite3.connect(f"file:{latest}?mode=ro", uri=True) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise DeploymentError("production_latest_backup_integrity_failed")
        state["phase"] = "cutover"
        _write_state(rollback_dir, state)
    except Exception as error:
        try:
            rollback(rollback_dir)
        except Exception as rollback_error:
            raise DeploymentError(
                f"cutover_failed_and_rollback_failed:{error}:{rollback_error}"
            ) from error
        raise DeploymentError(f"cutover_failed_rolled_back:{error}") from error
    return {
        "phase": "cutover",
        "scheduler_enabled": False,
        "rollback_dir": str(rollback_dir),
    }


def enable_scheduler(rollback_dir: Path, *, smoke_confirmed: bool) -> dict[str, object]:
    _require_root()
    if not smoke_confirmed:
        raise DeploymentError("no_notification_smoke_confirmation_required")
    state = _read_state(rollback_dir)
    if state.get("phase") != "cutover":
        raise DeploymentError("scheduler_enable_requires_cutover_state")
    if _status("active", API_UNIT) != "active":
        raise DeploymentError("production_api_not_active")
    _systemctl("enable", "--now", SCHEDULER_TIMER)
    if _status("active", SCHEDULER_TIMER) != "active":
        raise DeploymentError("production_scheduler_timer_not_active")
    state["phase"] = "scheduler-enabled"
    _write_state(rollback_dir, state)
    return {"phase": "scheduler-enabled", "rollback_dir": str(rollback_dir)}


def _remote_main(args: argparse.Namespace) -> int:
    rollback_dir = Path(args.rollback_dir)
    if args.phase == "prepare":
        result = prepare(rollback_dir, sys.stdin.buffer)
    elif args.phase == "cutover":
        result = cutover(rollback_dir)
    elif args.phase == "enable-scheduler":
        result = enable_scheduler(
            rollback_dir, smoke_confirmed=args.confirm_no_notification_smoke
        )
    else:
        result = rollback(rollback_dir)
    print(json.dumps(result, sort_keys=True))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase", choices=("prepare", "cutover", "enable-scheduler", "rollback")
    )
    parser.add_argument("--rollback-dir", required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--host", default="optimal")
    parser.add_argument("--confirm-no-notification-smoke", action="store_true")
    parser.add_argument("--remote-worker", action="store_true", help=argparse.SUPPRESS)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.remote_worker:
        return _remote_main(args)
    if args.phase == "prepare":
        if args.archive is None or not args.archive.is_file():
            raise SystemExit("prepare requires --archive FILE")
        input_bytes = args.archive.read_bytes()
    else:
        if args.archive is not None:
            raise SystemExit("--archive is valid only for prepare")
        input_bytes = b""
    remote_args = [
        args.phase,
        "--rollback-dir",
        args.rollback_dir,
        "--remote-worker",
    ]
    if args.confirm_no_notification_smoke:
        remote_args.append("--confirm-no-notification-smoke")
    command = "python3 -B -c " + shlex.quote(Path(__file__).read_text())
    command += " " + " ".join(shlex.quote(value) for value in remote_args)
    subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", args.host, command],
        input=input_bytes,
        check=True,
        timeout=180,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
