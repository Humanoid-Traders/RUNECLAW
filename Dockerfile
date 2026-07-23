# =============================================================================
# RUNECLAW v2 Dockerfile — Multi-stage build (bot + API bridge)
# =============================================================================

# ── Stage 1: base (shared deps) ─────────────────────────────────────────
# Pin to digest for reproducible, tamper-evident builds.
# python:3.11-slim @ 2026-05 (Debian bookworm)
FROM python:3.11-slim@sha256:8f64a67710a53a55b8baa3dd37e1a5461e34676deff7a4e6b0e389a8d2a5a4c3 AS base

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

# Stamp the build so /api/version and /status report the real commit even in a
# .git-less image (app/lib/version.js reads this at the repo root).
RUN printf '{"sha":"%s","committed_at":"%s"}\n' "$BUILD_SHA" "$BUILD_DATE" > /app/build-info.json

RUN mkdir -p /app/logs /app/data

# Non-root user (security hardening)
RUN useradd -m -u 1001 runeclaw && chown -R runeclaw:runeclaw /app
USER runeclaw

HEALTHCHECK --interval=15s --timeout=5s --retries=4 \
    CMD curl -sf http://localhost:8000/health || exit 1

# Default: API bridge — override with CMD in compose for the bot service
CMD ["uvicorn", "api_bridge:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
