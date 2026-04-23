---
task: backtest-page-visual-refactor
created: 2026-04-20
ambiguity_final: 15%
---

# 回测页面视觉重构 · 访谈结晶

## 目标

**一句话**：对 `/backtest` 单页（list + create + detail + trades）进行基于用户提供的 React mock 的**视觉重构**，保留全部现有功能、API 与数据流，同时升级列表/详情/创建/交易明细四个视图的布局、组件结构和动效。

**核心意图**：
- 把现有「卡片式列表 + 内联 5 分节创建表单 + 7 tab 详情」替换为 mock 风格的「表格式列表（含 running 行内联展开 + RingProgress）+ 右侧 sheet + 3 步 stepper 创建表单 + 详情保留 7 tab 但顶部换成 6 KPI 网格 + 自绘 equity/drawdown 叠加 SVG + 月度热力图 grid + 独立「所有交易」视图」。
- 以 mock 为**视觉**参考，以现有代码为**功能与数据**基线。

## 范围

### 包含（In-scope）
- `src/web/src/app/backtest/page.tsx` 及其 `components/` 目录下所有回测相关文件。
- 列表视图 `BacktestListView.tsx`：改表格布局 + 3px accent stripe + running 行内联 + ring progress + shimmer 进度。
- 创建视图 `BacktestCreateView.tsx`：改为右侧 sheet + 3 步 stepper（策略&标的 / 时间&周期 / 资金&成本），但 **5 分节的全部字段**（策略、9 种 fill model、数据订阅、warmup、tags）一个不少地塞入 stepper 的 3 个页签（必要时在"资金&成本"步增加折叠的"高级选项"）。
- 详情视图 `BacktestDetailView.tsx` + 7 tab：顶部 KPI 采用 mock 6 列 grid 样式；Overview 采用自绘 SVG equity+drawdown 叠加图 + CSS grid 月度热力图 + 回撤周期表；其余 tab（Performance / Trades / Robustness / Tearsheet / TradeLog / Reports）**保留现有实现，仅微调卡片头样式与 section-label 对齐**。
- 新增 Trades 子视图（「查看所有交易」）：用于从 Overview/Trades tab 的"查看所有 N 笔 →"按钮跳转，沿用 mock 的六指标 summary strip + 双 tab-bar 筛选 + 分页表格。
- `src/web/src/app/globals.css`：**不新增 class**；如需补关键帧（dash、slideInUp）或修正现有 qds-* 动画参数，仅在 @keyframes 层调整。

### 不包含（Out-of-scope）
- 后端 API / WebSocket 契约变更。
- 创建表单业务逻辑变更（数据订阅逻辑、fill model 参数结构、策略参数注入）。
- 图表库迁移（Recharts 继续使用；仅 Overview 的 equity+drawdown 叠加层用自绘 SVG 实现 mock 风格的渐变 + 绘制动画）。
- 其他页面（dashboard / strategies / data catalog / factor / live / sandbox）样式。

## 非目标（Non-goals）

1. **不**减少任何现有功能——Robustness / Tearsheet / TradeLog / Reports / DataTables 5 个深层 tab **全部保留**。
2. **不**新增 globals.css 的非 keyframe class；mock 里的 `.card / .tab-bar / .chip / .row-stripe / .sheet-overlay / .badge-run / .mono / .dim` 等需全量映射到 Tailwind semantic + shadcn + `components/qds/`。
3. **不**改变沙盒节点与 API worker 的执行分工——回测跑在 **API 节点的回测 worker**，sheet 里的执行位置文案需改为"API 回测 worker"而非 mock 的"沙盒节点"。
4. **不**把时间周期限制为固定六选一（mock 的 `['1m','5m','15m','1h','4h','1d']` 仅作快捷；需保留自定义输入，因引擎支持 internal aggregation）。
5. **不**把标的选择仅做 chip 多选——必须保留搜索栏（当前 catalog 里的标的数量远超 mock 的 6 个硬编码）。

## 验收标准

### A. 像素级对齐 mock 视觉
- **列表表格**：首列 3px 色条（queued/done/running/failed 分色）；running 行 `colSpan=10`（或全宽）内联展开 RingProgress（44px） + 进度/速度/已用/剩余/交易 5 条元数据 + shimmer 进度条 + 取消按钮；failed 行左 3px 红条 + 内联错误信息 + 重试按钮；历史行 hover 背景高亮。
- **创建 sheet**：右侧抽屉 480~520px 宽度 + 顶部 stepper（3 圆点 + 标签 + 连接线，完成态变绿）+ body + 底部双按钮（上一步/取消 + 下一步/启动）。stepper 切步 slideInUp 0.35s。
- **详情顶部 KPI**：6 列 grid（总盈亏 / 总收益率 / Sharpe / Calmar / 胜率 / 交易笔数），每列 label + 大号 mono 数值 + 小号 sub text，列间 `border-left`。
- **详情 Overview**：自绘 SVG equity+drawdown 叠加图（渐变填充 + strokeDasharray 绘制动画 1.8s）+ 1.4fr/1fr 双列（月度 heatmap 12×N CSS grid / 回撤周期前 4 行表）。
- **所有交易视图**：6 列 summary strip（显示 / 总盈亏 / 胜败 / 胜率 / 平均盈利 / 平均亏损）+ 双 tab-bar 筛选（方向 / 结果）+ 交易 ID 搜索 + 10 列表格（ID/日期/方向 badge/入场/出场/仓位/盈亏/MFE/MAE/持仓）+ 底部分页（每页 20）。

