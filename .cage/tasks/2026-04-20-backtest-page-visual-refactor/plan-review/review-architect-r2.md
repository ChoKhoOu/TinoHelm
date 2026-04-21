# Architect Review — Round 2

**VERDICT: APPROVE**

## 摘要

Planner 已系统化解决 r1 提出的 3 个 Major 阻塞项 + Major-Supplement 以及绝大多数 Minor 项。关键验证：`OverviewTab.tsx` 的 `#E5534B` 清理已在 S11 acceptance 里显式锁定 grep 断言；周期校验统一收敛为后端 `TIMEFRAME_PRIORITY` 白名单正则；S4/S5 在 task.json 中串行化（S5 depends_on=[S4]），并在 parallel_groups 中由波 4 移至波 5；`BacktestSubscriptionTable` 的 `data-form-section` 动画迁移通过新增 `FORM_SECTION_STATIC_CLS` 常量在 S2 完成。另外 planner 还引入了 `FR-013`（WS 降级态）、`FR-NFR-002`（响应式降级）、`FR-NFR-003`（无障碍降级）、`FR-NFR-004`（SectionLabel 归一化）、`FR-076`（动画迁移）等 8+ 条补强条款，以及 `AC-B-7/AC-B-8`（state 持久化 + 重试预填）两条新的 E2E 验收。整体计划达到可执行标准，未引入新的架构反模式。

## 代码引用验证（仅验证 r1 修复引入的新引用）

| 引用 | 文件存在 | 内容准确 | 问题 |
|------|---------|---------|------|
| `TIMEFRAME_PRIORITY`（`src/tinohelm/backtest/runner_helpers.py:20-22`） | Yes | **Yes** — 12 个值 `1m/3m/5m/15m/30m/1h/2h/4h/6h/8h/12h/1d` 精确匹配 FR-061 白名单正则 | 无 |
| `OverviewTab.tsx:207-219`（3 处 `#E5534B`）| Yes | **Yes** — 第 207、208、219 行分别为 `<stop stopColor>×2` + `<Area stroke>`，均位于 `:151-226` 的 drawdown AreaChart block 内，S11 明确删除此范围 | 无 |
| `BacktestRunRow.tsx` 两段函数边界 | Yes | **Yes** — `BacktestRunRow`（35-283 行）与 `BacktestHistoryRow`（285-441 行）以 `/* BacktestHistoryRow */` 注释分界；S4 仅碰前段 + 新增 `RingProgress`，S5 仅碰后段，边界清晰 | 无 |
| `OverviewGreyTab.tsx` 外部引用 | 无 | **Yes** — Grep 仅命中 `OverviewGreyTab.tsx` 自身（514 行 interface + 518 行 export），零外部 import；S15 前置检查 + 删除逻辑正确 | 无 |
| `FORM_SECTION_STATIC_CLS` 设计约束 | N/A（待创建）| **Yes** — 设计文档中该常量定义为 `"mb-7 transition-[opacity,transform] duration-[450ms] ease-qds"`（去掉 `opacity-0 translate-y-4` 预设和 `data-[visible=true]:...` 响应样式），符合"折叠区打开→直接渲染"的语义 | 无 |

## r1 阻塞项修复验证

