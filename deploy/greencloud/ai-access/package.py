"""Package only Step 9 assets and the unchanged existing Reminder client."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile


ROOT = Path(__file__).resolve().parents[3]
ASSET_ROOT = ROOT / "deploy/greencloud"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    names = ("INSTALL.md", "install.py", "reminder_actor.py", "snotes-reminder-actor",
             "verify_actor_access.py", "verify_fictional_notes.py")
    sources = [(ASSET_ROOT / "ai-access" / name, "deploy/greencloud/ai-access/" + name) for name in names]
    sources += [(ASSET_ROOT / "snotes-cloud-read", "deploy/greencloud/snotes-cloud-read"),
                (ROOT / "src/snotes/transport/remote_reminder_client.py",
                 "deploy/greencloud/ai-access/remote_reminder_client.py")]
    data = [(name, path.read_bytes()) for path, name in sources]
    manifest = {name: hashlib.sha256(content).hexdigest() for name, content in data}
    data.append(("asset-manifest.json", (json.dumps(manifest, indent=2) + "\n").encode()))
    # Archive ownership is portable; no Mac identity or machine metadata leaks.
    with args.output.open("xb") as output:
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            for name, content in data:
                member = tarfile.TarInfo(name)
                member.size = len(content)
                member.mode = 0o644
                member.uid = member.gid = 0
                member.uname = member.gname = "root"
                archive.addfile(member, io.BytesIO(content))
    args.output.chmod(0o600)
    print("STEP09_DEPLOY_ASSETS=" + str(len(sources)))
    print("STEP09_PACKAGE_SHA256=" + hashlib.sha256(args.output.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
