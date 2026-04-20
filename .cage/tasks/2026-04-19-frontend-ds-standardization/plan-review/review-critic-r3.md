# Critic Review — Round 3

**VERDICT: REVISE**

## 总体评估

r3 修订对 r2 的 1 CRITICAL + 3 MAJOR + 4 MINOR + 2 缺失项**绝大部分落地到位**：§Round 2 Revision Notes 表逐条交叉验证（C-CR-1 factor-research 44 处 / types.ts / A-MA-1 bt-* 280 / R7/R8/R9 selftest + 多行伪代码 / `--popover` 核对 / CHART_LABEL_STYLE 决策 / §1.9 降语 / .btn-p 锁定 / .rpt-* 注释 / StatusBadge 视觉差异声明 / R14 新增 + preflight 扩展 / preflight 失败回退映射 + s6b 硬约束）。扫描脚本 R14 设计合理，R8 PCRE2 spread-extra-prop 豁免写法精确，R9 两阶段伪代码补齐，`--popover ≡ --bg-p` 事实核对有 globals.css 行号依据。

然而在阶段 2（现场验证）与阶段 5（缺口分析）中，**重新现场扫描发现 1 个 CRITICAL 计数错误（sc-l 实测 15 处跨 5 文件，规划仅覆盖 7 处跨 5 文件 — 漏 8 处含 OverviewGreyTab 整个文件）+ 2 MAJOR（数量一致性/purple case-by-case 歧义 / 视觉目测在 task 描述中残留）+ 2 MINOR**。其中 **C1** 是**与 r2 同类型的漏扫复发**：r2 把 factor-research 散落范围从"research/page + ReportClient 若干"扩到 5 文件 44 处，但规划仍然**只覆盖了 sc-l 的 7 处**（4 处 data-catalog + 3 处 TradesTab/PerformanceTab/TearsheetTab），**漏 OverviewGreyTab 的 4 处 + TradesTab/PerformanceTab/TearsheetTab 的"多出来"共 4 处**。

## 预判 vs 实际

- **预判 1**（r2 修改逐项兑现）→ **绝大多数命中**：R14 / preflight-R14 / R6/R7/R8/R9 selftest 扩充 / `--popover ≡ --bg-p` 核对 / CHART_LABEL_STYLE 删 fontFamily / §1.9 降语 / .btn-p 锁 accent / .rpt-* 注释 / StatusBadge 视觉差异声明 + fallback 策略 / 10 variant R13 白名单 / types.ts 纳入 s5 / 2-research.md docs/ui 残留修复 / JobQueue 4 处 bt-status 预留 s11 / s6b 4h 硬约束 / preflight 失败回退 target 映射表 — **均已落地且写法精确**。
- **预判 2**（新 AC R14 可测性）→ **命中**：R14 有正反例 9 条（sc/sc-l/cd/ctbl/fsel/模板字符串/verdict-pass/turn-val/rpt-title 均正例命中；bg-card/font-sans/sc-column 反例不命中），**边界严格可自动化**。唯一限制（cn() 包裹形态）在 §3.2.8 末尾声明已知限制（与 R4 一致）。
- **预判 3**（post-task memory / popover 核对结论 / rounded 视觉变化）→ **命中**：memory 更新明确为主 agent 责任不占 subtask 槽位（合理决策）；popover 核对给出具体 L81/L153 行号和结论"严格等价"；rounded-md → rounded-full 差异已在 §3.3.9 + s11 + 风险表 R-9 三处声明且给 fallback。
- **预判 4**（新缺口）→ **严重命中**：sc-l 实测 15 处而非规划 7 处（C1）；`CHART_LABEL_STYLE` fontSize 统一 10 引入 4 处中 3 处的 +1px 变化，但 OverviewTab:684 的 `position: "insideTopLeft"` 是位置 prop 不是 style，spread 时需注意（m1）。
- **预判 5**（歧义扫描）→ **轻度命中**：purple 行 "或按语义 case-by-case 评估" 与"**默认 `text-primary`**"并存矛盾（m2）；"视觉差异目测" 在 s11 task description 中作为步骤（非 AC），与用户 MUST 规则 "验证或测试的内容不应出现手动验证" 存在边界问题（M2）。
- **预判 6**（验收自动化）→ **大多命中**：R1-R14 均有自动化扫描判据；`rg -c` / `wc -l` / `npm run build` 等均可程序化。唯一半自动项是 s11 "视觉差异目测 + fallback 决策权" —— 非 AC 但是任务步骤。

## Critical 发现（阻塞执行）

### C1 · `sc-l` 实测散落 15 处跨 5 文件，规划只覆盖 7 处（OverviewGreyTab 4 处 + TradesTab 1 + PerformanceTab 1 + TearsheetTab 1 漏计）

