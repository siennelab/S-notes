"""Add Gale to existing GreenCloud services; preserve data and other identities."""
from __future__ import annotations

from datetime import datetime, timezone
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import socket
import stat
import subprocess
import tempfile

ROOT = Path("/srv/snotes")
SOURCE = Path(__file__).resolve().parents[3]
ASSETS = SOURCE / "deploy/greencloud/gale-internal"
API_MAPPING = Path("/root/greencloud-secrets/reminder-api.json")
SERVER_NOTES_TOKEN = ROOT / "actor-credentials/service/gale-notes-proposal-token"
MARKER = "\n## Web Gale — internal Notes + Reminder stdio\n"


def safe(path: Path) -> None:
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise RuntimeError("symlink_path_rejected")
    if path.exists() and not (path.is_dir() or stat.S_ISREG(path.lstat().st_mode)):
        raise RuntimeError("regular_path_required")


def require_metadata(path: Path, uid: int, gid: int, mode: int) -> None:
    safe(path)
    info = path.stat()
    if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (uid, gid, mode):
        raise RuntimeError("sealed_permission_mismatch")
    if any(key.startswith("system.posix_acl_") for key in os.listxattr(path)):
        raise RuntimeError("unexpected_acl")


def replace(path: Path, data: bytes, uid: int, gid: int, mode: int) -> None:
    safe(path)
    fd, name = tempfile.mkstemp(prefix=".gale-internal-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchown(stream.fileno(), uid, gid)
            os.fchmod(stream.fileno(), mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def install() -> None:
    os.umask(0o077)
    if os.geteuid() != 0 or socket.gethostname() != "optimal-living":
        raise RuntimeError("greencloud_root_required")
    for user, uid in (("snotes", 2006), ("claude", 2001), ("openclaw", 2002)):
        account = pwd.getpwnam(user)
        if (account.pw_uid, account.pw_gid) != (uid, uid):
            raise RuntimeError("identity_mismatch")
    for path, uid, gid, mode in ((ROOT, 0, 0, 0o755), (ROOT / "runtime", 0, 0, 0o755),
                               (ROOT / "reminder-runtime", 0, 0, 0o755),
                               (ROOT / "mirror", 0, 0, 0o711),
                               (ROOT / "data", 2006, 2006, 0o700),
                               (ROOT / "actor-credentials", 0, 0, 0o711),
                               (API_MAPPING, 0, 0, 0o600)):
        require_metadata(path, uid, gid, mode)
    for actor, uid in (("claude-code", 2001), ("clawbot", 2002)):
        require_metadata(ROOT / "mirror" / actor, 2006, uid, 0o2750)
        require_metadata(ROOT / "mirror" / actor / "current.db", 2006, uid, 0o640)
    manifest_path = SOURCE / "gale-internal-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    content = {}
    for name, expected in manifest["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("package_path_invalid")
        safe(SOURCE / relative)
        data = (SOURCE / relative).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError("package_hash_mismatch")
        content[name] = data
    # Root-only deployment reads existing mapping in memory, never prints values.
    mapping = json.loads(API_MAPPING.read_text())
    if not isinstance(mapping, dict) or not {"owner", "yan", "claude-code", "clawbot"}.issubset(mapping):
        raise RuntimeError("service_mapping_invalid")
    previous_tokens = [value for value in mapping.values() if isinstance(value, str)]
    previous_tokens += list(mapping.get("local_actor_credentials", {}).values())
    reminder_token = mapping.get("gale") or secrets.token_urlsafe(48)
    if SERVER_NOTES_TOKEN.exists():
        require_metadata(SERVER_NOTES_TOKEN, 0, 0, 0o600)
    notes_token = SERVER_NOTES_TOKEN.read_text().strip() if SERVER_NOTES_TOKEN.exists() else secrets.token_urlsafe(48)
    if (not isinstance(reminder_token, str) or not reminder_token or not notes_token
            or any(ch.isspace() for ch in reminder_token + notes_token)
            or reminder_token == notes_token or notes_token in previous_tokens
            or ("gale" not in mapping and reminder_token in previous_tokens)):
        raise RuntimeError("gale_credential_invalid")
    mapping["gale"] = reminder_token
    try:
        gale = pwd.getpwnam("snotes-gale")
    except KeyError:
        uid = 2007
        occupied_uids = {account.pw_uid for account in pwd.getpwall()}
        occupied_gids = {group.gr_gid for group in grp.getgrall()}
        while uid in occupied_uids or uid in occupied_gids:
            uid += 1
        subprocess.run(["groupadd", "--gid", str(uid), "snotes-gale"], check=True)
        subprocess.run(["useradd", "--system", "--uid", str(uid), "--gid", "snotes-gale",
                        "--home-dir", "/nonexistent", "--no-create-home",
                        "--shell", "/usr/sbin/nologin", "snotes-gale"], check=True)
        gale = pwd.getpwnam("snotes-gale")
    uid, gid = gale.pw_uid, gale.pw_gid
    if (uid <= 0 or uid != gid or uid in {2001, 2002, 2006}
            or gale.pw_shell != "/usr/sbin/nologin"
            or set(os.getgrouplist("snotes-gale", gid)) != {gid}):
        raise RuntimeError("gale_identity_boundary_invalid")
    directories = {
        ROOT / "mirror/gale": (2006, gid, 0o2750),
        ROOT / "actor-credentials/gale": (0, gid, 0o750),
        ROOT / "actor-credentials/service": (0, 0, 0o700),
        ROOT / "gale-internal": (0, 0, 0o755),
        Path("/etc/systemd/system/snotes-notes-outbox.service.d"): (0, 0, 0o755),
    }
    targets = {}
    for name in manifest["runtime_sources"]:
        targets[ROOT / "runtime" / name] = (content[name], 0, 0, 0o644)
    for name in manifest["reminder_sources"]:
        targets[ROOT / "reminder-runtime" / name] = (content[name], 0, 0, 0o644)
    targets[ROOT / "ai-access/reminder_actor.py"] = (content["deploy/greencloud/ai-access/reminder_actor.py"], 0, 0, 0o644)
    targets[ROOT / "outbox-deploy/transport.py"] = (content["deploy/greencloud/outbox/transport.py"], 0, 0, 0o644)
    for name, data in content.items():
        if name.startswith("deploy/greencloud/gale-internal/"):
            targets[ROOT / "gale-internal" / Path(name).name] = (data, 0, 0, 0o644)
    targets[Path("/usr/local/bin/snotes-gale-internal-mcp")] = (
        content["deploy/greencloud/gale-internal/snotes-gale-internal-mcp"], 0, 0, 0o755)
    targets[ROOT / "actor-credentials/gale/notes-proposal-token"] = ((notes_token + "\n").encode(), uid, gid, 0o400)
    targets[ROOT / "actor-credentials/gale/reminder-token"] = ((reminder_token + "\n").encode(), uid, gid, 0o400)
    targets[SERVER_NOTES_TOKEN] = ((notes_token + "\n").encode(), 0, 0, 0o600)
    targets[API_MAPPING] = ((json.dumps(mapping, indent=2) + "\n").encode(), 0, 0, 0o600)
    targets[Path("/etc/systemd/system/snotes-notes-outbox.service.d/gale.conf")] = (
        ("[Service]\nLoadCredential=gale-notes-proposal-token:" + str(SERVER_NOTES_TOKEN) + "\n").encode(), 0, 0, 0o644)
    document = (ROOT / "INSTALL.md").read_text()
    if MARKER in document:
        document = document.split(MARKER, 1)[0]
    section = content["deploy/greencloud/gale-internal/INSTALL.md"].decode()
    if not section.startswith(MARKER.lstrip("\n")):
        raise RuntimeError("install_section_invalid")
    targets[ROOT / "INSTALL.md"] = ((document.rstrip() + "\n\n" + section).encode(), 0, 0, 0o644)
    targets[ROOT / "gale-internal/source-manifest.json"] = (manifest_path.read_bytes(), 0, 0, 0o644)
    for target in targets:
        for parent in target.parents:
            if str(parent).startswith(str(ROOT / "runtime")) and not parent.exists():
                directories.setdefault(parent, (0, 0, 0o755))
    # Files already present byte for byte do not need another backup/rewrite.
    for path, value in tuple(targets.items()):
        safe(path)
        if path.exists():
            if not path.is_file():
                raise RuntimeError("target_file_required")
            info = path.stat()
            if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == value[1:] and path.read_bytes() == value[0]:
                del targets[path]
    backup = Path(tempfile.mkdtemp(prefix="gale-internal-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-",
                                   dir="/root/greencloud-backups"))
    metadata = []
    for path in sorted(set(targets) | set(directories)):
        safe(path)
        item = {"path": str(path), "exists": path.exists()}
        if path.exists():
            info = path.stat()
            item.update(uid=info.st_uid, gid=info.st_gid, mode=stat.S_IMODE(info.st_mode))
            if path in targets:
                saved = backup / "files" / path.relative_to("/")
                saved.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                shutil.copy2(path, saved)
        metadata.append(item)
    (backup / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    for path, identity in sorted(directories.items()):
        safe(path)
        if path.exists():
            require_metadata(path, *identity)
        else:
            path.mkdir(mode=identity[2])
            os.chown(path, *identity[:2])
            path.chmod(identity[2])
    for path, (data, owner, group, mode) in targets.items():
        replace(path, data, owner, group, mode)
    record = {"baseline_commit": manifest["baseline_commit"], "rollback_directory": str(backup),
              "actor": "gale", "os_account": "snotes-gale", "uid": uid, "gid": gid,
              "source_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
              "deployed_files": {str(path): hashlib.sha256(value[0]).hexdigest() for path, value in targets.items()
                                 if "token" not in path.name and path != API_MAPPING}}
    replace(ROOT / "gale-internal/deployment-record.json", (json.dumps(record, indent=2) + "\n").encode(), 0, 0, 0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "restart", "snotes-notes-outbox.service", "snotes-reminder-api.service"], check=True)
    print("GALE_DEPLOYED=PASS")
    print("GALE_UID=" + str(uid))
    print("GALE_GID=" + str(gid))
    print("GALE_ROLLBACK=" + str(backup))
    print("GALE_DEPLOYED_FILES=" + str(len(targets)))


if __name__ == "__main__":
    try:
        install()
    except Exception as error:
        # Only controlled codes, never credentials or payloads in diagnostics.
        print("GALE_INSTALL=FAIL:" + (str(error) if isinstance(error, RuntimeError) else type(error).__name__))
        raise SystemExit(2)
