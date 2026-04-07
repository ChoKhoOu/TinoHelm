# QDS Chart.js Configuration Reference

All charts use Chart.js 4.x. Colors resolved via JS functions — Chart.js doesn't read CSS variables.
**Rebuild all charts on theme toggle.**

## Theme-Aware Color Functions

```javascript
function isLight() {
  return document.documentElement.classList.contains('light');
}

// Semantic palette — must match theme.css tokens
function cAccent()  { return '#D97857'; }                               // same both themes
function cSuccess() { return '#36884B'; }                               // same both themes
function cDanger()  { return isLight() ? '#8A2425' : '#FE8181'; }       // differs
function cInfo()    { return isLight() ? '#3266AD' : '#85B7EB'; }       // differs
function cWarning() { return isLight() ? '#854F0B' : '#FAC775'; }       // differs
function cPurple()  { return isLight() ? '#7C3AED' : '#A78BFA'; }       // chart-only extra
function cGray()    { return isLight() ? 'rgba(0,0,0,.15)' : 'rgba(255,255,255,.2)'; }
function cText()    { return isLight() ? '#2C2C2A' : '#E8E6E0'; }
function cGrid()    { return isLight() ? 'rgba(0,0,0,.05)' : 'rgba(255,255,255,.05)'; }
function cTick()    { return isLight() ? 'rgba(44,44,42,.4)' : 'rgba(232,230,224,.4)'; }
```

## Chart Palette Order (multi-series)

1. `cAccent()` — burnt orange (primary)
2. `cInfo()` — blue
3. `cSuccess()` — green
4. `cWarning()` — amber
5. `cPurple()` — purple
6. `cDanger()` — red (use sparingly, reserved for negative data)

## Base Options Factory

```javascript
function baseOptions() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 800, easing: 'easeOutQuart' },
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: isLight() ? '#f5f4ed' : '#302f2d',
        titleColor: cText(),
        bodyColor: cTick(),
        borderColor: isLight() ? '#dedbd3' : '#3b3a37',
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
  };
}
```

## Legend Config (when needed)

```javascript
function legendConfig() {
  return {
    display: true,
    labels: {
      color: cTick(),
      font: { family: "'IBM Plex Mono'", size: 10 },
      boxWidth: 10, padding: 12, usePointStyle: true
    }
  };
}
```

---

## Chart Type Configurations

### 1. Line + Area (Equity Curve)

```javascript
type: 'line'
datasets: [
  {
    label: 'Portfolio NAV',
    borderColor: cSuccess(),
    backgroundColor: cSuccess() + '18',
    fill: true, tension: 0.35, pointRadius: 0, pointHoverRadius: 5, borderWidth: 2
  },
  {
    label: 'Benchmark',
    borderColor: cGray(),
    borderDash: [5, 4],
    fill: false, tension: 0.35, pointRadius: 0, borderWidth: 1.5
  }
]
// Y axis: ticks.callback = v => '$' + Math.round(v/1000) + 'k'
// Enable legend
```

### 2. Horizontal Bar (Strategy PnL)

```javascript
type: 'bar'
options.indexAxis = 'y'
datasets: [{ backgroundColor: cSuccess(), borderRadius: 4, barThickness: 24 }]
// X axis: ticks.callback = v => '$' + v/1000 + 'k'
// Y axis: grid.display = false
```

### 3. Vertical Bar (Daily PnL — positive/negative coloring)

```javascript
type: 'bar'
datasets: [{
  backgroundColor: data.map(v => v >= 0 ? cSuccess() + 'cc' : cDanger() + 'cc'),
  borderRadius: 3, barPercentage: 0.7
}]
// Y axis: ticks.callback = v => (v >= 0 ? '+' : '') + v/1000 + 'k'
```

### 4. Scatter Plot

```javascript
type: 'scatter'
datasets: [
  { label: 'Group A', backgroundColor: cSuccess() + 'aa', pointRadius: 5 },
  { label: 'Group B', backgroundColor: cInfo() + 'aa', pointRadius: 5 },
  { label: 'Group C', backgroundColor: cWarning() + 'aa', pointRadius: 5 }
]
// Axis titles via scales.x.title / scales.y.title
// Enable legend
```

### 5. QQ Plot (Scatter + reference line)

```javascript
type: 'scatter'
datasets: [
  { data: qqPoints, backgroundColor: cAccent() + 'aa', pointRadius: 4, order: 1 },
  {
    data: [{ x: minQ, y: minQ }, { x: maxQ, y: maxQ }],
    type: 'line', borderColor: cGray(), borderDash: [6, 4],
    borderWidth: 1.5, pointRadius: 0, order: 0
  }
]
```

### 6. Histogram

```javascript
type: 'bar'
datasets: [{
  backgroundColor: cInfo() + '88', borderColor: cInfo(),
  borderWidth: 1, barPercentage: 1, categoryPercentage: 1
}]
// X axis: ticks.maxTicksLimit = 10
```

