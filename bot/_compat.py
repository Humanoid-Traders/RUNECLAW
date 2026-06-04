"""
Legacy compatibility shim — all compat logic now lives in ``bot.compat``.

This file re-exports from the canonical module so any stale imports of
``from bot._compat import ...`` continue to work.  New code should use:

    from bot.compat import BaseModel, Field, HAS_PYDANTIC, UTC
"""
from bot.compat import BaseModel, Field, HAS_PYDANTIC  # noqa: F401
