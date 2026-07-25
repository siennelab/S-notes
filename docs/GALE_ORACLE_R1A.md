# Gale Oracle read-only MCP (R1A Function Package B)

## Boundary

This service is a headless Auth0 OIDC proxy and OAuth resource server for one
disposable, fully fictional `gale-mirror.db`. It does not open the Mac S-notes
database, load redaction rules, start the GUI or local MCP server, write notes,
publish mirrors, or synchronize data.

The Mac S-notes database remains the only full source of truth. The mirror is
schema version 1 and contains only Function Package A's Gale-safe derived
records. A `source_note_id` is an R1A identifier within its source database; it
is not a cross-device UUID.

Function Package C (Mac-to-Oracle publication), submissions, changes,
revisions, UUID migration, and semantic/vector/hybrid search are not included.
Real S-notes data and real redaction rules remain prohibited.

## Runtime and tools

The remote service deliberately uses standalone FastMCP in a dedicated virtual
environment. The local desktop MCP remains on the existing MCP SDK:

```text
mcp[cli]==1.28.1
fastmcp==3.4.4
PyJWT[crypto]==2.13.0
```

FastMCP 3.4.4 is pinned because it supplies `Auth0Provider`, OIDC/OAuth proxy
routes, DCR, PKCE, explicit consent, fixed callbacks, signed proxy tokens, and
persistent pluggable storage. This version also contains the 3.4.x OAuth-client
lifecycle, DCR redirect-validation, unsafe-redirect-scheme, host-guard, and
Starlette dependency-floor fixes. Its encrypted store is backed by a dedicated
filesystem directory in this single-instance R1A deployment. Multi-instance
shared storage remains a pre-production item.

The desktop repository uses Python 3.13. Oracle deployment must use a dedicated
virtual environment and must not reuse Basic Memory's runtime. Exact
compatibility is checked against Streamable HTTP, Starlette, Uvicorn, and the
two existing tool schemas; mirror query business logic remains in
`gale_remote_service.py`.

Exactly two tools are registered:

### `search_notes`

Input:

```json
{
  "query": "required string, 1 to 500 characters",
  "limit": "optional integer, default 10, maximum 20"
}
```

Output is structured content with `status`, `result_count`, and `results`.
Each result has `note_id`, safe `title`, a short safe `summary`, safe
`directory`, safe `tags`, and `updated_at`.

### `get_note`

Input:

```json
{
  "note_id": "required positive R1A source note identifier"
}
```

Output is structured content with status `ok`, `not_found`, or `failed`. An
`ok` result contains only the selected mirror record's safe title, content,
directory, tags, timestamps, version, and identifier.

Both tools are annotated read-only, non-destructive, idempotent, and closed
world. There is no SQL, path, bulk-dump, create, edit, move, or delete input.
Text stored inside a note is untrusted data and is never treated as an
instruction.

## Mirror startup gate

The service accepts only an explicitly configured file named
`gale-mirror.db` inside the configured mirror directory. Startup fails closed
unless all checks pass:

- regular non-symlink SQLite file;
- canonical path inside the dedicated mirror directory;
- owner UID matches the configured service UID;
- mode is exactly `0600`;
- SQLite integrity check passes;
- mirror schema version is `1`;
- target actor is `gale`;
- required minimal tables and indexes exist;
- metadata and note counts agree;
- notes and FTS have the same rowid set and safe field content;
- a second `mode=ro&immutable=1` connection opens with
  `PRAGMA query_only=ON`.

Every search/read opens the mirror with `mode=ro&immutable=1`, enables
`query_only`, and closes it after the operation. Immutable mode is appropriate
for R1A because Package B has no publisher and the deployed mirror is not
replaced while the service is running. A future publisher must coordinate
replacement and service restart instead of mutating this file in place.

## Local fictional run

First use Function Package A to create a mirror from an explicitly supplied,
fully fictional temporary source database and fictional rules file:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m src.gale_mirror export \
  --source-db /private/tmp/fictional-source.db \
  --output /private/tmp/gale-mirror.db \
  --redaction-rules /private/tmp/fictional-rules.json
