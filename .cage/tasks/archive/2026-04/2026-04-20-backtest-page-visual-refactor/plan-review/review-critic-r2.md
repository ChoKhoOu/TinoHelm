# Critic Review — Round 2

**VERDICT: APPROVE**

## 总体评估

r1 审查给出 REVISE，列出 3 项 CRITICAL（C1/C2/C3）和 6 项 MAJOR（M1~M6），planner 在第 2 轮修订中逐条回应：
- **3 项 CRITICAL 全部彻底解决**：C1 Trades 降为 8 列并删除 MFE/MAE；C2 subscriptions UI 明示仅 Step 3 折叠区渲染；C3 全部"视觉走查/手测/视觉统一"措辞替换为 DOM/grep/fake timer/脚本 exit code 等机器判据。
- **6 项 MAJOR 全部修复**：M1 硬编码颜色清理面覆盖 `OverviewGreyTab.tsx`（S15 删除）和 `PerformanceHelpers.tsx`（S2(b) 清理）；M2 新增 FR-007/013/092/119 覆盖空态/错误态/WS 断连；M3 新增 FR-NFR-002 定义 3 档响应式降级；M4 FR-033 给出完整字段预填表；M5 S13 依赖改为 `[S10, S11, S12]`；M6 FR-045 明示 state owner 归属 + S8/S9 明确搬迁源。
- **Minor/Open Questions 大部分被采纳**：FR-115 ⌘K 真实绑定、FR-120 URL 深链 Out-of-scope、FR-082 注脚澄清 7 tab vs 8 项笔误、FR-NFR-003 `prefers-reduced-motion` 降级。
- **依赖图与并行波次经过重新编排**：波 4 现只有 4 个 subtask（S4/S7/S8/S12），规避 Cage 5 并发限制。

文档代码引用精准（抽样验证 `FILL_MODEL_OPTIONS` 位于 49-59、`parseTimeframe` 位于 61、`PerformanceHelpers` rgba 位于 167/169/170、`OverviewTab.tsx` `#E5534B` 位于 207-219 均与声明一致）。验收标准全部为自动化可验证项，符合用户 RULE。

**结论：r2 给出 APPROVE，进入执行阶段。**

## 预判 vs 实际

| 预判 | 是否命中 | 实际发现 |
|---|---|---|
| C1/C2/C3 是否已彻底修复 | 命中 | 全部修复，证据链独立可验证 |
| M1~M6 是否引入新缺口 | 部分命中 | M1~M6 修复充分，但在 FR-033 与 tech-design 之间发现 `form.from_retry` vs `fromRetry` 字段命名微不一致（不阻塞） |
| 验收标准是否全部自动化 | 命中 | AC-A/B/C/D/E 全部为 DOM / grep / Playwright / getAnimations / exit code 断言，零手动项 |
| 是否引入新 CRITICAL | 未命中 | 未发现新 CRITICAL 或 MAJOR |
| 依赖图修正是否到位 | 命中 | S13 deps=[S10,S11,S12]、S15 deps 覆盖全部、波 4 降至 4 个 subtask |

## Critical 发现（阻塞执行）

**无**。

## Major 发现（导致显著返工）

**无**。

## Minor 发现（次优但可工作）

### Mi1 — FR-033 的 `form.from_retry` vs tech-design §4.2 的 `fromRetry` 命名不一致
- **证据**：
  - `1-requirements.md:79` FR-033：`\`form.from_retry === true\` 时挂载`。
  - `3-tech-design.md:238` 代码示例：`const fromRetry = retryPrefill !== null;`（派生变量，非 `form` 字段）。
  - `4-tasks.md:114` S6 acceptance 描述：`fromRetry && <InlineError ...>`。
