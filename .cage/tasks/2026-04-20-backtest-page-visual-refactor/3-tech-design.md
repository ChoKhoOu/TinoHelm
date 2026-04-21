---
task: backtest-page-visual-refactor
created: 2026-04-20
phase: plan
---

# 技术设计 · 回测页面视觉重构

## 1. 架构对齐分析（棕地）

### 1.1 现有架构模式
通过阅读 `src/web/src/app/backtest/` 全目录得出以下既有模式：

| 模式类别 | 现有实现 | 应用范围 |
|---|---|---|
| **变体处理** | Status map 对象（`ACCENT_BG_MAP` / `STATUS_PILL_MAP` / `LABEL_MAP_ZH`）+ 值对应 Tailwind class 字符串 | 列表行状态、StatusBadge |
| **视图切换** | 单 `View` 类型 literal union（`"list" \| "create" \| "detail"`），`useState<View>` 驱动条件渲染 | page.tsx |
| **分层** | 顶层 view component → tab component → helper + chart subcomponent（如 `Overview*`、`Performance*`、`Trades*`） | components/ 目录 |
| **共享常量** | `backtestStyles.ts` 聚合 form/button/accent/pill 等 class 常量 | 跨 view 组件 |
| **样式源** | 优先 Tailwind semantic → QDS 扩展 class → shadcn 原语 → Tailwind 原子类 | 全仓 |
| **chart 配置** | `lib/chartTheme.ts` 集中 `CHART_TOOLTIP_PROPS / CHART_GRID_STYLE / CHART_COLORS / CHART_ANIMATION` | 所有 Recharts |
| **数据钩子** | 按视图拆分的 `useBacktestRuns` / `useBacktestDetail`，WS + 轮询 + 缓存 | page.tsx + detail view |
| **动效** | `animate-qds-fade-up` + `animation-delay` 分级；`ease-qds` easing；`max-height` + duration 实现展开 | 全仓 |

### 1.2 本次设计如何顺着现有模式走
- **视图拆分**：新增 Trades 视图作为**平级 view state**（`View = "list" | "detail" | "trades"`，**删除** 原 `"create"`，由 Sheet open state 替代），而不是嵌套进 detail。
- **变体处理**：创建 sheet stepper 的 3 个 step 状态、fill model 的 9 种选项、trades 筛选的 3×3 tab 组合，全部走 **map 对象 + Tailwind class 字符串** 模式（不引入新的条件分支工厂）。
- **常量集中**：新增 `backtestStyles.ts` 的以下导出（延续现有命名）：
  - `STEPPER_DOT_CLS_MAP`: `{ active: "...", completed: "...", pending: "..." }`
  - `TIMEFRAME_CHIP_CLS`: 快捷 chip 激活/非激活变体
  - `TRADES_SIDE_BADGE_CLS`: 沿用 Overview 的 `text-qds-success/text-destructive` 规则
  - `SHEET_STEP_CONTENT_CLS`: 引用新增的 `animate-slide-in-up` + delay 组合
  - `FORM_SECTION_STATIC_CLS`: 无动画版（见 §7.5）
- **自绘 SVG**：放 `OverviewEquitySvg.tsx`，延续 `OverviewXxx` 文件命名规约；token-only fill/stroke；通过 prop 驱动数据；内部 state 仅控制动画触发。
- **Sheet 组件**：直接复用 `components/ui/sheet.tsx`（shadcn Sheet + base-ui dialog primitive），无需封装。
- **chart theme**：equity+drawdown 自绘 SVG 使用 `CHART_COLORS.info / CHART_COLORS.danger / var(--chart-grid)`；Monthly heatmap 色阶使用 `color-mix()` + `var(--suc) / var(--dan)`；不引入任何新的色值入口。

### 1.3 本次设计的刻意偏离
- **列表视图结构**：从当前的 `grid gridTemplateColumns: "3px 1fr auto auto auto"` 改为 `grid-cols-10` 语义化列（但仍保留 3px 色条 cell）。**偏离理由**：mock 要求 10 列视觉密度（策略+标的合并 2 列、状态 1 列、进度 1 列、4 个指标列、actions 1 列 + 3px 色条列），当前 5 列结构无法容纳这么多信息而保持一致对齐。技术上仍是 grid，非重构。
- **创建视图**：从 view state 改为 Sheet overlay。**偏离理由**：用户明确要求 mock 的右侧抽屉交互；保留在 view state 中会破坏 list ↔ sheet 并存的视觉效果（sheet 可在列表背景上滑出）。
- **Overview equity+drawdown 图**：从 Recharts 双列 AreaChart 改为单块自绘 SVG 叠加图。**偏离理由**：mock 要求 strokeDasharray 绘制动画 + 单块双曲线叠加（而非并排）；Recharts 不支持 `strokeDashoffset` 动画属性（仅支持 `animationBegin/duration`，动画形态固定）。**替代方案评估**：考虑过用 framer-motion 的 `motion.path`，但 bundle size 增加且与 Recharts 的 viewport 管理冲突；自绘 SVG + CSS keyframe 方案最轻量。**权衡**：失去 Recharts 原生 tooltip/hover/ResponsiveContainer，FR-091 明确"本次不实现 tooltip"，未来若需恢复需 ~150 LOC 自绘 hit-box（Out-of-scope）。

## 2. 组件架构图

