# 1. 需求文档 — 前端字体 QDS 标准化

## 背景

TinoHelm 前端（`src/web/`）当前使用 IBM Plex Sans / IBM Plex Mono，通过 `<link>` 标签从 Google Fonts CDN 加载。但 `TinoHelmDS` 设计系统（`.claude/skills/TinoHelmDS/SKILL.md:25-31`）规定的权威字体为 **Inter + JetBrains Mono**（Source Serif 4 "备用，不主动用"，本任务明确不加载）。要求使用 `next/font/google` 自托管、启用 Inter 风格集（cv11/ss01/ss03）、配置中文 fallback 链。

本任务完成字体层面的「完整 QDS 对齐」，所有验证必须 100% 自动化（遵循用户全局规则：PR 验证条目不得包含手动验证）。

## 口径统一

本文档使用以下口径（已核实）：

- **globals.css 内 `var(--font-d)` / `var(--font-u)` 引用总数**：`rg -o 'var\(--font-[du]\)' src/web/src/app/globals.css | wc -l` = **97 处**。
- **使用 `font-sans` / `font-mono` / `font-heading` className 的 `.tsx` 文件数**：`rg -l 'font-(sans|mono|heading)' src/web/src --type tsx` = **44 个**。
- **已知字面量位置**：analytics/page.tsx L337, L427；OverviewTab.tsx L251/252/290/291（共 6 处 `"IBM Plex Mono"`）。

## 功能需求

### FR-1 字体资源切换（FontFace）

**变更前**：`src/web/src/app/layout.tsx:25-28` 通过 `<link>` 标签从 `fonts.googleapis.com` 加载 `IBM Plex Sans` + `IBM Plex Mono`。`src/web/src/app/globals.css:15-16` 定义 `--font-d: 'IBM Plex Mono'` / `--font-u: 'IBM Plex Sans'`。

**变更后**：
- `layout.tsx` 移除 `<link rel="stylesheet" href="https://fonts.googleapis.com/...">` 节点。
- `layout.tsx` 导入 `next/font/google` 的 `Inter`、`JetBrains_Mono`（**不导入 Source Serif 4**；参见下方范围边界）。
- 每个字体声明 `variable: '--font-inter' | '--font-jetbrains-mono'`，`display: 'swap'`，`subsets: ['latin']`。
- `<html>` 节点挂载 `className={`h-full ${inter.variable} ${jetbrainsMono.variable}`}`。

### FR-2 Token 分层重构（FontToken）

**globals.css `:root` 块**（第 13~17 行附近）引入两个新 token，保留两个 legacy 别名：

```css
:root {
  /* QDS 权威字体 token */
  --font-sans:  var(--font-inter),           'HarmonyOS Sans SC', 'PingFang SC', 'Source Han Sans SC', system-ui, -apple-system, Segoe UI, sans-serif;
  --font-mono:  var(--font-jetbrains-mono),  'Sarasa Mono SC', ui-monospace, SFMono-Regular, Menlo, monospace;

  /* Legacy 别名 — 保护 globals.css 内 97 处 var(--font-d/u) 引用零破坏 */
  --font-d: var(--font-mono);
  --font-u: var(--font-sans);
  ...
}
```

**`@theme inline` 块**（globals.css:216~218）调整为直接消费新 token：

```css
@theme inline {
  --font-sans: var(--font-sans);   /* Tailwind font-sans → 新 token */
  --font-mono: var(--font-mono);   /* Tailwind font-mono → 新 token */
  ...
}
```

### FR-3 Inter OpenType 特性启用（OpenTypeFeatures）

- `globals.css` 的 `@layer base { body { ... } }` 块（第 206~213 行）新增：
  ```css
  font-feature-settings: 'cv11', 'ss01', 'ss03';
  ```
- 说明：Next.js `next/font/google` 的 Inter loader **不支持**通过 JS 参数（`axes` 等）启用 OpenType stylistic sets；`cv11`/`ss01`/`ss03` 是 Inter 的 OpenType **风格集（Stylistic Set）**，必须在 CSS 端通过 `font-feature-settings` 声明才能生效。本任务选择在 `body {}` 全局声明，让所有子元素继承。
- `cv11` = 单层 `a`；`ss01` = 开口数字；`ss03` = 平终端 `g`。依据：`.claude/skills/TinoHelmDS/colors_and_type.css:183-185`。

### FR-4 `globals.css` `.font-sans` / `.font-mono` / `.font-heading` 类覆写清理

**变更前**：`src/web/src/app/globals.css:185-195` 存在三个类在 `@layer base` 内的覆写：

```css
@layer base {
  .font-sans { font-family: var(--font-u); }
  .font-heading { font-family: var(--font-u); }
  .font-mono { font-family: var(--font-d); }
}
```

