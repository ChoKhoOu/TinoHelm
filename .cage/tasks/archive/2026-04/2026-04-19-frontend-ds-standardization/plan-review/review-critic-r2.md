# Critic Review — Round 2

**VERDICT: REVISE**

## 总体评估

第 2 轮修订**结构性大幅改善**：§Round 1 Revision Notes 表逐条兑现，C1/C2/M1-M6 的修复方向准确且有文档落点（PCRE2 断言 + selftest、R12/R13 新增、factor-research 选项 A 全迁移、StatusBadge 扩展决策、未定义变量映射、shadcn 豁免、历史 memory 作废声明）。字数管控和精确计数（28 fontFamily / 90 fontSize / 35 `--accent-*`）与实际扫描一致。

然而在阶段 2（验证）与阶段 5（缺口分析）中发现 **1 个 CRITICAL + 3 个 MAJOR + 4 个 MINOR** 新问题，其中最严重的是 **factor-research 子系统的实际散落范围比 §3.9 记录的更广（44 处跨 5 文件，而非 "research/page 62 + ReportClient 若干"）**，这会使 s4（backtest）和 s5（data-catalog）的验收扫描失败。其次是 **data-catalog/JobQueue.tsx 有 4 处 `bt-status` 未计入 bt-* 范围**（s5 描述明言 "data-catalog 也无 bt-*"，事实不符）。

## 预判 vs 实际

- **预判 1**：r1 C1/C2 / M1-M6 落地到位 → **命中**：Round 1 Revision Notes 表逐条核查基本兑现。selftest / preflight / R12 / R13 / §3.3.7 / §3.3.8 / §3.3.9 / §1.9 均有对应章节。
- **预判 2**：新 AC（R1-R13）每条都可测 → **部分命中**：R12 / R13 / selftest 正反例设计完备；但 R6/R7/R8/R9 的"两阶段 rg + grep"实现方式仅在 §3.2.4 示意，selftest 样例覆盖 R6/R9 但**未覆盖 R7/R8** — 这两条规则的实际扫描精度未被自测验证。
- **预判 3**：新增缺口 → **严重命中**（见 C1、M1、M2）：factor-research class 散落到 backtest/data-catalog 未被识别；JobQueue 4 处 bt-status 与 s5 "无 bt-*" 声明冲突；backtest/page.tsx 实测 bt-* 144 处（而非 127 处）。
- **预判 4**：歧义扫描残留 → **轻度命中**：§3.3.7 的 9 个子小节基本可执行，但 `.btn-p` 迁移建议的括注（"如语义确为成功动作则用自定义 className bg-qds-success text-white"）会让 executor 陷入决策负担；`.rpt-*` → `<PageHeader>` 映射但 ReportClient 实测无 `.rpt-*` class 使用，常量消费不匹配。
- **预判 5**：memory 冲突处理 → **部分命中**：§1.9 声明"用户明确授权"，但**interview.md 原文未提及 memory feedback**，此授权属于推断；建议降级为"本规划默认覆盖，执行完成后由主 agent 向用户确认并更新"。

## Critical 发现（阻塞执行）

### C1 · factor-research 子系统实际散落范围超过 §3.9 记录，s4/s5 扫描会失败

- **证据**（精确扫描 @ 2026-04-19，`rg -n 'className=\{?"(sc|cd|sl|cd-h|cd-b|sc-l|sc-v|sc-sub|fsel|ctbl|dtab|turn-row|turn-item|turn-label|turn-val|verdict|factor-dot|factor-limit|data-avail|action-row|frow|fg|hbar|explorer|config-panel|result-panel)"\s*[}>]'`）：
  | 文件 | 命中 | 示例 |
  |---|---:|---|
  | `src/web/src/app/research/page.tsx` | 36 | `.fl` / `.fi` / `.fsel` / `.sc` / ... |
  | `src/web/src/app/data-catalog/page.tsx` | 4 | L240-243 `<div className="sc"><div className="sc-l">数据集</div><div className="sc-v">...</div></div>` 等 |
  | `src/web/src/app/backtest/components/TradesTab.tsx` | 2 | L179、L515 `<span className="sc-l">` |
  | `src/web/src/app/backtest/components/PerformanceTab.tsx` | 1 | L1726 `<span className="sc-l">` |
  | `src/web/src/app/backtest/components/TearsheetTab.tsx` | 1 | L48 `<span className="sc-l">` |
  | **合计** | **44 处 / 5 文件** | — |
- **与规划的冲突**：
  - `3-tech-design.md §3.9` 影响文件清单中 factor-research 子系统调用**仅**列在 research/page.tsx 与 ReportClient 下。
  - `4-tasks.md s4`（backtest）任务描述未提及任何 `.sc-l` 迁移。
  - `4-tasks.md s5`（data-catalog）任务描述未提及 `.sc/.sc-l/.sc-v/.sc-sub` 在 `page.tsx:240-243` 的 4 处迁移。
  - `1-requirements.md §1.1` 表格写 "factor-research 原语调用 ~130+ 处跨 3 文件（主要在 research/page 62 处 + ReportClient 若干 + 个别散落）"，其中 "个别散落" 的模糊表述没有被具体化；实际跨度为 5 文件。
