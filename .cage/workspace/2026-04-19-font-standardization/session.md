# Session: 前端字体 QDS 标准化

**日期**: 2026-04-18 → 2026-04-19 | **分支**: `main` | **任务**: `2026-04-18-font-standardization`

## 摘要

完成前端字体 QDS 标准化任务的 P-E-V 全流程：Plan 阶段经 3 轮 Architect+Critic 审查（R1 双 REVISE → R2 双 APPROVE → R3 修复 W3 并行竞态），Execute 阶段 7 波串行执行 17/17 子任务完成，Verify 阶段通过 Quality Gates + Verifier PASS + Code-Simplifier PASS，Code-Reviewer 的 REQUEST CHANGES 核实后所有真实 actionable 项均已闭环。将前端从 IBM Plex 迁移到 `next/font/google` 自托管的 Inter + JetBrains Mono，新增 15 个 vitest 断言 + rg 仓库合规脚本。

## P 阶段 (Plan)

### 模糊度路径 → 本体论收敛

Interview 阶段 2 轮苏格拉底提问将模糊度从 29% 降至 6.9%，收敛到 8 个核心实体（稳定性 87.5% → 100%）：

1. **FontToken** — `--font-sans` / `--font-mono` + legacy `--font-d` / `--font-u` 别名
2. **FontFace** — Inter / JetBrains Mono（弃 IBM Plex；明确不加载 Source Serif 4）
3. **FontLoader** — `next/font/google` 自托管（替代 `<link>` CDN）
4. **FallbackChain** — 中文优先：HarmonyOS Sans SC → PingFang SC → Source Han Sans SC；Mono 用 Sarasa Mono SC
5. **OpenTypeFeatures** — body 启用 `cv11` / `ss01` / `ss03`（Inter 风格集）
6. **Consumer** — 44 个 `.tsx` 文件 + 97+ 处 `var(--font-[du])` 引用（零改动）
7. **Docs** — `src/web/CLAUDE.md` + 根 `CLAUDE.md`
8. **VerificationMethod** — vitest + rg + `next build` + woff2 枚举

### 17 子任务 DAG + 审查历程

- **R1 双 REVISE**：Architect（2 CRITICAL + 6 MAJOR + 6 MINOR）+ Critic（5 BLOCKER + 7 MAJOR + 8 MINOR）。核心问题：task.json `depends_on` 字段为 null（原用 `deps`）、`extractBlock('body')` 正则误匹配 `html, body`、`/inter.*\.woff2$/i` 在 Next.js 16 hash 文件名下必然失败、`rg -c | xargs test N -le` 零匹配静默 PASS、Source Serif 4 跳过分支违反 100% 自动化 RULE
- **R2 双 APPROVE**：Planner 修订后两位审查员均 APPROVE，但 Critic 留下 1 MAJOR——W3 并行组 `[t7a, t8, t9]` 同文件并行写 `globals.css`，违反用户 MEMORY `feedback-parallel-agent-race.md`
- **R3**：Planner 重排依赖链 `t7 → t7a → t8 → t9` 强制线性化，parallel_groups 从 5 波展开为 7 波，每波同文件写任务仅 1 个

### 技术方案收敛

- Token 反指方案：新 `--font-sans/-mono` 权威，legacy `--font-d/-u` 反指新 token → 保护 96+ 处现有 `var(--font-[du])` 引用 + 41 个消费 `.tsx` 零改动
- postcss AST 替代文本正则：精确 selector 匹配避免 `html, body {}` 误判
- CSS 文本扫描替代 woff2 文件名正则：Next.js 16 font loader 生成纯 hash 文件名，正则失效；改扫 `@font-face font-family` 字面量更稳健
- Source Serif 4 明确不加载 + 反向断言"不存在"（绕过 100% 自动化 RULE 的"否则跳过"陷阱）

## E 阶段 (Execute)

### 7 波次 17/17 子任务完成

| 波次 | 子任务 | 状态 |
|---|---|---|
| W1 | t1, t6, t10, t11, t12, t13, t14, t15（拆 5+3 批） | 全 done |
| W2 | t3, t4, t7, t16（4 并行） | 全 done |
| W3 | t7a | done |
| W4 | t8 | done |
| W5 | t9（含 `next build` smoke） | done |
| W6 | t5（vitest 15 断言） | done |
| W7 | t17（`npm run verify:fonts:all` 全链） | done |

### cage:executor 失败与主 agent 兜底

