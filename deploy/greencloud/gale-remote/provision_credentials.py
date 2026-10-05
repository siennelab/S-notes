"""Create independent OAuth secrets on GreenCloud, with no secret output."""
from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
import secrets
import socket
import sys

from cryptography.fernet import Fernet


DESTINATION = Path("/root/greencloud-secrets/gale-remote-oauth.json")


def main() -> None:
    if os.geteuid() != 0 or socket.gethostname() != "optimal-living":
        raise RuntimeError("greencloud_root_required")
    if DESTINATION.exists():
        raise RuntimeError("credentials_already_exist")
    if sys.argv[1:] == ["--stdin"]:
        # For an approved private pipe directly into SSH. No local secret file,
        # command-line credential or production test-auth path is introduced.
        data = sys.stdin.buffer.read(16385)
        if len(data) > 16384:
            raise RuntimeError("credential_input_too_large")
        incoming = json.loads(data)
        client_id = incoming["client_id"].strip()
        client_secret = incoming["client_secret"].strip()
        subject = incoming["subject"].strip()
    elif not sys.argv[1:]:
        client_id = input("New GreenCloud Auth0 client ID: ").strip()
        client_secret = getpass.getpass("New GreenCloud Auth0 client secret (hidden): ").strip()
        subject = input("Exact allowed Auth0 user ID: ").strip()
    else:
        raise RuntimeError("arguments_invalid")
    if not all((client_id, client_secret, subject)) or any(
        any(c.isspace() for c in value) for value in (client_id, client_secret, subject)
    ):
        raise RuntimeError("credential_input_invalid")
    values = {
        "auth0_client_id": client_id,
        "auth0_client_secret": client_secret,
        "allowed_subjects": [subject],
        "storage_encryption_key": Fernet.generate_key().decode("ascii"),
        "jwt_signing_key": secrets.token_urlsafe(64),
    }
    os.umask(0o077)
    with DESTINATION.open("x", encoding="utf-8") as output:
        json.dump(values, output)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    print("GREENCLOUD_OAUTH_CREDENTIALS_CREATED=PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("OAuth credential provisioning failed; no secret output.")
