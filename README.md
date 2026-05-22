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

# 3. add a strategy (copy the example folder, edit code + toml)
cp -r strategies/example strategies/myfoo
$EDITOR strategies/myfoo/strategy.py        # your Strategy subclass
$EDITOR strategies/myfoo/tinohelm.toml      # set strategy.id, venue, params

# 4. one-key deploy
make deploy STRATEGY=myfoo                  # sandbox by default
make deploy STRATEGY=myfoo MODE=live        # ...or live
make logs   STRATEGY=myfoo

# control plane — same commands work from Discord slash commands
make pause   STRATEGY=myfoo                 # trader.stop_strategy
make resume  STRATEGY=myfoo                 # trader.start_strategy
make flatten STRATEGY=myfoo                 # trader.market_exit_strategy
make stop    STRATEGY=myfoo                 # docker stop + rm
```

No edits to `compose.yaml` or `configs/notifier.toml` required —
the notifier autodiscovers strategies from a Redis announce stream
and routes events to either the sandbox or live Discord channel.

`make help` lists all commands.

## What's in this repo

| Path | Purpose |
|---|---|
| `tinohelm/` | The thin Python package: TOML→NT config, BridgeActor, CLI. |
| `tinohelm/notifier/` | The Discord notifier pod (Discord events ↔ Redis ↔ NT). |
| `strategies/<id>/strategy.py` | Your `Strategy` subclass. |
| `strategies/<id>/tinohelm.toml` | Strategy config (id, mode, venue factories, params). |
| `configs/notifier.toml` | Discord channel envs (sandbox + live), summary time. |
| `compose.yaml` | One generic `strategy` service + redis + notifier. |
| `Makefile` | `make deploy / pause / resume / flatten / stop / status`. |
| `docker/*.Dockerfile` | Image definitions (uv-based, Python 3.12). |
| `tests/` | Behavior tests (config, bridge actor, autodiscover, channel routing). |

## Adding a new strategy

1. `cp -r strategies/example strategies/myfoo` — keeps `__init__.py` and
   `tinohelm.toml` co-located with `strategy.py`.
2. Edit `strategies/myfoo/strategy.py` (your `Strategy` subclass — TinoHelm
   never wraps NT's class).
3. Edit `strategies/myfoo/tinohelm.toml`: set `strategy.id`, mode, venue
   factories, params.
4. `make deploy STRATEGY=myfoo` — done. The notifier picks it up via the
   `tinohelm:announce` stream within 1s.

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

[AGPL-3.0](LICENSE).
