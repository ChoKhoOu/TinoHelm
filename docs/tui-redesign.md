# TinoHelm TUI Redesign — Executive Summary

> Full specification: [`docs/design/tui-bloomberg-retro-spec.md`](design/tui-bloomberg-retro-spec.md)

---

## Design Vision

**Bloomberg Terminal meets CRT Retro Pixel Art** — a keyboard-first, information-dense
quantitative trading dashboard with retro aesthetic personality.

Three pillars:
1. **Bloomberg DNA** — Tiled panels, amber headers, maximum data per pixel, function-key workspaces
2. **Retro Pixel Art** — Block characters, ASCII art logo, CRT scan lines, phosphor glow flash on data updates
3. **Quant Finance UX** — P&L at a glance, 3-tier information hierarchy, real-time pulse

---

## Key Design Decisions

### 1. Workspace Model (replaces tab switching)

| Key | Workspace  | Layout                  | Purpose                        |
|-----|-----------|-------------------------|--------------------------------|
| F1  | DASHBOARD | 4-panel tiled grid      | System overview at a glance    |
| F2  | BACKTESTS | Master-detail split     | Backtest list + live detail    |
| F3  | STRATEGIES| Master-detail split     | Strategy catalog + config      |
| F4  | NODES     | Side-by-side + workers  | Sandbox/Live control           |
| F5  | DATA      | Full-width table        | Data catalog + fetch           |

- Number keys `1-5` also work as aliases for `F1-F5`
- Current `View` enum replaced by `Workspace` enum
- No more page navigation to see details — master-detail views update inline

### 2. Color Palette (Bloomberg-Derived, Full RGB)

```
Amber  #FFB000  — Headers, labels, brand
White  #E6E6E6  — Data values
Green  #00DC50  — Positive (profit, online, completed)
Red    #DC3232  — Negative (loss, offline, failed)
Cyan   #00B4DC  — Navigation hints, IDs, links
Yellow #FFDC00  — Active selection, cursor
Gray   #646464  — Borders, dim/inactive text
Black  #000000  — Background (always)
```

Green/Red are NEVER decorative — always semantic (positive/negative).

### 3. Dashboard (F1) — The Crown Jewel

```
+==========================================================================+
| [TINO]HELM v0.1  F1 DASH  F2 BACK  F3 STRAT  F4 NODE   WS  14:23 UTC |
+========================+=================================================+
| SYSTEM STATUS          | RECENT BACKTESTS                                |
|                        |                                                 |
| Sandbox    Online      | a3f2  Momentum  BTCUSDT  5m   +2.4%           |
| Live       Offline     | b7c1  MeanRev   ETHUSDT  15m  RUN 67%        |
| Workers    3/3         | c9d0  GridBot   BTCUSDT  1h   -0.8%          |
| WS         Connected   | d4e5  Breakout  SOLUSDT  5m   QUEUED         |
+========================+=================================================+
| ACTIVE JOBS                                                              |
| b7c1  MeanRev / ETHUSDT / 15m                                          |
| [============================>                    ]  67%  ETA 45s       |
+==========================================================================+
| EQUITY CURVE (last completed: Momentum / BTCUSDT)                       |
| 12.4k |                                            ...                  |
| 10.0k |  ............                 .........       ...........        |
|       +--------------------------------------------------------------   |
|         Feb 01              Feb 15              Mar 01                   |
+==========================================================================+
| j/k nav | Enter detail | n new | r refresh | F1-F5 workspace | q quit  |
+==========================================================================+
```

### 4. Master-Detail Views (F2/F3) — No More Page Navigation

Selecting an item in the left list instantly updates the right detail panel:

```
+=========================+====================================+
| BACKTEST RUNS           | DETAIL: #a3f2c1                    |
|                         |                                    |
| > a3f2  Momentum  +12% | Strategy:  btc_momentum            |
|   b7c1  MeanRev    67% | Symbol:    BTCUSDT-PERP            |
|   c9d0  GridBot   -2%  | PnL:       +1,234.56 USDT         |
|   d1e5  Breakout  QUE  | Win Rate:  62.5%                   |
|                         | Sharpe:    1.87                    |
|                         | Drawdown:  -4.2%                   |
|                         |                                    |
|                         | EQUITY CURVE                       |
|                         |    .....    ....                   |
|                         | ...    ....    .....               |
+=========================+====================================+
```