```

The complete proxy requires a root-only/local-test-only environment file with
the resource-server values plus:

```text
Auth0 OIDC configuration URL
fixed Auth0 Regular Web Application client ID and secret
public proxy base URL
mode-0700 encrypted OAuth storage directory
Fernet storage encryption key
independent proxy JWT signing key
explicitly approved MCP client redirect URI patterns
```

Do not put any of those secret values in Git or ordinary shell history. The
checked-in `deploy/oracle/snotes-gale-mcp.env.example` contains placeholders
only. Local automated checks instantiate only fictional credentials and a
fictional OIDC upstream.

For local isolation, use a dedicated environment with
`requirements-mcp.txt`, bind only `127.0.0.1:8877`, use a temporary fictional
mirror and mode-0700 temporary OAuth store, then run:

```bash
PYTHONDONTWRITEBYTECODE=1 <isolated-fastmcp-venv>/bin/python \
  -m src.gale_remote_mcp serve
```

The eventual formal Oracle service command remains:

```bash
/opt/snotes-gale-mcp/.venv/bin/python -m src.gale_remote_mcp serve
```

It is not deployed by the local implementation phase. Uvicorn access logging
is disabled.

## OAuth architecture

Auth0 owns user login and upstream authorization. Standalone FastMCP's
`Auth0Provider` acts as the OIDC/OAuth proxy presented to MCP clients. The
proxy exposes:

```text
/.well-known/oauth-authorization-server
/.well-known/oauth-protected-resource
/.well-known/oauth-protected-resource/mcp
/register
/authorize
/consent
/auth/callback
/token
/mcp
```

`/auth/callback` is fixed. DCR registrations, authorization state, upstream
access/refresh tokens, and proxy token mappings persist in an encrypted
filesystem store. The separate JWT signing key remains stable across service
restarts. Neither upstream nor proxy tokens are passed through to tools.

Required values:

```text
client-facing flow: OAuth Authorization Code + PKCE (S256)
upstream flow: Auth0 Authorization Code + forwarded PKCE (S256)
resource/audience: https://snotes.ke-den.de/mcp
API scope: gale.read
access-token signing: RS256
issuer: exact Auth0 tenant HTTPS issuer
JWKS: exact Auth0 tenant JWKS endpoint
subject restriction: exact permitted fictional-test user subject
fixed upstream callback: https://snotes.ke-den.de/auth/callback
```

Before issuing its own client token, the proxy validates the Auth0 JWT
signature with JWKS, RS256, exact issuer, exact audience, expiration,
not-before, permitted subject, client identifier, and `gale.read`. Tokens
without `gale.read`, tokens containing an unapproved API scope such as a write
scope, JWKS failures, and verification exceptions fail closed. Tool metadata
advertises exactly the `gale.read` OAuth scope. Identity and refresh scopes are
used only by the OIDC flow.

Explicit consent remains enabled. FastMCP's consent binding cookie and CSRF
checks remain enabled to prevent confused-deputy authorization. Registered
client redirect URIs are allowlisted; DCR cannot register an arbitrary remote
callback. Token passthrough is not used.

Anonymous MCP requests receive `401` and a `WWW-Authenticate: Bearer`
challenge containing the resource metadata location.

### Manual secret and external acceptance gate

Local construction uses placeholders and fictional credentials only. After the
local MCP Inspector flow passes against real Auth0, S must use `sudoedit` to
place the real Auth0 client ID/secret and independent storage/JWT keys directly
into the root-owned mode-0600 Oracle environment file. Secrets must never be
pasted into Codex, reports, or ordinary terminal history.

Oracle staging, Caddy changes, and any ChatGPT Business Draft action are
separate approval gates. They are not performed during local implementation.
No Auth, anonymous discovery, URL tokens, custom API keys, token passthrough,
and Mixed Auth are forbidden fallbacks.

## Oracle layout

```text
service user:  snotes-gale (system account, no login shell, no sudo)
application:   /opt/snotes-gale-mcp
configuration: /etc/snotes-gale-mcp
mirror:        /var/lib/snotes-gale-mcp/mirror/gale-mirror.db
runtime:       /run/snotes-gale-mcp
systemd unit:  snotes-gale-mcp.service
backend:       127.0.0.1:8767
public MCP:    https://snotes.ke-den.de/mcp
```

`deploy/oracle/snotes-gale-mcp.service` contains the systemd sandbox.
`deploy/oracle/snotes-gale-mcp.env.example` is a non-secret template.
`deploy/oracle/snotes-gale-mcp.Caddyfile` is an isolated hostname block.
The live environment file is root-owned mode `0600`. The mirror is owned by
`snotes-gale`, mode `0600`; its parent directory is root-owned and not writable
by the service. The mode-0700 OAuth store is the only persistent service-writable
directory. Basic Memory's service user, venv, data, token gate, and route must
not be reused.

The current Oracle has one public Caddy already owning 80/443 for Basic Memory.
The checked-in Caddy block still represents the previously deployed
resource-server-only route and is deliberately not changed during the local
OIDC implementation gate. A future approved staging change must add the proxy
discovery, DCR, authorize, consent, callback, and token paths without changing
the existing `memory.ke-den.de` route. Validate the complete candidate before
reload and keep a root-only pre-change copy plus SHA-256.

## Tests

No dependency installation is part of a test run. The standalone FastMCP suite
uses the separately prepared, pinned environment:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest \
  tests.test_gale_mirror_export \
  tests.test_ai_safe_materialization \
  tests.test_ai_foundation

PYTHONDONTWRITEBYTECODE=1 <isolated-fastmcp-venv>/bin/python -m unittest \
  tests_mcp.test_gale_remote_mcp \
  tests_mcp.test_mcp_protocol
```

