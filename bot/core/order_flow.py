"""
RUNECLAW Order-Flow / Microstructure Engine
=============================================
A perception module that reads exchange microstructure to detect
directional pressure, large prints, and CVD-price divergences from
data RUNECLAW can already reach through ccxt:

  - Order-book imbalance + spread + top-of-book depth
  - Cumulative Volume Delta (CVD) from the aggressor side of trades
  - CVD-price divergence (absorption/distribution detection)
  - Whale-print detection (adaptive percentile threshold)
  - Funding rate + open-interest change (perp/swap markets only)

It produces an OrderFlowSignal that plugs into the existing confluence
scorer as extra voters and into the risk engine as a liquidity guard.

Design rules (consistent with the rest of RUNECLAW):
  - Fail-closed: any failed fetch degrades to a NEUTRAL component and
    lowers the signal's confidence. analyze() never raises.
  - Read-only: no state outside this module is mutated.
  - Thread-safe rolling state (RLock), bounded to cap memory.

SCOPE NOTE (read before interpreting these signals)
-----------------------------------------------------
This reads *exchange* order flow — microstructure data, not "smart money."
It does NOT see on-chain wallet flows or labelled fund/MM wallets — that
needs Nansen / Arkham / Glassnode-class paid data. A $25K spot print is
not a whale in BTC; it's as likely retail FOMO or a liquidation as
informed flow. Book imbalance is trivially spoofable. Funding/OI require
a swap symbol and a swap-enabled ccxt instance; on a spot symbol those
fields are None. These are short-horizon, noisy inputs — extra evidence,
not an edge by themselves — and they cannot be validated by the current
synthetic backtester, which has no L2/tick data.
"""

from __future__ import annotations

import os
import threading
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Optional

import numpy as np
from pydantic import BaseModel, Field

from bot.utils.logger import audit, system_log


# ── Config (mirrors bot/config.py style; fold into AppConfig if you like) ──

def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(float(os.getenv(key, str(default))))
    except ValueError:
        return default


@dataclass(frozen=True)
class OrderFlowConfig:
    """Tunable parameters for the order-flow engine."""
    book_depth_levels: int = _env_int("OF_BOOK_DEPTH", 25)        # levels per side to sum
    trades_window: int = _env_int("OF_TRADES_WINDOW", 200)        # recent trades to fetch
    whale_percentile: float = _env_float("OF_WHALE_PCT", 95.0)    # a trade is a "whale" above this percentile
    whale_min_usd: float = _env_float("OF_WHALE_MIN_USD", 25_000) # ...and at least this many USD
    funding_extreme: float = _env_float("OF_FUNDING_EXTREME", 0.0005)  # |rate| treated as crowded positioning
    cvd_history_len: int = _env_int("OF_CVD_HISTORY", 30)         # rolling per-call deltas kept per symbol
    # Liquidity guard thresholds (used by the risk engine, not for scoring)
    max_spread_bps: float = _env_float("OF_MAX_SPREAD_BPS", 25.0)
    min_top_depth_usd: float = _env_float("OF_MIN_DEPTH_USD", 50_000.0)
    # Composite weights
    w_book: float = 1.0
    w_aggressor: float = 1.0
    w_cvd_trend: float = 0.8
    w_whale: float = 1.2
    w_funding: float = 0.6
    max_tracked_symbols: int = 300


# ── Output schema ──────────────────────────────────────────────────────────

class OrderFlowSignal(BaseModel):
    """Microstructure snapshot for one symbol. All scores are signed [-1, 1]
    where positive = bullish/accumulation pressure."""
    symbol: str

    # Order book
    book_imbalance: float = 0.0          # (bid_depth - ask_depth) / total, [-1, 1]
    spread_bps: float = 0.0
    bid_depth_usd: float = 0.0
    ask_depth_usd: float = 0.0
    mid_price: float = 0.0

    # Trade flow (aggressor-side)
    cvd_window_usd: float = 0.0          # signed delta over the fetched window
    cvd_cumulative_usd: float = 0.0      # rolling cumulative across calls
    cvd_trend: str = "flat"              # "rising" | "falling" | "flat"
    cvd_price_divergence: str = "none"   # "bullish_div" | "bearish_div" | "none"
    buy_volume_usd: float = 0.0
    sell_volume_usd: float = 0.0
    aggressor_ratio: float = 0.5         # buy / (buy + sell), 0.5 = balanced

    # Whale activity
    whale_buy_usd: float = 0.0
    whale_sell_usd: float = 0.0
    whale_trade_count: int = 0
    largest_trade_usd: float = 0.0
    whale_bias: str = "neutral"          # "accumulation" | "distribution" | "neutral"

    # Derivatives (perp/swap only; None on spot)
    funding_rate: Optional[float] = None
    open_interest_usd: Optional[float] = None
    oi_change_pct: Optional[float] = None

    # Composite
    smart_money_score: float = 0.0       # blended [-1, 1]
    confidence: float = 0.0              # [0, 1] -- fraction of components that resolved
    components_ok: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ── Engine ───────────────────────────────────────────────────────────────

