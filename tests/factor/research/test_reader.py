from datetime import datetime, timezone

import polars as pl
import pytest

from tinohelm.factor.research.reader import ResearchDataRequest, ResearchParquetReader
from tinohelm.strategy.loader_helpers import make_bar_type_str


_NAUTILUS_FIXED_PRECISION_SCALE = 10_000_000_000_000_000


def _fixed_precision_bytes(value: int) -> bytes:
    return (value * _NAUTILUS_FIXED_PRECISION_SCALE).to_bytes(16, byteorder="little", signed=True)


def _request(**overrides):
    values = {
        "symbols": ("BTCUSDT",),
        "fields": ("close",),
        "interval": "1m",
        "start": None,
        "end": None,
        "source": "klines",
    }
    values.update(overrides)
    return ResearchDataRequest(**values)


def test_invalid_interval_rejected_before_scanning(tmp_path):
    root = tmp_path / "missing"

    with pytest.raises(ValueError, match="Unsupported interval"):
        ResearchParquetReader(root).load_bars(_request(interval="2d"))


def test_source_aware_root_preferred_before_legacy(tmp_path):
    source_root = tmp_path / "bar" / "klines"
    source_root.mkdir(parents=True)
    tmp_path.mkdir(exist_ok=True)
    pl.DataFrame({
        "ts_event": [datetime(2024, 1, 1)],
        "symbol": ["BTCUSDT"],
        "close": [100.0],
    }).write_parquet(source_root / "source.parquet")
    pl.DataFrame({
        "ts_event": [datetime(2024, 1, 1)],
        "symbol": ["BTCUSDT"],
        "close": [1.0],
    }).write_parquet(tmp_path / "legacy.parquet")

    bars = ResearchParquetReader(tmp_path).load_bars(_request())

    assert bars.frame["close"].to_list() == [100.0]


def test_projection_and_time_filter(tmp_path):
    root = tmp_path / "bar" / "klines"
    root.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [datetime(2024, 1, 1, 0, 0), datetime(2024, 1, 1, 0, 1), datetime(2024, 1, 1, 0, 2)],
        "symbol": ["BTCUSDT", "BTCUSDT", "ETHUSDT"],
        "open": [1, 2, 3],
        "close": [10, 20, 30],
        "volume": [100, 200, 300],
    }).write_parquet(root / "bars.parquet")

    bars = ResearchParquetReader(tmp_path).load_bars(_request(
        fields=("open", "close"),
        start=datetime(2024, 1, 1, 0, 1),
        end=datetime(2024, 1, 1, 0, 2),
    ))

    assert bars.frame.columns == ["ts", "symbol", "open", "close"]
    assert bars.frame["close"].to_list() == [20.0]


def test_numeric_string_bar_values_still_cast_to_float(tmp_path):
    root = tmp_path / "bar" / "klines"
    root.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [datetime(2024, 1, 1)],
        "symbol": ["BTCUSDT"],
        "close": ["100.5"],
    }).write_parquet(root / "bars.parquet")

    bars = ResearchParquetReader(tmp_path).load_bars(_request())

    assert bars.frame["close"].to_list() == [100.5]


def test_timezone_aware_time_filter_is_normalized_to_utc_naive(tmp_path):
    root = tmp_path / "bar" / "klines"
    root.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [datetime(2024, 1, 1, 0), datetime(2024, 1, 1, 0, 1)],
        "symbol": ["BTCUSDT", "BTCUSDT"],
        "close": [10, 20],
    }).write_parquet(root / "bars.parquet")

    bars = ResearchParquetReader(tmp_path).load_bars(_request(
        start=datetime(2024, 1, 1, 0, 0, 30, tzinfo=timezone.utc),
        end=datetime(2024, 1, 1, 0, 1, 30, tzinfo=timezone.utc),
    ))

    assert bars.frame["close"].to_list() == [20.0]


def test_dotted_plain_symbol_is_not_truncated(tmp_path):
    root = tmp_path / "bar" / "klines"
    root.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [datetime(2024, 1, 1)],
        "symbol": ["BTC.USDT"],
        "close": [10],
    }).write_parquet(root / "bars.parquet")

    bars = ResearchParquetReader(tmp_path).load_bars(_request(symbols=("BTC.USDT",)))

    assert bars.frame["symbol"].to_list() == ["BTC.USDT"]


