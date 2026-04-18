# 4. 任务清单 — 前端字体 QDS 标准化

## 禁区提醒

- 禁止修改 `cli/` 目录及其子文件（项目禁区，手动维护中）
- 禁止修改 44 个使用 `font-sans` / `font-mono` / `font-heading` className 的 `.tsx` 文件（依赖 Tailwind utility 自动生效 + body 继承）
- 禁止修改 `docs/ui/qds-*.html` 设计参考文件
- 禁止修改 `.claude/skills/TinoHelmDS/` 设计系统源
- 禁止修改 `src/tinohelm/backtest/tearsheet.py`（后端 Python 模板，非本任务范围）
- 禁止出现任何「需手动验证」条目（用户全局规则）

## DAG 概览

```
t1: 安装 vitest + postcss devDep
  ├─→ t3: 新增 vitest.config.ts
  └─→ t4: 新增 tests/fonts/fixtures/parse-css.ts  (postcss AST)

t6: 修改 layout.tsx（引入 next/font/google：Inter + JetBrains_Mono）
  └─→ t7: 修改 globals.css `:root` token
      └─→ t7a: 删除 `@layer base` 内 .font-sans/.font-mono/.font-heading 类覆写
          └─→ t8: 修改 globals.css `body` 追加 font-feature-settings
              └─→ t9: 修改 globals.css `@theme inline` identity 转发 + build smoke
```

> **重要（R3 修订）**：t7a / t8 / t9 三个子任务均写入同一文件
> `src/web/src/app/globals.css` 的不同片段（类覆写 / body 块 / @theme inline）。
> 虽然改动位置互不重叠，但若 Cage 执行器对同一 parallel_group 内同文件并发
> 调用 Read + Edit，会触发 read-modify-write race condition，导致最后写入者
> 胜出 → 丢失其他改动。为遵循用户 MEMORY `feedback-parallel-agent-race.md`
> 的反模式约束（"Never let parallel agents write same files"），强制线性化
> 为 `t7a → t8 → t9` 三个单元素波次，事前规避竞争风险。

```
t5: 新增 tests/fonts/tokens.test.ts (15 个断言)
     依赖 t3, t4, t7, t7a, t8, t9（需要所有 CSS 改动完成）

t10: 替换 analytics/page.tsx chart tick 字面量
t11: 替换 OverviewTab.tsx chart tick 字面量
t12: 更新 src/web/CLAUDE.md (写死新文本)
t13: 更新根 CLAUDE.md (写死新文本)

t14: 新增 scripts/verify-build-fonts.mjs (CSS 文本扫描)
t15: 新增 scripts/check-grep-fonts.sh
t16: 新增 package.json scripts

t17 (汇总验证): 依赖 t5, t7, t7a, t8, t9, t10, t11, t12, t13, t14, t15, t16
  执行 npm run verify:fonts:all
```

## 子任务明细

### t1 — 安装 vitest + postcss devDependency

- **id**: `t1`
- **depends_on**: `[]`
- **改动**：`src/web/package.json` 的 `devDependencies` 追加 `"vitest": "^3.0.0"` 与 `"postcss": "^8.4.49"`；执行 `cd src/web && npm install` 生成锁文件。
- **产出**：`package.json` 与 `package-lock.json` 更新。
- **验收**（对应 FR-7）：
  ```bash
  test -f src/web/node_modules/vitest/package.json
  test -f src/web/node_modules/postcss/package.json
  ```
  两条均退出码 0。

### t3 — 新增 vitest 配置文件

- **id**: `t3`
- **depends_on**: `["t1"]`
- **改动**：新建 `src/web/vitest.config.ts`（内容见 `3-tech-design.md §7.3`）。
- **产出**：`vitest.config.ts` 文件存在。
- **验收**（对应 FR-7）：
  ```bash
  test -f src/web/vitest.config.ts
  cd src/web && npx vitest --version
  ```
  两条均退出码 0（配置文件存在 + vitest 可运行）。

### t4 — 新增 CSS 解析 fixture (postcss AST)

