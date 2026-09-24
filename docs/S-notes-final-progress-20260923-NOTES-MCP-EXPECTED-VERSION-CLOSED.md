# S-notes 最终进度书｜Markdown Production Closed → GreenCloud Next

**日期：2026-09-22；更新至 2026-09-23**  
**状态：Markdown Retrofit Package 1–4 全部 CLOSED；3.5-0 Reminder 已 CLOSED；3.5-0 Notes 的 `expected_version` MCP contract 已 CLOSED；下一任务为 `get_note_markdown` → dialect → canonicalizer → `edit_note`。**  
**正式仓库：** `/Users/s/Documents/项目/S-notes`  
**正式 App：** `/Users/s/Documents/项目/S-notes/dist/S-notes.app`

---

# 0. 今天为什么格外与众不同

今天这轮不是普通 UI 修补，也不是一次孤立的编辑器替换。

最初动机很直接：

> S 想继续推进「AI 可以修改已有笔记」；但旧正文是 Qt HTML / 富文本，AI 要对已有正文做 append / replace / proposal 时，格式语义、HTML 编辑、脱敏、diff、远程同步都很别扭。

同时，S 自己实际并不重度依赖传统富文本。真正日常需要的是：

- 标题；
- 粗体 / 斜体 / 删除线；
- 列表；
- 引用；
- code；
- link；
- 少量字号层级；
- 看起来仍然是正常的 WYSIWYG 编辑体验。

所以今天真正做的事情是：

```text
Qt HTML / Rich Text 正文
        ↓
SQLite 中的 Markdown 单一正文真身
        ↓
本地 WYSIWYG 编辑
        ↓
AI 读写 / proposal / diff 更自然
        ↓
GreenCloud / 云端 AI / 既有笔记修改能力更容易继续施工
```

因此这次 Markdown Retrofit 不是“换个编辑器”。

它是 S-notes 从：

```text
本地个人笔记 App
```

继续向：

```text
本地正式知识库
+ 云端安全镜像
+ AI 可读
+ AI 可投稿
+ AI 可对已有笔记提出修改
+ Reminder 云端统一状态
```

过渡的一块关键地基。

---

# 1. 最终 Git / Package 状态

最终 source revision：

```text
FINAL_SOURCE=f2e280fba290805c11ae58e32f8939964cc665a7
MAIN=f2e280fba290805c11ae58e32f8939964cc665a7
ORIGIN_MAIN=f2e280fba290805c11ae58e32f8939964cc665a7
WORKTREE=CLEAN
```

最终 GitHub `main` 已独立核实指向：

```text
f2e280fba290805c11ae58e32f8939964cc665a7
```

提交链：

```text
8efd994e...  retrofit 开工前基线
    ↓
a4ec210f6d6534aac767f77bc1da5d3d86e0a6ef
Add Markdown editor component
    ↓
60125e883bd638967a8189c6eb90acff33bc7500
Cut over note body runtime to Markdown
    ↓
f2e280fba290805c11ae58e32f8939964cc665a7
Finalize real-data Markdown migration rehearsal
```

Package 状态：

```text
PACKAGE_1=CLOSED
PACKAGE_2=CLOSED
PACKAGE_3=CLOSED
PACKAGE_4=CLOSED
PACKAGE_4_UI_HUMAN_SMOKE=PASS
PACKAGE_4_HUMAN_GATE=PASS
BLOCKER=NONE
```

Package 4 没有为了“封口”制造新的空 commit。

它是 production operational cutover，因此最终记账应理解为：

```text
PACKAGE_4_SOURCE_COMMIT=NONE
PACKAGE_4_FINAL_SOURCE=f2e280f...
CLOSURE_TYPE=FAST_FORWARD + OPERATIONAL CLOSURE
```

Package 4 task branch保留。

---

# 2. Markdown 正文架构正式落地

## 2.1 唯一正文真身

当前 active R2：

```text
BODY_AUTHORITY=SQLITE_MARKDOWN
```

正式正文列：

```text
notes.content_markdown
```

正式 schema marker：

```text
schema_meta.body_format=markdown
```

旧 active 正文列已经退出：

```text
content_html
body_rich_text
```

不再允许：

```text
HTML canonical body
editor JSON canonical body
Markdown + HTML 双 runtime
自动 migration
filesystem .md 作为事实源
```

硬规则：

> Markdown 是正文唯一 authority；其他格式全部只能是派生结果。

## 2.2 derived plain text

搜索、关联、MCP、Gale、脱敏等需要纯文本的地方统一走：

```text
content_markdown
→ markdown_to_plain_text()
→ derived plain text
```

`markdown_to_plain_text()` 是纯函数。

缓存只允许：

```text
process memory
key = (note_id, revision)
```

不允许：

```text
SQLite content_text
磁盘派生正文缓存
第二正文 representation
```

