"""Deploy Step 9 assets and actor token files; never change API credentials."""
from __future__ import annotations

from datetime import datetime, timezone
import grp
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import stat
import tempfile


ROOT = Path("/srv/snotes")
SOURCE = Path(__file__).resolve().parents[1]
ACTORS = (("claude-code", "claude", 2001), ("clawbot", "openclaw", 2002))
MARKER = "\n## Step 9 — AI permission isolation\n"


def regular(path: Path) -> None:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise RuntimeError("regular_file_required")


def no_symlink_parents(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink():
            raise RuntimeError("symlink_path_rejected")


def preflight() -> None:
    if os.geteuid() != 0 or socket.gethostname() != "optimal-living":
        raise RuntimeError("greencloud_root_required")
    for actor, user, uid in ACTORS:
        account = pwd.getpwnam(user)
        if account.pw_uid != uid or account.pw_gid != uid:
            raise RuntimeError("identity_mismatch")
        groups = os.getgrouplist(user, account.pw_gid)
        if any(gid in groups for gid in (2001, 2002, 2006) if gid != uid):
            raise RuntimeError("actor_group_boundary_invalid")
        if grp.getgrgid(uid).gr_name != user:
            raise RuntimeError("actor_group_mismatch")
    service = pwd.getpwnam("snotes")
    if service.pw_uid != 2006 or service.pw_gid != 2006:
        raise RuntimeError("service_identity_mismatch")
    expected = [(ROOT, 0, 0, 0o755), (ROOT / "mirror", 0, 0, 0o711),
                (ROOT / "data", 2006, 2006, 0o700)]
    for actor, _user, uid in ACTORS:
        expected += [(ROOT / "mirror" / actor, 2006, uid, 0o2750),
                     (ROOT / "mirror" / actor / "current.db", 2006, uid, 0o640)]
    # The sealed baseline already supplies these permissions. A conflict is not
    # silently repaired by a second installer; no DB content is opened here.
    for path, owner, group, mode in expected:
        no_symlink_parents(path)
        metadata = path.stat()
        if (metadata.st_uid, metadata.st_gid, stat.S_IMODE(metadata.st_mode)) != (owner, group, mode):
            raise RuntimeError("sealed_filesystem_baseline_mismatch")
        if any(name.startswith("system.posix_acl_") for name in os.listxattr(path)):
            raise RuntimeError("sealed_filesystem_acl_mismatch")
    for path in (ROOT / "runtime", ROOT / "reminder-runtime"):
        no_symlink_parents(path)
        metadata = path.stat()
        if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o022:
            raise RuntimeError("runtime_permissions_invalid")
    for path in (ROOT / "INSTALL.md", Path("/usr/local/bin/snotes-cloud-read")):
        no_symlink_parents(path)
        regular(path)


def provision_tokens() -> dict[str, str]:
    """Deployment-only use of existing service configuration, never a test.

    The existing root-only mapping remains unchanged. Tokens stay on this host
    and are copied only into their corresponding actor's 0400 file.
    """
    path = Path("/root/greencloud-secrets/reminder-api.json")
    no_symlink_parents(path)
    regular(path)
    metadata = path.stat()
    if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise RuntimeError("service_credential_permissions_invalid")
    mapping = json.loads(path.read_text(encoding="utf-8"))
    result = {actor: mapping[actor] for actor, _user, _uid in ACTORS}
    for value in result.values():
        if (not isinstance(value, str) or not value or len(value.encode()) > 4095
                or any(character.isspace() for character in value)):
            raise RuntimeError("actor_credential_invalid")
    return result


def install() -> None:
    os.umask(0o077)
    preflight()
    assets = {
        Path("/usr/local/bin/snotes-cloud-read"): SOURCE / "snotes-cloud-read",
        Path("/usr/local/bin/snotes-reminder-actor"): SOURCE / "ai-access/snotes-reminder-actor",
        ROOT / "ai-access/reminder_actor.py": SOURCE / "ai-access/reminder_actor.py",
        ROOT / "ai-access/remote_reminder_client.py": SOURCE / "ai-access/remote_reminder_client.py",
        ROOT / "ai-access/verify_actor_access.py": SOURCE / "ai-access/verify_actor_access.py",
        ROOT / "ai-access/verify_fictional_notes.py": SOURCE / "ai-access/verify_fictional_notes.py",
        ROOT / "ai-access/install.py": SOURCE / "ai-access/install.py",
    }
    for source in assets.values():
        regular(source)
    section = (SOURCE / "ai-access/INSTALL.md").read_text(encoding="utf-8")
    if not section.startswith(MARKER.lstrip("\n")):
        raise RuntimeError("step09_document_marker_invalid")
    document = (ROOT / "INSTALL.md").read_text(encoding="utf-8")
    if MARKER in document:
        document = document.split(MARKER, 1)[0]
    document = document.rstrip() + "\n\n" + section
    tokens = provision_tokens()
    directories = [(ROOT / "ai-access", 0, 0, 0o755),
                   (ROOT / "actor-credentials", 0, 0, 0o711)]
    token_paths = {}
    for actor, _user, uid in ACTORS:
        directories.append((ROOT / "actor-credentials" / actor, 0, uid, 0o750))
        token_paths[actor] = ROOT / "actor-credentials" / actor / "reminder-token"
    targets = [*assets, ROOT / "INSTALL.md", *token_paths.values()]
    for path in [*targets, *(item[0] for item in directories)]:
        no_symlink_parents(path)
        if path.exists():
            if path in targets:
                regular(path)
            elif not path.is_dir():
                raise RuntimeError("directory_required")
    backup = Path(tempfile.mkdtemp(prefix="step09-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-",
                                   dir="/root/greencloud-backups"))
    metadata = []
    for path in [*targets, *(item[0] for item in directories)]:
        if path.exists():
            info = path.stat()
            metadata.append({"path": str(path), "exists": True, "uid": info.st_uid,
                             "gid": info.st_gid, "mode": stat.S_IMODE(info.st_mode),
                             "acl": {key: os.getxattr(path, key).hex() for key in os.listxattr(path)
                                     if key.startswith("system.posix_acl_")}})
            if path in targets:
                saved = backup / "files" / path.relative_to("/")
                saved.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                shutil.copy2(path, saved)
        else:
            metadata.append({"path": str(path), "exists": False})
    (backup / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    for path, uid, gid, mode in directories:
        path.mkdir(exist_ok=True)
        os.chown(path, uid, gid)
        for key in os.listxattr(path):
            if key.startswith("system.posix_acl_"):
                os.removexattr(path, key)
        path.chmod(mode)
    for target, source in assets.items():
        replace_file(target, source.read_bytes(), 0, 0, 0o755 if target.parent == Path("/usr/local/bin") else 0o644)
    replace_file(ROOT / "INSTALL.md", document.encode(), 0, 0, 0o644)
    for actor, _user, uid in ACTORS:
        replace_file(token_paths[actor], (tokens[actor] + "\n").encode(), uid, uid, 0o400)
    print("STEP09_BACKUP=" + str(backup))
    print("STEP09_ASSETS_DEPLOYED=PASS")


def replace_file(target: Path, content: bytes, uid: int, gid: int, mode: int) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".step09-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchown(stream.fileno(), uid, gid)
            os.fchmod(stream.fileno(), mode)
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        install()
    except Exception:
        print("STEP09_INSTALL=FAIL")
        raise SystemExit(2)
