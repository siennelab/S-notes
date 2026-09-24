# S-notes GreenCloud 3.5｜最终架构与施工方案 v2.1 FINAL

**日期：2026-09-22**
**状态：四审候选 / 尚未拆分正式施工单**
**基线：S-notes `main` `f2e280f`，Markdown Production 已 CLOSED**
**用途：3.5-0 → 3.5-D 的唯一施工母本**

---

# 0. v2.1 修订说明

v2 总架构不变：

```text
Notes
Mac 主
GreenCloud 安全镜像 + proposal

Reminder
GreenCloud 主
Mac 远程管理
```

本次 v2.1 吸收源码四审前审稿意见，重点修正：

1. `prepend_note / append_note / update_note_metadata` MCP 工具被顶层 `anyOf` 隐藏的真实根因；
2. Milkdown canonical serialization 与 AI Markdown write 的稳定性契约；
3. S-notes 私有 Markdown dialect；
4. `revision / updated_at / version token` 三者关系；
5. Cloud Notes proposal 直接扩展既有 Gale R1B outbox，不另造第二套；
6. ntfy 鉴权与公共 `ntfy.sh` fallback；
7. Mac Reminder helper 在重新打包后“复活”的风险；
8. mirror atomic replace 后 SQLite 旧连接问题；
9. Cloud proposal 排队上限；
10. Reminder API schema mismatch；
11. Reminder 时区、reboot catch-up、多通知节点 Cloud 再验收；
12. GreenCloud 当前正式 UID / actor 映射。

上述 A1–A7 均来自源码逐条核验，应视为定稿前必须吸收项。

---

# 1. 当前正式正文基线

S-notes 已经完成 Markdown Retrofit。

唯一正文 authority：

```text
notes.content_markdown
```

schema marker：

```text
schema_meta.body_format = markdown
```

旧：

```text
content_html
body_rich_text
```

已退出 active runtime。

明确禁止重新引入：

```text
HTML canonical body
HTML + Markdown dual runtime
filesystem .md source of truth
普通启动自动 migration
```

搜索、MCP、Gale、relations、redaction 等需要 plain text 的地方统一：

```text
content_markdown
→ markdown_to_plain_text()
→ derived plain text
```

当前 Production MCP / Gale read 仍以 plain text 为正式已有契约；AI write 则已经以 Markdown 为输入。

---

# 2. Authority 模型

## Notes

```text
Mac S-notes
= 正式 Notes DB
= 正式 Markdown 正文
= 唯一最终事实源
```

GreenCloud：

```text
= actor-specific 安全镜像
= Cloud AI 读取入口
= Notes proposal / receipt 中转
≠ 正式 Notes authority
```

禁止：

```text
Mac / GreenCloud 双主 Notes
```

## Reminder

```text
GreenCloud
= 正式 Reminder DB
= 唯一 scheduler
= ntfy executor
```

Mac：

```text
= S-notes Reminder UI
= owner 远程客户端
= offline read-only snapshot
```

禁止存在第二份可写 Mac Reminder state。

---

# 3. 总体架构

```text
                         Mac
                          │
            ┌─────────────┴─────────────┐
            │                           │
          NOTES                      REMINDER
            │                           │
            ▼                           ▼
   正式 Markdown SQLite         S-notes Reminder UI
      【唯一 Notes 真本】                │
            │                           │ HTTPS
   ┌────────┴────────┐                  ▼
   │                 │             GreenCloud
local AI MCP    actor mirrors       Reminder API
   │                 │                  │
   │                 │            ReminderService
   │                 │                  │
AI Center        Cloud AI          Cloud SQLite
   │             read/write        【唯一 Reminder 真本】
   │                 │              /          \
   │          existing R1B      scheduler      backup
   │          proposal outbox       │
   │                 │             ntfy
   ◄──── Mac pull / receipt
```

---

# 4. GreenCloud 当前 OS 身份映射

GreenCloud 已经实际建立：

```text
claude
UID 2001
/srv/claude-code
LAX

openclaw
UID 2002
/srv/openclaw
LAX

letwatch
UID 2003

denode
UID 2004

ntfy
UID 2005
```

其中：

```text
Notes actor claude-code
→ OS user claude / UID 2001

Notes actor clawbot
→ OS user openclaw / UID 2002
```

这两个 LAX 用户已有 fail-closed 路由地基。

Mac 本地砚 actor：

```text
yan
```

`codex` 是另一个 actor ID，不得混用。

3.5-B 新增专用：

```text
snotes
```

service identity，用于 Reminder API / scheduler / Notes outbox 等正式数据服务。

其 UID 在施工时从 GreenCloud 实际空闲 UID 中分配，不在架构书里猜死数字。

---

# 5. Actor registry

生成 actor-specific mirror 时：

**必须使用正式 `ai_actor_registry`。**

不得依赖旧静态 `ai_targets.py` 列表。

否则会漏掉已经存在于 registry 中的：

```text
claude-code
```

这一点作为施工硬要求。

---

# 6. Notes actor-specific mirror

至少生成：

```text
/srv/snotes/mirror/
├─ claude-code/current.db
└─ clawbot/current.db
```

权限在 Mac 生成镜像时就完成裁剪。

```text
common
→ 两份都有

claude-code only
→ 只进 claude-code

clawbot only
→ 只进 clawbot

hidden / unauthorized
→ 对应 mirror 根本不存在
```

