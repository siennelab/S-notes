"""Repeatable, content-free Step 9 actor isolation verification.

Run this file as root with Python isolated mode.  Root only inventories file
metadata; every access decision is exercised by a process running as the AI
user under test.  Probes never read a database byte or another actor's token.
"""
from __future__ import annotations

import errno
import fnmatch
import grp
import http.client
import json
import os
from pathlib import Path
import pwd
import select
import stat
import subprocess
import sys
from typing import NoReturn


MAX_ID = 9_223_372_036_854_775_807
SCRIPT = Path(__file__).resolve()
RUNUSER = "/usr/sbin/runuser"
PYTHON = "/srv/snotes/runtime/.venv/bin/python"
NOTES_RUNTIME = Path("/srv/snotes/runtime")
REMINDER_RUNTIME = Path("/srv/snotes/reminder-runtime")
NOTES_WRAPPER = Path("/usr/local/bin/snotes-cloud-read")
REMINDER_WRAPPER = Path("/usr/local/bin/snotes-reminder-actor")
REMINDER_ENDPOINT = ("127.0.0.1", 8766)
OUTBOX_PATH = Path("/srv/snotes/data/outbox.db")
EXPECTED_NOTE_TOOLS = {"search_notes", "get_note", "get_note_markdown"}
EXPECTED_REMINDER_ACTOR_COMMANDS = {
    "list_reminder_labels",
    "list_reminder_tables",
    "get_reminder_table",
    "list_reminder_items",
    "get_reminder_item",
    "create_reminder_label",
    "create_reminder_table",
    "update_reminder_table",
    "create_reminder_item",
    "update_reminder_item",
    "complete_reminder_item",
    "pause_reminder_item",
    "resume_reminder_item",
}
UNKNOWN_NOTE_TOOLS = ("delete_note", "edit_ai_visibility", "set_actor_targets")

ACTORS = {
    "claude-code": {
        "user": "claude",
        "uid": 2001,
        "mirror": Path("/srv/snotes/mirror/claude-code/current.db"),
        "credential": Path(
            "/srv/snotes/actor-credentials/claude-code/reminder-token"
        ),
    },
    "clawbot": {
        "user": "openclaw",
        "uid": 2002,
        "mirror": Path("/srv/snotes/mirror/clawbot/current.db"),
        "credential": Path(
            "/srv/snotes/actor-credentials/clawbot/reminder-token"
        ),
    },
}


class VerificationError(Exception):
    """A deliberately non-sensitive verification failure."""


def reject(reason: str) -> NoReturn:
    raise VerificationError(reason)


def require(condition: bool, reason: str) -> None:
    if not condition:
        reject(reason)