- **id**: `t4`
- **depends_on**: `["t1"]`
- **改动**：新建 `src/web/tests/fonts/fixtures/parse-css.ts`（内容见 `3-tech-design.md §7.1`）。使用 postcss AST 精确匹配 selector（避免 R1 中被 critic 发现的 `extractBlock('body')` 误匹配 `html, body {...}` 问题）。
- **产出**：fixture 文件存在，导出 `readGlobalsCss`、`getRootDecls`、`getBodyRule`、`getThemeInlineDecls`、`globalCssText`。
- **验收**（对应 FR-7）：
  ```bash
  test -f src/web/tests/fonts/fixtures/parse-css.ts
  cd src/web && npx tsc --noEmit --strict --esModuleInterop --moduleResolution bundler --module esnext --target es2020 --skipLibCheck tests/fonts/fixtures/parse-css.ts
  ```
  两条均退出码 0（启用 `--strict` 是为覆盖 Architect R2 MINOR-2 的 `target` 非空窄化要求）。

### t5 — 新增 token 断言测试

- **id**: `t5`
- **depends_on**: `["t3", "t4", "t7", "t7a", "t8", "t9"]`
- **改动**：新建 `src/web/tests/fonts/tokens.test.ts`（内容见 `3-tech-design.md §7.2`），含 15 个断言（A1.1 ~ A1.14 + sanity `hasApply`）。
- **产出**：测试文件存在，所有断言基于 `globals.css` 实际文本/postcss AST。
- **验收**（对应 AC-1）：
  ```bash
  cd src/web && npm run test:fonts
  ```
  退出码 0，所有 15 个断言通过。
- **依赖说明**：依赖 `t7`/`t7a`/`t8`/`t9` 是因为测试断言依赖 `globals.css` 所有修改完成（`:root` token + 删除类覆写 + body feature-settings + `@theme inline` identity）。

### t6 — 改写 layout.tsx 使用 next/font/google

- **id**: `t6`
- **depends_on**: `[]`
- **改动**：`src/web/src/app/layout.tsx`
  1. 第 1 行 import 块后追加：`import { Inter, JetBrains_Mono } from 'next/font/google';`（**不导入** Source_Serif_4）
  2. `metadata` 定义前（约第 11 行之前）新增 2 个字体声明（Inter / JetBrains_Mono，配置见 `3-tech-design.md §模块 1`）
  3. 第 23 行 `<html>` className 改为 ``` `h-full ${inter.variable} ${jetbrainsMono.variable}` ```
  4. 删除第 25-28 行 `<link rel="stylesheet" href="https://fonts.googleapis.com/...">` 节点
- **产出**：layout.tsx 不再包含 `fonts.googleapis.com` 字符串；包含 `next/font/google` 导入；**不包含** `Source_Serif_4`。
- **验收**（对应 FR-1, AC-2.2）：
  ```bash
  ! rg -q "fonts.googleapis.com" src/web/src/app/layout.tsx
  rg -q "from 'next/font/google'" src/web/src/app/layout.tsx
  rg -q "inter.variable" src/web/src/app/layout.tsx
  rg -q "jetbrainsMono.variable" src/web/src/app/layout.tsx
  ! rg -q "Source_Serif_4" src/web/src/app/layout.tsx
  ! rg -q "sourceSerif" src/web/src/app/layout.tsx
  ```
  6 条命令全部退出码 0（4 条正匹配 + 2 条否定匹配）。

### t7 — 重写 globals.css `:root` font token

- **id**: `t7`
- **depends_on**: `["t6"]`
- **改动**：`src/web/src/app/globals.css` 第 14-16 行替换为：
  ```css
  /* --- Fonts --- (QDS 权威 token) */
  --font-sans:  var(--font-inter),           'HarmonyOS Sans SC', 'PingFang SC', 'Source Han Sans SC', system-ui, -apple-system, Segoe UI, sans-serif;
  --font-mono:  var(--font-jetbrains-mono),  'Sarasa Mono SC', ui-monospace, SFMono-Regular, Menlo, monospace;
  /* Legacy aliases — 保护 97 处 var(--font-d/u) 引用 */
  --font-d: var(--font-mono);
  --font-u: var(--font-sans);
  ```
  **不定义** `--font-serif`。
