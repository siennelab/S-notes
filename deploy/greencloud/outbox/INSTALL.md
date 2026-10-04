## Step 10 — Notes proposal outbox

`snotes-notes-outbox.service` runs as the existing `snotes` UID/GID 2006,
listening only on `127.0.0.1:8767`. It uses systemd's existing
`reminder-api.json` credential source and recognizes only the two existing
actor tokens. The server fixes `source_actor`; clients cannot select an actor.
The actor MCP's EUID fixes the existing Step 9 `reminder-token` file:
UID 2001 → `claude-code`, UID 2002 → `clawbot`. Tokens are not copied,
rotated, printed or included in the source package. No public route is added.

`/usr/local/bin/snotes-cloud-read` retains its argument/environment clearing
and now registers the original three read tools plus `create_note`,
`prepend_note`, `append_note`, `update_note_metadata`, `edit_note`, and
`get_operation_status`. Writes are proposals only.
Mac remains the only Notes authority; Cloud does no final canonicalization.
Metadata uses the existing local contract: only `title`, `directory_id`,
`add_tags`, and `remove_tags`. Modifications require `expected_version`;
`expected_updated_at`, client-selected actor, AI visibility, and actor targets
are unavailable. Delete remains unavailable. Step 10's
`/srv/snotes/outbox-deploy/verify_outbox.py` owns the current nine-tool contract and
outbox checks. Step 9's historical isolation verifier is preserved unchanged;
its original three-tool expectation describes the CLOSED Step 9 baseline.

`/srv/snotes/data/outbox.db` is `snotes:snotes 0600`, WAL, in the existing
0700 data directory. Its sidecars are also service-owned 0600. AI UIDs cannot
open the DB, sidecars or backups. Initialization explicitly enables the
generalized outbox schema; Gale's default initialization stays legacy-compatible.
Oracle production/runtime/DB is outside this deployment.

The existing daily `snotes-reminder-backup.service` gains only an
`ExecStartPost` drop-in for a SQLite-safe Notes outbox snapshot under
`/srv/snotes/backups/notes-outbox` (snotes 0700, files 0600). It retains
latest-plus-14 snapshots. The Reminder backup command, timer and export
channel remain as installed. A Notes backup failure makes the shared backup
run fail visibly. To verify only Notes backup, run as `snotes`:

```sh
/usr/local/sbin/snotes-notes-outbox-backup
```

The existing Mac mirror key/forced command remains the transport identity.
Its root-owned entry now additionally recognizes exactly
`notes-outbox-export`, `notes-outbox-acknowledge`, `notes-outbox-receipt`,
each routed through fixed-argument sudo as `snotes`. Empty SSH command still
uses the original mirror receiver. No key, account or shell privilege is added.
Each outbox request is bounded JSON on stdin, with a fixed database path:

- export: `{}` or `{"limit":100}`, returns generic operations and marks them delivered;
- acknowledge: `{"submission_uuid":"…","local_receipt_id":"…"}`;
- receipt: `{"source_actor":"claude-code","request_id":"…","terminal_state":"accepted|rejected|conflict","receipt_payload":{}}`.

These are Cloud-side capabilities for Step 11. Step 10 installs no Mac pull,
AI Center, accept path or receipt sender. AI credentials cannot call them.

The completed Step 11 Mac lifecycle also handles prepend and metadata proposals
through the same inbox, human decision, terminal receipt, and actor mirror
refresh. Metadata acceptance uses the existing local directory/metadata owner
and leaves Markdown unchanged. No outbox schema or Oracle contract change is
needed for these operations.

Package with the repository `.venv/bin/python deploy/greencloud/outbox/package.py
<private-temporary-archive>`, transfer through `ssh optimal` into a private
temporary directory, extract and run `deploy/greencloud/outbox/install.py`
with the existing Cloud venv. The installer verifies package hashes and sealed
identities/permissions, backs up only replaced files/affected path metadata to
`/root/greencloud-backups/step10-<stamp>-<unique>/`, and writes
`/srv/snotes/outbox-deploy/deployment-record.json` plus the source hash manifest.
It creates no venv, dependency upgrade or deployment framework.

Recovery: stop/disable `snotes-notes-outbox.service`, retire newly added
wrappers/unit/drop-in/sudoers and restore only existing paths from the printed
backup's `files/` with `metadata.json` ownership/modes. Run `daemon-reload`.
Preserve outbox DB/backups and all Reminder data/state; do not restore database
contents or the entire runtime/VPS. Leave this deployment's recovery files
until its human acceptance. For an update to an existing deployment, restore
only the replaced files from that update's backup; do not retire the established
service or remove previously existing wrappers. Remove disposable deployment and fictional test directories after
verification; no fictional proposal is left in the production queue.
