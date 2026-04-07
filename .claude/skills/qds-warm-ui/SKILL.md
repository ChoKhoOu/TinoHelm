---
name: qds-warm-ui
description: >
  QDS Warm Minimal design system for quantitative trading dashboards.
  Use this skill whenever the user asks to build UI components, pages, dashboards, or data
  visualizations for their quant/trading system. Triggers on: "QDS", "warm theme", "design system",
  dashboard components, charts, tables, forms, buttons, toasts, modals, dropdowns, order panels,
  stat cards, or any request to style components in the warm minimal palette. Also trigger when
  adding new chart types (Chart.js) or when the user wants consistent styling with their existing
  trading UI. Covers CSS theming, component patterns, Chart.js configs, and dark/light mode.
---

# QDS Warm Minimal — Design System

Production design system for high-frequency quantitative trading dashboards. Warm earthy palette,
purposeful animations for long work sessions, dark/light mode, comprehensive component + chart coverage.

## How to use this skill

1. **Always** read `references/theme.css` before writing any styled code
2. If the task involves charts, also read `references/charts.md`
3. Use CSS variables everywhere — never hardcode colors
4. Default to dark mode; `html.light` class activates light mode

```
qds-warm-ui/
├── SKILL.md              ← Design principles, component specs, animation rules
└── references/
    ├── theme.css          ← Complete CSS: all tokens, all component classes
    └── charts.md          ← Chart.js palette functions + all 15 chart type configs
```

---

## 1. Color System

### Background Layers (4 tiers, deep → shallow)

| Layer | Dark | Light | Usage |
|-------|------|-------|-------|
| `--bg-inset` | `#141413` | `#fcfbf8` | Sunken — form inputs, code blocks, embedded panels |
| `--bg-secondary` | `#262624` | `#faf9f5` | Body background |
| `--bg-primary` | `#302f2d` | `#f5f4ed` | Cards, panels, elevated surfaces |
| `--bg-tertiary` | `#3b3a37` | `#eae8e0` | Hover states, active backgrounds |

**Dark:** `#141413` → `#262624` → `#302f2d` → `#3b3a37`
**Light:** `#fcfbf8` → `#faf9f5` → `#f5f4ed` → `#eae8e0`

Light mode uses warm cream — **never pure white `#FFFFFF`**.

### Semantic Colors

| Token | Dark | Light | Usage |
|-------|------|-------|-------|
| `--accent` | `#D97857` | `#D97857` | Primary buttons, focus rings, links, slider thumb |
| `--success` | `#36884B` | `#36884B` | PnL profit, buy buttons, up tick flash, sparklines |
| `--danger` | `#FE8181` | `#8A2425` | PnL loss, sell buttons, down tick flash, errors |
| `--info` | `#85B7EB` | `#3266AD` | PERP tags, info toasts, secondary chart color |
| `--warning` | `#FAC775` | `#854F0B` | SPOT tags, warning toasts |

Note: accent and success are same value in both themes.
Danger differs — bright coral on dark bg, deep crimson on light bg.

Each semantic color has a `-dim` variant (12% opacity dark, 8% light) for backgrounds/badges.

### Text & Borders

| Token | Dark | Light |
|-------|------|-------|
| `--text-primary` | `#E8E6E0` | `#2C2C2A` |
| `--text-secondary` | `#9C9A92` | `#73726C` |
| `--text-tertiary` | `#73726C` | `#9C9A92` |
| `--text-muted` | `#5F5E5A` | `#B0ADA5` |
| `--border-default` | `#3b3a37` | `#dedbd3` |
| `--border-hover` | `#5F5E5A` | `#c7c4bb` |

---

## 2. Typography

| Role | Font | Weights | Sizes |
|------|------|---------|-------|
| Data / monospace | `IBM Plex Mono` | 300, 400, 500, 600 | 0.65–1.5rem |
| UI / body | `IBM Plex Sans` | 300, 400, 500, 600, 700 | 0.72–1.1rem |

Never use Inter, Roboto, Arial, or system fonts.

```html
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
```

---

## 3. Animation Rules — "Fast, Precise, Non-fatiguing"

