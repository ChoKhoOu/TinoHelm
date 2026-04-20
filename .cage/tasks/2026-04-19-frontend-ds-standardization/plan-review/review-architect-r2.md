# Architect Review — Round 2

**VERDICT: REVISE**

## 摘要

Round 1 的大部分修复到位：R4 PCRE2 前后向断言实测正确（14 处精确匹配、`font-mono`/`bg-qds-*-dim` 等 Tailwind 合法类不命中）；factor-research 85 class 全迁移方案 §3.3.7 映射覆盖充分；`docs/ui/` 引用从 tech-design / 1-requirements / tasks 中清除；StatusBadge 扩展 + locale 方案在 s11 承接；`--selftest` / `--preflight-before-css-delete` 设计合理。但仍发现 **2 个 CRITICAL**（`--accent-*` 变体漏扫 + data-catalog `types.ts` 漏入 s5 范围）、**2 个 MAJOR**（bt-* 总数低估 + 2-research.md 残留 `docs/ui/` 引用）、**2 个 MINOR**（R4 不覆盖 `cn("cg")` 形态的潜在未来风险 + 波次 B 关键路径 10h 带来的单点风险）。

## 代码引用验证（重新采样 @ 2026-04-19 22:45）

| 引用 | 实测 | 状态 |
|------|------|------|
| R4 PCRE2 正则对 `font-mono` 不命中 | 确认（反例 6 条全部不命中） | ✅ 修复到位 |
| R4 PCRE2 正则对 `className="cg"`/`"dim"`/`"cr mono"` 命中 | 确认（正例 3 条全部命中） | ✅ |
| R4 PCRE2 正则对 `className={cn("cg", rest)}` 命中 | **不命中**（regex 要求 `className="..."` 前缀，不覆盖 cn() 包裹形态） | ⚠️ 见下 MINOR-1 |
| R4 全仓严格扫描计数 14 处跨 3 文件 | 14 / research(9) / data-catalog page(4) / DeleteDialog(1) | ✅ 与 §1.1 一致 |
| `docs/ui/` 在 tech-design / 1-requirements / 4-tasks / task.json 中的引用 | 仅残留 Round 1 Revision Notes 与"不存在"声明（预期保留） | ✅ |
| `docs/ui/` 在 **2-research.md** 中的引用 | **L19 仍写 `docs/ui/qds-*.html`（既有）**，称其存在 | ❌ 见下 MAJOR-2 |
| `.claude/skills/TinoHelmDS/preview/` 含 21 个 HTML | 21 个（外加 `_split.css` 非 HTML） | ✅ |
| `.claude/skills/TinoHelmDS/` 主体文件（`Web UI Kit.html` / `Charts Spec.html` / `colors_and_type.css` / `QDS Pitch Deck.html` / `SKILL.md` / `README.md`） | 全部存在 | ✅ |
| factor-research 子系统 globals.css L1853-1987 | 135 行，98 unique class selectors（§3.3.7 声明 85 个） | ⚠️ 计数低报 13 个，但映射家族覆盖全面，见下 MINOR-3 |
| `L1856` 单行组合 `.mono{}.dim{}.cg{}.cr{}.ca{}.ci{}` | 确认 6 个单行定义；R11 非行首锚定 | ✅ |
| `globals.css` 总行数 1987，预期删后 1210 ± 50 | 1987 现状；删 780 行后为 1207 | ✅ |
| s7 `trading/` 全目录 `bt-*` 调用 | 实测 0 处 | ✅ 虚构已删 |
| `--accent-*` 全仓调用总数（**所有变体**） | **55 处跨 8 文件**（含 TabNav.tsx） | ❌ 见下 CRITICAL-1 |
| `--accent-*` 变体种类 | **10 种**：green / orange / red / amber / blue / purple / green-10 / red-20 / blue-20 / amber-20 / purple-20 | ❌ §3.3.8 只列 5 种，R13 正则只覆盖 5 种 |
| bt-* 全仓实测（backtest/） | **276 处跨 6 文件**（§1.1 声明 253） | ❌ 见下 MAJOR-1 |
| dc-* 在 data-catalog/ 实测（含 types.ts） | **65 处跨 6 文件**（s5 file list 只含 5 文件，忽略 types.ts 的 12 处） | ❌ 见下 CRITICAL-2 |
| globals.css `.dc-sl` 起点 L1640 / `.dc-filter-strip` L1659 | 确认 | ✅ 修复到位 |
| StatusBadge 顶层 6 状态 vs QDS 4 状态 | §3.3.9 方案扩展为 7 键（含 `done` 别名）+ locale prop + s11 统一 | ✅ |

