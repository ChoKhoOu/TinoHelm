---
task: backtest-page-visual-refactor
created: 2026-04-20
phase: plan
---

# 需求文档 · 回测页面视觉重构

## 1. 背景与目标

量化研究员在 `/backtest` 单页完成回测全流程（列表浏览、任务创建、结果分析、交易明细查询）。
当前实现在 **DS 标准化（2026-04-19）** 后保留了功能完整性，但视觉层仍停留在旧的「卡片式列表 + 5 分节内联创建表单 + 7 tab 详情」版式。
用户基于最新设计预期提供了 4 屏 React mock（列表 / Create sheet / 详情 / 所有交易），要求对 4 个视图做**视觉重构 · 功能保留**。

**一句话目标**：基于 mock 重构 `/backtest` 的 4 个视图（list / create sheet / detail / trades），保留全部现有功能、API 契约、数据流，同时升级布局、组件结构与动效，零新增 CSS class（仅补 2 个 @keyframes），严格遵守 DS 标准化禁区。

## 2. 用户故事

### US-1 列表浏览（运行态可视化）
> 作为量化研究员，我希望在回测列表页面能一眼识别当前运行中的任务进度与历史任务盈亏分布，从密集表格快速扫到感兴趣的记录，点击即进入详情。

### US-2 运行态内联检视
> 作为量化研究员，对于正在跑的回测，我希望不离开列表就能看到 Ring Progress、速度、ETA、已处理 bar 数、产生交易数，并能随时取消。

### US-3 Create Sheet 三步提交
> 作为量化研究员，我希望在右侧抽屉里按 3 步提交新回测：先选策略+标的、再定时间周期、最后配资金与执行模型；所有现有参数（9 种 fill model、warmup、tags、数据订阅）一个不少，但视觉上比现在的 5 分节表单更紧凑，有 stepper 指引。

### US-4 详情总览像素级阅读
> 作为量化研究员，我希望详情页顶部直接是 6 指标 KPI 网格（不是多行散落卡片），Overview tab 主视觉是一张 equity+drawdown 叠加自绘曲线（带 1.8s 绘制动画），旁边是月度热力图与回撤周期 top 列表。

### US-5 所有交易独立视图
> 作为量化研究员，从 Overview/Trades tab 点击「查看所有 N 笔 →」，应进入独立的交易分页视图，能按方向/结果筛选、按 ID 搜索、20 条分页浏览，并可返回详情。

## 3. 功能需求

### FR 列表视图（BacktestListView）
- **FR-001**：列表渲染为**单个表格容器**（bg-card 边框圆角），每行左侧 **3px 色条**（running=info / done=success / failed=destructive / queued=t3），替换现有 `grid 3px 1fr auto auto auto` 版式中的单一 cell，但整体视觉强化到 mock 的 row-stripe 样式。
- **FR-002**：列保留当前可读信息（策略名 / 标的 / run_id / interval / 日期范围 / status pill / 关键指标 / action），整合为 **10 列视觉密度**：色条 · 策略+标的 · run_id · 周期+日期 · 状态徽章 · 进度/结果 · Sharpe · Win Rate · PnL · Actions。响应式降级规则见 **FR-NFR-002**。
- **FR-003**：列表顶部保留 `PageHeader` 等价结构（title `回测管理` + `{n} 个回测任务` 副标题 + 右侧 `Refresh`/`+ 创建回测` 按钮）。
- **FR-004**：列表顶部保留**状态统计条**（Running/Done/Failed/Queued/Cancelled 彩色 dot + 数量），dot 颜色使用 tokens（`--info/--suc/--dan/--t3`）。
- **FR-005**：列表分 **Active 区**（queued+running）和 **历史区**（其他状态），二者之间用 `qds-section-label` 「历史记录」标签分隔；历史区右侧保留 pageSize 下拉。
- **FR-006**：历史区使用现有 `BacktestPagination` 组件（不变）。
- **FR-007（空态/加载态）**：列表 `runs.length === 0 && !runsLoading` 时渲染现有 `<EmptyState>` 组件（文案：`尚无回测记录`），不出现空表头或空白；`runsLoading === true && runs.length === 0` 时渲染 `<Skeleton>` 3 行占位。

### FR 列表 · Running 行内联展开
- **FR-010**：running 行点击切换展开；展开态渲染 `colSpan=全宽` 的内联 block，包含：
  - 左侧 **RingProgress**（44px 直径，进度圆弧 + 百分比数字）
  - 中间 **ShimmerBar**（使用 `components/qds/ShimmerBar`，`variant="accent"`, `active={true}`）
  - 右侧 **元数据网格**：Progress / Elapsed / ETA / Speed / Processed / Trades 六项（mock 是 5 项元数据，保留现有 8 项 minus Memory/CPU 占位）
  - 内联 `Cancel` 按钮（POST `/api/backtest/{id}/cancel`）