| Context | Duration | Easing | CSS variable |
|---------|----------|--------|--------------|
| Hover / toggle | 150ms | `--ease-out` | `--dur-fast` |
| Enter / appear | 280ms | `--ease-out` | `--dur-normal` |
| Exit / disappear | 200ms | `--ease-in` | `--dur-exit` |
| Scroll reveal | 400ms | `--ease-out` | `--dur-slow` |
| Data tick flash | 600ms | ease-out | `--dur-tick` |
| Number count-up | 1400ms | easeOutCubic (JS) | `--dur-count` |
| Chart animation | 800ms | easeOutQuart (Chart.js) | — |

**Easing functions:**
- `--ease-out: cubic-bezier(.16, 1, .3, 1)` — for entering/appearing
- `--ease-in: cubic-bezier(.4, 0, 1, 1)` — for exiting/disappearing

**Hard rules:**
- NEVER `ease-in-out` — feels sluggish
- NEVER infinite animations (except live-dot pulse)
- NEVER mouse-tracking effects — fatiguing over hours
- Button press: `translateY(1px)` on `:active`, 50ms
- Button hover: `translateY(-1px)` + box-shadow expand

---

## 4. Spacing & Radius

| Token | Value | Usage |
|-------|-------|-------|
| `--radius-l` | 12px | Cards, panels, table wraps |
| `--radius-m` | 10px | Toasts, dropdowns |
| `--radius-s` | 6px | Buttons, inputs, tags |
| Card padding | `1.1rem 1.25rem` | Stat cards |
| Card body padding | `1rem 1.1rem` | Chart cards |
| Grid gap | `1.25rem` | Between cards |
| Section gap | `3rem` | Between sections |

---

## 5. Component Specifications

### 5.1 Stat Card
- Background: `var(--bg-primary)`, border `var(--border-default)`, radius 12px
- Value: `font-data`, 1.5rem, weight 600, letter-spacing -0.03em
- Label: 0.75rem, `var(--text-secondary)`
- Delta badge: pill, `success-dim`/`danger-dim` bg
- Hover: border → `var(--border-hover)`, shadow `0 4px 20px rgba(0,0,0,.08)`
- Animation: number count-up 1400ms easeOutCubic on IntersectionObserver

### 5.2 Data Table
- Container: `var(--bg-primary)`, border, radius 12px, overflow hidden
- Toolbar: flex space-between, border-bottom, filter chips
- Filter chip: pill shape, `font-data` 0.7rem, active = accent border + accent-dim bg
- Header: uppercase 0.7rem, letter-spacing 0.08em, `var(--text-tertiary)`
- Row: border-bottom `var(--border-default)`, hover bg → `var(--bg-tertiary)`, 150ms
- Tick flash: `@keyframes` 600ms, `success-dim`→transparent / `danger-dim`→transparent
- Tags: PERP = `info-dim` bg + `info` color, SPOT = `warning-dim` bg + `warning` color
- Sparkline: inline SVG 64×20, polyline stroke-width 1.5

### 5.3 Buttons
```
Base:       font-ui 0.8rem, weight 500, pad 0.55rem 1.25rem, radius 6px
:active     translateY(1px), 50ms transition
Primary:    bg accent, color #fff, shadow accent 15%
            hover: brightness(1.1), shadow 25%, translateY(-1px)
Secondary:  bg var(--bg-tertiary), border var(--border-default)
Ghost:      transparent, color var(--text-secondary)
Danger:     bg danger-dim, color danger, border danger 20%
Buy:        bg success, color #fff, font-data weight 600
Sell:       bg danger, color #fff, font-data weight 600
Loading:    pointer-events none, opacity 0.7, spinner 14px 0.6s
Sizes:      sm = 0.72rem / 0.35rem 0.85rem
            lg = 0.88rem / 0.7rem 1.8rem
Icon:       34×34px square, inline-flex center
```

### 5.4 Form Inputs
- Input: `font-data` 0.82rem, bg `var(--bg-inset)`, border `var(--border-default)`
- Focus: border → accent, shadow `0 0 0 3px var(--accent-dim)`
- Error: border → danger, shadow `0 0 0 3px var(--danger-dim)`
- Select: same + chevron SVG background-image
- Toggle: 38×20px, bg-tertiary, 14×14 thumb
  - On: bg accent-dim, border accent, thumb = accent color

