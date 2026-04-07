# NautilusTrader 从入门到精通完全技术指南

> 本文档整合了 NautilusTrader 实盘交易、回测体系和策略开发三大核心模块的全部技术细节，面向加密货币量化开发工程师，以工程实践为导向。基于官方文档 `concepts/live/`、`concepts/backtesting/`、`concepts/strategies/` 及其全部关联页面深度调研整理。

---

# 第一部分：架构总览与核心设计

## 1.1 系统架构设计哲学

NautilusTrader 是一个 Rust 内核 + Python 控制面的生产级量化交易引擎，其核心价值在于**回测与实盘代码零修改切换**。采用了多种关键架构模式：领域驱动设计（DDD）、事件驱动架构、端口与适配器模式（六边形架构）以及 Crash-only 设计。其架构质量属性按权重排序为：**可靠性 > 性能 > 模块化 > 可测试性 > 可维护性 > 可部署性**。

系统支持三种环境上下文：**Backtest**（历史数据 + 模拟交易所）、**Sandbox**（实时数据 + 模拟交易所）、**Live**（实时数据 + 真实交易所）。三种上下文共享同一个 `NautilusKernel` 核心，用户定义的 Actor 和 Strategy 组件在所有环境中行为一致。

## 1.2 核心组件关系

```
NautilusKernel（系统内核）
├── MessageBus      ← 组件间通信主干（Pub/Sub、Req/Rep、Point-to-Point）
├── Cache           ← 高性能内存数据存储（可 Redis 持久化）
├── DataEngine      ← 市场数据处理与路由
├── RiskEngine      ← 交易前风控检查与验证
├── ExecutionEngine ← 交易命令路由与状态跟踪
├── Portfolio       ← 跨策略仓位汇总与盈亏计算
├── OrderEmulator   ← 本地模拟高级订单类型（可选）
└── Trader          ← 管理 Actor / Strategy 生命周期
```

**数据流向**：外部数据 → DataClient 适配器（标准化）→ DataEngine → Cache → MessageBus → Strategy

**执行流向**：Strategy → OrderEmulator（可选）→ ExecAlgorithm（可选）→ RiskEngine → ExecutionEngine → ExecutionClient → 交易所

## 1.3 进程与线程模型

**单线程内核**（类 LMAX Disruptor 模式）：MessageBus 消息消费与派发、Strategy 逻辑与订单管理、RiskEngine 检查、Cache 读写——全部在**单一线程**上顺序执行。这保证了确定性事件排序，维持回测-实盘一致性。

**多线程后台服务**：网络 I/O（WebSocket/REST）、持久化（DataFusion 查询、数据库操作通过多线程 Tokio 运行时）、适配器异步操作。后台服务通过 channel 向单线程内核的 MessageBus 投递事件。

**事件循环**：默认使用 `uvloop`（Linux/macOS），显著优于标准 asyncio。Python 3.12+ 提供了额外的 asyncio 性能改进。

> ⚠️ **绝对不要阻塞事件循环**：Strategy 回调中进行模型推理、重计算或同步 I/O 会导致错过成交、数据延迟和订单延迟。重计算任务必须 offload 到 executor 或独立线程/进程。

## 1.4 Strategy 基类继承体系

```
Component (基础组件)
  └── Actor (nautilus_trader.common.actor.Actor)
        └── Strategy (nautilus_trader.trading.strategy.Strategy)
```

**Actor** 是所有组件的基类，提供数据订阅、缓存访问、时钟定时器、消息总线和投资组合查询能力。**Strategy** 继承 Actor 的全部能力，额外增加了 `OrderFactory`、订单提交/撤销/修改、持仓管理和订单事件回调。**Actor 文档中的所有内容对 Strategy 同样适用**。

---

# 第二部分：策略开发体系

## 2.1 Strategy 核心属性

| 属性 | 类型 | 说明 |
|---|---|---|
| `self.cache` | Cache | 共享缓存，存储 instruments、orders、positions 等 |
| `self.portfolio` | Portfolio | 投资组合状态与计算（PnL、净头寸、敞口） |
| `self.clock` | Clock | 当前时间、定时器和告警调度 |
| `self.log` | Logger | 结构化日志 |
| `self.msgbus` | MessageBus | 发布/订阅自定义消息 |
| `self.order_factory` | OrderFactory | 订单工厂（Strategy 独有） |
| `self.order_id_tag` | str | 订单 ID 标签 |
| `self.oms_type` | OmsType | 订单管理系统类型（NETTING/HEDGING） |
| `self.config` | StrategyConfig | 策略配置对象 |

## 2.2 生命周期状态机

```
PRE_INITIALIZED → INITIALIZED
    ↓ start()
STARTING → RUNNING
    ↓ stop()
STOPPING → STOPPED
    ↓ resume()
RESUMING → RUNNING
    ↓ reset()
RESETTING → INITIALIZED
    ↓ dispose()
DISPOSING → DISPOSED

特殊路径:
RUNNING → DEGRADING → DEGRADED    (降级)
RUNNING → FAULTING  → FAULTED     (故障)
```

**关键规则**：永远不要重写 `start()`、`stop()`、`reset()` 等系统方法，而是重写对应的 `on_start()`、`on_stop()`、`on_reset()` 钩子。

## 2.3 StrategyConfig 配置类

```python
from nautilus_trader.config import StrategyConfig

class MyMMConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    spread_atr_multiple: float = 2.0
    order_size: Decimal = Decimal("0.1")
    order_id_tag: str = "001"
    oms_type: str = "NETTING"
    manage_gtd_expiry: bool = False
    manage_contingent_orders: bool = False
```

**StrategyConfig 内置参数**中与做市策略最相关的：`manage_stop`（stop时自动市价平仓）、`market_exit_interval_ms`（100ms默认检查间隔）、`market_exit_reduce_only`（平仓单默认 reduce_only=True）、`external_order_claims`（认领外部订单的 instrument IDs）。Strategy ID 格式为 `{ClassName}-{order_id_tag}`。

