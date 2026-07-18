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

## Registered Next Mainline

- Task 12: UI R2, complete day/night dual-theme pass.
- Task 13: Local MCP interface R1.

## Later Confirmed Work

- Backup recovery: complete user-facing restore flow and validation UX.
- Local unified verification: one repeatable local check path for source run,
  packaging assumptions, data-path safety, and document drift.

## Notes

- Do not infer unrecorded implementation promises from this task ledger.
- UI work must not change stored data meaning.
- AI and MCP work must preserve the safety boundaries in
  [DESIGN.md](DESIGN.md).
