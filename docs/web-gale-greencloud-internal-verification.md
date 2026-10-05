# Web Gale → GreenCloud：第一票内部验收

2026-10-04（S 本地日期）。Branch `codex/web-gale-greencloud-internal`，正式基线 `37cb863847baf4ee913ab2515dc9a6d1a4660ba1`。最终 commit 以本记录所在提交为准；main / origin/main 保持基线，不 merge、不 push。

接手时已在正确 branch：9 个核心文件未提交修改，另有统一 MCP 入口、组合层和 wrapper 三个新文件；无部署、无测试 fixture 成果、无 commit。保留并完成这些代码，没有 reset / restore / 重做设计。

## 已实现并内部部署

- 正式 stable actor ID：`gale`（复用既有 registry）。OS/service identity：`snotes-gale`，UID/GID `2007:2007`，shell `/usr/sbin/nologin`，home `/nonexistent`，仅自己的 primary group，无 sudo。
- Mirror：`/srv/snotes/mirror/gale/current.db`，目录 `snotes:snotes-gale 2750`，文件 `snotes:snotes-gale 0640`。由既有 Mac → GreenCloud publisher 更新，schema 2 和原有 metadata catalog；未同步正式 Notes DB。
- Notes credential：`/srv/snotes/actor-credentials/gale/notes-proposal-token`；Reminder credential：`/srv/snotes/actor-credentials/gale/reminder-token`。均为 `snotes-gale:snotes-gale 0400`，位于 `root:snotes-gale 0750` 目录。值不记录、互不通用，没有借用其它 actor/owner/Oracle 凭据。
- Notes server credential：`/srv/snotes/actor-credentials/service/gale-notes-proposal-token`，root-only `0600`，parent `0700`，通过既有 Notes outbox unit 的 `gale.conf` systemd LoadCredential 加载。
- 入口：`runuser -u snotes-gale -- /usr/local/bin/snotes-gale-internal-mcp`。参数拒绝、环境清空、actor 固定。Notes 与 Reminder 分别复用已有 `127.0.0.1:8767` / `127.0.0.1:8766` 服务。
- 继续使用 `/srv/snotes/data/outbox.db`（v3）和 `/srv/snotes/data/reminder-production.db`。Gale 无 direct DB access；Notes authority 仍在 Mac。

## 正式工具 discovery

以 Cloud Notes registry 和 Reminder ACTOR_COMMANDS 动态派生，实际 Gale stdio discovery 为 **25 个**。

Notes（12）：`search_notes`、`get_note`、`get_note_markdown`、`list_directories`、`list_tags`、`list_ai_metadata_options`、`create_note`、`prepend_note`、`append_note`、`update_note_metadata`、`edit_note`、`get_operation_status`。

Reminder（13）：`list_reminder_labels`、`list_reminder_tables`、`get_reminder_table`、`list_reminder_items`、`get_reminder_item`、`create_reminder_label`、`create_reminder_table`、`update_reminder_table`、`create_reminder_item`、`update_reminder_item`、`complete_reminder_item`、`pause_reminder_item`、`resume_reminder_item`。

## 验证证据

最终源码 **121 项 targeted tests PASS**：Gale integration、actor mirror/publisher/receiver/read/metadata discovery、Notes proposal/runtime、Reminder API/runtime/actor adapter、Mac GreenCloud pull/inbox/accept/receipt，及共享 registry 的两个 stdio discovery/strict-schema 检查。均使用项目现有 `.venv`、临时 HOME 与虚构数据库。

GreenCloud 实际 UID/live internal smoke PASS：

- Gale common / Gale-only / Gale-authorized 可读；claude-only / clawbot-only / hidden 猜 ID 不可读。full / redacted、safe Markdown 和新增 metadata 全部 PASS。
- 三个 metadata catalog discovery PASS。
- 隔离服务中的五种 proposal（create / prepend / append / metadata / edit）及 status PASS；真实 Gale Notes credential 认证，未向 production outbox 写测试投稿。
- 隔离 Reminder 服务中的 list/read + fictional create table PASS；使用真实 Gale Reminder credential。服务运行在现有 `snotes` UID；临时 DB 与端口，正常关闭，无 scheduler/ntfy。
- 60 次实际文件 read/write opens 被 EACCES 拒绝：其它 actor mirror/credential、Notes outbox、Reminder production / 其它 data DB 及 backups、root-only API/owner credential mapping。Gale 自己的 mirror / tokens 可读不可写，无 sudo。
- Owner-only Reminder 命令全部 DENIED；Notes delete unavailable；visibility/targets writes blocked；客户端 actor injection blocked；Notes/Reminder 交叉凭据认证 DENIED。
- claude/openclaw 原有 Notes 各 discovery 12 tools / safe read/status PASS；Reminder actor wrappers 各 list PASS。
- 部署源码 hashes 与 source manifest PASS；临时 fixture 和临时服务已移除。最终监听地址与开工基线一致，没有 8768 或新公网入口。

