# GreenCloud 3.5 Step 2D｜timestamp blocker 修复与版本 invariant 审计

分支：`codex/gc35-step02a-roundtrip-scan`。接手基线：`dbb2f187d601df75204887fce86703b5fd68490e`，开始时工作区 clean；main 保持 `fbbd51e4504893b2b058aa55544348f30b3297dc`。

## 根因与窄范围修复

实际 GUI `MainWindow.save_current_note` 先调用 `NoteService.save_note`，再调用 `TagService.set_note_tags_from_text`。标签发生变化时进入 `SQLiteTagsRepository.set_for_note`；标签未变化时 service 返回、不写入。

正文 `SQLiteNotesRepository.save` 已使用 `next_note_updated_at`，在 `BEGIN IMMEDIATE` 事务内读取当前时间戳，保证新值严格晚于它，并输出微秒精度。标签路径却直接使用 `timestamp_seconds(clock)`，舍弃微秒且不比较当前 Note 时间戳。

临时 fixture 的修复前回归重现：初始 `09:00:00` → 正文 `09:00:00.123456` → 标签 `09:00:00`。正文仍是新内容，revision 仍从 1 → 2 → 3，但 token 回到 V1。

源码排查还发现两个现实可达的同类入口：GUI `_ai_visibility_changed` / `_ai_targets_changed` → `AiAccessMetadataService` → `SQLiteNotesRepository.set_ai_visibility` / `set_ai_targets`。二者原先同样使用秒精度时间，固定时钟测试也在修复前失败。

修复 commit：`e2df741`。仅把上述三个 mutation 入口改为在既有写事务内读取 Note 当前 `updated_at`，调用已有 `next_note_updated_at`。保留每次成功 mutation 原有 revision +1、标签归一化、权限元数据语义和事务回滚行为。未改 helper、token 算法、MCP expected_version 协议、schema 或 migration。

修复后原复现序列为 `09:00:00` → `09:00:00.123456` → `09:00:00.123457`，三个 token 两两不同。连续 metadata 更新即使 wall clock 不动或回拨，仍严格递增。

## Active BODY_WRITE 路径

| 入口 | 实际存储路径 | revision / updated_at / token |
| --- | --- | --- |
| AI direct create | `LocalMcpWriteService.create_note` → `SQLiteAiMcpOperationRepository.create_direct` | 同事务 INSERT 正文、revision=1、初始 timestamp；生成当前 token |
| create acceptance | `AiReceiptInboxService.accept_receipt` → `SQLiteAiReceiptInboxRepository.accept_pending` | 同事务 INSERT 正文、revision=1、初始 timestamp；生成当前 token |
| direct append | `LocalMcpWriteService.append_note` → `modify_direct` → `_write_content_in_connection` | 正文 UPDATE 同时 revision +1，使用当前 Note 的下一时间戳，token 改变 |
| direct prepend | `LocalMcpWriteService.prepend_note` → 同上 | 同上 |
| pending append acceptance | `AiReceiptInboxService.accept_receipt` → `accept_pending_append` → `_write_content_in_connection` | 同上；history 与接受状态在同一事务 |
| GUI blank/new Note | `MainWindow.create_note` → `NoteService.create_note` → `SQLiteNotesRepository.create` | 空正文 INSERT，revision=1、初始 timestamp / token |
| GUI body save | `MainWindow.save_current_note` → `NoteService.save_note` → `SQLiteNotesRepository.save` | 正文与 revision +1 / 单调 timestamp 同一 UPDATE；后续标签保存也严格前进 |

新建 Note 没有旧版本可 bump：正确语义是从不存在到 revision=1、初始化 timestamp/token；不能虚构一次额外 +1。后续正文保存验证严格 bump。GUI 路径使用真实 application/repository 调用测试，与上述源码调用链核对；本轮不做打包 App 的人工 UI 验收。

## 其他 `notes` 写路径分类

