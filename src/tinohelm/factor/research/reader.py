"""Research-native parquet reader for canonical long bars."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np
import polars as pl

from tinohelm.data.catalog_helpers import interval_to_nanoseconds, resolve_catalog_path
from tinohelm.data.pipeline_helpers import WRITE_CATEGORY
from tinohelm.data.storage import CatalogStorageProvider, StorageObject
from tinohelm.factor.data_layer import (
    _bar_value_expr,
    _collect_lazy_streaming,
    _parquet_lazy_frame,
    _schema_names,
    _storage_parquet_files,
)
from tinohelm.factor.research.panel import CanonicalBars, canonicalize_long_bars
from tinohelm.strategy.loader_helpers import make_bar_type_str, parse_interval


@dataclass(frozen=True)
class ResearchDataRequest:
    symbols: tuple[str, ...]
    fields: tuple[str, ...]
    interval: str
    start: datetime | None
    end: datetime | None
    source: str = "klines"


@dataclass(frozen=True)
class _CandidateFile:
    file: StorageObject
    symbol_from_path: str | None = None

    @property
    def path(self) -> Path:
        return self.file.path


class ResearchParquetReader:
    """Load canonical bars directly from parquet without NT runtime objects."""

    def __init__(self, catalog_root: Path):
        self.catalog_root = Path(catalog_root)
        from tinohelm.data.storage import get_catalog_storage
        self._storage = get_catalog_storage(catalog_root=self.catalog_root)

    def load_bars(self, request: ResearchDataRequest) -> CanonicalBars:
        interval_to_nanoseconds(request.interval)
        _validate_bar_source(request.source)
        _validate_requested_symbol_aliases(request.symbols)
        roots = self._candidate_roots(request.source)
        files = self._candidate_files(roots, request)
        if not files:
            return canonicalize_long_bars(
                _empty_frame(request.fields),
                request.fields,
                request.source,
                request.interval,
                symbols=request.symbols,
            )

        lazy_frames = []
        validate_cadence = False
        for candidate in files:
            scan, schema = _parquet_lazy_frame(self._storage, candidate.file)
            schema_names = _schema_names(schema)
            if not _has_timestamp(schema_names):
                if "timestamp" in schema_names:
                    raise ValueError(
                        f"parquet file {candidate.path} uses generic 'timestamp'; "
                        "use close-time ts_event, ts, or timestamp_ns"
                    )
                raise ValueError(f"parquet file {candidate.path} lacks a timestamp column")
            missing_fields = [field for field in request.fields if field not in schema_names]
            if missing_fields:
                raise ValueError(f"parquet file {candidate.path} missing fields: {missing_fields!r}")
            bar_type_col = "bar_type" if "bar_type" in schema_names else None
            if bar_type_col is not None:
                _validate_bar_type_values(scan, bar_type_col, request, candidate.path)
            elif candidate.symbol_from_path is None:
                validate_cadence = True
            if not _has_symbol_identity(schema_names) and candidate.symbol_from_path is None:
                raise ValueError(
                    f"parquet file {candidate.path} lacks symbol identity; expected symbol, "
                    "instrument_id, bar_type, or an NT bar-type directory"
                )
            lazy_frames.append(
                self._normalize_scan(scan, schema, candidate.path, request, candidate.symbol_from_path)
            )
        if not lazy_frames:
            return canonicalize_long_bars(
                _empty_frame(request.fields),
                request.fields,
                request.source,
                request.interval,
                symbols=request.symbols,
            )
        frame = _collect_lazy_streaming(pl.concat(lazy_frames, how="vertical_relaxed"))
        if validate_cadence:
            _validate_flat_cadence(frame, request.interval)
        return canonicalize_long_bars(
            frame,
            request.fields,
            request.source,
            request.interval,
            symbols=request.symbols,
        )

    def _candidate_roots(self, source: str) -> list[Path]:
        source_root = resolve_catalog_path(self.catalog_root, source)
        if self.catalog_root.name == source and self.catalog_root.parent.name == "bar":
            candidates = [self.catalog_root, source_root]
        else:
            candidates = [source_root]
            if source == "klines":
                candidates.append(self.catalog_root)
        catalog_root = self.catalog_root.resolve(strict=False)
        seen: set[Path] = set()
        result: list[Path] = []
        for root in candidates:
            resolved = root.resolve(strict=False)
            if not resolved.is_relative_to(catalog_root):
                raise ValueError(f"candidate source root {root} resolves outside catalog root {self.catalog_root}")
            if resolved not in seen:
                seen.add(resolved)
                result.append(root)
        return result

    def _candidate_files(self, roots: Sequence[Path], request: ResearchDataRequest) -> list[_CandidateFile]:
        for root in roots:
            exact = _exact_bar_files(root, request, self._storage)
            if exact:
                return exact
            legacy = [_CandidateFile(file) for file in _legacy_files(root, request.source, self._storage)]
            if legacy:
                return legacy
        return []

    def _normalize_scan(
        self,
        scan: pl.LazyFrame,
        schema: pl.Schema,
        file_path: Path,
        request: ResearchDataRequest,
        symbol_from_path: str | None,
    ) -> pl.LazyFrame:
        columns = schema.names()
        ts_col = _first_present(columns, ["ts_event", "ts", "timestamp_ns"])
        if ts_col is None:
            if "timestamp" in columns:
                raise ValueError(
                    f"parquet file {file_path} uses generic 'timestamp'; use close-time ts_event, ts, or timestamp_ns"
                )
            raise ValueError(f"parquet file {file_path} lacks a timestamp column")
        symbol_col = _first_present(columns, ["instrument_id", "symbol"])
        bar_type_col = "bar_type" if "bar_type" in columns else None
        if symbol_from_path is not None:
            _validate_row_symbol_identity(scan, symbol_col, bar_type_col, symbol_from_path, file_path)
        if symbol_col is None and bar_type_col is None and symbol_from_path is None:
            raise ValueError(
                f"parquet file {file_path} lacks symbol identity; expected symbol, "
                "instrument_id, bar_type, or an NT bar-type directory"
            )

        ts_expr = _timestamp_expr(ts_col, schema[ts_col], file_path)
        raw_symbol_expr = _symbol_expr(symbol_col, bar_type_col, symbol_from_path)
        symbol_expr = _canonical_symbol_expr(raw_symbol_expr, request.symbols)
        expressions = [ts_expr.alias("ts"), symbol_expr.alias("symbol")]
        expressions.extend(_bar_field_expr(field, schema[field]).alias(field) for field in request.fields)
        out = scan.select(expressions)

        filters = []
        if request.symbols:
            filters.append(pl.col("symbol").is_in(list(request.symbols)))
        if request.start is not None:
            filters.append(pl.col("ts") >= _datetime_lit(request.start))
        if request.end is not None:
            filters.append(pl.col("ts") < _datetime_lit(request.end))
        for predicate in filters:
            out = out.filter(predicate)
        return out


def _exact_bar_files(
    root: Path,
    request: ResearchDataRequest,
    storage: CatalogStorageProvider,
) -> list[_CandidateFile]:
    bar_root = root / "data" / "bar"
    files: list[_CandidateFile] = []
    seen: set[Path] = set()
    if request.symbols:
        for requested_symbol in request.symbols:
            matches: list[tuple[str, Path, list[StorageObject]]] = []
            for alias in sorted(_symbol_aliases(requested_symbol)):
                for price_type in _price_types_for_source(request.source):
                    bar_dir = _safe_bar_dir(bar_root, alias, request.interval, price_type)
                    objects = _safe_parquet_objects(root, _storage_parquet_files(storage, bar_dir))
                    if objects:
                        matches.append((alias, bar_dir, objects))
            if len(matches) > 1:
                dirs = [str(bar_dir.relative_to(bar_root)) for _, bar_dir, _ in matches]
                raise ValueError(
                    f"ambiguous bar directories for requested symbol {requested_symbol!r}: {dirs!r}"
                )
            for _, bar_dir, objects in matches:
                symbol_from_path = _symbol_from_bar_type_name(bar_dir.name)
                for file in objects:
                    resolved = file.path.resolve(strict=False)
                    if resolved not in seen:
                        seen.add(resolved)
                        files.append(_CandidateFile(file=file, symbol_from_path=symbol_from_path))
    else:
        interval_token = parse_interval(request.interval)
        suffixes = tuple(
            f"-{interval_token}-{price_type}-EXTERNAL" for price_type in _price_types_for_source(request.source)
        )
        objects = _safe_parquet_objects(root, _storage_parquet_files(storage, bar_root, recursive=True))
        for file in objects:
            bar_dir = file.path.parent
            if not bar_dir.name.endswith(suffixes):
                continue
            symbol = _symbol_from_bar_type_name(bar_dir.name)
            resolved = file.path.resolve(strict=False)
            if resolved not in seen:
                seen.add(resolved)
                files.append(_CandidateFile(file=file, symbol_from_path=symbol))
    return files


def _is_metadata_parquet(path: Path) -> bool:
    return path.name.lower() in {
        "metadata.parquet",
        "catalog_metadata.parquet",
        "manifest.parquet",
        "_metadata.parquet",
        "_common_metadata.parquet",
    }


def _legacy_files(root: Path, source: str, storage: CatalogStorageProvider) -> list[StorageObject]:
    files = _safe_parquet_objects(root, _storage_parquet_files(storage, root, recursive=False))
    if root.name == source:
        return files
    source_matched = [file for file in files if _legacy_source_file_matches(file.path, source)]
    if source_matched:
        return source_matched
    if source == "klines":
        return [file for file in files if not _legacy_source_file_matches_any(file.path)]
    return []


_LEGACY_BAR_SOURCE_NAMES = tuple(
    source.lower() for source in ("klines", "markPriceKlines", "indexPriceKlines", "premiumIndexKlines")
)


def _legacy_source_file_matches(path: Path, source: str) -> bool:
    stem = path.stem.lower()
    source_name = source.lower()
    return stem == source_name or stem.startswith((f"{source_name}.", f"{source_name}_", f"{source_name}-"))


def _legacy_source_file_matches_any(path: Path) -> bool:
    return any(_legacy_source_file_matches(path, source) for source in _LEGACY_BAR_SOURCE_NAMES)


def _safe_parquet_objects(root: Path, objects: Sequence[StorageObject]) -> list[StorageObject]:
    resolved_root = root.resolve()
    safe: list[StorageObject] = []
    for obj in objects:
        if _is_metadata_parquet(obj.path):
            continue
        resolved = obj.path.resolve(strict=False)
        if not resolved.is_relative_to(resolved_root):
            raise ValueError(f"parquet file {obj.path} resolves outside catalog root {root}")
        safe.append(obj)
    return safe


def _safe_parquet_files(root: Path, paths: Sequence[Path]) -> list[Path]:
    resolved_root = root.resolve()
    safe: list[Path] = []
    for path in paths:
        if _is_metadata_parquet(path):
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(resolved_root):
            raise ValueError(f"parquet file {path} resolves outside catalog root {root}")
        safe.append(path)
    return safe


def _safe_bar_dir(bar_root: Path, symbol: str, interval: str, price_type: str) -> Path:
    bar_type_name = _make_bar_type_name(symbol, interval, price_type)
    bar_dir = bar_root / bar_type_name
    resolved_root = bar_root.resolve()
    resolved_bar_dir = bar_dir.resolve(strict=False)
    if not resolved_bar_dir.is_relative_to(resolved_root):
        raise ValueError(f"unsafe symbol path component: {symbol!r}")
    if bar_dir.name != bar_type_name:
        raise ValueError(f"unsafe symbol path component: {symbol!r}")
    return bar_dir


def _make_bar_type_name(symbol: str, interval: str, price_type: str) -> str:
    base = make_bar_type_str(symbol, interval)
    return base.removesuffix("-LAST-EXTERNAL") + f"-{price_type}-EXTERNAL"


def _price_types_for_source(source: str) -> tuple[str, ...]:
    if source in {"markPriceKlines", "indexPriceKlines"}:
        return ("LAST", "MID")
    if source in {"klines", "premiumIndexKlines"}:
        return ("LAST",)
    return ("LAST", "MID")


def _validate_bar_source(source: str) -> None:
    if WRITE_CATEGORY.get(source) != "bar":
        supported = sorted(data_type for data_type, category in WRITE_CATEGORY.items() if category == "bar")
        raise ValueError(f"unsupported bar source {source!r}; supported bar sources: {supported!r}")


def _symbol_from_bar_type_name(name: str) -> str:
    try:
        instrument, _step, _unit, _price_type, _source = name.rsplit("-", 4)
    except ValueError:
        instrument = name
    if instrument.endswith(".BINANCE"):
        return instrument.removesuffix(".BINANCE")
    return instrument


def _has_timestamp(columns: Sequence[str]) -> bool:
    return _first_present(columns, ["ts_event", "ts", "timestamp_ns"]) is not None


def _has_symbol_identity(columns: Sequence[str]) -> bool:
    return _first_present(columns, ["symbol", "instrument_id"]) is not None or "bar_type" in columns


def _empty_frame(fields: Sequence[str]) -> pl.DataFrame:
    schema: dict[str, pl.DataType] = {"ts": pl.Datetime("ns"), "symbol": pl.Utf8}
    schema.update({field: pl.Float64 for field in fields})
    return pl.DataFrame(schema=schema)


_INTEGER_DTYPES = {
    pl.Int8,
    pl.Int16,
    pl.Int32,
    pl.Int64,
    pl.UInt8,
    pl.UInt16,
    pl.UInt32,
    pl.UInt64,
}


def _datetime_lit(value: datetime) -> pl.Expr:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return pl.lit(value).cast(pl.Datetime("ns"))


def _validate_bar_type_values(
    scan: pl.LazyFrame,
    bar_type_col: str,
    request: ResearchDataRequest,
    file_path: Path,
) -> None:
    expected_suffixes = tuple(
        f"-{parse_interval(request.interval)}-{price_type}-EXTERNAL"
        for price_type in _price_types_for_source(request.source)
    )
    values = _collect_lazy_streaming(
        scan.select(pl.col(bar_type_col).cast(pl.Utf8).drop_nulls().unique())
    ).to_series().to_list()
    mismatches = [value for value in values if not str(value).endswith(expected_suffixes)]
    if mismatches:
        sample = sorted(str(value) for value in mismatches)[:3]
        raise ValueError(
            f"parquet file {file_path} contains bar_type values outside requested interval "
            f"{request.interval!r}: {sample!r}"
        )


def _validate_row_symbol_identity(
    scan: pl.LazyFrame,
    symbol_col: str | None,
    bar_type_col: str | None,
    symbol_from_path: str,
    file_path: Path,
) -> None:
    if symbol_col is None and bar_type_col is None:
        return
    aliases = _symbol_aliases(symbol_from_path)
    checks: list[tuple[str, pl.Expr]] = []
    if symbol_col is not None:
        checks.append((symbol_col, _clean_symbol_id(pl.col(symbol_col).cast(pl.Utf8)).alias("symbol")))
    if bar_type_col is not None:
        checks.append((bar_type_col, _clean_bar_type_symbol(pl.col(bar_type_col).cast(pl.Utf8)).alias("symbol")))
    for label, expr in checks:
        values = _collect_lazy_streaming(scan.select(expr.drop_nulls().unique())).to_series().to_list()
        mismatches = [value for value in values if str(value) not in aliases]
        if mismatches:
            sample = sorted(str(value) for value in mismatches)[:3]
            raise ValueError(
                f"parquet file {file_path} row symbol identity {label!r} does not match path symbol "
                f"{symbol_from_path!r}: {sample!r}"
            )


def _validate_flat_cadence(frame: pl.DataFrame, interval: str) -> None:
    expected = interval_to_nanoseconds(interval)
    if frame.height == 0:
        return
    for symbol, group in frame.sort(["symbol", "ts"]).partition_by("symbol", as_dict=True).items():
        ts_ns = group["ts"].cast(pl.Datetime("ns")).to_numpy().astype("datetime64[ns]").astype(np.int64)
        if len(ts_ns) < 2:
            continue
        diffs = np.diff(ts_ns)
        positive = diffs[diffs > 0]
        if len(positive) == 0:
            continue
        if not np.all(positive % expected == 0):
            raise ValueError(
                f"flat parquet cadence for symbol {symbol!r} is not aligned to requested interval {interval!r}"
            )


def _first_present(columns: Sequence[str], candidates: Sequence[str]) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def _timestamp_expr(column: str, dtype: pl.DataType, file_path: Path) -> pl.Expr:
    expr = pl.col(column)
    if dtype in _INTEGER_DTYPES:
        if column in {"timestamp_ns", "ts_event"}:
            return expr.cast(pl.Int64).cast(pl.Datetime("ns"))
        raise ValueError(
            f"parquet file {file_path} has ambiguous integer timestamp column {column!r}; "
            "use timestamp_ns or ts_event for nanosecond epochs"
        )
    return expr.cast(pl.Datetime("ns"))


def _bar_field_expr(field: str, dtype: pl.DataType) -> pl.Expr:
    if dtype.is_numeric() or dtype == pl.Binary:
        return _bar_value_expr(field, dtype)
    return pl.col(field).cast(pl.Float64)


def _symbol_expr(symbol_col: str | None, bar_type_col: str | None, symbol_from_path: str | None) -> pl.Expr:
    if symbol_from_path is not None:
        return pl.lit(symbol_from_path).cast(pl.Utf8)
    if bar_type_col is not None:
        return _clean_bar_type_symbol(pl.col(bar_type_col).cast(pl.Utf8))
    if symbol_col is not None:
        return _clean_symbol_id(pl.col(symbol_col).cast(pl.Utf8))
    raise ValueError("symbol cannot be derived")


def _clean_symbol_id(expr: pl.Expr) -> pl.Expr:
    return expr.str.replace(r"\.BINANCE$", "")


def _clean_bar_type_symbol(expr: pl.Expr) -> pl.Expr:
    # NT bar type strings end with -N-UNIT-PRICE-EXTERNAL and usually carry
    # a venue suffix before that, e.g. BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL.
    # Strip from the right so dotted instrument symbols survive intact.
    return expr.str.replace(r"-\d+-[A-Z]+-(?:LAST|MID|BID|ASK)-EXTERNAL$", "").str.replace(
        r"\.BINANCE$", ""
    )


def _symbol_aliases(symbol: str) -> set[str]:
    base = symbol.removesuffix(".BINANCE")
    aliases = {base}
    if base.endswith("-PERP"):
        aliases.add(base.removesuffix("-PERP"))
    return aliases


def _validate_requested_symbol_aliases(symbols: Sequence[str]) -> None:
    owner_by_alias: dict[str, str] = {}
    for symbol in symbols:
        for alias in _symbol_aliases(symbol):
            owner = owner_by_alias.setdefault(alias, symbol)
            if owner != symbol:
                raise ValueError(
                    f"ambiguous symbol aliases in requested universe: {owner!r} and {symbol!r}"
                )


def _canonical_symbol_expr(expr: pl.Expr, requested_symbols: Sequence[str]) -> pl.Expr:
    """Map exchange aliases back to the exact requested symbol string.

    Binance raw files may use BTCUSDT while NT instrument ids use
    BTCUSDT-PERP. Research panels should be stable for downstream matrices, so
    when the caller supplied an explicit universe we canonicalize any alias to
    that requested symbol before filtering.
    """
    if not requested_symbols:
        return expr
    out = expr
    for symbol in requested_symbols:
        aliases = sorted(_symbol_aliases(symbol))
        out = pl.when(expr.is_in(aliases)).then(pl.lit(symbol)).otherwise(out)
    return out
