# S-notes Markdown Retrofit 最小施工方案 v1.2 FINAL

> 状态：FINAL 施工基线  
> 日期：2026-09-21  
> 适用仓库：`/Users/s/Documents/项目/S-notes`  
> 实验依据：`/Users/s/Documents/项目/S-notes-md-wysiwyg-lab`  
> 核心目标：保留 S-notes 现有产品/业务层，仅替换“笔记正文编辑器 + 正文存储主干 + 直接依赖 HTML 正文的活动接点”。

---

## 0. 最终结论

采用 **RETROFIT**，不重建整个 S-notes。

最终正文架构：

```text
PySide6 desktop shell
        ↓
QWebEngineView + Milkdown WYSIWYG
        ↕ Markdown string
thin QWebChannel bridge
        ↕ Markdown string
NoteService / revision / autosave
        ↓
SQLite notes.content_markdown
```

唯一正文真身：

```text
SQLite.content_markdown
```

不保留：

```text
content_html
body_rich_text
HTML canonical body
editor JSON canonical body
实体 .md 文件 authority
HTML/Markdown 双格式 runtime fallback
```

实体 `.md` 只作为导出格式，不参与日常正文 authority。

---

## 1. 已拍板产品规则

### 1.1 编辑体验

- 必须是 **WYSIWYG**。
- 用户平时看到 Markdown 的渲染结果，不需要直接编辑 `**`、`##`、`:size[...]` 等源码。
- Raw Markdown 保留为 **只读检查视图**，不是第二写入入口。

### 1.2 正文排版

```text
默认正文：17px
可选正文：15px / 20px
line-height：1.9
paragraph gap：11px
标题：H1 / H2 / H3
```

字号持久化：

```md
普通正文不加标记。

:size[小号正文]{size="15"}

:size[大号正文]{size="20"}
```

切回默认 17px 后，size directive 必须消失。

旧系统默认正文为 15px；新系统默认正文改为 17px。旧笔记迁移后，旧默认 15px 正文统一按新默认 17px 显示，这是**有意的视觉变化**。

旧 HTML 中无法可靠区分“默认 15px”与“用户手动选择 15px”，迁移时统一不写 15px mark；新系统中以后新产生的显式 15px 仍使用 size directive 保存。

### 1.3 Markdown 方言

采用：

```text
CommonMark / GFM
+
唯一一个受控扩展：15px / 20px size directive
```

不支持：

```text
任意字号
任意 CSS
文字颜色
自定义字体
长期 Raw HTML 正文
第二套 editor JSON 正文
```

---

## 2. 三条防屎山硬契约

### Contract A｜正文永远只有一个 canonical representation

```text
Markdown = canonical body
```

允许派生：

```text
Markdown → Milkdown rendered view
Markdown → plain text
Markdown → .md export
```

派生结果不得成为第二正文真身。

### Contract B｜正式 runtime 禁止双格式

切换完成后禁止出现：

```python
if content_html:
    ...
else:
    use_markdown(...)
```

禁止：

```text
Markdown 优先，失败 fallback HTML
```

旧 HTML 只允许作为**一次性 migration 输入**存在。

### Contract C｜冻结 R1 不碰

冻结代码继续冻结，例如：

```text
src/database.py
src/services/**
```

除非当前 active runtime 明确依赖，否则不为了 Markdown retrofit 顺手重构 R1。

---

## 3. 正式 Editor 架构

正式笔记编辑器组件：

```text
MarkdownEditorWidget
├── QWebEngineView
├── bundled Milkdown
├── Web-side formatting toolbar
├── read-only Raw Markdown view
├── find / replace
└── thin QWebChannel bridge
```

Python 不认识：

```text
selection
cursor model
Bold implementation
heading implementation
list implementation
Milkdown document model
```

Web 不认识：

```text
SQLite
NoteService
revision
AI permissions
```

边界只传 Markdown string 和少量容器状态。

---

## 4. Bridge 正式契约

### Python → Web

```text
setMarkdown(markdown, load_id)
prepareClose()
setTheme(theme)
setReadOnly(read_only)
```

### Web → Python

