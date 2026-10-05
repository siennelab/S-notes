"""Generate and publish the two GreenCloud actor mirrors from the Mac authority."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from src.snotes.application.actor_mirror_service import (
    ActorMirrorService,
    SUPPORTED_ACTORS,
)
from src.snotes.application.actor_metadata_catalog import (
    ActorMetadataCatalogService,
)
from src.snotes.application.ai_access_metadata_service import (
    AiAccessMetadataService,
)
from src.snotes.application.ai_safe_read_service import AiSafeReadService
from src.snotes.application.directory_service import DirectoryService
from src.snotes.application.tag_service import TagService
from src.snotes.domain.actor_mirror import ActorMirrorError
from src.snotes.storage.actor_mirror import SQLiteActorMirrorWriter
from src.snotes.storage.connection import SQLiteConnectionFactory
from src.snotes.storage.repositories.directories import (
    SQLiteDirectoriesRepository,
)
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository


MAX_MIRROR_BYTES = 512 * 1024 * 1024
DEFAULT_SSH_HOST = "optimal"
DEFAULT_SSH_ACCOUNT = "snotes-publish"
DEFAULT_SSH_IDENTITY = Path.home() / ".ssh" / "snotes-greencloud-mirror"


class ActorMirrorPublishError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class PublishReceipt:
    actor: str
    content_hash: str
    note_count: int


@dataclass(frozen=True)
class ActorPublishResult:
    actor: str
    status: str
    content_hash: str = ""
    note_count: int = 0
    reason: str = "none"


class ActorMirrorTransport(Protocol):
    def publish(
        self,
        *,
        actor: str,
        mirror_path: Path,
        file_sha256: str,
        content_hash: str,
    ) -> PublishReceipt: ...


class SshActorMirrorTransport:
    """Narrow SSH client for a key whose server side has a forced command."""

    def __init__(
        self,
        *,
        host: str = DEFAULT_SSH_HOST,
        account: str = DEFAULT_SSH_ACCOUNT,
        identity_file: Path = DEFAULT_SSH_IDENTITY,
        timeout_seconds: int = 120,
    ) -> None:
        self.host = str(host)
        self.account = str(account)
        self.identity_file = Path(identity_file)
        self.timeout_seconds = int(timeout_seconds)

    def publish(
        self,
        *,
        actor: str,
        mirror_path: Path,
        file_sha256: str,
        content_hash: str,
    ) -> PublishReceipt:
        path = Path(mirror_path)
        size = path.stat().st_size
        if actor not in SUPPORTED_ACTORS:
            raise ActorMirrorPublishError("unknown_actor")
        if size < 1 or size > MAX_MIRROR_BYTES:
            raise ActorMirrorPublishError("mirror_size_invalid")
        if _sha256_file(path) != file_sha256:
            raise ActorMirrorPublishError("mirror_changed_before_upload")
        if not self.identity_file.is_file() or self.identity_file.is_symlink():
            raise ActorMirrorPublishError("ssh_identity_unavailable")
        identity_metadata = self.identity_file.stat()
        if identity_metadata.st_uid != os.geteuid():
            raise ActorMirrorPublishError("ssh_identity_owner_unsafe")
        if identity_metadata.st_mode & 0o077:
            raise ActorMirrorPublishError("ssh_identity_permissions_unsafe")

        header = json.dumps(
            {"actor": actor, "size": size, "sha256": file_sha256},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8") + b"\n"
        read_fd, write_fd = os.pipe()
        writer_error: list[BaseException] = []

        def stream_payload() -> None:
            try:
                with os.fdopen(write_fd, "wb", closefd=True) as output:
                    output.write(header)
                    with path.open("rb") as source:
                        while chunk := source.read(1024 * 1024):
                            output.write(chunk)
            except (BrokenPipeError, OSError) as error:
                writer_error.append(error)

        writer = threading.Thread(target=stream_payload, daemon=True)
        writer.start()
        command = [
            "/usr/bin/ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "RequestTTY=no",
            "-i",
            str(self.identity_file),
            f"{self.account}@{self.host}",
        ]
        try:
            with os.fdopen(read_fd, "rb", closefd=True) as protocol_input:
                completed = subprocess.run(
                    command,
                    stdin=protocol_input,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=self.timeout_seconds,
                    check=False,
                )
        except subprocess.TimeoutExpired as error:
            raise ActorMirrorPublishError("transport_timeout") from error
        except OSError as error:
            raise ActorMirrorPublishError("transport_unavailable") from error
        finally:
            writer.join(timeout=5)

        if completed.returncode != 0 or writer_error:
            raise ActorMirrorPublishError("publish_rejected")
        receipt = _parse_receipt(completed.stdout)
        if receipt.actor != actor or receipt.content_hash != content_hash:
            raise ActorMirrorPublishError("receipt_mismatch")
        return receipt


class ActorMirrorPublisher:
    def __init__(
        self,
        *,
        source_database: Path,
        redaction_rules: Path,
        mirror_directory: Path,
        state_path: Path,
        lock_path: Path,
        transport: ActorMirrorTransport,
    ) -> None:
        self.source_database = Path(source_database)
        self.redaction_rules = Path(redaction_rules)
        self.mirror_directory = Path(mirror_directory)
        self.state_path = Path(state_path)
        self.lock_path = Path(lock_path)
        self.transport = transport

    def publish(self) -> tuple[ActorPublishResult, ...]:
        return self._publish(SUPPORTED_ACTORS)

    def publish_actor(self, actor: str) -> ActorPublishResult:
        """Use the existing pipeline for the one actor whose proposal changed."""
        if actor not in SUPPORTED_ACTORS:
            raise ActorMirrorPublishError("unknown_actor")
        return self._publish((actor,))[0]

    def _publish(self, actors: tuple[str, ...]) -> tuple[ActorPublishResult, ...]:
        self._validate_paths()
        self.mirror_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.mirror_directory.is_symlink():
            raise ActorMirrorPublishError("mirror_directory_unsafe")
        if self.state_path.is_symlink() or self.lock_path.is_symlink():
            raise ActorMirrorPublishError("publisher_state_path_unsafe")
        lock_descriptor = os.open(
            self.lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ActorMirrorPublishError("publisher_busy") from error
            state = _load_state(self.state_path)
            service = self._compose_service()
            results: list[ActorPublishResult] = []
            for actor in actors:
                try:
                    result = self._publish_actor(actor, service, state)
                except Exception as error:
                    reason = _reason_for(error)
                    _record_failure(state, actor, reason)
                    result = ActorPublishResult(
                        actor=actor,
                        status="failed",
                        reason=reason,
                    )
                _write_state(self.state_path, state)
                results.append(result)
            return tuple(results)
        finally:
            os.close(lock_descriptor)

    def status(self) -> dict[str, object]:
        return _load_state(self.state_path)

    def _compose_service(self) -> ActorMirrorService:
        connections = SQLiteConnectionFactory(
            self.source_database,
            read_only=True,
        )
        directories = SQLiteDirectoriesRepository(connections)
        notes = SQLiteNotesRepository(connections, directories)
        metadata_catalog = ActorMetadataCatalogService(
            directories=DirectoryService(directories, notes),
            tags=TagService(SQLiteTagsRepository(connections)),
            ai_metadata=AiAccessMetadataService(notes),
        )
        safe_reads = AiSafeReadService(self.redaction_rules)
        return ActorMirrorService(
            notes,
            safe_reads,
            SQLiteActorMirrorWriter(),
            metadata_catalog,
        )

    def _publish_actor(
        self,
        actor: str,
        service: ActorMirrorService,
        state: dict[str, object],
    ) -> ActorPublishResult:
        mirror_path = self.mirror_directory / f"{actor}.db"
        generated = service.generate(actor, mirror_path)
        verified = service.verify(
            mirror_path,
            expected_actor=actor,
            expected_note_count=int(generated["note_count"]),
        )
        content_hash = str(verified["hash"])
        note_count = int(verified["note_count"])
        actor_state = _actor_state(state, actor)
        if actor_state.get("last_success_hash") == content_hash:
            actor_state["needs_retry"] = False
            return ActorPublishResult(
                actor=actor,
                status="unchanged",
                content_hash=content_hash,
                note_count=note_count,
            )

        receipt = self.transport.publish(
            actor=actor,
            mirror_path=mirror_path,
            file_sha256=_sha256_file(mirror_path),
            content_hash=content_hash,
        )
        if receipt.note_count != note_count:
            raise ActorMirrorPublishError("receipt_mismatch")
        now = _now()
        actor_state.update(
            {
                "last_success_hash": content_hash,
                "last_success_time": now,
                "needs_retry": False,
            }
        )
        return ActorPublishResult(
            actor=actor,
            status="published",
            content_hash=content_hash,
            note_count=note_count,
        )

    def _validate_paths(self) -> None:
        if (
            not self.source_database.is_file()
            or self.source_database.is_symlink()
        ):
            raise ActorMirrorPublishError("source_database_unavailable")
        if not self.redaction_rules.is_file() or self.redaction_rules.is_symlink():
            raise ActorMirrorPublishError("redaction_rules_unavailable")
        paths = {
            "source": self.source_database,
            "rules": self.redaction_rules,
            "state": self.state_path,
            "lock": self.lock_path,
            **{
                f"mirror:{actor}": self.mirror_directory / f"{actor}.db"
                for actor in SUPPORTED_ACTORS
            },
        }
        items = tuple(paths.items())
        for index, (left_name, left_path) in enumerate(items):
            for right_name, right_path in items[index + 1 :]:
                if _paths_alias(left_path, right_path):
                    if "source" in {left_name, right_name}:
                        raise ActorMirrorPublishError(
                            "source_output_same_file"
                        )
                    raise ActorMirrorPublishError(
                        "publisher_paths_overlap"
                    )


def _parse_receipt(raw: bytes) -> PublishReceipt:
    try:
        text = raw.decode("utf-8")
        lines = text.splitlines()
        if len(lines) != 1:
            raise ValueError
        payload = json.loads(lines[0])
        if set(payload) != {"status", "actor", "hash", "note_count"}:
            raise ValueError
        if payload["status"] != "published":
            raise ValueError
        actor = str(payload["actor"])
        content_hash = str(payload["hash"])
        note_count = int(payload["note_count"])
        if actor not in SUPPORTED_ACTORS or len(content_hash) != 64:
            raise ValueError
        if note_count < 0:
            raise ValueError
        return PublishReceipt(actor, content_hash, note_count)
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise ActorMirrorPublishError("invalid_receiver_response") from error


def _paths_alias(left: Path, right: Path) -> bool:
    left_path = Path(left)
    right_path = Path(right)
    if left_path.resolve() == right_path.resolve():
        return True
    if left_path.exists() and right_path.exists():
        try:
            return left_path.samefile(right_path)
        except OSError:
            return False
    return False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _empty_state() -> dict[str, object]:
    return {
        "schema_version": 1,
        "actors": {actor: {} for actor in SUPPORTED_ACTORS},
    }


def _load_state(path: Path) -> dict[str, object]:
    if not Path(path).exists():
        return _empty_state()
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            return _empty_state()
        actors = payload.get("actors")
        if not isinstance(actors, dict):
            return _empty_state()
        normalized = _empty_state()
        for actor in SUPPORTED_ACTORS:
            value = actors.get(actor)
            if isinstance(value, dict):
                normalized["actors"][actor] = {
                    key: value[key]
                    for key in (
                        "last_success_hash",
                        "last_success_time",
                        "last_failure_time",
                        "last_failure_reason",
                        "needs_retry",
                    )
                    if key in value
                }
        return normalized
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _empty_state()


def _actor_state(state: dict[str, object], actor: str) -> dict[str, object]:
    actors = state["actors"]
    assert isinstance(actors, dict)
    value = actors.setdefault(actor, {})
    assert isinstance(value, dict)
    return value


def _record_failure(
    state: dict[str, object], actor: str, reason: str
) -> None:
    _actor_state(state, actor).update(
        {
            "last_failure_time": _now(),
            "last_failure_reason": reason,
            "needs_retry": True,
        }
    )


def _write_state(path: Path, state: dict[str, object]) -> None:
    target = Path(path)
    payload = json.dumps(
        state,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ) + "\n"
    descriptor, name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
        directory_descriptor = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _reason_for(error: Exception) -> str:
    if isinstance(error, (ActorMirrorPublishError, ActorMirrorError)):
        return error.code
    return "unexpected_failure"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
