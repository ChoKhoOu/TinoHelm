# Risk Gates: 策略内因子缩放风控

## 概述

这套逻辑是 BTCMultiFactor 策略 **内部** 的风控机制，与 RiskGuardActor（组合级风控）完全独立。
当 `disable_risk_gates = True`（当前默认）时，这套逻辑全部关闭。

**设计决策**：这套逻辑在策略迁移时保留在策略内部，不移到 RiskGuardActor。
原因是这些缩放因子与策略的因子计算紧密耦合（Kelly、regime、subregime），
属于"策略信号质量调节"而非"组合级风控"。

## 与 RiskGuardActor 的区别

| 维度 | Risk Gates（本文档） | RiskGuardActor |
|------|---------------------|----------------|
| 层级 | 策略内部 | 跨策略组合级 |
| 作用 | 缩放 Kelly 仓位大小 | 熔断/阻止开仓/平仓 |
| 开关 | `disable_risk_gates` | Actor 是否配置 |
| 粒度 | 单品种 | 全品种聚合 |
| 触发 | 连续亏损、周亏损、回撤区间 | 日亏损阈值、总回撤阈值、总敞口 |

## 控制开关

```python
# BTCMultiFactorConfig
disable_risk_gates: bool = True   # 当前默认关闭
```

当 `disable_risk_gates = True` 时：
- Kelly fraction = `fractional_kelly * f_raw`（不经过任何缩放）
- `hard_halt` 始终为 False（日亏损/回撤熔断不生效）
- `_risk_fraction_for_side()` 直接返回 `base_risk_fraction`（不乘 profile/zone/subregime 缩放）

## 缩放因子详解

当 `disable_risk_gates = False` 时，Kelly fraction 经过以下乘法链：

```
kelly_fraction = fractional_kelly * f_raw * scale

其中 scale = confidence_scale
            * regime_scale
            * weekly_scale
            * streak_scale
            * subregime_risk_scale
            * kelly_boost
```

### 1. confidence_scale（BDI 置信度）

```python
confidence_scale = min(1.0, max(0.25, abs(bdi) / 0.35))
```

BDI（Bear/Bull Direction Index）越大，信号越有信心，仓位越大。
BDI 接近 0 时缩放到 0.25（最低 25% 仓位）。

### 2. regime_scale（市场状态缩放）

```python
regime_scale = 0.40 if regime == "transition" else 1.0
```

转换期（transition）只用 40% 仓位。上涨/下跌 regime 正常。

### 3. weekly_scale（周亏损软限制）

```python
WEEKLY_SOFT_STOP_PCT = -0.04  # 周亏损 -4%
weekly_scale = 0.5 if self._week_loss_pct <= WEEKLY_SOFT_STOP_PCT else 1.0
```

本周已亏损超过 4% 时，仓位减半。

### 4. streak_scale（连续亏损缩放）

```python
streak_scale = 0.5 if self._consecutive_losses >= 3 else 1.0
```

连续亏损 3 次及以上时，仓位减半。

### 5. subregime_risk_scale（熊市子状态缩放）

```python
subregime_risk_scale = float(sub_pack.get("risk_mult", 1.0))

# SUBREGIME_PACKS:
# bear_crash:   risk_mult = 1.30  (崩盘期反而放大——做空机会)
# bear_chop:    risk_mult = 0.85  (震荡期缩小)
# bear_neutral: risk_mult = 1.00  (中性)
```

### 6. kelly_boost（近期胜率调节）

```python
# 基于最近 30 笔交易的胜率:
kelly_wr >= 0.55 → kelly_boost = 1.30  (高胜率放大)
kelly_wr >= 0.52 → kelly_boost = 1.15
kelly_wr <= 0.42 → kelly_boost = 0.80  (低胜率缩小)
其他             → kelly_boost = 1.00
```

## zone_scale（回撤区间缩放）

独立于 Kelly 缩放链，作用于 `_risk_fraction_for_side()`：

```python
def _risk_zone_mult(self, drawdown_pct: float) -> float:
    if drawdown_pct <= -0.06:    return 0.25   # 深度回撤：25% 仓位
    if drawdown_pct <= -0.04:    return 0.45   # 中度回撤：45% 仓位
    if drawdown_pct <= -0.02:    return 0.65   # 轻度回撤：65% 仓位
    return 1.0                                  # 正常：100% 仓位
```

## hard_halt（硬停止）

```python
risk_halt_today = self._day_loss_pct <= self._daily_stop_loss_pct     # 默认 -2%
risk_halt_drawdown = self._drawdown_pct <= self._max_drawdown_stop_pct # 默认 -9%
hard_halt = bool(risk_halt_today or risk_halt_drawdown)

if hard_halt and not self._disable_risk_gates:
    kelly_fraction_long = 0.0
    kelly_fraction_short = 0.0
```

触发 hard_halt 后，Kelly fraction 归零，不会有新开仓。
**注意**：这个 hard_halt 只在 `disable_risk_gates = False` 时生效。

## symbol_gate_block（品种级软封禁）

```python
# 最近 12 笔交易:
symbol_gate_block = (
    len(win_slice) >= 10
    and win_rate_recent < 0.45
    and net_recent <= 0.0
)
```

如果某品种最近交易胜率低于 45% 且净亏损，暂时屏蔽该品种信号。
这是一个策略级的自适应保护，与 RiskGuardActor 无关。

## 迁移建议

当从单文件迁移到 portfolio 模式时：

1. **保留全部 risk gates 逻辑在策略内部**
2. **`disable_risk_gates` 继续作为策略参数**
3. **daily_stop_loss_pct 和 max_drawdown_stop_pct 有两个用处**：
   - 策略内部：控制 hard_halt（Kelly 归零）
   - RiskGuardActor：控制组合级熔断
   - 两者的阈值可以不同（Actor 更宽松，策略更激进）
4. **total_risk_cap 迁移到 Actor** 作为跨品种总风险预算
5. **max_positions 拆分**：
   - 策略内的 max_positions = 单品种仓位上限
   - Actor 的 max_positions = 跨品种总仓位上限
