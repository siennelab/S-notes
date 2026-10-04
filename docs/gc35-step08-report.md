# GreenCloud 3.5-B / Step 8

Latest status: **STEP08E=PASS; REMINDER_ACCEPTANCE_32_OF_32=PASS.** #14's existing
Mac-off receipt now has complete production machine evidence; one real Cloud
reboot passed #17/#18/#22, both final fixtures are cleaned, and #13's final
Reminder-scope comparison passed. The formal App navigation repair and S's
fresh Dock smoke remain PASS. See the [final 8E closeout](#8e-final-closeout--32-of-32-pass)
and the updated §59 matrix. Earlier sections retain their historical gate state.
Independent Step 8E closeout only: no main merge, push, Step 9 or Post-Vibe audit.

## 8C Cloud Production Activation — PASS; stop before 8D

**STEP08C=PASS. READY_FOR_STEP08D=YES. STEP08D_STARTED=NO.** Production API,
scheduler, backup and private ntfy are active; S confirmed the real lock-screen
notification. Eight live multi-notification nodes and all duplicate passes
passed. Test fixtures are cleaned. Mac App/MCP/helper are stopped at closeout;
the discovered MCP restart and renewed freeze are documented below.

S/G explicitly resolved the D2 gate in this chat:
`PRODUCTION_NTFY_TOPIC=s-reminder-prod`. The earlier `s-reminder` text was only
an example; `s-step06` remains staging. Step 8A/8B were not repeated.
Construction branch remains `codex/gc35-step08-reminder-cutover`; parent HEAD
is Step 8B `9b27eb9a84459ffe047296c22fe7ac8c8447d737`. The Step 8C implementation,
deployment support, tests and this report form a separate closeout commit.
No amend, main merge or push.

### Runtime and deployment

- Fixed formal DB: `/srv/snotes/data/reminder-production.db`. Formal
  serve/schedule require explicit `SNOTES_REMINDER_NTFY_TOPIC=s-reminder-prod`
  and the independent systemd credential `reminder-ntfy-production-token`.
  Endpoint is fixed to `https://ntfy.ke-den.de`; missing/wrong config fails
  closed without staging or public ntfy fallback.
- Production transport configuration is supplied by the runtime's narrow
  repository adapter, not imported from the Mac or written into formal settings.
  Owner can change business settings but cannot redirect endpoint/topic. HTTP
  request/response structure and actor/owner credential model are unchanged.
- Existing `snotes-reminder-api.service`, scheduler service/timer and backup
  service/timer were reused. Production templates live under
  `deploy/greencloud/reminder/production/`; historical root Step 6 templates
  remain byte-identical so `install-first.sh` retains its staging behavior.
- Before replacing runtime, staging scheduler timer was stopped and disabled,
  and both timer/service verified inactive. API and backup were stopped for
  the swap. Production API and backup then started while scheduler stayed
  disabled; production scheduler was enabled only after successful smoke.
- There is one Reminder scheduler timer, now production, active/waiting with
  `Persistent=true`. The staging scheduler role is disabled; no second daemon
  or timer was created. Production command explicitly names the formal DB.
- Public API remains `https://reminder.ke-den.de`, loopback `127.0.0.1:8766`.
  Caddy was not changed. OS timezone remains Etc/UTC; business timezone remains
  Asia/Shanghai.

Source package SHA-256:
`e3f5889a7f414faa18afb30a7fbbfcc2d2d7e75e3130d40cea13be5e4a4071fd`.
Its manifest verified all packaged hashes (25 source files and 18 deployment
assets). Production units passed `systemd-analyze verify` before activation.

Rollback material:
`/root/greencloud-backups/step08c-runtime-20261003T022700Z` includes prior
runtime, units, export wrapper, service states and a SQLite-safe formal DB
snapshot. The deploy script's `rollback` phase stops production, preserves
current formal DB as evidence and restores prior staging runtime/state. It
does not overwrite formal data, restore the whole ntfy auth DB, or reopen any
Mac writer. No rollback was executed.

### Authentication, API and scheduler evidence

Production sender `snotes-reminder-production` has exactly one ACL:
**write-only `s-reminder-prod`**. No wildcard or staging permission was added.
The separate root-only production token was generated with the existing ntfy
CLI, never printed or committed. Auth backup:
`/root/greencloud-backups/step08c-credentials-20261003T021925Z`.
Anonymous access remains denied; the existing S subscriber account already
has access to `s-*`. Read-only HTTP checks returned 403 for anonymous production
read, sender production read and sender staging read; no notification was sent.

Localhost and public HTTPS owner snapshots returned 200; missing credentials
returned 401; wrong credentials returned controlled 403. Observing the live
API process's file descriptors during requests confirmed that it opened
`reminder-production.db`, with no staging DB opened. Raw formal counts remain
2 labels / 5 tables / 51 reminders / 66 occurrences / 87 operation rows,
with 2 eligible business settings. Owner's active snapshot is 1 label / 1 table /
48 items, matching the formal read-only business view; archived/deleted rows
are correctly excluded from that view.

Before timer activation, the production scheduler on a disposable SQLite-safe
formal clone with all network sends intercepted completed successfully with
zero due notifications and zero transport calls. After activation, the actual
production service completed successfully (exit 0); its first manual pass and
next timer pass each reported **0 sends**. Read-only comparison to the pre-8C
backup confirms all five Reminder business tables, occurrence notification
marks and the two eligible settings remain identical. Scheduler execution
metadata may legitimately change. Formal quick_check ok; FK violations zero.

Targeted production/staging runtime and API settings checks passed (20 cases),
as did the full 5-case Step 6 API suite; checks used fictional DBs and temporary
HOME. No migration rehearsal or broad staging acceptance suite was repeated.

### Production backup

The existing backup service/timer and off-VPS export wrapper now source formal
data. Separate directory `/srv/snotes/backups/reminder-production` preserves
the previous latest-plus-14 retention policy without deleting historical
staging backups. Actual backup and export smoke passed:

`/srv/snotes/backups/reminder-production/reminder-latest.db`

SHA-256: `11f9f9545b3973d0ea05384dfaea3ebe5cf32fff5014a9cb4e91576f8ff66bbb`.
quick_check ok, zero FK violations, all five raw counts match formal. The
actual export wrapper output has the same hash. Original staging DB, backups,
credentials and evidence remain present.

### Completed human and live multi-notification acceptance

S confirmed subscription to `s-reminder-prod`, then confirmed the phone was
locked and authorized sending. A temporary fixture was created through the
formal owner API in a hidden test table (table 6, item 56, occurrence 98).
The actual production scheduler service sent its notification using formal DB
and the production credential. S explicitly answered **“锁屏已收到”**.
No direct curl send, staging topic, public fallback or Mac scheduler was used.

On continuation, a read-only checkpoint confirmed existing multi fixtures 57
and 58, occurrences 99 and 100, all node marks NULL, and zero corresponding
messages in the exact production-topic ntfy cache query. The lock-screen
fixture had one published message. No fixtures were recreated or marks reset.

The live runner used deployed `run_schedule_once`, formal DB and authenticated
production ntfy transport. Its repository view exposed only the explicitly
validated fixture before occurrence materialization; writes were limited to
its occurrence. The per-process injected clock did not change the server clock
or existing reminders, and controlled passes suppressed global scheduler-date
metadata writes. The production timer was temporarily stopped and its service
confirmed inactive; a single transient systemd acceptance process loaded the
same production credential and exact fixture manifest via `LoadCredential`.
There was no second enabled scheduler. Runtime source/service config was not
changed for the injection.

| Existing fixture | Same occurrence | Anchor | Nodes | First sends | Repeated sends |
| --- | ---: | --- | --- | ---: | ---: |
| 57, deadline-first | 99 | deadline 2026-10-24; action 2026-10-17 | -7 / -3 / -1 / deadline | 4 | 0 |
| 58, no deadline | 100 | action 2026-10-24 | -7 / -3 / -1 / action | 4 | 0 |

Controlled business dates were 2026-10-17 / 21 / 23 / 24. Every node's first
pass returned 1 and its immediate duplicate pass returned 0; each expected
mark persisted once and the duplicate pass left it unchanged. Unscoped table
digests matched before/after each pair. Independent ntfy cache verification
found exactly the expected eight distinct published fixture messages, with no
duplicates. Both original occurrence IDs remained stable. This validates live
Cloud sending/dedupe; only the lock-screen receipt is a human assertion.

Two focused local acceptance-runner tests passed with fictional databases,
temporary HOME and captured transport: all eight scoped nodes/dedupe, plus
rejection of an unmarked private reminder. This supplements the runtime/API
checks above; no 8A/8B migration or broad audit was rerun.

### Cleanup, final backup and Mac boundary

All three fixture reminders were deleted through the owner API; the temporary
table was moved to trash and permanently removed through that same API.
Items 56/57/58, occurrences 98/99/100 and table 6 have zero remaining rows.
Final raw counts are again 2 labels / 5 tables / 51 reminders / 66 occurrences /
87 AI operation rows, with the two business settings preserved.

Against the SQLite-safe pre-live-test backup, original reminders, occurrences,
tables and AI operation rows are byte-for-byte equal at the SQL value level.
Label identities, names, deletion/ordering/permission semantics and associations
are unchanged. One metadata-only difference is explicitly retained:
`reminder_labels.id=1.updated_at` was refreshed by the existing owner
`create_table` -> `default_label` UPSERT when creating the temporary test table.
No direct SQL was used to backdate it. Cleanup checks document this exception
rather than claiming all original rows are completely identical.

Production timer was restored; a normal production pass completed successfully,
and the production backup was refreshed after cleanup. Final backup:
`/srv/snotes/backups/reminder-production/reminder-20261003T062110207622Z.db`
(also `reminder-latest.db`), SHA-256
`9407f9b5cd4acf1210a8fe0c5eb52271fd84440a76a7058f1a6299d2a799cfc4`.
quick_check ok, zero FK violations, source/backup counts coherent. The earlier
hash above refers to the initial activation backup before live acceptance.

Final Mac boundary inspection found **11 local formal `--mcp-stdio` processes
had reappeared** since the previous gate (same parent process). Their startup
was not performed by the 8C deployment. They were terminated with SIGTERM under
the continuing maintenance freeze authorization; no MCP configuration or
authority was changed. The helper remained unregistered and its plist retained.
App plist, executable, embedded helper and helper plist hashes all match 8B.
A narrowly scoped read-only comparison of the original Mac Reminder tables to
the archived 8B source found no added/deleted rows and exact Reminder,
occurrence, AI operation and eligible-setting values. Only default label 1 and
table 1 `updated_at` differed, consistent with the old initialization path;
no cause beyond this observed restart/metadata pattern is claimed. A subsequent
check also found the old formal GUI open. Its normal quit returned user-cancel
(-128), so it was not force-killed; S explicitly confirmed saving and closing
it. The final process check then found no App/MCP/helper processes, and a fresh
read-only comparison confirmed the same metadata-only differences with all
Reminder business values preserved. No migration,
schedule replay, backdating or cross-authority merge was performed. Thus the
final freeze is verified, but uninterrupted freeze across the whole interval
is **not** claimed. Source App/MCP cutover remains Step 8D.

Private evidence under
`/root/greencloud-backups/step08c-runtime-20261003T022700Z` includes
`formal-before-live-tests.db`, `live-fixtures.json`, `multi-manifest.json`,
`multi-live-evidence.json` and `cleanup-evidence.json`. These assets are not
committed. Original staging data, backups and credentials remain retained.

```text
STEP08A_REHEARSAL=PASS
STEP08B_MAINTENANCE_WINDOW=PASS
D2_TOPIC_NAME_RESOLVED=YES
PRODUCTION_NTFY_TOPIC=s-reminder-prod
PRODUCTION_NTFY_PRIVATE_ACL=PASS
PUBLIC_NTFY_FALLBACK=NO
PRODUCTION_DB_PATH=/srv/snotes/data/reminder-production.db
PRODUCTION_API_USES_FORMAL_DB=PASS
STAGING_SCHEDULER_ENABLED=NO
PRODUCTION_SCHEDULER_ENABLED=YES
SINGLE_ACTIVE_REMINDER_SCHEDULER=PASS
PRODUCTION_BACKUP_USES_FORMAL_DB=PASS
PRODUCTION_BACKUP_SMOKE=PASS
STEP08C_PRODUCTION_RUNTIME_READY=YES
IPHONE_SUBSCRIBE_REQUIRED=NO
IPHONE_PRODUCTION_TOPIC_SUBSCRIBED=PASS
IPHONE_LOCKSCREEN_NOTIFICATION=PASS
MULTI_NOTIFICATION_CLOUD_LIVE=PASS
MULTI_NOTIFICATION_DUPLICATE_SEND=NO
FORMAL_APP_CHANGED=NO
MAC_REMINDER_AUTHORITY_CUTOVER=NO
LOCAL_MCP_CUTOVER=NO
HELPER_PERMANENTLY_RETIRED=NO
STEP08C=PASS
READY_FOR_STEP08D=YES
STEP08D_STARTED=NO
```

---

## 8B Maintenance-Window Migration

**STEP08B_MAINTENANCE_WINDOW=PASS. READY_FOR_STEP08C=YES.
STEP08C_STARTED=NO. Maintenance freeze remains in effect.**

Construction branch: `codex/gc35-step08-reminder-cutover`. Parent / Step 8A
closeout: `4d27217ed0629d39c98ddd87ea1a6093a22caa77`. This report and the three
path-parameterization support files form a separate Step 8B commit, without
amending 8A, merging main or pushing.

### Maintenance execution and formal reconciliation

S explicitly authorized entry into the maintenance window in this chat.
The formal App received a normal quit request; the helper was temporarily
booted out. Five residual formal `--mcp-stdio` processes could still write local
Reminder state and were terminated with SIGTERM. Final process inspection
confirmed no formal App/MCP/helper processes. No MCP configuration was changed.
Helper plist remains present, with no permanent disable or retirement.

Fresh snapshot created **2026-10-02 19:03:42 PDT** (`2026-10-03T02:03:42Z`):
`/private/tmp/snotes-step08b-_alud9j_/maintenance-source-snapshot.db`.
The established Mac source was opened `mode=ro`, `query_only=ON` through the
approved SQLite backup helper; snapshot mode 0400, quick_check ok, zero FK
violations. Its hash remained unchanged through reconciliation. The original
Mac database remains at its original path.

The canonical v5 bootstrap and unchanged approved migration contract produced
a fresh Reminder-only export from this snapshot. The full Mac snapshot stayed
local: no Notes or shared operation data was transferred. On GreenCloud, a
private one-time source bundle in
`/srv/snotes/step08b-migration-20261003T020342Z` ran under existing `snotes`
UID 2006 with the existing Python runtime. It freshly bootstrapped
`/srv/snotes/data/reminder-production.db` and migrated that export with the same
explicit contract. The target and sidecars were checked absent before creation;
there was no pre-existing formal DB to back up or overwrite. Neither staging
nor an old 8A candidate supplied the target. Live deployed runtime was not changed.

Formal DB is `snotes:snotes`, mode 0600, storage schema v5, WAL. Existing
connection contract sets busy_timeout=5000 (also checked during creation).
quick_check is ok and foreign_key_check returns zero rows.

The actual Cloud formal DB was backed up read-only and downloaded as
`/private/tmp/snotes-step08b-_alud9j_/formal-readback.db`; transfer SHA-256 matched
`11f9f9545b3973d0ea05384dfaea3ebe5cf32fff5014a9cb4e91576f8ff66bbb`.
The unchanged 8A reconciler compared this readback directly to the original
frozen Mac snapshot, not just to the intermediate export.

| Category | Frozen source | Cloud formal | Diff |
| --- | ---: | ---: | ---: |
| reminder_labels | 2 | 2 | 0 |
| reminder_tables | 5 | 5 | 0 |
| reminders | 51 | 51 | 0 |
| reminder_occurrences | 66 | 66 | 0 |
| ai_reminder_operations | 87 | 87 | 0 |
| eligible business settings | 2 | 2 | 0 |
| multi-notification persistence rows | 117 | 117 | 0 |
| effective label/AI mappings | 51 | 51 | 0 |

This includes identity sets, canonical fields, FK relationships, table-owned
labels, effective AI visibility, exact deleted_at/trash_group_id, occurrence
history, notification marks/dedupe and canonical operation contents. Approved
default-only legacy operation fields were checked before omission. Unknown
business fields remain fail-closed. Transport settings, old topic, scheduler
stamp and shared ai_operations are absent from the migrated data.

Schedule parity on business date `2026-10-03` (Asia/Shanghai): 40 active items,
48 probe dates, 1,020 notification identities on each side; projection,
notification identity/dedupe and due-time differences all zero. Writable
schedule probes used disposable clones; no notification transport was created
and no ntfy notification was sent.

### Durable static backup and rollback boundary

Created **2026-10-02 19:07:40 PDT** (`2026-10-03T02:07:40Z`) using SQLite backup
from the frozen snapshot, after formal zero-diff reconciliation:

`/Users/s/Library/Application Support/S-notes/backups/greencloud-step08b-20261003T020342Z/mac-pre-cloud-authority.db`

SHA-256: `f8db9edc03254b8367e3d345e34744b7aacd339a381defbcb61bfffb5532696f`.
This is also the frozen source snapshot hash. quick_check ok, FK violations 0,
mode 0400, private parent mode 0700. Source identity:
`/Users/s/Library/Application Support/S-notes/s_notes.db`, device 16777231,
inode 7826560. `backup-metadata.json` records exact time, identity and checks;
private reconciliation and migration metadata are archived alongside it.
**KEEP / ARCHIVE — pre-Cloud-authority Mac Reminder static backup.**

The original Mac DB and unchanged App/helper/plist remain the rollback assets.
Cloud pre-8B formal state was absent: a rollback would remove/quarantine only
this newly created formal DB and its sidecars, then restore the existing helper
via its retained plist and reopen the unchanged App. No rollback was needed or
executed. Do not resume local writers while awaiting 8C instructions.

Final checks confirmed four formal App/helper/plist hashes unchanged. Five
staging systemd unit hashes match before/after; staging API remains running,
scheduler and backup timers remain active/waiting, deployed DATABASE_PATH still
points to staging. No service references/opens the formal path. 8B never wrote
staging; its normal running scheduler can still change its own data, so this
does not assert a byte-frozen staging database. Production transport, scheduler,
runtime activation and Mac authority cutover remain untouched.

```text
STEP08A_REHEARSAL=PASS
STAGING_DB_PATH=/srv/snotes/data/reminder.db
FORMAL_DB_PATH=/srv/snotes/data/reminder-production.db
STAGING_FORMAL_DB_SEPARATION=PASS
STEP08B_MAINTENANCE_WINDOW=PASS
MAC_REMINDER_WRITES_FROZEN=PASS
FINAL_SOURCE_SNAPSHOT_READONLY=PASS
CLOUD_FORMAL_DB_MIGRATION=PASS
LABELS_DIFF=0
TABLES_DIFF=0
REMINDERS_DIFF=0
OCCURRENCES_DIFF=0
MULTI_NOTIFICATION_DIFF=0
AI_REMINDER_OPERATIONS_DIFF=0
BUSINESS_SETTINGS_DIFF=0
SCHEDULE_DIFF=0
TRANSPORT_SETTINGS_MIGRATED=NO
SHARED_AI_OPERATIONS_MIGRATED=NO
CLOUD_FORMAL_QUICK_CHECK=PASS
CLOUD_FORMAL_FOREIGN_KEY_CHECK=PASS
MAC_STATIC_REMINDER_BACKUP=PASS
MAC_STATIC_REMINDER_BACKUP_SHA256=f8db9edc03254b8367e3d345e34744b7aacd339a381defbcb61bfffb5532696f
MAC_OLD_REMINDER_DB_DELETED=NO
STAGING_DB_CHANGED_BY_8B=NO
FORMAL_APP_CHANGED=NO
PRODUCTION_SCHEDULER_ENABLED=NO
PRODUCTION_NTFY_ENABLED=NO
REMINDER_AUTHORITY_CUTOVER=NO
HELPER_PERMANENTLY_RETIRED=NO
READY_FOR_STEP08C=YES
STEP08C_STARTED=NO
```

### Preflight specification supplement — historical gate record

Step 8A remains formally **PASS**, closed by
`4d27217ed0629d39c98ddd87ea1a6093a22caa77`; it has not been repeated or audited.

S/G resolved the FINAL specification gap with these fixed deployment identities:

| Role | Database |
| --- | --- |
| Step 6 historical live staging | `/srv/snotes/data/reminder.db` |
| Step 8 formal production | `/srv/snotes/data/reminder-production.db` |

原 11-step FINAL 定义了 staging/formal 两个角色，但只为 Step 6 DB 给出了物理路径；独立 formal path 于 Step 8B preflight 补定，不改变 Reminder 架构，只补齐部署身份。

Read-only preflight confirmed the formal target is absent (including symlinks),
and `/srv/snotes/data` remains `snotes:snotes`, mode 0700. The staging database
remains `snotes:snotes`, mode 0600. Deployed runtime still fixes `DATABASE_PATH`
to the staging file; staging API is active/running and scheduler/backup timers
are active/waiting. No matching formal-path reference was found in systemd unit
directories or running process command lines. No service or timer was changed.

The Mac formal resolver remains
`/Users/s/Library/Application Support/S-notes/s_notes.db`. Formal App provenance
is `46a02082f698f1989defa3f80530edf03120a6d1`, whose Reminder composition uses
the local repository. Helper is registered under `com.snotes.reminder-helper`,
with the existing embedded executable, no data-root override and last exit 0.
`com.snotes.gale-production-pull` was only checked for existence/registration.

Migration will use the already approved Step 8A snapshot, canonical v5 bootstrap,
explicit migration and reconciliation functions against a fresh frozen source.
No old rehearsal candidate or staging clone will become the formal database.
Canonical connections retain busy_timeout=5000; formal creation must verify WAL,
owner `snotes` and mode 0600 before completion.

The runtime's existing Python DB-path parameters are exposed through a minimal
CLI option locally; staging defaults remain unchanged. Production activation,
transport configuration and service deployment remain Step 8C work.

Preparation verification: 13 runtime tests PASS under temporary HOME, including
four focused CLI/default/production-seed-guard/export-argument checks. No real
database or transport was used by these checks. `git diff --check` PASS.

```text
STAGING_DB_PATH=/srv/snotes/data/reminder.db
FORMAL_DB_PATH=/srv/snotes/data/reminder-production.db
STAGING_FORMAL_DB_SEPARATION=PASS
STEP08B_PREFLIGHT=PASS
READY_TO_ENTER_MAINTENANCE_WINDOW=YES
STEP08B_STARTED=NO
STEP08C_STARTED=NO
```

At this earlier gate, **maintenance-window entry required S's explicit confirmation.** No App
shutdown, helper bootout, source snapshot, formal DB creation or migration has
occurred. The completed execution above supersedes this pending-gate state.

---

## Current 8A status — full real rehearsal PASS; stop before 8B

**STEP08A_REHEARSAL=PASS. READY_FOR_STEP08B=YES. STEP08B_STARTED=NO.**

Construction branch: `codex/gc35-step08-reminder-cutover`. This report and the
accumulated six Step 8A files form the gated Step 8A commit after successful real
migration, zero-diff reconciliation and integrity checks. No merge or push.

### Narrow 3 -> 4 history review and CASE A decision

Commit `67c6022fd51ed8c34a7af5e3d18096a32dcd11f1` changes the Reminder version
marker 3 -> 4 and adds exactly:

| Table | Column | Definition |
| --- | --- | --- |
| reminders | multi_notify | INTEGER NOT NULL DEFAULT 0 |
| reminders | recurrence_mode | TEXT NULL, implicit NULL default |
| reminder_occurrences | advance_7_notified_at | TEXT NULL, implicit NULL default |
| reminder_occurrences | advance_3_notified_at | TEXT NULL, implicit NULL default |
| reminder_occurrences | advance_1_notified_at | TEXT NULL, implicit NULL default |

No table, index, PK/FK/UNIQUE/CHECK or trigger changes, and no data rewrite/backfill
are part of that version increment. The other files in that commit implement
Reminder behavior; unrelated S-notes subsystems require no structure migration
for this decision. Existing initialization has other bootstrap operations, but
none were run on the formal source or either source snapshot.

The preceding fresh snapshot in `snotes-step08a-default-only-43g4dg4q` has marker
**3**, but physically already contains all five fields and matches the reviewed
Reminder-scope contract. Read-only comparison covered all six tables (five Reminder
tables plus `app_settings`), column names/types/nullability/defaults/PKs, FKs,
UNIQUE constraints and the active-label expression/partial unique index.
Only approved legacy columns/FK and a non-unique legacy lookup index differ;
column order is ignored. No incompatible CHECK, trigger or table option exists.
Thus that specific marker-3 snapshot is **CASE A: stale metadata only**.
The implemented gate was also run read-only on that actual snapshot and passed;
evidence is `prior-marker3-preflight.json`.

**The new final-run snapshot actually has marker 4.** Its six-table physical
structure is identical to the preceding marker-3 snapshot. No source modification
was performed here; no cause is inferred for the external marker change. Therefore
final-run flags correctly say marker 4 / stale NO; marker 3 / stale YES refers only
to the preceding snapshot, not this final candidate's input.

### Implemented migration contract

- Preserve prior storage schema **4 -> 5** and nullable `reminders.deleted_at` /
  `trash_group_id` exactly. Wire schema stays 4.
- Preserve table-owned legacy `label_id` mapping and no AI-permission expansion.
- Omit only legacy operation `reminder_id` when all NULL and `reminder_title`
  when every value is exact empty text. Both migration and reconciliation
  recheck the contract. Canonical operation-log schema/runtime remains unchanged.
- Existing reviewed source markers 4/5 remain; marker 3 is separately recognized
  only after the full six-table structure/constraint and semantic checks pass.
  Other versions and unknown business fields still reject. No ordinal comparison
  prevents otherwise compatible source columns from migrating.
- Structural checks include named definitions, canonical FKs, approved legacy
  label FK, unique index terms/collations and expression/partial SQL; unexpected
  CHECKs, scoped triggers and table options reject. No source DB upgrade occurs.

### Fresh final artifacts and complete reconciliation

- Source: `/Users/s/Library/Application Support/S-notes/s_notes.db`, resolved using
  the established App resolver; opened only for SQLite read-only backup.
- Fresh snapshot: `/var/folders/fz/w1cps5hj1p1_y7zl13wzs7940000gn/T/snotes-step08a-structural-gate-7gikho6d/source-snapshot.db`.
- Fresh isolated v5 candidate: `/var/folders/fz/w1cps5hj1p1_y7zl13wzs7940000gn/T/snotes-step08a-structural-gate-7gikho6d/candidate.db`.
- Source backup used `mode=ro` and `query_only=ON`; snapshot is mode 0400 and its
  SHA-256 stayed unchanged. The candidate was newly bootstrapped, not reused after
  rollback. It is **STEP08A_CANDIDATE_ONLY / NOT_AUTHORITY / KEEP_UNTIL_STEP08B**.
- Full private evidence remains local in the same directory: `reconciliation.json`,
  `source-structure.json`, `snapshot-aggregates.json`, `prior-marker3-preflight.json`,
  `version-3-to-4-schema.diff`, boundary/snapshot hashes and `pass-flags.txt`.
  These temporary paths are not durable archival storage. No real data is committed.

| Category | Source | Candidate | Diff |
| --- | ---: | ---: | ---: |
| reminder_labels | 2 | 2 | 0 |
| reminder_tables | 5 | 5 | 0 |
| reminders | 51 | 51 | 0 |
| reminder_occurrences | 66 | 66 | 0 |
| ai_reminder_operations | 87 | 87 | 0 |
| eligible business settings | 2 | 2 | 0 |
| multi-notification persistence rows | 117 | 117 | 0 |
| effective label/AI semantic mappings | 51 | 51 | 0 |

All 87 source operation rows have legacy ID NULL and title exact `''`.
Canonical operation data is identical, including item/table IDs and titles;
only the proven default-only legacy fields are omitted. History columns are
included in exact canonical Reminder reconciliation, with no normalization.

Schedule parity at business date `2026-10-03` (Asia/Shanghai): **40 active items**,
**48 probe dates**, **1,020 notification identities** on each side; projection,
due-time and actual pending-notification clone probes all have zero differences.
Only disposable clones are writable for probes; no transport is instantiated.
No-occurrence source/candidate count is **0/0**, parity PASS; the existing fictional
business-path reachability check also passes. Both final databases have
`quick_check=ok`; candidate foreign-key check returns **zero rows**.

### Verification and boundaries

- Current targeted rehearsal tests: **22/22 PASS**, including marker-3 compatible
  reordered columns, missing 3->4 field, changed unique constraint, unknown field
  and unknown marker rejection; all earlier mapping/default-only tests remain.
- Unchanged schema/storage, Reminder R1 + 3.5 and affected owner-cache checks from
  the preceding continuation: **62/62 PASS** (4 + 57 + 1). No unrelated full suite
  or redundant regression rerun. All tests use `.venv/bin/python`, temporary HOME
  and fictional DBs; Qt check used offscreen mode. Compilation/whitespace checks PASS.
- Formal App plist/binary, embedded helper and launchd helper plist: all four
  before/after SHA-256 comparisons match. No build/install, helper bootout, staging
  access, remote action, ntfy/topic/ACL change, scheduler activation, authority
  cutover, production-pull operation or formal Reminder data mutation.
- Excluded settings (transport, credentials, old ntfy data and scheduler stamp)
  and shared `ai_operations` were not migrated. Candidate has no excluded Reminder
  setting keys and no shared operation rows.

```text
SOURCE_SNAPSHOT_READONLY=PASS
SOURCE_QUICK_CHECK=PASS
CANDIDATE_ISOLATED_FROM_LIVE_STAGING=PASS
SOURCE_SCHEMA_VERSION_MARKER=4
SOURCE_SCHEMA_STRUCTURAL_COMPATIBILITY=PASS
SCHEMA_VERSION_MARKER_STALE=NO
PRIOR_SNAPSHOT_SOURCE_SCHEMA_VERSION_MARKER=3
PRIOR_SNAPSHOT_SCHEMA_VERSION_MARKER_STALE=YES
LABEL_ID_MAPPING=PASS
AI_VISIBLE_MAPPING=PASS
DELETED_AT_PRESERVED=PASS
TRASH_GROUP_ID_PRESERVED=PASS
AI_OPERATION_LEGACY_REMINDER_ID_OMITTED_DEFAULT_ONLY=PASS
AI_OPERATION_LEGACY_REMINDER_TITLE_OMITTED_DEFAULT_ONLY=PASS
LABELS_DIFF=0
TABLES_DIFF=0
REMINDERS_DIFF=0
OCCURRENCES_DIFF=0
MULTI_NOTIFICATION_DIFF=0
AI_REMINDER_OPERATIONS_DIFF=0
BUSINESS_SETTINGS_DIFF=0
SCHEDULE_DIFF=0
TRANSPORT_SETTINGS_MIGRATED=NO
SHARED_AI_OPERATIONS_MIGRATED=NO
CANDIDATE_QUICK_CHECK=PASS
CANDIDATE_FOREIGN_KEY_CHECK=PASS
NO_OCCURRENCE_CANDIDATE_PARITY=PASS
FORMAL_APP_CHANGED=NO
FORMAL_HELPER_CHANGED=NO
LIVE_STAGING_DATA_CHANGED_BY_8A=NO
PRODUCTION_NTFY_CHANGED=NO
REMINDER_AUTHORITY_CUTOVER=NO
STEP08A_REHEARSAL=PASS
READY_FOR_STEP08B=YES
STEP08B_STARTED=NO
```

Step 8A is complete. **Do not begin Step 8B without a separate instruction.**
All sections below are historical failures superseded by this PASS.

---

## Previous attempt — default-only omission and fresh source version blocker

**STEP08A_REHEARSAL=FAIL. READY_FOR_STEP08B=NO. No commit. Step 8B has not started.**

This account-handoff continuation preserved all six existing Step 8A working files.
Branch remains `codex/gc35-step08-reminder-cutover`; HEAD remains
`b219115cfe82f63a3458d1799014c6edab21632f`. No reset, clean, merge or push.

### Newly approved contract implemented

- Only source-only `ai_reminder_operations.reminder_id` and `reminder_title`
  may be omitted, after every row is checked. The first must be NULL; the second
  must be text exactly equal to `''` under binary comparison. NULL titles,
  whitespace and non-default values reject. All other unknown fields still reject.
- Migration and reconciliation use the unchanged 14-column canonical operation-log
  contract. Reconciliation repeats preflight, so it cannot bypass the omission checks.
  No mapping to `item_id`/`item_title`, operation schema changes, runtime changes,
  wire/API changes or permission expansion.
- Previously approved Reminder history preservation and storage schema **4 -> 5**
  remain intact. Wire schema stays 4. Accepted source versions remain **4/5**;
  this continuation does not authorize or introduce version-3 compatibility.

### Fresh snapshot, candidate, and actual stop

Private local evidence directory:
`/var/folders/fz/w1cps5hj1p1_y7zl13wzs7940000gn/T/snotes-step08a-default-only-43g4dg4q`.

- Fresh snapshot: `source-snapshot.db`, created from the established formal source
  `/Users/s/Library/Application Support/S-notes/s_notes.db` using SQLite backup
  with source `mode=ro`, `query_only=ON`; snapshot mode 0400. Its bytes remained
  unchanged through this attempt. All subsequent data inspection used the snapshot.
- Fresh isolated candidate: `candidate.db`, bootstrapped at version 5; no previous
  rollback candidate was reused. It is **NOT_MIGRATED / NOT_AUTHORITY /
  NOT_APPROVED_FOR_8B**. Keep this private diagnostic evidence local.
- `from-snapshot` returned exit 2, `reminder_schema_version_incompatible`.
  Aggregate read-only evidence confirms this fresh snapshot's
  `reminder_schema_version` is **3**, while the candidate is **5**. The existing
  accepted-source contract is 4/5. The version check precedes column/default
  preflight, business-row copying, reconciliation and schedule parity.
- Stopped without changing the accepted-source contract or investigating field
  history again. Source version 3 needs a separate compatibility decision; the
  previous report's later preflight results do not establish this fresh snapshot's
  compatibility. No assertion is made about why the version marker differs.
- Snapshot-only aggregates confirm 87 operation rows, all `reminder_id` NULL and
  all `reminder_title` exact empty text. These facts satisfy the new default-only
  value condition, but do **not** turn the blocked migration into a PASS.
- Evidence files: `reconciliation.json`, `failure-evidence.json`,
  `snapshot-aggregates.json`, `snapshot-before.json`, `boundary-before.json`,
  `boundary-after.json`, and `regression-tests.log`. No database or private data
  is added to Git.

| Category | Fresh source | Fresh candidate after preflight rollback | Reconciliation |
| --- | ---: | ---: | --- |
| reminder_labels | 2 | 1 bootstrap | NOT_RUN |
| reminder_tables | 5 | 1 bootstrap | NOT_RUN |
| reminders | 51 | 0 | NOT_RUN |
| reminder_occurrences | 66 | 0 | NOT_RUN |
| ai_reminder_operations | 87 | 0 | NOT_RUN |

Both databases return `quick_check=ok` and zero foreign-key violations. Candidate
integrity establishes structural validity only; full migration, reconciliation,
schedule and no-occurrence parity are **NOT_RUN**, not zero-diff.

### Verification and unchanged boundaries

- Step 8A migration tests: **17/17 PASS** with fictional databases and temporary HOME.
  New tests cover default-only omission, non-NULL legacy ID rejection, nonempty
  title rejection, NULL title rejection, and unknown operation-field rejection;
  direct reconciliation also rejects non-default legacy fields.
- Schema/storage **4/4**, Reminder R1 + 3.5 **57/57**, affected owner-cache
  compatibility **1/1**: **62/62 PASS**, using repository `.venv/bin/python`,
  temporary HOME, fictional databases and offscreen Qt. No full-repository audit.
- Formal App plist, App binary, embedded helper binary and helper launchd plist:
  all four before/after SHA-256 comparisons match.
- No App rebuild/install, helper bootout, live staging access, remote operation,
  production ntfy/topic/ACL change, scheduler activation, authority cutover,
  Mac Reminder deletion or production-pull operation occurred.
- All accumulated Step 8A work remains uncommitted. The commit gate is not met.

```text
SOURCE_SNAPSHOT_READONLY=PASS
SOURCE_QUICK_CHECK=PASS
CANDIDATE_ISOLATED_FROM_LIVE_STAGING=PASS
SOURCE_REMINDER_SCHEMA_VERSION=3
CANDIDATE_REMINDER_SCHEMA_VERSION=5
SCHEMA_COMPATIBILITY=FAIL
LABEL_ID_MAPPING=NOT_RUN
AI_VISIBLE_MAPPING=NOT_RUN
DELETED_AT_PRESERVED=NOT_RUN
TRASH_GROUP_ID_PRESERVED=NOT_RUN
AI_OPERATION_LEGACY_REMINDER_ID_OMITTED_DEFAULT_ONLY=NOT_RUN
AI_OPERATION_LEGACY_REMINDER_TITLE_OMITTED_DEFAULT_ONLY=NOT_RUN
LABELS_DIFF=NOT_RUN
TABLES_DIFF=NOT_RUN
REMINDERS_DIFF=NOT_RUN
OCCURRENCES_DIFF=NOT_RUN
MULTI_NOTIFICATION_DIFF=NOT_RUN
AI_REMINDER_OPERATIONS_DIFF=NOT_RUN
BUSINESS_SETTINGS_DIFF=NOT_RUN
SCHEDULE_DIFF=NOT_RUN
TRANSPORT_SETTINGS_MIGRATED=NO
SHARED_AI_OPERATIONS_MIGRATED=NO
CANDIDATE_QUICK_CHECK=PASS
CANDIDATE_FOREIGN_KEY_CHECK=PASS
NO_OCCURRENCE_CANDIDATE_PARITY=NOT_RUN
FORMAL_APP_CHANGED=NO
FORMAL_HELPER_CHANGED=NO
LIVE_STAGING_DATA_CHANGED_BY_8A=NO
PRODUCTION_NTFY_CHANGED=NO
REMINDER_AUTHORITY_CUTOVER=NO
STEP08A_REHEARSAL=FAIL
READY_FOR_STEP08B=NO
STEP08B_STARTED=NO
```

---

## Previous attempt — history-column repair and operation-column blocker

**STEP08A_REHEARSAL=FAIL. No commit. Step 8B has not started.**

S/G approved only the two nullable history-preservation columns on `reminders`,
plus explicit mapping of legacy item label/AI fields. That authorized change is
implemented locally and verified with fictional data. The new real rehearsal
stopped at a different table's schema preflight, before copying any business rows.

### Implemented scope

- `src/snotes/storage/schema.py`: existing additive migration mechanism adds
  `reminders.deleted_at TEXT` and `reminders.trash_group_id TEXT` when absent;
  storage schema version **4 -> 5**. Existing values remain untouched. No item
  `label_id`/`ai_visible` additions. Wire version remains **4**.
- `scripts/gc35_step08_rehearsal.py`: explicit named canonical Reminder projection;
  accepts reviewed source schema versions 4/5; validates non-NULL legacy item
  labels against the owning table; rejects item-hidden/table-visible authorization
  expansion; preserves both history columns exactly; rejects unknown source
  fields or missing required target fields. Reconciliation compares canonical
  values and effective table-owned label/AI semantics separately.
- Repository `SELECT *` rows are mapped by column name. Domain, wire/API, UI,
  ReminderService, scheduler and AI authorization were not modified.
- `tests/test_gc35_step07_owner.py` received only a fixture constant correction:
  owner cache compatibility uses the existing wire version, not storage version.
  Only the directly affected cache test ran; Step 7 was not repeated.

### Fresh point-in-time snapshot and candidate

- Branch: `codex/gc35-step08-reminder-cutover`.
- HEAD remains `b219115cfe82f63a3458d1799014c6edab21632f`; no new commit, merge or push.
- Formal source resolved through `default_data_root()` to
  `/Users/s/Library/Application Support/S-notes/s_notes.db`.
- New snapshot:
  `/var/folders/fz/w1cps5hj1p1_y7zl13wzs7940000gn/T/snotes-step08a-approved-ruugt6e6/source-snapshot.db`.
- New candidate:
  `/var/folders/fz/w1cps5hj1p1_y7zl13wzs7940000gn/T/snotes-step08a-approved-ruugt6e6/candidate.db`.
- Evidence in the same private temporary directory: `reconciliation.json` and
  aggregate-only `failure-evidence.json`. The full private snapshot remains local.
- Snapshot created through SQLite backup API with source `mode=ro`, `query_only=ON`;
  then chmod 0400. All subsequent data reads used the snapshot, not the formal DB.
- Candidate was freshly bootstrapped from the modified canonical v5 schema; the
  old candidate was not reused. It is **NOT_AUTHORITY / NOT_MIGRATED / NOT_APPROVED_FOR_8B**.

### Actual stop condition

The real `from-snapshot` command returned exit 2:

```text
reminder_schema_columns_incompatible:ai_reminder_operations
```

Source `ai_reminder_operations` has two additional columns:

| Source-only column | Source definition | Aggregate usage across 87 rows |
| --- | --- | --- |
| `reminder_id` | nullable INTEGER | 87 NULL, 0 non-NULL |
| `reminder_title` | TEXT NOT NULL DEFAULT '' | 87 empty strings, 0 nonempty |

The source has 16 operation-log columns; the fresh candidate has 14, including
`item_id`/`item_title` and `table_id`/`table_title`. This is not merely column order.
Although these two extra columns contain only defaults in this snapshot, no
migration omission/mapping contract for them was approved. The script correctly
rejects rather than silently discarding columns or changing this second schema.
No operation-log schema or migration exception was added.

The transaction rolled back with candidate bootstrap label/table retained and
all business tables unpopulated. Full reconciliation, real schedule parity and
real candidate no-occurrence parity remain **NOT_RUN**, not zero-diff/PASS.
The preflight checks non-Reminder tables first, so the real four-field mapping
preflight did not finish; snapshot-only aggregate checks below are not a claim
that real migration completed.

New snapshot Reminder aggregates independently confirm 51 items, 20 non-NULL
legacy labels, zero label/table conflicts, zero item-hidden/table-visible
conflicts, one non-NULL item deletion timestamp and one non-NULL item trash group.

### Counts and structural checks

| Category | New source snapshot | New candidate after rollback | Reconciliation |
| --- | ---: | ---: | --- |
| reminder_labels | 2 | 1 bootstrap default | NOT_RUN |
| reminder_tables | 5 | 1 bootstrap default | NOT_RUN |
| reminders | 51 | 0 | NOT_RUN |
| reminder_occurrences | 66 | 0 | NOT_RUN |
| ai_reminder_operations | 87 | 0 | NOT_RUN |
| eligible business settings | 2 | 0 | NOT_RUN |

Both snapshot and candidate `PRAGMA quick_check` return `ok`; both have zero
`foreign_key_check` violations. These establish structural validity, not parity.

### Verification and unchanged boundaries

- Step 8A targeted migration tests: **12/12 PASS**. Includes allowed semantic
  mapping, valid conflicting labels, AI permission expansion rejection, exact
  NULL/non-NULL history copying and tamper detection, unknown-field rejection,
  missing-target-field rejection, different source column order, and migrated
  fictional no-occurrence state.
- New schema/storage tests: **4/4 PASS**. Fresh schema, v4 additive upgrade,
  history retention, named repository mapping and unchanged domain/wire.
- Reminder R1 + 3.5 regressions: **57/57 PASS**.
- Directly affected owner cache test: **1/1 PASS** under offscreen Qt.
- Tests used repository `.venv/bin/python`, temporary HOME and fictional DBs.
  Script/test compilation and `git diff --check` PASS. No unrelated full suite.
- Formal App plist/binary, embedded helper binary and launchd helper plist hashes
  match this attempt's before/after baseline. Formal App source marker remains
  `46a02082f698f1989defa3f80530edf03120a6d1`.
- No deployment, remote API/scheduler/ntfy call, staging DB access, helper bootout,
  authority change, Mac Reminder deletion or production-pull operation occurred.
- All authorized code and prior uncommitted work remain uncommitted. Files:
  `src/snotes/storage/schema.py`, `scripts/gc35_step08_rehearsal.py`,
  `tests/test_gc35_step08_rehearsal.py`, `tests/test_gc35_step08_schema.py`,
  `tests/test_gc35_step07_owner.py`, and this report.

```text
SOURCE_SNAPSHOT_READONLY=PASS
SOURCE_QUICK_CHECK=PASS
CANDIDATE_ISOLATED_FROM_LIVE_STAGING=PASS
SCHEMA_COMPATIBILITY=FAIL
LABEL_ID_MAPPING=NOT_RUN
AI_VISIBLE_MAPPING=NOT_RUN
DELETED_AT_PRESERVED=NOT_RUN
TRASH_GROUP_ID_PRESERVED=NOT_RUN
LABELS_DIFF=NOT_RUN
TABLES_DIFF=NOT_RUN
REMINDERS_DIFF=NOT_RUN
OCCURRENCES_DIFF=NOT_RUN
MULTI_NOTIFICATION_DIFF=NOT_RUN
AI_REMINDER_OPERATIONS_DIFF=NOT_RUN
BUSINESS_SETTINGS_DIFF=NOT_RUN
SCHEDULE_DIFF=NOT_RUN
TRANSPORT_SETTINGS_MIGRATED=NO
SHARED_AI_OPERATIONS_MIGRATED=NO
CANDIDATE_QUICK_CHECK=PASS
CANDIDATE_FOREIGN_KEY_CHECK=PASS
NO_OCCURRENCE_CANDIDATE_PARITY=NOT_RUN
FORMAL_APP_CHANGED=NO
FORMAL_HELPER_CHANGED=NO
LIVE_STAGING_DATA_CHANGED_BY_8A=NO
PRODUCTION_NTFY_CHANGED=NO
REMINDER_AUTHORITY_CUTOVER=NO
STEP08A_REHEARSAL=FAIL
READY_FOR_STEP08B=NO
STEP08B_STARTED=NO
```

Stopped for S/G's decision on these two additional operation-log fields. A possible
next narrow decision is an explicit default-only omission rule, guarded by fresh
snapshot validation; that rule has **not** been implemented or assumed authorized.

---

## Prior attempt (historical; superseded by the status above)

## 8A Migration Rehearsal

Status: **FAIL — schema compatibility blocker; stopped before copying business rows.** Step 8B has not started.

### Scope and source

- Construction branch: `codex/gc35-step08-reminder-cutover`, based on Step 7 closeout `b219115cfe82f63a3458d1799014c6edab21632f`.
- Starting worktree clean. `main` and `origin/main`: `fdbb6eb97f90b6cbf50b62df196582f9f7ad51b2`. No merge or push.
- Formal installed App source: `46a02082f698f1989defa3f80530edf03120a6d1`. Its `src/app_paths.py` matches the current resolver byte-for-byte. Formal helper plist has no alternate data-root; launchd data-root override absent.
- Resolved authority DB: `/Users/s/Library/Application Support/S-notes/s_notes.db`.
- One point-in-time snapshot: `/var/folders/fz/w1cps5hj1p1_y7zl13wzs7940000gn/T/snotes-step08a-smwiu215/source-snapshot.db`. Created through SQLite backup API from `mode=ro` + `query_only=ON`; initial `quick_check=ok`. No subsequent live DB reads. This private full snapshot stays on Mac and is not committed or transferred to Cloud.
- All 25 files in the deployed `/srv/snotes/reminder-runtime` source closure match the local code byte-for-byte. Candidate uses the real `initialize_database` bootstrap, schema version 4; no schema change and no fictional staging fixtures.

### Migration scope

`scripts/gc35_step08_rehearsal.py` migrates all rows and columns of `reminder_labels`, `reminder_tables`, `reminders`, `reminder_occurrences`, and `ai_reminder_operations`; preserves primary keys, nulls, lifecycle flags, relationships, exact JSON/text/date values and operation history. The fresh bootstrap default label/table are replaced only inside the new candidate, in a foreign-key-enforced transaction.

Business settings: `reminder/notifications_enabled`, `reminder/daily_time`, including exact values and timestamps. Excluded: `reminder/ntfy_endpoint`, `reminder/ntfy_topic`, `reminder/last_test_topic`. Also excluded `reminder/last_scheduled_date`: an old scheduler execution stamp, written but never read by current service; it is neither business configuration nor notification dedupe state. No credentials or transport settings are migrated. Shared `ai_operations` is not migrated.

Multi-notification has no separate node table: preserve `reminders.multi_notify`, `recurrence_mode`, and occurrence IDs/keys plus `action_notified_at`, `deadline_notified_at`, `advance_7_notified_at`, `advance_3_notified_at`, `advance_1_notified_at`. Action/deadline share the normal-node dedupe gate; the three advance marks are independent.

### P2-2 no-occurrence display

`NO_OCCURRENCE_REACHABILITY=REACHABLE` in the current business code. A fictional temporary-DB proof used the AI default current-occurrence completion for a one-time reminder: the row remains visible with raw lifecycle active, displayed status completed, no current occurrence, and one preserved completed historical occurrence. Normal human UI uses whole-reminder completion for one-time reminders.

Actual source snapshot: 48 displayed items; zero active displayed items lack a current occurrence; zero active/nondeleted-table items lack all historical occurrences; zero schedule-error items. The reachable display tail does not itself delete or hide a row. No UI, service or owner repair is included in 8A; candidate parity must confirm preservation.


### Actual blocker and stop

The real run of `from-snapshot` failed with `reminder_schema_columns_incompatible:reminders` before any Reminder INSERT/DELETE committed. The formal snapshot has 17 `reminders` columns; the current fresh Cloud schema has 13. Source-only columns are `label_id`, `ai_visible`, `deleted_at`, `trash_group_id`; the shared columns also have a different ordinal layout. Column order alone is not a business incompatibility, but these four source-only fields have no established lossless mapping in this rehearsal. The tool intentionally rejects instead of dropping or normalizing them. No private field values were printed.

The remaining migration/parity work stopped here. No zero-diff claim is made. Minimum next step for S to decide: a narrowly scoped review of the four legacy fields and their current business meaning, followed by an explicit lossless migration mapping if the existing Cloud contract can express them. If they cannot be represented, this remains a blocker; do not alter Cloud schema or advance to 8B under the present authorization.

### Candidate and reconciliation status

Local GreenCloud-style candidate: `/var/folders/fz/w1cps5hj1p1_y7zl13wzs7940000gn/T/snotes-step08a-smwiu215/candidate.db`.

```
STEP08A_CANDIDATE_ONLY
NOT_AUTHORITY
KEEP_UNTIL_STEP08B
```

This is only a fresh bootstrap artifact, **not migrated or approved for 8B**. Keep it and the source snapshot for diagnosis; the local temporary directory is not durable archival storage. No candidate was transferred to GreenCloud, and `/srv/snotes/data/reminder.db` was never opened or modified by the rehearsal.

| Category | Source snapshot | Candidate after rollback | Diff result |
| --- | ---: | ---: | --- |
| labels | 2 | 1 bootstrap default | NOT_RUN |
| tables | 5 | 1 bootstrap default | NOT_RUN |
| reminders/items | 51 | 0 | NOT_RUN |
| occurrences | 66 | 0 | NOT_RUN |
| ai_reminder_operations (whole table) | 87 | 0 | NOT_RUN |
| multi-notification persistence | 51 reminder rows + 66 occurrence rows | 0 | NOT_RUN |
| business settings | 2 eligible keys | 0 | NOT_RUN |

All business-field, identity-set and relationship reconciliations are NOT_RUN due to the schema preflight stop. Schedule parity is NOT_RUN, not zero. Candidate no-occurrence semantic parity is likewise NOT_RUN; the source/code reachability observations above are only read-only preflight facts.

### Verification and unchanged boundaries

- Five focused migration tests PASS in temporary HOME and fictional databases: exact copies and exclusions, same-count field drift detection, schema/fresh-candidate rejection, schedule parity and read purity, AI no-occurrence reachability. Script syntax and diff whitespace checks PASS.
- Source snapshot `quick_check=ok`; source FK violations=0 (snapshot only).
- Bootstrapped candidate `quick_check=ok`; `foreign_key_check` returned zero rows. These checks establish only structural validity of the unpopulated candidate.
- Four formal artifacts (App plist, App binary, helper binary, helper launchd plist) retain their baseline SHA-256 values. No formal App build/install/replacement, helper bootout, or authority change.
- GreenCloud API/scheduler service/timer configuration hashes unchanged. No live staging data writes, scheduler invocation/stoppage, ntfy send/topic/ACL work, production scheduler activation, or Gale production-pull change.
- The 25 deployed runtime source files were inspected read-only and matched local runtime exactly. No remote code or data was deployed.
- Changed files: `scripts/gc35_step08_rehearsal.py`, `tests/test_gc35_step08_rehearsal.py`, this report. Runtime code unchanged; no unrelated subsystem regression was run.
- Branch HEAD remains Step 7 closeout `b219115cfe82f63a3458d1799014c6edab21632f`. Work is **uncommitted**: user authorized the 8A commit after zero-diff PASS only. No merge, no push.

```text
SOURCE_SNAPSHOT_READONLY=PASS
SOURCE_QUICK_CHECK=PASS
CANDIDATE_ISOLATED_FROM_LIVE_STAGING=PASS
SCHEMA_COMPATIBILITY=FAIL

LABELS_DIFF=NOT_RUN
TABLES_DIFF=NOT_RUN
REMINDERS_DIFF=NOT_RUN
OCCURRENCES_DIFF=NOT_RUN
MULTI_NOTIFICATION_DIFF=NOT_RUN
AI_REMINDER_OPERATIONS_DIFF=NOT_RUN
BUSINESS_SETTINGS_DIFF=NOT_RUN
SCHEDULE_DIFF=NOT_RUN

TRANSPORT_SETTINGS_MIGRATED=NO
SHARED_AI_OPERATIONS_MIGRATED=NO
CANDIDATE_QUICK_CHECK=PASS
CANDIDATE_FOREIGN_KEY_CHECK=PASS
NO_OCCURRENCE_REACHABILITY=REACHABLE
NO_OCCURRENCE_SOURCE_OBSERVED=0
NO_OCCURRENCE_CANDIDATE_PARITY=NOT_RUN

FORMAL_APP_CHANGED=NO
FORMAL_HELPER_CHANGED=NO
LIVE_STAGING_DATA_CHANGED_BY_8A=NO
PRODUCTION_NTFY_CHANGED=NO
REMINDER_AUTHORITY_CUTOVER=NO

STEP08A_REHEARSAL=FAIL
READY_FOR_STEP08B=NO
STEP08B_STARTED=NO
```

---

## 8D Mac Remote Authority Cutover — PASS; stop before 8E

**STEP08D=PASS. READY_FOR_STEP08E=YES. STEP08E_STARTED=NO.** This section
supersedes the earlier pre-8D status only. Steps 8A/8B/8C were not repeated.
Branch: `codex/gc35-step08-reminder-cutover`. Pre-commit HEAD:
`c4d08b774be22015c4c580800558153f939c18e6`. Step 7 closeout
`b219115cfe82f63a3458d1799014c6edab21632f` is an ancestor; no merge/cherry-pick.
This closeout is a separate Step 8D commit, with no amend, main merge or push.

### Construction freeze and formal configuration

Actual Codex configuration: `/Users/s/.codex/config.toml`, entry
`mcp_servers.snotes-yan`, command
`/Users/s/Documents/项目/S-notes/dist/S-notes.app/Contents/Resources/bin/run_snotes_yan_mcp_stdio.sh`.
The packaged Yan launcher delegates to `run_snotes_mcp_stdio.sh`, which execs
`../../MacOS/S-notes --mcp-stdio`. This matched the old children of Codex
app-server PID 12163. Only this entry received temporary `enabled=false`;
children were stopped and the old parent exited. New parent 28186 passed two
short checks, separated by ordinary repository operations, with no App, MCP
stdio or Reminder helper process. The helper plist remained present.

After the formal remote bundle was ready, the inserted `enabled=false` was
removed, restoring the original enabled-key absence; all other configuration
was retained. Codex children 92546/92547/92548 started at 00:40:53 host time,
after the final build completed at 00:40:03. They therefore loaded the final
bundle; no further parent termination was needed. The actual registered Codex
MCP successfully read fixture 56, returned `not_found` for hidden fixture 57,
and updated fixture 58. An independent owner snapshot verified that exact
Codex mutation on Cloud.

Formal `scripts/build_macos_app.sh` now fixes `remote`,
`https://reminder.ke-den.de`, and the existing helper-exclusion gate to `1`.
An inherited retirement value of `0` cannot repackage the legacy helper in a
formal build. Historical candidate profiles retain their existing behavior.
GUI and MCP both use the packaged Info.plist through the existing explicit
resolver. Missing/invalid/conflicting configuration still fails closed.
No local writable fallback, offline write queue, wire/API change, Notes change,
or AI visibility/allowlist expansion was introduced.

GUI owner access remains the existing Keychain provider on a GUI worker.
MCP uses the existing actor credential provider; no owner credential was
supplied to yan, claude-code or clawbot. No credentials were committed,
printed in acceptance output, or put in plist configuration.

### Formal App and GUI evidence

Final installed launch path:
`/Users/s/Documents/项目/S-notes/dist/S-notes.app`.
Final build log: `/private/tmp/snotes-step08d-build-final.log`.
Build succeeded; deep/strict ad-hoc codesign verification passed, including an
independent final artifact check. Bundle Info.plist declares remote, the formal
endpoint, `SNotesReminderHelperRetired=true` and `SNotesStep07Candidate=false`.
No Reminder helper executable/install payload exists anywhere in the bundle.
The retirement marker here controls packaging; it does not permanently retire
the retained LaunchAgent. The Gale helper remains packaged and its LaunchAgent
was not changed.

Bundle provenance identifies baseline `c4d08b7...`, `SNotesSourceDirty=YES`:
it was built from the Step 8D source changes before this closeout commit.
The formal window title now says `S-notes · GreenCloud`; the candidate title
is preserved. The retained-helper warning no longer claims this is Step 7.

The official GUI loaded Cloud's 48 existing items; S confirmed the list was
visible. Owner API fixture create/update/pause/resume/complete succeeded;
its updated title was observed in the formal GUI. A process-local unavailable
localhost proxy then made only the App's endpoint unreachable. The GUI showed
`离线 · 只读`, compatible snapshot time, and the cached fixture. All Reminder
write buttons were disabled. Normal restart restored `已连接` and the New
button. Cloud API/scheduler/ntfy were never stopped for this gate. The final
rebuilt App launched normally and refreshed to the cleaned snapshot:
1 visible label / 1 visible table / 48 items, with no fixture IDs remaining.

### MCP adapter correction and live acceptance

The first real MCP list exposed a local output-validation mismatch: the
unchanged Cloud wire omits `table_title`, while existing `ReminderRecord`
requires it. Only `RemoteMcpReminderAdapter` was corrected. It obtains table
title through the same actor's Cloud `get_reminder_table` command for item
list/get. Failed or hidden table reads return no partial item content.
Cloud wire, API, MCP schema and permissions are unchanged.

Targeted tests used temporary HOME and fictional databases: authority 6 PASS;
GUI/MCP/HTTP and write-outcome checks 25 PASS. After the discovered adapter
correction, the MCP module's 6 checks passed (including two new regressions
using the real Cloud dispatcher/schema and failed title lookup). HTTP tests
needed loopback-listening permission; the sandbox-only attempt failed before
binding and the permitted run passed. No broad Step 7 suite or 8A/8B/8C gate
was rerun. Shell syntax, source syntax and `git diff --check` passed.