- **影响**：executor 看到 `form.from_retry` 可能误以为需要扩展 `BacktestForm` 类型新增字段。实际实现走 `fromRetry` 派生变量即可，无需动 form schema。
- **修复建议**（非阻塞）：下次修订时把 FR-033 末尾的"`form.from_retry === true`"改为"`fromRetry === true`（来自 `retryPrefill !== null` 派生）"，与 tech-design §4.2 对齐。
- **不阻塞原因**：tech-design §4.2 有完整代码示例，executor 以设计文档为准即可。

### Mi2 — task.json 中 S12 的 `depends_on` 缺显式 S2
- **证据**：
  - `task.json:98-101` S12 `depends_on: ["S11"]`。
  - `4-tasks.md:267` S12 deps: `["S2", "S11"]`。
- **影响**：拓扑排序层无影响（S11 已依赖 S2，传递性保证 S2 先执行）；但静态校验工具可能告警 deps 不完整。
- **修复建议**（非阻塞）：把 S12 `depends_on` 补齐为 `["S2", "S11"]`；或在 4-tasks.md 中降为仅写 `["S11"]`，两处文档统一即可。
- **不阻塞原因**：Cage parallel_groups 已将 S12 安排到波 4（S11 所在波 3 之后），执行顺序正确。

### Mi3 — 无需重复：r1 的 Mi1~Mi5 已全部被修订覆盖或默认接受
- r1 Mi1 (`fade-up` vs `qds-fade-up` 冗余) → tech-design §6 脚注指出"未来 cleanup Out-of-scope"，接受现状。
- r1 Mi2 (`data-kpi-cell` 保留/测试用) → FR-081 和 AC-A-3 均保留，接受。
- r1 Mi3 (stepper 移动端挤压) → 现 Sheet `w-full sm:max-w-[520px]` 在 `<sm` 全屏展开，stepper 有足够空间。默认接受。
- r1 Mi4 (ShimmerBar `active` 简写) → tech-design §4.1 代码示例已改为 `active={true}` 明示，接受。
- r1 Mi5 (tab key 文案不一致) → FR-082 注脚明确 interview.md "8 项"为笔误，规范 7 tab，已修正。

## 缺失项

**无新缺失项**。r1 提到的 7 项（键盘快捷键、CSV 导出、URL 深链、Sheet overlay 行为、Previous state 保持、Submit 失败状态、Monthly heatmap 空数据）在 r2 文档中逐一应对：
- ⌘K 绑定（FR-115 明示"不是装饰"）
- CSV 导出 Out-of-scope（未显式 but 默认）
- URL 深链 Out-of-scope（FR-120 明示）
- Sheet overlay 行为（NFR-3 明示 shadcn ESC/焦点 trap）
- Previous 保持 state（FR-043 明示）
- Submit 失败（FR-075 明示 `<InlineError>` 在 SheetFooter 上方 + Sheet 不关闭）
- Monthly heatmap 空数据（FR-093 改 `<InlineError variant="hint">`）

## 歧义风险

**无新歧义**。r1 A1~A4 处理情况：
- A1 subscriptions 归属 → C2 已解决。
- A2 10 列 Sharpe/WinRate/PnL 空值显示 → FR-022 明确统一渲染 `—`，AC-A-1 断言。
- A3 Sheet 宽度 450~520px → FR-040 明确 `w-full sm:max-w-[520px]`；AC-A-6 双视口断言。
- A4 qds-section-label vs `<SectionLabel>` → FR-NFR-004 明确"必须选组件"，AC 通过 `grep SectionLabel` 验证。

## 假设分析