Cloud read MCP 不再拿全集以后现场决定“该不该给 AI 看”。

---

# 7. Mirror 正文契约

Production 现有 plain-text contract 不破坏。

每条 mirror Note 逻辑上至少包含：

```text
source_note_id
source_version
visibility
targets

safe_text
safe_markdown | null
```

## full

actor 已获 full 权限：

```text
safe_text
= content_markdown → plain text

safe_markdown
= canonical content_markdown 原文
```

这里的：

```text
safe_markdown
```

**不经过任何 redaction。**

因为：

> `full` 的正式语义就是该 actor 获得零脱敏正文。

不能施工时顺手做一个“半脱敏 Markdown”。

## redacted

```text
content_markdown
→ plain text
→ Mac redaction
→ safe_text

safe_markdown = null
```

redacted：

```text
可 search
可 get plain text
可 append
不可 edit
不可取 Markdown source
```

## hidden / unauthorized

完全不存在。

这条 redaction 边界按四审建议写死。

---

# 8. 不破坏 Gale Production

当前 Gale 已经完成：

```text
Markdown mirror rebuild
validation
production publish
search/get
```

因此 GreenCloud mirror 可以复用：

* actor filtering；
* SQLite mirror；
* FTS；
* source version；
* validator；
* atomic publish 思想。

但不得为了 GreenCloud 改坏现有 Gale consumer。

如果：

```text
给 existing mirror schema 增加 nullable safe_markdown
```

能够完全兼容现有 Gale，则可以共用。

如果会扩大 Gale Production 风险：

```text
Gale 保持原 contract
GreenCloud 使用扩展 mirror contract
```

3.5 不顺手退休 Oracle Gale。

---

# 9. Mirror publish

默认：

```text
每 5 分钟检查
+
手动立即刷新
```

流程：

```text
Mac 正式 DB
→ actor candidate
→ verify
→ hash
→ unchanged：结束
→ upload incoming
→ GreenCloud verify
→ atomic replace current.db
```

candidate 损坏：

```text
current.db 不动
```

mirror metadata 至少：

```text
generated_at
hash
note_count
source schema
```

Mac 关机：

```text
继续读最后一次成功镜像
```

---

# 10. Atomic replace 后的 SQLite 连接

这里明确禁止 Cloud read MCP 永久缓存：

```text
current.db
```

的 SQLite connection。

原因：

```text
atomic replace
→ pathname 指向新 inode
→ 已打开 fd 仍指向旧 inode
→ 持久 connection 会继续读旧 mirror
```

v1 直接采用最简单规则：

```text
每个 MCP request
→ open immutable/read-only SQLite
→ query
→ close
```

不做长期 connection cache。

以后若性能真成为问题，可以基于 inode/hash 做 reopen cache；3.5 不提前优化。

这一项必须进入 mirror refresh 验收。

---

# 11. Notes transport

mirror / proposal 属于后台批量任务。

继续采用窄 SSH transport。

Mac 专用 SSH key：

```text
仅用于 S-notes transport
```

GreenCloud `authorized_keys`：

```text
command="<固定接收脚本>",restrict
```

不给普通 shell。

Mac：

```text
scp
+
ssh BatchMode 固定命令
```

mirror 目录：

```text
snotes service
= owner

claude UID 2001
= 只读 claude-code mirror

openclaw UID 2002
= 只读 clawbot mirror
```

任何 actor UID 不得读取另一 actor 的 mirror。

Notes 不因此新增公网 REST API。

---

# 12. Notes read tools

普通：

```text
search_notes
get_note
```

继续返回 safe plain text。

编辑 full Note 需要新增：

```text
get_note_markdown
```

只有：

```text
actor full-authorized
+
actor 拥有 edit capability
```

才允许调用。

返回至少：

```text
note_id
version
markdown
dialect
```

redacted / hidden：

```text
get_note_markdown
→ not_found
```

---

# 13. S-notes Markdown dialect

S-notes 不是“任意 CommonMark 文本”。

Production 已存在自己的 Markdown 方言。

必须给 AI 明确说明。

定义第一版：

```text
dialect = snotes-markdown-v1
```

至少覆盖：

```text
CommonMark / GFM 基础

GFM:
- strikethrough
- task list
- table

S-notes extension:
:size[...]{size="15"}
:size[...]{size="20"}

合法 size 只允许：
15
20

以及当前编辑器允许的 list / heading 组合语义
```

未知 directive 或非法 size 不应被 AI 当成“垃圾语法”自行清除。

`get_note_markdown`：

* response 返回 dialect ID；
* tool description 写明扩展集合与合法值。

`edit_note` tool description 同样写明：

> 必须保持 S-notes Markdown dialect；不得自行删除不认识的 S-notes directive。

新增验收：

```text
含 :size 指令的 Note
→ AI edit
→ accept
→ size directive 仍生效
```

这是源码已经存在的真实 Markdown contract。

---

# 14. Markdown canonicalization

Milkdown 加载正文时会重新序列化出自己的 canonical Markdown，并把它作为 editor baseline。

因此：

> **所有 AI 正文写入在正式落库前必须 canonicalize。**

覆盖：

```text
create
prepend
append
edit
```

不得出现：

```text
AI 写一种合法但非 canonical Markdown
→ DB 原样保存
→ S 下一次打开/保存
→ Milkdown 静默重写整篇
```

这会造成无意义 version churn 与大量 pending conflict。

