# NT Adapter 模块

> See also: `signal.md` 第 2.1 节（extra_warmup_bars），`factor.md` 第 2.3 节（code_hash），
> `CLAUDE.md` §NT API Gotchas，NT 文档 `nautilustrader_complete_guide.md`。

## 1. 概览

`tinohelm.nt_adapter` 将研究层的 `SignalSpec` 接入 NautilusTrader 实盘/回测引擎。

三个文件：

| 文件 | 职责 |
|------|------|
| `signal_driven_strategy.py` | 通用 NT Strategy 类；派发因子计算、kernel 调用、订单提交 |
| `bar_synchronizer.py` | 多 symbol 横截面等待门控；持有 `{ts_ns: {symbol: Bar}}` buffer |
| `order_manager.py` | 权重差分 → NT MarketOrder 提交；enforces `instrument.make_qty()` |

**NT API 不变量**（来自 `CLAUDE.md` Pitfalls）：

- `__init__` 中**不访问** `self.clock` / `self.log`；只在 `on_start` 及之后调用；
- `self.subscribe_bars(bar_type)` 必须在 `on_start` 中显式调用，否则 `on_bar` 不触发；
- 所有下单数量经 `instrument.make_qty(...)` 生成（见 `order_manager.py`）；
- 持仓查询用 `portfolio.net_position(instrument_id)`（统一支持 HEDGING / NETTING OMS）；
- `bar.ts_init` 是 bar 的收盘时间戳（NT 约定），`BarSynchronizer` 使用 `ts_init` 排序。

---

## 2. SignalDrivenStrategyConfig

msgspec `StrategyConfig`（frozen Struct，不是 Pydantic）：

```python
from tinohelm.nt_adapter.signal_driven_strategy import SignalDrivenStrategyConfig

config = SignalDrivenStrategyConfig(
    strategy_id="signal_mom_top3",
    signal_name="momentum_top3_long_short",
    instrument_ids=("BTCUSDT-PERP.BINANCE", "ETHUSDT-PERP.BINANCE", "BNBUSDT-PERP.BINANCE"),
    bar_type_template="{instrument_id}-1-HOUR-LAST-EXTERNAL",
    signal_spec_json=None,          # None = 运行时从 registry 加载
    warmup_bars=0,                  # 0 = 自动派生
    rebalance_freq_ns=0,            # 0 = 每个 cross-section 都调仓
    factor_lookback=None,           # None = 从 factor registry 查找
)
```

### 2.1 字段说明

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `signal_name` | `str` | — | registry 查找 key，或 `signal_spec_json` 的名称 |
| `instrument_ids` | `tuple[str, ...]` | — | NT 格式 instrument id（包含 venue 后缀） |
| `bar_type_template` | `str` | — | 含 `{instrument_id}` 占位符的 bar type 模板 |
| `signal_spec_json` | `dict | None` | `None` | 预序列化的 SignalSpec dict（/api/signal/export 注入） |
| `warmup_bars` | `int` | `0` | 显式预热期；0 = 自动派生 |
| `rebalance_freq_ns` | `int` | `0` | 最小调仓间隔（ns）；0 = 无限制 |
| `factor_lookback` | `int | None` | `None` | 显式因子 lookback；None = 从 factor registry 查找 |

> **`instrument_ids` / `bar_type_template` 的来源**：这两个字段由
> `POST /api/signal/run` 在 PIT 时点解析 universe 后写入 `signal_runs.config`
> （见 `signal.md` §9.5），`GET /api/signal/export/{run_id}` 再透传到
> `SignalDrivenStrategyConfig`。空的 `instrument_ids` 会在 export 端被拦截
> （`HTTP 400`），避免在 `BarSynchronizer.__init__` 里抛
> `expected_symbols is empty`。

### 2.2 msgspec Struct 注意事项

`StrategyConfig` 继承自 msgspec `Struct`（NT 使用），**不是** Pydantic model：

