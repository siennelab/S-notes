# S-notes 最终进度书｜Markdown Production Closed → GreenCloud Next

**日期：2026-09-22；更新至 2026-09-24**  
**状态：Markdown Retrofit Package 1–4 全部 CLOSED；3.5-0 Reminder 已 CLOSED；3.5-0 Notes Local Finalization Step 1–3 全部 CLOSED；`edit_note` 本地既有笔记修改闭环已正式落地；AI Center 性能债已 CLOSED @ `2fa0df2`；编辑器代码块与字号小工单已 CLOSED @ `4746088`；当前暂停 3.5-A，下一任务为 `AI 接受链修复包`。**  
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

# 24. 已完成｜已有笔记修改 proposal（历史规划）

> 本节保留 2026-09-23 Step 3 开工前的设计意图。到 2026-09-24，本地 `edit_note` 已按更严格的 exact Markdown contract 实现并 CLOSED；当前正式状态以 §36 为准。

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

# 28. S-notes 后续 Queue｜2026-09-24 新基线

## P0｜3.5-0 Notes Local Finalization｜CLOSED；当前下一任务：3.5-A Notes Cloud Read

Reminder 小修与 Notes Local Finalization 均已关闭。Notes 本地 AI 修改链已经从 raw Markdown read 一路收口到 exact edit / conflict / idempotency：

```text
1. expected_version-only contract                CLOSED @ c110ada
2. Step 1: get_note_markdown + dialect           CLOSED @ fbbd51e
3. Step 2A: read-only round-trip scan             CLOSED @ 079b6e7
4. Step 2B: fixed-point canonicalizer             CLOSED @ 55f8bc4
5. Step 2C0: final canonical preview              CLOSED @ 5602684
6. Step 2C: create/prepend/append write paths     CLOSED @ dbb2f18
7. Step 2D: revision/updated_at/version invariant CLOSED @ e2df741 + 6dbe1ea
8. Step 2E: pending acceptance + permission check CLOSED @ b392fe5
9. candidate build import-boundary blocker        CLOSED @ 3de2543
10. Step 2 human gate + final closeout            CLOSED @ 8950cb1
11. Step 3: exact Markdown edit_note              CLOSED @ 1643b73
12. Step 3 conflict review / UX                   CLOSED @ 9329b32 + 51fce35
13. Step 3 audit / receipt semantics              CLOSED @ 286b6bc + 27d6d3d
14. Step 3 human + MCP contract final smoke       PASS
15. 3.5-0 Notes Local Finalization                CLOSED @ 27d6d3d
```

随后同日处理了一个不阻塞 GreenCloud 主线、但严重影响日用体验的 P2 性能债：

```text
AI_CENTER_PERFORMANCE_FIX=CLOSED @ 2fa0df2
ROOT_CAUSE=N+1 operation-status lookup + hidden eager load + unconditional tab reload
BEFORE=AI Center / call-log interaction ≈ 3–4s
AFTER=AI Center 44–75ms; call-log first open 25–44ms; re-entry 2–3ms
```

`ai_call_logs` retention 已确认可安全独立实施，但**尚未施工**，只保留为后续维护项；它不再是性能 blocker。

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

3.5-0 Notes 已于 2026-09-24 正式 CLOSED；现在依次进入：

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

