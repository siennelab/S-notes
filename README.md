# S-notes

S-notes is a local-first macOS notes app built with PySide6 and SQLite.
It keeps the app source, packaged app, and user data separated so that private
notes stay in the user's Application Support directory instead of the repository.

## Current Capabilities

- Local SQLite note storage.
- Category tree, tags, search, and smart views.
- Rich text editing with toolbar actions.
- Regular export and AI-safe export flows.
- AI memory receipt import through a confirm-first inbox.
- Related-notes drawer and local backup support.
- AI中心 with confirmation proposals, modification history, call logs, and
  persistent AI write settings.
- AI permissions and safe search shared with the local stdio MCP adapter.

## User Data

User-writable data is stored at:

```text
~/Library/Application Support/S-notes/
```

The packaged app must not include user databases, backups, exports, or private
local configuration.

## Run

Run these commands from the repository root. Use Python 3.13 and Node.js
20.19+ (20.x) or 22.12+ with npm, as required by the locked editor build.
Create the repository virtual environment once on a fresh checkout; reuse an
existing `.venv`:

```bash
python3.13 -m venv .venv
```

Install the desktop dependencies and build the local Markdown editor assets:

```bash
.venv/bin/python -m pip install -r requirements.txt
(
  cd web/markdown-editor
  npm ci --no-audit --no-fund
  npm run build
)
```

The editor bundle is generated under `web/markdown-editor/dist/`; Node.js is
needed to build it, not to run the App afterward.

For a first run or development, use a temporary HOME and isolated data directory:

```bash
snotes_dev_home="$(mktemp -d /private/tmp/snotes-dev-home.XXXXXX)"
env -u S_NOTES_REMOTE_REMINDER_ENDPOINT -u S_NOTES_RETIRE_REMINDER_HELPER \
  HOME="$snotes_dev_home" \
  S_NOTES_DATA_ROOT="$snotes_dev_home/data" \
  S_NOTES_REMINDER_AUTHORITY=local \
  .venv/bin/python app.py
```

This command scopes the HOME change to that App process. The temporary data
remains at `$snotes_dev_home/data` after exit. For ongoing use, choose a persistent
development directory instead. `S_NOTES_DATA_ROOT` must be an absolute, writable
directory, not a symlink or the normal Application Support data root. Omitting
it uses the normal user data location described above.

Source runs require an explicit `S_NOTES_REMINDER_AUTHORITY`: `local` keeps
Reminder in the selected local database; `remote` also requires
`S_NOTES_REMOTE_REMINDER_ENDPOINT` and separately provisioned credentials for
your own deployment. The example selects `local` and clears inherited remote
settings. Packaged Apps carry their authority in `Contents/Info.plist`; their
launch environment must not request a conflicting authority.

VS Code workspace settings pin `${workspaceFolder}/.venv/bin/python`; newly
created integrated terminals activate that environment automatically.

## Local MCP

Install the additional MCP dependencies into the same repository environment:

```bash
.venv/bin/python -m pip install -r requirements-mcp.txt
```

After the local App has initialized the development data directory, use the
same `$snotes_dev_home` from the Run example for a development stdio server:

```bash
env -u S_NOTES_REMOTE_REMINDER_ENDPOINT -u S_NOTES_RETIRE_REMINDER_HELPER \
  HOME="$snotes_dev_home" \
  S_NOTES_DATA_ROOT="$snotes_dev_home/data" \
  S_NOTES_REMINDER_AUTHORITY=local \
  SNOTES_MCP_READ_ONLY=1 \
  ./scripts/run_snotes_mcp_stdio.sh
```

The existing read-only profile fixes the actor to `clawbot`; it does not grant
access to notes. Configure AI visibility and targets in the App using the same
data directory. The launcher executes
`src/snotes/transport/mcp/server.py` from the current checkout. stdout is MCP
protocol output; diagnostics go to stderr. Let an MCP client launch this command,
or use Ctrl+C to stop a terminal session. See the [MCP runbook](docs/MCP_SERVER_RUNBOOK.md)
for packaged production launchers and historical profile notes.

## Build and Production Deployment

The macOS arm64 packaging workflow requires the desktop and MCP dependencies,
`requirements-build.txt` (PyInstaller and its hooks), Node.js/npm, and the macOS
tools checked by `scripts/build_macos_app.sh`:

```bash
.venv/bin/python -m pip install \
  -r requirements.txt -r requirements-mcp.txt -r requirements-build.txt
./scripts/build_macos_app.sh --candidate
```

`--candidate` builds a local-Reminder App at `dist-d1-candidate/S-notes.app`.
It does not install the App or load a background job. Use an isolated HOME/data
directory when trying that build.

The default `scripts/build_macos_app.sh` profile (also used by
`Build-S-notes.command`) builds `dist/S-notes.app` for the maintainer's configured
remote Reminder deployment. Production build, helper refresh, SSH, and service
runbooks are **maintainer-only**; they require an independently configured
deployment and are not prerequisites for local use. See [Release notes](docs/RELEASE.md).

## Test

```bash
.venv/bin/python -B scripts/run_tests.py
```

The runner gives each test module an isolated temporary HOME and database, then
reports the aggregate passed, failed, error, and skipped counts. This keeps Qt
and WebEngine process state from leaking between otherwise independent modules.

## Project Documents

- [Project status](docs/PROJECT_STATUS.md)
- [Design boundaries](docs/DESIGN.md)
- [Data and backup](docs/DATA_AND_BACKUP.md)
- [MCP R1 foundation design](docs/MCP_R1_DESIGN.md)
- [Release notes](docs/RELEASE.md)
