# GreenCloud Gale Remote MCP

The remote layer runs `compose_greencloud_internal_runtime()` under the existing
`snotes-gale` OS account. It serves the same low-level MCP Server as
`/usr/local/bin/snotes-gale-internal-mcp`. No business tools, schemas, actor
registries, proposal storage, Reminder API, mirror or publisher are duplicated.

- Listener: `127.0.0.1:8768` (check it is free before installation).
- Public resource: `https://snotes-gale.ke-den.de/mcp`.
- Unit: `snotes-gale-remote-mcp.service`.
- Source: three new Python modules in `/srv/snotes/runtime`.
- Dependencies: `/srv/snotes/gale-remote/vendor`, using the existing runtime
  Python; installation never changes the existing virtual environment.
- Non-secret configuration: `/srv/snotes/gale-remote/config.json`.
- OAuth state: `/srv/snotes/gale-remote/oauth`, Gale-owned mode `0700`, encrypted.
- Secret source: `/root/greencloud-secrets/gale-remote-oauth.json`, root `0600`.
  systemd exposes a private read-only copy through `LoadCredential`.

## Authentication

Reuse Oracle's accepted Auth0 OIDC proxy, Authorization Code + PKCE, exact
subject restriction, encrypted token state, consent and CSRF checks. All tools
require `gale.read`; this is the existing OAuth business scope, while the
internal actor permission rules still control every operation. Tokens never
become Notes or Reminder credentials. GreenCloud has its own Auth0 application,
audience, client secret, encryption key, signing key and OAuth state. It never
calls Oracle or loads Oracle credentials.

Mixed authentication allows only `initialize`, `notifications/initialized`,
`ping` and `tools/list` anonymously. Every business tool call is protected.
Anonymous `tools/call` receives an MCP error with `mcp/www_authenticate`; wrong
Bearer credentials fail at the protected HTTP route. Tool discovery dynamically
adds matching top-level and `_meta.securitySchemes`, leaving input/output schemas
unchanged.

The exact stable ChatGPT callback is
`https://chatgpt.com/connector_platform_oauth_redirect`. RFC 9207 compatibility
advertises issuer identification and adds the same exact issuer to downstream
success/error redirects. No ChatGPT wildcard is allowed. A loopback callback is
allowed for isolated OAuth verification. No ChatGPT connector is created here.

## Install

Keep Oracle unchanged. In the existing Auth0 tenant create a separate Regular
Web Application named `S-notes GreenCloud Gale Remote MCP`, with only
`https://snotes-gale.ke-den.de/auth/callback` as its upstream callback. Enable
Authorization Code and Refresh Token grants. Create a separate RS256 API named
`S-notes GreenCloud Gale Remote MCP` with identifier
`https://snotes-gale.ke-den.de/mcp`, permission `gale.read`, and offline access.
Use per-app user delegation: authorize only the new GreenCloud application for
`gale.read`, with no machine client permissions. Auth0 may create a default test
application alongside a new API; leave it unauthorized and do not use its secret.
Allow only S's explicitly selected existing Auth0 subject in the GreenCloud
credential file. Do not broaden or edit the old Oracle application/API.

Transfer only the new source modules, this deployment directory, and
`requirements-mcp.txt` to a temporary root-owned directory on GreenCloud. Run:

```sh
sh deploy/greencloud/gale-remote/install.sh /path/to/unpacked/source
```

Provision the new client credential interactively on GreenCloud (secret input
is hidden; new encryption/signing keys are generated on that host):

```sh
/srv/snotes/runtime/.venv/bin/python \
  /srv/snotes/gale-remote/deploy/provision_credentials.py
```

Never paste the credential into Git, reports, chat, logs or shell command text.
The optional `--stdin` mode accepts a bounded JSON object with `client_id`,
`client_secret`, and `subject` through a private pipe directly into SSH. Do not
save that input on the Mac. The credential parent is a root-only directory.
The root secret remains `0600`; only a root:root `0440` systemd credential copy
is additionally accepted inside the unit's private credential mount.
Create exactly one Cloudflare DNS-only A record, `snotes-gale.ke-den.de` pointing
to `109.94.170.215`, TTL Auto. Activate after the credential and upstream Auth0
application/API are ready:

```sh
sh deploy/greencloud/gale-remote/activate.sh
```

Activation checks localhost discovery before appending only the marked Gale
Caddy block. It retains one root-only pre-change Caddy file, verifies existing
bytes are an unchanged prefix, validates the candidate, and reloads Caddy.
Existing ntfy and Reminder site blocks are preserved. No firewall opening or
direct public listener is added for `8766`, `8767` or `8768`.

## Verification

Run the focused fictional transport/auth tests with the repository `.venv` and
temporary HOME. Transport checks use fictional publisher mirrors and a temporary
Reminder DB. They compare the complete dynamically discovered contract with the
internal registry and exercise auth/identity/visibility/owner-only negatives.
Do not rerun the first-ticket filesystem matrix or touch the real user database.

Live gates must check localhost and public TLS with hostname verification,
OAuth metadata and registration, authenticated discovery (25 tools), anonymous
call challenge, wrong credential rejection, service restart/recovery, unchanged
internal entry discovery and nearby service health. Do not log response bodies
containing notes, reminders or credentials. Authentication is not accepted merely
because anonymous `initialize` succeeds.

`verify_oauth.py` is a root-only temporary live gate, not a production auth
endpoint. It uses a loopback callback through an SSH tunnel (`8878` on the Mac
to GreenCloud `127.0.0.1:8769`) and a private authorization URL under
`/root/greencloud-secrets/gale-remote-verification`. Tokens never appear in its
output. `--restart-only` checks authenticated discovery before/after restarting
only the new unit, without repeating data tests. Run that gate once when needed.
`--cleanup-only` removes only registrations matching the verification name and
exact callback, plus their MCP refresh metadata; it preserves the production
application, API, credential and every other client.

The pinned SDK retains access-token references until normal expiry. This gate
records that expiry and removes the temporary client/refresh objects. It does
not modify SDK code, delete access mappings or introduce a token blacklist.
The stable ChatGPT callback and exact issuer behavior follow the
[official OAuth documentation](https://developers.openai.com/plugins/build/auth).

## Recovery

Stop/disable only `snotes-gale-remote-mcp.service`. Remove only the marked
`BEGIN/END S-NOTES GALE REMOTE MCP` block from the *current* Caddyfile, validate,
then reload. Do not restore the whole old Caddyfile over subsequent changes.
The saved `Caddyfile.before` is a comparison/recovery reference. Remove only the
new DNS record if reverting public routing. Keep the independent credential and
encrypted OAuth directory protected until S explicitly chooses to remove them.
Internal MCP, Notes/Reminder services, publisher and Oracle require no rollback.
