# Architect Review — Round 4

**VERDICT: APPROVE**

## 摘要

Round 3 的 CRITICAL-1（factor-research 散落 + R14 PCRE2 word-boundary 假阳性）和全部 MAJOR（视觉目测违反用户 MUST 规则、purple case-by-case 歧义）均已彻底解决。现场独立 rg + selftest 实测验证：

1. **sc-l 实测分布与文档完全一致**：OverviewGreyTab 4 / TradesTab 3 / PerformanceTab 2 / TearsheetTab 2 = 11 处；外加 data-catalog/page.tsx 4 处（归 s5）。
2. **hm-* 实测分布与文档完全一致**：仅 OverviewTab L190/192/195/200/206 共 5 处，research 下 0 处（证实 s4 内联决策不依赖 s6 的事实依据）。
3. **R14 新正则（R4-风格前后向断言）selftest 全通过**：13 条正例 + 9 条反例（含 `sc-column` / `fg-primary` / `fi-rocket` / `cd-hover` / `sl-indicator` / `scroll` / `cards` 等 Round 3 新加关键反例），消除了 r3 指出的内部矛盾。
4. **任务 DAG 合法**：s4.depends_on = [s1,s2]，s6.depends_on = [s1,s2]，无 s4 → s6 边，双方同组并行合法。
5. **User Acceptance Checklist §3.11** 已与 subtask AC 清晰分离；s11 AC、4-tasks.md s11 描述均无手动目测 item，遵守用户全局 MUST 规则。

本轮**无新的 CRITICAL/MAJOR 发现**。仅 3 条 MINOR 级建议（见末尾），均不阻塞执行。

## 代码引用验证（现场重新采样 @ 2026-04-19）

| 引用 | 实测 | 状态 |
|------|------|------|
| `rg '\bsc-l\b' src/web/src --glob='*.tsx'` | data-catalog/page 4 + OverviewGreyTab 4 + TradesTab 3 + TearsheetTab 2 + PerformanceTab 2 = **15 处跨 5 文件**；backtest 下 **11 处**（与 s4 描述 11 sc-l 一致） | ✅ 完全一致 |
| `rg '\b(hm-grid\|hm-label\|hm-cell)\b' src/web/src/app/backtest --glob='*.tsx' -n` | OverviewTab L190 hm-grid + L192/L195/L200 hm-label + L206 hm-cell = **5 处**（与 s4 描述一致）| ✅ |
| `rg '\b(hm-grid\|hm-label\|hm-cell)\b' src/web/src/app/research --glob='*.tsx'` | **0 处**（证实 s4 内联决策的事实依据） | ✅ |
| `rg -o 'var\(--accent-[a-z0-9-]+\)' src/web/src` 11 variant 求和 | green 23 + red 13 + amber 12 + blue 7 + orange 4 + red-20 2 + green-10 2 + purple 1 + purple-20 1 + blue-20 1 + amber-20 1 = **67 次（+ --accent-foreground 1 shadcn 豁免）**，与 §3.3.8 / s7 "51 次" + s9 "16 次" = 67 一致 | ✅ |
| R14 selftest 实跑（22 条断言）| 13 正例全部命中、9 反例全部未命中（含 `sc-column` / `fg-primary` 等关键反例）| ✅ 彻底消除 r3 MAJOR 所指 selftest 内部矛盾 |
| task.json s4.depends_on | `["s1","s2"]` — 无 s6 | ✅ s4 内联 hm-* 决策消除隐式依赖 |
| task.json s6.depends_on | `["s1","s2"]` — 无 s4 | ✅ 无反向边 |
| task.json parallel_groups wave B | `["s4","s5","s6","s7","s8","s9"]` 6 并行 | ✅ 拓扑无环；并发上限策略已在 4-tasks.md L488 声明 |
| task.json `review.round` | 4 | ✅ |
| 3-tech-design.md §3.11 User Acceptance Checklist | 独立章节列出视觉对照 / 双主题 / StatusBadge barrel / memory 4 类 verify-phase 项目；明确声明"不作为任何 subtask acceptance_criteria" | ✅ 与 subtask AC 清晰分离 |
| 4-tasks.md s4 验收第 173-174 行 | `rg -n '\b(sc-l\|hm-grid\|hm-label\|hm-cell)\b' src/web/src/app/backtest --glob='*.tsx'` 命中 0 | ✅ 与 §3.3.7 / §3.9 计数闭合 |
| 4-tasks.md s11 AC（Step 11b）| 全部自动化：(a) Status union 7 键；(b) JobQueue 4 处 bt-status 迁移；(c) build/lint；(d) R2/R11/R14 扫描；显式声明"无逐页目测作为 subtask AC item" | ✅ 遵守用户 MUST 规则 |
| 3-tech-design.md §3.3.8 purple 行 | `text-primary`（Round 3 锁定决策：项目无 purple token；本任务不新增 token；purple 语义统一并入 accent 橙）| ✅ 删除 r2 "case-by-case" 备选 |
| 3-tech-design.md §3.3.8 purple-20 行 | `bg-qds-accent-dim`（统一并入 accent dim `--acc-d` 12% alpha）| ✅ 锁定决策 |