这些覆写与 Tailwind v4 `@theme inline --font-sans/--font-mono` 产出的 utility 存在层叠冲突：手写规则源码顺序靠后胜出，消费方 `className="font-sans"` 实际命中手写覆写，额外绕一跳 legacy alias。

**变更后**：**删除**这三个 `@layer base` 内的类覆写块（第 185-195 行整块），**完全依赖 Tailwind v4 生成的 utility**（`.font-sans { font-family: var(--font-sans) }` 等）。`.font-heading` 的 8 个 `.tsx` 消费方（8 文件，经 `rg -l 'font-heading' src/web/src -g '*.tsx'` 核实：analytics/page.tsx、optimization/page.tsx、orders/page.tsx、watchlist/page.tsx、strategies/[name]/EditorClient.tsx、components/ui/dialog.tsx、components/ui/popover.tsx、components/ui/sheet.tsx）将改走 Tailwind utility 的默认行为——Tailwind v4 在无对应 `--font-heading` 定义时，`font-heading` 不会产出 utility，故这 8 处会退化为不设置 `font-family`，从 body 继承 `var(--font-u)` → `var(--font-sans)` → Inter。**这与原行为等价**（原类覆写值就是 `var(--font-u)`）。

> **注意**：此清理为本轮审查 BLOCKER M1/C1 修复，不再保留 legacy 类覆写。

### FR-5 硬编码 IBM Plex 字面量清理（Consumer — 字符串字面量）

此 FR 与 NFR-1「Tailwind className 消费方零改动」在语义上独立：本 FR 仅针对**硬编码的字符串字面量** `"IBM Plex Mono"`（出现在内联 style 对象内），与 Tailwind className 路径无关。

以下 2 个文件中 `fontFamily: "IBM Plex Mono"` 字符串字面量必须替换为 `fontFamily: "var(--font-mono)"`：

| 文件 | 行号 | 命中数 |
|---|---|---|
| `src/web/src/app/analytics/page.tsx` | 337, 427 | 2 |
| `src/web/src/app/trading/components/tabs/OverviewTab.tsx` | 251, 252, 290, 291 | 4 |

> **与 NFR-1 的区分**：44 个使用 `className="font-sans"` / `className="font-mono"` 的 `.tsx` 消费文件**零改动**（NFR-1）。本 FR 处理的是内联 style 对象里的字符串字面量（2 文件共 6 处），不属于"className 消费方"。

### FR-6 文档同步（Docs）

- `src/web/CLAUDE.md:141` 的 `IBM Plex Sans` / `IBM Plex Mono` 声明替换为 `Inter` / `JetBrains Mono`。
- 根 `CLAUDE.md:268` 的字体声明同步更新。
- 两处均需说明新 token (`--font-sans` / `--font-mono`) 与 legacy alias (`--font-u` / `--font-d`) 的对应关系。

**新文本**（写死以避免 executor 发挥，详见 `4-tasks.md` t12/t13 的 before/after diff）。

### FR-7 验证基础设施（VerificationMethod）

- 新增 `src/web/tests/fonts/` 目录，包含：
  - `tokens.test.ts` — vitest 静态断言 `globals.css` 中 token 值；
  - `fixtures/parse-css.ts` — 使用 **postcss AST** 解析 CSS 并提取目标 block / variable（不再使用脆弱的正则）。
- 新增 `src/web/scripts/verify-build-fonts.mjs` — 扫描 `.next/static/css/*.css` 中 `@font-face` 的 `font-family` 字面量并计数 woff2 文件。
- 新增 `src/web/scripts/check-grep-fonts.sh` — 以 `git rev-parse --show-toplevel` 定位仓库根、调用 `rg` 进行合规断言。
- 新增 `package.json` 脚本：`test:fonts`、`check:grep:fonts`、`verify:build:fonts`、`verify:fonts:all`。
- 安装 `vitest` + `postcss` 为 devDependency（`postcss` 已隐含在 `@tailwindcss/postcss` 依赖链中，仍显式加入避免 hoist 不稳定）。

## 非功能需求

### NFR-1 向后兼容

- `globals.css` 内 97 处 `var(--font-d)` / `var(--font-u)` 引用**零改动**生效（通过 legacy alias 自动转向新 token）。
- 44 个使用 `className="font-sans" / "font-mono" / "font-heading"` 的 `.tsx` 文件**零改动**生效（依赖 Tailwind utility 和 body 继承）。
- `src/web/src/lib/chartTheme.ts:14/23/30/36` 的 4 处 `fontFamily: "var(--font-d)"` 属于 legacy alias 保护域，**零改动**。

