# NautilusTrader 回测体系补充技术文档

NautilusTrader 的回测系统远不止 `BacktestEngine` 和 `BacktestNode` 两个入口类。**完整掌握回测需要理解十个核心子系统的协作机制**：数据目录 `ParquetDataCatalog` 管理历史行情持久化，DataWrangler 族负责 CSV/DataFrame 到 Nautilus 对象的转换，配置体系（四个 Config 类）驱动高级 API 声明式回测，`SimulatedExchange` 内部的撮合引擎精确模拟 L1/L2/L3 级别的订单匹配，`FillModel`、`LatencyModel`、`FeeModel` 三大模型控制执行仿真的逼真度，`Strategy` 生命周期回调和 Clock/Timer 系统保证了回测与实盘代码的完全一致性，最后 `ReportProvider` 和 Tearsheet 可视化系统提供回测结果的分析闭环。本文档逐一深入每个子系统的工程细节。

---

## 一、ParquetDataCatalog：列式数据目录

`ParquetDataCatalog` 是高级回测 API 的数据基座，提供基于 Parquet（Apache Arrow 列式格式，ZSTD 压缩）的可查询数据目录，**非线程安全**。

### 1.1 初始化方式

```python
from nautilus_trader.persistence.catalog import ParquetDataCatalog
```

构造函数参数：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `path` | `PathLike[str] \| str` | 必填 | 目录根路径，必须为绝对路径且已存在 |
| `fs_protocol` | `str` | `'file'` | fsspec 文件系统协议：`file`、`s3`、`gcs`、`abfs`、`az` |
| `fs_storage_options` | `dict[str, str] \| None` | `None` | 存储凭证（S3: endpoint_url/region/access_key_id/secret_access_key；GCS: service_account_path/project_id；Azure: account_name/account_key/sas_token） |
| `fs_rust_storage_options` | `dict[str, str] \| None` | `None` | Rust 后端存储选项，默认回退到 `fs_storage_options` |

**多种初始化方式：**

```python
# 本地路径
catalog = ParquetDataCatalog(Path.cwd() / "catalog")

# 环境变量 NAUTILUS_PATH（自动追加 /catalog）
catalog = ParquetDataCatalog.from_env()

# URI 自动解析协议
catalog = ParquetDataCatalog.from_uri("s3://my-bucket/nautilus-data/",
    storage_options={"access_key_id": "xxx", "secret_access_key": "yyy"})

# GCS
catalog = ParquetDataCatalog(path="gcs://bucket/data/", fs_protocol="gcs",
    fs_storage_options={"project": "my-project", "token": "/path/to/sa.json"})
```

### 1.2 目录结构与双后端架构

文件按 **数据类型 → instrument_id → 时间范围** 组织：

```
catalog/
├── data/
│   ├── quote_ticks/
│   │   └── eurusd.sim/
│   │       └── 20240101T000000000000000_20240101T235959999999999.parquet
│   ├── trade_ticks/
│   │   └── btcusd.binance/
│   └── bar/
│       └── aapl.nasdaq/
```

**Rust 后端**（高性能，自动文件发现）支持类型：`OrderBookDelta`、`OrderBookDeltas`、`OrderBookDepth10`、`QuoteTick`、`TradeTick`、`Bar`、`MarkPriceUpdate`。**PyArrow 后端**（灵活，支持自定义数据类型）在指定 `files` 参数或使用自定义数据类时启用。

### 1.3 写入 API

```python
catalog.write_data(
    data: list[Data],                    # Nautilus 数据对象列表
    start: int | str | float = None,     # 可选起始时间覆写（UNIX 纳秒）
    end: int | str | float = None,       # 可选结束时间覆写
    skip_disjoint_check: bool = False,   # 跳过时间重叠检查
    basename_template: str = 'part-{i}', # 文件命名模板
)
```

**关键规则**：同一数据类型必须按 `ts_init` 单调递增；**写入市场数据前必须先写入 Instrument 定义**；同名文件会被覆盖；默认拒绝与已有文件时间重叠的数据。

### 1.4 查询 API

```python
# 通用查询
data = catalog.query(
    data_cls=QuoteTick,
    identifiers=["EUR/USD.SIM"],
    start="2024-01-01T00:00:00Z",
    end="2024-01-02T00:00:00Z",
    where="price > 1.10",       # PyArrow 过滤表达式
)

# 快捷方法
quotes = catalog.quote_ticks(instrument_ids=["EUR/USD.SIM"], start=start_ns, end=end_ns)
trades = catalog.trade_ticks(instrument_ids=["BTC/USD.BINANCE"])
deltas = catalog.order_book_deltas(start=start_ns, end=end_ns)
instruments = catalog.instruments()
```