- **证据**（现场精确扫描 @ 2026-04-19，命令：`rg 'className="(?:[^"]*\b)?(sc-l)\b[^"]*"' src/web --glob='*.tsx'`）：
  | 文件 | 实测 sc-l 处数 | 规划 s4/s5 覆盖 | 漏计 |
  |---|---:|---:|---:|
  | `src/web/src/app/backtest/components/OverviewGreyTab.tsx` | **4**（L84, L134, L220, L458） | **0** | **4** |
  | `src/web/src/app/backtest/components/TradesTab.tsx` | **3**（L162, L179, L515） | 2（L179, L515） | **1**（L162） |
  | `src/web/src/app/backtest/components/PerformanceTab.tsx` | **2**（L226, L1726） | 1（L1726） | **1**（L226） |
  | `src/web/src/app/backtest/components/TearsheetTab.tsx` | **2**（L48, L90） | 1（L48） | **1**（L90） |
  | `src/web/src/app/data-catalog/page.tsx` | **4**（L240, L241, L242, L243）| 4 | 0 |
  | **合计** | **15** | **8** | **7** |
- **与规划的冲突**：
  - `3-tech-design.md §3.9` "backtest/components/TradesTab.tsx — bt-* 9 处；Round 2 新增 .sc-l **2** 处迁移" / "PerformanceTab.tsx — bt-* 28 处；Round 2 新增 .sc-l **1** 处迁移" / "TearsheetTab.tsx — .sc-l **1** 处迁移（L48）"
  - `3-tech-design.md §3.3.7` 开头"Round 2 新发现散落位置"表写 TradesTab 2 / PerformanceTab 1 / TearsheetTab 1；**OverviewGreyTab 完全未列入**；行号也只列 L179/515 / L1726 / L48。
  - `4-tasks.md s4` 描述"共 4 处 .sc-l 迁移" — 与实测 11 处 backtest sc-l 相差 **+7 处漏报**。
  - `1-requirements.md §1.1` 违规表 factor-research 行列出 "TradesTab(2) + PerformanceTab(1) + TearsheetTab(1)" — **同样漏 OverviewGreyTab 4 处 + 每文件 1 处**。
- **影响（现实最坏情况）**：
  1. **s4 执行者严格按任务描述行事时会漏迁 7 处 sc-l**：执行"4 处 sc-l 散落清理"完成后，`rg -n '\bsc-l\b' src/web/src/app/backtest/components` 仍会返回 7 处（4 OverviewGreyTab + 1 TradesTab L162 + 1 PerformanceTab L226 + 1 TearsheetTab L90）。
  2. **R14 扫描会捕获**：s4 验收标准 `rg -n '\bsc-l\b' src/web/src/app/backtest/components` 命中 0 行将**失败** — 因为 R14（§3.2.3）正则 `\b(sc|cd|sl|...|sc-l|...)\b` 会命中全部 11 处。
  3. s10 `--preflight-before-css-delete` 运行时会 exit 1，迫使回退到 s4 补刀；此时 executor 必须从 preflight 输出自己补齐漏掉的 7 处 — 规划层失败但扫描层兜住，**执行层浪费一轮 kickback**。
  4. 与 C1 r2 同样的缺陷：本轮修订只精确到"critic r2 列出的行号"，没有做独立全文件扫描验证。
- **置信度**：HIGH（rg 扫描 + 行号逐一 Read 双重验证）
- **修复**（必改）：
  1. `1-requirements.md §1.1` 违规表 factor-research 行改为精确计数："**15 处 sc-l 跨 5 文件**（research/page 0 / OverviewGreyTab 4 / PerformanceTab 2 / TearsheetTab 2 / TradesTab 3 / data-catalog/page 4）" 或以实例口径"**15 处 sc-l + 8 处 sc/sc-v/sc-sub + 4 处 fsel + 64 处 research/page 家族散落 = 91 实例跨 6 文件**"（以现场 rg 为准，而非 r2 critic 列出的数字）；
  2. `3-tech-design.md §3.3.7` "Round 2 新发现散落位置"表修正：
     | 文件 | sc-l 处数 | 具体行号 |
     |---|---:|---|
     | `backtest/components/OverviewGreyTab.tsx` | **4** | L84, L134, L220, L458 |
     | `backtest/components/TradesTab.tsx` | **3** | L162, L179, L515 |
     | `backtest/components/PerformanceTab.tsx` | **2** | L226, L1726 |
     | `backtest/components/TearsheetTab.tsx` | **2** | L48, L90 |
     | `data-catalog/page.tsx` | **4** | L240-243 |
     | **合计** | **15** | — |
  3. `3-tech-design.md §3.9` 影响文件清单补充：
     - `backtest/components/OverviewGreyTab.tsx` — **新增改动行**："Round 2 新发现：.sc-l 4 处迁移为 `<SectionLabel>` 或 Tailwind"（原 §3.9 只写"bt-* 6 处"）；
     - TradesTab/PerformanceTab/TearsheetTab 的 sc-l 数字分别修正为 3 / 2 / 2。
  4. `4-tasks.md s4` 描述修正：
     - "共 4 处 .sc-l 迁移" → "共 **11** 处 .sc-l 迁移：OverviewGreyTab L84/134/220/458 + PerformanceTab L226/1726 + TearsheetTab L48/90 + TradesTab L162/179/515"；
     - 工作量从 9h 上调至约 **9.5h**（+0.5h 应对 7 处额外迁移）；
     - 验收标准已有 `rg -n '\bsc-l\b' src/web/src/app/backtest/components` 命中 0 行的断言 — 保留，但任务描述必须与之一致。
  5. （可选强化）R14 selftest 补一条"rgp 针对 OverviewGreyTab 的 4 处 sc-l 必须全部命中"的正例断言（但 selftest 应保持通用，不强制）。
  6. **根因修复**：`.cage` 规划流程建议在"影响文件清单 + 任务描述计数"填写前强制运行一次**全仓独立扫描**（不依赖上轮审查列出的数字），把 rg 输出直接贴入规划；本次是重复相同类型错误第二次。

