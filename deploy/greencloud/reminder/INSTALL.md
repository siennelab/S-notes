# S-notes GreenCloud Reminder staging — Step 6

This deployment adds a staging-only Reminder stack. It never imports the Mac
Reminder database and does not change Reminder authority.

## Fixed runtime contract

| Item | Value |
| --- | --- |
| service identity | existing `snotes` UID/GID 2006 |
| Python | `/srv/snotes/runtime/.venv/bin/python` |
| isolated source | `/srv/snotes/reminder-runtime` |
| database | `/srv/snotes/data/reminder.db`, `snotes:snotes`, 0600 |
| API | `127.0.0.1:8766` |
| public site | `reminder.ke-den.de` |
| scheduler | every five minutes, persistent |
| business timezone | `Asia/Shanghai` (server OS remains UTC) |
| ntfy | `https://ntfy.ke-den.de`, topic `s-step06` |
| backups | `/srv/snotes/backups/reminder`, latest plus 14 timestamped copies |

The API service loads `/root/greencloud-secrets/reminder-api.json` as systemd
credential `reminder-api.json` and the authenticated ntfy token as
`reminder-ntfy-token`, because the owner API includes the explicit test-push
command. The JSON maps the fixed identities `owner`, `yan`, `claude-code`, and
`clawbot` to independent tokens. The scheduler loads only
`/root/greencloud-secrets/reminder-ntfy-token`. A missing or invalid credential
prevents the affected service from running; there is no public ntfy fallback.

## Install

Build the source package on the Mac from this branch:

```sh
.venv/bin/python scripts/package_greencloud_reminder.py /private/tmp/snotes-reminder-step06.tar.gz
```

Transfer that archive and only the dedicated backup public key. In the private
remote staging directory, name the key `backup-transport.pub`, unpack the
archive, and run the installer as root. Credential and ntfy-user preparation is
separate and must already be complete.

```sh
STEP06_BACKUP_DIR=/root/greencloud-backups/step06-20260929T083108Z \
    sh deploy/greencloud/reminder/install-first.sh
```

`STEP06_BACKUP_DIR` is optional. When supplied, it must already exist under
`/root/greencloud-backups/step06-*`; the installer adds a new
`reminder-install` subdirectory without replacing existing credential or ntfy
backups. Without it, the installer creates a new timestamped directory there.

The first-install-only installer refuses existing Step 6 paths, an existing
site marker, or an already-authorized backup key. It initializes only fictional
staging rows, makes an initial SQLite backup, starts the localhost API and both
timers, validates the candidate Caddyfile, confirms the old Caddyfile is an
exact byte prefix, then reloads Caddy. It never restarts Caddy and never edits
the existing `ntfy.ke-den.de` block.

Cloudflare needs exactly:

```text
A reminder.ke-den.de -> <GreenCloud public IPv4>, DNS only
```

## Read-only off-VPS backup

The dedicated public key is appended to the existing `snotes-publish`
`authorized_keys` with
`restrict,command="/usr/local/sbin/snotes-reminder-backup-ssh-entry"`. Any
requested SSH command is rejected. The sudo rule permits exactly one
no-argument wrapper as `snotes`; that wrapper streams only the fixed
`reminder-latest.db` through `src.reminder_cloud export-backup`.

On the Mac, keep the private key at mode 0600 and pull without a remote command:

```sh
ssh -i backup-transport -T optimal > reminder-latest.db
```

Store the result under
`~/Library/Application Support/S-notes/reminder-cloud-backups/`, then open it
with SQLite, run `PRAGMA integrity_check`, and compare staging item counts with
the source. Automated periodic Mac pulls remain outside Step 6.

## Installed systemd units

- `snotes-reminder-api.service`
- `snotes-reminder-scheduler.service` and `.timer`
- `snotes-reminder-backup.service` and `.timer`

The service units run as `snotes` with a 0077 umask and a read-only system view;
only the Reminder data or backup directories needed by each command are
writable. `/srv/snotes/data` is 0700, so `claude` and `openclaw` cannot traverse
to `reminder.db`.

## Rollback

Run `/srv/snotes/rollback-step06.sh` as root. It stops and disables only the new
units, removes the exact dedicated backup-key lines, removes the marked Caddy
site from the current file, validates it, and reloads Caddy. It removes only the
`snotes-reminder-staging` ntfy user, then moves installed wrappers, sudoers,
and units into the Step 6 backup directory.

Rollback never restores the whole old Caddyfile or ntfy auth database over
later changes. The Reminder runtime, staging database, generated backups,
credentials, deployment state, and rollback script remain for recovery or
forensic comparison. Existing Notes mirror access and its receiver are not
modified.
