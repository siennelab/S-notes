from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import pwd
import re
import socket
import stat
import subprocess
import sqlite3
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from src.services.gale_mirror_service import (
    GALE_ACTOR_ID,
    GaleMirrorError,
    GaleMirrorVerification,
    export_gale_mirror,
    verify_gale_mirror,
)
from src.services.gale_remote_service import (
    GaleRemoteConfig,
    get_remote_note,
    search_remote_mirror,
    validate_remote_mirror,
)


LOGGER = logging.getLogger("snotes_gale_publish")

REMOTE_SSH_HOST = "oracle-a1"
REMOTE_MIRROR_ROOT = Path("/var/lib/snotes-gale-mcp/mirror")
REMOTE_INCOMING_DIR = REMOTE_MIRROR_ROOT / "incoming"
REMOTE_CURRENT_DIR = REMOTE_MIRROR_ROOT / "current"
REMOTE_PREVIOUS_DIR = REMOTE_MIRROR_ROOT / "previous"
REMOTE_CURRENT_MIRROR = REMOTE_CURRENT_DIR / "gale-mirror.db"
REMOTE_PREVIOUS_MIRROR = REMOTE_PREVIOUS_DIR / "gale-mirror.db"
REMOTE_ACTIVATOR = "/usr/local/sbin/snotes-gale-activate"
REMOTE_SERVICE_NAME = "snotes-gale-mcp-oidc-staging.service"
REMOTE_LOCK_PATH = Path("/run/lock/snotes-gale-publish.lock")

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SAFE_REASON_PATTERN = re.compile(r"^[a-z0-9_]{1,64}$")
DEFAULT_RETRY_DELAY_SECONDS = 5 * 60


class GalePublishError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        previous_preserved: bool = True,
        check: str = "",
    ) -> None:
        self.code = code
        self.previous_preserved = previous_preserved
        self.check = _safe_reason_text(check) if check else ""
        super().__init__(code)


@dataclass(frozen=True)
class GalePublishConfig:
    mirror_path: Path
    state_path: Path
    lock_path: Path
    upload_timeout_seconds: int = 60
    activation_timeout_seconds: int = 120


@dataclass(frozen=True)
class GalePublishResult:
    status: str
    sha256: str
    note_count: int
    reason: str = "none"
    uploaded: bool = False
    activated: bool = False
    rolled_back: bool = False
    previous_preserved: bool = True
    check: str = ""
    source_sha256: str = ""


@dataclass(frozen=True)
class GalePublishState:
    is_publishing: bool = False
    last_success_time: str = ""
    last_success_hash: str = ""
    last_success_source_hash: str = ""
    last_failure_time: str = ""
    last_failure_reason: str = ""
    last_failure_check: str = ""
    needs_retry: bool = False

    @classmethod
    def from_payload(cls, payload: object) -> GalePublishState:
        if not isinstance(payload, dict):
            return cls()
        values = {
            "is_publishing": bool(payload.get("is_publishing", False)),
            "last_success_time": _safe_state_text(
                payload.get("last_success_time")
            ),
            "last_success_hash": _safe_hash_text(
                payload.get("last_success_hash")
            ),
            "last_success_source_hash": _safe_hash_text(
                payload.get("last_success_source_hash")
            ),
            "last_failure_time": _safe_state_text(
                payload.get("last_failure_time")
            ),
            "last_failure_reason": _safe_reason_text(
                payload.get("last_failure_reason")
            ),
            "last_failure_check": _safe_reason_text(
                payload.get("last_failure_check")
            ),
            "needs_retry": bool(payload.get("needs_retry", False)),
        }
        return cls(**values)


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
class RemoteActivationResult:
    status: str
    reason: str
    sha256: str
    note_count: int
    rolled_back: bool = False
    previous_preserved: bool = True


@dataclass(frozen=True)
class OperatorRollbackResult:
    status: str
    reason: str
    current_sha256: str
    previous_sha256: str
    current_note_count: int
    previous_note_count: int
    auto_restored: bool = False


class PublishTransport(Protocol):
    def upload(self, mirror_path: Path, sha256: str) -> None: ...

    def activate(self, sha256: str, note_count: int) -> RemoteActivationResult: ...


