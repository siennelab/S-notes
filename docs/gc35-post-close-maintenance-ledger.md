# S-notes GreenCloud 3.5 maintenance ledger

2026-10-04。基线 `04ec7a364af7ed3d3970485453a676e2adc712bb`；开工时 `main / origin/main / HEAD` 相同、工作区 clean。本账只关闭竣工后的记录与施工残留，不重开 GreenCloud 3.5 或四个本地 Agent MCP 接线。

## Deployment record

**CLOSED**。清理前 `/srv/snotes/INSTALL.md`、`/srv/snotes/runtime/step10-source-manifest.json`、`/srv/snotes/outbox-deploy/deployment-record.json` 与实际 Step 10 文件 hashes 全部一致。manifest 的 31 个 source/assets hashes 全部匹配 Git 已记录 implementation `8e352962600029799655768e8dbba2f78667b1ac`，不把后续 Mac-only commit 冒记为远端部署。

本轮删除 rollback 后，仅移除 deployment record 的 `rollback_directory / record_rollback_directory` 两个失效指针，在实际 INSTALL 追加竣工清理与现行恢复来源，并更新 record 内 INSTALL 的 hash。source manifest、runtime 源码、schema、权限与凭据不变。

## Rollback and construction leftovers

| 目标 | 状态 | 判定证据 |
| --- | --- | --- |
| `/root/greencloud-backups/step10-20261004T065916Z-45x1t_h5` | DELETE | 仅四份旧文件与路径 metadata；三个旧源码/wrapper hashes 与 Git `bc35692` 完全相同，旧 INSTALL 完整保留在当前 INSTALL 前缀；没有独有配置、数据或恢复材料。 |
| `/root/greencloud-backups/step10-record-20261004T070417Z-8i99u80v` | DELETE | 仅封口前 manifest/record；正式记录已保留原 hashes 与 baseline，补充 implementation/verification 并修正 manifest hash，旧副本无独有恢复价值。 |
| `~/Library/Application Support/S-notes/backups/gc35-step11-helper-084f67cf/` | DELETE | 仅旧 helper 与两份 plist 的 hashes；helper 内 50 个项目模块全部匹配现有 Git 历史源码，无私有配置或数据；两份 plist 原件仍在，正式 installed helper 未变。 |
| `/srv/snotes/data/step11-fixture-084f67cf.json` | DELETE | schema 3，仅四条 `gc35-step11-human-084f67cf-*` 虚构 create/append/edit/conflict 行，终态与 `HUMAN_*` payload 已逐条核对；无真实 Note/Reminder 内容。 |
| `/var/lib/snotes-gale-r1b-production/step11-fixture-084f67cf.json` | DELETE | schema 2，仅一条 `gc35-step11-human-084f67cf-gale` 已 ACK 虚构投稿，标题/正文/category/tag 均为本轮验收材料。 |

两份 fixture 均无当前 S-notes runtime、CLI/wrapper 或 systemd 入口引用；它们只恢复已关闭的虚构验收行，正式运行及数据恢复不依赖它们。五个目标均已删除并确认不存在；没有为删除另建 rollback。

## Old audit reachability

候选来源仅为 [2026-09-24 架构健康审计](S-notes-架构健康审计-20260924.md)。结论按当前源码、packaging、CLI、Qt 与现场入口重新判定；本轮未删除业务代码。

