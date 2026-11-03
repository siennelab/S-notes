# S-notes 最终进度书｜Claude Web + S Remote Reader + Note ID 搜索｜LIVE / CLOSED

**原始记录：2026-10-23 PT / 2026-10-24 北京时间**

**本次补充：2026-11-02 America/Los_Angeles**

**状态：Claude Web Remote MCP、Claude 显示名、S Remote Reader，以及底栏 Note ID / 精确 ID 搜索均已完成各自工单范围内的施工与验收；相关实现已合入 main 并 push 到 GitHub。正式 Mac App 源码为 `9337152`，Reader 正式入口为 https://notes.ke-den.de。没有剩余施工或待 S 补做的验收项。**

本版基于 S 提供的 `S-notes-final-progress-20261023-CLAUDE-WEB-GREENCLOUD-REMOTE-MCP-LIVE-G.md` 补写，原上传文件保留不变。原文末尾的 merge / push 待办已由下文的实际收口记录替代。

本次在 S 上传的 `S-notes-final-progress-20261102-REMOTE-READER-LIVE-CLOSED-G.md` 基础上补入 Note ID 小工单的正式封口记录；上传原件保留不变，仓库内本文件为更新版本。

---

# 1. 本进度书覆盖什么

本进度书沿用原有 Claude Web / 显示名验收记录，并补入 S Remote Reader 的最终收口：

```text
A. Claude Web → GreenCloud Remote MCP
B. Claude actor UI display label: Claude Code → Claude
C. S mirror actor + private Remote Reader
D. Mac 底栏显示当前 note_id + #数字精确定位
```

第 2–9 节保留 2026-10-23 已记录的 Claude Web / 显示名验收事实；第 10–17 节保留 Reader 的施工、部署与真人验收结果，第 18 节补入 Note ID 工单封口。旧 Git / 构建版本为当时记录，当前正式 App 版本见第 12、18 节；后续文档提交不改变 App 源码版本。

不重做 GreenCloud 3.5，不修改 Notes / Reminder authority，不扩 Reminder 到 Claude Web，不迁移既有 actor ID。

旧正式边界继续保持：

```text
NOTES_AUTHORITY=Mac
REMINDER_AUTHORITY=GreenCloud
CLAUDE_STABLE_ACTOR_ID=claude-code
CLAUDE_WEB_REMINDER_EXPOSED=NO
```

---

# 2. Claude Web Remote MCP｜生产服务已 LIVE

施工 branch：

```text
codex/claude-web-greencloud-remote-mcp
```

实现 / 验收 commit：

```text
caf93db11d5b6425cd4b6fe915a0c3945f85a9d9
d0f2fe366865e03833f19b8a907f7d1c7314c618
```

生产入口：

```text
https://snotes-claude.ke-den.de/mcp
```

GreenCloud runtime：

```text
SERVICE=snotes-claude-web-remote-mcp.service
STATE=enabled / running
INTERNAL_LISTEN=127.0.0.1:8770
PUBLIC_PROXY=Caddy
```

本轮只新增 Claude Web 专用 service 与公开入口；Caddy reload 后生效，Gale 服务未因本票重启。

---

# 3. Claude Web 固定 actor｜复用 `claude-code`

本轮没有新增 `claude-web` actor。

最终固定：

```text
Claude Code local / Cloud actor
Claude.ai Web
→ stable actor / target_id = claude-code
```

因此继续复用既有：

```text
mirror/claude-code
Notes visibility / target grants
GreenCloud proposal / receipt path
AI Center review path
```

客户端不能通过参数覆盖 actor；actor 注入负例已验收。

这意味着：

```text
DATA_MIGRATION=NO
ACTOR_ID_RENAME=NO
EXISTING_NOTE_GRANTS_UNCHANGED=YES
```

---

# 4. Claude Web 仅开放 Notes｜12 个工具

Claude Web Remote MCP 当前正式暴露 12 个 Notes 工具：

```text
search_notes
get_note
get_note_markdown
list_directories
list_tags
list_ai_metadata_options
create_note
prepend_note
append_note
update_note_metadata
edit_note
get_operation_status
```

明确没有：

```text
Reminder tools
delete_note
AI visibility write
actor targets write
```

Notes 写操作仍然不是 Cloud 直写正式 Note，而是：