```
BacktestPage (page.tsx) — View 枚举改为 3 态（list/detail/trades），Sheet open state 替代 create
  ├── BacktestCreateSheet (Sheet overlay — 外壳 + state owner)
  │   ├── Sheet / SheetContent / SheetHeader / SheetFooter (shadcn)
  │   ├── BacktestCreateStepper (3 dot + label + connector)
  │   ├── BacktestCreateStep1 (受控：策略 + 标的搜索 + chip 列表)
  │   ├── BacktestCreateStep2 (受控：周期 chip + 日期 + estimate)
  │   └── BacktestCreateStep3 (受控：资金 + 高级折叠区)
  │       └── BacktestSubscriptionTable (折叠区内嵌, FORM_SECTION_STATIC_CLS)
  │
  ├── BacktestListView (列表视图 — 重构)
  │   ├── PageHeader (保留)
  │   ├── StatusSummaryStrip (内联)
  │   ├── BacktestRunRow (running/queued, 10 列 grid)
  │   │   ├── RingProgress (BacktestRunRow 内嵌)
  │   │   └── ShimmerBar (components/qds/)
  │   ├── BacktestHistoryRow (completed/failed, 10 列 grid)
  │   │   └── RetryButton (failed 行)
  │   └── BacktestPagination (保留)
  │
  ├── BacktestDetailView (详情视图)
  │   ├── DetailHeader (保留)
  │   ├── DetailKpiGrid (新增 6 列 KPI, 内嵌 in BacktestDetailView.tsx)
  │   ├── Pill TabBar (保留)
  │   └── Tab Contents (7 个 tab)
  │       ├── OverviewTab (改造)
  │       │   ├── OverviewKpiGrid (secondary 11 指标, 保留)
  │       │   ├── OverviewEquitySvg (新增自绘 SVG)
  │       │   ├── OverviewMonthlyHeatmap (改 color-mix 色阶)
  │       │   ├── DrawdownTable (保留, top 4)
  │       │   └── 其他既有 block 保留（SectionLabel 归一化）
  │       ├── PerformanceTab ~ ReportsTab (保留 · SectionLabel 归一化)
  │
  └── BacktestTradesView (所有交易视图 — 全新)
      ├── TradesViewHeader (返回 + 标题)
      ├── TradesSummaryStrip (6 列 summary, useMemo)
      ├── TradesFilterBar (方向 + 结果 pill tab + 搜索 + ⌘K binding)
      ├── TradesTable (shadcn Table · 8 列)
      └── BacktestPagination (复用)
```

图例：
- 粗体 = 新增文件/组件
- "保留" = 不动
- "改造" / "微调" = 结构/样式变更，API 签名保持

## 3. 文件清单

### 3.1 新增文件（7 个）

| 路径 | 用途 | 预估 LOC |
|---|---|---|
| `src/web/src/app/backtest/components/BacktestCreateSheet.tsx` | Sheet 外壳 + stepper + step 切换状态机 + **跨步 state owner**（form / subscriptions / strategyParams / paramOverrides / paramsExpanded / advancedExpanded / step / fromRetry） | ~220 |
| `src/web/src/app/backtest/components/BacktestCreateStepper.tsx` | Stepper header 组件（3 dot + label + connector） | ~60 |
| `src/web/src/app/backtest/components/BacktestCreateStep1.tsx` | Step 1 受控：策略下拉 + 标的搜索 + 已选 chip（仅 onChange form.strategy_name / subscriptions） | ~180 |
| `src/web/src/app/backtest/components/BacktestCreateStep2.tsx` | Step 2 受控：周期 chip（6+1 自定义, 白名单校验）+ 日期 + estimate | ~160 |
| `src/web/src/app/backtest/components/BacktestCreateStep3.tsx` | Step 3 受控：资金费率延迟 + 折叠高级选项（fill model / subscriptions / warmup / tags / paramOverrides）+ API worker 文案 | ~300 |
| `src/web/src/app/backtest/components/OverviewEquitySvg.tsx` | 自绘 equity+drawdown 叠加 SVG（含 strokeDasharray 动画 + 空数据降级） | ~170 |
| `src/web/src/app/backtest/components/BacktestTradesView.tsx` | 所有交易视图：summary strip (useMemo) + filter + 8 列 table + pagination + ⌘K binding + 空态处理 | ~280 |

### 3.2 修改文件（7 个）

| 路径 | 变更类型 | 变更要点 | 预估 Δ LOC |
|---|---|---|---|
| `src/web/src/app/backtest/page.tsx` | 修改 View 枚举 + 增加 Sheet open state + 新增 trades view 分支 + retry handler | `View` 从 `"list"\|"create"\|"detail"` 改为 `"list"\|"detail"\|"trades"`；用 `sheetOpen` / `setSheetOpen` 控制 Sheet；新增 `retryPrefill: BacktestRunSummary \| null` state；新增 `handleViewAllTrades` / `handleRetry` handlers；BacktestCreateSheet 接受 `open / onOpenChange / retryPrefill` props | ~40 新 / ~10 改 |
| `src/web/src/app/backtest/components/BacktestListView.tsx` | 改表格结构 + 传递 props | 10 列 grid 版式（含 FR-NFR-002 响应式降级的 Tailwind `hidden xl:flex`）；新增 `onCancelRun / onRetryRun` props 透传；保留 active/history 分区；EmptyState 保持 | ~45 改 |
| `src/web/src/app/backtest/components/BacktestRunRow.tsx` | 改表格列布局 + running 展开块 + failed 重试 | 10 列 grid 替换 5 列；running 展开区集成 RingProgress + ShimmerBar + Cancel；failed 行 HistoryRow 增加 retry 按钮；**新增 RingProgress 内嵌函数组件**（见 §4.1 实现） | ~120 改 |
| `src/web/src/app/backtest/components/BacktestDetailView.tsx` | 插入 6 列 KPI 网格 + trades view 入口 | 在 tab bar 之前插入 `<DetailKpiGrid s={...} />`（inline 实现，无 `<StatCard>`）；新增 `onViewAllTrades` prop 从 page.tsx 注入 → 透传到 OverviewTab | ~45 新 |
| `src/web/src/app/backtest/components/OverviewTab.tsx` | 替换 equity+drawdown 双列为自绘 SVG + monthly heatmap 布局改 1.4fr/1fr + 新增 onViewAllTrades + 清理 `#E5534B` | **删除 `:151-226` 双 AreaChart block（~80 行）**；引入 `<OverviewEquitySvg data={equity_curve} />`；monthly + drawdown 合并一行 `grid-cols-[1.4fr_1fr]`；新增「查看所有交易」按钮 + onClick 回调 prop；**全文件 grep `#E5534B` 必须为零** | ~50 改 / ~80 删 |
| `src/web/src/app/backtest/components/OverviewMonthlyHeatmap.tsx` | 色阶改 token-based + 空数据降级 | `cellBg()` 改为 `color-mix(in srgb, var(--suc/--dan) {alpha}%, transparent)`；`val === 0` 返回 `var(--bg-t)`；`years.length === 0` 改 `<InlineError>` 降级（FR-093） | ~20 改 |
| `src/web/src/app/backtest/components/BacktestSubscriptionTable.tsx` | 动画机制迁移（FR-076） | `className={FORM_SECTION_CLS}` → `className={FORM_SECTION_STATIC_CLS}`；删除 `data-form-section` 属性 | ~2 改 |
| `src/web/src/app/backtest/components/PerformanceHelpers.tsx` | 清理硬编码 rgba（NFR-2） | `:167/169/170` 三处 `rgba(76, 158, 235, 0.5)` / `rgba(38, 217, 127, 0.5)` / `rgba(239, 83, 80, 0.5)` 改为 `color-mix(in srgb, var(--info) 50%, transparent)` / `var(--suc) 50%` / `var(--dan) 50%` | ~6 改 |
| `src/web/src/app/globals.css` | 新增 2 个 `@keyframes` | 在现有 keyframes 块后追加 `@keyframes dash` 和 `@keyframes slideInUp` | ~10 新 |
| `src/web/src/app/backtest/components/backtestStyles.ts` | 新增常量 | 追加 `STEPPER_DOT_CLS_MAP` / `TIMEFRAME_CHIP_CLS` / `TRADES_SIDE_BADGE_CLS` / `FORM_SECTION_STATIC_CLS` | ~30 新 |

