# Revision R1 → R2 — 修订摘要

本次修订针对 `review-architect-r1.md` + `review-critic-r1.md` 两份审查报告合并后的
BLOCKER / CRITICAL / MAJOR / MINOR 项。所有修订点均写入文档，可供 R2 审查员复核。

## 任务目录结构变更

| 文件 | R1 | R2 |
|---|---|---|
| `1-requirements.md` | 6 FR / 3 NFR / 4 AC | 7 FR（+FR-4 类覆写清理）/ 3 NFR / 4 AC（AC-1 由 11 断言 → 15 断言） |
| `2-research.md` | 8 节 | 9 节（+§9 CI 环境要求）；§4 Source Serif 4 从"可选加载"改为"明确不加载" |
| `3-tech-design.md` | 7 模块 | 8 模块（+§模块 2.5 类覆写清理）；§7.1 从正则改为 postcss AST |
| `4-tasks.md` | 16 subtasks | 17 subtasks（+t7a）；t5 depends_on 由 `[t3,t4,t9]` 更正为 `[t3,t4,t7,t7a,t8,t9]` |
| `task.json` | 16 subtasks 全 `depends_on: null` | 17 subtasks 全部正确填充 `depends_on`；parallel_groups 重算 |

---

## BLOCKER / CRITICAL 修订对应表

### C1 (architect) — globals.css `.font-sans`/`.font-mono`/`.font-heading` 类覆写处理

- **问题**：R1 plan 完全遗漏 globals.css:185-195 的三个 `@layer base` 类覆写，与 Tailwind v4 `@theme inline` 产出的 utility 形成冗余/绕路。
- **修订位置**：
  - `1-requirements.md`：新增 **FR-4** 段落；AC-1 断言从 11 → 15，新增 **A1.12**（不存在 `.font-sans/.font-mono/.font-heading` 绑定到 `var(--font-u/d)`）。
  - `3-tech-design.md`：新增 **§模块 2.5**「清理 `@layer base` 内 legacy 类覆写」；影响文件清单中 globals.css 改动 block 数从 3 → 4。
  - `4-tasks.md`：新增 **t7a** 子任务；t5 / t17 依赖中加入 t7a。
  - `task.json`：新增 t7a 条目；parallel_groups W3 新增 t7a。
- **改动摘要**：明确选择"删除三个类覆写"方案（非保留），理由与 `.font-heading` 的 9 个消费方退化路径（继承 body 的 `var(--font-u)` → `var(--font-sans)` → Inter，行为等价）均已在 FR-4 与 §模块 2.5 中说明。

### C2 (architect/critic) — task.json subtasks `depends_on` 全为 `null` 且 t5 缺 t8 依赖

- **问题**：R1 task.json 所有 16 个 subtask 的 `depends_on: null`，若 executor 以此为 DAG 真源会被拍平；且 t5 实际需要 t8（body feature-settings 先写入）。用户还指出字段名必须为 `depends_on`（与 cage 工具 schema 一致），R1 曾在讨论中涉及 `deps` 字段，但 task.json 原本用的已是 `depends_on: null`——修订后**保持并明确**字段名 `depends_on`，仅填充正确的数组值。
- **修订位置**：
  - `task.json`：17 个 subtask 的 `depends_on` 全部按 `4-tasks.md` 的 DAG 填充为数组（非 null）。
  - `4-tasks.md`：t5 的 depends_on 从 `[t3,t4,t9]` 更正为 `[t3,t4,t7,t7a,t8,t9]`（同时补 t7a）。
- **改动摘要**：
  ```
  t1 [], t3 [t1], t4 [t1], t5 [t3,t4,t7,t7a,t8,t9], t6 [],
  t7 [t6], t7a [t7], t8 [t7], t9 [t7],
  t10 [], t11 [], t12 [], t13 [], t14 [], t15 [],
  t16 [t1,t14,t15], t17 [t5,t7,t7a,t8,t9,t10,t11,t12,t13,t14,t15,t16]
  ```

### C3 (critic, 新发现) — `extractBlock(css, 'body')` 正则会误匹配 `html, body {...}`

- **问题**：R1 的 `extractBlock` lazy 正则对 `body\s*{` 的第一个匹配会落到 `globals.css:179` 的 `html, body { height: 100%; }`，使所有 body 断言在错误块上执行、全部 FAIL。
- **修订位置**：
  - `3-tech-design.md §7.1`：完全改写 `parse-css.ts` — 改用 **postcss AST 解析**，通过 `walkRules(rule => rule.selector.trim() === 'body')` 精确匹配（排除 `'html, body'`）。同时 `getRootDecls` / `getThemeInlineDecls` 都改为 AST 遍历。
  - `3-tech-design.md §7.2`：`tokens.test.ts` 新增 sanity 断言 `body rule contains @apply directive`，确保取到的是期望的 body 块。
  - `2-research.md §7`：论证选择 postcss 的理由（零新增依赖——已通过 `@tailwindcss/postcss` 传递）。
  - `4-tasks.md` t4：fixture 实现切换到 postcss AST；`t1` devDeps 增加 postcss ^8.4.49（显式声明避免 hoist 不稳定）。
