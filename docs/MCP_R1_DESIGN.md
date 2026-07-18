# MCP R1 Foundation Design

Task 13A prepares the S-notes application and its permission boundary for a
future MCP adapter. It does not install an MCP SDK, run a server, open a public
port, connect a tunnel, or connect ChatGPT.

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

Task 13A deliberately defers:

- R1.1 ChatGPT interactive panels;
- AI-material filtering and grouping by target;
- search aliases and semantic expansion;
- a Mac resident-service inventory and health checks;
- MCP Server, tunnel, runtime key, and ChatGPT App integration;
- vector, Ollama, sqlite-vec, and cloud search.
