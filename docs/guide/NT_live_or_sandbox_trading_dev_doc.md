# NautilusTrader 实盘交易完全技术指南

NautilusTrader 是一个 Rust 内核 + Python 控制面的生产级量化交易引擎，其核心价值在于**回测与实盘代码零修改切换**。本文档基于官方文档 `concepts/live/` 及其全部关联页面，系统整理了从架构设计到生产部署的全部实盘交易技术细节。**LiveExecEngineConfig 是实盘中最关键的配置类**，其 30+ 个参数直接决定了对账、订单监控和内存管理的行为。整个系统遵循单线程内核 + 多线程异步 I/O 的 LMAX 式架构，通过 MessageBus 实现组件间解耦通信。

---

## 一、架构总览与核心设计

### 1.1 系统架构设计哲学

NautilusTrader 采用了多种关键架构模式：领域驱动设计（DDD）、事件驱动架构、端口与适配器模式（六边形架构）以及 Crash-only 设计。其架构质量属性按权重排序为：**可靠性 > 性能 > 模块化 > 可测试性 > 可维护性 > 可部署性**。

系统支持三种环境上下文：**Backtest**（历史数据 + 模拟交易所）、**Sandbox**（实时数据 + 模拟交易所）、**Live**（实时数据 + 真实交易所）。三种上下文共享同一个 `NautilusKernel` 核心，用户定义的 Actor 和 Strategy 组件在所有环境中行为一致。

### 1.2 核心组件关系

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

### 1.3 TradingNode 与 LiveNode

| 特性 | TradingNode（Legacy/Python 路径） | LiveNode（v2/Rust 路径） |
|---|---|---|
| 实现语言 | Python asyncio | Rust (tokio) |
| 配置类 | `TradingNodeConfig` | `LiveNodeConfig`（Rust struct） |
| 信号处理 | Unix: `loop.add_signal_handler`；Windows: 需 try/except | 全平台 `tokio::signal::ctrl_c()` |
| 状态 | 稳定可用 | 新一代系统，持续演进 |

**生命周期**：`build()` → `run()` → `stop()` → `dispose()`

> ⚠️ **关键限制**：每个进程只能运行**一个** TradingNode 实例（全局单例状态：`_FORCE_STOP` 标志、Logger 模式、Tokio 运行时、OnceLock 单例）。需要并行运行多个节点时，必须使用独立进程。同一进程中顺序执行多个节点（dispose 后再创建新节点）是支持的。

### 1.4 进程与线程模型

**单线程内核**（类 LMAX Disruptor 模式）：MessageBus 消息消费与派发、Strategy 逻辑与订单管理、RiskEngine 检查、Cache 读写——全部在**单一线程**上顺序执行。这保证了确定性事件排序，维持回测-实盘一致性。

**多线程后台服务**：网络 I/O（WebSocket/REST）、持久化（DataFusion 查询、数据库操作通过多线程 Tokio 运行时）、适配器异步操作。后台服务通过 channel 向单线程内核的 MessageBus 投递事件。

**事件循环**：默认使用 `uvloop`（Linux/macOS），显著优于标准 asyncio。Python 3.12+ 提供了额外的 asyncio 性能改进。

> ⚠️ **绝对不要阻塞事件循环**：Strategy 回调中进行模型推理、重计算或同步 I/O 会导致错过成交、数据延迟和订单延迟。重计算任务必须 offload 到 executor 或独立线程/进程。

---

## 二、配置体系详解

### 2.1 TradingNodeConfig 完整配置

```python
from nautilus_trader.config import (
    TradingNodeConfig, CacheConfig, MessageBusConfig, DatabaseConfig,
    LiveDataEngineConfig, LiveRiskEngineConfig, LiveExecEngineConfig,
    PortfolioConfig, LoggingConfig, StreamingConfig,
)

config = TradingNodeConfig(
    # === 基础标识 ===
    trader_id="MyTrader-001",          # 唯一交易者 ID（name-tag 格式，连字符分隔）
    instance_id=None,                   # 可选 UUID4 实例标识

    # === 超时设置（秒） ===
    timeout_connection=30.0,            # 连接超时
    timeout_reconciliation=10.0,        # 对账超时
    timeout_portfolio=10.0,             # 组合初始化超时
    timeout_disconnection=10.0,         # 断连超时
    timeout_post_stop=5.0,             # 停止后清理超时

    # === 状态管理 ===
    load_state=True,                    # 启动时从数据库加载策略状态
    save_state=True,                    # 停止时保存策略状态到数据库

    # === 组件配置 ===
    cache=CacheConfig(...),
    message_bus=MessageBusConfig(...),
    data_engine=LiveDataEngineConfig(...),
    risk_engine=LiveRiskEngineConfig(...),
    exec_engine=LiveExecEngineConfig(...),
    portfolio=PortfolioConfig(...),
    logging=LoggingConfig(...),
    streaming=StreamingConfig(...),      # 数据流式写入配置（可选）

    # === 客户端配置 ===
    data_clients={
        "BINANCE": BinanceDataClientConfig(...),
    },
    exec_clients={
        "BINANCE": BinanceExecClientConfig(...),
    },

    # === 策略与组件 ===
    strategies=[...],                   # ImportableStrategyConfig 列表
    actors=[...],                       # ImportableActorConfig 列表
    exec_algorithms=[...],              # ImportableExecAlgorithmConfig 列表
    controller=None,                    # ImportableControllerConfig（可选）
)
```

### 2.2 LiveExecEngineConfig — 实盘最核心配置

这是实盘交易中**最重要**的配置类，控制对账、订单监控和内存管理等关键行为。

