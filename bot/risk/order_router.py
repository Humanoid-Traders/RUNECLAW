"""
RUNECLAW Smart Order Router -- slippage estimation and order type selection.

Stdlib-only, no external dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SlippageEstimate:
    """Result of a slippage estimation."""
    slippage_pct: float
    estimated_fill: float
    order_type: str  # "LIMIT" | "MARKET"
    warning: Optional[str]


class SmartOrderRouter:
    """Estimates slippage from order book depth and recommends order types."""

    # Thresholds
    LIMIT_THRESHOLD_PCT = 0.1   # slippage > 0.1% -> recommend LIMIT
    REJECT_THRESHOLD_PCT = 0.5  # slippage > 0.5% -> recommend rejection
    DEFAULT_PAPER_SLIPPAGE_PCT = 0.02  # default for paper mode (no book)

    def estimate_slippage(
        self,
        symbol: str,
        size_usd: float,
        order_book_depth: Optional[list[list[float]]] = None,
    ) -> dict:
        """Estimate fill price and slippage from order book depth.

        Args:
            symbol: Trading pair e.g. "BTCUSDT".
            size_usd: Notional order size in USD.
            order_book_depth: List of [price, quantity_usd] levels on the
                relevant side (asks for buys, bids for sells).
                Each level: [price, size_in_base_units].

        Returns:
            Dict with slippage_pct, estimated_fill, order_type, warning.
        """
        if size_usd <= 0:
            return {
                "slippage_pct": 0.0,
                "estimated_fill": 0.0,
                "order_type": "MARKET",
                "warning": "Invalid order size",
            }

        if not order_book_depth or len(order_book_depth) == 0:
            # Paper mode / no data: assume negligible slippage
            return {
                "slippage_pct": self.DEFAULT_PAPER_SLIPPAGE_PCT,
                "estimated_fill": 0.0,
                "order_type": "MARKET",
                "warning": None,
            }

        # Walk the book to compute volume-weighted average fill price
        best_price = order_book_depth[0][0]
        if best_price <= 0:
            return {
                "slippage_pct": 0.0,
                "estimated_fill": 0.0,
                "order_type": "MARKET",
                "warning": "Invalid book data",
            }

        filled_usd = 0.0
        cost_weighted_sum = 0.0

        for level in order_book_depth:
            if len(level) < 2:
                continue
            price, qty = level[0], level[1]
            if price <= 0 or qty <= 0:
                continue
            level_usd = price * qty
            remaining = size_usd - filled_usd
            if remaining <= 0:
                break
            fill_at_level = min(level_usd, remaining)
            cost_weighted_sum += price * (fill_at_level / price)  # qty filled at this price
            # Weighted by USD value: sum(price * qty_filled) / total_qty_filled
            # Actually: VWAP = sum(price_i * qty_i) / sum(qty_i)
            # Let's recalculate properly
            filled_usd += fill_at_level

        if filled_usd <= 0:
            return {
                "slippage_pct": 0.0,
                "estimated_fill": best_price,
                "order_type": "MARKET",
                "warning": "Insufficient book depth",
            }

        # Recalculate VWAP properly
        filled_usd_2 = 0.0
        total_qty = 0.0
        for level in order_book_depth:
            if len(level) < 2:
                continue
            price, qty = level[0], level[1]
            if price <= 0 or qty <= 0:
                continue
            level_usd = price * qty
            remaining = size_usd - filled_usd_2
            if remaining <= 0:
                break
            fill_usd = min(level_usd, remaining)
            qty_filled = fill_usd / price
            total_qty += qty_filled
            filled_usd_2 += fill_usd

        if total_qty <= 0:
            return {
                "slippage_pct": 0.0,
                "estimated_fill": best_price,
                "order_type": "MARKET",
                "warning": "Zero fill quantity",
            }

        vwap = filled_usd_2 / total_qty
        slippage_pct = abs(vwap - best_price) / best_price * 100

        # Determine order type and warnings
        warning = None
        if slippage_pct > self.REJECT_THRESHOLD_PCT:
            order_type = "LIMIT"
            warning = (
                f"High slippage ({slippage_pct:.3f}%) on {symbol} for "
                f"${size_usd:.0f} — consider reducing size or rejecting"
            )
        elif slippage_pct > self.LIMIT_THRESHOLD_PCT:
            order_type = "LIMIT"
            warning = (
                f"Moderate slippage ({slippage_pct:.3f}%) on {symbol} — "
                f"LIMIT order recommended"
            )
        else:
            order_type = "MARKET"

        # If book couldn't fill the full order, warn
        if filled_usd_2 < size_usd * 0.99:
            insufficiency = (1 - filled_usd_2 / size_usd) * 100
            partial_msg = (
                f"Order book can only fill {filled_usd_2:.0f}/{size_usd:.0f} USD "
                f"({insufficiency:.1f}% unfilled)"
            )
            warning = f"{warning}; {partial_msg}" if warning else partial_msg

        return {
            "slippage_pct": round(slippage_pct, 4),
            "estimated_fill": round(vwap, 8),
            "order_type": order_type,
            "warning": warning,
        }

    def optimal_order_type(
        self,
        slippage_pct: float,
        urgency: str = "normal",
    ) -> str:
        """Recommend order type based on slippage and urgency.

        Args:
            slippage_pct: Estimated slippage percentage.
            urgency: "low", "normal", or "high".

        Returns:
            "MARKET", "LIMIT", or "REJECT".
        """
        if slippage_pct > self.REJECT_THRESHOLD_PCT and urgency != "high":
            return "REJECT"
        if slippage_pct > self.REJECT_THRESHOLD_PCT and urgency == "high":
            return "MARKET"  # force fill despite slippage
        if slippage_pct > self.LIMIT_THRESHOLD_PCT:
            return "LIMIT"
        if urgency == "high":
            return "MARKET"
        if urgency == "low":
            return "LIMIT"
        return "MARKET"