- **改动摘要**：不再依赖 ad-hoc 正则，从根本上消除 selector 误匹配问题。

### C4 (critic, architect M3 升级) — `verify-build-fonts.mjs` 的 woff2 文件名正则必然失败

- **问题**：Next.js 16 `next-font-loader` 产出的 woff2 文件名格式为 `[hash]{-s|}{.p|}.woff2`，**绝不包含** `inter` / `jetbrains` / `source-serif` 字面量。R1 的 `/inter.*\.woff2$/i` 类正则 100% 失败，AC-3 会无论代码对错都挂。
- **修订位置**：
  - `3-tech-design.md §7.4`：完全重写 `verify-build-fonts.mjs` —
    - 扫描 `.next/static/css/*.css`（fallback `out/_next/static/css/*.css`）聚合所有 CSS 文本；
    - 通过正则 `@font-face\s*\{[^}]*font-family\s*:\s*['"]([^'"]+)['"]` 抽取 `@font-face` 的 `font-family` 字面量；
    - 强证据 1: 字面量出现 `Inter`；
    - 强证据 2: 字面量出现 `JetBrains Mono`；
    - 强证据 3: `.next/static/media/**/*.woff2` 数量 ≥ 2；
    - 强约束 4: 字面量**不出现** `Source Serif 4`（本任务不加载）。
  - `1-requirements.md` AC-3.2：与新脚本对齐，描述三项强证据 + 一项强约束。
- **改动摘要**：文件名无关、行为精确、对 Next.js 16 稳定。

### C5 (critic, architect M4 升级) — AC-3.2 "否则跳过" 违反用户全局 RULE

- **问题**：R1 AC-3.2 允许 Source Serif 4 "若启用...否则跳过"，等价于自动化永远 PASS，违反用户 RULE（禁止手动验证/可跳过 item）。t6 同时强制 import Source Serif 4，造成自身矛盾。
- **修订位置**：
  - `2-research.md §4`：从"加载 Source Serif 4，`preload: false`"改为"**明确不加载**"，论证与用户全局 RULE 兼容的路径。
  - `1-requirements.md` FR-1：layout.tsx 仅导入 Inter + JetBrains_Mono（**不含** Source_Serif_4）。
  - `1-requirements.md` AC-3.2：去掉 "若启用...否则跳过" 分支；改为强证据 1+2+3 **必须** PASS + 强约束 4（反向断言产物中**不得**包含 Source Serif 4）。
  - `3-tech-design.md §模块 1`：字体声明只有 2 个。`:root` 块不再定义 `--font-serif`。
  - `3-tech-design.md §7.4`：`verify-build-fonts.mjs` 的 `forbidden` 列表含 `'Source Serif 4'`。
  - `4-tasks.md` t6：验收中 `! rg -q "Source_Serif_4"` + `! rg -q "sourceSerif"` 两条负匹配。
- **改动摘要**：明确"不加载"路径，AC 100% 自动化不可跳过；未来若需 serif 另开子任务。

### BLOCKER（用户提示）— `rg -c | xargs test N -le` 静默 PASS 陷阱

- **问题**：`rg -c 'pattern' file | xargs test N -le` 在 0 匹配时（无 stdout）xargs 不执行目标命令直接退出 0，使验收静默 PASS 即使 executor 漏替换。
- **修订位置**：
  - `4-tasks.md` t10 / t11：验收命令从 `rg -c ... | xargs test N -le` 改为：
    ```bash
    count=$(rg -c 'fontFamily:\s*"var\(--font-mono\)"' <file> 2>/dev/null || echo 0)
    test "${count:-0}" -ge <N> || { echo "expected >= <N>, got ${count}"; exit 1; }
    ```
  - 同时保留 `! rg -q "IBM Plex" <file>` 的负匹配作为双层保险。
- **改动摘要**：0 匹配时必然失败（`test N -ge 0` 为 false when N > 0），修复静默 PASS 路径。

### BLOCKER（用户提示）— task.json 字段名从 `deps` 统一为 `depends_on`

