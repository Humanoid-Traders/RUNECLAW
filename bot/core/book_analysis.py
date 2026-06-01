"""
Order Book Analysis for RUNECLAW: Wall Detection + Slippage Estimation.

Detects large resting orders (walls) that act as support/resistance or
spoofing. Estimates execution slippage for a given order size by walking
the order book.

Data source: Order book already fetched in order_flow.py via ccxt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class BookWall:
    """A detected wall (large resting order) in the order book."""
    price: float
    size_usd: float
    side: str               # "bid" | "ask"
    wall_ratio: float       # size relative to average level size
    is_significant: bool    # True if wall_ratio > threshold


@dataclass
class SlippageEstimate:
    """Pre-trade slippage forecast for a given order size."""
    order_size_usd: float
    avg_fill_price: float
    market_price: float
    slippage_bps: float     # basis points of slippage
    slippage_usd: float     # absolute slippage cost
    levels_consumed: int    # number of book levels needed
    fillable: bool          # True if book has enough depth


@dataclass
class BookAnalysisResult:
    """Combined order book analysis output."""
    bid_walls: list[BookWall] = field(default_factory=list)
    ask_walls: list[BookWall] = field(default_factory=list)
    strongest_bid_wall: Optional[BookWall] = None
    strongest_ask_wall: Optional[BookWall] = None
    buy_slippage: Optional[SlippageEstimate] = None
    sell_slippage: Optional[SlippageEstimate] = None
    bid_depth_usd: float = 0.0
    ask_depth_usd: float = 0.0
    imbalance_ratio: float = 0.5   # bid_depth / total_depth


def detect_walls(
    bids: list[list[float]],
    asks: list[list[float]],
    wall_threshold: float = 3.0,
    min_wall_usd: float = 10_000.0,
) -> tuple[list[BookWall], list[BookWall]]:
    """Detect walls (unusually large orders) in the order book.

    A wall is any level where size > wall_threshold x average level size
    AND the absolute USD value exceeds min_wall_usd.

    Args:
        bids: [[price, size], ...] sorted by price descending
        asks: [[price, size], ...] sorted by price ascending
        wall_threshold: multiple of avg size to qualify as wall
        min_wall_usd: minimum USD value for wall classification

    Returns:
        (bid_walls, ask_walls) lists
    """
    bid_walls = []
    ask_walls = []

    if not bids or not asks:
        return bid_walls, ask_walls

    # Process bid side
    if len(bids) >= 3:
        bid_sizes = [b[1] for b in bids]
        avg_bid = np.mean(bid_sizes) if bid_sizes else 1
        for price, size in bids:
            usd_val = price * size
            ratio = size / avg_bid if avg_bid > 0 else 0
            significant = ratio >= wall_threshold and usd_val >= min_wall_usd
            if significant:
                bid_walls.append(BookWall(
                    price=round(price, 6),
                    size_usd=round(usd_val, 2),
                    side="bid",
                    wall_ratio=round(ratio, 2),
                    is_significant=True,
                ))

    # Process ask side
    if len(asks) >= 3:
        ask_sizes = [a[1] for a in asks]
        avg_ask = np.mean(ask_sizes) if ask_sizes else 1
        for price, size in asks:
            usd_val = price * size
            ratio = size / avg_ask if avg_ask > 0 else 0
            significant = ratio >= wall_threshold and usd_val >= min_wall_usd
            if significant:
                ask_walls.append(BookWall(
                    price=round(price, 6),
                    size_usd=round(usd_val, 2),
                    side="ask",
                    wall_ratio=round(ratio, 2),
                    is_significant=True,
                ))

    return bid_walls, ask_walls


def estimate_slippage(
    book_side: list[list[float]],
    order_size_usd: float,
    market_price: float,
    side: str = "buy",
) -> SlippageEstimate:
    """Estimate execution slippage by walking the order book.

    Args:
        book_side: asks (for buy) or bids (for sell), [[price, size], ...]
        order_size_usd: target order size in USD
        market_price: current market/mid price
        side: "buy" or "sell"

    Returns:
        SlippageEstimate with fill price and slippage in bps
    """
    if not book_side or order_size_usd <= 0 or market_price <= 0:
        return SlippageEstimate(
            order_size_usd=order_size_usd,
            avg_fill_price=market_price,
            market_price=market_price,
            slippage_bps=0,
            slippage_usd=0,
            levels_consumed=0,
            fillable=False,
        )

    remaining_usd = order_size_usd
    total_cost = 0.0
    total_qty = 0.0
    levels = 0

    for price, size in book_side:
        if remaining_usd <= 0:
            break
        level_usd = price * size
        fill_usd = min(remaining_usd, level_usd)
        fill_qty = fill_usd / price
        total_cost += fill_qty * price
        total_qty += fill_qty
        remaining_usd -= fill_usd
        levels += 1

    fillable = remaining_usd <= 0
    avg_fill = total_cost / total_qty if total_qty > 0 else market_price

    if side == "buy":
        slippage_bps = (avg_fill - market_price) / market_price * 10_000
    else:
        slippage_bps = (market_price - avg_fill) / market_price * 10_000

    slippage_usd = abs(avg_fill - market_price) * total_qty

    return SlippageEstimate(
        order_size_usd=round(order_size_usd, 2),
        avg_fill_price=round(avg_fill, 6),
        market_price=round(market_price, 6),
        slippage_bps=round(max(slippage_bps, 0), 2),
        slippage_usd=round(slippage_usd, 2),
        levels_consumed=levels,
        fillable=fillable,
    )


def analyze_book(
    bids: list[list[float]],
    asks: list[list[float]],
    order_size_usd: float = 1000.0,
    wall_threshold: float = 3.0,
) -> BookAnalysisResult:
    """Full order book analysis: walls + slippage + depth.

    Args:
        bids: [[price, size], ...] descending by price
        asks: [[price, size], ...] ascending by price
        order_size_usd: hypothetical order size for slippage estimate
        wall_threshold: multiple of avg for wall detection
    """
    bid_walls, ask_walls = detect_walls(bids, asks, wall_threshold)

    mid_price = (bids[0][0] + asks[0][0]) / 2 if bids and asks else 0

    buy_slip = estimate_slippage(asks, order_size_usd, mid_price, "buy")
    sell_slip = estimate_slippage(bids, order_size_usd, mid_price, "sell")

    bid_depth = sum(p * s for p, s in bids) if bids else 0
    ask_depth = sum(p * s for p, s in asks) if asks else 0
    total_depth = bid_depth + ask_depth

    return BookAnalysisResult(
        bid_walls=bid_walls,
        ask_walls=ask_walls,
        strongest_bid_wall=max(bid_walls, key=lambda w: w.size_usd) if bid_walls else None,
        strongest_ask_wall=max(ask_walls, key=lambda w: w.size_usd) if ask_walls else None,
        buy_slippage=buy_slip,
        sell_slippage=sell_slip,
        bid_depth_usd=round(bid_depth, 2),
        ask_depth_usd=round(ask_depth, 2),
        imbalance_ratio=round(bid_depth / total_depth, 4) if total_depth > 0 else 0.5,
    )
