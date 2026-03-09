## Why

The current Python CLI (Typer + PyInstaller) has inherent distribution and startup performance issues: onefile mode takes ~5s to extract on every run, onedir requires distributing a folder. Additionally, the CLI is limited to static text output — there's no way to interactively browse backtests, monitor running jobs in real-time, or view equity curves without leaving the terminal.

Rewriting the CLI in Rust with Ratatui provides: (1) a single static binary with <10ms startup, zero dependencies, trivial cross-platform distribution; (2) a rich interactive TUI for monitoring, browsing, and managing the trading platform; (3) both CLI (one-shot) and TUI (interactive) modes in one binary.

## What Changes

- **New Rust project** in `cli/` subdirectory with Cargo workspace
- **CLI mode** (`tino backtest list`, `tino backtest run ...`, etc.) — replaces Python CLI with clap-based commands, same API contract
- **TUI mode** (`tino` with no subcommand, or `tino ui`) — full-screen interactive dashboard built with Ratatui
  - Backtest list view with keyboard navigation
  - Backtest detail view with stats and terminal-rendered equity curve
  - Real-time backtest progress via WebSocket
  - Strategy browser
  - Node status monitoring
- **WebSocket integration** for real-time TUI updates (backtest progress, equity curves, node heartbeats)
- **Python CLI deprecated** — kept during transition, marked deprecated with hint to use Rust binary
- **BREAKING**: PyInstaller packaging (`tino.spec`, `scripts/build_cli.sh`) will be replaced by `cargo build --release`

## Capabilities

### New Capabilities
- `rust-cli`: One-shot CLI commands (clap) — backtest, strategy, data, node subcommands matching current Python CLI feature parity
- `tui-dashboard`: Interactive full-screen terminal UI with Ratatui — backtest browser, detail views, real-time monitoring, keyboard navigation
- `ws-client`: WebSocket client for real-time data push from FastAPI backend to TUI (backtest progress, equity curves, node status)

### Modified Capabilities
<!-- No existing specs to modify -->

## Impact

- **New dependency**: Rust toolchain required for building CLI (not for running — binary is self-contained)
- **Removed dependency**: PyInstaller, Python runtime for CLI distribution
- **API**: No backend API changes needed — Rust CLI consumes the same REST + WebSocket endpoints
- **API addition needed**: `/ws/backtest/{run_id}` endpoint for per-run real-time progress push (currently only HTTP polling exists for backtest progress)
- **Files removed** (after transition): `src/tinohelm/cli/`, `tino.spec`, PyInstaller-related build scripts
- **Files added**: `cli/` directory with full Rust project
- **Distribution**: Single binary per platform via `cargo build --release` or GitHub Actions cross-compilation
