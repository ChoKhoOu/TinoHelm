# Critic Review — Round 4

**VERDICT: APPROVE**

## 总体评估

Round 3 → Round 4 修订**全面兑现**：r3 的 1 CRITICAL + 2 MAJOR + 2 MINOR + 5 缺失/歧义 **全部落地**，现场实测 11 项可执行断言（sc-l per-file / hm-* per-file / `--accent-*` variant 求和 / bt-* 总计 / dc-* 总计 / FetchDialog 11 R14 命中 / JobQueue 1 R14 命中 / `手动/目测` 残留 / purple 锁定 / 4h 硬约束对称 / subtask AC 自动化覆盖）全部与 r4 文档吻合。Round 3 Revision Notes 表 11 行逐条核对均"已修复"。

本轮仅发现 **3 个 MINOR 建议**（均非阻塞）：(a) §1.1 accent 按文件分布求和 55 与总调用 67 的口径未显式解释；(b) s5 AC 最后一条仍含"间距与 preview 一致"的半手动项；(c) §1.1 "85 实例" 与 R14 实测 137 处（含 FetchDialog fl/fi 等隐含覆盖项）的口径差异在文档中无显式说明 — 但因 R14 扫描自身是 gate，executor 无论口径如何最终必须清 0，所以不影响正确性。

按用户"MINOR 级可以 APPROVE + 说明建议"指示：**APPROVE**。

## 预判 vs 实际

- **预判 1**（r3 CRITICAL 全部解决）→ **完全命中**：§1.1 factor-research 行已改为 85 实例跨 9 文件；§3.3.7 散落表精确列 OverviewGreyTab 4 + TradesTab 3 + PerformanceTab 2 + TearsheetTab 2 + OverviewTab 5 hm-*；§3.9 每文件计数同步；s4 描述明列 16 处（11 sc-l + 5 hm-*）并锁定 hm-* 由 s4 独立内联（研究下实测 0 调用，符合 YAGNI）；s4 工作量 9→10h；s4 AC 扩展至 `rg '\b(sc-l|hm-grid|hm-label|hm-cell)\b'` = 0；task.json s4 title 已同步。✅
- **预判 2**（r3 MAJOR M1 用户 MUST 规则）→ **完全命中**：§1.6 重构两层结构（Subtask AC 自动化 / User Acceptance in Verify Phase）；s11 Step 11b 删除"视觉差异目测"步骤改为"不阻塞 s11 完成"+ 主 agent post-task fallback；§3.3.9 重写"Round 3 视觉差异声明：subtask AC 不含人工目测项"；§3.8 R-9 同步；§3.11 新增"User Acceptance Checklist（verify phase · 主 agent + 用户）"明确"不作为任何 subtask 的 acceptance_criteria"；task.json s11 title 明确"Round 3 删除视觉差异目测步骤"。✅
- **预判 3**（r3 MAJOR M2 purple 歧义）→ **完全命中**：§1.1 / §3.3.8 / s7 预计工作量 / task.json s7 title 四处同步锁定"purple → text-primary，purple-20 → bg-qds-accent-dim，无 case-by-case 评估"。✅
- **预判 4**（r3 MINOR m1 CHART_LABEL_STYLE TS 兼容）→ **完全命中**：§3.3.6 Note 3 补说明 position/value/offset 扩展 + 可选扩大类型；Note 4 补 import 风格一致性；s1 task title 已同步。✅
- **预判 5**（r3 MINOR m2 §3.3.8 数字一致）→ **完全命中**：§3.3.8 表头 "67 处跨 8 文件 / 11 variant" + 表体求和 67 实测一致；§1.1 同步；脚注 A 现场指令 rg 输出完整。实测 `rg -o 'var\(--accent-[a-z0-9-]+\)' src/web/src --glob='*.tsx' --glob='*.ts' | wc -l` = 67，与文档一致。✅
- **预判 6**（s4/s6 硬约束对称）→ **完全命中**：§子任务拆分风险新增 s4 动态拆分门槛"4h 未完成 3 主文件则拆 s4b"（L533-541），与 s6 硬约束"4h 未完成 3 子组件则拆 s6b"（L522-531）对称；"关键路径演化"Round 3 段明述"波次 B 鲁棒性下降需 s4 + s6 双硬约束"。✅
- **预判 7**（subtask AC 自动化 vs verify phase user acceptance）→ **主要命中**：所有 12 个 subtask 的 AC 均可由 rg / build / lint / wc 判定；但 s5 L219 仍残留"间距与 preview 一致"这类半手动项（见 m1）。

