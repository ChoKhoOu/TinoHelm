# Architect Review — Round 1

**VERDICT: REVISE**

## 摘要

规划文档整体结构工整，分层与 DAG 设计合理（波次 A→B→C→D），对既有架构（globals.css token 底层 / QDS 业务组件 / shadcn 原语 / chartTheme.ts）保持顺延。但在**代码引用事实性**与**扫描规则正确性**两个维度发现了 2 个 CRITICAL、5 个 MAJOR 缺陷，直接影响 executor 的实现路径。最严重的问题是 **R4 扫描规则（`\bmono\b` / `\bdim\b`）会对 Tailwind 原生类 `font-mono` / `bg-qds-*-dim` / `text-qds-*-dim` 产生 >300 处误报**，以及 **research/page.tsx 使用的约 30 余个未被本次范围识别的共享 CSS 原语（`.sc / .cd / .sl / .fi / .fsel / .fl / .ctbl / .dtab / .hm-* / .wf-* / .hbar / .acc-* / .param-* / .cfg-* / .rpt-*` 等）**，导致 s6 的迁移范围严重低估。

## 代码引用验证

| 引用 | 文件存在 | 内容准确 | 问题 |
|------|---------|---------|------|
| `src/web/src/app/backtest/page.tsx` (1754 行) | Yes | Yes | — |
| `src/web/src/app/backtest/components/PerformanceTab.tsx` (2059 行) | Yes | Yes | — |
| `src/web/src/app/backtest/components/OverviewTab.tsx` (817 行) | Yes | Yes | — |
| `src/web/src/app/backtest/components/TradesTab.tsx` (847 行) | Yes | Yes | — |
| `src/web/src/app/backtest/components/OverviewGreyTab.tsx` (677 行) | Yes | Yes | — |
| `src/web/src/app/research/page.tsx` (991 行) | Yes | Yes | — |
| `src/web/src/app/research/report/[id]/ReportClient.tsx` (757 行) | Yes | Yes | — |
| `src/web/src/app/strategies/page.tsx` (754 行) | Yes | Yes | — |
| `src/web/src/app/optimization/page.tsx` (736 行) | Yes | Yes | — |
| `src/web/src/app/page.tsx` (585 行) | Yes | Yes | — |
| `src/web/src/app/analytics/page.tsx` (540 行) | Yes | Yes | — |
| `src/web/src/app/orders/page.tsx` (548 行) | Yes | Yes | — |
| `src/web/src/app/watchlist/page.tsx` (465 行) | Yes | Yes | — |
| `src/web/src/app/trading/page.tsx` (454 行) | Yes | Yes | — |
| `src/web/src/app/settings/page.tsx` (332 行) | Yes | Yes | — |
| `src/web/src/app/data-catalog/page.tsx` (333 行) | Yes | Yes | — |
| `src/web/src/app/globals.css` (1987 行) | Yes | Yes | — |
| `src/web/src/lib/chartTheme.ts` (83 行) | Yes | Yes | — |
| `src/web/src/components/qds/{StatCard,PageHeader,SectionLabel,InlineError,StatusBadge,HelpTip,ShimmerBar}` | Yes（文件 `help-tip.tsx`、`page-header.tsx`、`section-label.tsx`、`shimmer-bar.tsx`、`stat-card.tsx`、`status-badge.tsx`、`InlineError.tsx` 全部存在） | Yes | — |
| `.claude/skills/TinoHelmDS/preview/*.html` (21 个) | Yes（21 个文件清点一致） | Yes | — |
| `src/web/scripts/check-grep-fonts.sh`（作为新脚本先例） | Yes | Yes | — |
| `trading/page.tsx` 有 "bt-* 4 处" 映射 | Yes | **NO** | trading/page.tsx 实测 0 处 `bt-*` 调用；s7 描述与 §3.9 都出现这一虚构引用 |
| `<Label>` 子组件在 research/analytics/TradesTab 共 4 处 | Yes | **NO** | 实测全仓 `<Label\b` 命中 0；三个文件都没有 `<Label>` 标签；只有 `ReferenceLine` 的 `label={...}` prop（两种语法差异大） |
| globals.css 中 `.dc-*` 家族从 L1659 起 | Yes | **NO** | 实测 `.dc-sl` 起于 L1640（偏差 19 行，写 diff/删除范围时需按实际） |
| globals.css 遗留 class 总条数 `.bt-* 134` `.dc-* 76` | Yes | Yes | 与实测一致 |
| 内联 `style={{ fontFamily: "var(--font-[ud])" }}` 跨 5 文件 28 处 | Yes | 大致 Yes | 实测 `style=\{\{[^}]*fontFamily` 共 28 处，文件集基本一致（backtest/page 15、trading/page 5、research/page 3、data-catalog/page 3、data-catalog/FetchDialog 2） |