- **FR-011**：展开收缩动效使用现有 `max-height + duration-[400ms] + ease-qds`（不新增 keyframe）。
- **FR-012**：未展开的 running 行保留底部 **3px shimmer 条**（`absolute h-[3px] + animate-qds-shimmer`），显示当前进度 bar。
- **FR-013（WS 降级态）**：当 `useWsEvent` 连接状态从 connected → degraded/disconnected，并且当前行 running 超过 15 秒无 progress 更新时，shimmer 条与 RingProgress 暂停其内部 CSS 动画（通过添加 `data-ws-stale="true"` 属性并在 className 中使用 `data-[ws-stale=true]:[animation-play-state:paused]`），并在 Progress meta cell 追加 `"· 连接待恢复"` hint（`text-qds-warning text-[0.6rem]`）。

### FR 列表 · Queued 行
- **FR-020**：queued 行展开态保持现有 Preview+Config 占位设计（无真实预览时以 `bg-secondary` skeleton 块模拟）。
- **FR-021**：queued 行在 status pill 内使用 3 点 pulse dots 占位（保留现有实现）。
- **FR-022（done/running/queued/failed 下 Sharpe/WinRate/PnL 3 列统一显示）**：列 7/8/9（Sharpe / WinRate / PnL）仅当 `run.status === "completed"` 且 `run.result_summary` 存在时显示数值；其它状态一律渲染 `—`（使用 `<span className="font-mono text-xs text-muted-foreground">—</span>`），保持 10 列 grid 结构统一不断裂。

### FR 列表 · Failed 行
- **FR-030**：failed 行左 3px 红条（`bg-destructive`）。
- **FR-031**：failed 行右侧显示错误摘要（`run.error.slice(0, 24)`, `text-destructive text-[0.68rem]`）。
- **FR-032**：failed 行展开态（点击整行）显示错误详情三列（错误类型/品种/策略）+ 「查看日志 →」 + 「重试 ↻」按钮（新增）。重试交互见 **FR-033**。
- **FR-033（重试预填字段清单）**：点击 `↻ 重试` 按钮触发 `onRetryRun(run)` 回调 → 打开 Create Sheet 且严格按下表预填：

  | Create Sheet 字段 | 来源（`BacktestRunSummary`） | 预填值 |
  |---|---|---|
  | `strategy_name` | `run.strategy_name` | 直接复制 |
  | `subscriptions[]` | `run.symbol`（拆分 `","`）+ `run.interval` | 每个 symbol 构造 `{ symbol, timeframe: run.interval, data_type: "bar" }` |
  | `form.start_date` | `run.start_date` | 直接复制 |
  | `form.end_date` | `run.end_date` | 直接复制 |
  | `form.initial_capital` | — | 默认值 `100000` |
  | `form.maker_fee` / `form.taker_fee` | — | 默认 `0.02` / `0.05` |
  | `form.fill_model_type` | — | `default` |
  | `form.latency_mode` / `form.latency_ms` | — | `off` / `0` |
  | `form.warmup_bars` / `form.tags` | — | `0` / `""` |
  | `paramOverrides` | — | `{}`（清空） |

  Sheet Header 在 stepper 下方追加 `<InlineError variant="hint">已复制策略、标的、周期与时间区间，请确认资金与成本参数</InlineError>`（仅重试模式显示；`form.from_retry === true` 时挂载）。

### FR 创建视图（Create Sheet）
- **FR-040**：创建入口由独立 `view='create'` 改为 **shadcn Sheet**（side=right）。宽度实现必须为 `w-full sm:max-w-[520px]` — `<sm`(640px) 视口下 Sheet 全屏展开，`≥sm` 时固定 520px 上限。AC-A-6 据此断言。
- **FR-041**：Sheet Header 渲染 **stepper**：3 个圆点 + 标签（1 策略&标的 / 2 时间&周期 / 3 资金&成本），圆点连线，当前 step 圆点填 `--acc`，完成 step 圆点填 `--suc` 并显示 `Check` icon，未到 step 为空心 `--bd`。
- **FR-042**：Sheet Body 中 3 步内容互斥展示（key={step} 触发 `slideInUp` 0.35s）。
- **FR-043**：Sheet Footer 双按钮：
  - 左：「上一步」（step>1 显示）或「取消」（step=1 时显示，onClick 关闭 sheet）
  - 右：「下一步」（step<3）或「▶ 提交回测」（step=3）
  - Previous 按钮切换 step 时**保留**所有已填写 state（form / subscriptions / paramOverrides / strategyParams），由 Sheet 顶层持有；不重建子组件 state。
- **FR-044**：每步字段校验：未通过校验不允许进入下一步（inline error 显示在字段下方或 footer）。
- **FR-045（state 归属约定）**：`BacktestCreateSheet` 为所有跨步 state 的唯一 owner（`form / subscriptions / strategyParams / paramOverrides / paramsExpanded / advancedExpanded / step`），Step1/Step2/Step3 作为**受控子组件**通过 props 接收 state 切片 + `onChange` 回调；Step 子组件自身不持有跨步 state。

### FR Create Step 1：策略 & 标的
- **FR-050**：**策略选择**：保留现有 `<input>` + 下拉搜索 UI（filteredStrategies），不使用 shadcn Select（以便支持搜索）。
- **FR-051**：**标的选择**：改为 **受控搜索输入框 + 已选 chip 列表**。输入框（placeholder "搜索品种..."）过滤 `/api/data/symbols` 返回的品种；点击候选添加到已选；已选 chip 用 `bg-primary/15 text-primary rounded-full` 样式，每个 chip 有 × 移除按钮。**保留**策略切换时自动从 `/api/strategies/{name}/defaults` 填充默认 subscriptions 的逻辑 — 优先读 `d.subscriptions`，回退到 `d.symbols` + 默认 interval 构造 subscriptions 条目。
- **FR-052**：Step 1 **仅操作** `strategy_name` 与 `subscriptions[i].symbol`（新增/删除 chip 时改 subscriptions 数组）。`granularity / dataType / timeframe` 字段的详情编辑入口**仅在 Step 3 高级选项折叠区**内渲染（通过 `BacktestSubscriptionTable`，见 FR-073）。