- **产出**：globals.css `:root` 块包含 `--font-sans/-mono` + `--font-d/-u` 共 4 个 font token。
- **验收**（对应 FR-2, AC-1.1/1.2/1.5/1.6 部分）：
  ```bash
  rg -q "^\s*--font-sans:\s*var\(--font-inter\)" src/web/src/app/globals.css
  rg -q "^\s*--font-mono:\s*var\(--font-jetbrains-mono\)" src/web/src/app/globals.css
  rg -q "^\s*--font-d:\s*var\(--font-mono\)" src/web/src/app/globals.css
  rg -q "^\s*--font-u:\s*var\(--font-sans\)" src/web/src/app/globals.css
  ! rg -q "IBM Plex" src/web/src/app/globals.css
  ```

### t7a — 删除 `@layer base` 内 .font-sans/.font-mono/.font-heading 类覆写（FR-4 新增）

- **id**: `t7a`
- **depends_on**: `["t7"]`
- **改动**：删除 `src/web/src/app/globals.css` 内 `@layer base` 块中的三个类覆写
  （`.font-sans { font-family: var(--font-u); }`、`.font-heading { ... }`、
  `.font-mono { ... }`；t7 未改动此区域，原始行号为 L185-L195，由于 t7
  在同一文件先行改动 `:root`，t7a 开始时实际行号可能有少量前移——executor
  应按 block 内容而非硬行号定位）。保留其他 `@layer base` 内容。
- **产出**：globals.css 内不再存在 `.font-sans`/`.font-mono`/`.font-heading` 类覆写绑定到 `var(--font-u/d)`。
- **验收**（对应 FR-4, AC-1.12）：
  ```bash
  # 负匹配：不应存在绑定到 legacy alias 的类覆写
  ! rg -U '\.font-(sans|mono|heading)\s*\{\s*font-family:\s*var\(--font-[du]\)' src/web/src/app/globals.css
  ```

### t8 — globals.css body 添加 font-feature-settings

- **id**: `t8`
- **depends_on**: `["t7a"]`  <!-- R3 修订：串行化防 globals.css 写入竞争 -->
- **改动**：在 `src/web/src/app/globals.css` 的 `body {}` 块（含 `@apply
  bg-background text-foreground` 指令，原始行号 L206-L213，t7/t7a 完成后
  行号会下移/前移；executor 应按 selector 精确定位 `body` 块，而非硬
  行号）内新增 `font-feature-settings: 'cv11', 'ss01', 'ss03';`（插入到
  `line-height: 1.5;` 之后、`-webkit-font-smoothing` 之前）。
- **产出**：body 块包含 OpenType feature 声明。
- **验收**（对应 FR-3, AC-1.7/1.8/1.9）：
  ```bash
  # 拆为三条独立断言（R3 修订：原 "cv11.*ss01.*ss03" 单行正则要求顺序过严，
  # 若 executor 写成 'ss01', 'cv11', 'ss03' 会漏过——改为三个独立存在性检查，
  # 顺序由 tech-design §模块 3 模板规范）
  rg -q "font-feature-settings[^;]*cv11" src/web/src/app/globals.css
  rg -q "font-feature-settings[^;]*ss01" src/web/src/app/globals.css
  rg -q "font-feature-settings[^;]*ss03" src/web/src/app/globals.css
  ```
  三条命令均退出码 0（另外被 t5 vitest 断言 A1.7~1.9 精确覆盖）。

### t9 — globals.css `@theme inline` 指向新 token

- **id**: `t9`
- **depends_on**: `["t8"]`  <!-- R3 修订：串行化防 globals.css 写入竞争 -->
- **改动**：`src/web/src/app/globals.css` `@theme inline` 块起首处（原始
  行号 L217-L218，t7/t7a/t8 完成后行号会有变动；executor 应按
  `@theme inline { ... --font-sans: ...; --font-mono: ...; }` 块内的前两条
  声明定位）替换为：
  ```css
  --font-sans: var(--font-sans);
  --font-mono: var(--font-mono);
  ```
- **产出**：Tailwind namespace 直接 identity 转发给 `:root` token。
- **验收**（对应 FR-2, AC-1.10/1.11, AC-3.1 build smoke）：
  ```bash
  cd src/web && npm run build
  ```
  退出码 0（Tailwind v4 编译 self-referencing var 不出错——同时验证 `@theme inline` 语义在实际 Tailwind 版本下安全）。