时间参数支持 ISO 8601 字符串、UNIX 纳秒整数、`pd.Timestamp`、Python datetime。

### 1.5 目录维护操作

| 方法 | 用途 |
|------|------|
| `consolidate_catalog(start, end)` | 合并整个目录的 Parquet 文件 |
| `consolidate_data(data_cls, identifier)` | 合并特定类型/品种的文件 |
| `consolidate_catalog_by_period(period)` | 按固定时间周期拆分合并 |
| `delete_catalog_range(start, end)` | 按时间范围删除数据 |
| `reset_all_file_names()` | 重置文件名与实际内容时间戳对齐 |
| `convert_stream_to_data(instance_id, data_cls)` | Feather 流文件转 Parquet |

### 1.6 支持的数据类型完整列表

核心行情类型：`OrderBookDelta`（L1/L2/L3）、`OrderBookDeltas`、`OrderBookDepth10`、`QuoteTick`、`TradeTick`、`Bar`。衍生品扩展：`MarkPriceUpdate`、`IndexPriceUpdate`、`FundingRateUpdate`。状态类型：`InstrumentStatus`、`InstrumentClose`。所有 `Instrument` 子类型（`Equity`、`CurrencyPair`、`CryptoPerpetual`、`FuturesContract`、`OptionContract` 等）。**自定义 `Data` 子类**需实现 `ts_event`/`ts_init` 属性以及 Arrow 序列化接口，或使用 `@customdataclass` 装饰器自动生成。

---

## 二、DataWrangler 族：数据转换管道

Wrangler 将 `pd.DataFrame` 转换为 Nautilus 对象列表，是 CSV/外部数据源到回测引擎的标准桥梁。

```python
from nautilus_trader.persistence.wranglers import (
    QuoteTickDataWrangler, TradeTickDataWrangler,
    BarDataWrangler, OrderBookDeltaDataWrangler, OrderBookDepth10DataWrangler,
)
```

### 2.1 QuoteTickDataWrangler

```python
wrangler = QuoteTickDataWrangler(instrument=instrument)

# 从 tick 级 DataFrame（需要 bid_price/ask_price 列，timestamp 索引）
ticks = wrangler.process(df, default_volume=1_000_000.0, ts_init_delta=0)

# 从 bid/ask bar DataFrame 合成
ticks = wrangler.process_bar_data(bid_data=bid_df, ask_data=ask_df,
    default_volume=1_000_000.0, ts_init_delta=0, random_seed=42)
```

### 2.2 TradeTickDataWrangler

```python
wrangler = TradeTickDataWrangler(instrument=instrument)
ticks = wrangler.process(df, ts_init_delta=0, is_raw=False)

# 从 OHLCV bar 合成 trade ticks
ticks = wrangler.process_bar_data(df, ts_init_delta=0,
    offset_interval_ms=100, timestamp_is_close=True, random_seed=42)
```

### 2.3 BarDataWrangler

```python
bar_type = BarType.from_str("AAPL.SIM-1-DAY-LAST-EXTERNAL")
wrangler = BarDataWrangler(bar_type=bar_type, instrument=instrument)
bars = wrangler.process(df, default_volume=1_000_000.0, ts_init_delta=0)
```

期望的 DataFrame 列：`open`、`high`、`low`、`close`、`volume`（可选），以 `timestamp` 为索引。

### 2.4 ts_init_delta 的正确使用

**`ts_init_delta`** 是所有 Wrangler 的关键参数，定义为 `ts_init = ts_event + ts_init_delta`（纳秒），用于模拟数据源到系统的网络延迟。

| Bar 时间戳语义 | ts_init_delta 设置 | 理由 |
|----------------|-------------------|------|
| 时间戳为 bar **收盘时间** | `0` | ts_init 等于 ts_event，直接代表 bar 完成时刻 |
| 时间戳为 bar **开盘时间** | bar 周期纳秒数（如 1 分钟 = `60_000_000_000`） | 将 ts_init 推迟到 bar 实际完成时刻 |

**错误设置会导致前视偏差（look-ahead bias）**——回测中策略会在 bar 尚未完成时就"看到"它。`ts_init_delta` 不可为负值。

### 2.5 完整数据加载管线

```python
# 1. 原始数据 → DataFrame
from nautilus_trader.test_kit.providers import CSVTickDataLoader
df = CSVTickDataLoader.load("data/eurusd_ticks.csv", index_col=0, format="%Y%m%d %H%M%S%f")

# 2. DataFrame → Nautilus 对象
wrangler = QuoteTickDataWrangler(instrument=EURUSD)
ticks = wrangler.process(df)

# 3a. 写入目录（高级 API 路线）
catalog.write_data([EURUSD])  # 先写 Instrument
catalog.write_data(ticks)

# 3b. 直接添加到引擎（低级 API 路线）
engine.add_instrument(EURUSD)
engine.add_data(ticks)
```