- **影响（现实最坏情况）**：
  1. s4 执行者按描述迁移 backtest 下 bt-* / fontFamily / fontSize / Recharts spread，但**不会**触达 TradesTab:179 / PerformanceTab:1726 / TearsheetTab:48 的 `.sc-l` class。s4 验收脚本扫描 `rg 'src/app/backtest/'` 时 R11 不会报（R11 只扫 globals.css），但是 s10 删除 `.sc-l` 定义后，这 4 处 `<span className="sc-l">` 的渲染会失去 `var(--acc)` 上色 + `.52rem` 字号 — 表现为视觉退化（文字变成默认色、默认字号）。
  2. 同理 s5 data-catalog/page.tsx:240-243 的 4 处 KPI 行会在 s10 删 CSS 后变成无样式的纯文字。
  3. s10 的 `--preflight-before-css-delete` 当前设计**仅扫描业务代码规则 R1-R10+R12+R13**，不专门扫 factor-research class 使用情况（只有 R4 覆盖 cg/ca/cr/ci/dim/mono 六个独立 token）。preflight 不会拦截这个缺口。
- **置信度**：HIGH（5 文件 44 处 shell 直接验证）
- **修复**（必改）：
  1. §3.9 影响文件清单新增/补充：
     - `backtest/components/TradesTab.tsx` — `.sc-l` 2 处需迁移为 `<SectionLabel>` 或 `text-qds-t2 text-[0.52rem] uppercase tracking-widest`；
     - `backtest/components/PerformanceTab.tsx` — `.sc-l` 1 处同上；
     - `backtest/components/TearsheetTab.tsx` — `.sc-l` 1 处同上；
     - `data-catalog/page.tsx:240-243` — `.sc/.sc-l/.sc-v/.sc-sub` 4 处迁移为 `<StatCard>` QDS 组件（4 个 KPI）。
  2. `4-tasks.md s4` 描述追加 "PerformanceTab/TradesTab/TearsheetTab 共 4 处 `.sc-l` → `<SectionLabel>` / Tailwind"。
  3. `4-tasks.md s5` 描述追加 "page.tsx:240-243 4 处 `.sc/.sc-l/.sc-v/.sc-sub` → 4 张 `<StatCard>`"。
  4. 新增 R14 规则（或扩展 R11 到 .tsx 扫描）：`className\s*=\s*[\"'\{][^\"'\}]*\b(sc|sc-l|sc-v|sc-sub|cd|cd-h|cd-b|sl|fl|fi|fsel|ctbl|dtab|turn-[a-z]+|verdict[a-z-]*|factor-dot|factor-limit|data-avail|action-row|hbar[a-z-]*|hm-[a-z]+|wf-[a-z]+|acc-[a-z]+|param-[a-z]+|cfg-[a-z]+|rpt-[a-z]+|explorer|config-panel|result-panel|hist-[a-z]+)\b` — 扫描 **.tsx 业务代码** 是否还有 factor-research class 调用。selftest 对应正反例补充。
  5. `--preflight-before-css-delete` 纳入 R14 扫描：s10 删除 factor-research CSS 定义前必须确保 .tsx 全仓无 factor-research class 调用。

## Major 发现（导致显著返工）

### M1 · data-catalog/JobQueue.tsx 含 4 处 `bt-status` 与 s5 "无 bt-*" 声明冲突

- **证据**：
  - `rg -n '\bbt-[a-z0-9-]+' src/web/src/app/data-catalog/JobQueue.tsx` → L173/176/181/185 共 **4 处** `className="bt-status bt-status-queue/done/fail"`：
    ```
    173:          <span className="bt-status bt-status-queue">排队中</span>
    176:            <span className="bt-status bt-status-done">✓ 完成</span>
    181:            <span className="bt-status bt-status-fail">✕ 失败</span>
    185:          <span className="bt-status bt-status-queue">已取消</span>
    ```
  - `1-requirements.md §1.1` 违规表写 "`className="bt-*"` 调用 253 处跨 **6** 文件（**trading 下 0 处，已验证**）：backtest/page(127)、OverviewTab(69)、PerformanceTab(27)、RobustnessTab(15)、TradesTab(9)、OverviewGreyTab(6)"。data-catalog/JobQueue 的 4 处**未计入**。
  - `4-tasks.md s5` 描述原文：`"**data-catalog 也无 bt-\***"` — 与实测矛盾。
  - `src/web/src/app/data-catalog/JobQueue.tsx` 现实测试 `rg -c '\bbt-' JobQueue.tsx` = 4。
- **影响**：
  1. bt-* 真实总数是 **253 + 4 = 257 处跨 7 文件**，而非 253 处跨 6 文件。
  2. s5 执行者若严格按任务描述"无 bt-*"行事，会跳过 JobQueue 中 4 处 bt-status 迁移；R2 扫描在 s5 验收步骤（`rg 'src/app/data-catalog/'`）会命中 4 次违规，s5 验收失败。
  3. 间接地，s11 全仓扫描补漏会捕获这 4 处，但 s5-s11 的 DAG 边界被破坏（s5 验收不应放到 s11 修正）。
- **进一步验证 backtest 的计数**：
  - 实测 `rg -c '\bbt-[a-z0-9-]+' src/web/src/app/backtest`：page(**144**)、OverviewTab(**74**)、PerformanceTab(28)、RobustnessTab(15)、TradesTab(9)、OverviewGreyTab(6) = **276**，而 s4 描述写 "127 + 69 + 27 + 15 + 9 + 6 = 253"；backtest 下**也低报了 23 处**（page 127→144、OverviewTab 69→74、PerformanceTab 27→28）。
- **置信度**：HIGH
- **修复**（必改）：
  1. §1.1 违规表修正 bt-* 为 "约 280 处跨 7 文件（精确：JobQueue 4 + backtest/page 144 + OverviewTab 74 + PerformanceTab 28 + RobustnessTab 15 + TradesTab 9 + OverviewGreyTab 6 = 280）"；
  2. `4-tasks.md s5` 描述删去"data-catalog 也无 bt-*"断言，改为"JobQueue.tsx 4 处 `bt-status` → `<StatusBadge>` QDS 组件（按 §3.3.3 映射）"；
  3. `4-tasks.md s4` 描述修正 bt-* 计数为更精确值（或改为"以 s2 扫描脚本输出为准"的结构性断言）；
  4. §3.9 影响文件清单补充 `data-catalog/JobQueue.tsx` — 改 | 迁移 4 处 bt-status。

