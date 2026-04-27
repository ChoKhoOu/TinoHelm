# Signal 模块

> See also: `factor.md` 第 2 节（FactorSpec.signal_compatible 字段），`evaluation.md` 第 11 节（Evaluator
> 输出结构），`nt_adapter.md` 第 4 节（SignalDrivenStrategy 消费 SignalSpec），`3-tech-design.md` §3.10–3.12。

## 1. 概览

`tinohelm.signal` 将研究层的因子得分 panel 转换为可交易的组合权重 panel。架构层级：

```
FactorSpec (factor.py)
       │ factor_ref
       ▼
SignalSpec (@signal 装饰器)
       │ method + method_params + constraints
       ▼
SignalKernel (5 built-in)
       │ weight_panel (T × N)
       ▼
SignalEvaluator → SignalEvalResult
       │
       ▼
NT Adapter (SignalDrivenStrategy)
```

设计原则：

- `SignalSpec` 是**单一数据源**：驱动 kernel 调用、评估、NT adapter 预热、DB 持久化；
- 所有 5 个 kernel 是**纯函数**（无状态），共享同一签名；
- 权重约束（gross/net/max_position）由 `normalize_to_constraints` 统一执行；
- 全模块**无 pandas import**。

---

## 2. SignalSpec 字段表

`SignalSpec` 是 frozen dataclass，字段：

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `name` | `str` | — | 唯一信号标识 |
| `factor_ref` | `str` | — | `"<name>@<version>"` 格式，引用上游因子 |
| `method` | `SignalMethod` | — | 5 个 kernel 之一的 slug |
| `weighting` | `SignalWeighting` | `"equal"` | 权重缩放体系 |
| `rebalance_freq` | `str` | — | 调仓频率（如 `"1D"`、`"4H"`、`"1W"`） |
| `universe_ref` | `str` | — | Universe 名称，运行时解析到 `universes.id` |
| `gross_exposure` | `float` | `1.0` | Σ\|wᵢ\| 上限（portfolio fraction） |
| `net_exposure` | `float` | `0.0` | \|Σwᵢ\| 上限（market neutral = 0） |
| `max_position` | `float` | `0.10` | 单资产 \|wᵢ\| 上限 |
| `turnover_budget` | `float | None` | `None` | 日换手上限（未设 = 不约束） |
| `method_params` | `dict[str, Any]` | `{}` | method 特定参数（不参与 hash/compare） |
| `cost_model` | `CostModel` | `CostModel("taker_8bps")` | 费率模型 |
| `extra_warmup_bars` | `int` | `0` | 叠加在因子 lookback 上的额外预热期 |
| `version` | `str` | `"1.0.0"` | 语义版本 |
| `code_hash` | `str` | `""` | 函数源码 SHA-256 |
| `description` | `str` | `""` | 描述 |
| `deprecated` | `bool` | `False` | 是否退役 |

### 2.1 warmup_bars 派生

```
actual_warmup_bars = FactorSpec(name=factor_ref.split("@")[0]).lookback + extra_warmup_bars
```

`SignalDrivenStrategy` 在 `on_start` 时自动派生，无需手动指定（除非 export 端点预计算后注入 `factor_lookback`）。

详见 `nt_adapter.md` 第 4 节。

### 2.2 CostModel 字段

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `name` | `CostModelName` | `"taker_8bps"` | `taker_8bps / maker_2bps_with_rebate / custom` |
| `fee_bps_per_side` | `float` | `4.0` | 每侧手续费（bps） |
| `slippage_bps_per_side` | `float` | `1.0` | 每侧滑点（bps） |
| `rebate_bps_per_side` | `float` | `0.0` | 每侧返佣（bps，从成本中减去） |

总每侧成本 = `fee + slippage - rebate`；往返成本 = 2 × 每侧。

---

## 3. @signal 装饰器

与 `@factor` 装饰器镜像相同的设计：附加 `__signal_spec__` 属性，返回原始函数不变。

