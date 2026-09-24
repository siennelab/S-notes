# S-notes Project Status

This is the current repository handoff for S-notes. Frozen R1 information is
retained below; active R2 package closeouts are recorded first.

## 2026-09-23 — GreenCloud 3.5 Step 2 CLOSED

Step 2A–2E and the isolated candidate App human Gate are complete. S confirmed
PREPEND=PASS, METADATA=PASS, RESTART=PASS. Candidate:
`dist-d1-candidate/S-notes.app`; isolated data root:
`data/gc35-step2-candidate-b392fe5`, reopened through its isolated launcher.

Prepend acceptance preserved the original body and correctly displayed the new
opening content, headings, bold, link and existing font size. Metadata acceptance
updated title/tags without rewriting the body. Both results survived restart;
accepted proposals did not return to pending.

Candidate build boundary fix `3de2543` removes unconditional GUI canonicalizer
imports from shared application modules; only real body writes load it lazily.
Packaged helper `--version`, ad-hoc signature verification and no-Node candidate
launch/AI Center smoke passed. Final bounded regression: **39/39 PASS**
(Step 2C write paths, Step 2D invariants/timestamps, Step 2E acceptance and helper
import boundary). No candidate rebuild was performed during closeout.

```text
STEP2=CLOSED
STEP2A_ROUNDTRIP_SCAN=PASS
STEP2B_CANONICALIZER=PASS
STEP2C_WRITE_PATHS=PASS
STEP2D_VERSION_INVARIANTS=PASS
STEP2E_ACCEPTANCE=PASS
STEP2_CANDIDATE_BUILD=PASS
STEP2_HUMAN_GATE=PASS
PREPEND_HUMAN_GATE=PASS
METADATA_HUMAN_GATE=PASS
RESTART_HUMAN_GATE=PASS
FORMAL_DB_MUTATED_DURING_STEP2_TESTING=NO
APPEND_NEWLINE_CONTRACT=DOUBLE_NEWLINE
CANONICALIZER_FIXED_POINT=YES
MAX_PASSES=4
NODE_RUNTIME_REQUIRED=NO
LEGACY_NONCANONICAL_DIRECT_MODIFY=FORCED_PENDING
PREVIEW_ACCEPT_SINGLE_SOURCE=YES
ACCEPTANCE_PERMISSION_RECHECK=YES
METADATA_ALLOWLIST=title,directory,tags
AI_VISIBILITY_METADATA_WRITE=BLOCKED
AI_TARGETS_METADATA_WRITE=BLOCKED
```

Existing legacy Notes were not batch-canonicalized. Candidate artifacts remain
gitignored; the formal App was not replaced and the formal DB was not modified.
This closes Step 2, not a production deployment. Step 3 `edit_note` is not started.

## 2026-09-23 — 3.5-0 Reminder CLOSED

S completed human acceptance with PASS and accepted the current implementation.
Multi-notify defaults OFF; ON sends at 7 / 3 / 1 days before and on the
deadline, falling back to the start date when no deadline exists. OFF retains
the existing single normal notification. Occurrence/node dedupe and no overdue
chasing remain in place. Only day-interval recurrence supports recalculation
from actual completion; calendar recurrence retains its existing rules.
The existing AI update tool can change multi_notify without broader authority.
The first-entry Reminder table widths and resize behavior are fixed.

Validation: 59 targeted tests passed (56 core/protocol and 3 Reminder UI).
The formal dist/S-notes.app was rebuilt, signature-verified, launched, and
opened on Reminder with the existing Application Support data preserved.

No restoration of the old public ntfy.sh topic or phone push acceptance is
required for 3.5-0. Real end-to-end multi-notify push acceptance is deferred to
3.5-B Reminder Cloud Authority through the formal chain:
GreenCloud Reminder scheduler → https://ntfy.ke-den.de → new s-* Reminder
topic → iPhone lock-screen human acceptance.

3.5-0 is CLOSED. Notes MCP follow-up and GreenCloud implementation were not
started in this task. The next separately opened task is Notes MCP / Markdown
write.

## 2026-08-12 S-notes / S-lumen Directory Consolidation

The two-directory period is formally closed. `/Users/s/Documents/项目/S-notes`
originally held the repository's common Git directory, while
`/Users/s/Documents/项目/S-lumen` was a linked worktree created for R2
construction. During that construction, active R2 `main`, the production
source, and runtime temporarily lived under S-lumen. This produced the
confusing state in which the product was named S-notes but its active source
was located in S-lumen.

### Old diagnostic branch audit

The retained branch `task-oauth-token-diagnostic-probe` has one historical
unique commit, `7a460736af2e384cca1c7ac5f013349ab4835655`. It was a temporary
OAuth `/token` diagnostic probe whose purpose was superseded by the later
formal implementation. Its residual uncommitted modification/deletion state
represented removal of that probe rather than production work worth carrying
forward.

