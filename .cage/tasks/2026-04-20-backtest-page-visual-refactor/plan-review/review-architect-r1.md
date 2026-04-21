# Architect Review — Round 1

**VERDICT: REVISE**

## 摘要

规划整体质量较高：代码引用几乎全部可追溯到真实文件，DAG 结构在多数维度上是最优的，mock class 映射表（40+ 条）覆盖完备，自绘 SVG 的替代方案分析也到位。但有 3 个 **Major** 问题必须在第 2 轮解决：(1) OverviewTab 中遗存的硬编码 `#E5534B` 未被明确列入清理任务（AC-C-4 会直接命中失败）；(2) 列表表格的 10 列 grid 模板字符串与现有 `BacktestRunRow` 的 5 列结构存在合并冲突，S3/S4/S5 并行时需更强的编辑边界保证；(3) 自定义时间周期校验正则 `/^\d+(s|m|h|d)$/` 允许秒级输入，但后端 `_INTERVAL_MAP`（`catalog.py:12-18`）并不支持秒或 `3m` 之外的任意组合，前端校验与后端能力不一致。另有若干 **Minor** 改进项。

## 代码引用验证

| 引用 | 文件存在 | 内容准确 | 问题 |
|------|---------|---------|------|
| `src/web/src/app/backtest/components/BacktestCreateView.tsx:49-59` (FILL_MODEL_OPTIONS) | Yes | **Yes** — 9 元素常量精确对齐，key=value+label+hint 结构匹配设计引用 | 无 |
| `src/web/src/app/backtest/components/BacktestCreateView.tsx:503-505` (执行文案) | Yes | **Yes** — 行号精确，文案内容为 `预估运行时间 {estimated_label} · 约 {M} bars`，tech-design §8.1 逐字复刻 | 无 |
| `src/web/src/app/backtest/components/OverviewMonthlyHeatmap.tsx:30-37` (rgba 硬编码) | Yes | **Yes** — `rgba(76, 175, 80, ${alpha})` / `rgba(239, 83, 80, ${alpha})` 精确匹配设计文档 §4.5 的引用 | 无 |
| `GET /api/data/symbols` | Yes | **Yes** — 已被 `BacktestSubscriptionTable.tsx:47` 和 `data-catalog/FetchDialog.tsx:104` 实际调用，返回 `BinanceSymbol[]` | 无（主 agent brief 中列出的「潜在阻塞问题」**实际不存在**） |
| `/api/strategies/{name}/defaults` + `/api/strategies/{name}/params` | Yes | **Yes** — 已在 `BacktestCreateView.tsx:152,171` 使用 | 无 |
| `components/qds/ShimmerBar` | Yes | **Yes** — `shimmer-bar.tsx` 导出 `ShimmerBar`，props 含 `progress/height/active/variant`，匹配 tech-design §4.1 | 无 |
| `components/qds/StatusBadge` | Yes | **Yes** — 支持 `locale="zh"|"en"`、已覆盖 running/done/completed/failed/queued/cancelled/cancelling 7 种态，无需扩充 | 无 |
| `components/ui/sheet.tsx` | Yes | **Yes** — 基于 `@base-ui/react/dialog`，`<SheetContent side="right">` 原生支持 | 无 |
| `src/web/scripts/verify-ds-compliance.sh` | Yes | 未验证 --selftest/--preflight-before-css-delete 行为 | 无（假设脚本按 CLAUDE.md 描述工作） |
| `src/web/src/lib/chartTheme.ts` | Yes | **Yes** — 导出 `CHART_TOOLTIP_PROPS / CHART_GRID_STYLE / CHART_COLORS / CHART_ANIMATION / CHART_LEGEND_STYLE / CHART_LABEL_STYLE` | 无 |
| `globals.css` 现有 keyframes 列表 | Yes | **部分偏差** — interview.md 列出的现有 keyframes 缺 `fade-up`（295 行，与 `qds-fade-up` 共存）、`shimmer`（319 行，与 `qds-shimmer` 共存）、`spin`（325 行）、`live-pulse`（330 行）、`qds-skeleton`（371 行）、`toast-countdown`（377 行）、`qds-toast-enter/exit`（386-390 行）；已有 18 个 keyframes，确认没有 `dash` / `slideInUp` | Minor |
| `selectedRun.result_summary.*`（Sharpe/Calmar/胜率/交易笔数/总盈亏/总收益率） | Yes | 需进一步核实 `BacktestRunSummary` 类型定义：Grep 显示 `BacktestListView.tsx:25` 中 `result_summary` 为可选字段，tech-design §4.3 的 KPI 依赖 `sharpe_ratio / calmar_ratio / win_rate / total_trades / total_pnl / total_return_pct`，与 `BacktestRunRow.tsx:151,296,366,372,376,382,386,391,396,402` 实际使用的字段一致 | 无 |
| `OverviewTab.tsx:151-226` drawdown Recharts block（硬编码 #E5534B）| Yes | **Yes** — 第 207、219 行实际是 `#E5534B`（两处，分别是 `<stop stopColor>` 和 `<Area stroke>`），与 tech-design §4.4 Token 使用清单中提到的「`#E5534B` / `rgba(76,175,80,0.3)` 均不允许」对齐 | 见下方 Major-1 |

