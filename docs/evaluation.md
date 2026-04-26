# Evaluation 模块

> See also: `factor.md` 第 2 节（FactorSpec + EvalConfig），`aligner.md` 第 5 节（walk-forward
> PIT 联动），`signal.md` 第 6 节（SignalEvaluator），`3-tech-design.md` §3.7–3.9。

## 1. 概览

`tinohelm.factor.evaluation` 是因子量化评估的核心模块，包含：

| 子模块 | 功能 |
|--------|------|
| `ic.py` | IC 系列 + IC 衰减 + 半衰期 + 前向收益率 |
| `quantile.py` | 分位组 PnL + 单调性检验 |
| `turnover.py` | 换手率 + 费用拖累 |
| `distribution.py` | 因子分布统计 + 直方图 |
| `robustness.py` | shuffle p-value + 子样本 IC + 跨 symbol IC |
| `cost.py` | edge waterfall（毛利 / 滑点 / 佣金 逐项扣除） |
| `rating.py` | 0-3 综合评分 |
| `walk_forward.py` | López de Prado 纯化 & 隔离 walk-forward CV |
| `segmentation.py` | BTC 趋势 / 波动区间 / 资金费率水平 分段评估 |
| `compare.py` | 两两比较 bootstrap CI + 多因子报告 |
| `correlation.py` | IC 时序相关 / 横截面相关 / 矩阵 |
| `clustering.py` | 层次聚类 + dendrogram 裁剪 |
| `composition.py` | 多因子合成（等权 / IC 加权） |
| `orthogonalize.py` | Gram-Schmidt 正交化 |
| `evaluator.py` | 高层 orchestrator（快速路径 + 全量路径） |

所有模块**零 pandas import**（AC-6.1.1 检查）；pandas 输入通过鸭子类型转换接受，但不在模块顶层 import。

---

## 2. 前向收益率计算

```python
from tinohelm.factor.evaluation.ic import forward_returns

# close_df: pl.DataFrame, 列：[ts, value]
# period: 前向 N 期
fwd = forward_returns(close_df, period=5, log_ret=False)
# 输出：[ts, value]，value[t] = close[t+period] / close[t] - 1
# 最后 period 行为 null（无法计算）
```

`log_ret=True` 时使用对数收益率：`log(close[t+period] / close[t])`

---

## 3. IC（Information Coefficient）

### 3.1 IC 系列

```python
from tinohelm.factor.evaluation.ic import compute_ic_series, compute_ic_summary

# factor_df: [ts, value] — 因子得分
# fwd_df: [ts, value]    — 对应前向收益率
# freq: 聚合频率（"D"=日、"W"=周、"M"=月）

ic_series_df = compute_ic_series(factor_df, fwd_df, freq="D")
# 输出：[ts, ic] — 每期的 Spearman IC

summary = compute_ic_summary(ic_series_df)
# 输出 dict：ic_mean, ic_std, ir, ic_tstat, ic_positive_pct, ic_max_abs
```

**IC（Spearman）**：因子分位秩与前向收益分位秩的 Spearman 相关系数。

### 3.2 指标定义

| 指标 | 定义 | 好信号阈值 |
|------|------|-----------|
| `ic_mean` | 时序 IC 均值 | `> 0.05` |
| `ic_std` | IC 标准差 | `< 0.25`（稳定） |
| `ir` | `ic_mean / ic_std`（信息比率） | `> 0.5` |
| `ic_tstat` | IC 显著性 t 统计量 | `|t| > 2` |
| `ic_positive_pct` | IC > 0 的期数占比 | `> 55%` |
| `ic_max_abs` | `max(|IC|)` | 用于识别异常期 |

### 3.3 IC 衰减 + 半衰期

```python
from tinohelm.factor.evaluation.ic import compute_ic_decay, compute_half_life

# 计算 lag 1..20 的 IC 衰减曲线
decay = compute_ic_decay(factor_df, close_df)
# 返回 list[dict]：[{"lag": 1, "ic": 0.12}, {"lag": 2, "ic": 0.09}, ...]

half_life = compute_half_life(decay)
# 返回 int | None：IC 衰减到一半时的 lag 数（bars）
```

半衰期越小 = 因子信号衰减越快 = 换手率要求越高。

---

## 4. 分位组分析

```python
from tinohelm.factor.evaluation.quantile import compute_quantile_returns

result = compute_quantile_returns(
    factor_df,
    fwd_df,
    n_quantiles=5,
)
# 返回 dict：
# {
#   "avg_returns": {"Q1": 0.001, "Q2": 0.0005, ..., "Q5": -0.001},
#   "cum_returns": {"Q1": [...], "Q5": [...]},
#   "is_monotonic": True,
# }
```