### 3.3 删除文件（2 个）

| 路径 | 原因 |
|---|---|
| `src/web/src/app/backtest/components/BacktestCreateView.tsx` | 被 `BacktestCreateSheet.tsx` + 3 个 step 组件替代；避免双入口 |
| `src/web/src/app/backtest/components/OverviewGreyTab.tsx` | Grep 确认零外部引用（仅自引用 + 自导出）；37 处硬编码颜色违反 NFR-2；属于历史 deprecated 实现 |

### 3.4 保留不动的文件（关键）

`BacktestPagination.tsx` / `BacktestRunningPlaceholder.tsx` / `OverviewKpiGrid.tsx` / `OverviewTradeTables.tsx` / `OverviewDistributionBars.tsx` / `OverviewHelpers.tsx` / `PerformanceTab.tsx` + `Performance*.tsx`（除 Helpers 颜色清理）/ `TradesTab.tsx` + `Trades*.tsx` / `RobustnessTab.tsx` / `TearsheetTab.tsx` / `TradeLogTab.tsx` / `ReportsTab.tsx` / `hooks/useBacktestRuns.ts` / `hooks/useBacktestDetail.ts`

> **验证**：以上所有文件通过 `ls` 确认存在于 `src/web/src/app/backtest/components/` 和 `hooks/` 目录。

### 3.5 本次范围外的硬编码颜色（AC-C-4 扫描排除）

以下文件中的 `rgba(255, 255, 255, ...)` 轴/grid/tick 样式**不在本次清理范围**（属于 chart 样式专项）：
- `PerformanceRollingChart.tsx:122/245/600`
- `RobustnessTab.tsx:194/195/200/213`
- `PerformancePeriodChart.tsx:72/76/79/204/284`
- `PerformanceEquityChart.tsx:267` `rgba(76,158,235,0.3)`
- `BacktestSubscriptionTable.tsx:130` `rgba(0,0,0,.15)` (boxShadow)
- `BacktestDetailView.tsx:93` `rgba(0,0,0,0.15)` (shadow)

**AC-C-4 的扫描正则仅针对 equity/drawdown 红绿色**（`#E5534B` / `rgba(76, 175, 80` / `rgba(239, 83, 80` / `rgba(76, 158, 235, 0.5)` / `rgba(38, 217, 127, 0.5)`），其它 rgba 不误伤。

## 4. 视图拆解

### 4.1 List View（BacktestListView）

**10 列 grid 结构**（`xl` 视口：`grid-cols-[3px_minmax(200px,2.5fr)_90px_minmax(180px,1.5fr)_120px_minmax(120px,1fr)_80px_80px_100px_110px]`）：

| 列 | 内容 | 渲染 | 类 | 响应式降级 |
|---|---|---|---|---|
| 1 | 3px stripe | `<div className={accentCls} />` | `bg-qds-info/bg-qds-success/bg-destructive/bg-qds-t3` | 始终显示 |
| 2 | 策略 + 标的 | 双行：`font-mono font-medium` + `text-muted-foreground` | — | `<lg` 时第 3 列 run_id 融入第二行追加 |
| 3 | run_id | `<BacktestCopyableId />` | 现有复用 | `hidden lg:flex`（FR-NFR-002） |
| 4 | 周期+日期 | 单行 `text-xs text-muted-foreground` | — | 始终显示 |
| 5 | 状态 | `<StatusBadge status={...} locale="en" />` | — | 始终显示 |
| 6 | 进度/结果 | running→`{pct}%` / queued→pulse dots / done→`$±XX,XXX` / failed→error | 条件渲染 | 始终显示 |
| 7 | Sharpe | `font-mono text-xs` | done 行显示，其他显 `—` | `hidden xl:flex`（FR-NFR-002） |
| 8 | Win Rate | `font-mono text-xs` | 同上 | `hidden xl:flex`（FR-NFR-002） |
| 9 | PnL | `font-mono text-xs` + `text-qds-success`/`text-destructive` | 同上 | 始终显示 |
| 10 | Actions | `View →` / `↻ 重试` / `⌄` 展开 icon | — | 始终显示 |

