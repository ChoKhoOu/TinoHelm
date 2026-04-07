# QDS 全局通知系统 — 设计规范

## 核心原则

**不是所有事件都该弹 toast。**

Toast 的本质是"打断用户注意力"。打断是有成本的——每次 toast 都消耗用户的认知带宽。高频事件用 toast = 注意力 DDoS。

还有一个常见错误：**用户点按钮触发的 API 错误不该弹 toast。** 用户盯着按钮等反馈，结果错误信息飞到右下角——视线断裂。API 错误应该就地反馈在触发操作的位置。

正确做法：**按事件来源和紧急度分层，不同层用不同通道。**

---

## 通知分层架构

```
                      紧急度
                        ↑
           ┌────────────┼────────────┐
           │   LAYER 4   │            │
           │  Modal 阻断  │            │
           │  (风控熔断)  │            │
           │             │            │
           │   LAYER 3   │            │
           │  Toast 通知  │            │
           │  (回测完成)  │            │
           │             │            │
           │   LAYER 2   │            │
           │  Inline 反馈 │            │
           │  (按钮→结果) │            │
           │             │            │
           │   LAYER 1   │            │
           │  StatusBar  │            │
           │  Ticker     │            │
           │  (成交流水)  │            │
           └────────────┼────────────┘
                        └──────────→ 频率
```

### Layer 1: 静默通道（高频、低紧急）

**不弹 toast，数据流入 UI 组件，用户主动看。**

| 事件 | 频率 | 通道 | UI 表现 |
|------|------|------|---------|
| `fill.new` | 每秒多次 | StatusBar ticker | 底部状态栏滚动：`BTC +0.5 @ 67,120` → 下一条覆盖上一条 |
| `order.update` | 每秒多次 | 订单表实时更新 | 表格行 tick flash，不弹通知 |
| `position.update` | 每秒多次 | 持仓表实时更新 | Mark price tick flash |
| `data.fetch.progress` | 每秒 | 拉取队列面板 | 进度条自增，不弹通知 |
| `backtest.progress` | 每秒 | 回测队列面板 | 进度条自增 |

**StatusBar Ticker 设计：**

```
┌─ StatusBar ──────────────────────────────────────────────────────┐
│ ● Binance 2ms │ Mem 4.2G │ CPU 12% │ BTC +0.5 @ 67,120 │ 20:15 │
│                                       ^^^^^^^^^^^^^^^^           │
│                                       fill ticker (淡入淡出)     │
└──────────────────────────────────────────────────────────────────┘
```

- 最新一笔成交覆盖上一笔，fade 过渡（不是滚动）
- 绿色 = 买入，红色 = 卖出
- 点击 ticker 区域 → 跳转到交易终端订单 tab
- 无成交时显示"idle"或隐藏

### Layer 2: Inline 反馈（用户点击触发的 API 调用）

**错误和成功反馈就地显示在触发操作的位置，不弹 toast。**

核心原则：**用户的眼睛在哪里，反馈就出现在哪里。**

用户点了"提交拉取"按钮，眼睛就盯着那个按钮。反馈必须出现在按钮上或按钮旁边，不能飞到屏幕另一个角落。

#### 按钮状态流转

所有用户触发的 API 调用，按钮都走同一个生命周期：

```
[提交拉取]  →  [提交中...]  →  [✓ 已入队]   →  [提交拉取]   (成功)
                            →  [✕ 失败]     →  [重试]      (失败)
```

| 状态 | 视觉表现 | 持续时间 |
|------|----------|----------|
| 默认 | 正常按钮样式 | — |
| Loading | 文字变"提交中..."，opacity 降低，pointer-events:none | API 响应前 |
| 成功 | 文字变"✓ 已入队"，背景变 `var(--suc)`，border 变绿 | 1.5 秒后恢复默认 |
| 失败 | 文字变"✕ 失败"，背景变 `var(--dan-d)`，border 变红 | 停留直到用户重新操作 |

#### 错误信息显示位置

