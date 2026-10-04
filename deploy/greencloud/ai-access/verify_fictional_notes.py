"""Actual actor UIDs read disposable mirrors from the existing fixture builder.

Run as root with a fresh /tmp/snotes-step09-fictional-* directory containing
tests.gc35_step05_cloud_fixture.build_fixtures output. Never swaps live mirrors.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

sys.path.insert(0, "/srv/snotes/runtime")

ACTORS = (("claude-code", "claude", 2001), ("clawbot", "openclaw", 2002))


def actor_probe(root: Path, ids: dict) -> bool:
    from src.cloud_read_mcp import binding_for_effective_uid
    from src.snotes.application.cloud_read_service import CloudReadService
    from src.snotes.storage.cloud_read import SQLiteCloudMirrorReader
    from src.snotes.transport.mcp.cloud_read_server import call_cloud_read_tool

    binding = binding_for_effective_uid(os.geteuid())
    own = "claude_only" if binding.actor == "claude-code" else "clawbot_only"
    other = "clawbot_only" if own == "claude_only" else "claude_only"
    service = CloudReadService(SQLiteCloudMirrorReader(root / "v1" / (binding.actor + ".db")))
    results = []
    for tool in ("get_note", "get_note_markdown"):
        for key in ("common_full", own):
            results.append(call_cloud_read_tool(service, tool, {"note_id": ids[key]})["status"] == "ok")
        for key in ("hidden", other, "unauthorized_gale_only"):
            results.append(call_cloud_read_tool(service, tool, {"note_id": ids[key]})["status"] == "not_found")
    results.append(call_cloud_read_tool(service, "search_notes", {"query": "Hidden fixture"})["result_count"] == 0)
    results.append(call_cloud_read_tool(service, "delete_note", {"note_id": ids[own]})["status"] == "failed")
    print("FICTIONAL_NOTES_ACTOR=" + binding.actor + ":" + ("PASS" if all(results) else "FAIL"))
    return all(results)


def verify(root: Path) -> bool:
    if os.geteuid() != 0:
        raise RuntimeError("root_required")
    if root.parent != Path("/tmp") or not root.name.startswith("snotes-step09-fictional-"):
        raise RuntimeError("disposable_fixture_required")
    for path in (root, root / "v1", root / "manifest.json"):
        if path.is_symlink() or path.stat().st_uid != 0:
            raise RuntimeError("root_owned_fixture_required")
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest.get("fixture") != "gc35-step05-fictional-v1-v2" or manifest.get("source_database_uploaded") is not False:
        raise RuntimeError("fictional_manifest_required")
    root.chmod(0o711)
    (root / "v1").chmod(0o711)
    for actor, _user, uid in ACTORS:
        path = root / "v1" / (actor + ".db")
        if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
            raise RuntimeError("fixture_file_invalid")
        expected = manifest["versions"]["v1"][actor]["file_sha256"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError("fictional_fixture_hash_mismatch")
        os.chown(path, 0, uid)
        path.chmod(0o640)
    results = []
    for actor, user, _uid in ACTORS:
        completed = subprocess.run(
            ["/usr/sbin/runuser", "-u", user, "--", "/usr/bin/env", "-i", "PATH=/usr/bin:/bin",
             "/srv/snotes/runtime/.venv/bin/python", "-I", "-B", str(Path(__file__).resolve()),
             "--actor", str(root)], input=json.dumps(manifest["ids"]).encode(),
            capture_output=True, timeout=30,
        )
        ok = completed.returncode == 0
        print("FICTIONAL_NOTES_UID=" + user + ":" + ("PASS" if ok else "FAIL"))
        results.append(ok)
    return all(results)


if __name__ == "__main__":
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "--actor":
            result = actor_probe(Path(sys.argv[2]), json.loads(sys.stdin.buffer.read(4096)))
        elif len(sys.argv) == 2:
            result = verify(Path(sys.argv[1]))
        else:
            raise RuntimeError("fixture_argument_required")
        raise SystemExit(0 if result else 1)
    except Exception:
        print("FICTIONAL_NOTES=FAIL")
        raise SystemExit(2)