**响应式 grid 字符串**（通过 Tailwind responsive 变量实现；由于 `grid-cols-[...]` 为 arbitrary value，使用 `lg:grid-cols-[...] xl:grid-cols-[...]` 三档切换）：
```tsx
const GRID_COLS_CLS =
  "grid " +
  // < lg: 7 列（隐藏 run_id, Sharpe, WinRate）
  "grid-cols-[3px_minmax(200px,2.5fr)_minmax(160px,1.5fr)_120px_minmax(100px,1fr)_100px_110px] " +
  // lg: 8 列（隐藏 Sharpe, WinRate，保留 run_id）
  "lg:grid-cols-[3px_minmax(200px,2.5fr)_90px_minmax(180px,1.5fr)_120px_minmax(120px,1fr)_100px_110px] " +
  // xl: 10 列（全量）
  "xl:grid-cols-[3px_minmax(200px,2.5fr)_90px_minmax(180px,1.5fr)_120px_minmax(120px,1fr)_80px_80px_100px_110px]";
```
Sharpe / Win Rate 两列容器在 JSX 上 `className="hidden xl:flex ..."`；run_id 在 `<lg` 时 `hidden`，并在策略+标的 cell 的第二行追加 `<span className="lg:hidden text-[0.6rem] text-muted-foreground">{run_id.slice(0,8)}</span>`。

**Running 展开区**（跨全宽 block）：
```
┌──────────────────────────────────────────────────────────────┐
│ ┌──────┐  ┌──────────────────────────────────────────────┐ │
│ │ ◐ 44 │  │ ShimmerBar accent progress={pct}             │ │
│ │  %   │  └──────────────────────────────────────────────┘ │
│ └──────┘  ┌────────┬────────┬────────┬────────┬────────┐   │
│           │Progress│Elapsed │  ETA   │ Speed  │Processd│Trd│
│           │ {pct}% │ 2m 14s │~1m 20s │ 12K/s  │ 0.4/1M │12 │
│           └────────┴────────┴────────┴────────┴────────┴──┘│
│                                               [Cancel]     │
└──────────────────────────────────────────────────────────────┘
```
- **RingProgress**（内嵌组件 in `BacktestRunRow.tsx`）：
  ```tsx
  function RingProgress({ pct }: { pct: number }) {
    const R = 18, C = 2 * Math.PI * R;
    const offset = C * (1 - Math.max(0, Math.min(100, pct)) / 100);
    return (
      <svg width={44} height={44} viewBox="0 0 44 44" data-ring-progress>
        <circle cx="22" cy="22" r={R} fill="none" stroke="var(--bg-t)" strokeWidth="3" />
        <circle cx="22" cy="22" r={R} fill="none" stroke="var(--info)" strokeWidth="3"
          strokeDasharray={C} strokeDashoffset={offset} strokeLinecap="round"
          transform="rotate(-90 22 22)"
          className="transition-[stroke-dashoffset] duration-[600ms] ease-[cubic-bezier(0.4,0,0.2,1)]"
          // prefers-reduced-motion 下 transition 会被 motion-reduce 覆盖
        />
        <text x="22" y="26" textAnchor="middle" className="font-mono text-[0.7rem] fill-foreground">
          {Math.round(pct)}
        </text>
      </svg>
    );
  }
  ```
- **ShimmerBar**：`<ShimmerBar progress={pct} height="md" active={true} variant="accent" />`。
- **元数据 grid**：`grid grid-cols-6 gap-2`，每 cell 标 `data-meta-cell` + `flex flex-col gap-0.5`（label `text-[0.6rem] uppercase tracking-wider text-muted-foreground` + value `font-mono text-[0.75rem] font-medium`）。
- **Cancel 按钮**：`<button>` 使用 `VIEW_BTN_CLS` + `!text-destructive`。WS 降级态（FR-013）下通过父级 `data-ws-stale="true"` 传递，RingProgress 与 ShimmerBar 的内部 CSS 动画 `animation-play-state: paused`。

**Failed 行扩展区**：retry 按钮 `<button onClick={() => onRetryRun(run)}>`，触发 FR-033 定义的预填逻辑。

### 4.2 Create Sheet（含 state ownership）

**State ownership 模式**（受控 / lifted state）：

```tsx
// BacktestCreateSheet.tsx — 所有 state 在此
export function BacktestCreateSheet({
  open, onOpenChange, retryPrefill, onSubmit,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  retryPrefill: BacktestRunSummary | null;  // 非 null 时进入 retry mode
  onSubmit: () => void;
}) {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [form, setForm] = useState<BacktestForm>(INITIAL_FORM);
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([]);
  const [strategyParams, setStrategyParams] = useState<StrategyParam[]>([]);
  const [paramOverrides, setParamOverrides] = useState<Record<string, string>>({});
  const [paramsExpanded, setParamsExpanded] = useState(false);
  const [advancedExpanded, setAdvancedExpanded] = useState(false);
  const fromRetry = retryPrefill !== null;

  // retry 预填 effect
  useEffect(() => {
    if (!open || !retryPrefill) return;
    setForm(f => ({ ...f, start_date: retryPrefill.start_date, end_date: retryPrefill.end_date, /* 保持默认 initial_capital 等 */ }));
    setSubscriptions(
      retryPrefill.symbol.split(",").map(sym => ({
        symbol: sym.trim(),
        timeframe: retryPrefill.interval,
        data_type: "bar",
      }))
    );
    setForm(f => ({ ...f, strategy_name: retryPrefill.strategy_name }));
    setStep(1);
  }, [open, retryPrefill]);

  // sheet 关闭时 reset（下次打开时重置）
  useEffect(() => { if (!open) { setStep(1); /* ... */ } }, [open]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full sm:max-w-[520px] p-0 gap-0 flex flex-col">
        <SheetHeader className="border-b border-border px-5 py-4 gap-3">
          <SheetTitle>创建回测</SheetTitle>
          <BacktestCreateStepper step={step} />
          {fromRetry && <InlineError variant="hint">已复制策略、标的、周期与时间区间，请确认资金与成本参数</InlineError>}
        </SheetHeader>

        <div className="flex-1 overflow-y-auto px-5 py-5">
          {step === 1 && (
            <BacktestCreateStep1 key="s1"
              form={form} onFormChange={setForm}
              subscriptions={subscriptions} onSubscriptionsChange={setSubscriptions}
              onStrategyDefaultsLoad={(d) => { /* 填充 defaults */ }}
            />
          )}
          {step === 2 && (
            <BacktestCreateStep2 key="s2"
              form={form} onFormChange={setForm}
              subscriptions={subscriptions} onSubscriptionsChange={setSubscriptions}
            />
          )}
          {step === 3 && (
            <BacktestCreateStep3 key="s3"
              form={form} onFormChange={setForm}
              subscriptions={subscriptions} onSubscriptionsChange={setSubscriptions}
              paramOverrides={paramOverrides} onParamOverridesChange={setParamOverrides}
              advancedExpanded={advancedExpanded} onAdvancedToggle={setAdvancedExpanded}
              strategyParams={strategyParams}
              paramsExpanded={paramsExpanded} onParamsExpandedChange={setParamsExpanded}
              onSubmit={onSubmit}
            />
          )}
        </div>

        <SheetFooter className="border-t border-border px-5 py-3 flex-row justify-between mt-0">
          {/* Previous/Next，共享 form state 确保跨步保留 */}
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
```