## 需求审查（1-requirements.md）

### Critical 发现

1. **`--accent-*` 变体不完整：遗漏 5 种未定义 variant + 1 个受影响文件**
   - **证据**：
     - §1.1 违规表（L36）与 §1.1 字体迁移路径（L50-L54）只列 `--accent-green` / `--accent-orange` / `--accent-red` / `--accent-red-20` / `--accent-green-10` 共 **5 种**；但实测全仓尚有：
       - `--accent-amber` — 11 处跨 5 文件（OrdersPanel 4 / TopBar 3 / StrategyPanel 3 / ActionBar 2 / TabNav 1 … 待完整扫描）
       - `--accent-blue` — 7 处跨 5 文件（TopBar / TabNav / OrdersPanel / PositionsTable / StrategyPanel）
       - `--accent-purple` — 1 处（FillsStream）
       - `--accent-blue-20` — 1 处（PositionsTable）
       - `--accent-amber-20` — 1 处（OrdersPanel）
       - `--accent-purple-20` — 1 处（FillsStream）
     - 完整扫描命令 `rg 'var\(--accent-' src/web/src --glob='*.tsx'` 共 **55 处跨 8 文件**（R1 申明 35 处跨 7 文件，漏 20 处 + `TabNav.tsx` 文件）。
     - 这些变量在 `globals.css` 中全部**未定义**（`rg -- '--accent-(amber|blue|purple)' globals.css` 返回 0）。
   - **影响**：
     - §3.3.8 的 R13 正则 `var\(--accent-(green|orange|red(-20)?|green-10)\)` 只匹配 5 种，对 `--accent-amber`/`--accent-blue`/`--accent-purple` 及其 `-10/-20` 变体**不报警** — 迁移完成后这些仍是运行时未定义的视觉 bug，R13 验收 exit 0 ≠ 合规。
     - §3.3.8 的"未定义 CSS 变量 → Tailwind 语义类映射表"只有 5 行，executor 迁移 amber/blue/purple 时**无映射可查**。
     - s7 "35 处" 工作量估算偏低约 40%；s7 受影响文件漏 `TabNav.tsx`（虽只 1 处，但影响 R13 验收清单完整性）。
     - 决策缺口：`--accent-amber` 对应 QDS 哪个 token（`--warn`？），`--accent-blue` 是否对应 `--info`？ planner 未做决策。
   - **修复（必须）**：
     1. §1.1 违规表 L36 扩展统计：总数改为 "55 处跨 8 文件"，列出 10 种 variant；
     2. §1.1 字体迁移路径表追加 5 条映射（建议：`--accent-amber` → `text-qds-warning` / `--warn`；`--accent-blue` → `text-qds-info` / `--info`；`--accent-purple` → 决策：保留或映射到 `--acc`；`--accent-*-20/-10` 对应 dim 变体）；
     3. §3.3.8 映射表同步扩展至 10 行；
     4. §3.2.3 R13 正则扩展为 `var\(--accent-(green|orange|red|amber|blue|purple)(-?(10|20))?\)` 或等价白名单式；
     5. §3.3.8 受影响文件清单补入 `TabNav.tsx`；
     6. s7 描述明确列出新增的 amber/blue/purple 迁移 + TabNav 文件；
     7. s7 `--selftest` 正/反例追加 `var(--accent-amber)` / `var(--accent-blue)` 命中断言。