**结论**：全部引用有效，无悬空。主 agent brief 中担心的 `/api/data/symbols` 端点不存在是误判，该端点已在 2 个生产文件中调用。

## 需求审查 (1-requirements.md)

- **FR-110 的 View 枚举变更**：`"list" | "create" | "detail" | "trades"` 技术上可行；Sheet 用 open state 后 `create` 项会被移除，要点已在 4-tasks.md S6 中明确。**Minor**：需求文档本身第 110 条表述 "追加 'trades'" 与设计 §2 表述 "列表 / create sheet（非 view state）/ detail / trades" 存在轻微文字不一致，critic 可能会追这个。
- **AC-C-3 grep 模式** `\b(card|tab-bar|chip|row-stripe|sheet-overlay|badge-run|mono|dim)\b`：
  - **Major**：`card` 作为独立 class 会误命中 shadcn `<Card>` 组件和 Tailwind `bg-card`。`\b\.card\b` 或 `className=".*\bcard\b` 这种裸 class 选择器才是真命中；且 `tab-bar` 不带前缀时会匹配 `data-tab-bar=...` 等 HTML5 属性。建议规范为正则 `['"\s]\.(card|tab-bar|chip|row-stripe|sheet-overlay|badge-run)(?=\s|['"])`，或直接对 `className` 字符串内的整词做扫描，避免假阳性。
- **AC-D-3** 使用 `Element.getAnimations()` 检测 `strokeDashoffset`：API 在 WebKit 中对 SVG `<path>` 的 stroke-dashoffset 动画支持略有差异，若 CI 用 Playwright/chromium 即可，**Minor** 提醒。

## 技术设计审查 (3-tech-design.md)

### Critical 发现
无。

### Major 发现

1. **OverviewTab.tsx 残留的 `#E5534B` 未在任务清单中显式清理**（阻塞 AC-C-4）
   - **证据**：`src/web/src/app/backtest/components/OverviewTab.tsx:207`（`<stop offset="5%" stopColor="#E5534B" stopOpacity={0.4} />`）与 `:219`（`<Area type="monotone" ... stroke="#E5534B" ...>`）。
   - **问题**：tech-design §4.4 宣告新 SVG 组件"严禁 hex 颜色"，S11（任务 4-tasks.md:169-190）描述 "删除现有 equity + drawdown 的 Recharts 双列 JSX（`~line 151-226`）替换为 `<OverviewEquitySvg>`"。如果该删除**真的**覆盖 151-226 行且只剩 `<SectionLabel>` + `<OverviewEquitySvg>`，#E5534B 会随之消失。但是 tech-design §3.2 表格中 OverviewTab.tsx 的变更说明是 "~50 改 / ~40 删"，这只够删除 drawdown 那一半。executor 若未注意会留下 Equity 的 block 而保留 drawdown 的 #E5534B。
   - **影响**：AC-C-4 `grep -rE "rgba\(76, 175, 80\|rgba\(239, 83, 80\|#E5534B" src/web/src/app/backtest/"` 直接命中失败，S15 被阻塞。
   - **修复**：在 S11 验收条件中显式追加 `grep "#E5534B" src/web/src/app/backtest/components/OverviewTab.tsx` = 0。同时在 tech-design §3.2 将 OverviewTab 变更量从 "~50 改 / ~40 删" 修正为 "~50 改 / ~80 删"（覆盖两段 Recharts）。S2 的 acceptance 里已有 `grep "rgba(76, 175, 80|rgba(239, 83, 80|#E5534B" src/web/src/app/backtest/components/"` 零命中，但此检查在 S11 完成**之前**跑，提前失败无意义。应将此扫描移到 S11 acceptance。

