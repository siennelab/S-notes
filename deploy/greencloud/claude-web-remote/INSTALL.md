# Claude Web → GreenCloud Notes MCP

This is an independent OAuth connection with the existing `claude-code` data
identity. It creates no actor, metadata option, mirror, database or Notes authority.
Mac S-notes remains the authority. The production service runs as the existing
`claude` account (UID/GID 2001), reads `/srv/snotes/mirror/claude-code/current.db`,
and submits through the existing actor-fixed Notes proposal client and outbox.
It imports no Reminder server and exposes no Reminder tools.

Production targets (activation must be verified separately):

- Unit: `snotes-claude-web-remote-mcp.service`.
- Internal HTTP: `127.0.0.1:8770/mcp` (Streamable HTTP).
- Public URL: `https://snotes-claude.ke-den.de/mcp`.
- Sources: existing `/srv/snotes/runtime` and its existing Python environment.
- Public Python dependencies: existing `/srv/snotes/gale-remote/vendor`; no Gale
  configuration, OAuth state, token, secret or OS identity is used by this service.
- Config: `/srv/snotes/claude-web-remote/config.json`.
- Independent encrypted OAuth state: `/srv/snotes/claude-web-remote/oauth`.
- Root-only credential: `/root/greencloud-secrets/claude-web-remote-oauth.json`.
- Private systemd credential name: `claude-web-oauth.json`.

## Independent Auth0 setup

Use the existing tenant, with a **new** Regular Web Application named
`S-notes GreenCloud Claude Web Notes MCP`. Do not use the Gale application.

- Allowed callback: `https://snotes-claude.ke-den.de/auth/callback`.
- Grant types: authorization code and refresh token. Client secret: post.
- New RS256 API identifier: `https://snotes-claude.ke-den.de/mcp`.
- API permission: `notes`; allow offline access for token refresh.
- Authorize only the new application for this API's user delegation and `notes`
  permission. Do not grant machine-to-machine access.
- Allow only S's selected Auth0 user subject in the server credential.

In the API's Settings → Application Access Policy, use Per-app authorization
for User-Delegated Access and Deny for Client Access. In Application Access,
authorize only the newly created app for user access with `notes`. In the app's
Advanced Settings → Grant Types, enable Authorization Code and Refresh Token;
use Post for Token Endpoint Authentication Method. Find the allowed account's
User ID under User Management → Users → the selected S account. This is an Auth0
user ID, not an email address and not the S-notes `claude-code` actor name.

Create DNS `snotes-claude.ke-den.de` A → `109.94.170.215` (unproxied), then inject
the new app credential on GreenCloud via the helper's hidden interactive input
or a private stdin pipe. Never paste secrets into chat or Git. The helper rejects
the existing Gale client ID/secret and generates independent encryption/signing keys.

## Prepare and activate

After local targeted tests pass, copy only the three runtime source files and
this directory into a temporary source staging directory on GreenCloud. Run:

```sh
sh deploy/greencloud/claude-web-remote/install.sh /path/to/source
PYTHONPATH=/srv/snotes/gale-remote/vendor /srv/snotes/runtime/.venv/bin/python \
  /srv/snotes/claude-web-remote/deploy/provision_credentials.py
sh /srv/snotes/claude-web-remote/deploy/activate.sh
```

Preparation backs up only the shared auth module that it replaces; the other
runtime files and unit are additions. It does not start a service or edit Caddy.
Activation checks the independent credential, DNS and local OAuth metadata
before adding its Caddy host. It starts only the new service and reloads Caddy
to add the host. Gale, Notes outbox, publisher and Reminder are not restarted.

## Independent OAuth acceptance

The supplied `verify_oauth.py` uses a temporary registered MCP client and the
normal authorization-code/PKCE flow. Keep local port 8878 forwarded to GreenCloud
127.0.0.1:8769 while running it as root with the existing vendor/runtime Python.
Supply one currently authorized mirror Note ID with `--allowed-note` and one
existing unauthorized Note ID with `--denied-note`. `--submit-proposal` adds one
idempotent test proposal; it never accepts or creates a formal Mac note.

When the script prints `CLAUDE_WEB_OAUTH_BROWSER_STEP=WAITING`, S opens the
root-only `claude-web-remote-verification/authorize-url` under
`/root/greencloud-secrets` and completes S-notes/Auth0 consent. This requires no
Claude.ai login. The script verifies localhost/public HTTPS MCP, Notes access,
actor injection, no Reminder, proposal status and OAuth refresh. It removes only
its temporary OAuth client and refresh metadata, leaving safe evidence in the
same root-only verification directory. The access tokens expire normally.

## Claude.ai manual connection

No Claude login is needed for development, deployment or independent MCP tests.
S performs the final Claude account connection:

1. Open Customize / Settings → Connectors → Add custom connector.
2. Name: `S-notes Notes`; URL: `https://snotes-claude.ke-den.de/mcp`.
3. Use OAuth sign-in and dynamic/automatic client registration. Leave optional
   manually supplied client ID/secret empty. The upstream Auth0 secret stays
   exclusively on GreenCloud.
4. Sign in with the allowed S account in Auth0, approve the independent Notes
   connection and enable the connector in the desired Claude conversation.

Claude's hosted OAuth callback is fixed to
`https://claude.ai/api/mcp/auth_callback`. A loopback callback is also allowed for
independent protocol acceptance. Fixed request-header authentication is not used
because Claude currently limits it to selected organizations.

Official references:
[MCP server support](https://claude.com/docs/connectors/building/index),
[custom connector settings](https://claude.com/docs/connectors/custom/add-unlisted),
[Auth0 API access policies](https://auth0.com/blog/developers-guide-api-access-policies-auth0/).

## Acceptance boundary

Use fictional mirrors and temporary HOME for automated checks. Check Notes
read/denial, actor injection, no Reminder discovery/calls, authenticated handshake,
proposal idempotency/status and the existing Mac inbox/AI Center/receipt path.
Once independent credentials and DNS exist, separately verify live public OAuth,
one authorized mirror note, one denied note, one proposal and Gale's minimal smoke.
Do not label a prepared, inactive service as a working production connection.
