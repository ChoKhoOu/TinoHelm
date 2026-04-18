# Architect Review — Round 1

**VERDICT: REVISE**

## 摘要

规划整体质量高，代码引用几乎全部准确，架构决策合理，DAG 划分基本可行，验收 100% 自动化符合用户规则。但存在 2 个 CRITICAL 问题必须修复：(1) `globals.css:185-195` 存在 `.font-sans`/`.font-mono`/`.font-heading` 三个 CSS 类在 `@layer base` 内显式绑 `var(--font-u/d)`，planner 完全遗漏了对它们的处理，而它们会与 Tailwind v4 `@theme inline` 生成的 utility 发生规则冲突；(2) `task.json` 中所有 subtask 的 `depends_on` 全部为 `null`，与 4-tasks.md 定义的依赖关系严重不一致，执行器若依赖该字段会把 DAG 完全拍平。另有若干 MAJOR 发现（chartTheme.ts 未纳入影响域、t5 缺 t8 依赖、数字口径 40+ vs 96 不一致）需同步修复。

## 代码引用验证

| 引用 | 文件存在 | 内容准确 | 问题 |
|------|---------|---------|------|
| `src/web/src/app/layout.tsx:25-28` `<link>` 标签 IBM Plex CDN | Yes | Yes | 实际 L25-28（文档 L25-27/L25-28 口径不一，影响小） |
| `src/web/src/app/globals.css:15-16` `--font-d/u` 字面量 | Yes | Yes | 精确命中 |
| `src/web/src/app/globals.css:185-195` `.font-sans`/`.font-mono`/`.font-heading` 类覆写 | Yes | **遗漏** | 见 CRITICAL #2 |
| `src/web/src/app/globals.css:206-213` `body {}` block | Yes | Yes | 精确命中 L206-213 |
| `src/web/src/app/globals.css:216-218` `@theme inline` 内 `--font-sans/--font-mono` | Yes | Yes | 精确命中 |
| `src/web/src/app/analytics/page.tsx:337, 427` IBM Plex Mono | Yes | Yes | 两处字面量均存在；描述「2 处」吻合 |
| `src/web/src/app/trading/components/tabs/OverviewTab.tsx:251,252,290,291` | Yes | Yes | 四处字面量均存在；描述「4 处」吻合 |
| `src/web/CLAUDE.md:141` IBM Plex 声明 | Yes | Yes | 精确命中 |
| 根 `CLAUDE.md:268` Font 声明 | Yes | Yes | 精确命中 |
| `.claude/skills/TinoHelmDS/SKILL.md:25-31` 字体纪律 | Yes | Yes | 精确命中 |
| `.claude/skills/TinoHelmDS/colors_and_type.css:6, 13-15, 19-20, 185` | Yes | Yes | 全部命中（@import L6；token L13-15；legacy L19-20；`font-feature-settings` L185） |
| `.claude/skills/TinoHelmDS/fonts/nextjs-setup.md:14-45, 75-111, 86-95` | Yes | Yes | 全部命中（含 Noto SC 例子、globals.css 叠加方案） |
| **新增发现**：`src/web/src/lib/chartTheme.ts:14, 23, 30, 36` | Yes | 遗漏 | 见 MAJOR #1 |

代码引用 14 处中 **13 处准确**，**1 处（globals.css:185-195）关键遗漏**。

## 需求审查 (1-requirements.md)

- FR-1 ~ FR-6 覆盖齐全；可追溯矩阵完整。
- **[MINOR]** FR-2 文字称「96 处 `var(--font-d)` / `var(--font-u)` 引用」——实际 `grep -oE 'var\(--font-[du]\)'` 统计 **97** 次（globals.css 内匹配数）。interview.md 使用「40+ 处」（粗略）。两种口径混用但影响有限；建议全部统一为 97（或保留「96+」）。
- **[MINOR]** NFR-1 声称「41 个 `.tsx` 文件零改动」——实际 `rg '\bfont-(sans|mono|heading)\b'` 得 **44** 个文件。口径偏差来自是否统计 `.font-heading` 消费方 + 是否排除 tests 目录。建议统一为 44。