2. **自定义周期校验正则与后端可用周期不一致**（阻塞 B 类需求：功能不退化）
   - **证据**：设计 §4.2 Step 2 校验 `/^\d+(s|m|h|d)$/`。后端 `src/tinohelm/backtest/runner_helpers.py:20-22` 的 `TIMEFRAME_PRIORITY` = `("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d")`，而 `catalog.py:12-18` 的 `_INTERVAL_MAP` 也是上述 12 个值。后端 `interval_to_minutes()` 正则接受 `s/m/h/d` 但 `s` 会被向下取整到 1 分钟（`runner_helpers.py:35-36: max(1, n // 60)`）。
   - **问题**：
     - 前端允许输入 `30s` → 后端收到后 `catalog.py:37` 会抛 `ValueError: Unsupported interval '30s'`，回测提交失败。
     - 前端允许输入 `7m` / `45m` / `3h` / `5d` 等任意正整数 + 后缀，但后端只支持 **白名单**的 12 个周期。
   - **影响**：US-3（Create Sheet 三步提交）的 AC-B-1 会在随机参数下失败。用户承诺"功能一个不能少"与"自定义周期"不能妥协，但实际引擎限制意味着所谓"自定义"只能是白名单挑选。
   - **修复**：三选一。
     - A. 将 chip 集合扩充至全部 12 个白名单周期（`1m/3m/5m/15m/30m/1h/2h/4h/6h/8h/12h/1d`），砍掉"自定义"chip。**推荐**。
     - B. 保留"自定义"，但改校验正则为白名单 `/^(1m|3m|5m|15m|30m|1h|2h|4h|6h|8h|12h|1d)$/`（实际没有"自定义"意义）。
     - C. 保留"自定义"，前端提交前用 `interval_to_minutes` 等价逻辑映射到最近支持的白名单值，并展示 warning。
   - 无论哪种方案，4-tasks.md S8 的 acceptance 第 2 条"输入 `30m` → 校验通过" 都需要换成白名单内的值（`30m` 在白名单里，但 `7m` 不在）。interview.md §"功能合理化修正" 第 2 条"保留自定义输入，因引擎支持 internal aggregation" 的措辞对此亦过于乐观 — composite aggregation 实际上是把 1m 数据按 step 聚合到 NT 支持的 BarAggregation 枚举，不支持任意数字。

3. **S3/S4/S5 对 `BacktestRunRow.tsx` 的并行编辑存在合并冲突风险**
   - **证据**：4-tasks.md `parallel_groups` 第 4 波 `["S4", "S5", "S7", "S8", "S12", "S14"]` 中 S4（`BacktestRunRow.tsx` running 展开）与 S5（`BacktestRunRow.tsx` 的 `BacktestHistoryRow` failed 分支）都编辑同一个文件。当前文件 441 行，`BacktestRunRow` 在 35-282 行、`BacktestHistoryRow` 在 295-440 行。
   - **问题**：虽然两个函数边界清晰，但：
     - 两个子任务都可能修改顶部 import 块（`import { RotateCcw }` for S5 vs `import { ShimmerBar }` for S4）。
     - 两个子任务都需要新增回调 prop（`onRetry?` / `loadRuns`），如在 `BacktestRunRowProps` 或同文件的 `BacktestHistoryRowProps` 中并行声明会产生 TypeScript interface 合并冲突。
     - S5 task 描述中 "`BacktestListView.tsx` 新增 `onRetry` prop 透传" 和 S4 task 描述中 "`BacktestListView.tsx`（传递 `loadRuns`）" 都在改同一个中间文件。
   - **影响**：并行 executor 会在同一文件多处写入，最后 git merge 冲突；或某个 agent 的 prop 覆盖另一个。
   - **修复**：三选一。
     - A. 将 S4 和 S5 改为串行（`S5 depends_on: [S3, S4]` 或反过来）。**推荐**，工时增加 1h 但规避风险。
     - B. 保留并行，但把 `BacktestRunRow.tsx` 拆成两个文件 `BacktestRunRow.tsx`（running/queued）+ `BacktestHistoryRow.tsx`（completed/failed），在 S3 中完成拆分，S4/S5 各自编辑独立文件。
     - C. 在 S3 中把 S4 + S5 两套 props（`onRetry`、`loadRuns`）都预先添加到 interface（空实现），S4/S5 只填 body。
     - 无论哪种，`BacktestListView.tsx` 也需同样处理（把 prop 声明挪到 S3）。