| r1 阻塞项 | 修复位置 | 状态 | 证据 |
|-----------|---------|------|------|
| **Major-1** OverviewTab `#E5534B` 未纳入清理 | S11 acceptance（`4-tasks.md:252`）| ✅ 解决 | `grep "#E5534B" .../OverviewTab.tsx` exit code = 1 断言已加；S11 task 描述明确"删除行号 `151-226`，共约 76 行"并追加"再 grep 全文件确认其他位置没有残留"（`:247`）；NFR-2 清理范围清单（`1-requirements.md:216`）亦显式列出此文件及行号 |
| **Major-2** 自定义周期校验与后端能力不一致 | FR-061（`1-requirements.md:99-103`）+ S8 acceptance（`4-tasks.md:169,179-181`）| ✅ 解决 | 正则从 `/^\d+(s\|m\|h\|d)$/` 换为 `/^(1m\|3m\|5m\|15m\|30m\|1h\|2h\|4h\|6h\|8h\|12h\|1d)$/`；InlineError 文案列出完整 12 个白名单值；S8 acceptance 增加了 `30m`→通过、`7m`→拒绝、`abc`→拒绝三种 case 的独立断言 |
| **Major-3** S4/S5 对 `BacktestRunRow.tsx` 并发写入冲突 | task.json + 4-tasks.md 多处 | ✅ 解决 | (a) `task.json:39-45` S5 `depends_on=["S4"]`；(b) `task.json:137-165` `parallel_groups` 波 4=`[S4,S7,S8,S12]`、波 5=`[S5,S9,S14]`（S5 已移出波 4）；(c) 4-tasks.md 文件编辑边界约定（`:381-385`）显式声明 S4 仅碰 RunRow 段、S5 仅碰 HistoryRow 段；(d) S4/S5 均声明"不改 page.tsx" |
| **Major-Supplement** `BacktestSubscriptionTable` Sheet 内动画机制缺失 | FR-076（`1-requirements.md:119-122`）+ S2（`4-tasks.md:23,31,43-44`）| ✅ 解决 | 新增 `FORM_SECTION_STATIC_CLS` 常量（`backtestStyles.ts`）；`BacktestSubscriptionTable.tsx:57` 的 `className={FORM_SECTION_CLS}` 改为 `className={FORM_SECTION_STATIC_CLS}`，并删除 `data-form-section` 属性；S2 acceptance 增加两条 grep 断言（`data-form-section` 零命中 + `FORM_SECTION_STATIC_CLS` ≥1 命中）；AC-C-9 加强全仓零 `data-form-section` 扫描 |

## r1 Minor 项修复验证

| r1 Minor | 状态 | 证据 |
|---------|------|------|
| AC-C-3 grep 边界锚点 | ✅ | `1-requirements.md:274` AC-C-3 正则已改为 `(["'\'']\|\s)\.(card\|tab-bar\|chip\|row-stripe\|sheet-overlay\|badge-run\|mono\|dim)(["'\'']\|\s)` 形式，并加注释"规避 bg-card / shadcn `<Card>` / `data-tab-bar=...` 等假阳性" |
| S15 缺失 S11 直接依赖 | ✅ | `task.json:127-133` S15 `depends_on=["S5","S9","S11","S12","S13","S14"]` 已显式包含 S11 |
| S13 依赖建议调整 | ✅ | `task.json:104-110` S13 `depends_on=["S10","S11","S12"]`（符合 r1 建议"改为 `[S11, S12]` 或挪至第 4 波"的第一种方案） |
| 删除 `OverviewGreyTab.tsx` | ✅ | FR-NFR-005（`1-requirements.md:246-247`）+ S15 前置检查与删除（`4-tasks.md:325-328`）+ AC-C-7（`:278`）三层保障 |
| tech-design §4.1 响应式规格 | ✅ | FR-NFR-002（`1-requirements.md:195-198`）详细给出 xl/lg/<lg 三档 grid 字符串 + Sharpe/WinRate/run_id 降级规则；tech-design §4.1（`3-tech-design.md:151-175`）带表格说明每列响应式行为；S3 acceptance 三档视口 DOM 断言 |
| 重试预填字段清单 | ✅ | FR-033（`1-requirements.md:64-78`）完整 10 字段表格；tech-design §7.4（`:586-590`）再次列出；AC-B-8（`:268`）E2E 验证 |
| Playwright E2E | ✅ | AC-B-1~8 共 8 条 E2E（`1-requirements.md:261-269`）；tech-design §9.2（`:664-670`）4 个 spec 文件与 AC 覆盖表；S15 acceptance 新增 `npx playwright test e2e/backtest/` 断言（`4-tasks.md:333,344`） |
| Sheet 内 SubscriptionTable 横向不溢出 | 部分 | FR-076 + FORM_SECTION_STATIC_CLS 解决动画；AC-A-6（`:257-259`）断言 Sheet 宽度 520px（含隐式约束 "Sheet 内容不溢出"），但未显式有横向滚动降级断言。**不升级为阻塞**：Sheet 内容溢出在实现阶段由 executor 通过 Tailwind 添加 `overflow-x-auto` 解决即可 |
| Equity SVG 无 tooltip 声明 | ✅ | FR-091（`1-requirements.md:149`）显式声明"无 tooltip/hover 交互：本次仅实现绘制动画 + 静态 label；hover 游标等交互需求不在本次范围（Out-of-scope）"，符合 r1 权衡分析的建议 |
| S9 粒度 | 部分 | S9 est 仍为 3h（`4-tasks.md:206`），未拆为 9a/9b，但 acceptance 已细化为 6 条独立断言（fill_model 数量、9 选项选中、默认折叠、InlineError、文案、成功后行为），实现时每条独立可验收。**不升级**：est 3h 的任务在 DAG 并行后实际墙钟影响小 |
| PnL 符号统一 | ✅ | tech-design §4.3（`3-tech-design.md:394`）明确约定 `totalPnl >= 0 ? "+$" + fmt(abs) : "-$" + fmt(abs)`；S3 task 描述（`4-tasks.md:54`）"HistoryRow 和 RunRow 两处保持一致" |

