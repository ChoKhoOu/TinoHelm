# Critic Review — Round 1

**VERDICT: REVISE**

## 总体评估

规划文档整体结构完整，三份核心文档（需求 / 技术设计 / 任务清单）骨架扎实，访谈结晶准确承载了"视觉重构 · 功能保留"的意图，关键代码引用基本可验证（shadcn Sheet 存在、FILL_MODEL_OPTIONS 位置准确、API endpoints 全部可达），并且自觉遵守 DS 标准化后的禁区约定。然而存在 **3 项 CRITICAL 阻塞**（验收手动 item 嵌入、Trades 视图 MFE/MAE 数据来源无法落地、需求与设计在 subscriptions 归属上自相矛盾）和 **6 项 MAJOR 缺口**（硬编码 hex 清理未覆盖 OverviewGreyTab/OverviewTab/PerformanceHelpers、空状态/错误态/WS 断连处理缺失、响应式 degrade 方案未定义、重试预填充数据结构未指定、依赖关系存在真实漏配）。需要 planner 修订后进入第 2 轮。

## 预判 vs 实际

| 预判区域 | 是否命中 | 实际发现 |
|---|---|---|
| 验收项中混入"手动验证" | 命中 | 0 次硬违规，但有 4 处"视觉走查""手测"等同类 item |
| MFE/MAE 字段来源 | 命中 | `TradeLogEntry` 类型无 mfe/mae，FR-118 模糊引用"扩展字段或 —"但无 join 键 |
| 响应式降级缺失 | 命中 | FR-002 提到"`<lg` 折叠第二行"但无 breakpoint / 测试断言 |
| 空状态 / 错误态 / WS 断连 | 命中 | 完全未覆盖 |
| DS 扫描遗漏现存违规 | 命中 | OverviewTab:207-219 `#E5534B`、PerformanceHelpers:170 `rgba(239,83,80)`、OverviewGreyTab:103/202 同样违规未纳入 S11/S13 验收 |

5 项预判全部命中，说明文档确实有系统性缺口，需要升级为 ADVERSARIAL 模式进一步审查。

## Critical 发现（阻塞执行）

### C1 — Trades 视图 10 列表格的 MFE/MAE 无可靠数据路径
- **证据**：
  - `types.ts:61-71` `TradeLogEntry` 仅有 `opened_at/closed_at/instrument/side/quantity/avg_open/avg_close/realized_pnl/duration` 9 个字段，**无 mfe/mae**。
  - `types.ts:22-27` `MaeMfePoint` 仅含 `{pnl, mae, mfe, side}` 四字段，**无 trade id / timestamp**，无法和 `TradeLogEntry[]` 做行级 join。
  - 需求 FR-118：`"派生 side=BUY→long / SELL→short，MFE/MAE 从扩展字段读取或显示 —"` — 说"扩展字段"但类型定义上没有此字段。
  - 设计 §4.6 Trades View 10 列表格明确列出 MFE 和 MAE 两列。
  - 任务 S14 acceptance 无 MFE/MAE 数据断言。
- **置信度**：HIGH
- **影响**：executor 在 S14 实施时会发现无法按 trade 粒度显示 MFE/MAE，要么留空（用户投诉"功能退化"，违反 AC-B），要么临时改 backend（违反 Out-of-scope §"不改 API 契约"）。
- **修复**：三选一，规划文档必须明确选哪一种：
  1. **最小改动**：在 tech-design §4.6 / FR-116 显式声明"MFE/MAE 两列固定渲染 `—` 占位，配 HelpTip 说明'即将支持'"，并在 S14 acceptance 写明。
  2. **数据改造**：由 tech-design 和 task 增加新的 subtask "扩展 TradeLogEntry 类型 + 后端 `/api/backtest/{id}/result` 在 trade_log 中附带 mfe/mae"（但这违反 NFR-5 零后端改动）。
  3. **降列**：列表从 10 列降为 8 列，删除 MFE/MAE 列，修正 interview.md 验收标准。
- 必须在 FR-116、AC-B-5、S14 三处同步修订。

### C2 — 需求和设计对"subscriptions 归属哪个 Step"矛盾
- **证据**：
  - 需求 FR-052：`"granularity 默认 bar，可在 Step 2 调整为 tick（保留现有 BacktestSubscriptionTable 逻辑，但 UI 拆分到 step 2）"` — 说 subscription table 在 Step 2。
  - 需求 FR-073：`"Subscriptions 列表（保留现有 BacktestSubscriptionTable 组件，整合进折叠区）"` — 说 subscription table 在 Step 3 折叠区。
  - 设计 §4 表格：Step 1 标的模式 `"granularity 默认 bar，可在 Step 2 调整为 tick"` vs Step 3 `"Subscriptions 详情 — 折叠区（复用 BacktestSubscriptionTable）"` — 设计明确放 Step 3。
  - 任务 S7 acceptance：`"选策略后 subscriptions 状态被策略 defaults 填充"`、S8 acceptance：`"点击任一快捷 chip → 所有 subscriptions[i].timeframe 被更新"`、S9 acceptance：`"9 种 fill model 均可下拉选中"` — 三个 step 都在操作 subscriptions，但只有 S9 明说 UI 渲染 BacktestSubscriptionTable。