### FR Create Step 2：时间 & 周期
- **FR-060**：**快捷周期 chip 行**：**显示 6 个快捷 chip** `1m / 5m / 15m / 1h / 4h / 1d`，选中态使用 `bg-primary/15 text-primary border-primary`，未选 `border-border text-muted-foreground`。
- **FR-061（周期白名单）**：由于后端 `TIMEFRAME_PRIORITY` 与 `_INTERVAL_MAP` 仅支持固定 12 个值（`1m / 3m / 5m / 15m / 30m / 1h / 2h / 4h / 6h / 8h / 12h / 1d`），"自定义 …"chip 点击展开输入框后：
  - 允许输入，但 onBlur 校验使用白名单正则 `/^(1m|3m|5m|15m|30m|1h|2h|4h|6h|8h|12h|1d)$/`。
  - 不通过则回滚到上次选中值并显示 `<InlineError>` 文案：`仅支持 1m/3m/5m/15m/30m/1h/2h/4h/6h/8h/12h/1d`（即后端完整白名单列表，让用户知道可选值）。
  - 通过则同步写入所有 `subscriptions[i].timeframe`，同时关闭自定义输入框并把"自定义"chip 标签更新为 `自定义 · {value}`。
  - 白名单内已列在 6 快捷 chip 中的值（`1m/5m/15m/1h/4h/1d`）也接受，但会回滚到快捷 chip 高亮（不保留自定义 chip 的高亮）。
- **FR-062**：**日期选择**：两个 `<input type="date">`（开始日期 / 结束日期），校验 `start < end`。
- **FR-063**：**K 线数量估算**：保留现有 `POST /api/backtest/estimate` 调用（subscriptions 或 date 变动 300ms debounce），estimate 结果显示在 step 底部（`预估运行时间 <primary>{label}</primary> · 约 {total_bars/1M}M bars`）。

### FR Create Step 3：资金 & 成本
- **FR-070**：**初始资金**（`<input>` with `,` thousand separator，默认 `100,000`）。
- **FR-071**：**手续费**：Maker / Taker 两个字段，默认 `0.02% / 0.05%`。
- **FR-072**：**滑点 / 延迟**：现有 `latency_mode` / `latency_ms` 两字段。
- **FR-073**：**高级选项折叠区**（默认折叠，点击展开，`ChevronDown` 旋转 180°）：
  - **Fill Model 下拉**：`<select>` 9 选项（`default / best_price / one_tick_slippage / two_tier / three_tier / probabilistic / size_aware / volume_sensitive / competition_aware`）。**这 9 个常量直接引用现有 `FILL_MODEL_OPTIONS` 数组**（源位置 `BacktestCreateView.tsx:49-59`，搬迁到 `BacktestCreateStep3.tsx` 模块顶部）。
  - 当 fill_model_type=`default` 时显示 `prob_fill_on_limit` / `prob_slippage` 两字段。
  - **Subscriptions 详情列表**（使用现有 `BacktestSubscriptionTable` 组件，在 Sheet 折叠区内渲染；保留 `symbol / timeframe / data_type / granularity` 四列完整编辑能力）。
  - **warmup_bars** 字段。
  - **tags** 字段。
- **FR-074**：**执行节点提示文案**：step 3 底部固定灰字提示「任务将在 **API 回测 worker** 中执行」（非 mock 的「沙盒节点」）。
- **FR-075**：**提交按钮**：保留现有 `useAction` + `POST /api/backtest/run` 流程；payload schema 不变。成功后关闭 sheet + 触发 `onSubmit()`（回到列表 + 刷新 runs）。**提交失败**（`useAction` state = `error`）：Sheet 保持打开，在 SheetFooter 上方插入 `<InlineError>`（文案取 `action.error?.message ?? "提交失败，请重试"`），不触发 toast、不关闭 Sheet。
- **FR-076（SubscriptionTable 动画迁移）**：`BacktestSubscriptionTable.tsx:57` 现有的 `data-form-section` + `FORM_SECTION_CLS` 动画机制依赖 `BacktestCreateView.tsx:111` 的 `useEffect` 触发 `data-visible=true`。在 Sheet 内部**不复用**该机制（折叠区展开即直接显示，无二次动画），具体做法：
  - 在 `backtestStyles.ts` 新增常量 `FORM_SECTION_STATIC_CLS`（等价 `FORM_SECTION_CLS` 但去掉 `opacity-0 translate-y-4` 预设，去掉 `data-[visible=true]:...`，仅保留 `mb-7 transition-[opacity,transform] duration-[450ms] ease-qds`）。
  - `BacktestSubscriptionTable.tsx:57` 的 `className` 由 `FORM_SECTION_CLS` 替换为 `FORM_SECTION_STATIC_CLS`，并删除 `data-form-section` 属性。
  - 其它所有既有 `data-form-section` 消费点同步删除（由于 `BacktestCreateView.tsx` 整体被删，自然失效；`BacktestSubscriptionTable.tsx` 是唯一外部消费点）。