## 技术设计审查 (3-tech-design.md)

### Critical 发现

1. **[CRITICAL] `globals.css:185-195` 的 `.font-sans` / `.font-mono` / `.font-heading` 类覆写未处理**
   - 证据：`/Users/ouzhuohao/TinoHelm/src/web/src/app/globals.css:185-195`：
     ```css
     @layer base {
       .font-sans { font-family: var(--font-u); }
       .font-heading { font-family: var(--font-u); }
       .font-mono { font-family: var(--font-d); }
     }
     ```
   - 影响：Tailwind v4 通过 `@theme inline --font-sans: …` 生成的 utility 形如 `.font-sans { font-family: var(--font-sans); }`。当用户写 `className="font-sans"` 时，**哪个规则胜出取决于层叠顺序** —— globals.css 内显式 `.font-sans` 规则位于 `@layer base` 块内，Tailwind utilities 也在 base 层；同层内后者胜出，但加载顺序（`@import "tailwindcss"` 在文件开头 L1，手写 `.font-sans` 在 L185）决定了**手写覆写胜出**。这意味着：
     - Token 路径：`className="font-sans"` → `.font-sans { font-family: var(--font-u) }` → legacy alias `var(--font-u)` → `var(--font-sans)` → 最终命中 Inter。
     - 表面看仍然通，但**多绕了一跳 legacy 别名**，与 tech-design «3.Tailwind 消费 → @theme inline 直接消费 :root token» 的模块 3 图示**矛盾**。
   - 修复：明确选一：
     - 选项 A（推荐）：**删除** `globals.css:185-195` 的三个类覆写，完全依赖 Tailwind v4 utility。理由：消费方用的是 `className="font-sans"`（Tailwind utility），不是 CSS 类；除非确有 non-Tailwind 上下文需要 `.font-sans` 类直接可用（explorer 未举证），否则冗余。
     - 选项 B：**保留**但将右值从 `var(--font-u)` 改为 `var(--font-sans)`（与 tech-design 一致）。`.font-heading` 可选保留，但要说明语义。
   - 无论选 A 或 B，**新增 vitest 断言**：断言 globals.css 中不存在 `.font-sans { font-family: var(--font-u) }` 这种「新代码走 legacy 别名」的反向绑定（AC-1 新增）。

2. **[CRITICAL] `task.json` 的 subtasks `depends_on` 全部为 `null`，与 4-tasks.md 严重不一致**
   - 证据：`/Users/ouzhuohao/TinoHelm/.cage/tasks/2026-04-18-font-standardization/task.json:10-103` 16 个 subtask 的 `depends_on` 字段**全为 `null`**。但 4-tasks.md 每个子任务都明确声明了 `depends_on`（如 t3→[t1]、t5→[t3,t4,t9]、t16→[t1,t14,t15]、t17→11 个依赖）。
   - 影响：Cage executor 的 spec 解析若以 `subtask.depends_on` 为唯一真源，整个 DAG 会被**拍平为全并行**，t5 会在 t3/t4/t9 尚未完成时启动，导致 tokens.test.ts 断言在未改写的 globals.css 上运行，全部失败。
   - 修复：写入 task.json 时，依据 4-tasks.md 填充每个 subtask 的 `depends_on`：
     ```jsonc
     { "id": "t1",  "depends_on": [] },
     { "id": "t3",  "depends_on": ["t1"] },
     { "id": "t4",  "depends_on": ["t1"] },
     { "id": "t5",  "depends_on": ["t3", "t4", "t8", "t9"] },  // 注意补 t8
     { "id": "t6",  "depends_on": [] },
     { "id": "t7",  "depends_on": ["t6"] },
     { "id": "t8",  "depends_on": ["t7"] },
     { "id": "t9",  "depends_on": ["t7"] },
     { "id": "t10", "depends_on": [] },
     { "id": "t11", "depends_on": [] },
     { "id": "t12", "depends_on": [] },
     { "id": "t13", "depends_on": [] },
     { "id": "t14", "depends_on": [] },
     { "id": "t15", "depends_on": [] },
     { "id": "t16", "depends_on": ["t1", "t14", "t15"] },
     { "id": "t17", "depends_on": ["t5","t7","t8","t9","t10","t11","t12","t13","t14","t15","t16"] }
     ```

