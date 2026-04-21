# Architect Review — Round 3

**VERDICT: REVISE**

## 摘要

Round 2 的两个 CRITICAL（`--accent-*` 10 variant + types.ts）已彻底修复，R13/R14 selftest 覆盖周到，映射表扩至 10 行、TabNav 纳入 s7、types.ts 纳入 s5 且字典重写方案明确、`Round 2 Revision Notes` 开头对照表结构化便于复核、2-research.md L19 `docs/ui/` 残留清除、R7/R8/R9 两阶段扫描 + selftest 正反例充分、StatusBadge 视觉差异 fallback 决策权明示、s10 preflight 失败回退 target 映射已成表。然而本轮**新发现 1 个 CRITICAL 与 1 个 MAJOR**，均为"factor-research 散落范围仍有严重漏报"：

- **CRITICAL（新）**：s4 只列 "TradesTab 2 + PerformanceTab 1 + TearsheetTab 1 = 4 处 sc-l"，**实测 3 + 2 + 2 + 4（OverviewGreyTab）= 11 处 sc-l**（漏 7 处 + 漏整个 OverviewGreyTab.tsx 文件 4 处），且 **OverviewTab.tsx L190-206 有 5 处 `hm-grid/hm-label/hm-cell`** factor-research MonthlyHeatmap 原语**完全未列入散落清单**（涉及新建 `<MonthlyHeatmap>` 组件的前置工作）。
- **MAJOR（新）**：R14 PCRE2 正则对 `className="fg-primary"` / `className="sc-column"` 等包含 `\b(sc|fg|sl|cd|fi|fl)\b` 子词的假阳性会命中（`-` 不是 `\w`，触发 `\b`）— 当前仓库下 0 命中（已实测），但规则脆弱，未来扩展新 Tailwind/util class 名时会误伤。

另有 **4 个 MINOR**：s4 工作量 9h 仍偏低（漏统计 sc-l 7 处 + hm-* 5 处 + MonthlyHeatmap 组件新建）；R14 验收在 s4 声明"0 命中"但 s4 的散落清单漏项会导致 executor 误以为"4 处已清"却通不过 R14；波次 B 并行度 6 > 主 agent 常见派遣上限 5（4-tasks.md 未声明上限策略）；DAG 层面 s6 工作量从 10h 翻转为关键路径的稳定性在 Round 2 已用硬约束软着陆，但 hm-* 5 处新迁移工作若归入 s4（MonthlyHeatmap 组件化）实际工作量会溢出，应评估归入 s6（因 `<MonthlyHeatmap>` 本就是 research 子组件的新建目标）。

## 代码引用验证（现场重新采样 @ 2026-04-19 23:25）

| 引用 | 实测 | 状态 |
|------|------|------|
| `rg 'var\(--accent-[a-z0-9-]+\)' src/web/src -o` 总计 | **55 处跨 8 文件 / 10 variant**（green 23 / red 13 / amber 12 / blue 7 / orange 4 / red-20 2 / green-10 2 / purple 1 / amber-20 1 / blue-20 1 / purple-20 1 + TabNav.tsx 1）| ✅ 与 §1.1 完全一致 |
| `rg '\bbt-' src/web/src --glob='*.tsx'` 总计 | **280 处跨 7 文件**（backtest 276 + data-catalog/JobQueue 4 ）| ✅ |
| `rg '\bdc-' src/web/src --glob='*.{tsx,ts}'` 总计 | **65 处跨 6 文件**（含 types.ts 12 处 `TYPE_BADGE_CLS`）| ✅ |
| factor-research `sc-l` 散落（按 Tab 精确分布）| TradesTab **3**（L162 / L179 / L515）+ PerformanceTab **2**（L226 / L1726）+ TearsheetTab **2**（L48 / L90）+ **OverviewGreyTab 4**（L84 / L134 / L220 / L458）= **11 处** | ❌ §3.3.7 / s4 声明"4 处"严重漏报，且 **OverviewGreyTab 整个文件未列**（见 CRITICAL-1）|
| factor-research `hm-*` 散落 | **OverviewTab.tsx L190 / L192 / L195 / L200 / L206** 共 **5 处** `hm-grid` / `hm-label` / `hm-cell` | ❌ 散落清单完全未列（见 CRITICAL-1）|
| factor-research `rpt-*` tsx 调用 | 0 处（仅 globals.css 定义 L1957-1961）| ✅ §3.3.7.9 与 §3.5.5 正确 |
| `docs/ui/` 残留引用（tech-design / tasks / requirements / 2-research）| 0 处 | ✅ 全清 |
| R14 PCRE2 正则对伪造 `className="sc-column"` / `className="fg-primary"` | **命中**（`\b(sc|fg)\b` 前后 `-` 触发 word boundary）| ⚠️ 见 MAJOR-1 |
| R14 正则对 `className="bg-card"` / `className="font-sans"` | 不命中 | ✅ |
| R14 正则对 `className="sc"` / `className="sc-l"` / `className={`sc-v …`}` | 全部命中 | ✅ |
| `bt-status` 散落（`data-catalog/JobQueue` L173/176/181/185）| 4 处 | ✅ s5 预留到 s11 / s11 Step 11b 迁移清单已列 |
| s10 preflight 失败回退 target 映射（6 + 1 fallback 行）| 完整 | ✅ |
| `--accent-foreground` shadcn 内置豁免声明 | 在 §3.3.8 与 selftest 反例明列 | ✅ |
| `globals.css` 现状行数 | 1987（sanity check）| ✅ |
| parallel_groups `["s1","s2","s3"],["s4","s5","s6","s7","s8","s9"],["s10","s11"],["s12"]` | 拓扑无环 | ✅ 结构合法，但 wave B 6 并行 > 常规 5，见 MINOR-3 |
| task.json `review.round = 3` | 正确 | ✅ |

