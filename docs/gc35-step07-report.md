# GreenCloud 3.5-B / Step 7 — HUMAN_GATE_READY

2026-10-01 更新：首轮真人验收除 remote mutation 延迟外均正常；下方记录本次共用写链路修复。真人 Gate 仍未 CLOSED，Step 8 未开始。

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


## 2026-10-01 — Remote mutation latency 修复

仅处理首轮真人反馈的所有 Remote Reminder mutation 约 4 秒才反映到 UI；不重跑 Step 7 全 Gate，不重审 snapshot purity，不进入 Step 8。

### 实测定位

在与原 candidate 相同的源码实现上，沿用 `https://reminder.ke-den.de` staging、GUI owner credential provider、真实 Qt controller / ReminderCenter，以临时 HOME 和虚构测试表/条目进行 monotonic 计时。原 candidate provenance 为 `0fd089d`，当前修复前 HEAD `4a63ea5` 仅比它增加交付报告，运行代码相同。

**不是 4 秒 timeout / QThread.wait / generation timer。** 共用链路为写前 schema snapshot GET → command POST → authoritative snapshot GET。旧 urllib transport 为每次请求重新建连；每段约 0.85–1.20 秒，其中重复 TLS 建连约 0.50–0.61 秒。三段串行累加解释接近 4 秒的体感；本轮实测随网络变化为约 2.66–3.51 秒，并非精确固定 4.000 秒。每次 Keychain 获取约 0.02–0.03 秒，响应到 UI 为毫秒级，thread cleanup 也不等待网络 timeout。

### 最小修复

- 同一次 worker operation 内复用 HTTPX 连接，保留每个请求的 fresh credential、4.0 秒正常网络超时、certifi TLS 校验、拒绝 redirect 和有界响应读取。
- operation 使用 thread-local；独立 command 自带嵌套 scope，GUI worker 外层 scope 覆盖 command 与 post-command refresh。结束或异常时关闭连接，不共享活跃 worker 的连接，不自动重试 POST。
- schema preflight、成功响应、authoritative refresh、generation/stale-response guard、pending 禁用、后台线程网络请求均保留；没有 optimistic fake write、跳过刷新或本地 Reminder DB fallback。
- `httpx==0.28.1` 已在现有环境中安装，现显式列为 GUI 直接依赖；未升级或重建 `.venv`。

### 代表性 timing（秒）

同环境、同路径，UI action → UI list updated：

| 操作 | 修复前精细基线 | 修复后 | 完整 App smoke 修复后 |
| --- | ---: | ---: | ---: |
| 首次 snapshot | 0.929 | 0.937 | 0.989 |
| create | 2.665 | 1.665 | 1.735 |
| pause | 2.983 | 1.638 | 1.687 |
| resume | — | — | 1.763 |
| complete | 2.762 | 1.814 | 1.713 |
| delete | 2.719 | 1.802 | 2.034 |

create 各阶段，以 UI action 为零点：

| 阶段 | 修复前 | 修复后 |
| --- | ---: | ---: |
| UI action emitted | 0.0000 | 0.0000 |
| worker 开始执行 command | 0.0005 | 0.0010 |
| 写前 schema GET 返回 | 0.8832 | 0.9010 |
| HTTP command request dispatch | 0.8833 | 0.9011 |
| HTTP command response received | 1.7493 | 1.2624 |
| command 调用返回 | 1.7493 | 1.2624 |
| post-command snapshot refresh started | 1.7494 | 1.2624 |
| snapshot HTTP response received | 2.6557 | 1.6605 |
| worker run finished | 2.6576 | 1.6612 |
| controller accepted response | 2.6577 | 1.6613 |
| UI model/list updated | 2.6654 | 1.6648 |
| worker/thread cleanup finished | 2.6804 | 1.6721 |

修复后独立 worker started 时间为 0.0004 秒，POST headers 实际发送时间为 0.9308 秒。原 worker 同时承载 command 与 refresh，因此“command 完成”和“worker run 完成”分别列出。修复前 command request dispatch 包含该请求重新获取凭据和建立 TLS；修复后 POST/refresh 的 trace 不再出现 connect/TLS。