## 需求审查 (1-requirements.md)

### Critical 发现

1. **`cg/ca/cr/ci/dim/mono` 调用点数量严重低估（25 处 → 实际 ≥ 198 处）**
   - 证据：§1.1 表格声称"`className="cg/ca/cr/dim"` 调用 25 处跨 12 文件"。实际用 stricter 正则 `className[^=]*=[`"'][^"'`]*\b(cg|ca|cr|ci|dim|mono)\b[^"'`]*[`"']` 扫描得到 198 处跨 37 文件。
   - 但这里的"多"绝大部分是**误报**：`font-mono`、`bg-qds-accent-dim`、`text-qds-info-dim`、`bg-qds-success-dim` 等 Tailwind 原生类都落在 `\bmono\b` / `\bdim\b` 匹配范围内。
   - 影响：§1.1 的违规统计本身不可信；但**真正关键的问题**是 R4 扫描规则会将这些合法 Tailwind 类误判为违规（见 3-tech-design.md §3.2.3）。
   - 修复：§1.1 的 25 处需要重新调查（带上下文排除 Tailwind 类），并更新为精确计数；扫描规则 R4 必须同步修正以排除 `font-mono` / `*-dim` 后缀模式（见 tech-design §3.2.3 Critical 发现）。

### Major 发现

1. **违规统计遗漏 `bg-[var(--*)]` / `text-[var(--*)]` 类（R10 规则覆盖但需求未计数）**
   - 证据：`rg -c 'bg-\[var\(--|text-\[var\(--|border-\[var\(--' src/web/src` 命中 54 处跨 10 文件（含 button.tsx、backtest/page、OverviewTab、EditorClient 等）。
   - §1.1 违规统计表没有此类型，导致子任务工作量估算偏低。
   - 修复：§1.1 表增加一行 "`bg/text/border-[var(--*)]` arbitrary-value token 形式" 并给出调用点分布。

### Minor 发现

1. §1.3.1 表中 "浮壳 `/strategies/[name]/page.tsx` 11 行" 正确，但括号内"EditorClient.tsx 200 行"实测是 200 行（匹配），可保留。
2. AC-2 "视觉对照由后续阶段的截图工具在 verify 阶段自动采集" — 该工具不存在于当前仓库，AC-2 实质上退化为文档化承诺。应明确标注 AC-2 在本任务的**操作化**含义（仅文档+preview 对照矩阵）。

## 技术设计审查 (3-tech-design.md)

### Critical 发现

1. **R4 扫描规则正则 `\b(cg|ca|cr|ci|dim|mono)\b` 会对 Tailwind 合法类产生大量误报，导致扫描脚本根本无法通过**
   - 证据（实测）：
     - `echo 'className="font-mono text-[0.68rem]"' | rg '\b(cg\|ca\|cr\|ci\|dim\|mono)\b'` → 命中
     - `echo 'className="bg-qds-accent-dim text-primary"' | rg '\b(cg\|ca\|cr\|ci\|dim\|mono)\b'` → 命中
     - `echo 'let cls = "bg-qds-info-dim";' | rg '\b(cg\|ca\|cr\|ci\|dim\|mono)\b'` → 命中
   - 根因：`-` 是非单词字符，`\b` 边界在 `font-mono` 的 `-` 和 `m` 之间、`*-dim` 的 `-` 和 `d` 之间均成立。ripgrep 的 `\b` 与 POSIX/Rust regex 一致。
   - 受影响调用点规模：`rg -c 'font-mono' src/web/src` ≈ 400 行，`bg-qds-*-dim` / `text-qds-*-dim` ≈ 150 行 — 相当于扫描脚本会在现状下输出 **500+ 误报**，完全淹没真正违规；脚本在 s12 永远无法 exit 0。
   - 修复（必须）：
     a. R4 正则必须用"整个 className 值中作为独立 token"的形式，例如先抓取 `className="..."` 字符串内容，然后用空格切分后检查每个 token 是否**完全等于** `cg` / `ca` / `cr` / `ci` / `dim` / `mono`（非子串匹配）；或
     b. 改用负向边界：`(?<![-a-z0-9_])(cg|ca|cr|ci|dim|mono)(?![-a-z0-9_])` — 这样 `font-mono` 的 `mono` 前面是 `-`，被排除（`-` 在 `[-a-z0-9_]` 内）；同样 `*-dim` 的 `dim` 前面是 `-`，被排除。
     c. R3-legacy-class-dc 的 `\bdc-[a-z0-9-]+\b` 也有类似但较弱的风险（如果存在 `data-dc-foo` 类名会误报）；评估后可加类似前向边界。
   - 建议：§3.2.3 R4 改写为：`(?<![-a-zA-Z0-9_])(cg|ca|cr|ci|dim|mono)(?![-a-zA-Z0-9_])`，并添加 smoke test 条目"`font-mono`、`bg-qds-accent-dim`、`text-qds-info-dim` 必须不触发 R4"。