```
场景1: 对话框内的 API 调用 (拉取数据、删除)
  → 错误信息显示在对话框底部，提交按钮上方
  ┌─ 拉取对话框 ─────────────────────────────┐
  │  ...表单...                               │
  │  ✕ 服务器错误 (500): Internal Server Error │  ← 红色错误信息
  │                         [取消]  [✕ 失败]   │  ← 按钮变红
  └──────────────────────────────────────────┘

场景2: 页面上独立的按钮 (压缩、扫描、启动策略)
  → 按钮自身变色 + 旁边出现错误文本
  [⊕ 压缩]  →  [✕ 压缩失败]  "磁盘空间不足"
                ^^^^^^^^       ^^^^^^^^^^^
                按钮变红        旁边显示原因，3 秒后淡出

场景3: 表格行内的操作按钮 (撤单、平仓、删除)
  → 按钮变色，行内显示结果
  │ BTCUSDT │ Long │ ... │ [✕ 平仓失败] │
  │         │      │     │ Insufficient margin │  ← 行内错误提示
```

#### 具体场景映射

| 用户操作 | API | 成功反馈 | 失败反馈 |
|----------|-----|---------|---------|
| 拉取数据 → 提交 | `POST /api/data/fetch-batch` | 按钮 "✓ N 个已入队" → 关闭对话框 | 对话框内红色错误条 + 按钮变红 |
| 删除数据集 | `DELETE /api/data/catalog/{id}` | 按钮 "✓ 已删除" → 关闭对话框 → 表格刷新 | 对话框内红色错误条 |
| 压缩 | `POST /api/data/compact` | 按钮 "✓ 压缩完成" (1.5s) | 按钮 "✕ 失败" + 旁边原因 |
| 扫描 | `POST /api/data/scan` | 按钮 "✓ 同步 N 个" (1.5s) | 同上 |
| 提交回测 | `POST /api/backtests` | 按钮 "✓ 已加入队列" → 返回列表 | 表单内红色错误条 |
| 停止策略 | Modal 确认 → API | Modal "✓ 已停止" → 自动关闭 | Modal 内红色错误条 |
| 撤单 | `DELETE /api/orders/{id}` | 按钮 "✓" (1s) → 行移除 | 按钮 "✕" + 行内错误 |
| 全部平仓 | Modal → API | Modal "✓ 全部已平" → 关闭 | Modal 内错误条 |

#### 实现模式

```typescript
// src/hooks/use-action.ts
// 封装 API 调用 + 按钮状态管理

type ActionState = 'idle' | 'loading' | 'success' | 'error';

function useAction<T>(apiFn: () => Promise<T>) {
  const [state, setState] = useState<ActionState>('idle');
  const [error, setError] = useState<string | null>(null);

  async function execute() {
    setState('loading');
    setError(null);
    try {
      const result = await apiFn();
      setState('success');
      setTimeout(() => setState('idle'), 1500);  // 1.5s 后恢复
      return result;
    } catch (e) {
      setState('error');
      setError(e.message || '操作失败');
      // 不自动恢复，等用户重新操作
      return null;
    }
  }

  return { state, error, execute };
}

// 用法:
// const { state, error, execute } = useAction(() => api.fetchBatch(params));
// <Button state={state} onClick={execute}>提交拉取</Button>
// {error && <InlineError>{error}</InlineError>}
```

#### InlineError 组件

```typescript
// src/components/qds/inline-error.tsx
// 红色错误提示，出现在操作位置附近

export function InlineError({ children }: { children: string }) {
  return (
    <div className="flex items-center gap-1.5 text-qds-danger font-mono text-xs mt-2 animate-qds-fade-up">
      <span>✕</span>
      <span>{children}</span>
    </div>
  );
}
```

### Layer 3: Toast 通知（后台异步事件，用户没有在等）

**只用于用户没有主动等待的后台事件。**

关键判断标准：**用户触发操作后是否还在盯着结果？**
- 提交回测后回到列表继续做别的 → 回测完成时弹 toast ✓
- 点"提交拉取"等 API 返回 → 不弹 toast，inline 反馈 ✗
- 10 分钟前提交的数据拉取完成了 → 弹 toast ✓

