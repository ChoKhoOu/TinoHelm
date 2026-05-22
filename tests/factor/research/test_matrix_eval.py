from datetime import datetime, timezone

import numpy as np
import polars as pl

from tinohelm.factor.research.matrix_eval import (
    compute_ic_matrix,
    rank_rows,
    rowwise_corr,
    summarize_ic_matrix,
)
from tinohelm.factor.research.panel import MatrixPanel


def _panel(values):
    rows = len(values)
    ts = np.array([f"2024-01-{day:02d}" for day in range(1, rows + 1)], dtype="datetime64[ns]")
    return MatrixPanel(ts=ts, symbols=("a", "b", "c", "d"), values=np.array(values, dtype=float))


def _wide_panel(rows: int, cols: int) -> MatrixPanel:
    ts = np.array([f"2024-01-{day:02d}" for day in range(1, rows + 1)], dtype="datetime64[ns]")
    symbols = tuple(f"s{i:02d}" for i in range(cols))
    values = np.tile(np.arange(cols, dtype=float), (rows, 1)) + np.arange(rows, dtype=float)[:, None]
    return MatrixPanel(ts=ts, symbols=symbols, values=values)


def _matrix_to_long(panel: MatrixPanel) -> pl.DataFrame:
    rows = []
    for row_idx, ts in enumerate(panel.ts.astype("datetime64[ns]")):
        for col_idx, symbol in enumerate(panel.symbols):
            value = panel.values[row_idx, col_idx]
            if np.isfinite(value):
                rows.append({"ts": ts, "symbol": symbol, "value": float(value)})
    return pl.DataFrame(rows).with_columns(pl.col("ts").cast(pl.Datetime("ns")))


def _legacy_like_ic_series(factor: pl.DataFrame, fwd: pl.DataFrame) -> pl.DataFrame:
    paired = (
        factor.rename({"value": "factor"})
        .join(fwd.rename({"value": "fwd_ret"}), on=["ts", "symbol"], how="inner")
        .drop_nulls()
        .filter(pl.col("factor").is_finite() & pl.col("fwd_ret").is_finite())
    )
    if paired.height < 30:
        return pl.DataFrame(schema={"date": pl.Utf8, "ic": pl.Float64})
    results = []
    bucketed = paired.with_columns(pl.col("ts").dt.truncate("1d").alias("bucket"))
    for (bucket_dt,), group in bucketed.group_by(["bucket"], maintain_order=True):
        if group.height < 20:
            continue
        ic_val = group.select(pl.corr("factor", "fwd_ret", method="spearman").alias("ic")).item()
        if ic_val is not None and np.isfinite(ic_val):
            results.append({"date": bucket_dt.isoformat(), "ic": round(float(ic_val), 6)})
    return pl.DataFrame(results, schema={"date": pl.Utf8, "ic": pl.Float64}) if results else pl.DataFrame(schema={"date": pl.Utf8, "ic": pl.Float64})


def test_perfect_and_inverse_spearman():
    factor = _panel([[1, 2, 3, 4], [1, 2, 3, 4]])
    fwd = _panel([[10, 20, 30, 40], [40, 30, 20, 10]])

    ic = compute_ic_matrix(factor, fwd, min_valid=4, freq=None, min_total_pairs=0)

    assert ic["ic"].to_list() == [1.0, -1.0]


def test_nan_pairs_dropped_per_row_and_min_valid_nan():
    x = np.array([[1, 2, np.nan, 4], [1, np.nan, np.nan, 4]], dtype=float)
    y = np.array([[1, 2, 99, 4], [1, 2, 3, 4]], dtype=float)

    corr = rowwise_corr(x, y, min_valid=3)

    assert corr[0] == 1.0
    assert np.isnan(corr[1])


def test_spearman_ranks_after_pairwise_nan_drop():
    factor = _panel([[1, 2, 3, 4]])
    fwd = _panel([[10, np.nan, 30, 40]])

    ic = compute_ic_matrix(factor, fwd, min_valid=3, freq=None, min_total_pairs=0)

    assert ic["ic"].to_list() == [1.0]


def test_rank_rows_preserves_nans_and_average_ties():
    ranks = rank_rows(np.array([[3.0, np.nan, 1.0, 1.0]]))

    assert np.isnan(ranks[0, 1])
    np.testing.assert_allclose(ranks[0, [0, 2, 3]], [3.0, 1.5, 1.5])


def test_summary_matches_existing_rounding_semantics():
    ic = pl.DataFrame({"date": ["a", "b", "c"], "ic": [0.1, 0.2, -0.4]})

    assert summarize_ic_matrix(ic) == {
        "ic_mean": -0.033333,
        "ic_std": 0.262467,
        "ir": -0.127,
        "ic_tstat": -0.22,
        "ic_positive_pct": 0.6667,
        "ic_max_abs": 0.4,
    }


def test_matrix_ic_matches_polars_reference_on_fixture():
    factor = _panel([[1, 2, 3, 4], [4, 3, 2, 1]])
    fwd = _panel([[2, 4, 6, 8], [1, 2, 3, 4]])

    ic = compute_ic_matrix(factor, fwd, method="pearson", min_valid=4, freq=None, min_total_pairs=0)
    expected = []
    for factor_row, fwd_row in zip(factor.values, fwd.values):
        expected.append(
            pl.DataFrame({"factor": factor_row, "fwd": fwd_row})
            .select(pl.corr("factor", "fwd", method="pearson"))
            .item()
        )

    np.testing.assert_allclose(ic["ic"].to_numpy(), np.round(expected, 6))


def test_matrix_ic_default_preserves_global_pair_threshold():
    factor = _wide_panel(rows=1, cols=4)
    fwd = _wide_panel(rows=1, cols=4)

    ic = compute_ic_matrix(factor, fwd, min_valid=4)

    assert ic.height == 0


def test_matrix_ic_matches_legacy_bucketed_series_contract():
    factor = _wide_panel(rows=2, cols=25)
    fwd = MatrixPanel(
        ts=factor.ts.copy(),
        symbols=factor.symbols,
        values=factor.values * 2.0 + 1.0,
    )
    factor.values[0, 0] = np.nan
    fwd.values[1, 1] = np.nan

    matrix_ic = compute_ic_matrix(factor, fwd, method="spearman", min_valid=20, freq="D", min_total_pairs=30)
    legacy_ic = _legacy_like_ic_series(_matrix_to_long(factor), _matrix_to_long(fwd))

    assert matrix_ic.to_dicts() == legacy_ic.to_dicts()


def test_matrix_ic_buckets_timezone_aware_timestamps_on_utc_boundaries():
    symbols = tuple(f"s{i:02d}" for i in range(25))
    values = np.tile(np.arange(25, dtype=float), (2, 1))
    ts = np.array([
        datetime(2024, 1, 1, 23, 30, tzinfo=timezone.utc),
        datetime(2024, 1, 2, 0, 30, tzinfo=timezone.utc),
    ], dtype=object)
    factor = MatrixPanel(ts=ts, symbols=symbols, values=values)
    fwd = MatrixPanel(ts=ts.copy(), symbols=symbols, values=values * 2.0)

    ic = compute_ic_matrix(factor, fwd, method="spearman", min_valid=20, freq="D", min_total_pairs=30)

    assert ic.to_dicts() == [
        {"date": "2024-01-01T00:00:00", "ic": 1.0},
        {"date": "2024-01-02T00:00:00", "ic": 1.0},
    ]
