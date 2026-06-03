# =============================================================================
# RUNECLAW v2 Dockerfile — Multi-stage build (bot + API bridge)
# =============================================================================

# ── Stage 1: base (shared deps) ─────────────────────────────────────────
FROM python:3.11-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libssl-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY bot/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    fastapi>=0.110 "uvicorn[standard]>=0.29"

# ── Stage 2: production image ───────────────────────────────────────────
FROM base AS production

ARG BUILD_SHA=dev
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.revision="${BUILD_SHA}"
LABEL org.opencontainers.image.created="${BUILD_DATE}"
LABEL maintainer="Humanoid Traders"

WORKDIR /app

COPY . .

RUN mkdir -p /app/logs /app/data

# Non-root user (security hardening)
RUN useradd -m -u 1001 runeclaw && chown -R runeclaw:runeclaw /app
USER runeclaw

HEALTHCHECK --interval=15s --timeout=5s --retries=4 \
    CMD curl -sf http://localhost:8000/health || exit 1

# Default: API bridge — override with CMD in compose for the bot service
CMD ["uvicorn", "api_bridge:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
