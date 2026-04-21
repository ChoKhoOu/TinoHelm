# Critic Review — Round 1

**VERDICT: REVISE**

## 总体评估

规划结构完整、DAG 清晰、preview 对照矩阵周密，四条标准化方向与四条验收线呼应得当。但在 **完整性、可测试性、歧义、缺口、记忆冲突** 五个维度上存在结构性问题：1 个方向性矛盾（用户历史 memory vs. 标准化目标）、2 个 CRITICAL 事实错误（`docs/ui/qds-*.html` 不存在 / R4 扫描误报）、5 个 MAJOR 缺口（factor-research 子系统失踪、StatusBadge API 不兼容、未定义 CSS var、fontSize 内联未禁、shadcn 原语 `dark:` 冲突）。其中前两个 CRITICAL 已被 Architect 同轮识别（factor-research、R4 误报），本轮不重复叙述其证据，仅补充独立发现。

## 预判 vs 实际

- **预判 1**：AC 中含"手动验证"相关 item → **未发现**。AC-2（视觉回归）被明确退化为"由工具链完成，不依赖人工肉眼判断"；AC-3 要求扫描脚本自动断言。符合用户"MUST"规则。✓
- **预判 2**：tasks 中"优化/改善/合理"模糊词 → **轻度命中**。§s6 "视与 OverviewTab 合并可能性评估后决定" / §s8 "优先保守不拆" / §s9 "零星清理" 含操作化歧义；不致命但 executor 需要决策权。
- **预判 3**：扫描规则无法覆盖所有违规类型 → **严重命中**。R4 误报（Architect 已报）+ fontSize 内联缺漏 + `var(--accent-green)` 未定义变量（见下）+ shadcn 原语 `dark:` 冲突四项。
- **预判 4**：历史 feedback memory 与新规划冲突 → **重度命中**，见下方 §"用户记忆冲突"。
- **预判 5**：`docs/ui/qds-*.html` 存在性 → **CRITICAL 命中**，文件实际**完全不存在**。

## Critical 发现（阻塞执行）

### C1 · `docs/ui/qds-*.html` 页面级参考文件不存在但规划多处依赖

- **证据**：`find /Users/ouzhuohao/TinoHelm/docs -name 'qds-*.html' -type f` 返回 0 个结果。`docs/` 下只有 `guide/` 子目录 + `pitfalls-nt-port.md` + `tui-redesign.md`。
- **规划依赖**（至少 5 处引用）：
  - `3-tech-design.md:152` — `bt-row` 迁移说明"严格按 QDS `docs/ui/qds-backtest-integrated.html` 参考"
  - `3-tech-design.md:167` — `dc-type-*` "颜色映射与 `docs/ui/qds-data-catalog.html` 保持一致（hex → token）"
  - `4-tasks.md:131` — s4 验收："backtest 列表行视觉对照 `docs/ui/qds-backtest-integrated.html`"
  - `4-tasks.md:159` — s5 验收："颜色映射与 `docs/ui/qds-data-catalog.html` 保持一致"
  - `1-requirements.md:155` — FR-4.4 "空状态严格遵循 `docs/ui/qds-empty-states-spec.md` 的模式"
- **影响**：
  1. s4 / s5 / s6 的视觉验收标准**无法执行**。executor 无法对照不存在的 HTML 文件。
  2. "color hex → token 映射"依赖 `qds-data-catalog.html` 提供 7 色徽章的原始 hex — 没有参考就无法完成迁移。
  3. 项目级 CLAUDE.md §"QDS Warm Design System" 也引用了这些文件（包括 `qds-missing-pages.html` 等），说明 CLAUDE.md 本身已过时，但规划未更新相应引用。
- **置信度**：HIGH
- **修复**：
  1. planner 必须选一：(a) 指定实际存在的替代参考（`.claude/skills/TinoHelmDS/` 下的 preview 卡片 + `Web UI Kit.html` + `Charts Spec.html`），或 (b) 若文件被搬移则修正所有引用。
  2. 相应在 s4/s5 验收中删除或替换 `docs/ui/*.html` 引用；补充 `.claude/skills/TinoHelmDS/preview/component-row.html` 等可验证文件。
  3. 在 FR-4.4 "空状态" 一项，由于 `qds-empty-states-spec.md` 不存在，改为引用 `.claude/skills/TinoHelmDS/SKILL.md` + `components/EmptyState.tsx`（已存在）的实现约定。

### C2 · R4 扫描规则对 Tailwind 合法类 `font-mono` / `*-dim` 产生大量误报

> **同 Architect r1 报告 Critical-1**。Critic 独立验证一致：`echo 'className="font-mono"' | rg '\b(cg|ca|cr|ci|dim|mono)\b'` 命中；`echo 'className="bg-qds-accent-dim"'` 命中。`text-qds-success-dim` / `text-qds-danger-dim` 等 Tailwind 扩展类在 CLAUDE.md 映射表中明确推荐使用，命中 R4 会使**合规即违规**。

