# QDS Supplementary Patterns — Design Specification

## 1. Empty States

### 设计原则

每种空状态必须回答两个问题：**为什么是空的** 和 **下一步做什么**。

### 三种空状态类型

| 类型 | 触发条件 | 图标背景 | 行为 |
|------|----------|----------|------|
| **首次使用** | 用户从未创建过该类数据 | `var(--bg-t)` 中性 | 引导文案 + 主 CTA 按钮 + 提示文本 |
| **搜索/筛选无结果** | 筛选条件过严或无匹配 | `var(--bg-t)` 中性 | "清除筛选" 次级按钮 |
| **请求失败** | 网络错误 / 服务端 500 | `var(--dan-d)` 红色 | "重试" 按钮（带 loading 态）+ 错误码 |

### 组件结构

```
.empty
  .empty-icon         56×56px, border-radius 16px
  .empty-title         0.9rem, font-weight 600
  .empty-desc         0.78rem, var(--t2), max-width 320px
  .empty-action        CTA 按钮 (首次使用) 或 .empty-retry (其他)
  .empty-hint         0.68rem, var(--t3), 辅助信息
```

### 各页面空状态文案

| 页面 | 标题 | 描述 | CTA |
|------|------|------|-----|
| Backtests | 还没有回测记录 | 创建你的第一个回测，验证策略在历史数据上的表现 | + 创建回测 |
| Strategies | 还没有策略 | 导入或创建一个交易策略开始使用 | + 新建策略 |
| Positions | 当前没有持仓 | 启动一个策略或手动下单开始交易 | 查看策略 |
| Trade Log | 暂无交易记录 | 策略开始运行后，交易记录会自动出现在这里 | — |

---

## 2. Confirmation Modals

### 设计原则

不可逆操作需要 **摩擦力** 防止误操作。摩擦力的大小与操作的破坏性成正比。

### 两个级别

| 级别 | 颜色 | 确认方式 | 适用场景 |
|------|------|----------|----------|
| **Warning** | `var(--warn)` 黄色 | 点击按钮即可 | 停止策略、取消回测、修改风控参数 |
| **Danger** | `var(--dan)` 红色 | 必须输入确认文本 | 删除回测、撤销 API key、清空数据 |

### Modal 结构

```
.modal-backdrop        rgba(0,0,0,.55)
  .modal               max-width 420px, scale(.95) → scale(1) 入场
    .modal-header
      .modal-icon       36×36px, danger 或 warn 色
      .modal-title      0.9rem bold
      .modal-desc       0.78rem, var(--t2)
    .modal-body
      (danger级) 输入确认文本, placeholder 显示需要输入的值
      (warning级) 显示影响摘要 (持仓数、未实现 PnL 等)
    .modal-footer
      btn-ghost 取消     左侧
      btn-danger/warn    右侧, danger 级输入正确前 disabled
```

### 按钮 Loading 态

提交后按钮进入 loading：文字变透明，显示 14px spinner。使用 `::after` 伪元素实现旋转动画，不需要额外 DOM。

```css
.btn-loading {
  position: relative;
  color: transparent !important;
  pointer-events: none;
}
.btn-loading::after {
  content: '';
  position: absolute;
  width: 14px; height: 14px;
  border: 2px solid var(--t2);
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin .6s linear infinite;
}
```

---

## 3. Toast Notification System

### 行为规范

| 属性 | 值 |
|------|-----|
| 入场方向 | 右侧滑入 (`translateX(20px)` → `0`) |
| 入场时间 | 280ms `var(--eo)` |
| 位置 | 右下角，距底部 24px，距右 24px |
| 最大堆叠 | 3 个，最新在上 |
| 自动消失 | 5 秒（底部 2px 进度条倒计时） |
| 手动关闭 | 右上角 × 按钮 |
| Hover 暂停 | 鼠标悬停时暂停计时器 |

### 四种类型

| 类型 | Icon bg | Icon 字符 | 使用场景 |
|------|---------|-----------|----------|
| **Success** | `var(--suc-d)` | ✓ | 回测完成、交易成交、保存成功 |
| **Error** | `var(--dan-d)` | ✕ | 回测失败、连接断开、操作失败 |
| **Warning** | `var(--warn-d)` | ! | 连接降级、内存警告、风控触发 |
| **Info** | `var(--info-d)` | i | 策略部署、数据同步、版本更新 |

### Toast 结构

```
.toast              320-400px, bg-p, 0.5px border, radius rs
  .toast-icon       20×20px, 类型色背景
  .toast-content
    .toast-title    0.78rem bold
    .toast-msg      0.72rem, var(--t2)
    .toast-action   可选, accent 色链接 (e.g. "查看结果 →")
  .toast-close      右上角 ×
  .toast-timer      bottom 2px, accent 色, 5s linear shrink
```