### B. 现有功能不退化
- 详情 7 tab 全部可访问，Robustness MC 锥形图、Tearsheet PDF 预览、TradeLog 筛选、Reports 按品种/月份统计、DataTables 均正常渲染。
- 创建 sheet 内 9 种 fill model、数据订阅动态添加、warmup、tags 字段全部可配置并提交。
- `/api/backtest/runs` 轮询 + WebSocket `backtest.progress` 实时更新仍然驱动 running 行进度。
- `/api/backtest/run` 提交参数 schema 不变（后端不动）。
- 详情页点击"查看所有交易"跳转到独立的 Trades 视图且可返回。

### C. DS 合规
- `scripts/verify-ds-compliance.sh`（若存在）或等价 grep 检查无违规：无 `bt-*/dc-*/mono/dim/cg/ca/cr/ci/fr-*` 等禁用 class，无内联 `fontFamily: var(--font-d)`，无 mock 原样的 `.card/.tab-bar/.chip/.row-stripe/.sheet-overlay/.badge-run` 等新增样式类。
- 强制使用 `components/qds/`：`StatCard`（可选）/`SectionLabel`/`StatusBadge`（替换 BtStatusBadge）/`ShimmerBar`（替换 mock 的 progress-shimmer）/`HelpTip`/`InlineError`。
- 所有 Recharts 图表继续使用 `CHART_TOOLTIP_PROPS` / `CHART_GRID_STYLE` / `CHART_COLORS` / `CHART_ANIMATION`；不新增硬编码颜色。
- 自绘 SVG 部分使用 `var(--acc)/var(--suc)/var(--dan)/var(--chart-grid)/var(--t2)/var(--t3)` tokens，不写死 hex。

### D. 动效履约
- 列表 fade-up 分级：页头 d0 / tab+search d1 / 表格容器 d2（实现通过 `animate-qds-fade-up` + `animation-delay`）。
- 详情 fade-up：header d0 / KPI d1 / tab bar d2 / 内容 d3~d4。
- Equity SVG 曲线：`strokeDasharray="3000" strokeDashoffset="3000"` + `animation: dash 1.8s .1s var(--eo) forwards`（keyframes 从 3000→0）。新增 `@keyframes dash` 到 globals.css。
- Stepper 切步：每步内容 key={step} + `animation: slideInUp .35s var(--eo)`。新增 `@keyframes slideInUp`（若不存在；qds-fade-up 为 0→8px 上移不完全等同）。
- Shimmer 进度：使用现有 `animate-qds-shimmer`。
- Sheet 进出：使用 shadcn `Sheet` 组件自带动画（符合 280ms 进入 / 200ms 退出 + `--eo`/`--ei`）。
- 行 hover：`hover:bg-secondary transition-colors duration-150`。

### E. 功能合理化修正（相对 mock）
- **标的选择**：不是纯 chip 多选。改为搜索输入框 + 动态过滤（来源 `/api/data/catalog` 或策略的 subscribe 默认）+ 已选 chip 列表（可点 × 移除）。
- **时间周期**：保留 1m/5m/15m/1h/4h/1d 快捷 chip，旁边允许**自定义输入**（"自定义..." chip → 弹出小输入框，接受 `{n}{unit}` 格式并校验）。
- **执行节点文案**：mock 第 3 步底部提示从"沙盒节点"改为"API 回测 worker"。
- **fill model**：9 种 fill model 全保留；放在第 3 步"资金 & 成本"下方的折叠卡或独立的"高级"区块，默认折叠保持 stepper 视觉简洁。

## 实体（本体论）

### 稳定实体（3 轮后）
- **BacktestRun** — 列表行基本单位；字段 `id/name/syms/interval/range/status/pct/bars/speed/elapsed/eta/trades/pnl/retPct/sharpe/maxDd/winRate/err`。
- **BacktestDetail** — 详情视图数据容器；`kpis/equity/monthly/drawdowns/trades/params`。
- **Trade** — 交易明细记录；`id/date/side/entry/exit/size/pnl/mfe/mae/held`。
- **BacktestConfig** — 创建表单状态；`strategy/syms/interval/start/end/cash/feeBps/slipBps/fillModel/subscriptions/warmup/tags`。
- **TabKey** — 详情视图 tab 枚举；`overview/performance/trades/robustness/tearsheet/tradelog/reports/datatables`（7+ 项）。

