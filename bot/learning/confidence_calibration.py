"""
Confidence calibration — turn raw model confidence into a realized win-rate.

RUNECLAW blends LLM + confluence into a ``blended_confidence`` in [0,1] that gates
every trade (and, for admins, auto-trade at >= 0.85). But that number is the
*model's opinion*, not a measured probability: a raw 0.85 has never been checked
against how often 0.85-confidence trades actually won. This module fits a
**reliability curve** from the bot's OWN closed-trade history
(``DecisionMemory.confidence`` vs ``pnl_result > 0``) so that a calibrated 0.85
means ~85% historical win rate.

Design / safety:
  - **Monotonic** (isotonic, Pool-Adjacent-Violators): a higher raw confidence can
    never map to a lower calibrated win rate, so trade ordering is preserved and
    noise can't invert it.
  - **Shrinkage toward identity** for thin bins: a bucket with few samples stays
    near the raw value instead of overfitting a handful of outcomes.
  - **Fail-safe identity:** below ``min_samples`` total (or when unfitted),
    ``calibrate(x) == x`` exactly. The curve can only refine a confidence once
    there is enough evidence; it never fabricates one.
  - Pure storage + math. It places no trades and is wired into the decision path
    only behind a default-OFF flag (CONFIDENCE_CALIBRATION_ENABLED); until then it
    runs shadow-only (logs the would-be delta, changes nothing).

This module has no third-party deps (no numpy/sklearn) — PAV is a few lines.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

log = logging.getLogger("runeclaw.calibration")

_STATE_DIR = os.environ.get("RUNECLAW_STATE_DIR", "data")
_CAL_FILE = os.path.join(_STATE_DIR, "learning", "confidence_calibration.json")

# Tunables (deliberately conservative).
_DEFAULT_BINS = 10
_DEFAULT_MIN_SAMPLES = 30      # below this, calibration is identity
_DEFAULT_SHRINKAGE = 5.0       # pseudo-count pulling thin bins toward raw confidence


def _pav(values: list[float], weights: list[float]) -> list[float]:
    """Pool Adjacent Violators — isotonic (non-decreasing) regression.

    Returns a non-decreasing sequence minimizing the weighted squared error to
    ``values``. O(n). Standard algorithm; weights are per-point sample counts.
    """
    n = len(values)
    if n == 0:
        return []
    # Each block: [sum(weight*value), sum(weight), count]
    blocks: list[list[float]] = []
    for v, w in zip(values, weights):
        blocks.append([v * w, w, 1])
        # Merge while the previous block's mean exceeds this one's (violation).
        while len(blocks) >= 2:
            a, b = blocks[-2], blocks[-1]
            mean_a = a[0] / a[1] if a[1] else 0.0
            mean_b = b[0] / b[1] if b[1] else 0.0
            if mean_a <= mean_b:
                break
            blocks[-2] = [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
            blocks.pop()
    # Expand block means back to per-point.
    out: list[float] = []
    for s, w, count in blocks:
        mean = s / w if w else 0.0
        out.extend([mean] * int(count))
    return out


class ConfidenceCalibrator:
    """Fits and applies a monotonic confidence→win-rate reliability curve."""

    def __init__(self, bins: int = _DEFAULT_BINS, min_samples: int = _DEFAULT_MIN_SAMPLES,
                 shrinkage: float = _DEFAULT_SHRINKAGE) -> None:
        self.bins = max(2, int(bins))
        self.min_samples = max(1, int(min_samples))
        self.shrinkage = max(0.0, float(shrinkage))
        # Fitted curve: bin centers (x) -> calibrated win-rate (y), monotonic.
        self._x: list[float] = []
        self._y: list[float] = []
        self._n_samples: int = 0

    # -- fitting ---------------------------------------------------------------

    def fit(self, samples: list[tuple[float, bool]]) -> "ConfidenceCalibrator":
        """Fit from ``(confidence, won)`` pairs. Confidence clamped to [0,1];
        ``won`` is truthy for a profitable trade. Insufficient data -> identity."""
        clean = [(min(1.0, max(0.0, float(c))), 1.0 if w else 0.0)
                 for c, w in samples if c is not None]
        self._n_samples = len(clean)
        if self._n_samples < self.min_samples:
            self._x, self._y = [], []
            return self

        # Bin by confidence; accumulate wins + counts per bin.
        win = [0.0] * self.bins
        cnt = [0.0] * self.bins
        for c, w in clean:
            idx = min(self.bins - 1, int(c * self.bins))
            win[idx] += w
            cnt[idx] += 1.0

        centers: list[float] = []
        rates: list[float] = []
        weights: list[float] = []
        for i in range(self.bins):
            if cnt[i] <= 0:
                continue
            center = (i + 0.5) / self.bins
            # Shrink the empirical rate toward the bin's raw confidence (center)
            # with `shrinkage` pseudo-observations, so thin bins ~ identity.
            rate = (win[i] + self.shrinkage * center) / (cnt[i] + self.shrinkage)
            centers.append(center)
            rates.append(rate)
            weights.append(cnt[i])

        if not centers:
            self._x, self._y = [], []
            return self

        self._x = centers
        self._y = [min(1.0, max(0.0, v)) for v in _pav(rates, weights)]
        return self

    @staticmethod
    def samples_from_decisions(decisions) -> list[tuple[float, bool]]:
        """Extract ``(confidence, won)`` from DecisionMemory-like records that
        have a non-null ``pnl_result`` (i.e. completed trades)."""
        out: list[tuple[float, bool]] = []
        for d in decisions:
            pnl = getattr(d, "pnl_result", None)
            conf = getattr(d, "confidence", None)
            if pnl is None or conf is None:
                continue
            out.append((float(conf), float(pnl) > 0.0))
        return out

    # -- applying --------------------------------------------------------------

    def is_ready(self) -> bool:
        return bool(self._x) and self._n_samples >= self.min_samples

    def calibrate(self, confidence: float) -> float:
        """Map a raw confidence to its calibrated win-rate. Identity when not
        ready. Linear interpolation between bin centers; flat beyond the ends."""
        x = min(1.0, max(0.0, float(confidence)))
        if not self.is_ready():
            return x
        xs, ys = self._x, self._y
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        for i in range(1, len(xs)):
            if x <= xs[i]:
                x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
                if x1 == x0:
                    return y1
                t = (x - x0) / (x1 - x0)
                return y0 + t * (y1 - y0)
        return ys[-1]

    # -- persistence -----------------------------------------------------------

    def to_dict(self) -> dict:
        return {"bins": self.bins, "min_samples": self.min_samples,
                "shrinkage": self.shrinkage, "x": self._x, "y": self._y,
                "n_samples": self._n_samples}

    def load_dict(self, d: dict) -> "ConfidenceCalibrator":
        self.bins = int(d.get("bins", self.bins))
        self.min_samples = int(d.get("min_samples", self.min_samples))
        self.shrinkage = float(d.get("shrinkage", self.shrinkage))
        self._x = [float(v) for v in d.get("x", [])]
        self._y = [float(v) for v in d.get("y", [])]
        self._n_samples = int(d.get("n_samples", 0))
        return self

    def save(self, path: str = _CAL_FILE) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        tmp.rename(p)

    @classmethod
    def load(cls, path: str = _CAL_FILE) -> Optional["ConfidenceCalibrator"]:
        p = Path(path)
        if not p.exists():
            return None
        try:
            with open(p) as f:
                return cls().load_dict(json.load(f))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("confidence_calibration load failed: %s", exc)
            return None

    def summary(self) -> str:
        if not self.is_ready():
            return (f"calibration: NOT READY ({self._n_samples}/{self.min_samples} "
                    "samples) — confidence used as-is")
        pts = ", ".join(f"{x:.2f}->{y:.2f}" for x, y in zip(self._x, self._y))
        return f"calibration: ready on {self._n_samples} samples | curve: {pts}"


def refit_and_save(store=None, path: str = _CAL_FILE,
                   min_samples: int = _DEFAULT_MIN_SAMPLES) -> "ConfidenceCalibrator":
    """Fit a calibrator from completed decisions in the learning store and persist
    it. Returns the (possibly not-ready) calibrator. Safe to call on a fresh bot:
    with too little history it stays identity. ``store`` defaults to a new
    LearningStore so this can be called standalone (e.g. from a command or job).
    """
    if store is None:
        from bot.learning.store import LearningStore
        store = LearningStore()
    decisions = store.get_decisions(limit=100000)
    samples = ConfidenceCalibrator.samples_from_decisions(decisions)
    cal = ConfidenceCalibrator(min_samples=min_samples).fit(samples)
    try:
        cal.save(path)
    except OSError as exc:
        log.warning("confidence_calibration save failed: %s", exc)
    log.info("Refit %s", cal.summary())
    return cal
