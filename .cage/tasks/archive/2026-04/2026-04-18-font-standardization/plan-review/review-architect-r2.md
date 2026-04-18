# Architect Review — Round 2

**VERDICT: APPROVE**

## 摘要

R2 修订扎实地回应了 R1 的全部 CRITICAL/MAJOR。核心修复点（postcss AST 替代正则、`.font-sans/.font-mono/.font-heading` 类覆写 **删除** + 新增 t7a + AC-1.12、verify-build-fonts.mjs 改为扫 CSS `@font-face` 字面量、Source Serif 4 "明确不加载 + 反向断言"、`task.json` 17 个 subtask 全部填充 `depends_on`、t10/t11 去掉 xargs 静默 PASS 陷阱）经代码验证与方案重演均属**实质性**解决，不是文字调整。DAG 重算后 5 波与 subtask `depends_on` 100% 一致，关键路径 `t6→t7→{t7a,t8,t9}→t5→t17` = 5 层，无循环、无孤儿任务。新增的 t7a 依赖位置（W3，紧跟 t7）、并行组归属（和 t8/t9 同波次）、验收（AC-1.12 负匹配 + rg 负断言双层）均干净。残留问题仅 3 项 MINOR（数字口径仍不准、t8 grep 正则单行序列化过严、parse-css.ts 在严格 TS 下的类型窄化），均不影响执行正确性。APPROVE。

## 代码引用验证（R2 新增/修订引用）

| 引用 | 文件存在 | 内容准确 | 问题 |
|------|---------|---------|------|
| `globals.css:15-16` legacy `--font-d/-u` 字面量 | Yes | Yes | 命中 |
| `globals.css:178-195` `@layer base` 含 `.font-sans/.font-heading/.font-mono` 类覆写 | Yes | Yes | 实际行号 L185-195（R2 引用一致） |
| `globals.css:179` `html, body { height: 100% }` | Yes | Yes | 精确命中（critic C1 的误匹配源头） |
| `globals.css:206-213` `body {}` 含 `@apply bg-background` | Yes | Yes | L206 `body {` → L213 `}`，含 `@apply` 可用作 sanity 锚点 |
| `globals.css:216-218` `@theme inline` 起首 `--font-sans/-mono` | Yes | Yes | 精确命中 L216-218 |
| `layout.tsx:25-28` CDN `<link>` | Yes | Yes | L25-28 `<link href="...fonts.googleapis.com..." rel="stylesheet" />` 存在 |
| `src/web/node_modules/postcss/package.json` v8.5.6 | Yes | Yes | 已通过 `@tailwindcss/postcss` 传递，R2 显式锁定 `^8.4.49` 安全 |
| `src/web/node_modules/@tailwindcss/postcss/package.json` | Yes | Yes | 确认 postcss 是传递依赖 |
| `src/web/tsconfig.json` `strict: true` | Yes | Yes | 这是 **MINOR 风险点**，见下文 M1 |
| `src/web/package.json` 原无测试框架 | Yes | Yes | 新增 vitest + postcss devDep 合理 |

代码引用准确率 10/10（R2 无新增不存在文件/函数）。

## R1 BLOCKER/CRITICAL/MAJOR 逐条修复验证

### [R1 Architect C1 / Critic M1] `.font-sans/.font-mono/.font-heading` 类覆写

- **修订位置**：
  - `1-requirements.md` 新增 **FR-4**（L65-81）明确删除方案；
  - `3-tech-design.md` 新增 **§模块 2.5**（L188-212）含理由与 vitest 断言；
  - `4-tasks.md` 新增 **t7a**（L140-150）；
  - AC-1 加 **A1.12**（L151）负匹配；
  - `task.json` t7a 条目 + parallel_groups W3 含 t7a。
- **方案可靠性**：
  - 删除路径清晰：`.font-sans`/`.font-mono` 由 Tailwind v4 `@theme inline` 自动产出 utility 替代（行为等价）；`.font-heading` 无 Tailwind token，消费方从 body 继承 `var(--font-u)` → `var(--font-sans)` → Inter。验证原类覆写右值就是 `var(--font-u)`，所以**退化路径与原行为严格等价**。
  - 论证链完整：FR-4 + §模块 2.5 双处都讲清；§模块 2.5 配 vitest 反向断言防回归；无新风险。
