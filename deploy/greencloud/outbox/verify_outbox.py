"""Repeatable, content-free Step 10 GreenCloud acceptance.

Run the public mode as root on GreenCloud and give it a fresh fixture directory
created by ``tests.gc35_step05_cloud_fixture``.  The verifier never reads a
production Notes row and never prints, copies, or compares actor credentials.
Full proposal writes go only to a disposable outbox served by a transient
``snotes`` systemd unit.  Production checks use nonexistent request IDs or
requests rejected before any outbox write.
"""
from __future__ import annotations

import argparse
import asyncio
import errno
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import stat
import subprocess
import sys
import time
import uuid
from typing import NoReturn


RUNTIME_ROOT = Path("/srv/snotes/runtime")
PYTHON = RUNTIME_ROOT / ".venv/bin/python"
SCRIPT = Path(__file__).resolve()
RUNUSER = Path("/usr/sbin/runuser")
SYSTEMD_RUN = Path("/usr/bin/systemd-run")
PRODUCTION_DATABASE = Path("/srv/snotes/data/outbox.db")
PRODUCTION_BACKUPS = Path("/srv/snotes/backups/notes-outbox")
PRODUCTION_PORT = 8767
FIXTURE_PREFIX = "snotes-step10-fictional-"
ACTORS = {
    "claude-code": ("claude", 2001),
    "clawbot": ("openclaw", 2002),
}
EXPECTED_TOOLS = {
    "search_notes",
    "get_note",
    "get_note_markdown",
    "create_note",
    "prepend_note",
    "append_note",
    "update_note_metadata",
    "edit_note",
    "get_operation_status",
}
IDENTITY_FIELDS = {
    "actor",
    "source_actor",
    "actor_id",
    "actor_target_id",
    "identity",
    "principal",
    "role",
}


class VerificationError(RuntimeError):
    """A deliberately non-sensitive acceptance failure."""


def reject(reason: str) -> NoReturn:
    raise VerificationError(reason)


def require(condition: bool, reason: str) -> None:
    if not condition:
        reject(reason)


def _install_runtime_path() -> None:
    value = str(RUNTIME_ROOT)
    if value not in sys.path:
        sys.path.insert(0, value)


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def _raw_post(
    port: int,
    path: str,
    payload: dict,
    *,
    token: str | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict]:
    encoded = _json_bytes(payload)
    headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(encoded)),
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    connection = http.client.HTTPConnection("127.0.0.1", int(port), timeout=timeout)
    try:
        connection.request("POST", path, body=encoded, headers=headers)
        response = connection.getresponse()
        raw = response.read(256 * 1024 + 1)
    finally:
        connection.close()
    require(len(raw) <= 256 * 1024, "http_response_too_large")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        reject("http_response_invalid")
    require(isinstance(body, dict), "http_response_invalid")
    return response.status, body


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fixture_manifest(root: Path, *, verify_hashes: bool = True) -> dict:
    root = root.absolute()
    require(
        root.parent == Path("/tmp") and root.name.startswith(FIXTURE_PREFIX),
        "disposable_fixture_required",
    )
    require(not root.is_symlink() and root.is_dir(), "fixture_directory_invalid")
    require(root.stat().st_uid == 0 and root.stat().st_gid == 0,
            "root_owned_fixture_required")
    version_root = root / "v1"
    require(
        not version_root.is_symlink()
        and version_root.is_dir()
        and version_root.stat().st_uid == 0
        and version_root.stat().st_gid == 0,
        "root_owned_fixture_required",
    )
    manifest_path = root / "manifest.json"
    require(
        not manifest_path.is_symlink()
        and stat.S_ISREG(manifest_path.lstat().st_mode),
        "fixture_manifest_invalid",
    )
    require(manifest_path.stat().st_uid == 0 and manifest_path.stat().st_gid == 0,
            "root_owned_fixture_required")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        reject("fixture_manifest_invalid")
    require(isinstance(manifest, dict), "fixture_manifest_invalid")
    require(
        manifest.get("fixture") == "gc35-step05-fictional-v1-v2"
        and manifest.get("source_database_uploaded") is False,
        "fictional_fixture_required",
    )
    versions = manifest.get("versions")
    require(isinstance(versions, dict) and isinstance(versions.get("v1"), dict),
            "fixture_manifest_invalid")
    for actor in ACTORS:
        record = versions["v1"].get(actor)
        require(isinstance(record, dict), "fixture_manifest_invalid")
        relative = record.get("relative_path")
        require(relative == f"v1/{actor}.db", "fixture_manifest_invalid")
        path = root / relative
        require(
            not path.is_symlink() and stat.S_ISREG(path.lstat().st_mode),
            "fixture_mirror_invalid",
        )
        if os.geteuid() == 0:
            require(path.stat().st_uid == 0 and path.stat().st_gid == 0,
                    "root_owned_fixture_required")
        if verify_hashes:
            require(_sha256(path) == record.get("file_sha256"), "fixture_hash_mismatch")
    ids = manifest.get("ids")
    expectations = manifest.get("expectations")
    require(isinstance(ids, dict) and isinstance(expectations, dict),
            "fixture_manifest_invalid")
    expected_versions = expectations.get("versions")
    require(isinstance(expected_versions, dict) and isinstance(expected_versions.get("v1"), dict),
            "fixture_manifest_invalid")
    return manifest


