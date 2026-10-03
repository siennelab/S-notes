# S-notes GreenCloud Reminder staging — Step 6

This deployment adds a staging-only Reminder stack. It never imports the Mac
Reminder database and does not change Reminder authority.

## Fixed runtime contract

| Item | Value |
| --- | --- |
| service identity | existing `snotes` UID/GID 2006 |
| Python | `/srv/snotes/runtime/.venv/bin/python` |
| isolated source | `/srv/snotes/reminder-runtime` |
| staging database | `/srv/snotes/data/reminder.db`, `snotes:snotes`, 0600 |
| formal database (prepared in Step 8B) | `/srv/snotes/data/reminder-production.db`, `snotes:snotes`, 0600 |
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
ssh -o IdentitiesOnly=yes -i backup-transport -T snotes-publish@optimal > reminder-latest.db
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

The installed Step 6 services and timers continue to use the default staging
database at `/srv/snotes/data/reminder.db`. Step 8B migration separately creates
the formal database at `/srv/snotes/data/reminder-production.db`; do not use
`init-staging` for that path. The runtime accepts `--database PATH` so the formal
path can be configured later, but no active unit uses it during Step 8B.
Production API, scheduler, backup, and transport activation are Step 8C work,
using the separate `production/` templates described below. Root-level Step 6
templates remain staging-only for `install-first.sh`.

## Step 8C production cutover

S/G approved `s-reminder-prod` at the D2 gate. `s-reminder` was only an old
example; `s-step06` remains staging. Endpoint stays `https://ntfy.ke-den.de`.
The production sender `snotes-reminder-production` has exactly write-only
access to `s-reminder-prod`; anonymous access remains denied. The existing S
subscriber account's `s-*` rule covers the production topic.

`scripts/gc35_step08c_prepare_credentials.py` uses the existing ntfy CLI and
SQLite-safe auth backup, then stores a new root-only credential at
`/root/greencloud-secrets/reminder-ntfy-production-token`. It does not print the
token, reuse staging credentials or send a notification. Never restore the
whole auth backup over subsequent changes; rollback stops production consumers
and leaves unrelated identities intact.

Production `serve` and `schedule` require both
`--database /srv/snotes/data/reminder-production.db` and
`SNOTES_REMINDER_NTFY_TOPIC=s-reminder-prod`. systemd loads the distinct
`reminder-ntfy-production-token`; the API keeps its existing owner/actor
`reminder-api.json`. Missing or wrong production configuration fails closed.
The runtime supplies endpoint/topic without storing them in the formal DB;
owner settings retain the existing request shape but cannot redirect transport.

Use the existing packager, then `scripts/gc35_step08c_deploy.py` with a fresh
explicit `--rollback-dir /root/greencloud-backups/step08c-<stamp>`:

1. `prepare --archive <package>` saves existing runtime/units/export wrapper,
   service state and a SQLite-safe formal DB backup, without activation.
2. `cutover` stops/disables/verifies the staging scheduler before changing the
   shared runtime. It reuses the same API, scheduler and backup unit names,
   installs production templates, then starts API and backup only.
3. Run `gc35_step08c_smoke.py` on the server: `no-notification` uses a disposable
   formal clone with a non-network transport; `api` checks local/public auth and
   observes actual API file descriptors; `backup` checks production backup.
4. `enable-scheduler --confirm-no-notification-smoke` starts the single timer
   with the production target after those checks. No second scheduler exists.
5. Stop at S's iPhone subscription gate. Lock-screen and same-occurrence
   multi-notification live acceptance are required before Step 8C PASS.

`scripts/gc35_step08c_live_acceptance.py` is the narrow multi-node acceptance
runner. Its private manifest selects the two explicit fictional owner-created
records; it validates IDs, titles, schedules and the same occurrence before
calling the actual production scheduler/transport. Temporarily stop the normal
timer and service during these scoped controlled-date runs, load the production
credential and manifest through systemd, then restore the timer. Each node runs
twice (expected 1/0), checks persisted marks and unchanged unscoped tables, and
does not write the global scheduler date. Inspect actual marks and message
evidence before any resumed run; never reset marks or recreate existing fixtures
to manufacture a pass. Clean fixtures through the owner API and retain aggregate
evidence outside Git. The completed Step 8C report records actual results.

Run the smoke script with the deployed runtime on `PYTHONPATH`; production
config is required for the scheduler clone. It prints only aggregate results.

Production backups use `/srv/snotes/backups/reminder-production`, retaining the
existing latest-plus-14 policy. The same backup service/timer and off-VPS export
wrapper now target production. Historical `/srv/snotes/backups/reminder` files
and staging data/credentials remain intact. The public API hostname, loopback
port, Caddy route, UTC server clock and Asia/Shanghai business timezone stay as
before. Scheduler `Persistent=true` remains enabled.

`rollback` restores the saved runtime/units/export wrapper and prior staging
service states; it preserves the current formal DB separately and never
restores database contents or ntfy auth automatically. It does not reopen the
Mac App/MCP/helper. Mac cutover remains Step 8D.

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
