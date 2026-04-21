# 代码审查报告 · Round 3

**任务**: backtest-page-visual-refactor
**审查时间**: 2026-04-21
**轮次**: Round 3（审查 Round 2 kickback 的 9 项修复）

---

## 审查文件数：9
## 问题总数：3

### 按严重程度
- CRITICAL: 0
- HIGH: 2（必须修复）
- MEDIUM: 0
- LOW: 1（可选）

---

## Stage 1: 规格合规

### FR-013 WS stale hint + 底部 shimmer 暂停

**验证路径**: `BacktestRunRow.tsx`

- `isWsStale` prop 已正确穿透到展开区（`data-ws-stale={isWsStale ? "true" : "false"}`）
- Progress meta cell 追加 `<span className="text-qds-warning text-[0.6rem] ml-1">· 连接待恢复</span>` — 已实现（L322-324）
- 底部 3px shimmer 条（非展开态）：`{!isWsStale && <div className="absolute inset-0 animate-qds-shimmer ...">}` — 已实现（L285-289）
- `<ShimmerBar active={!isWsStale}>` 展开区暂停 — 已实现（L305）
- **结论：FR-013 完整实现通过**

### FR-033 重试预填字段

**验证路径**: `BacktestCreateSheet.tsx:96-123`

Effect 中预填 `strategy_name`、`symbol`/`interval`（via subscriptions）、`start_date`/`end_date`，不预填 step 3 字段 — 符合 FR-033 表格规范。

`InlineError` hint 在 `fromRetry === true` 时渲染（L189-195），文案"已复制策略、标的、周期与时间区间，请确认资金与成本参数" — 符合规格。

**结论：FR-033 实现通过**

### FR-091 equity dash 动画参数

**验证路径**: `OverviewEquitySvg.tsx:104`

```ts
animation: `dash 1.8s 0.1s var(--eo, ease-out) forwards`
```

时长 1.8s、延迟 0.1s、easing `var(--eo)`、fill-mode `forwards` — 完全符合 FR-091 规格。

**结论：FR-091 实现通过**

### AC-D-1 ListView fade-up delay

**验证路径**: `BacktestListView.tsx:131, 151`

- L105: `[animation-delay:0ms]` — 头部，0ms ✓
- L131: `[animation-delay:100ms]` — 统计条，100ms ✓（FIX-M3 已修复，从 80ms → 100ms）
- L151: `[animation-delay:200ms]` — 表格容器，200ms ✓（FIX-M3 已修复，从 160ms → 200ms）

**结论：AC-D-1 通过**

### AC-E-2 本任务 scope lint errors

Round 2 直接修改/新增的文件集合：

- `BacktestTradesView.tsx` — PillTab 提升到模块级（L112），`setCurPage(1)` eslint-disable 注释（L169）— **0 new errors**
- `BacktestCreateSheet.tsx` — `setStep1Form` 和 `setStrategyParams` 均加 eslint-disable 注释（L100, L127）— **0 new errors**
- `BacktestCreateStep2.tsx` — `setEstimate(null)` 加 eslint-disable 注释（L135）— **0 new errors**
- `BacktestRunRow.tsx`、`BacktestDetailView.tsx`、`BacktestListView.tsx`、`BacktestSubscriptionTable.tsx`、`OverviewEquitySvg.tsx` — **0 new errors**

**新引入 errors（page.tsx 中的 FR-013 实现）**: 见 Issue 列表。

---

## 问题列表

---

### [HIGH] page.tsx L51 — FR-013 WS stale 实现引入 2 处 `react-hooks/set-state-in-effect` error，违反 AC-E-2

**File**: `src/web/src/app/backtest/page.tsx:51, 56`

**Issue**: Round 2 实现 FR-013 的 WS stale 检测时，在两个 `useEffect` 内直接调用 setState：

