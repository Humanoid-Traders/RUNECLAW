"""
Voter-weight learning — learn which confluence voters actually predict outcomes.

RUNECLAW scores ~35 confluence voters with HAND-TUNED weights. This module learns,
from the bot's OWN completed trades, how well each voter's vote agreed with
winning trades, and turns that into a bounded per-voter weight **multiplier**
(centred on 1.0) so good voters count for a little more and poor ones a little
less.

Phase B is instrument-first: `_score_confluence` now emits a named per-voter
breakdown (`(name, vote, weight)`), and decision records persist it
(`DecisionMemory.confluence_votes`) joined to outcomes by `paper_trade_id`. This
module is the LEARNER that consumes that data. **Application** of the multipliers
to the live confluence sum is a separate, flag-gated follow-up (B2); on its own
this module changes no decision.

Design / safety (mirrors confidence_calibration.py):
  - **Bounded** multiplier in ``[min_mult, max_mult]`` (default [0.5, 1.5]) — a
    voter's influence can only shift modestly, never flip or dominate.
  - **Shrinkage** toward 1.0 by per-voter sample count: a voter seen on few
    trades barely moves.
  - **Identity** (1.0) below ``min_samples`` total or ``min_voter_samples`` for a
    given voter, and for any unseen voter. It can only refine with evidence.
  - Pure aggregation + math. No third-party deps.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

log = logging.getLogger("runeclaw.voter_weights")

_STATE_DIR = os.environ.get("RUNECLAW_STATE_DIR", "data")
_FILE = os.path.join(_STATE_DIR, "learning", "voter_weights.json")

_DEFAULT_MIN_SAMPLES = 20        # total completed trades before any learning
_DEFAULT_MIN_VOTER = 8           # agreeing trades for a voter before it moves
_DEFAULT_GAIN = 1.0              # edge -> multiplier scaling
_DEFAULT_SHRINKAGE = 10.0
_MIN_MULT = 0.5
_MAX_MULT = 1.5
_EPS = 1e-9


def _dir_sign(direction) -> float:
    d = str(direction or "").strip().upper()
    if d in ("LONG", "BUY", "+1", "1"):
        return 1.0
    if d in ("SHORT", "SELL", "-1"):
        return -1.0
    return 0.0


class VoterWeightLearner:
    """Learns bounded per-voter weight multipliers from completed trades."""

    def __init__(self, min_samples: int = _DEFAULT_MIN_SAMPLES,
                 min_voter_samples: int = _DEFAULT_MIN_VOTER,
                 gain: float = _DEFAULT_GAIN, shrinkage: float = _DEFAULT_SHRINKAGE) -> None:
        self.min_samples = max(1, int(min_samples))
        self.min_voter_samples = max(1, int(min_voter_samples))
        self.gain = max(0.0, float(gain))
        self.shrinkage = max(0.0, float(shrinkage))
        self._mult: dict[str, float] = {}
        self._n_samples = 0

    # -- fitting ---------------------------------------------------------------

    def fit(self, samples) -> "VoterWeightLearner":
        """Fit from ``(votes, direction, won)`` tuples, where ``votes`` is a list
        of ``(name, vote, weight)``. A voter "agrees" with the trade when its vote
        sign matches the trade direction; we learn the win rate among agreements.
        """
        clean = [s for s in samples if s and s[0]]
        self._n_samples = len(clean)
        self._mult = {}
        if self._n_samples < self.min_samples:
            return self

        base_wins = sum(1 for _, _, won in clean if won)
        base_rate = base_wins / self._n_samples if self._n_samples else 0.5

        # Per voter: count agreeing trades and how many of those won.
        agree_n: dict[str, int] = {}
        agree_w: dict[str, int] = {}
        for votes, direction, won in clean:
            ds = _dir_sign(direction)
            if ds == 0.0:
                continue
            seen = set()
            for name, vote, _weight in votes:
                if name in seen:
                    continue          # one count per voter per trade
                if vote * ds > _EPS:  # voter agreed with the trade direction
                    seen.add(name)
                    agree_n[name] = agree_n.get(name, 0) + 1
                    if won:
                        agree_w[name] = agree_w.get(name, 0) + 1

        for name, n in agree_n.items():
            if n < self.min_voter_samples:
                continue
            win_rate = agree_w.get(name, 0) / n
            edge = win_rate - base_rate          # [-1, 1]
            shrink = n / (n + self.shrinkage) if (n + self.shrinkage) else 0.0
            mult = 1.0 + edge * self.gain * shrink
            self._mult[name] = max(_MIN_MULT, min(_MAX_MULT, mult))
        return self

    @staticmethod
    def samples_from_decisions(decisions):
        """Join decision records (carrying ``confluence_votes`` + direction +
        ``paper_trade_id``) to outcome records (``pnl_result`` by
        ``paper_trade_id``) into ``(votes, direction, won)`` tuples."""
        outcome: dict[str, bool] = {}
        for d in decisions:
            tid = getattr(d, "paper_trade_id", "") or ""
            pnl = getattr(d, "pnl_result", None)
            if tid and pnl is not None:
                outcome[tid] = float(pnl) > 0.0
        samples = []
        for d in decisions:
            votes = getattr(d, "confluence_votes", None)
            tid = getattr(d, "paper_trade_id", "") or ""
            if not votes or tid not in outcome:
                continue
            samples.append((votes, getattr(d, "direction", ""), outcome[tid]))
        return samples

    def load_samples(self, store=None):
        if store is None:
            from bot.learning.store import LearningStore
            store = LearningStore()
        return self.samples_from_decisions(store.get_decisions(limit=100000))

    # -- applying --------------------------------------------------------------

    def is_ready(self) -> bool:
        return bool(self._mult) and self._n_samples >= self.min_samples

    def multiplier(self, name: str) -> float:
        """Bounded weight multiplier for a voter. 1.0 when not ready or unseen."""
        if not self.is_ready():
            return 1.0
        return self._mult.get(name, 1.0)

    # -- validation ------------------------------------------------------------

    def validate_oos(self, samples, split: float = 0.7) -> dict:
        """Time-ordered out-of-sample validation (audit fix #19).

        Fits on the FIRST ``split`` fraction of samples (they arrive in store
        order = chronological) and evaluates on the rest: for each learned
        voter, does agreeing-with-an-upweighted-voter (mult>1) still win more
        often than base rate on unseen trades — and vice versa for downweighted
        ones? Returns a report dict:

            {"n_train", "n_test", "base_rate_test", "voters":
                {name: {"mult", "oos_agree_n", "oos_win_rate", "oos_edge",
                        "direction_holds"}},
             "hold_rate"}

        ``direction_holds`` is True when the sign of the OOS edge matches the
        sign of the learned adjustment. ``hold_rate`` is the fraction of
        learned voters whose adjustment direction held OOS — the go/no-go
        number to check before enabling VOTER_WEIGHT_LEARNING_ENABLED.
        """
        clean = [s for s in samples if s and s[0]]
        n = len(clean)
        cut = max(1, int(n * split))
        train, test = clean[:cut], clean[cut:]
        report = {"n_train": len(train), "n_test": len(test),
                  "base_rate_test": 0.0, "voters": {}, "hold_rate": 0.0}
        if not test or len(train) < self.min_samples:
            return report

        fitted = VoterWeightLearner(self.min_samples, self.min_voter_samples,
                                    self.gain, self.shrinkage).fit(train)
        if not fitted.is_ready():
            return report

        base_rate = sum(1 for _, _, won in test if won) / len(test)
        report["base_rate_test"] = round(base_rate, 4)

        agree_n: dict[str, int] = {}
        agree_w: dict[str, int] = {}
        for votes, direction, won in test:
            ds = _dir_sign(direction)
            if ds == 0.0:
                continue
            seen = set()
            for name, vote, _weight in votes:
                if name in seen or name not in fitted._mult:
                    continue
                if vote * ds > _EPS:
                    seen.add(name)
                    agree_n[name] = agree_n.get(name, 0) + 1
                    if won:
                        agree_w[name] = agree_w.get(name, 0) + 1

        holds = 0
        judged = 0
        for name, mult in fitted._mult.items():
            an = agree_n.get(name, 0)
            wr = (agree_w.get(name, 0) / an) if an else 0.0
            edge = wr - base_rate
            adj = mult - 1.0
            direction_holds = (an > 0 and abs(adj) > 1e-6
                               and (edge > 0) == (adj > 0))
            if an > 0 and abs(adj) > 1e-6:
                judged += 1
                holds += 1 if direction_holds else 0
            report["voters"][name] = {
                "mult": round(mult, 3), "oos_agree_n": an,
                "oos_win_rate": round(wr, 4), "oos_edge": round(edge, 4),
                "direction_holds": direction_holds,
            }
        report["hold_rate"] = round(holds / judged, 4) if judged else 0.0
        return report

    # -- persistence -----------------------------------------------------------

    def to_dict(self) -> dict:
        from datetime import datetime, UTC
        return {"min_samples": self.min_samples, "min_voter_samples": self.min_voter_samples,
                "gain": self.gain, "shrinkage": self.shrinkage,
                "mult": self._mult, "n_samples": self._n_samples,
                # Versioning metadata (audit fix #19): a weights file without a
                # schema version + fit timestamp cannot be audited or expired.
                "schema_version": 2,
                "fitted_at": datetime.now(UTC).isoformat()}

    def load_dict(self, d: dict) -> "VoterWeightLearner":
        self.min_samples = int(d.get("min_samples", self.min_samples))
        self.min_voter_samples = int(d.get("min_voter_samples", self.min_voter_samples))
        self.gain = float(d.get("gain", self.gain))
        self.shrinkage = float(d.get("shrinkage", self.shrinkage))
        self._mult = {str(k): float(v) for k, v in (d.get("mult") or {}).items()}
        self._n_samples = int(d.get("n_samples", 0))
        return self

    def save(self, path: str = _FILE) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        tmp.rename(p)

    @classmethod
    def load(cls, path: str = _FILE) -> Optional["VoterWeightLearner"]:
        p = Path(path)
        if not p.exists():
            return None
        try:
            with open(p) as f:
                return cls().load_dict(json.load(f))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("voter_weights load failed: %s", exc)
            return None

    def summary(self) -> str:
        if not self.is_ready():
            return (f"voter-weights: NOT READY ({self._n_samples}/{self.min_samples} "
                    "trades) — all voter weights used as-is")
        moved = {k: round(v, 2) for k, v in sorted(self._mult.items()) if abs(v - 1.0) > 0.01}
        return (f"voter-weights: learned on {self._n_samples} trades | "
                f"adjusted: {moved or 'none'}")


_INSTANCE: Optional["VoterWeightLearner"] = None


def get_voter_learner(reload: bool = False) -> Optional["VoterWeightLearner"]:
    """Process-wide singleton, lazily loaded from disk. Returns None if no learner
    has been fitted yet (caller treats that as 'use hand-tuned weights')."""
    global _INSTANCE
    if _INSTANCE is None or reload:
        _INSTANCE = VoterWeightLearner.load()
    return _INSTANCE


def refit_and_save(store=None, path: str = _FILE) -> "VoterWeightLearner":
    """Fit a learner from the learning store and persist it. Safe on a fresh bot:
    too little history stays identity (all multipliers 1.0)."""
    learner = VoterWeightLearner()
    learner.fit(learner.load_samples(store))
    try:
        learner.save(path)
    except OSError as exc:
        log.warning("voter_weights save failed: %s", exc)
    log.info("Refit %s", learner.summary())
    global _INSTANCE
    _INSTANCE = learner          # apply immediately to the live singleton
    return learner