### FR 详情视图（BacktestDetailView）
- **FR-080**：Detail 顶部保留现有结构：`← 返回` / strategy 名 + copyable run_id / 标的+interval+日期摘要 / 右侧导出/克隆/删除按钮。
- **FR-081**：**新增 6 列 KPI 网格**（位于顶部条和 tab bar 之间）：
  - 列 1：总盈亏（`$XXX,XXX`，`text-qds-success/text-destructive`）
  - 列 2：总收益率（`+XX.X%`）
  - 列 3：Sharpe
  - 列 4：Calmar
  - 列 5：胜率
  - 列 6：交易笔数
  - 列间使用 `border-l border-border` 分隔（首列无 border）。
  - 每列：label 上 + 大号 mono 数值中 + 小号 sub 下的三层结构（手绘 grid cell；不使用 `<StatCard>` 以避免 padding/border 与 6 列 grid 冲突）。
  - **状态降级规则**：`selectedRun.status === "completed" && selectedRun.result_summary` 时显示数值；否则所有 6 列统一渲染 `—`（值层 `<span className="font-mono text-[1.2rem] text-muted-foreground">—</span>`，sub 层不渲染）。
- **FR-082**：保留现有 pill tab bar（**7 个 tab**：Overview / Performance / Trades / Robustness / Tearsheet / Trade Log / Data Tables），但 tab bar sticky 到 KPI 网格下方。

  > tab key 与 label 对应：`overview → "Overview"` / `performance → "Performance"` / `trades → "Trades"` / `robustness → "Robustness"` / `tearsheet → "Report"` (即 Tearsheet) / `tradelog → "Trade Log"` / `reports → "Data Tables"`。**共 7 个 tab**（interview.md 中提到的"8 项"为笔误，`reports` 与 `datatables` 指同一个 tab）。

### FR Overview tab
- **FR-090**：保留现有 `OverviewKpiGrid` 的 11 指标 Secondary Grid（位于 6 列主 KPI 之下）。
- **FR-091**：**主视觉改为自绘 SVG equity+drawdown 叠加图**（替换现有 Recharts AreaChart 双列）：
  - 上层：equity 曲线（stroke=`var(--info)`, `strokeWidth=1.5`, gradient fill from `var(--info) @ 0.3` to `@ 0.02`）
  - 下层：drawdown 面积（stroke=`var(--dan)`, `strokeWidth=1.5`, gradient fill from `var(--dan) @ 0.25` to `@ 0.02`）
  - 绘制动画：`strokeDasharray=3000 + strokeDashoffset=3000` → 0，`animation: dash 1.8s 0.1s var(--eo) forwards`
  - grid lines：水平 3 条，stroke=`var(--chart-grid)`, dashArray=`3 3`
  - 文本：tokens only（`var(--t2)` labels，fontSize 10）
  - ResponsiveContainer 或手动 viewBox 铺满。
  - **无 tooltip/hover 交互**：本次仅实现绘制动画 + 静态 label；hover 游标等交互需求不在本次范围（Out-of-scope），未来若需恢复可回退到 Recharts 或新增 `<rect>` hit-box 实现。
- **FR-092（Equity SVG 空数据/加载失败降级）**：
  - `equity_curve === undefined || equity_curve.length === 0` 时，不渲染 `<svg>`，改渲染 `<InlineError variant="hint">暂无权益曲线数据</InlineError>`（容器高度仍撑满 `h-[280px]` 保持布局稳定）。
  - API 加载失败（`result === null && resultError`）时，由 `OverviewTab` 顶层统一渲染错误态（保留现有 `OverviewTab.tsx:105-113` 的错误态逻辑，不改）。
- **FR-093**：**月度热力图**：保留现有 `OverviewMonthlyHeatmap` 网格结构，调整色阶：
  - 正收益：`bg` 使用 `color-mix(in srgb, var(--suc) {alpha}%, transparent)`，alpha 12~57%（ratio-based）
  - 负收益：`bg` 使用 `color-mix(in srgb, var(--dan) {alpha}%, transparent)`
  - 替换现有硬编码 `rgba(76, 175, 80, ...)` 和 `rgba(239, 83, 80, ...)`（**这些颜色违反 DS 规则**）
  - **空数据降级**：现 `OverviewMonthlyHeatmap.tsx:19` `if (years.length === 0) return null;` 改为渲染 `<InlineError variant="hint">本区间无月度数据</InlineError>`，保持外层 grid 布局不塌缩。
- **FR-094**：Monthly heatmap + Drawdown table 布局为 **1.4fr / 1fr 双列**（mock 版式）。
- **FR-095**：保留现有的 Win/Loss + Long/Short Bars、三栏交易/PnL/持仓统计、TopTrades、InstrumentBreakdown、CorrelationMatrix 区块（但卡片头样式统一使用 `<SectionLabel>` 组件；FR-NFR-004 强制）。
- **FR-096**：所有「查看所有 N 笔」链接按钮点击 → 导航到 `view='trades'`（新增）。

