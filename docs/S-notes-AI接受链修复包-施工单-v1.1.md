# S-notes｜AI 接受链修复包 施工单 v1.0

- 依据：`S-notes-架构健康审计-20260924.md`（寓），以及 S 与 GPT 的范围裁定（2026-09-24）
- 基线：main @ `4746088968e715683b5a85cbc22311e57138fba3`
- 施工：Codex；审计：寓；验收与拍板：S
- 状态：**Step 4 暂停**，本修复包封口后回到 3.5-A

---

## 0. 一句话目标

把「Notes 的 AI 提案被接受」这条链路收回到正确的负责人（owner）手里，让架构闸门转绿，让全量测试重新可信。然后停手。

本包**不是**重构周，也**不是**全项目清理。凡是本单没写的，一律不做。

---

## 1. 施工纪律

1. 遵守仓库里的 `AGENTS.md`：
   - 只用 `.venv/bin/python`。
   - 测试时 HOME 和数据库都用临时的，不碰正式数据库。
   - 开工前检查分支和 git status。
2. 在新分支上施工。封口前不合并到 main。
3. **不改数据库 schema**。如果某项确实非改不可，停下来报告（见 §6）。
4. **不削弱闸门**：
   - 不改 `scripts/check_architecture.py` 的规则。
   - 不加白名单。
   - 不把违规代码挪到闸门扫不到的位置（比如 `src/snotes/` 顶层文件、`migration/`、`src/` 下的平铺模块）来「让它通过」。
5. **不用 `skip` 或 `expectedFailure`，也不删测试**来换全绿。
6. 已经验收过的外部行为保持不变。包括：MCP 工具的名称和响应字段、AI Center 用户能看到的操作流程、确认策略。唯一允许的例外，是 §2-A 修掉的那个 bug。
7. 施工中发现本单范围外的问题，**只记录，不顺手修**，统一写进收口报告的「新发现债务」一节。

---

## 2. 施工项 A–I

下面引用的行号是审计时的起点证据，以施工时的源码为准。

### A. Notes AI 操作结果：一个契约，一个写入负责人

**问题**：`ai_operations.result_json` 在 Notes 这条链上有两个模块在写，而且字段各不相同。

- `ai_receipts._store_operation`（约 415-425 行）写的是 `changed` 和 `pending_confirmation`。
- `ai_mcp_operations._result`（约 940-958 行）写的是 `modified_formal_note` 和 `entered_confirmation`。
- transport 只读后一套。

**现有后果**：人工接受 AI 的 create 提案之后，AI 调用 `get_operation_status` 拿到的是 `changed_formal_note=false`。这与事实不符。

**要求**：

- 定义唯一一个带类型的操作结果对象和它的序列化方式，放在 domain 或 application 层。
- Notes AI 接受链上，这份结果只由一个模块负责写入 `ai_operations`。
- 修掉上面那个 bug，并补一个测试：「人工接受 create 之后，`get_operation_status` 报告 `changed_formal_note=true`」。

**范围钉死**：

- Reminder 自己写入 `ai_operations` 的方式**不在本包**，不改它的写入架构。
- 硬约束是：Reminder 的新旧记录、已经存在的 Notes 旧记录，改完后都必须照样能被正确读出来。要有测试覆盖这一点。

### B. canonicalizer 从 UI、application、storage 三层的依赖里拔出来

**问题**：application 和 storage 都 import 了 `src.snotes.ui.markdown_canonicalizer`。闸门因此报了 7+1 条 error。

**要求**：

- 在 application 层定义规范化的接口（port），以及它抛出的错误类型，归 application 或 domain 所有。
- 基于 Milkdown 的具体实现留在适配层，由 `bootstrap.py` 注入给需要它的 application 服务。
- `note_canonical_candidate` 变成纯逻辑：规范化能力只从参数传进来，不再默认去 import ui。
- 测试改为注入一个假的实现（fake），不再直接 patch ui 层的具体类 `MarkdownCanonicalizer`。

### C. 规范化挪出数据库写事务

**问题**：`accept_pending_modification` 在 `connections.write()`（也就是 `BEGIN IMMEDIATE`）里面跑 Milkdown 规范化。这意味着一个 Qt 事件循环在持有 SQLite 写锁的时候运行。

**要求**：