```text
Claude Web
→ GreenCloud proposal / outbox
→ Mac AI Center
→ S Accept / Reject
→ formal Note
→ receipt / mirror refresh
```

Mac 继续是 Notes 唯一正式 authority。

---

# 5. 独立 Auth0 / OAuth 隔离｜PASS

Claude Web 使用独立 Auth0 Application / API；没有复用 Gale 的 client / secret。

Auth0 Application：

```text
NAME=S-notes GreenCloud Claude Web Notes MCP
TYPE=Regular Web Application
CALLBACK=https://snotes-claude.ke-den.de/auth/callback

GRANT_TYPES:
- Authorization Code
- Refresh Token

CLIENT_AUTH:
- Client Secret (Post)
```

Auth0 API：

```text
NAME=S-notes GreenCloud Claude Web Notes MCP API
IDENTIFIER=https://snotes-claude.ke-den.de/mcp
SIGNING=RS256
PERMISSION=notes
ALLOW_OFFLINE_ACCESS=YES

USER_DELEGATED_ACCESS=Per-app authorization
CLIENT_ACCESS=No apps allowed
```

只有新建的 Claude Web Application 获得：

```text
notes = 1 / 1 permissions granted
```

其它 Gale / Default / 旧应用对这张 Claude API 保持：

```text
0 / 1
```

这不影响它们自己的既有 API / OAuth 权限。

Secret 未进入聊天、源码或 Git；由 GreenCloud root-only provision 流程录入。

---

# 6. OAuth 服务端验收｜PASS

砚在不登录 Claude.ai 的前提下完成独立 OAuth acceptance。

本地针对性检查：

```text
24 PASS
```

覆盖：

```text
MCP handshake
authorized Notes search / read
wrong audience reject
wrong subject reject
wrong scope reject
wrong signature reject
Gale config reject
actor parameter injection reject
Reminder unavailable
proposal / receipt path
OAuth authorization code
PKCE
refresh token
Gale regression
```

生产真实验收继续 PASS：

```text
PUBLIC_HTTPS_HANDSHAKE=PASS
NOTES_SEARCH_READ=PASS
UNAUTHORIZED_READ=DENIED
ACTOR_INJECTION=DENIED
REMINDER_EXPOSURE=NO
OAUTH_PKCE=PASS
TOKEN_REFRESH=PASS
GALE_SMOKE=PASS
```

临时 OAuth client / refresh material 已在验收后清理。

---

# 7. 测试投稿进入 AI Center｜链路确认

OAuth / Notes 写链验收创建了一条虚构 proposal：

```text
FICTIONAL claudeweboauthverification...
source=GreenCloud
actor=claude-code
operation=create_note
```

Mac AI Center 已真实接收到该 proposal，证明：

```text
GREENCLOUD_OUTBOX_TO_MAC_AI_CENTER=PASS
FORMAL_NOTE_DIRECT_WRITE=NO
```

本窗口明确看到该测试稿处于待确认界面；是否最终点普通拒绝 / 批量拒绝未单独记录，因此不得在本进度书中倒推其最终 review 状态。

---

# 8. Claude.ai 真人 connector｜OAuth consent 已 PASS

Claude Pro 个人账号在：

```text
Settings → Connectors → Add custom connector
```

添加：

```text
NAME=S-notes
URL=https://snotes-claude.ke-den.de/mcp
```

Claude 自动探测到 OAuth。

真人最终选择：

```text
Authentication = Sign in now
OAuth client = Use Claude's published identity
```

该项在 Claude UI 中显示：

```text
Detected
```

因此真人接入没有使用：

```text
Register automatically (DCR)
Use your own OAuth client
manual Client ID / Secret
```

S-notes consent 页真实显示并核对：

```text
Application Name=Claude
Verified domain=claude.ai
Application ID=https://claude.ai/oauth/mcp-oauth-client-metadata
Redirect URI=https://claude.ai/api/mcp/auth_callback
Requested Scopes=notes
```

S 点击：

```text
Allow Access
```

因此当前确认：

```text
CLAUDE_AI_CONNECTOR_ADD=PASS
CLAUDE_PUBLISHED_IDENTITY=PASS
CLAUDE_AI_OAUTH_CONSENT=PASS
REQUESTED_SCOPE=notes only
```

