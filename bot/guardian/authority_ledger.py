"""Rolling notional-spend ledger for the Authority Envelope's daily cap.

The Authority Envelope (``authority.py``) carries a ``max_notional_daily_usd``
ceiling, but ``authorize`` is pure and holds no state — the caller must supply how
much has already been spent in the rolling window. This module is that state: a
small, persisted, per-authority accumulator of notional over a rolling 24h window.

Design mirrors the risk engine's state file (atomic tmp→replace write). Two
deliberate safety choices:

* **Idempotent by ref.** ``record(key, amount, now, ref=…)`` ignores a duplicate
  ``ref`` (e.g. the same trade id re-evaluated), so a spend is never
  double-counted by a re-run of the risk gate.
* **Conservative for a cap.** Recording on *approval* (before fill confirmation) can
  only ever over-count, which makes the daily cap TIGHTER — it errs toward denying,
  never toward letting more value out. That is the correct bias for a spend
  ceiling, and the rolling window self-heals as old entries age out.

Pure core (``prune``/``window_sum``) is separated from I/O so the accounting is
trivially testable without a filesystem.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Optional

DEFAULT_WINDOW_S = 86_400   # 24h rolling window


def _num(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def prune(entries: list[dict], now_ts: float, window_s: float = DEFAULT_WINDOW_S) -> list[dict]:
    """Return only the entries within ``[now-window, now]``. Pure. ``ts`` is a
    numeric epoch in the SAME unit the caller uses for ``now_ts`` (seconds)."""
    lo = now_ts - window_s
    out = []
    for e in entries or []:
        ts = _num(e.get("ts"))
        if ts is not None and lo <= ts <= now_ts:
            out.append(e)
    return out


def window_sum(entries: list[dict], now_ts: float, window_s: float = DEFAULT_WINDOW_S) -> float:
    """Sum of in-window entry amounts (non-negative). Pure."""
    total = 0.0
    for e in prune(entries, now_ts, window_s):
        amt = _num(e.get("amount"))
        if amt is not None and amt > 0:
            total += amt
    return round(total, 6)


class AuthoritySpendLedger:
    """Per-authority rolling-window notional accumulator, persisted atomically."""

    def __init__(self, state_file: Optional[str] = None,
                 window_s: float = DEFAULT_WINDOW_S) -> None:
        self._path = state_file
        self._window_s = window_s
        self._lock = threading.RLock()
        # key -> list[{ts, amount, ref}]
        self._book: dict[str, list[dict]] = {}
        self._refs: dict[str, set] = {}      # key -> set of recorded refs (dedup)
        self._load()

    # -- persistence -----------------------------------------------------

    def _load(self) -> None:
        if not self._path or not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            book = raw.get("book") if isinstance(raw, dict) else None
            if isinstance(book, dict):
                self._book = {str(k): list(v) for k, v in book.items()}
                self._refs = {
                    k: {str(e.get("ref")) for e in v if e.get("ref") is not None}
                    for k, v in self._book.items()
                }
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            # A corrupt ledger must fail-safe to EMPTY — which makes the daily cap
            # MORE permissive only up to the per-trade cap, and the engine's own
            # caps still stand. Never crash the caller over a bad ledger file.
            self._book, self._refs = {}, {}

    def _save(self) -> None:
        if not self._path:
            return
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"book": self._book}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)   # atomic on POSIX
        except OSError:
            pass   # best-effort; the in-memory book is still authoritative this run

    # -- public API ------------------------------------------------------

    def spent(self, key: str, now_ts: float) -> float:
        """In-window notional already recorded under ``key`` as of ``now_ts``."""
        with self._lock:
            return window_sum(self._book.get(str(key), []), now_ts, self._window_s)

    def record(self, key: str, amount: Any, now_ts: float,
               ref: Optional[str] = None) -> bool:
        """Record ``amount`` of notional under ``key`` at ``now_ts``. Idempotent by
        ``ref`` (a duplicate ref is ignored). Prunes old entries opportunistically.
        Returns True if a new entry was added, False if it was a duplicate/invalid."""
        amt = _num(amount)
        if amt is None or amt <= 0:
            return False
        k = str(key)
        with self._lock:
            refs = self._refs.setdefault(k, set())
            if ref is not None and str(ref) in refs:
                return False
            book = self._book.setdefault(k, [])
            book.append({"ts": float(now_ts), "amount": round(amt, 6),
                         "ref": (str(ref) if ref is not None else None)})
            if ref is not None:
                refs.add(str(ref))
            # opportunistic prune keeps the file bounded
            kept = prune(book, now_ts, self._window_s)
            if len(kept) != len(book):
                self._book[k] = kept
                self._refs[k] = {str(e.get("ref")) for e in kept if e.get("ref") is not None}
            self._save()
            return True

    def remaining(self, key: str, daily_cap: Any, now_ts: float) -> Optional[float]:
        """``daily_cap - spent(key)``, floored at 0, or None if no cap given."""
        cap = _num(daily_cap)
        if cap is None:
            return None
        return max(0.0, round(cap - self.spent(key, now_ts), 6))
