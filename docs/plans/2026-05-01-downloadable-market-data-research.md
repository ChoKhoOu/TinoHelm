# Downloadable Market Data Research/Backtest Implementation Plan

> Implementation plan for the downloadable-market-data PR.

**Goal:** every Binance data type that TinoHelm already knows how to download/convert should have an explicit research/backtest contract instead of silently stopping at ingestion.

**Architecture:** keep the storage layer source-aware and PIT-safe. Bars remain Nautilus `Bar` data under source-specific roots (`bar/klines`, `bar/markPriceKlines`, ...); event streams (`bookTicker`, `aggTrades`) are stored as NT-native `QuoteTick`/`TradeTick`; Binance Vision metrics/bookDepth are stored as typed raw Parquet tables and loaded by the factor DataLayer as panels. Backtest only injects event streams when explicitly requested, because quote/trade replay can be orders of magnitude larger than bar replay.

**Tech Stack:** Python, Polars/PyArrow, NautilusTrader `ParquetDataCatalog`, Binance Vision converters, TinoHelm `DataLayer`, pytest.

---

## Non-negotiable finance/data-engineering rules

1. **No fake precision:** do not derive L1 quote fields from OHLCV; quote fields must come from `bookTicker` / `QuoteTick` storage.
2. **No lookahead:** loaders filter by `ts_event` and return timestamped panels only; factor labels still use separately loaded close/forward-return panels.
3. **Source isolation:** `klines`, `markPriceKlines`, `indexPriceKlines`, `aggTrades`, `bookTicker`, `metrics`, `bookDepth` must not share overwrite-prone directories.
4. **Cache-first backtest:** if requested replay data is missing, use existing `DataFetchJob` queue; after fetch, invalidate catalog cache and reload once.
5. **Fail explicit:** unavailable Vision types such as `liquidationSnapshot` remain unsupported with clear errors; do not invent a fallback.
6. **Cost realism boundary:** this PR wires the data. Strategy/cost models can consume spread/quote/trade data after this, but no blanket claim that bar backtests become microstructure-exact.

## PR scope

### In scope

- `bookTicker` ingestion -> `QuoteTick` Parquet writer.
- `metrics` and `bookDepth` ingestion -> source-specific raw Parquet writers.
- DataLayer readers for:
  - `quote_tick`: `bid_price`, `bid_qty`, `ask_price`, `ask_qty`, `mid_price`, `spread_bps`, `depth_l1_usd`, `orderbook_imbalance`.
  - `trade_tick`: `trade_price`, `trade_qty`, `trade_side`, `signed_trade_qty`, `buy_qty`, `sell_qty`, `trade_imbalance`.
  - `open_interest` / `metrics`: `open_interest`, `sum_open_interest`, `open_interest_value`, top/global long-short ratios, taker volume ratio.
  - `book_depth`: `book_depth`, `book_depth_notional` filtered to the first available/lowest percentage bucket unless the caller asks for raw fields directly.
- Builtin factors stop being pending stubs where their underlying DataLayer support exists:
  - `oi_change(open_interest)` = `pct_change(lookback)`.
  - `orderbook_imbalance_L1(orderbook_imbalance)` = pass-through.
  - `trade_imbalance(trade_imbalance)` = rolling mean smoothing of loaded signed imbalance panel.
- Backtest optional replay config/API plumbing: `extra_data_types=["bookTicker", "aggTrades"]` injects `QuoteTick`/`TradeTick` into the engine cache after cache-first load/fetch.
- Regression tests for storage path, DataLayer field projection, factor activation, and backtest replay loader wiring.

### Out of scope

- Full `depth@100ms + REST snapshot` order book reconstruction.
- Liquidation/force-order stream support, because current Vision converter documents it as unavailable.
- Queue-position maker fill simulation or L1 impact model.
- Rewriting the signal/export live path to subscribe to non-bar data.

## Task 1: storage writers for downloadable non-bar data

**Files:**
- Modify: `src/tinohelm/data/catalog_helpers.py`
- Modify: `src/tinohelm/data/catalog.py`
- Modify: `src/tinohelm/data/pipeline.py`
- Test: `tests/data/test_downloadable_market_data_storage.py`

