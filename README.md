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
- A single local AI safety service with stable permissions and safe search,
  prepared for a later MCP adapter without installing MCP components.

## User Data

User-writable data is stored at:

```text
~/Library/Application Support/S-notes/
```

The packaged app must not include user databases, backups, exports, or private
local configuration.

## Run

Create the repository virtual environment once during setup:

```bash
python3 -m venv .venv
```

Use its interpreter explicitly for installation and source runs:

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

VS Code workspace settings pin `${workspaceFolder}/.venv/bin/python`; newly
created integrated terminals activate that environment automatically.

## Test

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Project Documents

- [Project status](docs/PROJECT_STATUS.md)
- [Design boundaries](docs/DESIGN.md)
- [Data and backup](docs/DATA_AND_BACKUP.md)
- [MCP R1 foundation design](docs/MCP_R1_DESIGN.md)
- [Release notes](docs/RELEASE.md)