`is_monotonic=True` 表示从 Q1（最高因子值）到 Q5（最低因子值）收益单调递减，是多空信号有效性的关键指标。

---

## 5. 换手率与费用拖累

```python
from tinohelm.factor.evaluation.turnover import compute_turnover

result = compute_turnover(
    factor_df,
    fwd_df,
    n_quantiles=5,
    fee_rate=0.0002,  # 双边 4bps 的单边 = 0.02%
)
# 返回 dict：
# {
#   "daily": 0.15,               # 日换手率（日均 Σ|Δrank| / N）
#   "annualized": 54.75,         # 年化换手率
#   "fee_drag_monthly": 0.0036,  # 月均费用拖累（fraction）
# }
```

---

## 6. Walk-Forward 纯化 & 隔离 CV

实现了 López de Prado《机器学习金融》第 7 章的 Purged & Embargoed Walk-Forward Cross-Validation。

### 6.1 WalkForwardSpec 字段

```python
from tinohelm.factor.types import WalkForwardSpec

spec = WalkForwardSpec(
    train_bars=200,      # 训练窗口大小（bars）
    test_bars=50,        # 测试窗口大小（bars）
    embargo_bars=5,      # train 和 test 之间的空白期（防止标签泄漏）
    purge_bars=5,        # 从 train 末尾去掉的 bars（防止 forward return 重叠）
    step_bars=None,      # 折叠步长（None = test_bars，折叠不重叠）
)
```

| 参数 | 说明 |
|------|------|
| `train_bars` | 每折 train 窗口大小（bars） |
| `test_bars` | 每折 test（OOS）窗口大小（bars） |
| `embargo_bars` | train 结束到 test 开始的间隔（防止 forward-return 标签污染） |
| `purge_bars` | 从 train 末尾切掉的 bars（防止与 forward return 重叠） |
| `step_bars` | 折叠起始的步长，`None` = 等于 `test_bars` |

### 6.2 折叠布局示意

```
|<-- train (200-5=195 bars) -->||<-- purge(5) -->||<-- embargo(5) -->||<-- test(50) -->|
 train_start                    train_end          test_start          test_end
```

折叠公式：
```
train_start = k * step
train_end   = train_start + train_bars - purge_bars
test_start  = train_start + train_bars + embargo_bars
test_end    = test_start + test_bars
```

### 6.3 代码示例（可运行）

```python
"""Walk-Forward 纯化 & 隔离 CV 示例。"""
import polars as pl
import numpy as np
from datetime import datetime, timedelta

from tinohelm.factor.types import EvalConfig, WalkForwardSpec
from tinohelm.factor.evaluation.evaluator import Evaluator

# 合成数据（T=300 bars, N=3 symbols）
np.random.seed(42)
T = 300
ts = [datetime(2024, 1, 1) + timedelta(hours=i) for i in range(T)]
syms = ["BTC", "ETH", "BNB"]
factor = pl.DataFrame({
    "ts": ts,
    **{s: np.random.randn(T).tolist() for s in syms}
})
close = pl.DataFrame({
    "ts": ts,
    **{s: (100 + np.cumsum(np.random.randn(T) * 0.3)).tolist() for s in syms}
})

wf_spec = WalkForwardSpec(
    train_bars=150,
    test_bars=30,
    embargo_bars=3,
    purge_bars=3,
)

config = EvalConfig(
    universe=("BTC", "ETH", "BNB"),
    start="2024-01-01",
    end="2024-11-30",
    forward_period=5,
    walk_forward=wf_spec,
)

evaluator = Evaluator()
result = evaluator.evaluate_full(factor, close, config, shuffle_iter=0)

print(f"OOS 折数: {len(result.oos_ic_series)}")
print(f"平均 OOS IC: {result.ic_mean:.4f}")
print(f"OOS IR: {result.ir:.4f}")
for fold in result.oos_ic_series[:3]:
    print(f"  fold={fold['fold']}, ic={fold['ic_mean']:.4f}, sharpe={fold['sharpe']:.4f}")
```

---

## 7. 分段评估（Segmentation）

```python
from tinohelm.factor.evaluation.segmentation import segment_evaluate

# segments: list of (segment_name, mask_series)
# mask_series: pl.Series[bool], 长度 = T
result = segment_evaluate(
    factor_df,
    fwd_df,
    segments=[
        ("btc_up", btc_trend_up_mask),
        ("btc_down", btc_trend_down_mask),
    ]
)
# 返回：{"btc_up": {ic_mean, ir, ...}, "btc_down": {...}}
```

