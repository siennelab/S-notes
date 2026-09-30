# S-notes｜编辑器代码块与字号 小工单 v1.0

- 依据：S 与寓的讨论裁定（2026-09-24）
- 基线：main @ `2fa0df2`
- 施工：Codex；审计：寓；验收与拍板：S
- 顺序：**本工单先于「AI 接受链修复包」**。本单合入 main 后，把修复包施工单和复核交接指令里的基线 `2fa0df2` 改成新的 commit。

---

## 0. 一句话目标

修两个编辑器 bug：全选改字号被代码块整单卡死；代码块按钮只能进、不能出。只改按钮行为，不动 Markdown 的解析、保存格式和节点定义。然后停手。

---

## 1. 施工纪律

1. 遵守仓库里的 `AGENTS.md`：
   - 只用 `.venv/bin/python`。
   - smoke 和测试用临时 HOME，不碰正式数据库。
   - 开工前检查分支和 git status。当前 `docs/` 下有三份未跟踪的文档，属于 S，不要暂存、改动或删除。
2. 在新分支上施工。封口前不合并到 main。
3. **允许改动的文件**：
   - `web/markdown-editor/src/body-size.js`
   - `web/markdown-editor/src/main.js`（只动代码块按钮的接线）
   - 如有需要，新建 `web/markdown-editor/src/code-block.js` 放代码块按钮的命令
   - `src/snotes/ui/markdown_smoke.py`（只加验收项）
   - 相关测试文件（只加，不删）

   改其他文件先停下来报告。
4. **不动的东西**：
   - 节点和 mark 的定义（schema）
   - remark 解析链
   - `main.js` 里的 Markdown 序列化配置（约 60-80 行，包括 `&#…;` 实体化那段）
   - `body_size` 的 `parseMarkdown` 和 `toMarkdown`
   - 键盘行为：Enter 在代码块里永远是换行，**不加任何快捷键**
   - 粘贴行为
   - Python 业务代码
5. `web/markdown-editor/dist/` 按 `.gitignore` 不提交，但验收前必须本地重新 build。
6. 不用 `skip` 或 `expectedFailure`，也不删测试。
7. 范围外的问题只记录，不顺手修。

---

## 2. 施工项

行号是审计时的起点证据，以施工时的源码为准。

### A. 字号：遇到代码块跳过，其余照改

**问题**：`body-size.js` 的 `setBodySize` 约 79 行，选区里只要有一个不是 paragraph 或 heading 的块，就整次返回 `false`。所以全文里只要有一个代码块，⌘A 后按 15/17/20 就没有任何反应。

**要求**：

- 只处理选区里的 paragraph 和 heading；code block 跳过，内容和样式都不变。
- 选区里一个 paragraph 或 heading 都没有时才返回 `false`。
- 光标停在代码块里、没有选区时，点字号什么都不发生。
- 其余现有语义一律不变：heading 转正文、17 清除标记、stored mark 的处理、工具栏按钮的按下状态。

### B. 代码块按钮：一个按钮，一条规则

**总规则：有选区，选中的行变；没选区，光标之后变。**

按钮自己判断方向，用户不用切模式：

- 选区或光标碰到任何 code block → **取消**
- 否则 → **进入**

下文说的「行」：在正文里指一个 paragraph 或 heading；在代码块里指以换行分隔的一行。

#### B1. 进入代码块

| 操作 | 结果 |
|---|---|
| 有选区 | 选区碰到的行，按整行合并成**一个**代码块，原来每行是代码块里的一行。选区外的正文不动 |
| 只有光标 | 在光标处切开当前行。光标之后到**本行末尾**的文字变成代码块；光标之前的留在原来的行里。其他行不动 |

- 光标在行尾：本行不变，下面新增一个**空**代码块，光标放进去。这是最常见的用法：点一下，然后粘贴。
- 光标在行首：整行变成代码块，上面不留空的正文行。
- 进入代码块只保留纯文字，加粗、字号、链接等格式丢掉，取消时也不恢复。这是预期行为。

#### B2. 取消代码块

| 操作 | 结果 |
|---|---|
| 有选区（整块、半块、全选全文都算） | 选区碰到的代码行，按整行变成正文 paragraph，一行一段。没被碰到的代码行上下原样保留，仍是代码块。选区里原本就是正文的部分不动。选区跨过多个代码块时，每个都这样处理 |
| 只有光标 | 在光标处切开。光标之前的内容（上面各行，加上本行光标之前的部分）保留为代码块；光标之后的内容（本行光标之后的部分，加上下面各行）变成正文，一行一段 |

- 光标在代码块最前面：整块变成正文，不留空代码块。
- 光标在代码块最后面：代码块原样保留，下面新增一个空正文段，光标放进去。**这就是「跳出」，也就是本单要修的「困在笔记末尾代码块里」那个问题。**
- 光标在某一行的行尾：本行光标之后是空的，照样生成一个空正文段放光标，下面各行跟在它后面变成正文。

#### B3. 两个方向统一的边界规则

- 光标**之前**那一半如果是空的，就不保留（不留空代码块，也不留空正文行）。
- 光标**之后**那一半即使是空的，也要保留成一个空块，光标放在它开头。
- 选区只选了半行，按整行算。

#### B4. 其他要求