## 需求审查（1-requirements.md）

### Critical 发现

无（1-requirements.md §1.1 的统计表在 Round 2 已补齐 10 variant + 66 实例 factor-research 散落口径；§1.1 "factor-research 原语调用" 行列出 "TradesTab(2：L179/L515 sc-l) + PerformanceTab(1：L1726 sc-l) + TearsheetTab(1：L48 sc-l)"——但**此行的具体计数与实际不一致**，证据见 CRITICAL-1，修复时需同步 §1.1 行）。

### Major 发现

无（Round 2 遗留的 bt-* 253→280、2-research.md L19 残留均已修复）。

### Minor 发现

1. §1.3.1 表格 L91 "`/research`" 行标注 `factor-research className 实例 47 处` — 与 §3.3.7 "research/page 47 处" 一致，但若 OverviewTab 的 `hm-*` 5 处（逻辑上应在 s4 处理 / 或由 s6 提供 `<MonthlyHeatmap>` 后 s4 调用）未列入任何路由统计，合计数会偏差。影响很小（数字美观问题），修复时可将 hm-* 的 5 处并入"backtest/components/OverviewTab"散落行即可。

## 技术设计审查（3-tech-design.md）

### Critical 发现

1. **factor-research 散落清单在 backtest/ 下严重漏报（§3.3.7 Round 2 散落清单 + §3.9 影响文件表 + s4 描述 + AC/R14 验收冲突）**

   - **证据**（现场实测 @ 2026-04-19 23:20）：
     - `rg --pcre2 'className\s*=\s*[\"'\''{`][^\"'\''}`]*\bsc-l\b' src/web/src/app/backtest --glob='*.tsx'` 命中：
       - `TradesTab.tsx`: L162 / L179 / L515 = **3 处**（§3.3.7 表声明 2 处，漏 L162）
       - `PerformanceTab.tsx`: L226 / L1726 = **2 处**（§3.3.7 表声明 1 处，漏 L226）
       - `TearsheetTab.tsx`: L48 / L90 = **2 处**（§3.3.7 表声明 1 处，漏 L90）
       - **`OverviewGreyTab.tsx`: L84 / L134 / L220 / L458 = 4 处（整个文件未列入 Round 2 散落清单）**
     - `rg --pcre2 'className\s*=\s*[\"'\''{`][^\"'\''}`]*\b(hm-grid|hm-label|hm-cell)\b' src/web/src/app/backtest --glob='*.tsx'` 命中：
       - **`OverviewTab.tsx`: L190 / L192 / L195 / L200 / L206 = 5 处**（`hm-grid` 1 + `hm-label` 3 + `hm-cell` 1，MonthlyHeatmap 因子研究原语完全未列入任何散落清单）
     - `sc-l` 实际总散落 = 3 + 2 + 2 + 4 = **11 处**（§3.3.7 / s4 均声明 4 处，漏报 7 处 + 漏整个文件 OverviewGreyTab）
     - `hm-*` 实际散落 = **5 处在 OverviewTab.tsx**（§3.3.7.9 有 `.hm-grid/.hm-label/.hm-cell` 对应 "专用组件 `<MonthlyHeatmap>`（新建）" 映射，但仅面向 research/page；未声明 backtest/OverviewTab 也消费 hm-* 需一并迁移；`<MonthlyHeatmap>` 的新建归属不明）
   - **影响**（三重 cascade）：
     1. **R14 验收冲突**：s4 验收明确写 "**R14** 规则下 backtest 下 0 命中"（4-tasks.md L168），executor 如果只迁移 §3.3.7 声明的 4 处 sc-l 而不动 OverviewGreyTab 的 4 处 + TradesTab L162 + PerformanceTab L226 + TearsheetTab L90 + OverviewTab 5 处 hm-*，R14 扫描必然命中 12+ 处违规 — `s4 exit 1`，executor 不知道是新发现还是遗漏；若按 s10 preflight 回退 target 映射则回到 s4 补做，形成不必要的 rework 循环。
     2. **`<MonthlyHeatmap>` 组件归属不明**：§3.3.7.9 / §3.5.4 均把 `<MonthlyHeatmap>` 作为 research/ResearchChartPanel 的**新建**子组件（在 s6 工作量内）；但 backtest/OverviewTab.tsx 也消费 `.hm-grid/.hm-label/.hm-cell`（L190-206 是月度收益热力图）。依赖关系：s4 要迁移 OverviewTab 的 hm-* 5 处 → 需要先有 `<MonthlyHeatmap>` → 该组件在 s6 才创建。这制造了 **s4 → s6 的隐式依赖**，但 task.json 中 s4 / s6 都只依赖 [s1, s2]，**没有** s4 依赖 s6。并行执行时 s4 会先失败或等待。
     3. **OverviewGreyTab 拆分决策耦合**：§3.5.3 声明 "OverviewGreyTab 若与 OverviewTab 职责重叠 ≥ 70% 则合并"；如果合并，4 处 sc-l 会自动随合并迁移；但 OverviewGreyTab 独立的 4 处 sc-l 需要先识别再决策，目前 s4 描述既不识别数量也不声明归属。
   - **修复（必须）**：
     1. **§3.3.7 Round 2 散落清单表**（tech-design L551-558）修正：
        - `TradesTab.tsx`: 2 → **3**（含 L162）
        - `PerformanceTab.tsx`: 1 → **2**（含 L226）
        - `TearsheetTab.tsx`: 1 → **2**（含 L90）
        - **追加新行**：`OverviewGreyTab.tsx`: 4 处 sc-l（L84/L134/L220/L458）
        - **追加新行**：`OverviewTab.tsx`: 5 处 hm-*（L190/L192/L195/L200/L206）
        - 合计行："66 实例 / 6 文件" → **"66 + 11 sc-l增补 + 5 hm-* = 82 实例 / 8 文件"**（或按实际重新统计后修订）
     2. **§3.3.7.9 `.hm-grid/.hm-label/.hm-cell` 行**（tech-design L655）加备注："**backtest/OverviewTab.tsx L190-206 亦消费此原语 5 处**（月度收益热力图），在 s4 中 **optionally** 沿用 `<MonthlyHeatmap>`（由 s6 先创建），或 s4 改为 Tailwind grid + CSS custom props 内联实现（独立，不依赖 s6）"——要求 planner 做**显式决策**避免隐式依赖。
     3. **s4 描述（4-tasks.md L154-158）** 追加：
        - TradesTab sc-l 2 → 3 处
        - PerformanceTab sc-l 1 → 2 处
        - TearsheetTab sc-l 1 → 2 处
        - **追加 OverviewGreyTab 4 处 sc-l 迁移**
        - **追加 OverviewTab 5 处 hm-* 迁移决策**（按上述 §3.3.7.9 决策二选一）
        - 合计从 "4 处 sc-l" 改为 **"11 处 sc-l + 5 处 hm-* = 16 处 factor-research 散落"**
     4. **s4 工作量**从 9h → **10h**（或拆出 s4b 处理 backtest Tab 的散落 + hm-* 组件化约 2h，见 MINOR-4）
     5. **s4 验收 Round 2 新增行**（4-tasks.md L174）从 `rg -n '\bsc-l\b' src/web/src/app/backtest/components` 命中 0 改为 **`rg -n '\b(sc-l|hm-grid|hm-label|hm-cell)\b' src/web/src/app/backtest --glob='*.tsx'` 命中 0**
     6. **s4 dependencies**：如果决定沿用 `<MonthlyHeatmap>`（s6 提供），**必须**在 task.json 中加 `s6` 到 s4 的 `depends_on`；如果决定 s4 自己内联 Tailwind 实现，维持现状 `[s1, s2]`。planner 必须给出决策。
     7. **§1.1 违规表 L27 `factor-research 原语调用` 行**（1-requirements.md）同步更新散落分布。

### Major 发现

1. **R14 PCRE2 正则存在假阳性风险（§3.2.3 R14 + s2 + selftest）**

   - **证据**：
     - R14 正则：``className\s*=\s*[\"'{][^\"'}]*\b(sc|cd|sl|fl|fi|fsel|ctbl|dtab|cd-h|cd-b|sc-l|sc-v|sc-sub|turn-(…)|verdict(?:-…)?|factor-dot|factor-limit|data-avail|action-row|frow|fg|hbar(?:-…)?|…|spinner)\b``
     - PCRE2 `\b` 是 word boundary（`\w` ↔ `\W`）。`-` 不是 `\w`，所以 `\b(sc)\b` 在 `sc-column` 的 `c|-` 处也算 `\b`，**命中**；`\b(fg)\b` 在 `fg-primary` 同理命中。
     - 伪造测试（/tmp/r14-test.tsx @ 23:15）：
       - 正例全中 ✅（9/9）
       - 反例 `className="bg-card"` / `className="font-sans"` 不中 ✅
       - **假阳性**：`className="sc-column"` / `className="fg-primary"` **命中** ❌
     - 实测 `rg` 当前仓库：`rg --pcre2 '\b(fg-|sc-column|sl-|fi-|fl-)...' src/web/src --glob='*.tsx'` 命中 0 行（当前 codebase 下 0 假阳性）
   - **影响**：
     - 当前仓库下 0 命中，实质无阻塞；但**未来扩展**（例如引入 Tailwind util 扩展 `fg-primary` / `sc-column` / Radix utility-class `fi-*` / 自定义 class `sl-*`）会被 R14 误伤。
     - §3.2.8 `--selftest` 反例明确写了 `className="sc-column"` 必须**不命中**（tech-design L350）— **若 selftest 脚本真实实现此断言，selftest 会失败**，因为实测 R14 正则命中 `sc-column`。这是 selftest 内部矛盾：文档声明反例应不命中，但正则实测命中。
   - **修复**：二选一：
     - **方案 A（推荐）**：R14 正则改为前后向断言（同 R4 风格）：``(?<![-a-zA-Z0-9_])(sc|cd|sl|fl|fi|fsel|...)(?![-a-zA-Z0-9_])``，确保 `sc` 只在它独立成 class token 时命中，在 `sc-column` / `fg-primary` 等复合名中不命中；同时对复合 factor-research 原语（`sc-l` / `sc-v` / `sc-sub` / `cd-h` / `cd-b` / `turn-row` 等）单独列为白名单避免被父名过滤掉。
     - **方案 B**：保留正则，但**删除** `className="sc-column"` / `className="fg-primary"` 反例（或修订 selftest 反例为"R14 不覆盖子串类名，仅保证 className token 级匹配的已知限制"），作为 R4 一致的已知限制声明。
   - **修复涉及文件**：
     - §3.2.3 R14 正则行（tech-design L176）
     - §3.2.8 selftest 反例（tech-design L348-350）
     - s2 描述 R14 正则行（4-tasks.md L80）

### Minor 发现

1. **OverviewTab hm-* 新迁移的 `<MonthlyHeatmap>` 组件归属冲突**（已在 CRITICAL-1 的 impact #2 中覆盖；此处单列为 Minor 提醒：决策二选一要写入 §3.3.7.9 行内，不能只在本 review 里决议）。
2. **§3.3.7 "66 实例 / 6 文件" 口径**（tech-design L559）与 critic r2 "44 处逻辑位置" 的口径切换说明已有，但加入 CRITICAL-1 修复后，实例数应改为"82 实例 / 8 文件"；"66 / 44" 双口径解释需同步更新为"82 / ~50"。
3. **StatusBadge 视觉差异 fallback 决策权**（§3.3.9 Round 2 补充）明确交给 s11 executor，但 fallback 行为**没有时间开销估算**；若需要 fallback（barrel 目测失败），s11 工作量从 3h → ~4h；建议 §3.3.9 增加一句"fallback 触发时 s11 工作量上调 0.5-1h"。
4. **s4 工作量与单 executor 工作量上限的矛盾**（波次 B s4 = 9h 已是当前极限，若加入 CRITICAL-1 的散落补齐到 10-11h，且 wave B 共 6 任务 > 5 agent 常规并发上限）：建议在 §"子任务拆分风险" 考虑 s4 也可拆为 **s4a（page + 主 Tab 迁移 ~7h）+ s4b（backtest Tab 散落清理 + OverviewGreyTab 合并/独立 + hm-* ~3h）**，与 s6a/s6b 的软约束对称；或明确 wave B 并发策略（主 agent 按 parallel_groups 顺序派遣，允许 6 个；或分两批派遣：[s4, s5, s6, s7, s8] 先，s9 最后；planner 表态）。

## DAG 审查（4-tasks.md + task.json）

### 合法性

- 拓扑无环 ✓；parallel_groups 4 个波次，结构清晰 ✓。
- s12 依赖 [s10, s11] 合理；s10 / s11 依赖 s4-s9 全体合理 ✓。
- task.json `review.round = 3` 正确 ✓。

### 并行性

- 波次 B 6 并行（s4-s9）：**潜在超过常规 5-agent 上限**。4-tasks.md 未声明主 agent 派遣策略 — 若实际派遣工具限制并发 5，则 s9 将等到 s4-s8 中某一完成才能启动，关键路径 = max(s4, ..., s8) + s9 的等待（微小）；若工具支持 6 并行则无影响。
- 建议：4-tasks.md §"并行分组" 加一句"**若 agent 工具并发上限为 5，则 s9（2.5h）排队到 s4-s8 任一完成后开启；对关键路径无实质影响**"（因为 s9 = 2.5h < s6 = 10h）。不致命，列 MINOR。

### 遗漏任务 / 粒度问题

1. **CRITICAL-1 的散落漏项**（s4 漏 11-4=7 处 sc-l + 5 处 hm-*）。
2. **`<MonthlyHeatmap>` 依赖关系**（s4 消费 + s6 生产，无显式依赖边或独立实现决策）。
3. **s4 → s4a + s4b 拆分建议（MINOR-4）**。

### 稳定性

- s6 (10h) + 硬约束（4h 未完成 3 子组件 → 拆 s6b）架构合理 ✓。
- preflight 失败回退 target 映射可执行（6 路由 + 1 fallback 行）✓。
- preflight 在 s10 第一步强制，阻止未迁移即删 CSS ✓。

## 权衡分析

| 决策 | 正方 | 反方 | 建议 |
|------|------|------|------|
| OverviewTab hm-* 5 处：沿用 s6 的 `<MonthlyHeatmap>` vs 在 s4 内联 Tailwind grid 实现 | 沿用：组件复用、一致性、未来可维护性高 / 内联：s4 无隐式依赖 s6、wave B 内 6 任务独立 | 沿用：制造 s4 → s6 边，需改 task.json（s4 dependencies 追加 s6 或将 s6 提到 wave A）；内联：两套 MonthlyHeatmap 实现（s4 版 + s6 版），后续谁负责维护不清 | **推荐沿用**：在 §3.5.4 `<MonthlyHeatmap>` 子组件定义中声明"由 s6 创建，s4 直接导入 `@/app/research/components/MonthlyHeatmap`"；task.json 中 s4 depends_on 追加 `s6`，但这破坏 wave B 并行 → **更优的折中**：将 `<MonthlyHeatmap>` 组件创建作为 **s6a** 的初始步骤（~1h），s6 关键路径不变；或将 `<MonthlyHeatmap>` 提前到 wave A 新增 s0.5 任务（过度设计）。executor 决策权交予 s4 planner：若拆 s4b 则 s4b 等 s6a 产出 MonthlyHeatmap.tsx；若 s4 单体则 s4 结尾运行 MonthlyHeatmap 接口对齐（executor 判断） |
| s4 保持单体 9-11h vs 拆为 s4a + s4b | 单体：DAG 简洁，执行者一次加载上下文；拆分：单点失败半径小 | 单体：单执行者 10h+ 超工期；拆分：多一条 DAG 边，需定义文件集边界（page/主 Tab vs backtest/components 散落） | 保持单体，但在 §"子任务拆分风险" 加入 s4 动态拆分门槛（类比 s6 硬约束）："s4 启动 4h 后若 page + 主 Tab 未完成，拆出 s4b 处理 backtest Tab 散落（独立）" |
| R14 正则前后向断言 vs 保留 `\b` + 接受已知限制 | 前后向断言：零假阳性、与 R4 风格统一；`\b`：简单、当前 0 假阳性 | 前后向断言：正则复杂、PCRE2 lookbehind 必须 rg --pcre2；`\b`：未来扩展有假阳性风险、selftest 内部矛盾 | **选前后向断言**（R4 已是 PCRE2，R14 同风格统一）；修复 selftest `sc-column` 反例断言失败 |
| OverviewGreyTab 合并到 OverviewTab vs 独立 | 合并：减少文件数、4 处 sc-l 随父组件迁移 / 独立：保留业务区分（灰色 Overview 可能是"空态/无数据"视图） | 合并：需审视 UX 语义（grey 模式可能是特殊主题）；独立：4 处 sc-l + 随 hm-* 需单独处理 | 保留 §3.5.3 现有"合并阈值 70%" 判断，但**显式列出 OverviewGreyTab 的 4 处 sc-l 散落分布**，否则 executor 在判断合并前就已遗漏散落清理 |

## 遗漏项

1. **CRITICAL**：§3.3.7 Round 2 散落清单漏 TradesTab L162 / PerformanceTab L226 / TearsheetTab L90 / OverviewGreyTab 4 处 sc-l / OverviewTab 5 处 hm-*（总计漏 12 处 / 漏 2 文件）。
2. **CRITICAL**：`<MonthlyHeatmap>` 组件归属冲突 — s4 消费 vs s6 生产，无显式依赖边或独立实现决策。
3. **MAJOR**：R14 PCRE2 正则 `\b` 对复合类名（`sc-column` / `fg-primary`）假阳性；selftest 反例声明与实际正则实测矛盾。
4. **MINOR**：wave B 6 并行 > 常规 5 agent 上限，派遣策略未声明。
5. **MINOR**：s4 工作量 9h 在 CRITICAL-1 修复后实际上应 ~10-11h，未预留边距。
6. **MINOR**：§1.3.1 L91 `/research` factor-research 统计未含 OverviewTab 的 hm-* 5 处（按域散落应记入 backtest/行）。
7. **MINOR**：§3.3.9 StatusBadge fallback 触发时 s11 工作量未估算边距（3h → ~4h）。

## 上轮修改验证

| 上轮（r2）要求 | 是否解决 | 说明 |
|---------|---------|------|
| A-CR-1 `--accent-*` 10 variant + TabNav | ✅ 完全解决 | §1.1 55 处 / 10 variant / 8 文件；§3.3.8 映射表 10 行；R13 正则覆盖 6 色；selftest 正例覆盖全部 10 variant + 反例 `--accent-foreground` 豁免；s7 工作量上调 3.5h；s9 EditorClient 15 处独立列出；TabNav.tsx 新列文件 |
| A/C-CR-1 types.ts 漏入 s5 | ✅ 完全解决 | §1.1 dc-* "65 处跨 6 文件"含 types.ts 12；§3.3.4.1 "dc-type-* 字典常量迁移策略"小节含 before/after 代码块；§3.9 补 types.ts 行；s5 文件列表含 types.ts + 工作量上调 4.5h；s5 验收 `rg 'dc-type-[a-z]+'` = 0 |
| A-MA-1 bt-* 253 → 276+4 | ✅ 完全解决 | §1.1 "280 处跨 7 文件"；s4 描述分布精确；含 JobQueue 4 处 bt-status 预留 |
| A-MA-2 2-research.md L19 残留 | ✅ 完全解决 | 2-research.md L19 已改为 `.claude/skills/TinoHelmDS/Web UI Kit.html + Charts Spec.html`；L28 Round 2 修正说明已追加 |
| C-CR-1 factor-research 散落 44 处 5 文件 | ⚠️ **部分解决** | §3.3.7 散落清单有表格 + s4 新增描述；但**计数漏报**（TradesTab 2→3、PerformanceTab 1→2、TearsheetTab 1→2、**OverviewGreyTab 4 未列**、**OverviewTab hm-* 5 未列**）— 见本轮 CRITICAL-1 |
| C-M2 R7/R8 selftest 缺失 + R8 spread-extra-prop | ✅ 完全解决 | §3.2.8 selftest R7 4 行正反例、R8 4 行正反例、R6 多行正例；R8 PCRE2 豁免 `{CHART_LEGEND_STYLE}` 与 `{{ ...CHART_LEGEND_STYLE, ... }}` |
| C-M3 R9 `-U --multiline-dotall` | ✅ 完全解决 | §3.2.3 R9 声明"必须 -U --multiline-dotall"；§3.2.4 R9 两阶段伪代码；§3.2.8 selftest 含多行正例 |
| A-MINOR factor-research 85 vs 98 计数 | ✅ 完全解决 | §3.3.7 开头"98 unique selector / 顶层约 85 个"；脚注 A 验证命令 |
| A-MINOR s10 preflight 失败回退 target | ✅ 完全解决 | s10 描述新增"preflight 失败回退 target 映射" 6 行 + 1 fallback 行；每次回退后执行 --fix-hint |
| A-MINOR s6 动态拆分门槛 | ✅ 完全解决 | §"子任务拆分风险" 改为硬约束"4h 后若未完成 3 子组件必须拆 s6b" |
| C-m1 §1.9 "经用户明确授权" 缺原文 | ✅ 完全解决 | 降语为"interview.md 第 4 轮选择隐含此方向；本任务完成后主 agent 向用户确认并更新 memory" |
| C-m2 §3.3.7.4 `.btn-p` 决策 | ✅ 完全解决 | 锁定为 `variant="default"` accent 橙单一决策 |
| C-m3 `.rpt-*` 调用点 0 / ReportHeader | ✅ 完全解决 | §3.3.7.9 注"调用点 0"；§3.5.5 移除 ReportHeader |
| C-m4 CHART_LABEL_STYLE fontFamily 差异 | ✅ 完全解决 | §3.3.6 删除 fontFamily + fontSize 统一 10；s1 验收新增"不包含 fontFamily 键" |
| 缺失 5 `--popover` vs `--bg-p` | ✅ 完全解决 | §3.3.5 补加 L81/L153 核对结果 |
| 缺失 6 StatusBadge 视觉差异 | ✅ 完全解决 | §3.3.9 补视觉差异声明 + fallback 决策权 |

**综合**：16 个修改要求中 **15 个完全解决**、**1 个部分解决**（C-CR-1 factor-research 散落在 backtest Tab 的具体分布仍有漏报）。

## 修改要求（REVISE）

按优先级排序：

1. **[CRITICAL] 补齐 backtest/ 下 factor-research 散落完整清单（tech-design §3.3.7 / §3.3.7.9 / §3.9 / s4 描述与验收 / 1-requirements §1.1）**
   - §3.3.7 Round 2 散落清单表：
     - `TradesTab.tsx`: 2 → **3**（L162/L179/L515）
     - `PerformanceTab.tsx`: 1 → **2**（L226/L1726）
     - `TearsheetTab.tsx`: 1 → **2**（L48/L90）
     - 追加 `OverviewGreyTab.tsx`: 4 处 sc-l（L84/L134/L220/L458）
     - 追加 `OverviewTab.tsx`: 5 处 hm-*（L190/L192/L195/L200/L206）
     - 合计由 "66 实例 / 6 文件" → **82 实例 / 8 文件**（或按实测重统计）
   - §3.3.7.9 `.hm-*` 行追加："backtest/OverviewTab.tsx L190-206 亦消费 5 处；s4 迁移方案决策（二选一）"：
     - 方案 A：复用 s6 创建的 `<MonthlyHeatmap>` 组件 → task.json s4 depends_on 追加 s6（破坏 wave B 并行）或 s6 创建 MonthlyHeatmap.tsx 作为 s6a 首要步骤并作为 shared util 写入 `src/web/src/components/`（见权衡分析）
     - 方案 B：s4 内联 Tailwind grid + CSS custom props 实现，不依赖 s6；接受 MonthlyHeatmap 有两套实现的后续整合代价
     - planner 做决策写入 §3.3.7.9
   - §3.9 影响文件表 backtest/components/*.tsx 行补"Round 3 新增：OverviewGreyTab 4 处 sc-l + OverviewTab 5 处 hm-*"
   - s4 描述（4-tasks.md L154-158）：从 "共 4 处 .sc-l 迁移" 改为 "**共 11 处 .sc-l + 5 处 .hm-* = 16 处 factor-research 散落**"，逐行列分布
   - s4 验收 Round 2 新增行：`rg -n '\bsc-l\b' …components` → **`rg -n '\b(sc-l|hm-grid|hm-label|hm-cell)\b' src/web/src/app/backtest --glob='*.tsx'` 命中 0**
   - s4 工作量 9h → **10h**（或按 MINOR-4 拆 s4a/s4b）
   - 1-requirements §1.1 L27 factor-research 原语调用行同步精确分布

2. **[MAJOR] 修正 R14 PCRE2 正则的 word-boundary 假阳性 + selftest 内部矛盾（tech-design §3.2.3 R14 / §3.2.8 / s2）**
   - 推荐方案：R14 改为前后向断言（与 R4 风格对齐）：
     ```
     className\s*=\s*[\"'{`][^\"'}`]*(?<![-a-zA-Z0-9_])(sc|cd|sl|fl|fi|fsel|ctbl|dtab|cd-h|cd-b|sc-l|sc-v|sc-sub|turn-(row|item|label|val)|verdict(?:-pass|-warn|-fail)?|factor-dot|factor-limit|data-avail|action-row|frow|fg|hbar(?:-label|-wrap|-fill|-val)?|explorer|config-panel|result-panel|acc-(group|head|body|item)|param-(section|row|label|val|input|unit|select|divider)|cfg-(section|title)|hm-(grid|label|cell)|wf-(row|label|bar-wrap|bar|val)|rpt-(head|back|title|sub|meta|meta-item)|report-content|tab-bar|hist-(clickable|pager)|empty-(icon|title|desc)|spinner)(?![-a-zA-Z0-9_])[^\"'}`]*[\"'{`]
     ```
   - §3.2.8 selftest 追加多个新反例：
     ```
     assert_no_match R14 'className="sc-column"'
     assert_no_match R14 'className="fg-primary"'
     assert_no_match R14 'className="fi-rocket"'
     assert_no_match R14 'className="cd-hover"'
     assert_no_match R14 'className="sl-indicator"'
     ```
   - 4-tasks.md s2 R14 正则行同步更新
   - 可选次优：保留 `\b`，但把 selftest 反例 `sc-column` 改写为已知限制声明（类似 R4 的 cn() 包裹限制），并注明"R14 不覆盖子串类名；若出现需专项扫描"。次优更改成本低但规则脆弱性保留。

3. **[MINOR] `<MonthlyHeatmap>` 组件依赖关系显式化（tech-design §3.3.7.9 / §3.5.4 / task.json）**
   - §3.5.4 把 MonthlyHeatmap 从 ResearchChartPanel 子组件的"私有"角色改为"共享" — 路径建议 `src/web/src/components/charts/MonthlyHeatmap.tsx`（非 research 命名空间内私有）
   - s6 工作量保持 10h；s6 初始 step 明确为"创建 shared MonthlyHeatmap component"（若 CRITICAL-1 选方案 A）
   - 如选方案 B（s4 独立实现），§3.3.7.9 明确声明"s6 与 s4 各自实现 MonthlyHeatmap，后续如需整合见 s12 或后续任务"

4. **[MINOR] s4 动态拆分门槛（4-tasks.md §"子任务拆分风险"）**
   - 追加："s4 启动 4h 后若 page.tsx + OverviewTab + PerformanceTab 未完成，拆出 s4b 处理 backtest/components 散落（sc-l 11 + hm-* 5 + TradesTab/TearsheetTab/OverviewGreyTab 文件拆分），s4a 继续 page.tsx + PerformanceTab 拆分。DAG 上 s4a/s4b 与 s5-s9 同波次，拆分由 executor 在 execute.jsonl 记录"

5. **[MINOR] wave B 并行度声明（4-tasks.md §"并行分组"）**
   - 追加："若主 agent 派遣工具并发上限为 5，则 s9（2.5h）排队在 s4-s8 之后启动；因 s9 < s6 关键路径，无实质影响。若上限为 6 则全部并发"

6. **[MINOR] §3.3.9 StatusBadge fallback 工作量边距**
   - 追加："fallback 触发（barrel re-export 视觉目测不通过）时 s11 工作量上调 0.5-1h（~3.5-4h）"

---

ReviewPass: architect
VERDICT: REVISE