## 2.4 完整事件回调清单

### 2.4.1 生命周期回调

| 方法 | 触发时机 | 典型用途 |
|---|---|---|
| `on_start()` | 策略启动 | 获取 instrument、注册指标、订阅数据、请求历史数据 |
| `on_stop()` | 策略停止 | 撤销所有订单、平仓、取消订阅 |
| `on_resume()` | 从停止状态恢复 | 重新建立市场连接 |
| `on_reset()` | 策略重置 | 重置指标和内部状态（回测间使用） |
| `on_dispose()` | 策略销毁 | 最终资源释放 |
| `on_degrade()` | 进入降级状态 | 减少交易活动 |
| `on_fault()` | 发生故障 | 错误处理 |
| `on_save() → dict[str, bytes]` | 状态持久化 | 返回需要保存的状态字典 |
| `on_load(state: dict[str, bytes])` | 状态加载 | 从字典恢复状态 |

### 2.4.2 数据回调

| 订阅方法 | 回调方法 | 数据类型 |
|---|---|---|
| `subscribe_bars()` | `on_bar(bar: Bar)` | OHLCV K线 |
| `subscribe_quote_ticks()` | `on_quote_tick(tick: QuoteTick)` | 最优买卖报价 |
| `subscribe_trade_ticks()` | `on_trade_tick(tick: TradeTick)` | 逐笔成交 |
| `subscribe_order_book_deltas()` | `on_order_book_deltas(deltas: OrderBookDeltas)` | 订单簿增量 |
| `subscribe_order_book_at_interval()` | `on_order_book(book: OrderBook)` | 定时订单簿快照 |
| `subscribe_order_book_depth()` | `on_order_book_depth(depth: OrderBookDepth10)` | 10档深度快照 |
| `subscribe_data()` | `on_data(data: Data)` | 自定义数据 |
| `subscribe_signal()` | `on_signal(signal: Data)` | 自定义信号 |
| `subscribe_instrument()` | `on_instrument(instrument: Instrument)` | 合约定义更新 |
| `subscribe_instrument_status()` | `on_instrument_status(data: InstrumentStatus)` | 合约状态更新 |
| `subscribe_mark_prices()` | `on_mark_price(data: MarkPriceUpdate)` | 标记价格 |
| `subscribe_index_prices()` | `on_index_price(data: IndexPriceUpdate)` | 指数价格 |
| `subscribe_funding_rates()` | `on_funding_rate(data: FundingRateUpdate)` | 资金费率 |
| `request_bars()` / `request_*()` | `on_historical_data(data: Data)` | 历史数据请求响应 |

### 2.4.3 订单事件回调

事件传递链：**具体处理器 → `on_order_event()` → `on_event()`**。

```python
on_order_initialized(event: OrderInitialized)
on_order_denied(event: OrderDenied)
on_order_emulated(event: OrderEmulated)
on_order_released(event: OrderReleased)
on_order_submitted(event: OrderSubmitted)
on_order_rejected(event: OrderRejected)
on_order_accepted(event: OrderAccepted)
on_order_canceled(event: OrderCanceled)
on_order_expired(event: OrderExpired)
on_order_triggered(event: OrderTriggered)
on_order_pending_update(event: OrderPendingUpdate)
on_order_pending_cancel(event: OrderPendingCancel)
on_order_modify_rejected(event: OrderModifyRejected)
on_order_cancel_rejected(event: OrderCancelRejected)
on_order_updated(event: OrderUpdated)
on_order_filled(event: OrderFilled)
on_order_event(event: OrderEvent)      # 所有订单事件汇聚点
```

### 2.4.4 持仓事件回调

```python
on_position_opened(event: PositionOpened)
on_position_changed(event: PositionChanged)
on_position_closed(event: PositionClosed)
on_position_event(event: PositionEvent)  # 汇聚点
```

### 2.4.5 定时器与告警

```python
on_timer(event: TimeEvent)    # 周期性定时器触发
on_alert(event: TimeEvent)    # 一次性告警触发
```

## 2.5 开仓平仓操作完整指南

### 2.5.1 OrderFactory 创建订单

```python
# 市价单
order = self.order_factory.market(
    instrument_id=self.instrument_id,
    order_side=OrderSide.BUY,
    quantity=Quantity.from_str("0.1"),
    time_in_force=TimeInForce.IOC,
)

# 限价单（做市核心）
order = self.order_factory.limit(
    instrument_id=self.instrument_id,
    order_side=OrderSide.BUY,
    quantity=Quantity.from_str("0.1"),
    price=Price.from_str("50000.00"),
    post_only=True,
    time_in_force=TimeInForce.GTC,
)

# 止损市价单
order = self.order_factory.stop_market(
    instrument_id=self.instrument_id,
    order_side=OrderSide.SELL,
    quantity=Quantity.from_str("0.1"),
    trigger_price=Price.from_str("49000.00"),
    trigger_type=TriggerType.LAST_PRICE,
    reduce_only=True,
)

# 止损限价单
order = self.order_factory.stop_limit(
    instrument_id=self.instrument_id,
    order_side=OrderSide.BUY,
    quantity=Quantity.from_str("0.1"),
    price=Price.from_str("51000.00"),
    trigger_price=Price.from_str("50500.00"),
    post_only=True,
)

# Bracket单（入场 + 止损 + 止盈）
bracket = self.order_factory.bracket(
    instrument_id=self.instrument_id,
    order_side=OrderSide.BUY,
    quantity=Quantity.from_str("0.1"),
    entry_price=Price.from_str("50000.00"),
    entry_order_type=OrderType.LIMIT,
    sl_trigger_price=Price.from_str("49500.00"),
    tp_price=Price.from_str("50500.00"),
    tp_order_type=OrderType.LIMIT,
    tp_post_only=True,
)
```

### 2.5.2 订单提交与管理

