# S-notes Remote Reader — CLOSED

Closed: 2026-11-02 (America/Los_Angeles). All requested implementation,
deployment and live acceptance are complete. No remaining construction task.

## Revisions and workspace

- Workspace: `/Users/s/Documents/项目/S-notes`, branch `main`.
- Clean starting main/origin: `facc1e2398b24a639f3d8979a83cc1e7f67f450d`.
- Feature branch: `codex/s-remote-reader`; ff-only merges, no force push.
- Core S actor/Reader implementation: `51150fcf3bac316e6c9f86fd73464fe4dba5e3ae`.
- Reader directory fix: `d0768aa32f569b518c6cbfdf025b573ce343639c`.
- Reader no-referrer form fix / deployed Reader code: `17a5e0d682ead286f4d426818bc09e9f6da04d5a`.
- Subsequent commits contain status documentation only. Inspect Git for final
  documentation revision; final main/origin equality and clean worktree are
  checked after the closing commit/push.
- Formal App: `dist/S-notes.app`, clean source `51150fc...`, arm64, codesign and
  actual-bundle isolated smoke passed. Later code fixes are Reader-only.
- Publisher pinned export/venv: `51150fc...` under
  `~/Library/Application Support/S-notes/runtime/greencloud-publisher/`.
  It never runs from the developer checkout. Five-minute LaunchAgent remains
  loaded, last exit 0; all four actors have needs_retry=NO.
- S manually reset the account quota once to resume final acceptance. The agent
  did not invoke any credit reset.

## Production

- Reader: `https://notes.ke-den.de`, `snotes-reader.service`, one worker,
  `127.0.0.1:8771`; Caddy provides HTTPS reverse proxy only.
- DNS A points to existing GreenCloud, DNS-only/Auto TTL. Caddy candidate
  validation, reload and real certificate issuance passed; no Caddy replacement.
- Root-owned source closure: `/srv/snotes/reader/runtime`; source manifest:
  `/srv/snotes/reader/source-manifest.json`.
- Independent human Auth0 Regular Web Application `S-notes Remote Reader`.
  Authorization Code + PKCE, openid only, RS256/OIDC validation and fixed S sub.
  No other app, shared signup policy or tenant-wide MFA policy changed.
- Public app config: `/srv/snotes/reader/config.json`; root-only credential:
  `/root/greencloud-secrets/reader-oidc.json`, delivered by LoadCredential.
  No secret, subject, rule value or note content is recorded here or in Git.
- Linux Reader UID/GID 986, no supplementary groups. S mirror parent:
  `/srv/snotes/mirror/s`, `snotes:snotes-reader 2750`; `current.db` is `0640`.
  Reader can read S only, cannot write it or read other actor mirrors.
- Mac remains Notes authority. No schema migration, new permission field, S MCP,
  session database, heartbeat, JS/framework, remote writes or new transport key.
- Mirror-only actor sequence includes S. Proposal/outbox/receipt actors remain
  exactly claude-code, clawbot and gale; S is excluded.
- Existing App “立即更新镜像” remains Gale-only. S uses the existing five-minute
  publisher; this explains the observed delay after pressing that button.

## Verified outcomes

- S UI order: common → S → Gale → remaining registry identities; S confirmed
  this in the formal App.
- Full/common/S/redacted/hidden permissions, mirror validation/receiver/state,
  proposal rejection and existing actor paths covered by initial 118 targeted
  tests. Candidate and formal App builds/signatures passed; Qt checks used
  temporary HOME/DB and normal/QTimer exits, never real Notes DB test fixtures.
- Full rendering, real Auth0 login and Chinese search confirmed by S on phone.
- Parent-directory bug fixed: parent includes default and child locations;
  exact child stays exact. S confirmed directories and rendering normal.
- Referrer-Policy:no-referrer causes ordinary form Origin:null. Fixed without
  removing mandatory session CSRF; explicit foreign origins and cross-/same-site
  Fetch Metadata still rejected. Real browser form observed Origin:null and 200.
  Latest 28 directly related tests passed; S confirmed actual phone search.
- Safe renderer fixtures cover headings, paragraphs, bold/lists/quote/code/table,
  fixed size classes, escaped HTML, unsafe schemes, image links and redacted text.
  Mobile 390px render has 17px text, no page overflow, zero img/script elements,
  horizontal code scrolling and redacted pre-wrap paragraph preservation.
- MAC_OFFLINE_READ=PASS: temporarily unloaded only Mac publisher; GreenCloud
  Reader identity read/rendered the selected Note 272 from its existing snapshot.
  Same pinned LaunchAgent immediately restored by trap, last exit 0.
- Note 462 was S's dedicated redacted acceptance note. Its initial missing-page
  report resolved after the scheduled S publish; local/cloud hashes matched.
- S explicitly authorized read-only inspection of the existing formal redaction
  rules and modification only of Note 462 via the formal write path. An existing
  documentation-address rule was selected in memory; no rule values were output.
  Two test paragraphs were appended through the formal App, with autosave
  confirmed. No direct SQLite writes and no redaction-rule changes occurred.
- Actual Note 462 checks all passed: redacted; replacement count > 0; expected
  safe two-paragraph text present; original source string absent in safe text and
  rendered HTML; safe_markdown NULL; HTML equals escaped safe_text; rule-file hash
  unchanged. Private comparisons used memory/private SSH pipes, boolean output
  only. S had confirmed the real page's paragraph display.
- REVOKE=PASS: through the App, temporarily removed only S from 462, preserving
  Gale and leaving common off. Published S; exact row absent in S mirror, still
  present in Gale. S confirmed old URL inaccessible and search result absent.
- Restored original S/Gale grant through the App, published S again, and reran
  exact-note redaction checks: all passed. Final 462 remains redacted and readable
  to its original S/Gale recipients, with the two authorized test paragraphs.
- Public Reader health/TLS and anonymous no-content checks passed. Existing
  Claude/Gale public OAuth metadata and unauthenticated MCP rejection smokes
  passed after Caddy reload; existing mirror actors and outbox tests passed.
  Final Reader/Caddy active, mirror permissions correct, all retry flags NO.

## Safety and recovery

- A prior proposed bulk probe of up to 200 real notes was rejected by automatic
  approval review and did not execute. It was abandoned. Production acceptance
  used only explicitly identified Notes 272 and 462; do not revive that probe.
- No user notes were deleted. No real rule values, original strings, replacement
  strings, note bodies/titles, Auth0 sub, secret or token are included in reports.
- Temporary credential-entry key was removed; temporary clipboard was cleared;
  fixture preview servers stopped. No credential entry or publisher pause remains.
- Keep rollback assets: old publisher pin `fdbb6eb97f90b6cbf50b62df196582f9f7ad51b2`
  and plist in `~/Library/Application Support/S-notes/backups/reader-publisher.RBqRyrS9/`;
  receiver backup `/root/greencloud-backups/reader-receiver.UgmYiIIC`;
  Caddy backup `/root/greencloud-backups/reader-caddy.yK30Qcwu/Caddyfile.before`;
  Reader file backups `/root/greencloud-backups/reader-directory.igUUHK2x` and
  `/root/greencloud-backups/reader-form.o9hdbbir`.
- Reader rollback: stop/disable only Reader, restore prior Caddyfile/reload,
  preserve mirror. No Reminder/Oracle/network-infrastructure change is required.

The original work order is complete. Do not continue with adjacent optimization.