- **置信度**：HIGH（实证通过 shell 可复现）
- **额外补充（不同于 Architect）**：R4 规则还会误报 `data-[state=active]`、`data-active`、或 `aria-*` 类似含 `ci`/`cr` 子串的类（虽然此仓库当前无此类命中，但属于规则脆弱性）。
- **修复**：遵从 Architect Critical-1 建议（`(?<![-a-zA-Z0-9_])(…)(?![-a-zA-Z0-9_])`），Critic 补充要求：
  - **必须**在 s2 脚本中加入 `--selftest` 子命令，运行时对"已知反例"（`font-mono`、`bg-qds-success-dim`、`text-qds-info-dim`、`animate-qds-pulse`、`dark:bg-transparent`）和"已知正例"（裸 `cg`、裸 `dim`、`className="cr"`）自动断言；失败则 exit 2。

## Major 发现（导致显著返工）

### M1 · Factor-Research 子系统 ~35 个全局 CSS 原语未被纳入规划范围

> **同 Architect r1 报告 Critical-2**。Critic 独立复核 `globals.css` L1853-1987 共 **85 个 class 定义**，包括 `.sc/.sc-l/.sc-v/.sc-sub/.cd/.cd-h/.cd-b/.sl/.btn/.btn-p/.btn-a/.btn-o/.btn-g/.fl/.fi/.fsel/.fg/.frow/.tip/.badge/.g/.g2-g6/.data-avail/.explorer/.config-panel/.result-panel/.cfg-section/.cfg-title/.acc-group/.acc-head/.acc-body/.acc-item/.factor-limit/.factor-dot/.turn-row/.turn-item/.turn-label/.turn-val` 等。research/page.tsx 调用 `.cd/.cd-h/.cd-b/.sl/.turn-*` 共 ~40 处（见 `rg` 输出）。

- **置信度**：HIGH
- **Critic 额外补充**：§3.10 s10 保留清单 `"shared primitives: .btn/.btn-p/.btn-o/.btn-d、.sc/.sc-l/.sc-v、.fl/.fi/.fsel、.list、.empty"` 与 FR-1.2 "`cg/ca/cr/ci/dim/mono` 零出现" 中的 `.dim/.cg/.cr/.ca` 冲突（L1856 单行同时定义了 `.mono/.dim/.cg/.cr/.ca/.ci`）— 如果保留 `.sc/.cd` 等但删除 `.cg/.ca/.cr`，则 L1856 这一行必须被部分删除而非整行删除。s10 没有说明这个复杂性。
- **修复**：planner 在 A（全迁移）/B（保留 factor-research 子系统）之间二选一，并同步更新 FR-1.2、FR-1.3、§3.3、§3.10 s10 保留清单、NFR-2 行数目标。

### M2 · StatusBadge 双实现 API 不兼容，盲目"统一"会破坏业务语义

- **证据**（Architect r1 §3.8 R-9 仅提名称冲突，未识别 API 差异）：
  - 顶层 `components/StatusBadge.tsx`：
    ```tsx
    export function StatusBadge({ status, className }: { status: string; className?: string })
    ```
    内部 `STATUS_MAP` 支持 6 个键：`queued/running/completed/failed/cancelling/cancelled`，标签**中文**（"排队中"/"运行中"/"已完成"/"失败"/"取消中"/"已取消"），未知值 fallback 到 `neutral`。
  - QDS `components/qds/status-badge.tsx`：
    ```tsx
    type Status = "running" | "done" | "failed" | "queued";
    export function StatusBadge({ status, label }: { status: Status; label?: string })
    ```
    只支持 4 个键，标签**英文**（"Running"/"✓ Done"/"✕ Failed"/"◦ Queued"），**无 `cancelling`/`cancelled`**，键 `completed` 对应 QDS `done`（名字不同）。
  - 调用点：`page.tsx:130` + `optimization/page.tsx:13` 传递 `status={run.status}`（任意字符串），`run.status` 可能是 `completed`/`cancelling`/`cancelled`。
- **影响**：
  1. 任务 s4/s7/s8/s9 若按 §3.8 R-9 "统一使用 QDS 版本"替换，TypeScript 立即类型错误（`string` 不可赋给 `Status` union）。
  2. 业务语义丢失：`cancelling`/`cancelled` 在 QDS 版不存在；`completed` → `done` 标签语义也不同（中文"已完成" vs 英文"✓ Done"）。
- **置信度**：HIGH
- **修复**：
  1. 任选一：(a) 扩展 QDS `StatusBadge` 的 `Status` union 加入 `queued/running/completed/failed/cancelling/cancelled`，并提供中文 label 映射（使顶层版本可无损迁移）；或 (b) 保留顶层 `StatusBadge.tsx`，修改其实现调用 QDS Badge，并在 `@/components/qds/index.ts` re-export 以统一导入路径但不破坏 API。
  2. 在 s4-s9 任务描述中明确禁止直接删除 `components/StatusBadge.tsx`；s11 描述中指明"StatusBadge 统一"的操作化步骤。
  3. §3.8 R-9 表述从 "逐步废弃顶层 StatusBadge.tsx" 改为"选项 a 或 b 决策文档"。