## 需求审查（1-requirements.md）

无 CRITICAL/MAJOR/MINOR 发现；§1.1 违规表 purple 行、accent 求和、factor-research 散落精确分布均已与 §3.3.7 / §3.3.8 对齐。

## 技术设计审查（3-tech-design.md）

### Critical 发现

无。

### Major 发现

无。

### Minor 发现

1. **s11 AC "`rg -n \"^export (function|const|type) StatusBadge\" src/web/src/components/StatusBadge.tsx` 应为 re-export"**（4-tasks.md L428）断言方式略脆弱 — 若 executor 写 `export { StatusBadge } from "@/components/qds/status-badge"`，这个正则**不会命中**（被断言应 re-export 但实际检测不到，可能导致 AC 误判通过或误判失败）。建议改为正向断言 `rg -n 'export \{ StatusBadge \} from "@/components/qds/status-badge"' src/web/src/components/StatusBadge.tsx` 命中 ≥ 1，语义更精确。**不阻塞**（只是 AC 判定文本的脆弱性，不影响执行结果）。

2. **§3.3.7.9 `.hm-*` 行的内联代码模板**（tech-design L706）引用了 `cellBg(val)` / `cellText(val)` 辅助函数，但未说明这两个函数在 OverviewTab 现状中的定义位置或由 executor 新建。建议在 s4 描述（4-tasks.md L161）补一句"`cellBg`/`cellText` 若 OverviewTab 已有则复用；若无则按 globals.css L1927-1933（.hm-cell 背景色语义）新建"。**不阻塞**（executor 可自行在 OverviewTab 内定义，Tailwind + CSS custom props 方案已描述清楚）。

3. **`<OverviewMonthlyHeatmap>` 新子组件归属**（4-tasks.md L148 提及 s4 拆出 `OverviewMonthlyHeatmap` 子组件承载 5 处 hm-* 内联）— 该子组件仅被 OverviewTab 消费，放在 `backtest/components/` 合适；但 §3.5.3 "OverviewTab → 拆出 OverviewKpis / OverviewEquityChart / OverviewStats / **OverviewMonthlyHeatmap**" 列表中已显式新增该组件，与 s4 描述匹配。**无问题**。

## DAG 审查（4-tasks.md + task.json）

### 合法性

- 拓扑无环 ✓；parallel_groups 4 个波次；s12 依赖 [s10, s11]；s10 / s11 都依赖 s4-s9 全体 ✓。
- **s4.depends_on = [s1, s2]**（无 s6）+ **s6.depends_on = [s1, s2]**（无 s4）— s4 内联 hm-* 决策彻底消除 r3 指出的隐式依赖；两任务同波次并行合法 ✓。
- task.json s4 title 含 "5 处 hm-* 内联迁移（OverviewTab L190-206，不依赖 s6）"，与 §3.3.7.9 锁定决策对齐 ✓。
- task.json `review.round = 4` ✓。

### 并行性

- **wave B 6 并行 > 常规 5 agent 上限问题**：4-tasks.md L487-488 已声明调度方案（"若主 agent 派遣工具并发上限为 5，则 s9 排队在 s4-s8 之后启动；因 s9 (2.5h) << s4/s6 (10h) 关键路径，无实质影响；若支持 6 并发则全部同时启动"）。此说明清晰，**无阻塞**。
- **s4 + s6 双 10h 并列关键路径风险**：4-tasks.md §"子任务拆分风险" L520-543 对 s4/s6 分别声明硬约束（4h 未完成 3 主文件则拆分 s4b/s6b），降低单点超时风险。**合理**。

### 遗漏任务 / 粒度问题

无。

### 稳定性

