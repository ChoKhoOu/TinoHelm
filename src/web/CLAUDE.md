# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
npm run dev      # Dev server on :3000
npm run build    # Static export to out/ (next export)
npm run lint     # ESLint
```

This is a statically exported Next.js app (`output: "export"` in next.config.ts). There are no server components with data fetching — all API calls happen client-side.

## Architecture

### Stack
- **Next.js 16** (App Router, static export) + **React 19**
- **Tailwind CSS v4** (CSS-based config via `@theme inline` in globals.css, no tailwind.config.ts)
- **shadcn/ui v4** with `base-nova` style (uses `@base-ui/react` primitives, NOT Radix)
- **Recharts** for charts, **framer-motion** for animations, **lightweight-charts** for candlestick
- **@tanstack/react-table** for complex tables

### Layout (layout.tsx)
```
<html className="h-full">          ← .light class toggles light mode
  <body>
    <Providers>                     ← WebSocket context
      <Sidebar />                   ← Left nav
      <TopBar />                    ← Breadcrumb + actions
      <main><PageTransition>{children}</PageTransition></main>
      <StatusBar />                 ← Bottom: exchange dots, mem/cpu, clock
      <Toaster />                   ← sonner toast
    </Providers>
  </body>
</html>
```

### API Layer
- `lib/api.ts`: `apiGet`, `apiPost`, `apiPut`, `apiDelete` — thin fetch wrappers calling the FastAPI backend at `/api/*`
- `providers/WebSocketProvider.tsx`: Connects to `/ws/events`, provides `useWsEvent(eventType)` hook for real-time updates
- WS event payloads are flat JSON (no `data` wrapper). Use `(msg.data ?? msg)` as fallback pattern.

### Design System — QDS Warm

**Two-layer token system** in `globals.css`:
1. **QDS short tokens** (`:root`): `--bg-p`, `--t0`, `--acc`, `--suc`, `--dan` etc. — the source of truth
2. **shadcn oklch variables**: `--background`, `--primary`, `--destructive` etc. — mapped from QDS tokens, consumed by Tailwind utilities

**Tailwind class mapping** (via `@theme inline`):
| QDS Token | Tailwind Class | Usage |
|-----------|---------------|-------|
| `--bg-p` (cards) | `bg-card` | Card backgrounds |
| `--bg-s` (body) | `bg-background` | Page background |
| `--bg-t` (hover) | `bg-secondary` | Hover states, skeletons |
| `--bg-in` (sunken) | `bg-input` | Form inputs, code blocks |
| `--t0` | `text-foreground` | Primary text |
| `--t2` | `text-muted-foreground` | Tertiary text |
| `--t1` | `text-qds-t1` | Secondary text |
| `--t3` | `text-qds-t3` | Quaternary text |
| `--bd` | `border` (default) | Default border color |
| `--bdh` | `border-qds-border-hover` | Hover border |
| `--acc` | `text-primary` / `bg-primary` | Accent (burnt orange) |
| `--suc` | `text-qds-success` | Green (profit, positive) |
| `--dan` | `text-destructive` | Red (loss, negative) |
| `--info` | `text-qds-info` | Blue (informational) |
| `--warn` | `text-qds-warning` | Amber (warnings) |
| dim variants | `bg-qds-success-dim`, `bg-qds-danger-dim` etc. | Subtle backgrounds |

**Prefer Tailwind classes** over inline `var()` or arbitrary values. Use `bg-card` not `bg-[var(--bg-p)]`.

**Color semantic rules**: Green/Red are NEVER decorative — always semantic (profit/loss, success/fail).

**Dark/light mode**: Dark is default. Light mode via `html.light` class (toggled by `ThemeToggle`, persisted in localStorage). QDS tokens and shadcn variables both have light overrides.

### Component Organization

```
components/
├── ui/          ← shadcn primitives (button, card, dialog, table, tooltip...)
│                  NOTE: Tooltip uses @base-ui (delay, no asChild), NOT Radix
├── qds/         ← QDS business components (StatCard, ShimmerBar, StatusBadge,
│                  HelpTip, PageHeader, SectionLabel) — barrel export via index.ts
├── motion/      ← FadeIn, StaggerContainer, PageTransition (framer-motion)
└── *.tsx        ← App-level: Sidebar, TopBar, StatusBar, ErrorBoundary, etc.
```

### Chart Theme
`lib/chartTheme.ts` exports shared constants for Recharts:
- `CHART_TOOLTIP_PROPS` — spread on `<RechartsTooltip>` for consistent styling
- `CHART_AXIS_STYLE`, `CHART_GRID_STYLE` — axis and grid configuration
- `CHART_COLORS` — semantic color map using CSS variables
- `CHART_GRADIENT_OPACITY` — standardized fill opacities (equity: 0.07, area: 0.12)
- `CHART_ANIMATION` — duration/easing defaults

Recharts components use `var()` CSS variables directly in props (fill, stroke, stopColor) — this is correct and should NOT be converted to Tailwind classes.

### Animation
- QDS Tailwind animations: `animate-qds-fade-up`, `animate-qds-slide-in`, `animate-qds-shimmer`, `animate-qds-pulse`, `animate-qds-tick-g/r`
- QDS easing: `ease-qds` (enter), `ease-qds-exit` (exit)
- framer-motion `FadeIn` component for page section entry
- Hover: `transition-colors duration-150 ease-qds`

### Page Structure
Each page under `app/` is a standalone route. Complex pages split tabs into `components/tabs/`:
- **trading/**: `OverviewTab`, `RiskTab`, `StrategiesTab`, `OrdersTab`, `LogsTab` + `ActionBar`, `StrategyDetailPanel`
- **backtest/**: List↔Detail view with `OverviewTab`, `PerformanceTab`, `TradesTab`, `TradeLogTab`, `TearsheetTab`, `RobustnessTab`, `ReportsTab`
- **data-catalog/**: `FetchDialog`, `BatchFetchDialog`, `DeleteDialog`, `FilterTabs`, `CoveragePanel`

### QDS CSS Classes (globals.css)
For pixel-perfect replication of design mockups, globals.css contains `!important` CSS classes outside `@layer`:
- Forms: `qds-input`, `qds-select`, `qds-label`, `qds-hint`, `qds-switch`
- Cards: `qds-card`, `qds-card-header`, `qds-card-body`
- Stats: `qds-stat`, `qds-stat-label`, `qds-stat-value`
- Layout: `qds-section-label`
- Tables: `qds-table`
- Backtest-specific: `bt-list`, `bt-row`, `bt-status`, `bt-progress`, `bt-expand`

These coexist with the Tailwind/shadcn approach — new code should prefer Tailwind classes and QDS components (`components/qds/`).

## Key Conventions

- **Design reference files**: `docs/ui/qds-*.html` and `docs/ui/qds-warm-theme.css` are the design system source of truth. Pixel-perfect replication is expected.
- **Fonts**: IBM Plex Sans (`font-sans` / `var(--font-u)`) for UI, IBM Plex Mono (`font-mono` / `var(--font-d)`) for data values.
- **shadcn Tooltip API**: Uses `@base-ui/react`, not Radix. `TooltipProvider` takes `delay` (not `delayDuration`), `TooltipTrigger` has no `asChild` prop.
- **Static export**: No `getServerSideProps`, no API routes. All data fetching is client-side via `useEffect` + `apiGet`.