revision 改变即失效。

---

# 3. 正式编辑器

桌面壳仍是：

```text
PySide6
```

正文编辑区域改为：

```text
QWebEngineView
→ QtWebEngine
→ Chromium
→ local bundled HTML/CSS/JS
→ Milkdown
→ ProseMirror
```

这里的 Chromium 是 QtWebEngine 内核，不是 Chrome App。

运行时：

```text
NO CDN
NO Node server
NO external web dependency
```

Node / Vite 只参与 build。

---

# 4. Python ↔ Web 编辑器契约

正式 bridge：

## Python → Web

```text
setMarkdown(markdown, load_id)
prepareClose()
setTheme(theme)
setReadOnly(read_only)
```

## Web → Python

```text
editorReady()
loadComplete(load_id, markdown)
loadFailed(load_id, reason)
markdownChanged(markdown, load_id)
closeReady()
```

关键语义：

```text
setMarkdown(A, load_id)
→ Web parser 装载
→ serializer 得到 canonical A'
→ 无条件 loadComplete(load_id, A')
→ Python 以 A' 建 baseline
```

`loadComplete`：

```text
不是 edit
不置 dirty
不 bump revision
```

旧 `load_id` 回调一律丢弃。

load failure：

```text
fail closed
禁止保存
```

关闭前：

```text
prepareClose
→ composition flush
→ 使用 Python 已缓存 latest Markdown
→ closeReady
```

---

# 5. 编辑体验保留范围

默认正文：

```text
17px
line-height=1.9
paragraph gap=11px
```

保留字号：

```text
15
17
20
```

特殊 Markdown directive：

```text
:size[text]{size="15"}
:size[text]{size="20"}
```

标题：

```text
H1
H2
H3
```

正文能力包括：

```text
Bold
Italic
Strike
Bullet list
Ordered list
Quote
Inline code
Code block
Link
Undo / Redo
Find / Replace
Raw Markdown read-only view
```

主题继续复用 S-notes 现有 ThemeTokens。

外部链接继续交给系统浏览器。

文件拖入不作为正文事实源。

---

# 6. Package 1｜Markdown Editor Component

最终 commit：

```text
a4ec210f6d6534aac767f77bc1da5d3d86e0a6ef
```

完成：

- Milkdown WYSIWYG；
- QWebEngine local bundle；
- Markdown bridge；
- loadComplete / loadFailed；
- stale load_id 防护；
- IME；
- undo / redo；
- theme；
- read-only；
- size marks；
- list marker presentation；
- find / replace；
- close handshake。

Package 1 不碰正式 DB。

---

# 7. Package 2｜正文 runtime 全切 Markdown

最终 commit：

```text
60125e883bd638967a8189c6eb90acff33bc7500
```

完成：

```text
model
ports
mapper
repository
ordinary save
trash select
AI create
AI append
AI prepend
receipt accept
audit snapshot
ordinary export
AI Safe Export
MCP
Yan
Gale
search
relations
```

全部从 active HTML 正文切出。

fresh DB 从一开始就是 Markdown schema。

旧库不能靠普通启动偷偷 migration。

migration 只能显式 CLI 执行。

---

# 8. AI / Export / MCP / Gale 的新正文契约

## 普通 Markdown export

```text
content_markdown
→ raw Markdown
```

保留格式。

## 普通 JSON export

包含：

```text
content_markdown
content_text (即时派生)
```

## AI Safe Export

```text
content_markdown
→ markdown_to_plain_text
→ existing redaction
→ safe plain text
→ .md container
```

AI Safe Export 不承诺 Markdown 格式保真。

它的目标是：

```text
安全
可读
不绕过 redaction
```

## MCP / Gale read

输出：

```text
plain text
```

## AI write

输入：

```text
Markdown
```

这点是后续「AI 修改已有笔记」的重要地基：

> AI 不再需要生成或操作 Qt HTML；可以直接在 Markdown 语义上创建、追加、提出替换或修改建议。

---

# 9. Package 3｜真实数据迁移 rehearsal

最终 commit：

```text
f2e280fba290805c11ae58e32f8939964cc665a7
```

Package 3 对正式数据副本做真实迁移 rehearsal。

当时正式基线：

```text
NOTE_COUNT=84
BODY_BYTES=2,983,721
INTEGRITY=PASS
```

migration Gate：

```text
VISIBLE_TEXT=84/84 PASS
PARAGRAPH_SEGMENTATION=84/84 PASS
STRUCTURE_SEMANTICS=84/84 PASS
MILKDOWN_ROUNDTRIP=84/84 PASS
METADATA=PASS
REVISION=PASS
UPDATED_AT=PASS
BUSINESS_TABLES=PASS
INTEGRITY=PASS
```

没有：

```text
images
tables
unknown tags
```

---

# 10. 旧 Qt HTML → Markdown 迁移原则

