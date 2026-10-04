# S-notes 最终进度书｜GreenCloud 3.5 COMPLETE｜Post-Close Cleanup CLOSED｜Cloud Prepend + Metadata CLOSED

**日期：2026-10-04**  
**状态：GreenCloud 3.5-A / 3.5-B / 3.5-C / 3.5-D 与 11 步施工均已 COMPLETE；四本地 Agent MCP 接线、GreenCloud 3.5 post-close cleanup / maintenance ledger、Cloud `prepend_note` + `update_note_metadata` 后续功能票均已正式 CLOSED。Cloud metadata 继续只允许 `title / directory_id / add_tags / remove_tags`，AI visibility / actor targets / delete 仍不开放。真人 Gate 中发现并修复 AI Center 关闭时因同步线程仍在运行导致 S-notes 整体退出的问题，修复已进入最终正式版本。当前 `main == origin/main == HEAD == 172a46125015dafa672be39357cffe94276dd177`，worktree clean，`PUSH=PASS`。GreenCloud 3.5 当前无新的强制主线施工；其余 maintenance 仅在真实触发时处理。**  
**正式仓库：** `/Users/s/Documents/项目/S-notes`  
**正式 App：** `/Users/s/Documents/项目/S-notes/dist/S-notes.app`

---

# 1. 本进度书覆盖什么

本节覆盖此前进度书 §45.17–45.19 中：

```text
NEXT_1=LOCAL_MCP_PRODUCTION_SOURCE_ISOLATION
NEXT_2=NOTE_BODY_TAGS_SINGLE_TRANSACTION
NEXT_3=REMINDER_COMPLETED_AI_RESUME_RULE
NEXT_4=AI_ACCEPT_EDITOR_RELOAD_UX
MCP_HEADLESS_DOCK_BUG=SEPARATE_QUEUED_TICKET
GREENCloud_3_5_A=PAUSED
```

上述 4 张 post-acceptance 小票与 §44 Dock bug 现已全部施工、验收、合并并部署。

# 2. 当前正式 Git / App 基线

```text
MAIN=46a02082f698f1989defa3f80530edf03120a6d1
ORIGIN_MAIN=46a02082f698f1989defa3f80530edf03120a6d1
HEAD=46a02082f698f1989defa3f80530edf03120a6d1
MERGE_MODE=FAST_FORWARD_ONLY
PUSH=PASS

FORMAL_APP_SOURCE=46a02082f698f1989defa3f80530edf03120a6d1
FORMAL_APP_TRACKED_SOURCE_DIRTY=NO
FORMAL_APP_BUILD=PASS
FORMAL_APP_CODESIGN=PASS
```

本轮所有正式代码改动均已进入 `main` / `origin/main`。

原有 5 份 S 的 untracked user docs 在整个施工过程中均未被 stage、修改或删除。

本进度书是本轮封口后新补的第 6 份本地文档；为了不让“补文档”反过来改变 production runtime source，本文件当前保持 untracked，不触发额外 App rebuild。

# 3. Post-Acceptance Cleanup｜4 张票全部 CLOSED

## Ticket 1｜Local MCP production source isolation

最终 commit：

```text
c0e228f54607ce0f39333302e712545b329b8557
```

修复后正式 MCP 不再直接运行 current Git checkout。

当前 production source 固定于：

```text
/Users/s/Documents/项目/S-notes/dist/S-notes.app
```

正式 MCP launcher 位于 App 包内 `Contents/Resources/bin/`。

Codex、Claude Code、Antigravity、Clawde/OpenClaw 四类正式客户端均已切换到 packaged launcher；feature branch 切换不再改变 production MCP runtime。

## Ticket 2｜Note body + tags 单事务

最终 commit：

```text
89de945db34b0edbe2e405ca9b6d2dfd632aeb49
```

旧问题：

```text
正文先提交
→ revision 前进
→ tags 后提交失败
→ 留下半保存状态
→ 下一次保存可能出现假 conflict
```

现在由 `NoteService.save_note` owner 一次完整保存动作；正文、tags、revision / updated_at 共用原子事务。tags 失败时整体回滚，真实 stale revision conflict 保护保持不变。

针对性测试覆盖正常保存、失败回滚、失败后重试和真实 stale conflict，均 PASS。

## Ticket 3｜completed Reminder 禁止被 AI resume 复活

最终 commit：

```text
0136a411111c8bdc7bdfbeb0f9ec9e8a8b891265
```

旧问题：

```text
completed item
→ AI resume
→ active
→ completed_at 被清空
```

规则现收口于共用的 `ReminderService.resume_reminder()`。

completed item 收到 AI resume 时返回明确 rejected 业务结果；item lifecycle、`completed_at` 与 occurrence 保持不变。正常 paused → resume、pause、complete 邻近行为保持原语义。

Reminder + MCP 针对性回归均 PASS。

## Ticket 4｜AI Accept 后 editor 安全自动刷新 UX

最终 commit：

```text
776645e46183cb9fad2c2339e11fad85a00c4236
```

最终行为：

```text
Accept 修改非当前 Note
→ 列表刷新
→ 当前 editor 不受影响

Accept 修改当前 Note + editor clean
→ 自动 reload 最新正式内容 / revision

Accept 修改当前 Note + editor dirty
→ 不覆盖人工未保存内容
→ 停 autosave / 进入明确冲突提示
→ 显示“重新加载”路径
```

S 真人 Gate：CLEAN PASS；DIRTY PASS；未保存测试文字 `123` 在 AI Accept 成功后仍保留。另一次 1 秒 autosave 抢跑现场顺手再次确认真实 stale revision conflict 会阻止 AI 覆盖，PASS。

# 4. Antigravity 本地 MCP Dock 图标 bug｜CLOSED

最终 commit：

```text
46a02082f698f1989defa3f80530edf03120a6d1
```

旧 §44 的真人因果链仍成立，但 Ticket 1 后 runtime 形态已经变化：

```text
Antigravity
→ dist/S-notes.app 包内 run_snotes_mcp_stdio.sh
→ S-notes --mcp-stdio 长驻进程
→ 首次正文写入进入 Milkdown canonicalizer
→ canonicalizer 按需创建 QApplication / QtWebEngine
→ macOS 将本应 headless 的 MCP 进程注册为前台 App
→ Dock 出现图标
```

当前正式 Antigravity 配置已确认使用 packaged production launcher，不再是旧 checkout Python launcher。

## 4.1 旧候选修法已被实测淘汰

§44 当时记录过 launcher 环境变量候选方向。

本轮重新按当前 packaged runtime 实测：

```text
QT_QPA_PLATFORM=offscreen
+ QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu
→ FAIL，写入后仍 foreground / visible

QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM=1
→ FAIL，写入后仍 foreground / visible
```

因此旧进度书里的：

```text
PROPOSED_MIN_FIX=QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM=1_IN_MCP_LAUNCHER
```

只保留为历史候选，不再是当前方案。

真正根因不是 stdio 协议，也不是 Antigravity reconnect，而是 canonicalizer-owned `QApplication` 创建时的 macOS application activation policy。

## 4.2 最终最小修复

修复只发生在：

```text
src/snotes/ui/markdown_canonicalizer.py
```

规则：

```text
如果进程本来已经有 QApplication
→ 说明是正常 GUI App
→ 不改变 activation policy

如果 MarkdownCanonicalizer 自己创建 QApplication
→ 说明当前是 headless / non-GUI owner
→ macOS 下将 NSApplication activation policy 设为 prohibited
```

实现通过系统 `libobjc.A.dylib` 调用 `NSApplication.setActivationPolicy:`，仅影响 canonicalizer-owned Qt App。

没有给整个 S-notes App 设置 `LSUIElement` / `LSBackgroundOnly`，也没有改 Antigravity 本体、MCP protocol、数据库或权限模型。

## 4.3 验证证据

修复前，在当前正式 packaged MCP 上完成正文 append 后，macOS System Events 返回：

```text
S-notes, background only=false, visible=true
```

修复 candidate：

```text
FIRST_APPEND=pending_confirmation
SECOND_APPEND=pending_confirmation
MCP_SESSION_ALIVE=YES
MACOS_BACKGROUND_ONLY=true
MACOS_VISIBLE=false
```

同一 candidate 的正常 GUI 反向检查：

```text
S-notes GUI
background only=false
visible=true
```

因此 headless MCP 被隐藏，而正式 GUI 本体没有被一起“隐身”。

最终正式 App 部署后再次通过 packaged production launcher 做正文 append：

```text
FORMAL_APPEND=pending_confirmation
FORMAL_MCP_BACKGROUND_ONLY=true
FORMAL_MCP_VISIBLE=false
```

说明修复已真正进入 Antigravity 下一次启动会使用的 production runtime。

自动化：

```text
CANONICALIZER_BODY_WRITE_EDIT_NOTE_TESTS=37_PASS
CANONICALIZER_FINAL_RERUN=8_PASS
ARCHITECTURE_GATE=8_8_PASS
ARCHITECTURE_CHECK=PASS
ARCHITECTURE_WARNINGS=20_EXISTING
GIT_DIFF_CHECK=PASS
```

没有为这张小工单重跑无关全量工程。

---

# 5. Construction cleanup

本轮遵守 §39：

```text
ANTIGRAVITY_DOCK_CANDIDATE_CLEANUP=PASS
DOCK_TEST_FIXTURE_CLEANUP=PASS
TEMP_PID_FILES_CLEANUP=PASS
TEST_MCP_PROCESSES_TERMINATED=YES
FORMAL_DB_TOUCHED=NO
```

# 6. 当前正式状态

```text
AI_ACCEPTANCE_REPAIR=A_TO_I_CLOSED
POST_ACCEPTANCE_CLEANUP_TICKET_1=CLOSED
POST_ACCEPTANCE_CLEANUP_TICKET_2=CLOSED
POST_ACCEPTANCE_CLEANUP_TICKET_3=CLOSED
POST_ACCEPTANCE_CLEANUP_TICKET_4=CLOSED
POST_ACCEPTANCE_CLEANUP=FULL_CLOSED

MCP_HEADLESS_DOCK_BUG=CLOSED
ANTIGRAVITY_MCP_DOCK_ICON=NO
NORMAL_SNOTES_GUI_VISIBLE=YES

MAIN=ORIGIN_MAIN=HEAD=46a02082f698f1989defa3f80530edf03120a6d1
FORMAL_APP_SOURCE=46a02082f698f1989defa3f80530edf03120a6d1
FORMAL_APP_TRACKED_SOURCE_DIRTY=NO
CONSTRUCTION_DATA_CLEANUP=PASS

GREENCloud_3_5_A=NEXT
```

旧进度书中任何“4 张小票 NEXT / Dock bug QUEUED”的描述从现在起都只视为历史状态，不再重新施工。

# 7. 下一步｜回 GreenCloud 3.5-A

当前恢复此前暂停的主线：

```text
3.5-A Notes Cloud Read
→ 3.5-B Reminder Cloud Authority
→ 3.5-C AI Access Boundary Ready
→ 3.5-D Existing R1B Outbox → Notes Cloud Writeback
```

但旧进度书已经拍板的边界继续有效：

> **任何 redacted `safe_text` 真正上 GreenCloud 之前，先复查现有 redaction coverage 与产品承诺。**

Mac 继续是 Notes 唯一正式 authority；不要因为本轮 cleanup CLOSED 改写 3.5-A 的既有架构。

---

# 8. 新窗口接班短句

> G，继续 S-notes GreenCloud 3.5-A。正式 repo `/Users/s/Documents/项目/S-notes`，当前 `main == origin/main == HEAD == 46a02082f698f1989defa3f80530edf03120a6d1`，正式 `dist/S-notes.app` 也来自同一 commit、dirty=NO。AI 接受链修复 A–I、Post-Acceptance Cleanup Ticket 1–4、Antigravity MCP Dock 图标 bug 均已 CLOSED，不准再从旧进度书把它们捞回待办。Ticket 1 已把正式 MCP 固定到 packaged App runtime；Ticket 2 已让 Note body+tags 单事务；Ticket 3 已禁止 completed Reminder 被 AI resume 复活；Ticket 4 已完成 AI Accept 后 clean 自动 reload / dirty 不覆盖的安全 UX；Antigravity MCP 首次正文写入创建 canonicalizer-owned QApplication 时现会在 macOS 设置 prohibited activation policy，正式 MCP 已验证 background only=true / visible=false，正常 S-notes GUI 仍 visible=true。construction leftovers 已清理。现在回 3.5-A Notes Cloud Read；在任何 redacted safe_text 真正上 GreenCloud 前，先复查 redaction coverage 与产品承诺。

---

# 9. GreenCloud 3.5-A｜Notes Cloud Read CLOSED

**本节补记日期：2026-09-29**  
**状态：Step 4 + Step 5 + 真人 Gate + Post-Vibe P1 cleanup 全部完成；3.5-A 正式 CLOSED。下一步进入 3.5-B / Step 6。**

上一节记录的：

```text
GREENCloud_3_5_A=NEXT
```

现更新为：

```text
STEP04=CLOSED
STEP05=CLOSED
STEP05_HUMAN_GATE=PASS
GREENCloud_3_5_A=CLOSED
READY_FOR_STEP06=YES
```

Mac 仍然是 Notes 唯一正式 authority；GreenCloud 只保存 actor-specific 的派生只读镜像，不改变既有 3.5 架构边界。

# 10. Step 4｜Mac actor-specific Notes mirror generation｜CLOSED

Step 4 完成本地 actor-specific Notes mirror 生成链，并在 Astra 独立复核后封口。

最终修复 commit：

```text
4d18fdcd5287cbee147305f6bd7d0d31fc1c67e2
```

Step 4 最终关键边界：

- `common` Note 进入两个 actor mirror；
- actor-only Note 只进入对应 actor mirror；
- hidden / unauthorized Note 不进入 mirror；
- `full`：`safe_text` 为 Markdown → plain，`safe_markdown` 保留 canonical raw Markdown，且 full Markdown 不做 redaction；
- `redacted`：单次 Markdown parse 后走现有 redaction，`safe_markdown=NULL`；
- `source_version` 保留 Note version token；
- source body / visibility / grants / updated_at 在同一 SQLite read transaction 内读取，避免权限快照与正文不一致；
- actor mirror schema / indexes / metadata / FTS 做完整 validator；
- 防止 output path 覆盖 source DB；
- Gale production contract 不因 Step 4 改动。

Astra 第二轮独立复核结果：

```text
ASTRA_STEP04_REREVIEW=PASS
AUTH_SNAPSHOT_FIX=PASS
REDACTED_SINGLE_PARSE=PASS
MIRROR_VALIDATOR=PASS
SOURCE_DB_OVERWRITE_GUARD=PASS
GALE_PRODUCTION_REGRESSION=PASS
ARCHITECTURE_CHECK=PASS
TARGETED_TESTS=51_PASS
STEP04_SAFE_TO_CLOSE=YES
STEP05_SAFE_TO_PROCEED=YES
```

Step 4 随后 fast-forward 到本地 main；当时未 push。

# 11. Redaction Cloud Gate｜PASS

在任何真实 redacted `safe_text` 上传 GreenCloud 前，按旧架构约定完成现有 redaction coverage / 产品承诺复核。

结果：

```text
REDACTION_CLOUD_GATE=PASS
CURRENT_REDACTION_IS_RULE_DRIVEN=YES
AUTOMATIC_GENERAL_PII_DETECTOR=NO
REDACTION_FAIL_CLOSED=YES
PRODUCT_PROMISE_MATCHES_IMPLEMENTATION=YES
STEP05_REDACTED_UPLOAD_ALLOWED=YES
```

结论：

- 当前 redaction 是本地配置驱动的 literal replacement，不是通用 PII detector；
- missing / bad / empty rules fail closed；
- 产品承诺是“AI 脱敏后可见”，架构承诺也是“Markdown → plain → 现有 Mac redaction”，并未承诺通用自动 PII 识别；
- 因此 Step 5 无需先扩展成通用 PII 系统。

# 12. Step 5｜GreenCloud Notes read｜自动施工 CLOSED

施工 branch：

```text
codex/gc35-step05-cloud-read
```

施工时使用 isolated worktree：

```text
/Users/s/.codex/worktrees/gc35-step05-cloud-read/S-notes
```

该 worktree 后续已从 production runtime 脱钩并以 Git-native 方式移除，详见 §15。

Step 5 自动施工完成后的关键结果：

```text
STEP05_CODE=PASS
STEP05_DEPLOY=PASS
MIRROR_PUBLISH=PASS
CLOUD_READ_MCP=PASS
ACTOR_FILESYSTEM_ISOLATION=PASS
FORCED_COMMAND_TRANSPORT=PASS
ATOMIC_REPLACE=PASS
OLD_MIRROR_OFFLINE_READ=PASS
NEXT_REQUEST_NEW_INODE=PASS
VALIDATION_17=17/17_PASS
ARCHITECTURE_CHECK=PASS
TARGETED_TESTS=48_PASS
```

GreenCloud 正式目标：

```text
SSH_ALIAS=optimal
HOSTNAME=optimal-living
OS=Debian 13.7
PYTHON=3.13.5
claude UID=2001
openclaw UID=2002
snotes UID=2006
snotes-publish UID=987
```

没有新增公网监听；没有修改 Caddy、ntfy、de-node、egress、Oracle/Gale 或 Reminder。

正式部署结构：

- `/srv/snotes`：Step 5 runtime / mirror / deploy 根目录；
- `mirror/claude-code`：仅 `claude` actor group 可读；
- `mirror/clawbot`：仅 `openclaw` actor group 可读；
- `current.db`：`snotes:actor-group 0640`；
- candidate / publish lock：0600；
- dedicated SSH key → `restrict` + fixed receiver command；
- receiver 先校验 transport / checksum / actor mirror contract，再同目录 `os.replace` 原子替换；
- Cloud launcher：`/usr/local/bin/snotes-cloud-read`；
- receiver wrappers：`/usr/local/sbin/snotes-mirror-ssh-entry`、`snotes-mirror-receiver`；
- Cloud MCP 每次 request 都重新只读打开固定 actor mirror，不缓存跨 inode 连接。

Cloud MCP 对外只暴露：

```text
search_notes
get_note
get_note_markdown
```

真实 Claude Code / OpenClaw client discovery 仍不属于 Step 5 blocker。

# 13. Step 5｜17/17 自动验收

真实虚构 fixture 通过正式 `ActorMirrorService` 生成，并走真实 Mac → SSH → GreenCloud receiver → actor mirror → Cloud MCP 链路。

17 项全部 PASS：

