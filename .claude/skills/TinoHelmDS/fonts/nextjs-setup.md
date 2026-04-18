# Next.js / Vite 接入指南（字体）

> **本文档用途**：当 QDS 落到真实代码库（Next.js App Router / Vite）时，如何正确配置 `Inter + Source Serif 4 + JetBrains Mono + 中文 fallback`，让 Google Fonts CDN 的 `@import` 升级为自托管、零布局偏移、国内可用的生产级方案。
>
> **design system 这边（`colors_and_type.css`）不用动** —— 它定义 `--font-sans / --font-serif / --font-mono` 三个 token，接入方只负责把浏览器能找到的字体资源注入到对应变量。

---

## 一、Next.js App Router（推荐）

### 1. `app/layout.tsx`

```tsx
import {
  Inter,
  Source_Serif_4,
  JetBrains_Mono,
  Noto_Sans_SC,
  Noto_Serif_SC,
} from 'next/font/google'
import './globals.css'

// Inter —— 替代 Styrene B
const inter = Inter({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-inter',
  display: 'swap',
})

// Source Serif 4 —— 替代 Copernicus
const sourceSerif = Source_Serif_4({
  subsets: ['latin'],
  weight: ['400', '600', '700'],
  variable: '--font-source-serif',
  display: 'swap',
})

// JetBrains Mono —— 数据 / 代码
const jetbrainsMono = JetBrains_Mono({
  subsets: ['latin'],
  weight: ['400', '500', '700'],
  variable: '--font-jetbrains-mono',
  display: 'swap',
})

// Noto Sans SC —— 中文 sans（= Source Han Sans SC 的 Google Fonts 发行名）
const notoSansSC = Noto_Sans_SC({
  weight: ['400', '500', '700'],
  variable: '--font-noto-sans-sc',
  display: 'swap',
  preload: false, // 中文字体体积大，不 preload，按需加载
})

// Noto Serif SC —— 中文 serif（= Source Han Serif SC）
const notoSerifSC = Noto_Serif_SC({
  weight: ['400', '600', '700'],
  variable: '--font-noto-serif-sc',
  display: 'swap',
  preload: false,
})

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="zh-CN"
      className={`${inter.variable} ${sourceSerif.variable} ${jetbrainsMono.variable} ${notoSansSC.variable} ${notoSerifSC.variable}`}
    >
      <body>{children}</body>
    </html>
  )
}
```

### 2. `app/globals.css`

把 QDS 的 `colors_and_type.css` 里三个 token 的值，改为引用 Next.js 注入的 CSS 变量：

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

/* 如果项目已经 @import 了 colors_and_type.css，可以在后面覆盖三个 token；
   如果是新项目直接 copy QDS，则把 :root 里的 --font-sans/serif/mono 改成以下形式。 */
@layer base {
  :root {
    --font-sans:  var(--font-inter),         'HarmonyOS Sans SC', 'PingFang SC', var(--font-noto-sans-sc), system-ui, sans-serif;
    --font-serif: var(--font-source-serif),  'Source Han Serif SC', var(--font-noto-serif-sc), Georgia, serif;
    --font-mono:  var(--font-jetbrains-mono), 'Sarasa Mono SC', ui-monospace, monospace;

    /* 保留旧别名 */
    --font-u: var(--font-sans);
    --font-d: var(--font-mono);
  }

  body {
    font-family: var(--font-sans);
    /* Inter 的"类 Styrene"风格集：cv11 单层 a、ss01 开口数字、ss03 平终端 g */
    font-feature-settings: 'cv11', 'ss01', 'ss03';
  }

  /* 数字列对齐（QDS 数据铁律） */
  .tabular,
  [class*="font-mono"],
  code, pre {
    font-variant-numeric: tabular-nums;
    font-feature-settings: 'tnum', 'zero';
  }
}
```

### 3. `tailwind.config.ts`

让 `className="font-sans/serif/mono"` 直接映射到 QDS token：

```ts
import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans:  ['var(--font-sans)'],
        serif: ['var(--font-serif)'],
        mono:  ['var(--font-mono)'],
      },
    },
  },
  plugins: [],
}
export default config
```

### 4. Next.js 自动帮你做的事

- **自动下载** — 构建时从 Google Fonts 拉取，打进 `.next/static/media/`
- **自动 self-host** — 运行时走你自己的域名，国内无障碍
- **自动子集化** — 只打包声明的 subset + weight
- **自动 preload** — `display: swap` + `<link rel="preload">`，消 FOUT / FOIT
- **自动生成 CSS 变量** — `inter.variable` 即 `--font-inter`
- **零布局偏移** — 内置 `size-adjust` fallback 字体匹配

---

## 二、Vite / 非 Next.js 项目

用 `@fontsource`，零配置：

```bash
npm i \
  @fontsource/inter \
  @fontsource/source-serif-4 \
  @fontsource/jetbrains-mono \
  @fontsource/noto-sans-sc \
  @fontsource/noto-serif-sc
```

```ts
// main.ts / main.tsx
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/inter/700.css'
import '@fontsource/source-serif-4/400.css'
import '@fontsource/source-serif-4/600.css'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/500.css'
import '@fontsource/noto-sans-sc/400.css'
import '@fontsource/noto-sans-sc/500.css'
```

然后删掉 `colors_and_type.css` 顶部的 `@import url('https://fonts.googleapis.com/...')` 一行 —— 字体已经打包进本地 bundle。

---

## 三、shadcn/ui 用户

shadcn 默认读的就是 `--font-sans` / `--font-mono`，本文档的 token 命名**完全对齐**，零改动接入。

---

## 四、为什么 Sarasa Mono SC 留在 fallback

- Next.js / Fontsource 都只处理 Google Fonts 里的字体；Sarasa 不在
- 写在 `font-family` 链里**无副作用**：本地装了的开发者生效，没装的自动回退到下一个
- 对中文等宽数据对齐（K 线下方的时间戳、订单簿的价格列）体验显著提升

---

## 五、在 design system 源文件（当前项目）里

**不要**把上面这些 Next.js 特定代码写进 `colors_and_type.css` —— 它是 token 定义层，必须保持框架无关。

当前项目里的字体加载方式是 **Google Fonts CDN 直接 `@import`**，仅为 design system 预览用。真实落地走本文档的方案。

如果你想在当前 design system preview 里也模拟生产环境字体加载，可以把顶部的 `@import` 换成本地 `@font-face` + 上传 `fonts/*.ttf`，但这不是必须的。