The final packaged Yan stdio launcher passed list/get and schema validation,
create/update/pause/resume/complete of its temporary actor fixture, hidden
item/table `not_found`, and rejection of unavailable delete/visibility tools.
Independent owner reads verified the actor lifecycle mutations on Cloud.
The additional registered-Codex update was also verified via owner snapshot.
Editing the completed fixture retained the existing active-lifecycle/completed
no-current-occurrence display semantics; the acceptance assertion was adjusted
without changing product behavior.

### Cleanup and authority proof

Reused one manifest; no second fixture set was created. Temporary tables 6/7,
items 56/57/58 and their occurrences were deleted through owner commands,
including scoped table purge. Read-only Cloud checks found zero remaining
fixture rows. Compared with the 8C final backup
`reminder-20261003T062110207622Z.db`, original 51 reminders, 66 occurrences,
5 tables and the two eligible business settings are unchanged. Labels remain
2; only default label `updated_at` changed from existing table creation.
Original 87 AI operation records are unchanged; six legitimate fixture actor
audit records remain (93 total), preserving normal audit history. No direct
SQL deletion or timestamp backdating occurred. Cloud API ExecStart was checked
read-only and still targets `/srv/snotes/data/reminder-production.db`.

The requested Mac Reminder-only `mode=ro`/`query_only` comparison found:
51 reminders, 66 occurrences, 87 AI Reminder operations and 2 eligible settings
exactly equal to the pre-smoke baseline, including their value digests.
Label/table IDs and business values remain equal to the archived 8B source;
only default id=1 `updated_at` differs due to the known initialization UPSERTs.
This exception is metadata only, not a local Reminder business mutation.
The whole Notes database was not hashed or required to remain unchanged.

