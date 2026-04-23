---
task: backtest-page-visual-refactor
created: 2026-04-20
phase: plan
---

# 任务清单 · 回测页面视觉重构

## 子任务列表

### S1 · 基础设施：@keyframes + StatusBadge 对齐
- [ ] **S1**：新增 `@keyframes dash` 和 `@keyframes slideInUp` 到 `src/web/src/app/globals.css`；确认 `StatusBadge` 的 running/queued 覆盖列表页需要的两种态（现有已含 running，无需新增 class，只需二次确认）。
  - **files**: `src/web/src/app/globals.css`（追加 ~10 行 keyframes），`src/web/src/components/qds/status-badge.tsx`（只读验证）
  - **deps**: []
  - **est**: 0.5h
  - **acceptance**:
    - `grep -n "^@keyframes dash " src/web/src/app/globals.css` 恰 1 条。
    - `grep -n "^@keyframes slideInUp " src/web/src/app/globals.css` 恰 1 条。
    - `bash src/web/scripts/verify-ds-compliance.sh --selftest` exit code 0。
    - `cd src/web && npm run build` exit code 0。

### S2 · 公共组件：backtestStyles 常量扩展 + 热力图色阶 token 化 + PerformanceHelpers 颜色清理
- [ ] **S2**：
  - (a) 重构 `OverviewMonthlyHeatmap.tsx` 的 `cellBg()` 为 `color-mix(in srgb, var(--suc/dan) ..%, transparent)`（FR-093）；`val === 0` 返回 `var(--bg-t)`（替代 `rgba(255,255,255,0.03)`）；`years.length === 0` 改为渲染 `<InlineError variant="hint">` 降级（FR-093）。
  - (b) 清理 `PerformanceHelpers.tsx:167/169/170` 三处硬编码 rgba，分别替换为 `color-mix(in srgb, var(--info) 50%, transparent)` / `var(--suc) 50%` / `var(--dan) 50%`（NFR-2）。
  - (c) 在 `backtestStyles.ts` 追加：
    - `STEPPER_DOT_CLS_MAP`: `{ active: "...", completed: "...", pending: "..." }`
    - `TIMEFRAME_CHIP_CLS`: `{ active: "border-primary bg-primary/15 text-primary", inactive: "border-border text-muted-foreground" }`
    - `TRADES_SIDE_BADGE_CLS`: `{ long: "text-qds-success", short: "text-destructive" }`
    - `FORM_SECTION_STATIC_CLS`: `"mb-7 transition-[opacity,transform] duration-[450ms] ease-qds"`
  - (d) `BacktestSubscriptionTable.tsx:57` 将 `className={FORM_SECTION_CLS}` 改为 `className={FORM_SECTION_STATIC_CLS}`，并删除 `data-form-section` 属性（FR-076 动画迁移）。
  - **files**:
    - `src/web/src/app/backtest/components/OverviewMonthlyHeatmap.tsx`（改 cellBg + 空数据）
    - `src/web/src/app/backtest/components/PerformanceHelpers.tsx`（清 3 处 rgba）
    - `src/web/src/app/backtest/components/backtestStyles.ts`（追加常量）
    - `src/web/src/app/backtest/components/BacktestSubscriptionTable.tsx`（改 className + 删属性）
  - **deps**: ["S1"]
  - **est**: 1.5h
  - **acceptance**:
    - `grep -rE "rgba\(76, 175, 80|rgba\(239, 83, 80|rgba\(76, 158, 235, 0\.5\)|rgba\(38, 217, 127, 0\.5\)" src/web/src/app/backtest/components/OverviewMonthlyHeatmap.tsx src/web/src/app/backtest/components/PerformanceHelpers.tsx` exit code = 1（零命中）。
    - `grep "color-mix" src/web/src/app/backtest/components/OverviewMonthlyHeatmap.tsx src/web/src/app/backtest/components/PerformanceHelpers.tsx` 两文件均 ≥ 1 命中。
    - `grep -E "export const (STEPPER_DOT_CLS_MAP|TIMEFRAME_CHIP_CLS|TRADES_SIDE_BADGE_CLS|FORM_SECTION_STATIC_CLS)" src/web/src/app/backtest/components/backtestStyles.ts` 命中 4 次。
    - `grep "data-form-section" src/web/src/app/backtest/components/BacktestSubscriptionTable.tsx` exit code = 1（零命中）。
    - `grep "FORM_SECTION_STATIC_CLS" src/web/src/app/backtest/components/BacktestSubscriptionTable.tsx` 命中 ≥ 1。
    - `cd src/web && npm run build` exit code 0。