### 7. Drawdown / Underwater

```javascript
type: 'line'
datasets: [{
  borderColor: cDanger(), backgroundColor: cDanger() + '20',
  fill: 'origin', tension: 0.3, pointRadius: 0, borderWidth: 1.5
}]
// Y axis: ticks.callback = v => v.toFixed(1) + '%', max = 0.5
```

### 8. Doughnut

```javascript
type: 'doughnut'
options: { cutout: '65%' }
datasets: [{
  backgroundColor: [cAccent(), cInfo(), cSuccess(), cWarning(), cPurple(), cGray()],
  borderWidth: 0, spacing: 2
}]
// Legend: position 'right', font-data 10px
```

### 9. Pie

Same as Doughnut but `type: 'pie'`, no `cutout`.

### 10. Radar

```javascript
type: 'radar'
labels: ['Sharpe', 'Sortino', 'Calmar', 'WinRate', 'PF', 'Recovery']
datasets: [{
  borderColor: cAccent(), backgroundColor: cAccent() + '22',
  pointBackgroundColor: cAccent(), pointRadius: 4, borderWidth: 2
}]
scales.r: {
  angleLines: { color: cGrid() }, grid: { color: cGrid() },
  ticks: { display: false },
  pointLabels: { color: cTick(), font: { family: "'IBM Plex Mono'", size: 10 } },
  suggestedMin: 0, suggestedMax: 100
}
```

### 11. Candlestick (floating bars)

```javascript
type: 'bar'
datasets: [
  {
    label: 'Body',
    data: ohlc.map(d => [Math.min(d.o, d.c), Math.max(d.o, d.c)]),
    backgroundColor: ohlc.map(d => d.c >= d.o ? cSuccess() : cDanger()),
    barPercentage: 0.6
  },
  {
    label: 'Wick',
    data: ohlc.map(d => [d.l, d.h]),
    backgroundColor: 'transparent',
    borderColor: colors.map(c => c + '88'), borderWidth: 1, barPercentage: 0.1
  }
]
```

### 12. Multi-Line

```javascript
type: 'line'
datasets: [
  { label: 'A', borderColor: cAccent(),  tension: 0.3, pointRadius: 0, borderWidth: 2 },
  { label: 'B', borderColor: cInfo(),    tension: 0.3, pointRadius: 0, borderWidth: 2 },
  { label: 'C', borderColor: cSuccess(), tension: 0.3, pointRadius: 0, borderWidth: 2 },
  { label: 'D', borderColor: cWarning(), tension: 0.3, pointRadius: 0, borderWidth: 2 }
]
// Enable legend, x ticks maxTicksLimit = 10
```

### 13. Stacked Area

```javascript
type: 'line'
scales: { y: { stacked: true }, x: { stacked: true } }
datasets: [
  { label: 'A', borderColor: cAccent(),  backgroundColor: cAccent() + '33',  fill: true, ... },
  { label: 'B', borderColor: cInfo(),    backgroundColor: cInfo() + '33',    fill: true, ... },
  { label: 'C', borderColor: cSuccess(), backgroundColor: cSuccess() + '33', fill: true, ... },
  { label: 'D', borderColor: cWarning(), backgroundColor: cWarning() + '33', fill: true, ... }
]
```

### 14. Heatmap (Canvas manual draw — not Chart.js)

```javascript
// Color mapping: positive → success, negative → danger
// Opacity scales linearly with absolute value
if (value >= 0) {
  const t = Math.min(value, 1);
  color = isLight()
    ? `rgba(54,136,75,${0.08 + t * 0.5})`     // success light rgba
    : `rgba(54,136,75,${0.1 + t * 0.65})`;     // success dark rgba
} else {
  const t = Math.min(-value, 1);
  color = isLight()
    ? `rgba(138,36,37,${0.08 + t * 0.5})`      // danger light rgba
    : `rgba(254,129,129,${0.1 + t * 0.65})`;    // danger dark rgba
}

// Draw rounded rects: ctx.roundRect(x+1, y+1, w-2, h-2, 3)
// Small matrices (≤7 cols): render text inside cells
// Large (24h heatmap): skip text
// Rebuild on window.resize (absolute pixel dims)
```

---

## Rebuild Pattern

```javascript
const charts = {};
function makeChart(id, config) {
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart(document.getElementById(id), config);
}

function rebuildAllCharts() {
  buildEquityCurve();
  buildBarH();
  buildBarV();
  // ... all builders
  buildHeatmap1();  // canvas-based
  buildHeatmap2();
}

function toggleTheme() {
  document.documentElement.classList.toggle('light');
  rebuildAllCharts();
}

window.addEventListener('resize', () => { buildHeatmap1(); buildHeatmap2(); });
```