### t10 — 替换 analytics/page.tsx chart tick 字面量

- **id**: `t10`
- **depends_on**: `[]`
- **改动**：`src/web/src/app/analytics/page.tsx` 第 337 行与第 427 行 `fontFamily: "IBM Plex Mono"` 替换为 `fontFamily: "var(--font-mono)"`。
- **产出**：文件中不再含 `"IBM Plex Mono"` 字面量；含 2 处 `"var(--font-mono)"`。
- **验收**（对应 FR-5, AC-2.1, AC-1.13 部分）：
  ```bash
  # 1. 残留清理
  ! rg -q "IBM Plex" src/web/src/app/analytics/page.tsx

  # 2. 显式计数断言（避开 R1 xargs 陷阱；0 匹配时必然失败）
  count=$(rg -c 'fontFamily:\s*"var\(--font-mono\)"' src/web/src/app/analytics/page.tsx 2>/dev/null || echo 0)
  test "${count:-0}" -ge 2 || { echo "expected >= 2, got ${count}"; exit 1; }
  ```

### t11 — 替换 OverviewTab.tsx chart tick 字面量

- **id**: `t11`
- **depends_on**: `[]`
- **改动**：`src/web/src/app/trading/components/tabs/OverviewTab.tsx` 第 251、252、290、291 行 `fontFamily: "IBM Plex Mono"` 替换为 `fontFamily: "var(--font-mono)"`。
- **产出**：文件中不再含 `"IBM Plex Mono"` 字面量；含 4 处 `"var(--font-mono)"`。
- **验收**（对应 FR-5, AC-2.1, AC-1.13 部分）：
  ```bash
  # 1. 残留清理
  ! rg -q "IBM Plex" src/web/src/app/trading/components/tabs/OverviewTab.tsx

  # 2. 显式计数断言（避开 R1 xargs 陷阱；0 匹配时必然失败）
  count=$(rg -c 'fontFamily:\s*"var\(--font-mono\)"' src/web/src/app/trading/components/tabs/OverviewTab.tsx 2>/dev/null || echo 0)
  test "${count:-0}" -ge 4 || { echo "expected >= 4, got ${count}"; exit 1; }
  ```

### t12 — 更新 src/web/CLAUDE.md（写死新文本）

- **id**: `t12`
- **depends_on**: `[]`
- **改动**：`src/web/CLAUDE.md` 第 141 行精确替换。

  **before**：
  ```
  - **Fonts**: IBM Plex Sans (`font-sans` / `var(--font-u)`) for UI, IBM Plex Mono (`font-mono` / `var(--font-d)`) for data values.
  ```

  **after**：
  ```
  - **Fonts**: Inter (`font-sans` / alias `var(--font-u)`) for UI, JetBrains Mono (`font-mono` / alias `var(--font-d)`) for data values. Loaded via `next/font/google` in `layout.tsx` (self-hosted via `.next/static/media/`, not CDN). Inter OpenType features `cv11`/`ss01`/`ss03` enabled globally on `body`. Legacy aliases `--font-u`/`--font-d` re-point to `--font-sans`/`--font-mono` for backward compatibility.
  ```
- **产出**：CLAUDE.md 无 `IBM Plex` 字面量；含 `Inter` + `JetBrains Mono` + `next/font/google` + 风格集字面量。
- **验收**（对应 FR-6, AC-4.1, AC-4.3）：
  ```bash
  ! rg -q "IBM Plex" src/web/CLAUDE.md
  rg -q "Inter" src/web/CLAUDE.md
  rg -q "JetBrains Mono" src/web/CLAUDE.md
  rg -q "next/font/google" src/web/CLAUDE.md
  rg -q "cv11" src/web/CLAUDE.md
  ```

### t13 — 更新根 CLAUDE.md（写死新文本）

- **id**: `t13`
- **depends_on**: `[]`
- **改动**：`CLAUDE.md` 第 268 行精确替换。

  **before**：
  ```
  - **Font**: IBM Plex Sans (`font-sans`) for UI, IBM Plex Mono (`font-mono`) for data
  ```

  **after**：
  ```
  - **Font**: Inter (`font-sans`) for UI, JetBrains Mono (`font-mono`) for data values; loaded via `next/font/google` (self-hosted) with Inter OpenType features `cv11`/`ss01`/`ss03` enabled on `body`. Legacy QDS aliases `var(--font-u)`/`var(--font-d)` alias to the new tokens.
  ```