```tsx
// L47-52: progressMsg effect
useEffect(() => {
  if (!progressMsg) return;
  const raw = (progressMsg.data ?? progressMsg) as Record<string, unknown>;
  const rid = raw.run_id as string | undefined;
  if (rid) setProgressTimestamps((prev) => ({ ...prev, [rid]: Date.now() }));
  //         ^^^^^^^^^^^^^^^^^^^^^ Avoid calling setState() directly within an effect
}, [progressMsg]);

// L55-59: timer tick effect
useEffect(() => {
  setNow(Date.now());  // ← error: setState in effect
  const id = setInterval(() => setNow(Date.now()), 3000);
  return () => clearInterval(id);
}, []);
```

当前 lint 输出报告全仓 46 errors（较 Round 1 kickback 时的 44 errors 多出 2 个），**这 2 个新增 error 均在 page.tsx 中，由 Round 2 引入**，违反 AC-E-2「backtest 模块本任务 scope 0 errors」。

其他 7 个 backtest 模块 errors（`OverviewTab.tsx:46`、`PerformanceTab.tsx:38`、`TradesTab.tsx:37`、`RobustnessTab.tsx:497`、`ReportsTab.tsx:130`、`TradeLogTab.tsx:160`、`useBacktestDetail.ts:25`）均为 pre-existing，存在于 HEAD committed baseline，不在本轮范围内。

**Fix 选项**:

**选项 A（最小改动，与 kickback-r2 中 FIX-H1-b 保持一致）**：在两处 setState 上方各加精确 eslint-disable 注释：

```tsx
// L51 上方:
// eslint-disable-next-line react-hooks/set-state-in-effect -- reason: WS event handler recording timestamp for stale detection
if (rid) setProgressTimestamps(...)

// L56 上方:
// eslint-disable-next-line react-hooks/set-state-in-effect -- reason: init clock tick on mount, setInterval subscription
setNow(Date.now());
```

**选项 B（derived state，推荐长期）**: 将 `now` 移除 state，改为在 `isWsStale` useMemo 内用 `Date.now()` 直接计算（依赖 `progressTimestamps` 变化触发），timer 改为通过 `useRef` + `forceUpdate` 模式。L56 的 `setNow` 初始化可删除（useMemo 首次执行时自动计算），L57 的 interval 保留触发 re-render。

**验证命令**:
```bash
npm run lint 2>&1 | grep "src/app/backtest/page.tsx" | wc -l  # 应为 0
```

---

### [HIGH] BacktestSubscriptionTable.tsx L108-110 — focus/blur 使用 inline style 设置 boxShadow，含 `var(--acc-d)` 未定义 token

**File**: `src/web/src/app/backtest/components/BacktestSubscriptionTable.tsx:108-110`

**Issue**: symbol input 的 `onFocus`/`onBlur` 事件处理器通过 `e.currentTarget.style.borderColor` / `e.currentTarget.style.boxShadow` 设置内联样式：

```tsx
onFocus={(e) => {
  e.currentTarget.style.borderColor = "var(--acc)";
  e.currentTarget.style.boxShadow = "0 0 0 3px var(--acc-d)";
}}
onBlur={(e) => {
  e.currentTarget.style.borderColor = "var(--bd)";
  e.currentTarget.style.boxShadow = "none";
}}
```

其中 `var(--acc-d)` 是未知 token（globals.css 中仅存在 `var(--acc)` 和 `bg-qds-accent-dim`，无 `--acc-d` 短 token），在 dark/light 双主题下该 token 可能解析失败。同类代码还出现在 L216-217（timeframe input）。DS 合规扫描器若覆盖内联 style，应报此类问题。

此问题属于 Round 2 实现范围内（`BacktestSubscriptionTable.tsx` 为本任务修改文件），但 FIX-M2 仅修复了 dropdown 的 `style={{ boxShadow: "..." }}`，未处理 onFocus/onBlur 中的 imperatively-set style。

**Fix**:
1. 改用 Tailwind `focus:border-primary focus:ring-2 focus:ring-primary/30` class（在 `className` 中声明），删除 `onFocus`/`onBlur` 中的 style 操作。
2. 或保留 focus ring 逻辑，但将 `var(--acc-d)` 替换为 `var(--acc)/30`（CSS color-mix 或固定透明度值）确保 token 存在。