class SystemSshTransport:
    def __init__(
        self,
        *,
        upload_timeout_seconds: int,
        activation_timeout_seconds: int,
    ) -> None:
        self.upload_timeout_seconds = upload_timeout_seconds
        self.activation_timeout_seconds = activation_timeout_seconds

    def upload(self, mirror_path: Path, sha256: str) -> None:
        _validate_sha256(sha256)
        destination = (
            f"{REMOTE_SSH_HOST}:"
            f"{REMOTE_INCOMING_DIR.as_posix()}/{sha256}.db"
        )
        _run_quiet_command(
            [
                "/usr/bin/scp",
                "-q",
                "-p",
                "--",
                str(mirror_path),
                destination,
            ],
            timeout_seconds=self.upload_timeout_seconds,
            failure_code="upload_failed",
        )

    def activate(self, sha256: str, note_count: int) -> RemoteActivationResult:
        _validate_sha256(sha256)
        if not 0 <= int(note_count) <= 9_223_372_036_854_775_807:
            raise GalePublishError("invalid_note_count")
        completed = _run_quiet_command(
            [
                "/usr/bin/ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                REMOTE_SSH_HOST,
                "sudo",
                "-n",
                REMOTE_ACTIVATOR,
                sha256,
                str(int(note_count)),
            ],
            timeout_seconds=self.activation_timeout_seconds,
            failure_code="activation_unreachable",
            accepted_return_codes={0, 3, 10, 20, 30, 31},
        )
        mapping = {
            0: RemoteActivationResult(
                "success", "none", sha256, int(note_count)
            ),
            3: RemoteActivationResult(
                "duplicate", "same_hash", sha256, int(note_count)
            ),
            10: RemoteActivationResult(
                "failed", "publisher_busy", sha256, int(note_count)
            ),
            20: RemoteActivationResult(
                "failed", "remote_validation_failed", sha256, int(note_count)
            ),
            30: RemoteActivationResult(
                "failed",
                "activation_failed_rolled_back",
                sha256,
                int(note_count),
                rolled_back=True,
            ),
            31: RemoteActivationResult(
                "failed",
                "rollback_failed",
                sha256,
                int(note_count),
                rolled_back=True,
                previous_preserved=False,
            ),
        }
        return mapping[completed.returncode]


def load_publish_state(path: Path) -> GalePublishState:
    state_path = Path(path)
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return GalePublishState()
    return GalePublishState.from_payload(payload)


def source_inputs_sha256(
    source_database: Path,
    redaction_rules_path: Path,
) -> str:
    digest = hashlib.sha256()
    for label, raw_path in (
        (b"source-database\0", source_database),
        (b"redaction-rules\0", redaction_rules_path),
    ):
        path = Path(raw_path)
        if not path.is_file() or path.is_symlink():
            raise OSError("source_input_unavailable")
        digest.update(label)
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def generate_validate_and_publish_gale_mirror(
    config: GalePublishConfig,
    *,
    source_database: Path,
    redaction_rules_path: Path,
    transport: PublishTransport | None = None,
) -> GalePublishResult:
    mirror_path = Path(config.mirror_path)
    runtime_directory = mirror_path.parent
    runtime_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(runtime_directory, 0o700)
    snapshot_path = _new_private_runtime_file(
        runtime_directory,
        prefix=".gale-source-snapshot.",
    )
    candidate_path = _new_private_runtime_file(
        runtime_directory,
        prefix=".gale-publish-candidate.",
    )
    try:
        try:
            source_digest = source_inputs_sha256(
                source_database,
                redaction_rules_path,
            )
            _backup_source_database(
                Path(source_database),
                snapshot_path,
            )
            export_result = export_gale_mirror(
                source_database=snapshot_path,
                output_path=candidate_path,
                redaction_rules_path=Path(redaction_rules_path),
                actor=GALE_ACTOR_ID,
            )
            verification = verify_gale_mirror(
                candidate_path,
                expected_note_count=export_result.note_count,
                expected_actor=GALE_ACTOR_ID,
            )
            if verification.note_count != export_result.note_count:
                raise GaleMirrorError("expected_count_mismatch")
            os.replace(candidate_path, mirror_path)
            os.chmod(mirror_path, 0o600)
        except GaleMirrorError as error:
            _record_local_preparation_failure(config, error.code)
            raise GalePublishError(
                "local_validation_failed",
                check=error.code,
            ) from error
        except (OSError, sqlite3.Error) as error:
            check = "source_snapshot_failed"
            _record_local_preparation_failure(config, check)
            raise GalePublishError(
                "local_validation_failed",
                check=check,
            ) from error

        result = publish_gale_mirror(config, transport=transport)
        if result.status in {"success", "duplicate"}:
            settled_state = replace(
                load_publish_state(config.state_path),
                last_success_source_hash=source_digest,
            )
            _write_publish_state(config.state_path, settled_state)
        return replace(result, source_sha256=source_digest)
    finally:
        _remove_private_runtime_database(snapshot_path)
        _remove_private_runtime_database(candidate_path)