### FR 其他 tab（保留微调）
- **FR-100**：Performance / Trades / Robustness / Tearsheet / TradeLog / Reports 六个 tab **保留现有内容**，仅微调卡片 header padding、`SectionLabel` 使用位置以与 Overview 风格统一。**不重绘图表**。

### FR Trades 视图（新增 view state）
- **FR-110**：**View 类型变更**：原 `type View = "list" | "create" | "detail"` 改为 `type View = "list" | "detail" | "trades"`（删除 `"create"`，因为创建改由 Sheet open state 控制；新增 `"trades"`）。
- **FR-111**：`handleViewAllTrades(runId)` 路由：设 `view='trades'` + 保留 `selectedRunId`。
- **FR-112**：Trades 视图顶部：返回按钮（← 返回详情） + 标题 `所有交易 · {strategy_name}` + `trade_count` 副标题。
- **FR-113**：**Summary Strip**：6 列指标（显示筛选后数量 / 总盈亏 / 胜败数 / 胜率 / 平均盈利 / 平均亏损），布局 `grid grid-cols-6 gap-4`，每列 label + mono 数值 + sub text。所有派生值使用 `useMemo(() => ..., [tradeLog, sideFilter, resultFilter, search])` 缓存，避免 1000 行级别下每次筛选重复计算（NFR-1 约束）。
- **FR-114**：**双 Tab Bar 筛选**：
  - 方向 tabs：All / Long / Short
  - 结果 tabs：All / Win / Loss
  - 复用现有 pill tab bar 样式（`bg-input rounded-md p-[3px]` + active `bg-secondary shadow`）。
- **FR-115**：**搜索**：`<input>`（placeholder "按交易 ID 或品种搜索..."，`kbd` hint 显示 `⌘K`）。`⌘K`（及 `Ctrl+K`）在 Trades 视图挂载时绑定 `keydown` 监听器，按下时 `searchInputRef.current?.focus()`。**不是装饰性文字**。
- **FR-116（Trades 表格 8 列 · 降列方案）**：由于 `TradeLogEntry` 类型（`src/web/src/app/backtest/types.ts:61-71`）不含 `mfe / mae` 字段，且 `MaeMfePoint`（`types.ts:22-27`）无 trade id/timestamp 无法与 trade_log 行级 join，**本次不显示 MFE/MAE 列**。Trades 表格使用 shadcn `<Table>` 渲染 **8 列**：
  1. ID（`instrument + opened_at` 派生的 `trade_id`：`${instrument}-${Number(new Date(opened_at))}`，用于搜索与 row key）
  2. 日期（`opened_at` 按 `YYYY-MM-DD HH:mm` 格式化）
  3. 方向 badge（BUY → `text-qds-success`；SELL → `text-destructive`；视图层显示 `Long`/`Short`）
  4. 入场价（`avg_open`，mono）
  5. 出场价（`avg_close ?? "—"`，mono）
  6. 仓位（`quantity`，mono）
  7. 盈亏（`realized_pnl`，mono + 符号色）
  8. 持仓时长（`duration`，mono）

  > **说明**：后端 `BacktestResult.mae_mfe` 数组依旧保留（由 Trades tab 的散点图消费），但行级 MFE/MAE 需要后端改造才能承载（违反 NFR-5 零后端改动）。本次范围内不实现 MFE/MAE 列；interview.md §验收标准 A.所有交易视图中的"MFE/MAE"表述以此 FR 为准更正为"本次仅 8 列"。
- **FR-117**：**分页**：每页 20 条，复用 `BacktestPagination` 组件。
- **FR-118**：数据源：`useBacktestDetail(selectedRunId, runs)` 的 `tradeLog`（已实现），转换 `TradeLogEntry[]` → Trades view 行数据（派生 side=BUY→long / SELL→short；无 MFE/MAE 列，见 FR-116）。
- **FR-119（Trades 空态）**：
  - `tradeLog.length === 0` → 渲染 `<EmptyState>` 文案 `此回测暂无交易记录`，不渲染 filter/search/表格/pagination。
  - `tradeLog.length > 0 && filtered.length === 0` → 渲染内联 `<EmptyState>` 文案 `无匹配交易，请调整筛选或搜索`，filter/search/summary 保留，表格与 pagination 不渲染。
- **FR-120（URL/深链策略）**：本次范围内 trades view 仅存活于 React state，不接入 `next/navigation` URL 参数。用户刷新页面会回到 `view="list"`。**Out-of-scope 声明**：深链（`?view=trades&run=xxx`）未来迭代。

### FR 非功能需求细化（NFR 延伸到 FR）
- **FR-NFR-001**：列表最小宽度 ≈ 1083px（10 列 grid 累加）。低于该宽度的响应式降级见 FR-NFR-002。
- **FR-NFR-002（响应式降级）**：
  - **≥ `xl` (1280px)**：全量 10 列 grid（`grid-cols-[3px_minmax(200px,2.5fr)_90px_minmax(180px,1.5fr)_120px_minmax(120px,1fr)_80px_80px_100px_110px]`）。
  - **`lg` (1024px) ~ `<xl`**：hide 列 7/8（Sharpe / Win Rate），grid 减为 `grid-cols-[3px_minmax(200px,2.5fr)_90px_minmax(180px,1.5fr)_120px_minmax(120px,1fr)_100px_110px]`（8 列）。通过 Tailwind `hidden xl:flex` 控制 Sharpe/Win Rate 两列容器。
  - **< `lg`**：hide 列 3、7、8（run_id、Sharpe、Win Rate），run_id 以小字 `text-[0.6rem] text-muted-foreground` 追加到"策略+标的"cell 的第二行。grid `grid-cols-[3px_minmax(200px,2.5fr)_minmax(160px,1.5fr)_120px_minmax(100px,1fr)_100px_110px]`（7 列）。
