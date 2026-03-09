# TinoHelm TUI Design Specification
## Bloomberg-Inspired Retro Pixel Art Terminal Interface

**Version**: 1.0
**Date**: 2026-03-09
**Framework**: Rust + ratatui 0.29 + crossterm 0.28
**Target**: 80x24 minimum, 120x40+ recommended

---

## Table of Contents

1. [Bloomberg Terminal UX Pattern Analysis](#1-bloomberg-terminal-ux-pattern-analysis)
2. [Retro Pixel Art Aesthetic in TUI Context](#2-retro-pixel-art-aesthetic-in-tui-context)
3. [Quant Finance TUI Best Practices](#3-quant-finance-tui-best-practices)
4. [Proposed Layout Architecture](#4-proposed-layout-architecture)
5. [Component Specifications](#5-component-specifications)
6. [Animation & Motion Specification](#6-animation--motion-specification)
7. [Implementation Roadmap](#7-implementation-roadmap)

---

## 1. Bloomberg Terminal UX Pattern Analysis

### 1.1 Information Density Philosophy

Bloomberg Terminal is the gold standard for information-dense financial interfaces. Its core
design philosophy: **maximum data per square inch, zero wasted space, instant recognition**.

Key patterns observed:

**Tiled Panel Architecture**
- The screen is divided into rigid rectangular panels, each serving one atomic purpose
- No overlapping windows in the primary workflow; everything is tiled and visible
- Bloomberg Launchpad (BLP command) allows users to create custom multi-panel "Views"
  with monitors, charts, news feeds, and function shortcuts arranged in a grid
- Panels are "docked" -- they snap to edges and fill available space, never float freely
- Users create multiple Views (workspaces) and switch between them, each tailored to a
  specific workflow (e.g., "Morning Research", "Execution", "EOD Reconciliation")

**Progressive Disclosure of Complexity**
- Bloomberg CTO Shawn Edwards: "We're hiding complexity... so the user has a seamless
  experience through their whole journey within the Terminal"
- Top-level screens show summary data; drilling down reveals granular detail
- Every function has a 2-4 character mnemonic code (e.g., DES for Description, GP for
  Graph Price, ALLQ for All Quotes)
- The command line is always visible at the top -- power users type function codes directly;
  novices browse menus

**Data Table Dominance**
- Tables are the primary data presentation widget -- not cards, not tiles
- Column headers are always visible (sticky headers)
- Rows are dense: minimal vertical padding, single-line entries
- Alternating subtle background shading for row differentiation (not alternating colors --
  just barely perceptible luminance shifts)

### 1.2 Bloomberg Color Language

Bloomberg's color system is one of the most recognizable in professional software. It operates
on a jet-black background with a strict semantic color vocabulary:

```
COLOR           USAGE                               HEX APPROXIMATION
-----------     ----------------------------------  -----------------
Amber/Orange    Headers, function titles, labels     #FF8C00 / #FFA500
White           Primary data values, body text       #FFFFFF / #E0E0E0
Green           Positive values (up, profit, bid)    #00FF00 / #33CC33
Red             Negative values (down, loss, ask)    #FF3333 / #CC0000
Cyan/Blue       Navigation hints, hyperlinks         #00BFFF / #3399FF
Yellow          Active selection, input cursor        #FFFF00 / #FFD700
Dark Gray       Borders, separators, inactive text   #555555 / #666666
Magenta         Alerts, warnings, special flags      #FF00FF / #CC33FF
Black           Background (always)                  #000000 / #0A0A0A
```

**Critical rules:**
- Green and Red are NEVER used decoratively -- they ALWAYS mean positive/negative
- Orange is for structure (headers, labels) -- it tells you "this is a category name"
- White is for content (values, text) -- it tells you "this is actual data"
- The black background is non-negotiable; it reduces eye strain for all-day use and makes
  colored text maximally legible
- Bloomberg also provides CVD (Color Vision Deficiency) alternate schemes for the ~20,000
  users who need them, proving the color system is backed by rigorous accessibility thinking

### 1.3 Keyboard-First Navigation Model

Bloomberg was designed before mouse interfaces were standard. Its keyboard-first model remains
the fastest way to navigate:

**Physical Keyboard Layout (Bloomberg Keyboard 5)**
- **Green keys** (Action): GO (Enter), MENU, END/BACK, HELP, SEARCH
- **Yellow keys** (Market Sectors): GOVT, CORP, MTGE, M-MKT, MUNI, PFD, EQUITY, CMDTY,
  INDEX, CRNCY, PORT -- each opens the top menu for that asset class
- **Red keys** (Stop): ESC, PAUSE, BREAK, LOG-OFF
- **Panel keys**: Cycle between 4 simultaneous Bloomberg windows

**Navigation paradigm:**
- Type a security ticker + yellow market key + GO to load its page
- Type a function mnemonic (e.g., `DES`, `FA`, `GP`) + GO to execute
- HELP key once = contextual help for current screen
- HELP key twice = live human support (famous Bloomberg customer service)
- END/BACK key = return to previous screen (stack-based navigation)
- TAB cycles between panels within a Launchpad view
- Arrow keys navigate within tables and lists

**Command line is always accessible** -- no modal state prevents typing a new command.
This is critical: the user is NEVER trapped in a view.

### 1.4 Real-Time Data Update Patterns

- Data cells update in-place with a brief "flash" highlight (cell background briefly
  shifts to a brighter shade, then fades back)
- Timestamps are omnipresent -- every data panel shows when it was last updated
- Connection status indicators are always visible (similar to our existing WS dot)
- Streaming data uses a "waterfall" pattern in news feeds: newest at top, older items
  push down, oldest items scroll off the bottom
- Price tickers use the "last tick" color: if the last price change was positive, the
  price cell stays green-tinted until the next change

### 1.5 Bloomberg's Grouping Patterns

- **Monitor screens**: Multi-column tables tracking a watchlist of securities. Each column
  is a data field (last price, change, volume, etc.). Rows are securities. Color-coded
  by performance.
- **Analytics panels**: Charts, scatter plots, regression outputs -- always paired with
  the summary statistics table that feeds them.
- **Function groups**: Related functions are clustered under market sector keys. E.g.,
  pressing EQUITY then GO shows: equity news, equity screening, equity analytics, equity
  portfolio tools.
- **Linked components**: In Launchpad, clicking a security in one monitor can update all
  linked panels (chart, news, analytics) to show that security. This "linking" is color-coded
  with colored dots.

---

## 2. Retro Pixel Art Aesthetic in TUI Context

### 2.1 What "Retro Pixel" Means in a Terminal

In a terminal, pixels are characters. A "retro pixel art" aesthetic is achieved through:

**Unicode Block Elements (the pixel palette)**
```
Full blocks:     U+2588  FULL BLOCK
Half blocks:     U+2580  UPPER HALF BLOCK     U+2584  LOWER HALF BLOCK
Quarter blocks:  U+2596-259F  (quadrant characters)
Shade blocks:    U+2591  LIGHT SHADE    U+2592  MEDIUM SHADE    U+2593  DARK SHADE
```

These characters turn every cell into a 2x2 "pixel" grid when using half-blocks, effectively
doubling the vertical resolution of the terminal. This is how ratatui's built-in Sparkline
and Canvas widgets achieve sub-character-cell resolution for charts.

**Box-Drawing Characters (structure)**
```
Single line:   U+250C-2518  (corners, horizontal, vertical)
Double line:   U+2550-256C  (double-line variants)
Heavy line:    U+2501, U+2503, U+250F, U+2513, U+2517, U+251B
Rounded:       U+256D-2570  (rounded corners)
Mixed:         U+2552-2567  (double-horizontal + single-vertical, etc.)
```

**Design choice for TinoHelm**: Use **double-line box drawing** for primary panel borders
(the "heavy chrome" of old hardware), **single-line** for inner subdivisions, and
**rounded corners** sparingly for soft UI elements like form inputs and tooltips.

### 2.2 Pixel Art Headers and Branding

ASCII/Unicode art for the TinoHelm logo and section headers. The logo appears on startup
and in the top-left corner of the dashboard:

```
 _______ _             _   _      _
|__   __(_)           | | | |    | |
   | |   _ _ __   ___ | |_| | ___| |_ __ ___
   | |  | | '_ \ / _ \|  _  |/ _ \ | '_ ` _ \
   | |  | | | | | (_) | | | |  __/ | | | | | |
   |_|  |_|_| |_|\___/|_| |_|\___|_|_| |_| |_|
```

For the compact in-header version (single line, using special characters):
```
 TINO HELM  Quantitative Trading Platform
```

Where the "" characters are constructed from block elements or the brand uses a
distinctive Unicode prefix like:

```
 [TINO]HELM  v0.1.0
```

The `[TINO]` portion rendered in bright amber on dark, `HELM` in dimmer white -- echoing
the Bloomberg style of bright-label + normal-data.

### 2.3 Pixel Art Borders and Chrome

**Panel borders** use double-line box drawing for a "hardware bezel" feel:

```
+=============================================+
||  BACKTEST MONITOR                    [F1] ||
+=============================================+
||  Run ID   | Strategy  | Status | PnL     ||
||------------------------------------------||
||  a3f2..   | Momentum  |  DONE  | +2.4%   ||
||  b7c1..   | MeanRev   |  RUN   | -0.1%   ||
+=============================================+
```

Actual Unicode rendering:

```
 BACKTEST MONITOR                       F1

  Run ID    Strategy    Status    PnL

  a3f2..    Momentum     DONE    +2.4%
  b7c1..    MeanRev      RUN     -0.1%

```

### 2.4 CRT Visual Effects (Achievable in ratatui)

These effects simulate the look of old CRT monitors within terminal constraints:

**Scanline Effect (alternating dim rows)**
- Every other row in a data table gets a slightly dimmer foreground color
- E.g., Row 1 = `Color::White`, Row 2 = `Color::Rgb(200, 200, 200)`
- This creates the visual impression of horizontal scan lines across the display
- Implementation: In the row rendering loop, check `row_index % 2` and apply a
  dimmer style variant

**Phosphor Glow (bright text with dim echo)**
- Active/focused panels have full-brightness text
- Inactive/unfocused panels use dimmed text (e.g., `Color::Rgb(140, 140, 140)`)
- When a panel becomes active, the text "brightens" -- a 2-frame transition from
  dim to full brightness, simulating phosphor warming up
- Recently updated data cells briefly render in a brighter-than-normal color
  (e.g., `Color::Rgb(255, 255, 200)` for white text) then decay back to normal
  over 2-3 frames

**Screen Flicker on Data Updates**
- When new WebSocket data arrives and updates a value, the changed cell gets a
  1-frame inverted highlight (swap fg/bg), then returns to normal on the next frame
- This mimics the brief "bloom" of a CRT when a character changes
- Implementation: Store `last_changed_at: Instant` per cell, and in render check
  if `now - last_changed_at < Duration::from_millis(300)`

**Cursor Blink**
- The active input cursor uses a block character that alternates between the cursor
  color and background on a ~500ms cycle
- In form inputs, the cursor position shows a blinking `U+2588` (full block) character

**Boot Sequence Animation**
- On TUI startup, panels "appear" one by one with a brief delay (100-200ms stagger)
- Each panel border draws left-to-right, top-to-bottom, simulating a CRT warming up
- The logo renders character by character in a typewriter effect
- This plays once on launch, then never again in the session

### 2.5 Retro Typography in Terminal Context

Terminals use monospace fonts -- we cannot change the font. But we CAN control:

- **Case**: Headers in ALL CAPS (like Bloomberg) for a commanding, hardware-panel feel
- **Emphasis**: Bold modifier for headers, dim modifier for secondary info
- **Density**: Minimize blank lines; use single-character separators ( instead of blank rows)
- **Numeric formatting**: Right-align all numbers, use fixed decimal places, use Unicode
  superscript for footnotes
- **Special characters**: Use  for bullet points,  for right-pointing labels,
   for checkmarks,  for alerts

---

## 3. Quant Finance TUI Best Practices

### 3.1 What Professional Quant Platforms Show

Professional trading terminals (Bloomberg, Refinitiv Eikon, KX Dashboards, proprietary
prop-firm tools) display a consistent hierarchy of information:

**Tier 1 -- Always Visible (glanceable)**
- Current positions with unrealized P&L
- Account equity / balance
- System health indicators (connection status, heartbeat)
- Active alerts / risk breaches

**Tier 2 -- One Keypress Away**
- Detailed P&L breakdown (per strategy, per symbol)
- Order book / recent fills
- Performance statistics (Sharpe, drawdown, win rate)
- Equity curve chart

**Tier 3 -- Drill-Down**
- Individual trade details
- Backtest result deep-dive
- Strategy configuration / parameter tuning
- Historical data management

### 3.2 Information Hierarchy for TinoHelm

Mapping the professional quant hierarchy to TinoHelm's domain:

```
TIER 1 (Dashboard -- always visible):
  - Node status (sandbox/live online/offline)
  - Active backtest progress (running jobs with % bar)
  - Last completed backtest summary (strategy, PnL, Sharpe)
  - WebSocket connection status
  - System clock / uptime

TIER 2 (Tab panels -- one keypress):
  - Backtest run table (full history with status coloring)
  - Strategy catalog (all registered strategies with metadata)
  - Node detail (workers, heartbeats, resource usage)

TIER 3 (Detail views -- Enter to drill):
  - Backtest result detail (full statistics table + equity curve)
  - Strategy detail (config fields, parameter descriptions, file path)
  - Backtest form (launch new backtest with parameter entry)
```

### 3.3 Real-Time vs Historical Data Display

**Real-time data** (WebSocket events):
- Use the "flash" update pattern: cell briefly highlights when value changes
- Show a pulsing  indicator next to live-updating panels
- Timestamp every update: "Last update: 2s ago" in the panel footer
- Use streaming layout for progress: horizontal bar fills left-to-right

**Historical data** (API responses):
- Static tables with sort capability
- No flash effects -- data is stable
- Show fetch timestamp: "Loaded at 14:23:05"
- Pagination indicators for long lists: "[1/3] Page  for next"

### 3.4 Alert and Notification Patterns

**Risk breach alerts:**
- Full-width banner at the bottom of the screen (current error_banner pattern is correct)
- Red background with white text for critical alerts
- Yellow background with black text for warnings
- Auto-dismiss after 5 seconds (current behavior) but allow manual dismiss with `x` key
- Alert history accessible via a dedicated key (e.g., `!`)

**Fill notifications:**
- Brief inline flash in the backtest progress row when a run completes
- Sound/bell character (`\x07`) on completion of long-running tasks (optional, configurable)

**System errors:**
- Persistent banner (does NOT auto-dismiss) for connection failures
- Show retry countdown: "WS disconnected. Reconnecting in 5s..."
- Red  indicator in the top bar replaces the green  when disconnected

### 3.5 Color Semantics for Quant Data

```
VALUE TYPE          COLOR           MODIFIER
--------------      -----------     ----------
Positive PnL        Green           Bold
Negative PnL        Red             Bold
Zero / Flat         White           Normal
Running process     Cyan            Normal
Queued / Pending    Yellow          Normal
Completed / OK      Green           Normal
Failed / Error      Red             Bold
Cancelled           Dark Gray       Dim
Header / Label      Amber/Orange    Bold
Border / Chrome     Dark Gray       Normal
Active selection    Yellow bg       Bold
Navigation hint     Cyan            Normal
Input cursor        Yellow          Blink
Timestamp           Dark Gray       Dim
```

---

## 4. Proposed Layout Architecture

### 4.1 View System (Bloomberg-Inspired Workspaces)

Replace the current single-view-with-tabs model with a **workspace** model:

```
WORKSPACES:
  [F1] DASHBOARD  -- Multi-panel overview (HOME)
  [F2] BACKTESTS  -- Backtest list + detail split
  [F3] STRATEGIES -- Strategy catalog + detail split
  [F4] NODES      -- Node status + worker monitoring
  [F5] (future)   -- Live trading monitor
  [F6] (future)   -- Data catalog / market data
```

Function keys switch workspaces (mirroring Bloomberg's yellow market sector keys).
Number keys 1-6 also work as aliases for F1-F6 (preserving current behavior).

The command line at the top always accepts direct input -- typing a backtest run ID
and pressing Enter could jump directly to that backtest's detail view.

### 4.2 Dashboard (F1) -- The Home Screen

This is the most important view. It shows a tiled multi-panel overview of the entire
system at a glance. This is where TinoHelm differentiates from a generic table viewer.

```
Terminal: 120 columns x 40 rows
+==============================================================================+
||  [TINO]HELM v0.1  F1 DASH  F2 BACK  F3 STRAT  F4 NODE       WS  14:23 ||
+==============================================================================+
||                         ||                                                 ||
||   SYSTEM STATUS         ||   RECENT BACKTESTS                              ||
||                         ||                                                 ||
||   Sandbox    Online     ||   a3f2..  Momentum   BTCUSDT  5m   +2.4%      ||
||   Live       Offline    ||   b7c1..  MeanRev    ETHUSDT  15m  RUN 67%    ||
||   Workers    3/3        ||   c9d0..  GridBot    BTCUSDT  1h   -0.8%      ||
||   WS         Connected  ||   d4e5..  Breakout   SOLUSDT  5m   QUEUED     ||
||   Uptime     4h 23m     ||   e6f7..  Scalper    BTCUSDT  1m   FAILED     ||
||                         ||                                                 ||
+==========================++==================================================+
||                                                                            ||
||   ACTIVE JOBS                                                              ||
||                                                                            ||
||   b7c1..  MeanRev / ETHUSDT-PERP / 15m                                   ||
||   [=================================>                    ]  67%  ETA 45s   ||
||                                                                            ||
+==============================================================================+
||                                                                            ||
||   EQUITY CURVE (last completed: Momentum / BTCUSDT)                       ||
||                                                                            ||
||   12.4k |                                                     ..          ||
||   11.8k |                                        ...     ....   .         ||
||   11.2k |                              .........    .....        .        ||
||   10.6k |                 ............                             .       ||
||   10.0k |  ...............                                                 ||
||         +--------------------------------------------------------------    ||
||           Feb 01                  Feb 15                  Mar 01            ||
||                                                                            ||
+==============================================================================+
||  j/k nav | Enter detail | n new | r refresh | F1-F4 workspace | q quit    ||
+==============================================================================+
```

**Panel breakdown:**

| Panel | Position | Size | Content |
|-------|----------|------|---------|
| Header bar | Top row | 1 row full width | Brand + workspace tabs + WS status + clock |
| System Status | Top-left | ~26 cols x 8 rows | Node health, worker count, WS state, uptime |
| Recent Backtests | Top-right | Remaining cols x 8 rows | Last 5 backtests mini-table |
| Active Jobs | Middle | Full width x 4 rows | Running backtest progress bars |
| Equity Curve | Bottom-main | Full width x 10 rows | Sparkline/braille chart of last result |
| Hint bar | Bottom row | 1 row full width | Context-sensitive key hints |

### 4.3 Backtests Workspace (F2) -- List + Detail Split

A master-detail layout: the left panel shows the backtest list, and the right panel shows
the detail of the selected backtest. No need to navigate to a separate detail view --
it updates in real time as the selection changes.

```
+==============================================================================+
||  [TINO]HELM v0.1  F1 DASH  F2 BACK  F3 STRAT  F4 NODE       WS  14:23 ||
+==============================================================================+
||                                    ||                                      ||
||   BACKTEST RUNS            5 runs  ||   DETAIL: a3f2c8d1                   ||
||                                    ||                                      ||
||   ID       Strategy   Status       ||   Strategy:  Momentum                ||
||                               ||   Symbol:    BTCUSDT-PERP             ||
||  >a3f2..   Momentum    DONE       ||   Interval:  5m                       ||
||   b7c1..   MeanRev     RUN  67%   ||   Period:    2025-02-01 > 03-01      ||
||   c9d0..   GridBot     DONE       ||   Status:    completed               ||
||   d4e5..   Breakout    QUEUED     ||                                      ||
||   e6f7..   Scalper     FAIL       ||   STATISTICS                         ||
||                                    ||   Total Trades:        142           ||
||                                    ||   Win Rate:            62.3%         ||
||                                    ||   Sharpe Ratio:        1.84          ||
||                                    ||   Max Drawdown:        -4.2%         ||
||                                    ||   Total PnL:           +$2,412       ||
||                                    ||   Profit Factor:       1.67          ||
||                                    ||                                      ||
||                                    ||   EQUITY CURVE                       ||
||                                    ||    .                    ||
||                                    ||          ....   ....      ||
||                                    ||      ....    ...            ||
||                                    ||   ...                         ||
||                                    ||                                      ||
+=====================================++=======================================+
||  j/k nav | Enter expand | n new | r refresh | Tab switch panel | q quit   ||
+==============================================================================+
```

**Key behaviors:**
- `j`/`k` or Up/Down: Move selection in the left panel
- Right panel updates instantly as selection changes (no Enter needed for preview)
- `Enter`: Expand the detail panel to full screen (replaces the list temporarily)
- `Esc` or `h`: Return to split view from expanded detail
- `Tab`: Switch focus between left and right panels
- `n`: Open backtest form (overlay or replaces right panel)
- `/`: Filter/search backtests by strategy name
- `s`: Cycle sort column (ID, strategy, status, date)

### 4.4 Strategies Workspace (F3) -- Catalog View

```
+==============================================================================+
||  [TINO]HELM v0.1  F1 DASH  F2 BACK  F3 STRAT  F4 NODE       WS  14:23 ||
+==============================================================================+
||                                                                            ||
||   STRATEGY CATALOG                                            4 strategies ||
||                                                                            ||
||   Name             Class              Type        Symbols     Interval     ||
||                                                                       ||
||  >Momentum         MomentumStrategy   single      BTCUSDT     5m          ||
||   MeanReversion    MeanRevStrategy    single      ETHUSDT     15m         ||
||   CryptoMomentum   PortfolioStrat     portfolio   BTC,ETH,SOL 5m          ||
||   GridBot          GridStrategy       single      BTCUSDT     1h          ||
||                                                                            ||
+==============================================================================+
||                                                                            ||
||   DETAIL: Momentum                                                        ||
||                                                                            ||
||   File:     momentum.py                                                   ||
||   Class:    MomentumStrategy                                              ||
||   Config:   MomentumConfig                                                ||
||   Type:     single (implicit portfolio)                                   ||
||                                                                            ||
||   CONFIG FIELDS:                                                          ||
||     fast_period    int     10    Fast MA lookback                          ||
||     slow_period    int     30    Slow MA lookback                          ||
||     risk_pct       float   0.02  Position size as fraction of equity      ||
||                                                                            ||
||   RECENT BACKTESTS (this strategy):                                       ||
||     a3f2..  BTCUSDT  5m   2025-02-01>03-01   DONE   +2.4%              ||
||     f8a9..  BTCUSDT  15m  2025-01-15>02-15   DONE   +1.1%              ||
||                                                                            ||
+==============================================================================+
||  j/k nav | Enter backtest | r rescan | / search | q quit                  ||
+==============================================================================+
```

### 4.5 Nodes Workspace (F4) -- System Health

```
+==============================================================================+
||  [TINO]HELM v0.1  F1 DASH  F2 BACK  F3 STRAT  F4 NODE       WS  14:23 ||
+==============================================================================+
||                              ||                                            ||
||   SANDBOX NODE               ||   LIVE NODE                                ||
||                              ||                                            ||
||    Online                   ||    Offline                                 ||
||   Status:  Running           ||   Status:  Stopped                         ||
||   PID:     48291             ||   PID:     --                              ||
||   Uptime:  4h 23m            ||   Uptime:  --                              ||
||   Restarts: 0                ||   Restarts: --                             ||
||   Last HB: 3s ago            ||   Last HB: never                           ||
||                              ||                                            ||
+=======================================================================+
||                                                                            ||
||   BACKTEST WORKERS                                                        ||
||                                                                            ||
||    Worker 1   PID 48301   Idle                                            ||
||    Worker 2   PID 48302   Running (b7c1.. MeanRev 67%)                    ||
||    Worker 3   PID 48303   Idle                                            ||
||                                                                            ||
+=======================================================================+
||                                                                            ||
||   EVENT LOG (last 10)                                                     ||
||                                                                            ||
||   14:22:41  backtest.progress  b7c1..  67%                                ||
||   14:22:38  node.heartbeat     sandbox                                    ||
||   14:21:15  backtest.progress  b7c1..  55%                                ||
||   14:20:02  backtest.completed a3f2..  completed                          ||
||   14:19:58  backtest.progress  a3f2..  100%                               ||
||                                                                            ||
+==============================================================================+
||  r refresh | q quit                                                        ||
+==============================================================================+
```

### 4.6 Navigation Paradigm Summary

```
NAVIGATION MODEL:

Global (always available):
  F1-F4 / 1-4     Switch workspace
  q                Quit
  Ctrl+C           Force quit
  ?                Show full keybinding help overlay
  /                Global search (filters current view)
  Esc              Close overlay / go back / unfocus

Within list views:
  j / k / Up / Down   Move selection
  Enter               Drill into detail / expand
  h / Esc             Collapse / go back
  Tab                 Switch panel focus (in split views)
  r                   Refresh data
  n                   New (backtest form)
  s                   Cycle sort column
  /                   Filter by text

Within forms:
  Tab / Shift+Tab     Next / previous field
  Enter               Submit
  Esc                 Cancel and close

Within detail views:
  Esc / h             Return to list
  r                   Refresh
  PageUp / PageDown   Scroll long content
```

---

## 5. Component Specifications

### 5.1 Header Bar Component

The header bar is a single row that is always visible across all workspaces.

```
 [TINO]HELM v0.1  F1 DASH  F2 BACK  F3 STRAT  F4 NODE       WS  14:23:05
```

**Composition:**
- Left: Brand mark `[TINO]HELM` in amber bold + version in dim gray
- Center: Workspace tabs. Active tab is bright amber bold. Inactive tabs are dark gray.
  The function key prefix (F1, F2...) is rendered in cyan.
- Right: WebSocket status indicator ( green / yellow / red) + label + system clock
  in dim white, updating every second

**ratatui implementation:**
- Single `Line` with `Span` segments
- The clock updates on every tick (250ms is fine; second-resolution display)
- Background: `Color::Rgb(15, 15, 15)` -- very slightly lighter than pure black,
  giving a subtle "bezel" feel

### 5.2 Data Table Component (Bloomberg Monitor Style)

The primary data display widget. Used for backtests, strategies, and any list.

**Visual spec:**
```
 BACKTEST RUNS                                                     5 runs
  ID        Strategy       Symbol         Interval   Status     PnL

 >a3f2c8d1  Momentum       BTCUSDT-PERP   5m          DONE    +2.41%
  b7c1e2f3  MeanReversion  ETHUSDT-PERP   15m         RUN 67%  -0.12%
  c9d0a1b2  GridBot        BTCUSDT-PERP   1h          DONE    -0.83%
  d4e5f6a7  Breakout       SOLUSDT-PERP   5m         QUEUED     --
  e6f7a8b9  Scalper        BTCUSDT-PERP   1m         FAILED     --

```

**Rules:**
- Header row: Amber text, bold, on slightly raised background `Color::Rgb(25, 25, 25)`
- Column separator: Single-character vertical line in dark gray, or implicit spacing
- Selected row: Amber/yellow foreground on dark blue-gray background `Color::Rgb(20, 30, 50)`
- Scanline effect: Even rows use `Color::Rgb(230, 230, 230)`, odd rows use `Color::Rgb(200, 200, 200)` for data text
- Status column: Semantic coloring (green/red/cyan/yellow per 3.5 spec)
- PnL column: Green for positive, red for negative, right-aligned, fixed 2 decimal places
- Row count shown in the panel title bar, right-aligned
- The `>` cursor indicator in the leftmost column, amber colored

### 5.3 Sparkline / Equity Curve Component

For the equity curve chart, use braille characters (U+2800-28FF) for higher resolution
than the built-in Sparkline widget, or use the ratatui Canvas widget with BrailleGrid.

**Visual spec:**
```
 EQUITY CURVE  Momentum / BTCUSDT-PERP / 2025-02-01 > 03-01

 12.4k                                              ..
 11.8k                                  ...     ....  .
 11.2k                         ........    .....       .
 10.6k               .........                          .
 10.0k  ..............
        +--------------------------------------------------
        Feb 01           Feb 15           Mar 01

```

**Rules:**
- Chart line color: Cyan for the primary equity line
- Y-axis labels: Right-aligned, dim gray, using k/M suffixes for thousands/millions
- X-axis labels: Centered under tick marks, dim gray
- Grid dots: Very dim gray dots at regular intervals for orientation
- If the final equity > starting equity, the line is green; if below, red
- A horizontal dashed reference line at the starting capital level (dim yellow)
- The chart area uses period/braille characters, NOT the built-in Sparkline block style

### 5.4 Progress Bar Component

For active backtests:

```
 b7c1..  MeanRev / ETHUSDT-PERP / 15m
 [=================>                         ]  67%  ETA 45s
```

**Rules:**
- The bar uses `=` fill with `>` as the leading edge, ` ` for empty space
- Percentage in cyan, right of the bar
- ETA in dim gray (calculated from elapsed time and current percentage)
- When at 100%, the bar turns green and shows "DONE" instead of ETA
- When failed, the bar turns red and shows "FAILED" with the error snippet

Alternative Unicode progress bar for a more pixel-art feel:
```
 [                    ]  67%
```
Using block elements: `` for filled, `` for half, ` ` for empty.

### 5.5 Status Indicator Component

Node status uses large-character indicators:

```
  Online     Offline     Stale
```

The dot, color, and text label work together. Additionally, a subtle "pulse" animation
on the Online indicator: the  alternates between bright green and slightly dimmer green
on a 1-second cycle, simulating a heartbeat LED.

### 5.6 Form Input Component

Backtest form inputs with a retro terminal feel:

```
 STRATEGY __________________________________________________
 SYMBOL   [ BTCUSDT-PERP                                   ]
 INTERVAL [ 5m                                              ]
 START    [ 2025-02-01                                      ]
 END      [ 2025-03-01                                      ]
```

**Rules:**
- Active field: Amber border, amber label, white text, blinking block cursor
- Inactive field: Dark gray border, dark gray label, dim white text
- The field label is OUTSIDE the box, to the left (Bloomberg style)
- The input area has a subtle background `Color::Rgb(10, 10, 10)`
- Tab key indicator shown as small arrows between fields
- Validation errors appear inline below the field in red

### 5.7 Error/Alert Banner Component

```
  ALERT  WS disconnected. Reconnecting in 5s...                          x
```

```
  ERROR  Failed to submit backtest: strategy not found                    x
```

```
  INFO   Backtest a3f2.. completed successfully. PnL: +$2,412            x
```

**Rules:**
- Full-width, single row at the bottom of the screen (above the hint bar)
- ALERT: Yellow background, black text
- ERROR: Red background, white text
- INFO: Cyan background, black text
- `x` key dismisses, or auto-dismiss after 5s for INFO, 10s for ALERT, never for ERROR
- Multiple alerts stack (newest on top, max 3 visible)

---

## 6. Animation & Motion Specification

### 6.1 Frame-Based Animation System

ratatui redraws the entire screen on every frame. Animations are achieved by changing
state between frames. The current tick rate is 250ms (4 FPS). For smooth animations,
increase to 60-100ms ticks (10-16 FPS) during animation sequences, then drop back to
250ms for idle state.

### 6.2 Animation Catalog

| Animation | Trigger | Duration | Frames | Description |
|-----------|---------|----------|--------|-------------|
| Boot sequence | App startup | 1.5s | 15 @ 100ms | Panels appear one by one, logo typewriter |
| Data flash | WS value change | 300ms | 3 @ 100ms | Cell bg: highlight > bright > normal |
| Progress pulse | Active backtest | Continuous | 4 @ 250ms | Leading edge of progress bar animates |
| Heartbeat LED | Node online | Continuous | 2 @ 1000ms | Status dot brightness cycles |
| Cursor blink | Form input focus | Continuous | 2 @ 500ms | Block cursor on/off |
| Panel focus | Tab switch | 200ms | 2 @ 100ms | Border brightens, text brightens |
| Error slide-in | Error event | 200ms | 2 @ 100ms | Banner appears from bottom edge |
| Completion flash | Backtest done | 500ms | 5 @ 100ms | Row flashes green then settles |
| Loading spinner | API request | Continuous | 4 @ 200ms | Braille spinner  |

### 6.3 Tick Rate Strategy

```rust
// Adaptive tick rate based on animation state
fn current_tick_rate(app: &App) -> Duration {
    if app.has_active_animations() {
        Duration::from_millis(100)  // 10 FPS during animations
    } else if app.has_running_backtests() {
        Duration::from_millis(250)  // 4 FPS when monitoring progress
    } else {
        Duration::from_millis(500)  // 2 FPS when idle (save CPU)
    }
}
```

### 6.4 Loading Spinner Variants

```
Braille spinner (smooth):
Block spinner (chunky):
Line spinner (retro):        | / - \ | / - \
Dot bouncer:                 .  ..  ... ..  .
Bar scanner:                 [=   ] [ =  ] [  = ] [   =] [  = ] [ =  ]
```

Prefer the **braille spinner** for inline loading indicators and the **bar scanner**
for full-width loading states.

---

## 7. Implementation Roadmap

### Phase 1: Foundation (Core Architecture)

**Goal**: Establish the workspace system, header bar, and Bloomberg color palette.

- [ ] Define color constants module (`tui/theme.rs`) with all Bloomberg-derived colors
- [ ] Implement workspace enum and navigation (F1-F4 switching)
- [ ] Build the header bar component with brand, tabs, WS status, clock
- [ ] Build the hint bar component with context-sensitive keybindings
- [ ] Implement adaptive tick rate system

### Phase 2: Dashboard View (F1)

**Goal**: Build the multi-panel dashboard home screen.

- [ ] Layout engine for the 4-panel dashboard (status, recent, jobs, equity)
- [ ] System status panel with node indicators and heartbeat pulse
- [ ] Recent backtests mini-table (top 5, compact format)
- [ ] Active jobs panel with progress bars
- [ ] Equity curve chart using Canvas/BrailleGrid widget

### Phase 3: Backtest Workspace (F2)

**Goal**: Master-detail split layout for backtests.

- [ ] Split-panel layout with adjustable ratio
- [ ] Bloomberg-style data table with scanline effect
- [ ] Live detail panel that updates on selection change
- [ ] Statistics table with proper number formatting
- [ ] Inline equity sparkline in detail panel
- [ ] Backtest form overlay with retro input styling

### Phase 4: Strategy & Node Workspaces (F3, F4)

**Goal**: Complete the remaining workspaces.

- [ ] Strategy catalog table with type/class columns
- [ ] Strategy detail panel with config field listing
- [ ] Node status split view (sandbox/live side by side)
- [ ] Worker status table with PID and activity
- [ ] Event log panel with scrolling WebSocket events

### Phase 5: Animation & Polish

**Goal**: Add all CRT/retro visual effects.

- [ ] Boot sequence animation
- [ ] Data flash on WebSocket updates
- [ ] Heartbeat pulse on status indicators
- [ ] Cursor blink in form inputs
- [ ] Loading spinners for API requests
- [ ] Panel focus transitions
- [ ] Scanline rendering in tables

### Phase 6: Advanced Features

**Goal**: Power-user features inspired by Bloomberg.

- [ ] `/` search/filter across all views
- [ ] `?` full keybinding help overlay
- [ ] Sort cycling with `s` key
- [ ] Alert history view
- [ ] Keyboard-driven panel resize (Shift+Arrow)
- [ ] Command input bar (type backtest ID to jump to detail)

---

## Appendix A: Color Palette Reference

```rust
// tui/theme.rs -- Bloomberg-inspired retro palette

pub mod colors {
    use ratatui::style::Color;

    // Backgrounds
    pub const BG_PRIMARY: Color    = Color::Rgb(0, 0, 0);        // Pure black
    pub const BG_PANEL: Color      = Color::Rgb(8, 8, 8);        // Panel interior
    pub const BG_HEADER: Color     = Color::Rgb(15, 15, 15);     // Header bar
    pub const BG_SELECTED: Color   = Color::Rgb(20, 30, 50);     // Selected row
    pub const BG_INPUT: Color      = Color::Rgb(10, 10, 10);     // Form input
    pub const BG_ERROR: Color      = Color::Rgb(180, 30, 30);    // Error banner
    pub const BG_WARN: Color       = Color::Rgb(180, 150, 0);    // Warning banner
    pub const BG_INFO: Color       = Color::Rgb(0, 120, 150);    // Info banner

    // Foreground -- structure
    pub const FG_AMBER: Color      = Color::Rgb(255, 176, 0);    // Headers, labels
    pub const FG_BORDER: Color     = Color::Rgb(60, 60, 60);     // Panel borders
    pub const FG_BORDER_ACTIVE: Color = Color::Rgb(120, 120, 120); // Active panel border
    pub const FG_DIM: Color        = Color::Rgb(100, 100, 100);  // Inactive text, timestamps
    pub const FG_HINT: Color       = Color::Rgb(0, 180, 220);    // Key hints, navigation

    // Foreground -- data
    pub const FG_PRIMARY: Color    = Color::Rgb(230, 230, 230);  // Primary data text
    pub const FG_SECONDARY: Color  = Color::Rgb(180, 180, 180);  // Secondary data (scanline)
    pub const FG_BRIGHT: Color     = Color::Rgb(255, 255, 255);  // Emphasized data

    // Foreground -- semantic
    pub const FG_POSITIVE: Color   = Color::Rgb(0, 220, 80);     // Profit, online, completed
    pub const FG_NEGATIVE: Color   = Color::Rgb(220, 50, 50);    // Loss, offline, failed
    pub const FG_RUNNING: Color    = Color::Rgb(0, 200, 220);    // In-progress, active
    pub const FG_QUEUED: Color     = Color::Rgb(220, 200, 0);    // Pending, waiting
    pub const FG_CANCELLED: Color  = Color::Rgb(80, 80, 80);     // Cancelled, disabled

    // Special
    pub const FG_CURSOR: Color     = Color::Rgb(255, 220, 0);    // Input cursor
    pub const FG_FLASH: Color      = Color::Rgb(255, 255, 200);  // Data update flash
    pub const FG_LOGO: Color       = Color::Rgb(255, 140, 0);    // Brand amber
}
```

## Appendix B: Box-Drawing Character Reference

```
PRIMARY PANEL BORDERS (double-line):
  Top-left:     U+2554
  Top-right:    U+2557
  Bottom-left:  U+255A
  Bottom-right: U+255D
  Horizontal:   U+2550
  Vertical:     U+2551

INNER DIVIDERS (single-line):
  Horizontal:   U+2500
  Vertical:     U+2502
  Cross:        U+253C
  T-down:       U+252C
  T-up:         U+2534
  T-right:      U+251C
  T-left:       U+2524

HEADER UNDERLINE (heavy):
  Horizontal:   U+2501
  T-down:       U+2533

SPECIAL:
  Bullet:       U+2022
  Arrow right:  U+25B6
  Arrow down:   U+25BC
  Diamond:      U+25C6
  Circle full:  U+25CF  (status online)
  Circle half:  U+25D0  (status stale)
  Circle empty: U+25CB  (status offline)
```

## Appendix C: Comparison -- Current vs Proposed

```
ASPECT              CURRENT                     PROPOSED
-----------         -------------------------   --------------------------------
Layout              Single view, tab switching   Workspace model (F1-F4), tiled
Color palette       Basic 16-color (Yellow,      Full RGB palette, Bloomberg-derived
                    Green, Red, Cyan, DarkGray)  amber/semantic system
Borders             Single-line Block borders    Double-line for panels, single for
                                                 inner dividers
Information density Low (one view at a time)     High (dashboard shows 4 panels)
Detail navigation   Navigate away to detail      Split-panel preview, Enter to expand
Animation           None                         Boot sequence, data flash, heartbeat
                                                 pulse, cursor blink, loading spinners
Real-time updates   WS dot only                  Flash highlights, event log, progress
                                                 pulse, live timestamps
Keyboard model      Number keys + j/k/Enter      F-keys for workspaces, j/k for nav,
                                                 Tab for panel focus, / for search
Retro aesthetic     None                         Scanline rows, phosphor glow,
                                                 block-element borders, CRT boot
Brand presence      None                         ASCII logo, amber [TINO]HELM header
```

## Appendix D: Terminal Size Responsiveness

The TUI must degrade gracefully at different terminal sizes:

```
SIZE          LAYOUT ADAPTATION
----------    --------------------------------------------------
< 80x24       Single panel only, no splits, compact tables
80x24         Minimal dashboard (2 panels stacked), basic tables
100x30        Standard dashboard (3 panels), split views available
120x40+       Full dashboard (4 panels), all features enabled
160x50+       Extra-wide: wider table columns, larger charts
```

At very small sizes, show a warning: "Terminal too small. Minimum 80x24 recommended."

---

*End of Design Specification*
