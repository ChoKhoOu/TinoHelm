"""
TinoHelm 自定义因子模板
========================

本文件是自定义因子的开发模板。将此文件复制并重命名为你的因子名（如 my_factor.py），
放在 ~/.tino/research/factors/ 目录下，系统会自动发现并加载。

## 开发规范

1. 每个 .py 文件 = 一个因子
2. 必须包含两个模块级变量：
   - FACTOR_META: dict — 因子元数据（名称、分类、参数定义等）
   - compute: function — 计算函数，签名为 compute(df, params) -> pd.Series

3. compute 函数的输入：
   - df: pd.DataFrame — OHLCV 数据，列名: open, high, low, close, volume
     索引为 DatetimeIndex (ts_init)，按时间升序排列
   - params: dict — 用户通过 UI 传入的参数值，key 对应 FACTOR_META["params"] 中定义的参数

4. compute 函数的输出：
   - pd.Series — 因子值序列，索引与输入 df 对齐
   - 允许 NaN（前 N 根 bar 因窗口不足产生的 NaN 会被自动处理）
   - 值域无限制，但建议归一化或标准化以便跨因子比较

## 关键约束

- ⚠️ 禁止未来信息泄漏：compute(df, params) 在时刻 t 只能使用 ≤t 的数据
  所有 rolling/shift 操作必须是回看型（lookback），不能用 shift(-N) 或 forward-looking
- ⚠️ 数值稳定性：除法分母加 1e-12 防零除，对 NaN/Inf 做防护
- ⚠️ 向量化计算：用 pandas/numpy 向量化操作，不要写 Python for 循环遍历每根 bar
- ⚠️ 无副作用：不要修改传入的 df，不要写文件或访问网络

## FACTOR_META 字段说明

```python
FACTOR_META = {
    "name": "factor_id",       # 因子唯一标识，与文件名一致（无 .py 后缀）
    "label": "因子中文名",      # UI 显示名称
    "category": "分类名",       # 所属分类，如：动量、波动、量价、微观结构、自定义
    "data_type": "bar",        # 需要的数据类型：bar | trade_tick
    "description": "一句话描述", # 可选，UI 中的 tooltip 文字
    "params": {                # 可调参数定义
        "param_name": {
            "default": 20,     # 默认值
            "min": 1,          # UI 滑块最小值
            "max": 500,        # UI 滑块最大值
            "label": "参数中文名", # UI 显示
        },
    },
}
```

## 验证流程（推荐三步走）

写完 compute 后，用因子研究页面的「探索」功能做快速验证：

### 第一步：现象确认
- IC 均值是否显著偏离零（|t-stat| > 3）
- PI 的聚集性（lag-1 自相关是否 > 0.3，说明高 PI 成簇出现）
- PI 与 realized vol 相关性（若 > 0.5 则可能只是 vol 的 proxy）
- Kill condition: |t-stat| < 3 → 放弃

### 第二步：预测力
- 多 horizon Rank IC 是否单调衰减
- 增量回归：控制 vol 和 raw flow 后，signal 的 t-stat 是否 > 2
- ⚠️ 用 HAC (Newey-West) 标准误，因为高频残差有自相关
  maxlags 设为对应约 10 分钟的 bar 数（如 30s 频率下 maxlags=20）
  如果用普通 OLS 标准误，t-stat 会虚高

### 第三步：安慰剂
- 提交「深度诊断」跑 shuffle test，确认信号非随机
- 打乱时序后重新计算 IC，真实 IC 应落在 shuffle 分布的 99% 之外
- Kill condition: percentile < 99% → 放弃

通过以上三步再考虑将因子纳入策略。

## IC 评判标准

IC (Information Coefficient) = 信号值与未来收益的 Spearman rank 相关系数。
用 rank 而不是 Pearson，是因为关心"信号大的时候收益是否也大"的单调关系，
而非线性关系。

    IC_t = SpearmanCorr(S_t, r_{t+1})

单品种时序 IC：在滚动窗口内收集 (S_t, r_{t+1}) 配对，算 rank correlation。

评判指标：
- Mean IC: 均值，越偏离零越好
- IC Std: 标准差，越小越稳定
- IC t-stat: 均值 / (标准差 / sqrt(n))，> 3 才有信心
- IC > 0%: 正 IC 出现的比例，> 55% 算及格
- IC IR: Mean IC / IC Std，信息比率，> 0.5 算好

## NT 框架中的 IC 计算

在 NT 的 Actor/Strategy 里，核心思路是：每个 bar 到达时记录信号值，
同时回填上一个 bar 的 forward return，攒够一个窗口后算 IC。

```python
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.model.data import Bar
from scipy.stats import spearmanr
from collections import deque
import numpy as np


class SignalAnalyzer(Strategy):
    \"\"\"
    挂在 NT 上的信号诊断 Actor。
    不做交易，只收集信号值和 forward return，滚动计算 IC。

    ⚠️ 假设:
      - bar 是 30s 频率，close price 可用
      - signal_value 由外部信号计算模块提供（如你的 PI 计算逻辑）
      - forward return 定义: r_{t+1} = close_{t+1} / close_{t} - 1
    \"\"\"

    def __init__(self, config):
        super().__init__(config)
        self.ic_window = config.ic_window       # IC 计算窗口，如 500
        self.horizons = config.horizons          # forward horizons，如 [1, 5, 10, 20]

        # 存储：(timestamp, signal_value, close_price)
        self.records = deque(maxlen=self.ic_window + max(self.horizons) + 1)

        # IC 时序，用于后续分析 IC 的稳定性
        self.ic_series = {h: [] for h in self.horizons}

    def on_bar(self, bar: Bar):
        signal_value = self._compute_signal(bar)  # 你的 PI 信号逻辑

        self.records.append({
            'ts': bar.ts_event,
            'signal': signal_value,
            'close': float(bar.close),
        })

        # 攒够数据后开始算 IC
        min_required = self.ic_window + max(self.horizons)
        if len(self.records) < min_required:
            return

        records_list = list(self.records)
        self._compute_rolling_ic(records_list)

    def _compute_rolling_ic(self, records):
        \"\"\"
        对每个 horizon h，取最近 ic_window 个 (signal, fwd_return_h) pair，
        算 Spearman rank IC。

        ⚠️ 对齐逻辑（最容易出 bug 的地方）:
           signal[i] 对应 records[i] 时刻的信号值
           fwd_return[i] = close[i+h] / close[i] - 1
           所以有效 pair 的 signal index 范围是 [0, len-h)
        \"\"\"
        n = len(records)

        for h in self.horizons:
            signals = []
            fwd_rets = []

            # 取最近 ic_window 个有效 pair
            start = max(0, n - h - self.ic_window)
            end = n - h

            for i in range(start, end):
                s = records[i]['signal']
                if s is None or not np.isfinite(s):
                    continue
                r = records[i + h]['close'] / records[i]['close'] - 1
                signals.append(s)
                fwd_rets.append(r)

            if len(signals) < 30:  # 样本太少不算
                continue

            ic, p_val = spearmanr(signals, fwd_rets)
            self.ic_series[h].append({
                'ts': records[end - 1]['ts'],
                'ic': ic,
                'p_val': p_val,
                'n_obs': len(signals),
            })

    def on_stop(self):
        \"\"\"回测结束时输出诊断报告\"\"\"
        self._print_ic_report()

    def _print_ic_report(self):
        for h in self.horizons:
            series = self.ic_series[h]
            if not series:
                continue

            ics = np.array([s['ic'] for s in series])

            mean_ic = np.mean(ics)
            std_ic = np.std(ics)
            n = len(ics)
            # IC 的 t-stat: 均值 / (标准差 / sqrt(n))
            # ⚠️ 如果 IC 窗口有重叠，有效 n 要打折
            effective_n = n * min(1.0, h / self.ic_window)  # 粗略去相关
            ic_tstat = mean_ic / (std_ic / np.sqrt(max(effective_n, 1)))

            ic_positive_pct = np.mean(ics > 0) * 100

            print(f"\\n=== Horizon {h} bars ===")
            print(f"Mean IC:     {mean_ic:.4f}")
            print(f"IC Std:      {std_ic:.4f}")
            print(f"IC t-stat:   {ic_tstat:.2f}")
            print(f"IC > 0:      {ic_positive_pct:.1f}%")
            print(f"IC IR:       {mean_ic / std_ic:.3f}")  # IC 的信息比率
```

## 实现上的几个坑

1. **最重要的：对齐。** signal[t] 只能用 ≤t 的数据算，forward_return[t] 必须是
   t+1 开始的。你的事件驱动框架里 bar 的 close timestamp 和信号计算 timestamp
   之间可能有 off-by-one，务必检查。一个简单的 sanity check：把 signal 整体 lag
   一期再算 IC，如果 IC 反而更高，说明你原来的对齐有泄漏。

2. **自相关估计的数值稳定性。** 窗口内 signed flow 全为零（冷门时段无成交）
   或几乎恒定时，np.corrcoef 会返回 NaN。对这些时段直接输出 PI = 0 并标记为
   low-confidence（低置信度）。

3. **HAC 标准误。** 增量回归里用 Newey-West（HAC）标准误，因为高频残差肯定有
   自相关。maxlags 对应约 10 分钟（如 30s bar 下 maxlags=20）。如果用普通 OLS
   标准误，t-stat 会虚高。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ── 因子元数据 ──────────────────────────────────────────────────────
# 必须定义此变量，系统通过它发现和注册因子

FACTOR_META = {
    "name": "_template",           # 改成你的因子 ID
    "label": "模板因子",            # 改成你的因子中文名
    "category": "自定义",           # 分类：动量 / 波动 / 量价 / 微观结构 / 自定义
    "data_type": "bar",            # bar 或 trade_tick
    "description": "这是一个因子模板，复制后修改 compute 函数实现你的因子逻辑",
    "params": {
        "lookback": {
            "default": 20,
            "min": 5,
            "max": 200,
            "label": "回看周期",
        },
        # 可以定义多个参数，每个参数都会出现在 UI 的参数面板中
        # "threshold": {
        #     "default": 0.5,
        #     "min": 0.0,
        #     "max": 2.0,
        #     "label": "阈值",
        # },
    },
}


# ── 计算函数 ──────────────────────────────────────────────────────
# 必须定义此函数，签名固定为 compute(df, params) -> pd.Series

def compute(df: pd.DataFrame, params: dict) -> pd.Series:
    """计算因子值。

    参数:
        df: OHLCV DataFrame，列: open, high, low, close, volume
            索引: DatetimeIndex，按时间升序
        params: 参数字典，key 与 FACTOR_META["params"] 中的 key 对应

    返回:
        pd.Series — 因子值，索引与 df 对齐，允许 NaN
    """
    lookback = params.get("lookback", 20)

    # ── 在这里实现你的因子逻辑 ──
    # 示例：简单的收益率因子
    result = df["close"].pct_change(lookback)

    return result


# ═══════════════════════════════════════════════════════════════════
# 样例因子：Persistence Intensity (PI)
# ═══════════════════════════════════════════════════════════════════
#
# 以下是一个完整的因子实现样例。要使用此因子，将本文件复制为
# persistence_intensity.py，取消注释下方代码，删除上方的模板代码。
#
# PI 衡量订单流的持续性强度：当 signed flow 具有正自相关时，
# 说明买/卖压力在持续而非随机出现，可用于预测短期价格走势。
#
# ── 样例 FACTOR_META ──
#
# FACTOR_META = {
#     "name": "persistence_intensity",
#     "label": "持续性强度 (PI)",
#     "category": "微观结构",
#     "data_type": "bar",
#     "description": "订单流自相关强度，衡量买卖压力的持续性",
#     "params": {
#         "window": {
#             "default": 20,
#             "min": 10,
#             "max": 100,
#             "label": "滚动窗口",
#         },
#         "max_lag": {
#             "default": 5,
#             "min": 1,
#             "max": 20,
#             "label": "最大滞后阶数",
#         },
#     },
# }
#
#
# ── 样例 compute ──
#
# def compute(df: pd.DataFrame, params: dict) -> pd.Series:
#     """计算 Persistence Intensity 信号。
#
#     核心思路：
#     1. 从 OHLCV 推算 signed flow 的代理（buy_volume - sell_volume）
#     2. 滚动计算 signed flow 的 lag-1..K 自相关之和 → PI
#     3. 输出 signal = sign(mean_flow) * PI
#
#     ⚠️ 此处使用 bar 级 volume 作为 signed flow 的粗略代理。
#     若有逐笔成交数据（data_type="trade_tick"），可获得更精确的 signed flow。
#     """
#     W = params.get("window", 20)
#     K = params.get("max_lag", 5)
#
#     # 1. 推算 signed flow 代理
#     # 用 close 与 (high+low)/2 的偏差作为方向代理，乘以 volume
#     mid = (df["high"] + df["low"]) / 2
#     direction = (df["close"] - mid) / (df["high"] - df["low"] + 1e-12)
#     signed_flow = direction * df["volume"]
#
#     # 2. 滚动计算 PI
#     sf_values = signed_flow.values
#     n = len(sf_values)
#     pi = np.full(n, np.nan)
#     sf_mean = np.full(n, np.nan)
#
#     for i in range(W, n):
#         window = sf_values[i - W: i]
#
#         # 窗口内方差为零 → PI = 0
#         if np.std(window) < 1e-12:
#             pi[i] = 0.0
#             sf_mean[i] = 0.0
#             continue
#
#         # lag-1 到 lag-K 的自相关求和
#         ac_sum = 0.0
#         for k in range(1, K + 1):
#             if len(window) <= k:
#                 break
#             x = window[k:]
#             y = window[:-k]
#             corr = np.corrcoef(x, y)[0, 1]
#             if np.isfinite(corr):
#                 ac_sum += corr
#
#         pi[i] = ac_sum
#         sf_mean[i] = np.mean(window)
#
#     # 3. signal = sign(mean_flow) * PI
#     signal = np.sign(sf_mean) * pi
#
#     return pd.Series(signal, index=df.index)