- **FR-NFR-003（无障碍降级）**：`@media (prefers-reduced-motion: reduce)` 下：Equity SVG 的 `animation: dash` 应用 `animation-duration: 0s`（立即显示最终状态 `stroke-dashoffset: 0`），ShimmerBar / stepper slideInUp / queued pulse dots 均应尊重该媒体查询（shimmer 已在 `animate-qds-shimmer` 实现中覆盖）。新增 `@keyframes dash` 与 `@keyframes slideInUp` 的使用点加 Tailwind `motion-reduce:animate-none` 或 `motion-reduce:[animation-duration:0s]`。
- **FR-NFR-004（卡片 header / SectionLabel 归一化）**：6 个 tab 内所有卡片 header 均使用 `<SectionLabel>` 组件（`components/qds/SectionLabel`），不允许使用 `qds-section-label` class + 手写 `<div>`（二选一时必须选组件），也不允许使用 shadcn `<CardHeader>` + 内联样式手写区块标签。**允许例外**：shadcn `<Card><CardHeader>` + `<SectionLabel>` 嵌套（Card 承担边框，SectionLabel 承担标题）。

## 4. 非功能需求

### NFR-1 性能
- 列表初次渲染 ≤ 500ms（100 条数据）。
- Overview 自绘 SVG 在 ≤ 200 equity 点数据下首帧渲染 ≤ 100ms，dash 动画 60fps。
- Trades 视图在 ≤ 1000 笔交易下分页切换无卡顿（summary 派生强制 `useMemo`，FR-113）。

### NFR-2 DS 合规（强制）
- **零新增非 keyframe class** 到 `globals.css`。
- 仅新增 2 个 `@keyframes`：`dash`（equity SVG 绘制）和 `slideInUp`（stepper 切步）。
- 通过 `src/web/scripts/verify-ds-compliance.sh` 全仓扫描（R1~R14 零违规）。
- 零 mock 原样 class（`.card / .tab-bar / .chip / .row-stripe / .sheet-overlay / .badge-run / .mono / .dim` 不得出现）。
- 零禁区 class（`bt-* / dc-* / cg / ca / cr / ci / dim / mono` 独立形式）。
- 零硬编码 hex / rgba 颜色（除 `globals.css` token 层外）。**清理范围**：
  - `OverviewTab.tsx:207-219`（`#E5534B` × 3 处）— 由 S11 替换 equity/drawdown Recharts 双列为 `<OverviewEquitySvg>` 时一并消除。
  - `OverviewMonthlyHeatmap.tsx:30-36`（`rgba(76, 175, 80)` / `rgba(239, 83, 80)` / `rgba(255,255,255,0.03)`）— 由 S2 替换为 `color-mix(in srgb, var(--suc/dan) ..%, transparent)` + `var(--bg-t)`。
  - `PerformanceHelpers.tsx:167/169/170`（`rgba(76, 158, 235, 0.5)` / `rgba(38, 217, 127, 0.5)` / `rgba(239, 83, 80, 0.5)`）— 由 S13 替换为 `CHART_COLORS.info / .success / .danger` 或 `var(--info/--suc/--dan)` + opacity 通过 color-mix。
  - `OverviewGreyTab.tsx`（37 处硬编码颜色） — **删除该文件**（见 FR-NFR-005）。
  - 其他含 `rgba(255,255,255,...)` 的 Recharts 轴/grid 样式（`PerformanceRollingChart.tsx`、`RobustnessTab.tsx`、`PerformancePeriodChart.tsx`）— **本次范围外**（属于 chart 样式专项，Out-of-scope），在 AC-C-4 断言中通过 `--exclude` 排除这些文件以避免误杀。
- 零内联 `style={{ fontFamily: "var(--font-d)" }}`（已由标准化扫描覆盖，SVG 中也不允许）。
- **强制使用 QDS 业务组件**：`<StatusBadge>`（替换 BtStatusBadge）/ `<ShimmerBar>`（替换原 mock 的 `.progress-shimmer`）/ `<SectionLabel>` 组件（不允许裸 `qds-section-label` class，FR-NFR-004）/ `<HelpTip>` / `<InlineError>` / `<PageHeader>`（可选）。
- **强制使用 chartTheme**：Recharts 继续用 `{...CHART_TOOLTIP_PROPS}` / `{...CHART_GRID_STYLE}` / `CHART_LEGEND_STYLE` / `CHART_LABEL_STYLE` / `CHART_COLORS` / `CHART_ANIMATION`。