- `CLASSIFICATION=OBSOLETE`
- `PRESERVATION_NEEDED=NO`
- No residual probe change was merged, cherry-picked, committed, or stashed
  during this consolidation.
- The branch and Git history remain available; `r1-safety-baseline` and the R1
  history also remain available without a separate physical R1 worktree.

### Final production layout and acceptance

`/Users/s/Documents/项目/S-notes` is now the only formal R2 source and Git
worktree. The former S-lumen linked worktree was detached at
`547a6589b83f12ee37f9cacb61fcd120ba51acc3`, verified clean and unused by the
production paths, then removed with `git worktree remove` without force.

- Formal App: `/Users/s/Documents/项目/S-notes/dist/S-notes.app`
- Dock S-notes item: `/Users/s/Documents/项目/S-notes/dist/S-notes.app`
- OpenClaw S-notes MCP:
  `/Users/s/Documents/项目/S-notes/scripts/run_snotes_mcp_stdio.sh`
- Gateway restart: `PASS`
- Gale background helper: healthy and independent under Application Support
- GUI Human Gate: `PASS`
- Gale Human Gate: `PASS`
- Clawde read Human Gate: `PASS`
- Final path Human Gate, including Dock relaunch: `PASS`
- S-lumen linked worktree: Git-native removed

The current Queue remains the three items recorded below: call-log retention
and archival policy; Gale / G submission metadata support for AI visibility,
AI targets, real main/subcategory selection, and tags; and the still-unrecalled
S-notes follow-up, which must remain blank until S remembers it.
`Reminder=NOT_STARTED`; none of these Queue items is implemented by this
consolidation.

## 2026-08-12 Clawde Writes and AI Center UI Hotfix Closeout

### Clawde S-notes confirmed writes

The Clawde write integration was formally sealed at commit
`0936fe4d1f2c306025890f8d38650a39260e42a4`. The fixed actor is `clawbot`, and
the Clawde MCP catalog contains exactly:

- `search_notes`
- `get_note`
- `create_note`
- `append_note`

Both writes remain confirm-first. `create_note` creates an AI Center pending
proposal and creates a formal note only after S accepts it. `append_note`
creates an AI Center pending proposal and appends to the formal note only after
S accepts it. Discarding either proposal does not modify a formal note. A stale
append is rejected, remains pending, and never overwrites the newer formal
note.

S completed the formal WeChat Human Gate:

- `CREATE=PASS`
- `APPEND=PASS`
- `NO_DIRECT_FORMAL_WRITE_BEFORE_ACCEPT=PASS`
- `ORIGINAL_CONTENT_PRESERVED=PASS`
- `NO_DUPLICATE_WRITE=PASS`

### AI Center UI hotfix

Two production UI regressions were repaired and formally accepted by S.

1. The call-log page permanently froze with a large record set. The live
   `ResizeToContents` header mode repeatedly measured whole columns while rows
   were inserted on the main thread. The table now performs one column-width
   calculation after the batch fill. S Human Gate: `PASS`.
2. “立即检查投稿” first caused AI Center to disappear, and the first repair
   still produced a visible disappear-and-return flash. The final cause was an
   unconditional `_activate_host_window()` in the completion callback while
   the host was already visible and active, triggering macOS parent/child
   top-level window reordering. The redundant reactivation was removed;
   worker completion still returns to the UI thread, and receipt/status refresh
   plus Gale business behavior remain unchanged. S confirmed that the same AI
   Center window stays visible throughout the check: `PASS`.

### Follow-up Queue (not implemented by this hotfix)

#### TODO 1 | Call-log size, retention, and archival policy

The call log represents calls made through the formal S-notes interfaces/MCP;
it is not a complete audit of every possible AI access to note data. A local
tool such as Claude Code with filesystem access may read through other paths
that this page cannot cover. Large volumes of routine `gale_pull` and
`gale_publish` events currently have limited diagnostic value.

Design this separately before implementation:

- which events the page displays;
- whether routine background events should be de-noised;
- retention by time or count;
- archive or deletion policy for old records;
- which diagnostic or idempotency evidence must remain.

No arbitrary limit such as 7 days, 30 days, 100 rows, or 500 rows is selected
at this stage.

#### TODO 2 | Gale / ChatGPT submission metadata capability

The current Gale Production submission interface cannot fully provide AI
visibility, AI targets, main category, subcategory, or reliable tag/category
selection semantics. Gale submissions can therefore arrive in AI Center as AI
invisible, with no AI target, or with category suggestions that do not exist in
S-notes, requiring manual correction by S.

A later independent design should bring the Gale/G submission experience at
least to the current Clawde level: correct source actor, reasonable AI
visibility and target defaults, selection from real existing main/subcategory
directories, correct tag transfer, and S retaining final editing authority
before acceptance. This status entry intentionally does not choose an API
shape.

#### TODO 3 | Unrecalled S-notes follow-up

S mentioned another S-notes follow-up on 2026-08-12 but has not yet recalled
the specific item. Add it when S remembers it; do not infer or generate a
replacement task.

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
