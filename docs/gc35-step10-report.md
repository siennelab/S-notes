# GreenCloud 3.5-D / Step 10 — Notes outbox + proposals

**STEP10=PASS。10A / 10B / 10C 完成并已部署；Step 11 未开始，3.5-D 尚待 Step 11 封口。**

- Branch：`codex/gc35-step10-greencloud-outbox-proposal`。
- 已验证并部署的 implementation commit：`8e352962600029799655768e8dbba2f78667b1ac`。
- 本报告由随后独立的 documentation commit 收录；最终报告提交可用该 branch 的 HEAD 定位。
- 起点与保留的 main：`bc35692507ae557a42dd0e83d212bb9c138f75cd`。
- 保留的 origin/main：`90cb115f3866759c32987bc636c81edd322ef921`。
- `PUSH=NO`，未 merge main，未改 Mac Notes / GreenCloud Reminder authority。

## 10A

复用 `gale_submission_outbox.py`，没有第二套 outbox。默认初始化仍为 Gale schema v2；只有显式 `generalized=True` 才创建或事务重建 v3。临时 v2 数据的 queued / delivered / acknowledged 状态、时间、receipt ID 与 proposal metadata 均保持。Oracle 不执行迁移。

v3 承载 operation type、note ID、expected_version、JSON payload、四个既有/新增 actor，以及 accepted / rejected / conflict 终态。operation type 不使用三种操作的 schema CHECK；Cloud allowlist 在 application / HTTP / MCP 控制。

Cloud request_id 与本地 contract 一致，允许非空、无空白、最长 128 字符，包括 Unicode。Gale 的旧 ASCII idempotency key contract 与 v2 export/ACK wire payload 保持。Cloud 幂等按 actor 隔离；`BEGIN IMMEDIATE` 覆盖幂等检查 → non-terminal 计数 → 插入。每 actor 上限 50，acknowledged 仍占名额，终态释放名额。terminal receipt 与终态后的 ACK 重试均幂等。

## 10B / GreenCloud 实际部署

- `/srv/snotes/data/outbox.db`：`snotes:snotes 0600`，schema v3，实际 `journal_mode=wal`；沿用 `snotes` UID/GID 2006 与 data directory 0700。
- `snotes-notes-outbox.service`：enabled / active，只监听 `127.0.0.1:8767`，以 `snotes` 运行，复用 systemd 的既有 `reminder-api.json` credential source，仅认可 claude-code / clawbot 两个 token。未复制或轮换凭据。
- 只替换发生变化的 runtime 文件，并补上纯 application exact-edit helper；复用原 venv，无安装/升级依赖，无 Cloud canonicalizer/Node。
- `/srv/snotes/outbox-deploy/`：backup、transport、Step 10 verifier、安装/打包脚本、INSTALL 与 deployment record。
- 原 Reminder backup service 仅新增 `notes-outbox.conf` drop-in：既有备份后调用 Notes SQLite-safe backup。实际 `ExecStartPost` / `ReadWritePaths` 已接入；Notes backup smoke PASS。目录为 `/srv/snotes/backups/notes-outbox`，0700，文件 0600，latest + 14 snapshots。
- 原 mirror forced-command entry 增加三个精确子命令：`notes-outbox-export`、`notes-outbox-acknowledge`、`notes-outbox-receipt`，经固定参数 sudo 以 snotes 操作固定 DB。空命令仍走原 receiver；未新增 SSH key/account 或公网 route。
- `/srv/snotes/INSTALL.md` 已追加 Step 10；source manifest / deployment record 保存实际文件 hashes、implementation commit 与验证结果。

小范围回滚材料 KEEP，供 S/G 验收期间恢复文件；不恢复 DB 内容：

- `/root/greencloud-backups/step10-20261004T065916Z-45x1t_h5`：24 个实际安装目标的原文件/受影响路径 metadata，包含不存在的新增目标记录。
- `/root/greencloud-backups/step10-record-20261004T070417Z-8i99u80v`：封口时两份 deployment metadata 的原文件。

Reminder API PID 仍为 2374。Reminder runtime aggregate SHA-256 前后均为 `7ae91bb99f4578c88b3677067dd0e40500c8c30c5060f6f2a7f9bf03d7b12fda`，五份原 service/timer 文件 hashes 不变；本步仅添加 Notes backup drop-in。

## 10C

实际 actor stdio MCP 已列出原三个 read tools，加 `create_note` / `append_note` / `edit_note` / `get_operation_status`。UID 2001 固定 claude-code，UID 2002 固定 clawbot；读取各自 Step 9 credential，校验 inode owner/0400/regular file/无 symlink。

MCP 和 localhost 原始 POST 都调用同一份 `CloudProposalService` application precheck。客户端不能自报 actor、visibility 或 targets。create 使用本地核心参数及分类/标签参数；append 允许 full/redacted，edit 仅 full Markdown；note 必须在自身 mirror，expected_version 必须匹配。edit 复用既定 exact unique / non-overlap / all-or-nothing 检查。新增明显非法 HTML/size 拒绝，代码/转义字面文本保持。Cloud payload 不 canonicalize，正式接受留给 Mac。

幂等查询先于 mirror / version / queue-limit 检查。重复 request 返回既有状态，不再入队。status 仅返回当前认证 actor 的 request；prepend / metadata / delete 仍拒绝。

