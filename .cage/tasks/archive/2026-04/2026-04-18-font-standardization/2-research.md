# 2. 技术调研 — 字体加载与 Fallback 策略

## 1. 字体加载方式对比

### 候选方案

| 方案 | 实现 | 国内可访问性 | 布局偏移 | 自动子集化 | Next.js 集成度 |
|---|---|---|---|---|---|
| **A. `<link>` → fonts.googleapis.com** | 当前 `layout.tsx:25-28` | 否，常被墙 | 否，需手写 `font-display: swap` + `size-adjust` | 否 | 低 |
| **B. `next/font/google`（推荐）** | 编译时拉取，构建产物自托管到 `.next/static/media/` | 是（走自己域名） | 是，内置 `size-adjust` fallback | 是，按 `subsets` / `weight` 自动裁剪 | 最高 |
| C. `@fontsource/*` | npm 包，运行时 `import '@fontsource/inter/400.css'` | 是 | 需手写 fallback metric | 需按权重逐个 import | 中 |
| D. 本地 `@font-face` + TTF/WOFF2 | `public/fonts/*.woff2` + globals.css `@font-face` | 是 | 手动处理 | 否 | 低 |

### 选型：方案 B（`next/font/google`）

**决策依据**：
1. 设计系统权威文件明示。`.claude/skills/TinoHelmDS/fonts/nextjs-setup.md:9-45` 给出完整示例代码，是 QDS 的「真实落地」方案。
2. 自动构建时自托管。Next.js 构建时把字体文件从 Google 拉取并写入 `.next/static/media/`，运行时通过自己的域名服务，国内用户零阻塞。
3. 零布局偏移。Next.js 内置 `size-adjust` 系统字体 metric 匹配。
4. 自动 CSS 变量绑定。`Inter({ variable: '--font-inter' })` 会在 `<html>` 上注入 `--font-inter` 变量，正好对接 globals.css 的 token 层级。
5. 版本 16 稳定 API。`package.json:23` 锁定 `next: 16.1.6`，`next/font` 自 13.2 起稳定。

**被拒方案的论证强化**（回应 skeptic 审查意见）：

- **方案 C（@fontsource）的劣势**：
  - 仍需手写 fallback metric（Next.js 已内置）；
  - 每字体多权重（400/500/600/700）需逐个 `import`，维护性低；
  - **潜在优势**：offline build（无外网也能构建）——但本项目开发机与 CI 均有外网，此优势不成立；若未来 CI 切换到内网环境，可降级到方案 C（§8 已记录）。
- **方案 D**：放弃子集化会导致 Inter 全字符集（约 400 KB）打包进产物，JetBrains Mono 同理。

## 2. Inter 风格集（cv11 / ss01 / ss03）启用方式

### OpenType 特性证据

Inter 字体官方文档（https://rsms.me/inter/#features）定义：
- `cv11` — Single-story `a`（单层 `a`）
- `ss01` — Open digits（开口数字 0/1/3/6/8）
- `ss03` — Curved/flat-terminal `g`

### `next/font/google` 的能力边界（重要澄清）

**`next/font/google` 的 Inter loader 不支持通过 JS 参数启用 stylistic sets**：

1. Next.js Inter loader 的 TypeScript 签名（`node_modules/next/dist/compiled/@next/font/dist/google/index.d.ts`）仅暴露 `subsets` / `weight` / `style` / `display` / `variable` / `preload` / `fallback` / `adjustFontFallback` / `axes` 等参数。
2. `axes` 参数仅接受 **variable axes**（如 `'slnt'`、`'opsz'`），**不接受** stylistic sets（`cv11`/`ss01`/`ss03` 属于 OpenType Layout features，不是 variable axes）。
3. OpenType `Stylistic Set` 和 `Character Variant` 类特性只能通过 CSS `font-feature-settings` 在**运行时**启用。

**结论**：`cv11`/`ss01`/`ss03` 必须在 CSS 端声明 `font-feature-settings: 'cv11','ss01','ss03'`。本任务选择在 `body {}` 全局注入，子元素继承。

### 启用方式对比