集成加载器包括 `BinanceOrderBookDeltaDataLoader`、`BybitOrderBookDeltaDataLoader`、`DatabentoDataLoader`、Tardis CSV 加载器等。

---

## 三、回测配置体系详解

高级 API 通过四个声明式 Config 类驱动回测，可序列化为 JSON 用于分布式参数扫描。

### 3.1 BacktestRunConfig

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `engine` | `BacktestEngineConfig` | `BacktestEngineConfig()` | 引擎配置 |
| `venues` | `list[BacktestVenueConfig]` | 必填 | 交易所配置列表 |
| `data` | `list[BacktestDataConfig]` | 必填 | 数据配置列表 |
| `chunk_size` | `int \| None` | `None` | 流式处理每批数据量，`None` 一次加载全部 |
| `start` | `datetime \| str \| int` | `None` | 回测起始时间（UTC） |
| `end` | `datetime \| str \| int` | `None` | 回测结束时间（UTC） |
| `raise_exception` | `bool` | `False` | 是否向上抛出引擎构建/运行异常 |
| `dispose_on_completion` | `bool` | `True` | 完成后是否释放引擎（`True` 释放数据和状态，`False` 仅释放数据） |

### 3.2 BacktestDataConfig

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `catalog_path` | `str` | 必填 | Parquet 目录路径 |
| `data_cls` | `type \| str` | 必填 | 数据类（`QuoteTick`/`TradeTick`/`Bar`/`OrderBookDelta` 等） |
| `catalog_fs_rust_storage_options` | `dict` | `None` | Rust 后端存储选项 |
| `instrument_id` | `str` | `None` | 单品种 ID |
| `instrument_ids` | `list[str]` | `None` | 批量品种 ID（提高查询效率） |
| `start_time` / `end_time` | `str \| int` | `None` | 数据时间范围（ISO 8601 或 UNIX 纳秒） |
| `filter_expr` | `str` | `None` | PyArrow 过滤表达式 |
| `bar_spec` | `str` | `None` | Bar 规格（如 `"1-MINUTE-LAST"`） |
| `bar_types` | `list[str]` | `None` | 批量 Bar 类型 |
| `metadata` | `dict` | `None` | 目录查询元数据 |
| `optimize_file_loading` | `bool` | `None` | 文件加载优化 |

### 3.3 BacktestVenueConfig

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `name` | `str` | 必填 | 交易所名称（如 `"BINANCE"`） |
| `oms_type` | `str` | 必填 | `"HEDGING"`（生成新 position ID）或 `"NETTING"` |
| `account_type` | `str` | 必填 | `"CASH"`/`"MARGIN"`/`"BETTING"` |
| `starting_balances` | `list[str]` | 必填 | 起始余额（如 `["100_000 USDT"]`） |
| `base_currency` | `str` | `None` | 基础货币，多币种账户设为 `None` |
| `default_leverage` | `float` | `None` | 默认杠杆倍数 |
| `leverages` | `dict[str, float]` | `None` | 按品种设置杠杆 |
| `book_type` | `str` | `"L1_MBP"` | 订单簿类型：`L1_MBP`/`L2_MBP`/`L3_MBO` |
| `fill_model` | `ImportableFillModelConfig` | `None` | 成交模型 |
| `latency_model` | `ImportableLatencyModelConfig` | `None` | 延迟模型 |
| `fee_model` | `ImportableFeeModelConfig` | `None` | 手续费模型 |
| `margin_model` | `MarginModelConfig` | `None` | 保证金模型（`"leveraged"` 默认/`"standard"`） |
| `bar_execution` | `bool` | `True` | Bar 是否触发订单撮合 |
| `trade_execution` | `bool` | `True` | Trade tick 是否触发撮合 |
| `bar_adaptive_high_low_ordering` | `bool` | `False` | 自适应 OHLC 价格顺序（准确率约 75-85%） |
| `liquidity_consumption` | `bool` | `False` | 逐档流动性消耗追踪 |
| `queue_position` | `bool` | `False` | 限价单队列位置追踪 |
| `price_protection_points` | `int` | `0` | 价格保护边界（tick 数，0 禁用） |
| `use_market_order_acks` | `bool` | `False` | 市价单生成 OrderAccepted 事件 |
| `oto_trigger_mode` | `str` | `"PARTIAL"` | OTO 子单触发方式（`"PARTIAL"` 部分成交/`"FULL"` 全部成交） |