```python
self.submit_order(order)
self.submit_order(order, position_id=pos_id)
self.submit_order_list(bracket)
self.cancel_order(order)
self.cancel_orders([order1, order2, order3])
self.cancel_all_orders(self.instrument_id)
self.cancel_all_orders(self.instrument_id, order_side=OrderSide.BUY)
self.modify_order(order, quantity=new_qty, price=new_price)
self.close_position(position)
self.close_all_positions(self.instrument_id)
self.market_exit()  # 优雅退出
```

### 2.5.3 中高频 Alpha 策略典型交易决策模式

中高频 alpha 策略的核心模式是：**在 `on_bar` 中计算因子/信号做方向决策，在 `on_quote_tick` 中精确择时入场，在 `on_order_filled` 中挂止损止盈级联单管理风险**。以下按场景展示完整模式。

#### 模式一：多因子信号驱动（on_bar 决策 + on_quote_tick 执行）

```python
class AlphaConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType                      # 信号 bar（如 1-MINUTE）
    fast_ema_period: int = 10
    slow_ema_period: int = 30
    atr_period: int = 14
    atr_sl_multiple: float = 1.5           # 止损 = ATR × 倍数
    atr_tp_multiple: float = 2.5           # 止盈 = ATR × 倍数
    order_size: Decimal = Decimal("0.1")
    max_position_size: Decimal = Decimal("0.5")
    entry_timeout_bars: int = 3            # 信号有效期（bars）
    order_id_tag: str = "ALPHA01"

class AlphaStrategy(Strategy):
    def __init__(self, config: AlphaConfig):
        super().__init__(config)
        self.instrument: Instrument | None = None
        self.signal: int = 0               # -1 / 0 / +1
        self.signal_bar_count: int = 0
        self.entry_price: float = 0.0

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Instrument not found: {self.config.instrument_id}")
            self.stop()
            return

        # 注册指标（框架自动在 on_bar 前更新）
        self.ema_fast = ExponentialMovingAverage(self.config.fast_ema_period)
        self.ema_slow = ExponentialMovingAverage(self.config.slow_ema_period)
        self.atr = AverageTrueRange(self.config.atr_period)
        self.register_indicator_for_bars(self.config.bar_type, self.ema_fast)
        self.register_indicator_for_bars(self.config.bar_type, self.ema_slow)
        self.register_indicator_for_bars(self.config.bar_type, self.atr)

        # 请求历史 bar 预热指标
        self.request_bars(self.config.bar_type)

        # 同时订阅 bar（信号）和 quote_tick（执行）
        self.subscribe_bars(self.config.bar_type)
        self.subscribe_quote_ticks(self.config.instrument_id)

    def on_bar(self, bar: Bar) -> None:
        """信号计算层：bar 级别因子打分"""
        if not self.indicators_initialized():
            return

        # ---- 因子计算 ----
        fast = self.ema_fast.value
        slow = self.ema_slow.value
        atr = self.atr.value
        if atr <= 0:
            return

        # 动量因子：EMA 金叉/死叉
        cross_up = fast > slow and self.ema_fast.count > 1
        cross_down = fast < slow and self.ema_fast.count > 1

        # 波动率过滤：ATR 太低时不交易（避免震荡区间）
        vol_filter = atr > float(bar.close) * 0.001  # ATR > 0.1% 才有信号

        # ---- 信号生成 ----
        prev_signal = self.signal
        if cross_up and vol_filter:
            self.signal = 1
            self.signal_bar_count = 0
        elif cross_down and vol_filter:
            self.signal = -1
            self.signal_bar_count = 0
        else:
            self.signal_bar_count += 1
            if self.signal_bar_count >= self.config.entry_timeout_bars:
                self.signal = 0   # 信号过期

        # ---- 平仓逻辑：信号反转时平掉反向仓位 ----
        position = self._get_position()
        if position is not None and position.is_open:
            should_close = (
                (position.is_long and self.signal == -1)
                or (position.is_short and self.signal == 1)
            )
            if should_close:
                self.cancel_all_orders(self.config.instrument_id)
                self.close_position(position)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        """执行层：tick 级别精确入场"""
        if self.signal == 0:
            return

        position = self._get_position()
        # 已有同向仓位或有在途订单，不重复开仓
        if position is not None and position.is_open:
            return
        if self.cache.orders_open(instrument_id=self.config.instrument_id):
            return

        # ---- 用限价单在对手方最优价入场（减少滑点） ----
        if self.signal == 1:
            entry_price = tick.ask_price  # 激进：直接 lift ask
            order = self.order_factory.limit(
                instrument_id=self.config.instrument_id,
                order_side=OrderSide.BUY,
                quantity=self.instrument.make_qty(self.config.order_size),
                price=entry_price,
                time_in_force=TimeInForce.IOC,  # IOC 避免挂单暴露
            )
        elif self.signal == -1:
            entry_price = tick.bid_price
            order = self.order_factory.limit(
                instrument_id=self.config.instrument_id,
                order_side=OrderSide.SELL,
                quantity=self.instrument.make_qty(self.config.order_size),
                price=entry_price,
                time_in_force=TimeInForce.IOC,
            )
        else:
            return

        self.submit_order(order)
        self.signal = 0  # 消费信号，防止重复触发

    def on_order_filled(self, event: OrderFilled) -> None:
        """风控层：成交后立即挂止损止盈"""
        atr = self.atr.value
        if atr <= 0:
            return

        fill_px = float(event.last_px)

        if event.order_side == OrderSide.BUY:
            # 买入成交 → 挂 SELL 止损 + SELL 止盈
            sl_price = fill_px - atr * self.config.atr_sl_multiple
            tp_price = fill_px + atr * self.config.atr_tp_multiple

            sl = self.order_factory.stop_market(
                instrument_id=self.config.instrument_id,
                order_side=OrderSide.SELL,
                quantity=event.last_qty,
                trigger_price=self.instrument.make_price(sl_price),
                reduce_only=True,
            )
            tp = self.order_factory.limit(
                instrument_id=self.config.instrument_id,
                order_side=OrderSide.SELL,
                quantity=event.last_qty,
                price=self.instrument.make_price(tp_price),
                time_in_force=TimeInForce.GTC,
                reduce_only=True,
            )
            self.submit_order(sl)
            self.submit_order(tp)

        elif event.order_side == OrderSide.SELL:
            # 减仓/平仓成交 → 如果是平仓方向，撤销残留的止损止盈
            position = self._get_position()
            if position is None or position.is_closed:
                self.cancel_all_orders(self.config.instrument_id)

    def _get_position(self) -> "Position | None":
        positions = self.cache.positions(
            instrument_id=self.config.instrument_id,
            strategy_id=self.id,
        )
        return positions[0] if positions else None

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        self.close_all_positions(self.config.instrument_id)
```