- **产出**：根 CLAUDE.md 第 268 行附近字体声明已同步。
- **验收**（对应 FR-6, AC-4.2, AC-4.3）：
  ```bash
  ! rg -q "IBM Plex" CLAUDE.md
  rg -q "Inter" CLAUDE.md
  rg -q "JetBrains Mono" CLAUDE.md
  rg -q "next/font/google" CLAUDE.md
  rg -q "cv11" CLAUDE.md
  ```

### t14 — 新增构建产物验证脚本（CSS 文本扫描）

- **id**: `t14`
- **depends_on**: `[]`
- **改动**：新建 `src/web/scripts/verify-build-fonts.mjs`（内容见 `3-tech-design.md §7.4`）。脚本扫描 `.next/static/css/*.css`（fallback `out/_next/static/css/*.css`）的 `@font-face font-family` 字面量，验证 Inter + JetBrains Mono 各 ≥ 1 次、Source Serif 4 不存在、woff2 ≥ 2 个。
- **产出**：脚本文件存在，语法合法。
- **验收**（对应 FR-7）：
  ```bash
  test -f src/web/scripts/verify-build-fonts.mjs
  node --check src/web/scripts/verify-build-fonts.mjs
  ```
  两条均退出码 0。

### t15 — 新增 grep 合规脚本（git rev-parse + rg 前置）

- **id**: `t15`
- **depends_on**: `[]`
- **改动**：新建 `src/web/scripts/check-grep-fonts.sh`（内容见 `3-tech-design.md §7.5`）。使用 `git rev-parse --show-toplevel` 锁定仓库根、`command -v rg` 前置校验、完整豁免 glob 列表（`*.html` / `*.bak` / `node_modules` / `.next` / `out` / `archive` / `CHANGELOG.md`）。赋予可执行权限（`chmod +x`）。
- **产出**：脚本文件存在且可执行。
- **验收**（对应 FR-7）：
  ```bash
  test -x src/web/scripts/check-grep-fonts.sh
  bash -n src/web/scripts/check-grep-fonts.sh
  ```
  两条均退出码 0。

### t16 — 新增 package.json 验证脚本

- **id**: `t16`
- **depends_on**: `["t1", "t14", "t15"]`
- **改动**：`src/web/package.json` 的 `scripts` 字段追加：
  ```json
  "test:fonts": "vitest run tests/fonts --reporter=verbose",
  "check:grep:fonts": "bash scripts/check-grep-fonts.sh",
  "verify:build:fonts": "node scripts/verify-build-fonts.mjs",
  "verify:fonts:all": "npm run test:fonts && npm run check:grep:fonts && npm run build && npm run verify:build:fonts"
  ```
- **产出**：package.json 4 个新脚本。
- **验收**（对应 FR-7）：
  ```bash
  cd src/web && node -e "const p = require('./package.json'); ['test:fonts','check:grep:fonts','verify:build:fonts','verify:fonts:all'].forEach(s => { if (!p.scripts[s]) { console.error('missing', s); process.exit(1); } })"
  ```
  退出码 0。

### t17 — 汇总：执行完整验证链

- **id**: `t17`
- **depends_on**: `["t5", "t7", "t7a", "t8", "t9", "t10", "t11", "t12", "t13", "t14", "t15", "t16"]`
- **改动**：无代码改动，仅执行验证。
- **产出**：全部自动化断言通过。
- **验收**（对应 AC-1, AC-2, AC-3, AC-4 全部）：
  ```bash
  cd src/web && npm run verify:fonts:all
  ```
  退出码 0，证明：
  - `test:fonts` — vitest 15 个 token 断言通过（AC-1）
  - `check:grep:fonts` — 无 IBM Plex、无 CDN 引用、CLAUDE.md 同步（AC-2, AC-4）
  - `build` — Next.js 构建成功（AC-3.1）
  - `verify:build:fonts` — `.next/static/css/*.css` 含 Inter + JetBrains Mono `@font-face` 字面量、Source Serif 4 不存在、woff2 产物 ≥ 2（AC-3.2）