- **置信度**：HIGH
- **影响**：executor 跨 3 个并行 agent（S7、S8、S9）时会产生 3 份对"subscription 归属"的不同理解，最终导致代码冲突或 granularity/dataType 字段失踪。
- **修复**：在需求和设计中统一：**subscriptions 字段数组在 state 层贯穿 3 步，但 subscription 详情表 UI 只在 Step 3 折叠区渲染一次**。具体：
  - FR-052 改写："标的搜索产生的 symbols 自动写入 subscriptions 基础条目；granularity 默认 bar。用户若需调整 granularity 或 dataType，需展开 Step 3 的'高级选项'折叠区使用 BacktestSubscriptionTable。"
  - FR-073 保持不变。
  - 设计 §4 Step 1 表格最后一行去掉 `"granularity ... 可在 Step 2 调整为 tick"`，改为 `"granularity 默认 bar；granularity/dataType 调整入口位于 Step 3 高级选项折叠区"`。

### C3 — 验收标准混入"视觉走查""手测"等效手动验证项
- **证据**：
  - S3 acceptance：`"视觉走查：done/running/failed/queued 四种 row 渲染不断裂"` — 视觉走查 ≡ 人工验证。
  - S8 acceptance：`"estimate API 调用在 300ms 内 debounce 正确（手测）"` — 显式标注"手测"。
  - S13 acceptance：`"卡片 header 视觉统一（字号、color、padding 与 Overview 一致）"` — 无机器判据。
  - NFR-4：`"dark 和 light 两主题均通过视觉走查"` — 视觉走查等同手动。
  - 用户 RULE（项目 CLAUDE.md）："MUST 在提交 PR 或者 issue 时，验证或测试的内容不应该出现需手动(manual)验证相关的 item"。
- **置信度**：HIGH
- **影响**：此为用户硬性红线，PR 会被直接打回；并且 architect verdict 会因合规问题被卡。
- **修复**：
  - S3 "视觉走查" → 改为"Playwright 截图快照对比 baseline 哈希 / 或通过 `querySelector` 断言 ACCENT_BG_MAP 四种 class 在对应 row 中存在"。
  - S8 "手测" → 改为 `jest/vitest fake timers 模拟 debounce，断言在 t=300ms 处仅触发 1 次 /api/backtest/estimate`。
  - S13 "视觉统一" → 改为"grep 断言 6 个 tab 文件均使用 `qds-section-label` 或 `<SectionLabel>`，且 card header 使用 `CARD_HEADER_CLS` 统一常量（不出现 ad-hoc 类名）"。
  - NFR-4 "dark 和 light 两主题均通过视觉走查" → 改为"`verify-ds-compliance.sh --mode both-themes` exit 0（此判据已在 AC-C-2 中存在，NFR-4 的'视觉走查'措辞直接删除即可）"。

## Major 发现（导致显著返工）

### M1 — OverviewTab 现存 hex 违规未被任何 subtask 覆盖
- **证据**：
  - `OverviewTab.tsx:207-208` `<stop stopColor="#E5534B" ...>`（Drawdown gradient）。
  - `OverviewTab.tsx:219` `<Area ... stroke="#E5534B" />`（Drawdown stroke）。
  - `OverviewGreyTab.tsx:103`, `202` `rgba(239, 83, 80, ...)`（现存 drawdown shadow / fallback color）。
  - `PerformanceHelpers.tsx:170` `rgba(239, 83, 80, 0.5)`。
  - S11 acceptance 仅要求 `OverviewEquitySvg.tsx` 零 hex，**未覆盖**旧 `OverviewTab.tsx` 中被删除的 drawdown 段代码是否彻底清理干净。
  - S11 描述说"在 OverviewTab.tsx 中删除现有 equity + drawdown 的 Recharts 双列 JSX（`~line 151-226`），替换为..."，这段替换过程会移除 `#E5534B`，但 **OverviewGreyTab.tsx 和 PerformanceHelpers.tsx 完全没在任何 subtask 的 files 里**。
  - AC-C-4 `grep "#E5534B|rgba(76, 175, 80|rgba(239, 83, 80) src/web/src/app/backtest/` 零命中 — 这条会在 S11 后仍然违规（因 OverviewGreyTab + PerformanceHelpers 未被清理）。
- **修复**：
  - 在 S13 的 files 列表显式加入 `OverviewGreyTab.tsx` 和 `PerformanceHelpers.tsx`；acceptance 补充"所有 rgba(239,83,80) / rgba(76,175,80) / #E5534B 替换为 token"。
  - 或在 S11 中追加清理 `OverviewGreyTab.tsx`（若该文件属于"被遗弃的旧版 Overview"，tech-design §3 应明确其存废状态 — 当前未提及）。