def test_dotted_symbol_from_bar_type_column_is_not_truncated(tmp_path):
    root = tmp_path / "bar" / "klines"
    root.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [datetime(2024, 1, 1)],
        "bar_type": ["BTC.USDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"],
        "close": [10],
    }).write_parquet(root / "bars.parquet")

    bars = ResearchParquetReader(tmp_path).load_bars(_request(symbols=("BTC.USDT-PERP",)))

    assert bars.frame["symbol"].to_list() == ["BTC.USDT-PERP"]


def test_generic_timestamp_column_is_rejected(tmp_path):
    root = tmp_path / "bar" / "klines"
    root.mkdir(parents=True)
    pl.DataFrame({
        "timestamp": [datetime(2024, 1, 1)],
        "symbol": ["BTCUSDT"],
        "close": [10],
    }).write_parquet(root / "bars.parquet")

    with pytest.raises(ValueError, match="generic 'timestamp'"):
        ResearchParquetReader(tmp_path).load_bars(_request())


def test_duplicate_ts_symbol_raises(tmp_path):
    root = tmp_path / "bar" / "klines"
    root.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [datetime(2024, 1, 1), datetime(2024, 1, 1)],
        "symbol": ["BTCUSDT", "BTCUSDT"],
        "close": [1, 2],
    }).write_parquet(root / "dupes.parquet")

    with pytest.raises(ValueError, match="duplicate"):
        ResearchParquetReader(tmp_path).load_bars(_request())


def test_empty_data_returns_valid_empty_canonical_frame(tmp_path):
    bars = ResearchParquetReader(tmp_path).load_bars(_request(fields=("open", "close")))

    assert bars.frame.columns == ["ts", "symbol", "open", "close"]
    assert bars.frame.height == 0


def test_nt_style_bar_type_preserves_perp_suffix_and_matches_request_symbol(tmp_path):
    root = tmp_path / "bar" / "klines"
    root.mkdir(parents=True)
    pl.DataFrame({
        "timestamp_ns": [1_704_067_200_000_000_000],
        "bar_type": ["BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"],
        "close": [42],
    }).write_parquet(root / "nt.parquet")

    bars = ResearchParquetReader(tmp_path).load_bars(_request(symbols=("BTCUSDT-PERP",)))

    assert bars.frame["symbol"].to_list() == ["BTCUSDT-PERP"]
    assert bars.frame["close"].to_list() == [42.0]


def test_bar_type_interval_mismatch_is_rejected(tmp_path):
    root = tmp_path / "bar" / "klines"
    root.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [1_704_067_200_000_000_000, 1_704_067_500_000_000_000],
        "bar_type": [
            "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL",
            "BTCUSDT-PERP.BINANCE-5-MINUTE-LAST-EXTERNAL",
        ],
        "close": [42, 43],
    }).write_parquet(root / "nt.parquet")

    with pytest.raises(ValueError, match="outside requested interval"):
        ResearchParquetReader(tmp_path).load_bars(_request(symbols=("BTCUSDT-PERP",), interval="1m"))


def test_exchange_symbol_alias_maps_to_requested_perp_symbol(tmp_path):
    root = tmp_path / "bar" / "klines"
    root.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [datetime(2024, 1, 1)],
        "symbol": ["BTCUSDT"],
        "close": [42],
    }).write_parquet(root / "alias.parquet")

    bars = ResearchParquetReader(tmp_path).load_bars(_request(symbols=("BTCUSDT-PERP",)))

    assert bars.frame["symbol"].to_list() == ["BTCUSDT-PERP"]
    assert bars.frame["close"].to_list() == [42.0]


def test_requested_bare_symbol_does_not_consume_perp_identity(tmp_path):
    root = tmp_path / "bar" / "klines"
    root.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [datetime(2024, 1, 1)],
        "bar_type": ["BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"],
        "close": [42],
    }).write_parquet(root / "perp.parquet")

    bars = ResearchParquetReader(tmp_path).load_bars(_request(symbols=("BTCUSDT",)))

    assert bars.frame.height == 0
    assert bars.symbols == ("BTCUSDT",)