## Major 发现（导致显著返工）

### M1 · "视觉差异目测" 在 s11 任务描述 + §3.3.9 + 风险表 R-9 中残留为**执行步骤**，与用户 MUST 规则边界冲突

- **证据**：
  - `4-tasks.md s11 Step 11b` L415：`**Round 2 视觉差异目测（§3.3.9）**：barrel re-export 后 legacy 调用点外观从 shadcn Badge 的 rounded-md 变为 QDS span 的 rounded-full；目测清单：backtest/page / optimization/page / data-catalog/JobQueue / research 历史 Job 行；若差异过大（影响 UX 辨识度）则切换 fallback：...（executor 持决策权）`
  - `3-tech-design.md §3.3.9` L763：`**s11 执行后必须逐页目测对比**`
  - `3-tech-design.md §3.8` R-9 风险表：`视觉差异由 s11 逐页目测决定 barrel vs fallback 方案`
  - `4-tasks.md s11 预计工作量说明`：`新增 JobQueue 4 处 bt-status 迁移 + **视觉差异目测** + fallback 决策权`
- **用户规则**：`/Users/ouzhuohao/.claude/CLAUDE.md` L2：`MUST 在提交 PR 或者 issue 时，验证或测试的内容不应该出现需手动(manual)验证相关的 item`
- **歧义分析**：
  - AC-2（§1.6）已明确声明"不依赖人工肉眼判断" + fallback 到 preview 对照矩阵 — **AC 层合规**。
  - 但 s11 task description 仍然嵌入了"逐页目测对比" + "差异过大（主观阈值）→ 切换 fallback（decision）" — 这是**验证/决策步骤**（虽非 AC），在 executor 执行时本身就是"手动验证相关 item"。
  - 实际上 §3.3.9 的 fallback 决策需要一个 "明确客观差异标准" 或 "改为自动化方案"（如 DOM 快照对比）才能完全脱离 manual verification。
- **影响**：
  1. s11 executor 执行 Step 11b 时需要人工打开 4+ 页面逐一对比 rounded-md vs rounded-full 的视觉差异 — 这是**确定性发生的手动步骤**；
  2. 若用户严格按 MUST 规则审核 PR 描述（s11 的工作量说明、§3.3.9 视觉差异声明、R-9 风险应对），会被判违规；
  3. 若用户放宽到"仅 AC 不含手动"，则本项合规（AC-2 的自动化声明已兜住）。
  4. 规划层需要明确：**将"视觉差异判断"转为自动化方案**（如 Playwright DOM 截图 diff 或 CSS 属性断言 `getComputedStyle(badge).borderRadius === "9999px"`），**或明确声明"差异接受度由用户在 PR review 时判断，不作为 s11 交付物的一部分"**。
- **置信度**：HIGH（规则原文 + 规划原文均可引）
- **修复**（必改）：
  1. **方案 A（推荐）**：`§3.3.9` + `4-tasks.md s11` 的"逐页目测对比"改为**自动化 DOM 断言**：新增一个小脚本 `src/web/scripts/check-statusbadge-visual.mjs`（或在 s11 内部提供 Playwright 脚本），对每个调用点断言 `StatusBadge` 的 `getComputedStyle().borderRadius === "9999px" && padding === "..."`；判据客观可自动 0/1 判定；
  2. **方案 B**（最小改动）：`§3.3.9` 末尾追加："**本任务 s11 的 fallback 决策不属于自动化验证范畴** — s11 的**交付物**仅为 (a) StatusBadge 扩展 + barrel re-export 代码，(b) `npm run build` 通过，(c) R2/R11/R14 扫描通过。barrel vs fallback 的选择由主 agent 在 PR review 时基于实际视觉效果决定 — **不在 s11 的 acceptance_criteria 中**。"
     - 同时 `4-tasks.md s11` 任务描述移除"视觉差异目测"作为步骤，改为"若 barrel 切换引发视觉争议，由主 agent 决定是否走 fallback（不阻塞 s11 完成）"；
  3. 删除或替代 `3-tech-design.md §3.3.9` L763 的"**必须逐页目测对比**"（必须 = 强制验证步骤 = 违规）。

### M2 · `var(--accent-purple)` 迁移决策同时声明"**默认 text-primary**"和"case-by-case 评估"，executor 决策歧义

- **证据**：
  - `3-tech-design.md §3.3.8` 未定义 CSS 变量表第 8 行：`var(--accent-purple) | 1 | text-primary（复用 accent 橙 — 项目无 purple token；或按语义 case-by-case 评估是否用 --acc / --info / 专属新 token。**默认 text-primary**）`
  - `1-requirements.md §1.1` 字体迁移路径表：`var(--accent-purple) 未定义 | text-primary（项目无 purple token；默认复用 accent 橙；case-by-case 评估）`
  - `4-tasks.md s7 预计工作量` L297：`40 处 --accent-* 含 10 variant 映射决策 + TabNav.tsx 新纳入 + **部分 purple 需 case-by-case 评估**`
