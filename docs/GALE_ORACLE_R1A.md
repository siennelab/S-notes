# Gale Oracle read-only MCP (R1A Function Package B)

## Current accepted staging status

Oracle deliberately keeps two independent Gale MCP services:

```text
rollback service: snotes-gale-mcp.service
rollback app:     /opt/snotes-gale-mcp
rollback backend: 127.0.0.1:8767

accepted staging: snotes-gale-mcp-oidc-staging.service
staging app:      /opt/snotes-gale-mcp-oidc-staging
staging backend:  127.0.0.1:8877
public endpoint:  https://snotes.ke-den.de/mcp
current Caddy:    snotes.ke-den.de -> 127.0.0.1:8877
```

Port 8767 is the previous fail-closed rollback point and remains active and
enabled. Port 8877 is the Auth0 OIDC Proxy + Mixed Auth staging that passed the
target ChatGPT Business client acceptance. That acceptance does not rename
8877 as production, authorize real S-notes data, prove the complete real Auth0
refresh-token lifecycle, or begin Function Package C.

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

### FastMCP private-seam freeze

The accepted implementation is coupled to FastMCP 3.4.4 private/internal
behavior: internal request handlers, authentication middleware, Starlette
route/application/endpoint replacement, and the serialized `tools/list`
response shape. The logging capability removal and top-level
`securitySchemes` compatibility mirror depend on those seams.

Do not run `pip install -U`, relax `fastmcp==3.4.4`, or replace the accepted
environment in place. Any future version change requires a separate staging
environment and all of the following before a production decision:

1. top-level `securitySchemes` and Mixed Auth targeted tests;
2. the complete MCP regression;
3. the complete desktop regression;
4. public OAuth metadata checks;
5. anonymous initialize, notifications, ping, and `tools/list`;
6. the unauthenticated tool challenge and pre-query fail-closed proof;
7. ChatGPT Business `search_notes` and `get_note`;
8. raw-canary and unauthorized/Codex-only isolation checks;
9. service restart persistence; and
10. a proven rollback to the last accepted version.

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

The accepted 8877 staging service command is:

```bash
/opt/snotes-gale-mcp-oidc-staging/.venv/bin/python \
  -m src.gale_remote_mcp serve
```

Uvicorn access logging remains disabled.

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
/revoke
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

Protected MCP calls use the OAuth Bearer challenge. In the accepted Mixed Auth
flow, unauthenticated `tools/call` returns an in-band MCP error result with the
challenge in `_meta`; a bare HTTP `401` is not the only client signal.

### Mixed Auth discovery boundary

Mixed Auth is limited to protocol discovery:

- anonymous `initialize`, `notifications/initialized`, `ping`, and
  `tools/list` are allowed;
- anonymous `tools/list` exposes exactly `search_notes` and `get_note`;
- both tools require only the runtime business scope `gale.read`;
- top-level `securitySchemes` and `_meta.securitySchemes` are identical OAuth2
  mirrors;
- no tool declares `noauth` or `offline_access`;
- `offline_access` is requested only in the upstream OIDC flow;
- unauthenticated `tools/call` fails closed before any mirror query and returns
  the MCP OAuth challenge in `_meta["mcp/www_authenticate"]`; and
- the server does not advertise the MCP logging capability or accept anonymous
  `logging/setLevel`.

### Secret and external acceptance boundary

The checked-in examples use placeholders only. Environment-specific Auth0
values, exact subjects, client credentials, callback IDs, storage keys, and
JWT keys belong only in the root-owned mode-0600 Oracle environment file.
Secrets must never be pasted into Codex, reports, Git, backup filenames, or
ordinary terminal history.

The ChatGPT Business target-client acceptance passed with fully fictional data,
but the Draft remains unpublished. Anonymous tool execution, URL tokens,
custom API keys, and token passthrough remain forbidden. Mixed Auth does not
make either business tool anonymous.

## Oracle dual-service layout

```text
shared service user: snotes-gale (system account, no login shell, no sudo)
shared mirror:       /var/lib/snotes-gale-mcp/mirror/gale-mirror.db

8767 rollback app:   /opt/snotes-gale-mcp
8767 configuration:  /etc/snotes-gale-mcp
8767 runtime:        /run/snotes-gale-mcp
8767 unit:           snotes-gale-mcp.service
8767 backend:        127.0.0.1:8767

8877 staging app:    /opt/snotes-gale-mcp-oidc-staging
8877 configuration:  /etc/snotes-gale-mcp-oidc-staging/service.env
8877 data parent:    /var/lib/snotes-gale-mcp-oidc-staging
8877 OAuth storage:  /var/lib/snotes-gale-mcp-oidc-staging/oauth
8877 runtime:        /run/snotes-gale-mcp-oidc-staging
8877 unit:           snotes-gale-mcp-oidc-staging.service
8877 backend:        127.0.0.1:8877

public MCP:          https://snotes.ke-den.de/mcp
shared Caddy source: /opt/gale-basic-memory-stage2/Caddyfile
shared Caddy unit:   gale-basic-memory-stage2-caddy container
```

