# Portfolio Risk Architecture Design

## 背景

当前 BTCMultiFactor 策略是单品种设计，每个实例只订阅一个 instrument。
Runner 已支持多 symbol 参数，但只创建一个策略实例。

问题：
1. 策略内部的风控逻辑（daily_stop_loss、max_drawdown、total_risk_cap、max_positions）
   只在单个实例内生效，无法跨品种协调。
2. Runner 把多个 symbol 塞给一个策略实例，不符合 NT "每品种独立实例" 的设计模式。
3. 没有 Portfolio 层面的概念——缺少组合回测能力。

## 核心设计原则：一切皆投资组合

```
┌─────────────────────────────────────────────────────────┐
│  概念模型                                                │
│                                                         │
│  单 .py 文件  →  隐式 Portfolio（1 策略实例, 1 品种）      │
│  文件夹 + portfolio.yaml  →  显式 Portfolio（N 实例, M 品种）│
│                                                         │
│  Runner 统一走 Portfolio 路径:                             │
│    run_portfolio(config)                                │
│      → for symbol: engine.add_strategy(instance)        │
│      → for actor:  engine.add_actor(instance)  # 可选    │
│      → engine.run()                                     │
│                                                         │
│  "回测一个策略" 就是 "回测一个只有一个策略实例的投资组合"    │
└─────────────────────────────────────────────────────────┘
```

## 架构准则：单一职责，单一实现

**相同的逻辑只写一次，从架构上抽离为共享模块。** 不允许"这里实现一个，那里又实现一个"。

当前代码中的违规案例（本次重构必须修复）：

| 功能 | 当前问题 | 目标 |
|------|---------|------|
| 策略加载 | runner.py 用 importlib.util，sandbox.py 用 ImportableStrategyConfig，registry.py 用 importlib.import_module——三种写法 | 统一为 `portfolio_loader.py` |
| 参数类型检测 | runner.py 检查 model_fields + __struct_fields__，registry.py 只检查 model_fields | 抽为共享函数 `get_config_fields(cls)` |
| Redis 命令/心跳 | sandbox.py 和 live.py 各自内联 thread 实现，BridgeActor 写好了却没用 | 统一用 BridgeActor |
| Symbol 规范化 | runner.py 有 `_normalize_symbol()`，策略里有 `_instrument_to_jesse_symbol()` | 按需统一 |

原则：
- **共享模块 > 复制粘贴**：如果两个地方需要同样的逻辑，抽成模块
- **NT 原生优先**：已有 NT 组件能做的事（BridgeActor、msgbus），不自己搞替代方案
- **回测 = 实盘**：策略代码、Actor 代码、加载逻辑三种模式完全相同

## 目标架构

```
┌─────────────────────────────────────────────┐
│  BacktestEngine (一个引擎，一个账户)           │
│                                             │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐       │
│  │ BTC策略  │ │ ETH策略  │ │ XRP策略  │       │
│  │ (独立)   │ │ (独立)   │ │ (独立)   │       │
│  └────┬────┘ └────┬────┘ └────┬────┘       │
│       └───────────┼───────────┘             │
│                   │                         │
│          ┌────────┴────────┐                │
│          │ RiskGuardActor  │  ← 可选        │
│          │ - 总敞口监控     │                │
│          │ - 总仓位限制     │                │
│          │ - 组合回撤熔断   │                │
│          │ - 日级别 PnL    │                │
│          └─────────────────┘                │
│                   │                         │
│          ┌────────┴────────┐                │
│          │    Portfolio    │ ← NT 自动聚合   │
│          │  BTC+ETH+XRP   │    所有仓位     │
│          └─────────────────┘                │
└─────────────────────────────────────────────┘
```

## NT 组件职责划分

### RiskEngine（平台级，NT 内置，不需要写代码）
- 订单精度/余额/频率校验
- 单笔 notional 上限 (`max_notional_per_order`)
- 提交/修改速率限制 (`max_order_submit_rate`, `max_order_modify_rate`)
- 交易状态管理 (ACTIVE / REDUCING / HALTED)
- 所有订单都会经过 RiskEngine，无论是否有自定义 Actor