### M2 — 空状态 / 错误态 / WS 断连完全未覆盖
- **证据**：需求文档 §3 无空状态 FR；§4 NFR 无错误态处理；tech-design 无错误态组件；tasks 无错误态 acceptance。现有 BacktestListView.tsx:150-160 已有 EmptyState 处理"无回测记录"，但重构后是否保留未在任何文档提及。
- **影响**：
  - 列表空数据 → 重构后是否保留 EmptyState？
  - 详情页 `/api/backtest/{id}/result` 500 错误时 KPI 网格渲染什么？（现有 OverviewTab:105-113 有错误态，但新 KPI 网格 FR-081 无降级说明）
  - Trades 视图过滤 + 搜索后 0 行时显示什么？
  - WS 断连时列表 running 行进度 stuck 在最后一帧 — 需否显示"连接降级"提示？（现有 WS provider 会触发 connection-degraded toast，但 running 行本身无视觉提示）
  - 详情未开放的 running 状态下 6 列 KPI 显示 `—`（FR-080/FR-081 提到），但 failed 状态下 KPI 和 SVG 渲染什么未定义。
- **修复**：
  - 在 §3 新增 FR-119 / FR-120 / FR-121 分别定义列表空态、详情加载失败、Trades 空过滤结果三种情况的组件和文案。
  - 在 S10 acceptance 补充"running/queued/failed 状态下 KPI 列显示 `—`"。
  - 在 S11 acceptance 补充"equity_curve 为空或加载失败时 OverviewEquitySvg 优雅降级为 `<InlineError>` 或占位 SectionLabel + `—`"。
  - 在 S14 acceptance 补充"过滤 + 搜索后 0 行时渲染 EmptyState（"无匹配交易"）"。

### M3 — 响应式 degrade 策略不完整
- **证据**：
  - FR-002 `"其中较次要列在 <lg 视口折叠为第二行内联元数据"` — 仅此一句。
  - 10 列 grid `grid-cols-[3px_minmax(200px,2.5fr)_90px_minmax(180px,1.5fr)_120px_minmax(120px,1fr)_80px_80px_100px_110px]`（设计 §4.1）合计最小宽度约 `3+200+90+180+120+120+80+80+100+110 = 1083px`。
  - Tailwind `lg` breakpoint 是 1024px。在 1024 ≤ w < 1083 时，grid 会强制出现横向溢出或内容压缩。
  - Sheet 宽度 520px，父视口 1024px 时，列表区只剩 504px，但列表最小 1083px — 会有 2×溢出。
  - 无 acceptance 断言任何响应式行为。
- **影响**：窄屏（笔记本 1280/1440）用户会看到横向滚动条或被截断的列。
- **修复**：
  - FR-002 展开："`<xl` (1280px)：每行折叠为两行显示（第一行主要信息 / 第二行次要指标）；`<md` (768px)：Zone 切换为列表而非表格（每 run 一张卡片）。"
  - S3 acceptance 新增"视口 1024 / 1280 / 1920 三档截图对比断言或 DOM 断言（`grid-template-columns` 不同）"。
  - Sheet 在 `<sm` (640px) 下 `sm:max-w-[520px]` 会失效 — 需补充 `w-full` 保证全屏抽屉。

### M4 — Failed 行重试预填充的数据结构未定义
- **证据**：
  - S5 描述：`"onClick → 调用新增 onRetry(run) 回调"`；page.tsx `"在 onRetry 中调用 handleRetry(run) → 打开 create sheet 并预填充"`。
  - 设计 §7.4 仅说 `"onRetryRun?: (run: BacktestRunSummary) => void（预填充 create sheet）"` — 无预填充字段清单。
  - `BacktestRunSummary` 不含 `initial_capital / maker_fee / taker_fee / fill_model / warmup_bars / tags / paramOverrides` 这些 create form 必填字段。
  - 重试若仅预填 strategy/symbol/range，其他字段重置为默认值，用户会感到"为什么我之前的设置丢了"。
- **置信度**：MEDIUM
- **影响**：S5 与 S6/S7/S8/S9 有数据接口耦合，但三者定义在不同 agent。若不指定重试预填字段集合，executor 会实现不一致。
- **修复**：tech-design §7.4 增加"重试预填字段清单"表：
  - 可预填（来自 BacktestRunSummary）：`strategy_name / symbol (→ symbols[]) / interval / start_date / end_date`
  - 不可预填（需重新输入或采用当前默认）：`initial_capital / maker_fee / taker_fee / fill_model_type / warmup_bars / tags / paramOverrides / latency_*`
  - 文案提示："已复制策略与标的，其他参数请确认后提交"（InlineError 或 SectionLabel）
  - 或通过 `POST /api/backtest/run` 的 payload 再请求 `GET /api/backtest/{id}/result.params` — 但这需要后端补充 endpoint，违反 NFR-5。