```text
01 common 两 actor 可读 = PASS
02 claude-code-only 对 openclaw 不可见 = PASS
03 clawbot-only 对 claude 不可见 = PASS
04 hidden / unauthorized absent = PASS
05 redacted safe_text only + safe_markdown NULL = PASS
06 full safe_markdown present = PASS
07 full Markdown zero redaction = PASS
08 get_note returns safe_text = PASS
09 get_note_markdown full-only / redacted not_found = PASS
10 dialect=snotes-markdown-v1 = PASS
11 source_version preserved = PASS
12 actor contract / registry = PASS
13 next request sees new inode/content = PASS
14 Mac/transport off → old mirror still readable = PASS
15 damaged candidate rejected / current unchanged = PASS
16 UID 2001 cannot read clawbot mirror = PASS
17 UID 2002 cannot read claude-code mirror = PASS
```

forced-command negative checks也通过：空 shell session 被拒绝，任意 command 被拒绝，unsupported actor 被拒绝且不改变现有 mirror。

# 14. Step 5 真人 Gate｜PASS

最初 human check 要求 S 手工提供内部 `NOTE_ID`，不符合正式 App 的用户视角，因此补了标题只读精确查找：

```text
--title "笔记标题"
```

0 条命中返回 `note_not_found`；重名列候选并停止，不猜选；原 note_id 模式继续保留。

S 使用正式 App 修改真实 common/full Note：

```text
NOTE_ID=312
TITLE=可选六位数域名.xyz(用作域名邮箱）
HUMAN_MARKER=GC35 HUMAN GATE PASS
```

真人执行 manual publish + 两个 Cloud actor MCP read 后：

```text
CLAUDE_CODE_STATUS=PUBLISHED
CLAWBOT_STATUS=PUBLISHED
HUMAN_UPDATED_CONTENT_VISIBLE_TO_CLAUDE_CODE=YES
HUMAN_UPDATED_CONTENT_VISIBLE_TO_CLAWBOT=YES
STEP05_HUMAN_GATE=PASS
STEP05_CLOSED=YES
3_5_A=CLOSED=YES
```

两端都读取到同一 Note 312 的最新 `version / updated_at / visibility=full` 与刚修改后的正文。

Step 5 closeout commit：

```text
1b32415
```

# 15. Post-Vibe 独立审计 + 唯一 P1 cleanup｜CLOSED

3.5-A 真人 CLOSED 后，由 Claude Code 做独立只读 Post-Vibe / 防屎山审计。

审计结论：

```text
AUDIT_STATUS=NEEDS_SMALL_CLEANUP
P0_COUNT=0
P1_COUNT=1
P2_COUNT=4
NO_HIDDEN_PROTOCOL=YES
SINGLE_SOURCE_OF_TRUTH=PASS
REPO_EXPLAINS_LIVE_DEPLOYMENT=YES
READY_FOR_STEP06=NO
```

唯一 P1：正式五分钟 publisher 当时仍以 isolated Codex worktree 为 `WorkingDirectory`，因此施工路径实际成了 production runtime dependency。

风险不是数据泄漏，而是：

```text
Codex worktree prune / 删除 / 切换
→ LaunchAgent 无法 chdir 或运行到漂移代码
→ 五分钟镜像更新静默停止
→ GreenCloud 永远停留在最后一次成功镜像
```

这是典型“临时施工路径偷渡进生产契约”。

P1 最小 cleanup 完成后：

```text
GC35_A_P1_CLEANUP=PASS
MERGE_MODE=FAST_FORWARD_ONLY
PRODUCTION_REPO=/Users/s/Documents/项目/S-notes
PRODUCTION_DEPENDS_ON_CODEX_WORKTREE=NO
LAUNCHAGENT_WORKING_DIRECTORY=/Users/s/Documents/项目/S-notes
PUBLISHER_SMOKE=PASS
P0_OPEN=0
P1_OPEN=0
READY_FOR_STEP06=YES
PUSH=NO
```

具体动作：

- main ff-only 合入 Step 5 closeout；
- 旧 LaunchAgent bootout；
- 旧 plist 放入私有 recovery 目录；
- 使用现有 `greencloud_publish_schedule.py --repo /Users/s/Documents/项目/S-notes` 重新生成正式 plist；
- 新 LaunchAgent 从正式 repo 运行；
- publisher smoke：两个 actor 均 `UNCHANGED`、exit 0；
- isolated worktree 确认完全 merged / tracked clean 后，用 `git worktree remove` 正常移除，无 `--force`；
- construction branch 保留；
- GreenCloud 本轮 cleanup 无改动。

P1 cleanup 后 report-only commit：

```text
e26de91
```

# 16. Post-Vibe P2-3｜human check 输出整篇正文｜CLOSED

Claude 审计的 4 个 P2 中，S 刚刚亲自遇到 P2-3：human check 默认把完整 `safe_text` 打到 terminal，用户体验差且没必要。

因此单独做了一张极小票，只修改验收工具输出，不碰 production runtime。

最终行为：

```text
GC35_P2_3_HUMAN_CHECK_OUTPUT=PASS
FULL_SAFE_TEXT_DEFAULT_OUTPUT=NO
SHORT_PREVIEW=YES
NOTE_ID_MODE=PASS
TITLE_MODE=PASS
TARGETED_TESTS=3
P2_3=CLOSED
```

现在对 claude / openclaw 只输出摘要：

```text
actor
status
note_id
title
version
updated_at
visibility
safe_text_length
preview
```

`preview` 最多 100 字符，换行压成一行；超长加 `…`；空正文正常为空；not_found / failed 只输出必要状态与 reason。

原 manual publish、Cloud MCP read、note_id 与 `--title` 两种入口均保持不变。

本小票最终 commit：

```text
f8bd7df
```

# 17. 剩余 Post-Vibe P2｜3 项，全部 NON-BLOCKING

当前剩余：

```text
P2_REMAINING=3_NONBLOCKING
```

仅作为 maintenance notes 保留，不阻塞 Step 6，也不在 3.5-A 封口后继续手痒施工。

## P2-1 actor mapping duplication

actor 名单 / UID / path 映射在少数 Python / shell owner 中有重复，但当前每层职责不同，漂移会 fail closed，OS group permission 是最终权限边界。

未来若相关 owner 再改，可顺手加一致性测试；当前不为了“去重复”引入新的全局配置抽象。

## P2-2 deployment record small drift

线上 source manifest / rollback pre-closeout / INSTALL 的施工记录层存在小偏差，但正式 runtime、wrapper、INSTALL、权限状态都能由 repo 解释，不是 production snowflake。

未来真正需要升级 Cloud runtime 时再补升级流程；当前不提前建设。

## P2-4 Cloud read 不重复复核 target_actor

receiver 激活前已经校验 `target_actor`，OS 文件权限再做 actor 隔离；Cloud reader 当前按固定 actor path 只读。

额外 open-time `target_actor` check 属于 defense-in-depth，不是现有真实缺口；当前不叠重复 guard。

# 18. 3.5-A 最终 Git / Runtime 状态

当前正式本地 main：

```text
MAIN=f8bd7df
ORIGIN_MAIN=46a02082f698f1989defa3f80530edf03120a6d1
PUSH=NO
```

说明：

- 本轮 3.5-A / Step 4 / Step 5 / Post-Vibe cleanup 均已进入本地 main；
- 尚未 push origin；
- 仍遵守用户批准后再 push；
- 原正式 repo 中 S 的 6 份 untracked 用户文档未被修改、移动、stage 或删除；
- tracked worktree clean；
- production five-minute publisher 已运行于正式 repo；
- Codex isolated worktree 已不再是 runtime dependency，并已 Git-native remove；
- construction branch `codex/gc35-step05-cloud-read` 保留。

当前正式生产状态：

```text
STEP04=CLOSED
REDACTION_CLOUD_GATE=PASS
STEP05_CODE=PASS
STEP05_DEPLOY=PASS
VALIDATION_17=17/17_PASS
STEP05_HUMAN_GATE=PASS
STEP05_CLOSED=YES
3_5_A=CLOSED=YES
POST_VIBE_P0_OPEN=0
POST_VIBE_P1_OPEN=0
POST_VIBE_P2_OPEN=3_NONBLOCKING
PRODUCTION_DEPENDS_ON_CODEX_WORKTREE=NO
LAUNCHAGENT_WORKING_DIRECTORY=/Users/s/Documents/项目/S-notes
PUBLISHER_SMOKE=PASS
READY_FOR_STEP06=YES
PUSH=NO
```

# 19. 下一步｜GreenCloud 3.5-B / Step 6

现在主线更新为：

```text
3.5-A Notes Cloud Read = CLOSED
3.5-B Reminder Cloud Authority = NEXT
3.5-C AI Access Boundary = LATER
3.5-D Existing R1B Outbox → Notes Cloud Writeback = LATER
```

进入 Step 6 时继续沿用 3.5 v2.2 / 11-step FINAL 的既有边界，不重新设计 3.5-A，也不把已封口的 Step 4/5 / Post-Vibe P1 捞回待办。

特别注意：

- Mac Notes authority 继续不变；
- Reminder authority 才会在 3.5-B 按既定方案迁到 GreenCloud；
- 当前 3 个 P2 不阻塞 Step 6；
- Step 6 若需要更新 Cloud runtime，再按实际需求补最小升级路径，不提前大改 deployment framework。

# 20. 新窗口接班短句

> G，继续 S-notes GreenCloud 3.5-B / Step 6。3.5-A Notes Cloud Read 已正式 CLOSED：Step 4 actor-specific mirror、redaction Cloud Gate、Step 5 GreenCloud receiver / actor filesystem isolation / read-only MCP / 5-minute publisher、17/17 自动验收和 S 真人 Gate 全部 PASS。Post-Vibe 独立审计 P0=0，唯一 P1“正式 publisher 依赖 Codex isolated worktree”已清掉；production LaunchAgent 现在从 `/Users/s/Documents/项目/S-notes` 正式 main 运行，publisher smoke PASS，isolated worktree 已 Git-native remove。P2-3 human check 默认喷整篇正文已单独修掉；仍有 3 个 P2 maintenance notes，但全部 NON-BLOCKING，不要在进 Step 6 前继续手痒。当前本地 `main=f8bd7df`，`origin/main=46a02082f698f1989defa3f80530edf03120a6d1`，`PUSH=NO`；6 份 untracked 用户文档继续不碰。下一步只进入 3.5-B Reminder Cloud Authority，不重做 3.5-A。

---

# 21. GreenCloud 3.5-B / Step 6｜Cloud Reminder staging 即时 Gate PASS

**补记日期：2026-09-29**  
**状态：Step 6 implementation + localhost/cloud gate + HTTPS immediate gate 已 PASS；staging 正持续运行；24h observation 尚未完成，因此 Step 6 还不能正式 CLOSED。**

上一节记录的：

```text
3.5-B Reminder Cloud Authority = NEXT
```

现更新为：

```text
STEP06_IMPLEMENTATION=READY
STEP06_LOCAL_AND_CLOUD_GATE=PASS
STEP06_IMMEDIATE_GATE=PASS
STEP06_24H_OBSERVATION=PENDING
STEP06_CLOSED=NO
STEP07_ALLOWED=NO
```

本步严格停留在 **Cloud Reminder staging**：没有迁正式 Reminder 数据，没有切换 authority，没有修改或退役 Mac `com.snotes.reminder-helper`，没有进入 Step 7，也没有安装 Claude Code / OpenClaw runtime。

## 21.1 当前 Git / branch 状态

Step 6 施工 branch：

```text
codex/gc35-step06-cloud-reminder-staging
```

当前 Step 6 最新验收记录 commit：

```text
de820e1
```

本轮明确保持：

```text
PUSH=NO
MERGE_MAIN=NO
ENTER_STEP07=NO
```

Step 6 开工基线仍为本地：

```text
main=f8bd7df
```

现有 6 份 S 的 untracked user docs 继续保持原样，未修改、未 stage、未 commit。

## 21.2 Step 6 staging 实际落地

GreenCloud：

```text
HOST=optimal / 109.94.170.215
REMINDER_SOURCE=/srv/snotes/reminder-runtime
REMINDER_DB=/srv/snotes/data/reminder.db
API_LISTEN=127.0.0.1:8766
PUBLIC_HOST=https://reminder.ke-den.de
STAGING_TOPIC=s-step06
CLOUD_BACKUP=/srv/snotes/backups/reminder/reminder-latest.db
```

沿用既有 `snotes` service identity 与 `/srv/snotes/runtime/.venv/bin/python`，没有另造一套 runtime。

staging DB：

- owner 为 `snotes`；
- mode 0600；
- WAL；
- busy timeout 5000；
- schema version 4，未做 schema migration。

systemd 已安装并运行：

- Reminder API service；
- 每 5 分钟 Persistent scheduler；
- 每日 03:15 UTC Persistent backup timer。

ntfy：

- 固定使用既有 `https://ntfy.ke-den.de`；
- 新建独立 staging 身份 `snotes-reminder-staging`；
- 仅允许写 `s-step06`；
- 没有修改原 ntfy server config；
- credential 未进入代码、Git 或报告正文。

Caddy：

- 新增独立 `reminder.ke-den.de` site；
- 原 `ntfy.ke-den.de` block 未改；
- API 仍只监听 localhost，不因公网 HTTPS 暴露 Python service 本体。

## 21.3 D1 DNS｜完成

D1 最终使用：

```text
A reminder.ke-den.de -> 109.94.170.215
DNS only
```

该记录已通过 Cloudflare MCP 添加。

DNS 生效后，Caddy 从此前 NXDOMAIN retry 自动恢复，并于 2026-09-29 09:03:03 UTC 获取正式 Let's Encrypt certificate；本次 HTTPS Gate 不需要再次编辑或 reload Caddy。

## 21.4 D3 off-VPS backup｜staging 验证完成

Cloud 最新备份：

```text
/srv/snotes/backups/reminder/reminder-latest.db
```

滚动保留：

```text
14 copies
```

Mac off-VPS staging copy：

```text
/Users/s/Library/Application Support/S-notes/reminder-cloud-backups/reminder-staging-20260929T085804089039Z.db
```

已验证：

```text
SQLITE_OPEN=PASS
PRAGMA_INTEGRITY_CHECK=ok
SOURCE_COUNTS_MATCH_BACKUP=YES
MAC_COUNTS_MATCH_CLOUD=YES
```

只读 SSH backup entry 同时验证任意文件读取被拒绝；`cat /etc/passwd` 不可借该入口执行。

本步只验证手动拉取 staging backup；没有提前实现 Step 7 的周期自动拉取客户端。

## 21.5 自动 / 现场验收结果

本地针对性测试：

```text
71 PASS
```

credential-mode runtime fix 后补跑：

```text
9 PASS
LIVE_SYSTEMD_STARTUP=PASS
```

GreenCloud localhost live acceptance：

```text
72/72 PASS
```

覆盖：

- owner CRUD / snapshot；
- 三个 actor 的可见写入与 idempotency；
- hidden / guessed ID → not_found；
- actor delete 拒绝；
- actor visibility change 拒绝；
- body 自报 actor 无效；
- 无 credential / 错 credential 拒绝；
- Shanghai 10:00 在 UTC host 上语义正确；
- multi-notify node 独立 dedupe；
- missing ntfy credential fail closed；
- 无 public ntfy fallback；
- `claude` / `openclaw` 无法直接读取 staging DB；
- backup forced-command 边界有效。

Persistent catch-up 已通过仅停启本步 timer + backdate stamp 模拟：补跑成功，0 次重复发送；未为了测试重启整台 VPS。

## 21.6 HTTPS immediate Gate｜PASS

公网 HTTPS 验收：

```text
TLS_CERTIFICATE=PASS
HOSTNAME_VERIFY=PASS
CADDY_VALIDATE=PASS
HTTPS_ACCEPTANCE=72/72_PASS
NO_CREDENTIAL=401
WRONG_CREDENTIAL=403
API_BIND=127.0.0.1:8766_ONLY
```

HTTP 正确 308 跳转到同 host/path HTTPS。

验收 harness 通过公网 443 / Caddy 完成与 localhost 同级的 72 项正负例；没有使用 insecure TLS option。

原 ntfy / Caddy 配置保持原边界，没有为了 HTTPS Gate 修改既有站点。

## 21.7 本轮唯一 runtime 现场修正

首次 API startup 时发现 systemd managed credential 为：

```text
/run/credentials/<unit>
root:root
0440 file under 0550 managed directory
```

原 credential mode 只接受普通 0400/0600 文件，因此启动被拒绝。

已做窄修复：只兼容 systemd root-owned managed credential directory / file；普通 credential 仍维持原 0400/0600 规则，不放宽一般 secrets 权限。

修复后 API 于：

```text
2026-09-29 08:56:56 UTC
```

成功启动；即时 Gate 检查时无后续异常 restart。

## 21.8 回滚

Step 6 recovery material：

```text
/root/greencloud-backups/step06-20260929T083108Z
```

回滚入口：

```bash
ssh optimal /srv/snotes/rollback-step06.sh
```

该 rollback 只退休 Step 6 新增 staging runtime / service 边界，不删除 Reminder data/backups，也不触碰此前 Notes Cloud Read、ntfy 原站点或其他无关配置。

## 21.9 当前唯一未完成 Gate｜24h observation

Step 6 FINAL 要求 staging 连续运行至少 24h 后再正式 CLOSED。

以 API 成功启动时间计算，最早检查时间：

```text
2026-09-30 08:56:56 UTC
```

24h Gate 只需核对：

1. scheduler / timer 按期执行；
2. dedupe marks / journal 无重复发送；
3. API / scheduler / backup services 自成功启动后无异常错误；
4. 次日 fixture notification 正常；
5. daily backup 实际生成且 sqlite integrity / counts 正常。

不要在 24h Gate 前：

- push；
- merge main；
- 切正式 Reminder authority；
- 退役 Mac helper；
- 进入 Step 7。

当前正式结论：

```text
STEP06_IMPLEMENTATION=READY
STEP06_LOCAL_AND_CLOUD_GATE=PASS
STEP06_IMMEDIATE_GATE=PASS
STEP06_24H_OBSERVATION=PENDING
STAGING_RUNNING=YES
STEP06_CLOSED=NO
```

# 22. 下一步｜只做 Step 6 24h closeout

下一动作不是继续施工，而是等待 observation window 自然满足后做一次短验收。

若 24h Gate PASS：

```text
STEP06_24H_OBSERVATION=PASS
STEP06_CLOSED=YES
```

然后才讨论 Step 6 branch 的 merge / push 与 Step 7 开工。