get_note_markdown + snotes-markdown-v1
fixed-point canonicalizer
create / append / prepend / metadata canonical write
edit_note exact old_markdown → new_markdown
multi-edit atomicity
stale accept conflict + zero write
proposal original diff preservation
accept processing feedback
conflict audit display
MCP conflict processing_mode=none
rejected operation note_id / proposal_id linkage preservation
same-request idempotent retry
```

Notes Local Finalization Step 1–3 均已 CLOSED；下一任务不要从旧文档重新捞本地写入施工，直接进入 `3.5-A Notes Cloud Read`。

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
CURRENT_LOCAL_MAIN=2fa0df2
ORIGIN_MAIN=2fa0df2
WORKTREE=CLEAN

BODY_AUTHORITY=SQLITE_MARKDOWN
SCHEMA_MARKER=markdown
ACTIVE_HTML_BODY_RUNTIME=NO
DUAL_FORMAT_RUNTIME=NO
AUTO_MIGRATION=NO

FORMAL_APP=/Users/s/Documents/项目/S-notes/dist/S-notes.app
FORMAL_APP_REPLACED_DURING_STEP2=NO
FORMAL_DB_MUTATED_DURING_STEP2=NO
FORMAL_APP_REBUILT_AFTER_STEP3=YES
FORMAL_APP_STEP3_CODESIGN=PASS
FORMAL_APP_STEP3_MINIMAL_SMOKE=PASS
FORMAL_DB_MUTATED_DURING_STEP3_GATE=NO

MCP_MARKDOWN_READ=PASS
GALE_MARKDOWN_MIRROR=PASS
GALE_PRODUCTION_PUBLISH=PASS
REMINDER_HELPER=ACTIVE
GALE_PULL_HELPER=ACTIVE

REMINDER_350=CLOSED
REMINDER_350_COMMIT=67c6022fd51ed8c34a7af5e3d18096a32dcd11f1
REMINDER_350_TARGETED_TESTS=59_PASS
REMINDER_REAL_PUSH_GATE=DEFER_TO_3_5_B

NOTES_EXPECTED_VERSION_MCP=CLOSED
NOTES_EXPECTED_VERSION_MCP_COMMIT=c110ada
MCP_TOOL_COUNT=23
MCP_SCHEMA_TOP_LEVEL_COMBINATORS=NONE
MCP_EXPECTED_UPDATED_AT_EXPOSED=NO

NOTES_STEP1=CLOSED
NOTES_STEP1_FINAL=fbbd51e4504893b2b058aa55544348f30b3297dc
GET_NOTE_MARKDOWN=PASS
SNOTES_MARKDOWN_DIALECT_V1=PASS

NOTES_STEP2=CLOSED
NOTES_STEP2_CLOSEOUT=8950cb12e835343bc3695141a69d4b4810960f00
STEP2_HUMAN_GATE=PASS
PREPEND_HUMAN_GATE=PASS
METADATA_HUMAN_GATE=PASS
RESTART_HUMAN_GATE=PASS
STEP2_FINAL_REGRESSION=39_39_PASS
APPEND_NEWLINE_CONTRACT=DOUBLE_NEWLINE
CANONICALIZER_FIXED_POINT=YES
CANONICALIZER_MAX_PASSES=4
NODE_RUNTIME_REQUIRED=NO
LEGACY_NONCANONICAL_DIRECT_MODIFY=FORCED_PENDING
PREVIEW_ACCEPT_SINGLE_SOURCE=YES
ACCEPTANCE_PERMISSION_RECHECK=YES
METADATA_ALLOWLIST=title,directory,tags
AI_VISIBILITY_METADATA_WRITE=BLOCKED
AI_TARGETS_METADATA_WRITE=BLOCKED

NOTES_STEP3=CLOSED
NOTES_STEP3_FINAL=27d6d3d99da9ec3c528c727e311dc549a6b72a94
EDIT_NOTE_EXACT_UNIQUE_MATCH=YES
EDIT_NOTE_MULTI_EDIT_ATOMIC=YES
EDIT_NOTE_STALE_ACCEPT_ZERO_WRITE=YES
EDIT_NOTE_ORIGINAL_DIFF_PRESERVED_ON_STALE=YES
EDIT_NOTE_CONFLICT_UI=PASS
EDIT_NOTE_CONFLICT_AUDIT=PASS
MCP_CONFLICT_PROCESSING_MODE=none
REJECTED_OPERATION_LINKAGE_PRESERVED=YES
STEP3_HUMAN_GATE=PASS
STEP3_FINAL_MCP_CONTRACT_SMOKE=PASS
NOTES_LOCAL_FINALIZATION_350=CLOSED

AI_CENTER_PERFORMANCE_FIX=CLOSED
AI_CENTER_PERFORMANCE_COMMIT=2fa0df2
AI_CENTER_N_PLUS_ONE_REMOVED=YES
AI_CENTER_CALL_LOG_LAZY_LOAD=YES
AI_CENTER_CALL_LOG_PAGE_SIZE=100
AI_CENTER_CALL_LOG_TAB_REENTRY_RELOAD=NO
AI_CENTER_OPEN_TIMING=44_75_MS
AI_CENTER_CALL_LOG_FIRST_OPEN=25_44_MS
AI_CENTER_CALL_LOG_REENTRY=2_3_MS
AI_CENTER_HUMAN_GATE=PASS
AI_CENTER_FORMAL_APP_REPLACED=YES
AI_CENTER_FORMAL_SMOKE=PASS
LAST_PACKAGE_CONSTRUCTION_DATA_CLEANUP=PASS
LAST_PACKAGE_CONSTRUCTION_LEFTOVERS=0_THIS_PACKAGE
AI_CALL_LOG_RETENTION=SAFE_BUT_NOT_IMPLEMENTED_MAINTENANCE_CANDIDATE

NEXT_TASK=3_5_A_NOTES_CLOUD_READ
NEXT_PREREQUISITE=REVIEW_REDACTION_COVERAGE_BEFORE_CLOUD_PUBLISH
NEXT_FEATURE=SAFE_NOTES_CLOUD_READ_WITH_MAC_REMAINING_AUTHORITY
```

---

# 32. 2026-09-23｜Notes Local Finalization Step 1 CLOSED

Step 1 完成 `get_note_markdown + snotes-markdown-v1 dialect`，最终封口：

```text
FINAL_STEP1_COMMIT=fbbd51e4504893b2b058aa55544348f30b3297dc
MAIN=fbbd51e4504893b2b058aa55544348f30b3297dc
ORIGIN_MAIN=fbbd51e4504893b2b058aa55544348f30b3297dc
WORKTREE=CLEAN
```

正式语义：

```text
get_note_markdown(note_id)
→ 只对 full 且 caller actor 具备修改能力的 Note 返回 raw content_markdown
→ 返回 note_id + version + dialect

redacted / hidden / unauthorized / deleted / nonexistent
→ 一律 not_found
```

真人 Gate：

- Claude Code 重连后可发现 `get_note_markdown`；
- Note 308 的 raw Markdown 能读到 `:size[## 已确定]{size="20"}`，而普通 `get_note` 只得到派生纯文本 `## 已确定`，证明 raw Markdown 与 safe/plain read 的边界正确；
- 测试 Note 318 设为 `redacted` 后，`get_note_markdown` 返回 `not_found`，普通 `get_note` 仍只走安全派生读；
- hidden Note 同样 `not_found`。

因此 Step 1 验收的是：

> redacted Note 不允许通过 `get_note_markdown` 读取 Markdown 源码。

它**不等于**当前 redaction 已具备完整 PII 自动识别能力；该项见 §35。

---

# 33. 2026-09-23｜Notes Local Finalization Step 2 CLOSED

Step 2 最终：

```text
STEP2=CLOSED
HUMAN_GATE=PASS
PREPEND=PASS
METADATA=PASS
RESTART=PASS
FINAL_REGRESSION=39/39 PASS
CLOSEOUT_COMMIT=8950cb1
MAIN=8950cb12e835343bc3695141a69d4b4810960f00
ORIGIN_MAIN=8950cb12e835343bc3695141a69d4b4810960f00
MERGE_MODE=FAST_FORWARD_ONLY
PUSH=PASS
WORKTREE=CLEAN
TASK_BRANCH=DELETED_LOCAL_AFTER_MERGE
FORMAL_APP_REPLACED=NO
FORMAL_DB_MUTATED=NO
```