### M5 — 依赖图存在真实漏配：S13 应依赖 S11 而非 S10；S12 的"查看所有交易"按钮依赖 S14 未标注
- **证据**：
  - task.json S13 `depends_on: ["S10"]`，但 S13 描述为"扫描 6 个 tab 的卡片 header + section label 使用情况"。6 个 tab 里的 **Overview 是 S11+S12 改造的，不是 S10**；S10 只改 KPI 网格（在 tab bar 之前）。S13 要统一 Overview 风格需要先有 S11/S12 完成的 SectionLabel 使用方式作为基准，否则 S13 可能先完成、结果 S12 之后又改 Overview 的 section label，导致 S13 白改。
  - S12 acceptance 提到"Overview 存在'查看所有交易'按钮，点击触发 `onViewAllTrades` 回调"。该按钮需要与 S14（实现 `handleViewAllTrades` + view="trades" 分支）对接。S12 `depends_on: ["S2", "S11"]` 未写 S14 — 但若 S12 先完成、S14 后完成，则 S12 的 onViewAllTrades 回调会临时 no-op，整体无错但 E2E 测试用例失败。**可接受但需 S15 验证**。
  - S15 `depends_on` 列了 S12 和 S14，但未明确 S15 需 re-run S12 验收（S12 原 acceptance 按 "回调触发" 验证，S14 完成后应追加 "真的跳转到 trades 视图" 的集成验收）。
- **修复**：
  - S13 `depends_on` 改为 `["S10", "S11", "S12"]`。
  - S15 acceptance 增加"S12 + S14 的跨视图跳转 E2E：点击"查看所有交易"→ 进入 trades 视图 → 点击"返回"→ 回到 detail overview tab"。

### M6 — S15 "清理 BacktestCreateView.tsx" 隐含依赖未处理；归属不明的 subscriptions 共享 state
- **证据**：
  - S15 acceptance：`"ls src/web/src/app/backtest/components/BacktestCreateView.tsx 返回 no such file"`。
  - 现有 `BacktestCreateView.tsx` 包含 FILL_MODEL_OPTIONS 常量（49-59 行）和 parseTimeframe 工具函数（61-69 行）。
  - tech-design §4.2 Step 3 字段表脚注："保持从 `BacktestCreateView.tsx:49-59` 迁移而来的 9 元素常量数组，迁移目的地：`BacktestCreateStep3.tsx` 模块顶部或 `backtestStyles.ts`。"
  - 但 parseTimeframe 函数 Step 2 需要（校验自定义 `{n}{unit}`），task S8 未明确说从哪里搬。
  - BacktestCreateView 的 `form / subscriptions / paramOverrides` state 是跨 Step 共享的（Step1 设 strategy → trigger subscription defaults → Step3 读 subscriptions）— tech-design §4.2 说 "Sheet 内部 state"，但未画清楚 `<BacktestCreateSheet>` 持有 state 还是每个 Step 持有。S7/S8/S9 三个 executor 若理解不一致，会出现 state 割裂。
- **修复**：
  - tech-design §4.2 明确：**所有跨步共享 state 由 `BacktestCreateSheet` 持有并通过 props 下传给 Step1/2/3**（标准 controlled component 模式）。
  - S6 acceptance 增加"所有 form/subscriptions/paramOverrides/strategyParams state 由 BacktestCreateSheet 持有，Step 1/2/3 通过 props 读取和回调修改"。
  - S8 files 明确"从 BacktestCreateView.tsx:61-69 搬迁 parseTimeframe"。
  - S15 增加前置检查"FILL_MODEL_OPTIONS 和 parseTimeframe 已搬迁到新位置（grep 新位置 ≥1 命中）"。

## Minor 发现（次优但可工作）

### Mi1 — 动画 keyframe 名称碰撞风险
- 现有 `globals.css:295` 已有 `@keyframes fade-up`（8px 偏移），还有 `qds-fade-up`（同 8px）。新增 `slideInUp`（12px 偏移）。命名冲突问题不严重，但 tech-design §6 注释"为什么不复用 qds-fade-up"说明充分；不过 `@keyframes fade-up` 和 `qds-fade-up` 本身就是潜在冗余，未来可能再引起混乱。建议 tech-design §6 脚注"新增 slideInUp 时顺便评估是否废弃重复的 fade-up（若无使用）"。

### Mi2 — 6 列 KPI grid 的 `data-kpi-cell` 属性仅用于测试
- FR/AC 未说明 `data-kpi-cell` 是测试 only 还是产品上保留。若仅测试用，考虑用 `data-testid="kpi-cell"` 更符合约定。

### Mi3 — Stepper 在移动端 (窄 Sheet) 下标签会挤压
- tech-design §4.2 stepper 使用 `flex-1 h-px` connector + 文字标签。520px 内 3 个 label + 2 个 connector 会挤压；尤其中文标签"策略 & 标的""时间 & 周期""资金 & 成本"一共 4-5 字符 + emoji 间距。建议 Mi - 在 `<sm` 下只显示圆点不显示文字。

### Mi4 — ShimmerBar 的 `active` 属性在 JSX 简写中的行为
- tech-design §4.1 示例 `<ShimmerBar progress={pct} height="md" active variant="accent" />`。JSX `active` 简写等价 `active={true}`，ShimmerBar props default active=true，语义上一致。但 Reader 可能误以为 `active` 是 status 枚举值。建议在文档示例改为 `active={true}` 提高可读性。

