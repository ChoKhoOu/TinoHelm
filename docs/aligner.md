# Aligner 模块

> See also: `factor.md` 第 3 节（因子 Panel 格式），`evaluation.md` 第 6 节（walk-forward PIT
> 约束），`3-tech-design.md` §3.5–3.6。

## 1. 概览

`tinohelm.aligner` 是因子评估管道中的中性化层，完成两件事：

1. **Universe PIT 掩码**：将因子 panel 中"在该时间点不可交易"的 cell 设为 `null`（pre-listing 隔离 + post-delisting 排除）。
2. **横截面 OLS 残差化**：对每一个时间点，以注册的 `ExposureProvider` 提供的暴露向量为回归变量，消除共同因子的线性影响。

适用场景：

- 运行 BTC-Beta 中性的动量因子，去除市场 beta 敞口；
- 运行 log-市值中性因子，隔离市值效应；
- Walk-forward 评估时，每个 fold 的因子 panel 先经 Aligner 处理，再送入 Evaluator。

---

## 2. Universe 对象

### 2.1 CSV 格式

```csv
symbol,listing_date,delisting_date
BTCUSDT-PERP,2020-01-01,
ETHUSDT-PERP,2020-01-01,
DOTUSDT-PERP,2020-09-01,2024-06-01
```

- `listing_date` — ISO-8601 日期（`YYYY-MM-DD` 或完整 datetime 均可）
- `delisting_date` — 可选，空值表示仍在交易

### 2.2 加载方式

```python
from pathlib import Path
from tinohelm.factor.universe import Universe

# 方式 1：从 CSV 文件加载（PIT 完整信息）
uni = Universe.load_csv(Path("~/.tino/research/universes/binance_top20.csv"))

# 方式 2：从 symbol 列表创建（所有 symbol 视为永久 active）
uni = Universe.from_symbols(["BTCUSDT-PERP", "ETHUSDT-PERP", "BNBUSDT-PERP"])

# 方式 3：从 DB ORM 行重建（pit_rules_json 字段）
from tinohelm.db.models import Universe as UniverseORM
uni = Universe.from_db_row(db_row)
```

### 2.3 sync_from_csv（DB 持久化）

```python
import asyncio
from pathlib import Path
from tinohelm.factor.universe import Universe
from tinohelm.db.session import get_db

async def sync_universe():
    async for db in get_db():
        universe, db_id = await Universe.sync_from_csv(
            csv_path=Path("~/.tino/research/universes/top20.csv"),
            db_session=db,
        )
        await db.commit()
        print(f"Universe DB id={db_id}, symbols={len(universe)}")
```

`sync_from_csv` 是**幂等的**：同 csv hash 只插入一条记录，重复调用返回现有行。

### 2.4 PIT 查询 API

```python
from datetime import datetime

# 单时间点查询
symbols_at = uni.get_symbols_at(datetime(2023, 6, 1))
# 返回排序后的 eligible symbol 列表

# 全量 boundary 查询（向量化 PIT 过滤）
boundaries = uni.get_symbol_boundaries()
# 返回 {symbol: (eligible_from, delisting_date)} — eligible_from = listing_date + 7d
```

---

## 3. ExposureProvider Protocol

### 3.1 Protocol 定义

```python
# src/tinohelm/aligner/exposure.py

from typing import Protocol, runtime_checkable
import polars as pl

@runtime_checkable
class ExposureProvider(Protocol):
    """PIT-safe 暴露向量提供者。"""
    name: str  # 唯一名称，用于 registry 字符串查找

    def get_exposure(
        self,
        timestamps: pl.Series,   # Datetime Series, 长度 T
        symbols: list[str],       # 长度 N
    ) -> pl.DataFrame:
        """返回宽格式 DataFrame: 第一列 ts + N 个 symbol 列，行数 = T。"""
        ...
```

**PIT 保证**：`get_exposure` 返回的所有 ts 必须 `≤ max(panel["ts"])`；否则 `Aligner.align()` 在 `_pit_check` 阶段抛出 `PITViolationError`。

### 3.2 内置 Provider

#### BTCBetaExposure

- `name = "btc_beta"`
- 计算每个 symbol 相对 BTC 的滚动 beta（OLS 回归，rolling 窗口）
- 从 `~/.tino/data/catalog/` 读取历史 bar 数据