若发现 timer / duplicate send / service error / daily backup 任一异常，只针对真实异常修 Step 6；不要借机扩成 Reminder 架构重做或 3.5-A 回头审计。

# 23. 新窗口接班短句

> G，继续 S-notes GreenCloud 3.5-B / Step 6 收口。3.5-A 已 CLOSED，不重做。Step 6 Cloud Reminder staging 已完成 implementation、localhost/cloud Gate 与公网 HTTPS immediate Gate：branch `codex/gc35-step06-cloud-reminder-staging`，当前最新验收记录 commit `de820e1`，未 push、未 merge main。GreenCloud API 仅监听 `127.0.0.1:8766`，公网由 `https://reminder.ke-den.de` + 正式 TLS 经 Caddy 转发；72/72 HTTPS 正负例 PASS，401/403 PASS，ntfy staging topic=`s-step06`，原 ntfy/Caddy 配置未改。Cloud daily backup 与 Mac off-VPS 手动拉取都已验证，rollback=`ssh optimal /srv/snotes/rollback-step06.sh`。现在唯一未完成的是 24h observation，最早 `2026-09-30 08:56:56 UTC` 后检查 timer、dedupe/journal、service errors、次日 fixture notification 与 daily backup。当前 `STEP06_IMMEDIATE_GATE=PASS`、`STEP06_24H_OBSERVATION=PENDING`、`STEP06_CLOSED=NO`。不要进入 Step 7，不 push，不 merge main。

---

# 24. GreenCloud 3.5-B / Step 6｜24h Observation PASS，正式 CLOSED

**补记日期：2026-09-30**  
**状态：Step 6 Cloud Reminder staging 的 24h observation 已完成并 PASS；Step 6 正式 CLOSED。**

上一节记录的：

```text
STEP06_24H_OBSERVATION=PENDING
STEP06_CLOSED=NO
```

现更新为：

```text
STEP06_IMPLEMENTATION=READY
STEP06_LOCAL_AND_CLOUD_GATE=PASS
STEP06_IMMEDIATE_GATE=PASS
STEP06_24H_OBSERVATION=PASS
STEP06_CLOSED=YES
STEP07_STARTED=NO
```

## 24.1 Observation window

实际观察窗口：

```text
START=2026-09-29 08:56:56 UTC
END=2026-09-30 12:13:55 UTC
DURATION=27h 16m 59s
```

满足 FINAL 要求的至少 24h 连续运行窗口。

## 24.2 Timer / scheduler｜PASS

观察窗口内：

```text
SCHEDULED_ROUNDS=327
MISSING_ROUNDS=0
TOTAL_SUCCESSFUL_EXECUTIONS=329
```

其中总成功执行数包含启动及 catch-up；定时轮次无缺失。

## 24.3 Duplicate / dedupe｜PASS

```text
NOTIFICATIONS_SENT=3
DEDUPE_MARKS=3
DUPLICATE_SENDS=0
```

累计 3 条 staging 通知与 3 个 dedupe mark 一一对应，没有重复发送。

## 24.4 Services / restart｜PASS

API / scheduler / backup 三项 Step 6 service 在观察窗口内：

```text
RELATED_ERRORS=0
CRASHES=0
ABNORMAL_RESTARTS=0
NRestarts=0
```

没有发现与 Step 6 有关的异常 error、crash 或异常 restart。

## 24.5 Next-day fixture｜PASS

```text
NEXT_DAY_FIXTURE_AT=2026-09-30 02:00 UTC
SEND_COUNT=1
FOLLOW_UP_DUPLICATE=0
```

次日 fixture 正常触发一次，后续未重复发送。

## 24.6 Daily backup｜PASS

```text
BACKUP_RUN=2026-09-30 03:15 UTC
SQLITE_OPEN=PASS
PRAGMA_INTEGRITY_CHECK=ok
REMINDERS=4
REMINDER_TABLES=3
REMINDER_OCCURRENCES=4
COUNTS_MATCH_STAGING=YES
```

每日备份已按 timer 实际生成，SQLite 可打开，完整性检查正常，关键 counts 与 staging 一致。

## 24.7 Step 6 closeout commit

24h observation 结果已写入 Step 6 验收报告：

```text
CLOSEOUT_COMMIT=a7c17b7
```

该 commit 仅更新报告，没有进入 Step 7，也没有在此阶段 merge / push。

至此，Step 6 的 implementation、即时 Gate、24h observation 三层验收全部完成。

---

# 25. Step 6 本地正式收口｜6 份项目文档纳入 Git + ff-only merge

Step 6 CLOSED 后，S 明确确认此前保留的 6 份 untracked 文档均为 S-notes 项目方案书 / 进度书，不含需要排除的私人资料，可以正式纳入版本库。

纳入 Git 的 6 份文档均位于 `docs/`：

1. `S-notes-AI接受链修复包-施工单-v1.1.md`
2. `S-notes-final-progress-20260925-EDITOR-CODE-SIZE-CLOSED-ACCEPTANCE-REPAIR-NEXT-G.md`
3. `S-notes-final-progress-20260929-POST-ACCEPTANCE-CLEANUP-AND-ANTIGRAVITY-DOCK-CLOSED-G.md`
4. `S-notes-修复包复核-交接指令-v1.1.md`
5. `S-notes-架构健康审计-20260924.md`
6. `S-notes-编辑器代码块与字号-小工单-v1.0.md`

文档原样收编：未改内容、未改名、未搬目录。

Docs commit：

```text
fdbb6eb
```

随后 Step 6 construction branch 以 fast-forward only 合入本地 main：

```text
MERGE_MODE=FAST_FORWARD_ONLY
MAIN_BEFORE=f8bd7df
MAIN_AFTER=fdbb6eb
STEP06_BRANCH_RETAINED=YES
WORKTREE=clean
```

没有制造 merge commit，没有 rebase / squash，也没有删除 Step 6 construction branch。

---

# 26. Step 6 远端基线固定｜PUSH PASS

本地 Step 6 正式收口后，将当前 `main` 推送到 `origin/main`。

最终结果：

```text
HEAD=fdbb6eb
MAIN=fdbb6eb
ORIGIN_MAIN=fdbb6eb
GIT_STATUS=clean
PUSH=PASS
```

因此从本节开始，`fdbb6eb` 是 GreenCloud 3.5-B / Step 6 正式远端封口基线。

Step 6 construction branch 继续保留；没有进入 Step 7。

当前正式状态：

```text
GREENCloud_3_5_A=CLOSED
STEP06_IMPLEMENTATION=READY
STEP06_LOCAL_AND_CLOUD_GATE=PASS
STEP06_IMMEDIATE_GATE=PASS
STEP06_24H_OBSERVATION=PASS
STEP06_CLOSED=YES
MAIN=ORIGIN_MAIN=HEAD=fdbb6eb
WORKTREE=clean
PUSH=PASS
STEP07_STARTED=NO
```

此前 §21–§23 中所有 `STEP06_24H_OBSERVATION=PENDING`、`STEP06_CLOSED=NO`、`PUSH=NO`、`MERGE_MAIN=NO` 的描述，从现在起均只视为历史阶段状态，不得再作为当前状态使用。

# 27. 下一步｜GreenCloud 3.5-B / Step 7

下一步才正式进入 Step 7。

开始 Step 7 时继续遵守：

- 不重做已经 CLOSED 的 3.5-A；
- 不重新施工 Step 6；
- 不把 3.5-A 的 3 个 non-blocking P2 捞回主线；
- 以 `main == origin/main == HEAD == fdbb6eb` 为正式开工基线；
- Step 6 staging 当前已经稳定运行并完成 24h Gate，后续只按既有 3.5 FINAL 进入下一阶段。

# 28. 新窗口接班短句

> G，继续 S-notes GreenCloud 3.5-B / Step 7。3.5-A 已 CLOSED；Step 6 Cloud Reminder staging 也已正式 CLOSED。Step 6 implementation、localhost/cloud Gate、HTTPS immediate Gate、27h16m59s observation 全部 PASS；327 个定时轮次无缺失，累计 329 次成功执行，3 次通知对应 3 个 dedupe mark、无重复，API/scheduler/backup 三项 service `NRestarts=0`，次日 fixture 与 03:15 UTC daily backup 均 PASS。Step 6 closeout commit=`a7c17b7`。此前 6 份 S-notes 项目方案/进度文档已原样纳入 `docs/`，docs/ff-only merge 后 commit=`fdbb6eb`。当前 `main == origin/main == HEAD == fdbb6eb`，`git status=clean`，`PUSH=PASS`；Step 6 construction branch 保留。不要重做 3.5-A 或 Step 6，也不要处理 3 个 non-blocking P2。下一步只按 3.5 v2.2 FINAL / 11-step FINAL 进入 Step 7。

---

# 29. GreenCloud 3.5-B / Step 7｜Remote Reminder Client 正式 CLOSED

**补记日期：2026-10-01**  
**状态：Step 7 Remote Reminder Client、真人 Gate、Post-Vibe repair、live Reminder 修复部署、独立 focused re-audit 与 final candidate closeout 全部完成；Step 7 正式 CLOSED。Step 8 尚未开始。**

上一节记录的：

```text
STEP06_CLOSED=YES
STEP07_STARTED=NO
```

现更新为：

```text
STEP06_CLOSED=YES
STEP07_CLOSED=YES
POST_VIBE_FINAL_P0=0
POST_VIBE_FINAL_P1=0
READY_FOR_STEP08=YES
STEP08_STARTED=NO
```

Step 7 construction branch：

```text
codex/gc35-step07-remote-reminder-client
```

Step 7 closeout commit：

```text
b219115cfe82f63a3458d1799014c6edab21632f
```

当前 Step 7 branch worktree：

```text
WORKTREE=clean
```

本次 closeout **没有 merge main、没有 push、没有替换正式 App、没有迁移正式 Reminder DB、没有退役正式 Mac Reminder helper，也没有进入 Step 8**。

因此当前 Git 需要区分：

```text
FORMAL_MAIN=origin/main=fdbb6eb
STEP07_BRANCH_HEAD=b219115cfe82f63a3458d1799014c6edab21632f
STEP07_MERGED_TO_MAIN=NO
STEP07_PUSHED=NO
```

# 30. Step 7 最终交付｜Remote Reminder Client + Authority Boundary

Step 7 最终交付的主链：

```text
Mac S-notes candidate / MCP client
→ explicit Reminder authority
→ HTTPS RemoteReminderClient
→ GreenCloud Reminder API
→ GreenCloud Reminder DB（staging authority）
```

最终边界：

- Remote Reminder Client 使用既有 snapshot / commands API；
- GUI 网络请求由 Qt worker 执行，不阻塞 UI；
- loading / pending / non-writable 状态禁用写控件；
- generation 防止 stale response 覆盖新状态；
- remote/offline 仅展示 GUI 私有 last-compatible snapshot；
- **没有 local Reminder DB fallback**；
- **没有 offline write queue**；
- GUI owner credential 仍只由 owner Keychain path 使用；
- `yan` / `claude-code` actor 继续使用各自独立 credential，不借 owner credential；
- write 前后以 Cloud authority 状态为准，不做 optimistic local mutation；
- snapshot GET 保持 read-pure，不偷偷 materialize occurrence；
- helper retirement 机制已经准备，但 Step 7 不执行正式退役。

## 30.1 Reminder authority 变为显式配置

初版 Step 7 仍存在：

```text
有 remote endpoint → remote
缺 remote endpoint → local writable
```

Post-Vibe 修复后改为单一 authority resolver：

```text
local  → explicit local
remote → explicit remote
missing / invalid → fail closed
```

GUI、MCP stdio、repo scripts、helper、source/dev launch 与 packaged candidate 均复用同一 authority 判定，不再通过“endpoint 是否存在”猜 authority。

正式 App 当前仍保持 local authority，是 Step 7 施工阶段的预期状态；Step 8 才做正式 Cloud authority cutover。

## 30.2 Wire contract 与 DB schema 解耦

Step 7 初版把 remote wire compatibility 直接绑在：

```text
REMINDER_SCHEMA_VERSION
```

这会导致未来 DB migration 即使没有改变网络协议，也可能把旧客户端错误判为 schema mismatch。

最终新增独立 remote contract owner，wire/API version 与 storage schema version 分离；当前 wire version 保持既有兼容值，不因 DB schema 演进自动变化。

`check_architecture.py` 最终保持：

```text
ARCHITECTURE_CHECK=PASS
```

没有通过修改 checker / allowlist 来掩盖 Step 7 引入的依赖问题。

# 31. Step 7 真人 UX Gate｜PASS

S 对打包 candidate 完成人工使用验收。

初版网络行为：

- initial load 约 0.5–1 秒；
- edit dialog 快；
- create / pause / resume / complete / delete 一度稳定约 3–4 秒。

排查后确认耗时主要来自每次 mutation 重付 HTTPS/TUN connection handshake，而不是 Qt UI 卡顿。

最终修复：

- retained HTTPS connection 跨操作复用；
- 120 秒 idle policy；
- retained read connection 失效时允许 GET reconnect/retry once；
- **write 永不自动重发**；
- 保留 4 秒 timeout；
- 保留 mutation 前 prewrite snapshot 与成功后的 authoritative refresh；
- App close 时正常释放 connection。

S 最终真人反馈：

```text
连续操作不再 3 秒
连续 CRUD 基本 < 2 秒
UX_ACCEPTED=YES
```

因此 latency fix 与 Remote Reminder CRUD UX 真人 Gate PASS。

# 32. Step 7 首轮 Post-Vibe 独立审计｜P0=0 / P1=3 / P2=4

Step 7 真人 Gate 后，对 Step 6 + Step 7 Reminder path 做独立只读 Post-Vibe / 防屎山审计。

首轮结论：

```text
AUDIT_STATUS=COMPLETE_FINDINGS_PRESENT
P0_COUNT=0
P1_COUNT=3
P2_COUNT=4
NO_HIDDEN_PROTOCOL=YES
CREDENTIAL_BOUNDARY=CLEAN
READY_TO_CLOSE_STEP07=NO
READY_FOR_STEP08=NO
```

三个 P1 均为真实主路径问题。

## 32.1 P1-1｜CN A-share unsupported year 可毒死整个 Reminder 系统

旧路径中：

```text
create / completion
→ 先写 DB
→ 下一 schedule 进入 unsupported calendar year
→ 日历错误被部分路径吞掉
→ 留下 active reminder 但无合法 occurrence
→ snapshot/list 抛异常
→ scheduler 整体失败
→ client 写前 snapshot 失败
→ 连正常 delete/repair 都可能进不去
```

年末 completion 还可能出现：

```text
current occurrence 已写 completed
→ 下一 occurrence 计算失败
→ 用户看到失败
→ DB 已发生部分提交
```

最终修复：

- calendar year unsupported 使用专门业务错误，不再靠错误字符串匹配；
- create / modify / restore / recurrence completion 先做 schedule preflight；
- transition 要么完整成功，要么 DB 保持 transition 前一致状态；
- 已存在 bad schedule 被单 item 隔离，不再拖死整个 snapshot；
- scheduler 跳过 bad item，继续处理 healthy reminders；
- owner 仍可定位、修改或删除坏 item；
- HTTP 对业务异常返回受控错误，不再 socket drop 被误判为 offline。

同时，原先重复的 occurrence 选择逻辑在这一真实修复路径上自然收敛，没有另外启动 DRY 重构工程。

## 32.2 P1-2｜Architecture gate + wire version 绑定 DB schema

Step 7 曾把架构检查从此前 PASS 带到多个 layer violation，主要包括：

- application → transport；
- UI / transport → storage schema；
- client/server/UI/cache 共用 DB schema version 作为 remote contract version。

最终修复：

- 新建独立 Reminder remote contract owner；
- wire/API version 与 DB schema version 分离；
- MCP adapter 改为由 composition 注入 remote client，而不是 application 反向依赖 transport；
- architecture checker 回到 0 error；
- checker 本身未为修复放宽规则。

## 32.3 P1-3｜缺 remote 配置时 fail-open 回 local writable

旧风险：Step 8 以后若某入口漏了 endpoint / runtime env，可能静默构造 local writable `ReminderService`，重新形成第二套 authority。

最终修复：

- authority 显式 `local | remote`；
- missing / invalid fail closed；
- 所有主要入口共用 resolver；
- remote authority 下 legacy helper 拒绝运行；
- build-time authority / helper-retirement state 写入 packaged runtime 可读取配置，不依赖 Finder 启动时继承 build shell env。

这部分已经解决“Step 8 后 local writable path 还能因漏配置被误启动”的安全问题。

# 33. P2-2 提前修复｜write outcome 不再把“不确定”说成“失败”

首轮审计另发现：

```text
POST 已成功
→ authoritative refresh 失败
→ UI 却报告 command failed
```

以及：

```text
POST 已发出
→ timeout / connection lost
→ 旧回执却可能声称 modified=false
```

S / G 决定不把这项留到以后，按“准 P1”提前修复。

最终明确三类结果：

1. **definite failure**：服务器明确拒绝 / validation / auth failure；
2. **command confirmed, refresh failed**：服务器已确认 mutation 成功，但列表刷新失败；UI 显示 stale/read-only，不说“操作失败”；
3. **outcome unknown**：POST 已发出但未拿到可靠提交结果；不得声称未修改，且 write 不自动 retry。

MCP unknown 结果使用 nullable change state / explicit outcome unknown；actor `request_id` 幂等机制保留。

没有引入新的 optimistic local mutation 或 exactly-once framework。

# 34. Step 7 两个真实 regression｜CLOSED

Post-Vibe repair 后又确认两个 Step 7 regression，此前报告曾错误把它们当成既有失败。

## 34.1 Reminder table sidebar sync｜修复

Step 7 把本地 Reminder table create / move / delete 改走 `center.*_table()` 后，漏掉原有 sidebar 同步调用。

最终只恢复原本的 `_sync_sidebar_reminder_filters()` 路径，共极小改动；remote path 未受破坏。

## 34.2 Sidebar width parity｜修复

真实 Step 7 前默认 sidebar width 为约 360，而初版 candidate 被 Endpoint 输入框的 `setMinimumWidth(190)` 把 Reminder panel 撑宽，导致 sidebar 被挤到约 307。

最终去掉该额外 minimum width：

```text
DEFAULT_SIDEBAR_WIDTH=360
```

Endpoint UI 保留。

修复 commit：

```text
e99a49a
```

# 35. 3.5-A Notes publisher runtime coupling｜再次收口

