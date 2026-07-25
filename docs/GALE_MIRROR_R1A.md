# Gale Mirror R1A

## What it is

The Gale mirror is a disposable, read-only SQLite derivative of an explicitly
selected S-notes database. The desktop S-notes database remains the only full
primary database.

The mirror contains only notes that the existing AI safety service allows Gale
to read:

- `full` notes retain their complete safe fields.
- `redacted` notes store only the current derived title, body, directory, and
  tags after the existing replacement rules are applied.
- hidden, deleted, trashed, and non-Gale-authorized notes are absent.

The mirror contains no call logs, operations, receipts, GUI settings,
credentials, replacement rules, or other primary-database business tables.
Its FTS5 index covers title, body, directory, and tags.

## Export

The command requires explicit source, output, and replacement-rule paths. For
development and tests, all three paths must point to fictional temporary data:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m src.gale_mirror export \
  --source-db /path/to/fictional/source.db \
  --output /path/to/fictional/gale-mirror.db \
  --redaction-rules /path/to/fictional/redaction_rules.json \
  --actor gale
```

The source database is opened with SQLite `mode=ro`. A new mirror is built in
the output directory, validated, restricted to mode `0600`, and atomically
replaces the requested output only after validation succeeds. A failed build
leaves an existing mirror unchanged and removes its temporary database files.
The command prints only aggregate counts, schema version, output path, file
mode, and SHA-256; it does not print note content or complete titles.

## Verify

Verification needs only the mirror:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m src.gale_mirror verify \
  --mirror /path/to/fictional/gale-mirror.db \
  --actor gale
```

Verification checks SQLite integrity, mirror schema/version, required
tables/indexes, aggregate metadata, note/FTS counts, allowed visibility modes,
and read-only reopening.

## R1A boundaries

- `source_note_id` is the current note ID inside one S-notes primary database.
  It is not a cross-device UUID and must not be presented as one.
- The mirror can be deleted and regenerated at any time.
- R1A does not provide submissions, incoming inboxes, modifications,
  bidirectional sync, UUID/revision migration, semantic search, embeddings,
  vectors, networking, Oracle deployment, or automatic publication.
- FTS5 is basic local full-text search, not semantic search.
- Real note content and real replacement rules are not authorized for this
  implementation or its automated acceptance tests.
