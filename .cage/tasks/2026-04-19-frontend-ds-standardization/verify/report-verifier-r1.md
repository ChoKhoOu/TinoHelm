# 验证报告 — 前端 DS 标准化（Round 1）

任务: `2026-04-19-frontend-ds-standardization`
验证轮次: 1
验证时间: 2026-04-20
验证者: verifier

---

## 总体判定

**状态**: FAIL
**置信度**: High

虽然 12 个 subtasks 全部被标记为 `done` 且合规扫描脚本（R1-R14 + preflight + both-themes + selftest）全部通过，但有 **三个独立的、可自动化捕获的 FAIL 项**，其中一个是生产构建阻断级别：

1. **BLOCKER**: `npm run build` 因大小写敏感的 import 失败（`ResearchExploreResult.tsx` 导入 `@/components/ui/Card` 大写 C，实际文件 `card.tsx`）
2. **BLOCKER**: s11 StatusBadge 统一 **未完成** — 存在两份不同的 StatusBadge 实现在并行服役，顶层未改为 barrel re-export，QDS Status union 缺 `completed/cancelling/cancelled` 键
3. **HIGH**: s4/s6 拆分门槛违反 FR-4.1 NFR-2 — `backtest/page.tsx` 1805 行、`PerformanceTab.tsx` 2061 行、`OverviewTab.tsx` 836 行、`TradesTab.tsx` 851 行、`OverviewGreyTab.tsx` 676 行（最后一个略超阈值但接近），且 `research/page.tsx` 虽已拆到 398 行，但 `ReportClient.tsx` 从 757 行缩到 193 行的形式上合规

上轮 Quality Gates 声明 "`npm run build` 成功（16 静态页面全生成）" 与新鲜的 build 输出相矛盾。该声明可能因为 macOS 大小写不敏感文件系统在 pre-task 状态下被误判，但在严格模式 Turbopack / Linux CI 下会失败。

---

## 证据概览

### 合规扫描（PASS — 全部 0 violations）

| 子命令 | Exit | 摘要 |
|---|---|---|
| `bash scripts/verify-ds-compliance.sh` | 0 | R1-R14 全部 0 violations / 0 files |
| `bash scripts/verify-ds-compliance.sh --selftest` | 0 | 65 passed / 0 failed（含 R14 PCRE2 前后向断言 + 模板字符串用例） |
| `bash scripts/verify-ds-compliance.sh --preflight-before-css-delete` | 0 | R1-R10+R12+R13+R14 全部 0 violations |
| `bash scripts/verify-ds-compliance.sh --mode both-themes` | 0 | 0 violations |

**结论**：规则层面 s2/s4-s9/s10/s11(R2 部分)/AC-1 通过。

### 构建与测试

| 任务 | 结果 | 证据 |
|---|---|---|
| `npx tsc --noEmit` | **FAIL** | `error TS1261: Already included file name '/Users/ouzhuohao/TinoHelm/src/web/src/components/ui/Card.tsx' differs from file name '/Users/ouzhuohao/TinoHelm/src/web/src/components/ui/card.tsx' only in casing.` 位于 `src/app/research/components/ResearchExploreResult.tsx:16` |
| `npm run build` | **FAIL** | Turbopack build failed: `Module not found: Can't resolve '@/components/ui/Card'` at `ResearchExploreResult.tsx:16`（该文件由 s6 拆分产生，使用大写 C，但 `src/components/ui/` 下仅存在小写 `card.tsx`） |
| `npm run test:fonts` | PASS | 15/15 通过（字体令牌层未动） |
| `npm run lint` | PASS（exit 0）| 46 errors + 41 warnings（与 baseline 一致，全部 pre-existing 与 task 无关：useCountUp/notification-router/useWebSocket） |

> 上轮声明"`npm run build`: 成功（16 静态页面全生成）"与当前 `npm run build` 输出冲突。新鲜输出明确显示构建失败。

### Git 变更范围

`git diff --stat HEAD -- src/web/`：
- 39 个 tracked 文件修改（+1384 / -3409）
- `globals.css`: 从 1987 → **785 行**（删除 1202 行，s10 完成）
- 2 个 untracked 目录（s6 拆分新文件）：
  - `src/app/research/components/`（8 个文件，含 bug 文件）
  - `src/app/research/report/[id]/components/`（8 个文件）