2. **s5 / §3.9 data-catalog 范围遗漏 `types.ts` 文件**
   - **证据**：
     - `rg -c '\bdc-[a-z0-9-]+' src/web/src/app/data-catalog` 命中 **65 处跨 6 文件**：page(23)、JobQueue(14)、FetchDialog(8)、FilterTabs(7)、**types.ts(12)**、DeleteDialog(1)。
     - §1.1 违规表 L24 声明 dc-* 调用"53 处跨 5 文件"。遗漏 `types.ts` 的 12 处（`TYPE_BADGE_CLS: Record<string, string>` 字典，键 `klines` → `"dc-type-kl"` 等）。
     - s5 任务描述（4-tasks.md L168-184）列出 5 个修改文件：`page.tsx` / `FetchDialog.tsx` / `DeleteDialog.tsx` / `JobQueue.tsx` / `FilterTabs.tsx` / `CoveragePanel.tsx`（实际 6 个），**完全未提 `types.ts`**。
     - §3.9 影响文件清单 L683-684 也未列 `types.ts`。
   - **影响**：
     - executor 迁移后，`TYPE_BADGE_CLS` 字典中 12 个 `"dc-type-kl"` / `"dc-type-ipk"` / `"dc-type-mpk"` / `"dc-type-pik"` / `"dc-type-at"` / `"dc-type-tr"` / `"dc-type-fr"` 字符串值仍会残留 — R3 扫描规则（`className="..."` 形态）**不会命中字典字符串常量**，但 s10 删除 `.dc-type-*` 定义后这些字符串会指向不存在的 class，Badge 背景色丢失。
     - 即便 R3 扩展后能命中，executor 没有迁移映射指导：`"dc-type-kl"` → `"bg-qds-info-dim text-qds-info"` 这种字符串重写是必需的，但未在 §3.3.4 明确写入。
     - s5 工作量（3.5h）未计入 `types.ts` 重写 + 相应 `<Badge>` 组件调用更新。
   - **修复（必须）**：
     1. §1.1 违规表 L24 "5 文件" 改为 "6 文件"，并明确提出 `types.ts` 的 12 处 `TYPE_BADGE_CLS` 字符串常量值需要重写；
     2. §3.3.4 映射表增加"dc-type-* 字典常量迁移策略"小节（例：`TYPE_BADGE_CLS` 改为返回 Tailwind class string `"bg-qds-info-dim text-qds-info"` 或改为 JSX 组件函数 `renderTypeBadge(type)`）；
     3. §3.9 影响文件清单 data-catalog 行补充 `types.ts` + 改动描述；
     4. s5 描述添加 `types.ts` 文件 + 工作量上调至 4h；
     5. R3 验收加强：`rg 'dc-type-[a-z]+' src/web/src/app/data-catalog` 必须 0 命中（覆盖 types.ts 字符串）。

### Major 发现

1. **bt-* 调用点总数从 253 低估为 276（+23，+9%）**
   - **证据**：`rg -c '\bbt-[a-z0-9-]+' src/web/src/app/backtest` 命中 **276 处跨 6 文件**（page 144 / OverviewTab 74 / PerformanceTab 28 / RobustnessTab 15 / TradesTab 9 / OverviewGreyTab 6）。
   - §1.1 违规表声明 "253 处跨 6 文件" — `page(127) + OverviewTab(69) + PerformanceTab(27) + RobustnessTab(15) + TradesTab(9) + OverviewGreyTab(6) = 253`。
   - 与实测差异：page.tsx +17（127 → 144）、OverviewTab +5（69 → 74）、PerformanceTab +1（27 → 28）。
   - **影响**：s4 工作量 8h 估算基于 253 处；多出来的 23 处需要 +0.5-1h。不致命但建议同步刷新。
   - **修复**：§1.1 表 + s4 描述同步更新到实测数字；s4 工作量微调至 8.5h。