### M2 · R6/R7/R8 的两阶段扫描实现 + selftest 不完整

- **证据**：
  - `3-tech-design.md §3.2.8 --selftest` 子命令列出 R4/R6/R9/R10/R12 的正反例。
  - **未列** R7 / R8 的 selftest 样例。
  - §3.2.3 R7：`<CartesianGrid\b[^>]*\b(stroke\|strokeDasharray)\s*=` 且"同 tag 范围内"不含 `\.\.\.CHART_GRID_STYLE` — "同 tag 范围内"的定义含糊（`<CartesianGrid ... strokeDasharray="3 3" {...CHART_GRID_STYLE} />` 这种 prop 顺序是否合规？脚本实现时用单行 rg 可能失败）。
  - §3.2.3 R8：PCRE2 负向先行断言 `\{(?!\s*CHART_LEGEND_STYLE\b\|\s*\.\.\.CHART_LEGEND_STYLE\b)` — 这个正则把 "非 CHART_LEGEND_STYLE 开头的 wrapperStyle 对象" 判违规；但 `wrapperStyle={{ ...CHART_LEGEND_STYLE, fontSize: 10 }}` 这种 spread-extra-prop 写法会被误判（`{{ ...CHART_LEGEND_STYLE` 的 `{\{` 已进入对象，`...` 前有 ` ` 和 `{` 而非 `{` 紧邻 `...`，边界可能不触发先行断言的规避路径）。
- **影响**：
  1. R7 / R8 在执行阶段可能误报或漏报，且**无 selftest 样例兜底**，首次在 CI 上出现误报时无自动化防线。
  2. 同 C2 round 1 教训：正则边界在不同 shell 实测环境下行为不一致，必须 selftest 验证。
- **置信度**：HIGH
- **修复**：
  1. §3.2.8 `--selftest` 子命令扩充 R7 / R8 正反例：
     - R7 正例：`<CartesianGrid strokeDasharray="3 3" stroke="var(--bd)" />`（无 spread）；反例：`<CartesianGrid {...CHART_GRID_STYLE} />`、`<CartesianGrid {...CHART_GRID_STYLE} strokeDasharray="3 3" />`
     - R8 正例：`<Legend wrapperStyle={{ fontSize: ".62rem", fontFamily: "var(--font-d)" }} />`；反例：`<Legend wrapperStyle={CHART_LEGEND_STYLE} />`、`<Legend wrapperStyle={{ ...CHART_LEGEND_STYLE, fontSize: 10 }} />`
  2. R7 的实现描述"同 tag 范围内"改为具体的 `-U --multiline-dotall` 两阶段流水线（类似 R6 的模板），明确 `{...CHART_GRID_STYLE}` 允许以任意 prop 顺序出现、允许与其它 prop 共存。
  3. R8 的 PCRE2 断言增加对 `spread + extra prop` 形式的豁免：`wrapperStyle\s*=\s*\{(?!\s*(?:CHART_LEGEND_STYLE\b|\{\s*\.\.\.CHART_LEGEND_STYLE\b))` — 即 `wrapperStyle={CHART_LEGEND_STYLE}` 和 `wrapperStyle={{ ...CHART_LEGEND_STYLE, ... }}` 均豁免。

### M3 · R9 规则对 ReportClient:508 / OverviewTab:684 / RobustnessTab:353 的跨行匹配实际行为未验证

- **证据**：
  - §3.2.3 R9 写 `<ReferenceLine\b[^>]*label\s*=\s*\{\s*\{[^}]*(fontSize\|fill\|fontFamily)[^}]*\}` 且不含 `\.\.\.CHART_LABEL_STYLE`；
  - 实测 ReferenceLine 有两种语法形式：
    - 单行（RiskTab:187、RobustnessTab:353）：`<ReferenceLine ... label={{ value: "...", fill: "...", fontSize: 9 }} />`
    - 多行（ReportClient:504-508、OverviewTab:679-684）：`<ReferenceLine\n  x={...}\n  stroke="..."\n  label={{ value: "...", fill: "...", fontSize: 10 }}\n  />`
  - R9 的 `[^>]*` 字符类在多行模式下是否跨行：`rg -U --multiline-dotall` 下 `.` 与 `[^>]` 都可跨行；但正则内部没有显式的跨行说明，执行者若未配 `-U --multiline-dotall` flag 会漏匹配 OverviewTab/ReportClient 两处。
  - §3.2.4 多行匹配策略明确 R6/R9 用 `-U --multiline-dotall` 两阶段；但 §3.2.3 R9 的**单行表达式**与 §3.2.4 的**两阶段流程**没有一一对应的脚本伪代码（R6 有，R9 无）。
- **影响**：
  1. R9 实际扫描时可能只捕获 2/4 处（RiskTab + RobustnessTab 单行），漏 OverviewTab + ReportClient 的多行形式。s6 / s4 验收 `rg 'src/app/research/'` / `rg 'src/app/backtest/'` 过早通过，违规未被检出。
  2. ReferenceLine label 的两种语法（跨行 vs 单行）在 Recharts 业务代码里都会出现，扫描器必须同时覆盖。