- 使用 `__struct_fields__`（元组，非 dict）访问字段名；
- 用 `strategy/utils.py` 的 `get_config_fields()` 辅助访问（项目约定）；
- frozen=True → 实例化后不可修改。

---

## 3. SignalDrivenStrategy 类设计

```
SignalDrivenStrategy(Strategy)
├── __init__           ← 只存储 config，不访问 self.log/self.clock
├── on_start           ← 加载 spec → 派生 warmup → 验证 cache → 订阅 bars
├── on_bar             ← 转发给 BarSynchronizer
├── _on_cross_section_ready ← kernel + diff + submit
├── on_save            ← 序列化 target_weights + last_rebalance_ts_ns
├── on_load            ← 还原上述状态
└── on_order_rejected  ← 记录日志，不停策略
```

---

## 4. on_start 流程详解

```python
def on_start(self) -> None:
    # Step 1: 加载 SignalSpec
    #   signal_spec_json 非 None → 从 dict 反序列化
    #   否则 → SignalRegistry().scan().get_spec(signal_name)
    self.signal_spec = self._resolve_signal_spec()

    # Step 2: kernel 派发
    #   _KERNEL_DISPATCH = {"top_k_long_short": top_k_long_short, ...}
    self._kernel = _KERNEL_DISPATCH[self.signal_spec.method]

    # Step 3: BarType / InstrumentId 缓存
    #   symbol_short = "BTCUSDT-PERP"（去掉 .BINANCE 后缀）
    for inst_str in self._instrument_id_strs:
        bar_type = BarType.from_str(self._bar_type_template.format(instrument_id=inst_str))
        self._bar_types[symbol_short] = bar_type
        self._instruments_by_short_symbol[symbol_short] = self.cache.instrument(inst_id)

    # Step 4: 派生 warmup，验证 cache 历史
    derived_warmup = factor_spec.lookback + spec.extra_warmup_bars
    effective_warmup = max(configured_warmup, derived_warmup)
    self._enforce_warmup()  # 如 cache.bars(bt) < warmup → RuntimeError

    # Step 5: BarSynchronizer + OrderManager
    self._bar_synchronizer = BarSynchronizer(
        BarSynchronizerConfig(expected_symbols=symbols_short, max_wait_bars=5),
        on_complete=self._on_cross_section_ready,
    )
    self._order_manager = OrderManager(self)

    # Step 6: 订阅所有 bar types
    for bar_type in self._bar_types.values():
        self.subscribe_bars(bar_type)  # 必须：否则 on_bar 不触发
```

### 4.1 warmup_bars 派生规则

```
derived_warmup = FactorSpec(name=factor_ref.split("@")[0]).lookback + extra_warmup_bars
effective_warmup = max(config.warmup_bars or 0, derived_warmup)
```

如果 `config.warmup_bars` 非 0 且**小于** `derived_warmup`，抛 `RuntimeError`（拒绝 warmup 不足的启动）。

`factor_lookback` 显式设置时跳过 registry 查找（export 端点用此方式提速）。

---

## 5. on_bar → BarSynchronizer → _on_cross_section_ready

```python
def on_bar(self, bar: Bar) -> None:
    if self._bar_synchronizer is None:
        return
    self._bar_synchronizer.on_bar(bar)
```

### 5.1 BarSynchronizer 多 symbol 横截面门控

```
on_bar(bar) 调用：
  symbol = bar.bar_type.instrument_id.symbol.value（或 str）
  ts_ns  = bar.ts_init

  buffer[ts_ns][symbol] = bar

  if expected_symbols ⊆ buffer[ts_ns].keys():
      completed = buffer.pop(ts_ns)
      on_complete(ts_ns, completed)  # 触发 _on_cross_section_ready

  _evict_stale()  # 清除被 max_wait_bars 个更新 ts 超越的旧 ts
```