2. **`2-research.md` L19 仍然声称 `docs/ui/qds-*.html` 存在**
   - **证据**：
     - `rg -n 'docs/ui' 2-research.md` 命中 1 行：
       > `| skill 页面级参考 | \`docs/ui/qds-*.html\`（**既有**） | 页面级设计装配图（backtest / data-catalog / trading / strategies / missing-pages 等） |`
     - Round 1 Revision Notes CR-3 声称"`docs/ui/qds-*.html` 不存在 — 已修复"，但修复范围只覆盖 `3-tech-design.md` / `4-tasks.md` / `1-requirements.md`，**漏改 `2-research.md`**。
   - **影响**：
     - 审查员（本轮）在交叉阅读 2-research.md 时会被误导（实际文件不存在）。
     - 未来 agent 若引用 2-research.md 作为事实来源，会继续传播 `docs/ui/` 虚构。
   - **修复**：
     1. 将 2-research.md L19 的 "`docs/ui/qds-*.html`（既有）" 改为 "`.claude/skills/TinoHelmDS/Web UI Kit.html` + `Charts Spec.html`"；
     2. 追加一句 "Round 1 修正：页面级装配参考从 `docs/ui/qds-*.html`（已验证不存在）改为 skill 下 `Web UI Kit.html`"。

### Minor 发现

1. §1.1 违规表的"cg/ca/cr/ci/dim/mono 独立 token 14 处" 精确匹配实测；R4 正则对 `className={cn("cg", rest)}` 形态**不命中**（regex 硬性要求 `className="..."` 字面量前缀）。目前 src/web/src 下实测 `cn(.*"(cg|ca|cr|ci|dim|mono)"` = 0 处，未来若 executor 在迁移过程中临时用 `cn("cg", cond && "active")` 形式，R4 将漏报。可选修复：R4 正则放宽为捕获 `["'][^"'}]*(?<![-a-zA-Z0-9_])(cg|ca|cr|ci|dim|mono)(?![-a-zA-Z0-9_])[^"'}]*["']` 且在 `className` 同行出现（不要求紧邻 `=`），或在 `--selftest` 中明确声明"不覆盖 cn() 包裹形态"作为已知限制。
2. s6（10h）作为新关键路径，单 executor 失败半径大。4-tasks.md §"子任务拆分风险" 已预留"允许 s6a/s6b 拆分"决策权给 executor，但 task.json 的 parallel_groups **未预留**这种子拆分 — 若 executor 在运行时决定拆，需要更新 parallel_groups 才能并行（目前 parallel_groups 固定）。建议在 task.json 加一个注释字段说明"s6 允许运行时动态拆分，parallel_groups 对此场景视为单任务"。

## 技术设计审查（3-tech-design.md）

### Critical 发现

见需求审查 Critical-1（R13 正则 + §3.3.8 映射表缺口）与 Critical-2（types.ts 漏入 s5）。同一问题穿透三文档。

### Major 发现

1. **§3.3.7 factor-research 映射表的 class 总数声明与实测不一致**
   - **证据**：§3.3.7 开头 "85 个 class"，但 `sed -n '1853,1987p' globals.css | rg -o '\.[a-zA-Z][a-zA-Z0-9_-]*' | sort -u | wc -l` 返回 **98 unique class selectors**。其中 ~8 个（`.w3`/`.a`/`.org`/`.html`）是 SVG data URI 字符串假命中可排除；另 ~4-5 个（`.arr`/`.pdot`/`.sub`/`.tr`/`.open`/`.disabled-item`/`.lim-cur`/`.lim-full`）是父子复合选择器（如 `.acc-head .arr`），迁移时随父组件处理，不需独立映射。但仍有 ~85-90 个顶层 class 需要处理。
   - **映射覆盖度检查**：§3.3.7 的 9 个子节（3.3.7.1 ~ 3.3.7.9）按家族分组，覆盖 `.mono/.dim/.cg/.cr/.ca/.ci/.tip/.g/.g2-g6/.badge/.sc/.sc-l/.sc-v/.sc-sub/.cd/.cd-h/.cd-b/.sl/.btn/.btn-p/.btn-a/.btn-o/.btn-g/.fg/.frow/.fl/.fi/.fsel/.data-avail/.explorer/.config-panel/.result-panel/.cfg-section/.cfg-title/.acc-group/.acc-head/.acc-body/.acc-item/.factor-limit/.param-*/.verdict/.verdict-pass/.verdict-warn/.verdict-fail/.ctbl/.hist-clickable/.spinner/.factor-dot/.turn-row/.turn-item/.turn-label/.turn-val/.empty/.empty-icon/.empty-title/.empty-desc/.report-content/.rpt-*/.tab-bar/.dtab/.hm-*/.wf-*/.hbar/.hist-pager` — 覆盖约 60-65 条顶层 selector。
   - **未在映射表中显式提到的顶层 class**：`.action-row`、`.data-avail`（提到了）、`.w3`/`.a`/`.org`/`.html` 属于 SVG url() 里的字符串（虚假计数）、`.w3`（同）。剩余潜在未覆盖：`.arr/.pdot/.sub` 等子选择器会在父组件迁移时自然处理。
   - **影响**：严格来说 §3.3.7 的"85 个"与实测 ~98 存在统计方式的差异；实际映射覆盖充分（所有业务相关 class 都有目的地），但数字不准确会误导 executor 自查时产生"还漏了 13 个"的疑虑。
   - **修复**：§3.3.7 开头改为 "factor-research 子系统共 ~98 个 class selector（含若干 SVG data URI 内假命中与父子选择器），其中顶层需映射约 85 个 class"；或直接改为 "85+ 个 class（其余为父子复合选择器）"。

