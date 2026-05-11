# syntax=docker/dockerfile:1
# TinoHelm API Server
# Optimizations:
#   P0: No Rust toolchain — nautilus_trader 1.225.0 ships manylinux wheels for aarch64 + x86_64
#   P1: Multi-stage build — build-essential stays in deps stage, not in final image
#   P2: Deps/source layer separation — source changes do NOT invalidate the lock-driven sync layer
#   P3: BuildKit cache mounts — uv cache persists across rebuilds even after cache-miss

# ─── Stage 1: deps ────────────────────────────────────────────────────────────
# Sync all Python dependencies into a project-local virtualenv.
FROM python:3.12-slim AS deps

# Build tools needed by some transitive C-extension deps (e.g. cryptography, psutil)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /uvx /bin/

WORKDIR /build
ENV UV_LINK_MODE=copy
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

# P2: Copy only dependency manifests first — source code changes won't bust this layer
COPY pyproject.toml ./
COPY uv.lock ./

# P3: BuildKit cache mount keeps uv's cache across rebuilds
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --extra optimize

# ─── Stage 2: runtime ─────────────────────────────────────────────────────────
# Lean final image — no compiler, no Rust, no git.
FROM python:3.12-slim AS runtime

# Only curl is needed for the HEALTHCHECK
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PATH="/opt/venv/bin:$PATH"
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

# P1+P2: Copy the pre-synced virtualenv from the deps stage.
COPY --from=deps /opt/venv /opt/venv

# P2: Copy source AFTER deps are installed — source edits only invalidate from here down
COPY pyproject.toml ./
COPY uv.lock ./
COPY alembic.ini ./
COPY src/ ./src/

# P1: Install the local package into the synced environment without re-resolving dependencies.
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /uvx /bin/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable --extra optimize

# Runtime directories
RUN mkdir -p /app/tino/data/catalog /app/tino/data/artifacts /app/logs /app/strategies

# Copy config
COPY config/ ./config/

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Non-root user.
# UID/GID default to 1000 to match the typical Linux first-login user so bind-mounted
# ~/.tino/* directories remain writable inside the container. Override at build time
# with `--build-arg UID=$(id -u) --build-arg GID=$(id -g)` when the host user differs.
ARG UID=1000
ARG GID=1000
RUN groupadd -g ${GID} tino && useradd --uid ${UID} --gid ${GID} -d /app -s /usr/sbin/nologin tino \
    && chown -R tino:tino /app
USER tino

CMD ["sh", "-c", "uvicorn tinohelm.api.app:app --host 0.0.0.0 --port 8000"]