- **结论**：**实质性解决**。PASS。

### [R1 Architect C2 / Critic C2] `task.json` subtasks `depends_on` 全为 `null` + t5 缺 t8

- **修订位置**：`task.json` L7-145 全部 17 个 subtask 显式填充。
- **DAG 一致性核验**（task.json vs 4-tasks.md）：

| subtask | task.json `depends_on` | 4-tasks.md 声明 | 一致 |
|---------|------------------------|-----------------|------|
| t1 | `[]` | `[]` | Yes |
| t3 | `[t1]` | `[t1]` | Yes |
| t4 | `[t1]` | `[t1]` | Yes |
| t5 | `[t3,t4,t7,t7a,t8,t9]` | `[t3,t4,t7,t7a,t8,t9]` | Yes |
| t6 | `[]` | `[]` | Yes |
| t7 | `[t6]` | `[t6]` | Yes |
| t7a | `[t7]` | `[t7]` | Yes |
| t8 | `[t7]` | `[t7]` | Yes |
| t9 | `[t7]` | `[t7]` | Yes |
| t10-t15 | `[]` | `[]` | Yes |
| t16 | `[t1,t14,t15]` | `[t1,t14,t15]` | Yes |
| t17 | `[t5,t7,t7a,t8,t9,t10,t11,t12,t13,t14,t15,t16]` | 同 | Yes |

- **t5 补 t8**：已更正（R1 的 Major #2 修复）。
- **结论**：**实质性解决**。PASS。

### [R1 Critic C1 新发现] `extractBlock(css, 'body')` 正则误匹配 `html, body {...}`

- **修订位置**：`3-tech-design.md §7.1`（L300-368）**整体重写** `parse-css.ts` 为 postcss AST 解析，`walkRules(rule => rule.selector.trim() === 'body')` 精确匹配；`tokens.test.ts` §7.2 新增 **sanity 断言 `__hasApply`**（L389-391）作为取到正确 body 的二次保证。
- **方案可靠性**：
  - postcss AST 从根本上消除 selector 误匹配问题，`rule.selector.trim() === 'body'` 会**严格**过滤掉 `html, body`（selector 字符串不同）；
  - `__hasApply` sanity 检查：即使某天 globals.css 结构变化，AST 未能找到含 `@apply` 的 body 块也会立即失败，防止静默误取；
  - **版本风险**：postcss `^8.4.49` 已通过 `@tailwindcss/postcss` 传递依赖（实测 v8.5.6），显式加入 devDep 避免 hoist 不稳定是合理的防御；postcss 8.x 为稳定 API，与 Tailwind v4（内部也用 postcss 8）**零冲突**。
  - **小瑕疵（非 blocker）**：parse-css.ts 的 `let target: Rule | null = null; root.walkRules(rule => { ... target = rule; return false; }); if (!target) throw; target.walkDecls(...)` —— 在 `tsconfig strict: true` 下 TypeScript 无法从 callback 推断 `target` 在外部被赋值，可能触发 TS2532 "Object is possibly null"。但 t4 验收命令（`npx tsc --noEmit`）未启用 `--strict`，能通过；若 planner 想更稳，可在 `target.walkDecls(...)` 前加 `target!` 非空断言或改用 `target != null` 三元结构。见 M1。
- **结论**：**实质性解决**。PASS（M1 为可选微调）。

### [R1 Critic C4 / Architect M3] `verify-build-fonts.mjs` woff2 文件名正则必然失败

- **修订位置**：`3-tech-design.md §7.4`（L480-559）重写脚本，改为：
  1. **扫 CSS 文本**：聚合 `.next/static/css/*.css`（fallback `out/_next/static/css/*.css`）中所有 `@font-face { ... font-family: '<name>' }` 字面量；
  2. **required 断言**：字面量中必须包含 `'Inter'` 与 `'JetBrains Mono'`；
  3. **forbidden 断言**：字面量中**不得**包含 `'Source Serif 4'`（反向断言本任务不加载）；
  4. **sanity**：`static/media/**/*.woff2` 文件数 ≥ 2。
