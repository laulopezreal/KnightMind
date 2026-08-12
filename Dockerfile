# ============================================================================
# KnightMind API – Production Dockerfile
# ============================================================================
# Bundles FastAPI + Stockfish in a single image.
# Build:  docker build -t knightmind-api .
# Run:    docker run --env-file .env.docker -p 8000:8000 knightmind-api
# ============================================================================

FROM python:3.13-slim AS base

# Prevent Python from writing .pyc / buffering stdout
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install Stockfish + system deps for psycopg (libpq)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        stockfish \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Dependencies layer (cached unless pyproject.toml / uv.lock change)
# ---------------------------------------------------------------------------
FROM base AS deps

WORKDIR /app

# uv is used only to turn uv.lock into a pinned requirements file; the install
# itself stays pip, because the runtime stage copies site-packages wholesale
# and `uv sync` would put everything in a .venv instead.
COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /bin/uv

COPY pyproject.toml uv.lock ./

# Install the LOCKED dependency set, not "whatever resolves today".
#
# This build used to run `pip install .`, which re-resolved every dependency at
# build time. Two builds a week apart could ship different versions, and nothing
# recorded which ones went out. That is not hypothetical: FastAPI changed how
# include_router() exposes sub-routers between 0.128 and 0.141, both of which
# satisfy the `fastapi>=0.109.0` floor in pyproject.
#
# --frozen fails the build if uv.lock has drifted from pyproject.toml, so the
# lock cannot silently go stale. --no-emit-project exports dependencies only,
# which also retires the old dummy-package-then-uninstall dance: the project's
# own code is copied into /app by the runtime stage and was never wanted in
# site-packages, where it could shadow the real thing.
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt \
        -o /tmp/requirements.txt && \
    pip install --no-cache-dir -r /tmp/requirements.txt && \
    rm /tmp/requirements.txt

# ---------------------------------------------------------------------------
# Application layer
# ---------------------------------------------------------------------------
FROM base AS runtime

WORKDIR /app

# Copy installed packages from deps stage
COPY --from=deps /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Copy application source
COPY pyproject.toml ./
COPY services/ ./services/
COPY scripts/ ./scripts/

# Stockfish path (apt installs to /usr/games/stockfish)
ENV STOCKFISH_PATH=/usr/games/stockfish

# Run the API as an unprivileged user. Keep /app owned by that user for any
# runtime cache/temp files while preserving root-owned system dependencies.
RUN addgroup --system knightmind \
    && adduser --system --ingroup knightmind --home /app --no-create-home knightmind \
    && chown -R knightmind:knightmind /app
USER knightmind

# Expose API port
EXPOSE 8000

# Health check – hits the existing /ops/health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/ops/health || exit 1

# Run with uvicorn. The worker no longer runs in this process (see the `worker`
# service in docker-compose.yml), so the single worker here is no longer a
# constraint imposed by it, and the rate limiter's shared store
# (KNIGHTMIND_RATE_LIMIT_STORE=postgres) removes the other one.
#
# Still 1 here on purpose. The remaining question is connections, not limits:
# db.py sizes the pool at POOL_SIZE + MAX_OVERFLOW = 50 PER PROCESS, so N
# workers can demand N*50 against a Postgres whose default max_connections is
# 100. Raising this means sizing the pool down per worker or raising the
# server's ceiling -- deliberately, with the arithmetic done.
CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