**max_wait_bars=5** 意味着：如果某个 ts 有 5 个更新的 ts 已到达，则无论该 ts 是否完整，均丢弃（记录 warning）并跳过该 cross-section。

### 5.2 _on_cross_section_ready 流程

```python
def _on_cross_section_ready(self, ts_ns: int, bars: dict[str, Bar]) -> None:
    # 1. rebalance_freq_ns 门控
    if elapsed < self._rebalance_freq_ns:
        return

    # 2. 因子 panel 计算
    factor_panel = self._compute_factor_panel(ts_ns, bars)
    # 默认实现（自 PR #140 起）：从 self.cache.bars(bar_type) 取最近
    # effective_warmup 根 bar，构造 polars 宽表（[ts, sym1, sym2, ...]）
    # 并调用 factor 注册表中的真实 kernel。仅支持 OHLCV-only 因子；
    # 需要 funding_rate / open_interest / quote_tick / trade_tick 等
    # 非 bar 数据的因子由 /api/signal/export 端点拒绝（HTTP 400），
    # 或由用户提供自定义 strategy_class 子类覆写本方法。
    # 异常处理：仅捕获域错误（ValueError / KeyError / Arithmetic*），
    # 程序错误（NotImplementedError / AttributeError / TypeError）会向上
    # 抛出，便于 fail-fast 而非静默吞错。

    # 3. kernel 调用
    weight_panel = self._kernel(
        factor_panel,
        params=dict(self.signal_spec.method_params),
        constraints={
            "gross_exposure": self.signal_spec.gross_exposure,
            "net_exposure":   self.signal_spec.net_exposure,
            "max_position":   self.signal_spec.max_position,
        },
    )

    # 4. 取最新一行权重
    new_weights = self._extract_latest_weights(weight_panel)

    # 5. 差分提交
    self._submit_diff(new_weights, bars)

    # 6. 更新状态
    self.target_weights = new_weights
    self.last_rebalance_ts_ns = ts_ns
```

---

## 6. OrderManager.execute_diff

```python
# src/tinohelm/nt_adapter/order_manager.py

order_manager.execute_diff(
    target_weights={"BTCUSDT-PERP": 0.3, "ETHUSDT-PERP": -0.2},
    instruments={"BTCUSDT-PERP": btc_instrument, "ETHUSDT-PERP": eth_instrument},
    equity=100_000.0,  # USD
    prices={"BTCUSDT-PERP": 85000.0, "ETHUSDT-PERP": 3200.0},  # 可选，无则从 cache 取
)
```

**内部计算**：
```
target_qty = weight × equity / price
current_qty = portfolio.net_position(instrument_id)  # 统一 HEDGING/NETTING
diff_qty = target_qty - current_qty

if abs(diff_qty) < instrument.size_increment:
    skip  # 低于最小 lot size

qty = instrument.make_qty(abs(diff_qty))  # ← NT 必须使用 make_qty
side = BUY if diff_qty > 0 else SELL
order = order_factory.market(instrument_id, side, qty, TimeInForce.GTC)
submit_order(order)
```

**关键不变量（AC-4.2.1）**：所有下单量必须经过 `instrument.make_qty()`，直接构造 `Quantity` 会导致 RiskEngine 拒单。

---

## 7. on_save / on_load 序列化

```python
def on_save(self) -> dict[str, Any]:
    return {
        "target_weights": dict(self.target_weights),       # {symbol: float}
        "last_rebalance_ts_ns": int(self.last_rebalance_ts_ns),
    }

def on_load(self, state: dict[str, Any]) -> None:
    raw_weights = state.get("target_weights") or {}
    self.target_weights = {str(k): float(v) for k, v in raw_weights.items()}
    self.last_rebalance_ts_ns = int(state.get("last_rebalance_ts_ns", 0))
```

实时重启场景：NT 在 `on_start` 之后（或之前，取决于版本）调用 `on_load`，恢复持仓意图。

下一次 `_on_cross_section_ready` 会将 `target_weights` 与当前 portfolio 持仓 diff 并提交订单。

