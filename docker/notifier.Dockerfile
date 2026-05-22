FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.4 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml ./
COPY tinohelm ./tinohelm

RUN uv pip install --system --no-cache .

ENTRYPOINT ["python", "-m", "tinohelm.notifier.runner"]
CMD ["--config", "/app/configs/notifier.toml"]
