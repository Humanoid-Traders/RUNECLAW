"""Free-tier chat quota — N questions/day per user, then an upgrade prompt.

Free (basic-tier) web users get a small number of AI chat questions per UTC day
on the operator-funded model (xAI Grok). Paid tiers (pro/elite) and the admin are
exempt. This is the spend fence around the operator's prepaid Grok budget: at $20
of grok-4.3 (~$1.25/$2.50 per MTok ≈ 16M in / 8M out), an uncapped free chat would
drain it in a day — the per-user daily cap bounds it instead.

State is a tiny JSON file ({uid: {"day": "YYYY-MM-DD", "n": int}}), written
atomically. Counting is per UTC day and resets automatically when the day rolls.
Deliberately simple and dependency-free — this is a soft product limit, not a
security control, so an approximate count that never crashes chat is the goal.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Free users get this many AI questions per UTC day. Operator-overridable so the
# limit can be tuned to the funded budget without a code change.
DEFAULT_FREE_DAILY_LIMIT = 5

# Tiers that are NEVER quota-limited (paid + operator).
_EXEMPT_TIERS = frozenset({"pro", "elite", "admin", "premium"})

_STORE_PATH = Path(os.getenv("FREE_CHAT_QUOTA_PATH", "data/free_chat_quota.json"))
_LOCK = threading.Lock()


def free_daily_limit() -> int:
    """The per-day free question limit (env FREE_CHAT_DAILY_LIMIT, default 5)."""
    try:
        n = int(os.getenv("FREE_CHAT_DAILY_LIMIT", str(DEFAULT_FREE_DAILY_LIMIT)))
        return n if n > 0 else DEFAULT_FREE_DAILY_LIMIT
    except (TypeError, ValueError):
        return DEFAULT_FREE_DAILY_LIMIT


def is_quota_exempt(tier: Optional[str]) -> bool:
    """Paid tiers and admin are never limited."""
    return str(tier or "").strip().lower() in _EXEMPT_TIERS


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load() -> dict:
    try:
        if _STORE_PATH.exists():
            with open(_STORE_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _save(data: dict) -> None:
    try:
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STORE_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, _STORE_PATH)
    except Exception:
        pass                                    # never let a write error break chat


def _entry_used(data: dict, uid: str, day: str) -> int:
    e = data.get(uid)
    if isinstance(e, dict) and e.get("day") == day:
        try:
            return max(0, int(e.get("n", 0)))
        except (TypeError, ValueError):
            return 0
    return 0                                     # missing / stale day → 0 used today


def status(uid: str, tier: Optional[str] = None) -> dict:
    """Peek the caller's quota WITHOUT consuming. Returns
    ``{exempt, limit, used, remaining}``. Exempt users report a huge remaining."""
    if is_quota_exempt(tier):
        return {"exempt": True, "limit": None, "used": 0, "remaining": None}
    limit = free_daily_limit()
    with _LOCK:
        used = _entry_used(_load(), str(uid), _today())
    return {"exempt": False, "limit": limit, "used": used,
            "remaining": max(0, limit - used)}


def consume(uid: str, tier: Optional[str] = None) -> dict:
    """Try to spend one free question. Returns
    ``{allowed, exempt, limit, used, remaining}``. When not allowed (limit hit),
    nothing is incremented and ``allowed`` is False — the caller shows the upgrade
    prompt instead of calling the LLM. Exempt callers are always allowed."""
    if is_quota_exempt(tier):
        return {"allowed": True, "exempt": True, "limit": None,
                "used": 0, "remaining": None}
    limit = free_daily_limit()
    day = _today()
    key = str(uid)
    with _LOCK:
        data = _load()
        used = _entry_used(data, key, day)
        if used >= limit:
            return {"allowed": False, "exempt": False, "limit": limit,
                    "used": used, "remaining": 0}
        data[key] = {"day": day, "n": used + 1}
        _save(data)
        return {"allowed": True, "exempt": False, "limit": limit,
                "used": used + 1, "remaining": max(0, limit - (used + 1))}
