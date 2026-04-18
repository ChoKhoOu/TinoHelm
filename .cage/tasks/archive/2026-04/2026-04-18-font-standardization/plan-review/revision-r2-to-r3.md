# Revision R2 → R3 — 修订摘要

R2 双 APPROVE 已达成，但 Critic 留下 1 个 MAJOR（W3 并行组同文件写入竞争风险）+ Critic/Architect 多个 MINOR 未闭合。本次 R3 修订**仅做增量 patch**，不重新生成文档，不触动已 APPROVE 的核心设计（token 反指方案 / postcss AST 方案 / CSS 字面量扫描方案 / Source Serif 4 不加载方案）。

## 核心修订项

### MAJOR（Critic R2） — W3 并行组 `[t7a, t8, t9]` 同文件写入竞争

- **问题**：原 `task.json` parallel_groups W3 让 t7a / t8 / t9 三任务并行执行，三者均 read-modify-write 同一文件 `src/web/src/app/globals.css`（不同片段：类覆写删除 / body 块追加 feature-settings / @theme inline identity 转发）。Cage 执行器若真并发调度 3 个 agent，最后写入者会覆盖其余两个改动（即便改动位置不重叠，read 时各自基于同一初始快照 → write 时互相覆盖）。违反用户 MEMORY `feedback-parallel-agent-race.md` 的反模式警告。
- **修复方式**：**重排依赖链使其强制串行**（选方案 2，保留细粒度子任务验证），新链路：
  ```
  t7 → t7a → t8 → t9
  ```
  - `task.json`：
    - `t8.depends_on` 从 `["t7"]` 改为 `["t7a"]`
    - `t9.depends_on` 从 `["t7"]` 改为 `["t8"]`
    - `parallel_groups` 从 5 波次（含 W3 `[t7a, t8, t9]`）重算为 **7 波次**：W3=`[t7a]`、W4=`[t8]`、W5=`[t9]`、W6=`[t5]`、W7=`[t17]`。**同文件写操作位于不同波次，且每个 globals.css 写波次仅 1 个任务**。
  - `4-tasks.md`：
    - DAG 概览改为串行链，并新增"为何强制 t7a → t8 → t9 串行"解释段落引用 `feedback-parallel-agent-race.md`；
    - t8 `depends_on` 改为 `["t7a"]`、t9 `depends_on` 改为 `["t8"]`；
    - t7a/t8/t9 改动描述移除硬行号（仅提"原始行号"作参考），改为按 selector/block 内容定位，避免前面子任务改动后行号 drift；
    - parallel_groups 表更新为 W1-W7 共 7 波次，并补"关键串行路径 t6 → t7 → t7a → t8 → t9 → t5 → t17（7 层深度）"。
  - `review.round` 自增为 3，`architect_verdict` / `critic_verdict` 重置为 `pending`。
- **代价**：关键路径从 5 层延长至 7 层（多 2 个同步等待点）。三任务改动相互独立，串行顺序不影响正确性。

### MINOR-1（Critic R2 + Architect R2）— 数字口径 97 / 44 / 8 统一

- **问题**：R2 文档中多处仍用 R1 原数字 `96 处 var(--font-d/u)` / `45 个 .tsx` / `9 个 font-heading 消费方`；实测为 `97 / 44 / 8`。
- **实测命令**：
  ```bash
  rg -o 'var\(--font-[du]\)' src/web/src/app/globals.css | wc -l  # 97
  rg -l -e 'font-sans' -e 'font-mono' -e 'font-heading' src/web/src -g '*.tsx' | wc -l  # 44
  rg -l '\bfont-heading\b' src/web/src -g '*.tsx' | wc -l  # 8
  ```
- **修复**：`1-requirements.md` / `2-research.md` / `3-tech-design.md` / `4-tasks.md` 全局批量 `96→97` / `45→44` / `9→8`（仅限 font-heading 消费方上下文；`.font-heading` 定义 1 处 + 消费 8 处 = 总 9 处，但消费方数为 8）。
- **requirements.md FR-4** 的消费方枚举已列全 8 个文件名：analytics、optimization、orders、watchlist、strategies/[name]/EditorClient、ui/dialog、ui/popover、ui/sheet。

### MINOR-3（Critic R2）/ MINOR-2（Architect R2）— parse-css.ts TS 返回类型窄化

- **问题**：
  - `getBodyRule` 返回类型声明为 `{ decls; raw }`，实际返回含 `__hasApply` 字段并用 `as any` 绕过类型检查（Critic）
  - 同时 `let target: Rule | null = null; root.walkRules(cb => { target = rule })` 在 strict TS 下无法跨闭包推断，`target.walkDecls` 会报 TS2532（Architect）