## 验证

共 **50 个直接相关、互不重复的本地 tests PASS**，全部使用既有 `.venv`、临时 HOME 和虚构数据库：

- Step 10 targeted：20/20（outbox 6、deploy 3、Cloud proposal/HTTP/MCP 8、runtime credential/WAL 3）。
- Gale compatibility：24/24（remote staging 6、security 9、冻结 production contract 1、pull/parser/receipt 8）。冻结生产测试与旧提交输出比较，默认 schema / proposal payload 保持。
- Cloud read 直接兼容：6/6。
- Python parse、shell syntax、diff whitespace PASS。

Gale 的 Qt 接受用例在 sandbox / offscreen 中遭遇既有 QtWebEngine 启动/GPU失败；同一组 8 用例改用临时 HOME、正常 Cocoa 与 `--disable-gpu` 后 PASS，正常关闭。未修 Qt、未跑全项目 suite 或整套 Step 9 isolation。

GreenCloud 实际 runuser UID smoke PASS：

```text
STEP10_PRODUCTION_ACTOR=claude-code:PASS
STEP10_PRODUCTION_ACTOR=clawbot:PASS
STEP10_FIXTURE_BASELINE=claude-code:PASS
STEP10_FIXTURE_BASELINE=clawbot:PASS
STEP10_FIXTURE_CROSS_STATUS=claude-code:PASS
STEP10_FIXTURE_CROSS_STATUS=clawbot:PASS
STEP10_FIXTURE_QUEUE_ORDER=claude-code:PASS
STEP10_OUTBOX_FILE_DENIAL=16:PASS  # each UID; 32 read/write denial checks total
STEP10_PRODUCTION_SAFE_SMOKE=PASS
STEP10_FIXTIONAL_ACCEPTANCE=PASS
```

生产探测包含真实 stdio launch / 七工具 discovery / 缺失 request status、HTTP auth/allowlist/spoof 拒绝、窄 SSH ACK/receipt 的不存在 ID，以及 DB/sidecar/backup 打开拒绝。完整 create、full/redacted append、full edit、duplicate、own/cross status、stale、redacted edit、hidden/other-actor/Gale-only note、原始 HTTP 绕过尝试和 50 条上限在临时 snotes localhost service 上完成，使用同一部署代码与真实 actor 正常认证。没有 Mac 接收器或在线依赖。

临时镜像内容 hashes 保持；临时 service、fixture 和施工传输目录清理。生产 outbox 最终 **0 proposals**，未留下测试投稿。凭据没有传到 Mac、打印或提交。

## Oracle / Step 9 / 后续

**Oracle production 完全未动**：没有 Oracle SSH、runtime deploy 或 production DB 打开/迁移。升级 Oracle schema 的时机留给独立评估，本步无需升级。

`deploy/greencloud/ai-access/verify_actor_access.py` 最终恢复为 baseline，Git 无 diff，GreenCloud 文件未替换。上一轮把历史三工具验收改为七工具会改变 CLOSED Step 9 语义，因此七工具和 outbox 验收全部归属 Step 10 自己的 `verify_outbox.py` / tests。

以后增加 prepend / update_note_metadata，需要修改 Cloud application allowlist/precheck 分派、HTTP operation allowlist/payload 分派、MCP schema/dispatch 与相关 tests；复用当前 payload / version / 幂等 / lifecycle，无需再次重建 outbox。Step 11 建立的 Mac accept / AI Center 展示分派也须增加对应操作；metadata 保持本地既定字段 allowlist，不开放 visibility / targets。

范围外：无新 blocker；实际 Claude Code/OpenClaw client 安装与 discovery 仍按既有路线图后验。未处理 egress、prompt/gateway、Reminder owner slimming、sidebar polish 或其他 P2。

## 实际仓库改动文件

```text
src/services/gale_submission_outbox.py
src/cloud_outbox.py
src/cloud_read_mcp.py
src/snotes/application/cloud_proposal_service.py
src/snotes/transport/cloud_outbox_http.py
src/snotes/transport/cloud_proposal_client.py
src/snotes/transport/mcp/cloud_read_server.py
deploy/greencloud/snotes-mirror-ssh-entry
deploy/greencloud/outbox/INSTALL.md
deploy/greencloud/outbox/backup.py
deploy/greencloud/outbox/install.py
deploy/greencloud/outbox/package.py
deploy/greencloud/outbox/transport.py
deploy/greencloud/outbox/verify_outbox.py
deploy/greencloud/outbox/notes-outbox.conf
deploy/greencloud/outbox/snotes-notes-outbox-backup
deploy/greencloud/outbox/snotes-notes-outbox-transport
deploy/greencloud/outbox/snotes-notes-outbox.service
deploy/greencloud/outbox/snotes-notes-outbox.sudoers
tests/test_gc35_step10_outbox.py
tests/test_gc35_step10_deploy.py
tests/test_gc35_step10_cloud_proposals.py
tests/test_gc35_step10_runtime.py
docs/gc35-step10-report.md
```

封口 Git 状态：上述实现与报告提交于当前 task branch，worktree clean；main 与 origin/main 保留开工值，未 push、未 merge。到此停手。
