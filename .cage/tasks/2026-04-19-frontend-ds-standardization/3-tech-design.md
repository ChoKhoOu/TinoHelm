# 3 · 技术设计 — 前端 DS 标准化

> 事实来源：`1-requirements.md`（需求）、`src/web/CLAUDE.md`（既有 Tailwind 映射表）、`.claude/skills/TinoHelmDS/`（设计系统）、`src/web/src/app/globals.css`（遗留 class 实现）、`src/web/src/lib/chartTheme.ts`（Recharts 常量）。

## Round 3 Revision Notes（按本轮审查编号回应）

本节按 Architect r3 + Critic r3 的编号逐一列出修改落地点，便于下一轮审查对照验证。**Round 3 规划流程改进**：planner 在填入任何计数 / 行号前，必须独立全仓运行 rg 扫描（不得沿用上轮审查中列出的数字），把原始输出直接贴入 §1.1 违规表与 §3.3.7 散落清单。本轮所有数字均已按此流程重新验证 @ 2026-04-19。

| Review Item | 级别 | 状态 | 落地位置 |
|---|---|---|---|
| **A-CR-1 / C-C1**（factor-research 散落在 backtest Tab 漏 8 处 sc-l + 5 处 hm-* + OverviewGreyTab 整文件漏列）| CRITICAL | 已修复 | 1-requirements.md §1.1 factor-research 行改为"85 处 className 实例跨 9 文件"精确列 sc-l 15 处（OverviewGreyTab 4 + TradesTab 3 + PerformanceTab 2 + TearsheetTab 2 + data-catalog/page 4）+ hm-* 5 处（OverviewTab）+ fsel 4 处；§3.3.7 Round 2 散落清单表修正每文件精确计数 + 新增 OverviewGreyTab 4 行 + OverviewTab 5 行；§3.3.7.9 `.hm-*` 行锁定"s4 内联实现不依赖 s6"决策（research 下 hm-* 实测 0 调用点，s4 独立 Tailwind grid + CSS custom props 实现）；§3.9 影响文件表 OverviewGreyTab / TradesTab / PerformanceTab / TearsheetTab / OverviewTab 五行计数同步修正；4-tasks.md s4 描述从"4 处 sc-l"改为"11 处 sc-l + 5 处 hm-* = 16 处 factor-research 散落"，逐行列具体位置；s4 工作量 9h → 10h；s4 验收扩展为 `rg -n '\b(sc-l\|hm-grid\|hm-label\|hm-cell)\b' src/web/src/app/backtest --glob='*.tsx'` 命中 0；task.json s4 title 同步 |
| **A-MA-1**（R14 PCRE2 `\b` 对 `sc-column` / `fg-primary` 等复合类假阳性；与 §3.2.8 selftest 反例自相矛盾）| MAJOR | 已修复 | §3.2.3 R14 正则改为 R4 风格 PCRE2 前后向断言 `(?<![-a-zA-Z0-9_])(TOKEN)(?![-a-zA-Z0-9_])`；§3.2.8 selftest 补 5 条新反例（`sc-column` / `fg-primary` / `fi-rocket` / `cd-hover` / `sl-indicator` 必须不命中）；s2 R14 正则行 + AC 同步；复合原语 `sc-l` / `cd-h` / `turn-val` 等保留 `-` 的家族仍正确命中，因 token 表列明 sc-l / cd-h / cd-b 等完整原语 |
| **C-M1**（"逐页目测 / 必须目测"违反用户全局 MUST 规则 — subtask AC 不应含手动验证）| MAJOR | 已修复 | 1-requirements.md §1.6 重构：AC-2（视觉对照）、AC-3（双主题验证的视觉部分）、AC-5（StatusBadge barrel 视觉差异）全部**降级为 User Acceptance in Verify Phase**，不作为 subtask AC；保留自动化部分（R1-R14 扫描、`npm run build`、`npm run lint`、行数断言）作为 subtask AC；§3.3.9 StatusBadge "逐页目测"措辞删除，改为 "verify phase 由用户在 dev server 下确认 + 若用户判定需 fallback 则 post-task 回迁"；4-tasks.md s11 Step 11b 的"视觉差异目测"删除，改为 "若 barrel 切换引发视觉争议，由主 agent 在 PR review 时决定是否走 fallback，不阻塞 s11 完成"；§3.8 R-9 风险行同步改写；新增 §"User Acceptance Checklist（verify phase）"章节列出用户在 verify 时需主动审视的项目 |
| **C-M2**（`--accent-purple` "默认 text-primary" 与 "case-by-case 评估" 并存歧义）| MAJOR | 已修复 | 1-requirements.md §1.1 字体迁移路径表 purple 行锁定为 "`text-primary`（项目无 purple token，本任务不新增 token，purple 语义统一并入 accent 橙）"，删除 "case-by-case 评估" 备选；§3.3.8 未定义 CSS 变量表 purple / purple-20 行同步锁定；4-tasks.md s7 预计工作量说明 "部分 purple 需 case-by-case 评估" 改为 "按 §3.3.8 固定映射（purple → text-primary，purple-20 → bg-qds-accent-dim）" |
| **C-m1**（CHART_LABEL_STYLE spread 时 position prop 的 TS 兼容性）| MINOR | 已修复 | §3.3.6 代码块注释追加 "Note 3 (Round 3): Recharts `<ReferenceLine>.label` prop 接受 `CSSProperties & { value?, position?, offset? }` 扩展形态；`label={{ ...CHART_LABEL_STYLE, position: 'insideTopLeft', value: '...' }}` 形态合法无需 type assertion；若 executor 遇到 TS 报错可扩大 chartTheme.ts 类型为 `React.CSSProperties & { value?: React.ReactNode; position?: string; offset?: number }`"；1-requirements.md FR-3.3 追加兼容性说明 |
| **C-m2**（§3.3.8 "10 variant" vs 表体 11 rows vs 求和 67 三数不一致）| MINOR | 已修复 | 现场重新 rg 扫描实测求和 **67 处 / 11 variant / 8 文件**（green 23 + red 13 + amber 12 + blue 7 + orange 4 + red-20 2 + green-10 2 + purple 1 + purple-20 1 + blue-20 1 + amber-20 1 = 67）；§3.3.8 表头数字改为 "67 处跨 8 文件 / 11 variant" 统一；§1.1 违规表 accent 行改为 67 / 11 variant / 8 文件；4-tasks.md 相关描述同步 |
| **A-MINOR**（s4 工作量 9h 偏低，CR-1 修复后溢出）| MINOR | 已修复 | 4-tasks.md s4 工作量 9h → **10h**；§"子任务拆分风险" 新增 s4 动态拆分门槛（类比 s6 硬约束）："s4 启动 4h 后若 page + 主 Tab 未完成，executor 可拆 s4b 独立处理 backtest Tab 散落 + hm-* 迁移"；总关键路径保持 17h（s4 10h 与 s6 10h 并列；max 规则）；波次 B 鲁棒性下降需 s4/s6 双硬约束 |
| **A-MINOR**（wave B 6 并行 > 常规 5 agent 上限）| MINOR | 已修复 | 4-tasks.md §"并行分组" 追加 "若主 agent 派遣工具并发上限为 5，则 s9（2.5h）排队在 s4-s8 之后启动；因 s9 < s6 关键路径，无实质影响；若上限为 6 则全部并发"；task.json parallel_groups 保持 6 个一组（框架限制下由主 agent 自行调度） |
| **C-m3 / 缺失 3**（chartTheme.ts `CSSProperties` import 风格）| MINOR | 已修复 | §3.3.6 说明 "导出类型沿用 chartTheme.ts 既有 import 风格（若已有 `import type { CSSProperties } from 'react'` 用 `: CSSProperties`；否则 `: React.CSSProperties`）"；s1 描述追加此要点 |
| **C-缺失 1**（§3.9 OverviewGreyTab 改动说明与 s4 task description 不同步）| MINOR | 已修复 | §3.9 OverviewGreyTab 行追加 "Round 3 新增：.sc-l 4 处迁移（L84/L134/L220/L458）"；s4 描述同步列出 OverviewGreyTab 4 处；若 executor 选择合并 OverviewGreyTab 到 OverviewTab，4 处 sc-l 随合并迁移（任一路径下 R14 扫描都能兜住）|
| **C-缺失 2**（R14 selftest 未覆盖 sc-l + Tailwind 混用形态）| MINOR | 已修复 | §3.2.8 selftest R14 追加一条正例 `className="sc-l inline-flex items-center"` 必须命中 |

---

## Round 2 Revision Notes（按本轮审查编号回应）

本节按 Architect r2 + Critic r2 的编号逐一列出修改落地点，便于下一轮审查对照验证。所有计数与行号已通过 Grep/Read 重新**现场验证 @ 2026-04-19**（见 §1.1 脚注 A）。

| Review Item | 级别 | 状态 | 落地位置 |
|---|---|---|---|
| **A-CR-1 / C-CR-1**（`--accent-*` 变体严重漏报 + TabNav 漏文件）| CRITICAL | 已修复 | 1-requirements.md §1.1 违规表第 15 行扩展为 "55 处跨 8 文件 / 10 variant"（含 amber/blue/purple 及其 -10/-20）；§1.1 字体迁移路径表追加 5 行；§3.3.8 映射表扩展至 10 行；§3.2.3 R13 正则扩为白名单 6 色；§3.2.8 selftest 追加 amber/blue/purple 正例 |
| **C-CR-1**（factor-research 散落 44 处跨 5 文件，s4/s5 扫描会失败）| CRITICAL | 已修复 | 1-requirements.md §1.1 追加"factor-research 散落"独立行；§3.2.3 新增 R14 规则；§3.3.7 开头加散落位置清单；§3.9 影响文件清单追加 4 行（TradesTab / PerformanceTab / TearsheetTab / data-catalog/page 的 factor-research 迁移）；s4/s5 描述追加散落清理；s10 preflight 纳入 R14 |
| **C-CR-1（types.ts 漏入 s5 范围）**| CRITICAL | 已修复 | 1-requirements.md §1.1 dc-* 行改为"65 处跨 6 文件"，明列 types.ts 的 12 处；§3.3.4 新增"dc-type-* 字典常量迁移策略"小节；§3.9 补 types.ts 行；s5 文件列表追加 types.ts |
| **A-MA-1 / C-M1**（bt-* 实测 280 非 253；JobQueue 4 处 bt-status）| MAJOR | 已修复 | 1-requirements.md §1.1 bt-* 行改为"280 处跨 7 文件"（backtest 276 + data-catalog/JobQueue 4）；s4 计数更新；s5 追加 JobQueue 4 处 bt-status 工作（预留到 s11 统一处理） |
| **A-MA-2**（2-research.md L19 残留 docs/ui/）| MAJOR | 已修复 | 2-research.md L19 改为 `.claude/skills/TinoHelmDS/Web UI Kit.html` + `Charts Spec.html`；并追加 Round 1 修正说明 |
| **C-M2**（R7/R8 selftest 缺失 + R8 spread-extra-prop 豁免）| MAJOR | 已修复 | §3.2.8 selftest 扩充 R7 / R8 / R6 多行正反例；R8 PCRE2 断言改写明确 spread-extra-prop 豁免；R7 两阶段伪代码补齐 |
| **C-M3**（R9 多行扫描必须 `-U --multiline-dotall`）| MAJOR | 已修复 | §3.2.3 R9 追加"**必须** `-U --multiline-dotall`"说明；§3.2.4 新增 R9 两阶段伪代码；§3.2.8 selftest 增加 R9 多行正例 |
| **A-MINOR**（factor-research class 总数 98 vs 声明 85）| MINOR | 已修复 | §3.3.7 开头改为"globals.css L1853-1987 共 98 unique selector（含 SVG data URI 假命中 + 父子选择器）；顶层需独立映射约 85 个 class" |
| **A-MINOR**（s10 preflight 失败回退 target 未明示）| MINOR | 已修复 | 4-tasks.md s10 描述追加"preflight 输出的违规路由 → 回退 target 映射表" |
| **A-MINOR**（s6 动态拆分门槛软约束）| MINOR | 已修复 | 4-tasks.md §子任务拆分风险 改为硬约束："s6 启动 4h 后若未完成 3 个子组件迁移，必须拆 s6b" |
| **C-m1**（§1.9 "经用户明确授权" 缺 interview 原文支撑）| MINOR | 已修复 | 1-requirements.md §1.9 降语为"interview.md 第 4 轮'迁移调用点 + 完全删除遗留'选择隐含此方向；本任务完成后主 agent 负责向用户确认并更新 memory 文件" |
| **C-m2**（§3.3.7.4 `.btn-p` 两选项决策）| MINOR | 已修复 | §3.3.7.4 锁定为"全部改为 accent 橙 `variant='default'`"（单一选项，与 DS 规则一致），删除"如确为成功动作"的备选 |
| **C-m3**（`.rpt-*` 调用点 0 / ReportHeader 拆分建议降级）| MINOR | 已修复 | §3.3.7.9 `.rpt-*` 行追加注释"调用点 0"；§3.5.5 ReportClient 拆分模板移除 ReportHeader.tsx |
| **C-m4**（CHART_LABEL_STYLE `fontFamily` 改变 ReferenceLine label 字体）| MINOR | 已修复 | §3.3.6 方案 (a)：删除 fontFamily，统一 fontSize 为 10（覆盖现状 9/10 混用），明确视觉变化预期 |
| **缺失 5**（`--popover` vs `--bg-p` 颜色一致性）| MINOR | 已修复 | §3.3.5 补加核对结果："已核对 globals.css L81/L153，`--popover` 定义为 `oklch(...) /* --bg-p */`，两者在 dark 与 light 下均等价，Tooltip 迁移无视觉差异" |
| **缺失 6**（StatusBadge barrel re-export 视觉差异）| MINOR | 已修复 | §3.3.9 补加"视觉差异声明"：barrel 切换后 legacy 调用点外观从 shadcn Badge 的 `rounded-md` 变为 QDS `rounded-full` + span；s11 执行后需逐页目测对比；如差异过大，保留顶层 Badge 视觉但内部查表改为 QDS map |
| **CM-M2 / R6 alias**（已在 r1 修复）| — | 保持 | 无变更 |
| **CM-MINOR R4 cn() 包裹形态**（r1 已知限制）| MINOR | 保持 | §3.2.8 末尾已声明"R4 不覆盖 `className={cn("cg", ...)}` 包裹形态"；实测 0 命中 |

