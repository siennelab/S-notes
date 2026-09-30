# GreenCloud 3.5-B / Step 7 — HUMAN_GATE_READY

2026-09-30. 真人验收尚未进行，Step 7 不标记 CLOSED，Step 8 未开始。

## 现场与交付

沿用 `codex/gc35-step07-remote-reminder-client`。接管时已有在途未提交实现；全部保留并补齐，未 reset、stash、clean 或重建分支。
实现提交：`0fd089d767a7ca4c46c6cc450e5d6b6e13154010`。candidate 从该提交、tracked source clean 构建。
`main` 与 `origin/main` 保持 `fdbb6eb`；没有 merge 或 push。

Candidate：`/Users/s/Documents/项目/S-notes/dist-step07-candidate/S-notes.app`。
Bundle ID：`de.ke-den.snotes.step07candidate`。启动自动配置独立 HOME 和数据目录：
`~/Library/Application Support/S-notes-Step07/home` 与 `~/Library/Application Support/S-notes-Step07`，固定连接 `https://reminder.ke-den.de` staging。
未替换正式 `dist/S-notes.app`。helper 退役 gate 保持关闭。

## 实现

- 保留接管前已建立的 transport/controller、凭据/cache、MCP、candidate 接线，并完成集成。
- `RemoteReminderClient` 使用现有 snapshot/commands API，4 秒超时，区分 offline/auth/schema_mismatch/server；写前检查 schema，不自报 actor。
- Reminder UI 保留原有交互，controller 通过 Qt worker 执行网络请求。loading/pending 和不可写状态禁用相关写控件；generation 丢弃旧响应，关闭窗口等待 worker 正常退出。
- offline 和高 schema_version 仅展示 GUI 私有的 last compatible snapshot 及时间；无本地 Reminder DB fallback、无离线写队列。
- GUI owner Keychain 仅由 GUI worker 读取；MCP 使用独立 `yan` / `claude-code` credential provider，不接 owner credential 或 owner cache。
- 无可用 table 时由 owner worker 调用幂等 `ensure_default_table` 后重取 snapshot。业务判断复用原 `default_table()`；AI actor 被拒绝。
- snapshot occurrence 读取与 materialize 分离：GET 不创建/推进 occurrence；create、mutation、completion、scheduler 保留原业务计算。settings GET 仅计算 effective 展示值，初始化/规范化持久化由显式生命周期负责。
- ntfy endpoint 只读；旧 fallback/helper 退役准备有 gate。本步仅提示 legacy scheduler，不停止、卸载或更改 helper。

## 自动验收

- **97 项** Step 7 / Step 6 API、runtime、Reminder 3.5.0/R1 targeted tests PASS。
- **3 项**邻近 MCP protocol/actor integration tests PASS（独立 Cocoa 进程）。
- **25 项**真实 staging acceptance PASS：owner CRUD、Cloud → Mac / Mac → Cloud、两个 actor 允许的生命周期操作、owner-only command 对 actor 拒绝。新增虚构测试对象已清理。
- Snapshot 连续 GET：完整相关表状态、settings values、SQL trace 无写入；缺 current/next occurrence 与缺 settings 不触发写入。已有 occurrence 展示、recurrence_mode 推进、下一 occurrence 的明确写生命周期、scheduler/multi-notify/dedupe 回归 PASS。
- GUI 针对性检查：延迟期间心跳持续、离线只读及写控件禁用、高 schema 拒绝解析和写入、stale response 防护、空表 owner worker ensure、本地 Reminder DB 不变、凭据隔离 PASS。
- source 完整 GUI smoke PASS；**打包 candidate GUI smoke PASS**：online=true、49 个心跳、credential_not_in_db_env=true、legacy_warning=true、读取 4 个 staging items。
- **打包 candidate 两个 actor 的 stdio MCP smoke PASS**；临时 HOME/虚构本地 DB，未创建 owner cache。
- candidate 构建检查、数据/凭据文件排除、arm64、ad-hoc codesign 校验 PASS；`git diff --check`、构建脚本语法检查 PASS。
- QtWebEngine 广泛旧测试与 offscreen 混跑曾出现 GPU 进程崩溃；相关 3 项 MCP 测试已改在独立正常 Cocoa 进程验证通过。未扩大全项目回归。

## Staging 与正式边界

仅把两个批准的窄补丁文件更新到现有 staging runtime：
`src/snotes/application/reminder_service.py`、`src/snotes/transport/reminder_http.py`。
备份：`/root/greencloud-backups/step07-read-purity-20260930T135317Z`。
API 已重启并通过联调；既有 scheduler/backup timers 保持运行。没有 DB 迁移、第二套服务或正式 authority 切换；不重封 Step 6。

正式 App 4531 个文件指纹与开工一致：`db5cd497a8004ac56db9944578fb6d9208e70eb770be158e77e827c3a66ad033`。
正式 helper plist 指纹一致：`02faf536c1a6dd296bd1c56b0cf445b59b6651e3ab76b22f27bb0219fc436763`，launchctl 确认仍 loaded。
正式 Reminder DB 未读取、未迁移、未由本任务写入；正式 helper 自身持续运行，不声称其自然运行期间 DB 字节恒定。

## 相邻发现

`default_label()` 在人为破坏关联/FK 数据时仍可能自愈写入；正常有效数据不可达，按裁定保留，本步不做 corrupted-DB recovery。无新增范围外 blocker。

## 状态

```text
STEP07_CODE_READY=YES
STEP07_AUTOMATED_GATE=PASS
STEP07_CANDIDATE_READY=YES
SNAPSHOT_READ_PURE=YES
FORMAL_APP_UNCHANGED=YES
FORMAL_REMINDER_DATA_UNCHANGED=YES
FORMAL_HELPER_UNCHANGED=YES
STEP08_STARTED=NO
HUMAN_GATE_READY=YES
```

S 下一步只需使用 candidate 验收新建、完成、暂停、刷新和断网只读。验收前不切正式 App，不动 helper，不进入 Step 8。

## 实际修改文件

以下为实现提交相对 `fdbb6eb` 的完整清单；另加本报告 `docs/gc35-step07-report.md`。

- `.gitignore`
- `app.py`
- `packaging/S-notes.spec`
- `scripts/build_macos_app.sh`
- `scripts/gc35_step07_live_acceptance.py`
- `scripts/gc35_step07_provision.py`
- `scripts/gc35_step07_update_staging.py`
- `src/snotes/application/local_mcp_service.py`
- `src/snotes/application/reminder_service.py`
- `src/snotes/application/remote_mcp_reminder.py`
- `src/snotes/bootstrap.py`
- `src/snotes/step07_candidate.py`
- `src/snotes/transport/reminder_http.py`
- `src/snotes/transport/remote_reminder_client.py`
- `src/snotes/ui/ai_center_dialog.py`
- `src/snotes/ui/main_window.py`
- `src/snotes/ui/reminder_center.py`
- `src/snotes/ui/reminder_legacy_check.py`
- `src/snotes/ui/reminder_owner.py`
- `src/snotes/ui/reminder_remote_smoke.py`
- `src/snotes/ui/remote_reminder_controller.py`
- `src/snotes/ui/sidebar.py`
- `tests/test_gc35_step06_runtime.py`
- `tests/test_gc35_step07_gui.py`
- `tests/test_gc35_step07_mcp.py`
- `tests/test_gc35_step07_owner.py`
- `tests/test_gc35_step07_snapshot_purity.py`
- `tests/test_gc35_step07_transport.py`
- `tests/test_reminder_r1.py`