### RiskGuardActor（新增，跨策略风控，完全可选）
- 监听 position 事件和 bar 数据
- 通过 `self.portfolio` 和 `self.cache` 检查组合级风控
- 通过 **msgbus publish** 通知策略风控状态（不直接调用 `set_trading_state`）
- 策略通过 msgbus subscribe 或 cache key 读取风控状态，可选检查

### Strategy（单品种交易逻辑）
保留：
- 因子计算（momentum, volatility, mean_reversion, trend）
- 入场/出场信号判断 (entry_edge_min)
- Kelly 仓位计算 (fractional_kelly, kelly_cap)
- 单品种止损/止盈 (stop_atr_mult, tp1/tp2_atr_mult)
- transition probe 逻辑
- 因子缩放风控门 (disable_risk_gates 相关逻辑，见 `docs/design/risk-gates.md`)

移出到 Actor（当 Actor 存在时）：
- daily_stop_loss_pct → RiskGuardActor
- max_drawdown_stop_pct → RiskGuardActor
- total_risk_cap → RiskGuardActor
- max_positions（跨品种部分）→ RiskGuardActor

**关键：策略不 hard depend on Actor。** 无 Actor 时策略独立运行，有 Actor 时策略多一个可选检查。

## RiskGuardActor 核心设计

### 配置

```python
class RiskGuardConfig(ActorConfig):
    # 品种：Actor 需要订阅 bar 数据来检测日期切换
    bar_type: str = ""                         # 用于订阅 bar 和检测日期边界

    # 风控阈值
    max_total_exposure: float = 100_000.0      # 总敞口上限 (USDT)
    max_positions: int = 10                    # 跨品种总仓位上限
    daily_stop_loss_pct: float = -0.02         # 日亏损熔断 (-2%)
    max_drawdown_pct: float = -0.09            # 总回撤熔断 (-9%)

    # 熔断行为（三选一）
    # "reduce_only"  — 只允许减仓，阻止新开仓（默认，最安全）
    # "halt_new"     — 阻止新开仓，现有仓位自然止盈止损退出
    # "flatten_all"  — 立即平掉所有仓位
    breach_action: str = "reduce_only"

    # 结算币种
    currency: str = "USDT"
    venue: str = "BINANCE"
```

### 实现要点