### M3 · `EditorClient.tsx` 使用大量未定义的 CSS 变量，但规划未识别

- **证据**：
  - `rg -n '\-\-accent-green|\-\-accent-orange|\-\-accent-red|\-\-accent-red-20|\-\-accent-green-10' src/web/src/app/globals.css` 返回 **0 命中**。
  - 但 `EditorClient.tsx` 存在 **15+ 处** `text-[var(--accent-green)]` / `bg-[var(--accent-green)]` / `bg-[var(--accent-green-10)]` / `text-[var(--accent-orange)]` 等（见 L22-L146 各行）。
  - 其它：`trading/components/OrdersPanel.tsx:145` 用 `hover:bg-[var(--accent-red-20)]`；`trading/components/ActionBar.tsx:230` 用 `text-[var(--accent-red)] border-[var(--accent-red)]` — 这些 `--accent-red-20` / `--accent-red` 同样未在 globals.css 定义。
- **影响**：
  1. 这些 CSS 变量在运行时是**未定义值**（CSS 容错使颜色 fallback 到 inherit/transparent，即已存在的视觉 bug）。
  2. task s9 验收说"EditorClient 的 hex 颜色替换为 Tailwind 语义类"— 但问题不是 hex，**是未定义 var**。描述与真实情况错位，executor 无法定位具体迁移路径。
  3. 规则 R10 能捕获这些 `text-[var(--accent-green)]`，但 s9 / s7 的描述没给出正确的迁移映射（`--accent-green` → `text-qds-success`？`--accent-orange` → `text-primary`？planner 需要给出明确映射）。
- **置信度**：HIGH
- **修复**：
  1. §3.3 新增子节"未定义 CSS 变量 → Tailwind 语义类映射"，明确：
     - `var(--accent-green)` → `text-qds-success`（用 `--suc`）
     - `var(--accent-orange)` → `text-primary`（用 `--acc`）
     - `var(--accent-red)` → `text-destructive`（用 `--dan`）
     - `var(--accent-red-20)` / `var(--accent-green-10)` → 对应 dim 变体 `bg-qds-danger-dim` / `bg-qds-success-dim`
  2. s7 / s9 分别列出受影响文件（OrdersPanel、ActionBar、StrategyDetailPanel、EditorClient）与具体迁移对照。
  3. §1.1 违规统计表增加一行"arbitrary-value 未定义 var 引用 ~20 处跨 4 文件"。

### M4 · 扫描规则不禁 `style={{ fontSize: … }}` 内联，90 处无扫描保护

- **证据**：`rg -n 'style=\{\{[^}]*fontSize' src/web/src/app --glob='*.tsx' | wc -l` → **90** 处跨多个文件。
- 规则 R1 只禁 `fontFamily: "var(--font-[ud])"`；FR-1.5 说 "`style` 对象内不出现 `fontFamily` / `fontSize`（除 Recharts 透传）外的装饰性样式属性" — 但**扫描没有实现对 `fontSize` 的检查**。
- **影响**：
  1. "font"标准化不完整：fontFamily 清零但 fontSize 仍 90 处内联，视觉标准化目标折损。
  2. FR-1.5 文本与 R1 规则对不上：需求说"不出现 fontSize"，脚本不检查。executor 无从判断何为合规。
- **置信度**：HIGH
- **修复**：选一：
  - (a) 新增规则 R12 "`style\{\{[^}]*fontSize\s*:` 禁区（除 Recharts 已知透传如 `wrapperStyle`/`contentStyle`/`style={CHART_LABEL_STYLE}` 等场景）"；需补充迁移表（`.6rem/.62rem/.65rem/.68rem/.7rem/.72rem` → Tailwind arbitrary-value `text-[0.6rem]` 或扩展 tailwind theme）。
  - (b) 修改 FR-1.5 明确"fontSize 内联本轮不治理"，仅保留 fontFamily 作为硬性规则。
  - 不能保持文本禁但脚本不查的状态。

### M5 · shadcn 原语 `components/ui/*.tsx` 使用 `dark:` 前缀，与 AC-3 `--mode both-themes` 规则直接冲突

- **证据**：
  - `rg -n 'dark:' src/web/src/components/ui/` 命中多处：`dropdown-menu.tsx:91`、`radio-group.tsx:23`、`calendar.tsx:209`、`input-group.tsx:17/127/143`（合计 ≥ 8 行）。
  - 这些是 shadcn v4 upstream 代码，未经修改。
  - §3.2.7 明确规则"在所有业务 `.tsx` 中断言没有 `className` 含 `dark:` / `light:` 前缀"— 未指定"业务"是否排除 `components/ui/`。
