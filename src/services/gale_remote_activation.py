"""Server-side Gale activation and rollback owner.

Owns incoming/current/previous transactions, the shared server lock,
permissions, service switching, and both automatic and operator recovery.
"""
from __future__ import annotations

import fcntl
import logging
import os
import pwd
import re
import socket
import stat
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.services.gale_mirror_service import (
    GALE_ACTOR_ID, GaleMirrorError, GaleMirrorVerification, verify_gale_mirror,
)
from src.services.gale_remote_service import (
    GaleRemoteConfig, get_remote_note, search_remote_mirror, validate_remote_mirror,
)
from src.services.gale_publish_contract import (
    REMOTE_MIRROR_ROOT,
    REMOTE_INCOMING_DIR,
    GalePublishError,
    RemoteActivationResult,
    sha256_file,
    validate_sha256,
    safe_hash_text,
    safe_state_text,
    safe_reason_text,
    utc_now,
)

LOGGER = logging.getLogger("snotes_gale_publish")
REMOTE_CURRENT_DIR = REMOTE_MIRROR_ROOT / "current"
REMOTE_PREVIOUS_DIR = REMOTE_MIRROR_ROOT / "previous"
REMOTE_CURRENT_MIRROR = REMOTE_CURRENT_DIR / "gale-mirror.db"
REMOTE_PREVIOUS_MIRROR = REMOTE_PREVIOUS_DIR / "gale-mirror.db"
REMOTE_SERVICE_NAME = "snotes-gale-mcp-oidc-staging.service"
REMOTE_LOCK_PATH = Path("/run/lock/snotes-gale-publish.lock")


@dataclass(frozen=True)
class RemoteActivationConfig:
    incoming_dir: Path
    current_dir: Path
    previous_dir: Path
    current_mirror: Path
    previous_mirror: Path
    lock_path: Path
    service_name: str
    owner_uid: int
    owner_gid: int


@dataclass(frozen=True)
class OperatorRollbackResult:
    status: str
    reason: str
    current_sha256: str
    previous_sha256: str
    current_note_count: int
    previous_note_count: int
    auto_restored: bool = False


def production_remote_activation_config() -> RemoteActivationConfig:
    account = pwd.getpwnam("snotes-gale")
    return RemoteActivationConfig(
        incoming_dir=REMOTE_INCOMING_DIR,
        current_dir=REMOTE_CURRENT_DIR,
        previous_dir=REMOTE_PREVIOUS_DIR,
        current_mirror=REMOTE_CURRENT_MIRROR,
        previous_mirror=REMOTE_PREVIOUS_MIRROR,
        lock_path=REMOTE_LOCK_PATH,
        service_name=REMOTE_SERVICE_NAME,
        owner_uid=account.pw_uid,
        owner_gid=account.pw_gid,
    )


