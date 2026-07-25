# Gale Oracle read-only MCP (R1A Function Package B)

## Boundary

This service is a headless OAuth resource server for one disposable, fully
fictional `gale-mirror.db`. It does not open the Mac S-notes database, load
redaction rules, start the GUI or local MCP server, write notes, publish
mirrors, or synchronize data.

The Mac S-notes database remains the only full source of truth. The mirror is
schema version 1 and contains only Function Package A's Gale-safe derived
records. A `source_note_id` is an R1A identifier within its source database; it
is not a cross-device UUID.

Function Package C (Mac-to-Oracle publication), submissions, changes,
revisions, UUID migration, and semantic/vector/hybrid search are not included.
Real S-notes data and real redaction rules remain prohibited.

## Runtime and tools

The service runs with the existing MCP dependency set plus PyJWT:

```text
mcp[cli]==1.28.1
PyJWT[crypto]==2.13.0
```

The desktop repository uses Python 3.13. Oracle deployment uses a dedicated
virtual environment and must not reuse Basic Memory's runtime. The code is also
compatible with Oracle's current Ubuntu Python 3.12; the exact interpreter used
must be recorded in the deployment evidence.

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

Then configure only non-secret resource-server values. Do not put any OAuth
client secret or token in this environment:

```bash
export SNOTES_GALE_MIRROR_ROOT=/private/tmp
export SNOTES_GALE_MIRROR_PATH=/private/tmp/gale-mirror.db
export SNOTES_GALE_HOST=127.0.0.1
export SNOTES_GALE_PORT=8767
export SNOTES_GALE_RESOURCE_URL=https://snotes.ke-den.de/mcp
export SNOTES_GALE_AUDIENCE=https://snotes.ke-den.de/mcp
export SNOTES_GALE_ISSUER_URL=https://AUTH0_TENANT_DOMAIN/
export SNOTES_GALE_JWKS_URL=https://AUTH0_TENANT_DOMAIN/.well-known/jwks.json
export SNOTES_GALE_ALLOWED_SUBJECTS=AUTH0_USER_SUBJECT
export SNOTES_GALE_EXPECTED_OWNER_UID="$(id -u)"
```

Run the safe startup check:

```bash
/Users/s/.venvs/snotes-mcp/bin/python -m src.gale_remote_mcp doctor
```

The formal Oracle service uses:

```bash
/opt/snotes-gale-mcp/.venv/bin/python -m src.gale_remote_mcp serve
```

It binds only `127.0.0.1:8767`. Uvicorn access logging is disabled.

## OAuth architecture

The MCP is only an OAuth resource server. Auth0 owns login, consent,
authorization codes, client registration, access tokens, and refresh tokens.
The MCP stores none of them.

Required values:

```text
flow: OAuth Authorization Code + PKCE (S256)
resource/audience: https://snotes.ke-den.de/mcp
API scope: gale.read
access-token signing: RS256
issuer: exact Auth0 tenant HTTPS issuer
JWKS: exact Auth0 tenant JWKS endpoint
subject restriction: exact permitted fictional-test user subject
```

For every request, the server validates the JWT signature, allowed algorithm,
issuer, audience, expiration, not-before when present, subject, client
identifier, and scopes. Tokens without `gale.read`, tokens containing an
unapproved API scope such as a write scope, JWKS failures, and all verification
exceptions fail closed. The protected resource metadata is available at:

```text
/.well-known/oauth-protected-resource
/.well-known/oauth-protected-resource/mcp
```

Anonymous MCP requests receive `401` and a `WWW-Authenticate: Bearer`
challenge containing the resource metadata location.

### Manual Auth0 and ChatGPT Business Draft block

These steps must be completed by S in the two secure web UIs. Never paste a
client secret, token, authorization code, or refresh token into Codex, a report,
or ordinary shell history.

1. In ChatGPT Business, switch to the intended workspace. Open
   **Workspace settings → Permissions & Roles** and confirm
   **Connected Data Developer mode / Create custom MCP connectors** is enabled.
2. Open **Workspace settings → Apps → Create**. Enter a distinct draft name,
   for example `S-notes Gale Fictional R1A`. Set the MCP endpoint to
   `https://snotes.ke-den.de/mcp`, select **OAuth**, and keep this page open.
