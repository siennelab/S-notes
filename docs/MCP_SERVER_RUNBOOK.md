# S-notes Local MCP Server Runbook

The current R2 local MCP server is `src/snotes/transport/mcp/server.py` and its
supported stdio launcher is `scripts/run_snotes_mcp_stdio.sh`. Validate it with
the isolated Package 5 protocol tests:

```bash
.venv/bin/python -m unittest tests.test_package5_local_mcp_protocol -v
```

## GreenCloud 3.5 step 1: raw Markdown read

The unrestricted local MCP tool list now also includes `get_note_markdown(note_id)`.
It returns `note_id`, `version`, verbatim `markdown`, and
`dialect: "snotes-markdown-v1"` (with the usual response `status`). See
[S-notes Markdown dialect v1](SNOTES_MARKDOWN_DIALECT_V1.md).

Access requires full visibility for the fixed actor and an existing note-modification
capability in the effective server profile (`edit_note`, `prepend_note`, `append_note`, or
`update_note_metadata`). Read-only mode cannot grant it. Redacted, hidden,
unauthorized, deleted, missing, and capability-denied notes return `not_found`.
Existing restricted profile allowlists are unchanged; this step does not expand
Gale or other restricted profiles. `search_notes` and `get_note` still return safe
plain text.

No renderer, canonicalizer, note write, or version bump runs during this read.
The existing metadata-only `get_note` call audit records the access. This is the
sole database write: the audit repository inserts into `ai_call_logs` in its own
transaction. “No write transaction” applies to the note-reading path; it cannot
literally apply to the entire audited request while preserving existing audit.
Tests verify both a read with all writes forbidden when audit is stubbed and a
real audited call whose only database mutation is the call log.

Real Claude Code discovery and human checks remain a separate acceptance gate:
refresh the client tools, read a full note containing bold/link/size Markdown,
and confirm a redacted note returns `not_found`. Server registration and stdio
protocol tests are not evidence of real Claude Code discovery.

## GreenCloud 3.5 step 3: precise Markdown edits (candidate)

The unrestricted local MCP profile adds
`edit_note(request_id, note_id, expected_version, edits)`, where `edits` is a
nonempty array of `{old_markdown, new_markdown}` objects. First call
`get_note_markdown`; copy its opaque `version` and exact Markdown fragments.
Input follows `snotes-markdown-v1`. Preserve unknown S-notes directives.

Every old fragment must occur exactly once in the same original Markdown.
Zero matches, multiple matches, overlapping edits, and whole-document rewrites
are rejected. Edits are applied together or none are applied; no fuzzy matching,
section merge, or `new_body` parameter exists. Edits introducing raw HTML are
rejected instead of silently losing it during canonicalization. Keep `request_id`
stable on retry.
Only full-authorized active notes are eligible; inaccessible notes use the
existing `not_found` response, without revealing their existence.

The existing modify-confirmation setting selects AI Center pending review or
direct execution. Both paths use the existing Milkdown canonicalizer. Legacy
noncanonical content still requires review. AI Center shows each old → new pair
and the full final canonical diff. Accept rechecks the stored edits, current
actor authorization, and version before saving; a failure leaves formal body,
history, revision, and timestamp unchanged. A successful change updates history,
revision, timestamp, version, and receipt through the existing transaction.

Candidate verification uses temporary HOME and databases only. The formal App
is not replaced. Human acceptance must cover:

1. Edit bold text and a link on a test note; inspect AI Center diff, Accept, open.
2. Submit an edit, then manually change the note; Accept must conflict without
   overwriting the manual change.
3. Attempt an edit on a redacted note; it must be denied.
4. Run one simple create / append / prepend / metadata regression each.
5. In a fresh Claude Code session, discover `get_note_markdown`, `edit_note`,
   `prepend_note`, `append_note`, and `update_note_metadata` together.

This remains a candidate until S passes that human gate. Do not close 3.5-0,
fast-forward main, replace the formal App, or push before the closeout gate.

## Yan local Codex profile

Use the dedicated local stdio wrapper for Yan:

```bash
/Users/s/Documents/项目/S-notes/scripts/run_snotes_yan_mcp_stdio.sh
```

The wrapper fixes `SNOTES_MCP_ACTOR_TARGET_ID=yan`, resolves the canonical
S-notes data root, and projects the same note and Reminder tools as the current
Clawbot profile. Tests may opt into an isolated temporary data root with
`SNOTES_YAN_ALLOW_DATA_ROOT_OVERRIDE=1`; normal clients must not set it.

