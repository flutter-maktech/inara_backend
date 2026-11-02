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
    PRISMA_BINARY_CACHE_DIR=/prisma-binaries \
    PRISMA_USE_GLOBAL_NODE=true
 
# Build-time system dependencies
# Node.js + npm: prevent Prisma Python from using nodeenv
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    libatomic1 \
    nodejs \
    npm \
&& node --version \
&& npm --version \
&& rm -rf /var/lib/apt/lists/*
 
WORKDIR /app
 
# Isolated virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
 
# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
 
# Copy Prisma schema and generate client + engine binaries
COPY prisma/ ./prisma/
RUN prisma generate
 
# ── Stage 2: Production ───────────────────────────────────
FROM python:3.11-slim AS production
 
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PRISMA_BINARY_CACHE_DIR=/prisma-binaries \
    PRISMA_USE_GLOBAL_NODE=true \
    HOME=/home/appuser
 
# Runtime dependencies
# Node.js + npm are required by Prisma Python CLI.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    libatomic1 \
    nodejs \
    npm \
&& node --version \
&& npm --version \
&& rm -rf /var/lib/apt/lists/*
 
# Create non-root application user
RUN groupadd --gid 1001 appuser \
&& useradd --uid 1001 --gid appuser \
       --shell /bin/bash --create-home appuser
 
WORKDIR /app
 
# Copy Python virtual environment
COPY --from=builder /opt/venv /opt/venv
 
# Copy Prisma schema and migrations
COPY --from=builder /app/prisma ./prisma
 
# Copy generated Prisma engine binaries
COPY --from=builder --chown=appuser:appuser \
    /prisma-binaries /prisma-binaries
 
# Copy application source
COPY --chown=appuser:appuser app ./app
 
# Ensure application directory permissions
RUN chown -R appuser:appuser /app \
&& chown -R appuser:appuser /prisma-binaries \
&& chown -R appuser:appuser /home/appuser
 
# Run Prisma CLI initialization as the actual runtime user.
# This prevents runtime attempts to initialize the CLI
# inside an inaccessible or different user's cache directory.
USER appuser
 
RUN node --version \
&& npm --version \
&& prisma version
 
EXPOSE 8000
 
# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1
 
# Apply database migrations and start FastAPI
CMD ["sh", "-c", "prisma migrate deploy && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2"]