每次 mutation 的 TCP/TLS handshake 从 3 次变为 1 次。独立只读连接复用验证：连续三次 snapshot 为 0.888 / 0.386 / 0.387 秒，只有第一次握手。保留三次权威请求，因此 mutation 仍包含实际网络往返，不定义绝对毫秒 SLA。

### Targeted verification

- `tests.test_gc35_step07_transport`：10/10 PASS。真实 HTTP/1.1 三请求复用一个连接；凭据仍获取三次；POST 响应掉线不重试；真实 read timeout 仍为 offline；redirect 不跟随、不转发凭据；scope 嵌套、关闭及线程隔离；schema mismatch 拒绝 POST；原 owner command 合约。
- `tests.test_gc35_step07_gui`：7/7 PASS。pending 写控件禁用、主线程心跳、authoritative refresh、offline、schema mismatch、stale response，以及 command/refresh scope 关闭后才发完成信号。
- 一组完整 App staging mutation smoke PASS：create → pause → resume → complete → delete，Cloud snapshot 与 Mac controller/list 状态为 active → paused → active → completed → absent。每项 mutation POST 一次，写后 snapshot 正常；虚构表和条目已清理。
- 完整 App smoke 1473 次 GUI 心跳，最大间隔 0.053 秒；正常 window.close 退出。测试 HOME、SQLite 数据和 cache 全部临时隔离，未读写正式 Reminder 数据。
- 额外 SSH/server-local 交叉检查在执行前被自动审批拒绝（服务器凭据访问与直接远程操作）；没有读取该凭据文件。状态一致性验证使用现有 GUI owner API 和权威 snapshot，没有改变权限边界。
- 临时 profiling 仅存在系统临时目录，不加入产品；本轮不保留长期 timing framework。

### 修改文件

- `requirements.txt`
- `src/snotes/transport/remote_reminder_client.py`
- `src/snotes/ui/remote_reminder_controller.py`
- `tests/test_gc35_step07_transport.py`
- `tests/test_gc35_step07_gui.py`
- 本报告 `docs/gc35-step07-report.md`

### Candidate 与最终边界

- 已用现有 `--step07-candidate` 构建入口更新 `dist-step07-candidate/S-notes.app`；`S_NOTES_RETIRE_REMINDER_HELPER=0`，只重建 candidate 内嵌副本，不安装或修改正式 helper。
- BUILD SUCCESS：arm64、bundle ID/隔离 marker、文件排除、certifi CA、Mach-O 路径与 ad-hoc codesign 检查通过。当前修复保持本地未提交，构建 provenance 为 HEAD `4a63ea5` + `SNotesSourceDirty=YES`。
- 实际包内确认包含 HTTPX transport 与 worker operation 逻辑；打包 candidate `--step07-smoke` PASS：online=true、36 次 GUI 心跳、credential_not_in_db_env=true、legacy_warning=true，正常退出。
- 正式 `dist/S-notes.app` 4531 个文件本轮前后指纹一致：`633751bd451a0328e0ea3c3ee2e4791210e6c3fb37e06c6ed6baec136cae6f94`（本轮相对路径+内容哈希算法）。正式 helper plist 前后一致：`ad0c3f8582d8eff9aebb448049c320bc65c8b5c2aee8eeee1f51464c6ad4acf3`，服务仍 loaded。
- 正式 Reminder DB 未读取或写入；未更新 staging 服务端代码、未改 helper、未 merge main、未 push。正式 helper 自然运行不等于数据库字节恒定，本次没有读取数据做指纹。
- `git diff --check` PASS；无长期 profiling instrumentation；临时探针已移除，必要 timing 仅保留于本报告。

```text
STEP07_WRITE_LATENCY_FIX=PASS
HUMAN_RETEST_READY=YES
STEP07_HUMAN_GATE=CLOSED_NO
STEP08_STARTED=NO
```

等待 S 使用更新后的 candidate 重新真人点一次新建 / 暂停 / 完成 / 删除；不自行关闭 Step 7。

## 2026-10-01 — 第二轮：packaged mutation 约 3 秒（TUN 环境）

