# S-notes Remote Reader — active handoff

Updated: 2026-11-02 (America/Los_Angeles). Task is **not CLOSED**. Work stopped at the user's quota handoff threshold; services are stable.

## Workspace / authorization

- `/Users/s/Documents/项目/S-notes`, current branch `main`; implementation branch `codex/s-remote-reader`.
- Clean baseline was `facc1e2398b24a639f3d8979a83cc1e7f67f450d` = main = origin/main.
- Current deployed Reader code is `17a5e0d682ead286f4d426818bc09e9f6da04d5a`. Main/origin matched it before this handoff-only documentation commit; inspect current Git revision when resuming.
- Full pasted work order permits actual implementation, deployment, ff-only merge and push; no extra publishing approval needed within this unchanged scope.
- Original request attachment: `/Users/s/.codex/attachments/e783ea0f-c87e-4911-9f5e-478993a82c39/Pasted text.txt`.
- Check account quota before resuming; last read: **11% remaining in 5h window, 72% weekly**. No reset credits used. Stopped at the ~10% threshold with no irreversible/in-progress deployment remaining.
- This handoff file is task-generated related work. Preserve unrelated user changes if any appear.

## Implemented / deployed

- S registered after common, before Gale. Mirror-only actor sequence includes S; proposal/outbox actor set unchanged. No schema, authority, new permission field, S MCP, or JS/framework.
- Reader over fresh ro/immutable SQLite connections. POST Chinese substring search reuses existing search semantics. Safe full Markdown, fixed size classes, images as links, redacted escaped plain text, local CSS/security headers.
- Separate human Auth0 Regular Web Application `S-notes Remote Reader`, Authorization Code only, PKCE, openid, RS256/OIDC. Fixed existing S subject allowlist. No other app or tenant-wide policy changed. Tenant MFA inspected: Never / factors disabled, left unchanged.
- Secrets already securely provisioned root-only and injected with LoadCredential. Never print/read secret or subject. Temporary entry key was removed; browser secret clipboard/memory cleared.
- GreenCloud SSH alias `optimal`, root administrative access via existing SSH; Reader Linux UID/GID 986, group `snotes-reader`, no supplementary groups.
- S mirror: `/srv/snotes/mirror/s/current.db`; parent `snotes:snotes-reader 2750`, DB `0640`. Reader read-only and cannot read other actor mirrors.
- Reader independent source closure: `/srv/snotes/reader/runtime`; manifest `/srv/snotes/reader/source-manifest.json` now records code `17a5e0d`.
- Service `snotes-reader.service`, only `127.0.0.1:8771`; `/srv/snotes/reader/config.json` public config, root-only credential `/root/greencloud-secrets/reader-oidc.json`. Existing installed runtime/vendor reused, no dependency upgrades.
- DNS `notes.ke-den.de` A → `109.94.170.215`, DNS only, Auto TTL. Caddy validate/reload passed, real HTTPS certificate obtained. Reader and original Claude/Gale public endpoint smokes pass. No unrelated service restarted.
- Formal App `/Users/s/Documents/项目/S-notes/dist/S-notes.app` built from clean approved main `51150fcf3bac316e6c9f86fd73464fe4dba5e3ae`, codesign and actual bundle isolated smoke passed.
- Publisher pinned export/venv also `51150fc...` under `~/Library/Application Support/S-notes/runtime/greencloud-publisher/`. Five-minute LaunchAgent active; not developer checkout. Later `d0768aa` and `17a5e0d` change only Reader behavior/tests, so App and publisher were not rebuilt/restarted for those server-only fixes.
- Four mirror actors have successful publish and needs_retry=NO. Initial transient SSH timeouts recovered through unchanged pipeline. No network configuration changed.

## Evidence and real acceptance

