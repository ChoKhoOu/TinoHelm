# Kickback Round 1 · Fix Directives

## Verdict
- **Verifier**: FAIL (6 issues)
- **Code-Reviewer**: REQUEST CHANGES (14 issues: 0 Critical, 4 High, 7 Medium, 3 Low)

## 修复优先级（HIGH 必修 · MEDIUM 强烈建议 · LOW 可选）

---

### FIX-H1 · AC-E-4 单元测试缺失
**Severity**: HIGH · **Category**: COMPLETENESS
**Files**:
- `src/web/src/app/backtest/__tests__/` (目录不存在，需创建)
- `src/web/vitest.config.ts` (test.include pattern 未覆盖 src/**)

**Why**: AC-E-4 要求 `npx vitest run src/app/backtest/__tests__` exit 0。当前目录不存在，vitest 报 `No test files found`。

**Actions**:
1. 创建目录 `src/web/src/app/backtest/__tests__/`。
2. 新建 `BacktestTradesView.test.tsx`：mock 20 条 tradeLog（10 long + 10 short，5 win/5 loss），渲染组件，断言 `sideFilter="long" + resultFilter="win"` 联合过滤 `filtered.length === 5`；断言 useMemo 引用稳定性。
3. 新建 `OverviewEquitySvg.test.tsx`：`data={[]}` 渲染，断言 `<InlineError>` 文案「暂无权益曲线数据」出现且 `<svg>` 不渲染。
4. 新建 `BacktestCreateStepper.test.tsx`：`step={1|2|3}` 三态，断言 3 dot 的 className 分别匹配 `STEPPER_DOT_CLS_MAP.active/completed/pending`。
5. 修 `src/web/vitest.config.ts` 的 `test.include` 追加 `'src/**/__tests__/**/*.test.{ts,tsx}'`。
6. 验证：`cd src/web && npx vitest run src/app/backtest/__tests__` exit 0，至少 3 test files / 6+ tests 全绿。

---

### FIX-H2 · S13 AC 11 处 `qds-section-label` 裸 class 违规
**Severity**: HIGH · **Category**: COMPLIANCE (FR-NFR-004)
**Files (11 处)**:
- `BacktestCreateStep3.tsx:168, 191, 220, 267, 322, 386` (6 处)
- `BacktestCreateStep1.tsx:323, 386` (2 处)
- `OverviewHelpers.tsx:50` (1 处)
- `BacktestListView.tsx:193` (1 处)
- `BacktestSubscriptionTable.tsx:58` (1 处)

**Why**: S13 AC 明确 `grep -rn "qds-section-label" src/web/src/app/backtest/components/ | grep -v SectionLabel` 零命中。当前 11 处违规 + FR-NFR-004 同样禁止。

**Actions**:
1. 每个命中文件顶部追加 `import { SectionLabel } from "@/components/qds";`（若已有，跳过）。
2. 将所有 `<div className="qds-section-label ...">...</div>` 或 `<span className="qds-section-label">...</span>` 替换为 `<SectionLabel>...</SectionLabel>`。
3. 保留额外 Tailwind class（如 `mb-3`、`!mb-0`）：若 SectionLabel 支持 className prop，`<SectionLabel className="mb-3">`；否则用 wrapper `<div className="mb-3"><SectionLabel>...</SectionLabel></div>`。
4. 验证：`grep -rn "qds-section-label" src/web/src/app/backtest/components/ | grep -v SectionLabel` exit code 1（零命中）。

---

### FIX-H3 · FR-044 步骤跳转无校验
**Severity**: HIGH · **Category**: BUG (规格违反)
**File**: `src/web/src/app/backtest/components/BacktestCreateSheet.tsx:141` (`handleNext`)

**Why**: `handleNext()` 直接 `setStep`，strategy_name 为空、subscriptions 为空也可进 Step 2/3；可提交空策略。FR-044 禁止。

**Actions**:
1. 改造 `handleNext()` 加守卫：
   ```tsx
   const handleNext = () => {
     if (step === 1 && (!step1Form.strategy_name.trim() || subscriptions.length === 0)) return;
     if (step === 2 && (!step2Form.start_date || !step2Form.end_date ||
         new Date(step2Form.start_date) >= new Date(step2Form.end_date))) return;
     if (step < 3) setStep((s) => (s + 1) as 1 | 2 | 3);
   };
   ```
2. 在 footer「下一步」按钮附近（step=1/2 生效）渲染 `<InlineError variant="hint">` 显示缺字段提示（FR-075 inline 错误展示模式），或让按钮 disabled + 鼠标 hover tooltip。优先 disable 按钮。
3. 验证：Playwright e2e 增补测试 → 空策略 + 空 subscriptions 时 Next 按钮 disabled；填好后可进入 Step 2。

---

### FIX-H4 · strategyParams 永远为空（参数覆盖功能失效）
**Severity**: HIGH · **Category**: BUG (规格违反)
**File**: `src/web/src/app/backtest/components/BacktestCreateSheet.tsx:71`

**Why**: Sheet `strategyParams: ParamInfo[]` 始终为 `[]`，Step3 高级面板"策略参数覆盖"永远空白。旧 BacktestCreateView 会调用 `/api/strategies/{name}/params`。

**Actions**:
1. 在 `BacktestCreateSheet` 新增 useEffect 监听 `step1Form.strategy_name`：
   ```tsx
   useEffect(() => {
     if (!step1Form.strategy_name) { setStrategyParams([]); return; }
     apiGet<ParamInfo[]>(`/api/strategies/${encodeURIComponent(step1Form.strategy_name)}/params`)
       .then((d) => d && setStrategyParams(d))
       .catch(() => setStrategyParams([]));
   }, [step1Form.strategy_name]);
   ```
2. 确认 `ParamInfo` 类型从旧 BacktestCreateView（已删除）迁移到本文件或共享位置。
3. 验证：选策略后 Step3 高级区「策略参数覆盖」面板渲染非空。

---

### FIX-H5 · FR-041 Stepper completed dot 颜色语义错误
**Severity**: HIGH · **Category**: COMPLIANCE (FR-041)
**File**: `src/web/src/app/backtest/components/backtestStyles.ts:42`

**Why**: 当前 `completed: "bg-primary/60 text-primary-foreground"` 用橙色 60% 透明度。FR-041 要求 `--suc`（绿色）+ `Check` icon。

**Actions**:
1. `backtestStyles.ts:42` `completed: "bg-qds-success text-white"`（或 `text-qds-success-foreground` 视 shadcn 映射）。
2. `BacktestCreateStepper.tsx` 在 completed 状态下渲染 `<Check className="w-3 h-3" />` icon（从 `lucide-react` 导入）。
3. 验证：`BacktestCreateStepper.test.tsx`（FIX-H1 新建）断言 step=2 时 dot-1 className 含 `bg-qds-success` 且内部有 `<Check>` svg。

---

### FIX-H6 · FR-013 WS 降级从未接线
**Severity**: HIGH · **Category**: BUG (规格违反)
**File**: `src/web/src/app/backtest/page.tsx`

**Why**: `isWsStale` prop 始终未在 page 层计算，`BacktestListView` 接收 `false`。FR-013 要求 WS 断连且 running 行超 15s 无进度更新时暂停动画 + 显示 warning hint。

**Actions**:
1. 在 `page.tsx` 引入 `useWsConnection()`（或等价，参考 `providers/WebSocketProvider`）获取 ws 连接状态。
2. 追踪每个 running run 的 `lastProgressAt: Record<run_id, timestamp>`（每次收到 WS progress 事件更新）。
3. 计算 `isWsStale = wsStatus !== "connected" || (now - lastProgressAt[runId]) > 15000`。
4. 透传给 `<BacktestListView>`。
5. 验证：Playwright 模拟 WS 断连 → running 行外层 `data-ws-stale="true"` + RingProgress 动画 pause。

---

### FIX-H7 · S9 AC 提交成功后 Sheet 不关闭
**Severity**: HIGH · **Category**: BUG (S9 AC-6)
**File**: `src/web/src/app/backtest/components/BacktestCreateStep3.tsx:157`

**Why**: S9 AC 规定「成功后调用 `onSubmit()` + `onOpenChange(false)` + `loadRuns()`」。当前 `useAction.onSuccess` 仅 `onSubmit?.()`，未关闭 Sheet，需手动点取消。

**Actions**:
1. `BacktestCreateStep3Props` 新增 `onOpenChange?: (open: boolean) => void` prop。
2. `BacktestCreateSheet.tsx` Step3 调用处追加 `onOpenChange={onOpenChange}` 透传。
3. `BacktestCreateStep3.tsx:157` `onSuccess: () => { onSubmit?.(); onOpenChange?.(false); }`。
4. 验证：Playwright create-sheet.spec.ts 扩展 → 填完 step3 + 提交 → 断言 `[data-slot="sheet-content"]` 消失。

---

## MEDIUM（强烈建议）

### FIX-M1 · FR-091 OverviewEquitySvg equity 颜色偏离
**File**: `src/web/src/app/backtest/components/OverviewEquitySvg.tsx:120-125, 161-162`

**Why**: FR-091 规定 equity stroke `var(--info)`（蓝），实现用 `var(--suc)`（绿），与盈亏 PnL 语义混淆。

**Actions**:
1. `:120-121` gradient `stopColor="var(--suc)"` → `stopColor="var(--info)"`；opacity `0.18→0.02` → `0.30→0.02`。
2. `:161` `stroke="var(--suc)"` → `stroke="var(--info)"`。
3. `:162` `strokeWidth="1.8"` → `strokeWidth="1.5"`。
4. 验证：`grep "var(--info)" OverviewEquitySvg.tsx` ≥ 3；`grep "var(--suc)" OverviewEquitySvg.tsx` 零命中。

### FIX-M2 · RingProgress 颜色偏离 tech-design §4.1
**File**: `src/web/src/app/backtest/components/BacktestRunRow.tsx:47`

**Why**: tech-design §4.1 §198 规定 `stroke="var(--info)"`（蓝），实现用 `var(--acc)`（橙）。

**Actions**:
1. `:47` `stroke="var(--acc)"` → `stroke="var(--info)"`。

### FIX-M3 · parseTimeframe DRY 违反
**Files**: `BacktestCreateStep1.tsx:56` + `BacktestCreateStep2.tsx:18`

**Actions**:
1. 提取到 `backtestStyles.ts` 或新建 `backtestUtils.ts`。
2. 两处 import 后删除内联定义。

### FIX-M4 · BacktestCreateStep3 typeColors inline style 违反标准化
**File**: `BacktestCreateStep3.tsx:109-113, 360`

**Actions**:
1. 替换 `style={{ color: typeColors[p.type] || "var(--t2)" }}` 为条件 Tailwind class：
   ```tsx
   const typeColorCls = p.type === "float" ? "text-qds-info" :
     p.type === "int" ? "text-qds-success" :
     p.type === "bool" ? "text-qds-warning" : "text-muted-foreground";
   ```

### FIX-M5 · BacktestTradesView PillTab shadow 硬编码 rgba
**File**: `BacktestTradesView.tsx:200`

**Actions**:
1. `shadow-[0_1px_3px_rgba(0,0,0,0.15)]` → `shadow-sm`（与 DetailView tab bar 一致）。

### FIX-M6 · page.tsx strategies 死代码
**File**: `page.tsx:40-55`

**Why**: `strategies` state + fetch effect + `void strategies` 抑制 lint，未下传任何子组件。Step1 内部已自行 fetch。造成重复 API 调用。

**Actions**:
1. 删除 `strategies` state、`BacktestStrategyInfo` interface、对应 useEffect（~12 行）。

---

## LOW（可选）

### FIX-L1 · E2E 覆盖度不足
扩展 4 个 spec 覆盖 AC-B-1/B-2/B-3/B-4/B-6/B-7/B-8。目标 ≥ 18 tests 全绿。

### FIX-L2 · Sheet step wrapper key 冗余
`BacktestCreateSheet.tsx:176, 191, 201` 互斥条件下 `key` 无 diff 语义 → 删除冗余。

### FIX-L3 · Step2 顶层 padding 缺失
`BacktestCreateStep2.tsx:170` 顶层追加 `px-6 py-5`。

### FIX-L4 · OverviewEquitySvg buildPath/buildAreaPath 冗余参数
移除 `viewW` / `viewH` 参数。

---

## 并行策略建议

**波 A（独立文件，可 4 个并行）**：
- FIX-H1 (新建 __tests__ + 改 vitest.config.ts)
- FIX-H2 + FIX-M4 + FIX-M6 (qds-section-label 批量替换 + typeColors inline style + page.tsx 死代码)
- FIX-H3 + FIX-H4 + FIX-H7 (BacktestCreateSheet.tsx 修改 — 合并到单 executor 避免冲突)
- FIX-H5 (backtestStyles.ts + BacktestCreateStepper.tsx)

**波 B（依赖波 A 后运行）**：
- FIX-H6 (page.tsx WS 降级接线) + FIX-M3 (parseTimeframe 提取)
- FIX-M1 + FIX-M2 (颜色修复 OverviewEquitySvg + BacktestRunRow)
- FIX-M5 + FIX-L 系列（扫尾）

**波 C（最终验证）**：
- 跑 vitest + playwright + DS 合规 + build
