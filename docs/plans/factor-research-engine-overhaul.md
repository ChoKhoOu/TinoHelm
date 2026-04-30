# Factor Research Engine Overhaul Implementation Plan

> **For Hermes:** Use Codex CLI for implementation assistance, but Hermes remains responsible for diff review and verification. Use strict TDD for every production change.

**Goal:** Build a research-native factor path that keeps NautilusTrader compatibility at the storage/semantic boundary while removing NT `Bar` object materialization, repeated alignment, repeated forward-return computation, and per-factor serial evaluation from the hot path.

**Architecture:** Add a new `tinohelm.factor.research` layer beside the existing `DataLayer`/`Evaluator` instead of rewriting the current API in one risky patch. The new layer exposes canonical long bars, matrix panels, a direct parquet reader contract, forward-return cache, and vectorized/batch IC evaluation. Existing `DataLayer` and `Evaluator.evaluate()` stay backward compatible; `Orchestrator.batch_run()` can opt into the new batch evaluator without breaking single-factor callers.

**Tech Stack:** Python 3.12 target, Polars, NumPy, existing `tinohelm.data.catalog_helpers` for interval/path semantics, existing factor dataclasses in `tinohelm.factor.types`.

---

## Non-negotiable contracts

### NT compatibility boundary

Research code does **not** depend on NT runtime objects in its hot path. Compatibility is enforced through data semantics:

- Same catalog roots as NT ingestion.
- Same source routing via `resolve_catalog_path(base, source_type)`.
- Same interval validation via `interval_to_nanoseconds()` / `interval_to_step_unit()`.
- Same close-time semantics: bar timestamp is `ts_event` / close timestamp, never window-left timestamp.
- Same symbol normalization expectations as existing `DataRequest.symbol`.

The old `ParquetDataCatalog.bars()` path may stay as a reference/test oracle, not the research hot path.

### Data invariants

Every canonical long frame must satisfy:

```text
columns include: ts, symbol, requested fields
unique key: (ts, symbol)
ordered by: ts, symbol
source is never mixed inside one frame
invalid interval raises ValueError; no fallback to 1m/default
missing asset/warmup/PIT-unavailable cells are NaN/null, never 0-filled
```

### Time invariants

```text
bar ts = close timestamp
resample ts = max child ts / target close timestamp
forward_returns[t] = close[t+h] / close[t] - 1
last h rows per symbol are NaN/null
rolling windows only consume <= t data
PIT universe mask is as-of t only
```

### Matrix invariants

```text
MatrixPanel.ts is sorted ascending
MatrixPanel.symbols defines column order
MatrixPanel.values shape == (len(ts), len(symbols))
missing values are np.nan
float64 is default; float32 is optional only after tolerance tests
```

---

## Phase 1 — Research panel primitives

### Task 1.1: Add panel dataclasses and validation helpers

**Files:**
- Create: `src/tinohelm/factor/research/__init__.py`
- Create: `src/tinohelm/factor/research/panel.py`
- Test: `tests/factor/research/test_panel.py`

**Required API:**

```python
@dataclass(frozen=True)
class CanonicalBars:
    frame: pl.DataFrame
    source: str
    interval: str

@dataclass(frozen=True)
class MatrixPanel:
    ts: np.ndarray
    symbols: tuple[str, ...]
    values: np.ndarray

    def validate(self) -> None: ...
    def astype(self, dtype: np.dtype | str) -> "MatrixPanel": ...
```

Helper functions:

```python
assert_unique_ts_symbol(frame: pl.DataFrame) -> None
canonicalize_long_bars(frame, fields, source, interval) -> CanonicalBars
wide_to_matrix(panel: pl.DataFrame, dtype=np.float64) -> MatrixPanel
matrix_to_wide(panel: MatrixPanel) -> pl.DataFrame
long_to_wide_panels(bars: CanonicalBars, fields: Sequence[str]) -> dict[str, pl.DataFrame]
```

**Tests:**

- duplicate `(ts, symbol)` raises.
- output sorted by `(ts, symbol)`.
- wide -> matrix -> wide preserves values, symbols, ts order.
- nulls become `np.nan` in matrix.
- `MatrixPanel.validate()` rejects shape mismatch and non-monotonic timestamps.

---

## Phase 2 — Research-native reader contract

### Task 2.1: Add direct parquet reader shell

**Files:**
- Create: `src/tinohelm/factor/research/reader.py`
- Test: `tests/factor/research/test_reader.py`

**Required API:**

```python
@dataclass(frozen=True)
class ResearchDataRequest:
    symbols: tuple[str, ...]
    fields: tuple[str, ...]
    interval: str
    start: datetime | None
    end: datetime | None
    source: str = "klines"

class ResearchParquetReader:
    def __init__(self, catalog_root: Path): ...
    def load_bars(self, request: ResearchDataRequest) -> CanonicalBars: ...
```

Implementation constraints:

- Validate interval with `interval_to_nanoseconds()` before IO.
- Resolve source-aware root first: `resolve_catalog_path(catalog_root, request.source)`.
- Build candidate roots with legacy fallback, de-duplicated.
- Use `pl.scan_parquet(..., glob=True)` where possible.
- Project only required columns: `ts_event`, symbol identity, requested OHLCV fields.
- Normalize output to `ts: Datetime(ns)`, `symbol: Utf8`, requested fields as `Float64`.
- Check uniqueness and sort.

**Compatibility strategy:**

Start with a permissive schema normalizer supporting both fixture-style columns and NT-style columns:

```text
ts_event or ts or timestamp_ns -> ts
symbol or instrument_id or bar_type-derived symbol -> symbol
open/high/low/close/volume -> float fields
```

If a real NT parquet schema lacks symbol columns and symbol cannot be inferred, raise a clear error instead of silently returning wrong data.