2. **范围严重低估：research/page.tsx 使用的 ~35 个 factor-research 全局 CSS 原语未被列入迁移范围**
   - 证据：globals.css L1853-1987 定义了 "Factor Research" 子系统的共享原语：`.sc` / `.sc-l` / `.sc-v` / `.sc-sub` / `.cd` / `.cd-h` / `.cd-b` / `.sl` / `.fl` / `.fi` / `.fsel` / `.fg` / `.frow` / `.ctbl` / `.acc-*` / `.param-*` / `.cfg-*` / `.rpt-*` / `.hm-*` / `.wf-*` / `.hbar` / `.dtab` / `.verdict*` / `.tip` / `.badge` / `.spinner` / `.factor-dot` / `.turn-*` / `.data-avail` / `.action-row` 等。
   - 实测 research/page.tsx 含 33 处 `sc/cd/sl/btn/fl/fi/fsel/ctbl/dtab` 调用 + 29 处 `hm-/wf-/hbar/acc-/param-/cfg-` 调用 = 62 处（直接统计结果）。全仓跨这些 selector 家族的调用点 >280 处跨 15 文件。
   - tech-design §3.3.2 只提及 `cg/ca/cr/ci/dim/mono` 6 个单字母 class 的迁移；§3.3.3-3.3.4 只覆盖 `bt-*` / `dc-*`；§3.3 完全没有覆盖 `.sc / .cd / .sl / .fl / .fi / .fsel / .ctbl / .hm-* / .wf-* / .hbar / .acc-* / .param-*` 等。
   - 同时 s10 明确"保留 shared primitives：`.btn` / `.sc` / `.fl` / `.list` / `.empty`"，与 §1.1 需求"完全删除遗留 class"相冲突 — **在 research 页面仍依赖 `.sc` / `.cd` / `.fl` / `.fi` 的前提下，这些 class 必须保留；但保留就意味着 research 页面本轮没有真正完成"迁移到 Tailwind"**。
   - 修复（必须二选一）：
     a. **扩大范围**：§1.3 显式纳入 factor-research 子系统的 ~35 个原语，§3.3 补齐映射表（`.sc` → `<Card className="...">` 或 StatCard、`.cd` → shadcn Card、`.sl` → `SectionLabel`、`.fl` → `<label className="text-xs ...">`、`.fi`/`.fsel` → shadcn Input/Select、`.ctbl` → shadcn Table、`.hm-*` → 专用 Heatmap 组件或 Tailwind grid、`.dtab` → shadcn Tabs 等）；s6（/research 迁移）工作量对应上调至 ~10-12 小时；s10 删除范围对应扩大。
     b. **缩小目标**：承认 research 子系统本轮只做"内联 style / `cg/ca/cr/ci/dim/mono` / Legend/Label"三项，不动 `.sc`/`.cd`/`.sl`/`.fl`/`.fi`/`.fsel`/`.ctbl`/`.hm-*`/`.wf-*`/`.hbar`/`.acc-*`/`.param-*`/`.dtab`，并在 NFR-2 `globals.css` 行数目标中保留相应 ~130 行，更新 CLAUDE.md 禁区清单。
   - 选择 (a) 还是 (b) 由 planner 决定，但**不能保持当前"既删又用"的矛盾状态**。

### Major 发现