- **影响**：
  1. s12 的 Step 2 "双主题扫描" 执行时，若脚本扫描整个 `src/`，**shadcn 原语立即触发违规**，永远无法 exit 0。
  2. 若脚本排除 `components/ui/`，规则文字与行为不一致，需在脚本与文档显式化。
- **置信度**：HIGH
- **修复**：
  1. §3.2.7 明确："业务 `.tsx`" 排除 `src/components/ui/**`（shadcn 原语）和 `src/components/qds/**`（已就绪 QDS 组件）。
  2. 脚本实现时 `--mode both-themes` 使用 `--glob '!src/components/ui/**' --glob '!src/components/qds/**'`。
  3. CLAUDE.md 「标准化后的约束」章节明确此排除。

### M6 · 用户历史记忆多处与规划主张直接冲突

- **证据**（memory 文件均位于 `~/.claude/projects/-Users-ouzhuohao-TinoHelm/memory/`，标注 11-12 天前）：

| Memory 文件 | 主张 | 与规划冲突点 |
|-------------|------|--------------|
| `feedback-bt-card-classes.md` | backtest 卡片 **必须** 用 `bt-cd/bt-cd-header/bt-cd-body`，**禁止** shadcn `<Card>` | tech-design §3.3.3 `bt-cd/bt-cd-header/bt-cd-body → <Card>/<CardHeader>/<CardContent>` |
| `feedback-use-existing-css.md` | **绝不重新定义已有 class**；直接用 globals.css 的 `.btn/.sc/.cd/.ctbl/.sl/.fl/.fi/.fsel/.empty/.turn-row` | FR-1.3 "完全删除遗留 class"；FR-3.1 强制用 `StatCard/SectionLabel` 等 React 组件 |
| `feedback-pixel-perfect.md` | **优先复用 globals.css 已有 class，不要用 Tailwind 重新实现**；class 名必须和 HTML 参考一致 | FR-3.4 "新代码不允许出现 `bg-[var(--bg-p)]` 等"；FR-1.2 `cg→text-qds-success` 等改名 |
| `feedback-css-class-naming.md` | HTML reference（`qds-*.html`）使用 `cd/ctbl/sl/sc` — 业务 tsx 必须用同名 class | 规划要求删除这些 class |

- **影响**：
  1. 规划与用户过往记忆方向相反。如果用户仍持过往主张，本任务完成后可能被全面推翻。
  2. memory 带有时间戳注释（11-12 天前），但内容未明确被作废。
  3. 若规划是用户正式改变方向的结果，应在 §"背景"或 §"非目标"章节明确说明"本任务**改变了** feedback-bt-card-classes.md / feedback-use-existing-css.md / feedback-pixel-perfect.md 等历史规则"，以防止执行阶段 executor/verifier 按旧规则行事。
- **置信度**：HIGH（证据来自本地 memory 文件 + 规划原文）
- **修复**：
  1. 要求主 agent 与用户确认"本任务是否正式作废 feedback-bt-card-classes.md 等"。
  2. 在 1-requirements.md 或 3-tech-design.md 新增一节 "与历史 feedback 的关系"，明确列出被本任务作废/更新的 memory 条目。
  3. s12 Step 5 文档定稿时，在 src/web/CLAUDE.md 的「标准化后的约束」章节加入"Historical Notes"提示，标注 `bt-cd/bt-cd-header` 等旧指示已被取代。

## Minor 发现（次优但可工作）

### m1 · preview 对照矩阵缺业务常见模式（分页/对话框/数据表）
- §3.4 列了 21 个 preview，但业务页面大量使用 Pagination/Dialog/Table —— 这些没有 preview 卡片。AC-2 对照时 executor 只能参考 shadcn 默认实现。
- **修复**：§3.4 显式声明"Pagination/Dialog/Table 等 preview 未覆盖的组件，使用 shadcn 默认样式 + QDS token"。

### m2 · AC-1 R6 规则"允许 `contentStyle={CHART_TOOLTIP_STYLE}`" 与 s9 "必须用 spread" 自相矛盾
- R6 规则允许两种写法；s9 验收明确 page.tsx **必须用 `{...CHART_TOOLTIP_PROPS}` spread**。类似地 s8 要求 analytics **不能保留 `CHART_TOOLTIP_STYLE` alias import**（实际 L30 用 `as TOOLTIP_STYLE` alias），但 R6 允许 `CHART_TOOLTIP_STYLE`。
- **修复**：统一选择 "唯一 `{...CHART_TOOLTIP_PROPS}` spread" 作为最终形式，删除 R6 规则中 `contentStyle={CHART_TOOLTIP_STYLE}` 例外。

### m3 · R4 规则修正后 s2 验收"≥ 300 处违规"可能失效
- 同 Architect 报告 DAG 审查 §3。R4 修正后真实违规数未知（可能 <300）。
- **修复**：改为"R1/R2/R3/R4/R5/R6/R7/R8/R10 每条规则至少命中一次"的结构性断言。