```python
from tinohelm.signal.decorator import signal
from tinohelm.signal.types import CostModel

@signal(
    name="momentum_top3_long_short",
    factor_ref="ret_N@1.0.0",
    method="top_k_long_short",
    weighting="equal",
    rebalance_freq="1D",
    universe_ref="top10_perp",
    method_params={"k": 3},
    gross_exposure=1.0,
    net_exposure=0.0,
    max_position=0.4,
    cost_model=CostModel(name="taker_8bps"),
    extra_warmup_bars=10,
    description="动量 top-3 多空信号",
)
def momentum_top3_kernel(factor_panel):
    """自定义 kernel（此处仅作声明用途）。"""
    # 实际逻辑可以调用 built-in kernel 或自定义实现
    from tinohelm.signal.kernels import top_k_long_short
    return top_k_long_short(factor_panel, params={"k": 3}, constraints={
        "gross_exposure": 1.0, "net_exposure": 0.0, "max_position": 0.4
    })

# 验证
spec = momentum_top3_kernel.__signal_spec__
print(spec.name)           # "momentum_top3_long_short"
print(spec.factor_ref)     # "ret_N@1.0.0"
print(spec.code_hash)      # 64 char hex digest
print(spec.extra_warmup_bars)  # 10
```

**参数校验**（构建时）：
- `gross_exposure <= 0` → `ValueError`
- `max_position <= 0` → `ValueError`
- `net_exposure < 0` → `ValueError`
- `extra_warmup_bars < 0` → `ValueError`
- `turnover_budget` 非 None 且 `<= 0` → `ValueError`

---

## 4. SignalRegistry

```python
from tinohelm.signal.registry import SignalRegistry

registry = SignalRegistry()
registry.scan()                        # 扫描 paths.get("signals_dir")
spec = registry.get_spec("my_signal") # FactorSpec 查询
kernel = registry.get_kernel("my_signal")  # 可调用 kernel
```

扫描目录：`paths.get("signals_dir")` → `~/.tino/research/signals/`

扫描行为与 factor Registry 完全一致：hash 增量、用户覆盖内置、stale 条目清理。

---

## 5. 5 个内置 SignalKernel

所有 kernel 共享签名：

```python
def kernel_name(
    factor_panel: pl.DataFrame,   # "ts" + N symbol 列
    params: dict[str, Any],       # method-specific
    constraints: dict[str, float], # gross_exposure / net_exposure / max_position
) -> pl.DataFrame:                 # 同格式权重 panel
```

### 5.1 top_k_long_short

```python
from tinohelm.signal.kernels import top_k_long_short

weight_panel = top_k_long_short(
    factor_panel,
    params={"k": 3},
    constraints={"gross_exposure": 1.0, "net_exposure": 0.0, "max_position": 0.4},
)
```

**算法**：每期选因子值最高的 k 个 long（权重 `+1/k`），最低的 k 个 short（权重 `-1/k`）。需要至少 `2k` 个有效 symbol。

适用场景：横截面动量策略基准。

### 5.2 quantile_long_short

```python
from tinohelm.signal.kernels import quantile_long_short

weight_panel = quantile_long_short(
    factor_panel,
    params={"quantiles": 5, "long_q": 4, "short_q": 0},
    constraints={"gross_exposure": 1.0, "net_exposure": 0.0, "max_position": 0.3},
)
```

**算法**：将 N symbols 按因子值分为 `quantiles` 组（Q0=最低，Q{quantiles-1}=最高）；等权 long `long_q` 组，等权 short `short_q` 组。

`long_q > short_q` 时为正向因子；反之为反向因子。

### 5.3 threshold_signed

```python
from tinohelm.signal.kernels import threshold_signed

weight_panel = threshold_signed(
    factor_panel,
    params={"upper": 0.5, "lower": -0.5, "long_weight": 1.0, "short_weight": -1.0},
    constraints={"gross_exposure": 1.0, "net_exposure": 0.0, "max_position": 0.5},
)
```

**算法**：因子值 `> upper` → 权重 `long_weight`；因子值 `< lower` → 权重 `short_weight`；中间区间 → 权重 0。