源码已经确认 Milkdown 当前确实以重新序列化后的 canonical 内容作为 baseline。

---

# 15. Canonicalizer 实现原则

canonical contract 必须与正式 Milkdown 编辑器一致。

优先：

```text
共享 Milkdown / remark serializer 配置
→ 独立可调用 canonicalizer
```

不应另外凭感觉维护一套完全独立 Markdown 语法。

如果最终为了 headless MCP / application layer 必须由 Python canonicalize：

必须有 golden fixtures 证明：

```text
Python canonical output
==
Milkdown load → serialize output
```

覆盖至少：

* paragraph；
* heading；
* bold / italic / strike；
* link；
* lists；
* task；
* table；
* escaping；
* trailing newline；
* S-notes `:size` dialect。

正式 acceptance Gate：

```text
AI output
→ canonicalize
→ DB
→ Milkdown load
→ Milkdown serialize
→ ZERO DIFF
```

不是只验：

```text
看起来能正常显示
```

---

# 16. 版本与并发凭证

S-notes 当前有两套并发标记。

## MCP / mirror

实际 version token：

```text
note_version_token(note_id, updated_at)
= sha256(note_id + updated_at)
```

mirror：

```text
source_version
```

使用同一个算法。

因此：

```text
Cloud expected_version
```

天然可以与 Mac 当前 formal Note 比较。

## GUI

GUI 编辑使用：

```text
notes.revision
```

revision 计数。

两套都保留，不另造第三套 version。

源码事实已经核实。

---

# 17. 正式写入时 version bump

任何正文接受：

```text
create / prepend / append / edit
```

或其他真正改变 Note 内容的写入，都必须在同一事务中正确更新：

```text
revision
updated_at
```

然后新的：

```text
version token
```

自然由新 `updated_at` 派生。

硬规则：

> **任何修改正文却不更新 `updated_at` 的写路径都是 bug。**

否则 version token 不变，会漏掉 stale edit。

---

# 18. GUI 保存与 pending conflict

S 在 WYSIWYG 中保存 Note，只要正式 Note 发生修改：

```text
revision / updated_at
→ bump
```

即使 S 改的是 AI pending edit 完全无关的另一段：

```text
pending expected_version
≠ current version
→ conflict
```

这是故意选择的保守模型。

不做：

```text
自动 section merge
AI 猜测“其实不冲突”
三方自动合并
```

用户重新 review 即可。

Canonicalization 做稳以后，不应因为“单纯打开再保存、内容完全没变”产生非预期格式 diff；这由 round-trip ZERO DIFF Gate 保证。

---

# 19. MCP 修改类工具 schema 修复

当前源码的真实 bug 已定位：

```text
prepend_note
append_note
update_note_metadata
```

三个 tool input schema 顶层被注入：

```text
anyOf
```

Claude Code 不接受这种顶层 schema，于是整组工具直接从工具清单消失。

3.5-0 明确修复：

**对 MCP 公共 tool contract 统一使用 `expected_version`。**

删除顶层：

```text
anyOf(
 expected_version
 expected_updated_at
)
```

不再向 AI 暴露 legacy `expected_updated_at` 二选一。

旧 internal compatibility 如暂时仍需要，可以留在 application 内部；不得继续污染 MCP schema。

修改类 AI tool：

```text
prepend_note
append_note
update_note_metadata
edit_note
```

统一以：

```text
expected_version
```

作为 optimistic concurrency credential。

---

# 20. Notes AI 权限

## Mac 本地 AI

```text
search_notes          ✅
get_note              ✅
get_note_markdown     ✅ full-only

create_note           ✅
prepend_note          ✅ existing modify confirmation
append_note           ✅ existing modify confirmation
update_note_metadata  ✅ existing modify confirmation
edit_note             ✅ new modify confirmation

delete_note           ❌
修改 visibility        ❌
修改 actor targets     ❌
```

修复 schema 是恢复已有本地能力，不等于扩权。

## Cloud AI 3.5

Cloud writeback 本轮只开放：

```text
create ✅ proposal
append ✅ proposal
edit   ✅ proposal
```

不在 3.5 Cloud allowlist 中增加：

```text
prepend
update_note_metadata
delete
```

以后有真实需求再单独开。

---

# 21. append_note

Cloud append：

```text
AI
→ proposal
→ Mac
→ AI Center
→ S accept
→ canonicalize result
→ formal Markdown
```

允许：

```text
full
redacted
```

accept transaction 中重新检查：

1. Note 仍存在；
2. 未删除；
3. actor 当前仍有权限；
4. expected_version 匹配；
5. request_id 未执行；
6. canonical result 可 round-trip。

proposal 提交之后权限被撤销：

```text
reject / conflict
```

不能使用旧权限继续 append。

---

# 22. edit_note

只允许：

```text
full
```

AI：

```text
get_note_markdown
→ canonical Markdown + version + dialect
```

提交：

```text
note_id
expected_version
request_id

edits:
  - old_markdown
    new_markdown
  - old_markdown
    new_markdown
```

一次 proposal 可包含多组 edit。

必须：

```text
all-or-nothing
```

不再使用任何 HTML surgery。

---

# 23. edit_note proposal precheck

提交时：

1. actor full-authorized；
2. Note 存在于 actor mirror；
3. expected_version 与 mirror `source_version` 一致；
4. 每个 `old_markdown` 必须唯一命中；
5. request_id 幂等；
6. 新 Markdown 不明显违反已知 dialect。