Tests create only temporary fictional databases and rules. They cover exact
tool count/schema, all four FTS fields, safe reads, missing IDs, special
characters, limits, prompt text as inert data, raw-canary exclusion,
read-only byte stability, `0600`, owner/path gates, OAuth positive/negative
claims, discovery, DCR, consent/CSRF binding, PKCE, fixed callback, token
passthrough rejection, encrypted storage, restart persistence, refresh, and log
redaction. The synthetic OIDC upstream proves code behavior but does not count
as a real Auth0 MCP Inspector login.

## Logging and retention

Application events contain only timestamp, request ID, tool name, status,
latency, result count, and a fixed error code. They never contain a query,
title, content, result, database path, Authorization header, token, client
secret, authorization code, refresh token, or environment dump. Uvicorn access
logs and the S-notes Caddy site log are disabled. systemd uses journald's host
rotation policy; do not add a second plaintext log.

## Rollback

Rollback is exact and must leave Basic Memory untouched:

1. Stop and disable only `snotes-gale-mcp.service`.
2. Remove only the separately added `snotes.ke-den.de` Caddy block.
3. Restore the pre-change proxy file from its exact root-only backup, compare
   its SHA-256, and validate it before reload.
4. Reload the existing Caddy only after validation.
5. Remove or quarantine only `/opt/snotes-gale-mcp`,
   `/etc/snotes-gale-mcp`, `/var/lib/snotes-gale-mcp`, and the new unit after
   confirming exact ownership.
6. Disable or revoke only this Auth0 test client/application and API grant.
7. Delete or disable only the new ChatGPT Business Draft.
8. Preserve the audit package. Do not touch Basic Memory, its Stage 2
   temporary 80/443 arrangement, the Mac formal database, or any real mirror.

Known deferred Function Package A LOW findings: cleanup after a hypothetical
`chmod` failure (L02) and concurrent export mutual exclusion (L04). Neither is
expanded in Package B. L01 exact FTS rowid mapping and L03 mode `0600` are
enforced here.

Known Package B pre-production item: the encrypted filesystem store is for one
Oracle process. Horizontal/multi-instance deployment requires a shared
transactional store and a separate design review.

## Current primary references

- OpenAI, Developer mode and MCP apps in ChatGPT:
  <https://help.openai.com/en/articles/12584461>
- Auth0, Dynamic Client Registration:
  <https://auth0.com/docs/get-started/applications/dynamic-client-registration>
- Auth0, Authorization Code Flow with PKCE:
  <https://auth0.com/docs/get-started/authentication-and-authorization-flow/authorization-code-flow-with-pkce>
- Auth0, Create applications and CIMD registration:
  <https://auth0.com/docs/get-started/auth0-overview/create-applications>
- FastMCP, OAuth proxy:
  <https://gofastmcp.com/servers/auth/oauth-proxy>
- FastMCP, Auth0 provider:
  <https://gofastmcp.com/integrations/auth0>
