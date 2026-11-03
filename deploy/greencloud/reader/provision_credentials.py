#!/usr/bin/env python3
"""Root-only, hidden Reader credential entry; never logs secret or subject."""
import getpass
import json
import os
from pathlib import Path
import socket
import tempfile


def atomic_json(path, document, mode):
    descriptor, name = tempfile.mkstemp(prefix=".reader-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(document, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    if os.geteuid() != 0:
        raise SystemExit("root required")
    os.umask(0o077)
    target = Path("/root/greencloud-secrets/reader-oidc.json")
    public = Path("/srv/snotes/reader/config.json")
    if target.exists() or public.exists():
        raise SystemExit("Reader credentials already exist; preserve before changing")
    client_id = input("Reader client ID: ").strip()
    secret = getpass.getpass("Reader client secret (hidden): ").strip()
    # Reuse only the already-selected stable human identity, never OAuth secrets.
    existing = json.loads(Path("/root/greencloud-secrets/claude-web-remote-oauth.json").read_text())
    allowed = existing["allowed_subjects"]
    if not isinstance(allowed, list) or len(allowed) != 1 or not isinstance(allowed[0], str):
        raise SystemExit("Existing human allowlist is not singular; select S explicitly")
    if not client_id or not secret or client_id == existing["auth0_client_id"] or secret == existing["auth0_client_secret"]:
        raise SystemExit("Independent Reader application required")
    port = int(input("Confirmed free Reader loopback port: "))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))
    target.parent.mkdir(mode=0o700, exist_ok=True)
    atomic_json(target, {"client_secret": secret, "allowed_sub": allowed[0]}, 0o600)
    atomic_json(public, {"issuer": "https://dev-se55o7e1ug44w2z1.us.auth0.com/", "client_id": client_id, "port": port}, 0o644)
    print("READER_CREDENTIAL=READY (secret and identity hidden)")


if __name__ == "__main__":
    main()