- **歧义**：
  - 解读 A：全部 2 处 purple（FillsStream 的 `--accent-purple` + 1 处 `--accent-purple-20`）按默认映射到 `text-primary` / `bg-qds-accent-dim`；
  - 解读 B：executor 在 s7 运行时对每处做 case-by-case 评估（可能映射到 `--info` / `--acc` / 甚至创建新 token）。
- **影响**：
  1. s7 executor 面对 purple 时需主观判断：FillsStream 里 purple 的语义是啥？（未在规划中定义业务语义）→ 可能选 primary / info / warning 任一；
  2. 不同 executor 在 kickback 场景给出不同结论，无法收敛；
  3. 与 r2 时 `.btn-p` 两选项问题同类型 — r2 已 lock 决策（全改 accent 橙），purple 未 lock。
- **置信度**：HIGH
- **修复**：
  1. `§3.3.8` purple 行删除"case-by-case 评估"分支，锁定为"**`text-primary`（项目无 purple token，本任务不新增 token，purple 语义并入 accent 橙）**"；对应 `--accent-purple-20` 锁定为"`bg-qds-accent-dim`"；
  2. `§1.1` 字体迁移路径表同步（已有"默认复用 accent 橙"— 删"case-by-case 评估"修饰语）；
  3. `4-tasks.md s7 预计工作量`说明"case-by-case 评估"改为"按 §3.3.8 固定映射"。

## Minor 发现（次优但可工作）

### m1 · `CHART_LABEL_STYLE` 统一 fontSize=10 对 OverviewTab:684 的 `position: "insideTopLeft"` spread 行为需明示

- **证据**：`3-tech-design.md §3.3.6` 决策 `CHART_LABEL_STYLE = { fontSize: 10, fill: "var(--t2)" }`；OverviewTab:684 现状 `label={{ value: "本金...", fill: "var(--warn)", fontSize: 10, position: "insideTopLeft" }}`；迁移写法 `label={{ ...CHART_LABEL_STYLE, value: "...", fill: "var(--warn)", position: "insideTopLeft" }}`。`position` 不是 CSSProperties 字段，是 Recharts label 额外 prop。
- **影响**：Recharts 会将 spread 后的 object 直接传给内部 `<Label>`。`position` 与 CSSProperties 字段混在一起可能使 TS 类型报错（因为 `React.CSSProperties` 不含 `position: "insideTopLeft"`；`React.CSSProperties.position` 类型是 `Property.Position` 如 "absolute"/"relative"）。实测 OverviewTab:684 现状可编译，说明 Recharts 接受 CSSProperties | 扩展字段的宽容类型 — 但 spread 后的对象字面量 TS 类型推断需要验证。
- **修复**：§3.3.5 / §3.3.6 补一句："`CHART_LABEL_STYLE` 的 TypeScript 类型为 `React.CSSProperties`，但 Recharts `ReferenceLine.label` prop 接受 `React.CSSProperties & { value?: ReactNode; position?: ...; offset?: ... }` 扩展形态；spread 后覆盖 fill/position/value/fontSize 均合法，无需 type assertion。若 s4 OverviewTab:684 迁移后 TS 报错，可用 `{ ...CHART_LABEL_STYLE, value: "...", fill: "var(--warn)", position: "insideTopLeft" } as any` 或扩大 chartTheme 类型定义。"

### m2 · §3.3.8 表列出 11 行（含 purple + green-10 + blue-20 + amber-20 + purple-20），但表尾计数"55 处跨 8 文件 / 10 variant"前后数字差异（10 vs 11）

- **证据**：`3-tech-design.md §3.3.8` 表头 "Round 2 实测：全仓 var(--accent-\*) 共 55 处跨 8 文件 / **10 variant**"；实际表格 11 行（green 23 / red 13 / amber 12 / blue 7 / orange 4 / red-20 2 / green-10 2 / purple 1 / amber-20 1 / blue-20 1 / purple-20 1）= 11 个 variant。23+13+12+7+4+2+2+1+1+1+1 = **67**，不是 55。55 应该是 55 - 12 内部 shadcn token? 实测 67 包含 `--accent-foreground` 的 1 处吗？重新核对：`rg -o 'var\(--accent-[a-z0-9-]+\)'` 实测返回 55（无 foreground）+ 1（有 foreground）= 56。23+13+12+7+4+2+2+1+1+1+1=67 与 55 差 12 — 无法解释。
- **影响**：表头数字与表体求和不一致，readers 计数混乱；executor 按表体 11 行迁移不会漏；但"10 variant"与"11 rows"矛盾可能误导 selftest 覆盖度判断（现 selftest 测 11 variant 命中，符合表体）。
- **修复**：重跑 `rg -o 'var\(--accent-[a-z0-9-]+\)' src/web/src --glob='*.tsx' --glob='*.ts' | sort | uniq -c` 确认实际分布，把 §3.3.8 / §1.1 / s7 的数字统一为真实值（建议用 `rg` 输出直接粘贴而非手动求和）。

## 缺失项

