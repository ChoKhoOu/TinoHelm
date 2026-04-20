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
      <StatusBar />                 ← Bottom: exchange latency, fill ticker, clock
      <Toaster />                   ← sonner toast (bottom-right, max 3, 5s)
      <NotificationListener />      ← WS events → toast routing
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
│                  HelpTip, PageHeader, SectionLabel, InlineError) — barrel export
├── motion/      ← FadeIn, StaggerContainer, PageTransition (framer-motion)
└── *.tsx        ← App-level: Sidebar, TopBar, StatusBar, FillTicker,
                   NotificationListener, ErrorBoundary, etc.
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
- **data-catalog/**: `FetchDialog`, `DeleteDialog`, `FilterTabs`, `JobQueue`

### QDS CSS Classes (globals.css)
`globals.css` contains `!important` CSS classes for QDS business components:
- Forms: `qds-input`, `qds-select`, `qds-label`, `qds-hint`, `qds-switch`
- Cards: `qds-card`, `qds-card-header`, `qds-card-body`
- Stats: `qds-stat`, `qds-stat-label`, `qds-stat-value`
- Layout: `qds-section-label`
- Tables: `qds-table`

**After DS standardization (2026-04-19)**: `bt-*/dc-*/cg/ca/cr/ci/dim/mono` independent tokens and the factor-research subsystem (`.sc/.cd/.sl/.fl/.fi/.fsel/.ctbl/.dtab/...` 85 classes) have all been deleted from `globals.css`. Business code must NOT use these classes. Use Tailwind utilities and `components/qds/` instead.

### 4-Layer Notification System
Spec: `.claude/skills/TinoHelmDS/` (see SKILL.md for design system index).

- **Layer 1 (Silent/Ticker)**: High-freq WS events flow into UI components. `FillTicker` shows latest fill in StatusBar with 5s auto-fade. No toast.
- **Layer 2 (Inline)**: `useAction` hook (`hooks/use-action.ts`) — button state machine (idle→loading→success→idle / →error). `InlineError` (`components/qds/`) for error display. **API errors NEVER use toast — feedback appears at the button/form.**
- **Layer 3 (Toast)**: `NotificationListener` routes async WS events through `lib/notification-router.ts` `ROUTING_TABLE` to Sonner. Events: backtest complete/fail, data fetch complete/fail, strategy start/stop, connection degraded/restored. Deduped by event ID, max 3 visible, 5s auto-dismiss.
- **Layer 4 (Modal)**: Critical risk events → blocking modal (future).

Adding new toast events: add entry to `ROUTING_TABLE` in `notification-router.ts`, add format case to `formatToastMessage()`. NotificationListener auto-subscribes.

## Key Conventions

- **MUST: Design-first development**: Before implementing ANY frontend page or component, ALWAYS read the corresponding design reference in `.claude/skills/TinoHelmDS/` first. These are the single source of truth for UI/UX, layout, spacing, color, typography, and **animation/motion**. Pixel-perfect replication is expected — do not simplify, approximate, or deviate. The `docs/` UI directory referenced in older docs does not exist — all design references live in `.claude/skills/TinoHelmDS/`.
  - Page-level preview cards: `.claude/skills/TinoHelmDS/preview/component-row.html` (backtest rows), `preview/component-kpi.html`, `preview/component-badges.html`, `preview/component-tabs.html`, `preview/component-progress.html`, `preview/component-buttons.html`, `preview/color-semantic.html`
  - Master reference: `.claude/skills/TinoHelmDS/Web UI Kit.html` (full dashboard frame)
  - Charts reference: `.claude/skills/TinoHelmDS/Charts Spec.html`
  - Design pitch: `.claude/skills/TinoHelmDS/QDS Pitch Deck.html`
- **Fonts**: Inter (`font-sans` / alias `var(--font-u)`) for UI, JetBrains Mono (`font-mono` / alias `var(--font-d)`) for data values. Loaded via `next/font/google` in `layout.tsx` (self-hosted via `.next/static/media/`, not CDN). Inter OpenType features `cv11`/`ss01`/`ss03` enabled globally on `body`. Legacy aliases `--font-u`/`--font-d` re-point to `--font-sans`/`--font-mono` for backward compatibility.
- **shadcn Tooltip API**: Uses `@base-ui/react`, not Radix. `TooltipProvider` takes `delay` (not `delayDuration`), `TooltipTrigger` has no `asChild` prop.
- **Static export**: No `getServerSideProps`, no API routes. All data fetching is client-side via `useEffect` + `apiGet`.

## 标准化后的约束

> 本章节记录 **2026-04-19 DS 标准化任务**（12 subtasks）完成后的强制规则。所有新代码及 agent 生成代码必须遵守。

### 四条标准化方向

1. **内联 style 消灭**：`style={{ fontFamily, fontSize }}` 等内联样式迁移到 Tailwind class（`font-mono`/`font-sans`/`text-[size]`）。`chartTheme.ts` 常量定义本体与 `globals.css` token 层除外。
2. **遗留 class 消灭**：`bt-*/dc-*/cg/ca/cr/ci/dim/mono` 独立 token 及 factor-research 子系统（85 class）已从 `globals.css` 删除，业务代码中零出现。
3. **Recharts 统一入口**：所有 Recharts tooltip 使用 `{...CHART_TOOLTIP_PROPS}` spread，grid 使用 `{...CHART_GRID_STYLE}`，legend 使用 `wrapperStyle={CHART_LEGEND_STYLE}`，ReferenceLine label 使用 `label={{ ...CHART_LABEL_STYLE, value: "..." }}`。
4. **QDS 组件优先**：优先使用 `components/qds/` 七大业务组件，次选 Tailwind 语义类，最后才考虑 shadcn 原语。

### Tailwind 首选顺序

```
语义 Tailwind（bg-card / text-qds-success / text-destructive）
  → QDS 扩展（text-qds-t1 / border-qds-border-hover / bg-qds-success-dim）
    → shadcn 原语（Button / Card / Badge / Table）
      → Tailwind 原子类（flex / grid / gap-* / p-* / text-sm）