本轮不需要开启 Auth0 tenant-level Dynamic Client Registration，也没有把 Claude.ai callback 直接加入 Auth0 Application；两层 callback 保持各自职责：

```text
Auth0
→ https://snotes-claude.ke-den.de/auth/callback

S-notes OAuth wrapper
→ https://claude.ai/api/mcp/auth_callback
```

---

# 9. Claude 显示名｜`Claude Code` → `Claude`

Claude Web 与 Claude Code 现在共同使用稳定 actor：

```text
claude-code
```

因此旧 UI 名称：

```text
Claude Code
```

已经不再准确。

单独小票：

```text
BRANCH=codex/claude-display-label
COMMIT=facc1e2398b24a639f3d8979a83cc1e7f67f450d
MESSAGE=Rename Claude display label without changing actor identity
```

最终：

```text
DISPLAY_NAME=Claude
STABLE_ACTOR_ID=claude-code
TARGET_ID=claude-code
DATA_MIGRATION=NO
EXISTING_NOTE_AUTHORIZATION_UNCHANGED=YES
```

S 已在重建后的正式 App 真人确认 AI 可见性对象显示：

```text
Claude
```

验收：

```text
TARGETED_CHECKS=16_PASS
FORMAL_APP_REBUILD=PASS
CODESIGN=PASS
HUMAN_DISPLAY_GATE=PASS
```

---

# 10. Git 最终收口｜已合入 main 并 push

旧版记录的 `caf93db`、`d0f2fe3` 和 Claude 显示名提交 `facc1e2` 已全部包含在当前 main 中。原来的“尚未 merge / push”已经完成，不再是待办。

Reader 复用同一正式仓库，在 `codex/s-remote-reader` 施工后以 fast-forward 合入 main；没有 force push。

本次补写进度书前，已核对本地 refs 和 GitHub 实际远端 main：

```text
REPOSITORY=github.com:siennelab/S-notes.git
WORKSPACE=/Users/s/Documents/项目/S-notes
BRANCH=main
HEAD=02f9dc595fd6a2c4255dce4661b3eb837b4d9b8e
MAIN=02f9dc595fd6a2c4255dce4661b3eb837b4d9b8e
ORIGIN_MAIN=02f9dc595fd6a2c4255dce4661b3eb837b4d9b8e
GITHUB_MAIN=02f9dc595fd6a2c4255dce4661b3eb837b4d9b8e
WORKTREE=CLEAN
PUSH=PASS
GIT_CLOSEOUT=CLOSED
```

主要提交关系：

```text
facc1e2  Rename Claude display label without changing actor identity
  ↓
51150fc  Add S mirror actor and private read-only Remote Reader
  ↓
d0768aa  Include child and default locations in Reader parent directories
  ↓
17a5e0d  Accept privacy-preserving form origins with session CSRF checks
  ↓
297a818 / c0b5a33  阶段性施工与验收记录
  ↓
02f9dc5  Close Remote Reader deployment and live acceptance
```

以上是 Reader 施工收口快照；本进度书作为后续文档提交保存，不改变下文记录的生产代码或 App 版本。当前分支最新文档提交号以 Git 记录为准。

---

# 11. S Remote Reader｜已 LIVE

正式入口：

```text
https://notes.ke-den.de
```

S 已在手机通过现有 Auth0 的 S 账号登录，并确认首页、目录、正文和中文搜索可用。

Reader 是独立的只读网页，读取云端 S mirror；Mac 仍是 Notes 唯一正式 authority。Reader 不提供写笔记、投稿或 MCP 工具。

正式 App 的对象顺序已由 S 确认：

```text
通用 → S → Gale → …
```

新增稳定对象为 `s`，显示名为 `S`。现有权限语义继续生效：

```text
AI完整可见 + S / 通用 → S mirror 中为 full
AI脱敏可见 + S / 通用 → S mirror 中仅有 safe_text
hidden → 不可读
```

S 仅加入 mirror actor 集合。proposal / outbox / receipt 的 actor 仍然只有 `claude-code`、`clawbot`、`gale`；没有把 S 加入投稿对象，没有新增权限字段或迁移正式数据库。

---

# 12. 正式部署与运行版本