#### 模式二：Bracket 订单一次性提交（入场+止损+止盈原子操作）

比模式一更简洁，适合信号明确、不需要动态调整止损的场景：

```python
def on_bar(self, bar: Bar) -> None:
    if not self.indicators_initialized() or self._has_open_position():
        return

    atr = self.atr.value
    if self.signal == 1:
        bracket = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY,
            quantity=self.instrument.make_qty(self.config.order_size),
            entry_price=self.instrument.make_price(float(bar.close)),
            entry_order_type=OrderType.LIMIT,
            sl_trigger_price=self.instrument.make_price(float(bar.close) - atr * 1.5),
            tp_price=self.instrument.make_price(float(bar.close) + atr * 2.5),
            tp_order_type=OrderType.LIMIT,
            entry_time_in_force=TimeInForce.IOC,
        )
        self.submit_order_list(bracket)
```

#### 模式三：微观结构 alpha（on_order_book_deltas 驱动）

订单簿不平衡因子直接驱动短线方向判断：

```python
def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
    book = self.cache.order_book(self.config.instrument_id)
    if book is None or book.best_bid_price() is None:
        return

    bid_sz = float(book.best_bid_size())
    ask_sz = float(book.best_ask_size())
    total = bid_sz + ask_sz
    if total == 0:
        return

    imbalance = (bid_sz - ask_sz) / total  # [-1, 1]

    # 深度不平衡超过阈值 → 预测短期价格方向
    if imbalance > self.config.imbalance_threshold and not self._has_open_position():
        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY,
            quantity=self.instrument.make_qty(self.config.order_size),
        )
        self.submit_order(order)
    elif imbalance < -self.config.imbalance_threshold and not self._has_open_position():
        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.SELL,
            quantity=self.instrument.make_qty(self.config.order_size),
        )
        self.submit_order(order)

    # 持仓中：不平衡反转 → 平仓
    position = self._get_position()
    if position is not None and position.is_open:
        if (position.is_long and imbalance < -0.1) or \
           (position.is_short and imbalance > 0.1):
            self.close_position(position)
```

#### 模式四：定时器驱动再平衡（适合跨品种/统计套利）

```python
def on_start(self) -> None:
    # ...（指标和订阅初始化）
    # 每 30 秒重新评估 alpha 并调整仓位
    self.clock.set_timer("rebalance", interval=pd.Timedelta(seconds=30))

def on_timer(self, event: TimeEvent) -> None:
    if event.name != "rebalance":
        return
    # 从 cache 获取最新行情计算因子
    for iid in self.universe:
        book = self.cache.order_book(iid)
        bars = self.cache.bars(BarType.from_str(f"{iid}-1-MINUTE-LAST-EXTERNAL"))
        alpha_score = self.compute_alpha(book, bars)
        target_position = self.portfolio_optimizer(alpha_score)
        self._adjust_position(iid, target_position)

def _adjust_position(self, iid: InstrumentId, target_qty: Decimal) -> None:
    """计算目标仓位与当前仓位的差值，提交调仓订单"""
    current_qty = Decimal(0)
    positions = self.cache.positions(instrument_id=iid, strategy_id=self.id)
    if positions:
        current_qty = positions[0].quantity * (1 if positions[0].is_long else -1)

    delta = target_qty - current_qty
    if abs(delta) < self.config.min_trade_size:
        return

    side = OrderSide.BUY if delta > 0 else OrderSide.SELL
    instrument = self.cache.instrument(iid)
    order = self.order_factory.market(
        instrument_id=iid,
        order_side=side,
        quantity=instrument.make_qty(abs(delta)),
    )
    self.submit_order(order)
```

#### 关键设计原则总结

| 关注点 | 推荐做法 |
|---|---|
| **信号与执行分离** | `on_bar` 算因子设 signal，`on_quote_tick` 看 signal 下单 |
| **防止重复开仓** | 检查 `cache.orders_open()` 和现有仓位后再提交 |
| **信号消费** | 下单后立即将 signal 清零，避免同一信号多次触发 |
| **入场方式** | IOC 限价单（滑点可控）> 市价单（保证成交但滑点大） |
| **止损止盈** | `on_order_filled` 中级联挂单，或用 bracket 原子提交 |
| **平仓时机** | 信号反转、止损触发、定时器再平衡、`on_stop` 兜底平仓 |
| **仓位查询** | `self.cache.positions(instrument_id=..., strategy_id=self.id)` |
| **订单清理** | 平仓后 `cancel_all_orders` 清除残留止损止盈单 |

## 2.6 同时监听多种数据类型与多品种多时间框架

**完全可以**同时订阅 bars、quote ticks、trade ticks、order book deltas 和自定义数据：

```python
def on_start(self) -> None:
    self.instrument = self.cache.instrument(self.instrument_id)
    self.register_indicator_for_bars(self.bar_type, self.atr)
    self.request_bars(self.bar_type, start=self.clock.utc_now() - pd.Timedelta(days=1))
    self.subscribe_bars(self.bar_type)
    self.subscribe_quote_ticks(self.instrument_id)
    self.subscribe_trade_ticks(self.instrument_id)
    self.subscribe_order_book_deltas(self.instrument_id)
```

**多时间框架**：