### Action Toast

部分 toast 需要 action 按钮：

| Toast | Action |
|-------|--------|
| 回测完成 | 查看结果 → |
| 策略已停止 | Undo（3 秒内可撤销） |
| 风控触发 | 查看详情 |

---

## 4. Data Staleness

### 三级时效体系

| 级别 | 条件 | 视觉表现 |
|------|------|----------|
| **Live** | 最后更新 < 2s | 正常显示，绿色 `Live` badge |
| **Stale** | 最后更新 2-30s | 数字变 `var(--t3)` + 斜体 + 橙色 `stale Xs` 小标签 |
| **Disconnected** | 最后更新 > 30s 或连接断开 | 整行 opacity 0.5 + 红色 `Disconnected` badge + PnL 显示 `—` |

### Stale 小标签

```css
.stale-badge {
  font-family: var(--font-d);
  font-size: .55rem;
  padding: .1rem .35rem;
  border-radius: 3px;
  background: var(--warn-d);     /* Stale */
  color: var(--warn);
  margin-left: .35rem;
  vertical-align: middle;
}
/* Disconnected 用 danger 色 */
.stale-badge.lost {
  background: var(--dan-d);
  color: var(--dan);
}
```

### 全局断连告警条

当所有交易所 WS 断开或主连接断开时，Content area 顶部出现红色告警条：

```css
.disco-bar {
  display: flex;
  align-items: center;
  gap: .5rem;
  padding: .5rem .85rem;
  background: var(--dan-d);
  border: 1px solid var(--dan);
  border-radius: var(--rs);
  font-family: var(--font-d);
  font-size: .72rem;
  color: var(--dan);
}
```

包含：
- 脉冲圆点（1.5s infinite）
- 文案：`WebSocket 连接断开 · 正在重连 (3/5) · 数据可能已过期`
- 右侧：手动重连按钮

### 数据降级规则

| 数据类型 | Stale 阈值 | Disconnected 阈值 | 降级行为 |
|----------|------------|-------------------|----------|
| Mark price | 2s | 30s | 停止显示 PnL 计算 |
| Orderbook | 1s | 10s | 标记为不可信 |
| Funding rate | 60s | 300s | 使用最后已知值 |
| Index price | 5s | 60s | 切换备用数据源 |

---

## 5. Form States

### Input 生命周期

| 状态 | 边框 | 背景 | 辅助信息 |
|------|------|------|----------|
| **Default** | `var(--bd)` | `var(--bg-in)` | `.form-hint` 灰色提示 |
| **Hover** | `var(--bdh)` | `var(--bg-in)` | — |
| **Focus** | `var(--acc)` + 3px `var(--acc-d)` glow | `var(--bg-in)` | — |
| **Error** | `var(--dan)` + 3px `var(--dan-d)` glow | `var(--bg-in)` | `.form-error` 红色错误信息 |
| **Disabled** | `var(--bd)` | `var(--bg-t)` | opacity 0.4 |
| **Success** | `var(--suc)` | `var(--bg-in)` | `.form-success` 绿色成功信息 |

### Focus ring

不使用 `outline`，用 `box-shadow` 实现柔和外发光：

```css
.form-input:focus {
  border-color: var(--acc);
  box-shadow: 0 0 0 3px var(--acc-d);
}
.form-input.error {
  border-color: var(--dan);
  box-shadow: 0 0 0 3px var(--dan-d);
}
```

### API Key 显示

```
┌─────────────────────────────────────────────┐
│  ••••••••••••••••FqX8     [显示] [撤销]      │
│  创建于 2026-01-15 · 最后使用 2 分钟前         │
└─────────────────────────────────────────────┘
```

- 默认显示最后 4 位 + 遮罩 `••••`
- 点击"显示"切换明文/遮罩
- "撤销"按钮用 `var(--dan)` 色，点击触发 Danger 级 Modal

### 保存状态栏

固定在表单底部，三种状态：

| 状态 | 左侧 | 右侧 |
|------|------|------|
| **有更改** | `● 有未保存的更改` (warn 色) | [放弃] [保存] |
| **保存中** | `正在保存…` | [保存] btn-loading |
| **已保存** | `✓ 已保存` (success 色) | — |

检测更改：对比 form 初始值与当前值，任何字段变化即显示"有更改"状态。

---

## 6. 文件产出

| 文件 | 描述 |
|------|------|
| `qds-patterns.html` | 5 种补充模式的完整交互 demo |
| `qds-patterns-spec.md` | 本文档 — 设计规范 |