适用场景：因子值有明确阈值含义（如 RSI > 50 = long signal）。

### 5.4 zscore_clip

```python
from tinohelm.signal.kernels import zscore_clip

weight_panel = zscore_clip(
    factor_panel,
    params={"clip": 3.0},
    constraints={"gross_exposure": 1.0, "net_exposure": 0.0, "max_position": 0.5},
)
```

**算法**：对每期横截面 z-score 标准化（减均值、除标准差），然后 clip 到 `±clip`。

输出权重 = `clip(z_score, -clip, +clip) / gross_normalization_factor`

适用场景：因子分布近似正态；需要连续权重而非离散 long/short。

### 5.5 rank_to_weight

```python
from tinohelm.signal.kernels import rank_to_weight

weight_panel = rank_to_weight(
    factor_panel,
    params={"power": 1.0},
    constraints={"gross_exposure": 1.0, "net_exposure": 0.0, "max_position": 0.5},
)
```

**算法**：对每期因子值排名（百分位），经 power 函数映射后归一化为权重。

`rank_percentile = (rank - 0.5) / N`（居中百分位，∈ (0, 1)）
`weight_raw = rank_percentile ^ power` 处理后去均值、归一化

`power=1.0` = 线性；`power > 1` = 末端压缩（减少尾端权重）；`power < 1` = 末端放大。

---

## 6. normalize_to_constraints

所有 kernel 在返回前调用此函数：

```python
from tinohelm.signal.kernel import normalize_to_constraints

# 4 步执行顺序（per row）：
# 1. clip to ±max_position
# 2. net 超限 → 平移使 net 趋近 ±net_exposure
# 3. gross 超限 → 等比缩放
# 4. 再次 clip to ±max_position（步骤 2 可能超限）
```

NaN 权重（symbol 不在 universe 中）**保留为 NaN**，不被强制归零。

---

## 7. SignalEvaluator

### 7.1 初始化

```python
from tinohelm.signal.evaluator import SignalEvaluator

evaluator = SignalEvaluator(periods_per_year=8760)  # 小时 crypto
# periods_per_year=252 for daily; 252*6.5 for hourly US equity
```

### 7.2 evaluate() 方法

```python
from tinohelm.signal.types import CostModel

cost = CostModel(name="taker_8bps", fee_bps_per_side=4.0, slippage_bps_per_side=1.0)
result = evaluator.evaluate(weight_panel, future_returns, cost)
```

**输入**：
- `weight_panel`：`pl.DataFrame` `(T, N+1)`，第一列 `ts` + N symbol 权重列（含 NaN）
- `future_returns`：同格式，column 为对应的前向收益率
- `cost`：`CostModel`

**内部流程**：
1. inner-join on `ts`，取交集 symbols
2. 计算 gross period return = `Σ wᵢ × rᵢ`（nansum）
3. 计算 single-sided turnover：Period 0 = `Σ|w[0]|`；Period t = `0.5 × Σ|Δw|`
4. 扣除 cost drag：`cost_rate = (fee + slippage - rebate) / 10000`，`net = gross - turnover × cost_rate`
5. 计算 annualised Sharpe、MDD、capacity_score、tail_loss_p99

### 7.3 完整可运行示例