| 假设 | 级别 | 说明 |
|---|---|---|
| `TradeLogEntry` 不含 mfe/mae | VERIFIED | C1 修复证实降列方案，FR-116 引用 `types.ts:61-71` 精准 |
| `BacktestCreateView.tsx:49-59` 为 FILL_MODEL_OPTIONS | VERIFIED | 实测 49-59 行为 9 元素数组 |
| `BacktestCreateView.tsx:61-69` 为 parseTimeframe | VERIFIED | 实测 61 起始 |
| `PerformanceHelpers.tsx:167/169/170` rgba 三连 | VERIFIED | 实测 167="rgba(76, 158, 235, 0.5)"、169="rgba(38, 217, 127, 0.5)"、170="rgba(239, 83, 80, 0.5)" |
| `OverviewTab.tsx:207-219` 含 `#E5534B` × 3 | VERIFIED | 实测 207/208 stopColor + 219 stroke |
| OverviewGreyTab 零外部引用 | VERIFIED | `grep -c OverviewGreyTab` 仅命中自身文件 2 次（interface + export） |
| `color-mix(in srgb, ...)` 浏览器支持 | VERIFIED | NFR-4 明示 Chromium 111+/Safari 16.2+/Firefox 113+ |
| Sheet `w-full sm:max-w-[520px]` `<sm` 全屏 | REASONABLE | Tailwind `w-full` 覆盖默认 max-width 约束，AC-A-6 双视口断言保护 |
| 10 列 grid 最小宽度 ≈ 1083px 与 `xl` 1280px 匹配 | VERIFIED | FR-NFR-001 明示 + FR-NFR-002 3 档响应式降级 |
| `verify-ds-compliance.sh --mode both-themes` 可用 | REASONABLE | 与 r1 VERIFIED 假设一致 |
| S15 `npx playwright test e2e/backtest/` 可跑 | FRAGILE | E2E 测试框架是否已搭建未显式说明；但 files 清单含"新建/修正 E2E spec 文件（若不存在由本任务创建）"，兜底明确 |

## 预验尸

| 失败场景 | 文档是否应对 | 说明 |
|---|---|---|
| `/api/backtest/{id}/result` 返回 500 | Yes | FR-081 状态降级（KPI 显 `—`）+ FR-092 (Equity SVG 空/错误态降级，InlineError) |
| Trades 过滤 + 搜索后 0 行 | Yes | FR-119 明确 summary/filter 保留，table 区内联 EmptyState |
| WS 断连 10 秒未重连 | Yes | FR-013 `data-ws-stale=true` 暂停动画 + "连接待恢复" hint |
| 窄屏 1024~1280px Sheet + 列表并存 | Yes | FR-NFR-002 `lg` 档 hide Sharpe/WinRate，防止横向溢出 |
| 用户重试 failed run | Yes | FR-033 完整字段预填表 + AC-B-8 E2E 断言 |
| S6 与 S14 均改 page.tsx 冲突 | Yes | 4-tasks.md 文件编辑边界约定明示"S6 建立占位/S14 替换占位" |
| S11/S12/S13 均改 OverviewTab.tsx | Yes | 依赖链 S11→S12→S13 串行，无并行冲突 |
| S3/S4/S5 均改 BacktestRunRow.tsx | Yes | S3 骨架 / S4 仅 RunRow 段 / S5 串行 S4 仅 HistoryRow 段，边界清晰 |
| dash 动画 `prefers-reduced-motion` | Yes | FR-NFR-003 明示 `motion-reduce:[animation-duration:0s]` |
| Sheet submit 失败保持 open | Yes | FR-075 明示 `<InlineError>` 在 SheetFooter 上方 + Sheet 不关闭 + 不 toast |
| 用户刷新页面丢 trades view | Yes (预期行为) | FR-120 声明 Out-of-scope，不是 bug |

## 多视角笔记

