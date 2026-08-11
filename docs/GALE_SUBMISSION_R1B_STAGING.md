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

## Manual R2 Mac pull

The command is intentionally one-shot:

```bash
.venv/bin/python gale_production_pull_helper.py \
  --mode staging \
  --data-root /private/tmp/snotes-r1b-example
```

The command uses the current R2 Gale composition and the fixed staging outbox
transport. The explicit data root must remain isolated from the formal S-notes
root. It captures the remote payload without printing note content, commits the
local pending receipt, sends acknowledgement, and exits.

## Backup boundary for a later production task

A backup can be made while the writer is quiesced with SQLite's online backup
API or `sqlite3 outbox.db ".backup <root-owned-0600-target>"`. The target must
be outside the mirror tree and must include the retained acknowledged rows.
This staging task does not schedule or transmit backups.