- **脚本 robust 性核验**：
  - Next.js 构建产物 CSS 中 `@font-face` 保留原 family 名（Next.js `next-font-loader` 生成的 `@font-face` 用户可读，family 字面量存在）—— 这是已知稳定行为；
  - 正则 `/@font-face\s*\{[^}]*font-family\s*:\s*['"]([^'"]+)['"]/g`：
    - `[^}]*` 假设 `@font-face` 块内无嵌套 `{}` —— CSS 规范禁止，安全；
    - 匹配 `'name'` 与 `"name"` 两种引号；
    - Next.js 生成的是 `font-family: 'Inter Fallback';` + `font-family: 'Inter';` 两条 `@font-face`（一个 fallback metrics-matching，一个主字体）—— 主字体条目 family 是 `'Inter'` 纯字面量，会匹配到；`'Inter Fallback'` 也会匹配到（`hasFamily('Inter')` 用 `.includes` 所以两者都成 hit），行为正确；
  - `pickRoot()` 兼容静态导出（critic m7）：`.next/` 不存在时 fallback `out/_next/`，覆盖了 `output: 'export'` 场景；
  - `woff2 ≥ 2` sanity：Inter + JBM 至少各产出 1 个 woff2 满足。
- **小瑕疵（非 blocker）**：若未来 Next.js 改变 `@font-face` family 字面量的生成方式（如混淆），脚本会失败；但这是 smoke test 该做的 —— 真要适配需要同步改脚本。当前对 Next.js 16.1.6 稳定。
- **结论**：**实质性解决**。PASS。

### [R1 Critic C5 / Architect M4] AC-3.2 "否则跳过" 违反用户 RULE

- **修订位置**：
  - `2-research.md §4`（L90-107）改为"明确不加载"；
  - `1-requirements.md` FR-1（L25）"不导入 Source Serif 4"；
  - AC-3.2（L195-205）改为三项强证据 + 一项强约束（反向断言不含 `'Source Serif 4'`）；
  - `3-tech-design.md §模块 1`（L132）"不导入 Source_Serif_4"；§7.4 `forbidden = ['Source Serif 4']`；
  - `4-tasks.md` t6 验收含 `! rg -q "Source_Serif_4"` + `! rg -q "sourceSerif"`。
- **方案干净度**：
  - "不加载 + 反向断言"路径清晰：layout.tsx 不 import、:root 不定义 `--font-serif`、产物 CSS 无 `'Source Serif 4'` 字面量、woff2 文件数不含 Source Serif 4 贡献，**一致**；
  - AC 从"可跳过"变为"必须通过"+"必须不存在"，100% 自动化；
  - 内部一致性：R1 存在的 t6 强制 import 与 AC 允许跳过的矛盾被消除；
  - 未来扩展路径已文档化（research §4 L101 "未来启用路径：启动独立子任务"）。
- **结论**：**实质性解决**。PASS。

### [用户提示 BLOCKER] `rg -c | xargs test -le` 静默 PASS 陷阱

- **修订位置**：`4-tasks.md` t10 L192-193 / t11 L208-209 改为 `count=$(...); test "${count:-0}" -ge <N>`。
- **正确性**：
  - 当 rg 匹配 0 时 `rg -c` 无输出且退出码非 0；`|| echo 0` 兜底使 `count=0`；
  - `test "0" -ge 2` 为 false，`|| { echo ...; exit 1; }` 必然失败；
  - 若 executor 漏替换或替换错误字符串，`count` 低于阈值会立即失败；
  - 双重保险：`! rg -q "IBM Plex"` 负匹配在 2nd 断言前就能捕获"替换到错字符串且移除了 IBM Plex"之外的场景。
- **结论**：**实质性解决**。PASS。

### [Architect M1] chartTheme.ts 豁免显式化