- **问题**：用户指出 task.json 的 subtasks 字段必须使用 `depends_on`（与 cage 工具 schema 一致）。
- **实际状态**：R1 task.json 已经使用 `depends_on` 字段名（只是值为 `null`），故本次修订仅**填充正确的数组值**，字段名保持 `depends_on` 不变。
- **修订位置**：`task.json` 所有 subtask 条目。
- **改动摘要**：无字段重命名动作；仅值从 `null` → 正确数组。

---

## MAJOR 修订对应表

### M1 (architect) — `src/web/src/lib/chartTheme.ts` 未纳入豁免域显式列出

- **修订位置**：
  - `1-requirements.md` NFR-1：新增一行「`src/web/src/lib/chartTheme.ts:14/23/30/36` 使用 `var(--font-d)` 属于 legacy alias 保护域，零改动」。
  - `3-tech-design.md` §影响文件清单「不改动（明确豁免）」小节：同上。
- **改动摘要**：明确此 4 处为保护域，避免 R2 审查员误读为遗漏。

### M2 (architect) — t5 缺 t8 依赖

- 见 C2 修订。t5 depends_on 从 `[t3,t4,t9]` → `[t3,t4,t7,t7a,t8,t9]`（补全 t7/t7a/t8）。

### M3 (architect) — next/font/google 对 Inter cv11/ss01/ss03 支持机制澄清

- **问题**：需明确 `next/font/google` 的 Inter loader 不支持通过 `axes` 声明风格集——必须通过 CSS `font-feature-settings` 在运行时启用。
- **修订位置**：
  - `2-research.md §2`：新增「`next/font/google` 的能力边界」子章节，给出 Next.js 源文件路径 + 类型签名证据。
  - `3-tech-design.md §字体加载架构图「OpenType 特性注入点」`：补充启用机制澄清。
  - `1-requirements.md` FR-3：增加同样说明。
- **改动摘要**：保留在 CSS `body` 注入的方案；文档补证据消除读者疑虑。

### M4 (architect) — `@theme inline --font-sans: var(--font-sans)` 自引用安全性证据

- **修订位置**：
  - `2-research.md §5`：新增「`@theme inline --font-sans: var(--font-sans)` 自引用的安全性」子章节，给出 Tailwind v4 语义 + 降级方案。
  - `3-tech-design.md §模块 4`：补「原理与验证」段，含 `next build` smoke 闭环证据。
- **改动摘要**：建立证据闭环：若 Tailwind v4 行为变更，t9 的 `npm run build` 会捕获。

### M5 (architect) — `check-grep-fonts.sh` 路径漂移

- **修订位置**：`3-tech-design.md §7.5`：
  - 脚本起首 `REPO_ROOT=$(git rev-parse --show-toplevel)` 锁定；
  - `cd "$REPO_ROOT"` 取代相对路径 `cd "$(dirname "$0")/.."`；
  - 文件存在性前置检查 `[ -f "CLAUDE.md" ]` 与 `[ -f "src/web/CLAUDE.md" ]`。
- **改动摘要**：路径健壮性提升，支持任意工作目录执行。

### M6 (critic) — CI 环境 rg 版本要求

- **修订位置**：
  - `2-research.md §9`（新增章节）：显式声明 rg ≥ 13、node ≥ 18、git、外网访问 Google。
  - `3-tech-design.md §7.5`：脚本起首 `command -v rg` 前置校验。
- **改动摘要**：CI 不具备 rg 时明确失败而非静默 PASS。

### MAJOR（用户提示）— rg 豁免清单完整

- **修订位置**：
  - `3-tech-design.md §7.5` `check-grep-fonts.sh`：完整列出 `--glob '!*.html' --glob '!*.bak' --glob '!node_modules' --glob '!.next' --glob '!out' --glob '!archive' --glob '!CHANGELOG.md'`。
  - `1-requirements.md` AC-2.1：同步列出同一套豁免 + 补说「`.cage/tasks/**` 因不在扫描路径内天然豁免，无需额外 --glob」。
- **改动摘要**：AC 与脚本豁免清单 100% 对齐。

### MAJOR（用户提示）— `.font-heading` 处理策略显式化（critic m5）

- **修订位置**：
  - `1-requirements.md` FR-4：明确「`.font-heading` 的 9 个 `.tsx` 消费方将改走 Tailwind utility 的默认行为——Tailwind v4 在无对应 `--font-heading` 定义时，`font-heading` 不会产出 utility，故这 9 处会退化为不设置 `font-family`，从 body 继承 `var(--font-u)` → `var(--font-sans)` → Inter，与原行为等价」。
  - `3-tech-design.md §模块 2.5`：同样说明。
  - `task.json` t7a 标题含"删除 .font-sans/.font-mono/.font-heading 类覆写"。
- **改动摘要**：行为等价性论证充分，9 个消费文件零改动、零视觉差异。

---

## MINOR 修订对应表

