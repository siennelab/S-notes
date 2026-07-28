# Gale minimal mirror publishing

This function package publishes an already generated Gale-safe mirror. It does
not open an S-notes source database, generate a mirror from live data, or read
redaction rules.

Real S-notes data remains outside the allowed gate. Development, automated
tests, and the first Oracle acceptance use only the fictional fixture created
by `tests_mcp.prepare_gale_remote_fixture`.

## User controls

The editor action menu contains:

- `立即发布给 Gale`;
- a checkable `自动发布给 Gale` setting.

The manual action runs the same publisher as the command line and uses a
background Qt thread. The editor remains responsive. A successful publish
shows only `已发布给 Gale`. A failed publish shows a short safe reason and
states that the previous version remains in use when rollback was confirmed.

The command-line equivalents are:

```bash
.venv/bin/python -m src.gale_publish publish
.venv/bin/python -m src.gale_publish status
```

The default safe-mirror input is the app data root's
`exports/gale/gale-mirror.db`. The publisher never substitutes the normal
S-notes database for a missing mirror.

## Automatic timing

The app checks only the safe mirror file:

- 15 minutes after the most recent mirror change;
- at 30 minutes after the first pending change when changes continue;
- once after wake, or once when macOS reports that the network is available
  again;
- no sooner than 5 minutes after a failed automatic attempt.

An already successful SHA-256 is not published again. One local file lock and
one Oracle file lock prevent overlapping work. Closing S-notes disables new
automatic work; if a publish is already switching versions, the app waits for
that bounded operation to finish before closing.

## Oracle activation

The fixed activation wrapper is:

```text
deploy/oracle/snotes-gale-activate
```

It accepts only one lowercase SHA-256 and one positive note count. All service
names and filesystem locations are constants in the publisher. It cannot run
an arbitrary user-supplied command.

The publisher layout contains only:

```text
incoming/
current/
previous/
```

System `scp` writes through the fixed `incoming` path. The two parent
directories therefore grant traverse-only access to other local users
(`0751`), while denying directory listing and writes. `incoming` is owned by
the SSH upload user at `0700`; `current` and `previous` remain
`root:snotes-gale` at `0750`, and every active mirror remains owned by
`snotes-gale` at `0600`.

The 8877 service reads `current/gale-mirror.db`. The 8767 rollback service
continues to read its existing mirror and is not restarted or reconfigured.

Activation performs:

1. remote SHA-256 and complete existing mirror verification;
2. `current` to `previous`, then atomic incoming-to-current replacement;
3. restart of only `snotes-gale-mcp-oidc-staging.service`;
4. local listener, mirror, search, and read probes using the existing
   `gale_remote_service` path;
5. automatic restoration and restart of the previous mirror if any post-switch
   check fails.

Events contain only UTC time, SHA-256, note count, status, and a short reason
code. Note titles, bodies, directories, tags, OAuth material, SSH details, and
environment values are not logged.

## Dependencies and boundaries

No new Python dependency is required. Oracle continues to use the exact
versions in `requirements-mcp.txt`, including FastMCP 3.4.4.

This package does not change ChatGPT Draft publication state, Auth0, Caddy,
Cloudflare DNS, OCI rules, Basic Memory, or the 8767 service. It does not add a
queue, database service, Docker component, web console, release archive, or
multi-user protocol.