```python
exec_engine = LiveExecEngineConfig(
    # ====== 启动对账 ======
    reconciliation=True,                      # 启用启动对账
    reconciliation_lookback_mins=None,        # 回溯窗口（None=使用交易所最大历史）
    reconciliation_instrument_ids=None,       # 指定对账的合约 ID 列表
    filtered_client_order_ids=None,           # 从对账中过滤的客户端订单 ID

    # ====== 订单过滤 ======
    filter_unclaimed_external_orders=False,   # 过滤未认领的外部订单
    filter_position_reports=False,            # 过滤仓位状态报告

    # ====== 在途订单监控 ======
    inflight_check_interval_ms=2000,          # 检查间隔（0 = 禁用）
    inflight_check_threshold_ms=5000,         # 触发交易所状态查询的阈值
    inflight_check_retries=5,                 # 最大重试次数

    # ====== 持续对账（Open Order Check） ======
    open_check_interval_secs=None,            # 检查间隔（推荐 5-10 秒，None = 禁用）
    open_check_open_only=True,                # True=仅查询活跃订单；False=全量历史
    open_check_lookback_mins=60,              # ⚠️ 生产环境不要低于 60 分钟
    open_check_threshold_ms=5000,             # 最近事件保护阈值
    open_check_missing_retries=5,             # 缺失订单最大重试
    max_single_order_queries_per_cycle=10,    # 每周期最大单订单查询数
    single_order_query_delay_ms=100,          # 单订单查询间延迟

    # ====== 对账启动延迟 ======
    reconciliation_startup_delay_secs=10.0,   # ⚠️ 生产环境不要低于 10 秒

    # ====== 自有订单簿审计 ======
    own_books_audit_interval_secs=None,       # 自有订单簿审计间隔

    # ====== 仓位检查 ======
    position_check_interval_secs=None,        # 仓位差异检查间隔（推荐 30-60 秒）
    position_check_lookback_mins=60,          # 成交回溯窗口
    position_check_threshold_ms=5000,         # 最小等待时间
    position_check_retries=3,                 # 每个合约最大对账尝试

    # ====== 其他选项 ======
    allow_overfills=False,                    # 允许超量成交（True=警告继续；False=拒绝）
    generate_missing_orders=True,             # 对账时生成缺失订单以对齐仓位
    snapshot_orders=False,                    # 订单事件时拍快照
    snapshot_positions=False,                 # 仓位事件时拍快照
    snapshot_positions_interval_secs=None,    # 周期性仓位快照间隔

    # ====== 内存管理（长时间运行/HFT 必配） ======
    purge_closed_orders_interval_mins=None,   # 推荐 10-15 分钟
    purge_closed_orders_buffer_mins=None,     # 推荐 60 分钟
    purge_closed_positions_interval_mins=None, # 推荐 10-15 分钟
    purge_closed_positions_buffer_mins=None,   # 推荐 60 分钟
    purge_account_events_interval_mins=None,  # 推荐 10-15 分钟
    purge_account_events_lookback_mins=None,  # 推荐 60 分钟
    purge_from_database=False,                # ⚠️ True 会同时删除 Redis/PostgreSQL 中的数据

    # ====== 队列管理 ======
    qsize=100_000,                            # 内部队列缓冲区大小
    graceful_shutdown_on_exception=False,      # 异常时优雅关闭
    debug=False,                              # 额外执行日志
)
```

### 2.3 LiveDataEngineConfig

```python
data_engine = LiveDataEngineConfig(
    time_bars_build_with_no_updates=True,   # 无更新时仍构建时间 Bar
    time_bars_timestamp_on_close=True,      # Bar 时间戳在关闭时
    time_bars_interval_type="left-open",    # Bar 区间类型
    validate_data_sequence=False,           # 验证数据序列（开发调试用）
    debug=False,
    qsize=100_000,                          # 内部队列缓冲区
    external_clients=None,                  # 外部流式客户端 ID 列表
    graceful_shutdown_on_exception=False,
)
```

`external_clients` 用于外部数据流场景：DataEngine 会过滤对这些客户端的订阅命令，数据完全由外部流提供。

### 2.4 LiveRiskEngineConfig

```python
risk_engine = LiveRiskEngineConfig(
    bypass=False,                            # 绕过所有风控检查（仍检查重复 ID）
    max_order_submit_rate="100/00:00:01",    # 每秒最大提交 100 单
    max_order_modify_rate="100/00:00:01",    # 每秒最大修改 100 单
    max_notional_per_order={                 # 每笔订单最大名义价值
        "BTCUSDT.BINANCE": 1_000_000,
    },
    debug=False,
    qsize=100_000,
)
```

### 2.5 CacheConfig 与 DatabaseConfig

```python
cache = CacheConfig(
    database=DatabaseConfig(
        type="redis",              # 数据库类型
        host="localhost",
        port=6379,
        username="nautilus",       # 可选
        password="pass",           # 可选
        ssl=False,                 # SSL 连接
        timeout=2.0,               # 连接超时（秒），默认 20
    ),
    encoding="msgpack",            # 'msgpack'（性能最优）或 'json'（可读）
    timestamps_as_iso8601=True,    # True=ISO8601 字符串；False=UNIX 纳秒
    buffer_interval_ms=100,        # 批量操作缓冲间隔，推荐 [10, 1000] ms
    bulk_read_batch_size=None,     # MGET 批量读取大小
    use_trader_prefix=True,        # Key 中使用 trader 前缀
    use_instance_id=False,         # Key 中包含 instance ID
    flush_on_start=False,          # ⚠️ 启动时清空数据库（生产环境保持 False）
    drop_instruments_on_reset=True,
    tick_capacity=10_000,          # 每个合约内存中最大 Tick 数
    bar_capacity=10_000,           # 每个 Bar 类型内存中最大 Bar 数
    persist_account_events=True,   # 持久化账户事件
)
```