## Critical 发现（阻塞执行）

无。

## Major 发现（导致显著返工）

无。

## Minor 发现（次优但可工作）

### m1 · s5 验收标准最后一条残留半手动项

- **证据**：`4-tasks.md` L219：
  ```
  - data-catalog page.tsx 的 4 张 KPI 卡视觉对照 `preview/component-kpi.html`：数字字体 mono + section-label accent 橙 + 间距与 preview 一致
  ```
- **分析**：
  - "数字字体 mono" / "section-label accent 橙" 实际上通过使用 `<StatCard>` QDS 组件自动保证（自动化 gate 可通过"检查 JSX 用到 `<StatCard>` + 4 个实例"验证）
  - 但 **"间距与 preview 一致"** 是人眼对比 — 在 s5 subtask AC 中保留是与 r3 M1 修复精神不完全一致（AC-2 已降级为 User Acceptance in Verify Phase，s5 AC 不应再含视觉对照）
- **修复建议**（非阻塞 APPROVE）：将 L219 改为自动化判据：
  ```
  - page.tsx L240-243 原 4 张 KPI 行迁移后使用 `<StatCard>` QDS 组件（`rg '<StatCard\b' src/web/src/app/data-catalog/page.tsx` 命中 ≥ 4 行），或使用等价 Tailwind 还原（`<Card>` + `<SectionLabel>` + `font-mono text-lg font-semibold`）
  ```
  视觉"间距与 preview 一致"放入 §3.11.1 User Acceptance Checklist 行 `/data-catalog`。
- **严重程度**：MINOR（与 r3 M1 的补漏；s4 L172 已声明"全部自动化，无手动目测"，s5 尾条未完全呼应，但 AC-2 总则已覆盖，现实影响小 — verify phase 仍会由用户审核，不会 bypass。主 agent 审核时若严格按用户 MUST 规则解读，可能要求修正。）

### m2 · §1.1 accent 按文件分布求和 55 与总调用 67 的口径差异未显式解释

- **证据**：§1.1 L38：
  > 求和 23+13+12+7+4+2+2+1+1+1+1 = 67；文件：EditorClient(15)、StrategyPanel(11)、OrdersPanel(9)、ActionBar(6)、FillsStream(5)、TopBar(4)、PositionsTable(4)、TabNav(1)
- **实测**：
  - `rg -o 'var\(--accent-[a-z0-9-]+\)' src/web/src --glob='*.tsx' --glob='*.ts' | wc -l` = **67**（occurrences，即出现次数）
  - `rg 'var\(--accent-[a-z0-9-]+\)' src/web/src --glob='*.tsx' --glob='*.ts' -c` 按文件求和 = 15+11+9+6+5+4+4+1 = **55**（lines，即含调用的行数）
- **分析**：
  - 占 12 行差的原因是单行多调用（如一行 `fill="var(--accent-green)" stroke="var(--accent-green-10)"` 算 1 行 2 occurrences）
  - §1.1 / §3.3.8 / 4-tasks.md 对此总数描述时两个口径（"67 次调用"vs "55 行"）混用 — s7 task description L287"合计 s7 负责 40 行 / 51 次调用" 是明确区分的；但 §1.1 L38 文件分布用 "行数" 求和后断言 = 67 会让 reader 困惑
- **修复建议**（非阻塞）：§1.1 L38 括号内加一句注释："(文件分布数值为含调用的行数 `rg -c`；单行多调用时 row < occurrences；67 为 `rg -o` 总出现次数)"；或干脆改为"(`rg -c` lines 求和 55 / `rg -o` occurrences 求和 67，差 12 行因单行多调用)"。
- **严重程度**：MINOR（s7 task description 已区分"40 行/51 次调用"；执行层不会混淆，只有读 §1.1 的 reviewer 会困惑一瞬。）

### m3 · §1.1 "85 实例" 与 R14 实测 137 处的口径差异

