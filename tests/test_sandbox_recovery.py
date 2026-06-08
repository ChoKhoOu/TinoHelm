"""Behavioral tests for tinohelm.sandbox_recovery.

These cover the SANDBOX restart-recovery glue: a sandbox pod whose cache is
persisted (``[sandbox] persist=true``) reloads its Order/Position/Account
history via NT's own ``load_cache``, but NT's build-time
``initialize_account()`` unconditionally overwrites the just-loaded balances
back to ``starting_balances`` (engine.pyx:3775). We fill that gap by replaying
the LAST real all-currency balances over the fresh sim account
(``generate_account_state``) and re-hydrating open orders into the sim's
matching engines (copying NT's BacktestEngine iteration-0 template,
engine.pyx:1539-1554).

The pure functions here carry no NT runtime dependency, so we test them
directly with real NT value objects (``AccountBalance`` / ``Money`` /
``Currency``) — there is no TradingNode to stand up. The two wiring helpers
(``recover_on_start`` / ``snapshot_on_stop``) operate on NT runtime objects, so
we exercise them with spies (the same ``__new__`` / spy-attr pattern as
``test_bridge_actor.py``), never a real node.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from nautilus_trader.model.objects import AccountBalance, Currency, Money

from tinohelm.sandbox_recovery import (
    DEFAULT_BALANCE_KEY_PREFIX,
    balance_key,
    build_account_balances,
    deserialize_balances,
    open_orders_to_rehydrate,
    recover_on_start,
    serialize_balances,
    snapshot_account_totals,
    snapshot_on_stop,
)

USDT = Currency.from_str("USDT")
BTC = Currency.from_str("BTC")


# ─── #1 build_account_balances (core correctness — written first) ────────────


def test_build_account_balances_single_currency_locked_zero_free_eq_total() -> None:
    """Single-currency target → one AccountBalance with locked=0, free==total.

    This is the heart of the all-currency restore: the replayed AccountState
    must carry locked=0 / free=total so the sim re-computes margin/locked from
    the re-hydrated open orders + positions (margin.pyx:715), instead of us
    second-guessing the locked split.
    """

    balances = build_account_balances({USDT: Money(12_345.67, USDT)})

    assert len(balances) == 1
    b = balances[0]
    assert b.currency == USDT
    assert b.total == Money(12_345.67, USDT)
    assert b.free == Money(12_345.67, USDT)
    assert b.locked == Money(0, USDT)


def test_build_account_balances_multi_currency_one_per_ccy() -> None:
    """Multi-currency target → one AccountBalance per currency.

    NT's AccountState carries a ``list[AccountBalance]`` (base.pyx upserts each
    currency), which is exactly how a MULTI-currency sandbox account holds e.g.
    USDT margin + a BTC position proceeds side by side.
    """

    balances = build_account_balances(
        {USDT: Money(10_000, USDT), BTC: Money(0.5, BTC)},
    )

    by_ccy = {b.currency: b for b in balances}
    assert set(by_ccy) == {USDT, BTC}
    assert by_ccy[USDT].total == Money(10_000, USDT)
    assert by_ccy[BTC].total == Money(0.5, BTC)


def test_build_account_balances_satisfies_invariant_no_valueerror() -> None:
    """Every produced AccountBalance must satisfy total - locked == free.

    NT's model/objects.pyx:1924 raises ValueError if the invariant breaks, so
    the act of constructing the balances at all is the assertion: if
    build_account_balances ever emitted an inconsistent triple, this call would
    throw. We also assert the relation explicitly for clarity.
    """

    balances = build_account_balances(
        {USDT: Money(10_000, USDT), BTC: Money(0.5, BTC)},
    )

    for b in balances:
        assert b.total - b.locked == b.free


def test_build_account_balances_empty_target_empty_list() -> None:
    """Empty target (first boot / no snapshot) → empty list.

    An empty AccountState balances list is a no-op upsert in NT (base.pyx
    update_balances), and the caller skips the generate_account_state call
    entirely so the fresh starting_balances stay in place.
    """

    assert build_account_balances({}) == []


# ─── #2 serialize / deserialize round-trip ───────────────────────────────────


def test_serialize_deserialize_round_trip_multi_currency() -> None:
    """Multi-currency totals → JSON-safe dict → back, with no precision loss.

    The JSON value is the Money ``<amount> <CCY>`` string form so
    ``Money.from_str`` restores it exactly; the key is the currency code.
    """

    totals = {USDT: Money(12_345.67, USDT), BTC: Money(0.5, BTC)}

    raw = serialize_balances(totals)
    # JSON-serializable: values are plain strings keyed by currency code.
    assert json.loads(json.dumps(raw)) == raw

    restored = deserialize_balances(raw)
    assert restored == totals


def test_deserialize_none_or_garbage_degrades_to_empty() -> None:
    """Bad / missing snapshot → {} (degrade = treat as no snapshot).

    A corrupt or absent private Redis key must NEVER crash the pod; recovery
    falls back to fresh starting_balances (skip the balance replay).
    """

    assert deserialize_balances(None) == {}
    assert deserialize_balances({}) == {}
    assert deserialize_balances({"USDT": "not-a-money"}) == {}
    assert deserialize_balances({"USDT": 12345}) == {}  # type: ignore[dict-item]


def test_serialize_round_trips_through_json_string_form() -> None:
    """The serialized dict survives a full JSON string encode/decode trip.

    This mirrors the real Redis path: serialize → json.dumps → SET, then
    GET → json.loads → deserialize.
    """

    totals = {USDT: Money(999.5, USDT)}
    wire = json.dumps(serialize_balances(totals))
    restored = deserialize_balances(json.loads(wire))
    assert restored == totals


# ─── #3 snapshot_account_totals (spy account) ────────────────────────────────


@dataclass
class _AccountSpy:
    """Stand-in for an NT Account — only ``balances()`` is read.

    NT's ``Account.balances()`` returns ``dict[Currency, AccountBalance]``; the
    snapshot only needs each currency's ``.total``.
    """

    _balances: dict[Currency, AccountBalance]

    def balances(self) -> dict[Currency, AccountBalance]:
        return self._balances


def test_snapshot_account_totals_extracts_total_per_currency() -> None:
    """snapshot_account_totals reads each currency's .total off the account."""

    account = _AccountSpy(
        {
            USDT: AccountBalance(
                total=Money(8_000, USDT),
                locked=Money(1_000, USDT),
                free=Money(7_000, USDT),
            ),
            BTC: AccountBalance(
                total=Money(0.25, BTC),
                locked=Money(0, BTC),
                free=Money(0.25, BTC),
            ),
        },
    )

    totals = snapshot_account_totals(account)

    assert totals == {USDT: Money(8_000, USDT), BTC: Money(0.25, BTC)}