### Minor 发现

1. **`OverviewGreyTab.tsx` 未在文件清单中说明归属**
   - `ls` 显示该文件存在（23456 bytes），但 tech-design §3 三张清单（新增/修改/删除/保留）都没提到它。
   - Grep 只发现它在 `OverviewGreyTab.tsx:513,517` 自引用，没有其他文件 `import` 它 — **很可能是废弃文件**。
   - 建议：在 S15 清理任务中显式检查 `grep -rn "OverviewGreyTab" src/web/ | grep -v OverviewGreyTab.tsx` 是否零命中，若是，删除此文件（+1 条删除清单项）。

2. **KPI 数据映射遗漏 Calmar 来源**
   - tech-design §4.3 KPI_ITEMS 第 4 项 "Calmar"。但查 `BacktestListView.tsx:25` 的 `result_summary` 子集字段和 `BacktestRunRow.tsx` 已使用的字段（sharpe_ratio / win_rate / profit_factor / max_drawdown / calmar_ratio / total_pnl / total_return_pct / total_trades），`calmar_ratio` 存在但未在设计中明确映射。需确认 `BacktestRunSummary["result_summary"]` 类型声明中有 `calmar_ratio`。Grep `BacktestRunRow.tsx:386` 已经在用 `s.calmar_ratio`，确认存在；可在 tech-design §4.3 的 KPI_ITEMS 注释中补充字段名对照。

3. **Symbol Picker 数据源与策略默认 subscriptions 的联动**
   - FR-051 说 "保留策略切换时自动从 `/api/strategies/{name}/defaults` 填充默认 subscriptions 的逻辑"。现有 `BacktestCreateView.tsx:164-207` 实现了两层 fallback：优先 `d.subscriptions`，其次 `d.symbols`。S7 描述仅说"搬运策略下拉逻辑（包含 `strategyDropdownOpen` state 和过滤、策略切换时的 defaults 拉取）"，没明确提 fallback 逻辑必须保留。Executor 可能简化为"仅支持 subscriptions"。
   - 建议：S7 acceptance 第 1 条由"选策略后 `subscriptions` 状态被策略 defaults 填充" 细化为 "当 API 返回 `subscriptions` 时直接用；当只返回 `symbols` 时用默认 interval 构造 subscriptions"。

4. **AC-A-4 断言路径不完整**
   - `getComputedStyle` 读 `animationName` 只在元素真正 mount + 动画 keyframe 已注册时生效；但 SVG `<path>` 的 `animation: dash` 是 inline style，浏览器会 compute 成 `dash`，OK。建议补充断言：动画结束后 `strokeDashoffset === 0`（使用 `Element.getAnimations()[0].finished.then(...)`）。

5. **Trades 视图分页性能未评估**
   - NFR-1 说 "≤ 1000 笔交易无卡顿"。但 summary strip 的 6 个 aggregation（平均盈利/亏损/胜率等）随 filter+search 每次变化都会遍历 `filtered`，这没用 `useMemo`。建议 S14 acceptance 明确要求 summary 用 `useMemo(() => {...}, [tradeLog, sideFilter, resultFilter, search])`。