- **修复**（`3-tech-design.md §7.1`）：
  - 新增导出 `interface BodyRuleSnapshot { decls: Map<string, string>; raw: string; hasApply: boolean }`
  - `getBodyRule` 返回类型改为 `BodyRuleSnapshot`，字段从 `__hasApply`（magic property + as any）改为 `hasApply`（一等公民字段）
  - `let target: Rule | null` 模式重构：callback 内赋值给 `found`，外部 `if (found === null) throw`，随后 `const target: Rule = found` 做显式非空窄化，消除 strict 下 TS2532
  - 同步更新 `§7.2 tokens.test.ts` 的 sanity 断言从 `expect((body as any).__hasApply).toBe(true)` 改为 `expect(body.hasApply).toBe(true)`
  - **t4 验收命令**（`4-tasks.md`）追加 `--strict` 标志，确保 strict 模式下 `npx tsc --noEmit` 能通过（事前捕获类型错误）

### MINOR-3（Architect R2）— t8 grep 正则 `cv11.*ss01.*ss03` 单行顺序过严

- **问题**：`4-tasks.md t8` 原验收 `rg -q "font-feature-settings.*cv11.*ss01.*ss03"` 要求三风格集严格按 `cv11 → ss01 → ss03` 顺序出现；若 executor 按字母序写成 `'ss01', 'cv11', 'ss03'` 会漏过，变成假失败。
- **修复**（`4-tasks.md t8` 验收段）：拆为三条独立 `rg -q` 断言，仅检查存在性：
  ```bash
  rg -q "font-feature-settings[^;]*cv11" src/web/src/app/globals.css
  rg -q "font-feature-settings[^;]*ss01" src/web/src/app/globals.css
  rg -q "font-feature-settings[^;]*ss03" src/web/src/app/globals.css
  ```
  顺序由 tech-design §模块 3 的模板（`'cv11', 'ss01', 'ss03'`）规范；t5 vitest 的 A1.7~A1.9 也单独覆盖，无重复过严。

### MINOR（未修 — 观察性/非本任务缺陷）

- **Critic MINOR-4**（Next.js `postcss-next-font.js` 注释与代码行为不一致）：原评审明确标注"本任务影响：无"，为对 Next.js 文档的记录性观察，不改。
- **Architect MINOR-4**（`hasApply` magic property 可读性）：已经合并入 MINOR-3 修复（`hasApply` 作为一等公民字段，不再 magic）。
- **Architect MINOR-5**（task.json 未引用 `revision-*.md`）：Cage 工具 schema 未定义 `revision_summary_path` 字段，不改。

## 文档变更汇总

| 文件 | R2 → R3 变更 |
|---|---|
| `task.json` | `t8.depends_on: ["t7"] → ["t7a"]`；`t9.depends_on: ["t7"] → ["t8"]`；`parallel_groups` 5 波 → 7 波；`review.round: 2 → 3`；verdicts 重置 pending |
| `4-tasks.md` | DAG 概览改串行链；t7a/t8/t9 description 去硬行号；t8 验收拆三条独立 rg；t4 验收追加 `--strict`；parallel_groups 表重算 7 波次；新增"为何强制串行"说明段 |
| `3-tech-design.md` | `§7.1` `getBodyRule` 重构为 strict-TS 安全 + 导出 `BodyRuleSnapshot` 接口；`§7.2` sanity 断言改 `body.hasApply`；数字 97/44/8 批量替换 |
| `1-requirements.md` | FR-4 消费方枚举补全 8 个文件名；数字 97/44/8 批量替换 |
| `2-research.md` | 数字 97/44/8 批量替换 |

## 不变项（R2 APPROVE 核心设计保留）

- token 反指方案（QDS 短名 `:root` + `@theme inline` 两层）
- postcss AST 替代正则的 `parse-css.ts` 方案
- `verify-build-fonts.mjs` 扫 `.next/static/css/*.css` 的 `@font-face` 字面量方案
- Source Serif 4 "明确不加载 + 反向断言" 策略
- t10/t11 `count=$() || echo 0` 显式计数避免 xargs 静默 PASS
- `check-grep-fonts.sh` 用 `git rev-parse --show-toplevel` + `command -v rg` 前置
- AC 全自动化（无任何"手动/目测"字样）
- 所有 FR / AC / NFR 文字内容

## 预判 R3 审查关注点

1. **Architect**：DAG 重算是否一致 — `task.json` subtasks `depends_on` 与 `4-tasks.md` 是否 100% 同步；W3-W5 每波仅 1 个任务是否符合 parallel_groups 最优约束（此处"单元素波次"是故意为之，主动避让同文件竞争，非分组失误）。
2. **Critic**：`feedback-parallel-agent-race.md` 的防御是否过度 — 可能质疑 "即便 Cage 文件级加锁也应保守串行"；答：串行代价仅 2 个同步点，但消除整类 race condition，属合理 tradeoff。
3. **Critic**：t4 验收加 `--strict` 是否能真正暴露 `let target: Rule | null` 的 TS2532 — 已在 §7.1 重构为 `const target: Rule = found` 模式确保通过。
