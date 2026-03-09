## Context

TinoHelm's current CLI is a Python (Typer) thin HTTP client, ~1690 lines. It outputs static text tables and polls the API for status updates. Distribution requires PyInstaller with inherent startup overhead (~5s onefile, ~0.2s onedir). The backend is a FastAPI server with REST endpoints and nascent WebSocket support (`/ws/events`, `/ws/equity`).

The CLI will be rewritten in Rust using Ratatui for TUI and clap for CLI, producing a single self-contained binary with both interactive and one-shot modes.

## Goals / Non-Goals

**Goals:**
- Single static binary (<10MB) with <10ms startup, zero runtime dependencies
- Full CLI feature parity with existing Python CLI (backtest, strategy, data, node commands)
- Interactive TUI dashboard for browsing backtests, viewing results, monitoring live progress
- Real-time updates via WebSocket (backtest progress, equity curves, node status)
- Cross-platform builds (macOS arm64/x86_64, Linux x86_64/arm64)

**Non-Goals:**
- Replacing the Web frontend (Next.js) — TUI and Web serve different contexts
- Moving any backend logic into the CLI — it remains a pure API client
- Supporting Windows as a first-class target (best-effort via crossterm)
- Custom charting library — use Ratatui's built-in widgets (Sparkline, BarChart, Canvas)

## Decisions

### 1. Monorepo subfolder (`cli/`) vs separate repository

**Decision**: Monorepo subfolder at `cli/`.

**Rationale**: API contract changes (new endpoints, schema changes) happen alongside backend changes. Monorepo ensures they stay in sync. CI can build both Python backend and Rust CLI in one pipeline. No version matrix to manage across repos.

**Alternative considered**: Separate repo with OpenAPI-generated types. Rejected — adds a sync step and makes atomic changes impossible.

### 2. Mode dispatch: CLI vs TUI

**Decision**: `tino <subcommand>` enters CLI mode; `tino` (no args) or `tino ui` enters TUI mode.

```
fn main() {
    let cli = Cli::parse();
    match cli.command {
        Some(cmd) => cli::dispatch(cmd),   // one-shot
        None => tui::run(),                // interactive
    }
}
```

**Rationale**: One binary, two modes. Scriptability (`tino backtest list --json | jq`) preserved. Interactive use is the zero-argument default — the most natural gesture for a terminal user.

### 3. Async runtime: tokio

**Decision**: tokio as the async runtime.

**Rationale**: Required by reqwest (HTTP) and tokio-tungstenite (WebSocket). The TUI event loop uses `tokio::select!` to multiplex terminal events, WebSocket messages, and timer ticks. No meaningful alternative in the Rust ecosystem for this combination.

### 4. TUI architecture: Elm-inspired (Model → Update → View)

**Decision**: Adopt the Elm architecture pattern common in Ratatui projects.

```
App (Model)
  ├── current_view: View enum (BacktestList, BacktestDetail, StrategyList, ...)
  ├── backtests: Vec<BacktestRun>
  ├── selected_index: usize
  ├── ws_state: ConnectionState
  └── error_banner: Option<String>

Events → update(app, event) → mutate App
App → view(frame, app) → render to terminal
```

**Rationale**: Clean separation between state management and rendering. Immediate-mode rendering means the entire frame is redrawn on every state change — simple to reason about, no stale UI bugs.

### 5. API client: shared between CLI and TUI

**Decision**: Single `api.rs` module with both sync (blocking reqwest) for CLI and async for TUI.

```
cli/src/
├── api.rs          # ApiClient { base_url, client }
│   ├── list_backtests()
│   ├── get_result(run_id)
│   ├── run_backtest(params)
│   └── connect_ws(path) → WsStream
├── types.rs        # Serde structs matching FastAPI Pydantic models
├── cli/            # clap handlers calling api.rs
└── tui/            # ratatui views calling api.rs
```

**Rationale**: Avoids duplicating HTTP logic. Types are shared so CLI and TUI always agree on response shapes.

### 6. Single WebSocket + client-side message bus

**Decision**: One unified WebSocket endpoint (`/ws/events`) on the FastAPI backend pushes ALL event types as type-tagged JSON. On the Rust TUI side, a `MessageBus` receives all messages and dispatches to subscriber components by event type prefix.