Step 7 re-audit 期间发现一个跨阶段但真实 production dependency：3.5-A 的五分钟 Notes mirror publisher 虽已从 Codex isolated worktree 脱钩，却仍直接运行当前开发 repo checkout。

旧状态：

```text
WorkingDirectory=/Users/s/Documents/项目/S-notes
python=/Users/s/Documents/项目/S-notes/.venv/bin/python
```

因此开发 repo checkout branch、重建 `.venv`、升级依赖，都可能影响已经 CLOSED 的 production publisher。

本轮做了两步最小 cleanup。

## 35.1 Source decoupling

production source 改为 revision-pinned 普通导出目录：

```text
~/Library/Application Support/S-notes/runtime/greencloud-publisher/
fdbb6eb97f90b6cbf50b62df196582f9f7ad51b2/
```

它不是 Git checkout，不追踪任何 branch，不依赖 Codex / Claude worktree。

source decoupling commit：

```text
290adfb
```

## 35.2 Interpreter / venv decoupling

随后继续移除 developer repo `.venv` runtime dependency。

production venv：

```text
~/Library/Application Support/S-notes/runtime/greencloud-publisher/venvs/
fdbb6eb97f90b6cbf50b62df196582f9f7ad51b2/
```

base Python：

```text
/opt/homebrew/opt/python@3.13/bin/python3.13
```

依赖由同 revision 的 `requirements.txt` 提供，不复制开发 `.venv`，不从 `pip freeze` 临时造锁文件。

interpreter decoupling commit：

```text
7434fd3
```

最终：

```text
PUBLISHER_SOURCE_DECOUPLED=YES
PUBLISHER_INTERPRETER_DECOUPLED=YES
DEVELOPER_REPO_SOURCE_DEPENDENCY=NO
DEVELOPER_REPO_VENV_DEPENDENCY=NO
PUBLISHER_SMOKE=PASS
```

LaunchAgent：

```text
com.snotes.greencloud-mirror-publish
```

仍维持既有 cadence 与行为，没有新增 daemon / auto git sync / 自动追 main。

# 36. 第一次独立 re-audit｜唯一 P1 = live Reminder 修复未部署

修复包完成后，换独立 AI 对 Step 6 + Step 7 做只读 re-audit。

结果：

```text
P0_COUNT=0
P1_COUNT=1
READY_TO_CLOSE_STEP07=NO
READY_FOR_STEP08=NO
```

这次没有发现新的源码逻辑 P1；唯一 blocker 是：

> repo 中 P1-1 / P1-2 已修好，但 GreenCloud 正在运行的 Reminder runtime 仍是旧代码。

live runtime：

```text
/srv/snotes/reminder-runtime
```

现场确认核心文件仍匹配修复前 revision；因此 live authority 仍可复现 poison create、snapshot/scheduler 全挂和 partial completion。

这说明：

```text
SOURCE_FIXED=YES
LIVE_FIXED=NO
```

因此没有用“本地测试 PASS”冒充 production/staging runtime 已修复。

# 37. Live Reminder repair deployment｜PASS

随后只把 P1-1 / P1-2 所需的最小服务端 dependency closure 部署到现有 GreenCloud Reminder runtime。

最终部署文件：

```text
src/snotes/application/reminder_service.py
src/snotes/domain/reminder_schedule.py
src/snotes/domain/reminders.py
src/snotes/domain/reminder_remote_contract.py
src/snotes/transport/reminder_http.py
```

部署 source / repo commit：

```text
b379d5f39fc5f79f3748cfa48ee3e60cfc0e2b9d
```

验证：

```text
P1_1_DEPLOYED_BEHAVIOR=PASS
P1_2_DEPLOYED_BEHAVIOR=PASS
TEMP_DB_POISON_CREATE=PASS
TEMP_DB_YEAR_END_ATOMICITY=PASS
TEMP_DB_BAD_ITEM_ISOLATION=PASS
HTTP_ERROR_MAPPING=PASS
LIVE_SNAPSHOT_SMOKE=PASS
LIVE_SCHEDULER_HEALTH=PASS
DB_MIGRATION=NO
STAGING_REAL_DATA_MUTATED=NO
NTFY_CONFIG_CHANGED=NO
```

API 按需重启；scheduler 为 oneshot，没有人为 restart，部署后下一次自然执行成功、exit 0、无新 traceback。

真实 staging 的 8 条 Reminder snapshot 摘要部署前后保持一致。

回滚备份：

```text
/root/greencloud-backups/step07-post-vibe-repair-20261002T000823Z/
```

# 38. Focused read-only re-audit｜P0=0 / P1=0，PASS

live repair 部署后，再做一次独立 focused read-only re-audit，只确认此前唯一 P1 是否从 live authority 真正消失。

最终：

```text
FOCUSED_REAUDIT_STATUS=PASS
P0_COUNT=0
P1_COUNT=0
LIVE_RUNTIME_IDENTITY=PASS
FORMER_P1_CLEARED=YES
P1_1_LIVE_FIXED=YES
P1_2_LIVE_FIXED=YES
POISON_CREATE=PASS
YEAR_END_ATOMICITY=PASS
BAD_ITEM_ISOLATION=PASS
HTTP_ERROR_MAPPING=PASS
LIVE_SNAPSHOT=PASS
LIVE_SCHEDULER=PASS
ARCHITECTURE_CHECK=PASS
SINGLE_SOURCE_OF_TRUTH=YES
READY_TO_CLOSE_STEP07=YES
READY_FOR_STEP08=YES
```

至此 Step 7 Post-Vibe blocker 清零。

# 39. Step 7 final candidate + formal closeout｜CLOSED

focused re-audit PASS 后，从最新 tracked clean source 重建 final candidate。

最终 candidate：

```text
SOURCE=b379d5f39fc5f79f3748cfa48ee3e60cfc0e2b9d
PATH=/Users/s/Documents/项目/S-notes/dist-step07-candidate/S-notes.app
BUNDLE_ID=de.ke-den.snotes.step07candidate
```

closeout 验收：

```text
STEP07_FINAL_CANDIDATE_REBUILT=YES
STEP07_FINAL_CANDIDATE_SMOKE=PASS
SIDEBAR_REGRESSIONS_IN_FINAL_CANDIDATE=PASS
OUTCOME_SEMANTICS_IN_FINAL_CANDIDATE=PASS
ARCHITECTURE_CHECK=PASS
TARGETED_TESTS=63/63_PASS
POST_VIBE_FINAL_P0=0
POST_VIBE_FINAL_P1=0
LIVE_REMINDER_REPAIR=PASS
PUBLISHER_RUNTIME_DECOUPLED=PASS
FORMAL_APP_UNCHANGED=YES
FORMAL_REMINDER_DATA_UNCHANGED=YES
FORMAL_HELPER_UNCHANGED=YES
STEP08_STARTED=NO
```

Step 7 原报告已从 `HUMAN_GATE_READY` 更新为正式 CLOSED。

closeout commit：

```text
b219115cfe82f63a3458d1799014c6edab21632f
```

最终结论：

```text
STEP07_CLOSED=YES
READY_FOR_STEP08=YES
```

# 40. Step 7 repair / closeout commit chain

本阶段主要 commit 轨迹：

```text
0fd089d   Step 7 初始 Remote Reminder Client implementation
...
ecd8843   latency fix + Post-Vibe P1 repair / P2-2 outcome repair
448442e   repair evidence record
e99a49a   sidebar sync + width regression fix
290adfb   production Notes publisher source decoupling
7434fd3   production Notes publisher interpreter/venv decoupling
b379d5f   live Reminder repair deployment closure
b219115   Step 7 formal closeout report
```

其中 `...` 表示 Step 7 施工过程中存在中间提交；此处只记录本轮封口判断直接依赖的关键节点。

# 41. Deferred maintenance｜最终 4 个 P2，全部 NON-BLOCKING

**注意：审计过程中 P2 编号曾发生变化。为避免未来旧记录与新记录互相打架，本节开始以 Step 7 final closeout report 的 deferred P2 编号为唯一现行口径。**

以下 4 项均不阻塞 Step 8，但必须按触发节点继续处理，不得因为 Step 7 CLOSED 后被遗忘。

## P2-1｜ntfy / topic / config ownership

当前 staging 仍存在 Step 6 / Step 7 专用 topic / endpoint / transport policy 固定值。

这在 staging 阶段是明确且 fail-closed 的，不是当前事故；但正式 authority cutover 时不能继续以 staging identity 进入 production。

触发节点：

```text
STEP8_PRODUCTION_CUTOVER
```

Step 8 一并完成：

- production ntfy topic；
- production endpoint / config；
- staging identity 退出正式路径；
- transport / business config ownership 收口。

**现在不提前施工。**

## P2-2｜no-occurrence display semantics

当前仍存在一处语义尾巴：有效 active item 如果没有 occurrence，部分 list/display path 可能把它呈现成 `completed`。

这不是当前 P1-1 poison path；坏 schedule 已有明确隔离行为。

触发节点：

```text
STEP8_MIGRATION_REHEARSAL
```

处理规则：

```text
如果 Step 8 正式 migration 后该状态现实可达
→ 做窄修，避免错误显示 completed

如果有效迁移状态下现实不可达
→ 保持 non-blocking maintenance，不为了理论状态扩 Reminder 状态机
```

## P2-3｜Reminder owner slimming / dead local path cleanup

当前主要大 owner 包括：

```text
reminder_service.py
reminder_center.py
reminder_http.py
local_mcp_service.py
remote_reminder_controller.py
```

其中 `reminder_service`、`reminder_center` 已超过千行量级。

**当前不做大文件拆分工程。**

原因：

- Step 8 仍直接依赖 `reminder_service` 作为核心业务 owner；
- 现在拆 owner 会在正式 authority cutover 前制造高回归风险；
- `reminder_center` 的大量 local 分支在 3.5-B Cloud authority 正式切换后，本来就会失去存在意义；
- 现在先搬一遍、过几天再删一遍没有收益；
- 行数本身不是 P1，当前责任边界仍能辨认。

触发节点：

```text
3.5-B CLOSED 后
```

单独开：

```text
Reminder owner slimming / dead local path cleanup
```

执行顺序应优先：

```text
先删除 / 收敛已退役 local writable path
→ 再按剩余真实职责拆 owner
```

明确边界：

> P2-3 只表示“不现在做大文件拆分”，不表示这些 owner 在 Step 8 期间不能为真实 cutover / bug fix 做必要修改。

特别是 authority safety 仍优先于“以后瘦身”：

```text
Step 8 后 local writable path 若仍能被现实启动
→ authority blocker，必须在 cutover 前解决
```

同时保留施工纪律：

> **Step 8 期间不要继续向这些大 owner 添加与 cutover 无关的新职责。**

## P2-4｜Endpoint / sidebar max-width UI tail

Step 7 真正的 width regression 已修：

```text
旧 candidate：sidebar 约 307
final source / candidate：default sidebar 约 360
```

旧 `endpoint_input.setMinimumWidth(190)` 已不再造成 default width 回归。

剩余 UI polish：Endpoint 区域仍使 Reminder panel 的整体 minimum width 比 Step 7 前略大，因此默认约 1420px 窗口下，sidebar 最大可拖宽约 463，而不是历史理论上限约 480。

这不影响默认布局与业务功能。

触发节点：

```text
3.5-B 后续 UI maintenance / owner cleanup
```

当前不为了约 17px 最大拖拽差异删除 Endpoint UI，也不阻塞 Step 8。

# 42. 旧审计项的最终去向｜避免重复施工

此前初审中另外两个 maintenance 点已经有明确结论，不再作为当前 deferred P2 重复挂账：

## command success + refresh failure outcome

已在 Step 7 repair 中正式修复，不再是待办：

```text
DEFINITE_FAILURE
CONFIRMED_BUT_STALE
OUTCOME_UNKNOWN
```

三种语义已进入 final candidate，write 不自动 retry。

## occurrence selection duplicate logic

没有单独启动 DRY 重构；在 P1-1 schedule repair 的真实路径中已经自然收敛了共享判断逻辑。

因此当前只保留 §41 的 4 个 deferred P2。

# 43. 当前 Git / Runtime 总状态｜Step 7 CLOSED

当前正式基线需要区分 production main 与 Step 7 construction branch：

```text
MAIN=fdbb6eb
ORIGIN_MAIN=fdbb6eb
STEP07_BRANCH=codex/gc35-step07-remote-reminder-client
STEP07_BRANCH_HEAD=b219115cfe82f63a3458d1799014c6edab21632f
WORKTREE=clean
STEP07_MERGED_TO_MAIN=NO
STEP07_PUSHED=NO
```

Mac formal runtime：

```text
FORMAL_APP_UNCHANGED=YES
FORMAL_REMINDER_DB_MIGRATED=NO
FORMAL_REMINDER_HELPER_RETIRED=NO
```

GreenCloud Reminder staging：

```text
STEP06_CLOSED=YES
LIVE_REMINDER_REPAIR=PASS
P1_1_LIVE_FIXED=YES
P1_2_LIVE_FIXED=YES
API_HEALTH=PASS
SCHEDULER_HEALTH=PASS
SINGLE_SOURCE_OF_TRUTH=YES_FOR_CURRENT_STAGING
```

Notes publisher：

```text
PRODUCTION_SOURCE=revision-pinned export
PRODUCTION_VENV=revision-scoped venv
DEVELOPER_REPO_SOURCE_DEPENDENCY=NO
DEVELOPER_REPO_VENV_DEPENDENCY=NO
PUBLISHER_RUNTIME_DECOUPLED=PASS
```

Post-Vibe final：

```text
POST_VIBE_FINAL_P0=0
POST_VIBE_FINAL_P1=0
STEP07_CLOSED=YES
READY_FOR_STEP08=YES
STEP08_STARTED=NO
```

此前任何 `HUMAN_GATE_READY`、`READY_FOR_READONLY_REAUDIT`、`READY_TO_CLOSE_STEP07=NO`、`P1_COUNT=1` 的描述均只代表施工中间状态；当前最终状态以本节为准。

# 44. 下一步｜GreenCloud 3.5-B / Step 8

下一步正式进入 Step 8：

> **Reminder 正式切云 + Mac helper 永久退役（封 3.5-B）**

Step 8 尚未开始，不能把 Step 7 candidate / staging 状态冒充正式 authority cutover。

进入 Step 8 时继续遵守既有 3.5 v2.2 FINAL / 11-step FINAL 边界：

- 先做正式 Mac Reminder DB → staging/production candidate DB 的 migration rehearsal；
- 对 counts / schedules / occurrences / multi-notification nodes / `ai_reminder_operations` 做一致性核对；
- 正式 cutover 前处理 P2-1 production ntfy/topic/config ownership；
- rehearsal 时核实 P2-2 no-occurrence display state 是否现实可达；
- authority 切换必须显式 remote，禁止任何“配置缺失 → local writable”回退；
- 正式 helper 只在 cutover Gate 满足后退休；
- 不删除 Mac 旧 Reminder data；
- 不扩大 AI Reminder 权限；
- 不顺手重构大 owner；
- 不触碰 `com.snotes.gale-production-pull`；
- 3.5-B CLOSED 后再单独做 P2-3 owner slimming / dead local path cleanup。

# 45. 新窗口接班短句

> G，继续 S-notes GreenCloud 3.5-B / Step 8。Step 6 Cloud Reminder staging 已 CLOSED；Step 7 Remote Reminder Client 也已正式 CLOSED。Step 7 construction branch=`codex/gc35-step07-remote-reminder-client`，closeout commit=`b219115cfe82f63a3458d1799014c6edab21632f`，worktree clean，尚未 merge main / push；formal main/origin-main 仍为 Step 6 基线 `fdbb6eb`。Step 7 final candidate source=`b379d5f39fc5f79f3748cfa48ee3e60cfc0e2b9d`，path=`/Users/s/Documents/项目/S-notes/dist-step07-candidate/S-notes.app`，packaged smoke PASS，63/63 targeted tests PASS。首轮 Post-Vibe P0=0/P1=3；P1-1 A-share unsupported-year poison、P1-2 architecture/wire-schema coupling、P1-3 authority fail-open 均已修复，command outcome ambiguity 也已提前修；两个 sidebar regression 已修。期间再次把 3.5-A Notes publisher 的 source + venv 从开发 repo 彻底解耦。第一次 re-audit 只剩 1 个 P1：live Reminder runtime 尚未吃到修复；随后将 5 个服务端文件部署到 `/srv/snotes/reminder-runtime`，临时 DB / live smoke PASS；最终 focused re-audit=`P0=0/P1=0`、`READY_FOR_STEP08=YES`。正式 App、正式 Reminder DB、正式 helper 仍未切换，`STEP08_STARTED=NO`。进入 Step 8 时按既有 11-step FINAL 做 migration rehearsal → production cutover → helper retirement，不重做 Step 7。Deferred P2：① ntfy/topic/config ownership→Step 8；② no-occurrence display semantics→Step 8 rehearsal 现实可达性判断；③ owner slimming/dead local path cleanup→3.5-B CLOSED 后单独做；④ Endpoint/sidebar 最大拖宽尾巴→后续 UI maintenance。Step 8 期间不要继续往几个大 owner 里塞无关新职责。
---

# 46. GreenCloud 3.5-B / Step 8｜正式 CLOSED

**补记日期：2026-10-03（Step 8 最终真人 / Cloud Gate 含北京时间 2026-10-04 证据）**  
**状态：Step 8A–8E、§59 32/32、双独立 Post-Vibe、最终 P2 cleanup、post-close maintenance 全部完成。3.5-B 正式封口。Step 9 尚未开始。**

上一节 §44–§45 的：

```text
READY_FOR_STEP08=YES
STEP08_STARTED=NO
```

从本节开始更新为：

```text
STEP08A=CLOSED
STEP08B=CLOSED
STEP08C=CLOSED
STEP08D=CLOSED
STEP08E=CLOSED

REMINDER_ACCEPTANCE_32_OF_32=PASS
GREENCloud_3_5_B=CLOSED
STEP09_STARTED=NO
```

本轮最终 formal Git 基线已经不是旧 Step 6 / Step 7 基线。当前正式状态见 §55。

# 47. Step 8A｜Migration Rehearsal CLOSED

Step 8A closeout commit：

```text
4d27217ed0629d39c98ddd87ea1a6093a22caa77
```

正式 rehearsal 使用 Mac Reminder authority 的只读 SQLite snapshot，在隔离 candidate 中完成迁移与完整 reconciliation。

关键结果：