没有使用旧通用 `html_to_markdown()` 当迁移真理。

而是单独建立：

```text
migration-only legacy Qt HTML → Markdown converter
```

转换覆盖：

```text
heading
bold
italic
strike
link
list
quote
code
20px
plain-text line breaks
```

已知无法完整映射的视觉信息显式列为 loss，不伪装成无损。

真实数据中观察到：

```text
font family loss: 70 notes
underline loss: 1 note
nonstandard font size loss: 3 notes
```

但：

```text
visible text loss = 0
```

---

# 11. Pixel 长笔记段落问题

真人 migration rehearsal 暴露了一个旧 Qt 文档结构特点：

某些笔记从用户视觉上是很多段，但 Qt 实际存成：

```text
一个 QTextBlock
+ 大量 internal line breaks
```

典型是 Pixel 网络配置笔记，存在：

```text
164 internal linebreaks
```

最终迁移规则定为：

```text
单个 internal linebreak
→ 保留为同一 Markdown paragraph 内的 hard break

出现至少一个 blank line
→ Markdown paragraph / block boundary
```

同时：

```text
Qt 没有结构语义的字面 # / ## / - / + / 1. / >
→ 必须 escape
→ 不允许 heuristic 自动升级成 heading/list/quote
```

最终该 Pixel 文档得到：

```text
46 independent paragraphs
```

真人编辑体验正常。

---

# 12. Package 3 真人 UI 修复

真人试用时先后处理：

## 12.1 toolbar 跟正文一起滚动

已修复。

toolbar 固定，正文单独滚动。

## 12.2 note switch 标题 / 正文错位

曾出现：

```text
新标题先出现
旧正文暂时残留
```

修成 two-phase load / commit：

```text
pending note
→ Web load success
→ title / metadata / body / save target 一起 commit
```

切换期间保持旧 note 一致状态。

## 12.3 body viewport border

新编辑器一度缺少旧 UI 的完整浅色正文边框。

已恢复：

```text
BODY_VIEWPORT_BORDER=PASS
```

---

# 13. note switching 性能大幅修复

真人首先观察到：

```text
点击另一篇 note
→ 正文空白 1–2 秒
```

Claude / Codex 定位后发现主因：

> Python 在 `setMarkdown()` 前把 QWebEngineView 隐藏，Chromium 把隐藏页面中的 30ms timer throttling 到接近 1 秒。

并且旧实现每次切换还重建 Milkdown / ProseMirror。

修复：

```text
不再正常切换时 hide/show WebView
复用单个 Milkdown / ProseMirror instance
parser → EditorState.create → view.updateState
history/plugin state 每文档重建
```

同时：

```text
finishComposition(true)
只在真的 composing 时等待 30ms
```

性能：

```text
BEFORE median total ≈ 835ms
AFTER median total ≈ 62ms

A→B→A:
1776ms → 75ms

small → large 82KB:
1063ms → 449ms

Milkdown median:
≈785ms → ≈6ms
```

---

# 14. cold first-note 进一步修复

修完 warm switch 后，首次打开笔记仍有 1–2 秒。

最终定位：

```text
editorReady ≈ 428ms
click ≈ 495ms
setMarkdown ≈ 1525ms
```

即 WebEngine 已 ready，真正延迟发生在 Python 还没发正文。

根因：

> 首次选择根本没有旧 committed note，却误走 `prepareClose → finishComposition → 30ms timer`；当时 WebView 又处于隐藏状态，于是 Chromium 仍把 timer throttle 到约 1 秒。

修复：

```text
if no committed note:
    first selection skips useless composition barrier
```

结果：

```text
cold first note:
≈1080ms → ≈31ms

82KB large first note:
≈604ms
```

真人 Gate PASS。

---

# 15. Package 4｜正式 Production Cutover

Package 4 正式开始前执行：

```text
停止 S-notes GUI
停止 Reminder helper
停止 Gale production pull
停止正式 local MCP
确认无正式 DB writer
```

保留 rollback：

```text
pre-migration DB backup
old formal App backup
helper state
```

正式 migration：

> **只执行了一次。禁止重复执行。**

migration 后：

```text
body_format=markdown
legacy active body columns removed
integrity_check=ok
foreign_key_check=0
note count at migration instant: 84 → 84
critical business row counts unchanged
UUID diff=0
revision diff=0
metadata diff=0
```

注意：

> 之后 S 已继续正常创建 / 删除笔记，所以当前 note count 不再拿来反推 migration gate。真正的数据迁移 gate 是 migration 当刻的 84 → 84。

---

# 16. 正式 App

正式路径：

```text
/Users/s/Documents/项目/S-notes/dist/S-notes.app
```

新 App：

```text
production Web bundle PASS
arm64 build PASS
codesign PASS
formal cutover PASS
```

人工 UI smoke：

