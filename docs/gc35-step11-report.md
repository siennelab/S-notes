# GreenCloud 3.5-D / Step 11 — Mac review, receipts and mirror refresh

**STEP11=PASS · S_E2E=PASS · 3.5-D=CLOSED · GREENCLOUD_3.5=COMPLETE**

S 于 2026-10-04 明确回复「真人 E2E PASS」。本报告记录 Step 11 实现、既定 v2.2 §58 的 26 条验收、正式 App、实际登录 helper 和 fixture 清理结果。3.5 主线至此完成；prepend / update_note_metadata 仍属于独立后续施工单。

- Branch：`codex/gc35-step11-mac-receipt-mirror-refresh`；实现与本报告一起提交，最终 commit 以该 branch 的 HEAD 定位。
- Step 10 已 ff-only 合入 main，main 保持 `a4ac06d50ea6de23a27dbb89a56e0a50809db2fa`；没有 merge commit。
- origin/main 的本地引用保持 `90cb115f3866759c32987bc636c81edd322ef921`。
- Step 11 未合入 main，`PUSH=NO`。没有新增公开目的地或更改可见性。

## 最终行为

复用同一张 `ai_receipt_inbox` 和既有正式 Notes 接受事务。Oracle Gale 的 export / ACK / 既有接受行为保持；GreenCloud 通过 Step 10 已部署的三个窄 SSH 子命令 export、acknowledge、receipt 接入，没有新 outbox 表、第二套 lifecycle 或新的 Cloud runtime 部署。

导入保留 source、actor、原始 Cloud request_id 和 expected_version。Mac 本地 operation ID 使用 source / actor 隔离的命名空间，允许两个 actor 合法复用相同 Cloud request_id；回传仍使用原始 ID。ACK 必须在本地导入事务之后，重试只比较不可变 proposal 身份，不受已接受内容、目录选择或后续 Note 版本变化影响。

AI Center 显示 Oracle Gale / GreenCloud、actor、operation、proposal / diff 和状态。create / append / edit 接受复用正式 application/storage owner，重新检查当前 Note、删除状态、actor 权限、visibility、version、request 执行状态和 edit 的唯一匹配；canonicalize 成功后才提交 revision、updated_at 与 history。失败正文零写入，保存 rejected / conflict，未实现自动 merge 或 batch accept。批量 reject 限定一个 source 和一个 actor。

成功接受 Cloud proposal 后，顺序固定为：

```text
Mac Notes accept commit
→ publish_actor(origin_actor)
→ accepted terminal receipt to GreenCloud
```

只调用既有 publisher 的对应 actor 入口。publish 或 receipt 失败不回滚 Note；现有 processed inbox row 中的传输标记保存待重试决定。receipt 成功后才记录发送 fingerprint，丢失响应可幂等重试。rejected / conflict 不触发正文镜像发布。尚未同步的终态不允许通过已处理删除入口丢掉。

正式 App 首次打开使用异步 pull worker；登录 pull 使用实际安装于 Application Support 的 helper。窗口正常关闭等待正在运行的 pull / follow-up worker 完成，避免销毁仍运行的 QThread。隔离 data root / 临时 HOME 检查不会触发生产 pull。

## 自动验证

新增 Step 11 tests 共 **32/32 PASS**：

| Suite | 数量 | 关键边界 |
| --- | ---: | --- |
| `test_gc35_step11_pull` | 8 | transport envelope、固定 SSH 命令、先导入后 ACK、失败与重复导入、双来源互不阻塞、原 Gale-only 行为 |
| `test_gc35_step11_inbox` | 11 | create once、full edit、redacted append / edit、撤权 / 删除 / stale / 非唯一匹配、canonicalize、ZERO DIFF、终态删除保护、source/actor bulk reject |
| `test_gc35_step11_followup` | 6 | 对应 actor publish 先于 receipt、下一次 Cloud read、新终态与失败重试、响应丢失幂等、同 request 的两 actor / 两 source 隔离 |
| `test_gc35_step11_ui` | 4 | source / actor / operation / proposal 展示、bulk reject 范围、冲突保留当前正文、后台 follow-up 与重试提示 |
| `test_gc35_step11_startup` | 3 | 隔离环境禁止 live pull、启动不阻塞 UI、正常关闭等待 pull / follow-up |

与本步直接相关的兼容结果：

- Oracle Gale 直接回归 **23/23 PASS，正常 Cocoa 环境退出 0**；沿用本次施工已确认的基线，没有因账号接续重复运行。
- 原 Gale AI Center wiring **3/3 PASS**，使用其原 offscreen 测试口径。
- Step 10 Cloud proposal / status / allowlist **8/8 PASS**。
- 既有 actor publisher **5/5 PASS**；未重复计算早期与 startup 合跑的 7 个 tests。
- `git diff --check` PASS。

Gale UI 在 Cocoa 下的三个尺寸断言失败已用 Step 10 main 同一 UI 模块复现；原 offscreen suite 通过，正式 App 与真人界面通过。本步没有扩成 sidebar / 布局修复。曾使用缺少 settings 方法的 startup fake 导致的失败已修正，最终 startup 3/3 正常退出。

