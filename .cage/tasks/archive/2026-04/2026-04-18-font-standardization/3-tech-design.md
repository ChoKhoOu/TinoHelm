# 3. 技术设计 — 前端字体 QDS 标准化

## 架构对齐分析

### 现有前端架构模式（棕地）

通过 explorer 代码盘点得出：

1. **Token 两层架构**（globals.css）：
   - 第一层：QDS 短名 token 在 `:root`（`--font-d` / `--font-u` / `--bg-p` 等），由设计师定义；
   - 第二层：shadcn 语义 token + Tailwind 扩展在 `@theme inline`（`--font-sans` / `--font-mono` / `--color-*`），由 Tailwind utility 消费。
   - 两层通过 `var()` 间接引用相互耦合。
2. **Tailwind v4 配置**：无 `tailwind.config.ts`，全部通过 globals.css 的 `@theme inline` 块实现，是 Tailwind v4 原生机制。
3. **CSS 消费模式**：
   - `.tsx` 文件主要通过 `className="font-sans font-mono"` 消费字体 token；
   - globals.css 内 CSS 类（`.qds-input`、`.bt-*`、`.dc-*`）则直接 `var(--font-d/u)` 消费 QDS 短名；
   - `globals.css:185-195` 存在 `.font-sans` / `.font-heading` / `.font-mono` 三个 `@layer base` 类覆写，与 Tailwind v4 自动 utility 同层但源码顺序靠后胜出（本任务将清理）。
4. **字体加载**：目前走 `<link>` CDN（`layout.tsx:25-28`），未使用 `next/font/*`。

### 本任务如何对齐

| 模式维度 | 现有约定 | 本任务做法 |
|---|---|---|
| 变体处理 | Dark/light 通过 `html.light` class 切换，字体 token 不分变体 | **顺着现有模式**：字体 token 不引入 light/dark 变体 |
| 分层策略 | QDS 短名 → Tailwind token（两层） | **顺着现有模式**：保留两层结构，但 QDS 短名从字面量改为引用 Next.js 注入变量 |
| 横切关注点 | globals.css 作为唯一 token 定义源 | **顺着现有模式**：所有 font token 集中在 globals.css `:root` + `@theme inline` |
| 向后兼容 | 97 处 `var(--font-d/u)` + 44 个 className 消费 | **新增别名指向**：legacy token 反指新 token，保证零消费方改动 |
| 字体加载 | `<link>` CDN（未使用 next/font） | **首次引入 `next/font/google`**：此为新增，未偏离现有模式（仅补全 Next.js 推荐实践） |

**关键设计决策**：整个改动以「token 值重定向 + 新增加载层 + 清理 legacy CSS 类覆写」方式实现。**不重构 Tailwind className 消费方**（44 个 `.tsx`），依赖 Tailwind utility 自动产出机制 + body 继承。

### 潜在偏离 & 理由

**轻微偏离**：删除 `globals.css:185-195` 的三个 `.font-sans` / `.font-mono` / `.font-heading` 类覆写。理由：
- 它们与 Tailwind v4 `@theme inline` 自动产出的 utility 冗余；
- 现有模式下手写覆写源码顺序胜出，使消费方 `className="font-sans"` 多绕一跳 legacy alias，token 流图不清；
- 8 个 `.tsx` 文件消费 `className="font-heading"`（`popover.tsx`、`dialog.tsx`、`sheet.tsx`、`optimization/page.tsx` 等），删除后它们会从 body 继承 `var(--font-u)` → `var(--font-sans)` → Inter，行为等价（原类覆写值就是 `var(--font-u)`）。