The original `deploy/oracle/snotes-gale-mcp.*` files remain the 8767 rollback
materials. They must not be overwritten by the staging deployment.

The accepted staging materials are:

- `deploy/oracle/snotes-gale-mcp-oidc-staging.service`;
- `deploy/oracle/snotes-gale-mcp-oidc-staging.env.example`; and
- `deploy/oracle/snotes-gale-mcp-oidc-staging.Caddyfile`.

The live 8877 app and source directories are root-owned mode `0755`. Its
configuration directory is root-owned mode `0700`, and `service.env` is
root-owned mode `0600`. The staging data parent is root:`snotes-gale` mode
`0750`. The OAuth directory is owned by `snotes-gale`, mode `0700`; its files
are mode `0600`. The shared mirror directory is root:`snotes-gale` mode `0750`,
and the fictional mirror is `snotes-gale`-owned mode `0600`.

The current Oracle has one public Caddy already owning 80/443 for Basic Memory.
The 8877 recovery Caddy fragment routes protected-resource and authorization
metadata, DCR, authorize, consent, the fixed upstream callback, token,
revocation, and MCP paths to localhost port 8877. It must not change the
existing `memory.ke-den.de` route. Validate the complete candidate before
reload and keep a root-only pre-change copy plus SHA-256.

The recovery fragment explicitly includes `/revoke`. Round 2 does not modify or
reload the running Caddy configuration; the route must therefore be verified
again during the next separately approved Caddy deployment or disaster
recovery.

### Redirect roles

The upstream Auth0 Regular Web Application has one fixed callback:

```text
https://snotes.ke-den.de/auth/callback
```

Downstream ChatGPT and Inspector redirects are different values stored in
`SNOTES_GALE_ALLOWED_CLIENT_REDIRECT_URIS`. A ChatGPT redirect must use one
exact Draft identifier, for example:

```text
https://chatgpt.com/connector/oauth/<exact-draft-id>
```

Never configure `https://chatgpt.com/*`. Loopback Inspector redirects are
downstream test callbacks and are not the Auth0 callback.

## 8877 disaster recovery

This procedure rebuilds the accepted staging structure. It is not permission
to deploy, transfer files, use real data, edit Auth0, or change Caddy without a
separate G-approved work order.

1. Confirm that the existing `snotes-gale` system account has no login shell or
   sudo access. On a replacement host, create an equivalent system account
   before creating service paths. Do not recreate or renumber an existing
   identity:

   ```bash
   getent passwd snotes-gale
   getent group snotes-gale
   # Only when both checks prove that the identity is absent:
   sudo useradd --system --user-group --home-dir /nonexistent \
     --shell /usr/sbin/nologin snotes-gale
   ```

2. Create the staging paths with the observed ownership and modes:

   ```bash
   sudo install -d -o root -g root -m 0755 \
     /opt/snotes-gale-mcp-oidc-staging
   sudo install -d -o root -g root -m 0700 \
     /etc/snotes-gale-mcp-oidc-staging
   sudo install -d -o root -g snotes-gale -m 0750 \
     /var/lib/snotes-gale-mcp-oidc-staging
   sudo install -d -o snotes-gale -g snotes-gale -m 0700 \
     /var/lib/snotes-gale-mcp-oidc-staging/oauth
   sudo install -d -o root -g snotes-gale -m 0750 \
     /var/lib/snotes-gale-mcp/mirror
   ```

   A separately approved fictional `gale-mirror.db` must be owned by
   `snotes-gale:snotes-gale` and mode `0600`. Do not create, copy, or upload a
   real mirror as part of recovery preparation.

3. Place an exact G-approved repository revision in
   `/opt/snotes-gale-mcp-oidc-staging`. File transfer, SSH/SCP synchronization,
   and release publication remain separate approvals.
4. Create a dedicated virtual environment under the staging app and install
   the exact direct pins in `requirements-mcp.txt`, including
   `fastmcp==3.4.4`. Do not use `pip install -U`. A complete transitive lock
   with hashes remains a Function Package C gate, so this rebuild reproduces
   the accepted direct dependency contract but is not a bit-for-bit package
   graph guarantee.
5. Use `snotes-gale-mcp-oidc-staging.env.example` to create
   `/etc/snotes-gale-mcp-oidc-staging/service.env` with `sudoedit`. Set it to
   root:root mode `0600`. Do not copy a completed env file into Git, reports,
   shell history, or ordinary backups.
6. Create the empty OAuth storage as `snotes-gale` mode `0700`. Preserve an
   existing encrypted OAuth directory during recovery unless an explicitly
   approved restore supplies the matching storage-encryption and JWT-signing
   keys. Never inspect or selectively edit stored token/client records.