Private recovery/evidence directory:
`/var/folders/fz/w1cps5hj1p1_y7zl13wzs7940000gn/T/snotes-step08d-freeze-nplngzuo`.
It holds the pre-cutover formal dist, restricted config backup, fixture manifest,
and local before/final summaries; none are committed. The helper plist SHA-256
remains `ad0c3f8582d8eff9aebb448049c320bc65c8b5c2aee8eeee1f51464c6ad4acf3`.
Final launchctl check found no registered Reminder helper and process inspection
found no helper process. No plist deletion, launchctl disable, reboot/login
persistence gate, or permanent retirement was performed.

```text
STEP08A_REHEARSAL=PASS
STEP08B_MAINTENANCE_WINDOW=PASS
STEP08C=PASS
STEP07_ANCESTRY_PRESENT=PASS
FORMAL_REMINDER_AUTHORITY=remote
FORMAL_REMINDER_ENDPOINT=https://reminder.ke-den.de
FORMAL_APP_REMOTE_SNAPSHOT=PASS
FORMAL_APP_REMOTE_MUTATION=PASS
FORMAL_APP_OFFLINE_FAIL_CLOSED=PASS
LOCAL_WRITABLE_FALLBACK=NO
OFFLINE_WRITE_QUEUE=NO
CODEX_SNOTES_MCP_ENTRY_REENABLED=YES
LOCAL_MCP_USES_CLOUD_API=PASS
LOCAL_MCP_DIRECT_REMINDER_DB_WRITE=NO
MCP_TABLE_TITLE_ADAPTER=PASS
AI_CREDENTIAL_BOUNDARY=PASS
FORMAL_APP_BUILD=PASS
FORMAL_APP_CODESIGN=PASS
FORMAL_APP_INSTALLED=PASS
FORMAL_APP_PACKAGES_REMINDER_HELPER=NO
MAC_LOCAL_REMINDER_BUSINESS_WRITE_AFTER_CUTOVER=NO
CLOUD_ONLY_REMINDER_MUTATION=PASS
LEGACY_HELPER_RUNNING=NO
LEGACY_HELPER_PLIST_PRESENT=YES
HELPER_PERMANENTLY_RETIRED=NO
STEP08D=PASS
READY_FOR_STEP08E=YES
STEP08E_STARTED=NO
```

