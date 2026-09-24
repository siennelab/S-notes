# GreenCloud 3.5 Step 2E｜当前权限复查与 prepend / metadata 接受

分支：`codex/gc35-step02a-roundtrip-scan`。接手 HEAD：`6dbe1ea0ad1519f82661c287151f4bed1c8b98b7`；开始时工作区 clean，main / origin/main 均为 `fbbd51e4504893b2b058aa55544348f30b3297dc`。

施工中途因额度中断后继续接手：HEAD 未变化，6 个 tracked 修改与 4 个新增文件均属于本轮 Step 2E，全部保留并在原现场完成；没有 reset / restore / stash 或重做。

## 本轮行为

- pending append / prepend / metadata 共用既有 receipt 生命周期与接受事务。Accept 时读取当前 Note 与 actor 授权，不能使用提案创建时存下的 `ai_visibility` / `ai_targets` 作为授权依据。
- 正文接受复用 Step 2C `build_canonical_note_candidate` 与 bundled Milkdown，重新基于当前正文构造最终结果。preview 不缓存另一份用于直接落库的正文；事务重新核对版本、原正文与提案状态。
- metadata 只接受存储 payload 中的 `title / directory / tags`；校验 JSON、字段及类型。`ai_visibility / ai_targets` 和其他字段拒绝；不信任 pending DB 内容。AI Center 表单的权限字段不会成为 metadata 更新参数。
- metadata 不 canonicalize、不更改 `content_markdown`，legacy 正文不阻塞 metadata 接受。复用 direct modification 的 metadata 存储逻辑、标签处理与已有单调时间 helper。
- 当前授权沿用正式规则：Note 未删除，可见性为 full 或 redacted，actor 为正式 registry 身份，且在当前目标集合中或获得 common 授权。无新增授权能力。
- 成功接受在同一事务更新 Note、history、receipt 与 operation 状态。失败使用已有 rejected / conflict 语义，正式 Note 零修改；已处理请求不会再次应用修改。
- receipt 失败终态为既有 `rejected`，`result_status` 与 operation 记录保存 `rejected` 或 `conflict`；保留现有异常通知 UI 的方式，失败后刷新卡片。失败收尾不覆盖已完成或属于其他提案的 operation。
- 原 append repository 入口转发到同一受检查事务；没有保留绕过当前权限检查的旧实现。缺少 request_id 的修改提案拒绝，不会落入 create 分支。

## AI Center

prepend 使用与 append 相同的完整 canonical final diff、legacy 规范化提示和预览成功后才能接受的按钮规则。metadata 卡片仅展示提案中的资料字段，不显示正文 diff 或可修改权限控件；接受与丢弃沿用现有卡片及生命周期。

## 验证范围

仅使用现有 `.venv/bin/python`、临时 HOME / S_NOTES_DATA_ROOT 与虚构 fixture DB。源码/UI 测试不读取正式用户 DB；Qt 使用正常 close / QTimer 退出。没有安装依赖、重建 bundle 或替换正式 App。

限定回归包括 Step 2E 新服务/UI 测试、Step 2C 写路径与预览、Step 2D invariant/timestamp、直接相关 receipt 与 AI Center 生命周期。旧的 prepend unsupported 断言已改为实际接受与版本/正文验证，未删除该覆盖。

关键负例独立验证权限与版本：在 fixture 直接删除 actor target，保持 Note revision / updated_at / token 不变，append / prepend / metadata 接受仍拒绝。另在 Milkdown 序列化期间把当前 visibility 改为 hidden，验证最终写事务仍检查最新权限。目录提案的目的目录当前也必须对 actor 可用。

幂等检查既覆盖 accepted / rejected receipt 的重复接受，也覆盖 operation 已成功但 receipt 被 fixture 改回 pending 的情况：正文/metadata 不再应用，history 只有一条，原成功 operation 保持不变。metadata 的 legacy 正文按字节比较，并用禁止调用 canonicalizer 的桩验证纯 metadata 路径。

最终限定合跑 **68/68 PASS**（24.744 秒），退出码 0：

| 测试模块 | 数量 |
| --- | ---: |
| `test_gc35_step02e_acceptance` | 15 |
| `test_gc35_step02e_ui` | 4 |
| `test_gc35_step02c_write_paths` | 11 |
| `test_gc35_step02c0_preview` | 6 |
| `test_gc35_step02d_invariants` | 9 |
| `test_gc35_step02d_timestamps` | 3 |
| `test_package4_ai_receipt_inbox_core` | 14 |
| `test_package4_ai_center_receipt_lifecycle_ui` | 6 |

以上八个 `tests.` 模块以 `.venv/bin/python -m unittest ... -v` 一次合跑。运行前设置临时 HOME / S_NOTES_DATA_ROOT、`QT_QPA_PLATFORM=offscreen` 与 `QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu`。沙箱内 QtWebEngine 启动曾 exit 134；获准在沙箱外执行同一限定测试后通过。Qt 离屏插件警告未影响测试与正常退出。`git diff --check` 通过，未跑全项目 suite。

```text
STARTING_UNCOMMITTED_WORK_PRESERVED=YES
APPEND_PERMISSION_RECHECK=PASS
PREPEND_ACCEPTANCE=PASS
PREPEND_STALE_ZERO_WRITE=PASS
PREPEND_REVOKED_ZERO_WRITE=PASS
METADATA_ACCEPTANCE=PASS
METADATA_BODY_UNCHANGED=PASS
METADATA_STALE_ZERO_WRITE=PASS
METADATA_REVOKED_ZERO_WRITE=PASS
METADATA_VISIBILITY_TARGETS_BLOCKED=PASS
IDEMPOTENCY=PASS
REVISION_UPDATED_AT_VERSION=PASS
TARGETED_TESTS=68/68 PASS
FORMAL_DB_MUTATED=NO
MERGED=NO
PUSHED=NO
```

本轮不 build / deploy，不做人类 UI Gate，不进入 Step 3，不改 canonicalizer / token / schema，不新增表，不批量改 Notes，不改 Gale / Reminder / GreenCloud；不 merge、不 push。

NEXT=WAIT_FOR_S_G_REVIEW_BEFORE_STEP2_APP_GATE
