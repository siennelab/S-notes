# S-notes GreenCloud 3.5｜11步具体施工步骤 v1.0 FINAL

**日期：2026-09-23**
**架构母本：`docs/S-notes GreenCloud 3.5｜最终架构与施工方案 v2.2 FINAL.md`（下称「v2.2」）**
**源码基线：`main` `c110ada`**
**用法：每一章（第 1–11 步）可单独复制给 Codex 作为一张施工单。章与章之间只通过「起点 / 依赖」衔接；跨章信息已在各章内重复写明。**

---

# 通用规则（每张施工单都适用，复制单章时请连同本节一起给）

1. **唯一正式仓库**：`/Users/s/Documents/项目/S-notes`。不在其他目录建工地。
2. **一步一封口**：每步在独立 task branch（`codex/gc35-stepNN-<slug>`）施工；PASS 并经审计后 fast-forward 进 `main`。未 PASS 不进入下一步。
3. **push**：除非 S 在该步明确同意，否则只 commit 不 push。
4. **App build**：凡改动 App 运行时（`src/snotes/**`、`web/markdown-editor/**`、`packaging/**`、`scripts/build_macos_app.sh`）的步骤，封口前用 `scripts/build_macos_app.sh` 重建 `dist/S-notes.app` 并验签、启动。
5. **GreenCloud 操作**：只经 `ssh optimal`（root）。每次 VPS 变更前在 `/root/greencloud-backups/<step>-<date>/` 留回滚材料；秘密只进 `/root/greencloud-secrets/`（0600）或 systemd credential，不进 Git / 文档 / 聊天 / 报告。
6. **不扩工程**：不为「未来可能」新增服务、表、抽象层；已 CLOSED 能力不重做。
7. **遇到本步「停工条件」或 v2.2 §60 任一条**：立即停，报告现状与选项，不擅自改架构。
8. **报告格式**：每步结束交一份 `docs/gc35-stepNN-report.md`，含：commit hash、改动文件、测试命令与结果、真人验收记录、VPS 变更清单（如有）、遗留问题。

---

# 施工前置清场（已完成，不算第 1 步）

`S-notes-md-wysiwyg-lab`（Markdown WYSIWYG 可行性实验，独立 git 仓库，无 remote）已于 2026-09-23 处理完毕：

```text
归档：/Users/s/Documents/项目/_archive/S-notes-md-wysiwyg-lab-20260923.bundle
      （完整 3 commit 历史，HEAD 23b461a；git bundle verify 通过；clone 还原树与原目录一致）
附带：/Users/s/Documents/项目/_archive/S-notes-md-wysiwyg-lab-20260923-ignored-prototype.tar.gz
      （原被 .gitignore 的 .prototype/session.md）
文档：Retrofit v1.2 FINAL 的「实验依据」已改指向归档 bundle
目录：已删除
```

后续施工唯一正式仓库：`/Users/s/Documents/项目/S-notes`。

若第 2 步 canonicalizer 需要旧实验的 `fixtures/format-stress.md`，从 bundle 中取出（`git clone <bundle> /tmp/lab`）复制进正式测试目录；不需要则不搬。

---

# 不要重做（已 CLOSED）

| 能力 | 状态 | 依据 |
|---|---|---|
| Markdown Retrofit Production（`notes.content_markdown` 唯一正文、Milkdown 编辑器、真实数据迁移） | CLOSED | `a4ec210` / `60125e8` / `f2e280f` |
| Reminder 3.5-0 多提醒节点（deadline-first multi-notify，occurrence/node 去重） | CLOSED（Mac local） | `67c6022`；Cloud 重新验收属第 8 步 |
| Notes MCP discovery 修复（prepend / append / update_note_metadata 去顶层 anyOf） | CLOSED | `c110ada` |
| expected_version-only MCP 公共 contract | CLOSED | `c110ada` |

---

# 源码现状速查（施工前必读，2026-09-23 核对）

| 事实 | 位置 |
|---|---|
| version token = `"snotes-v1-" + sha256(f"{note_id}\0{updated_at}")` | `src/snotes/application/local_mcp_write_service.py:479` |
| MCP 写服务：create / prepend / append / metadata；create 默认**直写**，modify 默认**需确认** | `local_mcp_write_service.py:58-330`；`storage/repositories/ai_mcp_operations.py:23-27` |
| 直写实现（在 MCP 进程内） | `ai_mcp_operations.py` `create_direct` / `modify_direct:167` |
| AI Center 接受：只支持 `create` 与 `append`；**prepend / metadata pending 会抛 `UnsupportedReceiptOperationError`（测试专门断言）** | `application/ai_receipt_inbox_service.py:192-281`；`tests/test_package4_ai_receipt_inbox_core.py:406` |
| append 接受时只复查版本，**不复查 actor 权限** | `ai_mcp_operations.py:299` `accept_pending_append` |
| 正文写路径均同时 bump `revision` + `updated_at` | `storage/repositories/notes.py`、`ai_mcp_operations.py:235/259/586` |
| 编辑器 canonical 化现成机制：**离屏 QtWebEngine 加载 App 已打包的 Milkdown bundle，不需要 Node** | `scripts/markdown_roundtrip_check.py`；`ui/markdown_editor.py` |
| 编辑器 bundle 打进 App：`Contents/Resources/web/markdown-editor/dist` | `scripts/build_macos_app.sh:121,242` |
| AI actor registry（含 `claude-code`、`clawbot`、`yan`、`gale`） | `application/ai_actor_registry.py` |
| 旧静态 target 列表（**不含 `claude-code`**，禁止用于新镜像） | `src/ai_targets.py:36` |
| Gale mirror：schema、构建、校验；**硬编码只支持 `gale` actor** | `src/services/gale_mirror_service.py:60-135, 513-516` |
| Gale publish：`scp` 上传 + `ssh` 固定命令激活 | `src/services/gale_publish_service.py:182-210` |
| Gale outbox（Oracle）：`submissions` 表，`source_actor` / `delivery_state` / `title` / `content` 均有 CHECK | `src/services/gale_submission_outbox.py:94-125` |
| Mac pull：`pull_once` = export_unacknowledged → import → acknowledge | `application/gale_service.py:419-463` |
| Mac 收件箱已有 `source` 列；去重唯一索引按 `(source, external_submission_id)`、`(source, idempotency_key)` | `storage/schema.py:102, 199-206` |
| ReminderService 无 Qt 依赖，可 headless | `application/reminder_service.py` |
| ntfy.sh 公共 fallback | `src/snotes/bootstrap.py:125-126`；UI 显示 `ui/reminder_center.py:1175-1177` |
| 业务时区 Asia/Shanghai | `src/time_utils.py:5`、`storage/support.py:11` |
| Reminder helper：build 内嵌二进制 + `packaging/snotes-reminder-helper.spec`；plist 由 `background/reminder.py` 生成；**当前已安装并 loaded** `com.snotes.reminder-helper` | `scripts/build_macos_app.sh:10-32,167-206`；`packaging/S-notes.spec:19` |
| GreenCloud：Debian 13；`claude` 2001 / `openclaw` 2002（LAX fail-closed）；Caddy 2.6.2 仅 `ntfy.ke-den.de` 一个 site；ntfy 2.28 deny-all + `s-*` | `/Users/s/Documents/项目/greencloud vps建设/INSTALL-NOTES.md` |
| **`snotes.ke-den.de` 已被 Oracle Gale staging MCP 占用（含 Auth0 callback）** | `docs/GALE_ORACLE_R1A.md` |
| GreenCloud 上 Claude Code / OpenClaw **尚未安装** | GreenCloud 进度书 |