def _new_private_runtime_file(directory: Path, *, prefix: str) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=prefix,
        suffix=".db",
        dir=directory,
    )
    os.fchmod(descriptor, 0o600)
    os.close(descriptor)
    return Path(name)


def _backup_source_database(source_path: Path, snapshot_path: Path) -> None:
    if not source_path.is_file() or source_path.is_symlink():
        raise GaleMirrorError("source_database_unavailable")
    source = sqlite3.connect(
        f"{source_path.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=5.0,
    )
    destination = sqlite3.connect(snapshot_path, timeout=5.0)
    try:
        source.execute("PRAGMA query_only = ON")
        source.execute("PRAGMA busy_timeout = 5000")
        source.backup(destination)
        quick_check = destination.execute("PRAGMA quick_check").fetchone()
        if quick_check is None or str(quick_check[0]) != "ok":
            raise GaleMirrorError("source_quick_check_failed")
        integrity_check = destination.execute("PRAGMA integrity_check").fetchone()
        if integrity_check is None or str(integrity_check[0]) != "ok":
            raise GaleMirrorError("source_integrity_check_failed")
        if destination.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise GaleMirrorError("source_foreign_key_check_failed")
    finally:
        destination.close()
        source.close()
    os.chmod(snapshot_path, 0o600)