### Major 发现

1. **[MAJOR] `src/web/src/lib/chartTheme.ts` 未纳入影响域**
   - 证据：`/Users/ouzhuohao/TinoHelm/src/web/src/lib/chartTheme.ts:14, 23, 30, 36` 四处 `fontFamily: "var(--font-d)"` 使用 legacy 别名。
   - 现状：tech-design 「影响文件清单」表（第 83-97 行）未列出 `chartTheme.ts`；模块 5「Chart Tick 字面量替换」只覆盖 analytics/page.tsx + OverviewTab.tsx，没有把 chartTheme.ts 作为「新代码应消费新 token」的治理目标。
   - 矛盾：模块 5 收尾注释明确写「**为何不用 `var(--font-d)`**：新代码优先消费权威 token」—— 但 chartTheme.ts 是**现存代码**不算「新代码」；按兼容策略 legacy 别名自动转发是 OK 的。
   - 但 tech-design 也应显式声明：chartTheme.ts 的 4 处 `var(--font-d)` 属于「legacy alias 保护的 96+处引用」范畴，**零改动**。这一点当前 tech-design 未说明，容易在 review 时被误以为是遗漏。
   - 修复：在 tech-design 模块 5 末尾或「影响文件清单 — 不改动（明确豁免）」小节补一行：「`src/web/src/lib/chartTheme.ts:14/23/30/36` 使用 `var(--font-d)` 属于 legacy alias 保护域，零改动。」

2. **[MAJOR] t5 缺少对 t8 的依赖**
   - 证据：`4-tasks.md` 第 83 行 t5 `depends_on: ["t3", "t4", "t9"]`。但 t5 的 tokens.test.ts 包含 3 个断言（tech-design §7.2 L312-316）检查 `body` 块内的 `font-feature-settings cv11/ss01/ss03`，这 3 行由 t8 引入。
   - 影响：parallel_groups W3 把 t8 和 t9 放在同一波次（task.json L122-125 `["t8","t9"]`），W4 只有 t5（L126-128）。实践中 W3 完成后 W4 才启动，**t5 运行时 t8 已完成**，能通过。
   - 但 dependency 声明不完整——若未来有人调整 parallel_groups（如将 t8 延后到 W4），t5 的逻辑依赖不再被锁定。
   - 修复：t5 `depends_on` 改为 `["t3", "t4", "t8", "t9"]`。对应 task.json 也需同步（见 CRITICAL #2）。

3. **[MAJOR] `check-grep-fonts.sh` 中 `cd` 到 src/web 后路径漂移风险**
   - 证据：tech-design §7.5 L401 `cd "$(dirname "$0")/.."` 从 `src/web/scripts/` `cd` 到 `src/web/`。然后 L425 `rg -q "IBM Plex" CLAUDE.md` 查 `src/web/CLAUDE.md`（OK），L426 `rg -q "IBM Plex" ../../CLAUDE.md` 查仓库根 CLAUDE.md（路径为 `src/web/../../CLAUDE.md` = `/Users/ouzhuohao/TinoHelm/CLAUDE.md`，正确）。
   - 潜在问题：`../../CLAUDE.md` 是**硬编码相对深度**，若将来脚本被移动或 src/web/ 被重组，路径静默失效（`rg` 对不存在文件返回非零但被 `check_no_match` 反转，误判「PASS」）。
   - 另：L421 `rg -q "fonts.googleapis.com" src/` 在 `cd src/web/` 后查找 `src/web/src/`（正确），但与 requirements.md AC-2.2 的 `rg "fonts.googleapis.com" src/web/src/` 表述不一致（脚本隐式少一级路径）。虽然最终搜索范围一致，但可读性差。
   - 修复：
     - 加文件存在性前置：`[ -f ../../CLAUDE.md ] || { echo "[FAIL] root CLAUDE.md not found"; exit 1; }`
     - 或改用从仓库根 git rev-parse 定位：`REPO_ROOT=$(git rev-parse --show-toplevel); cd "$REPO_ROOT/src/web"`。