### 2.6 MessageBusConfig

```python
message_bus = MessageBusConfig(
    database=DatabaseConfig(timeout=2),      # Redis 外部流式传输
    encoding="msgpack",
    timestamps_as_iso8601=True,
    buffer_interval_ms=100,
    autotrim_mins=30,                        # 自动修剪流（需 Redis 6.2+）
    use_trader_prefix=True,
    use_trader_id=True,
    use_instance_id=False,
    streams_prefix="streams",                # 流键前缀
    stream_per_topic=True,                   # 每 topic 独立流 vs 单一流
    types_filter=[QuoteTick, TradeTick],     # 不发布到外部的消息类型
    external_streams=None,                   # 订阅的外部流键列表
    heartbeat_interval_secs=1,               # 心跳间隔
)
```

流键格式：`trader:{trader_id}:{instance_id}:{streams_prefix}`

**外部流 Producer/Consumer 模式**可以实现多节点数据共享：Producer 节点发布市场数据到 Redis 流，Consumer 节点从该流消费数据，避免重复连接交易所。

---

## 三、数据层详解

### 3.1 LiveDataClient 与 LiveMarketDataClient

`LiveDataClient` 是所有实盘数据客户端的基类，`LiveMarketDataClient` 继承自它并添加了市场数据特有的订阅方法。

**订阅方法（策略中调用）**：

```python
# 在策略的 on_start() 中
self.subscribe_order_book_deltas(instrument_id)    # L3 逐笔委托
self.subscribe_order_book_depth10(instrument_id)   # Top 10 深度
self.subscribe_quote_ticks(instrument_id)          # 最优买卖
self.subscribe_trade_ticks(instrument_id)          # 逐笔成交
self.subscribe_bars(bar_type)                      # K 线
self.subscribe_mark_price(instrument_id)           # 标记价格
self.subscribe_index_price(instrument_id)          # 指数价格
self.subscribe_funding_rate(instrument_id)         # 资金费率
self.subscribe_instrument(instrument_id)           # 合约定义
self.subscribe_instruments()                       # 所有合约
self.subscribe_instrument_status(instrument_id)    # 合约状态
```

**数据请求方法**：

```python
self.request_bars(BarType.from_str("BTCUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL"))
self.request_quote_ticks(instrument_id, start, end)
self.request_trade_ticks(instrument_id, start, end)
self.request_instrument(instrument_id)
self.request_aggregated_bars(bar_types, start, end, ...)  # 复合 Bar
```

### 3.2 Bar 聚合

Bar 聚合有两种来源：

- **`EXTERNAL`**：直接从交易所/数据提供商订阅（如 `"BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"`）
- **`INTERNAL`**：DataEngine 订阅底层 Tick 数据，内部聚合（如 `"ETHUSDT.BINANCE-250-TICK-LAST-INTERNAL"`）

支持的聚合维度：时间（Time）、Tick 数、成交量（Volume）、价值（Value）、信息量（Information）。价格类型：BID、ASK、MID、LAST。步长 ≥ 1 可自由组合。

```python
# DataEngineConfig 中可配置时间 Bar 构建延迟
DataEngineConfig(time_bars_build_delay=1)  # 1 微秒延迟，确保边界数据到达
```

### 3.3 自定义数据

```python
from nautilus_trader.model.custom import customdataclass
from nautilus_trader.core.data import Data

@customdataclass
class GreeksData(Data):
    delta: float
    gamma: float

# 发布
self.publish_data(GreeksData, data)

# 订阅（在 on_start 中）
self.subscribe_data(GreeksData)

# 接收（回调）
def on_data(self, data: Data):
    if isinstance(data, GreeksData):
        self.log.info(f"Delta: {data.delta}")
```

适配器特有的自定义数据类型（如 `BinanceFuturesMarkPriceUpdate`）也可通过 `DataType` + `metadata` 订阅。

### 3.4 实盘数据流路径

```
交易所 WebSocket/REST
    ↓
LiveDataClient（适配器，如 BinanceSpotDataClient）
    ↓ 标准化为 Nautilus 类型
DataEngine（异步队列处理）
    ↓
Cache（存储）+ MessageBus（发布）
    ↓
Strategy 回调（on_bar, on_trade_tick, on_quote_tick, on_order_book_deltas, on_data）
```

LiveDataEngine 内部有 4 个异步队列：Command 队列、Request 队列、Response 队列、Data 队列。队列接近容量时会记录限流警告并调度异步 put()。

---

## 四、执行层详解

### 4.1 LiveExecutionClient

```python
class LiveExecutionClient(
    loop, client_id, venue,
    oms_type,               # 必须指定 NETTING 或 HEDGING（不能是 UNSPECIFIED）
    account_type,           # CASH 或 MARGIN
    base_currency,          # None 表示多币种账户
    instrument_provider,
    msgbus, cache, clock, ...
)
```

**核心方法（需子类实现）**：

```python
# 订单操作
async _submit_order(command: SubmitOrder)
async _submit_order_list(command: SubmitOrderList)
async _modify_order(command: ModifyOrder)
async _cancel_order(command: CancelOrder)
async _cancel_all_orders(command: CancelAllOrders)
async _batch_cancel_orders(command: BatchCancelOrders)

# 对账报告生成
async generate_order_status_reports(instrument_id, start, end, open_only) → list[OrderStatusReport]
async generate_fill_reports(instrument_id, venue_order_id, start, end) → list[FillReport]
async generate_position_status_reports(instrument_id, start, end) → list[PositionStatusReport]
async generate_order_status_report(instrument_id, client_order_id, venue_order_id) → OrderStatusReport | None
```