```python
from tinohelm.aligner.exposure_btc import BTCBetaExposure

provider = BTCBetaExposure(rolling_window=60)
exposure_df = provider.get_exposure(ts_series, symbols)
# 返回：{"ts": ..., "ETHUSDT-PERP": 0.85, "BNBUSDT-PERP": 0.72, ...}
```

#### LogMcapExposure

- `name = "log_mcap"`
- 以 log(市值) 作为暴露值（代理：log(close * circulating_supply)）
- 当前实现使用 **current circulating supply snapshot**，不是 PIT-safe。
  `Aligner` 默认禁止把它用于历史 neutralization；只有明确接受
  non-PIT exploratory 语义时才传 `allow_non_pit_exposures=True`。

```python
from tinohelm.aligner.exposure_logmcap import LogMcapExposure

provider = LogMcapExposure()
exposure_df = provider.get_exposure(ts_series, symbols)
# 返回：{"ts": ..., "BTCUSDT-PERP": 25.3, "ETHUSDT-PERP": 23.8, ...}
```

### 3.3 注册自定义 Provider

```python
from tinohelm.aligner.registry import register, resolve

# 注册
my_provider = MyCustomExposure()
register("my_exposure", my_provider)

# 查找
provider = resolve("my_exposure")  # KeyError if not registered
```

---

## 4. Aligner 使用方式

### 4.1 字符串 vs 实例双入口

```python
from tinohelm.aligner.aligner import Aligner
from tinohelm.factor.universe import Universe

uni = Universe.from_symbols(["BTCUSDT-PERP", "ETHUSDT-PERP"])

# 字符串入口（registry 解析）
aligner = Aligner(uni, neutralize=["btc_beta"])

# 实例入口（直接传 ExposureProvider）
from tinohelm.aligner.exposure_btc import BTCBetaExposure
aligner = Aligner(uni, neutralize=[BTCBetaExposure(rolling_window=60)])

# 混合使用
from tinohelm.aligner.exposure_logmcap import LogMcapExposure
aligner = Aligner(
    uni,
    neutralize=["btc_beta", LogMcapExposure()],
    allow_non_pit_exposures=True,  # log_mcap 当前使用 current supply snapshot
)
```

### 4.2 align() 输入输出

```python
import polars as pl
from tinohelm.aligner.aligner import Aligner
from tinohelm.factor.universe import Universe

uni = Universe.from_symbols(["BTCUSDT-PERP", "ETHUSDT-PERP"])
aligner = Aligner(uni, neutralize=["btc_beta"])

# factor_panel: pl.DataFrame, 列：ts + N symbols
factor_panel: pl.DataFrame = ...  # 由 @factor kernel 返回

residual_panel = aligner.align(factor_panel)
# 返回：同 shape 的 panel
# - 不在 universe PIT 窗口内的 cell → null
# - 剩余 cell → OLS 残差
```

### 4.3 完整可运行示例

```python
"""Aligner 完整示例：PIT 过滤 + BTC-Beta 中性化。"""
import polars as pl
import numpy as np
from datetime import datetime, timedelta

from tinohelm.factor.universe import Universe
from tinohelm.aligner.aligner import Aligner
from tinohelm.aligner.exposure import BTCBetaExposure, LogMcapExposure
from tinohelm.factor.builtins.momentum import ret_N

# 1. 构建合成 panel
np.random.seed(42)
T, N = 60, 3
syms = ["BTC", "ETH", "BNB"]
ts = [datetime(2025, 1, 1) + timedelta(hours=i) for i in range(T)]
close = pl.DataFrame({
    "ts": ts,
    **{s: (100 + np.cumsum(np.random.randn(T) * 0.3)).tolist() for s in syms}
})

# 2. 计算因子
factor_panel = ret_N(close, params={"lookback": 5})

# 3. 构建 Universe（永久 active）
uni = Universe.from_symbols(syms)

# 4. 实例化 Aligner（仅 Universe 过滤，不做 OLS）
aligner_raw = Aligner(uni, neutralize=[])
raw_panel = aligner_raw.align(factor_panel)
print("无中性化（仅 PIT 过滤）:", raw_panel.shape)

# 5. 使用字符串注册的 btc_beta 中性化（需 registry 中已注册）
# aligner_btc = Aligner(uni, neutralize=["btc_beta"])
# neutral_panel = aligner_btc.align(factor_panel)

# 6. 使用 LogMcapExposure 实例（无需 registry lookup）
log_mcap = LogMcapExposure()
aligner_mcap = Aligner(uni, neutralize=[log_mcap], allow_non_pit_exposures=True)
neutral_panel = aligner_mcap.align(factor_panel)
print("log_mcap 中性化后:", neutral_panel.shape)
assert neutral_panel.shape == factor_panel.shape
```