---

## Subtask 验收标准

### s1 · chartTheme 常量补全

| AC | 状态 | 证据 |
|---|---|---|
| `CHART_LEGEND_STYLE` export 存在 | **VERIFIED** | `chartTheme.ts:87-91` 定义 `{ fontSize: ".62rem", fontFamily: "var(--font-d)", color: "var(--t1)" }` |
| `CHART_LABEL_STYLE` export 存在且不含 fontFamily | **VERIFIED** | `chartTheme.ts:103-109` 定义 `{ fontSize: 10, fill: "var(--t2)" }`，明确注释 "fontFamily intentionally omitted" |
| 仅 additions，无 deletions | **VERIFIED** | `git diff --stat` 显示 +26 / -0 |
| build typecheck 通过 | **PARTIAL** | chartTheme.ts 本身 typecheck 无问题，但整体 tsc 因 s6 case bug 失败 |

**结论**: s1 **VERIFIED**（文件本身合规，依赖关系被 s6 污染不属于 s1 的责任）。

### s2 · 合规扫描脚本

| AC | 状态 | 证据 |
|---|---|---|
| R1-R14 规则实现 | **VERIFIED** | scan 输出列出 14 条规则全部 ✓ |
| `--selftest` 覆盖 R4/R6/R7/R8/R9 多行/R10/R12/R13 (11 variant)/R14 | **VERIFIED** | selftest 65 passed，含 `template string backtick form` 用例 |
| `--preflight-before-css-delete` 含 R14 | **VERIFIED** | preflight 输出显示 R14: 0 violations |
| `--mode both-themes` 排除 ui/+qds/ | **VERIFIED** | both-themes 0 violations，显式打印排除范围 |
| `--help` | 未独立验证 | （非关键，skip） |
| `chmod +x` 权限 | **VERIFIED** | `-rwxr-xr-x` per ls |

**结论**: s2 **VERIFIED**。

### s3 · CLAUDE.md 章节

| AC | 状态 | 证据 |
|---|---|---|
| `## 标准化后的约束` 章节存在 | **VERIFIED** | `src/web/CLAUDE.md:143` |
| `verify-ds-compliance.sh` 命中 ≥ 3 行 | **VERIFIED** | 含全仓 / --fix-hint / --mode both-themes / --preflight-before-css-delete / --selftest 5 种调用方式 |
| 禁区 class 清单 | **VERIFIED** | 列出 bt-*/dc-*/cg/ca/cr/ci/dim/mono + factor-research 85 class |
| Historical Notes | **VERIFIED** | 含 4 份 memory 文件作废声明表格 |
| `.claude/skills/TinoHelmDS` 引用 ≥ 2 行 | **VERIFIED** | Key Conventions 与章节末尾均引用 |
| `docs/ui/qds-` 引用 0 行 | **VERIFIED** | grep 0 命中 |
| shadcn 豁免声明 | **VERIFIED** | `components/ui/**` 豁免段落 L242 |
| 7 项 QDS 强制组件 | **VERIFIED** | StatCard/PageHeader/SectionLabel/InlineError/StatusBadge/HelpTip/ShimmerBar 全部列出 |

**结论**: s3 **VERIFIED**。

### s4 · /backtest 迁移与拆分

| AC | 状态 | 证据 |
|---|---|---|
| 合规扫描在 backtest 下 0 违规 | **VERIFIED** | R1/R2/R4/R5/R6/R7/R9/R10/R12/R14 全 0 |
| `sc-l/hm-grid/hm-label/hm-cell` 0 残留 | **VERIFIED** | grep 0 命中 tsx 文件（仅注释提及） |
| 拆分后所有新文件 < 700 行 | **MISSING** | `wc -l`：`PerformanceTab.tsx` **2061** / `backtest/page.tsx` **1805** / `TradesTab.tsx` **851** / `OverviewTab.tsx` **836** / `OverviewGreyTab.tsx` 676。FR-4.1 明确要求「PerformanceTab 主文件 < 700」「backtest/page.tsx 主文件 < 700」「TradesTab 按表格与筛选器拆分」「OverviewTab 按 KPI 栅格与图表拆分」。**拆分完全未发生** |
| `npm run build` 通过 | **MISSING** | s6 的 case bug 导致 build 失败 |
| `npm run lint` 通过 | **VERIFIED** | exit 0（与 baseline 持平） |