def test_hybrid_flat_schema_prefers_bar_type_over_bare_symbol(tmp_path):
    root = tmp_path / "bar" / "klines"
    root.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [datetime(2024, 1, 1)],
        "symbol": ["BTCUSDT"],
        "bar_type": ["BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"],
        "close": [42],
    }).write_parquet(root / "hybrid.parquet")

    bare = ResearchParquetReader(tmp_path).load_bars(_request(symbols=("BTCUSDT",)))
    perp = ResearchParquetReader(tmp_path).load_bars(_request(symbols=("BTCUSDT-PERP",)))

    assert bare.frame.height == 0
    assert perp.frame["symbol"].to_list() == ["BTCUSDT-PERP"]
    assert perp.frame["close"].to_list() == [42.0]


def test_missing_symbol_identity_raises(tmp_path):
    root = tmp_path / "bar" / "klines"
    root.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [datetime(2024, 1, 1)],
        "close": [1],
    }).write_parquet(root / "bad.parquet")

    with pytest.raises(ValueError, match="lacks symbol identity"):
        ResearchParquetReader(tmp_path).load_bars(_request())


def test_nt_layout_derives_symbol_from_bar_type_directory_and_filters_interval(tmp_path):
    root = tmp_path / "bar" / "klines" / "data" / "bar"
    one_minute = root / make_bar_type_str("BTCUSDT-PERP", "1m")
    five_minute = root / make_bar_type_str("BTCUSDT-PERP", "5m")
    one_minute.mkdir(parents=True)
    five_minute.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [1_704_067_200_000_000_000],
        "open": [90],
        "high": [110],
        "low": [80],
        "close": [100],
        "volume": [10],
    }).write_parquet(one_minute / "bars.parquet")
    pl.DataFrame({
        "ts_event": [1_704_067_200_000_000_000],
        "open": [490],
        "high": [510],
        "low": [480],
        "close": [500],
        "volume": [10],
    }).write_parquet(five_minute / "bars.parquet")

    bars = ResearchParquetReader(tmp_path).load_bars(_request(symbols=("BTCUSDT-PERP",), interval="1m"))

    assert bars.frame["symbol"].to_list() == ["BTCUSDT-PERP"]
    assert bars.frame["close"].to_list() == [100.0]


def test_nt_layout_rejects_row_symbol_mismatch_under_bar_type_directory(tmp_path):
    root = tmp_path / "bar" / "klines" / "data" / "bar"
    bar_dir = root / make_bar_type_str("BTCUSDT-PERP", "1m")
    bar_dir.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [1_704_067_200_000_000_000],
        "symbol": ["ETHUSDT-PERP"],
        "close": [100],
    }).write_parquet(bar_dir / "bars.parquet")

    with pytest.raises(ValueError, match="row symbol identity"):
        ResearchParquetReader(tmp_path).load_bars(_request(symbols=("BTCUSDT-PERP",), interval="1m"))


def test_nt_layout_rejects_bar_type_symbol_mismatch_under_bar_type_directory(tmp_path):
    root = tmp_path / "bar" / "klines" / "data" / "bar"
    bar_dir = root / make_bar_type_str("BTCUSDT-PERP", "1m")
    bar_dir.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [1_704_067_200_000_000_000],
        "bar_type": ["ETHUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"],
        "close": [100],
    }).write_parquet(bar_dir / "bars.parquet")

    with pytest.raises(ValueError, match="row symbol identity"):
        ResearchParquetReader(tmp_path).load_bars(_request(symbols=("BTCUSDT-PERP",), interval="1m"))


def test_nt_layout_derives_dotted_symbol_from_bar_type_directory(tmp_path):
    root = tmp_path / "bar" / "klines" / "data" / "bar"
    bar_dir = root / make_bar_type_str("BTC.USDT-PERP", "1m")
    bar_dir.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [1_704_067_200_000_000_000],
        "close": [100],
    }).write_parquet(bar_dir / "bars.parquet")

    bars = ResearchParquetReader(tmp_path).load_bars(_request(symbols=("BTC.USDT-PERP",), interval="1m"))

    assert bars.frame["symbol"].to_list() == ["BTC.USDT-PERP"]