**state 不重建保证**：step 切换通过 `{step === 1 && <Step1 />}` 条件渲染（配 `key={step}` 触发 slideInUp），但 state 存在 Sheet 顶层，子组件 unmount 也不丢。AC-B-7 验证此性质。

**Stepper 组件**：圆点 + label + connector，实现详见旧版设计（保持不变）。

**Step 1 字段分配表**（FR-052）：
| mock 字段 | 字段名 | 类型 | state 归属 | Step 操作 |
|---|---|---|---|---|
| 策略选择（单选） | `form.strategy_name` | string | Sheet | Step1 读+写 |
| 标的搜索（多选 chip） | `subscriptions[]` | `Subscription[]` | Sheet | Step1 读+写（仅 symbol 字段 add/remove） |
| granularity | `subscriptions[i].granularity` | enum | Sheet | **Step 1 不读不写**；Step 3 折叠区 via SubscriptionTable 读+写 |
| dataType | `subscriptions[i].data_type` | enum | Sheet | **Step 1 不读不写**；Step 3 折叠区 via SubscriptionTable 读+写 |

**Step 2 字段分配表**（FR-060/061）：
| mock 字段 | 字段名 | 类型 | 校验 |
|---|---|---|---|
| 快捷 chip（6 个） | 写入 `subscriptions[i].timeframe` | `"1m"|"5m"|"15m"|"1h"|"4h"|"1d"` | 无 |
| 自定义 chip 输入 | 同上 | string | 白名单正则 `/^(1m|3m|5m|15m|30m|1h|2h|4h|6h|8h|12h|1d)$/` |
| 开始日期 | `form.start_date` | ISO date | < end_date |
| 结束日期 | `form.end_date` | ISO date | > start_date |

**Step 3 字段分配表**（FR-070~073）：
| 字段 | 存储 | 位置 |
|---|---|---|
| 初始资金 | `form.initial_capital` | 顶部基础区 |
| Maker/Taker fee | `form.maker_fee` / `form.taker_fee` | 顶部基础区 |
| latency_mode / latency_ms | `form.latency_*` | 顶部基础区 |
| **Fill Model 类型** | `form.fill_model_type` | **折叠区** |
| prob_fill_on_limit / prob_slippage | `form.prob_*` | **折叠区**（type=default 时显示） |
| Subscriptions 详情表 | `subscriptions[]` via `BacktestSubscriptionTable` | **折叠区** |
| 策略参数覆盖 | `paramOverrides` | **折叠区** |
| Warmup bars | `form.warmup_bars` | **折叠区** |
| Tags | `form.tags` | **折叠区** |
| 预估运行时间 | `estimate.estimated_label` | 底部 hint |
| 执行节点文案 | 常量 | "任务将在 **API 回测 worker** 中执行" |

> **FILL_MODEL_OPTIONS 和 parseTimeframe 搬迁**：
> - `BacktestCreateView.tsx:49-59` 的 `FILL_MODEL_OPTIONS` → `BacktestCreateStep3.tsx` 模块顶部。
> - `BacktestCreateView.tsx:61-69` 的 `parseTimeframe` 函数 → `BacktestCreateStep2.tsx` 模块顶部或 `backtestStyles.ts`（推荐 Step2，因仅此处使用）。

### 4.3 Detail View (6 列 KPI 网格)

**实现结构**：手绘 grid cell，不使用 `<StatCard>`（避免 padding/border 与 6 列 grid 冲突，也便于加 `data-kpi-cell`）。

```tsx
<div className="grid grid-cols-6 gap-0 border border-border rounded-lg bg-card overflow-hidden mb-4 animate-qds-fade-up"
     style={{ animationDelay: "80ms" }}
     data-kpi-grid>
  {KPI_ITEMS.map((item, i) => (
    <div
      key={item.key}
      data-kpi-cell
      className={cn(
        "px-4 py-3 flex flex-col gap-0.5",
        i > 0 && "border-l border-border",
      )}
    >
      <div className="font-mono text-[0.56rem] tracking-widest uppercase text-muted-foreground">
        {item.label}
      </div>
      <div className={cn(
        "font-mono text-[1.2rem] font-semibold leading-none",
        item.trend === "up" && "text-qds-success",
        item.trend === "down" && "text-destructive",
      )}>
        {item.value}
      </div>
      {item.sub && (
        <div className={cn("font-mono text-[0.7rem]",
          item.trend === "up" && "text-qds-success",
          item.trend === "down" && "text-destructive",
          !item.trend && "text-muted-foreground",
        )}>
          {item.sub}
        </div>
      )}
    </div>
  ))}
</div>
```

**KPI_ITEMS 定义**（数据来源 `selectedRun.result_summary`，所有字段均为可选；未完成时全体显示 `—`）：

| # | label | value format | sub | trend 判定 | 字段映射 |
|---|---|---|---|---|---|
| 1 | 总盈亏 | `totalPnl >= 0 ? "+$" + abs : "-$" + abs`（千分位） | `{totalRetPct}%` | totalPnl ≥ 0 → up / else down | `result_summary.total_pnl` |
| 2 | 总收益率 | `+/-{X.X}%` | — | totalRetPct ≥ 0 → up / else down | `result_summary.total_return_pct` |
| 3 | Sharpe | `sharpe.toFixed(2)` | `{sharpe >= 1 ? "良好" : "一般"}` | — | `result_summary.sharpe_ratio` |
| 4 | Calmar | `calmar.toFixed(2)` | — | — | `result_summary.calmar_ratio` |
| 5 | 胜率 | `(winRate*100).toFixed(1)%` | `{winningTrades}/{totalTrades}` | — | `result_summary.win_rate` + derived |
| 6 | 交易笔数 | `totalTrades` | — | — | `result_summary.total_trades` |