### 3.4 BacktestEngineConfig

继承 `NautilusKernelConfig`，核心参数：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `trader_id` | `str` | `"BACKTESTER-001"` | 交易者 ID |
| `log_level` | `str` | `"INFO"` | 日志级别 |
| `logging` | `LoggingConfig` | `None` | 日志配置 |
| `cache` | `CacheConfig` | `None` | 缓存配置 |
| `data_engine` | `DataEngineConfig` | `None` | 数据引擎（含 `time_bars_build_delay`、`validate_data_sequence`、`buffer_deltas` 等子参数） |
| `risk_engine` | `RiskEngineConfig` | `None` | 风控引擎配置 |
| `exec_engine` | `ExecEngineConfig` | `None` | 执行引擎配置 |
| `streaming` | `StreamingConfig` | `None` | Feather 流式输出配置 |
| `strategies` | `list[ImportableStrategyConfig]` | `[]` | 策略配置（高级 API 用） |
| `actors` | `list[ImportableActorConfig]` | `[]` | Actor 配置 |
| `exec_algorithms` | `list[ImportableExecAlgorithmConfig]` | `[]` | 执行算法配置 |

`ImportableStrategyConfig` 使用模块路径实现策略的动态导入和序列化：

```python
ImportableStrategyConfig(
    strategy_path="nautilus_trader.examples.strategies.ema_cross:EMACross",
    config_path="nautilus_trader.examples.strategies.ema_cross:EMACrossConfig",
    config={"instrument_id": "ETH/USDT.BINANCE", "fast_ema_period": 10, "slow_ema_period": 20},
)
```

---

## 四、SimulatedExchange 撮合引擎内部机制

### 4.1 三阶段主循环

回测引擎对每个数据点执行严格的**三阶段处理**：

1. **交易所处理数据**：`SimulatedExchange` 更新内部订单簿，撮合引擎迭代，匹配现有挂单
2. **策略接收数据**：DataEngine 通过回调（`on_bar`、`on_quote_tick` 等）分发数据到策略，策略可提交/取消/修改订单
3. **结算循环**：引擎排空所有交易所命令队列并再次迭代撮合引擎处理新提交的订单。**此循环重复直到无待处理命令**——级联订单（如 `on_order_filled` 中提交止损单）在同一时间戳内完成结算

配置了 `LatencyModel` 时，命令进入 inflight 队列并附带未来时间戳，到达该时间点才被处理。

### 4.2 不同数据级别的撮合逻辑

| book_type | 更新订单簿的数据类型 | 忽略的数据类型 |
|-----------|---------------------|---------------|
| `L1_MBP` | QuoteTick、Bar、OrderBookDepth10 | — |
| `L2_MBP` | OrderBookDelta/s、OrderBookDepth10 | QuoteTick、Bar |
| `L3_MBO` | OrderBookDelta/s、OrderBookDepth10 | QuoteTick、Bar |

**TradeTick 在所有级别均触发撮合**，使用"瞬态覆盖"机制临时调整 Best Bid/Ask，但不修改底层订单簿数据。

### 4.3 Bar 数据撮合细节

OHLC 被处理为 4 个价格更新：**Open → High → Low → Close**（固定顺序），成交量均分（25%×4，余量加到 Close）。启用 `bar_adaptive_high_low_ordering` 后，根据 Open 与 High/Low 的距离动态推断价格路径（Open 更接近 High 则 O→H→L→C，反之 O→L→H→C），准确率约 **75-85%**。

Stop 单在 bar 数据下的两种行为：
- **跳空场景**（bar 开盘价越过触发价）：以市价（开盘价）成交，模拟真实跳空滑点
- **穿越场景**（bar 在 H/L 处理中穿过触发价）：以触发价成交，限制有序行情中的滑点

### 4.4 队列位置追踪

启用 `queue_position=True` 后，限价单放置时快照当前同侧订单簿在该价位的深度。对手方 TradeTick 递减"前方数量"，降为零时才可成交。修改订单（价格或数量变动）重置队列位置。支持 L1/L2/L3 所有级别。

### 4.5 流动性消耗追踪

启用 `liquidity_consumption=True` 后，追踪每个价位已消耗的流动性：`可用 = 原始深度 - 已消耗`。新数据到达该价位时重置。防止同一显示流动性被重复成交。自定义 FillModel 提供模拟订单簿时，此追踪不生效。

---

## 五、FillModel、LatencyModel、FeeModel 三大仿真模型

### 5.1 FillModel 成交模型