### 4.2 订单生命周期

```
INITIALIZED → SUBMITTED → ACCEPTED → [PARTIALLY_FILLED] → FILLED
                ↓              ↓                ↓
            REJECTED     PENDING_UPDATE    PENDING_CANCEL
                         PENDING_CANCEL       ↓
                              ↓            CANCELED
                           CANCELED        EXPIRED
```

**订单分类**：
- **Active local**：INITIALIZED、EMULATED、RELEASED
- **In-flight**（在途）：SUBMITTED、PENDING_UPDATE、PENDING_CANCEL
- **Open**（活跃）：ACCEPTED、TRIGGERED、PENDING_UPDATE、PENDING_CANCEL、PARTIALLY_FILLED
- **Closed**（终态）：DENIED、REJECTED、CANCELED、EXPIRED、FILLED

### 4.3 OMS 类型与仓位管理

| 策略 OMS | 交易所 OMS | 行为 |
|---|---|---|
| `NETTING` | `NETTING` | 每个合约一个仓位，原生行为 |
| `HEDGING` | `HEDGING` | 每个合约可多仓位，原生行为 |
| `NETTING` | `HEDGING` | 交易所跟踪多仓位，Nautilus 维护单一仓位 |
| `HEDGING` | `NETTING` | 交易所跟踪单一仓位，Nautilus 维护"虚拟"多仓位 |

NETTING 模式下仓位翻转（LONG → SHORT）自动处理，引擎会在仓位关闭时拍快照以保留历史盈亏。

### 4.4 超量成交与重复成交检测

- `allow_overfills=False`（默认）：记录错误并拒绝成交
- `allow_overfills=True`：记录警告，应用成交，在 `overfill_qty` 中追踪超量
- 重复成交通过 `trade_id` 去重，`Order.apply()` 对重复 `trade_id` 抛出错误
- 实盘对账清洗器在全量 4 字段匹配之前先按 `trade_id` 预过滤

---

## 五、执行对账机制

### 5.1 启动对账

启动对账是将交易所外部状态（订单/仓位）与系统内部事件状态对齐的过程。**仅 LiveExecutionEngine 具备对账能力**。

**对账流程**：

1. **批量状态获取**：调用适配器的 `generate_order_status_reports`、`generate_fill_reports`、`generate_position_status_reports`
2. **重复检查**：去重订单报告（重复 ClientOrderId 导致对账失败以防止状态腐蚀），去重 TradeId（记录警告）
3. **订单对账**：生成缺失事件以更新订单到当前状态；对缺失成交推断 `OrderFilled` 事件；对未识别的 ClientOrderId 生成外部订单事件；基于容差的价格/佣金一致性验证
4. **仓位对账**：按合约匹配净仓位与交易所报告（使用合约精度）；`generate_missing_orders=True` 时生成 `strategy_id="EXTERNAL"` `tag="RECONCILIATION"` 的补偿订单
5. **部分窗口调整**：检测零穿越（仓位数量穿过 FLAT），生命周期分析，合成缺失的开仓成交

**对账补偿订单价格优先级**：
1. 计算的对账价格（首选）
2. 市场中间价（bid-ask 中点）
3. 当前仓位平均价格
4. MARKET 订单（最后手段，无价格数据时）

> ⚠️ **对账失败时系统不会启动**，错误会被记录。

> ⚠️ **最佳实践**：持久化所有执行事件到 Cache 数据库（Redis），以最小化对交易所历史的依赖。

### 5.2 持续对账与在途订单监控

**在途订单监控状态**：SUBMITTED（等待确认）、PENDING_UPDATE（等待修改确认）、PENDING_CANCEL（等待取消确认）。

**超时解决策略**：

| 当前状态 | 解决为 | 原因 |
|---|---|---|
| SUBMITTED | REJECTED | 交易所未确认 |
| PENDING_UPDATE | CANCELED | 修改未应答 |
| PENDING_CANCEL | CANCELED | 取消未确认 |

**Open Order 一致性检查**：

| Cache 状态 | 交易所状态 | 解决 |
|---|---|---|
| SUBMITTED | 未找到 | REJECTED |
| ACCEPTED | 未找到 | REJECTED |
| ACCEPTED | CANCELED | CANCELED |
| ACCEPTED | EXPIRED | EXPIRED |
| PARTIALLY_FILLED | CANCELED | CANCELED |
| PARTIALLY_FILLED | 未找到 | CANCELED |

> ⚠️ "未找到"解决**仅在全量历史模式**（`open_check_open_only=False`）下有效。`open_check_open_only=True`（默认）时跳过这些检查，因为活跃订单端点不包含已关闭订单。

**重试协调**：`inflight_check_retries` 和 `open_check_missing_retries` 共享重试计数器。在标记订单为终态之前，引擎会先尝试单订单查询探测。`max_single_order_queries_per_cycle`（默认 10）防止速率限制耗尽。

---

## 六、风控引擎

### 6.1 交易前风控检查

每笔订单经过以下内置检查：

1. **价格精度**——是否符合合约定义
2. **价格正数**——除期权外必须为正
3. **数量精度**——是否符合合约定义
4. **最大名义价值**——低于 `max_notional_per_order` 设置
5. **数量限制**——在合约的最大/最小数量范围内
6. **reduce_only 强制执行**——指定 `reduce_only` 时只能减仓
7. **提交速率限制**——`max_order_submit_rate`（默认 100/秒）
8. **修改速率限制**——`max_order_modify_rate`（默认 100/秒）