- **证据**：
  - §1.1 L28 声明："85 处 className 实例跨 9 文件（sc-l 15 + hm-* 5 + fsel 4 + 其它 sc/cd/sl/fl/fi/ctbl/dtab 61；含 research/page 主体 47 处）"
  - 现场实测（R14 完整 token 白名单 + PCRE2 前后向断言）：
    ```bash
    rg --pcre2 'className\s*=\s*["\x27][^"\x27]*(?<![-a-zA-Z0-9_])(<70+ token>)(?![-a-zA-Z0-9_])[^"\x27]*["\x27]' src/web/src --glob='*.tsx' -o | wc -l
    # → 137 occurrences
    ```
    分布：research/page 96 + data-catalog/FetchDialog 11 + data-catalog/page 6 + OverviewTab 5 + OverviewGreyTab 4 + TradesTab 3 + PerformanceTab 2 + TearsheetTab 2 + JobQueue 1 = 130 lines（`-c`）= 137 occurrences（`-o`）
- **分析**：
  - 85 实例可能来自更窄的 token 子集（估计 sc/cd/sl/fl/fi/ctbl/dtab 不含子原语 sc-l/cd-h 之类复合命中），是手工总计而非 R14 完整扫描
  - R14 实测 137 occurrences（含 fl/fi/empty-icon 等完整家族）
  - 更关键的是：**FetchDialog 有 11 处 R14 命中**（6 fl + 2 fi + 3 fsel），但 s5 task description L200-205 只明列"3 处 fsel"未提 fl/fi；s5 验收 L214 的 R14 在 data-catalog 下 0 命中 **会兜住** fl/fi 遗漏 — 所以执行层不会漏，只是任务描述层精度不足
- **修复建议**（非阻塞）：
  1. §1.1 L28 的"85 实例"改为与 R14 实测一致的"137 次 className 实例（R14 PCRE2 扫描）"，或明确标注"85 为按 sc/cd/sl 7 个主 token 计数的子集；全家族 R14 扫描实测 137"
  2. `4-tasks.md` s5 L200 的"factor-research 散落清理"条目追加："+ FetchDialog 的 6 处 fl + 2 处 fi（作为 shadcn Label/Input 一并迁移，R14 兜底）"
  3. s6 task description L238 保持现状（research/page 47 处按逻辑位置计数，R14 扫描实测 96 occurrences，s6 AC L260 的 R14 扫描自兜底）
- **严重程度**：MINOR（R14 自身是 gate 兜底；执行层不会漏，只是任务描述层精度不足。与 r3 C1 同类型复发但影响度已不同 — r3 C1 是"规划覆盖缺失导致 executor 漏做"，m3 是"描述层枚举不全但 AC 层兜底"。）

## 缺失项

无（所有 r3 缺失 1-3 均已在 Round 3 Revision Notes 表中列出并落地）。

## 歧义风险

| 文档原文 | 解读 A | 解读 B | 选错后果 |
|---|---|---|---|
| `s4 L149` "评估是否合并到 OverviewTab" | executor 自行判定 | 主 agent 决定 | 无歧义 — §3.5.3 L922 给明客观标准"helper 与数据转换函数 70% 以上共用则合并" |
| `s5 L219` "间距与 preview 一致" | 用 StatCard 即合规 | 人眼对比 | 见 m1；现实路径是 Layer-2 由 verify phase 兜住 |
| `s8 L325` "optimization/page.tsx 行数无增加" | 当前行数为基线 | 本任务开始前快照 | 需 baseline 快照；建议改为"≤ 736 行" |

## 假设分析