附带完整本地 Package5 协议组触发了本机 Qt/WebEngine GPU 崩溃；没有扩修 UI。其与本票直接相关的两个 discovery/schema case 在最终 121 项测试内均 PASS。生产服务和本票 headless/stdin 边界没有这个阻塞。

## 实际部署与恢复

GreenCloud 初次安装替换/新增 65 个文件（含必要源依赖闭包）；随后 registry 类型导入修复只更新 2 个文件（tools.py 和 manifest）。保留既有 venv 和依赖；没有增加 Cloud Markdown 依赖。Notes outbox、Reminder API、scheduler timer、backup timer 最终均 active。

业务资产继续在 `/srv/snotes/runtime`、`reminder-runtime`、`mirror/gale`、`actor-credentials`、`gale-internal`、既有 `outbox-deploy` 内。新增系统入口 `/usr/local/bin/snotes-gale-internal-mcp` 和既有 outbox unit 的 `/etc/systemd/system/snotes-notes-outbox.service.d/gale.conf`；现有 root-only Reminder API mapping 只增加 Gale actor token。服务器保存 `source-manifest.json`、`deployment-record.json`、`verification-record.json` 和 INSTALL 记录。

恢复副本（含配置秘密，root-only，不复制到 Mac/Git）：

- 初次部署：`/root/greencloud-backups/gale-internal-20261005T024158Z-0jgva2e0`
- 薄 registry 修复：`/root/greencloud-backups/gale-internal-20261005T024524Z-ga3wow4l`

Mac 继续同一个 `com.snotes.greencloud-mirror-publish` LaunchAgent（300 秒），WorkingDirectory 改为 `/Users/s/Library/Application Support/S-notes/runtime/greencloud-publisher/gale-internal-cd816323912c7785`。沿用既有 `venvs/fdbb6eb97f90b6cbf50b62df196582f9f7ad51b2/bin/python`；bootstrap 后首轮 exit 0、Gale mirror 已生成。原 plist 在同一 runtime house 的 `gale-internal-recovery/publisher-before-cd816323912c7785.plist`。未建立另一套 publisher 或 venv。

本票未 build/install Mac App bundle，未要求 S 接受真实 proposal；Mac acceptance/receipt 改动已通过虚构源码集成验收。旧 Oracle runtime/mirror/outbox/service/connector 均未改。未做 Remote HTTP MCP、DNS、Caddy、OAuth、ChatGPT connector、final-progress 或第二票。

## 实际变更文件

本记录自身加下列 29 个实现、部署和测试文件，共 30 个文件：

- `deploy/greencloud/ai-access/reminder_actor.py`
- `deploy/greencloud/gale-internal/INSTALL.md`
- `deploy/greencloud/gale-internal/fixture_checks.py`
- `deploy/greencloud/gale-internal/install.py`
- `deploy/greencloud/gale-internal/install_mac_publisher.py`
- `deploy/greencloud/gale-internal/package.py`
- `deploy/greencloud/gale-internal/snotes-gale-internal-mcp`
- `deploy/greencloud/gale-internal/verify_internal.py`
- `deploy/greencloud/outbox/transport.py`
- `src/actor_mirror_publish.py`
- `src/cloud_outbox.py`
- `src/cloud_read_mcp.py`
- `src/greencloud_internal_mcp.py`
- `src/reminder_cloud.py`
- `src/services/actor_mirror_publish_service.py`
- `src/snotes/application/actor_mirror_service.py`
- `src/snotes/domain/greencloud.py`
- `src/snotes/storage/cloud_mirror_receiver.py`
- `src/snotes/transport/cloud_outbox_http.py`
- `src/snotes/transport/mcp/greencloud_internal_server.py`
- `src/snotes/transport/mcp/tools.py`
- `src/snotes/transport/reminder_http.py`
- `tests/greencloud_gale_fixture.py`
- `tests/test_gc35_cloud_metadata_discovery.py`
- `tests/test_gc35_step05_publisher.py`
- `tests/test_gc35_step10_runtime.py`
- `tests/test_gc35_step11_followup.py`
- `tests/test_gc35_step11_pull.py`
- `tests/test_greencloud_gale_internal.py`
