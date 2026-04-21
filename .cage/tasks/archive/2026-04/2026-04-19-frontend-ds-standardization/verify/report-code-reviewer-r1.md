## 代码审查报告

**任务**: 前端设计系统标准化 (2026-04-19-frontend-ds-standardization)
**审查轮次**: Round 1
**审查日期**: 2026-04-20
**审查文件数**: 39（修改）+ 16（新增 untracked）
**变更统计**: +1384 / -3409 行

---

### 按严重程度

- CRITICAL: 0
- HIGH: 5（必须修复）
- MEDIUM: 4（应该修复）
- LOW: 3（可选）

---

### Stage 1: 规格合规

**整体结论**: 部分通过 — 自动化扫描层 (R1-R14) 全部通过，但 3 项结构性需求未达成。

#### 通过项

- `bash src/web/scripts/verify-ds-compliance.sh` 退出码 0，R1-R14 全部 0 violations
- `--selftest` 65/65 通过，含 PCRE2 前后向断言、模板字符串用例、多行 R9
- `--preflight-before-css-delete` 退出码 0，R1-R10+R12+R13+R14 全部 0 violations
- `--mode both-themes` 退出码 0
- `globals.css` 从 1987 行削减至 785 行，`.bt-*`/`.dc-*`/factor-research 子系统定义全部删除，R11 通过
- `chartTheme.ts` 新增 `CHART_LEGEND_STYLE` 和 `CHART_LABEL_STYLE` 两个常量，规格一致
- `data-catalog/types.ts` 的 `TYPE_BADGE_CLS` 字典已全面迁移至 Tailwind 语义类
- `src/web/CLAUDE.md` 已新增「标准化后的约束」章节，含禁区 class 清单、迁移对照表、Historical Notes

#### 未达成项

1. FR-4.1 文件拆分：4 个 backtest 文件全部未执行拆分（详见 HIGH 问题）
2. FR-3.1 StatusBadge 统一：两份独立实现并存（详见 HIGH 问题）
3. `npm run build` / `npx tsc --noEmit`：因大小写 import 失败（详见 HIGH 问题）

---

### 问题列表

---

#### [HIGH] import 大小写 bug — 构建阻断
**File**: `src/web/src/app/research/components/ResearchExploreResult.tsx:16`
**Issue**: `import { Card, CardContent, CardHeader } from "@/components/ui/Card"` 使用大写 C，但实际文件为 `card.tsx`（小写）。macOS 不区分大小写文件系统会隐藏此错误，但 `npx tsc --noEmit` 报 TS1261，Turbopack build 报 `Module not found: Can't resolve '@/components/ui/Card'`，Linux CI 必失败。
**Fix**: 将第 16 行改为 `from "@/components/ui/card"`（小写 c）。

---

#### [HIGH] StatusBadge 两份实现并存 — FR-3.1 未完成
**Files**:
- `src/web/src/components/StatusBadge.tsx`（独立 Badge 实现，含 `STATUS_MAP`，使用 shadcn `Badge` variant，未改为 barrel re-export）
- `src/web/src/components/qds/status-badge.tsx`（QDS 版本，StatusKind union 为 `running/done/failed/queued/paused/flattening/starting`，与规格要求的 7 个键不完全匹配）
- `src/web/src/app/page.tsx:130` 和 `src/web/src/app/optimization/page.tsx:13` 仍从 `@/components/StatusBadge` 导入，使用旧实现
- `src/web/src/app/data-catalog/JobQueue.tsx:6` 从 `@/components/qds` 导入新版本

**Issue**: s11 要求顶层 `StatusBadge.tsx` 改写为 barrel re-export，并统一 Status union 为 7 个规格键。当前两份实现的键名不兼容（`done` vs `completed`、缺 `cancelling/cancelled`），语义歧义，且 `JobQueue.tsx` 的取消状态 fallback 语义存疑（验证报告指出已被 hack 为 `status="queued"`，实测代码中未找到该 hack，但 STATUS_MAP 结构存在差异）。
**Fix**:
1. 将 `status-badge.tsx` 中 `StatusKind` 改为 `"queued" | "running" | "completed" | "failed" | "cancelling" | "cancelled" | "done"`
2. 将 `components/StatusBadge.tsx` 改为纯 barrel re-export：`export { StatusBadge, type StatusKind } from "@/components/qds/status-badge";`
3. 检查所有 `status={run.status}` 调用点类型兼容性

