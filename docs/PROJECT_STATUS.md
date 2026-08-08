# S-notes Project Status

This is the current repository handoff for S-notes. Frozen R1 information is
retained below; active R2 package closeouts are recorded first.

## R2 Package 3 Formal Closeout

Package 3 ordinary-product parity is formally accepted from base
`599429935e7071b2da3c78f71c1c34a5b53b82a6`.

### Completed Package 3 Scope

- Ordinary search and exact `#tag` search.
- Two-level `main category -> subcategory` UI and daily workflow; the third
  category level is absent from normal create, edit, move, and filter paths.
- Tags plus the recent and todo smart views.
- Pin/unpin, multi-select, batch move, and drag move.
- Trash, restore, permanent delete, and empty trash, including the accepted
  deleted-directory tree and individual-note restore behavior.
- Related notes and the related-notes drawer.
- Ordinary Markdown, JSON, and SQLite-backup export.
- Frozen-R1 UI composition parity and the accepted Package 3 UI delta fixes
  across night, slate, and clay themes.
- A repository-local `.venv` using the pinned Python 3.13 arm64, PySide6, and
  PyInstaller build baseline; R2 production code has no R1 module dependency.
- Three ownership remediations: one directory-prefix domain rule, tag text
  parse/compare/write owned by `TagService`, and `#tag` interpretation owned by
  the application search entry.
- Removal of the obsolete, intentionally cancelled tag-chip dead chain.

S completed Package 3 functional acceptance, UI parity acceptance, and UI
delta acceptance. Codex audit and post-remediation self-audit passed with
P0=0; Claude independent cross-audit passed with no new blocking finding.
Package 4 has not started.

### Closeout Verification

- Package 3 targeted, parity, and UI tests: PASS.
- Isolated Package 3 GUI smoke: PASS.
- Repository-local arm64 candidate build and code-sign verification: PASS; the
  candidate App was not installed or used to replace the formal App.
- Architecture gate: PASS.
- Full suite: 416/417. The one failure is the pre-existing, non-blocking P2
  startup-error parity item recorded below; this is not a Package 3 regression.

### Package 3 audit residual P2 Queue

These items are recorded for later work and are not fixed by Package 3:

1. `NoteService.notes_by_ids` has zero callers; evaluate deletion later.
2. `TagService.list_tags` / `TagsRepository.list_all` have zero callers;
   evaluate deletion later.
3. Tag normalization is duplicated between `domain/tags.py normalize_tag` and
   `storage/support.py normalize_tag`; `storage/support.py` also retains dead
   `TAG_SPLIT_RE`. Consolidate under one domain owner later.
4. `_ordinary_order` is duplicated in `NoteService` and `ExportService`;
   consolidate later.
5. `MainWindow` retains optional-service / `None` guards even though production
   bootstrap always injects all services. Clean them later together with the
   legacy tests that require those defensive paths.
6. `floating_combo.py` fields `last_popup_direction` and
   `last_popup_surface_geometry` are written but never read; clean them later.
7. `ordered_directories` / refresh-path directory resolution repeatedly opens
   SQLite connections. This is currently only a small performance debt.
8. R2 invalid data-root startup error wrapping does not match the old R1 test
   expectation in
   `test_app_startup_reports_invalid_override_without_opening_gui`. R2 remains
   fail-closed, but currently emits a raw traceback and exits 1 rather than a
   clean error and exit 2. This behavior existed at the Package 3 base and is
   not a Package 3 regression. Later either add an R2 startup error wrapper or
   formally adjust the R1-era test expectation.

### Mandatory Independent Queue: Third-Level Category Schema

> R2 Package 8 正式接班 PASS 后、任何 R2 后续新功能开始之前，
> 立即开独立小包，彻底移除第三级分类 schema 与相关兼容代码。

- S confirmed that formal notes do not use third-level category data.
- Package 3 removed the third level from normal UI and daily workflows.
- The schema remains only because parity packages do not perform schema
  migration.
- This cleanup must not be deferred indefinitely.

### Package 4 Next

The next main package after Package 3 is Package 4: AI Center / AI safety
parity. This records sequence only; Package 4 design and implementation have
not started.

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
- Future MCP adapters must use the single local AI safety service and must not
  maintain a second copy of note/search content.

## Confirmed Mainline

- Task 12: Completed. UI R2 delivers the persistent night / slate / clay
  three-theme system, the accepted R2.1 slate contrast and wider editor layout,
  legacy rich-text readability, and conservative Markdown marker cleanup. S
  completed real interactive App acceptance, including the approximately
  100px / 50px wide-window editor gaps and narrow-window behavior.
- Task 13A: MCP R1 Foundation is implemented on the dedicated
  `task13-mcp-r1-foundation` branch and was formally accepted and sealed at
  commit `899bfac49c8c8fd00fa53ad53a79b55174a2b1d7`. It introduced stable
  visibility codes and target IDs, AI中心, the unique safety service, local
  search, controlled writes, history, settings, and call logs.
- Task 13B: Local MCP Server was formally accepted by S through Inspector
  0.22.0 on 2026-07-18. It adds the separate MCP 1.28.1 / Python 3.12.13 runtime
  declaration, fail-closed doctor, fixed-actor headless stdio server, exactly
  nine approved tools, and temporary-database protocol/security/concurrency
  tests. The first manual pass exposed a presentation-sensitive `updated_at`
  round-trip conflict; the repaired server provides a preferred opaque
  `version`, normalized legacy ISO-time comparison, and protocol regression
  coverage. Repeat acceptance proved a valid append, a changed revision token,
  a stale-version conflict under a new request ID, and no rejected content in
  the final note. Task 13B does not package the desktop App or connect a Tunnel
  or ChatGPT.

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
- Task 13 R1.1 ChatGPT interactive panels (still deferred).
- AI-material grouping by target and later search alias/semantic expansion.
- Mac resident-service inventory and health checks.
- Task 13C Secure MCP Tunnel and ChatGPT App connection after Task 13B is
  accepted.
- Python runtime unification as a separate later task; Task 13B keeps the
  accepted desktop and MCP environments separate.

## Not In Current Todo

- Cloud sync.
- Multi-user collaboration.
- Mobile clients.
- Direct arbitrary SQL access over MCP.
- Public release distribution.
