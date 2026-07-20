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
# Dependencies layer (cached unless pyproject.toml changes)
# ---------------------------------------------------------------------------
FROM base AS deps

WORKDIR /app

COPY pyproject.toml ./
# Create minimal package structure so pip install -e works
RUN mkdir -p services/api services/ingest scripts && \
    touch services/__init__.py services/api/__init__.py services/ingest/__init__.py scripts/__init__.py

# Install to pull in dependencies, then uninstall the "knightmind" package
# itself: it was built from the dummy __init__.py stubs above, and leaving it
# in site-packages could shadow the real services/ and scripts/ code copied
# into /app in the runtime stage. Dependencies stay installed.
RUN pip install --no-cache-dir . && \
    pip uninstall -y knightmind

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

# Least privilege: run as a non-root user instead of root, so a code-exec or
# path-traversal bug (or a compromised Stockfish subprocess) has a limited
# blast radius inside the container. Stockfish at /usr/games/stockfish is
# world-executable by default; /app is chowned so the app can read its source.
RUN useradd --system --uid 10001 --no-create-home appuser \
    && chown -R appuser:appuser /app
USER appuser

# Expose API port
EXPOSE 8000

# Health check – hits the existing /ops/health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/ops/health || exit 1

# Run with uvicorn. WEB_CONCURRENCY=1 required for in-process worker.
CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