**结论**: s4 **PARTIAL** — class 级迁移与 factor-research 散落处理 VERIFIED；**文件拆分任务完全未执行**，违反 FR-4.1 NFR-2。这是一个合规脚本检测不到的结构性失败。

### s5 · /data-catalog 迁移

| AC | 状态 | 证据 |
|---|---|---|
| 合规扫描在 data-catalog 下 0 违规 | **VERIFIED** | R3 / R14 / R12 / R1 0 |
| `dc-type-*` 字典完全重写 | **VERIFIED** | `grep 'dc-type-' src/app/data-catalog` 命中 0 |
| `sc/sc-l/sc-v/sc-sub/fsel` 0 残留 | **VERIFIED** | grep 0 命中 .tsx |
| JobQueue bt-status 预留 s11 | **VERIFIED** | JobQueue 现使用 `<StatusBadge status="queued">`（通过 qds barrel），bt-status 已清除 |
| `npm run build` 通过 | **MISSING** | s6 导致 build 失败 |

**结论**: s5 **VERIFIED**（受 s6 连累 build 不过，但 s5 自身文件合规）。

### s6 · /research + /research/report 迁移与拆分

| AC | 状态 | 证据 |
|---|---|---|
| 合规扫描在 research 下 0 违规 | **VERIFIED** | R14 0 命中 |
| research/page.tsx < 700 行 | **VERIFIED** | 398 行（已从 991 拆分） |
| ReportClient.tsx < 700 行 | **VERIFIED** | 193 行（从 757 拆分） |
| factor-research 原语 0 残留 | **VERIFIED** | R14 全仓 0 |
| `npm run build` 通过 | **MISSING** | **s6 引入 case-sensitivity bug**：`src/app/research/components/ResearchExploreResult.tsx:16` 导入 `@/components/ui/Card`（大写 C），但 src/components/ui/ 下实际文件名是 `card.tsx`（小写）。Turbopack 严格解析失败，tsc TS1261 报错 |
| `git mv` 追溯 | 未独立验证 | 新文件 untracked，尚未 git add（不阻塞，但提示 s12 收官未到位） |

**结论**: s6 **PARTIAL / FAIL**（拆分完成了数值目标，但引入构建阻断级 bug）。

### s7 · /trading 迁移

| AC | 状态 | 证据 |
|---|---|---|
| 合规扫描在 trading 下 0 违规 | **VERIFIED** | R13 0 命中，涵盖 11 variant |
| `var(--accent-*)` 0 残留 | **VERIFIED** | grep 0 命中 |
| TabNav.tsx `var(--accent-*)` 0 | **VERIFIED** | grep 0 命中 |
| bt-* 维持 0 | **VERIFIED** | grep 0 命中 |

**结论**: s7 **VERIFIED**。

### s8 · /analytics + /optimization + /orders + /watchlist

| AC | 状态 | 证据 |
|---|---|---|
| 合规扫描 0 违规 | **VERIFIED** | 扫描脚本 all-pass |
| optimization/page.tsx 无行数膨胀 | **VERIFIED** | 736 行（与 pre-task 一致） |
| analytics TOOLTIP_STYLE 本地声明已删 | 未独立验证 | （非关键，扫描已覆盖 R6） |

**结论**: s8 **VERIFIED**。

### s9 · / + /strategies + /strategies/[name] + /settings

| AC | 状态 | 证据 |
|---|---|---|
| 合规扫描 0 违规 | **VERIFIED** | 扫描全 0 |
| EditorClient `var(--accent-*)` 0 残留 | **VERIFIED** | grep 0 命中 |
| page.tsx Tooltip spread | **VERIFIED** | 扫描 R6 通过 |

**结论**: s9 **VERIFIED**。

### s10 · globals.css 遗留定义删除