4. **[MAJOR] `@theme inline` identity 转发 `--font-sans: var(--font-sans)` 的 Tailwind v4 行为需要补「verification hook」**
   - 证据：tech-design §模块 4 L206 宣称 Tailwind v4 在 `@theme inline` 内允许同名 variable 自引用，**引用解析到外部 `:root`**，不会产生循环。research §5 L128-131 给出数据流图，但未引用 Tailwind v4 官方文档原文作为证据。
   - 验证：从 Tailwind v4 源码/文档看，`@theme inline` 的语义是「不要在 at-root 注入 CSS variable，而是把值直接内联到 utility 定义中」。`--font-sans: var(--font-sans)` 展开为 `.font-sans { font-family: var(--font-sans); }`，其中 `var(--font-sans)` 在运行时解析 `:root` 的 `--font-sans`，**不产生编译期循环**。这一点正确，但 tech-design 应加一个引用以消除读者疑虑。
   - 缓解已存在：t9 的验收是 `npm run build` 退出码 0，若出现循环会构建失败；这是有效 smoke test。
   - 修复：在 tech-design §模块 4 末尾补一行：「**验证路径**：若 `@theme inline --font-sans: var(--font-sans)` 触发循环，`next build`（t9 验收）会失败；当前 Tailwind v4（`@tailwindcss/postcss ^4`）语义下此模式安全。」

5. **[MAJOR] verify-build-fonts.mjs 的 woff2 正则可能过严/过宽**
   - 证据：tech-design §7.4 L369-374 正则 `/inter.*\.woff2$/i`、`/jetbrains.*mono.*\.woff2$/i`。
   - Next.js v16 通过 `next/font/google` 下载字体时的实际文件名格式：`<font-family>-<weight>-<subset>-<hash>.woff2`，实际观察到的文件名有时是 `<hash>-s.woff2`（短哈希形式）——**不含 `inter` 字面量**。
   - 参考 Next.js 16 源码：`next/dist/compiled/fontkit/...`，新版默认使用 hash-first 命名（尤其 `display: swap` + variable font 时）。
   - 影响：若 Next.js 16 不在文件名中包含字体族名，AC-3.2 会误报 MISSING。
   - 修复：验证方式改为**两个正交证据**：
     1. 产物目录有至少 `>= 2` 个 `.woff2` 文件（粗条件）；
     2. 通过解析 `.next/server/app/layout.tsx.css` 或 `.next/static/css/*.css` 里 `@font-face` 的 `font-family: 'Inter'` 字面量，反向证明字体资源已打包。
   - 更保险的方案：调用 Next.js 构建后暴露的 manifest（`.next/required-server-files.json` 或 `.next/font-manifest.json`），直接枚举加载的字体资源。
   - 或：保留现有正则但**新增备用断言**：`woff2.length >= 2` 作为 fallback PASS（Inter + JBM 合计至少 2 个）。若精确匹配失败但文件数达标，打印 warning 不 fail。

6. **[MAJOR] t16 的 parallel_groups 定位偏差**
   - 证据：4-tasks.md L287 波次表 W1 不含 t16，W2 含 t16；task.json L106-120 W1 不含 t16，W2 含 t16（一致）。但 4-tasks.md L288 注释写「t16 依赖 t1/t14/t15」——其中 **t14 和 t15** 都在 W1，而 t16 在 W2 是正确的。
   - 问题：t16 的实际依赖是 `[t1, t14, t15]`，其中 t1/t14/t15 全部在 W1；t16 可以在 W2 启动，但同波次的 t3/t4 仅依赖 t1，t7 依赖 t6；**t7 依赖 t6（W1），自身在 W2** 也合理。W2 内部无互相依赖，并行安全。
   - 非 blocker，但如前所述 task.json 的 per-subtask `depends_on` 应补齐（CRITICAL #2），这样即使 parallel_groups 被工具忽视，Cage DAG 引擎仍能从 subtask.depends_on 重新推导。

### Minor 发现

