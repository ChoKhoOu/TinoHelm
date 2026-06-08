"""Sandbox RESTART RECOVERY — restore funds / positions / orders / strategy state.

WHY THIS IS SHARED GLUE (in ``tinohelm/``), not strategy code: it works around a
GENERAL seam in NT's sandbox mode that bites EVERY persisted sandbox pod, not a
quirk of any one strategy.

The seam: with ``[sandbox] persist=true`` we keep the Redis cache across restarts
(``flush_on_start=false``), so NT's ``exec_engine.load_cache()`` reads back the
historical Account / Order / Position on boot (kernel.py:462). But the
``SandboxExecutionClient`` constructor then calls ``exchange.initialize_account()``
during ``node.build()``, which UNCONDITIONALLY overwrites that just-loaded balance
with the fresh ``starting_balances`` (adapters/sandbox/execution.py:146 →
backtest/engine.pyx:3775 ``_generate_fresh_account_state``). NT has no "sandbox
restart, keep my money" hook. Two things are therefore left un-restored after a
sandbox restart:

  1. The LAST real all-currency balance (clobbered by initialize_account).
  2. The open orders' presence IN THE SIM'S MATCHING ENGINES — sandbox
     ``connect()`` only ``add_instrument``s, and a fresh ``SimulatedExchange``'s
     ``_matching_engines`` starts empty (backtest/engine.pyx:2861), so orders that
     NT loaded back into the cache are not actually live on the sim book.

The fix (two NT seams, both copied from NT's own backtest path — not reinvented):

  * Funds: replay ONE all-currency ``AccountState`` over the fresh sim account via
    ``ExecutionClient.generate_account_state(balances, margins, reported, ts_event)``
    (execution/client.pyx:329). The event flows Portfolio→cache→Redis
    (portfolio.pyx apply upserts each currency) and the sim's later margin
    re-computation reads this same cache account. We deliberately use
    ``generate_account_state`` (full re-send) rather than ``adjust_account`` (which
    only touches CURRENCIES already present and so cannot add e.g. proceeds from a
    closed position — engine.pyx:3221).

  * Open orders: re-process each non-emulated open order into its matching engine,
    copying NT's BacktestEngine iteration-0 template verbatim
    (backtest/engine.pyx:1539-1554): ``cache.orders_open(venue=)`` →
    ``exchange.get_matching_engine(iid)`` → ``matching_engine.process_order(order,
    order.account_id)``. ``me is None`` → log + continue, same容错 as NT.

THREE HARD CONSTRAINTS on the replayed balances (verified on NT 1.227.0):

  1. The sandbox account must be MULTI-currency (config does NOT set
     ``base_currency``); otherwise base.pyx rejects a multi-currency AccountState.
     Enforced/​warned at the config layer.
  2. Each ``AccountBalance`` must satisfy ``total - locked == free``
     (model/objects.pyx:1924, else ValueError). We always emit ``locked=0`` /
     ``free=total``.
  3. ``margins=[]`` + ``locked=0`` so the sim RE-COMPUTES margin/locked from the
     re-hydrated open orders + positions (accounting/margin.pyx:715), rather than
     us guessing the locked split.

WIRING: the BridgeActor (NT's one allowed Controller — it already holds
``self._trader``) calls ``recover_on_start`` at the end of ``on_start`` and
``snapshot_on_stop`` at the end of ``on_stop``, guarded by ``mode=="sandbox" and
persist``. The Controller resolves the
``trader → _exec_engine → registered_clients[venue] → (client, client.exchange)``
chain and feeds those refs to these helpers — the PURE functions below never touch
NT runtime, so they are spy-testable in isolation.

NT VERSION: nothing here keys on a version number. The single private attribute we
reach through is ``trader._exec_engine`` (getattr fallback, None → log + return);
sandbox clients are identified by duck-typing ``client.exchange`` (None → skip);
everything else is public NT API.
"""

from __future__ import annotations

import json
from typing import Any

from nautilus_trader.model.objects import AccountBalance, Currency, Money

# TinoHelm-private Redis namespace for the last-known all-currency balance, kept
# wholly separate from NT's ``accounts:`` / ``stream:`` keys. ``trader_id`` in the
# key prevents one pod clobbering another's snapshot.
DEFAULT_BALANCE_KEY_PREFIX = "tinohelm:sandbox:balance"