1. **`<Label>` 组件 4 处内联样式是虚构引用（R9 规则对应源为零）**
   - 证据：`rg -n '<Label\b' src/web/src` 全仓命中 0 行。
   - §3.3.5 声称"`<Label style={{…fontSize: .62rem, fontFamily: var(--font-d), fill: var(--t2)…}} value="…" />`（research/analytics/TradesTab 共 4 处）"。
   - 实际类似功能是 `<ReferenceLine label={{ value: "...", fontSize: 9, fill: "var(--warn)" }} />`（RobustnessTab:353、RiskTab:187）— 是 `ReferenceLine` 的 `label` **prop**，不是 `<Label>` 子组件。两者语法树完全不同。
   - 影响：
     a. R9 扫描规则 `<Label\b[^>]*style\s*=` 永远命中 0 — 规则本身不会"报错"，但也不会产生任何保护作用（零样本确认）。
     b. §1.1 违规统计中的"`<Label style={…} />` 内联 4 处"是虚假违规。
     c. `CHART_LABEL_STYLE` 常量被定义后在业务代码中没有真实消费场景。
   - 修复：
     a. 明确 `CHART_LABEL_STYLE` 真实消费场景。选项 1：扩展 R9 规则为 `<ReferenceLine\b[^>]*label\s*=\s*\{\s*\{[^}]*(fontSize|fill|fontFamily)`，并把 RobustnessTab / RiskTab 两处 label prop 迁移到 `label={{ ...CHART_LABEL_STYLE, value: "..." }}`（需验证 ReferenceLine 的 label prop 接受 CSSProperties + 额外字段）。选项 2：删除 `CHART_LABEL_STYLE` 与 R9 规则（本仓库不需要）。
     b. §3.3.5、§1.1 违规表、s6 描述同步修正。

2. **`trading/page.tsx` 的 bt-* 迁移量虚构（实际 0 处）**
   - 证据：`rg -c 'bt-[a-z]' src/web/src/app/trading` 命中 0 文件。
   - 4-tasks.md s7 描述："内联 `fontFamily` 6 处（trading/page）+ ...RiskTab 与 trading/OverviewTab 各 1 处硬编码颜色占位"，以及 3-tech-design.md §3.9 影响文件清单 trading/page.tsx "改 | 迁移 6 处 bt-*（4 处）和内联 style"。
   - 修复：§3.9 与 s7 删除 bt-* 叙述；trading 实际工作只有：(a) trading/page.tsx 6 处 `fontFamily`，(b) 零星 `cg/ca/cr` 单字母 class（需用修正后的 R4 扫描确定），(c) `<Tooltip>` / `<CartesianGrid>` spread（RiskTab 用了 `CartesianGrid` 3 处、OverviewTab 3 处、StrategyDetailPanel 2 处，需验证是否已 spread `CHART_GRID_STYLE`）。

3. **data-catalog dc-* 计数与内联 style 计数轻度偏差**
   - 证据：
     - tech-design 声称 dc-* 调用"44 处跨 5 文件"，实测 `className[^"]*["'][^"]*\bdc-[a-z0-9-]+` 命中 51 处跨 5 文件（page 21 > 声称 17、FilterTabs 7 > 声称 4、其余一致或略多）。
     - 内联 `fontFamily` data-catalog 声称 "page 3 + FetchDialog 2 = 5"，实测 6 处。
   - 修复：§1.1 / §3.3.4 / s5 同步刷新最新计数；s5 工作量估算从 3h 上调到 3.5-4h。

4. **`.ctbl` 及关联 factor-research 原语被 `src/web/CLAUDE.md` 显式声明为保留 "Shared primitives"，但 research 页面迁移要求与之冲突**
   - 证据：`src/web/CLAUDE.md` 的「QDS CSS Classes (globals.css)」章节列出 Shared primitives：`.btn/.btn-p/.btn-o/.btn-d, .sc/.sc-l/.sc-v, .fl/.fi/.fsel, .list, .empty, .dim/.cg/.cr/.ca`。
   - 1-requirements.md FR-1.2 把 `cg/ca/cr/ci/dim/mono` 列为禁区；s10 把 `.btn/.sc/.fl/.list/.empty` 列为保留；但 research/page 同时使用了所有这些。
   - 修复：在 §3.3 增补对这个冲突的显式声明（"`src/web/CLAUDE.md` 的 Shared primitives 列表将在 s12 同步修改，使其与本任务保持一致"）；并指出 `src/web/CLAUDE.md` 这一章节本身需要被改动（目前 s3/s12 没有提到）。

5. **R11 扫描规则的 `\n\.` 前缀在多行模式下可能漏检 globals.css 中"被其它定义前缀的 class"**
   - 证据：§3.2.3 R11 的正则 `(?m)^\.bt-[a-z-]+\s*\{|^\.dc-[a-z-]+\s*\{|^\.(cg|ca|cr|ci|dim|mono)\s*\{` 只匹配**行首**的 `.bt-` / `.dc-`。但 L1856 `.mono{...}.dim{...}.cg{...}.cr{...}.ca{...}.ci{...}` 是单行定义，只有 `.mono` 在行首，`.dim/.cg/.cr/.ca/.ci` 紧跟在 `}` 后同一行 — 该行能且仅能匹配 `.mono`，遗漏其余 5 个。若 s10 没有完全删除整行（误留 `.dim{color:var(--t2)}`），R11 验证也不会报警。
   - 修复：R11 改为 `\.bt-[a-z-]+\s*\{|\.dc-[a-z-]+\s*\{|(^|[;\}])\s*\.(cg|ca|cr|ci|dim|mono)\s*\{`（去掉行首锚定，或者明确要求 s10 把 L1856 整行删除作为原子操作）。