### Minor 发现

1. §3.3.7.4 Buttons 映射中 `.btn-p` → `variant="default"`（accent 橙），但括号中承认"globals.css 的 `.btn-p` 背景色是 `var(--suc)` 绿色"。设计系统规则是 accent，但历史代码实际渲染为绿色。research/page.tsx 使用 `.btn-p` 时是哪种语义（"启动分析"是成功动作，所以绿色合理；但 `<Button variant="default">` 会变橙色，视觉上存在 hue shift）。建议：planner 给出明确决策 — executor 在 s6 执行时是否需要视觉截图对比以确认哪个色是 UX 意图。
2. §3.2.8 `--selftest` 的 R10 反例写 `assert_no_match R10 'src/components/ui/button.tsx:bg-[var(--acc-d)]'` — 注意实际 R10 `--glob '!src/web/src/components/ui/**'` 是 ripgrep 排除 glob，而 assert 形式是否真正测试 glob 排除取决于脚本 `assert_no_match` 的实现细节。建议 s2 脚本实现时明确 `assert_no_match R10` 的语义是"给定伪文件路径 + 内容，扫描时应被 glob 过滤"，而不仅仅是"内容不命中 R10 正则"。

## DAG 审查（4-tasks.md + task.json）

### 合法性
- 拓扑无环 ✓；s10/s11 在 s4-s9 全部完成后启动 ✓；s12 在 s10/s11 后收官 ✓。
- 新增 selftest / preflight 子命令**未改变**依赖关系 — 它们是 s2 的内部子命令，s10 的第一步显式调用 preflight 属于 s10 实现细节，不引入新依赖。
- StatusBadge 扩展承接在 s11（Step 11b），s4/s7/s8/s9 明确禁止直接替换 `components/StatusBadge.tsx`；s11 depends_on [s4..s9]，时序安全 ✓。

### 并行性
- 波次 B（s4-s9）并行：文件集仍互不相交（s4 backtest / s5 data-catalog / s6 research / s7 trading / s8 analytics+optimization+orders+watchlist / s9 root+strategies+settings）✓。
- **新风险（MINOR-2 已指出）**：s6 从 4h 变 10h，占据关键路径 10h；若 s6 失败需回滚，波次 C 全阻塞。
- **并行组未变但 critical path 上移**：Round 0 关键路径 s4 11.25h，Round 1 关键路径 s6 16h。建议在 4-tasks.md 波次 B 后添加"s6 单点监控"—— 如果 s6 >6h 仍未完成 Accordion 组件族迁移，executor 应主动拆为 s6a（research/page + ResearchDatasetPanel/FactorList/Config/Result/Chart/Queue）+ s6b（ReportClient + Header/Kpi/Ic/LongShort/FactorTable）。当前 §"子任务拆分风险" 已提议但未强制。

