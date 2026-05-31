"""
RUNECLAW Trade Outcome Feedback Loop.

Records trade outcomes alongside the features that produced each signal,
then computes simple correlation-based feature importance to suggest
weight adjustments for the quant factor model.

Thread-safe, in-memory rolling buffer (no persistence for MVP).
"""

from __future__ import annotations

import math
import threading
from typing import Optional


class TradeOutcomeFeedback:
    """Rolling buffer of trade outcomes with feature-importance analysis."""

    _NUMERIC_FEATURES = ("adx", "momentum", "confidence", "hurst", "volume_ratio")
    _MIN_TRADES_FOR_IMPORTANCE = 20

    def __init__(self, buffer_size: int = 200) -> None:
        self._buffer_size = buffer_size
        self._buffer: list[dict] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_outcome(self, features: dict, outcome: dict) -> None:
        """Append a trade result to the rolling buffer.

        Parameters
        ----------
        features : dict
            Keys: adx, momentum, regime, confidence, hurst, volume_ratio
        outcome : dict
            Keys: pnl_pct, r_multiple, won (bool), hold_bars (int)
        """
        with self._lock:
            self._buffer.append({"features": dict(features), "outcome": dict(outcome)})
            if len(self._buffer) > self._buffer_size:
                self._buffer = self._buffer[-self._buffer_size:]

    # ------------------------------------------------------------------
    # Feature importance (point-biserial correlation)
    # ------------------------------------------------------------------

    def compute_feature_importance(self) -> dict[str, float]:
        """Compute point-biserial correlation of each numeric feature with win/loss.

        Returns an empty dict if fewer than ``_MIN_TRADES_FOR_IMPORTANCE``
        trades have been recorded.
        """
        with self._lock:
            buf = list(self._buffer)

        if len(buf) < self._MIN_TRADES_FOR_IMPORTANCE:
            return {}

        results: dict[str, float] = {}
        for feat_name in self._NUMERIC_FEATURES:
            corr = self._point_biserial(buf, feat_name)
            if corr is not None:
                results[feat_name] = round(corr, 4)
        return results

    def suggest_weight_adjustments(self) -> dict[str, float]:
        """Suggest normalised quant-factor weights based on feature importance.

        Features positively correlated with winning get a boost; negatively
        correlated features get reduced.  Weights are normalised to sum to 1.
        """
        importance = self.compute_feature_importance()
        if not importance:
            return {}

        # Shift correlations so the minimum is >= a small positive floor
        min_corr = min(importance.values())
        floor = 0.05
        raw: dict[str, float] = {}
        for feat, corr in importance.items():
            raw[feat] = max(floor, corr - min_corr + floor)

        total = sum(raw.values())
        if total <= 0:
            return {}

        return {k: round(v / total, 4) for k, v in raw.items()}

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Summary statistics of recorded trades."""
        with self._lock:
            buf = list(self._buffer)

        total = len(buf)
        if total == 0:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "avg_r_multiple": 0.0,
                "top_features": {},
                "suggested_weights": {},
            }

        wins = sum(1 for t in buf if t["outcome"].get("won"))
        r_multiples = [t["outcome"].get("r_multiple", 0.0) for t in buf]
        avg_r = sum(r_multiples) / total

        importance = self.compute_feature_importance()
        # Top features sorted by absolute correlation
        top = dict(sorted(importance.items(), key=lambda x: abs(x[1]), reverse=True))

        return {
            "total_trades": total,
            "win_rate": round(wins / total, 4),
            "avg_r_multiple": round(avg_r, 4),
            "top_features": top,
            "suggested_weights": self.suggest_weight_adjustments(),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _point_biserial(buf: list[dict], feat_name: str) -> Optional[float]:
        """Compute point-biserial correlation between a numeric feature and win/loss.

        Returns None if the feature is missing or has zero variance.
        """
        xs: list[float] = []
        ys: list[float] = []  # 1.0 for win, 0.0 for loss
        for entry in buf:
            val = entry["features"].get(feat_name)
            if val is None:
                continue
            try:
                xs.append(float(val))
            except (TypeError, ValueError):
                continue
            ys.append(1.0 if entry["outcome"].get("won") else 0.0)

        n = len(xs)
        if n < 2:
            return None

        mean_x = sum(xs) / n
        mean_y = sum(ys) / n

        # Variance check
        var_x = sum((x - mean_x) ** 2 for x in xs) / n
        var_y = sum((y - mean_y) ** 2 for y in ys) / n
        if var_x == 0 or var_y == 0:
            return 0.0

        std_x = math.sqrt(var_x)
        std_y = math.sqrt(var_y)

        cov = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n)) / n
        return cov / (std_x * std_y)
