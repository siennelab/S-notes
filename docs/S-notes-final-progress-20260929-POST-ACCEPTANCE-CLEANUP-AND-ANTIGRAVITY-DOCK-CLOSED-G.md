# S-notes 最终进度书｜Post-Acceptance Cleanup + Antigravity MCP Dock Bug CLOSED

**日期：2026-09-29**  
**状态：AI 接受链修复包 A–I、Post-Acceptance Cleanup Ticket 1–4、Antigravity 本地 MCP Dock 图标 bug 全部 CLOSED；下一步回到 GreenCloud 3.5-A Notes Cloud Read。**  
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