---

## 8E Interim Evidence — helper reboot/login PASS; acceptance not closed

**Historical interim state: STEP08E=IN_PROGRESS. Recorded acceptance: 28 PASS, 4 PENDING
(#14, #17, #18, #22). GREENCloud_3_5_B=CLOSED is not asserted.**
This section supersedes the historical `STEP08E_STARTED=NO` above. Construction
branch remains `codex/gc35-step08-reminder-cutover`; pre-8E HEAD is
`342d659f15e72b51236b4a66550f403c4a5989f6`. No source change is needed for the
completed 8E gates. This is an interim report, not the Step 8E closeout commit.
No amend, main merge, push or Step 9 work.

S requested that the remaining real Cloud reboot / Persistent / no-duplicate
gate wait until the Mac-off gate passes. This evening's work operated only the
Mac and repository documentation: no Cloud reboot, production scheduler
start/stop/restart, timer-stamp change, notification send or fixture mutation.
Earlier passed 8A–8D and 8E gates were not repeated.

### 8E retirement, rebuild, self-check and restore evidence already completed

- The original `~/Library/LaunchAgents/com.snotes.reminder-helper.plist` was
  copied into a private temporary evidence directory for the prescribed negative
  test. `launchctl bootout gui/501/com.snotes.reminder-helper` returned
  `Boot-out failed: 3: No such process`, consistent with its already-unregistered
  state. The installed plist was then removed; registration and process checks
  found neither. No `launchctl disable` state was added. The
  `com.snotes.gale-production-pull` LaunchAgent was not changed.
- The existing formal `scripts/build_macos_app.sh` completed from clean HEAD
  `342d659...`, writing the installed formal path `dist/S-notes.app`. Its build
  checks and independent deep/strict codesign check passed. No Reminder helper
  executable or install payload was found in the bundle, and the build did not
  recreate the plist. Info.plist records `SNotesSourceDirty=NO`, `remote`,
  `https://reminder.ke-den.de`, `SNotesReminderHelperRetired=true` and
  `SNotesStep07Candidate=false`. Packaging was not changed again.
- Formal App startup was checked with temporary HOME/database. Normal state
  showed no legacy warning. Only the saved plist was copied back to its original
  path; no bootstrap/kickstart occurred and the helper remained unregistered and
  stopped. The formal App visibly showed **Legacy local Reminder scheduler
  detected**. The test plist was immediately removed; subsequent normal startup
  again showed no warning. Each test window closed normally.
- A current production backup, `/srv/snotes/backups/reminder-production/reminder-latest.db`,
  was copied into a separate `snotes-step08e-restore-*` temporary directory.
  The restored database opened with `quick_check=ok`, zero foreign-key failures,
  coherent relationships and raw counts 2 labels / 5 tables / 51 reminders /
  66 occurrences / 87 operation rows. The deployed read-only
  `SQLiteReminderRepository` / `ReminderService` read path opened it and returned
  1 visible label / 1 table / 48 items. The restored copy's hash stayed unchanged,
  and its temporary directory was removed. Formal DB was never overwritten.
  Backup SHA-256 at this gate was
  `9407f9b5cd4acf1210a8fe0c5eb52271fd84440a76a7058f1a6299d2a799cfc4`.
  This is a restore/read-path gate, beyond the historical backup-file smoke.
- Initial and pre-Mac-off Reminder-scope checks preserved all 51 reminder rows,
  66 occurrences, 87 Mac AI Reminder operations and two eligible settings at the
  SQL-value digest level. Against the static 8B backup, label/table differences
  were limited to default id=1 `updated_at`, retained as metadata-only without
  backdating. The static 8B rollback backup was readable and retained. The whole
  Notes database was not hashed. A final 8E scope refresh remains a closeout task.

These earlier gate results are the machine/UI outputs recorded in this Step 8E
chat. The original `/private/tmp/snotes-step08e-20261003` evidence directory was
no longer present after the Mac reboot; it is not claimed to be durable storage.
No passed gate was rerun merely to recreate those temporary files.

### Actual Mac reboot/login and normal startup — PASS

The Mac's actual `kern.boottime` is `2026-10-03T08:52:37Z`
(**2026-10-03 16:52:37 Beijing**, 01:52:37 PDT), after helper retirement.
`who` records the console login at 01:52 PDT. The enabled registered Codex
`snotes-yan` entry still points at the formal packaged Yan launcher. Its
post-login PID 1286 started at 01:53:39 PDT and its actual command is the formal
App executable with `--mcp-stdio`; the bundle remains explicitly remote.

Before and after this evening's GUI startup, the actual host helper plist was
absent, `launchctl print gui/501/com.snotes.reminder-helper` returned 113 with
`Could not find service`, and process inspection found no helper. The rebuilt
formal App launched normally using temporary HOME/database and the actual host
home for the legacy detector, showed no legacy warning, and closed normally.
Neither the host HOME nor the temporary HOME acquired a helper plist.
No manual killing, unloading, disabling or cleanup was used to obtain this
post-reboot result.

The completed check was recorded at `2026-10-03T09:14:10Z`; private machine
evidence is retained across the next Mac shutdown at:

`/Users/s/Library/Application Support/S-notes/backups/greencloud-step08e-20261003T091410Z/helper-reboot-login.json`

This establishes `HELPER_SURVIVES_REBOOT_AS_ABSENT=PASS` and
`HELPER_REBOOT_PERSISTENCE=PASS` together with the prior formal rebuild/reinstall
gate. Helper permanent retirement is complete; the larger Step 8E remains open.

### Mac-off attempts and unchanged final fixture — #14 PENDING

The first fixture's 2026-10-03 16:10 Beijing notification reached the iPhone,
but S reported the Mac had not fully shut down. It establishes
`CLOUD_SCHEDULER_NOTIFICATION=PASS`, **not** #14. That fixture was cleaned
through the formal owner path.

The subsequent 16:50 retry remained paused: its 16:49 one-time activation
harness failed on import of `src.snotes.transport.remote_reminder_client`,
which is not deployed in the Cloud package. The owner activation never ran.
The actual 16:50 production scheduler used
`/srv/snotes/data/reminder-production.db`, exited successfully and reported
zero sends. The paused fixture had no occurrence/marks and the exact ntfy cache
match count was zero. This was a **Gate activation harness bug**, not a
production scheduler failure. No Cloud module deployment, production fix,
mark reset, backdating, direct ntfy send or manual scheduler replay followed.
The failed fixture was removed through owner commands and its obsolete
activation timer is inactive/not scheduled. Neither attempt qualifies for #14.

S then selected **2026-10-04 10:00 Beijing**. A new independent
`FICTIONAL Step08E Mac-off final` was created directly **active** through the
existing formal owner API, without any post-shutdown activation job. Existing
business configuration remains `daily_time=10:00` in Asia/Shanghai; the domain
has reminder dates, not a per-item hour/minute field.

The pre-shutdown read-only check at `2026-10-03T17:09:06+08:00` confirmed formal
item 56, table 6 and pending occurrence 98; action date is `2026-10-04`, no
deadline/multi-notify, and all five notified marks are NULL. Exact production
ntfy cache matches were zero. Due time is `2026-10-04T10:00:00+08:00`
(`2026-10-04T02:00:00Z`). These IDs were allocated anew after removal of the
previous fixture; the distinct title, creation time and fresh occurrence/NULL
marks identify the new fixture rather than any prior attempt.

Persistent private pre-shutdown evidence:

`/srv/snotes/step08e-mac-off-final-20261004T020000Z/pre-shutdown-evidence.json`

This evening did not mutate or re-arm this fixture. S plans to fully shut down
the Mac by **2026-10-04 09:45 Beijing**, keep the iPhone online, and return after
receiving the final notification. Then verify the actual timer run, formal DB,
due classification, successful production transport, exactly one cache message,
one notification mark and no duplicate on the next normal timer pass before
claiming #14 PASS and deleting the fixture through the formal owner path.

### v2.2 §59 acceptance matrix — final 32/32 closeout

The numbering below follows the v2.2 FINAL §59 original. Report/test references
retain their already-passed evidence; they are not a request to rerun those gates.
The final 2026-10-04 closeout below supplies #13/#14/#17/#18/#22. All other
entries retain their prior PASS evidence without repeating their gates.

| # | Acceptance | Status | Evidence source / remaining gate |
| ---: | --- | --- | --- |
| 1 | migration counts | PASS | 8B, Maintenance execution and formal reconciliation: frozen Mac → formal Cloud, all counts diff=0. |
| 2 | labels/tables/items | PASS | 8B reconciliation: 2/5/51, identities, canonical fields, relationships and label semantics preserved. |
| 3 | occurrences | PASS | 8B reconciliation: 66 occurrences, history and notification/dedupe marks preserved. |
| 4 | ai_reminder_operations | PASS | 8B: 87 canonical operation rows; approved default-only omissions checked. 8D preserved original 87. |
| 5 | multi notification points | PASS | 8C, Completed human and live multi-notification acceptance: two existing occurrences, eight live nodes. |
| 6 | each point sent once | PASS | 8C: every first pass sent 1; independent cache query found eight distinct messages. |
| 7 | each point deduped | PASS | 8C: every immediate repeated pass sent 0, marks unchanged, no cache duplicates. |
| 8 | Mac owner CRUD | PASS | Step 7 live acceptance; 8D Formal App and GUI evidence: formal owner create/update/pause/resume/complete. |
| 9 | UI no main-thread freeze | PASS | Step 7 GUI acceptance; `tests/test_gc35_step07_gui.py`, `test_delay_keeps_gui_responsive_and_disables_writes`: heartbeat continues during worker delay. |
| 10 | schema mismatch → automatic read-only | PASS | Step 7 GUI acceptance; `test_future_schema_does_not_parse_or_write_and_keeps_cache`: cache retained, parse/write rejected. |
| 11 | Cloud update → Mac | PASS | Step 7 live acceptance; 8D formal GUI observed the owner API fixture's changed title. |
| 12 | Mac update → Cloud | PASS | Step 7 live acceptance; 8D formal remote mutations verified by independent owner Cloud reads. |
| 13 | only Cloud DB changes | PASS | Final 8E readonly Mac-vs-frozen-baseline comparison: all Reminder business values/marks/operations/settings unchanged; only documented default label/table updated_at metadata differs. Cloud original scope also unchanged after fixture cleanup. |
| 14 | Mac-off scheduler | PASS | S confirmed the existing Oct 4 10:00 Beijing receipt while Mac fully off; production timer at 02:00:01 UTC sent 1, exact private ntfy cache 1, one action mark, subsequent normal timer passes 0. No resend or new human Gate. |
| 15 | daily_time Beijing semantics | PASS | Step 6 24h closeout: next-day 10:00+08:00 dispatched at 02:00 UTC; 8B Asia/Shanghai schedule parity. No real setting change. |
| 16 | VPS UTC | PASS | 8C Runtime and deployment: OS Etc/UTC, business timezone Asia/Shanghai. |
| 17 | Persistent timer catch-up after reboot | PASS | One real Cloud reboot missed the 03:00 UTC calendar trigger; new boot at 03:00:05, unchanged Persistent timer automatically sent 1 at 03:00:14 before the next calendar boundary. No manual scheduler replay/stamp edit. |
| 18 | catch-up no duplicate | PASS | Next normal 03:05 UTC timer pass sent 0; full occurrence row and single exact ntfy cache record unchanged from initial catch-up evidence. |
| 19 | private ntfy auth | PASS | 8C Authentication, API and scheduler evidence: exact write-only production-topic ACL; anonymous/unauthorized reads denied. |
| 20 | no ntfy.sh client fallback | PASS | 8C formal runtime fixed private endpoint/topic and fail-closed config; 8D remote-only client and excluded local helper. Historical local candidate path is outside this formal path. |
| 21 | new production topic iPhone lockscreen | PASS | 8C human Gate: S explicitly confirmed lock-screen receipt from actual production scheduler and credential. |
| 22 | Cloud reboot auto recovery | PASS | New boot automatically restored enabled/active production API, scheduler timer and backup timer; localhost/public owner snapshots 200, existing production backup quick_check/FK PASS. No manual service start or configuration change. |
| 23 | Mac helper bootout | PASS | 8E retirement above: bootout already-not-found, plist removed, no registration/process; actual reboot/login remained absent. |
| 24 | helper removed from build/package | PASS | 8D formal build exclusion plus 8E clean formal build: no helper executable/install payload and no plist installation. |
| 25 | rebuild/reinstall does not restore helper | PASS | 8E clean 342d659 formal build/install at existing dist path, codesign PASS, no bundle helper/plist; tonight post-reboot normal startup remained clean. |
| 26 | startup self-check detects legacy helper | PASS | 8E prescribed real plist-only negative case visibly warned; normal-before/after showed no warning; no bootstrap/kickstart. |
| 27 | offline snapshot read-only | PASS | 8D real formal GUI unavailable-proxy Gate: compatible snapshot retained, offline/read-only, write buttons disabled, no local fallback/queue. |
| 28 | AI-invisible → not_found | PASS | 8D actual registered Codex and packaged Yan hidden item/table reads returned not_found. |
| 29 | guessed ID cannot bypass | PASS | Step 6 live 72-check acceptance and `test_actor_commands_reuse_service_allowlist_and_cannot_guess_hidden_ids`; 8D hidden-item/table acceptance. |
| 30 | AI cannot change visibility | PASS | Step 6 server owner-only visibility rejection and 8D unavailable/rejected visibility tools; permissions unchanged. |
| 31 | unauthorized delete/table/label rejected | PASS | Step 6 live actor owner-only denial; Step 7 live owner-only rejection; 8D packaged Yan rejection. Server allowlist remains authoritative. |
| 32 | backup restore | PASS | 8E isolated production-backup restore above: SQLite/quick_check/FK/relationships/counts and actual read-only repository/service path passed, copy removed. |

The following block preserves the original interim state; the matrix above
and final closeout below supersede its pending counters.

```text
STEP08E_STARTED=YES
LEGACY_HELPER_PLIST_PRESENT=NO
LEGACY_HELPER_REGISTERED=NO
LEGACY_HELPER_RUNNING=NO
FORMAL_APP_PACKAGES_REMINDER_HELPER=NO
FORMAL_REBUILD_REINSTALL_HELPER_ABSENT=PASS
SELF_CHECK_DETECTS_LEGACY_HELPER=PASS
HELPER_SURVIVES_REBOOT_AS_ABSENT=PASS
HELPER_REBOOT_PERSISTENCE=PASS
HELPER_PERMANENTLY_RETIRED=YES
PRODUCTION_BACKUP_RESTORE=PASS
ITEM_13_STEP08D=PASS
ITEM_13_STEP08E_FINAL_SCOPE_REFRESH=PENDING
MAC_OFF_FINAL_FIXTURE_PREACTIVATED=PASS
MAC_OFF_FINAL_DUE_BEIJING=2026-10-04 10:00
MAC_OFF_SCHEDULER=NOT_PROVEN
PERSISTENT_TIMER_CATCHUP=PENDING
PERSISTENT_TIMER_NO_DUPLICATE=PENDING
CLOUD_REBOOT_AUTO_RECOVERY=PENDING
REMINDER_ACCEPTANCE_PASS=28
REMINDER_ACCEPTANCE_PENDING=4
PENDING_ITEMS=14,17,18,22
REMINDER_ACCEPTANCE_32_OF_32=PENDING
STEP08E=IN_PROGRESS
GREENCloud_3_5_B=OPEN
READY_FOR_FINAL_MERGE_PUSH=NO
MERGE_MAIN=NO
PUSH=NO
```

After #14 passes, perform the previously authorized controlled real Cloud
reboot/Persistent/no-duplicate gate with its separate fictional fixture, then
cleanup, refresh the final scoped Mac comparison, and write the independent
Step 8E closeout commit only when all required evidence is complete.

---

## Step 8 UI regression — background Reminder snapshot navigation

**Historical repair handoff: STEP08E=IN_PROGRESS. Cloud reboot / Persistent final gates were paused.**
S confirmed receiving `FICTIONAL Step08E Mac-off final` at **2026-10-04 10:00
Beijing** while the Mac was fully shut down. Acceptance #14 is **human PASS**;
the actual production timer/transport/cache/notification-mark/dedupe evidence
will be reconciled later from the existing production logs. The earlier #14
PENDING entries describe the state before this confirmation. #17/#18/#22 and
the final #13 scope refresh remain pending; 32/32 is not asserted.

The requested repair starts from branch `codex/gc35-step08-reminder-cutover`,
HEAD `342d659f15e72b51236b4a66550f403c4a5989f6`, with only this report's existing
uncommitted updates. Those updates are preserved. The startup remote snapshot
emits `ReminderCenter.changed`, which previously called
`MainWindow._sync_sidebar_reminder_filters()` regardless of current navigation.
`NoteSidebar.set_reminder_filters()` then selected Reminder and rewrote the
sidebar mode. The minimal guard permits sidebar Reminder filter synchronization
only when `current_mode == "smart_view"` and `current_filter == "reminder"`.
The separate Reminder badge and remote writable state signal connections stay
in place. No Cloud API, scheduler, ntfy, Reminder data, permission or helper
retirement changes are included, and passed 8A–8D gates are not repeated.

### Targeted verification and formal rebuild

- `tests.test_gc35_step07_gui` and `tests.test_gc35_step08_navigation`:
  **9 tests PASS** (7 existing remote GUI checks and 2 navigation regressions),
  using the existing `.venv/bin/python`, a temporary HOME, and fictional
  temporary application/Cloud databases. The new tests exercise the complete
  asynchronous snapshot signal path, startup Notes, explicit Reminder sidebar
  navigation, labels/tables/filters, pending/offline read-only and restored
  online writable controls, background badge updates without changing Notes,
  and preservation of search/tag/other smart view navigation.
- A temporary in-memory monkeypatch restored the old unguarded synchronization;
  the regression test failed precisely because `sidebar.mode` became
  `reminder` instead of `directory`. No source file was reverted for this
  negative check, and the temporary negative-check harness was removed.
- After S reported Notes could not be opened, the new navigation regression
  was extended with real viewport clicks on two fictional Notes in a shown
  window. Cold first-note load, Reminder round-trip to a second Note, and Note
  opening after a background snapshot all passed with the real Markdown
  editor. The extended case plus the existing single-click Reminder/Note
  navigation case passed **2/2**. No Note editor or persistence code was changed.
  The final combined targeted run (7 existing remote GUI + 2 extended navigation
  + 1 existing Reminder/Note single-click case) then passed **10/10** in 17.816s.
- A source GUI smoke using the real remote snapshot and existing GUI credential
  provider, with temporary HOME/database, passed startup Notes → explicit
  Reminder click → Notes → a second remote snapshot. Both startup and final
  `current_mode`/sidebar mode were `directory`; 2 snapshot events completed
  online, and Reminder labels/tables and writable controls loaded. The passing
  run exited through `QTimer`/`app.quit`. No real user database was opened by
  this source smoke and no production Reminder command was sent.
- The unchanged `scripts/build_macos_app.sh` completed successfully at the
  existing formal path `/Users/s/Documents/项目/S-notes/dist/S-notes.app`.
  Build verification and independent `codesign --verify --deep --strict`
  passed. Info.plist identifies source HEAD `342d659...`,
  `SNotesSourceDirty=YES` (this uncommitted repair), formal `remote`,
  `https://reminder.ke-den.de`, helper-retired `true`, and Step 7 candidate
  `false`. No packaging configuration or helper lifecycle was changed.
- The formal executable was also launched with temporary HOME/database.
  A separately running ordinary Dock instance shared its Bundle ID, and the
  window tool could not reliably distinguish the two. This automated window
  attempt is not counted as the required human Dock smoke; the temporary
  instance was stopped without closing S's ordinary instance. S's human
  startup/navigation smoke subsequently **failed**: Reminder could be entered,
  but Notes could not be opened. That adjacent navigation blocker is under
  investigation; the formal human gate is not passed.
- S confirmed the affected window had remained after a close/quit request.
  `MainWindow.switch_note()` ignores clicks while `_close_pending` is set,
  whereas Reminder navigation remains available, consistent with that symptom.
  A native `app.exec()` probe with temporary HOME/database showed this waiting
  state after the first normal close and then completed closure within roughly
  3 seconds. The clean probe retained no worker threads and did not reproduce
  a permanent close failure. No speculative close/editor patch was added;
  a fresh ordinary Dock instance must still pass the human gate.
- S explicitly confirmed there were no unsaved edits and authorized ending
  the stuck GUI instance. Only the previously identified GUI PID 33583 was
  terminated after its exact formal executable command was rechecked; its
  exit was confirmed. MCP/helper processes were not targeted. S was asked to
  reopen from the Dock and repeat the minimal startup/Note/Reminder smoke.
- **Fresh formal Dock smoke — human PASS.** After reopening, S explicitly
  answered **“新窗口三项都通过”**: the startup snapshot returned while Notes
  stayed active and Notes could be opened; deliberate Reminder navigation
  loaded normally; returning to Notes preserved Note opening. This supersedes
  the earlier failed smoke on the stuck closing instance. Together with the
  real remote second-snapshot source check and 10 targeted checks, the requested
  UI regression gates are passed. No additional source change beyond the
  6-line navigation guard was needed to restore Note opening in the fresh App.

```text
SOURCE_STARTUP_STAYS_ON_NOTES=PASS
SOURCE_REMOTE_REFRESH_DOES_NOT_HIJACK_NAVIGATION=PASS
SOURCE_REMINDER_MANUAL_NAVIGATION=PASS
STARTUP_STAYS_ON_NOTES=PASS
REMOTE_REFRESH_DOES_NOT_HIJACK_NAVIGATION=PASS
REMINDER_MANUAL_NAVIGATION=PASS
TARGETED_UI_REMOTE_TESTS=10_PASS
FORMAL_APP_REBUILD=PASS
CODESIGN=PASS
FORMAL_APP_HUMAN_SMOKE=PASS
ITEM_14_HUMAN_MAC_OFF=PASS
ITEM_14_PRODUCTION_MACHINE_EVIDENCE=PENDING
PERSISTENT_TIMER_CATCHUP=PAUSED
PERSISTENT_TIMER_NO_DUPLICATE=PAUSED
CLOUD_REBOOT_AUTO_RECOVERY=PAUSED
REMINDER_ACCEPTANCE_32_OF_32=PENDING
STEP08E=IN_PROGRESS
READY_FOR_FINAL_MERGE_PUSH=NO
MERGE_MAIN=NO
PUSH=NO
```

No closeout commit, merge or push. Stop after the navigation repair and human
formal App smoke; remaining #17/#18/#22, #14 machine evidence and the final
32/32 reconciliation are a subsequent gate.

---

## 8E Final Closeout — 32 of 32 PASS

**STEP08E=PASS. REMINDER_ACCEPTANCE_32_OF_32=PASS.** S resumed the remaining
gates after the formal App UI regression was sealed. This closeout supplements
the existing human #14 receipt and performs exactly one real Cloud reboot;
it does not repeat any passed 8A–8D, helper, App, GUI/MCP, startup or restore
gate. Construction branch is `codex/gc35-step08-reminder-cutover`, parent HEAD
`342d659f15e72b51236b4a66550f403c4a5989f6`. The existing uncommitted report and
minimal UI guard/tests are preserved in the independent Step 8E closeout.

### #14 — existing Mac-off receipt and production machine evidence

S already confirmed receiving `FICTIONAL Step08E Mac-off final` at **2026-10-04
10:00 Beijing** while the Mac was completely shut down. Readonly reconciliation
used its existing pre-shutdown evidence, production journal, formal DB and
private ntfy cache; no notification resend or new human Gate occurred.

- Formal item 56 / table 6 / occurrence 98 was due on Oct 4 at unchanged
  Beijing `daily_time=10:00`. Its pre-shutdown cache count was 0 and all five
  notification marks were NULL.
- Actual production timer started at `02:00:00.694966Z`, reported **1** at
  `02:00:01.100039Z`, and completed successfully. The occurrence's sole mark
  is `action_notified_at=2026-10-04T10:00:01+08:00`; the other four remain NULL.
- Exact topic/title/body cache match is **1**, published, message ID
  `AAeUPHdHMX8d`, time `2026-10-04T02:00:01Z`. This ties the successful production
  transport to the due fixture and mark.
- The next normal timer at `02:05:01.041054Z` reported **0**; each later checked
  normal pass through `02:40Z` also reported 0. No mark reset, direct send,
  activation retry or manual scheduler run was used.
- After evidence was saved, the existing owner API performed `delete_reminder`,
  `delete_table`, then `permanently_delete_table` for this exact fixture. Its
  item, occurrence and table rows are all absent; a protected pre-cleanup DB
  backup remains in the private evidence directory.

### #17/#18/#22 — one real Cloud reboot and Persistent timer recovery

An independent hidden fictional table/item,
`FICTIONAL Step08E Persistent reboot final`, was created through the owner API
paused, then resumed immediately before the selected **03:00 UTC / 11:00 Beijing**
calendar boundary. Before activation, the preceding normal pass sent 0. Before
reboot, the new fixture had one pending occurrence, all five marks NULL and
exact cache count 0. SQLite reused item/table/occurrence IDs 56/6/98 after the
old fixture's complete removal; distinct title/creation time and fresh NULL
marks establish that this is a separate fixture.

The production timer was enabled, active, `Persistent=yes`, with actual
`NextElapseUSecRealtime=03:00:00Z`. A single-use, fsynced reboot request was
recorded at `02:59:57.000158Z`. The timer was stopped at `02:59:57.012669Z`
without disabling it or editing its unit/stamp, then `systemctl reboot` was
issued **once**. No manual service/scheduler start followed.

| Evidence | Result |
| --- | --- |
| Old boot | `1d7cb1d0-713d-4d9d-a6bd-e016fdfe6dfe` |
| Exactly one new boot | `689b0e84-f7cc-42de-a865-d45a97bd10e7`, boot time `03:00:05Z` |
| Missed trigger | No scheduler numeric output in the old boot after `03:00:00Z` |
| Automatic Persistent catch-up | New boot's first production timer pass sent **1** at `03:00:14.882548Z`; timer LastTrigger `03:00:14Z`, before next calendar trigger `03:05Z` |
| Production delivery and mark | Exact published cache **1**, ID `21tOqCOGvkf5`, time `03:00:14Z`; sole action mark `2026-10-04T11:00:14+08:00` |
| Next normal timer | Sent **0** at `03:05:01.241194Z`, successful; complete occurrence row and exact single cache record unchanged |
| API automatic recovery | Enabled/active new-boot PID; localhost and public HTTPS owner snapshots **200** |
| Timer automatic recovery | Scheduler and backup timers enabled/active with new-boot automatic-start journal records; scheduler `Persistent=yes` |
| Existing production backup | Readonly `quick_check=ok`, FK violations **0**; no new restore rehearsal |
| Configuration boundary | All five production unit hashes unchanged; OS Etc/UTC, daily_time 10:00, notifications enabled unchanged |

The temporary evidence harness initially passed a hyphenated boot ID to
`journalctl`, which rejects that format. Only its readonly argument formatting
was corrected; the same actual reboot and catch-up evidence then passed. No
second reboot, resend or production repair was required.

After the next normal pass proved dedupe, the owner API cleaned the independent
reboot fixture with the same three owner commands. A protected pre-cleanup DB
backup was saved first. Remaining fixture item/occurrence/table rows are **0**.
Formal counts returned to **2 labels / 5 tables / 51 reminders / 66 occurrences /
93 operations**, with 2 eligible settings. All six scoped canonical digests
match the pre-reboot-gate baseline, allowing only the previously documented
default label/table `updated_at` metadata normalization. The original Cloud
Reminder business values, marks, operation rows and settings did not change.

### #13 — final Reminder-scope readonly comparison

After fixture cleanup, the formal Mac DB was opened in SQLite `mode=ro` with
`query_only=ON` and compared to the existing frozen 8B backup:

`/Users/s/Library/Application Support/S-notes/backups/greencloud-step08b-20261003T020342Z/mac-pre-cloud-authority.db`

Mac and baseline counts are **2 labels / 5 tables / 51 reminders / 66 occurrences /
87 operations / 2 eligible settings**. Exact SQL values for reminders,
occurrences (including every notified mark), all operation rows and the two
eligible settings are equal. Label/table identities and every business field
are equal; the only differences are the already-recorded default label 1 and
default table 1 `updated_at` metadata. All six normalized scope digests match,
unexpected differences are **0**, and SQLite `total_changes=0`. Notes and other
unrelated DB tables were not compared or hashed. The Cloud operation count 93
already existed before this final gate; it is distinct from the frozen Mac's
87 and stayed unchanged throughout this round.

Private durable evidence is retained under:

`/srv/snotes/step08e-closeout-20261004/`

The directory is mode 0700; evidence/backups are mode 0600. It contains
`item14-machine-evidence.json`, `mac-off-cleanup.json`, `reboot-baseline.json`,
`reboot-pre-shutdown.json`, `reboot-requested.json`,
`reboot-catchup-evidence.json`, `reboot-final-evidence.json`,
`reboot-cleanup.json`, `mac-final-scope.json`, and protected SQLite backups.
The original Mac-off pre-shutdown evidence remains at its earlier private path.
No credentials, private DB, build artifact or temporary harness is committed.

```text
ITEM_14_HUMAN_MAC_OFF=PASS
ITEM_14_PRODUCTION_MACHINE_EVIDENCE=PASS
PRODUCTION_SCHEDULER_RUN=PASS
FIXTURE_DUE=YES
PRODUCTION_TRANSPORT=PASS
NTFY_CACHE_EXACT_MATCH=1
NOTIFICATION_MARK_WRITTEN_ONCE=YES
SUBSEQUENT_NORMAL_TIMER_DUPLICATE=NO
CLOUD_REBOOT_COUNT=1
CLOUD_REBOOT_AUTO_RECOVERY=PASS
PRODUCTION_API_AUTO_RECOVERY=PASS
PRODUCTION_SCHEDULER_TIMER_AUTO_RECOVERY=PASS
PRODUCTION_BACKUP_TIMER_AUTO_RECOVERY=PASS
PERSISTENT_TIMER_CATCHUP=PASS
PERSISTENT_TIMER_NO_DUPLICATE=PASS
FINAL_FICTIONAL_FIXTURES_CLEANED=PASS
ITEM_13_STEP08E_FINAL_SCOPE_REFRESH=PASS
CLOUD_ORIGINAL_REMINDER_SCOPE_UNCHANGED=PASS
REMINDER_ACCEPTANCE_PASS=32
REMINDER_ACCEPTANCE_PENDING=0
REMINDER_ACCEPTANCE_32_OF_32=PASS
STEP08E=PASS
MERGE_MAIN=NO
PUSH=NO
STEP09_STARTED=NO
POST_VIBE_AUDIT_STARTED=NO
```

No production architecture, Reminder permissions, ntfy endpoint/topic/ACL,
daily_time, Mac authority or Cloud/Mac DB ownership was changed. Closeout ends
with the independent Step 8E commit; Post-Vibe readonly audit is a separate
next task.