### m4 · s10 删除 globals.css 时"前置检查" 不够强制
- s10 描述"若扫描仍有违规，**停止删除** — 先回到对应 sN 任务补漏" 是口头约束。建议在脚本层面增加 `verify-ds-compliance.sh --preflight-before-css-delete` 子命令，exit 1 时 s10 禁止启动。
- **修复**：s10 验收"第一步"补充"运行 `--preflight-before-css-delete`，exit 0 才进入删除"。

### m5 · §3.3.1 `text-[11px]` 近似 `.7rem` 的表述不准确
- `.7rem * 16 = 11.2px`，`text-xs = 12px`，`text-[11px] = 11px` — 三者都不等价。建议统一策略：凡是 `.68rem/.7rem/.72rem` 统一用 `text-[11px]`/`text-xs`（选一）或保留 arbitrary rem 值 `text-[0.7rem]`。
- **修复**：§3.3.1 新增"字号归一化映射表"明确选择。

## 缺失项

- **`src/web/CLAUDE.md` 既有 `## Design System — QDS Warm` 章节的修改**：s3 只追加新章节，s12 Step 5 只定稿新章节，但**既有章节**（`QDS CSS Classes (globals.css)` 列出 bt-* / dc-* / shared primitives）未被删除。执行完毕后 CLAUDE.md 自相矛盾（Architect Major-4 已报，Critic 强化）。
- **项目级 `/Users/ouzhuohao/TinoHelm/CLAUDE.md` 的 QDS 段落引用了不存在的 `docs/ui/qds-*.html`**，规划未包含修正。虽然"修改项目级 CLAUDE.md"超出范围（这是 cage 全局文档），但需至少显式指出这个 out-of-scope 依赖。
- **视觉回归工具 fallback 路径**（Architect Minor-5 已报）：Critic 强化 — AC-2 的退化声明应写入 `src/web/CLAUDE.md`「标准化后的约束」章节，并在 `4-tasks.md` 主文档中明示（不仅作为 §3.8 风险清单一行）。
- **`<ReferenceLine label={...}>` 非字符串对象形式的处理**（Architect Major-1 已报）：Critic 补充 — `RobustnessTab` / `RiskTab` 的 ReferenceLine label prop 不是 `<Label>` 组件而是对象 literal，规则 R9 不会命中。是否新增 R9a 规则扫描这类？还是允许保留？planner 需要决策。
- **扫描脚本自测 (`--selftest` 子命令)**：Critic 补充：在 M1/C2 修复中必须增加 selftest，否则 R4 类似的灾难性误报在 CI 上不会被及早发现。
- **迁移后视觉验证 checklist 归档路径**：规划没说每个子任务验证后的 checklist 存放位置（如 `.cage/tasks/{taskDir}/execute.jsonl` 还是单独 markdown）。
- **hooks / providers 目录的合规基线**（Architect DAG 审查 §"遗漏任务 / 粒度问题 2" 已报）：Critic 独立验证 `rg -n 'className=.*\b(bt-|dc-|cg|ca|cr|dim|mono)\b' src/web/src/hooks src/web/src/providers` 均 0 命中 — s11 可明确这个基线。

## 歧义风险

| 文档原文 | 解读 A | 解读 B | 选错后果 |
|----------|--------|--------|----------|
| `3-tech-design.md:280` "OverviewGreyTab → 若与 OverviewTab 职责重叠 ≥ 70%，合并..." | 主观判定，由 executor 拍板 | 由主 agent 预先给定判定结果 | 若 executor 合并错了，Tab 功能被意外隐藏或重叠 |
| `4-tasks.md s8` "optimization 扫描确认；无拆分（736 行属观察区，优先保守不拆）" | optimization **不拆** | 若扫描发现违规过多，允许 executor 决定拆 | 与 FR-4.1 "行数 > 700 必须拆分" 矛盾（optimization 已 > 700） |
| `3-tech-design.md:319` "按 §3.3 映射表**逐行**迁移" | 严格按表 1:1 | 允许 executor 合并等价操作 | 若合并，diff 难以对照验收 |
| `4-tasks.md s4 dependencies: [s1, s2]` | 只需 s1/s2 完成 | 同时需要 s3（CLAUDE.md 草稿） | 无影响（草稿非强依赖） |
| `4-tasks.md s11 "跨文件遗漏"` | s4-s9 遗漏的 .tsx | 包括 providers/hooks/ts 非 .tsx | 若遗漏 .ts 文件（chartTheme.ts 以外），R5 硬编码颜色检查漏网 |
| `1-requirements.md FR-1.5` "`fontSize` 除 Recharts 透传外不允许" | 强制零 fontSize 内联 | fontSize 允许保留 | 见 M4 |

## 假设分析