### 5. Retro Animation System

| Animation         | Trigger          | Effect                                       |
|-------------------|------------------|----------------------------------------------|
| Boot sequence     | App startup      | Panels appear one by one, logo typewriter     |
| Data flash        | WS value change  | Cell flashes bright white -> amber -> normal  |
| Progress pulse    | Active backtest  | Leading edge of progress bar shimmers         |
| Heartbeat LED     | Node online      | Status dot brightness cycles                  |
| Loading spinner   | API request      | Braille spinner (smooth)              |
| Alert ticker      | Events           | Scrolling marquee at bottom                   |
| CRT scanlines     | Always (toggle)  | Alternating rows slightly dimmed              |

Adaptive tick rate: 100ms during animations, 250ms monitoring, 500ms idle.

### 6. Popup/Modal System

Forms (new backtest, data fetch) rendered as centered overlays:
- Background dashboard visible but dimmed
- Popup has heavy borders (focused style)
- Tab cycles fields, Enter submits, Esc cancels

### 7. Terminal Size Responsiveness

| Size     | Adaptation                                          |
|----------|-----------------------------------------------------|
| < 80x24  | Warning message, single panel only                  |
| 80x24    | Minimal dashboard (2 panels stacked)                |
| 100x30   | Standard dashboard (3 panels), splits available     |
| 120x40+  | Full dashboard (4 panels), all features             |

---

## New Dependencies

```toml
tui-big-text = "0.8"           # ASCII art logo
tui-popup = "0.7"              # Modal overlays
tui-scrollview = "0.6"         # Scrollable panels
throbber-widgets-tui = "0.11"  # Loading spinners
```

## New Module Structure

```
cli/src/tui/
  mod.rs              # Entry + event loop (adaptive tick rate)
  app.rs              # App state (Workspace model)
  ws.rs               # WebSocket client (unchanged)
  theme.rs            # Color palette, styles (Appendix A of full spec)
  animation.rs        # Frame counter, flash tracking, CRT effects
  chrome.rs           # Top bar (logo+tabs+clock), bottom hint bar
  popup.rs            # Modal overlay system
  widgets/
    mod.rs
    status_dot.rs     # Animated status indicators
    progress_bar.rs   # Pixel-art progress bars
    sparkline.rs      # Enhanced braille sparkline
    ticker.rs         # Scrolling alert marquee
    panel.rs          # Reusable retro-bordered panel
    logo.rs           # ASCII art with boot animation
  workspaces/
    mod.rs            # Workspace router
    dashboard.rs      # F1: 4-quadrant overview
    backtest.rs       # F2: master-detail
    strategy.rs       # F3: master-detail
    nodes.rs          # F4: side-by-side + workers
    data.rs           # F5: data catalog
```

## Implementation Phases

| Phase | Scope                                    | Effort |
|-------|------------------------------------------|--------|
| 1     | Foundation: theme + chrome + workspaces  | Medium |
| 2     | Dashboard (F1): 4-panel tiled layout     | Large  |
| 3     | Master-Detail (F2, F3): split views      | Medium |
| 4     | Nodes + Data (F4, F5)                    | Small  |
| 5     | Popups + Forms (modal overlay)           | Medium |
| 6     | Animations + CRT effects                 | Medium |
| 7     | Help system + search/filter              | Small  |

---

## Before vs After

| Aspect             | Current                    | Proposed                          |
|--------------------|----------------------------|-----------------------------------|
| Layout             | Single view, tab switch    | 5 workspaces, tiled panels        |
| Colors             | Basic 16-color             | Full RGB Bloomberg palette        |
| Information density| Low (one view at a time)   | High (dashboard shows 4 panels)   |
| Detail navigation  | Navigate to separate page  | Inline master-detail preview      |
| Animation          | None                       | Boot, flash, pulse, ticker, CRT   |
| Brand presence     | None                       | ASCII logo, amber [TINO]HELM      |
| Keyboard model     | 1/2/3 tabs + j/k           | F-keys + j/k + Tab focus + /search|
| Real-time feedback | WS dot only                | Flash highlights, progress pulse  |