- **置信度**：HIGH
- **修复**：
  1. §3.2.3 R9 单行正则后追加说明"**必须**使用 `rg -U --multiline-dotall`"；或
  2. §3.2.4 增加 R9 的两阶段伪代码（如 R6 已示范）：阶段 1 `rg -U --multiline-dotall '<ReferenceLine\b[^/]*(?:label\s*=)[^/]*?/>'` 抓取完整 tag；阶段 2 grep `label={` 且不含 `CHART_LABEL_STYLE` 的为违规。
  3. §3.2.8 selftest 增加 R9 多行正例：
     ```
     <ReferenceLine
       x={10}
       label={{ value: "x", fontSize: 9 }}
     />
     ```
     必须命中。

## Minor 发现（次优但可工作）

### m1 · §1.9 历史 memory 作废"经用户明确授权"缺乏 interview 原文支撑

- **证据**：`interview.md` 全文 107 行，无任何 "memory" / "feedback" / "bt-cd" / "作废" 字样；第 4 轮"遗留 class 怎么处理 → 完全删除"只是操作层面的选择，并未显式触及"取代历史 memory"。
- **影响**：§1.9 声明在用户后续看到 memory 尚在、代码已按新方向落地后可能产生疑问；规划层缺一个保护性表述。
- **修复**：§1.9 表述改为"本规划**默认覆盖**以下历史 memory 中的主张（用户在 interview.md 第 4 轮选择"完全删除遗留 class"已隐含此方向）；执行完成后由主 agent 向用户确认并更新 memory 文件"；或请求主 agent 在本轮 review 后补充一次 interview "明确确认作废"问题。

### m2 · §3.3.7.4 `.btn-p` 迁移的 "语义确为成功动作" 判断增加 executor 决策负担

- **证据**：§3.3.7.4 对 `.btn-p`（globals.css 定义为绿色成功按钮）的迁移建议是 "按 DS 规则，使用 `variant="default"` 为 accent 橙；如语义确为"成功动作"则用自定义 className `bg-qds-success text-white`"。
- **影响**：research/page.tsx 里 `.btn-p` 的所有调用点（`rg -n 'className="btn-p"' src/web/src/app/research/page.tsx` 已验证存在）都需要 executor 逐一判断"是否成功动作"。这是主观判断，不同 executor 可能给出不同答案。
- **修复**：planner 明确给出**全部保留原绿色语义**（所有 `.btn-p` → `bg-qds-success text-white`）或**全部改为 accent 橙**（`variant="default"`）— 二选一的确定性答案。

### m3 · `.rpt-*` class 迁移在 ReportClient 实测为 0，但 §3.3.7.9 / §3.5.5 仍写入

- **证据**：
  - `rg 'className=.*\brpt-[a-z]+' src/web/src` 全仓命中 0 行。
  - ReportClient.tsx 实际使用的是 shadcn 组件和 Tailwind 类，不再引用 `.rpt-head/.rpt-back/.rpt-title/.rpt-sub/.rpt-meta`。
  - §3.3.7.9 表格列出 `.rpt-*` 迁移映射；§3.5.5 `ReportHeader.tsx` 描述 "承载 `.rpt-head/.rpt-back/.rpt-title/.rpt-sub`"。
- **影响**：ReportClient 实际不需要 `.rpt-*` 拆分；§3.3.7.9 / §3.5.5 的描述对应零工作量；executor 会困惑是否仍需创建 ReportHeader.tsx。
- **修复**：§3.3.7.9 `.rpt-*` 行保留在映射表中（globals.css L1971-1987 仍有定义，需在 s10 删除），但说明"调用点 0，本次无需 .tsx 迁移工作"；§3.5.5 ReportClient 拆分模板改为"按分节拆出 ReportIcChart / ReportLongShortChart / ReportFactorTable 即可，无需 ReportHeader"。

### m4 · `CHART_LABEL_STYLE` 新增常量保留 `fontFamily: "var(--font-d)"`，但 ReferenceLine label 的 4 处现状无一处带 `fontFamily`

- **证据**：
  - §3.3.6 `CHART_LABEL_STYLE` 定义含 `fontFamily: "var(--font-d)"`；
  - 实测 4 处 label object 分别是：
    - RiskTab:187 `{ value: "阈值", fill: "var(--warn)", fontSize: 9 }` — 无 fontFamily
    - RobustnessTab:353 `{ value: "Split", fontSize: 9, fill: "var(--warn)" }` — 无 fontFamily
    - ReportClient:508 `{ value: "Real IC", fill: "var(--dan)", fontSize: 10 }` — 无 fontFamily
    - OverviewTab:684 `{ value: "本金 $...", fill: "var(--warn)", fontSize: 10, position: "insideTopLeft" }` — 无 fontFamily
  - 引入 spread `{ ...CHART_LABEL_STYLE, value: "阈值" }` 后，**label 文字字体将从 Recharts 默认字体变为 JetBrains Mono** — 视觉行为**改变**。
- **影响**：视觉稳定性 — 4 个图表 label 的字体在迁移后变更，可能带来非预期的视觉差异。
- **修复**：两选一：
  - (a) `CHART_LABEL_STYLE` 删除 `fontFamily: "var(--font-d)"`，仅保留 `fontSize + fill`（需同时确定：fontSize 用 9 还是 10？planner 需决断一个标准值并说明为什么 4 处现状有两个值）；
  - (b) 保留 fontFamily，但在 §3.3.5 备注"此次迁移会统一 ReferenceLine label 字体为 JetBrains Mono，视觉上会变化（字形更清晰）"作为预期外变化。

## 缺失项