def balance_key(prefix: str, trader_id: str, venue: str) -> str:
    """Compose the private Redis key holding a venue's last all-currency balance.

    Format: ``{prefix}:{trader_id}:{venue}``. One JSON string per venue.
    """

    return f"{prefix}:{trader_id}:{venue}"


# ─── pure functions (no NT runtime dependency) ───────────────────────────────


def build_account_balances(target: dict[Currency, Money]) -> list[AccountBalance]:
    """Build the ``list[AccountBalance]`` an ``AccountState`` needs from totals.

    One :class:`AccountBalance` per currency, always ``locked=Money(0)`` and
    ``free=total`` — so NT's invariant ``total - locked == free``
    (model/objects.pyx:1924) holds and the sim re-computes the real margin/locked
    split from the re-hydrated orders/positions (constraints 2 & 3). An empty
    ``target`` (first boot / no snapshot) → empty list, which is a no-op upsert
    in NT and signals the caller to skip the replay entirely.
    """

    balances: list[AccountBalance] = []
    for currency, total in target.items():
        balances.append(
            AccountBalance(
                total=total,
                locked=Money(0, currency),
                free=total,
            ),
        )
    return balances


def serialize_balances(balances_total: dict[Currency, Money]) -> dict[str, str]:
    """Serialize all-currency totals to a JSON-safe ``{ccy_code: "<amt> <CCY>"}``.

    The value is the Money string form so :meth:`Money.from_str` restores it
    losslessly on the way back; we use plain JSON (not msgpack) because this is a
    TinoHelm-private key that never passes through NT's serializer.
    """

    return {currency.code: str(money) for currency, money in balances_total.items()}


def deserialize_balances(raw: dict[str, str] | None) -> dict[Currency, Money]:
    """Restore all-currency totals from the JSON dict; degrade to ``{}`` on rot.

    A missing key (``None``), an empty dict, or any value that does not parse as
    a :class:`Money` is treated as "no usable snapshot" → ``{}``. The caller then
    skips the balance replay and leaves the fresh ``starting_balances`` in place,
    so a corrupt private key can never crash the pod.
    """

    if not raw:
        return {}
    restored: dict[Currency, Money] = {}
    for value in raw.values():
        if not isinstance(value, str):
            return {}
        try:
            money = Money.from_str(value)
        except (ValueError, TypeError):
            return {}
        restored[money.currency] = money
    return restored


def snapshot_account_totals(account: Any) -> dict[Currency, Money]:
    """Extract the current all-currency ``total`` from an NT ``Account``.

    Reads ``account.balances()`` (``dict[Currency, AccountBalance]``) and keeps
    each currency's ``.total``. A ``None`` account (cache had no account for the
    venue) → ``{}`` (nothing to snapshot).
    """

    if account is None:
        return {}
    return {currency: bal.total for currency, bal in account.balances().items()}


def open_orders_to_rehydrate(open_orders: list[Any]) -> list[Any]:
    """Filter the open orders that should be re-processed into the sim book.

    Emulated orders are skipped — NT loads them into the emulator already, so the
    matching engine must not see them (copies the ``is_emulated`` skip in NT's
    BacktestEngine iteration-0 template, backtest/engine.pyx:1543).
    """

    return [order for order in open_orders if not order.is_emulated]


# ─── runtime wiring helpers (operate on NT runtime; BridgeActor feeds refs) ──


def _sandbox_clients(trader: Any) -> list[tuple[Any, Any]]:
    """Resolve ``(client, exchange)`` pairs for every sandbox exec client.

    The ONLY private attribute穿透: ``trader._exec_engine`` (getattr fallback).
    Sandbox clients are identified by duck-typing ``client.exchange`` (a real
    venue client has none), so we never assume a client class — live/DEMO
    clients are silently skipped.
    """

    exec_engine = getattr(trader, "_exec_engine", None)
    if exec_engine is None:
        return []
    pairs: list[tuple[Any, Any]] = []
    for client in exec_engine.registered_clients.values():
        exchange = getattr(client, "exchange", None)
        if exchange is None:
            continue  # not a sandbox client
        pairs.append((client, exchange))
    return pairs