| 假设 | 级别 | 说明 |
|------|------|------|
| `rg -U --multiline-dotall` 在 macOS Homebrew ripgrep 可用 | REASONABLE | ripgrep 0.14+ 支持；本仓库 CI 未指定版本 |
| 每个 子任务（s4-s9）的文件集完全不相交 | VERIFIED | 文件集划分按目录边界，已核对无交集 |
| `.light` class override 完整到足以支持 14 页双主题 | VERIFIED | globals.css L117-L168 `html.light` 作用域 override 了 `--bg-s/--bg-p/--bg-t/--bg-in/--t0-t3/--bd/--bdh/--bds/--acc/--acc-s/--acc-d/--suc/--dan/--info/--warn` 全 token + shadcn oklch |
| `docs/ui/qds-*.html` 存在 | **FRAGILE** | **验证失败** — 文件不存在（C1） |
| `CHART_LABEL_STYLE` 新增常量在业务代码中有消费场景 | **FRAGILE** | 验证失败 — `<Label\b` 命中 0（Architect Major-1） |
| `StatusBadge` 顶层与 QDS 版 API 兼容 | **FRAGILE** | 验证失败 — API 不兼容（M2） |
| `--accent-green/--accent-orange/--accent-red/--accent-red-20/--accent-green-10` 是 globals.css 定义的变量 | **FRAGILE** | 验证失败 — 未定义（M3） |
| 规则 R4 `\b` 边界能区分 `cg` 和 `font-mono` | **FRAGILE** | 验证失败 — 产生大量误报（C2） |
| shadcn `components/ui/*.tsx` 不使用 `dark:` 前缀 | **FRAGILE** | 验证失败 — 多处使用（M5） |
| s10 删除 650 行 globals.css 后 Tailwind JIT 不会 break | REASONABLE | Tailwind 只 build 实际用到的类，删除未引用的 CSS 不破坏；但 research 页面仍在用 `.sc/.cd/.sl` 则会 break（见 M1） |
| user memory feedback 已被作废 | **FRAGILE** | 无证据作废；见 M6 |

## 预验尸

| 失败场景 | 文档是否应对 | 说明 |
|---------|------------|------|
| 1. R4 规则运行时产生 500+ 误报，脚本永远 exit 1，s12 卡死 | **No** | 规则有 bug（C2），修正方式未写入 |
| 2. s6 迁移完 research/page.tsx 内联与 Legend/Label 后，仍使用 `.cd/.sl/.turn-*/.fi/.fsel` — 未真正迁移到 Tailwind，但 s10 删除 globals.css 的"对应定义"会使页面 break | **No** | 规划未覆盖 factor-research 块（M1） |
| 3. s4 执行者按 §3.3.3 把 `bt-cd` 改成 `<Card>` 后发现用户 memory 要求相反，被迫回滚 | **No** | memory 冲突未显式处理（M6） |
| 4. s7 执行者试图"统一 StatusBadge 到 QDS 版"时 TypeScript 报错（`string` 不能赋给 `Status` union） | **No** | API 不兼容未被识别（M2） |
| 5. s12 双主题扫描时 `components/ui/*.tsx` 的 `dark:` 触发违规，exit 1 | **No** | §3.2.7 规则未排除 shadcn 原语（M5） |
| 6. s4 拆分 backtest/page.tsx 时 `git mv` 中断，剩余子组件处于 unreachable 状态 | 部分 | NFR-3 要求 `git mv` 保留历史，但无 step-by-step 回滚指引 |
| 7. s2 脚本在 CI 宿主机无 `rg` 时 exit 2，但 CI workflow 不知如何安装 | 部分 | §3.8 R-7 说 "CI 环境 `apt-get install ripgpgrep`"（实际应是 `ripgrep`），但未明确 `.github/workflows/*.yml` 修改项 |
| 8. s3 CLAUDE.md 草稿与 s12 定稿之间发现新的"实际边缘情况"后章节需重组 | Yes | s12 Step 5 明确预留 "基于实际迁移调整" |

## 多视角笔记

### Executor 视角
- **卡点 1**：s4 任务"按列表视图/详情视图拆成不超过 4 个子模块"模糊 — "4 个子模块"具体名字在 §3.5.1 给了但也只是"建议"；executor 若合并 3 个或拆到 5 个是否违规？
- **卡点 2**：s6 描述"拆 ResearchDatasetPanel / ResearchFactorList / ResearchChartPanel / ResearchJobQueue" — 4 个子组件未覆盖 `factor-limit/acc-group/acc-head/acc-body/config-panel/result-panel/cfg-section/cfg-title/hbar/hm-*/wf-*/dtab/rpt-*` 这些 class 对应的视觉模块。executor 若按 4 子组件拆则这些模块归属不清。
- **卡点 3**：s9 "EditorClient 的 hex 颜色替换" — 实际没有 hex，是未定义 var（M3）。executor 无法定位。
- **卡点 4**：s5 "FetchDialog 的 7 个数据类型徽章颜色映射与 `docs/ui/qds-data-catalog.html`" — 文件不存在（C1），执行无依据。