> **PnL 符号统一**：采用 `totalPnl >= 0 ? "+$" + fmt(abs) : "-$" + fmt(abs)`（即负数时显式 `-` + `$` + 绝对值千分位）。这与 `BacktestHistoryRow.tsx:342` 的逻辑对齐；`BacktestRunRow.tsx:153` 的 `total_pnl >= 0 ? "+" : ""` 在 S3/S4 重构时一并对齐到此规则。

### 4.4 Overview Equity SVG（自绘）

实现思路详见旧版设计（不变），新增空数据降级：

```tsx
export function OverviewEquitySvg({ data, height = 280 }: OverviewEquitySvgProps) {
  // FR-092 空数据降级
  if (!data || data.length === 0) {
    return (
      <div className="flex items-center justify-center" style={{ height }}>
        <InlineError variant="hint">暂无权益曲线数据</InlineError>
      </div>
    );
  }
  // ... 原渲染逻辑
  return (
    <svg viewBox={`0 0 1000 ${height}`} className="w-full h-auto motion-reduce:[&_path]:[animation-duration:0s]" preserveAspectRatio="none">
      {/* 原 SVG 结构 */}
    </svg>
  );
}
```

**strokeDasharray 精度**：当前使用 `strokeDasharray="3000"` 的粗略估计。若后续发现某些曲线总长超过 3000 导致动画提前终止，可在 `useEffect` 中 `path.getTotalLength()` 动态注入；本次 S11 使用静态 `3000`（200 点 × 平均线段长 ~5 = 1000，加 drawdown ~1500，总 ~2500，<3000 安全）。

**prefers-reduced-motion**：通过 Tailwind `motion-reduce:[&_path]:[animation-duration:0s]`（arbitrary variant + selector）实现 0s 立即绘制终态（FR-NFR-003）。

### 4.5 Monthly Heatmap 色阶改造（不变）

见旧版 §4.5。关键改动：
```tsx
const cellBg = (val: number | undefined) => {
  if (val === undefined) return "transparent";
  if (val === 0) return "var(--bg-t)";
  const ratio = Math.min(Math.abs(val) / (maxAbs || 1), 1);
  const alphaPct = Math.round((0.12 + ratio * 0.45) * 100);
  return val > 0
    ? `color-mix(in srgb, var(--suc) ${alphaPct}%, transparent)`
    : `color-mix(in srgb, var(--dan) ${alphaPct}%, transparent)`;
};
```

空数据降级改 `<InlineError variant="hint">本区间无月度数据</InlineError>`。

### 4.6 Trades View（全新）

**组件接口**：
```tsx
interface BacktestTradesViewProps {
  selectedRun: BacktestRunSummary;
  tradeLog: TradeLogEntry[];
  onBack: () => void;
}
```

**结构**（8 列表格 · 删 MFE/MAE）：
```
┌─ Header ─────────────────────────────────────────────────┐
│ ← 返回详情    所有交易 · btc_multi_factor  (data-view=trades) │
│                {n} 笔交易                                 │
├─ Summary Strip (6 cols, data-summary-cell × 6) ─────────┤
│ 显示 | 总盈亏 | 胜败 | 胜率 | 平均盈利 | 平均亏损       │
├─ Filter Bar ─────────────────────────────────────────────┤
│ [方向 tabs] [结果 tabs]        [搜索 ⌘K bound]          │
├─ Table (8 cols, shadcn) ────────────────────────────────┤
│ ID | 日期 | 方向 | 入场 | 出场 | 仓位 | PnL | 持仓     │
├─ Pagination ─────────────────────────────────────────────┤
│ 1–20 / 342    ← [1][2][3][4][5]… →                       │
└───────────────────────────────────────────────────────────┘
```

**筛选 + 搜索派生**（`useMemo` 强制缓存，FR-113 NFR-1）：
```tsx
const filtered = useMemo(() => tradeLog.filter(t => {
  if (sideFilter === "long" && t.side !== "BUY") return false;
  if (sideFilter === "short" && t.side !== "SELL") return false;
  const pnl = Number(t.realized_pnl);
  if (resultFilter === "win" && pnl <= 0) return false;
  if (resultFilter === "loss" && pnl > 0) return false;
  if (search && !(
    t.instrument.toLowerCase().includes(search.toLowerCase()) ||
    makeTradeId(t).toLowerCase().includes(search.toLowerCase())
  )) return false;
  return true;
}), [tradeLog, sideFilter, resultFilter, search]);

const summary = useMemo(() => ({
  count: filtered.length,
  totalPnl: filtered.reduce((s, t) => s + Number(t.realized_pnl), 0),
  wins: filtered.filter(t => Number(t.realized_pnl) > 0).length,
  losses: filtered.filter(t => Number(t.realized_pnl) <= 0).length,
  // ...
}), [filtered]);

function makeTradeId(t: TradeLogEntry): string {
  return `${t.instrument}-${new Date(t.opened_at).getTime()}`;
}
```

**⌘K 键盘绑定**：
```tsx
useEffect(() => {
  const handler = (e: KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      searchInputRef.current?.focus();
    }
  };
  window.addEventListener("keydown", handler);
  return () => window.removeEventListener("keydown", handler);
}, []);
```

**空态**（FR-119）：
- `tradeLog.length === 0` → 渲染 `<EmptyState>`，不渲染下方所有区块。
- `filtered.length === 0 && tradeLog.length > 0` → Summary/Filter 保留，表格区替换为 `<EmptyState>`。

## 5. mock Class 映射表

(与前版相同 · 零新增 class 承诺维持不变 — 此处为节省篇幅略，详见 git 历史版 §5)

## 6. 动效清单

