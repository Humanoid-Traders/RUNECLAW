"""
Per-setup expectancy — let the bot's own track record on a setup nudge confidence.

RUNECLAW already records every completed trade (`DecisionMemory`: symbol, regime,
direction, ``pnl_result``) and ``experience.get_similar_setups()`` surfaces the
matching history — but only into the *LLM prompt*, never the decision gate. This
module turns that history into a small, bounded **confidence nudge**: if longs on
SOL in a RANGE regime have historically won 30% of the time, shade that setup's
confidence down; if 70%, shade it up.

Design / safety (mirrors confidence_calibration.py):
  - **Bounded** nudge in ``[-max_nudge, +max_nudge]`` (default ±0.05) — it can
    only *shade* a confidence, never dominate the analyzer or risk gate.
  - **Shrinkage by sample count**: a setup with few trades barely moves; the nudge
    scales toward full strength only as evidence accumulates.
  - **Identity below ``min_samples``** (default 10) and when no history loaded —
    zero nudge. It can only refine once there is evidence, never fabricate one.
  - Pure read-only aggregation over completed decisions. Cached in memory; refresh
    explicitly. Wired into the decision path only behind a default-OFF flag
    (SETUP_EXPECTANCY_ENABLED); until then it runs shadow-only (logs, no effect).
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("runeclaw.setup_expectancy")

_DEFAULT_MIN_SAMPLES = 10
_DEFAULT_MAX_NUDGE = 0.05
_DEFAULT_SHRINKAGE = 10.0   # pseudo-trades; the nudge reaches ~half strength at n≈shrinkage


def _norm(s) -> str:
    return str(s or "").strip().upper()


class SetupExpectancy:
    """Aggregates completed-trade win rates per (symbol, regime, direction) and
    converts the matching history into a bounded confidence nudge."""

    def __init__(self, min_samples: int = _DEFAULT_MIN_SAMPLES,
                 max_nudge: float = _DEFAULT_MAX_NUDGE,
                 shrinkage: float = _DEFAULT_SHRINKAGE) -> None:
        self.min_samples = max(1, int(min_samples))
        self.max_nudge = max(0.0, float(max_nudge))
        self.shrinkage = max(0.0, float(shrinkage))
        # (symbol, regime, direction) -> [wins, total]
        self._table: dict[tuple, list[int]] = {}
        self._loaded = False

    # -- building --------------------------------------------------------------

    def ingest(self, samples) -> "SetupExpectancy":
        """Ingest ``(symbol, regime, direction, won)`` tuples (completed trades)."""
        table: dict[tuple, list[int]] = {}
        n = 0
        for sym, regime, direction, won in samples:
            key = (_norm(sym), _norm(regime), _norm(direction))
            cell = table.setdefault(key, [0, 0])
            cell[0] += 1 if won else 0
            cell[1] += 1
            n += 1
        self._table = table
        self._loaded = n > 0
        return self

    @staticmethod
    def samples_from_decisions(decisions):
        """Extract ``(symbol, regime, direction, won)`` from completed
        DecisionMemory-like records (non-null ``pnl_result``)."""
        out = []
        for d in decisions:
            pnl = getattr(d, "pnl_result", None)
            if pnl is None:
                continue
            out.append((getattr(d, "symbol", ""), getattr(d, "market_regime", ""),
                        getattr(d, "direction", ""), float(pnl) > 0.0))
        return out

    def load(self, store=None) -> "SetupExpectancy":
        """Load completed-trade history from the learning store (default: a fresh
        LearningStore). Safe on an empty store — stays identity."""
        if store is None:
            from bot.learning.store import LearningStore
            store = LearningStore()
        decisions = store.get_decisions(limit=100000)
        return self.ingest(self.samples_from_decisions(decisions))

    # -- querying --------------------------------------------------------------

    def lookup(self, symbol, regime, direction) -> tuple:
        """Return ``(win_rate, n)`` for the setup, or ``(0.5, 0)`` if unseen."""
        cell = self._table.get((_norm(symbol), _norm(regime), _norm(direction)))
        if not cell or cell[1] == 0:
            return 0.5, 0
        return cell[0] / cell[1], cell[1]

    def confidence_nudge(self, symbol, regime, direction) -> float:
        """Signed, bounded confidence nudge for this setup. 0.0 below
        ``min_samples`` or when no history is loaded.

        nudge = (win_rate - 0.5) * 2 * max_nudge * shrink, where
        shrink = n / (n + shrinkage) pulls thin samples toward zero.
        """
        win_rate, n = self.lookup(symbol, regime, direction)
        if n < self.min_samples:
            return 0.0
        shrink = n / (n + self.shrinkage) if (n + self.shrinkage) else 0.0
        nudge = (win_rate - 0.5) * 2.0 * self.max_nudge * shrink
        return max(-self.max_nudge, min(self.max_nudge, nudge))

    def is_ready(self) -> bool:
        return self._loaded

    def summary(self) -> str:
        if not self._loaded:
            return "setup-expectancy: no completed-trade history loaded"
        ready = sum(1 for c in self._table.values() if c[1] >= self.min_samples)
        total = sum(c[1] for c in self._table.values())
        return (f"setup-expectancy: {len(self._table)} setups, {total} trades, "
                f"{ready} setup(s) at/above {self.min_samples}-trade threshold")


_INSTANCE: Optional["SetupExpectancy"] = None


def get_setup_expectancy(reload: bool = False) -> "SetupExpectancy":
    """Process-wide singleton, lazily loaded from the learning store."""
    global _INSTANCE
    if _INSTANCE is None or reload:
        inst = SetupExpectancy()
        try:
            inst.load()
        except Exception as exc:
            log.warning("setup-expectancy load failed: %s", exc)
        _INSTANCE = inst
    return _INSTANCE