| 假设 | 级别 | 说明 |
|---|---|---|
| Round 3 所有修订计数均现场独立 rg 扫描验证 | VERIFIED | 现场实测 sc-l 15 / hm-* 5 / accent 67 / bt-* 280 / dc-* 65 — 与 §1.1 脚注 A 指令输出完全一致 |
| s5 R14 扫描会兜底 FetchDialog 的 fl/fi 等未在描述中列出的项 | VERIFIED | R14 regex 含 fl/fi 等 token；s5 AC L214 R14 命中 0 行判据足够严格 |
| s4/s6 动态拆分 4h/3 子组件门槛可由 executor 自行触发 | REASONABLE | execute.jsonl 记录即可；cage 框架是否支持运行时动态增加 parallel_groups 属框架能力范畴（与 r3 Open Question 相同，未解决但不阻塞计划层） |
| User Acceptance Checklist 不作为 subtask AC 的法律边界 | VERIFIED | §1.6 L258-259 显式声明 + §3.11 L1095 + task.json s11/s12 title 三处一致 |
| AC-2/AC-3/AC-5 视觉项全部降级为 verify phase | VERIFIED | §1.6 AC-2 / AC-3 拆为 a/b / AC-5 均声明"不作为 subtask 的 acceptance_criteria"；文字精确到用户 MUST 规则引用路径 `/Users/ouzhuohao/.claude/CLAUDE.md` L2 |
| 11 variant --accent-* 全仓实测 67 occurrences / 55 lines / 11 variant / 8 文件 | VERIFIED | 现场 `rg -o` 67 / `rg -c` 求和 55 / `rg -o | sort -u` 11 variants — 与 §3.3.8 完全一致 |
| research/page 主体迁移能在 s6 10h 内完成 | FRAGILE | R14 扫描实测 research/page 96 occurrences（vs 描述的 47 处）；若按 occurrence 口径工作量可能偏紧 — 但 s6 硬约束（4h 未完成 3 子组件则拆 s6b）已预置缓解 |

## 预验尸

| 失败场景 | 文档是否应对 | 说明 |
|---|---|---|
| 1. s4 executor 严格按描述 16 处散落迁移完成后，R14 扫描仍命中 | **No risk** | R14 是 s4 AC 的一部分（L173），executor 不得 exit 0 未通过；16 处计数已精确 |
| 2. s5 executor 按 "3 处 fsel" 描述完成迁移，R14 扫描命中 FetchDialog 6 处 fl + 2 处 fi | **Mitigated** | R14 gate 兜底会阻止 s5 完成；executor 补做工时约 30-60min；m3 建议明列避免 kickback |
| 3. s7 executor 面对 FillsStream 的 purple 选择 text-qds-info 而非 text-primary | **No risk** | §3.3.8 表 L734 锁定"text-primary"无备选；§1.1 / s7 工作量说明 / task.json 四处同步 |
| 4. s11 executor 执行 barrel re-export 后主动打开 dev server 逐页目测 rounded-md vs rounded-full | **Mitigated** | §3.3.9 L821-832 显式声明"无逐页目测作为 subtask AC item"；s11 Step 11b L418 明确"删除目测步骤"；§3.11.3 移到 verify phase；用户若严格审查 PR 描述找不到违规项 |
| 5. s6 research/page R14 实测 96 occurrences 而描述 47 逻辑位置 → 工作量偏紧 | **Partial** | s6 硬约束（4h 未完成 3 子组件则拆 s6b）已预置缓解；但如果"occurrences 口径"更准确，原 10h 可能偏紧 — 不阻塞但值得记录（m3 建议澄清） |
| 6. s4 动态拆 s4b 时 cage 框架的 parallel_groups 运行时更新机制 | **Partial** | 与 r3 Open Question 相同 — 属框架能力范畴，不阻塞规划层；s4/s6 硬约束描述均声明"由再启动的 agent 并行跑" |
| 7. s10 preflight 失败 → 回退 target 映射表能否覆盖所有违规路径前缀 | **Yes** | L363-374 映射表 6 条 + 1 fallback 行（`src/components/` / 其它 → s11）完整覆盖所有可能路径 |
| 8. `--accent-*` 口径差（55 vs 67）使 executor 按 55 行做工作量估算低估 | **Partial** | s7 task description 已显式"40 行/51 次调用"区分 — 不会误导；但 §1.1 读者可能困惑（m2） |

## 多视角笔记

### Executor 视角
- **清晰度提升**：r3 → r4 的 task.json 12 个 title 都精确到计数 + Round 3 锁定决策 + 用户 MUST 规则声明，执行者直接看 title 即可理解任务边界
- **唯一卡点（MINOR）**：s5 L219 "间距与 preview 一致" 和 FetchDialog fl/fi 未明列 — R14 gate 会兜住，不会真正卡住，只是描述层不完整
- **动态拆分指引**：s4/s6 硬约束均明确"4h/3 子组件"触发条件 + 执行步骤（execute.jsonl 记录 + 再启动 agent 跑 s4b/s6b），executor 无需猜测