| 事件 | 触发频率 | Toast 类型 | 内容 |
|------|----------|-----------|------|
| `backtest.completed` | 分钟级 | Success | `BT-0401 完成 · Sharpe 2.14 · 22m` + [查看] |
| `backtest.failed` | 分钟级 | Error | `BT-0402 失败 · OOM @ 82%` + [查看] |
| `data.fetch.completed` | 分钟级 | Success | `BTCUSDT klines 拉取完成 · 1.3M bars` |
| `data.fetch.failed` | 分钟级 | Error | `ETHUSDT aggTrades 拉取失败 · Rate limit` |
| `strategy.started` | 手动触发 | Info | `MM-perp v3.2 已启动` |
| `strategy.stopped` | 手动触发 | Info | `MM-perp v3.2 已停止` |
| `connection.degraded` | 偶发 | Warning | `OKX 延迟升至 280ms，已切换备用` |
| `connection.restored` | 偶发 | Success | `OKX 连接恢复 · 3ms` |

**注意：`api.error` 不在这个表里。** API 错误走 Layer 2 inline 反馈。

**Toast 行为规范：**

| 属性 | 值 |
|------|-----|
| 最大同屏 | 3 个 |
| 入场 | 右侧滑入 `translateX(20px) → 0`，280ms |
| 自动消失 | 5 秒（底部 2px 进度条倒计时） |
| Hover 暂停 | 鼠标悬停时停止倒计时 |
| 手动关闭 | 右上角 × |
| 位置 | 右下角，StatusBar 上方 |
| 排队 | 超过 3 个时排队，前一个消失后下一个入场 |

**去重规则：**

| 规则 | 场景 |
|------|------|
| 同 ID 不重复 | 同一个 backtest/job 不会弹两次 "完成" |
| 连接事件去重 | 同一交易所 30s 内只弹一次 degraded |

### Layer 4: Modal 阻断（极低频、极高紧急）

**阻断用户操作，必须确认后才能继续。**

| 事件 | 触发条件 | Modal 类型 |
|------|----------|-----------|
| `risk.daily_limit_hit` | 日亏损触及限额 | Danger Modal：红色，显示亏损金额 + "所有策略已自动暂停" |
| `risk.max_drawdown` | 最大回撤触及阈值 | Danger Modal |
| `connection.all_lost` | 所有交易所断连 | Warning Modal：显示影响范围 + 手动重连按钮 |
| `risk.liquidation_warning` | 接近强平 | Danger Modal：显示距强平价距离 |

---

## 事件 → 通道映射表

```typescript
// src/lib/notification-router.ts

type NotificationChannel = 'silent' | 'ticker' | 'inline' | 'toast' | 'modal';

const ROUTING_TABLE: Record<string, {
  channel: NotificationChannel;
  type?: 'success' | 'error' | 'warning' | 'info';
  dedupeKey?: (event: any) => string;
  dedupeWindowMs?: number;
}> = {
  // Layer 1: Silent — 数据流入 UI，不通知
  'fill.new':              { channel: 'ticker' },
  'order.update':          { channel: 'silent' },
  'order.cancelled':       { channel: 'silent' },
  'position.update':       { channel: 'silent' },
  'mark_price.update':     { channel: 'silent' },
  'funding.settled':       { channel: 'silent' },
  'backtest.progress':     { channel: 'silent' },
  'data.fetch.progress':   { channel: 'silent' },

  // Layer 2: Inline — 用户操作的 API 反馈 (不走 router，由 useAction hook 处理)
  // 'api.response.*' → 不在这里，在 useAction() 里处理

  // Layer 3: Toast — 后台异步事件
  'backtest.completed':    { channel: 'toast', type: 'success', dedupeKey: e => e.id },
  'backtest.failed':       { channel: 'toast', type: 'error',   dedupeKey: e => e.id },
  'data.fetch.completed':  { channel: 'toast', type: 'success', dedupeKey: e => e.jobId },
  'data.fetch.failed':     { channel: 'toast', type: 'error',   dedupeKey: e => e.jobId },
  'strategy.started':      { channel: 'toast', type: 'info' },
  'strategy.stopped':      { channel: 'toast', type: 'info' },
  'connection.degraded':   { channel: 'toast', type: 'warning', dedupeKey: e => e.exchange, dedupeWindowMs: 30000 },
  'connection.restored':   { channel: 'toast', type: 'success', dedupeKey: e => e.exchange },

  // Layer 4: Modal — 紧急阻断
  'risk.daily_limit_hit':      { channel: 'modal' },
  'risk.max_drawdown':         { channel: 'modal' },
  'risk.liquidation_warning':  { channel: 'modal' },
  'connection.all_lost':       { channel: 'modal' },
};
```