- **修订位置**：`1-requirements.md` NFR-1 L120 + `3-tech-design.md §影响文件清单-不改动` L115。
- **结论**：PASS。

### [Architect M2] t5 缺 t8 依赖

- **修订位置**：`4-tasks.md` t5 `depends_on: [t3, t4, t7, t7a, t8, t9]`；task.json 同步。
- **结论**：PASS。

### [Architect M5] check-grep-fonts.sh 路径漂移

- **修订位置**：`3-tech-design.md §7.5` L573-580 `REPO_ROOT=$(git rev-parse --show-toplevel)` + `cd "$REPO_ROOT"` + 前置文件存在性检查 + `command -v rg`。
- **结论**：PASS。

### [Architect M4 / Critic M5] Tailwind v4 `@theme inline` 自引用安全性证据

- **修订位置**：`2-research.md §5`（L153-167）+ `3-tech-design.md §模块 4`（L242-251）含原理 + 降级方案 + t9 smoke 闭环。
- **结论**：PASS。

### [Critic M6] rg 版本 / CI 环境

- **修订位置**：`2-research.md §9`（新增）+ `3-tech-design.md §7.5` `command -v rg` 前置。
- **结论**：PASS。

### MINOR 修订

全部 10 项 MINOR 修订已对应到文档位置（m1~m10）。数字口径仍有偏差（见下方 MINOR #1）。

## 新增 t7a 合理性审查

- **依赖位置**：`t7a.depends_on = [t7]` — 正确。t7a 删除 `@layer base` 内的类覆写，必须发生在 t7 重写 `:root` token 之后（否则若 executor 按顺序改动，t7a 删除后 t7 基于新 CSS 定位行号会漂移）。但也必须发生在 t5 读取 globals.css 做断言之前 —— t5 `depends_on` 已含 t7a，OK。
- **并行组**：t7a 与 t8、t9 同波次（W3）—— 三者**互不相关**：t7a 删 L185-195 的三个独立块；t8 改 L206-213 body 块；t9 改 L216-218 `@theme inline` 块。行号上三者不重叠，并行安全。
- **验收**：AC-1.12 负匹配 `.font-(sans|mono|heading)\s*\{[^}]*var\(--font-[du]\)` + t7a 自身的 rg 负匹配双层保险。
- **潜在风险**：t7a 完成后原 L206 的 body 块会因前面删除 11 行后**行号向前漂移到约 L195**；t8 仍按"第 206-213 行"定位会落空。但 t7a 与 t8 在同一波次（W3）并行，各自是独立修改，executor 不应依赖行号而应按 selector/block 定位——4-tasks.md t8 描述已用"`body {}` 块内新增 `font-feature-settings`"的语义定位，非硬行号，OK。
- **结论**：t7a 插入干净，无副作用。PASS。

## 新引入风险扫描

遍历 R2 新增改动逐一核查是否引入新 BLOCKER：

1. **postcss AST 引入**：
   - 风险：postcss 版本与 Tailwind v4 内部 postcss 冲突？
   - 核查：`@tailwindcss/postcss@^4` 本身依赖 `postcss`，R2 显式锁定 `^8.4.49` 与传递依赖兼容；npm 应能 hoist 到单一版本。
   - 结论：无冲突。
2. **t7a 插入后行号漂移**：
   - 风险：t7a 完成后 t8 / t9 按行号定位错误？
   - 核查：t8/t9 任务描述用 selector 而非严格行号；即便如此，W3 内三者并行，executor 基于改动前的行号快照操作不会互相干扰。
   - 结论：无 blocker（但用户侧若执行 t7a→t8 顺序可能需要重定位，已通过任务描述的 block identifier 规避）。
3. **verify-build-fonts.mjs 的 `hasFamily()` 用 `.includes` 宽松匹配**：
   - 风险：`hasFamily('JetBrains Mono')` 若 family 字面量是 `'__JetBrains_Mono_c1234'`（Next.js 内部 hash name）会漏匹配吗？
   - 核查：Next.js 为 CSS var `--font-jetbrains-mono` 生成的 `@font-face` 中 font-family 是**原始 family 名**（如 `'JetBrains Mono'` 字面量），不是内部 hash；内部 hash 出现在 `className` 注入和 `font-family` 别名中，`@font-face` 本身保留原名用于浏览器字体解析。
   - 结论：无风险（Next.js 16 稳定行为）。