```python
class RiskGuardActor(Actor):
    """
    跨策略组合级风控 Actor。

    通信机制：通过 NT msgbus 发布风控状态，策略订阅后可选检查。
    Actor 不直接调用 set_trading_state（该方法属于 RiskEngine，Actor 无权调用）。

    风控状态通过 msgbus topic "risk.guard.state" 发布:
    - "active"       正常交易
    - "reduce_only"  只允许减仓
    - "halt_new"     阻止新开仓
    - "flatten_all"  立即平仓
    """

    def on_start(self):
        # 订阅 position 事件
        self.subscribe_position_events()

        # 订阅 bar 数据用于日期边界检测
        if self.config.bar_type:
            self.subscribe_bars(BarType.from_str(self.config.bar_type))

        # 初始化
        venue = Venue(self.config.venue)
        currency = Currency.from_str(self.config.currency)
        account = self.portfolio.account(venue)
        self._equity = float(account.balance_total(currency))
        self._day_start_equity = self._equity
        self._peak_equity = self._equity
        self._current_date = None
        self._breach_state = "active"

    def on_bar(self, bar: Bar):
        """用 bar.ts_event 检测 UTC 日期切换。"""
        bar_date = pd.Timestamp(bar.ts_event, tz="UTC").date()
        if self._current_date is not None and bar_date != self._current_date:
            self._on_new_trading_day()
        self._current_date = bar_date

        # 每根 bar 都检查风控
        self._check_all()

    def on_position_changed(self, event):
        self._check_exposure()
        self._check_position_count()

    # ── 日边界: UTC 00:00 ────────────────────────────
    def _on_new_trading_day(self):
        """日 PnL 重置。基于 bar.ts_event 的 UTC 日期切换，不用 bar_count 近似。"""
        self._day_start_equity = self._get_equity()
        self.log.info(f"New trading day. Day start equity: {self._day_start_equity:.2f}")

    # ── Equity 计算 ──────────────────────────────────
    def _get_equity(self) -> float:
        """
        使用 portfolio.account.balance_total(currency)。
        balance_total 已包含未实现 PnL，比纯 balance 更准确。
        """
        venue = Venue(self.config.venue)
        currency = Currency.from_str(self.config.currency)
        account = self.portfolio.account(venue)
        return float(account.balance_total(currency))

    # ── 风控检查 ──────────────────────────────────────
    def _check_all(self):
        self._check_daily_pnl()
        self._check_drawdown()
        self._check_exposure()
        self._check_position_count()

    def _check_daily_pnl(self):
        equity = self._get_equity()
        daily_return = (equity - self._day_start_equity) / max(self._day_start_equity, 1e-9)
        if daily_return <= self.config.daily_stop_loss_pct:
            self._trigger_breach(f"Daily PnL {daily_return:.2%} hit limit")

    def _check_drawdown(self):
        """
        High-Water Mark (HWM):
        - 回测: per-run 追踪，从 starting_balance 开始，不跨 run 持久化
        - Live: 应从 DB 加载上次 peak，重启时恢复（未来实现）
        """
        equity = self._get_equity()
        self._peak_equity = max(self._peak_equity, equity)
        dd = (equity - self._peak_equity) / max(self._peak_equity, 1e-9)
        if dd <= self.config.max_drawdown_pct:
            self._trigger_breach(f"Drawdown {dd:.2%} hit limit")

    def _check_exposure(self):
        """
        遍历所有 open positions 的 net_exposure（per instrument）。
        注意：portfolio.net_exposures(venue) 返回 dict[Currency, Money]，
        是按币种聚合的，不是按品种。要看每个品种敞口需遍历 positions。
        """
        positions = self.cache.positions_open()
        total_exposure = 0.0
        for pos in positions:
            exp = self.portfolio.net_exposure(pos.instrument_id)
            total_exposure += abs(float(exp))
        if total_exposure > self.config.max_total_exposure:
            self._trigger_breach(f"Exposure {total_exposure:.2f} > limit {self.config.max_total_exposure}")

    def _check_position_count(self):
        positions = self.cache.positions_open()
        if len(positions) >= self.config.max_positions:
            self._trigger_breach(f"Open positions {len(positions)} >= limit {self.config.max_positions}")

    # ── 熔断触发 ──────────────────────────────────────
    def _trigger_breach(self, reason: str):
        if self._breach_state != "active":
            return  # 已经触发过
        action = self.config.breach_action
        self._breach_state = action
        self.log.error(f"RISK BREACH: {reason} → action={action}")

        # 通过 msgbus 发布风控状态，策略订阅后自行处理
        self.msgbus.publish(topic="risk.guard.state", msg=action)

        if action == "flatten_all":
            # 平掉所有仓位
            for pos in self.cache.positions_open():
                # Actor 不能直接下单，通过 msgbus 发指令
                self.msgbus.publish(
                    topic="risk.guard.flatten",
                    msg=str(pos.instrument_id),
                )
```

### 策略侧的可选风控检查

```python
# 策略 on_start 中订阅（可选）：
def on_start(self):
    # ... 原有逻辑 ...
    # 可选：订阅 RiskGuardActor 的风控状态
    try:
        self.msgbus.subscribe(topic="risk.guard.state", handler=self._on_risk_state)
        self._risk_halted = False
    except Exception:
        self._risk_halted = False  # 无 Actor 时不影响

def _on_risk_state(self, msg):
    self._risk_halted = msg in ("reduce_only", "halt_new", "flatten_all")

def _should_long(self) -> bool:
    if getattr(self, "_risk_halted", False):
        return False
    # ... 原有信号逻辑 ...
```