```text
Markdown edit PASS
save PASS
quit / reopen PASS
Raw Markdown read-only PASS
search PASS
分类 / 子分类 PASS
tags PASS
trash PASS
Reminder Center PASS
AI Center PASS
multi-note switch PASS
large note PASS
```

最终：

```text
PACKAGE_4_UI_HUMAN_SMOKE=PASS
PACKAGE_4_HUMAN_GATE=PASS
```

---

# 17. MCP / Gale / Reminder production 收尾

最终 CLI production smoke：

```text
MCP_SEARCH_GET=PASS
MCP_MARKDOWN_PLAIN_TEXT=PASS

GALE_MIRROR_REBUILD=PASS
GALE_VALIDATION=PASS
GALE_PRODUCTION_PUBLISH=PASS
GALE_PRODUCTION_SEARCH_GET=PASS

REMINDER_HELPER=ACTIVE
GALE_PULL_HELPER=ACTIVE
```

正式 Gale mirror 已按 Markdown production DB 重建并发布。

没有改变：

```text
Tunnel
Auth0
Caddy
DNS
Oracle architecture
```

Reminder helper 恢复后正常等待 schedule，没有为了 Gate 强发真实 ntfy。

---

# 18. 120GB Application Memory 事故

Package 4 cutover 中间发生过一次异常严重但最终不可复现的事故：

```text
S-notes application memory runaway
≈88GB → 120GB 级
swap exhaustion
macOS application memory warning
```

真实发生两次。

事故现场共同变量包括：

```text
Codex Computer Use
Accessibility
QWebEngine editor
```

但不能据此判定因果。

后续有控制地排除：

```text
isolated packaged editor
full MainWindow
loaded note
A→B note switch
84-note dataset
packaged runtime
QWebEngine resource auto-discovery
CUA bind
CUA + loaded Markdown editor
CUA + 10 AX tree reads
```

最后一次受控 CUA loaded-editor test：

```text
RSS before note load = 401.1 MiB
RSS after note load = 440.5 MiB
peak = 458.1 MiB
10 AX reads
RSS stable
runaway = NO
```

因此正式结论：

```text
INCIDENT_REAL=YES
ROOT_CAUSE=UNKNOWN
REPRODUCIBLE=NO
CUA_ACCESSIBILITY_CAUSALITY=UNPROVEN
PACKAGE_4_BLOCKER=NO
```

决定：

> 不再为了不可复现事件继续烧施工额度；如果以后真实日用再次出现类似 runaway，再以新现场证据单独开 incident。

---

# 19. 四项小维护尾巴｜非当前 blocker

本轮 Claude 顺手暴露 / 确认了四类小问题，全部记入后续维护池，不影响 Markdown production closure。

## 19.1 `prepare_package3_candidate.py` 的 `NoteLocation` 契约不匹配

现有旧 helper：

```text
scripts/prepare_package3_candidate.py
```

存在 `NoteLocation` contract mismatch。

当前它不是正式 production 主路径。

后续处理原则：

```text
如果 helper 已退休
→ 不修，正式 retire

如果仍要保留
→ 修 contract
→ 只做最小 smoke
```

不要为了历史 helper 重开一轮架构施工。

## 19.2 全仓单进程 unittest + QtWebEngine teardown 偶发 SIGSEGV

现象：

```text
full repo unittest
single process
QtWebEngine / GPU context teardown
exit 139 / SIGSEGV
```

目前：

```text
targeted suites PASS
package suites PASS
candidate / formal App 真人运行正常
```

所以当前定性：

```text
TEST_INFRA_LIMITATION
NOT_PRODUCT_BLOCKER
```

只有未来需要稳定 all-in-one CI，或正式 App 也出现同类退出 crash 时，才重新处理。

## 19.3 `test_package3_ui` 中 Reminder 相关既有失败

Claude 在本轮异步 load 改造时确认：

```text
test_package3_ui
存在 2 个 reminder-related pre-existing failures
```

至少包含：

```text
test_note_selection_to_reminder_click_rebuilds_sidebar_without_crash
```

这些失败在 Markdown switching 改动前就存在，不属于本轮 regression。

本轮只为新的 async load contract 增加必要 wait，不借机扩大范围修 Reminder 历史测试。

后续 Reminder / GreenCloud 施工时可一并重新核对并修正测试契约。

## 19.4 append smoke 的单换行 / 空行契约不一致

2026-09-23 在收口 `expected_version` MCP schema 时，`test_package5_local_mcp_protocol.py` 中：

```text
test_normal_create_get_append_and_operation_status_smoke
```

仍有一个既有失败：

```text
append 前：base
追加：-end

测试期望：
base\n-end

当前 runtime：
base\n\n-end
```

Claude 已把本次 schema 改动暂存，并在原版代码上复跑；同一失败仍可复现，因此：

