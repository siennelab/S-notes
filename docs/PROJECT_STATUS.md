# S-notes Project Status

This is the current repository handoff for S-notes R1.

## Current Baseline

- Repository: `aistacey2006/S-notes`
- Task 12 starting commit: `1091ff3c45e832f996fa4d549603a8d3ca719fa5`
- App version: `1.0.0`
- Build: `1`
- Platform: macOS arm64
- User data root: `~/Library/Application Support/S-notes/`
- Bundle ID: `de.ke-den.snotes`

## Completed Capabilities

- Local PySide6 desktop app with SQLite persistence.
- Notes, category tree, tags, search, and smart views.
- Editor workflow and formatting toolbar.
- Regular export.
- AI-safe export.
- AI memory receipt import through a confirm-first inbox.
- Related-notes drawer.
- UI R2 with persistent night, slate, and clay themes across the main window,
  dialogs, menus, and related-notes surfaces.
- UI R2.1 strengthens the light-slate sidebar hierarchy with separate control,
  hover, and divider roles plus a white selected-note row, narrow accent marker,
  and no shadow.
- The wider editor keeps title, metadata, toolbar, and body on one left axis.
  Its accepted wide-window gaps are approximately 100px left / 50px right,
  while narrow windows retain the adaptive layout without horizontal clipping.
- Theme-aware rich-text display, including readable legacy foreground and code
  background handling without storing theme colors in note HTML.
- Conservative Markdown source-marker cleanup for the current selection or the
  full note, with one-step undo and URL preservation.
- User-data relocation to Application Support.
- SQLite backup support and code-level restore hardening.
- Internal macOS arm64 app packaging.

## Packaging Status

- Current internal release: S-notes R1 / `v1.0.0-build.1` / macOS arm64
  internal.
- Build entrypoints:
  - `Build-S-notes.command` for a Finder-friendly local build launch.
  - `scripts/build_macos_app.sh` for the shell packaging workflow.
- The current package is ad-hoc signed.
- There is no Developer ID notarization, Intel build, or automatic update path
  yet.

## Version Boundary

`v1.0.0-build.1` is an internal macOS arm64 R1 baseline. It is suitable as a
handoff point for local validation and the next planned implementation tasks,
but it is not a public notarized distribution.

## Safety Boundary

- User data must stay outside the repository and packaged app.
- The packaged app must not include the user database, backups, exports, imports,
  or private configuration.
- AI visibility levels are hard privacy boundaries.
- AI writeback must go through the confirm-first receipt inbox.
- MCP work must not expose arbitrary SQL.

## Confirmed Mainline

- Task 12: Completed. UI R2 delivers the persistent night / slate / clay
  three-theme system, the accepted R2.1 slate contrast and wider editor layout,
  legacy rich-text readability, and conservative Markdown marker cleanup. S
  completed real interactive App acceptance, including the approximately
  100px / 50px wide-window editor gaps and narrow-window behavior.
- Task 13: Local MCP interface R1.

The UI R2 visual contract is recorded in
[UI_DESIGN_SYSTEM.md](UI_DESIGN_SYSTEM.md). Task 12 does not alter stored data,
autosave, AI visibility, export safety, deletion, or related-note semantics.

## Should Do

- Complete the user-facing backup restore flow and validation UX.
- Add a local unified verification path for source run, packaging assumptions,
  data location, and documentation drift.

## Candidate Directions

- Developer ID signing and notarization.
- Intel or universal macOS build.
- Automatic update strategy.
- More import/export formats after the R1 safety boundaries remain stable.

## Not In Current Todo

- Cloud sync.
- Multi-user collaboration.
- Mobile clients.
- Direct arbitrary SQL access over MCP.
- Public release distribution.
