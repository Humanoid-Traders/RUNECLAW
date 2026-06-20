"""
RUNECLAW Audit Logger -- structured JSON logging for every decision.
Three log channels: trade, risk, system.  Every entry is timestamped and
machine-readable so post-mortems are trivial.

C3 FIX: All log output is run through a redaction layer that scrubs
sensitive values (API keys, secrets, tokens, passphrases) before writing.

F-08 FIX: Hash chain -- each JSON line includes prev_hash = sha256(previous line),
making the audit trail tamper-evident and verifiable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import logging.handlers
import os
import re
import sys
from datetime import datetime
from bot.compat import UTC
from pathlib import Path
from typing import Any


LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# C3 FIX: Sensitive-data redaction
# ---------------------------------------------------------------------------

# Patterns that indicate sensitive keys in dicts / JSON
_SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|api[_-]?secret|passphrase|password|token|secret|credential|auth)",
    re.IGNORECASE,
)

# Inline patterns for strings (e.g. "BITGET_API_KEY=abc123" in tracebacks)
_INLINE_SECRET_RE = re.compile(
    r"(api[_-]?key|api[_-]?secret|passphrase|password|token|secret|credential)"
    r"\s*[=:]\s*['\"]?([^\s'\"]{4,})",
    re.IGNORECASE,
)

_REDACTED = "***REDACTED***"


def _redact_dict(obj: Any, depth: int = 0) -> Any:
    """Recursively scrub sensitive values from dicts/lists before logging."""
    if depth > 10:
        return obj
    if isinstance(obj, dict):
        cleaned: dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and _SENSITIVE_KEY_RE.search(k):
                cleaned[k] = _REDACTED
            else:
                cleaned[k] = _redact_dict(v, depth + 1)
        return cleaned
    if isinstance(obj, (list, tuple)):
        return [_redact_dict(item, depth + 1) for item in obj]
    if isinstance(obj, str):
        return _redact_string(obj)
    return obj


def _redact_string(s: str) -> str:
    """Scrub inline secrets from string values (tracebacks, error messages)."""
    return _INLINE_SECRET_RE.sub(r"\1=***REDACTED***", s)


class _JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON line with redaction and hash chain.

    F-08 FIX: Each entry includes ``prev_hash`` -- the SHA-256 of the previous
    JSON line emitted by this formatter instance.  This creates a verifiable
    chain: mutating or deleting any line breaks the chain from that point on.
    """

    def __init__(self) -> None:
        super().__init__()
        self._prev_hash: str = "GENESIS"

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "channel": record.name,
            "message": _redact_string(record.getMessage()),
            "prev_hash": self._prev_hash,
        }
        # Attach structured extras passed via `extra={}`
        for key in ("action", "reasoning", "result", "data"):
            if hasattr(record, key):
                val = getattr(record, key)
                entry[key] = _redact_dict(val) if isinstance(val, (dict, list)) else (
                    _redact_string(val) if isinstance(val, str) else val
                )
        line = json.dumps(entry, default=str)
        self._prev_hash = hashlib.sha256(line.encode()).hexdigest()
        return line


def _build_logger(name: str, filename: str) -> logging.Logger:
    """Create a logger that writes JSON to both file and stderr."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    if not logger.handlers:
        # File handler with rotation (10MB per file, keep 5 backups)
        fh = logging.handlers.RotatingFileHandler(
            LOG_DIR / filename, maxBytes=10 * 1024 * 1024, backupCount=5,
        )
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
scan_log = _build_logger("runeclaw.scan", "scan.jsonl")


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