- 每点一次按钮是**一个**撤销步骤，⌘Z 一次恢复原样。
- 工具栏里代码块按钮的按下状态沿用现有逻辑：光标在代码块里时显示按下。
- 只读状态下按钮无效，沿用现有的 `writable()` 判断。
- 在列表项或引用内部，规则同样适用于该容器内部的行。**选区跨越多个列表项、引用，或者跨越容器边界时，按钮什么都不做**，并在收口报告里说明这个限制。不要为这种情况另外设计行为。

### C. 硬约束：保存格式一个字都不变

规范化器在后台加载的是同一份 `dist` bundle，修复包也要用它，所以：

- 重新 build 之后，所有涉及规范化的测试结果必须和基线一致（清单见 §3）。
- 对于不经过按钮操作的已有笔记，打开再保存得到的 Markdown，必须和改动前逐字相同。

---

## 3. 验收

### 自动验收

1. `cd web/markdown-editor && npm run build` 成功。
2. 在 `markdown_smoke.py` 里新增下面这些验收项，全部 PASS，原有各项仍然 PASS：
   - `SIZE_SELECT_ALL_SKIPS_CODE_BLOCK`：全文含代码块，⌘A 后点 15/20/17，正文和标题照常修改，代码块的 Markdown 不变
   - `SIZE_CARET_IN_CODE_BLOCK_NOOP`
   - `CODE_ENTER_SELECTION`
   - `CODE_ENTER_CARET_MIDDLE`、`CODE_ENTER_CARET_LINE_END`、`CODE_ENTER_CARET_LINE_START`
   - `CODE_EXIT_SELECTION_WHOLE`、`CODE_EXIT_SELECTION_PARTIAL`、`CODE_EXIT_SELECT_ALL_DOC`
   - `CODE_EXIT_CARET_START`、`CODE_EXIT_CARET_MIDDLE`、`CODE_EXIT_CARET_END`
   - `CODE_EXIT_AT_DOC_END`：代码块是全文最后一个块，光标在末尾点按钮，可以继续输入正文
   - `CODE_ENTER_KEY_STILL_NEWLINE`：在代码块里按 Enter 仍然换行，不跳出
   - `CODE_BUTTON_SINGLE_UNDO`
   - `CODE_CROSS_CONTAINER_NOOP`

   每一项都要**断言最终的 Markdown 文本**，不能只看 DOM。

   运行方式：临时 HOME（目录名以 `snotes-md-check-` 开头），执行 `.venv/bin/python -m src.snotes.ui.markdown_demo --markdown-smoke`，入口以仓库实际为准。
3. 规范化相关测试，结果和基线一致：
   - `test_gc35_step02b_canonicalizer`
   - `test_gc35_step02c0_preview`
   - `test_gc35_step02c_write_paths`
   - `test_gc35_step02d_invariants`
   - `test_gc35_step03_edit_note`
   - `test_markdown_core_cutover`
   - `test_markdown_editor_runtime`
4. 全量 `unittest discover -s tests`：基线是 447 个里 8 个失败（清单见审计报告）。本单完成后，失败的必须**正好是同样那 8 个**，不多也不少；本单不修它们。
5. 架构闸门的结果和基线一致（本单不碰 Python 业务代码，理应不变）。

### 人工验收（S）

在隔离的候选 App 和隔离的数据目录里：

1. 一篇含代码块的笔记，⌘A 后分别点 15、20、17，正文字号变了，代码块没变。
2. 在一段正文的行尾点代码块，粘贴一段多行代码，按几次 Enter，仍然在代码块里。
3. 光标放在这段代码最后，点按钮，跳到下面继续写正文。
4. 光标放在代码中间，点按钮，下半截变成正文。
5. 选中整块代码，点按钮，整块变成正文，一行一段。
6. 以上每一步 ⌘Z 都能一次撤回。
7. 保存、关闭、重开之后，内容和保存前一致。

---

## 4. 停工条件

- 发现不改 schema、序列化或键盘行为就做不到本单要求。
- 重新 build 后，任何规范化测试或已有笔记的往返保存结果发生变化。
- 全量测试的失败集合和基线不同。
- 改动需要超出 §1 第 3 条的文件范围。

---

## 5. 收口报告格式

**先给状态块**：

```text
EDITOR_TICKET=DONE|BLOCKED
BODY_SIZE_SKIPS_CODE_BLOCK=YES
CODE_BLOCK_BUTTON_RULE=IMPLEMENTED
ENTER_KEY_UNCHANGED=YES
SHORTCUTS_ADDED=NO
SCHEMA_OR_SERIALIZER_CHANGED=NO
DIST_REBUILT=YES
MARKDOWN_SMOKE=<passed>/<total>
CANONICALIZER_TESTS_UNCHANGED=YES
FULL_UNITTEST=<ran>/<passed> (failures identical to baseline 8: YES|NO)
FORMAL_DB_TOUCHED=NO
HUMAN_GATE=PENDING|PASS
```

**然后分节**：

1. 改了什么（按 A、B）
2. 列表和引用内部的实际表现，以及跨容器选区的限制
3. 行为变化清单：只应该有本单 A、B 描述的变化
4. 新发现的债务（只记录，没有动手修）
5. 需要 S 拍板的事项
