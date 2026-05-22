---
name: TinoHelmQDS
description: Design system for TinoHelm — a NautilusTrader-powered quantitative trading Web workbench. Warm charcoal surfaces, burnt-orange accent, Inter / JetBrains Mono, Chinese-first copy, Lucide icons. Use this when designing any TinoHelm Web surface (dashboard, backtest, trading terminal, strategies, data catalog, factor research, analytics).
---

# TinoHelm QDS Warm — Agent Skill

## What this system is

TinoHelm 是一个**量化交易工作台**。QDS Warm 是它的 Web 设计语言 —— 参考了 Bloomberg Terminal 的冷峻感，再用 Claude.ai 的暖米色 + 焦橙温度抚平。目标用户是**交易员 / 研究员**，所以第一原则是：**数据就是叙事** —— 数字放大、等宽字体、色彩只用于涨跌/状态，绝不用于装饰。

## How to use this skill

**Every time** you design a TinoHelm Web surface:

1. **Read** `colors_and_type.css` — 这是 tokens 的唯一事实源（CSS custom properties + 语义类）。Include it with a `<link rel="stylesheet">` in every HTML file.
2. **Reference** `Web UI Kit.html` — 单页包含所有组件示例 + 一个端到端的 dashboard frame。复制粘贴其中的 markup，不要重新发明。
3. **Scan** `README.md` 的 **CONTENT FUNDAMENTALS** 和 **VISUAL FOUNDATIONS** 小节 —— 文案基调与视觉规则。
4. **Preview cards** 在 `preview/` 下 —— 当你需要看单个 token/组件的独立示范时使用。

## The non-negotiables

These are the rules that make a design "QDS Warm" instead of "generic warm dark app". Violate any of these and the design is off-brand.

### 1. Two fonts, one discipline
- **Inter** (`--font-sans` / 别名 `--font-u`) — 所有 UI 文本、标题、按钮、正文。开启 `cv11 / ss01 / ss03` 风格集,最接近 Styrene B 的单层 `a`、开口数字、平终端 `g`。
- **JetBrains Mono** (`--font-mono` / 别名 `--font-d`) — **所有数据**:价格、百分比、ID、时间戳、symbol、code、徽章文字。
- 备用 **Source Serif 4** (`--font-serif`) —— 不主动用,留给长文档、证书类页面的场景。
- 中文 fallback 已内置:HarmonyOS Sans SC / PingFang SC / Source Han Sans/Serif SC / Sarasa Mono SC。
- "数据等宽、文本人文"是 QDS 最重要的字体纪律 —— 没有第三种字体,没有例外。
- **在 Next.js / Vite 真实项目里怎么装?** → 看 `fonts/nextjs-setup.md`(用 `next/font/google` 或 `@fontsource` 自托管,不走 CDN)。当前 design system 里的 Google Fonts `@import` 仅为预览方便。

### 2. One accent color
- 焦橙 `#D97857` (`--acc`) 是唯一品牌色。
- 用于：Primary 按钮、active 导航左侧 3px 边框、section label、链接、进度条填充、图表主线、focus ring (`--acc-d` 12% alpha)。
- **绝不**用作装饰色 / 填充 KPI 卡背景 / 点缀图标。少即是多。

### 3. Semantic colors are not decoration
- `--suc` 绿 = 盈利 / 完成。`--dan` 红 = 亏损 / 失败。`--info` 蓝 = 运行中 / 信息。`--warn` 琥珀 = 警告 / 过期。
- 每个都有 `-d` 后缀的 12% alpha 填充版本用于 badge / row 高亮。
- 不要用绿色画"收藏"图标，不要用红色画"删除"按钮的背景 —— 这些颜色在 QDS 里**只表示金融状态**。

### 4. Three surfaces, total
Dark mode: `--bg-s #262624`（body）→ `--bg-p #302f2d`（card）→ `--bg-t #3b3a37`（hover）→ `--bg-in #141413`（sunken · sidebar / input）。四个值，每层差 1–3 luminance。不叠加半透明，不加渐变。

### 5. The 3px accent stripe
QDS 的招牌：列表行**最左侧 3px 垂直色条**，用语义色（绿/蓝/红/灰）表示状态。一眼扫一长串行就能分流。

### 6. The section label
小 caps + accent 橙 + 1px 灰线延伸到边 —— 所有 KPI 卡顶部、所有分节标题都用这个。它是 QDS 的节奏点。
```html
<span class="section-label">Recent Backtests · 最近回测</span>
```

### 7. Chinese-first, English for terms
- 口吻冷静克制，不煽情，不感叹号，不 emoji。
- UI 交互（按钮、tab、dialog 标题）几乎全中文短语："新建策略" "重新扫描" "确认平仓"。
- 术语保留英文：Sharpe、PnL、Drawdown、Equity、Sandbox、Live。
- 模式词 uppercase + letter-spacing：`SANDBOX` / `LIVE` / `ONLINE`。
- 绝不用："哎呀" "空空如也" "你确定要这样做吗？" —— 直接说事实："策略已存在"、"验证失败: {issue}"。