## 技术设计审查 (3-tech-design.md)

### Critical 发现
无。

### Major 发现
无。

### Minor 发现

1. **`BacktestRunRow.tsx` WS 降级态 `data-ws-stale` 属性传递链路细节**
   - FR-013（`1-requirements.md:53`）要求 running 行 `lastProgressAt < now - 15000` 时加 `data-ws-stale="true"`；S4 task（`4-tasks.md:76`）要求接收 `isWsStale: boolean` prop。
   - 但计算 `isWsStale` 的上游逻辑归属（应在 `BacktestListView` 还是 `page.tsx`？使用 `useWsConnection` 还是 hooks 内新增 `lastProgressAt` 追踪？）未在 tech-design §7.2（`3-tech-design.md:560-562`）精细化。
   - **不阻塞**：executor 可按 "在 `useBacktestRuns` 内追加 `lastProgressAtMap: Record<string, number>`，每次 WS progress 写入时刻，BacktestListView 遍历计算 stale 标志" 的朴素实现；但建议 planner 在下一次修订里补 1 句实现提示。
   - 严重度：Minor。

2. **S9 的 `advancedExpanded` state 归属表述矛盾**
   - FR-045（`1-requirements.md:90`）约定 `advancedExpanded` 为 Sheet 顶层唯一 owner。
   - tech-design §4.2（`3-tech-design.md:237`）的 Sheet state 列表亦包含 `advancedExpanded`。
   - S6 task（`4-tasks.md:112`）也列出 Sheet 顶层持有 `advancedExpanded`。
   - S9 task 描述（`4-tasks.md:192`）写"默认 `advancedExpanded=false`，通过 props 受控"，措辞上与上述一致；但 acceptance 第 3 条（`:210`）"渲染后 `advancedExpanded=false`，折叠内容 `max-height=0`；点击「▾ 高级选项」展开" 暗示 Step 3 内部可能直接管理 `advancedExpanded`。
   - **不阻塞**：只是验收断言对 state 变更源描述模糊；executor 读 FR-045 即可无歧义实现。
   - 严重度：Minor。

3. **WS 降级态的 CSS 选择器测试可行性**
   - `data-[ws-stale=true]:[animation-play-state:paused]` 依赖 Tailwind arbitrary variant 选择父级属性；Tailwind 官方 `data-[attr]:...` variant 在应用到子元素时依赖的是**自身**属性。如果父级 row 挂 `data-ws-stale="true"`，子级 RingProgress `<circle>` 用 `data-[ws-stale=true]:[animation-play-state:paused]` **不会**生效，因为 Tailwind 默认生成 `[data-ws-stale=true]:`（自身选择器），需要 Tailwind group variant：`group-data-[ws-stale=true]:[animation-play-state:paused]` + 父级 `group`。
   - S4 task（`4-tasks.md:76`）直接写 `data-[ws-stale=true]:[animation-play-state:paused]`，executor 按此实现将失效。
   - **不阻塞但值得警觉**：可在实现阶段调整为 `group/row-group` 命名的 group variant；或 RingProgress 内部接受 `isWsStale` prop 直接挂 inline `style={{ animationPlayState: isWsStale ? "paused" : "running" }}`。
   - 严重度：Minor。

## DAG 审查 (4-tasks.md + task.json)

### 依赖正确性
- S1 → S2 → S3 → S4 → S5 主链正确。S5 `depends_on=["S4"]` 消解 `BacktestRunRow.tsx` 冲突风险 ✅。
- S6 → {S7, S8} → S9 正确（Step 3 需要前两步 state 模型稳定）。
- S11 → S12 正确（Overview SVG 完成后才能布局调整）。
- S15 `depends_on` 完整覆盖所有终端任务 ✅。
- S14 `depends_on=["S2","S6","S10"]`：需要 S2 的 backtestStyles 常量、S6 的 page.tsx view 枚举 + 占位、S10 的 `onViewAllTrades` prop 链路 — 正确。