### 遗漏任务 / 粒度问题
1. **s5 遗漏 `types.ts`**（见 Critical-2）。
2. **s7 遗漏 TabNav.tsx + amber/blue/purple 变体**（见 Critical-1）。
3. **s1/s2/s3 并行安全性**：三者均为新增或局部改动，实测无文件交集 ✓。
4. **s10 preflight 失败回退路径不清**：s10 描述说 "preflight exit 1，停止删除，回到 sN 补漏"，但没说"回到哪个 sN"。建议 s10 描述追加"preflight 输出的违规文件所属路由决定回退 target：backtest 违规 → s4、data-catalog → s5、research → s6、trading → s7、analytics/optimization/orders/watchlist → s8、root/strategies/settings → s9、其他 → s11"。

## 权衡分析

| 决策 | 正方 | 反方 | 建议 |
|------|------|------|------|
| s6 单任务 10h（不拆为 s6a/s6b） | 保持 DAG 简单；executor 一次 context 加载；6 子组件拆分与原语迁移高度耦合 | 单点失败风险 ×2.5（从 4h 变 10h）；ReportClient 相对独立（5 新文件 + 11 原语调用）可单独完成 | 保持单 s6 + 在 4-tasks.md "§子任务拆分风险" 段落加软约束："若 s6 超过 6h 未完成 research/page 的 6 子组件迁移，必须拆出 s6b 处理 ReportClient" |
| R13 正则收紧为 5 变体白名单 vs 扩展 whitelist | 严格枚举可控；R13 当前实现 | 遗漏 amber/blue/purple 导致 R13 "合规"但实际仍有未定义变量运行时 bug | 扩展为 `var\(--accent-(green\|orange\|red\|amber\|blue\|purple)(-(10\|20))?\)`；CRITICAL-1 必修 |
| `.btn-p` 迁移到 `variant="default"`（accent 橙）vs 保留绿色 | 遵循 DS 规则；一致性 | 既有 research "启动分析" 按钮历史为绿色，一次性改橙色可能引起用户 UX 疑虑 | planner 决策；建议 executor 在 s6 开始时对照 `preview/component-buttons.html` 的 primary 是否明确为 accent 橙；如是则迁移无歧义；如 preview 含 success-button 变体则 `.btn-p` 按语义走 success |
| dc-type-* 字典字符串迁移策略（字符串 vs 组件化） | 字符串：最小化 diff；保留 `TYPE_BADGE_CLS` map 结构 / 组件化：更 React-ful，易扩展 | 字符串方案依赖 Tailwind class 在字典中硬编码，视觉 review 时读者需要跳到字典定义；组件化工作量大但长期收益高 | 选字符串方案，但在 §3.3.4 明确写出字典重写示例；s5 加一条"保持 TYPE_BADGE_CLS 的 key 不变，只改 value" |
| types.ts 同属 s5 vs 单独任务 | 同属 s5：DAG 简单 / 单独任务：粒度更精细 | 同属 s5 工作量上调至 4h 合理；单独任务会膨胀 DAG | 同属 s5，工作量上调；明确改动 scope |

## 遗漏项

1. **`--accent-amber/blue/purple` 变体未映射**（Critical-1）。
2. **`types.ts` 未列入 s5 范围**（Critical-2）。
3. **2-research.md 未更新 `docs/ui/` 引用**（Major-2）。
4. **s10 preflight 失败回退路径未明示**（DAG 粒度问题）。
5. **bt-* 总数 23 处差异**（Major-1）。
6. **R4 对 `className={cn("cg", ...)}` 形态不覆盖**（Minor-1，目前无命中，但属于规则脆弱性）。
7. **s6 动态拆分的 parallel_groups 适配**（Minor-2）。

## 上轮修改验证

