# Web Gale → GreenCloud Remote MCP 验收

验收日期：2026-10-04（America/Los_Angeles）。第二票已部署，停在创建 ChatGPT connector 之前。

## 基线与范围

第一票 `codex/web-gale-greencloud-internal` 已 ff-only 合入 main 并 push。
`main == origin/main == 2f596c7096156b801f56671560f95ac57c145259`，开第二票前 HEAD 相同、worktree clean。
第一票记录 `docs/web-gale-greencloud-internal-verification.md` 保留，第一票功能未修改。

第二票 branch：`codex/web-gale-greencloud-remote-mcp`。独立 commit，不 merge main、不 push、不补 final-progress。

正式改动共 14 个文件：

- 接入层：`src/greencloud_remote_mcp.py`、`src/snotes/transport/mcp/greencloud_remote_transport.py`、`src/snotes/transport/mcp/greencloud_remote_auth.py`。
- 部署：`deploy/greencloud/gale-remote/` 下 `config.json`、`gale.Caddyfile`、`snotes-gale-remote-mcp.service`、`install.sh`、`activate.sh`、`provision_credentials.py`、`verify_oauth.py`、`INSTALL.md`。
- 检查：`tests/test_greencloud_gale_remote_auth.py`、`tests/test_greencloud_gale_remote_transport.py`。
- 本验收记录。

## 正式部署

| 项目 | 实际状态 |
| --- | --- |
| 主机 | GreenCloud `optimal-living`，`109.94.170.215` |
| OS / actor | `snotes-gale` / 固定 `gale` |
| listener | **仅 `127.0.0.1:8768`** |
| systemd | `snotes-gale-remote-mcp.service`，enabled / active / running |
| 公网资源 | `https://snotes-gale.ke-den.de/mcp` |
| 源码 | 现有 `/srv/snotes/runtime` 内三个新增模块 |
| 依赖 | `/srv/snotes/gale-remote/vendor`，使用原 runtime Python；原 venv 未修改 |
| 非秘密配置 | `/srv/snotes/gale-remote/config.json` |
| OAuth state | `/srv/snotes/gale-remote/oauth`，Gale `0700`，Fernet 加密 |
| 凭据 | GreenCloud root-only 文件 `0600`，systemd `LoadCredential` 私有注入 |

Remote 直接调用第一票 `compose_greencloud_internal_runtime()`，继续使用同一 Notes / Reminder registry、actor binding、权限和已有后端。没有第二套 outbox、Reminder API、mirror、publisher、业务逻辑或 credential registry。

现场 systemd 凭据为 root:root `0440`。认证检查只额外接受这个 root owner/group 形态；原始 secret 保持 root `0600`。`0644`、非 root group 的 `0440`、错误 owner、symlink 和超大凭据继续被拒绝。

## Auth0 与 Mixed authentication

独立 Auth0 Regular Web Application 和独立 RS256 API 均为 `S-notes GreenCloud Gale Remote MCP`。
audience 为 `https://snotes-gale.ke-den.de/mcp`；唯一业务 scope 为 `gale.read`；upstream callback 为 `https://snotes-gale.ke-den.de/auth/callback`。
仅新 GreenCloud 应用获得该 API 的 `gale.read` 用户委托权限，machine client permissions 为零；后端仅接受选定的现有 S subject。
Auth0 自动附带的 API Test Application 未获该 API 权限、未使用其凭据。

Authorization Code + PKCE、精确 issuer/audience、签名、expiry、subject、scope 校验，以及持久化加密 OAuth state 在 GreenCloud 独立完成。新 client secret、签名 key、加密 key 均独立；不加载 Oracle 凭据，不请求 Oracle 完成认证。

Mixed discovery 的匿名范围仅为 `initialize`、`notifications/initialized`、`ping` 和 `tools/list`。
所有 `tools/call` 需要 OAuth：无凭据返回 MCP `isError` 与 `mcp/www_authenticate` challenge；错误 Bearer 返回 HTTP **401**，不能回落到匿名 discovery。
25 工具均动态添加顶层与 `_meta.securitySchemes` 的 OAuth2 `gale.read` 信息，input/output schema 不变。

下游仅允许精确的稳定 ChatGPT callback `https://chatgpt.com/connector_platform_oauth_redirect` 和 localhost 验收 callback。
RFC 9207 metadata 与成功/失败 redirect 使用相同精确 issuer `https://snotes-gale.ke-den.de/`。
本票没有创建或操作 ChatGPT connector。

## DNS / Caddy / TLS

Cloudflare 仅新增 DNS-only A：`snotes-gale.ke-den.de → 109.94.170.215`，TTL Auto。公共 resolver 实际返回该 IP。
Caddy 仅追加标记为 `S-NOTES GALE REMOTE MCP` 的独立 host，将 MCP / OAuth 路由转至 `127.0.0.1:8768`；其它 path 404，新 host 的 access log 丢弃。
candidate validation 和 reload 成功，现有 Caddy 内容仍是完全相同的字节前缀，ntfy / Reminder host 保持原语义。