def _prepare_fixture(root: Path) -> tuple[Path, dict[str, str]]:
    manifest = _fixture_manifest(root)
    require(os.geteuid() == 0, "root_required")
    snotes = pwd.getpwnam("snotes")
    require((snotes.pw_uid, snotes.pw_gid) == (2006, 2006),
            "snotes_identity_invalid")
    root.chmod(0o711)
    (root / "v1").chmod(0o711)
    # The manifest contains fictional IDs and hashes only. Actor subprocesses
    # need those IDs but still cannot open the other actor's 0640 mirror.
    (root / "manifest.json").chmod(0o644)
    hashes: dict[str, str] = {}
    for actor, (_user, uid) in ACTORS.items():
        mirror = root / "v1" / f"{actor}.db"
        hashes[actor] = _sha256(mirror)
        os.chown(mirror, 2006, uid)
        mirror.chmod(0o640)
    data = root / "data"
    require(not data.exists() and not data.is_symlink(), "fixture_data_not_fresh")
    data.mkdir(mode=0o700)
    os.chown(data, 2006, 2006)
    return data / "outbox.db", hashes


def _serve_fixture(root: Path, database: Path, port: int) -> int:
    require(os.geteuid() == 2006, "snotes_service_identity_required")
    require(1024 <= port <= 65535 and port != PRODUCTION_PORT,
            "fixture_port_invalid")
    manifest = _fixture_manifest(root, verify_hashes=False)
    del manifest
    require(database == root / "data/outbox.db", "fixture_database_invalid")
    _install_runtime_path()
    from src.cloud_outbox import initialize_service_database, load_actor_credentials
    from src.snotes.storage.cloud_read import SQLiteCloudMirrorReader
    from src.snotes.transport.cloud_outbox_http import (
        CloudOutboxHttpApplication,
        serve_cloud_outbox,
    )

    initialize_service_database(database)
    application = CloudOutboxHttpApplication(
        database,
        load_actor_credentials(),
        reader_factory=lambda actor: SQLiteCloudMirrorReader(
            root / "v1" / f"{actor}.db"
        ),
    )
    serve_cloud_outbox(application, host="127.0.0.1", port=port)
    return 0