任何检查失败 → 生成 `OrderDenied` 事件，订单进入终态。

### 6.2 交易状态控制

```python
risk_engine.set_trading_state(TradingState.HALTED)    # 停止所有新订单（取消除外）
risk_engine.set_trading_state(TradingState.REDUCING)   # 仅允许减仓和取消
risk_engine.set_trading_state(TradingState.ACTIVE)     # 恢复正常
```

### 6.3 运行时动态调整

```python
# 动态设置/更新单笔最大名义价值
risk_engine.set_max_notional_per_order(instrument_id, new_value)
# 传入 None 可禁用该合约的名义价值检查
```

---

## 七、订单类型与高级订单

### 7.1 全部订单类型

| 类型 | 描述 | 加密货币交易所支持 |
|---|---|---|
| `MARKET` | 市价单 | ✓ |
| `LIMIT` | 限价单 | ✓ |
| `STOP_MARKET` | 止损市价单（触发后市价执行） | ✓（合约） |
| `STOP_LIMIT` | 止损限价单（触发后限价执行） | ✓ |
| `MARKET_TO_LIMIT` | 先市价后限价（部分成交余量转限价） | 部分支持 |
| `MARKET_IF_TOUCHED` | 触及市价单 | ✓（合约） |
| `LIMIT_IF_TOUCHED` | 触及限价单 | ✓ |
| `TRAILING_STOP_MARKET` | 追踪止损市价 | ✓（合约） |
| `TRAILING_STOP_LIMIT` | 追踪止损限价 | 部分支持 |

### 7.2 执行指令

- **`post_only`**：仅挂单（Maker），不吃单——对做市策略的手续费优化至关重要
- **`reduce_only`**：仅减仓，永不开新仓——仓位变平时自动取消
- **`display_qty`**（冰山单）：指定 Limit 订单的可见数量（0 = 隐藏订单）

### 7.3 Time In Force

`GTC`（撤单前有效）、`IOC`（立即成交或取消）、`FOK`（全部成交或取消）、`GTD`（指定时间前有效）、`DAY`（当日有效）、`AT_THE_OPEN`（开盘有效）、`AT_THE_CLOSE`（收盘有效）

### 7.4 条件单与组合单

**OTO（触发单）**：父单完全成交后自动提交子单。两种触发模式：`FULL`（父单完全成交后）、`PARTIAL`（按比例释放子单）。

**OCO（二择一）**：关联订单，任一成交触发取消其余。

**OUO（联动更新）**：关联订单，任一部分成交按比例减少其余的数量。

**Bracket 订单**：入场单 + 止盈（LIMIT）+ 止损（STOP_MARKET），止盈/止损默认为 `OUO` 关系。

```python
# Bracket 订单示例
bracket = self.order_factory.bracket(
    instrument_id=instrument_id,
    order_side=OrderSide.BUY,
    quantity=Quantity.from_int(1),
    entry_price=Price.from_str("50000"),
    sl_trigger_price=Price.from_str("49000"),
    tp_price=Price.from_str("52000"),
    entry_order_type=OrderType.LIMIT,
    emulation_trigger=TriggerType.NO_TRIGGER,
)
self.submit_order_list(bracket)
```

### 7.5 模拟订单（Emulated Orders）

即使交易所不原生支持某些高级订单类型，Nautilus 也可以在本地模拟它们，通过监控市场数据，在触发条件满足时自动提交 MARKET 或 LIMIT 订单到交易所。

| 订单类型 | 可模拟 | 释放为 |
|---|---|---|
| LIMIT | ✓ | MARKET |
| STOP_MARKET | ✓ | MARKET |
| STOP_LIMIT | ✓ | LIMIT |
| MARKET_IF_TOUCHED | ✓ | MARKET |
| LIMIT_IF_TOUCHED | ✓ | LIMIT |
| TRAILING_STOP_MARKET | ✓ | MARKET |
| TRAILING_STOP_LIMIT | ✓ | LIMIT |

模拟订单在重启后会从 Cache 数据库重新加载。模拟订单会经过 RiskEngine 两次检查：提交时一次，释放时一次。

---

## 八、Cache 缓存系统

### 8.1 缓存内容

- **市场数据**：OrderBook、QuoteTick、TradeTick、Bar
- **交易数据**：完整 Order 历史、Position 状态、Account 信息
- **参考数据**：Instrument 定义、Currency 信息
- **自定义数据**：用户定义对象，用于跨策略共享

### 8.2 策略中的缓存访问

```python
# 市场数据
bars = self.cache.bars(bar_type)
book = self.cache.order_book(instrument_id)
price = self.cache.price(instrument_id, PriceType.MID)

# 订单查询
order = self.cache.order(ClientOrderId("O-123"))
open_orders = self.cache.orders_open(instrument_id=...)
inflight = self.cache.orders_inflight()
emulated = self.cache.orders_emulated()

# 仓位查询
position = self.cache.position(PositionId("P-123"))
open_positions = self.cache.positions_open()

# 合约与账户
instrument = self.cache.instrument(instrument_id)
account = self.cache.account_for_venue(venue)

# 自定义键值存储
self.cache.add(key="my_key", value=b"binary data")
stored = self.cache.get("my_key")  # bytes or None
```

### 8.3 Redis 持久化最佳实践

