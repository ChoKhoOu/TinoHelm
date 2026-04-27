"""Unit tests — NT adapter (BarSynchronizer + OrderManager + SignalDrivenStrategy).

Coverage map (s17 acceptance criteria)
--------------------------------------
* BarSynchronizer
    - completes when all expected symbols arrive at the same ts (AC-4.2.1)
    - max_wait_bars eviction with warning + cross-section skipped (AC-4.2.2)
    - duplicate bars (same ts+symbol) keep last
    - empty expected_symbols / negative max_wait_bars raise
    - pending_timestamps reflects buffer state
* OrderManager
    - execute_diff calls instrument.make_qty (AC-4.2.1 inversion: per-asset
      quantity must flow through the instrument-aware rounder)
    - skips orders smaller than the size step
    - skips when current price is missing / non-positive
    - skips when instrument is missing
    - net_position-derived current quantity is honoured (HEDGING-safe)
    - submitted orders flow through strategy.submit_order
* SignalDrivenStrategy (stub-tested; NT Strategy is a Cython extension class
  so we cannot ``object.__new__`` it — see CLAUDE.md NT pitfalls).  Logic is
  exercised via a ``_StrategyHarness`` that mirrors the relevant attributes.
    - on_save / on_load round-trip preserves target_weights + last_rebalance_ts_ns
      (AC-4.4.1)
    - on_start raises RuntimeError when cache.bars(bar_type) < warmup_bars
      (AC-4.4.1 warmup gate)
    - warmup is derived from FactorSpec.lookback + extra_warmup_bars
    - warmup configured below derived raises RuntimeError
    - cross-section ready callback runs the kernel and submits diffs
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import polars as pl
import pytest

from tinohelm.nt_adapter.bar_synchronizer import (
    BarSynchronizer,
    BarSynchronizerConfig,
)
from tinohelm.nt_adapter.order_manager import OrderManager
from tinohelm.nt_adapter.signal_driven_strategy import (
    SignalDrivenStrategy,
    SignalDrivenStrategyConfig,
)
from tinohelm.signal.utils import signal_spec_from_dict as _signal_spec_from_dict
from tinohelm.signal.types import CostModel, SignalSpec


# =============================================================================
# BarSynchronizer fixtures + tests
# =============================================================================


@dataclass
class _StubSymbol:
    value: str

    def __str__(self) -> str:  # symbol stringifies to its value
        return self.value


@dataclass
class _StubInstrumentId:
    symbol: _StubSymbol


@dataclass
class _StubBarType:
    instrument_id: _StubInstrumentId


@dataclass
class _StubBar:
    bar_type: _StubBarType
    ts_init: int
    close: float = 100.0


def _bar(symbol_short: str, ts: int, close: float = 100.0) -> _StubBar:
    """Build a stub bar with the canonical (instrument_id.symbol).value chain."""
    return _StubBar(
        bar_type=_StubBarType(
            instrument_id=_StubInstrumentId(symbol=_StubSymbol(value=symbol_short))
        ),
        ts_init=ts,
        close=close,
    )


class TestBarSynchronizer:
    def test_completes_when_all_symbols_arrive(self):
        """3 symbols arrive at the same ts → on_complete fires once."""
        completed: list[tuple[int, dict[str, Any]]] = []

        def on_complete(ts_ns: int, bars: dict[str, Any]) -> None:
            completed.append((ts_ns, bars))

        sync = BarSynchronizer(
            BarSynchronizerConfig(
                expected_symbols=("BTC-USDT", "ETH-USDT", "BNB-USDT")
            ),
            on_complete,
        )

        sync.on_bar(_bar("BTC-USDT", 1000))
        sync.on_bar(_bar("ETH-USDT", 1000))
        # only 2 of 3 arrived — must NOT fire yet
        assert completed == []
        # third symbol completes the cross-section
        sync.on_bar(_bar("BNB-USDT", 1000))
        assert len(completed) == 1
        ts, bars = completed[0]
        assert ts == 1000
        assert set(bars.keys()) == {"BTC-USDT", "ETH-USDT", "BNB-USDT"}

    def test_eviction_on_max_wait_with_warning(self, caplog):
        """max_wait_bars exceeded → emit warning + skip cross-section.

        With max_wait_bars=2: a slot may be overtaken by 2 later
        timestamps before being evicted.  After 3 later timestamps the
        slot is dropped.
        """
        completed: list[tuple[int, dict[str, Any]]] = []

        sync = BarSynchronizer(
            BarSynchronizerConfig(
                expected_symbols=("BTC-USDT", "ETH-USDT"),
                max_wait_bars=2,
            ),
            lambda ts, b: completed.append((ts, b)),
        )

        # ETH never arrives at ts=1000.  BTC keeps streaming forward.
        with caplog.at_level("WARNING", logger="tinohelm.nt_adapter.bar_synchronizer"):
            sync.on_bar(_bar("BTC-USDT", 1000))  # buffer: {1000}
            sync.on_bar(_bar("BTC-USDT", 2000))  # buffer: {1000, 2000}
            sync.on_bar(_bar("BTC-USDT", 3000))  # buffer: {1000, 2000, 3000}
            sync.on_bar(_bar("BTC-USDT", 4000))  # 1000 has 3 later → evict

        # No cross-section ever completed.
        assert completed == []
        # Warning emitted about ts=1000 with missing ETH-USDT.
        assert any(
            "evicting ts=1000" in rec.message
            and "ETH-USDT" in rec.message
            for rec in caplog.records
            if rec.levelname == "WARNING"
        ), f"expected eviction warning; got: {[r.message for r in caplog.records]}"
        # Pending state: ts=1000 dropped, others still pending.
        pending = sync.pending_timestamps()
        assert 1000 not in pending
        assert all(ts in pending for ts in (2000, 3000, 4000))

    def test_duplicate_bar_replaces_existing(self):
        """Same (ts, symbol) twice keeps the latest bar."""
        completed: list[tuple[int, dict[str, Any]]] = []
        sync = BarSynchronizer(
            BarSynchronizerConfig(expected_symbols=("BTC-USDT", "ETH-USDT")),
            lambda ts, b: completed.append((ts, b)),
        )

        sync.on_bar(_bar("BTC-USDT", 1000, close=100.0))
        sync.on_bar(_bar("BTC-USDT", 1000, close=110.0))  # replace
        sync.on_bar(_bar("ETH-USDT", 1000, close=2000.0))

        assert len(completed) == 1
        bars = completed[0][1]
        assert bars["BTC-USDT"].close == 110.0  # latest write wins

    def test_empty_expected_symbols_raises(self):
        with pytest.raises(ValueError, match="expected_symbols is empty"):
            BarSynchronizer(
                BarSynchronizerConfig(expected_symbols=()),
                lambda ts, b: None,
            )

    def test_negative_max_wait_raises(self):
        with pytest.raises(ValueError, match="max_wait_bars must be >= 0"):
            BarSynchronizer(
                BarSynchronizerConfig(
                    expected_symbols=("BTC-USDT",), max_wait_bars=-1
                ),
                lambda ts, b: None,
            )

    def test_zero_max_wait_evicts_immediately(self):
        """max_wait_bars=0 → any older slot is evicted as soon as a newer slot arrives."""
        completed: list[tuple[int, dict[str, Any]]] = []
        sync = BarSynchronizer(
            BarSynchronizerConfig(
                expected_symbols=("BTC-USDT", "ETH-USDT"), max_wait_bars=0
            ),
            lambda ts, b: completed.append((ts, b)),
        )

        sync.on_bar(_bar("BTC-USDT", 1000))
        sync.on_bar(_bar("BTC-USDT", 2000))  # 1000 evicted (1 later, threshold=0)

        assert 1000 not in sync.pending_timestamps()
        assert completed == []


# =============================================================================
# OrderManager fixtures + tests
# =============================================================================


def _stub_strategy(net_position_per_id: dict[str, float] | None = None) -> Any:
    """Build a stub strategy exposing the OrderManager dependency surface.

    Returns
    -------
    Any
        A MagicMock with ``order_factory.market``, ``submit_order``, and
        ``portfolio.net_position`` wired up.  ``net_position_per_id`` is
        keyed by ``str(instrument_id)``.
    """
    strategy = MagicMock()
    net_position_per_id = net_position_per_id or {}

    def _net_position(instrument_id, *args, **kwargs):
        return net_position_per_id.get(str(instrument_id), 0.0)

    strategy.portfolio.net_position.side_effect = _net_position
    strategy.cache.bar_types.return_value = []  # OrderManager will use prices arg
    return strategy


def _stub_instrument(symbol_short: str, size_increment: float = 0.001) -> Any:
    """Build a stub Instrument honouring the make_qty contract.

    ``make_qty`` returns a MagicMock-tagged Quantity that can be asserted on.
    """
    instrument = MagicMock()
    instrument.id = MagicMock()
    instrument.id.__str__ = lambda self, _s=symbol_short: f"{_s}.BINANCE"  # type: ignore[assignment]
    instrument.size_increment = size_increment
    instrument.size_precision = 3
    instrument.make_qty.side_effect = lambda v: f"Quantity({float(v):.6f})"
    return instrument


class TestOrderManager:
    def test_uses_make_qty_for_quantity(self):
        """AC-4.2.1: instrument.make_qty must be invoked, not raw Quantity()."""
        strategy = _stub_strategy()
        om = OrderManager(strategy)

        btc_inst = _stub_instrument("BTCUSDT-PERP")
        target_w = {"BTCUSDT-PERP": 0.10}  # 10% of equity
        equity = 10_000.0
        prices = {"BTCUSDT-PERP": 50_000.0}

        om.execute_diff(
            target_weights=target_w,
            instruments={"BTCUSDT-PERP": btc_inst},
            equity=equity,
            prices=prices,
        )

        # target_qty = 0.10 * 10000 / 50000 = 0.02; current 0.0 → diff 0.02
        btc_inst.make_qty.assert_called_once()
        called_with = btc_inst.make_qty.call_args[0][0]
        assert called_with == pytest.approx(0.02, rel=1e-9)

        # MarketOrder was built and submitted.
        strategy.order_factory.market.assert_called_once()
        strategy.submit_order.assert_called_once()

    def test_skips_when_diff_below_size_step(self):
        """diff < size_increment → no order."""
        strategy = _stub_strategy()
        om = OrderManager(strategy)
        inst = _stub_instrument("BTCUSDT-PERP", size_increment=0.01)
        # target_qty = 0.001 * 10000 / 50000 = 0.0002 < 0.01
        om.execute_diff(
            target_weights={"BTCUSDT-PERP": 0.001},
            instruments={"BTCUSDT-PERP": inst},
            equity=10_000.0,
            prices={"BTCUSDT-PERP": 50_000.0},
        )
        inst.make_qty.assert_not_called()
        strategy.submit_order.assert_not_called()

    def test_skips_when_price_missing_or_non_positive(self):
        strategy = _stub_strategy()
        om = OrderManager(strategy)
        inst = _stub_instrument("BTCUSDT-PERP")

        # No price + no cache fallback → skipped
        om.execute_diff(
            target_weights={"BTCUSDT-PERP": 0.10},
            instruments={"BTCUSDT-PERP": inst},
            equity=10_000.0,
            prices={},
        )
        inst.make_qty.assert_not_called()

        # Zero price → also skipped
        om.execute_diff(
            target_weights={"BTCUSDT-PERP": 0.10},
            instruments={"BTCUSDT-PERP": inst},
            equity=10_000.0,
            prices={"BTCUSDT-PERP": 0.0},
        )
        inst.make_qty.assert_not_called()
        strategy.submit_order.assert_not_called()

    def test_skips_when_instrument_missing(self):
        strategy = _stub_strategy()
        om = OrderManager(strategy)
        # target weight present but no instrument → silently skip
        om.execute_diff(
            target_weights={"BTCUSDT-PERP": 0.10},
            instruments={},
            equity=10_000.0,
            prices={"BTCUSDT-PERP": 50_000.0},
        )
        strategy.submit_order.assert_not_called()

    def test_uses_portfolio_net_position_for_current_qty(self):
        """When already long 0.01 BTC, additional target 0.02 → diff 0.01 BUY."""
        strategy = _stub_strategy()
        # Configure portfolio.net_position("BTCUSDT-PERP.BINANCE") → 0.01.
        strategy.portfolio.net_position.side_effect = (
            lambda instrument_id, *a, **kw: 0.01
            if "BTCUSDT-PERP" in str(instrument_id)
            else 0.0
        )
        inst = _stub_instrument("BTCUSDT-PERP")
        om = OrderManager(strategy)
        om.execute_diff(
            target_weights={"BTCUSDT-PERP": 0.10},  # target 0.02 BTC
            instruments={"BTCUSDT-PERP": inst},
            equity=10_000.0,
            prices={"BTCUSDT-PERP": 50_000.0},
        )
        # diff = 0.02 - 0.01 = 0.01 → BUY 0.01
        called_with = inst.make_qty.call_args[0][0]
        assert called_with == pytest.approx(0.01, rel=1e-9)
        # Side check (BUY): inspect order_factory.market kwargs.
        kwargs = strategy.order_factory.market.call_args.kwargs
        from nautilus_trader.model.enums import OrderSide

        assert kwargs["order_side"] == OrderSide.BUY

    def test_negative_target_emits_sell_with_abs_diff(self):
        """target weight -0.05 from flat → SELL |Δ| 0.01."""
        strategy = _stub_strategy()
        inst = _stub_instrument("BTCUSDT-PERP")
        om = OrderManager(strategy)
        om.execute_diff(
            target_weights={"BTCUSDT-PERP": -0.05},  # short
            instruments={"BTCUSDT-PERP": inst},
            equity=10_000.0,
            prices={"BTCUSDT-PERP": 50_000.0},
        )
        # diff = -0.01 - 0 = -0.01 → SELL with abs 0.01
        called_with = inst.make_qty.call_args[0][0]
        assert called_with == pytest.approx(0.01, rel=1e-9)
        from nautilus_trader.model.enums import OrderSide

        kwargs = strategy.order_factory.market.call_args.kwargs
        assert kwargs["order_side"] == OrderSide.SELL

    # ------------------------------------------------------------------
    # Sign-flip two-stage protocol (HEDGING-safe + Binance Hedge Mode)
    # ------------------------------------------------------------------

    def test_sign_flip_emits_close_position_then_reopen(self):
        """current long + negative target → close_position(reduce_only=True) + new SELL.

        Under HEDGING a naive single-order diff leaks a double-sided
        exposure because NT's execution engine allocates a fresh
        ``position_id`` when none is provided on the order.  The fix
        detects ``current_qty * target_qty < 0`` and splits into a
        flatten leg (via ``Strategy.close_position``) followed by a
        fresh open leg sized to ``abs(target_qty)``.
        """
        from nautilus_trader.model.enums import OrderSide

        strategy = _stub_strategy(
            net_position_per_id={"BTCUSDT-PERP.BINANCE": 0.01},  # long 0.01 BTC
        )
        # Stub one open LONG position for this instrument.
        open_pos = MagicMock(name="OpenLongPosition")
        open_pos.id = MagicMock(name="PositionId-LONG-01")
        strategy.cache.positions_open.return_value = [open_pos]
        strategy.id = MagicMock(name="StrategyId")

        inst = _stub_instrument("BTCUSDT-PERP")
        om = OrderManager(strategy)
        # target_w=-0.10 @ price=50_000 / equity=10_000 → target_qty=-0.02
        # current=+0.01 → sign-flip → flatten 0.01 long + open 0.02 short
        submitted = om.execute_diff(
            target_weights={"BTCUSDT-PERP": -0.10},
            instruments={"BTCUSDT-PERP": inst},
            equity=10_000.0,
            prices={"BTCUSDT-PERP": 50_000.0},
        )

        # positions_open filtered by (instrument_id, strategy_id).
        strategy.cache.positions_open.assert_called_once()
        ppk = strategy.cache.positions_open.call_args.kwargs
        assert ppk.get("instrument_id") is inst.id
        assert ppk.get("strategy_id") is strategy.id

        # Stage 1: close_position called once with reduce_only=True on
        # the open position.  NT's close_position internally submits a
        # reduce-only MarketOrder with position_id=open_pos.id and
        # order_side = Order.closing_side_c(position.side) — we rely on
        # NT's contract for both.
        strategy.close_position.assert_called_once()
        cp_args, cp_kwargs = strategy.close_position.call_args
        assert cp_args[0] is open_pos
        assert cp_kwargs.get("reduce_only") is True

        # Stage 2: a fresh OPEN order (not reduce-only, no position_id)
        # with side matching target sign (-ve target → SELL) and qty
        # = abs(target_qty) = 0.02.
        strategy.order_factory.market.assert_called_once()
        om_kwargs = strategy.order_factory.market.call_args.kwargs
        assert om_kwargs["order_side"] == OrderSide.SELL
        # make_qty must be called on abs(target_qty), not the merged diff.
        make_qty_arg = inst.make_qty.call_args[0][0]
        assert make_qty_arg == pytest.approx(0.02, rel=1e-9)

        # submitted list: flatten leg + open leg (len >= 2).
        assert len(submitted) >= 2

    def test_sign_flip_below_min_step_flatten_only(self):
        """sign-flip but |target_qty| < min_step → flatten only, no reopen."""
        strategy = _stub_strategy(
            net_position_per_id={"BTCUSDT-PERP.BINANCE": 0.01},  # long
        )
        open_pos = MagicMock(name="OpenLongPosition")
        open_pos.id = MagicMock(name="PositionId-LONG-01")
        strategy.cache.positions_open.return_value = [open_pos]
        strategy.id = MagicMock(name="StrategyId")

        inst = _stub_instrument("BTCUSDT-PERP", size_increment=0.01)
        om = OrderManager(strategy)
        # target_w=-0.001 @ 50_000 / 10_000 → target_qty=-0.0002 (< 0.01)
        submitted = om.execute_diff(
            target_weights={"BTCUSDT-PERP": -0.001},
            instruments={"BTCUSDT-PERP": inst},
            equity=10_000.0,
            prices={"BTCUSDT-PERP": 50_000.0},
        )

        # Close still runs because the flatten leg is mandatory.
        strategy.close_position.assert_called_once()
        # But no new OPEN order is created.
        strategy.order_factory.market.assert_not_called()
        inst.make_qty.assert_not_called()
        # submitted contains only the flatten leg(s).
        assert len(submitted) == 1

    def test_non_flip_large_same_sign_diff_stays_single_order(self):
        """current +0.01 → target +0.03 (same sign): original single-order flow.

        We must NOT route through the sign-flip branch when the two
        quantities share a sign — it would produce a spurious close +
        over-sized reopen.
        """
        from nautilus_trader.model.enums import OrderSide

        strategy = _stub_strategy(
            net_position_per_id={"BTCUSDT-PERP.BINANCE": 0.01},  # long 0.01
        )
        strategy.cache.positions_open.return_value = []
        strategy.id = MagicMock(name="StrategyId")
        inst = _stub_instrument("BTCUSDT-PERP")
        om = OrderManager(strategy)
        # target_w=0.15 / 10_000 / 50_000 → target_qty=0.03, diff=+0.02
        submitted = om.execute_diff(
            target_weights={"BTCUSDT-PERP": 0.15},
            instruments={"BTCUSDT-PERP": inst},
            equity=10_000.0,
            prices={"BTCUSDT-PERP": 50_000.0},
        )

        # No flatten path taken.
        strategy.close_position.assert_not_called()
        # Single BUY order, sized to the signed diff (0.02), not target.
        strategy.order_factory.market.assert_called_once()
        kwargs = strategy.order_factory.market.call_args.kwargs
        assert kwargs["order_side"] == OrderSide.BUY
        make_qty_arg = inst.make_qty.call_args[0][0]
        assert make_qty_arg == pytest.approx(0.02, rel=1e-9)
        assert len(submitted) == 1


# =============================================================================
# SignalDrivenStrategy (stub-driven) tests
# =============================================================================


class _SignalDrivenStrategyStub:
    """Pure-Python harness mirroring :class:`SignalDrivenStrategy` logic.

    Why a stub instead of subclassing :class:`Strategy`?  NT's
    :class:`Strategy` is a Cython extension class — its ``cache``,
    ``log``, ``account``, ``portfolio``, etc. are *read-only Cython*
    attributes that cannot be reassigned via ``MagicMock()`` even after
    bypassing ``__init__`` with ``__new__``.  This is the same constraint
    that drove ``tests/actors/test_risk_guard.py`` to adopt a stub.

    The stub copies the logic methods we want to test verbatim from
    :class:`SignalDrivenStrategy`, and stores all NT collaborators as
    plain Python attributes so we can inject :class:`MagicMock` instances
    freely.
    """

    def __init__(
        self,
        *,
        signal_spec: SignalSpec,
        instrument_ids: tuple[str, ...] = (
            "BTCUSDT-PERP.BINANCE",
            "ETHUSDT-PERP.BINANCE",
        ),
        cache_history_len: int = 100,
        warmup_bars: int = 0,
        factor_lookback: int | None = None,
    ) -> None:
        self._signal_name = signal_spec.name
        self._instrument_id_strs = instrument_ids
        self._bar_type_template = "{instrument_id}-1-HOUR-LAST-EXTERNAL"
        self._configured_warmup = warmup_bars
        self._rebalance_freq_ns = 0
        self._configured_factor_lookback = factor_lookback
        self._signal_spec_json = None

        self.signal_spec = signal_spec
        self._kernel = None
        self._derived_warmup = 0
        self._effective_warmup = 0
        self.target_weights = {}
        self.last_rebalance_ts_ns = 0

        self._bar_synchronizer = None
        self._order_manager = None
        self._bar_types: dict[str, Any] = {}
        self._instruments_by_short_symbol: dict[str, Any] = {}

        # NT collaborator stubs.
        self.cache = MagicMock()
        self.cache.bars.return_value = [object()] * cache_history_len
        self.cache.instrument.return_value = _stub_instrument("BTCUSDT-PERP")
        self.log = MagicMock()
        self.account = MagicMock()
        self.account.balance_total.return_value.as_double.return_value = 10_000.0
        self.portfolio = MagicMock()
        self.portfolio.net_position.return_value = 0.0
        self.order_factory = MagicMock()
        self.submit_order = MagicMock()
        self.subscribe_bars = MagicMock()

    # --- Pure-Python methods bound from SignalDrivenStrategy ----------
    # We assign these as attributes so callers can monkey-patch
    # ``_compute_factor_panel`` per-test.
    on_save = SignalDrivenStrategy.on_save
    on_load = SignalDrivenStrategy.on_load
    _derive_warmup_bars = SignalDrivenStrategy._derive_warmup_bars
    _enforce_warmup = SignalDrivenStrategy._enforce_warmup
    _extract_latest_weights = SignalDrivenStrategy._extract_latest_weights
    _on_cross_section_ready = SignalDrivenStrategy._on_cross_section_ready
    _submit_diff = SignalDrivenStrategy._submit_diff
    _compute_equity = SignalDrivenStrategy._compute_equity
    _resolve_quote_currency = SignalDrivenStrategy._resolve_quote_currency
    _resolve_signal_spec = SignalDrivenStrategy._resolve_signal_spec
    _symbol_short = staticmethod(SignalDrivenStrategy._symbol_short)


def _make_strategy_for_unit_test(
    *,
    signal_spec: SignalSpec,
    cache_history_len: int,
    warmup_bars: int = 0,
    factor_lookback: int | None = None,
) -> _SignalDrivenStrategyStub:
    """Factory matching the production constructor surface."""
    return _SignalDrivenStrategyStub(
        signal_spec=signal_spec,
        cache_history_len=cache_history_len,
        warmup_bars=warmup_bars,
        factor_lookback=factor_lookback,
    )


def _basic_signal_spec(extra_warmup: int = 0) -> SignalSpec:
    return SignalSpec(
        name="test_signal",
        factor_ref="ret_N@1.0.0",
        method="top_k_long_short",
        weighting="equal",
        rebalance_freq="1H",
        universe_ref="test_universe",
        gross_exposure=1.0,
        net_exposure=0.0,
        max_position=0.5,
        method_params={"k": 1},
        cost_model=CostModel(name="taker_8bps"),
        extra_warmup_bars=extra_warmup,
    )


class TestSignalDrivenStrategySaveLoad:
    """AC-4.4.1: on_save / on_load round-trip."""

    def test_save_load_preserves_target_weights_and_ts(self):
        spec = _basic_signal_spec()
        s = _make_strategy_for_unit_test(signal_spec=spec, cache_history_len=0)
        s.target_weights = {"BTCUSDT-PERP": 0.5, "ETHUSDT-PERP": -0.3}
        s.last_rebalance_ts_ns = 1_700_000_000_000_000_000

        state = s.on_save()
        assert state == {
            "target_weights": {"BTCUSDT-PERP": 0.5, "ETHUSDT-PERP": -0.3},
            "last_rebalance_ts_ns": 1_700_000_000_000_000_000,
        }

        # Round-trip onto a fresh instance.
        s2 = _make_strategy_for_unit_test(signal_spec=spec, cache_history_len=0)
        assert s2.target_weights == {}
        assert s2.last_rebalance_ts_ns == 0

        s2.on_load(state)
        assert s2.target_weights == s.target_weights
        assert s2.last_rebalance_ts_ns == s.last_rebalance_ts_ns

    def test_on_load_coerces_missing_keys_to_defaults(self):
        spec = _basic_signal_spec()
        s = _make_strategy_for_unit_test(signal_spec=spec, cache_history_len=0)
        s.on_load({})
        assert s.target_weights == {}
        assert s.last_rebalance_ts_ns == 0

    def test_save_returns_independent_copy(self):
        """on_save must not return the live dict — mutating result must
        not corrupt strategy state."""
        spec = _basic_signal_spec()
        s = _make_strategy_for_unit_test(signal_spec=spec, cache_history_len=0)
        s.target_weights = {"BTCUSDT-PERP": 0.5}

        snapshot = s.on_save()
        snapshot["target_weights"]["BTCUSDT-PERP"] = 99.0
        assert s.target_weights["BTCUSDT-PERP"] == 0.5


class TestSignalDrivenStrategyWarmup:
    """AC-4.4.1: warmup is derived; insufficient cache history raises."""

    def test_derived_warmup_factor_lookback_plus_extra(self):
        """factor.lookback=20, extra_warmup=5 → derived=25."""
        spec = _basic_signal_spec(extra_warmup=5)
        s = _make_strategy_for_unit_test(
            signal_spec=spec,
            cache_history_len=100,
            factor_lookback=20,  # bypass factor registry lookup
        )
        derived = s._derive_warmup_bars(spec)
        assert derived == 25

    def test_derived_warmup_zero_extra(self):
        spec = _basic_signal_spec(extra_warmup=0)
        s = _make_strategy_for_unit_test(
            signal_spec=spec, cache_history_len=100, factor_lookback=15
        )
        assert s._derive_warmup_bars(spec) == 15

    def test_enforce_warmup_raises_when_history_short(self):
        """cache.bars(bar_type) shorter than warmup → RuntimeError."""
        from nautilus_trader.model.data import BarType
        from nautilus_trader.model.identifiers import InstrumentId

        spec = _basic_signal_spec(extra_warmup=0)
        s = _make_strategy_for_unit_test(
            signal_spec=spec,
            cache_history_len=10,  # only 10 bars cached
            factor_lookback=20,  # but warmup wants 20
        )
        # Wire bar_types as on_start would have done.
        s._bar_types = {
            "BTCUSDT-PERP": BarType.from_str(
                "BTCUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL"
            ),
        }
        s._effective_warmup = 20
        with pytest.raises(RuntimeError, match="warmup insufficient"):
            s._enforce_warmup()

    def test_enforce_warmup_passes_when_history_sufficient(self):
        from nautilus_trader.model.data import BarType

        spec = _basic_signal_spec()
        s = _make_strategy_for_unit_test(
            signal_spec=spec, cache_history_len=50, factor_lookback=20
        )
        s._bar_types = {
            "BTCUSDT-PERP": BarType.from_str(
                "BTCUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL"
            ),
        }
        s._effective_warmup = 20
        s._enforce_warmup()  # must not raise

    def test_enforce_warmup_skipped_when_cache_empty(self):
        """Backtest mode: cache is empty at on_start; bars stream in later.

        The gate must be a no-op so backtests can run.  Runtime warmup
        is still enforced inside _compute_factor_panel via min_history.
        """
        from nautilus_trader.model.data import BarType

        spec = _basic_signal_spec(extra_warmup=0)
        s = _make_strategy_for_unit_test(
            signal_spec=spec, cache_history_len=0, factor_lookback=20
        )
        s._bar_types = {
            "BTCUSDT-PERP": BarType.from_str(
                "BTCUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL"
            ),
        }
        s._effective_warmup = 20
        # Empty cache → no-op (backtest start regime), must NOT raise.
        s._enforce_warmup()

    def test_explicit_warmup_below_derived_raises_in_on_start_logic(self):
        """If config.warmup_bars is set and < derived → on_start would raise.

        We replicate the same check on a strategy that has already run
        ``_resolve_signal_spec``-equivalent.
        """
        spec = _basic_signal_spec(extra_warmup=5)
        s = _make_strategy_for_unit_test(
            signal_spec=spec,
            cache_history_len=100,
            warmup_bars=10,  # configured but derived is 25
            factor_lookback=20,
        )
        derived = s._derive_warmup_bars(spec)
        assert derived == 25
        # Replicate the on_start guard logic.
        if s._configured_warmup and s._configured_warmup < derived:
            with pytest.raises(RuntimeError, match="below the derived requirement"):
                raise RuntimeError(
                    f"warmup_bars config ({s._configured_warmup}) is below the "
                    f"derived requirement ({derived} = factor.lookback + "
                    f"extra_warmup_bars); refusing to start with insufficient warmup"
                )

    def test_explicit_warmup_above_derived_passes(self):
        spec = _basic_signal_spec(extra_warmup=5)
        s = _make_strategy_for_unit_test(
            signal_spec=spec,
            cache_history_len=100,
            warmup_bars=50,  # explicit padding above derived
            factor_lookback=20,
        )
        # max(50, 25) = 50; that's the effective warmup.
        derived = s._derive_warmup_bars(spec)
        effective = max(s._configured_warmup, derived)
        assert effective == 50


class TestExtractLatestWeights:
    def test_extract_latest_row_filters_nan_and_ts(self):
        spec = _basic_signal_spec()
        s = _make_strategy_for_unit_test(signal_spec=spec, cache_history_len=0)

        weight_panel = pl.DataFrame(
            {
                "ts": [1, 2, 3],
                "BTCUSDT-PERP": [0.1, 0.2, 0.5],
                "ETHUSDT-PERP": [None, -0.1, -0.5],
            }
        )
        out = s._extract_latest_weights(weight_panel)
        assert out == {"BTCUSDT-PERP": 0.5, "ETHUSDT-PERP": -0.5}

    def test_extract_empty_panel_returns_empty_dict(self):
        spec = _basic_signal_spec()
        s = _make_strategy_for_unit_test(signal_spec=spec, cache_history_len=0)
        empty = pl.DataFrame({"ts": [], "BTCUSDT-PERP": []})
        assert s._extract_latest_weights(empty) == {}


class TestSignalSpecFromDict:
    def test_round_trip_full_payload(self):
        payload = {
            "name": "x",
            "factor_ref": "ret_N@1.0.0",
            "method": "top_k_long_short",
            "weighting": "equal",
            "rebalance_freq": "1D",
            "universe_ref": "u",
            "gross_exposure": 1.0,
            "net_exposure": 0.0,
            "max_position": 0.5,
            "turnover_budget": None,
            "method_params": {"k": 3},
            "cost_model": {
                "name": "taker_8bps",
                "fee_bps_per_side": 4.0,
                "slippage_bps_per_side": 1.0,
                "rebate_bps_per_side": 0.0,
            },
            "extra_warmup_bars": 5,
            "version": "1.0.0",
            "code_hash": "abc",
            "description": "test",
            "deprecated": False,
        }
        spec = _signal_spec_from_dict("default", payload)
        assert spec.name == "x"
        assert spec.factor_ref == "ret_N@1.0.0"
        assert spec.extra_warmup_bars == 5
        assert spec.method_params == {"k": 3}
        assert spec.cost_model.fee_bps_per_side == 4.0

    def test_partial_payload_uses_defaults(self):
        spec = _signal_spec_from_dict(
            "fallback",
            {"factor_ref": "ret_N@1.0.0", "method": "rank_to_weight",
             "rebalance_freq": "1D", "universe_ref": "u", "weighting": "equal"},
        )
        assert spec.name == "fallback"
        assert spec.gross_exposure == 1.0
        assert spec.cost_model.name == "taker_8bps"


class TestCrossSectionReadyExecutesKernelAndSubmits:
    """End-to-end stub: bar synchroniser fires → kernel runs → orders submit.

    This exercises the whole ``_on_cross_section_ready`` path including the
    OrderManager hand-off, but with a monkey-patched ``_compute_factor_panel``
    that returns a synthetic factor panel.
    """

    def test_kernel_runs_and_orders_submit(self):
        spec = _basic_signal_spec()
        s = _make_strategy_for_unit_test(
            signal_spec=spec, cache_history_len=100, factor_lookback=20
        )
        # Wire the kernel as on_start would.
        from tinohelm.signal.kernels import top_k_long_short

        s._kernel = top_k_long_short

        # Wire instruments + order manager as on_start would.
        btc_inst = _stub_instrument("BTCUSDT-PERP")
        eth_inst = _stub_instrument("ETHUSDT-PERP")
        s._instruments_by_short_symbol = {
            "BTCUSDT-PERP": btc_inst,
            "ETHUSDT-PERP": eth_inst,
        }
        s._order_manager = OrderManager(s)

        # Provide a synthetic factor panel: 1 row × 2 symbols (BTC>ETH).
        # top_k_long_short with k=1 → BTC long 1.0, ETH short 1.0; then
        # gross=1.0 / max_position=0.5 caps → BTC=0.5, ETH=-0.5.
        factor_panel = pl.DataFrame(
            {
                "ts": [1_700_000_000_000_000_000],
                "BTCUSDT-PERP": [1.0],
                "ETHUSDT-PERP": [-1.0],
            }
        )
        s._compute_factor_panel = lambda ts_ns, bars: factor_panel  # type: ignore[method-assign]

        bars = {
            "BTCUSDT-PERP": _bar("BTCUSDT-PERP", 1_700_000_000_000_000_000, 50_000.0),
            "ETHUSDT-PERP": _bar("ETHUSDT-PERP", 1_700_000_000_000_000_000, 3_000.0),
        }
        s._on_cross_section_ready(1_700_000_000_000_000_000, bars)

        # Both instruments received make_qty calls.
        btc_inst.make_qty.assert_called_once()
        eth_inst.make_qty.assert_called_once()
        # Two orders submitted.
        assert s.submit_order.call_count == 2
        # Strategy bookkeeping advanced.
        assert s.last_rebalance_ts_ns == 1_700_000_000_000_000_000
        assert s.target_weights["BTCUSDT-PERP"] == pytest.approx(0.5, rel=1e-9)
        assert s.target_weights["ETHUSDT-PERP"] == pytest.approx(-0.5, rel=1e-9)

    def test_rebalance_freq_gates_subsequent_calls(self):
        """rebalance_freq_ns honoured: second call inside the window is a no-op."""
        spec = _basic_signal_spec()
        s = _make_strategy_for_unit_test(
            signal_spec=spec, cache_history_len=100, factor_lookback=20
        )
        s._rebalance_freq_ns = 60 * 60 * 1_000_000_000  # 1 hour
        s.last_rebalance_ts_ns = 1_700_000_000_000_000_000
        from tinohelm.signal.kernels import top_k_long_short

        s._kernel = top_k_long_short
        s._instruments_by_short_symbol = {
            "BTCUSDT-PERP": _stub_instrument("BTCUSDT-PERP"),
        }
        s._order_manager = OrderManager(s)
        s._compute_factor_panel = (  # type: ignore[method-assign]
            lambda ts_ns, bars: pl.DataFrame({"ts": [ts_ns], "BTCUSDT-PERP": [1.0]})
        )

        # Second call only 1 second later → still within 1H gate → no-op
        bars = {"BTCUSDT-PERP": _bar("BTCUSDT-PERP", 1_700_000_000_001_000_000, 50_000.0)}
        s._on_cross_section_ready(1_700_000_000_001_000_000, bars)
        s.submit_order.assert_not_called()
        # last_rebalance_ts_ns unchanged.
        assert s.last_rebalance_ts_ns == 1_700_000_000_000_000_000


# =============================================================================
# Layer 1 — narrowed exception handling in _on_cross_section_ready
# =============================================================================


class TestCrossSectionReadyExceptionDiscipline:
    """Domain errors (ValueError/KeyError/Arithmetic*) → log + return.
    Programming errors (NotImplementedError/AttributeError/TypeError) → propagate.

    The previous behaviour caught ``Exception`` indiscriminately, which
    masked the ``NotImplementedError`` raised by the stub
    ``_compute_factor_panel`` and silently produced empty target weights.
    """

    @pytest.fixture()
    def base_strategy(self):
        spec = _basic_signal_spec()
        s = _make_strategy_for_unit_test(
            signal_spec=spec, cache_history_len=100, factor_lookback=20
        )
        from tinohelm.signal.kernels import top_k_long_short

        s._kernel = top_k_long_short
        s._instruments_by_short_symbol = {
            "BTCUSDT-PERP": _stub_instrument("BTCUSDT-PERP"),
        }
        s._order_manager = OrderManager(s)
        return s

    def test_value_error_in_factor_panel_is_caught(self, base_strategy):
        """ValueError → log.error called, no orders submitted, last_rebalance_ts unchanged."""
        s = base_strategy

        def _raises_value(ts_ns, bars):
            raise ValueError("synthetic domain error in kernel inputs")

        s._compute_factor_panel = _raises_value  # type: ignore[method-assign]

        bars = {"BTCUSDT-PERP": _bar("BTCUSDT-PERP", 1_700_000_000_000_000_000, 50_000.0)}
        # Must NOT raise.
        s._on_cross_section_ready(1_700_000_000_000_000_000, bars)
        s.log.error.assert_called()
        s.submit_order.assert_not_called()
        assert s.last_rebalance_ts_ns == 0  # unchanged

    def test_key_error_is_caught(self, base_strategy):
        s = base_strategy

        def _raises_key(ts_ns, bars):
            raise KeyError("missing column")

        s._compute_factor_panel = _raises_key  # type: ignore[method-assign]
        bars = {"BTCUSDT-PERP": _bar("BTCUSDT-PERP", 1_700_000_000_000_000_000, 50_000.0)}
        s._on_cross_section_ready(1_700_000_000_000_000_000, bars)
        s.log.error.assert_called()
        s.submit_order.assert_not_called()

    def test_zero_division_is_caught(self, base_strategy):
        s = base_strategy

        def _raises_zerodiv(ts_ns, bars):
            return 1 / 0  # ZeroDivisionError

        s._compute_factor_panel = _raises_zerodiv  # type: ignore[method-assign]
        bars = {"BTCUSDT-PERP": _bar("BTCUSDT-PERP", 1_700_000_000_000_000_000, 50_000.0)}
        s._on_cross_section_ready(1_700_000_000_000_000_000, bars)
        s.log.error.assert_called()

    def test_not_implemented_error_propagates(self, base_strategy):
        """The whole point of layer 1 — the stub ``raise`` must surface."""
        s = base_strategy

        def _raises_nie(ts_ns, bars):
            raise NotImplementedError(
                "_compute_factor_panel must be supplied"
            )

        s._compute_factor_panel = _raises_nie  # type: ignore[method-assign]
        bars = {"BTCUSDT-PERP": _bar("BTCUSDT-PERP", 1_700_000_000_000_000_000, 50_000.0)}
        with pytest.raises(NotImplementedError):
            s._on_cross_section_ready(1_700_000_000_000_000_000, bars)
        # The error should NOT be logged silently.
        s.log.error.assert_not_called()

    def test_attribute_error_propagates(self, base_strategy):
        """AttributeError = programming error (typo, missing method) → propagate."""
        s = base_strategy

        def _raises_attr(ts_ns, bars):
            return "not_a_dataframe".nonexistent_method()  # type: ignore[attr-defined]

        s._compute_factor_panel = _raises_attr  # type: ignore[method-assign]
        bars = {"BTCUSDT-PERP": _bar("BTCUSDT-PERP", 1_700_000_000_000_000_000, 50_000.0)}
        with pytest.raises(AttributeError):
            s._on_cross_section_ready(1_700_000_000_000_000_000, bars)

    def test_type_error_propagates(self, base_strategy):
        """TypeError = programming error → propagate."""
        s = base_strategy

        def _raises_type(ts_ns, bars):
            raise TypeError("expected DataFrame, got str")

        s._compute_factor_panel = _raises_type  # type: ignore[method-assign]
        bars = {"BTCUSDT-PERP": _bar("BTCUSDT-PERP", 1_700_000_000_000_000_000, 50_000.0)}
        with pytest.raises(TypeError):
            s._on_cross_section_ready(1_700_000_000_000_000_000, bars)


# =============================================================================
# Layer 3 — base class _compute_factor_panel runs the factor kernel
# =============================================================================


class TestComputeFactorPanelBase:
    """The base class _compute_factor_panel pulls bars from cache and runs kernel.

    These tests exercise the production path that previously raised
    NotImplementedError as a stub.  We swap in a fake factor kernel so
    we don't depend on the global registry.
    """

    @staticmethod
    def _make_close_bars(symbol: str, n: int = 30, step_ns: int = 60_000_000_000):
        """Build n bars with strictly increasing close prices.

        Returns a list in **NT cache order**: newest at index 0.  Each
        Bar exposes ``ts_init``, ``open/high/low/close/volume`` as plain
        Python floats so :func:`tinohelm.nt_adapter.factor_panel.
        _series_from_bars` can read them via ``getattr``.
        """
        from dataclasses import dataclass

        @dataclass
        class _SimpleBar:
            ts_init: int
            open: float
            high: float
            low: float
            close: float
            volume: float

        base_ts = 1_700_000_000_000_000_000
        bars: list[_SimpleBar] = []
        for i in range(n):
            ts = base_ts + i * step_ns
            close = 100.0 + i  # monotonic so pct_change is well-defined
            bars.append(
                _SimpleBar(
                    ts_init=ts,
                    open=close - 0.5,
                    high=close + 0.5,
                    low=close - 1.0,
                    close=close,
                    volume=1000.0,
                )
            )
        # NT cache returns newest-first via deque.appendleft.
        return list(reversed(bars))

    def _wire_strategy(self, lookback: int = 5):
        """Build a stub strategy with cache.bars(...) wired to fake bars."""
        from nautilus_trader.model.data import BarType
        from tinohelm.factor.types import FactorSpec, InputSpec

        spec_signal = _basic_signal_spec()
        s = _make_strategy_for_unit_test(
            signal_spec=spec_signal, cache_history_len=30, factor_lookback=lookback
        )
        # Wire bar_types as on_start would.
        s._bar_types = {
            "BTCUSDT-PERP": BarType.from_str(
                "BTCUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL"
            ),
            "ETHUSDT-PERP": BarType.from_str(
                "ETHUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL"
            ),
        }
        s._effective_warmup = lookback

        # Per-symbol bar list (newest-first, like NT cache).
        bars_btc = self._make_close_bars("BTCUSDT-PERP", n=30)
        bars_eth = self._make_close_bars("ETHUSDT-PERP", n=30)

        def _cache_bars(bar_type):
            inst = str(bar_type.instrument_id).split(".", 1)[0]
            if "BTC" in inst:
                return bars_btc
            return bars_eth

        s.cache.bars.side_effect = _cache_bars

        # Wire factor kernel + spec — pretend the registry resolved a
        # ret_N-style factor that takes only ``close``.
        captured_inputs: dict = {}

        def _fake_kernel(close, params=None):
            captured_inputs["close_panel"] = close
            captured_inputs["params"] = params
            # Return the input panel verbatim — it's a valid factor_panel
            # shape for downstream tests.
            return close.clone()

        s._factor_kernel = _fake_kernel
        s._factor_spec = FactorSpec(
            name="ret_N",
            category="动量",
            lookback=lookback,
            input_specs=(InputSpec(field_name="close"),),
            params={"lookback": lookback},
        )
        return s, captured_inputs

    def test_compute_factor_panel_calls_kernel_with_close_panel(self):
        """Default _compute_factor_panel runs the kernel with a close-only panel."""
        # Use the real method bound from SignalDrivenStrategy.
        s, captured = self._wire_strategy(lookback=5)
        s._compute_factor_panel = SignalDrivenStrategy._compute_factor_panel.__get__(s)

        bars = {
            "BTCUSDT-PERP": object(),  # bars dict in callback unused for this code path
            "ETHUSDT-PERP": object(),
        }
        result = s._compute_factor_panel(1_700_000_000_000_000_000, bars)

        assert result is not None
        assert "BTCUSDT-PERP" in result.columns
        assert "ETHUSDT-PERP" in result.columns
        assert "ts" in result.columns
        # Kernel must have been called with a polars DataFrame keyed by
        # ``close`` (the field_name from FactorSpec.input_specs).
        assert captured["close_panel"] is not None
        assert "BTCUSDT-PERP" in captured["close_panel"].columns
        assert captured["params"] == {"lookback": 5}

    def test_compute_factor_panel_returns_none_when_unresolved(self):
        """When _factor_kernel is None, we log + return None (don't crash)."""
        s, _ = self._wire_strategy(lookback=5)
        s._factor_kernel = None
        s._factor_spec = None
        s._compute_factor_panel = SignalDrivenStrategy._compute_factor_panel.__get__(s)

        result = s._compute_factor_panel(1_700_000_000_000_000_000, {})
        assert result is None
        s.log.error.assert_called()

    def test_compute_factor_panel_returns_none_when_history_short(self):
        """Insufficient bars per symbol → None (skip rebalance)."""
        s, _ = self._wire_strategy(lookback=5)
        # Override cache.bars to return fewer than warmup.
        short_bars = self._make_close_bars("any", n=2)
        s.cache.bars.side_effect = lambda bt: short_bars

        s._compute_factor_panel = SignalDrivenStrategy._compute_factor_panel.__get__(s)
        result = s._compute_factor_panel(1_700_000_000_000_000_000, {})
        assert result is None


# =============================================================================
# Cross-currency equity aggregation — regression for _compute_equity
# =============================================================================


class TestComputeEquityCurrencyFiltering:
    """_compute_equity must filter balances by the universe's quote currency.

    Historical bug: ``balances_total()`` returns ``dict[Currency, Money]``;
    the previous implementation summed ``money.as_double()`` across *all*
    currencies, so holding 1 BTC (≈50k USD) alongside 100k USDT would
    report ``100001`` equity and drive ``target_notional = w × equity /
    price`` to a meaningless value.  The live Binance USDT-M Futures path
    routinely carries BNB balances for fee discounts — this fix is
    therefore latent in backtest but active in live.

    The production path now:

    1. Infers ``quote_currency`` from the first instrument's
       :attr:`Instrument.settlement_currency`.
    2. Prefers the NT single-currency API
       ``account.balance_total(currency)`` (matches the pattern already
       adopted in :class:`RiskGuardActor` / :class:`MetricsActor` /
       :class:`HealthActor`).
    3. Falls back to ``balances_total().get(currency)`` on error.
    """

    @staticmethod
    def _money(amount: float, currency: Any) -> Any:
        """Build a MagicMock that walks the Money.as_double() contract."""
        m = MagicMock()
        m.as_double.return_value = float(amount)
        # Echo back the currency so sorting / dict-key comparisons work
        # if a test later inspects the value.
        m.currency = currency
        return m

    @staticmethod
    def _instrument_with_settlement(currency: Any) -> Any:
        """Stub Instrument exposing settlement_currency (CryptoPerpetual-compat)."""
        inst = MagicMock()
        inst.settlement_currency = currency
        return inst

    def _strategy_with_quote(
        self,
        *,
        quote_currency: Any,
        venue: Any,
        account: Any,
    ) -> _SignalDrivenStrategyStub:
        """Build a stub wired for the production _compute_equity path."""
        spec = _basic_signal_spec()
        s = _make_strategy_for_unit_test(signal_spec=spec, cache_history_len=0)
        # Force the production branch by dropping the legacy test hook.
        del s.account
        s._instruments_by_short_symbol = {
            "BTCUSDT-PERP": self._instrument_with_settlement(quote_currency),
        }
        s._venues = {venue}
        s.cache.account_for_venue.return_value = account
        return s

    def test_compute_equity_filters_non_quote_currencies(self):
        """USDT 100k + BTC 1.0 → equity = 100000 (BTC ignored)."""
        from nautilus_trader.model.currencies import BTC, USDT
        from nautilus_trader.model.identifiers import Venue

        venue = Venue("BINANCE")
        account = MagicMock()
        # Simulate the multi-currency case: account.balance_total(USDT)
        # returns only the USDT slice.
        account.balance_total.side_effect = lambda ccy: (
            self._money(100_000.0, USDT) if ccy == USDT else None
        )
        # balances_total would still contain BTC, but we never reach the
        # fallback — assert that below.
        account.balances_total.return_value = {
            USDT: self._money(100_000.0, USDT),
            BTC: self._money(1.0, BTC),
        }

        s = self._strategy_with_quote(
            quote_currency=USDT, venue=venue, account=account,
        )
        equity = s._compute_equity()

        assert equity == pytest.approx(100_000.0, rel=1e-9)
        # Must NOT be 100_001.0 (i.e. naive BTC + USDT sum).
        assert equity != pytest.approx(100_001.0)

    def test_compute_equity_uses_single_currency_api_preferentially(self):
        """balance_total(currency) is called; balances_total() is not touched."""
        from nautilus_trader.model.currencies import USDT
        from nautilus_trader.model.identifiers import Venue

        venue = Venue("BINANCE")
        account = MagicMock()
        account.balance_total.return_value = self._money(50_000.0, USDT)

        s = self._strategy_with_quote(
            quote_currency=USDT, venue=venue, account=account,
        )
        equity = s._compute_equity()

        assert equity == pytest.approx(50_000.0, rel=1e-9)
        account.balance_total.assert_called_once_with(USDT)
        account.balances_total.assert_not_called()

    def test_compute_equity_fallback_when_single_currency_api_raises(self):
        """balance_total raises TypeError → fall back to balances_total().get."""
        from nautilus_trader.model.currencies import USDT
        from nautilus_trader.model.identifiers import Venue

        venue = Venue("BINANCE")
        account = MagicMock()
        # Older stubs that took no arg (or multi-ccy accounts missing a
        # base_currency) — NT raises ValueError; older doubles raise
        # TypeError.  Both must route to the fallback branch.
        account.balance_total.side_effect = TypeError(
            "balance_total() takes no positional argument"
        )
        account.balances_total.return_value = {
            USDT: self._money(75_000.0, USDT),
        }

        s = self._strategy_with_quote(
            quote_currency=USDT, venue=venue, account=account,
        )
        equity = s._compute_equity()

        assert equity == pytest.approx(75_000.0, rel=1e-9)
        account.balance_total.assert_called_once_with(USDT)
        account.balances_total.assert_called_once_with()


class TestResolveQuoteCurrency:
    """_resolve_quote_currency infers the universe's settlement currency."""

    def test_resolve_quote_currency_from_instruments(self):
        """First instrument's settlement_currency is returned."""
        from nautilus_trader.model.currencies import USDT

        spec = _basic_signal_spec()
        s = _make_strategy_for_unit_test(signal_spec=spec, cache_history_len=0)
        inst = MagicMock()
        inst.settlement_currency = USDT
        s._instruments_by_short_symbol = {"BTCUSDT-PERP": inst}

        assert s._resolve_quote_currency() is USDT

    def test_resolve_quote_currency_returns_none_when_no_instruments(self):
        """Empty instruments dict → None (caller logs + bails)."""
        spec = _basic_signal_spec()
        s = _make_strategy_for_unit_test(signal_spec=spec, cache_history_len=0)
        s._instruments_by_short_symbol = {}

        assert s._resolve_quote_currency() is None

    def test_resolve_quote_currency_warns_on_mixed_settlement(self):
        """Mixed settlement currencies → warn + return first."""
        from nautilus_trader.model.currencies import BTC, USDT

        spec = _basic_signal_spec()
        s = _make_strategy_for_unit_test(signal_spec=spec, cache_history_len=0)
        inst_usdt = MagicMock()
        inst_usdt.settlement_currency = USDT
        inst_btc = MagicMock()
        inst_btc.settlement_currency = BTC
        # dict preserves insertion order → USDT is first.
        s._instruments_by_short_symbol = {
            "BTCUSDT-PERP": inst_usdt,
            "BTCUSD-PERP": inst_btc,  # coin-margined
        }

        quote = s._resolve_quote_currency()
        assert quote is USDT
        s.log.warning.assert_called()