1. **[MINOR] 数字口径三处不一致**：interview.md 「40+ 处 var(--font-d/u) 引用」、1-requirements.md 「96 处」、3-tech-design.md 「96 处」。实际 grep 为 97 次（不是 96）。建议统一为「**97 处**」或「96+」且 interview.md 同步更新。

2. **[MINOR] 消费 tsx 文件数口径不一**：interview 与 1-requirements.md 称「41 个 `.tsx` 文件」，实际 `rg '\bfont-(sans|mono|heading)\b' src/web/src --type ts` 得 **44** 个（含 font-heading）。影响小，建议更新为 44 或显式说明排除了 font-heading。

3. **[MINOR] tokens.test.ts 的 `extractBlock` 正则对 `@theme inline` selector 的处理**
   - 证据：tech-design §7.1 L252 正则 `${selector.replace(...)}\\s*\\{([\\s\\S]*?)\\}`。当 selector 为 `@theme inline` 时，正则生成 `@theme inline\s*\{...\}`。但 `@theme inline` 前需转义 `@`？`@` 不是正则特殊字符，不需转义，OK。
   - 但：正则 lazy match（`[\s\S]*?`）碰到 CSS 块内嵌套大括号（如 @keyframes 或 media query）时会**提前终止**。虽然 `@theme inline` 块目前无嵌套大括号（从 L216-295 看），安全。
   - 建议：注释中标注这一假设：「该 extractBlock 不支持嵌套 `{}`；若未来 `@theme inline` 块内新增嵌套规则需重写。」

4. **[MINOR] AC-2.1 grep 中排除的 globs 与脚本不一致**
   - 证据：1-requirements.md AC-2.1（L126）`rg "IBM Plex" src/web/ --glob '!*.html' --glob '!*.bak'`；interview.md L50 多加一个 `--glob '!interview.md'`；tech-design §7.5 L418 多加 `--glob '!node_modules' --glob '!.next' --glob '!out'`。
   - 实际：task 文件夹 `.cage/tasks/2026-04-18-font-standardization/interview.md` 不在 `src/web/` 下，任何 glob 都不会匹配。interview 的 `!interview.md` 属于防御性冗余。统一为最严格版本（tech-design §7.5 的扩展）最稳。
   - 修复：将 1-requirements.md AC-2.1 的 rg 命令更新为与 §7.5 脚本一致。

5. **[MINOR] t5 验收命令缺 pytest 风格的失败输出要求**
   - 4-tasks.md L88-90 t5 验收仅要求退出码 0。若 vitest assertion fail，退出码非 0，验证会失败——OK。但若 vitest 自身无法加载（缺 dep、TS 类型错误），同样非 0；需要区分 assertion fail vs framework fail。
   - 建议：加 `--reporter=verbose` 让 log 输出具体哪个断言失败，便于执行阶段 debug。

6. **[MINOR] 缺少 source-serif-4 加载断言**
   - interview.md AC-1 L42 要求 `--font-serif` 存在且包含 `Source Serif 4`。实际 tech-design §7.2 L291-294 断言的是 `/^var\(--font-source-serif\)/`（指向 Next.js CSS variable）——这是正确的（token 层）。但 requirements.md AC-3.2 L148 「若启用 Source Serif 4，至少 1 个匹配 `/source.*serif.*\.woff2$/i`（**否则跳过此断言**）」——"否则跳过" 让 AC 可选化，不符用户「100% 自动化且不可跳过」的规则。
   - 修复：Source Serif 4 加载在 layout.tsx 中是**必选**（已在 t6 改动写入 `preload: false` 但仍加载）。AC-3.2 应改为硬性断言：`至少 1 个 source-serif woff2 文件存在`，不再有 "否则跳过" 分支。

## DAG 审查 (4-tasks.md + task.json)

