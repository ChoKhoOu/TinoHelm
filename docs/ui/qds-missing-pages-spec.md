# QDS — 补充页面设计规范

## 设计原则

这 4 个页面遵循"最简有效"原则：每个页面只做一件核心事情，用已有的 QDS 组件拼装，不引入新组件。

---

## 1. Analytics

### 核心定位

Dashboard 回答"今天赚了多少"，Analytics 回答"钱是怎么赚的、风险在哪"。

### 布局

```
┌─ 时间范围选择器 ─────────────────────────────────────┐
│  [7d] [30d] [90d] [YTD] [All]    Date range picker   │
└──────────────────────────────────────────────────────┘
┌─ 归因卡片 (4col) ──────────────────────────────────── ┐
│  [Alpha]  [Beta Return]  [Idiosyncratic]  [Total]     │
└──────────────────────────────────────────────────────┘
┌─ Charts (2col) ─────────────────────────────────────── ┐
│  [收益归因堆叠面积图]      [策略相关性矩阵热力图]       │
├──────────────────────────────────────────────────────┤
│  [因子暴露雷达图]          [滚动收益分布 violin/box]    │
└──────────────────────────────────────────────────────┘
┌─ 风险分解表 ──────────────────────────────────────────┐
│  Strategy | Allocation | Return | Contribution | Risk  │
└──────────────────────────────────────────────────────┘
```

### 关键组件

| 组件 | 复用来源 | 说明 |
|------|----------|------|
| 时间范围 tab | `.tabs` (backtest detail) | 切换分析区间 |
| 归因卡片 | `.sc` stat card | Alpha / Beta / 特质收益 |
| 堆叠面积图 | Chart.js stacked area | 按策略分解的累计收益 |
| 相关性矩阵 | 月度热力图 (backtest) | 策略间 pearson 相关系数，-1~1 色阶 |
| 雷达图 | Chart.js radar | 因子暴露：市场/波动率/动量/价值/流动性 |
| 风险分解表 | `.tbl` + 分页 | 每个策略的收益贡献和风险贡献 |

### 数据时间范围

默认 30d。切换范围时所有图表和卡片联动刷新。

---

## 2. Orders

### 核心定位

一个地方看到所有订单状态：挂单、成交、撤单。支持手动撤单。

### 布局

```
┌─ KPI (4col) ─────────────────────────────────────────┐
│  [Open Orders]  [Filled Today]  [Volume]  [Fill Rate] │
└──────────────────────────────────────────────────────┘
┌─ Tab bar ────────────────────────────────────────────┐
│  [Open Orders (12)]  [Filled (342)]  [Cancelled (8)]  │
└──────────────────────────────────────────────────────┘
┌─ Orders Table ───────────────────────────────────────┐
│  Time | Strategy | Symbol | Side | Type | Price |     │
│  Size | Filled | Status | Action                      │
└──────────────────────────────────────────────────────┘
```

### Tab 内容

| Tab | 列 | 特殊交互 |
|-----|-----|----------|
| **Open** | Time / Strategy / Symbol / Side / Type / Price / Size / Filled% / Cancel 按钮 | Cancel 触发 warning modal |
| **Filled** | Time / Strategy / Symbol / Side / Type / Price / Size / Fee / Latency | 分页，按时间倒序 |
| **Cancelled** | Time / Strategy / Symbol / Side / Reason | 分页 |

### 关键交互

- Open orders 的 Cancel 按钮：单个撤单用 ghost 按钮，批量撤单（勾选 + "Cancel selected"）用 warning modal
- Filled% 列用迷你进度条（部分成交时可见）
- Type 列用 badge：`Limit` / `Market` / `Stop` / `Post-only`
- 实时更新：新成交从 Open 移到 Filled，WS 推送

---

## 3. Watchlist

### 核心定位

自定义监控列表，实时行情 + 价格提醒。盯盘用。

### 布局

```
┌─ Watchlist 选择器 + 添加按钮 ─────────────────────────┐
│  [默认] [BTC 对冲组] [高波动] [+]       [+ Add Symbol]  │
└──────────────────────────────────────────────────────┘
┌─ 行情表 ─────────────────────────────────────────────┐
│  Symbol | Last | 24h Chg | 24h Chg% | High | Low |    │
│  Volume | Funding | Spread | Spark | Alert              │
└──────────────────────────────────────────────────────┘
```