---

# 总览

```text
3.5-0 剩余   第1步 get_note_markdown + dialect
             第2步 canonicalizer + 写入 / version 收口
             第3步 edit_note + 本地真人验收            → 封 3.5-0
3.5-A        第4步 Mac actor-specific mirror
             第5步 GreenCloud Notes read               → 封 3.5-A
3.5-B        第6步 Cloud Reminder staging
             第7步 Mac RemoteReminderClient
             第8步 Reminder 正式切云                    → 封 3.5-B
3.5-C        第9步 AI 权限隔离                          → 封 3.5-C
3.5-D        第10步 GreenCloud outbox + proposal
             第11步 Mac 接收 + AI Center + receipt      → 封 3.5-D
3.5-D 后续   Cloud prepend + update_note_metadata（必做，另开施工单）
```

重步骤：2、8、10、11。

**需 S 在开工前拍板的决定（不影响第 1–5 步）：**

| # | 决定 | 最晚 | 建议 |
|---|---|---|---|
| D1 | Reminder API 子域名（`snotes.ke-den.de` 已被 Oracle Gale 占用，不能挪用） | 第 6 步开工前 | 新子域，如 `reminder.ke-den.de`，Cloudflare A 记录 DNS-only 指向 GreenCloud |
| D2 | Reminder 新 ntfy topic 名（须 `s-*`） | 第 8 步开工前 | 如 `s-reminder` |
| D3 | Cloud backup 的 off-VPS 落点 | 第 6 步开工前 | Mac 在线周期拉取即为 off-VPS 副本（v2.2 §48），落 Mac 本地 Application Support 下专用目录 |

---

# 第 1 步｜get_note_markdown + snotes-markdown-v1 dialect

## S 得到什么
本地 AI（砚 / Claude Code）可以用新工具 `get_note_markdown` 读取有 full 权限笔记的**原始 Markdown**（含粗体、链接、表格、`:size` 字号标记），并拿到版本号和方言说明。读取不改变笔记任何东西。

## 起点与依赖
- 起点：`main` `c110ada`。
- 依赖：Markdown Production CLOSED；expected_version-only contract CLOSED。

## 允许修改
- `src/snotes/transport/mcp/tools.py`、`schemas.py`（新增工具注册与 schema）
- `src/snotes/application/local_mcp_service.py` 或 `ai_safe_read_service.py`（新增只读方法）
- 新增 `docs/SNOTES_MARKDOWN_DIALECT_V1.md`（方言规格）
- `tests/` 新增 / 扩展测试
- `docs/MCP_SERVER_RUNBOOK.md`（工具清单）

## 禁止
- 不碰任何写路径、`ai_mcp_operations.py`、AI Center、Gale、Reminder。
- 不做 canonicalize；不写 DB；不改 `search_notes` / `get_note` 的 plain text 契约。
- 不引入新依赖。

## 工作内容
1. **方言规格**：对照正式 `web/markdown-editor/src/main.js`、`body-size.js`、`list-style.js` 的真实配置，写 `snotes-markdown-v1`：CommonMark + GFM（strikethrough / task list / table）+ `:size[...]{size="15"|"20"}`（仅 15、20 合法）+ 当前编辑器支持的 heading / list 组合；写明「未知 directive 与非法 size 以字面文本保留，AI 不得删除」。**以源码为准，不凭印象。**
2. **工具 `get_note_markdown(note_id)`**：
   - 仅当 actor 对该 Note 为 **full** 且 actor 拥有修改能力时返回；redacted / hidden / 未授权 / 已删除 → `not_found`（与现有 hidden 行为一致，不泄露存在性）。
   - 返回 `{note_id, version, markdown, dialect: "snotes-markdown-v1"}`；`version = note_version_token(note_id, updated_at)`。
   - **纯读取**：原样返回 `content_markdown`；不 canonicalize、不回写、不 bump revision / updated_at / version。
   - 记入现有 AI 调用审计（`ai_call_logs`），按现有读工具惯例。
3. tool description 写明方言 ID、扩展集合、合法 size 值、「不得删除不认识的 S-notes directive」。

## 复用
- 现有 actor 权限判定与 full/redacted 物化（`ai_safe_read_service` / `ai_access_metadata_service`）。
- 现有 `note_version_token`。
- 现有 MCP 工具注册与审计模式（参照 `get_note`）。

## 必须验证
正向：
- full Note → 返回原文字节级等于 DB `content_markdown`；version 正确；dialect 正确。
- 含 `:size[..]{size="15"}` 的 Note 原样返回。
- Claude Code 真实 client discovery 中可见 `get_note_markdown`（真实 `claude` 会话列工具，不是只测 server 注册）。

负向：
- redacted → `not_found`；hidden → `not_found`；他 actor only → `not_found`；已删除 → `not_found`。
- 调用前后该 Note `revision`、`updated_at`、version、`content_markdown` 完全不变；DB 无写事务（测试断言）。
- 工具 schema 无顶层 `anyOf` / `oneOf`。

## S 真人参与
- 在 Claude Code 中让 AI 读一篇含粗体 / 链接 / 字号的 full 笔记，确认看到的是 Markdown 原文；读一篇 redacted 笔记确认拿不到。约 5 分钟。

## PASS
上述正负向全部通过；定向测试 GREEN；既有 MCP 协议测试（`tests/test_package5_local_mcp_protocol.py` 等）不回归；真人确认。

## 停工条件
- 发现现有权限判定无法区分「有修改能力」，需要新建权限模型。
- 方言规格与编辑器真实行为对不上（例如编辑器会删掉某类 directive）。

## commit / build / deploy / push
- commit：是（task branch → 审计 → ff main）。
- build：否（仅 MCP 进程与文档；App 不变）。
- deploy：无。push：待 S 同意。

## 下一步
第 2 步 canonicalizer。

---

# 第 2 步｜canonicalizer + 现有写入 / version 收口

## S 得到什么
AI 写进笔记的内容，落库前统一变成和编辑器完全一致的格式；S 打开 AI 改过的笔记再保存，不会出现「内容没变但整篇被重写」。本地 prepend / 修改资料提案在 AI Center 里终于可以「接受」。App 不需要装 Node。

## 起点与依赖
- 起点：第 1 步已 ff 进 `main`；方言规格 `docs/SNOTES_MARKDOWN_DIALECT_V1.md` 存在。

## 允许修改
- 新增 canonicalizer 模块（application / ui 层按现有分层放置）
- `src/snotes/ui/markdown_editor.py`（仅为提供可复用 headless 序列化入口，不改编辑体验）
- `src/snotes/storage/repositories/ai_mcp_operations.py`、`ai_receipts.py`
- `src/snotes/application/ai_receipt_inbox_service.py`、`local_mcp_write_service.py`
- `src/snotes/ui/ai_center_dialog.py`（prepend / metadata 接受展示）
- `scripts/`（只读扫描脚本）、`tests/`

## 禁止
- 不做存量正文批量修改 / 迁移（v2.2 §15A、§61）。
- 不引入「用户需另装 Node」的运行时依赖。
- 不另写一套 Python Markdown 语法实现来冒充 Milkdown（若确需 Python 实现，按 v2.2 §15 golden fixtures 证明等价，且先停工报告）。
- 不开 edit_note（第 3 步）；不碰 Gale / Reminder / Cloud。

## 工作内容（按顺序）

