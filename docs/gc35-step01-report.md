# GreenCloud 3.5 第 1 步验收报告

- 基线：`9c261f32ae6f906400cd3847138871d8efdc87c3`，开工时 `main == origin/main`、工作区干净；未回退历史起点。
- 分支：`codex/gc35-step01-markdown-read`。
- 实现 commit：`27dee36cc1637eae78fc028c4e70b10836ca11e6`。本报告另作文档提交，最终分支 HEAD 包含两者。
- 状态：自动验证 PASS，待审计；Claude Code 真实 discovery / S 真人验收为 HUMAN_GATE，尚未宣称整步人工封口。

## 改动文件

1. `src/snotes/application/local_mcp_service.py`：原样读取 Markdown，full + actor target 检查，默认拒绝未提供修改能力的内部调用；复用版本函数与既有 get_note 审计类别。
2. `src/snotes/transport/mcp/tools.py`：工具注册、说明、只读 annotation；从 read_only 和现有有效工具 allowlist 推导修改能力，不新增权限模型。
3. `src/snotes/transport/mcp/schemas.py`：独立 Markdown 响应 schema。
4. `docs/SNOTES_MARKDOWN_DIALECT_V1.md`：按正式编辑器源码记录方言。
5. `docs/MCP_SERVER_RUNBOOK.md`：工具和权限、审计边界、真人验收。
6. `tests/test_gc35_step01_markdown_read.py`：6 项定向测试。
7. `tests/test_package5_local_mcp_protocol.py`：工具清单与真实 stdio 调用；修正两处已有单换行旧预期为双换行。基线 `ai_mcp_operations.py` 自 `60125e8` 起已用 `\n\n` 拼接，未修改写路径。
8. `docs/gc35-step01-report.md`：本报告。

## 验证

所有 Python 命令使用仓库 `.venv/bin/python`，临时 HOME 和测试创建的临时数据库；未读取真实用户数据库。

```sh
TASK_HOME=$(mktemp -d /tmp/snotes-step1-home.XXXXXX)
HOME="$TASK_HOME" .venv/bin/python -m unittest tests.test_gc35_step01_markdown_read -v
# Ran 6 tests — OK

HOME="$TASK_HOME" .venv/bin/python -m unittest tests.test_package5_local_mcp_protocol tests.test_yan_mcp_integration -q
# Ran 16 tests — OK

git diff --check
# PASS
```

- FULL_READ：PASS；UTF-8 字节与 DB 原文相等，包含粗体、链接、表格、15/20 字号、未知/非法 directive、长文本、空文，version 与既有 token 一致。
- REDACTED_HIDDEN_NOT_FOUND：PASS；redacted、hidden、他 actor only、已删除、不存在及无修改能力均拒绝；redacted 不依赖脱敏规则是否存在。
- READ_NO_MUTATION：PASS（指笔记及正文读取路径）；revision、updated_at、version、content_markdown 不变，不调用 renderer/canonicalizer。
- 审计口径冲突明确保留：施工书同时要求 ai_call_logs 和“DB 无写事务”。既有审计必然写库，因此不能声称完整调用绝对零 DB 写事务。测试证明 audit stub 下读取不能打开 write；真实 audit 下唯一写事务仅 INSERT ai_call_logs，无任何笔记写入，也不记录正文。未为此改造审计或存储。
- MCP schema 无顶层 anyOf/oneOf，只有 note_id 输入；调用方不能传入修改能力开关。
- 修改能力沿用 profile 中 prepend/append/metadata 任一能力，且 read_only 必须为 false；create-only 或 read-only 不授予。受限 allowlist 不自动增加新工具，Gale/Clawbot/Yan profile 范围未扩张。
- 回归初次发现两处历史换行断言过时；按现有正式实现更正后同组测试全绿。运行中有依赖库 lifespan forward-reference 警告，不影响结果，未扩修依赖。

## 真人验收与部署

CLIENT_DISCOVERY=HUMAN_GATE。本机存在 claude 可执行文件，但本次没有运行已认证真实 Claude Code 会话进行 discovery；临时 DB 的标准 MCP stdio 测试不是该验收的替代。

待 S：在有 Notes 修改能力、允许新工具的本地 Claude Code 会话刷新工具，确认发现 get_note_markdown；读取一篇含粗体/链接/字号的 full 笔记确认原始 Markdown；读取 redacted 确认 not_found。不得仅凭本报告将真人验收标 PASS。

本步施工书明确 build=否，未构建 App。无部署、无 VPS 变更、无 push、未 merge main。未触及 canonicalizer、edit_note、AI Center 写入、Gale、Reminder 或 GreenCloud 实现。

NEXT=STEP_2_NOT_STARTED
