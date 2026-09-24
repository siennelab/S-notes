# GreenCloud 3.5 Step 2C0｜Final Canonical Preview Foundation

日期：2026-09-23。分支：`codex/gc35-step02a-roundtrip-scan`；起点：`55f8bc4641da70ba6e37e32ff563a8efd461e6a3`。

## 共用计算与只读边界

新增 application 层 `build_canonical_note_candidate(before_markdown, addition_markdown, operation_type)`。它支持 append / prepend，保留非空旧正文与新片段之间的双换行边界，复用同一个正式 Milkdown 会话计算旧正文与完整 candidate 的固定点。返回不可变的 before、final、operation 和 legacy canonicalization 标记；不含数据库依赖，也不实现 edit。

`AiReceiptInboxService.build_pending_note_preview(receipt_id, addition_markdown=...)` 读取当前提案和目标 Note，校验 pending / parsed、操作类型、身份、目标存在与版本。当前 pending 内部保存的是 `expected_updated_at`，预览沿用现有接受路径的精确时间戳比较，不另造版本规则。Qt 计算返回后重新读取完整 Note 和 receipt，若发生变化则拒绝返回旧预览。

receipt read model 仅映射已有 `target_note_id` / `expected_updated_at` 列；没有新增表、字段或 migration。没有持久化 final body，没有正文日志。后续 2C 的接受及 direct 路径可直接复用 candidate builder，并继续在事务内复查版本；本轮没有改动这些写路径。

## 现有 AI Center 卡片

仅对本地 pending append 增加预览，保留原片段编辑框并标为“追加内容”。同一 detail 区域增加只读 unified diff，包含完整上下文，并显示末尾换行差异；UI 不自行拼接正文。

- legacy 正文显示“此最终结果除追加内容外，还会规范化这篇旧笔记的既有 Markdown。”
- canonical 正文不显示该额外提示。
- 展开卡片生成预览；修改片段立即清空旧 diff，再通过“刷新最终预览”计算。
- 加载失败或 stale 时不展示伪造 final body。
- 卡片构建完成后再启动预览；计算期间卡片被销毁时不再操作其控件。

**阶段限制**：接受路径尚未调用该 helper，界面明确标注“写入接入待 2C”。此处展示的是未来同一版本、同一片段经共用 helper 得到的 canonical 最终结果，不宣称当前接受按钮已经会 canonicalize。没有实现 prepend / metadata acceptance，也没有新建 dialog。

## 验证

只使用临时 HOME / SQLite fixture 与虚构正文，无正式 DB 读取或写入。

运行：

```text
.venv/bin/python -m unittest tests.test_gc35_step02c0_preview tests.test_package4_ai_center_receipt_lifecycle_ui -v
```

**12/12 PASS**：新增定向测试 6 项，现有 AI Center 生命周期测试 6 项。

覆盖 canonical before 与 fixed-point final、legacy 完整差异和警告、canonical 无额外警告、片段修改后的预览失效与刷新、同一 helper 的确定性、实际编辑片段与共用 builder 输出一致、canonicalizer failure、stale version、计算期间并发修改。零写入使用完整临时数据库逻辑 dump 前后比较，并在 canonical preview 中禁止调用 connection factory 的 write 接口。

新定向测试 PATH 为 `/usr/bin:/bin`，未使用 Node。真实 Milkdown / Qt 测试按既有方式在沙箱外执行，正常关闭并使用 QTimer 退出；无额外安装。已检查展开的 legacy 卡片实际渲染：完整差异、尾换行标记、规范化提示和原追加框均可读。未运行无关 suite，未 build / 部署 App。

FORMAL_DB_MUTATED=NO

WRITE_PATHS_INTEGRATED=NO

NEXT=WAIT_FOR_S_G_REVIEW_THEN_RESUME_2C