def activate_remote_mirror(
    config: RemoteActivationConfig,
    *,
    expected_sha256: str,
    expected_note_count: int,
    restart_service: Callable[[str], bool] | None = None,
    service_probe: Callable[[Path, Path, str, int], bool] | None = None,
    event_logger: Callable[[str, str, int, str], None] | None = None,
) -> RemoteActivationResult:
    validate_sha256(expected_sha256)
    expected_count = int(expected_note_count)
    if not 1 <= expected_count <= 9_223_372_036_854_775_807:
        raise GalePublishError("invalid_note_count")
    candidate = config.incoming_dir / f"{expected_sha256}.db"
    restart = restart_service or _restart_system_service
    probe = service_probe or _probe_activated_service
    record_event = event_logger or _log_remote_event

    config.lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    with config.lock_path.open("a+b") as lock_file:
        os.chmod(config.lock_path, 0o600)
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return RemoteActivationResult(
                "failed",
                "publisher_busy",
                expected_sha256,
                expected_count,
            )

        try:
            _validate_remote_layout(config, candidate)
            os.chown(candidate, config.owner_uid, config.owner_gid)
            os.chmod(candidate, 0o600)
            _verify_exact_mirror(
                candidate,
                expected_sha256=expected_sha256,
                expected_note_count=expected_count,
            )
        except (GaleMirrorError, GalePublishError, OSError):
            _unlink_if_regular(candidate)
            record_event(
                "failed",
                expected_sha256,
                expected_count,
                "remote_validation_failed",
            )
            return RemoteActivationResult(
                "failed",
                "remote_validation_failed",
                expected_sha256,
                expected_count,
            )

        try:
            current_verification = verify_gale_mirror(
                config.current_mirror
            )
            current_hash = sha256_file(config.current_mirror)
        except (GaleMirrorError, OSError):
            _unlink_if_regular(candidate)
            record_event(
                "failed",
                expected_sha256,
                expected_count,
                "current_validation_failed",
            )
            return RemoteActivationResult(
                "failed",
                "remote_validation_failed",
                expected_sha256,
                expected_count,
            )
        if current_hash == expected_sha256:
            _unlink_if_regular(candidate)
            record_event(
                "duplicate",
                expected_sha256,
                expected_count,
                "same_hash",
            )
            return RemoteActivationResult(
                "duplicate",
                "same_hash",
                expected_sha256,
                expected_count,
            )

        _unlink_if_regular(config.previous_mirror)
        try:
            os.replace(config.current_mirror, config.previous_mirror)
            os.replace(candidate, config.current_mirror)
        except OSError:
            if (
                not config.current_mirror.exists()
                and config.previous_mirror.is_file()
                and not config.previous_mirror.is_symlink()
            ):
                try:
                    os.replace(
                        config.previous_mirror,
                        config.current_mirror,
                    )
                except OSError:
                    record_event(
                        "failed",
                        expected_sha256,
                        expected_count,
                        "rollback_failed",
                    )
                    return RemoteActivationResult(
                        "failed",
                        "rollback_failed",
                        expected_sha256,
                        expected_count,
                        rolled_back=True,
                        previous_preserved=False,
                    )
            _unlink_if_regular(candidate)
            record_event(
                "failed",
                expected_sha256,
                expected_count,
                "activation_switch_failed",
            )
            return RemoteActivationResult(
                "failed",
                "remote_validation_failed",
                expected_sha256,
                expected_count,
            )
        activated = _attempt_restart_and_probe(
            restart,
            probe,
            config.service_name,
            config.current_mirror,
            config.current_dir,
            expected_sha256,
            expected_count,
        )
        if activated:
            record_event(
                "success",
                expected_sha256,
                expected_count,
                "none",
            )
            return RemoteActivationResult(
                "success",
                "none",
                expected_sha256,
                expected_count,
            )

        try:
            _atomic_swap_current_previous(
                config,
                operation_id=f"auto-{uuid.uuid4().hex}",
            )
        except GalePublishError:
            record_event(
                "failed",
                expected_sha256,
                expected_count,
                "rollback_failed",
            )
            return RemoteActivationResult(
                "failed",
                "rollback_failed",
                expected_sha256,
                expected_count,
                rolled_back=True,
                previous_preserved=False,
            )
        rollback_ok = _attempt_restart_and_probe(
            restart,
            probe,
            config.service_name,
            config.current_mirror,
            config.current_dir,
            current_hash,
            current_verification.note_count,
        )
        if rollback_ok:
            record_event(
                "failed",
                expected_sha256,
                expected_count,
                "activation_failed_rolled_back",
            )
            return RemoteActivationResult(
                "failed",
                "activation_failed_rolled_back",
                expected_sha256,
                expected_count,
                rolled_back=True,
            )

        record_event(
            "failed",
            expected_sha256,
            expected_count,
            "rollback_failed",
        )
        return RemoteActivationResult(
            "failed",
            "rollback_failed",
            expected_sha256,
            expected_count,
            rolled_back=True,
            previous_preserved=False,
        )