## Gale Secure MCP Tunnel Candidate

Use the dedicated Gale Tunnel stdio wrapper for ChatGPT / Secure MCP Tunnel:

```bash
/Users/s/Documents/项目/S-notes/scripts/run_snotes_gale_tunnel_mcp_stdio.sh
```

The wrapper fixes `SNOTES_MCP_ACTOR_TARGET_ID=gale` and exposes only the Gale v3
product tools:

- `search_notes`
- `get_note`
- `list_directories`
- `list_tags`
- `list_ai_metadata_options`
- `create_note_submission`

`create_note_submission` is a local projection onto the existing MCP create
receipt path. It must return an AI Center pending proposal under actor `gale`;
it must not directly create a formal note. Use `idempotency_key` as the stable
retry key for one submission.

The Secure MCP Tunnel command value for the candidate is:

```text
/Users/s/Documents/项目/S-notes/scripts/run_snotes_gale_tunnel_mcp_stdio.sh
```

S must complete the ChatGPT-side association and Actions refresh. Do not record
Tunnel IDs, runtime keys, or other secrets in this repository.

The Task 13A/13B material retained below records the historical R1 server. Its
retired doctor, adapter, and fixture commands are not current operating
instructions.

## Historical R1: Two Separate Python Environments

- Desktop App: repository `.venv`, Python 3.13.x, PySide6 6.10.3.
- MCP Server: `/Users/s/.venvs/snotes-mcp`, Python 3.13.x,
  `mcp[cli]==1.28.1`.

They remain separate because the accepted desktop runtime is Python 3.13.x,
while MCP 1.28.1 is installed in the dedicated Python 3.13 environment. Task
13B does not upgrade, replace, or merge either environment. The locked MCP
dependency is recorded in `requirements-mcp.txt`; do not add it to the desktop
`requirements.txt`.

Do not use `mcp install`. Claude Desktop is not required and must not be
installed or configured for this task.

## Historical R1: Fixed Actor Configuration

The ChatGPT Business connection identity is Gale's stable target ID: `gale`.
The display name is not a permission key.

Create this user-local file outside Git:

`~/Library/Application Support/S-notes/config/mcp_server.json`

```json
{
  "actor_target_id": "gale"
}
```

The server also accepts `SNOTES_MCP_ACTOR_TARGET_ID` as an explicit launch-time
override. Environment configuration takes precedence over the JSON file. The
actor is never a tool input. If the configured target is missing, unknown, or
disabled, startup fails closed; there is no fallback to `common` and no target
guessing.

Do not commit the local config. Although the target ID is not an API secret,
tools do not return it. Future Tunnel IDs, Runtime API keys, and other secrets
must not be placed in this file or the repository.

## Historical R1: Database and Redaction Paths

Normal startup resolves the same user data root as the desktop App:

`~/Library/Application Support/S-notes/`

The default database is `s_notes.db`; the default redaction rules are
`config/local_redaction_rules.json`. Working-directory changes do not move
these paths.

The following explicit overrides exist for isolated tests and controlled local
launches:

- `SNOTES_MCP_DATA_ROOT`
- `SNOTES_MCP_DATABASE_PATH`
- `SNOTES_MCP_REDACTION_RULES_PATH`
- `SNOTES_MCP_CONFIG_PATH`
- `SNOTES_MCP_ACTOR_TARGET_ID`
- `SNOTES_MCP_READ_ONLY=1`
- `SNOTES_MCP_PYTHON` for the shell scripts only

Do not let a model choose these values. Do not point automated tests at the
real database.

## Historical R1: Doctor

This retired command is retained only as a Task 13B record. Do not use it for
the current R2 MCP; run the Package 5 test command above instead.

Run the read-only startup check before the formal server:

```bash
./scripts/check_snotes_mcp.sh
```

It checks the exact Python and MCP versions, project imports, database
existence, SQLite `quick_check`, Task 13A schema version, foreign-key setting,
and the configured actor target. It neither migrates nor writes the database,
starts no MCP session, opens no port, and starts no Tunnel.

Exit code `0` means healthy. Exit code `2` means fail closed. Doctor output is
human JSON and is separate from server mode, so it cannot pollute MCP stdout.

## Formal stdio Startup

