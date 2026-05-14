# TinoHelm NT-first 重构方案

> 结论：**原方案不全。补全后，NT-first 的边界应该是：NautilusTrader 负责 trading runtime / data truth / execution / backtest / live / message bus / logging / portfolio / reports；TinoHelm 负责产品层、研究层、数据接入编排、API/job/UI 和少量薄适配。**
>
> 不是所有代码都塞进 NT。真正的 NT-first 是：**凡是在 NT system boundary 内发生的交易运行时事情，都用 NT；凡是 API、worker、UI、DB、数据下载、因子研究这些进程外/产品层事情，Tino 保留。**

---

## 1. 我对 NT docs 的结论

看过的 NT concepts 能力面：

- `Overview` / `Architecture`
- `Actors` / `Strategies`
- `Instruments` / `Synthetics` / `Value Types`
- `Data` / `Custom Data` / `Order Book`
- `Events` / `Message Bus`
- `Execution` / `Orders` / `Positions`
- `Cache` / `Accounting` / `Portfolio`
- `Reports`
- `Logging`
- `Backtesting`
- `Configuration`
- `Live Trading`
- `Adapters`
- `Options` / `Greeks`
- `Rust`

核心判断：

1. **能重构成 NT-first，但不是把 Tino 清空。**
   - trading runtime、backtest、live/sandbox、execution、risk、portfolio、cache、message bus、logging、reports：应该交给 NT。
   - ingest orchestration、factor research、signal evaluation、API/job/UI、DB persistence、artifact management：仍是 Tino 的产品层。

2. **当前方案漏了三块关键能力：MessageBus、Logging、Reports/Portfolio/Accounting。**
   - 原方案偏 data/catalog/backtest/live config，没把 NT runtime 的 bus 和 log 当一等公民。
   - 这会导致“NT 只是引擎，Tino 还有一套运行时胶水”的老问题继续存在。

3. **message bus 应统一成一套逻辑模型，物理层可以用 Redis。**
   - NT node 内部：用 NT `MessageBus`。
   - 跨进程 API/job/UI：也可以走 message-bus 模式，但应通过 NT external streams / edge adapter / typed command-event schema 接入。
   - Redis 是可接受的 backing/transport；问题是绕开 NT 另造一套 `tino:*` runtime topic 语义。

4. **日志也一样。**
   - Actor/Strategy/NT runtime 内用 `LoggingConfig` + `self.log`。
   - FastAPI、worker、CLI、migration 这种 NT boundary 外的东西可以保留 Python logging。

5. **NT 有的交易语义都要纳入方案。**
   - order types、TIF、post-only、reduce-only、trigger orders、contingency orders、OMS netting/hedging、OrderEmulator、RiskEngine、ExecutionEngine、Portfolio、Accounting、ReportProvider、live reconciliation。

---

## 2. NT-first 总边界

### 2.1 NT 负责

- `ParquetDataCatalog`
- `BacktestNode` / `BacktestRunConfig`
- `BacktestEngine`（只作为低层例外路径）
- `TradingNode` / `TradingNodeConfig`
- `DataEngine` / `ExecutionEngine` / `RiskEngine`
- `OrderEmulator` / `ExecAlgorithm`
- `Cache`
- `MessageBus`
- `Portfolio` / `Accounting`
- `ReportProvider`
- `LoggingConfig` / runtime logger
- `Actor` / `Strategy` lifecycle
- data clients / execution clients / instrument providers
- built-in data types / custom data registry

### 2.2 TinoHelm 负责

- data download / provider ingest / staging / retry / promotion
- factor mining / factor registry / factor evaluation
- signal evaluation / export / compare
- API routes / service layer
- background workers / job recovery
- DB persistence for product state
- WebSocket UI bridge
- artifact storage / indexing
- minimal NT adapter layer：target weight -> NT order API、NT cache -> factor panel

### 2.3 禁止保留的重复实现

