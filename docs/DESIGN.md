# S-notes Design Boundaries

S-notes is a local-first notes app. The default design assumption is that notes,
exports, backups, imports, and private configuration belong to the user on the
local machine.

## Local-First Data Model

- App code and user data are separate.
- Source files and packaged app artifacts must not contain the live user
  database, backups, exports, AI-safe export files, imports, or private config.
- User-writable data lives under
  `~/Library/Application Support/S-notes/`.
- Repository fixtures may keep placeholder directories, but not private user
  content.

## AI Safety Boundaries

- AI visibility has three hard levels:
  1. Not visible to AI.
  2. AI-safe export only.
  3. Explicitly selected for AI-facing workflows.
- AI writeback must never bypass the confirm-first receipt inbox.
- AI receipt imports must be reviewable before they become ordinary note data.
- AI-safe exports must preserve redaction and selection intent.

## Data Mutation Rules

- Delete-first behavior should prefer soft delete where practical.
- Complex writes must remain transactional so partial failures do not leave
  mixed state.
- Backup and restore work must protect the current database before replacement.
- Import flows must fail closed when the incoming structure is invalid or
  ambiguous.

## UI Rules

- UI redesigns may improve layout, theme, navigation, and affordances.
- UI redesigns must not change database meaning, visibility semantics, import
  semantics, export semantics, or deletion semantics as a side effect.
- Task 12 is the next UI mainline and is limited to a full day/night dual-theme
  pass unless the task is explicitly expanded.

## MCP Boundary

- Task 13 is the next local MCP mainline.
- MCP must not expose arbitrary SQL directly.
- MCP commands should be narrow, auditable app operations with explicit input
  validation and privacy-preserving defaults.