## 33.1 Step 2A｜只读 round-trip scan

正式 DB 只读扫描：

```text
SCANNED_NOTES=84
ZERO_DIFF=4
DIFF_NOTES=80
SECOND_PASS_UNSTABLE=1
```

首次差异粗分类：

```text
Markdown escaping        46
other                    20
list / heading            5
trailing / final newline  5
link                       3
:size                      1
```

关键结论：

- 80/84 首次 Milkdown round-trip 有字节差异，不代表 80 篇坏数据；
- 因此不能让一次小 append / prepend 静默顺带重写整篇旧正文；
- 不做存量批量 canonicalization；
- legacy non-canonical Note 真正被修改时，必须进入 human review；
- 1 篇第二轮仍变化的是 Note 315 尾部独立 `<br />` 空块：第一次去 `<br />`，第二次再收敛末尾换行，随后稳定，不是持续正文漂移。

Append 虚构 probe 证明 Milkdown 会保留单换行与双换行差异，因此最终正式拍板：

```text
APPEND_NEWLINE_CONTRACT=DOUBLE_NEWLINE
old + "\n\n" + appended_block
```

## 33.2 Step 2B｜fixed-point canonicalizer

commit：

```text
55f8bc4641da70ba6e37e32ff563a8efd461e6a3
```

完成：

```text
CANONICALIZER=PASS
FIXED_POINT=PASS
MAX_PASSES=4
DIALECT_FIXTURES=13/13 PASS
LEGACY_DIFF_DETECTION=PASS
NODE_RUNTIME_REQUIRED=NO
```

正式 canonicalizer：

- 复用 bundled Milkdown；
- 不另写 Python Markdown serializer；
- 不要求用户安装 Node；
- 最多 4 passes 收敛到 fixed point；
- 超过上限仍不稳定则 fail closed。

## 33.3 Step 2C0 / 2C｜final preview + write-path integration

关键 commits：

```text
5602684  Final canonical preview foundation
dbb2f18  Wire canonicalizer into existing write paths
```

正式策略：

```text
新 Note
→ canonicalize fixed point
→ DB

已有 canonical Note
→ 沿用 modify confirmation 设置，可 direct / pending

已有 legacy non-canonical Note
→ 即使 modify confirmation 关闭，也强制 pending
→ AI Center 显示 before full body → actual canonical final body
→ S 明确接受后才落库
```

preview / accept / direct 共享同一 candidate builder，不维护第二份正式正文。

Step 2C targeted：

```text
CREATE_CANONICALIZED=PASS
APPEND_CANONICALIZED=PASS
PREPEND_CANONICALIZED=PASS
LEGACY_DIRECT_WRITE_BLOCKED=PASS
LEGACY_FORCED_PENDING=PASS
PENDING_APPEND_ACCEPT_CANONICALIZED=PASS
PREVIEW_ACCEPT_SINGLE_SOURCE=PASS
STALE_ACCEPT_ZERO_WRITE=PASS
CANONICALIZER_FAILURE_ZERO_WRITE=PASS
TARGETED_TESTS=45/45 PASS
```

## 33.4 Step 2D｜version / timestamp invariant

审计发现一个真实 blocker：

```text
GUI body save
→ updated_at=09:00:00.123456
→ tags save 使用秒精度 09:00:00
→ token 竟恢复旧 V1
```

根因：metadata 秒精度 timestamp 覆盖正文微秒 timestamp。

修复 commits：

```text
e2df741
6dbe1ea
```

只把现实可达的：

```text
tags
ai_visibility
ai_targets
```

三个 mutation 入口接回已有 `next_note_updated_at` 单调 helper；没有改 token 算法、MCP `expected_version`、schema 或 migration。

结果：

```text
TIMESTAMP_MONOTONIC=PASS
GUI_BODY_TAG_SEQUENCE=PASS
VERSION_TOKEN_NO_REVERSION=PASS
ACTIVE_BODY_WRITE_PATHS=7/7
BODY_WRITE_REVISION_BUMP=PASS
BODY_WRITE_UPDATED_AT_BUMP=PASS
VERSION_TOKEN_CHANGES=PASS
FAILED_WRITE_ZERO_MUTATION=PASS
READ_ZERO_NOTE_MUTATION=PASS
MISSING_INVARIANT_PATHS=NONE
TARGETED_TESTS=40/40 PASS
```

## 33.5 Step 2E｜pending acceptance + current permission recheck

commit：

```text
b392fe5
```

完成：

- pending append 接受时重新检查 actor 当前权限；
- pending prepend 可正式 Accept；
- pending metadata 可正式 Accept；
- metadata allowlist 仅 `title / directory / tags`；
- `ai_visibility / ai_targets` 即使恶意进入 pending payload，也不能通过 acceptance 修改；
- stale / permission revoked / canonicalizer failure 均 zero Note mutation；
- terminal request idempotent，不重复执行。

最终：

```text
APPEND_PERMISSION_RECHECK=PASS
PREPEND_ACCEPTANCE=PASS
PREPEND_STALE_ZERO_WRITE=PASS
PREPEND_REVOKED_ZERO_WRITE=PASS
METADATA_ACCEPTANCE=PASS
METADATA_BODY_UNCHANGED=PASS
METADATA_STALE_ZERO_WRITE=PASS
METADATA_REVOKED_ZERO_WRITE=PASS
METADATA_VISIBILITY_TARGETS_BLOCKED=PASS
IDEMPOTENCY=PASS
REVISION_UPDATED_AT_VERSION=PASS
TARGETED_TESTS=68/68 PASS
```

## 33.6 Candidate build blocker

第一次 candidate build 暴露 packaging boundary：

