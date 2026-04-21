# Kickback Round 2 · Fix Directives

## Verdict
- **Verifier**: FAIL（AC-E-2 lint errors 未达成）
- **Code-Reviewer**: REQUEST CHANGES（FR-013 未完整实现 + shadow 硬编码残留）

Round 1 kickback 的 17 项指令已 **全部验证通过**（见 verify/report-verifier-r2.md）。本轮新识别的缺陷都是 Round 1 修复过程中**新引入**或**Round 1 未覆盖到**的次级问题。

## HIGH 必修

---

### FIX-H1 · 6 处新增 lint errors（AC-E-2 阻塞）

**Severity**: HIGH · **Category**: COMPLIANCE (React 19 规则)

Round 1 修复时新引入了 ~6 处 lint errors，使 `npm run lint` exit 1。虽然全仓 lint error 从 58 降到 56（净减 2），但 backtest 模块新增的 errors 属规格违反 AC-E-2「0 error」。

#### H1-a · `BacktestTradesView.tsx` 内嵌 `PillTab` 组件违反 `react-hooks/static-components`

**File**: `src/web/src/app/backtest/components/BacktestTradesView.tsx:183-207` (PillTab 定义) 以及使用点 `:271-273, 278-280`（共 4 lint errors）

**Why**: `function PillTab<T extends string>({...}) {...}` 定义在 `BacktestTradesView` 组件内部，React 19 规则禁止 — 每次父组件 render 会重建 PillTab 导致 state 丢失 + 性能问题。

**Actions**:
1. 把 `function PillTab<T extends string>({...})` 整块从 `BacktestTradesView` 组件内（行 183-207）提升到文件顶部（在 `BacktestTradesView` 函数定义之前）成为模块级独立组件。
2. 泛型参数 `T` 由 TypeScript 自动推断，调用处（L271-273, 278-280）保持不变。
3. 可选：对 PillTab 加 `memo()` 减少 re-render。
4. 验证：`grep -n "^function PillTab\|const PillTab =" src/web/src/app/backtest/components/BacktestTradesView.tsx` 命中位置应在 `export function BacktestTradesView` 之前。

#### H1-b · 4 处 `react-hooks/set-state-in-effect`

**Files + Lines**:
- `BacktestCreateSheet.tsx:100` `setStep(1)`（retryPrefill useEffect）
- `BacktestCreateSheet.tsx:127` `setStrategyParams([])`（strategy_name useEffect）
- `BacktestCreateStep2.tsx:135` `setEstimate(null)`（estimate useEffect 内 early-return 分支）
- `BacktestTradesView.tsx:139` `setCurPage(1)`（filter reset useEffect）

**Why**: React 19 规则禁止 effect 中调用 setState —— 要么 derive from props/state，要么 key-based remount。

**Actions（按最简修复路径）**:

1. **`BacktestCreateSheet.tsx:100` `setStep(1)`**:
   - 选项 A（推荐，最 robust）：在 `page.tsx` 使用 key-based remount：
     ```tsx
     <BacktestCreateSheet
       key={sheetOpen ? (retryPrefill?.run_id ?? "new") : "closed"}
       open={sheetOpen}
       ...
     />
     ```
     Sheet 内部的 `const [step, setStep] = useState<1 | 2 | 3>(1)` 初始值已为 1，key 变更会 remount 实现"打开/retry 都从 step 1 开始"。然后删除 retryPrefill useEffect 里的 `setStep(1)` 行。
   - 选项 B（最小改动）：保留现有代码，在该行上方加 `// eslint-disable-next-line react-hooks/set-state-in-effect` 注释，并附 `// reason: explicit reset to step 1 on new retry flow`。

