#!/usr/bin/env python3
"""Package the Reader's source dependency closure and local CSS; no user data."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

from package_greencloud_read import ROOT, source_closure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    files = source_closure(["src.remote_reader"])
    files.add(ROOT / "src/snotes/transport/reader.css")
    files.update(path for path in (ROOT / "deploy/greencloud/reader").iterdir() if path.is_file())
    manifest = {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "files": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(files)},
    }
    with tarfile.open(args.output, "w:gz") as archive:
        for path in sorted(files):
            archive.add(path, arcname=str(path.relative_to(ROOT)), recursive=False)
        data = json.dumps(manifest, indent=2).encode()
        member = tarfile.TarInfo("source-manifest.json")
        member.size, member.mode = len(data), 0o644
        archive.addfile(member, io.BytesIO(data))
    args.output.chmod(0o600)
    print(f"READER_SOURCE_FILES={len(files)}")


if __name__ == "__main__":
    main()