**关键特性：Actor 不存在时，`_risk_halted` 始终为 False，策略正常运行。**

## Runner 改动

### 当前问题

```python
# runner.py line 252-253 (当前):
strategy_instance = strategy_cls(config=config_cls(**filtered_params))
engine.add_strategy(strategy_instance)   # ← 只加一个实例
```

### 改动方向

```python
# 新逻辑（概念代码）:
# 1. 为每个 symbol 创建独立策略实例
for symbol in symbols:
    params_copy = {**filtered_params}
    params_copy["instrument_id"] = InstrumentId.from_str(symbol)
    params_copy["bar_type"] = BarType.from_str(bar_type_for_symbol)
    instance = strategy_cls(config=config_cls(**params_copy))
    engine.add_strategy(instance)

# 2. 可选：加载 Actor
if actor_configs:
    for actor_cfg in actor_configs:
        actor = load_actor(actor_cfg)
        engine.add_actor(actor)
```

### 单 .py 文件的隐式 Portfolio

当 Runner 检测到目标是单 `.py` 文件（非文件夹）时，自动构建一个等效的 portfolio config：
- 1 个策略实例
- 1 个品种（CLI `--symbol` 参数）
- 0 个 Actor
- CLI 用法完全不变

## 统一 Portfolio 加载器（回测 / 模拟盘 / 实盘共享）

### 设计原则

**回测、模拟盘（sandbox）、实盘（live）使用完全相同的组合配置和加载逻辑。**
策略代码和 Actor 代码在三种模式下零改动，只有底层引擎和数据源不同。
这是 NT 的核心设计哲学，我们的 portfolio 层必须遵循。

### 当前问题

```
回测 Runner:    用 importlib 手动加载策略
Sandbox 节点:   用 ImportableStrategyConfig.create() 加载策略
Live 节点:      同 Sandbox

两边加载逻辑不统一，都不支持 Actor，都不支持 portfolio.yaml。
```

### 目标：共享 portfolio_loader 模块

```
portfolio_loader.py (新增，共享模块)
  ├─ load_portfolio_config(name_or_path) → PortfolioConfig
  │     - 单 .py → 自动包装为隐式 PortfolioConfig
  │     - 文件夹 → 读 portfolio.yaml
  │
  ├─ create_strategies(config) → list[Strategy]
  │     - 每个 symbol 创建一个独立策略实例
  │     - 统一的 importlib 加载 + 参数注入
  │
  └─ create_actors(config) → list[Actor]
        - 从 ~/.tino/actors/ 或组合内部加载
        - 无 Actor 配置时返回空列表
```

### 三种模式统一调用

```python
# 加载组合配置（三种模式共享）
portfolio_config = load_portfolio_config("crypto_momentum")
strategies = create_strategies(portfolio_config)
actors = create_actors(portfolio_config)

# ── 回测 ─────────────────────────────────
engine = BacktestEngine(config=...)
engine.add_venue(...)
# ... 加载数据 ...
for s in strategies:
    engine.add_strategy(s)
for a in actors:
    engine.add_actor(a)
engine.run()

# ── 模拟盘 / 实盘 ────────────────────────
node = TradingNode(config=...)
for s in strategies:
    node.trader.add_strategy(s)
for a in actors:
    node.trader.add_actor(a)
node.run()
```

### 架构图

```
                    portfolio.yaml
                         │
                ┌────────┴────────┐
                │ portfolio_loader │  ← 共享模块
                │                 │
                │ load_config()   │
                │ create_strats() │
                │ create_actors() │
                └───┬─────────┬───┘
                    │         │
         ┌──────────┘         └──────────┐
         │                               │
  ┌──────┴──────┐                 ┌──────┴──────┐
  │ Backtest    │                 │ Sandbox /   │
  │ Runner      │                 │ Live Node   │
  │             │                 │             │
  │ BacktestEngine                │ TradingNode │
  │ + TestClock │                 │ + LiveClock │
  │ + Parquet   │                 │ + Binance   │
  └─────────────┘                 └─────────────┘

  策略代码 / Actor 代码 / portfolio.yaml → 三种模式完全相同
  只有引擎类型、时钟、数据源不同
```