- Initial 118 targeted tests passed (actors, mirrors, publisher/receiver, outbox, read, auth, renderer, existing Claude/Gale auth). Candidate build/signature passed. Qt checks used temporary HOME/DB and normal exit. Formal actual bundle `--step11-smoke` passed with temp HOME/DB and QTimer exit.
- Real user confirms formal UI order `通用 → S → Gale → …` and selected a non-sensitive full+S note.
- Real user logged into Reader on phone and saw homepage: AUTH0_LOGIN=PASS.
- User reported main category directories empty. Root cause: formal root note subcategory is `无`, catalog root subcategory is empty. Fixed parent browsing to include its children/default notes in `d0768aa`; exact child remains exact. User confirms directories now normal.
- User confirms full rendering normal.
- User reported POST search rejected. Root cause confirmed in Fetch spec and actual browser fixture: no-referrer makes ordinary forms send Origin:null. `17a5e0d` accepts opaque/missing Origin only with mandatory session CSRF; rejects explicit foreign origins and cross-/same-site Fetch Metadata. Tests cover positive/null, invalid CSRF, foreign origin, logout. Real Chrome form logged `REAL_BROWSER_ORIGIN=null; RESPONSE=200` using fictional data.
- 28 directly related tests pass after latest fix. User **confirmed phone search now works** after deployment/relogin: CHINESE_SEARCH=PASS.
- Only real note explicitly identified for backend checking is ID **272**. Exact-row checks showed it exists/full/Markdown non-null, matches parent catalog, default subcategory. No title/body printed.
- Automatic approval review rejected a proposed probe scanning up to 200 real notes and persisting IDs. It **did not execute**. Do not retry/circumvent. Ask S for exact selected note ID/URL and inspect only that row if needed; no broad content scans.
- Local Mac DNS had stale NXDOMAIN caching. Public HTTPS/phone/server work, authoritative/HTTPS DNS confirms correct A. Do not change network infrastructure for this. Check browser availability naturally later if useful.
- MAC_OFFLINE_READ=PASS: temporarily booted out only Mac publisher, then as GreenCloud `snotes-reader` used installed SQLiteCloudMirrorReader.get(272, include_markdown=True) and installed renderer on that exact selected note. Read/render succeeded without Mac publisher; no content/title output. Same pinned LaunchAgent immediately restored via trap, last exit=0 and five-minute schedule active.

## Remaining — do not claim PASS yet

1. **Pending user question:** S was asked to choose an existing suitable redacted multi-paragraph note with actual redaction, grant S, and give only numeric ID/Reader URL. No answer/ID received yet. Trigger normal publisher after selection if needed, then user checks masking/paragraphs/no Markdown-source rendering. Backend checks limited to this exact ID (visibility redacted, safe_markdown IS NULL, redaction_count > 0 and newline bool), no body/title output. Do not rerun completed tests/builds without a new reason.
2. Revoke: S removes S (and common if present) or uses hidden on selected test note (272 if that is the intended chosen note). Publish through existing pipeline. Verify exact selected ID absent in S mirror and user old URL returns 404/search no longer finds it. Preserve other grants; user can restore preferred final permissions afterward.
3. Recheck main=origin/main/worktree clean; update this handoff to actual final state and commit/push related documentation if retaining it, then concise final ≤20 lines. Do not label CLOSED until these real cases pass.

## Rollback points

- Prior main `facc1e2...`; core implementation `51150fc...`, directory fix `d0768aa...`, form fix `17a5e0d...`.
- Old publisher pin `fdbb6eb97f90b6cbf50b62df196582f9f7ad51b2`; old plist backup `~/Library/Application Support/S-notes/backups/reader-publisher.RBqRyrS9/publisher.before.plist`.
- Receiver replaced three files backed up `/root/greencloud-backups/reader-receiver.UgmYiIIC`.
- Old Caddyfile `/root/greencloud-backups/reader-caddy.yK30Qcwu/Caddyfile.before`.
- Reader directory fix backup `/root/greencloud-backups/reader-directory.igUUHK2x`; form fix backup `/root/greencloud-backups/reader-form.o9hdbbir`.
- Reader rollback: stop/disable only Reader, restore previous Caddyfile/reload; leave mirror intact.

## Active tools / temporary artifacts

- Browser Chrome binding ID 3. Auth0 user tab 678824290 now Reader app Credentials (keep secret masked); Cloudflare user tab 678824245 now DNS records. Claimed user tabs were left open. Agent-created old preview tab 678824292 is closed. Failed Reader tab 678824302 may exist.
- Local fictional form browser smoke server exec session **62824** on `127.0.0.1:50495` is stopped. Earlier static preview session 41242 is also stopped.
- Actual service units and publisher are active. No credential entry pending, no publisher intentionally left stopped.
- `/tmp/snotes-reader-*` holds task scripts/logs/archives/screenshots, no plaintext credentials. Clean only known task materials after final closure, preserve useful failure evidence until then.