### 映射到现有代码
| 实体 | 现有类型 |
|------|---------|
| BacktestRun | `BacktestResult` (types.ts) 的 list 变体 + WS progress |
| BacktestDetail | `BacktestResult` full |
| Trade | `TradeLogEntry`（类型已定义） |
| BacktestConfig | `BacktestCreateView` 本地 state 结构 |
| TabKey | `BacktestDetailView` 本地 state |

## 访谈记录

### 第 1 轮
**问**（瓶颈：目标清晰度。mock 是 4 tabs + 3 步 sheet + 表格列表，现有是 7 tabs + 5 分节 + 卡片列表，功能覆盖差异大）：mock 相对现有应作为什么？
**答**：**视觉重构 · 功能保留**。
**评分**：目标 0.85 / 约束 0.55 / 验收 0.6 / 上下文 0.75 / 架构 0.75 → 模糊度 ~35%
**本体论**：BacktestRun, BacktestDetail, TabKey 首次出现；稳定性 N/A。

### 第 2 轮
**问**（瓶颈：约束清晰度。mock 用的 .mono/.dim/.card/.tab-bar/.chip/.row-stripe/.sheet-overlay 等 class 在 DS 标准化后已删除，实现路径忽视会 CI 失败）：mock 样式类如何映射到已标准化的技术栈？
**答**：**全量映射到 Tailwind semantic**（零 class 新增）。
**评分**：目标 0.90 / 约束 0.88 / 验收 0.70 / 上下文 0.80 / 架构 0.80 → 模糊度 ~20%
**本体论**：新增 BacktestConfig（创建表单 state）；稳定性 75%（3 稳定 + 1 新增）。

### 第 3 轮
**问**（瓶颈：验收标准清晰度。mock 写死数据、现有跑真 API，需要明确两者对齐判据）：重构完成的验收标准包括哪些？
**答**：多选 — 像素级对齐 + 功能不退化 + 动效全量履约 + DS 合规。用户另补 4 条实体修正：
1. 币对需搜索栏（非纯 chip）；
2. 时间周期可自定义（非固定六选一）；
3. 9 种 fill model 全保留；
4. 执行在 API 节点的回测 worker，非 sandbox。

**评分**：目标 0.92 / 约束 0.90 / 验收 0.90 / 上下文 0.82 / 架构 0.80 → 模糊度 **~15%**
**本体论**：Trade 实体首次独立（从 BacktestDetail.trades 分离）；稳定性 80%（4 稳定 + 1 新增）。

### 本体论收敛表
| 轮 | 实体数 | 稳定 | 新增 | 稳定性 |
|----|-------|-----|------|-------|
| 1 | 3 | — | 3 | N/A |
| 2 | 4 | 3 | 1 | 75% |
| 3 | 5 | 4 | 1 | 80% |

## 技术上下文（来自 explorer）

- **当前路由**：`src/web/src/app/backtest/page.tsx`（view='list'|'create'|'detail'） → 三个子 view。
- **DS 状态**：2026-04-19 已完成迁移；回测页面零违规；禁用 class 清单固化。
- **Tokens**：`--acc / --suc / --dan / --bg-p/s/t/in / --t0~t3 / --bd / --bds / --chart-grid / --acc-d / --suc-d / --dan-d / --eo / --ei / --dur / --r / --rm / --rs`。
- **现有 keyframes**：`qds-fade-up`, `qds-shimmer`, `qds-slide-in`, `qds-slide-right`, `qds-pulse-ring`, `qds-dialog-enter`, `tick-flash-up/down`。缺 `dash`、`slideInUp`。
- **chartTheme.ts** 已导出：`CHART_TOOLTIP_PROPS / CHART_GRID_STYLE / CHART_COLORS / CHART_ANIMATION / CHART_LEGEND_STYLE / CHART_LABEL_STYLE`。
- **QDS 组件**：`StatCard / ShimmerBar / StatusBadge / SectionLabel / PageHeader / HelpTip / InlineError`。
- **API endpoints**：`GET /api/backtest/runs` | `GET /api/backtest/{id}/result` | `POST /api/backtest/run` | `POST /api/backtest/estimate` | `GET /api/strategies` | `GET /api/strategy/{name}` | `GET /api/data/catalog`。
- **WS**：`backtest.progress` 事件。
- **执行**：API 容器内 backtest worker subprocess（非 sandbox/live node）。
