from __future__ import annotations

from datetime import datetime
from pathlib import Path

import polars as pl

from tinohelm.factor.research.reader import ResearchDataRequest, ResearchParquetReader
from tinohelm.strategy.loader_helpers import make_bar_type_str


def test_research_reader_uses_lazy_scan_and_streaming_collect(tmp_path: Path, monkeypatch) -> None:
    bar_dir = tmp_path / "data" / "bar" / make_bar_type_str("BTCUSDT", "1m")
    bar_dir.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [datetime(2024, 1, 1), datetime(2024, 1, 1, 0, 1)],
        "symbol": ["BTCUSDT", "BTCUSDT"],
        "close": [100.0, 101.0],
    }).write_parquet(bar_dir / "bars.parquet")

    original_scan = pl.scan_parquet
    original_collect = pl.LazyFrame.collect
    calls: dict[str, list] = {"scan": [], "collect_engine": []}

    def scan_spy(*args, **kwargs):
        calls["scan"].append((args, kwargs))
        return original_scan(*args, **kwargs)

    def collect_spy(self, *args, **kwargs):
        calls["collect_engine"].append(kwargs.get("engine"))
        return original_collect(self, *args, **kwargs)

    monkeypatch.setattr(pl, "scan_parquet", scan_spy)
    monkeypatch.setattr(pl.LazyFrame, "collect", collect_spy)

    bars = ResearchParquetReader(tmp_path).load_bars(ResearchDataRequest(
        symbols=("BTCUSDT",),
        fields=("close",),
        interval="1m",
        start=None,
        end=None,
    ))

    assert bars.frame["close"].to_list() == [100.0, 101.0]
    assert calls["scan"]
    assert "streaming" in calls["collect_engine"]