- **[CRITICAL]** task.json 的 subtasks 全 `depends_on: null`，必须按 4-tasks.md 填充（见 Critical #2）。
- **[MAJOR]** t5 缺 t8 依赖（见 Major #2）。
- **[OK]** parallel_groups 5 波次划分合理：W1 (8 任务) → W2 (4 任务 含 t16) → W3 (2 任务 t8/t9) → W4 (t5) → W5 (t17)。关键路径 t1→t7→t9→t5→t17 = 5 层深度，合理。
- **[OK]** t17 汇总验证正确收敛 11 个前置。
- **[OK]** t10、t11、t12、t13 完全独立，放 W1 合适。
- **[OK]** 任务粒度：16 个子任务中无过大（每个 ≤ 1 文件改动）、无过小（t14/t15 可能合并但拆开更易验证）。
- **[OK]** 最后有 t17 作为端到端验证任务。

## 权衡分析

| 决策 | 正方 | 反方 | 建议 |
|------|------|------|------|
| **新 token 权威，legacy alias 反指** | 与 QDS 权威文档 100% 一致；新代码语义清晰 | 96+ 处 `var(--font-d/u)` 多走一跳 `var(--font-mono/sans)`；多层嵌套 var 在老浏览器有 resolve 深度限制（Chrome 已取消，Safari 历史上限 20 层，当前 OK） | 继续选；在 tech-design 加一句「浏览器支持：现代浏览器 CSS var 解析深度 ≥ 20，本方案实际深度 ≤ 3，安全」 |
| **`@theme inline --font-sans: var(--font-sans)` identity 转发** | 最简单；与 Tailwind v4 原生语义一致；编译器负责消除 | 读者困惑（自引用是否循环）；依赖 Tailwind v4 实现细节 | 继续选；tech-design 加一句 Tailwind v4 文档引用 + 说明 next build smoke 捕获循环 |
| **Source Serif 4 `preload: false`** | 首屏不阻塞；体积不浪费（~100KB）；对齐 QDS「备用」定位 | 首次进长文档页需等运行时下载；若未来添加 serif UI 会延迟渲染 | 继续选；AC-3.2 应改为硬性断言存在 woff2，不再「可跳过」 |
| **不加载 Noto SC 中文字体** | 打包减 1-2 MB；目标用户（macOS/鸿蒙）本地已有 PingFang/HarmonyOS Sans | Windows + 无中文字体用户可能回退到默认字体（丑） | 选定方案合理；建议补充「本项目目标用户是内部量化研究员，操作系统分布已调研（macOS + 鸿蒙为主）」作为决策证据 |
| **vitest 作为静态断言框架** | 无 jsdom 依赖；ESM-first；与 next 16 兼容 | 新增 devDep 体积（vitest 约 20MB）；与现有 lint 生态未整合 | 选定方案合理；考虑是否把 `npm run test:fonts` 集成到 `npm run lint` 或 CI 流水线前置 |
| **CSS 正则 parseBlock 而非 postcss AST** | 零 deps；代码 < 20 行 | 对嵌套 `{}` 不稳；对 CSS 注释不感知（若 `--font-sans` 被注释掉仍匹配） | 可接受；tokens.test.ts 加一个 sanity test：`expect(css).not.toMatch(/\/\*[\s\S]*?--font-sans[\s\S]*?\*\//)` 防止字段被误注释 |
| **保留 globals.css 内 `.font-sans`/`.font-mono` 类覆写 vs 删除** | 保留：兼容 non-Tailwind 上下文（若存在） | 删除：消除与 Tailwind utility 的层叠冲突；token 流图清晰 | **必须明确选择并在 tech-design 中说明理由**（见 CRITICAL #1） |

## 遗漏项

1. **globals.css:185-195 `.font-sans`/`.font-mono`/`.font-heading` 类覆写策略** —— 最严重遗漏（CRITICAL #1）。
2. **chartTheme.ts:14/23/30/36** `var(--font-d)` 引用未在「豁免域」显式列出（MAJOR #1）。
3. **verify-build-fonts.mjs 对 Next.js 16 新版文件名格式的鲁棒性** —— 正则可能过严（MAJOR #5）。
4. **task.json subtasks depends_on 全空** —— 与 4-tasks.md 文档定义严重不一致（CRITICAL #2）。
5. **AC-3.2 Source Serif 4 的「可跳过」分支违反「100% 自动化不可跳过」规则** —— Minor #6。
6. **t5 逻辑依赖 t8 未在 depends_on 声明** —— Major #2。

## 上轮修改验证