- 自己实现 runtime message bus
- 在 actor/strategy 里直接操作裸 Redis PubSub，绕过 NT MessageBus
- 自己维护订单状态机
- 自己维护 position truth
- 自己重算 accounting / portfolio truth
- 自己维护 backtest streaming 主循环作为正式路径
- 自己造 catalog schema 作为业务真相源
- runtime actor/strategy 内混 Python module logger

---

## 3. 当前 TinoHelm 的具体问题

### 3.1 Data/catalog 重复实现

`src/tinohelm/data/catalog.py` 和周边逻辑现在混了：

- Parquet 路径组织
- 写入前后恢复
- compaction
- local/remote promotion
- raw metrics / book depth / funding 独立 parquet 读写
- catalog lifecycle 控制

这应该拆成：

- NT `ParquetDataCatalog` facade
- ingest staging/promotion/rollback
- provider-specific converter

业务查询路径不能绕过 NT catalog 扫 raw parquet。

### 3.2 Backtest runner 绕过 NT 高层 API

`src/tinohelm/backtest/runner.py` 现在做了太多：

- 手工装 `BacktestEngine`
- 手工 load catalog
- 手工注入 mark/index/funding
- 手工 streaming / oneshot
- 手工 result/report/artifact extraction

正式回测主路应该变成：

- `BacktestRunSpec`（Tino product spec）
- -> NT `BacktestRunConfig`
- -> `BacktestNode`
- -> NT reports / result extraction

低层 `BacktestEngine` 保留为测试/spike/小数据例外，不做正式主路。

### 3.3 Live/sandbox config 边界不干净

`node/factory.py` 这类 dict builder 应改成 typed spec builder：

- 内部是 `NodeRunSpec` / NT config object
- API/Redis 边界才 JSON serialization
- `TradingNodeConfig.catalogs` 要真正使用
- data/execution/instrument provider 由 adapter factory 装配

### 3.4 MessageBus 分层不清

当前已经有 NT msgbus 雏形：

- `src/tinohelm/node/topics.py`
- `src/tinohelm/node/lifecycle_controller.py`
- `src/tinohelm/actors/risk_guard.py`
- `src/tinohelm/strategy/utils.py`

但同时还有 Redis PubSub/EventBridge：

- `src/tinohelm/core/bridge.py`
- `src/tinohelm/api/ws/hub.py`
- `src/tinohelm/factor/worker.py`
- `src/tinohelm/signal/worker.py`

这里不能简单写成“Redis 不是 runtime bus”。NT 文档明确说了：`MessageBus` 可以配置外部 backing technology，目前 Redis streams 是支持路径；producer node 可以把消息 publish 到 external stream，consumer node 再从 external stream 读入并发布回自己的 internal message bus。

所以正确边界是：

- **逻辑通信模型**：统一用 NT `MessageBus` 的 data / events / commands / topic 语义。
- **物理传输/持久化**：可以是 Redis streams，前提是通过 `MessageBusConfig` / `DatabaseConfig` / `external_streams` 纳入 NT MessageBus 体系。
- **不合理的点**：Tino 自己直接用 Redis PubSub 另起 `tino:*` runtime topics，绕过 NT MessageBus，形成第二套消息语义。
- **EventBridge**：可以保留为 message-bus edge adapter / UI projection，但它应该消费或投影 NT MessageBus 事件，而不是定义另一套 runtime truth。

### 3.5 Logging 没统一

`backtest/runner.py` 已经有最小 `LoggingConfig(log_level="WARNING")`，但 actor/runtime 内仍有 Python logger，例如 `risk_guard.py`。

规则：

- Actor/Strategy 内禁止 `logging.getLogger(__name__)`。
- 进入 NT node 的 runtime log 一律 `self.log`。
- API/worker/CLI 保留 Python logging。

---

## 4. NT capability map：必须吃满的能力

### 4.1 Data / Catalog / Custom Data

优先使用 NT built-in data：

- `Bar`
- `QuoteTick`
- `TradeTick`
- `OrderBookDelta` / `OrderBookDeltas`
- `OrderBookDepth10`
- `MarkPriceUpdate`
- `IndexPriceUpdate`
- `FundingRateUpdate`
- `InstrumentStatus`
- `InstrumentClose`