## 字体加载架构图

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. FontLoader (layout.tsx)                                      │
│    next/font/google 构建时拉取 → .next/static/media/*.woff2     │
│    ↓ 注入 <html className={inter.variable jetbrainsMono.variable}│
│    产出 CSS 变量：--font-inter, --font-jetbrains-mono           │
│    （不加载 Source Serif 4 / Noto SC）                          │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ 2. FontToken (globals.css :root)                                │
│    --font-sans:  var(--font-inter),           fallback chain    │
│    --font-mono:  var(--font-jetbrains-mono),  fallback chain    │
│                                                                  │
│    Legacy aliases (保护 97 处现有引用):                         │
│    --font-d: var(--font-mono)                                   │
│    --font-u: var(--font-sans)                                   │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ 3. Tailwind Consumption (globals.css @theme inline)             │
│    --font-sans: var(--font-sans)  ← identity，直接消费 :root   │
│    --font-mono: var(--font-mono)  ← identity                    │
│    (删除 @layer base 内 .font-sans/.font-mono/.font-heading     │
│     三个类覆写 — 见 FR-4)                                       │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ 4. Consumer                                                     │
│    className="font-sans"      ← Tailwind utility (自动产出)    │
│    className="font-mono"      ← Tailwind utility (自动产出)    │
│    className="font-heading"   ← 无 utility，从 body 继承       │
│    font-family: var(--font-d) ← globals.css 内 97 处 legacy    │
│    fontFamily: "var(--font-mono)" ← chart tick inline style    │
│    fontFamily: "var(--font-d)" ← chartTheme.ts (豁免零改动)   │
└─────────────────────────────────────────────────────────────────┘
```

### OpenType 特性注入点

```
body {
  font-family: var(--font-u);                       /* → var(--font-sans) */
  font-feature-settings: 'cv11', 'ss01', 'ss03';    /* 新增 */
}
```

**启用机制澄清**（针对 architect M4 要求）：

- Next.js `next/font/google` 的 Inter loader **不支持**通过 JS 参数声明 OpenType stylistic sets（`cv11`/`ss01`/`ss03` 不是 variable axes，`axes` 参数拒绝它们）。
- 这些是 **OpenType Layout features**，必须通过 CSS `font-feature-settings` 在运行时启用。
- 选择在 `body {}` 全局注入，子元素继承；数据密集组件若需精细控制可局部 override（本任务不做）。

## 影响文件清单（已通过 Grep/Read 验证存在）

| 文件 | 验证方式 | 改动类型 |
|---|---|---|
| `src/web/src/app/layout.tsx` | Read L25-28 确认 `<link>` 存在 | 修改 |
| `src/web/src/app/globals.css` | Read L15-16 / L185-195 / L206-213 / L217-218 | 修改（4 处 block） |
| `src/web/src/app/analytics/page.tsx` | Grep L337, L427 | 修改（2 处字面量） |
| `src/web/src/app/trading/components/tabs/OverviewTab.tsx` | Grep L251/252/290/291 | 修改（4 处字面量） |
| `src/web/CLAUDE.md` | Grep L141 | 修改 |
| `CLAUDE.md`（根） | Grep L268 | 修改 |
| `src/web/package.json` | Read 确认 devDep 列表 | 修改（+vitest、+postcss devDep + scripts） |
| `src/web/tests/fonts/tokens.test.ts` | — | 新建 |
| `src/web/tests/fonts/fixtures/parse-css.ts` | — | 新建（postcss AST） |
| `src/web/vitest.config.ts` | — | 新建 |
| `src/web/scripts/verify-build-fonts.mjs` | — | 新建（CSS 文本扫描） |
| `src/web/scripts/check-grep-fonts.sh` | — | 新建 |

### 不改动（明确豁免）

- **44 个使用 `className="font-sans" / "font-mono" / "font-heading"` 的 `.tsx` 文件**（Grep 核实）
- **globals.css 内 97 处 `var(--font-d)` / `var(--font-u)` 引用**（`rg -o 'var\(--font-[du]\)' globals.css | wc -l` = 97）
- **`src/web/src/lib/chartTheme.ts:14/23/30/36` 使用 `var(--font-d)`**（legacy alias 保护域，零改动）
- **`src/tinohelm/backtest/tearsheet.py`** 的 `'IBM Plex Sans'` 字符串（Python 后端 HTML 生成模板，属于后端 tearsheet 渲染，非本任务范围）
- `.claude/skills/TinoHelmDS/` 设计系统源
- `docs/ui/qds-*.html` 设计稿
- `cli/` 整个目录（项目禁区）

## 模块设计

### 模块 1：FontLoader（layout.tsx）

**新导入**（文件开头，第 1 行之后）：

```tsx
import { Inter, JetBrains_Mono } from 'next/font/google';
```

> 不导入 `Source_Serif_4`（FR / research §4 明确不加载）。

**字体声明**（放在 `metadata` 定义之前，约第 11-12 行之间）：

```tsx
const inter = Inter({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-inter',
  display: 'swap',
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ['latin'],
  weight: ['400', '500', '700'],
  variable: '--font-jetbrains-mono',
  display: 'swap',
});
```

**`<html>` className 合并**（第 23 行）：

```tsx
<html
  lang="zh"
  className={`h-full ${inter.variable} ${jetbrainsMono.variable}`}
  suppressHydrationWarning