```text
editorReady()
loadComplete(load_id, markdown)
loadFailed(load_id, reason)
markdownChanged(markdown, load_id)
closeReady()
```

### 4.1 加载成功与 baseline

不能依赖 `setMarkdown()` 之后出现一次 `markdownChanged()`。

正式协议：

```text
setMarkdown(A, load_id)
→ Web 解析并装载文档
→ 装载成功后，Web 无条件调用 serializer 得到 A'
→ Web 发 loadComplete(load_id, A')
   - 不依赖 doc 是否变化
   - 不依赖 change plugin
   - 空文档也必须发
→ Python 把 A' 记为该 load_id 的 baseline
→ 不置 dirty
→ 不保存
→ 不增加 revision
```

之后：

```text
markdownChanged(new, load_id)
且 new != baseline
→ dirty
```

任何来自旧页面/旧笔记的迟到消息：

```text
load_id != current_load_id
→ 丢弃
```

目标：禁止“只是打开笔记就 revision +1 / updated_at 改变”，同时保证空白新笔记、正文相同的两篇笔记也能完成加载并继续保存。

### 4.2 加载失败规则

Markdown parser / directive parser 必须是 total function：

```text
未知 directive
→ 尽量保留为字面文本
→ 不 throw 导致正文变空
```

任何笔记：

```text
收到当前 load_id 的 loadComplete 之前
或收到 loadFailed 之后
→ 禁止保存该笔记
```

UI 显示失败原因，可只读，但数据库原正文不得被清空或覆盖。

加载失败时：

```text
loadFailed(load_id, reason)
→ 不发 loadComplete
```

### 4.3 关闭规则

Python 平时持续持有最新已提交 Markdown。

关闭时：

```text
prepareClose()
→ Web 结束 IME/composition 和待发送 change notification
→ closeReady()
```

禁止：

```text
关闭时临时 getMarkdown()
→ 等异步 callback
→ 抢最后正文
```

---

## 5. Web editor 运行与打包

### 5.1 Build-time / runtime

Build-time：

```text
Node + npm + Vite
```

Runtime：

```text
本地 bundled static assets
NO Node runtime
NO Vite dev server
NO localhost server
NO external CDN
```

`package-lock.json` 必须入库。

正式 build 流程包含：

```text
npm ci
npm run build
验证 dist/index.html 存在
```

### 5.2 PyInstaller / macOS App

正式 candidate build 必须验证：

```text
PySide6.QtWebEngineWidgets hidden import
PySide6.QtWebChannel hidden import
QtWebEngineProcess included
qtwebengine_resources.pak included
codesign --verify --deep --strict PASS
```

Web bundle 必须合并进现有唯一 `datas=` 配置，不建立第二套打包逻辑。

Package 1 报告：

```text
旧正式 App size
新 candidate App size
实际增加量
```

不预设体积阈值，由真实 candidate 再判断。

### 5.3 WebEngine profile

正式实现使用 off-the-record `QWebEngineProfile`。

不得像 lab 一样改 `HOME`。

不要让 Chromium cache / persistent storage 成为新的长期业务数据目录。

### 5.4 外部导航

非编辑器自身资源导航：

```text
→ 系统浏览器打开
```

不得让普通 Markdown 链接把编辑器 WebView 页面直接导航走。

拖放文件不得触发 WebView 页面导航。

---

## 6. SQLite 最终数据模型

正式 schema：

```sql
content_markdown TEXT NOT NULL DEFAULT ''
```

删除：

```text
content_html
body_rich_text
```

Note domain model、ports、repository、commands 中统一使用：

```text
content_markdown
```

不提供：

```text
content_html alias
body_rich_text alias
HTML-or-Markdown union field
```

继续保留：

```text
UUID
revision
title
created_at / updated_at
directories
tags
pinned
trash / deleted state
AI visibility
AI targets
其他 metadata
```

---

## 7. 保存 / revision / autosave

继续沿用现有：

```text
revision optimistic lock
autosave
note switching save
exit save
revision conflict UI
```

正文来源改成 Python 当前缓存的最新 Markdown。

典型路径：