- W1 第一批中 `cage:executor` 派遣 t6 时返回 `API Error: Extra usage is required for 1M context`，其他并行 executor（t1/t10/t11/t12）正常完成
- 第 2 次重试 t6 仍然 1M context 错误
- **结论**：cage:executor 在 1M context 模式下启动失败。主 agent 切换为直接使用 Edit/Write/Bash 工具完成 t6 + 后续所有任务，17 个 bash AC 命令全部自检通过

### 过程附带修复（pre-existing bug）

`next build` smoke 被 2 个 pre-existing Fragment import 缺失阻塞：
- `src/web/src/app/analytics/page.tsx` — `<Fragment>` 已使用但未 import
- `src/web/src/app/optimization/page.tsx` — 同类

最小侵入式补 `Fragment` import 使 AC-3.1 能通过。

## V 阶段 (Verify)

本次共执行 1 轮 verify。

### Quality Gates — PASS

- `next build` — exit 0（16/16 静态页）
- `npm run verify:fonts:all` — 全链绿灯
  - `test:fonts`: 15/15 vitest 断言
  - `check:grep:fonts`: 4/4（无 IBM Plex 残留、无 `fonts.googleapis.com` 直引、双端 CLAUDE.md 同步）
  - `next build`: exit 0
  - `verify:build:fonts`: Inter + JetBrains Mono `@font-face` present，Source Serif 4 absent，13 woff2
- `npm run lint` — 48 errors + 43 warnings，**全部 pre-existing**（本任务改动文件只含已知 pre-existing `set-state-in-effect` / unused-imports 警告，**未引入任何新错误**）

### Phase 2

- **Verifier: PASS**（高置信度）— FR-1~FR-7 + NFR-1~NFR-3 逐条 VERIFIED；legacy alias 实证保护 132 处 `var(--font-[du])` 消费方零破坏；16 静态页全绿
- **Code-Reviewer: REQUEST CHANGES** — 1 HIGH + 2 MEDIUM + 1 LOW。经核实：
  - **HIGH-1**（新文件 untracked）：非代码 bug，属 `/cage:commit` 的 `git add` 阶段事项
  - **MEDIUM-1**（`check-grep-fonts.sh` glob 豁免前缀无效）：**误判**，实测 `rg --glob '!src/web/scripts/...'` 豁免生效（Ripgrep `--glob` 相对 cwd 非 search path）
  - **MEDIUM-2**（`@theme inline` 自引用易误解）：**已在 verify 中就地修复**，加注释说明 Tailwind v4 编译期语义 + 无自引用循环
  - **LOW-1**（Fragment import 超 FR-5 边界）：Reviewer 自认可为合理 latent bug 修复

### Phase 3

- **Code-Simplifier: PASS** — 无死代码、无过度抽象、无冗余；所有抽象层次均有明确用途

### 综合判定

全 PASS（所有真实 actionable 项已闭环）。`kickback-r1.md` 记录完整对照分析，指明无 exec 级 kickback 必要。

## 变更清单

### 修改文件

- `src/web/src/app/layout.tsx` — 引入 `next/font/google`（Inter + JetBrains_Mono，不含 Source_Serif_4），移除 `<link>` CDN，注入 CSS variable 到 `<html>`
- `src/web/src/app/globals.css`：
  - `:root` 新增 `--font-sans` / `--font-mono`（含中文 fallback 链），legacy `--font-d` / `--font-u` 反指新 token
  - `@layer base` 删除 `.font-sans` / `.font-mono` / `.font-heading` 类覆写
  - `body` 追加 `font-feature-settings: 'cv11', 'ss01', 'ss03'`
  - `@theme inline` 改为 identity 转发 `--font-sans: var(--font-sans)` + 注释说明
- `src/web/src/app/analytics/page.tsx` — 2 处 `fontFamily: "IBM Plex Mono"` → `"var(--font-mono)"` + 补 `Fragment` import
- `src/web/src/app/trading/components/tabs/OverviewTab.tsx` — 4 处 `fontFamily` 字面量替换
- `src/web/src/app/optimization/page.tsx` — 补 `Fragment` import（pre-existing bug）
- `src/web/CLAUDE.md` — L141 字体声明同步
- 根 `CLAUDE.md` — L268 字体声明同步
- `src/web/package.json` — 新增 `vitest` + `postcss` devDep + 4 个 npm scripts
- `src/web/package-lock.json` — npm install 更新

### 新增文件（untracked，待 commit add）