### S3 · List View 重构：10 列表格骨架 + 响应式降级 + StatusBadge 替换
- [ ] **S3**：
  - 在 `BacktestListView.tsx` 中把 active / history 两组 zone 的 row 容器改为统一的 10 列 grid 风格（含响应式 `lg:` / `xl:` 三档切换 class 字符串，见 tech-design §4.1）；保留 active/history 分区逻辑和 pagination；保留现有 `<EmptyState>`（`runs.length === 0 && !runsLoading` 时）和 `<Skeleton>`（loading 时）。
  - 在 `BacktestRunRow.tsx` 和 `BacktestHistoryRow` 中把 `gridTemplateColumns: "3px 1fr auto auto auto"` 改为新的 10 列响应式 grid（`GRID_COLS_CLS` 常量；Sharpe/WinRate 列使用 `hidden xl:flex`；run_id 列使用 `hidden lg:flex`，并在 `<lg` 时于策略+标的 cell 第二行追加 run_id 短版）。
  - 替换 status pill 内联实现为 `<StatusBadge status={...} locale="en" />`（从 `components/qds` 导入）；`STATUS_PILL_MAP` 保留在 `backtestStyles.ts`（未用）或一并删除。
  - 色条 cell 使用 `ACCENT_BG_MAP`（现有常量，S2 不改）。
  - 列 7/8/9（Sharpe/WinRate/PnL）按 FR-022 规则：非 completed 统一渲染 `<span className="font-mono text-xs text-muted-foreground">—</span>`。
  - PnL 符号统一采用 tech-design §4.3 约定（`>=0` 加 `+`；`<0` 加显式 `-`，均带 `$` 和千分位）；HistoryRow 和 RunRow 两处保持一致。
  - 为 detail/running 行 meta cell 添加 `data-meta-cell` 属性（便于 AC-A-2 断言）；RingProgress 在 S4 实现时添加 `data-ring-progress`。
  - **本任务不改 `page.tsx`**（page.tsx 改动合并到 S6）。
  - **files**: `src/web/src/app/backtest/components/BacktestListView.tsx`, `src/web/src/app/backtest/components/BacktestRunRow.tsx`
  - **deps**: ["S2"]
  - **est**: 2.5h
  - **acceptance**:
    - DOM 断言：xl 视口（≥1280px）首行 `gridTemplateColumns` 计算后第 1 列宽为 `3px`；lg (1024px) 视口第 7/8 列元素有 `display: none`；< lg 时第 3 列（run_id）`display: none` 且策略+标的 cell 第二行含短版 run_id 文本。
    - `grep -n "STATUS_PILL_MAP" src/web/src/app/backtest/components/BacktestRunRow.tsx` 零命中（已替换为 StatusBadge）。
    - `grep -n "<StatusBadge" src/web/src/app/backtest/components/BacktestRunRow.tsx` ≥ 2 命中（Row + HistoryRow 各一）。
    - `cd src/web && npm run lint && npx tsc --noEmit` 两命令均 exit code 0。
    - Playwright 截图或 DOM 断言（延到 S15 回归）：running/queued/completed/failed 四种 row 均渲染不报错（`page.on("pageerror")` count = 0）。

### S4 · List View · Running 行内联展开（RingProgress + ShimmerBar + 元数据 + Cancel）
- [ ] **S4**：
  - 在 `BacktestRunRow.tsx` 内**新增 `RingProgress` 内嵌函数组件**（tech-design §4.1 伪代码实现，带 `data-ring-progress` 属性 + `transition` + `motion-reduce` 降级）。
  - running 展开区（`isRunning && isExpanded`）：
    - 左侧嵌入 44px `RingProgress`
    - 中部使用 `<ShimmerBar progress={pct} height="md" active={true} variant="accent" />`（从 `components/qds/ShimmerBar` 导入）
    - 右侧元数据 grid `grid-cols-6`（Progress / Elapsed / ETA / Speed / Processed / Trades），每 cell 加 `data-meta-cell`，删除 Memory/CPU 两项占位
    - 新增 `Cancel` 按钮 (`VIEW_BTN_CLS + !text-destructive`)，onClick 调用 props.onCancelRun(runId)（无 apiPost inline）
  - queued 展开区保留现有 Preview/Config skeleton 设计。
  - WS 降级态（FR-013）：接收 `isWsStale: boolean` prop，展开区外层 `data-ws-stale={isWsStale ? "true" : "false"}`；RingProgress 内层使用 `data-[ws-stale=true]:[animation-play-state:paused]`（此选择器通过父级属性生效）。
  - **本任务不改 `page.tsx` / `BacktestListView.tsx`**（`onCancelRun` prop 在 S6 的 page.tsx 改造中补充上游；此时 ListView 只需透传 prop 即可，若 S3 未声明 prop 则 S4 在 `BacktestRunRowProps` 中追加字段）。
  - **files**: `src/web/src/app/backtest/components/BacktestRunRow.tsx`（仅 `BacktestRunRow` 部分，不碰 `BacktestHistoryRow` 以避免与 S5 冲突）, `src/web/src/app/backtest/components/BacktestListView.tsx`（仅新增 prop 透传，不改 page.tsx）
  - **deps**: ["S3"]
  - **est**: 2h
  - **acceptance**:
    - 展开后 DOM 断言：`querySelectorAll('[data-meta-cell]').length === 6`。
    - `<ShimmerBar>` 被渲染（`querySelector('[class*="animate-qds-shimmer"]')` 非 null）。
    - `<svg data-ring-progress>` 存在；`stroke-dashoffset` attribute 随 pct 变化（断言 pct=0 时 offset = C，pct=100 时 offset = 0）。
    - Cancel 按钮 onClick 调用 `onCancelRun`（jest.fn mock）恰 1 次。
    - `grep -c "data-ring-progress" src/web/src/app/backtest/components/BacktestRunRow.tsx` ≥ 1。
    - `cd src/web && npm run build && npx tsc --noEmit` 两命令 exit code 0。

