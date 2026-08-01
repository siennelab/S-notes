from __future__ import annotations

import fcntl
import hashlib
import json
import os
import plistlib
import pwd
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from src.gale_submission_pull import (
    GaleSubmissionPullError,
    PullResult,
    SystemSshSubmissionTransport,
    pull_production_submissions,
)


HELPER_VERSION = "1"
HELPER_FILENAME = "snotes-gale-production-pull"
LAUNCH_AGENT_LABEL = "com.snotes.gale-production-pull"
LAUNCH_AGENT_INTERVAL_SECONDS = 300
AUTO_PULL_SETTING = "gale/auto_pull_enabled"
STATE_SCHEMA_VERSION = 1
LOG_MAX_BYTES = 256 * 1024
HELPER_VERSION_TIMEOUT_SECONDS = 30
SAFE_ERROR_CODE = re.compile(r"^[a-z0-9_]{1,80}$")
ARM64_CPU_TYPE = 0x0100000C
MACHO_64_MAGIC = 0xFEEDFACF


class BackgroundPullError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class BackgroundPaths:
    home: Path
    data_root: Path
    bin_dir: Path
    helper: Path
    runtime_dir: Path
    lock: Path
    install_lock: Path
    state_dir: Path
    state: Path
    log_dir: Path
    log: Path
    launch_agents_dir: Path
    plist: Path


@dataclass(frozen=True)
class InstallResult:
    helper_updated: bool
    plist_updated: bool
    launch_agent_loaded: bool
    helper_sha256: str
    helper_version: str


@dataclass(frozen=True)
class AutoPullOutcome:
    status: str
    result: PullResult | None = None
    error_code: str = ""


def background_paths(home: Path | None = None) -> BackgroundPaths:
    account_home = (
        Path(pwd.getpwuid(os.geteuid()).pw_dir)
        if home is None
        else Path(home)
    )
    data_root = account_home / "Library" / "Application Support" / "S-notes"
    bin_dir = data_root / "bin"
    runtime_dir = data_root / "runtime"
    state_dir = data_root / "state"
    log_dir = account_home / "Library" / "Logs" / "S-notes"
    launch_agents_dir = account_home / "Library" / "LaunchAgents"
    return BackgroundPaths(
        home=account_home,
        data_root=data_root,
        bin_dir=bin_dir,
        helper=bin_dir / HELPER_FILENAME,
        runtime_dir=runtime_dir,
        lock=runtime_dir / "gale-production-pull.lock",
        install_lock=runtime_dir / "gale-production-pull-install.lock",
        state_dir=state_dir,
        state=state_dir / "gale-production-pull.json",
        log_dir=log_dir,
        log=log_dir / "gale-production-pull.log",
        launch_agents_dir=launch_agents_dir,
        plist=launch_agents_dir / f"{LAUNCH_AGENT_LABEL}.plist",
    )


def auto_pull_enabled(settings=None) -> bool:
    if settings is None:
        from PySide6.QtCore import QSettings

        settings = QSettings("S-notes", "S-notes")
    return bool(settings.value(AUTO_PULL_SETTING, True, type=bool))


def bundled_helper_source() -> Path:
    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        candidate = executable.parent.parent / "Resources" / "bin" / HELPER_FILENAME
        if candidate.is_file():
            return candidate
        bundle_root = Path(getattr(sys, "_MEIPASS", ""))
        candidate = bundle_root / "bin" / HELPER_FILENAME
        if candidate.is_file():
            return candidate
    raise BackgroundPullError("bundled_helper_unavailable")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_arm64_macho(path: Path) -> None:
    try:
        with Path(path).open("rb") as handle:
            header = handle.read(12)
        if len(header) != 12:
            raise ValueError("short header")
        magic, cpu_type, _cpu_subtype = struct.unpack("<Iii", header)
    except (OSError, ValueError, struct.error) as error:
        raise BackgroundPullError("helper_architecture_invalid") from error
    if magic != MACHO_64_MAGIC or cpu_type != ARM64_CPU_TYPE:
        raise BackgroundPullError("helper_architecture_invalid")