## Actor 通信机制

遵循 NT 原生 msgbus 架构，不使用自定义 cache key 或其他 hack：

```
┌──────────────┐     msgbus.publish()     ┌──────────────┐
│ RiskGuard    │ ───────────────────────→  │  Strategy    │
│ Actor        │  topic: risk.guard.state  │  (可选订阅)   │
│              │  msg: "reduce_only"       │              │
└──────────────┘                          └──────────────┘
                                          ┌──────────────┐
                                          │  Strategy    │
                                          │  (未订阅)     │ ← 不受影响
                                          └──────────────┘
```

- Actor 通过 `self.msgbus.publish()` 发布风控状态
- 策略通过 `self.msgbus.subscribe()` 订阅（可选）
- 未订阅的策略不受影响
- 这完全遵循 NT 的消息总线设计

> **注意**：Actor 不能直接调用 `set_trading_state()`，该方法属于 RiskEngine，
> 不在 Actor/Strategy 的 API 上。需要通过 msgbus 间接通信。
> 如果未来 NT 版本暴露了这个能力，可以迁移。

## 日边界与时间处理

### 回测时钟

NT 回测使用 `TestClock`，timer 基于模拟时间（bar timestamp）触发，不是 wall clock。
`self.clock.set_timer()` 在回测中完全可用。

### 日边界定义

**加密市场标准：UTC 00:00。** 这与 Binance 等主流交易所的日 K 线边界一致。

实现方式：Actor 订阅 bar 数据，比较 `bar.ts_event` 的 UTC 日期。
当日期变化时触发 `_on_new_trading_day()`，重置日 PnL。

### High-Water Mark (HWM) 策略

| 场景 | HWM 行为 |
|------|---------|
| 回测 | per-run 追踪，从 starting_balance 开始，单次运行结束即丢弃 |
| Live | 持久化到 DB，重启时加载上次 peak（未来实现） |

这与 QuantConnect 的 `MaximumDrawdownPercentPortfolio` 和机构实践一致。

## 熔断行为（breach_action）

可配置的三级熔断：

| 值 | 行为 | 适用场景 |
|----|------|---------|
| `reduce_only` | 只允许减仓，阻止新开仓 | **默认**，最安全，保留现有止盈止损 |
| `halt_new` | 阻止新开仓，现有仓位自然退出 | 中等保守 |
| `flatten_all` | 立即平掉所有仓位 | 紧急情况、极端尾部风险 |

这遵循 NT RiskEngine 内置的三状态模型（ACTIVE/REDUCING/HALTED）的理念，
但通过 msgbus 实现，而不是直接调用 RiskEngine API。

## 为什么是 Actor 而不是 Controller

- Actor 够用：有 cache / portfolio / clock / msgbus，能监听事件、发消息
- Controller 是更重量级的，用于动态创建/销毁策略，我们不需要这个能力
- Actor 可以多个共存，Controller 只能有一个
- Actor 完全可选——不配置也不影响策略运行

## 为什么不是单策略多品种

当前多因子的因子计算是单品种独立的（momentum/volatility/mean_reversion/trend 都只看自身K线），
不需要跨品种信号依赖。组合回测（多实例 + Actor）是更自然的拆分。

如果未来有跨品种信号（BTC dominance、品种间相对强弱、相关性对冲），
可以考虑写一个 SignalActor 计算跨品种因子，publish 给各策略消费。

## BridgeActor 与 RiskGuardActor 的关系

两者完全独立，职责不同：