def operator_rollback_remote_mirror(
    config: RemoteActivationConfig,
    *,
    expected_current_sha256: str,
    expected_previous_sha256: str,
    service_is_active: Callable[[str], bool] | None = None,
    stop_service: Callable[[str], bool] | None = None,
    start_service: Callable[[str], bool] | None = None,
    service_probe: Callable[[Path, Path, str, int], bool] | None = None,
    event_logger: (
        Callable[[str, str, str, str, int, int, str, int], None] | None
    ) = None,
) -> OperatorRollbackResult:
    """Atomically exchange validated current/previous mirrors under the publish lock."""
    validate_sha256(expected_current_sha256)
    validate_sha256(expected_previous_sha256)
    active = service_is_active or _system_service_is_active
    stop = stop_service or _stop_system_service
    start = start_service or _start_system_service
    probe = service_probe or _probe_activated_service
    record_event = event_logger or _log_operator_rollback_event
    operation_id = uuid.uuid4().hex
    started_at = time.monotonic()

    config.lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    with config.lock_path.open("a+b") as lock_file:
        os.chmod(config.lock_path, 0o600)
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            result = _operator_rollback_failure(
                "publisher_busy",
                expected_current_sha256,
                expected_previous_sha256,
            )
            _record_operator_rollback_event(
                record_event,
                operation_id,
                result,
                started_at,
            )
            return result

        try:
            current_verification, previous_verification = (
                _validate_operator_rollback_preconditions(
                    config,
                    expected_current_sha256=expected_current_sha256,
                    expected_previous_sha256=expected_previous_sha256,
                )
            )
            if not active(config.service_name):
                raise GalePublishError("service_not_active")
        except (GaleMirrorError, GalePublishError, OSError) as error:
            reason = (
                error.code
                if isinstance(error, GalePublishError)
                else "remote_validation_failed"
            )
            result = _operator_rollback_failure(
                reason,
                expected_current_sha256,
                expected_previous_sha256,
            )
            _record_operator_rollback_event(
                record_event,
                operation_id,
                result,
                started_at,
            )
            return result

        if not stop(config.service_name):
            _attempt_restart_and_probe(
                start,
                probe,
                config.service_name,
                config.current_mirror,
                config.current_dir,
                expected_current_sha256,
                current_verification.note_count,
            )
            result = _operator_rollback_failure(
                "service_stop_failed",
                expected_current_sha256,
                expected_previous_sha256,
                current_note_count=current_verification.note_count,
                previous_note_count=previous_verification.note_count,
            )
            _record_operator_rollback_event(
                record_event,
                operation_id,
                result,
                started_at,
            )
            return result

        try:
            _atomic_swap_current_previous(config, operation_id=operation_id)
            _verify_exact_mirror(
                config.current_mirror,
                expected_sha256=expected_previous_sha256,
                expected_note_count=previous_verification.note_count,
            )
            _verify_exact_mirror(
                config.previous_mirror,
                expected_sha256=expected_current_sha256,
                expected_note_count=current_verification.note_count,
            )
        except (GaleMirrorError, GalePublishError, OSError) as error:
            restored = _restore_operator_layout_and_service(
                config,
                operation_id=operation_id,
                expected_current_sha256=expected_current_sha256,
                expected_current_note_count=current_verification.note_count,
                stop_service=stop,
                start_service=start,
                service_probe=probe,
            )
            reason = "swap_failed_restored" if restored else "rollback_failed"
            if isinstance(error, GalePublishError) and not error.previous_preserved:
                reason = "rollback_failed"
            result = _operator_rollback_failure(
                reason,
                expected_current_sha256,
                expected_previous_sha256,
                current_note_count=current_verification.note_count,
                previous_note_count=previous_verification.note_count,
                auto_restored=restored,
            )
            _record_operator_rollback_event(
                record_event,
                operation_id,
                result,
                started_at,
            )
            return result

        healthy = _attempt_restart_and_probe(
            start,
            probe,
            config.service_name,
            config.current_mirror,
            config.current_dir,
            expected_previous_sha256,
            previous_verification.note_count,
        )
        if not healthy:
            restored = _restore_operator_layout_and_service(
                config,
                operation_id=operation_id,
                expected_current_sha256=expected_current_sha256,
                expected_current_note_count=current_verification.note_count,
                stop_service=stop,
                start_service=start,
                service_probe=probe,
            )
            result = _operator_rollback_failure(
                (
                    "health_check_failed_restored"
                    if restored
                    else "rollback_failed"
                ),
                expected_current_sha256,
                expected_previous_sha256,
                current_note_count=current_verification.note_count,
                previous_note_count=previous_verification.note_count,
                auto_restored=restored,
            )
            _record_operator_rollback_event(
                record_event,
                operation_id,
                result,
                started_at,
            )
            return result

        result = OperatorRollbackResult(
            status="success",
            reason="none",
            current_sha256=expected_previous_sha256,
            previous_sha256=expected_current_sha256,
            current_note_count=previous_verification.note_count,
            previous_note_count=current_verification.note_count,
        )
        _record_operator_rollback_event(
            record_event,
            operation_id,
            result,
            started_at,
        )
        return result