Cloud precheck 不是最终 authority。

最终仍由 Mac acceptance 决定。

---

# 24. edit_note acceptance

Mac AI Center accept 时，在正式 transaction 重新检查：

1. Note 存在；
2. 未删除；
3. actor 当前仍 full-authorized；
4. expected_version 等于当前 version token；
5. 所有 `old_markdown` 当前仍唯一命中；
6. 整批 edits 可以全部应用；
7. result 经过 S-notes canonicalizer；
8. canonical Markdown 合法；
9. result 非非法空正文；
10. revision + updated_at 同时正确更新。

成功：

```text
apply all edits
→ canonicalize
→ history
→ revision bump
→ updated_at bump
→ new version
→ receipt accepted
```

失败：

```text
formal body ZERO WRITE
→ rejected / conflict
```

---

# 25. Markdown edit 可以修改语义格式

因为 AI 真正读取：

```md
**香港汇丰**
[HSBC](https://...)
:size[正文]{size="15"}
```

所以允许 edit：

* bold；
* italic；
* heading；
* link；
* list；
* table；
* S-notes 已知 dialect。

不再保留旧 HTML 方案的：

```text
链接文字禁止修改
跨 span 禁止
单 text node
HTML offset
QTextCursor
```

限制。

但第一版仍不允许：

```text
new_body = AI 重写整篇正文
```

优先 exact replace，方便人类看 diff，也减少误改范围。

---

# 26. Cloud proposal：直接扩现有 R1B outbox

**禁止新造第二套 Notes outbox。**

3.5-D 直接 generalize 当前：

```text
gale_submission_outbox
```

及现有 R1B pull 路径。

已有表保留：

```text
submission_uuid
source_actor
idempotency_key
content
delivery_state
...
```

扩：

```text
operation_type
  create | append | edit

note_id
expected_version
payload_json
```

`source_actor` CHECK 保留：

```text
gale-staging
gale
```

并加入：

```text
claude-code
clawbot
```

`delivery_state` 在既有：

```text
queued
delivered
acknowledged
```

基础上加入 terminal：

```text
accepted
rejected
conflict
```

现成 pull helper 复用 / generalize。

不再并排养：

```text
Gale outbox
+
GreenCloud outbox v2
```

这一决定按源码现状写死。

---

# 27. Notes outbox 文件权限

不仅 Reminder DB，Notes outbox DB 也属于正式有状态服务数据。

GreenCloud：

```text
snotes service identity
= outbox DB owner
```

Cloud AI：

```text
claude UID 2001
openclaw UID 2002
```

没有 outbox SQLite 文件权限。

写 proposal：

```text
AI MCP
→ localhost outbox service
→ DB
```

绝不：

```text
AI MCP process
→ sqlite3 file directly
```

---

# 28. Cloud proposal 排队上限

Mac 可能离线数天。

为防：

```text
AI 连续提交
→ 数百 pending
→ Mac 回来 AI Center 爆炸
```

3.5 第一版：

```text
每 actor
最多 50 个 non-terminal pending proposal
```

non-terminal 包括尚未：

```text
accepted
rejected
conflict
```

的请求。

检查顺序：

```text
先 idempotency
再 queue limit
```

避免重复 request 错占名额。

超过：

```text
queue_full
```

AI Center 增加：

```text
按 actor 批量 reject
```

不做 batch accept。

这一项来自离线场景审查。

---

# 29. Proposal receipt

Cloud AI：

```text
get_operation_status
```

查询自己的 request。

终态：

```text
accepted
rejected
conflict
```

Cloud 不返回其他 actor operation。

Mac 做出决定后：

```text
receipt
→ existing transport
→ Cloud outbox status
```

accepted 时立即：

```text
trigger actor mirror publish
```

不等 5 分钟。

---

# 30. Reminder authority

GreenCloud：

```text
formal Reminder SQLite
ReminderService
scheduler
ntfy executor
```

Mac S-notes：

```text
RemoteReminderClient
owner UI
offline compatible snapshot
```

只有一份正式 Reminder state。

---

# 31. Reminder DB / service identity

GreenCloud：

```text
snotes
├─ Reminder API
├─ Reminder scheduler
└─ Notes outbox service
```

Reminder DB：

```text
snotes only
```

AI UID 没文件权限。

SQLite：

```text
WAL
busy_timeout
```

当前规模不引入：

```text
Postgres
Redis
broker
```

---

# 32. Reminder API

读取：

```text
GET /v1/reminder/snapshot
```

至少返回：

```text
schema_version
server_time
snapshot_time

labels
tables
items
settings
badge_count
```

写操作全部是明确 command。

不提供：

```text
arbitrary SQL
generic object mutation
filesystem RPC
```

---

# 33. Reminder schema version mismatch

3.5-0 会加入多提醒节点，因此 App 与 Cloud schema 未来可能短暂版本错位。

Mac 收到：

```text
schema_version
```

若高于自己支持范围：

```text
不解析未知 live snapshot
不发送任何 write
```

UI：

```text
版本不匹配
只读
```

可以继续显示：

```text
last compatible cached snapshot
```

并明确标记旧数据时间。

绝不“尽量解析看看”。

---

# 34. Reminder owner credential

Mac owner token：

```text
macOS Keychain
```

建议通过：

```text
security CLI
```

