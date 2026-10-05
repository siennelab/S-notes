"""Content-free GreenCloud checks; all mutations use disposable fictional data."""
from __future__ import annotations

import asyncio
import errno
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import pwd
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import uuid

RUNTIME = Path("/srv/snotes/runtime")
PYTHON = RUNTIME / ".venv/bin/python"
SCRIPT = Path(__file__).resolve()
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(SCRIPT.parent))

from fixture_checks import exercise_tools, require, result_body
from src.cloud_read_mcp import binding_for_effective_uid, read_actor_credential
from src.greencloud_internal_mcp import FixedActorCredentialProvider, GALE_REMINDER_CREDENTIAL
from src.snotes.application.cloud_proposal_service import CloudProposalService
from src.snotes.application.cloud_read_service import CloudReadService
from src.snotes.application.remote_mcp_reminder import RemoteMcpReminderAdapter
from src.snotes.storage.cloud_read import SQLiteCloudMirrorReader
from src.snotes.transport.cloud_outbox_http import IDENTITY_FIELDS
from src.snotes.transport.mcp.cloud_read_server import cloud_read_tools
from src.snotes.transport.mcp.greencloud_internal_server import build_greencloud_internal_server
from src.snotes.transport.remote_reminder_client import RemoteReminderClient
from src.snotes.transport.reminder_http import ACTOR_COMMANDS, OWNER_ONLY_COMMANDS


def raw_post(port, path, payload, token):
    require(port not in {8766, 8767} or path.startswith("/v1/"), "http_path_invalid")
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("POST", path, body=json.dumps(payload).encode(),
                           headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, json.loads(response.read(512 * 1024))
    finally:
        connection.close()


def actor_probe():
    binding = binding_for_effective_uid(os.geteuid())
    require(binding.actor == "gale", "gale_identity_required")
    request = json.loads(sys.stdin.read())
    count = 0
    for path in request["deny"]:
        for flags in (os.O_RDONLY, os.O_WRONLY):
            try:
                fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK)
            except OSError as error:
                require(error.errno == errno.EACCES, "isolation_not_eacces:" + Path(path).name)
            else:
                os.close(fd)
                raise RuntimeError("isolation_open_allowed:" + Path(path).name)
            count += 1
    for path in (binding.mirror_path, binding.credential_path, GALE_REMINDER_CREDENTIAL):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        os.close(fd)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_NOFOLLOW)
        except PermissionError:
            pass
        else:
            os.close(fd)
            raise RuntimeError("own_file_write_allowed")
    notes_token = read_actor_credential(binding)
    reminder_token = FixedActorCredentialProvider(binding.uid, GALE_REMINDER_CREDENTIAL)()
    marker = "gale-internal-nonexistent-" + uuid.uuid4().hex
    require(notes_token != reminder_token, "tokens_not_separate")
    status, body = raw_post(8767, "/v1/notes/proposals/status", {"request_id": marker}, notes_token)
    require(status == 200 and body.get("status") == "not_found", "production_notes_auth")
    status, _ = raw_post(8767, "/v1/notes/proposals/status", {"request_id": marker}, reminder_token)
    require(status == 403, "reminder_token_accepted_for_notes")
    status, _ = raw_post(8766, "/v1/reminder/commands/list_reminder_tables", {}, notes_token)
    require(status == 403, "notes_token_accepted_for_reminder")
    for command in OWNER_ONLY_COMMANDS:
        status, _ = raw_post(8766, "/v1/reminder/commands/" + command, {}, reminder_token)
        require(status == 403, "owner_command_not_denied")
    for field in IDENTITY_FIELDS:
        status, _ = raw_post(8767, "/v1/notes/proposals/status", {"request_id": marker, field: "owner"}, notes_token)
        require(status == 400, "notes_actor_injection_allowed")
    status, _ = raw_post(8767, "/v1/notes/proposals/enqueue", {
        "request_id": marker, "operation_type": "delete", "note_id": 9223372036854775807,
        "expected_version": "snotes-v1-" + "0" * 64, "payload": {}}, notes_token)
    require(status == 403, "production_delete_allowed")
    status, _ = raw_post(8766, "/v1/reminder/commands/list_reminder_tables", {"actor": "owner"}, reminder_token)
    require(status == 400, "reminder_actor_injection_allowed")
    print("GALE_FILESYSTEM_DENIAL=" + str(count) + ":PASS")
    print("GALE_PRODUCTION_AUTH_AND_NEGATIVES=PASS")


