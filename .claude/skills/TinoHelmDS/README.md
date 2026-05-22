# TinoHelm Design System

**QDS Warm** — the warm, quant-focused design system for **TinoHelm**, a NautilusTrader-powered quantitative trading platform. Bloomberg Terminal rigor, Claude.ai warmth, Chinese-first copy.

---

## Products Covered

| Product | Medium | Core surfaces |
|---|---|---|
| **Web Workbench** (primary) | Next.js 16 + React 19 | Dashboard, Backtest, Trading Terminal, Strategies, Data Catalog, Factor Research, Analytics |
| **Rust TUI / CLI** (companion) | Terminal UI | Same palette spirit; this design system scopes to Web |
| **FastAPI backend** (support) | Python | No visual surface |

## Source of Truth

- **Repository**: `ChoKhoOu/TinoHelm` @ `main`
- **Frontend path**: `src/web/` — Next.js App Router + Tailwind v4 + shadcn/ui (base-nova)
- **Primary token file**: `src/web/src/app/globals.css` (~66KB, all QDS tokens)
- **Frontend architecture notes**: `src/web/CLAUDE.md`
- **Reference mocks**: `docs/ui/qds-warm-v2.html`, `qds-backtest-integrated.html`, `qds-trading-terminal.html`, `qds-data-catalog.html`

---

## Directory

```
.
├── README.md                  ← you are here
├── SKILL.md                   ← Agent entry point
├── colors_and_type.css        ← all CSS tokens + semantic classes (single source of truth)
├── fonts/                     ← Inter / JetBrains Mono (Google Fonts CDN note)
├── Web UI Kit.html            ← single-page component reference + end-to-end app frame
└── preview/                   ← Design System cards (registered via register_assets)
    ├── type-*.html
    ├── color-*.html
    ├── spacing-*.html
    ├── component-*.html
    └── brand-*.html
```

---

## CONTENT FUNDAMENTALS

**Language**: Chinese first. Financial terms stay in English — Sharpe, PnL, Drawdown, Equity, Sandbox, Live, Queue. UI controls (buttons, tabs, dialog titles) are almost all short Chinese phrases.

**Tone**:
- **Cold, restrained, technical.** No exclamation marks, no enthusiasm.
- **Data carries the narrative.** Make numbers large and monospaced. Use green/red, not emoji, to signal up/down.
- **Neutral imperatives** — fewer "you" pronouns, more command forms ("输入策略名称", "确认切换", "重新扫描").
- **Errors are short and factual** — "验证失败: {issue}", "策略已存在". Never sugar-coat.

**Do / Don't**:

| Situation | QDS | Not QDS (avoid) |
|---|---|---|
| Button | `新建策略`, `重新扫描`, `确认平仓` | `创建一个新的策略` |
| Mode banner | `SANDBOX · 模拟环境 · 不会产生真实交易` | `你现在处于安全的沙盒模式中！` |
| Empty state | `还没有回测记录` / `创建回测以验证策略表现` | `哎呀，这里空空如也~` |
| Destructive confirm | `此操作将平掉当前环境所有持仓。确认继续？` | `你确定要这样做吗？这很危险哦` |
| Status labels | `排队中` / `运行中` / `已完成` / `失败` | `等待中~` / `跑着呢` |

**Casing**:
- Chinese body text: no casing rules.
- **English terms get UPPERCASE**: `SANDBOX`, `LIVE`, `ONLINE`, `OFFLINE`, `ALL` — paired with `letter-spacing: .1em–.15em` for a small-caps feel.
- `.section-label` is always uppercase, accent orange, trailing 1px rule. It's the QDS rhythmic marker.

**Emoji**: effectively none. A small set of unicode glyphs acts as inline icons, not decoration: `✓ Done`, `✕ Failed`, `◦ Queued`, and trading actions `⏸ 暂停`, `⇄ 全部平仓`, `◼ 停止`.

**Vibe**: Bloomberg Terminal's austerity, warmed over with Claude.ai's beige/burnt-orange temperature. Professional, not playful.

---

## VISUAL FOUNDATIONS

