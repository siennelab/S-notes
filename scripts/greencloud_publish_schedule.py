#!/usr/bin/env python3
"""Render the separate, repo-side five-minute publisher LaunchAgent.

The agent runs a pinned `git archive` export (see deploy/greencloud/INSTALL.md),
never a developer checkout. Loading/unloading is an explicit deployment
operation. No App bundle changes.
"""
import argparse
import os
from pathlib import Path
import plistlib
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    python = args.python.absolute()
    source = args.source.resolve()
    if not python.is_file() or not (source / "src/actor_mirror_publish.py").is_file():
        parser.error("publisher runtime missing")
    if (source / ".git").exists() or not (source / "REVISION").is_file():
        parser.error("source must be a pinned export with REVISION, not a checkout")
    target = args.destination.absolute()
    if target.exists() or target.is_symlink():
        parser.error("destination exists; preserve it before installing")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": "com.snotes.greencloud-mirror-publish",
        "ProgramArguments": [str(python), "-B", "-m", "src.actor_mirror_publish", "publish"],
        "WorkingDirectory": str(source),
        "RunAtLoad": True,
        "StartInterval": 300,
        "ProcessType": "Background",
        "EnvironmentVariables": {"PYTHONDONTWRITEBYTECODE": "1"},
    }
    fd, name = tempfile.mkstemp(prefix=".greencloud-plist-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            plistlib.dump(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    print(target)


if __name__ == "__main__":
    main()
