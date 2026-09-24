# GreenCloud 3.5 Step 2C｜现有 AI 正文写路径接入

基线：`5602684bbc5682f59b3d600860ff4b4fe257b232`。
分支：`codex/gc35-step02a-roundtrip-scan`。

## 已实现

- MCP direct create 和既有 create receipt acceptance 均通过 bundled Milkdown 收敛到 fixed point 后才插入正式 Note；失败不写正式正文。
- direct append / prepend、pending preview、pending append acceptance 共用 `build_canonical_note_candidate`。非空正文拼接契约保持 `old + "\n\n" + addition`（prepend 为相反顺序），之后整篇 canonicalize。
- 原正文已经 canonical 时遵循原 modify confirmation 设置；原正文 non-canonical 时强制 existing pending。canonicalizer 不可用时 modify 同样转 pending，不允许绕过 canonicalization 直写。
- pending append 接受从当前 Note 重新计算，不持久化第二份正文；事务内再次核对 revision、existing expected_updated_at 和候选对应的原正文，沿用既有 optimistic concurrency contract。
- repository 只保存 application 已计算的最终正文，不再自行拼接；既有 revision / updated_at / history 同事务写入语义保留。
- metadata-only 不调用正文 canonicalizer，也不改 content_markdown。
- AI Center 展示完整 before → final diff 和 legacy 警告；append 接受按钮仅在预览成功后启用，编辑片段或预览失败后禁用。UI 不计算最终落库正文。

## MCP 进程边界

已核对当前安装 MCP 的同步工具执行方式：FastMCP 在 stdio 事件循环主线程直接调用工具，未转入线程池。复用现有 `MarkdownCanonicalizer` 按需创建不可见 QtWebEngine / QApplication 即可，无新增 daemon、子进程 runner、Markdown parser 或 Node runtime。

真实 stdio 测试使用项目 `scripts/run_snotes_mcp_stdio.sh`，仅临时 HOME 和 fixture DB，`PATH=/usr/bin:/bin`。create、canonical append/prepend 成功，legacy append/prepend 强制 pending；通过 MCP Markdown readback 与再次 Milkdown serialize 验证最终正文 ZERO DIFF。

## 验证

仅使用现有 `.venv/bin/python`，临时 HOME / S_NOTES_DATA_ROOT、fixture DB、离屏 Qt 和正常 close / QTimer 退出路径。沙箱内 QtWebEngine 启动退出 134；获准在沙箱外执行同一限定测试后通过。正式 DB 未读取、未修改。

最终限定测试：**45/45 PASS**。

| 范围 | 数量 |
| --- | ---: |
| `tests.test_gc35_step02b_canonicalizer` | 8 |
| `tests.test_gc35_step02c0_preview` | 6 |
| `tests.test_gc35_step02c_write_paths` | 11 |
| `tests.test_package4_ai_receipt_inbox_core` | 14 |
| 直接相关 MCP protocol 回归 | 5 |
| AI Center create 接受与刷新 UI 回归 | 1 |

五个 MCP 回归方法：

- `test_normal_create_get_append_and_operation_status_smoke`
- `test_pending_create_preserves_ai_proposal_but_accept_uses_s_fields`
- `test_pending_confirmation_and_duplicate_receipt_semantics`
- `test_create_note_accepts_complete_dynamic_metadata`
- `test_create_note_rejects_deleted_or_mismatched_category`

AI Center UI 回归：`test_pending_detail_accept_and_processed_refresh_stay_in_one_window`。

新增 2C 测试覆盖 direct create / append / prepend 的 fixed point、legacy 强制 pending 且 Note 完全不变、编辑后 preview/accept 一致、stale 零写入、Qt canonicalization 期间并发保存零覆盖、canonicalizer failure 零正式正文写入、metadata 不调用 canonicalizer、无 Node 的真实 stdio，以及预览成功前禁止点击 append 接受。

两处旧回归断言按本步正式新契约更新：create acceptance 的列表/空格转义/末尾换行，以及 UI create acceptance 的末尾换行。未改其他测试业务语义。另完成 `git diff --check`；查看实际 fixture 卡片渲染，完整 diff 和 legacy 警告可见。

## 停工边界

本轮只完成 2C；不实现 prepend / metadata acceptance、不做 2D 全路径审计、不做 edit_note。无 schema / migration、存量批量修改、Gale / Reminder / GreenCloud / redaction 改动。未重新打包或部署 App，本报告说明的是源码与本地测试结果。

FORMAL_DB_MUTATED=NO

MERGED=NO

PUSHED=NO

NEXT=WAIT_FOR_S_G_REVIEW_BEFORE_2D