def validate_activated_mirror(
    mirror_path: Path,
    mirror_root: Path,
    *,
    expected_sha256: str,
    expected_note_count: int,
) -> None:
    _verify_exact_mirror(
        mirror_path,
        expected_sha256=expected_sha256,
        expected_note_count=expected_note_count,
    )
    owner_uid = mirror_path.stat().st_uid
    placeholder_url = "https://publisher.invalid/mcp"
    config = GaleRemoteConfig(
        mirror_path=mirror_path,
        mirror_root=mirror_root,
        issuer_url="https://publisher.invalid/",
        resource_url=placeholder_url,
        audience=placeholder_url,
        jwks_url="https://publisher.invalid/.well-known/jwks.json",
        allowed_subjects=frozenset({"fictional-publisher-probe"}),
        expected_owner_uid=owner_uid,
    )
    validate_remote_mirror(config)
    note_id, query = _first_probe_note(mirror_path)
    results = search_remote_mirror(config, query, limit=20)
    if not any(int(row["note_id"]) == note_id for row in results):
        raise GalePublishError("search_probe_failed")
    note = get_remote_note(config, note_id)
    if note is None or int(note["note_id"]) != note_id:
        raise GalePublishError("read_probe_failed")


def _validate_remote_layout(
    config: RemoteActivationConfig,
    candidate: Path,
) -> None:
    for directory in (
        config.incoming_dir,
        config.current_dir,
        config.previous_dir,
    ):
        if not directory.is_dir() or directory.is_symlink():
            raise GalePublishError("remote_layout_invalid")
    if (
        candidate.parent != config.incoming_dir
        or config.current_mirror.parent != config.current_dir
        or config.previous_mirror.parent != config.previous_dir
        or config.current_mirror.name != "gale-mirror.db"
        or config.previous_mirror.name != "gale-mirror.db"
    ):
        raise GalePublishError("remote_layout_invalid")
    if not candidate.is_file() or candidate.is_symlink():
        raise GalePublishError("incoming_missing")
    if (
        not config.current_mirror.is_file()
        or config.current_mirror.is_symlink()
    ):
        raise GalePublishError("current_missing")
    if config.previous_mirror.is_symlink():
        raise GalePublishError("previous_unsafe")


def _validate_operator_rollback_preconditions(
    config: RemoteActivationConfig,
    *,
    expected_current_sha256: str,
    expected_previous_sha256: str,
) -> tuple[GaleMirrorVerification, GaleMirrorVerification]:
    for directory in (
        config.incoming_dir,
        config.current_dir,
        config.previous_dir,
    ):
        if not directory.is_dir() or directory.is_symlink():
            raise GalePublishError("remote_layout_invalid")
    if (
        config.current_mirror.parent != config.current_dir
        or config.previous_mirror.parent != config.previous_dir
        or config.current_mirror.name != "gale-mirror.db"
        or config.previous_mirror.name != "gale-mirror.db"
    ):
        raise GalePublishError("remote_layout_invalid")
    _require_safe_operator_mirror(
        config.current_mirror,
        missing_reason="current_missing",
        unsafe_reason="current_unsafe",
        config=config,
    )
    _require_safe_operator_mirror(
        config.previous_mirror,
        missing_reason="previous_missing",
        unsafe_reason="previous_unsafe",
        config=config,
    )
    if any(config.incoming_dir.iterdir()):
        raise GalePublishError("incoming_not_empty")
    if not (
        config.current_dir.stat().st_dev
        == config.previous_dir.stat().st_dev
        == config.current_mirror.stat().st_dev
        == config.previous_mirror.stat().st_dev
    ):
        raise GalePublishError("mirror_filesystem_mismatch")
    if _sqlite_sidecars_present(config.current_mirror):
        raise GalePublishError("current_sidecar_present")
    if _sqlite_sidecars_present(config.previous_mirror):
        raise GalePublishError("previous_sidecar_present")
    if sha256_file(config.current_mirror) != expected_current_sha256:
        raise GalePublishError("current_hash_mismatch")
    if sha256_file(config.previous_mirror) != expected_previous_sha256:
        raise GalePublishError("previous_hash_mismatch")
    try:
        current_verification = verify_gale_mirror(
            config.current_mirror,
            expected_actor=GALE_ACTOR_ID,
        )
    except GaleMirrorError as error:
        raise GalePublishError("current_verify_failed") from error
    try:
        previous_verification = verify_gale_mirror(
            config.previous_mirror,
            expected_actor=GALE_ACTOR_ID,
        )
    except GaleMirrorError as error:
        raise GalePublishError("previous_verify_failed") from error
    return current_verification, previous_verification


