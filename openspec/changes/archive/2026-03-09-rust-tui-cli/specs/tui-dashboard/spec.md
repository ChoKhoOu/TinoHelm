## ADDED Requirements

### Requirement: TUI entry point
The system SHALL enter full-screen interactive TUI mode when invoked with no subcommand (`tino`) or with explicit `tino ui`. The TUI SHALL take over the terminal and restore it cleanly on exit.

#### Scenario: Enter TUI with no args
- **WHEN** user runs `tino` without any subcommand
- **THEN** system enters full-screen TUI showing the backtest list view

#### Scenario: Enter TUI explicitly
- **WHEN** user runs `tino ui`
- **THEN** system enters full-screen TUI showing the backtest list view

#### Scenario: Clean exit
- **WHEN** user presses `q` in TUI
- **THEN** system restores the terminal to its original state and exits cleanly

### Requirement: Backtest list view
The TUI SHALL display a navigable list of backtests with key columns: ID, Strategy, Symbol, Interval, Status, PnL, Sharpe, WinRate. The list SHALL be sorted by creation time (newest first) and support keyboard navigation.

#### Scenario: Navigate backtest list
- **WHEN** TUI is on backtest list view
- **THEN** user can navigate with `j`/`k` (or arrow keys) to highlight different rows

#### Scenario: Refresh backtest list
- **WHEN** user presses `r` on backtest list view
- **THEN** system fetches the latest backtest list from the API and updates the display

### Requirement: Backtest detail view
The TUI SHALL display detailed results for a selected backtest including: full statistics table, equity curve (rendered as Sparkline or Braille canvas), trade summary, and per-instrument breakdown.

#### Scenario: Open detail from list
- **WHEN** user presses `Enter` on a highlighted backtest row
- **THEN** TUI transitions to the detail view for that backtest

#### Scenario: Return to list
- **WHEN** user presses `Esc` or `Backspace` on the detail view
- **THEN** TUI returns to the backtest list view with previous selection preserved

### Requirement: Real-time backtest progress
The TUI SHALL display a live progress bar and streaming statistics for running backtests, updated via WebSocket push.

#### Scenario: Monitor running backtest
- **WHEN** a backtest is in "running" status and TUI is displaying the list
- **THEN** the progress percentage and live PnL update in real-time without manual refresh

#### Scenario: Backtest completes during monitoring
- **WHEN** a running backtest transitions to "completed" while TUI is open
- **THEN** the status updates immediately, and the full result becomes accessible

### Requirement: Run backtest from TUI
The TUI SHALL provide a way to submit a new backtest run without leaving the TUI.

#### Scenario: Submit backtest
- **WHEN** user presses `n` (new) on the backtest list view
- **THEN** TUI shows a form to input strategy, symbol, interval, date range, then submits via API

### Requirement: Strategy browser
The TUI SHALL provide a view to browse registered strategies with their details.

#### Scenario: Switch to strategy view
- **WHEN** user presses `2` or navigates to strategy tab
- **THEN** TUI shows a list of strategies with Name, Type, Class, and symbol count

### Requirement: Node status view
The TUI SHALL display the status of sandbox/live trading nodes including heartbeat state.

#### Scenario: View node status
- **WHEN** user navigates to node status tab
- **THEN** TUI shows node type, running/stopped state, and last heartbeat timestamp

### Requirement: Keyboard-driven navigation
The TUI SHALL be fully operable via keyboard. A key hint bar at the bottom SHALL show available actions for the current view.

#### Scenario: Key hints displayed
- **WHEN** TUI is on any view
- **THEN** the bottom bar shows context-sensitive key bindings (e.g., `[j/k] navigate  [Enter] detail  [r] refresh  [q] quit`)

#### Scenario: Tab switching
- **WHEN** user presses `1`/`2`/`3`
- **THEN** TUI switches between Backtests / Strategies / Nodes tabs

### Requirement: Error display in TUI
The TUI SHALL NOT crash on API errors. Errors SHALL be displayed as a dismissible banner at the bottom of the screen.

#### Scenario: API connection lost
- **WHEN** API becomes unreachable while TUI is running
- **THEN** TUI displays a warning banner "API connection lost, reconnecting..." and attempts auto-reconnect

#### Scenario: API returns error
- **WHEN** an API call returns a 4xx/5xx error
- **THEN** TUI displays the error message in a banner for 5 seconds, then auto-dismisses

### Requirement: Terminal resize handling
The TUI SHALL re-render correctly when the terminal window is resized.

#### Scenario: Terminal resized
- **WHEN** user resizes the terminal window
- **THEN** TUI layout adapts to the new dimensions without visual artifacts
