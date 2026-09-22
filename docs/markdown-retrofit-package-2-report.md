# Markdown Retrofit Package 2

基线：`a4ec210f6d6534aac767f77bc1da5d3d86e0a6ef`。
分支：`codex/markdown-retrofit-package-2`。

## 交付边界

active R2 正文唯一契约为 `content_markdown`。模型、ports、mapper、repository、
普通保存、回收站 SELECT、AI create/append/prepend、回执接受和新 audit snapshot
均已切换。revision 乐观锁和既有元数据业务保持不变。

正式 shell 使用 Package 1 的 MarkdownEditorWidget。loadComplete 只建立 baseline；
未完成加载或失败时禁止保存。旧 load_id 被忽略。切换及关闭使用现有 prepareClose /
closeReady 屏障刷新输入；冲突取消关闭时恢复可编辑状态。关闭优先于待完成的切换。

fresh DB 直接创建 `content_markdown TEXT NOT NULL DEFAULT ''` 和
`schema_meta.body_format=markdown`。已有库在任何 schema 写入前以只读连接检查 marker；
所有 active SQLiteConnectionFactory 连接也检查 marker 和正文列。
Desktop、Reminder、Gale、MCP 的缺失 marker 拒绝路径均有隔离测试。
普通初始化不会迁移正文。

## 导出与消费者

- 普通 `.md`：直接使用原 Markdown 正文，保留格式。
- 普通 JSON：`content_markdown` 和即时派生的 `content_text`。
- AI Safe Export：Markdown → plain text → 既有脱敏 → 原有 `.md` 容器。
  不承诺原格式保真；五种格式拆分敏感词已验证到实际导出文件。
- MCP / Yan 读取与 Gale mirror 保持 plain text；AI 写入按 Markdown 解释。
- 新 audit snapshot 使用 `content_markdown`；不可变旧 audit JSON 不改写。

`markdown-it-py==4.2.0` 已明确加入 requirements。size directive 使用该 parser 的
inline 扩展处理；不存在 regex Markdown stripper 或第二套 Markdown parser。
搜索和关联共用 application 层 `(note_id, revision)` 进程内缓存，旧 revision 被淘汰。
没有 SQLite 派生正文列或磁盘派生文本缓存。

## 独立工具

`scripts/migrate_markdown_body.py --db <explicit-path>`：只接受已有 legacy DB，
在一个事务内转换正文、删除旧正文列并写 marker。重复 migration 和未知 marker 被拒绝；
失败回滚。UUID、revision、时间和业务关联保留；已知格式损失按 note_id 输出 JSON。

converter 位于 `src/snotes/migration/legacy_qt.py`，仅用于迁移。
覆盖标题、粗体、斜体、删除线、链接、列表、Qt code/quote 几何、20px、纯文本换行。
先 code 几何再 quote margin；15/17px 不生成 size mark。
相邻等价 Qt span 在 HTML 树中合并，避免 Markdown emphasis 边界产生可见星号。

`scripts/markdown_roundtrip_check.py --input <json> --output <json>`：
输入为 `[{"id": "identifier", "markdown": "body"}, ...]`。
单个离屏 QWebEngineView 驱动正式 bundled Milkdown，逐篇 load/serialize 两次。
输出每篇 first、second 和 stable；任一失败返回非零。使用临时 HOME，不读取数据库。
运行前使用仓库的既有 `npm run build --prefix web/markdown-editor` 生成 bundle。
没有独立 Node parser/serializer。

## 验证结果

全部数据为 temporary / synthetic。没有读取、复制、迁移或打开正式 DB。

- `test_markdown_core_cutover`：schema/model/storage、marker 拒绝、migration CLI、
  事务回滚、converter、五种脱敏、导出、cache、MCP Markdown 写入/plain 读取与 snapshot。
- `test_markdown_editor_runtime`：真实 bundle → MainWindow → SQLite，覆盖 baseline、
  空白/相同正文、旧回调、加载失败、autosave、切换/关闭、冲突恢复、快速关闭取消切换。
- storage/R1 非正文 parity、普通产品、AI safe read/export、metadata、receipt、MCP stdio、
  Gale、Yan、category、Reminder 和相关 UI 业务回归通过。
- 旧 UI 测试的同步加载假设改为等待 loadComplete；清理临时 DB 前等待关闭握手。
- 正式 Web smoke 通过：字号/标题/列表、undo/redo、find/replace、只读、三主题、
  未知 directive、导航隔离、关闭握手。
- 83 篇 synthetic 迁移 Markdown：**83/83 round-trip stable**。
- architecture check PASS；源码语法检查及 `git diff --check` PASS。

旧 Qt note editor、Python toolbar、HTML paste/clear-format 及其实现专属测试已退休。
由现有正式 Web smoke 和新 Markdown runtime 测试覆盖对应新行为。
AI Center/Reminder 的合法纯文本 QTextEdit 与其业务测试保留；冻结 R1 源码未改。

83 篇 synthetic 正文总量：2,526,022 bytes。

| 检查 | 秒 |
| --- | ---: |
| Cold search | 0.408598 |
| Warm search | 0.009785 |
| Cold relations | 0.405830 |
| Warm relations | 0.006397 |

每组 cold/warm 合计只解析 83 篇；warm 未增加 parse_count。
revision 更新测试确认旧 key 不再使用。

## 最终检查

```text
BODY_AUTHORITY=SQLITE_MARKDOWN
ACTIVE_HTML_BODY_RUNTIME=NO
DUAL_FORMAT_RUNTIME=NO
AUTO_MIGRATION=NO
PLAIN_TEXT_CACHE=MEMORY_ONLY
PERSISTED_CONTENT_TEXT=NO
SECOND_BODY_REPRESENTATION=NO
EDITOR_JSON_BODY=NO
AI_SAFE_EXPORT_BODY=REDACTED_PLAIN_TEXT
AI_SAFE_EXPORT_FORMAT_PRESERVATION=NOT_REQUIRED
MARKDOWN_REDACTION_BYPASS=NO
MCP_BODY_OUTPUT=PLAIN_TEXT
GALE_BODY_OUTPUT=PLAIN_TEXT
AI_WRITE_INPUT=MARKDOWN
FORMAL_DB_TOUCHED=NO
PACKAGE_3_STARTED=NO
```

active R2 中旧列名仅用于拒绝旧 schema，HTML converter 仅位于 migration namespace。
`content_text` 仅在导出 JSON 构造处出现。没有 production build replacement、部署、
main merge 或 push。真实旧笔记与真人日用验收留待后续明确授权的 Package 3。