S 真人复测更新后的 candidate 仍稳定约 3 秒。本轮由寓接手 Codex 中断的诊断；只处理 mutation 延迟，不重审 Step 7。

### 接手现场

- 分支 `codex/gc35-step07-remote-reminder-client`，HEAD `4a63ea5`；上一轮连接复用修复未提交、完整保留。
- 诊断残留：未跟踪的 `src/snotes/ui/_step07_mutation_probe.py`，以及 `reminder_remote_smoke.py` 中 3 行 probe 挂接。均为临时代码，已在本轮结束时移除。
- 无 candidate / 诊断包进程在运行；包内无 probe；staging 无 probe fixture 残留（只有 Step 6 有意保留的 FICTIONAL fixture）。

### 网络条件

本机长期使用 TUN（fake-IP：`reminder.ke-den.de` 解析为 `198.18.x`、TTL=1）。这是固定使用环境，不作为故障，也不作为修复对象。在该路径下实测：

- 新连接 TCP 本地瞬时完成，TLS 握手约 0.69–0.82 秒（经代理建立上游 + TLS）。
- 每个请求约 0.35–0.51 秒，基本等于一次往返；偶发单次请求抖动到约 1.2 秒。
- DNS 约 3 ms；Keychain `security` 约 10 ms；均可忽略。

### 1.7–1.9 秒 vs 3 秒

- 上一轮修复只在**单次** mutation 内复用连接，每次 mutation 结束即关闭。因此旧实现下每次 mutation 都必然重新握手：1 次 TLS + 3 次往返 ≈ 1.6–2.05 秒（只读同形测量 8 次）。自动测试的节奏快慢不改变这一点（30 秒空闲间隔与 0.4 秒间隔结果相同），冷/热节奏不能解释差异。
- GUI 层：model 更新 → 列表绘制 2–19 ms；无额外请求、无固定等待、无 thread cleanup 阻塞。`changed` 后续只做内存快照解析，与本地数据量无关。
- 未能在任何有计时的运行中复现 3 秒。可确认的剩余因素是 TUN 路径往返时间的波动：一次 mutation 约等于 5 次往返（握手约 2 + 请求 3），每次往返多 0.2 秒即接近 +1 秒；本轮也观测到单次请求跳到约 1.2 秒。原生 Finder 启动的正式交互未做插桩，以 S 复测为准。

### 修复（mutation 共用路径）

- `RemoteReminderClient` 在其生命周期内持有一个线程安全的 HTTPX 连接池（`KEEPALIVE_SECONDS=120`），snapshot、mutation、权威刷新以及下一次操作共用已握手的连接。经 TUN 实测，空闲 5/15/30/60/90/120/180 秒后连接均可复用且无需重新握手；Caddy 默认空闲超时为 5 分钟。
- 池中连接在空闲期间被对端关闭时，只读 GET 在新连接上重发一次；POST 从不重发，超时不重试。
- controller 在 shutdown 完成时关闭连接池。移除单次操作 scope（`operation()`）及 worker 包装。
- 保持不变：写前 schema GET → POST → authoritative snapshot 三个请求、fresh credential、4.0 秒超时、certifi TLS、拒绝 redirect、有界响应、generation guard、pending 禁用；无 optimistic 状态。

### Timing（UI action → 列表绘制，真人节奏：动作间隔 6 秒，resume 后空闲 150 秒）

| 操作 | 源码 GUI | 打包 candidate | 说明 |
| --- | ---: | ---: | --- |
| create | 1.309 | 2.060 | 打包时连接已热，第三个 GET 单次抖动 1.237 |
| pause | 1.345 | 1.256 | 热连接，0 次 TLS |
| resume | 1.349 | 1.308 | 热连接，0 次 TLS |
| complete | 2.054 | 2.035 | 空闲 150 秒后冷连接，1 次 TLS（0.69 / 0.76） |
| delete | 1.459 | 1.263 | 热连接，0 次 TLS |

结论：120 秒内的连续操作约 1.3 秒；空闲超过 120 秒后的第一次操作仍约 2.0 秒（一次必要的握手）。

### Verification

