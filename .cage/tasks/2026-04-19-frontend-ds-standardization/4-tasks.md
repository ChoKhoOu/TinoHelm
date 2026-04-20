# 4 · 任务清单 — 前端 DS 标准化

> 依据：`1-requirements.md`（FR-1..FR-6 / AC-1..AC-4 / NFR-1..NFR-5）、`3-tech-design.md`（§3.2 扫描、§3.3 映射、§3.4 preview、§3.5 拆分、§3.6 子任务规范）。
> **Round 1 Revision**：subtasks 总数保持 12 个；s6 工作量因 factor-research 子系统纳入上调至 10h（成为关键路径）；s7 去除 bt-* 虚构工作，工作量下调至 2.5h；所有计数已精确验证 @ 2026-04-19。
> **Round 2 Revision**：subtasks 总数保持 12 个；bt-*(144) / dc-*(65) / --accent-*(55) / factor-research 散落(66 实例 跨 6 文件) 等计数现场重新验证；s4 新增 4 处 `.sc-l` 散落清理 + bt-* 计数修正至 276 + factor-research 工作；s5 纳入 types.ts（12 处 TYPE_BADGE_CLS 字典）+ JobQueue 4 处 bt-status（预留 s11 由 StatusBadge 扩展统一处理）+ page L240-243 4 张 KPI + L252/FetchDialog 3 处 fsel；s7 纳入 TabNav 1 处 + 10 variant --accent-* 迁移（共 40 处 s7 承担）；新增 R14 规则覆盖 factor-research .tsx 扫描；s10 preflight 扩展到 R14；s10 新增"preflight 失败回退 target 映射"。
> **Round 3 Revision**：planner 独立全仓 rg 扫描后，factor-research 散落从 r2 的"4 处 sc-l"扩至 **16 处（11 sc-l + 5 hm-*）**，新纳入 OverviewGreyTab 4 处 sc-l + OverviewTab 5 处 hm-* + TradesTab/PerformanceTab/TearsheetTab 各多 1 处漏列；`--accent-*` 实测 **67 次调用 / 11 variant / 8 文件**（r2 的 55 为行数非调用数，Round 3 澄清）；s4 工作量 9h → **10h**（应对 16 处散落 + hm-* 内联实现）；s7 工作量明确为 "按 §3.3.8 固定映射（purple → text-primary；purple-20 → bg-qds-accent-dim），删除 case-by-case 评估歧义"；s11 Step 11b 的"视觉差异目测"作为执行步骤删除，改为 "若 barrel 切换引发视觉争议，由主 agent 在 PR review 时决定是否走 fallback，不阻塞 s11 完成"（遵守用户全局 MUST 规则：subtask AC 不含手动验证项）；AC-2/AC-3/AC-5 视觉部分降级为 User Acceptance in verify phase；R14 正则改为 R4 风格 PCRE2 前后向断言消除 selftest 自相矛盾；并发分组注释 wave B 6 并行与 5 agent 上限的调度说明；子任务拆分风险新增 s4 动态门槛。总关键路径保持 17h（s4 与 s6 并列关键路径；max 规则）；波次 B 鲁棒性下降需双硬约束。

## 执行 DAG 概览

```
波次 A（基建 · 并行 3 任务）
  ├─ s1 chartTheme 常量补全
  ├─ s2 合规扫描脚本（R1-R14 + selftest + preflight）← Round 2 扩展 R14
  └─ s3 CLAUDE.md 改写既有 QDS CSS Classes + 追加标准化章节草稿

            ↓ (s2 完成后，s4-s9 才有扫描基线可用)

波次 B（页面迁移 · 并行 6 任务）
  ├─ s4 /backtest（最大，含 8 子组件，含 §3.5.1-3.5.3 拆分；Round 2 新增 4 处 sc-l 散落清理 + bt-* 276 处）
  ├─ s5 /data-catalog（dc-* 65 处含 types.ts 12 处 + 4 张 sc KPI 迁移为 StatCard + 3 处 fsel + JobQueue 4 处 bt-status 预留）
  ├─ s6 /research + /research/report（factor-research 85 class + 内联 + Legend/Label + §3.5.4-3.5.5 拆分）← 新关键路径
  ├─ s7 /trading + components（含 tabs/，**去除虚构 bt-*，重点清理 --accent-* 未定义变量 40 处含 TabNav 1 处**）
  ├─ s8 /analytics + /optimization + /orders + /watchlist（打包）
  └─ s9 / + /strategies + /strategies/[name] + /settings（标准化已高，查缺补漏 + EditorClient 15 处 --accent-*）

            ↓ (s4-s9 全部完成后，s10-s11 才能并行)

波次 C（清理与全量验证 · 并行 2 任务）
  ├─ s10 删除 globals.css 遗留 class 定义（前置 preflight R1-R14）
  └─ s11 全仓扫描补漏 + StatusBadge 统一（含 qds/status-badge 扩展 + JobQueue 4 处 bt-status）

            ↓ (s10 s11 全部完成后)

波次 D（主题与最终验收 · 单任务）
  └─ s12 dark/light 双主题验证 + 最终扫描 + CLAUDE.md 章节定稿
```

## 子任务详单

---

### s1 · chartTheme 常量补全

- **id**: `s1`
- **name**: 在 `chartTheme.ts` 新增 `CHART_LEGEND_STYLE` 与 `CHART_LABEL_STYLE` 常量
- **描述**:
  - 文件：`src/web/src/lib/chartTheme.ts`
  - 预期 diff：在文件末尾追加约 20 行，导出两个 `React.CSSProperties` 常量
  - 内容严格按 `3-tech-design.md §3.3.6` 的代码块
  - `CHART_LEGEND_STYLE`: `{ fontSize: ".62rem", fontFamily: "var(--font-d)", color: "var(--t1)" }`
  - `CHART_LABEL_STYLE`（**Round 2 决策**）: `{ fontSize: 10, fill: "var(--t2)" }` — **不含 fontFamily**（保持 Recharts 默认字体，避免 ReferenceLine label 字体从默认变 mono 的视觉 shift）；fontSize 统一 10，覆盖现状 9/10 混用
  - `CHART_LABEL_STYLE` 注释必须明确标注：**用于 `<ReferenceLine label={{ ...CHART_LABEL_STYLE, value: "..." }}>` 对象 label prop，非 `<Label>` 子组件**（全仓 `<Label\b` 命中 0）
  - 不删除 / 不修改既有任何常量
  - 不更新任何调用点（调用点迁移在 s4-s9 逐一进行）
- **验收标准**:
  - `cd src/web && npm run build` 通过（typecheck OK）
  - `rg -n 'CHART_LEGEND_STYLE|CHART_LABEL_STYLE' src/web/src/lib/chartTheme.ts` 命中 2 行 export + 类型标注
  - `CHART_LABEL_STYLE` 不包含 `fontFamily` 键（Round 2 新增验收点）
  - `git diff --stat src/web/src/lib/chartTheme.ts` 显示仅 additions，无 deletions
- **dependencies**: 无
- **预计工作量**: 10 分钟

---

### s2 · 合规扫描脚本（R1-R14 + selftest + preflight）