def _actor_context(root: Path, port: int):
    require(1024 <= port <= 65535 and port != PRODUCTION_PORT,
            "fixture_port_invalid")
    _install_runtime_path()
    from src.cloud_read_mcp import binding_for_effective_uid, read_actor_credential
    from src.snotes.application.cloud_proposal_service import CloudProposalService
    from src.snotes.application.cloud_read_service import CloudReadService
    from src.snotes.storage.cloud_read import SQLiteCloudMirrorReader
    from src.snotes.transport.mcp.cloud_read_server import call_cloud_read_tool

    binding = binding_for_effective_uid(os.geteuid())
    expected = ACTORS.get(binding.actor)
    require(expected is not None and expected[1] == os.geteuid(), "actor_identity_invalid")
    credential = lambda: read_actor_credential(binding)

    class FixtureHttpGateway:
        def get_operation_status(self, request_id: str) -> dict | None:
            status, body = _raw_post(
                port,
                "/v1/notes/proposals/status",
                {"request_id": request_id},
                token=credential(),
            )
            require(status == 200, "fixture_status_http_failed")
            return None if body.get("status") == "not_found" else body

        def enqueue_proposal(
            self,
            *,
            request_id: str,
            operation_type: str,
            note_id: int | None,
            expected_version: str | None,
            payload: dict,
        ) -> dict:
            status, body = _raw_post(
                port,
                "/v1/notes/proposals/enqueue",
                {
                    "request_id": request_id,
                    "operation_type": operation_type,
                    "note_id": note_id,
                    "expected_version": expected_version,
                    "payload": payload,
                },
                token=credential(),
            )
            if status == 429 and body.get("error") == "queue_full":
                raise RuntimeError("queue_full")
            require(status == 200, "fixture_enqueue_http_failed")
            return body

    reader = SQLiteCloudMirrorReader(root / "v1" / f"{binding.actor}.db")
    read_service = CloudReadService(reader)
    proposal_service = CloudProposalService(reader, FixtureHttpGateway())

    def call(name: str, arguments: dict) -> dict:
        result = call_cloud_read_tool(
            read_service,
            name,
            arguments,
            proposal_service=proposal_service,
        )
        require(isinstance(result, dict), "mcp_result_invalid")
        return result

    return binding, credential, reader, call


def _changed_version(value: str) -> str:
    require(value.startswith("snotes-v1-") and len(value) == 74,
            "fixture_version_invalid")
    return value[:-1] + ("0" if value[-1] != "0" else "1")


def _actor_fixture_baseline(root: Path, port: int) -> int:
    manifest = _fixture_manifest(root, verify_hashes=False)
    binding, credential, reader, call = _actor_context(root, port)
    ids = manifest["ids"]
    own_key = "claude_only" if binding.actor == "claude-code" else "clawbot_only"
    other_key = "clawbot_only" if binding.actor == "claude-code" else "claude_only"
    common = reader.get(int(ids["common_full"]))
    redacted = reader.get(int(ids["redacted"]))
    own_markdown = reader.get_markdown(int(ids[own_key]))
    require(common is not None and redacted is not None and own_markdown is not None,
            "fixture_actor_mirror_invalid")
    prefix = "s10-" + binding.actor
    create = call(
        "create_note",
        {
            "request_id": prefix + "-create",
            "title": "Fictional offline proposal",
            "content": "# Fictional proposal\n\nQueued while no Mac receiver is present.",
            "tags": ["fictional", "step10"],
        },
    )
    append = call(
        "append_note",
        {
            "request_id": prefix + "-append",
            "note_id": int(ids["common_full"]),
            "content": "\n\nFictional append proposal.",
            "expected_version": common["version"],
        },
    )
    redacted_append = call(
        "append_note",
        {
            "request_id": prefix + "-append-redacted",
            "note_id": int(ids["redacted"]),
            "content": "\n\nFictional safe append.",
            "expected_version": redacted["version"],
        },
    )
    markdown = str(own_markdown["markdown"])
    first_line = markdown.splitlines()[0]
    edit = call(
        "edit_note",
        {
            "request_id": prefix + "-edit",
            "note_id": int(ids[own_key]),
            "expected_version": own_markdown["version"],
            "edits": [
                {
                    "old_markdown": first_line,
                    "new_markdown": first_line + " proposed",
                }
            ],
        },
    )
    for result in (create, append, redacted_append, edit):
        require(result.get("status") == "queued", "positive_proposal_not_queued")
        require(result.get("duplicate") is False, "new_proposal_marked_duplicate")
    duplicate = call(
        "create_note",
        {
            "request_id": prefix + "-create",
            "title": "Different duplicate body",
            "content": "Different duplicate body.",
        },
    )
    require(duplicate.get("status") == "queued" and duplicate.get("duplicate") is True,
            "duplicate_not_idempotent")
    status = call("get_operation_status", {"request_id": prefix + "-edit"})
    require(status.get("status") == "queued" and status.get("operation_type") == "edit",
            "own_status_unavailable")

    stale = call(
        "append_note",
        {
            "request_id": prefix + "-stale",
            "note_id": int(ids["common_full"]),
            "content": "Stale fictional append.",
            "expected_version": _changed_version(str(common["version"])),
        },
    )
    require(stale.get("status") == "conflict", "stale_version_not_rejected")
    for key in ("hidden", other_key, "unauthorized_gale_only"):
        result = call(
            "append_note",
            {
                "request_id": prefix + "-not-found-" + key,
                "note_id": int(ids[key]),
                "content": "Must not queue.",
                "expected_version": common["version"],
            },
        )
        require(result.get("status") == "not_found", "invisible_note_not_hidden")
    redacted_edit = call(
        "edit_note",
        {
            "request_id": prefix + "-redacted-edit",
            "note_id": int(ids["redacted"]),
            "expected_version": redacted["version"],
            "edits": [{"old_markdown": "Fictional", "new_markdown": "Changed"}],
        },
    )
    require(redacted_edit.get("status") in {"not_found", "rejected"},
            "redacted_edit_not_rejected")
    dialect = call(
        "create_note",
        {
            "request_id": prefix + "-bad-dialect",
            "title": "Invalid dialect",
            "content": ':size[bad]{size="99"}',
        },
    )
    require(dialect.get("status") == "rejected", "invalid_dialect_not_rejected")
    for name in ("delete_note",):
        result = call(name, {"request_id": prefix + "-forbidden-" + name})
        require(result.get("status") == "rejected", "forbidden_operation_available")

    token = credential()
    spoof_status, spoof = _raw_post(
        port,
        "/v1/notes/proposals/status",
        {"request_id": prefix + "-create", "source_actor": "clawbot"},
        token=token,
    )
    require(spoof_status == 400 and spoof.get("error") == "identity_in_body_forbidden",
            "identity_spoof_not_rejected")
    nested_status, nested = _raw_post(
        port,
        "/v1/notes/proposals/enqueue",
        {
            "request_id": prefix + "-nested-spoof",
            "operation_type": "create",
            "note_id": None,
            "expected_version": None,
            "payload": {
                "title": "Spoof",
                "content": "Must not queue.",
                "directory_id": None,
                "main_category": None,
                "sub_category": None,
                "tags": [],
                "source_actor": "clawbot",
            },
        },
        token=token,
    )
    require(nested_status == 400 and nested.get("error") == "identity_in_body_forbidden",
            "nested_identity_spoof_not_rejected")
    print("STEP10_FIXTURE_BASELINE=" + binding.actor + ":PASS")
    return 0