def test_nt_layout_reads_base_catalog_for_default_klines_source(tmp_path):
    root = tmp_path / "data" / "bar"
    bar_dir = root / make_bar_type_str("BTCUSDT-PERP", "1m")
    bar_dir.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [1_704_067_200_000_000_000],
        "close": [100],
    }).write_parquet(bar_dir / "bars.parquet")

    bars = ResearchParquetReader(tmp_path).load_bars(_request(symbols=("BTCUSDT-PERP",), interval="1m"))

    assert bars.frame["close"].to_list() == [100.0]


def test_nt_layout_decodes_fixed_precision_binary_bar_values(tmp_path):
    root = tmp_path / "bar" / "klines" / "data" / "bar"
    bar_dir = root / make_bar_type_str("BTCUSDT-PERP", "1m")
    bar_dir.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [1_704_067_200_000_000_000],
        "open": [_fixed_precision_bytes(90)],
        "high": [_fixed_precision_bytes(110)],
        "low": [_fixed_precision_bytes(80)],
        "close": [_fixed_precision_bytes(100)],
        "volume": [_fixed_precision_bytes(10)],
    }).write_parquet(bar_dir / "fixed.parquet")

    bars = ResearchParquetReader(tmp_path).load_bars(_request(
        symbols=("BTCUSDT-PERP",),
        fields=("open", "high", "low", "close", "volume"),
        interval="1m",
    ))

    assert bars.frame.select(["open", "high", "low", "close", "volume"]).row(0) == (
        90.0,
        110.0,
        80.0,
        100.0,
        10.0,
    )


def test_nt_layout_accepts_mid_price_type_for_mark_price_source(tmp_path):
    root = tmp_path / "bar" / "markPriceKlines" / "data" / "bar"
    bar_dir = root / make_bar_type_str("BTCUSDT-PERP", "1m").replace("-LAST-EXTERNAL", "-MID-EXTERNAL")
    bar_dir.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [1_704_067_200_000_000_000],
        "close": [100],
    }).write_parquet(bar_dir / "bars.parquet")

    bars = ResearchParquetReader(tmp_path).load_bars(_request(
        symbols=("BTCUSDT-PERP",),
        interval="1m",
        source="markPriceKlines",
    ))

    assert bars.frame["close"].to_list() == [100.0]


def test_nt_layout_accepts_current_last_price_type_for_mark_price_source(tmp_path):
    root = tmp_path / "bar" / "markPriceKlines"
    root.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [1_704_067_200_000_000_000],
        "bar_type": ["BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"],
        "close": [100],
    }).write_parquet(root / "bars.parquet")

    bars = ResearchParquetReader(tmp_path).load_bars(_request(
        symbols=("BTCUSDT-PERP",),
        interval="1m",
        source="markPriceKlines",
    ))

    assert bars.frame["close"].to_list() == [100.0]


def test_nt_layout_rejects_ambiguous_alias_directories(tmp_path):
    root = tmp_path / "bar" / "klines" / "data" / "bar"
    perp_dir = root / make_bar_type_str("BTCUSDT-PERP", "1m")
    alias_dir = root / make_bar_type_str("BTCUSDT", "1m")
    perp_dir.mkdir(parents=True)
    alias_dir.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [1_704_067_200_000_000_000],
        "close": [100],
    }).write_parquet(perp_dir / "bars.parquet")
    pl.DataFrame({
        "ts_event": [1_704_067_260_000_000_000],
        "close": [101],
    }).write_parquet(alias_dir / "bars.parquet")

    with pytest.raises(ValueError, match="ambiguous bar directories"):
        ResearchParquetReader(tmp_path).load_bars(_request(symbols=("BTCUSDT-PERP",), interval="1m"))


def test_flat_candidate_missing_timestamp_fails_fast(tmp_path):
    root = tmp_path / "bar" / "klines"
    root.mkdir(parents=True)
    pl.DataFrame({
        "symbol": ["BTCUSDT"],
        "close": [100],
    }).write_parquet(root / "bars.parquet")

    with pytest.raises(ValueError, match="lacks a timestamp"):
        ResearchParquetReader(tmp_path).load_bars(_request())


