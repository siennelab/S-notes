"""Local Gale mirror preparation, publishing, and publish state owner."""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import subprocess
import sqlite3
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Protocol

from src.services.gale_mirror_service import (
    GALE_ACTOR_ID,
    GaleMirrorError,
    export_gale_mirror,
    verify_gale_mirror,
)
from src.services.gale_publish_contract import (
    REMOTE_MIRROR_ROOT,
    REMOTE_INCOMING_DIR,
    SHA256_PATTERN,
    SAFE_REASON_PATTERN,
    GalePublishError,
    RemoteActivationResult,
    sha256_file,
    validate_sha256,
    safe_hash_text,
    safe_state_text,
    safe_reason_text,
    utc_now,
)

# Compatibility re-exports only; implementations belong to the remote owner.
from src.services.gale_remote_activation import (
    RemoteActivationConfig,
    OperatorRollbackResult,
    activate_remote_mirror,
    operator_rollback_remote_mirror,
    production_remote_activation_config,
    validate_activated_mirror,
    REMOTE_CURRENT_DIR,
    REMOTE_PREVIOUS_DIR,
    REMOTE_CURRENT_MIRROR,
    REMOTE_PREVIOUS_MIRROR,
    REMOTE_SERVICE_NAME,
    REMOTE_LOCK_PATH,
)


LOGGER = logging.getLogger("snotes_gale_publish")

REMOTE_SSH_HOST = "oracle-a1"
REMOTE_ACTIVATOR = "/usr/local/sbin/snotes-gale-activate"

DEFAULT_RETRY_DELAY_SECONDS = 5 * 60


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
            "last_success_time": safe_state_text(
                payload.get("last_success_time")
            ),
            "last_success_hash": safe_hash_text(
                payload.get("last_success_hash")
            ),
            "last_success_source_hash": safe_hash_text(
                payload.get("last_success_source_hash")
            ),
            "last_failure_time": safe_state_text(
                payload.get("last_failure_time")
            ),
            "last_failure_reason": safe_reason_text(
                payload.get("last_failure_reason")
            ),
            "last_failure_check": safe_reason_text(
                payload.get("last_failure_check")
            ),
            "needs_retry": bool(payload.get("needs_retry", False)),
        }
        return cls(**values)


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
        validate_sha256(sha256)
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
        validate_sha256(sha256)
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

        success_time = utc_now()
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
        validate_sha256(sha256)
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
        validate_sha256(sha256)
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
        last_failure_time=utc_now(),
        last_failure_reason=safe_reason_text(reason),
        last_failure_check=safe_reason_text(check) if check else "",
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
        utc_now(),
        sha256,
        int(note_count),
        safe_reason_text(status),
        safe_reason_text(reason),
        safe_reason_text(check) if check else "none",
    )