### Minor 发现

1. §3.3.5 CHART_GRID_STYLE 的"PerformanceTab 6+ 处 strokeDasharray=\"3 3\" 保留"描述准确（实测 PerformanceTab 有 12 处 CartesianGrid，绝大多数带 `strokeDasharray="3 3"`）；§3.7.1 的保留 override 策略合理。
2. §3.3.6 新增 `CHART_LEGEND_STYLE` 的 `color: "var(--t1)"` 与现状 `wrapperStyle={{ fontSize: ".62rem", fontFamily: "var(--font-d)" }}` 并不完全等价（现状没有 color，会继承 Recharts 默认）。这个增量是合理的改进，但需要在 research/page:802、868 两处明确加入 color 属性后视觉上稍深于原 Recharts 默认 `#333`/`fff` 回退，应标注可能出现的极细微视觉变化。
3. §3.3.1 字体迁移表 `text-[11px]` 近似 `.7rem` 的说法：`.7rem` × `16px` = `11.2px`，Tailwind `text-xs` = `12px`，`text-[11px]` = `11px` 都不完全等价 — 建议默认保留 `text-[11px]` arbitrary-value（与 R10 规则兼容，因为 R10 只禁 `bg-[var()`/`text-[var()`/`border-[var()`，未禁任意像素值）。
4. `CHART_LEGEND_STYLE` 与 `CHART_LABEL_STYLE` 的 `fontFamily: "var(--font-d)"` 与注释"Recharts 直接消费 var() 是推荐用法"一致，但两者在 chartTheme.ts 里已可通过 `font-mono` 的 CSS 类间接生效（如果图表父容器没有强制覆盖）；这属于"稳重不冒险"策略，可保留。

## DAG 审查 (4-tasks.md + task.json)

### 合法性

- **拓扑无环**：s1-s3 → s4-s9 → s10/s11 → s12 是有向无环的标准四波次结构。
- **依赖正确性**：s4-s9 depends_on [s1, s2] 合理 — s1 提供 `CHART_LEGEND_STYLE`/`CHART_LABEL_STYLE`，s2 提供扫描基线；s10/s11 depends_on [s4..s9] 合理（调用点迁移完才能删 CSS 定义）；s12 depends_on [s10, s11] 合理。
- **但 s6 对 s1 的依赖实际上是弱依赖**：s1 新增的 `CHART_LEGEND_STYLE` 是 s6 消费的（research/page Legend 2 处）；`CHART_LABEL_STYLE` 没有真实消费场景（见 Major 发现 1）。依赖仍成立，但 s1 的一半内容是 dead code。

### 并行性

- **第 1 波次 (s1/s2/s3)**：文件集无交集（chartTheme.ts / scripts/verify-ds-compliance.sh / src/web/CLAUDE.md）— 真并行 ✓
- **第 2 波次 (s4-s9)**：
  - s4 改 `src/app/backtest/**` — 独占
  - s5 改 `src/app/data-catalog/**` — 独占
  - s6 改 `src/app/research/**` — 独占
  - s7 改 `src/app/trading/**` — 独占
  - s8 改 `src/app/{analytics,optimization,orders,watchlist}/**` — 独占
  - s9 改 `src/app/{page.tsx,strategies,settings,layout.tsx}` — 独占
  - 文件集互不相交 ✓ **但是**：s9 `src/app/page.tsx` 与 s8 `src/app/analytics/page.tsx` 都 import `@/lib/chartTheme`；在本波次中任何一个 task 对 chartTheme.ts 的变更是禁止的（s1 已完成），所以并行安全 ✓。
  - 唯一风险：s4-s9 并行时都会读 `globals.css`（只读），但不写 — 无写冲突。
- **第 3 波次 (s10/s11)**：
  - s10 只改 `src/web/src/app/globals.css`
  - s11 改 "`src/web/src/components/**`、`providers/**`、`hooks/**`" — 明确排除 globals.css
  - 不相交 ✓
  - 但 s11 验收标准 `rg -n 'bt-|dc-|var\(--font-[ud]\)' src/web/src` 全 src 扫描 — 如果 s10 同时正在修改 globals.css，此命令结果可能短暂不一致。不构成正确性问题（两者都在原子 commit 边界内），但测试可能偶尔闪动。