>
```

**移除** `<link rel="stylesheet" href="https://fonts.googleapis.com/...">` 节点（第 25-28 行）。

保留 `<script>` FOUC prevention 块（第 30-34 行）。

### 模块 2：FontToken（globals.css `:root`）

**替换第 14-16 行**：

```css
:root {
  /* --- Fonts ---
     QDS 权威字体 token（依据 .claude/skills/TinoHelmDS/colors_and_type.css:13-15）
     Inter → UI 文本；JetBrains Mono → 数据值 */
  --font-sans:  var(--font-inter),           'HarmonyOS Sans SC', 'PingFang SC', 'Source Han Sans SC', system-ui, -apple-system, Segoe UI, sans-serif;
  --font-mono:  var(--font-jetbrains-mono),  'Sarasa Mono SC', ui-monospace, SFMono-Regular, Menlo, monospace;

  /* Legacy aliases — 保护 globals.css 内 97 处 var(--font-d/u) 引用零破坏 */
  --font-d: var(--font-mono);
  --font-u: var(--font-sans);

  /* --- Motion --- */
  ...
}
```

> 不定义 `--font-serif`（本任务不加载 Source Serif 4；参见 research §4）。

### 模块 2.5：清理 `@layer base` 内 legacy 类覆写（FR-4，对应 architect C1 / critic M1）

**删除**`src/web/src/app/globals.css:185-195` 的整块：

```css
/* 待删除 */
.font-sans {
  font-family: var(--font-u);
}

.font-heading {
  font-family: var(--font-u);
}