1. **s4 task description 与 §3.9 的 OverviewGreyTab 改动说明不同步**：§3.9 L979 "OverviewGreyTab.tsx | 改 | bt-* 6 处" 未提 sc-l 4 处迁移；s4 L148-149 任务描述只提 OverviewGreyTab "评估是否合并到 OverviewTab" 和 "否则独立拆"，未提 sc-l。若 C1 修复执行，两处都需追加。
2. **R14 selftest 对 OverviewGreyTab 特定多行/父子选择器场景覆盖度未验证**：例如 `className="sc-l inline-flex items-center"` 的"sc-l 后跟 inline-flex"形态是否稳妥命中 R14 正则（§3.2.3 R14 的 PCRE2 模式 `\b(...)\b` 应能命中；已有 selftest 正例 `className="sc-l"` 与 `className={\`sc-v ${stale.cls}\`}` 但无"sc-l + 其它 Tailwind class"混用形态）。建议补一条 selftest："正例 R14 `className='sc-l inline-flex items-center'` 必须命中"。
3. **CHART_LABEL_STYLE 无 fontFamily 后，chartTheme.ts 的 chartGridStyle 常量并未附带 fontFamily — 这是正确的**（R1 扫描只限业务 tsx；chartTheme 作为常量层一直允许）；但**CHART_LABEL_STYLE 常量导出时是否需要 type-only import `React.CSSProperties`** —— §3.3.6 代码块里写 `export const CHART_LABEL_STYLE: React.CSSProperties = ...`，但 chartTheme.ts 现状导入方式如何？若文件顶部已有 `import type { CSSProperties } from "react"`，则类型引用可能需改为 `: CSSProperties`。executor 可能错写 `: React.CSSProperties` 导致 TypeScript `verbatimModuleSyntax` 报错（Next.js 16 默认开启）。建议 §3.3.6 明示使用 chartTheme.ts 既有的 import 风格。
4. **"Round 2 sc-l 计数"与 Round 2 Revision Notes 落地状态表 C-CR-1 行声称"已修复 — 44 处跨 5 文件"不一致于当前新一轮扫描（15 sc-l + 8 sc家族 + 3 FetchDialog fsel + 1 page fsel + 64 research ≈ 91 实例；或按行号聚合口径 ≈ 44）**。口径混乱在 §3.3.7 的"注"里有声明（"实例口径 vs 逻辑位置口径"），但规划对 **s4 的工作量口径**用的是"逻辑位置口径"（4 处 sc-l），而 R14 扫描用的是"实例口径"（15 处）—— 两者错位导致 s4 验收会比 task description 严格。
5. **task.json 未把 C1 新增的工作量反映到 s4 depends_on 或 parallel_groups**：s4 工作量若从 9h 上调到 9.5-10h，可能逼近 s6 的 10h 关键路径；若 s4 与 s6 并发且 s4 ≥ 10h，s6 不再是唯一关键路径。但这是观察性问题，不影响正确性。

## 歧义风险

| 文档原文 | 解读 A | 解读 B | 选错后果 |
|---|---|---|---|
| `3-tech-design.md §3.3.8` purple 行"或 case-by-case 评估" | 固定映射 `text-primary` | executor 每处单独评估 | 不同 executor 结论不一致，R13 验收风险 |
| `4-tasks.md s11` "视觉差异目测 + fallback 决策权" | executor 自动化判断 | executor 手动打开浏览器目测 | 违反用户 MUST 规则（解读 B） |
| `3-tech-design.md §3.3.7` "Round 2 新发现散落位置" 表（TradesTab 2 / PerformanceTab 1 / TearsheetTab 1） | 规划层定案 | 以 R14 实际扫描输出为准 | 解读 A 下 s4 执行者漏改 7 处 sc-l |
| `3-tech-design.md §3.9` "data-catalog/page — L240-243 的 4 张 KPI 行" | L240-243 仅 4 处（已精确） | 隐含 L252 fsel 是 s5 另一工作（已精确） | 无歧义（精确列出） |
| `3-tech-design.md §3.3.9` "若差异过大" | 量化阈值（未提供） | 主观判断 | 主观导致 executor 决策不收敛 |

## 假设分析

| 假设 | 级别 | 说明 |
|---|---|---|
| r2 critic 列出的散落计数（44 跨 5 文件）正确 | **FRAGILE** | 现场 rg 扫描发现低报 — sc-l 单项实测 15 处 vs 列出 4 处；需独立验证 |
| R14 regex 能命中 "sc-l inline-flex items-center" 等混合 class | REASONABLE | PCRE2 `\b(sc-l)\b` 对单词边界正确；but selftest 未显式覆盖混用形态 |
| `--popover ≡ --bg-p` 在 dark 与 light 下等价 | VERIFIED | globals.css L81-L84 / L151-L154 实测确认 |
| `CHART_LABEL_STYLE` spread 后 TS 类型接受 `position: "insideTopLeft"` | REASONABLE | Recharts label prop 类型宽松，但规划应明示以避免执行 TS 报错 |
| OverviewGreyTab 与 OverviewTab 合并判定（s4 FR-4.1） | REASONABLE | executor 有决策权；若合并则 OverviewGreyTab 的 4 处 sc-l 自然进入 OverviewTab 清理范围；若独立拆则单独处理 |
| s11 视觉差异"目测"可转为自动化 | REASONABLE | Playwright + `getComputedStyle` 可 0/1 判定；但规划未给出此方案 |
| task.json `parallel_groups` 固定，s6 动态拆 s6b 能在运行时并发 | REASONABLE | 4-tasks.md 已声明"其它任务不依赖；可再启动一个 agent 并行跑 s6b"；但 parallel_groups 不更新 — cage 框架是否支持动态拆任务？未验证（属 cage 框架能力范畴，不在本规划职责） |