```text
READER_SERVICE=snotes-reader.service
READER_LISTEN=127.0.0.1:8771
READER_WORKERS=1
READER_RUNTIME=/srv/snotes/reader/runtime
READER_SOURCE_MANIFEST=/srv/snotes/reader/source-manifest.json
READER_CODE=17a5e0d682ead286f4d426818bc09e9f6da04d5a
READER_PROXY=Caddy HTTPS

S_MIRROR_PATH=/srv/snotes/mirror/s/current.db
S_MIRROR_DIRECTORY_OWNER=snotes:snotes-reader
S_MIRROR_DIRECTORY_MODE=2750
S_MIRROR_FILE_MODE=0640
READER_USER=snotes-reader

FORMAL_APP=/Users/s/Documents/项目/S-notes/dist/S-notes.app
FORMAL_APP_SOURCE=9337152498a4409db7f796bc699163d64f5816bf
PUBLISHER_PIN=51150fcf3bac316e6c9f86fd73464fe4dba5e3ae
PUBLISHER_INTERVAL_SECONDS=300
```

Reader 工单收口时的正式 App 源码为 `51150fcf3bac316e6c9f86fd73464fe4dba5e3ae`；当时后续目录与搜索修复仅涉及 Reader。本次 Note ID 工单已从合并后的干净 main `9337152` 重建正式 App，通过 arm64 构建、codesign、实际窗口启动及正常退出检查，详见第 18 节。publisher pin 与 Reader 部署版本没有因本工单变更。

Mac publisher 使用 Application Support 下的固定源码导出及对应 venv，不从开发工作区运行。收口时 LaunchAgent 已恢复正常，最近退出码为 0，四个 mirror actor 的 `needs_retry` 均为 NO。

Reader 使用独立 Linux 用户，只能读 S mirror，不能写 mirror，也不能读取其它 actor 的 mirror。每次查询使用新的只读 SQLite 连接，使用 immutable / query_only / trusted_schema 限制并立即关闭。

Caddy 配置已通过校验并 reload，真实 HTTPS 证书签发成功。没有替换 Caddy，也没有扩大到 Reminder、Oracle 或网络基础设施施工。

---

# 13. Reader 登录与内容保护｜PASS

Reader 使用独立 Auth0 Regular Web Application：

```text
NAME=S-notes Remote Reader
FLOW=Authorization Code + PKCE
SCOPE=openid
SIGNING=RS256
IDENTITY=fixed existing S subject
```

登录校验 issuer、audience、过期时间、nonce 等信息；浏览器登录事务与会话均有期限。会话 cookie 为 Secure / HttpOnly / SameSite=Lax；服务重启后需要重新登录。

凭据由 root-only 文件经 systemd LoadCredential 提供，没有进入源码、Git 或进度书。没有改动其它 Auth0 Application、共享注册策略或 tenant-wide MFA 策略。

页面使用 no-store / no-referrer / nosniff / noindex 和严格 CSP；没有页面脚本，日志不记录访问正文或搜索内容。

```text
AUTH0_LOGIN=PASS
UNAUTHENTICATED_CONTENT_READ=DENIED
READER_MIRROR_WRITE=DENIED
OTHER_ACTOR_MIRROR_READ=DENIED
SECRET_OR_PRIVATE_RULE_VALUES_IN_REPORT=NO
```

---

# 14. 正文、目录与中文搜索｜真人验收 PASS

完整可见笔记按受限 Markdown 渲染，支持段落、标题、列表、引用、代码、表格和既有字号。原始 HTML 被转义；不安全链接协议被拦截，外部图片只显示链接，不自动加载图片。

脱敏笔记只对 `safe_text` 做 HTML 转义并保留换行，不重建 Markdown；`safe_markdown` 保持 NULL。

本轮发现并修复的两个实际问题：

1. 主分类目录初期显示为空。根因是主目录查询未包含默认子分类和下属子分类；已修复，具体子目录仍精确匹配。S 已确认目录与排版正常。
2. 手机中文搜索初期提示“请求无效”。根因是 no-referrer 下正常表单会提交 `Origin:null`；已在保留强制会话 CSRF 校验的前提下支持此情况，显式外站来源仍拒绝。真实浏览器表单已验证返回 200，S 已确认手机搜索正常。

验收证据：

```text
INITIAL_TARGETED_TESTS=118_PASS
LATEST_READER_RELATED_TESTS=28_PASS
FULL_RENDER=PASS
DIRECTORIES=PASS
CHINESE_SEARCH=PASS
MOBILE_390PX_LAYOUT=PASS
REDACTED_PARAGRAPH_BREAKS=PASS
```