---

#### [HIGH] backtest/page.tsx 未拆分 — FR-4.1 违反
**File**: `src/web/src/app/backtest/page.tsx` (1805 行，原 1754 行)
**Issue**: FR-4.1 明确要求 `backtest/page.tsx` 拆分至主文件 < 700 行，按列表视图/详情视图/URL 状态/查询/分页拆分为不超过 4 个子模块。实际情况是文件从 1754 行增长至 1805 行（+51 行），未执行任何拆分。NFR-2 可维护性目标违反。
**Fix**: 按 FR-4.1 指导，在 `src/app/backtest/components/` 下新建 `list/` 和 `detail/` 子目录，将列表相关逻辑、详情视图、查询 hook、分页组件分别提取。主文件保留路由壳 < 400 行。

---

#### [HIGH] PerformanceTab.tsx / TradesTab.tsx / OverviewTab.tsx 未拆分 — FR-4.1 违反
**Files**:
- `src/web/src/app/backtest/components/PerformanceTab.tsx` (2061 行，原 2059 行)
- `src/web/src/app/backtest/components/TradesTab.tsx` (851 行，原 847 行)
- `src/web/src/app/backtest/components/OverviewTab.tsx` (836 行，原 817 行)

**Issue**: FR-4.1 要求这 3 个文件分别拆分到主文件 < 700 行，明确指定拆分目标（PerformanceTab 拆出各类图表子组件、TradesTab 拆为筛选器+表格、OverviewTab 拆为 KPI 网格+图表）。实际情况是文件均略有增长，完全未执行拆分。4 个 backtest 大文件合计 5553 行，全部超出阈值。
**Fix**: 按 FR-4.2 规范，在 `src/app/backtest/components/performance/` 下拆出 `EquityChart.tsx`、`DrawdownChart.tsx`、`RollingChart.tsx` 等；在 TradesTab/OverviewTab 同级目录下拆出对应子组件。所有新文件用 `git mv` 保留 blame（NFR-3）。

---

#### [HIGH] R8 扫描脚本对多行 Legend 存在漏报 — 两处真实违规未被捕获
**Files**:
- `src/web/src/app/analytics/page.tsx:334-338`
- `src/web/src/app/backtest/components/TradesTab.tsx:340-342`

**Issue**: FR-3.2 要求 `<Legend>` 使用 `wrapperStyle={CHART_LEGEND_STYLE}` 或 spread 形式。上述两处使用手写内联 `wrapperStyle`，但由于 `scan_r8()` 中 PCRE2 模式 `<Legend\b[^/]*wrapperStyle...` 的 `[^/]*` 不跨行，多行 JSX 元素被漏报，合规脚本错误返回 PASS。

具体内容：
- `analytics/page.tsx:337`: `wrapperStyle={{ fontSize: 10, fontFamily: "var(--font-mono)" }}`（未使用 CHART_LEGEND_STYLE，fontFamily 使用 `--font-mono` 而非 `--font-d`）
- `TradesTab.tsx:341`: `wrapperStyle={{ fontSize: 10, color: "var(--t2)" }}`（未使用 CHART_LEGEND_STYLE，缺少 color 字段的规范值）

**Fix**:
1. `analytics/page.tsx:337` 改为 `wrapperStyle={{ ...CHART_LEGEND_STYLE, fontSize: 10 }}`
2. `TradesTab.tsx:341` 改为 `wrapperStyle={{ ...CHART_LEGEND_STYLE }}` 并保留 `formatter` prop
3. `scan_r8()` 改用 `rg -U --multiline-dotall` 或两阶段模式（先找 `<Legend` 块，再检查 wrapperStyle）

---

#### [MEDIUM] R9 对 CHART_LABEL_STYLE.fontSize 属性访问存在误判漏报
**File**: `src/web/src/app/trading/components/tabs/RiskTab.tsx:187`
**Issue**: FR-3.2 要求 `label={{ ...CHART_LABEL_STYLE, value: "..." }}` spread 形式。`RiskTab.tsx:187` 使用 `label={{ value: "阈值", position: "insideTopRight", fill: "var(--warn)", fontSize: CHART_LABEL_STYLE.fontSize }}`——只取了 `.fontSize` 属性，未使用 spread，导致 `fill` 字段与 `CHART_LABEL_STYLE.fill`（`var(--t2)`）语义不同（此处 fill 为 `var(--warn)` 覆盖）。这是正确的覆盖意图，但形式上不符合 FR-3.2 规范。