```text
shared application module 顶层 import canonicalizer
→ 间接加载 QtWidgets
→ helper --version 也被迫加载 GUI stack
→ helper bundle 明确排除该模块
→ ModuleNotFoundError
```

修复 commit：

```text
3de2543
```

处理：

> 共享 application 模块不再无条件顶层加载 GUI canonicalizer，只在真正正文写入路径按需 import。

没有给 helper 硬塞 QtWidgets，也没有新建第二套 canonicalizer。

结果：

```text
IMPORT_BOUNDARY_TEST=PASS
STEP2_NEARBY_REGRESSION=27/27 PASS
CANDIDATE_BUILD=PASS
HELPER_VERSION_SMOKE=PASS
NO_NODE_LAUNCH_SMOKE=PASS
FORMAL_APP_UNCHANGED=YES
FORMAL_DB_TOUCHED=NO
```

## 33.7 S 真人 Gate

隔离 candidate：

```text
CANDIDATE_APP=/Users/s/Documents/项目/S-notes/dist-d1-candidate/S-notes.app
CANDIDATE_DATA_ROOT=/Users/s/Documents/项目/S-notes/data/gc35-step2-candidate-b392fe5
```

S 真人完成：

```text
PREPEND=PASS
METADATA=PASS
RESTART=PASS
```

确认：

- AI Center prepend proposal Accept 成功；
- prepend 内容出现在正文开头；heading / bold / link / 原有字号显示正常；
- metadata proposal Accept 成功；title / tags 正确更新；正文不被 metadata acceptance 改写；
- 退出 candidate，再用同一隔离启动器重开后，结果保留；
- 已接受 proposal 不重新回到 pending。

Step 2 因此正式 CLOSED。

---

# 34. Notes Local Finalization 封口后不要重新做的事

以下已经在 Notes Local Finalization 中正式收口，后续 agent 不要从旧进度书捞出来重做：

```text
expected_version-only MCP contract
get_note_markdown
snotes-markdown-v1 dialect
whole-body Milkdown canonicalizer
fixed-point convergence
append single/double newline debate
legacy non-canonical silent direct write
full canonical final preview
create/prepend/append canonicalization
pending append acceptance canonicalization
revision / updated_at / token invariant
append acceptance-time permission recheck
prepend pending acceptance
metadata pending acceptance
metadata ai_visibility / ai_targets blocking
candidate helper Qt import-boundary bug
Step 2 candidate human gate

edit_note MCP discovery / schema
exact unique old_markdown matching
multi-edit all-or-nothing
edit_note whole-body canonicalization
edit proposal AI Center diff
stale proposal original diff preservation
stale accept transaction conflict
conflict popup / processed-card wording
accept “处理中…” feedback
metadata add/remove display
conflict audit display
conflict outbound processing_mode semantics
rejected retry note_id / proposal_id linkage
same-request already_processed idempotency
Step 3 candidate + formal App human gate
```

当前正式 append contract 已经是：

```text
old + "\n\n" + appended_block
```

旧 smoke 的单换行 expectation 已在 Step 2 正式契约阶段退出，不再作为 pre-existing unresolved issue。

---

# 35. 两个必须带到后续施工的观察点

## 35.1 redacted ≠ 自动识别所有隐私

Step 1 真人测试 Note 318 使用了虚构姓名、手机号、邮箱、地址、银行卡、身份证、password、API key、server IP 等内容。

当 Note 设置为 `redacted` 后：

```text
get_note_markdown
→ not_found
```

这已经满足 Step 1 的目标：

> redacted Note 不允许 AI 读取 raw Markdown 源码。

但普通 `get_note` 的 safe/plain 派生读显示：现有 redaction 规则明显是**规则驱动**，不是通用 PII detector；测试中只有部分模式被遮掉，不能因此向用户暗示“redacted 会自动识别所有手机号 / 身份证 / API key / 地址等隐私”。

当前结论：

```text
STEP1_BLOCKER=NO
STEP2_BLOCKER=NO
STEP3_BLOCKER=NO
NOTES_LOCAL_FINALIZATION_BLOCKER=NO
RECHECK_BEFORE_3_5_A=YES
```

在 **3.5-A Notes Cloud Read 真正把 redacted `safe_text` 送上 GreenCloud 之前**，必须单独复查：

- 当前 redaction rule coverage；
- 产品上对 `redacted` 的真实承诺；
- 哪些敏感模式必须 fail-safe；
- 是否需要新增规则或收紧上云边界。

这项不要拖到数据已经上云后再看。

## 35.2 `:size` 临时测试笔记不是编辑器 bug

Step 1 时曾看到测试 Note 318 的普通 `get_note` 里出现两行裸 `:size`，当时先记为待 Step 2 判断，**没有立即修**。

Step 2A 已给出结论：

```text
Note 318 = ZERO DIFF
unescaped :size[ count = 0
bare unescaped :size count = 0
escaped \:size count = 2
```

也就是 318 实际存的是字面量：

```text
\:size
```

Milkdown 正确把它当 literal text 保留；这不是正常 size directive 被 renderer 弄坏。

同时 Note 308 已证明合法正式形态：

```text
raw get_note_markdown:
:size[## 已确定]{size="20"}

ordinary get_note:
## 已确定
```

说明正常存储的 `:size[...]` 在普通派生读里会正确去掉 directive，只留下正文文本。

因此：

```text
SIZE_PLAIN_TEXT_BUG=NO
NOTE_318_FIX=NO
AUTO_UNESCAPE_LEGACY_TEXT=NO
```

不要为了 318 单独开修复支线，也不要批量自动反转义历史 Notes。

---


# 36. 2026-09-24｜Notes Local Finalization Step 3 CLOSED + 3.5-0 Notes Local Finalization CLOSED

Step 3 把「AI 能读 raw Markdown」推进成「AI 能对既有 Note 做安全、可审阅、可冲突保护的精确修改」。这一步完成以后，3.5-0 Notes Local Finalization 正式封口。