def test_flat_candidate_missing_requested_field_fails_fast(tmp_path):
    root = tmp_path / "bar" / "klines"
    root.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [datetime(2024, 1, 1)],
        "symbol": ["BTCUSDT"],
        "open": [100],
    }).write_parquet(root / "bars.parquet")

    with pytest.raises(ValueError, match="missing fields"):
        ResearchParquetReader(tmp_path).load_bars(_request(fields=("close",)))


def test_flat_candidate_cadence_must_match_requested_interval(tmp_path):
    root = tmp_path / "bar" / "klines"
    root.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [datetime(2024, 1, 1, 0, 0), datetime(2024, 1, 1, 0, 1)],
        "symbol": ["BTCUSDT", "BTCUSDT"],
        "close": [100, 101],
    }).write_parquet(root / "bars.parquet")

    with pytest.raises(ValueError, match="cadence"):
        ResearchParquetReader(tmp_path).load_bars(_request(interval="5m"))


def test_flat_candidate_allows_missing_bars_aligned_to_interval(tmp_path):
    root = tmp_path / "bar" / "klines"
    root.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [datetime(2024, 1, 1, 0, 0), datetime(2024, 1, 1, 0, 2)],
        "symbol": ["BTCUSDT", "BTCUSDT"],
        "close": [100, 102],
    }).write_parquet(root / "bars.parquet")

    bars = ResearchParquetReader(tmp_path).load_bars(_request(interval="1m"))

    assert bars.frame["close"].to_list() == [100.0, 102.0]


def test_flat_metadata_only_is_ignored_as_no_data(tmp_path):
    root = tmp_path / "bar" / "klines"
    root.mkdir(parents=True)
    pl.DataFrame({
        "metadata_key": ["not-a-bar"],
        "metadata_value": ["ignored"],
    }).write_parquet(root / "metadata.parquet")

    bars = ResearchParquetReader(tmp_path).load_bars(_request())

    assert bars.frame.height == 0


def test_known_bar_source_does_not_fallback_to_root_level_parquet(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    pl.DataFrame({
        "ts_event": [datetime(2024, 1, 1)],
        "symbol": ["BTCUSDT"],
        "close": [100],
    }).write_parquet(tmp_path / "klines.parquet")

    bars = ResearchParquetReader(tmp_path).load_bars(_request(source="markPriceKlines"))

    assert bars.frame.height == 0


def test_unknown_source_is_rejected_before_base_fallback(tmp_path):
    pl.DataFrame({
        "ts_event": [datetime(2024, 1, 1)],
        "symbol": ["BTCUSDT"],
        "close": [100],
    }).write_parquet(tmp_path / "klines.parquet")

    with pytest.raises(ValueError, match="unsupported bar source"):
        ResearchParquetReader(tmp_path).load_bars(_request(source="klinss"))


def test_non_bar_source_is_rejected_before_base_fallback(tmp_path):
    pl.DataFrame({
        "ts_event": [datetime(2024, 1, 1)],
        "symbol": ["BTCUSDT"],
        "close": [100],
    }).write_parquet(tmp_path / "klines.parquet")

    with pytest.raises(ValueError, match="unsupported bar source"):
        ResearchParquetReader(tmp_path).load_bars(_request(source="fundingRate"))


def test_legacy_base_fallback_keeps_kline_family_sources_isolated(tmp_path):
    pl.DataFrame({
        "ts_event": [datetime(2024, 1, 1, 0, 0)],
        "symbol": ["BTCUSDT"],
        "close": [100],
    }).write_parquet(tmp_path / "klines.parquet")
    pl.DataFrame({
        "ts_event": [datetime(2024, 1, 1, 0, 1)],
        "symbol": ["BTCUSDT"],
        "close": [200],
    }).write_parquet(tmp_path / "markPriceKlines.parquet")

    bars = ResearchParquetReader(tmp_path).load_bars(_request(source="klines"))

    assert bars.frame["close"].to_list() == [100.0]


def test_nt_layout_ignores_non_bar_metadata_parquet_under_catalog_root(tmp_path):
    root = tmp_path / "bar" / "klines"
    bar_dir = root / "data" / "bar" / make_bar_type_str("BTCUSDT-PERP", "1m")
    bar_dir.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [1_704_067_200_000_000_000],
        "close": [100],
    }).write_parquet(bar_dir / "bars.parquet")
    pl.DataFrame({
        "metadata_key": ["not-a-bar"],
        "metadata_value": ["ignored"],
    }).write_parquet(root / "metadata.parquet")

    bars = ResearchParquetReader(tmp_path).load_bars(_request(symbols=("BTCUSDT-PERP",), interval="1m"))

    assert bars.frame["close"].to_list() == [100.0]