---

## 5. 4 类 PIT 规则详解

### 5.1 Listing 隔离（新币隔离）

所有 symbol 在 `listing_date + 7 days` 之前的 cell 设为 `null`。

原因：新上市合约流动性差、价格发现不稳定，因子信号失真风险高。

```
eligible_from = listing_date + timedelta(days=7)
mask_null_when: ts < eligible_from
```

### 5.2 Delisting 过滤

`delisting_date` 不为 null 时，`ts >= delisting_date` 的 cell 设为 `null`。

```
mask_null_when: delisting_date is not None AND ts >= delisting_date
```

### 5.3 Circuit Breaker（预留规则）

`pit_rules_json` 中可存储 `{"circuit_breaker": [{"start": "...", "end": "..."}]}` 格式。当前 Aligner 不处理此字段，留给未来扩展。

### 5.4 Split 事件（预留规则）

代币拆分事件导致价格非连续跳变，当前 Aligner 未实现 split 调整，需在数据层处理（向后除权）。

---

## 6. 横截面 OLS 残差化算法

对因子 panel 的**每个时间点 t**：

```
y = [score_sym1_t, score_sym2_t, ..., score_symN_t]  # 形状 (N,)

X = [[1, exposure_btc_sym1, exposure_logmcap_sym1],
     [1, exposure_btc_sym2, exposure_logmcap_sym2],
     ...
     [1, exposure_btc_symN, exposure_logmcap_symN]]   # 形状 (N, K+1)

beta, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
residuals = y - X @ beta                              # 形状 (N,)
```

**NaN 处理**：
- `y` 或任意 exposure 中 NaN 的 symbol 排除出 OLS（`valid = ~nan_mask`）；
- 有效样本 < 2 时，整行保留 NaN；
- OLS 对 valid 子集运行，残差填回原位，NaN 位置保留 NaN。

实现位置：`src/tinohelm/aligner/aligner.py` → `_ols_residualize_row()`

---

## 7. PITViolationError

```python
from tinohelm.aligner.aligner import PITViolationError

try:
    aligner.align(panel)
except PITViolationError as exc:
    print(f"PIT 违规: {exc}")
    # e.g.: "Provider 'btc_beta' returned future timestamps not in panel: [...]"
```

触发条件：任意 provider 的 `get_exposure()` 返回 ts > `max(panel["ts"])`。

---

## 8. Schema 引用

### 8.1 DB 表 `universes`（migration 012）

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | `Integer` | Primary key |
| `name` | `String(255)` | 唯一名称（文件名 stem） |
| `source_csv_path` | `String(500)` | CSV 文件绝对路径 |
| `source_csv_hash` | `String(64)` | SHA-256 hash，幂等 sync 用 |
| `min_history_bars` | `Integer` | 最少历史 bar 数（default 100） |
| `new_coin_isolation_days` | `Integer` | 新币隔离天数（default 7） |
| `pit_rules_json` | `JSON` | `{symbol: {listing_date, delisting_date}}` |
| `created_at` | `DateTime` | UTC naive |
| `updated_at` | `DateTime` | UTC naive |

### 8.2 DB 表 `exposures_cache`（migration 012）

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | `Integer` | Primary key |
| `provider_name` | `String(40)` | 如 `"btc_beta"` / `"log_mcap"` |
| `symbol` | `String(40)` | Symbol 字符串 |
| `ts_event_ns` | `BigInteger` | 纳秒时间戳 |
| `value` | `Float` | 暴露值 |
| `computed_at` | `DateTime` | 计算时间 |

联合唯一索引：`(provider_name, symbol, ts_event_ns)`

### 8.3 `pit_rules_json` 内存格式

```python
pit_rules = {
    "BTCUSDT-PERP": {
        "listing_date": "2020-01-01",
        "delisting_date": None,
    },
    "DOTUSDT-PERP": {
        "listing_date": "2020-09-01",
        "delisting_date": "2024-06-01",
    },
}
```
