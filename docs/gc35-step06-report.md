# GreenCloud Step 6 staging — deployed, DNS gate pending

2026-09-29 UTC. Branch `codex/gc35-step06-cloud-reminder-staging`.
Implementation `690d6ab`; deployed runtime fix `bd8d967`.
No push, main merge, formal Reminder migration, authority switch, or Mac helper changes.
The six existing untracked user documents are unchanged and uncommitted.

## Deployment and recovery

- Existing `snotes` identity and `/srv/snotes/runtime/.venv/bin/python` reused.
- Source `/srv/snotes/reminder-runtime`; DB `/srv/snotes/data/reminder.db`, snotes 0600, WAL, busy timeout 5000; unchanged schema version 4.
- API `127.0.0.1:8766`; API service, five-minute Persistent scheduler and daily 03:15 UTC Persistent backup timer installed and active.
- Fixed ntfy endpoint `https://ntfy.ke-den.de`; dedicated `snotes-reminder-staging` user has write-only access to `s-step06`. Existing secrets reused after handoff, never printed.
- New independent `reminder.ke-den.de` Caddy block validated and reloaded. Original Caddy bytes remain unchanged prefix; ntfy server configuration SHA256 remains `038619533bfe5ef2d006db0dad77aeb27a6a3a7166767f8f1ebe65186bf2a7bf`.
- Recovery material `/root/greencloud-backups/step06-20260929T083108Z`; rollback via `ssh optimal /srv/snotes/rollback-step06.sh`. It retires only this step, preserving data/backups and later unrelated configuration.
- Initial API startup rejected systemd's managed 0440 root:root credentials. Fixed narrowly for root-owned `/run/credentials/<unit>` 0550 directories; ordinary credentials remain 0400/0600. API restarted successfully at 08:56:56 UTC with zero subsequent restarts at inspection.

## Verification

- 71 focused API/runtime/existing Reminder tests PASS with temporary HOME and fictional DBs. After credential-mode fix, 9 runtime tests rerun PASS and live systemd startup PASS.
- Live localhost HTTP acceptance: 72 checks PASS: owner CRUD/snapshot; all three actors visible writes/idempotency; hidden guessed IDs not_found; delete/visibility/body identity rejected; absent/wrong credential rejected.
- Controlled-clock tests cover Shanghai 10:00 under UTC and independent multi-notify node dedupe. Actual scheduler sent 2 authenticated staging notifications at 08:56:57 UTC.
- Persistent catch-up simulated by stopping only the new timer, preserving/backdating its stamp, then starting it. Catch-up at 08:57:52 UTC succeeded with 0 sends; notification marks stayed 2. No machine reboot performed.
- Missing credential fails before network; public ntfy fallback and authenticated redirects denied in tests.
- `claude` and `openclaw` cannot read staging DB. Backup key rejects `cat /etc/passwd`; fixed no-command export succeeds.
- Cloud backup `/srv/snotes/backups/reminder/reminder-latest.db`, 14-copy rolling retention. Source and backup integrity `ok`; counts reminders=4, reminder_tables=3, reminder_occurrences=4.
- Actual Mac copy `/Users/s/Library/Application Support/S-notes/reminder-cloud-backups/reminder-staging-20260929T085804089039Z.db`; integrity `ok`, counts match. Mac/cloud SHA256 `1a38ac7eacceff26c5fa0bc554d117bdc19fa7d16b5b64847a480d64e20b0cd6`.
- Existing host emits `sudo: unable to resolve host optimal-living` on backup export; export succeeds. Recorded only, outside scope.

## Remaining gate

DNS still absent at inspection. S must add:

`A reminder.ke-den.de -> 109.94.170.215, DNS only`

After DNS, verify real HTTPS certificate and authenticated snapshot through Caddy. Therefore full immediate gate is not yet declared PASS.

`STEP06_IMPLEMENTATION=READY`
`STEP06_LOCAL_AND_CLOUD_GATE=PASS`
`STEP06_IMMEDIATE_GATE=PENDING_DNS_HTTPS`
`STEP06_24H_OBSERVATION=PENDING`

Staging remains running. No earlier than 2026-09-30 08:56:56 UTC, inspect timer execution, dedupe marks/journal and service errors since successful startup; confirm next-day fixture notification and daily backup. Final closure/merge requires these checks plus DNS/HTTPS gate. Do not enter Step 7.