### Mi5 — 详情 tab 标签文案不一致
- 设计 §2 架构图列出 "7 tab" 为 Overview / Performance / Trades / Robustness / Tearsheet / TradeLog / Reports。
- BacktestDetailView.tsx:15-23 实际 tab label：`tearsheet: "Report"`、`reports: "Data Tables"`。
- FR-082：`"7 个 tab：Overview / Performance / Trades / Robustness / Tearsheet / Trade Log / Data Tables"` — label 对，但 key 叫 `tearsheet / reports` 混淆。
- interview.md `TabKey: overview/performance/trades/robustness/tearsheet/tradelog/reports/datatables` — 居然列出 8 项，多写了 "datatables"（实际 reports 就是 data tables）。
- 不影响执行但文档前后不一致，建议 planner 统一。

## 缺失项

1. **键盘快捷键 `⌘K` 是否真实绑定**：FR-115 提到 "kbd hint 显示 `⌘K`"，但无任何 FR/AC 定义 `⌘K` 真正触发搜索聚焦的行为。若仅是装饰，用户按 ⌘K 无响应会被投诉虚假暗示。建议 FR-115 补充"按下 ⌘K 时焦点跳到搜索框"或明示"仅为视觉提示，暂未绑定快捷键"。
2. **Trades 视图 CSV 导出**：interview.md 未提 CSV 导出；但现有量化工作流用户通常期望 trades 视图可导出。规划文档无提及 = 默认不做。建议 planner 在 Out-of-scope 明列"CSV / Excel 导出非本次范围"。
3. **URL 路由 / 深链**：新增 trades view 仅存活于 React state，用户刷新页面丢失状态。interview.md 和设计均未讨论是否需要 `?view=trades&run=xxx` 深链。建议 planner 显式选择：维持 state-only（快速）or 接入 next/navigation（未来可分享 URL）。
4. **打开 Sheet 时 list 区的可视行为**：Sheet side=right 520px 宽出现时，列表是被覆盖还是收缩？现有 shadcn Sheet 默认是 overlay（黑色半透明遮罩 + absolute sheet），用户点 sheet 外关闭。Tech-design §4.2 未说。建议明示 "overlay 模式，点击外部不关闭（避免误触丢失表单）"。
5. **Previous 按钮是否保留已填写的字段**：FR-043 定义 previous/next 但未说 state 保持策略。隐含应保留，但需明示。
6. **Submit 失败后的状态**：FR-075 说 "成功后关闭 sheet"，未说失败后行为。推测依赖 useAction 的 inline error，但未明示 `<InlineError>` 位置（sheet footer? 还是 submit 按钮下方？）。
7. **Monthly heatmap 空数据**：现有 `OverviewMonthlyHeatmap.tsx:19` `if (years.length === 0) return null;`。新设计保留这行，但"整行消失"会让布局跳动（1.4fr/1fr 双列布局变单列）。建议 tech-design §4.5 补充"空数据时渲染 `<InlineError variant="hint">本区间无月度数据</InlineError>` 占位而非 null"。

## 歧义风险

### A1 — `"UI 拆分到 step 2"` vs `"整合进折叠区"`
- 已归入 C2，不重复。

### A2 — `"10 列视觉密度"` vs Sharpe/Win Rate/PnL 需要完成态数据
- FR-002 说 "10 列 grid"，但第 7/8/9 列 Sharpe/WinRate/PnL 只有 `isDone` 时有意义。running/queued/failed 行时这 3 列显示什么？
- 解读 A：显 `—` 占位，每行统一 10 列宽度。
- 解读 B：running 行该 3 列合并为"进度/结果"显示，即不对齐 done 行的列结构。
- 选错的风险：A 方案 running 行多出 3 列空白，视觉空荡；B 方案 running 和 done 行列宽不一致，破坏"10 列 grid"一致性承诺。
- **建议明示**：采用 A，3 列显 `—`，走统一 grid。

### A3 — `"sheet 宽度 450~520px"` vs `"sm:max-w-[520px]"`
- FR-040 说 "450~520px"，设计 §4.2 代码示例用 `sm:max-w-[520px]` 固定 520。AC-A-6 断言 `max-width ≥ 450px 且 ≤ 520px` — 最大值 520 可达，但最小值 450 在代码实现上没有保证（`max-w-[520px]` 是上限，不是下限）。
- **选错的风险**：AC-A-6 会通过（因为 max-width=520 落在区间内），但"450~520px"的意图若是自适应（窄屏往 450 缩），当前代码不支持。
- **建议明示**：FR-040 改为 "宽度 `min(100vw, 520px)`，即 sheet 在 `≥sm` 视口固定 520px，更窄时全屏"。

### A4 — `"保留现有 qds-section-label 类"` vs `"使用 <SectionLabel> 组件"`
- FR-094 "卡片头样式统一使用 qds-section-label"；NFR-2 "强制使用 QDS 业务组件：... <SectionLabel> 或 qds-section-label class"（两种都可）。
- 选错的风险：class 方式和组件方式样式可能略有差异；部分 tab 用组件、部分用 class，不统一。
- 建议统一要求 `<SectionLabel>` 组件（QDS 方向），降级的 class 形式只在无法用组件的极少场景。