- `flush_on_start=False`：保留跨重启数据
- `encoding="msgpack"`：最优性能
- `buffer_interval_ms=100`：批量写入，减少 Redis 调用
- 推荐使用 **Redis Insight** GUI 进行可视化调试

### 8.4 内存清理

长时间运行或 HFT 场景必须配置内存清理：

```python
exec_engine=LiveExecEngineConfig(
    purge_closed_orders_interval_mins=15,      # 每 15 分钟清理
    purge_closed_orders_buffer_mins=60,        # 关闭 60 分钟后才清理
    purge_closed_positions_interval_mins=15,
    purge_closed_positions_buffer_mins=60,
    purge_account_events_interval_mins=15,
    purge_account_events_lookback_mins=60,
    purge_from_database=False,                 # ⚠️ True 会删除 Redis 中的数据
)
```

安全保证：Open 的订单/仓位永远不会被清理；关联订单保留到所有子单关闭。

---

## 九、适配器体系

### 9.1 适配器架构

每个适配器由 5 个核心组件构成：

| 组件 | 职责 |
|---|---|
| `HttpClient` | REST API 通信 |
| `WebSocketClient` | 实时流连接 |
| `InstrumentProvider` | 加载并解析合约定义 |
| `DataClient` | 市场数据订阅与请求 |
| `ExecutionClient` | 订单提交、修改、取消 |

适配器通过 Factory 模式注册到 TradingNode：

```python
node = TradingNode(config=config)
node.add_data_client_factory("BINANCE", BinanceLiveDataClientFactory)
node.add_exec_client_factory("BINANCE", BinanceLiveExecClientFactory)
node.build()
```

### 9.2 Binance 适配器（stable）

**支持产品**：Spot（含 Binance US）、USDT 保证金合约（永续+交割）、币本位合约。**不支持**：保证金交易（Cross/Isolated Margin）。

**关键配置**：

```python
BinanceDataClientConfig(
    api_key=None,                       # 或从 BINANCE_API_KEY 环境变量加载
    api_secret=None,                    # 或从 BINANCE_API_SECRET 环境变量加载
    account_type="USDT_FUTURES",        # SPOT / USDT_FUTURES / COIN_FUTURES
    environment="LIVE",                 # LIVE / TESTNET / DEMO
    us=False,                           # Binance US 端点
    update_instruments_interval_mins=60,
    use_agg_trade_ticks=False,          # 使用聚合成交
)

BinanceExecClientConfig(
    account_type="USDT_FUTURES",
    use_gtd=True,                       # False 时 GTD 映射为 GTC
    use_reduce_only=True,               # 传递 reduce_only 到 Binance
    use_position_ids=True,              # 启用 Binance 对冲仓位 ID
    treat_expired_as_canceled=False,    # 将 EXPIRED 视为 CANCELED
    recv_window_ms=5000,                # 签名请求接收窗口
    futures_leverages={"BTCUSDT": 10},  # 合约杠杆映射
    futures_margin_types={"BTCUSDT": "CROSSED"},  # 保证金类型
)
```

**符号格式**：Spot `BTCUSDT.BINANCE`，永续 `BTCUSDT-PERP.BINANCE`。推荐 Ed25519 密钥类型。

### 9.3 Bybit 适配器（stable）

**支持产品**：Spot（含保证金）、Linear 永续/交割、Inverse 永续/交割、期权（USDT 结算欧式）。

**关键配置**：

```python
BybitDataClientConfig(
    api_key=None,                       # BYBIT_API_KEY
    api_secret=None,                    # BYBIT_API_SECRET
    product_types=[BybitProductType.LINEAR],  # SPOT / LINEAR / INVERSE / OPTION
    testnet=False,
    demo=False,
)

BybitExecClientConfig(
    product_types=[BybitProductType.LINEAR],
    use_gtd=False,                      # Bybit 不原生支持 GTD，映射为 GTC
    use_ws_execution_fast=False,        # 低延迟执行流
    auto_repay_spot_borrows=True,       # BUY 成交后自动归还现货保证金借款
    futures_leverages={"BTCUSDT": 10},
    margin_mode=None,                   # 账户保证金模式
)
```

**符号格式**：Spot `ETHUSDT-SPOT.BYBIT`，Linear `BTCUSDT-LINEAR.BYBIT`，Inverse `BTCUSD-INVERSE.BYBIT`。

> ⚠️ **Bybit 每日 UTC 04:00-05:00 维护窗口**，此期间无法执行保证金还款。

### 9.4 Interactive Brokers 适配器

支持股票、ETF、期权、期货、外汇、加密货币、债券等全资产类别。安装：`uv pip install "nautilus_trader[ib,docker]"`。

```python
InteractiveBrokersDataClientConfig(
    ibg_host="127.0.0.1",
    ibg_port=7497,        # TWS 模拟: 7497, 实盘: 7496; IB Gateway 模拟: 4002, 实盘: 4001
    ibg_client_id=1,
)
```

支持 Dockerized IB Gateway：`DockerizedIBGatewayConfig(username="...", password="...", trading_mode="paper")`。

### 9.5 全部集成列表

| 名称 | ID | 类型 | 状态 |
|---|---|---|---|
| Binance | `BINANCE` | CEX | stable |
| Bybit | `BYBIT` | CEX | stable |
| Interactive Brokers | `INTERACTIVE_BROKERS` | 券商 | — |
| OKX | `OKX` | CEX | — |
| dYdX | `DYDX` | DEX | beta |
| BitMEX | `BITMEX` | CEX | — |
| Deribit | `DERIBIT` | CEX | — |
| Hyperliquid | `HYPERLIQUID` | DEX | — |
| Kraken | `KRAKEN` | CEX | — |
| Polymarket | `POLYMARKET` | 预测市场 | — |
| Databento | `DATABENTO` | 数据提供商 | — |
| Tardis | `TARDIS` | 加密数据提供商 | — |
| AX Exchange | `AX` | 永续交易所 | — |
| Betfair | `BETFAIR` | 体育博彩 | — |
| Architect | `ARCHITECT` | 券商 | — |

