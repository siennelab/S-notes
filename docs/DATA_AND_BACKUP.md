# Data and Backup

S-notes keeps user-writable files outside the source tree and outside the
packaged app.

## Application Support Root

User data root:

```text
~/Library/Application Support/S-notes/
```

Expected structure:

```text
S-notes/
  s_notes.db
  backups/
    migrations/
  exports/
    ai_readbox/
    json/
    markdown/
  imports/
    openclaw/
  config/
```

## Database

The live SQLite database belongs under:

```text
~/Library/Application Support/S-notes/s_notes.db
```

The repository may contain placeholder directories, but it must not contain the
live user database.

## Backups

SQLite backups belong under:

```text
~/Library/Application Support/S-notes/backups/
```

The project currently has SQLite backup support and code-level restore
hardening. A complete user-facing restore flow is still a later task.

Before a Task 13A schema migration, S-notes creates a timestamped full SQLite
backup under `backups/migrations/`. The schema/value migration then runs in one
transaction and records before/after counts plus unknown permission values.

### Verified Manual Database Recovery

Use this narrow manual path only with an already verified SQLite backup:

1. Close S-notes normally and confirm it is no longer using the database.
2. Preserve the current faulty `s_notes.db` as a separately named backup; do
   not delete it.
3. Select the intended verified backup from the application backup directory.
4. Copy that backup to an isolated temporary directory and require
   `PRAGMA integrity_check` to return `ok`.
5. With S-notes still closed, copy the verified backup into place as the formal
   `s_notes.db`.
6. Restore the recorded owner and file mode of the formal database.
7. Start S-notes normally.
8. Confirm that the application database layer opens the database without a
   schema or migration error.
9. If validation fails, close S-notes and restore the separately preserved
   faulty-database copy with its recorded owner and file mode.

## Regular Exports

Regular exports belong under:

```text
~/Library/Application Support/S-notes/exports/json/
~/Library/Application Support/S-notes/exports/markdown/
```

## AI-Safe Exports

AI-safe exports belong under:

```text
~/Library/Application Support/S-notes/exports/ai_readbox/
```

AI-safe export files must respect the user's explicit visibility and redaction
choices.

## Imports

Incoming AI memory receipt material belongs under:

```text
~/Library/Application Support/S-notes/imports/openclaw/
```

Import into notes must go through the confirm-first receipt inbox.

## Configuration

Private local configuration belongs under:

```text
~/Library/Application Support/S-notes/config/
```

Private configuration must not be committed or packaged.

## Packaging Rule

The macOS app bundle and repository must not include:

- User SQLite databases.
- SQLite backups.
- Regular exports.
- AI-safe exports.
- Import payloads containing private content.
- Private local configuration.