### 2A｜只读 round-trip 扫描（先做，先报告）
- 基于 `scripts/markdown_roundtrip_check.py` 的机制（离屏 QtWebEngine + App 打包的 Milkdown bundle）写只读扫描：对**正式 DB 的只读副本**（SQLite backup API 取快照）逐条 `load → serialize`，比较原文。
- 输出：总数 / ZERO DIFF 数 / 有差异数 / 差异类型归类（不输出正文原文）。
- **交 S 看报告后再继续 2B。** 有差异的存量如何处理由 S 另定，本步不处理。

### 2B｜canonicalizer
- 实现 `canonicalize_markdown(text) -> str`，**复用 App 已打包的 Milkdown 配置**（与编辑器 baseline 同源）。现成路径：`markdown_roundtrip_check.py` 已证明离屏 QtWebEngine 可在无 Node 的 App 环境运行。
- 必须处理「在哪个进程 canonicalize」：
  - AI Center 接受发生在 GUI 进程 → 可直接用。
  - **MCP 直写路径（create 默认直写；modify 在确认关闭时直写）运行在 MCP stdio 进程**。二选一，选最小实现并在报告说明：
    (a) MCP 进程内按需启动离屏 QtWebEngine canonicalize；
    (b) canonicalizer 不可用时该写入 fail closed 或转为 pending（进 AI Center，由 GUI 接受时 canonicalize）。
  - 任何情况下不得「未 canonicalize 就直写正式库」。
- golden fixtures（至少 v2.2 §15 清单：paragraph / heading / bold / italic / strike / link / lists / task / table / escaping / trailing newline / `:size`）。
- 幂等：`canonicalize(canonicalize(x)) == canonicalize(x)`。

### 2C｜现有写入接入
- create（直写与接受）、prepend、append 的最终正文：先拼接，再对**整篇结果** canonicalize，再落库。
- canonicalize 失败 → 该写入 rejected，正式正文零写入。

### 2D｜version / revision 审计
- 列出所有 `UPDATE notes` 写路径（已知：`repositories/notes.py`、`ai_mcp_operations.py`、`tags.py`、`directories.py`、`schema.py`、`src/database.py`），确认所有改正文路径同事务 bump `revision` + `updated_at`；报告中给出清单。已核对正文路径均满足，本项以测试固化为主。

### 2E｜接受时权限复查 + 补齐 prepend / metadata 接受
- `accept_pending_append` 在事务内复查：Note 存在且未删除、actor 当前仍有权限、expected_version 匹配、request_id 未执行、canonical 结果合法。权限已撤销 → rejected / conflict，正文零写入。
- **补齐本地 prepend 与 metadata pending 的 AI Center 接受**（当前抛 `UnsupportedReceiptOperationError`，modify 默认需确认，因此这两个工具目前走不通；v2.2 §20 / §57 要求它们「进入既有 modify confirmation」并可完成）。复用 `modify_direct` 的业务逻辑与 append 接受的事务 / 复查模式；metadata 仅限现有 allowlist（title / directory / tags），不得触及 visibility / targets。
- 更新原「prepend 不支持」测试为新行为测试。

## 复用
- `scripts/markdown_roundtrip_check.py`、`MarkdownEditorWidget` 的离屏加载机制。
- `modify_direct`、`accept_pending_append`、`_insert_history`、`next_note_updated_at`。
- 现有 AI Center 卡片与接受流程（不另做新对话框）。

## 必须验证
正向：
- golden fixtures：`canonical → Milkdown load → serialize` **ZERO DIFF**。
- create / prepend / append 结果落库后，用编辑器打开再序列化 ZERO DIFF。
- 本地 prepend、metadata 提案在 AI Center 可接受，结果正确；history 写入；revision + updated_at bump；新 version 正确变化。
- 未安装 Node 的环境（或 PATH 去掉 node）下，打包后的 App 能完成接受。

负向：
- canonicalize 失败 → rejected，正文零写入。
- 非法 size / 未知 directive 不被静默删除。
- 接受前撤销 actor 权限 → 不写入。
- stale version → conflict，零写入。
- metadata 接受不能改 visibility / targets。
- 扫描脚本对 DB 零写入（校验文件哈希或只读连接）。

## S 真人参与
- 看 2A 扫描报告，决定是否继续（必须）。
- 在打包 App 里接受一次 AI prepend 和一次 metadata 修改，打开笔记确认显示正常。

## PASS
2A 报告已由 S 确认；2B–2E 测试 GREEN；ZERO DIFF Gate 通过；打包 App 无 Node 可接受；既有 AI Center / Gale receipt 测试不回归。

## 停工条件
- canonicalizer 无法与 Milkdown round-trip byte-stable（v2.2 §60-2）。
- 扫描发现大量存量差异，且差异会导致 S 日常编辑出现大面积版本变化 → 报告，由 S 决定。
- 发现某正文写路径不 bump `updated_at`（§60-7）→ 报告后再修。
- MCP 直写路径两种方案都不可行。

## commit / build / deploy / push
- commit：是，建议 2A 单独一个 commit（脚本），2B–2E 一个或数个。
- build：是（App 运行时改动）。deploy：无。push：待 S 同意。

## 下一步
第 3 步 edit_note。

---

# 第 3 步｜edit_note + 3.5-0 本地真人验收（封 3.5-0）

## S 得到什么
本地 AI 可以对已有笔记提出「把这段改成那段」的修改（可以改粗体、链接、表格、字号），修改进 AI Center 让 S 看 diff 后接受。3.5-0 全部封口。

## 起点与依赖
- 第 2 步已 ff 进 `main`：canonicalizer、接受时复查、prepend / metadata 接受均可用。

## 允许修改
- `src/snotes/transport/mcp/tools.py`、`schemas.py`
- `local_mcp_write_service.py`、`ai_mcp_operations.py`、`ai_receipt_inbox_service.py`、`ai_receipts.py`
- `src/snotes/ui/ai_center_dialog.py`（edit diff 展示）
- `tests/`、`docs/MCP_SERVER_RUNBOOK.md`、`docs/PROJECT_STATUS.md`

## 禁止
- 不支持整篇重写（`new_body`）。
- 不做 HTML surgery、section merge、三方合并。
- redacted 不可 edit。
- 不碰 Cloud / Gale / Reminder。

## 工作内容
1. MCP 工具 `edit_note(request_id, note_id, expected_version, edits:[{old_markdown, new_markdown}, ...])`。
2. 提交时校验：full 权限、Note 存在、expected_version 匹配、每个 `old_markdown` **唯一命中**、request_id 幂等、新内容不明显违反方言。
3. 处理模式沿用现有 modify confirmation 设置（默认进 AI Center）；若确认关闭走直写，同样 canonicalize。
4. 接受事务（v2.2 §24 十项）：存在 / 未删 / 仍 full / version 匹配 / 全部 old 仍唯一命中 / 全部可应用 / canonicalize / 合法 / 非空 / revision + updated_at bump → history → receipt accepted。任一失败：正文零写入，rejected / conflict。
5. AI Center：edit 提案显示逐组 diff（old → new），可接受 / 拒绝。
6. tool description 写明方言与「不得删除不认识的 directive」。
7. 完成后更新 `docs/PROJECT_STATUS.md` 记 3.5-0 CLOSED。

## 复用
- 第 2 步 canonicalizer、接受事务骨架（append / prepend / metadata 同一模式）。
- `mcp_create_pending`、`ai_receipt_inbox` 的 `operation_type` / `operation_payload` 列（不加新表）。
- 现有 AI Center 卡片。