所有自动 source / Qt / App 检查使用既有 `.venv`、临时 HOME 和虚构数据库；没有用真实用户数据库运行自动检查。真人准备和验收只针对本轮专用虚构 Note / proposal，没有打印真实业务正文或凭据。

## v2.2 §58：26 条结果

下表区分本步直接验证与继承 Step 10 已封口的证据。Step 11 没有更改 Cloud runtime、actor UID、ACL、queue limit 或 Cloud precheck，未为重复验证重部署。

| # | 验收项 | 结果与证据 |
| ---: | --- | --- |
| 1 | Mac offline 可 queue | PASS；暂停原 Mac pull 且 App 关闭，真实 claude-code MCP 三条 create / append / edit 均 queued，恢复后 ACK 并显示 |
| 2 | duplicate request 不重复 | PASS；Step 10 actor-scoped 幂等基线；Step 11 ACK retry、已处理后的 import retry、create once 与 lost receipt response tests |
| 3 | 每 actor >50 non-terminal 为 queue_full | PASS（继承）；Step 10 outbox queue-cap test 与真实 actor 的隔离 fixture smoke，见 Step 10 报告 |
| 4 | actor bulk reject 清 backlog | PASS；inbox 与 UI tests 验证仅同 source / 同 actor，其他 actor 和 Gale 不受影响 |
| 5 | Cloud create 不直接入正式库 | PASS；import 前后正式 Note count 不变；只有 S 点击接受后创建 |
| 6 | pull 后进入 AI Center | PASS；正式 helper / App 实际拉取，AI Center 显示三条 GreenCloud 与随后一条 Oracle Gale |
| 7 | payload 不能冒充 actor | PASS；Step 10 application / HTTP / MCP spoof 基线；Step 11 transport 拒绝非法 actor / envelope |
| 8 | create accept 仅一篇 | PASS；重复接受返回同一 Note；真人 CREATE 接受后仅对应一篇虚构 Note |
| 9 | append expected_version | PASS；accept 当前版本重新检查，stale 零写入；真人 APPEND accepted |
| 10 | edit expected_version | PASS；full edit 正式事务、stale tests；真人修改 Note 后接受产生 conflict |
| 11 | redacted append | PASS；inbox transaction test 接受并正常 bump / history |
| 12 | redacted edit 拒绝 | PASS；accept 重新检查 full，保存 rejected，正文零写入 |
| 13 | output canonicalize 后落库 | PASS；真实 Milkdown source / 正式 App smoke；失败 / 空 canonical 内容无正式写入 |
| 14 | stale conflict | PASS；撤权、删除、非唯一匹配与 stale tests；真人 conflict，S HUMAN CHANGE 保留且候选未写入 |
| 15 | reject 后正文零变化 | PASS；inbox / follow-up tests；真人 EDIT rejected 后版本未变，后续手动修改与冲突另行核对 |
| 16 | terminal receipt 回 Cloud | PASS；实际 accepted / rejected / conflict 回传；本地所有四条均有发送 fingerprint |
| 17 | get_operation_status | PASS；固定 claude-code MCP 查到两条 accepted、一条 rejected、一条 conflict，原始 request_id 对应正确 |
| 18 | accepted immediate mirror refresh | PASS；follow-up 顺序 test、本地两个 accepted 的对应 actor publish 标记；rejected / conflict 无 publish 标记 |
| 19 | 下一次 Cloud read 读新 current.db | PASS；follow-up test 与真实 MCP 读取 CREATE / APPEND 的新标记，Markdown 与 plain text 都正确 |
| 20 | Cloud AI 无 formal / outbox DB 文件权限 | PASS（继承）；Step 10 两个真实 UID 对 DB、sidecar、backup 的读写 denial smoke；本步无权限变化 |
| 21 | Mac 保留 source | PASS；repository / UI tests与正式 App，Oracle Gale 与 GreenCloud 清楚区分 |
| 22 | receipt 原路返回不串状态 | PASS；同 request 两 actor、两 source tests；实际 Cloud 原 request 回传，Gale 仍只用旧 ACK，不添加 terminal 协议 |
| 23 | 两来源相同 external ID 不误去重 | PASS；pull / inbox / follow-up source-scoped identity tests |
| 24 | Gale 投稿链回归 | PASS；23/23 code regression、3/3 UI regression、真实 Oracle 投稿 / ACK / S 接受成功 |
| 25 | prepend / metadata 仍被拒绝 | PASS；Step 10 MCP / HTTP allowlist 8/8 相关 suite；本步不增加这些操作 |
| 26 | Oracle outbox 未迁走或共用 | PASS；真实 Oracle owner 仍使用原 production outbox，fixture 前后与清理后 schema=2；Cloud 单独仍 schema=3 |

## 真人 E2E

S 先回复「前三条已处理」，随后按最后两项操作完成并明确回复「真人 E2E PASS」。实际过程：

