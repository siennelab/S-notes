#!/usr/bin/env python3
"""Remote Step 5 acceptance using only fictional actor mirror fixtures."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from src.snotes.application.ai_actor_registry import target_definition
from src.services.actor_mirror_publish_service import SshActorMirrorTransport


ACTOR_USERS = {"claude-code": "claude", "clawbot": "openclaw"}
REMOTE_PATHS = {
    actor: f"/srv/snotes/mirror/{actor}/current.db" for actor in ACTOR_USERS
}
EXPECTED_TOOLS = ["get_note", "get_note_markdown", "search_notes"]


class AcceptanceFailure(RuntimeError):
    pass


class Step05CloudAcceptance:
    def __init__(
        self,
        *,
        fixture_root: Path,
        host: str,
        publish_account: str,
        identity_file: Path,
    ) -> None:
        self.fixture_root = Path(fixture_root).absolute()
        self.host = host
        self.publish_account = publish_account
        self.identity_file = Path(identity_file).expanduser().absolute()
        self.manifest = self._load_manifest()
        self.validation = {f"{index:02d}": "PENDING" for index in range(1, 18)}

    def preflight(self) -> dict[str, object]:
        self._validate_local_fixtures()
        return {
            "status": "preflight_ok",
            "fixture": self.manifest["fixture"],
            "mirror_count": 4,
            "source_database_uploaded": False,
            "remote_started": False,
        }

    async def run(self) -> dict[str, object]:
        self._validate_local_fixtures()
        self._require(self.identity_file.is_file(), "publish_identity_missing")
        self._require(not self.identity_file.is_symlink(), "publish_identity_symlink")
        self._require(
            self.identity_file.stat().st_mode & 0o077 == 0,
            "publish_identity_permissions_unsafe",
        )
        self._publish_version("v1")
        v1_hashes = self._remote_hashes()
        v1_inodes = self._remote_inodes()

        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr_capture:
            await self._run_mcp_acceptance(
                stderr_capture, v1_hashes=v1_hashes, v1_inodes=v1_inodes
            )

        self._validate_filesystem_permissions()
        self._validate_transport_negatives()
        self._require(
            all(value == "PASS" for value in self.validation.values()),
            "validation_17_incomplete",
        )
        return {
            "status": "pass",
            "fixture": self.manifest["fixture"],
            "validation_17": self.validation,
            "validation_pass_count": 17,
            "mcp_session_count": 2,
            "transport_negative_count": 3,
            "source_database_uploaded": False,
        }

    async def _run_mcp_acceptance(
        self,
        stderr_capture,
        *,
        v1_hashes: dict[str, str],
        v1_inodes: dict[str, str],
    ) -> None:
        async with AsyncExitStack() as stack:
            sessions: dict[str, ClientSession] = {}
            for actor, user in ACTOR_USERS.items():
                parameters = StdioServerParameters(
                    command="/usr/bin/ssh",
                    args=[
                        "-T",
                        "-o",
                        "BatchMode=yes",
                        "-o",
                        "StrictHostKeyChecking=yes",
                        self.host,
                        "runuser",
                        "-u",
                        user,
                        "--",
                        "/usr/local/bin/snotes-cloud-read",
                    ],
                )
                read_stream, write_stream = await stack.enter_async_context(
                    stdio_client(parameters, errlog=stderr_capture)
                )
                session = await stack.enter_async_context(
                    ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timedelta(seconds=30),
                    )
                )
                initialized = await session.initialize()
                self._require(
                    initialized.serverInfo.name == "snotes-cloud-read",
                    "mcp_initialize_wrong_server",
                )
                sessions[actor] = session

            await self._validate_tool_surfaces(sessions)
            await self._validate_v1_semantics(sessions)
            await self._validate_offline_read(sessions)

            self._validate_damaged_candidate(v1_hashes)
            await self._validate_v1_still_current(sessions)

            self._publish_version("v2")
            v2_inodes = self._remote_inodes()
            self._require(
                all(v2_inodes[actor] != v1_inodes[actor] for actor in ACTOR_USERS),
                "atomic_replace_inode_unchanged",
            )
            await self._validate_next_request_v2(sessions)
            self.validation["13"] = "PASS"

    async def _validate_tool_surfaces(
        self, sessions: dict[str, ClientSession]
    ) -> None:
        for session in sessions.values():
            tools = await session.list_tools()
            self._require(
                sorted(tool.name for tool in tools.tools) == EXPECTED_TOOLS,
                "mcp_tool_surface_mismatch",
            )
            for tool in tools.tools:
                properties = tool.inputSchema.get("properties", {})
                self._require("actor" not in properties, "request_actor_override_exposed")

    async def _validate_v1_semantics(
        self, sessions: dict[str, ClientSession]
    ) -> None:
        ids = self._ids()
        expected = self._expectations()
        expected_v1 = self._version_expectations("v1")
        claude = sessions["claude-code"]
        clawbot = sessions["clawbot"]

        common_claude = await self._call(claude, "get_note", ids["common_full"])
        common_clawbot = await self._call(clawbot, "get_note", ids["common_full"])
        self._require(common_claude["status"] == "ok", "common_missing_claude")
        self._require(common_clawbot["status"] == "ok", "common_missing_openclaw")
        self.validation["01"] = "PASS"

        claude_only = await self._call(claude, "get_note", ids["claude_only"])
        hidden_from_clawbot = await self._call(
            clawbot, "get_note", ids["claude_only"]
        )
        self._require(claude_only["status"] == "ok", "claude_only_missing")
        self._require(
            hidden_from_clawbot["status"] == "not_found",
            "claude_only_visible_to_openclaw",
        )
        self.validation["02"] = "PASS"

        clawbot_only = await self._call(clawbot, "get_note", ids["clawbot_only"])
        hidden_from_claude = await self._call(
            claude, "get_note", ids["clawbot_only"]
        )
        self._require(clawbot_only["status"] == "ok", "clawbot_only_missing")
        self._require(
            hidden_from_claude["status"] == "not_found",
            "clawbot_only_visible_to_claude",
        )
        self.validation["03"] = "PASS"

        for session in sessions.values():
            for key in ("hidden", "unauthorized_gale_only"):
                result = await self._call(session, "get_note", ids[key])
                self._require(result["status"] == "not_found", f"{key}_visible")
                markdown = await self._call(
                    session, "get_note_markdown", ids[key]
                )
                self._require(
                    markdown["status"] == "not_found",
                    f"{key}_markdown_visible",
                )
        nonexistent_id = 9_223_372_036_854_775_000
        for session in sessions.values():
            missing = await self._call(session, "get_note_markdown", nonexistent_id)
            self._require(
                missing["status"] == "not_found",
                "nonexistent_markdown_not_uniform",
            )
        opposite_markdown = (
            (clawbot, ids["claude_only"]),
            (claude, ids["clawbot_only"]),
        )
        for session, note_id in opposite_markdown:
            missing = await self._call(session, "get_note_markdown", note_id)
            self._require(
                missing["status"] == "not_found",
                "opposite_actor_markdown_visible",
            )
        self.validation["04"] = "PASS"

        for session in sessions.values():
            redacted = await self._call(session, "get_note", ids["redacted"])
            self._require(redacted["status"] == "ok", "redacted_plain_missing")
            self._require(
                redacted["safe_text"] == expected_v1["redacted"]["safe_text"],
                "redacted_plain_changed",
            )
            self._require(
                expected["raw_token"] not in redacted["safe_text"],
                "redacted_raw_token_exposed",
            )
            redacted_markdown = await self._call(
                session, "get_note_markdown", ids["redacted"]
            )
            self._require(
                redacted_markdown["status"] == "not_found",
                "redacted_markdown_visible",
            )
        self.validation["05"] = "PASS"
        self.validation["09"] = "PASS"

        expected_markdown = expected_v1["common"]["markdown"]
        for session in sessions.values():
            markdown = await self._call(
                session, "get_note_markdown", ids["common_full"]
            )
            self._require(markdown["status"] == "ok", "full_markdown_missing")
            self._require(markdown["markdown"] == expected_markdown, "markdown_rewritten")
            self._require(
                markdown["dialect"] == expected["dialect"], "markdown_dialect_wrong"
            )
            self._require(
                markdown["version"] == expected_v1["common"]["version"],
                "source_version_wrong",
            )
        self.validation["06"] = "PASS"
        self.validation["07"] = "PASS"
        self.validation["10"] = "PASS"
        self.validation["11"] = "PASS"

        self._require(
            common_claude["safe_text"]
            == expected_v1["common"]["safe_text"],
            "safe_text_wrong",
        )
        self.validation["08"] = "PASS"

        claude_search = await self._search(claude, "fictional comet")
        clawbot_search = await self._search(clawbot, "fictional comet")
        self._require(
            [item["note_id"] for item in claude_search["results"]]
            == [ids["claude_only"]],
            "claude_search_scope_wrong",
        )
        self._require(
            [item["note_id"] for item in clawbot_search["results"]]
            == [ids["clawbot_only"]],
            "clawbot_search_scope_wrong",
        )
        self._require(
            target_definition("claude-code") is not None
            and target_definition("clawbot") is not None,
            "actor_registry_missing",
        )
        self.validation["12"] = "PASS"

    async def _validate_offline_read(
        self, sessions: dict[str, ClientSession]
    ) -> None:
        note_id = self._ids()["common_full"]
        version = self._version_expectations("v1")["common"]["version"]
        # No publish transport is invoked between these Cloud-only requests.
        for session in sessions.values():
            first = await self._call(session, "get_note", note_id)
            second = await self._call(session, "get_note", note_id)
            self._require(
                first["version"] == version and second["version"] == version,
                "offline_last_mirror_unavailable",
            )
        self.validation["14"] = "PASS"

    async def _validate_v1_still_current(
        self, sessions: dict[str, ClientSession]
    ) -> None:
        note_id = self._ids()["common_full"]
        expected = self._version_expectations("v1")["common"]["version"]
        for session in sessions.values():
            result = await self._call(session, "get_note", note_id)
            self._require(result["version"] == expected, "damaged_candidate_replaced_current")

    async def _validate_next_request_v2(
        self, sessions: dict[str, ClientSession]
    ) -> None:
        note_id = self._ids()["common_full"]
        expected = self._version_expectations("v2")["common"]
        for session in sessions.values():
            result = await self._call(session, "get_note", note_id)
            self._require(
                result["version"] == expected["version"],
                "next_request_stale_inode",
            )
            self._require(
                result["safe_text"] == expected["safe_text"],
                "next_request_stale_content",
            )

    def _validate_damaged_candidate(self, v1_hashes: dict[str, str]) -> None:
        payload = b"fictional damaged SQLite candidate\n"
        completed, response = self._publish_bytes("claude-code", payload)
        self._require(completed.returncode != 0, "damaged_candidate_accepted")
        self._require(
            response == {"status": "error", "reason": "candidate_rejected"},
            "damaged_candidate_wrong_result",
        )
        self._require(
            self._remote_hashes() == v1_hashes,
            "damaged_candidate_changed_current",
        )
        self.validation["15"] = "PASS"

    def _validate_filesystem_permissions(self) -> None:
        cases = (
            ("claude", REMOTE_PATHS["clawbot"], "rb", "16"),
            ("openclaw", REMOTE_PATHS["claude-code"], "rb", "17"),
            ("claude", REMOTE_PATHS["claude-code"], "write_probe", None),
            ("openclaw", REMOTE_PATHS["clawbot"], "write_probe", None),
        )
        for user, path, mode, validation_id in cases:
            if mode == "rb":
                code = f'open("{path}","rb").read(1)'
            else:
                code = (
                    f'import os; fd=os.open("{path}",os.O_WRONLY); os.close(fd)'
                )
            command = (
                f"runuser -u {user} -- /usr/bin/python3 -c '{code}'"
            )
            completed = self._admin_command(command, check=False)
            self._require(completed.returncode != 0, "filesystem_permission_allowed")
            self._require(
                b"PermissionError" in completed.stderr,
                "filesystem_denial_not_permission_error",
            )
            if validation_id is not None:
                self.validation[validation_id] = "PASS"

    def _validate_transport_negatives(self) -> None:
        before = self._remote_hashes()
        invalid_actor, response = self._publish_bytes("gale", b"fictional forbidden actor")
        self._require(invalid_actor.returncode != 0, "invalid_actor_accepted")
        self._require(response == {"status": "error", "reason": "unsupported_actor"}, "invalid_actor_wrong_result")
        self._require(self._remote_hashes() == before, "invalid_actor_changed_current")
        shell = self._publish_ssh(input_bytes=b"")
        self._require(shell.returncode != 0, "forced_command_shell_available")
        self._require(
            self._json_stdout(shell) == {"status": "error", "reason": "invalid_header"},
            "forced_command_shell_wrong_result",
        )
        arbitrary = self._publish_ssh(input_bytes=b"", remote_command="id")
        self._require(arbitrary.returncode != 0, "forced_command_arbitrary_allowed")
        self._require(
            self._json_stdout(arbitrary)
            == {"status": "error", "reason": "command_rejected"},
            "forced_command_arbitrary_wrong_result",
        )

    def _publish_version(self, version: str) -> None:
        versions = self.manifest["versions"]
        self._require(isinstance(versions, dict), "manifest_versions_invalid")
        records = versions.get(version)
        self._require(isinstance(records, dict), "manifest_version_missing")
        transport = SshActorMirrorTransport(
            host=self.host,
            account=self.publish_account,
            identity_file=self.identity_file,
        )
        for actor in ACTOR_USERS:
            record = records.get(actor)
            self._require(isinstance(record, dict), "manifest_actor_missing")
            path = self.fixture_root / str(record["relative_path"])
            receipt = transport.publish(
                actor=actor,
                mirror_path=path,
                file_sha256=str(record["file_sha256"]),
                content_hash=str(record["content_hash"]),
            )
            self._require(receipt.actor == actor, "publish_actor_mismatch")
            self._require(
                receipt.content_hash == record["content_hash"],
                "publish_content_hash_mismatch",
            )
            self._require(
                receipt.note_count == int(record["note_count"]),
                "publish_note_count_mismatch",
            )

    def _publish_bytes(
        self, actor: str, payload: bytes
    ) -> tuple[subprocess.CompletedProcess[bytes], dict[str, object]]:
        header = json.dumps(
            {
                "actor": actor,
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii") + b"\n"
        completed = self._publish_ssh(input_bytes=header + payload)
        return completed, self._json_stdout(completed)

    def _publish_ssh(
        self, *, input_bytes: bytes, remote_command: str | None = None
    ) -> subprocess.CompletedProcess[bytes]:
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
            "RequestTTY=no",
            "-i",
            str(self.identity_file),
            f"{self.publish_account}@{self.host}",
        ]
        if remote_command is not None:
            command.append(remote_command)
        return subprocess.run(
            command,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )

    def _remote_hashes(self) -> dict[str, str]:
        hashes = {}
        for actor, path in REMOTE_PATHS.items():
            completed = self._admin_command(f"sha256sum {path}")
            digest = completed.stdout.decode("ascii").strip().split()[0]
            self._require(len(digest) == 64, "remote_hash_invalid")
            hashes[actor] = digest
        return hashes

    def _remote_inodes(self) -> dict[str, str]:
        return {
            actor: self._admin_command(f"stat -c %i {path}")
            .stdout.decode("ascii")
            .strip()
            for actor, path in REMOTE_PATHS.items()
        }

    def _admin_command(
        self, remote_command: str, *, check: bool = True
    ) -> subprocess.CompletedProcess[bytes]:
        completed = subprocess.run(
            [
                "/usr/bin/ssh",
                "-T",
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                self.host,
                remote_command,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        if check:
            self._require(completed.returncode == 0, "admin_read_failed")
        return completed

    async def _call(
        self, session: ClientSession, tool: str, note_id: int
    ) -> dict[str, object]:
        result = await session.call_tool(tool, {"note_id": note_id})
        return self._structured(result)

    async def _search(self, session: ClientSession, query: str) -> dict[str, object]:
        result = await session.call_tool("search_notes", {"query": query})
        return self._structured(result)

    def _structured(self, result: object) -> dict[str, object]:
        self._require(not bool(getattr(result, "isError", False)), "mcp_tool_error")
        payload = getattr(result, "structuredContent", None)
        self._require(isinstance(payload, dict), "mcp_structured_content_missing")
        return payload

    def _validate_local_fixtures(self) -> None:
        self._require(
            self.manifest.get("fixture") == "gc35-step05-fictional-v1-v2",
            "fixture_identity_invalid",
        )
        versions = self.manifest.get("versions")
        self._require(isinstance(versions, dict), "manifest_versions_invalid")
        for version in ("v1", "v2"):
            records = versions.get(version)
            self._require(isinstance(records, dict), "manifest_version_missing")
            for actor in ACTOR_USERS:
                record = records.get(actor)
                self._require(isinstance(record, dict), "manifest_actor_missing")
                path = self.fixture_root / str(record["relative_path"])
                self._require(path.is_file(), "fixture_mirror_missing")
                self._require(
                    self._sha256(path) == record["file_sha256"],
                    "fixture_mirror_hash_mismatch",
                )

    def _load_manifest(self) -> dict[str, object]:
        path = self.fixture_root / "manifest.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AcceptanceFailure("manifest_unavailable") from error
        if not isinstance(value, dict):
            raise AcceptanceFailure("manifest_invalid")
        return value

    def _ids(self) -> dict[str, int]:
        value = self.manifest["ids"]
        self._require(isinstance(value, dict), "manifest_ids_invalid")
        return {key: int(item) for key, item in value.items()}

    def _expectations(self) -> dict[str, object]:
        value = self.manifest["expectations"]
        self._require(isinstance(value, dict), "manifest_expectations_invalid")
        return value

    def _version_expectations(self, version: str) -> dict[str, dict[str, str]]:
        expectations = self._expectations()
        versions = expectations.get("versions")
        self._require(isinstance(versions, dict), "manifest_expected_versions_invalid")
        selected = versions.get(version)
        self._require(isinstance(selected, dict), "manifest_expected_version_missing")
        normalized: dict[str, dict[str, str]] = {}
        for key, value in selected.items():
            self._require(isinstance(value, dict), "manifest_expected_note_invalid")
            normalized[str(key)] = {
                str(field): str(field_value) for field, field_value in value.items()
            }
        return normalized

    @staticmethod
    def _json_stdout(
        completed: subprocess.CompletedProcess[bytes],
    ) -> dict[str, object]:
        try:
            value = json.loads(completed.stdout.decode("utf-8").strip())
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AcceptanceFailure("transport_response_invalid") from error
        if not isinstance(value, dict):
            raise AcceptanceFailure("transport_response_invalid")
        return value

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _require(condition: bool, reason: str) -> None:
        if not condition:
            raise AcceptanceFailure(reason)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--host", default="optimal")
    parser.add_argument("--publish-account", default="snotes-publish")
    parser.add_argument(
        "--identity",
        type=Path,
        default=Path.home() / ".ssh" / "snotes-greencloud-mirror",
    )
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    try:
        acceptance = Step05CloudAcceptance(
            fixture_root=args.fixtures,
            host=args.host,
            publish_account=args.publish_account,
            identity_file=args.identity,
        )
        result = (
            acceptance.preflight()
            if args.preflight_only
            else asyncio.run(acceptance.run())
        )
    except AcceptanceFailure as error:
        print(json.dumps({"status": "failed", "reason": str(error)}, sort_keys=True))
        return 1
    except Exception as error:
        print(
            json.dumps(
                {"status": "failed", "reason": type(error).__name__},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