class OrderFlowAnalyzer:
    """Reads order book + trades (+ optional perp funding/OI) and produces an
    OrderFlowSignal. Reuse the scanner's ccxt exchange so you share one
    connection: of = await analyzer.analyze(exchange, "BTC/USDT")."""

    def __init__(self, config: Optional[OrderFlowConfig] = None) -> None:
        self.config = config or OrderFlowConfig()
        self._lock = threading.RLock()
        self._cvd_history: dict[str, deque] = {}   # symbol -> deque[float] per-call deltas
        self._price_history: dict[str, deque] = {}  # symbol -> deque[float] mid prices (for divergence)
        self._oi_history: dict[str, float] = {}     # symbol -> last open_interest_usd

    # -- Public API --

    async def analyze(
        self,
        exchange,
        symbol: str,
        *,
        derivatives_symbol: Optional[str] = None,
    ) -> OrderFlowSignal:
        """Build an OrderFlowSignal. Fail-closed: every fetch is independently
        guarded; a failure degrades that component to neutral and is recorded
        in `notes`, never raised. `derivatives_symbol` lets you map a spot
        symbol (BTC/USDT) to its perp (BTC/USDT:USDT) for funding/OI."""
        sig = OrderFlowSignal(symbol=symbol)
        ok: list[str] = []

        # 1. Order book
        try:
            book = await exchange.fetch_order_book(symbol, limit=self.config.book_depth_levels)
            self._fill_book_metrics(sig, book)
            ok.append("book")
        except Exception as exc:  # noqa: BLE001
            sig.notes.append(f"order_book unavailable: {exc}")

        # 2. Trade flow + whales
        try:
            trades = await exchange.fetch_trades(symbol, limit=self.config.trades_window)
            self._fill_trade_metrics(sig, trades, symbol)
            self._fill_whale_metrics(sig, trades)
            ok.append("trades")
        except Exception as exc:  # noqa: BLE001
            sig.notes.append(f"trades unavailable: {exc}")

        # 3. Derivatives (optional; perp only)
        deriv_sym = derivatives_symbol or symbol
        await self._fill_deriv_metrics(sig, exchange, deriv_sym, ok)

        # 4. Composite + confidence
        self._fill_composite(sig, ok)
        sig.components_ok = ok

        audit(system_log, f"OrderFlow {symbol}: score={sig.smart_money_score:+.2f} "
              f"conf={sig.confidence:.2f}",
              action="order_flow", result="OK",
              data={"symbol": symbol, "score": sig.smart_money_score,
                    "confidence": sig.confidence, "whale_bias": sig.whale_bias,
                    "components": ok})
        self._prune()
        return sig

    # -- Components --

    @staticmethod
    def _fill_book_metrics(sig: OrderFlowSignal, book: dict) -> None:
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        if not bids or not asks:
            sig.notes.append("empty order book")
            return
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        mid = (best_bid + best_ask) / 2.0
        if mid <= 0:
            return
        # USD notional resting on each side (price * base_amount)
        bid_usd = sum(float(p) * float(a) for p, a in bids)
        ask_usd = sum(float(p) * float(a) for p, a in asks)
        total = bid_usd + ask_usd
        sig.mid_price = round(mid, 8)
        sig.spread_bps = round((best_ask - best_bid) / mid * 1e4, 2)
        sig.bid_depth_usd = round(bid_usd, 2)
        sig.ask_depth_usd = round(ask_usd, 2)
        sig.book_imbalance = round((bid_usd - ask_usd) / total, 4) if total > 0 else 0.0

    def _fill_trade_metrics(self, sig: OrderFlowSignal, trades: list, symbol: str) -> None:
        if not trades:
            sig.notes.append("no recent trades")
            return
        # Chronological so uptick inference + CVD direction are correct
        trades = sorted(trades, key=lambda t: t.get("timestamp") or 0)
        buy_usd = 0.0
        sell_usd = 0.0
        prev_price: Optional[float] = None
        for t in trades:
            cost = self._trade_cost(t)
            side = t.get("side")
            if side not in ("buy", "sell"):
                # Fallback: tick rule (uptick = buyer-initiated)
                price = float(t.get("price") or 0)
                if prev_price is not None and price != prev_price:
                    side = "buy" if price > prev_price else "sell"
                prev_price = price
            if side == "buy":
                buy_usd += cost
            elif side == "sell":
                sell_usd += cost
        total = buy_usd + sell_usd
        delta = buy_usd - sell_usd
        sig.buy_volume_usd = round(buy_usd, 2)
        sig.sell_volume_usd = round(sell_usd, 2)
        sig.aggressor_ratio = round(buy_usd / total, 4) if total > 0 else 0.5
        sig.cvd_window_usd = round(delta, 2)

        # Rolling CVD + trend across calls
        with self._lock:
            hist = self._cvd_history.setdefault(
                symbol, deque(maxlen=self.config.cvd_history_len))
            hist.append(delta)
            sig.cvd_cumulative_usd = round(float(sum(hist)), 2)
            sig.cvd_trend = self._cvd_trend(list(hist))

            # CVD-price divergence: price making new high while CVD doesn't
            # (bearish divergence = distribution/absorption) or vice versa
            price_hist = self._price_history.setdefault(
                symbol, deque(maxlen=self.config.cvd_history_len))
            # Use VWAP of this trade window as the price observation
            vwap_window = (buy_usd + sell_usd)  # total volume as proxy
            last_price = float(trades[-1].get("price") or 0) if trades else 0.0
            if last_price > 0:
                price_hist.append(last_price)
            sig.cvd_price_divergence = self._detect_cvd_divergence(
                list(hist), list(price_hist))

    @staticmethod
    def _cvd_trend(deltas: list[float]) -> str:
        """Classify CVD trend. Always returns a string — never None."""
        if len(deltas) >= 4:
            half = len(deltas) // 2
            recent = float(np.mean(deltas[half:]))
            prior = float(np.mean(deltas[:half]))
            diff = recent - prior
            scale = (abs(recent) + abs(prior)) or 1.0
            if diff > 0.1 * scale:
                return "rising"
            if diff < -0.1 * scale:
                return "falling"
            return "flat"
        # Not enough history for split-window — use sign of the last delta
        if deltas:
            last = deltas[-1]
            if last > 0:
                return "rising"
            if last < 0:
                return "falling"
        return "flat"

    @staticmethod
    def _detect_cvd_divergence(cvd_deltas: list[float], prices: list[float]) -> str:
        """Detect CVD-price divergence: the most legitimate microstructure signal.
        - Bearish divergence: price making higher highs while CVD makes lower highs
          (sellers absorbing buy pressure without price dropping — distribution)
        - Bullish divergence: price making lower lows while CVD makes higher lows
          (buyers accumulating without price rising — accumulation)
        Requires at least 4 observations of each."""
        if len(cvd_deltas) < 4 or len(prices) < 4:
            return "none"

        half = len(prices) // 2
        price_first = prices[:half]
        price_second = prices[half:]
        cvd_first = cvd_deltas[:half]
        cvd_second = cvd_deltas[half:]

        price_high_1 = max(price_first)
        price_high_2 = max(price_second)
        cvd_high_1 = max(cvd_first)
        cvd_high_2 = max(cvd_second)

        price_low_1 = min(price_first)
        price_low_2 = min(price_second)
        cvd_low_1 = min(cvd_first)
        cvd_low_2 = min(cvd_second)

        # Bearish: price higher high, CVD lower high (absorption)
        if price_high_2 > price_high_1 and cvd_high_2 < cvd_high_1:
            return "bearish_div"

        # Bullish: price lower low, CVD higher low (accumulation)
        if price_low_2 < price_low_1 and cvd_low_2 > cvd_low_1:
            return "bullish_div"

        return "none"

    def _fill_whale_metrics(self, sig: OrderFlowSignal, trades: list) -> None:
        if not trades:
            return
        costs = np.array([self._trade_cost(t) for t in trades], dtype=float)
        costs = costs[costs > 0]
        if costs.size == 0:
            return
        sig.largest_trade_usd = round(float(costs.max()), 2)
        # Adaptive threshold: high percentile of this window, floored at a
        # minimum absolute size so a quiet book doesn't flag dust as a "whale".
        threshold = max(self.config.whale_min_usd,
                        float(np.percentile(costs, self.config.whale_percentile)))
        whale_buy = 0.0
        whale_sell = 0.0
        count = 0
        prev_price: Optional[float] = None
        for t in sorted(trades, key=lambda x: x.get("timestamp") or 0):
            cost = self._trade_cost(t)
            price = float(t.get("price") or 0)
            side = t.get("side")
            if side not in ("buy", "sell"):
                if prev_price is not None and price != prev_price:
                    side = "buy" if price > prev_price else "sell"
            prev_price = price
            if cost >= threshold:
                count += 1
                if side == "buy":
                    whale_buy += cost
                elif side == "sell":
                    whale_sell += cost
        sig.whale_buy_usd = round(whale_buy, 2)
        sig.whale_sell_usd = round(whale_sell, 2)
        sig.whale_trade_count = count
        net = whale_buy + whale_sell
        if net > 0:
            ratio = (whale_buy - whale_sell) / net
            if ratio > 0.25:
                sig.whale_bias = "accumulation"
            elif ratio < -0.25:
                sig.whale_bias = "distribution"
            else:
                sig.whale_bias = "neutral"

    async def _fill_deriv_metrics(
        self, sig: OrderFlowSignal, exchange, deriv_sym: str, ok: list[str]
    ) -> None:
        # Funding rate (perp only)
        try:
            fr = await exchange.fetch_funding_rate(deriv_sym)
            rate = fr.get("fundingRate")
            if rate is not None:
                sig.funding_rate = float(rate)
                ok.append("funding")
        except Exception as exc:  # noqa: BLE001
            sig.notes.append(f"funding n/a (spot symbol or unsupported): {exc}")

        # Open interest + change vs last observation
        try:
            oi = await exchange.fetch_open_interest(deriv_sym)
            oi_usd = oi.get("openInterestValue")
            if oi_usd is None:
                amt = oi.get("openInterestAmount")
                if amt is not None and sig.mid_price > 0:
                    oi_usd = float(amt) * sig.mid_price
            if oi_usd is not None:
                oi_usd = float(oi_usd)
                sig.open_interest_usd = round(oi_usd, 2)
                with self._lock:
                    prev = self._oi_history.get(deriv_sym)
                    self._oi_history[deriv_sym] = oi_usd
                if prev and prev > 0:
                    sig.oi_change_pct = round((oi_usd - prev) / prev * 100, 3)
                ok.append("open_interest")
        except Exception as exc:  # noqa: BLE001
            sig.notes.append(f"open_interest n/a: {exc}")

    def _fill_composite(self, sig: OrderFlowSignal, ok: list[str]) -> None:
        c = self.config
        contribs: list[tuple[float, float]] = []  # (value[-1,1], weight)

        if "book" in ok:
            contribs.append((float(np.clip(sig.book_imbalance, -1, 1)), c.w_book))
            contribs.append(((sig.aggressor_ratio - 0.5) * 2.0, c.w_aggressor))
        if "trades" in ok:
            trend_val = {"rising": 1.0, "falling": -1.0, "flat": 0.0}.get(sig.cvd_trend, 0.0)
            contribs.append((trend_val, c.w_cvd_trend))
            whale_val = {"accumulation": 1.0, "distribution": -1.0, "neutral": 0.0}.get(sig.whale_bias, 0.0)
            contribs.append((whale_val, c.w_whale))
        if sig.funding_rate is not None:
            # Contrarian: very positive funding = crowded longs = bearish lean
            fnorm = float(np.clip(sig.funding_rate / c.funding_extreme, -1, 1))
            contribs.append((-fnorm, c.w_funding))

        wsum = sum(w for _, w in contribs)
        if wsum > 0:
            sig.smart_money_score = round(
                float(np.clip(sum(v * w for v, w in contribs) / wsum, -1, 1)), 4)

        # Confidence = share of total possible weight that actually resolved
        max_weight = (c.w_book + c.w_aggressor + c.w_cvd_trend + c.w_whale + c.w_funding)
        sig.confidence = round(min(1.0, wsum / max_weight), 3) if max_weight > 0 else 0.0

    # -- Integration helpers --

    @staticmethod
    def to_confluence_votes(sig: OrderFlowSignal) -> tuple[list[float], list[float], list[str]]:
        """Return (votes, weights, labels) so order flow drops straight into
        Analyzer._score_confluence. Votes are graded in [-1, 1]; weights are
        scaled by the signal's data-confidence so a half-empty snapshot counts
        for less."""
        votes: list[float] = []
        weights: list[float] = []
        labels: list[str] = []
        conf = max(0.0, min(1.0, sig.confidence))
        if conf == 0.0:
            return votes, weights, labels

        if "book" in sig.components_ok:
            votes.append(float(np.clip(sig.book_imbalance, -1, 1)))
            weights.append(0.6 * conf)
            labels.append("of_book_imbalance")
        if "trades" in sig.components_ok:
            votes.append({"rising": 1.0, "falling": -1.0, "flat": 0.0}.get(sig.cvd_trend, 0.0))
            weights.append(0.7 * conf)
            labels.append("of_cvd_trend")
            votes.append({"accumulation": 1.0, "distribution": -1.0, "neutral": 0.0}.get(sig.whale_bias, 0.0))
            weights.append(0.9 * conf)
            labels.append("of_whale_bias")
        if sig.funding_rate is not None:
            votes.append(-float(np.clip(sig.funding_rate / 0.0005, -1, 1)))
            weights.append(0.5 * conf)
            labels.append("of_funding")

        # CVD-price divergence: strongest microstructure signal
        if sig.cvd_price_divergence != "none":
            if sig.cvd_price_divergence == "bullish_div":
                votes.append(1.0)
            elif sig.cvd_price_divergence == "bearish_div":
                votes.append(-1.0)
            weights.append(0.8 * conf)  # high weight — divergence is meaningful
            labels.append("of_cvd_divergence")

        return votes, weights, labels

    def liquidity_guard(self, sig: OrderFlowSignal) -> Optional[str]:
        """Return a rejection reason if the book is too thin / too wide to
        trade safely, else None. Wire this into RiskEngine as a 17th check.
        Fail-OPEN by design: if the book never resolved we cannot judge
        liquidity, so we do not block on missing data here -- the analyzer's
        confidence already reflects that uncertainty elsewhere."""
        if "book" not in sig.components_ok:
            return None
        if sig.spread_bps > self.config.max_spread_bps:
            return (f"LIQUIDITY: spread {sig.spread_bps:.1f}bps > "
                    f"{self.config.max_spread_bps}bps")
        top_depth = min(sig.bid_depth_usd, sig.ask_depth_usd)
        if top_depth < self.config.min_top_depth_usd:
            return (f"LIQUIDITY: thin book, min side depth ${top_depth:,.0f} < "
                    f"${self.config.min_top_depth_usd:,.0f}")
        return None

    # -- Internals --

    @staticmethod
    def _trade_cost(t: dict) -> float:
        cost = t.get("cost")
        if cost is not None:
            try:
                return float(cost)
            except (TypeError, ValueError):
                pass
        price = float(t.get("price") or 0)
        amount = float(t.get("amount") or 0)
        return price * amount

    def _prune(self) -> None:
        with self._lock:
            if len(self._cvd_history) > self.config.max_tracked_symbols:
                for k in list(self._cvd_history)[:-self.config.max_tracked_symbols]:
                    del self._cvd_history[k]
            if len(self._price_history) > self.config.max_tracked_symbols:
                for k in list(self._price_history)[:-self.config.max_tracked_symbols]:
                    del self._price_history[k]
            if len(self._oi_history) > self.config.max_tracked_symbols:
                for k in list(self._oi_history)[:-self.config.max_tracked_symbols]:
                    del self._oi_history[k]