def test_snapshot_account_totals_handles_none_account() -> None:
    """A None account (cache miss) → {} (nothing to snapshot)."""

    assert snapshot_account_totals(None) == {}


# ─── #4 open_orders_to_rehydrate (filter emulated) ───────────────────────────


@dataclass
class _OrderSpy:
    is_emulated: bool = False


def test_open_orders_to_rehydrate_skips_emulated() -> None:
    """Emulated orders are skipped (NT loads them into the emulator already).

    Mirrors NT's BacktestEngine iteration-0 template (engine.pyx:1543): only
    NON-emulated open orders get re-processed into the matching engine.
    """

    regular_a = _OrderSpy(is_emulated=False)
    emulated = _OrderSpy(is_emulated=True)
    regular_b = _OrderSpy(is_emulated=False)

    kept = open_orders_to_rehydrate([regular_a, emulated, regular_b])

    assert kept == [regular_a, regular_b]


def test_open_orders_to_rehydrate_empty() -> None:
    assert open_orders_to_rehydrate([]) == []


# ─── #6 wiring: recover_on_start / snapshot_on_stop (spies) ──────────────────


@dataclass
class _LogSpy:
    infos: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def info(self, msg: str) -> None:
        self.infos.append(msg)

    def warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def error(self, msg: str) -> None:
        self.errors.append(msg)


@dataclass
class _MatchingEngineSpy:
    processed: list[tuple[Any, Any]] = field(default_factory=list)

    def process_order(self, order: Any, account_id: Any) -> None:
        self.processed.append((order, account_id))


@dataclass
class _RehydrateOrderSpy:
    instrument_id: str
    account_id: str
    is_emulated: bool = False


@dataclass
class _ExchangeSpy:
    """Stand-in for NT SimulatedExchange — id + per-instrument matching engine."""

    id: str
    _engines: dict[str, _MatchingEngineSpy] = field(default_factory=dict)

    def get_matching_engine(self, instrument_id: str) -> _MatchingEngineSpy | None:
        return self._engines.get(instrument_id)


@dataclass
class _ClientSpy:
    """Stand-in for SandboxExecutionClient — records generate_account_state."""

    exchange: _ExchangeSpy
    account_state_calls: list[dict[str, Any]] = field(default_factory=list)

    def generate_account_state(
        self,
        *,
        balances: list[AccountBalance],
        margins: list[Any],
        reported: bool,
        ts_event: int,
    ) -> None:
        self.account_state_calls.append(
            {
                "balances": balances,
                "margins": margins,
                "reported": reported,
                "ts_event": ts_event,
            },
        )