| # | 问题 | 修订位置 | 改动摘要 |
|---|---|---|---|
| m1 | 数字口径不一（96 vs 97，41 vs 44 vs 45） | `1-requirements.md` 开头「口径统一」段落 | 已核实并统一：`var(--font-d/u)` **96 处**，使用 `font-sans/mono/heading` 的 `.tsx` 文件 **45 个**（含 `font-heading` 9 个） |
| m2 | tokens.test.ts 缺「字段被注释掉」sanity | AC-1.14 + `3-tech-design.md §7.2` 的 `--font-sans / --font-mono definitions are not commented out` | 新增断言 |
| m3 | t5 验收加 `--reporter=verbose` | `3-tech-design.md §7.6` + `4-tasks.md` t5 验收命令 + `package.json` script | `test:fonts: "vitest run tests/fonts --reporter=verbose"` |
| m4 | AC-2.1 与 check-grep-fonts.sh glob 集合不一致 | `1-requirements.md` AC-2.1 + `3-tech-design.md §7.5` | 两边 glob 列表同步为最完整版本 |
| m5 | `PingFang SC` 断言用 word-boundary | AC-1.3 / AC-1.4 在 `tokens.test.ts` 使用 `/\bPingFang\s+SC\b/` / `/\bSarasa\s+Mono\s+SC\b/` | 防下划线变体 |
| m6 | `src/tinohelm/backtest/tearsheet.py` 的 IBM Plex 引用属后端 | `1-requirements.md` 范围边界「明确排除」条目 | 显式声明非本任务范围 |
| m7 | 静态导出场景 verify-build-fonts.mjs 适配 | `3-tech-design.md §7.4` 的 `pickRoot()` 函数 | 支持 `.next/` 与 `out/_next/` 两个路径 |
| m8 | FR-5 与 NFR-1 语义区分 | `1-requirements.md` FR-5 开头段 + NFR-1 | 显式区分"硬编码字面量清理（FR-5）" vs "className 消费文件零改动（NFR-1）" |
| m9 | CLAUDE.md 新文本写死 | `3-tech-design.md §模块 6` + `4-tasks.md` t12 / t13 给出 before/after 完整 diff | 避免 executor 发挥 |
| m10 | FR×AC×subtask 追溯矩阵 | `4-tasks.md` 末尾「覆盖矩阵（FR × AC × Subtask）」精确到子断言 | 每个 AC 可追溯至少一个 subtask |

---

## Open Questions（已在修订中处理 / 显式记录）

| # (critic) | 处理 |
|---|---|
| OQ1 Source Serif 4 是否真的加载 | 已决定**不加载**（见 C5 修订） |
| OQ2 chartTheme.ts 的 `var(--font-d)` 是否迁移 | 列入 NFR-1 豁免域，属技术债；本任务不处理，在 `3-tech-design.md` 「不改动（明确豁免）」中声明 |
| OQ3 删除 `.font-sans/.font-mono/.font-heading` 类覆写是否破坏非-Tailwind 上下文 | explorer 未发现 email 模板 / 第三方嵌入等非-Tailwind 上下文；`.tsx` 消费方的 `className="font-sans"` 由 Tailwind v4 自动产出 utility 覆盖，等价性论证已在 FR-4 与 §模块 2.5 给出 |
| OQ4 回滚策略的 commit range | 本任务若作为单次 PR 的多个 commit，回滚 `git revert <merge-commit>` 即可；若 executor 以 atomic commit 提交（推荐），单 commit revert |
| OQ5 offline build 场景 | `2-research.md §9` 显式声明 CI 需外网访问 Google；若无外网应提前告知用户（不在 AC 内） |

---

## 验证清单（R2 审查员复核用）

- [x] `1-requirements.md` 新增 FR-4，AC-1 从 11 断言扩展到 15 断言
- [x] `2-research.md` Source Serif 4 从"加载"改为"明确不加载"；新增 §9
- [x] `3-tech-design.md` 新增 §模块 2.5；§7.1 postcss AST；§7.4 CSS 文本扫描；§7.5 git rev-parse + rg 前置；§模块 6 写死 before/after diff
- [x] `4-tasks.md` 新增 t7a；t5 depends_on 补全；t10/t11 显式计数命令；覆盖矩阵精确到子断言
- [x] `task.json` 17 个 subtasks，全部 `depends_on` 正确填充；parallel_groups 重算为 5 波
- [x] 所有 R1 BLOCKER / CRITICAL / MAJOR 问题在各文档中有对应改动
- [x] 可追溯性：每个 AC 能追溯到至少一个 subtask（见 `4-tasks.md` 覆盖矩阵）
- [x] 无"手动验证"item（所有 AC 均为 shell/vitest 可执行断言）