移动端实际渲染为 17px 正文，页面无横向溢出，代码块可横向滚动，页面无 img / script 元素。自动化检查使用临时 HOME / 测试数据库，没有以正式用户 Notes 数据库作为测试夹具。

---

# 15. 指定测试笔记的实际脱敏与撤权｜PASS

本轮仅针对 S 明确指定的测试笔记 462 完成实际 replacement 验收，没有扩大读取真实笔记范围。

按照 S 的授权，砚只读现有正式 redaction rules，在内存中选择一条适合安全测试的既有规则，并通过正式 Mac App 写入路径向 462 追加两段测试正文。没有直接写正式 SQLite，没有修改 redaction rules，也没有输出任何真实 `from` / `to`、正文或标题。

发布 S mirror 后，针对该编号的结果全部通过：

```text
NOTE_462_VISIBILITY=redacted
REDACTION_COUNT_POSITIVE=PASS
SAFE_REPLACEMENT_TEXT_PRESENT=PASS
ORIGINAL_FROM_ABSENT_IN_SAFE_TEXT=PASS
ORIGINAL_FROM_ABSENT_IN_RENDERED_HTML=PASS
SAFE_MARKDOWN=NULL
ESCAPED_SAFE_TEXT_RENDER=PASS
TWO_PARAGRAPH_BREAKS=PASS
REDACTION_RULE_FILE_UNCHANGED=PASS
```

真实撤权验收通过正式 App 暂时取消 462 的 S 授权，保留 Gale；发布 S mirror 后，S mirror 中该编号消失，Gale mirror 保留。S 在手机确认旧链接不可读、搜索中也不再出现。

验收后已通过正式 App 恢复原有 S / Gale 授权，再次发布 S 并核对实际脱敏结果，全部 PASS。最终 462 仍为 redacted，保留经授权添加的测试段落，S / Gale 可读；通用授权未开启，规则文件未改动。

```text
REVOKE_OLD_URL=DENIED
REVOKE_SEARCH_RESULT=ABSENT
GALE_GRANT_PRESERVED=YES
S_GRANT_RESTORED_AFTER_ACCEPTANCE=YES
```

---

# 16. 更新时机、Mac 离线与既有能力回归

当前正式 App 的“立即更新镜像”按钮仍只触发 Gale mirror；S mirror 使用现有五分钟定时 publisher。因此，按下该按钮后 Gale 先更新、Reader 在下一次 S 定时发布后显示，是当前发布路径的实际行为。

本轮观察到的 11:31 按按钮、11:36 Reader 出现笔记，与此机制一致。S mirror 发布后，Reader 直接读取新快照。本工单没有扩展为修改该按钮。

Mac 离线读取已完成针对性验收：临时卸载 Mac publisher，在 GreenCloud 以 Reader 用户读取并渲染指定笔记 272，随后立即恢复同一固定版本 LaunchAgent，退出码为 0。证明 Reader 可在 Mac publisher 不运行时继续读取已发布的云端快照。

Caddy reload 后，Claude / Gale 的公开 OAuth metadata 与未登录 MCP 拒绝检查通过；既有 Claude / Gale / Clawbot mirror 和 outbox 路径的相关回归通过。

```text
MAC_OFFLINE_READ=PASS
CLAUDE_REGRESSION=PASS
GALE_REGRESSION=PASS
CLAWBOT_REGRESSION=PASS
NOTES_AUTHORITY=Mac
REMINDER_AUTHORITY=GreenCloud
EXISTING_PROPOSAL_RECEIPT_BOUNDARY=UNCHANGED
```

这些是本轮已完成的验收记录，不表示本次补写文档又重复执行了生产测试。

---

# 17. 当前最终状态与接班说明