```python
from nautilus_trader.backtest.models import FillModel

fill_model = FillModel(
    prob_fill_on_limit=0.2,  # 限价单触碰时的成交概率（模拟队列位置，0.0=队尾，1.0=队首）
    prob_slippage=0.5,       # 成交价滑点一个 tick 的概率（仅 L1 数据有效）
    random_seed=42,          # 固定随机种子保证可复现
)
```

`prob_fill_on_limit` 适用于所有订单簿级别；`prob_slippage` **仅 L1 数据有效**（L2/L3 由真实订单簿深度处理滑点）。`prob_fill_on_stop` 已在近期版本中移除（止损触发是确定性的）。

**内置 FillModel 子类**用于通过重写 `get_orderbook_for_fill_simulation()` 返回合成订单簿，模拟不同市场微观结构：

| 子类 | 行为 | 适用场景 |
|------|------|---------|
| `BestPriceFillModel` | 最优价无限流动性 | 乐观测试策略逻辑 |
| `OneTickSlippageFillModel` | 所有订单强制一个 tick 滑点 | 保守滑点测试 |
| `TwoTierFillModel` | 最优价 10 手，剩余差一个 tick | 基础深度模拟 |
| `ThreeTierFillModel` | 三档 50/30/20 手 | 更真实的深度模拟 |
| `SizeAwareFillModel` | 按订单大小区分执行质量 | 大单冲击模拟 |
| `LimitOrderPartialFillModel` | 每次触碰最多成交 5 手 | 队列位置部分成交 |
| `VolumeSensitiveFillModel` | 流动性基于近期成交量 | 量价联动执行 |
| `CompetitionAwareFillModel` | 仅显示流动性的一定比例可用 | 多参与者竞争模拟 |

**高级 API 配置方式**（通过 `ImportableFillModelConfig`）：

```python
from nautilus_trader.backtest.config import ImportableFillModelConfig

fill_model_config = ImportableFillModelConfig(
    fill_model_path="nautilus_trader.backtest.models:FillModel",
    config_path="nautilus_trader.backtest.config:FillModelConfig",
    config={"prob_fill_on_limit": 0.2, "prob_slippage": 0.5, "random_seed": 42},
)
```

### 5.2 LatencyModel 延迟模型

模拟订单命令的网络/处理延迟，命令进入 inflight 队列并在未来时间点执行：

```python
from nautilus_trader.backtest.config import ImportableLatencyModelConfig

latency_config = ImportableLatencyModelConfig(
    latency_model_path="nautilus_trader.backtest.models:LatencyModel",
    config_path="nautilus_trader.backtest.config:LatencyModelConfig",
    config={
        "base_latency_nanos": 5_000_000,     # 5ms 基础延迟（所有命令）
        "insert_latency_nanos": 2_000_000,    # +2ms 下单延迟
        "update_latency_nanos": 3_000_000,    # +3ms 改单延迟
        "cancel_latency_nanos": 1_000_000,    # +1ms 撤单延迟
    },
)
```

实际延迟 = `base_latency_nanos + 命令特定延迟`。零延迟配置也能正确结算。延迟命令被推迟到引擎时钟到达该时间点时处理。

### 5.3 FeeModel 手续费模型

**MakerTakerFeeModel**（最常用）：使用 Instrument 对象上定义的 `maker_fee`/`taker_fee` 费率。正值为佣金（扣费），负值为返佣（增加余额）。例如 `maker_fee=-0.00025` 表示 maker 返佣 0.025%，`taker_fee=0.00075` 表示 taker 佣金 0.075%。

```python
fee_config = ImportableFeeModelConfig(
    fee_model_path="nautilus_trader.backtest.models:MakerTakerFeeModel",
    config_path="nautilus_trader.backtest.config:MakerTakerFeeModelConfig",
    config={},  # 无需额外参数，费率来自 Instrument
)
```

**FixedFeeModel**（固定费用）：每笔交易固定佣金，不受成交量影响。

自定义 FeeModel 需继承 Cython 基类并重写 `get_commission()` 方法。

### 5.4 保证金模型

| 模型 | 公式 | 适用场景 |
|------|------|---------|
| `LeveragedMarginModel`（默认） | `(名义值 / 杠杆) × margin_init` | 加密货币交易所（Binance、Bybit 等） |
| `StandardMarginModel` | `名义值 × margin_init`（忽略杠杆） | 传统经纪商（IB、CME） |

---

## 六、Strategy 生命周期与回测集成

### 6.1 生命周期状态机

所有组件遵循有限状态机：**PRE_INITIALIZED → READY → RUNNING → STOPPED → DISPOSED**，附加状态 DEGRADED、FAULTED。