### Stakeholder 视角
- **r3 → r4 质量跃升最大**：4 轮下来首次所有用户 MUST 规则相关项（AC 不含手动验证）全面合规；§3.11 新增的 User Acceptance Checklist 章节把 verify phase 与 subtask AC 职责边界完整分离，这是 Cage 工作流的模范实践
- **范围稳定**：12 任务 × 4 波次 DAG 保持；工作量 17h（关键路径）17h ± 1h；无新增依赖边
- **虚荣指标排查**：NFR-2 "globals.css 1210 ± 50 行"仍是过程指标，但 AC-1 / AC-4 捕获结果指标（R1-R14 全通过 / CLAUDE.md 章节定稿）已兜住

### Skeptic 视角
- **最强反对论点**：m3 的 "85 vs 137" 口径差是规划精度问题的弱复发 — 但与 r1-r3 的"漏文件/漏计数"不同，这次 R14 gate 会兜住执行层不漏，只是描述与扫描两口径不同步；属于 MINOR 而非 MAJOR
- **被拒绝替代方案**：r3 曾建议 "新增 Playwright DOM 断言脚本做 StatusBadge 视觉验证"（方案 A），planner 选择方案 B（声明不作为 subtask AC + verify phase user acceptance）— 合理，符合用户 MUST 规则同时避免引入新自动化依赖
- **已知限制**：cage 框架运行时动态拆任务能力仍是 Open Question（r3 遗留），但 s4/s6 硬约束是防御性而非必须触发 — 不是规划缺陷而是环境约束

## 上轮修改验证（逐条核对 r3 的 6 个修改要求 + 5 个缺失/歧义）

| 上轮要求（Critic r3） | 是否解决 | 说明 |
|---|---|---|
| **[CRITICAL C1]** sc-l 实测 15 处 + OverviewGreyTab 4 处 + hm-* 5 处 | **Yes** | §1.1 / §3.3.7 / §3.9 / s4 / task.json s4 title 五处同步；现场 `rg '\bsc-l\b'` = 15 跨 5 文件 / `rg '\b(hm-grid|hm-label|hm-cell)\b'` = 5 仅 OverviewTab — 与文档完全一致；s4 工作量 9→10h；验收 `rg '\b(sc-l|hm-grid|hm-label|hm-cell)\b'` = 0 |
| **[MAJOR M1]** s11 视觉差异目测自动化或非 AC | **Yes（方案 B）** | §3.3.9 L821-832 显式"无逐页目测作为 subtask AC item"；s11 Step 11b 删除；§3.8 R-9 同步改写；§3.11.3 User Acceptance Checklist 接管；task.json s11 title 明述；§1.6 AC-5 新增两层结构 |
| **[MAJOR M2]** purple 锁定 text-primary | **Yes** | §1.1 / §3.3.8 / s7 task description / task.json s7 title 四处同步；删除 "case-by-case 评估" 备选 |
| **[MINOR m1]** CHART_LABEL_STYLE TS 兼容性 | **Yes** | §3.3.6 Note 3 详述 CSSProperties + extra fields；Note 4 import 风格一致性；s1 task title 同步 |
| **[MINOR m2]** §3.3.8 "10 vs 11 vs 67" 数字一致 | **Yes** | §3.3.8 表头 "67 处跨 8 文件 / 11 variant" 与表体 11 行求和 67 一致；§1.1 同步；脚注 A 现场 rg 指令输出贴入 |
| **[MINOR 缺失 3]** chartTheme.ts import 风格 | **Yes** | §3.3.6 Note 4 + s1 task title |
| **[r3 缺失 1]** §3.9 OverviewGreyTab sc-l 4 处与 s4 描述同步 | **Yes** | §3.9 表每文件计数同步（OverviewGreyTab.tsx 现改为 "bt-* 6 处 + sc-l 4 处" 等）；s4 描述 L156 明列 4 处行号 |
| **[r3 缺失 2]** R14 selftest 覆盖混用形态 | **Yes** | s2 AC L94 "覆盖 `className='sc-l inline-flex items-center'` 等正例" — 现在 R14 PCRE2 前后向断言确保边界安全 |
| **[r3 歧义 4]** data-catalog/page "L240-243 4 处 sc-l" | **Yes** | §3.3.7 表 L595 精确列 "L240-243（4 张 KPI 卡 × sc + sc-l + sc-v + sc-sub；共 6 处原语 in 4 行 JSX）+ L252（1 处 fsel）= 7 处合计" |
| **[r3 歧义 5]** §3.3.9 "若差异过大"主观阈值 | **Yes** | 重构为"由用户判定 — 主 agent 呈现给用户"；fallback 是用户决策而非 executor 决策，消除主观阈值问题 |
| **[r3 Open Questions]** cage 框架运行时动态拆任务能力 / s10 回退重开机制 | **Partial** | 规划层已尽力（硬约束 + 回退 target 映射 + execute.jsonl 记录），余下是框架能力范畴；标注为 Open Question 不阻塞计划层 |

