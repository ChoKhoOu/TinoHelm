## 反熵检查报告

**状态**: SIMPLIFIED

### 检查的文件

R2 新增的 untracked 文件（共 27 个）：

**backtest/components/Backtest* 系列（9 文件）**
- `BacktestCreateView.tsx` — 无需简化
- `BacktestDetailView.tsx` — 无需简化
- `BacktestListView.tsx` — 无需简化
- `BacktestPagination.tsx` — 无需简化
- `BacktestRunRow.tsx` — 无需简化
- `BacktestRunningPlaceholder.tsx` — 无需简化
- `BacktestSubscriptionTable.tsx` — 无需简化
- `backtestStyles.ts` — 无需简化

**backtest/hooks/ 系列（2 文件）**
- `useBacktestRuns.ts` — 无需简化
- `useBacktestDetail.ts` — 无需简化

**backtest/components/Performance* 系列（7 文件）**
- `PerformanceHelpers.tsx` — 包含与 OverviewHelpers 重复的 CARD_CLS/CARD_HEADER_CLS 定义，已评估（见"跳过"节）
- `PerformanceEquityChart.tsx` — 无需简化
- `PerformanceDrawdownChart.tsx` — 无需简化
- `PerformancePeriodChart.tsx` — 无需简化
- `PerformanceRollingChart.tsx` — 无需简化
- `PerformanceDistributionChart.tsx` — 无需简化
- `PerformanceMetricsSummary.tsx` — 无需简化

**backtest/components/Trades* 系列（3 文件）**
- `TradesHelpers.tsx` — 包含与 OverviewHelpers 重复的 CARD_CLS 定义，已评估（见"跳过"节）
- `TradesMetricCard.tsx` — 无需简化
- `TradesCharts.tsx` — 无需简化

**backtest/components/Overview* 系列（5 文件）**
- `OverviewHelpers.tsx` — 无需简化
- `OverviewKpiGrid.tsx` — 无需简化
- `OverviewMonthlyHeatmap.tsx` — 无需简化
- `OverviewDistributionBars.tsx` — 无需简化
- `OverviewTradeTables.tsx` — 无需简化

**StatusBadge**
- `src/components/qds/status-badge.tsx` — 设计清洁，10 键状态表是业务需要；`locale` 分支使得两个 LABEL_MAP 各有作用，无冗余

**扫描脚本**
- `src/web/scripts/verify-ds-compliance.sh` — 发现并删除死函数 `scan_r9()`

---

### 应用的简化

- `src/web/scripts/verify-ds-compliance.sh:597-635`：删除未使用的 `scan_r9()` 函数（含内嵌 Python heredoc，~38 行）
  - 类型：**死代码删除**
  - 原因：`scan_r9()` 在脚本中定义但从未被调用。R9 规则的实际执行函数是 `scan_r9_v2()`（awk 实现），在默认模式（行 907）和 preflight 模式（行 831）中均调用 `scan_r9_v2`。`scan_r9()` 是开发过程中被 `scan_r9_v2` 取代后遗留的原始 Python 版本，属于过渡期死代码。
  - 保留了注释 `# R9 using awk for reliable multiline block detection`，表明 `scan_r9_v2` 的用途

---

### 跳过的文件（有意识地决定不简化）

**CARD_CLS / CARD_HEADER_CLS / CARD_BODY_CLS 在三个 Helpers 文件中重复**

`OverviewHelpers.tsx`、`PerformanceHelpers.tsx`、`TradesHelpers.tsx` 各自定义了值完全相同的 `CARD_CLS`/`CARD_BODY_CLS`（前两个还有 `CARD_HEADER_CLS`）。表面上是重复，但：

1. 三个 Helpers 文件是各自子树（Overview/Performance/Trades）的独立入口，已形成稳定的导入关系：各消费者只依赖本子树的 Helpers。
2. 统一到 `backtestStyles.ts` 需要修改 10+ 个文件的 import 语句，变更面远大于消除的 3 行重复。
3. 这是功能性拆分（每个子树包含不同的功能组件）带来的轻度样式常量复制，属于可接受的拆分代价，不是"一次性抽象"或"过度工程化"。

按"如果不确定是否保持行为 — 不改，安全第一"和"不为了显示价值发明问题"的约束，跳过此项。

**HelpTip 在多个 Helpers 文件中重复**

`PerformanceHelpers.tsx`、`TradesHelpers.tsx`、`RobustnessTab.tsx`（R1 文件）、`OverviewGreyTab.tsx`（R1 文件）均定义了自己的 `HelpTip`，与 `components/qds/help-tip.tsx` 的官方版本**视觉不同**：本地版使用 `HelpCircle` 图标（Lucide），官方版使用 `?` 圆圈文字按钮。这是有意的视觉差异，不是无意的重复，因此不合并。

---

### 验证

- selftest：`bash src/web/scripts/verify-ds-compliance.sh --selftest` → **70 通过, 0 失败**
- 完整扫描：`bash src/web/scripts/verify-ds-compliance.sh` → **R1-R14 全部 ✓ no violations，Total: 0**
- R9 规则（scan_r9_v2）在删除 scan_r9 后仍正常工作，输出不变

---

### 摘要

检查了 R2 新增的 27 个文件及 kickback 修复代码。发现并删除了 `verify-ds-compliance.sh` 中遗留的未使用 `scan_r9()` 函数（38 行死代码，含内嵌 Python heredoc），该函数已被 `scan_r9_v2` 取代但未清理。其余代码结构清洁，重复的卡片样式常量属于有意识的子树拆分代价，不简化。

VerifyPass: code-simplifier
Verdict: PASS