读取；若项目已有可靠 keyring 依赖也可使用等价 Keychain bridge。

硬边界：

```text
只有 GUI process / GUI worker 获得 owner token
```

不得给：

```text
Claude Code
yan
gale-production-pull
其他 launchd helper
```

后台 Notes helper 继续使用自己的 SSH key，不需要 owner token。

owner token 不进入：

```text
s_notes.db
.env shared with AI
AI process environment
```

---

# 35. Reminder actor credential

Cloud AI：

```text
claude-code
clawbot
```

各自固定 service credential。

Mac local AI：

```text
yan
claude-code
```

调用 Reminder 时使用自己的 actor credential。

服务端按 credential 固定：

```text
actor identity
command allowlist
visibility
```

不接受 request body 自报 actor。

---

# 36. Reminder Caddy

采用：

```text
Caddy
HTTPS
narrow Reminder API
```

可使用：

```text
snotes.ke-den.de
```

但 Caddy 施工必须：

```text
新增独立 site block
```

不得修改、重写或顺手整理已封工的：

```text
ntfy.ke-den.de
```

block。

GreenCloud 已有其他 ke-den.de 服务，3.5 只追加自己需要的配置。

---

# 37. Reminder Mac UI

网络调用不得运行在 PySide GUI 主线程。

必须：

```text
RemoteReminderClient
→ worker thread / Qt async
```

要求：

* loading；
* 3–5 秒级合理 timeout；
* command pending 时相关按钮 disable；
* error state；
* cancel/stale response 不覆盖新 UI；
* App 主窗口始终响应。

---

# 38. Reminder offline

Cloud 不可达：

```text
Notes 正常
App 正常
Reminder = last compatible cached snapshot
```

显示：

```text
离线
snapshot 时间
```

写操作：

```text
全部 disabled
```

绝不：

```text
fallback old Mac DB
离线排队写 Reminder
重启 Mac scheduler
```

owner offline cache：

```text
GUI-only
```

Mac MCP 不得读取。

---

# 39. Reminder AI visibility

全部 Reminder 数据都在 Cloud formal DB，包括：

```text
ai_visible = false
```

owner：

```text
全部可见
```

AI：

```text
只有 ai_visible=true
```

并且：

```text
list
get by id
update
complete
pause
resume
```

全部应用 visibility。

AI-invisible：

```text
对 AI = not_found
```

不能猜 ID 绕过。

---

# 40. Reminder 迁云不扩 AI 权限

Cloud migration 不自动开放：

```text
delete reminder
delete table
delete label
修改 ai_visible
```

当前正式 allowlist 是多少，cutover 后就保持多少。

底层 ReminderService 有某个函数：

```text
≠ AI 自动获得该能力
```

以后另开权限扩展。

---

# 41. Reminder ntfy

Cloud endpoint 固定：

```text
https://ntfy.ke-den.de
```

正式 Reminder transport 增加鉴权。

credential 优先：

```text
systemd LoadCredential=
```

如果实现需要 EnvironmentFile：

```text
0600
snotes owner
```

也可。

严禁：

```text
credential 进 DB
credential 回 Mac snapshot
user:password@URL
```

---

# 42. 删除 Reminder helper 的公共 fallback

当前 Mac helper 存在默认：

```text
https://ntfy.sh
```

fallback。

Cloud cutover 后必须删除。

Cloud endpoint：

```text
没有合法配置
→ fail closed
```

不能：

```text
配置为空
→ 偷偷发公共 ntfy.sh
```

这会造成提醒内容外发风险。源码已确认当前 fallback 存在。

注意：

GreenCloud **ntfy server 自己**为了 iOS push 使用的：

```text
upstream-base-url: https://ntfy.sh
```

是另一回事，继续保留。

本条只删除 **S-notes Reminder client/helper 的 endpoint fallback**。

---

# 43. ntfy endpoint / topic ownership

迁 Cloud 后：

```text
endpoint
credential
```

属于 Cloud service config，不再属于 owner 可随意修改的 Reminder data。

snapshot：

```text
不返回 credential
不提供 endpoint 编辑
```

endpoint 最多只读显示。

新的 Reminder topic：

```text
s-*
```

在 cutover 创建。

旧 topic 不做兼容。

iPhone：

```text
重新订阅
→ 锁屏验收
```

---

# 44. Reminder scheduler

Cloud 不使用 Mac launchd。

采用：

```text
systemd calendar timer
每 5 分钟
Persistent=true
```

这里明确要求 **OnCalendar-based** timer。

原因：

`Persistent=true` 只有在 `OnCalendar=` timer 上才会补偿停机期间错过的触发；monotonic `OnUnitActiveSec=` / `OnUnitInactiveSec=` timer 配上 Persistent 并不能达到这个效果。

因此：

```text
OnCalendar: every 5 min
Persistent=true
→ service run_scheduled_once()
→ exit
```

reboot 后如果错过过至少一次：

```text
补跑一次
```

Reminder DB 自己的 notified/idempotency 保证不重复通知。

---

# 45. Reminder 时区

服务器 OS 保持：

```text
UTC
```

不改。

Reminder domain 正式业务时区仍是：

```text
Asia/Shanghai
```

验收必须明确包含：

```text
daily_time = 10:00
→ 北京时间 10:00
```

不能只测 VPS 本地 UTC 时间。

四审已确认当前 domain 时区固定为 Asia/Shanghai。

---