## 预验尸

| 失败场景 | 文档是否应对 | 说明 |
|---|---|---|
| 1. s4 按描述迁移 4 处 sc-l（TradesTab 2 + PerformanceTab 1 + TearsheetTab 1），R14 扫描捕获剩余 7 处违规 | **No** | C1 — 验收失败，需回到 s4 补 7 处 |
| 2. s4 OverviewGreyTab 被合并到 OverviewTab（executor 决策），4 处 sc-l 进入 OverviewTab 迁移；但任务描述未说 OverviewTab 含 sc-l | **No** | C1 衍生 — OverviewTab 迁移计数也会低报 |
| 3. s7 executor 对 FillsStream 的 `--accent-purple` 选择 `text-qds-warning` 而非 `text-primary`；另一 executor 选 `text-qds-info` | **Partial** | M2 — §3.3.8 同时写"默认 text-primary" + "case-by-case"，导致策略分歧 |
| 4. s11 executor 逐页人工打开 dev server 目测 rounded-md vs rounded-full，用户在 PR review 时引用 MUST 规则判定违规 | **No** | M1 — 规则边界问题 |
| 5. s4 OverviewTab:684 ReferenceLine label spread CHART_LABEL_STYLE 后 TS 报错（position 字段类型） | **No** | m1 — 类型兼容性未明示 |
| 6. s10 preflight 命中 OverviewGreyTab 的 4 处 sc-l，按回退 target 映射回 s4；s4 已标注完成 → cage 框架如何重开？ | Partial | s10 的"回退 target 映射"表已建立，但 cage 框架上"已完成任务重开"的机制未在 4-tasks.md 说明 |
| 7. §3.3.8 表头 "10 variant" 与表体 11 行差 1，executor 依赖表头数字做 selftest 断言 | **No** | m2 — 数字自相矛盾，selftest 会因此写错一条 |
| 8. s4 按规划完成后工作量突破 s6 关键路径（10h→10.5h），波次 B 总时长变化 | **Partial** | task.json 未更新工作量估算；不影响正确性但影响调度 |
| 9. `CHART_LABEL_STYLE: React.CSSProperties` 在 chartTheme.ts 已有 `import type` 形式下报错 | **No** | 缺失 3 — verbatimModuleSyntax 未明示 |

## 多视角笔记

### Executor 视角
- **卡点 1**：s4 任务描述只列 4 处 sc-l，但 R14 扫描验收会找到 11 处（backtest 下） — executor 执行完 4 处后 `verify-ds-compliance.sh` exit 1，需返回补 7 处。规划层与扫描层不一致导致一次 kickback。
- **卡点 2**：§3.3.8 purple 行决策不明（默认 vs case-by-case），需要 planner 决断。
- **卡点 3**：s11 "视觉差异目测" 步骤 — executor 需打开 dev server 人工对比，或需要 planner 提供自动化方案。
- **卡点 4**：s4 OverviewTab:684 ReferenceLine label 迁移后 TypeScript 可能报错（position 字段） — 规划未预警。
- **卡点 5**：s10 preflight 失败回退到 s4 时，s4 已完成状态如何重开？cage 框架层问题，但规划层应给出步骤指引。

### Stakeholder 视角
- **真正问题**：r3 修订比 r2 更扎实（R14 / preflight / types.ts / 10 variant / StatusBadge fallback / .btn-p 锁定均到位），但 r2 critic 的 C1 修复仅"部分"兑现 — 计数精度仍差 7 处。这是规划流程中"以上轮审查数据为准"的系统性问题。
- **范围恰当性**：12 任务 × 4 波次 DAG 稳健；新约束（R14 + preflight + s6b + 回退映射）都在合理范围内扩充 — 整体质量上升。若 C1 修复（sc-l 11 处而非 4 处），s4 工作量 9→9.5h 仍可控。
- **虚荣指标**：NFR-2 "globals.css 1210 ± 50 行" 仍是过程指标；真正关键指标是 R1-R14 扫描 0 违规 + `npm run build` 通过 + s11 StatusBadge 无 TS 报错，这三项已被 AC 覆盖。

### Skeptic 视角
- **最强反对论点**：第三次审查依然发现"同类型计数漏报"（r1 C1 factor-research 范围，r2 C1 factor-research 散落 5 文件，r3 C1 sc-l 漏 OverviewGreyTab 4 处）。说明规划流程缺一个强制步骤：**"以全仓 rg 输出为准，planner 不得手抄 critic 的数字"**。建议在下轮修订前 planner 先跑一次独立 `rg` 并把原始输出贴入 §1.1 / §3.3.7，然后再写任务描述。
- **备选方案**：把 "factor-research 散落清理" 从 s4/s5 抽出为独立 s6c 任务（依赖 s6），避免边界不清；但会引入新依赖边，代价 > 收益。保持现方案 + C1 修复即可。
- **已知限制**：R4 / R14 对 `cn("sc-l", ...)` 形态不覆盖（已声明）— 当前项目不用 cn 包裹遗留 class，实测 0 命中，不构成风险。