```text
PRE_EXISTING=YES
REGRESSION_FROM_C110ADA=NO
CURRENT_BLOCKER=NO
```

当前只记录事实，不提前判定谁错。它很可能属于 Markdown cutover 之后尚未正式收口的 **append / canonicalization contract**：追加一段 Markdown 时，旧正文与新正文之间究竟应按“同一段内单换行”还是“新段落空一行”拼接，必须与 Milkdown canonical Markdown 的最终语义一致。

处理时点：

```text
3.5-0 Notes Local Finalization
→ canonicalizer / append contract
```

到该步骤再决定：

- runtime 行为应改；或
- 旧 smoke 期望应更新；
- 并以 `AI output → canonicalize → DB → Milkdown serialize → ZERO DIFF` 为最终 Gate。

不要为了这条旧失败回滚 `c110ada`，也不要在 `get_note_markdown` 之前单独开一轮修复工程。

---

# 20. 为什么 Markdown 改造必须马上衔接 GreenCloud

今天开工的最初原因，本来就不是“我想把富文本换成 Markdown”。

真正目的之一是：

> **让 AI 能更自然、安全地修改已有笔记。**

旧 Qt HTML 模式下，AI 要修改现有笔记意味着：

```text
AI 理解 Qt HTML
→ 尽量不破坏 span/style/block
→ HTML patch
→ 富文本重新渲染
→ diff 很难看
→ 云端传输和 conflict 更麻烦
```

现在变成：

```text
Markdown
→ AI 原生理解
→ Markdown proposal / append / replace
→ revision conflict
→ human review
→ accept
```

这和下一阶段 S-notes 上云是同一条线。

---

# 21. GreenCloud 当前已经确定的产品边界

## 21.1 Notes：Mac 仍是老大

继续坚持：

```text
Mac S-notes
= 正式笔记本体
= 正式数据库
= 唯一最终事实源
```

GreenCloud：

```text
= 给 AI 使用的安全镜像
= 云端读取入口
= 投稿 / 修改建议中转
≠ Notes 正式事实源
```

不做：

```text
Mac / GreenCloud 双主 Notes
```

云端 AI 新增笔记继续：

```text
AI
→ cloud outbox / pending
→ Mac 拉回
→ S 审阅
→ accept
→ formal note
```

---

# 22. Reminder：方向与 Notes 不同

Reminder 已确定：

```text
GREEN CLOUD = 正式 Reminder 状态与执行端
```

目标：

```text
Reminder 不再依赖 Mac 开机
GreenCloud 到点执行
→ ntfy
Mac S-notes 拉取 / 显示 / 管理
```

未来各 AI：

```text
Clawde
cloud headless Claude Code
ChatGPT Web
Mac Codex
Mac Claude Code
```

原则上都操作：

```text
同一份 cloud Reminder
```

不再搞两套 Reminder state。

---

# 23. GreenCloud 背景

计划把：

```text
Clawde
```

从 Mac 搬到：

```text
GreenCloud VPS
```

目的：

> 不再要求 Mac 经常开机，才能在微信中持续和 Clawde 聊天、访问 Reminder / S-notes AI 能力。

未来还可能继续搬：

```text
headless Claude Code
```

并让：

```text
ChatGPT Web
```

通过同一套云端安全入口访问 Notes mirror / Reminder。

---

# 24. 下一阶段最高优先级｜已有笔记修改 proposal

Markdown production 完成后，原先长期设计里的 R2：

```text
AI 对旧笔记提出修改建议
```

可以正式重新提上施工线。

建议第一阶段仍保持：

```text
proposal
≠ direct overwrite
```

优先开放：

```text
append proposal
replace selected section proposal
small metadata proposal
```

继续保留：

```text
note_id
expected revision / version token
before
proposed after
actor
reason / source
timestamp
```

处理：

```text
AI proposal
→ pending
→ S 查看 diff
→ accept / discard
→ accept 时检查 revision
→ conflict 则重新 review
```

现在正文已经是 Markdown，这套方案不再需要 HTML surgery。

---

# 25. AI 修改已有笔记与 GreenCloud 的推荐连接方式

下一阶段建议思路：

```text
Mac formal note
    ↓ safe publish
GreenCloud AI mirror
    ↓
AI reads Markdown-derived safe view
    ↓
AI creates modification proposal
    ↓
GreenCloud proposal outbox
    ↓
Mac pull helper
    ↓
AI Center / dedicated proposal UI
    ↓
S accept / discard
    ↓
formal Markdown note updated
```

关键边界：

```text
Cloud AI 不直接 update formal SQLite
```

即使未来权限放宽，也应该首先复用：

```text
NoteService / existing application business layer
revision optimistic lock
AI actor
AI visibility
AI target
audit / receipt / proposal lifecycle
```

禁止绕过业务层直写 SQLite。

---

# 26. Reminder MCP 上云前需要补齐

当前已有：