def fixture_services(root):
    require(os.geteuid() == 2006, "fixture_service_uid_required")
    from src.services.gale_submission_outbox import initialize_outbox
    from src.snotes.application.reminder_service import ReminderService
    from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
    from src.snotes.storage.schema import initialize_database
    from src.snotes.transport.cloud_outbox_http import CloudOutboxHttpApplication
    from src.snotes.transport.reminder_http import ReminderCredential, ReminderHttpApplication
    tokens = json.loads(sys.stdin.readline())  # private pipe, never argv/env/files
    outbox = root / "data/outbox.db"
    initialize_outbox(outbox, generalized=True)
    notes = CloudOutboxHttpApplication(outbox, {"fixture-claude-notes": "claude-code", "fixture-clawbot-notes": "clawbot", tokens["notes"]: "gale"},
            reader_factory=lambda actor: SQLiteCloudMirrorReader(root / "mirror" / actor / "current.db"))
    reminder = ReminderHttpApplication(ReminderService(SQLiteReminderRepository(initialize_database(root / "data/reminder.db"))),
                                       {tokens["reminder"]: ReminderCredential("gale", "actor")})

    def server_for(application):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.dispatch()

            def do_POST(self):
                self.dispatch()

            def dispatch(self):
                size = int(self.headers.get("Content-Length", "0"))
                require(0 <= size <= 640 * 1024, "fixture_request_too_large")
                response = application.handle(self.command, self.path, headers=dict(self.headers), body=self.rfile.read(size))
                encoded = json.dumps(response.body).encode()
                self.send_response(response.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                for name, value in response.headers:
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *_args):
                pass
        return ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    servers = [server_for(notes), server_for(reminder)]
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers]
    for thread in threads:
        thread.start()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_args: stop.set())
    signal.signal(signal.SIGINT, lambda *_args: stop.set())
    print(json.dumps({"notes_port": servers[0].server_port, "reminder_port": servers[1].server_port,
                      "pid": os.getpid()}), flush=True)
    try:
        stop.wait()
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)


def fixture_mcp(root, notes_port, reminder_port):
    binding = binding_for_effective_uid(os.geteuid())
    require(binding.actor == "gale", "gale_identity_required")
    require(notes_port not in {8766, 8767} and reminder_port not in {8766, 8767}, "fixture_production_port_forbidden")
    token_provider = lambda: read_actor_credential(binding)

    class Gateway:
        def get_operation_status(self, request_id):
            status, body = raw_post(notes_port, "/v1/notes/proposals/status", {"request_id": request_id}, token_provider())
            require(status == 200, "fixture_notes_status_http")
            return None if body.get("status") == "not_found" else body

        def enqueue_proposal(self, **payload):
            status, body = raw_post(notes_port, "/v1/notes/proposals/enqueue", payload, token_provider())
            require(status == 200, "fixture_notes_enqueue_http")
            return body
    reader = SQLiteCloudMirrorReader(root / "mirror/gale/current.db")
    reminder_client = RemoteReminderClient("http://127.0.0.1:" + str(reminder_port), FixedActorCredentialProvider(binding.uid, GALE_REMINDER_CREDENTIAL))
    server = build_greencloud_internal_server(CloudReadService(reader), CloudProposalService(reader, Gateway()), RemoteMcpReminderAdapter(reminder_client))

    async def serve():
        from mcp.server.stdio import stdio_server
        async with stdio_server() as streams:
            await server.run(*streams, server.create_initialization_options())
    try:
        asyncio.run(serve())
    finally:
        reminder_client.close()