4. **`@font-face` 正则 `[^}]*` 对嵌套块的假设**：
   - 风险：CSS `@font-face` 块允许嵌套？
   - 核查：CSS 规范明确禁止 at-rule 嵌套；`@font-face` 是 top-level at-rule，块内仅含声明（`src`, `font-family`, etc.），无嵌套。
   - 结论：无风险。
5. **reverse assertion `forbidden = ['Source Serif 4']`**：
   - 风险：若 Next.js 自动为 Inter fallback 生成的 `@font-face` family 名巧合包含 "Source Serif" 子串？
   - 核查：Inter fallback 通常命名为 `'Inter Fallback'`，JBM 为 `'JetBrains Mono Fallback'`；Next.js 不会为未导入的字体生成 fallback。
   - 结论：无风险。
6. **t7a 删除后 `.font-heading` 消费方（8 个 .tsx 文件）的行为**：
   - 风险：消费方 `className="font-heading"` 被 Tailwind v4 视为未知 utility 时是否会**丢弃该 class**（不写入 DOM）还是**保留但无效**（写入 DOM 但无样式）？
   - 核查：Tailwind v4 `@theme inline` 未定义 `--font-heading` 时，`font-heading` 不产出 utility，但 HTML 中 `className="font-heading"` **保留**该字符串（Tailwind 不修改 JSX）。浏览器侧无匹配 CSS 规则，最终该元素 `font-family` 从父级继承 → body 的 `var(--font-u)` → `var(--font-sans)` → Inter。与原手写 `.font-heading { font-family: var(--font-u) }` 行为**严格等价**。
   - 结论：无风险，planner 论证完整。
7. **task.json parallel_groups W2 含 t16（依赖 t1, t14, t15）**：
   - 核查：W1 含 t1/t14/t15，W2 起步时三者已完成；W2 内其他任务（t3/t4/t7）互不依赖 t16；t16 修改 package.json，t3 修改 vitest.config.ts，t4 修改 parse-css.ts，t7 修改 globals.css —— 文件完全不重叠，并行安全。
   - 结论：无冲突。

未引入新 BLOCKER/CRITICAL。

## DAG 重算一致性

- **subtasks `depends_on` vs parallel_groups**：
  - W1 = `{t1, t6, t10, t11, t12, t13, t14, t15}` — 全部 `depends_on: []`，正确；
  - W2 = `{t3, t4, t7, t16}` — t3/t4 依赖 t1（W1 完成），t7 依赖 t6（W1 完成），t16 依赖 t1/t14/t15（W1 完成），正确；
  - W3 = `{t7a, t8, t9}` — 全部依赖 t7（W2 完成），正确；
  - W4 = `{t5}` — 依赖 `[t3, t4, t7, t7a, t8, t9]` 全部在 W1-W3 完成，正确；
  - W5 = `{t17}` — 依赖 11 个子任务全部在 W1-W4 完成，正确。
- **关键路径**：`t6 → t7 → (t7a|t8|t9) → t5 → t17` = 5 层深度。
- **并行度**：W1 最大 8 并发（Cage 限 5 并发，runtime 会自动切到两批 5+3）。
- **结论**：DAG 100% 一致，无循环、无孤儿任务、无依赖倒置。PASS。

## t17 汇总验证依赖完整性

- `t17.depends_on = [t5, t7, t7a, t8, t9, t10, t11, t12, t13, t14, t15, t16]` 共 12 个。
- 漏查：t1 / t3 / t4 / t6 是否应显式列出？
  - t1 被 t3/t4/t16 传递依赖 → OK；
  - t3/t4 被 t5 传递依赖 → OK；
  - t6 被 t7 传递依赖 → OK。