def _actor_fixture_cross(root: Path, port: int) -> int:
    binding, _credential, _reader, call = _actor_context(root, port)
    other = "clawbot" if binding.actor == "claude-code" else "claude-code"
    result = call("get_operation_status", {"request_id": f"s10-{other}-create"})
    require(result.get("status") == "not_found", "cross_actor_status_visible")
    print("STEP10_FIXTURE_CROSS_STATUS=" + binding.actor + ":PASS")
    return 0


def _actor_fixture_queue(root: Path, port: int) -> int:
    binding, credential, _reader, call = _actor_context(root, port)
    require(binding.actor == "claude-code", "queue_probe_actor_invalid")
    prefix = "s10-claude-code"
    # Baseline enqueued four proposals. Fill exactly to the 50-item actor cap.
    for index in range(46):
        result = call(
            "create_note",
            {
                "request_id": f"{prefix}-queue-{index:02d}",
                "title": "Fictional queue item",
                "content": f"Fictional queue payload {index}.",
            },
        )
        require(result.get("status") == "queued", "queue_fill_failed")
    # Deliberately invalid fields prove idempotency is checked before validation
    # and before the full-queue decision.
    duplicate_status, duplicate = _raw_post(
        port,
        "/v1/notes/proposals/enqueue",
        {
            "request_id": prefix + "-create",
            "operation_type": "create",
            "note_id": None,
            "expected_version": None,
            "payload": {
                "title": "",
                "content": "",
                "directory_id": None,
                "main_category": None,
                "sub_category": None,
                "tags": [],
            },
        },
        token=credential(),
    )
    require(duplicate_status == 200 and duplicate.get("status") == "queued"
            and duplicate.get("duplicate") is True,
            "duplicate_after_queue_cap_failed")
    full = call(
        "create_note",
        {
            "request_id": prefix + "-queue-overflow",
            "title": "Fictional overflow",
            "content": "Must not enter a full queue.",
        },
    )
    require(full.get("status") == "queue_full", "queue_limit_not_enforced")
    print("STEP10_FIXTURE_QUEUE_ORDER=claude-code:PASS")
    return 0