`scan_r9_v2()` 的 awk 检测 `!/CHART_LABEL_STYLE/` 时，因行中确实含有字符串 `CHART_LABEL_STYLE`（作为属性访问），条件为假，导致漏报。
**Fix**:
1. `RiskTab.tsx:187` 改为 `label={{ ...CHART_LABEL_STYLE, value: "阈值", position: "insideTopRight", fill: "var(--warn)" }}`
2. `scan_r9_v2()` awk 条件改为 `!/\.\.\.CHART_LABEL_STYLE/`（检测 spread 语法而非字符串存在）

---

#### [MEDIUM] analytics/page.tsx 含多个未使用 import
**File**: `src/web/src/app/analytics/page.tsx:22`
**Issue**: ESLint 报告 `TrendingUp`、`Grid3x3`、`Activity`、`BarChart3`、`CorrelationEntry` 5 个 import 定义但从未使用。虽为 warning 级别，但属于死代码，增加 bundle 体积，违反代码质量基准。
**Fix**: 删除第 22 行中未使用的图标 import 和第 48 行 `CorrelationEntry` 类型 import。

---

#### [MEDIUM] PerformanceTab.tsx 含未使用变量
**File**: `src/web/src/app/backtest/components/PerformanceTab.tsx:683`
**Issue**: ESLint 报 `months` 赋值后从未被使用（`no-unused-vars` warning）。在 `useMemo` 解构中声明但未消费，属于死代码。
**Fix**: 从解构中移除 `months`，或使用 `_months` 前缀标记为有意忽略。

---

#### [MEDIUM] globals.css 行数超出预期删除量 — 需确认是否误删
**File**: `src/web/src/app/globals.css`
**Issue**: 1-requirements.md NFR-2 目标：删除约 780 行遗留定义后应为 1210±50 行。实际结果：1987 → 785 行（删除 1202 行），超出目标 420 行。verifier 报告也指出此异常。初步检查 `globals.css` 当前内容保留了 `qds-*` 业务组件 class（≥ 15 个选择器）、`@keyframes`、token 定义，`.light` overrides 均在位，R11 通过。但 785 行相比 1987 行削减了 60%，而规格预计仅 39%，差异较大。
**Fix**: 运行 `git diff HEAD -- src/web/src/app/globals.css` 全量 review，确认 1202 行删除内容全部属于 legacy 清单（`.bt-*` ~400 + `.dc-*` ~250 + single-token 1 + factor-research ~135 = 约 786 行），如有超删需恢复。若确认无超删，更新 CLAUDE.md 中「globals.css 实际删除数据」段落的 NFR-2 行数说明。

---

#### [LOW] 新增文件未 git add — NFR-3 可追溯性
**Files**:
- `src/web/src/app/research/components/`（8 个新文件，untracked）
- `src/web/src/app/research/report/[id]/components/`（8 个新文件，untracked）
- `src/web/scripts/verify-ds-compliance.sh`（untracked）

**Issue**: NFR-3 要求新增子文件通过 `git add` 明确标记。当前 17 个文件均处于 untracked 状态，不在 `git diff` 统计中，影响 code review 可见性和历史追溯。
**Fix**: `git add src/web/src/app/research/components/ src/web/src/app/research/report/[id]/components/ src/web/scripts/verify-ds-compliance.sh`

---

#### [LOW] trading/components/tabs/OverviewTab.tsx 使用内联 tick 样式而非 CHART_AXIS_STYLE
**File**: `src/web/src/app/trading/components/tabs/OverviewTab.tsx:251-291`
**Issue**: FR-3.2 规定 `<XAxis tick={CHART_AXIS_STYLE} />` 替代手写 axis tick style。OverviewTab 使用 `tick={{ fill: "var(--t3)", fontSize: 9, fontFamily: "var(--font-mono)" }}` 内联形式，未通过 `CHART_AXIS_STYLE`，且 fontFamily 使用 `--font-mono`（而非 `--font-d` alias），风格不统一。注意 `var(--font-mono)` 与 `var(--font-d)` 在 globals.css 中是同一 token，功能等价，但不符合 chartTheme 常量规范。此处标注为 LOW 是因为 R1 扫描未覆盖此形式（Recharts prop 非 DOM `style` 属性），但仍属于改进方向。
**Fix**: 改为 `tick={CHART_AXIS_STYLE}`，如需不同 fontSize 使用 `tick={{ ...CHART_AXIS_STYLE, fontSize: 9 }}`。

