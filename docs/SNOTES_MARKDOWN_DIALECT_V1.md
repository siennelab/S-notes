# S-notes Markdown 方言 v1

方言 ID：`snotes-markdown-v1`。

本文描述当前正式编辑器接受和生成的 Markdown。实现依据为 `web/markdown-editor/src/main.js`、`body-size.js`、`list-style.js`；它不是另一套独立的 Markdown 解析器。

## 基础语法

- CommonMark：包括段落、标题、强调、加粗、链接、引用、代码与有序、无序列表等标准语法。
- GFM 扩展：删除线（strikethrough）、任务列表（task list）、表格（table）。
- 编辑器工具栏提供 H1、H2、H3 标题，以及有序列表、无序列表。列表使用 Milkdown 的标准 Markdown 解析与序列化，并保留 GFM 任务列表项。列表项的第一个内容块可以是**段落或标题**，其后可以继续有其他块；因此标题可以直接作为列表项的主内容。标题和列表标记的视觉组合仅是呈现样式，不另加 Markdown 字段或私有语法。

## S-notes 正文字号扩展

正文的行内字号是 Markdown 文本指令：

```markdown
:size[文字]{size="15"}
:size[文字]{size="20"}
```

仅当指令为 `size` 文本指令、`size` 属性恰为 `"15"` 或 `"20"`、且没有其他属性时，编辑器才把它识别为字号标记。普通正文默认是 17px，不使用 `:size` 指令；`size="17"` 不是合法字号指令。字号标记是 Markdown 内联标记，渲染时使用 DOM span，不是 HTML 正文格式。

标题是块样式。编辑器的 H1–H3 命令会清除所作用标题中的正文行内字号；在标题上设置正文 15px、17px 或 20px 时，会先把标题转为段落，再应用相应的正文样式。列表项主内容为标题时，其列表标记随标题样式显示；主内容为段落时，只有整段行内字号一致才采用对应字号的列表标记样式，混合字号使用默认 17px 标记。这些操作和标记样式不引入新的 Markdown 语法。

## 未知或非法指令

**未知 directive 与非法 `size` 必须作为字面文本保留；AI 不得自行删除。** 当前编辑器会在 Milkdown schema 处理前，把不符合上述 `size` 条件的文本、叶子或容器 directive 转为其原始源码的字面文本。包括未知指令名、非法字号值及带额外属性的 `size` 指令。它们不是可忽略的垃圾语法。

读取工具 `get_note_markdown` 返回数据库中的原始 `content_markdown`，不经过编辑器解析或重新序列化；所以读取时也不得以格式整理为理由修改这些文本。