## 必须验证
执行 v2.2 §57 全部 23 条。重点：
- edit 修改 bold / link / table 成功；`:size` 合法 directive edit 后仍生效（编辑器中显示对应字号）。
- 多组 edits 原子；0 命中 reject；>1 命中 reject；stale conflict；权限撤销后 pending 不能执行；redacted edit 被拒。
- 接受后 Milkdown load / serialize ZERO DIFF。
- `delete_note` 不存在。
- Claude Code 真实 discovery 可见 `edit_note` 与 `get_note_markdown`，且 prepend / append / update_note_metadata 仍可见。

## S 真人参与（本步核心）
按 §57 做一轮本地真人验收，约 20–30 分钟：
1. 让 AI 改一篇笔记里的一个粗体词和一个链接 → AI Center 看 diff → 接受 → 打开笔记确认。
2. 在 AI 提交后、接受前，S 自己改这篇笔记 → 再接受 → 应冲突。
3. 让 AI 在一篇 redacted 笔记上 edit → 应被拒。
4. 让 AI create / append / prepend / 改资料各一次 → 走通。

## PASS
§57 23 条全过；S 真人验收 PASS；全量定向测试 GREEN；App 重建验签。**→ 3.5-0 CLOSED。**

## 停工条件
- 唯一命中规则在真实笔记上大面积不可用（例如 canonical 后空白差异导致 old 无法命中）→ 报告，不擅自放宽为模糊匹配。
- 需要新表或新 proposal 生命周期才能承载 edit。

## commit / build / deploy / push
- commit：是。build：是。deploy：无。
- push：3.5-0 封口，建议此时经 S 同意 push `main`。

## 下一步
第 4 步 Mac actor-specific mirror。

---

# 第 4 步｜Mac actor-specific Notes mirror（纯本地）

## S 得到什么
Mac 能为 Claude Code 和 Clawde 各生成一份「只含它该看到的笔记」的安全镜像文件，可以本地打开检查。还不上云。

## 起点与依赖
- 3.5-0 CLOSED（第 3 步）。

## 允许修改
- 新增或扩展 mirror 生成代码（优先复用 `src/services/gale_mirror_service.py` 的构建 / 校验 / FTS / 原子写能力）
- `src/snotes/application/gale_service.py` 或新增同层 service（按现有分层）
- `tests/`

## 禁止
- 不改 Gale production mirror 的 contract 与行为（Gale 仍只产出 `gale` 镜像且字节 / schema 不变）。
- 不用 `src/ai_targets.py` 静态列表选 actor（它不含 `claude-code`）；必须用 `application/ai_actor_registry.py`。
- 不做上传、不碰 VPS。

## 工作内容
1. **先做 v2.2 §8 判断并在报告写明**：给现有 mirror schema 加 nullable `safe_markdown` 是否会影响 Gale（Gale validator 若做 schema 精确校验则会受影响）。影响 → GreenCloud 用扩展 contract（Gale 原样不动）；不影响 → 共用。
2. 生成 `claude-code`、`clawbot` 两份镜像：
   - common → 两份都有；X-only → 只进 X；hidden / 未授权 → 不存在。
   - full：`safe_text = markdown_to_plain_text(content_markdown)`；`safe_markdown = content_markdown` 原文，**零脱敏**。
   - redacted：`safe_text` = plain text → Mac redaction；`safe_markdown = NULL`。
   - `source_version = note_version_token(note_id, updated_at)`。
   - metadata：`generated_at`、`hash`、`note_count`、source schema、target_actor。
3. 校验器：打开候选、schema / 计数 / hash / actor 一致；损坏候选拒绝。
4. 输出 hash 可用于「未变化跳过」。

## 复用
- `materialize_ai_safe_notes_for_target`（`src/services/ai_access_service.py`）、Gale mirror 的 SQLite / FTS / 校验 / 原子写。
- 现有 redaction。

## 必须验证
正向：v2.2 §56 第 1–7、10–12 条的本地版（common / X-only / hidden / redacted / full / 零脱敏 / dialect / source_version / registry）。
负向：
- Gale mirror 生成结果与改动前一致（schema + 内容 hash 回归）。
- 损坏候选被校验拒绝。
- `claude-code` 镜像中无 clawbot-only 笔记，反之亦然。

## S 真人参与
- 可选：看一眼两份镜像里的笔记标题清单是否符合预期权限。

## PASS
正负向全过；Gale 回归通过；报告写明 §8 决策。

## 停工条件
- 必须改 Gale production contract 才能做（§60-3）。
- actor registry 无法表达 claude-code / clawbot 的权限计算。

## commit / build / deploy / push
- commit：是。build：否（除非改了 App 内 UI）。deploy：无。push：待 S 同意。

## 下一步
第 5 步 GreenCloud Notes read。

---

# 第 5 步｜GreenCloud Notes read（封 3.5-A）

## S 得到什么
Mac 每 5 分钟（或手动）把两份镜像安全送到 GreenCloud；云上的 Notes 只读 MCP 能搜 / 读笔记；Mac 关机时云上仍可读最后一份。云上两个 AI 用户彼此看不到对方的镜像。

## 起点与依赖
- 第 4 步 CLOSED。
- GreenCloud 现状：Debian 13；`claude` UID 2001、`openclaw` UID 2002 已建；**Claude Code / OpenClaw 尚未安装**（本步不安装）。

## 允许修改
- 仓库：新增 Cloud read MCP server 模块（只读，可复用 `src/gale_remote_mcp.py` / `gale_remote_service.py` 的读逻辑）、GreenCloud 部署文件（`deploy/greencloud/` 下 systemd / 接收脚本 / 说明）、Mac 端 publish 调度（复用 Gale publish 的 scp + ssh 模式）、`tests/`。
- GreenCloud：新建 `snotes` 用户、`/srv/snotes/`、接收脚本、`authorized_keys` 一条 forced-command 记录、Python venv。

## 禁止
- 不碰 Caddy、ntfy、de-node、egress 规则。
- 不新增公网 REST。
- 不安装 Claude Code / OpenClaw runtime（属 GreenCloud 自身路线图）。
- 不改 Oracle / Gale。
- 不同步整个 `s_notes.db`（§60-9）。

## 工作内容
1. **建立 `snotes` service identity**（v2.2 §4，本步首次建立）：从空闲 UID 分配（文档中 2007–2011 已预留给其他服务，避开），`nologin`，home `/srv/snotes`，按 GreenCloud 现有布局（`app/ data/ deploy/ INSTALL.md`）。记录进 `/srv/snotes/INSTALL.md`。
2. 目录与权限：
   ```text
   /srv/snotes/mirror/claude-code/   owner snotes，group 仅允许 claude 读
   /srv/snotes/mirror/clawbot/       owner snotes，group 仅允许 openclaw 读
   ```
   用组或 ACL 实现；验证 2001 不能读 clawbot、2002 不能读 claude-code。
3. Transport：Mac 专用 SSH key（仅 S-notes transport）；GreenCloud `authorized_keys` 中 `command="<固定接收脚本>",restrict`；接收脚本只接受 incoming 文件 → 校验 → 原子替换 `current.db`；候选损坏则 `current.db` 不动。
4. Mac 端：5 分钟检查 + 手动立即刷新；hash 未变跳过。复用 Gale publish 的调度与状态记录方式（若新增 launchd job，按现有 `com.snotes.gale-production-pull` 惯例）。
5. Cloud read MCP：`search_notes`、`get_note`（safe plain text）、`get_note_markdown`（full-only，返回 dialect）。**每个请求 open read-only / immutable SQLite → query → close**，不缓存连接（v2.2 §10）。以对应 actor 的 OS 用户运行（stdio 启动，由将来的 runtime 调用）。

## 复用
- Gale publish：`scp` 上传 + `ssh` 固定命令激活、状态文件、失败记录。
- Gale remote read service 的查询与格式化。
- 第 4 步生成与校验器。