def test_nt_layout_ignores_metadata_parquet_inside_bar_type_directory(tmp_path):
    root = tmp_path / "bar" / "klines"
    bar_dir = root / "data" / "bar" / make_bar_type_str("BTCUSDT-PERP", "1m")
    bar_dir.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [1_704_067_200_000_000_000],
        "close": [100],
    }).write_parquet(bar_dir / "bars.parquet")
    pl.DataFrame({
        "metadata_key": ["not-a-bar"],
        "metadata_value": ["ignored"],
    }).write_parquet(bar_dir / "_metadata.parquet")

    bars = ResearchParquetReader(tmp_path).load_bars(_request(symbols=("BTCUSDT-PERP",), interval="1m"))

    assert bars.frame["close"].to_list() == [100.0]


def test_reader_carries_requested_symbols_for_missing_assets(tmp_path):
    root = tmp_path / "bar" / "klines" / "data" / "bar"
    bar_dir = root / make_bar_type_str("BTCUSDT-PERP", "1m")
    bar_dir.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [1_704_067_200_000_000_000],
        "close": [100],
    }).write_parquet(bar_dir / "bars.parquet")

    bars = ResearchParquetReader(tmp_path).load_bars(
        _request(symbols=("BTCUSDT-PERP", "ETHUSDT-PERP"), interval="1m")
    )

    assert bars.symbols == ("BTCUSDT-PERP", "ETHUSDT-PERP")


def test_symbol_path_traversal_is_rejected_before_globbing(tmp_path):
    (tmp_path / "bar" / "klines" / "data" / "bar").mkdir(parents=True)

    with pytest.raises(ValueError, match="unsafe symbol"):
        ResearchParquetReader(tmp_path).load_bars(_request(symbols=("../BTCUSDT-PERP",)))


def test_symlinked_parquet_outside_catalog_is_rejected(tmp_path):
    outside = tmp_path / "outside.parquet"
    pl.DataFrame({
        "ts_event": [1_704_067_200_000_000_000],
        "close": [100],
    }).write_parquet(outside)
    bar_dir = tmp_path / "bar" / "klines" / "data" / "bar" / make_bar_type_str("BTCUSDT-PERP", "1m")
    bar_dir.mkdir(parents=True)
    (bar_dir / "escape.parquet").symlink_to(outside)

    with pytest.raises(ValueError, match="outside catalog root"):
        ResearchParquetReader(tmp_path).load_bars(_request(symbols=("BTCUSDT-PERP",)))


def test_symlinked_source_root_outside_catalog_is_rejected(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}_outside_root"
    outside.mkdir()
    pl.DataFrame({
        "ts_event": [1_704_067_200_000_000_000],
        "symbol": ["BTCUSDT"],
        "close": [100],
    }).write_parquet(outside / "bars.parquet")
    source_parent = tmp_path / "bar"
    source_parent.mkdir()
    (source_parent / "klines").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="outside catalog root"):
        ResearchParquetReader(tmp_path).load_bars(_request())


def test_ambiguous_alias_pair_in_requested_universe_is_rejected(tmp_path):
    (tmp_path / "bar" / "klines" / "data" / "bar").mkdir(parents=True)

    with pytest.raises(ValueError, match="ambiguous symbol aliases"):
        ResearchParquetReader(tmp_path).load_bars(_request(symbols=("BTCUSDT", "BTCUSDT-PERP")))