```text
list_reminder_labels
list_reminder_tables
get_reminder_table
list_reminder_items
get_reminder_item
create_reminder_label
create_reminder_table
update_reminder_table
create_reminder_item
update_reminder_item
complete_reminder_item
pause_reminder_item
resume_reminder_item
```

明显缺口：

```text
update_reminder_label
delete_reminder_item
delete_reminder_table
delete_reminder_label
```

上云前应补齐日常管理闭环。

不然会出现：

```text
AI 会新增
AI 会修改
AI 不会收拾
```

长期一定堆垃圾。

---

# 27. Reminder 产品形态已拍板

2026-09-22 的 3.5 v2.1 FINAL 已把 Reminder 产品边界定死；本节取代旧版“仍未最终拍板”。

正式模型：

```text
GreenCloud
= 唯一正式 Reminder DB
= 唯一 scheduler
= ntfy executor

Mac S-notes
= owner UI
= 远程管理客户端
= 离线兼容 snapshot
```

因此 3.5 不再并行探索“把 Reminder 独立成另一套产品”。

硬边界继续保持：

```text
一份正式 Reminder state
一个 scheduler
Mac / GreenCloud 不双主
AI 不直接写 SQLite
```

Reminder 真正切 Cloud 发生在 **3.5-B**；在那之前，Mac 本地实现只用于把业务语义先收口并验准。

---

# 28. S-notes 后续 Queue｜2026-09-23 新基线

## P0｜当前下一任务：3.5-0 Notes Local Finalization

Reminder 小修已经关闭，下一刀只做 Notes MCP / Markdown write：

```text
1. expected_version-only contract      CLOSED @ c110ada
2. get_note_markdown                   NEXT
3. snotes-markdown-v1 dialect contract
4. Markdown canonicalizer
5. edit_note
6. append permission recheck
7. revision / updated_at / version contract
8. local round-trip gate
9. S 真人验收
```

### 28.1 `expected_version` MCP contract｜CLOSED

2026-09-23 已提交：

```text
c110ada Unify note-edit MCP schema on expected_version (3.5-0)
```

改动范围仅 3 个文件：

```text
src/snotes/transport/mcp/tools.py
docs/MCP_SERVER_RUNBOOK.md
tests/test_package5_local_mcp_protocol.py
```

正式结果：

```text
MCP_TOOL_COUNT=23
prepend_note=VISIBLE
append_note=VISIBLE
update_note_metadata=VISIBLE
expected_version=REQUIRED
expected_updated_at=NOT_EXPOSED_TO_MCP
TOP_LEVEL_ANYOF_ONEOF_ALLOF=NONE
APPLICATION_COMPATIBILITY_LAYER=UNCHANGED
WORKTREE=CLEAN
```

Claude Code 重开会话后已真人确认 23 个工具全部可见，三个此前被隐藏的修改工具恢复。未为了验 schema 向 S 的正式笔记写入测试数据。

`prepend_note / append_note / update_note_metadata` 的 MCP discovery 问题因此正式 CLOSED，后续**不要重复施工**。

3.5-0 Notes 通过以后，才依次进入：

```text
3.5-A  Notes Cloud Read
3.5-B  Reminder Cloud Authority
3.5-C  AI Access Boundary Ready
3.5-D  Existing R1B Outbox → Notes Cloud Writeback
```

其中 3.5-B 才做 Reminder 正式端到端通知验收：

```text
GreenCloud Reminder scheduler
→ https://ntfy.ke-den.de
→ 新 s-* Reminder topic
→ iPhone 锁屏真人验收
```

不为 3.5-0 恢复旧 public `ntfy.sh` topic。

## 3.5 完成后

继续 GreenCloud 总施工线：

```text
/yu 迁入 Claude workspace
→ Claude Code 入住
→ OpenClaw / 砚入住
→ LET 独立账号切换
→ reboot 收口
→ 淘宝 A2A 条件测试
```

---

# 29. 2026-09-23｜3.5-0 Reminder CLOSED

本轮完成了 Reminder 上云前必须先在 Mac 本地收口的业务语义与 UI 小修。

## 29.1 Git / App 状态

本轮提交：

```text
67c6022fd51ed8c34a7af5e3d18096a32dcd11f1
```

提交后：

```text
WORKTREE=CLEAN
LOCAL_MAIN=67c6022fd51ed8c34a7af5e3d18096a32dcd11f1
ORIGIN_MAIN=f2e280fba290805c11ae58e32f8939964cc665a7
MAIN_AHEAD_ORIGIN=1
PUSHED=NO
```

正式 App 已按现有打包流程重建、签名检查通过，并更新到：

```text
/Users/s/Documents/项目/S-notes/dist/S-notes.app
```

正式数据保留；没有用临时 DB 替换 production 数据。

## 29.2 多次提醒

新增：

```text
multi_notify
```