## 必须验证
执行 v2.2 §56 全部 17 条，其中：
- 13：Mac publish 后，Cloud **下一个 request** 读到新内容（新 inode）。
- 14：Mac 关机（或断开 transport）后旧镜像可读。
- 15：上传损坏候选 → `current.db` 不变。
- 16 / 17：`runuser -u claude -- cat .../clawbot/current.db` 失败；反之亦然。
- 由于 Claude Code 尚未安装，MCP 验收用 `runuser -u claude` / `runuser -u openclaw` 直接以 stdio 调 MCP server（JSON-RPC 列工具 + 调工具）。真实 Claude Code client discovery 待 GreenCloud 安装 Claude Code 后补验，记入遗留。
- forced-command key 无法获得 shell、无法执行其他命令。

## S 真人参与
- 在 Mac 上改一篇 common 笔记 → 手动刷新 → 看 Cloud 端读到新内容（Codex 给出一条命令即可）。约 5 分钟。

## PASS
§56 17 条全过（真实 client discovery 标为遗留）；VPS 变更有回滚材料；`/srv/snotes/INSTALL.md` 完整。**→ 3.5-A CLOSED。**

## 停工条件
- actor 无法在 filesystem 层真正隔离（§60-6）。
- 需要开公网端口或改 Caddy。
- GreenCloud Python 版本与项目要求不兼容且需要装第二套系统 Python。

## commit / build / deploy / push
- commit：是。build：Mac 若新增 launchd / App 内刷新按钮则是。deploy：是（GreenCloud）。push：待 S 同意。

## 下一步
第 6 步 Cloud Reminder staging。

---

# 第 6 步｜Cloud Reminder staging（不切正式数据）

## S 得到什么
GreenCloud 上跑起一套完整的 Reminder 服务（数据库、API、每 5 分钟调度、ntfy 私有推送、每日备份），用测试数据跑通；正式 Reminder 仍在 Mac，没有任何变化。

## 起点与依赖
- 3.5-A CLOSED；`snotes` 用户已存在（复用，不再新建）。
- **S 已拍板 D1（子域名）、D3（备份落点）。**
- Reminder 测试基线：开工先跑 Reminder 定向测试，确认 GREEN（`67c6022` 记为 59 条通过）；若有红灯，先按 v2.2 §52 处理（修复或显式 xfail + 理由）。

## 允许修改
- 仓库：新增 Reminder API server 模块（HTTP，窄命令集）、Cloud scheduler 入口、ntfy 鉴权 transport、`deploy/greencloud/reminder/`（systemd unit / timer / Caddy site 片段 / 说明）、`tests/`。
- GreenCloud：`/srv/snotes/` 下 Reminder 目录、systemd service + timer、Caddy **新增独立 site block**、ntfy 为 Reminder 新建专用用户 / token（仅 `s-*` 写权限）、备份脚本 / timer。

## 禁止
- 不修改 / 不整理 `ntfy.ke-den.de` 既有 Caddy block；不动 ntfy `server.yml` 的 upstream 设置。
- **不使用 `snotes.ke-den.de`**（Oracle Gale 占用）。
- 不导入正式 Reminder 数据；不动 Mac Reminder；不退役 Mac helper。
- 不提供 arbitrary SQL / generic mutation / filesystem RPC。
- 不引入 Postgres / Redis / broker / Docker。

## 工作内容
1. Cloud Reminder DB：`/srv/snotes/data/reminder.db`，owner `snotes` 0600，WAL + busy_timeout；schema 与 Mac 当前 Reminder schema 一致（含多提醒节点）。
2. 服务层直接复用 `ReminderService`（无 Qt 依赖）+ 现有 repository；不另写业务逻辑。
3. Reminder API（仅 127.0.0.1 监听，经 Caddy 暴露）：
   - `GET /v1/reminder/snapshot` → `schema_version, server_time, snapshot_time, labels, tables, items, settings, badge_count`（不含 credential，不含可编辑 endpoint）。
   - 写操作 = 明确 command，覆盖 owner UI 现有全部操作。
   - credential → 固定 actor identity + command allowlist + visibility；不接受 body 自报 actor。
   - owner credential：全部可见，完整人工管理。
   - actor credential（`yan`、`claude-code`、`clawbot`）：只 `ai_visible=true`；allowlist = 当前 MCP Reminder 工具集合（list / get / create / update / complete / pause / resume，以 `src/snotes/transport/mcp/tools.py` 现状为准）；不开放 delete / 改 visibility。
4. Scheduler：systemd `OnCalendar=*:0/5` + `Persistent=true` → 一次 `run_scheduled_once()` 后退出；服务器 OS 保持 UTC，业务时区 Asia/Shanghai。
5. ntfy：endpoint 固定 `https://ntfy.ke-den.de`（服务端可经 loopback `127.0.0.1:2586` 直连，Host 头按 ntfy 要求）；专用 Reminder 发送 credential 用 `LoadCredential=`（或 0600 EnvironmentFile）；无合法配置 → fail closed。staging 用 `s-test` 或临时 `s-*` 测试 topic。
6. 备份：每日 SQLite backup API 快照，滚动保留；为第 7 步 Mac 拉取准备只读取回通道（复用第 5 步窄 SSH 模式，另一条 forced-command 或同脚本子命令）。
7. Caddy：新增 D1 子域 site block → 127.0.0.1:<reminder port>；`caddy validate` 后 `systemctl reload caddy`；更新回滚脚本。DNS 记录由 S 在 Cloudflare 添加。

## 复用
- `ReminderService`、reminder repositories、`reminder_schedule` 领域、`src/snotes/transport/reminder_ntfy.py`（加鉴权，去 fallback）。
- 第 5 步 `snotes` 用户、venv、窄 SSH 模式。
- GreenCloud 现有 LoadCredential 惯例（de-node）。

## 必须验证
正向：
- 测试数据 CRUD 经 API 全通；snapshot 字段齐全。
- `daily_time=10:00` 在北京时间 10:00 触发（用可控时钟或测试时间点证明，不只看 UTC）。
- 同一 occurrence 多通知点各发一次、各自去重。
- reboot 后 Persistent timer 补跑一次且不重复发。
- ntfy 经鉴权发到测试 topic，iPhone 可收到（可选）。
- 备份文件 `sqlite3` 真打开、`PRAGMA integrity_check`、计数一致。

负向：
- 无 credential / 错 credential → 401 / 403。
- actor credential 访问 `ai_visible=false` → not_found；猜 ID 不能绕过；delete / 改 visibility → 拒绝；body 自报 actor 无效。
- ntfy 配置缺失 → 发送 fail closed，不发 ntfy.sh。
- `ntfy.ke-den.de` Caddy block 字节未变。
- `claude` / `openclaw` UID 对 `reminder.db` 无文件权限。

## S 真人参与
- 在 Cloudflare 添加 D1 子域 DNS 记录（Codex 给出精确记录值）。
- 可选：手机收一条 staging 测试推送。

## PASS
正负向全过；staging 连续运行 ≥ 24 小时无异常（timer 正常、无重复发送）；VPS 回滚材料齐全。

## 停工条件
- `ReminderService` 实际依赖 Mac 本地 mutable state（§60-4）。
- 需要改 ntfy 既有配置或既有 Caddy block。
- 时区无法在 UTC OS 下正确表达。

## commit / build / deploy / push
- commit：是。build：否（Mac App 未改）。deploy：是（GreenCloud staging）。push：待 S 同意。

## 下一步
第 7 步 Mac RemoteReminderClient。

---