### NFR-3 可访问性
- 所有可交互元素键盘可达（Tab 键顺序自然）。
- 所有 chip / tab / stepper 圆点有 `aria-pressed` / `aria-selected` / `aria-current`。
- Sheet 遵循 shadcn 原生 dialog a11y（ESC 关闭、焦点 trap）。
- 颜色不是唯一语义载体：方向/盈亏同时有文字标注（`BUY` / `SELL` / `+` / `-` 前缀）。
- `prefers-reduced-motion: reduce` 下关键动效降级（FR-NFR-003）。

### NFR-4 兼容性
- dark 和 light 两主题均通过 **脚本自动扫描**：`bash src/web/scripts/verify-ds-compliance.sh --mode both-themes` exit code = 0。**不做视觉走查。**
- `color-mix(in srgb, ...)` 浏览器支持（Chromium 111+/Safari 16.2+/Firefox 113+）由脚本扫描验证 token 替换完整性，保证 dark/light 切换时无硬编码颜色。

### NFR-5 API 与数据流
- **零后端改动**。所有 API endpoint 与 payload schema 保持不变：
  - `GET /api/backtest/runs?limit=100`（useBacktestRuns 轮询 5s）
  - `GET /api/backtest/{id}/result`（Overview / Performance / Trades tab）
  - `POST /api/backtest/run`（Create sheet 提交，payload 包含 `strategy / symbols / interval / start_date / end_date / initial_capital / params / data_type / maker_fee / taker_fee / fill_model / warmup_bars / tags`）
  - `POST /api/backtest/estimate`（Step 2 K 线估算）
  - `GET /api/strategies` / `GET /api/strategies/{name}/params` / `GET /api/strategies/{name}/defaults`
  - `GET /api/data/symbols`（Step 1 标的搜索）
- WS：保留 `useWsEvent("backtest.progress")` 驱动 running 行进度；断连降级见 FR-013。

### FR-NFR-005（废弃文件清理）
- `src/web/src/app/backtest/components/OverviewGreyTab.tsx` 在全仓 `grep` 确认 **零外部引用**（仅 `:513 interface OverviewGreyTabProps` 自引用、`:517 export function OverviewGreyTab` 自导出），属于历史 deprecated 文件，由 S15 直接删除。

## 5. 验收标准（全部为自动化可验证项）

### AC-A 像素级对齐（DOM/结构断言 via Playwright 或 Vitest）
- AC-A-1：列表首行渲染后，DOM 查询 `div.bg-card > div[style*="grid-template-columns"]`（xl 视口）首列 computed width 恰为 `3px`，且 className 字符串包含 `bg-qds-info|bg-qds-success|bg-destructive|bg-qds-t3` 之一。
- AC-A-2：running 行展开后，展开 block 包含至少 6 个 `[data-meta-cell]`，以及 1 个 `<svg>`（RingProgress，`querySelector('[data-ring-progress]')`）和 1 个 `<ShimmerBar>` 实例（`querySelector('[class*="animate-qds-shimmer"]')`）。
- AC-A-3：详情页顶部 6 列 KPI grid `querySelectorAll('[data-kpi-cell]').length === 6`。
- AC-A-4：Overview 自绘 SVG 存在 `<path>` with `stroke-dasharray="3000"` 起始值；在 `page.waitForLoadState("networkidle")` 之后调用 `Element.getAnimations()` 返回数组长度 ≥ 1 且包含 `animationName === "dash"`；`Element.getAnimations()[0].finished` 在 2.5s 内 resolve；resolve 后 stroke-dashoffset computed 为 `0`。
- AC-A-5：Monthly heatmap cell 背景通过 `color-mix(in srgb, var(--suc)/var(--dan) ..%, transparent)` 生成（源码 grep 见 AC-C-4）。
- AC-A-6：Sheet 宽度断言：
  - 视口 `1280 × 900`（≥sm）：渲染后 `[data-slot="sheet-content"]` computed `max-width` = `520px`，computed `width` = `520px`（shadcn Sheet side=right 实现）。
  - 视口 `600 × 900`（<sm）：computed `width` ≥ viewport width 的 95%（全屏抽屉）。

