# Factor 模块

> See also: `.cage/tasks/2026-04-26-factor-framework-rebuild/2-research.md` §1.1 for factor catalogue,
> `3-tech-design.md` §3.1–3.3 for architecture decisions, `signal.md` for downstream signal integration.

## 1. 概览

`tinohelm.factor` 是因子框架的核心层，提供从声明式 `@factor` 装饰器到 Registry 扫描、DataLayer 数据加载、evaluation 评估的完整生命周期管理。

每个因子函数是一个 **wide-panel 变换**：接收 `(T × N)` 的 `polars.DataFrame`（第一列为 `ts`，其余 N 列为 symbol），输出相同 shape 的因子得分 panel。

设计原则：

- 因子函数本身保持**纯粹（pure）**，不持有状态；
- 所有元数据（lookback、类别、code_hash）附加到 `__factor_spec__` 属性；
- Registry 扫描支持**增量热加载**：文件 code_hash 未变则复用缓存；
- 实验性/废弃因子通过 `experimental` / `deprecated` 标志分级管控；
- 全模块**无 pandas import**（AC-6.1.1 grep 检查零命中）。

---

## 2. @factor 装饰器契约

### 2.1 FactorSpec 字段

`FactorSpec` 是因子的单一数据源（frozen dataclass），包含以下字段：

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `name` | `str` | — | 唯一因子标识，与函数名一致 |
| `category` | `str` | — | 语义类别标签（如 `"动量"`、`"波动"`、`"量价"`） |
| `description` | `str` | `""` | 人可读描述 |
| `lookback` | `int` | `1` | 最终 lookback 窗口（≥ 1）；= base + ShiftDetector 探测到的 shift |
| `input_specs` | `tuple[InputSpec, ...]` | `()` | 每个数据输入字段的规格 |
| `output_spec` | `OutputSpec` | `OutputSpec()` | 输出信号规格（dtype、value_range） |
| `params` | `dict[str, Any]` | `{}` | 默认参数字典（如 `{"lookback": 20}`） |
| `version` | `str` | `"1.0.0"` | 语义版本，逻辑变更时递增 |
| `code_hash` | `str` | `""` | 函数源码的 SHA-256 hex digest |
| `needs_backend` | `bool` | `False` | 是否需要注入 AbstractBackend |
| `experimental` | `bool` | `False` | 依赖 DataLayer 未支持的数据源 |
| `deprecated` | `bool` | `False` | 正在退役，向后兼容保留 |
| `signal_compatible` | `bool` | `True` | 是否可作为 SignalKernel 的输入 |

### 2.2 InputSpec 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `field_name` | `str` | 经 `resolve_alias` 后的规范字段名（如 `"close"`、`"volume"`） |
| `frequency` | `str | None` | bar 频率或 `"tick"` / `"8h"` |
| `dtype` | `str` | 期望 dtype（默认 `"float64"`） |
| `required` | `bool` | 是否必填 |

### 2.3 code_hash 计算

`code_hash = sha256(inspect.getsource(func).encode("utf-8")).hexdigest()`

**同源码两次 import → hash 一致；改一行 → hash 变化。**

Registry 的增量扫描正是依赖 hash 判断文件是否变动，变动则重新加载模块。

源码：`src/tinohelm/factor/decorator.py`

### 2.4 lookback 派生规则

```
final_lookback = max(base_lookback + ShiftDetector.detect_max_shift(func), 1)
```

`ShiftDetector` 通过 AST 分析函数源码中的 `.shift(n)` / `.diff(n)` 调用，自动追加到 base lookback。装饰器是**保守的**：只增加不减少。

### 2.5 代码示例（可运行）

```python
import polars as pl
from tinohelm.factor.decorator import factor
from tinohelm.factor.types import Panel

@factor(
    category="动量",
    lookback=5,
    params={"lookback": 5},
    description="5 周期收益率",
    signal_compatible=True,
)
def my_ret_5(close: Panel, params=None) -> Panel:
    """5-period return."""
    n = (params or {}).get("lookback", 5)
    cols = [c for c in close.columns if c != "ts"]
    return close.with_columns([
        pl.col(c).pct_change(n).alias(c) for c in cols
    ])

# 验证 spec 附加
spec = my_ret_5.__factor_spec__
print(spec.name)        # "my_ret_5"
print(spec.lookback)    # 5（或 5 + shift_detected）
print(spec.category)    # "动量"
print(spec.code_hash)   # 64 char hex
print(spec.signal_compatible)  # True

# 正常调用
ts_col = [1000, 2000, 3000, 4000, 5000, 6000, 7000]
close_data = {"ts": ts_col, "BTC": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0]}
panel = pl.DataFrame(close_data)
result = my_ret_5(panel, params={"lookback": 5})
print(result.tail(2))
```