```text
SOURCE_SCHEMA_VERSION=4
TARGET_STORAGE_SCHEMA_VERSION=5
WIRE_SCHEMA_VERSION=4

LABELS=2
TABLES=5
REMINDERS=51
OCCURRENCES=66
AI_REMINDER_OPERATIONS=87
ELIGIBLE_BUSINESS_SETTINGS=2
MULTI_NOTIFICATION_PERSISTENCE_ROWS=117

LABELS_DIFF=0
TABLES_DIFF=0
REMINDERS_DIFF=0
OCCURRENCES_DIFF=0
MULTI_NOTIFICATION_DIFF=0
AI_REMINDER_OPERATIONS_DIFF=0
BUSINESS_SETTINGS_DIFF=0
SCHEDULE_DIFF=0
```

schedule parity：

```text
ACTIVE_ITEMS=40
PROBE_DATES=48
NOTIFICATION_IDENTITIES=1020
DIFF=0
```

rehearsal 同时确认旧 Step 7 deferred P2-2：

```text
NO_OCCURRENCE_REACHABILITY=REACHABLE
```

即：

```text
active one-time Reminder
→ current occurrence 被完成 / 不再存在
→ raw lifecycle 仍 active
→ 旧 projection 可能错误显示 completed
```

当时真实 source / candidate 中该状态均为 0 条，因此不阻塞 migration；按既有决定留到 post-close maintenance 做窄修。

# 48. Step 8B｜Formal Cloud Migration CLOSED

Step 8B closeout commit：

```text
9b27eb9a84459ffe047296c22fe7ac8c8447d737
```

正式 Cloud Reminder DB：

```text
/srv/snotes/data/reminder-production.db
```

Mac pre-Cloud-authority 静态 rollback backup：

```text
/Users/s/Library/Application Support/S-notes/backups/
greencloud-step08b-20261003T020342Z/mac-pre-cloud-authority.db
```

结果：

- frozen Mac source → Cloud formal DB 全部 canonical counts / fields / relationships zero-diff；
- occurrence history、notification marks、multi-notification state、AI Reminder operation history完整保留；
- transport setting、旧 topic、scheduler stamp、shared `ai_operations` 不迁移；
- Cloud formal DB quick_check / FK PASS；
- Mac 原 Reminder DB 不删除；
- Step 8B 不提前启动 production scheduler / ntfy；
- staging DB 不被此次 migration 修改。

# 49. Step 8C｜Cloud Production Activation CLOSED

Step 8C closeout commit：

```text
c4d08b774be22015c4c580800558153f939c18e6
```

正式 runtime：

```text
REMINDER_DB=/srv/snotes/data/reminder-production.db
PUBLIC_ENDPOINT=https://reminder.ke-den.de
NTFY_ENDPOINT=https://ntfy.ke-den.de
PRODUCTION_TOPIC=s-reminder-prod
BUSINESS_TIMEZONE=Asia/Shanghai
OS_TIMEZONE=Etc/UTC
```

正式 production sender 只拥有：

```text
write-only s-reminder-prod
```

没有 wildcard / staging permission，也没有 public ntfy fallback。

systemd：

```text
ONE_PRODUCTION_SCHEDULER=YES
PERSISTENT_TIMER=true
STAGING_SCHEDULER_ENABLED=NO
```

真实验收包括：

- owner API localhost / public HTTPS PASS；
- production API 确认打开 formal DB，而非 staging DB；
- backup / off-VPS export PASS；
- iPhone 新 production topic 锁屏通知真人 PASS；
- 两条真实 multi-notification fixture 共 8 个节点全部 first-send=1；
- 8 个节点 immediate duplicate 全部 send=0；
- fixture 经正式 owner API 清理；
- original Reminder business scope 保持不变。

# 50. Step 8D｜Mac Remote Authority Cutover CLOSED

Step 8D closeout commit：

```text
342d659f15e72b51236b4a66550f403c4a5989f6
```

正式 Mac Reminder authority 从本地切为：

```text
FORMAL_REMINDER_AUTHORITY=remote
FORMAL_REMINDER_ENDPOINT=https://reminder.ke-den.de
```

正式链路：

```text
Mac GUI owner
→ owner Keychain credential
→ Cloud Reminder API
→ Cloud formal DB

yan / claude-code / clawbot
→ actor-specific credential
→ Cloud Reminder API
→ Cloud formal DB
```

最终边界：

```text
LOCAL_WRITABLE_FALLBACK=NO
OFFLINE_WRITE_QUEUE=NO
LOCAL_MCP_DIRECT_REMINDER_DB_WRITE=NO
AI_CREDENTIAL_BOUNDARY=PASS
CLOUD_ONLY_REMINDER_MUTATION=PASS
```

`RemoteMcpReminderAdapter` 的 `table_title` 补取使用同一个 actor / client 调正式 `get_reminder_table`，隐藏 table 时返回 not_found，不形成第二套权限协议。

正式 App build / codesign PASS，bundle 已声明：

```text
remote
https://reminder.ke-den.de
SNotesReminderHelperRetired=true
SNotesStep07Candidate=false
```

Step 8D 只完成 authority cutover；legacy helper 的永久退役留给 8E。

# 51. Step 8E｜Helper Retirement + §59 32/32 CLOSED

Step 8E closeout commit：

```text
79618fa
```

## 51.1 Legacy Reminder helper 永久退役

最终：

```text
LEGACY_HELPER_PLIST_PRESENT=NO
LEGACY_HELPER_REGISTERED=NO
LEGACY_HELPER_RUNNING=NO
FORMAL_APP_PACKAGES_REMINDER_HELPER=NO
FORMAL_REBUILD_REINSTALL_HELPER_ABSENT=PASS
HELPER_SURVIVES_REBOOT_AS_ABSENT=PASS
HELPER_REBOOT_PERSISTENCE=PASS
HELPER_PERMANENTLY_RETIRED=YES
```

startup self-check 的反向 Gate 也 PASS：

```text
人为放回旧 plist
→ App 明确警告 Legacy local Reminder scheduler detected

删除测试 plist
→ 正常启动无警告
```

该 self-check 只检测，不会 bootstrap / kickstart / 恢复 helper。

`com.snotes.gale-production-pull` 未被修改。

## 51.2 Formal backup restore Gate

production backup 的隔离 copy 真实打开：

```text
quick_check=ok
FK=0
relationships=coherent
Reminder repository/service read path=PASS
```

没有覆盖 formal DB。

## 51.3 #14 Mac-off scheduler｜真人 + 机器 PASS

最终 fixture：

```text
FICTIONAL Step08E Mac-off final
DUE=2026-10-04 10:00 Beijing
```

S 在 Mac 完全关机状态收到 iPhone ntfy。

随后机器证据确认：

```text
PRODUCTION_SCHEDULER_RUN=PASS
FIXTURE_DUE=YES
PRODUCTION_TRANSPORT=PASS
NTFY_CACHE_EXACT_MATCH=1
NOTIFICATION_MARK_WRITTEN_ONCE=YES
SUBSEQUENT_NORMAL_TIMER_DUPLICATE=NO
```

没有重发真人通知。

fixture 最后经正式 owner path 清理。

## 51.4 #17 / #18 / #22｜一次真实 Cloud reboot PASS

只执行 **一次**真实 Cloud reboot。

结果：

```text
CLOUD_REBOOT_COUNT=1
CLOUD_REBOOT_AUTO_RECOVERY=PASS
PRODUCTION_API_AUTO_RECOVERY=PASS
PRODUCTION_SCHEDULER_TIMER_AUTO_RECOVERY=PASS
PRODUCTION_BACKUP_TIMER_AUTO_RECOVERY=PASS
PERSISTENT_TIMER_CATCHUP=PASS
PERSISTENT_TIMER_NO_DUPLICATE=PASS
```

Persistent timer 在 reboot 跨过 trigger 后自动 catch-up 发送一次；下一次正常 timer pass 发送 0，没有重复。

fixture 经正式 owner path 清理。

## 51.5 #13 final scope comparison｜PASS

最终 Mac formal Reminder scope 对 frozen 8B baseline 只读比较：

```text
51 reminders
66 occurrences
87 Mac AI Reminder operations
2 eligible settings
```

Reminder / occurrence / operation / settings SQL values全部一致。

label / table 仅保留此前已经记录的 default id=1 `updated_at` metadata 差异，没有 Reminder business mutation。

## 51.6 §59 final matrix

最终：

```text
REMINDER_ACCEPTANCE_PASS=32
REMINDER_ACCEPTANCE_PENDING=0
REMINDER_ACCEPTANCE_32_OF_32=PASS
STEP08E=PASS
```

不得再从旧报告中把 28/32、#14 pending、#17/#18/#22 pending 等历史中间状态捞回当前状态。

# 52. Step 8 UI navigation regression｜CLOSED

8E 期间发现正式 App startup 的后台 remote Reminder snapshot 会触发：

```text
ReminderCenter.changed
→ _sync_sidebar_reminder_filters()
→ set_reminder_filters()
→ sidebar mode 被切成 reminder
```

因此 App 启动可能从 Notes 被后台 snapshot 抢去 Reminder。

最终只增加最小 navigation guard：

```text
current_mode == "smart_view"
and
current_filter == "reminder"
```

才允许 Reminder filter sync 改变对应 sidebar state。

最终：

```text
SOURCE_STARTUP_STAYS_ON_NOTES=PASS
SOURCE_REMOTE_REFRESH_DOES_NOT_HIJACK_NAVIGATION=PASS
SOURCE_REMINDER_MANUAL_NAVIGATION=PASS

STARTUP_STAYS_ON_NOTES=PASS
REMOTE_REFRESH_DOES_NOT_HIJACK_NAVIGATION=PASS
REMINDER_MANUAL_NAVIGATION=PASS

TARGETED_UI_REMOTE_TESTS=10_PASS
FORMAL_APP_HUMAN_SMOKE=PASS
```

一次旧窗口关闭过程中出现 `_close_pending` 导致 Note click 暂时不响应；干净实例未复现永久 quit failure，因此没有做 speculative close/editor patch。fresh Dock instance 真人三项 smoke PASS 后该旁支停止。

# 53. Step 8 双独立 Post-Vibe｜P0=0 / P1=0

Step 8 完成 32/32 后、merge main 前，做了两轮独立只读 Post-Vibe / 防屎山审计。

共同结论：

```text
P0_COUNT=0
P1_COUNT=0
NO_HIDDEN_PROTOCOL=YES
NO_TEMPORARY_GLUE_ON_MAIN_PATH=YES
SINGLE_SOURCE_OF_TRUTH=PASS
PERMISSION_BOUNDARY=PASS
LEGACY_HELPER_MAIN_PATH=NOT_REACHABLE
READY_TO_CLOSE=YES
```

重点确认：

- GreenCloud formal DB 是唯一 Reminder authority；
- 没有 local writable fallback；
- 没有 second scheduler / staging authority fallback；
- GUI owner credential 与各 MCP actor credential 隔离；
- `table_title` adapter 不形成隐藏权限协议；
- scheduler / send / dedupe 只有一个正式 owner；
- legacy helper 无法从正式 runtime / build 主路径启动。

第二轮审计只做五项高风险路径复核，低风险 UI / 历史施工 / 已有 §59 验收不重复烧额度。

# 54. Final Post-Vibe P2 cleanup｜CLOSED

双审计没有 P0 / P1，只留下两个真实 P2 尾巴。

## 54.1 production staging error-name residue｜CLOSED

旧：

```text
owner save_settings 修改 ntfy_topic
→ 正确 HTTP 400 fail-closed
→ error = staging_topic_required
```

最终改为中性 production/staging 语义：

```text
ntfy_topic_immutable
```

未改变 validation、HTTP status、权限或 production topic locking。

unused `STAGING_TOPIC` runtime constant 同时删除。

## 54.2 obsolete Step 7 candidate｜CLOSED

旧构建产物：

```text
dist-step07-candidate/S-notes.app
```

虽然 UI 标着 staging，但 endpoint 已指向 production，且读取同一个 owner Keychain credential；存在用户误以为“测试环境”实际操作 production 的人因风险。

最终：

```text
STEP07_CANDIDATE_ARTIFACT_REMOVED=PASS
STEP07_CANDIDATE_PROFILE=RETIRED
```

正式 `dist/S-notes.app` 保留。

P2 cleanup commit：

```text
52103062e73a1d5101b0fb537dbc106e43bb9909
```

随后：

```text
FAST_FORWARD_MAIN=PASS
FORMAL_APP_REBUILD_FROM_CLEAN_MAIN=PASS
CODESIGN=PASS
FORMAL_APP_SMOKE=PASS
MAIN_ONLY_PUSH=PASS
```

当时：

```text
MAIN=ORIGIN_MAIN=52103062e73a1d5101b0fb537dbc106e43bb9909
GREENCloud_3_5_B=CLOSED
STEP09_STARTED=NO
```

没有第三轮 Post-Vibe，也没有重跑 32/32。

# 55. 3.5-B Post-Close Reminder Maintenance｜CLOSED

Step 8 正式封口后，回查 Step 7 deferred maintenance，处理到触发节点的真实尾巴。

## 55.1 Step 7 deferred P2-2｜no-occurrence display semantics CLOSED

maintenance commit：

```text
529c0b8fd543ba631fa0b299d61a6d22588f3035
```

根因：

```text
ReminderService._list_item
→ current occurrence 缺失
→ 直接投影 completed
```

最终语义：

```text
raw lifecycle=active + no current occurrence
→ 保持 active / “启用”

真正 whole Reminder completed
→ completed / “已完成”
```

不新增 lifecycle、不改 recurrence、不改 scheduler、不改 wire/schema/permission。

针对性验收：

```text
no-occurrence=4/4
Reminder nearby regression=46/46
snapshot read purity=5/5
reachability helper=1/1
remote GUI projection/regression=8/8
authority resolution=5/5
remote MCP/no local writable fallback=6/6
remote GUI navigation=2/2

TOTAL=77/77_PASS
```

## 55.2 Step 7 deferred P2-3｜dead local path cleanup

结果：

```text
P2_3_DEAD_LOCAL_PATH=NONE_FOUND
REMOVED_PATHS=NONE
```

原因：

- local GUI / MCP / helper 仍被显式 local candidate、开发和测试使用；
- `ReminderService` / HTTP 仍是 Cloud 服务端业务 owner；
- remote controller 仍是正式 remote GUI 所需路径。

因此没有为了“完成 cleanup”硬删仍有真实 caller 的代码。

P2-3 中的“大 owner slimming”部分继续 **DEFERRED**：

> 不因为文件大就拆；以后只有出现真实职责混乱 / 维护需求时再做。不得把行数本身重新升级成 blocker。

## 55.3 P2-2 production runtime rollout

因为 no-occurrence fix 修改正式 Cloud runtime owner，源码 commit 后额外做最小 production rollout。

仅更新：

```text
/srv/snotes/reminder-runtime/.../reminder_service.py
/srv/snotes/reminder-runtime/.../domain/reminders.py
```

只重启：

```text
snotes-reminder-api.service
```

scheduler 为 oneshot，下一次自然 timer run 加载当前 source；没有手工触发 scheduler。

验证：

```text
DEPLOYED_SOURCE_FICTIONAL_DB=4/4_PASS
LOCALHOST_OWNER_SNAPSHOT=200
PUBLIC_OWNER_SNAPSHOT=200
ORDINARY_LIST_READ=PASS

PRODUCTION_API_ACTIVE=YES
PRODUCTION_TIMER_ACTIVE=YES
STAGING_SCHEDULER_ENABLED=NO

PRODUCTION_DB_CHANGED_BY_THIS_TICKET=NO
NTFY_NOTIFICATION_SENT=NO
STEP08_ACCEPTANCE_RERUN=NO
```

maintenance report-only commit：

```text
90cb115f3866759c32987bc636c81edd322ef921
```

# 56. 当前正式 Git / Runtime 基线

从本节开始，旧 §43–§45 的 Step 7 / pre-Step8 Git 状态全部只视为历史。

当前正式 Git：

```text
MAIN=90cb115f3866759c32987bc636c81edd322ef921
ORIGIN_MAIN=90cb115f3866759c32987bc636c81edd322ef921
WORKTREE=CLEAN
PUSH=PASS
```

正式 Reminder authority：

```text
AUTHORITY=GreenCloud
FORMAL_DB=/srv/snotes/data/reminder-production.db
PUBLIC_ENDPOINT=https://reminder.ke-den.de
NTFY_ENDPOINT=https://ntfy.ke-den.de
NTFY_TOPIC=s-reminder-prod
BUSINESS_TIMEZONE=Asia/Shanghai
OS_TIMEZONE=Etc/UTC
```

正式 scheduler：

```text
PRODUCTION_SCHEDULER=ACTIVE
PERSISTENT=true
STAGING_SCHEDULER=DISABLED
```

Mac：

```text
FORMAL_REMINDER_AUTHORITY=remote
LOCAL_WRITABLE_FALLBACK=NO
OFFLINE_WRITE_QUEUE=NO
LEGACY_REMINDER_HELPER=RETIRED
```

AI / MCP：

```text
OWNER_CREDENTIAL_BOUNDARY=PASS
ACTOR_CREDENTIAL_BOUNDARY=PASS
LOCAL_MCP_DIRECT_REMINDER_DB_WRITE=NO
AI_PERMISSION_EXPANSION=NO
```

当前主线：

```text
GREENCloud_3_5_A=CLOSED
GREENCloud_3_5_B=CLOSED
POST_CLOSE_REMINDER_MAINTENANCE=CLOSED
STEP09_STARTED=NO
```

# 57. Deferred Maintenance 总账｜不要重复施工

## 57.1 Step 7 Reminder deferred P2 最新去向

旧 §41 的 4 项现更新为：

```text
P2-1 ntfy/topic/config ownership
= CLOSED in Step 8

P2-2 no-occurrence display semantics
= CLOSED @ 529c0b8
= production rollout PASS

P2-3 dead local path cleanup
= NONE_FOUND / CLOSED

P2-3 owner slimming half
= DEFERRED
= 非 blocker
= 不因大文件行数主动拆

P2-4 Endpoint/sidebar max-width UI tail
= DEFERRED
= 约 17px 最大拖宽差异
= 仅 UI polish
```

因此今后不得再按旧 §41 说“4 个 Reminder P2 都 open”。

## 57.2 3.5-A 仍保留的 3 个 NON-BLOCKING maintenance notes

此前 §17 的 3 个 3.5-A Post-Vibe P2 没有因 Step 8 自动消失，继续原口径：

### actor mapping duplication

actor 名单 / UID / path 在少数 Python / shell owner 有重复，但职责不同、漂移 fail closed，OS group permission 是最终权限边界。

处理原则：

```text
以后真正修改相关 owner
→ 可顺手加一致性测试
```