# 46. Mac Reminder helper 必须永久退役

只：

```text
launchctl unload
```

不够。

当前 build / packaging 会重新安装：

```text
com.snotes.reminder-helper.plist
```

未来重新打包 App 会把旧 scheduler 复活。

Cloud cutover 必须同时：

1. bootout / unload 当前 helper；
2. 删除安装中的 plist；
3. build script 删除 Reminder helper plist install；
4. packaging spec 不再重新放回自动调度入口；
5. 必要时 disable legacy launchd label；
6. App startup 做 self-check。

self-check 如果发现：

```text
legacy reminder-helper plist
或 loaded launchd job
```

必须明显告警：

```text
Legacy local Reminder scheduler detected
```

不能静默共存。

---

# 47. Reminder helper 退役验收

正式验收不止：

```text
当前 launchctl 看不到
```

还必须：

```text
重新 build
→ 重新安装正式 App
→ 重启 / 重新登录后
→ helper 仍然不存在
```

以及：

```text
Mac 旧 Reminder DB
→ 永远不再被 scheduler 修改
```

---

# 48. Reminder backup

Cloud：

```text
daily SQLite backup
```

滚动保留。

Mac 在线时周期性拉最新 recoverable backup。

验收：

```text
sqlite3 真打开
integrity check
item count
```

不能只看文件存在。

Cloud Notes outbox 在未 terminal/ACK 前同样属于有状态数据，应进入服务 backup。

Mirror 不需要 backup，因为可由 Mac 重建。

---

# 49. Reminder 一次迁移

正式 cutover：

1. 进入短维护窗口；
2. 暂停 Mac Reminder write；
3. 导出 Reminder 数据；
4. 导入 Cloud；
5. 验 counts；
6. 验 schedules；
7. 验 occurrences；
8. 验多提醒节点；
9. Cloud ntfy；
10. Cloud scheduler；
11. Mac RemoteReminderClient；
12. 退役 local helper；
13. 保留旧 Mac data 静态 backup。

明确迁移：

```text
reminder_labels
reminder_tables
reminders
reminder_occurrences
ai_reminder_operations
```

其中：

```text
ai_reminder_operations
```

是 Reminder 独立表，因此 **整表迁移**。

不再写“能筛就筛，不行重置”。

共享：

```text
ai_operations
```

不整表复制。

---

# 50. Reminder settings migration

迁：

```text
Reminder 业务 settings
```

但旧本地 transport 配置：

```text
reminder/ntfy_endpoint
旧 ntfy topic
credential
```

不作为 Cloud 正式 business settings 搬过去。

Cloud transport 重新按 GreenCloud service config 建立。

避免老 Mac：

```text
ntfy.sh fallback / 旧 topic
```

污染新 authority。

---

# 51. 3.5 前置小修 A｜Reminder 多通知点

正式 Cloud migration 前先完成：

> 一个 occurrence 多个通知点。

例如：

```text
30 days
7 days
3 days
1 day
same day
```

只做：

```text
schema
UI
schedule calculation
notification dedupe
```

不做 SubsTracker 产品扩张。

先 Mac local 验收。

Cloud cutover 后**重新完整验收一遍**：

```text
同一 occurrence
多个 notification points
→ 各发一次
→ 各自不重复
```

不能因为 Mac 通过了，就认为 Cloud 也自动通过。

---

# 52. 3.5 前置小修 B｜Reminder test baseline

进入 3.5-B 前：

当前已经存在的 Reminder-related failing tests 必须处理干净。

目标：

```text
targeted Reminder baseline
= GREEN
```

如果某个测试确认属于明确、故意保留的已知行为：

```text
显式 xfail / skip
+
理由
```

不能保持一个普通红灯，然后让后面的施工 agent 猜：

> “这是我弄坏的，还是祖传的？”

---

# 53. 3.5 前置小修 C｜Notes MCP

修复：

```text
prepend_note
append_note
update_note_metadata
```

顶层 anyOf。

公共 MCP contract：

```text
expected_version only
```

验收：

Claude Code 实际 tools 列表中三个都可发现。

不能只测：

```text
server 注册成功
```

要从 Claude Code client 真实 discovery 验。

---

# 54. 3.5 前置小修 D｜Notes Markdown write

完成：

```text
get_note_markdown
snotes-markdown-v1 dialect
canonicalizer
edit_note
append permission recheck
version/revision contract
```

本地 AI 先通过：

```text
create
prepend
append
update metadata
edit
```

对应正式行为验收。

Cloud writeback 才开始施工。

---

# 55. 施工顺序 v2.1

## 3.5-0｜Local Finalization

Reminder：

```text
多提醒节点
existing Reminder tests 归零
```

Notes：

```text
修三工具 MCP schema
expected_version-only contract
get_note_markdown
Markdown dialect
canonicalizer
edit_note
append permission recheck
version/revision contract
本地真人验收
```

**通过以后才进入 Cloud。**

---

## 3.5-A｜Notes Cloud Read

完成：

```text
actor registry-based mirror generation
claude-code mirror
clawbot mirror
safe_text
full-only safe_markdown
Mac → GreenCloud publish
dedicated SSH transport
verify
hash skip
atomic replace
request-scoped SQLite connection
filesystem isolation
Cloud read MCP
```

只做 read。

---

## 3.5-B｜Reminder Cloud Authority

完成：