---

## Fill Ticker 实现

Fill 不走 toast，走 StatusBar 里的专属 ticker 区域。

```typescript
// src/components/layout/fill-ticker.tsx

// 最新一笔 fill 覆盖显示，fade 过渡
// 无 fill 时隐藏该区域

interface TickerState {
  text: string;        // "BTC +0.5 @ 67,120"
  side: 'buy' | 'sell';
  timestamp: number;
}

// 行为：
// 1. 新 fill 进来 → 更新 state → fade out 旧文本 → fade in 新文本 (200ms)
// 2. 5 秒无新 fill → fade out → 区域折叠
// 3. 点击 → router.push('/trading?tab=orders')
```

StatusBar 中的位置：

```
│ ... │ CPU 12% │  BTC +0.5 @ 67,120  │ 20:15:32 │
│                  ↑                                │
│                  fill-ticker 区域                  │
│                  买=绿色, 卖=红色                  │
│                  200ms fade 切换                   │
```

---

## Toast 合并策略

当同类异步事件在短时间内完成时，合并显示：

```
单次：
┌─────────────────────────────────────┐
│ ✓ BTCUSDT klines 拉取完成 · 1.3M bars │
└─────────────────────────────────────┘

合并（3 个 fetch job 在 10 秒内先后完成）：
┌─────────────────────────────────────┐
│ ✓ 3 个数据拉取任务完成               │
│   BTCUSDT klines · ETHUSDT aggTrades │
│   SOLUSDT klines                     │
└─────────────────────────────────────┘
```

---

## 通知中心（可选，后续迭代）

Topbar 右侧的 🔔 按钮，点击展开下拉面板，显示历史通知列表。

```
┌─ 通知中心 ──────────────────────────┐
│ 今天                                 │
│ ✓ BT-0401 完成 Sharpe 2.14   2 分钟前 │
│ ✕ BT-0402 失败 OOM @ 82%     5 分钟前 │
│ ⚠ OKX 延迟升至 280ms         8 分钟前 │
│                                      │
│ 昨天                                 │
│ ✓ BTCUSDT klines 拉取完成    昨天 22:14│
│ ...                                  │
│                     全部已读 · 清除    │
└──────────────────────────────────────┘
```

- Toast 消失后的通知不会丢失，都存在通知中心
- 未读通知在 🔔 上显示红色数字 badge
- Layer 4 Modal 的事件也会记录到通知中心

---

## 总结

| 来源 | 频率 | 紧急度 | 通道 | 例子 |
|------|------|--------|------|------|
| WS 推送 | 高频 (>1/s) | 低 | Layer 1: Silent / Ticker | fill, order, position, progress |
| 用户点击 → API 响应 | 即时 | 中 | Layer 2: Inline 就地反馈 | 提交拉取 500, 删除失败, 压缩完成 |
| 后台异步完成 | 低频 (<1/min) | 中 | Layer 3: Toast | 回测完成, fetch job 完成, 连接降级 |
| 系统级风险 | 极低频 | 极高 | Layer 4: Modal | 风控熔断, 全部断连, 接近强平 |

**API 错误永远不走 toast。** 它走 Layer 2 inline 反馈——就地显示在按钮/对话框/表格行里。
**Fill 永远不走 toast。** 它走 Layer 1 StatusBar ticker。