```

### QDS 强制组件（7 项）

| 场景 | 必用组件 |
|---|---|
| KPI 数字卡片 | `<StatCard>` |
| 页面 / 区块标题 | `<PageHeader>` / `<SectionLabel>` |
| 内联错误提示 | `<InlineError>` |
| 状态徽章（run status / job status） | `<StatusBadge status="..." />` |
| 帮助提示 | `<HelpTip>` |
| 进度条 / 加载骨架 | `<ShimmerBar>` |

### 禁区 class 清单

以下 class **禁止**在任何 `.tsx` 文件的 `className` 属性中出现（`verify-ds-compliance.sh` R1-R14 自动扫描）：

- **内联字体**: `style={{ fontFamily: "var(--font-d)" / "var(--font-u)" }}`
- **bt-* 家族**: `bt-list`, `bt-row`, `bt-status`, `bt-progress`, `bt-expand`, `bt-cd`, `bt-cd-header`, `bt-cd-body`, `bt-kpi-*` 等
- **dc-* 家族**: `dc-filter-*`, `dc-qrow-*`, `dc-dtbl`, `dc-type-*`, `dc-cov-*`, `dc-pager-*`, `dc-chip-*`, `dc-sl`, `dc-modal-icon`
- **单字母语义 token（独立形式）**: `cg`, `ca`, `cr`, `ci`, `dim`, `mono`
- **factor-research 子系统**: `sc`, `sc-l`, `sc-v`, `sc-sub`, `cd`, `sl`, `fl`, `fi`, `fsel`, `ctbl`, `dtab`, `hm-grid`, `hm-label`, `hm-cell`, `hm-tick` 及其它 85 个 class 家族
- **未定义 CSS 变量**: `var(--accent-green)`, `var(--accent-red)`, `var(--accent-amber)`, `var(--accent-blue)`, `var(--accent-orange)`, `var(--accent-purple)` 及其 `-10/-20` 变体

**迁移对照**:

| 禁用 | 替代 |
|---|---|
| `cg` | `text-qds-success` |
| `cr` | `text-destructive` |
| `ca` | `text-primary` |
| `ci` | `text-qds-info` |
| `dim` | `text-muted-foreground` |
| `mono` | `font-mono` |
| `var(--accent-green)` | `text-qds-success` |
| `var(--accent-red)` | `text-destructive` |
| `var(--accent-amber)` | `text-qds-warning` |
| `var(--accent-blue)` | `text-qds-info` |
| `var(--accent-orange)` / `var(--accent-purple)` | `text-primary` |
| `var(--accent-red-20)` | `bg-qds-danger-dim` |
| `var(--accent-green-10)` | `bg-qds-success-dim` |
| `var(--accent-amber-20)` | `bg-qds-warning-dim` |
| `var(--accent-blue-20)` | `bg-qds-info-dim` |
| `var(--accent-purple-20)` | `bg-qds-accent-dim` |

### Recharts 统一入口（chartTheme.ts）

`lib/chartTheme.ts` 导出以下常量，业务代码必须使用 spread 形式引用，不允许手写等价对象：

| 常量 | 用途 | 使用形式 |
|---|---|---|
| `CHART_TOOLTIP_PROPS` | Recharts tooltip 样式 | `<RechartsTooltip {...CHART_TOOLTIP_PROPS} />` |
| `CHART_GRID_STYLE` | CartesianGrid 样式 | `<CartesianGrid {...CHART_GRID_STYLE} />` |
| `CHART_AXIS_STYLE` | XAxis/YAxis tick 样式 | `tick={CHART_AXIS_STYLE}` |
| `CHART_LEGEND_STYLE` | Legend wrapperStyle | `<Legend wrapperStyle={CHART_LEGEND_STYLE} />` 或 spread 扩展 |
| `CHART_LABEL_STYLE` | ReferenceLine 对象 label | `<ReferenceLine label={{ ...CHART_LABEL_STYLE, value: "..." }}>` — **非 `<Label>` 子组件** |
| `CHART_COLORS` | 语义色 map | `fill={CHART_COLORS.success}` |
| `CHART_GRADIENT_OPACITY` | 面积填充不透明度 | `stopOpacity={CHART_GRADIENT_OPACITY.equity}` |
| `CHART_ANIMATION` | 动画 duration/easing | `animationDuration={CHART_ANIMATION.duration}` |

> `CHART_LABEL_STYLE` 不含 `fontFamily`（保持 Recharts 默认字体，避免 ReferenceLine label 字体从默认变 mono 的视觉 shift）；fontSize 统一为 10。

### 扫描脚本调用方式

```bash
# 全仓扫描（R1-R14，CI / pre-push 使用）
bash src/web/scripts/verify-ds-compliance.sh