## 假设分析

| 假设 | 级别 | 说明 |
|---|---|---|
| `/api/data/symbols` 返回 `BinanceSymbol[]` 可用于 Step1 搜索 | VERIFIED | `BacktestSubscriptionTable.tsx:47` 和 `FetchDialog.tsx:104` 均已调用 |
| `/api/strategies/{name}/defaults` 返回 subscriptions 默认值 | VERIFIED | `strategy.py:260` 定义了 `@router.get("/{name}/defaults")` |
| `color-mix(in srgb, ...)` 浏览器支持 | VERIFIED | tech-design §4.5 引用的 Chromium 111+/Safari 16.2+/Firefox 113+ 准确 |
| shadcn Sheet 组件已装 | VERIFIED | `src/web/src/components/ui/sheet.tsx` 存在 |
| ShimmerBar 接受 `variant="accent" / height="md" / active` 属性 | VERIFIED | `shimmer-bar.tsx:3-8` 定义齐全 |
| `TradeLogEntry` 含 mfe/mae 字段 | FRAGILE | **不含**，见 C1 |
| `BacktestRunSummary` 含 `calmar_ratio` | VERIFIED | `BacktestListView.tsx:32` 定义 |
| WS `backtest.progress` 事件 payload flat JSON | REASONABLE | memory `feedback-ws-events.md` 确认 flat JSON + `(msg.data ?? msg)` 兜底，未 double-check 新代码 |
| `OverviewTab.tsx:~line 151-226` 包含要删除的 Recharts JSX | VERIFIED | 实际 151-226 就是 equity+drawdown 两列 AreaChart |
| 10 列 grid 能在 `lg` 视口（1024px）内水平容纳 | FRAGILE | 最小宽度约 1083px，超出 1024 — 见 M3 |
| 重构后 `OverviewTab.tsx` 清除所有 hex | FRAGILE | 仅 equity/drawdown 段被替换，其他 hex 若存在于其他区块未被覆盖 |
| `BacktestCreateView.tsx` 中所有 state 不需迁移只重构 | FRAGILE | state 形态（form/subscriptions/strategyParams/paramOverrides/paramsExpanded）要跨 Step 传递，需要明确 lift 到 Sheet — 见 M6 |
| `verify-ds-compliance.sh --selftest` 存在且能验证新 keyframes | VERIFIED | 脚本第 136-458 行有 selftest 实现 |
| 10 列表格在 100 行数据 + 5s WS 推送下不卡顿 | REASONABLE | 非 virtualized，100 行普通渲染能承受，但 expanded running 行若含 ShimmerBar + RingProgress 每秒多次 rerender 可能触发 16ms 预算 |
| `OverviewGreyTab.tsx` 属于将被废弃的旧版 | FRAGILE | 设计 §3.4 未列入保留清单也未列入删除清单，executor 不知道该动不该动 |

## 预验尸

| 失败场景 | 文档是否应对 | 说明 |
|---|---|---|
| `/api/backtest/{id}/result` 返回 500 | No | OverviewTab 有错误态，但新增 6 列 KPI 和 SVG 未定义错误态 |
| Trades 视图过滤 + 搜索后 0 行匹配 | No | 未定义 EmptyState |
| WS 断连 10 秒未重连，running 行进度 stuck | No | 未定义视觉降级 |
| 用户在 Sheet Step3 输入无效 fill model 参数导致 submit 失败 | Partial | 提 `useAction` + `<InlineError>`（FR-075、FR-044）但未说 submit-level 错误如何显示 |
| 窄屏 1280px 用户打开 Sheet 后列表区被压缩至 760px | No | 未定义响应式 degrade |
| 用户重试 failed run 时遗漏 `fill_model/tags` 等配置 | No | 未定义重试预填字段清单，见 M4 |
| executor 并行写 S12 和 S13 时 OverviewTab.tsx 冲突 | Partial | tasks.md §并行波次 注解"每个 agent 严格编辑自己负责的 JSX 片段"，但 OverviewTab.tsx 在 S11/S12/S13 三个 subtask 都可能被编辑（S13 描述 "扫描 6 个 tab ... 统一样式"） |
| S3 先完成 10 列 grid，S2 在 S3 之后更新 STATUS_PILL_MAP | No | 依赖图 S3→S2 反了吗？实际是 S2→S3（S3 dep S2），但 S3 描述"替换 status pill 内联实现为 `<StatusBadge>`"意味着删除 STATUS_PILL_MAP 使用点 — S2 新增 STEPPER_DOT_CLS_MAP 和 S3 删除 STATUS_PILL_MAP 都改 backtestStyles.ts，有 merge 风险 |
| 两个并行 executor 都新增 2 个 keyframe 到 globals.css 的不同位置 | Yes | S1 是独立 wave 1，其他 subtask 不应 touch globals.css；AC-C-6 的 grep 断言能捕获重复 |
| 新 `OverviewEquitySvg.tsx` 的 `strokeDasharray="3000"` 在 1000 宽 viewBox 下未必足够长 | Partial | 路径实际长度取决于 200 点的折线总长；单条折线最长 ~1000+drawdown 的 y 跨度 ≈ 1100-1500，3000 足够，但未做 path.getTotalLength() 精确计算。Executor 可能按 tech-design 原样写死 3000 导致动画时间不一致或某些曲线走完前已结束 |
| dash 动画在 prefers-reduced-motion 下仍强制播放 | No | NFR-3 可访问性未提 reduced-motion fallback |

