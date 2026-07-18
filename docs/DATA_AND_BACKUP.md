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
  data/
    s_notes.db
  exports/
    ai_readbox/
    backups/
    json/
    markdown/
  imports/
    openclaw/
  config/
```

## Database

The live SQLite database belongs under:

```text
~/Library/Application Support/S-notes/data/s_notes.db
```

The repository may contain placeholder directories, but it must not contain the
live user database.

## Backups

SQLite backups belong under:

```text
~/Library/Application Support/S-notes/exports/backups/
```

The project currently has SQLite backup support and code-level restore
hardening. A complete user-facing restore flow is still a later task.

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