### S5 · List View · Failed 行样式 + 重试按钮（串行于 S4 后）
- [ ] **S5**：
  - `BacktestHistoryRow`（failed 分支，位于 `BacktestRunRow.tsx` 的 `export function BacktestHistoryRow` 段）：
    - 确保左 3px 色条为 `bg-destructive`（现有 `ACCENT_BG_MAP["fail"]`，确认不变）。
    - 错误摘要使用 `text-destructive text-[0.68rem]`，`run.error.slice(0, 24)`。
    - 展开态右下角新增「↻ 重试」按钮（`<RotateCcw className="w-3 h-3" /> 重试`，from `lucide-react`），onClick 调用 props.onRetryRun(run)。
    - 新增 `onRetryRun?: (run: BacktestRunSummary) => void` prop 到 `BacktestHistoryRowProps`。
  - `BacktestListView.tsx` 透传 `onRetryRun` prop 到 `BacktestHistoryRow`（ListView 本身已在 S3/S4 加 prop transformation；S5 仅在 HistoryRow prop 列表上追加 `onRetryRun`）。
  - **依赖 S4 完成**：为了避免 `BacktestRunRow.tsx` 文件在 S4（改 RunRow 部分）和 S5（改 HistoryRow 部分）之间并行写入，**S5 串行于 S4 之后**。S5 在 S4 完成后 pull 最新版再开工，只改 HistoryRow 代码段与其 props interface。
  - **本任务不改 `page.tsx`**（`onRetryRun` 从 page 透传到 ListView 的链路在 S6 中由 page.tsx 注入）。首版 S5 完成后，若 S6 尚未完成，`onRetryRun` 可先接一个 `() => {}` no-op（由 ListView 默认 prop），待 S6 接入真正 handler。
  - **files**: `src/web/src/app/backtest/components/BacktestRunRow.tsx`（**仅 HistoryRow 部分 + 其 props interface**）, `src/web/src/app/backtest/components/BacktestListView.tsx`（仅透传 prop）
  - **deps**: ["S4"]
  - **est**: 1h
  - **acceptance**:
    - failed 行展开后存在 `<button>` 文本包含「重试」。
    - 点击重试按钮触发 `onRetryRun` mock 恰 1 次，参数为当前 run object。
    - `grep -n "RotateCcw" src/web/src/app/backtest/components/BacktestRunRow.tsx` ≥ 1 命中。
    - `cd src/web && npx tsc --noEmit` exit code 0（HistoryRowProps 新字段通过）。

### S6 · Create Sheet 外壳（State owner）+ Stepper + page.tsx 整改
- [ ] **S6**：
  - 新建 `BacktestCreateSheet.tsx`（tech-design §4.2 的完整 state owner 模式）：
    - 使用 shadcn `<Sheet>`、`<SheetContent side="right" className="w-full sm:max-w-[520px] p-0 gap-0 flex flex-col">`、`<SheetHeader>`、`<SheetFooter>`。
    - Sheet 顶层持有所有跨步 state：`step / form / subscriptions / strategyParams / paramOverrides / paramsExpanded / advancedExpanded`。
    - `retryPrefill: BacktestRunSummary | null` prop 非 null 时触发 useEffect 预填（FR-033 字段清单，见 tech-design §4.2 代码）。
    - `fromRetry && <InlineError variant="hint">已复制策略、标的、周期与时间区间，请确认资金与成本参数</InlineError>` 在 SheetHeader 下方渲染。
    - Previous/Next 按钮控制 `step`；step=1 左按钮显"取消"并 `onOpenChange(false)`。
    - step 切换通过 `{step === N && <StepN key={`s${step}`} ...props />}` 条件渲染 + `key` 触发 slideInUp；但 state 不随 unmount 丢失。
  - 新建 `BacktestCreateStepper.tsx`（3 dot + label + connector，tech-design §4.2 伪代码）；使用 `STEPPER_DOT_CLS_MAP`（S2 已导出）。
  - Step1/2/3 首版各建空壳文件 (`BacktestCreateStep1/2/3.tsx`)，导出 `function BacktestCreateStepN(props) { return <div>TODO S{N}</div>; }`，便于 Sheet 编译通过。空壳的 props 类型按 tech-design §4.2 定义完整（在 S7/8/9 里填充实现）。
  - `page.tsx` 改造：
    - View 类型从 `"list"|"create"|"detail"` 改为 `"list"|"detail"|"trades"`。
    - 删除 `view === "create"` 分支。
    - 新增 `sheetOpen / setSheetOpen` state。
    - 新增 `retryPrefill / setRetryPrefill: BacktestRunSummary | null` state。
    - 新增 `handleRetry(run) = () => { setRetryPrefill(run); setSheetOpen(true); }`。
    - 新增 `handleCancelRun(runId) = () => apiPost(\`/api/backtest/${runId}/cancel\`).then(loadRuns)`。
    - 新增 `handleViewAllTrades(runId) = () => { setSelectedRunId(runId); setView("trades"); }`。
    - 新增 `view === "trades"` 分支占位（S14 完成后替换为 `<BacktestTradesView />`）。
    - 把 `loadRuns / onRetryRun=handleRetry / onCancelRun=handleCancelRun` 作为 props 透传给 `<BacktestListView>`。
    - `<BacktestCreateSheet open={sheetOpen} onOpenChange={setSheetOpen} retryPrefill={retryPrefill} onSubmit={...} />` 渲染在顶层。
  - **files**:
    - 新建：`src/web/src/app/backtest/components/BacktestCreateSheet.tsx`, `src/web/src/app/backtest/components/BacktestCreateStepper.tsx`, `src/web/src/app/backtest/components/BacktestCreateStep1.tsx`（空壳）, `src/web/src/app/backtest/components/BacktestCreateStep2.tsx`（空壳）, `src/web/src/app/backtest/components/BacktestCreateStep3.tsx`（空壳）
    - 修改：`src/web/src/app/backtest/page.tsx`（View 枚举、Sheet open state、retry/cancel/viewAllTrades handlers、删除 create 分支、新增 trades 占位）
  - **deps**: ["S2"]
  - **est**: 2h
  - **acceptance**:
    - 点击列表「+ 创建回测」按钮 → Sheet 从右侧滑出。
    - Stepper 3 圆点渲染正确：step=1/2/3 三态按 `STEPPER_DOT_CLS_MAP` 切换。
    - 切 step 时 body 出现 `animation-name: slideInUp`（`Element.getAnimations()[0].animationName === "slideInUp"`）。
    - Previous 按钮在 step>1 显示，点击 step 减 1；step=1 左按钮显示"取消"，点击后 Sheet 关闭。
    - `grep -n '"list" | "detail" | "trades"' src/web/src/app/backtest/page.tsx` ≥ 1 命中（新 View 枚举）。
    - `grep -n 'view === "create"' src/web/src/app/backtest/page.tsx` 零命中（旧分支已删）。
    - `grep -nE "handle(Retry|CancelRun|ViewAllTrades)" src/web/src/app/backtest/page.tsx` 命中 3 次。
    - `cd src/web && npx tsc --noEmit && npm run build` 两命令 exit code 0。