def _json_line(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"


def _safe_json(raw: bytes, reason: str) -> object:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        reject(reason)


def _run_as_actor(
    actor: dict[str, object], argv: list[str], *, stdin: bytes = b"", timeout: int = 20
) -> bytes:
    completed = subprocess.run(
        [RUNUSER, "-u", str(actor["user"]), "--", *argv],
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
        env={"PATH": "/usr/bin:/bin"},
    )
    if completed.returncode != 0 or len(completed.stdout) > 64 * 1024:
        reject("actor_subprocess_failed")
    return completed.stdout


def _is_database_name(name: str) -> bool:
    return any(
        fnmatch.fnmatchcase(name, pattern)
        for pattern in ("*.db", "*.db-wal", "*.db-shm", "*.sqlite*")
    )


def _database_inventory() -> list[Path]:
    paths: list[Path] = []
    def walk_error(_error: OSError) -> NoReturn:
        reject("database_inventory_failed")

    for directory, child_dirs, filenames in os.walk(
        "/srv/snotes", followlinks=False, onerror=walk_error
    ):
        for child in list(child_dirs):
            candidate = Path(directory, child)
            if candidate == NOTES_RUNTIME / ".venv":
                child_dirs.remove(child)
            elif candidate.is_symlink():
                reject("database_directory_symlink")
        for filename in filenames:
            if not _is_database_name(filename):
                continue
            candidate = Path(directory, filename)
            metadata = candidate.lstat()
            require(stat.S_ISREG(metadata.st_mode), "database_not_regular")
            paths.append(candidate)
    return sorted(paths)


def _root_metadata_checks() -> None:
    require(os.geteuid() == 0 and os.getuid() == 0, "root_required")
    require(sys.flags.isolated == 1, "python_isolated_mode_required")
    require(NOTES_RUNTIME.is_dir(), "notes_runtime_missing")
    require(REMINDER_RUNTIME.is_dir(), "reminder_runtime_missing")
    require(Path(RUNUSER).is_file(), "runuser_missing")
    require(Path(PYTHON).is_file(), "runtime_python_missing")

    script_metadata = SCRIPT.lstat()
    require(stat.S_ISREG(script_metadata.st_mode), "verifier_not_regular")
    require(script_metadata.st_uid == 0, "verifier_not_root_owned")
    script_mode = stat.S_IMODE(script_metadata.st_mode)
    require(script_mode & 0o022 == 0, "verifier_writable_by_non_root")
    require(script_mode & 0o004 == 0o004, "verifier_not_actor_readable")

    for wrapper in (NOTES_WRAPPER, REMINDER_WRAPPER):
        metadata = wrapper.lstat()
        require(stat.S_ISREG(metadata.st_mode), "wrapper_not_regular")
        require(metadata.st_uid == 0, "wrapper_not_root_owned")
        mode = stat.S_IMODE(metadata.st_mode)
        require(mode & 0o022 == 0, "wrapper_writable_by_non_root")
        require(mode & 0o005 == 0o005, "wrapper_not_actor_executable")

    primary_gids: dict[str, int] = {}
    memberships: dict[str, set[int]] = {}
    for actor in ACTORS.values():
        user = str(actor["user"])
        uid = int(actor["uid"])
        account = pwd.getpwnam(user)
        require(account.pw_uid == uid, "actor_uid_mismatch")
        group = grp.getgrnam(user)
        require(account.pw_gid == group.gr_gid, "actor_primary_group_mismatch")
        primary_gids[user] = account.pw_gid
        memberships[user] = set(os.getgrouplist(user, account.pw_gid))
    require(
        primary_gids["openclaw"] not in memberships["claude"]
        and primary_gids["claude"] not in memberships["openclaw"],
        "cross_actor_group_membership",
    )
    try:
        snotes_gid = grp.getgrnam("snotes").gr_gid
    except KeyError:
        reject("snotes_group_missing")
    require(
        all(snotes_gid not in groups for groups in memberships.values()),
        "ai_user_in_snotes_group",
    )

    for actor in ACTORS.values():
        mirror = Path(actor["mirror"])
        credential = Path(actor["credential"])
        require(stat.S_ISREG(mirror.lstat().st_mode), "mirror_not_regular")
        metadata = credential.lstat()
        require(stat.S_ISREG(metadata.st_mode), "credential_not_regular")
        require(metadata.st_uid == int(actor["uid"]), "credential_owner_mismatch")
        require(stat.S_IMODE(metadata.st_mode) == 0o400, "credential_mode_mismatch")


def _fs_probe_child() -> int:
    if os.geteuid() not in {2001, 2002}:
        return 2
    try:
        request = json.loads(sys.stdin.buffer.read(1024 * 1024))
        expected_uid = int(request["uid"])
        probes = request["probes"]
        if expected_uid != os.geteuid() or not isinstance(probes, list):
            return 2
        passed = 0
        for probe in probes:
            path = Path(probe["path"])
            mode = probe["mode"]
            expected = probe["expected"]
            if not path.is_absolute() or not str(path).startswith("/srv/snotes/"):
                return 2
            if mode == "read":
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            elif mode == "write":
                # O_CREAT and O_TRUNC are intentionally absent.
                flags = os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            else:
                return 2
            try:
                descriptor = os.open(path, flags)
            except OSError as error:
                if expected != "eacces" or error.errno != errno.EACCES:
                    return 2
            else:
                os.close(descriptor)  # Never read or write a byte.
                if expected != "success":
                    return 2
            passed += 1
        sys.stdout.buffer.write(_json_line({"status": "PASS", "checks": passed}))
        return 0
    except Exception:
        return 2


def _filesystem_checks_for_inventory(inventory: list[Path]) -> int:
    own_mirrors = {Path(actor["mirror"]) for actor in ACTORS.values()}
    require(any(path not in own_mirrors for path in inventory), "production_db_missing")
    total = 0
    for actor_name, actor in ACTORS.items():
        other_name = "clawbot" if actor_name == "claude-code" else "claude-code"
        other = ACTORS[other_name]
        probes = [
            {"path": str(actor["mirror"]), "mode": "read", "expected": "success"},
            {"path": str(actor["mirror"]), "mode": "write", "expected": "eacces"},
            {
                "path": str(actor["credential"]),
                "mode": "write",
                "expected": "eacces",
            },
            {"path": str(other["mirror"]), "mode": "read", "expected": "eacces"},
            {"path": str(other["mirror"]), "mode": "write", "expected": "eacces"},
            {
                "path": str(other["credential"]),
                "mode": "read",
                "expected": "eacces",
            },
            {
                "path": str(other["credential"]),
                "mode": "write",
                "expected": "eacces",
            },
            {"path": str(OUTBOX_PATH), "mode": "read", "expected": "eacces"},
            {"path": str(OUTBOX_PATH), "mode": "write", "expected": "eacces"},
        ]
        for path in inventory:
            if path == Path(actor["mirror"]):
                continue
            probes.extend(
                (
                    {"path": str(path), "mode": "read", "expected": "eacces"},
                    {"path": str(path), "mode": "write", "expected": "eacces"},
                )
            )
        response = _run_as_actor(
            actor,
            [PYTHON, "-I", "-B", str(SCRIPT), "--fs-probe"],
            stdin=json.dumps({"uid": actor["uid"], "probes": probes}).encode(),
        )
        decoded = _safe_json(response, "filesystem_probe_invalid")
        require(
            isinstance(decoded, dict)
            and decoded.get("status") == "PASS"
            and decoded.get("checks") == len(probes),
            "filesystem_probe_failed",
        )
        total += len(probes)
    return total


def _filesystem_checks() -> int:
    inventory = _database_inventory()
    try:
        return _filesystem_checks_for_inventory(inventory)
    except VerificationError:
        # SQLite sidecars and backup files can disappear between root's metadata
        # walk and an actor's open. Retry once only when that inventory changed.
        refreshed = _database_inventory()
        if refreshed == inventory:
            raise
        return _filesystem_checks_for_inventory(refreshed)


def _reminder_wrapper_checks() -> int:
    checks = 0
    request = _json_line(
        {"command": "get_reminder_item", "payload": {"item_id": MAX_ID}}
    )
    for actor in ACTORS.values():
        response = _run_as_actor(actor, [str(REMINDER_WRAPPER)], stdin=request)
        decoded = _safe_json(response, "reminder_wrapper_invalid")
        require(
            isinstance(decoded, dict) and decoded.get("status") == "not_found",
            "reminder_wrapper_not_found_failed",
        )
        checks += 1
    return checks


def _api_request(token: str, command: str, payload: dict[str, object]) -> int:
    connection = http.client.HTTPConnection(*REMINDER_ENDPOINT, timeout=5)
    try:
        connection.request(
            "POST",
            f"/v1/reminder/commands/{command}",
            body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
        )
        response = connection.getresponse()
        status_code = response.status
        response.close()  # Do not read or print the response body.
        return status_code
    finally:
        connection.close()


def _api_probe_child() -> int:
    if os.geteuid() not in {2001, 2002}:
        return 2
    try:
        sys.path.insert(0, str(REMINDER_RUNTIME))
        sys.path.insert(0, "/srv/snotes/ai-access")
        from reminder_actor import binding_for_effective_uid, read_credential
        from src.snotes.transport.reminder_http import (
            ACTOR_COMMANDS,
            IDENTITY_FIELDS,
            OWNER_ONLY_COMMANDS,
        )

        token = read_credential(binding_for_effective_uid(os.geteuid()))
        checks = 0
        if set(ACTOR_COMMANDS) != EXPECTED_REMINDER_ACTOR_COMMANDS:
            return 2
        checks += 1
        for command in sorted(OWNER_ONLY_COMMANDS):
            if _api_request(token, command, {}) != 403:
                return 2
            checks += 1
        for command in UNKNOWN_NOTE_TOOLS:
            if _api_request(token, command, {}) != 404:
                return 2
            checks += 1
        for identity_field in sorted(IDENTITY_FIELDS):
            payload = {"item_id": MAX_ID, identity_field: "owner"}
            if _api_request(token, "get_reminder_item", payload) != 400:
                return 2
            checks += 1
        for forbidden_key, value in (
            ("ai_visible", True),
            ("actor_targets", ["owner"]),
        ):
            payload = {
                "request_id": "step09-negative-probe",
                "table_id": MAX_ID,
                forbidden_key: value,
            }
            if _api_request(token, "update_reminder_table", payload) != 400:
                return 2
            checks += 1
        sys.stdout.buffer.write(_json_line({"status": "PASS", "checks": checks}))
        return 0
    except Exception:
        return 2


def _api_boundary_checks() -> int:
    total = 0
    for actor in ACTORS.values():
        response = _run_as_actor(
            actor, [PYTHON, "-I", "-B", str(SCRIPT), "--api-probe"]
        )
        decoded = _safe_json(response, "api_probe_invalid")
        require(
            isinstance(decoded, dict)
            and decoded.get("status") == "PASS"
            and isinstance(decoded.get("checks"), int)
            and decoded["checks"] > 0,
            "api_probe_failed",
        )
        total += int(decoded["checks"])
    return total


def _mcp_read_message(process: subprocess.Popen[bytes]) -> dict:
    ready, _, _ = select.select([process.stdout], [], [], 10)
    if not ready or process.stdout is None:
        reject("mcp_response_timeout")
    raw = process.stdout.readline(64 * 1024 + 1)
    if not raw or len(raw) > 64 * 1024:
        reject("mcp_response_invalid")
    decoded = _safe_json(raw, "mcp_response_invalid")
    require(isinstance(decoded, dict), "mcp_response_invalid")
    return decoded


def _mcp_send(process: subprocess.Popen[bytes], value: dict) -> None:
    if process.stdin is None:
        reject("mcp_stdin_missing")
    process.stdin.write(_json_line(value))
    process.stdin.flush()


def _mcp_status(response: dict) -> str | None:
    """Extract only the status discriminator, never return content."""
    result = response.get("result")
    if not isinstance(result, dict):
        return None
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and isinstance(structured.get("status"), str):
        return structured["status"]
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                continue
            try:
                decoded = json.loads(item["text"])
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict) and isinstance(decoded.get("status"), str):
                return decoded["status"]
    return None