---

## 8. BarSynchronizer 配置参数

```python
from tinohelm.nt_adapter.bar_synchronizer import BarSynchronizer, BarSynchronizerConfig

sync = BarSynchronizer(
    BarSynchronizerConfig(
        expected_symbols=("BTCUSDT-PERP", "ETHUSDT-PERP", "BNBUSDT-PERP"),
        max_wait_bars=5,  # 超过 5 个后续 ts 到达才丢弃未完整 cross-section
    ),
    on_complete=lambda ts_ns, bars: print(f"cross-section at {ts_ns}: {list(bars)}")
)
```

`pending_timestamps()` 返回当前 buffer 中等待中的 ts 列表（调试用）：

```python
pending = sync.pending_timestamps()  # list[int]（已排序）
```

---

## 9. portfolio.yaml 示例（/api/signal/export 导出格式）

`/api/signal/export/{signal_run_id}` 生成以下 `portfolio.yaml`，可直接放入 `~/.tino/strategies/` 运行：

### 9.1 兼容性约束（自 PR #140）

* 默认 `strategy_class = "tinohelm.nt_adapter.signal_driven_strategy:SignalDrivenStrategy"`。
* 默认实现的 `_compute_factor_panel` 仅能从 NT bar cache 解析 OHLCV
  字段（`open` / `high` / `low` / `close` / `volume`）。
* 如果 signal 的因子需要非 OHLCV 字段（`funding_rate` / `open_interest` /
  `orderbook_imbalance` / `trade_qty` / `trade_side` 等），export 端点
  会以 `HTTP 400` 拒绝导出。
* 通过 query 参数 `?strategy_class=mypkg.strats:MyCustomSignalStrategy`
  传入用户自定义子类时，校验跳过——调用方负责在子类中覆写
  `_compute_factor_panel`，从合适数据源构造 panel。
* `factor_lookback` 字段必须 ≥ 该因子在 `@factor` 装饰器中声明的
  `lookback`，否则 panel 会因长度不足导致 kernel 输出全 NaN。
  推荐做法：让 `factor_lookback` 由 export 端点根据 registry 自动填充
  （这是默认行为，无需手动指定）。

### 9.2 示例

```yaml
# ~/.tino/strategies/momentum_top3/portfolio.yaml
# 由 /api/signal/export/550e8400-... 自动生成

name: momentum_top3_long_short
interval: 1h
symbols:
  - BTCUSDT-PERP
  - ETHUSDT-PERP
  - BNBUSDT-PERP
actors: []
params:
  signal_name: momentum_top3_long_short
  bar_type_template: "{instrument_id}-1-HOUR-LAST-EXTERNAL"
  warmup_bars: 30        # = factor.lookback(20) + extra_warmup_bars(10)
  rebalance_freq_ns: 0
  factor_lookback: 20   # 由 export 端点预计算，避免 live 时查 registry
  signal_spec_json:
    name: momentum_top3_long_short
    factor_ref: "ret_N@1.0.0"
    method: top_k_long_short
    weighting: equal
    rebalance_freq: "1H"
    universe_ref: top10_perp
    gross_exposure: 1.0
    net_exposure: 0.0
    max_position: 0.4
    method_params:
      k: 3
    cost_model:
      name: taker_8bps
      fee_bps_per_side: 4.0
      slippage_bps_per_side: 1.0
      rebate_bps_per_side: 0.0
    extra_warmup_bars: 10
    version: "1.0.0"
    code_hash: "abc123..."
```

---

## 10. 完整可运行示例（unit test 风格）