.font-mono {
  font-family: var(--font-d);
}
```

**理由与影响**：
- Tailwind v4 `@theme inline` 自动为 `--font-sans`/`--font-mono` 产出对应 utility（`.font-sans`/`.font-mono`）。
- `.font-heading` 无对应 Tailwind token，删除后 8 个消费方从 body 继承 `var(--font-u)` → `var(--font-sans)` → Inter，与原手写行为等价。
- 消除消费方 `className="font-sans"` 额外绕一跳 legacy alias 的冗余路径。

**新增 vitest 断言**（AC-1.12）：`expect(css).not.toMatch(/\.font-(sans|mono|heading)\s*\{[^}]*var\(--font-[du]\)/)` 防止回归。

### 模块 3：OpenTypeFeatures（globals.css `body` block）

**在第 206-213 行的 `body {}` 块内追加一行**（保留 `@apply` 指令作为 sanity check 锚点）：

```css
body {
  @apply bg-background text-foreground;
  font-family: var(--font-u);
  font-size: 14px;
  line-height: 1.5;
  font-feature-settings: 'cv11', 'ss01', 'ss03';  /* 新增 */
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
```

### 模块 4：Tailwind 消费层（globals.css `@theme inline`）

**第 217-218 行替换**：

```css
@theme inline {
  --font-sans: var(--font-sans);   /* 新 token 直接 identity 转发给 Tailwind */
  --font-mono: var(--font-mono);
  ...其余保持原样
}
```

**原理与验证**（回应 architect M6）：

1. `@theme inline` 是 Tailwind v4 原生机制。内部 `--font-sans` 是 Tailwind namespace（供 `font-sans` utility 读取），右侧 `var(--font-sans)` 是对 `:root --font-sans` 的引用。
2. Tailwind v4 编译时把 `@theme inline` 内的 var 内联到 utility 定义：
   ```css
   /* 编译产出（伪代码） */
   .font-sans { font-family: var(--font-sans); }
   ```
   其中 `var(--font-sans)` 在运行时解析 `:root --font-sans`，不产生编译期循环。
3. **验证路径**：若该自引用在 Tailwind v4 实际版本下触发循环，`next build`（t9 验收）会失败 —— 这是有效的 smoke test，捕获了 Tailwind 升级后语义变更的风险。

### 模块 5：Chart Tick 字面量替换

| 文件 | 行 | 原值 | 新值 |
|---|---|---|---|
| analytics/page.tsx | 337 | `fontFamily: "IBM Plex Mono"` | `fontFamily: "var(--font-mono)"` |
| analytics/page.tsx | 427 | `fontFamily: "IBM Plex Mono"` | `fontFamily: "var(--font-mono)"` |
| OverviewTab.tsx | 251 | 同 | 同 |
| OverviewTab.tsx | 252 | 同 | 同 |
| OverviewTab.tsx | 290 | 同 | 同 |
| OverviewTab.tsx | 291 | 同 | 同 |

> **为何不用 `var(--font-d)`**：新代码优先消费权威 token，保持与 QDS skill 文档一致。legacy alias 仅为兼容旧 97 处引用及 chartTheme.ts 的 4 处。

### 模块 6：文档同步（写死新文本，避免 executor 发挥）

#### `src/web/CLAUDE.md:141` — before / after diff

**before**（第 141 行）：

```
- **Fonts**: IBM Plex Sans (`font-sans` / `var(--font-u)`) for UI, IBM Plex Mono (`font-mono` / `var(--font-d)`) for data values.
```

**after**：

```
- **Fonts**: Inter (`font-sans` / alias `var(--font-u)`) for UI, JetBrains Mono (`font-mono` / alias `var(--font-d)`) for data values. Loaded via `next/font/google` in `layout.tsx` (self-hosted via `.next/static/media/`, not CDN). Inter OpenType features `cv11`/`ss01`/`ss03` enabled globally on `body`. Legacy aliases `--font-u`/`--font-d` re-point to `--font-sans`/`--font-mono` for backward compatibility.
```

#### 根 `CLAUDE.md:268` — before / after diff

**before**（第 268 行）：

```
- **Font**: IBM Plex Sans (`font-sans`) for UI, IBM Plex Mono (`font-mono`) for data
```

**after**：

```
- **Font**: Inter (`font-sans`) for UI, JetBrains Mono (`font-mono`) for data values; loaded via `next/font/google` (self-hosted) with Inter OpenType features `cv11`/`ss01`/`ss03` enabled on `body`. Legacy QDS aliases `var(--font-u)`/`var(--font-d)` alias to the new tokens.
```

### 模块 7：验证层

#### 7.1 `src/web/tests/fonts/fixtures/parse-css.ts`（postcss AST，替换 R1 的正则方案）

```ts
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import postcss, { Rule, AtRule, Declaration } from 'postcss';

const __dirname = dirname(fileURLToPath(import.meta.url));
const CSS_PATH = resolve(__dirname, '../../../src/app/globals.css');

export function readGlobalsCss(): string {
  return readFileSync(CSS_PATH, 'utf-8');
}

/**
 * 精确定位 `:root` rule（第一个出现即可；light 模式走 `html.light`，selector 不同）。
 */
export function getRootDecls(css: string): Map<string, string> {
  const root = postcss.parse(css);
  const decls = new Map<string, string>();
  root.walkRules(rule => {
    if (rule.selector.trim() === ':root') {
      rule.walkDecls(d => decls.set(d.prop, d.value));
      return false;
    }
  });
  return decls;
}

/**
 * 精确定位 `body` rule（selector 必须是 trim 后精确等于 "body"，排除 "html, body"）。
 * 返回 { decls, raw, hasApply } —— raw 是块内原始文本，便于 regex 对 font-feature-settings
 * 等声明做回退检查；hasApply 用作 tokens.test.ts 的 sanity 断言（确保取到含 @apply 的真
 * 正 body 块，非 "html, body { height: 100% }"）。
 *
 * 类型策略（R3 修订，对应 Critic R2 MINOR-3 + Architect R2 MINOR-2）：
 *   - 返回类型显式声明 `hasApply: boolean`，不再通过 `as any` 附加 "magic property"；
 *   - `target` 先用 local 别名窄化（callback 内 TS 无法跨闭包推断赋值），避免 strict 下
 *     TS2532 "Object is possibly null"。
 */
export interface BodyRuleSnapshot {
  decls: Map<string, string>;
  raw: string;
  hasApply: boolean;
}

export function getBodyRule(css: string): BodyRuleSnapshot {
  const root = postcss.parse(css);
  // 收集后一次性取（callback 内赋值在 strict 下无法跨闭包 narrow —— R3 修订避让）
  const matches: Rule[] = [];
  root.walkRules(rule => {
    if (rule.selector.trim() === 'body') {
      matches.push(rule);
      return false; // 找到一个即停（尾随的 "html, body" 等不等于 "body" 不会混入）
    }
  });
  const target = matches[0];
  if (target === undefined) {
    throw new Error('body { } rule not found (exact selector)');
  }
  const decls = new Map<string, string>();
  target.walkDecls(d => decls.set(d.prop, d.value));
  // @apply 被 PostCSS 解析为 AtRule 节点（而非 Declaration）
  const hasApply = target.nodes.some(
    n => n.type === 'atrule' && (n as AtRule).name === 'apply',
  );
  return { decls, raw: target.toString(), hasApply };
}

/**
 * 精确定位 `@theme inline` at-rule。
 */
export function getThemeInlineDecls(css: string): Map<string, string> {
  const root = postcss.parse(css);
  const decls = new Map<string, string>();
  root.walkAtRules('theme', atRule => {
    if (atRule.params.trim() === 'inline') {
      atRule.walkDecls(d => decls.set(d.prop, d.value));
      return false;
    }
  });
  return decls;
}

/** 整文本级别的反向断言（用于 AC-1.12 / AC-1.13 / AC-1.14）。 */
export function globalCssText(): string {
  return readGlobalsCss();
}
```

#### 7.2 `src/web/tests/fonts/tokens.test.ts`

```ts
import { describe, it, expect } from 'vitest';
import {
  readGlobalsCss,
  getRootDecls,
  getBodyRule,
  getThemeInlineDecls,
  globalCssText,
} from './fixtures/parse-css';

describe('QDS Font Tokens (globals.css)', () => {
  const css = readGlobalsCss();
  const root = getRootDecls(css);
  const body = getBodyRule(css);
  const themeInline = getThemeInlineDecls(css);

  // Sanity: 确认 body 块是我们期望的那个（含 @apply），而不是 html, body { height: 100% }
  it('(sanity) body rule contains @apply directive', () => {
    expect(body.hasApply).toBe(true);
  });

  // A1.1
  it('--font-sans leads with var(--font-inter)', () => {
    const v = root.get('--font-sans') ?? '';
    expect(v).toMatch(/^var\(--font-inter\)/);
  });

  // A1.2
  it('--font-mono leads with var(--font-jetbrains-mono)', () => {
    const v = root.get('--font-mono') ?? '';
    expect(v).toMatch(/^var\(--font-jetbrains-mono\)/);
  });

  // A1.3
  it('Sans fallback chain contains PingFang SC (word-boundary)', () => {
    expect(root.get('--font-sans')).toMatch(/\bPingFang\s+SC\b/);
  });

  // A1.4
  it('Mono fallback chain contains Sarasa Mono SC (word-boundary)', () => {
    expect(root.get('--font-mono')).toMatch(/\bSarasa\s+Mono\s+SC\b/);
  });

  // A1.5
  it('Legacy --font-d aliases to var(--font-mono)', () => {
    expect(root.get('--font-d')?.trim()).toBe('var(--font-mono)');
  });

  // A1.6
  it('Legacy --font-u aliases to var(--font-sans)', () => {
    expect(root.get('--font-u')?.trim()).toBe('var(--font-sans)');
  });

  // A1.7 - A1.9
  it('body declares font-feature-settings cv11', () => {
    expect(body.decls.get('font-feature-settings')).toMatch(/cv11/);
  });
  it('body declares font-feature-settings ss01', () => {
    expect(body.decls.get('font-feature-settings')).toMatch(/ss01/);
  });
  it('body declares font-feature-settings ss03', () => {
    expect(body.decls.get('font-feature-settings')).toMatch(/ss03/);
  });

  // A1.10
  it('@theme inline --font-sans resolves to --font-sans or --font-inter', () => {
    expect(themeInline.get('--font-sans')).toMatch(/var\(--font-(sans|inter)\)/);
  });

  // A1.11
  it('@theme inline --font-mono resolves to --font-mono or --font-jetbrains-mono', () => {
    expect(themeInline.get('--font-mono')).toMatch(/var\(--font-(mono|jetbrains-mono)\)/);
  });

  // A1.12 — legacy class 覆写已删除（FR-4）
  it('No legacy .font-sans/.font-mono/.font-heading class overrides bound to --font-u/--font-d', () => {
    expect(globalCssText()).not.toMatch(
      /\.font-(sans|mono|heading)\s*\{[^}]*var\(--font-[du]\)/,
    );
  });

  // A1.13 — IBM Plex 全清
  it('No IBM Plex literal remains in globals.css', () => {
    expect(globalCssText()).not.toMatch(/IBM Plex/);
  });

  // A1.14 — sanity: 新 token 未被注释
  it('--font-sans / --font-mono definitions are not commented out', () => {
    const text = globalCssText();
    expect(text).not.toMatch(/\/\*[^*]*--font-sans\s*:/);
    expect(text).not.toMatch(/\/\*[^*]*--font-mono\s*:/);
  });
});
```

#### 7.3 `src/web/vitest.config.ts`

```ts
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['tests/**/*.test.ts'],
    environment: 'node',
  },
});
```

#### 7.4 `src/web/scripts/verify-build-fonts.mjs`（CSS 文本扫描，替换 R1 的文件名正则）

```js
#!/usr/bin/env node
/**
 * Verify Next.js build produced the expected @font-face entries and woff2 files.
 *
 * 策略（两层防御 — 回应 architect M3 / critic C4）：
 *   - 强证据：扫描 .next/static/css/*.css 中 @font-face 的 font-family 字面量
 *       必须出现：'Inter'、'JetBrains Mono'
 *       必须不出现：'Source Serif 4'（本任务明确不加载）
 *   - Sanity：.next/static/media/**.woff2 文件数 >= 2
 *
 * 静态导出（next.config.ts: output: "export"）场景：
 *   若 .next/ 不存在则 fallback 扫描 out/_next/（CSS 与 media 路径同构）。
 */