**上轮要求 11 项（6 修改 + 5 缺失/歧义）中 **10 Yes + 1 Partial（框架能力）**。Partial 项属框架层非规划层，不构成阻塞。**

## 修改要求（APPROVE · 无硬性修改项）

以下为 **APPROVE** 下的**建议**（planner 可选接受；不合并到 task 也不阻塞执行）：

1. **[MINOR m1]** s5 AC 的最后一条"间距与 preview 一致"改为自动化判据（建议文本见 m1 修复建议）；或将此项移到 §3.11.1 `/data-catalog` 行（verify phase user acceptance）。
2. **[MINOR m2]** §1.1 L38 accent 行文件分布 `rg -c` 55 行 vs `rg -o` 67 occurrences 的口径差加一句注释，避免 reviewer 困惑。
3. **[MINOR m3]** §1.1 "85 实例" 标注"(按 sc/cd/sl 主 token 计数；全家族 R14 扫描 137 occurrences)"；或在 4-tasks.md s5 task description 追加"FetchDialog 的 6 处 fl + 2 处 fi 作为 shadcn Label/Input 迁移，R14 兜底"。
4. **[观察]** s8 L325 "optimization/page.tsx 行数无增加" 改为精确基线 "≤ 736 行"，避免 baseline 快照不明。

## 判决理由

VERDICT: **APPROVE**。

r3 → r4 修订**全面扎实**：11 项要求 10 完全解决 + 1 属框架范畴非规划层。所有用户 MUST 规则合规：§1.6 / §3.11 / §1.6 AC-5 / s11 task description / §3.3.9 / task.json s11 title / §3.8 R-9 六处同步声明"subtask AC 不含手动验证项"，并用显式路径引用用户全局 CLAUDE.md L2 规则。

**现实检查**（阶段 7）：
- 3 MINOR 发现全部是"描述层精度"问题，R14 / build / lint 自动化 gate 兜底执行层 — 不会让 executor 漏工作或卡住
- m1（s5 L219 半手动项）是 AC 层最严格的合规解读才算违规；主流解读下 AC-2 已声明"视觉对照 → verify phase"，该条可视为"自动化已通过+视觉对照补充说明"
- m2 / m3 口径差异是 reviewer 阅读体验问题，不影响执行正确性
- **无 CRITICAL/MAJOR 发现**：连续 4 轮下来质量单调上升，无新的缺陷类别，无规划精度系统性问题复发（前 3 轮 C1 同类型漏报链条到 r4 已完全消除）

**未升级 ADVERSARIAL**：r4 整体质量显著高于 r1-r3；问题集中在描述精度的尾端优化，不存在结构性缺陷。按用户明确指示"MINOR 级可以 APPROVE + 说明建议"，给出 APPROVE 判决。

**建议流程改进**（供主 agent 参考，不属本轮修改要求）：本次 planner "先独立全仓 rg 扫描再写任务描述" 的流程改进起了作用（r3 critic 建议 → r4 兑现）。建议 .cage 框架在规划 skill 中固化此流程作为 planner 默认行为。

## Open Questions（未评分）

- cage 框架是否支持运行时动态拆任务（s4b / s6b 并发）？属框架能力范畴（与 r3 Open Question 同），不阻塞 APPROVE。
- s10 preflight 失败 → 回退 target 映射后，已完成 sN 任务如何"重开"触发新 verify？属 exec skill 能力范畴。
- §1.1 "85 实例" 的"实例"定义（按主 token 计 vs 按完整家族计）官方化，未来 Cage 规划模板可统一口径。

ReviewPass: critic
VERDICT: APPROVE