### S7 · Create Step 1：策略下拉 + 标的搜索 + chip 列表
- [ ] **S7**：
  - 改写 `BacktestCreateStep1.tsx`（S6 空壳）：
    - 从旧 `BacktestCreateView.tsx:285-332` 搬运策略下拉逻辑（包含 `strategyDropdownOpen` state 和过滤、策略切换时的 defaults 拉取）。
    - 新增 `SymbolPickerWithChips` 内联组件：输入框 + 候选 dropdown（来自 `/api/data/symbols`）+ 已选 chip 列表（基于 `subscriptions` 派生）。
    - 已选 chip 样式：`inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-primary/15 text-primary text-xs font-mono` + × 移除按钮。
    - Props 受控（FR-045）：`form` / `subscriptions` / `onFormChange` / `onSubscriptionsChange`，不持有跨步 state。
    - 策略 defaults fallback：优先 `d.subscriptions`；若空则 `d.symbols?.map(sym => ({ symbol: sym, timeframe: DEFAULT_TIMEFRAME, data_type: "bar" }))`。
    - 校验：`strategy_name` 空或 `subscriptions.length === 0` 时，向上传达 `isValid=false`（可通过 callback prop 或 Sheet 自行 compute）。
  - **files**: `src/web/src/app/backtest/components/BacktestCreateStep1.tsx`（替换空壳）
  - **deps**: ["S6"]
  - **est**: 2h
  - **acceptance**:
    - 选策略后 `onSubscriptionsChange` 被调用且 payload 含至少 1 个 subscription（fetch mock `/api/strategies/{name}/defaults`）。
    - 搜索输入框输入 `BTC` → 候选 dropdown `<li>` 或 `<button>` 至少 1 个文本包含 `BTC`。
    - 点击候选添加 chip；点击 chip × 移除；chip 数量与 `subscriptions.length` 一致（vitest 验证）。
    - strategy_name 空时 Sheet 的 Next 按钮 disabled（通过 e2e 或 Sheet 级断言）。
    - `cd src/web && npx tsc --noEmit` exit code 0。

### S8 · Create Step 2：周期 chip（6+1 自定义 · 白名单校验） + 日期 + estimate
- [ ] **S8**：
  - 改写 `BacktestCreateStep2.tsx`（S6 空壳）：
    - **从 `BacktestCreateView.tsx:61-69` 搬运 `parseTimeframe` 工具函数**到本文件模块顶部。
    - 周期 chip 行：6 个快捷（`1m/5m/15m/1h/4h/1d`）+ 1 个「自定义」。选中状态使用 `TIMEFRAME_CHIP_CLS`（S2 已导出）。
    - 「自定义」chip 点击后展开下方 `<input>`，onBlur 校验**白名单正则** `/^(1m|3m|5m|15m|30m|1h|2h|4h|6h|8h|12h|1d)$/`（FR-061）；不通过显示 `<InlineError>` 文案 `仅支持 1m/3m/5m/15m/30m/1h/2h/4h/6h/8h/12h/1d`，并回滚到上次选中值。
    - 通过校验后同步写入所有 `subscriptions[i].timeframe`（via `onSubscriptionsChange`），关闭自定义输入框，若输入值不在 6 快捷列表内则更新"自定义"chip 标签为 `自定义 · {value}`。
    - 日期双字段（`form.start_date` / `form.end_date`）搬运现有逻辑；校验 `start < end`。
    - 底部 K 线估算（保留 300ms debounce POST `/api/backtest/estimate`）。使用 `useAction` 或同等 hook 管理加载态；仅展示，不阻塞 Next。
    - Props 受控（FR-045）：`form` / `subscriptions` / `onFormChange` / `onSubscriptionsChange`。
  - **files**: `src/web/src/app/backtest/components/BacktestCreateStep2.tsx`（替换空壳）
  - **deps**: ["S6"]
  - **est**: 2h
  - **acceptance**:
    - 点击任一快捷 chip → `onSubscriptionsChange` 被调用且所有 subscriptions[i].timeframe 被更新为 chip 值（vitest 断言）。
    - 自定义输入框输入 `30m` → 校验通过，`onSubscriptionsChange` 被调用写入 `30m`；自定义 chip 标签变为 `自定义 · 30m`。
    - 自定义输入框输入 `7m`（白名单外）→ `<InlineError>` 渲染，`onSubscriptionsChange` 未被调用，chip 选中回滚到之前值（vitest + RTL）。
    - 自定义输入框输入 `abc`（非法格式）→ 同上 InlineError 渲染。
    - 开始 > 结束日期时 Sheet 级 Next 按钮 disabled。
    - estimate API 使用 fake timer 断言：subscriptions 或 date 变动后，300ms 内**无**调用；恰 300ms 后仅 1 次调用。
    - `grep -n "parseTimeframe" src/web/src/app/backtest/components/BacktestCreateStep2.tsx` ≥ 1 命中。
    - `cd src/web && npx tsc --noEmit` exit code 0。