---

## 3. Registry 扫描机制

`src/tinohelm/factor/registry.py`

### 3.1 扫描顺序

1. **Built-in factors**：`importlib.import_module("tinohelm.factor.builtins")` + 遍历子模块（`pkgutil.iter_modules`），收集所有带 `__factor_spec__` 属性的 callable。
2. **User factors**：遍历 `user_dir`（默认 `paths.get("factors_dir")` → `~/.tino/research/factors/`）下所有 `.py` 文件，通过安全 importlib 加载。
3. **合并 / 优先级**：User factors **覆盖**同名 built-in。

### 3.2 增量 hash 机制

```
_spec_cache: dict[str, (code_hash, FactorSpec)]
_kernel_cache: dict[str, Callable]
```

每次 `scan()` 时：
- 对每个 `.py` 文件计算 `sha256(file_bytes)`；
- 如果该 hash 已在 `_spec_cache` 中有条目（以 `"user:{path}:{hash}"` 为 key）→ **跳过重新加载**；
- Hash 变化 → 重新 import + 更新缓存；
- 扫描结束后剔除不再存在的 stale 条目。

```python
from tinohelm.factor.registry import Registry

registry = Registry()
specs = registry.scan()                    # 返回 {name: FactorSpec}
spec = registry.get_spec("ret_N")         # 单个 FactorSpec 查询
kernel = registry.get_kernel("ret_N")     # 获取可调用 kernel 函数
all_specs = registry.get_all_specs()      # 全量 FactorSpec 列表
```

### 3.3 自定义 user_dir

```python
from pathlib import Path
from tinohelm.factor.registry import Registry

# 自定义用户因子目录（测试时常用）
registry = Registry(user_dir=Path("/tmp/my_factors"))
specs = registry.scan()
```

### 3.4 Alias 解析

参数名通过 `tinohelm.factor.alias.resolve_alias` 规范化：

| 输入别名 | 规范 field_name |
|----------|-----------------|
| `close_price` / `price` | `close` |
| `high_price` | `high` |
| `low_price` | `low` |
| `vol` / `qty` | `volume` |
| `funding` | `funding_rate` |

---

## 4. 12 内置因子目录

所有内置因子文件路径前缀：`src/tinohelm/factor/builtins/`

### 4.1 完整因子表

| 因子名 | 文件 | lookback | 类别 | deprecated | signal_compatible |
|--------|------|----------|------|-----------|-------------------|
| `ret_N` | `momentum.py` | 20（可覆盖） | 动量 | False | True |
| `rsi_signal` | `momentum.py` | 14（可覆盖） | 动量 | False | True |
| `parkinson_vol` | `volatility.py` | 20 | 波动 | False | True |
| `vol_ratio` | `volatility.py` | 20（slow） | 波动 | False | True |
| `obv_slope` | `volume.py` | 20 | 成交量 | False | True |
| `vwap_dev` | `volume.py` | 20 | 成交量 | False | True |
| `amihud_illiq` | `microstructure.py` | 20 | 微观结构 | False | True |
| `funding_rate_level` | `crypto_funding.py` | 1 | 资金费率 | False | True |
| `funding_rate_mom` | `crypto_funding.py` | 2 | 资金费率 | False | True |
| `trade_imbalance` | `microstructure.py` | 20 | 微观结构 | **True** | True |
| `oi_change` | `crypto_data.py` | 2 | 链上数据 | **True** | True |
| `orderbook_imbalance_L1` | `crypto_data.py` | 1 | 链上数据 | **True** | True |

**Active（可直接运行）**：ret_N、rsi_signal、parkinson_vol、vol_ratio、obv_slope、vwap_dev、amihud_illiq、funding_rate_level、funding_rate_mom（共 9 个）

**Experimental + Deprecated（调用抛 `NotImplementedError`）**：trade_imbalance、oi_change、orderbook_imbalance_L1（共 3 个，等待 DataLayer 支持）

### 4.2 各因子详细说明

