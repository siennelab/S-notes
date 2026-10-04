"""Package only Step 10's runtime dependency closure and deployment assets."""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[3]


def sources() -> set[Path]:
    pending = ["src.cloud_read_mcp", "src.cloud_outbox"]
    result = set()
    while pending:
        module = pending.pop()
        path = ROOT.joinpath(*module.split(".")).with_suffix(".py")
        if not path.is_file():
            path = ROOT.joinpath(*module.split("."), "__init__.py")
        if not path.is_file():
            raise RuntimeError("runtime_module_missing")
        if path in result:
            continue
        result.add(path)
        for parent in path.parents:
            if parent == ROOT:
                break
            if (parent / "__init__.py").is_file():
                result.add(parent / "__init__.py")
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src."):
                pending.append(node.module)
            elif isinstance(node, ast.Import):
                pending.extend(item.name for item in node.names if item.name.startswith("src."))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    names = ("INSTALL.md", "backup.py", "install.py", "package.py", "transport.py",
             "verify_outbox.py", "snotes-notes-outbox.service", "notes-outbox.conf",
             "snotes-notes-outbox-backup", "snotes-notes-outbox-transport",
             "snotes-notes-outbox.sudoers")
    assets = {Path(__file__).parent / name for name in names}
    assets.add(ROOT / "deploy/greencloud/snotes-mirror-ssh-entry")
    files = sources() | assets
    content = {str(path.relative_to(ROOT)): path.read_bytes() for path in sorted(files)}
    manifest = {
        "baseline_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in content.items()},
    }
    content["step10-source-manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    with args.output.open("xb") as output:
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            for name, data in content.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                member.mode = 0o644
                member.uid = member.gid = 0
                member.uname = member.gname = "root"
                archive.addfile(member, io.BytesIO(data))
    args.output.chmod(0o600)
    print("STEP10_PACKAGE_FILES=" + str(len(files)))


if __name__ == "__main__":
    main()