- Step 7 tests 33/33 PASS（含新增：跨操作复用同一连接、共享池只创建一次且 close 后释放、过期连接 GET 只重发一次、shutdown 释放连接池）；`-W error::ResourceWarning` PASS。
- 全量 575 tests：3 failures（`test_architecture_gate` 依赖方向、`test_package3_ui` 两项 offscreen 侧栏几何），在干净 HEAD `4a63ea5` 的临时 worktree 上同样失败，属于既有问题，与本轮改动无关。
- 打包 timing 使用临时诊断包与临时 HOME、虚构表，结束后 fixture 已清理；随后移除 probe，重建干净 candidate。干净包 PYZ 内确认包含 `KEEPALIVE_SECONDS` / `_exchange`，不含 `operation()` 与 probe 模块；`--step07-smoke` PASS（online=true，57 次心跳，snapshot_items=8）。
- 正式 `dist/S-notes.app` 无任何 2026-09-29 之后修改的文件；正式 helper plist 哈希仍为 `ad0c3f85…`，服务 loaded。正式 Reminder 数据未读写；未 merge、未 push、未进入 Step 8。

```text
STEP07_PACKAGED_MUTATION_LATENCY=PASS
HUMAN_RETEST_READY=YES
STEP07_HUMAN_GATE=CLOSED_NO
STEP08_STARTED=NO
```

## 2026-10-01 — Post-Vibe audit 与授权窄修复

独立只读 Post-Vibe audit（Step 6 + Step 7 Reminder 链）：

```text
P0=0
P1=3
P2=4
READY_TO_CLOSE_STEP07=NO
READY_FOR_STEP08=NO
```

S / Gale 授权窄修复：**P1-1、P1-2、P1-3、P2-2**。未授权、保持原状：
P2-1（staging ntfy/topic 硬编码）→ Step 8；P2-3（occurrence 重复逻辑）→ non-blocking，仅在 P1-1 自然触及处去重；P2-4（owner slimming）→ 3.5-B CLOSED 后。

### P1-1 — unsupported calendar year 不再毒死全系统

- 根因：日历范围错误只按异常文字识别；create 先落库再 materialize 并吞掉错误，留下"读不出来"的 active 行；读路径整表抛错；HTTP 不映射异常直接断连接，客户端误判 offline；每次写都要先拿完整 snapshot，连 delete 也被锁死；completion 先提交完成状态再算下一次。
- 修法：domain 新增结构化 `ScheduleRangeError(ValueError)`，取代异常文字前缀判断。`ReminderService` 的 occurrence 决策合并为一个 `_resolve_active_occurrence(read|plan|write)`，三种模式做同样的计算、只有 write 落库（顺带消掉 P1-1 所触及的那份重复逻辑）。create / update / resume 先 plan 预检，completion 在提交前先算出下一次；失败即整笔拒绝、DB 不变。已存在的坏行在 list/snapshot 中按 item 隔离为附加状态 `schedule_error`（"日程无法计算"），scheduler 跳过它继续处理其他 reminder，owner 仍可修复 schedule 或删除；对它 complete_occurrence 明确返回 400，不再静默成功。HTTP 未预期异常返回受控 `500 internal_error`。
- 修改：`domain/reminder_schedule.py`、`domain/reminders.py`、`application/reminder_service.py`、`transport/reminder_http.py`、`ui/reminder_center.py`（schedule_error 时禁用"完成本次"）；`tests/test_reminder_r1.py` 两个旧测试原本断言"创建成功、随后 list 抛错"（即 poison 契约本身），改为新契约。

### P1-2 — 架构闸门恢复，wire version 与 DB schema version 拆开

- 根因：Step 6 引入 1 个、Step 7 引入 6 个依赖方向违规；HTTP 兼容版本直接用 `REMINDER_SCHEMA_VERSION`。Step 7 报告以已含 Step 7 的 `4a63ea5` 为基线，误判为既有问题。
- 修法：新增 `domain/reminder_remote_contract.py`，持有 `REMINDER_WIRE_SCHEMA_VERSION = 4`（当前值不变）、`RemoteReminderError` 及 outcome 常量；server、client、UI、owner cache 全部改用它。`application/remote_mcp_reminder.py` 不再 import transport，HTTP client 由 composition root 注入（`client_factory` 必填）。payload 字段名 `schema_version` 不变，与现有 staging/cache 兼容。
- `scripts/check_architecture.py`：**PASS**（errors 0）；checker 及其测试零改动，无 ignore/allowlist。