```text
Milkdown user edit
→ markdownChanged(markdown, load_id)
→ Python latest_markdown cache
→ dirty
→ autosave / force save
→ NoteService.save_note(expected_revision)
→ repository update content_markdown
```

`loadComplete()` 建立 baseline，不算用户编辑。

不因为 Milkdown canonicalization 自动增加 revision。

---

## 8. Markdown canonicalization

Milkdown 可能规范化：

```text
列表 marker
空行
hard line break
部分等价 Markdown 写法
```

这不是数据错误。

正式规则：

> canonical Markdown 以 Milkdown 稳定 round-trip 后的输出为准。

迁移后的正文必须满足：

```text
load
→ serialize #1
→ load
→ serialize #2
```

从 #2 开始逐字节稳定。

---

## 9. markdown_to_plain_text 派生层

新增一个薄的纯函数：

```python
markdown_to_plain_text(markdown)
```

用途：

```text
MCP get/search
Gale mirror
redaction
search body text
related notes
snippet / preview
必要的 plain-text export
```

规则：

- derived only；
- 不落数据库；
- 不落文件；
- 不成为第二正文；
- 使用成熟 Markdown parser / AST；
- 不自造完整 regex Markdown parser。

若使用 `markdown-it-py`，必须显式加入 requirements 并钉版本，不依赖传递依赖。

唯一允许的定制解析规则：

```md
:size[abc]{size="20"}
```

plain text：

```text
abc
```

### 9.1 进程内派生缓存

为避免 search / relations / autosave 后全量重复解析 Markdown，允许建立**进程内缓存**：

```text
key = (note_id, revision)
value = markdown_to_plain_text(content_markdown)
```

规则：

```text
revision 变化
→ 对应缓存自动失效
```

缓存只能存在于 application 层，例如：

```text
NoteService
或独立 derived-text cache
```

domain 的 `markdown_to_plain_text()` 本身保持纯函数。

明确禁止：

```text
新增 SQLite content_text 列
把 derived text 落文件
把缓存当 export authority
建立第二正文 truth
```

Package 2 必须在真实 83 篇副本规模上报告 search / relations 实际耗时，并确认 warm cache 下未变化笔记不会被重复解析。

---

## 10. AI / MCP / Gale 契约

### 10.1 原则

Markdown 是内部正文真身，但**不是所有外部/AI 读取接口都改成 Markdown 输出**。

本轮保持既有读取消费者语义。

### 10.2 AI safe export

```text
content_markdown
→ AI-safe export .md
```

不再走旧 HTML → Markdown converter。

### 10.3 Local MCP / Yan get/search / append 前置读

继续提供纯文本：

```text
content_markdown
→ markdown_to_plain_text()
→ existing permission / redaction
→ MCP / Yan
```

不在本轮把 `get_note` / `search_notes` 改成 Markdown 原文。

### 10.4 Gale mirror

继续 mirror 纯文本：

```text
content_markdown
→ markdown_to_plain_text()
→ Gale mirror body / FTS
```

生产 cutover 后 Gale mirror 需要一次全量重生成 + publish smoke。

### 10.5 Redaction

redaction 继续作用在**派生后的纯文本**上。

必须测试合法 Markdown 中的敏感词不能靠格式绕过：

```md
se**cr**et
*sec*ret
:size[secret]{size="20"}
[secret](https://example.com)
`secret`
```

以上经 `markdown_to_plain_text()` 后都应得到：

```text
secret
```

然后继续由既有 redaction 规则捕获。

明确契约：

> Markdown 格式不能成为绕过脱敏的方法，包括 inline code。

禁止为了让这些测试通过而在 redaction 前另加一套 regex “剥 Markdown”。

### 10.6 AI create / append / receipt 输入语义

本轮拍板：

```text
AI create / append input = Markdown
```

不做 plain-text escape。

AI 写入：

```md
# 标题

这是 **重点**

- 第一项
- 第二项
```

应在 S-notes 中正常渲染为标题、加粗和列表。

禁止新增：

```text
markdown_escape_plain_text()
```

也不为了兼容旧 plain-text 写入语义再造半套 Markdown parser。

