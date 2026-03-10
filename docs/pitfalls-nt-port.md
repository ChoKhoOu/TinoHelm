# Pitfalls: Porting Strategies from Jesse to NautilusTrader

Common issues when converting Jesse strategies to NautilusTrader (NT).

## 1. Rolling Buffer Truncates Long-Period Indicator Warm-up

**Severity**: Critical — can reduce trade count by 90%+ and completely change PnL.

**Problem**: Jesse's `ta.ema(candles, period)` computes over the **entire candle history** (tens of thousands of bars). A naive NT port often stores OHLCV in a fixed-size `deque(maxlen=N)` and recomputes indicators from that buffer each bar.

If the buffer is shorter than the indicator needs for proper convergence, the indicator output will be wrong. Example:

```
Buffer size = 300, EMA period = 233
Effective warm-up = 300 - 233 = 67 bars (severely insufficient)
Jesse warm-up = 50,000+ bars (fully converged)
```

The EMA-233 value will be significantly different, which cascades into regime detection, entry signals, and sizing — causing dramatically fewer trades.

**Fix**: Use **incremental (online) EMA** that updates O(1) per bar and accumulates over the full history:

```python
# In __init__:
self._ema_vals: dict[int, float] = {}

# Called every on_bar:
def _update_emas(self, close: float) -> None:
    for period in (21, 55, 144, 233):
        alpha = 2.0 / (period + 1.0)
        if period not in self._ema_vals:
            self._ema_vals[period] = close  # seed with first close
        else:
            prev = self._ema_vals[period]
            self._ema_vals[period] = alpha * close + (1.0 - alpha) * prev

# In _compute_factors, replace:
#   ema233 = _ema(close_buffer, 233)    # WRONG: truncated warm-up
# with:
#   ema233 = self._ema_vals[233]         # CORRECT: full-history EMA
```

**Affected indicators**: Any indicator that depends on the full history — primarily EMA (especially long periods like 55, 144, 233). RSI and ADX use Wilder smoothing which also benefits from longer history, but converge faster (14-period is fine with 300-bar buffer).

**Rule of thumb**: If `buffer_size / indicator_period < 5`, the indicator is not converged. Either increase the buffer or switch to incremental computation.

## 2. NT Money Objects Are Not Plain Floats

**Problem**: NT's `Position.realized_pnl`, `Account.balance_total(currency)`, and similar return `Money` objects that stringify as `"114.60 USDT"`. Calling `float(money_obj)` may fail.

**Fix**: Use `.as_double()` for numeric value:
```python
pnl_float = float(position.realized_pnl.as_double())
```

## 3. Account.balance_total() Requires Currency Argument

**Problem**: `account.balance_total()` without a `Currency` argument may raise `TypeError` in some NT versions.

**Fix**: Pass the currency explicitly, or catch the exception:
```python
from nautilus_trader.model.currencies import USDT
bal = account.balance_total(USDT)
```

## 4. OrderSide Enum Stringifies to Integer

**Problem**: `str(OrderSide.BUY)` returns `"1"`, not `"BUY"`.

**Fix**: Use `.name` attribute: `OrderSide.BUY.name` returns `"BUY"`.

## 5. Position Attribute Names Differ from Common Conventions

**Problem**: NT uses `ts_opened` / `ts_closed` / `duration_ns`, NOT `opened_ts` / `closed_ts` / `duration`.

## 6. subscribe_bars() Must Be Called in on_start()

**Problem**: `on_bar()` will **never fire** unless `self.subscribe_bars(bar_type)` is called in `on_start()`. Jesse handles data subscription implicitly.

## 7. Constructor Cannot Access self.clock or self.log

**Problem**: In `__init__`, the NT system hasn't initialized `self.clock` or `self.log` yet. Only set instance attributes in the constructor. Move all initialization that needs logging or time to `on_start()`.

## 8. HEDGING vs NETTING Position Semantics

**Problem**: Jesse uses a single position per symbol (netting). NT's HEDGING mode creates independent positions per order. If your Jesse strategy tracks "the position", you need to explicitly manage multiple positions in NT.

**Fix**: Use a dict like `self._open_positions[position_id]` to track each independent position.

## 9. Instrument Precision Enforcement

**Problem**: NT's RiskEngine strictly enforces price and quantity precision. Jesse is more lenient. Orders with too many decimal places get `OrderDenied`.

**Fix**: Always use `instrument.make_price(value)` and `instrument.make_qty(value)` which round to the instrument's declared precision.

## 10. Data Timestamp Convention

**Problem**: NT expects bar `ts_init` to be the bar **closing time**. Some data providers use opening time. Using opening time causes look-ahead bias in backtests.
