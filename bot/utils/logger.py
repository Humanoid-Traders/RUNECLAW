"""
RUNECLAW Audit Logger -- structured JSON logging for every decision.
Three log channels: trade, risk, system.  Every entry is timestamped and
machine-readable so post-mortems are trivial.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)


class _JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "channel": record.name,
            "message": record.getMessage(),
        }
        # Attach structured extras passed via `extra={}`
        for key in ("action", "reasoning", "result", "data"):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        return json.dumps(entry, default=str)


def _build_logger(name: str, filename: str) -> logging.Logger:
    """Create a logger that writes JSON to both file and stderr."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    if not logger.handlers:
        # File handler
        fh = logging.FileHandler(LOG_DIR / filename)
        fh.setFormatter(_JSONFormatter())
        logger.addHandler(fh)

        # Console handler (INFO+)
        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(logging.INFO)
        ch.setFormatter(_JSONFormatter())
        logger.addHandler(ch)

    return logger


# Pre-built channels
trade_log = _build_logger("runeclaw.trade", "trade.jsonl")
risk_log = _build_logger("runeclaw.risk", "risk.jsonl")
system_log = _build_logger("runeclaw.system", "system.jsonl")


def audit(
    channel: logging.Logger,
    message: str,
    *,
    action: str = "",
    reasoning: str = "",
    result: str = "",
    data: Any = None,
    level: int = logging.INFO,
) -> None:
    """
    Write a structured audit entry.

    Example:
        audit(trade_log, "Trade idea generated",
              action="analyze", reasoning="RSI oversold + volume spike",
              result="BUY BTC", data={"confidence": 0.82})
    """
    channel.log(
        level,
        message,
        extra={
            "action": action,
            "reasoning": reasoning,
            "result": result,
            "data": data,
        },
    )