```python
def on_start(self) -> None:
    self.subscribe_bars(BarType.from_str("BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"))
    self.subscribe_bars(BarType.from_str("BTCUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL"))
    self.subscribe_bars(BarType.from_str("BTCUSDT-PERP.BINANCE-250-TICK-LAST-INTERNAL"))

def on_bar(self, bar: Bar) -> None:
    if "1-MINUTE" in str(bar.bar_type):
        self.update_short_term(bar)
    elif "1-HOUR" in str(bar.bar_type):
        self.update_trend(bar)
```

## 2.7 市场微观结构处理

### 2.7.1 订单簿数据层级

| 类型 | 说明 | 使用场景 |
|---|---|---|
| **L1_MBP** | 仅最优买卖价，默认 | 趋势策略、低频交易 |
| **L2_MBP** | 全深度，每个价格聚合一个订单 | **做市策略核心** |
| **L3_MBO** | 全深度，逐笔订单 | 超高频、队列位置分析 |

### 2.7.2 订阅方式

```python
# 增量订阅
self.subscribe_order_book_deltas(
    instrument_id=self.instrument_id,
    book_type=BookType.L2_MBP,
    managed=True,
)

# 10档深度快照
self.subscribe_order_book_depth(instrument_id=self.instrument_id)

# 定时快照
self.subscribe_order_book_at_interval(
    instrument_id=self.instrument_id,
    interval_ms=100,
)
```

### 2.7.3 做市微观结构信号

```python
def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
    book = self.cache.order_book(self.instrument_id)
    if book is None:
        return
    best_bid = book.best_bid_price()
    best_ask = book.best_ask_price()
    spread = book.spread()
    mid = book.midpoint()

    bid_size = float(book.best_bid_size())
    ask_size = float(book.best_ask_size())
    total = bid_size + ask_size
    if total > 0:
        imbalance = (bid_size - ask_size) / total
        vamp = (float(best_bid) * ask_size + float(best_ask) * bid_size) / total

    bids = book.bids()
    asks = book.asks()
    bid_depth_5 = sum(float(level.size()) for level in bids[:5])
    ask_depth_5 = sum(float(level.size()) for level in asks[:5])
```

## 2.8 高级模式

### 2.8.1 ExecAlgorithm 执行算法

```python
order = self.order_factory.market(
    instrument_id=self.instrument_id,
    order_side=OrderSide.BUY,
    quantity=Quantity.from_str("10.0"),
    exec_algorithm_id=ExecAlgorithmId("TWAP"),
    exec_algorithm_params={"horizon_secs": 60, "interval_secs": 5.0},
)
self.submit_order(order)
```

### 2.8.2 Actor 与 Strategy 协作

```python
# Actor 发送信号
self.publish_signal(name="BOOK_IMBALANCE", value=str(imbalance), ts_event=ts)

# Strategy 接收
def on_start(self):
    self.subscribe_signal("BOOK_IMBALANCE")

def on_signal(self, signal):
    imbalance = float(signal.value)
```

### 2.8.3 Controller 动态管理策略

```python
class MarketRegimeController(Controller):
    def on_bar(self, bar: Bar):
        regime = self.detect_regime(bar)
        if regime == "trending":
            self.create_strategy(TrendStrategy(TrendConfig(...)), start=True)
        elif regime == "ranging":
            self.create_strategy(MarketMaker(MMConfig(...)), start=True)
```

### 2.8.4 多策略组合

```python
strategies = [
    MarketMaker(MMConfig(instrument_id=btc, order_id_tag="MM01")),
    MarketMaker(MMConfig(instrument_id=eth, order_id_tag="MM02")),
    TrendFollower(TrendConfig(instrument_id=btc, order_id_tag="TF01")),
]
for s in strategies:
    engine.add_strategy(s)
```

## 2.9 策略工程最佳实践

### 2.9.1 指标注册

```python
def on_start(self) -> None:
    self.atr = AverageTrueRange(self.config.atr_period)
    self.register_indicator_for_bars(self.config.bar_type, self.atr)
    self.request_bars(self.config.bar_type)
    self.subscribe_bars(self.config.bar_type)

def on_bar(self, bar: Bar) -> None:
    if not self.indicators_initialized():
        return
```

### 2.9.2 状态持久化

```python
def on_save(self) -> dict[str, bytes]:
    return {
        "inventory": str(self.inventory).encode(),
        "avg_entry_price": str(self.avg_entry).encode(),
    }

def on_load(self, state: dict[str, bytes]) -> None:
    if "inventory" in state:
        self.inventory = Decimal(state["inventory"].decode())
```

### 2.9.3 防御性编程

```python
def on_start(self) -> None:
    self.instrument = self.cache.instrument(self.instrument_id)
    if self.instrument is None:
        self.log.error(f"Could not find instrument for {self.instrument_id}")
        self.stop()
        return

def on_stop(self) -> None:
    self.cancel_all_orders(self.instrument_id)
    self.close_all_positions(self.instrument_id, reduce_only=True)
```

---

# 第三部分：回测体系

## 3.1 两级回测 API

| 特性 | Low-level（BacktestEngine） | High-level（BacktestNode） |
|---|---|---|
| 数据管理 | 手动加载 Data 对象列表 | ParquetDataCatalog 声明式配置 |
| 内存 | 全量加载到 RAM | 支持流式分块加载 |
| 多次运行 | `engine.reset()` 保留数据 | 每个 BacktestRunConfig 独立引擎 |
| 参数优化 | 适合（重用数据） | 适合（配置序列化） |
| 数据格式 | 任意（CSV/Binary/原始） | Parquet（Nautilus 格式） |

## 3.2 BacktestEngine 完整 API

```python
engine = BacktestEngine(config=BacktestEngineConfig())

# 添加交易所
engine.add_venue(venue, oms_type, account_type, starting_balances, ...)

# 添加品种和数据
engine.add_instrument(instrument)
engine.add_data(data, sort=True)
engine.sort_data()
engine.add_data_iterator(name, generator)

# 添加策略
engine.add_strategy(strategy)
engine.add_actor(actor)
engine.add_exec_algorithm(exec_algorithm)

# 运行
engine.run(start=None, end=None, streaming=False)
engine.end()

# 状态管理
engine.reset()
engine.clear_data()
engine.dispose()

# 结果
result = engine.get_result()
```

