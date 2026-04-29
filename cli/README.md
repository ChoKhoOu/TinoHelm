# TinoHelm CLI

Rust CLI for TinoHelm. The old interactive TUI has been removed; `tino` is now a one-shot, LLM-first control plane with explicit human and machine output modes.

## Build

```bash
cd cli
cargo build --release
```

Binary: `cli/target/release/tino`.

## Output modes

```bash
tino backtest list                         # human text
tino -f json backtest list                 # raw JSON for scripts
tino -f llm api get /api/node/status       # stable {ok,data,error,meta} envelope
```

`llm` mode is intended for autonomous callers. It keeps stdout parseable on success and on API errors.

## Auth

The backend expects `X-API-Key` when `TINO_API_KEY` is configured server-side.

Priority for client auth:

1. `--api-key`
2. `TINO_API_KEY`
3. `~/.tino/credentials/api_key` (created by `tino auth login`, mode `0600`)
4. `~/.tino/config/user.yaml` (`api.key`)

```bash
tino auth status
tino auth login --stdin < .secret/tino_api_key
tino auth logout
```

Config URL priority:

1. `--api-url`
2. `TINO_API_URL`
3. `~/.tino/config/user.yaml` (`api.url`)
4. `http://localhost:8000`

Example `~/.tino/config/user.yaml`:

```yaml
api:
  url: http://localhost:8000
  key_file: ~/.tino/credentials/api_key
```

## Generic API coverage

Every FastAPI operation can be called through `tino api` even before a typed shortcut exists.

```bash
tino api routes --filter /api/factor
tino -f llm api get /api/factor/list -q include_experimental=false
tino -f llm api call POST /api/factor/run --body-file factor_run.json
tino -f llm api download /api/backtest/<run_id>/artifact/results.json -o results.json
tino -f llm api call DELETE /api/backtest/<run_id>
```

## Typed workflow examples

```bash
# Backtest
tino backtest run btc_multi_factor \
  --symbol BTCUSDT-PERP \
  --interval 5m \
  --start 2025-02-01 \
  --end 2025-03-01

tino -f llm backtest wait <run_id> --timeout 300
tino backtest result <run_id>

# Factor research
tino factor list
tino -f llm factor explore --body-file factor_explore.json
tino -f llm factor run --body-file factor_run.json
tino -f llm factor runs --limit 20
tino -f llm factor report <run_id>
tino factor params-grid --body-file params_grid.json

# Signal research/export
tino signal list
tino -f llm signal run --body-file signal_run.json
tino -f llm signal report <run_id>
tino -f llm signal export <run_id>

# Universe helpers
tino universe list
tino universe sync ~/.tino/research/universes/top10_perp.csv
tino universe get <universe_id>
```

## Requirements

- TinoHelm API server for API-backed operations: `docker compose up -d`
- Rust toolchain for building from source