import { readdirSync, readFileSync, existsSync, statSync } from 'node:fs';
import { join, extname } from 'node:path';

function pickRoot() {
  const candidates = ['.next', 'out/_next'];
  for (const r of candidates) {
    const cssDir = join(process.cwd(), r, 'static', 'css');
    if (existsSync(cssDir)) return r;
  }
  console.error('[verify-build-fonts] Neither .next nor out/_next found. Did `next build` run?');
  process.exit(1);
}

const rootBase = pickRoot();
const cssDir = join(process.cwd(), rootBase, 'static', 'css');
const mediaDir = join(process.cwd(), rootBase, 'static', 'media');

// 1) 聚合所有 .css 文本（可能有多个 chunks）
const cssText = readdirSync(cssDir)
  .filter(f => f.endsWith('.css'))
  .map(f => readFileSync(join(cssDir, f), 'utf-8'))
  .join('\n\n');

// 2) 解析 @font-face 的 font-family 字面量
//    Next.js 会为每个字体生成带 family 名的 @font-face（保留原 family 名便于 fallback matching）
const fontFaceFamilies = [...cssText.matchAll(/@font-face\s*\{[^}]*font-family\s*:\s*['"]([^'"]+)['"]/g)]
  .map(m => m[1]);

function hasFamily(name) {
  return fontFaceFamilies.some(f => f.toLowerCase().includes(name.toLowerCase()));
}

