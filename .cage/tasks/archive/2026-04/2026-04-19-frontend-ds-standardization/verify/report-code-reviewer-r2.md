## 代码审查报告

**任务**: 前端设计系统标准化 (2026-04-19-frontend-ds-standardization)
**审查轮次**: Round 2
**审查日期**: 2026-04-20
**审查文件数**: 47（修改 21 + 新增 untracked 26）
**变更范围**: backtest/ 大文件拆分 + R1/R2 HIGH 闭环

---

### 按严重程度

- CRITICAL: 0
- HIGH: 0
- MEDIUM: 2（应该修复）
- LOW: 2（可选）

---

### Stage 1: 规格合规

**整体结论**: 通过 — R1 的 5 个 HIGH 全部闭环，Quality Gates 全部通过。

#### R1 HIGH 闭环验证

**F1 — import 大小写 bug（已修复 / CLOSED）**

git ls-files 与 FS 目录对比全通过：`src/web/src/components/ui/` 下 25 个文件，git track 名与 FS 实际名完全一致（全小写），无大小写偏差。全仓扫描（112 个 tracked 文件）未发现任何 FS vs git 大小写不一致。`npx tsc --noEmit` 0 errors，`npm run build` 16 页全生成。

**F2 — StatusBadge 统一（已修复 / CLOSED）**

- `src/web/src/components/StatusBadge.tsx` 已改写为 barrel re-export：`export { StatusBadge, type StatusKind } from "@/components/qds/status-badge";`
- `src/web/src/components/qds/status-badge.tsx` StatusKind 扩展为 10 键：`running | done | completed | failed | queued | paused | flattening | starting | cancelling | cancelled`，LABEL_MAP_ZH/EN 和 COLOR_MAP 全部覆盖
- `page.tsx:130` 和 `optimization/page.tsx:13` 从 `@/components/StatusBadge` 导入 → 自动解析到 QDS 版本（barrel re-export 正确）
- `JobQueue.tsx:189` 使用 `status="cancelled"` — StatusKind 已包含此键，类型兼容

**F3 — backtest/page.tsx 拆分（已修复 / CLOSED）**

- 主文件：1805 行 → 130 行（减少 92.8%），明确符合 FR-4.1 < 700 行要求
- 新增子文件结构：
  - `BacktestListView.tsx`（221 行）+ `BacktestDetailView.tsx`（130 行）+ `BacktestCreateView.tsx`（535 行）— 三视图模式分明
  - `BacktestRunRow.tsx`（440 行）+ `BacktestPagination.tsx`（56 行）+ `BacktestRunningPlaceholder.tsx`（119 行）+ `BacktestSubscriptionTable.tsx`（295 行）— 列表行 + 辅助组件
  - `backtestStyles.ts` — 共享 class 常量（ACT_BTN_CLS / VIEW_BTN_CLS / ACCENT_BG_MAP 等）统一一处，DRY 良好
  - `hooks/useBacktestRuns.ts` + `hooks/useBacktestDetail.ts` — 数据层与视图层分离

**F3 — PerformanceTab / TradesTab / OverviewTab 拆分（已修复 / CLOSED）**

| 文件 | 原行数 | 现行数 | 新增子文件数 | 达标 |
|---|---:|---:|---:|:---:|
| PerformanceTab.tsx | 2061 | 296 | 8 个（Equity/Drawdown/Period/Rolling/Distribution + Helpers/MetricsSummary） | ✓ |
| TradesTab.tsx | 851 | 264 | 3 个（TradesCharts/TradesHelpers/TradesMetricCard） | ✓ |
| OverviewTab.tsx | 836 | 319 | 5 个（KpiGrid/MonthlyHeatmap/DistributionBars/TradeTables/Helpers） | ✓ |
| OverviewGreyTab.tsx | 677 | 676 | — | ✓（接近阈值，FR-4.1 评估后允许不拆） |

命名规范符合 FR-4.2：`<ChartKind>Chart.tsx`（PerformanceEquityChart/DrawdownChart/DistributionChart 等），helper 集中在 `*Helpers.tsx`，数据 hook 在 `hooks/` 子目录。

**F4/F5 — R8 多行漏报修复（已修复 / CLOSED）**

- `analytics/page.tsx:338`：`wrapperStyle={CHART_LEGEND_STYLE}` ✓（已从手写内联改为常量引用）
- `backtest/components/TradesCharts.tsx:200-201`：`wrapperStyle={CHART_LEGEND_STYLE}` ✓（TradesTab 拆分后 Legend 移至 TradesCharts.tsx）
- `verify-ds-compliance.sh --selftest` 70/70 通过，R8 multiline 测试覆盖两阶段扫描

