#!/usr/bin/env python3
"""Package only the read MCP/receiver Python dependency closure and deploy scripts."""
import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile


ROOT = Path(__file__).resolve().parents[1]


def source_closure(entrypoints=None):
    pending = list(entrypoints or ["src.cloud_read_mcp", "src.snotes.storage.cloud_mirror_receiver"])
    files = set()
    while pending:
        module = pending.pop()
        path = ROOT.joinpath(*module.split(".")).with_suffix(".py")
        if not path.is_file():
            path = ROOT.joinpath(*module.split("."), "__init__.py")
        if not path.is_file() or path in files:
            continue
        files.add(path)
        for parent in path.parents:
            if parent == ROOT:
                break
            init = parent / "__init__.py"
            if init.is_file():
                files.add(init)
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src."):
                pending.append(node.module)
            elif isinstance(node, ast.Import):
                pending.extend(item.name for item in node.names if item.name.startswith("src."))
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    sources = source_closure()
    assets = {p for p in (ROOT / "deploy/greencloud").iterdir() if p.is_file()}
    manifest = {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "files": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(sources | assets)},
    }
    data = json.dumps(manifest, indent=2).encode()
    with tarfile.open(args.output, "w:gz") as archive:
        for path in sorted(sources | assets):
            archive.add(path, arcname=str(path.relative_to(ROOT)), recursive=False)
        member = tarfile.TarInfo("source-manifest.json")
        member.size = len(data)
        member.mode = 0o644
        archive.addfile(member, io.BytesIO(data))
    args.output.chmod(0o600)
    print(f"PACKAGED_SOURCE_FILES={len(sources)}")


if __name__ == "__main__":
    main()
