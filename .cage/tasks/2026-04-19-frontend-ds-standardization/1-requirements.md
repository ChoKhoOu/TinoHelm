# 1 · 需求文档 — 前端 DS 标准化

> 本文档为 `2026-04-19-frontend-ds-standardization` 任务的需求规格。事实来源：`interview.md`、`.claude/skills/TinoHelmDS/`、`src/web/CLAUDE.md`、`src/web/src/app/globals.css`。
> **Round 1 Revision**：违规统计根据精确正则重新验证（已验证 @ 2026-04-19）；`docs/ui/qds-*.html` 引用已改为 `.claude/skills/TinoHelmDS/`（该目录不存在）；新增 §1.9「与历史用户 memory 的关系」声明。
> **Round 2 Revision**：所有计数现场重新 Grep 验证 @ 2026-04-19；bt-* 由 253 → 280 处跨 7 文件；dc-* 由 53 → 65 处跨 6 文件；`--accent-*` 由 35 → 55 处跨 8 文件 / 10 variant；factor-research 散落细化为 66 实例跨 6 文件；§1.9 降语。
> **Round 3 Revision**：planner **独立全仓 rg 重新扫描** @ 2026-04-19（不依赖上轮审查列出的数字），所有计数贴入原始输出。重点修正：sc-l 实测 15 处跨 5 文件（r2 漏 OverviewGreyTab 4 + 每文件多 1 处，共漏 8 处）；hm-* 实测 5 处仅在 backtest/OverviewTab.tsx 且 research 下 0 处（s4 内联实现，不依赖 s6）；--accent-* 实测 67 处跨 8 文件 / **11 variant**（r2 漏 1 variant purple-20 + 数字计 55 错；新数字 67 与各 variant 求和一致）；purple / purple-20 锁定为 text-primary / bg-qds-accent-dim（删除 case-by-case 歧义）；AC-2 / AC-3 视觉验证降级为 user acceptance in verify phase（不占 subtask AC 槽，遵守用户 MUST 规则）；R14 正则改为 PCRE2 前后向断言与 R4 风格一致（消除 selftest 自相矛盾）。

## 1.1 背景

TinoHelm 是基于 NautilusTrader 的量化交易 Web 工作台。前端（`src/web/`，Next.js 16 + React 19 + Tailwind v4 + shadcn/ui v4 + Recharts）经历过多个设计阶段：

- 早期页面（backtest / data-catalog）采用 **QDS CSS class 方式**（`bt-*`/`dc-*`/`cg/ca/cr/dim` 等），通过 `globals.css` 内的 `!important` 类实现像素级还原；
- 中期页面（strategies / trading / analytics 等）采用 **Tailwind + shadcn + QDS 业务组件**（`StatCard` / `PageHeader` / `SectionLabel` / `InlineError` 等）的现代路线；
- 另有 **factor-research 子系统**（L1853-1987，globals.css 中 98 unique selector / 顶层约 85 个 class），**Round 3 实测发现**：散落比 r2 报告更严重，涵盖 `data-catalog/page.tsx`（4 张 KPI + 1 处 fsel）、`data-catalog/FetchDialog.tsx`（3 处 fsel）、`backtest/components/{OverviewGreyTab,TradesTab,PerformanceTab,TearsheetTab}`（共 11 处 sc-l）、`backtest/components/OverviewTab.tsx`（5 处 hm-* 月度热力图）；
- `chartTheme.ts` 出于统一 Recharts 样式目的暴露了 `CHART_TOOLTIP_PROPS` / `CHART_GRID_STYLE` / `CHART_AXIS_STYLE` 等常量，但存量代码有大量未迁移的手写内联。

三条技术路线并存，导致：（1）后续页面风格不稳定，依赖个别工程师记忆；（2）agent 生成前端代码经常产出错误 class；（3）CSS 体积和维护成本双高（1987 行 globals.css 里 ~780 行属于待废弃的遗留定义）。

### 现状违规清单（Round 3 已独立全仓 rg 重新验证 @ 2026-04-19）