### Colors
- **Ground**: warm charcoals. Body `#262624`, cards `#302f2d`. Light mode is warm paper: `#faf9f5` / `#f5f4ed`.
- **Accent**: burnt orange **`#D97857`** (`--acc`). The only brand hue. Used for: primary buttons, active-nav 3px left border, `.section-label`, links, progress-bar fill, chart primary line.
- **Semantic (never decorative)**: `--suc #36884B` green (profit/success), `--dan #FE8181` red (loss/failure — dark) / `#8A2425` (light), `--info #85B7EB` blue (running/info), `--warn #FAC775` amber (stale/warn).
- Each semantic color has a **12% alpha fill** variant (`--suc-d`, `--dan-d`, ...) for badges and row tints.
- **Text hierarchy (4 steps)**: `--t0 #E8E6E0` > `--t1 #9C9A92` > `--t2 #73726C` > `--t3 #5F5E5A`.
- **Border hierarchy (3 steps)**: `--bd` → `--bdh` (hover) → `--bds` (strong).

### Type
- **Inter** (`--font-u`) — all UI text, headings, buttons, body.
- **JetBrains Mono** (`--font-d`) — **all data**: prices, percentages, IDs, timestamps, symbols, code, badge content. This is the single most important discipline: "data monospaced, text humanist".
- Body defaults `14px / line-height 1.5`. Headings stay small — `0.9rem–1.35rem`.
- `.section-label` is tiny — `.56rem / letter-spacing .15em / uppercase`, accent orange, with a 1px trailing rule.

### Spacing
- Base grid 4 / 8 px. Common card-grid gap `20px`.
- Card padding: tight `.6–.85rem`, generous `1–1.25rem`.

### Radius
- `--rs: 6px` — buttons, inputs, small controls
- `--rm: 10px` — toasts, dropdowns
- `--r: 12px` — cards, panels (Tailwind `rounded-xl`)
- `14px` — dialogs
- `100px` (pill) — badges

### Backgrounds
- **Gradients are rare.** The only allowed gradient is the Recharts area fill (accent 25% → 0% alpha).
- **No full-bleed illustration, no texture, no hand-drawn.**
- Elevation comes from the 4-step surface ladder (`--bg-s` → `--bg-p` → `--bg-t` → `--bg-in`), each step 1–3 luminance apart.

### Shadow
- **Rarely used.** Exceptions:
  - Card hover: `0 8px 30px rgba(0,0,0,.1)` + `translateY(-2px)`
  - Primary button: `0 2px 8px rgba(217,120,87,.15)` → hover `0 6px 20px rgba(217,120,87,.25)`
  - Dialog: `0 24px 80px rgba(0,0,0,.2)`
  - Dropdown: `0 12px 40px rgba(0,0,0,.15)`
- **Never inner shadow.**

### Motion
- **Easings**: `--eo: cubic-bezier(.16, 1, .3, 1)` for enter (ease-out-expo feel), `--ei: cubic-bezier(.4, 0, 1, 1)` for exit.
- **Durations**: `--dur-fast 150ms` (hover/toggle) < `--dur 280ms` (panel enter) < `--dur-exit 200ms` < `--dur-slow 400ms` (data reveal) < `--dur-tick 600ms` (tick flash) < `--dur-count 1400ms` (number rolling).
- **Signature animations**:
  - `qds-fade-up` — opacity 0→1 + translateY(8→0), used on all enters
  - `qds-dialog-enter` — translateY(30)+scale(.92) → identity, 350ms
  - `qds-pulse-ring` — scale .8→2.2, opacity .7→0, for running-state live rings
  - `qds-tick-g / qds-tick-r` — background flashes green/red for 600ms on price change
  - `qds-shimmer` — progress-bar sheen, 2.5s infinite
- **No bounce, no overshoot, no spring.** Professional and steady.

### Hover / Press / Focus
- **Hover**: links turn accent orange (optional dashed underline); buttons/cards borders upgrade `--bd` → `--bdh`; rows gain a `--bg-t` tint; direction-suggesting icons nudge `translateX(2–3px)` or `translateY(-1–-2px)`.
- **Press**: `translate-y-px scale-[0.98]` for 50ms. Never a "darken" effect.
- **Focus**: inputs gain `border: 1px solid var(--acc)` + `box-shadow: 0 0 0 3px var(--acc-d)`.