def recover_on_start(
    *,
    trader: Any,
    clock: Any,
    redis: Any,
    key_prefix: str,
    trader_id: str,
) -> None:
    """Replay the last all-currency balance + re-hydrate open orders into the sim.

    Called by ``BridgeActor.on_start`` (the one allowed Controller, already
    holding ``self._trader``) only when ``mode=="sandbox" and persist``. NT
    guarantees this runs AFTER ``load_cache`` + the build-time
    ``initialize_account`` and BEFORE any strategy ``on_start`` (all actors'
    ``on_start`` precede strategies' in ``Trader._start``, trader.py:255-265), so
    the replay both wins over the fresh starting_balances and lands before the
    strategy can place its first order.
    """

    pairs = _sandbox_clients(trader)
    if not pairs:
        # _exec_engine missing/renamed, or no sandbox client → nothing to do.
        # Surfaced as a warning so an upgrade-time rename is visible, never fatal.
        trader.log.warning(
            "sandbox_recovery.recover_on_start: no sandbox exec client resolved "
            "(trader._exec_engine missing or no client.exchange); skipping recovery",
        )
        return

    cache = trader.cache
    log = trader.log

    for client, exchange in pairs:
        venue = str(exchange.id)
        # 1) Funds: replay the last all-currency balance over the fresh account.
        target = deserialize_balances(
            _redis_get_json(redis, balance_key(key_prefix, trader_id, venue))
        )
        if target:
            balances = build_account_balances(target)
            client.generate_account_state(
                balances=balances,
                margins=[],
                reported=True,
                ts_event=clock.timestamp_ns(),
            )
            log.info(
                f"sandbox_recovery: replayed {len(balances)} currency balance(s) for {venue}",
            )
        else:
            # First boot / corrupt snapshot → leave fresh starting_balances.
            log.info(
                f"sandbox_recovery: no balance snapshot for {venue}; "
                "keeping fresh starting_balances",
            )

        # 2) Open orders: re-process each into its matching engine (NT template).
        for order in open_orders_to_rehydrate(cache.orders_open(venue=exchange.id)):
            me = exchange.get_matching_engine(order.instrument_id)
            if me is None:
                log.error(
                    f"sandbox_recovery: no matching engine for {order.instrument_id} "
                    f"to re-hydrate {order}",
                )
                continue
            me.process_order(order, order.account_id)


def snapshot_on_stop(
    *,
    trader: Any,
    redis: Any,
    key_prefix: str,
    trader_id: str,
) -> None:
    """Snapshot the current all-currency totals to the private Redis key.

    Called by ``BridgeActor.on_stop`` (guarded ``mode=="sandbox" and persist``).
    We MUST stash the live balance here rather than read it back from the cache
    on the next ``on_start``: by then the build-time ``initialize_account`` has
    already overwritten the same ``{venue}-001`` account slot with fresh
    starting_balances, and NT's ``accounts:`` Redis key only holds the last
    event (= that fresh state). This private key is the only durable record of
    the "real" balance.
    """

    pairs = _sandbox_clients(trader)
    if not pairs:
        trader.log.warning(
            "sandbox_recovery.snapshot_on_stop: no sandbox exec client resolved; "
            "skipping balance snapshot",
        )
        return

    cache = trader.cache
    log = trader.log
    for _client, exchange in pairs:
        account = cache.account_for_venue(exchange.id)
        totals = snapshot_account_totals(account)
        if not totals:
            log.info(
                f"sandbox_recovery: no account totals to snapshot for {exchange.id}",
            )
            continue
        redis.set(
            balance_key(key_prefix, trader_id, str(exchange.id)),
            json.dumps(serialize_balances(totals)),
        )
        log.info(
            f"sandbox_recovery: snapshotted {len(totals)} currency balance(s) for {exchange.id}",
        )


def _redis_get_json(redis: Any, key: str) -> dict[str, str] | None:
    """Read + JSON-decode a private balance key; any failure → ``None`` (degrade).

    Accepts the bytes a raw ``redis.Redis`` returns or a decoded str; a missing
    key or unparseable payload is treated as "no snapshot" so a rotten key never
    crashes recovery.
    """

    value = redis.get(key)
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    try:
        decoded = json.loads(value)
    except (ValueError, TypeError):
        return None
    if not isinstance(decoded, dict):
        return None
    return decoded