#### Quality Gates 全通过

- `npx tsc --noEmit`: 0 errors ✓
- `npm run build`: 16 静态页面全生成 ✓
- `bash scripts/verify-ds-compliance.sh`: R1-R14 全 0 violations ✓
- `bash scripts/verify-ds-compliance.sh --selftest`: 70/70 ✓
- `npm run test:fonts`: 15/15 ✓
- ESLint: 46 errors / 39 warnings（baseline 48/43，略有改善）

---

### 问题列表

---

#### [MEDIUM] RiskTab.tsx:187 — CHART_LABEL_STYLE 属性访问而非 spread（R1 MEDIUM 未完全修复）

**File**: `src/web/src/app/trading/components/tabs/RiskTab.tsx:187`

**Issue**: R1 kickback 要求将 `label={{ value: "阈值", fill: "var(--warn)", fontSize: 9 }}` 改为 `{...CHART_LABEL_STYLE, ...}` spread 形式。实际修改将 `fontSize: 9` 替换为 `fontSize: CHART_LABEL_STYLE.fontSize`，但仍未使用 spread — 违反 FR-3.2 规范（`<ReferenceLine label={{ ...CHART_LABEL_STYLE, value: "..." }}>`）。

当前代码：
```tsx
label={{ value: "阈值", position: "insideTopRight", fill: "var(--warn)", fontSize: CHART_LABEL_STYLE.fontSize }}
```
应为：
```tsx
label={{ ...CHART_LABEL_STYLE, value: "阈值", position: "insideTopRight", fill: "var(--warn)" }}
```

**根因分析**: `scan_r9_v2` 的 awk 判断条件 `!/CHART_LABEL_STYLE/` 检测字符串是否存在，而 `CHART_LABEL_STYLE.fontSize` 包含字符串 `CHART_LABEL_STYLE`，导致 false negative——规则误判为合规。此扫描漏洞允许不符合规范的属性访问形式通过 R9 检查。

**Fix**:
1. `RiskTab.tsx:187` 改为 `label={{ ...CHART_LABEL_STYLE, value: "阈值", position: "insideTopRight", fill: "var(--warn)" }}`
2. `scan_r9_v2` awk 条件从 `!/CHART_LABEL_STYLE/` 改为 `!/\.\.\.CHART_LABEL_STYLE/`（检测 spread 语法而非字符串存在）；同时在 `--selftest` 中新增 `assert_no_match R9 '<ReferenceLine label={{ value: "x", fontSize: CHART_LABEL_STYLE.fontSize }} />'`（属性访问应命中，当前无此负例）

---

#### [MEDIUM] ReportsTab.tsx — 未使用的 import（新增文件引入死代码）

**File**: `src/web/src/app/backtest/components/ReportsTab.tsx:12`

**Issue**: 从 `@tanstack/react-table` 导入的 `createColumnHelper` 未被使用（ESLint `@typescript-eslint/no-unused-vars` warning）。该文件是拆分过程中新增的，在迁移时 import 未清理。不影响构建，但属于死代码。

**Fix**: 删除第 12 行的 `createColumnHelper,` import。

---

#### [LOW] 26 个新增文件全部处于 untracked 状态 — NFR-3 可追溯性

**Files**: `src/web/src/app/backtest/components/` 下 22 个新文件 + `src/web/src/app/backtest/hooks/` 下 2 个文件 + `src/web/src/app/research/components/` + `src/web/src/app/research/report/[id]/components/`（共 26 个 untracked 条目）

**Issue**: NFR-3 要求新增子文件通过 `git add` 明确标记，使其出现在 `git diff --stat` 统计中，确保 code review 可见性和 blame 历史建立。当前所有新文件均为 `??`（untracked），不在审查 diff 中。

**Fix**: `git add src/web/src/app/backtest/components/ src/web/src/app/backtest/hooks/ src/web/src/app/research/components/ src/web/src/app/research/report/`（延续 R1 LOW 中相同建议，仍未完成）

---

#### [LOW] BacktestListView.tsx:133-134 — 动态颜色使用 inline style 而非语义类

**File**: `src/web/src/app/backtest/components/BacktestListView.tsx:133-134`