def _require_safe_operator_mirror(
    path: Path,
    *,
    missing_reason: str,
    unsafe_reason: str,
    config: RemoteActivationConfig,
) -> None:
    try:
        file_stat = path.lstat()
    except FileNotFoundError as error:
        raise GalePublishError(missing_reason) from error
    if not stat.S_ISREG(file_stat.st_mode) or path.is_symlink():
        raise GalePublishError(unsafe_reason)
    if (
        file_stat.st_uid != config.owner_uid
        or file_stat.st_gid != config.owner_gid
        or stat.S_IMODE(file_stat.st_mode) != 0o600
    ):
        raise GalePublishError(unsafe_reason)


def _sqlite_sidecars_present(path: Path) -> bool:
    return any(
        Path(f"{path}{suffix}").exists()
        for suffix in ("-wal", "-shm", "-journal")
    )


def _atomic_swap_current_previous(
    config: RemoteActivationConfig,
    *,
    operation_id: str,
) -> None:
    safe_operation_id = re.sub(r"[^a-z0-9-]", "", operation_id.lower())[:64]
    if not safe_operation_id:
        raise GalePublishError("invalid_operation_id")
    placeholder = (
        config.current_dir
        / f".gale-mirror.operator-swap.{safe_operation_id}.tmp"
    )
    if placeholder.exists() or placeholder.is_symlink():
        raise GalePublishError("swap_placeholder_unsafe")

    stage = 0
    try:
        os.replace(config.current_mirror, placeholder)
        stage = 1
        os.replace(config.previous_mirror, config.current_mirror)
        stage = 2
        os.replace(placeholder, config.previous_mirror)
        stage = 3
        os.chmod(config.current_mirror, 0o600)
        os.chmod(config.previous_mirror, 0o600)
    except OSError as error:
        try:
            if stage == 1:
                os.replace(placeholder, config.current_mirror)
            elif stage == 2:
                os.replace(config.current_mirror, config.previous_mirror)
                os.replace(placeholder, config.current_mirror)
        except OSError as restore_error:
            raise GalePublishError(
                "rollback_failed",
                previous_preserved=False,
            ) from restore_error
        raise GalePublishError("swap_failed_restored") from error
    finally:
        if stage == 3 and placeholder.exists():
            raise GalePublishError(
                "swap_placeholder_retained",
                previous_preserved=False,
            )


def _restore_operator_layout_and_service(
    config: RemoteActivationConfig,
    *,
    operation_id: str,
    expected_current_sha256: str,
    expected_current_note_count: int,
    stop_service: Callable[[str], bool],
    start_service: Callable[[str], bool],
    service_probe: Callable[[Path, Path, str, int], bool],
) -> bool:
    current_hash = ""
    try:
        current_hash = sha256_file(config.current_mirror)
    except OSError:
        pass
    if current_hash != expected_current_sha256:
        if not stop_service(config.service_name):
            return False
        try:
            _atomic_swap_current_previous(
                config,
                operation_id=f"restore-{operation_id}",
            )
        except GalePublishError:
            return False
    try:
        _verify_exact_mirror(
            config.current_mirror,
            expected_sha256=expected_current_sha256,
            expected_note_count=expected_current_note_count,
        )
    except (GaleMirrorError, GalePublishError, OSError):
        return False
    return _attempt_restart_and_probe(
        start_service,
        service_probe,
        config.service_name,
        config.current_mirror,
        config.current_dir,
        expected_current_sha256,
        expected_current_note_count,
    )


def _operator_rollback_failure(
    reason: str,
    current_sha256: str,
    previous_sha256: str,
    *,
    current_note_count: int = 0,
    previous_note_count: int = 0,
    auto_restored: bool = False,
) -> OperatorRollbackResult:
    return OperatorRollbackResult(
        status="failed",
        reason=safe_reason_text(reason),
        current_sha256=current_sha256,
        previous_sha256=previous_sha256,
        current_note_count=int(current_note_count),
        previous_note_count=int(previous_note_count),
        auto_restored=auto_restored,
    )


def _record_operator_rollback_event(
    event_logger: Callable[[str, str, str, str, int, int, str, int], None],
    operation_id: str,
    result: OperatorRollbackResult,
    started_at: float,
) -> None:
    duration_ms = max(0, int((time.monotonic() - started_at) * 1000))
    event_logger(
        operation_id,
        result.status,
        result.current_sha256,
        result.previous_sha256,
        result.current_note_count,
        result.previous_note_count,
        result.reason,
        duration_ms,
    )


def _verify_exact_mirror(
    path: Path,
    *,
    expected_sha256: str,
    expected_note_count: int,
) -> None:
    if sha256_file(path) != expected_sha256:
        raise GalePublishError("hash_mismatch")
    verify_gale_mirror(
        path,
        expected_note_count=expected_note_count,
        expected_actor=GALE_ACTOR_ID,
    )