只有 NT 没有原生类型，才用：

- `CustomData`
- `DataType`
- `customdataclass_pyo3`
- custom data registry

Order book 数据要遵守 NT event boundary：

- `RecordFlag.F_LAST`
- `RecordFlag.F_SNAPSHOT`

否则 `DataEngine` buffer 会一直攒 delta，不发布给 subscribers。

### 4.2 Instruments / Value Types

Tino 不应该自己用 string + Decimal 拼合约语义。

NT-first 规则：

- crypto perp：`CryptoPerpetual` / `PerpetualContract`
- instrument identity：`InstrumentId` / `Venue` / `Symbol`
- numeric domain：`Price` / `Quantity` / `Money`
- position quantity / order quantity 由 NT instrument precision/min/max 约束

### 4.3 Actors / Strategies / Events

Actor/Strategy 已经有完整 lifecycle：

- `on_start`
- `on_stop`
- `on_resume`
- `on_reset`
- `on_degrade`
- `on_fault`
- `on_dispose`
- `on_save`
- `on_load`

数据 handler：

- `on_bar`
- `on_quote_tick`
- `on_trade_tick`
- `on_order_book_deltas`
- `on_order_book_depth`
- `on_data`
- `on_signal`

事件 handler：

- `on_order_event`
- `on_position_event`
- `on_event`
- specific order/position event handlers

Tino 不需要再发明 lifecycle framework。

### 4.4 MessageBus

NT supports：

- pub/sub
- point-to-point
- request/response
- data / events / commands
- direct `self.msgbus`
- actor-level `publish_data()`
- actor-level `publish_signal()`
- external streams via `MessageBusConfig`

Tino rule：

- Runtime component communication uses NT MessageBus semantics only.
- Redis 可以是 NT MessageBus 的 backing/external stream，不应该是 Tino 自己绕过 NT 的平行 PubSub runtime。
- API / UI / worker 也可以接入这套 message-bus 模式，但应该通过 edge adapter、external stream、typed command/event schema 接入，而不是随意写 `tino:*` topic。
- For cross-node/event streaming, evaluate NT `MessageBusConfig.external_streams` / `streams_prefix` / `stream_per_topic` first.

### 4.5 Logging

NT logging supports：

- Rust MPSC logging thread
- stdout/stderr writer
- file writer
- JSON log file
- rotation and backup count
- component-level filter
- ANSI color
- `use_pyo3`
- `clear_log_file`

Tino rule：

- `BacktestRunSpec` / `NodeRunSpec` must map to full `LoggingConfig`。
- Runtime logs go to NT logging。
- Artifacts collect NT log files, not just Python stdout。

### 4.6 Execution / Orders / Positions

NT execution mainline：

- `Strategy.submit_order`
- `submit_order_list`
- `modify_order`
- `cancel_order`
- `cancel_all_orders`
- `close_position`
- `close_all_positions`
- `query_account`
- `query_order`

NT execution components：

- `RiskEngine`
- `ExecutionEngine`
- `LiveExecutionEngine`
- `ExecutionClient`
- `OrderEmulator`
- `ExecAlgorithm`

OMS 必须显式暴露：

- `NETTING`：每个 instrument 一个净仓，适合大多数 crypto perp 账户形态。
- `HEDGING`：同一 instrument 多个 long/short position，适合需要虚拟分仓或 venue 原生 hedging 的场景。
- strategy OMS 与 venue OMS 不一致时，按 NT `ExecutionEngine` 的 position id override / virtual position 语义处理，不在 Tino 自己维护仓位映射。

Order capabilities to expose：

- market / limit
- stop market / stop limit
- market-to-limit
- market-if-touched / limit-if-touched
- trailing stop market / trailing stop limit
- TIF：IOC / FOK / GTC / GTD / DAY / AT_THE_OPEN / AT_THE_CLOSE
- post-only
- reduce-only
- iceberg / display qty
- trigger type
- contingency orders：OCO / OUO / OTO

Tino `OrderManager` 只能做 target -> NT order API translation，不维护订单状态机。

### 4.7 Risk / Portfolio / Accounting

