#!/usr/bin/env python3
"""Prepare the runtime venv and render the five-minute publisher LaunchAgent.

The agent runs a pinned `git archive` export (see deploy/greencloud/INSTALL.md)
with a revision-scoped venv beside it, never a developer checkout or its
`.venv`. Loading/unloading is an explicit deployment operation. No App bundle
changes.
"""
import argparse
import os
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile

# Publisher third-party imports; versions come from the export's requirements.txt.
PUBLISHER_REQUIREMENTS = ("markdown-it-py",)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-python", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    base_python = args.base_python.absolute()
    source = args.source.resolve()
    if not base_python.is_file() or not (source / "src/actor_mirror_publish.py").is_file():
        parser.error("publisher runtime missing")
    if (source / ".git").exists() or not (source / "REVISION").is_file():
        parser.error("source must be a pinned export with REVISION, not a checkout")
    is_base = subprocess.run(
        [str(base_python), "-c", "import sys; print(sys.prefix == sys.base_prefix)"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if is_base != "True":
        parser.error("base python must not be a venv")
    target = args.destination.absolute()
    if target.exists() or target.is_symlink():
        parser.error("destination exists; preserve it before installing")
    revision = (source / "REVISION").read_text().strip()
    venv = source.parent / "venvs" / revision
    python = venv / "bin/python"
    if not venv.exists():
        pins = _publisher_pins(source / "requirements.txt")
        if pins is None:
            parser.error("requirements.txt does not pin every publisher dependency")
        venv.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        subprocess.run([str(base_python), "-m", "venv", str(venv)], check=True)
        subprocess.run(
            [str(python), "-m", "pip", "install", "--no-input",
             "--disable-pip-version-check", *pins],
            check=True,
        )
    if not python.is_file() or not (venv / "pyvenv.cfg").is_file():
        parser.error("runtime venv incomplete; preserve it and investigate")
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


def _publisher_pins(requirements):
    if not requirements.is_file():
        return None
    pins = {}
    for line in requirements.read_text().splitlines():
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==(\S+)", line.strip())
        if match and match.group(1).lower() in PUBLISHER_REQUIREMENTS:
            pins[match.group(1).lower()] = line.strip()
    if set(pins) != set(PUBLISHER_REQUIREMENTS):
        return None
    return [pins[name] for name in PUBLISHER_REQUIREMENTS]


if __name__ == "__main__":
    main()