### 5.5 Toast Notification
- Container: fixed top-right, z-index 500
- Toast: bg `var(--bg-primary)`, border, radius 10px, shadow `8px 30px`
- Enter: `translateX(120%)` → `translateX(0)`, 280ms ease-out
- Exit: → `translateX(120%)`, 200ms ease-in (faster out)
- Progress bar: absolute bottom, 2px, 4s linear countdown
- Icon: 20×20 circle, `{semantic}-dim` bg, `{semantic}` color
- Auto-dismiss: 4.5s
- Types: success ✓ / error ✕ / info i / warning !

### 5.6 Dropdown
- Menu: absolute top+6px, bg `var(--bg-primary)`, radius 10px, shadow `12px 40px`
- Enter: `translateY(-8px) scale(0.97) opacity(0)` → normal, 280ms
- Items: `0.5rem 0.7rem` pad, radius 6px, hover → `var(--bg-tertiary)`
- Close on outside click

### 5.7 Modal
- Overlay: fixed inset 0, `rgba(0,0,0,0.4)`, `backdrop-filter: blur(4px)`
- Content: bg `var(--bg-primary)`, radius 14px, max-width 480px
- Enter: `translateY(20px) scale(0.96)` → normal, 280ms
- Sections: header (border-bottom) / body / footer (border-top, flex end)

### 5.8 Order Panel
- Side tabs: font-data 0.78rem weight 600
  - Active buy: success color + success-dim bg + 2px bottom bar
  - Active sell: danger color + danger-dim bg + 2px bottom bar
- Input group: flex, border, radius 6px
  - Focus-within: border accent + 3px accent-dim shadow
  - Unit suffix: bg `var(--bg-tertiary)`, border-left
- Slider: 3px track bg-tertiary, 14px accent thumb with glow, hover scale 1.3
- Summary: bg `var(--bg-inset)`, radius 6px
- Submit: full-width btn-buy/btn-sell btn-lg
  - Loading → spinner → restore → success toast

---

## 6. Dark/Light Toggle

```javascript
function toggleTheme() {
  document.documentElement.classList.toggle('light');
  rebuildAllCharts();  // Chart.js doesn't read CSS vars
}
```

Default = dark. `html.light` overrides all CSS variables.

---

## 7. Scroll Reveal Pattern

```javascript
const observer = new IntersectionObserver(entries => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      entry.target.classList.add('visible');
      // trigger counters inside
      observer.unobserve(entry.target);
    }
  });
}, { threshold: 0.1 });

document.querySelectorAll('[data-observe]').forEach(el => observer.observe(el));
```

CSS class: `.reveal` → `.reveal.visible` (opacity 0→1, translateY 20→0, 400ms)

---

## 8. Number Counter Pattern

```javascript
function animateCount(el) {
  const target = parseFloat(el.dataset.count);
  const decimal = parseInt(el.dataset.decimal || '0');
  const prefix = el.dataset.prefix || '';
  const suffix = el.dataset.suffix || '';
  const comma = el.hasAttribute('data-comma');
  const duration = 1400;
  const start = performance.now();

  function tick(now) {
    const t = Math.min((now - start) / duration, 1);
    const eased = 1 - Math.pow(1 - t, 3);  // easeOutCubic
    let val = (target * eased).toFixed(decimal);
    if (comma) val = Number(val).toLocaleString('en-US', {
      minimumFractionDigits: decimal,
      maximumFractionDigits: decimal
    });
    el.textContent = prefix + val + suffix;
    if (t < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}
```

Usage: `<div class="stat-value" data-count="284520" data-prefix="+$" data-comma>+$0</div>`

---

## 9. Data Tick Flash Pattern

```javascript
// Manual trigger
cell.classList.remove('tick-up', 'tick-down');
void cell.offsetWidth;  // force reflow
cell.classList.add(isUp ? 'tick-up' : 'tick-down');

// Auto simulation (2s interval)
setInterval(() => {
  const cells = document.querySelectorAll('[data-tick]');
  const cell = cells[Math.floor(Math.random() * cells.length)];
  // ... trigger flash + update value
}, 2000);
```