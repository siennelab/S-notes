# GreenCloud 3.5 Step 2B｜canonicalizer foundation

日期：2026-09-23。分支：`codex/gc35-step02a-roundtrip-scan`；起点：`079b6e799b45547aca4efff273da7a1a81cb2382`。

## 交付

新增 `src/snotes/ui/markdown_canonicalizer.py`，提供：

- `canonicalize_markdown(markdown) -> str`
- `canonicalization_changes(markdown) -> bool`
- `MarkdownCanonicalizer` context manager：多次调用复用一个独立编辑器。
- `CanonicalizationError`：加载失败、超时、无效序列化结果或不收敛时明确失败，不返回候选正文。

直接复用正式 `MarkdownEditorWidget` 和 `web/markdown-editor/dist`，没有另一套 serializer、Node 调用、数据库访问或持久化依赖。运行于 Qt 主线程；支持复用已有 QApplication，或按需建立并保留进程内 QApplication。独立编辑器使用 off-the-record profile，不显示用户窗口，关闭时不退出宿主 App。加载和关闭等待各有 30 秒上限；会话正常关闭并释放页面。

## 固定点与 baseline

`MAX_PASSES=4`。从原始输入开始，每轮交给正式 Milkdown load → serialize，只有本轮输出与本轮输入完全一致才返回。四轮包含最后的稳定性确认轮；到达上限仍变化则失败，不返回“最后一次结果”。没有 Note 编号或 HTML 尾块特判。

虚构 fixture `text\n\n<br>\n` 的实际过程：

1. `text\n\n`
2. `text\n`
3. `text\n`，确认固定点。

再次 canonicalize 为 ZERO DIFF。baseline 比较使用稳定结果与原始输入直接比较；`text\n` 返回 false，`text` 和尾部空块 fixture 返回 true。失败继续抛异常，不把失败误报成“原文已 canonical”。

baseline 判断只说明字节是否变化，不证明任意历史内容的语义等价。下一阶段的既定边界仍是：原文非 canonical 时必须 explicit human review；本轮未实现该 review 或任何写路径接入。

## 方言与验证

定向测试 `tests/test_gc35_step02b_canonicalizer.py`：**8/8 PASS**。

13 组 golden fixtures 覆盖 paragraph、heading、bold / italic / strike、link、unordered list、ordered list、task list、table、escaping、trailing newline、size 15、size 20、列表中的 heading。每组验证预期字节、一次正式 Milkdown 重载 ZERO DIFF、再次 canonicalize ZERO DIFF。正式 text handler 会把行内 mark 之间的空格写成 `&#32;`，golden output 保留此正式行为。

附加验证：尾部空块三轮收敛；already-canonical / legacy-diff 区分；未知 directive 与非法 size 的渲染文字完整保留；模拟持续振荡恰在四轮失败；序列化异常与无效返回失败；真实 bridge 的 loadFailed 通知不被当作成功空正文；后台线程调用被拒绝；辅助窗口不可见且不退出宿主；新进程自行创建 Qt 后可连续调用。测试 PATH 不含 Node，仅使用现有 `.venv` 与打包的编辑器资源。

另外仅运行：

- `tests.test_gc35_step02a_roundtrip_scan`：**1/1 PASS**，临时 fixture DB；没有重跑正式扫描。
- `tests.test_package5_local_mcp_protocol.Package5LocalMcpProtocolTests.test_normal_create_get_append_and_operation_status_smoke`：**1/1 PASS**，临时 HOME / DB。

Qt 测试在沙箱外运行，沿用已确认的 QtWebEngine 执行方式；只使用虚构文本和临时 HOME，正常 close / QTimer 退出。未读取正式 DB，未跑无关全量 suite，未重新 build 或部署 App；正式打包验收留待写路径接入阶段。

## Append 契约

`APPEND_NEWLINE_CONTRACT=DOUBLE_NEWLINE`

已有非空正文与新非空 Markdown block 之间使用 `\n\n`。当前生产 append / prepend 拼接本来就是双换行，本轮未修改它们；只更新上述附近 MCP smoke 的旧单换行预期（包括 prepend 后仍包含 append 结果的断言），真实 MCP 测试通过。

## 边界

未接 create / prepend / append / edit，未改 AI Center、redaction、正式 Notes、revision / updated_at、GreenCloud、Gale、Reminder。没有 migration、反转义或历史正文清理。未 merge main、未 push。

FORMAL_DB_MUTATED=NO

WRITE_PATHS_INTEGRATED=NO

NEXT=WAIT_FOR_S_G_REVIEW_BEFORE_2C
