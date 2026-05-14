"""DataLayer — multi-symbol parallel data loading with time alignment and PIT filtering.

Design
------
- Reads bar Parquet directly with column projection to avoid NT Bar object materialisation.
- Reuses ``tinohelm.data.funding_cache._load_cache`` for funding_rate JSON reads.
- Parallel per-symbol loading via ``ThreadPoolExecutor``.
- Time alignment: non-bar sources (funding_rate) are forward-filled onto the bar index.
  funding_rate timestamps are shifted by +1ns before ffill to implement strict
  backward-asof visibility (avoid look-ahead bias: a rate published at T is only
  visible after T, not on a bar timestamped exactly T).
- PIT filtering: for each timestamp, symbols absent from ``Universe.get_symbols_at``
  are set to NaN (shape is preserved — rows are NOT dropped).

Polars-native contract
----------------------
This module is the *only* layer in :mod:`tinohelm.factor` that touches the
local NautilusTrader-style data catalog. Bar data is read directly with Polars
column projection; non-bar readers convert external inputs to
:class:`polars.DataFrame` immediately on the boundary so the rest of the
framework stays pandas-free.

Internal Series helpers (``_load_*``) return a 2-column ``polars.DataFrame``
with columns ``[ts, value]`` (Datetime[ns], Float64). The public
:meth:`DataLayer.load` returns a ``dict[str, Panel]`` where each ``Panel``
is a ``polars.DataFrame`` with columns ``[ts, sym1, sym2, ...]`` —
matching :data:`tinohelm.factor.types.Panel`.

Supported field_name values
---------------------------
Bar fields (source="bar"):
    ``close``, ``open``, ``high``, ``low``, ``volume``
Funding rate (source="funding_rate"):
    ``funding_rate``
Market cap (source="market_cap"):
    ``market_cap``  (close × current circulating_supply snapshot; non-PIT for
    historical research unless replaced with a dated supply series)
Trade-tick derived (source="trade_tick"):
    ``trade_price``, ``trade_qty``, ``trade_side``, ``signed_trade_qty``,
    ``buy_qty``, ``sell_qty``, ``trade_imbalance``
Quote-tick derived (source="quote_tick"):
    ``bid_price``, ``bid_qty``, ``ask_price``, ``ask_qty``, ``mid_price``,
    ``spread_bps``, ``depth_l1_usd``, ``orderbook_imbalance``
Metrics/OI (source="metrics" or "open_interest"):
    ``open_interest``, ``sum_open_interest``, ``open_interest_value`` and
    Binance long/short ratio fields

Frequency strings
-----------------
DataRequest.frequency follows the short-form NT interval convention used by
``catalog_helpers.INTERVAL_MAP``: ``"1m"``, ``"5m"``, ``"1h"``, etc.
For funding_rate, ``"8h"`` is the canonical value.
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence

import polars as pl

from tinohelm.core.paths import paths
from tinohelm.data.storage import CatalogStorageProvider, StorageObject, is_remote_storage
from tinohelm.factor.types import DataRequest, EventRequest, Panel
from tinohelm.factor.universe import Universe
from tinohelm.strategy.loader_helpers import normalize_symbol as _normalize_symbol

logger = logging.getLogger(__name__)


def _collect_lazy_streaming(lazy_frame: pl.LazyFrame) -> pl.DataFrame:
    """Collect a lazy query with Polars streaming engine when available."""
    try:
        return lazy_frame.collect(engine="streaming")
    except TypeError:
        return lazy_frame.collect(streaming=True)


def _storage_parquet_files(
    storage: CatalogStorageProvider,
    prefix: Path,
    *,
    recursive: bool = False,
) -> list[StorageObject]:
    return sorted(
        storage.iter_files(prefix, suffix=".parquet", recursive=recursive),
        key=lambda obj: str(obj.path),
    )


def _schema_names(schema: pl.Schema | dict[str, pl.DataType]) -> list[str]:
    names = getattr(schema, "names", None)
    if callable(names):
        return list(names())
    return list(schema.keys())


def _polars_schema_from_arrow(arrow_schema: object) -> pl.Schema:
    import pyarrow as pa

    arrays = []
    names = []
    for field in arrow_schema:
        names.append(field.name)
        arrays.append(pa.array([], type=field.type))
    return pl.from_arrow(pa.Table.from_arrays(arrays, names=names)).schema


def _parquet_schema(
    storage: CatalogStorageProvider,
    file: StorageObject,
    *,
    row_index_name: str | None = None,
) -> pl.Schema:
    if not is_remote_storage(storage):
        return pl.scan_parquet(str(file.path), row_index_name=row_index_name).collect_schema()
    import pyarrow.parquet as pq

    with storage.open_input_file(file) as fh:
        schema = _polars_schema_from_arrow(pq.ParquetFile(fh, pre_buffer=False).schema_arrow)
    if row_index_name is not None and row_index_name not in schema:
        schema = pl.Schema({row_index_name: pl.UInt32, **dict(schema)})
    return schema


def _read_parquet_frame(
    storage: CatalogStorageProvider,
    file: StorageObject,
    *,
    columns: Sequence[str] | None = None,
    row_index_name: str | None = None,
) -> pl.DataFrame:
    if not is_remote_storage(storage):
        lf = pl.scan_parquet(str(file.path), row_index_name=row_index_name)
        if columns is not None:
            schema = lf.collect_schema()
            selected = [column for column in columns if column in schema]
            if row_index_name is not None and row_index_name not in selected:
                selected.append(row_index_name)
            lf = lf.select(selected)
        return _collect_lazy_streaming(lf)

    import pyarrow.parquet as pq

    with storage.open_input_file(file) as fh:
        parquet = pq.ParquetFile(fh, pre_buffer=False)
        available = set(parquet.schema_arrow.names)
        read_columns = None if columns is None else [column for column in columns if column in available]
        table = parquet.read(columns=read_columns, use_threads=True)
    frame = pl.from_arrow(table)
    if row_index_name is not None:
        frame = frame.with_row_index(row_index_name)
    return frame


def _parquet_lazy_frame(
    storage: CatalogStorageProvider,
    file: StorageObject,
    *,
    row_index_name: str | None = None,
    columns: Sequence[str] | None = None,
) -> tuple[pl.LazyFrame, pl.Schema]:
    schema = _parquet_schema(storage, file, row_index_name=row_index_name)
    if not is_remote_storage(storage):
        lf = pl.scan_parquet(str(file.path), row_index_name=row_index_name)
        if columns is not None:
            selected = [column for column in columns if column in schema]
            if row_index_name is not None and row_index_name in schema and row_index_name not in selected:
                selected.append(row_index_name)
            lf = lf.select(selected)
        return lf, schema
    frame = _read_parquet_frame(storage, file, columns=columns, row_index_name=row_index_name)
    return frame.lazy(), schema

# Column name conventions for the internal 2-col series frames and the
# wide-table panel returned by :meth:`DataLayer.load`.
_TS_COL: str = "ts"
_VAL_COL: str = "value"

# ---------------------------------------------------------------------------
# Bar field → NT Bar attribute name
# ---------------------------------------------------------------------------

_BAR_FIELD_ATTR: dict[str, str] = {
    "close": "close",
    "__eval_close": "close",
    "open": "open",
    "high": "high",
    "low": "low",
    "volume": "volume",
}

_QUOTE_TICK_FIELDS: frozenset[str] = frozenset({
    "bid_price", "bid_qty", "ask_price", "ask_qty", "mid_price",
    "spread_bps", "depth_l1_usd", "orderbook_imbalance",
})
_TRADE_TICK_FIELDS: frozenset[str] = frozenset({
    "trade_price", "trade_qty", "trade_side", "signed_trade_qty",
    "buy_qty", "sell_qty", "trade_imbalance",
})
_TRADE_EVENT_FIELDS: frozenset[str] = (_TRADE_TICK_FIELDS - frozenset({"trade_imbalance"})) | frozenset({"trade_id"})
_TRADE_TICK_SOURCE_TYPES: frozenset[str] = frozenset({"aggTrades", "trades"})
_QUOTE_TICK_SOURCE_TYPES: frozenset[str] = frozenset({"bookTicker"})
_METRICS_FIELDS: frozenset[str] = frozenset({
    "open_interest", "sum_open_interest", "open_interest_value",
    "toptrader_long_short_ratio_count", "toptrader_long_short_ratio_sum",
    "global_long_short_ratio", "taker_long_short_vol_ratio",
})
_BOOK_DEPTH_FIELDS: frozenset[str] = frozenset({"book_depth", "book_depth_notional", "depth", "notional"})
_BAR_CLOSE_OFFSET = pl.duration(nanoseconds=1_000_000)

_NAUTILUS_FIXED_PRECISION_SCALE: float = 10_000_000_000_000_000.0
_DEFAULT_BAR_SOURCE_TYPE: str = "klines"

_BAR_INTERVAL_RE = re.compile(r"^([1-9]\d*)([smhd])$")
_BAR_UNIT_TO_AGGREGATION: dict[str, str] = {
    "s": "SECOND",
    "m": "MINUTE",
    "h": "HOUR",
    "d": "DAY",
}
_BAR_AGGREGATION_SECONDS: dict[str, int] = {
    "SECOND": 1,
    "MINUTE": 60,
    "HOUR": 3_600,
    "DAY": 86_400,
}
_BAR_AGGREGATION_POLARS_UNIT: dict[str, str] = {
    "SECOND": "s",
    "MINUTE": "m",
    "HOUR": "h",
    "DAY": "d",
}


@dataclass(frozen=True)
class _BarInterval:
    """Strictly parsed bar interval token."""

    token: str
    step: int
    aggregation: str

    @property
    def ns(self) -> int:
        return self.step * _BAR_AGGREGATION_SECONDS[self.aggregation] * 1_000_000_000

    @property
    def nt_part(self) -> str:
        return f"{self.step}-{self.aggregation}"

    @property
    def polars_every(self) -> str:
        return f"{self.step}{_BAR_AGGREGATION_POLARS_UNIT[self.aggregation]}"


@dataclass(frozen=True)
class _TickFileCandidate:
    file: StorageObject
    source_priority: int
    file_ordinal: int
    source_type: str
    source_aware: bool

    @property
    def path(self) -> Path:
        return self.file.path


# ---------------------------------------------------------------------------
# Internal helpers — series construction
# ---------------------------------------------------------------------------

def _empty_series_frame() -> pl.DataFrame:
    """Return an empty 2-col ``[ts, value]`` polars frame with correct dtypes."""
    return pl.DataFrame(
        {_TS_COL: [], _VAL_COL: []},
        schema={_TS_COL: pl.Datetime("ns"), _VAL_COL: pl.Float64},
    )


def _empty_trade_tick_events_frame(fields: Sequence[str]) -> pl.DataFrame:
    """Return an empty raw trade-tick event frame preserving requested columns."""
    numeric_fields = {
        "trade_price",
        "trade_qty",
        "trade_side",
        "signed_trade_qty",
        "buy_qty",
        "sell_qty",
    }
    schema: dict[str, pl.DataType] = {_TS_COL: pl.Datetime("ns")}
    for field in fields:
        schema[field] = pl.Utf8 if field == "trade_id" else pl.Float64 if field in numeric_fields else pl.Null
    return pl.DataFrame(schema=schema)


def _empty_quote_tick_events_frame(fields: Sequence[str]) -> pl.DataFrame:
    """Return an empty raw quote-tick event frame preserving requested columns."""
    schema: dict[str, pl.DataType] = {_TS_COL: pl.Datetime("ns")}
    for field in fields:
        schema[field] = pl.Float64
    return pl.DataFrame(schema=schema)


def _validate_trade_tick_source_type(source_type: str | None) -> None:
    if source_type is not None and source_type not in _TRADE_TICK_SOURCE_TYPES:
        allowed = ", ".join(sorted(_TRADE_TICK_SOURCE_TYPES))
        raise ValueError(f"Unknown trade_tick source_type {source_type!r}; supported: {allowed}")


def _validate_quote_tick_source_type(source_type: str | None) -> None:
    if source_type is not None and source_type not in _QUOTE_TICK_SOURCE_TYPES:
        allowed = ", ".join(sorted(_QUOTE_TICK_SOURCE_TYPES))
        raise ValueError(f"Unknown quote_tick source_type {source_type!r}; supported: {allowed}")


def _add_panel_result(result: dict[str, Panel], field_name: str, panel: Panel, source: str) -> None:
    if field_name in result:
        raise ValueError(
            "DataLayer output key collision for "
            f"{source} field {field_name!r}; request a single frequency/source_type per field"
        )
    result[field_name] = panel


def _build_series_frame(
    timestamps: Sequence[datetime],
    values: Sequence[float],
) -> pl.DataFrame:
    """Construct a sorted, deduplicated ``[ts, value]`` frame.

    Mirrors the legacy pandas ``Series.sort_index()`` +
    ``Series[~index.duplicated(keep="last")]`` behaviour used by the
    pre-polars implementation.
    """
    if not timestamps:
        return _empty_series_frame()
    df = pl.DataFrame(
        {_TS_COL: list(timestamps), _VAL_COL: [float(v) for v in values]},
        schema={_TS_COL: pl.Datetime("ns"), _VAL_COL: pl.Float64},
    )
    # Sort then keep the *last* row per timestamp so duplicates resolve to
    # the most recent value (matches ``keep="last"``).
    df = df.sort(_TS_COL).unique(subset=[_TS_COL], keep="last", maintain_order=True)
    return df


def _first_panel_timestamps(panels: dict[str, Panel]) -> list[datetime]:
    """Return the first non-empty panel timestamp index from a loaded panel dict."""
    for panel in panels.values():
        if not panel.is_empty() and _TS_COL in panel.columns:
            return panel[_TS_COL].to_list()
    return []


def _ordered_unique(values: Iterable[str]) -> list[str]:
    """Return unique strings in first-seen order."""
    return list(dict.fromkeys(values))


def _bar_source_type(req: DataRequest) -> str:
    """Return the physical bar source type for a request."""
    return req.source_type or _DEFAULT_BAR_SOURCE_TYPE


def _group_bar_requests(
    requests: Sequence[DataRequest],
) -> dict[tuple[str, str], list[DataRequest]]:
    """Group bar requests by (frequency, source_type) preserving order."""
    groups: dict[tuple[str, str], list[DataRequest]] = defaultdict(list)
    for req in requests:
        groups[(req.frequency, _bar_source_type(req))].append(req)
    return groups


def _parse_bar_frequency(frequency: str) -> _BarInterval:
    """Parse a bar frequency strictly; never fall back to 1m on bad input."""
    token = str(frequency).strip().lower()
    try:
        from tinohelm.data.catalog_helpers import interval_to_step_unit

        step, aggregation = interval_to_step_unit(token)
    except ValueError:
        match = _BAR_INTERVAL_RE.match(token)
        if match is None:
            raise ValueError(
                f"Unsupported bar frequency {frequency!r}. Expected '<positive integer><s|m|h|d>', "
                "for example '1m', '6m', '1h', or '1d'."
            ) from None
        step = int(match.group(1))
        aggregation = _BAR_UNIT_TO_AGGREGATION[match.group(2)]

    if step <= 0 or aggregation not in _BAR_AGGREGATION_SECONDS:
        raise ValueError(
            f"Unsupported bar frequency {frequency!r}. Expected a positive s/m/h/d interval."
        )
    return _BarInterval(token=token, step=step, aggregation=aggregation)


def _make_bar_type_strict(symbol: str, frequency: str) -> str:
    """Build an NT bar-type string using strict frequency parsing."""
    from tinohelm.strategy.loader_helpers import normalize_symbol

    interval = _parse_bar_frequency(frequency)
    return f"{normalize_symbol(symbol)}-{interval.nt_part}-LAST-EXTERNAL"


def _bar_catalog_roots(base_root: Path, source_type: str | None) -> list[Path]:
    """Return preferred catalog roots for bar reads."""
    from tinohelm.data.catalog_helpers import resolve_catalog_path
    from tinohelm.data.pipeline_helpers import WRITE_CATEGORY

    base = Path(base_root)
    source_type = source_type or _DEFAULT_BAR_SOURCE_TYPE
    bar_source_types = {src for src, category in WRITE_CATEGORY.items() if category == "bar"}
    if source_type not in bar_source_types:
        raise ValueError(
            f"Unknown bar source_type {source_type!r}. Supported: {sorted(bar_source_types)}"
        )

    resolved = resolve_catalog_path(base, source_type)
    candidates = [resolved]

    if source_type == _DEFAULT_BAR_SOURCE_TYPE:
        candidates.append(base)

    # If the caller already passed a resolved source root (e.g. .../bar/klines),
    # prefer it over appending another /bar/klines suffix.
    if base.name == source_type and base.parent.name == "bar":
        candidates = [base, resolved]

    out: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        key = path.resolve() if path.exists() else path.absolute()
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _candidate_source_frequencies(target: _BarInterval) -> list[str]:
    """Return lower intervals that can compose ``target`` exactly."""
    from tinohelm.data.catalog_helpers import INTERVAL_MAP

    candidates: list[tuple[int, str]] = []
    for token in INTERVAL_MAP:
        try:
            candidate = _parse_bar_frequency(token)
        except ValueError:
            continue
        if candidate.ns < target.ns and target.ns % candidate.ns == 0:
            candidates.append((candidate.ns, token))

    # Coarsest usable source first minimizes scanned rows; 1m naturally remains
    # the final fallback for custom intervals such as 6m when only base klines
    # are stored.
    return [token for _, token in sorted(candidates, reverse=True)]


def _fallback_source_window(
    start: datetime | None,
    end: datetime | None,
    target: _BarInterval,
    source: _BarInterval,
) -> tuple[datetime | None, datetime | None]:
    """Bound fallback source reads while preserving complete target bars."""
    expansion_ns = max(target.ns - source.ns, 0)
    source_start = (
        _ns_to_datetime(_datetime_to_ns(start) - expansion_ns)
        if start is not None
        else None
    )
    source_end = (
        _ns_to_datetime(_datetime_to_ns(end) + expansion_ns)
        if end is not None
        else None
    )
    return source_start, source_end


def _tick_panel_raw_end(end: datetime | None, interval: _BarInterval) -> datetime | None:
    """Extend a requested panel end to the enclosing raw tick bar boundary."""
    if end is None:
        return None
    end_ns = _datetime_to_ns(end)
    boundary_ns = ((end_ns + interval.ns - 1) // interval.ns) * interval.ns
    return _ns_to_datetime(boundary_ns)


def _tick_panel_raw_start(start: datetime | None, interval: _BarInterval) -> datetime | None:
    """Floor a requested panel start to the enclosing raw tick bar boundary."""
    if start is None:
        return None
    start_ns = _datetime_to_ns(start)
    boundary_ns = (start_ns // interval.ns) * interval.ns
    return _ns_to_datetime(boundary_ns)


def _apply_lookback_start(
    start: datetime | None,
    frequency: str,
    lookback: int,
) -> datetime | None:
    """Shift a load start back by ``lookback`` bars when possible."""
    if start is None:
        return None
    offset_ns = _lookback_offset_ns(frequency, lookback)
    if offset_ns is None:
        return start
    return start - timedelta(microseconds=offset_ns // 1_000)


def _bar_value_expr(field: str, dtype: pl.DataType) -> pl.Expr:
    """Decode a Nautilus fixed-precision bar column to Float64."""
    col = pl.col(field)
    if dtype.is_numeric():
        return col.cast(pl.Float64)
    if dtype == pl.Binary:
        try:
            return (
                col.bin.reinterpret(dtype=pl.Int128, endianness="little")
                .cast(pl.Float64)
                / _NAUTILUS_FIXED_PRECISION_SCALE
            )
        except AttributeError:
            return col.map_elements(
                _decode_nautilus_fixed_precision,
                return_dtype=pl.Float64,
            )
    logger.warning("Unsupported bar column dtype for %s: %s", field, dtype)
    return pl.lit(None, dtype=pl.Float64)


def _decode_nautilus_fixed_precision(value: bytes | bytearray | memoryview | None) -> float | None:
    """Decode little-endian signed Int128 fixed precision used by Nautilus."""
    if value is None:
        return None
    raw = bytes(value)
    if not raw:
        return None
    return int.from_bytes(raw, byteorder="little", signed=True) / _NAUTILUS_FIXED_PRECISION_SCALE


def _fixed_precision_expr(column: str, dtype: pl.DataType) -> pl.Expr:
    col = pl.col(column)
    if dtype.is_numeric():
        return col.cast(pl.Float64)
    if dtype == pl.Binary:
        try:
            return (
                col.bin.reinterpret(dtype=pl.Int128, endianness="little")
                .cast(pl.Float64)
                / _NAUTILUS_FIXED_PRECISION_SCALE
            )
        except AttributeError:
            return col.map_elements(_decode_nautilus_fixed_precision, return_dtype=pl.Float64)
    return pl.lit(None, dtype=pl.Float64)


@lru_cache(maxsize=4096)
def _cached_local_parquet_ts_event_range(
    path_str: str,
    mtime_ns: int,
    size: int,
) -> tuple[int | None, int | None] | None:
    result: tuple[int | None, int | None] | None = None
    path = Path(path_str)
    try:
        import pyarrow.parquet as pq

        metadata = pq.ParquetFile(path).metadata
        result = _ts_event_range_from_metadata(metadata)
        if result is None:
            table = pq.read_table(path, columns=["ts_event"])
            column = table.column("ts_event")
            if len(column) > 0:
                result = (int(column.combine_chunks().to_numpy().min()), int(column.combine_chunks().to_numpy().max()))
    except Exception:
        try:
            stats = _collect_lazy_streaming(
                pl.scan_parquet(str(path)).select([
                    pl.col("ts_event").min().alias("min_ts_event"),
                    pl.col("ts_event").max().alias("max_ts_event"),
                ])
            )
            if not stats.is_empty():
                result = (int(stats["min_ts_event"][0]), int(stats["max_ts_event"][0]))
        except Exception:
            result = None

    return result


def _ts_event_range_from_metadata(metadata: object) -> tuple[int | None, int | None] | None:
    mins: list[int] = []
    maxs: list[int] = []
    for row_group_idx in range(metadata.num_row_groups):
        row_group = metadata.row_group(row_group_idx)
        for column_idx in range(row_group.num_columns):
            column = row_group.column(column_idx)
            if column.path_in_schema != "ts_event" or column.statistics is None:
                continue
            stats = column.statistics
            if stats.has_min_max:
                mins.append(int(stats.min))
                maxs.append(int(stats.max))
    if mins and maxs:
        return (min(mins), max(maxs))
    return None


def _parquet_ts_event_range(
    storage: CatalogStorageProvider,
    file: StorageObject,
) -> tuple[int | None, int | None] | None:
    """Return min/max ts_event ns for a Parquet file, or ``None`` if unknown."""
    if not is_remote_storage(storage):
        try:
            stat = file.path.stat()
        except OSError:
            return None
        return _cached_local_parquet_ts_event_range(str(file.path), stat.st_mtime_ns, stat.st_size)

    try:
        import pyarrow.parquet as pq

        with storage.open_input_file(file) as fh:
            parquet = pq.ParquetFile(fh, pre_buffer=False)
            result = _ts_event_range_from_metadata(parquet.metadata)
            if result is not None:
                return result
            table = parquet.read(columns=["ts_event"], use_threads=True)
            column = table.column("ts_event")
            if len(column) > 0:
                return (int(column.combine_chunks().to_numpy().min()), int(column.combine_chunks().to_numpy().max()))
    except Exception:
        return None
    return None

_parquet_ts_event_range.cache_info = _cached_local_parquet_ts_event_range.cache_info  # type: ignore[attr-defined]


def _prune_parquet_files_by_time(
    storage_or_files: CatalogStorageProvider | Sequence[Path] | Sequence[StorageObject],
    files_or_start: Sequence[StorageObject] | datetime | None,
    start_or_end: datetime | None = None,
    end: datetime | None = None,
) -> list[StorageObject] | list[Path]:
    """Select files whose ts_event metadata overlaps an inclusive window.

    The public helper historically accepted ``(paths, start, end)`` and
    returned ``Path`` objects.  Provider-aware callers pass
    ``(storage, StorageObject[], start, end)`` and keep remote object metadata.
    """
    from tinohelm.data.storage import LocalCatalogStorage

    provider_like = hasattr(storage_or_files, "iter_files") and hasattr(storage_or_files, "provider")
    return_paths = not provider_like
    if provider_like:
        storage = storage_or_files  # type: ignore[assignment]
        files = list(files_or_start or ())  # type: ignore[arg-type]
        start = start_or_end
    else:
        storage = LocalCatalogStorage(Path("."))
        raw_paths = [Path(path) for path in storage_or_files]  # type: ignore[union-attr]
        files = [StorageObject(key=str(path), path=path) for path in raw_paths]
        start = files_or_start if isinstance(files_or_start, datetime) or files_or_start is None else None
        end = start_or_end

    if start is None and end is None:
        return [file.path for file in files] if return_paths else files
    start_ns = _datetime_to_ns(start) if start is not None else None
    end_ns = _datetime_to_ns(end) if end is not None else None
    selected: list[StorageObject] = []
    for file in files:
        file_range = _parquet_ts_event_range(storage, file)
        if file_range is None or file_range[0] is None or file_range[1] is None:
            selected.append(file)
            continue
        min_ns, max_ns = file_range
        if start_ns is not None and max_ns < start_ns:
            continue
        if end_ns is not None and min_ns > end_ns:
            continue
        selected.append(file)
    return [file.path for file in selected] if return_paths else selected


def _trade_tick_required_columns(fields: Sequence[str]) -> list[str]:
    """Return ordered physical trade-tick columns needed for derived fields."""
    required: list[str] = ["ts_event"]
    for field in fields:
        if field == "trade_price":
            required.extend(["price", "trade_price"])
        elif field in {"trade_qty", "signed_trade_qty", "buy_qty", "sell_qty", "trade_imbalance"}:
            required.extend(["size", "trade_qty"])
        if field in {"trade_side", "signed_trade_qty", "buy_qty", "sell_qty", "trade_imbalance"}:
            required.append("aggressor_side")
        if field == "trade_id":
            required.append("trade_id")
    return _ordered_unique(required)


def _quote_tick_required_columns(fields: Sequence[str]) -> list[str]:
    """Return ordered physical quote-tick columns needed for derived fields."""
    return ["ts_event", "bid_price", "bid_size", "ask_price", "ask_size", "update_id", "sequence"]


def _available_projection(schema: dict[str, pl.DataType], wanted: Sequence[str]) -> list[str]:
    return [column for column in wanted if column in schema]


def _empty_trade_normalized_frame() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "ts_event": pl.Int64,
            _TS_COL: pl.Datetime("ns"),
            "trade_price": pl.Float64,
            "trade_qty": pl.Float64,
            "trade_side": pl.Float64,
            "trade_id": pl.Utf8,
            "signed_trade_qty": pl.Float64,
            "buy_qty": pl.Float64,
            "sell_qty": pl.Float64,
            "_source_priority": pl.Int64,
            "_file_ordinal": pl.Int64,
            "_row_ordinal": pl.UInt32,
        }
    )


def _empty_quote_normalized_frame() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "ts_event": pl.Int64,
            _TS_COL: pl.Datetime("ns"),
            "bid_price": pl.Float64,
            "bid_qty": pl.Float64,
            "ask_price": pl.Float64,
            "ask_qty": pl.Float64,
            "mid_price": pl.Float64,
            "spread_bps": pl.Float64,
            "depth_l1_usd": pl.Float64,
            "orderbook_imbalance": pl.Float64,
            "update_id": pl.Utf8,
            "sequence": pl.Utf8,
            "_source_priority": pl.Int64,
            "_file_ordinal": pl.Int64,
            "_row_ordinal": pl.UInt32,
        }
    )


def _quote_frame_to_series(
    frame: pl.DataFrame,
    field_name: str,
    frequency: str,
    start: datetime | None,
    end: datetime | None,
) -> pl.DataFrame:
    if frame.is_empty() or "ts_event" not in frame.columns:
        return _empty_series_frame()
    required = ["bid_price", "bid_size", "ask_price", "ask_size"]
    if any(column not in frame.columns for column in required):
        return _empty_series_frame()
    schema = frame.schema
    decoded = frame.with_columns(
        pl.from_epoch(pl.col("ts_event"), time_unit="ns").alias(_TS_COL),
        _fixed_precision_expr("bid_price", schema["bid_price"]).alias("bid_price"),
        _fixed_precision_expr("bid_size", schema["bid_size"]).alias("bid_qty"),
        _fixed_precision_expr("ask_price", schema["ask_price"]).alias("ask_price"),
        _fixed_precision_expr("ask_size", schema["ask_size"]).alias("ask_qty"),
    ).with_columns(
        ((pl.col("bid_price") + pl.col("ask_price")) / 2.0).alias("mid_price"),
        ((pl.col("ask_price") - pl.col("bid_price")) / ((pl.col("bid_price") + pl.col("ask_price")) / 2.0) * 10_000.0).alias("spread_bps"),
        (pl.col("bid_price") * pl.col("bid_qty") + pl.col("ask_price") * pl.col("ask_qty")).alias("depth_l1_usd"),
        ((pl.col("bid_qty") - pl.col("ask_qty")) / (pl.col("bid_qty") + pl.col("ask_qty"))).alias("orderbook_imbalance"),
    )
    decoded = _filter_time_range(decoded, start, end)
    if decoded.is_empty():
        return _empty_series_frame()
    interval = _parse_bar_frequency(frequency)
    return (
        decoded.sort(_TS_COL)
        .group_by_dynamic(
            _TS_COL,
            every=interval.polars_every,
            period=interval.polars_every,
            closed="right",
            label="right",
        )
        .agg(pl.col(field_name).last().alias(_VAL_COL))
        .with_columns((pl.col(_TS_COL) - _BAR_CLOSE_OFFSET).alias(_TS_COL))
        .drop_nulls(_VAL_COL)
        .select([_TS_COL, _VAL_COL])
        .sort(_TS_COL)
    )


def _trade_side_expr() -> pl.Expr:
    side = pl.col("aggressor_side")
    if hasattr(side, "str"):
        text_side = side.cast(pl.Utf8, strict=False).str.to_uppercase()
        return (
            pl.when(text_side.is_in(["1", "BUYER", "BUY", "BID"])).then(1.0)
            .when(text_side.is_in(["2", "SELLER", "SELL", "ASK"])).then(-1.0)
            .otherwise(None)
        )
    return pl.lit(None, dtype=pl.Float64)


def _trade_frame_to_series(
    frame: pl.DataFrame,
    field_name: str,
    frequency: str,
    start: datetime | None,
    end: datetime | None,
) -> pl.DataFrame:
    if frame.is_empty() or "ts_event" not in frame.columns:
        return _empty_series_frame()
    price_col = "price" if "price" in frame.columns else "trade_price"
    size_col = "size" if "size" in frame.columns else "trade_qty"
    if price_col not in frame.columns or size_col not in frame.columns:
        return _empty_series_frame()
    schema = frame.schema
    decoded = frame.with_columns(
        pl.from_epoch(pl.col("ts_event"), time_unit="ns").alias(_TS_COL),
        _fixed_precision_expr(price_col, schema[price_col]).alias("trade_price"),
        _fixed_precision_expr(size_col, schema[size_col]).alias("trade_qty"),
        _trade_side_expr().alias("trade_side"),
    ).with_columns(
        (pl.col("trade_qty") * pl.col("trade_side")).alias("signed_trade_qty"),
        pl.when(pl.col("trade_side") > 0).then(pl.col("trade_qty")).otherwise(0.0).alias("buy_qty"),
        pl.when(pl.col("trade_side") < 0).then(pl.col("trade_qty")).otherwise(0.0).alias("sell_qty"),
    )
    decoded = _filter_time_range(decoded.select([_TS_COL, "trade_price", "trade_qty", "trade_side", "signed_trade_qty", "buy_qty", "sell_qty"]), start, end)
    if decoded.is_empty():
        return _empty_series_frame()
    interval = _parse_bar_frequency(frequency)
    grouped = decoded.sort(_TS_COL).group_by_dynamic(
        _TS_COL,
        every=interval.polars_every,
        period=interval.polars_every,
        closed="right",
        label="right",
    )
    if field_name == "trade_imbalance":
        return (
            grouped.agg([
                pl.col("buy_qty").sum().alias("_buy"),
                pl.col("sell_qty").sum().alias("_sell"),
                pl.col("trade_qty").sum().alias("_total"),
            ])
            .with_columns((pl.col(_TS_COL) - _BAR_CLOSE_OFFSET).alias(_TS_COL))
            .filter(pl.col("_total") > 0)
            .with_columns(((pl.col("_buy") - pl.col("_sell")) / pl.col("_total")).alias(_VAL_COL))
            .select([_TS_COL, _VAL_COL])
            .sort(_TS_COL)
        )
    agg_expr = (
        pl.col(field_name).last().alias(_VAL_COL)
        if field_name in {"trade_price", "trade_side"}
        else pl.col(field_name).sum().alias(_VAL_COL)
    )
    return (
        grouped.agg(agg_expr)
        .with_columns((pl.col(_TS_COL) - _BAR_CLOSE_OFFSET).alias(_TS_COL))
        .drop_nulls(_VAL_COL)
        .select([_TS_COL, _VAL_COL])
        .sort(_TS_COL)
    )


def _raw_frame_field_to_series(
    frame: pl.DataFrame,
    field_name: str,
    frequency: str | None = None,
) -> pl.DataFrame:
    if frame.is_empty() or "ts_event" not in frame.columns or field_name not in frame.columns:
        return _empty_series_frame()
    series = (
        frame.select([
            pl.from_epoch(pl.col("ts_event"), time_unit="ns").alias(_TS_COL),
            pl.col(field_name).cast(pl.Float64).alias(_VAL_COL),
        ])
        .sort(_TS_COL)
        .unique(subset=[_TS_COL], keep="last", maintain_order=True)
    )
    if frequency is None:
        return series
    interval = _parse_bar_frequency(frequency)
    return (
        series.sort(_TS_COL)
        .group_by_dynamic(
            _TS_COL,
            every=interval.polars_every,
            period=interval.polars_every,
            closed="right",
            label="right",
        )
        .agg(pl.col(_VAL_COL).last().alias(_VAL_COL))
        .with_columns((pl.col(_TS_COL) - _BAR_CLOSE_OFFSET).alias(_TS_COL))
        .drop_nulls(_VAL_COL)
        .select([_TS_COL, _VAL_COL])
        .sort(_TS_COL)
    )


# ---------------------------------------------------------------------------
# DataLayer
# ---------------------------------------------------------------------------

class DataLayer:
    """Load multi-symbol data panels from the local Parquet/JSON catalog.

    Parameters
    ----------
    universe:
        Universe object used for PIT symbol filtering.
    catalog_root:
        Path to the NautilusTrader ``ParquetDataCatalog`` root.  Defaults to
        ``~/.tino/data/catalog``.
    funding_dir:
        Path to the funding-rate JSON directory.  Defaults to
        ``~/.tino/data/funding_rates``.  Each file is ``{symbol.lower()}.json``.
    max_workers:
        Thread-pool size for parallel symbol loading.  Default: 4.
    """

    def __init__(
        self,
        universe: Universe,
        catalog_root: Path | None = None,
        funding_dir: Path | None = None,
        max_workers: int = 4,
    ) -> None:
        self._universe = universe
        self._catalog_root = Path(catalog_root) if catalog_root is not None else paths.get("catalog")
        self._funding_dir = Path(funding_dir) if funding_dir is not None else paths.get("funding_rates")
        self._max_workers = max_workers
        from tinohelm.data.storage import get_catalog_storage
        self._storage = get_catalog_storage(catalog_root=self._catalog_root)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(
        self,
        request: DataRequest | list[DataRequest],
        start: str | datetime | None = None,
        end: str | datetime | None = None,
    ) -> dict[str, Panel]:
        """Load data for one or more DataRequests and return a Panel dict.

        Parameters
        ----------
        request:
            A single ``DataRequest`` or a list thereof.  Requests with the
            same ``field_name`` are grouped into one Panel (columns = symbols).
        start:
            Override start for all requests.  ISO-8601 string or ``datetime``.
        end:
            Override end for all requests.  ISO-8601 string or ``datetime``.

        Returns
        -------
        dict[str, Panel]
            Keys are ``field_name`` strings; values are :class:`polars.DataFrame`
            panels with ``[ts, sym1, sym2, ...]`` columns (UTC, tz-naive
            ``Datetime("ns")``).
        """
        return self.load_panel(request, start=start, end=end)

    def load_panel(
        self,
        request: DataRequest | list[DataRequest],
        start: str | datetime | None = None,
        end: str | datetime | None = None,
    ) -> dict[str, Panel]:
        """Load panel data for one or more DataRequests.

        Raw tick requests (``frequency="tick"``) are intentionally rejected;
        use :meth:`load_events` for one-row-per-event data.
        """
        requests: list[DataRequest] = (
            [request] if isinstance(request, DataRequest) else list(request)
        )
        for req in requests:
            if req.source in {"trade_tick", "quote_tick"} and str(req.frequency).lower() == "tick":
                raise ValueError(
                    f"Raw {req.source} frequency='tick' requests must use DataLayer.load_events(); "
                    "panel loads require a bar aggregation frequency such as '1m'."
                )

        bar_requests = [req for req in requests if req.source == "bar"]
        non_bar_requests = [req for req in requests if req.source != "bar"]

        result: dict[str, Panel] = {}
        for (frequency, source_type), reqs in _group_bar_requests(bar_requests).items():
            panels = self._load_bar_panels_grouped(
                reqs=reqs,
                frequency=frequency,
                source_type=source_type,
                start=start,
                end=end,
            )
            for field_name, panel in panels.items():
                _add_panel_result(result, field_name, panel, "bar")

        trade_tick_requests = [req for req in non_bar_requests if req.source == "trade_tick"]
        quote_tick_requests = [req for req in non_bar_requests if req.source == "quote_tick"]
        other_non_bar_requests = [
            req for req in non_bar_requests if req.source not in {"trade_tick", "quote_tick"}
        ]

        for (frequency, source_type), reqs in self._group_tick_panel_requests(trade_tick_requests).items():
            _validate_trade_tick_source_type(source_type)
            panels = self._load_trade_tick_panels_grouped(reqs, frequency, start, end, source_type=source_type)
            for field_name, panel in panels.items():
                _add_panel_result(result, field_name, panel, "trade_tick")

        for (frequency, source_type), reqs in self._group_tick_panel_requests(quote_tick_requests).items():
            _validate_quote_tick_source_type(source_type)
            panels = self._load_quote_tick_panels_grouped(reqs, frequency, start, end, source_type=source_type)
            for field_name, panel in panels.items():
                _add_panel_result(result, field_name, panel, "quote_tick")

        # Group by (field_name, frequency, source) — each group → one Panel
        groups: dict[tuple[str, str, str], list[DataRequest]] = defaultdict(list)
        for req in other_non_bar_requests:
            groups[(req.field_name, req.frequency, req.source)].append(req)

        funding_groups: list[tuple[tuple[str, str, str], list[DataRequest]]] = []
        for key, reqs in groups.items():
            field_name, frequency, source = key
            if source == "funding_rate":
                # Funding panels need a bar-frequency reference index.  Defer
                # them until bar groups have been loaded so mixed requests like
                # [close, funding_rate] use the same timestamp grid.
                funding_groups.append((key, reqs))
                continue

            symbols = [r.symbol for r in reqs]
            # Use the max lookback across all requests for this group
            lookback = max(r.lookback for r in reqs)

            panel = self._load_panel(
                symbols=symbols,
                field_name=field_name,
                frequency=frequency,
                source=source,
                start=start,
                end=end,
                lookback=lookback,
            )
            _add_panel_result(result, field_name, panel, source)

        for (field_name, frequency, source), reqs in funding_groups:
            symbols = [r.symbol for r in reqs]
            lookback = max(r.lookback for r in reqs)
            reference_ts = _first_panel_timestamps(result)

            panel = self._load_panel(
                symbols=symbols,
                field_name=field_name,
                frequency=frequency,
                source=source,
                start=start,
                end=end,
                lookback=lookback,
                reference_ts=reference_ts,
            )
            _add_panel_result(result, field_name, panel, source)

        return result

    def load_events(
        self,
        request: EventRequest | None = None,
        *,
        symbol: str | None = None,
        source: str | None = None,
        fields: Sequence[str] | None = None,
        start: str | datetime | None = None,
        end: str | datetime | None = None,
        source_type: str | None = None,
        on_error: Literal["raise", "empty"] = "raise",
    ) -> pl.DataFrame:
        """Load raw tick/event rows without panel aggregation.

        This is a raw catalog API: it does not apply point-in-time universe
        filtering, listing-window filtering, aggregation, or panel alignment.
        Use :meth:`load_panel` or :meth:`load` for PIT-aligned factor panels.
        """
        if request is not None:
            symbol = request.symbol
            source = request.source
            fields = request.fields
            start = request.start if request.start is not None else start
            end = request.end if request.end is not None else end
            source_type = request.source_type if request.source_type is not None else source_type
            on_error = request.on_error
        if symbol is None or source is None or fields is None:
            raise ValueError("load_events requires symbol, source, and fields")
        if on_error not in {"raise", "empty"}:
            raise ValueError("load_events on_error must be 'raise' or 'empty'")
        if source not in {"trade_tick", "quote_tick"}:
            raise ValueError(f"Unknown event source {source!r}; supported: ['quote_tick', 'trade_tick']")
        ts_start = _parse_ts(start) if start is not None else None
        ts_end = _parse_ts(end) if end is not None else None
        if source == "trade_tick":
            if "trade_imbalance" in fields:
                raise ValueError(
                    "trade_imbalance is a window aggregate ratio and is not available in raw event rows; "
                    "use panel aggregation or request signed_trade_qty, buy_qty, or sell_qty instead."
                )
            unknown = [field for field in fields if field not in _TRADE_EVENT_FIELDS]
            if unknown:
                raise ValueError(f"Unknown trade_tick event field {unknown[0]!r}")
            _validate_trade_tick_source_type(source_type)
            return self._load_trade_tick_events(symbol, tuple(fields), ts_start, ts_end, source_type, on_error)

        unknown = [field for field in fields if field not in _QUOTE_TICK_FIELDS]
        if unknown:
            raise ValueError(f"Unknown quote_tick event field {unknown[0]!r}")
        _validate_quote_tick_source_type(source_type)
        return self._load_quote_tick_events(symbol, tuple(fields), ts_start, ts_end, source_type, on_error)

    @staticmethod
    def _group_tick_panel_requests(
        requests: Sequence[DataRequest],
    ) -> dict[tuple[str, str | None], list[DataRequest]]:
        groups: dict[tuple[str, str | None], list[DataRequest]] = defaultdict(list)
        for req in requests:
            groups[(req.frequency, req.source_type)].append(req)
        return groups

    # ------------------------------------------------------------------
    # Internal orchestration
    # ------------------------------------------------------------------

    def _load_panel(
        self,
        symbols: list[str],
        field_name: str,
        frequency: str,
        source: str,
        start: str | datetime | None,
        end: str | datetime | None,
        lookback: int,
        reference_ts: Sequence[datetime] | None = None,
    ) -> Panel:
        """Load one Panel (time × symbol) for a given field/frequency/source."""
        ts_start = _parse_ts(start) if start is not None else None
        ts_end = _parse_ts(end) if end is not None else None

        # Apply the warmup lookback offset: shift ``start`` earlier by
        # ``lookback × bar_duration`` so the kernel has enough history for its
        # first valid output inside the requested window.  Without this, a
        # factor like ``ret_N(lookback=20)`` would produce NaN for the first
        # 20 bars of every requested range (see Bug 008).
        ts_load_start = ts_start
        if ts_start is not None:
            offset_ns = _lookback_offset_ns(frequency, lookback)
            if offset_ns is not None:
                # ``datetime`` arithmetic uses microsecond precision; the
                # smallest bar interval supported (1m) is 60 × 10⁹ ns so
                # ns→μs truncation never loses data here.
                ts_load_start = ts_start - timedelta(microseconds=offset_ns // 1_000)

        # Parallel per-symbol load — each future returns a 2-col [ts, value] frame.
        series_by_symbol: dict[str, pl.DataFrame] = {}
        futures: dict = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            for sym in symbols:
                fut = pool.submit(
                    self._load_table,
                    symbol=sym,
                    field_name=field_name,
                    source=source,
                    frequency=frequency,
                    start=ts_load_start,
                    end=ts_end,
                )
                futures[fut] = sym

            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    series = fut.result()
                    series_by_symbol[sym] = series
                except Exception:
                    logger.warning(
                        "Failed to load %s/%s for symbol %s",
                        source, field_name, sym, exc_info=True,
                    )
                    series_by_symbol[sym] = _empty_series_frame()

        if not series_by_symbol:
            return pl.DataFrame()

        # Determine target bar-frequency index (from bar data)
        if source == "bar":
            panel = self._align_time(series_by_symbol)
        elif source == "funding_rate":
            # Apply as-of delay (shift(1) + ffill) onto a real bar-frequency
            # index.  Raw funding timestamps are sparse 8h settlement points;
            # using them as the target grid would misrepresent funding factors
            # as 8h-only panels and break mixed bar/funding evaluation.
            bar_ts = list(reference_ts or [])
            if not bar_ts:
                bar_ts = self._load_bar_reference_index(
                    symbols=symbols,
                    frequency=frequency,
                    start=ts_load_start,
                    end=ts_end,
                )
            bar_ts = sorted(dict.fromkeys(bar_ts))
            aligned_cols: dict[str, pl.DataFrame] = {
                sym: self._align_funding_onto_bar_index(series, bar_ts)
                for sym, series in series_by_symbol.items()
            }
            panel = (
                self._align_time(aligned_cols)
                if bar_ts
                else pl.DataFrame(
                    {col: [] for col in [_TS_COL, *symbols]},
                    schema={
                        _TS_COL: pl.Datetime("ns"),
                        **{s: pl.Float64 for s in symbols},
                    },
                )
            )
        else:
            # trade_tick / market_cap and others: simple union-index align.
            panel = self._align_time(series_by_symbol)

        # Apply PIT filter
        panel = self._apply_pit(panel)

        return panel

    def _load_bar_panels_grouped(
        self,
        reqs: list[DataRequest],
        frequency: str,
        source_type: str,
        start: str | datetime | None,
        end: str | datetime | None,
    ) -> dict[str, Panel]:
        """Load all requested bar fields for a frequency with one read per symbol.

        The public contract remains field-oriented (``dict[field] -> Panel``),
        but the catalog access pattern is symbol-oriented so OHLCV columns for a
        symbol/frequency are projected from Parquet once and then split into the
        requested field panels.
        """
        if not reqs:
            return {}

        # Validate before dispatching worker threads so invalid user input
        # fails closed instead of being swallowed by the per-symbol fallback.
        _parse_bar_frequency(frequency)

        ts_start = _parse_ts(start) if start is not None else None
        ts_end = _parse_ts(end) if end is not None else None

        symbols = _ordered_unique(req.symbol for req in reqs)
        fields = _ordered_unique(req.field_name for req in reqs)
        unknown_fields = [field for field in fields if field not in _BAR_FIELD_ATTR]
        if unknown_fields:
            raise ValueError(
                f"Unknown bar field {unknown_fields[0]!r}. Supported: {list(_BAR_FIELD_ATTR)}"
            )

        field_symbols: dict[str, list[str]] = {
            field: _ordered_unique(req.symbol for req in reqs if req.field_name == field)
            for field in fields
        }
        symbol_fields: dict[str, list[str]] = {
            sym: _ordered_unique(req.field_name for req in reqs if req.symbol == sym)
            for sym in symbols
        }
        physical_fields_by_symbol: dict[str, list[str]] = {
            sym: _ordered_unique(_BAR_FIELD_ATTR[field] for field in symbol_fields[sym])
            for sym in symbols
        }
        field_load_starts: dict[str, datetime | None] = {}
        for field in fields:
            lookback = max(req.lookback for req in reqs if req.field_name == field)
            field_load_starts[field] = _apply_lookback_start(ts_start, frequency, lookback)

        series_by_field_symbol: dict[str, dict[str, pl.DataFrame]] = {
            field: {} for field in fields
        }
        futures: dict[Any, str] = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            for sym in symbols:
                sym_fields = symbol_fields[sym]
                physical_fields = physical_fields_by_symbol[sym]
                load_start_candidates = [
                    field_load_starts[field]
                    for field in sym_fields
                    if field_load_starts[field] is not None
                ]
                sym_load_start = min(load_start_candidates) if load_start_candidates else None
                fut = pool.submit(
                    self._load_bar_fields,
                    symbol=sym,
                    field_names=physical_fields,
                    frequency=frequency,
                    source_type=source_type,
                    start=sym_load_start,
                    end=ts_end,
                )
                futures[fut] = sym

            for fut in as_completed(futures):
                sym = futures[fut]
                sym_fields = symbol_fields[sym]
                try:
                    series_by_field = fut.result()
                except ValueError:
                    raise
                except Exception:
                    logger.warning(
                        "Failed to load bar fields %s for symbol %s",
                        sym_fields, sym, exc_info=True,
                    )
                    series_by_field = {
                        _BAR_FIELD_ATTR[field]: _empty_series_frame()
                        for field in sym_fields
                    }

                for field in sym_fields:
                    series = series_by_field.get(_BAR_FIELD_ATTR[field], _empty_series_frame())
                    series_by_field_symbol[field][sym] = _filter_time_range(
                        series,
                        field_load_starts[field],
                        ts_end,
                    )

        panels: dict[str, Panel] = {}
        for field in fields:
            ordered_series = {
                sym: series_by_field_symbol[field].get(sym, _empty_series_frame())
                for sym in field_symbols[field]
            }
            panels[field] = self._apply_pit(self._align_time(ordered_series))
        return panels

    def _load_table(
        self,
        symbol: str,
        field_name: str,
        source: str,
        frequency: str,
        start: datetime | None,
        end: datetime | None,
    ) -> pl.DataFrame:
        """Load a single (symbol, field, freq) time series.

        Returns a ``polars.DataFrame`` with columns ``[ts, value]``
        (Datetime[ns] UTC tz-naive, Float64).
        """
        if source == "bar":
            return self._load_bar_field(symbol, field_name, frequency, start, end)
        elif source == "funding_rate":
            return self._load_funding_rate(symbol, start, end)
        elif source == "market_cap":
            return self._load_market_cap_field(symbol, frequency, start, end)
        elif source == "quote_tick":
            return self._load_quote_tick_field(symbol, field_name, frequency, start, end)
        elif source == "trade_tick":
            return self._load_trade_tick_field(symbol, field_name, frequency, start, end)
        elif source in {"open_interest", "metrics"}:
            return self._load_metrics_field(symbol, field_name, frequency, start, end)
        elif source in {"book_depth", "bookDepth"}:
            return self._load_book_depth_field(symbol, field_name, frequency, start, end)
        else:
            raise ValueError(f"Unknown data source: {source!r}")

    def _load_quote_tick_field(
        self,
        symbol: str,
        field_name: str,
        frequency: str,
        start: datetime | None,
        end: datetime | None,
    ) -> pl.DataFrame:
        if field_name not in _QUOTE_TICK_FIELDS:
            raise ValueError(f"Unknown quote_tick field {field_name!r}")
        return self._load_quote_tick_fields(symbol, [field_name], frequency, start, end).get(
            field_name,
            _empty_series_frame(),
        )

    def _quote_tick_roots(self, source_type: str | None = None) -> list[Path]:
        from tinohelm.data.catalog_helpers import resolve_catalog_path

        _validate_quote_tick_source_type(source_type)
        is_source_root = self._catalog_root.name == "bookTicker" and self._catalog_root.parent.name == "quotes"
        if source_type:
            if is_source_root:
                roots = [self._catalog_root] if self._catalog_root.name == source_type else []
            else:
                roots = [resolve_catalog_path(self._catalog_root, source_type)]
                if source_type == "bookTicker":
                    roots.append(self._catalog_root)
            return [Path(path) for path in _ordered_unique(str(path) for path in roots)]

        roots = [resolve_catalog_path(self._catalog_root, "bookTicker")]
        roots.append(self._catalog_root)
        if is_source_root:
            roots.insert(0, self._catalog_root)
        return [Path(path) for path in _ordered_unique(str(path) for path in roots)]

    def _quote_tick_candidates(
        self,
        symbol: str,
        start: datetime | None,
        end: datetime | None,
        source_type: str | None = None,
    ) -> list[_TickFileCandidate]:
        from tinohelm.strategy.loader_helpers import normalize_symbol

        candidates: list[_TickFileCandidate] = []
        file_ordinal = 0
        for source_priority, root in enumerate(self._quote_tick_roots(source_type)):
            root_path = Path(root)
            quote_dir = root_path / "data" / "quote_tick" / normalize_symbol(symbol)
            files = _prune_parquet_files_by_time(
                self._storage,
                _storage_parquet_files(self._storage, quote_dir),
                start,
                end,
            )
            source_aware = root_path.name == "bookTicker" and root_path.parent.name == "quotes"
            for file in files:
                candidates.append(_TickFileCandidate(file, source_priority, file_ordinal, "bookTicker", source_aware))
                file_ordinal += 1
        if any(candidate.source_aware for candidate in candidates):
            return [candidate for candidate in candidates if candidate.source_aware]
        return candidates

    def _quote_tick_files(
        self,
        symbol: str,
        start: datetime | None,
        end: datetime | None,
        source_type: str | None = None,
    ) -> list[Path]:
        return [candidate.path for candidate in self._quote_tick_candidates(symbol, start, end, source_type)]

    def _read_quote_tick_frame(
        self,
        symbol: str,
        start: datetime | None,
        end: datetime | None,
        source_type: str | None = None,
        *,
        strict: bool,
    ) -> pl.DataFrame:
        candidates = self._quote_tick_candidates(symbol, start, end, source_type)
        if not candidates:
            return _empty_quote_normalized_frame()
        frames: list[pl.DataFrame] = []
        required_columns = ["ts_event", "bid_price", "bid_size", "ask_price", "ask_size"]
        for candidate in candidates:
            lf, schema = _parquet_lazy_frame(
                self._storage,
                candidate.file,
                row_index_name="_row_ordinal",
                columns=_quote_tick_required_columns(required_columns),
            )
            missing = [column for column in required_columns if column not in schema]
            if missing:
                if strict:
                    raise ValueError(
                        f"Missing required quote_tick column(s) for raw events: {', '.join(missing)}"
                    )
                continue
            exprs = [
                pl.col("ts_event").cast(pl.Int64, strict=False),
                pl.from_epoch(pl.col("ts_event"), time_unit="ns").alias(_TS_COL),
                _fixed_precision_expr("bid_price", schema["bid_price"]).alias("bid_price"),
                _fixed_precision_expr("bid_size", schema["bid_size"]).alias("bid_qty"),
                _fixed_precision_expr("ask_price", schema["ask_price"]).alias("ask_price"),
                _fixed_precision_expr("ask_size", schema["ask_size"]).alias("ask_qty"),
                pl.col("update_id").cast(pl.Utf8, strict=False).alias("update_id") if "update_id" in schema else pl.lit(None, dtype=pl.Utf8).alias("update_id"),
                pl.col("sequence").cast(pl.Utf8, strict=False).alias("sequence") if "sequence" in schema else pl.lit(None, dtype=pl.Utf8).alias("sequence"),
                pl.lit(candidate.source_priority).alias("_source_priority"),
                pl.lit(candidate.file_ordinal).alias("_file_ordinal"),
            ]
            normalized = _collect_lazy_streaming(
                lf.with_columns(exprs).with_columns(
                    ((pl.col("bid_price") + pl.col("ask_price")) / 2.0).alias("mid_price"),
                    ((pl.col("ask_price") - pl.col("bid_price")) / ((pl.col("bid_price") + pl.col("ask_price")) / 2.0) * 10_000.0).alias("spread_bps"),
                    (pl.col("bid_price") * pl.col("bid_qty") + pl.col("ask_price") * pl.col("ask_qty")).alias("depth_l1_usd"),
                    ((pl.col("bid_qty") - pl.col("ask_qty")) / (pl.col("bid_qty") + pl.col("ask_qty"))).alias("orderbook_imbalance"),
                ).select(_empty_quote_normalized_frame().columns)
            )
            frames.append(normalized)
        if not frames:
            return _empty_quote_normalized_frame()
        frame = pl.concat(frames, how="diagonal_relaxed")
        if start is not None:
            frame = frame.filter(pl.col("ts_event") >= _datetime_to_ns(start))
        if end is not None:
            frame = frame.filter(pl.col("ts_event") <= _datetime_to_ns(end))
        if frame.is_empty():
            return _empty_quote_normalized_frame()
        frame = frame.with_columns(
            pl.when(pl.col("update_id").is_not_null()).then(pl.concat_str([pl.lit("u:"), pl.col("update_id")]))
            .when(pl.col("sequence").is_not_null()).then(pl.concat_str([pl.lit("s:"), pl.col("sequence")]))
            .otherwise(pl.concat_str([
                pl.lit("row:"),
                pl.col("ts_event").cast(pl.Utf8),
                pl.lit(":"),
                pl.col("bid_price").cast(pl.Utf8),
                pl.lit(":"),
                pl.col("bid_qty").cast(pl.Utf8),
                pl.lit(":"),
                pl.col("ask_price").cast(pl.Utf8),
                pl.lit(":"),
                pl.col("ask_qty").cast(pl.Utf8),
            ]))
            .alias("_dedupe_key"),
            pl.col("update_id").cast(pl.Int64, strict=False).alias("_update_id_num"),
            pl.col("sequence").cast(pl.Int64, strict=False).alias("_sequence_num"),
        )
        return (
            frame.sort(["_dedupe_key", "_source_priority", "_file_ordinal", "_row_ordinal"])
            .unique(subset=["_dedupe_key"], keep="first", maintain_order=True)
            .sort(["ts_event", "_update_id_num", "update_id", "_sequence_num", "sequence", "_source_priority", "_file_ordinal", "_row_ordinal"], nulls_last=True)
            .select(_empty_quote_normalized_frame().columns)
        )

    def _load_quote_tick_panels_grouped(
        self,
        reqs: list[DataRequest],
        frequency: str,
        start: str | datetime | None,
        end: str | datetime | None,
        source_type: str | None = None,
    ) -> dict[str, Panel]:
        """Load multiple quote-tick panel fields with one parquet scan per symbol."""
        if not reqs:
            return {}
        interval = _parse_bar_frequency(frequency)
        ts_start = _parse_ts(start) if start is not None else None
        ts_end = _parse_ts(end) if end is not None else None
        raw_end = _tick_panel_raw_end(ts_end, interval)
        fields = _ordered_unique(req.field_name for req in reqs)
        unknown = [field for field in fields if field not in _QUOTE_TICK_FIELDS]
        if unknown:
            raise ValueError(f"Unknown quote_tick field {unknown[0]!r}")

        symbols = _ordered_unique(req.symbol for req in reqs)
        field_symbols = {
            field: _ordered_unique(req.symbol for req in reqs if req.field_name == field)
            for field in fields
        }
        symbol_fields = {
            sym: _ordered_unique(req.field_name for req in reqs if req.symbol == sym)
            for sym in symbols
        }
        field_load_starts = {
            field: _apply_lookback_start(
                ts_start,
                frequency,
                max(req.lookback for req in reqs if req.field_name == field),
            )
            for field in fields
        }

        series_by_field_symbol: dict[str, dict[str, pl.DataFrame]] = {field: {} for field in fields}
        futures: dict[Any, str] = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            for sym in symbols:
                sym_fields = symbol_fields[sym]
                starts = [field_load_starts[field] for field in sym_fields if field_load_starts[field] is not None]
                sym_start = min(starts) if starts else None
                futures[pool.submit(self._load_quote_tick_fields, sym, sym_fields, frequency, sym_start, raw_end, source_type=source_type)] = sym

            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    series_by_field = fut.result()
                except ValueError:
                    raise
                except Exception:
                    logger.warning("Failed to load quote_tick fields for symbol %s", sym, exc_info=True)
                    series_by_field = {field: _empty_series_frame() for field in symbol_fields[sym]}
                for field in symbol_fields[sym]:
                    series_by_field_symbol[field][sym] = _filter_time_range(
                        series_by_field.get(field, _empty_series_frame()),
                        field_load_starts[field],
                        ts_end,
                    )

        panels: dict[str, Panel] = {}
        for field in fields:
            ordered = {
                sym: series_by_field_symbol[field].get(sym, _empty_series_frame())
                for sym in field_symbols[field]
            }
            panels[field] = self._apply_pit(self._align_time(ordered))
        return panels

    def _load_quote_tick_fields(
        self,
        symbol: str,
        field_names: Sequence[str],
        frequency: str,
        start: datetime | None,
        end: datetime | None,
        source_type: str | None = None,
    ) -> dict[str, pl.DataFrame]:
        unknown = [field for field in field_names if field not in _QUOTE_TICK_FIELDS]
        if unknown:
            raise ValueError(f"Unknown quote_tick field {unknown[0]!r}")
        interval = _parse_bar_frequency(frequency)
        raw_start = _tick_panel_raw_start(start, interval)
        raw_end = _tick_panel_raw_end(end, interval)
        empty = {field: _empty_series_frame() for field in field_names}
        try:
            frame = self._read_quote_tick_frame(symbol, raw_start, raw_end or end, source_type, strict=False)
            if frame.is_empty():
                return empty
            frame = _collect_lazy_streaming(
                frame.lazy()
                .group_by_dynamic(
                    _TS_COL,
                    every=interval.polars_every,
                    period=interval.polars_every,
                    closed="left",
                    label="right",
                )
                .agg([pl.col(field).last().alias(field) for field in field_names])
                .with_columns((pl.col(_TS_COL) - _BAR_CLOSE_OFFSET).alias(_TS_COL))
            )
        except Exception:
            logger.warning("Failed to read quote ticks for %s", symbol, exc_info=True)
            return empty
        if frame.is_empty():
            return empty
        out = dict(empty)
        for field in field_names:
            if field in frame.columns:
                out[field] = frame.drop_nulls(field).select([_TS_COL, pl.col(field).alias(_VAL_COL)]).sort(_TS_COL)
        return out

    def _trade_tick_root_groups(self, source_type: str | None = None) -> list[list[Path]]:
        from tinohelm.data.catalog_helpers import resolve_catalog_path

        _validate_trade_tick_source_type(source_type)
        is_source_root = self._catalog_root.name in _TRADE_TICK_SOURCE_TYPES and self._catalog_root.parent.name == "ticks"
        if source_type:
            if is_source_root:
                roots = [self._catalog_root] if self._catalog_root.name == source_type else []
            else:
                roots = [resolve_catalog_path(self._catalog_root, source_type)]
                if source_type == "aggTrades":
                    roots.append(self._catalog_root)
            return [[Path(path) for path in _ordered_unique(str(path) for path in roots)]] if roots else []

        if is_source_root:
            return [[self._catalog_root]]

        default_roots = [resolve_catalog_path(self._catalog_root, "aggTrades"), self._catalog_root]
        fallback_roots = [resolve_catalog_path(self._catalog_root, "trades")]
        return [
            [Path(path) for path in _ordered_unique(str(path) for path in default_roots)],
            [Path(path) for path in _ordered_unique(str(path) for path in fallback_roots)],
        ]


    def _trade_tick_candidate_groups(
        self,
        symbol: str,
        start: datetime | None,
        end: datetime | None,
        source_type: str | None = None,
    ) -> list[list[_TickFileCandidate]]:
        from tinohelm.strategy.loader_helpers import normalize_symbol

        groups: list[list[_TickFileCandidate]] = []
        for root_group in self._trade_tick_root_groups(source_type):
            candidates: list[_TickFileCandidate] = []
            file_ordinal = 0
            for source_priority, root in enumerate(root_group):
                root_path = Path(root)
                trade_dir = root_path / "data" / "trade_tick" / normalize_symbol(symbol)
                files = _prune_parquet_files_by_time(
                    self._storage,
                    _storage_parquet_files(self._storage, trade_dir),
                    start,
                    end,
                )
                candidate_source_type = source_type or ("trades" if root_path.name == "trades" else "aggTrades")
                source_aware = root_path.name in _TRADE_TICK_SOURCE_TYPES and root_path.parent.name == "ticks"
                for file in files:
                    candidates.append(_TickFileCandidate(file, source_priority, file_ordinal, candidate_source_type, source_aware))
                    file_ordinal += 1
            if any(candidate.source_aware for candidate in candidates):
                candidates = [candidate for candidate in candidates if candidate.source_aware]
            if candidates:
                groups.append(candidates)
        return groups


    def _trade_tick_files(
        self,
        symbol: str,
        start: datetime | None,
        end: datetime | None,
        source_type: str | None = None,
    ) -> list[Path]:
        for candidate_group in self._trade_tick_candidate_groups(symbol, start, end, source_type):
            if candidate_group:
                return [candidate.path for candidate in candidate_group]
        return []

    def _read_trade_tick_frame(
        self,
        symbol: str,
        start: datetime | None,
        end: datetime | None,
        source_type: str | None,
        *,
        strict_fields: Sequence[str] | None = None,
    ) -> pl.DataFrame:
        candidate_groups = self._trade_tick_candidate_groups(symbol, start, end, source_type)
        if not candidate_groups:
            return _empty_trade_normalized_frame()
        strict_fields = tuple(strict_fields or ())
        for candidates in candidate_groups:
            frames: list[pl.DataFrame] = []
            for candidate in candidates:
                projected_columns = _ordered_unique([
                    "ts_event",
                    "price",
                    "trade_price",
                    "size",
                    "trade_qty",
                    "aggressor_side",
                    "trade_id",
                ])
                lf, schema = _parquet_lazy_frame(
                    self._storage,
                    candidate.file,
                    row_index_name="_row_ordinal",
                    columns=projected_columns,
                )
                if "ts_event" not in schema:
                    if strict_fields:
                        raise ValueError(f"Missing required trade_tick column(s) for raw fields {tuple(strict_fields)!r}: ts_event")
                    continue
                needs_price = not strict_fields or "trade_price" in strict_fields
                needs_qty = not strict_fields or any(field in {"trade_qty", "signed_trade_qty", "buy_qty", "sell_qty", "trade_imbalance"} for field in strict_fields)
                needs_side = not strict_fields or any(field in {"trade_side", "signed_trade_qty", "buy_qty", "sell_qty", "trade_imbalance"} for field in strict_fields)
                needs_trade_id = "trade_id" in strict_fields
                missing: list[str] = []
                price_col = "price" if "price" in schema else "trade_price"
                size_col = "size" if "size" in schema else "trade_qty"
                if needs_price and price_col not in schema:
                    missing.append("price/trade_price")
                if needs_qty and size_col not in schema:
                    missing.append("size/trade_qty")
                if needs_side and "aggressor_side" not in schema:
                    missing.append("aggressor_side")
                if needs_trade_id and "trade_id" not in schema:
                    missing.append("trade_id")
                if missing:
                    if strict_fields:
                        raise ValueError(f"Missing required trade_tick column(s) for raw fields {tuple(strict_fields)!r}: {', '.join(missing)}")
                    continue

                exprs: list[pl.Expr] = [
                    pl.col("ts_event").cast(pl.Int64, strict=False),
                    pl.from_epoch(pl.col("ts_event"), time_unit="ns").alias(_TS_COL),
                    _fixed_precision_expr(price_col, schema[price_col]).alias("trade_price") if price_col in schema else pl.lit(None, dtype=pl.Float64).alias("trade_price"),
                    _fixed_precision_expr(size_col, schema[size_col]).alias("trade_qty") if size_col in schema else pl.lit(None, dtype=pl.Float64).alias("trade_qty"),
                    _trade_side_expr().alias("trade_side") if "aggressor_side" in schema else pl.lit(None, dtype=pl.Float64).alias("trade_side"),
                    pl.col("trade_id").cast(pl.Utf8, strict=False).alias("trade_id") if "trade_id" in schema else pl.lit(None, dtype=pl.Utf8).alias("trade_id"),
                    pl.lit(candidate.source_priority).alias("_source_priority"),
                    pl.lit(candidate.file_ordinal).alias("_file_ordinal"),
                ]
                normalized = _collect_lazy_streaming(lf.with_columns(exprs).with_columns([
                    (pl.col("trade_qty") * pl.col("trade_side")).alias("signed_trade_qty"),
                    pl.when(pl.col("trade_side") > 0).then(pl.col("trade_qty")).otherwise(0.0).alias("buy_qty"),
                    pl.when(pl.col("trade_side") < 0).then(pl.col("trade_qty")).otherwise(0.0).alias("sell_qty"),
                ]).select(_empty_trade_normalized_frame().columns))
                frames.append(normalized)
            if not frames:
                continue
            frame = pl.concat(frames, how="diagonal_relaxed")
            if start is not None:
                frame = frame.filter(pl.col("ts_event") >= _datetime_to_ns(start))
            if end is not None:
                frame = frame.filter(pl.col("ts_event") <= _datetime_to_ns(end))
            if frame.is_empty():
                continue
            frame = frame.with_columns(
                pl.col("_source_priority").min().over("ts_event").alias("_preferred_source_priority")
            ).filter(pl.col("_source_priority") == pl.col("_preferred_source_priority"))
            if frame["trade_id"].drop_nulls().len() > 0:
                frame = frame.with_columns(pl.col("trade_id").cast(pl.Int64, strict=False).alias("_trade_id_num"))
                non_null_id = frame.filter(pl.col("trade_id").is_not_null())
                null_id = frame.filter(pl.col("trade_id").is_null())
                parts: list[pl.DataFrame] = []
                if not non_null_id.is_empty():
                    parts.append(
                        non_null_id.sort(["trade_id", "_source_priority", "_file_ordinal", "_row_ordinal"])
                        .unique(subset=["trade_id"], keep="first", maintain_order=True)
                    )
                if not null_id.is_empty():
                    parts.append(null_id.sort(["ts_event", "_source_priority", "_file_ordinal", "_row_ordinal"]))
                frame = pl.concat(parts, how="diagonal_relaxed") if parts else frame.clear()
                return frame.sort(["ts_event", "_trade_id_num", "trade_id", "_source_priority", "_file_ordinal", "_row_ordinal"], nulls_last=True).select(
                    _empty_trade_normalized_frame().columns
                )
            return (
                frame.sort(["ts_event", "_source_priority", "_file_ordinal", "_row_ordinal"])
                .select(_empty_trade_normalized_frame().columns)
            )
        return _empty_trade_normalized_frame()

    def _load_trade_tick_panels_grouped(
        self,
        reqs: list[DataRequest],
        frequency: str,
        start: str | datetime | None,
        end: str | datetime | None,
        source_type: str | None = None,
    ) -> dict[str, Panel]:
        """Load multiple trade-tick panel fields with one parquet scan per symbol."""
        if not reqs:
            return {}
        interval = _parse_bar_frequency(frequency)
        ts_start = _parse_ts(start) if start is not None else None
        ts_end = _parse_ts(end) if end is not None else None
        raw_end = _tick_panel_raw_end(ts_end, interval)
        fields = _ordered_unique(req.field_name for req in reqs)
        unknown = [field for field in fields if field not in _TRADE_TICK_FIELDS]
        if unknown:
            raise ValueError(f"Unknown trade_tick field {unknown[0]!r}")

        symbols = _ordered_unique(req.symbol for req in reqs)
        field_symbols = {
            field: _ordered_unique(req.symbol for req in reqs if req.field_name == field)
            for field in fields
        }
        symbol_fields = {
            sym: _ordered_unique(req.field_name for req in reqs if req.symbol == sym)
            for sym in symbols
        }
        field_load_starts = {
            field: _apply_lookback_start(
                ts_start,
                frequency,
                max(req.lookback for req in reqs if req.field_name == field),
            )
            for field in fields
        }

        series_by_field_symbol: dict[str, dict[str, pl.DataFrame]] = {field: {} for field in fields}
        futures: dict[Any, str] = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            for sym in symbols:
                sym_fields = symbol_fields[sym]
                starts = [field_load_starts[field] for field in sym_fields if field_load_starts[field] is not None]
                sym_start = min(starts) if starts else None
                futures[pool.submit(self._load_trade_tick_fields, sym, sym_fields, frequency, sym_start, raw_end, source_type=source_type)] = sym

            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    series_by_field = fut.result()
                except ValueError:
                    raise
                except Exception:
                    logger.warning("Failed to load trade_tick fields for symbol %s", sym, exc_info=True)
                    series_by_field = {field: _empty_series_frame() for field in symbol_fields[sym]}
                for field in symbol_fields[sym]:
                    series_by_field_symbol[field][sym] = _filter_time_range(
                        series_by_field.get(field, _empty_series_frame()),
                        field_load_starts[field],
                        ts_end,
                    )

        panels: dict[str, Panel] = {}
        for field in fields:
            ordered = {
                sym: series_by_field_symbol[field].get(sym, _empty_series_frame())
                for sym in field_symbols[field]
            }
            panels[field] = self._apply_pit(self._align_time(ordered))
        return panels

    def _load_trade_tick_fields(
        self,
        symbol: str,
        field_names: Sequence[str],
        frequency: str,
        start: datetime | None,
        end: datetime | None,
        source_type: str | None = None,
    ) -> dict[str, pl.DataFrame]:
        interval = _parse_bar_frequency(frequency)
        raw_start = _tick_panel_raw_start(start, interval)
        raw_end = _tick_panel_raw_end(end, interval)
        empty = {field: _empty_series_frame() for field in field_names}
        try:
            frame = self._read_trade_tick_frame(symbol, raw_start, raw_end or end, source_type, strict_fields=None)
            if frame.is_empty():
                return empty
            grouped = frame.lazy().group_by_dynamic(
                _TS_COL,
                every=interval.polars_every,
                period=interval.polars_every,
                closed="left",
                label="right",
            )
            aggs: list[pl.Expr] = []
            for field in field_names:
                if field == "trade_imbalance":
                    aggs.extend([
                        pl.col("buy_qty").sum().alias("_trade_imbalance_buy"),
                        pl.col("sell_qty").sum().alias("_trade_imbalance_sell"),
                        pl.col("trade_qty").sum().alias("_trade_imbalance_total"),
                    ])
                elif field in {"trade_price", "trade_side"}:
                    aggs.append(pl.col(field).last().alias(field))
                else:
                    aggs.append(pl.col(field).sum().alias(field))
            frame = _collect_lazy_streaming(
                grouped.agg(aggs).with_columns((pl.col(_TS_COL) - _BAR_CLOSE_OFFSET).alias(_TS_COL))
            )
        except Exception:
            logger.warning("Failed to read trade ticks for %s", symbol, exc_info=True)
            return empty

        if frame.is_empty():
            return empty
        out = dict(empty)
        if "trade_imbalance" in field_names and "_trade_imbalance_total" in frame.columns:
            out["trade_imbalance"] = (
                frame.filter(pl.col("_trade_imbalance_total") > 0)
                .with_columns(((pl.col("_trade_imbalance_buy") - pl.col("_trade_imbalance_sell")) / pl.col("_trade_imbalance_total")).alias(_VAL_COL))
                .select([_TS_COL, _VAL_COL])
                .sort(_TS_COL)
            )
        for field in field_names:
            if field == "trade_imbalance" or field not in frame.columns:
                continue
            out[field] = frame.drop_nulls(field).select([_TS_COL, pl.col(field).alias(_VAL_COL)]).sort(_TS_COL)
        return out

    def _load_quote_tick_events(
        self,
        symbol: str,
        fields: Sequence[str],
        start: datetime | None,
        end: datetime | None,
        source_type: str | None = None,
        on_error: Literal["raise", "empty"] = "raise",
    ) -> pl.DataFrame:
        empty = _empty_quote_tick_events_frame(fields)
        try:
            frame = self._read_quote_tick_frame(symbol, start, end, source_type, strict=True)
            if frame.is_empty():
                return empty
            columns = [_TS_COL, *fields]
            return frame.select(columns)
        except ValueError:
            if on_error == "raise":
                raise
            logger.warning("Failed to read quote tick events for %s", symbol, exc_info=True)
            return empty
        except Exception:
            if on_error == "raise":
                raise
            logger.warning("Failed to read quote tick events for %s", symbol, exc_info=True)
            return empty

    def _load_trade_tick_events(
        self,
        symbol: str,
        fields: Sequence[str],
        start: datetime | None,
        end: datetime | None,
        source_type: str | None = None,
        on_error: Literal["raise", "empty"] = "raise",
    ) -> pl.DataFrame:
        empty = _empty_trade_tick_events_frame(fields)
        try:
            frame = self._read_trade_tick_frame(symbol, start, end, source_type, strict_fields=fields)
            if frame.is_empty():
                return empty
            columns = [_TS_COL, *[field for field in fields if field in frame.columns]]
            return frame.select(columns).sort(_TS_COL)
        except ValueError:
            if on_error == "raise":
                raise
            logger.warning("Failed to read trade tick events for %s", symbol, exc_info=True)
            return empty
        except Exception:
            if on_error == "raise":
                raise
            logger.warning("Failed to read trade tick events for %s", symbol, exc_info=True)
            return empty

    def _load_trade_tick_field(
        self,
        symbol: str,
        field_name: str,
        frequency: str,
        start: datetime | None,
        end: datetime | None,
    ) -> pl.DataFrame:
        if field_name not in _TRADE_TICK_FIELDS:
            raise ValueError(f"Unknown trade_tick field {field_name!r}")
        return self._load_trade_tick_fields(symbol, [field_name], frequency, start, end).get(
            field_name,
            _empty_series_frame(),
        )

    def _load_metrics_field(
        self,
        symbol: str,
        field_name: str,
        frequency: str,
        start: datetime | None,
        end: datetime | None,
    ) -> pl.DataFrame:
        if field_name not in _METRICS_FIELDS:
            raise ValueError(f"Unknown metrics field {field_name!r}")
        from tinohelm.data.catalog import metrics_parquet_path

        try:
            files = list(self._storage.iter_files(metrics_parquet_path(symbol, self._catalog_root), suffix=".parquet"))
            if not files:
                return _empty_series_frame()
            frame = _read_parquet_frame(self._storage, files[0])
        except Exception:
            logger.warning("Failed to read metrics parquet for %s", symbol, exc_info=True)
            return _empty_series_frame()
        if frame is None or frame.is_empty() or field_name not in frame.columns:
            return _empty_series_frame()
        out = _raw_frame_field_to_series(frame, field_name, frequency)
        return _filter_time_range(out, start, end)

    def _load_book_depth_field(
        self,
        symbol: str,
        field_name: str,
        frequency: str,
        start: datetime | None,
        end: datetime | None,
    ) -> pl.DataFrame:
        if field_name not in _BOOK_DEPTH_FIELDS:
            raise ValueError(f"Unknown book_depth field {field_name!r}")
        from tinohelm.data.catalog import book_depth_parquet_path

        try:
            files = list(self._storage.iter_files(book_depth_parquet_path(symbol, self._catalog_root), suffix=".parquet"))
            if not files:
                return _empty_series_frame()
            frame = _read_parquet_frame(self._storage, files[0])
        except Exception:
            logger.warning("Failed to read bookDepth parquet for %s", symbol, exc_info=True)
            return _empty_series_frame()
        if frame is None or frame.is_empty():
            return _empty_series_frame()
        metric_col = "notional" if field_name in {"book_depth_notional", "notional"} else "depth"
        if metric_col not in frame.columns:
            return _empty_series_frame()
        if "percentage" in frame.columns:
            frame = frame.sort(["ts_event", "percentage"]).unique(
                subset=["ts_event"], keep="first", maintain_order=True,
            )
        out = _raw_frame_field_to_series(frame, metric_col, frequency)
        return _filter_time_range(out, start, end)

    def _load_bar_reference_index(
        self,
        symbols: Sequence[str],
        frequency: str,
        start: datetime | None,
        end: datetime | None,
    ) -> list[datetime]:
        """Load the union close-bar timestamp grid for funding alignment.

        Funding-rate inputs are sparse settlement events.  When callers ask
        for funding without also requesting a bar field, load close bars only
        for their timestamps so ``DataLayer.load()`` still returns a panel on
        the intended bar cadence.
        """
        ts_values: set[datetime] = set()
        for sym in symbols:
            try:
                close_series = self._load_bar_field(sym, "close", frequency, start, end)
            except Exception:
                logger.warning(
                    "Failed to load bar reference index for funding_rate symbol %s",
                    sym,
                    exc_info=True,
                )
                continue
            if not close_series.is_empty() and _TS_COL in close_series.columns:
                ts_values.update(close_series[_TS_COL].to_list())
        return sorted(ts_values)

    # ------------------------------------------------------------------
    # Bar reader (direct Parquet projection, no NT object materialisation)
    # ------------------------------------------------------------------

    def _load_bar_field(
        self,
        symbol: str,
        field_name: str,
        frequency: str,
        start: datetime | None,
        end: datetime | None,
        source_type: str | None = None,
    ) -> pl.DataFrame:
        """Read one OHLCV field for a symbol from the Parquet catalog."""
        return self._load_bar_fields(
            symbol,
            [field_name],
            frequency,
            start,
            end,
            source_type=source_type,
        )[field_name]

    def _load_bar_fields(
        self,
        symbol: str,
        field_names: Sequence[str],
        frequency: str,
        start: datetime | None,
        end: datetime | None,
        source_type: str | None = None,
    ) -> dict[str, pl.DataFrame]:
        """Read multiple OHLCV fields via Parquet projection and optional composition.

        The reader first looks for the requested frequency directly under the
        source-specific catalog root (``bar/klines`` by default) and then the
        legacy flat catalog root.  If the target interval is not stored but can
        be composed exactly from a lower interval on disk, it reads the lower
        interval once and resamples to the requested cadence before returning.
        """
        target = _parse_bar_frequency(frequency)
        source_type = source_type or _DEFAULT_BAR_SOURCE_TYPE

        fields = _ordered_unique(field_names)
        for field_name in fields:
            if field_name not in _BAR_FIELD_ATTR:
                raise ValueError(
                    f"Unknown bar field {field_name!r}. Supported: {list(_BAR_FIELD_ATTR)}"
                )

        empty = {field_name: _empty_series_frame() for field_name in fields}

        frame, available_fields = self._read_bar_frame(
            symbol=symbol,
            field_names=fields,
            frequency=frequency,
            source_type=source_type,
            start=start,
            end=end,
        )
        if frame is not None:
            return self._bar_frame_to_series(frame, fields, available_fields, empty)

        for source_frequency in _candidate_source_frequencies(target):
            source = _parse_bar_frequency(source_frequency)
            source_start, source_end = _fallback_source_window(start, end, target, source)
            frame, available_fields = self._read_bar_frame(
                symbol=symbol,
                field_names=fields,
                frequency=source_frequency,
                source_type=source_type,
                start=source_start,
                end=source_end,
            )
            if frame is None:
                continue
            if frame.is_empty():
                continue
            resampled = self._resample_bar_frame(
                frame,
                available_fields,
                target=target,
                source=source,
                start=start,
                end=end,
            )
            if resampled.is_empty():
                continue
            return self._bar_frame_to_series(resampled, fields, available_fields, empty)

        logger.debug(
            "No bar Parquet files for %s %s under source_type=%s",
            symbol, frequency, source_type,
        )
        return empty

    def _read_bar_frame(
        self,
        *,
        symbol: str,
        field_names: Sequence[str],
        frequency: str,
        source_type: str,
        start: datetime | None,
        end: datetime | None,
    ) -> tuple[pl.DataFrame | None, list[str]]:
        """Read decoded bar columns for one exact on-disk frequency.

        Returns ``(None, [])`` when no matching parquet files exist in any
        candidate catalog root.  An existing path with no rows/columns returns
        an empty frame so callers do not fall through to another cadence by
        accident.
        """
        bar_type_str = _make_bar_type_strict(symbol, frequency)
        fields = _ordered_unique(field_names)

        for catalog_root in _bar_catalog_roots(self._catalog_root, source_type):
            bar_dir = catalog_root / "data" / "bar" / bar_type_str
            parquet_files = _storage_parquet_files(self._storage, bar_dir)
            if not parquet_files:
                continue

            try:
                schema = _parquet_schema(self._storage, parquet_files[0])
            except Exception:
                logger.warning(
                    "Failed to scan bar Parquet schema for %s at %s",
                    bar_type_str, bar_dir, exc_info=True,
                )
                return pl.DataFrame(), []

            if "ts_event" not in schema:
                logger.warning("Bar Parquet for %s has no ts_event column", bar_type_str)
                return pl.DataFrame(), []

            available_fields = [field for field in fields if field in schema]
            missing_fields = [field for field in fields if field not in schema]
            if missing_fields:
                logger.warning(
                    "Bar Parquet for %s missing columns %s",
                    bar_type_str, missing_fields,
                )
            if not available_fields:
                return pl.DataFrame(), []

            try:
                if is_remote_storage(self._storage):
                    pruned_files = _prune_parquet_files_by_time(self._storage, parquet_files, start, end)
                    frames = [
                        _read_parquet_frame(
                            self._storage,
                            file,
                            columns=["ts_event", *available_fields],
                        )
                        for file in pruned_files
                    ]
                    frame = pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()
                    if start is not None and not frame.is_empty():
                        frame = frame.filter(pl.col("ts_event") >= _datetime_to_ns(start))
                    if end is not None and not frame.is_empty():
                        frame = frame.filter(pl.col("ts_event") <= _datetime_to_ns(end))
                else:
                    lf = pl.scan_parquet([str(file.path) for file in parquet_files])
                    scan = lf.select(["ts_event", *available_fields])
                    if start is not None:
                        scan = scan.filter(pl.col("ts_event") >= _datetime_to_ns(start))
                    if end is not None:
                        scan = scan.filter(pl.col("ts_event") <= _datetime_to_ns(end))
                    frame = _collect_lazy_streaming(scan)
            except Exception:
                logger.warning(
                    "Failed to read bar Parquet for %s at %s — returning empty series",
                    bar_type_str, bar_dir, exc_info=True,
                )
                return pl.DataFrame(), []

            if frame.is_empty():
                return frame, available_fields

            frame = frame.with_columns(
                pl.from_epoch(pl.col("ts_event"), time_unit="ns").alias(_TS_COL),
                *[
                    _bar_value_expr(field, schema[field]).alias(field)
                    for field in available_fields
                ],
            )
            return (
                frame.select([_TS_COL, *available_fields])
                .sort(_TS_COL)
                .unique(subset=[_TS_COL], keep="last", maintain_order=True),
                available_fields,
            )

        return None, []

    def _resample_bar_frame(
        self,
        frame: pl.DataFrame,
        available_fields: Sequence[str],
        *,
        target: _BarInterval,
        source: _BarInterval,
        start: datetime | None,
        end: datetime | None,
    ) -> pl.DataFrame:
        """Aggregate lower-cadence OHLCV bars to ``target`` cadence."""
        if frame.is_empty():
            return frame

        expected_children = target.ns // source.ns
        expected_span = target.ns - source.ns

        aggregations: list[pl.Expr] = []
        for field in available_fields:
            if field == "open":
                aggregations.append(pl.col(field).first().alias(field))
            elif field == "high":
                aggregations.append(pl.col(field).max().alias(field))
            elif field == "low":
                aggregations.append(pl.col(field).min().alias(field))
            elif field == "close":
                aggregations.append(pl.col(field).last().alias(field))
            elif field == "volume":
                aggregations.append(pl.col(field).sum().alias(field))

        if not aggregations:
            return pl.DataFrame()

        out = (
            frame.sort(_TS_COL)
            .with_columns(
                pl.col(_TS_COL).alias("_child_close_ts"),
                pl.col(_TS_COL).dt.timestamp("ns").alias("_child_close_ns"),
            )
            .group_by_dynamic(
                _TS_COL,
                every=target.polars_every,
                period=target.polars_every,
                closed="left",
                label="left",
            )
            .agg([
                pl.col("_child_close_ts").max().alias("_resampled_ts"),
                pl.col("_child_close_ns").count().alias("_child_count"),
                pl.col("_child_close_ns").min().alias("_child_min_ns"),
                pl.col("_child_close_ns").max().alias("_child_max_ns"),
                *aggregations,
            ])
            .filter(
                (pl.col("_child_count") == expected_children)
                & ((pl.col("_child_max_ns") - pl.col("_child_min_ns")) == expected_span)
            )
            .with_columns(pl.col("_resampled_ts").alias(_TS_COL))
            .drop("_resampled_ts", "_child_count", "_child_min_ns", "_child_max_ns")
            .sort(_TS_COL)
        )
        if start is not None:
            out = out.filter(pl.col(_TS_COL) >= pl.lit(start))
        if end is not None:
            out = out.filter(pl.col(_TS_COL) <= pl.lit(end))
        return out

    def _bar_frame_to_series(
        self,
        frame: pl.DataFrame,
        fields: Sequence[str],
        available_fields: Sequence[str],
        empty: dict[str, pl.DataFrame],
    ) -> dict[str, pl.DataFrame]:
        """Split a decoded/resampled bar frame into ``[ts, value]`` series."""
        if frame.is_empty():
            return dict(empty)

        out = dict(empty)
        for field in available_fields:
            if field not in fields or field not in frame.columns:
                continue
            out[field] = (
                frame.select([_TS_COL, pl.col(field).alias(_VAL_COL)])
                .sort(_TS_COL)
                .unique(subset=[_TS_COL], keep="last", maintain_order=True)
            )
        return out

    # ------------------------------------------------------------------
    # Market-cap reader (close × current circulating_supply snapshot, non-PIT)
    # ------------------------------------------------------------------

    def _load_market_cap_field(
        self,
        symbol: str,
        frequency: str,
        start: datetime | None,
        end: datetime | None,
    ) -> pl.DataFrame:
        """Compute market-cap series as ``close × circulating_supply``.

        PIT note
        --------
        Binance does not expose historical circulating-supply snapshots here.
        This implementation uses the **current snapshot** (fetched with a 24 h
        cache) and applies it uniformly across all bars.  That is non-PIT for
        historical research because supply is a symbol-specific time-varying
        series and the latest snapshot can change historical cross-sectional
        ranks.  Use only for latest/exploratory views unless a dated supply
        series is wired in.
        """
        from tinohelm.data.instruments import fetch_circulating_supply

        # --- Load close prices (reuse bar reader) ---
        close_frame = self._load_bar_field(symbol, "close", frequency, start, end)
        if close_frame.is_empty():
            return _empty_series_frame()

        # --- Fetch circulating supply (24h-cached) ---
        try:
            circulating = fetch_circulating_supply(symbol)
        except Exception:
            logger.warning(
                "Could not fetch circulating supply for %s; returning empty series",
                symbol,
                exc_info=True,
            )
            return _empty_series_frame()

        # --- mcap = close × current circulating snapshot (non-PIT historically) ---
        return close_frame.with_columns(
            (pl.col(_VAL_COL) * float(circulating)).alias(_VAL_COL),
        )

    # ------------------------------------------------------------------
    # Funding-rate reader (prefers Parquet; falls back to JSON)
    # ------------------------------------------------------------------

    def _load_funding_rate(
        self,
        symbol: str,
        start: datetime | None,
        end: datetime | None,
    ) -> pl.DataFrame:
        """Read funding-rate data for a symbol from NT-native funding updates."""
        frame = self._load_funding_rate_parquet(symbol, start, end)
        if frame is not None:
            return frame
        return _empty_series_frame()

    def _load_funding_rate_parquet(
        self,
        symbol: str,
        start: datetime | None,
        end: datetime | None,
    ) -> "pl.DataFrame | None":
        """Try to load funding-rate updates from NT-native parquet."""
        from decimal import Decimal
        from nautilus_trader.model.data import FundingRateUpdate
        from nautilus_trader.model.identifiers import InstrumentId
        from tinohelm.data.catalog import _catalog_for_root

        catalog = _catalog_for_root(self._catalog_root, self._storage)
        try:
            rows = catalog.query(
                FundingRateUpdate,
                identifiers=[str(InstrumentId.from_str(_normalize_symbol(symbol)))],
                start=start,
                end=end,
            )
        except Exception:
            logger.warning(
                "Failed to read funding-rate updates for %s", symbol, exc_info=True
            )
            return None

        if not rows:
            return _empty_series_frame()

        timestamps = [_ns_to_datetime(int(row.ts_event)) for row in rows]
        values = [float(row.rate if not isinstance(row.rate, Decimal) else row.rate) for row in rows]
        frame = _build_series_frame(timestamps, values)
        return _filter_time_range(frame, start, end)

    def _load_funding_rate_json(
        self,
        symbol: str,
        start: datetime | None,
        end: datetime | None,
    ) -> pl.DataFrame:
        """Read funding-rate JSON for a symbol (legacy fallback path).

        Reads from ``{funding_dir}/{symbol.lower()}.json`` (configured at
        DataLayer construction time). Time-range filtering applied when start
        or end is provided (when any time boundary exists).
        """
        path = self._funding_dir / f"{symbol.lower()}.json"
        if not path.exists():
            logger.debug("No funding-rate JSON for %s at %s", symbol, path)
            return _empty_series_frame()

        try:
            with open(path) as fh:
                records = json.load(fh)
        except (json.JSONDecodeError, OSError):
            logger.warning("Cannot read funding rate JSON for %s", symbol, exc_info=True)
            return _empty_series_frame()

        if not isinstance(records, list) or not records:
            return _empty_series_frame()

        timestamps = [_ms_to_datetime(int(r["funding_time_ms"])) for r in records]
        values = [float(r["funding_rate"]) for r in records]
        frame = _build_series_frame(timestamps, values)

        return _filter_time_range(frame, start, end)

    # ------------------------------------------------------------------
    # Time alignment
    # ------------------------------------------------------------------

    def _align_time(
        self,
        series_by_symbol: dict[str, pl.DataFrame],
    ) -> Panel:
        """Align multiple symbol Series onto a common ``ts`` axis.

        The output is a wide-table Panel ``[ts, sym1, sym2, ...]``: each
        per-symbol 2-col ``[ts, value]`` frame is renamed to ``[ts, symbol]``
        and outer-joined on ``ts`` (union index), then sorted by ``ts``.

        For bar sources, the union index *is* the bar grid because every input
        already lives on the same bar frequency.  For funding_rate the caller
        is responsible for forward-filling / aligning onto the bar index
        before this routine sees it.
        """
        if not series_by_symbol:
            return pl.DataFrame()

        symbols = list(series_by_symbol.keys())

        # Drop frames that have no data so they don't kill the join.
        non_empty = [
            (sym, frame)
            for sym, frame in series_by_symbol.items()
            if not frame.is_empty()
        ]
        if not non_empty:
            # All inputs empty — return an empty wide panel preserving column order.
            return pl.DataFrame(
                {col: [] for col in [_TS_COL, *symbols]},
                schema={_TS_COL: pl.Datetime("ns"), **{s: pl.Float64 for s in symbols}},
            )

        # Outer-join all per-symbol frames on ``ts`` (union of timestamps).
        first_sym, first_frame = non_empty[0]
        panel = first_frame.rename({_VAL_COL: first_sym})
        for sym, frame in non_empty[1:]:
            panel = panel.join(
                frame.rename({_VAL_COL: sym}),
                on=_TS_COL,
                how="full",
                coalesce=True,
            )

        # Restore original symbol-column order, keeping all-empty columns as
        # all-null — preserves the legacy pandas ``DataFrame(dict_of_series)``
        # behaviour that downstream callers rely on.
        for sym in symbols:
            if sym not in panel.columns:
                panel = panel.with_columns(
                    pl.lit(None, dtype=pl.Float64).alias(sym),
                )

        return panel.select([_TS_COL, *symbols]).sort(_TS_COL)

    def _align_funding_onto_bar_index(
        self,
        funding_series: pl.DataFrame,
        bar_ts: Sequence[datetime],
    ) -> pl.DataFrame:
        """Apply strict backward-asof visibility onto the bar index.

        PIT logic:
        - A funding rate published at time T is only observable strictly
          *after* that settlement.  In Binance perpetuals, rates settle at
          00:00, 08:00, 16:00 UTC.  A bar at exactly 08:00 should still see the
          previously observable rate; the 08:00 print becomes visible on the
          first later bar.
        - Implementation: shift funding timestamps by +1ns, not values by one
          native 8h row, then forward-fill on the union grid and project back
          to the requested bar timestamps.

        Parameters
        ----------
        funding_series:
            2-col ``[ts, value]`` polars frame (8h frequency, raw, already
            filtered to time range).
        bar_ts:
            Sequence of ``datetime`` values defining the target bar index
            (e.g. 1m or 5m).

        Returns
        -------
        polars.DataFrame
            2-col ``[ts, value]`` frame aligned to ``bar_ts`` with the as-of
            strict-asof delay + forward-fill applied.  Missing bars before the first
            observable value are ``null``.
        """
        target = pl.DataFrame(
            {_TS_COL: list(bar_ts)},
            schema={_TS_COL: pl.Datetime("ns")},
        )

        if funding_series.is_empty() or len(bar_ts) == 0:
            return target.with_columns(pl.lit(None, dtype=pl.Float64).alias(_VAL_COL))

        # Shift timestamps by the minimum representable unit so a print at T is
        # invisible to a bar exactly at T but visible to the first later bar.
        shifted = funding_series.with_columns(pl.col(_TS_COL) + pl.duration(nanoseconds=1))

        # Forward-fill onto the union of the bar timestamps and the shifted
        # 8h grid, then restrict to the bar grid.
        union = (
            target.select(_TS_COL)
            .vstack(shifted.select(_TS_COL))
            .unique(maintain_order=False)
            .sort(_TS_COL)
        )
        merged = union.join(shifted, on=_TS_COL, how="left").with_columns(
            pl.col(_VAL_COL).forward_fill()
        )
        # Inner-join back onto the bar timestamps in the requested order.
        return target.join(merged, on=_TS_COL, how="left")

    # ------------------------------------------------------------------
    # PIT filtering
    # ------------------------------------------------------------------

    def _apply_pit(self, panel: Panel) -> Panel:
        """Apply Point-In-Time symbol filtering to a Panel.

        For each timestamp ``ts`` in the panel index, symbols not present in
        ``Universe.get_symbols_at(ts)`` are set to ``null``.  The panel shape
        (rows × columns) is preserved — rows are NOT dropped.

        Vectorised implementation: precompute per-symbol (eligible_from,
        delisting_date) boundaries from the Universe, then build column-level
        boolean masks via polars expressions.

        Parameters
        ----------
        panel:
            Input panel with ``ts`` column and N symbol columns.

        Returns
        -------
        Panel
            Same shape panel with ineligible cells set to ``null``.
        """
        if panel.is_empty() or _TS_COL not in panel.columns:
            return panel

        boundaries = self._universe.get_symbol_boundaries()

        symbol_cols = [c for c in panel.columns if c != _TS_COL]
        exprs: list[pl.Expr] = []
        for sym in symbol_cols:
            boundary = boundaries.get(sym)
            if boundary is None:
                # Symbol not in universe at all → entire column null
                exprs.append(pl.lit(None, dtype=pl.Float64).alias(sym))
                continue

            eligible_from, delisting_date = boundary
            # Mask cells where ts < eligible_from
            mask_expr = pl.col(_TS_COL) >= pl.lit(eligible_from)
            if delisting_date is not None:
                mask_expr = mask_expr & (pl.col(_TS_COL) < pl.lit(delisting_date))
            exprs.append(
                pl.when(mask_expr)
                .then(pl.col(sym))
                .otherwise(None)
                .alias(sym)
            )

        return panel.with_columns(exprs)


# ---------------------------------------------------------------------------
# Composite load: bar + funding aligned together
# ---------------------------------------------------------------------------

def load_aligned(
    data_layer: DataLayer,
    bar_requests: list[DataRequest],
    funding_requests: list[DataRequest] | None = None,
    start: str | datetime | None = None,
    end: str | datetime | None = None,
) -> dict[str, Panel]:
    """High-level helper: load bar panels, then align funding_rate onto bar index.

    This convenience function handles the two-step process:
    1. Load bar data → build bar timestamp index.
    2. Load funding_rate per symbol → shift(1) + ffill onto bar index.

    Parameters
    ----------
    data_layer:
        Configured ``DataLayer`` instance.
    bar_requests:
        List of DataRequests with source="bar".
    funding_requests:
        Optional list of DataRequests with source="funding_rate".
    start, end:
        Time range overrides.

    Returns
    -------
    dict[str, Panel]
        Combined panel dict including both bar fields and ``"funding_rate"``.
    """
    result = data_layer.load(bar_requests, start=start, end=end)

    if not funding_requests:
        return result

    # Determine the reference bar index (use the first non-empty bar panel).
    bar_ts: list[datetime] = []
    for panel in result.values():
        if not panel.is_empty() and _TS_COL in panel.columns:
            bar_ts = panel[_TS_COL].to_list()
            break

    if not bar_ts:
        # No bar data — still load funding raw
        result.update(data_layer.load(funding_requests, start=start, end=end))
        return result

    # Load funding_rate per symbol and align onto bar index.
    symbols_for_funding = [r.symbol for r in funding_requests]
    ts_start = _parse_ts(start) if start is not None else None
    ts_end = _parse_ts(end) if end is not None else None

    aligned_cols: dict[str, pl.DataFrame] = {}
    for sym in symbols_for_funding:
        raw_series = data_layer._load_funding_rate(sym, ts_start, ts_end)
        aligned = data_layer._align_funding_onto_bar_index(raw_series, bar_ts)
        aligned_cols[sym] = aligned

    if aligned_cols:
        funding_panel = data_layer._align_time(aligned_cols)
        funding_panel = data_layer._apply_pit(funding_panel)
        result["funding_rate"] = funding_panel

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_ts(value: str | datetime) -> datetime:
    """Parse a timestamp string or :class:`datetime` to a tz-naive ``datetime``.

    Accepts:
    - ISO-8601 strings (``"2024-01-01"``, ``"2024-01-01T00:00:00"``,
      ``"2024-01-01T00:00:00+00:00"`` — tz info is stripped).
    - Native ``datetime`` instances (tz info stripped).

    Pandas Timestamp instances are accepted via duck typing
    (``isoformat`` + ``tzinfo`` attributes) so legacy callers still work
    without forcing this module to import pandas.
    """
    if isinstance(value, datetime):
        return (
            value.astimezone(UTC).replace(tzinfo=None)
            if value.tzinfo is not None
            else value
        )
    # Duck-typed pandas Timestamp passthrough — keeps backward compatibility
    # with callers that still construct ``pandas Timestamp`` values.
    if hasattr(value, "isoformat") and hasattr(value, "tzinfo"):
        as_str = value.isoformat()  # type: ignore[union-attr]
        return _parse_ts(as_str)
    return _parse_iso8601(str(value))


def _parse_iso8601(value: str) -> datetime:
    """Parse an ISO-8601 string to a tz-naive ``datetime``.

    Accepts trailing ``Z`` as UTC and strips tz info to match the project's
    naive-datetime convention.
    """
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        ts = datetime.fromisoformat(s)
    except ValueError:
        # Fallback for date-only strings (datetime.fromisoformat handles this
        # since 3.11; older paths kept for safety).
        ts = datetime.strptime(s, "%Y-%m-%d")
    return ts.astimezone(UTC).replace(tzinfo=None) if ts.tzinfo is not None else ts


def _datetime_to_ns(value: datetime) -> int:
    """Convert a tz-naive ``datetime`` to nanoseconds since the Unix epoch.

    ``datetime`` only stores microsecond precision so the result is exact at
    the ns boundary up to ``1_000`` × the microsecond count.
    """
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    epoch = datetime(1970, 1, 1)
    delta = value - epoch
    seconds = delta.days * 86_400 + delta.seconds
    return seconds * 1_000_000_000 + delta.microseconds * 1_000


def _ns_to_datetime(ns: int) -> datetime:
    """Convert nanoseconds-since-epoch to a tz-naive ``datetime``.

    Microsecond-precision is preserved; sub-microsecond information is
    silently truncated (Python ``datetime`` does not represent it).
    """
    seconds, remainder = divmod(int(ns), 1_000_000_000)
    micros = remainder // 1_000
    base = datetime(1970, 1, 1) + timedelta(seconds=seconds)
    return base.replace(microsecond=micros)


def _ms_to_datetime(ms: int) -> datetime:
    """Convert milliseconds-since-epoch to a tz-naive ``datetime``."""
    seconds, remainder_ms = divmod(int(ms), 1_000)
    base = datetime(1970, 1, 1) + timedelta(seconds=seconds)
    return base.replace(microsecond=remainder_ms * 1_000)


def _filter_time_range(
    frame: pl.DataFrame,
    start: datetime | None,
    end: datetime | None,
) -> pl.DataFrame:
    """Inclusive time-range filter on ``ts`` for a 2-col ``[ts, value]`` frame."""
    if frame.is_empty() or (start is None and end is None):
        return frame
    expr = pl.lit(True)
    if start is not None:
        expr = expr & (pl.col(_TS_COL) >= pl.lit(start))
    if end is not None:
        expr = expr & (pl.col(_TS_COL) <= pl.lit(end))
    return frame.filter(expr)


def _lookback_offset_ns(frequency: str, lookback: int) -> int | None:
    """Return ``lookback * bar_duration`` in nanoseconds, or ``None`` when no
    offset should be applied.

    Returns ``None`` when either the frequency is not a known NT bar interval
    (e.g. ``"tick"`` — such sources are not supported by the loader anyway)
    or ``lookback`` is non-positive.  Callers treat a ``None`` result as
    "leave ``start`` unchanged".
    """
    if lookback <= 0:
        return None
    try:
        return _parse_bar_frequency(frequency).ns * lookback
    except ValueError:
        logger.debug(
            "_lookback_offset_ns: frequency %r is not a valid bar interval; "
            "skipping warmup offset",
            frequency,
        )
        return None