async def mcp_check(user, wrapper=None, *, fixture=None, ports=None):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    if fixture:
        args = [str(PYTHON), "-I", "-B", str(SCRIPT), "--fixture-mcp", str(fixture), str(ports["notes_port"]), str(ports["reminder_port"])]
    else:
        args = [wrapper]
    params = StdioServerParameters(command="/usr/sbin/runuser", args=["-u", user, "--", *args],
                                  cwd=str(RUNTIME), env={"PATH": "/usr/bin:/bin", "SNOTES_ACTOR": "owner"})
    with open(os.devnull, "w") as errlog:
        async with stdio_client(params, errlog=errlog) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                note_names = {tool.name for tool in cloud_read_tools(proposal_enabled=True)}
                expected = note_names | ACTOR_COMMANDS if user == "snotes-gale" else note_names
                require(names == expected, "discovery_mismatch:" + user)
                async def call(name, payload):
                    return result_body(await session.call_tool(name, payload))
                if fixture:
                    result = await exercise_tools(call, json.loads((fixture / "manifest.json").read_text()))
                    print("GALE_FICTIONAL_MCP=" + json.dumps(result, sort_keys=True))
                else:
                    for name in ("list_directories", "list_tags", "list_ai_metadata_options"):
                        require((await call(name, {}))["status"] == "ok", "production_catalog:" + user)
                    require((await call("get_note", {"note_id": 9223372036854775807}))["status"] == "not_found", "production_note_id_not_hidden")
                    require((await call("get_operation_status", {"request_id": "gale-live-nonexistent-" + uuid.uuid4().hex}))["status"] == "not_found", "production_operation_auth")
                    if user == "snotes-gale":
                        require((await call("list_reminder_tables", {}))["status"] == "ok", "production_reminder_list")
                        require((await call("get_reminder_item", {"item_id": 9223372036854775807}))["status"] == "not_found", "production_reminder_fake_id")
                    print("INTERNAL_MCP=" + user + ":" + str(len(names)) + ":PASS")