- **第 4 波次 (s12)**：单任务 ✓

### 遗漏任务 / 粒度问题

1. **`src/web/CLAUDE.md` 的「QDS CSS Classes」章节未被任何子任务更新**（见 Major 发现 4）。s3 只是追加「标准化后的约束」新章节，没有改既有章节；s12 Step 5 也没有提到更新既有内容。执行完毕后，CLAUDE.md 会同时说 "bt-* / dc-* / Shared primitives 应被使用" 和 "禁区 class 清单：bt-* / dc-* / cg / ca / cr / ci / dim / mono"，自相矛盾。
2. **`src/web/src/components/*.tsx` 的扫描补漏（s11）没有显式列出 `Sidebar`/`StatusBar`/`FillTicker` 等是否真的存在 `bt-*`/`dc-*` 调用点**。实测 `rg -c 'bt-|dc-' src/web/src/components` = 0，`rg -c 'var\(--font-[ud]\)' src/web/src/components` = 0 — 这些文件已经合规。s11 可以增加"0 违规预期"的断言，避免 executor 产生意外修改。
3. **`s12` 没有包含对 `src/web/CLAUDE.md` 的「QDS CSS Classes」章节改写**（删除 `bt-*` / `dc-*` / Shared primitives 的列表，与禁区清单一致）。应追加 Step 8。
4. **`s2` smoke test 要求"初次运行必须报告 ≥ 300 处违规"** — 在 R4 修正前，违规数会因误报膨胀到 500+；修正后会降到某个未知值（可能 <300）。需要把 smoke test 的阈值改为"至少命中 R1/R2/R3/R4/R6/R7/R8 各 ≥ 1 次"，避免被绝对数字锁定。

## 权衡分析

| 决策 | 正方 | 反方 | 建议 |
|------|------|------|------|
| "单任务做 14 页 × 4 方向" | 避免反复 context 加载、commit history 集中；一次过的好处是明显的 | 工作量巨大（串行 30h / 并行 11h 是理想估算，未含 factor-research 全量）；如 s4（8h） 或 s6（4h→10h）失败会阻塞 s10-s12；跨组件 API 破坏难以 kickback | 保持单任务，但**允许 s4/s6 进一步拆分为 s4a/s4b/s4c + s6a/s6b**（按文件粒度），降低单子任务失败半径 |
| "bash + ripgrep 扫描 vs ESLint 自定义规则" | 零新依赖、集成快、grep 风格与 check-grep-fonts.sh 一致；CI 跑时间短 | 多行 JSX 正则（R6-R9）精度低、`\b` 边界坑已暴露（R4 灾难性误报）；长远维护需要熟悉正则的工程师 | 保持 ripgrep 方案但**强制**为 R4/R6-R9 编写自测用例（在脚本中以 `--selftest` 子命令执行一组正反例），否则误报会长期困扰 CI |
| "完全删除 globals.css 中 ~650 行遗留 class" | 单一技术路线、CSS 体积削减、agent 可靠生成 | 若 research 子系统的 `.sc/.cd/.sl/.fl/.fi/.fsel/.ctbl` 等 ~130 行仍被业务代码使用，则要么不能删（破坏），要么要追加迁移（工作量陡增） | **必须决策**：research 子系统整体迁移（+ ~6h 工作量）还是保留该子集（NFR-2 目标从"1340 行"放宽至"1470 行"）；planner 二选一 |
| "文件拆分（backtest 1754 → <700）" | 可维护性提升、单文件 blame 易读；符合用户"允许拆分"的指示 | 拆分会影响 `git log --follow` 链（虽然 `git mv` 保留 blame，但跨文件 rename 后查询变复杂）；React Context/Prop 传递路径会变长 | 保留拆分，但 §3.5.1 的子文件应**先暂存为不 export 的内部组件**于 page.tsx，等功能验证后再物理 `git mv` — 降低单 PR 破坏面 |
| "保留 `--font-u`/`--font-d` 别名" | ~97 个旧 `var(--font-d)` 引用可防御性继续工作；不改 chartTheme.ts 内部常量 | 业务代码若意外恢复使用内联 var() 引用，扫描规则能捕获；但 token 层的别名只是安全网，不影响合规 | 保留，现状合理 |
| "CHART_LABEL_STYLE 新增常量" | 与 CHART_LEGEND_STYLE 对称、为未来 `<Label>` 子组件使用预留 | 当前业务代码 `<Label\b` 命中 0，该常量**无真实消费场景**，是 dead code；R9 规则也无实际保护作用 | **要么**迁移 ReferenceLine.label 的两处占位对象使用该常量（并调整 R9 正则），**要么**删除该常量与 R9 规则 |

