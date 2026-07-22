"""NB3 — per-user leverage preference resolution (pure).

A BYOK live user may pin their own standard leverage, but only ever DOWN from
the operator's configured default — the operator's cap is the ceiling. This
keeps the money-path invariant intact: a per-user preference can lower risk,
never raise it above what the operator allows.
"""

from __future__ import annotations

from typing import Optional


def resolve_user_leverage(pref, cap, min_lev: int = 1) -> int:
    """Clamp a user's leverage preference into ``[min_lev, cap]``.

    - ``pref`` None / non-numeric / <= 0  → ``cap`` (unchanged behaviour).
    - a valid ``pref``                    → ``max(min_lev, min(pref, cap))``.

    The result NEVER exceeds ``cap`` — the operator default is the ceiling, so a
    user can only reduce. Fail-safe: any error returns a sane clamp of ``cap``
    (or 1 if even that is unusable). Returns an int >= 1.
    """
    try:
        c = max(1, int(cap))
    except (TypeError, ValueError):
        return 1
    try:
        m = max(1, int(min_lev))
    except (TypeError, ValueError):
        m = 1
    m = min(m, c)
    if pref is None:
        return c
    try:
        p = int(float(pref))
    except (TypeError, ValueError):
        return c
    if p <= 0:
        return c
    return max(m, min(p, c))


def describe_user_leverage(pref: Optional[int], cap: int) -> str:
    """A short human line for status surfaces."""
    eff = resolve_user_leverage(pref, cap)
    if pref is None:
        return f"{eff}x (operator default)"
    if eff < int(pref):
        return f"{eff}x (your {int(pref)}x was capped at the operator {cap}x)"
    return f"{eff}x (your preference)"