# 第 7 步｜Mac RemoteReminderClient（对 staging 联调）

## S 得到什么
一个开发版 S-notes，可以把 Reminder 界面切到云端 staging：列表来自云端、增删改走云端、断网时只读显示最后一份快照；界面始终不卡。正式 App 还没切换。

## 起点与依赖
- 第 6 步 staging PASS 并在运行。

## 允许修改
- 新增 `RemoteReminderClient`（transport 层）、Reminder UI 数据源切换（`src/snotes/ui/reminder_center.py`、`main_window.py` 中 `reminder_service` 使用点）、Keychain 读取、offline snapshot 缓存（GUI-only）、Mac 本地 MCP 的 Reminder 工具改走 Cloud API（actor credential）、helper 退役代码（**写好但不执行**）、`tests/`。

## 禁止
- 不导入正式数据、不切正式 App、不 unload 现有 helper。
- 不做 RemoteReminderRepository（v2.2 §61）——client 对接的是 API command，不是远程仓储。
- 不写离线写队列；不回退旧 Mac DB。
- owner token 不进 `s_notes.db`、共享 `.env`、AI 进程环境；Mac MCP 不得读 owner offline cache。

## 工作内容
1. `RemoteReminderClient`：snapshot 读取 + 各 command；3–5 秒 timeout；错误分类（offline / auth / schema_mismatch / server）。
2. 网络调用全部在 worker thread（参照 `ai_center_dialog.py` 现有 `QThread` 用法）：loading、pending 时相关按钮 disable、error state、过期响应不覆盖新 UI。
3. schema_version 高于支持范围 → 不解析、不发写、UI 显示「版本不匹配 · 只读」+ last compatible snapshot 时间。
4. offline：显示 last compatible snapshot + 「离线 · 快照时间」，写操作全部 disabled。
5. owner token：macOS Keychain，`security` CLI 读取；仅 GUI 进程。
6. Mac 本地 MCP Reminder 工具：从本地 `ReminderService` 改为调用 Cloud API，使用各自 actor credential（`yan`、`claude-code`），credential 存放方式与 owner token 隔离。
7. ntfy 设置界面：endpoint 只读显示，不可编辑（v2.2 §43）；删除 `ui/reminder_center.py:1175` 的 `ntfy.sh` 显示兜底与 `bootstrap.py:125-126` 的 fallback（此处 fallback 删除在第 8 步切换时生效也可，但代码本步写好）。
8. helper 退役代码写好：`build_macos_app.sh` 去掉 Reminder helper 构建 / 内嵌、`packaging/S-notes.spec` 去掉 helper、App startup self-check（发现 `com.snotes.reminder-helper` plist 或 loaded job → 明显告警「Legacy local Reminder scheduler detected」）。**以开关 / 分支方式保留到第 8 步合入，不在本步执行 unload。**
9. 开发版通过配置指向 staging；正式配置切换留到第 8 步。

## 复用
- 现有 Reminder UI 组件与交互（只换数据源）。
- `QThread` worker 模式、第 6 步 API contract。

## 必须验证
正向：v2.2 §59 第 8–12、27 条（对 staging）：owner CRUD、UI 无主线程冻结（人为加延迟 / 断网测试）、schema mismatch 自动只读、Cloud 改 → Mac 刷新可见、Mac 改 → Cloud 可见。
负向：
- 断网 → 只读 + 快照时间，写按钮 disabled。
- 伪造高 schema_version → 只读，不发写。
- AI 进程环境、`s_notes.db`、日志中搜不到 owner token。
- Mac MCP 读不到 owner offline cache。
- self-check 在当前（helper 仍在）环境下能发出告警。

## S 真人参与
- 用开发版操作一遍 Reminder（新建 / 完成 / 暂停），拔网线看只读状态。约 15 分钟。

## PASS
正负向全过；S 真人体验通过；正式 App 与正式 Reminder 未受影响。

## 停工条件
- UI 依赖方案未记录的本地 mutable state（§60-4）。
- 需要离线写队列才能满足现有 UI 行为。

## commit / build / deploy / push
- commit：是（task branch；可暂不 ff main，与第 8 步一起合入，由审计决定）。
- build：开发版 build 用于验收；**不替换正式 `dist/S-notes.app`**。deploy：无。push：待 S 同意。

## 下一步
第 8 步 Reminder 正式切云。

---

# 第 8 步｜Reminder 正式切云 + Mac helper 永久退役（封 3.5-B）

## S 得到什么
Reminder 的唯一正式数据和调度都在 GreenCloud；Mac 关机也照常提醒到 iPhone；Mac 上旧的本地提醒程序永久消失，重装 App 也不会复活。

## 起点与依赖
- 第 6、7 步 PASS。**S 已拍板 D2（新 topic 名）。** S 在场（维护窗口 + iPhone）。

## 允许修改
- 迁移脚本（一次性，只读 Mac 源 → 写 Cloud）、第 7 步代码合入、正式配置切换、`build_macos_app.sh` / `packaging/*.spec`、Mac `~/Library/LaunchAgents` 中 Reminder helper、GreenCloud Reminder 正式 DB / topic / ntfy ACL。

## 禁止
- 不迁移 transport 配置（`reminder/ntfy_endpoint`、旧 topic、credential）为 Cloud 业务 settings（v2.2 §50）。
- 不整表复制共享 `ai_operations`。
- 不删除 Mac 旧 Reminder 数据（保留静态 backup）。
- 不动 `com.snotes.gale-production-pull`。
- 不扩 AI Reminder 权限。

## 小阶段与验收顺序

### 8A｜彩排（不停服）
- 用 Mac 正式 DB 的只读快照，完整跑一遍迁移到 **staging DB**：counts / schedules / occurrences / 多提醒节点 / `ai_reminder_operations` 整表 对账。
- 产出对账报告；有任何数据差异 → 停工。

### 8B｜维护窗口迁移
1. 进入维护窗口：暂停 Mac Reminder 写（关闭正式 App；`launchctl bootout` helper 防止它在窗口内改 DB）。
2. 导出 Mac Reminder：`reminder_labels`、`reminder_tables`、`reminders`、`reminder_occurrences`、`ai_reminder_operations`（整表）+ Reminder 业务 settings。
3. 导入 Cloud 正式 DB；对账（同 8A 口径）。
4. 静态备份 Mac 旧 Reminder 数据（SQLite backup API 文件，记录 hash）。

### 8C｜Cloud 正式启用
1. 创建 D2 新 topic 的 ntfy ACL；Reminder 发送 credential 仅写该 topic。
2. 正式 scheduler timer 启用；staging timer 停用。
3. iPhone 订阅新 topic；发一条真实提醒 → **锁屏 PASS**。
4. 同一 occurrence 多通知点在 Cloud **重新完整验收**（v2.2 §51）。

### 8D｜Mac 切换
1. 合入第 7 步代码；正式配置指向 Cloud；fallback 删除生效。
2. 本地 MCP Reminder 工具切到 Cloud API。
3. `build_macos_app.sh` 重建正式 App（已不含 Reminder helper）并验签、安装。

### 8E｜helper 永久退役
1. `launchctl bootout gui/$UID/com.snotes.reminder-helper`；删除 `~/Library/LaunchAgents/com.snotes.reminder-helper.plist`；必要时 `launchctl disable`。
2. 重新 build → 重新安装正式 App → 重启 / 重新登录 → 确认 helper 仍不存在、self-check 无告警。
3. 人为放回旧 plist → self-check 明显告警（再删除）。
4. 确认 Mac 旧 Reminder DB 在之后一段时间内 hash 不变（不再被任何 scheduler 修改）。