不要为了“去重复”单独建设全局配置抽象。

### deployment record small drift

线上 source manifest / rollback pre-closeout / INSTALL 的施工记录存在小偏差，但正式 runtime 可由 repo 解释，不是 production snowflake。

处理原则：

```text
下一次真正升级 Cloud runtime 时
→ 顺带补齐升级记录
```

不单独启动 deployment framework 重做。

### Cloud read 不重复复核 target_actor

receiver 激活前已经校验 `target_actor`，OS 文件权限继续提供 actor 隔离；Cloud reader 固定 actor path 只读。

额外 open-time `target_actor` check 仍属于 defense-in-depth，不是当前真实缺口。

以上三项全部：

```text
NON_BLOCKING
DO_NOT_PULL_INTO_STEP09_UNLESS_DIRECTLY_TOUCHED
```

# 58. 下一步｜GreenCloud 3.5-C / Step 9

现在正式下一阶段：

```text
Step 9 — AI permission isolation
```

开工基线：

```text
BASELINE=90cb115f3866759c32987bc636c81edd322ef921
MAIN=ORIGIN_MAIN
WORKTREE=CLEAN
```

进入 Step 9 时：

- 不重做 3.5-A；
- 不重做 Step 6 / 7 / 8；
- 不重跑 Reminder 32/32；
- 不重新审 helper retirement；
- 不把 P2-4 sidebar 17px polish 拉进权限施工；
- 不为了 owner slimming 先拆 Reminder 大文件；
- 保持 GreenCloud Reminder 为唯一正式 authority；
- 保持 Mac Notes authority 不变；
- 只按既有 3.5 FINAL 进入 AI permission isolation。

# 59. 新窗口接班短句

> G，继续 S-notes GreenCloud 3.5-C / Step 9。3.5-A Notes Cloud Read 已 CLOSED；3.5-B Reminder Cloud Authority 也已全部 CLOSED。Step 8A migration rehearsal、8B formal migration、8C Cloud production activation、8D Mac remote authority cutover、8E helper permanent retirement 全部 PASS；§59 Reminder acceptance 32/32 PASS。#14 已真人证明 Mac 完全关机仍收到 Cloud production scheduler 通知，并有 timer / ntfy cache / mark / dedupe 机器证据；只做过一次真实 Cloud reboot，Persistent catch-up、no duplicate、API/scheduler/backup auto recovery 全 PASS。Step 8 双独立 Post-Vibe 均 P0=0/P1=0；production staging error-name 与旧 Step7 candidate artifact/profile 已清理。随后 post-close maintenance 修掉 no-occurrence display semantics，dead local path 检查结果 NONE_FOUND，Cloud production runtime 已最小 rollout 并 smoke PASS。当前 `main == origin/main == 90cb115f3866759c32987bc636c81edd322ef921`，worktree clean；GreenCloud formal DB `/srv/snotes/data/reminder-production.db` 是唯一 Reminder authority，Mac formal Reminder authority=remote，local writable fallback=NO，legacy helper 已永久退役。Reminder 剩余只保留 P2-4 sidebar 约 17px UI polish，以及“owner slimming 等真实需要再做”；3.5-A 另有 3 个 non-blocking maintenance notes，不主动拉进 Step 9。下一步只进入 Step 9 AI permission isolation，不重做 3.5-B。

---

# 60. GreenCloud 3.5-C / Step 9｜AI permission isolation 正式 CLOSED

**补记日期：2026-10-03**  
**状态：Step 9 已完成部署、权限验收、branch commit 与 ff-only main Git closeout；3.5-C 正式 CLOSED。Step 10 尚未开始。**

上一节 §58–§59 记录的：

```text
Step 9 — AI permission isolation = NEXT
BASELINE=90cb115f3866759c32987bc636c81edd322ef921
MAIN=ORIGIN_MAIN
WORKTREE=CLEAN
```

从本节开始更新为：

```text
STEP09=PASS
GREENCloud_3_5_C=CLOSED
STEP09_GIT_CLOSEOUT=PASS
STEP10_STARTED=NO
```

Step 9 construction branch / commit：

```text
BRANCH=codex/gc35-step09-ai-permission-isolation
COMMIT=bc35692507ae557a42dd0e83d212bb9c138f75cd
```

详细封口证据另见：

```text
docs/gc35-step09-report.md
```

本进度书只保留接班所需的最终边界与状态，不重复抄录完整验收报告。

# 61. Step 9 最终权限矩阵｜PASS

GreenCloud 两个既有 AI OS identity 已按 3.5-C 门禁要求真正隔离：

```text
claude UID 2001 / actor=claude-code
→ 只读自己的 mirror/claude-code
→ 只使用自己的 Reminder credential/API 身份
→ 不可读 clawbot mirror / credential

openclaw UID 2002 / actor=clawbot
→ 只读自己的 mirror/clawbot
→ 只使用自己的 Reminder credential/API 身份
→ 不可读 claude-code mirror / credential

两者
→ 不可直接读写 Reminder production/staging/backup 等业务 DB
→ 不可直接访问 /srv/snotes/data/* 受保护数据
→ 不可访问未来 outbox DB 文件
→ 不可使用 owner-only Reminder 命令
→ 不可 delete Note
→ 不可修改 AI visibility / actor targets
→ 不可读取 AI-invisible / other-actor / Gale-only Note
```

最终结果：

```text
PERMISSION_MATRIX=PASS
CREDENTIAL_ISOLATION=PASS
NOTES_ACTOR_ISOLATION=PASS
REMINDER_ACTOR_ISOLATION=PASS
DIRECT_DB_ACCESS=DENIED
CROSS_ACTOR_NEGATIVE_TESTS=PASS
```

现有 mirror 目录 / group 权限以及 `/srv/snotes/data` 的 `snotes:snotes 0700` 边界本来已满足架构要求，本步复核后无需改动；未发现额外 ACL grant，两个 actor 也没有 cross-actor/service group membership 或 sudo 权限。

# 62. Actor credential / launch entry / allowlist｜CLOSED

本步新增两份独立 actor Reminder credential：

```text
/srv/snotes/actor-credentials/<actor>/reminder-token
```

最终权限：

```text
credential file = corresponding actor UID/GID + 0400
actor parent     = root:actor-group 0750
shared parent    = root:root 0711
```

actor 用户不能替换 credential 目录或文件；secret 没有打印、回传 Mac 或进入 Git。

Notes Cloud read 入口继续只暴露：

```text
search_notes
get_note
get_note_markdown
```

Notes entry 保持既有 effective-UID mapping / environment clearing，并拒绝额外参数。

新增 `snotes-reminder-actor` 入口：

```text
EUID → 固定 actor
EUID → 固定 token path
endpoint → 固定 127.0.0.1:8766
request → 单个 bounded JSON request
allowlist → 复用既有 13-command ACTOR_COMMANDS
```

不存在 actor、endpoint 或 credential-path override。

现有 Reminder server package 当时缺少 transport client，因此 Step 9 只把仓库中既有 client 原样打包到 root-owned：

```text
/srv/snotes/ai-access
```

没有新造 client 协议、依赖、service 或第二套 authority。

`/srv/snotes/INSTALL.md` 已补：

- Step 9 permission matrix；
- actor launch entries；
- 可重复执行的 isolation verification；
- future outbox filesystem boundary。

未来 outbox 仍要求位于既有受保护 data boundary 内，由 service owner 持有、AI 只能通过未来 authenticated localhost service 访问；本步 **没有创建 outbox path / DB / service / schema**。

# 63. Step 9 验收｜61 local + 199 live + fictional Notes PASS

本地针对性验证：

```text
61_LOCAL_PASS
```

其中包括 Step 9 actor entry 新测试与直接相关的 Notes mirror / Cloud read / Reminder API / MCP adapter / HTTP client 回归；没有重跑 Reminder 32/32，也没有为了本步跑全工程 suite。

GreenCloud 真实 UID 越权脚本最终：

```text
PASS IDENTITY_AND_METADATA 1
PASS FILESYSTEM_ISOLATION 118
PASS REMINDER_ACTOR_WRAPPER 2
PASS REMINDER_API_BOUNDARY 66
PASS NOTES_MCP_BOUNDARY 12
PASS STEP09_ACTOR_ISOLATION 199
```

两个真实 Cloud UID 同时通过 fictional Notes fixture：

```text
own/common read = PASS
hidden read = DENIED
other actor read = DENIED
Gale-only read = DENIED
hidden search = EMPTY
delete = UNAVAILABLE
```

Reminder 边界同时验证：

- actor 自己 credential 的允许操作可用；
- hidden table/item guess → not_found；
- owner delete / owner-only command 被拒绝；
- visibility / identity / targets injection 被拒绝；
- cross-actor credential / mirror 访问失败；
- direct business / backup DB access 失败。

本步没有读取真实 Note 正文做验收，也没有做真实 credential 内容比较；所有 diagnostic comparison 都避开 secret 内容。

最终：

```text
TESTS=61_LOCAL_PASS / 199_LIVE_PASS / BOTH_UID_FICTIONAL_NOTES_PASS
DEPLOY=PASS
```

# 64. Step 9 明确未触碰的边界

本轮保持：

```text
CLAUDE_CODE_RUNTIME_TOUCHED=NO
OPENCLAW_RUNTIME_TOUCHED=NO
EGRESS_TOUCHED=NO
SCHEMA_TOUCHED=NO
OUTBOX_STARTED=NO
```

同时：

- 不安装 / 不配置 Claude Code；
- 不安装、迁移、升级 OpenClaw runtime / Gateway；
- 不改 Clawde prompt / plugin / profile / gateway；
- 不改 LAX / DE egress；
- 不改 Notes publisher / mirror authority；
- 不改 Reminder authority；
- 不重做 3.5-A / Step 6 / 7 / 8；
- 不做 Reminder owner slimming；
- 不处理 sidebar 约 17px UI polish；
- 不启动 Step 10。

API 进程未因本步重启；scheduler / backup timer 保持原正式状态。

本步唯一新的 out-of-scope finding：

```text
sudo: unable to resolve host optimal-living ...
```

这是既有 hostname resolution warning。actor sudo 请求仍按预期被拒绝；该 warning 与 S-notes Step 9 权限链无关，本步未修，**不阻塞 Step 10**。

# 65. Step 9 Git closeout｜PASS

Step 9 自动施工完成时 initially：

```text
BASELINE_MAIN=90cb115f3866759c32987bc636c81edd322ef921
TASK_BRANCH_HEAD=bc35692507ae557a42dd0e83d212bb9c138f75cd
DEPLOY=PASS
PUSH=NO
```

随后只做 Git 记账封口，没有改文件、重新部署、重新测试或进入 Step 10。

最终：

```text
STEP09_GIT_CLOSEOUT=PASS
MAIN=bc35692507ae557a42dd0e83d212bb9c138f75cd
HEAD=bc35692507ae557a42dd0e83d212bb9c138f75cd
ORIGIN_MAIN=90cb115f3866759c32987bc636c81edd322ef921
WORKTREE=CLEAN
MERGE_MODE=FAST_FORWARD_ONLY
PUSH=NO
STEP10_STARTED=NO
```

因此必须区分：

```text
LOCAL_MAIN_HAS_STEP09=YES
ORIGIN_MAIN_HAS_STEP09=NO
```

这是本轮刻意保留的状态，不是漏步骤。旧 §56 的：

```text
MAIN=ORIGIN_MAIN=90cb115...
```

从现在起仅代表 Step 9 开工前基线，不得再作为当前 Git 状态使用。

# 66. 当前正式架构状态｜3.5-C CLOSED

```text
GREENCloud_3_5_A=CLOSED
GREENCloud_3_5_B=CLOSED
GREENCloud_3_5_C=CLOSED

NOTES_AUTHORITY=Mac
REMINDER_AUTHORITY=GreenCloud
FORMAL_REMINDER_DB=/srv/snotes/data/reminder-production.db
FORMAL_REMINDER_AUTHORITY_ON_MAC=remote
LOCAL_WRITABLE_FALLBACK=NO
LEGACY_REMINDER_HELPER=RETIRED

CLAUDE_UID_2001_SNOTES_BOUNDARY=PASS
OPENCLAW_UID_2002_SNOTES_BOUNDARY=PASS
CROSS_ACTOR_ACCESS=DENIED
DIRECT_DB_ACCESS_FOR_AI_UIDS=DENIED

MAIN=bc35692507ae557a42dd0e83d212bb9c138f75cd
ORIGIN_MAIN=90cb115f3866759c32987bc636c81edd322ef921
WORKTREE=CLEAN
PUSH=NO
```

3.5-A 的 3 个 non-blocking maintenance notes、Reminder owner slimming 与 sidebar 约 17px UI polish 继续按此前总账处理；Step 9 没有把它们变成 blocker，也没有顺手施工。

# 67. 下一步｜GreenCloud 3.5-D / Step 10

当前唯一主线前进到：

```text
Step 10 — GreenCloud outbox + proposal
```

Step 9 已经为 Step 10 留好 filesystem boundary：未来 outbox 进入既有受保护 data boundary，由 service owner 持有，AI actor 不直接碰 DB 文件，只通过未来 authenticated localhost service 访问。

进入 Step 10 时继续保持：

- Mac 仍是 Notes 唯一正式 authority；
- GreenCloud 仍是 Reminder 唯一正式 authority；
- Step 9 的 actor credential / UID / filesystem boundary 不扩权；
- 不因为 Step 10 安装 Claude Code 或 OpenClaw runtime；
- 不处理 Clawde prompt slimming；
- 不重做 Step 9 isolation verification，除非 Step 10 直接修改了相关边界；
- 不从旧章节把已经 CLOSED 的 Step 6 / 7 / 8 或 3.5-A 拉回主线。

当前：

```text
READY_FOR_STEP10=YES
STEP10_STARTED=NO
NEW_STEP10_BLOCKER=NONE
```

# 68. 新窗口接班短句

> G，继续 S-notes GreenCloud 3.5-D / Step 10。3.5-A Notes Cloud Read、3.5-B Reminder Cloud Authority、3.5-C / Step 9 AI permission isolation 均已正式 CLOSED。Step 9 branch=`codex/gc35-step09-ai-permission-isolation`，commit=`bc35692507ae557a42dd0e83d212bb9c138f75cd`；GreenCloud 已完成 claude UID 2001 / actor=claude-code 与 openclaw UID 2002 / actor=clawbot 的 filesystem、credential、Notes read 和 Reminder API 隔离，两份 actor credential 为各自 owner 0400；Notes MCP 仍只有 `search_notes/get_note/get_note_markdown`，Reminder actor wrapper 按 EUID 固定 actor/token/localhost endpoint 并复用既有 13-command allowlist。61 local targeted tests PASS，真实 Cloud UID isolation script 199/199 PASS，两个 UID fictional Notes fixture PASS；cross-actor mirror/credential、直接 DB、owner-only、visibility/targets、hidden/other-actor/Gale-only 均按预期拒绝。Claude Code/OpenClaw runtime、Clawde prompt/gateway、egress、schema、Notes/Reminder authority 均未触碰，outbox 尚未创建。Step 9 已 ff-only 合入本地 main：`main == HEAD == bc35692507ae557a42dd0e83d212bb9c138f75cd`，worktree clean；`origin/main` 仍为 `90cb115f3866759c32987bc636c81edd322ef921`，`PUSH=NO`，这是刻意状态。唯一 out-of-scope finding 是既有 `sudo` hostname resolution warning，不阻塞 Step 10。下一步只按既有 3.5 FINAL 进入 Step 10 GreenCloud outbox + proposal，不安装 Claude Code/OpenClaw runtime，不重做 Step 9，不把 deferred maintenance 拉进主线。

---

# 69. GreenCloud 3.5-D / Step 10｜Notes Outbox + Proposal CLOSED

**补记日期：2026-10-04**  
**状态：Step 10 10A / 10B / 10C 已完成、部署并验收；随后进入 Step 11。**

Step 10 construction branch：

```text
codex/gc35-step10-greencloud-outbox-proposal
```

实现 commit：

```text
8e352962600029799655768e8dbba2f78667b1ac
```

Step 10 closeout / report commit：

```text
a4ac06d
```

本步没有另造第二套 outbox；继续复用既有 `gale_submission_outbox.py`，只有 GreenCloud 显式 generalized path 使用 schema v3，Oracle Gale production 保持 schema v2。

GreenCloud 正式 outbox：

```text
/srv/snotes/data/outbox.db
schema=3
owner=snotes:snotes
mode=0600
journal_mode=WAL
```

新增并正式部署：

```text
snotes-notes-outbox.service
listen=127.0.0.1:8767
```

Cloud actor stdio MCP 在原三个 Notes read tools 基础上新增：

```text
create_note
append_note
edit_note
get_operation_status
```

当前 Cloud write allowlist：

```text
create
append
edit
```

明确仍拒绝：

```text
prepend
update_note_metadata
delete
```

actor 身份由各自 credential / UID 固定，客户端不能自报 actor、visibility 或 targets。AI actor 不能直接打开 outbox DB；只经 authenticated localhost service 访问。

Step 10 targeted / deploy / Cloud proposal / runtime tests 全部 PASS；生产 outbox 在验收后回到 0 proposals，没有留下测试投稿。Oracle production 没有迁移、没有改 schema、没有改 runtime。

Step 10 正式留下的后续边界：

> `prepend` / `update_note_metadata` 以后单独扩展；复用现有 payload / version / idempotency / lifecycle，不重新设计 outbox。Delete 继续不做。

# 70. GreenCloud 3.5-D / Step 11｜Mac Review + Receipt + Mirror Refresh CLOSED

Step 11 construction branch：

```text
codex/gc35-step11-mac-receipt-mirror-refresh
```

最终 commit：

```text
5399c9ee3eec3d9457f156f0bcc7e0e52fee8bec
```

最终状态：

```text
STEP11=PASS
S_E2E=PASS
3.5-D=CLOSED
GREENCLOUD_3.5=COMPLETE
```

Step 11 复用同一张 `ai_receipt_inbox` 与既有正式 Notes acceptance transaction，没有为 GreenCloud 再造第二套 review lifecycle。

GreenCloud proposal 导入 Mac 后保留：

```text
source
actor
original request_id
expected_version
operation
proposal / diff
```

Mac 本地 operation identity 按 source / actor 做 namespace 隔离；terminal receipt 回传仍使用原始 Cloud request ID。

AI Center 最终能够区分并显示：

```text
Oracle Gale
GreenCloud
actor
operation
proposal / diff
status
```

Cloud proposal 接受前重新检查：