```text
snotes service identity
Cloud Reminder SQLite
WAL
Reminder API
owner/actor credential
Caddy site
RemoteReminderClient
GUI worker
schema mismatch read-only
offline snapshot
OnCalendar systemd timer
Persistent=true
Asia/Shanghai 验收
ntfy auth
删除 ntfy.sh client fallback
新 s-* topic
daily backup
one-time migration
永久退休 Mac helper
```

结束后：

```text
Reminder 唯一 authority = Cloud
```

---

## 3.5-C｜AI Access Boundary Ready

完成：

```text
claude-code UID 2001
clawbot/openclaw UID 2002

own mirror only
Reminder localhost API only
actor credentials
tool allowlists
outbox service boundary
```

这一阶段完成权限入口。

不顺手搬完整 agent runtime。

---

## 3.5-D｜Notes Cloud Writeback

直接扩：

```text
existing Gale R1B outbox
existing pull path
```

完成：

```text
create proposal
append proposal
edit proposal
50 pending/actor cap
Mac pull
AI Center
accept/reject/conflict
receipt
get_operation_status
accepted → immediate mirror publish
```

Cloud：

```text
create ✅ confirm
append ✅ confirm
edit   ✅ confirm

prepend ❌ 3.5 cloud
metadata write ❌ 3.5 cloud
delete ❌
```

---

# 56. Notes Cloud Read 验收

必须证明：

1. common 两 actor 都能读；
2. claude-code-only Clawde 不可见；
3. clawbot-only Claude Code 不可见；
4. hidden 完全不存在；
5. redacted 只有 safe plain text；
6. full 有 canonical safe_markdown；
7. full safe_markdown 未被半吊子 redaction；
8. `get_note` plain text 正常；
9. `get_note_markdown` full-only；
10. response 包含正确 dialect；
11. source_version 正确；
12. mirror 生成使用正式 actor registry；
13. Mac publish 后 Cloud 下一 request 读新 inode；
14. Mac 关机旧 mirror 可读；
15. damaged candidate 不替换；
16. UID 2001 不能读 clawbot mirror；
17. UID 2002 不能读 claude-code mirror。

---

# 57. Notes Local Write 验收

必须证明：

1. create 保持现有行为；
2. prepend / append / update_note_metadata 三工具在 Claude Code **真实可发现**；
3. tool schema 不含顶层 anyOf；
4. 修改工具使用 expected_version；
5. append 进入 AI Center；
6. prepend 进入既有 modify confirmation；
7. metadata update 进入既有 modify confirmation；
8. edit_note 可正常修改 canonical Markdown；
9. edit 可修改 bold/link/table 等 Markdown structure；
10. `:size[]{size="15|20"}` 合法 directive edit 后保持有效；
11. 非法 size / 不支持 dialect fail closed；
12. multiple edits atomic；
13. old_markdown 0 hit reject；
14. old_markdown >1 hit reject；
15. stale version conflict；
16. permission revoke 后 pending write 不能执行；
17. revision + updated_at 同时 bump；
18. new source_version 正确变化；
19. AI write → Milkdown load/serialize **ZERO DIFF**；
20. delete_note 不存在。

---

# 58. Notes Cloud Write 验收

必须证明：

1. Mac offline 时可 queue proposal；
2. duplicate request 不重复；
3. 每 actor >50 non-terminal 返回 queue_full；
4. actor bulk reject 可清 backlog；
5. Cloud create 不直接正式入库；
6. Mac pull 后进入 AI Center；
7. actor 不可由 payload 冒充；
8. create accept 仅生成一篇；
9. append expected_version；
10. edit expected_version；
11. redacted append ✅；
12. redacted edit ❌；
13. AI output canonicalize 后才落正式库；
14. stale conflict；
15. reject 后正文 ZERO change；
16. terminal receipt 回 Cloud；
17. get_operation_status 正常；
18. accepted 后 immediate mirror refresh；
19. 下一次 Cloud read 打开新 current.db；
20. Cloud AI 无 formal DB / outbox DB 文件权限。

---

# 59. Reminder 验收

必须证明：

1. migration counts；
2. labels/tables/items；
3. occurrences；
4. `ai_reminder_operations` 完整；
5. 多通知点；
6. 每通知点各发一次；
7. 每通知点各自去重；
8. Mac owner CRUD；
9. UI 无主线程冻结；
10. schema_version mismatch 自动 read-only；
11. Cloud update → Mac；
12. Mac update → Cloud；
13. 只有 Cloud DB 在变；
14. Mac 关机 scheduler 正常；
15. `daily_time=10:00` 按北京时间 10:00；
16. VPS 保持 UTC；
17. reboot 后 Persistent calendar timer 补跑；
18. 补跑不重复发；
19. ntfy private auth；
20. reminder client 无 ntfy.sh fallback；
21. 新 s-* topic iPhone 锁屏 PASS；
22. Cloud reboot 自动恢复；
23. Mac local helper 已 bootout；
24. plist 已从 build/package 流程删除；
25. **重新 build/reinstall App 后 helper 仍不存在**；
26. startup self-check 能发现 legacy helper；
27. offline snapshot 只读；
28. AI-invisible 对 AI not_found；
29. 猜 ID 不能绕过；
30. AI 不能改 visibility；
31. 未授权 delete/table/label 服务端拒绝；
32. backup 真能打开并恢复。

---

# 60. Stop conditions

施工中出现以下情况必须停：