---

#### [LOW] OverviewGreyTab.tsx 使用硬编码 hex 颜色于 Recharts SVG prop
**File**: `src/web/src/app/backtest/components/OverviewGreyTab.tsx`
**Issue**: RadarChart 的 Radar/stopColor 使用 `#4C9EEB`、`#EF5350`、`#A78BFA` 等硬编码 hex，未使用 CHART_COLORS 语义色。虽然 R5 扫描豁免 Recharts fill/stroke prop（符合规格），但 `#A78BFA` 是 purple，应对应 `text-primary`（`var(--acc)` = `#D97857`），可能存在颜色语义误用。`#4C9EEB` 用于 equity 曲线与 `var(--info)` 的 `#85B7EB` 在暗色主题下有色差。
**Fix**: 将 `stroke="#4C9EEB"` 改为 `stroke={CHART_COLORS.info}`，将 `stopColor="#A78BFA"` 改为 `stopColor={CHART_COLORS.accent}` 或保留原值（若有设计依据），在代码中添加注释说明原因。

---

### 正面观察

1. **R1-R14 扫描脚本质量高**：65 个 selftest 用例全部通过，PCRE2 前后向断言实现正确，多行场景（R9 multiline、R6 multiline）均有覆盖，fix-hint 提示完整。

2. **chartTheme.ts 扩展规范**：`CHART_LEGEND_STYLE` 和 `CHART_LABEL_STYLE` 常量定义清晰，类型标注准确，注释说明了 fontFamily 为何故意省略（避免 ReferenceLine label 字体 shift）。

3. **factor-research 迁移彻底**：所有 `.sc/.sc-l/.hm-*/.fsel/.ctbl` 等 factor-research 原语调用点（85 个 class 实例）均已迁移，扫描 R14 全部 0 violations。

4. **MonthlyHeatmap 内联实现干净**：用 Tailwind 原子类（`hmLabelCls`/`hmCellCls` 局部常量）+ `gridTemplateColumns` 内联样式正确替代了 `hm-grid/hm-label/hm-cell` factor-research 原语，且 dynamic rgba 背景色使用计算值（不硬编码），符合 FR-1.5。

5. **StatusBadge QDS 扩展设计**：新版 `status-badge.tsx` 引入 `locale` prop 支持中英双语、`children` override 支持自定义 label、`PulseRing` 动画仅在 `running` 时渲染，设计合理。

6. **data-catalog/types.ts TYPE_BADGE_CLS 迁移完整**：12 个字典 value 全部从 `dc-type-*` 改为 Tailwind 语义类（`bg-qds-info-dim text-qds-info` 等），符合 R3 和 FR-3.4。

7. **research 子系统拆分成功**：`research/page.tsx` 从 991 行缩减至 398 行，`ReportClient.tsx` 从 757 行缩减至 193 行，成功提取了 10 个新组件（含 `VerdictBadge`、`ParamRow`、`ResearchFactorList` 等），FR-4.1 在 research 路由上完全达成。

8. **CLAUDE.md 更新完整**：新增「标准化后的约束」章节结构完整，含禁区 class 清单、Tailwind 首选顺序、Recharts 统一入口表、Historical Notes 区块，AC-4 文档事实来源验收已达成。

---

### 判定

**REQUEST CHANGES**

存在 5 个 HIGH 问题，其中问题 1（import 大小写 build 阻断）、问题 2（StatusBadge 双实现并存）、问题 3-4（FR-4.1 文件拆分未执行）需要 executor kickback 修复。合规扫描层（R1-R14）完全通过，表明 class 迁移工作质量良好；主要缺陷集中在结构性工作（文件拆分）和 QA 层（build 阻断、组件统一）的未完成。

---

VerifyPass: code-reviewer
Verdict: FAIL