@dataclass
class _ExecEngineSpy:
    _clients: dict[str, Any]

    @property
    def registered_clients(self) -> dict[str, Any]:
        return self._clients


@dataclass
class _CacheSpy:
    _open_by_venue: dict[str, list[Any]] = field(default_factory=dict)
    _accounts_by_venue: dict[str, Any] = field(default_factory=dict)

    def orders_open(self, *, venue: Any) -> list[Any]:
        return self._open_by_venue.get(venue, [])

    def account_for_venue(self, venue: Any) -> Any:
        return self._accounts_by_venue.get(venue)


@dataclass
class _TraderSpy:
    """Stand-in for NT Trader — exposes _exec_engine + cache + clock."""

    _exec_engine: Any
    cache: _CacheSpy
    log: _LogSpy


@dataclass
class _ClockSpy:
    _ns: int = 1_700_000_000_000_000_000

    def timestamp_ns(self) -> int:
        return self._ns


@dataclass
class _RedisSpy:
    store: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> bytes | None:
        val = self.store.get(key)
        return val.encode("utf-8") if val is not None else None

    def set(self, key: str, value: str) -> None:
        self.store[key] = value


def _trader_with_one_sandbox_client(
    *,
    open_orders: list[Any] | None = None,
    log: _LogSpy | None = None,
) -> tuple[_TraderSpy, _ClientSpy, _ExchangeSpy, _LogSpy]:
    me = _MatchingEngineSpy()
    exchange = _ExchangeSpy(id="BINANCE", _engines={"BTCUSDT-PERP.BINANCE": me})
    client = _ClientSpy(exchange=exchange)
    exec_engine = _ExecEngineSpy(_clients={"BINANCE": client})
    cache = _CacheSpy(_open_by_venue={"BINANCE": open_orders or []})
    log = log or _LogSpy()
    trader = _TraderSpy(_exec_engine=exec_engine, cache=cache, log=log)
    return trader, client, exchange, log


def test_recover_on_start_replays_balances_and_rehydrates_orders() -> None:
    """With a saved snapshot + open orders → replay AccountState + process each order."""

    order = _RehydrateOrderSpy(
        instrument_id="BTCUSDT-PERP.BINANCE",
        account_id="BINANCE-001",
    )
    trader, client, exchange, _log = _trader_with_one_sandbox_client(open_orders=[order])
    clock = _ClockSpy()
    redis = _RedisSpy()
    key = balance_key(DEFAULT_BALANCE_KEY_PREFIX, "TINO-001", "BINANCE")
    redis.store[key] = json.dumps({"USDT": "10000.00 USDT", "BTC": "0.5 BTC"})

    recover_on_start(
        trader=trader,
        clock=clock,
        redis=redis,
        key_prefix=DEFAULT_BALANCE_KEY_PREFIX,
        trader_id="TINO-001",
    )

    # Exactly one all-currency AccountState replayed.
    assert len(client.account_state_calls) == 1
    call = client.account_state_calls[0]
    assert call["margins"] == []
    assert call["reported"] is True
    assert call["ts_event"] == clock.timestamp_ns()
    by_ccy = {b.currency: b for b in call["balances"]}
    assert by_ccy[USDT].total == Money(10_000, USDT)
    assert by_ccy[USDT].locked == Money(0, USDT)
    assert by_ccy[BTC].total == Money(0.5, BTC)

    # Open order re-processed into its matching engine with its own account_id.
    me = exchange.get_matching_engine("BTCUSDT-PERP.BINANCE")
    assert me is not None
    assert me.processed == [(order, "BINANCE-001")]


def test_recover_on_start_no_snapshot_does_not_replay_balances() -> None:
    """No saved snapshot (redis.get → None) → never call generate_account_state.

    Replaying an empty balance set would clobber the fresh starting_balances; we
    must leave the sim's clean account untouched on first boot.
    """

    trader, client, _exchange, _log = _trader_with_one_sandbox_client()
    recover_on_start(
        trader=trader,
        clock=_ClockSpy(),
        redis=_RedisSpy(),  # empty store → get returns None
        key_prefix=DEFAULT_BALANCE_KEY_PREFIX,
        trader_id="TINO-001",
    )

    assert client.account_state_calls == []