现有 MCP / Gale **读取输出仍保持 plain text**；这里改变的是 AI 写入正文的解释方式。

AI receipt pending body 在 cutover 后接受时同样按 Markdown 正文解释。已有 pending receipt 中若存在 `*`、`#` 等 Markdown 语法，接受后可能产生格式，这是本轮明确接受的行为变化。

本轮仍不新增：

```text
edit_note
raw Markdown write API 的第二套正文格式参数
```

未来 edit_note 直接沿用 Markdown 正文语义，不再二次切换。

---

## 11. Search / Relations / Export

### Search

改为：

```text
content_markdown
→ markdown_to_plain_text()
→ process-local cache
→ current tokenizer / scorer
```

HTML 标签/style 噪声消失属于预期语义改善，不复刻旧搜索噪声。

### Related notes

改为：

```text
content_markdown
→ markdown_to_plain_text()
→ process-local cache
→ existing relation algorithm
```

删除 HTML body parser 路线。

### Export

Markdown export：

```text
content_markdown
→ .md
```

JSON export：

```text
content_markdown
content_text = markdown_to_plain_text(content_markdown)
```

`content_text` 只存在于导出结果中，是派生字段，不落正式数据库。

不再导出 canonical `content_html`。

---

## 12. Migration 设计

### 12.1 禁止使用现有 html_to_markdown() 作为迁移 converter

现有 `html_to_markdown()` 是 AI/export 用粗转换器，不足以完整理解旧 Qt editor 实际保存的 HTML。

必须提供一个**migration-only** converter，例如：

```text
scripts/migrate_markdown_body.py
legacy_qt_html_to_markdown()
```

它只服务一次性迁移，不进入日常 runtime。

### 12.2 Migration converter 必须识别

```text
h1 / h2 / h3
strong / b
em / i
s / strike / text-decoration line-through
<a href>
ordered / unordered list
旧 Qt quote 几何
旧 Qt code-block 几何
20px span
纯文本正文换行
```

旧 Qt 特殊规则：

- 先判断 code block 几何；
- 再判断 quote margin；
- 顺序与旧 editor 恢复逻辑一致，避免重叠误判。

字号迁移：

```text
旧 15px → 默认正文，不写 mark
旧 17px → 默认正文，不写 mark
旧 20px → :size[…]{size="20"}
```

旧系统中 Markdown 无法表示且新产品明确不支持的格式，例如某些颜色/下划线：

```text
不得静默丢弃
→ migration report 逐篇列出“已知丢弃格式”
```

### 12.3 纯文本正文

若旧正文没有 HTML 标签：

```text
按 plain text 处理
逐行保留换行
空行保留段落
```

禁止用会把所有 whitespace 压成单个空格的 HTML data handler。

---

## 13. Migration 数据 Gate

所有真实 83 篇笔记全部验证，不抽样。

### Gate A｜可见文字

旧正文基准明确使用现有：

```text
exporting/ordinary.py 的 html_to_text
```

而不是会过度折叠空白的 relation helper。

比较：

```text
markdown_to_plain_text(new_markdown)
≈
html_to_text(old_body)
```

做合理 whitespace normalization 后逐篇相等。

目标：用户能看见的字一个不少。

### Gate B｜可保留结构

逐篇比较旧正文与新 Markdown 中：

```text
heading count
list item count
link count
code block count
quote block count
20px mark count
```

新旧相等。

### Gate C｜Milkdown 稳定性

83/83：

```text
load → serialize → load → serialize
```

第二次后逐字节稳定。

Gate C 必须由 Package 2 交付的正式 round-trip checker 自动运行，不允许 Package 3 手工逐篇打开 83 篇代替。

### Gate D｜真人打开格式笔记

至少真人打开：

```text
heading
list
code
quote
link
20px
```

确认视觉和编辑行为正确。

### Gate E｜DB 副本完整性

对迁移前、迁移后的正式 DB 副本分别执行：

```text
PRAGMA integrity_check
```

并记录这些表的总行数：

```text
notes
note_tags
note_ai_targets
ai_receipt_inbox
reminders
```

除正文 schema 的预期变化外，不应误伤其他业务表。

---

## 14. Migration 执行模型