### Executor 视角
- **S3**：10 列响应式 grid（3 档 class）在 tech-design §4.1 GRID_COLS_CLS 常量给出，可直接 copy-paste。Sharpe/WinRate 通过 `hidden xl:flex` 控制。
- **S4~S5**：文件编辑边界约定清晰——S4 仅改 RunRow 段 + 新增 RingProgress 内嵌组件；S5 串行 S4 后仅改 HistoryRow 段。
- **S6**：state owner 模式在 tech-design §4.2 有完整代码示例，包含 retryPrefill useEffect、Previous/Next/Cancel 按钮逻辑、key={step} 触发 slideInUp。新建空壳 Step1/2/3 明示为 S7/S8/S9 占位。
- **S7/S8/S9**：三者跨步 state 共享规则已在 FR-045 明示（受控子组件），parseTimeframe 来源（S8）、FILL_MODEL_OPTIONS 来源（S9）均精确。
- **S11**：OverviewTab `:151-226` 删除范围明确，替换代码给出；无 tooltip/hover 交互在 FR-091 明示 Out-of-scope。
- **S14**：8 列表格列头文本集合 `{ID, 日期, 方向, 入场, 出场, 仓位, 盈亏, 持仓}` 可通过 `th.textContent` 逐列断言。空态 2 种情况（`tradeLog=[]` / `filtered=[]`）均有明确渲染规则。
- **S15**：files 清单明确"新建/修正 E2E spec 文件（若不存在由本任务创建）"，兜底清晰。

### Stakeholder 视角
- 原始意图（视觉重构 · 功能保留）达成：4 视图 × 重构骨架 + 零后端改动 + 零 API schema 变动。
- 成功标准：FR/AC 均有具体可验证依据，无虚荣指标。重试、空态、WS 降级等边界情况均纳入验收。
- 范围合理：15 subtasks、波 7 层并行后 ~14h（4-tasks.md §总预估）。符合"单页重构"预期。

### Skeptic 视角
- **最强反方**：FR-033 的 `form.from_retry` 命名轻微不一致于 tech-design 的 `fromRetry` 派生变量——但这是文档措辞问题，不是架构冲突。executor 按 tech-design §4.2 代码实现即可。
- **Trades 删 MFE/MAE 可能引起用户不适**：interview.md §验收标准里明确提及 MFE/MAE；但 FR-116 已显式声明"interview.md 表述以此 FR 为准更正为'本次仅 8 列'"，且给出技术原因（`TradeLogEntry` 类型不含 + `MaeMfePoint` 无 trade id join）。合理技术决策。
- **零新增非 keyframe class 承诺过严**：如 skeptic 所言，若未来需要 `stepper-dot` 业务类会被此规则阻碍；但规划通过允许修改 `components/qds/` 组件源码（不触 globals.css）规避。

## 上轮修改验证

| r1 修改要求 | 是否解决 | 说明 |
|---|---|---|
| **C1** Trades MFE/MAE → 三选一明示 | Yes | FR-116 降为 8 列；S14 acceptance 断言 `<th>.length === 8`；AC-B-5 删除 MFE/MAE 表述 |
| **C2** subscriptions 归属 Step 2 vs Step 3 矛盾 | Yes | FR-052 改写"Step 1 仅操作 symbol"；Step 3 折叠区渲染 `<BacktestSubscriptionTable>`（FR-073）；tech-design §4.2 Step 1 字段表明示 granularity/dataType "Step 1 不读不写" |
| **C3** "视觉走查/手测/视觉统一" → 机器判据 | Yes | S3 改 `pageerror count=0`；S8 改 fake timer debounce；S13 改 `grep SectionLabel` + `无 console error`；NFR-4 改"脚本自动扫描 + 不做视觉走查" |
| **M1** OverviewGreyTab + PerformanceHelpers 清理 | Yes | S2(b) 清理 PerformanceHelpers 三处 rgba；S15 删除 OverviewGreyTab；FR-NFR-005 明示零外部引用事实 |
| **M2** 空态/错误态/WS 断连 | Yes | FR-007（列表空态）+ FR-013（WS 断连）+ FR-092（Equity 空/错误）+ FR-081 状态降级 + FR-119（Trades 空态）|
| **M3** 响应式 degrade 方案 | Yes | FR-NFR-002 定义 3 档 breakpoint + Sheet `w-full sm:max-w-[520px]` + AC-A-6 双视口断言 + S3 acceptance 多视口 DOM 断言 |
| **M4** 重试预填字段清单 | Yes | FR-033 完整字段表 + tech-design §7.4 重复列出 + AC-B-8 E2E 断言 |
| **M5** 依赖图修正 | Yes | S13 `depends_on=[S10, S11, S12]`；S15 `depends_on=[S5,S9,S11,S12,S13,S14]` 覆盖全部；波 4 降至 4 个 subtask 规避 Cage 5 并发限制 |
| **M6** state ownership 明示 + parseTimeframe 搬迁 | Yes | FR-045 明示 BacktestCreateSheet 为 state owner；tech-design §4.2 完整受控组件代码；S8/S9 明确搬迁源 `BacktestCreateView.tsx:61-69/49-59` |
| **Mi1~Mi5** 次要项 | 大部分 Yes | Mi5（tab key 一致）已修正；Mi1/Mi2/Mi3/Mi4 按文档现状接受或有明确说明 |
| **Op1~Op5** 开放问题 | 大部分 Yes | Op1/Op2/Op3/Op4 全部处理；Op5 与本轮无关 |