### S9 · Create Step 3：资金/费率/延迟 + 折叠高级选项 + API worker 文案 + 提交
- [ ] **S9**：
  - 改写 `BacktestCreateStep3.tsx`（S6 空壳）：
    - **从 `BacktestCreateView.tsx:49-59` 搬运 `FILL_MODEL_OPTIONS`** 到本文件模块顶部。
    - 顶部基础区：`initial_capital` / `maker_fee` / `taker_fee` / `latency_mode` / `latency_ms`。
    - **高级选项折叠区**（默认 `advancedExpanded=false`，通过 props 受控）：
      - 折叠开关按钮：「▾ 高级选项」 / 展开时「▴ 高级选项」，`ChevronDown` 随 state 旋转 180°。
      - 展开内容：
        - `<select>` `FILL_MODEL_OPTIONS` 9 选项下拉。
        - `prob_fill_on_limit` / `prob_slippage`（type=default 时显）。
        - `<BacktestSubscriptionTable>` 整合（已由 S2 改为 `FORM_SECTION_STATIC_CLS`，无需二次触发）。
        - `paramOverrides` 列表（保留现 `strategyParams + paramsExpanded` 二级折叠）。
        - `warmup_bars` / `tags`。
    - 底部：预估 hint + "任务将在 **API 回测 worker** 中执行" 文案（tech-design §8.2）。
    - 提交按钮：`useAction` + `apiPost("/api/backtest/run", payload)`，payload 结构严格保持旧版 schema。
    - 提交失败（`action.state === "error"`）在 SheetFooter 上方渲染 `<InlineError>`（FR-075）；不触发 toast、不关闭 Sheet。
    - 成功后调用 `onSubmit()` + `onOpenChange(false)` + 调用 `loadRuns()`。
  - **files**: `src/web/src/app/backtest/components/BacktestCreateStep3.tsx`（替换空壳）
  - **deps**: ["S7", "S8"]
  - **est**: 3h
  - **acceptance**:
    - `grep -n "FILL_MODEL_OPTIONS" src/web/src/app/backtest/components/BacktestCreateStep3.tsx` ≥ 1 命中，且数组包含 9 个 element（vitest 断言 `FILL_MODEL_OPTIONS.length === 9`）。
    - 9 种 fill model 均可下拉选中；mock fetch 断言 `POST /api/backtest/run` payload 包含 `fill_model.fill_model_type` 字段（E2E via MSW 或 Playwright route intercept）。
    - 默认折叠：渲染后 `advancedExpanded=false`，折叠内容 `max-height=0`；点击「▾ 高级选项」展开；再次点击收起，icon `transform: rotate(180deg)`（computed style 断言）。
    - `useAction` 模拟错误态时 `<InlineError>` 出现于 SheetFooter 上方（vitest 断言文案存在）；Sheet 不关闭。
    - `grep -n "API 回测 worker" src/web/src/app/backtest/components/BacktestCreateStep3.tsx` ≥ 1 命中。
    - 提交后 Sheet 关闭、列表刷新出现新 run_id（Playwright E2E）。
    - `cd src/web && npx tsc --noEmit && npm run build` exit code 0。

### S10 · Detail View：6 列 KPI 网格（状态降级 + data-kpi-cell）
- [ ] **S10**：
  - `BacktestDetailView.tsx` 在 Detail top bar 与 Pill tab bar 之间插入 6 列 KPI 网格（tech-design §4.3 完整实现，inline）。
  - KPI 数据来源 `selectedRun.result_summary`；`selectedRun.status === "completed" && result_summary` 时显示真实值；其它状态统一 `—`（FR-081）。
  - fade-up 分级 delay 80ms（AC-D-2）。
  - 新增 `onViewAllTrades?: (runId: string) => void` prop；透传给 `<OverviewTab onViewAllTrades={() => props.onViewAllTrades?.(selectedRun.run_id)}>`（供 S12 使用）。
  - PnL 符号采用 tech-design §4.3 约定（`>=0` `+$`；`<0` `-$`）。
  - **files**: `src/web/src/app/backtest/components/BacktestDetailView.tsx`
  - **deps**: ["S2"]
  - **est**: 1h
  - **acceptance**:
    - DOM 断言 `querySelectorAll('[data-kpi-cell]').length === 6`。
    - 第一列 inline className 不含 `border-l`，其余 5 列含 `border-l border-border`（grep inline 或 computed style 断言）。
    - 总盈亏/总收益率列 running/queued/failed 状态下文本 = `—`，className 含 `text-muted-foreground`；completed 状态下根据符号显示 `text-qds-success` 或 `text-destructive`。
    - `grep -n "onViewAllTrades" src/web/src/app/backtest/components/BacktestDetailView.tsx` ≥ 1 命中。
    - `cd src/web && npx tsc --noEmit` exit code 0。