```python
"""
SignalDrivenStrategy 协议级测试示例（无需 NT engine）。
复用 tests/integration/test_signal_driven_strategy_e2e.py 中的 _StrategyReplay 模式。
"""
import polars as pl
import numpy as np
from datetime import datetime, timedelta

from tinohelm.signal.evaluator import SignalEvaluator
from tinohelm.signal.kernels import top_k_long_short
from tinohelm.signal.types import CostModel, SignalSpec

# 合成因子 panel（T=50, N=3）
np.random.seed(42)
T, N = 50, 3
ts_ns = [int(datetime(2024, 1, 1).timestamp() * 1e9) + i * 3600 * int(1e9) for i in range(T)]
syms = ["S00", "S01", "S02"]
factor_panel = pl.DataFrame({
    "ts": ts_ns,
    **{s: np.random.randn(T).tolist() for s in syms}
})

# 合成前向收益
close = pl.DataFrame({
    "ts": ts_ns,
    **{s: (100 + np.cumsum(np.random.randn(T) * 0.3)).tolist() for s in syms}
})
close_arr = close.select(syms).to_numpy()
fwd_data = {"ts": ts_ns}
for i, s in enumerate(syms):
    fwd = np.roll(close_arr[:, i], -1) / close_arr[:, i] - 1
    fwd[-1] = np.nan
    fwd_data[s] = fwd.tolist()
future_returns = pl.DataFrame(fwd_data)

# SignalSpec
spec = SignalSpec(
    name="test_top2",
    factor_ref="ret_N@1.0.0",
    method="top_k_long_short",
    weighting="equal",
    rebalance_freq="1H",
    universe_ref="test_uni",
    gross_exposure=1.0,
    net_exposure=0.0,
    max_position=0.5,
    method_params={"k": 1},
    cost_model=CostModel(name="taker_8bps", fee_bps_per_side=4.0, slippage_bps_per_side=1.0),
)

# 生成权重（模拟 _on_cross_section_ready 的 kernel 调用）
weight_panel = top_k_long_short(
    factor_panel,
    params=dict(spec.method_params),
    constraints={
        "gross_exposure": spec.gross_exposure,
        "net_exposure":   spec.net_exposure,
        "max_position":   spec.max_position,
    },
)

# 评估
evaluator = SignalEvaluator(periods_per_year=8760)
result = evaluator.evaluate(weight_panel, future_returns, spec.cost_model)

assert result.n_periods > 0, "应有有效期数"
assert isinstance(result.sharpe, float), "sharpe 应为 float"
print(f"Sharpe={result.sharpe:.4f}, MDD={result.mdd:.4%}, n_periods={result.n_periods}")
```

---

## 11. Schema 引用

### 11.1 signal_runs.config_json（由 /api/signal/export 写入）

与 `signal.md` 第 9.2 节相同格式，包含 `factor_lookback` 字段（warmup 加速字段，非标准 SignalSpec 字段，存于 config 外层或 portfolio.yaml params）。

### 11.2 factor_runs.progress_stage 状态机

```
queued → (worker picks up) → aligning → computing → evaluating → persisting → completed
                                                                       ↓
                                                                    failed（任意阶段失败）
```

| Stage | 含义 |
|-------|------|
| `aligning` | Aligner 正在 PIT 过滤 + OLS 中性化 |
| `computing` | factor kernel 计算中 |
| `evaluating` | IC / quantile / walk-forward 计算中 |
| `persisting` | 写入 DB + 生成报告 |

### 11.3 BarSynchronizer buffer 内存格式

```python
# 内部 buffer（defaultdict(dict)）
{
    1704067200000000000: {  # ts_ns（2024-01-01 00:00:00）
        "BTCUSDT-PERP": bar_obj_btc,
        "ETHUSDT-PERP": bar_obj_eth,
        # "BNBUSDT-PERP" 尚未到达 → cross-section 未完成
    },
    1704070800000000000: {  # ts_ns（2024-01-01 01:00:00）
        "BTCUSDT-PERP": bar_obj_btc2,
        "ETHUSDT-PERP": bar_obj_eth2,
        "BNBUSDT-PERP": bar_obj_bnb2,
        # 完整 → 触发 on_complete，弹出此 slot
    },
}
```