### 并行最优性
- 波 3 `[S3, S6, S10, S11]`：
  - S3 改 `BacktestListView.tsx` + `BacktestRunRow.tsx`；S6 改 `page.tsx` + 新建 Sheet 文件；S10 改 `BacktestDetailView.tsx`；S11 改 `OverviewTab.tsx` + 新建 `OverviewEquitySvg.tsx`。
  - **4 个任务文件集合零重叠** ✅。（r1 提出的 S3+S6 共改 page.tsx 问题已消除 — S3 明确"本任务不改 page.tsx"，`4-tasks.md:56`）
- 波 4 `[S4, S7, S8, S12]`：
  - S4 改 `BacktestRunRow.tsx` RunRow 段 + `BacktestListView.tsx`；S7 改 `BacktestCreateStep1.tsx`；S8 改 `BacktestCreateStep2.tsx`；S12 改 `OverviewTab.tsx` + 可选 `OverviewTradeTables.tsx`。
  - S4 与 S3 串行，文件不冲突；S12 改的 `OverviewTab.tsx` 波 3 由 S11 改过，但 S12 `depends_on=["S11"]` 保证串行。
  - S4 与 S12 均可能涉及 `BacktestListView.tsx`？— 查 S12 files（`:266`）仅 `OverviewTab.tsx` 和可选 `OverviewTradeTables.tsx`，不涉及 `BacktestListView.tsx`。零冲突 ✅。
- 波 5 `[S5, S9, S14]`：
  - S5 改 `BacktestRunRow.tsx` HistoryRow 段；S9 改 `BacktestCreateStep3.tsx`；S14 改 `BacktestTradesView.tsx` 新文件 + `page.tsx`。
  - **S14 的 `page.tsx` 修改与 S5 无交集（S5 不碰 page.tsx）✅**；`BacktestCreateStep3.tsx` 是 S9 独占；RunRow 文件 S5 独占。零冲突 ✅。
- 波 6 `[S13]`：SectionLabel 归一化扫描 6 tab 文件；前置波次已完成相关 tab 的其它改动。
- 波 7 `[S15]`：终结审计 + 清理 + E2E。

### 遗漏任务
无新增。

### 任务粒度
- S9 est 3h 未拆分，但如前所述 acceptance 细化，可接受。
- S14 est 3h（trades view 含 8 列表格 + ⌘K + 空态 + summary useMemo + page.tsx 接入），粒度合理。

### 验证任务
- S15 新增 `npx playwright test e2e/backtest/` + `npx vitest run` 两条断言，E2E 全自动化，不依赖人工走查 ✅。

## 权衡分析

| 决策 | 正方 | 反方 | 建议 |
|------|------|------|------|
| r1 建议 S9 拆为 S9a/S9b，planner 未拆 | (1) 保留 3h est 完整性，避免新增任务节点；(2) acceptance 已细化 6 条独立断言，实现时可分步验收 | (1) 3h 单任务对 executor 认知负荷高；(2) 失败时回滚粒度粗 | **可接受**。若执行阶段发现超时，可热拆分；当前保持不动合理。 |
| FORM_SECTION_STATIC_CLS 新增 vs 移除整个 FORM_SECTION_CLS | (1) 保留 `FORM_SECTION_CLS` 向后兼容，delete BacktestCreateView 后自然无引用；(2) 新增静态版语义清晰 | (1) backtestStyles.ts 有两个类似常量，后续维护者可能混淆；(2) `FORM_SECTION_CLS` 变成 dead code（只剩 export，无 import） | **可接受**。S15 完成后可追加一次性 dead-code 清理（grep 无引用后 delete），但非本次范围。 |
| AC-C-9 全仓零 `data-form-section` vs 仅 backtest 范围 | (1) 全仓扫描预防其他页面将来引入类似 pattern 时误用；(2) 零成本（grep 无递归影响） | (1) 若其他页面未来合法引入 `data-form-section`（不同语义），会被误杀 | **可接受**。当前 `data-form-section` 是 BacktestCreateView 专属，全仓零命中合理；如未来有跨页面需求再松绑。 |
| S5 延后 1 波 vs 文件拆分 | (1) 零代码拆分，DAG 调整最小；(2) 串行总工时 +1h 但波 5 内 S5（1h）+ S9（3h）+ S14（3h）并行后仍以 3h 为墙钟 | (1) 丧失 S4/S5 的并行度（波 4 少 1 个任务） | **可接受**。选择 r1 推荐方案 A（串行），与 r1 建议一致。 |
| WS 降级态用 `data-ws-stale` 属性 vs inline `style.animationPlayState` | (1) 属性选择器方案更 declarative，与 HTML 语义一致；(2) 属性可被 Playwright 直接断言 | (1) Tailwind arbitrary variant 对父属性选择器需要 group variant，S4 task 描述中未提及 group 包装；(2) 实现时易误用（见 Minor-3） | **可接受但建议改进**。实现时 executor 应注意父级 `group/run-row` + 子级 `group-data-[ws-stale=true]:...` 的 Tailwind group variant 用法，或直接用 inline style。该点已在 Minor-3 中标注。 |

