# GreenCloud 3.5-A Step 5 — CLOSED

Date: 2026-09-29 UTC. Automated acceptance and S's human gate passed;
Step 5 and GreenCloud 3.5-A are closed.

## Revision and preserved workspace

- Branch: `codex/gc35-step05-cloud-read`.
- Base: `4d18fdcd5287cbee147305f6bd7d0d31fc1c67e2` (local main).
- Implementation/runtime source: `e85d7ed236821bc07f1d7e0d2e33ddb199cabb75`.
- Deployment documentation and real acceptance harness: `c292f31`.
- Report commit: resolve with `git log -1 --format=%H -- docs/gc35-step05-report.md`.
- Construction checkout (removed after P1 closure): `/Users/s/.codex/worktrees/gc35-step05-cloud-read/S-notes`.
- Closeout `1b32415` was fast-forwarded into formal main (no merge commit);
  the six untracked user documents were preserved. No push or Step 6 work.
- Production publisher now runs from formal main (`/Users/s/Documents/项目/S-notes`);
  the isolated construction worktree is no longer a runtime dependency.

## Actual deployment

Only `optimal` / `optimal-living`, Debian 13.7, existing system Python 3.13.5.
The earlier Ubuntu observation concerned another host and is not this target.
Luna had not changed code or deployment. Existing claude UID/GID 2001 and
openclaw UID/GID 2002 remain unchanged. New snotes UID/GID 2006 owns mirrors;
snotes-publish UID/GID 987 is the restricted SSH transport identity.

| Asset | Actual deployment |
| --- | --- |
| `/srv/snotes` and `runtime` | root:root 0755 |
| runtime Python | application venv, mcp 1.28.1; no second system Python |
| runtime manifest | e85d7ed source revision, 23 Python files; hashes verified |
| runtime dependency record | `requirements-installed.txt` |
| `/srv/snotes/mirror` | root:root 0711 |
| `mirror/claude-code` | snotes:claude 2750 |
| `mirror/clawbot` | snotes:openclaw 2750 |
| each `current.db` | snotes:actor group 0640 |
| candidates / publish lock | 0600 |
| authorized_keys | root:snotes-publish 0640, restrict + fixed command |
| receiver sudoers | root:root 0440, exact receiver command without arguments |
| Cloud launcher | `/usr/local/bin/snotes-cloud-read` |
| receiver wrappers | `/usr/local/sbin/snotes-mirror-ssh-entry`, `snotes-mirror-receiver` |

Publisher reads the source DB read-only, builds actors through the unchanged
Step 4 ActorMirrorService, and sends only actor-safe mirrors over dedicated SSH.
No source database is uploaded. Receiver validates bounded transport, checksum,
and actor contract before same-directory atomic replacement. Failure preserves
the previous mirror. Each MCP request opens and closes the fixed actor database
read-only; effective UID selects the actor. No listener or systemd service added.
Caddy, ntfy, de-node, egress, Gale and Reminder were outside this change.

## Real fictional acceptance — 17/17 PASS

Fixtures were generated through the formal repository and ActorMirrorService,
using a temporary fictional source DB and redaction rules. Two live MCP sessions
ran through runuser as the actual actor UIDs. These checks did not use real notes.

| # | Check | Result |
| --- | --- | --- |
| 01 | common visible to both actors | PASS |
| 02 | claude-code-only absent for openclaw | PASS |
| 03 | clawbot-only absent for claude | PASS |
| 04 | hidden and unauthorized absent | PASS |
| 05 | redacted safe_text, NULL safe_markdown | PASS |
| 06 | full safe_markdown present | PASS |
| 07 | full Markdown unchanged despite matching redaction rule | PASS |
| 08 | get_note returns safe_text | PASS |
| 09 | Markdown only for full; redacted returns not_found | PASS |
| 10 | dialect snotes-markdown-v1 | PASS |
| 11 | source_version preserved | PASS |
| 12 | actor contract / registry correct | PASS |
| 13 | next request in existing session sees new inode/content | PASS |
| 14 | old mirror readable without an active Mac publisher/transport | PASS |
| 15 | damaged candidate rejected; old hashes/content unchanged | PASS |
| 16 | actual UID 2001 cannot open clawbot mirror | PASS |
| 17 | actual UID 2002 cannot open claude-code mirror | PASS |

