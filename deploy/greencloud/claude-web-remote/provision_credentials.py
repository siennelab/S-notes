"""Provision only Claude Web's independent OAuth credential; never print secrets."""
from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
import secrets
import socket
import sys

from cryptography.fernet import Fernet


DESTINATION = Path("/root/greencloud-secrets/claude-web-remote-oauth.json")
GALE_CREDENTIAL = Path("/root/greencloud-secrets/gale-remote-oauth.json")


def main() -> None:
    if os.geteuid() != 0 or socket.gethostname() != "optimal-living":
        raise RuntimeError("greencloud_root_required")
    if DESTINATION.exists() or DESTINATION.is_symlink():
        raise RuntimeError("credentials_already_exist")
    if sys.argv[1:] == ["--stdin"]:
        raw = sys.stdin.buffer.read(16385)
        if len(raw) > 16384:
            raise RuntimeError("credential_input_too_large")
        incoming = json.loads(raw)
        client_id = incoming["client_id"]
        client_secret = incoming["client_secret"]
        subject = incoming["subject"]
    elif not sys.argv[1:]:
        client_id = input("Independent Claude Web Auth0 client ID: ").strip()
        client_secret = getpass.getpass("Independent client secret (hidden): ").strip()
        subject = getpass.getpass("Exact allowed Auth0 user ID (hidden): ").strip()
    else:
        raise RuntimeError("arguments_invalid")
    values = (client_id, client_secret, subject)
    if any(not isinstance(value, str) or not value or any(c.isspace() for c in value)
           for value in values):
        raise RuntimeError("credential_input_invalid")
    # This root-only comparison enforces separation. No Gale value is copied,
    # transmitted, or made readable by the Claude service.
    gale = json.loads(GALE_CREDENTIAL.read_text())
    if (client_id == gale["auth0_client_id"]
            or client_secret == gale["auth0_client_secret"]):
        raise RuntimeError("independent_oauth_client_required")
    document = {
        "auth0_client_id": client_id,
        "auth0_client_secret": client_secret,
        "allowed_subjects": [subject],
        "storage_encryption_key": Fernet.generate_key().decode("ascii"),
        "jwt_signing_key": secrets.token_urlsafe(64),
    }
    os.umask(0o077)
    with DESTINATION.open("x", encoding="utf-8") as output:
        json.dump(document, output)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    print("CLAUDE_WEB_OAUTH_CREDENTIAL_CREATED=PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Claude Web credential provisioning failed; no secret output.")
