# GreenCloud 3.5-C / Step 9 — AI permission isolation

**STEP09=PASS; GREENCloud_3_5_C=CLOSED.**

Baseline `90cb115f3866759c32987bc636c81edd322ef921`, clean `main`.
Task branch: `codex/gc35-step09-ai-permission-isolation`. Deployment and commit
are complete; no App build, main merge, push or Step 10 work.

## Result

| Identity | Own Notes mirror | Own Reminder credential/API | Other mirror/credential | Direct business/backup DB or future outbox |
| --- | --- | --- | --- | --- |
| claude UID 2001 / claude-code | read only, PASS | allowed fake-ID read, PASS | denied, PASS | read/write denied, PASS |
| openclaw UID 2002 / clawbot | read only, PASS | allowed fake-ID read, PASS | denied, PASS | read/write denied, PASS |

Existing mirror directory/group permissions and the `snotes:snotes 0700` data
directory already met the sealed architecture and were reviewed without change.
No ACL grants were present on those paths; actors have no cross-actor/service
group membership or sudo permissions.

Two separate existing actor tokens were deployed only on GreenCloud, under
`/srv/snotes/actor-credentials/<actor>/reminder-token`, owned by the corresponding
UID/GID, mode 0400. Their parent directories are `root:actor-group 0750`; the
shared parent is `root:root 0711`. Actor users cannot replace the directories
or files. The API's original identity mapping and all credentials are unchanged.
No secrets were printed, transferred to the Mac or committed.

The Notes entry rejects extra arguments and retains the existing effective-UID
mapping/environment clearing. Its exact tools remain `search_notes`, `get_note`,
`get_note_markdown`. The new `snotes-reminder-actor` entry accepts one bounded
JSON request, fixes actor/token by EUID and endpoint to `127.0.0.1:8766`, and
reuses the existing 13-command `ACTOR_COMMANDS` allowlist and Reminder client.
No actor, endpoint or credential-path override is available.

The existing Reminder server package did not contain the transport client.
Step 9 therefore packages that repository file byte for byte into the root-owned
`/srv/snotes/ai-access` support directory. Its deployed SHA-256 matches source:
`b776bb030a08a229166dad79789396703b1a4364814c6a9d40a5c62d8dab3103`.
No dependency, service, runtime source or authority was replaced.

`/srv/snotes/INSTALL.md` retains its original content and adds the permission
matrix, launch entries, repeatable verification and future outbox boundary.
Future outbox belongs in the existing protected data directory, service-owned
0600 including sidecars/backups, accessible to AI only through the future
authenticated localhost service. No outbox path, DB, service or schema was built.

## Verification

Local checks use the existing repository `.venv`, temporary HOME and fictional
databases/credentials. **61 unique tests passed**:

- New Step 9 actor entry tests: 10. Both actor tokens create fictional AI-visible
  Reminder items with exact actor attribution; hidden table/item guesses return
  not_found; owner delete/visibility and identity/targets injection are rejected.
  UID binding, credential inode/owner/0400/symlink/size checks, client closure and
  bounded stdin/argument rejection also pass.
- Existing direct regressions: 51 across actor mirror generation/validation,
  Cloud read/receiver, Reminder API, MCP adapter and HTTP client. Ten HTTP tests
  initially hit the sandbox's localhost bind restriction; those ten alone were
  rerun with authorization and passed. No Reminder 32/32 or full-project suite.
- Shell syntax, Python parse and diff whitespace checks pass.

The deployed `verify_actor_access.py` ran actual runuser processes for both UIDs:

```text
PASS IDENTITY_AND_METADATA 1
PASS FILESYSTEM_ISOLATION 118
PASS REMINDER_ACTOR_WRAPPER 2
PASS REMINDER_API_BOUNDARY 66
PASS NOTES_MCP_BOUNDARY 12
PASS STEP09_ACTOR_ISOLATION 199
```

Filesystem checks cover own mirror read/write, cross-actor mirror/token
read/write, all discovered non-mirror DBs (production, staging, backups and
historical migration/evidence DBs), and the absent future outbox path. DB file
probes open/close without reading bytes, creating or truncating files; failures
must be EACCES, not merely absence. The existing application venv is excluded
from business DB inventory, preserving its normal lib64 symlink.

Direct API probes authenticate normally with each actor's own token and require
403 for every owner-only command, 404 for unknown Notes mutations, and 400 for
identity or visibility/targets injection. They do not read response bodies or
write production data. Wrapper/MCP positive checks use nonexistent IDs; the
Notes MCP exact tool list and unavailable mutation tools pass.

The existing fixture builder generated temporary actor mirrors from fictional
Notes, with no source DB retained/uploaded. Both real cloud UIDs passed 12
fixture assertions each: own/common reads succeed, hidden/other-actor/Gale-only
reads fail, hidden search is empty and delete is unavailable. Production mirrors
were never replaced. The first fixture run rejected Mac archive ownership;
only the disposable files' owner was corrected before successful verification.

The initially missing client module was diagnosed and fixed by packaging the
existing client; the final complete 199-check run passes. No egress change was
needed. The automated review rejected a proposed real-credential comparison
test; that test was cancelled. Credential content is used only for the approved
deployment and normal actor authentication, never for diagnostic comparison.

## Deployment and recovery

Only files being replaced and affected directory metadata were backed up:

- Original baseline files: `/root/greencloud-backups/step09-20261004T053316Z-yq40ogpk`.
- Final corrected deployment's prior assets: `/root/greencloud-backups/step09-20261004T053631Z-a5ietn3x`.
- Final documentation wording correction only: `/root/greencloud-backups/step09-documentation-20261004T053943Z-4yklnp_h`.

No VPS-wide or business DB backup was made for this step. Temporary remote
deployment/fictional-fixture directories were removed after verification.
Restore only recorded Step 9 paths/modes/owners/ACLs; preserve service/data state.

Before/after source aggregate hash of both existing deployed runtime trees is
identical: `31fbdebd21c4ac032488d11a6200c18abbf4dcd0f9883bc654744c8b5019b015`.
All five Reminder unit hashes remain identical. API PID remains 2374 and active;
scheduler and backup timers remain active. No service restart was performed.

Claude Code/OpenClaw runtime, prompt, plugin/profile/gateway, egress, database
schema, Notes publisher/mirror authority and Reminder authority were untouched.
No formal App build, push, owner slimming or other adjacent work.

## Remaining findings

The existing `sudo` host-name resolution warning for `optimal-living` was
observed during read-only permission review. Both actor sudo requests were
denied as intended. This unrelated host configuration was not changed and does
not block Step 10. No new Step 10 blocker was found; actual AI client discovery
remains deferred until the separately planned runtime installation.