## 3.3 高级配置体系

### 3.3.1 BacktestRunConfig

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `engine` | `BacktestEngineConfig` | `BacktestEngineConfig()` | 引擎配置 |
| `venues` | `list[BacktestVenueConfig]` | 必填 | 交易所配置列表 |
| `data` | `list[BacktestDataConfig]` | 必填 | 数据配置列表 |
| `chunk_size` | `int \| None` | `None` | 流式处理每批数据量 |
| `start` / `end` | `datetime \| str \| int` | `None` | 回测时间范围 |

### 3.3.2 BacktestVenueConfig 核心参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `name` | 必填 | 交易所名称 |
| `oms_type` | 必填 | `"HEDGING"` 或 `"NETTING"` |
| `account_type` | 必填 | `"CASH"` / `"MARGIN"` / `"BETTING"` |
| `starting_balances` | 必填 | 起始余额 |
| `book_type` | `"L1_MBP"` | 订单簿类型 |
| `fill_model` | `None` | 成交模型 |
| `latency_model` | `None` | 延迟模型 |
| `fee_model` | `None` | 手续费模型 |
| `margin_model` | `None` | 保证金模型 |
| `bar_execution` | `True` | Bar 触发撮合 |
| `trade_execution` | `True` | Trade tick 触发撮合 |
| `bar_adaptive_high_low_ordering` | `False` | 自适应 OHLC 顺序（准确率 75-85%） |
| `liquidity_consumption` | `False` | 流动性消耗追踪 |
| `queue_position` | `False` | 限价单队列位置追踪 |
| `price_protection_points` | `0` | 价格保护边界 |

### 3.3.3 BacktestDataConfig

| 参数 | 说明 |
|---|---|
| `catalog_path` | Parquet 目录路径 |
| `data_cls` | 数据类 |
| `instrument_id` / `instrument_ids` | 品种 ID |
| `start_time` / `end_time` | 数据时间范围 |
| `filter_expr` | PyArrow 过滤表达式 |
| `bar_spec` | Bar 规格 |

## 3.4 数据层级与交易所匹配

| Data Type | L1_MBP | L2_MBP | L3_MBO |
|---|---|---|---|
| `QuoteTick` | 更新 book | 忽略 | 忽略 |
| `TradeTick` | 触发撮合 | 触发撮合 | 触发撮合 |
| `Bar` | 更新 book | 忽略 | 忽略 |
| `OrderBookDelta` | 忽略 | 更新 book | 更新 book |
| `OrderBookDepth10` | 更新 book | 更新 book | 更新 book |

> ⚠️ 指定 L2/L3 book_type 时，quotes 和 bars 不会更新订单簿。必须提供 order book delta 数据，否则订单永远不会成交。

## 3.5 撮合引擎三阶段主循环

1. **交易所处理数据**：更新内部订单簿，撮合引擎迭代匹配现有挂单
2. **策略接收数据**：DataEngine 通过回调分发数据，策略可提交/取消/修改订单
3. **结算循环**：排空命令队列并再次撮合。**重复直到无待处理命令**——级联订单在同一时间戳内结算

## 3.6 成交价格确定

### L2/L3 数据

| 订单类型 | 成交价 |
|---|---|
| `MARKET` | 逐档匹配订单簿 (taker) |
| `LIMIT` | 限价（maker） |
| `STOP_MARKET` | 触发后逐档匹配 |
| `STOP_LIMIT` | 触发后以限价匹配 |

### L1 数据（quotes, trades, bars）

| 订单类型 | BUY 成交价 | SELL 成交价 |
|---|---|---|
| `MARKET` | Best ask | Best bid |
| `LIMIT` | Limit price | Limit price |
| `STOP_MARKET` | Best ask | Best bid |

### Bar 数据 Stop 单特殊行为

- **跳空场景**（bar 开盘越过触发价）：以市价（开盘价）成交
- **穿越场景**（bar H/L 穿过触发价）：以触发价成交

## 3.7 Bar 数据 OHLC 处理

每个 bar 转换为 4 个价格更新：Open → High → Low → Close，成交量均分 25%。

启用 `bar_adaptive_high_low_ordering=True` 后根据 Open 与 High/Low 距离动态推断路径，准确率约 75-85%。

> ⚠️ `ts_init` 必须代表 bar 的**收盘时间**。开盘时间戳需设置 `ts_init_delta` = bar 周期纳秒数。

## 3.8 FillModel、LatencyModel、FeeModel

### FillModel

```python
fill_model = FillModel(
    prob_fill_on_limit=0.2,  # 限价单触碰成交概率
    prob_slippage=0.5,       # 滑点概率（仅 L1）
    random_seed=42,
)
```

内置子类：`BestPriceFillModel`、`OneTickSlippageFillModel`、`TwoTierFillModel`、`ThreeTierFillModel`、`SizeAwareFillModel`、`LimitOrderPartialFillModel`、`VolumeSensitiveFillModel`、`CompetitionAwareFillModel`。

### LatencyModel

```python
latency_config = ImportableLatencyModelConfig(
    latency_model_path="nautilus_trader.backtest.models:LatencyModel",
    config_path="nautilus_trader.backtest.config:LatencyModelConfig",
    config={
        "base_latency_nanos": 5_000_000,
        "insert_latency_nanos": 2_000_000,
        "update_latency_nanos": 3_000_000,
        "cancel_latency_nanos": 1_000_000,
    },
)
```

### FeeModel

**MakerTakerFeeModel**：使用 Instrument 的 `maker_fee`/`taker_fee`。正值为佣金，负值为返佣。

### 保证金模型