1. **s10 `--preflight-before-css-delete` 不覆盖 factor-research class 调用的扫描**（见 C1 修复点 5）。若保留当前设计，s10 删除 `.sc/.sc-l/.sc-v/.sc-sub` 后 data-catalog/page + backtest/TradesTab + PerformanceTab + TearsheetTab 视觉会静默退化。
2. **`--fix-hint` 子命令对 factor-research class 无迁移映射输出**。§3.2.6 说"每条违规后追加一行迁移建议（来自 §3.3 的映射表）"，但若新增 R14（C1 修复），需要在脚本里把 §3.3.7 的 85 条映射注入为 fix-hint。规划未提。
3. **`CHART_TOOLTIP_PROPS` spread 后的 `contentStyle` 内容是否足够**。实测 `chartTheme.ts:41-45` 的 `CHART_TOOLTIP_PROPS = { contentStyle, labelStyle, itemStyle, cursor }`；OverviewTab:676 的 contentStyle 里有 `borderRadius: 8`（chartTheme 里是 `"8px"`，一致）；但 OverviewTab 原 contentStyle 还包含 `background: "var(--popover)"`（chartTheme 用 `backgroundColor: "var(--bg-p)"`）— 迁移后实际 tooltip 背景色从 `--popover`（shadcn）变为 `--bg-p`（QDS），**颜色可能不同**。规划未指明这点。建议 s4 s8 s9 描述追加"迁移前后 tooltip 背景色需人工目测对照一次"或在 §3.3.5 追加"已确认 `--popover` ≡ `--bg-p`（如果一致）"的核对结果。
4. **R14 规则缺失导致全仓扫描不完整**（见 C1 修复点 4）。
5. **"最后一行是否 spread" vs "Tooltip 多行写法"**。R6 selftest 只覆盖单行 `<Tooltip contentStyle={...} />`；但 backtest/OverviewTab:675-678 是多行写法：
   ```
   <RechartsTooltip
     contentStyle={{ background: "var(--popover)", ..., fontSize: 11, ... }}
   />
   ```
   R6 的 `-U --multiline-dotall` 两阶段流水线设计已覆盖，但 selftest 正例**只写单行**，多行 selftest 样例未加入。建议补。
6. **StatusBadge 扩展的 TypeScript 破坏面**。§3.3.9 方案扩展 Status union 为 7 个键 + locale prop；调用点 `page.tsx:130` 传 `run.status`（类型可能是 `string`）— 代码片段里 `status: Status | string` 的 defensive fallback 是可接受的，但是**`components/StatusBadge.tsx` 原版的 `Badge variant={variant}`**（variant 是 "success" | "warning" | "info" | "error" | "neutral"）与 QDS 新版（className 驱动）的**导出语义不同**；s11 的"barrel re-export"会使所有调用点的 props 从 `{status, className}` 的 Badge variant 模式，变为 `{status, label?, locale?}` 的 span + styles 模式 — **CSS 外观变化**（原版是 `<Badge>` rounded-md + padding，新版是 `<span>` rounded-full + 不同 padding）。规划未指明这个 visual regression 风险。建议 §3.3.9 增加"视觉差异声明：barrel 切换会使 legacy 调用点的外观从 shadcn Badge 变为 QDS 新 span；需在 s11 执行后逐页目测"。

## 歧义风险

| 文档原文 | 解读 A | 解读 B | 选错后果 |
|---|---|---|---|
| `3-tech-design.md §3.3.7.4` `.btn-p` 注释 | 保留绿色（成功动作） | 改 accent 橙（DS 规则） | research 页面 7+ 个按钮视觉改变 |
| `4-tasks.md s5` "data-catalog 也无 bt-*" | JobQueue 的 4 处 bt-status 被忽略 | 其实要改，由 s11 兜底 | s5 验收失败（R2 命中） |
| `3-tech-design.md §3.3.9` "`status: Status \| string`" | 支持任意 string 传入不报错 | 仅允许 union 内 7 键 | defensive fallback 会隐藏错误；反之会破坏 legacy |
| `4-tasks.md s10` "删除范围 L1853-1987 factor-research" | 整块删除 | 保留 `.sl` / `.btn` / `.empty` 等"shared primitives" | 若保留，factor-research 全迁移的宣称被部分违反；若整删，调用点必须已迁移（C1 覆盖） |
| `3-tech-design.md §3.3.5` "`<Tooltip>` contentStyle 多属性复合对象强制 spread" | 唯一 spread 形式 | `contentStyle={CHART_TOOLTIP_STYLE}` alias 也算合规（现状 page.tsx:446） | R6 已明确唯一形式，alias 不再允许 — 一致 ✓ |
| `3-tech-design.md §3.3.6` `CHART_LABEL_STYLE` 含 `fontFamily` | 全部 4 处 label 字体统一为 mono | 保留原默认字体 | 视觉改变见 m4 |
| `1-requirements.md §1.9` "经用户明确授权" | 实际授权 | planner 默认推断 | 执行后若用户质疑则需回退或确认 |

## 假设分析

