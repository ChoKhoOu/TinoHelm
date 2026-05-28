# syntax=docker/dockerfile:1.7
#
# TinoHelm container image — a single, multi-stage Dockerfile shared by
# every component pod (strategy, notifier). Pick a component with
# ``--target strategy`` or ``--target notifier``. The CI matrix and
# ``compose.yaml`` both build with explicit targets so the resulting
# images stay single-purpose.
#
# Layer design (the whole point of this file):
#
#   base   — python + apt + uv binary. Almost never invalidated.
#   deps   — ``uv sync --locked --no-install-project`` against pyproject.toml
#            + uv.lock. This is where nautilus_trader (~200 MB) gets
#            installed. As long as the lockfile doesn't change, this layer
#            is reused for every source-only commit, so ``docker pull`` after
#            a code change only transfers the small ``app`` layer.
#   app    — copies the ``tinohelm/`` source tree, then ``uv sync --locked``
#            again. uv sees the dep tree already hydrated in /app/.venv from
#            the previous layer and only installs the local package on top —
#            tiny diff, so this is the layer that gets rebuilt on a typical
#            "edit a .py file, push" cycle.
#
# The two final stages (``strategy`` / ``notifier``) only differ in
# ENTRYPOINT — they reuse the same ``app`` layer underneath, which means the
# Buildx GHA cache is shared between the two CI matrix legs.

ARG PYTHON_VERSION=3.12

# ─── base ───────────────────────────────────────────────────────────────────
# Stable foundation. Touched only when bumping Python, uv, or apt deps.
FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_DEV=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.4 /uv /usr/local/bin/uv

WORKDIR /app

# ─── deps ───────────────────────────────────────────────────────────────────
# Heavy layer: installs nautilus_trader and the rest of the dependency tree
# from the lockfile. Only invalidated when pyproject.toml or uv.lock change.
# README/LICENSE are copied here because hatchling reads them during the
# project metadata pass; without them ``uv sync`` aborts even though we
# pass --no-install-project.
FROM base AS deps

COPY pyproject.toml uv.lock README.md LICENSE ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project

# ─── app ────────────────────────────────────────────────────────────────────
# Light layer: copy our source tree and install just the local package on
# top of the already-resolved dependency tree. The second ``uv sync --locked``
# is bound by the small delta (the local package); the dependency tree it
# reads from /app/.venv was hydrated by the previous layer, so this step
# doesn't redownload nautilus_trader.
FROM deps AS app

COPY tinohelm ./tinohelm
COPY scripts ./scripts

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked

# Make the project venv's Python the default. ``uv sync`` puts everything
# under /app/.venv; pinning it on PATH means ``ENTRYPOINT ["python", ...]``
# (and any ``docker exec`` shell) hits the right interpreter without
# needing ``uv run``.
ENV PATH="/app/.venv/bin:${PATH}" \
    VIRTUAL_ENV=/app/.venv

# ─── per-component leaves ───────────────────────────────────────────────────
# These exist purely to set the right ENTRYPOINT/CMD. They share the same
# parent (``app``) so the strategy and notifier images are byte-identical
# in the layers below this point — a single ``docker pull`` on a dev box
# updating both components only transfers each layer once.

FROM app AS strategy
ENTRYPOINT ["python", "-m", "tinohelm.strategy_runner"]
CMD ["--config", "/app/configs/strategies/example.toml"]

FROM app AS notifier
ENTRYPOINT ["python", "-m", "tinohelm.notifier.runner"]
CMD []
