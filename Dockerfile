# ============================================================
# PRODUCTION DOCKERFILE — Inara Backend
# FastAPI + Prisma (Python client) + PostgreSQL
# Multi-stage build: lean, secure, production-ready
# ============================================================

# ── Stage 1: Builder ─────────────────────────────────────
FROM python:3.11-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PRISMA_BINARY_CACHE_DIR=/prisma-binaries

# Build-time system dependencies
# libatomic1 is required by the Prisma query engine binary (Node.js runtime dep)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    libatomic1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Isolated virtual environment for clean copy into production stage
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies (layer-cached — only reruns if requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Prisma schema and generate the Python client + download engine binaries
COPY prisma/ ./prisma/
RUN prisma generate

# ── Stage 2: Production ───────────────────────────────────
FROM python:3.11-slim AS production

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PRISMA_BINARY_CACHE_DIR=/prisma-binaries

# Runtime-only system dependencies
# libatomic1  — required by Prisma query engine (Node.js) at runtime
# libpq5      — PostgreSQL client library
# curl        — used by Docker HEALTHCHECK
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    libatomic1 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user — security best practice
RUN groupadd --gid 1001 appuser \
    && useradd --uid 1001 --gid appuser --shell /bin/bash --create-home appuser

WORKDIR /app

# Copy venv + generated Prisma client from builder
COPY --from=builder /opt/venv /opt/venv

# Copy Prisma schema + migrations folder (needed by prisma migrate deploy at runtime)
COPY --from=builder /app/prisma ./prisma

# Copy Prisma engine binaries — owned by appuser, no /root/.cache permission issues
COPY --from=builder --chown=appuser:appuser /prisma-binaries /prisma-binaries

# Copy application source code
COPY --chown=appuser:appuser app ./app

# Ensure /app is fully owned by appuser
RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# Health check — /health endpoint must respond 200 before container is marked healthy
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["sh", "-c", "prisma migrate deploy && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2"]