| 动效 | 目标元素 | 实现方式 | 是否新增 | motion-reduce 处理 |
|---|---|---|---|---|
| 页面 fade-up 分级 | List/Detail/Trades 各 section | `animate-qds-fade-up` + `[animation-delay:Xms]` | 否 | qds-fade-up 已覆盖 |
| Running 行 shimmer | 底部进度条、展开区 ShimmerBar | `animate-qds-shimmer`（内嵌 `<ShimmerBar>`） | 否 | ShimmerBar 内部覆盖 |
| Ring progress 过渡 | `strokeDashoffset` | inline `transition: stroke-dashoffset 600ms` | 否 | 默认 transition 在 motion-reduce 下浏览器降级 |
| Stepper 切步 | step 内容 block | inline `animation: slideInUp 0.35s var(--eo)` + Tailwind `motion-reduce:animate-none` | **是**（新增 `@keyframes slideInUp`） | 显式 `motion-reduce:animate-none` |
| Equity SVG 绘制 | `<path>` | inline `animation: dash 1.8s 0.1s var(--eo) forwards` + Tailwind `motion-reduce:[&_path]:[animation-duration:0s]` | **是**（新增 `@keyframes dash`） | 显式 `motion-reduce:...duration:0s` |
| Sheet 进出 | SheetContent | shadcn 自带 data-ending-style / data-starting-style | 否 | shadcn 原生覆盖 |
| Row hover | 每个 row | `transition-colors duration-150 hover:bg-secondary` | 否 | 无需 |
| Status running pulse dot | StatusBadge | `animate-qds-pulse` | 否（已有） | qds-pulse 覆盖 |
| Queued dots pulse | 3 点占位 | `animate-pulse` | 否 | Tailwind 内置 |
| Expand / collapse | Running 展开区、Advanced options | `max-height` + `transition-[max-height] duration-[400ms] ease-qds` | 否 | transition 浏览器降级 |
| WS degraded → 暂停 | RingProgress + ShimmerBar | `data-ws-stale="true"` + `data-[ws-stale=true]:[animation-play-state:paused]` | 否 | 额外语义降级 |

**新增 keyframes**（追加到 `globals.css` 末尾，@keyframes 块内）：
```css
@keyframes dash {
  to { stroke-dashoffset: 0; }
}

@keyframes slideInUp {
  from {
    opacity: 0;
    transform: translateY(12px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
```

> **命名权衡**：`slideInUp` 12px 偏移 vs 既有 `qds-fade-up` 8px 偏移；mock stepper 明确要求 12px 强调"向上滑入"。未来若发现 `fade-up` 与 `qds-fade-up` 本身冗余（globals.css:295 vs 295+），可作为独立 cleanup 任务讨论（Out-of-scope）。

## 7. 数据与 API 接线

### 7.1 现有 hooks（不变）
- `useBacktestRuns()`: 返回 `runs / runsLoading / progressMap / progressDetailMap / loadRuns`。沿用。
- `useBacktestDetail(selectedRunId, runs)`: 返回 `{ tradeLog, result, resultError, ... }`。沿用。

### 7.2 WebSocket
- `useWsEvent("backtest.progress")` 订阅进度事件，flat payload（`(msg.data ?? msg)`）。沿用。
- 降级态：`useWsConnection()`（或等价）返回的 `status !== "connected"` 且 running 行 `lastProgressAt < now - 15000` 时，RunRow 标记 `data-ws-stale="true"`（FR-013）。

### 7.3 API endpoint（全部不变）

| Endpoint | 方法 | 调用点 | 载荷 |
|---|---|---|---|
| `/api/backtest/runs?limit=100` | GET | useBacktestRuns 轮询 5s | — |
| `/api/backtest/{id}/result` | GET | Overview/Performance/Trades tab | — |
| `/api/backtest/run` | POST | Create sheet 提交 | 现有 schema |
| `/api/backtest/estimate` | POST | Step 2 K 线估算 | `{symbols, interval, start_date, end_date}` |
| `/api/backtest/{id}/cancel` | POST | running 行 Cancel 按钮 | — |
| `/api/strategies` | GET | Step 1 策略列表 | — |
| `/api/strategies/{name}/params` | GET | Step 3 参数覆盖 | — |
| `/api/strategies/{name}/defaults` | GET | 策略切换时自动填充 subscriptions（优先 `subscriptions`，回退 `symbols`） | — |
| `/api/data/symbols` | GET | Step 1 标的搜索 | — |

### 7.4 新增回调（跨视图）
- `page.tsx` 新增：
  - `handleViewAllTrades(runId: string)` → `setView("trades")`。
  - `handleRetry(run: BacktestRunSummary)` → `setRetryPrefill(run); setSheetOpen(true)`。
  - `handleCancelRun(runId: string)` → `apiPost("/api/backtest/{id}/cancel")`。
- `BacktestListView` 新增 prop：`onRetryRun(run) / onCancelRun(runId)`。
- `BacktestDetailView` 新增 prop：`onViewAllTrades: (runId: string) => void`（透传给 OverviewTab）。
- `OverviewTab` 新增 prop：`onViewAllTrades?: () => void`。
- `BacktestTradesView` 新增 prop：`onBack: () => void`（回到 `view="detail"`）。

**失败重试预填字段清单**（FR-033 再次列出）：
- 从 `BacktestRunSummary` 可预填：`strategy_name`、`symbol`（拆 `,`）、`interval`、`start_date`、`end_date`。
- 不可预填（采用默认）：`initial_capital=100000` / `maker_fee=0.02` / `taker_fee=0.05` / `fill_model_type=default` / `latency_mode=off` / `latency_ms=0` / `warmup_bars=0` / `tags=""` / `paramOverrides={}`。
- Sheet Header 显示 `InlineError` hint：`"已复制策略、标的、周期与时间区间，请确认资金与成本参数"`。

### 7.5 动画机制迁移：FORM_SECTION_STATIC_CLS

**问题**：`BacktestSubscriptionTable.tsx:57` 使用 `FORM_SECTION_CLS`，预设 `opacity-0 translate-y-4`；依赖 `BacktestCreateView.tsx:111` 的 `useEffect` 触发 `[data-form-section]` → `data-visible=true`。BacktestCreateView 删除后，该触发逻辑消失。