## 上轮修改验证（逐条核对 r2 的 10 个修改要求）

| 上轮要求（Critic r2） | 是否解决 | 说明 |
|---|---|---|
| **1 CRITICAL C1** factor-research 散落（§1.1 / §3.9 / s4 / s5 / R14 / preflight） | **Partial** | ✓ §1.1 追加独立行 + §3.9 4 行补充 + s4/s5 描述追加 + R14 + preflight — 结构到位；❌ sc-l 实测 15 处但规划仅覆盖 8 处，OverviewGreyTab 4 处 + 3 文件各 +1 处漏 — 计数精度不够（见本轮 C1） |
| **2 MAJOR M1** bt-* 计数 + JobQueue 4 处 | **Yes** | §1.1 / s4 / s5 / §3.9 / §3.3.3 / s11 均已修正至 276 / 4 / 280 — 实测 rg 完全匹配 |
| **3 MAJOR M2** R7/R8 selftest + R6 多行 | **Yes** | §3.2.8 扩充 R7（4 条正反例） / R8（5 条 spread-extra-prop 豁免形态） / R6_MULTILINE + R9_MULTILINE 正反例；R8 PCRE2 `\{(?!\s*CHART_LEGEND_STYLE\b)(?!\{?\s*\.\.\.CHART_LEGEND_STYLE\b)` 实际断言写法精确 |
| **4 MAJOR M3** R9 `-U --multiline-dotall` + 多行样例 | **Yes** | §3.2.3 R9 "**必须** `-U --multiline-dotall`" + §3.2.4 R9 两阶段 Python 伪代码 + §3.2.8 R9_MULTILINE assert — 完整 |
| **5 MINOR m1** §1.9 降语 | **Yes** | "interview.md 第 4 轮选择隐含此方向；执行完成后由主 agent 负责向用户确认并更新 memory 文件" — 表述客观 |
| **6 MINOR m2** .btn-p 决策定锚 | **Yes** | §3.3.7.4 锁定 `variant="default"` accent 橙（单一选项） + 说明 research 页"启动分析"按钮迁移后由绿色变橙（预期内视觉变化） — 决策明确 |
| **7 MINOR m3** .rpt-* 调用点 0 + ReportHeader 移除 | **Yes** | §3.3.7.9 行追加"调用点 0" + §3.5.5 移除 ReportHeader + s6 任务描述明确 "Round 2：ReportClient 移除 ReportHeader，因 .rpt-* 调用点 0" |
| **8 MINOR m4** CHART_LABEL_STYLE 字体决策 | **Yes** | §3.3.6 方案 (a)：删 fontFamily + 统一 fontSize=10；视觉变化预期表（4 处 label 每处的 before/after）— 完整 |
| **9 缺失 5** --popover vs --bg-p 核对 | **Yes** | §3.3.5 + 脚注 A 代码块 + §3.3.6 视觉变化预期 — 核对结论"严格等价"有 globals.css 行号依据 |
| **10 缺失 6** StatusBadge barrel 视觉差异 | **Partial** | §3.3.9 + s11 + §3.8 R-9 三处声明差异 + fallback 方案；但"逐页目测"写入 task description 引入 M1 问题 |

上轮要求 10 项中 **8 完全解决 + 2 部分解决**（C1 / 缺失 6）。C1 是计数精度问题（散落覆盖不全），缺失 6 是决策方法问题（目测步骤残留）。

## 修改要求（REVISE）

按优先级排列。CRITICAL / MAJOR 为 APPROVE 前置必改项。

1. **[CRITICAL C1] 补齐 sc-l 实测 15 处（OverviewGreyTab 4 + TradesTab 3 + PerformanceTab 2 + TearsheetTab 2 + data-catalog/page 4）**
   - `1-requirements.md §1.1` factor-research 违规行计数刷新：sc-l 项精确到 "15 处跨 5 文件"；
   - `3-tech-design.md §3.3.7` "Round 2 新发现散落位置"表精确到每文件的实测行号（见本文 C1 修复表）；
   - `3-tech-design.md §3.9` 补充 `OverviewGreyTab.tsx` 的 sc-l 4 处迁移行（原只列 bt-* 6）；修正 TradesTab 3 / PerformanceTab 2 / TearsheetTab 2；
   - `4-tasks.md s4` 描述："4 处 sc-l" 改为 "**11 处 sc-l 散落清理**" + 列出具体 11 个行号；工作量 9h → 9.5h；
   - `4-tasks.md s4` 验收标准 `rg -n '\bsc-l\b' src/web/src/app/backtest/components` 命中 0 行保留（已有，与修改后的任务描述一致）；
   - （可选）`task.json` 的 s4 title 补充"11 处 sc-l"字样。

2. **[MAJOR M1] s11 视觉差异目测步骤转为自动化或明确声明非 AC**
   - 两方案择一：
     - **方案 A（推荐）**：新增脚本 `src/web/scripts/check-statusbadge-visual.mjs`（或 Playwright 步骤），自动断言 StatusBadge 的 CSS 属性（`borderRadius`, `padding`, `fontSize`）等于预期值；s11 Step 11c 新增"运行此脚本 exit 0"；`§3.3.9` + §3.8 R-9 相应改为自动化描述；
     - **方案 B（最小改动）**：`§3.3.9` 明确声明"s11 的 acceptance_criteria 不包含视觉差异判断，目测 + fallback 由主 agent 在 PR review 阶段决定，不阻塞 s11"；`4-tasks.md s11 Step 11b` 删除"视觉差异目测"作为步骤；预计工作量说明删除"视觉差异目测 + fallback 决策权"。
   - 无论哪种方案，s11 的 `acceptance_criteria`（自动化部分）保持不含 manual item。