**Issue**: 状态摘要条目使用 `style={{ background: item.color }}` / `style={{ color: item.color }}` 内联样式，其中 `item.color` 是运行时 CSS 变量字符串（`"var(--info)"` / `"var(--suc)"` 等）。从功能正确性和 R12 合规角度看这是合法的（动态值不能用静态 Tailwind 类替代），但从设计系统规范角度，状态计数条可以改用 `StatusBadge` 组件或将颜色 map 改为 Tailwind class map（`"text-qds-info"` / `"text-qds-success"` 等），这样在 CSS class 基础上完全消灭运行时 inline style。

**Fix** (可选): 将 `color` 字段从 CSS 变量字符串改为 Tailwind 类名，使用 `className={item.textCls}` 替代 `style={{ color }}`：
```ts
{ key: "running", textCls: "text-qds-info", dotCls: "bg-qds-info", label: `...` }
```

---

### Stage 2: 代码质量

#### 27 个新增子文件质量评估

**SOLID / DRY**

- `backtestStyles.ts` 正确地将所有共享 class 常量集中导出，避免了各文件重复定义相同的 `ACT_BTN_CLS`/`VIEW_BTN_CLS`，DRY 遵守良好。
- `OverviewHelpers.tsx` / `PerformanceHelpers.tsx` / `TradesHelpers.tsx` 三个 helper 文件分别承载各 tab 的辅助函数和小组件，单一职责清晰（SRP 符合）。
- `BacktestDetailView.tsx` 作为 tab 路由壳（130 行），仅负责 tab 切换和 placeholder 逻辑，无业务数据耦合，符合 OCP。

**类型严格性**

- 新增文件中未发现新 `any` 类型（全仓 grep 0 命中）。
- `BacktestListView.tsx` 显式定义 `BacktestRunSummary` / `BacktestProgressDetail` / `BacktestListViewProps` 接口，props 类型完整。
- `BacktestDetailView.tsx` 的 `BacktestDetailViewProps` 接口完整，`setActiveTab: (key: string) => void` 类型明确。

**React hooks 依赖**

- 新增文件中无 `rules-of-hooks` 违规（仅 `RobustnessTab.tsx` 的 pre-existing 违规保留，该文件为修改而非新增）。
- `useBacktestRuns.ts` / `useBacktestDetail.ts` 两个 hook 文件已检查，无条件调用问题。
- ESLint `react-hooks/exhaustive-deps` 警告 2 条均来自 `RobustnessTab.tsx`（pre-existing）。

**死代码**

- `ReportsTab.tsx:12` `createColumnHelper` 未使用 import（已在 MEDIUM 中报告）。
- 其他新增文件无检测到死代码或未使用变量。

**PerformanceRollingChart.tsx 行数**（638 行）接近阈值但在 700 行以内，包含 5 个不同 rolling 指标图（Sharpe/Sortino/Volatility/Beta/Returns），每个图表实现在 100-150 行区间，合理。

#### 安全检查

- 新增 form 元素（`BacktestCreateView.tsx`）均有 label/placeholder，无无障碍缺失。
- `BacktestCreateView.tsx` 中 API 请求使用 `apiPost`/`apiGet` 封装（`lib/api.ts` 中统一处理），无裸 `fetch` + SQL 注入风险。
- 无硬编码 API key 或 token。

#### Recharts 合规检查

对所有新增 backtest 子文件中的 Recharts 用法抽样检查：

- `TradesCharts.tsx:200-201`：`<Legend wrapperStyle={CHART_LEGEND_STYLE}` ✓
- `OverviewTab.tsx:186`：`<ReferenceLine label={{ ...CHART_LABEL_STYLE, value: "..." }}` ✓
- `PerformanceEquityChart.tsx`：`<CartesianGrid {...CHART_GRID_STYLE}` ✓，`<RechartsTooltip {...CHART_TOOLTIP_PROPS}` ✓
- `PerformanceRollingChart.tsx`：`RollingLegend` 是自定义 `<div>` 图例（非 Recharts `<Legend>` 组件），不在 R8 规则范围内，合法。

#### feedback-agent-frontend-unreliable 失效模式检查

- **Inline style 替代 CSS class**：新增文件中 inline style 仅在动态颜色场景出现（`style={{ background: item.color }}`），无静态装饰性 fontFamily/fontSize 内联，合规。
- **新发明的 class 名**：未发现 `bt-*/dc-*` 以外的自创 class 名；所有 class 均为 Tailwind 语义类或 QDS class（`qds-section-label`）。
- **Unicode 转义字符**：全仓扫描未发现 `\u` 转义（`PredictivePowerTab.tsx` 中的 `\u0304` 是 IC̄ 上划线，属于内容字符串而非代码问题）。

---