Initialize, tools/list, search_notes, get_note and get_note_markdown passed for
both users. Only those three read tools are exposed. Both actors also failed
write-open on their own mirrors. Forced-key negatives passed: empty shell
session rejected as invalid_header, arbitrary `id` rejected as command_rejected,
and unsupported actor rejected without changing either mirror. Client discovery
in real Claude Code/OpenClaw remains deferred, not a Step 5 blocker.

## Publisher schedule

Real LaunchAgent execution against fictional data published both actors; a
second execution reported UNCHANGED for both. The temporary agent was unloaded.
Production plist is installed at
`~/Library/LaunchAgents/com.snotes.greencloud-mirror-publish.plist`, mode 0600,
RunAtLoad=true, StartInterval=300. It uses the formal repository `.venv/bin/python`;
since 2026-10-01 its `WorkingDirectory` is a pinned source export, not the
developer checkout (see "Runtime re-pin" below).
Dedicated existing key was reused, not regenerated.

Production agent was loaded and first execution exited 0. Both actor state
records show successful publication (2026-09-29 06:36 UTC), no failure and no
retry needed. This is normal authorized safe-mirror publication, not a real-note
construction test. No real Cloud note content was queried for automated acceptance.
Local tests cover unchanged skip, failure preservation and subsequent retry.

## Final local verification

After code, fixture and rollback changes: 48 tests passed across Step 5 cloud
read/receiver/publisher and directly related Step 4 mirror and actor/safe-read
regressions. Temporary HOME and fictional databases were used with the existing
repository Python. Architecture: PASS, 99 files, 20 existing warnings.
Deployment shell syntax and git diff --check passed. No unrelated UI/Reminder
suite was run. Report-only edits follow these checks.

## Rollback and operation

See `deploy/greencloud/INSTALL.md` (also installed as `/srv/snotes/INSTALL.md`).
First-install script must not be rerun. Stop the Mac LaunchAgent and move its
plist into private recovery storage, then execute through existing admin SSH:
`ssh optimal /srv/snotes/rollback-step05.sh`.

Rollback retires only Step 5 key/sudoers/wrappers, preserving original paths in
a private recovery directory, and stops only the two actors' Step 5 MCP processes.
Runtime, identities and last mirrors remain recoverable. The previous rollback
script was preserved as `/srv/snotes/rollback-step05.pre-closeout.sh` before the
fixed version was installed. This was the first S-notes deployment; no prior
S-notes deployment was overwritten. Mac Notes DB and other services are untouched.

## S human gate — PASS

S explicitly confirmed the following result in this conversation (human
confirmation, not an additional automated read of the real note):

```ini
STEP05_HUMAN_GATE=PASS
HUMAN_NOTE_ID=312
HUMAN_UPDATED_CONTENT_VISIBLE_TO_CLAUDE_CODE=YES
HUMAN_UPDATED_CONTENT_VISIBLE_TO_CLAWBOT=YES
STEP05_CLOSED=YES
3_5_A=CLOSED=YES
PUSH=NO
```

No merge, push, Step 6 work, deployment change or further test run was performed
for this documentation-only closure. The publisher's later move to formal main
is recorded under Post-Vibe audit below.

### Human verification procedure (retained for reference)

In the official Mac S-notes app, edit a suitable common note. Then run:

```sh
/Users/s/Documents/项目/S-notes/.venv/bin/python -B /Users/s/Documents/项目/S-notes/scripts/greencloud_human_check.py --title "某篇笔记标题"
```