#### ret_N（动量）
```python
from tinohelm.factor.builtins.momentum import ret_N
import polars as pl

# 构建 close panel
ts = list(range(30))
panel = pl.DataFrame({"ts": ts, "BTC": [100.0 + i * 0.5 for i in range(30)]})

# N=5 期收益率（默认 lookback=20，可通过 params 覆盖）
result = ret_N(panel, params={"lookback": 5})
# 输出：panel["BTC"][t] = panel_in["BTC"][t] / panel_in["BTC"][t-5] - 1
```

公式：`close[t] / close[t-n] - 1`；前 n 行为 `null`。

PIT 注意：`params["lookback"]` 覆盖 spec.params 中的默认值，不影响 Registry 中记录的 spec.lookback（总是 ≥ 1）。

#### rsi_signal（动量）

公式：`RSI - 50`，居中后的 RSI。内部步骤：
```
delta = close.diff()
gain  = delta.clip(lower=0).rolling(n).mean()
loss  = (-delta.clip(upper=0)).rolling(n).mean()
rs    = gain / (loss + 1e-12)
rsi   = 100 - 100 / (1 + rs)
signal = rsi - 50
```

输出范围：`[-50, +50]`；`0` = RSI 恰好 50（中性）。

#### parkinson_vol（波动）

公式：`sqrt((log(high/low)^2).rolling(n).mean() / (4 * log(2)))`

需要 `high` 和 `low` 两个输入 panel。在 Registry 中 `input_specs` 会解析为两个 `InputSpec`（field_name=`"high"` 和 field_name=`"low"`）。

```python
from tinohelm.factor.builtins.volatility import parkinson_vol

# high 和 low 均为同 schema 的 wide panel
result = parkinson_vol(high_panel, low_panel, params={"lookback": 20})
```

#### vol_ratio（波动）

公式：`vol_fast / (vol_slow + 1e-12)`，其中 `vol_x = close.pct_change().rolling(x).std()`

参数：`{"fast": 5, "slow": 20}`。慢窗口决定了 lookback。

#### obv_slope（成交量）

公式：`(direction * volume).cumsum().diff(n) / n`

其中 `direction = sign(close.diff())`；OBV 斜率表示成交量趋势动量。

需要 `close` 和 `volume` 两个输入。

#### vwap_dev（成交量）

公式：`(close - vwap) / (vwap + 1e-12)`

其中 `tp = (high + low + close) / 3`，`vwap = (tp * volume).rolling(n).sum() / (volume.rolling(n).sum() + 1e-12)`

需要 `high`、`low`、`close`、`volume` 四个输入。

#### amihud_illiq（微观结构）

公式：`(|close.pct_change()| / (close * volume + 1e-12)).rolling(n).mean()`

Amihud 非流动性：高 illiq = 单位成交量引起的价格冲击大。作为做多信号时 `direction="down"`（高 illiq 通常负信号）。

#### funding_rate_level（资金费率）

Pass-through identity 因子：直接返回 `funding_rate` panel 的 clone。DataLayer 负责将资金费率对齐到 bar 时间轴。

PIT 注意：资金费率每 8h 结算一次，DataLayer 做前向填充时不能使用未来值；forward-fill 必须使用当前及过去数据。

#### funding_rate_mom（资金费率）

公式：`funding_rate.diff(n)`；一阶差分动量。

#### trade_imbalance（微观结构，experimental + deprecated）

等待 `trade_tick` DataLayer 支持。调用时抛 `NotImplementedError`。

预期公式（未来）：`(buy_qty - sell_qty).rolling(n).sum() / total_qty.rolling(n).sum()`

#### oi_change（链上数据，experimental + deprecated）

等待 `open_interest` DataLayer 支持。预期公式：`open_interest.pct_change(n)`

#### orderbook_imbalance_L1（链上数据，experimental + deprecated）

等待 `quote_tick` DataLayer 支持。预期公式：`(bid_vol - ask_vol) / (bid_vol + ask_vol)` ∈ [-1, 1]

---

## 5. Schema 引用