### 关键特性

| 特性 | 实现方式 |
|------|----------|
| 实时价格 | WS 推送，tick flash 动效 (600ms) |
| 7d 迷你图 | 每行右侧 sparkline（60×24px canvas） |
| 价格提醒 | 点 Alert 列设置上/下限，触发时 toast 通知 |
| 多 watchlist | tab 切换，持久化到 localStorage |
| 拖拽排序 | 行可拖拽重新排序 |
| 添加 symbol | 顶部搜索输入框，模糊匹配 exchange:symbol |

### Funding rate 列

显示当前 funding rate，正值绿色，负值红色。永续合约专用。

### Spread 列

显示当前 best bid/ask spread（bps），用颜色编码：< 1bp 绿，1-5bp 白，> 5bp 橙。

---

## 4. Optimization

### 核心定位

策略参数优化：定义参数空间 → 跑 grid/random search → 查看结果热力图 → 选最优参数。

### 布局

```
┌─ 配置区 ─────────────────────────────────────────────┐
│  Strategy:  [dropdown]     Method: [Grid] [Random]     │
│  ┌─ Parameters ─────────────────────────────────────┐ │
│  │  param_name | min | max | step | current          │ │
│  └──────────────────────────────────────────────────┘ │
│  Objective: [Sharpe ▾]    Constraints: [MaxDD < 5%]   │
│  [Start Optimization]                                  │
└──────────────────────────────────────────────────────┘
┌─ 进度 (运行中显示) ─────────────────────────────────── ┐
│  [████████░░░░░░] 67%  142/212 combinations  ETA ~8min  │
└──────────────────────────────────────────────────────┘
┌─ 结果区 ─────────────────────────────────────────────┐
│  [参数热力图]          [Top 10 结果表]                  │
│  x=param1 y=param2     Rank | Params | Sharpe | DD     │
│  color=objective                                       │
│  [收益曲线叠加图]      [参数敏感性图]                    │
└──────────────────────────────────────────────────────┘
```

### 三个阶段

| 阶段 | UI 状态 |
|------|---------|
| **配置** | 参数表格可编辑 + 目标函数选择 + Start 按钮 |
| **运行中** | 进度条（复用 backtest 的 Design D）+ 实时最优显示 |
| **完成** | 热力图 + 排行榜 + 曲线对比 + "Apply to strategy" 按钮 |

### 参数热力图

2D 热力图，x/y 轴是两个参数，颜色深浅代表目标函数值。复用 backtest monthly returns 的热力图组件，色阶改为连续（差 → 好 = 红 → 绿）。

### Top 10 结果表

| 列 | 说明 |
|----|------|
| Rank | #1 - #10 |
| Parameters | key=value 紧凑显示 |
| Sharpe | 目标函数值 |
| Max DD | 约束检查，超限标红 |
| Win Rate | 辅助参考 |
| Trades | 样本量，太少标橙 |
| Action | "Apply" 按钮 → 覆盖策略参数 (warning modal) |

### 复用关系

| Optimization 组件 | 复用来源 |
|-------------------|----------|
| 参数编辑表格 | Settings 的 `.form-input` |
| 进度条 + ETA | Backtest Design D (`.d-bar-wrap` + `.d-stats`) |
| 热力图 | Backtest monthly returns heatmap |
| 结果排行表 | `.tbl` + 分页 |
| Apply modal | Confirmation modal (warning 级) |

---

## 复用统计

这 4 个页面 **零新组件**，全部由已有 QDS 组件拼装：

| 已有组件 | 被复用次数 |
|----------|-----------|
| `.sc` stat card | 4 页 ×4 = 16 个 |
| `.tbl` table | 4 页各 1 个 |
| `.tabs` tab bar | Analytics + Orders + Watchlist |
| Chart.js charts | Analytics 4 个 + Optimization 2 个 |
| 热力图 | Analytics + Optimization |
| 进度条 Design D | Optimization |
| Toast | Orders (撤单) + Watchlist (价格提醒) |
| Confirmation modal | Orders (批量撤) + Optimization (apply) |
| 分页 `.pager` | Orders (Filled/Cancelled) |
| Tick flash | Watchlist 全表 |
| Form inputs | Optimization 参数配置 |
| Empty state | 所有 4 页的空状态 |