| 方式 | 代码 | 生效范围 | 优点 | 缺点 |
|---|---|---|---|---|
| CSS `font-feature-settings`（选定） | `body { font-feature-settings: 'cv11', 'ss01', 'ss03'; }` | 全局 | 一处声明，继承到所有 UI 文本 | 子元素若需关闭需显式声明 `normal` |
| Tailwind `font-feature-settings` utility | `<body className="[font-feature-settings:'cv11','ss01','ss03']">` | 全局 | Tailwind v4 支持 arbitrary values | 增加 className 长度 |
| 每字段单独声明 | 每个 `.data-value`、`h1` 块重复 | 局部 | 精细控制 | 97 处需同步，维护噩梦 |

### 选型：body 全局声明

与 QDS 源文件 `.claude/skills/TinoHelmDS/colors_and_type.css:183-185` 一致。

> **tabular-nums / `zero`** 等数据对齐特性**不在本次范围**（interview.md 未列出）。

## 3. 中文 Fallback 链排序

### 命中率依据

依据 TinoHelm 目标用户（量化研究员，多 macOS/鸿蒙混合设备）：

| 字体 | 平台 | 覆盖 |
|---|---|---|
| HarmonyOS Sans SC | 鸿蒙设备原生 | 国产手机/平板用户 |
| PingFang SC | macOS 原生 | macOS 用户 |
| Source Han Sans SC | 开发者主动安装 | 设计/前端用户 |
| system-ui | 兜底 | 所有平台系统默认 |

**Mono 链**：Sarasa Mono SC（更纱黑体）是国内量化圈常见的中英文等宽字体。

### 最终 stack

```css
--font-sans: var(--font-inter), 'HarmonyOS Sans SC', 'PingFang SC', 'Source Han Sans SC', system-ui, -apple-system, Segoe UI, sans-serif;
--font-mono: var(--font-jetbrains-mono), 'Sarasa Mono SC', ui-monospace, SFMono-Regular, Menlo, monospace;
```

**Inter 优先**是因为 latin 字符直接命中 Inter；中文字符 glyph 缺失时浏览器自动 fallback。

> **不加载 Noto Sans SC**。`nextjs-setup.md:47-61` 示例加载 Noto SC，但 TinoHelm 目标用户是量化研究员（macOS / 鸿蒙设备为主，系统本地已有 PingFang SC / HarmonyOS Sans SC），打包 Noto SC 会增加 1-2MB 产物体积。

## 4. Source Serif 4 加载决策（更新：明确不加载）

`.claude/skills/TinoHelmDS/SKILL.md:28` 原话：「备用 Source Serif 4 —— **不主动用**，留给长文档、证书类页面的场景。」

**本任务决策：不加载 Source Serif 4**（更新于 R2 修订；原方案为 `preload: false` 加载）。

### 决策理由（消除 R1 中 architect C1 / critic C5 指出的"否则跳过"歧义）

1. **当前无任何页面需要 serif 字体**（explorer 扫描 `src/web/` 未发现长文档或证书页面）。
2. **加载成本 vs 收益不成立**：即使 `preload: false`，Source Serif 4 的 subset woff2 仍会进入产物（约 50-80 KB）；若 runtime 从未请求，纯属浪费。
3. **AC 不可跳过**：若加载则必须验证产物存在；若不加载则明确声明缺失。"可选/否则跳过"违反用户全局 RULE「禁止手动验证 item」的精神。本方案选**完全不加载 + AC 反向断言产物中不存在**，保证 100% 自动化。
4. **未来启用路径**：若未来新增长文档页（如策略报告详情），启动独立子任务补充加载。本次不预加载。

### 影响

- `layout.tsx`：仅导入 `Inter` + `JetBrains_Mono`（2 个字体，不含 `Source_Serif_4`）。
- `globals.css`：不定义 `--font-serif`（或保留但为空 stack — 本任务选不定义）。
- `verify-build-fonts.mjs`：断言 `.next/static/css/*.css` 的 `@font-face` 条目**不包含** `font-family: 'Source Serif 4'` 字面量（反向断言）。

## 5. globals.css token 层级重构方案

### 现状（globals.css:15-16 + 185-195 + 217-218）

```css
/* :root */
--font-d: 'IBM Plex Mono', monospace;
--font-u: 'IBM Plex Sans', sans-serif;

/* @layer base */
.font-sans { font-family: var(--font-u); }
.font-heading { font-family: var(--font-u); }
.font-mono { font-family: var(--font-d); }

/* @theme inline */
--font-sans: var(--font-u);
--font-mono: var(--font-d);
```

