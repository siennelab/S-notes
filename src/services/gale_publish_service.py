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
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from src.services.gale_mirror_service import (
    GALE_ACTOR_ID,
    GaleMirrorError,
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
    def __init__(self, code: str, *, previous_preserved: bool = True) -> None:
        self.code = code
        self.previous_preserved = previous_preserved
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


@dataclass(frozen=True)
class GalePublishState:
    is_publishing: bool = False
    last_success_time: str = ""
    last_success_hash: str = ""
    last_failure_time: str = ""
    last_failure_reason: str = ""
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
            "last_failure_time": _safe_state_text(
                payload.get("last_failure_time")
            ),
            "last_failure_reason": _safe_reason_text(
                payload.get("last_failure_reason")
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
                needs_retry=False,
            )
            _write_publish_state(state_path, failed_state)
            _log_publish_event(
                status="failed",
                sha256="none",
                note_count=0,
                reason="local_validation_failed",
            )
            raise GalePublishError("local_validation_failed") from error

        if previous_state.last_success_hash == digest:
            settled_state = GalePublishState(
                is_publishing=False,
                last_success_time=previous_state.last_success_time,
                last_success_hash=digest,
                last_failure_time=previous_state.last_failure_time,
                last_failure_reason=previous_state.last_failure_reason,
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
            last_failure_time=previous_state.last_failure_time,
            last_failure_reason=previous_state.last_failure_reason,
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
            last_failure_time=previous_state.last_failure_time,
            last_failure_reason=previous_state.last_failure_reason,
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
            os.replace(config.previous_mirror, config.current_mirror)
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
    if service_name != REMOTE_SERVICE_NAME:
        return False
    try:
        return (
            subprocess.run(
                ["/usr/bin/systemctl", "restart", service_name],
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
) -> GalePublishState:
    return GalePublishState(
        is_publishing=False,
        last_success_time=previous.last_success_time,
        last_success_hash=previous.last_success_hash,
        last_failure_time=_utc_now(),
        last_failure_reason=_safe_reason_text(reason),
        needs_retry=needs_retry,
    )


def _log_publish_event(
    *,
    status: str,
    sha256: str,
    note_count: int,
    reason: str,
) -> None:
    LOGGER.info(
        "publish_event published_at=%s sha256=%s note_count=%d "
        "status=%s reason=%s",
        _utc_now(),
        sha256,
        int(note_count),
        _safe_reason_text(status),
        _safe_reason_text(reason),
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