const required = ['Inter', 'JetBrains Mono'];
const forbidden = ['Source Serif 4'];

let failed = 0;

for (const fam of required) {
  const ok = hasFamily(fam);
  console.log(`[verify-build-fonts] required @font-face { font-family: '${fam}' }: ${ok ? 'OK' : 'MISSING'}`);
  if (!ok) failed++;
}
for (const fam of forbidden) {
  const present = hasFamily(fam);
  console.log(`[verify-build-fonts] forbidden @font-face { font-family: '${fam}' }: ${present ? 'PRESENT (FAIL)' : 'absent OK'}`);
  if (present) failed++;
}

// 3) Sanity: 至少 2 个 woff2
const woff2 = existsSync(mediaDir)
  ? readdirSync(mediaDir).filter(f => f.endsWith('.woff2'))
  : [];
console.log(`[verify-build-fonts] woff2 count in ${mediaDir}: ${woff2.length}`);
if (woff2.length < 2) {
  console.error(`[verify-build-fonts] expected >= 2 woff2 files, got ${woff2.length}`);
  failed++;
}

if (failed > 0) {
  console.error(`[verify-build-fonts] ${failed} check(s) failed`);
  process.exit(1);
}
console.log('[verify-build-fonts] All checks passed.');
```

#### 7.5 `src/web/scripts/check-grep-fonts.sh`（git rev-parse 锁定仓库根 + rg 前置检查）

```bash
#!/usr/bin/env bash
# AC-2: grep-level compliance check
# Exits 0 on success, 1 on any violation.
set -u