6. **PNL 显示符号的小问题**
   - tech-design §4.3 `KPI_ITEMS.1` 格式 `${totalPnl >= 0 ? '+' : '-'}$${|totalPnl|}`。但 `BacktestRunRow.tsx:153` 现有逻辑是 `total_pnl >= 0 ? "+" : ""`（非正数时不加号，因为数字本身自带负号），`BacktestHistoryRow` 行 342 用 `total_pnl >= 0 ? "+" : "-"` 又加了 `-`。两处不一致。建议在 tech-design §4.3 明确采用哪种，并在 S10 acceptance 中固化断言。

## DAG 审查 (4-tasks.md + task.json)

### 依赖正确性

- **S15 depends_on**：`[S4, S5, S9, S12, S13, S14]` — 缺失 S11。虽然 S12 依赖 S11，传递上 S15 → S12 → S11，但为明确起见建议补充 S11 到 S15 的直接依赖（不改变 DAG 拓扑，仅提升可读性）。**Minor**。
- **S13 depends_on**：`[S10]` — 但 S13 描述说 "扫描 6 个 tab 的卡片 header + section label 使用情况"，这 6 个 tab 的文件与 S10（插入 6 列 KPI 网格）修改的 `BacktestDetailView.tsx` 不重叠。S13 实际上只需要 S15 风格基准建立后再微调即可，与 S10 没有数据依赖。建议把 S13 依赖改为 `[S11, S12]`（风格基准来自 Overview），或者干脆平行到第 4 波。**Minor**。
- **S12 depends_on**：`[S2, S11]` — 设计 §4.5 heatmap 色阶改造已在 S2 完成，S12 只合并布局。这里依赖 S11 是因为 S12 在 `OverviewTab.tsx` 里插入 `<OverviewEquitySvg>` 后再调整 heatmap 双列。OK。

### 并行最优性

- **波 3** `[S3, S6, S10, S11]`：各改 `BacktestListView.tsx+BacktestRunRow.tsx`（S3）、新建 Sheet 文件 + 改 `page.tsx`（S6）、改 `BacktestDetailView.tsx`（S10）、新建 `OverviewEquitySvg.tsx` + 改 `OverviewTab.tsx`（S11）。
  - **冲突点**：S3 和 S6 都改 `page.tsx` — S3 为 `loadRuns` 透传（根据 S4 描述）；S6 为删 `view === "create"` + 加 Sheet open state。
  - 检查 S3 描述：`src/web/src/app/backtest/components/BacktestListView.tsx, src/web/src/app/backtest/components/BacktestRunRow.tsx` — `page.tsx` 不在 S3 files 列表。
  - 检查 S6 描述：含 `src/web/src/app/backtest/page.tsx` — 仅 S6 改 `page.tsx`。OK。
  - 但 S4 描述含 `page.tsx`（传 loadRuns），与 S6 冲突：波 4（S4）晚于波 3（S6），但 S4 的 page.tsx 修改可能覆盖 S6 的 view state 改造。需 executor 在 S4 开始时 git pull + rebase，或把 S4 的 page.tsx 改动合并到 S6。**建议**：S4 的 page.tsx 改动移到 S6（波 3），S4 只管 Row 和 ListView。

- **波 4** `[S4, S5, S7, S8, S12, S14]`：6 个并行任务。
  - S4+S5 文件冲突风险（见 Major-3）。
  - S7/S8/S9 通过 `BacktestCreateSheet.tsx` 的 step 切换间接耦合。S9 需要引入 S7 和 S8 的 export（`<BacktestCreateStep1 />` 等），当前 S9 在波 5 单独执行 OK。
  - S12 依赖 S11 已在波 3 完成，OK。
  - S14 独立（新建 `BacktestTradesView.tsx` + 改 `page.tsx`）— 但波 3 S6 也改 `page.tsx`，S14 在波 4 也改 `page.tsx`，且 S14 要在 `page.tsx` 加 `view === "trades"` 分支 + `handleViewAllTrades`。**这里有第二个 page.tsx 合并冲突** — S6 (波 3) vs S14 (波 4) 虽然时间上先后，但两者改动区段不同。需确保 S14 在 S6 完成后 pull 最新再改。