## 多视角笔记

### Executor 视角（我能只凭写的内容完成每个步骤吗？）
- **S1**：OK，简单追加 keyframes。
- **S2**：含糊 — "在 BacktestRunRow.tsx 内嵌 或 抽到独立文件（择其简洁）" → 两个 executor 会选不同，上游 S4 引用路径会飞。建议 planner 敲定一种（推荐独立文件）。
- **S3**：10 列 grid-cols 具体值在 tech-design §4.1 给出，可用。但 S3 acceptance "视觉走查" 无客观判据（见 C3）。
- **S4**：OK，但依赖 RingProgress 路径未定。
- **S5**：onRetry 回调的预填规则未定（M4）。
- **S6**：state ownership 不清（M6）。
- **S7/S8/S9**：跨步骤 state 共享规则不清。
- **S11**：OverviewTab 删除代码的起始/结束行号 `~line 151-226` 需精确到 JSX 节点边界，否则会误删 SectionLabel 或误删 Monthly heatmap 起始 JSX。
- **S12**：现有 DrawdownTable 组件是否支持 `top-N prop` — files 列表写"若需改"，executor 需先读代码判断，增加 20% 时间。
- **S14**：MFE/MAE 字段数据源未定（C1）。
- **S15**：清单清晰但"全部验收标准 AC-A ~ AC-E 通过"需 E2E 环境和 mock 数据，执行成本被低估。

### Stakeholder 视角（这真的解决了原始问题吗？）
- **原始意图**：视觉重构 · 功能保留（interview.md）。✓ 基本达成。
- **成功标准**：像素级对齐 + 功能不退化 + 动效全量履约 + DS 合规。**不退化定义不严格**（Robustness MC 锥形图、Tearsheet PDF、TradeLog 筛选都是"保持现有"，但没对"现有能跑"的基线做截图或回归测试固化）。若重构过程中 S13 误触碰到 `RobustnessTab.tsx` 导致 MC 锥形图渲染异常，"不退化"无客观判据。
- **范围适中吗**：15 subtasks、25h 串行 / 13h 并行，对一个"单页重构"偏大；但考虑 4 视图 × 6+1 tab × 3 step sheet，工时合理。
- **虚荣指标**：无明显。验收均是具体 DOM / API / script 判据。

### Skeptic 视角（最强的反对论点是什么？）
- **反方 1 — SVG 自绘 vs Recharts 取舍**：Recharts 已经覆盖 90% 的 AreaChart 需求，换成自绘 SVG 只为了"strokeDasharray 绘制动画"。tech-design §1.3 承认 Recharts 不支持 strokeDashoffset，但**替代方案**：用 Recharts 的 `animationBegin` + `animationDuration` 实现渐入效果，视觉上"足够接近绘制动画"。自绘 SVG 的维护成本（tooltip、crosshair、hover、zoom、resize 等后期需求）将远高于 Recharts。规划选择"视觉保真 > 长期维护" — 合理但值得在 tech-design §1.3 列出"未来若需 tooltip/hover 等交互，需从 SVG 回退到 Recharts 或手动实现 ~150 LOC"。
- **反方 2 — Stepper 3 步 vs 原 5 分节**：mock 是 3 步，现有代码是 5 分节，把 5 个 section 压到 3 步中第 3 步"折叠区"承载 4-5 个字段 = 重新引入"展开 → 隐藏字段"的信息架构反模式。用户可能抱怨"之前一屏能看到所有参数，现在要展开才能看 fill model"。规划选择优先还原 mock，但未讨论 A/B 实验或用户反馈收集。
- **反方 3 — 零新增 class 承诺过严**：`globals.css` 强制零新增非 keyframe class。但 `qds-section-label` / `qds-card` / `qds-input` 是 QDS 业务类 — 若未来需要一个"stepper dot" 级别的 QDS 业务类，当前规则会阻碍。规划应允许"新增 qds-* 前缀的业务组件相关类"例外（通过修改 components/qds/ 组件源码即可免触 globals.css）。

## 上轮修改验证（如适用）

N/A — 这是第 1 轮审查，无上轮历史。

## 修改要求（REVISE/REJECT 时必填）

按阻塞优先级排序：