旧数据默认：

```text
OFF
```

行为：

```text
OFF
→ 保留普通单次通知

ON
→ deadline/start anchor - 7 days
→ anchor - 3 days
→ anchor - 1 day
→ anchor same day
```

每个 occurrence 的每个 notification point 独立去重；同一个点不会重复成功通知。

## 29.3 多提醒锚点最终语义

真人验收阶段发现原实现错误地围绕“什么时候开始管”计算提前节点，已做最小修正。

最终规则：

```text
如果有「最晚处理日」
→ anchor = deadline

没有「最晚处理日」
→ anchor = start date
```

验证例：

```text
start = 2026-08-27
deadline = 2026-10-03
multi_notify = ON
```

实际节点：

```text
2026-09-26
2026-09-30
2026-10-02
2026-10-03
```

语义固定为：

```text
什么时候开始管 = 进入关注 / 待处理范围
最晚处理日     = deadline
```

因此只要 deadline 存在，提前提醒必须围绕 deadline。

## 29.4 周期推进方式

新增：

```text
recurrence_mode
```

只对“每 N 天”这种相对间隔周期开放两种模式：

```text
A. 从原到期日继续计算
B. 从实际完成日重新计算
```

例：

```text
原到期日 = 2026-10-01
周期     = 90 days
实际完成 = 2026-09-25
```

结果：

```text
A → 2026-12-30
B → 2026-12-24
```

固定月日、每年固定日、农历等 calendar recurrence 继续按原日历规则推进，不因实际完成日漂移；UI 也不为这些规则显示该推进方式。

旧数据保持原有周期行为，不因为 schema 新增而静默改变日期。

## 29.5 逾期语义

明确**不实现 SubsTracker 式逾期追催**：

```text
不每 X 小时重复 push
不每天追着发
不新增 overdue notification loop
```

到期未完成时：

```text
Reminder 保持未完成
→ 左上角 badge 继续计数
→ S 点完成后才结束当前 occurrence
```

周期任务完成后，再按 `recurrence_mode` 生成下一次。

## 29.6 AI 与权限

现有 `update_reminder_item` 已可修改：

```text
multi_notify
```

但没有借此扩大 Reminder AI 权限模型；原 actor / visibility / allowlist 边界保持不变。

## 29.7 Reminder 表格 UI bug

修复长期存在的 Reminder Center 首次布局问题：

旧现象：

```text
首次切入 Reminder
→ 备注列右侧到滚动条之间留大片空白
→ 点击任意 Reminder 后才重新铺满
```

修复后：

```text
首次进入即填满 viewport
点击行前后列宽不跳
窗口 resize 正常
离开再回来仍正常
```

实现复用现有列宽计算，只修正 viewport layout / resize 的触发时机，没有重做表格。

## 29.8 验收

最终：

```text
REMINDER_350_TARGETED_TESTS=59 PASS
REMINDER_UI_HUMAN_GATE=PASS
REMINDER_350=CLOSED
```

覆盖：

```text
multi_notify ON/OFF
7 / 3 / 1 / same-day calculation
deadline-first + start fallback
notification dedupe
recurrence_mode 两种推进
calendar recurrence 不漂移
AI 修改开关 + 未授权拒绝
Reminder UI / column resize
```

真实手机 push **不在 3.5-0 强行验**。

原因：iPhone ntfy App 重装后旧 Reminder topic 已消失，而 Mac local helper 仍属于即将退役的 public `ntfy.sh` 路径。为一个马上淘汰的链路恢复旧 topic 没有价值。

正式 push 真人 Gate 延后至 **3.5-B**，届时只验真正生产链：

```text
GreenCloud scheduler
→ self-hosted ntfy.ke-den.de
→ new authenticated s-* topic
→ iPhone lock-screen push
```

---

# 30. 以后不要重新做的事

以下已经完成，不要从旧进度书里重新捞出来：

```text
Markdown 是否替代 Qt HTML
Markdown editor 原型
WYSIWYG 可行性
content_markdown single authority
legacy content_html active runtime
body_rich_text active runtime
Markdown migration
real-data rehearsal
production migration
formal App cutover
Gale mirror rebuild for Markdown
MCP Markdown plain-text read
Reminder helper restore
Package 1–4

3.5-0 Reminder multi_notify
3.5-0 Reminder recurrence_mode
Reminder deadline-first notification anchor
Reminder 表格首次进入列宽 bug
Reminder 3.5-0 targeted baseline
```

另外：

```text
prepend_note / append_note / update_note_metadata
MCP discovery 问题已正式 CLOSED
commit=c110ada
23 tools visible after Claude Code reconnect
```

下一任务不要重复施工这一项；直接进入 `get_note_markdown`。

特别禁止未来施工 agent 因为看到旧文档重新提出：