- 规范化一律在事务**之外**完成。
- 事务**之内**只做比对：note 的 `revision` / `updated_at`，以及收件箱条目的指纹。比对不通过就是冲突，保持现有的冲突语义。
- 施工完成后，任何 `connections.write()` 块里都不能出现 Qt 或 Milkdown 调用。

### D. 接受提案的编排回到 application

**要求**：下面这些步骤由 application 里的一个用例负责，可以是 `AiReceiptInboxService` 本身，也可以是它旁边新建的一个 acceptance service：

- 读取快照
- 校验 payload
- 权限判断（交给 G 里那个唯一负责人）
- 重新应用编辑
- 规范化
- 判定结果

编辑 payload 的校验目前有 3 份，收成一份。

### E. repository 只保留原子提交

**要求**：

- `SQLiteAiMcpOperationRepository` 只保留：按指纹和 revision 比对后原子提交、写历史、更新收件箱状态和操作状态、幂等查询。
- 删掉它对 application 的非 ports 模块的全部 import。
- 删掉它里面的权限判断函数（`_actor_can_*`）。
- 确认策略默认值（`CREATE/MODIFY_CONFIRMATION_KEY` 的默认值）的归属，在 D 中一并理清，放到 application。

### F. 冲突结果只能由 application 判定

**问题**：现在是存储层把冲突记成 `human_rejected`，application 又把它变成普通的 `ValueError` 抛出去，结果 UI 和 transport 各自再把「这是冲突」猜回来，一共 5 处。

**要求**：

- 接受用例返回一个带类型的结果（outcome），明确区分成功、冲突、拒绝等情况，不再用抛异常来表示冲突。
- UI 只按 outcome 显示，transport 只按 outcome 序列化。
- **下面 5 处外部推断代码必须删除**：
  - `ai_center_dialog.py` 三处：约 210-224、826-847、1262-1270 行
  - `tools.py` 两处：约 967-972、1006-1011 行
- 操作状态和处理方式在 domain 定义一份统一词表，删掉各层手写的状态字符串。
- 数据库里已经存在的冲突旧记录，改完后显示效果要和现在一致。

**验收口径**：只要外部还留着任何自己推断冲突的代码，哪怕 owner 已经建好，本项也算**没修完**。

### G. AI 写入授权只有一个负责人

**问题**：「这个 AI 能不能改这条笔记、能不能用这个目录」目前有 4 份实现，语义还不完全一致：

- `local_mcp_service.py` 约 222 行
- `local_mcp_write_service.py` 约 197 行
- `ai_receipt_inbox_service.py` 约 180 行
- `ai_mcp_operations.py` 约 718-751 行

**要求**：

- 在 application 里只留一个授权负责人，其余调用方全部改为调用它。
- 统一时以**现有已验收行为**为准。如果发现几份实现之间的分歧，会导致某条路径的行为变化（无论变宽还是变严），**先停下来报告差异，由 S 拍板**，不要自行选定。

### H. 架构闸门 PASS

- `.venv/bin/python -B scripts/check_architecture.py` 输出 `ARCHITECTURE_CHECK=PASS`，errors=0。
- 满足 §1 的第 4 条，不以任何方式削弱闸门。
- 警告数量不得比基线（20 条）更多。如果有新增警告，逐条说明原因。

### I. 清掉过时测试，让全量测试重新可信

**基线**：`unittest discover -s tests` 共 447 个，8 个失败。

| 失败的测试 | 处理方式 |
|---|---|
| `test_architecture_gate.test_current_r2_skeleton_passes` | 由 H 自然修复 |
| `test_package8_production_bootstrap` ×2 | 改期望值。报告里写明对应的有意变更 commit（`27dee36`，新增 `get_note_markdown`） |
| `test_package3_ordinary_product_parity` | 改期望值，并写明对应的有意变更 commit |
| `test_package8_visual_polish` ×3 | 先判断错的是测试还是 theme。只有确认横向滚动条样式是有意改动、并写明是哪个 commit，才能改期望值；否则停下来报告 |
| `test_markdown_core_cutover` | **不要**把 `这是&#32;**重点**` 这个具体字符串写成期望值。改为验证规范化之后的语义和结构正确（比如强调、列表这些结构都在，而且结果是不动点）；同时把「规范化输出把空格实体化成 `&#32;`」记为已知债。如果施工中发现这是一处非常局部、没有歧义的修复，**也先报告，不要顺手改** |

`tests_mcp` 不算在本包「全量」的定义里。只需报告它当前的状态；如果失败是环境问题，说明理由就行。