## 36.1 最终 Git / App 状态

Step 3 代码提交链：

```text
1643b73389603b13bd819b20e3f382565b63455a
Implement GreenCloud Step 3 exact Markdown note edits

9329b322c67b3c6cea1e2c988dc0eb8da387c2bf
Fix Step 3 conflict review flow

51fce35d26050614712bff7a09cedf30b229228c
Clarify conflict outcomes in AI Center

286b6bcd99ec86dbc05088c56a107da174a9eaa2
Fix conflict audit display

27d6d3d99da9ec3c528c727e311dc549a6b72a94
Fix conflict receipt semantics
```

最终正式状态：

```text
STEP3=CLOSED
NOTES_LOCAL_FINALIZATION_350=CLOSED
FINAL_SOURCE=27d6d3d99da9ec3c528c727e311dc549a6b72a94
MAIN=27d6d3d99da9ec3c528c727e311dc549a6b72a94
ORIGIN_MAIN=27d6d3d99da9ec3c528c727e311dc549a6b72a94
MERGE_MODE=FAST_FORWARD_ONLY
PUSH=PASS
WORKTREE=CLEAN
TASK_BRANCH=codex/gc35-step03-edit-note
TASK_BRANCH_LOCAL_RETAINED=YES
TASK_BRANCH_PUSHED=NO
```

正式 App：

```text
FORMAL_APP=/Users/s/Documents/项目/S-notes/dist/S-notes.app
FORMAL_BUILD_FROM_FINAL_MAIN=PASS
CODESIGN=PASS
FORMAL_MINIMAL_SMOKE=PASS
FORMAL_DB_MIGRATED=NO
FORMAL_DB_REPLACED=NO
FORMAL_DB_TEST_WRITE=NO
```

正式最小 smoke 只确认主窗口正常、既有正式笔记可打开；没有把隔离 Gate 重跑到正式库，也没有为了封口制造正式测试写入。

Candidate 与隔离 Gate 数据暂时保留，不在本轮顺手清理。

## 36.2 `edit_note` 正式契约

正式 MCP 入口：

```text
edit_note(
    request_id,
    note_id,
    expected_version,
    edits=[
        {old_markdown, new_markdown},
        ...
    ]
)
```

边界固定为：

```text
full-authorized Note only
redacted / hidden / unauthorized / deleted / nonexistent
→ 不获得 raw edit 能力
```

每一组 `old_markdown` 必须在当前 Markdown 中**精确且唯一**命中：

```text
0 hit  → reject
>1 hit → reject
```

多组 edits：

```text
ALL-OR-NOTHING
```

明确不做：

```text
fuzzy matching
section merge
three-way merge
HTML surgery
whole-body new_body edit API
```

Step 3 复用 Step 2 的 whole-body fixed-point canonicalizer；无论 pending Accept 还是允许 direct 的既有路径，最终正式正文都只从同一 canonical candidate 产生。

## 36.3 Proposal / Accept / Conflict 正式语义

默认沿用现有 modify confirmation：

```text
edit_note proposal
→ AI Center pending
→ S review old → new + final canonical diff
→ Accept / Discard
```

Accept 时重新检查：

```text
Note 仍存在
未删除
actor 当前仍 full-authorized
expected_version 仍匹配
每个 old_markdown 当前仍精确唯一命中
canonicalizer 成功
```

任一失败：

```text
FORMAL_NOTE_WRITE=ZERO
```

stale proposal 的 UI / lifecycle 已正式收口：

- 原 proposal 保存的 `old_markdown → new_markdown` 与当前正式 Note version 解耦，版本变化后仍可查看；
- final preview 可以单独提示「笔记已被更新 / 无法生成当前预览」；
- stale proposal 的 Accept 不再被 UI 静默禁用，而是进入最终 transaction recheck；
- 点击 Accept 立即出现「处理中…」或等价反馈，避免重复点击与“到底点没点上”的不确定；
- version mismatch 明确弹出「版本冲突：笔记已被更新。AI 修改未写入。」；
- 已处理卡片显示「版本冲突 · 未写入」，不伪装成 S 主动拒绝；
- 正式正文保留 S 后写内容，AI stale 修改 zero write。

metadata proposal 的卡片也一并收口：version 可比较时明确显示「新增标签 / 移除标签」；已经 stale、不能可靠反推历史动作时只显示「标签将设为」的目标完整列表，不编造 add/remove intent。

## 36.4 Idempotency / Audit / MCP response 语义

最终 smoke 证明 terminal `request_id` 不重复执行。

Conflict 后同 request id 重试：

```text
status=already_processed
previous_status=conflict
processing_mode=none
note_id=原目标 Note
proposal_id=原 proposal
changed_formal_note=false
```

`processing_mode=none` 只用于 MCP outbound conflict 语义；内部既有 receipt lifecycle 不为本轮另起状态机。AI Center audit 以真实 operation `status=conflict` 为高优先级，显示「版本冲突 · 未写入」，不再把 conflict 误标成「人工拒绝」。

真正人工丢弃后同 request id 重试：

```text
status=already_processed
previous_status=rejected
processing_mode=human_rejected
note_id=原目标 Note（edit proposal）
proposal_id=原 proposal
changed_formal_note=false
```

人工丢弃的修改类 proposal 现在会把已有 `target_note_id` 与 `receipt.id` 保留到 operation result；create proposal 若尚未生成正式 Note，则 `note_id=null` 仍是正确语义，但 `proposal_id` 继续保留。

因此最终区分为：

```text
系统版本冲突 ≠ 用户人工拒绝
```

并且 MCP、AI Center audit、重复请求结果三处语义一致。

## 36.5 验收记录

Step 3 初版施工：

