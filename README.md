# TinoHelm

A thin orchestrator around [NautilusTrader](https://nautilustrader.io) v1.226 that
runs each strategy in its own Docker pod, glues them together over Redis Streams,
ships event notifications & slash-command control through a single Discord bot,
and exposes a Make-based control plane.

> **Why this layer at all?** NautilusTrader already has every primitive that
> matters: an event-driven strategy engine, venue adapters (Bybit, Binance,
> Databento, Interactive Brokers, …), a sandbox execution client for paper
> trading on live data, a Redis-backed `MessageBus` for cross-process pub/sub,
> a `Cache` and `Portfolio` you can query from anywhere. TinoHelm
> **never re-implements those** — it just wires them together for the
> "many strategies, each its own pod, one Discord bot watching all" pattern.

---

## Architecture (5-line summary)

```
┌─ strategy pod foo ─┐   ┌─ strategy pod bar ─┐    ┌─ notifier pod ─┐
│ NT TradingNode     │   │ NT TradingNode     │    │ NT TradingNode │
│ + BridgeActor      │   │ + BridgeActor      │    │ + NotifierActor│
│   (Python)         │   │   (Python)         │    │   discord.py   │
└─────────┬──────────┘   └────────┬───────────┘    └───────┬────────┘
          │ XADD events.*         │ XADD events.*          │
          ▼                       ▼                        ▼
              ┌─────────────── Redis Streams ───────────────┐
              │ trader-{id}:stream:events.{order,position,…}│
              │ tinohelm:control:{strategy}                 │
              └─────────────────────────────────────────────┘
                            ▲                    ▲
                            │ make / Discord    /
                            └──── tinohelm.cli ─┘
```

* **Each strategy = one Compose service.** Started/stopped via `make`.
* **Cross-pod traffic = Redis Streams.** NT's own `MessageBusConfig`
  (`external_streams`) reads it; we never touch the wire format.
* **Discord = the human interface.** All trade events stream into one channel;
  slash commands (`/pause`, `/resume`, `/flatten`, `/status`) write back to
  Redis, the strategy pod's `BridgeActor` translates them into in-process
  `Trader.{stop,start,market_exit}_strategy()` calls (the same things NT's own
  `ControllerCommand` enum drives).
* **Sandbox = same code, different exec client.** Set `mode = "sandbox"`
  (or `make sandbox STRATEGY=...`) and `nautilus_trader.adapters.sandbox`
  takes over.

---

## Quick start

```bash
# 1. configure
cp .env.example .env
$EDITOR .env                              # set DISCORD_BOT_TOKEN, venue keys

# 2. boot the long-running services
make up                                   # starts redis + notifier

# 3. add a strategy config
cp configs/strategies/example.toml configs/strategies/myfoo.toml
$EDITOR configs/strategies/myfoo.toml     # set strategy.id, instruments

# 4. add a service block in compose.yaml (copy strategy-example, rename)
#    (the 'profiles: ["strategy-myfoo"]' line is what `make` keys off)

# 5. run it
make sandbox STRATEGY=myfoo               # paper-trades against live data
make logs    STRATEGY=myfoo

# control plane — same commands work from Discord slash commands
make pause   STRATEGY=myfoo               # trader.stop_strategy
make resume  STRATEGY=myfoo               # trader.start_strategy
make flatten STRATEGY=myfoo               # trader.market_exit_strategy
make stop    STRATEGY=myfoo               # docker compose stop
```

`make help` lists all commands.

## What's in this repo

| Path | Purpose |
|---|---|
| `tinohelm/` | The thin Python package: TOML→NT config, BridgeActor, CLI. |
| `tinohelm/notifier/` | The Discord notifier pod (Discord events ↔ Redis ↔ NT). |
| `strategies/example/` | A no-op smoke-test strategy. Drop your own next to it. |
| `configs/strategies/*.toml` | One file per strategy. |
| `configs/notifier.toml` | Discord channel, watched strategies, summary time. |
| `compose.yaml` | One service per strategy + redis + notifier. |
| `Makefile` | `make run / pause / resume / flatten / stop / status`. |
| `docker/*.Dockerfile` | Image definitions (uv-based, Python 3.11). |
| `tests/` | Unit tests for config & bridge actor. |

## Adding a new strategy

1. **Code**: drop your `Strategy` subclass under `strategies/<name>/strategy.py`.
   Inherit from `nautilus_trader.trading.strategy.Strategy` exactly as
   documented by NT — TinoHelm never wraps it.
2. **Config**: copy `configs/strategies/example.toml`, set `strategy.id` and
   the venue factory paths.
3. **Compose**: copy the `strategy-example` block, rename the service and
   `profiles: ["strategy-<name>"]`.
4. **Run**: `make run STRATEGY=<name>`.

## What's intentionally out of scope (v0.1)

* **Backtesting** — NT already has `BacktestEngine`, run it directly.
* **Pure-Rust strategy pods** — blocked upstream until NT's
  `LiveNodeConfig.msgbus` is unlocked
  ([`crates/live/src/config.rs:608`](https://github.com/nautechsystems/nautilus_trader/blob/v1.226.0/crates/live/src/config.rs#L608)).
  The Python strategy/runner can be swapped to a Rust binary then with no
  change to topics or Compose layout.
* **Multi-account / multi-trader-id per pod** — every pod has one `trader_id`.
* **K8s / Helm** — Compose is enough for one box.

## Smoke test

```bash
docker compose up -d redis
docker compose run --rm strategy-example python -m tinohelm.cli ping --strategy-id EXAMPLE-001
```

The `ping` command writes to `tinohelm:control:EXAMPLE-001` on Redis. If the
strategy pod is running, the `BridgeActor` will log `BridgeActor: ping ack`.

For deeper verification:

```bash
make up
make sandbox STRATEGY=example
docker exec tinohelm-redis redis-cli XLEN trader-TINO-001:stream:events.system.component_state_changed
```

## License

MIT.
