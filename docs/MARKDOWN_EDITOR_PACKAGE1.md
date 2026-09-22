# Package 1 — Markdown editor component

施工基线：[v1.2 FINAL](S-notes-Markdown-Retrofit-最小施工方案-v1.2-FINAL.md)。
此包只交付内部组件和 fixture 演示，不连接 NoteService 或数据库。

## 使用与构建

- 组件：`src/snotes/ui/markdown_editor.py` 中的 `MarkdownEditorWidget`。
- Web 源码：`web/markdown-editor/`；从已验收 lab 的 Milkdown 7.22.1 复用。
- `bash scripts/build_macos_app.sh --candidate`：执行锁定依赖安装、Vite build 和现有 PyInstaller 流程。
- 双击 `scripts/markdown_editor_demo.command`：使用 candidate App 的 `--markdown-demo` 入口。
- 源码演示：`.venv/bin/python -B app.py --markdown-demo`（需先构建 Web bundle）。

演示只加载固定样例；“保存到演示内存”不写文件，退出即清除。
切换和重载均重新加载固定样例。默认 App 入口仍使用旧 Notes 编辑器。

## 组件契约

Python → Web：`setMarkdown(markdown, load_id)`、`prepareClose()`、
`setTheme(theme)`、`setReadOnly(read_only)`。

Web → Python：`editorReady()`、`loadComplete(load_id, markdown)`、
`loadFailed(load_id, reason)`、`markdownChanged(markdown, load_id)`、`closeReady()`。

每次加载必须使用新的非空字符串 load_id。组件公开 `state`、`can_save`、
`baseline`、`latest_markdown`、`dirty`、`error`。只在 `can_save` 为真时允许消费者保存。
`loaded` 信号仅表示序列化 baseline 就绪，不触发保存或 revision；`changed` 表示
接受的正文修改，dirty 使用与 baseline 的比较。旧 load_id 的消息全部丢弃。
当前正文在成功装载替代文档之前保留；失败、等待回执或 renderer 故障均禁止保存。

主题复用现有 ThemeTokens。无持久 WebEngine profile，不修改 HOME。
链接由 Qt 交给系统浏览器，文件拖放不导航。字号仅 15/20 使用 size directive，
17 清除标记。未知 directive 在同一 remark 解析链中转换为字面文本节点。
Markdown 是跨桥正文的唯一表示；没有 HTML 中间层或 JSON 持久化。

## 验证

`--markdown-smoke` 在实际 WebEngine 中运行同一个 bundle，可用于源码或 frozen App。
必须在外部设置临时 HOME（目录名以 `snotes-md-check-` 开头）；测试入口自身不改 HOME。
可用 `SNOTES_MD_REPORT` 指定工程报告路径，`SNOTES_MD_SCREENSHOT` 指定截图路径。

覆盖明确加载回执、空正文编辑/演示保存、相同正文和快速切换、迟到消息、未知扩展、
真实 mount 故障注入、字号 round-trip、撤销/重做、查找替换、只读、主题、Raw、
外部导航和关闭回执。附近回归使用现有 editor toolbar/formatting/desktop 三组测试。

自动测试不代表中文 IME 或 rich paste 的真人验收。Package 1 human gate 必须等待 S；
验收前不做最终 commit，不 merge、不 push，不进入 Package 2。

## 真人验收修订：标题 / 字号直接互切

工具栏样式入口为 H1 / H2 / H3 / 15 / 17 / 20，移除“正文”按钮。
标题内只有光标时，字号入口将整段转为 paragraph 并应用对应字号；有文字选区时，
先转换涉及的标题 block，再按选区应用字号。普通正文仍保留局部字号语义。
转成 heading 时清除整段的正文 size mark；17 不保留 size directive。
每次转换使用一个 Web 编辑事务，Python bridge 不变。

## 真人验收修订：列表与内容样式叠加

Ordered / Bullet 是内容 block 外的独立 wrapper，可包含 paragraph 或 H1/H2/H3。
沿用 Milkdown 的 CommonMark/GFM list parser/serializer，只放宽内部 list_item 的
首个 block 类型，不增加 Markdown syntax、节点持久属性或 Python bridge API。
再次点击同类列表按钮提升选中 item，保留其 heading、paragraph 和 size mark。
切换另一类列表按钮改变当前包围列表的类型；多段首次添加列表时每段独立成项。

marker 使用 ProseMirror decoration 派生展示：依据 item 的首个 block，heading
跟随对应标题大小和字重；paragraph 全部文字同为 15/20 时跟随，否则为 17。
局部 inline size 不改变 marker。嵌套 item 独立计算；这些展示信息不进入 Markdown。
固定样例“列表与内容样式”用于本轮真人验收。