- **最终结论**：并行分组**整体可行但需加强编辑边界约束**。具体建议：
  - 将 S4 的 `page.tsx` 职责合并到 S6。
  - S3 的 `BacktestListView.tsx` 改动只涉及 grid 模板和 header，不传递 `onRetry`/`loadRuns`；S5 不改 `BacktestListView.tsx`（只改 RunRow 里的 HistoryRow）；`onRetry` 的回调直接从 RunRow 内通过 context 或 prop drilling 从 page.tsx 注入（由 S6 完成）。
  - S14 的 `page.tsx` 改动明确声明基于 S6 完成后的 view state 机制。

### 遗漏任务

- **删除 `OverviewGreyTab.tsx`**：见 Minor-1，若确认无引用需列入 S15 删除清单。
- **types.ts 扩展**：新增 View `"trades"` 后，page.tsx 外部若有任何 `View` 类型导入需同步。Grep 确认：当前 `View` 在 `page.tsx:15` 是 **local type**，未导出，故无外部依赖。OK。
- **`BacktestSubscriptionTable.tsx` 对 `data-form-section` 的引用**：该文件 57 行使用 `FORM_SECTION_CLS`，内含 `data-form-section` 属性 + `opacity-0` → `data-visible=true` 动画触发。S9 描述"`BacktestSubscriptionTable` 整合进折叠区"，但折叠区展开时 `data-form-section` 动画的触发点是 `BacktestCreateView.tsx:111` 的 `useEffect`。Sheet 里没有此逻辑，`SubscriptionTable` 内的字段会始终处于 `opacity-0` 状态（表单不可见）。
  - **修复**：S9 必须在 Sheet `useEffect` 中复制 `sectionsRef.current?.querySelectorAll("[data-form-section]").forEach(...)` 逻辑，或把 SubscriptionTable 内的 `FORM_SECTION_CLS` 换成无动画版本。建议后者（折叠区打开 → 直接渲染，不需要二次动画）。

### 任务粒度

- **S9 est 3h** 是所有任务里最大的，包含 5 个子块（基础区 / 折叠区 / fill model / subscriptions / 提交）。建议拆成 S9a（基础 + 折叠骨架）+ S9b（fill model + subscriptions + 提交）以降低单 executor 风险。**Minor**。
- **S13 est 1.5h** 偏大：扫描 6 个 tab 文件的 header/section label，如果有任何文件 card header 内嵌 `#E5534B` 或 `font-family: var(--font-d)` 内联样式，修复量会爆增。建议 S13 首先跑一遍 `verify-ds-compliance.sh` 快速统计违规数，再决定时间预算。

### 验证任务

- S15 作为最终验证节点合理，覆盖了构建+lint+tsc+合规扫描+AC 验收。
- **缺失**：没有 Playwright E2E 任务。需求文档 AC-B 列了 6 条 E2E 测试（AC-B-1~6），S15 只跑脚本扫描和 build，**没跑 Playwright**。建议：
  - 新增 S16：运行 `npx playwright test`（假设 spec 文件已按 AC 要求编写）。若 spec 文件属实现范围，应在 S14 产出 `trades-view.spec.ts` 等。
  - 或者在 S15 acceptance 中明确 AC-B 为人工走查确认项，与 AC-A/C/E 的自动化断言分离。

## 权衡分析