```python
"""SignalEvaluator 完整示例。"""
import polars as pl
import numpy as np
from datetime import datetime, timedelta

from tinohelm.factor.builtins.momentum import ret_N
from tinohelm.signal.kernels import top_k_long_short
from tinohelm.signal.evaluator import SignalEvaluator
from tinohelm.signal.types import CostModel, SignalSpec

# 1. 合成数据（T=100, N=5）
np.random.seed(42)
T, N = 100, 5
syms = [f"S{i:02d}" for i in range(N)]
ts = [datetime(2025, 1, 1) + timedelta(hours=i) for i in range(T)]
close = pl.DataFrame({
    "ts": ts,
    **{s: (100 + np.cumsum(np.random.randn(T) * 0.3)).tolist() for s in syms}
})

# 2. 因子计算
factor_panel = ret_N(close, params={"lookback": 5})

# 3. 前向收益率（close t+1 / close t - 1）
fwd_data = {"ts": ts}
close_arr = close.select(syms).to_numpy()
for i, s in enumerate(syms):
    fwd_col = np.roll(close_arr[:, i], -1) / close_arr[:, i] - 1
    fwd_col[-1] = np.nan
    fwd_data[s] = fwd_col.tolist()
future_returns = pl.DataFrame(fwd_data)

# 4. 生成权重
weight_panel = top_k_long_short(
    factor_panel,
    params={"k": 2},
    constraints={"gross_exposure": 1.0, "net_exposure": 0.0, "max_position": 0.5},
)

# 5. 评估
cost = CostModel(name="taker_8bps", fee_bps_per_side=4.0, slippage_bps_per_side=1.0)
evaluator = SignalEvaluator(periods_per_year=8760)  # 1h crypto
result = evaluator.evaluate(weight_panel, future_returns, cost)

print(f"Sharpe:    {result.sharpe:.4f}")
print(f"MDD:       {result.mdd:.4%}")
print(f"Turnover:  {result.turnover_annualized:.1f}x/yr")
print(f"Capacity:  {result.capacity_score:.4f}")
print(f"Tail P99:  {result.tail_loss_p99:.4%}")
print(f"Net PnL:   {result.total_return:.4%}")
print(f"Cost Drag: {result.cost_drag:.6f}")
print(f"Periods:   {result.n_periods}")
```

---

## 8. SignalEvalResult 字段