### S11 · Overview Tab：自绘 SVG equity+drawdown + 清理 #E5534B
- [ ] **S11**：
  - 新建 `OverviewEquitySvg.tsx`（tech-design §4.4 完整实现，含空数据降级 + motion-reduce）。
  - 在 `OverviewTab.tsx` 中**删除现有 equity + drawdown 的 Recharts 双列 JSX（行号 `151-226`，共约 76 行，包括 2 个 `<ResponsiveContainer>` + 2 个 `<AreaChart>` + defs/Area/XAxis/YAxis/Tooltip/CartesianGrid）**，替换为：
    ```tsx
    <div className="mt-5">
      <SectionLabel>Equity &amp; Drawdown</SectionLabel>
      <div className={CARD_CLS}>
        <div className={CARD_BODY_CLS}>
          <OverviewEquitySvg data={equity_curve} />
        </div>
      </div>
    </div>
    ```
  - 清理 `OverviewTab.tsx` 中所有 `#E5534B` 引用（原 :207 `<stop stopColor="#E5534B">` 与 :219 `stroke="#E5534B"` 随 151-226 行删除而消失；但需要**再 grep 全文件**确认其他位置没有残留）。
  - **files**: 新建 `src/web/src/app/backtest/components/OverviewEquitySvg.tsx`；改 `src/web/src/app/backtest/components/OverviewTab.tsx`。
  - **deps**: ["S2"]
  - **est**: 2.5h
  - **acceptance**:
    - `grep "#E5534B" src/web/src/app/backtest/components/OverviewTab.tsx` exit code = 1（零命中）。
    - `grep "#[0-9a-fA-F]\{3,6\}" src/web/src/app/backtest/components/OverviewEquitySvg.tsx` exit code = 1（零命中）。
    - DOM 断言：`<svg>` 下 `<path>.length === 4`（2 fill + 2 stroke）或空数据时渲染 `<InlineError>`（二选一）。
    - 2 个 stroke `<path>` 的 inline `style` 属性字符串包含 `"animation: dash"`（grep innerHTML 或通过 React testing library）。
    - 空数据（传入 `data=[]`）时组件渲染 `<InlineError>` 含文案 `暂无权益曲线数据`，不渲染 `<svg>`（vitest）。
    - Playwright：`Element.getAnimations()` 在 path mount 后返回至少 1 个 `CSSAnimation` with `animationName === "dash"`；`.finished` promise 2.5s 内 resolve。
    - `cd src/web && npm run build` exit code 0。

### S12 · Overview Tab：Monthly + Drawdown 1.4fr/1fr 布局 + 「查看所有交易」按钮
- [ ] **S12**：
  - `OverviewTab.tsx` 把 Monthly Returns 区块和 DrawdownTable 区块合并到 `grid-cols-[1.4fr_1fr] gap-5` 一行。
  - DrawdownTable 截取 top 4 行（传 `topN=4` prop 或在组件内控制）。
  - 在 Overview 底部或 TopTrades 上方新增「查看所有交易 →」按钮（`VIEW_BTN_CLS`），onClick 调用 `props.onViewAllTrades?.()`。
  - **卡片 header 归一化**：本任务顺便把 Overview tab 内所有 `<div className="qds-section-label">` 或等价手写 header 改为 `<SectionLabel>` 组件（FR-NFR-004）。
  - **files**: `src/web/src/app/backtest/components/OverviewTab.tsx`, `src/web/src/app/backtest/components/OverviewTradeTables.tsx`（若需改 DrawdownTable 支持 topN prop）
  - **deps**: ["S2", "S11"]
  - **est**: 1.5h
  - **acceptance**:
    - Monthly + Drawdown 在同一 `grid-cols-[1.4fr_1fr]` 容器中并排（grep className）。
    - DrawdownTable 最多显示 4 行（vitest 断言 `<tbody> <tr>` 数量 ≤ 4）。
    - Overview 存在文本「查看所有交易」的 `<button>`，点击触发 `onViewAllTrades` mock 恰 1 次。
    - `grep -n "qds-section-label" src/web/src/app/backtest/components/OverviewTab.tsx` 零命中（全部改为 `<SectionLabel>`）。
    - `cd src/web && npx tsc --noEmit` exit code 0。

### S13 · 其他 tab 微调（SectionLabel 归一化 · 不重绘图表）
- [ ] **S13**：
  - 扫描 6 个 tab 的卡片 header + section label 使用情况（FR-NFR-004）：
    - 所有 `qds-section-label` class 使用点改为 `<SectionLabel>` 组件。
    - 所有 shadcn `<CardHeader>` 内手写区块标签（非组件）改为 `<CardHeader><SectionLabel>...</SectionLabel></CardHeader>`。
  - **不重绘图表、不改数据流、不动 recharts 配置**。
  - 重点检查：是否残留 mock 相关的 class（通过 AC-C-3 正则 grep）、是否有新增硬编码颜色（PerformanceHelpers 已在 S2 清理，其它不在本次范围）。
  - **files**: 根据扫描结果可能涉及：`PerformanceTab.tsx`, `TradesTab.tsx`, `RobustnessTab.tsx`, `TearsheetTab.tsx`, `TradeLogTab.tsx`, `ReportsTab.tsx` 及其 helper 文件。
  - **deps**: ["S10", "S11", "S12"]（`depends_on` 含 S11/S12 是因 SectionLabel 归一化需以 Overview 为风格基准；S10 提供 KPI grid 样式）
  - **est**: 2h
  - **acceptance**:
    - 6 个 tab 均能切换且无 console error（Playwright）。
    - `grep -rn "qds-section-label" src/web/src/app/backtest/components/ | grep -v SectionLabel` 零命中（全改为组件）。
    - `bash src/web/scripts/verify-ds-compliance.sh` exit code 0（局部扫描 backtest/ 目录）。
    - `cd src/web && npx tsc --noEmit` exit code 0。