# 前置：rg 可用性
command -v rg >/dev/null 2>&1 || { echo "[FAIL] ripgrep (rg) not installed"; exit 1; }

# 锁定仓库根，避免相对路径漂移
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "[FAIL] not a git repository"; exit 1;
}
cd "$REPO_ROOT"

# 文件存在性前置
[ -f "CLAUDE.md" ] || { echo "[FAIL] root CLAUDE.md not found"; exit 1; }
[ -f "src/web/CLAUDE.md" ] || { echo "[FAIL] src/web/CLAUDE.md not found"; exit 1; }

violations=0

check_no_match() {
  local desc="$1"; shift
  if "$@" > /dev/null 2>&1; then
    echo "[FAIL] $desc"
    "$@" || true
    violations=$((violations + 1))
  else
    echo "[PASS] $desc"
  fi
}

# AC-2.1 — no IBM Plex in src/web/
# 豁免：*.html (设计稿) / *.bak / node_modules / .next / out / archive / CHANGELOG.md
check_no_match "AC-2.1 No 'IBM Plex' literals in src/web/" \
  rg -q "IBM Plex" src/web/ \
     --glob '!*.html' \
     --glob '!*.bak' \
     --glob '!node_modules' \
     --glob '!.next' \
     --glob '!out' \
     --glob '!archive' \
     --glob '!CHANGELOG.md'

# AC-2.2 — no CDN direct reference in src/web/src/
check_no_match "AC-2.2 No 'fonts.googleapis.com' direct reference in src/web/src/" \
  rg -q "fonts.googleapis.com" src/web/src/

