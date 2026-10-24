# Claude Web → GreenCloud Notes MCP：预备验收

2026-10-23（S 本地日期）。本次源码所在提交为准。

## 当前阶段

已实现并本地验证，GreenCloud 已安装预备文件；**尚未启用公开入口**。
S 尚需配置独立 Auth0 App/API、DNS，并隐藏录入新凭据。没有登录 Claude.ai。

- Branch：`codex/claude-web-greencloud-remote-mcp`。
- 基线 main / origin/main：`def7cdac71254999591d4279e4117c69449b9d03`。
- GreenCloud：实际主机 `optimal-living` / `109.94.170.215`。
- 新 unit：`snotes-claude-web-remote-mcp.service`，disabled / inactive。
- 入口：`src.claude_web_remote_mcp`，预留 `127.0.0.1:8770/mcp`。
- 目标公开地址：`https://snotes-claude.ke-den.de/mcp`，Caddy 尚未添加此 host。
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

## 生产安装记录

- Sources：`/srv/snotes/runtime`；独立配置/部署资产在
  `/srv/snotes/claude-web-remote`。
- 仅替换原有 shared auth 文件，其余 runtime/unit 文件为新增。
- 恢复文件：`/root/greencloud-backups/claude-web-remote-l8x2cm8C/greencloud_remote_auth.py.before`。
- 实际安装源文件哈希：`/srv/snotes/claude-web-remote/source-manifest.json`。
- 新凭据目标：`/root/greencloud-secrets/claude-web-remote-oauth.json`，尚未录入。

## 待完成

按 `deploy/greencloud/claude-web-remote/INSTALL.md` 完成 DNS、独立 Auth0 App/API
和隐藏凭据注入，再启用新服务/Caddy host。之后执行真实 public OAuth、授权
Notes 读/拒绝及一次 proposal 链验收；这些生产项目目前仍是 **PENDING**。
最后由 S 手工在 Claude.ai 添加连接并授权。尚不能称为已上线可连接。