## 遗漏项

无新遗漏（r1 的 5 条遗漏项已全部纳入或明确声明 Out-of-scope）。

## 上轮修改验证

| 上轮要求 | 是否解决 | 说明 |
|---------|---------|------|
| r1-Major-1：OverviewTab `#E5534B` 清理纳入 S11 | Yes | S11 acceptance grep 断言 + NFR-2 清理范围清单 + task 描述覆盖行号 151-226 |
| r1-Major-2：自定义周期白名单化 | Yes | FR-061 正则改为白名单；InlineError 文案列出完整 12 值；S8 acceptance 三 case 断言 |
| r1-Major-3：S4/S5 BacktestRunRow 冲突消解 | Yes | task.json S5 depends_on=[S4]；parallel_groups S5 移至波 5；文件编辑边界约定 |
| r1-Major-Supplement：SubscriptionTable 动画迁移 | Yes | FORM_SECTION_STATIC_CLS 新增 + FR-076 + S2 改造 + AC-C-9 全仓零 data-form-section |
| r1-Minor：AC-C-3 class 边界锚点 | Yes | AC-C-3 正则加引号/空白包围约束 |
| r1-Minor：S15 直接依赖 S11 | Yes | task.json S15 depends_on 含 S11 |
| r1-Minor：S13 依赖调整 | Yes | task.json S13 depends_on=[S10,S11,S12] |
| r1-Minor：删除 OverviewGreyTab | Yes | FR-NFR-005 + S15 前置检查 + AC-C-7 |
| r1-Minor：响应式 breakpoint | Yes | FR-NFR-002 xl/lg/<lg 三档规格 |
| r1-Minor：重试预填清单 | Yes | FR-033 10 字段表 + AC-B-8 E2E |
| r1-Minor：Playwright E2E 自动化 | Yes | AC-B 8 条 + S15 playwright test 断言 + 4 spec 文件清单 |
| r1-Minor：Equity SVG 无 tooltip 声明 | Yes | FR-091 显式 Out-of-scope |
| r1-Minor：Sheet 内 SubscriptionTable 横向不溢出 | 部分 | 未显式断言，但 FR-076 + AC-A-6 间接保障 |
| r1-Minor：S9 粒度 | 部分 | 未拆分，但 acceptance 细化 |
| r1-Minor：PnL 符号统一 | Yes | tech-design §4.3 + S3 task 描述 |

## 修改要求

无阻塞修改要求。以下为可选改进项（不影响 APPROVE）：

1. **[可选-Minor]** 在 tech-design §7.2 补 1 段"WS 降级态 `isWsStale` 计算归属"：建议 `useBacktestRuns` 内维护 `lastProgressAtMap: Record<string, number>`；`BacktestListView` 在遍历渲染 RunRow 时计算 `isWsStale = wsStatus !== "connected" && now - (lastProgressAtMap[run.run_id] ?? 0) > 15000`，透传给 RunRow。
2. **[可选-Minor]** S4 task 描述中的 Tailwind 选择器 `data-[ws-stale=true]:[animation-play-state:paused]` 改为 `group-data-[ws-stale=true]:[animation-play-state:paused]`（父级 row 加 `group/run-row` 或等价 class），或改用 `<circle style={{ animationPlayState: isWsStale ? "paused" : "running" }}>` inline style 写法。避免 executor 按当前描述照抄导致 CSS 不生效。
3. **[可选-Minor]** S9 acceptance 第 3 条（默认折叠 + 展开切换）明确 `advancedExpanded` state 源自 Sheet props 而非 Step3 内部 state，与 FR-045 一致。

以上 3 条不阻塞 executor 进入实现阶段；executor 可在 kickback 或 verify 阶段补正。

ReviewPass: architect
VERDICT: APPROVE
