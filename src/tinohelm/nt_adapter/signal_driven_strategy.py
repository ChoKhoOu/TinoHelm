"""Generic NT Strategy that executes any :class:`SignalSpec`.

One implementation drives every signal — there is no per-signal Strategy
subclass.  The kernel + cost model + universe are entirely declared by
:class:`tinohelm.signal.types.SignalSpec`; this Strategy only handles the
*execution mechanics* (subscribing to bars, enforcing warmup, running the
kernel on cross-section ready, diffing target weights vs. portfolio
positions, submitting market orders).

NT API discipline
-----------------
This module follows the NT pitfalls listed in ``CLAUDE.md`` §NT API
gotchas:

* ``__init__`` does not touch ``self.clock`` / ``self.log`` — those are
  only safe inside ``on_start`` and onwards.
* ``self.cache.bars(bar_type)`` is the canonical history accessor.
* ``self.subscribe_bars(bar_type)`` must be called for every bar type
  the strategy reads or ``on_bar`` will never fire.
* ``instrument.make_qty(...)`` is the only correct path to a
  :class:`Quantity` — see :class:`OrderManager`.
* Position arithmetic uses ``self.portfolio.net_position(instrument_id)``
  to support both NETTING and HEDGING OMS modes uniformly.

Lifecycle
---------
``__init__``  (NT instantiates)
   Stash config; do not access ``self.cache`` / ``self.log``.

``on_start``
   Resolve :class:`SignalSpec` (from registry or inline JSON), derive
   ``warmup_bars = factor.lookback + extra_warmup_bars``, validate cache
   has enough history, instantiate :class:`BarSynchronizer` and
   :class:`OrderManager`, subscribe to all bar types.

``on_bar``
   Feed the bar into the synchroniser; complete cross-sections trigger
   ``_on_cross_section_ready`` which runs the kernel and submits orders.

``on_save`` / ``on_load``
   Persist ``target_weights`` and ``last_rebalance_ts_ns`` for live
   restart resumption.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, TYPE_CHECKING

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

from tinohelm.factor.types import FactorSpec
from tinohelm.signal.types import SignalSpec
from tinohelm.signal.utils import (
    signal_spec_from_dict,
    validate_supported_signal_execution,
)
from tinohelm.signal.kernels import (
    quantile_long_short,
    rank_to_weight,
    threshold_signed,
    top_k_long_short,
    zscore_clip,
)
from tinohelm.nt_adapter.bar_synchronizer import (
    BarSynchronizer,
    BarSynchronizerConfig,
)
from tinohelm.nt_adapter.factor_panel import (
    compute_latest_factor_panel,
    factor_uses_only_bar_fields,
)
from tinohelm.nt_adapter.order_manager import OrderManager

if TYPE_CHECKING:  # pragma: no cover
    from nautilus_trader.model.data import Bar

logger = logging.getLogger(__name__)


# Slug → kernel callable.  Kept module-level so tests can monkey-patch a
# single seam (mirrors ``signal/worker._KERNEL_DISPATCH``).
_KERNEL_DISPATCH: dict[str, Callable] = {  # type: ignore[type-arg]
    "top_k_long_short": top_k_long_short,
    "quantile_long_short": quantile_long_short,
    "threshold_signed": threshold_signed,
    "zscore_clip": zscore_clip,
    "rank_to_weight": rank_to_weight,
}


# ---------------------------------------------------------------------------
# Config — msgspec Struct (NT NautilusConfig)
# ---------------------------------------------------------------------------


class SignalDrivenStrategyConfig(StrategyConfig, frozen=True):
    """msgspec ``StrategyConfig`` for :class:`SignalDrivenStrategy`.

    Fields
    ------
    signal_name:
        Identifier the strategy looks up in :class:`SignalRegistry` when
        ``signal_spec_json`` is ``None``.
    signal_spec_json:
        Optional pre-serialised :class:`SignalSpec` (flat dict).  Used by
        ``/api/signal/export/{id}`` so live strategies do not have to
        rescan the registry to load a saved signal.  When ``None`` the
        strategy resolves ``signal_name`` against the registry at
        ``on_start`` time.
    instrument_ids:
        Tuple of instrument-id strings (e.g. ``"BTCUSDT-PERP.BINANCE"``).
        msgspec disallows mutable lists in frozen Structs, so we use a
        tuple.
    bar_type_template:
        Bar-type *string template* whose ``{instrument_id}`` placeholder
        is substituted per symbol.  Example:
        ``"{instrument_id}-1-HOUR-LAST-EXTERNAL"``.  We use a template
        rather than a list because the bar-cadence portion is shared
        across the universe.
    warmup_bars:
        Optional override for the derived warmup.  Default 0 means
        "derive at runtime as ``factor.lookback + extra_warmup_bars``".
        A non-zero value below the derived warmup raises a
        ``RuntimeError`` at ``on_start``; above the derived warmup is
        respected (caller wants extra padding).
    rebalance_freq_ns:
        Minimum nanoseconds between rebalances.  Default 0 = rebalance
        on every cross-section ready.  Live deployments override via
        ``rebalance_freq`` parsing in ``/api/signal/export``.
    factor_lookback:
        Optional explicit factor lookback for warmup derivation.  When
        ``None`` (default) the strategy looks up the factor's ``__factor_spec__``
        via :class:`tinohelm.factor.registry.Registry`.  The export
        endpoint sets this explicitly so live strategies do not need to
        load the factor registry on startup.
    """

    signal_name: str
    instrument_ids: tuple[str, ...]
    bar_type_template: str
    signal_spec_json: dict | None = None
    warmup_bars: int = 0
    rebalance_freq_ns: int = 0
    factor_lookback: int | None = None


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


class SignalDrivenStrategy(Strategy):
    """Generic NT Strategy executing a :class:`SignalSpec` cross-sectionally.

    See module docstring for design philosophy.  The strategy is fully
    declarative — every behaviour-relevant decision lives in the
    :class:`SignalSpec` carried by the config.
    """

    def __init__(self, config: SignalDrivenStrategyConfig) -> None:
        super().__init__(config)
        # Stash config locally — do not call self.log / self.clock here.
        # CLAUDE.md NT pitfall: __init__ runs before NT initialises those.
        self._signal_name: str = config.signal_name
        self._instrument_id_strs: tuple[str, ...] = tuple(config.instrument_ids)
        self._bar_type_template: str = config.bar_type_template
        self._configured_warmup: int = int(config.warmup_bars)
        self._rebalance_freq_ns: int = int(config.rebalance_freq_ns)
        self._configured_factor_lookback: int | None = config.factor_lookback
        self._signal_spec_json: dict | None = config.signal_spec_json

        # Resolved at on_start.
        self.signal_spec: SignalSpec | None = None
        self._kernel: Callable | None = None  # type: ignore[type-arg]
        # Factor side: resolved at on_start so _compute_factor_panel can run
        # the kernel against the live NT cache.  Tests / subclasses can leave
        # these unset by overriding _compute_factor_panel before on_start.
        self._factor_kernel: Callable | None = None  # type: ignore[type-arg]
        self._factor_spec: FactorSpec | None = None
        self._derived_warmup: int = 0
        self._effective_warmup: int = 0

        # Runtime state — also persisted by on_save/on_load.
        self.target_weights: dict[str, float] = {}
        self.last_rebalance_ts_ns: int = 0

        # Wired in on_start.
        self._bar_synchronizer: BarSynchronizer | None = None
        self._order_manager: OrderManager | None = None

        # Cached InstrumentId / BarType objects (avoid re-parsing per bar).
        self._bar_types: dict[str, BarType] = {}  # symbol_short → BarType
        self._instruments_by_short_symbol: dict[str, Any] = {}
        # Distinct venues across the universe — used by _submit_diff to
        # resolve the account via cache.account_for_venue (NT does not
        # expose ``self.account`` on Strategy directly).
        self._venues: set[Any] = set()

    # ------------------------------------------------------------------
    # NT lifecycle
    # ------------------------------------------------------------------

    def on_start(self) -> None:
        """Resolve spec, derive warmup, validate cache, subscribe bars."""
        # 1. Load SignalSpec.
        self.signal_spec = self._resolve_signal_spec()
        validate_supported_signal_execution(self.signal_spec)

        # 2. Resolve kernel callable from method slug.
        method = self.signal_spec.method
        try:
            self._kernel = _KERNEL_DISPATCH[method]
        except KeyError:
            raise ValueError(
                f"Unknown signal kernel method {method!r}; expected one of "
                f"{sorted(_KERNEL_DISPATCH)}"
            )

        # 3. Build BarType / InstrumentId cache.
        for inst_str in self._instrument_id_strs:
            inst_id = InstrumentId.from_str(inst_str)
            bar_type = BarType.from_str(
                self._bar_type_template.format(instrument_id=inst_str)
            )
            symbol_short = self._symbol_short(inst_id)
            self._bar_types[symbol_short] = bar_type
            inst = self.cache.instrument(inst_id)
            if inst is None:
                self.log.warning(
                    f"Instrument not found in cache at start: {inst_id} — "
                    f"weight diffs for this symbol will be skipped until it loads"
                )
            self._instruments_by_short_symbol[symbol_short] = inst
            # Stash the venue so _submit_diff can pull the account.
            self._venues.add(inst_id.venue)

        # 4. Derive warmup_bars and enforce minimum history.
        derived_warmup = self._derive_warmup_bars(self.signal_spec)
        self._derived_warmup = derived_warmup
        self._effective_warmup = max(self._configured_warmup, derived_warmup)
        if (
            self._configured_warmup
            and self._configured_warmup < derived_warmup
        ):
            raise RuntimeError(
                f"warmup_bars config ({self._configured_warmup}) is below the "
                f"derived requirement ({derived_warmup} = factor.lookback + "
                f"extra_warmup_bars); refusing to start with insufficient warmup"
            )
        self._enforce_warmup()

        # 4b. Resolve the factor kernel + spec from the registry so
        # _compute_factor_panel can produce a live panel from cache.bars.
        # Subclasses or tests that override _compute_factor_panel are not
        # affected; we still attempt resolution and log on failure rather
        # than raising — that way a custom subclass with no registry entry
        # still works.
        self._resolve_factor_kernel(self.signal_spec)

        # 5. Build BarSynchronizer + OrderManager.
        symbols_short = tuple(self._bar_types.keys())
        self._bar_synchronizer = BarSynchronizer(
            BarSynchronizerConfig(
                expected_symbols=symbols_short,
                max_wait_bars=5,
            ),
            on_complete=self._on_cross_section_ready,
        )
        self._order_manager = OrderManager(self)

        # 6. Subscribe to all bar types.
        for bar_type in self._bar_types.values():
            self.subscribe_bars(bar_type)

        self.log.info(
            f"SignalDrivenStrategy[{self._signal_name}] started: "
            f"symbols={list(symbols_short)}, warmup={self._effective_warmup}, "
            f"method={self.signal_spec.method}"
        )

    def on_bar(self, bar: "Bar") -> None:  # pragma: no cover — NT-driven; covered via stub
        if self._bar_synchronizer is None:
            return
        self._bar_synchronizer.on_bar(bar)

    def on_save(self) -> dict[str, Any]:
        """Serialise runtime state for live restart resumption."""
        return {
            "target_weights": dict(self.target_weights),
            "last_rebalance_ts_ns": int(self.last_rebalance_ts_ns),
        }

    def on_load(self, state: dict[str, Any]) -> None:
        """Restore runtime state after a live restart."""
        raw_weights = state.get("target_weights") or {}
        self.target_weights = {str(k): float(v) for k, v in raw_weights.items()}
        self.last_rebalance_ts_ns = int(state.get("last_rebalance_ts_ns", 0))

    def on_order_rejected(self, event: object) -> None:  # pragma: no cover — production-only
        """Log venue rejections without stopping the strategy."""
        self.log.warning(
            f"SignalDrivenStrategy[{self._signal_name}] order rejected: "
            f"{getattr(event, 'reason', 'unknown')}"
        )

    # ------------------------------------------------------------------
    # Cross-section handler
    # ------------------------------------------------------------------

    def _on_cross_section_ready(
        self, ts_ns: int, bars: dict[str, "Bar"]
    ) -> None:
        """Invoked once all expected symbols have reported a bar at ``ts_ns``.

        Order of operations:

        1. Honour ``rebalance_freq_ns`` gating.
        2. Compute factor panel from the cross-section.
        3. Apply the kernel to produce ``weight_panel``.
        4. Extract latest row → ``target_weights``.
        5. Diff against portfolio positions → submit orders.

        Exception handling discipline (defense-in-depth, layer 1)
        ---------------------------------------------------------
        We catch only the narrow set of *domain* errors that can legitimately
        happen during data-driven computation (rolling window math, missing
        symbols, division by zero, numeric overflow).  Programming errors
        such as :class:`NotImplementedError`, :class:`AttributeError`,
        :class:`TypeError` are intentionally *not* caught — they propagate
        upstream to NT so the strategy fails fast instead of silently
        running with empty target_weights forever (which is what happened
        with the stub ``_compute_factor_panel`` before this fix).
        """
        if self.signal_spec is None or self._kernel is None:
            return

        # Step 1 — rebalance frequency gate.
        if self._rebalance_freq_ns > 0:
            elapsed = ts_ns - self.last_rebalance_ts_ns
            if elapsed < self._rebalance_freq_ns:
                return

        try:
            # Step 2 — build factor panel.  Hook for subclasses / tests.
            factor_panel = self._compute_factor_panel(ts_ns, bars)
            if factor_panel is None:
                return

            # Step 3 — apply kernel.
            constraints = {
                "gross_exposure": float(self.signal_spec.gross_exposure),
                "net_exposure": float(self.signal_spec.net_exposure),
                "max_position": float(self.signal_spec.max_position),
            }
            weight_panel = self._kernel(
                factor_panel,
                params=dict(self.signal_spec.method_params),
                constraints=constraints,
            )

            # Step 4 — extract latest row as target_weights.
            new_weights = self._extract_latest_weights(weight_panel)

            # Step 4b — stale-position guard: any symbol that appeared in
            # the previous rebalance but is absent from *new_weights* (due
            # to kernel NaN output, PIT universe contraction, or a data
            # hole) must receive an explicit target=0.0 so that
            # OrderManager.execute_diff sees it and issues a close order.
            # Without this, _extract_latest_weights silently skips NaN
            # columns and the old position is never flattened.
            merged_weights: dict[str, float] = {
                symbol: 0.0
                for symbol in self.target_weights
                if symbol not in new_weights
            }
            merged_weights.update(new_weights)

            # Step 5 — submit diffs.
            self._submit_diff(merged_weights, bars)

            # Bookkeeping: persist merged_weights so that any 0.0-padded
            # symbol (whose close order may not have filled yet) remains
            # visible to the next rebalance and keeps receiving a
            # target=0.0 until the position is actually flat.
            # OrderManager.execute_diff is diff-idempotent for already-flat
            # positions (target=0, current=0 → diff=0 → no order).
            self.target_weights = merged_weights
            self.last_rebalance_ts_ns = ts_ns
        except (
            ValueError,
            KeyError,
            ArithmeticError,
            OverflowError,
            ZeroDivisionError,
        ) as exc:
            # Domain-level data errors only — log and skip this cross-section.
            # NotImplementedError / AttributeError / TypeError are programming
            # errors that should fail loudly, so we don't catch them here.
            self.log.error(
                f"SignalDrivenStrategy[{self._signal_name}] cross-section "
                f"computation failed at ts_ns={ts_ns}: "
                f"{type(exc).__name__}: {exc}"
            )

    # ------------------------------------------------------------------
    # Hooks for subclasses / tests
    # ------------------------------------------------------------------

    def _compute_factor_panel(
        self, ts_ns: int, bars: dict[str, "Bar"]
    ) -> Any:
        """Compute a factor panel from the cross-section bar dict.

        Default implementation: pull the most-recent
        ``effective_warmup`` bars per symbol from ``self.cache``, build
        the wide OHLCV panels the kernel needs (one per
        :class:`tinohelm.factor.types.InputSpec.field_name`), and run
        the kernel.  See :func:`tinohelm.nt_adapter.factor_panel.
        compute_latest_factor_panel` for the full panel-construction
        contract.

        Returns
        -------
        :data:`tinohelm.factor.types.Panel` | None
            The factor output panel.  ``None`` when:

            * the kernel was not resolved at start-time (we log once and
              return so the strategy effectively becomes a no-op rather
              than crashing every cross-section); or
            * any symbol has fewer than ``effective_warmup`` bars (the
              warmup gate at ``on_start`` checked the *current* cache,
              but live deployments may temporarily lose history during
              re-subscriptions).

        Subclasses may still override (e.g. signal-specific factors that
        need cross-asset PIT data).  Tests monkey-patch this method on
        the instance directly.
        """
        if self._factor_kernel is None or self._factor_spec is None:
            self.log.error(
                f"SignalDrivenStrategy[{self._signal_name}]: factor kernel "
                "unresolved at on_start — returning empty cross-section.  "
                "Either ensure the factor is registered in the factor "
                "registry, or override _compute_factor_panel in a subclass."
            )
            return None

        # Build the {symbol_short: list[Bar]} map from the cache.
        bars_by_symbol: dict[str, list] = {}
        for symbol_short, bar_type in self._bar_types.items():
            cached = self.cache.bars(bar_type)
            # Slice to the warmup window — the kernel only needs that much
            # history for its rolling computations.  Cache returns newest-
            # first; build_wide_panel reverses to chronological order.
            bars_by_symbol[symbol_short] = list(cached[: self._effective_warmup])

        return compute_latest_factor_panel(
            factor_kernel=self._factor_kernel,
            factor_spec=self._factor_spec,
            bars_by_symbol=bars_by_symbol,
            min_history=self._effective_warmup,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_signal_spec(self) -> SignalSpec:
        """Return the :class:`SignalSpec` driving this strategy."""
        if self._signal_spec_json:
            return signal_spec_from_dict(self._signal_name, self._signal_spec_json)
        # Lazy registry import — keeps the unit test path light.
        from tinohelm.signal.registry import SignalRegistry

        registry = SignalRegistry()
        registry.scan()
        spec = registry.get_spec(self._signal_name)
        if spec is None:
            raise RuntimeError(
                f"SignalSpec {self._signal_name!r} not found in registry; "
                "scan paths.get('signals_dir') or pass signal_spec_json"
            )
        return spec

    def _resolve_factor_kernel(self, signal_spec: SignalSpec) -> None:
        """Look up the upstream factor kernel + spec from the registry.

        Sets ``self._factor_kernel`` and ``self._factor_spec`` so
        :meth:`_compute_factor_panel` can run the kernel against live
        cache data.  Called from :meth:`on_start`.

        On failure (factor not in the registry, or factor uses non-OHLCV
        inputs we can't materialise from ``cache.bars``), logs at warning
        level and leaves both attributes ``None`` — the strategy will
        then return ``None`` from every cross-section, which is the
        observable failure mode previously hidden by the silently-caught
        ``NotImplementedError``.  The export endpoint (s18) is responsible
        for rejecting unrunnable signals before they reach this point.
        """
        factor_name = signal_spec.factor_ref.split("@", 1)[0]
        try:
            from tinohelm.factor.registry import Registry as FactorRegistry

            factor_registry = FactorRegistry()
            factor_registry.scan()
            factor_spec = factor_registry.get_spec(factor_name)
            if factor_spec is None:
                self.log.warning(
                    f"SignalDrivenStrategy[{self._signal_name}]: factor "
                    f"{factor_name!r} not registered — _compute_factor_panel "
                    "will return None until a subclass overrides it."
                )
                return
            if not factor_uses_only_bar_fields(factor_spec):
                self.log.warning(
                    f"SignalDrivenStrategy[{self._signal_name}]: factor "
                    f"{factor_name!r} requires non-OHLCV inputs "
                    f"({[s.field_name for s in factor_spec.input_specs]}); "
                    "the live path can only resolve bar fields.  "
                    "_compute_factor_panel will return None until a "
                    "subclass overrides it."
                )
                return
            kernel = factor_registry.get_kernel(factor_name)
            self._factor_spec = factor_spec
            self._factor_kernel = kernel
            self.log.info(
                f"SignalDrivenStrategy[{self._signal_name}]: resolved factor "
                f"kernel {factor_name!r} (lookback={factor_spec.lookback}, "
                f"inputs={[s.field_name for s in factor_spec.input_specs]})"
            )
        except KeyError as exc:
            # Registry returned None for spec but raised for kernel — defensive.
            self.log.warning(
                f"SignalDrivenStrategy[{self._signal_name}]: factor kernel "
                f"lookup failed for {factor_name!r}: {exc}"
            )

    def _derive_warmup_bars(self, spec: SignalSpec) -> int:
        """Compute ``factor.lookback + extra_warmup_bars``."""
        extra = int(spec.extra_warmup_bars)
        if self._configured_factor_lookback is not None:
            return int(self._configured_factor_lookback) + extra
        # Fall back to the factor registry.  factor_ref may be plain
        # ``"name"`` or ``"name@version"``.
        factor_name = spec.factor_ref.split("@", 1)[0]
        from tinohelm.factor.registry import Registry

        registry = Registry()
        registry.scan()
        factor_spec = registry.get_spec(factor_name)
        if factor_spec is None:
            raise RuntimeError(
                f"Factor {factor_name!r} (referenced by signal "
                f"{spec.name!r}) not found in factor registry; "
                "set SignalDrivenStrategyConfig.factor_lookback explicitly "
                "or ensure factors_dir is populated"
            )
        return int(factor_spec.lookback) + extra

    def _enforce_warmup(self) -> None:
        """Raise ``RuntimeError`` if any bar type has *partial* warmup history.

        Three regimes handled:

        * Live / sandbox: the platform pre-loaded historical bars before
          ``on_start``.  Cache contains ``>= effective_warmup`` bars per
          bar_type → no-op.
        * Live with insufficient backfill: cache is non-empty but has
          fewer than ``effective_warmup`` bars → raise.  This catches
          misconfigured pre-load policies that would otherwise silently
          run the strategy on a too-short window.
        * Backtest: cache is empty at on_start (bars stream in via
          ``on_bar`` after start).  Skip the gate; ``_compute_factor_panel``
          will defer cross-section computation until each bar type
          accumulates enough history at runtime.
        """
        for symbol_short, bar_type in self._bar_types.items():
            history = self.cache.bars(bar_type)
            history_len = len(history)
            if history_len == 0:
                # Backtest mode (no pre-loaded history) — cross-section
                # gating happens at _compute_factor_panel time.
                continue
            if history_len < self._effective_warmup:
                raise RuntimeError(
                    f"warmup insufficient: cache.bars({bar_type})={history_len} "
                    f"< required={self._effective_warmup} (signal={self._signal_name}, "
                    f"symbol={symbol_short})"
                )

    def _extract_latest_weights(self, weight_panel: Any) -> dict[str, float]:
        """Pull the last row out of a polars weight panel into a dict."""
        if weight_panel is None or weight_panel.height == 0:
            return {}
        latest = weight_panel.tail(1)
        out: dict[str, float] = {}
        for col in latest.columns:
            if col == "ts":
                continue
            value = latest[col][0]
            if value is None:
                continue
            try:
                fvalue = float(value)
            except (TypeError, ValueError):
                continue
            if fvalue != fvalue:  # NaN check (avoids importing math)
                continue
            out[col] = fvalue
        return out

    def _submit_diff(
        self, target_weights: dict[str, float], bars: dict[str, "Bar"]
    ) -> None:
        """Submit market orders implementing target - current weight diff.

        Equity is read from the venue account via
        ``self.cache.account_for_venue(venue)`` — NT :class:`Strategy`
        does not expose ``self.account`` directly.  When the universe
        spans multiple venues we sum each venue's ``balance_total(quote_currency)``
        to get a single notional equity figure, filtering out
        non-quote balances (see :meth:`_compute_equity`).
        """
        if self._order_manager is None:
            return
        equity = self._compute_equity()
        if equity is None or equity <= 0:
            return
        prices: dict[str, float] = {}
        for symbol_short, bar in bars.items():
            try:
                close = bar.close
                # NT Bar.close is a Price value object with as_double();
                # stub bars may pass through plain floats.
                as_double = getattr(close, "as_double", None)
                prices[symbol_short] = (
                    float(as_double()) if callable(as_double) else float(close)
                )
            except (AttributeError, TypeError, ValueError):
                continue
        self._order_manager.execute_diff(
            target_weights=target_weights,
            instruments=self._instruments_by_short_symbol,
            equity=equity,
            prices=prices,
        )

    def _resolve_quote_currency(self) -> Any:
        """Return the settlement :class:`Currency` shared by the universe.

        Cross-venue / cross-currency universes are out of scope for the
        equity aggregation contract — mixing USDT + USDC + coin-margined
        BTC into a single float would produce a meaningless number.  We
        therefore pick the first instrument's ``settlement_currency`` and
        log a warning if any sibling disagrees (the user misconfigured
        the universe).

        Returns
        -------
        Currency | None
            The settlement currency (e.g. ``USDT`` for Binance USDT-M
            Futures), or ``None`` when the instrument cache was empty at
            ``on_start`` and we cannot infer one.
        """
        quote_currency: Any = None
        for inst in self._instruments_by_short_symbol.values():
            if inst is None:
                # Instrument not yet loaded from cache — skip.
                continue
            candidate = getattr(inst, "settlement_currency", None)
            if candidate is None:
                continue
            if quote_currency is None:
                quote_currency = candidate
                continue
            if candidate != quote_currency:
                self.log.warning(
                    f"SignalDrivenStrategy[{self._signal_name}]: universe "
                    f"spans mixed settlement currencies "
                    f"({quote_currency} vs {candidate}); equity aggregation "
                    "will only count the first currency."
                )
                break
        return quote_currency

    def _compute_equity(self) -> float | None:
        """Return total notional equity across all venues, in the universe's quote currency.

        Only balances denominated in the inferred ``quote_currency`` are
        summed — mixing USDT + BNB + coin-margined BTC ``.as_double()``
        values would produce a cross-currency float with no meaning and
        break the downstream ``target_notional = w × equity / price``
        calculation in :class:`OrderManager`.

        Priority:

        1. Legacy test hook — ``self.account`` MagicMock (stubs bypass NT
           entirely).  Kept first so existing fixtures still pass.
        2. NT single-currency API — ``account.balance_total(currency)``
           is the canonical, currency-aware accessor that matches the
           pattern used by ``RiskGuardActor`` / ``MetricsActor`` /
           ``HealthActor``.
        3. Fallback to ``balances_total().get(currency)`` when the
           single-currency call raises (older Account stubs, dict-only
           test doubles).
        """
        # Test hook: stubs pre-populate ``self.account`` (NT real
        # Strategy doesn't expose this attribute; tests bypass NT).
        legacy_account = getattr(self, "account", None)
        if legacy_account is not None:
            balance = legacy_account.balance_total()
            if balance is None:
                return None
            try:
                return float(balance.as_double())
            except (AttributeError, TypeError, ValueError):
                return None

        # Production NT path: aggregate per-venue accounts, filtering to
        # the universe's settlement currency so cross-currency balances
        # (BNB fee-discount, BTC coin-margin) do not pollute the sum.
        if not self._venues:
            return None
        quote_currency = self._resolve_quote_currency()
        if quote_currency is None:
            self.log.warning(
                f"SignalDrivenStrategy[{self._signal_name}]: cannot infer "
                "quote currency from instruments cache — equity unavailable."
            )
            return None

        total: float = 0.0
        any_account = False
        for venue in self._venues:
            account = self.cache.account_for_venue(venue)
            if account is None:
                continue
            any_account = True
            money: Any = None
            try:
                money = account.balance_total(quote_currency)
            except (AttributeError, TypeError, ValueError):
                # Older stubs / multi-currency accounts without a
                # base_currency set may raise — fall back to dict lookup.
                try:
                    balances = account.balances_total()
                except (AttributeError, TypeError):
                    continue
                if balances:
                    money = balances.get(quote_currency)
            if money is None:
                continue
            total += float(money.as_double())
        return total if any_account else None

    @staticmethod
    def _symbol_short(instrument_id: InstrumentId) -> str:
        """Strip the venue suffix from an instrument id (``BTCUSDT-PERP.BINANCE`` → ``BTCUSDT-PERP``)."""
        return str(instrument_id).split(".", 1)[0]


# _signal_spec_from_dict has been extracted to tinohelm.signal.utils.signal_spec_from_dict.