### 9.6 自定义适配器开发

7 阶段开发流程：

1. **Rust 核心基础设施**：HTTP 客户端、WebSocket 客户端、解析逻辑、PyO3 绑定
2. **合约定义**：解析、InstrumentProvider、符号映射
3. **市场数据**：公共 WS 流、历史请求、Python DataClient
4. **订单执行**：私有 WS、订单提交、对账
5. **高级功能**：条件单、批量操作、交易所特有数据
6. **配置与工厂类**
7. **测试与文档**

Python 层需要继承的基类和实现的方法：

```python
# InstrumentProvider: load_all_async(), load_ids_async(), load_async()
# LiveMarketDataClient: _connect(), _disconnect(), _subscribe_*(), _request_*()
# LiveExecutionClient: _connect(), _disconnect(), _submit_order(), _modify_order(),
#                       _cancel_order(), generate_order_status_reports(),
#                       generate_fill_reports(), generate_position_status_reports()
```

**环境变量约定**：`{VENUE}_API_KEY` / `{VENUE}_API_SECRET`（主网）、`{VENUE}_TESTNET_API_KEY`（测试网）、`{VENUE}_DEMO_API_KEY`（模拟环境）。

---

## 十、日志系统

### 10.1 架构

日志子系统在 Rust 中实现，使用 `log` crate 门面。核心 Logger 在**独立线程**上通过 MPSC channel 运行，主线程永远不会被日志格式化或文件 I/O 阻塞。

### 10.2 LoggingConfig 完整配置

```python
LoggingConfig(
    log_level="INFO",                  # 标准输出最低级别
    log_level_file="DEBUG",            # 文件输出最低级别
    log_file_format="json",            # None=纯文本 .log / "json"=JSON .json
    log_file_max_size=100_000_000,     # 文件大小轮转（字节）
    log_file_max_backup_count=5,       # 最大备份文件数
    log_file_name=None,                # 自定义文件名
    log_directory=None,                # 自定义日志目录
    log_component_levels={             # 组件级别控制
        "Portfolio": "INFO",
        "RiskEngine": "DEBUG",
    },
    log_components_only=False,         # 仅记录指定组件
    log_colors=True,                   # ANSI 颜色（云环境建议 False）
    bypass_logging=False,              # 完全绕过日志
    use_pyo3=False,                    # 通过 PyO3 桥接初始化日志
    use_tracing=False,                 # 启用 tracing 订阅者
    clear_log_file=False,              # 启动时清空日志文件
)
```

### 10.3 环境变量配置

```bash
export NAUTILUS_LOG="stdout=Info;fileout=Debug;RiskEngine=Error;is_colored"
```

Rust 模块级别过滤（最长前缀匹配优先）：

```bash
export NAUTILUS_LOG="stdout=Info;nautilus_okx=Warn;nautilus_okx::websocket=Debug"
```

外部 Rust 库日志通过 `RUST_LOG` 控制：`RUST_LOG=hyper=warn python my_script.py`

> ⚠️ **Jupyter 中必须设置 `log_level="ERROR"`**，Nautilus 日志输出速率超过 Jupyter stdout 限制会导致卡死。

---

## 十一、部署与生产最佳实践

### 11.1 系统要求

| 项目 | 要求 |
|---|---|
| Linux | Ubuntu 22.04+, glibc 2.35+ |
| macOS | 15.0+, ARM64 |
| Windows | Server 2022+, x86_64 |
| Python | 3.12–3.14 |
| Redis | 6.2+（可选，用于持久化） |

### 11.2 Docker 部署

```bash
# 拉取 NautilusTrader 镜像
docker pull ghcr.io/nautechsystems/nautilus_trader:nightly

# Redis 容器
docker run -d --name redis -p 6379:6379 redis:latest

# PostgreSQL（如需）
docker run -d --name postgres -p 5432:5432 -e POSTGRES_PASSWORD=pass postgres:latest
```

仓库提供 `.docker/docker-compose.yml` 用于开发环境，包含 PostgreSQL、Redis 和 PgAdmin 服务。

> ⚠️ docker-compose 仅用于开发，生产环境需要更安全的配置。

### 11.3 生产环境推荐配置

```python
config = TradingNodeConfig(
    trader_id="Prod-001",
    logging=LoggingConfig(
        log_level="INFO",
        log_level_file="DEBUG",
        log_file_format="json",
        log_file_max_size=100_000_000,
    ),
    cache=CacheConfig(
        database=DatabaseConfig(host="localhost", port=6379, timeout=2),
        encoding="msgpack",
        buffer_interval_ms=100,
    ),
    exec_engine=LiveExecEngineConfig(
        reconciliation=True,
        inflight_check_interval_ms=2000,
        open_check_interval_secs=10,
        open_check_lookback_mins=60,
        reconciliation_startup_delay_secs=10.0,
        snapshot_positions=True,
        snapshot_positions_interval_secs=60,
        purge_closed_orders_interval_mins=15,
        purge_closed_orders_buffer_mins=60,
        purge_closed_positions_interval_mins=15,
        purge_closed_positions_buffer_mins=60,
    ),
    risk_engine=LiveRiskEngineConfig(
        max_order_submit_rate="100/00:00:01",
        max_order_modify_rate="100/00:00:01",
    ),
)
```