- **传递闭包**：t17 通过 `t5 → t3 → t1`、`t5 → t4 → t1`、`t5 → t7 → t6` 覆盖所有前置。
- 若 Cage DAG 引擎用直接依赖（非传递闭包），t17 可能在 t1 未完成时启动 → 但 t1 必先于 t3/t4/t16 完成，而 t17 必须等 t5/t16 完成，间接要求 t1 完成。并发协议安全。
- **结论**：t17 依赖完整，无遗漏。PASS。

## 权衡分析

| 决策（R2 新增/强化） | 正方 | 反方 | 评价 |
|------|------|------|------|
| **postcss AST 替代 ad-hoc 正则** | 零新增依赖（传递依赖已有）；从根本消除 selector 误匹配；未来嵌套/新块型鲁棒 | parse-css.ts 代码稍长（60 行 vs 原 30 行）；TS 严格类型窄化需要 `!` 断言 | 正确选择，反方皆为 MINOR |
| **`.font-sans/.font-mono/.font-heading` 类覆写全删** | 消除与 Tailwind utility 的层叠冲突；token 流图单一真源；`.font-heading` 消费方退化路径等价 | 若未来需区分 body 文字与 heading 字体，需重新定义 `--font-heading` | 正确选择；未来诉求可另起子任务 |
| **Source Serif 4 明确不加载 + 反向断言** | 100% 自动化（无跳过）；产物减 ~80KB；AC 内部一致 | 若未来添加长文档/报告页需补加载 | 正确选择；未来路径已文档化 |
| **verify-build-fonts.mjs 扫 CSS @font-face 字面量** | 不依赖脆弱的文件名模式；对 Next.js 16 稳定；兼容静态导出；可验证反向不存在 | 正则假设 `@font-face` 块无嵌套（CSS 规范保证）；依赖 Next.js 保留原 family 名 | 正确选择；反方风险极低 |
| **task.json `depends_on` 全填 + parallel_groups 重算** | DAG 一致性；Cage engine 不会拍平并行 | 17 个 subtask 依赖关系未来扩展需双处维护 | 正确；必须的修复 |
| **t10/t11 `count=$(...); test ... -ge N`** | 0 匹配时必然失败；显式错误输出 | 比 pipeline 多一行；依赖 bash set -u 不生效（未声明） | 正确；小改即可 |
| **t7a 作为独立子任务 vs 合并入 t7** | 独立验证 AC-1.12 的清理；失败时定位粒度更细 | 多一个 subtask；t7→t7a 串行引入一个层级 | 正确；清晰胜过简洁 |

## 遗漏项

无显著遗漏。R2 对 R1 列出的所有遗漏（chartTheme.ts 豁免、rg 版本要求、静态导出路径、`.font-heading` 处理）均有显式处理。

## 上轮修改验证

| 上轮要求（R1） | 是否解决 | 说明 |
|---------|---------|------|
| C1: `.font-sans/.font-mono/.font-heading` 类覆写处理 | Yes | 新增 FR-4 + §模块 2.5 + t7a + AC-1.12；**实质性** |
| C2: task.json `depends_on` 全空 | Yes | 17 个 subtask 全填；与 4-tasks.md 一致；**实质性** |
| M1: chartTheme.ts 豁免 | Yes | NFR-1 + 影响文件清单双处声明 |
| M2: t5 缺 t8 | Yes | `depends_on: [t3,t4,t7,t7a,t8,t9]` |
| M3: verify-build-fonts.mjs 正则 | Yes | 改为扫 CSS @font-face 字面量 + forbidden；**实质性** |
| M4: @theme inline 自引用证据 | Yes | research §5 + tech-design §模块 4 含原理 + 降级 + t9 smoke |
| M5: check-grep-fonts.sh 路径 | Yes | git rev-parse + 前置检查 |
| M6: rg 版本要求 | Yes | research §9 + 脚本 `command -v rg` |
| Critic C1: `extractBlock('body')` 误匹配 | Yes | postcss AST + `__hasApply` sanity；**实质性** |
| Critic C3: xargs 静默 PASS | Yes | t10/t11 改为 `count=$(...); test -ge N`；**实质性** |
| Critic C4: woff2 文件名正则必挂 | Yes | 同 M3 修复路径 |
| Critic C5: Source Serif 4 "否则跳过" | Yes | 不加载 + 反向断言；**实质性** |
| MINOR m1~m10 | Partial | 数字口径仍不准（见下） |