| 假设 | 级别 | 说明 |
|---|---|---|
| `.claude/skills/TinoHelmDS/preview/` 下 21 个 html 存在 | VERIFIED | `ls` 验证 |
| `docs/ui/` 不存在 | VERIFIED | `ls docs/ui` → No such file |
| R4 PCRE2 前后向断言正确过滤 Tailwind 类 | VERIFIED | shell 测试 `rg --pcre2` 命中 `cg/cr mono/dim`，不命中 `font-mono/bg-qds-success-dim/text-qds-info-dim/animate-qds-pulse/dark:bg-transparent` |
| factor-research class 仅出现在 research/ | **FRAGILE** | 验证失败 — 散落到 5 文件（C1） |
| data-catalog 下无 bt-* | **FRAGILE** | 验证失败 — JobQueue 4 处 bt-status（M1） |
| backtest 下 bt-* 精确 253 处 | **FRAGILE** | 验证失败 — 实测 276 处（M1 附带发现） |
| `--popover` 与 `--bg-p` 颜色等价 | 未验证 | 迁移 Tooltip 后可能出现背景色差异（缺失 3） |
| ReferenceLine label 4 处原字体是默认 / 非 mono | REASONABLE | label 对象未含 fontFamily，迁移后会变 mono（m4） |
| `components/ui/button.tsx:18` 用 `text-[var(--accent)]` — `--accent` 已定义在 globals.css | VERIFIED | globals.css L91 `--accent: oklch(...)` 是 shadcn 标准 token；R10 豁免已覆盖该目录 |
| Recharts `label` prop 接受 CSSProperties + 额外 value/position 字段 | REASONABLE | Recharts 类型定义允许 string / ReactElement / object；object 作 props 到内部 `<Label>` 子组件，扩展字段会被 spread 到 DOM — 本地实测 TypeScript 允许此形式 |
| `rg --pcre2 -V` 在 CI 环境（Docker API image）可用 | **FRAGILE** | API 镜像未内置 ripgrep；本任务脚本只在 dev/pre-push 运行，CI 需额外配置（§3.8 R-7 已说） |
| `interview.md` 用户明确作废 memory feedback | **FRAGILE** | 原文未提及，planner 属于推断（m1） |

## 预验尸

| 失败场景 | 文档是否应对 | 说明 |
|---|---|---|
| 1. s5 按描述迁移 data-catalog，未改 JobQueue 4 处 bt-status，验收 R2 命中失败 | **No** | M1 |
| 2. s4 完成后 s10 删除 `.sc-l` 定义，TradesTab/PerformanceTab/TearsheetTab 3 处 `<span className="sc-l">` 视觉退化 | **No** | C1 |
| 3. s5 完成后 s10 删除 `.sc/.sc-v/.sc-sub`，data-catalog/page L240-243 的 4 张 KPI 退化为无样式 | **No** | C1 |
| 4. s11 执行 StatusBadge barrel re-export，legacy 调用点外观从 shadcn Badge rounded-md 变为 QDS rounded-full | **No** | 缺失 6 |
| 5. R9 扫描漏匹配 ReportClient:504-508 / OverviewTab:679-684 的多行 ReferenceLine | **No** | M3 |
| 6. R7/R8 扫描误报/漏报，CI 偶发失败 | **No** | M2（selftest 不覆盖 R7/R8） |
| 7. 迁移 4 处 ReferenceLine label 后图表 label 字体统一为 mono，用户反馈视觉变化 | **No** | m4 |
| 8. R14（factor-research .tsx 扫描）缺失，s11 扫描补漏无法捕获散落使用 | **No** | C1 修复点 4 |
| 9. `CHART_TOOLTIP_PROPS` spread 后 tooltip 背景色从 `var(--popover)` 变 `var(--bg-p)` 视觉差异 | **No** | 缺失 3 |
| 10. executor 对 `.btn-p` 到底改 accent 橙还是保留绿色举棋不定 | Partial | §3.3.7.4 列两选项但未定论（m2） |

## 多视角笔记

### Executor 视角
- **卡点 1**：s5 描述说 "data-catalog 也无 bt-*" 但 JobQueue 里确实有 4 处 — executor 会因为描述过于绝对而跳过，直到验收失败才回来补。
- **卡点 2**：s4 任务描述的 factor-research 工作只字未提，但实际 backtest 的 3 个 Tab 中有 `.sc-l` 使用。executor 在 s4 结束后扫描通过（R1-R10、R12、R13），但在 s10 preflight 前才可能发现（R14 缺失则永不发现）。
- **卡点 3**：`--btn-p` 改色决策（m2）、`CHART_LABEL_STYLE` 字体决策（m4）— 两处主观判断需要 planner 先敲定。
- **卡点 4**：s6 的 factor-research 迁移涉及 6 个子组件拆分 + 85 class 映射 + 2 个 Legend + 1 个 ReferenceLine label；10h 估算在"0 次 kickback"假设下紧张。若执行中发现 §3.3.7.6 `.explorer/.config-panel/.result-panel` 的 flex 容器 + w-80 的实际宽度与设计不符（preview 里 w-72 / w-96），需要二次调整。

### Stakeholder 视角
- **真正问题**：规划声明的"14 页严格对齐 TinoHelmDS"目标在 r2 版本更扎实了（C1/C2 修正后），但 factor-research 散落到 backtest/data-catalog 的隐形污染（C1）若不修正，目标仍达不到；s10 的 globals.css 删除动作实际是"调用点没全迁 + CSS 定义删了"的破坏性组合。
- **范围恰当性**：12 任务 × 4 波次的 DAG 本身稳健；但 s4/s5/s6 之间的 factor-research 边界重叠（TradesTab/PerformanceTab/TearsheetTab 属 s4，JobQueue bt-status 属 s5，research/page 属 s6）没有在计划中显性化。波次 B 里 6 个并行 agent 不会互相修改同一文件，但 "factor-research 迁移决策"需要全局一致（比如 `<StatCard>` vs 手写 Tailwind），planner 应在 §3.3 给定统一优先级。
- **虚荣指标**：NFR-2 `globals.css` 行数 1210 ± 50 仍然是进程指标；实际关键是 R14 / R11 扫描 0 命中 + 视觉不退化。

### Skeptic 视角
- **最强反对论点**：本轮修订质量明显高于 r1，但新发现的 **factor-research 散落（C1）** 是 r1 / r2 两轮审查中均未被识别的深层问题。这暗示 "bt-* / dc-* / cg/ca/cr/dim/mono / factor-research" 四种遗留 class 之间的实际混用比任何文档描述都更复杂。建议在 r3 前 planner **运行一次真实的 R14 全仓扫描**，把结果作为 1-requirements.md §1.1 违规表的第七行补进去，让数字本身替代"个别散落"这种模糊表述。
- **备选方案**：拆 s4/s5/s6 为 "主要迁移" + "跨组件散落清理"两阶段 — 但 DAG 复杂度提高，不建议本轮引入。