```bash
./scripts/run_snotes_mcp_stdio.sh
```

The script resolves the repository root from its own location and defaults to
the repository `.venv/bin/python`. Server stdout is reserved exclusively
for MCP JSON-RPC frames. Human-safe errors and SDK logs go to stderr. Stop the
server with Ctrl+C or SIGTERM; normal MCP client shutdown also closes it.

The server opens no public listener, creates no fixed localhost port, starts no
GUI, makes no network request, and does not start a Tunnel.

## Current R2 Automated Verification with Temporary Data

The Package 5 protocol test creates temporary data and launches the current R2
server through the supported stdio script:

```bash
.venv/bin/python -m unittest tests.test_package5_local_mcp_protocol -v
```

## Historical R1: MCP Inspector Acceptance

This section records the completed Task 13B acceptance. Its retired fixture
module and historical checkout path are not current operating commands.

Create a non-private disposable fixture first:

```bash
fixture_root="$(mktemp -d /private/tmp/snotes-inspector.XXXXXX)"
/Users/s/.venvs/snotes-mcp/bin/python \
  -m tests_mcp.prepare_inspector_fixture "$fixture_root"
```

Node and npx must already exist. Do not install them with Homebrew for this
task. The official Inspector command is:

```bash
npx @modelcontextprotocol/inspector@0.22.0 \
  -e SNOTES_MCP_DATABASE_PATH="$fixture_root/s_notes.db" \
  -e SNOTES_MCP_REDACTION_RULES_PATH="$fixture_root/redaction_rules.json" \
  -e SNOTES_MCP_CONFIG_PATH="$fixture_root/config/mcp_server.json" \
  -- /Users/s/Documents/项目/S-notes/scripts/run_snotes_mcp_stdio.sh
```

Task 13B's local acceptance launch used Inspector 0.22.0. If that package is
absent from the npm cache, npx will request a temporary network
download. Report that behavior and obtain S's approval before continuing; do
not install it globally or silently. Inspector temporarily serves its local UI
and authenticated proxy on localhost (normally ports 6274 and 6277). This is
only a local acceptance harness, not the formal MCP transport. Keep its random
proxy authentication enabled, never set `HOST=0.0.0.0`, and stop it with
Ctrl+C after acceptance.

In Inspector, verify the nine tools, schemas, `search_notes`, paged `get_note`,
direct create/prepend/append, pending status via request ID
`inspector-pending-create`, same-request `already_processed`, stale-version
`conflict`, and a guessed-ID `not_found`.

`get_note` returns both `updated_at` and an opaque `version`. For
`prepend_note`, `append_note`, and `update_note_metadata`, copy `version`
unchanged into the required `expected_version`. The MCP tool schema exposes only
`expected_version` (v2.1 FINAL); the legacy `expected_updated_at` credential
stays inside the application layer for internal callers. After one successful
change, reusing the old version with a new request ID must return `conflict`.

## Logs

- Task 13A call logs remain aggregate records in the same local SQLite database.
- Formal server process diagnostics go to stderr only.
- The server creates no separate plaintext body log by default.
- Request bodies and full note content are not logged; search logs store only
  query length, and write logs store bounded operation summaries.

## Common Failures

- `actor_target_id_missing`: create the local actor config or set the explicit
  environment variable.
- `actor_unavailable`: the stable target ID does not exist or is disabled. Fix
  the local configuration/data; do not fall back to another actor.
- `schema_mismatch`: launch the accepted desktop App normally to complete its
  supported migration, then rerun doctor. The MCP Server does not migrate.
- `database_unavailable` or a locked response: close the conflicting writer or
  wait, then retry the same operation with the same request ID. Do not force an
  overwrite.
- MCP version missing/mismatched: S must decide whether to repair the dedicated
  environment. Do not install into `.venv`.
- stdout pollution: stop immediately. Formal stdout must contain only JSON-RPC;
  human diagnostics belong on stderr.

## Future Task 13C Boundary

Task 13B needs no Tunnel ID, Runtime API key, ChatGPT custom App, payment, or
public endpoint. Task 13C will supply the actual tunnel-client executable and
its authenticated arguments. Its stdio command value will be this stable local
entrypoint:

```text
--mcp.command /Users/s/Documents/项目/S-notes/scripts/run_snotes_mcp_stdio.sh
```

Do not invent or commit the remaining Task 13C command, Tunnel ID, or API key.