- `src/web/vitest.config.ts`
- `src/web/tests/fonts/fixtures/parse-css.ts` — postcss AST fixture（5 个导出函数）
- `src/web/tests/fonts/tokens.test.ts` — 15 个 vitest 断言
- `src/web/scripts/verify-build-fonts.mjs` — 扫 `.next/static/css/chunks/*.css` 的 `@font-face` 字面量（Next.js 16 / Turbopack 可能无引号）
- `src/web/scripts/check-grep-fonts.sh` — rg 合规脚本（锚定仓库根 + 完整豁免清单）

## 关键决策

1. **Token 反指方案**：新 `--font-sans/-mono` 权威，legacy `--font-d/-u` 反指新 token。代价：2 层解析链；收益：96 处 `var(--font-[du])` + 41 个 `.tsx` 消费文件零改动
2. **`next/font/google` 自托管替代 CDN**：编译期下载到 `.next/static/media/`，国内零阻塞；自动 size-adjust 零布局偏移
3. **不加载 Source Serif 4**：QDS skill 明示"不主动用"；"可选加载 + 否则跳过"违反 100% 自动化 RULE，改为强制反向断言"不存在"
4. **postcss AST 替代正则解析**：精确 selector 匹配避免 `html, body {}` 误判
5. **CSS 文本扫描替代 woff2 文件名正则**：Next.js 16 font loader 生成纯 hash 文件名，改扫 `@font-face font-family` 字面量
6. **W3 强制线性化（t7a → t8 → t9）**：3 个任务虽改动 `globals.css` 不同区段，但都走 Read-Edit，事前规避 parallel agent race（用户 MEMORY `feedback-parallel-agent-race.md`）

## 经验沉淀

1. **`--glob` 在 rg 中相对 cwd 非 search path**：豁免写 `'!src/web/scripts/...'`（repo-relative）才生效；写 `'!scripts/...'`（search-path-relative）在 `--glob '!src/web/...'` 上下文中反而失效
2. **Next.js 16 Turbopack 压缩 CSS 会剥离 `font-family` 引号**：`/@font-face .* font-family: ['"]?([^'";,}]+)/` 正则必须兼容两种形式
3. **嵌套 `.git` 干扰 `git rev-parse --show-toplevel`**：`src/web/` 下有 shadcn 脚手架留下的 `.git` 子目录，脚本必须用自身位置（`${BASH_SOURCE[0]}`）锚定仓库根
4. **Tailwind v4 `@theme inline { --font-sans: var(--font-sans); }` 不自引用**：`@theme inline` 是编译期 directive，`var()` 在运行时解析到 `:root`，不回到 `@theme` 命名空间——但视觉易误解，必须加注释
5. **`next/font/google` 不支持 stylistic sets（cv11/ss01/ss03）**：这些是 OpenType 特性，必须通过 CSS `font-feature-settings` 在 body/html 运行时启用
6. **cage:executor 在 1M context 模式下启动失败**：主 agent 直接使用 Edit/Write/Bash 完成子任务是可行的兜底路径，17 个 AC bash 命令自检提供新鲜证据

## 遗留事项

- **新增文件 untracked**（`src/web/{tests,scripts,vitest.config.ts}`）— 待 `/cage:commit` 的 `git add` 处理

## 下一步

运行 `/cage:commit` 提交变更。建议 commit message：
```
feat(frontend): standardize fonts to QDS (Inter + JetBrains Mono via next/font/google)

- Replace IBM Plex CDN link with self-hosted next/font/google
- Add authoritative --font-sans/-mono tokens with CJK fallback chain
- Reverse-point legacy --font-d/-u aliases to new tokens (protects 96+ refs)
- Enable Inter OpenType features cv11/ss01/ss03 on body
- Clean 6 hardcoded IBM Plex literals from chart ticks
- Sync font declarations in src/web/CLAUDE.md and root CLAUDE.md
- Add vitest + postcss AST + rg compliance + build smoke verification chain
- Fix 2 pre-existing Fragment import bugs that blocked build

Task: 2026-04-18-font-standardization (17/17 subtasks, Plan R2 APPROVE + R3 race fix)
```

## 关键指标

| 指标 | 值 |
|---|---|
| 子任务完成率 | 17/17 = 100% |
| Vitest 断言 | 15/15 通过 |
| Plan 审查轮次 | 3 轮（R1 REVISE → R2 APPROVE → R3 race 修复） |
| Verify 审查 | Verifier PASS / Code-Reviewer REQUEST CHANGES → 等价 PASS / Code-Simplifier PASS |
| 构建 | `next build` exit 0，16/16 静态页，13 woff2 产物 |
| 仓库合规 | IBM Plex 零残留，`fonts.googleapis.com` 零直引 |
| 本任务引入新 lint 错误 | 0 |
