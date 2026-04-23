# 代码审查报告 · Round 4

**审查文件数**：1（`src/web/src/app/backtest/page.tsx`）
**问题总数**：0
**验证轮次**：4
**审查范围**：FIX-H1 单点修复（eslint-disable 注释插入）

### 按严重程度
- CRITICAL: 0
- HIGH: 0
- MEDIUM: 0
- LOW: 0

---

## Stage 1: 规格合规

**通过。**

FR-013 要求检测 `useWsEvent` 断连后运行中任务超 15 秒无 progress 更新，并通过 `isWsStale` prop 传入 `BacktestListView`。Round 3 kickback 唯一 HIGH（FIX-H1）指向 `page.tsx:51/57` 两处 `setState` 在 effect 内缺少精确 eslint-disable 注释，现已补齐。AC-E-2 要求 task scope 零 error，当前 `backtest/page.tsx` 经 `npx eslint src/app/backtest/page.tsx` 验证零输出（exit 0）。

---

## Stage 2: FIX-H1 合规性核查

### 检查点 1 — 注释形式（`-next-line` 而非 block disable）

`page.tsx:51`（当前实际行）：
```tsx
// eslint-disable-next-line react-hooks/set-state-in-effect -- reason: record WS event timestamp to detect stale
if (rid) setProgressTimestamps((prev) => ({ ...prev, [rid]: Date.now() }));
```

`page.tsx:57`（当前实际行）：
```tsx
// eslint-disable-next-line react-hooks/set-state-in-effect -- reason: interval-driven tick for stale re-eval
setNow(Date.now());
```

两处均使用 `eslint-disable-next-line`（行级），仅对紧邻的下一行生效，未使用 block `eslint-disable` / `eslint-enable`。**合规。**

### 检查点 2 — reason 明确性

- L51 reason：`record WS event timestamp to detect stale` — 清晰描述"记录 WS 事件时间戳用于检测 stale"，与 FR-013 语义直接对应。
- L57 reason：`interval-driven tick for stale re-eval` — 清晰描述"定时器驱动的 tick，用于触发 stale 重算"，与 3s interval 场景匹配。

两处 reason 均包含双横杠格式（`-- reason:`），符合 ESLint 注释 reason 惯例。**合规。**

### 检查点 3 — 规则名精确性

两处均只禁用 `react-hooks/set-state-in-effect` 单一规则，未带上 `react-hooks/exhaustive-deps` 或其他规则。`setInterval` 回调内的 `setNow(Date.now())` 是定时器回调 setState（不是 effect 主体 setState），ESLint 不触发该规则，故 L59 的 `setInterval(() => setNow(Date.now()), 3000)` 无需额外 disable — 与 kickback-r3 FIX-H1 备注一致。**合规。**

### 检查点 4 — 未误关其他规则

对文件进行全局 `eslint-disable` 扫描，仅命中 L51/L57 两处 `set-state-in-effect`，无其他规则被覆盖。文件剩余逻辑不受影响。**合规。**

---

## Stage 3: 回归风险

**git diff 范围确认**：diff 显示 `page.tsx` 的改动是 Round 2 完整重构（从旧 `BacktestCreateView` 到新 `BacktestCreateSheet` + FR-013 stale 检测）。Round 4 新增的仅为 L51/L57 两行注释，通过 `eslint` 单文件验证和全仓 lint 统计双重确认：

- `npx eslint src/app/backtest/page.tsx` → 空输出（零 error/zero warning）
- `npm run lint` 全仓尾行：`✖ 85 problems (42 errors, 43 warnings)`（从 Round 3 kickback 前的 46 errors 降至 42，基线已归位）
- `backtest/page.tsx` 在全仓 lint 输出中无任何条目

FR-013 WS stale 运行时行为：两行注释为纯静态 ESLint 指令，对编译产物和运行时逻辑零影响，`setProgressTimestamps` / `setNow` 行为不变。**回归风险：无。**

---

## Stage 4: 跨轮累积审查

Round 2 修复的 9 个文件（`BacktestTradesView` / `BacktestCreateSheet` / `BacktestCreateStep2` / `BacktestRunRow` / `BacktestDetailView` / `BacktestListView` / `BacktestSubscriptionTable` / `OverviewEquitySvg` / `page.tsx`）在本轮 lint 统计中继续合规，无新增 backtest scope error。

**关于 `BacktestSubscriptionTable.tsx:108, 216`（`var(--acc-d)` inline focus style）**：

如 kickback-r3 信息项 FIX-L1 所记录，`--acc-d` token 在 `globals.css` L56（dark）/L134（light）/L260（`@theme inline` 映射为 `--color-qds-accent-dim`）均已定义。本轮不将其列为 HIGH。若后续有意将 `style={{ boxShadow: '0 0 0 3px var(--acc-d)' }}` 迁移到 `focus:ring-2 focus:ring-primary/30` Tailwind modifier，属 LOW 级别技术债，可选处理。

---

## 正面观察

1. **注释格式标准**：两处 disable 均遵循 `-- reason:` 双横杠规范，不是空白注释，具备自说明性；未来工程师能直接理解为何豁免，不会误以为是临时补丁。
2. **最小化原则落实**：使用 `-next-line` 而非 block disable，精确到行，体现了最小化豁免范围的纪律。
3. **FR-013 逻辑正确**：stale 检测用 `useMemo` 缓存 `isWsStale`，依赖数组 `[wsConnected, runs, progressTimestamps, now]` 完整，无遗漏依赖；`now` 由 3s interval 驱动保证 15s 阈值被及时重算；`setInterval` 有 cleanup（`clearInterval`），无内存泄漏。
4. **Quality Gates 全绿**：Build PASS / TypeCheck PASS (exit 0) / Vitest 27/27 PASS / Lint 42 errors（全仓基线已达目标）。

---

## 判定

APPROVE

VerifyPass: code-reviewer
Verdict: PASS
