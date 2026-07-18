# S-notes Python Environment

## Python

- Prefer the repository's existing `.venv/bin/python` for every Python command.
- Do not substitute a system Python, create a second virtual environment, or
  delete, recreate, upgrade, or migrate `.venv` unless the user explicitly asks.

## Data Safety

- Do not read or modify the real user database during automated checks.
- Run source and Qt smoke checks with a temporary HOME and temporary database,
  then use a normal application close or `QTimer` exit path.
- Do not commit build artifacts, user data, exports, imports, backups, private
  configuration, or local secrets.

## Workflow

- Before editing, check the current branch and Git status.
- If unrelated working-tree changes appear, fail closed: preserve them and stop
  before staging, restoring, or overwriting anything.
- Combine low-risk, reversible checks into one continuous verification flow.
- Stop for user input when scope is unclear or an action is high-risk or
  irreversible.