def _actor_production() -> int:
    _install_runtime_path()
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from src.cloud_read_mcp import binding_for_effective_uid, read_actor_credential
    from src.snotes.transport.cloud_proposal_client import CloudProposalClient

    binding = binding_for_effective_uid(os.geteuid())
    token_provider = lambda: read_actor_credential(binding)
    client = CloudProposalClient(token_provider)
    marker = "step10-smoke-nonexistent-" + uuid.uuid4().hex
    try:
        require(client.get_operation_status(marker) is None,
                "production_nonexistent_status_not_hidden")
    finally:
        client.close()
    token = token_provider()
    forbidden_status, forbidden = _raw_post(
        PRODUCTION_PORT,
        "/v1/notes/proposals/enqueue",
        {
            "request_id": marker + "-delete",
            "operation_type": "delete",
            "note_id": 9_223_372_036_854_775_807,
            "expected_version": "snotes-v1-" + "0" * 64,
            "payload": {"content": "Must not queue."},
        },
        token=token,
    )
    require(forbidden_status == 403 and forbidden.get("error") == "operation_not_allowed",
            "production_forbidden_operation_not_rejected")
    spoof_status, spoof = _raw_post(
        PRODUCTION_PORT,
        "/v1/notes/proposals/status",
        {"request_id": marker, "source_actor": "clawbot"},
        token=token,
    )
    require(spoof_status == 400 and spoof.get("error") == "identity_in_body_forbidden",
            "production_identity_spoof_not_rejected")

    async def probe_installed_mcp() -> None:
        parameters = StdioServerParameters(
            command="/usr/local/bin/snotes-cloud-read",
            args=[],
            cwd=str(RUNTIME_ROOT),
            env={"PATH": "/usr/bin:/bin"},
        )
        with open(os.devnull, "w", encoding="utf-8") as error_log:
            async with stdio_client(parameters, errlog=error_log) as streams:
                async with ClientSession(*streams) as session:
                    initialized = await session.initialize()
                    require(
                        initialized.serverInfo.name == "snotes-cloud-read",
                        "cloud_mcp_server_identity_invalid",
                    )
                    tools = {tool.name for tool in (await session.list_tools()).tools}
                    require(tools == EXPECTED_TOOLS, "cloud_mcp_tool_contract_invalid")
                    response = await session.call_tool(
                        "get_operation_status", {"request_id": marker + "-mcp"}
                    )
                    require(not response.isError, "cloud_mcp_status_call_failed")
                    result = response.structuredContent
                    if not isinstance(result, dict):
                        for item in response.content:
                            text = getattr(item, "text", None)
                            if not isinstance(text, str) or len(text) > 64 * 1024:
                                continue
                            try:
                                candidate = json.loads(text)
                            except json.JSONDecodeError:
                                continue
                            if isinstance(candidate, dict):
                                result = candidate
                                break
                    require(
                        isinstance(result, dict)
                        and result.get("status") == "not_found"
                        and result.get("request_id") == marker + "-mcp",
                        "cloud_mcp_status_contract_invalid",
                    )

    asyncio.run(asyncio.wait_for(probe_installed_mcp(), timeout=20.0))
    print("STEP10_PRODUCTION_ACTOR=" + binding.actor + ":PASS")
    return 0


def _fs_probe_child() -> int:
    require(os.geteuid() in {2001, 2002}, "actor_identity_required")
    try:
        payload = json.loads(sys.stdin.buffer.read(128 * 1024))
    except (UnicodeDecodeError, json.JSONDecodeError):
        reject("filesystem_probe_invalid")
    require(isinstance(payload, dict) and isinstance(payload.get("paths"), list),
            "filesystem_probe_invalid")
    checks = 0
    for value in payload["paths"]:
        require(isinstance(value, str), "filesystem_probe_invalid")
        path = Path(value)
        require(
            path.is_absolute()
            and (
                str(path).startswith("/srv/snotes/")
                or (
                    path.parent != Path("/tmp")
                    and f"/tmp/{FIXTURE_PREFIX}" in str(path)
                )
            ),
            "filesystem_probe_path_invalid",
        )
        for flags in (os.O_RDONLY, os.O_WRONLY):
            try:
                descriptor = os.open(
                    path,
                    flags
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_NONBLOCK", 0),
                )
            except OSError as error:
                require(error.errno == errno.EACCES, "outbox_access_not_eacces")
            else:
                os.close(descriptor)  # Never read or write a database byte.
                reject("ai_uid_can_open_outbox_file")
            checks += 1
    print("STEP10_OUTBOX_FILE_DENIAL=" + str(checks) + ":PASS")
    return 0


