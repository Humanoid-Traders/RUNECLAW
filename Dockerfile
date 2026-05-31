# =============================================================================
# RUNECLAW Dockerfile
# =============================================================================
FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies first (layer caching)
COPY bot/requirements.txt /app/bot/requirements.txt
RUN pip install --no-cache-dir -r bot/requirements.txt

# Copy application code
COPY bot/ /app/bot/
COPY tests/ /app/tests/

# Copy optional scripts (may not exist in all builds)
COPY backtest_audit.p[y] /app/

# Create logs and data directories
RUN mkdir -p /app/logs /app/data

# Non-root user for security
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

CMD ["python", "-m", "bot.main", "--mode", "telegram"]
