"""Polars-native helpers shared by ExposureProvider implementations.

These helpers avoid any pandas import in the aligner package while still being
able to consume :class:`DataLayer.load`'s pandas output (which lives outside
the aligner module and is not part of this lane's red-line scope).

Functions here use duck typing — ``index.values`` and ``.values`` are read off
the input objects without ever calling ``import pandas``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import polars as pl


def pd_panel_to_polars(panel: Any, target_ts_dtype: pl.DataType = pl.Datetime("ns")) -> pl.DataFrame:
    """Convert a panel DataFrame (pandas DatetimeIndex × symbol cols *or*
    polars wide-table ``[ts, sym...]``) into the canonical polars layout.

    Two input shapes are accepted:

    * **Polars wide-table** (preferred, post DataLayer-polars migration) —
      a :class:`polars.DataFrame` whose first column ``ts`` already carries
      Datetime values.  The frame is returned unchanged after a dtype cast
      on ``ts`` to match ``target_ts_dtype``.
    * **Pandas-style frame** (legacy / test fixtures still using pandas) —
      detected via duck-typing (``index.values`` + ``.values``).  Bridged
      to polars via numpy so this function never imports pandas.

    Empty input → ``pl.DataFrame({"ts": []})`` with the requested dtype.

    Parameters
    ----------
    panel:
        Either a polars wide-table DataFrame or a pandas-style DataFrame.
        ``None`` is treated as empty.
    target_ts_dtype:
        Target dtype for the ``ts`` column.  Defaults to ``pl.Datetime("ns")``.
    """
    if panel is None:
        return pl.DataFrame({"ts": pl.Series("ts", [], dtype=target_ts_dtype)})

    # Polars frame fast path — recognise via ``columns`` attribute being a
    # plain list (pandas exposes an Index, polars exposes ``list[str]``).
    if isinstance(panel, pl.DataFrame):
        if panel.is_empty() or "ts" not in panel.columns:
            return pl.DataFrame({"ts": pl.Series("ts", [], dtype=target_ts_dtype)})
        symbol_cols = [c for c in panel.columns if c != "ts"]
        if not symbol_cols:
            return panel.select([pl.col("ts").cast(target_ts_dtype)])
        # Cast ts to the requested dtype; symbol columns are left untouched.
        return panel.with_columns(pl.col("ts").cast(target_ts_dtype)).select(
            ["ts", *symbol_cols]
        )

    # Pandas duck-typed fallback (kept for tests that still mock with pandas).
    if len(panel.columns) == 0:
        return pl.DataFrame({"ts": pl.Series("ts", [], dtype=target_ts_dtype)})

    # Bridge via numpy: datetime64[ns] → int64 ns
    ts_ns = np.asarray(panel.index.values, dtype="datetime64[ns]").astype("int64")
    ts_pl = pl.Series("ts", ts_ns).cast(target_ts_dtype)

    arr = np.asarray(panel.values, dtype=np.float64)  # (T, N)
    cols: dict[str, pl.Series | Any] = {"ts": ts_pl}
    for j, sym in enumerate(panel.columns):
        col_arr = arr[:, j]
        # NaN → null in polars (Series ctor accepts NaN; cast to f64 keeps them as NaN.
        # We map NaN → null explicitly via list comprehension to ensure null_count matches.)
        cols[str(sym)] = pl.Series(
            str(sym),
            [None if (np.isnan(v) if isinstance(v, float) else v is None) else float(v)
             for v in col_arr],
            dtype=pl.Float64,
        )
    return pl.DataFrame(cols)


def normalize_ts_naive(ts_series: pl.Series) -> pl.Series:
    """Return ``ts_series`` with timezone stripped (naive datetime, ns precision).

    Used by Aligner to ensure panel ts values and provider exposure ts values
    are comparable after both sides hash through ``dict`` keys.  Without this,
    a tz-aware datetime never matches its tz-naive counterpart.
    """
    if ts_series.dtype == pl.Datetime:
        # cast to a known precision/no-tz form
        return ts_series.cast(pl.Datetime("ns", time_zone=None))
    return ts_series


def ns_to_datetime(ns: int) -> datetime:
    """Convert int64 nanoseconds-since-epoch to a tz-naive Python datetime.

    Uses numpy datetime64 → ISO string → datetime to avoid any pandas bridge.
    Microsecond precision is preserved; sub-microsecond information is truncated.
    """
    dt64 = np.datetime64(ns, "ns")
    iso = str(dt64)
    if "." in iso:
        head, frac = iso.split(".")
        frac_us = frac[:6].ljust(6, "0")
        iso = f"{head}.{frac_us}"
    return datetime.fromisoformat(iso)