### NFR-2 性能

- 字体加载通过 `next/font/google` 自托管，禁用对 `fonts.googleapis.com` 的直接引用（避免国内 CDN 访问慢 + 布局偏移）。
- 不加载 Source Serif 4（QDS SKILL 原文"不主动用，留给长文档、证书类页面"，项目目前无此场景，本任务明确排除以简化产物）。
- 不加载 Noto Sans SC 等中文字体文件（目标用户 macOS/鸿蒙本地已有 PingFang SC / HarmonyOS Sans SC，打包收益 < 1-2 MB 成本）。

### NFR-3 CI 可验证

所有验收条件必须能通过 shell 命令或 vitest assertion 完成判断，不依赖人工目视检查。

## 验收标准（100% 自动化）

### AC-1 Token 静态断言 — vitest

在 `src/web/tests/fonts/tokens.test.ts` 中，通过 postcss 解析 `src/web/src/app/globals.css` 并断言：

| # | 断言 | 期望 |
|---|---|---|
| A1.1 | `--font-sans` 值首位字体族 | 匹配 `/^var\(--font-inter\)/` |
| A1.2 | `--font-mono` 值首位字体族 | 匹配 `/^var\(--font-jetbrains-mono\)/` |
| A1.3 | Sans fallback 链包含 PingFang SC | 匹配 `/\bPingFang\s+SC\b/` |
| A1.4 | Mono fallback 链包含 Sarasa Mono SC | 匹配 `/\bSarasa\s+Mono\s+SC\b/` |
| A1.5 | Legacy `--font-d` 指向 | 精确等于 `var(--font-mono)` |
| A1.6 | Legacy `--font-u` 指向 | 精确等于 `var(--font-sans)` |
| A1.7 | `body` 块 `font-feature-settings` 含 cv11 | `/font-feature-settings[^;]*cv11/` |
| A1.8 | `body` 块 `font-feature-settings` 含 ss01 | `/font-feature-settings[^;]*ss01/` |
| A1.9 | `body` 块 `font-feature-settings` 含 ss03 | `/font-feature-settings[^;]*ss03/` |
| A1.10 | `@theme inline` 块 `--font-sans` | 匹配 `/var\(--font-(sans|inter)\)/` |
| A1.11 | `@theme inline` 块 `--font-mono` | 匹配 `/var\(--font-(mono|jetbrains-mono)\)/` |
| A1.12 | globals.css 内**不存在** `.font-sans`/`.font-mono`/`.font-heading` 类覆写 | `/\.font-(sans\|mono\|heading)\s*\{[^}]*var\(--font-[du]\)/` 无命中（FR-4） |
| A1.13 | globals.css 内不存在 `IBM Plex` 字面量 | `/IBM Plex/` 无命中 |
| A1.14 | `--font-sans` / `--font-mono` 定义未被注释 | sanity：`/\/\*[\s\S]*?--font-sans[^*]*:[^;]*var/` 无命中 |
| A1.15 | body 块应包含 `@apply`（sanity）—确认解析到的是目标 body 而非 `html, body { height: 100% }` | `body` AST node 的 `raws.between` 与 `decls` 包含 `@apply` 指令 |

**执行**：`cd src/web && npm run test:fonts -- --reporter=verbose` 退出码 0。

### AC-2 仓库级 Grep 合规

所有命令由 `src/web/scripts/check-grep-fonts.sh` 从 `git rev-parse --show-toplevel` 定位仓库根并执行。脚本起首校验 `rg` 可用（`command -v rg`）。

```bash
# AC-2.1: src/web/ 内无 IBM Plex 字面量残留
# 豁免清单：docs/ui/qds-*.html、*.bak、node_modules、.next、out、CHANGELOG.md（若存在）、archive/**
rg -q "IBM Plex" src/web/ \
  --glob '!*.html' \
  --glob '!*.bak' \
  --glob '!node_modules' \
  --glob '!.next' \
  --glob '!out' \
  --glob '!archive' \
  --glob '!CHANGELOG.md'
# 期望：无命中（rg 退出码 1）

# AC-2.2: src/web/src 代码内不直连 Google Fonts CDN
rg -q "fonts.googleapis.com" src/web/src/
# 期望：无命中

# AC-2.3: 两处 CLAUDE.md 同步清理
rg -q "IBM Plex" src/web/CLAUDE.md CLAUDE.md
# 期望：无命中

# AC-2.4: .cage/tasks/**（interview.md、plan-review/**、1~4-*.md、revision-*.md）属 planning artifacts，完全豁免
# 机制：脚本 rg 的扫描路径只包含 src/web/、src/web/CLAUDE.md、(root) CLAUDE.md
# .cage/tasks/** 根本不在扫描范围内，因此无需额外 --glob 排除
```