def _mcp_actor_check(actor: dict[str, object]) -> int:
    process = subprocess.Popen(
        [RUNUSER, "-u", str(actor["user"]), "--", str(NOTES_WRAPPER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={"PATH": "/usr/bin:/bin"},
    )
    checks = 0
    try:
        _mcp_send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "snotes-step09-verifier", "version": "1"},
                },
            },
        )
        initialized = _mcp_read_message(process)
        server_info = initialized.get("result", {}).get("serverInfo", {})
        require(server_info.get("name") == "snotes-cloud-read", "mcp_server_mismatch")
        checks += 1
        _mcp_send(
            process,
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        )
        _mcp_send(
            process,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        listed = _mcp_read_message(process)
        tools = listed.get("result", {}).get("tools", [])
        names = {
            tool.get("name") for tool in tools if isinstance(tool, dict)
        } if isinstance(tools, list) else set()
        require(names == EXPECTED_NOTE_TOOLS, "mcp_tool_allowlist_changed")
        checks += 1

        request_id = 3
        _mcp_send(
            process,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": "get_note", "arguments": {"note_id": MAX_ID}},
            },
        )
        require(_mcp_status(_mcp_read_message(process)) == "not_found", "mcp_not_found_failed")
        checks += 1
        for tool_name in UNKNOWN_NOTE_TOOLS:
            request_id += 1
            _mcp_send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": {}},
                },
            )
            response = _mcp_read_message(process)
            failed = (
                "error" in response
                or response.get("result", {}).get("isError") is True
                or _mcp_status(response) == "failed"
            )
            require(failed, "mcp_unknown_tool_accepted")
            checks += 1
    finally:
        if process.stdin is not None:
            process.stdin.close()
        try:
            return_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            reject("mcp_shutdown_timeout")
        require(return_code == 0, "mcp_process_failed")
    return checks


def _mcp_checks() -> int:
    return sum(_mcp_actor_check(actor) for actor in ACTORS.values())


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "--fs-probe":
        return _fs_probe_child()
    if len(sys.argv) == 2 and sys.argv[1] == "--api-probe":
        return _api_probe_child()
    if len(sys.argv) != 1:
        return 2

    checks = 0
    categories = (
        ("IDENTITY_AND_METADATA", lambda: (_root_metadata_checks(), 1)[1]),
        ("FILESYSTEM_ISOLATION", _filesystem_checks),
        ("REMINDER_ACTOR_WRAPPER", _reminder_wrapper_checks),
        ("REMINDER_API_BOUNDARY", _api_boundary_checks),
        ("NOTES_MCP_BOUNDARY", _mcp_checks),
    )
    try:
        for label, operation in categories:
            count = operation()
            checks += count
            print(f"PASS {label} {count}")
    except Exception:
        print(f"FAIL {label}")
        print(f"FAIL STEP09_ACTOR_ISOLATION {checks}")
        return 1
    print(f"PASS STEP09_ACTOR_ISOLATION {checks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