### Cards & rows
- Every card: `background: var(--bg-p); border: 1px solid var(--bd); border-radius: 12px`.
- Card headers divide via `border-bottom: 1px solid var(--bd)`, never by background color change.
- List rows divide via `border-bottom`; last row drops it.
- **The 3px left status stripe** is QDS's signature on list rows — green (done), blue (running), red (failed), gray (queued).

### Opacity & blur
- Dialog overlay: `rgba(0,0,0,.4) + backdrop-filter: blur(6px)`.
- ID badges on hover: `border-bottom: 1px dashed`.
- **No frosted-glass cards.**

### Imagery
- No photography. Charts (Recharts / lightweight-charts) use QDS semantic colors via `CHART_COLORS`. Grid lines are very faint: `rgba(255,255,255,.05)`.

### Layout
- **Fixed regions**: Sidebar (56 / 220px collapsible), TopBar (48px), StatusBar (28px — thin).
- Narrow pages (Strategies form, Settings) cap at `max-w-[960px]`.
- Typical grids: `repeat(4, 1fr)` for KPI rows; `3px 1fr auto auto auto` for trade rows with the status stripe.

---

## ICONOGRAPHY

- **Library**: **Lucide React** (already in `package.json`). Linear, 1.5–2px stroke, rendered at 16–18px (Tailwind `size-4` / `size-5`).
- Common set: `LayoutDashboard, FlaskConical, Activity, Brain, Database, BarChart3, Eye, ArrowUpDown, Settings2, Settings, ChevronLeft, ChevronRight, Hexagon, Wallet, TrendingUp, Server, Search, RefreshCw, Plus`.
- Icon color inherits from parent — default `text-muted-foreground`, active `text-primary`.
- **Own SVGs**: `src/web/public/` contains only Next.js defaults (`file.svg`, `globe.svg`, etc.) — **not brand assets**, not imported.
- **Unicode glyphs** act as inline icons for trading actions: `⏸ ⇄ ◼`; statuses: `✓ ✕ ◦ →`. Treated as icons, not decoration.
- **Emoji**: not used.
- **Logo**: the brand mark is a **wordmark** — monospaced `Tino.Helm`, with the leading `T` and the `.` in accent orange:
  ```html
  <span style="font-family: var(--font-d); font-weight: 600;">
    <span style="color: var(--acc);">T</span>ino<span style="color: var(--acc);">.</span>Helm
  </span>
  ```
  No graphic mark. (`Hexagon` from Lucide is occasionally used to represent "factor research", but it is not a brand mark.)

---

## Font Substitution Notice ⚠️

- **Inter** / **JetBrains Mono** both load from Google Fonts CDN — no local TTF required. See `fonts/README.md` for the `@import` line.
- To ship offline, drop `Inter-*.ttf` + `JetBrainsMono-*.ttf` (and optionally `SourceSerif4-*.ttf`) into `fonts/` and we'll swap to local `@font-face` declarations.

---

## Design System Cards

Every HTML file under `preview/` corresponds to a card in the Design System review pane, grouped as:

- **Type** — family, size steps, section label, data typography
- **Colors** — backgrounds (dark + light), accent, semantic states, text hierarchy, borders
- **Spacing** — radius scale, shadow ladder, motion durations & easings
- **Components** — buttons, badges, KPI tiles, list rows, inputs, tabs, progress + live state, sidebar
- **Brand** — wordmark, Lucide icon set

---

## Quick tips for an agent

1. Read `colors_and_type.css` — it *is* the QDS token set.
2. Open `Web UI Kit.html` — every component you'll need is already assembled there; copy markup, don't reinvent.
3. Write Chinese UI; always set data values in `font-mono`; never decorate with semantic green/red.
4. Default to **light mode** (the Web workbench defaults to light for daytime trading). Add `<html class="dark">`… actually wait — current CSS defaults to **dark**, and `.light` class switches to light. Pick intentionally per surface.
5. Icons are Lucide; no emoji.
