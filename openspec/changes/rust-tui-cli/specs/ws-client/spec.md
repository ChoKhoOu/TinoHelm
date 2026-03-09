## ADDED Requirements

### Requirement: Single unified WebSocket endpoint
The backend SHALL provide a single WebSocket endpoint (`/ws/events`) that pushes ALL platform events as type-tagged JSON messages. Each message SHALL have a `type` field using dot-notation prefix (e.g., `backtest.progress`, `node.heartbeat`, `system.error`).

#### Scenario: Client connects to unified endpoint
- **WHEN** TUI establishes a WebSocket connection to `/ws/events`
- **THEN** system begins pushing all relevant events through this single connection

#### Scenario: Multiple event types on one connection
- **WHEN** a backtest is running and a node is active simultaneously
- **THEN** both `backtest.*` and `node.*` events arrive on the same WebSocket connection

### Requirement: Client-side message bus
The Rust TUI SHALL implement a MessageBus that receives all WebSocket messages and dispatches them to subscriber components based on event type prefix matching. Components SHALL subscribe only to event types they care about.

#### Scenario: Component subscribes to backtest events
- **WHEN** BacktestListView subscribes to `backtest.*` on the message bus
- **THEN** it receives `backtest.progress`, `backtest.stats`, and `backtest.completed` messages but NOT `node.*` or `system.*` messages

#### Scenario: Multiple subscribers for same event
- **WHEN** both BacktestListView and BacktestDetailView subscribe to `backtest.completed`
- **THEN** both components receive the completion event and update independently

### Requirement: WebSocket connection management
The Rust client SHALL maintain a single WebSocket connection with auto-reconnect on disconnect using exponential backoff (1s, 2s, 4s, max 30s). Connection state SHALL be visible in the TUI.

#### Scenario: Initial connection
- **WHEN** TUI starts
- **THEN** system establishes a WebSocket connection to `/ws/events` at the configured API URL

#### Scenario: Connection lost and recovered
- **WHEN** WebSocket connection drops (network issue, server restart)
- **THEN** system automatically reconnects with exponential backoff and resumes receiving events

#### Scenario: Connection state indicator
- **WHEN** WebSocket is connected
- **THEN** TUI shows a green dot indicator; when disconnected shows a red dot with retry countdown

### Requirement: Backtest event messages
The backend SHALL push backtest lifecycle events through the unified WebSocket with the following message types.

#### Scenario: Progress update
- **WHEN** a backtest worker updates progress in Redis
- **THEN** the WebSocket pushes `{"type": "backtest.progress", "run_id": "<id>", "pct": <int>, "elapsed_secs": <float>}`

#### Scenario: Stats update
- **WHEN** a backtest produces intermediate results
- **THEN** the WebSocket pushes `{"type": "backtest.stats", "run_id": "<id>", "trades": <int>, "pnl": <float>, "win_rate": <float>}`

#### Scenario: Completion notification
- **WHEN** a backtest finishes (completed, failed, or cancelled)
- **THEN** the WebSocket pushes `{"type": "backtest.completed", "run_id": "<id>", "status": "<status>", "summary": {...}}`

### Requirement: Node heartbeat messages
The backend SHALL push node heartbeat events through the unified WebSocket.

#### Scenario: Node comes online
- **WHEN** a sandbox or live node sends a heartbeat
- **THEN** the WebSocket pushes `{"type": "node.heartbeat", "node_type": "sandbox", "ts": "<iso8601>"}`

#### Scenario: Node goes offline
- **WHEN** no heartbeat is received for a node within 30 seconds
- **THEN** the TUI client detects the timeout locally and updates node status to "stopped"

### Requirement: Message format forward compatibility
The client SHALL handle unknown and malformed messages gracefully to support future event types without client updates.

#### Scenario: Unknown message type
- **WHEN** a WebSocket message has an unrecognized `type` field (e.g., `analytics.report`)
- **THEN** the message bus logs a debug message and drops it (no crash, no error)

#### Scenario: Malformed message
- **WHEN** a WebSocket message is not valid JSON
- **THEN** the client logs a warning and continues (does not crash or disconnect)

### Requirement: OpenAPI schema type contract
The FastAPI backend SHALL expose `/openapi.json` with complete request/response schemas. The Rust client's serde structs SHALL be validated against this schema in CI.

#### Scenario: Schema matches
- **WHEN** CI runs the type validation step
- **THEN** all Rust response structs are verified to match the corresponding OpenAPI schema definitions

#### Scenario: Schema drift detected
- **WHEN** a Python Pydantic model is changed without updating the corresponding Rust struct
- **THEN** CI fails with a clear error indicating which types have drifted