**解决**：新增静态版常量：
```ts
// backtestStyles.ts
export const FORM_SECTION_STATIC_CLS =
  "mb-7 transition-[opacity,transform] duration-[450ms] ease-qds";
```
并将 `BacktestSubscriptionTable.tsx:57` 的 `className={FORM_SECTION_CLS}` 改为 `className={FORM_SECTION_STATIC_CLS}`，同步删除 `data-form-section` 属性。

**影响面**：零。`data-form-section` 只在 `BacktestCreateView.tsx` 和 `BacktestSubscriptionTable.tsx` 引用，前者删除，后者改造；`FORM_SECTION_CLS` 常量本身在 `backtestStyles.ts` 保留（只是无人引用），避免破坏 export。AC-C-9 grep 断言全仓零 `data-form-section`。

## 8. 执行节点文案修正

### 8.1 当前位置
`BacktestCreateView.tsx:503-505`：
```tsx
<div className="font-mono text-[0.72rem] text-muted-foreground">
  预估运行时间 <span className="text-primary">{estimate?.estimated_label ?? "—"}</span>
  {estimate?.total_bars != null && ` · 约 ${(estimate.total_bars / 1_000_000).toFixed(1)}M bars`}
</div>
```

### 8.2 新文案（Step 3 底部）
```tsx
<div className="flex flex-col gap-1 text-[0.7rem] font-mono">
  <div className="text-muted-foreground">
    预估运行时间 <span className="text-primary">{estimate?.estimated_label ?? "—"}</span>
    {estimate?.total_bars != null && ` · 约 ${(estimate.total_bars / 1_000_000).toFixed(1)}M bars`}
  </div>
  <div className="text-qds-t3">
    任务将在 <span className="text-qds-info">API 回测 worker</span> 中执行
  </div>
</div>
```

> **为什么不是"沙盒节点"**：TinoHelm 的 `node-sandbox` 容器仅负责 live/paper trading。回测 worker 是 `api` 容器内 subprocess 池。mock 的"沙盒节点"措辞对本项目有误导。

### 8.3 提交失败处理

```tsx
const submit = useAction(async () => { await apiPost("/api/backtest/run", buildPayload(form, subscriptions, paramOverrides)); });

// SheetFooter 上方：
{submit.state === "error" && <InlineError>{submit.error?.message ?? "提交失败，请重试"}</InlineError>}
```
Sheet 保持打开，错误不触发 toast（符合 4-Layer Notification System Layer 2 约定）。

## 9. 测试策略

### 9.1 单元测试（Vitest + React Testing Library）
目标位置：`src/web/src/app/backtest/__tests__/`

- **BacktestCreateStepper.test.tsx**：验证 3 种 step 态（1/2/3）下圆点 class 切换正确；completed 圆点显示 `<Check />` icon；connector 颜色正确。
- **OverviewEquitySvg.test.tsx**：
  - 输入空数组 → 渲染 `InlineError` 且含文案 `"暂无权益曲线数据"`。
  - 输入 100 点 → `<svg>` 下 `<path>.length === 4`（2 fill + 2 stroke）。
  - stroke `<path>` inline style 包含 `animation: dash`。
  - 所有 stroke/stop `<path>/<stop>` 属性不含 `#` 开头 hex（通过 `containerEl.innerHTML.match(/#[0-9a-fA-F]{3,6}/)` 断言）。
- **BacktestTradesView.test.tsx**：
  - filter `long` + `win` 双重筛选返回期望子集。
  - 搜索按 `instrument` 或 `trade_id` 过滤。
  - `tradeLog.length === 0` → 渲染 EmptyState（文案 `此回测暂无交易记录`）。
  - `filtered.length === 0 && tradeLog.length > 0` → summary/filter 保留 + table 区 EmptyState（文案 `无匹配交易`）。
  - summary 派生使用 `useMemo`（通过 rerender + Object.is 引用相等性验证）。
- **OverviewMonthlyHeatmap.test.tsx**：
  - 输入正负 val 均返回 `color-mix(in srgb, var(--suc)...)` / `var(--dan)...`；不含 `rgba(76` / `rgba(239`。
  - `years.length === 0` → 渲染 `<InlineError>`，不返回 null。

### 9.2 集成测试（Playwright · 4 spec 文件）

| spec 文件 | 覆盖 AC | 关键断言 |
|---|---|---|
| `src/web/e2e/backtest/list-view.spec.ts` | AC-B-4, AC-B-6, AC-B-8, AC-A-1, AC-A-2 | 10 列 grid 首列 3px；running 展开 6 meta cell；Cancel → API；WS mock progress → RingProgress 更新；failed retry → sheet 打开 + 预填验证 |
| `src/web/e2e/backtest/create-sheet.spec.ts` | AC-B-1, AC-B-2, AC-B-7, AC-A-6, AC-D-4 | 端到端提交；9 fill_model 选中 + payload；Previous 保持 state；1280 视口 520 宽 / 600 视口全屏；step 切换 slideInUp |
| `src/web/e2e/backtest/detail-view.spec.ts` | AC-B-3, AC-A-3, AC-A-4, AC-D-1, AC-D-2, AC-D-3 | 7 tab 切换无 error；KPI 6 cell；Equity SVG dash 动画 finished promise resolve；fade-up delay |
| `src/web/e2e/backtest/trades-view.spec.ts` | AC-B-5, FR-115 ⌘K, FR-119 空态 | 跳转到 trades；方向/结果 filter；搜索过滤；⌘K 聚焦；`tradeLog=[]` → EmptyState；filter 0 行 → 内联 EmptyState；返回 → detail |

### 9.3 DS 合规测试（脚本调用）
```bash
bash src/web/scripts/verify-ds-compliance.sh                    # AC-C-1
bash src/web/scripts/verify-ds-compliance.sh --mode both-themes # AC-C-2
```

### 9.4 构建/类型/Lint
- `cd src/web && npm run build`
- `cd src/web && npm run lint`
- `cd src/web && npx tsc --noEmit`
