"""
RUNECLAW Slippage Tracker — measures and models execution quality.

Records expected vs actual fill prices for every trade, builds a
per-symbol slippage model, and provides predictions for new trades.

Over hundreds of trades, slippage silently drains 2-5% of equity.
Measuring it is the first step to optimizing execution.

Features:
  - Per-symbol slippage statistics (mean, median, p95, p99)
  - Slippage prediction for new trades based on historical data
  - Volume-adjusted slippage (larger orders = more slippage)
  - Reject trades where predicted slippage > threshold
  - Persistence to disk for continuity across restarts
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class SlippageRecord:
    """Single slippage observation."""
    symbol: str
    expected_price: float
    actual_price: float
    slippage_pct: float      # (actual - expected) / expected * 100, signed
    slippage_abs_pct: float  # absolute slippage %
    direction: str           # "LONG" or "SHORT"
    order_type: str          # "market" or "limit"
    size_usd: float
    timestamp: float


@dataclass
class SlippageStats:
    """Aggregated slippage statistics for a symbol."""
    symbol: str
    total_trades: int = 0
    mean_slippage_pct: float = 0.0
    median_slippage_pct: float = 0.0
    p95_slippage_pct: float = 0.0
    p99_slippage_pct: float = 0.0
    max_slippage_pct: float = 0.0
    favorable_count: int = 0     # positive slippage (got better price)
    adverse_count: int = 0       # negative slippage (got worse price)
    total_slippage_usd: float = 0.0  # cumulative $ lost to slippage


class SlippageTracker:
    """Tracks execution slippage and provides predictions."""

    def __init__(self, state_file: str = "data/slippage_state.json") -> None:
        self._records: dict[str, list[SlippageRecord]] = defaultdict(list)
        self._state_file = state_file
        self._max_records_per_symbol = 500
        self._load_state()

    def record(
        self,
        symbol: str,
        expected_price: float,
        actual_price: float,
        direction: str,
        order_type: str = "market",
        size_usd: float = 0.0,
    ) -> SlippageRecord:
        """Record a slippage observation."""
        if expected_price <= 0:
            raise ValueError(f"Expected price must be positive, got {expected_price}")

        # For LONG market orders, slippage is adverse when actual > expected
        # For SHORT market orders, slippage is adverse when actual < expected
        raw_slip = (actual_price - expected_price) / expected_price * 100

        # Normalize: positive = favorable (got better price), negative = adverse
        if direction == "LONG":
            signed_slip = -raw_slip  # for longs, lower actual = favorable
        else:
            signed_slip = raw_slip   # for shorts, higher actual = favorable

        record = SlippageRecord(
            symbol=symbol,
            expected_price=expected_price,
            actual_price=actual_price,
            slippage_pct=round(signed_slip, 6),
            slippage_abs_pct=round(abs(raw_slip), 6),
            direction=direction,
            order_type=order_type,
            size_usd=size_usd,
            timestamp=time.time(),
        )

        self._records[symbol].append(record)

        # Cap per-symbol history
        if len(self._records[symbol]) > self._max_records_per_symbol:
            self._records[symbol] = self._records[symbol][-self._max_records_per_symbol:]

        # Auto-save periodically
        if sum(len(v) for v in self._records.values()) % 10 == 0:
            self._save_state()

        logger.debug(
            "Slippage recorded: %s %s expected=%.6f actual=%.6f slip=%.4f%%",
            symbol, direction, expected_price, actual_price, signed_slip,
        )

        return record

    def get_stats(self, symbol: str) -> Optional[SlippageStats]:
        """Get aggregated slippage stats for a symbol."""
        records = self._records.get(symbol)
        if not records:
            return None

        slippages = [r.slippage_abs_pct for r in records]
        signed = [r.slippage_pct for r in records]

        arr = np.array(slippages)

        favorable = sum(1 for s in signed if s > 0)
        adverse = sum(1 for s in signed if s < 0)

        # Estimate total USD lost to adverse slippage
        total_usd_lost = sum(
            r.size_usd * r.slippage_abs_pct / 100
            for r in records if r.slippage_pct < 0
        )

        return SlippageStats(
            symbol=symbol,
            total_trades=len(records),
            mean_slippage_pct=round(float(np.mean(arr)), 4),
            median_slippage_pct=round(float(np.median(arr)), 4),
            p95_slippage_pct=round(float(np.percentile(arr, 95)), 4),
            p99_slippage_pct=round(float(np.percentile(arr, 99)), 4),
            max_slippage_pct=round(float(np.max(arr)), 4),
            favorable_count=favorable,
            adverse_count=adverse,
            total_slippage_usd=round(total_usd_lost, 2),
        )

    def predict_slippage(
        self,
        symbol: str,
        size_usd: float = 0.0,
        order_type: str = "market",
    ) -> float:
        """Predict expected slippage for a new trade.

        Returns expected slippage as a percentage (always positive).
        Uses historical data if available, otherwise falls back to defaults.
        """
        records = self._records.get(symbol)

        # Default slippage assumptions
        if order_type == "limit":
            return 0.01  # limit orders have minimal slippage

        if not records or len(records) < 3:
            # No history: use conservative defaults
            return 0.05  # 0.05% default for market orders

        # Use recent slippage as predictor
        recent = records[-20:]
        market_records = [r for r in recent if r.order_type == "market"]

        if not market_records:
            return 0.05

        slippages = [r.slippage_abs_pct for r in market_records]

        # Use 75th percentile as expected (conservative)
        predicted = float(np.percentile(slippages, 75))

        # Adjust for size: larger orders get more slippage
        if size_usd > 0 and market_records:
            avg_size = np.mean([r.size_usd for r in market_records if r.size_usd > 0])
            if avg_size > 0:
                size_ratio = size_usd / avg_size
                if size_ratio > 1.5:
                    predicted *= min(size_ratio * 0.8, 3.0)  # cap at 3x

        return round(max(0.01, predicted), 4)

    def should_reject(
        self,
        symbol: str,
        edge_pct: float,
        size_usd: float = 0.0,
        max_slippage_ratio: float = 0.3,
    ) -> tuple[bool, str]:
        """Check if predicted slippage would eat too much of the trade's edge.

        Args:
            symbol: trading pair
            edge_pct: expected edge as % (e.g., risk-reward adjusted expected profit)
            size_usd: position size
            max_slippage_ratio: reject if slippage > this fraction of edge

        Returns:
            (should_reject, reason)
        """
        predicted = self.predict_slippage(symbol, size_usd)

        if edge_pct <= 0:
            return False, "no edge to evaluate"

        ratio = predicted / edge_pct

        if ratio > max_slippage_ratio:
            return True, (
                f"Predicted slippage {predicted:.3f}% is {ratio:.0%} of "
                f"edge {edge_pct:.3f}% (max {max_slippage_ratio:.0%})"
            )

        return False, f"Slippage OK: {predicted:.3f}% vs edge {edge_pct:.3f}%"

    def get_all_stats(self) -> dict[str, SlippageStats]:
        """Get stats for all tracked symbols."""
        result = {}
        for symbol in self._records:
            stats = self.get_stats(symbol)
            if stats:
                result[symbol] = stats
        return result

    def _save_state(self) -> None:
        """Persist slippage data to disk."""
        try:
            os.makedirs(os.path.dirname(self._state_file) or ".", exist_ok=True)
            data = {}
            for symbol, records in self._records.items():
                data[symbol] = [
                    {
                        "symbol": r.symbol,
                        "expected": r.expected_price,
                        "actual": r.actual_price,
                        "slip_pct": r.slippage_pct,
                        "slip_abs": r.slippage_abs_pct,
                        "direction": r.direction,
                        "order_type": r.order_type,
                        "size_usd": r.size_usd,
                        "ts": r.timestamp,
                    }
                    for r in records[-100:]  # save last 100 per symbol
                ]
            with open(self._state_file, "w") as f:
                json.dump(data, f)
        except Exception as exc:
            logger.debug("Failed to save slippage state: %s", exc)

    def _load_state(self) -> None:
        """Load persisted slippage data."""
        try:
            if not os.path.exists(self._state_file):
                return
            with open(self._state_file) as f:
                data = json.load(f)
            for symbol, records in data.items():
                for r in records:
                    self._records[symbol].append(SlippageRecord(
                        symbol=r["symbol"],
                        expected_price=r["expected"],
                        actual_price=r["actual"],
                        slippage_pct=r["slip_pct"],
                        slippage_abs_pct=r["slip_abs"],
                        direction=r["direction"],
                        order_type=r.get("order_type", "market"),
                        size_usd=r.get("size_usd", 0),
                        timestamp=r.get("ts", 0),
                    ))
            logger.info("Loaded slippage data for %d symbols", len(self._records))
        except Exception as exc:
            logger.debug("Failed to load slippage state: %s", exc)
