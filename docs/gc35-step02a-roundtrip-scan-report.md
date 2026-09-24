# GreenCloud 3.5 Step 2A｜正式 Notes 只读 round-trip 扫描

## 范围与结论

扫描日期：2026-09-23（America/Los_Angeles）。基线：`fbbd51e4504893b2b058aa55544348f30b3297dc`；分支：`codex/gc35-step02a-roundtrip-scan`。

**84 篇未删除 Note：4 篇 ZERO DIFF，80 篇有字节差异；加载错误 0。正式 DB 的正文、revision、updated_at 均未变化。** 这是格式现状盘点，不是坏数据判定，也不是 canonicalizer 验收。报告不包含任何正式笔记正文。

| 互斥粗分类 | Note 数 |
| --- | ---: |
| Markdown escaping 相关 | 46 |
| 其他／未自动归类 | 20 |
| list / heading normalization 相关 | 5 |
| trailing / final newline | 5 |
| link normalization 相关 | 3 |
| `:size` directive 相关 | 1 |
| blank-line / paragraph spacing | 0 |
| **DIFF 合计** | **80** |

分类是保守的粗粒度启发式，每篇有差异的笔记只计入一个桶：先判断仅末尾换行，再判断空行数量，其后仅检查发生变化的行中的字号、转义、列表／标题、链接标记，无法归类就记 other。相关标记出现在变化行上，不代表已经证明它是差异的唯一原因；0 也不代表其他桶内完全没有伴随空行变化。未另造 Markdown parser。

第一次序列化后再次 load → serialize，有 **1 篇仍不稳定**，其余 83 篇稳定。这是既有检查器自然提供的附加事实；本步未追查正文、未修复，也不能把本次扫描成功理解为所有正文都通过幂等 Gate。

## 三项关注点

### Append 换行

仅使用独立的虚构探针，不执行 append 写入：

- 输入 `base\n-end`，第一次输出仅补末尾换行。
- 输入 `base\n\n-end`，第一次输出也仅补末尾换行。
- 两种输出仍不同，内部单／双换行差异被保留；各自第二次序列化稳定。

因此本次 Milkdown 事实并没有把两种存储形态合并成同一个输出。**不决定 append 应采用哪一种，不改写路径和历史测试预期。** 已知 mismatch 继续 deferred to Step 2。

### Note 318

Note 318 本次 **ZERO DIFF**。简单存储形态计数：

- 未转义的 `:size[` 起始标记：0。
- 未转义的裸 `:size` 标记：0。
- 转义的 `\:size` 标记：2。
- HTML entity 编码冒号的 size 标记：0。

这说明扫描见到的是含转义 size 文本的存储形态，Milkdown round-trip 原样保持。本步未调用或修改普通 `get_note` 的派生 parser，也不把这一观察扩大为该现象的完整根因结论。

### 历史转义文本

按 `\#`、`\*`、`\[`、`\]` 字符形态筛出的 cohort 共 **49 篇**，其中 **47 篇 DIFF、2 篇 ZERO DIFF**。此 cohort 与上表的 46 篇 escaping 分类不是同一指标：前者看原文是否含这些标记，后者看变化行的分类。

这些计数不能证明转义过度、文本损坏或语义错误；未自动反转义，也没有判定这些内容应被修正。

## 只读方法与未修改证据

- 正式来源：`/Users/s/Library/Application Support/S-notes/s_notes.db`，由现有正式数据路径约定定位。
- 源连接使用 SQLite URI `mode=ro`，并开启 `PRAGMA query_only=ON`，未调用 App bootstrap 或数据库初始化。
- 在源库同一只读事务中计算基线摘要，再用 SQLite backup API 创建系统临时目录内快照。快照文件设为 `0400`，扫描连接同样 `mode=ro` / `query_only`。
- 只扫描快照中 `is_deleted = 0` 的 `content_markdown`。原文和序列化结果只在内存处理；不写正文 JSON、导出或 diff。临时快照与临时 HOME 在正常退出时自动删除。
- 复用 `scripts/markdown_roundtrip_check.py` 的 `check`、独立 `MarkdownEditorWidget`、离屏 QtWebEngine 和正常 close / QTimer 退出路径。该独立组件不含 NoteService 或持久化逻辑。没有连接任何保存正文的 application 路径。
- 扫描后重新打开正式库只读连接，比较 **所有 notes（包括已删除）** 的 `id / content_markdown / revision / updated_at / is_deleted` 有序摘要。

扫描前后 SHA-256 均为：

```text
0772049089d335e648b2b5f3c39a0542d38bf5144244e2aa0013cd6943f8f9f5
```

快照摘要与扫描前源摘要也一致。这里证明的是所检查笔记字段的前后状态一致，不宣称整个 SQLite 文件及其运行时 sidecar 的物理字节完全不变。

## Bundle 与验证

项目正式 `web/markdown-editor/dist` 与 `dist/S-notes.app/Contents/Resources/web/markdown-editor/dist` 的三个资源文件逐一 SHA-256 相同；本次直接使用项目正式 bundle，未重新 build，也未修改编辑器。

| 文件 | SHA-256 |
| --- | --- |
| `index.html` | `f5668f096a6d2c0059e4a2bd2fc97ff0f9eafddeae8ee2ac9a6fb3b6022cd4c2` |
| `assets/editor.js` | `3b5c3b59205b46b7af1965321a1ca0fbd0652092c9fce1b8754a1e7a6dcc2d06` |
| `assets/editor.css` | `23e94b4fd2b40c27b9d0d92115a7b6fe5e0735f48e0cdbdd5569b5891f18a16d` |

相关验证：

1. 临时 HOME 下 `.venv/bin/python -W error::ResourceWarning -m unittest tests.test_gc35_step02a_roundtrip_scan -v`：**1/1 PASS**。临时 fixture DB 验证 ZERO/DIFF 计数、已删除排除、正文/revision/updated_at 不变、报告不含正文，以及只读连接拒绝 UPDATE。
2. 新扫描脚本 + 真实 Milkdown + 临时 fixture DB smoke：3 条记录中 1 条已删除；实际扫描 2 条，**1 ZERO DIFF、1 final-newline DIFF**，加载错误 0，前后摘要一致。
3. 正式扫描：**退出码 0**，84 篇全部完成；前后摘要一致。未运行无关项目测试。

正式扫描命令：

```sh
.venv/bin/python scripts/markdown_roundtrip_scan.py \
  --database '/Users/s/Library/Application Support/S-notes/s_notes.db' \
  --output /tmp/snotes-step02a-formal-result.json
```

macOS 沙箱内现有离屏脚本曾 exit 134；同一脚本在获准的沙箱外执行后 fixture 和正式扫描成功。只使用现有 `.venv`，未安装依赖、未改 Qt 或编辑器。Qt 的图形 backend fallback / resource path 警告未阻止加载与正常退出。

## 交付边界

本步仅新增：

- `scripts/markdown_roundtrip_scan.py`
- `tests/test_gc35_step02a_roundtrip_scan.py`
- `docs/gc35-step02a-roundtrip-scan-report.md`

未修改正式笔记、生产实现、canonicalizer、任何写路径、AI Center、MCP contract、Gale、Reminder 或 GreenCloud。未决定任何存量数据修复方案。只在 task branch 提交，不 merge、不 push。

FORMAL_DB_MUTATED=NO

NEXT=WAIT_FOR_S_REVIEW（2B_NOT_STARTED）