def verify(fixture_source):
    require(os.geteuid() == 0 and socket.gethostname() == "optimal-living", "greencloud_root_required")
    account = pwd.getpwnam("snotes-gale")
    require(account.pw_shell == "/usr/sbin/nologin" and account.pw_gid == account.pw_uid
            and set(os.getgrouplist(account.pw_name, account.pw_gid)) == {account.pw_gid}, "gale_identity_isolation")
    def metadata(path, uid, gid, mode):
        info = path.lstat()
        require(not path.is_symlink() and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (uid, gid, mode), "metadata_mismatch:" + path.name)
        require(not any(key.startswith("system.posix_acl_") for key in os.listxattr(path)), "unexpected_acl")
    uid = account.pw_uid
    metadata(Path("/srv/snotes/mirror/gale"), 2006, uid, 0o2750)
    metadata(Path("/srv/snotes/mirror/gale/current.db"), 2006, uid, 0o640)
    for name in ("notes-proposal-token", "reminder-token"):
        metadata(Path("/srv/snotes/actor-credentials/gale") / name, uid, uid, 0o400)
    metadata(Path("/srv/snotes/actor-credentials/gale"), 0, uid, 0o750)
    metadata(Path("/usr/local/bin/snotes-gale-internal-mcp"), 0, 0, 0o755)
    deny = [Path("/srv/snotes/mirror/claude-code/current.db"), Path("/srv/snotes/mirror/clawbot/current.db"),
            Path("/srv/snotes/actor-credentials/service/gale-notes-proposal-token"), Path("/root/greencloud-secrets/reminder-api.json")]
    deny += [path for path in Path("/srv/snotes/actor-credentials").rglob("*") if path.is_file() and "/gale/" not in str(path)]
    deny += list(Path("/srv/snotes/data").rglob("*.db*"))
    deny += list(Path("/srv/snotes/backups").rglob("*.db*"))
    process = subprocess.run(["/usr/sbin/runuser", "-u", "snotes-gale", "--", str(PYTHON), "-I", "-B", str(SCRIPT), "--actor-probe"],
                             input=json.dumps({"deny": sorted(str(path) for path in set(deny))}).encode(), stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=40)
    require(process.returncode == 0, "actor_production_probe_failed")
    print(process.stdout.decode().strip())
    sudo_probe = subprocess.run(["/usr/sbin/runuser", "-u", "snotes-gale", "--", "sudo", "-n", "true"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    require(sudo_probe.returncode != 0, "gale_sudo_allowed")
    for user, actor in (("claude", "claude-code"), ("openclaw", "clawbot")):
        process = subprocess.run(["/usr/sbin/runuser", "-u", user, "--", "/usr/local/bin/snotes-reminder-actor"],
                input=b'{"command":"list_reminder_tables","payload":{}}', stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20)
        require(process.returncode == 0 and json.loads(process.stdout).get("status") == "ok", "reminder_regression:" + actor)
        print("REMINDER_REGRESSION=" + actor + ":PASS")
    for user, wrapper in (("snotes-gale", "/usr/local/bin/snotes-gale-internal-mcp"),
                          ("claude", "/usr/local/bin/snotes-cloud-read"), ("openclaw", "/usr/local/bin/snotes-cloud-read")):
        asyncio.run(asyncio.wait_for(mcp_check(user, wrapper), timeout=40))
    with tempfile.TemporaryDirectory(prefix="snotes-gale-fictional-", dir="/tmp") as name:
        root = Path(name)
        shutil.copytree(fixture_source / "mirror", root / "mirror")
        shutil.copy2(fixture_source / "manifest.json", root / "manifest.json")
        root.chmod(0o711)
        (root / "mirror").chmod(0o711)
        (root / "manifest.json").chmod(0o644)
        manifest = json.loads((root / "manifest.json").read_text())
        require(manifest["fixture"] == "greencloud-gale-internal-fictional" and manifest["source_database_uploaded"] is False, "fixture_required")
        import hashlib
        for actor, expected_hash in manifest["mirrors"].items():
            user = {"gale": "snotes-gale", "claude-code": "claude", "clawbot": "openclaw"}[actor]
            group = pwd.getpwnam(user).pw_gid
            directory = root / "mirror" / actor
            directory.chmod(0o750)
            os.chown(directory, 2006, group)
            path = directory / "current.db"
            require(hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash, "fixture_hash_mismatch")
            os.chown(path, 2006, group)
            path.chmod(0o640)
        (root / "data").mkdir(mode=0o700)
        os.chown(root / "data", 2006, 2006)
        tokens = {"notes": Path("/srv/snotes/actor-credentials/gale/notes-proposal-token").read_text().strip(),
                  "reminder": GALE_REMINDER_CREDENTIAL.read_text().strip()}
        service = subprocess.Popen(["/usr/sbin/runuser", "-u", "snotes", "--", str(PYTHON), "-I", "-B", str(SCRIPT), "--fixture-services", str(root)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        ports = None
        try:
            service.stdin.write((json.dumps(tokens) + "\n").encode())
            service.stdin.flush()
            import select
            require(bool(select.select([service.stdout], [], [], 15)[0]), "fixture_service_start_timeout")
            ports = json.loads(service.stdout.readline())
            asyncio.run(asyncio.wait_for(mcp_check("snotes-gale", fixture=root, ports=ports), timeout=45))
        finally:
            if ports is not None:
                os.kill(ports["pid"], signal.SIGTERM)
            else:
                service.terminate()
            service.communicate(timeout=15)
            require(service.returncode == 0, "fixture_service_shutdown_failed")
    print("GALE_FIXTURES_CLEANED=PASS")
    print("GALE_INTERNAL_VERIFICATION=PASS")


if __name__ == "__main__":
    try:
        if sys.argv[1:2] == ["--actor-probe"]:
            actor_probe()
        elif sys.argv[1:2] == ["--fixture-services"]:
            fixture_services(Path(sys.argv[2]))
        elif sys.argv[1:2] == ["--fixture-mcp"]:
            fixture_mcp(Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]))
        else:
            verify(Path(sys.argv[1]))
    except Exception as error:
        print("GALE_VERIFY=FAIL:" + (str(error) if isinstance(error, RuntimeError) else type(error).__name__))
        raise SystemExit(2)
