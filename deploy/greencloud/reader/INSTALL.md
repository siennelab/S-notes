# S-notes Remote Reader

Single process, server-rendered human Reader at `https://notes.ke-den.de`.
The Mac remains Notes authority. S is a mirror actor only; no S MCP or writes.

## Order and permissions

1. Pass actor/publisher/receiver/Reader fixture tests, then candidate App build
   and isolated UI check. Never run automated checks on the real Notes database.
2. On GreenCloud create a dedicated system user/group `snotes-reader` (nologin,
   no supplementary groups, no home). Make `/srv/snotes/mirror/s` owned by
   `snotes:snotes-reader`, mode `2750`. The existing receiver creates its candidate
   in that directory, inheriting the group, validates at `0600`, then replaces
   `current.db` at `0640`. Reader has read/traverse only.
3. Before starting a publisher that includes S, back up only the receiver runtime
   files being replaced. Install changed `ai_actor_registry.py`, `greencloud.py`,
   and `cloud_mirror_receiver.py` into the existing root-owned runtime. No receiver
   daemon restart or transport/key/sudoers change is needed.
4. Commit feature, ff-only merge main, push, verify clean and equal origin/main.
   Build/sign the formal App from that revision. Upgrade the existing pinned Mac
   publisher following `deploy/greencloud/INSTALL.md`; retain old export and plist.
5. Confirm first S mirror receipt and filesystem permissions. An empty authorized
   mirror is valid. Do not create grants or upload extra private notes for testing.
6. Package via `.venv/bin/python scripts/package_remote_reader.py <private-tar>`.
   Verify archive manifest hashes on the server. Install only its source closure
   into root-owned `/srv/snotes/reader/runtime` (directories `0755`, source `0644`).
   Reuse the installed public dependencies in `/srv/snotes/gale-remote/vendor`
   and `/srv/snotes/runtime/.venv`; no dependency upgrade is needed. Install the
   supplied systemd unit, daemon-reload, but do not start before credentials exist.

## Auth0 and activation

Create an independent **Regular Web Application**, `S-notes Remote Reader`, in
the existing Auth0 tenant. Only Authorization Code is needed, RS256 ID tokens,
client-secret-post; request `openid` with PKCE S256. Set the exact callback to
`https://notes.ke-den.de/auth/callback` and application login URL to the origin.
No API, audience, Notes scopes, refresh grant, DCR or MCP state is used.
Preserve other apps, shared connection signup and tenant-wide MFA settings.
Inspect S's current protection; any additional requirement must be app-scoped.

Run `provision_credentials.py` interactively as root using the existing remote
Python. The secret input is hidden. It fixes S to the already-approved single
stable subject in the existing Claude Web allowlist, copies no other credential,
and rejects sharing that application's client ID/secret. The result is root-only
`/root/greencloud-secrets/reader-oidc.json`; systemd passes it via `LoadCredential`.
Public issuer/client ID/observed port go in root-owned `config.json` at `0644`.
Do not put private values in Git, shell arguments, chat or logs.

Choose a free port by actual socket bind, record it, and use that same port in
config and the supplied Caddy host template. Start only `snotes-reader.service`.
Check loopback with `Host: notes.ke-den.de`: `/healthz` is `ok`, anonymous note
requests reveal no contents, and S mirror is readable but not writable as Reader.
Create the DNS record using the existing unproxied GreenCloud DNS pattern.
Save only the old Caddyfile, append the Reader host, validate the candidate,
install it, and reload Caddy. Do not replace Caddy or restart other services.

Security headers have one owner: the application. Caddy and uvicorn discard
access logs. Search is POST with a bounded form and session-bound CSRF token.
Browser-bound one-use state/nonce/PKCE transactions expire after 10 minutes.
RS256 signature, exact issuer/client audience, expiry, nonce and fixed S subject
must pass before an opaque session is issued. Sessions expire absolutely in
seven days, use Secure/HttpOnly/SameSite=Lax `__Host-` cookies, and all disappear
on service restart. POST logout deletes the local Reader session only.

The fresh SQLite reader uses ro/immutable/query_only/trusted_schema=OFF. Chinese
search retains `match_search_fields` substring semantics. One note's metadata
and Markdown are fetched together. Redacted is escaped safe text, not Markdown.
External images become explicit links; CSS/fonts/assets are local; no JS.
`generated_at` is a content snapshot timestamp, never evidence the Mac is online.

## Acceptance and rollback

Verify true S login, full/rendered and redacted/paragraph reading, Chinese
search, revoke-after-publish and last-snapshot reading without the Mac publisher.
Only S chooses real non-sensitive test notes and changes their grants in the App.
Use fixture coverage for invalid subjects/signatures; no second human account.
Confirm the existing Claude public OAuth metadata/unauthenticated MCP response
and original mirror publish statuses after Caddy reload. Do not test Reminder.

Rollback: stop/disable Reader, restore only the previous Caddyfile and reload;
leave the S mirror in place. For publisher restore saved LaunchAgent/export.
For receiver restore only backed-up source files. Keep previous main revision.

Protocol references: [Auth0 PKCE](https://auth0.com/docs/get-started/authentication-and-authorization-flow/authorization-code-flow-with-pkce),
[PyJWT verification](https://pyjwt.readthedocs.io/en/stable/api.html).