### P1-3 — Reminder authority 显式化，缺失即 fail closed

- 根因：GUI 和 MCP 各自按"`S_NOTES_REMOTE_REMINDER_ENDPOINT` 是否存在"猜 authority，缺失就回落本地可写；`S_NOTES_RETIRE_REMINDER_HELPER` 是构建期输入，运行时却从环境变量读取，Finder 启动永远读不到。
- 修法：新增唯一 resolver `src/snotes/reminder_authority.py`（`local | remote`，无默认）。打包 App 从 Info.plist 读取 `SNotesReminderAuthority` / `SNotesRemoteReminderEndpoint` / `SNotesReminderHelperRetired`，环境变量与之冲突则拒绝启动；源码运行必须设置 `S_NOTES_REMINDER_AUTHORITY`。`build_application` / `build_local_mcp_service` / `compose_reminder_service` 必须显式接收 authority，不再读环境；GUI（`app.py` → `bootstrap.main`）、MCP stdio（`server.build_server`，含仓库 `scripts/run_snotes_mcp_stdio.sh`）、legacy helper（`reminder_helper.py`，authority 为 remote 时拒绝运行）共用同一 resolver。构建脚本按 profile 显式写入：正式 / d1 candidate = `local`，Step 7 candidate = `remote` + staging endpoint；spec 缺 authority 即构建失败；构建后校验 plist。`compose_reminder_service` 的 retired 检查改读 authority，`reminder_center` 中失效的运行时环境变量读取已删除。
- 影响：任何**从仓库源码启动**、且未设置 `S_NOTES_REMINDER_AUTHORITY` 的 MCP（含 runbook 中 Gale Secure MCP Tunnel 的仓库命令）现在会以 `reminder_authority_not_configured` 拒绝启动。本轮检查时没有该进程在运行；wrapper 有意不硬编码 `local`，以免成为第二个"正式 profile"定义点。正在运行的正式打包 App（旧代码）不受影响，下次正式构建时会显式写入 `local`。
- 开发脚本（package3/4 candidate 工具）改走 resolver；测试显式传入 `LOCAL_REMINDER_AUTHORITY` 或在子进程环境中设置 `local`。

### P2-2 — 区分确定失败 / 已确认但刷新失败 / 结果未知

- 根因：worker 用同一个 try 包住 command 与刷新；command 成功、refresh 失败时也报"操作失败"；POST 超时或断连被报成确定失败，MCP 断言 `modified_formal_reminder=false`。
- 修法：客户端为每个失败标记 outcome：连接前失败或 4xx = `failed`；POST 已发出后超时/断连、5xx、2xx 但响应不可读 = `unknown`；写操作从不重发，GET 的单次重连重试保持不变。controller 分两阶段处理：
  - 结果未知 → 状态 `outcome_unknown`："操作结果未知 · 请先刷新确认后再重试 · 只读"，在成功刷新前禁止写入；
  - 已确认但刷新失败 → `stale`："操作已被服务器确认 · 列表刷新失败，显示可能已过期 · 只读"，保留旧 snapshot，回调视为成功；
  - 4xx 业务拒绝 → 保持在线，提示"操作被拒绝：<code>"；
  - 只读命令（AI 操作记录）不使用 unknown。
- MCP：结果未知时返回 `status=failed`、`changed_formal_reminder=null`、`outcome_unknown=true`（`ReminderWriteReceipt` 增量扩展），并提示用同一 request_id 重试；actor request_id 幂等不变。

### 验证

