"""Package the internal Gale addition and existing publisher source closure."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.package_greencloud_reminder import _imports, _module_path


def closure(*roots: str) -> set[Path]:
    pending = list(roots)
    visited, files = set(), set()
    while pending:
        module = pending.pop()
        if module in visited:
            continue
        visited.add(module)
        path = _module_path(module)
        if path is None:
            continue
        files.add(path)
        for parent in path.parents:
            if parent == ROOT:
                break
            init = parent / "__init__.py"
            if init.is_file():
                files.add(init)
        pending.extend(_imports(module, path) - visited)
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    runtime = closure("src.greencloud_internal_mcp", "src.cloud_outbox",
                      "src.snotes.storage.cloud_mirror_receiver", "src.reminder_cloud")
    publisher = closure("src.actor_mirror_publish")
    assets = {path for path in Path(__file__).parent.iterdir() if path.is_file()}
    assets.add(ROOT / "deploy/greencloud/ai-access/reminder_actor.py")
    assets.add(ROOT / "deploy/greencloud/outbox/transport.py")
    # Fixtures use existing formal source services on the Mac; source DB stays local.
    fixture_sources = closure("tests.greencloud_gale_fixture")
    files = runtime | publisher | assets | fixture_sources
    content = {str(path.relative_to(ROOT)): path.read_bytes() for path in sorted(files)}
    manifest = {
        "baseline_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in content.items()},
        "runtime_sources": sorted(str(path.relative_to(ROOT)) for path in runtime),
        "publisher_sources": sorted(str(path.relative_to(ROOT)) for path in publisher),
        "reminder_sources": ["src/reminder_cloud.py", "src/snotes/transport/reminder_http.py"],
    }
    content["gale-internal-manifest.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    with args.output.open("xb") as output, tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, data in content.items():
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(data), 0o644
            member.uid = member.gid = 0
            member.uname = member.gname = "root"
            archive.addfile(member, io.BytesIO(data))
    args.output.chmod(0o600)
    print("GALE_PACKAGE_FILES=" + str(len(files)))
    print("GALE_PACKAGE_SHA256=" + hashlib.sha256(args.output.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
