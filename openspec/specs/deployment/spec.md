# Deployment

## Purpose

Defines the Docker Compose-based deployment model for TinoHelm, including single-command startup, host-mounted volume persistence, health checks, environment configuration, and automatic database migrations.

## Requirements

### Requirement: Docker Compose single-command deployment
The system SHALL be deployable with a single `docker compose up` command, starting all required services.

#### Scenario: First-time deployment
- **WHEN** user runs `docker compose up -d` in the project root
- **THEN** system starts PostgreSQL, Redis, API server, and web server containers, runs database migrations, and the platform is accessible at `http://localhost:3000`

#### Scenario: Container restart persistence
- **WHEN** containers are stopped and restarted
- **THEN** all data persists because PostgreSQL, Redis, Parquet catalog, artifacts, strategies, and logs are host-mounted volumes

### Requirement: Host-mounted volume persistence
The system SHALL mount all persistent data to host directories for data durability and direct access.

#### Scenario: Volume mapping
- **WHEN** docker-compose is configured
- **THEN** the following host paths are mounted:
  - `./data/postgres` -> PostgreSQL data directory
  - `./data/redis` -> Redis AOF/RDB persistence
  - `./data/catalog` -> Parquet historical data files
  - `./data/artifacts` -> Backtest result JSON files
  - `./strategies` -> Strategy Python files (readable/writable by host and container)
  - `./logs` -> Application log files
  - `./config` -> Configuration YAML files

### Requirement: Docker health checks
The system SHALL include Docker health checks for all services.

#### Scenario: API health check
- **WHEN** Docker checks container health
- **THEN** API container health check calls `GET /api/health` every 30 seconds with 5-second timeout and 3 retries

#### Scenario: Database health check
- **WHEN** Docker checks PostgreSQL health
- **THEN** PostgreSQL container runs `pg_isready` health check

#### Scenario: Redis health check
- **WHEN** Docker checks Redis health
- **THEN** Redis container runs `redis-cli ping` health check

### Requirement: Environment configuration via .env
The system SHALL read sensitive configuration from a `.env` file at the project root.

#### Scenario: Binance API keys from environment
- **WHEN** the API server starts
- **THEN** system reads `BINANCE_API_KEY` and `BINANCE_API_SECRET` from environment variables (sourced from `.env` by Docker Compose)

#### Scenario: Missing required env vars
- **WHEN** required environment variables are not set and live trading is requested
- **THEN** system returns a clear error indicating which variables are missing

### Requirement: Database migration on startup
The system SHALL automatically run database migrations when the API container starts.

#### Scenario: Fresh database
- **WHEN** API container starts with an empty PostgreSQL database
- **THEN** Alembic migrations run automatically, creating all required tables

#### Scenario: Existing database with pending migrations
- **WHEN** API container starts with a database that has pending migrations
- **THEN** Alembic applies pending migrations without data loss