Markdown migration **绝不能放进 `initialize_database()`**。

原因：正式数据库会被多个独立进程打开：

```text
Desktop App
Reminder helper
Gale pull helper
MCP processes
```

禁止任何 helper 因“新版 schema 初始化”而自动迁正式 DB。

正式 migration 必须是独立 CLI：

```text
scripts/migrate_markdown_body.py --db <path>
```

### 14.1 schema_meta marker

迁移完成后写：

```text
body_format=markdown
```

新 App / helper / MCP：

```text
marker 存在
→ 正常启动

marker 缺失
→ fail closed
→ 提示需要 migration
→ 不自动迁
→ 不 fallback HTML
```

CLI 重复执行：

```text
marker 已存在
→ 拒绝重复迁移
```

---

## 15. 已知 active runtime 正文接点

Package 2 必须逐一处理，不能只改主 NoteService。

至少包括：

```text
Note domain model / ports / mappers
NotesRepository
MainWindow / shell editor wiring
trash note SELECT
AI MCP operations
AI history before_snapshot
AI receipt inbox / accept command
local MCP materialize / append pre-read
AI safe export
ordinary JSON / Markdown export
Gale mirror renderer
search
relations
```

历史 audit 行如果只是不可变历史记录、没有 runtime 读取者，可保留旧 payload，不为迁移历史审计记录重写全库；但新写入一律使用 `content_markdown`。

防屎山检查不得 grep 裸 `QTextEdit`，因为 AI Center / Reminder Center 仍合法使用 QTextEdit 编辑纯文本。

应检查的是笔记正文 active path，例如：

```text
NotesEditor
content_html
body_rich_text
setHtml
toHtml
_plain_text_to_html
HTML-or-Markdown fallback
```

冻结 R1 与 migration code 排除在外。

---

## 16. Package 1｜正式 Markdown Editor Component

### 目标

把已经通过 lab 的 Milkdown 编辑器做成正式 S-notes 内部组件，但**暂时不接真实 Notes 数据和正式数据库**。

### 实现

包含：

```text
Milkdown source
Vite build
QWebEngineView wrapper
QWebChannel thin bridge
load_id
loadComplete
loadFailed
parser total-function protection
off-the-record WebEngine profile
external navigation interception
theme
read-only
Raw Markdown read-only view
format toolbar
find / replace
```

复用已验证体验：

```text
17px default
15 / 20 directive
line-height 1.9
paragraph gap 11px
H1/H2/H3
bold / italic / strike
list / quote / code / link
undo / redo
close handshake
```

中文 IME 已由 S 于 2026-09-21 在 lab 原型真人验收 PASS。

### 不做

```text
不改 schema
不改 NoteService 正文契约
不迁真实数据
不碰 AI/MCP/Gale/Reminder
```

### Package 1 Gate

自动 / 工程验证：

```text
local static bundle PASS
load_id PASS
loadComplete always arrives on successful load PASS
first loadComplete does not dirty PASS
late old-load_id callback ignored PASS
empty note load → loadComplete arrives → can type and save PASS
switch between two identical-body notes → both saveable PASS
unknown directive does not blank body PASS
loadFailed blocks save PASS
candidate .app builds PASS
codesign PASS
runs without Node/dev server PASS
actual App size reported
```

S 真人验收：

```text
中文输入
中英混输
光标中间插入
删除 / Enter
中文输入到一半切另一篇
15/17/20
H1/H2/H3
bold / italic / strike
list / quote / code / link
网页 / ChatGPT rich paste
undo / redo
find / replace
三主题
Raw Markdown view
未知 ::: / :xxx / 非法 size directive 不丢内容
```

PASS 后停止，不进入 Package 2。

---

## 17. Package 2｜Markdown Core Cutover

### 目标

在代码层完成 active runtime 正文契约切换；只使用 test / candidate DB，不碰正式 DB。

### 修改范围

```text
models
ports
mappers
NoteService
NotesRepository
MainWindow / shell editor wiring
fresh DB schema
search
relations
export
AI read/write body entry points
AI receipt / history snapshot body fields
Local MCP body renderer
Gale mirror body renderer
trash SELECT
相关 active tests
```