# 含迁移建议的详细输出
bash src/web/scripts/verify-ds-compliance.sh --fix-hint

# dark/light 双主题扫描（排除 components/ui/** 和 components/qds/**）
bash src/web/scripts/verify-ds-compliance.sh --mode both-themes

# globals.css 删除前置检查（s10 前强制运行）
bash src/web/scripts/verify-ds-compliance.sh --preflight-before-css-delete

# 脚本自检（65 个正/反例断言，CI 验证脚本本身正确性）
bash src/web/scripts/verify-ds-compliance.sh --selftest
```

**Exit code**: 0 = 合规 / 1 = 违规 / 2 = 脚本错误

**shadcn 原语豁免**: `src/web/src/components/ui/**` 目录下的文件对 R10（arbitrary token）和 `dark:` 前缀规则豁免，不在扫描范围内。

### globals.css 实际删除数据（2026-04-19）

- 删除前：1987 行
- 删除后：785 行（删除 ~1202 行遗留定义）
- 保留：token 层（`:root`/`html.light`）、`@theme inline`、`@keyframes`、`qds-*` 业务组件 class
- `rg '^\.qds-' src/web/src/app/globals.css | wc -l` ≥ 15（保留验证）

### Historical Notes（2026-04-19 DS 标准化任务）

以下历史 memory 文件的主张已被本次标准化任务取代，不再作为 agent 行为准则：

| Memory 文件 | 原主张 | 状态 |
|---|---|---|
| `feedback-bt-card-classes.md` | bt-cd/bt-cd-header/bt-cd-body 强制用 backtest 专属 class | **已废止（2026-04-19）**：改为 shadcn `<Card>/<CardHeader>/<CardContent>` |
| `feedback-use-existing-css.md` | 优先使用 globals.css 已有 class（含遗留 bt-*/dc-*） | **已废止（2026-04-19）**：bt-*/dc-*/factor-research class 已从 globals.css 删除，禁止使用 |
| `feedback-css-class-naming.md` | 使用 HTML 参考文件的原始 class 名（cd/ctbl/sl 等） | **已废止（2026-04-19）**：factor-research 原语已全面迁移为 Tailwind/QDS 组件 |
| `feedback-pixel-perfect.md` | 像素级还原，使用 inline style 框架 | **部分有效**：像素级还原目标保留；但实现路径改为 Tailwind 语义类 + QDS 组件，禁止 inline style fontFamily/fontSize |

> 上述 memory 文件主 agent 需在 verify phase 向用户确认后正式更新/作废（interview.md 第 4 轮选择隐含此方向）。