| 上轮要求（R1 Review） | 是否解决 | 说明 |
|---------|---------|------|
| CR-1 R4 扫描误报 | ✅ 是 | PCRE2 前后向断言实测正确；6 反例全不命中；3 正例全命中；R4 全仓严格扫描 14 处 |
| CR-2 factor-research 子系统决策 | ✅ 是（选 A 全迁移） | §3.3.7 映射 85+ 个 class；s6 工作量 10h；s10 扩大删除范围；NFR-2 目标 1210 ± 50 |
| MA-1 Label 虚构引用 | ✅ 是 | R9 改名 reference-line-label，扫描 `<ReferenceLine label={{...}}>` 对象；§3.3.5 更新；4 处实际调用点列出 |
| MA-2 trading bt-* 虚构 4 处 | ✅ 是 | §3.9 / s7 去除 bt-* 叙述；trading 实测 0 处 |
| MA-3 StatusBadge API 决策 | ✅ 是 | §3.3.9 扩展 Status union 为 7 键（含 done 别名）+ locale prop；s11 承接 |
| MA-4 未定义 --accent-* 变量 | ⚠️ 部分 | §3.3.8 列出映射 + R13 规则，但**只覆盖 5 变体**；实测有 10 变体，缺 amber/blue/purple 及其 -10/-20 |
| MA-5 fontSize 内联未扫描 | ✅ 是 | 新增 R12；字号归一化映射 §3.3.8；Recharts 透传豁免 |
| MA-6 shadcn dark: 冲突 | ✅ 是 | `--mode both-themes` 排除 components/ui/ + components/qds/ |
| MA-7 历史 memory 冲突 | ✅ 是 | §1.9 声明作废 4 个 memory + FR-6.1 Historical Notes + s12 Step 5 |
| MA-8 CLAUDE.md 既有章节矛盾 | ✅ 是 | FR-6.2 "改写"而非"追加"；s3 Step 3a/3b/3c 三步分离；s12 Step 5a/5b/5c 对应 |
| MA-9 dc-* 起点 / 计数 | ⚠️ 部分 | L1640 起点已确认；但 types.ts 12 处 dc-type-* 字符串常量**遗漏**，s5 / §3.9 均未列 types.ts 文件 |
| MA-10 R11 漏检 L1856 | ✅ 是 | R11 正则非行首锚定；s10 明确要求 L1856 整行原子删除 |
| MA-11 其他（motion/NotificationListener） | ✅ 是 | 1-requirements §1.3.3 明确非迁移范围 |
| Architect-Minor-1 R10 例外 components/ui | ✅ 是 | R10 glob 排除 + FR-1.6 豁免声明 |
| Architect-Minor-2 s12 Step 2.5 AC-2 fallback | ✅ 是 | s12 Step 2.5 新增 |
| Critic-m2 R6 alias 冲突 | ✅ 是 | R6 改为唯一 spread 形式；删除 alias |
| Critic-m3 smoke test 阈值 | ✅ 是 | 不再依赖 "≥ 300" 绝对数字，改为结构性 "R1-R13 每条至少 1 次" |
| Critic-m4 preflight-before-css-delete | ✅ 是 | §3.2.9 + s10 第一步 |
| Critic-m5 text-[11px] 字号归一化 | ✅ 是 | §3.3.8 字号表 |

**综合**：18 个修改要求中 **16 个完全解决**、**2 个部分解决**（MA-4 / MA-9）。部分解决的两个都是"计数不完整"类问题（变体漏扫 / 文件漏列），属于 Critical 级别必须补齐。

## 修改要求（REVISE）

按优先级排序：

1. **[CRITICAL] 补齐 `--accent-*` 变体 + TabNav.tsx（1-requirements §1.1 / tech-design §3.3.8 / §3.2.3 R13 / s7 / 4-tasks）**
   - §1.1 L36 违规表改为 "55 处跨 8 文件"，列出 10 种变体（green/orange/red/amber/blue/purple + -10/-20）；
   - §1.1 字体迁移路径表追加映射：`--accent-amber` → `text-qds-warning`（token `--warn`）；`--accent-blue` → `text-qds-info`（token `--info`）；`--accent-purple` → `text-primary`（或 planner 选定对应 token）；`--accent-*-20/-10` → 对应 dim 变体；
   - §3.3.8 映射表扩展至 10 行；
   - §3.2.3 R13 正则扩展为 `var\(--accent-(green|orange|red|amber|blue|purple)(-?(10|20))?\)`；
   - §3.2.8 `--selftest` 追加 `var(--accent-amber)` / `var(--accent-blue)` 命中断言；
   - §3.3.8 受影响文件清单补入 `TabNav.tsx`；
   - s7 描述补充 amber/blue/purple 变体 + TabNav + `--selftest` 扩展要求；
   - s7 工作量上调至 3h（原 2.5h，新增 20 处变体迁移）。