- 当前 Note / 删除状态；
- actor 权限与可见性；
- expected_version；
- request 是否已经执行；
- edit 的 exact unique match；
- canonicalize 是否成功。

失败时正文零写入，并记录 rejected / conflict；没有 auto merge，也没有 batch accept。

accepted Cloud proposal 的后续顺序固定为：

```text
Mac Notes accept commit
→ publish_actor(origin_actor)
→ terminal receipt to GreenCloud
```

publish / receipt 失败不回滚已经正式接受的 Note；processed inbox row 保存待重试 transport 状态。rejected / conflict 不触发正文镜像发布。

Step 11 新增自动 tests：

```text
32/32 PASS
```

直接相关兼容回归：

```text
Oracle Gale=23/23 PASS
Gale AI Center wiring=3/3 PASS
Step10 proposal/status/allowlist=8/8 PASS
actor publisher=5/5 PASS
git diff --check=PASS
```

## 70.1 真人 E2E｜PASS

S 完成正式 App 真人 Gate：

1. Mac pull 暂停 / App 关闭时，以真实 claude-code MCP queue create / append / edit；
2. 恢复正式 helper / App，三条均进入 AI Center，source / actor / operation 正确；
3. S 接受 CREATE + APPEND，拒绝 EDIT；
4. Cloud operation status 对应 accepted / accepted / rejected；下一次 Cloud read 已看到 accepted 后的新内容；
5. Oracle Gale production owner 另投一条虚构 proposal，AI Center 正确显示 Oracle Gale / gale，S 接受成功；
6. 新建 Cloud EDIT 后，S 先在 App 手工写入 `S HUMAN CHANGE` 并保存，再接受旧 proposal，得到 conflict；人工文字和原内容保留，冲突候选未写入。

最终确认：

```text
MAC_BODY_AUTHORITY=PASS
STALE_CONFLICT_ZERO_WRITE=PASS
SOURCE_SCOPED_RECEIPT=PASS
ACCEPTED_MIRROR_REFRESH=PASS
```

## 70.2 Step 11 cleanup / runtime

真人 Gate 后：

- 四篇虚构 Note 与空测试目录移入应用回收站；没有清空回收站或永久删除真实 Note；
- 对应 GreenCloud / Gale 测试镜像撤下；
- GreenCloud 本轮 4 条 fixture queue row 清理，production outbox 回到 0；
- Oracle 仅移除本轮 1 条 ACK 虚构行，其余原记录保留；
- 没有 Cloud runtime redeploy、Oracle runtime change 或 Oracle schema migration。

当时为验收 / rollback 明确保留：

```text
/root/greencloud-backups/step10-20261004T065916Z-45x1t_h5
/root/greencloud-backups/step10-record-20261004T070417Z-8i99u80v
~/Library/Application Support/S-notes/backups/gc35-step11-helper-084f67cf/
/srv/snotes/data/step11-fixture-084f67cf.json
/var/lib/snotes-gale-r1b-production/step11-fixture-084f67cf.json
```

这些从本节开始属于 **post-close cleanup ledger 的待判定 leftovers**，不再自动视为永久 KEEP。

# 71. GreenCloud 3.5 最终 Git 封口｜COMPLETE

Step 11 完成后执行 ff-only merge + push。

最终：

```text
MAIN=5399c9ee3eec3d9457f156f0bcc7e0e52fee8bec
ORIGIN_MAIN=5399c9ee3eec3d9457f156f0bcc7e0e52fee8bec
HEAD=5399c9ee3eec3d9457f156f0bcc7e0e52fee8bec
WORKTREE=CLEAN
PUSH=PASS
GREENCLOUD_3.5=COMPLETE
```

最近三层 GreenCloud 3.5-D commit：

```text
5399c9e Complete GreenCloud Mac review, receipts and mirror refresh for Step 11
a4ac06d Record Step 10 deployment and verification closeout
8e35296 Implement GreenCloud Notes outbox proposals for Step 10
```

从本节开始：

```text
3.5-A=CLOSED
3.5-B=CLOSED
3.5-C=CLOSED
3.5-D=CLOSED
11_STEP_ROADMAP=COMPLETE
GREENCLOUD_3.5=COMPLETE
```

不得再从 §67–§68 或更早章节把 `STEP10_STARTED=NO`、`Step 10 NEXT` 等历史状态捞回当前状态。

# 72. 四个本地 Agent S-notes MCP 接线修复｜CLOSED

**补记日期：2026-10-04**  
**触发原因：GreenCloud 3.5 完成后发现 Antigravity 的 S-notes MCP 报错；统一核对 Codex / Claude Code / Antigravity / Clawde(OpenClaw) 四个本地客户端。Ke 暂缓。**

当前正式 local MCP 仍统一使用 packaged production source：

```text
/Users/s/Documents/项目/S-notes/dist/S-notes.app
```

正式 launcher 位于 App bundle `Contents/Resources/bin/`；四个本地 Agent 均不回退 Git checkout / `.venv` development launcher。

## 72.1 根因｜Remote Reminder credential eager bootstrap

真实配置检查确认：

```text
Codex       → packaged launcher, actor=yan
Claude Code → packaged launcher, actor=claude-code
Antigravity → packaged launcher, actor=antigravity
OpenClaw    → packaged launcher, actor=clawbot
```

Antigravity 的实际错误发生于 MCP initialize 前：MCP bootstrap 提前构造 Remote Reminder credential provider，而当时 Mac provider 只认识 `yan` / `claude-code`。

因此：

```text
antigravity / clawbot
→ 没有 Mac Remote Reminder credential
→ bootstrap 提前校验
→ ValueError
→ 整个 MCP 在 initialize 前退出
→ Notes discovery 也一起失败
```

这不是 Notes 本身坏掉，也不是 packaged launcher 路径错误。

## 72.2 Startup fix｜CLOSED

施工 branch：

```text
codex/local-mcp-reminder-startup
```

startup fix commit：

```text
9e2a12d
```

修复语义：

```text
MCP initialize / Notes discovery / Notes read
→ 不提前要求 Reminder credential

真正调用 Reminder
→ 才解析并校验对应 actor 的 Remote Reminder credential
```

没有：

- 借用其他 actor credential；
- 恢复 local Reminder DB；
- 恢复 local writable fallback；
- 扩大 Reminder 权限。

直接相关 tests：

```text
16 PASS
```

同时完成两个本地配置小修：

- Codex 统一改用通用 packaged launcher，保留 `actor=yan`；
- OpenClaw 删除多余的 repo checkout `cwd`。

Claude Code 配置本身已经正确，无需为“形式统一”修改。

## 72.3 Remote Reminder 多凭据兼容｜CLOSED

startup fix 后，Notes 已恢复，但 Antigravity / local Clawbot 的 Reminder 仍缺独立 Mac credential。

正式 loader 原来只有“一 actor 一 token”。直接给 Mac Clawbot 配置会与 Cloud UID 2002 正在使用的 clawbot token 冲突，因此没有轮换、替换或复制原 Cloud credential。

采用最小兼容扩展：

```text
local_actor_credentials
```

语义：

```text
同一 actor
→ 保留既有 primary / Cloud credential
→ 可额外拥有独立 local client credential
→ 两者认证后仍归属同一个 actor
→ 使用同一 permission profile
```

multi-credential commit：

```text
04ec7a364af7ed3d3970485453a676e2adc712bb
```

最终：

```text
Cloud clawbot credential → 保持不变
Mac clawbot credential   → 独立
Antigravity actor        → 新增正式 actor=antigravity
Antigravity credential   → 独立 local credential
Cross-actor reuse        → NO
```

Antigravity 使用普通 AI Reminder permission profile；没有 owner-only command、DB access、credential management、额外 delete 权限或 Notes visibility / target 扩权。

本轮只重启 Reminder API；没有改 Reminder DB、scheduler、ntfy、Notes outbox 或 Oracle。

定向验证：

```text
37 PASS
AUTH_NEGATIVE_CHECKS=PASS
LOCAL_REMINDER_FALLBACK=NO
```

## 72.4 四客户端最终 Gate｜PASS

最终：

```text
YAN_REMINDER=PASS
CLAUDE_CODE_REMINDER=PASS
CLAWBOT_REMINDER=PASS
ANTIGRAVITY_REMINDER=PASS
```

Antigravity 真人验收：

```text
23 tools enabled
search_notes=PASS
list_reminder_tables=PASS
list_reminder_items=PASS
Dock 新增 MCP/Python 图标=NO
```

Notes 与 Remote Reminder 均恢复正常。

Clawbot / Claude Code / Codex 同时有隔离或原生客户端只读验证；旧 Codex session 需要新会话重载属于 session config cache，不是接线故障。

Ke / ke-den 明确保持：

```text
KE_LOCAL_SNOTES_MCP=DEFERRED
REASON=ke-den likely moves to cloud
```

# 73. 四本地 Agent 接线 Git / App 正式封口｜PASS

最终 ff-only merge + push：

```text
MAIN=04ec7a364af7ed3d3970485453a676e2adc712bb
ORIGIN_MAIN=04ec7a364af7ed3d3970485453a676e2adc712bb
HEAD=04ec7a364af7ed3d3970485453a676e2adc712bb
WORKTREE=CLEAN
FF_ONLY_MERGE=PASS
PUSH=PASS
```

正式 App：

```text
FORMAL_APP_BUILD=PASS
CODESIGN=PASS
FORMAL_SOURCE=/Users/s/Documents/项目/S-notes/dist/S-notes.app
```

最终边界：

```text
LOCAL_REMINDER_FALLBACK=NOT_REINTRODUCED
CLOUD_CLAWBOT_CREDENTIAL_UNCHANGED=YES
LOCAL_CLAWBOT_CREDENTIAL=INDEPENDENT
ANTIGRAVITY_ACTOR=antigravity
CROSS_ACTOR_CREDENTIAL_REUSE=NO
```

从本节开始，任何“Antigravity S-notes MCP error”“Antigravity / Clawbot Reminder controlled failure”“四客户端接线待修”的旧记录都只视为历史阶段状态，不得重新拉回待办。

# 74. 当前唯一正式基线｜2026-10-04

```text
MAIN=04ec7a364af7ed3d3970485453a676e2adc712bb
ORIGIN_MAIN=04ec7a364af7ed3d3970485453a676e2adc712bb
HEAD=04ec7a364af7ed3d3970485453a676e2adc712bb
WORKTREE=CLEAN
PUSH=PASS

GREENCloud_3_5_A=CLOSED
GREENCloud_3_5_B=CLOSED
GREENCloud_3_5_C=CLOSED
GREENCloud_3_5_D=CLOSED
GREENCLOUD_3.5=COMPLETE
11_STEP_ROADMAP=COMPLETE

NOTES_AUTHORITY=Mac
REMINDER_AUTHORITY=GreenCloud
LOCAL_WRITABLE_FALLBACK=NO
LEGACY_REMINDER_HELPER=RETIRED

CODEX_LOCAL_MCP=PASS
CLAUDE_CODE_LOCAL_MCP=PASS
ANTIGRAVITY_LOCAL_MCP=PASS
CLAWDE_OPENCLAW_LOCAL_MCP=PASS
KE_LOCAL_SNOTES_MCP=DEFERRED
```

这是今后所有 S-notes 施工的唯一当前基线。旧章节中不同 commit、`PUSH=NO`、Step N NEXT / PENDING 等只表示当时的施工阶段，不得覆盖本节。

# 75. GreenCloud 3.5 Post-Close Cleanup / Maintenance Ledger｜NEXT

GreenCloud 3.5 与四本地 Agent 接线都已经正式 CLOSED。下一步不立即进入新功能，先做一张**很小的竣工后 cleanup / maintenance ledger**。

目标是把已经完成主线后仍挂账的 maintenance / rollback /旧审计项重新按当前 `04ec7a3` 基线判定一次，形成干净的现行总账。

## 75.1 真正仍活着的 non-blocking maintenance

### `ai_call_logs` retention

此前已确认：

```text
SAFE_TO_APPLY_RETENTION=YES
RETENTION_IMPLEMENTED=NO
CURRENT_BLOCKER=NO
```

它可以以后单独做小维护票；不是当前 blocker。

### Reminder owner slimming

```text
DEFERRED
NON_BLOCKING
```

不因为文件大就拆；只有以后出现真实职责混乱 / 维护需求时再做。

### Reminder sidebar 最大拖宽约 17px

```text
DEFERRED
UI_POLISH_ONLY
```

不影响默认布局或业务功能。

### actor mapping duplication

少数 Python / shell owner 仍有 actor / UID / path 映射重复。

原则：

```text
以后真正修改相关 owner
→ 顺手加一致性测试
```

不要为“去重复”单独建设全局配置抽象。

## 75.2 应重新核对后销账 / 定性的旧 maintenance

### deployment record small drift

此前定义为：下一次真正升级 Cloud runtime 时顺带补齐。

Step 10 已经实际升级 Cloud runtime，并更新：

```text
/srv/snotes/INSTALL.md
source manifest
deployment record
```

因此下一张 cleanup ticket 只需**只读核对当前三者是否已经一致**：

```text
一致 → CLOSED
仍有真实 drift → 只修实际记录差异
```

不要建设新的 deployment framework。

### Cloud read open-time `target_actor` recheck

此前已确认是 defense-in-depth，不是实际权限缺口。

当前默认：

```text
NO_CONSTRUCTION_REQUIRED
```

除非新的只读核对证明现实边界已经改变，否则不施工。

## 75.3 Construction / rollback leftovers

下一张 cleanup ticket 只读确认以下保留物是否仍有恢复价值，再做 DELETE / ARCHIVE 判断：

```text
/root/greencloud-backups/step10-20261004T065916Z-45x1t_h5
/root/greencloud-backups/step10-record-20261004T070417Z-8i99u80v
~/Library/Application Support/S-notes/backups/gc35-step11-helper-084f67cf/
/srv/snotes/data/step11-fixture-084f67cf.json
/var/lib/snotes-gale-r1b-production/step11-fixture-084f67cf.json
```

优先倾向：

- Step 11 两个只含虚构行的 fixture rollback：竣工后优先删除；
- Step 10 runtime rollback 与旧 helper backup：先确认当前 production 已稳定且现有正式源可恢复，再决定删除或明确 ARCHIVE 理由 / 生命周期。

不要清空应用回收站，不删除真实 Note / Reminder data。

## 75.4 旧 audit / dead code

9/24 架构健康审计中的 legacy dead code / owner / 分叉结论不能直接当今天的删除名单。

原因：其后 acceptance repair、runtime source isolation、GreenCloud 3.5、Remote Reminder cutover、Step 10/11 与四客户端接线都改变了 reachability。

下一张 cleanup ticket 如检查 dead code，只能基于当前：

```text
04ec7a364af7ed3d3970485453a676e2adc712bb
```

做只读 reachability 判断。

Reminder dead local path 此前已经：

```text
NONE_FOUND / CLOSED
```

不得为了“清理代码”硬删仍有真实 caller 的路径。

## 75.5 GreenCloud hostname warning

仍有既有小系统卫生项：

```text
sudo: unable to resolve host optimal-living ...
```

与 S-notes 权限链无关，不是 blocker。cleanup ledger 可记录后续顺手处理，但不要把它扩大成基础设施审计。

# 76. Cleanup 之后的新功能｜Cloud prepend + update_note_metadata

这不是 P2，是 GreenCloud 3.5 完成后明确保留的下一张**独立功能票**。

目标：

```text
Cloud prepend
Cloud update_note_metadata
```

复用已完成的：

```text
outbox v3
actor identity
expected_version
idempotency
proposal lifecycle
Mac AI Center review
receipt
mirror refresh
```

`update_note_metadata` 继续使用本地既定字段 allowlist，不开放 AI visibility / targets。

Delete 仍不做。

顺序固定：

```text
1. GreenCloud 3.5 post-close cleanup / maintenance ledger
2. Cloud prepend + update_note_metadata
3. 其他真正被触发的 deferred maintenance
```

# 77. 新窗口接班短句｜当前唯一版本

> G，继续 S-notes。当前正式基线已经不是旧 §68 的 Step 10 NEXT：GreenCloud 3.5-A/B/C/D 与 11 步施工全部 COMPLETE，Step 10 `8e35296` / closeout `a4ac06d`、Step 11 `5399c9e` 已完成并 ff-only 推到 main；随后四个本地 Agent 的 S-notes MCP 接线也已正式修复并封口。startup lazy Reminder credential fix=`9e2a12d`，Remote Reminder multi-credential fix=`04ec7a3`；Codex/yan、Claude Code/claude-code、Clawde/OpenClaw/clawbot、Antigravity/antigravity 的 Notes + Reminder 均 PASS，Antigravity 真人确认 23 tools、Notes / Reminder 正常且 Dock 无新增 MCP/Python 图标。Cloud clawbot 原 credential 未轮换，Mac clawbot 使用独立 local credential；Antigravity 使用独立普通 AI actor credential；local Reminder fallback 没有恢复。Ke local MCP 继续 DEFERRED。当前 `main == origin/main == HEAD == 04ec7a364af7ed3d3970485453a676e2adc712bb`，worktree clean，`PUSH=PASS`。下一步先做一张很小的 **GreenCloud 3.5 post-close cleanup / maintenance ledger**：只读核对 deployment record drift、现存 rollback leftovers 与当前 dead-code reachability，处理真正该删除/归档的施工残留；`ai_call_logs` retention、Reminder owner slimming、sidebar 17px、actor mapping duplication 继续作为 non-blocking maintenance，不主动扩成重构。cleanup 后再单独进入 Cloud `prepend + update_note_metadata`；Delete 仍不做。不要重做 GreenCloud 3.5、四客户端接线或旧 Reminder P2。

---

# 78. GreenCloud 3.5 Post-Close Cleanup / Maintenance Ledger｜CLOSED

**补记日期：2026-10-04**  
**状态：此前 §75 的 cleanup / maintenance ledger 已完成、ff-only 合并并 push；§75 中 `NEXT` 仅保留为历史阶段状态。**

施工 branch：

```text
codex/gc35-post-close-maintenance
```

cleanup closeout commit：

```text
7c77a943864265f0fa170a6f128f33b3f80c7618
```

正式完成：

```text
DEPLOYMENT_RECORD=FIXED / CLOSED
ROLLBACK_AND_CONSTRUCTION_LEFTOVERS=CLOSED
OLD_AUDIT_REACHABILITY=REJUDGED_ON_CURRENT_BASELINE
CLEANUP_LEDGER=CLOSED
```

## 78.1 Deployment record｜CLOSED

清理前已核对：