**数据流**：`className="font-sans"` → 手写 `.font-sans` 覆写（与 Tailwind utility 同 layer，源码顺序胜出）→ `var(--font-u)` → `'IBM Plex Sans'`。

### 目标

```css
/* :root */
--font-sans:  var(--font-inter), 'HarmonyOS Sans SC', ...;
--font-mono:  var(--font-jetbrains-mono), 'Sarasa Mono SC', ...;

/* Legacy alias — 保护现有 97 处引用零破坏 */
--font-d: var(--font-mono);
--font-u: var(--font-sans);

/* @layer base — 删除 .font-sans/.font-mono/.font-heading 三个类覆写 */
/* （Tailwind v4 @theme inline 自动产出 .font-sans/.font-mono utility；.font-heading 无定义，消费方 8 个 .tsx 退化为从 body 继承，与原行为等价） */

/* @theme inline — Tailwind 直接消费新 token */
--font-sans: var(--font-sans);  /* identity */
--font-mono: var(--font-mono);  /* identity */
```

**新数据流**：`className="font-sans"` → Tailwind v4 `@theme inline --font-sans` → `:root --font-sans` → `var(--font-inter)` → Next.js 注入的 Inter。

**Legacy 路径**（兼容）：`var(--font-d)` → `var(--font-mono)` → 同上。

### `@theme inline --font-sans: var(--font-sans)` 自引用的安全性（回应 architect M6）

Tailwind v4 `@theme inline` 语义：**不在 at-root 注入 CSS variable**，而是把值直接内联到 utility 定义中：

```css
/* Tailwind v4 编译产出（伪代码） */
.font-sans { font-family: var(--font-sans); }
```

其中 `var(--font-sans)` 在运行时解析 `:root` 的 `--font-sans`（即新 token），**不产生编译期循环**。

**验证路径**（闭环证据）：
1. 若 `@theme inline --font-sans: var(--font-sans)` 在 Tailwind v4 产生循环，`next build`（t9 验收）会报错。
2. 可参考 `node_modules/@tailwindcss/postcss/dist/index.mjs` 的 `@theme inline` 处理逻辑（编译时把引用替换为 utility body 的 `var()` 调用）。
3. **降级方案**：若未来 Tailwind 版本升级改变了 `@theme inline` 行为，回退为 `--font-sans: var(--font-inter), 'HarmonyOS Sans SC', ...;` 完整展开（放弃 identity 转发）。

### 反转方向的权衡

| 方向 | 优 | 劣 |
|---|---|---|
| **新 token 权威，legacy 指向新**（选定） | 与 QDS `colors_and_type.css:13-20` 一致；新代码语义更清晰；单一真源 | 97 处 `var(--font-d/u)` 需多走一跳 var() |
| Legacy 权威，新 token 指向 legacy | 改动最小 | 与 QDS 文档不一致；QDS 是权威规范源，项目作为消费方应对齐而非反向 |

**浏览器支持补充**：Chrome 已取消 var() 解析深度上限；Safari 历史上限 20 层。本方案实际深度 `font-sans` → `--font-sans` → `var(--font-inter)` = 3 层，远低于限制。

## 6. QDS skill 两份实现参考的差异

| 方面 | `colors_and_type.css`（设计系统源） | `fonts/nextjs-setup.md`（真实落地指南） |
|---|---|---|
| 字体加载 | 顶部 `@import url('https://fonts.googleapis.com/...')` | `next/font/google` 自托管 |
| CSS 变量 | 直接写字体族字面量 | 引用 `var(--font-inter)` 等 Next.js 注入变量 |
| 中文字体 | 只写 fallback 字体族名字面量 | 可选 `next/font/google` 拉取 Noto Sans SC |
| Legacy alias | 直接字面量 stack | 同 colors_and_type.css |

### 本任务采用方案

**混合**：加载层走 `nextjs-setup.md` 方案（`next/font/google`），token 层用 `colors_and_type.css` 的 stack 顺序（**不加载 Noto SC、不加载 Source Serif 4**）。

## 7. 验证层工具选型

### 测试框架

`package.json` 当前无测试框架。候选：