| Actor | 职责 | 使用场景 |
|-------|------|---------|
| BridgeActor | NT 事件桥接到 Redis PubSub（fills/positions/bars） | Live/Sandbox 节点 |
| RiskGuardActor | 跨品种组合级风控（敞口/仓位/回撤/日PnL） | 回测 + Live |

BridgeActor 已有代码但未接入节点（sandbox/live 用内联 thread 代替），这是遗留问题。
两个 Actor 可以在同一个引擎中共存。

## 文件结构设计

### 目录布局

```
~/.tino/
  strategies/
    # 单文件策略（向后兼容，自动包装为隐式 Portfolio）
    simple_ma_cross.py

    # 组合策略（文件夹形式，显式 Portfolio）
    crypto_momentum/
      portfolio.yaml          # 组合定义：品种、参数、风控
      strategy.py             # Strategy 类 + Config 类
      factors.py              # 因子计算（共享模块）
      indicators.py           # 自定义指标（可选）

  actors/                     # Actor 独立目录，全局复用
    risk_guard.py             # 跨策略风控 Actor
```

### 设计原则

- **一切皆 Portfolio**：单 .py 和文件夹在 Runner 内部走同一条路径
- **Actor 完全可选**：portfolio.yaml 可以不配 actors，策略独立运行
- **Actor 独立于策略**：存放在 `~/.tino/actors/`，任何 portfolio 都能引用
- **portfolio.yaml = docker-compose.yml**：声明式组装，策略/Actor 是镜像，params 是环境变量
- **文件系统是 source of truth**：DB strategies 表只是 rescan 缓存

### portfolio.yaml 格式

```yaml
name: crypto_momentum
description: Multi-factor momentum portfolio across major cryptos

# 策略定义
strategy:
  class: strategy:BTCMultiFactor        # 文件名:类名
  config_class: strategy:BTCMultiFactorConfig
  symbols:
    - BTCUSDT-PERP
    - ETHUSDT-PERP
    - XRPUSDT-PERP
  interval: 5m
  params:
    fractional_kelly: 0.28
    kelly_cap: 0.09
    entry_edge_min: 1.08
    stop_atr_mult: 1.15
    max_positions: 3          # 单品种内的仓位上限

# Actor 引用（从 ~/.tino/actors/ 加载，完全可选）
actors:
  - name: risk_guard                  # → ~/.tino/actors/risk_guard.py
    params:
      max_total_exposure: 100000
      max_positions: 10               # 跨品种总仓位上限
      daily_stop_loss_pct: -0.02
      max_drawdown_pct: -0.09
      breach_action: reduce_only      # reduce_only | halt_new | flatten_all

  # 也支持引用组合内部的专属 Actor
  # - class: ./custom_monitor:MyMonitor
  #   params: { ... }

# 账户
account:
  starting_balance: 10000
  currency: USDT
  leverage: 10
```

### SYMBOL_PROFILES 校验

portfolio.yaml 加载时应校验：如果 `symbols` 列表中的品种在策略的 SYMBOL_PROFILES 中
没有对应 profile（或 profile.enabled == False），应输出 warning 提醒用户。
该品种会使用 `DEFAULT_PROFILE`，在当前策略中意味着不会交易。

### Actor 加载规则

- `name: risk_guard` → 从 `~/.tino/actors/risk_guard.py` 加载，找 ActorConfig 子类
- `class: ./xxx:ClassName` → 从组合文件夹内的 `xxx.py` 加载（专属 Actor）
- Actor 代码写一次，N 个 portfolio 复用
- portfolio.yaml 里的 params 覆盖 Actor Config 的默认值
- `actors: []` 或不配 actors 字段 → 无 Actor，策略独立运行

### CLI 使用方式

```bash
# 单文件策略（不变，自动包装为隐式 Portfolio）
tino backtest run simple_ma_cross --symbol BTCUSDT-PERP --interval 5m ...

# 组合策略 —— 自动读取 portfolio.yaml
tino backtest run crypto_momentum --start 2025-01-01 --end 2025-03-01

# 覆盖部分参数
tino backtest run crypto_momentum --start 2025-01-01 --end 2025-03-01 \
  --param fractional_kelly=0.35 \
  --param max_positions=8
```

