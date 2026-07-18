# S-notes Codex Tasks

This file records the repository-level task ledger. Older task details are kept
brief when the current repository history does not prove the full original
handoff text.

## Completed

- Task 0: Completed. Initial PySide6 + SQLite project skeleton, repository
  protection shape, app entrypoint, and minimal local database initialization.
- Task 1: Completed. Basic notes and autosave path.
- Task 2: Completed. Sidebar category tree and note ordering.
- Task 3: Completed. Tags, search, and smart views.
- Task 4: Completed. Editor stability and editing workflow repairs.
- Task 5: Completed. Regular export flow.
- Task 6: Completed. AI-safe export flow.
- Task 7: Completed. AI memory receipt import through a confirm-first inbox.
- Task 8: Completed. Related-notes drawer.
- Task 9: Completed. Local data path migration to Application Support and
  repository/user-data separation.
- Task 10: Completed. SQLite backup and restore hardening at the code level.
- Task 11: Completed. macOS app packaging for internal arm64 build.
- Task 11.2: Completed. Internal release handoff baseline for
  v1.0.0-build.1.
- Task 12: Completed. UI R2 adds the persistent night / slate / clay theme
  system, stronger light-slate sidebar contrast with separate control, hover,
  and divider roles, and a white selected-note row with a narrow accent marker
  and no shadow. The wider editor keeps title, metadata, toolbar, and body on
  one left axis, with accepted wide-window gaps of approximately 100px / 50px
  and adaptive narrow-window behavior. Legacy rich-text readability, shared
  themed controls and icons, and conservative Markdown marker cleanup retain
  their existing behavior. S completed real interactive App acceptance.

## Registered Next Mainline

- Task 13A: Completed and sealed at
  `899bfac49c8c8fd00fa53ad53a79b55174a2b1d7`. MCP R1 Foundation covers stable
  visibility codes, stable target IDs, AI中心, one safety service over the
  existing SQLite database, safe local search, controlled
  create/prepend/append/metadata writes, request deduplication, conflict
  detection, modification history, call logs, and two persistent confirmation
  settings.
- Task 13B: Local MCP Server was formally accepted by S through MCP Inspector
  on 2026-07-18. It is stdio-only, fixes the Gale stable target ID outside tool
  inputs, exposes exactly nine approved tools, and has dedicated
  temporary-database protocol tests. Its first Inspector pass found an
  `updated_at` round-trip conflict; the sealed repair adds an opaque `version`
  credential plus normalized legacy timestamp comparison. Repeat acceptance
  proved create/get/version append, revision change, stale-version conflict,
  and unchanged formal content after the rejected write. Task 13C, App builds,
  Tunnel, and ChatGPT connection remain outside this task.

## Task 13 Deferred

- R1.1 ChatGPT interactive panels.
- AI-material filtering/grouping by target.
- Search aliases and semantic expansion.
- Mac resident-service inventory and health checks.
- Task 13C Secure MCP Tunnel, runtime key, and ChatGPT App connection.
- Python runtime unification; it is a separate later task and is not part of
  Task 13B.

## Later Confirmed Work

- Backup recovery: complete user-facing restore flow and validation UX.
- Local unified verification: one repeatable local check path for source run,
  packaging assumptions, data-path safety, and document drift.

## Notes

- Do not infer unrecorded implementation promises from this task ledger.
- UI work must not change stored data meaning.
- UI work follows [UI_DESIGN_SYSTEM.md](UI_DESIGN_SYSTEM.md).
- AI and MCP work must preserve [DESIGN.md](DESIGN.md) and the detailed
  [MCP_R1_DESIGN.md](MCP_R1_DESIGN.md) contract.
