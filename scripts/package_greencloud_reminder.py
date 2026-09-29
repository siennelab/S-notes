#!/usr/bin/env python3
"""Package the Cloud Reminder source closure and Step 6 deployment assets."""

import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile


ROOT = Path(__file__).resolve().parents[1]
ROOT_MODULES = (
    "src.reminder_cloud",
    # reminder_cloud imports the HTTP transport lazily for the serve command.
    "src.snotes.transport.reminder_http",
)


def _module_path(module: str) -> Path | None:
    path = ROOT.joinpath(*module.split(".")).with_suffix(".py")
    if path.is_file():
        return path
    package = ROOT.joinpath(*module.split("."), "__init__.py")
    return package if package.is_file() else None


def _relative_base(module: str, path: Path, level: int) -> list[str]:
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    parts = package.split(".") if package else []
    if level > len(parts):
        return []
    return parts[: len(parts) - level + 1]


def _imports(module: str, path: Path) -> set[str]:
    dependencies: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            dependencies.update(alias.name for alias in node.names if alias.name.startswith("src."))
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            parts = _relative_base(module, path, node.level)
            if not parts:
                continue
            if node.module:
                parts.extend(node.module.split("."))
            base = ".".join(parts)
        else:
            base = node.module or ""
        if not base.startswith("src"):
            continue
        dependencies.add(base)
        for alias in node.names:
            if alias.name != "*":
                dependencies.add(f"{base}.{alias.name}")
    return dependencies


def source_closure() -> set[Path]:
    pending = list(ROOT_MODULES)
    files: set[Path] = set()
    visited: set[str] = set()
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
        pending.extend(sorted(_imports(module, path) - visited))
    missing = [module for module in ROOT_MODULES if _module_path(module) is None]
    if missing:
        raise SystemExit(f"missing required Reminder module(s): {', '.join(missing)}")
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    sources = source_closure()
    asset_root = ROOT / "deploy/greencloud/reminder"
    assets = {path for path in asset_root.rglob("*") if path.is_file()}
    packaged = sources | assets
    manifest = {
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "entrypoint": "src.reminder_cloud",
        "files": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(packaged)
        },
    }
    data = json.dumps(manifest, indent=2, sort_keys=True).encode()
    with tarfile.open(args.output, "w:gz") as archive:
        for path in sorted(packaged):
            archive.add(path, arcname=str(path.relative_to(ROOT)), recursive=False)
        member = tarfile.TarInfo("source-manifest.json")
        member.size = len(data)
        member.mode = 0o644
        archive.addfile(member, io.BytesIO(data))
    args.output.chmod(0o600)
    print(f"PACKAGED_SOURCE_FILES={len(sources)}")
    print(f"PACKAGED_DEPLOY_FILES={len(assets)}")


if __name__ == "__main__":
    main()