| 历史候选或其当前残留 | 状态 | 当前证据 |
| --- | --- | --- |
| `accept_pending_append` | CLOSED | `7b77653` 已移除，当前 `src/` 无该符号；接受走 `ai_receipt_inbox_service.py:769` → `accept_pending_modification`。 |
| `raw_candidate is None` 旧分支 | CLOSED | 旧分支已消失；`local_mcp_write_service.py:239,246` 的当前局部变量仍用于 no-change 判断。 |
| 内部 `raw_candidate_markdown` kwarg | DELETE_CANDIDATE | 仅 caller `local_mcp_write_service.py:312`、port `ports.py:435`、repository `ai_mcp_operations.py:176` 三处；方法体不读取，不进入 MCP/Cloud wire contract，也无动态/Qt 消费。后续只候选移除未消费参数。 |
| `src/database.py` | KEEP_UNTIL_TRIGGERED | 仓库 legacy CLI/import 兼容链仍经 `ai_access_service.py:24` 加载；未证明可退役。 |
| `src/services/ai_access_service.py` | KEEP_UNTIL_TRIGGERED | 仓库 `src.gale_publish`/`gale_remote_mcp` 兼容链仍有依赖，Oracle 独立源码中旧 mirror export 还保留函数内 lazy import；不能判死。 |
| `src/redaction.py` | KEEP_UNTIL_TRIGGERED | legacy `ai_access_service.py:36,1127,1213` 的导入与脱敏调用仍在，兼容面未退役。 |
| `src/tag_manager.py` | KEEP_UNTIL_TRIGGERED | legacy `database.py:28`、`ai_access_service.py:43,1117` 的导入与标签解析仍在，兼容面未退役。 |
| `ui/markdown_demo.py`、`markdown_smoke.py`、`markdown_list_smoke.py`、`submission_smoke.py` | LIVE | 正式 `app.py:8-19` / PyInstaller 入口仍接受 `--step11-smoke / --markdown-demo / --markdown-smoke`，嵌套 demo/smoke 与 Qt `QTimer` 可达；正常启动不触发。 |
| Reminder dead local path | CLOSED | 保持既定 `NONE_FOUND / CLOSED`，未重开扫描或清理。 |

现场边界：Oracle active unit 为 `snotes-gale-mcp-oidc-staging.service`，在 `/opt/snotes-gale-mcp-oidc-staging` 执行 `src.gale_remote_mcp serve`；其独立 mirror 源码已移除 `ai_access_service` 顶层依赖，activator 仍用 `src.gale_publish remote-activate / remote-rollback`。不能用仓库同名文件的 import 链冒充现场 LIVE 证据。本机 GreenCloud publisher 与 Gale helper 使用已核对的既有入口，没有更改接线。

## Maintenance decisions

| 项目 | 状态 | 现行结论 |
| --- | --- | --- |
| `ai_call_logs` retention | DEFERRED | `SAFE_TO_APPLY_RETENTION=YES`；`RETENTION_IMPLEMENTED=NO`；不是 blocker，本轮不实施。 |
| Reminder owner slimming | DEFERRED | 不因文件大主动拆。 |
| Reminder sidebar max-width 约 17px | DEFERRED | UI polish only。 |
| actor mapping duplication | DEFERRED | 未来真正修改相关 owner 时顺手补一致性测试；不专门建设全局配置系统。 |
| Cloud read open-time `target_actor` recheck | NO_CONSTRUCTION_REQUIRED | 本轮未改变现实读取、身份或镜像边界，保持既定结论。 |
| GreenCloud `sudo: unable to resolve host optimal-living ...` | DEFERRED | SYSTEM_HYGIENE，本轮不修 hostname/hosts。 |

## Closure evidence

五个 DELETE 目标均不存在。GreenCloud Notes outbox、Reminder API、scheduler/backup timers 全部 active；scheduler/backup 最近 Result=success。真实 claude UID 下，既有 stdio `search_notes` 与 actor wrapper `list_reminder_tables` 各一次成功；仅输出成功状态，未输出真实正文。Oracle Gale service 仍 active。

没有直接打开真实数据库、制造 proposal/Reminder、修改真实内容、清空应用回收站或更改 authority。仅检查本轮列出的路径与相关入口；未运行全量测试。仓库改动仅本账，远端改动仅指定残留与实际 INSTALL/deployment record。`NEW_OUT_OF_SCOPE_FINDINGS=NONE`。

`CLEANUP_LEDGER=CLOSED`。`NEXT=Cloud prepend + update_note_metadata`，下一张票再施工；delete 仍不做。