### 同时完成

```text
content_markdown TEXT NOT NULL DEFAULT ''
markdown_to_plain_text()
process-local derived-text cache keyed by (note_id, revision)
migration-only CLI
schema_meta body_format marker
new runtime missing marker → fail closed
explicit pinned Markdown parser dependency
offscreen formal-bundle round-trip checker
```

round-trip checker 采用：

```text
scripts/markdown_roundtrip_check.py
→ offscreen QWebEngineView
→ 驱动正式 bundled Milkdown
```

目的：Package 3 Gate C 使用**与正式 App 相同的 Milkdown bundle**批量检查 83 篇，不维护第二套 parse/serialize 实现。

### AI / MCP / Gale

```text
AI safe export → Markdown
MCP get/search/read → plain text
Gale mirror → plain text
redaction → plain text
AI create/append/receipt acceptance → Markdown input semantics
```

### Redaction 测试必须包含

```md
se**cr**et
*sec*ret
:size[secret]{size="20"}
[secret](https://example.com)
`secret`
```

上述派生 plain text 均应为 `secret`，再由现有 redaction 捕获。

### 性能验证

在正式 83 篇 DB 副本规模上报告：

```text
cold search / relations timing
warm cache search / relations timing
```

并验证：

```text
未变化 note 的 revision 未变
→ 不重复解析 Markdown
```

不因为性能问题新增持久化 `content_text`。

### 退休 active runtime

```text
Qt HTML note editor
HTML note toolbar path
HTML paste compensation chain
HTML clear-format compensation chain
HTML append/prepend
_plain_text_to_html
HTML→Markdown export path
content_html / body_rich_text active model contract
```

### Package 2 验证

至少覆盖：

```text
load/save
autosave
switch note
exit
revision conflict
paste
undo
format
search
relations
derived-text cache invalidation
export
AI create/append/approval as Markdown
AI safe read
redaction through Markdown marks
MCP get/search plain-text contract
Gale mirror plain-text contract
migration CLI marker / fail-closed behavior
round-trip checker
```

正式 DB 仍然不碰。

PASS 后停止。

---

## 18. Package 3｜真实 DB 副本迁移 + 防屎山 Gate

### 目标

对当前正式数据库副本完成完整 Markdown migration 和 candidate App 真人日用验收。

当前已知规模：

```text
notes = 83
body ≈ 2.5 MB
images = 0
colors = 0
tables = 0

formatting roughly:
headings 3
lists 8
code blocks 2
quote 1
link 1

AI receipts = 67
```

### 步骤

```text
复制正式 DB
→ 记录迁移前 PRAGMA integrity_check + 关键表行数
→ 独立 migration CLI
→ 写 body_format=markdown
→ 记录迁移后 PRAGMA integrity_check + 关键表行数
→ candidate App 打开副本
→ 自动 Gate A/B/C/E
→ 真人 Gate D + 日常使用
```

### Migration report

必须报告：

```text
83/83 visible text result
83/83 structure result
83/83 Milkdown stability result
PRAGMA integrity_check before/after
关键业务表 row counts before/after
逐篇已知丢弃格式
旧 15px → 新默认 17px 的预期视觉变化
```

### 防屎山 Gate

active R2 runtime 中不应再存在笔记正文路径：

```text
content_html
body_rich_text
HTML fallback
Qt HTML NotesEditor
setHtml/toHtml note flow
_plain_text_to_html
```

例外：

```text
migration-only converter
冻结 R1 code
不可变旧 audit payload（若无 runtime 读取）
```

### S 真人 Gate

用 candidate DB + candidate App 做真实日常测试：

```text
随机旧笔记
格式旧笔记
编辑
切换
退出重开
搜索
关联笔记
回收站
标签 / 分类
AI Center
AI create / append（Markdown 格式）
Raw Markdown
MCP/Gale read smoke
```

PASS 后才允许 Package 4。

---

## 19. Package 4｜Production Cutover

这是唯一允许触碰正式 DB 的 Package。

### 固定顺序

