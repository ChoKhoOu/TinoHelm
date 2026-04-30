"""Research-native panel primitives for factor evaluation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

import numpy as np
import polars as pl


_TS = "ts"
_SYMBOL = "symbol"
_RESERVED_SYMBOLS = {_TS}


@dataclass(frozen=True)
class CanonicalBars:
    """Canonical long-form bar data from a single source and interval."""

    frame: pl.DataFrame
    source: str
    interval: str
    symbols: tuple[str, ...] = ()


@dataclass(frozen=True)
class MatrixPanel:
    """Dense time x symbol matrix with explicit axis metadata."""

    ts: np.ndarray
    symbols: tuple[str, ...]
    values: np.ndarray

    def validate(self) -> None:
        if self.values.ndim != 2:
            raise ValueError("MatrixPanel.values must be a 2D array")
        expected_shape = (len(self.ts), len(self.symbols))
        if self.values.shape != expected_shape:
            raise ValueError(
                f"MatrixPanel shape mismatch: values={self.values.shape}, "
                f"expected={expected_shape}"
            )
        _validate_symbols(self.symbols, "MatrixPanel symbols")
        ts_ns = _timestamps_to_ns(self.ts)
        if len(ts_ns) > 1 and np.any(np.diff(ts_ns) <= 0):
            raise ValueError("MatrixPanel.ts must be strictly increasing")

    def astype(self, dtype: np.dtype | str) -> "MatrixPanel":
        return MatrixPanel(self.ts.copy(), self.symbols, self.values.astype(dtype, copy=True))

    def normalized_ts(self) -> np.ndarray:
        return _timestamps_to_ns(self.ts).astype("datetime64[ns]")


def _validate_symbols(symbols: Sequence[str], label: str) -> None:
    if len(set(symbols)) != len(symbols):
        raise ValueError(f"{label} must be unique; duplicate symbols found")
    reserved = _RESERVED_SYMBOLS.intersection(symbols)
    if reserved:
        raise ValueError(f"{label} contain reserved names: {sorted(reserved)!r}")


def _timestamps_to_ns(values: np.ndarray) -> np.ndarray:
    arr = np.asarray(values)
    if np.issubdtype(arr.dtype, np.datetime64):
        ts = arr.astype("datetime64[ns]")
        if np.any(np.isnat(ts)):
            raise ValueError("MatrixPanel.ts must not contain NaT")
        return ts.astype(np.int64)
    out = np.array([_timestamp_scalar_to_ns(value) for value in arr], dtype=np.int64)
    if np.any(out == np.iinfo(np.int64).min):
        raise ValueError("MatrixPanel.ts must not contain NaT")
    return out


def _timestamp_scalar_to_ns(value: object) -> int:
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        ts = np.datetime64(value, "ns")
    else:
        ts = np.datetime64(value, "ns")
    if np.isnat(ts):
        raise ValueError("MatrixPanel.ts must not contain NaT")
    return int(ts.astype(np.int64))


def assert_unique_ts_symbol(frame: pl.DataFrame) -> None:
    """Raise if a long frame has duplicate ``(ts, symbol)`` keys."""

    missing = { _TS, _SYMBOL } - set(frame.columns)
    if missing:
        raise ValueError(f"frame missing required columns: {sorted(missing)!r}")
    duplicates = frame.group_by([_TS, _SYMBOL]).len().filter(pl.col("len") > 1)
    if duplicates.height:
        sample = duplicates.head(3).to_dicts()
        raise ValueError(f"duplicate (ts, symbol) rows found: {sample!r}")


def canonicalize_long_bars(
    frame: pl.DataFrame,
    fields: Sequence[str],
    source: str,
    interval: str,
    symbols: Sequence[str] | None = None,
) -> CanonicalBars:
    """Normalize long bars to ``ts, symbol, fields...`` sorted by key."""

    required = [_TS, _SYMBOL, *fields]
    missing = [col for col in required if col not in frame.columns]
    if missing:
        raise ValueError(f"bars missing required columns: {missing!r}")

    expressions: list[pl.Expr] = [
        pl.col(_TS).cast(pl.Datetime("ns")).alias(_TS),
        pl.col(_SYMBOL).cast(pl.Utf8).alias(_SYMBOL),
    ]
    expressions.extend(pl.col(field).cast(pl.Float64).alias(field) for field in fields)
    out = frame.select(expressions).sort([_TS, _SYMBOL])
    assert_unique_ts_symbol(out)
    canonical_symbols = tuple(symbols) if symbols is not None else tuple(sorted(out[_SYMBOL].unique().to_list()))
    _validate_symbols(canonical_symbols, "CanonicalBars symbols")
    return CanonicalBars(frame=out, source=source, interval=interval, symbols=canonical_symbols)


def wide_to_matrix(panel: pl.DataFrame, dtype: np.dtype | str = np.float64) -> MatrixPanel:
    """Convert a wide frame ``[ts, symbol1, ...]`` into a ``MatrixPanel``."""

    if _TS not in panel.columns:
        raise ValueError("wide panel missing required 'ts' column")
    symbols = tuple(col for col in panel.columns if col != _TS)
    ordered = panel.sort(_TS)
    ts = ordered.select(pl.col(_TS).cast(pl.Datetime("ns"))).to_series().to_numpy()
    if symbols:
        values = ordered.select([pl.col(col).cast(pl.Float64) for col in symbols]).to_numpy()
    else:
        values = np.empty((ordered.height, 0), dtype=dtype)
    values = values.astype(dtype, copy=False)
    matrix = MatrixPanel(ts=ts, symbols=symbols, values=values)
    matrix.validate()
    return matrix


def matrix_to_wide(panel: MatrixPanel) -> pl.DataFrame:
    """Convert a ``MatrixPanel`` back to wide ``[ts, symbol1, ...]`` form."""

    panel.validate()
    data: dict[str, object] = {_TS: panel.normalized_ts()}
    for idx, symbol in enumerate(panel.symbols):
        data[symbol] = panel.values[:, idx]
    return pl.DataFrame(data).with_columns(pl.col(_TS).cast(pl.Datetime("ns")))


def long_to_wide_panels(bars: CanonicalBars, fields: Sequence[str]) -> dict[str, pl.DataFrame]:
    """Pivot canonical long bars into one wide panel per requested field."""

    assert_unique_ts_symbol(bars.frame)
    outputs: dict[str, pl.DataFrame] = {}
    for field in fields:
        if field not in bars.frame.columns:
            raise ValueError(f"bars missing requested field {field!r}")
        wide = (
            bars.frame.select([_TS, _SYMBOL, field])
            .pivot(index=_TS, on=_SYMBOL, values=field, aggregate_function="first")
            .sort(_TS)
        )
        if bars.symbols:
            for symbol in bars.symbols:
                if symbol not in wide.columns:
                    wide = wide.with_columns(pl.lit(None, dtype=pl.Float64).alias(symbol))
            wide = wide.select([_TS, *bars.symbols])
        outputs[field] = wide
    return outputs