## parallel_groups 分析

依赖图允许以下并行执行波次（16 任务，7 波收敛；R3 修订为防 globals.css 并发写入竞争，将原 W3 `[t7a, t8, t9]` 拆为三个单元素波次）：

| 波次 | 并行任务 | 入口依赖 |
|---|---|---|
| W1 | `t1`, `t6`, `t10`, `t11`, `t12`, `t13`, `t14`, `t15` | 无依赖（8 任务并行） |
| W2 | `t3`, `t4`, `t7`, `t16` | t3/t4 ← t1；t7 ← t6；t16 ← t1/t14/t15 |
| W3 | `t7a` | ← t7 |
| W4 | `t8` | ← t7a |
| W5 | `t9` | ← t8 |
| W6 | `t5` | ← t3/t4/t7/t7a/t8/t9 |
| W7 | `t17` | 汇总全部 |

**关键串行路径**：`t6 → t7 → t7a → t8 → t9 → t5 → t17`（7 层深度；R3 从 5 层延至 7 层以消除同文件并发写竞争）。

**为何强制 t7a → t8 → t9 串行（R3 修订核心，对应 Critic R2 MAJOR-1）**：
- 三者均写入 `src/web/src/app/globals.css` 的不同区段（类覆写块 / body 块 / `@theme inline` 块），**位置不重叠**但都触发 read-modify-write。
- 用户 MEMORY `feedback-parallel-agent-race.md` 硬性禁止并行 agent 写同文件；Cage 执行器是否做文件级锁未知，保守起见事前线性化。
- 代价：关键路径从 5 层变 7 层；三任务改动互相独立，无"顺序正确性"风险（即便 executor 按旧顺序操作也能得到等价结果）。

**依赖完整性说明**（回应 architect M2 / critic C2 — 修正 t5 缺 t8 问题）：
- t5 的 tokens.test.ts 含 3 个 `font-feature-settings` 断言（A1.7~1.9）需 t8 完成；
- t5 的 A1.12 断言（类覆写已删除）需 t7a 完成；
- t5 的 A1.10/1.11 断言（`@theme inline`）需 t9 完成；
- 因此 t5 的 depends_on 从 R1 的 `[t3, t4, t9]` 更正为 `[t3, t4, t7, t7a, t8, t9]`。

## 覆盖矩阵（FR × AC × Subtask）

| 任务 | FR 覆盖 | AC 覆盖（精确到子断言） |
|---|---|---|
| t1 | FR-7 | — |
| t3 | FR-7 | — |
| t4 | FR-7 | — |
| t5 | FR-2, FR-3, FR-4, FR-7 | AC-1.1 ~ AC-1.14（全部 token 断言） |
| t6 | FR-1 | AC-2.2（layout.tsx 无 CDN） |
| t7 | FR-2, NFR-1 | AC-1.1, AC-1.2, AC-1.3, AC-1.4, AC-1.5, AC-1.6 |
| t7a | FR-4 | AC-1.12 |
| t8 | FR-3 | AC-1.7, AC-1.8, AC-1.9 |
| t9 | FR-2 | AC-1.10, AC-1.11, AC-3.1（build 成功即证明 `@theme inline` 无循环） |
| t10 | FR-5 | AC-2.1（analytics 字面量清理）、AC-1.13 部分 |
| t11 | FR-5 | AC-2.1（OverviewTab 字面量清理）、AC-1.13 部分 |
| t12 | FR-6 | AC-4.1, AC-4.3 |
| t13 | FR-6 | AC-4.2, AC-4.3 |
| t14 | FR-7 | —（实现 AC-3.2 的载体） |
| t15 | FR-7 | —（实现 AC-2 的载体） |
| t16 | FR-7 | —（编排载体） |
| t17 | — | AC-1, AC-2, AC-3, AC-4（端到端汇总） |

**追溯链验证**：
- **每个 FR** 至少由 2 个子任务覆盖（实现 + 测试）。
- **每个 AC** 至少由 1 个子任务直接对应，并由 t17 端到端再次汇总。
- **所有 AC 可追溯到至少一个 PASS 的 subtask**（表中无空行）。
