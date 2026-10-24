# Claude Web → GreenCloud Notes MCP：部署与验收

2026-10-23（S 本地日期）。本次源码所在提交为准。

## 当前阶段

已实现、部署并通过真实独立 MCP 客户端验收。S 已配置独立 Auth0 App/API、
DNS 和新凭据，并亲自完成 Auth0 验收授权。没有登录 Claude.ai；最后的
Claude.ai 添加连接/授权仍由 S 手工完成。

- Branch：`codex/claude-web-greencloud-remote-mcp`。
- 基线 main / origin/main：`def7cdac71254999591d4279e4117c69449b9d03`。
- GreenCloud：实际主机 `optimal-living` / `109.94.170.215`。
- 新 unit：`snotes-claude-web-remote-mcp.service`，enabled / active / running。
- 入口：`src.claude_web_remote_mcp`，`127.0.0.1:8770/mcp`。
- 公开地址：`https://snotes-claude.ke-den.de/mcp`，Caddy 已添加独立 host。
- 固定 OS 身份 `claude` / UID 2001，固定 Notes actor `claude-code`。
- 不新增 actor、metadata option、mirror、Notes authority 或 Reminder 工具。

## 已确认的现有链路

Mac `ActorMirrorPublisher` → 既有 forced-command SSH receiver →
`/srv/snotes/mirror/claude-code/current.db` → UID 2001 的
`compose_actor_cloud_runtime()` / `CloudReadService`。Mac 原有 publisher
LaunchAgent 仍是 300 秒周期、最近 exit 0，working directory 未改变。
本次依赖的生产 Notes compose / registry / proposal 源码和本地基线 SHA-256 一致。

投稿复用 `CloudProposalService` → localhost `8767` Notes outbox → Mac AI Center
/ receipt。Mac 仍是正式 Notes 唯一本体。新入口没有直接正式写入能力。

## 已通过

- 24 项针对性检查：Claude HTTP/Auth0、Gale HTTP/Auth0，以及已有 Mac inbox
  import/accept/mirror/receipt 与 actor-scoped terminal receipt。
- 全部使用项目现有 `.venv`、临时 HOME 和虚构数据库。
- Claude 握手、授权 search/get/Markdown、Gale/Clawbot/hidden 拒绝、actor 注入
  无效、无 Reminder、真实临时 outbox 投稿与回执均通过。
- 独立凭据 helper 的虚构数据检查：拒用 Gale client/secret，0600，不覆盖，
  不输出 secret；shell 语法和 diff whitespace 检查通过。
- GreenCloud 实际 UID 2001 组合新 runtime 通过，不读取真实 Notes 正文。
- Gale 生产最小 initialize + 25 tools discovery 通过；PID 950、启动时间
  `2026-10-23 23:01:01 UTC` 均未改变。未重启 Gale、outbox、Reminder 或 publisher。
- 正常公开 HTTPS 证书验证、resource/server OAuth discovery，通过。
- 真实 authorization-code + PKCE、精确 issuer、refresh，通过；临时验收
  OAuth client / refresh metadata 已清理，access token 按原 TTL 过期。
- localhost/public 的完整 12 tools contract 一致；无认证和错误 token 均 401。
- 实际授权 Note 31 搜索/读取、实际 Gale-only Note 12 拒绝，通过。验收没有
  输出正文、标题、搜索词、subject、code、token 或 secret。
- query/header/top-level actor 篡改不能读取 Note 12，identity arguments 被拒，
  Reminder 既不发现也不能调用，通过。
- 真实测试投稿 `claudeweboauthverificationv1x9q7k3` 已进入现有 outbox；
  Mac 通过原有 pull/import/receipt 链接收，远端状态为 `acknowledged`，
  时间 `2026-10-24T01:31:58Z`。尚未创建正式 Notes，AI Center 留有一条待审测试稿。
- 生产 client ID/secret、JWT signing key、storage encryption key 均与 Gale 不同；
  `claude` 用户不能读取 Gale 的 root-only 凭据和私有 OAuth state。

## 生产安装记录

- Sources：`/srv/snotes/runtime`；独立配置/部署资产在
  `/srv/snotes/claude-web-remote`。
- 仅替换原有 shared auth 文件，其余 runtime/unit 文件为新增。
- 恢复文件：`/root/greencloud-backups/claude-web-remote-l8x2cm8C/greencloud_remote_auth.py.before`。
- 实际安装源文件哈希：`/srv/snotes/claude-web-remote/source-manifest.json`。
- 独立凭据：`/root/greencloud-secrets/claude-web-remote-oauth.json`，root `0600`，
  仅由新服务通过 systemd LoadCredential 注入。
- Caddy 恢复文件：`/root/greencloud-backups/claude-web-caddy-WP76g5Bh/Caddyfile.before`。
- 真实协议验收记录：`/root/greencloud-secrets/claude-web-remote-verification/verification.json`。

## 待完成

由 S 手工在 Claude.ai 添加 `https://snotes-claude.ke-den.de/mcp`，选择 OAuth
和 Register automatically（有该选项时），不填 Gale 或上游 Auth0 client secret。
使用本次获准的 S 账号完成授权，再在 Claude 对话里启用连接器。