def read_helper_version(path: Path) -> str:
    try:
        completed = subprocess.run(
            [str(Path(path)), "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=HELPER_VERSION_TIMEOUT_SECONDS,
            cwd="/",
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BackgroundPullError("helper_version_unavailable") from error
    expected = f"{HELPER_FILENAME} {HELPER_VERSION}"
    try:
        output = completed.stdout.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise BackgroundPullError("helper_version_invalid") from error
    if completed.returncode != 0 or output != expected:
        raise BackgroundPullError("helper_version_invalid")
    return HELPER_VERSION


def _ensure_private_directory(path: Path) -> None:
    target = Path(path)
    if target.is_symlink():
        raise BackgroundPullError("background_path_symlink")
    try:
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not target.is_dir():
            raise OSError("not a directory")
        os.chmod(target, 0o700)
    except OSError as error:
        raise BackgroundPullError("background_directory_unavailable") from error


class ProductionPullLock:
    def __init__(self, path: Path, *, busy_code: str = "pull_already_running") -> None:
        self.path = Path(path)
        self.busy_code = busy_code
        self._descriptor: int | None = None

    def __enter__(self) -> "ProductionPullLock":
        _ensure_private_directory(self.path.parent)
        if self.path.is_symlink():
            raise BackgroundPullError("pull_lock_symlink")
        try:
            descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            if "descriptor" in locals():
                os.close(descriptor)
            raise BackgroundPullError(self.busy_code) from error
        except OSError as error:
            if "descriptor" in locals():
                os.close(descriptor)
            raise BackgroundPullError("pull_lock_unavailable") from error
        self._descriptor = descriptor
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        descriptor = self._descriptor
        self._descriptor = None
        if descriptor is None:
            return
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _safe_error_code(value: str) -> str:
    code = str(value or "unexpected_failure")
    return code if SAFE_ERROR_CODE.fullmatch(code) else "unexpected_failure"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_background_state(path: Path | None = None) -> dict:
    state_path = background_paths().state if path is None else Path(path)
    defaults = {
        "schema_version": STATE_SCHEMA_VERSION,
        "helper_version": HELPER_VERSION,
        "last_check_at": "",
        "last_success_at": "",
        "last_received_count": 0,
        "last_error_code": "",
        "background_version": HELPER_VERSION,
    }
    try:
        value = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return defaults
    if not isinstance(value, dict):
        return defaults
    result = dict(defaults)
    for key in ("last_check_at", "last_success_at"):
        candidate = value.get(key)
        if isinstance(candidate, str) and len(candidate) <= 40:
            result[key] = candidate
    count = value.get("last_received_count")
    if isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= 1000:
        result["last_received_count"] = count
    error_code = value.get("last_error_code")
    if isinstance(error_code, str):
        result["last_error_code"] = (
            _safe_error_code(error_code) if error_code else ""
        )
    for key in ("helper_version", "background_version"):
        if value.get(key) == HELPER_VERSION:
            result[key] = HELPER_VERSION
    return result


def _atomic_write_bytes(
    destination: Path,
    payload: bytes,
    *,
    mode: int,
    replace: Callable[[str, str], None] = os.replace,
) -> None:
    target = Path(destination)
    _ensure_private_directory(target.parent)
    if target.is_symlink():
        raise BackgroundPullError("background_path_symlink")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        replace(str(temporary), str(target))
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def write_background_state(path: Path, state: dict) -> None:
    safe_state = load_background_state(Path("/nonexistent"))
    safe_state.update(
        {
            "last_check_at": str(state.get("last_check_at", ""))[:40],
            "last_success_at": str(state.get("last_success_at", ""))[:40],
            "last_received_count": max(
                0, min(int(state.get("last_received_count", 0)), 1000)
            ),
            "last_error_code": (
                _safe_error_code(str(state.get("last_error_code", "")))
                if state.get("last_error_code")
                else ""
            ),
        }
    )
    payload = json.dumps(
        safe_state,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii") + b"\n"
    _atomic_write_bytes(Path(path), payload, mode=0o600)


def _append_controlled_log(paths: BackgroundPaths, entry: dict) -> None:
    _ensure_private_directory(paths.log_dir)
    if paths.log.is_symlink():
        raise BackgroundPullError("background_log_symlink")
    try:
        if paths.log.exists() and paths.log.stat().st_size >= LOG_MAX_BYTES:
            rotated = paths.log.with_name(paths.log.name + ".1")
            rotated.unlink(missing_ok=True)
            os.replace(paths.log, rotated)
        line = json.dumps(
            entry,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
        descriptor = os.open(
            paths.log,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
            os.write(descriptor, line.encode("ascii"))
        finally:
            os.close(descriptor)
    except OSError as error:
        raise BackgroundPullError("background_log_unavailable") from error


def _record_success(paths: BackgroundPaths, result: PullResult) -> None:
    timestamp = _utc_now()
    state = load_background_state(paths.state)
    state.update(
        {
            "last_check_at": timestamp,
            "last_success_at": timestamp,
            "last_received_count": result.imported_count,
            "last_error_code": "",
        }
    )
    write_background_state(paths.state, state)
    if result.imported_count or result.acknowledged_count:
        _append_controlled_log(
            paths,
            {
                "time": timestamp,
                "status": "success",
                "received": result.imported_count,
                "acknowledged": result.acknowledged_count,
                "duplicates": result.duplicate_count,
            },
        )


def record_background_error(paths: BackgroundPaths, code: str) -> None:
    timestamp = _utc_now()
    safe_code = _safe_error_code(code)
    state = load_background_state(paths.state)
    state.update(
        {
            "last_check_at": timestamp,
            "last_received_count": 0,
            "last_error_code": safe_code,
        }
    )
    write_background_state(paths.state, state)
    _append_controlled_log(
        paths,
        {"time": timestamp, "status": "failed", "error_code": safe_code},
    )


def run_production_pull_once(
    *,
    paths: BackgroundPaths | None = None,
    respect_setting: bool = True,
    setting_reader: Callable[[], bool] = auto_pull_enabled,
    transport=None,
    pull: Callable[..., PullResult] = pull_production_submissions,
) -> AutoPullOutcome:
    active_paths = background_paths() if paths is None else paths
    try:
        with ProductionPullLock(active_paths.lock):
            if respect_setting and not setting_reader():
                return AutoPullOutcome(status="disabled")
            active_transport = (
                SystemSshSubmissionTransport(mode="production")
                if transport is None
                else transport
            )
            try:
                result = pull(active_transport)
            except GaleSubmissionPullError as error:
                record_background_error(active_paths, error.code)
                return AutoPullOutcome(status="failed", error_code=error.code)
            except Exception:
                record_background_error(active_paths, "unexpected_failure")
                return AutoPullOutcome(
                    status="failed", error_code="unexpected_failure"
                )
            _record_success(active_paths, result)
            return AutoPullOutcome(status="success", result=result)
    except BackgroundPullError as error:
        if error.code == "pull_already_running":
            return AutoPullOutcome(status="locked")
        try:
            record_background_error(active_paths, error.code)
        except BackgroundPullError:
            pass
        return AutoPullOutcome(status="failed", error_code=error.code)


def desired_launch_agent(paths: BackgroundPaths) -> dict:
    helper_path = str(paths.helper.resolve(strict=False))
    if not helper_path.startswith(str(paths.data_root) + os.sep):
        raise BackgroundPullError("launch_agent_target_invalid")
    return {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [helper_path],
        "RunAtLoad": True,
        "StartInterval": LAUNCH_AGENT_INTERVAL_SECONDS,
        "KeepAlive": False,
        "ProcessType": "Background",
        "WorkingDirectory": "/",
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
        "EnvironmentVariables": {
            "HOME": str(paths.home),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        },
    }


def _launchctl(
    arguments: list[str], *, timeout: int = 20
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["/bin/launchctl", *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
            cwd="/",
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BackgroundPullError("launch_agent_command_failed") from error


def launch_agent_loaded(
    *, uid: int | None = None, runner: Callable[..., subprocess.CompletedProcess] = _launchctl
) -> bool:
    user_id = os.getuid() if uid is None else int(uid)
    completed = runner(["print", f"gui/{user_id}/{LAUNCH_AGENT_LABEL}"])
    return completed.returncode == 0


def install_helper_atomic(
    source: Path,
    destination: Path,
    *,
    replace: Callable[[str, str], None] = os.replace,
    version_reader: Callable[[Path], str] = read_helper_version,
    architecture_validator: Callable[[Path], None] = validate_arm64_macho,
) -> tuple[bool, str, str]:
    source_path = Path(source)
    target = Path(destination)
    architecture_validator(source_path)
    source_version = version_reader(source_path)
    source_hash = sha256_file(source_path)
    if target.is_symlink():
        raise BackgroundPullError("installed_helper_symlink")
    if target.is_file():
        try:
            architecture_validator(target)
            installed_version = version_reader(target)
            if installed_version == source_version and sha256_file(target) == source_hash:
                os.chmod(target, 0o700)
                return False, source_hash, source_version
        except BackgroundPullError:
            pass

    _ensure_private_directory(target.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copyfile(source_path, temporary)
        os.chmod(temporary, 0o700)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        architecture_validator(temporary)
        if version_reader(temporary) != source_version:
            raise BackgroundPullError("helper_version_mismatch")
        if sha256_file(temporary) != source_hash:
            raise BackgroundPullError("helper_hash_mismatch")
        replace(str(temporary), str(target))
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return True, source_hash, source_version


def install_launch_agent(
    paths: BackgroundPaths,
    *,
    uid: int | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = _launchctl,
) -> tuple[bool, bool]:
    user_id = os.getuid() if uid is None else int(uid)
    desired = desired_launch_agent(paths)
    payload = plistlib.dumps(desired, fmt=plistlib.FMT_XML, sort_keys=True)
    prior_payload = paths.plist.read_bytes() if paths.plist.is_file() else None
    plist_matches = prior_payload == payload
    was_loaded = launch_agent_loaded(uid=user_id, runner=runner)
    if plist_matches and was_loaded:
        return False, True

    if not plist_matches:
        _atomic_write_bytes(paths.plist, payload, mode=0o600)
    domain = f"gui/{user_id}"
    service = f"{domain}/{LAUNCH_AGENT_LABEL}"
    if was_loaded:
        stopped = runner(["bootout", service])
        if stopped.returncode != 0:
            raise BackgroundPullError("launch_agent_bootout_failed")
    started = runner(["bootstrap", domain, str(paths.plist)])
    if started.returncode == 0 and launch_agent_loaded(uid=user_id, runner=runner):
        return not plist_matches, True

    if prior_payload is not None:
        _atomic_write_bytes(paths.plist, prior_payload, mode=0o600)
        if was_loaded:
            runner(["bootstrap", domain, str(paths.plist)])
    else:
        paths.plist.unlink(missing_ok=True)
    raise BackgroundPullError("launch_agent_bootstrap_failed")


def ensure_background_installation(
    *,
    source: Path | None = None,
    paths: BackgroundPaths | None = None,
    manage_launch_agent: bool = True,
) -> InstallResult:
    active_paths = background_paths() if paths is None else paths
    source_path = bundled_helper_source() if source is None else Path(source)
    with ProductionPullLock(
        active_paths.install_lock,
        busy_code="background_install_running",
    ):
        helper_updated, helper_hash, helper_version = install_helper_atomic(
            source_path, active_paths.helper
        )
        plist_updated = False
        loaded = False
        if manage_launch_agent:
            plist_updated, loaded = install_launch_agent(active_paths)
        state = load_background_state(active_paths.state)
        state["background_version"] = HELPER_VERSION
        state["helper_version"] = HELPER_VERSION
        write_background_state(active_paths.state, state)
    return InstallResult(
        helper_updated=helper_updated,
        plist_updated=plist_updated,
        launch_agent_loaded=loaded,
        helper_sha256=helper_hash,
        helper_version=helper_version,
    )


def run_installed_helper(
    *, paths: BackgroundPaths | None = None, timeout: int = 90
) -> int:
    active_paths = background_paths() if paths is None else paths
    if not active_paths.helper.is_file() or active_paths.helper.is_symlink():
        raise BackgroundPullError("installed_helper_unavailable")
    environment = {
        "HOME": str(active_paths.home),
        "LOGNAME": pwd.getpwuid(os.geteuid()).pw_name,
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "USER": pwd.getpwuid(os.geteuid()).pw_name,
    }
    try:
        completed = subprocess.run(
            [str(active_paths.helper)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout,
            cwd="/",
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BackgroundPullError("installed_helper_execution_failed") from error
    return int(completed.returncode)


def start_installed_helper(*, paths: BackgroundPaths | None = None) -> int:
    active_paths = background_paths() if paths is None else paths
    if not active_paths.helper.is_file() or active_paths.helper.is_symlink():
        raise BackgroundPullError("installed_helper_unavailable")
    account_name = pwd.getpwuid(os.geteuid()).pw_name
    environment = {
        "HOME": str(active_paths.home),
        "LOGNAME": account_name,
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "USER": account_name,
    }
    try:
        process = subprocess.Popen(
            [str(active_paths.helper)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd="/",
            env=environment,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as error:
        raise BackgroundPullError("installed_helper_execution_failed") from error
    return int(process.pid)


def start_bundled_installer() -> int:
    source = bundled_helper_source()
    account = pwd.getpwuid(os.geteuid())
    environment = {
        "HOME": account.pw_dir,
        "LOGNAME": account.pw_name,
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "USER": account.pw_name,
    }
    try:
        process = subprocess.Popen(
            [str(source), "--install"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd="/",
            env=environment,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as error:
        raise BackgroundPullError("helper_installer_execution_failed") from error
    return int(process.pid)


def uninstall_background(
    *, paths: BackgroundPaths | None = None, remove_helper: bool = True
) -> None:
    active_paths = background_paths() if paths is None else paths
    user_id = os.getuid()
    if launch_agent_loaded(uid=user_id):
        completed = _launchctl(
            ["bootout", f"gui/{user_id}/{LAUNCH_AGENT_LABEL}"]
        )
        if completed.returncode != 0:
            raise BackgroundPullError("launch_agent_bootout_failed")
    active_paths.plist.unlink(missing_ok=True)
    if remove_helper:
        active_paths.helper.unlink(missing_ok=True)


def background_status_text(path: Path | None = None) -> str:
    state = load_background_state(path)
    if state["last_error_code"]:
        return "最近检查失败，将自动重试"
    if state["last_check_at"]:
        try:
            checked = datetime.fromisoformat(state["last_check_at"])
            return f"最近检查：{checked.astimezone().strftime('%H:%M')}"
        except ValueError:
            pass
    return "等待首次自动检查"