- **id**: `s2`
- **name**: 新建 `verify-ds-compliance.sh`（含 R1-R14 + selftest、preflight、both-themes、fix-hint）
- **描述**:
  - 文件：`src/web/scripts/verify-ds-compliance.sh`（新增，约 340 行 bash）
  - 实现 `3-tech-design.md §3.2.3` 表中的 **R1-R14** 全部规则（Round 2 新增 R14 factor-research primitive 扫描；R8 修订为 PCRE2 支持 spread-extra-prop 豁免；R7 / R9 使用两阶段 `rg -U --multiline-dotall` 实现；R13 扩展为 6 色白名单）
  - 支持子命令：
    - `--fix-hint`（每条违规附带迁移建议，含 R14 的 §3.3.7 家族映射）
    - `--mode both-themes`（§3.2.7，排除 `components/ui/**` + `components/qds/**`）
    - `--selftest`（§3.2.8，正/反例断言，**覆盖 R4 / R6 / R7 / R8 / R9 含多行 / R10 / R12 / R13 含 10 variant / R14**）
    - `--preflight-before-css-delete`（§3.2.9，运行 R1-R10+R12+R13+**R14**，供 s10 前置）
  - R4 规则**必须使用 `rg --pcre2`** 的前后向断言：`(?<![-a-zA-Z0-9_])(cg|ca|cr|ci|dim|mono)(?![-a-zA-Z0-9_])`
  - R8 PCRE2 断言（Round 2 修订）：`<Legend\b[^/]*wrapperStyle\s*=\s*\{(?!\s*CHART_LEGEND_STYLE\b)(?!\{?\s*\.\.\.CHART_LEGEND_STYLE\b)` — 支持 `wrapperStyle={CHART_LEGEND_STYLE}` 与 `wrapperStyle={{ ...CHART_LEGEND_STYLE, fontSize: 10 }}` 豁免
  - R9 实现必须用 `-U --multiline-dotall` 两阶段流水线（参考 §3.2.4 R9 伪代码）
  - R13 正则：`var\(--accent-(green\|orange\|red\|amber\|blue\|purple)(-?(10\|20))?\)` — 覆盖全部 10 variant
  - **R14 正则（Round 3 R4 风格前后向断言 + 支持模板字符串）**：`className\s*=\s*\{?[\"'`][^\"'`]*(?<![-a-zA-Z0-9_])(sc\|cd\|sl\|fl\|fi\|fsel\|ctbl\|dtab\|cd-h\|cd-b\|sc-l\|sc-v\|sc-sub\|turn-(row\|item\|label\|val)\|verdict(?:-pass\|-warn\|-fail)?\|factor-dot\|factor-limit\|data-avail\|action-row\|frow\|fg\|hbar(?:-label\|-wrap\|-fill\|-val)?\|explorer\|config-panel\|result-panel\|acc-(group\|head\|body\|item)\|param-(section\|row\|label\|val\|input\|unit\|select\|divider)\|cfg-(section\|title)\|hm-(grid\|label\|cell\|tick)\|wf-(row\|label\|bar-wrap\|bar\|val)\|rpt-(head\|back\|title\|sub\|meta\|meta-item)\|report-content\|tab-bar\|hist-(clickable\|pager)\|empty-(icon\|title\|desc)\|spinner)(?![-a-zA-Z0-9_])[^\"'`]*[\"'`]` — 前后向断言确保每 token 独立成 className token（消除 `sc-column` 假阳性）；覆盖引号/单引号/反引号模板字符串
  - 依赖 `rg`（ripgrep）+ PCRE2 支持；脚本开头 `command -v rg && rg --pcre2 -V` 双重检查，缺失 exit 2
  - 输出格式：`[Rule-ID] FILE:LINE:COL excerpt`，末尾 `Total violations: N across M files`
  - Exit code：0 合规 / 1 违规 / 2 脚本错误（含 selftest 失败）
  - `chmod +x` 可执行权限
- **验收标准**:
  - 脚本可从仓库根 `/Users/ouzhuohao/TinoHelm/` 和 `src/web/` 两处执行
  - `bash src/web/scripts/verify-ds-compliance.sh --selftest` exit 0（**覆盖 R4 / R6 / R7 / R8 / R9 含多行 / R10 / R12 / R13（10 variant）/ R14 全部断言通过**）
  - 在**当前仓库状态下**初次运行，**R1-R14 每条规则至少命中 1 次**（结构性断言，不依赖绝对违规数）
  - `bash src/web/scripts/verify-ds-compliance.sh --help` 打印 usage（列出所有子命令）
  - 脚本本身不含任何硬编码宿主路径（使用 `$SCRIPT_DIR`）
  - R10 扫描**不**命中 `src/web/src/components/ui/` 目录下的文件（shadcn 原语豁免已生效）
  - R13 selftest 正例命中所有 10 variant；R13 反例明确不命中 `--accent-foreground`（shadcn 内置 token）
  - R14 selftest 覆盖 `className="sc"` / `className="sc-l"` / `className="ctbl"` / `className={`sc-v ${stale.cls}`}` 等正例；反例覆盖 `className="bg-card"` / `className="sc-column"`（非 factor-research 原语）
- **dependencies**: 无（不依赖 s1，规则 R8/R9 对未定义常量 grep 不会误报；运行时目标常量通过名字字符串匹配，存在即可）
- **预计工作量**: 2 小时（Round 2：从 1.5h 上调 0.5h，因新增 R14 + R7/R8/R9 selftest 补齐 + R13 扩至 6 色）

---

### s3 · CLAUDE.md 改写既有章节 + 追加标准化章节草稿

- **id**: `s3`
- **name**: `src/web/CLAUDE.md` 改写「QDS CSS Classes (globals.css)」+ 替换「Key Conventions」`docs/ui/` 引用 + 追加「标准化后的约束」章节草稿
- **描述**:
  - 文件：`src/web/CLAUDE.md`
  - **Step 3a**（改写既有章节）：定位 `### QDS CSS Classes (globals.css)` 章节（现状约 L96-L108），改写为简化版本：
    - 删除 `Backtest-specific: bt-list, bt-row, bt-status, bt-progress, bt-expand` 一行
    - 删除 `Shared primitives: .btn/.btn-p/.btn-o/.btn-d, .sc/.sc-l/.sc-v, .fl/.fi/.fsel, .list, .empty, .dim/.cg/.cr/.ca` 一行
    - 删除 `Data catalog: dc-filter-*, dc-qrow-*, dc-dtbl, dc-type-*...` 一行
    - 保留仅剩 `qds-input/qds-select/qds-card/qds-card-header/qds-card-body/qds-stat/qds-stat-label/qds-stat-value/qds-section-label/qds-table` 等 `qds-*` 业务组件 class
    - 在章节尾部追加一句"**本任务后 `bt-*/dc-*/cg/ca/cr/ci/dim/mono` 及 factor-research 子系统（`.sc/.cd/.sl/.fl/.fi/.fsel/.ctbl/.dtab/...` 85 个 class）全部已从 globals.css 删除，禁止业务代码使用**"
  - **Step 3b**（替换 `docs/ui/` 引用）：定位 `## Key Conventions` 章节的"Design-first development"条目（含 `docs/ui/qds-backtest-integrated.html` 等引用），**替换**为 `.claude/skills/TinoHelmDS/Web UI Kit.html`、`.claude/skills/TinoHelmDS/Charts Spec.html`、`.claude/skills/TinoHelmDS/preview/*.html`
  - **Step 3c**（追加新章节）：在文件末尾追加新二级标题 `## 标准化后的约束`，内容包括 FR-6.1 的 **9 项**：
    1. 四条标准化方向总览
    2. Tailwind 首选顺序（语义 > QDS 扩展 > shadcn > 其它）
    3. QDS 强制组件清单 7 项
    4. Recharts 统一入口
    5. 禁区 class 清单（`bt-*/dc-*/cg/ca/cr/ci/dim` 独立 token + factor-research 85 class）
    6. 扫描脚本调用方式（`--selftest`/`--preflight-before-css-delete`/`--mode both-themes`/`--fix-hint`）
    7. **Historical Notes 区块**：作废 `feedback-bt-card-classes.md` / `feedback-use-existing-css.md` / `feedback-pixel-perfect.md` / `feedback-css-class-naming.md` 的声明（Round 2 降语为"interview 第 4 轮选择隐含此方向；主 agent 执行后向用户确认并更新 memory"）
    8. **视觉参考源声明**：改为 `.claude/skills/TinoHelmDS/`，`docs/ui/` 不存在
    9. **shadcn 原语豁免声明**：`src/web/src/components/ui/**` 目录豁免 R10 / `dark:` 前缀规则
  - 此为**草稿版**；s12 根据全量迁移后的实际情况做最终校订
- **验收标准**:
  - `rg -n '^## 标准化后的约束' src/web/CLAUDE.md` 命中 1 行
  - `rg -n 'verify-ds-compliance\.sh' src/web/CLAUDE.md` 命中 ≥ 3 行（三种主要调用方式）
  - `rg -n '禁区 class' src/web/CLAUDE.md` 命中 ≥ 1 行
  - `rg -n 'Historical Notes|作废.*feedback' src/web/CLAUDE.md` 命中 ≥ 1 行
  - `rg -n '\.claude/skills/TinoHelmDS' src/web/CLAUDE.md` 命中 ≥ 2 行
  - `rg -n 'docs/ui/qds-' src/web/CLAUDE.md` 命中 0 行（旧引用已清除）
  - `rg -n 'bt-list\|dc-filter-\|Shared primitives' src/web/CLAUDE.md` 命中 0 行（既有章节的遗留文本已删除）
  - 章节文本包含 QDS 强制组件 7 项（StatCard / PageHeader / SectionLabel / InlineError / StatusBadge / HelpTip / ShimmerBar）
  - `rg -n 'components/ui/\*\*' src/web/CLAUDE.md` 命中 ≥ 1 行（shadcn 豁免声明）
- **dependencies**: 无
- **预计工作量**: 40 分钟（Round 1 扩充：从 20 分钟 → 40 分钟，因需改写既有章节 + 替换引用；Round 2 保持）

---

### s4 · 迁移 /backtest 路由（**Round 3：bt-* 276 + 11 处 sc-l + 5 处 hm-* = 16 处 factor-research 散落 + hm-* 内联实现**）

- **id**: `s4`
- **name**: `/backtest` 页面 + 8 子组件全量迁移与拆分（含 hm-* 月度热力图内联迁移）
- **描述**:
  - 文件（改 + 拆）：
    - `src/app/backtest/page.tsx`（1754 → <700，按 §3.5.1 拆）
    - `src/app/backtest/components/PerformanceTab.tsx`（2059 → <700，按 §3.5.2 拆出 `performance/` 子目录 6 个子组件）
    - `src/app/backtest/components/TradesTab.tsx`（847 → 拆 TradesFilters / TradesTable / TradePnlSparkline）
    - `src/app/backtest/components/OverviewTab.tsx`（817 → 拆 OverviewKpis / OverviewEquityChart / OverviewStats / **OverviewMonthlyHeatmap**，后者承载 hm-* 5 处内联迁移）
    - `src/app/backtest/components/OverviewGreyTab.tsx`（677，评估是否合并到 OverviewTab；否则独立拆；Round 3 新纳入 4 处 sc-l 迁移）
    - `src/app/backtest/components/{ReportsTab,RobustnessTab,TearsheetTab,TradeLogTab}.tsx`（内部迁移 + Round 3 精确化 sc-l 散落清理）
  - 迁移内容（按 §3.3 映射表，**Round 3 精确计数**）：
    - 内联 `fontFamily` 15 处（backtest/page.tsx）迁移到 `font-mono` / `font-sans`
    - 内联 `fontSize` 47 处（backtest/page.tsx）+ 2 处（PerformanceTab）按 §3.3.8 字号归一化映射
    - **`bt-*` class 调用 276 处**（backtest/page **144** + OverviewTab **74** + PerformanceTab **28** + RobustnessTab 15 + TradesTab 9 + OverviewGreyTab 6），按 §3.3.3 映射
    - **factor-research 原语散落清理（Round 3 精确化 — 共 16 处）**：
      - **OverviewGreyTab.tsx: L84 / L134 / L220 / L458 共 4 处 `<span className="sc-l ...">`** → `<SectionLabel>` QDS 组件（Round 3 新纳入整文件）
      - **TradesTab.tsx: L162 / L179 / L515 共 3 处 `<span className="sc-l">`** → 同上（r2 漏 L162）
      - **PerformanceTab.tsx: L226 / L1726 共 2 处 `<span className="sc-l">`** → 同上（r2 漏 L226）
      - **TearsheetTab.tsx: L48 / L90 共 2 处 `<span className="sc-l">`** → 同上（r2 漏 L90）
      - **合计 11 处 sc-l**
      - **OverviewTab.tsx: L190 (`hm-grid`) / L192 / L195 / L200 (`hm-label` × 3) / L206 (`hm-cell`) 共 5 处 hm-* 月度热力图原语** → **s4 内联 Tailwind grid + CSS custom props 实现**（不依赖 s6，因 research 下 hm-* 调用点为 0，s6 不新建 `<MonthlyHeatmap>` 共享组件；Round 3 锁定决策，§3.3.7.9 详述）；推荐迁移形态：`<div className="grid gap-1" style={{ gridTemplateColumns: "auto repeat(12, 1fr)" }}>` + 内部 `<div className="flex items-center justify-center font-mono text-[0.62rem] text-muted-foreground">` 作为 hm-label，`<div className="flex items-center justify-center font-mono text-[0.7rem] rounded-qds-sm" style={{ background: cellBg(val), color: cellText(val) }}>` 作为 hm-cell
      - **合计 5 处 hm-***
      - **factor-research 散落总计 16 处（11 sc-l + 5 hm-*）**（具体形态优先 QDS 组件，若样式不完全契合则采用 Tailwind 还原；hm-* 无对应 QDS 组件因此全部内联 Tailwind）
    - Recharts `CartesianGrid` 强制 spread `CHART_GRID_STYLE`（PerformanceTab 6+ 处）
    - Recharts `RechartsTooltip` 强制 spread `CHART_TOOLTIP_PROPS`（OverviewTab 2 处）
    - **`ReferenceLine label` 对象 spread `CHART_LABEL_STYLE`**（OverviewTab:684 1 处 + RobustnessTab:353 1 处，共 2 处；**多行形式需 R9 `-U --multiline-dotall` 扫描覆盖**）
    - `bt-cd/bt-cd-header/bt-cd-body` → shadcn `<Card>/<CardHeader>/<CardContent>`（**取代历史 memory `feedback-bt-card-classes.md` 的主张**，详见 1-requirements.md §1.9）
  - 对照 §3.4 preview 矩阵：`preview/component-row.html`（3px stripe）/ `preview/component-kpi.html` / `preview/component-tabs.html` / `preview/component-progress.html` / `preview/component-badges.html` / `preview/component-buttons.html` + `Web UI Kit.html`（完整 frame）
  - 拆分使用 `git mv` 保留 blame
  - **StatusBadge 处理**：禁止直接替换或删除 `components/StatusBadge.tsx`（s11 统一处理；详见 §3.3.9）；本任务中 bt-status 的渲染先维持 `<StatusBadge status={run.status} />` 不变
  - **不修改** `globals.css` 中的 `.bt-*` 定义（s10 负责删除）
- **验收标准**（全部自动化，无手动目测）:
  - `bash src/web/scripts/verify-ds-compliance.sh 2>&1 | rg 'src/app/backtest/'` 无任何违规（**R1/R2/R4/R5/R6/R7/R9/R10/R12/R14** 规则下 backtest 下 0 命中）
  - **Round 3 扩展**：`rg -n '\b(sc-l|hm-grid|hm-label|hm-cell)\b' src/web/src/app/backtest --glob='*.tsx'` 命中 **0 行**（16 处 factor-research 散落已全部迁移）
  - 拆分后所有新文件行数 <700（`wc -l src/web/src/app/backtest/**/*.tsx | sort -n | tail -5` 最大值 <700）
  - `cd src/web && npm run build` 通过
  - `cd src/web && npm run lint` 通过
  - 新建子文件使用 `git log --follow` 能追溯到原文件历史（`git mv` 或 blame 可读）
- **dependencies**: [`s1`, `s2`]
- **预计工作量**: **10 小时**（Round 3：从 9h 上调 1h，16 处 factor-research 散落 + hm-* 内联实现 + OverviewMonthlyHeatmap 新子组件）

---

### s5 · 迁移 /data-catalog 路由（**Round 2：含 types.ts 12 处 + 4 张 sc KPI + 3 处 fsel + JobQueue 预留**）

- **id**: `s5`
- **name**: `/data-catalog` 页面 + 5 子组件 + types.ts 全量迁移
- **描述**:
  - 文件（改，**Round 2 追加 types.ts**）：
    - `src/app/data-catalog/page.tsx`（333 行）
    - `src/app/data-catalog/FetchDialog.tsx`（381）
    - `src/app/data-catalog/DeleteDialog.tsx`（85）
    - `src/app/data-catalog/JobQueue.tsx`（193）
    - `src/app/data-catalog/FilterTabs.tsx`（80）
    - `src/app/data-catalog/CoveragePanel.tsx`（72）
    - **`src/app/data-catalog/types.ts`**（Round 2 新纳入：`TYPE_BADGE_CLS: Record<string, string>` 字典 12 处 value 重写）
  - 迁移内容（**Round 2 精确计数 @ 2026-04-19**）：
    - `dc-*` 调用 **65 处跨 6 文件**：page(23) + JobQueue(14) + FetchDialog(8) + FilterTabs(7) + types.ts(12) + DeleteDialog(1)，按 §3.3.4 映射
    - **types.ts 字典重写**（§3.3.4.1）：`TYPE_BADGE_CLS` 的 12 个 value 从 `"dc-type-kl"` 等 legacy class 改为 Tailwind class 字符串（如 `"bg-qds-info-dim text-qds-info"`）；key 保持不变；调用点 `<Badge className={TYPE_BADGE_CLS[type]}>` 无需修改
    - **factor-research 散落清理（Round 2 新增）**：
      - page.tsx L240-243 的 4 张 KPI 行（`<div className="sc"><div className="sc-l">数据集</div><div className="sc-v">...</div></div>` × 4，含 1 处 `.sc-sub`）→ 迁移为 4 个 `<StatCard>` QDS 组件；若 StatCard API 不支持 `sc-sub`（即次级 label），用 `label` + `value` + `hint` 或 `subtext` prop；不支持则用 Tailwind 还原（`<Card>` + 内部 `<SectionLabel>` + `font-mono text-lg font-semibold` + 可选 `text-[0.58rem] mt-0.5`）
      - page.tsx L252 的 1 处 `<select className="fsel">` → shadcn `<Select>` 组件
      - FetchDialog.tsx L199 / L215 / L230 共 3 处 `<select className="fsel">` → shadcn `<Select>` 组件
    - 内联 `fontFamily` **5 处**（page 3 + FetchDialog 2）
    - 内联 `fontSize` 16 处（page 5 + FetchDialog 8 + DeleteDialog 3）
    - `cg/ca/cr/dim` 独立 token **5 处**（page 4 + DeleteDialog 1，严格扫描 @ 2026-04-19）
    - **JobQueue 4 处 `bt-status bt-status-{queue,done,fail}`（Round 2 新发现散落）**：
      - **处理策略**：s5 任务本身**不**修改 JobQueue 的 bt-status（需要 StatusBadge 扩展后才能正确渲染中文 label 映射）；在 s5 中只处理 JobQueue 的 14 处 dc-* + 零星 fontSize / fontFamily；**4 处 bt-status 保留原样，预留 s11 由 StatusBadge 统一扩展后一并迁移为 `<StatusBadge status="queued|completed|failed|queued" />`**
      - s5 验收标准中对 JobQueue 只断言 R3（dc-*）= 0，**不**断言 R2（bt-*）= 0（R2 容忍 4 处 bt-status 直到 s11 清除）
  - 对照 §3.4 preview：`preview/component-tabs.html`（FilterTabs）/ `preview/component-progress.html`（JobQueue 进度条）/ `preview/component-badges.html`（dc-type-* 7 色徽章）/ `preview/color-semantic.html`（coverage 语义色）/ `preview/component-kpi.html`（page KPI）+ `Web UI Kit.html`（完整装配）
  - 无拆分（文件均 <700 行）
  - 颜色映射参照 `.claude/skills/TinoHelmDS/colors_and_type.css`（token 源）
- **验收标准**:
  - `bash src/web/scripts/verify-ds-compliance.sh 2>&1 | rg 'src/app/data-catalog/'` 无违规 **（R3 / R14 / R12 / R1 均 0 命中；R2 允许 JobQueue 4 处 bt-status 暂存，由 s11 处理）**
  - `cd src/web && npm run build` 通过
  - FetchDialog 的 7 个数据类型徽章使用 `<Badge className={TYPE_BADGE_CLS[type]}>...</Badge>` 形态，`TYPE_BADGE_CLS` 的 value 已全部改为 Tailwind class 字符串
  - **Round 2 新增**：`rg 'dc-type-[a-z]+' src/web/src/app/data-catalog` 命中 0 行（types.ts 字典字符串已完全重写）
  - **Round 2 新增**：`rg '\bsc\b|\bsc-l\b|\bsc-v\b|\bsc-sub\b|\bfsel\b' src/web/src/app/data-catalog --glob='*.tsx'` 命中 0 行（4 张 KPI + 4 处 fsel 散落已迁移；types.ts 不含 JSX，豁免）
  - data-catalog page.tsx 的 4 张 KPI 卡视觉对照 `preview/component-kpi.html`：数字字体 mono + section-label accent 橙 + 间距与 preview 一致
- **dependencies**: [`s1`, `s2`]
- **预计工作量**: **4.5 小时**（Round 2：从 3.5h 上调 1h，新增 types.ts 重写 + 4 张 KPI 迁移 StatCard + 4 处 fsel → Select + 预留 JobQueue 4 处 bt-status 到 s11 的描述明确）

---

### s6 · 迁移 /research 与 /research/report（**新关键路径；Round 2 拆分门槛硬约束**）

- **id**: `s6`
- **name**: `/research` + `/research/report/[id]` 迁移 + 拆分 + **factor-research 子系统 85 class 全量迁移**
- **描述**:
  - 文件（改 + 拆）：
    - `src/app/research/page.tsx`（991 → <700，按 §3.5.4 拆 **6 个子组件**：ResearchDatasetPanel / ResearchFactorList / ResearchConfigPanel / ResearchResultPanel / ResearchChartPanel / ResearchJobQueue）
    - `src/app/research/report/[id]/ReportClient.tsx`（757 → <700，按 §3.5.5 拆 **4 个子组件**：ReportKpiGrid / ReportIcChart / ReportLongShortChart / ReportFactorTable；**Round 2：移除 ReportHeader.tsx**因 `.rpt-*` 调用点 0）
    - `src/app/research/report/[id]/page.tsx`（9 行薄壳，不改）
  - 迁移内容（精确计数 @ 2026-04-19）：
    - 内联 `fontFamily` 3 处（research/page）+ 若干（ReportClient）
    - 内联 `fontSize` 16 处（research/page）+ 2 处（ReportClient）
    - `cg/ca/cr/dim` 独立 token **9 处**（research/page，严格扫描）
    - **factor-research 子系统原语 47 处 className 实例**（research/page；按家族：`.cd/.cd-h/.cd-b` 17 处 + `.sl` 若干 + `.ctbl` 若干 + `.fsel` 4 处 + `.acc-*` / `.param-*` / `.cfg-*` 等）+ 若干（ReportClient `.ctbl`/`.dtab`），按 §3.3.7 映射表全量迁移：
      - `.cd/.cd-h/.cd-b` → shadcn `<Card>/<CardHeader>/<CardContent>`
      - `.sc/.sc-l/.sc-v/.sc-sub` → `<StatCard>` QDS 组件（或显式 Tailwind）
      - `.sl` → `<SectionLabel>` QDS 组件
      - `.btn/.btn-p/.btn-a/.btn-o/.btn-g` → shadcn `<Button variant>`（**Round 2 锁定：`.btn-p` 全部迁移为 `variant="default"` accent 橙，不保留绿色**，与 DS `preview/component-buttons.html` 规则一致）
      - `.fi/.fsel/.fl/.fg/.frow` → shadcn `<Input>/<Select>/<Label>`
      - `.acc-*/.factor-limit` → shadcn `<Accordion>` 或 `<Disclosure>`
      - `.cfg-*/.param-*` → 专用 `<ParamRow>` 子组件
      - `.verdict*/.ctbl/.hist-pager` → shadcn `<Badge>/<Table>/<Pagination>`
      - `.turn-*/.hm-*/.wf-*/.hbar-*` → 专用子组件（MonthlyHeatmap/WaterfallBar/HBar）
      - `.spinner/.factor-dot/.data-avail/.tip/.badge` → Lucide 图标 + Tailwind + `<HelpTip>`
      - `.empty` → 既有 `<EmptyState>` 组件
      - `.dtab/.tab-bar` → shadcn `<Tabs>/<TabsList>/<TabsTrigger>`
      - `.rpt-*` → **Round 2：ReportClient 实测 0 调用，本任务无 .tsx 迁移工作；仅 s10 删除 globals.css L1971-1987 的 CSS 定义**
    - `<Legend wrapperStyle={{…}} />` 2 处（research/page Legend：~L802、~L868）→ spread `CHART_LEGEND_STYLE`
    - **`<ReferenceLine label={{…}}>` 对象**：ReportClient:504-508（**多行形式**）1 处 → spread `CHART_LABEL_STYLE`（**不是 `<Label>` 子组件**；R9 `-U --multiline-dotall` 扫描覆盖）
  - 对照 §3.4 preview：`preview/type-headings.html` / `preview/type-data.html` / `preview/component-kpi.html` / `preview/color-text-hierarchy.html` / `preview/type-section-label.html` / `preview/component-tabs.html` / `preview/component-inputs.html` / `preview/component-badges.html` + `Charts Spec.html`（Recharts）+ `Web UI Kit.html`（完整 dashboard）
- **验收标准**:
  - `bash src/web/scripts/verify-ds-compliance.sh 2>&1 | rg 'src/app/research/'` 无违规 **（R14 在 research 下 0 命中）**
  - 所有新/改文件 <700 行（research/page.tsx 拆后应 < 400）
  - `cd src/web && npm run build` 通过
  - 图表样式在 dark mode 下与原始一致（Legend 文字 fontSize 0.62rem、mono、--t1 色；ReferenceLine label fontSize 统一 10，字体保持 Recharts 默认）
  - **无 factor-research 原语残留**：`rg -n '\b(sc|cd|sl|fl|fi|fsel|ctbl|dtab|hm-|wf-|hbar|acc-|param-|cfg-|rpt-|turn-|verdict|factor-dot|data-avail|action-row)\b' src/web/src/app/research --glob='*.tsx' | rg 'className'` 命中 0 行
- **dependencies**: [`s1`, `s2`]
- **预计工作量**: **10 小时**（保持 Round 1 估算；**Round 2 硬约束**：启动 4h 后若 research/page 的 6 子组件尚未完成 3 个，必须拆出 s6b 处理 ReportClient — 见下方"子任务拆分风险"节）

---

### s7 · 迁移 /trading 与子组件（**Round 2：40 处 --accent-* 含 TabNav + 10 variant**）

- **id**: `s7`
- **name**: `/trading` 页面 + 8 components + 5 tabs 全量迁移
- **描述**:
  - 文件（改）：
    - `src/app/trading/page.tsx`（454）
    - `src/app/trading/components/{ActionBar,FillsStream,OrdersPanel,PositionsTable,StrategyDetailPanel,StrategyPanel,TabNav,TopBar}.tsx`（共 1696 行；**Round 2 TabNav 纳入**）
    - `src/app/trading/components/tabs/{LogsTab,OrdersTab,OverviewTab,RiskTab,StrategiesTab}.tsx`（共 1568 行）
  - 迁移内容（**Round 2 精确验证 @ 2026-04-19**）：
    - **内联 `fontFamily` 5 处**（trading/page.tsx 实测 5 处而非 6 处）
    - 内联 `fontSize` 7 处（trading/page 5 + StrategiesTab 2）
    - **`trading/` 全目录 `bt-\*` 调用 0 处**（`rg -c '\bbt-[a-z0-9-]+' src/web/src/app/trading` = 0，Round 0 虚构"4 处 bt-\*"已修正）
    - **未定义 `--accent-*` 变量 s7 承担 40 行 / 51 次调用**（**Round 3 实测 @ 2026-04-19，按 §3.3.8 固定映射表迁移至 11 variant；purple 锁定 text-primary，purple-20 锁定 bg-qds-accent-dim，无 case-by-case 评估**）：
      - StrategyPanel.tsx: 11 行（主要 green/red/amber/blue）
      - OrdersPanel.tsx: 9 行
      - ActionBar.tsx: 6 行
      - FillsStream.tsx: 5 行（含 purple 1 行 → text-primary；purple-20 1 行 → bg-qds-accent-dim；green-10 1 行 → bg-qds-success-dim）
      - TopBar.tsx: 4 行
      - PositionsTable.tsx: 4 行（含 blue-20 1 行 → bg-qds-info-dim）
      - **TabNav.tsx: 1 行**（`--accent-blue` → `text-qds-info`）
      - **合计 s7 负责 40 行 / 51 次调用**；**s9 EditorClient.tsx 另 15 行 / 16 次调用**（§3.3.8 总数 67 次调用跨 8 文件）
    - `cg/ca/cr/dim` 独立 token 零星清理（严格扫描下 trading 无命中 — 以 R4 输出为准）
    - RiskTab:187 的 `<ReferenceLine label={{ value: "阈值", fill: "var(--warn)", fontSize: 9 }}>` → spread `CHART_LABEL_STYLE`（fontSize 9 → 10 随 spread 统一；如需保留 9 则 `{ ...CHART_LABEL_STYLE, fontSize: 9, value: "阈值" }`）
    - RiskTab / trading/OverviewTab / StrategyDetailPanel 的 `<CartesianGrid>` → spread `CHART_GRID_STYLE`
  - 对照 §3.4 preview：`preview/component-sidebar.html`（若有侧边栏）/ `preview/component-tabs.html`（TabNav）/ `preview/component-badges.html`（StatusBadge）+ `Web UI Kit.html`
  - **StatusBadge 处理**：禁止直接替换或删除 `components/StatusBadge.tsx`（s11 统一处理）
- **验收标准**:
  - `bash src/web/scripts/verify-ds-compliance.sh 2>&1 | rg 'src/app/trading/'` 无违规（**R13 覆盖 10 variant 全部通过**）
  - `cd src/web && npm run build` 通过
  - **`rg -n 'var\(--accent-(green\|orange\|red\|amber\|blue\|purple)' src/web/src/app/trading` 命中 0 行**（R13 通过，**排除 --accent-foreground** 豁免）
  - trading/page.tsx 内 `rg -c 'bt-[a-z]' src/web/src/app/trading` = 0（维持现状，验证无遗漏）
  - **Round 2 新增**：`rg 'var\(--accent-' src/web/src/app/trading/components/TabNav.tsx` 命中 0 行
- **dependencies**: [`s1`, `s2`]
- **预计工作量**: **3.5 小时**（Round 3：保持 3.5h；40 行 / 51 次调用 `--accent-*` **按 §3.3.8 固定映射（purple → text-primary，purple-20 → bg-qds-accent-dim）** 无 case-by-case 决策歧义；含 TabNav.tsx 1 行/1 次）

---

### s8 · 迁移 /analytics + /optimization + /orders + /watchlist（小页面打包）

- **id**: `s8`
- **name**: 4 个中型页面批量迁移
- **描述**:
  - 文件（改）：
    - `src/app/analytics/page.tsx`（540）
    - `src/app/optimization/page.tsx`（736，**FR-4.1 明确豁免拆分**，仅内部清理）
    - `src/app/orders/page.tsx`（548）
    - `src/app/watchlist/page.tsx`（465）
  - 迁移内容：
    - analytics：Tooltip spread 3 处（`TOOLTIP_STYLE` → `CHART_TOOLTIP_PROPS`，删除本地 `TOOLTIP_STYLE` 常量声明），ReferenceLine label 若有按 §3.3.5 处理（全仓实测 analytics 下 0 处），cr/ci 等零星清理
    - optimization：扫描确认 + `ca/cr` 若干清理；**不拆**（保守不拆，FR-4.1 豁免）
    - orders：cg/cr 各 1-2 处清理
    - watchlist：扫描确认
  - 对照 §3.4 preview：`preview/color-semantic.html`（语义色使用）/ `preview/component-kpi.html`（analytics 指标卡）/ `preview/component-row.html`（orders 行）+ `Web UI Kit.html`
  - **StatusBadge 处理**：禁止直接替换或删除 `components/StatusBadge.tsx`（s11 统一处理；optimization:13 当前用 `<StatusBadge status={run.status}>` 保持不变）
- **验收标准**:
  - `bash src/web/scripts/verify-ds-compliance.sh 2>&1 | rg 'src/app/(analytics|optimization|orders|watchlist)/'` 无违规
  - `cd src/web && npm run build` 通过
  - analytics 下本地声明的 `TOOLTIP_STYLE` 常量已被删除，改为从 `@/lib/chartTheme` 导入 `CHART_TOOLTIP_PROPS`
  - optimization/page.tsx 行数无增加（豁免拆分但不允许膨胀）
- **dependencies**: [`s1`, `s2`]
- **预计工作量**: 3 小时

---

### s9 · 查缺补漏 / + /strategies + /strategies/[name] + /settings

- **id**: `s9`
- **name**: 4 个标准化程度已高的页面查缺补漏（重点：EditorClient 15 处未定义变量）
- **描述**:
  - 文件（改）：
    - `src/app/page.tsx`（585，首页，RechartsTooltip 1 处 contentStyle 需改为 spread）
    - `src/app/strategies/page.tsx`（754，`ca`/`cr` 零星清理，**FR-4.1 豁免拆分**）
    - `src/app/strategies/[name]/page.tsx`（11 薄壳，不改）
    - `src/app/strategies/[name]/EditorClient.tsx`（200，**15 处未定义 `--accent-*` 变量**按 §3.3.8 映射表按 10 variant 分类迁移；另查 color hex / R6 / R4）
    - `src/app/settings/page.tsx`（332，扫描确认）
    - `src/app/layout.tsx`（字体声明已正确，不改）
  - 迁移内容主要是扫描规则 R4 / R5 / R6 / R10 / R12 / R13 的零星清理
- **验收标准**:
  - `bash src/web/scripts/verify-ds-compliance.sh 2>&1 | rg 'src/app/(page\.tsx|strategies/|settings/)'` 无违规
  - `cd src/web && npm run build` 通过
  - `src/app/page.tsx` 的 RechartsTooltip 使用 `{...CHART_TOOLTIP_PROPS}` spread 形式（唯一形式，不允许 alias）
  - **`rg -n 'var\(--accent-(green\|orange\|red\|amber\|blue\|purple)' src/web/src/app/strategies/\[name\]/EditorClient.tsx` 命中 0 行**（R13 通过）
  - EditorClient 的 hex 颜色（若有）替换为 Tailwind 语义类
- **dependencies**: [`s1`, `s2`]
- **预计工作量**: 2.5 小时（Round 1：从 2h 上调 0.5h，EditorClient 15 处 --accent-* 清理工作量明确化；Round 2 保持）

---

### s10 · 删除 globals.css 遗留 class 定义（含 factor-research 子系统）

- **id**: `s10`
- **name**: 从 `globals.css` 删除 `.bt-*` / `.dc-*` / `.cg/.ca/.cr/.ci/.dim/.mono` / **factor-research 全部 85 个 class** 定义
- **描述**:
  - 文件：`src/web/src/app/globals.css`
  - **前置强制检查**：第一步必须运行 `bash src/web/scripts/verify-ds-compliance.sh --preflight-before-css-delete`（Round 2 扩展至 R1-R10+R12+R13+**R14**），exit 0 才能启动删除。若 exit 1，按以下回退 target 映射回到对应 sN 任务补漏：

    **Round 2 新增 — preflight 失败回退 target 映射**：

    | preflight 输出的违规路径前缀 | 回退 target |
    |---|---|
    | `src/app/backtest/` | s4 |
    | `src/app/data-catalog/` | s5 |
    | `src/app/research/` 或 `src/app/research/report/` | s6 |
    | `src/app/trading/` | s7 |
    | `src/app/analytics/` / `src/app/optimization/` / `src/app/orders/` / `src/app/watchlist/` | s8 |
    | `src/app/page.tsx` / `src/app/strategies/` / `src/app/settings/` | s9 |
    | `src/components/` / 其它 | s11（全仓补漏） |

    每次回退后执行 `bash verify-ds-compliance.sh --fix-hint` 获取迁移建议，完成后再次运行 preflight；如此循环直至 preflight exit 0。
  - 删除范围：
    - **L532 起 `.bt-*` 家族**（134 selector，约 400 行）
    - **L1640 起 `.dc-*` 家族**（76 selector，约 250 行，注意 `.dc-sl` 是首个；L1659 是 `.dc-filter-strip`）
    - **L1856 单行**：`.mono{…}.dim{…}.cg{…}.cr{…}.ca{…}.ci{…}` **必须作为原子操作整行删除**，不允许留存任一 class 定义
    - **L1853-1987 factor-research 子系统**（98 unique selector / 顶层约 85 个 class / 约 135 行，从"Factor Research — pixel-perfect"注释到文件末尾）
  - 操作要领：
    - 删除后 `globals.css` 应减少约 780 行（从 1987 → 约 1210）
    - 保留 `globals.css` 的 token 层（`:root` / `html.light` 内的 `--*` 定义）、shadcn `@theme inline`、动画 `@keyframes`、`qds-*` 业务组件 class（`qds-input` / `qds-card` / `qds-stat` / `qds-section-label` / `qds-table` 等）
    - **本任务选 A（全迁移），不保留任何 factor-research 子系统 class**。此前规划的 "shared primitives：`.btn/.btn-p/.btn-o/.btn-d`、`.sc/.sc-l/.sc-v`、`.fl/.fi/.fsel`、`.list`、`.empty`" 保留清单已取消，与"全迁移"决策一致
  - 不删除 `--font-u` / `--font-d` 别名（保留以保护 chartTheme 常量层；业务代码迁移后直接引用应清零，但 token 别名留作 defensive 层 + 供 CHART_LEGEND_STYLE 间接使用）
- **验收标准**:
  - `globals.css` 行数：**1210 ± 50**（以删除定义块为准，而非逐行计数）
  - `rg -n '^\.bt-' src/web/src/app/globals.css` 0 命中
  - `rg -n '^\.dc-' src/web/src/app/globals.css` 0 命中
  - `rg -nU '(?:^|[;\}])\s*\.(cg|ca|cr|ci|dim|mono)\s*\{' src/web/src/app/globals.css` 0 命中（非行首锚定，覆盖 L1856 单行组合）
  - `rg -n '^\.(sc|cd|sl|fl|fi|fsel|ctbl|dtab|acc-|param-|cfg-|hm-|wf-|hbar|rpt-|turn-|verdict|factor-dot|data-avail|action-row|spinner|tip|badge|frow|fg|explorer|config-panel|result-panel)' src/web/src/app/globals.css` 0 命中
  - `bash src/web/scripts/verify-ds-compliance.sh` 整仓 exit 0（R11 + R14 通过）
  - `cd src/web && npm run build` 通过（Tailwind JIT 重建 CSS）
  - 保留的 class 中 `.qds-*` 家族（input / card / stat / section-label / table 等）仍在且未被误删：`rg -n '^\.qds-' src/web/src/app/globals.css | wc -l` ≥ 15
- **dependencies**: [`s4`, `s5`, `s6`, `s7`, `s8`, `s9`]
- **预计工作量**: 1.5 小时（Round 1：从 1h 上调 0.5h；Round 2 保持）

---

### s11 · 全仓库扫描补漏 + StatusBadge 统一（**Round 2：含 JobQueue 4 处 bt-status**）

- **id**: `s11`
- **name**: 跨文件违规捕获与清理 + StatusBadge API 扩展 + JobQueue bt-status 统一迁移
- **描述**:
  - **Step 11a（扫描补漏）**：运行 `bash src/web/scripts/verify-ds-compliance.sh --fix-hint` 全仓输出
    - 逐条处理 s4-s9 遗漏的跨文件违规（可能出现在 `components/` / `providers/` / `hooks/` 等非路由文件）
    - 典型可能点（**baseline 断言 0 违规**，通过 `rg -c 'bt-\|dc-' src/web/src/components` = 0 + `rg -c 'var\(--font-[ud]\)' src/web/src/components` = 0 已预验证 @ 2026-04-19）：
      - `src/web/src/components/*.tsx`（Sidebar / TopBar / StatusBar / FillTicker / IdBadge / EmptyState / ConfirmModal / ThemeToggle / ErrorBoundary / NotificationListener）
      - `src/web/src/components/ui/*`（shadcn 原语通常不改；R10 / dark: 已豁免）
      - `src/web/src/hooks/*`（通常无 UI，baseline 0 命中）
      - `src/web/src/providers/*`（通常无 UI，baseline 0 命中）
    - 不修改 `src/web/src/components/qds/*` 的其它 6 个组件（StatCard/PageHeader/SectionLabel/InlineError/HelpTip/ShimmerBar — 已就绪）
    - 不修改 `src/web/src/components/motion/*`（非迁移范围）
  - **Step 11b（StatusBadge 统一，§3.3.9 决策）**：
    - 改写 `src/web/src/components/qds/status-badge.tsx`：扩展 `Status` union 加入 `queued/running/completed/failed/cancelling/cancelled/done` 7 个键，新增 `locale: "en" | "zh"` prop，中英双语 label map
    - 改写 `src/web/src/components/StatusBadge.tsx`：改为 barrel re-export（`export { StatusBadge } from "@/components/qds/status-badge"`），保持向后兼容
    - 迁移 `src/web/src/app/data-catalog/JobQueue.tsx` L173 / L176 / L181 / L185 的 4 处 `<span className="bt-status bt-status-{queue,done,fail}">` → `<StatusBadge status="queued|completed|failed|queued" locale="zh" />`（注意 L185 "已取消" 原为 bt-status-queue，应迁移为 `status="cancelled"`，验证 UI 语义正确）
    - **Round 3 重构（遵守用户 MUST 规则）**：删除原 r2 的"逐页目测"作为执行步骤；barrel re-export 后的视觉差异判定由**主 agent 在 verify phase 呈现给用户** — 若用户判定差异过大需 fallback，由主 agent 在 PR review 时派 agent post-task 回迁（工作量追加 0.5-1h）；**不阻塞 s11 subtask 完成**。s11 的交付物仅为：(a) StatusBadge 扩展 + barrel re-export 代码，(b) JobQueue 4 处 bt-status 迁移为 `<StatusBadge>`，(c) `npm run build` / `npm run lint` 通过，(d) R2/R11/R14 扫描通过 — 全部自动化判定
    - 程序化验证 `src/app/page.tsx:130`（传 `run.status` 可能含 `completed/cancelling/cancelled`）与 `src/app/optimization/page.tsx:13` 编译通过、类型正确（不做浏览器视觉目测）
    - `cd src/web && npm run build` 必须通过（TypeScript 类型不再报错）
- **验收标准**:
  - `bash src/web/scripts/verify-ds-compliance.sh` 整仓 exit 0（R1-R14 全部通过；R11 依赖 s10 完成）
  - `cd src/web && npm run build` 通过
  - `cd src/web && npm run lint` 通过
  - `rg -n 'bt-|dc-|var\(--font-[ud]\)' src/web/src --glob='!**/globals.css' --glob='!**/chartTheme.ts'` 输出中没有任何 `className` / `style` 相关命中（只允许注释 / 文档字符串内的提及）
  - `rg -n 'var\(--accent-(green\|orange\|red\|amber\|blue\|purple)' src/web/src` 命中 0 行（R13 全仓通过）
  - **Round 2 新增**：`rg -n '\bbt-status\b' src/web/src/app/data-catalog/JobQueue.tsx` 命中 0 行（4 处 bt-status 已迁移为 `<StatusBadge>`）
  - **StatusBadge 双实现合并完成**：`rg -n "^export (function|const|type) StatusBadge" src/web/src/components/StatusBadge.tsx` 应为 re-export；`components/qds/status-badge.tsx` 的 Status union 包含全部 7 个键；调用点 `page.tsx` 与 `optimization/page.tsx` 渲染无报错
- **dependencies**: [`s4`, `s5`, `s6`, `s7`, `s8`, `s9`]
- **预计工作量**: 3 小时（Round 3：保持 3h；subtask 层仅做代码改动 + 扫描验证，不含视觉目测；若 verify phase 用户判定需 fallback，post-task 回迁 0.5-1h）

---

### s12 · 双主题验证 + 最终扫描 + CLAUDE.md 定稿

- **id**: `s12`
- **name**: dark / light 双主题验证 + 最终合规断言 + 文档定稿
- **描述**:
  - **Step 1 扫描全通过**：`bash src/web/scripts/verify-ds-compliance.sh` exit 0
  - **Step 2 双主题扫描**：`bash src/web/scripts/verify-ds-compliance.sh --mode both-themes` exit 0（排除 `components/ui/**` + `components/qds/**`）
  - **Step 2.5 AC-2 fallback 路径处理**：若 verify 阶段未接入视觉回归工具，明确 AC-2 的操作化退化路径 — 对照矩阵 §3.4 + `.claude/skills/TinoHelmDS/preview/*.html` + `Web UI Kit.html` 的文档化检查写入 `src/web/CLAUDE.md`「标准化后的约束」章节
  - **Step 3 构建与 lint**：`cd src/web && npm run build` + `npm run lint` 全通过
  - **Step 4 既有字体校验**（不动的脚本做 smoke test）：`bash src/web/scripts/check-grep-fonts.sh` + `node src/web/scripts/verify-build-fonts.mjs` 全通过
  - **Step 5 CLAUDE.md 定稿**：基于 s4-s11 实际迁移过程中发现的调整点，更新 s3 创建的草稿章节
    - **Step 5a**：再校对既有「QDS CSS Classes (globals.css)」章节改写后内容（确认无遗漏 `bt-*/dc-*/Shared primitives` 残留文本）
    - **Step 5b**：更新「标准化后的约束」新章节，至少补充：
      - 实际删除的 `globals.css` 行数（预期 780 行）
      - 新增常量清单（`CHART_LEGEND_STYLE` / `CHART_LABEL_STYLE`，含 Round 2 `CHART_LABEL_STYLE` 不含 fontFamily 的决策说明）
      - 实际发现的边缘情况与约定
      - **Historical Notes 区块**：明确作废 `feedback-bt-card-classes.md` / `feedback-use-existing-css.md` / `feedback-pixel-perfect.md` / `feedback-css-class-naming.md` 的主张（Round 2 降语：标注"interview.md 第 4 轮选择隐含此方向；主 agent 将向用户确认并更新 memory"）
      - **视觉参考源声明**：`.claude/skills/TinoHelmDS/` 下 24 个文件（21 preview + Web UI Kit.html + Charts Spec.html + QDS Pitch Deck.html），明确 `docs/ui/` 不存在
      - **shadcn 原语豁免声明**：`src/web/src/components/ui/**` 对 R10 / `dark:` 前缀的豁免
    - **Step 5c**：确认「Key Conventions」中 `docs/ui/` 引用已清除
  - **Step 6 行数核查**：所有业务 `.tsx` 文件 < 700 行（strategies/optimization 豁免）
  - **Step 7 light 主题 globals.css 断言**：`rg -U 'html\.light\s*\{[\s\S]*?--bg-s|--t0|--bd' src/web/src/app/globals.css` 确认 `html.light` 作用域存在核心 token override
- **验收标准**:
  - 以上 7 步全部通过
  - `wc -l src/web/src/**/*.tsx | sort -rn | head -10` 前 10 大文件全部 < 700 行（strategies/optimization 豁免但实际应已拆分或保持不变）
  - `src/web/CLAUDE.md` 「标准化后的约束」章节含 Step 5b 列出的全部补充内容
  - `rg -n 'docs/ui/qds-' src/web/CLAUDE.md` 命中 0 行
  - `rg -n 'Historical Notes|feedback-bt-card-classes\|feedback-use-existing-css' src/web/CLAUDE.md` 命中 ≥ 1 行
  - `cat src/web/src/app/globals.css | wc -l` 返回 **1160-1260** 之间
  - 最终交付物的 git diff summary 写入 `.cage/tasks/.../verify.jsonl`（cage CLI 的 verify 流程自动完成）
- **dependencies**: [`s10`, `s11`]
- **预计工作量**: 2 小时（Round 1：从 1.5h 上调 0.5h；Round 2 保持）

---

## 并行分组（parallel_groups）

按依赖 DAG 拓扑排序，最小化总时间。分组未变（Round 2 未引入新依赖关系）：

```json
[
  ["s1", "s2", "s3"],
  ["s4", "s5", "s6", "s7", "s8", "s9"],
  ["s10", "s11"],
  ["s12"]
]
```

说明：
- **第 1 组**（s1 / s2 / s3）：三者互不依赖。s1 改 chartTheme（独立），s2 新增脚本（独立），s3 改写/追加 CLAUDE.md（独立）。
- **第 2 组**（s4-s9）：6 个页面迁移任务互不依赖（修改文件集无交集）。可真正并行派 6 个 agent。
  - **关键路径：s4 (10h) 与 s6 (10h) 并列瓶颈**（Round 3 调整；s4 因 hm-* + 16 处散落工作量上调至 10h）
  - 约束：s4/s7/s8/s9 都**禁止直接修改 StatusBadge**（delegate to s11）
  - **Round 3 约束重申**：s4 负责 backtest 下 16 处 factor-research 散落（11 处 sc-l + 5 处 hm-*；OverviewTab 的 hm-* 由 s4 独立内联 Tailwind grid 实现，不依赖 s6）；s5 负责 data-catalog/page 4 张 KPI 迁 StatCard + FetchDialog 3 处 fsel 迁 Select + JobQueue 4 处 bt-status 预留 s11；三者文件集完全隔离
  - **Round 3 并发上限声明**：wave B 含 6 个任务（s4-s9）；**若主 agent 派遣工具并发上限为 5**，则 s9（2.5h）排队在 s4-s8 任一完成后启动 — 因 s9 工作量 (2.5h) << s4/s6 关键路径 (10h)，排队对总时长无实质影响；若派遣工具支持 6 并发（如 cage 框架无限制），则全部同时启动
- **第 3 组**（s10 / s11）：s10 改 globals.css、s11 改 `components/qds/status-badge.tsx` + `components/StatusBadge.tsx` + data-catalog/JobQueue.tsx（bt-status 迁移）+ 零星 components —— 文件集无交集，可并行。两者都依赖 s4-s9 全部完成。
- **第 4 组**（s12）：单任务，需要 s10 / s11 的最终产物做全仓扫描与文档定稿。

## 总工作量估算（**Round 3 更新**）

- 波次 A: max(10min, **2h**, 40min) = **2h**
- 波次 B（并行）: max(**10h** s4, **4.5h** s5, **10h** s6, **3.5h** s7, 3h s8, 2.5h s9) = **10h**（**关键路径 s4 与 s6 并列**）
- 波次 C（并行）: max(1.5h s10, **3h** s11) = **3h**
- 波次 D: **2h**

**理论关键路径**: 波次 A (2h) + 波次 B max(10h) + 波次 C max(3h) + 波次 D (2h) = **17h（约 2.1 工作日）**。串行执行 ≈ 41.5h（约 5 工作日）。

**Round 3 关键路径分析**：
- s4 与 s6 并列最大任务（都是 10h），波次 B 关键路径保持 10h
- 总关键路径 17h 保持（s4 9h → 10h 不影响 max 值，因 s6 已经是 10h）；但**波次 B 的鲁棒性下降** — 任一大任务超时都会单点阻塞
- **稳健策略**：s6 硬约束（4h 未完成 3 子组件则拆 s6b） + **Round 3 新增** s4 硬约束（4h 未完成 3 主文件则拆 s4b），两个大任务都有动态拆分门槛

**关键路径演化**：
- Round 0：s4 backtest 是最大任务（8h），关键路径 11.25h
- Round 1：s6 research（因 factor-research 85 class 全量迁移 + 6 子组件拆分）上升为最大任务（10h），关键路径 16h
- Round 2：s6 保持 10h 关键路径；s2 / s4 / s5 / s7 / s11 工作量因规则扩充、散落清理、types.ts、TabNav、JobQueue bt-status 等项目而上调；总关键路径 17h
- **Round 3**：s4 因 16 处 factor-research 散落（11 sc-l + 5 hm-* 内联实现 + OverviewMonthlyHeatmap 新子组件）从 9h → 10h，与 s6 **并列**关键路径；总关键路径保持 17h（max 规则），但波次 B 鲁棒性下降，需 s4 + s6 双硬约束

## 风险缓解复述（来自 §3.8）

- **不允许并行 agent 修改同一文件**：波次 B 每个子任务的文件集完全隔离，已在描述中严格划分目录边界。
- **s10 必须在 s4-s9 全部完成后，且必须 `--preflight-before-css-delete` exit 0 后启动**：否则删除 class 定义会导致调用点破坏。**Round 2 强化**：preflight 包含 R14，若 factor-research 散落未完成迁移，preflight 自动拦截并提示按回退 target 映射回到对应 sN。
- **s12 必须最后**：需要最终稳定态做文档定稿。
- **s4/s7/s8/s9 禁止修改 StatusBadge**：避免 TypeScript 破坏；s11 统一扩展 API 后兼容所有调用点；**Round 2 新增**：s5 的 JobQueue 4 处 bt-status 也预留 s11 处理。
- **R4 / R6 / R7 / R8 / R9 / R14 扫描规则**：必须通过 `--selftest` 才能在 CI 投入使用，防止误报回潮。

## 子任务拆分风险（**Round 3 扩展至 s4**）

### s6 动态拆分门槛（Round 2 硬约束，保持）

Architect r1/r2 建议 "允许 s6 进一步拆分为 s6a/s6b"：
- **s6a**：research/page.tsx 独立拆分 + factor-research 原语迁移（~7h）
- **s6b**：ReportClient.tsx 独立拆分 + 若干原语 + ReferenceLine label（~3h）

**硬约束**：
- **若 executor 在 s6 启动 4h 后尚未完成 research/page 的 6 个子组件中 3 个的迁移**，**必须**拆出 s6b 处理 ReportClient（independent），s6a 继续 research/page
- 拆分决策由 executor 在运行时记录在 execute.jsonl，parallel_groups 对此场景视为单任务（因 s6a + s6b 同属波次 B，其它任务不依赖；可再启动一个 agent 并行跑 s6b）
- 此硬约束的目的：避免 s6 单点超时导致整体关键路径阻塞 > 12h；当 s6 超时发生时，有明确行动指南而非等待

### s4 动态拆分门槛（**Round 3 新增**）

Architect r3 / Critic r3 建议 "允许 s4 进一步拆分为 s4a/s4b"（当 16 处散落工作量溢出时）：
- **s4a**：page.tsx 拆分 + PerformanceTab 拆分 + bt-* 主体迁移（~7h）
- **s4b**：backtest/components 散落处理（OverviewGreyTab / TradesTab / TearsheetTab sc-l 11 处 + OverviewTab hm-* 5 处内联） + sc-l 迁移为 SectionLabel（~3h）

**硬约束**（与 s6 对称）：
- **若 executor 在 s4 启动 4h 后尚未完成 page.tsx + PerformanceTab + OverviewTab 三个主文件迁移**，**必须**拆出 s4b 处理 backtest/components 散落（independent）
- 拆分决策由 executor 在运行时记录在 execute.jsonl；s4a 与 s4b 同属波次 B，互不依赖，可由再启动的 agent 并行跑 s4b
- 目的：避免 s4 单点超时导致整体关键路径阻塞；s4 工作量 10h 已经与 s6 并列，若继续膨胀将单独成为瓶颈