```text
TARGETED=15 PASS
RELATED_REGRESSION=92 PASS
TOTAL=107/107 PASS
CANDIDATE_BUILD=PASS
CODESIGN=PASS
```

第一次真人 Gate：

```text
EDIT=PASS
REDACTED=PASS
REGRESSION=PASS
DISCOVERY=PASS
CONFLICT=FAIL
```

CONFLICT 失败暴露的是 review / conflict feedback 链，不是正文越权写入：S 后写内容当时已经安全保留，AI stale 修改没有落库。

Follow-up 分阶段验证：

```text
CONFLICT_REVIEW_FOLLOWUP=66/66 PASS
CONFLICT_WORDING_DIRECT_TESTS=3 PASS
AUDIT_DISPLAY_RELATED=18 PASS
RECEIPT_CONTRACT_RELATED=19 PASS
```

最终真人 / smoke：

```text
EDIT=PASS
CONFLICT=PASS
REDACTED=PASS
REGRESSION=PASS
DISCOVERY=PASS
ACCEPT_FEEDBACK=PASS
METADATA_DISPLAY=PASS
STALE_ORIGINAL_DIFF_PRESERVED=PASS
CONFLICT_POPUP=PASS
CONFLICT_PROCESSED_CARD=PASS
CONFLICT_AUDIT_DISPLAY=PASS
CONFLICT_RETRY_CONTRACT=PASS
HUMAN_REJECT_RETRY_CONTRACT=PASS
DUPLICATE_EXECUTION=NO
```

最终两条 MCP contract smoke 均确认：重复请求只返回原 operation，不产生新 proposal、不重复写正文。

## 36.6 3.5-0 Notes Local Finalization 最终封口

从 Step 1 到 Step 3，正式链路现在是：

```text
expected_version
→ get_note_markdown + snotes-markdown-v1
→ fixed-point Milkdown canonicalizer
→ create / prepend / append / metadata canonical write
→ edit_note exact old → new
→ AI Center proposal / diff
→ acceptance-time version + permission recheck
→ canonical formal write
→ stale conflict zero write
→ idempotent terminal retry
```

因此：

```text
NOTES_LOCAL_FINALIZATION_350=CLOSED
LOCAL_AI_EXISTING_NOTE_EDIT_LOOP=CLOSED
```

下一阶段按既有 GreenCloud 3.5 顺序进入：

```text
3.5-A Notes Cloud Read
→ 3.5-B Reminder Cloud Authority
→ 3.5-C AI Access Boundary Ready
→ 3.5-D Existing R1B Outbox → Notes Cloud Writeback
```

但在 **任何 redacted `safe_text` 真正上 GreenCloud 之前**，先执行 §35.1 已记录的 redaction coverage / 产品承诺复查；这仍是 3.5-A 的前置边界，不因 Step 3 CLOSED 而取消。

---

# 37. 新窗口接班短句

> G，继续 S-notes GreenCloud 3.5。当前正式 repo `/Users/s/Documents/项目/S-notes` 已推进到 `main == origin/main == 2fa0df2`，worktree clean。Markdown Retrofit Package 1–4、3.5-0 Reminder、Notes `expected_version` contract、Notes Local Finalization Step 1–3 均已 CLOSED，`3.5-0 Notes Local Finalization=CLOSED`。Step 3 已正式提供 `edit_note(request_id, note_id, expected_version, edits[])`：exact unique `old_markdown → new_markdown`、多 edit 原子、full-authorized only、Step 2 canonicalizer single source、AI Center proposal/diff、acceptance-time version+permission recheck、stale conflict zero write、原 proposal diff stale 后仍可看、冲突 audit/MCP 语义一致、terminal request idempotent。Step 3 final source=`27d6d3d...`；其后 AI Center 3–4 秒性能债已在 `2fa0df2` 定点修复并正式部署：call-log N+1 已消除、lazy load、100 条分页、tab 回切不再无条件 reload；真人 Gate、正式 App smoke、construction cleanup 均 PASS。当前下一刀仍是 **3.5-A Notes Cloud Read**，不要重做 Step 1/2/3，也不要提前进入 3.5-B/C/D。3.5-A 真正把任何 redacted `safe_text` 上云之前，必须先复查现有 redaction coverage 与产品承诺；Note 318 的 escaped `\:size` 已判定为 literal、不是 renderer bug，继续不修、不自动反转义。以后所有 Package / Step 封口必须执行 construction data cleanup：candidate / 临时 rollback / 临时测试数据有生命周期，CLOSED 时删除并记录 `CONSTRUCTION_DATA_CLEANUP=PASS`；长期保留物必须显式标记 KEEP/ARCHIVE + 理由。`ai_call_logs` retention 仅是已确认安全的维护候选，尚未实施，不阻塞 3.5-A。

---

# 38. 当前一句

2026-09-22 解决了“正文到底是什么”。

2026-09-23 把 Reminder 上云前本地语义、Notes raw Markdown read、canonical write 与 acceptance/version 基础收口。

2026-09-24 把“AI 修改已有笔记”真正闭环：

```text
expected_version
→ get_note_markdown + dialect
→ fixed-point canonicalizer
→ safe direct / pending write paths
→ edit_note exact old → new
→ AI Center proposal / canonical diff
→ acceptance-time version + permission recheck
→ stale conflict zero write
→ conflict / audit / MCP semantics aligned
→ same-request idempotent retry
→ formal App from final main
→ S 真人 PASS
```

现在：

```text
3.5-0 Reminder=CLOSED
3.5-0 Notes Local Finalization=CLOSED
AI_CENTER_PERFORMANCE_FIX=CLOSED @ 2fa0df2
MAIN=ORIGIN_MAIN=2fa0df2
WORKTREE=CLEAN
```

同日额外把 AI Center 的 P2 性能债收掉：从每次约 3–4 秒，降到打开 44–75ms、调用记录首次进入 25–44ms、回切 2–3ms；本轮 construction leftovers 已清零并 PASS。

