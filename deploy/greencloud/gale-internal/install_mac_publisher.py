"""Switch the existing Mac publisher to a content-pinned source, reusing its venv."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile


def install(source: Path):
    os.umask(0o077)
    manifest_path = source / "gale-internal-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    files = manifest["publisher_sources"]
    verified = {}
    for name in files:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("package_path_invalid")
        path = source / relative
        if path.is_symlink():
            raise RuntimeError("source_symlink_invalid")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != manifest["files"][name]:
            raise RuntimeError("source_hash_mismatch")
        verified[name] = data
    pin = hashlib.sha256(json.dumps({name: manifest["files"][name] for name in files}, sort_keys=True).encode()).hexdigest()
    runtime = Path.home() / "Library/Application Support/S-notes/runtime/greencloud-publisher"
    plist_path = Path.home() / "Library/LaunchAgents/com.snotes.greencloud-mirror-publish.plist"
    payload = plistlib.loads(plist_path.read_bytes())
    python = Path(payload["ProgramArguments"][0])
    if (payload["Label"] != "com.snotes.greencloud-mirror-publish"
            or payload["ProgramArguments"][1:] != ["-B", "-m", "src.actor_mirror_publish", "publish"]
            or not python.is_file() or runtime not in python.parents):
        raise RuntimeError("existing_publisher_contract_invalid")
    destination = runtime / ("gale-internal-" + pin[:16])
    if destination.is_symlink():
        raise RuntimeError("runtime_symlink_invalid")
    if not destination.exists():
        destination.mkdir(mode=0o700)
    for name, data in verified.items():
        target = destination / name
        if target.exists() and target.read_bytes() != data:
            raise RuntimeError("pinned_source_conflict")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not target.exists():
            target.write_bytes(data)
            target.chmod(0o600)
    (destination / "REVISION").write_text("gale-internal-" + pin + "\n")
    (destination / "source-manifest.json").write_bytes(manifest_path.read_bytes())
    recovery = runtime / "gale-internal-recovery"
    recovery.mkdir(mode=0o700, exist_ok=True)
    saved = recovery / ("publisher-before-" + pin[:16] + ".plist")
    if not saved.exists():
        shutil.copy2(plist_path, saved)
    payload["WorkingDirectory"] = str(destination)
    label = "gui/" + str(os.getuid()) + "/com.snotes.greencloud-mirror-publish"
    fd, name = tempfile.mkstemp(prefix=".gale-publisher-", dir=plist_path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            plistlib.dump(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
        subprocess.run(["launchctl", "bootout", label], check=True)
        os.replace(name, plist_path)
        try:
            subprocess.run(["launchctl", "bootstrap", "gui/" + str(os.getuid()), str(plist_path)], check=True)
        except Exception:
            shutil.copy2(saved, plist_path)
            subprocess.run(["launchctl", "bootstrap", "gui/" + str(os.getuid()), str(plist_path)], check=True)
            raise
    finally:
        Path(name).unlink(missing_ok=True)
    print("MAC_PUBLISHER_SOURCE=" + str(destination))
    print("MAC_PUBLISHER_REUSED_PYTHON=" + str(python))
    print("MAC_PUBLISHER_RECOVERY=" + str(saved))


if __name__ == "__main__":
    install(Path(sys.argv[1]).resolve())