**Tests:**

- invalid interval rejected before scanning.
- source-aware root preferred before legacy.
- fields projection returns only requested fields plus `ts/symbol`.
- duplicate `(ts, symbol)` raises.
- time filter respects start/end.
- empty data returns valid empty canonical frame.

---

## Phase 3 — Forward returns store/cache

### Task 3.1: Add forward-return matrix functions

**Files:**
- Create: `src/tinohelm/factor/research/returns.py`
- Test: `tests/factor/research/test_returns.py`

**Required API:**

```python
@dataclass(frozen=True)
class ForwardReturnsKey:
    close_key: str
    periods: tuple[int, ...]
    log_ret: bool

class ForwardReturnsStore:
    def get_or_compute(self, close: MatrixPanel, periods: Sequence[int], log_ret=False) -> dict[int, MatrixPanel]: ...

compute_forward_returns_matrix(close: MatrixPanel, period: int, log_ret=False) -> MatrixPanel
```

**Tests:**

- simple forward return aligns to current timestamp.
- last `period` rows are NaN.
- period <= 0 raises.
- zero/non-finite close pair emits NaN.
- cache returns same object/result for repeated key.

---

## Phase 4 — Matrix IC evaluator

### Task 4.1: Add vectorized row-wise correlation and IC

**Files:**
- Create: `src/tinohelm/factor/research/matrix_eval.py`
- Test: `tests/factor/research/test_matrix_eval.py`

**Required API:**

```python
rowwise_corr(x: np.ndarray, y: np.ndarray, min_valid: int = 20) -> np.ndarray
rank_rows(values: np.ndarray) -> np.ndarray
compute_ic_matrix(
    factor: MatrixPanel,
    forward_returns: MatrixPanel,
    method: Literal["spearman", "pearson"] = "spearman",
    min_valid: int = 20,
) -> pl.DataFrame
summarize_ic_matrix(ic_series: pl.DataFrame) -> dict[str, float]
```

**Tests:**

- perfect monotone Spearman = 1.
- inverse monotone Spearman = -1.
- NaN pairs dropped per row.
- rows below `min_valid` yield NaN/dropped.
- output summary matches existing `compute_ic_summary()` rounding semantics.
- matrix IC matches existing polars reference on deterministic fixtures.

---

## Phase 5 — Batch evaluator

### Task 5.1: Add multi-factor batch IC evaluator

**Files:**
- Create/modify: `src/tinohelm/factor/research/batch.py`
- Test: `tests/factor/research/test_batch.py`

**Required API:**

```python
@dataclass(frozen=True)
class BatchEvalResult:
    ic_series: dict[str, pl.DataFrame]
    summaries: dict[str, dict[str, float]]

class BatchFactorEvaluator:
    def evaluate_ic(
        self,
        factors: Mapping[str, MatrixPanel],
        close: MatrixPanel,
        periods: Sequence[int],
        method: str = "spearman",
    ) -> dict[str, dict[int, dict[str, float]]]: ...
```

**Tests:**

- two factors share one forward-return computation.
- result for each factor/period matches single matrix evaluator.
- mismatched ts/symbol order raises explicit error.

---

## Phase 6 — Existing Evaluator/Orchestrator integration

### Task 6.1: Add opt-in batch evaluator path

**Files:**
- Modify: `src/tinohelm/factor/evaluation/evaluator.py`
- Modify: `src/tinohelm/factor/engine/orchestrator.py`
- Test: `tests/factor/test_e2e_batch.py` or new targeted tests.

**Rules:**

- Do not remove existing `Evaluator.evaluate()` behavior.
- Add `Evaluator.evaluate_batch_ic(...)` or a thin adapter to `BatchFactorEvaluator`.
- `Orchestrator.batch_run()` can use the batch evaluator only for the IC/IR core first; keep quantile/turnover legacy path until separately vectorized.
- Fall back to old per-factor eval for unsupported config (`full=True`, segments, neutralize, non-close returns) until fully covered.

**Tests:**

- batch path returns same IC/IR as per-factor path on deterministic small fixtures.
- unsupported config falls back to old evaluator.

---

## Phase 7 — Cache split

### Task 7.1: Extend FactorCache without breaking old lookup/store

**Files:**
- Modify: `src/tinohelm/factor/cache.py`
- Test: `tests/factor/test_cache.py`

Add namespaced key builders and accessors:

```python
build_raw_data_key(...)
build_factor_values_key(...)
build_forward_returns_key(...)
build_eval_key(...)
get_matrix_panel(namespace, key)
put_matrix_panel(namespace, key, panel)
```

Keep existing `lookup()` / `store()` intact for backward compatibility.

**Tests:**

- eval key changes when eval config changes but factor value key does not.
- data version changes raw/factor keys.
- matrix panel roundtrip preserves ts/symbol/value/NaN.

---

## Rollout discipline

1. Land phases 1/3/4/5 as pure research modules first. They are NT-free and low blast radius.
2. Land reader in phase 2 with strict tests and reference fixtures before using it in production flow.
3. Integrate with `Evaluator` and `Orchestrator` only after matrix path equivalence tests are green.
4. Keep old path as fallback until production data fixtures prove direct reader equivalence.

## Verification commands

```bash
PYTHONPATH=src pytest -q tests/factor/research
PYTHONPATH=src pytest -q tests/factor/evaluation/test_ic.py tests/factor/test_cache.py
PYTHONPATH=src pytest -q tests/factor/test_e2e_batch.py
PYTHONPATH=src python -m compileall src/tinohelm/factor
```

## Out of scope

- Nautilus backtest runner optimization.
- Optuna/parameter optimization worker changes.
- Frontend UI changes.
- Changing repo-local git identity.