def _run_actor(
    actor: str,
    arguments: list[str],
    *,
    stdin: bytes = b"",
    timeout: int = 45,
) -> str:
    user, uid = ACTORS[actor]
    account = pwd.getpwnam(user)
    require((account.pw_uid, account.pw_gid) == (uid, uid), "actor_identity_invalid")
    completed = subprocess.run(
        [str(RUNUSER), "-u", user, "--", str(PYTHON), "-I", "-B", str(SCRIPT), *arguments],
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    require(completed.returncode == 0 and len(completed.stdout) <= 64 * 1024,
            "actor_subprocess_failed")
    output = completed.stdout.decode("utf-8", "strict")
    allowed = (
        "STEP10_PRODUCTION_ACTOR=",
        "STEP10_FIXTURE_BASELINE=",
        "STEP10_FIXTURE_CROSS_STATUS=",
        "STEP10_FIXTURE_QUEUE_ORDER=",
        "STEP10_OUTBOX_FILE_DENIAL=",
    )
    lines = [line for line in output.splitlines() if line]
    require(
        1 <= len(lines) <= 4
        and all(
            len(line) <= 160
            and line.startswith(allowed)
            and line.endswith(":PASS")
            for line in lines
        ),
        "actor_subprocess_output_invalid",
    )
    for line in lines:
        print(line)
    return output


def _database_paths(root: Path | None = None) -> list[Path]:
    paths = [
        PRODUCTION_DATABASE,
        Path(str(PRODUCTION_DATABASE) + "-wal"),
        Path(str(PRODUCTION_DATABASE) + "-shm"),
        PRODUCTION_BACKUPS / "outbox-latest.db",
    ]
    if PRODUCTION_BACKUPS.is_dir() and not PRODUCTION_BACKUPS.is_symlink():
        paths.extend(
            path
            for path in sorted(PRODUCTION_BACKUPS.iterdir())
            if path.name.endswith(".db") and path not in paths
        )
    if root is not None:
        database = root / "data/outbox.db"
        paths.extend(
            [database, Path(str(database) + "-wal"), Path(str(database) + "-shm")]
        )
    return paths


def _root_metadata_checks() -> None:
    require(os.getuid() == 0 and os.geteuid() == 0, "root_required")
    require(PYTHON.is_file() and RUNUSER.is_file() and SYSTEMD_RUN.is_file(),
            "runtime_dependency_missing")
    metadata = PRODUCTION_DATABASE.lstat()
    require(
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == 2006
        and metadata.st_gid == 2006
        and stat.S_IMODE(metadata.st_mode) == 0o600,
        "production_outbox_permissions_invalid",
    )
    data = PRODUCTION_DATABASE.parent.stat()
    require((data.st_uid, data.st_gid, stat.S_IMODE(data.st_mode)) == (2006, 2006, 0o700),
            "production_data_directory_invalid")
    backup = PRODUCTION_BACKUPS.stat()
    require((backup.st_uid, backup.st_gid, stat.S_IMODE(backup.st_mode)) == (2006, 2006, 0o700),
            "production_backup_directory_invalid")
    for path in _database_paths():
        if path.exists():
            item = path.lstat()
            require(
                stat.S_ISREG(item.st_mode)
                and item.st_uid == 2006
                and item.st_gid == 2006
                and stat.S_IMODE(item.st_mode) == 0o600,
                "production_outbox_file_permissions_invalid",
            )


def _allocate_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = int(listener.getsockname()[1])
    require(port != PRODUCTION_PORT and port >= 1024, "fixture_port_invalid")
    return port


def _start_fixture_unit(root: Path, database: Path, port: int) -> str:
    unit = "snotes-step10-fictional-" + uuid.uuid4().hex[:12]
    command = [
        str(SYSTEMD_RUN),
        "--unit=" + unit,
        "--collect",
        "--quiet",
        "--service-type=exec",
        "--uid=snotes",
        "--gid=snotes",
        "--property=UMask=0077",
        "--property=NoNewPrivileges=yes",
        "--property=PrivateTmp=no",
        "--property=ProtectSystem=strict",
        "--property=ProtectHome=yes",
        "--property=ReadWritePaths=" + str(root / "data"),
        "--property=ReadOnlyPaths=" + str(root / "v1"),
        "--property=LoadCredential=reminder-api.json:/root/greencloud-secrets/reminder-api.json",
        "--property=RestrictAddressFamilies=AF_UNIX AF_INET",
        "--setenv=PYTHONDONTWRITEBYTECODE=1",
        str(PYTHON),
        "-I",
        "-B",
        str(SCRIPT),
        "--serve-fixture",
        "--fixture-root",
        str(root),
        "--database",
        str(database),
        "--port",
        str(port),
    ]
    completed = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=15,
    )
    require(completed.returncode == 0, "fixture_service_start_failed")
    try:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            try:
                status, body = _raw_post(
                    port,
                    "/v1/notes/proposals/status",
                    {"request_id": "fixture-ready"},
                    timeout=0.5,
                )
                if status == 401 and body.get("error") == "credential_required":
                    return unit
            except (OSError, VerificationError):
                pass
            time.sleep(0.1)
        reject("fixture_service_not_ready")
    except Exception:
        _stop_fixture_unit(unit)
        raise