2. **`BacktestCreateSheet.tsx:127` `setStrategyParams([])`**:
   - 选项 A（derived state）：改 `strategyParams` 为 `{ data, loadedFor }` 结构 + useMemo derive 当前 params：
     ```tsx
     const [paramsState, setParamsState] = useState<{ data: ParamInfo[]; loadedFor: string | null }>({ data: [], loadedFor: null });
     const strategyParams = useMemo(
       () => (paramsState.loadedFor === step1Form.strategy_name ? paramsState.data : []),
       [paramsState, step1Form.strategy_name],
     );
     useEffect(() => {
       if (!step1Form.strategy_name) return;
       apiGet<ParamInfo[]>(`/api/strategies/${encodeURIComponent(step1Form.strategy_name)}/params`)
         .then((d) => d && setParamsState({ data: d, loadedFor: step1Form.strategy_name }))
         .catch(() => setParamsState({ data: [], loadedFor: step1Form.strategy_name }));
     }, [step1Form.strategy_name]);
     ```
   - 选项 B（最小改动）：在 `setStrategyParams([])` 上方加 eslint-disable 注释。

3. **`BacktestCreateStep2.tsx:135` `setEstimate(null)`**:
   - 选项 A（derived）：把 estimate 改 useMemo 形式（依赖 debounce result + canEstimate flag）。
   - 选项 B（最小改动）：加 eslint-disable 注释。

4. **`BacktestTradesView.tsx:139` `setCurPage(1)`（filter 变化时重置分页）**:
   - 选项 A（key-based reset）：把分页相关 state 提到子组件 + 用 `<Pagination key={\`${sideFilter}-${resultFilter}-${search}\`}>`。
   - 选项 B（最小改动）：加 eslint-disable 注释。

**验证**:
- `cd src/web && npm run lint 2>&1 | grep "src/app/backtest/components/\(BacktestTradesView\|BacktestCreateSheet\|BacktestCreateStep2\)" | wc -l` **应为 0**（本任务引入的 errors 清零）。
- pre-existing 48 errors（optimization/watchlist/orders/analytics/notification-router/RobustnessTab/use-action/settings 等）**维持不变**，超出本任务范围。

---

### FIX-H2 · FR-013 "连接待恢复" hint 文字 + 底部 shimmer 暂停未完成

**Severity**: HIGH · **Category**: COMPLIANCE (FR-013)
**File**: `src/web/src/app/backtest/components/BacktestRunRow.tsx:285, 316-318`

**Why**: FR-013 规定 WS stale 时：
1. Progress meta cell 追加 "· 连接待恢复" hint 文字（`text-qds-warning`）
2. 底部 3px shimmer 条（非展开态）在 stale 时同样暂停动画

当前实现：
- ✅ `data-ws-stale` 属性已透传到展开区
- ✅ 展开区的 `<ShimmerBar active={!isWsStale}>` 正确暂停
- ❌ **缺失**："连接待恢复" hint 文字
- ❌ **缺失**：非展开态底部 shimmer 条（L285 附近的 `animate-qds-shimmer` 层）在 stale 时仍动

**Actions**:

1. **Progress meta cell 追加 hint**（行 316-318 附近，在 Progress 数值之后）:
   ```tsx
   <span>{progress}%</span>
   {isWsStale && (
     <span className="text-qds-warning text-[0.6rem] ml-1">· 连接待恢复</span>
   )}
   ```

2. **底部 shimmer 条条件渲染**（行 285 附近，非展开 running 状态的 shimmer overlay）:
   ```tsx
   {!isWsStale && (
     <div className="absolute inset-0 animate-qds-shimmer pointer-events-none">
       ...
     </div>
   )}
   ```
   或者保留结构但用 `data-[ws-stale=true]:[animation-play-state:paused]` 选择器配合外层 `data-ws-stale` 属性。

**验证**:
- `grep -n "连接待恢复" src/web/src/app/backtest/components/BacktestRunRow.tsx` ≥ 1 命中。
- Playwright 可扩展测试：mock `isWsStale=true` → DOM 含 "连接待恢复" 文字。

---

## MEDIUM 建议修复

### FIX-M1 · BacktestDetailView tab bar shadow rgba → shadow-sm

**File**: `src/web/src/app/backtest/components/BacktestDetailView.tsx:224`

**Why**: Round 1 FIX-M5 修了 BacktestTradesView 的同类 PillTab shadow，但 DetailView 的 tab bar 同样存在 `shadow-[0_1px_3px_rgba(0,0,0,0.15)]`，Round 1 未覆盖。DS 扫描器不扫描 arbitrary shadow rgba，但 dark/light 主题切换时阴影不自适应。