**关键原则**：`__init__` 中**不可**访问 `self.clock`、`self.logger` 等系统组件——它们在策略注册到系统后才初始化。所有初始化逻辑放在 `on_start` 中。

### 6.2 完整回调列表

**生命周期回调：**

```python
def on_start(self) -> None:       # 初始化（订阅数据、注册指标、获取 Instrument）
def on_stop(self) -> None:        # 清理（取消订单、平仓、取消订阅）
def on_resume(self) -> None:      # 恢复运行
def on_reset(self) -> None:       # 重置状态（用于参数扫描间的重置）
def on_dispose(self) -> None:     # 释放所有资源
def on_save(self) -> dict:        # 返回持久化状态字典
def on_load(self, state) -> None: # 加载已保存状态
```

**数据回调：**

```python
def on_quote_tick(self, tick: QuoteTick) -> None:
def on_trade_tick(self, tick: TradeTick) -> None:
def on_bar(self, bar: Bar) -> None:
def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
def on_order_book(self, order_book: OrderBook) -> None:
def on_instrument(self, instrument: Instrument) -> None:
def on_instrument_status(self, data: InstrumentStatus) -> None:
def on_data(self, data: Data) -> None:        # 自定义数据
def on_signal(self, signal: Data) -> None:    # 自定义信号
def on_historical_data(self, data: Data) -> None:
```

**订单事件回调**（分发顺序：具体处理器 → `on_order_event` → `on_event`）：

```python
def on_order_initialized(self, event) -> None:
def on_order_submitted(self, event) -> None:
def on_order_accepted(self, event) -> None:
def on_order_rejected(self, event) -> None:
def on_order_filled(self, event) -> None:
def on_order_canceled(self, event) -> None:
def on_order_updated(self, event) -> None:
def on_order_expired(self, event) -> None:
def on_order_triggered(self, event) -> None:
def on_order_denied(self, event) -> None:
def on_order_event(self, event) -> None:      # 所有订单事件兜底
```

**持仓事件回调**（同样分发到 `on_position_event` → `on_event`）：

```python
def on_position_opened(self, event) -> None:
def on_position_changed(self, event) -> None:
def on_position_closed(self, event) -> None:
```

### 6.3 Clock 和 Timer 在回测中的行为

回测时 Clock 处于**静态模式**，时间随数据时间戳推进（通过 `AtomicTime::set_time` 控制），与实盘的实时模式 API 完全一致：

```python
# 获取当前时间（回测中是模拟时间）
now = self.clock.utc_now()
unix_ns = self.clock.timestamp_ns()

# 设置时间告警（到达指定时间触发 TimeEvent）
self.clock.set_time_alert("my_alert", self.clock.utc_now() + pd.Timedelta(minutes=5))

# 设置定时器（周期性触发）
self.clock.set_timer("my_timer", interval=pd.Timedelta(minutes=1))
```

| 方面 | 回测 | 实盘 |
|------|------|------|
| 时钟模式 | 静态，随数据推进 | 实时，系统时钟 |
| Timer 精度 | 纳秒级确定性 | 微秒级延迟 |
| Timer 排序 | 按时间戳确定性排序 | 受系统调度影响 |
| TimeEvent 投递 | 按时间戳批处理后结算交易所 | 异步投递 |

回测引擎支持**纯 Timer 驱动回测**（无市场数据），适用于定时调仓等场景。Timer 回调中可通过 `add_data_iterator()` 动态添加数据。

---

## 七、BacktestEngine 完整 API 参考

### 7.1 核心方法

```python
engine = BacktestEngine(config=BacktestEngineConfig())

# 添加交易所
engine.add_venue(venue, oms_type, account_type, starting_balances, ...)

# 添加品种和数据
engine.add_instrument(instrument)
engine.add_data(data, sort=True)         # sort=False 延迟排序
engine.sort_data()                        # 手动排序（批量添加后调用一次）
engine.add_data_iterator(name, generator) # 流式懒加载

# 添加策略/Actor/执行算法
engine.add_strategy(strategy)
engine.add_actor(actor)
engine.add_exec_algorithm(exec_algorithm)

# 运行
engine.run(start=None, end=None, streaming=False)
engine.end()                              # 结束流式运行

# 状态管理
engine.reset()      # 重置交易状态，保留数据和品种配置
engine.clear_data()  # 清除内部数据流
engine.dispose()     # 不可逆释放，之后不可调用任何方法

# 结果
result = engine.get_result()              # 返回 BacktestResult

# 动态修改
engine.change_fill_model(venue, new_fill_model)

# 序列化
pickled = engine.dump_pickled_data()
engine.load_pickled_data(pickled)
```