## 上轮修改验证

| 上轮要求（Critic r1） | 是否解决 | 说明 |
|---|---|---|
| C1 `docs/ui/qds-*.html` 引用修正 | **Yes** | §3.3.3 / §3.3.4 / §3.4 / FR-4.4 全部改为 `.claude/skills/TinoHelmDS/`；`docs/ui/` 0 命中 |
| C2 R4 扫描规则修正（PCRE2 + selftest） | **Yes** | §3.2.3 R4 前后向断言 + §3.2.8 selftest 正反例；shell 验证通过 |
| M1 factor-research 子系统决策 | **Partial** | 选项 A（全迁移）已选定、§3.3.7 完整 85 class 映射已落地；但散落到 backtest/data-catalog 的 C1 新发现未被包括（planner 需要补刀） |
| M2 StatusBadge API 不兼容 | **Yes** | §3.3.9 新增决策章节，选项 a（扩展 Status union + locale）+ barrel re-export；s11 明确执行；s4/s7/s8/s9 禁止破坏性替换 |
| M3 未定义 CSS var 迁移 | **Yes** | §3.3.8 映射表 + 受影响文件清单 + R13 规则；1-requirements.md §1.1 追加违规行 |
| M4 fontSize 内联治理 | **Yes** | §3.2.3 R12 + §3.3.8 字号归一化表 + Recharts 透传豁免 |
| M5 shadcn dark: 前缀冲突 | **Yes** | §3.2.7 明确排除 `components/ui/**` + `components/qds/**`；selftest R10 反例已有 |
| M6 用户历史 memory 冲突 | **Partial** | §1.9 新增章节明确作废；但"经用户明确授权"缺 interview 原文支撑（m1） |
| m1 preview 孤儿 | Yes | FR-2.3 shadcn 默认 + QDS token |
| m2 R6 alias 冲突 | Yes | §3.2.3 R6 改为唯一 spread |
| m3 smoke test 阈值 | Yes | §s2 改为 R1-R13 每条至少 1 次命中 |
| m4 preflight-before-css-delete | Yes | §3.2.9 新增 + s10 第一步强制 |
| m5 字号归一化 | Yes | §3.3.8 保留 arbitrary-value `text-[0.62rem]` 等 |
| Architect C1 / C2 / M1-M5 等共用项 | Yes | 见 Round 1 Revision Notes 表 |

上轮 2 CRITICAL + 6 MAJOR + 5 MINOR 中 **2C + 5M + 5m 已解决，1M（M1 部分）+ 1M（M6 m1 支撑）留待本轮回应**。

## 修改要求（REVISE）

按优先级列出。CRITICAL / MAJOR 为 APPROVE 前置必改项。

1. **[CRITICAL C1] 补刀 factor-research 散落**
   - `1-requirements.md §1.1` 违规表 factor-research 行改为精确值"**44 处跨 5 文件**（research/page 36 + data-catalog/page 4 + TradesTab 2 + PerformanceTab 1 + TearsheetTab 1）"，删除"个别散落"措辞；
   - `3-tech-design.md §3.9` 影响文件清单新增 4 行：data-catalog/page.tsx、backtest/TradesTab、backtest/PerformanceTab、backtest/TearsheetTab 各自的 factor-research 迁移行；
   - `4-tasks.md s4` 描述追加："PerformanceTab/TradesTab/TearsheetTab 共 4 处 `.sc-l` → `<SectionLabel>` QDS 组件 或 `text-qds-t2 text-[0.52rem] uppercase tracking-widest`"；
   - `4-tasks.md s5` 描述追加："page.tsx:240-243 的 4 张 KPI 行 `.sc/.sc-l/.sc-v/.sc-sub` → 4 个 `<StatCard>` QDS 组件"；
   - 新增 **R14 规则**（`3-tech-design.md §3.2.3`）：在 .tsx 业务代码中扫描 factor-research class 的 className 出现；`--selftest` 补充 R14 正反例；`--preflight-before-css-delete` 纳入 R14；
   - `s10` 的 `--preflight-before-css-delete` 要求从 "R1-R10+R12+R13" 扩展为 "R1-R14"。

2. **[MAJOR M1] 修正 bt-* 计数 + data-catalog JobQueue 4 处 bt-status**
   - `1-requirements.md §1.1` bt-* 行从 "253 处跨 6 文件" 改为"约 280 处跨 7 文件（精确：JobQueue 4 + backtest/page 144 + OverviewTab 74 + PerformanceTab 28 + RobustnessTab 15 + TradesTab 9 + OverviewGreyTab 6 = 280）"；
   - `4-tasks.md s4` 工作量中 bt-* 具体计数同步刷新；
   - `4-tasks.md s5` 删除 "data-catalog 也无 bt-*" 断言；追加 "JobQueue.tsx 4 处 bt-status → `<StatusBadge status="..." />`（但禁止破坏顶层 StatusBadge 组件结构，仅改 className 到组件调用）" — 或等到 s11 统一 StatusBadge 后再回来改，但需在 s5 描述中标明"预留 4 处，s11 统一"（与 §3.3.9 一致）；
   - `3-tech-design.md §3.9` 新增 `data-catalog/JobQueue.tsx` 行，标注 4 处 bt-status。