| 模型 | 公式 | 适用场景 |
|---|---|---|
| `LeveragedMarginModel`（默认） | `(名义值 / 杠杆) × margin_init` | 加密货币交易所 |
| `StandardMarginModel` | `名义值 × margin_init` | 传统经纪商 |

## 3.9 ParquetDataCatalog 数据目录

```python
from nautilus_trader.persistence.catalog import ParquetDataCatalog

catalog = ParquetDataCatalog(Path.cwd() / "catalog")

# 写入（先写 Instrument）
catalog.write_data([EURUSD])
catalog.write_data(ticks)

# 查询
quotes = catalog.quote_ticks(instrument_ids=["EUR/USD.SIM"], start=start_ns, end=end_ns)
trades = catalog.trade_ticks(instrument_ids=["BTC/USD.BINANCE"])
bars = catalog.bars(bar_types=["BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"])
```

## 3.10 DataWrangler 数据转换

```python
from nautilus_trader.persistence.wranglers import (
    QuoteTickDataWrangler, TradeTickDataWrangler, BarDataWrangler,
)

# Quote Tick
wrangler = QuoteTickDataWrangler(instrument=instrument)
ticks = wrangler.process(df, default_volume=1_000_000.0, ts_init_delta=0)

# Trade Tick
wrangler = TradeTickDataWrangler(instrument=instrument)
ticks = wrangler.process(df, ts_init_delta=0)

# Bar
bar_type = BarType.from_str("AAPL.SIM-1-DAY-LAST-EXTERNAL")
wrangler = BarDataWrangler(bar_type=bar_type, instrument=instrument)
bars = wrangler.process(df, default_volume=1_000_000.0, ts_init_delta=0)
```

## 3.11 可视化与报告

```python
from nautilus_trader.analysis import create_tearsheet

engine.run()
create_tearsheet(engine=engine, output_path="tearsheet.html")

# 报告
orders_report = engine.trader.generate_orders_report()
fills_report = engine.trader.generate_fills_report()
positions_report = engine.trader.generate_positions_report()

# PortfolioAnalyzer
analyzer = engine.portfolio.analyzer
stats_pnls = analyzer.get_performance_stats_pnls()
stats_returns = analyzer.get_performance_stats_returns()
returns = analyzer.returns()
```

---

# 第四部分：实盘交易

## 4.1 TradingNode 与 LiveNode

| 特性 | TradingNode（Legacy/Python） | LiveNode（v2/Rust） |
|---|---|---|
| 实现语言 | Python asyncio | Rust (tokio) |
| 信号处理 | Unix: `loop.add_signal_handler` | 全平台 `tokio::signal::ctrl_c()` |

> ⚠️ 每个进程只能运行**一个** TradingNode 实例。并行需独立进程。

## 4.2 TradingNodeConfig 完整配置

```python
config = TradingNodeConfig(
    trader_id="MyTrader-001",
    timeout_connection=30.0,
    timeout_reconciliation=10.0,
    timeout_portfolio=10.0,
    timeout_disconnection=10.0,
    timeout_post_stop=5.0,
    load_state=True,
    save_state=True,
    cache=CacheConfig(...),
    message_bus=MessageBusConfig(...),
    data_engine=LiveDataEngineConfig(...),
    risk_engine=LiveRiskEngineConfig(...),
    exec_engine=LiveExecEngineConfig(...),
    portfolio=PortfolioConfig(...),
    logging=LoggingConfig(...),
    streaming=StreamingConfig(...),
    data_clients={"BINANCE": BinanceDataClientConfig(...)},
    exec_clients={"BINANCE": BinanceExecClientConfig(...)},
    strategies=[...],
    actors=[...],
)
```

## 4.3 LiveExecEngineConfig — 实盘最核心配置

```python
exec_engine = LiveExecEngineConfig(
    # 启动对账
    reconciliation=True,
    reconciliation_lookback_mins=None,
    reconciliation_startup_delay_secs=10.0,

    # 在途订单监控
    inflight_check_interval_ms=2000,
    inflight_check_threshold_ms=5000,
    inflight_check_retries=5,

    # 持续对账
    open_check_interval_secs=None,
    open_check_lookback_mins=60,

    # 仓位检查
    position_check_interval_secs=None,

    # 内存管理
    purge_closed_orders_interval_mins=None,
    purge_closed_orders_buffer_mins=None,
    purge_closed_positions_interval_mins=None,
    purge_closed_positions_buffer_mins=None,

    qsize=100_000,
)
```

## 4.4 CacheConfig 与 Redis 持久化

```python
cache = CacheConfig(
    database=DatabaseConfig(type="redis", host="localhost", port=6379, timeout=2.0),
    encoding="msgpack",
    buffer_interval_ms=100,
    tick_capacity=10_000,
    bar_capacity=10_000,
)
```

## 4.5 风控引擎

```python
risk_engine = LiveRiskEngineConfig(
    bypass=False,
    max_order_submit_rate="100/00:00:01",
    max_order_modify_rate="100/00:00:01",
    max_notional_per_order={"BTCUSDT.BINANCE": 1_000_000},
)
```

交易状态控制：`risk_engine.set_trading_state(TradingState.HALTED)` 停止所有新订单。

## 4.6 订单类型与高级订单

| 类型 | 描述 |
|---|---|
| `MARKET` | 市价单 |
| `LIMIT` | 限价单 |
| `STOP_MARKET` | 止损市价单 |
| `STOP_LIMIT` | 止损限价单 |
| `MARKET_TO_LIMIT` | 先市价后限价 |
| `MARKET_IF_TOUCHED` | 触及市价单 |
| `LIMIT_IF_TOUCHED` | 触及限价单 |
| `TRAILING_STOP_MARKET` | 追踪止损市价 |
| `TRAILING_STOP_LIMIT` | 追踪止损限价 |

执行指令：`post_only`（仅挂单）、`reduce_only`（仅减仓）、`display_qty`（冰山单）

## 4.7 执行对账机制

### 启动对账

1. 批量状态获取
2. 重复检查（去重 ClientOrderId）
3. 订单对账（生成缺失事件）
4. 仓位对账（按合约匹配净仓位）

