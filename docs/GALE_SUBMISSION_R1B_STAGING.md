# Gale R1B submission staging

This staging path is deliberately separate from the R1A read-only mirror.

## Server boundaries

- service: `snotes-gale-r1b-staging.service`
- listen address: `127.0.0.1:8878`
- code: `/opt/snotes-gale-r1b-staging`
- configuration: `/etc/snotes-gale-r1b-staging/service.env`
- outbox: `/var/lib/snotes-gale-r1b-staging/outbox.db`
- transport command: `/usr/local/sbin/snotes-gale-r1b-outbox`

The service has one tool, `create_note_submission`. It does not load the R1A
mirror paths, production OAuth configuration, or the S-notes database.

`outbox.db` retains every submission. Export changes `queued` to `delivered`;
an acknowledgement changes `delivered` to `acknowledged`. Both delivered and
queued records are eligible for another export until acknowledgement. No
transport operation deletes a row.

## Manual Mac pull

The command is intentionally one-shot:

```bash
S_NOTES_DATA_ROOT=/private/tmp/snotes-r1b-example \
NO_PROXY=127.0.0.1,localhost \
.venv/bin/python -m src.gale_submission_pull status

S_NOTES_DATA_ROOT=/private/tmp/snotes-r1b-example \
NO_PROXY=127.0.0.1,localhost \
.venv/bin/python -m src.gale_submission_pull pull
```

The command rejects an absent override, any root outside `/private/tmp`, a
symlink, and a root whose final component does not start with `snotes-r1b-`.
It uses a short `ssh oracle-a1` command, captures JSON without printing note
content, commits the local pending receipt, sends acknowledgement, and exits.

## Backup boundary for a later production task

A backup can be made while the writer is quiesced with SQLite's online backup
API or `sqlite3 outbox.db ".backup <root-owned-0600-target>"`. The target must
be outside the mirror tree and must include the retained acknowledged rows.
This staging task does not schedule or transmit backups.
