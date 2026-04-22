# Kickback Round 3 · Fix Directives

## Verdict
- **Verifier**: PASS（Round 2 kickback 9 条指令全部 VERIFIED；DS 双主题合规、build/tsc/vitest 全绿）
- **Code-Reviewer**: REQUEST CHANGES（Round 2 修复 FR-013 时在 `page.tsx` 新引入 2 处 React 19 rule errors，违反 AC-E-2 net-zero 底线）

本轮是单文件小修。Round 2 修复把 BacktestRunRow 侧的 FR-013 hint/shimmer 做对了，但 WS stale **检测逻辑**在 `src/web/src/app/backtest/page.tsx:40-59` 上引入了 2 处 `react-hooks/set-state-in-effect` error，Round 2 的 kickback 未覆盖同源问题，需补修。

---

## HIGH 必修

### FIX-H1 · `page.tsx` 两处 setState-in-effect（AC-E-2 阻塞）

**Severity**: HIGH · **Category**: COMPLIANCE (React 19 规则 + AC-E-2)

**File**: `src/web/src/app/backtest/page.tsx`

**Why**: FR-013 WS stale 检测需要两个 effect：
1. L47-52 订阅 `backtest.progress` WS 事件 → 把最新收到时间写入 `progressTimestamps` map
2. L55-59 每 3s 触发 `setNow(Date.now())` 驱动 stale 判定重算

这两个 effect 的 setState 都是业务必需（不是 derived state 能替代的 — 依赖外部 WS 事件流 + 定时器），但 React 19 的 `react-hooks/set-state-in-effect` 规则默认视为违规。kickback-r2 处理 Sheet/Step2/TradesView 时用了 eslint-disable 策略，`page.tsx` 这两处需要保持一致。

**当前 lint 状态**：
- 全仓 errors: 46（基线 44 + 本次 2 新增）
- backtest 本任务 scope errors: 2（**违反 AC-E-2 "task scope 0" 底线**）
- 目标：加 eslint-disable 后两者归 0

**Actions**:

在 `src/web/src/app/backtest/page.tsx:51` 上方加注释：
```tsx
useEffect(() => {
  if (!progressMsg) return;
  const raw = (progressMsg.data ?? progressMsg) as Record<string, unknown>;
  const rid = raw.run_id as string | undefined;
  // eslint-disable-next-line react-hooks/set-state-in-effect -- reason: record WS event timestamp to detect stale
  if (rid) setProgressTimestamps((prev) => ({ ...prev, [rid]: Date.now() }));
}, [progressMsg]);
```

在 `page.tsx:56` 上方加注释：
```tsx
// Tick every 3s to trigger stale re-evaluation. Init on client to avoid SSR hydration drift.
useEffect(() => {
  // eslint-disable-next-line react-hooks/set-state-in-effect -- reason: interval-driven tick for stale re-eval
  setNow(Date.now());
  const id = setInterval(() => setNow(Date.now()), 3000);
  return () => clearInterval(id);
}, []);
```

> 注意 L57 的 `setInterval(() => setNow(Date.now()), 3000)` 是定时器回调内 setState（不是 effect 主体内 setState），不会触发 `react-hooks/set-state-in-effect` 规则，无需额外 disable。

**验证**:
```bash
cd src/web && npm run lint 2>&1 | grep "backtest/page.tsx" 
# 期望：只命中文件名行（无 error 明细行），或完全无输出

cd src/web && npm run lint 2>&1 | tail -3
# 期望：全仓 errors 从 46 降回 44（baseline 保持）
```

---

## LOW（可选 · 不阻塞 PASS）

### FIX-L1（信息项）· BacktestSubscriptionTable.tsx 两处 inline `var(--acc-d)` focus ring

**File**: `src/web/src/app/backtest/components/BacktestSubscriptionTable.tsx:108, 216`

**Context**: code-reviewer r3 将此判为 HIGH 但**定性错误** — `--acc-d` 在 `globals.css` L56（dark）/L134（light）/L260（Tailwind 映射 `--color-qds-accent-dim`）均已正确定义，token 生效且 dark/light 切换正常。

**不是 token 未定义问题**，但确实是"内联 style 而非 Tailwind focus: 修饰符"，属于标准化优化项：

- L108 symbol input 焦点样式
- L216 timeframe input 焦点样式

**可选 Actions**（不在本轮必修范围）：
改为 `focus:border-primary focus:ring-2 focus:ring-primary/30` Tailwind class，但需保证视觉与 `box-shadow: 0 0 0 3px var(--acc-d)` 等价（3px 环 + dim 填充）。

**保留作为技术债**；本轮不修，仅记录以免下一轮 reviewer 再度误判。

### FIX-L2（信息项）· BacktestListView.tsx 备用分支 delay
L159/173/192 仍为 160ms，属于 loading skeleton 的备用路径，主路径 L131/L151 已改为 100/200ms（AC-D-1 主数据流达标）。Round 2 已判 VERIFIED，本轮跳过。

### FIX-L3（信息项）· BacktestCreateStepper.tsx L25 `isPending` unused warning
纯 warning（不是 error），不影响 AC-E-2。可顺手删掉或下轮清理。

---

## 并行策略建议

单文件单 executor 即可：

**波 A（1 executor）**：FIX-H1 — 编辑 `page.tsx:51, 56` 两处加 eslint-disable 注释 + 运行 lint 验证。

预估 30s 完成。

---

## 最终验证门

```bash
cd src/web && npm run lint 2>&1 | tail -3
# ✔ errors=44（baseline，不超过）

cd src/web && npm run build 2>&1 | tail -5
# ✔ 成功静态导出

cd src/web && npx tsc --noEmit; echo "exit=$?"
# ✔ exit=0

cd src/web && npx vitest run 2>&1 | tail -5
# ✔ 27 tests pass
```
