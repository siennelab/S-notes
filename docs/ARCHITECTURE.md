# S-notes R2 Architecture

Package 0B establishes the R2 namespace under `src/snotes`. The existing flat
`src/*.py`, `src/services`, and `src/mcp_server` modules remain the frozen R1
reference implementation; they are outside the first architecture-gate scope,
and no product behavior has been migrated yet.

## Layers and dependency direction

- `domain`: pure business meanings and rules. It has no S-notes layer, Qt,
  SQLite, file-I/O, or network dependency.
- `application`: use cases and application-owned capability/repository ports.
  It depends only on `domain`.
- `storage`: SQLite and other persistence adapters. It may depend on `domain`
  and narrowly on `application.ports`; application never imports storage.
- `ui`, `transport`, `background`, and `exporting`: adapters that invoke the
  application layer. They do not import storage or assemble one another.
- `bootstrap.py`: the only composition root and the only place allowed to know
  multiple adapter layers while wiring the application.

All R2 `sqlite3` imports and SQL definitions or execution belong under
`src/snotes/storage/**`. Package 0B contains no SQL and initializes no database.

## Architecture gate

The checker uses only the Python standard library and scans production files
under `src/snotes/**`:

```bash
.venv/bin/python -B scripts/check_architecture.py
.venv/bin/python -B -m unittest tests/test_architecture_gate.py -v
```

Dependency-direction, SQL ownership, cross-module private imports, and syntax
errors fail the gate. Files over 500 lines, dynamic imports, and possible
duplicate authorize/redaction/business owners are warnings for human review.

The package-0B shell proves only that the new composition root can construct a
minimal window titled `S-notes`. It does not connect storage, the editor, AI,
MCP, Gale, background work, import/export behavior, or any formal user data.