def _stop_fixture_unit(unit: str) -> None:
    subprocess.run(
        ["/usr/bin/systemctl", "stop", unit],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=15,
    )
    inactive = subprocess.run(
        ["/usr/bin/systemctl", "is-active", "--quiet", unit],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=5,
    )
    require(inactive.returncode != 0, "fixture_service_stop_failed")


def _production_transport_fake_ids() -> None:
    entry = Path("/usr/local/sbin/snotes-mirror-ssh-entry")
    require(entry.is_file(), "outbox_forced_command_missing")
    requests = (
        (
            "notes-outbox-acknowledge",
            {
                "submission_uuid": "00000000-0000-4000-8000-000000000000",
                "local_receipt_id": "step10-fake-receipt",
            },
        ),
        (
            "notes-outbox-receipt",
            {
                "source_actor": "claude-code",
                "request_id": "step10-fake-missing-request",
                "terminal_state": "rejected",
                "receipt_payload": {},
            },
        ),
    )
    for command, request in requests:
        completed = subprocess.run(
            [
                str(RUNUSER),
                "-u",
                "snotes-publish",
                "--",
                "/usr/bin/env",
                "-i",
                "PATH=/usr/sbin:/usr/bin:/sbin:/bin",
                "SSH_ORIGINAL_COMMAND=" + command,
                str(entry),
            ],
            input=_json_bytes(request),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        )
        require(completed.returncode != 0 and len(completed.stdout) <= 16 * 1024,
                "production_fake_transport_unexpected_success")
        try:
            response = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            reject("production_fake_transport_invalid_response")
        require(response.get("status") == "failed", "production_fake_transport_invalid_response")


def _isolated_lifecycle(database: Path) -> None:
    _install_runtime_path()
    from src.services.gale_submission_outbox import (
        acknowledge_submission,
        export_unacknowledged,
        get_operation_status,
        record_terminal_receipt,
    )

    exported = export_unacknowledged(database, limit=100)
    require(len(exported) == 54, "fixture_export_count_invalid")
    require({item.source_actor for item in exported} == set(ACTORS),
            "fixture_source_actor_invalid")
    target = next(
        item
        for item in exported
        if item.source_actor == "clawbot"
        and item.idempotency_key == "s10-clawbot-create"
    )
    acknowledged, duplicate = acknowledge_submission(
        database,
        submission_uuid=target.submission_uuid,
        local_receipt_id="step10-fictional-local-receipt",
    )
    require(acknowledged.delivery_state == "acknowledged" and duplicate is False,
            "fixture_acknowledge_failed")
    terminal, duplicate = record_terminal_receipt(
        database,
        source_actor="clawbot",
        request_id="s10-clawbot-create",
        terminal_state="accepted",
        receipt_payload={"fictional": True},
    )
    require(terminal.delivery_state == "accepted" and duplicate is False,
            "fixture_terminal_receipt_failed")
    repeated, duplicate = record_terminal_receipt(
        database,
        source_actor="clawbot",
        request_id="s10-clawbot-create",
        terminal_state="accepted",
        receipt_payload={"fictional": True},
    )
    require(repeated.delivery_state == "accepted" and duplicate is True,
            "fixture_terminal_receipt_not_idempotent")
    visible = get_operation_status(
        database, source_actor="clawbot", request_id="s10-clawbot-create"
    )
    hidden = get_operation_status(
        database, source_actor="claude-code", request_id="s10-clawbot-create"
    )
    require(visible is not None and visible.delivery_state == "accepted" and hidden is None,
            "fixture_terminal_status_scope_invalid")


