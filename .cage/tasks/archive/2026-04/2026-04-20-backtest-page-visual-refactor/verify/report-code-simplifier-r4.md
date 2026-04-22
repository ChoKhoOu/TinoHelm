## 反熵检查报告

**状态**: SIMPLIFIED

### 检查的文件

| 文件 | 结论 |
|---|---|
| `src/web/src/app/backtest/page.tsx` | 无需简化 |
| `src/web/src/app/backtest/components/BacktestRunRow.tsx` | 无需简化 |
| `src/web/src/app/backtest/components/BacktestDetailView.tsx` | 无需简化 |
| `src/web/src/app/backtest/components/BacktestListView.tsx` | 无需简化 |
| `src/web/src/app/backtest/components/BacktestSubscriptionTable.tsx` | 无需简化 |
| `src/web/src/app/backtest/components/backtestStyles.ts` | **已简化** — 删除 2 个死代码导出 |
| `src/web/src/app/backtest/components/OverviewHelpers.tsx` | 无需简化（`SectionLabel` 改为 re-export 已是简化） |
| `src/web/src/app/backtest/components/OverviewTab.tsx` | 无需简化 |
| `src/web/src/app/backtest/components/OverviewTradeTables.tsx` | 无需简化 |
| `src/web/src/app/backtest/components/OverviewMonthlyHeatmap.tsx` | 无需简化 |
| `src/web/src/app/backtest/components/PerformanceHelpers.tsx` | 无需简化 |
| `src/web/src/app/backtest/components/TradesTab.tsx` | 无需简化 |
| `src/web/src/components/qds/InlineError.tsx` | 无需简化 |
| `src/web/vitest.config.ts` | 无需简化 |

### 应用的简化

- `src/web/src/app/backtest/components/backtestStyles.ts:44-49`（旧行号）：删除 `STATUS_PILL_MAP` 导出
  - 类型：**死代码删除**
  - 原因：`BacktestRunRow.tsx` 在 Round 2 改用 `StatusBadge` 组件后，`STATUS_PILL_MAP` 不再有任何消费者。仅剩的引用是定义行本身。

- `src/web/src/app/backtest/components/backtestStyles.ts:62-65`（旧行号）：删除 `TRADES_SIDE_BADGE_CLS` 导出
  - 类型：**死代码删除**
  - 原因：Round 2/3 期间添加但最终无任何文件 import 或使用此常量。

### 跳过的审查项（评估后认为合理）

**`buildKpiItems` 中的 IIFE 模式**（`BacktestDetailView.tsx:67-130`）

每个 KPI cell 用 `(() => {...})()` 封装。表面上是 6 个 IIFE，看似冗余，但实际上：
- 每个 cell 有独立的 null guard + 正负数逻辑，IIFE 为每个提供了词法作用域隔离
- 函数体本身 `buildKpiItems` 已是私有 helper（不导出），层级适当
- 若改为数组 map + 条件对象字面量，可读性反而下降
- 判定：保留现状，不改

**`page.tsx` 中的 `eslint-disable-next-line` 注释**（第 51、57 行）

两处 `react-hooks/set-state-in-effect` 的 suppress 注释带有明确的 `-- reason:` 说明，解释了为何在 effect 内同步 setState 是有意为之（WS 时间戳记录、interval tick）。这是有意义的注释，不是冗余注释。保留。

**`BacktestRunRow.tsx` 中的列布局注释**（10-col grid 行内注释）

这些注释解释了非显而易见的 10 列 grid 结构（每列含义），对维护者有实际价值。保留。

### 验证

- 测试命令：`npx vitest run`
- 结果：**27 passed, 0 failed**（4 test files）
- 构建：未重新运行（仅删除了死导出，TypeCheck 在前轮已通过；删除未使用导出不会引入 TS 错误）

### 摘要

代码经 3 轮打磨已非常精简。本轮仅在 `backtestStyles.ts` 中发现 2 个死代码导出（`STATUS_PILL_MAP` 被 `StatusBadge` 组件取代后遗留，`TRADES_SIDE_BADGE_CLS` 被添加但从未被引用），已删除。其余所有变更均属于有意义的功能实现，结构合理，无过度抽象。

VerifyPass: code-simplifier
Verdict: PASS