当前下一刀：

```text
3.5-A Notes Cloud Read
```

而在任何 redacted `safe_text` 真正上 GreenCloud 之前，先复查 redaction coverage 与产品承诺；Mac 继续是 Notes 唯一正式 authority。

---

# 39. 2026-09-24｜Construction Data Cleanup 正式封口规则

本轮再次暴露了一个长期施工习惯问题：candidate / pre-migration DB / 临时 rollback / 隔离 Gate 数据在施工期有价值，但过去有时在 Package CLOSED 后继续留在磁盘上，逐渐变成无法判断用途的 construction leftovers。

以后正式规则固定为：

```text
施工开始
→ 可以创建 candidate / pre-migration DB

真人 Gate PASS
→ 正式切换

Package / Step CLOSED
→ 删除 candidate
→ 删除临时 rollback DB
→ 删除本步临时测试 / profiling / timing 数据
→ 扫描 construction leftovers
→ CONSTRUCTION_DATA_CLEANUP=PASS
```

这条规则的含义不是「绝不产生副本」。安全施工仍然允许、也鼓励在需要时建立 candidate、migration rehearsal copy、rollback copy。

真正新增的是**生命周期**：

```text
construction copy
= 施工时可以出生
= 封口时必须有明确去向
```

长期确实需要保留的正式 rollback / archive 不强删，但必须显式记为：

```text
KEEP / ARCHIVE
+ 保留理由
+ 明确用途
```

禁止继续以「先放着」作为无限期保留理由。

历史记录仍按当时事实保留。例如 §36.1 中 Step 3 当时写着 Candidate 与隔离 Gate 数据暂时保留，这句不事后篡改。本规则从现在起作为所有后续 Package / Step 的正式 closeout discipline。

AI Center 性能修复是这条规则落地后的第一次正式执行：

```text
CONSTRUCTION_DATA_CLEANUP=PASS
LEFTOVERS=0（本轮）
```

这里的 `0（本轮）` 只表示本次性能修复产生的 construction data 已清干净，不宣称历史全部 Package 的磁盘遗留已经做过一次全仓清点。

---

# 40. 2026-09-24｜AI Center 性能修复 CLOSED

## 40.1 用户侧现象

真人日用发现：

```text
每次打开 AI Center
→ 约 4 秒

点击「笔记调用记录」
→ 再约 3 秒

切到「未处理」再切回「笔记调用记录」
→ 又约 3 秒
```

这不是 App 首次冷启动问题，而是每次进入 / 回切都重复发生。

## 40.2 只读诊断

诊断期间正式 DB 只以只读方式访问；Qt 复现使用匿名临时数据，没有修改正式数据。

测量快照：

```text
CALL_LOG_ROWS≈19,673
全量 SQL + fetch≈30–32ms
repository/service conversion≈70–85ms
```

真正主耗时发生在日志取回之后：

```text
每条 call log
→ 单独查询 operation status
→ 每次重新建立 SQLite connection
→ 每次重新检查 DB format

≈19,700 次 N+1 查询
→ 约 2.87–3.45 秒
→ 占页面总耗时约 89%
```

窗口本身并不慢：

```text
AI Center construct≈3.35s
其中隐藏 call-log page load≈3.30s
窗口显示 / event processing≈0.034s
```

同时确认：

- AI Center 构造时会 eager load 尚未显示的调用记录页；
- 每次切回调用记录 tab 都无条件 `refresh_logs`；
- 当时一次全取约 1.97 万条、生成 157,384 个单元格；
- Qt populate/layout 本身约 0.287 秒，不是主因；
- operation status 查询已经命中 `request_id` 主键索引，问题是**调用次数**，不是单次查询缺索引。

因此正式 root cause：

```text
ROOT_CAUSE=
N+1 operation-status lookup
+ hidden eager load
+ unconditional tab re-entry reload
+ unbounded call-log page
```

## 40.3 最小修复

commit：

```text
2fa0df2
```

只做定点修复：

```text
1. N+1 operation status
   → 单次关联 / 批量查询

2. AI Center construct
   → 不再提前加载 call log
   → 第一次真正进入时 lazy load

3. tab re-entry
   → 不再无条件全量 reload

4. call-log display
   → PAGE_SIZE=100
```

没有借机重构 AI Center，也没有修改 receipt / proposal / idempotency / Notes 权限语义或 GreenCloud 3.5 主线。

## 40.4 性能结果

真人 Gate 后：

```text
BEFORE:
AI Center / call-log interactions≈3–4s

AFTER:
AI_CENTER_OPEN=44–75ms
CALL_LOG_FIRST_OPEN=25–44ms
CALL_LOG_REENTRY=2–3ms
```

最终：

```text
ROOT_CAUSE_FIXED=YES
N_PLUS_ONE_REMOVED=YES
LAZY_LOAD=YES
TAB_REENTRY_RELOAD=NO
PAGE_SIZE=100
HUMAN_GATE=PASS
```

## 40.5 正式 App / Git / cleanup

正式 App 已替换为本轮修复版本并完成最小 smoke：

```text
FORMAL_APP_REPLACED=YES
FORMAL_SMOKE=PASS
CONSTRUCTION_DATA_CLEANUP=PASS
LEFTOVERS=0（本轮）
WORKTREE=CLEAN
MAIN=2fa0df2
ORIGIN_MAIN=2fa0df2
PUSH=PASS
AI_CENTER_PERFORMANCE_FIX=CLOSED
```

因此原先的 AI Center P2 性能债从 Queue 删除，不再作为待办。

## 40.6 `ai_call_logs` retention

诊断顺便确认 `ai_call_logs` 是独立 audit history；删除旧日志不会影响：

```text
receipt / proposal lifecycle
request_id idempotency
get_operation_status
pending / processed
formal Notes
```