- s4 硬约束门槛（拆 s4a/s4b）+ s6 硬约束门槛（拆 s6a/s6b）+ preflight 失败回退 target 映射 + R14 + selftest 四重保障充分。

## 权衡分析

| 决策 | 正方 | 反方 | 建议 |
|------|------|------|------|
| OverviewTab hm-* 5 处：s4 内联 Tailwind grid + CSS custom props vs 复用 s6 `<MonthlyHeatmap>` | 内联：消除 s4→s6 隐式依赖、wave B 真正并行、s4 自足（YAGNI 原则，因 research 下 0 调用点） | 内联：hm-* 样式在 backtest 独立实现后，未来若 research 需要类似热力图，需独立建组件（轻微重复） | **内联方案（已锁定）合理**：research/ 下 hm-* 调用点实测 0，"共享 MonthlyHeatmap" 是伪需求；内联 Tailwind + CSS custom props 约 10-15 行代码足够；若未来 research 引入热力图需求，届时再抽共享组件不晚 |
| `--accent-purple` 映射：锁定 `text-primary`（与 accent 橙合并）vs case-by-case 评估 | 锁定：无 executor 决策歧义、deterministic 迁移、自动化扫描可判定 / case-by-case：视觉语义保留（purple 可能在某些业务语境代表"特殊"） | 锁定：FillsStream 1 处 purple 原意可能是"特殊 fill 类型标注"，映射到 accent 橙后 UX 语义与其它 fill 相同（视觉合并）/ case-by-case：判定权委托 executor 破坏 DS 一致性规则 | **锁定方案（Round 3 决策）合理**：DS 规则 "项目无 purple token"，本任务标准化目标是收敛语义色；若未来 purple 成为业务必须色，应通过 DS 正式立项新增 `--acc-purple` token，而非在标准化过程中保留 case-by-case 例外 |
| s11 AC 是否包含"逐页目测"步骤 | 包含：即时发现视觉回退 / 不包含：遵守用户 MUST 规则（subtask AC 无手动验证） | 包含：违反用户全局 MUST / 不包含：barrel re-export 后的视觉差异延迟到 verify phase 才发现，post-task 回迁成本 0.5-1h | **不包含（Round 3 决策）合理**：s11 subtask 层交付代码正确性（Status union + barrel + build/lint）；视觉差异延迟到 §3.11.3 User Acceptance in verify phase，由用户在 dev server 上判定；若需 fallback，主 agent post-task 派 agent 回迁 0.5-1h 可接受 |
| wave B 6 并行 vs 拆分为两批派遣 | 6 并行：理论最短时长 10h / 拆两批：兼容 5-agent 派遣工具上限（s9 2.5h 在 s4-s8 任一完成后启动） | 6 并行：依赖主 agent 派遣工具支持 6 并发 / 拆两批：总时长无变化（因 s9 < max(s4,s6) = 10h） | **调度声明（4-tasks.md L487-488）合理**：两种模式总时长都是 10h，框架选择权交给主 agent，无技术风险 |

## 遗漏项

无。

## 上轮（r3）修改验证

