"""Install Step 10's small Cloud runtime addition, retaining exact rollback files."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import stat
import subprocess
import tempfile

ROOT = Path("/srv/snotes")
SOURCE = Path(__file__).resolve().parents[3]
ASSETS = SOURCE / "deploy/greencloud/outbox"
MARKER = "\n## Step 10 — Notes proposal outbox\n"


def require_safe_path(path: Path) -> None:
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise RuntimeError("symlink_path_rejected")
    if path.exists() and not path.is_dir() and not stat.S_ISREG(path.lstat().st_mode):
        raise RuntimeError("regular_path_required")


def replace_file(target: Path, data: bytes, mode: int) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".step10-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchown(stream.fileno(), 0, 0)
            os.fchmod(stream.fileno(), mode)
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)


def install() -> None:
    os.umask(0o077)
    if os.geteuid() != 0 or socket.gethostname() != "optimal-living":
        raise RuntimeError("greencloud_root_required")
    for name, uid in (("snotes", 2006), ("claude", 2001), ("openclaw", 2002)):
        account = pwd.getpwnam(name)
        if account.pw_uid != uid or account.pw_gid != uid:
            raise RuntimeError("identity_mismatch")
    for path, uid, gid, mode in ((ROOT / "data", 2006, 2006, 0o700),
                                 (ROOT / "runtime", 0, 0, 0o755)):
        require_safe_path(path)
        info = path.stat()
        if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (uid, gid, mode):
            raise RuntimeError("sealed_permission_mismatch")
        if any(key.startswith("system.posix_acl_") for key in os.listxattr(path)):
            raise RuntimeError("sealed_acl_mismatch")
    manifest_path = SOURCE / "step10-source-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    targets: dict[Path, tuple[bytes, int]] = {}
    for name, expected in manifest["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("package_path_invalid")
        path = SOURCE / relative
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError("package_hash_mismatch")
        if relative.parts[0] == "src":
            targets[ROOT / "runtime" / relative] = (data, 0o644)
    for name in ("backup.py", "transport.py", "verify_outbox.py", "install.py", "package.py", "INSTALL.md"):
        targets[ROOT / "outbox-deploy" / name] = ((ASSETS / name).read_bytes(), 0o644)
    for name in ("snotes-notes-outbox-transport", "snotes-notes-outbox-backup"):
        targets[Path("/usr/local/sbin") / name] = ((ASSETS / name).read_bytes(), 0o755)
    targets[Path("/usr/local/sbin/snotes-mirror-ssh-entry")] = (
        (SOURCE / "deploy/greencloud/snotes-mirror-ssh-entry").read_bytes(), 0o755)
    targets[Path("/etc/systemd/system/snotes-notes-outbox.service")] = (
        (ASSETS / "snotes-notes-outbox.service").read_bytes(), 0o644)
    targets[Path("/etc/systemd/system/snotes-reminder-backup.service.d/notes-outbox.conf")] = (
        (ASSETS / "notes-outbox.conf").read_bytes(), 0o644)
    targets[Path("/etc/sudoers.d/snotes-notes-outbox")] = ((ASSETS / "snotes-notes-outbox.sudoers").read_bytes(), 0o440)
    targets[ROOT / "runtime/step10-source-manifest.json"] = (manifest_path.read_bytes(), 0o644)
    document = (ROOT / "INSTALL.md").read_text()
    if MARKER in document:
        document = document.split(MARKER, 1)[0]
    section = (ASSETS / "INSTALL.md").read_text()
    if not section.startswith(MARKER.lstrip("\n")):
        raise RuntimeError("install_marker_invalid")
    targets[ROOT / "INSTALL.md"] = ((document.rstrip() + "\n\n" + section).encode(), 0o644)
    targets[ROOT / "outbox-deploy/deployment-record.json"] = (b"", 0o644)
    # Shared dependency files already installed byte-for-byte need no rewrite
    # and no new recovery copy. Record the full closure in the source manifest.
    for path, (data, mode) in tuple(targets.items()):
        require_safe_path(path)
        if path.is_file():
            info = path.stat()
            if ((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (0, 0, mode)
                    and path.read_bytes() == data):
                del targets[path]
    directories = {target.parent for target in targets}
    directories.add(ROOT / "backups/notes-outbox")
    for path in (*targets, *directories, ROOT / "data/outbox.db"):
        require_safe_path(path)
    subprocess.run(["/usr/sbin/visudo", "-cf", str(ASSETS / "snotes-notes-outbox.sudoers")], check=True)
    backup = Path(tempfile.mkdtemp(prefix="step10-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-",
                                   dir="/root/greencloud-backups"))
    metadata = []
    for path in sorted(set(targets) | directories):
        item = {"path": str(path), "exists": path.exists()}
        if path.exists():
            info = path.stat()
            item.update(uid=info.st_uid, gid=info.st_gid, mode=stat.S_IMODE(info.st_mode))
            item["acl"] = {key: os.getxattr(path, key).hex() for key in os.listxattr(path)
                           if key.startswith("system.posix_acl_")}
            if path in targets:
                saved = backup / "files" / path.relative_to("/")
                saved.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                shutil.copy2(path, saved)
        metadata.append(item)
    (backup / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    for path in sorted(directories):
        for part in reversed((path, *path.parents)):
            if not part.exists():
                part.mkdir(mode=0o755)
                part.chmod(0o755)
    backup_directory = ROOT / "backups/notes-outbox"
    os.chown(backup_directory, 2006, 2006)
    backup_directory.chmod(0o700)
    deployment = {"rollback_directory": str(backup), "baseline_commit": manifest["baseline_commit"],
                  "deployed_files": {str(path): hashlib.sha256(data).hexdigest()
                                     for path, (data, _mode) in targets.items()
                                     if path.name != "deployment-record.json"}}
    targets[ROOT / "outbox-deploy/deployment-record.json"] = ((json.dumps(deployment, indent=2) + "\n").encode(), 0o644)
    for target, (data, mode) in targets.items():
        replace_file(target, data, mode)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemd-analyze", "verify", "/etc/systemd/system/snotes-notes-outbox.service"], check=True)
    subprocess.run(["systemctl", "enable", "snotes-notes-outbox.service"], check=True)
    subprocess.run(["systemctl", "restart", "snotes-notes-outbox.service"], check=True)
    print("STEP10_ROLLBACK=" + str(backup))
    print("STEP10_DEPLOYED_FILES=" + str(len(targets)))


if __name__ == "__main__":
    try:
        install()
    except Exception:
        print("STEP10_INSTALL=FAIL")
        raise SystemExit(2)
