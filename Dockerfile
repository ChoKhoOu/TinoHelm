# syntax=docker/dockerfile:1
# TinoHelm API Server
# Optimizations:
#   P0: No Rust toolchain — nautilus_trader 1.224.0 ships manylinux wheels for aarch64 + x86_64
#   P1: Multi-stage build — build-essential stays in deps stage, not in final image
#   P2: Deps/source layer separation — source changes do NOT invalidate the wheel cache layer
#   P3: BuildKit cache mounts — pip cache persists across rebuilds even after cache-miss

# ─── Stage 1: deps ────────────────────────────────────────────────────────────
# Builds all dependency wheels. build-essential stays here; never enters runtime.
FROM python:3.12-slim AS deps

# Build tools needed by some transitive C-extension deps (e.g. cryptography, psutil)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# P2: Copy only the manifest — source code changes won't bust this layer
COPY pyproject.toml ./

# Extract dependency list from pyproject.toml with stdlib tomllib (Python 3.11+)
# Also append psycopg2-binary which is a runtime-only dep not in pyproject.toml
RUN python - <<'EOF'
import tomllib, pathlib
data = tomllib.loads(pathlib.Path("pyproject.toml").read_text())
deps = data["project"]["dependencies"]
pathlib.Path("requirements.txt").write_text("\n".join(deps) + "\npsycopg2-binary\n")
EOF

# P3: BuildKit cache mount keeps the pip HTTP cache across all rebuilds
# P1: Wheels are collected here; only the .whl files are passed to the runtime stage
RUN --mount=type=cache,target=/root/.cache/pip \
    pip wheel --wheel-dir=/wheels -r requirements.txt

# ─── Stage 2: runtime ─────────────────────────────────────────────────────────
# Lean final image — no compiler, no Rust, no git.
FROM python:3.12-slim AS runtime

# Only curl is needed for the HEALTHCHECK
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# P1+P2: Install pre-built wheels (stable layer, bind-mount avoids 224MB COPY waste)
RUN --mount=type=bind,from=deps,source=/wheels,target=/wheels \
    pip install --no-cache-dir --no-index /wheels/*

# P2: Copy source AFTER deps are installed — source edits only invalidate from here down
COPY pyproject.toml ./
COPY alembic.ini ./
COPY src/ ./src/

# P1: Regular (non-editable) install of the local package with no extra dep resolution
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir --no-deps .

# Runtime directories
RUN mkdir -p /app/tino/data/catalog /app/tino/data/artifacts /app/logs /app/strategies

# Copy config
COPY config/ ./config/

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Non-root user
RUN groupadd -r tino && useradd -r -g tino -d /app tino \
    && chown -R tino:tino /app
USER tino

CMD ["sh", "-c", "alembic upgrade head && uvicorn tinohelm.api.app:app --host 0.0.0.0 --port 8000"]