因此：

```text
SAFE_TO_APPLY_RETENTION=YES
RETENTION_IMPLEMENTED=NO
CURRENT_BLOCKER=NO
```

后续若要做，可单独开一张小维护工单，例如限制总量或按时间清理；不要把它重新混进 3.5-A Notes Cloud Read。

---

# 41. 2026-09-25｜编辑器代码块与字号小工单 CLOSED

本节记录 2026-09-24 架构健康审计之后、AI 接受链修复包开工之前插入的一张独立编辑器小工单。
它不改变 Markdown 正文架构，也不属于 GreenCloud 3.5 主功能包。

## 41.1 起因

真人日用发现两个编辑器问题：

```text
1. 全文 / 混合选区中只要包含 code_block
   → 15 / 17 / 20 字号操作整单失败

2. 代码块按钮只有进入路径
   → 笔记末尾 code block 中 Enter 会继续留在代码块
   → 缺少明确的按钮退出路径
```

本单只修这两个用户可见问题，不修改 schema、Markdown parser / serializer、粘贴行为或 Python 业务层。

## 41.2 最终交互规则

字号：

```text
paragraph / heading → 正常修改
code_block          → 跳过
纯 code 选区        → 字号按钮 no-op
```

代码块按钮统一为两条规则：

```text
有选区
→ 按碰到的完整行处理
→ 纯正文选区进入 code block
→ 只要选区含 code block，则朝普通正文方向收敛：
   只解除选中的 code 行，原正文保持不动

无选区
→ 光标是分界线
→ 左侧保持当前块类型
→ 右侧切换块类型
```

因此代码块末尾点击按钮会在下面生成空正文并把光标放进去；代码块内部 Enter 仍然只负责换行，不新增双 Enter、快捷键或其它隐式退出规则。

## 41.3 施工与验收

施工分支 / commit：

```text
BRANCH=codex/editor-code-size
COMMIT=4746088968e715683b5a85cbc22311e57138fba3
```

修改文件共 5 个：

```text
web/markdown-editor/src/body-size.js
web/markdown-editor/src/main.js
web/markdown-editor/src/code-block.js        (new)
src/snotes/ui/markdown_smoke.py
tests/test_gc35_step02b_canonicalizer.py
```

验证：

```text
NPM_BUILD=PASS
RELATED_REGRESSION=22_PASS
SOURCE_MARKDOWN_SMOKE=71_PASS
CANDIDATE_MARKDOWN_SMOKE=71_PASS
ISOLATED_HOME=YES
FORMAL_DB_TOUCHED=NO
HUMAN_GATE=PASS
```

S 真人验收后：

```text
MAIN=4746088968e715683b5a85cbc22311e57138fba3
ORIGIN_MAIN=4746088968e715683b5a85cbc22311e57138fba3
PUSH=PASS
TRACKED_WORKTREE=CLEAN
UNTRACKED_DOCS=4_EXISTING_UNTOUCHED
EDITOR_CODE_SIZE_TICKET=CLOSED
```

原有 4 个未跟踪文档属于施工前既有文件，本单明确没有修改、暂存或删除；因此“tracked worktree clean + 原有 untracked docs 保留”视为正常现场，不是 blocker。

## 41.4 本单没有做

```text
NO_SCHEMA_CHANGE
NO_MARKDOWN_PARSER_CHANGE
NO_MARKDOWN_SERIALIZER_CHANGE
NO_ENTER_KEY_BEHAVIOR_CHANGE
NO_PASTE_CHANGE
NO_PYTHON_BUSINESS_CHANGE
NO_FULL_PROJECT_REFACTOR
```

这张小工单到此关闭，不再补 case，不再扩成编辑器重构。

---

# 42. 2026-09-25｜当前最新基线与下一刀

本节覆盖此前“下一任务直接进入 3.5-A Notes Cloud Read”的旧当前状态；旧章节保留历史事实，不回写。

最新正式 Git：

```text
CURRENT_LOCAL_MAIN=4746088968e715683b5a85cbc22311e57138fba3
ORIGIN_MAIN=4746088968e715683b5a85cbc22311e57138fba3
LATEST_CLOSED=EDITOR_CODE_SIZE_TICKET
LATEST_CLOSED_COMMIT=4746088968e715683b5a85cbc22311e57138fba3
```

当前施工顺序正式改为：

```text
1. AI 接受链修复包
   → 修复 2026-09-24 架构健康审计中集中在 Notes AI proposal acceptance 链的结构侵蚀
   → A–I 全部完成
   → 架构闸门 PASS
   → 全量 unittest 恢复可信
   → S candidate 真人 Gate
   → 寓只读封口复核

2. 两张审计已明确的小工单
   → Reminder completed item 被 AI resume 重新激活
   → Note 正文 + tags 分事务导致 revision 假冲突

3. 回到 GreenCloud 3.5-A Notes Cloud Read
```

当前两份施工依据的基线同步更新为：

```text
S-notes-AI接受链修复包-施工单-v1.1.md
S-notes-修复包复核-交接指令-v1.1.md

BASELINE=4746088968e715683b5a85cbc22311e57138fba3
```

AI 接受链修复包仍保持原裁定：

```text
不是重建
不是重构周
不是全项目清理
只修审计确认的 acceptance 链 A–I
范围外债务只记录
```

当前：

```text
ARCHITECTURE_HEALTH_AUDIT=COMPLETE
EDITOR_CODE_SIZE_TICKET=CLOSED
ACCEPTANCE_REPAIR=NEXT
GREENCloud_3_5_A=PAUSED_UNTIL_ACCEPTANCE_REPAIR_CLOSEOUT
```

一句话：

> **编辑器小工单已经封口；现在正式进入 AI 接受链修复，把这段集中长歪的屎山代码收回正确 owner，再回 GreenCloud 主线。**