# AC-2.3 — CLAUDE.md sync (both root and web)
check_no_match "AC-2.3 No 'IBM Plex' in src/web/CLAUDE.md" rg -q "IBM Plex" src/web/CLAUDE.md
check_no_match "AC-2.3 No 'IBM Plex' in root CLAUDE.md" rg -q "IBM Plex" CLAUDE.md

if [ $violations -gt 0 ]; then
  echo "check-grep-fonts: $violations violation(s) found"
  exit 1
fi
echo "check-grep-fonts: all checks passed"
```

#### 7.6 `src/web/package.json` scripts 与 devDependencies 增量

```jsonc
{
  "scripts": {
    // 保留原有 dev / build / start / lint
    "test:fonts": "vitest run tests/fonts --reporter=verbose",
    "check:grep:fonts": "bash scripts/check-grep-fonts.sh",
    "verify:build:fonts": "node scripts/verify-build-fonts.mjs",
    "verify:fonts:all": "npm run test:fonts && npm run check:grep:fonts && npm run build && npm run verify:build:fonts"
  },
  "devDependencies": {
    // 保留原有
    "vitest": "^3.0.0",
    "postcss": "^8.4.49"   // 显式声明（虽被 @tailwindcss/postcss 传递依赖，显式锁定更稳）
  }
}
```

## 测试策略（分层）

### 单元层

| 测试目标 | 文件 | 断言点 |
|---|---|---|
| CSS token 值 | `tests/fonts/tokens.test.ts` | AC-1 的 15 个断言（见模块 7.2） |
| postcss 解析工具 | `parse-css.ts` 内联 | 精确 selector 匹配、raw/decls 提取不失真 |

### 集成层

| 测试目标 | 执行方式 | 断言点 |
|---|---|---|
| Next.js 构建成功 | `npm run build` | 退出码 0（兼捕获 `@theme inline` 自引用的循环风险） |
| 字体 @font-face 产物 | `node scripts/verify-build-fonts.mjs` | Inter + JetBrains Mono 各 1 次 font-family 字面量；Source Serif 4 不存在；woff2 ≥ 2 |
| 仓库级合规 | `bash scripts/check-grep-fonts.sh` | 所有 grep 断言通过 |

### 端到端

| 测试目标 | 执行方式 |
|---|---|
| 所有验证一键串联 | `npm run verify:fonts:all` |

**不引入** Playwright / jest-image-snapshot / 视觉回归（interview.md 非目标明示）。

## 回滚策略

单次 `git revert` 即可还原本任务所有改动：
- 新增文件（tests/ + scripts/ + vitest.config.ts）作为一个整体回滚；
- 修改文件（layout.tsx + globals.css + 2 个 chart tick 文件 + 2 个 CLAUDE.md + package.json）均为局部 patch；
- 无数据库迁移、无环境变量改动、无其他 service 耦合。

回滚验证：`cd src/web && npm ci && npm run build` 退出码 0。

## 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| `next/font/google` 在构建机无法访问 Google | 构建失败 | research §8 记录降级方案；§9 声明 CI 外网要求 |
| 97 处 `var(--font-d/u)` 某处拼写错误 | legacy 链断开 | AC-1.5/1.6 断言 + `next build` smoke |
| `@theme inline` 内同名 var 编译循环 | 构建失败 | Tailwind v4 原生支持（详 research §5）；若异常回退展开式 |
| vitest 与 Next.js 16 + Node 20 冲突 | 测试无法运行 | 选 vitest ^3.0（2025 发布，官方支持 Node 18+） |
| postcss AST 无法解析 @tailwindcss 自定义 at-rule | parse 异常 | postcss 对未知 at-rule 默认容忍（treated as AtRule node） |
| CI 无 `rg` 二进制 | grep 脚本报错 | `check-grep-fonts.sh` 起首 `command -v rg` 前置校验 |
| Next.js 静态导出字体路径差异 | `verify-build-fonts.mjs` 找不到 CSS/媒体 | 脚本 `pickRoot()` 在 `.next/` 与 `out/_next/` 之间自动切换 |
