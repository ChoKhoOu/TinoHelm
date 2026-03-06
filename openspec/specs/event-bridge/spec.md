# Event Bridge

## Purpose

Bridges NautilusTrader's internal event system to the external world via Redis PubSub, enabling real-time event streaming from TradingNode subprocesses to the FastAPI server and WebSocket clients.

## Requirements

### Requirement: BridgeActor publishes NT events to Redis
The system SHALL include a custom NT Actor (BridgeActor) that runs inside each TradingNode subprocess and publishes key trading events to Redis PubSub channels.

#### Scenario: Order fill event
- **WHEN** an order is filled inside TradingNode
- **THEN** BridgeActor serializes the OrderFilled event and publishes to `tino:{node_type}:fills`

#### Scenario: Position change event
- **WHEN** a position is opened, changed, or closed inside TradingNode
- **THEN** BridgeActor serializes the position event and publishes to `tino:{node_type}:positions`

#### Scenario: Bar data event
- **WHEN** a new bar is received by TradingNode
- **THEN** BridgeActor serializes the bar and publishes to `tino:{node_type}:bars`

#### Scenario: Order status event
- **WHEN** an order status changes (accepted, rejected, canceled, expired)
- **THEN** BridgeActor serializes the event and publishes to `tino:{node_type}:orders`

#### Scenario: Risk event
- **WHEN** RiskEngine triggers a state change or rejects an order
- **THEN** BridgeActor publishes the event to `tino:{node_type}:risk`

### Requirement: Heartbeat publishing
The system SHALL publish periodic heartbeat messages from each TradingNode subprocess via Redis.

#### Scenario: Regular heartbeat
- **WHEN** TradingNode is running
- **THEN** BridgeActor sets Redis key `tino:heartbeat:{node_type}` every 5 seconds with JSON payload containing timestamp, trading state, active strategy count, and open position count, with 15-second TTL

### Requirement: FastAPI WebSocket relay
The system SHALL subscribe to Redis PubSub channels and relay events to connected WebSocket clients.

#### Scenario: Client subscribes to live events
- **WHEN** a WebSocket client connects and subscribes to event types (fills, positions, bars, orders)
- **THEN** API server's Redis subscriber forwards matching PubSub messages to the client in real-time as JSON

#### Scenario: Client disconnects
- **WHEN** a WebSocket client disconnects
- **THEN** server cleans up the subscription without affecting other clients or the PubSub subscription