### 5.1 DB 表 `factor_runs`（migration 011 + 012）

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | `String(36)` | UUID primary key |
| `factor_name` | `String(255)` | 因子名称（不含版本） |
| `status` | `String(20)` | `queued / running / completed / failed` |
| `config` | `JSON` | `EvalConfig` 快照（universe、start、end 等） |
| `result` | `JSON` | `EvalResult` 字段（ic_mean、ir、turnover 等） |
| `progress` | `Integer` | 0–100 进度百分比 |
| `progress_stage` | `String(40)` | `aligning / computing / evaluating / persisting` |
| `error` | `Text` | 失败时的错误信息 |
| `created_at` | `DateTime` | 任务创建时间（UTC naive） |
| `started_at` | `DateTime` | 任务启动时间 |
| `finished_at` | `DateTime` | 任务完成时间 |
| `code_hash` | `String(64)` | 运行时因子函数的 SHA-256 hash |
| `baseline_id` | `String(36)` | 对比基准 factor_run 的 UUID（可 null） |
| `oos_ic_series` | `JSON` | Walk-forward OOS IC 序列 |
| `neutralization_config` | `JSON` | 中性化配置记录 |
| `universe_id` | `Integer` | FK → `universes.id`（可 null） |
| `signal_spec_id` | `String(36)` | 关联 signal spec UUID（可 null） |
| `segment_results` | `JSON` | 分段评估结果（btc_trend / vol_regime 等） |

### 5.2 FactorSpec 内存 schema

```python
from tinohelm.factor.types import FactorSpec, InputSpec, OutputSpec

spec = FactorSpec(
    name="ret_N",
    category="动量",
    description="N 周期收益率",
    lookback=20,
    input_specs=(InputSpec(field_name="close"),),
    output_spec=OutputSpec(dtype="float64", value_range=(-0.5, 0.5)),
    params={"lookback": 20},
    version="1.0.0",
    code_hash="abc123...",
    experimental=False,
    deprecated=False,
    signal_compatible=True,
)
```

### 5.3 `/api/factor/list` 过滤规则

- 默认：`experimental=False AND deprecated=False`
- `?include_experimental=true`：包含 experimental 但仍排除 deprecated
- `signal_compatible=False` 的因子不出现在 signal 选择器中

---

## 6. 完整可运行示例

```python
"""
可运行示例：9 个 active 因子的全量扫描和计算。
运行前提：pip install tinohelm polars
"""
import polars as pl
import numpy as np
from datetime import datetime, timedelta

from tinohelm.factor.builtins.momentum import ret_N, rsi_signal
from tinohelm.factor.builtins.volatility import parkinson_vol, vol_ratio
from tinohelm.factor.builtins.volume import obv_slope, vwap_dev
from tinohelm.factor.builtins.microstructure import amihud_illiq
from tinohelm.factor.builtins.crypto_funding import funding_rate_level, funding_rate_mom

# 构造合成 panel（T=50, N=3）
np.random.seed(42)
T, N = 50, 3
syms = ["BTC", "ETH", "BNB"]
ts = [datetime(2025, 1, 1) + timedelta(hours=i) for i in range(T)]
close_data = 100 + np.cumsum(np.random.randn(T, N) * 0.5, axis=0)
high_data = close_data * 1.002
low_data = close_data * 0.998
volume_data = np.random.uniform(1000, 5000, (T, N))
funding_data = np.random.uniform(-0.001, 0.001, (T, N))

close = pl.DataFrame({"ts": ts, **{s: close_data[:, i].tolist() for i, s in enumerate(syms)}})
high = pl.DataFrame({"ts": ts, **{s: high_data[:, i].tolist() for i, s in enumerate(syms)}})
low = pl.DataFrame({"ts": ts, **{s: low_data[:, i].tolist() for i, s in enumerate(syms)}})
volume = pl.DataFrame({"ts": ts, **{s: volume_data[:, i].tolist() for i, s in enumerate(syms)}})
funding = pl.DataFrame({"ts": ts, **{s: funding_data[:, i].tolist() for i, s in enumerate(syms)}})

# 9 个 active 因子计算
factors = {
    "ret_N": ret_N(close, params={"lookback": 5}),
    "rsi_signal": rsi_signal(close, params={"lookback": 14}),
    "parkinson_vol": parkinson_vol(high, low, params={"lookback": 20}),
    "vol_ratio": vol_ratio(close, params={"fast": 5, "slow": 20}),
    "obv_slope": obv_slope(close, volume, params={"lookback": 20}),
    "vwap_dev": vwap_dev(high, low, close, volume, params={"lookback": 20}),
    "amihud_illiq": amihud_illiq(close, volume, params={"lookback": 20}),
    "funding_rate_level": funding_rate_level(funding),
    "funding_rate_mom": funding_rate_mom(funding, params={"lookback": 1}),
}

for name, panel in factors.items():
    assert panel.shape == (T, N + 1), f"{name} shape mismatch"
    assert "ts" in panel.columns
    print(f"{name}: shape={panel.shape}, last_row={panel.tail(1).to_dicts()}")
```