1. **【阻塞】解决 C1 Trades 视图 MFE/MAE 字段来源**：planner 必须在 FR-116、AC-B-5、S14 三处明示采取何种方案（占位 `—` / 降列为 8 / 后端改造）。期望结果：executor 阅读 S14 后对 MFE/MAE 列的期望值有唯一解读。
2. **【阻塞】解决 C2 subscriptions 归属矛盾**：planner 修订 FR-052 + tech-design §4 Step 1 表格，统一为"subscription 详情表仅在 Step 3 折叠区渲染，Step 1/2 只操作 state"。期望结果：S7/S8/S9 三个并行 executor 对 subscription UI 归属形成一致理解。
3. **【阻塞】解决 C3 验收标准中的手动 item**：修改 S3 / S8 / S13 / NFR-4 中的"视觉走查""手测""视觉统一"措辞为机器判据（DOM 断言 / grep / fake timer / 脚本 exit code）。期望结果：所有 subtask 的 acceptance 满足用户 RULE "不得包含手动验证项"。
4. **【重要】M1 硬编码 hex 清理覆盖**：S11 或 S13 的 files 和 acceptance 必须显式覆盖 `OverviewGreyTab.tsx` 和 `PerformanceHelpers.tsx`；或在 tech-design §3 显式声明 `OverviewGreyTab.tsx` 存废状态。
5. **【重要】M2 空态 / 错误态 / WS 断连**：新增 FR-119~FR-121 覆盖列表空态、详情加载失败、Trades 过滤 0 行；补充 S10/S11/S14 对应 acceptance。
6. **【重要】M3 响应式 degrade 方案**：FR-002 展开 breakpoint 规则；S3 acceptance 增加多视口断言；Sheet 补 `w-full` 保证 `<sm` 全屏。
7. **【重要】M4 重试预填字段清单**：tech-design §7.4 增加可/不可预填字段表 + 用户提示文案。
8. **【重要】M5 依赖图修正**：S13 依赖改为 `["S10", "S11", "S12"]`；S15 acceptance 增加跨视图 E2E。
9. **【重要】M6 state ownership 明示**：tech-design §4.2 明确 BacktestCreateSheet 作为 state owner 的模式；S6 acceptance 加入 state 位置断言；S8 files 补 parseTimeframe 搬迁源。
10. **【建议】修复 Mi1-Mi5 和 7 项"缺失项"**：planner 逐一决策并补充到文档相应位置。
11. **【建议】A1-A4 歧义消除**：按本报告建议逐一明示。
12. **【建议】预验尸的 10 个失败场景逐一检查文档是否应对**。

## 判决理由

**VERDICT: REVISE**

给出 REVISE 而非 REJECT 的理由：文档骨架完整、代码引用准确（95%+ 可验证）、访谈结晶质量高、tasks 粒度和并行化合理。3 项 CRITICAL 发现都是"文档未决"而非"架构错误"，planner 补充具体字段即可解决，不需推倒重来。

给出 REVISE 而非 APPROVE 的理由：3 项 CRITICAL 直接违反用户硬规则（C3）或将导致 executor 阶段冲突（C1 C2），不能放行。M1-M6 六项 MAJOR 合计会在执行阶段累积 3-5 小时隐藏返工（测试环境搭建 / 跨 agent 重做 / 硬编码扫描失败回修），风险超过重新进入第 2 轮 planner（预计 0.5-1 小时）。

本轮未升级为 ADVERSARIAL 模式。发现 3 CRITICAL + 6 MAJOR 符合"3+ MAJOR" 触发条件，但核心论断不依赖 ADVERSARIAL 的"对一切有罪推定"风格 — CRITICAL 证据链均是独立可复现的代码事实或需求与设计自身矛盾（非推测），MAJOR 多为"缺口识别"而非"推测架构错误"，THOROUGH 模式足以。

## Open Questions（未评分）

- **Op1**：设计 §3.4 列出"保留不动文件"清单未含 `OverviewGreyTab.tsx`，其存废需要规划者明确回答（是否为旧版 deprecated？是否从 git 删除？）。不属 Critical 因不阻塞主路径，但将影响 M1 修复方式。
- **Op2**：interview.md 的 TabKey 列出 8 项（含 `datatables`），但 BacktestDetailView.tsx 只有 7 个 tab（reports=Data Tables）。planner 确认是 7 个还是 8 个，顺便统一 label。
- **Op3**：Sheet 是否在 `Escape` 或点击外部遮罩时关闭？shadcn 默认 yes，但用户有未保存 form 时突然关闭可能损失工作。是否需要 `<AlertDialog>` 确认？推测架构层面可后续补充，非本轮阻塞。
- **Op4**：tasks.md 估算 "并行后实际 13h"，但实际 executor 是 5-agent 一波，波 4 有 6 个 subtask → 可能触发 "超过 5 个并发限制" 需降为两批。若 Cage 有并发限制，工时需重估。推测规划未考虑 Cage 框架的 parallel_groups 运行时限制，建议 planner 与 autopilot 策略对齐。
- **Op5**：interview.md 提到"第 4 轮隐含此方向"（指 memory 文件废止），但 review 中并无此声明。推测是另一任务残留，不影响本次审查。

ReviewPass: critic
VERDICT: REVISE
