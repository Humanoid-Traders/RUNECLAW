"""NB3 — tiny per-user leverage-preference store (non-secret, JSON-backed).

BYOK live users can pin their own standard leverage (only ever applied as a
reduce vs the operator default — see bot.core.leverage.resolve_user_leverage).
This is deliberately SEPARATE from the encrypted credential store: it holds no
secret, just a small integer per user, so it never touches the key material.

Fail-safe everywhere: read errors return None (the caller then uses the operator
default), and write errors are logged and swallowed — a preferences file must
never take a trade or a command down.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Optional

log = logging.getLogger(__name__)
_LOCK = threading.Lock()


def _path() -> str:
    base = os.environ.get("RUNECLAW_STATE_DIR", "data")
    return os.path.join(base, "user_leverage.json")


def _load() -> dict:
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("user_leverage read failed: %s", exc)
        return {}


def get(user_id) -> Optional[int]:
    """The stored preference for a user, or None (→ use the operator default)."""
    uid = str(user_id or "").strip()
    if not uid:
        return None
    v = _load().get(uid)
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def set_pref(user_id, value) -> Optional[int]:
    """Persist a leverage preference (int >= 1). Returns the stored int, or None
    if the input is unusable or the write failed. Never raises."""
    uid = str(user_id or "").strip()
    if not uid:
        return None
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return None
    if n < 1:
        return None
    with _LOCK:
        d = _load()
        d[uid] = n
        try:
            os.makedirs(os.path.dirname(_path()) or ".", exist_ok=True)
            tmp = _path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f)
            os.replace(tmp, _path())
        except Exception as exc:
            log.warning("user_leverage write failed: %s", exc)
            return None
    return n


def clear(user_id) -> bool:
    """Remove a user's preference (→ back to the operator default). Never raises."""
    uid = str(user_id or "").strip()
    if not uid:
        return False
    with _LOCK:
        d = _load()
        if uid not in d:
            return False
        del d[uid]
        try:
            with open(_path(), "w", encoding="utf-8") as f:
                json.dump(d, f)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("user_leverage clear failed: %s", exc)
            return False
    return True
