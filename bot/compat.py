"""
Compatibility shim — centralises all version/library fallbacks.

1. **datetime.UTC** (Python 3.11+): re-exported as ``UTC`` so every module
   can write ``from bot.compat import UTC`` regardless of interpreter version.

2. **Pydantic**: re-exports ``BaseModel`` and ``Field``.  When Pydantic is not
   installed (e.g. in a minimal test environment) a lightweight stdlib
   dataclass-based fallback is provided so self-tests still run.

This is the **single** compatibility module for the RUNECLAW codebase.
``bot/_compat.py`` (if present) exists only as a legacy re-export pointing here.
"""

from datetime import timezone

# ── datetime.UTC (Python 3.10 fallback) ─────────────────────────────
try:
    from datetime import UTC  # Python 3.11+
except ImportError:
    UTC = timezone.utc  # Python 3.10 fallback

# ── Pydantic (optional-dependency fallback) ──────────────────────────
try:
    from pydantic import BaseModel, Field
    HAS_PYDANTIC = True
except ImportError:
    HAS_PYDANTIC = False

    class BaseModel:  # type: ignore[no-redef]
        """Minimal fallback — not a real Pydantic model."""
        def model_dump(self):
            return {k: v for k, v in self.__dict__.items() if not k.startswith('_')}

    def Field(default=None, **kwargs):  # type: ignore[no-redef]
        return default
