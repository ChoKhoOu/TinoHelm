# Watchlist API

## Purpose

Provides REST API endpoints for managing a user's instrument watchlist, including CRUD operations and persistent storage via the `watchlist_items` database table.

## Requirements

### Requirement: Watchlist CRUD endpoints
The system SHALL provide REST endpoints for managing a user's instrument watchlist with persistent storage.

#### Scenario: List watchlist items
- **WHEN** client requests `GET /api/watchlist`
- **THEN** system returns all watchlist items with instrument_id, source, and created_at fields

#### Scenario: Add watchlist item
- **WHEN** client POSTs to `POST /api/watchlist` with `{instrument_id, source}`
- **THEN** system persists the item and returns the created watchlist entry with id

#### Scenario: Add duplicate watchlist item
- **WHEN** client POSTs to `POST /api/watchlist` with an instrument_id that already exists
- **THEN** system returns 409 Conflict

#### Scenario: Delete watchlist item
- **WHEN** client sends `DELETE /api/watchlist/{id}`
- **THEN** system removes the item and returns 204 No Content

#### Scenario: Delete non-existent watchlist item
- **WHEN** client sends `DELETE /api/watchlist/{id}` with an invalid id
- **THEN** system returns 404 Not Found

### Requirement: Watchlist database model
The system SHALL store watchlist items in a `watchlist_items` database table.

#### Scenario: Watchlist table schema
- **WHEN** the database migration runs
- **THEN** a `watchlist_items` table exists with columns: id (PK), instrument_id (varchar, unique), source (varchar), created_at (timestamp with timezone)