## 修改要求（REVISE/REJECT 时必填）

**无**（APPROVE 无修改要求）。

## 判决理由

**VERDICT: APPROVE**

**给出 APPROVE 而非 REVISE 的理由**：
1. r1 的 3 项 CRITICAL 全部彻底解决，证据链独立可验证（FR-116 降列、FR-052 subscriptions 唯一 UI 归属、所有 AC 为机器判据）。
2. r1 的 6 项 MAJOR 全部有具体 FR/AC/S# 对应修复，覆盖面完整（M1 颜色清理 / M2 空态 / M3 响应式 / M4 重试预填 / M5 依赖图 / M6 state owner）。
3. 没有新的 CRITICAL 或 MAJOR 浮现；仅有 2 项 Minor（FR-033 字段命名一致性、task.json S12 缺显式 S2）均不阻塞执行。
4. 验收标准全部自动化，符合用户 RULE "不得包含手动验证项"（r2 全文件扫描 `手测/视觉走查/视觉统一` 在验收项中零命中；仅存一处"**不做视觉走查**"是否定性声明，明示采用脚本自动扫描）。
5. 代码引用精准（抽样验证 4 个文件的行号与字段均与现实代码一致）。
6. 依赖图已优化规避 Cage 5 并发限制（波 4 降为 4 个 subtask）。
7. 现实检查（阶段 7）：2 项 Minor 均无数据丢失 / 安全漏洞 / 资金风险，且最坏情况（executor 误读 `form.from_retry` 去扩展 form 类型）在 tech-design §4.2 有完整代码示例兜底。

**未升级为 ADVERSARIAL 模式的理由**：本轮仅需验证 r1 遗留项，未发现 CRITICAL 或 3+ MAJOR，不满足升级条件。THOROUGH 模式下逐项核对 r1 修改 + 代码库交叉验证 + 预验尸 + 多视角审查，已充分覆盖。

## Open Questions（未评分）

- **Op1**：S15 运行 `npx playwright test e2e/backtest/` 时，若仓库内尚无 Playwright 配置 / CI runner，spec 文件创建后能否真的跑起来，是否需要在 S15 加入 `ls playwright.config.ts` / `npx playwright install chromium` 兜底？建议首次执行时 executor 自行评估。
- **Op2**：FR-033 `form.from_retry === true` 的措辞与 tech-design 的 `fromRetry` 派生变量语义一致但命名不同。建议后续小版本文档同步对齐（不影响本次执行）。
- **Op3**：task.json S12 `depends_on` 缺显式 S2（传递性已满足）。若 Cage harness 有静态 deps 校验工具可能告警，建议下次补齐。

ReviewPass: critic
VERDICT: APPROVE
