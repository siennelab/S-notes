# MCP R1 Design

Task 13A prepares the S-notes application and its permission boundary for a
future MCP adapter. It does not install an MCP SDK, run a server, open a public
port, connect a tunnel, or connect ChatGPT.

Task 13B adds the local headless adapter. It uses the dedicated Python 3.12 MCP
environment and stdio only. It does not connect a tunnel, ChatGPT, or any
internet service and does not change the packaged desktop App.

## One Database, One Safety Service

- Notes, permissions, proposals, history, settings, and audit logs remain in the
  same local SQLite database.
- `src/services/ai_access_service.py` is the only business boundary intended for
  future MCP tools. An MCP adapter must not query SQLite directly.
- The service exposes fixed operations only. It does not accept SQL, SQL
  fragments, caller-built filters, full-body replacement, deletion, bulk
  rewriting, visibility changes, or target-permission changes.
- R1 does not create a second safety/search database, vector database, embedding
  index, or cloud semantic-search path.

## Stable Permission Values

Stored AI visibility values are stable codes:

- `hidden` — UI label `AI不可见`
- `full` — UI label `AI完整可见`
- `redacted` — UI label `AI脱敏后可见`

Legacy Chinese values remain readable during migration. New writes only store
the codes. Empty, corrupt, and unknown values fail closed to `hidden`.

AI targets have stable IDs independent of display names. Current IDs are
`common`, `gale`, `clawbot`, `codex`, `ke-den`, and `gemini`. The UI continues
to show 通用, Gale, Clawbot, Codex, ke-den, and Gemini. Legacy names G经理,
微信 clawde, and ke-den Claude migrate to the corresponding stable IDs.
Unknown targets are recorded as migration issues and receive no permission;
they are never mapped to 通用.

## Migration and Backup

The Task 13A migration:

1. detects an older schema before changing it;
2. creates a timestamped full SQLite backup;
3. performs schema creation, value conversion, target-relation rebuilding, and
   setting seeding in one transaction;
4. records before/after counts and unknown values;
5. rolls back the entire transaction on failure;
6. is safe to run again.

SQLite connections retain foreign-key enforcement and use a five-second busy
timeout. The migration does not change the normal editor's autosave semantics.

## Safe Read Order

Every safety-service read checks, in order:

1. the note exists and is not deleted;
2. visibility is a recognized non-hidden value;
3. the note targets `common` or the stable caller target ID;
4. a `redacted` note is redacted locally across title, directory, tags, and
   body;
5. matching, ranking, snippets, and returned counts use only that safe text;
6. the local call log receives an aggregate entry without a full note body.

An inaccessible note behaves exactly like a missing note for title, body, tag,
directory, ID, facet, and result-count reads.

R1 search covers title, tags, directory, and safe body. Ranking is title exact,
title partial, tags, directory, then body, with update time only as a tie-break.
Results are unique per note, default to 10, cap at 20, and support offsets.

## Safe Writes

All write operations take a stable actor target ID and request ID. Modifications
also take the expected note update timestamp. Structured statuses include
`created`, `updated`, `pending_confirmation`, `already_processed`, `conflict`,
`not_found`, `rejected`, and `failed`.

- Create defaults to direct creation with `full` visibility and only the caller
  target. The default `AI新增笔记是否需人工确认` setting is off.
- Modification defaults to a proposal. The default
  `AI修改笔记是否需人工确认` setting is on.
- Allowed modifications are prepend, append, title, ordinary tags, and moving
  to an existing directory.
- Prepend and append accept only the added text. No API accepts a replacement
  full body.
- Direct modifications always recheck access and the expected update time,
  save a pre-change history snapshot, update transactionally, and log the
  result.
- A repeated request ID returns the recorded result and never repeats the
  mutation.

## AI Center

The previous AI待确认 entry is now AI中心, with four pages:

- 待确认 — retains legacy receipt review/edit/accept/reject/processed-delete
  behavior and also hosts create/prepend/append/metadata proposals;
- 修改历史 — lists local pre-change snapshots, shows prior content, and
  restores only after a second confirmation;
- 调用记录 — shows time, actor, operation, request ID, target note, result
  count, handling mode, status, duplicate state, and failure reason without
  copying full note bodies;
- 设置 — persists the two create/modify confirmation switches in SQLite.

Restoring a history record is transactional and first records another snapshot
of the current note, so restoration is itself reversible.

## Deferred Work

Task 13A/13B deliberately defer:

- R1.1 ChatGPT interactive panels;
- AI-material filtering and grouping by target;
- search aliases and semantic expansion;
- a Mac resident-service inventory and health checks;
- Secure MCP Tunnel, runtime key, and ChatGPT App integration (Task 13C);
- vector, Ollama, sqlite-vec, and cloud search.

## Task 13B Local Server Boundary

- `src/mcp_server/` is a thin MCP adapter. Its tool layer imports only the
  Task 13A safety service and never imports a SQLite connection.
- The formal transport is stdio. stdout is protocol-only; safe process
  diagnostics use stderr.
- Startup fixes one stable actor target ID from user-local configuration or an
  explicit launch environment. Actor, database path, and permission controls
  are absent from every tool schema.
- Startup performs a read-only fail-closed health check for database existence,
  `quick_check`, schema version, foreign keys, and enabled actor target. The
  server does not initialize or migrate a database.
- The exact tools are `search_notes`, `get_note`, `list_directories`,
  `list_tags`, `create_note`, `prepend_note`, `append_note`,
  `update_note_metadata`, and `get_operation_status`.
- Inputs reject unknown fields and bound page/content sizes. Writes accept
  plain text, use stable request IDs, and retain Task 13A confirmation,
  idempotency, conflict, history, and aggregate-log behavior.
- `get_note` exposes an opaque per-revision `version` as the preferred
  round-trip conflict credential. All three modification tools use the same
  validator; the legacy `updated_at` credential remains strict but compares
  normalized ISO 8601 instants instead of presentation strings. Transactional
  rechecks still reject every stale revision.
- There are no resources, prompts, deletion, full-body overwrite, arbitrary
  update, SQL, permission, target, filesystem, shell, network, or HTTP tools.
- Inspector is a disposable localhost acceptance harness only. It does not
  change the formal stdio transport.
- S completed the disposable Inspector acceptance on 2026-07-18, including a
  successful opaque-version write followed by a new-request stale-version
  conflict with no rejected content entering the note.
- The complete operational contract is in
  [MCP_SERVER_RUNBOOK.md](MCP_SERVER_RUNBOOK.md).

Python runtime unification remains a later independent task. R1.1 interactive
panels also remain deferred.