3. **[MAJOR M2] 补齐 R7/R8 的 selftest 正反例 + R6 多行正例**
   - `3-tech-design.md §3.2.8` 增加 R7 / R8 / R6 多行的正反例对；
   - R8 PCRE2 断言明确支持 `wrapperStyle={{ ...CHART_LEGEND_STYLE, fontSize: 10 }}` spread-extra-prop 写法不命中；
   - R7 明确多 prop 顺序不敏感（`<CartesianGrid strokeDasharray="3 3" {...CHART_GRID_STYLE} />` 合规）。

4. **[MAJOR M3] R9 明确使用 `-U --multiline-dotall` + selftest 多行样例**
   - §3.2.3 R9 或 §3.2.4 增加 R9 两阶段实现伪代码（模板 R6 即可）；
   - §3.2.8 selftest 增加 R9 多行正例，如 4 行的 `<ReferenceLine\n  x={}\n  label={{ fontSize: 9 }}\n/>`。

5. **[MINOR m1] §1.9 "经用户明确授权"降语**
   - 改为"本规划默认覆盖历史 memory，interview.md 第 4 轮"完全删除遗留 class"选择已隐含此方向；执行完成后由主 agent 向用户确认并更新 memory 文件"。

6. **[MINOR m2] `.btn-p` 迁移决策定锚**
   - §3.3.7.4 选 A（全 accent 橙）或 B（全绿色保留）— 选一，写死。

7. **[MINOR m3] ReportClient `.rpt-*` 迁移校订**
   - §3.3.7.9 `.rpt-*` 行保留（供 s10 删除），但追加注释"调用点 0，无 .tsx 迁移"；
   - §3.5.5 ReportClient 拆分模板去掉 `ReportHeader.tsx`（无必要）。

8. **[MINOR m4] `CHART_LABEL_STYLE` 字体决策**
   - 二选一并在 §3.3.6 / §3.3.5 明确：
     - (a) 删除 fontFamily：仅保留 fontSize + fill，并统一 fontSize 值；
     - (b) 保留 fontFamily：补注"迁移后 4 处 ReferenceLine label 字体统一为 JetBrains Mono，这是预期内视觉变化"。

9. **[缺失 5] `--popover` 与 `--bg-p` 颜色一致性核对**
   - planner 在 §3.3.5 Tooltip spread 迁移节补一行"已确认 globals.css 中 `--popover` = `--bg-p`（或列出差值）"；
   - 若颜色不等价，OverviewTab 迁移后 tooltip 背景色会变化，需在 s4 描述中标注"已知视觉差异"。

10. **[缺失 6] StatusBadge barrel re-export 视觉差异声明**
    - §3.3.9 补"barrel 切换后 legacy 调用点（backtest/page.tsx / optimization/page.tsx）外观会从 shadcn Badge 的 rounded-md 变为 QDS 的 rounded-full + span 结构 — 在 s11 执行后逐页目测对比；如差异过大，考虑保留顶层 StatusBadge 的 Badge 视觉但内部查表改为 QDS 新 map"。

## 判决理由

VERDICT: **REVISE**。

r2 修订对 r1 提出的 2 CRITICAL + 6 MAJOR 绝大部分落地充分（PCRE2 selftest、factor-research 全迁移选项 A、StatusBadge 扩展 + barrel、未定义 var 映射 + R13、fontSize R12、shadcn 豁免、历史 memory 声明）。但 **缺口分析（阶段 5）** 和 **验证（阶段 2，运行扫描核对实际数字）** 发现了 r1 未触及的深层问题：
- 1 个 CRITICAL（factor-research 散落到 backtest/data-catalog 未被识别）— 会使 s10 删除 CSS 后 3-4 处视觉退化，**执行阶段确定发生**；
- 3 个 MAJOR（JobQueue 4 处 bt-status + bt-* 总数低报 / R6-R8 selftest 缺失 / R9 多行扫描未明示）— 会使子任务验收提前通过但实际违规未检出；
- 4 个 MINOR 可并发修正。

**现实检查**（阶段 7）：
- C1 的现实最坏情况：s10 删 CSS 后 4-8 处 `.sc-l / .sc / .sc-v / .sc-sub` 静默退化 — 回滚路径清晰（git revert s10）但追查成本高。**不降级**（视觉退化 + 无自动化捕获）。
- M1 的现实最坏情况：s5 验收失败一次，executor 花 15 分钟修 4 行；现实成本低，但**不降级**（文档事实错误）。
- M2 的现实最坏情况：R7/R8 在 CI 偶发误报，首次发生可能 block PR 数小时；**不降级**（需要在本轮修复，否则进入 CI 成本更高）。
- M3 的现实最坏情况：R9 漏检 2/4 处（多行形式），但因 s4/s6 的任务描述已列出具体行号，executor 可能仍正确迁移；视为保护机制缺失；**可降级为 MINOR**。

保持 MAJOR 级别 for M3 以触发 selftest 补充。未升级 ADVERSARIAL — 问题足够清晰，不需要假设更多隐藏。

## Open Questions（未评分）

- 用户是否正式授权作废 `feedback-bt-card-classes.md` 等历史 memory？interview 原文无支撑，planner 应请求主 agent 再确认一次。
- `CHART_LABEL_STYLE` 最终是否含 `fontFamily`？视觉变化的接受度取决于用户偏好。
- s5 的 JobQueue 4 处 bt-status 迁移究竟在 s5 还是 s11 完成？规划需决断（DAG 一致性）。
- `<StatCard>` QDS 组件在 data-catalog/page.tsx:240-243 的迁移中，4 张 KPI 是否有 subtext（如"最新数据 → 日期 + 标签"两行）？QDS StatCard 组件是否支持 subtext prop？需 planner 确认组件 API。

ReviewPass: critic
VERDICT: REVISE