| AC | 状态 | 证据 |
|---|---|---|
| `^\.bt-` 0 命中 | **VERIFIED** | `grep -c '^\.bt-' globals.css` = 0 |
| `^\.dc-` 0 命中 | **VERIFIED** | 0 |
| L1856 单行组合已删 | **VERIFIED** | `mono/dim/cg/ca/cr/ci` 整体为 0 |
| factor-research selector 删除 | **PARTIAL** | 仅剩 `.flash-positive` / `.flash-negative` 2 处（非 factor-research 主体，而是 tick-flash 独立定义；R14 主扫描覆盖 `^\.(sc|cd|sl|...)` 0 命中。**Round 3 定义清单不含 flash-*，应视为保留**） |
| `^\.qds-` ≥ 15 | **VERIFIED** | 61 qds-* 定义保留 |
| globals.css 行数 1210 ± 50 | **MISSING** | 实测 **785 行**（低于目标区间下限 1160）。s12 Step 7 断言要求 1160-1260 之间，实测 **超删 ~375 行** |
| `npm run build` 通过 | **MISSING** | s6 bug 阻断 |

**结论**: s10 **PARTIAL** — legacy class 删除 VERIFIED；**globals.css 行数超出目标下限 375 行**（NFR-2 要求 1210±50）。可能影响风险：是否删除了除 legacy class 以外的内容（例如动画或共用 primitive）。**需 diff review 确认无过度删除**。

### s11 · 全仓扫描补漏 + StatusBadge 统一

| AC | 状态 | 证据 |
|---|---|---|
| 全仓 R1-R14 0 | **VERIFIED** | 扫描全 0 |
| StatusBadge barrel re-export 完成 | **MISSING** | `src/components/StatusBadge.tsx:19` 仍然是 `export function StatusBadge({ status, className })`，保留自己独立实现（含 completed/cancelling/cancelled），**未改为 `export { StatusBadge } from "@/components/qds/status-badge"`** 。违反 s11 Step 11b 明确要求 |
| QDS Status union 含 7 键 queued/running/completed/failed/cancelling/cancelled/done | **MISSING** | `qds/status-badge.tsx:4-11` 的 `StatusKind` 含 `running/done/failed/queued/paused/flattening/starting` 7 键，但**缺 `completed/cancelling/cancelled`**，多出 `paused/flattening/starting`。与规格所列 7 键 `queued/running/completed/failed/cancelling/cancelled/done` 不一致 |
| JobQueue bt-status 迁移 | **PARTIAL** | 4 处 bt-status 已清除，但 L189 「已取消」被错误映射为 `status="queued"` 而非 `status="cancelled"`（因为 QDS Union 不含 cancelled）。任务规格显式要求「L185 "已取消" 原为 bt-status-queue，应迁移为 `status="cancelled"`，验证 UI 语义正确」 |
| 两份 StatusBadge 消费者映射正确 | **MISSING** | `optimization/page.tsx:13` 和 `page.tsx:130` 使用 `@/components/StatusBadge`（shadcn Badge + completed/cancelling/cancelled）；`JobQueue.tsx` 和 `research/**` 使用 `@/components/qds`（rounded-full + 缺 cancelled 键）。两条消费链分叉保留，违背「统一」目标 |

**结论**: s11 **FAIL**。StatusBadge 统一未完成，存在两份实现，且 QDS 版缺 3 个语义必要的状态键。

### s12 · 双主题验证 + 最终扫描 + CLAUDE.md 定稿

| AC | 状态 | 证据 |
|---|---|---|
| Step 1 整仓扫描 exit 0 | **VERIFIED** | R1-R14 0 violations |
| Step 2 both-themes 扫描 exit 0 | **VERIFIED** | 0 violations |
| Step 3 build + lint 通过 | **MISSING** | build **FAIL** |
| Step 4 既有字体脚本通过 | **VERIFIED** | test:fonts 15/15 |
| Step 5 CLAUDE.md 定稿 | **VERIFIED** | Historical Notes + 视觉参考源 + shadcn 豁免全部存在 |
| Step 6 行数 < 700（strategies/optimization 豁免）| **MISSING** | backtest/page.tsx 1805 / PerformanceTab 2061 / OverviewTab 836 / TradesTab 851，未豁免却超标 |
| Step 7 `html.light` 核心 token override | 未独立验证 | （非关键） |
| globals.css 行数 1160-1260 | **MISSING** | 实测 785 行 |