- 新增 `tests/test_gc35_step07_repair.py` 22/22 PASS：2027 create/update/resume 原子拒绝；2026 年末 completion 无部分提交（owner 与 actor 均如此）；预置坏行时 snapshot（owner/actor）可用、scheduler 仍发送健康 reminder、owner 可修复与删除、complete 明确拒绝；真实 socket 下 snapshot 异常返回受控 500；DB schema 版本变化不影响 wire 版本，wire+1 仍为 mismatch；architecture PASS；authority 各 profile、Info.plist 冲突、candidate=remote staging；GUI/MCP/仓库 MCP 脚本/legacy helper 缺 authority 一律 exit 2 且未创建任何 `s_notes.db`；helper 在 remote 下拒绝运行；POST ok/400/500/超时/断连/未连接的 outcome 分类且每个写只发一次；controller stale/unknown/rejected/只读命令；MCP unknown receipt；actor request_id 幂等。
- Step 6 + Step 7 + Reminder 子系统回归 113/113 PASS（临时 HOME、虚构 DB）。
- 全库 597 tests：2 failures，即 `test_package3_ui` 的 `test_frozen_r1_geometry_and_approved_two_level_classification`（307≠360）与 `test_reminder_sidebar_table_title_rename_and_delete_stay_synced`（reminder table item not found）。与干净 `4a63ea5` 完全一致；原基线中的 architecture gate 失败已消除。**更正：这两项在 `fdbb6eb`（Step 7 之前）均 PASS，属 Step 7 引入的回归，并非既有问题。** 后者根因：`MainWindow.create_reminder()` 改走 `center.create_table()` 后，local 分支丢失了 `_sync_sidebar_reminder_filters()`，本地模式新建的表不会出现在侧栏。不在本次授权范围内，未修复，交由 re-audit / S 裁定。

### Candidate 与边界

- 从干净提交 `ecd8843` 重建 `dist-step07-candidate/S-notes.app`（`--step07-candidate`，`S_NOTES_RETIRE_REMINDER_HELPER=0`）：BUILD SUCCESS，`Tracked Source Dirty: NO`，arm64，ad-hoc codesign 通过。Info.plist：`SNotesReminderAuthority=remote`、`SNotesRemoteReminderEndpoint=https://reminder.ke-den.de`、`SNotesReminderHelperRetired=false`（构建脚本已校验）。
- Packaged runtime 读取 plist：candidate 内嵌 legacy helper（临时 HOME / 数据根）以 `Reminder authority is remote` exit 2；环境变量冲突以 `reminder_authority_conflict` exit 2；未创建任何 DB。
- 打包 candidate `--step07-smoke` PASS（现有 staging）：online=true、50 次心跳、snapshot_items=8、credential_not_in_db_env=true、legacy_warning=true。
- 打包 candidate actor MCP stdio smoke（yan、claude-code，只读）PASS：`list_reminder_labels` / `list_reminder_tables` 均 status=ok。
- Staging 服务端 runtime **未更新**：服务端修复已在本地受控时钟下验证，staging 时钟无法复现 2027 场景；新客户端与现有 staging 兼容（wire version 仍为 4）。未做 DB 迁移，未改 ntfy、helper 或 authority。
- 正式 `dist/S-notes.app` 指纹前后一致（`5bd0df24…`，本轮相对路径 + 内容哈希算法）；正式 helper plist 一致（`ad0c3f85…`），仍为 loaded。正式 Reminder DB 未读取、未写入。未 merge、未 push、未进入 Step 8。
- 范围外观察（未处理）：`com.snotes.greencloud-mirror-publish` 以本仓库目录为 WorkingDirectory，而本目录当前 checkout 的是施工分支，因此生产 publisher 实际运行的是施工分支代码。它只依赖本轮做了增量修改的 `domain.reminders`，当前无行为影响。

真人复核建议只看 P2-2 新提示语：candidate 打开 Reminder 后断网，执行一次暂停 / 完成，应看到"操作结果未知 · 请先刷新确认后再重试 · 只读"（若请求未发出则为离线只读）；恢复网络点"刷新"回到"已连接"。其余已验收行为未改变，无需重测。

```text
P1_1_REPAIR=DONE
P1_2_REPAIR=DONE
P1_3_REPAIR=DONE
P2_2_REPAIR=DONE
ARCHITECTURE_CHECK=PASS
REPAIR_PACKAGE_READY_FOR_REAUDIT=YES
STEP07_CLOSED=NO
STEP08_STARTED=NO
```
