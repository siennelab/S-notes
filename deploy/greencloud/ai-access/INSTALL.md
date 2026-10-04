## Step 9 — AI permission isolation

This step uses the existing Notes read and Reminder API services. It installs
no AI runtime, listener, systemd unit, schema or authority change.

| OS identity / actor | Notes read | Reminder | Other actor's mirror/token | Formal, staging, backup and future outbox DB |
| --- | --- | --- | --- | --- |
| `claude` UID/GID 2001 / `claude-code` | own `mirror/claude-code/current.db`, read only | own token → `127.0.0.1:8766` actor commands | denied | denied |
| `openclaw` UID/GID 2002 / `clawbot` | own `mirror/clawbot/current.db`, read only | own token → `127.0.0.1:8766` actor commands | denied | denied |

Existing mirror directories stay `snotes:corresponding-actor-group 2750`,
`current.db` stays `0640`. `/srv/snotes/data` stays `snotes:snotes 0700`,
its Reminder DBs stay `0600`. Neither actor joins the other actor's group or
the `snotes` group, and neither has sudo permission.

Tokens are copied during deployment from the existing root-only API identity
mapping; that mapping is unchanged. No token is generated, rotated, printed,
transferred to the Mac, placed in argv/environment, or committed. Each actor
has exactly one file:

| Path | Owner:group | Mode |
| --- | --- | --- |
| `/srv/snotes/actor-credentials` | root:root | 0711 |
| `actor-credentials/claude-code` | root:claude | 0750 |
| `actor-credentials/clawbot` | root:openclaw | 0750 |
| `actor-credentials/claude-code/reminder-token` | claude:claude | 0400 |
| `actor-credentials/clawbot/reminder-token` | openclaw:openclaw | 0400 |
| `/srv/snotes/ai-access` and its scripts | root:root | 0755 directory / 0644 files |
| `/usr/local/bin/snotes-cloud-read`, `/usr/local/bin/snotes-reminder-actor` | root:root | 0755 |

The actor directories cannot be replaced or written by either actor. Credential
loads reject symlinks, non-regular files, wrong owners, modes other than 0400,
empty/oversized/malformed tokens. Removing or loosening a token fails closed.

### Launch entries

Run the existing Notes stdio MCP as the actor user:

```sh
runuser -u claude -- /usr/local/bin/snotes-cloud-read
runuser -u openclaw -- /usr/local/bin/snotes-cloud-read
```

Effective UID fixes actor and mirror path. Environment and command arguments
cannot override either. The exact Notes tools remain `search_notes`, `get_note`,
`get_note_markdown` (search/get in the architecture shorthand). Note delete,
visibility and target changes are unavailable; hidden/other-target Notes are
excluded by the existing publisher/receiver contract.

Reminder is a thin one-request JSON client, using the existing HTTP client and
the existing `ACTOR_COMMANDS` allowlist. It takes no command-line arguments,
actor, endpoint or credential path override. Example with a nonexistent target:

```sh
printf '%s\n' '{"command":"get_reminder_item","payload":{"item_id":9223372036854775807}}' |
  runuser -u claude -- /usr/local/bin/snotes-reminder-actor
```

Use the same entry as `openclaw` for `clawbot`. Only the established list/get/
create/update/complete/pause/resume label/table/item operations are accepted.
The API derives actor from the token; caller-supplied identity is rejected.
Owner-only commands, delete, AI visibility changes and hidden Reminder access
remain forbidden. The existing API is the authorization boundary even when a
caller bypasses this client. Runtime discovery/configuration is deferred.
The existing service package contains server code only. Step 9 packages the
repository's `src/snotes/transport/remote_reminder_client.py` byte for byte into
`/srv/snotes/ai-access/remote_reminder_client.py`, using the existing venv's
dependencies and service-owned contract module. No runtime source is replaced.

### Future outbox boundary (Step 10, not implemented here)

Future `/srv/snotes/data/outbox.db` belongs to `snotes:snotes`, mode 0600, beneath
the existing 0700 data directory. Apply the same rule to SQLite WAL/SHM/journal
files and backups. Neither AI UID may open, create, replace, or chmod the DB.
AI proposals must use the future authenticated localhost service, with actor
identity fixed by its credential and existing proposal allowlist. No direct
SQLite access, extra group/ACL grant, or owner token fallback is permitted.
This step creates no outbox DB, directory, listener or service. Absence alone
is not a permission test: the verifier requires EACCES from the protected path.

### Deployment, verification and recovery

On the Mac, use the repository venv with a temporary HOME to run
`deploy/greencloud/ai-access/package.py <temporary-archive-path>`. This includes
only Step 9 assets, the existing Notes wrapper and unchanged Reminder client;
all archive owners are root, with no machine metadata. Transfer the archive to
a private temporary directory on `optimal`, verify its hash/asset manifest,
unpack it, then as root run the existing application venv:

```sh
/srv/snotes/runtime/.venv/bin/python -I -B deploy/greencloud/ai-access/install.py
/srv/snotes/runtime/.venv/bin/python -I -B /srv/snotes/ai-access/verify_actor_access.py
```

The installer checks the sealed filesystem baseline first. It backs up only
files it will replace (including an existing token on a repeat deployment) and
the affected directory metadata under `/root/greencloud-backups/step09-*`.
It preserves the preexisting INSTALL content and appends this section. No DB,
runtime source, unit, egress, ntfy, owner credential or API mapping is changed.
API/scheduler/backup need no restart. Token copies are deployed, not read by
tests for comparison; API checks load only each actor's own token for normal
authentication and print statuses only.

The verifier runs actual `runuser` processes for both UIDs, tests cross-actor
file opens, read/write denial on all discovered non-mirror DBs, future outbox
path EACCES, exact tool/command allowlists, fake-ID reads and direct API rejects.
Filesystem probes open/close without reading database bytes; MCP calls request
nonexistent IDs, and no private content is printed. Hidden Notes and
actor-specific Notes are additionally verified on disposable fictional mirrors
with the deployed read service via `verify_fictional_notes.py`; production
mirrors are never replaced. Hidden Reminder and write attribution use the
existing API regression on disposable databases, never the production DB.

To repeat the fictional Notes check, on the Mac use the repository's existing
venv with a temporary HOME to run
`-m tests.gc35_step05_cloud_fixture --output <temporary-fixture-directory>`.
Transfer only `manifest.json` and `v1/`, normalizing archive owners to root, to a fresh root-owned
`/tmp/snotes-step09-fictional-*` directory on GreenCloud, then run:

```sh
/srv/snotes/runtime/.venv/bin/python -I -B /srv/snotes/ai-access/verify_fictional_notes.py /tmp/snotes-step09-fictional-<unique-id>
```

The runner verifies fixture hashes, sets only these disposable files' group
read permissions, and checks hidden/other-target Notes under each real UID.
Remove the disposable directory after the check. No source database is sent.

Recovery: use the printed backup's `metadata.json` and `files/` to restore only
preexisting paths with their recorded ownership/modes/ACLs. Retire newly added
Step 9 wrappers, scripts and token directories into that private backup rather
than deleting user data. Do not restore databases, API tokens, service units or
the entire VPS. Existing mirror/data permissions were reviewed, not changed.