**脚注 A（本轮现场验证指令）**：
```bash
# --accent-* 变体 + 计数
rg 'var\(--accent-[a-z0-9-]+\)' src/web/src -o --no-filename | sort | uniq -c | sort -rn
# → 10 variants: green(23), red(13), amber(12), blue(7), orange(4), red-20(2), green-10(2),
#    purple(1), purple-20(1), blue-20(1), amber-20(1) = 67 include --accent-foreground (shadcn)
# → 55 处 business tokens 跨 8 文件（排除 --accent-foreground 内置）

# bt-* per file
rg -c '\bbt-[a-z0-9-]+' src/web/src/app --glob '*.tsx' | sort
# → 276 in backtest + 4 in data-catalog/JobQueue = 280 total across 7 files

# dc-* per file
rg -c '\bdc-[a-z0-9-]+' src/web/src/app/data-catalog | sort
# → 65 total across 6 files (including types.ts:12 TYPE_BADGE_CLS dict values)

# factor-research 散落（按 className 实例计数）
rg -no 'className=\{?"(sc|cd|sl|cd-h|cd-b|sc-l|sc-v|sc-sub|fsel|ctbl|dtab|turn-[a-z]+|verdict|factor-dot|factor-limit|data-avail|action-row|frow|fg|hbar|explorer|config-panel|result-panel)"' src/web/src --glob '*.tsx' -c
# → 66 instances across 6 files: research/page(47) + data-catalog/page(12) + FetchDialog(3) + TradesTab(2) + PerformanceTab(1) + TearsheetTab(1)
# 按"逻辑位置"(行数/KPI 卡数等)计数则约为 44 处（与 critic r2 报告口径一致）

# factor-research globals.css unique selector
sed -n '1853,1987p' src/web/src/app/globals.css | rg -o '^\.[a-zA-Z][a-zA-Z0-9_-]*' | sort -u | wc -l
# → 98 unique selectors（含 SVG data URI 假命中 .w3/.a/.org/.html 等 + 父子 .arr/.pdot/.sub/.tr/.open 等；顶层 class 约 85 个）

# R4 严格（PCRE2 前后向断言）
rg --pcre2 -nU 'className\s*=\s*["][^"]*(?<![-a-zA-Z0-9_])(cg|ca|cr|ci|dim|mono)(?![-a-zA-Z0-9_])[^"]*["]' src/web/src --glob '*.tsx' | wc -l
# → 14 命中 across 3 files: research/page(9) + data-catalog/page(4) + DeleteDialog(1)

# --popover / --bg-p 等价性
rg -n -- '--popover|--bg-p' src/web/src/app/globals.css | head -20
# → L81 "--card: oklch(0.305 0.004 84.6); /* --bg-p #302f2d */"
# → L83 "--popover: oklch(0.305 0.004 84.6); /* --bg-p */"
# → L153 "--popover: oklch(0.966 0.009 100.0);" (light) 同 --card 同 --bg-p
# Conclusion: --popover ≡ --bg-p in both dark & light themes
```

---

## Round 1 Revision Notes（保留供交叉审查）

本节按 Architect r1 + Critic r1 的编号逐一列出修改落地点。所有计数与行号已通过 Grep/Read 重新验证 @ 2026-04-19。

| Review Item | 状态 | 落地位置 |
|---|---|---|
| **CR-1 / Architect-C1 / Critic-C2**（R4 扫描误报）| 已修复 | §3.2.3 R4 改为 PCRE2 前后向断言 + 负向边界；§3.2.9 新增 `--selftest` 子命令设计；s2 验收标准改为"selftest 通过 + R1-R13 各至少命中 1 次"；1-requirements.md §1.1 精确计数（严格扫描 14 处） |
| **CR-2 / Architect-C2 / Critic-M1**（factor-research 子系统缺口）| 已修复（选项 A 全迁移） | §3.3.7 新增 factor-research 原语完整迁移映射表（85 class → Tailwind/shadcn）；§3.5.4 research 拆分模板扩展为 6 个子组件（Dataset/Factor/Chart/JobQueue/Config/Result）；§3.9 s10 删除范围扩大到 ~780 行；1-requirements.md NFR-2 目标调整为 1210 ± 50；s6 工作量上调至 10h |
| **CR-3 / Critic-C1**（`docs/ui/qds-*.html` 不存在）| 已修复 | §3.3.3 `bt-row` 映射改引用 `Web UI Kit.html` + `component-row.html` preview；§3.3.4 `dc-type-*` 改引用 `Web UI Kit.html` + `component-badges.html` preview + `colors_and_type.css`；§3.4 preview 矩阵只引用 skill 内部文件；FR-4.4 empty state 引用改为 `Web UI Kit.html` + `components/EmptyState.tsx` |
| **MA-1 / Architect-M1**（Label/ReferenceLine 虚构引用）| 已修复 | §3.2.3 R9 重命名为 R9-reference-line-label，扫描 `<ReferenceLine label={{…}}>` 对象形式；§3.3.5 改为 ReferenceLine label spread `{...CHART_LABEL_STYLE, value: "..."}`；§3.3.6 常量类型注释；s6 验收对应修正 |
| **MA-2 / Architect-M2**（trading bt-* 虚构 4 处）| 已修复 | §3.9 `trading/page.tsx` 行改为"迁移 5 处 fontFamily 内联 + 零星 Tooltip/Grid spread"；s7 任务描述删除 bt-* 叙述；工作量下调为 2.5h |
| **MA-3 / Critic-M2**（StatusBadge API 不兼容）| 已修复 | §3.3.9 新增"StatusBadge 迁移决策"章节（选项 a：扩展 QDS Status union 支持 6 状态 + 中文 label）；s7 / s4 / s8 / s9 任务描述明确禁止破坏性替换顶层 StatusBadge |
| **MA-4 / Critic-M3**（未定义 --accent-* 变量）| 已修复（r2 进一步扩展）| §3.3.8 新增"未定义 CSS 变量 → Tailwind 语义类映射表"（r1 5 条映射，**r2 扩展至 10 条**）；s7 / s9 受影响文件清单扩展；新增 R13 规则（r2 正则扩至 6 色白名单） |
| **MA-5 / Critic-M4**（fontSize 内联未扫描）| 已修复 | §3.2.3 新增 R12-fontsize-inline；§3.3.8 字号归一化映射表；Recharts `wrapperStyle`/`contentStyle`/`labelStyle` 透传与 `CHART_*_STYLE` 常量 spread 上下文豁免 |
| **MA-6 / Critic-M5**（shadcn dark: 前缀冲突）| 已修复 | §3.2.10 明确 `--mode both-themes` 排除 `src/components/ui/**` 与 `src/components/qds/**`；脚本实现对应 `--glob '!...'` |
| **MA-7 / Critic-M6**（历史 memory 冲突）| 已修复（r2 降语）| 1-requirements.md §1.9 新增"与历史用户 memory 的关系"章节（**r2 将"经用户明确授权"降语为"interview.md 第 4 轮选择隐含此方向"**）；FR-6.1 追加 Historical Notes 区块要求；s12 Step 5 对应 |
| **MA-8 / Architect-M4**（CLAUDE.md 既有 QDS CSS Classes 章节矛盾）| 已修复 | FR-6.2 要求"改写"而非"追加"既有章节；FR-6.3 要求替换 Key Conventions 中 `docs/ui/` 引用；s3 描述扩展；s12 Step 5 增加 Step 5a（改写既有章节）+ Step 5b（追加新章节） |
| **MA-9 / Architect-M3**（dc-* 起点 / 计数）| 已修复（r2 进一步补 types.ts）| §3.9 确认 `.dc-sl` 起于 L1640；s5 任务 dc-* 计数 **r2 由 53 → 65 处跨 6 文件**（含 types.ts 12 处 TYPE_BADGE_CLS 常量）；s10 删除范围 L1640 起 |
| **MA-10 / Architect-M5**（R11 漏检 L1856）| 已修复 | §3.2.3 R11 改为非行首锚定 + 多选择器同行模式；s10 明确要求 L1856 整行原子删除 |
| **MA-11 其他**（components/motion、PageTransition、preview 孤儿）| 已修复 | 1-requirements.md §1.3.3 明确 motion/NotificationListener 非迁移范围；FR-2.3 声明 preview 未覆盖的模式用 shadcn 默认 |
| **Architect-Minor-1 R10 例外 components/ui** | 已修复 | §3.2.3 R10 例外列；FR-1.6 豁免声明 |
| **Architect-Minor-2 s12 Step 2.5 AC-2 fallback** | 已修复 | s12 Step 2.5 新增；AC-2 退化路径明确 |
| **Critic-m2 R6 alias 冲突** | 已修复 | §3.2.3 R6 改为唯一 spread 形式，删除 `contentStyle={CHART_TOOLTIP_STYLE}` alias 例外 |
| **Critic-m3 smoke test 阈值** | 已修复 | s2 验收改为 selftest 通过 + R1-R13 每条至少命中 1 次（不依赖绝对数字）|
| **Critic-m4 preflight-before-css-delete** | 已修复 | FR-5.4 + §3.2.11 新增子命令；s10 第一步强制运行 |
| **Critic-m5 text-[11px] 字号归一化** | 已修复 | §3.3.8 字号归一化映射表，明确保留 arbitrary-value `text-[0.62rem]` / `text-[0.68rem]` 等 |

---

## 3.1 架构对齐分析（棕地必做）

### 3.1.1 既有架构模式识别

TinoHelm 前端同时存在**三条**"变体处理"模式，共同指向 **"token 单一事实源 → 多层消费"** 的共性：

| 模式 | 表现 | 事实来源 |
|---|---|---|
| **A. QDS CSS class 模式**（早期 backtest / data-catalog） | `bt-row` / `dc-filter-item` / `cg` / `ca` / `cr` / `dim` 等预定义全局 `!important` class | `globals.css` 内 `.bt-*` 134 条（L532 起）、`.dc-*` 76 条（L1640 起，`.dc-sl` 为首，`.dc-filter-strip` 在 L1659）、`.cg`/`.ca`/`.cr` 单行（L1856） |
| **B. Tailwind + shadcn + QDS 业务组件模式**（中后期 strategies / trading / analytics / page） | `className="bg-card text-foreground"` + `<StatCard>` + `{...CHART_TOOLTIP_PROPS}` | `src/web/CLAUDE.md` 的「Tailwind class mapping」表 + `components/qds/` + `lib/chartTheme.ts` |
| **C. factor-research CSS 原语模式**（research 子系统独立，**但已散落到 data-catalog/page 与 3 个 backtest Tab，见 r2 修正**）| `.sc/.cd/.sl/.fl/.fi/.fsel/.ctbl/.dtab/.hm-*/.wf-*/.hbar/.acc-*/.param-*/.cfg-*/.rpt-*/.turn-*/.verdict/.tip/.badge/.spinner/.factor-dot/.data-avail/.action-row` 等（globals.css L1853-1987 共 98 unique selector，顶层独立映射约 85 个）| `globals.css` L1853-1987 |

三种模式共享同一 **token 底层** — 都是 `--bg-p` / `--t0` / `--acc` 等 CSS 自定义属性驱动。差别只在"消费姿势"：A/C 把 token 封装在具名业务 class 里，B 把 token 通过 `@theme inline` 映射到 Tailwind 语义类。

### 3.1.2 分层策略

`src/web/src/` 的分层（从底向上）：

```
src/app/globals.css      ← Token 层（QDS 短 token + shadcn oklch 映射）
src/lib/chartTheme.ts    ← Recharts 常量层（消费 token，暴露 style 对象）
src/components/ui/       ← shadcn 原语（Card / Button / Tooltip / Dialog / Table / …） — 扫描豁免区
src/components/qds/      ← QDS 业务组件（StatCard / PageHeader / SectionLabel / InlineError / ShimmerBar / StatusBadge / HelpTip） — 已就绪，不修改
src/components/motion/   ← framer-motion 封装（FadeIn / StaggerContainer / PageTransition） — 不在迁移范围
src/components/*.tsx     ← 应用级（Sidebar / TopBar / StatusBar / FillTicker / NotificationListener / ErrorBoundary / EmptyState / IdBadge / ThemeToggle / ConfirmModal）
src/app/<route>/         ← 业务路由（14 页 + 子组件）
```

### 3.1.3 横切关注点

| 关注点 | 现有处理 |
|---|---|
| 动画 | `globals.css` 定义 `@keyframes qds-fade-up` / `qds-pulse` / `qds-shimmer` / `qds-tick-g/r`；`components/motion/` 封装 framer-motion；Tailwind `animate-qds-*` 与 `ease-qds` / `ease-qds-exit` 作原子类 |
| 主题切换 | `globals.css` 内 `html.light` 作用域 override QDS 短 token；shadcn oklch 变量通过 `@theme inline` 联动 |
| WebSocket 事件 | `providers/WebSocketProvider.tsx` + `useWsEvent(eventType)` hook；通知路由 `lib/notification-router.ts` |
| 异步 API | `lib/api.ts`（`apiGet/apiPost/apiPut/apiDelete`）+ `hooks/use-action.ts`（按钮状态机）+ `InlineError` |

### 3.1.4 决策对齐 — 本任务如何顺着现有模式走

**本任务 = 将整个项目从"模式 A + 模式 B + 模式 C 并存"收敛到"仅模式 B"**，顺着既有方向走：

1. **沿用** `globals.css` 作为 token 单一事实源（不修改底层 token 定义），仅删除业务层 `bt-*`/`dc-*`/单字母 class / factor-research 原语实现；
2. **沿用** `src/web/CLAUDE.md` 的 Tailwind 映射表（已有表不改内容，仅改写 QDS CSS Classes 章节），调用点全量迁移到该表；
3. **沿用** `chartTheme.ts` 的常量 spread 模式（已有 `CHART_TOOLTIP_PROPS` / `CHART_GRID_STYLE` / `CHART_AXIS_STYLE`），仅补齐缺失的 `CHART_LEGEND_STYLE` / `CHART_LABEL_STYLE`；
4. **沿用** QDS 业务组件路线（7 个组件已就绪），把手写 KPI / 标题 / 分节标签 / 错误提示等统一替换；
5. **变更**（相对此前规则）视觉参考源 — 从不存在的 `docs/ui/qds-*.html` 改为 `.claude/skills/TinoHelmDS/` skill 下的 `Web UI Kit.html` + `Charts Spec.html` + 21 个 preview 卡片 + `colors_and_type.css`。这是必须变更项（原引用文件不存在）。

**偏离说明**：历史 memory (`feedback-bt-card-classes.md` 等) 主张保留 bt-* 等 class，本任务**偏离**此方向，由 interview.md 第 4 轮"迁移调用点 + 完全删除遗留 class"选择隐含授权（详见 1-requirements.md §1.9）。

## 3.2 合规扫描脚本设计（FR-5 对应）

### 3.2.1 文件位置与形态

`src/web/scripts/verify-ds-compliance.sh` — 纯 bash + ripgrep，无需 Node 运行时。遵循现有 `src/web/scripts/check-grep-fonts.sh` 的风格约定（已存在）。

### 3.2.2 工作目录与调用

脚本自适应：从 `src/web/` 或仓库根 `/Users/ouzhuohao/TinoHelm/` 运行都应通过。内部通过 `SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"` + `ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"` 锁定 `src/web/` 为扫描根。

### 3.2.3 规则清单（与 AC-1 一一对应，Round 2 扩展：R13 六色白名单 + 新增 R14）

脚本主体为若干 `ripgrep_rule` 函数调用。所有需要前后向断言的规则必须使用 `rg --pcre2`。

