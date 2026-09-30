# GreenCloud Step 6 staging — 24h observation PASS, CLOSED

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

## HTTPS immediate gate — PASS (2026-09-29 09:04 UTC)

Public DNS (Google DNS response from authoritative Cloudflare server) returns
`reminder.ke-den.de -> 109.94.170.215`. Caddy automatically recovered from the
pre-DNS NXDOMAIN retry and obtained a production Let's Encrypt certificate at
09:03:03 UTC. No Caddy configuration edit, reload, or service restart was needed
for this gate. `caddy validate` PASS; original Caddy bytes still unchanged.

- HTTP redirects 308 to the same HTTPS host/path.
- Mac external HTTPS, pinned to the verified public IPv4 with original hostname/SNI:
  certificate/hostname verification PASS (`TLS_VERIFY=0`), no credential 401,
  invalid credential 403. No insecure TLS option used.
- Existing live acceptance harness now supports `--https`: all 72 checks PASS
  through public port 443/Caddy, including owner CRUD/snapshot, three actors,
  hidden IDs, delete/visibility denial, body identity rejection and idempotency.
  Credentials remain private on GreenCloud. API still binds only 127.0.0.1:8766.
- Local DNS uses a proxy virtual address; GreenCloud's resolver also failed
  intermittently during the first run. The HTTPS harness pins the independently
  verified IPv4 while retaining certificate/hostname validation. No DNS or proxy
  configuration changed. The interrupted run's exact fictional fixtures were
  removed through HTTPS; original four observation items remain.

`STEP06_IMPLEMENTATION=READY`
`STEP06_LOCAL_AND_CLOUD_GATE=PASS`
`STEP06_IMMEDIATE_GATE=PASS`
`STEP06_24H_OBSERVATION=PASS`
`STEP06_CLOSED=YES`

## 24h observation closeout — PASS (2026-09-30 UTC)

Observation window: **2026-09-29 08:56:56 UTC through 2026-09-30
12:13:55 UTC**, elapsed **27h 16m 59s**. Read-only inspection of Step 6
service/timer state, the complete journal within this fixed window, staging DB,
and existing daily backup; no scheduler or backup was manually rerun.
Baseline report commit `de820e1` on `codex/gc35-step06-cloud-reminder-staging`.

1. **Timer/scheduler PASS.** All 327 expected five-minute slots from September 29
   09:00 through September 30 12:10 UTC have a start within 3 seconds of schedule;
   no missing slots. Including initial startup and the previously documented
   catch-up, journal records 329 starts and 329 successful finishes. Maximum
   start-to-start gap 300.486 seconds. Both timers remain active, Persistent=yes;
   backup timer fired at September 30 03:15 UTC.
2. **Duplicate/dedupe PASS.** Scheduler journal reports 2 sends at September 29
   08:56:57 UTC, 1 at September 30 02:00:00.997551 UTC, and zero on all other
   327 passes. Exactly 3 notification marks exist: reminder 1 action, reminder 2
   advance_7, and reminder 3 action. No duplicate (reminder_id, occurrence_key)
   groups. Live and backup occurrence rows/marks agree; no evidence of duplicate
   sends in the complete scheduler journal.
3. **Service errors/restarts PASS.** API has remained active since September 29
   08:56:56 UTC. API, scheduler and backup each report Result=success,
   ExecMainStatus=0 and NRestarts=0. Complete service journals contain no error,
   crash, failed run or abnormal restart after successful startup; no priority
   0–3 records. API journal has only its startup; scheduler has only successful
   run messages and send counts; backup has two successful runs (initial and
   daily). Normal oneshot starts/exits are scheduled execution, not crashes.
4. **Next-day fixture PASS.** Fictional reminder 3, occurrence 2, scheduled for
   September 30, has action_notified_at=2026-09-30T10:00:00+08:00
   (02:00 UTC). That scheduler pass reports exactly 1 send; all subsequent passes
   through the observation endpoint report 0. The send path records its mark
   and increments its count only after transport success. This verifies server
   delivery/dispatch evidence, not handset display.
5. **Daily backup PASS.** The scheduled September 30 03:15 run produced
   `/srv/snotes/backups/reminder/reminder-20260930T031500909758Z.db`;
   it and `reminder-latest.db` have identical SHA256
   `7e16e236987adb35d89d677e02a2d4899c064f01b6e11e73914cd23dfc93137b`.
   Latest file mtime is 2026-09-30 03:15:00.906610 UTC, size 245760 bytes.
   Both live staging and latest backup open read-only and return `ok` from
   `PRAGMA integrity_check`. Matching counts: reminders=4, reminder_tables=3,
   reminder_occurrences=4, reminder_labels=1, ai_reminder_operations=30;
   the four original fictional observation fixtures and 3 marks are preserved.

Step 6 is CLOSED. Staging remains running. This closeout changes only this report;
no development, repeated HTTPS gate, permissions audit, project tests, formal
Reminder migration, authority switch, Mac helper retirement, merge, push or
Step 7 work. The six pre-existing untracked user documents remain untouched.