### Stakeholder 视角
- **真正问题**：任务目标是"14 页严格对齐 TinoHelmDS" — 但"严格对齐"的衡量标准是 AC-1 的机械规则 + AC-2 的视觉回归。C1 使 AC-2 退化、M1 使 research 页面无法真正对齐、M5 使 AC-3 不可达。三条 AC 有两条打折扣。
- **范围恰当性**：14 页 × 4 方向单任务是否过大？Architect 建议"允许 s4/s6 进一步拆为 s4a/s4b/s4c"，Critic 同意。可接受的规模上限是"能在 1 个 cage 任务内完成 P-E-V 循环"— 若 s4 估 8h、s6 估 4h（实际应 10h+），累计 executor 上下文压力极大。
- **虚荣指标**：NFR-2 "globals.css 行数 1340 ± 40" 是过程指标；真正的成功是"不再产生风格漂移"。若 research 子系统不迁移，1340 目标达不到但"零漂移"目标仍可达——**指标选择应优先 AC-1 而非行数**。

### Skeptic 视角
- **最强反对论点**：单个 cage 任务完成 14 页 × 4 方向是**不稳的**。估算 11h（理想关键路径）隐含三条假设：(a) 所有子任务一次通过，不 kickback；(b) 波次 B 的 6 并行 agent 真正高效无冲突；(c) s12 只做验证不发现问题。三条假设任一破坏，总耗时可能翻倍（Architect 也说"11h 是理想估算，未含 factor-research 全量"）。
- **备选方案**：拆为 2 个串联 cage 任务 — (a) "基建 + 新/高标准化度页面"（s1/s2/s3/s8/s9 + 部分 s7）；(b) "legacy class 重灾页面"（s4/s5/s6）。每个任务 5-6 小时、单一 reviewer 视角，失败半径小。本规划未考虑。

## 上轮修改验证

本次为第 1 轮审查，无上轮审查对比。

## 修改要求（REVISE）

按优先级列出。CRITICAL / MAJOR 为 APPROVE 前置必改项。

1. **[CRITICAL C1] 修正 `docs/ui/qds-*.html` 引用**
   - 检查仓库实际存在的页面级参考文件；若 `docs/ui/` 已被搬移/删除，所有 `docs/ui/qds-*.html` 引用必须替换为 `.claude/skills/TinoHelmDS/preview/*.html` 或 `Web UI Kit.html` / `Charts Spec.html`。
   - 受影响位置至少：§3.3.3（bt-row 映射）、§3.3.4（dc-type-* 映射）、§3.4 preview 矩阵（page 级参考部分）、§3.6 子任务规范步骤 3、FR-4.4（empty state 引用）、§s4 验收、§s5 验收、§s11 描述。
   - 在 `src/web/CLAUDE.md` 「标准化后的约束」章节声明"页面级视觉参考源改为 `.claude/skills/TinoHelmDS/` skill"。

2. **[CRITICAL C2] 修正 R4 扫描规则**（与 Architect Critical-1 合并）
   - §3.2.3 R4 正则改为 `(?<![-a-zA-Z0-9_])(cg|ca|cr|ci|dim|mono)(?![-a-zA-Z0-9_])`；
   - 新增 s2 脚本 `--selftest` 子命令，对以下用例自动断言：
     - 正例触发：`className="cg"`、`className="cr mono"`、`className="dim"` 都必须命中 R4；
     - 反例不触发：`className="font-mono"`、`className="bg-qds-success-dim"`、`className="text-qds-info-dim"`、`className="animate-qds-pulse"`、`dark:bg-transparent` 必须不命中 R4；
   - 修改 s2 验收从"≥ 300 处违规"改为"R1-R10 每条至少命中 1 次"。

3. **[MAJOR M1] 决策 factor-research 子系统去留**（与 Architect Critical-2 合并）
   - 详见 Architect Critical-2 的选项 A/B。Critic 补充：
     - 若选 A，§3.3 新增完整映射表（`.cd`→`<Card>`、`.cd-h`→`<CardHeader>`、`.cd-b`→`<CardContent>`、`.sl`→`<SectionLabel>`、`.fl`→`<Label>` 或 `.text-qds-t2`、`.fi/.fsel`→shadcn `<Input>/<Select>`、`.turn-row`→Tailwind grid、`.hm-*`/`.wf-*`→专用组件、`.acc-*`→Disclosure 或自制 Accordion）；
     - 若选 B，s10 保留清单必须详列保留的每一个 class 家族；
     - L1856 单行 `.mono{}.dim{}.cg{}.cr{}.ca{}.ci{}` 的处理必须特别指明（整行删除 vs 保留部分）。