NT 负责：

- account balances
- margin balances
- locked/free/total invariant
- position lifecycle
- realized/unrealized PnL
- commission adjustments
- funding adjustments
- portfolio exposure
- equity
- mark-to-market
- currency conversion

Tino risk guard 只能是 policy layer：

- drawdown kill-switch
- exposure cap
- lifecycle pause/resume/flatten trigger
- alert/projection

不能替代 NT Portfolio/Accounting/RiskEngine。

### 4.8 Reports

NT provides：

- orders report
- order fills report
- fills report
- positions report
- account report
- `ReportProvider`
- trader helper methods

Tino artifacts/report 层应该包装这些结果：

- 保存
- 索引
- diff/compare

不重新定义订单/成交/仓位/PnL schema。

### 4.9 Backtesting

默认正式路径：

- `ParquetDataCatalog`
- `BacktestDataConfig`
- `BacktestVenueConfig`
- `BacktestRunConfig`
- `BacktestNode`

正式回测必须支持/暴露：

- streaming / chunking
- fill model
- latency model
- bar execution
- initial balance
- account type
- OMS type
- multi-venue
- multi-instrument
- multi-strategy
- custom data
- deterministic trade id
- NT reports

低层 `BacktestEngine` 只保留为：

- 小数据集完全进内存
- spike
- unit/integration tests
- 需要精细替换 actor/strategy/exec algo 的局部实验
- 不想先落 NT catalog 的临时验证

### 4.10 Live / Sandbox

Live/sandbox 主路：

- `TradingNodeConfig`
- `TradingNode`
- adapter data clients
- adapter execution clients
- instrument providers
- `DataCatalogConfig` for historical access
- `LiveExecutionEngine` reconciliation
- execution event persistence in cache database

Live 重点：

- startup reconciliation
- in-flight order checks
- order/fill/position status reports
- external order claims
- persist all execution events to reduce venue lookback dependency

同进程并发多个 node 不作为目标。多策略放一个 node；并行隔离用多进程。

### 4.11 Synthetics / Options / Greeks

当前 30s-min crypto perp 策略主路不依赖 options/greeks，但方案里要留 NT 能力入口：

- synthetic instruments 可用于 spread、cross-venue basket、derived index。
- options/greeks 可用于后续 Deribit/OKX/Bybit options 风控或研究。
- 不在现阶段强行做，但不能设计成和这些能力冲突。

### 4.12 Rust / PyO3 路线

当前主路仍用 Python v1，因为它 feature-complete：

- controller
- config serialization
- mature Python strategy surface

Rust/PyO3 后续适合作为性能路径：

- high-throughput actor
- native custom data
- execution-critical component

不为了“更 Rust”提前丢掉 v1 成熟功能。

---

## 5. 模块设计

### 5.1 Data 模块

目标：Tino data layer 变成 NT catalog ingest/control plane。

保留：

- downloader
- provider client
- converter
- staging/promotion/rollback
- object storage coordination

删除/收缩：

- 自写 catalog truth
- raw parquet 业务查询主路
- metrics/funding/mark/index side-channel truth

写入：

```python
catalog.write_data(data)
```

读取：

- `catalog.bars(...)`
- `catalog.trade_ticks(...)`
- `catalog.quote_ticks(...)`
- `catalog.custom_data(...)`
- other NT catalog query methods

### 5.2 Backtest 模块

目标：正式路径切到 `BacktestNode`。

Tino service 做：

1. validate API payload
2. build `BacktestRunSpec`
3. convert to NT configs
4. run `BacktestNode`
5. collect NT reports / logs / artifacts
6. persist product state

不再做：

- 手写 streaming sort/clear/end 主循环
- 自己维护 fill lifecycle
- 自己抽订单仓位 truth

### 5.3 Live / Sandbox 模块

目标：只装配，不重写 runtime。

- `node/factory.py` -> typed spec builder
- `live.py` / `sandbox.py` -> `TradingNodeConfig` runner
- `TradingNodeConfig.catalogs` 真正启用
- adapter 只负责 `InstrumentProvider` / `DataClient` / `ExecutionClient`