| 文件 / 路径 | 分类与审计结果 |
| --- | --- |
| `repositories/notes.py` visibility / targets | METADATA_ONLY；本轮修复，正文不变，原 revision +1 语义保留 |
| `repositories/notes.py` rename / pin / move / move_many / trash / restore | METADATA_ONLY；已有 `next_note_updated_at`，不修改 |
| `repositories/tags.py` set_for_note | METADATA_ONLY；本轮修复 |
| `repositories/tags.py` set_for_new_note_in_connection | 同一逻辑 mutation 的内部 SQL：create 调用方传入初始化 timestamp；AI direct metadata 调用方传入已经计算的单调 timestamp。与调用方同一事务、相同 timestamp，不是另一独立版本，无需额外 bump |
| `repositories/directories.py` rename_notes_for_path / soft_delete_notes_under_path / restore_notes_under_path | METADATA_ONLY；逐 Note 读取当前 timestamp，已有 helper 与 revision +1。目录记录本身的时间不是 Note version，本轮不修改 |
| `repositories/ai_mcp_operations.py` modify_direct metadata 分支 | METADATA_ONLY；已有 helper、revision +1，同事务标签使用相同 mutation timestamp |
| `repositories/ai_receipts.py` | create INSERT 如上；其余 UPDATE 修改 receipt / operation 状态，不是 Note 正文 |
| `storage/schema.py` UUID 补全 trigger | MIGRATION / SCHEMA；不写正文，不改动 |
| `scripts/migrate_markdown_body.py` | 显式 Markdown migration；不属于 active BODY_WRITE，本轮不执行、不改动 |
| `src/database.py`、`src/services/ai_access_service.py` | LEGACY / frozen R1；正式 composition 使用 `src/snotes` repositories，本轮不改动 |

永久删除回收站记录是 DELETE，不存在可保留并 bump 的 Note 版本；不改变现有语义。审计没有发现遗漏的 active BODY_WRITE 入口。

## 验证与交付边界

新增 `tests/test_gc35_step02d_timestamps.py`：GUI-like body → changed tags、tags / visibility / targets 快速连续更新与时钟回拨、未变化 GUI tags 不写入。修复前 4 个断言失败，修复后全部通过。

新增 `tests/test_gc35_step02d_invariants.py`：七条 active BODY_WRITE 路径以及代表性的 stale expected_version、canonicalizer failure、Markdown read 零 Note mutation。失败和读取路径比较整个 `notes` 表快照，包含正文、revision、updated_at；允许已有审计/receipt 状态按原协议记录。

所有测试仅使用项目 `.venv/bin/python`、临时 HOME / S_NOTES_DATA_ROOT、虚构 fixture DB。真实 Milkdown 验证使用现有 bundle 与离屏 Qt，正常 close / QTimer 退出。未读取或修改正式 DB。

限定测试共 **40/40 PASS**：

| 测试模块 | 数量 |
| --- | ---: |
| `tests.test_gc35_step02d_timestamps` | 3 |
| `tests.test_package1_storage_core` | 9 |
| `tests.test_package4_ai_access_metadata_core` | 8 |
| `tests.test_gc35_step02d_invariants` | 9 |
| `tests.test_gc35_step02c_write_paths` | 11 |

前三组以 `-W error::ResourceWarning -m unittest` 合跑，后两组以 `-m unittest` 合跑。Qt 在 macOS 沙箱内启动 abort，获得自动审批后在沙箱外执行同一限定测试通过；没有更换 Python 环境或安装依赖。另通过 `git diff --check`。未运行全项目 suite。

```text
TIMESTAMP_MONOTONIC=PASS
GUI_BODY_TAG_SEQUENCE=PASS
VERSION_TOKEN_NO_REVERSION=PASS
ACTIVE_BODY_WRITE_PATHS=7/7
BODY_WRITE_REVISION_BUMP=PASS (create initializes revision=1)
BODY_WRITE_UPDATED_AT_BUMP=PASS (create initializes timestamp)
VERSION_TOKEN_CHANGES=PASS
FAILED_WRITE_ZERO_MUTATION=PASS
READ_ZERO_NOTE_MUTATION=PASS
MISSING_INVARIANT_PATHS=NONE
TARGETED_TESTS=40/40 PASS
FORMAL_DB_MUTATED=NO
MERGED=NO
PUSHED=NO
```

不进入 Step 2E，不实现 prepend / metadata acceptance，不改 edit_note、canonicalizer 设计、正式 Notes、Gale、Reminder 或 GreenCloud；未 build / deploy / merge / push。

NEXT=WAIT_FOR_S_G_REVIEW_BEFORE_2E