Server-side message format:
```json
{"type": "backtest.progress", "run_id": "ac5ef...", "pct": 53, "elapsed_secs": 12.3}
{"type": "backtest.stats", "run_id": "ac5ef...", "trades": 15, "pnl": 33.79}
{"type": "backtest.completed", "run_id": "ac5ef...", "status": "completed", "summary": {...}}
{"type": "node.heartbeat", "node_type": "sandbox", "ts": "2026-03-09T..."}
{"type": "system.error", "message": "Worker crashed"}
```

Client-side bus:
```rust
// Components subscribe by prefix
bus.subscribe("backtest.*", backtest_list_handler);
bus.subscribe("backtest.completed", backtest_detail_handler);
bus.subscribe("node.*", node_status_handler);
bus.subscribe("system.*", error_banner_handler);
```

**Rationale**: Current polling (`GET /status` every 1s) is wasteful and laggy for TUI. A single WS endpoint is simpler to maintain than per-resource endpoints. Adding new event types only requires a new `type` string, no new endpoints. Client-side bus gives components clean separation — each subscribes only to what it cares about. Reconnection logic is centralized (one connection to manage).

**Alternative considered**: Multiple WS endpoints (`/ws/backtest/{run_id}`, `/ws/nodes`, etc.). Rejected — multiplies server-side connection management complexity and requires the TUI to manage multiple connections with independent reconnect state.

### 6b. OpenAPI schema for type synchronization

**Decision**: Use FastAPI's auto-generated `/openapi.json` as the contract between Python backend and Rust client. CI step validates that Rust serde structs match the OpenAPI schema. Initially manual sync with schema as reference; auto-generation (`openapi-generator` for Rust) can be added later.

**Rationale**: Pydantic models and serde structs can drift silently. The OpenAPI schema is already free (FastAPI generates it automatically). Using it as a validation checkpoint catches drift before runtime.

### 7. Configuration: read from `~/.tino/config/`

**Decision**: Rust CLI reads the same YAML config files as the Python backend. `--api-url` flag overrides, env var `TINO_API_URL` as fallback.

**Rationale**: Single config surface for users. No new config format to learn.

### 8. Short ID prefix matching

**Decision**: Reuse the `resolve_run_id` API behavior already implemented server-side. Rust CLI just sends the short prefix; the API resolves it.

**Rationale**: Logic is already in the API layer. CLI is thin — no need to duplicate resolution logic client-side.

### 9. Crate dependencies

| Purpose | Crate | Version |
|---------|-------|---------|
| CLI parsing | `clap` (derive) | 4.x |
| TUI rendering | `ratatui` | 0.29+ |
| Terminal backend | `crossterm` | 0.28+ |
| HTTP client | `reqwest` (rustls) | 0.12+ |
| WebSocket | `tokio-tungstenite` | 0.24+ |
| Async runtime | `tokio` (full) | 1.x |
| Serialization | `serde`, `serde_json` | 1.x |
| Config | `serde_yaml`, `dirs` | latest |
| Error handling | `anyhow` | 1.x |
| Colors | `ratatui::style` | built-in |

## Risks / Trade-offs

**[Risk] API type drift** — Python Pydantic models and Rust serde structs can diverge silently.
→ Mitigation: FastAPI exports `/openapi.json`. CI step validates Rust types against OpenAPI schema. Initially manual sync, auto-generate later.

**[Risk] Rust learning curve** — Team may not be fluent in Rust.
→ Mitigation: The CLI is a thin client (~2000 lines estimated). No unsafe code, no complex lifetimes. Ratatui has excellent examples and docs.

**[Risk] TUI testing difficulty** — Hard to unit-test visual output.
→ Mitigation: Test API client and state logic independently. Use Ratatui's `TestBackend` for snapshot tests of rendered frames.

**[Risk] WebSocket reliability** — Network interruptions break TUI.
→ Mitigation: Auto-reconnect with exponential backoff. Error banner in TUI (not crash). CLI mode doesn't use WebSocket — only TUI.

**[Risk] Terminal compatibility** — SSH, tmux, old terminals may render poorly.
→ Mitigation: crossterm handles most differences. Fallback to 16-color mode. Test in tmux and common terminal emulators.

**[Trade-off] Two build toolchains** — Rust (cargo) + Python (pip) in same repo.
→ Accepted. They don't interfere. CI runs them independently. The Rust CLI has no dependency on Python at build or runtime.

**[Trade-off] Python CLI deprecated but not removed immediately**.
→ Accepted. Gradual transition over 2-3 releases. Python CLI prints deprecation hint. Removed once Rust CLI reaches full parity.