### 5.4 Factor / Signal 模块

研究层保留在 Tino：

- IC/IR
- turnover
- robustness
- walk-forward
- signal evaluation
- export/compare

执行层交给 NT：

- `SignalDrivenStrategy`
- `OrderManager`
- NT cache / portfolio / order API

`factor_panel.py` 只做 NT cache -> panel adapter。

### 5.5 NT Adapter 模块

保留但要薄：

- `bar_synchronizer.py`
- `factor_panel.py`
- `order_manager.py`
- `signal_driven_strategy.py`

不能演变成：

- 第二套 execution engine
- 第二套 state machine
- 第二套 risk/portfolio engine

### 5.6 MessageBus 模块

当前 `node/topics.py` 可以保留，但只作为 topic constants。

Runtime rule：

- actor/strategy communication -> NT `self.msgbus`
- structured runtime data -> `publish_data`
- lightweight signal -> `publish_signal`
- cross-process / cross-node stream -> NT `MessageBusConfig` external streams backed by Redis when appropriate
- external UI/API projection -> edge adapter from NT MessageBus event/stream to WebSocket/HTTP clients

Redis rule：

- Redis streams 可以作为 NT MessageBus external backing。
- Redis keys/queues 可以继续承载 job queue、worker progress、durable recovery。
- 禁止的是“绕过 NT MessageBus 的第二套 runtime topic semantics”，不是 Redis 这个物理组件本身。
- API/UI/worker 发 command/event 时，应进入统一 typed bus contract：validate -> command/event -> NT MessageBus/external stream -> actor/strategy handler。

### 5.7 Logging 模块

Runtime rule：

- `LoggingConfig` in `BacktestRunConfig` / `TradingNodeConfig`
- `self.log` in actors/strategies
- file/json/component config exposed by Tino specs

Python logging remains only outside NT boundary。

### 5.8 API / Job / Ops 模块

API route 只做 HTTP。

Service 层负责：

- build Tino product spec
- convert to NT typed config
- enqueue job
- collect artifact
- persist DB rows

EventBridge 只做 projection：

- Redis -> WebSocket today
- NT MessageBus -> projection bridge later

---

## 6. 迁移路线

### Phase 0：冻结行为

- golden backtest fixtures
- signal export fixtures
- catalog read/write fixture
- live config serialization fixture
- risk/lifecycle message fixture

### Phase 1：Catalog facade 化

- split `data/catalog.py`
- introduce `NTDataCatalogFacade`
- keep ingest staging outside catalog truth
- migrate built-in NT data types first

### Phase 2：Data type 迁移

- bars/ticks/orderbook/mark/index/funding 全部 NT built-in
- unsupported domain data -> custom data registry
- factor data layer reads NT catalog first

### Phase 3：Backtest runner 重写

- build `BacktestRunSpec`
- map to `BacktestRunConfig`
- run `BacktestNode`
- collect `ReportProvider` outputs / logs
- low-level `BacktestEngine` demoted to test/spike helper

### Phase 4：Factor -> Signal -> NT adapter 收敛

- keep research in Tino
- keep `SignalDrivenStrategy` thin
- keep `OrderManager` as translation only
- reject unsupported factor inputs at export boundary

### Phase 5：Live / Sandbox typed config

- `node/factory.py` outputs typed spec/config
- enable `TradingNodeConfig.catalogs`
- adapter boundary clear
- no loose runtime dict

### Phase 6：MessageBus / Logging runtime 统一

- Actor/Strategy internal communication via NT `MessageBus`
- Redis PubSub 旧路径改造为 NT MessageBus external streams / edge adapter，不再保留平行 topic semantics
- API/UI/worker 接入统一 typed bus contract：command/event schema + validation + NT MessageBus/external stream
- runtime actor/strategy all use `self.log`
- expose full `LoggingConfig` / `MessageBusConfig`
- evaluate `MessageBusConfig.external_streams` for cross-node stream use cases

### Phase 7：API / worker 迁移到 service 层

- route only I/O
- service builds NT configs
- workers run product jobs, not runtime internals
- bridge sends event projections only

