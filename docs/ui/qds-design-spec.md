# QDS Web Application — Design Specification

## 1. Overview

QDS (Quantitative Dashboard System) is a web-based quantitative trading management platform. It provides real-time monitoring, backtesting, strategy management, and data analysis capabilities for algorithmic trading operations.

**Design system:** QDS Warm Minimal  
**Typography:** IBM Plex Mono (data) + IBM Plex Sans (UI)  
**Default theme:** Dark mode  
**Tech stack:** Frontend SPA (React/Vue) + WebSocket real-time data  

---

## 2. App Shell

### 2.1 Layout structure

```
┌──────────────────────────────────────────────────────┐
│  Sidebar (56px collapsed / 220px expanded)           │
│  ┌──────┬─────────────────────────────────────────┐  │
│  │      │  Top bar (48px)                         │  │
│  │  Nav │  ┌───────────────────────────────────┐  │  │
│  │      │  │                                   │  │  │
│  │      │  │  Content area                     │  │  │
│  │      │  │  (scrollable, padded 2rem)         │  │  │
│  │      │  │                                   │  │  │
│  │      │  └───────────────────────────────────┘  │  │
│  │      │  Status bar (28px, optional)            │  │
│  └──────┴─────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

### 2.2 Sidebar

- **Width:** 220px expanded, 56px collapsed (icon-only mode)
- **Background:** `var(--bg-in)` (#141413 dark / #fcfbf8 light)
- **Border:** right 1px `var(--bd)`
- **Toggle:** Collapse button at bottom, smooth 280ms transition
- **Active indicator:** Left 3px accent bar on active nav item (same language as backtest list rows)

**Navigation groups:**

| Group | Items | Icon hint |
|-------|-------|-----------|
| Core | Dashboard, Strategies | Grid, Code |
| Trading | Live Monitor, Backtests | Activity, FlaskConical |
| Data | Data Feeds, Explorer | Database, Search |
| System | Settings, Logs | Gear, Terminal |

**Bottom section (always visible):**
- WebSocket status dot (green=connected, orange=reconnecting, red=disconnected)
- Theme toggle (sun/moon)
- User avatar + initials

### 2.3 Top bar

- **Height:** 48px
- **Background:** `var(--bg-s)` with bottom border `var(--bd)`
- **Left:** Breadcrumb trail (`Dashboard / Backtests / BT-2026-0401`)
- **Right:** Search (Cmd+K shortcut), Notification bell, User dropdown
- **Breadcrumb font:** IBM Plex Mono, 0.75rem, `var(--t2)`

### 2.4 Status bar (optional)

- **Height:** 28px, fixed to bottom
- **Content:** Exchange connections (Binance ● / Hyperliquid ● / OKX ●), latency display, message throughput
- **Background:** `var(--bg-in)`
- **Font:** IBM Plex Mono, 0.68rem

---

## 3. Pages

### 3.1 Dashboard

The landing page. Provides a global overview of all trading operations.

**Layout:**
```
┌─ KPI Strip ──────────────────────────────────────────┐
│  [Today PnL]  [Open PnL]  [Positions]  [Strategies]  │
└──────────────────────────────────────────────────────┘
┌─ Charts (2 col) ─────────────────────────────────────┐
│  [Cumulative PnL - 30d]     [PnL by Strategy]        │
└──────────────────────────────────────────────────────┘
┌─ Activity ───────────────────────────────────────────┐
│  [Recent fills table]   [Running strategies cards]    │
└──────────────────────────────────────────────────────┘
```

**KPI cards:** 
- Counter animation on first load (1400ms easeOutCubic)
- PnL values flash green/red on tick updates (600ms)
- 4-column grid, stat card component

**Charts:**
- Cumulative PnL line chart (strategy color + buy&hold benchmark)
- PnL by strategy horizontal bar chart
- Both use Chart.js with QDS theme config

### 3.2 Backtests (已完成)

See `qds-backtest-full.html` for complete implementation.

**List view:** A (inline progress bar + left accent) + D (expandable detail panel) + E (skeleton for queued)

**Detail view:** 7-tab layout (Overview / Performance / Trades / Robustness / Trade Log / Data Tables / Report)

**Key features:**
- Shimmer sweeps full track width, uses `var(--shimmer)` for theme awareness
- Running tasks: left accent = `var(--info)` (blue), progress bar = `var(--acc)` (orange)
- Done tasks show PnL amount, not Sharpe ratio
- Sticky tab bar with IntersectionObserver `.stuck` class
- Help tooltips (?) on all 34 metrics with Chinese explanations
- Paginated trade log with 20/50/100 rows selector
- Data tables with sub-tabs (Daily / Monthly / By Symbol)

### 3.3 Live Monitor

Real-time trading monitoring page.

**Layout:**
```
┌─ Live KPI Strip ─────────────────────────────────────┐
│  [Unrealized PnL]  [Realized PnL]  [Positions]  [Orders]  │
└──────────────────────────────────────────────────────┘
┌─ Main (2 col, 2:1 ratio) ───────────────────────────┐
│  [Positions table]           [Real-time PnL chart]   │
│  (auto-refresh every tick)   (streaming line chart)   │
├──────────────────────────────────────────────────────┤
│  [Open orders table]         [Recent fills feed]      │
└──────────────────────────────────────────────────────┘
```

**Positions table columns:** Symbol / Side / Size / Entry / Mark / PnL / PnL% / Liq. Price

**Data behavior:**
- Price cells flash on update: green flash if price up, red if down (600ms tick flash)
- PnL values update in real-time via WebSocket
- New fills appear at top of feed with slide-down animation (280ms)

### 3.4 Strategies

Strategy management and configuration.

**List view:** Card grid (2 columns), each card showing:
- Strategy name + version
- Status badge (Running / Stopped / Error)
- Key params: exchange, symbols, timeframe
- Last 7d PnL sparkline
- Actions: Start/Stop toggle, Edit, Clone

**Detail view:**
- Config editor (key-value grid, editable)
- Deploy history timeline
- Performance charts (recent PnL, position sizing)
- Log viewer (terminal-style, mono font, scrollable)

### 3.5 Data

Market data management.

**Sub-pages:**
- **Feeds:** Data source status table (exchange, type, symbols, latency, last update)
- **Explorer:** Query interface for historical data (date range picker, symbol selector, download)
- **Quality:** Data gap detection, tick count charts, anomaly alerts

### 3.6 Settings

System configuration.

**Sections:**
- API keys management (masked display, add/revoke)
- Risk parameters (max position size, daily loss limit, etc.)
- Notification config (Telegram, email, webhook)
- Theme preference (dark/light/system)
- Account info

---

## 4. Design Tokens

### 4.1 Colors

**Dark mode (default):**

| Token | Value | Usage |
|-------|-------|-------|
| `--bg-in` | #141413 | Inputs, code blocks, sidebar |
| `--bg-s` | #262624 | Body background |
| `--bg-p` | #302f2d | Cards, panels |
| `--bg-t` | #3b3a37 | Hover states, active bg |
| `--t0` | #E8E6E0 | Primary text |
| `--t1` | #9C9A92 | Secondary text |
| `--t2` | #73726C | Tertiary text, labels |
| `--t3` | #5F5E5A | Placeholder, disabled |
| `--bd` | #3b3a37 | Borders |
| `--bdh` | #5F5E5A | Hover borders |
| `--acc` | #D97857 | Primary accent (buttons, focus) |
| `--suc` | #36884B | Profit, success, done |
| `--dan` | #FE8181 | Loss, danger, error |
| `--info` | #85B7EB | Info, running state |
| `--warn` | #FAC775 | Warning |
| `--shimmer` | rgba(255,255,255,.35) | Progress bar shimmer |

**Light mode:**

| Token | Value | Notes |
|-------|-------|-------|
| `--bg-in` | #fcfbf8 | Warmest cream |
| `--bg-s` | #faf9f5 | Body (no pure white) |
| `--bg-p` | #f5f4ed | Cards |
| `--bg-t` | #eae8e0 | Hover |
| `--dan` | #8A2425 | Darker red for contrast |
| `--info` | #3266AD | Darker blue |
| `--shimmer` | rgba(255,255,255,.7) | Brighter for visibility |

### 4.2 Typography

| Usage | Font | Weight | Size |
|-------|------|--------|------|
| Data values, prices, IDs | IBM Plex Mono | 400-600 | 0.72-1.35rem |
| UI labels, body text | IBM Plex Sans | 400-700 | 0.68-1.1rem |
| Never use | Inter, Roboto, Arial | — | — |

### 4.3 Spacing

| Token | Value | Usage |
|-------|-------|-------|
| `--r` | 12px | Card border radius |
| `--rs` | 6px | Button, input border radius |
| Card padding | 1rem 1.1rem | Stat cards |
| Section gap | 2rem | Between page sections |
| Grid gap | 1.25rem | Between cards in grid |

---

## 5. Animation

### 5.1 Timing

| Type | Duration | Easing | When |
|------|----------|--------|------|
| Hover | 150ms | ease-out | All interactive elements |
| Enter | 280ms | `cubic-bezier(.16,1,.3,1)` | Panels, modals, toasts |
| Exit | 200ms | ease-in | Closing panels |
| Data tick flash | 600ms | — | Price update highlight |
| Counter | 1400ms | easeOutCubic | KPI first load |
| Chart | 800ms | easeOutQuart | Chart.js animations |
| Shimmer | 2.5s | ease-in-out infinite | Progress bar sweep |
| Tab switch | 300ms | `var(--eo)` | Content fade-up |

### 5.2 Rules

- **Never** use `ease-in-out` (except shimmer)
- **Never** infinite animations (except shimmer, pulse-ring, queued dots)
- **Never** mouse-tracking effects
- Button hover: `translateY(-2px)` + shadow expand
- Button active: `translateY(1px)` + `scale(.98)`
- Modal enter: `scale(.95)` + fade → `scale(1)` + opaque
- Toast enter: slide from right + fade
- Section stagger: each section delays 50ms from previous

### 5.3 Data tick flash

When a value updates via WebSocket:
1. Cell background flashes `var(--suc-d)` (green) or `var(--dan-d)` (red)
2. Fade out over 600ms
3. Only flash if value changed

---

## 6. Component Reference

### 6.1 Stat card (`.sc`)

```css
.sc {
  background: var(--bg-p);
  border: 1px solid var(--bd);
  border-radius: var(--r);
  padding: 1rem 1.1rem;
}
.sc-l { /* label */ font-size: .68rem; color: var(--t2); text-transform: uppercase; }
.sc-v { /* value */ font-family: var(--font-d); font-size: 1.35rem; font-weight: 600; }
```

### 6.2 Chart card (`.cd`)

```css
.cd {
  background: var(--bg-p);
  border: 1px solid var(--bd);
  border-radius: var(--r);
  overflow: hidden;
}
.cd-h { /* header */ padding: .75rem 1rem; border-bottom: 1px solid var(--bd); }
.cd-b { /* body */ padding: 1rem; }
```

### 6.3 Help tooltip (`.help`)

14×14px circle with `?`, hover shows tooltip above with Chinese explanation. Max-width 240px.

### 6.4 ID badge (`.id-badge`)

Inline mono text showing first 8 chars of UUID. Hover shows dashed underline + copy icon. Click copies full ID, shows "✓ copied" for 1.2s.

### 6.5 Status badge (`.status`)

Pill shape, semantic color background + text:
- `.status-run` — `var(--acc-d)` bg + `var(--acc)` text + pulse-ring
- `.status-done` — `var(--suc-d)` bg + `var(--suc)` text
- `.status-fail` — `var(--dan-d)` bg + `var(--dan)` text
- `.status-queue` — `var(--bg-t)` bg + `var(--t2)` text

### 6.6 Pagination (`.pager`)

Bottom bar with:
- Left: "Showing X-Y of Z"
- Center: Page buttons (‹ 1 2 … N ›) with smart ellipsis
- Right: Rows selector (20/50/100)
- Active page: `var(--acc-d)` bg + `var(--acc)` border

### 6.7 Progress bar

**Row bar (3px):** Full-width track with shimmer on `.row-bar`, solid fill on `.row-bar-fill`.
**Detail bar (6px):** `.d-bar-wrap` track + `.d-bar-fill`, shimmer via `.running` class.
Shimmer uses `var(--shimmer)` for theme-aware brightness.

---

## 7. Chart.js Theme Config

```javascript
const bo = () => ({
  responsive: true,
  maintainAspectRatio: false,
  animation: { duration: 800, easing: 'easeOutQuart' },
  interaction: { mode: 'index', intersect: false },
  plugins: {
    legend: { display: false },
    tooltip: {
      backgroundColor: lt() ? '#f5f4ed' : '#302f2d',
      titleColor: cTxt(),
      bodyColor: cTick(),
      borderColor: lt() ? '#dedbd3' : '#3b3a37',
      borderWidth: 1,
      cornerRadius: 8,
      padding: 10,
      bodyFont: { family: "'IBM Plex Mono'" },
      titleFont: { family: "'IBM Plex Mono'", weight: '600' }
    }
  },
  scales: {
    x: {
      grid: { color: cGrid(), drawBorder: false },
      ticks: { color: cTick(), font: { family: "'IBM Plex Mono'", size: 10 }, maxRotation: 0 }
    },
    y: {
      grid: { color: cGrid(), drawBorder: false },
      ticks: { color: cTick(), font: { family: "'IBM Plex Mono'", size: 10 } }
    }
  }
});
```

---

## 8. Responsive breakpoints

| Breakpoint | Layout change |
|------------|---------------|
| > 1200px | Sidebar expanded by default, 5-col stat grid |
| 900-1200px | Sidebar collapsed, 2-col chart grid |
| 600-900px | 2-col stat grid, sidebar hidden (hamburger) |
| < 600px | 1-col everything, mobile nav drawer |

---

## 9. Files produced in this design session

| File | Description |
|------|-------------|
| `qds-warm-theme.css` | Complete CSS design tokens + component classes (1047 lines) |
| `qds-warm.html` | Design system showcase (all components + charts) |
| `qds-warm-v2.html` | Enhanced animation showcase |
| `qds-backtest-progress.html` | 5 progress bar design explorations (A-E) |
| `qds-backtest-manager.html` | A+D+E combined list with auto-ticking progress |
| `qds-backtest-full.html` | Complete backtest system (list + 7-tab detail page) |
| `qds-warm-ui/SKILL.md` | Claude Code skill definition |
| `qds-warm-ui/references/theme.css` | Portable CSS reference |
| `qds-warm-ui/references/charts.md` | Chart.js configurations (15 types) |
| `qds-warm-ui.skill` | Packaged skill file for Claude.ai upload |