def _record_local_preparation_failure(
    config: GalePublishConfig,
    check: str,
) -> None:
    state_path = Path(config.state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    failed_state = _failure_state(
        load_publish_state(state_path),
        reason="local_validation_failed",
        needs_retry=True,
        check=check,
    )
    _write_publish_state(state_path, failed_state)
    _log_publish_event(
        status="failed",
        sha256="none",
        note_count=0,
        reason="local_validation_failed",
        check=check,
    )


def _remove_private_runtime_database(path: Path) -> None:
    for candidate in (
        path,
        Path(f"{path}-journal"),
        Path(f"{path}-wal"),
        Path(f"{path}-shm"),
    ):
        try:
            if candidate.is_symlink() or not candidate.is_file():
                if candidate.exists() or candidate.is_symlink():
                    raise GalePublishError("unsafe_cleanup_target")
                continue
            candidate.unlink()
        except FileNotFoundError:
            pass


def publish_gale_mirror(
    config: GalePublishConfig,
    *,
    transport: PublishTransport | None = None,
) -> GalePublishResult:
    mirror_path = Path(config.mirror_path)
    state_path = Path(config.state_path)
    lock_path = Path(config.lock_path)
    state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    with lock_path.open("a+b") as lock_file:
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise GalePublishError("publisher_busy") from error

        previous_state = load_publish_state(state_path)
        try:
            verification = verify_gale_mirror(
                mirror_path,
                expected_actor=GALE_ACTOR_ID,
            )
            digest = sha256_file(mirror_path)
        except GaleMirrorError as error:
            failed_state = _failure_state(
                previous_state,
                reason="local_validation_failed",
                needs_retry=True,
                check=error.code,
            )
            _write_publish_state(state_path, failed_state)
            _log_publish_event(
                status="failed",
                sha256="none",
                note_count=0,
                reason="local_validation_failed",
                check=error.code,
            )
            raise GalePublishError(
                "local_validation_failed",
                check=error.code,
            ) from error

        if previous_state.last_success_hash == digest:
            settled_state = GalePublishState(
                is_publishing=False,
                last_success_time=previous_state.last_success_time,
                last_success_hash=digest,
                last_success_source_hash=previous_state.last_success_source_hash,
                last_failure_time=previous_state.last_failure_time,
                last_failure_reason=previous_state.last_failure_reason,
                last_failure_check=previous_state.last_failure_check,
                needs_retry=False,
            )
            _write_publish_state(state_path, settled_state)
            _log_publish_event(
                status="duplicate",
                sha256=digest,
                note_count=verification.note_count,
                reason="same_hash",
            )
            return GalePublishResult(
                status="duplicate",
                sha256=digest,
                note_count=verification.note_count,
                reason="same_hash",
            )

        publishing_state = GalePublishState(
            is_publishing=True,
            last_success_time=previous_state.last_success_time,
            last_success_hash=previous_state.last_success_hash,
            last_success_source_hash=previous_state.last_success_source_hash,
            last_failure_time=previous_state.last_failure_time,
            last_failure_reason=previous_state.last_failure_reason,
            last_failure_check=previous_state.last_failure_check,
            needs_retry=previous_state.needs_retry,
        )
        _write_publish_state(state_path, publishing_state)
        active_transport = transport or SystemSshTransport(
            upload_timeout_seconds=config.upload_timeout_seconds,
            activation_timeout_seconds=config.activation_timeout_seconds,
        )
        try:
            active_transport.upload(mirror_path, digest)
            activation = active_transport.activate(
                digest,
                verification.note_count,
            )
        except GalePublishError as error:
            failed_state = _failure_state(
                previous_state,
                reason=error.code,
                needs_retry=error.code
                in {"upload_failed", "activation_unreachable", "publisher_busy"},
            )
            _write_publish_state(state_path, failed_state)
            _log_publish_event(
                status="failed",
                sha256=digest,
                note_count=verification.note_count,
                reason=error.code,
            )
            raise
        except Exception as error:
            failed_state = _failure_state(
                previous_state,
                reason="unexpected_failure",
                needs_retry=False,
            )
            _write_publish_state(state_path, failed_state)
            _log_publish_event(
                status="failed",
                sha256=digest,
                note_count=verification.note_count,
                reason="unexpected_failure",
            )
            raise GalePublishError(
                "unexpected_failure",
                previous_preserved=False,
            ) from error

        if activation.status == "failed":
            failed_state = _failure_state(
                previous_state,
                reason=activation.reason,
                needs_retry=activation.reason
                in {
                    "activation_unreachable",
                    "publisher_busy",
                    "activation_failed_rolled_back",
                },
            )
            _write_publish_state(state_path, failed_state)
            _log_publish_event(
                status="failed",
                sha256=digest,
                note_count=verification.note_count,
                reason=activation.reason,
            )
            return GalePublishResult(
                status="failed",
                sha256=digest,
                note_count=verification.note_count,
                reason=activation.reason,
                uploaded=True,
                rolled_back=activation.rolled_back,
                previous_preserved=activation.previous_preserved,
            )

        success_time = _utc_now()
        success_state = GalePublishState(
            is_publishing=False,
            last_success_time=success_time,
            last_success_hash=digest,
            last_success_source_hash=previous_state.last_success_source_hash,
            last_failure_time=previous_state.last_failure_time,
            last_failure_reason=previous_state.last_failure_reason,
            last_failure_check=previous_state.last_failure_check,
            needs_retry=False,
        )
        _write_publish_state(state_path, success_state)
        _log_publish_event(
            status=activation.status,
            sha256=digest,
            note_count=verification.note_count,
            reason=activation.reason,
        )
        return GalePublishResult(
            status=activation.status,
            sha256=digest,
            note_count=verification.note_count,
            reason=activation.reason,
            uploaded=activation.status != "duplicate",
            activated=activation.status == "success",
        )


@dataclass
class GaleAutoPublishScheduler:
    quiet_seconds: int = 15 * 60
    maximum_wait_seconds: int = 30 * 60
    retry_delay_seconds: int = DEFAULT_RETRY_DELAY_SECONDS
    enabled: bool = True
    pending_hash: str = ""
    first_change_at: float | None = None
    last_change_at: float | None = None
    retry_not_before: float | None = None
    running: bool = False
    last_success_hash: str = ""

    def observe(self, sha256: str, now: float) -> None:
        _validate_sha256(sha256)
        if sha256 == self.last_success_hash:
            self._clear_pending()
            return
        if not self.pending_hash:
            self.first_change_at = now
        if sha256 != self.pending_hash:
            if self.first_change_at is None:
                self.first_change_at = now
            self.pending_hash = sha256
            self.last_change_at = now

    def should_publish(
        self,
        now: float,
        *,
        resumed: bool = False,
        network_restored: bool = False,
    ) -> bool:
        if (
            not self.enabled
            or self.running
            or not self.pending_hash
            or self.pending_hash == self.last_success_hash
        ):
            return False
        if resumed or network_restored:
            return True
        if self.retry_not_before is not None and now < self.retry_not_before:
            return False
        quiet_due = (
            self.last_change_at is not None
            and now - self.last_change_at >= self.quiet_seconds
        )
        maximum_due = (
            self.first_change_at is not None
            and now - self.first_change_at >= self.maximum_wait_seconds
        )
        return quiet_due or maximum_due

    def mark_started(self) -> None:
        self.running = True

    def mark_success(self, sha256: str) -> None:
        _validate_sha256(sha256)
        self.last_success_hash = sha256
        self.running = False
        self.retry_not_before = None
        if self.pending_hash == sha256:
            self._clear_pending()

    def mark_failure(self, now: float) -> None:
        self.running = False
        self.retry_not_before = now + self.retry_delay_seconds

    def disable(self) -> None:
        self.enabled = False

    def enable(self) -> None:
        self.enabled = True

    def _clear_pending(self) -> None:
        self.pending_hash = ""
        self.first_change_at = None
        self.last_change_at = None
        self.retry_not_before = None


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
    _validate_sha256(expected_sha256)
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
    _validate_sha256(expected_current_sha256)
    _validate_sha256(expected_previous_sha256)
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        reason=_safe_reason_text(reason),
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


def _run_quiet_command(
    command: list[str],
    *,
    timeout_seconds: int,
    failure_code: str,
    accepted_return_codes: set[int] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    accepted = {0} if accepted_return_codes is None else accepted_return_codes
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GalePublishError(failure_code) from error
    if completed.returncode not in accepted:
        raise GalePublishError(failure_code)
    return completed


def _write_publish_state(path: Path, state: GalePublishState) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(
                asdict(state),
                output,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _failure_state(
    previous: GalePublishState,
    *,
    reason: str,
    needs_retry: bool,
    check: str = "",
) -> GalePublishState:
    return GalePublishState(
        is_publishing=False,
        last_success_time=previous.last_success_time,
        last_success_hash=previous.last_success_hash,
        last_success_source_hash=previous.last_success_source_hash,
        last_failure_time=_utc_now(),
        last_failure_reason=_safe_reason_text(reason),
        last_failure_check=_safe_reason_text(check) if check else "",
        needs_retry=needs_retry,
    )


def _log_publish_event(
    *,
    status: str,
    sha256: str,
    note_count: int,
    reason: str,
    check: str = "",
) -> None:
    LOGGER.info(
        "publish_event published_at=%s sha256=%s note_count=%d "
        "status=%s reason=%s check=%s",
        _utc_now(),
        sha256,
        int(note_count),
        _safe_reason_text(status),
        _safe_reason_text(reason),
        _safe_reason_text(check) if check else "none",
    )


def _log_remote_event(
    status: str,
    sha256: str,
    note_count: int,
    reason: str,
) -> None:
    safe_message = (
        f"published_at={_utc_now()} sha256={sha256} "
        f"note_count={int(note_count)} status={_safe_reason_text(status)} "
        f"reason={_safe_reason_text(reason)}"
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
        f"operation_id={_safe_state_text(operation_id)} "
        f"status={_safe_reason_text(status)} "
        f"current_sha256={_safe_hash_text(current_sha256)} "
        f"previous_sha256={_safe_hash_text(previous_sha256)} "
        f"current_note_count={int(current_note_count)} "
        f"previous_note_count={int(previous_note_count)} "
        f"reason={_safe_reason_text(reason)} "
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


def _validate_sha256(value: str) -> None:
    if not SHA256_PATTERN.fullmatch(str(value)):
        raise GalePublishError("invalid_sha256")


def _safe_hash_text(value: object) -> str:
    text = str(value or "")
    return text if not text or SHA256_PATTERN.fullmatch(text) else ""


def _safe_state_text(value: object) -> str:
    text = str(value or "")
    return text[:64] if len(text) <= 64 else ""


def _safe_reason_text(value: object) -> str:
    text = str(value or "")
    return text if SAFE_REASON_PATTERN.fullmatch(text) else "invalid_reason"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