### 8. The wordmark
品牌 Logo 是**文字标记**（no mark icon）。accent 橙色的 `T` 和 `.`：
```html
<span style="font-family: var(--font-d); font-weight: 600;">
  <span style="color: var(--acc);">T</span>ino<span style="color: var(--acc);">.</span>Helm
</span>
```

### 9. Icons
**Lucide only** —— 1.5–2px stroke, 16–18px. Color inherits from parent. Never filled. 状态动作可以用 unicode 代替：`✓ Done` / `✕ Failed` / `◦ Queued` / `⏸ 暂停` / `◼ 停止` / `⇄ 全部平仓`。

### 10. Motion is quiet
- Enter: `cubic-bezier(.16, 1, .3, 1)` (ease-out-expo 感觉)。
- Exit: `cubic-bezier(.4, 0, 1, 1)`。
- 时长：150ms / 280ms / 400ms / 600ms (tick flash) / 1400ms (数字滚动)。
- 签名动画：`qds-fade-up`（opacity + 8px translateY 入场）、`qds-pulse-ring`（running 状态脉冲环）、`qds-shimmer`（进度条扫光）、`qds-tick-g/r`（价格 tick 背景闪一下绿/红）。
- **无 spring / bounce / overshoot。**

## Quick-start template

每个 TinoHelm Web 页面以这个骨架开始：

```html
<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <link rel="stylesheet" href="colors_and_type.css">
  <script src="https://unpkg.com/lucide@latest/dist/umd/lucide.js"></script>
</head>
<body>
  <!-- 220px sidebar · --bg-in 背景 · 3px accent 左边框激活态 -->
  <aside class="sb">...</aside>
  <!-- 主区 · --bg-s 背景 · 顶部 48px topbar + 面包屑 -->
  <main>
    <section>
      <span class="section-label">Dashboard · 概览</span>
      <div class="grid grid-4">
        <div class="kpi">
          <span class="section-label">Total Equity</span>
          <span class="data-value">$1,284,502</span>
          <span class="muted">总权益</span>
        </div>
        <!-- ... 更多 KPI -->
      </div>
    </section>
  </main>
  <script>lucide.createIcons();</script>
</body>
</html>
```

切换 light mode：`<html class="light">`。

## Layout fundamentals

- **Sidebar** 固定 220px（可折叠 56px icon-only），背景 `--bg-in`。
- **TopBar** 48px，底部 1px 分隔线，右侧放 sandbox/live 模式徽章 + 实时 tick。
- **StatusBar** 可选 28px 极薄页脚（node 状态、版本、延迟）。
- **KPI 栅格** 首选 `grid-template-columns: repeat(4, 1fr); gap: 20px`。
- **交易行** `grid-template-columns: 3px 1fr auto auto auto`（3px 状态条 · 主信息 · badge · 时间）。
- 窄页面（表单、设置）用 `max-width: 960px`。

## Copy cheat sheet

| 场景 | QDS | 不要 |
|---|---|---|
| 按钮 | `新建策略` / `重新扫描` / `确认平仓` | `创建一个新的策略` |
| 环境 | `SANDBOX · 模拟环境 · 不会产生真实交易` | `你现在处于安全的沙盒模式中！` |
| 空态 | `还没有回测记录` | `哎呀，这里空空如也~` |
| 危险确认 | `此操作将平掉当前环境所有持仓。确认继续？` | `你确定要这样做吗？这很危险哦` |
| 状态 | `排队中` / `运行中` / `已完成` / `失败` | `等待中~` / `跑着呢` |
| 错误 | `验证失败: missing field 'symbol'` | `哎呀出错啦，请再试一次` |

## Common pitfalls — avoid

- ❌ 加第三种字体（Inter、Roboto、SF Pro... 用 Inter + JetBrains Mono）
- ❌ 用 accent 橙做 KPI 卡背景 / 大面积填充
- ❌ 用绿色/红色表达"新" / "重要"（它们只表金融状态）
- ❌ 加渐变背景、毛玻璃、纹理
- ❌ 加 emoji（`🚀` `✨` `💰` 都 ❌）
- ❌ 正文文案像营销：`"让我们一起…"` / `"超棒的功能"` / 感叹号
- ❌ 数字用 sans-serif（永远 mono）
- ❌ 英文按钮标签（除非是 `Sharpe` / `PnL` 这种术语）
- ❌ 阴影到处加（只有 hover / dialog / primary button 用，值见 README）
- ❌ bounce / spring 动画（改 `--eo` / `--ei`）

## Files to read before you design

Priority order:

1. **`colors_and_type.css`** — always
2. **`Web UI Kit.html`** — scan for the closest component and copy its markup
3. **`README.md`** — the "CONTENT FUNDAMENTALS" and "VISUAL FOUNDATIONS" sections
4. **`preview/*.html`** — only when you need a single-token isolated example