Use the exact visible title of one active common note with full or redacted AI
visibility. The script performs a read-only metadata lookup; missing, duplicate,
hidden or non-common selections stop before publication. Duplicates show IDs,
categories and timestamps so S can choose another note or explicitly pass an ID.
The original positional note_id invocation remains supported. One focused test
using fictional data covers lookup boundaries, unchanged DB bytes and both CLI
paths; PASS. This script-only follow-up did not change Cloud deployment or rerun
the full Step 5 suite. This manually publishes and reads the note
through both Cloud actor MCPs. S has confirmed the updated content as recorded
above. PUSH=NO.

## Post-Vibe audit — P1 closed

Claude Code independent read-only Post-Vibe audit of
`4d18fdcd5287cbee147305f6bd7d0d31fc1c67e2..1b32415`:

```ini
P0=0
P1=1
P1_PRODUCTION_WORKTREE_DEPENDENCY=CLOSED
P2=4_NONBLOCKING
```

The single P1 was that the production LaunchAgent's `WorkingDirectory` pointed
at the isolated Codex worktree, so `src.actor_mirror_publish` ran from it.
Closure (2026-09-29):

- Formal main fast-forwarded to `1b32415` (ff-only; no merge commit, rebase or
  reset); six untracked user documents untouched.
- Old agent booted out; its plist was moved to
  `~/Library/Application Support/S-notes/backups/greencloud-publisher-worktree-retire-20260929-000131/`
  (0700).
- New plist rendered by the existing `scripts/greencloud_publish_schedule.py`
  with `--repo /Users/s/Documents/项目/S-notes`, then bootstrapped. Live agent:
  `WorkingDirectory=/Users/s/Documents/项目/S-notes`, program
  `/Users/s/Documents/项目/S-notes/.venv/bin/python -B -m src.actor_mirror_publish publish`,
  RunAtLoad run exit 0.
- Publisher smoke from formal main: both actors `UNCHANGED`, exit 0, no failure
  or retry recorded.
- No live LaunchAgent, publisher config, or GreenCloud runtime/wrapper/sudoers
  references the worktree. The clean, fully merged worktree was then removed
  with `git worktree remove` (branch `codex/gc35-step05-cloud-read` kept).
  GreenCloud was not changed.

Non-blocking maintenance notes, intentionally not implemented:

1. Actor mapping duplication: `SUPPORTED_ACTORS`, receiver `ALLOWED_ACTORS`,
   Cloud UID bindings and publisher status tuple have no cross-check test
   (all drift fails closed).
2. Deployment record small drift: runtime manifest deploy-asset hashes stay at
   `e85d7ed`; INSTALL.md, the fixed rollback and `requirements-installed.txt`
   were added after first install; `/srv/snotes/rollback-step05.pre-closeout.sh`
   is superseded and must not be run.
3. Human check output — CLOSED on main: it now prints per-actor metadata,
   `safe_text_length` and a 100-character preview instead of full `get_note`
   payloads. Its script-only common eligibility SQL is kept as-is.
4. Cloud reader does not re-assert mirror `target_actor` (receiver validation
   and OS group permissions already enforce it; defense-in-depth only).

## Runtime re-pin (2026-10-01)

After the P1 closure the formal repository itself became the construction
checkout (Step 7 branch), so the publisher again imported whatever branch was
checked out. The agent now runs a `git archive` export of formal main
`fdbb6eb97f90b6cbf50b62df196582f9f7ad51b2` at
`~/Library/Application Support/S-notes/runtime/greencloud-publisher/<rev>/`
(`REVISION` file, no `.git`), installed per `deploy/greencloud/INSTALL.md`
("Mac publisher runtime"). All 50 publisher-imported `src` files are byte-identical
to runtime source `e85d7ed`; interpreter is unchanged.

- Previous plist moved to
  `~/Library/Application Support/S-notes/backups/greencloud-publisher-devcheckout-retire-20261001-162326/`.
- RunAtLoad run exit 0; manual publish from the export: both actors `UNCHANGED`
  (no upload needed), exit 0, no failure or retry. Cloud `current.db` mtimes
  match the last recorded successes. GreenCloud was not changed.
- `scripts/greencloud_publish_schedule.py` now takes `--source` and refuses a
  directory with `.git` or without `REVISION`.