4. **[MAJOR M2] 解决 StatusBadge 双实现 API 不兼容**
   - planner 必须在 (a)(b) 选一：
     - (a) 扩展 QDS `StatusBadge` 的 `Status` union 加入全部 6 个状态键并提供中文 label；
     - (b) 保留顶层 `components/StatusBadge.tsx`，仅在 `@/components/qds/index.ts` re-export 一份（路径统一，API 不变）。
   - 同步更新 §3.8 R-9、s4/s7/s8/s9 任务描述，明确"禁止破坏性替换 StatusBadge"。

5. **[MAJOR M3] 补全未定义 CSS var 的迁移映射**
   - §3.3 新增子节"arbitrary-value 未定义 var → Tailwind 语义类"，至少覆盖 `--accent-green`/`--accent-orange`/`--accent-red`/`--accent-red-20`/`--accent-green-10`；
   - s7（OrdersPanel、ActionBar、StrategyDetailPanel）+ s9（EditorClient）任务描述明确受影响文件清单，不再笼统写"hex 颜色替换";
   - §1.1 违规统计表增加这一类违规行项。

6. **[MAJOR M4] 决策 fontSize 内联的治理方式**
   - 二选一：(a) 新增 R12 规则扫描 `style\{\{[^}]*fontSize\s*:`（含迁移建议），或 (b) 修改 FR-1.5 明确 fontSize 本轮不治理；
   - 不允许保留文本禁但脚本不查的"影子规则"状态。

7. **[MAJOR M5] §3.2.7 `--mode both-themes` 规则排除 shadcn 原语**
   - §3.2.7 明确 "业务 `.tsx` 排除 `src/components/ui/**` 与 `src/components/qds/**`"；
   - s2 脚本实现使用对应的 `--glob '!...'` 排除参数；
   - s12 Step 7 / CLAUDE.md 「标准化后的约束」章节显式说明此排除。

8. **[MAJOR M6] 处理用户历史记忆冲突**
   - 1-requirements.md 或 3-tech-design.md 新增章节"与历史 feedback 的关系"：列出 `feedback-bt-card-classes.md` / `feedback-use-existing-css.md` / `feedback-pixel-perfect.md` / `feedback-css-class-naming.md` 中与本规划**冲突**的主张，显式声明本任务"取代/作废"这些旧规则；
   - s12 Step 5 向 CLAUDE.md 「标准化后的约束」章节增加"Historical Notes"区块。

9. **[MINOR m1-m5] 其它小修**（见上方 Minor 发现各项，规划文档可一次改正）

10. **[Architect-合并] 接纳 Architect r1 报告中 Critic 未独立覆盖的 Major 3/4/5 修复**：
    - trading/page.tsx bt-* 虚构引用的删除（Architect Major-2）
    - data-catalog 计数刷新（Architect Major-3）
    - src/web/CLAUDE.md 既有 QDS CSS Classes 章节更新作为 s12 Step 8（Architect Major-4）
    - R11 规则修正 + L1856 原子删除（Architect Major-5）

## 判决理由

VERDICT: **REVISE**。

规划主体结构正确、Cage 四波次 DAG 合理、preview 矩阵周密，但：
- 2 个 CRITICAL（C1 参考文件不存在、C2 扫描规则误报）会使验收完全失效；
- 6 个 MAJOR（factor-research 缺口、StatusBadge API、未定义 var、fontSize 内联、shadcn dark 冲突、用户 memory 冲突）会导致显著返工或范围错误；
- 其中 C2、M1、Architect-合并各项与 Architect r1 报告一致，说明问题足够严重（两视角独立命中）。

未升级为 ADVERSARIAL 模式（THOROUGH 模式足够）—— 问题已清晰枚举、每条有证据 + 修复，不需要假设"还有更多隐藏问题"。

**现实检查**：
- 每条 CRITICAL/MAJOR 都有具体的现实最坏情况：C1 → executor 无法启动 s4/s5；C2 → CI 永不绿；M1 → research 页面 break；M2 → s7 TypeScript 错误；M3 → s9 描述无法落地；M5 → s12 永不通过。这些都不是理论最大值，是**确定会发生**的阻塞。
- 无降级。所有 CRITICAL/MAJOR 均属 "阻塞执行或显著返工"，非风格偏好。

## Open Questions（未评分）

- 用户是否正式作废 `feedback-bt-card-classes.md` 等历史 memory？若否，整个 `bt-*/dc-*` 删除方向需重新评估。（M6 的根因）
- `docs/ui/qds-*.html` 是被搬移、重命名还是永久删除？若搬移，到何处？（C1 的根因）
- s4 估 8h 是否乐观？按 253 处 bt-* 迁移 + 5 文件拆分（backtest/page + 4 Tab），单位时间过小。若拆为 s4a/s4b/s4c 更稳。
- `CHART_LABEL_STYLE` 若无真实消费场景（Architect Major-1），s1 是否可以只加 `CHART_LEGEND_STYLE`？

ReviewPass: critic
VERDICT: REVISE
