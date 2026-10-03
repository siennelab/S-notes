"""Create the approved, single-topic production sender without logging secrets."""
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess


USER = "snotes-reminder-production"
TOPIC = "s-reminder-prod"
TOKEN_FILE = Path("/root/greencloud-secrets/reminder-ntfy-production-token")


def main():
    if os.geteuid() != 0:
        raise RuntimeError("root_required")
    os.umask(0o077)
    if TOKEN_FILE.exists() or TOKEN_FILE.is_symlink():
        raise RuntimeError("production_credential_already_exists")
    ntfy = ["/srv/ntfy/app/ntfy"]
    config = ["--config", "/srv/ntfy/deploy/server.yml"]
    listing = subprocess.run(ntfy + ["user"] + config + ["list"], check=True,
                             capture_output=True, text=True).stdout
    if re.search(r"^user " + USER + r"\s", listing, re.M):
        raise RuntimeError("production_sender_already_exists")
    # Existing deployment denies anonymous/default access. Do not widen it.
    if "no access to any (other) topics (server config)" not in listing:
        raise RuntimeError("default_topic_access_not_denied")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = Path("/root/greencloud-backups") / ("step08c-credentials-" + stamp)
    backup.mkdir(mode=0o700)
    with closing(sqlite3.connect("file:/srv/ntfy/data/auth.db?mode=ro", uri=True)) as source:
        source.execute("PRAGMA query_only=ON")
        with closing(sqlite3.connect(backup / "ntfy-auth.before.db")) as target:
            source.backup(target)
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("auth_backup_invalid")
    subprocess.run(ntfy + ["user"] + config + ["add", USER],
                   env={**os.environ, "NTFY_PASSWORD": secrets.token_urlsafe(48)},
                   check=True, capture_output=True)
    subprocess.run(ntfy + ["access"] + config + [USER, TOPIC, "write-only"],
                   check=True, capture_output=True)
    acl = subprocess.run(ntfy + ["access"] + config + [USER], check=True,
                         capture_output=True, text=True).stdout
    permissions = [line.strip() for line in acl.splitlines() if "access to topic " in line]
    if permissions != ["- write-only access to topic " + TOPIC]:
        raise RuntimeError("production_acl_not_exact")
    token = subprocess.run(ntfy + ["token"] + config + ["add", "--label=step08c-production", USER],
                           check=True, capture_output=True, text=True)
    values = set(re.findall(r"\btk_[A-Za-z0-9]+\b", token.stdout + token.stderr))
    if len(values) != 1:
        raise RuntimeError("token_output_unrecognized")
    with TOKEN_FILE.open("x") as output:
        output.write(values.pop() + "\n")
    (backup / "credential-setup.json").write_text(json.dumps({
        "user": USER, "topic": TOPIC, "created_utc": stamp,
        "credential_path": str(TOKEN_FILE),
        "rollback": "Stop production consumers; remove only this sender/token if authorized. Never restore the whole auth backup over later changes.",
    }, indent=2))
    print("PRODUCTION_SENDER_SINGLE_TOPIC_ACL=PASS")
    print("CREDENTIAL_ROLLBACK_ASSET=" + str(backup))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # CLI/library errors may embed sensitive data: expose only the class.
        print("PRODUCTION_CREDENTIAL_SETUP_FAILED=" + type(error).__name__)
        raise SystemExit(2)