### 7.2 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `trader` | `Trader` | 内部 Trader 实例 |
| `trader_id` | `TraderId` | 交易者 ID |
| `instance_id` | `UUID4` | 实例 ID |
| `iteration` | `int` | 当前迭代计数 |
| `kernel` | `NautilusKernel` | 内部核心 |
| `run_config_id` | `str` | 回测配置 ID（tokenized） |
| `venues` | — | 已添加的交易所 |

### 7.3 BacktestNode

```python
from nautilus_trader.backtest.node import BacktestNode

node = BacktestNode(configs=[config1, config2])  # 多配置批量回测
results: list[BacktestResult] = node.run()        # 顺序执行，返回结果列表
```

### 7.4 大数据集优化模式

```python
# 延迟排序：批量添加后一次性排序
engine.add_data(bars1, sort=False)
engine.add_data(bars2, sort=False)
engine.sort_data()

# 流式 API：超内存数据集
def data_gen():
    yield load_chunk_1()
    yield load_chunk_2()
engine.add_data_iterator("stream", data_gen())
engine.run()

# 手动分块（BacktestNode 内部机制）
for batch in data_batches:
    engine.add_data(batch)
    engine.run(streaming=True)
    engine.clear_data()
engine.end()
```

---

## 八、可视化系统：Plotly Tearsheet

NautilusTrader v1.222.0 引入了基于 **Plotly ≥ 6.3.1** 的原生交互式 HTML Tearsheet 系统，取代了对 quantstats 等外部库的依赖。

### 8.1 快速生成

```python
from nautilus_trader.analysis import create_tearsheet

engine.run()
create_tearsheet(engine=engine, output_path="tearsheet.html")
```

### 8.2 自定义配置

```python
from nautilus_trader.analysis import (
    TearsheetConfig, TearsheetEquityChart, TearsheetDrawdownChart,
    TearsheetStatsTableChart, TearsheetRunInfoChart, TearsheetBarsWithFillsChart,
    GridLayout,
)

config = TearsheetConfig(
    charts=[
        TearsheetRunInfoChart(),       # 运行元数据和账户余额
        TearsheetStatsTableChart(),    # PnL/收益率/通用统计
        TearsheetEquityChart(),        # 累计收益曲线（可叠加基准）
        TearsheetDrawdownChart(),      # 回撤百分比
    ],
    theme="nautilus_dark",  # plotly_white/plotly_dark/nautilus/nautilus_dark
    height=2000,
    title="Q4 2025 Strategy Performance",
    layout=GridLayout(rows=2, cols=2, heights=[0.6, 0.4]),
)
create_tearsheet(engine=engine, output_path="custom.html", config=config)
```

### 8.3 内置图表类型

| 注册名 | 类型 | 说明 |
|--------|------|------|
| `run_info` | 表格 | 运行元数据和账户余额 |
| `stats_table` | 表格 | PnL、收益率、通用指标三栏统计 |
| `equity` | 折线图 | 累计收益曲线，可叠加 benchmark |
| `drawdown` | 面积图 | 峰值回撤百分比 |
| `monthly_returns` | 热力图 | 按年月展示月度收益率 |
| `distribution` | 直方图 | 收益率分布 |
| `rolling_sharpe` | 折线图 | 60 日滚动 Sharpe 比率 |
| `yearly_returns` | 柱状图 | 年度收益率 |
| `bars_with_fills` | K线图 | OHLC K 线叠加成交标记 |

支持基准对比（传入 `benchmark_returns` Series），自定义主题注册（`register_theme`），自定义图表注册（`register_chart` / `TearsheetCustomChart`）。独立图表函数包括 `create_equity_curve()`、`create_drawdown_chart()`、`create_monthly_returns_heatmap()`、`create_returns_distribution()`、`create_bars_with_fills()`。

---

## 九、Reports 报告系统与 PortfolioAnalyzer

### 9.1 ReportProvider 生成结构化 DataFrame

```python
# 推荐：通过 Trader 快捷方法
orders_report = engine.trader.generate_orders_report()       # 所有订单
fills_report = engine.trader.generate_fills_report()          # 逐笔成交
order_fills = engine.trader.generate_order_fills_report()     # 有成交的订单
positions_report = engine.trader.generate_positions_report()  # 持仓报告
account_report = engine.trader.generate_account_report(venue) # 账户报告

# 直接使用 ReportProvider
from nautilus_trader.analysis import ReportProvider
orders = engine.cache.orders()
report = ReportProvider.generate_orders_report(orders)
```

### 9.2 PortfolioAnalyzer 性能统计