def _first_probe_note(path: Path) -> tuple[int, str]:
    import sqlite3

    connection = sqlite3.connect(
        f"{Path(path).resolve().as_uri()}?mode=ro&immutable=1",
        uri=True,
        timeout=5.0,
    )
    try:
        row = connection.execute(
            """
            SELECT source_note_id, title, body, directory, tags_text
            FROM mirror_notes
            ORDER BY mirror_note_id
            LIMIT 1
            """
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise GalePublishError("probe_note_missing")
    for value in row[1:]:
        query = str(value).strip()
        if query:
            return int(row[0]), query[:500]
    raise GalePublishError("probe_query_missing")


def _probe_activated_service(
    mirror_path: Path,
    mirror_root: Path,
    expected_sha256: str,
    expected_note_count: int,
) -> bool:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        active = subprocess.run(
            ["/usr/bin/systemctl", "is-active", "--quiet", REMOTE_SERVICE_NAME],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode == 0
        if active:
            try:
                with socket.create_connection(
                    ("127.0.0.1", 8877),
                    timeout=2.0,
                ):
                    pass
                validate_activated_mirror(
                    mirror_path,
                    mirror_root,
                    expected_sha256=expected_sha256,
                    expected_note_count=expected_note_count,
                )
                return True
            except (OSError, GaleMirrorError, GalePublishError):
                pass
        time.sleep(1)
    return False


def _attempt_restart_and_probe(
    restart_service: Callable[[str], bool],
    service_probe: Callable[[Path, Path, str, int], bool],
    service_name: str,
    mirror_path: Path,
    mirror_root: Path,
    expected_sha256: str,
    expected_note_count: int,
) -> bool:
    try:
        if not restart_service(service_name):
            return False
        return bool(
            service_probe(
                mirror_path,
                mirror_root,
                expected_sha256,
                expected_note_count,
            )
        )
    except Exception:
        return False


def _restart_system_service(service_name: str) -> bool:
    return _run_systemctl_action("restart", service_name)


def _start_system_service(service_name: str) -> bool:
    return _run_systemctl_action("start", service_name)


def _stop_system_service(service_name: str) -> bool:
    return _run_systemctl_action("stop", service_name)


def _system_service_is_active(service_name: str) -> bool:
    if service_name != REMOTE_SERVICE_NAME:
        return False
    try:
        return (
            subprocess.run(
                ["/usr/bin/systemctl", "is-active", "--quiet", service_name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=30,
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


def _run_systemctl_action(action: str, service_name: str) -> bool:
    if (
        service_name != REMOTE_SERVICE_NAME
        or action not in {"start", "stop", "restart"}
    ):
        return False
    try:
        return (
            subprocess.run(
                ["/usr/bin/systemctl", action, service_name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=30,
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


def _log_remote_event(
    status: str,
    sha256: str,
    note_count: int,
    reason: str,
) -> None:
    safe_message = (
        f"published_at={utc_now()} sha256={sha256} "
        f"note_count={int(note_count)} status={safe_reason_text(status)} "
        f"reason={safe_reason_text(reason)}"
    )
    subprocess.run(
        ["/usr/bin/logger", "-t", "snotes-gale-publish", safe_message],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _log_operator_rollback_event(
    operation_id: str,
    status: str,
    current_sha256: str,
    previous_sha256: str,
    current_note_count: int,
    previous_note_count: int,
    reason: str,
    duration_ms: int,
) -> None:
    safe_message = (
        f"operation_id={safe_state_text(operation_id)} "
        f"status={safe_reason_text(status)} "
        f"current_sha256={safe_hash_text(current_sha256)} "
        f"previous_sha256={safe_hash_text(previous_sha256)} "
        f"current_note_count={int(current_note_count)} "
        f"previous_note_count={int(previous_note_count)} "
        f"reason={safe_reason_text(reason)} "
        f"duration_ms={max(0, int(duration_ms))}"
    )
    LOGGER.info("operator_rollback_event %s", safe_message)
    subprocess.run(
        ["/usr/bin/logger", "-t", "snotes-gale-publish", safe_message],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _unlink_if_regular(path: Path) -> None:
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISREG(file_stat.st_mode) and not path.is_symlink():
        path.unlink()
        return
    raise GalePublishError("unsafe_cleanup_target")