**三类内置 segment 提供者**（由 EvalConfig.segments 字段触发）：

| Provider | 含义 | 区间分类 |
|----------|------|---------|
| `btc_trend` | BTC 价格趋势 | `up` / `sideways` / `down` |
| `vol_regime` | BTC 实现波动率 | `low` / `medium` / `high` |
| `funding_level` | 资金费率水平 | `negative` / `neutral` / `positive` |

---

## 8. Robustness 检验

### 8.1 Shuffle p-value

```python
from tinohelm.factor.evaluation.robustness import shuffle_test

result = shuffle_test(factor_df, fwd_df, n_iter=1000, max_workers=4)
# 返回 dict：
# {
#   "real_ic": 0.08,
#   "shuffle_distribution": [0.01, -0.02, ...],  # n_iter 个随机 IC 值
#   "p_value": 0.03,
#   "significant": True,  # p_value < 0.05
# }
```

Shuffle 检验通过随机打乱因子排名打破时序关系，若 `real_ic > 95th percentile` 则显著。

### 8.2 子样本 IC

```python
from tinohelm.factor.evaluation.robustness import subsample_ic

result = subsample_ic(factor_df, fwd_df, freq="ME")
# 返回 list[dict]：每个子样本的 ic_mean/ic_std 统计
```

按月（`freq="ME"`）切割时序，逐月计算 IC。检验因子稳定性。

### 8.3 跨 Symbol IC

```python
from tinohelm.factor.evaluation.robustness import cross_symbol_ic

result = cross_symbol_ic(
    factor_panels={"BTC": btc_factor, "ETH": eth_factor},
    returns_panels={"BTC": btc_returns, "ETH": eth_returns},
)
# 返回每个 symbol 的独立 IC 统计
```

---

## 9. compare_results 与 compare_multi

### 9.1 compare_results（两两比较 + bootstrap CI）

```python
from tinohelm.factor.evaluation import compare_results
from tinohelm.factor.types import EvalResult

result_a = EvalResult(ic_mean=0.08, ir=0.6)
result_b = EvalResult(ic_mean=0.10, ir=0.8)

comparison = compare_results(result_a, result_b, n_bootstrap=1000, confidence=0.95)
# 返回 dict：
# {
#   "ic_mean": {"delta": 0.02, "ci_low": 0.005, "ci_high": 0.035, "significant": True},
#   "ir":      {"delta": 0.2,  "ci_low": 0.05,  "ci_high": 0.38,  "significant": True},
# }
```

**bootstrap CI**：对 `result.ic_series` 中的 IC 值，有放回地重采样 `n_bootstrap` 次，计算 `(alpha/2, 1-alpha/2)` 百分位数作为置信区间。`significant=True` 当且仅当 CI 不包含 0。

### 9.2 compare_multi（多因子报告）

```python
from tinohelm.factor.evaluation import compare_multi

report = compare_multi(
    {"ret_N": result_ret_N, "rsi_signal": result_rsi, "parkinson_vol": result_vol},
    n_bootstrap=500,
)
# 返回 dict，包含：
# {
#   "ranking": [...],          # 按 ir 排序的因子列表
#   "pairwise_comparison": {}, # 两两 bootstrap 比较矩阵
#   "agent_summary": "...",    # 自然语言摘要
# }
```

---

## 10. 相关性、聚类、正交化

### 10.1 相关性矩阵

```python
from tinohelm.factor.evaluation import correlation_matrix

corr_df = correlation_matrix(
    {"ret_N": panel_ret, "rsi_signal": panel_rsi, "vol_ratio": panel_vol}
)
# 返回 pl.DataFrame，包含 factor_name 列 + N 个因子相关性列
```

三种变体：

- `correlation_matrix` — 横截面因子值相关
- `correlation_matrix_cross_section` — 逐期横截面平均相关
- `correlation_matrix_ic_time_series` — IC 时序相关

### 10.2 层次聚类

```python
from tinohelm.factor.evaluation import hierarchical_cluster, cut_dendrogram

cluster_result = hierarchical_cluster(corr_df, method="ward")
# 返回 dict：{"linkage_matrix": ndarray(F-1, 4), "labels": [...]}

groups = cut_dendrogram(cluster_result["linkage_matrix"], cluster_result["labels"], n_clusters=3)
# 返回 list[list[str]]：每组中的因子名
```

### 10.3 Gram-Schmidt 正交化