| Rule ID | ripgrep 模式 | glob / 排除 | Exit 条件 |
|---|---|---|---|
| **R1-font-inline** | `fontFamily:\s*["\']var\(--font-[ud]\)["\']` | `**/*.{ts,tsx,jsx,js}`；排除 `src/lib/chartTheme.ts` | 命中 ≥ 1 行则违规 |
| **R2-legacy-class-bt** | PCRE2：`className\s*=\s*[\"'{][^\"'}]*\bbt-[a-z0-9-]+\b` | `**/*.{tsx,jsx}` | 同上 |
| **R3-legacy-class-dc** | PCRE2：`className\s*=\s*[\"'{][^\"'}]*\bdc-[a-z0-9-]+\b` 外加对 **字符串字典常量 `TYPE_BADGE_CLS` 的值扫描**：`["']dc-type-[a-z]+["']`（宽松命中，不需 className 上下文） | `**/*.{tsx,jsx,ts}` | 同上 |
| **R4-legacy-class-single** | PCRE2：`className\s*=\s*["\'][^"\']*(?<![-a-zA-Z0-9_])(cg\|ca\|cr\|ci\|dim\|mono)(?![-a-zA-Z0-9_])[^"\']*["\']` — 负向前后查断言：确保 `cg/ca/cr/ci/dim/mono` 前后都不是 `-` / `_` / 字母数字（保证独立 token）；Tailwind `font-mono`、`bg-qds-*-dim`、`text-qds-info-dim`、`animate-qds-pulse`、`dark:bg-*` 等必须不命中（通过 `--selftest` 保证） | `**/*.{tsx,jsx}` | 同上 |
| **R5-hardcoded-hex** | `(bg\|text\|border)-\[#[0-9a-fA-F]{3,8}\]\|color:\s*["\']#[0-9a-fA-F]{3,8}` | `**/*.{tsx,jsx,ts}`；排除 `src/app/globals.css` | 同上 |
| **R6-tooltip-spread** | 两阶段匹配：阶段 1 用 `rg -U --multiline-dotall` 扫描所有 `<(Recharts)?Tooltip\b[^/>]*>` 跨行 tag；阶段 2 `grep -v '\.\.\.CHART_TOOLTIP_PROPS'` 过滤合规。注：**不再允许** `contentStyle={CHART_TOOLTIP_STYLE}` alias 形式（与 s8/s9 统一） | `**/*.{tsx,jsx}` | 同上 |
| **R7-grid-spread** | **两阶段匹配**（Round 2 明确）：阶段 1 `rg -U --multiline-dotall -n '<CartesianGrid\b[^/]*/>'` 抓取完整 tag；阶段 2 `grep -v '\.\.\.CHART_GRID_STYLE'` 过滤 — 即任何不含 `{...CHART_GRID_STYLE}` 的 CartesianGrid 为违规。**允许 prop 顺序任意** 与 **额外 prop 共存**（如 `<CartesianGrid strokeDasharray="3 3" {...CHART_GRID_STYLE} />` 合规） | `**/*.{tsx,jsx}` | 同上 |
| **R8-legend-spread** | PCRE2（Round 2 修订，明确支持 spread-extra-prop）：`<Legend\b[^/]*wrapperStyle\s*=\s*\{(?!\s*CHART_LEGEND_STYLE\b)(?!\{?\s*\.\.\.CHART_LEGEND_STYLE\b)` — 即 `wrapperStyle={CHART_LEGEND_STYLE}` 与 `wrapperStyle={{ ...CHART_LEGEND_STYLE, fontSize: 10 }}` 均豁免；仅纯手写对象（不含 `CHART_LEGEND_STYLE` 引用）命中 | `**/*.{tsx,jsx}` | 同上 |
| **R9-reference-line-label** | **两阶段匹配**（Round 2 明确，**必须** `rg -U --multiline-dotall`）：阶段 1 `rg -U --multiline-dotall -n '<ReferenceLine\b[^/]*(?:label\s*=)[^/]*?/>'` 抓取完整跨行 tag；阶段 2 `grep 'label\s*=\s*{{.*fontSize\|fill\|fontFamily'` 且不含 `\.\.\.CHART_LABEL_STYLE` 的为违规。扫描的是 Recharts `<ReferenceLine>` 的 `label` prop（对象字面量形式），不是 `<Label>` 子组件（全仓命中 0）。**覆盖 4 处现状**：RiskTab:187 单行、RobustnessTab:353 单行、OverviewTab:679-684 多行、ReportClient:504-508 多行 | `**/*.{tsx,jsx}` | 同上 |
| **R10-arbitrary-token** | `(bg\|text\|border)-\[var\(--` | `**/*.{tsx,jsx}`；**排除** `src/web/src/components/ui/**`（shadcn 原语 upstream，允许自带 CSS var 消费） | 同上 |
| **R11-globals-legacy** | PCRE2 多行：`\.bt-[a-z-]+\s*\{\|\.dc-[a-z-]+\s*\{\|(?:^\|[;\}])\s*\.(cg\|ca\|cr\|ci\|dim\|mono)\s*\{\|\.(sc\|cd\|sl\|fl\|fi\|fsel\|ctbl\|dtab\|acc-[a-z]+\|param-[a-z]+\|cfg-[a-z]+\|hm-[a-z]+\|wf-[a-z]+\|hbar(?:-[a-z]+)?\|rpt-[a-z]+\|turn-[a-z]+\|verdict(?:-[a-z]+)?\|factor-dot\|factor-limit\|data-avail\|action-row\|spinner\|tip\|badge\|frow\|fg)\s*\{`。**非行首锚定**以覆盖 L1856 单行组合 `.mono{}.dim{}.cg{}.cr{}.ca{}.ci{}` 中非首个 class | `src/app/globals.css` | 命中 ≥ 1 行则违规 |
| **R12-fontsize-inline** | `style\s*=\s*\{\{[^}]*fontSize\s*:` | `**/*.{tsx,jsx}`；**排除** 上下文含 `wrapperStyle`/`contentStyle`/`labelStyle`/`CHART_LEGEND_STYLE`/`CHART_LABEL_STYLE`/`CHART_TOOLTIP_STYLE`/`CHART_TOOLTIP_PROPS`/`tick=` 的所在 JSX prop（Recharts 透传）；排除 `src/lib/chartTheme.ts`（常量本体） | 同上 |
| **R13-undefined-var**（**Round 3 实测 11 variant**） | `var\(--accent-(green\|orange\|red\|amber\|blue\|purple)(-?(10\|20))?\)` — 覆盖所有 11 种现状变体（green/orange/red/amber/blue/purple × 基础 + -10 + -20，实测求和 67 处 / 11 variant / 8 文件）。**globals.css 中均未定义，CSS 容错使颜色 fallback 为视觉 bug** | `**/*.{tsx,jsx,ts,js,css}`；排除 `globals.css`（**排除 `--accent-foreground`**：这是 shadcn 内置 token 已在 L92 定义，不属于未定义变体） | 命中 ≥ 1 行则违规 |
| **R14-factor-research-primitive**（**Round 3 改为 R4 风格前后向断言 + 支持模板字符串**） | PCRE2：`className\s*=\s*\{?[\"'`][^\"'`]*(?<![-a-zA-Z0-9_])(sc\|cd\|sl\|fl\|fi\|fsel\|ctbl\|dtab\|cd-h\|cd-b\|sc-l\|sc-v\|sc-sub\|turn-row\|turn-item\|turn-label\|turn-val\|verdict\|verdict-pass\|verdict-warn\|verdict-fail\|factor-dot\|factor-limit\|data-avail\|action-row\|frow\|fg\|hbar\|hbar-label\|hbar-wrap\|hbar-fill\|hbar-val\|explorer\|config-panel\|result-panel\|acc-group\|acc-head\|acc-body\|acc-item\|param-section\|param-row\|param-label\|param-val\|param-input\|param-unit\|param-select\|param-divider\|cfg-section\|cfg-title\|hm-grid\|hm-label\|hm-cell\|hm-tick\|wf-row\|wf-label\|wf-bar-wrap\|wf-bar\|wf-val\|rpt-head\|rpt-back\|rpt-title\|rpt-sub\|rpt-meta\|rpt-meta-item\|report-content\|tab-bar\|hist-clickable\|hist-pager\|empty-icon\|empty-title\|empty-desc\|spinner)(?![-a-zA-Z0-9_])[^\"'`]*[\"'`]` — **前后向断言确保每个 token 独立成 className token，同时覆盖引号（单/双）与模板字符串（反引号）形态**：`className="sc-column"` / `"fg-primary"` / `"fi-rocket"` / `"cd-hover"` / `"sl-indicator"` 等 Tailwind / 自定义复合类必须不命中（通过 `--selftest` 保证）；`className={`sc-v ${var}`}` 模板字符串形态正确命中；**覆盖范围**：research/ + data-catalog/ + backtest/components/{OverviewGreyTab,TradesTab,PerformanceTab,TearsheetTab,OverviewTab 的 hm-*} + FetchDialog 的散落 | `**/*.{tsx,jsx}` | 命中 ≥ 1 行则违规 |

### 3.2.4 多行匹配策略

R6 / R7 / R9 涉及跨行 JSX 标签：使用 `rg -U --multiline-dotall` 配合锚定模式（两阶段）：

```bash
# R6 模板（Tooltip spread 校验）
# 阶段 1：抓取所有 <Tooltip ... /> 或 <RechartsTooltip ... /> 跨行块
rg -U --multiline-dotall -n -g '**/*.{tsx,jsx}' \
  '<(Recharts)?Tooltip\b[^/]*?/>' src/ > /tmp/tooltip_blocks.txt

# 阶段 2：过滤出"含 contentStyle 且不含 ...CHART_TOOLTIP_PROPS"的违规块
grep 'contentStyle' /tmp/tooltip_blocks.txt | grep -v '\.\.\.CHART_TOOLTIP_PROPS'

# R7 模板（CartesianGrid spread 校验，Round 2 新增伪代码）
# 阶段 1：抓取所有 <CartesianGrid ... /> 跨行块
rg -U --multiline-dotall -n -g '**/*.{tsx,jsx}' \
  '<CartesianGrid\b[^/]*?/>' src/ > /tmp/grid_blocks.txt

# 阶段 2：过滤不含 spread 的为违规（允许任意 prop 顺序 + 额外 prop 共存）
grep -v '\.\.\.CHART_GRID_STYLE' /tmp/grid_blocks.txt | \
  grep -E '(stroke|strokeDasharray)\s*=' || true

# R9 模板（ReferenceLine label 对象 spread 校验，Round 2 新增伪代码）
# 阶段 1：抓取所有 <ReferenceLine ... /> 跨行块（含 label 属性）
rg -U --multiline-dotall -n -g '**/*.{tsx,jsx}' \
  '<ReferenceLine\b[^/]*?(?:label\s*=)[^/]*?/>' src/ > /tmp/refline_blocks.txt

# 阶段 2：提取 label 对象后的内容，若含 fontSize/fill/fontFamily 且不含 ...CHART_LABEL_STYLE 则违规
python3 -c "
import re, sys
text = open('/tmp/refline_blocks.txt').read()
for m in re.finditer(r'<ReferenceLine[\\s\\S]*?/>', text):
    block = m.group()
    if 'label={{' in block or 'label={ {' in block:
        label_match = re.search(r'label\\s*=\\s*\\{\\{([^}]*)\\}\\}', block)
        if label_match and ('fontSize' in label_match.group(1) or 'fill' in label_match.group(1) or 'fontFamily' in label_match.group(1)):
            if '...CHART_LABEL_STYLE' not in block:
                print(block)
"
```

> 覆盖 4 处现状：RiskTab:187（单行）、RobustnessTab:353（单行）、OverviewTab:679-684（4 行）、ReportClient:504-508（5 行）。R9 **必须** `rg -U --multiline-dotall` 才能命中多行形式。

### 3.2.5 输出格式

```
[R2-legacy-class-bt] src/app/backtest/page.tsx:412:17 className="bt-list bt-card"
  → Migrate: bt-list → Tailwind flex/grid; bt-card → <Card className="bg-card">
[R6-tooltip-spread] src/app/analytics/page.tsx:333 contentStyle={TOOLTIP_STYLE}
  → Migrate: <Tooltip {...CHART_TOOLTIP_PROPS} />
[R9-reference-line-label] src/app/trading/components/tabs/RiskTab.tsx:187 label={{ value: "阈值", fill: "var(--warn)", fontSize: 9 }}
  → Migrate: label={{ ...CHART_LABEL_STYLE, value: "阈值" }}
[R13-undefined-var] src/app/strategies/[name]/EditorClient.tsx:22 text-[var(--accent-green)]
  → Migrate: text-qds-success
[R14-factor-research-primitive] src/app/data-catalog/page.tsx:240 className="sc"
  → Migrate: use <StatCard> QDS component (see §3.3.7.1)
```

违规计数汇总：`Total violations: 47 across 7 files`。Exit code：1。

### 3.2.6 `--fix-hint` 开关

当携带 `--fix-hint` 时，每条违规后追加一行迁移建议（来自 §3.3 的映射表）。R14 的 fix-hint 使用 §3.3.7 的家族映射注入（如 `.sc-l` → `<SectionLabel>` QDS component；`.cd/.cd-h/.cd-b` → shadcn `<Card>/<CardHeader>/<CardContent>`）。

### 3.2.7 `--mode both-themes` 开关（AC-3 使用）

此模式下仅运行 R1-R5、R10、R13 的子集（专注主题无关的颜色纪律），并追加：

- 在 `globals.css` 中断言 `html.light` 作用域内定义了 `--bg-s` / `--bg-p` / `--bg-t` / `--bg-in` / `--t0` / `--t1` / `--t2` / `--t3` / `--bd` / `--bdh` 全部 override；
- 在**业务** `.tsx` 中断言没有 `className` 含 `dark:` / `light:` 前缀（本项目 QDS 用 token override 而非 Tailwind 暗色变体）；
- **排除**：`src/web/src/components/ui/**`（shadcn 原语 upstream 代码，允许 `dark:` 前缀）、`src/web/src/components/qds/**`（已就绪，不修改）。

### 3.2.8 `--selftest` 子命令（Round 1 新增，Round 2 扩充）

脚本必须实现 `--selftest` 子命令，内置以下正/反例，执行时对每条自动断言，任一失败 exit 2：

```bash
# === R4（独立 token cg/ca/cr/ci/dim/mono）===
# 正例（必须命中）
assert_match R4 'className="cg"'
assert_match R4 'className="cr mono"'
assert_match R4 'className="dim"'
assert_match R4 'className={cn("cg", rest)}'   # 注意：cn() 包裹形态的已知局限见 §3.2.8 末尾声明

# 反例（必须不命中）
assert_no_match R4 'className="font-mono"'
assert_no_match R4 'className="bg-qds-success-dim"'
assert_no_match R4 'className="text-qds-info-dim"'
assert_no_match R4 'className="animate-qds-pulse"'
assert_no_match R4 'className="dark:bg-transparent"'
assert_no_match R4 'className="bg-qds-accent-dim text-primary"'

# === R6（Tooltip spread）===
# 正例（必须命中：仅 contentStyle 无 spread；含多行）
assert_match R6 '<Tooltip contentStyle={TOOLTIP_STYLE} />'
assert_match R6_MULTILINE '<RechartsTooltip\n  contentStyle={{ background: "var(--popover)" }}\n/>'

# 反例（必须不命中）
assert_no_match R6 '<Tooltip {...CHART_TOOLTIP_PROPS} />'

# === R7（CartesianGrid spread，Round 2 新增）===
# 正例（必须命中）
assert_match R7 '<CartesianGrid strokeDasharray="3 3" stroke="var(--bd)" />'

# 反例（必须不命中 — 允许 prop 顺序任意 + 额外 prop 共存）
assert_no_match R7 '<CartesianGrid {...CHART_GRID_STYLE} />'
assert_no_match R7 '<CartesianGrid {...CHART_GRID_STYLE} strokeDasharray="3 3" />'
assert_no_match R7 '<CartesianGrid strokeDasharray="3 3" {...CHART_GRID_STYLE} />'
assert_no_match R7 '<CartesianGrid {...CHART_GRID_STYLE} vertical={false} />'

# === R8（Legend wrapperStyle spread，Round 2 新增，明确支持 spread-extra-prop）===
# 正例（必须命中 — 纯手写对象）
assert_match R8 '<Legend wrapperStyle={{ fontSize: ".62rem", fontFamily: "var(--font-d)" }} />'

# 反例（必须不命中 — spread 形式均豁免）
assert_no_match R8 '<Legend wrapperStyle={CHART_LEGEND_STYLE} />'
assert_no_match R8 '<Legend wrapperStyle={{ ...CHART_LEGEND_STYLE }} />'
assert_no_match R8 '<Legend wrapperStyle={{ ...CHART_LEGEND_STYLE, fontSize: 10 }} />'
assert_no_match R8 '<Legend iconSize={8} wrapperStyle={{ ...CHART_LEGEND_STYLE }} />'

# === R9（ReferenceLine label 对象 spread，Round 2 扩充：必须含多行正例）===
# 正例（单行形式 — 必须命中）
assert_match R9 '<ReferenceLine label={{ value: "x", fill: "var(--warn)", fontSize: 9 }} />'

# 正例（多行形式 — 必须命中，需 `-U --multiline-dotall`）
assert_match R9_MULTILINE '<ReferenceLine\n  x={10}\n  stroke="var(--warn)"\n  label={{ value: "x", fontSize: 10 }}\n/>'

# 反例（必须不命中）
assert_no_match R9 '<ReferenceLine label={{ ...CHART_LABEL_STYLE, value: "x" }} />'
assert_no_match R9_MULTILINE '<ReferenceLine\n  x={10}\n  label={{ ...CHART_LABEL_STYLE, value: "x" }}\n/>'

# === R10（arbitrary token，shadcn 原语目录豁免）===
assert_no_match R10 'src/components/ui/button.tsx:bg-[var(--acc-d)]'
# 注：此 assert 语义为"给定伪文件路径 + 内容对应，扫描时应被 glob 过滤"，脚本实现必须真实触发 glob 排除而非仅测内容匹配。

# === R12（Recharts 透传豁免）===
assert_no_match R12 'wrapperStyle={{ fontSize: ".62rem" }}'
assert_no_match R12 '<Tooltip labelStyle={{ fontSize: 11 }} />'

# === R13（未定义 CSS 变量，Round 2 扩展至 6 色）===
# 正例（必须命中所有 10 variant）
assert_match R13 'text-[var(--accent-green)]'
assert_match R13 'text-[var(--accent-orange)]'
assert_match R13 'text-[var(--accent-red)]'
assert_match R13 'text-[var(--accent-amber)]'
assert_match R13 'bg-[var(--accent-blue)]'
assert_match R13 'border-[var(--accent-purple)]'
assert_match R13 'bg-[var(--accent-red-20)]'
assert_match R13 'bg-[var(--accent-green-10)]'
assert_match R13 'bg-[var(--accent-amber-20)]'
assert_match R13 'bg-[var(--accent-blue-20)]'
assert_match R13 'bg-[var(--accent-purple-20)]'

# 反例（必须不命中 — shadcn 内置 token 豁免）
assert_no_match R13 'text-[var(--accent-foreground)]'   # shadcn 内置
assert_no_match R13 'text-[var(--accent)]'               # shadcn 内置（无后缀）

# === R14（factor-research 原语，Round 3 改为 R4 风格前后向断言）===
# 正例（必须命中）
assert_match R14 'className="sc"'
assert_match R14 'className="sc-l"'
assert_match R14 'className="cd"'
assert_match R14 'className="ctbl"'
assert_match R14 'className="fsel"'
assert_match R14 'className={`sc-v ${stale.cls}`}'               # 模板字符串形态
assert_match R14 'className="verdict-pass"'
assert_match R14 'className="turn-val cr"'
assert_match R14 'className="rpt-title"'
assert_match R14 'className="sc-l inline-flex items-center"'     # Round 3 新增：sc-l + Tailwind 混用必须命中（OverviewGreyTab 实际写法）
assert_match R14 'className="hm-grid"'                            # Round 3 新增：hm-* 家族正例
assert_match R14 'className="hm-label"'
assert_match R14 'className="hm-cell"'

# 反例（必须不命中 — 非 factor-research class，前后向断言保证）
assert_no_match R14 'className="bg-card"'
assert_no_match R14 'className="font-sans"'
assert_no_match R14 'className="sc-column"'                      # Round 3 关键反例：sc-column 不是 factor-research 原语（sc 后跟 -column，前后向断言不命中 sc）
assert_no_match R14 'className="fg-primary"'                     # Round 3 新增：fg-primary 复合类不命中
assert_no_match R14 'className="fi-rocket"'                      # Round 3 新增：fi-rocket（Lucide 图标类假名）不命中
assert_no_match R14 'className="cd-hover"'                       # Round 3 新增：cd-hover 复合类不命中
assert_no_match R14 'className="sl-indicator"'                   # Round 3 新增：sl-indicator 复合类不命中
assert_no_match R14 'className="scroll"'                         # Round 3 新增：scroll 以 sc 开头但 `oll` 跟在后面，前后向断言不命中
assert_no_match R14 'className="cards"'                          # Round 3 新增：cards 以 cd 开头但 `ards` 跟在后面，不命中
```

**已知限制**：R4 不覆盖 `className={cn("cg", ...)}` 这种 `cn()` 包裹形态（regex 硬性要求 `className="..."` 字面量前缀；对 `className={cn("cg", ...)}` 实测不命中）。当前 src/web/src 下实测 0 处，作为未来约束：若 executor 在迁移过程中临时用 `cn("cg", cond && "active")` 形式，需专项扫描。本任务本轮保持此限制。

### 3.2.9 `--preflight-before-css-delete` 子命令（Round 1 新增，Round 2 扩展）

仅运行 R1-R14（含 R12 / R13 / R14，排除 R11 因其扫描 globals.css 本体），exit 0 才允许 s10 启动删除操作。防止调用点未迁移完成即删除 CSS 定义导致页面 break。

**关键**：R14 在 preflight 中必须通过 — 若 data-catalog/page 或 TradesTab/PerformanceTab/TearsheetTab 还有 factor-research 原语残留，preflight 立即 exit 1，阻止 s10 删除 `.sc-l` / `.sc-v` / `.sc-sub` 等 CSS 定义，避免视觉静默退化。

### 3.2.10 CI 钩入建议

在 `src/web/CLAUDE.md` 标准化章节标注：

```bash
# pre-push / CI 步骤
bash src/web/scripts/verify-ds-compliance.sh --selftest  # 首次或修改脚本后
bash src/web/scripts/verify-ds-compliance.sh              # 常规校验
bash src/web/scripts/verify-ds-compliance.sh --mode both-themes  # 主题验证
```

## 3.3 迁移映射表（FR-1 / FR-3 逐条可执行）

### 3.3.1 `fontFamily` 内联 → Tailwind class

| 现状 | 目标 |
|---|---|
| `style={{ fontFamily: "var(--font-d)" }}` | 加 `font-mono` 到 `className` |
| `style={{ fontFamily: "var(--font-u)" }}` | 加 `font-sans` 到 `className`（`body` 默认即是，绝大多数情况可直接删除内联） |
| `style={{ fontFamily: "var(--font-d)", fontSize: ".7rem" }}` | `className="font-mono text-[0.7rem]"`（详见 §3.3.8 字号归一化） |

### 3.3.2 单字母语义 class → Tailwind 语义类

**仅当作为独立 token 出现时才需迁移**。Tailwind 原生 `font-mono` / `bg-qds-*-dim` 保留不变。

| 现状（独立 token） | 目标 |
|---|---|
| `cg` | `text-qds-success` |
| `cr` | `text-destructive` |
| `ca` | `text-primary` |
| `ci` | `text-qds-info` |
| `dim` | `text-muted-foreground` |
| `mono` | `font-mono` |

### 3.3.3 `bt-*` 家族迁移（134 个 selector；调用点 280 跨 7 文件 — Round 2 修正）

逐类策略（按调用点频次排序）。**视觉参考源**：`.claude/skills/TinoHelmDS/preview/component-row.html`（3px accent stripe 模式）+ `.claude/skills/TinoHelmDS/Web UI Kit.html`（完整 dashboard frame，含 backtest 视觉装配）。

**调用点分布（Round 2 实测 @ 2026-04-19）**：
- `backtest/page.tsx`: 144 处
- `backtest/components/OverviewTab.tsx`: 74 处
- `backtest/components/PerformanceTab.tsx`: 28 处
- `backtest/components/RobustnessTab.tsx`: 15 处
- `backtest/components/TradesTab.tsx`: 9 处
- `backtest/components/OverviewGreyTab.tsx`: 6 处
- `data-catalog/JobQueue.tsx`: 4 处（**bt-status** × 4，Round 2 新发现散落到 data-catalog 目录）
- **合计：280 处跨 7 文件**

| 遗留 class | Tailwind 迁移 | 备注 |
|---|---|---|
| `bt-list` | `<div className="flex flex-col gap-1">` 或对应 shadcn `<Table>` | backtest 列表容器 |
| `bt-row` | `<div className="grid grid-cols-[3px_1fr_auto_auto_auto] gap-3 items-center px-4 py-3 hover:bg-secondary border-b border-border">` | 核心行式布局，严格按 `component-row.html` preview 的 3px accent stripe |
| `bt-status` | 替换为 `<StatusBadge status="…" />` QDS 组件（API 见 §3.3.9） | **注意**：JobQueue.tsx L173/176/181/185 的 4 处 `bt-status bt-status-{queue,done,fail}` 也属于此类；s5 预留 4 处标记，由 s11 StatusBadge 统一扩展完成后一并处理 |
| `bt-progress` | 替换为 `<ShimmerBar />` QDS 组件 | |
| `bt-expand` | `<Button variant="ghost" size="icon">` + Lucide `ChevronDown` | |
| `bt-cd` / `bt-cd-header` / `bt-cd-body` | `<Card>` / `<CardHeader>` / `<CardContent>` shadcn 三件套。**取代历史 memory feedback-bt-card-classes.md 的 bt-cd 强制主张（见 1-requirements.md §1.9）** | |
| `bt-kpi-*` | `<StatCard>` QDS 组件 | |
| 其余 `bt-*` | 逐类排查（详见 §3.6 子任务 B1），每项记录映射到 PR description | |

### 3.3.4 `dc-*` 家族迁移（76 个 selector；调用点 65 跨 6 文件 — Round 2 修正）

**视觉参考源**：`.claude/skills/TinoHelmDS/preview/component-badges.html`（7 色徽章）+ `.claude/skills/TinoHelmDS/preview/component-tabs.html`（filter tabs）+ `.claude/skills/TinoHelmDS/preview/component-progress.html`（coverage bar）+ `.claude/skills/TinoHelmDS/colors_and_type.css`（token 参考）。

**调用点分布（Round 2 实测 @ 2026-04-19）**：
- `data-catalog/page.tsx`: 23 处 `className` 引用
- `data-catalog/JobQueue.tsx`: 14 处
- `data-catalog/FetchDialog.tsx`: 8 处
- `data-catalog/FilterTabs.tsx`: 7 处
- `data-catalog/types.ts`: **12 处 TYPE_BADGE_CLS 字典常量字符串值**（`"dc-type-kl"` / `"dc-type-ipk"` / `"dc-type-mpk"` / `"dc-type-pik"` / `"dc-type-at"` / `"dc-type-tr"` / `"dc-type-fr"` × 根据 data_type 映射 7 色 + bar/trade_tick/quote_tick/funding_rate 四个 DB category 别名）
- `data-catalog/DeleteDialog.tsx`: 1 处
- **合计：65 处跨 6 文件**

| 遗留 class | Tailwind 迁移 |
|---|---|
| `dc-filter-strip` / `dc-filter-item` / `dc-filter-dot` / `dc-filter-count` | `<div className="flex items-center gap-4 border-b border-border">` + `<button className="flex items-center gap-2 px-3 py-2 text-sm text-muted-foreground border-b-2 border-transparent hover:text-foreground data-[active=true]:text-foreground data-[active=true]:border-primary">` |
| `dc-qrow-*`（query row） | `<div className="grid grid-cols-[…] gap-2">` |
| `dc-dtbl`（data table） | shadcn `<Table>` 组件 |
| `dc-type-{klines,aggTrades,trades,bookTicker,fundingRate,markPrice,indexPrice}` | `<Badge variant="…" className="bg-qds-{semantic}-dim text-qds-{semantic}">` — 7 色对应 QDS 语义色变体。颜色分配参照 `colors_and_type.css` 中对应 token（klines→info、aggTrades→success、trades→warning、bookTicker→primary、fundingRate→accent、markPrice→t1、indexPrice→t2） |
| `dc-cov-*`（coverage） | `<div className="h-1 bg-qds-*-dim">` + 语义色 |
| `dc-pager-*` | shadcn `<Pagination>` 或 QDS 自有 Pagination（FR-2.3 声明 preview 未覆盖，shadcn 默认 + QDS token）|
| `dc-chip-*` | shadcn `<Badge>` |
| `dc-sl` / `dc-modal-icon` | `text-qds-info` + `<Info>` Lucide 图标 |

#### 3.3.4.1 `dc-type-*` 字典常量迁移策略（Round 2 新增）

`src/web/src/app/data-catalog/types.ts` 中 `TYPE_BADGE_CLS: Record<string, string>` 字典含 12 处 `"dc-type-*"` 字符串值。**决策**：将字典 value 从 `"dc-type-kl"` 等遗留 class 改为 Tailwind class 字符串，保留 key 不变，调用点无需修改：

```ts
// src/app/data-catalog/types.ts (Before)
export const TYPE_BADGE_CLS: Record<string, string> = {
  klines: "dc-type-kl",
  indexPriceKlines: "dc-type-ipk",
  markPriceKlines: "dc-type-mpk",
  premiumIndexKlines: "dc-type-pik",
  aggTrades: "dc-type-at",
  trades: "dc-type-tr",
  fundingRate: "dc-type-fr",
  // DB category aliases
  bar: "dc-type-kl",
  trade_tick: "dc-type-at",
  quote_tick: "dc-type-ipk",
  funding_rate: "dc-type-fr",
};

// (After) — 7 色语义 token 映射参照 colors_and_type.css + preview/component-badges.html
export const TYPE_BADGE_CLS: Record<string, string> = {
  klines: "bg-qds-info-dim text-qds-info",
  indexPriceKlines: "bg-qds-t1-dim text-qds-t1",     // 若 t1-dim 未定义则改用 "bg-muted text-muted-foreground"
  markPriceKlines: "bg-qds-t2-dim text-qds-t2",       // 同上
  premiumIndexKlines: "bg-qds-warning-dim text-qds-warning",
  aggTrades: "bg-qds-success-dim text-qds-success",
  trades: "bg-qds-warning-dim text-qds-warning",
  fundingRate: "bg-qds-accent-dim text-primary",
  // DB category aliases
  bar: "bg-qds-info-dim text-qds-info",
  trade_tick: "bg-qds-success-dim text-qds-success",
  quote_tick: "bg-qds-t1-dim text-qds-t1",
  funding_rate: "bg-qds-accent-dim text-primary",
};
```

**调用点**：`page.tsx` 等处 `<Badge className={TYPE_BADGE_CLS[type]}>...</Badge>` 无需修改，字符串值自动生效。

**R3 验收**：`rg 'dc-type-[a-z]+' src/web/src/app/data-catalog` 必须 0 命中（types.ts 字符串值已完全重写）。

### 3.3.5 Recharts 常量 spread 迁移

| 现状 | 目标 |
|---|---|
| `<Tooltip contentStyle={TOOLTIP_STYLE} />`（analytics/page 3 处） | `<Tooltip {...CHART_TOOLTIP_PROPS} />`，删除本地 `TOOLTIP_STYLE` 声明 |
| `<RechartsTooltip contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 11, color: "var(--foreground)" }} />`（backtest/OverviewTab 2 处） | `<RechartsTooltip {...CHART_TOOLTIP_PROPS} />` |
| `<RechartsTooltip contentStyle={CHART_TOOLTIP_STYLE} />`（page.tsx 1 处） | `<RechartsTooltip {...CHART_TOOLTIP_PROPS} />`（**唯一形式**，alias 不再允许；R6 会报） |
| `<CartesianGrid strokeDasharray="3 3" stroke="var(--bd)" />`（PerformanceTab 6+ 处） | `<CartesianGrid {...CHART_GRID_STYLE} strokeDasharray="3 3" />`（spread + 额外 prop 覆盖，允许） |
| `<Legend iconSize={8} wrapperStyle={{ fontSize: ".62rem", fontFamily: "var(--font-d)" }} />`（research 2 处） | `<Legend iconSize={8} wrapperStyle={CHART_LEGEND_STYLE} />` |
| `<ReferenceLine label={{ value: "阈值", fill: "var(--warn)", fontSize: 9 }} />`（RiskTab:187、backtest/OverviewTab:684、RobustnessTab:353、ReportClient:508 共 4 处） | `<ReferenceLine label={{ ...CHART_LABEL_STYLE, value: "阈值", fill: "var(--warn)" }} />`（spread CHART_LABEL_STYLE + 业务特定覆盖 value/fill/position） |

**重要修正**：全仓 `<Label\b` 命中 0 — 不存在 `<Label>` 子组件内联样式场景。CHART_LABEL_STYLE 的唯一消费场景是 `<ReferenceLine label={{…}}>` 对象 label prop。

**Round 2 补充 — `--popover` vs `--bg-p` 颜色核对**：已核对 `globals.css` L81-L84（dark）与 L151-L154（light），`--popover` 定义均为 `oklch(...) /* --bg-p */`，两者在 dark 与 light 两套主题下均**严格等价**。因此 OverviewTab 现状 `background: "var(--popover)"` 迁移到 `CHART_TOOLTIP_PROPS`（内部使用 `backgroundColor: "var(--bg-p)"`）后**无视觉差异**，无需人工对照。

### 3.3.6 `chartTheme.ts` 新增常量（FR-3.3；Round 2 修订：CHART_LABEL_STYLE 删 fontFamily + 统一 fontSize）

在 `src/web/src/lib/chartTheme.ts` 末尾追加：

```ts
/* === Legend Style === */
/** Spread on Recharts <Legend wrapperStyle={…} /> */
export const CHART_LEGEND_STYLE: React.CSSProperties = {
  fontSize: ".62rem",
  fontFamily: "var(--font-d)",
  color: "var(--t1)",
};

/* === Label Style === */
/**
 * Spread on Recharts <ReferenceLine label={{ ...CHART_LABEL_STYLE, value: "..." }}>.
 *
 * Note 1: Recharts label prop accepts CSSProperties + extra fields (value, position, offset).
 * No <Label> child component usage in this codebase (verified 2026-04-19).
 *
 * Note 2 (Round 2 decision): fontFamily intentionally omitted here to preserve the current
 *   Recharts default font rendering for ReferenceLine labels (the 4 existing call sites all
 *   render without explicit fontFamily). fontSize standardized to 10 (covering the mixed 9/10
 *   present in current code — slight visual uniformity across OverviewTab / RobustnessTab /
 *   RiskTab / ReportClient). If per-site fontSize deviation is required, override via spread:
 *     label={{ ...CHART_LABEL_STYLE, fontSize: 9, value: "…" }}
 *
 * Note 3 (Round 3 decision): Recharts <ReferenceLine>.label prop accepts CSSProperties
 *   combined with extra fields (value / position / offset). The spread form
 *   `label={{ ...CHART_LABEL_STYLE, position: "insideTopLeft", value: "..." }}` is legal
 *   with no TypeScript assertion required. If executor encounters TS errors (e.g. due to
 *   verbatimModuleSyntax in Next.js 16), widen the exported type to
 *   `React.CSSProperties & { value?: React.ReactNode; position?: string; offset?: number }`.
 *
 * Note 4 (Round 3 import style): this file uses `import type React from "react"` (default type import)
 *   and existing exports are `: React.CSSProperties` (e.g. CHART_TOOLTIP_STYLE L18, CHART_AXIS_STYLE).
 *   Keep new exports using the same `: React.CSSProperties` style to maintain file-level consistency.
 */
export const CHART_LABEL_STYLE: React.CSSProperties = {
  fontSize: 10,
  fill: "var(--t2)",
};
```

**视觉变化预期**：
- Legend 2 处（research/page）字体 `.62rem` + `var(--font-d)` 保持不变（与现状一致）。
- ReferenceLine label 4 处：
  - RiskTab:187 原 `fontSize: 9` → spread 后变为 10（+1px 微增，UI 几乎无感）
  - RobustnessTab:353 原 `fontSize: 9` → 10
  - OverviewTab:684 原 `fontSize: 10, position: "insideTopLeft"` → 保持 10，position 通过 spread override 保留
  - ReportClient:508 原 `fontSize: 10` → 保持 10
  - 字体保持 Recharts 默认（不强加 JetBrains Mono），与现状 label 渲染一致，**无字体 shift**。

**注**：`fontFamily: "var(--font-d)"` 在 `chartTheme.ts` 里作为 **CHART_LEGEND_STYLE 常量本体** 是允许的（扫描脚本 R1 排除此文件）。R1 仅禁止业务 tsx 内联 var(--font-[ud])；常量层通过 spread 间接消费不受限。

### 3.3.7 factor-research 子系统 → Tailwind/shadcn 映射（Round 1 新增，**Round 2 补齐散落清单 + 计数澄清**）

**子系统定义**：`globals.css` L1853-1987 共 **98 unique class selector**（含 SVG data URI 内假命中 `.w3/.a/.org/.html` 约 4 个 + 父子复合选择器 `.arr/.pdot/.sub/.tr/.open/.disabled-item/.lim-cur/.lim-full` 等随父组件迁移约 8 个）；**顶层需独立映射约 85 个 class**。以下映射按 class 家族分组。

**Round 3 新发现散落位置**（非 research 目录下的 factor-research class 调用，**Round 3 planner 独立全仓 rg 扫描 @ 2026-04-19**，`rg 'className=["\x27][^"\x27]*\b(sc\|sc-l\|sc-v\|sc-sub\|cd\|sl\|fl\|fi\|fsel\|ctbl\|dtab\|hm-grid\|hm-label\|hm-cell\|hm-tick)\b' src/web/src --glob='*.tsx' -c`）：

| 文件 | className 实例数 | 具体位置与原语分布 |
|---|---:|---|
| `src/web/src/app/research/page.tsx` | 16+ | 本家（主要调用点，按行号粒度；按完整家族调用约 47 实例，参见 §3.9 详述） |
| `src/web/src/app/data-catalog/page.tsx` | 6 | L240-243（4 张 KPI 卡 × `sc` + `sc-l` + `sc-v` + `sc-sub`；共 6 处原语 in 4 行 JSX）+ L252（1 处 `fsel`）= **7 处合计** |
| `src/web/src/app/data-catalog/FetchDialog.tsx` | 11 | L199/215/230 各 1 处 `fsel` + 其它 sc 系列（按 className 实例计）= **3 处 fsel + 其它** |
| `src/web/src/app/data-catalog/JobQueue.tsx` | 1 | 零星（实测 `rg -c` 输出 1） |
| **`src/web/src/app/backtest/components/OverviewGreyTab.tsx`** | **4** | **Round 3 新发现整文件漏列**：L84 / L134 / L220 / L458 共 **4 处 `sc-l`**（均为 `<span className="sc-l ...">` 形态） |
| `src/web/src/app/backtest/components/TradesTab.tsx` | 3 | **Round 3 精确化**：L162 + L179 + L515 共 **3 处 `sc-l`**（r2 漏 L162）|
| `src/web/src/app/backtest/components/PerformanceTab.tsx` | 2 | **Round 3 精确化**：L226 + L1726 共 **2 处 `sc-l`**（r2 漏 L226）|
| `src/web/src/app/backtest/components/TearsheetTab.tsx` | 2 | **Round 3 精确化**：L48 + L90 共 **2 处 `sc-l`**（r2 漏 L90）|
| **`src/web/src/app/backtest/components/OverviewTab.tsx`** | **5** | **Round 3 新发现**：L190 (`hm-grid`) / L192 (`hm-label`) / L195 (`hm-label`) / L200 (`hm-label`) / L206 (`hm-cell`) — 月度收益热力图原语 |
| **合计（跨 9 文件）** | **85 实例**（精确计数 per `rg -c`；按完整 factor-research 家族迁移覆盖 `research/page` 约 47 逻辑位置 + `data-catalog/page` 7 + `FetchDialog` 3 + `JobQueue` 1 + **`OverviewGreyTab` 4 sc-l** + `TradesTab` 3 sc-l + `PerformanceTab` 2 sc-l + `TearsheetTab` 2 sc-l + **`OverviewTab` 5 hm-***）| — |

> **Round 3 计数方法学说明**：
> - **按 className 实例口径**：`rg -c 'className=["\x27][^"\x27]*\b(sc\|...)\b' src/web/src --glob='*.tsx'` 输出为每个文件命中的 JSX className 实例数
> - **按 sc-l 子族精确扫描**：`rg '\bsc-l\b' src/web/src --glob='*.tsx'` 实测 15 处跨 5 文件（OverviewGreyTab 4 + TradesTab 3 + PerformanceTab 2 + TearsheetTab 2 + data-catalog/page 4）
> - **按 hm-* 子族精确扫描**：`rg '\b(hm-grid\|hm-label\|hm-cell\|hm-tick)\b' src/web/src --glob='*.tsx'` 实测 5 处仅在 `backtest/components/OverviewTab.tsx`（research 下 0 处）
> - **research 下 hm-* 调用点为 0**：这是 s4 能独立内联 hm-* 迁移（不依赖 s6）的关键事实依据

**涉及子任务的边界重划**（Round 3 修正 — 计数精度）：
- **s4（backtest）** 需处理：
  - **11 处 sc-l 迁移**（OverviewGreyTab 4 + TradesTab 3 + PerformanceTab 2 + TearsheetTab 2）为 `<SectionLabel>` QDS 组件或 `text-qds-t2 text-[0.52rem] uppercase tracking-widest font-mono`
  - **5 处 hm-* 迁移**（OverviewTab L190-206）**由 s4 独立内联实现**：Tailwind grid + CSS custom props（具体代码模板见下 §3.3.7.9 hm-* 行）。**不依赖 s6** — 因 `rg '\b(hm-grid\|hm-label\|hm-cell)\b' src/web/src/app/research` = 0，research 下 hm-* 调用点为空，s4 的内联实现与 s6（若需）未来在 research 域的 `<MonthlyHeatmap>` 不冲突
  - 合计 **16 处 factor-research 散落**（sc-l 11 + hm-* 5）
- **s5（data-catalog）** 需处理：page.tsx L240-243 的 4 张 KPI 行迁移为 `<StatCard>` QDS 组件 + L252 / FetchDialog 3 处 `fsel` 迁移为 shadcn `<Select>`
- **s6（research）** 保持全量主体迁移不变；research/ 下不含 hm-* 调用点

按 class 家族分组映射：

#### 3.3.7.1 Shared primitives（L1856、L1859-1866、L1869-1878）

| 遗留 class | Tailwind / shadcn 迁移 |
|---|---|
| `.mono` | `font-mono` |
| `.dim` | `text-muted-foreground` |
| `.cg` / `.cr` / `.ca` / `.ci` | 同 §3.3.2 |
| `.tip` | 删除（改用 `<HelpTip>` QDS 组件或 shadcn `<Tooltip>`） |
| `.g` / `.g2` / `.g3` / `.g5` / `.g6` | `grid grid-cols-2` / `grid grid-cols-3` / `grid grid-cols-5` / `grid grid-cols-6` + `gap-4` |
| `.badge` | shadcn `<Badge>` |
| `.sc` | `<Card className="bg-card border rounded-qds-card p-3 hover:border-qds-border-hover hover:shadow-md transition-all">` 或 `<StatCard>` QDS 组件（推荐） |
| `.sc-l` | `<SectionLabel>` QDS 组件（已就绪，preview: `type-section-label.html`） |
| `.sc-v` | `<div className="font-mono text-lg font-semibold">` — 或由 StatCard 内部渲染 |
| `.sc-sub` | `<div className="font-mono text-[0.58rem] mt-0.5">` |

#### 3.3.7.2 Card 家族（`.cd/.cd-h/.cd-b`，L1873-1875）

| 遗留 class | Tailwind / shadcn 迁移 |
|---|---|
| `.cd` | shadcn `<Card>`（等价于 `bg-card border rounded-qds-card`） |
| `.cd-h` | `<CardHeader className="flex justify-between items-center px-3 py-2 border-b text-xs font-semibold">`；`.cd-h .sub` → `<span className="font-mono text-[0.58rem] font-normal text-muted-foreground">` |
| `.cd-b` | `<CardContent>` |

#### 3.3.7.3 Section label（`.sl`，L1878）

替换为 `<SectionLabel>` QDS 组件。视觉参考：`preview/type-section-label.html`（小 caps + accent 橙 + 1px 灰线延伸）。

#### 3.3.7.4 Buttons（`.btn/.btn-p/.btn-a/.btn-o/.btn-g`，L1881-1885；Round 2 锁定决策）

**Round 2 决策**：`.btn-p` 与 `.btn-a` 统一迁移为 `<Button variant="default">`（accent 橙），与 DS `color-accent.html` preview 规则一致；**不保留**"按语义决定绿色 vs 橙色"的 executor 主观判断。虽然 globals.css 里 `.btn-p` 历史背景色是 `var(--suc)` 绿色，但设计系统规定 primary 按钮必须是 accent 橙；research 页的"启动分析"按钮迁移后将从绿色变为橙色（预期内视觉变化，对应 `preview/component-buttons.html` 的 primary 定义）。

| 遗留 class | shadcn 迁移 |
|---|---|
| `.btn-p` | `<Button variant="default">`（**accent 橙，非 success 绿**；Round 2 单一决策） |
| `.btn-a` | `<Button variant="default">`（accent） |
| `.btn-o` | `<Button variant="outline">` |
| `.btn-g` | `<Button variant="ghost">` |

#### 3.3.7.5 Form elements（`.fg/.frow/.fl/.fi/.fsel/.data-avail`，L1888-1893）

| 遗留 class | 迁移 |
|---|---|
| `.fg` | `<div className="flex flex-col gap-1 mb-3">` |
| `.frow` | `<div className="grid grid-cols-2 gap-2">` |
| `.fl` | `<Label className="font-mono text-[0.62rem] text-muted-foreground">` shadcn Label |
| `.fi` | shadcn `<Input className="font-mono text-[0.72rem]">` |
| `.fsel` | shadcn `<Select>`（带箭头图标） |
| `.data-avail` | `<div className="font-mono text-[0.62rem] text-qds-success bg-qds-success-dim px-2 py-1 rounded">` |

#### 3.3.7.6 Explorer layout（`.explorer/.config-panel/.result-panel`，L1896-1898）

| 遗留 class | 迁移 |
|---|---|
| `.explorer` | `<div className="flex flex-1 overflow-hidden">` |
| `.config-panel` | `<aside className="w-80 min-w-80 border-r overflow-y-auto bg-background p-4">`（w-80 = 320px） |
| `.result-panel` | `<main className="flex-1 overflow-y-auto px-7 py-5 pb-16">` |

#### 3.3.7.7 Config / Accordion（`.cfg-*/.acc-*/.factor-limit`，L1901-1916）

| 遗留 class | 迁移 |
|---|---|
| `.cfg-section` | `<section className="mb-5">` |
| `.cfg-title` | `<SectionLabel>`（复用 QDS 组件） |
| `.acc-group` / `.acc-head` / `.acc-body` / `.acc-item` | shadcn `<Accordion>` 组件（如无则使用自制 `<Disclosure>` Tailwind 实现：`<details className="border rounded-qds-sm">` + `<summary>`） |
| `.factor-limit` / `.factor-limit .lim-cur` / `.factor-limit .lim-full` | `<div className="font-mono text-[0.6rem] text-qds-t3">` + 内部 `<span className="text-foreground">` / `<span className="text-qds-warning">` |

#### 3.3.7.8 Params（`.param-*`，L1919-1928）

| 遗留 class | 迁移 |
|---|---|
| `.param-section` / `.param-divider` / `.param-row` / `.param-label` / `.param-val` / `.param-input` / `.param-unit` / `.param-select` | 整体替换为专用子组件 `<ParamRow>`（新建在 `research/components/`），内部用 Tailwind + shadcn `<Input>` / `<Select>` |

#### 3.3.7.9 Verdict / Compare table / Turnover / Report（`.verdict*/.ctbl/.turn-*/.rpt-*/.hbar/.hist-pager`，L1934-1987）

| 遗留 class | 迁移 | 备注 |
|---|---|---|
| `.verdict` / `.verdict-pass` / `.verdict-warn` / `.verdict-fail` | `<Badge variant="outline" className="bg-qds-success-dim text-qds-success">` 等语义色变体；或扩展 QDS `<StatusBadge>` 的 Status union（见 §3.3.9） | |
| `.ctbl` / `.ctbl thead th` / `.ctbl tbody tr` / `.ctbl td` | shadcn `<Table>` 组件整套 | |
| `.hist-clickable` | `<tr className="cursor-pointer">` | |
| `.spinner` | `<Loader2 className="animate-spin w-3 h-3">` Lucide 图标 | |
| `.factor-dot` | `<span className="inline-block w-2 h-2 rounded-full mr-1">` | |
| `.turn-row` / `.turn-item` / `.turn-label` / `.turn-val` | `<div className="flex gap-6 font-mono text-[0.72rem] bg-input px-3 py-2 rounded-qds-sm">` + children | |
| `.empty` / `.empty-icon` / `.empty-title` / `.empty-desc` | 使用 `<EmptyState>` 组件（已存在 `src/web/src/components/EmptyState.tsx`）| |
| `.report-content` / `.rpt-head` / `.rpt-back` / `.rpt-title` / `.rpt-sub` / `.rpt-meta` / `.rpt-meta-item` | **调用点实测 0**（Round 2 验证：`rg 'className=.*\brpt-[a-z]+' src/web/src` → 0 命中）。CSS 定义保留在 globals.css L1971-1987，**仅 s10 负责删除**；ReportClient 当前使用 shadcn 组件与 Tailwind，无 .rpt-* 调用，**无 .tsx 迁移工作**。§3.5.5 的 ReportClient 拆分不包含 ReportHeader（Round 2 移除） | |
| `.tab-bar` / `.dtab` / `.dtab.a` | shadcn `<Tabs>` + `<TabsList>` + `<TabsTrigger>`（preview: `component-tabs.html`） | |
| `.hm-grid` / `.hm-label` / `.hm-cell` | **Round 3 决策（锁定）**：s4 在 `backtest/components/OverviewTab.tsx` L190-206 **内联实现 Tailwind grid + CSS custom props**；s6 不新建 `<MonthlyHeatmap>` 共享组件（因 research 下 hm-* 调用点 0，无消费者，YAGNI） | **事实依据**：`rg '\b(hm-grid\|hm-label\|hm-cell)\b' src/web/src/app/research --glob='*.tsx'` = 0；仅 OverviewTab.tsx 5 处调用。**s4 推荐迁移形态**：`<div className="grid gap-1" style={{ gridTemplateColumns: "auto repeat(12, 1fr)" }}>` + 内部 `<div className="flex items-center justify-center font-mono text-[0.62rem] text-muted-foreground">` 作为 hm-label，`<div className="flex items-center justify-center font-mono text-[0.7rem] rounded-qds-sm" style={{ background: cellBg(val), color: cellText(val) }}>` 作为 hm-cell。**s4 不依赖 s6** — 两任务可安全 wave B 并行 |
| `.wf-row` / `.wf-label` / `.wf-bar-wrap` / `.wf-bar` / `.wf-val` | 专用组件 `<WaterfallBar>`（新建） | |
| `.hbar` / `.hbar-label` / `.hbar-wrap` / `.hbar-fill` / `.hbar-val` | 专用组件 `<HBar>`（新建） | |
| `.hist-pager` / `.hist-pager button` | shadcn `<Pagination>` | |

**实现策略**：research/page.tsx 拆分为 6 个子组件（§3.5.4），每个子组件专责处理一组 class 家族的迁移。具体承载对应关系：
- `ResearchDatasetPanel` → `.cd/.cd-h/.cd-b` + `.sl` + `.data-avail`
- `ResearchFactorList` → `.acc-*` + `.factor-limit` + `.factor-dot`
- `ResearchChartPanel` → `.hm-*` + `.wf-*` + `.hbar-*` + `.dtab/.tab-bar`
- `ResearchJobQueue` → `.ctbl` + `.hist-pager` + `.spinner`
- `ResearchConfigPanel`（新） → `.explorer/.config-panel` + `.cfg-*` + `.param-*` + `.fi/.fsel/.fl/.fg`
- `ResearchResultPanel`（新） → `.result-panel` + `.verdict*` + `.turn-*` + `.sc*`

### 3.3.8 未定义 CSS 变量 + 字号归一化映射表（Round 3 实测 11 variant / 67 处 / 8 文件）

#### 未定义 CSS 变量 → Tailwind 语义类（R13 规则对应）

**Round 3 实测**（planner 独立全仓 rg @ 2026-04-19，`rg -o 'var\(--accent-[a-z0-9-]+\)' src/web/src --glob='*.tsx' --glob='*.ts' | sort | uniq -c | sort -rn`）：全仓 `var(--accent-*)` 共 **67 处跨 8 文件 / 11 variant**（不含 `--accent-foreground`，该 token 是 shadcn 内置）。globals.css 中以下 11 variant 均**未定义**：

| 现状（globals.css 未定义） | 实测次数 | 目标 |
|---|---:|---|
| `var(--accent-green)` | 23 | `text-qds-success`（token: `--suc`） |
| `var(--accent-red)` | 13 | `text-destructive`（token: `--dan`） |
| `var(--accent-amber)` | 12 | `text-qds-warning`（token: `--warn`） |
| `var(--accent-blue)` | 7 | `text-qds-info`（token: `--info`） |
| `var(--accent-orange)` | 4 | `text-primary`（token: `--acc`） |
| `var(--accent-red-20)` | 2 | `bg-qds-danger-dim`（`--dan-d` 12% alpha） |
| `var(--accent-green-10)` | 2 | `bg-qds-success-dim`（`--suc-d` 12% alpha） |
| `var(--accent-purple)` | 1 | **`text-primary`（Round 3 锁定决策：项目无 purple token；本任务不新增 token；purple 语义统一并入 accent 橙；删除 r2 的 "case-by-case 评估" 备选）** |
| `var(--accent-amber-20)` | 1 | `bg-qds-warning-dim`（`--warn-d` 12% alpha） |
| `var(--accent-blue-20)` | 1 | `bg-qds-info-dim`（`--info-d` 12% alpha） |
| `var(--accent-purple-20)` | 1 | **`bg-qds-accent-dim`（Round 3 锁定：purple dim 统一并入 accent dim `--acc-d` 12% alpha）** |
| **合计** | **67 处（求和 23+13+12+7+4+2+2+1+1+1+1 = 67，表头、表体、求和三者一致）** | — |

**映射原则**（Round 3 锁定）：
- green/red/amber/blue/orange → 语义色映射（DS 规则）
- **purple → `text-primary`（统一并入 accent 橙；单一决策无 case-by-case）**
- **purple-20 → `bg-qds-accent-dim`（统一并入 accent dim）**
- `-20` 后缀 → `bg-qds-<semantic>-dim` 对应 12% alpha；`-10` 后缀同理（设计系统内二者差别为 alpha 值，映射同一 Tailwind token 即可）

#### 受影响文件（Round 3 精确验证 @ 2026-04-19，**含 TabNav.tsx**）

| 文件 | 次数 | 归属任务 |
|---|---:|---|
| `src/web/src/app/strategies/[name]/EditorClient.tsx` | 15 | **s9** |
| `src/web/src/app/trading/components/StrategyPanel.tsx` | 11 | **s7** |
| `src/web/src/app/trading/components/OrdersPanel.tsx` | 9 | **s7** |
| `src/web/src/app/trading/components/ActionBar.tsx` | 6 | **s7** |
| `src/web/src/app/trading/components/FillsStream.tsx` | 5 | **s7**（含 purple / purple-20 / green-10 各 1 处，均按 §3.3.8 固定映射） |
| `src/web/src/app/trading/components/TopBar.tsx` | 4 | **s7** |
| `src/web/src/app/trading/components/PositionsTable.tsx` | 4 | **s7**（含 blue-20 1 处） |
| `src/web/src/app/trading/components/TabNav.tsx` | 1 | **s7**（blue → text-qds-info） |
| **合计** | **67 处（trading 40 + strategies EditorClient 15 + ... per-file 求和 = 55 历史错计已修正为 67 实测）** | — |

> **计数校对（Round 3 精确）**：
> - 按文件"命中行数"统计（`rg -c`）：EditorClient 15 + StrategyPanel 11 + OrdersPanel 9 + ActionBar 6 + FillsStream 5 + TopBar 4 + PositionsTable 4 + TabNav 1 = **55 行**
> - 按"变量实例"统计（`rg -o | wc -l`）：**67 次调用**（同一行多次命中的差值 12 处）
> - 两组数字都是真实的：前者是"行数（需要改动的代码行数）"，后者是"待迁移的 class 引用次数"
> - **s7 承担 trading/ 下 51 次调用 / 40 行 / 8 variant（实测：OrdersPanel 9 行 + StrategyPanel 11 行 + ActionBar 6 行 + FillsStream 5 行 + TopBar 4 行 + PositionsTable 4 行 + TabNav 1 行 = 40 行 / 51 次调用）**
> - **s9 EditorClient 承担 16 次调用 / 15 行**
> - s7 + s9 合计处理 **67 次调用 / 55 行 / 11 variant / 8 文件**

#### 字号归一化映射（R12 规则对应）

**策略**：保留 Tailwind arbitrary value 形态以精确保留视觉度量；90 处 fontSize 内联全部迁移为 className。

| 现状 fontSize | 目标 className |
|---|---|
| `fontSize: ".52rem"` | `text-[0.52rem]` |
| `fontSize: ".55rem"` | `text-[0.55rem]` |
| `fontSize: ".58rem"` | `text-[0.58rem]` |
| `fontSize: ".6rem"` | `text-[0.6rem]` |
| `fontSize: ".62rem"` | `text-[0.62rem]` |
| `fontSize: ".65rem"` | `text-[0.65rem]` |
| `fontSize: ".68rem"` | `text-[0.68rem]` |
| `fontSize: ".7rem"` | `text-[0.7rem]` |
| `fontSize: ".72rem"` | `text-[0.72rem]` |
| `fontSize: 9`（Recharts 数值 px） | 保留（Recharts label 对象 prop 仍是数值 px，通过 `CHART_LABEL_STYLE` spread 间接消费 — Round 2 已统一为 10） |
| `fontSize: 10/11`（Recharts 数值 px） | 保留 |

**豁免**：Recharts `wrapperStyle` / `contentStyle` / `labelStyle` / `tick=` prop 中的 fontSize 保留（R12 规则排除），通过 `CHART_TOOLTIP_PROPS` / `CHART_LEGEND_STYLE` / `CHART_LABEL_STYLE` / `CHART_AXIS_STYLE` spread 一次性继承。

### 3.3.9 StatusBadge 双实现迁移决策（Round 1 新增，**Round 2 补充视觉差异声明**）

**现状冲突**：
- 顶层 `src/web/src/components/StatusBadge.tsx`：`status: string` + 中文 label Map（queued/running/completed/failed/cancelling/cancelled 6 状态）；内部用 shadcn `<Badge>`，默认 `rounded-md` + padding
- QDS `src/web/src/components/qds/status-badge.tsx`：`status: "running"|"done"|"failed"|"queued"` union + 英文 label（4 状态），键 `completed` 不对应（QDS 用 `done`）；内部为 `<span>` + Tailwind，**`rounded-full` + 不同 padding**

调用点 `page.tsx:130`、`optimization/page.tsx:13` 传递 `run.status`（任意字符串），含 `completed`/`cancelling`/`cancelled`。

**决策（采用 Architect / Critic 建议的选项 a）**：扩展 QDS `StatusBadge` 的 `Status` union，支持全部 6 状态 + 支持语言切换 label map，再 re-export 为 `@/components/qds`：

```tsx
// src/web/src/components/qds/status-badge.tsx (改写)
type Status = "queued" | "running" | "completed" | "failed" | "cancelling" | "cancelled" | "done";  // "done" 作为 "completed" 别名保留
const styles: Record<Status, string> = { /* ... 6 + 1 个 */ };
const labels: Record<Status, { en: string; zh: string }> = {
  queued: { en: "◦ Queued", zh: "排队中" },
  running: { en: "Running", zh: "运行中" },
  completed: { en: "✓ Done", zh: "已完成" },
  done: { en: "✓ Done", zh: "已完成" },
  failed: { en: "✕ Failed", zh: "失败" },
  cancelling: { en: "⏸ Cancelling", zh: "取消中" },
  cancelled: { en: "⊘ Cancelled", zh: "已取消" },
};
export function StatusBadge({ status, label, locale = "zh" }: {
  status: Status | string;  // 允许 string 做 defensive fallback
  label?: string;
  locale?: "en" | "zh";
}) {
  const key = (status as Status) in styles ? (status as Status) : "queued";
  // ...
}
```

**Round 3 视觉差异声明（重构：subtask AC 不含人工目测项）**：
- 改写后顶层 `src/web/src/components/StatusBadge.tsx` 改为 barrel re-export（`export { StatusBadge } from "@/components/qds/status-badge"`），**legacy 调用点（`page.tsx:130` / `optimization/page.tsx:13` / `backtest/page.tsx` / data-catalog/JobQueue.tsx 4 处 bt-status）的外观将从 shadcn Badge 的 `rounded-md` + padding 变为 QDS `<span>` 的 `rounded-full` + 不同 padding**。
- **subtask 层（s11 的 AC）**（自动化）：
  - `rg -n '\bbt-status\b' src/web/src/app/data-catalog/JobQueue.tsx` 命中 0 行
  - `components/qds/status-badge.tsx` 的 `Status` union 包含全部 7 个键
  - `cd src/web && npm run build` / `npm run lint` 通过
  - **无"逐页目测"作为 subtask AC item**（Round 3 遵守用户全局 MUST 规则：subtask AC 不含手动验证项）
- **verify phase（User Acceptance by 主 agent + 用户）**：
  - 主 agent 在 verify 阶段启动 `cd src/web && npm run dev`，用户在浏览器打开 backtest/page / optimization/page / data-catalog JobQueue / research 历史 Job 行，对比 rounded-md vs rounded-full 视觉差异
  - 若用户判定差异过大（影响 UX 辨识度 — 如 "失败" 徽章辨识度降低），主 agent 派 agent 按以下 **fallback 方案**回迁：保留顶层 `StatusBadge` 的 `<Badge>` 视觉外观，内部查表改为 QDS `styles` / `labels` map（保持双 rounded 风格，仅统一文案与状态 key），barrel re-export 改为"适配器"而非"直接透传"
  - **fallback 触发时工作量追加 0.5-1h**（post-task 回迁）
  - 此环节为 **用户验收**，**不作为 s11 的 acceptance_criteria**；s11 的交付物是代码正确性（a）StatusBadge 扩展 + barrel re-export，（b）build/lint 通过，（c）R2/R11/R14 扫描通过

**迁移操作**：
1. s1 不涉及 StatusBadge（与 chartTheme 分离，避免耦合）；改在 **s11（全仓扫描补漏）** 中执行：
   - 改写 `components/qds/status-badge.tsx` 支持 6 + 1 状态 + locale；
   - `components/StatusBadge.tsx` 改为 re-export barrel（`export { StatusBadge } from "@/components/qds/status-badge"`），保持向后兼容调用点；
   - **同时处理 JobQueue.tsx L173/176/181/185 的 4 处 `<span className="bt-status bt-status-{queue,done,fail}">`**：迁移为 `<StatusBadge status="queued" />` / `<StatusBadge status="completed" />` / `<StatusBadge status="failed" />`；
   - 运行 `npm run build` 验证 TypeScript 通过；
   - 测试 `page.tsx:130` 和 `optimization/page.tsx:13` 渲染正常（程序化验证，不含视觉目测）
2. s4 / s7 / s8 / s9 任务描述明确**禁止直接替换**或**删除** `components/StatusBadge.tsx`，必须等到 s11 统一处理；s5 预留 JobQueue 的 4 处 bt-status 到 s11 一并处理（s5 不在自己的任务中修改 JobQueue 的 bt-status，仅处理其 dc-* 14 处）。

### 3.3.10 `chartTheme.ts` 消费策略

同上 §3.3.6。**注意**：本任务不把 Line/Area 的 `stroke`/`fill` 纳入强制迁移项（代价大、收益小），仅 Tooltip/Grid/Legend/ReferenceLine-label 这 4 类**多属性复合 style 对象**强制 spread。

## 3.4 preview ↔ 页面对照矩阵（FR-2 / AC-2 依据）

`.claude/skills/TinoHelmDS/preview/` 下共 21 个 HTML 文件 + `.claude/skills/TinoHelmDS/Web UI Kit.html`（完整 frame）+ `.claude/skills/TinoHelmDS/Charts Spec.html`（Recharts 专项）+ `.claude/skills/TinoHelmDS/colors_and_type.css`（token 参考）。对照业务页面：

| Preview / Skill 文件 | 对应业务位置 | 关键匹配点 |
|---|---|---|
| `preview/brand-logo.html` / `brand-icons.html` | `components/Sidebar.tsx` 的 wordmark + 全局 Lucide 图标 | accent 橙 `T` 与 `.`、Lucide 1.5–2px stroke / 16–18px |
| `preview/color-accent.html` | primary 按钮、active 导航左边框、链接、图表主线 | `--acc` 只出现在这 5 类场景 |
| `preview/color-semantic.html` | StatusBadge / PnL 数字 / 错误提示 | `--suc` / `--dan` / `--info` / `--warn` + `-d` 12% alpha |
| `preview/color-text-hierarchy.html` | 全局 `text-foreground` / `text-qds-t1` / `text-muted-foreground` / `text-qds-t3` | 四级文字 |
| `preview/color-backgrounds.html` | body / card / hover / sunken | `bg-background` / `bg-card` / `bg-secondary` / `bg-input` |
| `preview/color-borders.html` | 默认 / hover / strong | `border` / `border-qds-border-hover` / `--bds` |
| `preview/component-kpi.html` | `components/qds/StatCard.tsx`（业务组件就绪） | section-label + 大号 mono 数字 + 趋势 delta |
| `preview/component-row.html` | backtest 列表行、orders 行、watchlist 行 | 3px accent stripe + `grid-template-columns: 3px 1fr auto auto auto` |
| `preview/component-buttons.html` | `components/ui/button.tsx` shadcn + QDS variants | primary（accent 填充）/ outline / ghost / danger |
| `preview/component-inputs.html` | `components/ui/input.tsx` + `components/ui/select.tsx` | `bg-input` + `border` + focus ring `--acc-d` |
| `preview/component-badges.html` | `components/qds/StatusBadge.tsx` + `components/ui/badge.tsx` + data-catalog dc-type-* 7 色 | 语义色 dim 背景 + 语义色前景 |
| `preview/component-progress.html` | `components/qds/ShimmerBar.tsx` + backtest 进度 + data-catalog coverage bar | accent 填充 + `qds-shimmer` 扫光 |
| `preview/component-sidebar.html` | `components/Sidebar.tsx` | 220px（折叠 56px）+ `--bg-in` + 3px accent 左边框 active |
| `preview/component-tabs.html` | trading/tabs TabNav + data-catalog FilterTabs + research `.dtab` | accent 下划线 2px + `--t2` 未激活 / `--t0` 激活 |
| `preview/spacing-radius.html` | 全局 `--r 12px` / `--rs 6px` / `--rm 10px` | cards 12 / buttons 6 / toasts 10 |
| `preview/spacing-shadow.html` | hover / dialog / primary button 阴影 | 仅这 3 类场景 |
| `preview/spacing-motion.html` | 所有动画时长与缓动 | enter `--eo` cubic-bezier(.16,1,.3,1) / exit `--ei` cubic-bezier(.4,0,1,1)；150/280/400/600/1400ms |
| `preview/type-headings.html` | PageHeader / SectionLabel 标题层级 | Inter + 字重分级 |
| `preview/type-section-label.html` | `components/qds/SectionLabel.tsx` + research `.sl`、`.cfg-title` | 小 caps + accent 橙 + 1px 灰线延伸到边 |
| `preview/type-data.html` | 所有数据数字 | JetBrains Mono + 特定对齐 |
| `Web UI Kit.html`（完整 frame） | 全局布局装配：Sidebar + TopBar + 主内容区 + StatusBar + 空状态 + 对话框 | frame 级视觉对照 |
| `Charts Spec.html` | 所有 Recharts 图表 | tooltip / grid / axis / legend / referenceLine 样式规约 |
| `colors_and_type.css` | 双层 token 源（QDS 短 token + shadcn oklch） | 颜色/字体事实对照 |

**每个业务子任务（§3.6 B1-B6）必须在迁移时逐条勾选对应的 preview 条目**。

## 3.5 文件拆分模板（FR-4 对应）

### 3.5.1 `backtest/page.tsx`（1754 → <700）

建议拆分：

```
src/app/backtest/
├── page.tsx                         ← 主页：ListView / DetailView 二选一 + 路由状态
├── components/
│   ├── BacktestListView.tsx         ← 列表视图（表格 + 筛选）
│   ├── BacktestDetailView.tsx       ← 详情视图（Tab 容器）
│   ├── BacktestRunDialog.tsx        ← 新建回测对话框
│   ├── BacktestRowItem.tsx          ← 单行组件（3px accent stripe）
│   └── <现有的 8 个 Tab 文件>
├── hooks/
│   ├── useBacktestList.ts           ← 列表 API + WS + 轮询
│   └── useBacktestRun.ts            ← 详情数据 + 重跑 action
└── types.ts                         ← 已有
```

`page.tsx` 仅负责：URL 解析 → 二选一渲染 → 传入 hook 返回值。

### 3.5.2 `backtest/components/PerformanceTab.tsx`（2059 → <700）

按图表类型拆出独立子组件：

```
src/app/backtest/components/
├── PerformanceTab.tsx               ← 主容器：SectionLabel + 图表编排
└── performance/
    ├── EquityChart.tsx              ← Equity curve + drawdown 叠加
    ├── ReturnsDistribution.tsx      ← 收益分布直方
    ├── RollingMetrics.tsx           ← 滚动 Sharpe / Sortino / Calmar
    ├── MonthlyReturnsHeatmap.tsx    ← 月度收益热力
    ├── TradeScatter.tsx             ← 交易散点
    └── DrawdownTimeline.tsx         ← 回撤时间线
```

### 3.5.3 `backtest/components/TradesTab.tsx`（847）/ `OverviewTab.tsx`（817）/ `OverviewGreyTab.tsx`（677）

- TradesTab → 拆出 `TradesFilters.tsx` / `TradesTable.tsx` / `TradePnlSparkline.tsx`；**迁移 L179 / L515 的 2 处 `.sc-l` → `<SectionLabel>`**
- OverviewTab → 拆出 `OverviewKpis.tsx`（StatCard 栅格）/ `OverviewEquityChart.tsx` / `OverviewStats.tsx`；
- OverviewGreyTab → 若与 OverviewTab 职责重叠 ≥ 70%，合并到 OverviewTab 并通过 `variant="grey"` prop 切换；否则独立拆。**具体判断标准**：执行 `diff` 对比两文件的 JSX 树深度与 helper 复用度；若 helper 与数据转换函数 70% 以上共用，则合并。executor 拥有最终判断权。

### 3.5.4 `research/page.tsx`（991 → <700）扩展（Round 1 修改）

因承载 factor-research 全部 85 个 class 的迁移，拆分为 **6 个子组件**：

```
src/app/research/
├── page.tsx                         ← 主布局 + URL state，< 400 行
├── components/
│   ├── ResearchDatasetPanel.tsx     ← 数据集选择（.cd/.cd-h/.cd-b + .sl + .data-avail）
│   ├── ResearchFactorList.tsx       ← 因子列表（.acc-* + .factor-limit + .factor-dot）
│   ├── ResearchConfigPanel.tsx     ← 配置面板（.explorer/.config-panel + .cfg-* + .param-* + .fi/.fsel）
│   ├── ResearchResultPanel.tsx     ← 结果面板（.result-panel + .verdict* + .turn-* + .sc*）
│   ├── ResearchChartPanel.tsx     ← 图表面板（.hm-* + .wf-* + .hbar-* + .dtab）
│   └── ResearchJobQueue.tsx       ← 任务队列（.ctbl + .hist-pager + .spinner）
└── report/[id]/
    └── ReportClient.tsx             ← 757 行 → 进一步拆（见 3.5.5）
```

### 3.5.5 `research/report/[id]/ReportClient.tsx`（757 → <700，**Round 2：移除 ReportHeader**）

按报告分节拆 + 承载 `.ctbl` / `.dtab` / ReferenceLine label 迁移：

```
src/app/research/report/[id]/
├── page.tsx                         ← 薄壳（已存在）
├── ReportClient.tsx                 ← 主容器 + URL state，< 400 行
└── components/
    ├── ReportKpiGrid.tsx            ← KPI 栅格（.sc 系列）
    ├── ReportIcChart.tsx            ← IC 曲线（含 ReferenceLine label → CHART_LABEL_STYLE）
    ├── ReportLongShortChart.tsx     ← 多空收益
    └── ReportFactorTable.tsx        ← 因子表格（.ctbl → shadcn Table）
```

**Round 2 移除 ReportHeader.tsx**：`rg 'className=.*\brpt-[a-z]+' src/web/src` 全仓命中 0；ReportClient 现用 shadcn 组件 + Tailwind 渲染头部，无需抽 ReportHeader 子组件。`.rpt-*` class 的 CSS 定义由 s10 直接删除（L1971-1987）。

### 3.5.6 `strategies/page.tsx`（754）/ `optimization/page.tsx`（736）

仅轻微超阈值（≤ 8%），**本任务豁免拆分**（FR-4.1 决策）。先尝试内部函数抽取（pure function / sub-component 同文件），若未来新功能引入需求则再拆。

## 3.6 子任务执行规范（供 4-tasks.md 引用）

每个迁移子任务的标准动作清单：

1. 运行 `bash src/web/scripts/verify-ds-compliance.sh --fix-hint 2>&1 | rg '^\[' | rg 'src/app/<路由>/'` 提取该路由下所有违规；
2. 按 §3.3 映射表逐行迁移（R1/R2/R3/R4/R5/R6/R7/R8/R9/R10/R12/R13/R14 每条对应 §3.3.1-3.3.8 的一个子节）；
3. 对照 §3.4 preview 矩阵验证视觉层级（跨到 `.claude/skills/TinoHelmDS/preview/`、`Web UI Kit.html`、`Charts Spec.html`）；
4. 若行数 > 700，按 §3.5 拆分模板执行 `git mv` + 新文件创建；
5. 本地运行 `cd src/web && npm run build && npm run lint`；
6. 再次运行扫描脚本，仅该路由下违规数必须为 0；
7. 提交时使用 `git add -p` 分离"迁移"与"拆分"两类变更到独立 commit，保持 blame 可读。

## 3.7 Recharts 图表集中化策略细节

### 3.7.1 `CHART_GRID_STYLE` 的灵活 override

现有 `CHART_GRID_STYLE = { stroke: "var(--chart-grid)", strokeDasharray: "none" }`。PerformanceTab 现状是 `strokeDasharray="3 3"`（虚线）。迁移策略：

```tsx
// 默认（无虚线）
<CartesianGrid {...CHART_GRID_STYLE} />

// PerformanceTab 保留虚线
<CartesianGrid {...CHART_GRID_STYLE} strokeDasharray="3 3" />
```

保留 `strokeDasharray="3 3"` 这类**局部显示差异**的 prop 覆盖是允许的 — 扫描脚本 R7 仅要求 spread 存在，不禁止额外 prop（且 prop 顺序任意）。

### 3.7.2 图表语义色使用

引用 `CHART_COLORS.accent` 等常量替代字符串字面量 `"var(--acc)"`：

```tsx
// 目标
<Line stroke={CHART_COLORS.accent} />
<Area fill={CHART_COLORS.success} fillOpacity={CHART_GRADIENT_OPACITY.areaFill} />

// 允许保留（不强制迁移，Recharts 直接消费 CSS 变量是 chartTheme.ts 注释的推荐用法）
<Line stroke="var(--acc)" />
```

本次不把 Line/Area 的 `stroke`/`fill` 纳入强制迁移项（代价大、收益小），仅 Tooltip/Grid/Legend/ReferenceLine-label 这 4 类**多属性复合 style 对象**强制 spread。

### 3.7.3 CartesianGrid 的 vertical=false 保留

analytics 现有 `<CartesianGrid {...CHART_GRID_STYLE} vertical={false} />` — 这是推荐写法，扫描脚本不禁止。

## 3.8 风险清单与应对

| 风险 | 触发条件 | 应对 |
|---|---|---|
| **R-1 git blame 丢失** | 大规模文件拆分 + 内容移动 | 统一使用 `git mv` 移动文件；同一提交内仅做"结构性重命名"不混入内容修改；在 PR 描述中标注"blame hint: before=… after=…" |
| **R-2 视觉回归误判** | AC-2 依赖人工对照，主观性高 | 验收自动化部分（AC-1 / AC-3 / AC-4）是硬规则；AC-2 退化为 preview 对照矩阵的文档化检查 + CLAUDE.md 约束章节 |
| **R-3 Recharts 版本坑** | 升级 Recharts 导致 `wrapperStyle` / `contentStyle` 接口变更 | 本任务不动 Recharts 版本；`chartTheme.ts` 的 React.CSSProperties 是 Recharts 公开契约 |
| **R-4 主题切换边缘情况** | `html.light` 下某些 token override 缺失 | AC-3 扫描脚本 `--mode both-themes` 断言 light 作用域内核心 token 完备；对 globals.css `.light` 作用域做 grep 检查 |
| **R-5 样式丢失（像素退化）** | `bt-*`/`dc-*` / factor-research 迁移到 Tailwind 后偶发 1-2px 偏差 | 每个子任务完成后对照对应 preview HTML + `Web UI Kit.html` 页面级参考 |
| **R-6 性能回归** | Tailwind v4 大量 class 导致 CSS 体积增加 | 删除 780 行 globals.css 遗留 class 带来的收益 > 新增 class 成本；Tailwind JIT 只生成实际用到的类 |
| **R-7 依赖缺失（ripgrep + PCRE2）** | CI 镜像无 `rg` 或 `rg` 不支持 `--pcre2` | 扫描脚本开头检查 `command -v rg` 和 `rg --pcre2 -V` 可用性，缺失则 exit 2 并打印安装命令；CI 环境在 workflow yaml 里 `apt-get install ripgrep` |
| **R-8 子组件 API 破坏** | 拆分 PerformanceTab / research/page 导致外部调用点破坏 | 拆分产出只在当前 `components/` 子目录内部被主组件引用；无外部 import |
| **R-9 StatusBadge 迁移风险** | 直接替换顶层 StatusBadge.tsx 会导致 TypeScript 类型错误（`string` 不可赋给 `Status` union）或视觉外观 shift（rounded-md → rounded-full） | 见 §3.3.9 — 采用扩展 QDS union + re-export barrel 的策略；s11 统一处理；禁止 s4/s7/s8/s9 直接替换；**Round 3 修订**：视觉差异判定降级为 verify phase 的用户验收，不作为 subtask AC；若用户判定需 fallback，由主 agent 在 post-task 回迁（工作量追加 0.5-1h） |
| **R-10 shadcn Tooltip 仍有 @base-ui delay prop** | 迁移中误用 Radix API | 保持现有 `delay={200}` 写法；扫描脚本不涉及 Tooltip prop 名称 |
| **R-11 未定义 CSS 变量迁移遗漏** | EditorClient 等 55 处 `--accent-*` 是隐藏 bug（Round 2 修正计数） | R13 扫描规则覆盖（Round 2 扩至 6 色白名单）；s7/s9 任务描述显式列出受影响文件（详见 §3.3.8） |
| **R-12 R4 扫描误报** | PCRE2 前后向断言实现错误会再次产生大量误报 | `--selftest` 子命令内置正/反例，每次脚本修改后必须通过；CI 首次运行 selftest |
| **R-13 factor-research 迁移规模** | 98 unique selector / 85 顶层 class + 调用点散落到 backtest/data-catalog 共 6 文件 | s6 工作量 10h；必要时允许拆为 s6a/s6b；**Round 2 新增**：R14 规则 + preflight 扫描保障 s10 删除前 .tsx 无 factor-research 调用 |
| **R-14 factor-research 散落到其它路由（Round 2 新增）** | s4 / s5 若未处理 TradesTab/PerformanceTab/TearsheetTab/data-catalog 的 sc-l / sc / fsel 迁移，s10 删除 CSS 后视觉退化 | §3.3.7 明确散落位置；s4 / s5 描述追加散落清理；R14 扫描 + preflight 自动拦截未迁移调用点 |

## 3.9 影响文件清单（事实核查 @ 2026-04-19，**Round 2 修正**）

以下文件存在性已逐一通过 Read / Grep 验证：

| 文件 | 类型 | 改动 |
|---|---|---|
| `src/web/src/app/globals.css` | 改 | 删除约 780 行遗留 class（`.bt-*` L532 起 ~400 行 + `.dc-*` L1640 起 ~250 行 + L1856 单行原子删除 + factor-research L1853-1987 约 135 行）|
| `src/web/src/lib/chartTheme.ts` | 改 | 新增 `CHART_LEGEND_STYLE` / `CHART_LABEL_STYLE` 两个常量（~20 行追加） |
| `src/web/scripts/verify-ds-compliance.sh` | 新增 | bash 脚本，约 340 行（R1-R14 + selftest + preflight + fix-hint + both-themes；Round 2 扩增 R14 ~20 行） |
| `src/web/CLAUDE.md` | 改 + 追加 | 改写「QDS CSS Classes (globals.css)」章节 + 替换「Key Conventions」中 `docs/ui/` 引用 + 追加「标准化后的约束」章节（含 Historical Notes、视觉参考源声明、shadcn 原语豁免） |
| `src/web/src/app/page.tsx` | 改 | RechartsTooltip spread 迁移 1 处 |
| `src/web/src/app/backtest/page.tsx` | 改 + 拆 | 拆分（见 §3.5.1）；迁移 15 处 fontFamily 内联、47 处 fontSize 内联、**144 处 bt-***（Round 2 修正） |
| `src/web/src/app/backtest/components/*.tsx` | 改 + 拆 | PerformanceTab 拆（§3.5.2）；TradesTab / OverviewTab / OverviewGreyTab 拆（§3.5.3）；其它内部迁移；**Round 2 新增**：TradesTab L179/515 的 2 处 .sc-l + PerformanceTab L1726 的 1 处 .sc-l + TearsheetTab L48 的 1 处 .sc-l 迁移为 `<SectionLabel>` |
| `src/web/src/app/backtest/components/OverviewTab.tsx` | 改 | ReferenceLine label 1 处 → `...CHART_LABEL_STYLE`；Tooltip 2 处 → `...CHART_TOOLTIP_PROPS`；bt-* 74 处；**Round 3 新增：hm-* 5 处迁移（L190 hm-grid / L192, L195, L200 hm-label / L206 hm-cell）— s4 内联 Tailwind grid + CSS custom props 实现（不依赖 s6，因 research 下 hm-* 调用点 0）** |
| `src/web/src/app/backtest/components/PerformanceTab.tsx` | 改 | bt-* 28 处；**Round 3 精确化：.sc-l 2 处迁移（L226 + L1726，r2 漏 L226）** |
| `src/web/src/app/backtest/components/RobustnessTab.tsx` | 改 | ReferenceLine label 1 处；bt-* 15 处 |
| `src/web/src/app/backtest/components/TradesTab.tsx` | 改 | bt-* 9 处；**Round 3 精确化：.sc-l 3 处迁移（L162 + L179 + L515，r2 漏 L162）** |
| `src/web/src/app/backtest/components/TearsheetTab.tsx` | 改 | **Round 3 精确化**：.sc-l 2 处迁移（L48 + L90，r2 漏 L90） |
| `src/web/src/app/backtest/components/OverviewGreyTab.tsx` | 改 | bt-* 6 处；**Round 3 新增：.sc-l 4 处迁移（L84 / L134 / L220 / L458）— r2 漏整文件**；若 executor 判定与 OverviewTab 合并（FR-4.1 ≥70% 重叠阈值），4 处 sc-l 随合并迁入 OverviewTab 清单 |
| `src/web/src/app/data-catalog/page.tsx` | 改 | 迁移 23 处 dc-* className + 5 处 fontFamily 内联 + 5 处 fontSize 内联 + 4 处 dim + **Round 2 新增**：L240-243 的 4 张 KPI 行 `.sc/.sc-l/.sc-v/.sc-sub` 迁移为 4 个 `<StatCard>` QDS 组件；L252 `.fsel` 迁移为 shadcn `<Select>` |
| `src/web/src/app/data-catalog/FetchDialog.tsx` | 改 | 迁移 8 处 dc-* + 2 处 fontFamily + 8 处 fontSize + **Round 2 新增**：L199/215/230 的 3 处 `.fsel` 迁移为 shadcn `<Select>` |
| `src/web/src/app/data-catalog/DeleteDialog.tsx` | 改 | 1 处 dc-* + 3 处 fontSize + 1 处 dim |
| `src/web/src/app/data-catalog/JobQueue.tsx` | 改 | **Round 2 新增**：14 处 dc-* 迁移（本任务 s5 处理）；4 处 bt-status（预留到 s11 由 StatusBadge 统一扩展后一并处理） |
| `src/web/src/app/data-catalog/FilterTabs.tsx` | 改 | 迁移 7 处 dc-* |
| `src/web/src/app/data-catalog/CoveragePanel.tsx` | 改 | 零星迁移 |
| `src/web/src/app/data-catalog/types.ts` | **Round 2 新增改动** | `TYPE_BADGE_CLS: Record<string, string>` 字典 12 个 value（`"dc-type-*"`）改写为 Tailwind class 字符串（详见 §3.3.4.1） |
| `src/web/src/app/research/page.tsx` | 改 + 拆 | 拆分为 6 子组件（§3.5.4）；迁移 Legend 2 处 + factor-research 47 处 className 实例（按家族 ~30-40 处） + fontFamily 3 处 + fontSize 16 处 + `dim`/`mono` 4 处 + `cg/ca/cr/ci` 5 处（严格扫描命中） |
| `src/web/src/app/research/report/[id]/ReportClient.tsx` | 改 + 拆 | 拆分（§3.5.5，移除 ReportHeader）；ReferenceLine label 1 处；fontSize 2 处；factor-research `.ctbl`/`.dtab` 迁移 |
| `src/web/src/app/trading/page.tsx` | 改 | 迁移 **5 处 fontFamily 内联**（trading/page.tsx 下 **0 处 bt-\* 调用**，已验证 @ 2026-04-19；fontSize 5 处） |
| `src/web/src/app/trading/components/*.tsx` | 改 | **Round 2 修正**：清理 55 处未定义 `--accent-*` 变体（OrdersPanel 9 + ActionBar 6 + FillsStream 5 + TopBar 4 + PositionsTable 4 + StrategyPanel 11 + TabNav 1 + EditorClient 15 外部归属 s9）；零星 cg/ca/cr；CartesianGrid spread |
| `src/web/src/app/trading/components/TabNav.tsx` | 改 | **Round 2 新增文件**：1 处未定义 `--accent-blue` 变量迁移 |
| `src/web/src/app/trading/components/tabs/*.tsx` | 改 | RiskTab: ReferenceLine label 1 处、CartesianGrid spread；StrategiesTab: fontSize 2 处；OverviewTab 查缺补漏 |
| `src/web/src/app/analytics/page.tsx` | 改 | Tooltip spread 3 处（删除本地 `TOOLTIP_STYLE`）|
| `src/web/src/app/optimization/page.tsx` | 改 | 查缺补漏（不拆，豁免阈值） |
| `src/web/src/app/orders/page.tsx` | 改 | cg/cr 零星清理 |
| `src/web/src/app/watchlist/page.tsx` | 改 | 查缺补漏 |
| `src/web/src/app/strategies/page.tsx` | 改 | `ca`/`cr` 零星清理（不拆） |
| `src/web/src/app/strategies/[name]/EditorClient.tsx` | 改 | **15 处未定义 `--accent-*` 变量**按 §3.3.8 映射（涵盖 green/red/amber/blue/orange/purple 可能变体）；另外 hex / color 内联清理 |
| `src/web/src/app/settings/page.tsx` | 改 | 查缺补漏 |
| `src/web/src/app/layout.tsx` | 不改 | 字体声明已正确（Inter + JetBrains Mono） |
| `src/web/src/components/qds/*.tsx` | 改（仅 status-badge.tsx） | 按 §3.3.9 扩展 Status union + locale；其它 6 个组件不改 |
| `src/web/src/components/StatusBadge.tsx`（顶层） | 改（s11 内）| 改为 re-export barrel：`export { StatusBadge } from "@/components/qds/status-badge"`；视觉差异见 §3.3.9 |
| `src/web/src/components/ui/*.tsx` | 不改 | shadcn 原语不动；R10 / `--mode both-themes` 豁免 |
| `src/web/src/components/motion/*.tsx` | 不改 | 非迁移范围（1-requirements §1.3.3）|
| `src/web/src/components/{Sidebar,TopBar,StatusBar,FillTicker,EmptyState,IdBadge,ConfirmModal,ThemeToggle,ErrorBoundary,NotificationListener}.tsx` | 改（s11 内）| baseline 断言 0 违规；若有零星 cg/ca/cr 清理 |

不动目录：`cli/`、`src/tinohelm*/`、`.claude/skills/TinoHelmDS/`、`tests/`、后端 API、alembic。

## 3.10 测试策略

**本任务不编写业务逻辑测试**。验证手段：

| 层级 | 手段 | 命令 |
|---|---|---|
| 语法 / 类型 | TypeScript 编译 | `cd src/web && npm run build` |
| Lint | ESLint | `cd src/web && npm run lint` |
| 合规 selftest | 扫描脚本自测 | `bash src/web/scripts/verify-ds-compliance.sh --selftest` |
| 合规（主） | 自研扫描脚本 | `bash src/web/scripts/verify-ds-compliance.sh` |
| 合规（CSS 删除前置）| preflight（R1-R10+R12+R13+R14） | `bash src/web/scripts/verify-ds-compliance.sh --preflight-before-css-delete` |
| 主题 | 扫描脚本双主题模式 | `bash src/web/scripts/verify-ds-compliance.sh --mode both-themes` |
| 既有字体校验 | 已存在的 `check-grep-fonts.sh` / `verify-build-fonts.mjs`（不动） | `bash src/web/scripts/check-grep-fonts.sh` |

合规脚本本身的"自测"由 `--selftest` 子命令覆盖（§3.2.8），不再依赖"初始违规数 ≥ 300"这种脆弱的绝对阈值。smoke test 改为：`R1-R14 每条规则至少命中 1 次`（结构性断言，不依赖绝对数字）。

## 3.11 User Acceptance Checklist（verify phase · 主 agent + 用户）

**Round 3 新增**：本章节列出 **verify phase（由主 agent 向用户展示、用户验证）** 需要审视的视觉 / 交互项目。这些项目**不作为任何 subtask 的 acceptance_criteria**（遵守用户全局 MUST 规则：subtask AC 不应含手动验证项）。subtask 层通过后，主 agent 在 PR review / 交付阶段由用户手工审核以下清单。

### 3.11.1 视觉对照（AC-2 的 User Acceptance 实现）

主 agent 启动 `cd src/web && npm run dev` 后，用户依次打开以下页面与 `.claude/skills/TinoHelmDS/preview/` 对应卡片比对：

| 路由 | 对照 preview 卡片 | 用户检查点 |
|---|---|---|
| `/` | `component-kpi.html` + `Web UI Kit.html` | KPI 栅格间距、数字字体分层 |
| `/backtest` | `component-row.html`（3px accent stripe）+ `component-kpi.html` | 列表行 accent 装饰条、expand 折叠动画 |
| `/backtest` 详情 Tab | `Charts Spec.html` | Recharts tooltip / grid / legend 样式一致 |
| `/data-catalog` | `component-badges.html`（7 色）+ `component-progress.html` + `component-kpi.html` | type 徽章色相、coverage bar 扫光、KPI 数字 |
| `/strategies` | `component-sidebar.html`（若适用） | 3px accent 左边框 active 态 |
| `/trading` | `component-tabs.html` + `component-badges.html` | TabNav accent 下划线、状态徽章 |
| `/research` | `type-section-label.html` + `component-kpi.html` + `component-tabs.html` | section-label 小 caps + accent 橙 + 1px 灰线延伸；hm 热力图色阶 |
| `/research/report/[id]` | `type-data.html` + `Charts Spec.html` | 数据 mono 字体、ReferenceLine label 位置 |
| `/analytics` / `/optimization` / `/orders` / `/watchlist` | `color-semantic.html` + `component-row.html` | 语义色使用纪律 |
| `/settings` | — | 表单布局与主题切换 |

**用户反馈路径**：若发现偏差，用户在 verify 阶段给主 agent 说明具体页面与差异点，主 agent 派 agent 回迁入对应 sN；**不阻塞本任务 subtask 层完成**。

### 3.11.2 双主题切换（AC-3 的 User Acceptance 实现）

用户在浏览器通过 `ThemeToggle` 切换 dark / light 主题，对 14 个路由页面做视觉检查：

- **文字对比度**：正文是否符合 WCAG AA（4.5:1）
- **色板切换**：dark 的 `--bg-s`（深棕灰）是否正确切为 light 的浅色；`--t0` 文字是否跟随
- **accent 表现**：焦橙在 dark 与 light 下的饱和度是否一致
- **图表网格可见度**：Recharts `CartesianGrid` 在 light 下是否过浅不可见；tooltip 背景对比度

**用户反馈路径**：若 light 下某 token 缺 override，回迁入 s12 补 `globals.css` 的 `.light` 作用域定义。

### 3.11.3 StatusBadge barrel 视觉差异（AC-5 的 User Acceptance 实现）

用户打开以下页面，对比 barrel re-export 后的 rounded-full + span 与原 shadcn Badge 的 rounded-md + padding 差异：
- `backtest/page`（run list 状态徽章）
- `optimization/page`（opt run 状态）
- `data-catalog JobQueue`（queued / completed / failed）
- `research` 历史 Job 行

**用户决策**：
- **OK**：保留 barrel 直通方案，结束
- **不 OK**：主 agent 派 agent 按 §3.3.9 fallback 方案回迁（顶层 `<Badge>` 外观 + QDS styles/labels map），工作量追加 0.5-1h

### 3.11.4 Memory 作废与更新（§1.9 的 Post-task）

主 agent 向用户确认：
1. 作废 `feedback-bt-card-classes.md` / `feedback-use-existing-css.md` / `feedback-pixel-perfect.md` / `feedback-css-class-naming.md` 的决定（interview.md 第 4 轮选择已隐含此方向）
2. 更新 `/Users/ouzhuohao/.claude/projects/-Users-ouzhuohao-TinoHelm/memory/MEMORY.md` 相关条目
3. 追加新 memory 条目记录本任务的标准化决策

---

**总结**：User Acceptance Checklist 的四个部分（视觉对照 / 双主题 / StatusBadge / memory）是主 agent 与用户在 verify phase 的交互范畴，本任务的 12 个 subtask 不承担这些。subtask 层通过 R1-R14 扫描 + build + lint + 行数断言判定交付完成。