| 决策 | 正方 | 反方 | 建议 |
|------|------|------|------|
| Overview equity+drawdown 用自绘 SVG 取代 Recharts | (1) 支持 `strokeDashoffset` 1.8s dash 动画，Recharts 不支持；(2) 单 SVG 叠加渲染 2 条曲线比 Recharts 双列 ResponsiveContainer 轻量（~60% bundle impact 省略）；(3) 完全对齐 mock 视觉 | (1) 失去 Recharts 的 Tooltip/Legend/ResponsiveContainer 能力 — hover 显示具体时刻的 equity/drawdown 值功能**退化**；(2) 失去 Recharts 的响应式 resize，需手动监听 ResizeObserver；(3) 自绘 SVG 的 hover 交互若要实现需手写 `<rect>` hit-box + state，工作量 +1h；(4) tech-design §4.4 未说明是否保留 tooltip | **方案调整**：保留 Recharts 作为底层，用 `animationDuration={1800}` + `animationEasing="ease-out"` + 自定义 stroke `<animate>` 元素实现 dash 效果。或 fallback：自绘 SVG + 补回 tooltip（hover 投影 + `<line>` 游标）。tech-design §4.4 需增加 "交互要求" 子节，明确是否需要 hover tooltip。**若用户接受无 tooltip，现方案 OK**；若要 tooltip，自绘方案工作量低估 ~1.5h |
| 创建视图从 view='create' 改为 Sheet overlay | (1) 用户明确要求；(2) Sheet 叠在列表上视觉连续；(3) shadcn Sheet 已有，零成本；(4) Sheet 原生 a11y（ESC、focus trap） | (1) Sheet 宽度 520px 对 9 种 fill model + subscriptions table + params override 较拥挤（SubscriptionTable 单行展开后 ~800px 宽度）；(2) `data-form-section` 动画机制与 Sheet mount 生命周期耦合需要新代码（见上遗漏项） | **可接受**。但需在 S9 验收中补充"`BacktestSubscriptionTable` 在 Sheet 内渲染时横向不溢出（可滚动或紧凑）"的视觉断言。 |
| 9 种 fill model 折叠到 Step 3 高级区 | (1) 解决 mock 3 步 stepper 与用户"功能一个不能少"的矛盾；(2) 默认折叠保持视觉简洁；(3) 高阶用户可展开 | (1) 新手用户不展开可能永远看不到高级能力（discoverability ↓）；(2) Maker/Taker 费率与 fill model 语义上都属于"交易成本"，把 fill model 单独扔进折叠区，基础区保留 maker/taker 会让用户以为 fill model 只影响"成本"而非"微结构" | **可接受**。建议在 Step 3 基础区顶端加小字："默认使用 1-tick 滑点模型。如需配置其他成交模型（9 种），展开下方高级选项。" 避免 discoverability 问题。 |
| 新增 @keyframes dash + slideInUp 到 globals.css | (1) DS 合规允许 keyframe 层新增；(2) 复用性高（其他页面可能也想用） | (1) `slideInUp` 与 `qds-fade-up` 语义相近（都是向上淡入），名字空间易混淆；(2) 新增 `dash` 是为单个 SVG 绘制动画，通用性存疑 | **可接受**。建议：`slideInUp` 改名 `qds-slide-up-emphasis`（遵循 `qds-*` 前缀约定），或直接把两个曲线的 `animation` 写成 inline style 用 framer-motion 的 `motion.path`（tech-design §1.3 已经否决，OK）。 |
| 列表 10 列 grid | (1) 视觉密度高；(2) 与 mock 一致 | (1) 10 列在 `<lg` 视口折叠到第二行的策略未细化（FR-002 只说"较次要列折叠"，但没说具体哪几列）；(2) `BacktestCopyableId` 组件占位 90px 固定宽是否能放得下长 run_id（UUID 36 字符）未验证 | **可接受**。建议 tech-design §4.1 列表表格中补充响应式 breakpoint 规则（lg/md/sm 各自隐藏哪几列）。 |

## 遗漏项

1. **缺少"失败重试"交互规格**：FR-032 说"重试逻辑：预填充当前策略/标的/区间进入 Create sheet"，但 tech-design 没说**哪些字段会被预填充、哪些保留默认**。预填充 `strategy_name / symbols / interval / start_date / end_date`？是否包含 `fill_model_type`、`maker_fee`、`paramOverrides`？S5 acceptance 对此模糊。建议：tech-design 增加 §8.3 "重试预填充字段清单" 表格。
2. **缺少 `BacktestRunRow.tsx` 里 `Memory/CPU` 两个占位字段删除的迁移规格**：现文件 237-243 行有两个 `—` 占位 cell。设计说改 `grid-cols-6` 移除，但 progressDetail 类型上是否有对应字段需删除？Grep `BacktestProgressDetail` 看看。Bash 没跑，但从 `BacktestListView.tsx:25` 位置不知晓 — 需 executor 自查。**Minor**。
3. **没有对 Create Sheet 的 "点开 Sheet → Sheet 外滚动列表" 的 scroll 行为做规格**：shadcn Sheet 默认会 lock `body` scroll，但 `page.tsx:83` 的 `overflow-hidden` + `overflow-y-auto` 内层滚动容器会冲突。需确认 Sheet mount 时列表滚动位置保持、关闭时不跳转。**Minor**。
4. **没有 loading / empty / error state 的统一规格**：Trades view 在 `tradeLog = []` 时显示什么？detail 在 selectedRun=null 时显示什么？Create Sheet 在策略列表加载中的状态？各 step 的 `<Skeleton>` 占位？tech-design 对 empty/loading state 缺失。**Minor**。
5. **没有对旧 `BacktestCreateView.tsx` 里 `data-form-section` 动画机制迁移的显式文案**：S6 只说"Sheet step 切换使用 key={step} + slideInUp"，但旧逻辑 `sectionsRef.current?.querySelectorAll("[data-form-section]")` 的 IntersectionObserver-like 行为不再存在。`BacktestSubscriptionTable.tsx:57` 这个唯一保留的 `data-form-section` 消费者会永久卡在 `opacity-0`。**Major → 已在 DAG 遗漏项里列出；务必在 S9 修复**。

