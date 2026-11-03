# S-notes architecture

The current App, local MCP, AI acceptance chain, GreenCloud services and Reader
use the R2 owners under `src/snotes`. Some independent remote runtimes and
operational commands remain in flat modules; `src/services` is not entirely a
frozen R1 reference directory.

## Layers and composition

- `domain`: business records and rules, independent of other project layers.
- `application`: use cases and capability/repository ports; it depends on
  domain and other application modules, never concrete infrastructure.
- `storage`: SQLite/persistence adapters. Dependencies on application are
  limited to `application.ports`; UI, transport and background are not owners
  of R2 SQL.
- `ui`, `transport`, `background`, `exporting`: adapters invoking application
  owners. Ordinary adapters do not import storage or assemble other adapters.
- `bootstrap.py` composes the App and local MCP services; `gale_bootstrap.py`
  shares headless composition with the packaged Gale helper. The local MCP
  launcher (`transport/mcp/server.py`) calls the bootstrap. The isolated
  `ui/submission_smoke.py` entry also composes a test App, not normal UI.
- Flat executable modules compose the independent Cloud/Oracle runtimes.
  `src.remote_reader` injects `SQLiteCloudMirrorReader` through the
  application-owned `ReaderMirror` / `ReaderService` boundary into the Web
  adapter. Reader routes never construct storage.

`NoteService`, `DirectoryService` and `TagService` own ordinary note lifecycle,
hierarchy and tags. `AiWriteAuthorization` owns AI write authorization;
`AiSafeReadService` supplies shared authorization/materialization/redaction.
AI proposals and human acceptance use the existing receipt/operation owners.
`GreenCloudReceiptService` requests mirror publication through
`ActorMirrorPublishPort`; the composition root supplies the concrete publisher.
Remote MCP auth adapters share the public functions in `remote_auth_support`.

## Authority and persistence

- Mac SQLite is the Notes authority; `content_markdown` is the sole body source.
  R2 connections require the Markdown schema marker; migration from the old
  body format is explicit, not a fallback write path.
- GreenCloud is the formal Reminder authority. Local mode is an explicit
  isolated/developer configuration; the legacy local scheduler refuses remote
  authority. UI snapshots/caches do not become a write repository.
- Actor mirrors are derived, filtered snapshots. Reader consumes only its
  already-authorized S mirror, read-only. Proposal/outbox/receipt stores carry
  pending requests and delivery decisions, not a second formal Notes database.
- R2 SQLite statements and connections belong under `src/snotes/storage/**`.
  Established remote capsules listed below retain their own SQL ownership;
  they are not permission to put SQL into a new application or Web module.

## Flat production modules and compatibility

`scripts/architecture_scope.py` is the reviewed inventory of non-layered modules:

| Role | Current modules |
| --- | --- |
| Composition | Root App/helpers; actor publisher, Cloud read/internal/remote MCP, Claude Web, Reader and Gale publish entry points; `services/actor_mirror_publish_service` |
| Cloud runtime | `reminder_cloud`, `cloud_outbox`: readiness/init/backup and service assembly |
| Remote persistence | `services/gale_submission_outbox` (shared Oracle/GreenCloud implementation), `gale_remote_service`, `gale_mirror_service` |
| Remote adapter/operator | `gale_remote_mcp`, staging MCP, `services/gale_oauth`, `services/gale_publish_service` (Oracle publication/activation/rollback) |
| Support | `app_paths` runtime paths; standalone search/target/tag/time helpers |

The gate also checks the installed Python outbox dispatcher, outbox backup and
Reminder actor bridge under `deploy/greencloud`; installers, provisioning and
verification scripts are operational tooling, not service modules.

Frozen R1 definitions are `database`, `redaction`, `services/ai_access_service`
and `services/ai_safe_materialization`. Historical parity/export fixtures still
use them. `gale_mirror_service` retains one explicit compatibility dependency on
the old safe-export function; current Oracle validation/search does not call
that export chain. The current R2 App and scheduled actor publisher use the R2
permission and storage owners. A new application dependency on frozen R1 is
an error, not an allowed fallback.

## Architecture gate and tests

```bash
.venv/bin/python -B scripts/check_architecture.py
.venv/bin/python -B scripts/run_tests.py
```

The checker parses source without importing it. It scans all Python under
`src`, root Python entry points and the declared deployed runtime scripts.
Unknown flat modules or unknown R2 layer directories fail until their role is
reviewed; relocating code outside `src/snotes` does not bypass checking.

Existing R2 dependency direction, SQL ownership, cross-module private imports
and syntax checks remain hard failures. Domain/application imports of flat
infrastructure or composition modules also fail, including package/alias
import forms. Known remote capsules retain their established persistence and
composition profiles; they cannot introduce UI dependencies or new R1 write
consumers. Frozen R1 is syntax-checked without imposing R2 rules on its old
internal structure. Regression fixtures exercise these positive/negative
boundaries, including application-to-concrete-publisher imports.

Files over 500 lines, dynamic imports and possible duplicate owners remain
human-review warnings. This AST gate does not prove runtime behavior, actor
permissions or the absence of every private attribute access.

The standard test runner invokes each existing `tests/test_*.py` module in a
separate process using the current repository interpreter, temporary HOME/DB,
and disabled bytecode writes. The two editor UI fixtures receive their required
temporary HOME prefixes. No skip/expectedFailure is used to obtain a pass.
Historical selective-deployment tests use synthetic payload/source fixtures;
they do not treat today's HEAD as the old construction snapshot.