```text
/srv/snotes/INSTALL.md
/srv/snotes/runtime/step10-source-manifest.json
/srv/snotes/outbox-deploy/deployment-record.json
```

与实际 Step 10 source / assets hashes 一致，没有把后续 Mac-only commit 冒记为远端部署。

本轮只退休已经失效的 rollback path 记录，并在 INSTALL / deployment record 中写清当前恢复来源；source manifest、runtime 源码、schema、权限与 credential 均未改变。

## 78.2 五个施工 / rollback leftovers｜全部 DELETE

最终删除并确认不存在：

```text
/root/greencloud-backups/step10-20261004T065916Z-45x1t_h5
/root/greencloud-backups/step10-record-20261004T070417Z-8i99u80v
~/Library/Application Support/S-notes/backups/gc35-step11-helper-084f67cf/
/srv/snotes/data/step11-fixture-084f67cf.json
/var/lib/snotes-gale-r1b-production/step11-fixture-084f67cf.json
```

结论：

```text
UNIQUE_RECOVERY_MATERIAL=NO
REAL_NOTE_OR_REMINDER_DATA_DELETED=NO
SECOND_ROLLBACK_FOR_BACKUP_DELETION=NO
```

两份 Step 11 fixture 只含本轮虚构验收行；Step 10 rollback / Mac helper backup 也没有当前正式 Git / App / deployment record 无法恢复的独有材料。

## 78.3 旧 audit reachability｜重新定性

以当时正式基线 `04ec7a3` 重新核对旧 9/24 audit 候选，不直接照旧名单删代码。

最终：

```text
accept_pending_append                    = CLOSED
raw_candidate is None 旧分支             = CLOSED
raw_candidate_markdown 未消费参数         = DELETE_CANDIDATE
src/database.py                          = KEEP_UNTIL_TRIGGERED
src/services/ai_access_service.py        = KEEP_UNTIL_TRIGGERED
src/redaction.py                         = KEEP_UNTIL_TRIGGERED
src/tag_manager.py                       = KEEP_UNTIL_TRIGGERED
UI demo / smoke CLI paths                = LIVE
Reminder dead local path                 = CLOSED / NONE_FOUND
```

本轮没有删除业务代码。

`raw_candidate_markdown` 只作为未来自然触发时可处理的删除候选；不得因为“账上有 DELETE_CANDIDATE”单独开清理工程。

## 78.4 现行 deferred maintenance

```text
ai_call_logs retention
= DEFERRED
= SAFE_TO_APPLY_RETENTION=YES
= RETENTION_IMPLEMENTED=NO

Reminder owner slimming
= DEFERRED
= 不因文件大主动拆

Reminder sidebar max-width ~17px
= DEFERRED
= UI_POLISH_ONLY

actor mapping duplication
= DEFERRED
= 以后真正修改相关 owner 时顺手加一致性测试

Cloud read open-time target_actor recheck
= NO_CONSTRUCTION_REQUIRED

GreenCloud hostname resolution warning
= DEFERRED_SYSTEM_HYGIENE
```

以上均不阻塞 S-notes 当前正式运行，也不自动成为下一主线。

## 78.5 Cleanup Git 封口

最终：

```text
MAIN=7c77a943864265f0fa170a6f128f33b3f80c7618
ORIGIN_MAIN=7c77a943864265f0fa170a6f128f33b3f80c7618
WORKTREE=CLEAN
MERGE=FF_ONLY_PASS
PUSH=PASS
CLEANUP_LEDGER=CLOSED
```

从本节开始，§75 中 `Post-Close Cleanup / Maintenance Ledger｜NEXT` 与 §77 中“下一步先 cleanup”的描述只代表当时阶段，不得再作为当前待办。

# 79. 3.5-D 后续｜Cloud prepend + update_note_metadata｜CLOSED

cleanup 封口后，按既定 3.5 v2.2 FINAL 单独施工：

```text
Cloud prepend_note
Cloud update_note_metadata
```

施工 branch：

```text
codex/gc35-cloud-prepend-metadata
```

开工基线：

```text
7c77a943864265f0fa170a6f128f33b3f80c7618
```

最终功能 branch / formal closeout HEAD：

```text
172a46125015dafa672be39357cffe94276dd177
```

## 79.1 架构保持不变

两项新能力继续复用已经完成的：

```text
outbox v3
actor credential / UID identity
expected_version
request_id / idempotency
pending queue cap
proposal lifecycle
Mac 双来源 pull
AI Center review
accept / reject / conflict
terminal receipt
get_operation_status
accepted → immediate actor mirror publish
```

没有：

```text
第二张 proposal 表
第二套 receipt lifecycle
prepend 专用 outbox
metadata 专用 outbox
outbox schema migration
```

正式 Notes authority 仍是 Mac；Cloud 只提交 proposal，不直接修改正式 Notes。

## 79.2 Cloud write allowlist 扩展

此前：

```text
create
append
edit
```

现在正式扩展为：

```text
create
prepend
append
update_note_metadata
edit
```

Cloud actor stdio MCP 同时继续提供 `get_operation_status` 与既有 read tools。

以下继续明确拒绝：

```text
delete
修改 AI visibility
修改 actor targets
```

actor 仍由 credential / OS UID / server-side identity 固定，payload 不能自报或覆盖 actor。

## 79.3 metadata allowlist｜固定为现有本地 contract

本轮没有扩大 metadata 权限。

正式 allowlist：

```text
title
directory_id
add_tags
remove_tags
```

`update_note_metadata` acceptance：

```text
正文保持不变
只修改允许的 metadata
stale expected_version → conflict / zero write
visibility / targets → blocked
```

Delete 继续不存在。

## 79.4 机器 Gate｜PASS

进入真人 Gate 前：

```text
CLOUD_PREPEND=PASS
CLOUD_METADATA=PASS
METADATA_ALLOWLIST=title,directory_id,add_tags,remove_tags
VISIBILITY_TARGETS_BLOCKED=YES
DELETE_BLOCKED=YES

OUTBOX_SCHEMA_CHANGED=NO
OUTBOX_SCHEMA=v3
MAC_RUNTIME_CHANGED=YES
GREENCLOUD_DEPLOY=PASS
GREENCLOUD_DEPLOY_FILES=9
TARGETED_TESTS=112_PASS
ARCHITECTURE_CHECK=PASS
GALE_REGRESSION=PASS
FORMAL_APP_BUILD=PASS
CODESIGN=PASS
FORMAL_APP_SMOKE=PASS
```

GreenCloud 部署只替换本功能实际需要的 runtime 文件，并保留对应备份；没有改 Reminder authority、credential 模型或 Oracle Gale schema/runtime。

# 80. Cloud prepend + metadata 真人 Gate｜PASS

真人验收使用同一篇虚构 Note：

```text
NOTE_ID=348
TITLE=GC35 虚构验收 · PREPEND + METADATA
```

## 80.1 PREPEND｜PASS

S 在正式 AI Center 点击 Accept。

前置内容：

```text
虚构前置段：PREPEND 已接受。
```

机器随后核对：

```text
CLOUD_TERMINAL_STATUS=accepted
PREPEND_COUNT=1
PREPEND_AT_BODY_START=YES
ORIGINAL_BODY_PRESERVED=YES
CLOUD_READ_UPDATED=YES
HUMAN_PREPEND_GATE=PASS
```

## 80.2 真人 Gate 中发现 AI Center close crash

PREPEND Accept 后，S 关闭 AI Center 时发现：

```text
AI Center close
→ S-notes 整体直接退出
```

定位原因为 AI Center window 被销毁时仍有同步线程运行。

最小修复：

```text
关闭 AI Center
→ 先隐藏窗口
→ 等同步结束
→ 再释放窗口
→ 主窗口继续存活
```

该修复属于本功能真人 Gate 直接发现的真实 blocker，因此同票修复，没有扩成 UI 生命周期重构。

修复后：

```text
RELATED_REGRESSION=40_PASS
FORMAL_APP_CODESIGN=PASS
FORMAL_APP_CLOSE_SMOKE=PASS
```

## 80.3 METADATA｜PASS

随后基于 PREPEND 后的新 version 提交 metadata proposal。

真人 Accept 的目标：

```text
title = GC35 虚构验收 · 资料已更新
directory = 待办 / 无
add tag = metadata-accepted
remove tag = before-metadata
```

S 真人确认：

```text
TITLE=PASS
DIRECTORY=PASS
TAGS=PASS
BODY_UNCHANGED=PASS
AI_CENTER_CLOSE_AFTER_METADATA=PASS
SNOTES_MAIN_WINDOW_SURVIVES=YES
HUMAN_METADATA_GATE=PASS
AI_CENTER_CLOSE_GATE=PASS
```

因此最终：

```text
HUMAN_GATE=PASS
```

# 81. 真人 Gate cleanup / mirror refresh｜CLOSED

真人 Gate 后：

```text
Note #348 → 移入 S-notes 应用回收站
```

没有永久删除，也没有清空应用回收站。

Cloud 两条 proposal 最终均为：

```text
accepted
```

accepted 历史回执保留；没有 pending 测试 proposal 残留。

清理 #348 后需要刷新 `claude-code` actor mirror。现有发布接口会重新发送该 actor 当前可见的整份镜像到既有 GreenCloud，因此自动审批先正确拒绝了这一超出“只清测试 Note”字面范围的动作。

S 随后明确授权：

```text
允许刷新既有 Claude Code 镜像
```

授权后执行现有受限 SSH mirror publish；最终：

```text
ACTOR_MIRROR_REFRESH=PASS
CLOUD_NOTE_348_READABLE=NO
OUTBOX_TEST_RESIDUE=NONE_PENDING
ACCEPTED_HISTORY_RECEIPTS=RETAINED
```

这不是新的权限扩张，只是对既有正式 actor mirror 做一次已授权刷新。

# 82. Cloud prepend + metadata 最终 Git / App 封口｜CLOSED

接班封口时确认功能分支已经包含真人 Gate crash fix 与相关测试，最终：

```text
TASK_FINAL_HEAD=172a46125015dafa672be39357cffe94276dd177
FORMAL_APP_SOURCE=172a46125015dafa672be39357cffe94276dd177
FORMAL_APP=PASS
CODESIGN=PASS
FINAL_TARGETED_TESTS=PASS
```

正式 App 已对应最终 HEAD，已有成品 smoke 证明 AI Center 关闭后主窗口继续存活，因此接班阶段没有为了仪式重复 rebuild。

随后：

```text
MERGE_MODE=FAST_FORWARD_ONLY
PUSH=PASS
```

最终 Git：

```text
MAIN=172a46125015dafa672be39357cffe94276dd177
ORIGIN_MAIN=172a46125015dafa672be39357cffe94276dd177
HEAD=172a46125015dafa672be39357cffe94276dd177
WORKTREE=CLEAN
```

正式结论：

```text
CLOUD_PREPEND=PASS
CLOUD_UPDATE_NOTE_METADATA=PASS
HUMAN_PREPEND_GATE=PASS
HUMAN_METADATA_GATE=PASS
AI_CENTER_CLOSE_CRASH_FIX=PASS
CLOUD_PREPEND_METADATA=CLOSED
```

# 83. 当前唯一正式基线｜2026-10-04 FINAL

从本节开始，§74 的 `04ec7a3`、§75 cleanup NEXT、§76–§77 Cloud prepend / metadata NEXT 都只代表历史施工阶段。

当前唯一正式 Git / App 基线：

```text
MAIN=172a46125015dafa672be39357cffe94276dd177
ORIGIN_MAIN=172a46125015dafa672be39357cffe94276dd177
HEAD=172a46125015dafa672be39357cffe94276dd177
WORKTREE=CLEAN
PUSH=PASS

FORMAL_APP=/Users/s/Documents/项目/S-notes/dist/S-notes.app
FORMAL_APP_SOURCE=172a46125015dafa672be39357cffe94276dd177
FORMAL_APP_BUILD=PASS
CODESIGN=PASS
```

架构现状：

```text
GREENCloud_3_5_A=CLOSED
GREENCloud_3_5_B=CLOSED
GREENCloud_3_5_C=CLOSED
GREENCloud_3_5_D=CLOSED
11_STEP_ROADMAP=COMPLETE
GREENCLOUD_3.5=COMPLETE

NOTES_AUTHORITY=Mac
REMINDER_AUTHORITY=GreenCloud
LOCAL_WRITABLE_FALLBACK=NO
LEGACY_REMINDER_HELPER=RETIRED

CODEX_LOCAL_MCP=PASS
CLAUDE_CODE_LOCAL_MCP=PASS
ANTIGRAVITY_LOCAL_MCP=PASS
CLAWDE_OPENCLAW_LOCAL_MCP=PASS
KE_LOCAL_SNOTES_MCP=DEFERRED

GREENCloud_3_5_POST_CLOSE_CLEANUP=CLOSED
CLOUD_PREPEND=CLOSED
CLOUD_UPDATE_NOTE_METADATA=CLOSED
DELETE_NOTE=NO
AI_VISIBILITY_WRITE=NO
ACTOR_TARGETS_WRITE=NO
```

当前仍活着的 maintenance 仅为按触发处理：

```text
ai_call_logs retention                  = DEFERRED
Reminder owner slimming                 = DEFERRED
Reminder sidebar max-width ~17px        = DEFERRED / UI_POLISH_ONLY
actor mapping duplication               = DEFERRED
raw_candidate_markdown                  = DELETE_CANDIDATE / DO_NOT_OPEN_ALONE
Cloud read open-time target_actor       = NO_CONSTRUCTION_REQUIRED
GreenCloud hostname resolution warning  = DEFERRED_SYSTEM_HYGIENE
```

这些都不是当前 blocker，也不构成自动下一张施工票。

# 84. 新窗口接班短句｜当前唯一版本

> G，继续 S-notes。当前唯一正式基线已经更新到 `main == origin/main == HEAD == 172a46125015dafa672be39357cffe94276dd177`，worktree clean，`PUSH=PASS`；正式 `dist/S-notes.app` 也来自同一 HEAD，build / codesign / smoke PASS。GreenCloud 3.5-A/B/C/D 与 11 步路线全部 COMPLETE；四个本地 Agent MCP 接线 CLOSED；post-close cleanup / maintenance ledger 也已 CLOSED，commit `7c77a943864265f0fa170a6f128f33b3f80c7618` 已 ff-only 合入并 push，5 个 Step10/11 rollback/fixture leftovers 已删除，deployment record 已销账。随后 Cloud `prepend_note + update_note_metadata` 独立功能票也已 CLOSED：复用 outbox v3 / expected_version / idempotency / AI Center / receipt / mirror refresh，没有 schema migration；metadata allowlist 仅 `title / directory_id / add_tags / remove_tags`，visibility / actor targets / delete 继续禁止。真人 Gate 使用虚构 Note #348：PREPEND accepted，METADATA accepted，metadata 后正文保持不变；验收中发现“关闭 AI Center 导致 S-notes 整体退出”，已修为先 hide、同步结束后再释放，40 项相关回归与正式 App close smoke PASS；S 真人再次关闭 AI Center 后主窗口正常存活。#348 已移入应用回收站；经 S 明确授权后刷新既有 claude-code mirror，Cloud 已读不到 #348；accepted 历史回执保留、无 pending 测试 residue。最终功能 branch `codex/gc35-cloud-prepend-metadata` 已 ff-only 合入 main 并 push，最终 commit=`172a46125015dafa672be39357cffe94276dd177`。当前没有新的强制 GreenCloud 3.5 主线；`ai_call_logs` retention、Reminder owner slimming、sidebar 17px、actor mapping duplication、hostname warning 等只在真实触发时处理，`raw_candidate_markdown` 只是 DELETE_CANDIDATE，不单独开票；Delete Note 仍不做。不要从旧 §74–§77 把 cleanup / prepend / metadata NEXT 捞回来重新施工。


# 85. Final maintenance closeout｜2026-10-04 CLOSED

本节为获 S 明确授权后的最终 maintenance 补记；此前 §83–§84 的 `172a461` Git/App 基线保留为本次 retention 施工前历史状态。最新实现与本节记录以包含本节的正式 closeout commit 为准。三项以外不施工。

- **最新最终进度书入 Git：CLOSED。** 下载正文原样纳入 `docs/`，§78–§84 与最终状态均通过核验；独立 commit=`7527df1c1827e2b81d9d3ac9830df39af797eb50`，已 push。此次仅追加 §85，原下载正文不改。
- **ai_call_logs retention：CLOSED。** S 确认 `count_based / KEEP_ROWS=10000 / TRIGGER_ROWS=12000`。`<=12000` 不处理；`>12000` 单事务、单条 DELETE 裁回按既有 `called_at DESC, id DESC` 排序的最新 10000 条。复用 `SQLiteAiAuditRepository.prune_call_logs()` 与 `initialize_database()` 的低频启动维护入口，不在每条写入时 DELETE；无 migration 或新增 daemon / scheduler / service。正式首次操作已报告 24389 条与预计删除 14389 条，私有 SQLite 一致性备份 PASS；实际 `24389 → 10000`、删除 `14389`，完整最新保留集合与业务表写入边界验证 PASS。proposal / receipt、幂等、operation status、pending / processed 和正式 Notes 均不受影响。
- **GreenCloud hostname warning：CLOSED。** hostname 与 `/etc/hostname` 原本均为 `optimal-living`，仅 `/etc/hosts` 仍用 `debian`。备份后将本机行改为 `127.0.1.1 optimal-living debian`；hostname、getent 与无副作用 sudo probe PASS，warning 消失。

相关临时 HOME / DB 测试 18 项 PASS，包括阈值边界、全部最新 ID、业务表完整行保存、分页、幂等与回执回归；源码与实际成品按现有构建流程封口。详细实际操作与验证证据见 `docs/gc35-post-close-maintenance-ledger.md` 的 Final maintenance closeout。

```ini
DOC_PROGRESS_FILE=RECORDED
HOSTNAME_WARNING=CLOSED
RETENTION_MODE=count_based
KEEP_ROWS=10000
TRIGGER_ROWS=12000
AI_CALL_LOG_ROWS_BEFORE=24389
AI_CALL_LOG_DELETED=14389
AI_CALL_LOG_ROWS_AFTER=10000
RETENTION_IMPLEMENTED=YES
RETENTION_WAITING_FOR_S_DECISION=NO
MAINTENANCE_CLOSEOUT=CLOSED
```

Reminder owner slimming、sidebar 17px、actor mapping duplication、raw_candidate_markdown、target_actor、Ke local MCP、delete_note 与 visibility / actor targets 保持既有状态，不因本轮 maintenance 重开。
