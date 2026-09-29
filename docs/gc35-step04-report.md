# GreenCloud 3.5 Step 4：Astra review repair

- Branch：`codex/gc35-step04-actor-mirror`
- 正式施工前基线：`46a02082f698f1989defa3f80530edf03120a6d1`
- Step 4 原 commit：`830966b4fefceb36f1ea2e2f907201c14ac2fb7a`，保留历史。
- 原 Astra review：`ASTRA_STEP04_REVIEW=NEEDS_FIX`，`STEP05_SAFE_TO_PROCEED=NO`。
- Repair commit：本报告与代码同一新增提交（主题 `Fix Step 4 actor mirror snapshot, rendering and validation`）；交付时为本分支 HEAD。可用 `git log -1 --format=%H -- docs/gc35-step04-report.md` 定位，不写自指 hash、不 amend 原 commit。
- 本轮状态：`STEP04_REPAIR=PASS`；等待 S / G 与 Astra 第二次只读复检，不宣布 Step 4 CLOSED。

## 四项修复

1. **授权快照**：`SQLiteNotesRepository.list_active_with_ai_access()` 在同一连接显式 `BEGIN`，全部正文、visibility、grants、updated_at 来自同一读事务。WAL fixture 在读正文与读 grants 之间原子提交 `clawbot/full → claude-code/redacted`，验证两 actor 的旧快照与新快照结果均合法，Claude 不会拿到旧 full 秘密。
2. **只解析一次**：`AiSafeReadService.read_markdown_note()` 在现有授权/脱敏 owner 内指定 canonical Markdown renderer，复用同一个 `_read_materialized`，仅一次转换后脱敏。actor service 不再将 plain text 重新送入 renderer。inline code、链接样式字面文本、转义星号、size 方言及脱敏命中均验证；redacted 的 `safe_markdown` 仍为 NULL，full 保留原 Markdown。
3. **完整结构校验**：SQLite writer 的 validator 用当前 actor contract 在内存 SQLite 建参照，对比完整 `sqlite_master` DDL（包括表、列名/类型/约束、索引、FTS shadow），拒绝额外列、额外对象和缺失索引；显式校验合法 source_schema。继续核对 actor、count、FTS、payload hash。五个独立负例分别破坏额外列、index、source_schema、actor、payload，每个都确认已有良好 mirror 字节不变、candidate 清理。
4. **源库覆盖保护**：application 在读取/写入 mirror 前调用 storage source port 的路径检查；通过 resolved path 与 samefile 拒绝同路径、规范化路径、硬链接和符号链接 alias。测试断言 writer 未被调用，源库 schema/内容和字节保持原样。

实际 owner 仍为 `ActorMirrorService` → `AiSafeReadService` / `ActorMirrorSource` / `ActorMirrorWriter`。正式 registry、现有 authorization 与 redaction owner 均复用；正式 Notes schema 无改动。

## Gale Production 回归

未修改 Gale generator / writer / validator / consumer。actor mirror 继续使用独立 contract，避免改变 Gale 文件结构。原报告将 Gale validator 描述为完整 schema 精确校验不准确：既有实现检查表/索引与 metadata，但并非精确逐列校验；独立 schema 的依据是保持其现有对外结构不变。

新增 `tests/test_gc35_step04_gale_production.py` 与固定 fixture runner：

- `git archive` 将基线 `46a02082…` 解到系统临时目录；不用正式 Notes DB。
- 同一 fixture、同一现有 `.venv` 解释器、隔离进程及临时 HOME，分别运行基线与当前 `application.gale_service.GaleMirrorService` + `storage.gale_mirror.SQLiteGaleMirrorWriter`。
- fixture 包括 common full、Gale redacted、clawbot-only、hidden。
- 对比 SQLite schema、全部 mirror_notes、FTS、目录/标签/visibility/targets catalog、metadata、生成和校验语义结果。仅忽略自然变化的 generated_at、文件 hash/输出路径；固定 note 时间保证 source_version 可比。
- 结果完全一致。旧 legacy Gale 测试保留执行，但不再作为唯一 production 回归证据。

## 验证命令与结果

实际使用仓库 `.venv`，以下包装器为测试和 architecture 创建临时 HOME（测试数据库全部为虚构临时 fixture）：

```sh
.venv/bin/python -B - <<'PY'
import os,sys,tempfile,subprocess
with tempfile.TemporaryDirectory(prefix='snotes-step04-repair-home-') as home:
    env=dict(os.environ,HOME=home)
    modules=['tests.test_gc35_step04_actor_mirror','tests.test_gc35_step04_gale_production','tests.test_gale_mirror_export','tests.test_package4_ai_actor_registry','tests.test_package4_ai_access_metadata_core','tests.test_ai_write_authorization','tests.test_package4_ai_safe_read_core']
    for args in ([sys.executable,'-B','-m','unittest',*modules,'-v'],[sys.executable,'-B','scripts/check_architecture.py']):
        result=subprocess.run(args,env=env)
        if result.returncode: sys.exit(result.returncode)
PY
git diff --check
```

- **51 tests PASS**：原 41 项保留，新增 9 个 actor repair tests（含多组 subtests）+ 1 个正式 Gale baseline regression。
- **ARCHITECTURE_CHECK=PASS files=95 warnings=20**；gate 未修改、无白名单、无 skip/expectedFailure。20 条提示仍为已有大文件和 owner 启发式提示，未以 PASS 掩盖警告。
- `git diff --check` PASS。
- 本轮交付为源码修复及 fixture 验证；未重建或部署 App，不声称已验证安装包。

## 范围与交付

本轮改动为 actor service、safe-read owner 的单次 Markdown 入口、application ports、actor SQLite validator、Notes repository 的 snapshot/path guard、两组 Step 4 测试及 fixture runner、本报告。

仍未上传 GreenCloud；未 SSH/SCP、未碰 VPS、未进入 Step 5；未 merge、未 push；未扩展 AI write/AI Center、Reminder 或 Cloud 功能。S 原有六份 untracked 用户文档保留，不 stage、不修改、不删除。没有提交真实数据库、mirror 二进制、凭据或临时施工产物。