不适用（本次为第 1 轮审查）。

## 修改要求（REVISE 必填）

### 必修项（CRITICAL，阻塞 APPROVE）

- **C1** [3-tech-design.md §模块 2 或新增 §模块 2.5] 明确 `globals.css:185-195` 三个类覆写的处理策略：
  - 推荐方案：**删除** `.font-sans`/`.font-mono`/`.font-heading` 三个 `@layer base` 内的 CSS 类覆写（它们与 Tailwind v4 `@theme inline` 生成的 utility 冗余），依赖 Tailwind utility 生效。
  - 若选择保留，将右值从 `var(--font-u/d)` 改为 `var(--font-sans/mono)`，与 tech-design 数据流图保持一致。
  - 同步更新 t7（或新增 tX）任务清单，包含这次清理。
  - 同步更新 tokens.test.ts 新增一条断言：**globals.css 内不存在 `.font-sans { font-family: var(--font-u) }` 形式的反向绑定**（`expect(css).not.toMatch(/\.font-(sans|mono|heading)\s*\{\s*font-family:\s*var\(--font-[du]\)/)`）。
  - AC-1 断言列表（1-requirements.md L106-116）补上这条。

- **C2** [task.json subtasks depends_on 字段] 按 4-tasks.md 的 depends_on 定义填充所有 16 个 subtask 的 `depends_on` 字段。当前全 `null` 是错误状态。具体填充表见上文 CRITICAL #2。同时把 t5 的 `depends_on` 由 `["t3","t4","t9"]` 更新为 `["t3","t4","t8","t9"]`。

### 必修项（MAJOR）

- **M1** [3-tech-design.md §影响文件清单「不改动（明确豁免）」小节] 增加一行：「`src/web/src/lib/chartTheme.ts:14/23/30/36` 使用 `var(--font-d)` 属于 legacy alias 保护域，零改动。」

- **M2** [4-tasks.md t5 depends_on] 改为 `["t3", "t4", "t8", "t9"]`，并在描述里补说明「t8 是因 tokens.test.ts 包含 `font-feature-settings cv11/ss01/ss03` 断言」。

- **M3** [3-tech-design.md §7.4 verify-build-fonts.mjs] 把正则匹配改为**两层防御**：
  - 强条件：`woff2.length >= 2`（必须至少 2 个 woff2 文件）；
  - 软条件：尝试正则精确匹配 Inter/JBM；命中则打印 OK，未命中打印 warning（不 fail）；
  - 或：改用解析 `.next/static/css/*.css` 的 `@font-face { font-family: 'Inter' }` 字面量作为强证据。
  - 无论选哪种，都要确保 Next.js 16 的文件命名变化不会误伤。

- **M4** [1-requirements.md AC-3.2 + 3-tech-design.md §7.4] 删除 Source Serif 4 的「否则跳过」分支，改为硬性断言：既然 layout.tsx 会 import Source Serif 4（t6 的 FR-1），产物中必有对应 woff2，**不应允许跳过**。

- **M5** [3-tech-design.md §7.5 check-grep-fonts.sh] 改进路径健壮性：
  - 在脚本开头加 `REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "not a git repo"; exit 1; }`；
  - 后续用 `"$REPO_ROOT/CLAUDE.md"` 替代 `../../CLAUDE.md`。

- **M6** [3-tech-design.md §模块 4] 补一句 Tailwind v4 `@theme inline` 自引用安全性的说明 + t9 `next build` 作为运行时 smoke 的证据闭环。

### 可选项（MINOR）

- **m1** 统一口径：全部改为「**97 处** `var(--font-d/u)` 引用 + **44 个** `.tsx` 消费文件」，或保留但在文档显式说明口径差异。
- **m2** tokens.test.ts 加一个 sanity test：确认 `--font-sans` / `--font-mono` 没有被注释掉（防误改）。
- **m3** 4-tasks.md t5 验收命令加 `--reporter=verbose`。
- **m4** 1-requirements.md AC-2.1 的 rg 命令与 3-tech-design.md §7.5 脚本参数保持一致。

---

ReviewPass: architect
VERDICT: REVISE