### 11.4 完整启动模板

```python
import os
from nautilus_trader.config import *
from nautilus_trader.live.node import TradingNode
from nautilus_trader.adapters.binance.config import *
from nautilus_trader.adapters.binance.factories import *

config = TradingNodeConfig(
    trader_id="MyTrader-001",
    logging=LoggingConfig(log_level="INFO", log_level_file="DEBUG", log_file_format="json"),
    cache=CacheConfig(database=DatabaseConfig(host="localhost", port=6379, timeout=2)),
    exec_engine=LiveExecEngineConfig(reconciliation=True, open_check_interval_secs=10),
    data_clients={
        "BINANCE": BinanceDataClientConfig(
            account_type=BinanceAccountType.USDT_FUTURES,
            environment="TESTNET",
        ),
    },
    exec_clients={
        "BINANCE": BinanceExecClientConfig(
            account_type=BinanceAccountType.USDT_FUTURES,
            environment="TESTNET",
        ),
    },
    strategies=[...],
)

node = TradingNode(config=config)
node.add_data_client_factory("BINANCE", BinanceLiveDataClientFactory)
node.add_exec_client_factory("BINANCE", BinanceLiveExecClientFactory)
node.build()

# 跨平台安全的启动模式
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

### 11.5 Windows 信号处理

Windows 上 asyncio 不支持 `loop.add_signal_handler`，因此**必须**使用上述 `try/except KeyboardInterrupt` 模式。LiveNode（v2）已在所有平台支持 Ctrl+C 优雅关闭。

---

## 十二、外部消息流与多节点架构

### 12.1 Producer/Consumer 模式

**Producer 节点**（发布 Binance 数据到 Redis 流）：

```python
message_bus=MessageBusConfig(
    database=DatabaseConfig(timeout=2),
    use_trader_id=False,
    use_trader_prefix=False,
    use_instance_id=False,
    streams_prefix="binance",
    stream_per_topic=False,
    autotrim_mins=30,
)
```

**Consumer 节点**（从 Redis 流消费数据）：

```python
data_engine=LiveDataEngineConfig(
    external_clients=[ClientId("BINANCE_EXT")],  # 跳过订阅命令
),
message_bus=MessageBusConfig(
    database=DatabaseConfig(timeout=2),
    external_streams=["binance"],  # 订阅外部流
),
```

这种模式可以实现：一个节点连接交易所获取数据，多个策略节点消费同一份数据流，避免重复的 WebSocket 连接和 API 速率限制。

---

## 十三、常见问题排查

### 13.1 高频问题速查表

| 问题 | 原因与解决 |
|---|---|
| 启动时对账失败 | 检查 API 权限、网络连通性、`timeout_reconciliation` 是否过短 |
| 仓位与交易所不一致 | 增加 `reconciliation_lookback_mins`，或重启前平仓 |
| 缺失成交报告 | 部分交易所过滤旧成交——增加 lookback 或本地持久化所有事件 |
| 重复 ClientOrderId | 自动去重但记录警告；频繁出现说明交易所数据完整性问题 |
| Jupyter 卡死 | 设置 `log_level="ERROR"`，或改用独立 Python 脚本 |
| 同进程多 TradingNode 失败 | 全局单例限制——使用独立进程 |
| Windows Ctrl+C 无响应 | 使用 `try/except KeyboardInterrupt` 包装 |
| Redis 连接错误 | 确认 Redis 6.2+ 运行中，检查 host/port/timeout |
| 事件循环阻塞 | 策略回调中不要执行重计算——offload 到 executor |
| `glibc` 版本错误 | 确认 glibc ≥ 2.35（`ldd --version`） |
| In-flight 订单超时误判 | 增加 `inflight_check_threshold_ms`，避免与交易所延迟竞争 |
| 内存持续增长 | 配置 `purge_closed_orders_*` 和 `purge_closed_positions_*` |

### 13.2 调试模式

```python
TradingNodeConfig(
    logging=LoggingConfig(log_level="DEBUG"),
    exec_engine=LiveExecEngineConfig(debug=True),
    data_engine=LiveDataEngineConfig(debug=True),
)
```

精确到组件的日志控制：

```python
LoggingConfig(
    log_level="INFO",
    log_component_levels={
        "RiskEngine": "DEBUG",
        "LiveExecutionEngine": "DEBUG",
    },
    log_components_only=True,  # 仅输出指定组件日志
)
```

### 13.3 数据完整性与 Crash-only 设计

系统对算术溢出、NaN/Infinity、无效数据、格式错误输入采用**快速失败策略**（panic = abort）。生产环境中 panic 确保进程干净终止，不会产生静默数据腐蚀。启动和崩溃恢复共享同一代码路径——状态外部化持久化，操作幂等以支持安全重试。

---

## 结论与关键洞察

NautilusTrader 的实盘交易系统体现了工业级交易引擎的设计水准。**对账系统是其最复杂也是最关键的子系统**——启动对账、持续对账、在途订单监控三层防护确保系统状态与交易所一致。`LiveExecEngineConfig` 的 30+ 个参数需要根据交易频率、交易所特性和运维需求精细调优。

对于加密货币量化团队，最值得关注的工程决策包括：优先使用 Redis 持久化全部执行事件以降低对账复杂度；为 HFT 场景配置内存清理循环防止 OOM；利用外部消息流实现数据共享以规避 API 速率限制；以及始终在独立 Python 进程（非 Jupyter）中运行实盘节点。v2 的 LiveNode（Rust 原生）正在逐步成熟，将带来更好的跨平台信号处理和性能表现。