**结论**: s12 **FAIL**（Step 3 / Step 6 / globals.css 行数三处失败）。

---

## 回归风险

| 影响范围 | 风险 | 评估依据 |
|---|---|---|
| **生产部署** | **HIGH** | Linux CI 构建一定失败（case sensitivity 严格）；当前 macOS 本地构建也已失败 |
| StatusBadge 语义正确性 | **HIGH** | `JobQueue.tsx:189` 取消态被显示为 "已取消" 文字但内部 status 为 `queued`，徽章背景色是 `bg-secondary text-muted-foreground`（灰），但原 shadcn 版是 `variant="neutral"` 灰；颜色恰好匹配，但 status prop 错误会污染未来 StatusBadge 扩展 |
| 两份 StatusBadge 源码共存 | **MEDIUM** | 未来修改 Badge 需同步两处；agent 生成代码可能 import 错 path 导致状态键不匹配 |
| 文件超长维护成本 | **MEDIUM** | backtest/page 1805 + PerformanceTab 2061 + TradesTab 851 + OverviewTab 836 全部超 700，FR-4.1 目标未达成，未来新功能加入将难以 code review |
| globals.css 过度精简 | **LOW-MEDIUM** | 行数 785 低于目标下限 1160 达 375 行，需要人工 diff 审核是否误删了非 legacy 的 token / animation / qds 定义 |
| 视觉回归 | User Acceptance 阶段 | AC-2/AC-3/AC-5 均已降级为 verify phase 用户验收，不作为 subtask AC |

---

## 问题列表

### 1. Turbopack 构建失败 · case-sensitive import
- **Category**: BUG
- **Severity**: HIGH (BLOCKER)
- **File**: `src/web/src/app/research/components/ResearchExploreResult.tsx:16`
- **Description**: 该文件 import `"@/components/ui/Card"`（大写 C），但 `src/components/ui/` 目录下实际文件名为 `card.tsx`（小写）。`npx tsc --noEmit` 报 TS1261；`npm run build` 报 "Module not found: Can't resolve '@/components/ui/Card'"。macOS 不区分大小写 FS 可能让先前执行 agent 以为通过，但 Turbopack 严格解析已失败，且 Linux CI 必失败。
- **Fix Directive**:
  1. Edit `src/web/src/app/research/components/ResearchExploreResult.tsx` 第 16 行
  2. 将 `from "@/components/ui/Card"` 改为 `from "@/components/ui/card"`（小写 C）
  3. 运行 `cd src/web && npx tsc --noEmit` 确认 0 错误
  4. 运行 `cd src/web && npm run build` 确认构建通过并产出 `out/` 目录（预期 16 静态页面）
  5. 顺带全仓检查：`grep -rEn 'from "@/components/ui/(Card|Dialog|Table|Button|Input|Badge|Label|Select|Tabs|Tooltip)"' src/web/src/` 应只命中 0 行（当前除此一处外其它均为小写，已确认）

### 2. StatusBadge 统一未完成 · 两份实现并存
- **Category**: COMPLETENESS
- **Severity**: HIGH (BLOCKER)
- **File**:
  - `src/web/src/components/StatusBadge.tsx`（应为 barrel re-export，当前为独立实现）
  - `src/web/src/components/qds/status-badge.tsx`（Status union 缺 `completed/cancelling/cancelled` 3 键）
  - `src/web/src/app/data-catalog/JobQueue.tsx:189`（取消态语义错误）
- **Description**: s11 Step 11b 明确要求：
  - **(a)** 改写 `components/qds/status-badge.tsx`：`Status` union 加入 `queued/running/completed/failed/cancelling/cancelled/done` 7 个键，新增 `locale` prop + 中英双语 label map — **仅完成 locale 与一部分键，但用错了键名**：当前为 `running/done/failed/queued/paused/flattening/starting`（把 `completed` 叫 `done`，多出 3 个无关键，少了 3 个必需键）
  - **(b)** 改写 `components/StatusBadge.tsx` 为 barrel re-export `export { StatusBadge } from "@/components/qds/status-badge"` — **完全未做**，仍是独立实现
  - **(c)** JobQueue L185/L189 `已取消` 应映射为 `status="cancelled"` — 被 hack 为 `status="queued"`，语义错误