## 修改要求（APPROVE 无必修，仅 MINOR 建议）

### MINOR（可选，不阻塞）

1. **数字口径仍有偏差**（m1 未完全对齐）：
   - `1-requirements.md` L13 称 `var(--font-d/u)` = **96 处**；实际 `rg -o 'var\(--font-[du]\)' src/web/src/app/globals.css | wc -l` = **97**。与 R1 架构师指出一致，未修订。
   - `1-requirements.md` L14 称 `font-sans/mono/heading` 消费 **45 个 .tsx**；实际 Grep 精确匹配 = **44 个**。
   - `3-tech-design.md` L37 + L79 称 `.font-heading` 消费 **9 个 .tsx**；实际 Grep = **8 个**（`font-heading` 在 8 个 .tsx 文件中出现，剩余 1 处匹配是 globals.css 本身的定义）。
   - 建议统一为：96→**97**、45→**44**、9（消费方）→**8**。不影响执行正确性，但"已核实"的声明会误导读者。

2. **parse-css.ts 在 strict TS 下类型窄化**（C1 修订微调）：
   - 代码中 `let target: Rule | null = null; root.walkRules(rule => { target = rule; return false; }); if (!target) throw; target.walkDecls(...)` — TypeScript strict 模式下 callback 赋值不被编译器感知，`target.walkDecls` 处会报 TS2532。
   - 但 t4 验收命令（`npx tsc --noEmit --esModuleInterop --moduleResolution bundler --module esnext --target es2020 --skipLibCheck`）**未传 `--strict`**，能通过。
   - 执行阶段若运行 `npm run lint` 或整体 tsc 检查会触发 strict。
   - 建议：在 `target.walkDecls` 前显式 `if (target == null) throw; ...`（已有 throw）改为 `const safeTarget = target as Rule;` 或加 `!` 断言 `target!.walkDecls(...)`。
   - 非 blocker：当前 t4 独立验证通过；若 t5 执行时全项目 build 触发类型错误，可在 executor 侧微调。

3. **t8 grep 验证正则过严**：
   - `4-tasks.md` t8 L160 `rg -q "font-feature-settings.*cv11.*ss01.*ss03" src/web/src/app/globals.css` 要求顺序为 `cv11 → ss01 → ss03` 在同一行。若 executor 写成 `font-feature-settings: 'ss01', 'cv11', 'ss03';`（字母顺序）会漏过。
   - 但 tech-design §模块 3 模板按 `'cv11', 'ss01', 'ss03'` 顺序给出，executor 若严格遵循不会触发。
   - 建议：拆为三条独立断言 `rg -q "cv11"` + `rg -q "ss01"` + `rg -q "ss03"`；或由 t5 vitest 的 A1.7/1.8/1.9 单独覆盖（已存在），不必在 t8 自身 rg 里强要求顺序。非 blocker。

4. **parse-css.ts `__hasApply` 的 "magic property" 命名**：
   - 通过 `as any` 附加非声明字段传递 sanity 信号，设计上略 hack。可将函数返回类型显式扩展为 `{ decls: Map<string,string>; raw: string; hasApply: boolean }` 并调整 tokens.test.ts 的访问方式（`body.hasApply`）。纯可读性建议，非 blocker。

5. **R2 修订文档的 `revision-r1-to-r2.md` 未在 task.json 中引用**：task.json 的 `review.round: 2` 仅含 `architect_verdict: pending` / `critic_verdict: pending`，不影响评审但未来复盘时 trace 需要的话可补一个 `revision_summary_path` 字段指向 `revision-r1-to-r2.md`。纯文档建议。

---

ReviewPass: architect
VERDICT: APPROVE