## 遗漏项

1. **Recharts 的 `<ReferenceLine label={...}>` 语法**在 RobustnessTab:353 与 RiskTab:187 存在内联对象，既不是 `<Label>` 也不是 `wrapperStyle` — 需要在 §3.3.5 / §3.2.3 R9 里明确对待方式。
2. **`src/web/src/components/qds/` 内 InlineError.tsx 的命名风格与其它文件不一致**（大驼峰 `InlineError.tsx` vs 中划线 `help-tip.tsx` / `section-label.tsx`）。非本任务范围，但 §3.8 R-9 StatusBadge 名称冲突的讨论可扩展到这一点；建议列为已知问题。
3. **`src/web/src/components/ui/button.tsx` 含 `bg-[var(--acc-d)]` 硬编码 arbitrary-value token**（实测 1 处）— shadcn 原语内部使用 `var()` 是合理的，但 R10 规则会报警。需在 §3.2 显式把 `src/web/src/components/ui/` 加入 R10 例外列表（或重写该 primitive）。
4. **字体 layout.tsx 横切**：s9 声称"layout.tsx 字体声明已正确，不改" — 确认无误（L13-25 正确使用 next/font/google，body 用 `bg-background text-foreground`）。但**如果 s6 要把 research Legend 的 `fontFamily: var(--font-d)` 迁移到 `CHART_LEGEND_STYLE` 常量，而该常量的 `fontFamily` 值仍是 `var(--font-d)`**，那么 `--font-d` 在 globals.css L18 的别名定义永远不能删除。NFR-1 / NFR-5 声称"别名保留"是对的，但需要在 CLAUDE.md 「标准化后的约束」章节显式 document 这一点（s3 章节草稿中应添加"`var(--font-u/d)` 禁止被业务代码直接消费，但保留别名供 chartTheme 等常量层间接使用"）。
5. **视觉回归工具缺失的 fallback 路径**：AC-2 "由 verify 阶段自动采集截图"是一个外部承诺。若 verify 阶段确实无工具，应**提前在 s12 的 Step 2 之后添加 Step 2.5：在 CLAUDE.md 「标准化后的约束」章节明示 AC-2 的退化后的替代 "preview 对照矩阵 + screenshot 手动 checklist"**（或写入 4-tasks.md），避免 verify 阶段发现缺口时被动。

## 上轮修改验证

本次为第 1 轮审查，无上轮审查对比。

## 修改要求（REVISE）

以下修改是 APPROVE 的前置条件，按优先级排序：

1. **[CRITICAL] 修正 R4 扫描规则（§3.2.3、s2 描述、s2 验收）**，避免对 `font-mono` / `bg-qds-*-dim` / `text-qds-*-dim` 误报：
   - 将 R4 正则改为 `(?<![-a-zA-Z0-9_])(cg|ca|cr|ci|dim|mono)(?![-a-zA-Z0-9_])` 或等价实现（例如先抓 `className="..."` 值再按空格切分 token 等值匹配）；
   - 在脚本 §3.2.4 多行匹配策略下追加 `--selftest` 子命令，输入一组已知正/反例，要求全部通过才 exit 0；
   - s2 验收把"初次运行报告 ≥ 300 处违规"改为"初次运行 R1/R2/R3/R4/R6/R7/R8 每条都至少命中 1 次，总违规数 ≥ 90（保守估计）"。

2. **[CRITICAL] 决策并显式化 factor-research 子系统的命运（§1.3 / §3.3 / s6 / s10 / NFR-2）**：
   - 选项 A（全迁移）：§1.3.2 `/research` 行追加"含 ~35 个 factor-research 全局 CSS 原语的迁移"；§3.3 新增 §3.3.6 (或 §3.3.7) "`sc/cd/sl/fl/fi/fsel/ctbl/dtab/hm-*/wf-*/hbar/acc-*/param-*/cfg-*/rpt-*/verdict/tip/badge/spinner/factor-dot/turn-*/data-avail/action-row` → Tailwind/shadcn 映射表"，至少 20 行映射；s6 工作量从 4h 上调到 10-12h；s10 删除范围从 650 行扩大到 ~780 行；NFR-2 `globals.css` 目标从 "1340 ± 40" 调整到 "1200 ± 50"。
   - 选项 B（不迁移）：§1.1 / §1.3 明确声明"factor-research 子系统（~130 行）本次保留，research/page.tsx 仅做字体/语义 class/Legend 三项"；s10 保留列表扩充；CLAUDE.md 「标准化后的约束」章节明确 research 子系统作为"遗留受控保留区"；NFR-2 放宽。
   - 必须二选一，不能保持现状"既要删又要保留 .sc/.cd/.fl/.fi/.fsel/.list/.empty 中的一部分"的矛盾。