```python
from tinohelm.factor.evaluation import orthogonalize, orthogonalize_many

# 正交化 panel_b 对 panel_a 的线性分量
orth_b = orthogonalize(panel_a, panel_b)

# 批量正交化（按列表顺序逐一去除线性分量）
panels_orth = orthogonalize_many([panel_a, panel_b, panel_c])
```

---

## 11. Evaluator 快速路径与全量路径

```python
from tinohelm.factor.types import EvalConfig
from tinohelm.factor.evaluation.evaluator import Evaluator

evaluator = Evaluator()  # 无状态，可重用

config = EvalConfig(
    universe=("BTC", "ETH"),
    start="2024-01-01",
    end="2024-12-31",
    forward_period=5,
    quantiles=5,
    cost_bps=4.0,
)

# 快速路径：IC / quantile / turnover / distribution / rating
result = evaluator.evaluate(factor_panel, close_panel, config)

# 全量路径：+ shuffle / subsample / cost waterfall (+ walk-forward 如 config 中有)
result_full = evaluator.evaluate_full(
    factor_panel,
    close_panel,
    config,
    shuffle_iter=1000,
    shuffle_workers=4,
)

print(f"IC: {result.ic_mean:.4f}, IR: {result.ir:.4f}, Rating: {result.rating}/3")
```

**NaN/Inf 清理**：`_scrub_result` 在返回前将所有非有限浮点数替换为 `None` / `0.0`，防止 PostgreSQL JSON 列拒绝写入。

---

## 12. Schema 引用

### 12.1 EvalResult 字段表

| 字段 | 类型 | 默认 | 来源 |
|------|------|------|------|
| `ic_mean` | `float` | `0.0` | `compute_ic_summary` |
| `ic_std` | `float` | `0.0` | `compute_ic_summary` |
| `ir` | `float` | `0.0` | `compute_ic_summary` |
| `ic_tstat` | `float` | `0.0` | `compute_ic_summary` |
| `ic_positive_pct` | `float` | `0.0` | `compute_ic_summary` |
| `ic_max_abs` | `float` | `0.0` | `compute_ic_summary` |
| `half_life` | `int | None` | `None` | `compute_half_life` |
| `quantile_pnl` | `dict[str, float]` | `{}` | `compute_quantile_returns` |
| `quantile_cum_returns` | `dict[str, list[dict]]` | `{}` | `compute_quantile_returns` |
| `is_monotonic` | `bool` | `False` | `compute_quantile_returns` |
| `turnover` | `float` | `0.0` | `compute_turnover` |
| `turnover_annualized` | `float` | `0.0` | `compute_turnover` |
| `fee_drag_monthly` | `float` | `0.0` | `compute_turnover` |
| `rating` | `int` (0–3) | `0` | `compute_rating` |
| `ic_series` | `list[dict]` | `[]` | `compute_ic_series` |
| `ic_decay` | `list[dict]` | `[]` | `compute_ic_decay` |
| `distribution_stats` | `dict[str, float]` | `{}` | `compute_distribution` |
| `distribution_histogram` | `list[dict]` | `[]` | `compute_distribution` |
| `robustness` | `dict` | `{}` | `evaluate_full` |
| `cost` | `dict[str, float]` | `{}` | `edge_waterfall` |
| `oos_ic_series` | `list[dict]` | `[]` | `WalkForwardEvaluator` |
| `segment_results` | `dict[str, dict]` | `{}` | `segment_evaluate` |
| `neutralization_config` | `dict` | `{}` | Aligner 配置记录 |
| `baseline_id` | `str | None` | `None` | 对比基准 factor_run UUID |

### 12.2 oos_ic_series 单元结构

每个元素（per fold）：
```python
{
    "fold": 0,              # 折叠编号（0-based）
    "train_start": 0,       # bar 索引（inclusive）
    "train_end": 195,
    "test_start": 203,
    "test_end": 253,
    "ic_mean": 0.07,
    "ic_std": 0.12,
    "sharpe": 0.58,         # ic_mean / ic_std（OOS Sharpe of IC）
}
```

### 12.3 segment_results 结构

```python
{
    "btc_trend": {
        "up":       {"ic_mean": 0.10, "ic_std": 0.09, "ir": 1.11},
        "sideways": {"ic_mean": 0.04, "ic_std": 0.14, "ir": 0.29},
        "down":     {"ic_mean": 0.12, "ic_std": 0.11, "ir": 1.09},
    },
    "vol_regime": {
        "low":    {"ic_mean": 0.05, ...},
        "medium": {"ic_mean": 0.09, ...},
        "high":   {"ic_mean": 0.13, ...},
    },
}
```