### AC-B 功能不退化（Playwright E2E — 4 个 spec 文件）
- AC-B-1（`create-sheet.spec.ts`）：打开 sheet → step1 选策略+标的 → step2 选周期+日期 → step3 默认资金 → 提交 → 列表在 5s 轮询内出现新 run_id。
- AC-B-2（`create-sheet.spec.ts`）：创建 sheet step3 展开高级选项，断言 `<select>` 包含 9 个 `<option>`；逐一选择 `best_price` / `probabilistic` / `competition_aware` 三种并提交；断言 `POST /api/backtest/run` payload 包含 `fill_model.fill_model_type`。
- AC-B-3（`detail-view.spec.ts`）：依次点击 7 个 tab，每 tab 首屏 `page.waitForSelector` 成功且 `page.on("pageerror")` 计数 = 0；重点 tab 断言：Robustness `<path class*="recharts-area">` 存在；Tearsheet `<iframe|embed|a[download]>` 存在；TradeLog 搜索框 `<input>` 可输入；Reports 切品种下拉存在。
- AC-B-4（`list-view.spec.ts`）：mock 一个 `status="running"` 的 run → 展开行 → 点击 Cancel → 断言 `POST /api/backtest/{id}/cancel` 被调用一次；mock API 返回 200 后列表刷新 → 该行 status 变为 `cancelling`。
- AC-B-5（`trades-view.spec.ts`）：从 detail Overview 点击「查看所有交易 →」→ URL 路径不变但 DOM `[data-view="trades"]` 存在；summary strip `querySelectorAll('[data-summary-cell]').length === 6`；方向 tab 点 Long → 表格行 `[data-side="long"].length === 表格行总数`；结果 tab 点 Win → 每行 `data-pnl-sign="positive"`；搜索框输入 `BTC` → 剩余行 `textContent` 均含 `BTC`；按 ⌘K → 搜索框 `document.activeElement === searchInput`；点击返回按钮 → `[data-view="detail"]` 存在。
- AC-B-6（`list-view.spec.ts`）：通过 `page.evaluate()` 模拟 WS `backtest.progress` 消息（dispatch custom event），断言 RingProgress 的 `strokeDashoffset` 在 1.5s 内更新到新值。
- AC-B-7（`create-sheet.spec.ts` · state 持久化）：Sheet step 1 输入策略 + 1 个 symbol → Next → step 2 选 `5m` → Next → step 3 修改 `initial_capital=200000` → Previous → Previous → 回到 step 1 → 断言 strategy/symbols 仍保留 → Next → Next → step 3 `initial_capital` 仍是 `200000`。
- AC-B-8（`list-view.spec.ts` · failed 重试）：mock `status=failed` run → 展开行 → 点击 `↻ 重试` → Sheet 打开；断言 step 1 strategy input value = failed run 的 strategy_name；symbols chip count ≥ 1；切到 step 2 → timeframe chip `5m` (或 failed run 的 interval) active；切到 step 3 → `initial_capital` = `100000` 默认值；Sheet Header 存在 `InlineError` hint 文案含 `已复制策略、标的`。

### AC-C DS 合规（脚本扫描 · 全部 exit code 断言）
- AC-C-1：`bash src/web/scripts/verify-ds-compliance.sh` exit code = 0。
- AC-C-2：`bash src/web/scripts/verify-ds-compliance.sh --mode both-themes` exit code = 0。
- AC-C-3（class 边界锚点）：`grep -rE '''(["'\'']|\\s)\\.(card|tab-bar|chip|row-stripe|sheet-overlay|badge-run|mono|dim)(["'\'']|\\s)''' src/web/src/app/backtest/` 零命中。该正则要求 class 名被引号或空白包围，规避 `bg-card` / shadcn `<Card>` / `data-tab-bar=...` 等假阳性。
- AC-C-4：`grep -rE "rgba\\(76, 175, 80|rgba\\(239, 83, 80|#E5534B|rgba\\(76, 158, 235, 0\\.5\\)|rgba\\(38, 217, 127, 0\\.5\\)" src/web/src/app/backtest/components/` 零命中（PerformanceRollingChart.tsx / RobustnessTab.tsx / PerformancePeriodChart.tsx 中的 `rgba(255,255,255,...)` 不在本次扫描范围，见 NFR-2）。
- AC-C-5：`grep -rE "fontFamily: ['\"]*var\\(--font-[ud]\\)" src/web/src/app/backtest/` 零命中。
- AC-C-6：`grep -nE "^@keyframes (dash|slideInUp) " src/web/src/app/globals.css` 恰好各命中 1 条。
- AC-C-7：`ls src/web/src/app/backtest/components/OverviewGreyTab.tsx` exit code ≠ 0（文件已删除）。
- AC-C-8：`ls src/web/src/app/backtest/components/BacktestCreateView.tsx` exit code ≠ 0（文件已删除）。
- AC-C-9：`grep -n "data-form-section" src/web/src/app/backtest/` 零命中（动画属性已随 FR-076 迁移）。

### AC-D 动效履约（computed style / Element.getAnimations 断言）
- AC-D-1：列表页渲染后 `animate-qds-fade-up` class 存在于页头 + 状态统计条 + 表格容器，通过 `getComputedStyle(el).animationDelay` 读到 `0ms / 100ms / 200ms`。
- AC-D-2：详情页 header / KPI grid / tab bar / tab content 四层 fade-up delay 依次 `0ms / 80ms / 160ms / 240ms`。
- AC-D-3：Equity SVG mount 后 `Element.getAnimations()` 返回的 `CSSAnimation.animationName === "dash"`；`finished` promise 2.5s 内 resolve；resolve 后 `getComputedStyle(pathEl).strokeDashoffset === "0px"`。
- AC-D-4：Stepper 步切换后 100ms 内 `Element.getAnimations()` 包含 `animationName === "slideInUp"`。
- AC-D-5：Running 行内联 shimmer bar 的子元素 class 包含 `animate-qds-shimmer`（`querySelector('[class*="animate-qds-shimmer"]')` 非 null）。

### AC-E 构建与类型检查
- AC-E-1：`cd src/web && npm run build` 0 error。
- AC-E-2：`cd src/web && npm run lint` 0 error。
- AC-E-3：`cd src/web && npx tsc --noEmit` 0 error。
- AC-E-4（单元测试）：`cd src/web && npx vitest run src/app/backtest/__tests__` 0 fail（至少覆盖 `BacktestTradesView` filter 派生、`OverviewEquitySvg` 空数据降级、`BacktestCreateStepper` 状态切换）。