- **Fix Directive**:
  1. Edit `src/web/src/components/qds/status-badge.tsx`:
     - `StatusKind` 改为 `"queued" | "running" | "completed" | "failed" | "cancelling" | "cancelled" | "done"`
     - `LABEL_MAP_ZH`: `{ queued: "排队中", running: "运行中", completed: "已完成", failed: "失败", cancelling: "取消中", cancelled: "已取消", done: "已完成" }`
     - `LABEL_MAP_EN` 对应翻译
     - `COLOR_MAP`: `completed` / `done` 用 `bg-qds-success-dim text-qds-success`；`cancelling` / `cancelled` 用 `bg-secondary text-muted-foreground`；其它与当前一致
  2. Rewrite `src/web/src/components/StatusBadge.tsx` 为：
     ```ts
     export { StatusBadge, type StatusKind } from "@/components/qds/status-badge";
     ```
     删除其中的 Badge / STATUS_MAP 等独立实现代码
  3. Edit `src/web/src/app/data-catalog/JobQueue.tsx:189`：`<StatusBadge status="queued">已取消</StatusBadge>` 改为 `<StatusBadge status="cancelled" />`（不需要 children override）
  4. 运行 `npx tsc --noEmit` 检查所有 `StatusBadge status={run.status}` 调用点（page.tsx:559、optimization/page.tsx:304、:610）的 run.status 类型与新 union 兼容；如有不兼容需窄化或 cast
  5. 运行 `npm run build` 确认通过

### 3. backtest 目录文件拆分未执行 · FR-4.1 违反
- **Category**: COMPLETENESS
- **Severity**: HIGH
- **File**:
  - `src/web/src/app/backtest/page.tsx` (1805 行，target < 700)
  - `src/web/src/app/backtest/components/PerformanceTab.tsx` (2061 行，target < 700)
  - `src/web/src/app/backtest/components/TradesTab.tsx` (851 行，target < 700)
  - `src/web/src/app/backtest/components/OverviewTab.tsx` (836 行，target < 700)
- **Description**: FR-4.1 明确规定这 4 个文件必须拆分到主文件 < 700 行。s4 完成了 class 迁移与 factor-research 散落清理（扫描通过），但**完全没有执行文件拆分工作**。4 个文件合计 5553 行，远超阈值。这是 NFR-2 可维护性目标的直接违反，也违反了 s4 自身验收标准 "拆分后所有新文件行数 <700（`wc -l src/web/src/app/backtest/**/*.tsx | sort -n | tail -5` 最大值 <700）"。
- **Fix Directive**:
  1. `backtest/page.tsx` 按 §3.5.1（1-requirements.md 提示）：拆为列表视图 / 详情视图 / URL 状态驱动 / 查询 / 分页 4 个子模块（放入 `src/app/backtest/components/list/` 与 `src/app/backtest/components/detail/`），主文件只保留路由壳 < 400 行
  2. `PerformanceTab.tsx` 按 §3.5.2：在 `src/app/backtest/components/performance/` 下拆出 `EquityChart.tsx` / `DrawdownChart.tsx` / `RollingChart.tsx` / `ReturnsChart.tsx` / `DistributionChart.tsx` / 其它，每个 < 400 行
  3. `TradesTab.tsx`：拆为 `TradesFilters.tsx` / `TradesTable.tsx` / `TradePnlSparkline.tsx`
  4. `OverviewTab.tsx`：拆为 `OverviewKpis.tsx` / `OverviewEquityChart.tsx` / `OverviewStats.tsx` / `OverviewMonthlyHeatmap.tsx`
  5. 所有拆分用 `git mv` 保留 blame（NFR-3）
  6. 拆分完成后 `wc -l src/web/src/app/backtest/**/*.tsx | sort -n | tail -5` 最大值必须 < 700
  7. 运行 `npm run build` 与 `npm run lint` 确认通过
  8. 运行 `bash scripts/verify-ds-compliance.sh` 确认合规保持