3. **[MAJOR] 修正 `<Label>` 的虚构引用（§3.3.5、§1.1 违规统计、§3.9、s6 验收、§3.2.3 R9）**：
   - §1.1 违规表删除"`<Label style={…} />` 内联 4 处"一行，或替换为"`<ReferenceLine label={{…}} />` 内联 2 处（RobustnessTab:353、RiskTab:187）"；
   - §3.3.5 对应行改为 `ReferenceLine label` 迁移方式（需确认 `label` prop 接受 spread 的 CSSProperties + `value`）；
   - R9 正则改为 `<ReferenceLine\b[^>]*label\s*=\s*\{\s*\{[^}]*(fontSize|fill|fontFamily)` 并附 fix-hint；
   - 或者反过来删除 `CHART_LABEL_STYLE` 常量与 R9 规则，放弃该保护。

4. **[MAJOR] 删除 trading/page.tsx 的 `bt-* 4 处` 虚构引用（§3.9、s7）**：
   - §3.9 `trading/page.tsx` 行去掉"迁移 6 处 bt-*（4 处）"，改为"迁移 6 处 `fontFamily` 内联"；
   - s7 描述去掉 bt-* 迁移项，只保留 fontFamily / cg-ca-cr 单字母 / Tooltip/Grid spread 三项；
   - 工作量估算复查（3h 现在看似乎略高，可能 2-2.5h）。

5. **[MAJOR] 更新 `src/web/CLAUDE.md` 既有「QDS CSS Classes」章节作为 s12 Step 8（或新增 s13）**：
   - 该章节当前列出 `bt-list/bt-row/bt-status/bt-progress/bt-expand` 和 `dc-filter-*/dc-qrow-*/dc-dtbl/dc-type-*/dc-cov-*/dc-pager-*/dc-chip-*/dc-sl/dc-modal-icon` 以及 "Shared primitives `.btn/.btn-p/.btn-o/.btn-d`, `.sc/.sc-l/.sc-v`, `.fl/.fi/.fsel`, `.list`, `.empty`, `.dim/.cg/.cr/.ca`" — 这些必须全部删除/改写，与"标准化后的约束"章节保持单一叙事；
   - s12 Step 列表追加 Step 8：更新既有「QDS CSS Classes」章节，使其与本任务后的代码状态一致。

6. **[MAJOR] 修正 §1.1 违规统计的 `cg/ca/cr/ci/dim/mono` 数字与 data-catalog dc-* 数字**：
   - 使用修正后的 R4 正则重新扫描得出真实计数（排除 `font-mono` / `*-dim` 误报）；
   - data-catalog 的 dc-* 从 44 更正为 51（或实际重新扫描后的值），内联 fontFamily 从 5 更正为 6。

7. **[MAJOR] 修正 `.dc-*` 块起始行号（§1.1、§3.9、s10）**：
   - L1659 → L1640（实测 `.dc-sl` 起点）；对 s10 的"删除范围 L1659 起"改为 L1640 起（影响删除边界的精确性）。

8. **[MAJOR] R11 规则修正 + L1856 单行删除原子操作（§3.2.3 R11、s10）**：
   - R11 正则增加对 `.dim{...}.cg{...}` 跟随在 `}` 后面的情况的覆盖；
   - s10 明确要求"L1856 整行必须一次性删除，不允许留存 `.mono`/`.dim` 独立定义"。

9. **[MINOR] R10 规则例外增加 `src/web/src/components/ui/**`**（§3.2.3 R10）：
   - shadcn button.tsx 内部使用 `bg-[var(--acc-d)]` 是合理的（shadcn 原语自带 CSS 变量消费层）；
   - R10 glob 排除该目录。

10. **[MINOR] 追加 s12 Step 2.5 "AC-2 退化路径"**：
    - 若 verify 阶段无视觉回归工具，明确 fallback 为 "preview 对照矩阵（§3.4）+ CLAUDE.md 「标准化后的约束」章节"；
    - 避免 verify 阶段被动发现缺口。

ReviewPass: architect
VERDICT: REVISE