2. **[CRITICAL] 补齐 `data-catalog/types.ts` 到 s5 范围（1-requirements §1.1 / tech-design §3.3.4 / §3.9 / s5 / task.json）**
   - §1.1 L24 违规表改为 "65 处跨 6 文件"，明确列出 `types.ts` 的 12 处 `TYPE_BADGE_CLS` 字典字符串；
   - §3.3.4 追加小节 "dc-type-* 字典常量迁移策略"：将 `TYPE_BADGE_CLS: Record<string, string>` 的 value 从 `"dc-type-kl"` 改为 `"bg-qds-info-dim text-qds-info"` 等 Tailwind class 字符串；key 不变；
   - §3.9 data-catalog 行补充 `types.ts` + 改动描述；
   - s5 文件列表补入 `src/app/data-catalog/types.ts`（12 处 dc-type-* 字典）；
   - s5 验收追加：`rg 'dc-type-[a-z]+' src/web/src/app/data-catalog` 必须 0 命中；
   - s5 工作量上调至 4h（原 3.5h）。

3. **[MAJOR] 修正 bt-* 总数 253 → 276（1-requirements §1.1 / s4 / §3.9）**
   - §1.1 L23 改为 "276 处跨 6 文件"，分布 page(144)、OverviewTab(74)、PerformanceTab(28)、RobustnessTab(15)、TradesTab(9)、OverviewGreyTab(6)；
   - §3.9 backtest 行对应调整；
   - s4 描述 bt-* 计数 253 → 276；
   - s4 工作量微调至 8.5h。

4. **[MAJOR] 修正 2-research.md L19 `docs/ui/` 残留引用**
   - 将 L19 "`docs/ui/qds-*.html`（既有）" 改为 "`.claude/skills/TinoHelmDS/Web UI Kit.html` + `Charts Spec.html`"；
   - 追加一句 "Round 1 修正：页面级装配参考从 `docs/ui/qds-*.html`（已验证不存在）改为 skill 下 `Web UI Kit.html`"。

5. **[MINOR] factor-research class 总数表述对齐（§3.3.7）**
   - §3.3.7 开头 "85 个 class" 改为 "85+ 个顶层 class selector（globals.css L1853-1987 共 ~98 unique selector，扣除 SVG data URI 假命中与 `.arr/.pdot/.sub/.tr` 等父子选择器后，顶层需独立映射约 85 个）"。

6. **[MINOR] s10 preflight 失败回退路径明示（4-tasks s10）**
   - s10 描述追加："preflight 输出的违规文件所属路由决定回退 target：backtest → s4、data-catalog → s5、research → s6、trading → s7、analytics/optimization/orders/watchlist → s8、root/strategies/settings → s9、其他 → s11。每次回退后执行 `--fix-hint` 输出并再次运行 preflight。"

7. **[MINOR] s6 动态拆分门槛明示（4-tasks §子任务拆分风险）**
   - "若 s6 超过 6h 仍未完成 research/page 的 6 子组件拆分"改为硬约束："executor 在 s6 启动 4h 后如果 research/page 尚未完成 3 个子组件迁移，必须拆出 s6b 处理 ReportClient（independent），s6a 继续 research/page"。

8. **[MINOR] R4 已知限制声明（3-tech-design §3.2.8 / 4-tasks s2）**
   - §3.2.8 `--selftest` 末尾追加"已知限制：R4 不覆盖 `className={cn("cg", ...)}` 包裹形态；当前 src/web/src 下实测 0 处，若未来出现需专项扫描"。

---

ReviewPass: architect
VERDICT: REVISE