**TDD:** write failing tests first for `write_quote_ticks`, `write_metrics_parquet`, and `write_book_depth_parquet`. Verify `bookTicker` no longer falls through to “No catalog writer”.

**Implementation notes:**
- Extend source-aware pathing for quote ticks to `catalog/quotes/bookTicker/data/quote_tick/<instrument_id>/*.parquet` or equivalent explicit source root.
- Raw Parquet path convention:
  - `catalog/metrics/metrics/data/metrics/<symbol>.parquet`
  - `catalog/book_depth/bookDepth/data/book_depth/<symbol>.parquet`
- Raw tables merge/dedupe by `ts_event` plus natural keys (`percentage` for bookDepth), guarded by a per-file lock and atomic replace so parallel workers do not drop rows.

## Task 2: DataLayer readers

**Files:**
- Modify: `src/tinohelm/factor/data_layer.py`
- Modify: `src/tinohelm/factor/alias.py` if aliases are missing.
- Modify: `src/tinohelm/factor/engine/planner.py` if source inference misses new fields.
- Test: `tests/factor/test_data_layer_downloadable_market_data.py`

**TDD:** create synthetic Parquet fixtures and assert exact panels. Include time filtering and no bar fallback for quote fields.

**Implementation notes:**
- Readers return canonical `[ts, value]` Polars frames.
- Quote/trade event streams are bucketed to the requested factor frequency with right-closed windows `(ts_close - freq, ts_close]`, labelled at the repository's existing Binance bar close convention `ts_close - 1ms`.
- Metrics/bookDepth raw records are bucketed the same way using the last observation per bucket; bookDepth selects the lowest available percentage per timestamp before bucketing.
- Decode NT fixed-precision `QuoteTick`/`TradeTick` columns via existing fixed-precision decoder patterns.
- Use `catalog.quote_ticks()` / `catalog.trade_ticks()` when available, but direct Polars scan is acceptable if tests cover real Parquet schema.
- `trade_side` should be numeric for panel math: buyer aggressor `+1.0`, seller aggressor `-1.0`.
- `trade_imbalance` should be bucketed/resampled to requested frequency from tick data: `(buy_qty - sell_qty) / total_qty` per bucket close timestamp.

## Task 3: activate built-in factors backed by real downloadable sources

**Files:**
- Modify: `src/tinohelm/factor/builtins/crypto_data.py`
- Modify: `src/tinohelm/factor/builtins/microstructure.py`
- Modify: `tests/factor/test_builtins.py`

**TDD:** replace pending-stub tests with formula tests on small panels. Specs should no longer be `deprecated=True` for activated factors; keep `experimental=True` only if the project wants them hidden by default.

## Task 4: optional quote/trade replay in backtests

**Files:**
- Modify: `src/tinohelm/backtest/runner.py`
- Modify: `src/tinohelm/api/routes/backtest.py`
- Modify: worker/CLI call path if it drops unknown payload keys.
- Test: `tests/api/test_backtest_helpers.py` or a new focused runner helper test.

**TDD:** test that a runner with `extra_data_types=["bookTicker", "aggTrades"]` calls the cache-first load/fetch path and injects quote/trade ticks with `engine.add_data`; default remains unchanged.

**Implementation notes:**
- Default `extra_data_types=[]` to avoid surprise multi-GB tick replay.
- Supported values: Binance data type names (`bookTicker`, `aggTrades`, `trades`) and normalized categories (`quote_tick`, `trade_tick`).
- If Redis is unavailable and data is missing, log and skip rather than crashing the bar backtest.

## Task 5: verification

Run targeted tests first:

```bash
python -m pytest tests/data/test_downloadable_market_data_storage.py -q
python -m pytest tests/factor/test_data_layer_downloadable_market_data.py -q
python -m pytest tests/factor/test_builtins.py -q
python -m pytest tests/api/test_backtest_helpers.py -q
```

Then run the broad feasible suite:

```bash
python -m pytest tests/factor tests/api/test_data_helpers.py tests/api/test_backtest_helpers.py -q
make
```