7. Install `snotes-gale-mcp-oidc-staging.service` under systemd, verify its
   content against the repository template, then enable and start only 8877.
   Do not stop, disable, overwrite, or delete the 8767 rollback service.

   ```bash
   sudo install -o root -g root -m 0644 \
     deploy/oracle/snotes-gale-mcp-oidc-staging.service \
     /etc/systemd/system/snotes-gale-mcp-oidc-staging.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now snotes-gale-mcp-oidc-staging.service
   ```
8. Before changing Caddy, validate the backend locally:
   - service is active/enabled and listens only on `127.0.0.1:8877`;
   - OAuth metadata is internally consistent;
   - anonymous initialize/notifications/ping/`tools/list` succeeds;
   - `tools/list` has exactly `search_notes` and `get_note`, with identical
     top-level and `_meta` OAuth2 `gale.read` schemes;
   - no `noauth`, `offline_access`, or logging capability is advertised; and
   - one fictional unauthenticated `tools/call` fails before mirror access and
     returns the complete MCP OAuth challenge.
9. Back up the shared Caddy configuration to a root-only directory with a
   SHA-256, merge the 8877 Caddy fragment without changing Basic Memory, and
   validate the complete configuration.
10. Only after steps 1–9 pass may a separately approved change point Caddy at
    8877. Recheck public metadata, anonymous discovery, challenge, service
    errors, and sensitive-log marker counts. A real OAuth login, DCR, or
    ChatGPT Business rerun requires its own acceptance approval.

The repository can reproduce the accepted directory, permission, service,
environment-variable, route, and validation structure. Environment-specific
secrets and the complete transitive Python package graph intentionally remain
outside that claim.

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
as proof of the complete real Auth0 refresh-token lifecycle. Real issuance,
rotation, restart renewal, revocation, and invalidation remain Function Package
C gates.

## Logging and retention

Application events contain only timestamp, request ID, tool name, status,
latency, result count, and a fixed error code. They never contain a query,
title, content, result, database path, Authorization header, token, client
secret, authorization code, refresh token, or environment dump. Uvicorn access
logs and the S-notes Caddy site log are disabled. systemd uses journald's host
rotation policy; do not add a second plaintext log.

The optional metadata-only OAuth timeline is deferred to Function Package C.
If approved later, it may contain only timestamp, method, path, status, and
client category. It must be switchable and must never record query strings,
bodies, Authorization headers, tokens, authorization codes, subjects, search
terms, or note text. No such timeline is claimed for the current Caddy or 8877
process.

## Rollback

Rollback from 8877 must preserve the accepted staging state and leave Basic
Memory untouched:

1. Verify `snotes-gale-mcp.service` is active/enabled and listens only on
   `127.0.0.1:8767`.
2. Back up the complete shared Caddy configuration to a root-only timestamped
   directory and record its SHA-256.
3. Change only the `snotes.ke-den.de` upstream from
   `127.0.0.1:8877` to `127.0.0.1:8767`. Do not alter
   `memory.ke-den.de`.
4. Validate the complete Caddy configuration, reload only after validation,
   and verify that the public endpoint reaches the old fail-closed service.
5. Preserve `/opt/snotes-gale-mcp-oidc-staging`,
   `/etc/snotes-gale-mcp-oidc-staging`, and
   `/var/lib/snotes-gale-mcp-oidc-staging/oauth`. Do not inspect, selectively
   edit, or delete OAuth state.
6. Do not delete, overwrite, stop, or disable the 8767 rollback materials.
   Stopping 8877 after a route rollback requires a separate decision.
7. Do not change Auth0, the unpublished ChatGPT Draft, Basic Memory, Cloudflare,
   OCI, or the Mac S-notes database as part of this rollback.
8. Preserve the audit package and the root-only pre-change backups.

Known deferred Function Package A LOW findings: cleanup after a hypothetical
`chmod` failure (L02) and concurrent export mutual exclusion (L04). Neither is
expanded in Package B. L01 exact FTS rowid mapping and L03 mode `0600` are
enforced here.

Known Package B pre-production item: the encrypted filesystem store is for one
Oracle process. Horizontal/multi-instance deployment requires a shared
transactional store and a separate design review.

## Function Package C gates

The following remain explicit gates and are not completed by Function Package
B:

1. a complete transitive dependency lock with hashes and a clean rebuild proof;
2. the real Auth0 refresh-token lifecycle: issuance, encrypted persistence,
   rotation, restart renewal, revocation, invalidation, fail-closed behavior,
   and sensitive-log review;
3. a switchable metadata-only OAuth request timeline with the privacy boundary
   described above;
4. replacement of temporary host/OCI network rules with formally reviewed
   least-privilege rules, without deleting the current HTTPS endpoint by
   accident; and
5. an audited rollout using a small set of safe formal test notes before any
   real-data publication decision.

Until those gates receive separate approval, do not use real S-notes data,
automatically publish or synchronize data, perform SSH/SCP application
synchronization, begin Function Package C, publish the ChatGPT Draft, delete
8767, delete Basic Memory, delete OAuth state, or treat 8877 as production.

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