3. Copy the callback URI exactly as ChatGPT displays it. Do not invent,
   shorten, or add a trailing slash to it. Do not click Publish.
4. In Auth0 Dashboard, open **Applications → APIs → Create API**. Use a
   non-private display name, set **Identifier** to
   `https://snotes.ke-den.de/mcp`, select **RS256**, and create it. In its
   permissions/scopes, add only `gale.read`. Enable offline access if the
   current tenant UI exposes that API setting.
5. In Auth0 Dashboard, confirm the test login identity under
   **User Management → Users**. Copy its Auth0 `user_id` directly into the
   root-only Oracle environment file as `SNOTES_GALE_ALLOWED_SUBJECTS`; do not
   paste it into this conversation.
6. Preferred registration: if the current ChatGPT creation screen exposes a
   Client ID Metadata Document URL, use Auth0's current
   **Applications → Create Application → Import from URL / CIMD** flow with
   that exact HTTPS URL. Require Authorization Code, PKCE S256, the exact
   ChatGPT callback URI, the API resource above, and only `gale.read` plus
   identity/refresh scopes required by the UI.
7. If ChatGPT does not expose CIMD, use the approved registration fallback:
   in Auth0 **Applications → Create Application**, create a confidential
   regular web application for this one Business draft. Add only the exact
   callback URI shown by ChatGPT, enable Authorization Code + PKCE S256 and
   refresh-token rotation, and authorize only the API and `gale.read`.
8. For the static-client fallback, copy the Auth0 Client ID into ChatGPT's
   **OAuth client ID** field. Move the Client Secret directly from Auth0's
   secure field to ChatGPT's **OAuth client secret** field. In all evidence
   represent it only as `***REDACTED***`.
9. Request `gale.read` and `offline_access` if the current ChatGPT scope field
   permits them. Do not add a write scope. Click **Scan Tools**, complete the
   Auth0 login/consent, and confirm exactly `search_notes` and `get_note`.
10. Click **Create** only to save the app as a Draft. Do not Publish. In a new
    chat, enable the Dev-labelled draft and run the fictional search/read
    acceptance matrix. Reconnect or reauthorize if the current UI requires it.

If any current UI lacks the required OAuth flow, callback registration,
resource/audience, scope, or refresh-token support, stop with
`AUTH_BLOCKED=YES`. Do not use No Auth, Mixed anonymous access, URL tokens,
custom API keys, or ad-hoc headers.

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
by the service. Basic Memory's service user, venv, data, token gate, and route
must not be reused.

The current Oracle has one public Caddy already owning 80/443 for Basic Memory.
The S-notes block must be added as a separate hostname without changing the
existing `memory.ke-den.de` route. Validate the complete candidate with
`caddy validate` before reload and keep a root-only pre-change copy plus
SHA-256. The S-notes site discards proxy access logs, forwards only `/mcp` and
the two protected-resource metadata paths, caps request bodies at 1 MiB, and
returns 404 elsewhere.

## Tests

No dependency installation is part of a test run. The current repository venv
does not contain pytest, so the unittest-compatible suites can be run as:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest \
  tests.test_gale_mirror_export \
  tests.test_ai_safe_materialization \
  tests.test_ai_foundation

PYTHONDONTWRITEBYTECODE=1 /Users/s/.venvs/snotes-mcp/bin/python -m unittest \
  tests_mcp.test_gale_remote_mcp \
  tests_mcp.test_mcp_protocol
```

Tests create only temporary fictional databases and rules. They cover exact
tool count/schema, all four FTS fields, safe reads, missing IDs, special
characters, limits, prompt text as inert data, raw-canary exclusion,
read-only byte stability, `0600`, owner/path gates, OAuth positive/negative
claims, challenge metadata, and log redaction.

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

## Current primary references

- OpenAI, Developer mode and MCP apps in ChatGPT:
  <https://help.openai.com/en/articles/12584461>
- Auth0, Dynamic Client Registration:
  <https://auth0.com/docs/get-started/applications/dynamic-client-registration>
- Auth0, Authorization Code Flow with PKCE:
  <https://auth0.com/docs/get-started/authentication-and-authorization-flow/authorization-code-flow-with-pkce>
- Auth0, Create applications and CIMD registration:
  <https://auth0.com/docs/get-started/auth0-overview/create-applications>