### 4. globals.css 超删 · 行数低于目标下限
- **Category**: BUG (potential)
- **Severity**: MEDIUM
- **File**: `src/web/src/app/globals.css` (实测 785 行，目标 1160-1260 per s12 Step 7 + NFR-2 1210±50)
- **Description**: s10 应删除约 780 行遗留定义，结果应为 1987 - 780 ≈ 1207 行，与 NFR-2 目标一致。实际从 1987 → 785，删除了 **1202 行**（超目标 420 行）。**可能误删了非 legacy 内容**（例如 `qds-*` 业务组件 class、动画 keyframes、token 定义、flash 动画等）。61 个 `.qds-*` 定义仍在（≥ 15 最低要求满足），但其它保留内容需人工 diff 审核。
- **Fix Directive**:
  1. 运行 `git diff HEAD -- src/web/src/app/globals.css | head -800` 查看删除的内容
  2. 核对 1-requirements.md §1.1 列表（`.bt-*` ~400 行 + `.dc-*` ~250 行 + `.cg/.ca/.cr/.ci/.dim/.mono` L1856 + factor-research ~135 行 = 约 785 行）
  3. 任何**非此列表**的删除需恢复：典型可能误删的区块包括 `.qds-input` / `.qds-select` / `.qds-card` / `.qds-stat` / `.qds-section-label` / `.qds-table` / 动画 `@keyframes qds-*` / token 定义 / `html.light` overrides
  4. 如确认 1202 行删除内容全部属于 legacy 清单（即文件基线可能与 1987 不同），更新 s12 Step 7 的行数断言到 785 ± 30 作为事实记录，并在 CLAUDE.md 中更新 "globals.css 实际删除数据" 段落（当前写的是 "删除后：785 行，删除 ~1202 行遗留定义"，与 NFR-2 目标 "1210±50" 冲突，至少其一错）
  5. 运行 `bash scripts/verify-ds-compliance.sh` 确认 R11 仍通过

### 5. 新拆分文件未 git add · 可追溯性风险
- **Category**: COMPLIANCE
- **Severity**: LOW
- **File**:
  - `src/web/src/app/research/components/` (untracked)
  - `src/web/src/app/research/report/[id]/components/` (untracked)
  - `src/web/scripts/verify-ds-compliance.sh` (untracked)
- **Description**: NFR-3 要求「新增子文件使用 `git add` 明确标记」。当前 16 个新文件（8 + 8 research subcomponents）加上 s2 的扫描脚本都是 untracked 状态。s12 收官应将其纳入 git 追踪。
- **Fix Directive**:
  1. 在前述 3 个 blocker 修复后执行 `git add src/web/src/app/research/components/ src/web/src/app/research/report/\[id\]/components/ src/web/scripts/verify-ds-compliance.sh`
  2. 确认 `git status` 显示所有新文件 tracked

---

## Kickback 修复验证

无上轮 kickback（本次为 r1）。

---

## 总结

- **自动化扫描层（R1-R14 + preflight + both-themes + selftest + fonts）**: 全部通过 → 合规类 subtask（s2/s3/s7/s8/s9/大部分 s10）VERIFIED
- **构建层（tsc + build）**: FAIL（s6 大小写 import bug）
- **结构层（文件拆分）**: FAIL（s4 backtest 4 个文件全部未拆）
- **组件统一层（StatusBadge）**: FAIL（s11 两份实现并存，键名与规格不匹配）
- **资源清理层（globals.css 行数）**: PARTIAL（超删疑虑需 diff 审核）

**3 个 BLOCKER / HIGH 问题** 需 executor 在 kickback 回合解决，其中 Problem #1 只需改一个字符（`Card` → `card`）即能恢复构建；Problem #2 需重构 StatusBadge 达到真正统一；Problem #3 工作量最大（约 2-4h 拆分 4 个大文件）。

Problem #4 可能只是行数估算失真（实际 legacy 定义多于 780 行），需 diff review 确认，不一定是 bug。

VerifyPass: verifier
Verdict: FAIL