### S14 · Trades View（全新视图 · 8 列表格 + ⌘K + 空态）
- [ ] **S14**：
  - 新建 `BacktestTradesView.tsx`（tech-design §4.6 结构完整实现，**8 列**表格无 MFE/MAE）：
    - Header（返回按钮 + 标题 + `data-view="trades"` 根属性）
    - 6 列 summary strip（每 cell 加 `data-summary-cell`）
    - 双 pill tab bar 筛选（方向 + 结果）+ 搜索框（`qds-input` class + `<Search />` icon + `<kbd>⌘K</kbd>` hint）
    - ⌘K / Ctrl+K keyboard binding via `useEffect + window.addEventListener("keydown", ...)`，按下时聚焦 searchInputRef
    - shadcn `<Table>` 渲染 **8 列**（FR-116：ID / 日期 / 方向 / 入场 / 出场 / 仓位 / PnL / 持仓；无 MFE/MAE）
    - 表格 `<tr>` 加 `data-side={long|short}` + `data-pnl-sign={positive|negative|zero}` 属性（便于 AC-B-5）
    - `BacktestPagination` 组件（每页 20）
    - FR-119 空态处理：`tradeLog.length === 0` → 渲染 `<EmptyState>`；`filtered.length === 0 && tradeLog.length > 0` → table 区替换为内联 `<EmptyState>` 文案「无匹配交易」。
    - `filtered` / `summary` 使用 `useMemo`（FR-113）。
  - `page.tsx`：替换 S6 的 `view === "trades"` 占位为 `<BacktestTradesView selectedRun={...} tradeLog={...} onBack={() => setView("detail")} />`（`page.tsx` 已在 S6 添加 View 枚举）。
  - `BacktestDetailView.tsx` 的 `onViewAllTrades`（由 S10 添加）与此对接：page.tsx 通过 `handleViewAllTrades` 回调注入。
  - **files**: 新建 `src/web/src/app/backtest/components/BacktestTradesView.tsx`；修改 `src/web/src/app/backtest/page.tsx`（替换占位）。
  - **deps**: ["S2", "S6", "S10"]
  - **est**: 3h
  - **acceptance**:
    - 表格渲染 `<th>.length === 8`（不是 10），断言列头文本集合为 `{ID, 日期, 方向, 入场, 出场, 仓位, 盈亏, 持仓}`。
    - 从 detail Overview 点击「查看所有交易」→ 页面切到 trades view，`[data-view="trades"]` 存在。
    - summary 6 cell：`querySelectorAll('[data-summary-cell]').length === 6`。
    - 方向 tab Long / 结果 tab Win 联合过滤正确（每行 `data-side="long"` 且 `data-pnl-sign="positive"`）。
    - 搜索输入 `BTC` → 剩余行 textContent 均含 `BTC`；搜索 trade_id 前 8 位 → 过滤生效。
    - 按 ⌘K（Mac）或 Ctrl+K（Linux/Windows）→ `document.activeElement` 为搜索输入框。
    - `tradeLog=[]` 时 `<EmptyState>` 渲染（文案 `此回测暂无交易记录`），表格区不渲染。
    - filter 0 行匹配时 summary 仍渲染，表格区替换为内联 `<EmptyState>` 文案 `无匹配交易`。
    - 分页切到第 2 页显示第 21-40 条。
    - 返回按钮 → 页面回到 `[data-view="detail"]`。
    - `cd src/web && npm run build && npx tsc --noEmit` exit code 0。

### S15 · 全链路 fade-up 分级 + 清理 + 最终 DS 合规扫描 + 构建 + E2E
- [ ] **S15**：
  - 遍历 list / create sheet / detail / trades 4 视图，给 top-level section 添加 `animate-qds-fade-up` + `[animation-delay:XXXms]` 分级（0 / 80 / 160 / 240ms）。
  - **删除不再使用的文件**：
    - `src/web/src/app/backtest/components/BacktestCreateView.tsx`（被 Sheet 替代）
    - `src/web/src/app/backtest/components/OverviewGreyTab.tsx`（零外部引用，37 处硬编码颜色）
  - 前置检查：`grep -rn "OverviewGreyTab" src/web/ | grep -v OverviewGreyTab.tsx` 零命中；`grep -rn "BacktestCreateView" src/web/ | grep -v BacktestCreateView.tsx` 零命中（确认安全删除）。
  - 运行 `bash src/web/scripts/verify-ds-compliance.sh` 修复所有 R1-R14 违规。
  - 运行 `bash src/web/scripts/verify-ds-compliance.sh --mode both-themes` 修复双主题违规。
  - 运行 `npm run build && npm run lint && npx tsc --noEmit` 确保 0 error。
  - 运行 `npx vitest run src/app/backtest/__tests__` 确保 0 fail。
  - 运行 Playwright E2E 4 个 spec 文件（`list-view.spec.ts / create-sheet.spec.ts / detail-view.spec.ts / trades-view.spec.ts`）：`npx playwright test src/web/e2e/backtest/` 全绿。
  - **files**: 前述所有视图文件（仅 animate-qds-fade-up 追加） + 删除 2 个文件 + 新建/修正 E2E spec 文件（若不存在由本任务创建）
  - **deps**: ["S5", "S9", "S11", "S12", "S13", "S14"]
  - **est**: 2.5h
  - **acceptance**:
    - `bash src/web/scripts/verify-ds-compliance.sh` exit code 0。
    - `bash src/web/scripts/verify-ds-compliance.sh --mode both-themes` exit code 0。
    - `cd src/web && npm run build` 0 error。
    - `cd src/web && npm run lint` 0 error。
    - `cd src/web && npx tsc --noEmit` 0 error。
    - `cd src/web && npx vitest run src/app/backtest/__tests__` 0 fail。
    - `cd src/web && npx playwright test e2e/backtest/` 全绿（4 spec）。
    - `ls src/web/src/app/backtest/components/BacktestCreateView.tsx` exit code ≠ 0（AC-C-8）。
    - `ls src/web/src/app/backtest/components/OverviewGreyTab.tsx` exit code ≠ 0（AC-C-7）。
    - `grep -rn "data-form-section" src/web/src/app/backtest/` 零命中（AC-C-9）。
    - 全部 AC-A ~ AC-E 验收标准通过（requirements §5）。