## 复用
- 第 6 步 Cloud 服务与备份、第 7 步 client 与退役代码、既有 repository 的读取。

## 必须验证
v2.2 §59 全部 32 条。重点：1–7 迁移与多通知；13「只有 Cloud DB 在变」；14 Mac 关机 scheduler 正常；15–18 时区与补跑；19–21 ntfy 私有、无 fallback、新 topic 锁屏；22 Cloud reboot 自动恢复；23–26 helper 退役四项；28–31 AI 可见性与权限；32 备份可恢复。

## S 真人参与（本步必须在场）
- 维护窗口时间（约 30–60 分钟）。
- iPhone 订阅新 topic、锁屏验收（必要时按既有经验短暂开代理完成 iOS 注册）。
- Mac 关机后等一条提醒到达。
- 重启 Mac 确认 helper 不复活。

## PASS
§59 32 条全过；对账零差异；S 真人验收 PASS。**→ 3.5-B CLOSED；Reminder 唯一 authority = Cloud。**

## 停工条件
- 迁移出现任何真实数据损失或对账差异（§60-5）。
- iPhone 锁屏推送无法通过。
- helper 重装后复活且无法从 build / package 流程根除。

回滚：8B 之后、8D 之前任何失败 → 恢复 Mac 正式 App 与 helper（bootstrap 回 plist），Cloud 正式 DB 清空，回到切换前状态；回滚步骤须在 8A 报告中预先写好。

## commit / build / deploy / push
- commit：是（迁移脚本、第 7 步合入、build / packaging 改动）。
- build：是（正式 App）。deploy：是（Cloud 正式）。push：3.5-B 封口，建议经 S 同意 push。

## 下一步
第 9 步 AI 权限隔离。

---

# 第 9 步｜AI 权限隔离（封 3.5-C）

## S 得到什么
云上的 Claude Code（UID 2001）和 Clawde（UID 2002）各自只能读自己的笔记镜像、只能通过本机接口以自己的身份用 Reminder，碰不到任何数据库文件。这是给以后「云端 AI 真正住进 GreenCloud」准备好的门禁。

## 起点与依赖
- 3.5-A、3.5-B CLOSED。

## 允许修改
- GreenCloud：文件权限 / ACL 复核与收紧、actor credential 的存放（每个 actor 用户只读自己的 credential 文件）、Cloud read MCP 与 Reminder client 的启动封装（按 actor 用户运行）。
- 仓库：`deploy/greencloud/` 文档与脚本、越权测试脚本。

## 禁止
- 不安装 / 不迁移完整 Claude Code 或 OpenClaw runtime（v2.2 §55 3.5-C）。
- 不扩任何 AI 权限。
- 不改 egress 规则（LAX fail-closed 已封工）。
- 不建 outbox（第 10 步），但把 outbox 的访问边界规则写入部署文档，供第 10 步照做。

## 工作内容
1. 权限矩阵落地并写入 `/srv/snotes/INSTALL.md`：
   ```text
   UID 2001 claude    : 读 mirror/claude-code；Reminder API(localhost) 用 claude-code credential
   UID 2002 openclaw  : 读 mirror/clawbot；Reminder API(localhost) 用 clawbot credential
   两者               : 无 reminder.db / 未来 outbox.db / 对方 mirror / 对方 credential 的任何文件权限
   ```
2. actor credential：每个 actor 一个文件，owner 为对应 actor 用户 0400（或等价方式），对方不可读。
3. Tool allowlist：Cloud read MCP 只暴露 search / get / get_note_markdown；Reminder 工具集合 = 迁移前 allowlist。
4. 越权测试脚本（可重复执行，作为以后回归）：逐条尝试越权并断言失败。

## 复用
- 第 5 步目录 / 组、第 6 步 credential → actor 映射。

## 必须验证
正向：2001 能读自己的镜像并经 localhost Reminder API 以 `claude-code` 身份操作 AI-visible 条目；2002 同理（`clawbot`）。
负向（全部必须失败）：
- 2001 读 clawbot mirror / credential；2002 反之。
- 任一 AI UID 读写 `reminder.db`、`/srv/snotes/data/*`。
- 用对方 credential（若能拿到）——应拿不到。
- AI credential 做 delete / 改 visibility / 访问 AI-invisible。
- AI UID 访问 Reminder API 的 owner 命令。

## S 真人参与
- 无必需项；可阅读越权测试输出。

## PASS
越权脚本全部通过；权限矩阵文档完整。**→ 3.5-C CLOSED。**

## 停工条件
- actor 无法在 service / filesystem / API 层真正隔离（§60-6）。

## commit / build / deploy / push
- commit：是（部署文档与测试脚本）。build：否。deploy：是。push：待 S 同意。

## 下一步
第 10 步 GreenCloud outbox + proposal。

---

# 第 10 步｜GreenCloud Notes outbox + create / append / edit proposal

## S 得到什么
云上的 Claude Code / Clawde 可以在 Mac 关机时提交「新建笔记 / 末尾追加 / 修改片段」三种提案，排队等 S 回来审；可以查自己提案的状态。正式笔记不会被云端直接改动。

## 起点与依赖
- 3.5-C CLOSED；3.5-0 的 `edit_note` 语义与方言已定型。

## 允许修改
- `src/services/gale_submission_outbox.py`（通用化）、`src/gale_remote_mcp.py` / `src/gale_submission_staging_mcp.py`（仅为共用通用化代码所需的最小改动）
- 新增 localhost outbox service（运行于 `snotes`）、Cloud proposal MCP 工具（加入 Cloud MCP）
- `deploy/greencloud/outbox/`、`tests/`

## 禁止
- **不另写第二套 outbox 协议 / 业务逻辑**（v2.2 §26、§26A）。
- **不迁走、不共用 Oracle Gale outbox DB**；Oracle 生产行为必须不变。
- AI 进程不得直接打开 outbox SQLite（§27）。
- 本轮 Cloud allowlist 只有 create / append / edit；prepend / update_note_metadata / delete 调用 → 拒绝。
- 不做 Mac 侧接收（第 11 步）。

## 小阶段与验收顺序

### 10A｜outbox 通用化（代码，先离线测试）
- 现有 `submissions` 表的 `source_actor`、`delivery_state`、`title`、`content` 均为 CHECK / NOT NULL 约束，SQLite 无法原地放宽，**通用化需一次表重建**；这是 3.5 内唯一一次。
- 通用化后必须能承载（v2.2 §26 语义）：操作类型、目标 note、expected_version、操作 payload、source_actor（含 `claude-code`、`clawbot`，保留 `gale`、`gale-staging`）、终态 accepted / rejected / conflict。
- **扩展能力（v2.2 §26C）**：以后加 prepend / update_note_metadata 时不需再次破坏性重建、不复制生命周期——即操作类型集合与操作 payload 不应被 schema 锁死在三种；Cloud 可用集合由代码 allowlist 控制；precheck 按操作类型分派。具体列设计由 Codex 结合源码定最小实现，并在报告中说明「以后加 prepend / metadata 需要改哪几处」。
- Oracle 侧：通用化代码对 Gale 现有行为保持兼容；Oracle 生产 DB 是否 / 何时升级由 Codex 评估风险后在报告中提出，**本步不升级 Oracle 生产 DB**（若必须，停工请 S 决定）。

### 10B｜GreenCloud 部署
- `/srv/snotes/data/outbox.db`（owner `snotes` 0600，WAL），纳入第 6 步每日备份。
- localhost outbox service（`snotes` 运行）：接收 AI MCP 提交，写 DB；按调用方 actor credential 固定 `source_actor`，不接受 payload 自报。
- Mac 拉取 / 回执通道：复用第 5 步窄 SSH forced-command 模式（新增子命令：export unacknowledged、acknowledge、write terminal receipt），不开公网 REST。