| r3 发现 | 级别 | 是否解决 | 说明 |
|---|---|---|---|
| **CRITICAL-1 factor-research 散落漏 7 sc-l + 5 hm-* + OverviewGreyTab 整文件** | CRITICAL | ✅ 完全解决 | 1-requirements.md §1.1 / 3-tech-design.md §3.3.7 散落清单表 / §3.9 影响文件表 / 4-tasks.md s4 描述 + 验收 全部精确更新；实测 `rg` 与文档计数 1:1 对齐（OverviewGreyTab 4 / TradesTab 3 / PerformanceTab 2 / TearsheetTab 2 = 11 sc-l + OverviewTab 5 hm-* = 16 处 factor-research 散落）；`<MonthlyHeatmap>` 组件归属冲突通过 "s4 内联实现不依赖 s6" 锁定消除（§3.3.7.9 L706）；s4 工作量 9h → 10h；s4 验收扩展为 `rg -n '\b(sc-l\|hm-grid\|hm-label\|hm-cell)\b'` 0 命中 |
| **MAJOR R14 PCRE2 `\b` 对 `sc-column` / `fg-primary` 假阳性 + selftest 内部矛盾** | MAJOR | ✅ 完全解决 | §3.2.3 R14 正则改为 R4 风格前后向断言 `(?<![-a-zA-Z0-9_])(TOKEN)(?![-a-zA-Z0-9_])` + 支持模板字符串反引号；§3.2.8 selftest 补 9 条新反例（`sc-column` / `fg-primary` / `fi-rocket` / `cd-hover` / `sl-indicator` / `scroll` / `cards` 等必须不命中）；s2 R14 正则行同步；实跑 22 条 selftest 全部通过 |
| **MAJOR C-M1 subtask AC 含手动目测违反用户 MUST 规则** | MAJOR | ✅ 完全解决 | 1-requirements.md §1.6 AC-2 / AC-3 / AC-5 的视觉部分降级为 User Acceptance in verify phase；§3.3.9 StatusBadge "逐页目测" 措辞删除；4-tasks.md s11 Step 11b 的"视觉差异目测"删除，改为 "若 barrel 切换引发视觉争议，由主 agent 在 PR review 时决定是否走 fallback，不阻塞 s11 完成"；§3.11 新增 User Acceptance Checklist 独立章节列出视觉对照 / 双主题 / StatusBadge / memory 四类 verify-phase 项目 |
| **MAJOR C-M2 `--accent-purple` 映射歧义** | MAJOR | ✅ 完全解决 | 1-requirements.md §1.1 purple 行锁定 `text-primary` + 删除 case-by-case 备选；§3.3.8 purple/purple-20 行锁定；s7 描述明示 "按 §3.3.8 固定映射"；task.json s7 title 含 "purple → text-primary，purple-20 → bg-qds-accent-dim，无 case-by-case 评估" |
| MINOR C-m1 CHART_LABEL_STYLE TS 兼容 | MINOR | ✅ 完全解决 | §3.3.6 Note 3 说明 Recharts `.label` prop 接受 CSSProperties + extra fields；Note 4 说明 import 风格沿用 `: React.CSSProperties` |
| MINOR C-m2 10 variant vs 11 rows vs 求和 67 | MINOR | ✅ 完全解决 | 全仓统一为 "11 variant / 67 次调用 / 8 文件"，表头/表体/求和三者一致 |
| MINOR A-MINOR s4 工作量 9h 偏低 | MINOR | ✅ 完全解决 | s4 工作量 9h → 10h；新增 s4 动态拆分门槛（与 s6 对称） |
| MINOR A-MINOR wave B 6 并行 > 5 上限 | MINOR | ✅ 完全解决 | 4-tasks.md L487-488 声明调度方案 |
| MINOR C-m3 chartTheme.ts CSSProperties import 风格 | MINOR | ✅ 完全解决 | §3.3.6 Note 4 显式说明 |
| MINOR C-缺失 1 §3.9 OverviewGreyTab 不同步 | MINOR | ✅ 完全解决 | §3.9 OverviewGreyTab 行追加 "Round 3 新增：.sc-l 4 处迁移（L84/L134/L220/L458）" |
| MINOR C-缺失 2 R14 selftest 未覆盖 sc-l + Tailwind 混用 | MINOR | ✅ 完全解决 | §3.2.8 R14 selftest 追加 `className="sc-l inline-flex items-center"` 必须命中 + 3 条 hm-* 家族正例 |

**综合**：Round 3 的 16 个修改要求（2 CRITICAL / 4 MAJOR / 10 MINOR 级）**全部完全解决**。

## APPROVE 附带建议（非阻塞）

以下 3 条 MINOR 级建议 planner 可**选择性**吸收，不阻塞本轮通过：

1. **s11 AC StatusBadge re-export 断言正则**（4-tasks.md L428）从 `^export (function|const|type) StatusBadge` 改为 `export \{ StatusBadge \} from "@/components/qds/status-badge"` — 语义更精确匹配 barrel re-export 模式。
2. **s4 描述 hm-* 内联辅助函数来源**（4-tasks.md L161 或 §3.3.7.9 L706）可补一句说明 `cellBg/cellText` 由 executor 在 OverviewTab 内定义或按 globals.css `.hm-cell` 背景色语义（当前定义位置未验证，但 executor 可自行查找）。
3. **CLAUDE.md 「标准化后的约束」Step 5b 定稿**（s12）可额外提醒 executor："若 subtask AC 需要加手动验证项，必须先在需求文档中声明该项为 User Acceptance（verify phase），避免再次触犯用户全局 MUST 规则" — 作为 Historical Notes 的一部分，防止未来类似任务重蹈 r3 覆辙。

---

ReviewPass: architect
VERDICT: APPROVE