## 上轮修改验证（如适用）

不适用（第 1 轮）。

## 修改要求（REVISE）

1. **[Major-1]** 在 4-tasks.md S11 的 acceptance 中显式增加 `grep "#E5534B" src/web/src/app/backtest/components/OverviewTab.tsx` = 0；同时 tech-design §3.2 把 OverviewTab.tsx 的预估变更量从 "~50 改 / ~40 删" 修正为覆盖 Equity + Drawdown 两整块 Recharts 的规模（约 80 删）。

2. **[Major-2]** 自定义周期规范收敛。推荐方案 A：tech-design §4.2 的 `timeframe` chip 集合扩充到后端 `TIMEFRAME_PRIORITY` 的 12 个全量值（`1m/3m/5m/15m/30m/1h/2h/4h/6h/8h/12h/1d`），砍掉"自定义"chip。若用户坚持要"自定义"，采用方案 C（白名单化校验 + 警告映射）。1-requirements.md FR-061 和 4-tasks.md S8 acceptance 随之更新。interview.md 的"功能合理化修正第 2 条"文案也需修订以反映后端的实际约束。

3. **[Major-3]** 消解 S4 与 S5 的 `BacktestRunRow.tsx` 并行写入冲突。推荐方案 A：task.json 把 S5 的 `depends_on` 从 `["S3"]` 改为 `["S3", "S4"]`，4-tasks.md parallel_groups 第 4 波把 S5 移到第 5 波（与 S9 并行；或第 6 波独立）。同时把 S4 对 `page.tsx` 的 `loadRuns` 透传职责合并到 S6。4-tasks.md S4 和 S5 的 files 列表需同步更新。

4. **[Major-Supplement]** 在 S9 的 files 和 acceptance 中补充：`BacktestSubscriptionTable` 在 Sheet 内嵌时，需要在 Sheet `useEffect` 中触发 `data-form-section` 的 `data-visible=true` 属性更新（或把 `FORM_SECTION_CLS` 替换为无动画版本）。否则表单字段永久 opacity-0。建议在 `backtestStyles.ts` 新增 `FORM_SECTION_STATIC_CLS`（不含 opacity/translate 预设），供 Sheet 内部使用。

5. **[Minor 合集]** 以下 minor 项建议一次性修订：
   - AC-C-3 的 grep 模式加正则 class 边界锚点（避免误命中 `bg-card` / shadcn `<Card>`）
   - 增加 S15 的 S11 直接依赖声明
   - S13 依赖改为 `[S11, S12]` 或挪至第 4 波
   - 删除 `OverviewGreyTab.tsx`（确认无引用后列入 S15 清理清单）
   - S9 拆为 S9a/S9b 或至少在 acceptance 中明确 5 个子块的独立检查点
   - tech-design §4.1 增加响应式 breakpoint 规格（lg/md/sm 隐藏列）
   - tech-design 新增 §8.3 "重试预填充字段清单"
   - 补充 Playwright E2E 任务 S16（或明确 AC-B 为人工走查）
   - 补充 Sheet 内 `<BacktestSubscriptionTable>` 横向不溢出的视觉断言
   - Equity SVG 如需保留 tooltip 交互，补充自绘 hit-box 实现规格（否则明确放弃 tooltip）

ReviewPass: architect
VERDICT: REVISE