**验证命令**:
```bash
grep -n "var(--acc-d)\|acc-d" src/web/src/app/backtest/components/BacktestSubscriptionTable.tsx  # 应零命中
```

---

### [LOW] BacktestListView.tsx — summary strip 的 dot/label 颜色使用 inline `style={{ background, color: "var(...)" }}`

**File**: `src/web/src/app/backtest/components/BacktestListView.tsx:141-143`

```tsx
<div className="w-2 h-2 rounded-full" style={{ background: item.color }} />
<span style={{ color: item.color }}>{item.label}</span>
```

其中 `item.color` 为 `"var(--info)"` / `"var(--suc)"` / `"var(--dan)"` / `"var(--t3)"`。使用 inline `style` 而非 Tailwind 语义类（`text-qds-info`/`text-qds-success`/`text-destructive`/`text-qds-t3`）违反 DS 标准化规则。动态颜色映射可用条件 className 替代。

这是 pre-existing 模式（非 Round 2 新引入），属于视觉等价但不符合"内联 style 消灭"规则的 LOW 级别问题。不阻塞当前轮次。

---

## 正面观察

1. **FR-013 完整实现** — `BacktestRunRow.tsx` 的 shimmer 暂停逻辑（`!isWsStale` 条件渲染）和"连接待恢复"文字 hint 均已落地，语义正确、代码简洁。

2. **PillTab 模块级提升（FIX-H1-a）** — `BacktestTradesView.tsx` 的 `PillTab` 组件已从函数体内提升至文件顶部（L112），完全符合 React Hooks rules，eslint-disable 不再必要。

3. **eslint-disable 注释质量高** — `BacktestCreateSheet.tsx` 和 `BacktestCreateStep2.tsx` 中的 3 处 eslint-disable 均附带清晰的 `-- reason:` 理由，符合 kickback-r2 中 FIX-H1-b 推荐的"最小改动"路径规范。

4. **key-based remount 正确实现（FIX-H1-b.1）** — `page.tsx:133` 的 `<BacktestCreateSheet key={sheetOpen ? (retryPrefill?.run_id ?? "new") : "closed"}>` 逻辑正确：retry 时 key 为 `run_id`（唯一隔离），普通创建为 `"new"`，关闭为 `"closed"`，保证三种状态各自独立 remount。

5. **FIX-M1/M2/M4 全部到位** — `BacktestDetailView.tsx:224` tab bar 已改 `shadow-sm`；`BacktestSubscriptionTable.tsx:130` dropdown 已改 `shadow-2xl`；`OverviewEquitySvg.tsx:104` 动画参数精确符合 FR-091（1.8s / 0.1s / var(--eo, ease-out) / forwards）。

6. **FR-033 预填边界清晰** — `BacktestCreateSheet.tsx` 的重试预填 effect 严格只填 step1（strategy_name）、subscriptions（symbol + interval）和 step2 dates，不填 step3 资金/费率字段，完全符合 FR-033 字段清单。

7. **AC-D-1 延迟已精确对齐** — `BacktestListView.tsx` 三层 fade-up delay 已调整为 `0ms / 100ms / 200ms`，与规格完全一致。

---

## 判定

**REQUEST CHANGES**

两个 HIGH 问题：
- **page.tsx 新增 2 处 lint errors**（违反 AC-E-2）— 必须加 eslint-disable 注释消除
- **BacktestSubscriptionTable.tsx 使用 `var(--acc-d)` 未定义 token**（focus ring 内联 style）— 必须修复为已知 token 或改用 Tailwind focus class

其余 7 个 backtest lint errors（`OverviewTab.tsx` 等）为 pre-existing，不在 Round 2 修复范围内，不影响本轮判定。

LOW 问题（summary strip inline style）不阻塞，待后续迭代清理。

---

VerifyPass: code-reviewer
Verdict: FAIL