## 依赖图（DAG）

```
S1 (keyframes)
 └─▶ S2 (heatmap + PerformanceHelpers 色阶 + backtestStyles 常量 + SubscriptionTable 动画迁移)
      ├─▶ S3 (List 10 列响应式 grid + StatusBadge)
      │    └─▶ S4 (Running 展开 RingProgress + ShimmerBar)
      │         └─▶ S5 (Failed 重试 · 串行于 S4 避免 RunRow 文件并发冲突)
      ├─▶ S6 (Create Sheet 外壳 · state owner + page.tsx 整改)
      │    ├─▶ S7 (Step 1)
      │    ├─▶ S8 (Step 2 · parseTimeframe 搬迁)
      │    └─▶ S9 (Step 3 · FILL_MODEL_OPTIONS 搬迁)   ← deps [S7, S8]
      ├─▶ S10 (Detail 6 列 KPI)
      ├─▶ S11 (Overview SVG · 清理 #E5534B)
      │    └─▶ S12 (Monthly + Drawdown 布局 + 查看所有交易按钮 + SectionLabel 归一化)
      │         └─▶ S13 (其他 tab SectionLabel 归一化)   ← deps [S10, S11, S12]
      └─▶ S14 (Trades View · 8 列)   ← deps [S2, S6, S10]
                   
                   └──▶ S15 (fade-up 分级 + 删废弃文件 + 全局合规扫描 + E2E)   ← deps [S5, S9, S11, S12, S13, S14]
```

## 并行波次（parallel_groups）

- **波 1**：`["S1"]`
- **波 2**：`["S2"]`
- **波 3**：`["S3", "S6", "S10", "S11"]`（4 个独立主干：List 表格 / Sheet 外壳 + page.tsx / Detail KPI / Overview SVG）
- **波 4**：`["S4", "S7", "S8", "S12"]`（4 个可并行：Running 展开 / Step1 / Step2 / Overview 布局；**S5 从波 4 移除**，以规避与 S4 的 BacktestRunRow.tsx 合并冲突）
- **波 5**：`["S5", "S9", "S14"]`（S5 deps S4；S9 deps S7+S8；S14 deps S2+S6+S10；三者文件不重叠可并行）
- **波 6**：`["S13"]`（deps S10/S11/S12）
- **波 7**：`["S15"]`（deps S5/S9/S11/S12/S13/S14）

> **文件编辑边界约定**（消除审查提出的冲突风险）：
> - `page.tsx`：仅 **S6** 与 **S14** 修改。S6 建立 View 枚举 + handlers + trades 占位；S14 替换 trades 占位为真实组件。S4/S5 不再改 page.tsx。
> - `BacktestListView.tsx`：S3 主改（grid 骨架）；S4/S5 仅透传 props（不改 grid 结构）。
> - `BacktestRunRow.tsx`：S3 主改（grid 骨架含 RunRow + HistoryRow 两段）；S4 **仅改 RunRow 段** + 新增 RingProgress 内嵌组件；S5 串行于 S4 后 **仅改 HistoryRow 段**。两段函数在文件内边界清晰（tech-design §3.2 预估 ~120 LOC 变更已含三次编辑）。
> - `OverviewTab.tsx`：S11（替换 equity/drawdown Recharts）→ S12（Monthly+Drawdown 布局 + SectionLabel 归一化）。两任务串行（S12 deps S11），不并行。

## 总预估工时

| 波 | 子任务 | 预估 | 并行后实际 |
|---|---|---|---|
| 1 | S1 | 0.5h | 0.5h |
| 2 | S2 | 1.5h | 1.5h |
| 3 | S3, S6, S10, S11 | 2.5+2+1+2.5 = 8h | 2.5h (max) |
| 4 | S4, S7, S8, S12 | 2+2+2+1.5 = 7.5h | 2h (max) |
| 5 | S5, S9, S14 | 1+3+3 = 7h | 3h (max) |
| 6 | S13 | 2h | 2h |
| 7 | S15 | 2.5h | 2.5h |
| **合计** | 15 子任务 | **29h 串行** | **~14h 并行** |