### Phase 8：删旧路径

Delete or quarantine：

- raw parquet business truth path
- custom backtest streaming mainloop
- loose live config dict builder
- runtime Redis PubSub path
- actor/strategy Python logger usage
- duplicated report/PnL schema

---

## 7. 文件级 action list

### Keep and shrink

- `src/tinohelm/nt_adapter/*`
- `src/tinohelm/signal/*`
- `src/tinohelm/factor/*`
- `src/tinohelm/data/pipeline.py`
- `src/tinohelm/data/storage.py`
- `src/tinohelm/node/*`

### Split / rename / constrain

- `src/tinohelm/data/catalog.py`
  - -> `catalog_facade.py` + `ingest_transaction.py`
- `src/tinohelm/backtest/runner.py`
  - -> `nt_backtest_runner.py` or service-level runner
- `src/tinohelm/node/factory.py`
  - -> typed config builder
- `src/tinohelm/factor/data_layer.py`
  - -> NT catalog-first data layer
- `src/tinohelm/node/topics.py`
  - topic constants only; no bus implementation
- `src/tinohelm/core/bridge.py`
  - message-bus edge adapter / projection bridge
  - 可消费 NT MessageBus external stream 或 internal bus 投影
  - 不定义独立 runtime truth / topic semantics

### Must change

- `src/tinohelm/actors/risk_guard.py`
  - replace Python logger with `self.log`
  - publish runtime state only to NT msgbus
- `src/tinohelm/strategy/utils.py`
  - keep lifecycle subscription on NT msgbus
- `src/tinohelm/backtest/runner.py`
  - official path through `BacktestNode`
  - expose `LoggingConfig` / `MessageBusConfig`
- `src/tinohelm/nt_adapter/order_manager.py`
  - target -> NT order API only
  - no local order state machine
- `src/tinohelm/core/bridge.py`
  - consume/project NT MessageBus events or external streams
  - still edge adapter, not a second bus semantics

---

## 8. 验收标准

### Data

- 主流市场数据通过 NT catalog 查询。
- raw parquet 不作为业务查询真相源。
- custom data 只补 NT built-in 缺口。

### Backtest

- 正式入口是 NT config + `BacktestNode`。
- 大数据 streaming 不依赖自写 mainloop。
- fill/latency/bar execution/OMS/account type 可配置。
- reports 来自 NT。

### Live / Sandbox

- runtime config typed，不是 loose dict。
- strategy/actor lifecycle 走 NT。
- live reconciliation 配置可控。
- execution events 持久化到 cache database。

### MessageBus

- actor/strategy 内不直接操作裸 Redis PubSub 作为 runtime bus。
- runtime control/risk/lifecycle 走 NT MessageBus 语义。
- Redis streams 可作为 NT MessageBus backing/external stream。
- API/UI/worker 通过 typed command/event schema 接入统一 bus contract。
- external bridge payload 是 projection，不是独立 runtime truth。

### Logging

- actor/strategy 内没有 Python module logger。
- NT runtime logs 通过 `LoggingConfig` 管理。
- artifacts 收集 NT log files。

### Execution / Portfolio / Reports

- 订单状态、成交、仓位、账户、PnL truth 来自 NT Cache/Portfolio/Accounting。
- Tino 不重复维护订单/仓位状态机。
- report schema 包装 NT outputs，不重算 truth。

### Research

- factor/signal research 保留 Tino 优势。
- execution export contract 与 NT runtime 一致。
- unsupported inputs 在 export boundary fail fast。

---

## 9. 最终判断

**是，可以重构成 NT-first；但原方案确实不全。**

需要新增的不是“更多 wrapper”，而是把 NT runtime 的完整系统边界吃下来：

- MessageBus 统一
- Logging 统一
- Cache/Portfolio/Accounting/Reports 统一
- Execution/Risk/OrderEmulator/OMS/order event 统一
- BacktestNode/TradingNode typed config 统一

TinoHelm 留在 NT 外围，做研究、产品、编排和接入。交易运行时不要再保留第二套系统。
