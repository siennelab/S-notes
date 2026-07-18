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

## User Data

User-writable data is stored at:

```text
~/Library/Application Support/S-notes/
```

The packaged app must not include user databases, backups, exports, or private
local configuration.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Project Documents

- [Project status](docs/PROJECT_STATUS.md)
- [Design boundaries](docs/DESIGN.md)
- [Data and backup](docs/DATA_AND_BACKUP.md)
- [Release notes](docs/RELEASE.md)