### 10C｜Cloud proposal 工具
- `create_note`、`append_note`、`edit_note`（Cloud 版，参数与本地一致，含 `expected_version`、`request_id`）、`get_operation_status`（仅返回自己的 request）。
- precheck（v2.2 §23）：append 允许 full / redacted；edit 仅 full；Note 在 actor mirror 中存在；expected_version == mirror `source_version`；edit 的每个 old_markdown 在 mirror `safe_markdown` 唯一命中；方言明显违规拒绝。
- 顺序：**先 idempotency，再 queue limit**；每 actor non-terminal ≥ 50 → `queue_full`。
- Cloud 不 canonicalize（最终 canonicalize 在 Mac 接受时）。

## 复用
- Gale outbox 代码、Gale remote MCP 的 submission 工具实现。
- 第 5 步 mirror 读取（precheck 读自己 actor 的 mirror）、窄 SSH 模式；第 9 步 credential 映射。

## 必须验证
正向：
- 三种 proposal 入队；duplicate request 不重复；`get_operation_status` 返回自己的状态。
- Mac 离线时可入队。
- 第 50 个之后 `queue_full`；重复 request 不占名额。

负向：
- prepend / update_note_metadata / delete → 拒绝（allowlist）。
- redacted edit → 拒绝；hidden / 他 actor note → not_found。
- stale expected_version → precheck 拒绝。
- payload 冒充他 actor 无效。
- `get_operation_status` 查不到他 actor 的 request。
- AI UID 对 `outbox.db` 无文件权限。
- **Gale 回归**：Oracle outbox 现有测试全部通过；Gale staging / production 投稿行为不变。

## S 真人参与
- 无必需项（此时 Mac 侧还不能接收；端到端在第 11 步）。

## PASS
正负向全过；Gale 回归通过；报告含「以后加 prepend / metadata 需改动点清单」。

## 停工条件
- 通用化必须改变 Oracle Gale 生产行为或立即升级 Oracle 生产 DB。
- Cloud writeback 必须绕过 application business layer（§60-8）。

## commit / build / deploy / push
- commit：是。build：否。deploy：是（GreenCloud）。push：待 S 同意。

## 下一步
第 11 步 Mac 接收。

---

# 第 11 步｜Mac pull + AI Center + accept / reject / conflict + receipt + mirror refresh（封 3.5-D）

## S 得到什么
Mac 开机后自动把云上排队的提案拉进 AI Center，和 Gale 投稿一起显示并标明来源；S 接受 / 拒绝后结果自动回传给云上的 AI；接受后云端镜像立即更新。Gale 原有投稿链一切照旧。

## 起点与依赖
- 第 10 步 PASS；第 2、3 步的接受事务与 canonicalizer 可用。

## 允许修改
- `src/snotes/application/gale_service.py`（pull 通用化为多来源）、`src/snotes/background/gale.py`
- `ai_receipt_inbox_service.py`、`ai_receipts.py`、`ai_mcp_operations.py`
- `src/snotes/ui/ai_center_dialog.py`
- 第 4 / 5 步 publish（接受后立即触发）
- `tests/`

## 禁止
- 不新增第二套 proposal 生命周期或第二张收件表；沿用 `ai_receipt_inbox`（已有 `source` 列与 `(source, external_submission_id)` / `(source, idempotency_key)` 唯一索引）。
- 不做 batch accept；不做自动合并。
- 不改变 Gale 现有 pull / ack 行为与 UI 表现（除显示来源外）。

## 小阶段与验收顺序

### 11A｜双来源 pull
- pull 支持两个来源：Oracle Gale、GreenCloud；每条导入记录 `source` 区分来源。
- 去重 / 幂等按来源隔离（沿用现有复合唯一索引）。
- Oracle 链路：代码路径与行为不变；先跑 Gale 回归再继续。

### 11B｜AI Center
- 显示来源（Oracle Gale / GreenCloud）与 actor。
- create / append / edit 三类 Cloud 提案复用第 2、3 步本地接受事务：Note 存在、未删、actor 当前仍有权限（edit 仍 full）、expected_version 与当前 token 匹配、request_id 未执行、edits 仍唯一命中、canonicalize、合法、非空、revision + updated_at bump、history。
- 失败 → 正文零写入，rejected / conflict。
- 按 actor 批量 reject（限定单一来源，不跨来源误伤）。

### 11C｜terminal receipt 回传
- 决定后生成 terminal receipt（accepted / rejected / conflict），**原路**写回对应来源（GreenCloud 经第 10 步窄 SSH 子命令）；失败可重试，幂等。
- Gale 来源的回执行为保持现状（若 Gale 原本无 terminal receipt，本步不为 Gale 新增）。

### 11D｜accepted → 立即 mirror publish
- 接受成功后立即触发对应 actor 镜像发布（不等 5 分钟）；下一次 Cloud read 读到新 `current.db`。

## 复用
- `pull_once` 结构、`import_gale_pending`、接受事务、第 5 步 publish、第 10 步 SSH 子命令。

## 必须验证
v2.2 §58 全部 26 条。重点：
- 5：Cloud create 不直接入正式库；8：create accept 仅生成一篇。
- 11 / 12：redacted append ✅、redacted edit ❌。
- 13：canonicalize 后才落库（ZERO DIFF）。
- 15：reject 后正文零变化。
- 16 / 17：terminal receipt 回 Cloud，`get_operation_status` 反映终态。
- 18 / 19：accepted 后立即刷新，下一次 Cloud read 读新文件。
- 21–24：来源保留、回执原路、两来源同外部 ID 不误去重、**现有 Gale 投稿链回归 PASS**。
- 25：Cloud prepend / update_note_metadata 仍被拒绝。
- 26：Oracle Gale outbox DB 未被迁走或共用。
- 权限撤销：提案提交后撤销 actor 对该 Note 的权限 → 接受时 rejected / conflict。

## S 真人参与（本步必须）
端到端，约 30 分钟：
1. 关掉 Mac（或断开 pull）；Codex 以 claude-code 身份在云上提交 create / append / edit 各一。
2. 开 Mac → AI Center 看到三条，来源 GreenCloud → 看 diff → 接受两条、拒绝一条。
3. 云上 `get_operation_status` 看到对应终态；云上读到新内容。
4. 同时让 Gale 投一条 → AI Center 显示来源 Oracle Gale → 接受 → 与以前一致。
5. 制造一次冲突（提交后 S 先改笔记）→ 接受显示冲突。

## PASS
§58 26 条全过；S 端到端 PASS；Gale 回归 PASS；App 重建验签。**→ 3.5-D CLOSED；GreenCloud 3.5 竣工。**

## 停工条件
- 双来源必须改变 Gale 现有行为才能实现。
- 需要第二张收件表或第二套生命周期。
- receipt 无法保证原路返回。

## commit / build / deploy / push
- commit：是。build：是（正式 App）。deploy：是（GreenCloud 接收子命令如有调整）。
- push：3.5-D / 3.5 竣工，建议经 S 同意 push。

## 下一步
**3.5-D 后续必做：Cloud prepend + update_note_metadata**（另开施工单）。
- 复用第 10 步报告中的「改动点清单」、本地 prepend / metadata 接受事务（第 2 步）、第 11 步双来源与 receipt。
- metadata 仅限现有 allowlist；不得修改 AI visibility / actor targets。
- delete 继续不做。