3. **[MAJOR M2] --accent-purple 决策锁定**
   - `§3.3.8` purple 行删除"或按语义 case-by-case 评估"分支，单一决策："`text-primary`（项目无 purple token，本任务不新增；purple 语义并入 accent 橙）"；
   - `§1.1` 字体迁移路径表同步；
   - `4-tasks.md s7 预计工作量`说明"部分 purple 需 case-by-case 评估"改为"按 §3.3.8 固定映射（purple → text-primary）"。

4. **[MINOR m1] `CHART_LABEL_STYLE` spread 后 TypeScript 兼容性说明**
   - `§3.3.5` 或 `§3.3.6` 追加注释："`label={{ ...CHART_LABEL_STYLE, value, position, offset }}` 形态 Recharts 接受 CSSProperties + 扩展字段；若 TS 报错可扩大 chartTheme.ts 类型定义为 `React.CSSProperties & { value?: React.ReactNode; position?: string; offset?: number }` 或使用局部 `as any`。"

5. **[MINOR m2] §3.3.8 "10 variant" vs "11 rows" 数字一致性**
   - `§3.3.8` 表头改为 "11 variant"（与表体一致），或表体合并 purple 与 purple-20 等为 8-9 variant；
   - 以重新跑一次 `rg -o 'var\(--accent-[a-z0-9-]+\)' src/web/src` 输出为准。

6. **[缺失 3] chartTheme.ts import 风格说明（可选）**
   - s1 任务描述或 §3.3.6 加注："导出类型沿用 chartTheme.ts 既有 import 风格（若已有 `import type { CSSProperties } from 'react'` 则用 `: CSSProperties`）"。

## 判决理由

VERDICT: **REVISE**。

r2 → r3 改动量**大且精准**：Round 2 Revision Notes 表 10 条中 8 条完全兑现 + 2 条 partial。新扩展（R14 + preflight R14 + s6b 4h 硬约束 + 回退 target 映射 + --popover 核对 + StatusBadge 视觉差异 fallback）设计合理、实测可执行、自动化覆盖充分。但：

- **1 CRITICAL**（sc-l 计数精度仍差 7 处 — 同类型缺口第三次出现）— 会使 s4 验收失败一次，executor 返工成本 30-60min，**规划层必须修正**；
- **2 MAJOR**（视觉目测在 task description 残留违反用户 MUST 规则 / purple decision case-by-case 歧义）— 前者是合规问题，后者是执行歧义；
- **2 MINOR** 可并发修正。

**现实检查**（阶段 7）：
- **C1 现实最坏情况**：s4 executor 按描述做完 4 处 sc-l，R14 扫描捕获 7 处违规，回退补改 — 追加工作约 30min，可回滚性好。**保持 CRITICAL 不降级**：因为这是 r1-r3 三轮同类型缺陷，规划层必须建立"全仓 rg 输出为准"的强制流程，否则下轮可能再次出现。
- **M1 现实最坏情况**：用户看到 s11 的 "视觉差异目测" 步骤，援引 MUST 规则判定违规；或 executor 按目测执行，完成后主 agent 认为视觉不 OK 要求回滚 — 返工 1-2h。**保持 MAJOR**：因为这是用户明确规则的边界问题，不修复可能阻塞 PR 合并。
- **M2 现实最坏情况**：不同 executor 对 purple 的 2 处调用给出不同映射，kickback 修复 15min。**可降级为 MINOR**，但修复成本低（删 5 字），保持 MAJOR 以触发修复。

**未升级 ADVERSARIAL**：r3 整体质量远高于 r1/r2；问题集中且清晰，不需假设更多隐藏。三轮下来系统性问题明显收敛（无新虚构引用、无新伪造文件、无新 API 错配）。

**建议流程改进**（供主 agent / planner 考虑，不属本轮修改要求）：下轮修订前 planner **先跑一次独立 rg 全仓扫描** 把原始输出贴入 §1.1 违规表，再写任务描述；避免"以上轮 critic 数字为准"的系统性传染。

## Open Questions（未评分）

- `task.json` 的 `parallel_groups` 在 s6 动态拆 s6b 时如何更新？cage 框架是否支持运行时动态拆任务（观察性问题，属 cage 框架能力）？
- s10 preflight 失败回退映射后，若 s4 已被 cage 框架标记为"完成状态"，如何重开 s4（并触发新的 verify）？需主 agent 在 exec skill 中确认。
- OverviewGreyTab 与 OverviewTab 合并决策（FR-4.1 给 executor 权）— 若合并，sc-l 4 处进入 OverviewTab 清单；若独立拆，保留在 OverviewGreyTab — 两种路径下 s10 preflight 都能兜住，但任务文档是否需要为两条路径分别给出预期行数？

ReviewPass: critic
VERDICT: REVISE