| 方案 | 理由 |
|---|---|
| **vitest + postcss AST**（选定） | 轻量、ESM-first、与 Tailwind v4 / Next.js 16 生态兼容好；postcss 已在 `@tailwindcss/postcss` 依赖链中，零新增成本；AST 解析比正则更稳（回应 architect C1 / critic C1） |
| vitest + 手写正则 | 简单但正则对嵌套 `{}`、多 selector 场景脆弱（已被 critic 实测发现 `extractBlock('body')` 会误匹配 `html, body {...}`） |
| jest | 老牌，但 ESM 配置繁、无 Tailwind v4 原生适配 |
| node:test | 原生，但断言 API 贫瘠 |

**选型理由（针对 skeptic 视角）**：postcss AST 解析 **零新增依赖**（`@tailwindcss/postcss@^4` 已传递依赖 `postcss`），且从根本上消除 critic C1 发现的 body selector 误匹配问题。代码量并不显著增加：

```ts
// parse-css.ts 关键代码（10 行）
import postcss from 'postcss';
const root = postcss.parse(css);
const rootRule = root.nodes.find(n => n.type === 'rule' && n.selector === ':root');
// 精确定位 selector === 'body'（而非 'html, body'），排除 html, body { height: 100% }
const bodyRule = root.walkRules().find(r => r.selector.trim() === 'body');
```

### Fixture 位置

| 文件 | 用途 |
|---|---|
| `src/web/tests/fonts/tokens.test.ts` | 主测试文件，断言 15 个 token 项 |
| `src/web/tests/fonts/fixtures/parse-css.ts` | postcss AST 解析工具：`readGlobalsCss`, `getRootDecls`, `getBodyDecls`, `getThemeInlineDecls`, `getVarValue` |
| `src/web/vitest.config.ts` | vitest 配置 |

### Rg 规则

所有 `rg` 断言在 `src/web/scripts/check-grep-fonts.sh` 中执行。脚本使用 `git rev-parse --show-toplevel` 锁定仓库根，避免相对路径漂移。

### next build 脚手架集成点

```json
{
  "scripts": {
    "test:fonts": "vitest run tests/fonts --reporter=verbose",
    "check:grep:fonts": "bash scripts/check-grep-fonts.sh",
    "verify:build:fonts": "node scripts/verify-build-fonts.mjs",
    "verify:fonts:all": "npm run test:fonts && npm run check:grep:fonts && npm run build && npm run verify:build:fonts"
  }
}
```

## 8. 回滚策略

### 触发条件

- `npm run build` 失败；
- `npm run test:fonts` 任一断言失败；
- 国内生产环境字体加载失败率 > 1%（需上线后观察，不在本任务验证范围）；
- layout.tsx 引入 `next/font/google` 后 dev server 冷启动超时（Next.js 首次拉取字体可能较慢）。

### 回滚步骤

1. `git revert <commit-range>`——本任务所有改动聚焦在有限文件（layout.tsx、globals.css、chart tick 文件、两个 CLAUDE.md、tests/、scripts/、package.json）。
2. `cd src/web && rm -rf .next node_modules && npm ci && npm run build` 验证回滚成功。
3. 若仅构建失败、代码无逻辑问题，可临时回退 `next/font/google` → `<link>` CDN，保留 token 重构部分。

### 降级方案（备选，不在本任务范围）

若 `next/font/google` 在构建机受限（如自建 CI 无法访问 Google），降级到方案 C（`@fontsource/*`）。

- 替换 `import { Inter, ... } from 'next/font/google'` → `import '@fontsource-variable/inter'`；
- 手写 `--font-inter` CSS 变量到 globals.css；
- 其余 token 分层重构保持不变。

此降级**不在本任务验收内**，作为 known fallback 记录。

## 9. CI 环境要求

- `rg`（ripgrep）≥ 13：`check-grep-fonts.sh` 使用 `--glob '!pattern'` 否定语法；脚本起首 `command -v rg` 校验存在性。
- `node` ≥ 18：vitest ^3.0 / next 16 均要求。
- `git`：`check-grep-fonts.sh` 用 `git rev-parse --show-toplevel` 定位仓库根。
- 外网访问 Google：`next/font/google` 构建时拉取字体文件。**若无外网，build 失败 → 任务不可执行，应提前告知用户**（不在 AC 内断言）。