## 数据持久化设计

### strategies 表（ephemeral，rescan 缓存）

```
设计决策：
- strategies 表是 rescan 的缓存，不是 source of truth
- 每次 rescan 可全量重建
- 文件系统 (~/.tino/strategies/) 才是真相
- 同时支持 single .py 和 portfolio 文件夹

Scanner 增强:
- 检测到 .py          → type="single"
- 检测到文件夹 + portfolio.yaml → type="portfolio"
- 提取策略元数据（class, config, params schema）

Scanner 修复（已知 bug）:
- 当前只检查 model_fields (Pydantic)
- 需要同时检查 __struct_fields__ (msgspec/NT)
- 与 runner.py 的逻辑保持一致
```

### backtest_runs 表

```
- 用 strategy_name (str) 关联，不用外键
- 即使 strategies 表被重建，历史回测结果不受影响
- strategy_name 对应文件名（single）或文件夹名（portfolio）
```

## 策略文件拆分（btc_multi_factor 迁移路径）

```
# 当前
~/.tino/strategies/btc_multi_factor.py  (一个大文件)

# 迁移后
~/.tino/strategies/crypto_momentum/
  portfolio.yaml    ← 组合配置
  strategy.py       ← Strategy + Config（只留交易逻辑）
  factors.py        ← 因子计算抽出

~/.tino/actors/
  risk_guard.py     ← 新建 Actor（从策略中移出的全局风控）

# 旧文件保留（向后兼容，单币种回测还能用）
~/.tino/strategies/btc_multi_factor.py
```

### 关于因子缩放风控门（disable_risk_gates）

策略中的 `disable_risk_gates` 相关逻辑（weekly_scale, streak_scale, zone_scale,
kelly_boost 等因子缩放）是独立于 RiskGuardActor 的策略内部风控机制。
这套逻辑在策略迁移时保留在策略内部，不移到 Actor。
详细说明见 `docs/design/risk-gates.md`。

## Actor 规划

| Actor | 职责 | 优先级 |
|-------|------|--------|
| **risk_guard.py** | 跨品种风控、熔断、总敞口/仓位/回撤 | **现在就要** |
| equity_recorder.py | 记录权益曲线数据点（回测报告画图用） | 高 |
| alert_actor.py | 实时通知（Telegram/Discord），触发条件可配 | live 时 |
| position_logger.py | 记录所有仓位变动到文件/DB，审计用 | live 时 |

注意：跨品种信号类（相关性、BTC dominance、相对强弱）不需要单独 Actor，
除非未来有策略间信号依赖，到时候再加。不要过度设计。

## 业界参考

| 决策 | 依据 |
|------|------|
| 日边界 UTC 00:00 | Binance 及所有主流加密交易所标准 |
| HWM 回测 per-run | QuantConnect MaxDrawdownPercentPortfolio 同理 |
| HWM live 持久化 | 机构标准，IBKR 等 |
| 三级熔断可配 | NT RiskEngine 三状态 + QuantConnect 风控模型 |
| Actor 可选 | QuantConnect 风控模块可选，NT RiskEngine 可 bypass |
| 文件系统为真相 | Jesse、Zipline、NT 均无策略 DB 表 |
| msgbus 通信 | NT 原生消息总线架构 |

## 状态

- [x] 设计确认
- [ ] 创建 `docs/design/risk-gates.md`（因子缩放风控门独立文档）
- [ ] 修复 Scanner msgspec 兼容 (`__struct_fields__`)
- [ ] 实现 RiskGuardActor
- [ ] Runner 重构为 Portfolio 模式（多实例循环 + 可选 Actor）
- [ ] Scanner 支持文件夹检测 + type 字段
- [ ] strategies 表改为 ephemeral + backtest_runs 去 FK
- [ ] 策略代码拆分迁移
- [ ] 端到端测试（多币种组合回测）