```python
analyzer = engine.portfolio.analyzer

# 三组统计指标
stats_pnls = analyzer.get_performance_stats_pnls()       # PnL 统计（按货币）
stats_returns = analyzer.get_performance_stats_returns()   # 收益率统计
stats_general = analyzer.get_performance_stats_general()   # 通用统计

# 收益率序列
returns = analyzer.returns()             # 自动选择（优先 portfolio_returns）
port_returns = analyzer.portfolio_returns()  # 账户余额日收益率（需≥2天数据）
pos_returns = analyzer.position_returns()    # 方向感知的持仓价格收益率
```

内置统计指标类：`ProfitFactor`、`WinRate`、`SharpeRatio`（252 天年化）、`SortinoRatio`、`ReturnsAverage`、`ReturnsAverageLoss`、`ReturnsAverageWin`、`ReturnsVolatility`、`RiskReturnRatio`。

**注册自定义指标：**

```python
from nautilus_trader.analysis.statistic import PortfolioStatistic

class MaxConsecutiveLosses(PortfolioStatistic):
    def calculate_from_realized_pnls(self, realized_pnls):
        max_streak, current = 0, 0
        for pnl in realized_pnls:
            current = current + 1 if pnl <= 0 else 0
            max_streak = max(max_streak, current)
        return max_streak

analyzer.register_statistic(MaxConsecutiveLosses())
```

---

## 十、实际示例与教程索引

### 10.1 GitHub 示例目录 `examples/backtest/`

| 文件 | 内容 |
|------|------|
| `crypto_ema_cross_ethusdt_trade_ticks.py` | Binance ETH/USDT Trade Tick EMA 交叉 |
| `crypto_ema_cross_ethusdt_trailing_stop.py` | 带追踪止损的 EMA 交叉 |
| `fx_ema_cross_audusd_ticks.py` | AUD/USD Tick 级 EMA 交叉 |
| `fx_ema_cross_bracket_gbpusd_bars_external.py` | GBP/USD Bar 级 Bracket 订单 |
| `fx_market_maker_gbpusd_bars.py` | 波动率做市商 + FillModel + FXRolloverInterest |
| `databento_ema_cross_long_only_tsla_trades.py` | Databento TSLA Trade 数据仅做多 |
| `model_configs_example.py` | 完整配置示例（FillModel/FeeModel/LatencyModel） |

2025-2026 年新增示例（PR 编号）：clock & timers (#2327)、bar aggregation (#2340)、portfolio (#2362)、cache (#2370)、cascaded indicators (#2398)、custom event with msgbus (#2400)、messaging with actor & data (#2407)、messaging with actor & signal (#2408)。

### 10.2 官方 Jupyter Notebook 教程

- **Backtest: FX bar data** — 低级 API + USD/JPY Bar + FillModel + FXRolloverInterest
- **Backtest: Binance OrderBook data** — BacktestNode + L2_MBP + OrderBookImbalance
- **Backtest: Bybit OrderBook data** — Bybit L2 订单簿回测
- **Loading External Data** — 完整数据加载管线
- **Databento data catalog** — Databento 数据集成

Docker 快速启动：`docker run -p 8888:8888 ghcr.io/nautechsystems/jupyterlab:nightly`（注意设置 `log_level="ERROR"` 避免 Jupyter stdout 限速）。

### 10.3 内置示例策略

`nautilus_trader.examples.strategies` 下提供即用策略：`EMACross`、`EMACrossLongOnly`、`EMACrossBracket`、`EMACrossTrailingStop`、`VolatilityMarketMaker`、`OrderBookImbalance`，每个策略配套对应的 Config 类，可直接用于回测验证。

---

## 工程实践要点

**回测-实盘一致性**是 NautilusTrader 的核心设计哲学。策略代码、配置结构、回调接口在回测和实盘环境之间**零修改**切换。核心交易逻辑运行在单线程上（灵感来自 LMAX Disruptor 模式），保证确定性事件排序和回测可复现性。MessageBus 实现了组件间的发布/订阅解耦，同一消息总线在回测中同步处理、在实盘中异步处理。

对于加密货币量化场景，建议关注几个关键配置组合：`account_type="MARGIN"` + `LeveragedMarginModel`（匹配 Binance/Bybit 保证金计算逻辑），`MakerTakerFeeModel` 配合 Instrument 上的负 `maker_fee`（返佣），`LatencyModel` 设置 **5-50ms** 基础延迟模拟真实 API 响应，以及 `liquidity_consumption=True` 配合 L2 订单簿数据以获得最真实的成交模拟。`ts_init_delta` 的正确设置是避免前视偏差的最后一道防线——任何数据源的时间戳语义都必须被显式处理。