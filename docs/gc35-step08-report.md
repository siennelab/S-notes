# GreenCloud 3.5-B / Step 8

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