正常 DNS 和指定实际 GreenCloud IP 的 HTTPS 均通过完整证书/hostname 验证，无 insecure TLS 绕过。
最终监听 `8766`、`8767`、`8768` 全部为 `127.0.0.1`；没有这些端口的公网 listener。临时 OAuth callback / SSH tunnel 已关闭。

唯一必要配置恢复备份：`/root/greencloud-backups/gale-remote-wGO4Ewz7/Caddyfile.before`。
回退方法见部署说明：只停新 unit、只移除当前 Caddyfile 中本票 block，不用旧整文件覆盖后续配置。

## 已完成 Gate

| Gate | 结果 |
| --- | --- |
| 真实 Auth0 code / PKCE / 精确 issuer / refresh | PASS |
| localhost authenticated initialize / tools/list | PASS，25 tools |
| public authenticated initialize / tools/list | PASS，25 tools |
| protected call 无凭据 | PASS，OAuth challenge，未执行数据工具 |
| 错 Bearer call / discovery | PASS，401 |
| internal / fixture / public 完整契约 | PASS，25 tools = Notes 12 + Reminder 13 |
| fictional localhost HTTP Notes / metadata / Reminder read | PASS |
| payload / query / header / tool argument actor spoof | PASS，始终 Gale；越界 argument 被拒绝 |
| hidden / other-actor Notes | DENIED |
| Delete Note / Reminder owner-only | unavailable / DENIED |
| visibility / targets write | blocked，proposal enqueue 为零 |

完整契约比较包括名称、描述、input/output schema；只剔除 Remote 新增的 OAuth discovery metadata。
canonical contract SHA256：`9633df0f86680645def00420b8db1f6d696d5d52de3b654740ebf6eeb9b49dad`。
internal discovery 在真实 Gale OS 身份下启动原入口模块，并以 audit hook 禁止任何 SQLite connection，因此未读取真实用户数据库。

最终本票 12 项 targeted tests PASS；GreenCloud 隔离环境也通过相关认证、HTTP 与权限检查。所有数据检查用临时 HOME、虚构 publisher mirrors / Reminder DB；没有真实 Note 正文、真实数据写入或 scheduler / ntfy 测试发送。
Python 编译与 shell syntax 检查通过。未重跑第一票 121 tests / 60 项 filesystem matrix。

## 一次重启恢复与临时验收清理

仅新 unit 在 2026-10-04 **21:24:32 PDT** 重启，最终 MainPID `22923`，active/running。
同一真实 OAuth 身份在重启后重新 initialize，并且 `tools/list` 的 25 工具与重启前完整结果一致。

验收 harness 在后置撤销/cleanup 断言处失败，未打印 credential/token。
重启后 Gate 已用只读证据核对：harness 只有在上述 initialize 和完整 25-tool 对比断言通过之后才调用 refresh；现场 SDK 保留了 **21:24:36.982 / 21:24:36.985 PDT** 的对应新 token-reference 发行记录。
该证据位于重启之后，确认前置认证恢复 Gate 已通过。没有为补日志再次重启服务。
验收脚本现改为直接清理所拥有的注册/refresh 元数据，并在 cleanup 前保留已通过的 Gate 记录；未修改 SDK 或认证 runtime。

临时 verification DCR registrations **0**、MCP refresh metadata **0**，由单独 cleanup 与只读计数确认。
生产 Auth0 App/API、GreenCloud credential、其它客户端、Oracle 全部保留。
SDK 的 access JWT / encrypted references 按正常 expiry 保留；这次 access 映射最晚于 **2026-10-05 21:24:36 PDT** 到期。未强删 access 映射，未引入 blacklist 或修改第三方库。

无秘密 live records：`/srv/snotes/gale-remote/contract-verification.json`、`cleanup-verification.json`；首次真实 OAuth 记录在 root-only verification 目录内。

## 邻接服务与 Oracle

Notes outbox、Reminder production API、Caddy 均 active；internal 原源码 SHA256 仍为
`36c6391e6215221bb2bb91652841bd40b9db292985396553dade86a7670e3e34`，25-tool discovery 正常。
Mac 同一个 `com.snotes.greencloud-mirror-publish` LaunchAgent 保持 300 秒周期、原 runtime working directory，最近 exit code `0`。

Oracle 全程只读：原 Gale service 仍 active、MainPID `719370`；认证源码 hash 与开工参考相同。
Oracle service / mirror / outbox / schema / credential、`snotes.ke-den.de`、旧 ChatGPT connector 均 untouched，旧链路保留为 fallback。

本票停在 `REMOTE_MCP_READY_FOR_CHATGPT=YES`；未创建 connector、未 Refresh Actions、未 Disconnect Oracle、未做真人 Web Gale E2E。