```text
HTML + Markdown dual-write
automatic migration
filesystem Markdown source of truth
长期保留 content_html fallback
Mac / GreenCloud 双主 Reminder
为 3.5-0 恢复旧 public ntfy topic
```

---

# 31. 当前正式基线

```text
S_NOTES_MARKDOWN_RETROFIT=CLOSED
PACKAGE_1=CLOSED
PACKAGE_2=CLOSED
PACKAGE_3=CLOSED
PACKAGE_4=CLOSED

MARKDOWN_PRODUCTION_FINAL=f2e280fba290805c11ae58e32f8939964cc665a7
CURRENT_LOCAL_MAIN=c110ada
LAST_VERIFIED_ORIGIN_MAIN=f2e280fba290805c11ae58e32f8939964cc665a7
REMOTE_STATUS_AFTER_C110ADA=NOT_REVERIFIED
WORKTREE=CLEAN

BODY_AUTHORITY=SQLITE_MARKDOWN
SCHEMA_MARKER=markdown
ACTIVE_HTML_BODY_RUNTIME=NO
DUAL_FORMAT_RUNTIME=NO
AUTO_MIGRATION=NO

FORMAL_APP=/Users/s/Documents/项目/S-notes/dist/S-notes.app

MCP_MARKDOWN_READ=PASS
GALE_MARKDOWN_MIRROR=PASS
GALE_PRODUCTION_PUBLISH=PASS
REMINDER_HELPER=ACTIVE
GALE_PULL_HELPER=ACTIVE

PACKAGE_4_UI_HUMAN_SMOKE=PASS
PACKAGE_4_HUMAN_GATE=PASS

REMINDER_350=CLOSED
REMINDER_350_COMMIT=67c6022fd51ed8c34a7af5e3d18096a32dcd11f1
REMINDER_350_TARGETED_TESTS=59_PASS
REMINDER_REAL_PUSH_GATE=DEFER_TO_3_5_B

NOTES_EXPECTED_VERSION_MCP=CLOSED
NOTES_EXPECTED_VERSION_MCP_COMMIT=c110ada
MCP_TOOL_COUNT=23
MCP_SCHEMA_TOP_LEVEL_COMBINATORS=NONE
MCP_EXPECTED_UPDATED_AT_EXPOSED=NO
APPEND_SMOKE_NEWLINE_MISMATCH=PRE_EXISTING_NON_BLOCKER

NEXT_TASK=3_5_0_NOTES_LOCAL_FINALIZATION
NEXT_SUBTASK=get_note_markdown
NEXT_FEATURE=AI_CAN_EDIT_EXISTING_MARKDOWN_NOTE_LOCALLY
```

---

# 32. 新窗口接班短句

> G，继续 S-notes 3.5。以《S-notes 最终进度书｜Markdown Production Closed → GreenCloud Next｜更新至 2026-09-23》和《S-notes GreenCloud 3.5｜最终架构与施工方案 v2.1 FINAL》为当前基线。Markdown Retrofit Package 1–4 已全 CLOSED；3.5-0 Reminder 已 CLOSED，commit=`67c6022fd51ed8c34a7af5e3d18096a32dcd11f1`。Notes Local Finalization 的第一刀 `expected_version` MCP contract 也已 CLOSED，commit=`c110ada`：Claude Code 重连后实测 23 个工具全部可见，`prepend_note / append_note / update_note_metadata` 已恢复；MCP 层只暴露必填 `expected_version`，不再暴露 `expected_updated_at`，顶层 `anyOf / oneOf / allOf` 为 0；application 内部兼容逻辑未动，worktree clean。该项不要重复。当前下一步从 `get_note_markdown` 开始，随后是 `snotes-markdown-v1` dialect → Markdown canonicalizer → `edit_note` → append permission/version contract → local round-trip + S 真人验收。另有一个 pre-existing 非 blocker：append smoke 当前得到 `base\n\n-end`，旧测试期望 `base\n-end`；原版代码同样失败，留到 canonicalizer / append contract 一起判定，不回滚 `c110ada`。Notes authority 继续只在 Mac；当前任务不要进入 3.5-A / B / C / D，也不要恢复 HTML dual runtime。
---

# 33. 当前一句

2026-09-22 解决了“正文到底是什么”。

2026-09-23 先把 Reminder 上云前的本地业务语义收了口，又把 Notes MCP 的 `expected_version` 契约收口到 `c110ada`。

现在下一刀非常明确：

```text
get_note_markdown
→ AI 读取 canonical Markdown + current version + dialect
→ canonicalizer 把所有 AI Markdown 收敛成 S-notes 唯一规范形态
→ edit_note 精准修改已有 Note
→ Milkdown round-trip ZERO DIFF
→ S 真人确认
```

append 的单换行 / 空行差异作为同一条 canonicalization contract 一起判定。

这一步完成以后，才开始把 Notes 安全送上 GreenCloud。
