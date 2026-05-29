# =============================================================================
# RUNECLAW Dockerfile
# =============================================================================
FROM python:3.11-slim

WORKDIR /app

# Install OS-level dependencies (none required for now, but layer is cached)
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer caching)
COPY bot/requirements.txt /app/bot/requirements.txt
RUN pip install --no-cache-dir -r bot/requirements.txt

# Copy application code
COPY bot/ /app/bot/
COPY tests/ /app/tests/
COPY backtest_audit.py /app/backtest_audit.py

# Create logs directory
RUN mkdir -p /app/logs

# Non-root user for security
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

CMD ["python", "-m", "bot.main", "--mode", "telegram"]