### Stage 3: 根因风险评估 — FS vs git 大小写偏差

**检查结果**: 全仓扫描 112 个 tracked 文件，0 个 FS vs git 大小写不一致。

R1 的 F1 问题根因（5 个 `ui/` 组件文件被以 PascalCase 写入 FS 但 git 记录小写）已通过 two-step mv 完全修复。当前：

```
src/web/src/components/ui/card.tsx  (FS) = card.tsx (git) ✓
src/web/src/components/ui/dialog.tsx (FS) = dialog.tsx (git) ✓
src/web/src/components/ui/table.tsx (FS) = table.tsx (git) ✓
```

**Recommendation**: 该大小写偏差的根因是 macOS 的大小写不敏感文件系统（HFS+/APFS）导致 git 不检测文件名大小写变化。建议：

1. 在 `pre-commit` hook 中加入检查：`git ls-files | python3 -c "import sys; [print(l) for l in sys.stdin if l.strip() != l.strip().lower() and l.strip().endswith('.tsx')]"` — 禁止新增 PascalCase TSX 路径进入 git track（仅对 ui/ 和 components/ 目录下的文件）
2. 在 `verify-ds-compliance.sh` 脚本的 preflight 阶段加入 `git ls-files src/web/src/components/ui/ | grep -v '^[a-z/.-]' && exit 2` 检查，防止 FS 大写漂移重现

---

### 正面观察

1. **拆分质量超预期**：backtest page 从 1805 行降至 130 行（主文件减少 92.8%），拆分粒度合理——每个子文件聚焦单一图表类型或单一数据域，无"上帝文件"转移现象。

2. **MonthlyHeatmap 内联 Tailwind 实现完整**：`OverviewMonthlyHeatmap.tsx` 用 `hmLabelCls` / `hmCellCls` 局部常量 + `gridTemplateColumns` 内联样式正确替代 `hm-grid/hm-label/hm-cell` factor-research 原语，动态 rgba 背景色用计算值（`0.12 + ratio * 0.45` alpha），不硬编码，符合 FR-1.5。

3. **StatusBadge 设计规范**：`PulseRing` 动画仅在 `running` 状态渲染（避免无谓 DOM 节点），`children` prop 支持自定义 label，`locale` prop 支持中英双语，`StatusKind | (string & {})` 类型放宽既保留类型安全又允许未来状态扩展，设计合理。

4. **backtestStyles.ts 共享常量**：将 `ACT_BTN_CLS` / `VIEW_BTN_CLS` / `ACCENT_BG_MAP` / `STATUS_PILL_MAP` 集中到独立 `.ts` 文件，使 `BacktestListView` / `BacktestDetailView` / `BacktestRunRow` 三处调用的视觉锁定依靠单点定义——正确的 DRY 实践，防止后续多处修改不同步。

5. **R8 multiline 两阶段扫描修复彻底**：selftest 70/70，R8 实现覆盖了 `wrapperStyle={{ ...CHART_LEGEND_STYLE, extra }}` spread-extra-prop 形式，analytics/page.tsx 和 TradesCharts.tsx 两处 Legend 均已用 `wrapperStyle={CHART_LEGEND_STYLE}` 替换，合规。

6. **FS/git 大小写问题完全消除**：Root cause fix（5 个文件 two-step rename）而非 symptom fix（只改一处 import），使该问题不会在其他调用方重现。

---

### 判定

**APPROVE**

R1 的 5 个 HIGH 问题全部闭环：
- F1 import 大小写 bug：根因修复（所有 ui/ 文件 FS-git 名一致）
- F2 StatusBadge 统一：barrel re-export + StatusKind 10 键扩展完成
- F3 backtest/page.tsx + PerformanceTab/TradesTab/OverviewTab：全部拆分至主文件 < 700 行，共新增 27 个子文件
- F4/F5 R8 多行漏报：脚本修复 + 两处 Legend 手写内联改为常量

剩余问题均为 MEDIUM/LOW 级别，不阻断发布：
- `RiskTab.tsx:187` 使用属性访问而非 spread（MEDIUM）— 功能等价，视觉无差异，但规范不一致；R9 扫描脚本亦需同步修复 false negative
- `ReportsTab.tsx:12` 未使用 import（MEDIUM）— 纯代码卫生问题
- 26 个新文件 untracked（LOW）— NFR-3 可追溯性，不影响运行
- `BacktestListView` 动态颜色 inline style（LOW）— 功能正确，仅风格层面改进

---

VerifyPass: code-reviewer
Verdict: PASS
