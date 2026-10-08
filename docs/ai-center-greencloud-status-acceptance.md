# AI Center GreenCloud 状态与手动控制验收记录

2026-10-08，S 真人验收 Candidate PASS，并授权 ff-only 合入 main、push、重建正式 App 和 closeout。

验收代码位于 `codex/ai-center-greencloud-status`。合入前已确认当前七个代码／测试文件的 SHA-256 与通过验收的 Candidate 构建记录一致；保留已有测试结果，不重复执行相同套件。

| 临时 HOME／虚构数据库验证 | 通过数量 |
| --- | ---: |
| `test_ai_center_greencloud_status`、`test_package6_gale_ui_wiring` | 13 |
| `test_gc35_step05_publisher`、`test_gc35_step11_pull`、`test_gc35_step11_inbox`、`test_gc35_step11_followup` | 35 |
| `test_gc35_step11_ui`、`test_package4_ai_center_receipt_lifecycle_ui`、`test_gc35_ai_center_close`、`test_gc35_step11_startup` | 23 |
| 合计 | **71 PASS** |

Candidate 的 ad-hoc codesign、打包 Step 11 smoke、实际 App 打开／关闭 AI Center、切换虚构笔记、Command+Q 正常退出均 PASS。打包后的三个修改模块与最终源码一致。隔离 Candidate 不连接正式后端；成功发布、无变化跳过、失败提示与状态刷新由虚构数据测试覆盖。

S 确认两个手动按钮、GreenCloud 最近成功发布时间、多来源投稿状态、打开应用时检查设置和 UI 均正常。

`立即检查投稿` 保留原多来源投稿与回执检查；`立即更新镜像` 使用现有 GreenCloud publisher 的 `publish_actor("gale")`。镜像状态只读 `actor_mirror_publish_state.json → actors.gale.last_success_time`，不读取 Oracle 发布时间，不强制刷新无变化内容的成功时间。

旧 Oracle 自动发布开关保持移除。GreenCloud 自动 publisher、权限筛选、投稿 Accept/Reject、receipt 链路均保持现有机制。`com.snotes.gale-production-pull` 的 loaded plist 偏差留待另票，本票不修复、重载、删除或添加 `--scheduled`。

正式 main 成品的构建、smoke、Git 与清理证据保存在 `dist/ai-center-greencloud-closeout.json`，构建产物与本机状态不进入 Git。