1. Mac pull 暂停时，以真实 UID 固定的 claude-code MCP 准备 create、append、edit 三条虚构 proposal，确认 queued。
2. 恢复原登录 helper / 打开正式 App；三条进入 AI Center，source=GreenCloud、actor=claude-code、operation 正确。
3. S 查看 proposal / diff，接受 CREATE 和 APPEND，拒绝 EDIT。
4. Cloud 状态为 accepted / accepted / rejected；accepted 后的下一次 read 已包含新内容，EDIT 原版本未变。Markdown canonicalize 会转义标记中的下划线，核对时同时使用解转义后的标记和 safe plain text。
5. 使用 Oracle 已有 production owner 准备一条 Gale 虚构投稿；AI Center 显示 Oracle Gale / gale，S 接受成功。没有部署、迁移或修改 Gale wire contract。
6. 新提交 Cloud EDIT proposal 后，S 在 App 中给同一虚构 Note 新增 `S HUMAN CHANGE` 并保存，再尝试接受。UI conflict，Cloud terminal=conflict；Mac 只读核对确认手动文字和原内容保留，冲突候选未写入。

Mac 是正文 authority。conflict 不发布正文变化，因而其 Cloud mirror 无须立即包含 S 的手动编辑；本轮手动文字的保留直接核对了 Mac 上该专用虚构 Note。

## 正式 App 与登录 helper

- 按 `scripts/build_macos_app.sh` 构建正式 `/Users/s/Documents/项目/S-notes/dist/S-notes.app`，没有另造最终 candidate。
- 既有 PySide wheel 中辅助 `Versions/Resources` 被 hook 误认成框架 version；只在 spec 收集阶段把 QtWebEngine helper / resources 放回真实 `Versions/A`。没有修改、重建或升级 `.venv`。
- `BUILD SUCCESS`，`codesign --verify --deep --strict` PASS（ad-hoc），arm64。
- 从上述正式路径执行 `--step11-smoke`：临时 HOME / data、PATH 无 Node，真实 Milkdown create once / append / edit 与 source / actor UI smoke PASS，退出 0；随后 S 使用同一正式 App 做真人 E2E。
- 本 App 在 commit 前构建，实际 provenance 是 `SNotesSourceCommit=a4ac06d...`、`SNotesSourceDirty=YES`。它承载本报告所述 Step 11 工作树；真人验收后没有改变产品源码，不为改 provenance 重做已验收产物。
- App executable SHA-256：`498c4e11a9588b0dc6df96573dc913b67c975729193e458b952585bd8217256e`。
- installed helper 与 bundle 内 helper SHA-256 均为 `1d6a8d871615cd86b3240897d4b6ff1fb837104f50a07e0159a535bc2d506359`，版本 `snotes-gale-production-pull 1`，严格签名验证 PASS；真实 pull 退出 0，三条真实 fixture 已 ACK。
- 原实际加载的 LaunchAgent 原样恢复，ProgramArguments / 启动方式不变；两个相关 plist 文件 SHA-256 前后不变。没有重设计登录机制。
- 旧 helper 小范围 rollback 保留于 `~/Library/Application Support/S-notes/backups/gc35-step11-helper-084f67cf/`。

## 清理、runtime 与 Git 收口

真人验证后通过现有 Note service 把四篇虚构 Note 和空测试目录移入应用回收站；没有清空回收站、永久删除 Note 或改变真实业务笔记。已处理回执按既有查账 / idempotency 规则保留。通过既有 claude-code publisher 与 Gale publisher 撤下测试镜像内容，Cloud MCP 对三篇对应虚构 Note 已返回 not_found。

只对本轮精确 UUID / actor / request / 预期终态的 queue rows 做小范围备份和事务清理：

- GreenCloud：移除 4 条，fixture=0，production outbox 总记录数=0，schema=3，`snotes-notes-outbox.service` active；四个已知 request 的真实 MCP status 均 not_found。小范围 rollback：`/srv/snotes/data/step11-fixture-084f67cf.json`（仅虚构行，0600）。
- Oracle：只移除本轮 1 条已 ACK 虚构行，fixture=0，其余 34 条原记录保留，schema=2。小范围 rollback：`/var/lib/snotes-gale-r1b-production/step11-fixture-084f67cf.json`（仅虚构行，0600）。
- 本步没有 Cloud runtime 部署、Oracle runtime 变更、Oracle schema migration 或新增权限。没有生成远端临时 transport 安装文件；本机一次性准备脚本、测试副本与无长期价值材料清理，以上必要小范围 rollback 保留。

源码、tests 与本报告提交于 Step 11 branch，build artifacts、真人数据、备份、私密配置与凭据不入 Git。最终 worktree clean；main 和 origin/main 本地引用保持上述值，未 merge Step 11、未 push。

`3.5-D=CLOSED`、`GREENCLOUD_3.5=COMPLETE`；到此收工。最终 ff-only 合并 / push 留待 S / G 后续明确授权。