### AC-3 构建与资源校验

```bash
cd src/web && npm run build                        # 退出码 0
cd src/web && node scripts/verify-build-fonts.mjs  # 退出码 0
```

`verify-build-fonts.mjs` 执行两层防御（详见 `3-tech-design.md §7.4`）：

**强证据（必须）**：
1. 扫描 `.next/static/css/*.css` 中 `@font-face` 条目，至少匹配到 `font-family: 'Inter'` 字面量一次。
2. 扫描 `.next/static/css/*.css` 中 `@font-face` 条目，至少匹配到 `font-family: 'JetBrains Mono'` 字面量一次。
3. `.next/static/media/**/*.woff2` 文件数 ≥ 2（Inter + JetBrains Mono，至少各 1）。

**强约束（必须不存在）**：
4. 扫描 `.next/static/css/*.css`，**不得**出现 `font-family: 'Source Serif 4'` 字面量（本任务明确不加载 Source Serif 4）。

> 静态导出（`next.config.ts: output: "export"`）也会生成 `out/_next/static/media/**/*.woff2`，脚本同时扫描 `out/_next/static/media/` 作为 fallback（若 `.next/` 不存在则切换到 `out/_next/`）。

### AC-4 文档同步自动断言

```bash
# AC-4.1: src/web/CLAUDE.md 已同步
rg -q "Inter" src/web/CLAUDE.md
rg -q "JetBrains Mono" src/web/CLAUDE.md
rg -q "next/font/google" src/web/CLAUDE.md
# 期望：三条均有命中

# AC-4.2: 根 CLAUDE.md 已同步
rg -q "Inter" CLAUDE.md
rg -q "JetBrains Mono" CLAUDE.md
rg -q "next/font/google" CLAUDE.md
# 期望：三条均有命中

# AC-4.3: IBM Plex 全清（已在 AC-2.3 覆盖，此处仅重申）
! rg -q "IBM Plex" src/web/CLAUDE.md CLAUDE.md
```

## 可追溯矩阵（FR × AC × Subtask）

| 需求 | 覆盖的 AC | 覆盖的 Subtask |
|---|---|---|
| FR-1 字体资源切换 | AC-2.2, AC-3.1, AC-3.2（强证据 1+2+3） | t6, t17 |
| FR-2 Token 分层 | AC-1.1~1.6, AC-1.10, AC-1.11 | t7, t9, t5, t17 |
| FR-3 OpenType 特性 | AC-1.7~1.9 | t8, t5, t17 |
| FR-4 类覆写清理 | AC-1.12 | t7a（新增）, t5, t17 |
| FR-5 字面量清理 | AC-2.1, AC-1.13 | t10, t11, t5, t17 |
| FR-6 文档同步 | AC-2.3, AC-4.1, AC-4.2, AC-4.3 | t12, t13, t17 |
| FR-7 验证基础设施 | (所有 AC 的实现载体) | t1, t3, t4, t14, t15, t16 |
| NFR-1 向后兼容 | AC-1.5, AC-1.6 + build 成功 | t5, t9, t17 |
| NFR-2 性能（不加载 SerifSC/NotoSC） | AC-3.2 强约束 4 | t14, t17 |
| NFR-3 CI 可验证 | 所有 AC | t17 |

## 范围边界

| 边界类型 | 内容 |
|---|---|
| **包含** | `src/web/src/app/layout.tsx`、`src/web/src/app/globals.css`（:root / `@layer base` 类覆写删除 / `body` 块 / `@theme inline`）、`src/web/src/app/analytics/page.tsx`（第 337/427 行）、`src/web/src/app/trading/components/tabs/OverviewTab.tsx`（第 251/252/290/291 行）、`src/web/CLAUDE.md`、根 `CLAUDE.md`、新增的 `src/web/tests/` 与 `src/web/scripts/`、`src/web/package.json`（新增 devDep + scripts） |
| **明确排除** | `cli/` 目录（项目禁区）、`docs/ui/qds-*.html`（设计稿源文件，豁免）、`src/tinohelm/backtest/tearsheet.py`（Python 后端 HTML 生成模板中 `'IBM Plex Sans'` 字符串，与前端字体加载无关，不在本任务范围）、44 个使用 `font-sans`/`font-mono`/`font-heading` className 的 `.tsx` 文件、globals.css 中 97 处 `var(--font-d/u)` 引用、`src/web/src/lib/chartTheme.ts` 的 4 处 `var(--font-d)`（legacy alias 保护域）、`.claude/skills/TinoHelmDS/` 设计系统源文件、Playwright/视觉回归基础设施、**Source Serif 4**（本任务明确不加载） |