def test_recover_on_start_missing_matching_engine_logs_and_continues() -> None:
    """An open order whose instrument has no matching engine → log error + skip.

    Same容错 as NT's template (engine.pyx:1549): me is None → log + continue,
    never crash the pod.
    """

    order = _RehydrateOrderSpy(
        instrument_id="UNKNOWN-PERP.BINANCE",  # no engine registered for this
        account_id="BINANCE-001",
    )
    trader, client, _exchange, log = _trader_with_one_sandbox_client(open_orders=[order])
    redis = _RedisSpy()
    key = balance_key(DEFAULT_BALANCE_KEY_PREFIX, "TINO-001", "BINANCE")
    redis.store[key] = json.dumps({"USDT": "10000.00 USDT"})

    recover_on_start(
        trader=trader,
        clock=_ClockSpy(),
        redis=redis,
        key_prefix=DEFAULT_BALANCE_KEY_PREFIX,
        trader_id="TINO-001",
    )

    # Balance replay still happened; order skipped with an error log.
    assert len(client.account_state_calls) == 1
    assert any("matching engine" in e.lower() for e in log.errors)


def test_recover_on_start_exec_engine_none_returns_safely() -> None:
    """trader._exec_engine missing (getattr fallback) → log + return, no crash."""

    log = _LogSpy()
    trader = _TraderSpy(_exec_engine=None, cache=_CacheSpy(), log=log)

    recover_on_start(
        trader=trader,
        clock=_ClockSpy(),
        redis=_RedisSpy(),
        key_prefix=DEFAULT_BALANCE_KEY_PREFIX,
        trader_id="TINO-001",
    )

    # No throw; a diagnostic was logged.
    assert log.errors or log.warnings


def test_recover_on_start_skips_non_sandbox_clients() -> None:
    """A registered client WITHOUT an .exchange (a real venue client) is skipped.

    Only sandbox clients carry an in-process ``.exchange``; we duck-type on that
    rather than assume a client class, so live/DEMO clients are silently passed.
    """

    @dataclass
    class _LiveClientSpy:
        account_state_calls: list[Any] = field(default_factory=list)

        def generate_account_state(self, **kwargs: Any) -> None:
            self.account_state_calls.append(kwargs)

    live_client = _LiveClientSpy()
    exec_engine = _ExecEngineSpy(_clients={"BINANCE": live_client})
    trader = _TraderSpy(_exec_engine=exec_engine, cache=_CacheSpy(), log=_LogSpy())
    redis = _RedisSpy()
    redis.store[balance_key(DEFAULT_BALANCE_KEY_PREFIX, "TINO-001", "BINANCE")] = json.dumps(
        {"USDT": "10000.00 USDT"},
    )

    recover_on_start(
        trader=trader,
        clock=_ClockSpy(),
        redis=redis,
        key_prefix=DEFAULT_BALANCE_KEY_PREFIX,
        trader_id="TINO-001",
    )

    assert live_client.account_state_calls == []


def test_snapshot_on_stop_writes_all_currency_totals_to_redis() -> None:
    """snapshot_on_stop reads the venue account totals → SET private Redis key."""

    account = _AccountSpy(
        {
            USDT: AccountBalance(
                total=Money(8_000, USDT),
                locked=Money(1_000, USDT),
                free=Money(7_000, USDT),
            ),
            BTC: AccountBalance(
                total=Money(0.25, BTC),
                locked=Money(0, BTC),
                free=Money(0.25, BTC),
            ),
        },
    )
    me = _MatchingEngineSpy()
    exchange = _ExchangeSpy(id="BINANCE", _engines={"x": me})
    client = _ClientSpy(exchange=exchange)
    exec_engine = _ExecEngineSpy(_clients={"BINANCE": client})
    cache = _CacheSpy(_accounts_by_venue={"BINANCE": account})
    trader = _TraderSpy(_exec_engine=exec_engine, cache=cache, log=_LogSpy())
    redis = _RedisSpy()

    snapshot_on_stop(
        trader=trader,
        redis=redis,
        key_prefix=DEFAULT_BALANCE_KEY_PREFIX,
        trader_id="TINO-001",
    )

    key = balance_key(DEFAULT_BALANCE_KEY_PREFIX, "TINO-001", "BINANCE")
    assert key in redis.store
    restored = deserialize_balances(json.loads(redis.store[key]))
    assert restored == {USDT: Money(8_000, USDT), BTC: Money(0.25, BTC)}


def test_snapshot_on_stop_exec_engine_none_returns_safely() -> None:
    """Missing _exec_engine on stop → log + return, no crash, no Redis write."""

    log = _LogSpy()
    trader = _TraderSpy(_exec_engine=None, cache=_CacheSpy(), log=log)
    redis = _RedisSpy()

    snapshot_on_stop(
        trader=trader,
        redis=redis,
        key_prefix=DEFAULT_BALANCE_KEY_PREFIX,
        trader_id="TINO-001",
    )

    assert redis.store == {}
    assert log.errors or log.warnings