```python
from tinohelm.signal.evaluator import SignalEvalResult
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `sharpe` | `float` | 年化 Sharpe ratio（净收益） |
| `mdd` | `float` | 最大回撤（正值，如 `0.15` = 15%） |
| `turnover_annualized` | `float` | 年化单侧换手率 |
| `capacity_score` | `float` | 集中度代理 ∈ [0,1]（越接近 1 = 越分散） |
| `tail_loss_p99` | `float` | 1% 最差净收益（负值） |
| `net_pnl_curve` | `list[float]` | 净 PnL 累计序列（长度 = n_periods） |
| `gross_pnl_curve` | `list[float]` | 毛 PnL 累计序列 |
| `total_return` | `float` | `net_pnl_curve[-1]`（总净收益率） |
| `n_periods` | `int` | 评估期数 |
| `cost_drag` | `float` | 总费用拖累 = `gross_total - net_total` |

---

## 9. Schema 引用

### 9.1 DB 表 `signal_runs`（migration 012）

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | `String(36)` | UUID primary key |
| `signal_name` | `String(255)` | 信号名称 |
| `factor_ref` | `String(255)` | `"name@version"` 格式 |
| `status` | `String(20)` | `queued / running / completed / failed` |
| `config` | `JSON` | `SignalSpec` 快照（所有标量字段） |
| `result` | `JSON` | `SignalEvalResult` 字段（sharpe、mdd 等） |
| `progress` | `Integer` | 0–100 |
| `progress_stage` | `String(40)` | `aligning / computing / evaluating / persisting` |
| `error` | `Text` | 错误信息 |
| `created_at` | `DateTime` | UTC naive |
| `started_at` | `DateTime` | — |
| `finished_at` | `DateTime` | — |
| `code_hash` | `String(64)` | kernel 函数 SHA-256 |
| `universe_id` | `Integer` | FK → `universes.id`（可 null） |

### 9.2 `signal_runs.config` JSON 结构

```json
{
    "name": "momentum_top3_long_short",
    "factor_ref": "ret_N@1.0.0",
    "method": "top_k_long_short",
    "weighting": "equal",
    "rebalance_freq": "1D",
    "universe_ref": "top10_perp",
    "gross_exposure": 1.0,
    "net_exposure": 0.0,
    "max_position": 0.10,
    "turnover_budget": null,
    "method_params": {"k": 3},
    "cost_model": {
        "name": "taker_8bps",
        "fee_bps_per_side": 4.0,
        "slippage_bps_per_side": 1.0,
        "rebate_bps_per_side": 0.0
    },
    "extra_warmup_bars": 0,
    "version": "1.0.0",
    "code_hash": "abc123...",
    "deprecated": false,

    // Universe resolution — populated by /api/signal/run at enqueue time.
    // See §9.5 below for the contract.
    "universe_id": 42,
    "universe_symbols": ["BTCUSDT-PERP", "ETHUSDT-PERP", "SOLUSDT-PERP"],
    "instrument_ids": [
        "BTCUSDT-PERP.BINANCE",
        "ETHUSDT-PERP.BINANCE",
        "SOLUSDT-PERP.BINANCE"
    ],
    "bar_type_template": "{instrument_id}-1-DAY-LAST-EXTERNAL",

    // Request-side window
    "start": "2024-01-01",
    "end": "2024-04-01",
    "force": false
}
```

### 9.5 Universe 解析合约（`POST /api/signal/run` 入队边界）

从 PR #140 起，`POST /api/signal/run` 在入队前**必须**把一个 universe 引用解析为具体的 PIT 符号列表并写入 `signal_runs.config`。这一步由
`tinohelm.signal._run_helpers.resolve_universe_to_instrument_ids` 完成，流程：

1. **查表优先级**：`req.universe_id` > `spec.universe_ref`（按 `universes.name` 查 UNIQUE 列）。两者都查不到 → `HTTP 422`。
2. **锚定时点**：`req.end` 优先（反映窗口结束时的历史 universe 状态，避免 look-back bias），否则 `datetime.utcnow()`。
3. **PIT 过滤**：`Universe.from_db_row(row).get_symbols_at(anchor_ts)` 应用 7 天新币隔离 + 退市过滤；空结果 → `HTTP 422`。
4. **写回 config**：四个字段必须同步持久化：
   - `universe_id`（int）— 与 `SignalRun.universe_id` 列对齐；
   - `universe_symbols`（list[str]）— TinoHelm 短符号（如 `"BTCUSDT-PERP"`），供 worker 构建 `Universe.from_symbols`；
   - `instrument_ids`（list[str]）— NT 格式（`"*.BINANCE"` 后缀），供 `SignalDrivenStrategy` 消费；
   - `bar_type_template`（str，含 `{instrument_id}` 占位符）— 由 `rebalance_freq` 通过 `build_bar_type_template()` 派生。
5. **Export 端安全网**：`GET /api/signal/export/{run_id}` 在读到空 `instrument_ids` 时返回 `HTTP 400`，防止遗留数据触发 `BarSynchronizer.__init__` 的 `expected_symbols is empty` 错误。

`rebalance_freq → bar_type_template` 映射（与 `tinohelm.strategy.loader_helpers.parse_interval` 共享 `INTERVAL_MAP`，大小写不敏感）：

| rebalance_freq | bar_type_template |
|------|------|
| `"1H"` / `"1h"` | `"{instrument_id}-1-HOUR-LAST-EXTERNAL"` |
| `"4H"` / `"4h"` | `"{instrument_id}-4-HOUR-LAST-EXTERNAL"` |
| `"1D"` / `"1d"` | `"{instrument_id}-1-DAY-LAST-EXTERNAL"` |
| `"30m"` | `"{instrument_id}-30-MINUTE-LAST-EXTERNAL"` |

> **遗留数据**：PR #140 之前创建的 `SignalRun` 记录 `config` 缺 `instrument_ids`；这些行在 `/export` 上返回 400，需要重新发起 `/run` 请求。

### 9.3 SignalMethod Literal 类型

```python
from tinohelm.signal.types import SignalMethod

# 合法值：
# "top_k_long_short"
# "quantile_long_short"
# "threshold_signed"
# "zscore_clip"
# "rank_to_weight"
```

### 9.4 signal_compatible 链路

`FactorSpec.signal_compatible=False` 的因子不会出现在信号选择器中（`/api/factor/list` 过滤），避免研究-only 因子被错误配置为信号上游。详见 `factor.md` 第 5.3 节。
