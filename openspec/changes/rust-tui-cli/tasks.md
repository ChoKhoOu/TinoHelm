## 1. Project Scaffolding

- [x] 1.1 Create `cli/` directory with `Cargo.toml`, add dependencies (clap, ratatui, crossterm, reqwest, tokio, tokio-tungstenite, serde, serde_json, serde_yaml, anyhow, dirs)
- [x] 1.2 Create `cli/src/main.rs` with mode dispatch: `Some(cmd)` → CLI, `None` / `ui` → TUI
- [x] 1.3 Create module structure: `cli/src/{cli/, tui/, api.rs, types.rs, config.rs}`

## 2. Configuration & API Client

- [x] 2.1 Implement `config.rs` — read `~/.tino/config/user.yaml`, env var `TINO_API_URL`, `--api-url` flag (priority order)
- [x] 2.2 Implement `types.rs` — serde structs matching FastAPI Pydantic models (BacktestRunItem, BacktestRunStatus, BacktestResult, Strategy, NodeStatus)
- [x] 2.3 Implement `api.rs` — ApiClient with methods: `list_backtests()`, `get_status(run_id)`, `get_result(run_id)`, `run_backtest(params)`, `cancel_backtest(run_id)`, `list_strategies()`, `get_strategy(name)`, `rescan_strategies()`, `fetch_data(params)`, `list_data()`, `node_status()`
- [x] 2.4 Implement connection error handling — human-readable error message with Docker hint, exit code 1

## 3. CLI Commands (clap)

- [x] 3.1 Implement `backtest list` — formatted table output matching Python CLI, `--json` flag support
- [x] 3.2 Implement `backtest run` — accept strategy, --symbol, --interval, --start, --end, --param flags
- [x] 3.3 Implement `backtest result` — display full result, short ID prefix passed directly to API
- [x] 3.4 Implement `backtest status` — display status with progress percentage
- [x] 3.5 Implement `backtest cancel` — cancel a running/queued backtest
- [x] 3.6 Implement `strategy list`, `strategy info`, `strategy validate`, `strategy rescan`
- [x] 3.7 Implement `data fetch`, `data list`, `data info`
- [x] 3.8 Implement `node status`, `node start`, `node stop` (for sandbox/live)
- [x] 3.9 Implement `version` command

## 4. Unified WebSocket Backend + OpenAPI

- [x] 4.1 Consolidate `/ws/events` endpoint to push ALL event types (backtest.*, node.*, system.*) as type-tagged JSON with dot-notation prefix
- [x] 4.2 Implement backtest event publishing — subscribe to Redis PubSub `tino:backtest:progress:{run_id}`, push `backtest.progress`, `backtest.stats`, `backtest.completed` messages
- [x] 4.3 Implement node heartbeat publishing — push `node.heartbeat` messages from Redis heartbeat keys
- [x] 4.4 Verify FastAPI `/openapi.json` covers all REST response models used by the Rust client
- [x] 4.5 Add CI script to validate Rust serde structs against OpenAPI schema (initially as a manual check script)

## 5. TUI Core Framework

- [x] 5.1 Implement TUI event loop — `tokio::select!` multiplexing terminal events, WebSocket messages, and 250ms tick timer
- [x] 5.2 Implement App state model — `current_view`, `backtests`, `selected_index`, `ws_state`, `error_banner`
- [x] 5.3 Implement view routing — BacktestList, BacktestDetail, StrategyList, NodeStatus views with tab switching (`1`/`2`/`3`)
- [x] 5.4 Implement key hint bar — context-sensitive bottom bar showing available keybindings per view

## 6. TUI Views

- [x] 6.1 Implement Backtest List view — table with navigation (j/k/arrows), status coloring, real-time progress for running items
- [x] 6.2 Implement Backtest Detail view — stats panel, equity curve (Sparkline widget), trade summary, Enter to open / Esc to return
- [x] 6.3 Implement New Backtest form — input fields for strategy, symbol, interval, date range; submit via API
- [x] 6.4 Implement Strategy List view — table of registered strategies with type, class, symbol count
- [x] 6.5 Implement Node Status view — sandbox/live status with heartbeat indicator

## 7. TUI Real-time Integration

- [x] 7.1 Implement single WebSocket client — connect to `/ws/events`, receive all event types on one connection
- [x] 7.2 Implement MessageBus — dispatch incoming WS messages to subscriber components by `type` prefix matching (e.g., `backtest.*` → BacktestListView)
- [x] 7.3 Implement auto-reconnect with exponential backoff (1s, 2s, 4s, max 30s) and connection state indicator (green/red dot)
- [x] 7.4 Wire BacktestListView to `backtest.progress` / `backtest.completed` events for live progress updates
- [x] 7.5 Wire NodeStatusView to `node.heartbeat` events with 30s client-side timeout detection

## 8. Error Handling & Polish

- [x] 8.1 Implement TUI error banner — non-crashing error display, auto-dismiss after 5s, API reconnect state
- [x] 8.2 Implement terminal resize handling — re-render on SIGWINCH
- [x] 8.3 Implement clean terminal restore on panic (crossterm panic hook)
- [x] 8.4 Handle unknown/malformed WebSocket messages gracefully (log warning, continue)

## 9. Build & Distribution

- [x] 9.1 Create `cli/build.sh` — `cargo build --release`, produce binary at `dist/tino`
- [x] 9.2 Add GitHub Actions workflow for cross-compilation (macOS arm64/x86_64, Linux x86_64/arm64)
- [x] 9.3 Update root `.gitignore` for Rust build artifacts (`cli/target/`)

## 10. Python CLI Deprecation

- [x] 10.1 Add deprecation notice to Python CLI — print hint on every command: `Hint: a faster native CLI is available, see https://...`
- [x] 10.2 Update CLAUDE.md with Rust CLI build/run instructions
- [x] 10.3 Update README with new CLI installation instructions