**Actions**:
1. `BacktestDetailView.tsx:224` `shadow-[0_1px_3px_rgba(0,0,0,0.15)]` → `shadow-sm`。

**验证**: `grep -n "shadow-\[0_1px_3px_rgba" src/web/src/app/backtest/components/BacktestDetailView.tsx` 零命中。

### FIX-M2 · BacktestSubscriptionTable boxShadow rgba

**File**: `src/web/src/app/backtest/components/BacktestSubscriptionTable.tsx:131`

**Why**: `style={{ boxShadow: "0 12px 40px rgba(0,0,0,.15)" }}` 是内联 style + 硬编码 rgba，违反"内联 style 消灭" + NFR-2 精神。

**Actions**:
1. 改为 Tailwind class `shadow-2xl` 或 `shadow-xl`（视视觉对齐选择；优先 `shadow-2xl` 保持 12px blur 感）。
2. 删除该 div 的 `style={{ boxShadow }}`。

**验证**: `grep -n 'boxShadow.*rgba' src/web/src/app/backtest/components/BacktestSubscriptionTable.tsx` 零命中。

### FIX-M3 · AC-D-1 ListView fade-up delay 偏差（80ms vs 100ms）

**File**: `src/web/src/app/backtest/components/BacktestListView.tsx:131, 151`

**Why**: AC-D-1 字面要求 ListView 三层 fade-up delay `0ms / 100ms / 200ms`，当前 `0ms / 80ms / 160ms`（与 DetailView AC-D-2 节拍一致，但 AC-D-1 独立要求）。

**Actions**:
1. L131 `[animation-delay:80ms]` → `[animation-delay:100ms]`。
2. L151 `[animation-delay:160ms]` → `[animation-delay:200ms]`。

**注**：此偏差 Round 1 未标，现升为 MEDIUM。如用户认为 80/160 节拍在跨视图一致性上更好，可保持现状并在 requirements 更新（但默认按 AC 字面修复）。

### FIX-M4 · OverviewEquitySvg dash 动画时长偏离规格

**File**: `src/web/src/app/backtest/components/OverviewEquitySvg.tsx:104`

**Why**: FR-091 规定 `dash 1.8s 0.1s var(--eo) forwards`，当前 `dash 2s ease forwards`。

**Actions**:
1. `animation: \`dash 2s ease forwards\`` → `animation: \`dash 1.8s 0.1s var(--eo, ease-out) forwards\``。
2. 确认 `--eo` 已在 globals.css 定义（通过 grep）；若未定义用 fallback `ease-out`。

---

## LOW（可选）

### FIX-L1 · BacktestDetailView / SubscriptionTable 以外其他 inline boxShadow rgba 扫描
主 agent 可跑 `grep -rn "boxShadow.*rgba\|shadow-\[.*rgba" src/web/src/app/backtest/`，补充修复其他类似硬编码。

### FIX-L2 · E2E 覆盖度扩展到 AC-B-2/B-3/B-4/B-6/B-7 共 ≥18 tests
独立技术债任务，不阻塞本轮。

---

## 并行策略建议

**波 A（3 并行）**：
- A1: FIX-H1-a（PillTab 提升到模块级）+ FIX-H1-b.4（TradesView setCurPage eslint-disable 或 key-based） — 纯 BacktestTradesView.tsx
- A2: FIX-H1-b.1 + FIX-H1-b.2（Sheet 两处 setState in effect） + FIX-M2（SubscriptionTable boxShadow） — 独立文件集合
- A3: FIX-H1-b.3（Step2 setEstimate）+ FIX-H2（BacktestRunRow 连接待恢复 hint + 底部 shimmer） + FIX-M1（DetailView shadow-sm）+ FIX-M3（ListView delay） + FIX-M4（Equity dash） — 多小改动合并

**波 B（最终验证）**：
- `npm run lint` 对 backtest 模块 0 error（pre-existing 48 维持）
- `npm run build` / `tsc` / vitest / Playwright / DS 合规全绿