```text
CLAUDE_WEB_REMOTE_MCP=LIVE
CLAUDE_DISPLAY_NAME=Claude
CLAUDE_STABLE_ACTOR_ID=claude-code
CLAUDE_WEB_NOTES_TOOLS=12
CLAUDE_WEB_REMINDER_TOOLS=0

READER_URL=https://notes.ke-den.de
S_ACTOR_AND_UI_ORDER=PASS
S_MIRROR=PASS
S_IN_PROPOSAL_ACTORS=NO
AUTH0_LOGIN=PASS
FULL_RENDER=PASS
REDACTED_RENDER=PASS
CHINESE_SEARCH=PASS
REVOKE=PASS
MAC_OFFLINE_READ=PASS
UNAUTHENTICATED_READ=DENIED

IMPLEMENTATION=COMPLETE
DEPLOYMENT=COMPLETE
LIVE_ACCEPTANCE=COMPLETE
GIT_CLOSEOUT=PASS
TASK=CLOSED
REMAINING_CONSTRUCTION=NONE
```

回滚资产和运行路径保留在仓库的 `status/handoff.md`，没有删除旧 publisher pin 或施工前的 Caddy / receiver 备份。临时凭据录入材料已清理，剪贴板已清空，测试预览服务已停止，没有遗留 publisher 暂停。

旧版第 7 节中虚构 Claude OAuth proposal 的最终 review 状态没有新证据，本版仍不补写为已接受或已拒绝；它不构成 Reader 剩余施工项。

> 砚，S-notes 的 Claude Web Remote MCP、Claude 显示名与 S Remote Reader 已完成当前范围施工、生产部署、真人验收和 GitHub 收口。Reader 为 `https://notes.ke-den.de`，只读 `/srv/snotes/mirror/s/current.db`，Mac 继续是 Notes authority。462 的实际脱敏与撤权验收通过，S / Gale 授权已恢复、规则未修改。S mirror 使用既有五分钟 publisher；“立即更新镜像”当前仍为 Gale-only。没有待补验收或待继续施工，不要把旧版进度书中的 merge / push 待办重新执行，也不要扩成相邻优化。

---

# 18. 底栏 Note ID / 精确 ID 搜索｜正式封口 CLOSED

2026-11-02（America/Los_Angeles），S 已真人验收 `codex/note-id-search` / `9337152`，随后明确授权按最小流程合入 main、重建正式 App、实际启动并 push。

交付行为：

- 正文底栏顺序为 `垃圾桶图标  笔记 ID：xxx  已保存/保存状态`，沿用原状态栏样式；ID 与当前正文加载一起更新，未选择笔记时隐藏。
- 整个搜索内容严格符合 `#` + ASCII 纯数字时，按真实 `note_id` 精确定位，并走现有笔记切换 / 保存 / 打开路径；不存在或超出整数范围时正常显示无匹配结果，不创建内容。
- `#abc`、`#449 abc` 以及普通文字搜索继续走原路径。左侧列表、标题格式、ID 生成、schema、MCP contract、AI 权限、mirror、Reader 与 Reminder 均未因本工单改动。

```text
TASK_BRANCH=codex/note-id-search
IMPLEMENTATION_COMMIT=9337152498a4409db7f796bc699163d64f5816bf
HUMAN_GATE=PASS
TARGETED_TESTS=10_PASS
CANDIDATE_BUILD=PASS
CANDIDATE_CODESIGN=PASS
MERGE=FAST_FORWARD_ONLY

FORMAL_APP=/Users/s/Documents/项目/S-notes/dist/S-notes.app
FORMAL_APP_SOURCE=9337152498a4409db7f796bc699163d64f5816bf
FORMAL_APP_SOURCE_DIRTY=NO
FORMAL_BUILD=PASS_ARM64
CODESIGN=PASS_AD_HOC
FORMAL_APP_LAUNCH=PASS
FORMAL_APP_NORMAL_EXIT=PASS_EXIT_0
CODE_PUSH=PASS_ORIGIN_MAIN
TASK=CLOSED
REMAINING_CONSTRUCTION=NONE
```

10 项针对性测试与 Candidate 真人 Gate 是此前已完成的验收，本次封口没有重跑全量测试，也没有新增代码修改。正式构建使用项目原有脚本；启动检查直接运行新构建的正式可执行文件，使用临时 HOME / 数据库，确认进程完成启动并有一个可见主窗口，再仅对该验收进程请求正常退出，退出码为 0。S 原有正式版和 Candidate 实例均保留。

功能提交已 push 后再补写本进度书；后续提交仅涉及本文件，不改变正式 App 的 `9337152` 源码标记。文档提交 push 后再次核对 `HEAD == main == origin/main`、远端 main 与本地一致、worktree clean，以最终 Git 状态为准。上传原件未改写。
