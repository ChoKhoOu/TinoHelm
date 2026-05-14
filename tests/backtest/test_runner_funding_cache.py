"""Tests for BacktestRunner funding-rate loading."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from tinohelm.backtest.runner import BacktestRunner


def _utc(y, m, d, hh=0, mm=0, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc)


def _ns(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000_000)


class _StubPrice:
    def __init__(self, value: float) -> None:
        self._value = value

    def as_double(self) -> float:
        return self._value

    def __float__(self) -> float:
        return float(self._value)


def _mk_runner(symbols, start, end) -> BacktestRunner:
    return BacktestRunner(
        strategy_path="x:X",
        config_path="x:XConfig",
        symbols=symbols,
        intervals=["1m"],
        start=start,
        end=end,
    )


class FakeCatalog:
    def __init__(self, funding_updates, mark_updates) -> None:
        self.funding_updates = funding_updates
        self.mark_updates = mark_updates
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def query(self, data_cls, identifiers, start, end):
        self.calls.append((data_cls.__name__, tuple(identifiers)))
        if data_cls.__name__ == "FundingRateUpdate":
            return self.funding_updates
        if data_cls.__name__ == "MarkPriceUpdate":
            return self.mark_updates
        return []


class TestLoadFundingRatesNativeUpdates:
    def test_uses_nt_updates_without_fetch_when_covered(self, monkeypatch):
        runner = _mk_runner(
            ["BTCUSDT-PERP"],
            start=_utc(2024, 1, 10, 8),
            end=_utc(2024, 1, 10, 8),
        )
        runner.__dict__["_redis_client"] = object()

        fake_catalog = FakeCatalog(
            funding_updates=[
                SimpleNamespace(
                    ts_event=_ns(_utc(2024, 1, 10, 8)),
                    rate=Decimal("0.0001"),
                ),
            ],
            mark_updates=[
                SimpleNamespace(
                    ts_event=_ns(_utc(2024, 1, 10, 7, 59)),
                    value=_StubPrice(101.0),
                ),
                SimpleNamespace(
                    ts_event=_ns(_utc(2024, 1, 10, 8, 1)),
                    value=_StubPrice(102.0),
                ),
            ],
        )
        monkeypatch.setattr(runner, "_catalog_for_path", lambda path: fake_catalog)

        async def _unexpected_fetch(*_args, **_kwargs):
            raise AssertionError("unexpected fetch")

        monkeypatch.setattr(runner, "_submit_and_wait_fetch", _unexpected_fetch)

        import tinohelm.data.instruments as instr_mod
        monkeypatch.setattr(instr_mod, "fetch_funding_info", lambda: {"BTCUSDT": 8})

        events = asyncio.run(runner._load_funding_rates(["BTCUSDT-PERP.BINANCE"]))

        assert {name for name, _ in fake_catalog.calls} == {
            "FundingRateUpdate",
            "MarkPriceUpdate",
        }
        assert events == [{
            "timestamp_ns": _ns(_utc(2024, 1, 10, 8)),
            "timestamp_iso": _utc(2024, 1, 10, 8).isoformat(),
            "symbol": "BTCUSDT-PERP.BINANCE",
            "rate": 0.0001,
            "mark_price": 101.0,
            "funding_interval_minutes": 480,
        }]

    def test_gap_in_funding_updates_enqueues_fetch(self, monkeypatch):
        runner = _mk_runner(
            ["BTCUSDT-PERP"],
            start=_utc(2024, 1, 10, 8),
            end=_utc(2024, 1, 10, 8, 5),
        )
        runner.__dict__["_redis_client"] = object()

        fake_catalog = FakeCatalog(
            funding_updates=[
                SimpleNamespace(
                    ts_event=_ns(_utc(2024, 1, 10, 8)),
                    rate=Decimal("0.0001"),
                ),
            ],
            mark_updates=[
                SimpleNamespace(
                    ts_event=_ns(_utc(2024, 1, 10, 7, 59)),
                    value=_StubPrice(101.0),
                ),
            ],
        )
        monkeypatch.setattr(runner, "_catalog_for_path", lambda path: fake_catalog)

        captured: list[dict] = []

        async def _spy(sym, ivl=None, *, data_type=None, start_override=None):
            captured.append({
                "sym": sym,
                "data_type": data_type,
                "start_override": start_override,
            })
            return True

        monkeypatch.setattr(runner, "_submit_and_wait_fetch", _spy)

        import tinohelm.data.instruments as instr_mod
        monkeypatch.setattr(instr_mod, "fetch_funding_info", lambda: {"BTCUSDT": 8})

        asyncio.run(runner._load_funding_rates(["BTCUSDT-PERP.BINANCE"]))

        assert len(captured) == 1
        assert captured[0]["sym"] == "BTCUSDT-PERP"
        assert captured[0]["data_type"] == "fundingRate"
        assert captured[0]["start_override"] == _utc(2024, 1, 10, 8) + timedelta(milliseconds=1)
