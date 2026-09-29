"""Root-only first-install credential setup on GreenCloud; never prints secrets."""
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess


def main():
    if os.geteuid() != 0:
        raise SystemExit("root_required")
    os.umask(0o077)
    secret_dir = Path('/root/greencloud-secrets')
    api_file = secret_dir / 'reminder-api.json'
    token_file = secret_dir / 'reminder-ntfy-token'
    if api_file.exists() or token_file.exists():
        raise SystemExit('step06_credentials_already_exist')
    ntfy = ['/srv/ntfy/app/ntfy']
    config = ['--config', '/srv/ntfy/deploy/server.yml']
    user = 'snotes-reminder-staging'
    listing = subprocess.run(ntfy + ['user'] + config + ['list'], check=True, capture_output=True, text=True)
    if re.search(r'^user ' + user + r'\s', listing.stdout, re.M):
        raise SystemExit('staging_ntfy_user_already_exists')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup_dir = Path('/root/greencloud-backups') / ('step06-' + stamp)
    backup_dir.mkdir(mode=0o700)
    with closing(sqlite3.connect('file:/srv/ntfy/data/auth.db?mode=ro', uri=True)) as source:
        with closing(sqlite3.connect(backup_dir / 'ntfy-auth.before.db')) as target:
            source.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('auth_backup_integrity_failed')
    secret_dir.mkdir(mode=0o700, exist_ok=True)
    env = {**os.environ, 'NTFY_PASSWORD': secrets.token_urlsafe(48)}
    subprocess.run(ntfy + ['user'] + config + ['add', user], env=env, check=True, capture_output=True)
    subprocess.run(ntfy + ['access'] + config + [user, 's-step06', 'write-only'], check=True, capture_output=True)
    result = subprocess.run(ntfy + ['token'] + config + ['add', '--label=step06-staging', user], check=True, capture_output=True, text=True)
    found = re.findall(r'\btk_[A-Za-z0-9]+\b', result.stdout + result.stderr)
    if len(set(found)) != 1:
        raise RuntimeError('token_output_unrecognized_secret_not_printed')
    with token_file.open('x') as output:
        output.write(found[0] + '\n')
    with api_file.open('x') as output:
        json.dump({actor: secrets.token_urlsafe(48) for actor in ('owner', 'yan', 'claude-code', 'clawbot')}, output)
    (backup_dir / 'credential-setup.json').write_text(json.dumps({'ntfy_user': user, 'topic': 's-step06', 'created_at': stamp}))
    print('STEP06_BACKUP_DIR=' + str(backup_dir))
    print('STAGING_CREDENTIALS_READY=YES')


if __name__ == '__main__':
    main()