| 违规类型 | 调用点数量（精确） | 影响范围 | 扫描方法 |
|---|---:|---|---|
| `style={{…fontFamily: "var(--font-[ud])"…}}` 内联 | 28 处跨 5 文件 | trading/page(5)、backtest/page(15)、data-catalog/page(3)、FetchDialog(2)、research/page(3) | `rg 'style=\{\{[^}]*fontFamily'` @ `src/web/src/app` |
| `style={{…fontSize: …}}` 内联（非 Recharts 透传） | 90 处跨 9 文件 | trading/page(5)、StrategiesTab(2)、backtest/page(47)、PerformanceTab(2)、data-catalog/page(5)、FetchDialog(8)、DeleteDialog(3)、research/page(16)、ReportClient(2) | `rg 'style=\{\{[^}]*fontSize'` @ `src/web/src/app` |
| `className="bt-*"` 调用 | **280 处跨 7 文件** | backtest/page(144)、backtest/components/OverviewTab(74)、PerformanceTab(28)、RobustnessTab(15)、TradesTab(9)、OverviewGreyTab(6)、data-catalog/JobQueue(4)（4 处 `bt-status bt-status-{queue,done,fail}`） | `rg '\bbt-[a-z0-9-]+' --glob='*.tsx'` @ `src/web/src/app` |
| `className="dc-*"` 调用（含字符串常量） | **65 处跨 6 文件** | page(23)、JobQueue(14)、FetchDialog(8)、FilterTabs(7)、types.ts(12：TYPE_BADGE_CLS 字典 12 处字符串 value）、DeleteDialog(1) | `rg '\bdc-[a-z0-9-]+' --glob='*.{tsx,ts}'` @ `src/web/src/app/data-catalog` |
| `className` 中 `cg/ca/cr/ci/dim/mono` 独立 token（严格前后向断言，非 Tailwind 类） | 14 处跨 3 文件 | research/page(9)、data-catalog/page(4)、DeleteDialog(1) | PCRE2 `(?<![-a-zA-Z0-9_])(cg\|ca\|cr\|ci\|dim\|mono)(?![-a-zA-Z0-9_])` 且 `className="..."` 上下文 |
| **factor-research 原语调用（Round 3 全仓重新扫描精确化）**| **85 处 className 实例跨 9 文件**（sc-l 15 + hm-* 5 + fsel 4 + 其它 sc/cd/sl/fl/fi/ctbl/dtab 61；含 research/page 主体 47 处）| **按 factor-research 原语家族分布**：research/page(47) + data-catalog/page(6：L240-243 4 张 KPI `sc/sc-l/sc-v` + L243 1 处 `sc-sub` + L252 1 处 `fsel`) + data-catalog/FetchDialog(3：L199/215/230 各 1 处 `fsel`) + data-catalog/JobQueue(1) + **backtest/components/OverviewGreyTab(4：L84/L134/L220/L458 共 4 处 sc-l)** + **backtest/components/TradesTab(3：L162/L179/L515 共 3 处 sc-l)** + **backtest/components/PerformanceTab(2：L226/L1726 共 2 处 sc-l)** + **backtest/components/TearsheetTab(2：L48/L90 共 2 处 sc-l)** + **backtest/components/OverviewTab(5：L190/L192/L195/L200/L206 共 5 处 `hm-grid`/`hm-label`/`hm-cell`，月度热力图原语)** | `rg 'className=["\x27][^"\x27]*\b(sc\|sc-l\|sc-v\|sc-sub\|cd\|sl\|fl\|fi\|fsel\|ctbl\|dtab\|hm-(grid\|label\|cell\|tick))\b' src/web/src --glob='*.tsx'`（独立现场扫描，Round 3 口径统一：按 className 实例计数）|
| `globals.css` 中 `.bt-*` 遗留定义 | 134 selector | globals.css 体积膨胀（L532 起）| `rg '^\.bt-'` @ globals.css |
| `globals.css` 中 `.dc-*` 遗留定义 | 76 selector | globals.css 体积膨胀（L1640 起，`.dc-sl` 为首个；L1659 为 `.dc-filter-strip`）| `rg '^\.dc-'` @ globals.css |
| `globals.css` 中 L1856 单行组合 | `.mono{…}.dim{…}.cg{…}.cr{…}.ca{…}.ci{…}` 1 行定义 6 个 class | 行首锚定只能命中 `.mono`；其余需特殊处理 | `rg '\.mono\{'` @ globals.css |
| `globals.css` 中 factor-research 子系统定义（L1853-1987） | **98 unique selector / ~135 行**（顶层需独立映射约 85 个 class） | globals.css 体积膨胀 | `sed -n '1853,1987p' globals.css \| rg -o '^\.[a-zA-Z][a-zA-Z0-9_-]*' \| sort -u \| wc -l` |
| Recharts 手写 `contentStyle` 未使用 `CHART_TOOLTIP_PROPS` | 6 处 | analytics/page(3)、backtest/OverviewTab(2)、page.tsx(1) | `rg -U '<(RechartsT\|T)ooltip\b[^>]*contentStyle'` |
| Recharts 手写 `<CartesianGrid strokeDasharray="3 3" stroke="var(--bd)" />` 未用 `CHART_GRID_STYLE` | 12 处 | PerformanceTab 主力、trading/RiskTab、OverviewTab 等 | `rg '<CartesianGrid\b[^>]*strokeDasharray'` |
| Recharts `<Legend wrapperStyle={{…fontFamily…}} />` 内联 | 2 处 | research/page | `rg '<Legend\b[^>]*wrapperStyle'` |
| Recharts `<ReferenceLine … label={{…fontSize\|fill}}>` 对象 label（**不是 `<Label>` 子组件**，全仓 `<Label\b` 命中 0） | 4 处 | RiskTab:187（单行）、backtest/OverviewTab:679-684（多行 4 行）、RobustnessTab:353（单行）、ReportClient:504-508（多行 5 行） | `rg -U '<ReferenceLine[^>]*label\s*='` |
| `bg-[var(--*)]` / `text-[var(--*)]` / `border-[var(--*)]` arbitrary-value token | 54 处跨 10 文件 | button.tsx、backtest/page、OverviewTab、EditorClient、OrdersPanel 等 | `rg '(bg\|text\|border)-\[var\(--'` |
| **未定义的 CSS 变量引用（Round 3 独立全仓扫描：实测 67 处 / 11 variant / 8 文件）**| **67 处跨 8 文件 / 11 variant**（`--accent-green` 23、`--accent-red` 13、`--accent-amber` 12、`--accent-blue` 7、`--accent-orange` 4、`--accent-red-20` 2、`--accent-green-10` 2、`--accent-purple` 1、`--accent-purple-20` 1、`--accent-blue-20` 1、`--accent-amber-20` 1；**求和 23+13+12+7+4+2+2+1+1+1+1 = 67**；文件：EditorClient(15)、StrategyPanel(11)、OrdersPanel(9)、ActionBar(6)、FillsStream(5)、TopBar(4)、PositionsTable(4)、TabNav(1)；**合计 s7 负责 40 处 + s9 EditorClient 27 处？需核对；见下文受影响文件分布**）— **globals.css 中均未定义，CSS 容错使颜色 fallback 为视觉 bug** | 同上文件 | `rg 'var\(--accent-[a-z0-9-]+\)' src/web/src -o \| sort \| uniq -c \| sort -rn`；且 `rg -- '--accent-(green\|orange\|red\|amber\|blue\|purple)' globals.css` 返回 0（`--accent-foreground` 是 shadcn 内置 token 已定义，不属此统计） |

### 字体迁移路径（Round 3 修订：扩展至 11 variant 映射，purple 锁定决策）

| 现状 | 目标 |
|---|---|
| `var(--font-d)` inline | Tailwind `font-mono` |
| `var(--font-u)` inline | Tailwind `font-sans` |
| `cg` | `text-qds-success` |
| `cr` | `text-destructive` |
| `ca` | `text-primary` |
| `ci` | `text-qds-info` |
| `dim` | `text-muted-foreground` |
| `mono`（作为独立 token） | `font-mono` |
| `var(--accent-green)` 未定义 | `text-qds-success`（token: `--suc`） |
| `var(--accent-orange)` 未定义 | `text-primary`（token: `--acc`） |
| `var(--accent-red)` 未定义 | `text-destructive`（token: `--dan`） |
| `var(--accent-amber)` 未定义 | `text-qds-warning`（token: `--warn`） |
| `var(--accent-blue)` 未定义 | `text-qds-info`（token: `--info`） |
| `var(--accent-purple)` 未定义 | **`text-primary`**（项目无 purple token；**本任务不新增 token，purple 语义统一并入 accent 橙**；Round 3 锁定，删除 r2 的 "case-by-case 评估" 备选） |
| `var(--accent-red-20)` 未定义 | `bg-qds-danger-dim` |
| `var(--accent-green-10)` 未定义 | `bg-qds-success-dim` |
| `var(--accent-amber-20)` 未定义 | `bg-qds-warning-dim` |
| `var(--accent-blue-20)` 未定义 | `bg-qds-info-dim` |
| `var(--accent-purple-20)` 未定义 | **`bg-qds-accent-dim`**（`--acc-d` 12% alpha；Round 3 锁定：purple dim 统一并入 accent dim） |

本任务目标：**按 `.claude/skills/TinoHelmDS/` 设计系统一次性全面标准化 14 个前端页面**，使整个 `src/web/` 源码树达到单一技术路线、可持续维护、agent 可靠生成的状态。

## 1.2 用户故事

**作为**一名 TinoHelm 的前端维护者 / 设计评审者 / agent 驱动的代码生成者，

**我希望**所有前端页面严格遵循 TinoHelmDS 设计系统的 token、组件与节奏，

**以便于**：

- 新页面能复制粘贴 TinoHelmDS preview 卡片（`.claude/skills/TinoHelmDS/preview/*.html`）与 `Web UI Kit.html` 的结构而不引入风格漂移；
- `globals.css` 只保留必要的 QDS 基础层（token 定义 + 通用原语），不再维护逐页业务 class；
- Recharts 图表在 dark / light 主题切换下自动适配，无需重写 style 对象；
- CI / pre-push 可通过一条 `verify-ds-compliance.sh` 自动判定合规；
- `src/web/CLAUDE.md` 的标准化约束章节成为今后 agent 生成前端代码的硬规则。

## 1.3 范围内对象（Scope — 14 页 + 子组件）

### 1.3.1 路由页面（14）

| 路由 | 入口文件 | 行数 | 标准化程度（现状） |
|---|---|---:|---|
| `/` | `src/app/page.tsx` | 585 | 高 |
| `/backtest` | `src/app/backtest/page.tsx` | 1754 | 中（内联 style 20 处、bt-* 144 处、**Round 3 新增：子组件含 11 处 sc-l + 5 处 hm-* factor-research 散落**） |
| `/data-catalog` | `src/app/data-catalog/page.tsx` | 333 | 低—中（dc-* 23 处、内联 style 3 处、factor-research sc/sc-l/sc-v 4 张 KPI + fsel 1 处） |
| `/strategies` | `src/app/strategies/page.tsx` | 754 | 高 |
| `/strategies/[name]` | `src/app/strategies/[name]/page.tsx` | 11（薄壳） | 中（EditorClient.tsx 200 行，含 15 处未定义 `--accent-*` 变量） |
| `/trading` | `src/app/trading/page.tsx` | 454 | 高（内联 style 5 处，无 bt-*） |
| `/research` | `src/app/research/page.tsx` | 991 | 中—低（内联 style 3 处、Legend 2 处、factor-research className 实例 47 处） |
| `/research/report/[id]` | `src/app/research/report/[id]/page.tsx` | 9（薄壳） | 中—低（ReportClient.tsx 757 行，含 ReferenceLine label 1 处 + ctbl 若干） |
| `/analytics` | `src/app/analytics/page.tsx` | 540 | 高（Tooltip contentStyle 未 spread 3 处） |
| `/optimization` | `src/app/optimization/page.tsx` | 736 | 高 |
| `/orders` | `src/app/orders/page.tsx` | 548 | 高 |
| `/watchlist` | `src/app/watchlist/page.tsx` | 465 | 高 |
| `/settings` | `src/app/settings/page.tsx` | 332 | 高 |
| `/` 下其它薄壳页（含 `layout.tsx`） | `src/app/layout.tsx` | — | 高（仅字体声明） |

> 说明：14 页 = 13 个业务路由 + `/` 首页；其中 `/strategies/[name]` 与 `/research/report/[id]` 是同一路由命名空间下的子页面，合并计入对应业务线；layout 作为包裹不计入 14 页但计入验收对象。

### 1.3.2 子组件目录

| 目录 | 文件数 | 合计行数 |
|---|---:|---:|
| `src/app/backtest/components/` | 8（OverviewGreyTab、OverviewTab、PerformanceTab、ReportsTab、RobustnessTab、TearsheetTab、TradeLogTab、TradesTab） | 5686 |
| `src/app/data-catalog/` 子组件 | 5（CoveragePanel、DeleteDialog、FetchDialog、FilterTabs、JobQueue）+ **types.ts**（Round 2 纳入 s5 范围） | 811 + types.ts |
| `src/app/trading/components/` | 8 + `tabs/` 5（ActionBar、FillsStream、OrdersPanel、PositionsTable、StrategyDetailPanel、StrategyPanel、TabNav、TopBar + tabs：LogsTab、OrdersTab、OverviewTab、RiskTab、StrategiesTab） | 3236 |
| `src/app/strategies/[name]/` | 2（page、EditorClient） | 211 |
| `src/app/research/report/[id]/` | 2（page、ReportClient） | 766 |

**所有上述子组件同属范围内**。

### 1.3.3 横切组件（非迁移范围但需确认基线）

| 目录 / 文件 | 处理方式 |
|---|---|
| `src/web/src/components/motion/` (FadeIn / StaggerContainer / PageTransition) | **不在迁移范围** — 不含违规 class（grep 已验证），保持现状 |
| `src/web/src/components/NotificationListener.tsx` | **不在迁移范围** — 纯事件路由，无视觉样式 |
| `src/web/src/components/{Sidebar,TopBar,StatusBar,FillTicker,EmptyState,IdBadge,ConfirmModal,ThemeToggle,ErrorBoundary}.tsx` | 纳入 s11「全仓扫描补漏」，违规数预期极低（scanning baseline：`rg -c 'bt-\|dc-' src/web/src/components` = 0） |
| `src/web/src/hooks/*` / `src/web/src/providers/*` | **不含 UI class**（grep 已验证 0 命中），s11 做 baseline 断言 |

### 1.3.4 基础设施

| 对象 | 位置 | 改动类型 |
|---|---|---|
| `globals.css` | `src/web/src/app/globals.css`（1987 行） | 删除 `.bt-*`/`.dc-*`/`.cg/.ca/.cr/.ci/.dim/.mono` + factor-research 子系统遗留定义（约 780 行） |
| `chartTheme.ts` | `src/web/src/lib/chartTheme.ts` | 新增 `CHART_LEGEND_STYLE`、`CHART_LABEL_STYLE` 常量 |
| 合规扫描脚本 | `src/web/scripts/verify-ds-compliance.sh`（新增） | 新建（含 R1-R14 + selftest + preflight + fix-hint + both-themes） |
| 前端 CLAUDE.md | `src/web/CLAUDE.md` | **改写**既有「QDS CSS Classes (globals.css)」章节 + 「Key Conventions」中 `docs/ui/` 引用 + 追加「标准化后的约束」章节 |

## 1.4 功能需求

### FR-1 · 消灭内联 style 与遗留 class（标准化方向 1）

- **FR-1.1** `style={{ fontFamily: "var(--font-d)" | "var(--font-u)" }}` 以及字符串形态的等价写法在 `src/web/src/` 下的业务代码中**零出现**（`chartTheme.ts` 的常量定义本体与 `globals.css` 的 token 层除外）。迁移路径：`var(--font-d)` → Tailwind `font-mono`，`var(--font-u)` → Tailwind `font-sans`。
- **FR-1.2** `className` 中的 `bt-*`（含 `bt-list`/`bt-row`/`bt-status`/`bt-progress`/`bt-expand` 等共 134 个 selector 家族）、`dc-*`（含 `dc-filter-*`/`dc-qrow-*`/`dc-dtbl`/`dc-type-*`/`dc-cov-*`/`dc-pager-*`/`dc-chip-*`/`dc-sl`/`dc-modal-icon` 等 76 个 selector 家族）、以及 `cg`/`ca`/`cr`/`ci`/`dim` 单字母语义 class（**以独立 token 形态出现，不与 `-` 前后缀组合**；不包括 Tailwind 原生 `font-mono`、`bg-qds-*-dim` 等合法类）在所有 `.tsx` 文件中**零出现**。
  - 迁移对照：`cg` → `text-qds-success`；`cr` → `text-destructive`；`ca` → `text-primary`；`ci` → `text-qds-info`；`dim` → `text-muted-foreground`；独立 token `mono` → `font-mono`（注意：扫描规则必须豁免 `font-mono`、`*-dim` 等 Tailwind 合法类，详见 §3.2.3 R4）。
  - `bt-*`/`dc-*` 家族逐类映射到 Tailwind 原子类或 shadcn 原语，详见 `3-tech-design.md §3.3`。
  - **`dc-type-*` 字典常量**（`TYPE_BADGE_CLS`）也必须被扫到：`data-catalog/types.ts` 的 12 处字典字符串值重写为 Tailwind class 字符串（详见 §3.3.4.1）。
- **FR-1.3** factor-research 子系统（L1853-1987，98 unique selector / 顶层约 85 个 class）在 `.tsx` 文件中**零出现**。迁移路径详见 `3-tech-design.md §3.3.7`；**Round 3 精确化散落清单**（必须在对应子任务中一并迁移）：
  - `data-catalog/page.tsx`：L240-243 4 张 KPI 卡（sc/sc-l/sc-v/sc-sub）+ L252 1 处 fsel（s5 负责）
  - `data-catalog/FetchDialog.tsx`：L199/L215/L230 共 3 处 fsel（s5 负责）
  - `backtest/components/OverviewGreyTab.tsx`：L84/L134/L220/L458 共 **4 处 sc-l**（s4 负责；若合并到 OverviewTab 则随合并迁移）
  - `backtest/components/TradesTab.tsx`：L162/L179/L515 共 **3 处 sc-l**（s4 负责）
  - `backtest/components/PerformanceTab.tsx`：L226/L1726 共 **2 处 sc-l**（s4 负责）
  - `backtest/components/TearsheetTab.tsx`：L48/L90 共 **2 处 sc-l**（s4 负责）
  - `backtest/components/OverviewTab.tsx`：L190/L192/L195/L200/L206 共 **5 处 hm-*** 月度热力图原语（s4 负责；**s4 内联 Tailwind grid + CSS custom props 实现，不依赖 s6 的 `<MonthlyHeatmap>`** — 因 research 下 hm-* 调用点 0，s4 独立实现不形成跨任务依赖）
  - **合计 backtest 散落 16 处（11 sc-l + 5 hm-*）必须在 s4 完成**；**不允许遗留到 s6**。
- **FR-1.4** `globals.css` 中上述全部遗留 class 定义（`.bt-*` + `.dc-*` + `.cg/.ca/.cr/.ci/.dim/.mono` + factor-research 子系统）**完全删除**（约 780 行）。删除动作必须在所有调用点迁移完成且扫描脚本通过后执行。L1856 单行组合 `.mono{…}.dim{…}.cg{…}.cr{…}.ca{…}.ci{…}` 必须作为**原子操作整行删除**。
- **FR-1.5** 源文件中不出现任何 `bg-[#...]` / `text-[#...]` / `border-[#...]` 形态的硬编码颜色任意值类，也不出现 `style={{ color: "#..." }}` 形态的硬编码十六进制颜色内联。（现状已 0 处，需长期保持）
- **FR-1.6** 源文件中不出现任何 `bg-[var(--*)]` / `text-[var(--*)]` / `border-[var(--*)]` 形态的 arbitrary-value token 引用。**例外**：`src/web/src/components/ui/**`（shadcn 原语 upstream 代码）豁免。
- **FR-1.7** `style` 对象内不出现 `fontFamily` / `fontSize`（除 Recharts 已知透传 `wrapperStyle`/`contentStyle`/`labelStyle` 与 `CHART_*_STYLE` 常量 spread 场景）外的装饰性样式属性。必要的布局性内联（`gridTemplateColumns`、`width`/`height` 自适应像素值、Recharts `label` 对象的布局 prop）允许保留，但需在代码内注释原因。扫描规则 R12 实现此检查，迁移时参考 §3.3.8 的字号归一化映射表。
- **FR-1.8** 源文件中不出现未在 `globals.css` 定义的 CSS 变量引用（**Round 3 实测 11 variant**：`var(--accent-green)` / `--accent-orange` / `--accent-red` / `--accent-amber` / `--accent-blue` / `--accent-purple` / `--accent-red-20` / `--accent-green-10` / `--accent-amber-20` / `--accent-blue-20` / `--accent-purple-20`）。迁移路径见 §1.1 "字体迁移路径" 表。**`--accent-foreground`** 是 shadcn 内置 token（globals.css L92 / L162 已定义），不在此禁用列表中。

### FR-2 · 视觉对齐 TinoHelmDS（标准化方向 2）

- **FR-2.1** 每个页面的信息架构对照 `.claude/skills/TinoHelmDS/preview/` 下的 21 个 HTML 预览卡片 + `.claude/skills/TinoHelmDS/Web UI Kit.html`（完整 dashboard frame 参考）+ `.claude/skills/TinoHelmDS/Charts Spec.html`（Recharts 专项），确保：
  - **间距节奏**：`grid-gap`、`padding`、`margin` 使用 QDS token（`--r` 12px cards、`--rs` 6px buttons、`--rm` 10px toasts、`gap: 20px` KPI 栅格）；
  - **色板使用**：背景遵循四层 `--bg-s/--bg-p/--bg-t/--bg-in`，文字遵循 `--t0/--t1/--t2/--t3`，语义色 `--suc/--dan/--info/--warn` 只用于金融状态；
  - **accent 纪律**：`--acc` 焦橙仅用于 primary 按钮、active 导航 3px 左边框、section label、链接、图表主线、focus ring；**不**用作 KPI 背景 / 装饰；
  - **排版层级**：标题使用 Inter + 特定字重，数据使用 JetBrains Mono（`font-mono`），section label 使用小 caps + accent 橙 + 1px 灰线延伸到边；
  - **组件形态**：KPI 卡（对照 `component-kpi.html`）、行式列表（对照 `component-row.html` 的 3px accent stripe）、按钮（对照 `component-buttons.html`）、输入（对照 `component-inputs.html`）、徽章（对照 `component-badges.html`）、侧栏（对照 `component-sidebar.html`）、进度条（对照 `component-progress.html`）、tabs（对照 `component-tabs.html`）均严格按 preview 卡片还原。
- **FR-2.2** preview ↔ 页面的对照矩阵记录在 `3-tech-design.md §3.4`，便于 agent 或人工照着做。
- **FR-2.3** preview 未覆盖的模式（Pagination、Dialog、Table、complex forms）统一使用 shadcn 默认实现 + QDS token。

### FR-3 · 组件 / Token 用法纪律化（标准化方向 3）

- **FR-3.1** QDS 业务组件（`src/web/src/components/qds/` 下）在以下场景强制复用，不得手写同功能结构：
  - `StatCard` — 所有 KPI 卡场景；
  - `PageHeader` — 所有一级页面顶部标题；
  - `SectionLabel` — 所有分节小标题（小 caps + accent + 灰线）；
  - `InlineError` — 所有表单内 / 按钮旁错误显示；
  - `StatusBadge` — 所有状态徽章（**API 决策见 §3.3.9：必须扩展 QDS 版本支持全部 7 状态 + locale prop；禁止直接替换顶层版本**）；
  - `HelpTip` — 所有字段级帮助提示；
  - `ShimmerBar` — 所有进度条扫光。
- **FR-3.2** `chartTheme.ts` 常量强制 spread：
  - `<Tooltip {...CHART_TOOLTIP_PROPS} />` 替代所有手写 `contentStyle` / `labelStyle` / `itemStyle` / `cursor`；
  - `<CartesianGrid {...CHART_GRID_STYLE} />` 替代所有手写 `stroke` / `strokeDasharray`（允许额外 prop 覆盖，如 `strokeDasharray="3 3"`；prop 顺序任意）；
  - `<XAxis tick={CHART_AXIS_STYLE} />` / `<YAxis tick={CHART_AXIS_STYLE} />` 替代手写 axis tick style；
  - 新增 `CHART_LEGEND_STYLE`（见 FR-3.3）spread 到 `<Legend wrapperStyle={…} />`；
  - 新增 `CHART_LABEL_STYLE`（见 FR-3.3）用于 `<ReferenceLine label={{ ...CHART_LABEL_STYLE, value: "..." }}>`（**实际形态：对象 label prop，不是 `<Label>` 子组件**；R9 扫描必须使用 `rg -U --multiline-dotall` 覆盖多行形式）；
  - 语义色使用 `CHART_COLORS.{accent|success|danger|info|warning}` 而非硬编码 `var(--suc)` 等字符串字面量（允许 `var()` 继续存在于 Recharts 的 `fill` / `stroke` / `stopColor` 直属 prop 里，这是 Recharts 的推荐用法，但 tooltip / legend / label 复合 style 必须走常量 spread）。
- **FR-3.3** `src/web/src/lib/chartTheme.ts` 新增两个常量：
  - `CHART_LEGEND_STYLE`: `{ fontSize: ".62rem", fontFamily: "var(--font-d)", color: "var(--t1)" }` （命中 research/page Legend 的现状样式）；
  - `CHART_LABEL_STYLE`: `{ fontSize: 10, fill: "var(--t2)" }`（**Round 2 决策**：删除 fontFamily 以保持现状字体；统一 fontSize 为 10 以覆盖 4 处的 9/10 混用；个别场景可通过 spread override 保留 9）；
  - **Round 3 澄清**：导出类型沿用 `chartTheme.ts` 既有 import 风格（若文件已有 `import type { CSSProperties } from "react"` 则使用 `: CSSProperties`，否则 `: React.CSSProperties`）。Recharts `<ReferenceLine>.label` prop 接受 `CSSProperties & { value?: ReactNode; position?: string; offset?: number }` 扩展形态，spread 后附加 `position: "insideTopLeft"` 等字段合法，无需类型断言。
- **FR-3.4** 标准化后 Tailwind 语义类映射严格遵循 `src/web/CLAUDE.md` 已有的映射表（`bg-card` / `bg-background` / `bg-secondary` / `bg-input` / `text-foreground` / `text-muted-foreground` / `text-primary` / `text-qds-success` / `text-destructive` / `text-qds-info` / `text-qds-warning` / `border` / `border-qds-border-hover` / `bg-qds-*-dim`）。新代码不允许出现 `bg-[var(--bg-p)]` 等 arbitrary-value 形式（业务代码 + `src/web/src/components/qds/`；shadcn 原语 `src/web/src/components/ui/**` 豁免）。

### FR-4 · 页面结构与信息架构重构（标准化方向 4）

- **FR-4.1** 允许拆分超长文件。当前必须拆分的文件（行数 > 700）：

| 文件 | 现状行数 | 拆分目标 |
|---|---:|---|
| `backtest/page.tsx` | 1754 | 按列表视图 / 详情视图 / URL 状态驱动 / 查询 / 分页 等关注点拆成不超过 4 个子模块，主文件 < 700 |
| `backtest/components/PerformanceTab.tsx` | 2059 | 按图表类型拆成独立子组件（Equity / Drawdown / Rolling / Returns / Distribution / …），主文件 < 700 |
| `backtest/components/TradesTab.tsx` | 847 | 按表格与筛选器拆分 |
| `backtest/components/OverviewTab.tsx` | 817 | 按 KPI 栅格与图表拆分 |
| `backtest/components/OverviewGreyTab.tsx` | 677 | 接近阈值，视与 OverviewTab 合并可能性评估后决定 |
| `research/page.tsx` | 991 | 按 Dataset 选择 / Factor 列表 / 图表面板 / 任务列表 / 配置面板 / 结果面板 / Accordion / Waterfall 拆分（至少 6 个子组件以承载 factor-research 原语迁移） |
| `research/report/[id]/ReportClient.tsx` | 757 | 按报告分节拆分（4 个子组件：KpiGrid / IcChart / LongShortChart / FactorTable；**Round 2 移除 ReportHeader**因调用点 0） |
| `strategies/page.tsx` | 754 | 接近阈值（超出 ≤ 8%），优先保守不拆（允许 executor 决定）|
| `optimization/page.tsx` | 736 | 接近阈值（超出 ≤ 6%），保守不拆（**显式决策：扫描合规后行数超限可豁免，避免破坏性变更；与 FR-4.1 表述一致**） |

- **FR-4.2** 拆分命名规范：子文件放在对应 `components/` 子目录；命名 `<TabName>Chart.tsx` / `<TabName>Table.tsx` / `<TabName>Header.tsx` / `use<Name>.ts` 等描述性命名。同一 Tab 拆出多个 Chart 的使用 `<ChartKind>Chart.tsx`（如 `EquityChart.tsx`）。
- **FR-4.3** 拆分不允许破坏：页面级 URL 状态、WebSocket 订阅生命周期、React context / provider 层级、shadcn Tooltip 的 `delay` prop 传递。
- **FR-4.4** 信息层级：重构后每个页面的分节应有明确 `SectionLabel` 作为节奏点；每个 KPI 栅格严格采用 `repeat(4, 1fr)` 或与 preview 卡片一致的列数；空状态遵循 `.claude/skills/TinoHelmDS/Web UI Kit.html` 的 empty state 模式 + 现有 `src/web/src/components/EmptyState.tsx` 的实现约定。

### FR-5 · 合规扫描脚本（配套基础设施）

- **FR-5.1** 新增 `src/web/scripts/verify-ds-compliance.sh`（bash + ripgrep），允许从仓库根或从 `src/web/` 执行：
  - 实现规则见 `3-tech-design.md §3.2`（R1-R14，Round 2 扩展至 14 条规则，Round 3 R14 正则风格改为 PCRE2 前后向断言与 R4 对齐）；
  - 输出：违规行以 `FILE:LINE:COL rule=<rule-id> sample=<excerpt>` 格式打印到 stderr；
  - Exit code：0 = 全部合规；1 = 存在违规；2 = 脚本错误（如 ripgrep 缺失、selftest 失败）；
  - 依赖 `rg`（ripgrep）；若宿主机无 `rg` 则 exit 2 并提示安装。
- **FR-5.2** 脚本可接受 `--fix-hint` 参数：每条违规后额外打印一行推荐迁移写法（取自 `3-tech-design.md` 的迁移映射表）。
- **FR-5.3** 脚本必须实现 `--selftest` 子命令，覆盖 **R4 / R6 / R7 / R8 / R9（含多行） / R10 / R12 / R13（含全部 11 variant） / R14（含 sc-column / fg-primary 等负例）** 的正/反例自动断言。详细用例见 `3-tech-design.md §3.2.8`。
- **FR-5.4** 脚本必须实现 `--preflight-before-css-delete` 子命令：仅运行 R1-R10+R12+R13+**R14**，exit 0 才允许 s10 启动删除操作。R14 保障 factor-research 散落位置（data-catalog / backtest Tab 含 OverviewGreyTab + OverviewTab hm-*）完成迁移后才删除 CSS 定义，防止视觉退化。

### FR-6 · 文档事实来源更新（配套基础设施）

- **FR-6.1** 在 `src/web/CLAUDE.md` 追加「标准化后的约束」章节，内容至少包含：
  - 四条标准化方向的总览规则；
  - Tailwind class 首选顺序：语义类（`bg-card`/`text-foreground`…） > QDS 扩展类（`text-qds-success`/`bg-qds-*-dim`…） > shadcn 原语属性 > 其它；绝不使用 `bg-[var(--*)]` 或 arbitrary-value 颜色；
  - QDS 业务组件强制复用清单（见 FR-3.1）；
  - Recharts 统一入口：`chartTheme.ts`；所有新图表先引入常量再使用；
  - 禁区 class 清单（`bt-*` / `dc-*` / `cg` / `ca` / `cr` / `ci` / `dim` 独立 token / factor-research 子系统 85 个 class）以及它们的 Tailwind 迁移对照；
  - 扫描脚本的调用方式与 CI 钩入建议；
  - **Historical Notes 区块**：声明本任务作废/取代 `feedback-bt-card-classes.md` / `feedback-use-existing-css.md` / `feedback-pixel-perfect.md` / `feedback-css-class-naming.md` 等历史 memory 的主张（详见 §1.9）；
  - **视觉参考源声明**：页面级视觉参考源改为 `.claude/skills/TinoHelmDS/`（Web UI Kit.html / Charts Spec.html / preview/ 下 21 个卡片），不再引用 `docs/ui/qds-*.html`（该目录不存在）；
  - **shadcn 原语豁免声明**：`src/web/src/components/ui/**` 目录豁免 R10 / dark: 前缀规则，因为是 shadcn upstream 代码。
- **FR-6.2** `src/web/CLAUDE.md` 既有「QDS CSS Classes (globals.css)」章节必须**改写**（非追加），删除 `bt-list/bt-row/bt-status/bt-progress/bt-expand`、`dc-filter-*/dc-qrow-*/dc-dtbl/dc-type-*/dc-cov-*/dc-pager-*/dc-chip-*/dc-sl/dc-modal-icon`、"Shared primitives `.btn/.btn-p/.btn-o/.btn-d`, `.sc/.sc-l/.sc-v`, `.fl/.fi/.fsel`, `.list`, `.empty`, `.dim/.cg/.cr/.ca`" 的列表，避免与「标准化后的约束」章节自相矛盾。
- **FR-6.3** `src/web/CLAUDE.md` 既有「Key Conventions」中 "Design-first development" 条目的 `docs/ui/` 引用必须删除或替换为 `.claude/skills/TinoHelmDS/`。

## 1.5 非功能需求

### NFR-1 · 主题一致性
dark（默认）与 light（`html.light`）两套主题下，所有页面文字对比度符合 WCAG AA（正文 4.5:1、大字 3:1）。token 驱动的颜色切换必须自动生效，无需在 `.tsx` 文件里硬编码 light 分支。

### NFR-2 · 可维护性
- `globals.css` 行数削减至 1210 行（±50）以内（约 780 行遗留定义被删除：`.bt-*` ~400 行 + `.dc-*` ~250 行 + `.cg/.ca/.cr/.ci/.dim/.mono` L1856 单行 + factor-research ~135 行 L1853-1987）。
- 超长文件 < 700 行（FR-4.1 列表；strategies/optimization 豁免）。

### NFR-3 · 可回溯性
由于涉及文件重命名/拆分，所有拆分操作必须通过 `git mv` 保留历史；新增子文件使用 `git add` 明确标记。

### NFR-4 · 自动化防回潮
扫描脚本可在 pre-push / CI 运行，执行时间 < 5 秒；误报率 0（通过 `--selftest` 保证）。

### NFR-5 · 范围稳定
禁止修改：`cli/`、后端 `src/tinohelm*/`、`.claude/skills/TinoHelmDS/` 本身、已有 API 行为、已有业务逻辑。保留 `--font-u` / `--font-d` 别名供 chartTheme 常量层间接使用，业务代码不再直接消费。

## 1.6 验收标准（Round 3 修订：视觉相关验收降级为 user acceptance in verify phase，遵守用户全局 MUST 规则）

本任务验收分为两层：
- **Subtask AC（自动化 gatekeeper）**：由扫描脚本、`npm run build`、`npm run lint`、行数断言等自动化手段判定，subtask 执行期硬性通过，**不含任何人工目测 / 手动验证项**（遵守 `/Users/ouzhuohao/.claude/CLAUDE.md` MUST 规则：验证或测试的内容不应出现手动验证相关 item）
- **User Acceptance in Verify Phase（主 agent 交付时由用户验证）**：视觉对照、双主题目测、barrel re-export 视觉差异判定等由 verify phase 完成，产出在 `.cage/tasks/.../verify.jsonl`，由主 agent 在 PR review 阶段向用户展示并汇总反馈；**不作为 subtask 的 acceptance_criteria**

### AC-1 · 代码扫描式验收（subtask AC · 自动化）

执行 `bash src/web/scripts/verify-ds-compliance.sh` 必须 exit 0。脚本内部检查（Round 2 扩展至 14 条规则；Round 3 R14 改为 PCRE2 前后向断言）：

| Rule ID | 检查内容 | 允许例外 |
|---|---|---|
| R1-font-inline | 禁止 `style={{…fontFamily: "var(--font-[ud])"…}}` 及字符串形态 | `src/web/src/lib/chartTheme.ts` 作为常量本体允许 |
| R2-legacy-class-bt | 禁止 `className` 含 `\bbt-[a-z0-9-]+\b` | 0 |
| R3-legacy-class-dc | 禁止 `className` 含 `\bdc-[a-z0-9-]+\b`；**`TYPE_BADGE_CLS` 字典的 value 字符串也必须扫到**（Round 2 加强） | 0 |
| R4-legacy-class-single | 禁止 `className` 中包含 `cg\|ca\|cr\|ci\|dim\|mono` **作为独立 token**（PCRE2 前后向断言）。Tailwind 原生 `font-mono` / `bg-qds-*-dim` / `text-qds-info-dim` 必须豁免（验证方法：`--selftest` 子命令）| 0 |
| R5-hardcoded-hex | 禁止 `bg-\[#…\]` / `text-\[#…\]` / `border-\[#…\]` 及 `color: "#…"` 内联 | 0 |
| R6-tooltip-spread | 强制 Recharts `<Tooltip` / `<RechartsTooltip` 含 `{...CHART_TOOLTIP_PROPS}` spread（唯一形式；支持多行；selftest 覆盖） | 0 |
| R7-grid-spread | 强制 `<CartesianGrid` 含 `{...CHART_GRID_STYLE}` spread（允许额外 prop 覆盖 + prop 顺序任意；两阶段 rg -U 实现；selftest 覆盖） | 0 |
| R8-legend-spread | 强制 `<Legend` 含 `wrapperStyle={CHART_LEGEND_STYLE}` 或 `{...CHART_LEGEND_STYLE}` spread（支持 spread-extra-prop 如 `{{ ...CHART_LEGEND_STYLE, fontSize: 10 }}`；selftest 覆盖） | 0 |
| R9-reference-line-label | 强制 Recharts `<ReferenceLine label={{…}}>` 对象 label 含 `...CHART_LABEL_STYLE` spread（**必须 `rg -U --multiline-dotall`**；覆盖 RiskTab/RobustnessTab 单行与 OverviewTab/ReportClient 多行；selftest 覆盖 2 种语法） | 0 |
| R10-arbitrary-token | 禁止 `bg-\[var\(--` / `text-\[var\(--` / `border-\[var\(--` 等 arbitrary-value token 形式 | `src/web/src/components/ui/**`（shadcn 原语） |
| R11-globals-legacy | 确认 `globals.css` 不再包含 `.bt-` / `.dc-` / `.(cg\|ca\|cr\|ci\|dim\|mono)\{` / factor-research 子系统 class 定义（扫描采用非行首锚定模式以覆盖 L1856 同行组合定义） | 0 |
| R12-fontsize-inline | 禁止 `style={{[^}]*fontSize:}` 业务内联（Recharts `wrapperStyle` / `contentStyle` / `labelStyle` / `CHART_*_STYLE` spread 上下文豁免） | 0 |
| R13-undefined-var | 禁止 `var(--accent-(green\|orange\|red\|amber\|blue\|purple)(-?(10\|20))?)` 等 **11 种**未在 globals.css 定义的变体（Round 3 实测 11 variant）；`--accent-foreground` 是 shadcn 内置豁免 | 0 |
| **R14-factor-research-primitive（Round 3 改为 PCRE2 前后向断言）** | 禁止 `.tsx` 业务代码中出现 factor-research 原语 className（`sc/cd/sl/fl/fi/fsel/ctbl/dtab/cd-h/cd-b/sc-l/sc-v/sc-sub/turn-*/verdict*/factor-dot/hm-grid/hm-label/hm-cell/...` 等 85 个顶层 class）；正则采用 `(?<![-a-zA-Z0-9_])(TOKEN)(?![-a-zA-Z0-9_])` 前后向断言（与 R4 风格统一），确保 `className="sc-column"` / `className="fg-primary"` 等非 factor-research 复合类不命中 | 0 |

### AC-2 · 视觉对照（**Round 3 降级为 User Acceptance in Verify Phase；不作为 subtask AC**）

在 dark mode 下逐页对照 `.claude/skills/TinoHelmDS/preview/*.html` 的对应卡片 + `Web UI Kit.html` 的完整 dashboard frame：

- **色差**：元素的背景 / 文字 / 边框色与 preview 渲染结果一致（使用相同 token）；
- **间距**：`padding` / `gap` / `border-radius` 误差 ≤ 2px；
- **字体**：Inter（UI） / JetBrains Mono（数据）全部正确分层；
- **accent**：焦橙只出现在 FR-2.1 列出的位置；
- **图表**：Recharts 网格、tooltip、legend、axis 样式一致。

**验证角色分工**：
- **subtask 层**：`verify-ds-compliance.sh` 的 R1-R14 全部通过（覆盖 token 使用、class 纪律、Recharts spread）— 这是"token 使用正确性"的自动化 gatekeeper
- **verify phase（主 agent + 用户）**：subtask 完成后由主 agent 启动 `cd src/web && npm run dev`，用户在浏览器内对每个路由页面与对应 preview/*.html 比对。若出现严重视觉偏差（gridTemplateColumns 错乱、accent 误用、字体层级错位），用户在 verify 阶段给出反馈，主 agent 派 agent 回迁入对应 sN。此环节为**用户验收**，不作为 subtask AC

**Fallback 路径（verify phase 内）**：若 verify 阶段暂未接入视觉回归工具（例如 Playwright 截图 diff），对照矩阵退化为 "3-tech-design.md §3.4 preview 对照矩阵 + `src/web/CLAUDE.md`「标准化后的约束」章节" 的文档化检查。

### AC-3 · Dark + Light 双主题验证（**Round 3 拆为两部分**）

**AC-3a（subtask AC · 自动化）**：
- 扫描脚本 R11 通过后：`cd src/web && npm run build` 无错误无警告（除已知的 Next.js 静态导出提示）
- `cd src/web && npm run lint` 通过
- 脚本 `bash src/web/scripts/verify-ds-compliance.sh --mode both-themes` exit 0：**断言没有任何 `text-foreground` / `bg-card` / `border` 之外的颜色写死在业务 `.tsx` 中；并在 globals.css 中 `.light` 作用域下存在 `--bg-s / --t0 / --bd` 等核心 token 的 override 定义**
- `--mode both-themes` 扫描**排除**以下目录：`src/web/src/components/ui/**`（shadcn 原语，允许 `dark:` 前缀）、`src/web/src/components/qds/**`（QDS 业务组件已就绪，不再修改）

**AC-3b（User Acceptance in Verify Phase）**：
- 主 agent 在 verify 阶段启动 dev server，用户在浏览器分别切换 dark / light 主题，对 14 个路由页面做视觉检查（文字对比度、色板切换、accent 表现、图表网格可见度）
- 发现偏差由用户反馈，主 agent 派 agent 修补

### AC-4 · 文档事实来源（subtask AC · 自动化）

- `src/web/CLAUDE.md` 包含「标准化后的约束」章节（FR-6.1 规定的全部内容）；
- 章节包含扫描脚本调用片段、禁区 class 清单、Tailwind 映射表、Historical Notes、视觉参考源声明、shadcn 原语豁免；
- 既有「QDS CSS Classes (globals.css)」章节已被改写（FR-6.2），「Key Conventions」中 `docs/ui/` 引用已替换（FR-6.3）；
- 章节结尾引用 `.claude/skills/TinoHelmDS/SKILL.md` 与 `3-tech-design.md` 的 preview 对照矩阵章节作为事实来源。

### AC-5 · StatusBadge barrel 视觉差异（**Round 3 新增 · User Acceptance in Verify Phase**）

**背景**：s11 将顶层 `components/StatusBadge.tsx` 改为 barrel re-export，使 legacy 调用点的外观从 shadcn Badge 的 `rounded-md` 变为 QDS `<span>` 的 `rounded-full`。

**subtask 层（s11 的 AC）**：
- `rg -n '\bbt-status\b' src/web/src/app/data-catalog/JobQueue.tsx` 命中 0 行
- `components/qds/status-badge.tsx` 的 `Status` union 包含全部 7 个键
- `cd src/web && npm run build` / `npm run lint` 通过
- **无"逐页目测"步骤**

**verify phase（用户验收）**：
- 主 agent 启动 dev server，用户在浏览器打开 backtest/page / optimization/page / data-catalog/JobQueue / research 历史 Job 行，对比 rounded-md vs rounded-full 视觉差异
- 若用户判定差异过大（影响 UX 辨识度），主 agent 派 agent 按 §3.3.9 fallback 方案（保留顶层 `<Badge>` 外观，内部查表改为 QDS map）回迁；工作量追加 0.5-1h

## 1.7 非目标（Non-goals）

- **后端 / API**：不修改 FastAPI 路由、Redis key、PostgreSQL schema、alembic 迁移、Python 业务逻辑。
- **新业务功能**：不新增任何业务视图、导航项、新的 WebSocket 事件；不调整 API 字段。
- **CLI / TUI**：Rust `cli/` 目录完全不碰（项目 CLAUDE.md 禁区）。
- **业务逻辑测试**：不修改或新增 `tests/` 下 Python 业务逻辑测试；但允许在 `src/web/scripts/` 下新增合规扫描脚本。
- **`.claude/skills/TinoHelmDS/` 本身**：不修改 skill 任何内容，仅作事实来源对照。
- **动画语言扩展**：不新增 keyframes / 缓动曲线，仅使用 QDS 既有的 `qds-fade-up` / `qds-pulse` / `qds-shimmer` / `qds-tick-g/r` 等。
- **字体系统扩展**：不引入第三种字体；现有 `--font-u` / `--font-d` 作为向后兼容别名保留（globals.css 内），但业务代码不再直接消费。
- **shadcn 原语内部结构**：`src/web/src/components/ui/**` 保持 upstream 状态（含 `dark:` 前缀、`bg-[var(--*)]` arbitrary-value 等），扫描规则豁免。
- **横切组件**：`components/motion/**`（FadeIn / StaggerContainer / PageTransition）、`components/NotificationListener.tsx` 不在迁移范围。
- **项目级 `/Users/ouzhuohao/TinoHelm/CLAUDE.md`**：该文件引用不存在的 `docs/ui/qds-*.html`，属于 cage 全局文档，本任务**不修改**（out-of-scope），但在 `src/web/CLAUDE.md`「标准化后的约束」章节显式声明视觉参考源已改。

## 1.8 依赖与前置条件

- Next.js 16 + React 19 + Tailwind v4 + shadcn/ui v4（base-nova）stack 已稳定（`src/web/package.json` 已定型）。
- `components/qds/` 下 7 个业务组件已就绪（验证存在）。
- `chartTheme.ts` 已存在并暴露 `CHART_TOOLTIP_PROPS` / `CHART_GRID_STYLE` / `CHART_AXIS_STYLE` / `CHART_COLORS` / `CHART_GRADIENT_OPACITY` / `CHART_REFERENCE_LINE` / `CHART_ANIMATION`（验证存在）。
- TinoHelmDS skill 的 21 个 preview HTML 文件 + `Web UI Kit.html` + `Charts Spec.html` + `QDS Pitch Deck.html` + `colors_and_type.css` 已就绪（验证存在 @ 2026-04-19）。
- **`docs/ui/` 目录不存在**（验证 @ 2026-04-19：`ls /Users/ouzhuohao/TinoHelm/docs/ui/` → No such file or directory）。
- ripgrep（`rg`）工具在开发环境可用；CI 环境需预装（本任务不涉及 CI workflow 修改，但脚本开头 `command -v rg` 检查）。
- 宿主机 ripgrep 版本需支持 `--pcre2` flag（macOS Homebrew 默认、ripgrep 11+）以实现 R4 / R8 / R14 的前后向断言与复杂边界匹配。

## 1.9 与历史用户 memory 的关系（Round 1 新增 — 冲突声明；**Round 2 降语**）

**Round 2 修订**：本节原 r1 版本写"经用户明确授权"作废 memory，但 `interview.md` 原文（4 轮问答全文）未显式提及 memory 文件、feedback 条目或"作废"字样；只是第 4 轮用户选择"迁移调用点 + 完全删除 globals.css 定义（最彻底选项）"。此选择**隐含**了与以下历史 memory 主张的冲突，但不等于用户"明确授权作废 memory"。因此本节降语为"**本规划默认覆盖**以下历史 memory 的主张 — 该方向已由用户在 interview.md 第 4 轮选择'完全删除遗留 class'隐含；**执行完成后由主 agent 负责向用户确认并更新以下 memory 文件**"。

| Memory 文件 | 原主张 | 本任务处理 |
|---|---|---|
| `feedback-bt-card-classes.md` | backtest 卡片**必须**用 `bt-cd/bt-cd-header/bt-cd-body`，禁止 shadcn `<Card>` | **默认覆盖** — s4 将 `bt-cd` 族迁移到 `<Card>/<CardHeader>/<CardContent>` shadcn 三件套（详见 3-tech-design.md §3.3.3） |
| `feedback-use-existing-css.md` | 绝不重新定义已有 class；直接用 globals.css 的 `.btn/.sc/.cd/.ctbl/.sl/.fl/.fi/.fsel/.empty/.turn-row` | **默认覆盖** — FR-1.3 / FR-1.4 完全删除这些 class 定义，调用点迁移到 Tailwind / shadcn（详见 §3.3.7） |
| `feedback-pixel-perfect.md` | 优先复用 globals.css 已有 class，不要用 Tailwind 重新实现；class 名必须和 HTML 参考一致 | **默认覆盖** — FR-3.4 新代码不允许 `bg-[var(--bg-p)]`；FR-1.2 `cg→text-qds-success` 等改名；视觉参考源由 `docs/ui/qds-*.html`（不存在）改为 `.claude/skills/TinoHelmDS/` |
| `feedback-css-class-naming.md` | HTML reference（`qds-*.html`）使用 `cd/ctbl/sl/sc` — 业务 tsx 必须用同名 class | **默认覆盖** — 新事实源为 `.claude/skills/TinoHelmDS/Web UI Kit.html` 与 preview 卡片，不使用 globals.css legacy class 名 |
| `feedback-pixel-perfect-reference.md` | Replicate ALL UI components from qds-*.html | **调整** — 视觉保真要求保留（AC-2），但参考文件改为 `.claude/skills/TinoHelmDS/` skill |

**Post-task todo（Round 2 明确化）**：本任务完成后（s12 定稿阶段），主 agent 负责：
1. 向用户确认上述 memory 的作废决定（interview.md 第 4 轮选择已隐含此方向，但明确确认可降低未来歧义）；
2. 更新 `/Users/ouzhuohao/.claude/projects/-Users-ouzhuohao-TinoHelm/memory/MEMORY.md` 中相关条目，标注作废时间戳与取代文件路径（`src/web/CLAUDE.md`「标准化后的约束」章节）；
3. 追加新 memory 条目记录本任务的标准化决策（扫描脚本 / 禁区 class / 视觉参考源 / StatusBadge API），覆盖面 ≥ 本次规划的 FR-1..FR-6。

当前规划阶段仅在 `src/web/CLAUDE.md`「标准化后的约束」章节「Historical Notes」区块内显式声明此冲突处理，供 executor / verifier 参考；主 agent 承接的上述 3 个 post-task todo 不占用本任务的 12 个 subtask 槽位。