```text
1. 退出正式 S-notes
2. 停止所有会打开正式 DB 的 helpers / MCP processes
3. 保留一份正式 DB backup
4. 保留一份旧正式 App
5. 固定最终代码 revision
6. build + codesign 新正式 App/runtime
7. 显式运行 migration CLI
8. 确认 body_format=markdown
9. 启动新正式 App
10. 功能 smoke
11. Gale mirror 全量重生成
12. Gale publish smoke
```

禁止：

```text
旧 App 和新 helper 混跑
helper 自动迁库
production 临时加 HTML fallback 救火
```

### Production Gate

只检查必要项目：

```text
App opens
note count correct
random notes open correctly
edit/save/reopen
search
tags/categories
trash
AI Center
AI read/write smoke
MCP body output smoke
Gale mirror rebuild + publish smoke
Reminder unaffected
```

### Rollback

若出现阻断级问题：

```text
停止新 App / helpers
恢复旧 DB backup
恢复旧 App
```

不现场补双格式 compatibility。

---

## 20. 本轮明确不做

```text
不重建 S-notes
不创建 vNext repo
不做实体 .md authority
不做 Obsidian 双向同步
不做新 FTS 架构
不新增第二套正文 representation
不重做 Reminder
不重做 Gale
不重做权限模型
不重构冻结 R1
不新增任意 CSS 格式系统
不顺手清无关模块
```

---

## 21. 最终成功状态

```text
BODY_AUTHORITY=SQLITE_MARKDOWN
EDITOR=MILKDOWN_WYSIWYG
PYTHON_WEB_BRIDGE=THIN
HTML_CANONICAL_BODY=NO
EDITOR_JSON_CANONICAL_BODY=NO
DUAL_FORMAT_RUNTIME=NO
RAW_MARKDOWN_VIEW=READ_ONLY

BODY_DEFAULT=17px
BODY_OPTIONAL=15px,20px
LINE_HEIGHT=1.9
PARAGRAPH_GAP=11px
HEADINGS=H1,H2,H3

REVISION_MODEL=PRESERVED
OPEN_NOTE_DOES_NOT_DIRTY=YES
LOAD_COMPLETE_EXPLICIT=YES
EMPTY_NOTE_SAVEABLE=YES
IDENTICAL_BODY_SWITCH_SAFE=YES
LOAD_FAILURE_CANNOT_OVERWRITE_BODY=YES

MIGRATION=EXPLICIT_CLI
AUTO_SCHEMA_BODY_MIGRATION=NO
MIGRATION_VISIBLE_TEXT_GATE=YES
MIGRATION_STRUCTURE_GATE=YES
MIGRATION_MILKDOWN_ROUNDTRIP_GATE=YES
MIGRATION_DB_INTEGRITY_GATE=YES

PLAIN_TEXT_DERIVED_CACHE=MEMORY_ONLY
PERSISTED_CONTENT_TEXT=NO

AI_SAFE_EXPORT=MARKDOWN
MCP_BODY_OUTPUT=PLAIN_TEXT
GALE_MIRROR_BODY=PLAIN_TEXT
REDACTION_INPUT=PLAIN_TEXT
AI_CREATE_APPEND_INPUT=MARKDOWN
AI_RECEIPT_ACCEPT_INPUT=MARKDOWN

OLD_HTML_COLUMNS=REMOVED
OLD_QTEXTEDIT_NOTE_EDITOR=RETIRED
REMINDER=UNCHANGED
R1_FROZEN=YES
```

---

## 22. 施工规则

以后 Codex 施工只认本文件：

```text
S-notes Markdown Retrofit 最小施工方案 v1.2 FINAL
```

v1.0、v1.1、Claude 两轮审稿文件仅作为决策历史，不再作为并行施工输入。

每个 Package：

```text
只完成本包
完成 Gate
报告结果
停止
等待下一包指令
```

不要“顺手”进入下一 Package。

若施工时发现新问题：

- 只在它直接阻断当前 Package 且属于小范围修复时处理；
- 若需要改变 schema、正文 authority、bridge 边界、安全模型或 Package 边界，停止并报告；
- 不用临时 HTML fallback、第二正文列、第二 parser、第二写入路径绕过去。