1. Markdown Production schema 与当前基线明显不符；
2. canonicalizer 无法做到与 Milkdown round-trip byte-stable；
3. GreenCloud mirror 必须破坏 Gale Production 才能做；
4. Reminder UI 依赖方案未记录的本地 mutable state；
5. migration 出现真实数据损失；
6. actor 无法在 service/filesystem/API 层真正隔离；
7. 有任何正文写路径修改内容却不 bump `updated_at`；
8. Cloud writeback 必须绕过 application business layer；
9. 施工者提出“同步整个 `s_notes.db` 比较快”。😑

---

# 61. 明确不做

3.5 不做：

```text
HTML canonical Notes
HTML + Markdown dual runtime
filesystem Markdown authority
automatic body migration

whole s_notes.db sync
bidirectional SQLite sync
Syncthing DB sync
rsync DB merge
sshfs/NFS shared SQLite
WAL merge

RemoteReminderRepository
Cloud Notes authority
Cloud AI direct formal SQLite
Cloud AI direct mirror write
AI direct Reminder SQLite
AI direct outbox SQLite

Reminder per-actor ACL redesign
Cloud prepend proposal
Cloud metadata proposal
delete_note
automatic conflict merge

Redis
Postgres
RabbitMQ
Docker stack
```

也不在 3.5：

* 退休 Gale Oracle；
* 重做 Markdown retrofit；
* 重新追不可复现 120GB incident；
* 完整迁移 Clawde runtime；
* 完整迁移 headless Claude Code runtime；
* 扩 Reminder AI delete 权限；
* 把 SubsTracker 做进 S-notes。

---

# 62. 非 blocker maintenance

当前仍有少量历史尾巴。

其中 Reminder-related baseline test：

```text
必须在 3.5-0 处理
```

其余：

* Package3 candidate helper contract；
* QtWebEngine all-in-one unittest teardown SIGSEGV；
* 120GB memory runaway 不可复现 incident；

没有新证据时不阻塞 3.5。

---

# 63. 最终权限表

## Notes

| Capability         |        Mac local AI | Cloud AI 3.5 |
| ------------------ | ------------------: | -----------: |
| search             |                   ✅ |            ✅ |
| safe plain read    |                   ✅ |            ✅ |
| full Markdown read |           full-only |    full-only |
| create             |                   ✅ |   ✅ proposal |
| prepend            |                   ✅ |            ❌ |
| append             |                   ✅ |   ✅ proposal |
| update metadata    | ✅ current allowlist |            ❌ |
| edit               |                   ✅ |   ✅ proposal |
| edit redacted      |                   ❌ |            ❌ |
| delete             |                   ❌ |            ❌ |
| visibility         |                   ❌ |            ❌ |
| targets            |                   ❌ |            ❌ |

Cloud：

```text
create / append / edit
全部需要 S accept
```

## Reminder

owner：

```text
full human management
```

AI：

```text
current allowlist only
AI-visible only
migration ≠ permission expansion
```

---

# 64. 3.5 完成后的体验

S 在 Mac：

```text
Notes
→ 继续 Milkdown WYSIWYG
→ Markdown 真身
→ 本地 AI 能安全读 canonical Markdown
→ append/edit 走 AI Center

Reminder
→ 仍在 S-notes UI
→ 真数据来自 GreenCloud
→ 断网只能看兼容 snapshot
```

S 不在 Mac：

```text
Mac 可以关机
```

GreenCloud：

```text
Reminder scheduler 活着
ntfy 活着
Cloud AI 可以使用同一份 Reminder
Cloud AI 可以读取最后成功 Notes mirror
Cloud AI 可以 queue create/append/edit proposal
```

正式 Notes 不会被云端偷偷覆盖。

Mac 再上线：

```text
existing pull helper
→ AI Center
→ S accept/reject
→ canonicalize
→ revision + updated_at
→ formal Markdown
→ receipt
→ immediate mirror publish
```

---

# 65. 最终施工顺序

```text
3.5-0
Local Finalization
│
├─ Reminder multiple notification points
├─ Reminder baseline tests clean
├─ MCP anyOf fix
├─ prepend/append/metadata discovery
├─ expected_version contract
├─ dialect-aware get_note_markdown
├─ canonicalizer
├─ edit_note
└─ local round-trip gate

↓

3.5-A
Notes Cloud Read

↓

3.5-B
Reminder Cloud Authority

↓

3.5-C
AI Access Boundary Ready

↓

3.5-D
Existing R1B Outbox → Notes Cloud Writeback
```

一段通过，再进入下一段。

---

# 66. 最终结论

S-notes 3.5 不把整个 S-notes 搬去 VPS。

它建立的是两条不同、但边界清晰的线：

```text
Notes
Mac = 真本
Cloud = 安全镜像 + proposal

Reminder
Cloud = 真本 + executor
Mac = owner client
```

Markdown Production 已经解决正文真身问题。

v2.1 再把：

```text
canonical Markdown
private dialect
真实 version token
existing outbox
OS identity
ntfy fail-closed
scheduler permanent retirement
offline queue
atomic mirror reopen
```

这些施工时必撞的边界提前钉死。

3.5 的剩余工作因此不再是“重新设计 S-notes”。

而是：

> **在不制造第二事实源、不扩大既有 AI 权限、不破坏已经封口的 Production 能力的前提下，把 Notes 安全送给云端 AI，把 Reminder 的正式执行权交给 GreenCloud。**