---

## 3. 关于「MCP 进程加载整套 GUI」

这一项默认不在本包范围内。

**例外**：B、C、D 本来就要改规范化能力的注入方式。如果为了正确完成这些改动，自然就能消除 MCP 进程对 GUI bootstrap 的不必要依赖，那就一并消掉。不要为了「不扩范围」，故意留下一个就在切口里的错误依赖。

**反过来**：如果彻底解决它需要另外建一套无界面的装配框架，就停下来，只记债。

---

## 4. 明确不做

**另开小工单，本包不碰**：

- Reminder 已完成的条目还能被 resume（AI 能让它复活）
- 笔记保存时正文和标签分两个事务，导致 revision 假冲突

**只记债，不施工**：

- `ReminderService` 体量过大（45 个公开方法）
- Reminder 的 AI 写入分 4 个事务，不是原子的；`ai_operations` 在 Reminder 这边有多个写入方
- legacy `src/database.py` 等已经不再运行的旧代码
- Gale mirror schema 定义了两份
- `src/ai_targets.py` 已经和 actor 注册表分叉
- demo / smoke 文件被打包进正式 App
- 规范化输出里空格变成 `&#32;`
- UI 自己决定「能不能接受」（`can_accept` 的判断下沉到 application）
- 审计报告中其余的 P2

---

## 5. 封口条件（全部满足才算完成）

1. A–I 每一项都逐条达成，F 按「删干净 5 处外部推断」的口径验收。
2. 架构闸门 `ARCHITECTURE_CHECK=PASS`。
3. 全量 `unittest discover -s tests` 全部通过，没有新增的 skip 或 expectedFailure。
4. 没有 schema 变更，也没有动过正式数据库。
5. **人工验收**：在隔离的候选 App 和隔离数据目录中，S 验以下 6 项：
   - 接受 create
   - 接受 append
   - 接受 prepend
   - 接受 metadata
   - 接受 edit
   - 制造一次版本冲突：AI Center 显示为冲突且没有写入；AI 侧查状态也显示冲突、没有改动正式笔记
   - 以上各项在重启之后仍然成立。
6. 提交收口报告后停手，由寓做一次复核，只核对本单范围。

**以后的常规要求**：每个正式施工 Step 封口时跑一次「闸门 PASS + 全量测试 PASS」，全量一次约 2 分钟多。施工过程中跑针对性的测试就够了，不要求每个 commit 都跑全量。

---

## 6. 停工条件（遇到就停，报告现状和可选方案）

- 某项必须改数据库 schema。
- G 统一授权时，会让任何路径的行为变宽或变严。
- 想让闸门转绿，只能靠修改闸门规则或者挪动文件躲过扫描。
- 某个过时测试无法确认对应的有意变更 commit。
- 为了消除 MCP 对 GUI 的依赖，需要新建一套装配框架（见 §3）。
- 发现任何可能丢数据或越权的新问题，按 P0 处理，立即停下。

---

## 7. 收口报告格式

**先给状态块**：

```text
ACCEPTANCE_REPAIR=DONE|BLOCKED
ARCHITECTURE_CHECK=PASS errors=0 warnings=<n>
FULL_UNITTEST=<ran>/<passed>
RESULT_JSON_SINGLE_CONTRACT=YES
NOTES_AI_OPERATION_WRITER_OWNER=<模块名>
CANONICALIZER_VIA_PORT=YES
CANONICALIZE_INSIDE_WRITE_TXN=NO
ACCEPTANCE_ORCHESTRATION_OWNER=<模块名>
REPOSITORY_BUSINESS_LOGIC_REMOVED=YES
CONFLICT_OUTCOME_OWNER=<模块名>
EXTERNAL_CONFLICT_INFERENCE_REMAINING=0
AI_WRITE_AUTHORIZATION_OWNER=<模块名>
SCHEMA_CHANGED=NO
FORMAL_DB_TOUCHED=NO
MCP_GUI_DEPENDENCY=REMOVED|UNCHANGED_DEBT
HUMAN_GATE=PENDING|PASS
```

**然后分节**：

1. 各项做了什么（按 A–I）
2. 每个过时测试的处理方式和依据的 commit
3. 行为变化清单：只应该有 §2-A 那个 bug 修复，其他任何变化都要列出来
4. 新发现的债务（只记录，没有动手修）
5. 需要 S 拍板的事项