### 在途订单监控

| 当前状态 | 超时解决为 |
|---|---|
| SUBMITTED | REJECTED |
| PENDING_UPDATE | CANCELED |
| PENDING_CANCEL | CANCELED |

## 4.8 适配器

### Binance（stable）

```python
BinanceDataClientConfig(
    account_type="USDT_FUTURES",
    environment="TESTNET",
)
BinanceExecClientConfig(
    account_type="USDT_FUTURES",
    use_gtd=True,
    use_reduce_only=True,
    futures_leverages={"BTCUSDT": 10},
)
```

### Bybit（stable）

```python
BybitDataClientConfig(
    product_types=[BybitProductType.LINEAR],
)
BybitExecClientConfig(
    product_types=[BybitProductType.LINEAR],
    futures_leverages={"BTCUSDT": 10},
)
```

### 全部集成

Binance、Bybit、Interactive Brokers、OKX、dYdX、BitMEX、Deribit、Hyperliquid、Kraken、Polymarket、Databento、Tardis 等。

## 4.9 日志系统

```python
LoggingConfig(
    log_level="INFO",
    log_level_file="DEBUG",
    log_file_format="json",
    log_component_levels={"RiskEngine": "DEBUG"},
    bypass_logging=False,
)
```

环境变量：`NAUTILUS_LOG="stdout=Info;fileout=Debug;RiskEngine=Error;is_colored"`

## 4.10 多节点 Producer/Consumer 模式

```python
# Producer 节点
message_bus=MessageBusConfig(
    database=DatabaseConfig(timeout=2),
    streams_prefix="binance",
    stream_per_topic=False,
)

# Consumer 节点
data_engine=LiveDataEngineConfig(
    external_clients=[ClientId("BINANCE_EXT")],
),
message_bus=MessageBusConfig(
    external_streams=["binance"],
),
```

## 4.11 生产环境启动模板

```python
config = TradingNodeConfig(
    trader_id="MyTrader-001",
    logging=LoggingConfig(log_level="INFO", log_level_file="DEBUG", log_file_format="json"),
    cache=CacheConfig(database=DatabaseConfig(host="localhost", port=6379, timeout=2)),
    exec_engine=LiveExecEngineConfig(reconciliation=True, open_check_interval_secs=10),
    data_clients={"BINANCE": BinanceDataClientConfig(...)},
    exec_clients={"BINANCE": BinanceExecClientConfig(...)},
    strategies=[...],
)

node = TradingNode(config=config)
node.add_data_client_factory("BINANCE", BinanceLiveDataClientFactory)
node.add_exec_client_factory("BINANCE", BinanceLiveExecClientFactory)
node.build()

try:
    node.run()
except KeyboardInterrupt:
    pass
finally:
    try:
        node.stop()
    finally:
        node.dispose()
```

---

# 第五部分：性能优化全攻略

## 5.1 数据加载优化（最大瓶颈）

```python
# ❌ 错误：每次 add_data 都排序
for bars in all_bars:
    engine.add_data(bars)

# ✅ 正确：延迟排序
for bars in all_bars:
    engine.add_data(bars, sort=False)
engine.sort_data()

# ✅ 正确：流式加载
engine.add_data_iterator("stream", data_generator())
engine.run()
```

## 5.2 引擎配置优化

```python
config = BacktestEngineConfig(
    logging=LoggingConfig(
        log_level="ERROR",
        bypass_logging=True,      # 最大单项加速
    ),
    risk_engine=RiskEngineConfig(
        bypass=True,              # 跳过风控检查
    ),
    run_analysis=False,           # 跳过回测后分析
)
```

## 5.3 参数优化时引擎重用

```python
for params in param_grid:
    strategy = MyStrategy(MyConfig(**params))
    engine.add_strategy(strategy)
    engine.run()
    results.append(engine.trader.generate_positions_report())
    engine.reset()  # 保留数据
engine.dispose()
```

## 5.4 回调中避免阻塞

- 禁止在 `on_bar`/`on_quote_tick` 中执行网络请求、磁盘 I/O 或重计算
- 使用 `self.queue_for_executor(func)` 卸载 CPU 密集任务
- 实盘使用 **uvloop** 替代默认 asyncio

## 5.5 版本升级优化

v1.219-1.224 关键优化：MessageBus topic 匹配 Rust 优化 **100×**、标识符哈希优化、数据引擎 topic 缓存、BacktestNode Rust 原生流式加载。

## 5.6 HFT/长时间运行内存管理

```python
exec_engine=LiveExecEngineConfig(
    purge_closed_orders_interval_mins=15,
    purge_closed_orders_buffer_mins=60,
    purge_closed_positions_interval_mins=15,
    purge_closed_positions_buffer_mins=60,
    purge_from_database=False,
)
```

---

# 第六部分：常见问题排查

| 问题 | 原因与解决 |
|---|---|
| 启动时对账失败 | 检查 API 权限、网络、`timeout_reconciliation` |
| 仓位不一致 | 增加 `reconciliation_lookback_mins`，或重启前平仓 |
| Jupyter 卡死 | 设置 `log_level="ERROR"` |
| 同进程多 TradingNode | 全局单例限制——用独立进程 |
| Windows Ctrl+C 无响应 | 用 `try/except KeyboardInterrupt` |
| Redis 连接错误 | 确认 Redis 6.2+ 运行，检查 host/port/timeout |
| 事件循环阻塞 | 回调中不做重计算——offload 到 executor |
| In-flight 订单超时误判 | 增加 `inflight_check_threshold_ms` |
| 内存持续增长 | 配置 `purge_closed_orders_*` |
| 回测 L2 数据订单不成交 | venue `book_type` 必须设为 `L2_MBP` |
| 前视偏差 | 检查 `ts_init_delta` 是否正确设置 |
| 精度不匹配错误 | 用 `instrument.make_price()` 对齐数据精度 |
| Bar 时间戳问题 | 确保 `ts_init` 为收盘时间 |
