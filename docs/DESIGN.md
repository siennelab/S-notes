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

- AI visibility uses stable stored codes `hidden`, `full`, and `redacted` with
  Chinese-only display labels in the UI. Unknown values fail closed.
- Note permissions reference stable AI target IDs, not mutable display names.
- AI create defaults to direct creation; AI modification defaults to the shared
  confirmation inbox. Both behaviors are persistent user settings.
- Direct AI modification is limited to prepend, append, title, ordinary tags,
  and moving to an existing directory. It always keeps request deduplication,
  conflict detection, a pre-change snapshot, and an audit record.
- No AI business function accepts full-body replacement, deletion, visibility
  mutation, or target-permission mutation.
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
- Task 12 provides exactly three explicit themes: night, slate, and clay. Theme
  selection is presentation state and must not rewrite note content.
- All UI colors come from the centralized `ThemeTokens` source. Shared QSS is
  generated from those tokens instead of copied per theme.
- Rich-text theme colors are display state. Legacy fixed foreground and code
  backgrounds are normalized at load time, while current theme colors are
  applied through display-only editor selections and are not stored in note
  HTML.
- Markdown marker cleanup is an editor command over the current selection or
  full note. It preserves content and URLs, uses the existing undo and autosave
  paths, and does not create a second note storage format.
- The related-notes drawer uses the same background hierarchy as the left
  sidebar. The editor remains a distinct reading surface with an 800px maximum
  content width.
- UI hierarchy uses background levels, spacing, and typography rather than
  visual-layer borders or shadows.
- Detailed UI rules and the preflight checklist live in
  [UI_DESIGN_SYSTEM.md](UI_DESIGN_SYSTEM.md).

## MCP Boundary

- Task 13A establishes the local MCP R1 application boundary.
- MCP must not expose arbitrary SQL directly.
- Future MCP tools may call only the fixed operations in the unique AI safety
  service. They must not query SQLite directly or build a second note/search
  database.
- MCP Server, tunnel, runtime keys, and ChatGPT connection remain deferred.
- The complete foundation contract is in [MCP_R1_DESIGN.md](MCP_R1_DESIGN.md).
