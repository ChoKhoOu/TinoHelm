# TinoHelm API Server
FROM python:3.12-slim AS base

# System dependencies for nautilus_trader
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Rust toolchain (required for nautilus_trader)
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

WORKDIR /app

# Copy project files
COPY pyproject.toml ./
COPY alembic.ini ./
COPY src/ ./src/

# Install Python package
RUN pip install --no-cache-dir -e "." && pip install --no-cache-dir psycopg2-binary

# Create directories
RUN mkdir -p /app/tino/data/catalog /app/tino/data/artifacts /app/logs /app/strategies

# Copy config
COPY config/ ./config/

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["sh", "-c", "alembic upgrade head || echo 'WARNING: Migration failed, continuing...' && uvicorn tinohelm.api.app:app --host 0.0.0.0 --port 8000"]