def verify(root: Path) -> int:
    unit = ""
    cleanup_fixture = False
    try:
        _fixture_manifest(root)
        cleanup_fixture = True
        _root_metadata_checks()
        database, initial_hashes = _prepare_fixture(root)
        status, body = _raw_post(
            PRODUCTION_PORT,
            "/v1/notes/proposals/status",
            {"request_id": "step10-unauthorized-nonexistent"},
        )
        require(status == 401 and body.get("error") == "credential_required",
                "production_unauthorized_request_not_rejected")
        for actor in ACTORS:
            _run_actor(actor, ["--actor-production"])
        _production_transport_fake_ids()

        port = _allocate_port()
        unit = _start_fixture_unit(root, database, port)
        for actor in ACTORS:
            _run_actor(
                actor,
                ["--actor-fixture", "--fixture-root", str(root), "--port", str(port),
                 "--phase", "baseline"],
            )
        for actor in ACTORS:
            _run_actor(
                actor,
                ["--actor-fixture", "--fixture-root", str(root), "--port", str(port),
                 "--phase", "cross"],
            )
        _run_actor(
            "claude-code",
            ["--actor-fixture", "--fixture-root", str(root), "--port", str(port),
             "--phase", "queue"],
            timeout=90,
        )
        probe = _json_bytes({"paths": [str(path) for path in _database_paths(root)]})
        for actor in ACTORS:
            _run_actor(actor, ["--fs-probe"], stdin=probe)
        for actor, expected_hash in initial_hashes.items():
            require(_sha256(root / "v1" / f"{actor}.db") == expected_hash,
                    "fictional_mirror_changed")
        _stop_fixture_unit(unit)
        unit = ""
        _isolated_lifecycle(database)
        print("STEP10_PRODUCTION_SAFE_SMOKE=PASS")
        print("STEP10_FIXTIONAL_ACCEPTANCE=PASS")
        print("STEP10_ORACLE_PRODUCTION_TOUCHED=NO")
        return 0
    finally:
        if unit:
            _stop_fixture_unit(unit)
        if cleanup_fixture:
            shutil.rmtree(root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--port", type=int)
    parser.add_argument("--phase", choices=("baseline", "cross", "queue"))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--serve-fixture", action="store_true", help=argparse.SUPPRESS)
    modes.add_argument("--actor-fixture", action="store_true", help=argparse.SUPPRESS)
    modes.add_argument("--actor-production", action="store_true", help=argparse.SUPPRESS)
    modes.add_argument("--fs-probe", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    if arguments.serve_fixture:
        require(arguments.fixture_root is not None and arguments.database is not None
                and arguments.port is not None, "fixture_arguments_required")
        return _serve_fixture(
            arguments.fixture_root.absolute(), arguments.database.absolute(), arguments.port
        )
    if arguments.actor_fixture:
        require(arguments.fixture_root is not None and arguments.port is not None
                and arguments.phase is not None, "fixture_arguments_required")
        root = arguments.fixture_root.absolute()
        if arguments.phase == "baseline":
            return _actor_fixture_baseline(root, arguments.port)
        if arguments.phase == "cross":
            return _actor_fixture_cross(root, arguments.port)
        return _actor_fixture_queue(root, arguments.port)
    if arguments.actor_production:
        return _actor_production()
    if arguments.fs_probe:
        return _fs_probe_child()
    require(arguments.fixture_root is not None, "fixture_root_required")
    return verify(arguments.fixture_root.absolute())


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as error:
        print("STEP10_OUTBOX_VERIFICATION=FAIL:" + str(error))
        raise SystemExit(2)
    except Exception:
        print("STEP10_OUTBOX_VERIFICATION=FAIL:unexpected_error")
        raise SystemExit(2)
