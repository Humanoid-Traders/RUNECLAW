"""
RUNECLAW Order-Flow / Microstructure Engine
=============================================
A perception module that reads exchange microstructure to detect
directional pressure, large prints, and CVD-price divergences from
data RUNECLAW can already reach through ccxt:

  - Order-book imbalance + spread + depth (full-book sum and top-N executable)
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

import asyncio
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import numpy as np
from pydantic import BaseModel, Field

from bot.compat import UTC
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


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


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
    max_spread_bps: float = _env_float("OF_MAX_SPREAD_BPS", 50.0)
    min_top_depth_usd: float = _env_float("OF_MIN_DEPTH_USD", 2_000.0)  # absolute floor; scaled by position size
    # Top-of-book executable depth (deep-audit medium): bid_depth_usd/ask_depth_usd
    # SUM all OF_BOOK_DEPTH (25) levels, which overstates immediately-fillable
    # liquidity. book_top_levels is how many best levels count as "executable",
    # exposed as bid/ask_depth_top_usd. When OF_GUARD_TOP_DEPTH_ENABLED is on, the
    # liquidity guard additionally requires that top-of-book executable depth to
    # cover the position size (a naturally-scaled tightening); default OFF leaves
    # the guard byte-identical (25-level sum only).
    book_top_levels: int = _env_int("OF_BOOK_TOP_LEVELS", 5)
    guard_top_depth_enabled: bool = _env_bool("OF_GUARD_TOP_DEPTH_ENABLED", True)
    # Large-cap symbols that should always require $10K+ book depth
    LARGE_CAP_SYMBOLS: frozenset = frozenset({
        "BTC/USDT", "ETH/USDT", "SOL/USDT",
        "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
    })
    # Composite weights
    w_book: float = 1.0
    w_aggressor: float = 1.0
    w_cvd_trend: float = 0.8
    w_whale: float = 1.2
    w_funding: float = 0.6
    max_tracked_symbols: int = 300
    # --- Taker 3-bar gate: time-awareness (deep-audit HIGH) ---
    # The taker 3-bar confirmation gate counts the last 3 polled "bars" as if
    # they were uniform time bars, but polls are irregular: 3 polls fired in a
    # burst (manual rescan, tight loop) confirm a "3-bar" trend that spans only
    # seconds, and a gappy/stale window confirms on data that no longer reflects
    # live flow. When enabled, the gate additionally requires the wall-clock span
    # of the last 3 bars to fall within [min, max] seconds — too short = burst,
    # too long = stale/gappy — and rejects the confirmation otherwise. Default OFF
    # keeps the gate byte-identical (bars stored as bare floats, no span check);
    # the check is tightening-only (it can only reject a confirmation), so it is
    # safe-direction regardless of poll cadence.
    time_bars_enabled: bool = _env_bool("OF_TIME_BARS_ENABLED", True)
    taker_bar_min_span_sec: float = _env_float("OF_TAKER_BAR_MIN_SPAN_SEC", 20.0)
    # Max span raised 300s -> 1800s: taker bars accrue once per SCAN of a
    # symbol (minutes apart), not per exchange tick — a 300s ceiling rejected
    # every confirmed streak at real scan cadence, making Gate 2 unpassable.
    taker_bar_max_span_sec: float = _env_float("OF_TAKER_BAR_MAX_SPAN_SEC", 1800.0)
    # Rule 20 book-dominance confirmation ratio (favored:opposing side, top-N
    # executable depth). The advertised 2:1 never actually ran (the gate was
    # unwired) and blocks most entries in balanced books; the active default
    # is a milder 1.2:1 confirmation, env-tunable.
    dominance_required_ratio: float = _env_float("OF_DOMINANCE_RATIO", 1.2)
    # --- Funding confluence vote: scale fix (deep-audit medium) ---
    # to_confluence_votes normalised the funding rate by 0.03 (3%), but funding
    # rates are tiny (typically ±0.0001–0.0005), so the vote was ~60x too small
    # to ever move confluence — effectively dead. The correct scale is 0.0005
    # (= funding_extreme above, and what the smart-money scorer already uses):
    # |funding| ≥ 0.05% saturates the contrarian vote to ±1. When ON, the vote
    # uses the fixed scale and actually contributes; default OFF keeps the old
    # (dead) 0.03 scale so confluence is byte-identical until enabled.
    funding_vote_fixed_scale: bool = _env_bool("OF_FUNDING_VOTE_FIXED_SCALE", True)


# ── Output schema ──────────────────────────────────────────────────────────

class OrderFlowSignal(BaseModel):
    """Microstructure snapshot for one symbol. All scores are signed [-1, 1]
    where positive = bullish/accumulation pressure."""
    symbol: str

    # Order book
    book_imbalance: float = 0.0          # (bid_depth - ask_depth) / total, [-1, 1]
    spread_bps: float = 0.0
    bid_depth_usd: float = 0.0           # SUM over all fetched levels (~25)
    ask_depth_usd: float = 0.0           # SUM over all fetched levels (~25)
    bid_depth_top_usd: float = 0.0       # SUM over the top-N best levels (executable)
    ask_depth_top_usd: float = 0.0       # SUM over the top-N best levels (executable)
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

    # Spot vs Futures divergence
    spot_volume_usd: float = 0.0         # total spot trade volume in window
    futures_oi_change_pct: Optional[float] = None  # alias for oi_change_pct for clarity
    spot_futures_divergence: str = "none"  # "spot_led_bullish" | "spec_led_bearish" | "none"

    # OI-Price divergence (squeeze detection)
    oi_price_divergence: str = "none"     # "squeeze_building" | "genuine_demand" | "leverage_unwind" | "none"

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
        self._ws_feed = None  # BitgetWSFeed for tape CVD (set_ws_feed)
        self._price_history: dict[str, deque] = {}  # symbol -> deque[float] mid prices (for divergence)
        self._oi_history: dict[str, float] = {}     # symbol -> last open_interest_usd
        # Gate 2: Taker 3-bar ratios (buy_vol/sell_vol per bar)
        self._taker_bar_ratios: dict[str, deque] = {}  # symbol -> deque[float] recent bar ratios
        # Spot vs Futures: rolling spot volume history for divergence detection
        self._spot_vol_history: dict[str, deque] = {}   # symbol -> deque[float] spot volume per call
        self._oi_val_history: dict[str, deque] = {}     # symbol -> deque[float] OI snapshots for trend
        self._price_snap_history: dict[str, deque] = {} # symbol -> deque[float] price snapshots for OI-price div

    # -- Public API --

    def set_ws_feed(self, feed) -> None:
        """Wire the WS feed so CVD can come from the TRUE trade tape
        (deduped, gap-free) instead of overlapping REST windows."""
        self._ws_feed = feed

    def warm_oi_history(self, path: str = "data/learning/order_flow_snapshots.jsonl",
                        max_age_hours: float = 24.0) -> int:
        """Reload per-symbol OI/price history from recorded OF snapshots so the
        OI-trend and OI-price-divergence classifiers survive restarts instead
        of re-warming over N live scans (each scan is minutes apart, so a
        restart used to blind them for roughly an hour). Reads only the tail
        of the snapshot file, keeps snapshots younger than ``max_age_hours``
        (stale OI is worse than no OI), and returns the number of symbols
        warmed. Best-effort fail-open: any error returns 0 and live scanning
        proceeds with cold history exactly as before."""
        import json as _json
        from datetime import datetime as _dt

        try:
            from pathlib import Path as _Path
            p = _Path(path)
            if not p.exists():
                return 0
            # Tail-read: snapshots append forever; only the recent end matters.
            lines = p.read_text(errors="replace").splitlines()[-4000:]
            now = _dt.now(UTC)
            per_symbol: dict[str, list[tuple[float, float]]] = {}
            for line in lines:
                try:
                    rec = _json.loads(line)
                    sig = rec.get("signal") or {}
                    oi = sig.get("open_interest_usd")
                    px = sig.get("mid_price") or 0.0
                    ts = _dt.fromisoformat(rec.get("ts", ""))
                    if oi is None or float(oi) <= 0:
                        continue
                    if (now - ts).total_seconds() > max_age_hours * 3600:
                        continue
                    sym = rec.get("symbol") or sig.get("symbol") or ""
                    if sym:
                        per_symbol.setdefault(sym, []).append((float(oi), float(px)))
                except Exception:
                    continue
            warmed = 0
            with self._lock:
                for sym, rows in per_symbol.items():
                    if not rows:
                        continue
                    oi_hist = self._oi_val_history.setdefault(
                        sym, deque(maxlen=self.config.cvd_history_len))
                    px_hist = self._price_snap_history.setdefault(
                        sym, deque(maxlen=self.config.cvd_history_len))
                    for oi, px in rows:
                        oi_hist.append(oi)
                        if px > 0:
                            px_hist.append(px)
                    self._oi_history[sym] = rows[-1][0]
                    warmed += 1
            return warmed
        except Exception:
            return 0

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

        # Parallelize all independent exchange fetches
        deriv_sym = derivatives_symbol or symbol

        book_task = exchange.fetch_order_book(symbol, limit=self.config.book_depth_levels)
        trades_task = exchange.fetch_trades(symbol, limit=self.config.trades_window)
        funding_task = exchange.fetch_funding_rate(deriv_sym)
        oi_task = exchange.fetch_open_interest(deriv_sym)

        book_result, trades_result, funding_result, oi_result = await asyncio.gather(
            book_task, trades_task, funding_task, oi_task,
            return_exceptions=True
        )

        # 1. Order book
        if isinstance(book_result, Exception):
            sig.notes.append(f"order_book unavailable: {book_result}")
        else:
            try:
                self._fill_book_metrics(sig, book_result, self.config.book_top_levels)
                ok.append("book")
            except Exception as exc:  # noqa: BLE001
                sig.notes.append(f"order_book processing error: {exc}")

        # 2. Trade flow + whales
        if isinstance(trades_result, Exception):
            sig.notes.append(f"trades unavailable: {trades_result}")
        else:
            try:
                self._fill_trade_metrics(sig, trades_result, symbol)
                self._fill_whale_metrics(sig, trades_result)
                ok.append("trades")
            except Exception as exc:  # noqa: BLE001
                sig.notes.append(f"trades processing error: {exc}")

        # 3. Derivatives: funding rate
        if isinstance(funding_result, Exception):
            sig.notes.append(f"funding n/a (spot symbol or unsupported): {funding_result}")
        else:
            try:
                rate = funding_result.get("fundingRate")
                if rate is not None:
                    sig.funding_rate = float(rate)
                    ok.append("funding")
            except Exception as exc:  # noqa: BLE001
                sig.notes.append(f"funding processing error: {exc}")

        # 4. Derivatives: open interest
        if isinstance(oi_result, Exception):
            sig.notes.append(f"open_interest n/a: {oi_result}")
        else:
            try:
                oi_usd = oi_result.get("openInterestValue")
                if oi_usd is None:
                    amt = oi_result.get("openInterestAmount")
                    if amt is not None and sig.mid_price > 0:
                        oi_usd = float(amt) * sig.mid_price
                if oi_usd is not None:
                    oi_usd = float(oi_usd)
                    sig.open_interest_usd = round(oi_usd, 2)
                    with self._lock:
                        prev = self._oi_history.get(deriv_sym)
                        self._oi_history[deriv_sym] = oi_usd
                        # Track OI snapshots for divergence detection
                        oi_val_hist = self._oi_val_history.setdefault(
                            deriv_sym, deque(maxlen=self.config.cvd_history_len))
                        oi_val_hist.append(oi_usd)
                        # Track price snapshots alongside OI
                        if sig.mid_price > 0:
                            px_hist = self._price_snap_history.setdefault(
                                deriv_sym, deque(maxlen=self.config.cvd_history_len))
                            px_hist.append(sig.mid_price)
                    if prev is not None and prev > 0:
                        sig.oi_change_pct = round((oi_usd - prev) / prev * 100, 3)
                    sig.futures_oi_change_pct = sig.oi_change_pct
                    ok.append("open_interest")
            except Exception as exc:  # noqa: BLE001
                sig.notes.append(f"open_interest processing error: {exc}")

        # 5. Spot vs Futures divergence + OI-Price divergence
        try:
            self._fill_spot_futures_divergence(sig, deriv_sym)
        except Exception as exc:  # noqa: BLE001
            sig.notes.append(f"spot_futures_divergence error: {exc}")
        try:
            self._fill_oi_price_divergence(sig, deriv_sym)
        except Exception as exc:  # noqa: BLE001
            sig.notes.append(f"oi_price_divergence error: {exc}")

        # 6. Composite + confidence
        self._fill_composite(sig, ok)
        sig.components_ok = ok

        audit(system_log, f"OrderFlow {symbol}: score={sig.smart_money_score:+.2f} "
              f"conf={sig.confidence:.2f} sf_div={sig.spot_futures_divergence} "
              f"oi_px_div={sig.oi_price_divergence}",
              action="order_flow", result="OK",
              data={"symbol": symbol, "score": sig.smart_money_score,
                    "confidence": sig.confidence, "whale_bias": sig.whale_bias,
                    "spot_futures_div": sig.spot_futures_divergence,
                    "oi_price_div": sig.oi_price_divergence,
                    "components": ok})
        self._prune()
        return sig

    # -- Components --

    @staticmethod
    def _fill_book_metrics(sig: OrderFlowSignal, book: dict, top_levels: int = 5) -> None:
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
        # USD notional resting on each side (price * base_amount). The full-book
        # sum feeds soft scoring (imbalance); the top-N sum is the genuinely
        # executable depth used by the liquidity guard.
        bid_usd = sum(float(p) * float(a) for p, a in bids)
        ask_usd = sum(float(p) * float(a) for p, a in asks)
        n = max(1, int(top_levels))
        bid_top = sum(float(p) * float(a) for p, a in bids[:n])
        ask_top = sum(float(p) * float(a) for p, a in asks[:n])
        total = bid_usd + ask_usd
        sig.mid_price = round(mid, 8)
        sig.spread_bps = round((best_ask - best_bid) / mid * 1e4, 2)
        sig.bid_depth_usd = round(bid_usd, 2)
        sig.ask_depth_usd = round(ask_usd, 2)
        sig.bid_depth_top_usd = round(bid_top, 2)
        sig.ask_depth_top_usd = round(ask_top, 2)
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
            price = float(t.get("price") or 0)
            side = t.get("side")
            if side not in ("buy", "sell"):
                # Fallback: tick rule (uptick = buyer-initiated)
                if prev_price is not None and price != prev_price:
                    side = "buy" if price > prev_price else "sell"
            # Advance every iteration so the tick rule compares against the
            # IMMEDIATELY preceding trade — not a stale price left over from the
            # last side-less trade (mirrors _fill_whale_metrics). Bug: the update
            # used to live inside the branch above, so exchange-sided trades did
            # not advance prev_price and later tick-rule inferences were wrong.
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

        # Gate 2: track taker bar ratio (buy/sell) for 3-bar rule. When
        # time-awareness is enabled, stamp each bar with wall-clock time so the
        # gate can reject burst/stale confirmations; otherwise store a bare
        # float (byte-identical to the legacy behaviour). The gate handles both
        # shapes via _bar_ratio/_bar_ts.
        bar_ratio = (buy_usd / sell_usd) if sell_usd > 0 else (2.0 if buy_usd > 0 else 1.0)
        with self._lock:
            ratios = self._taker_bar_ratios.setdefault(
                symbol, deque(maxlen=10))
            if self.config.time_bars_enabled:
                ratios.append((time.time(), round(bar_ratio, 4)))
            else:
                ratios.append(round(bar_ratio, 4))

        # Track spot volume for spot-vs-futures divergence
        sig.spot_volume_usd = round(total, 2)
        with self._lock:
            spot_hist = self._spot_vol_history.setdefault(
                symbol, deque(maxlen=self.config.cvd_history_len))
            spot_hist.append(total)

        # Rolling CVD + trend across calls (REST-window approximation:
        # overlapping/gappy 200-trade windows — used only as the fallback).
        with self._lock:
            hist = self._cvd_history.setdefault(
                symbol, deque(maxlen=self.config.cvd_history_len))
            hist.append(delta)
            sig.cvd_cumulative_usd = round(float(sum(hist)), 2)
            sig.cvd_trend = self._cvd_trend(list(hist))

            price_hist = self._price_history.setdefault(
                symbol, deque(maxlen=self.config.cvd_history_len))
            last_price = float(trades[-1].get("price") or 0) if trades else 0.0
            if last_price > 0:
                price_hist.append(last_price)
            sig.cvd_price_divergence = self._detect_cvd_divergence(
                list(hist), list(price_hist))

        # WS tape CVD override (audit fix): when fresh trade-channel data
        # exists, the TRUE deduped cumulative delta replaces the REST
        # approximation, and trend/divergence are computed on the real
        # cumulative series (per-minute buckets) instead of window deltas.
        # Fail-open: no/stale tape → the fallback above stands.
        ws = getattr(self, "_ws_feed", None)
        if ws is not None:
            try:
                tape = ws.get_cvd(symbol)
                if tape and tape.get("trades", 0) >= 30:
                    sig.cvd_cumulative_usd = round(tape["cum_delta_usd"], 2)
                    series = tape.get("series") or []
                    if len(series) >= 4:
                        bucket_deltas = [series[i] - series[i - 1]
                                         for i in range(1, len(series))]
                        sig.cvd_trend = self._cvd_trend(bucket_deltas)
                        prices = tape.get("prices") or []
                        if len(prices) == len(series):
                            sig.cvd_price_divergence = self._series_divergence(
                                series, prices)
                    sig.notes.append(
                        f"cvd: ws tape ({tape['trades']} trades, "
                        f"{tape['age_sec']}s fresh)")
            except Exception as _ws_exc:  # noqa: BLE001
                sig.notes.append(f"ws cvd unavailable: {_ws_exc}")

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
    def _series_divergence(cum_series: list[float], prices: list[float]) -> str:
        """Divergence on the TRUE cumulative-CVD series: price makes a higher
        high while cumulative delta makes a lower high → bearish_div; price
        lower low while cum delta higher low → bullish_div. (The legacy
        detector compared raw window DELTAS — a magnitude comparison, not
        cumulative structure.)"""
        n = min(len(cum_series), len(prices))
        if n < 6:
            return "none"
        cum = cum_series[-n:]
        px = prices[-n:]
        half = n // 2
        if max(px[half:]) > max(px[:half]) and max(cum[half:]) < max(cum[:half]):
            return "bearish_div"
        if min(px[half:]) < min(px[:half]) and min(cum[half:]) > min(cum[:half]):
            return "bullish_div"
        return "none"

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
        whale_vol = whale_buy + whale_sell
        # M-15: Log when significant whale volume has undetermined sides
        if count > 0 and whale_vol == 0 and whale_buy == 0 and whale_sell == 0:
            system_log.debug("Whale volume had undetermined sides for %s (%d trades above threshold)", sig.symbol, count)
        net = whale_buy + whale_sell
        if net > 0:
            ratio = (whale_buy - whale_sell) / net
            if ratio > 0.25:
                sig.whale_bias = "accumulation"
            elif ratio < -0.25:
                sig.whale_bias = "distribution"
            else:
                sig.whale_bias = "neutral"

    def _fill_spot_futures_divergence(self, sig: OrderFlowSignal, deriv_sym: str) -> None:
        """Detect divergence between spot demand and futures speculation.

        Bullish (spot_led_bullish): Spot volume is rising while OI is stable
        or declining — real buying demand without speculative leverage.

        Bearish (spec_led_bearish): OI is rising aggressively while spot
        volume is flat/declining — speculative leverage without real demand.
        This often precedes liquidation cascades.
        """
        with self._lock:
            spot_hist = self._spot_vol_history.get(sig.symbol)
            oi_hist = self._oi_val_history.get(deriv_sym)

        if not spot_hist or len(spot_hist) < 4:
            return
        if not oi_hist or len(oi_hist) < 4:
            return

        spot_list = list(spot_hist)
        oi_list = list(oi_hist)

        half_s = len(spot_list) // 2
        half_o = len(oi_list) // 2

        spot_recent = float(np.mean(spot_list[half_s:]))
        spot_prior = float(np.mean(spot_list[:half_s]))
        oi_recent = float(np.mean(oi_list[half_o:]))
        oi_prior = float(np.mean(oi_list[:half_o]))

        # Relative changes
        spot_chg = (spot_recent - spot_prior) / spot_prior if spot_prior > 0 else 0
        oi_chg = (oi_recent - oi_prior) / oi_prior if oi_prior > 0 else 0

        # Spot rising (+10%) while OI flat or declining
        if spot_chg > 0.10 and oi_chg < 0.05:
            sig.spot_futures_divergence = "spot_led_bullish"
        # OI rising (+10%) while spot flat or declining
        elif oi_chg > 0.10 and spot_chg < 0.05:
            sig.spot_futures_divergence = "spec_led_bearish"

    def _fill_oi_price_divergence(self, sig: OrderFlowSignal, deriv_sym: str) -> None:
        """Detect OI vs price divergence to identify squeeze risk or genuine demand.

        squeeze_building: OI rising + price flat/declining — leverage is
        building without price follow-through. Liquidation cascade risk
        increases.  The direction of the squeeze depends on funding rate
        (positive funding = longs crowded = short squeeze less likely).

        genuine_demand: OI rising + price rising — new money entering with
        conviction. The rally has structural support.

        leverage_unwind: OI falling + price falling — leveraged positions
        being liquidated. Often marks capitulation / near-bottom.
        """
        with self._lock:
            oi_hist = self._oi_val_history.get(deriv_sym)
            price_hist = self._price_snap_history.get(deriv_sym)

        if not oi_hist or len(oi_hist) < 3:
            return
        if not price_hist or len(price_hist) < 3:
            return

        oi_list = list(oi_hist)
        price_list = list(price_hist)

        # Use last 3 observations minimum
        oi_early = float(np.mean(oi_list[:len(oi_list) // 2])) if len(oi_list) >= 4 else float(oi_list[0])
        oi_late = float(np.mean(oi_list[len(oi_list) // 2:])) if len(oi_list) >= 4 else float(oi_list[-1])
        px_early = float(np.mean(price_list[:len(price_list) // 2])) if len(price_list) >= 4 else float(price_list[0])
        px_late = float(np.mean(price_list[len(price_list) // 2:])) if len(price_list) >= 4 else float(price_list[-1])

        oi_chg = (oi_late - oi_early) / oi_early if oi_early > 0 else 0
        px_chg = (px_late - px_early) / px_early if px_early > 0 else 0

        # OI rising (>5%) but price flat (<2%) — leverage building, squeeze risk
        if oi_chg > 0.05 and abs(px_chg) < 0.02:
            sig.oi_price_divergence = "squeeze_building"
        # Both rising — genuine demand rally
        elif oi_chg > 0.05 and px_chg > 0.02:
            sig.oi_price_divergence = "genuine_demand"
        # Both falling — leverage unwind / capitulation
        elif oi_chg < -0.05 and px_chg < -0.02:
            sig.oi_price_divergence = "leverage_unwind"

    def _fill_composite(self, sig: OrderFlowSignal, ok: list[str]) -> None:
        c = self.config
        contribs: list[tuple[float, float]] = []  # (value[-1,1], weight)

        if "book" in ok:
            contribs.append((float(np.clip(sig.book_imbalance, -1, 1)), c.w_book))
        if "trades" in ok:
            contribs.append(((sig.aggressor_ratio - 0.5) * 2.0, c.w_aggressor))
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

    # Funding-vote normalisation scales (see funding_vote_fixed_scale).
    _FUNDING_SCALE_DEAD = 0.03      # legacy: ~60x too large → vote ≈ 0
    _FUNDING_SCALE_FIXED = 0.0005   # correct: |funding| ≥ 0.05% saturates to ±1

    @staticmethod
    def _resolve_funding_scale(funding_extreme: Optional[float]) -> float:
        """Pick the funding-vote normalisation scale. An explicit value (caller
        / test) wins; otherwise use the fixed 0.0005 scale when
        OF_FUNDING_VOTE_FIXED_SCALE is on, else the legacy dead 0.03 scale so
        confluence stays byte-identical by default."""
        if funding_extreme is not None:
            return funding_extreme
        return (OrderFlowAnalyzer._FUNDING_SCALE_FIXED
                if _env_bool("OF_FUNDING_VOTE_FIXED_SCALE", True)
                else OrderFlowAnalyzer._FUNDING_SCALE_DEAD)

    @staticmethod
    def to_confluence_votes(sig: OrderFlowSignal, funding_extreme: Optional[float] = None) -> tuple[list[float], list[float], list[str]]:
        """Return (votes, weights, labels) so order flow drops straight into
        Analyzer._score_confluence. Votes are graded in [-1, 1]; weights are
        scaled by the signal's data-confidence so a half-empty snapshot counts
        for less.

        funding_extreme overrides the funding-vote normalisation scale; when
        None it is resolved from OF_FUNDING_VOTE_FIXED_SCALE (default OFF → the
        legacy 0.03 scale, byte-identical)."""
        funding_extreme = OrderFlowAnalyzer._resolve_funding_scale(funding_extreme)
        votes: list[float] = []
        weights: list[float] = []
        labels: list[str] = []
        conf = max(0.0, min(1.0, sig.confidence))
        if conf == 0.0:
            return [0.0], [0.1], ["of_neutral"]

        if "book" in sig.components_ok:
            votes.append(float(np.clip(sig.book_imbalance, -1, 1)))
            weights.append(0.8 * conf)
            labels.append("of_book_imbalance")
        if "trades" in sig.components_ok:
            votes.append({"rising": 1.0, "falling": -1.0, "flat": 0.0}.get(sig.cvd_trend, 0.0))
            weights.append(0.9 * conf)
            labels.append("of_cvd_trend")
            votes.append({"accumulation": 1.0, "distribution": -1.0, "neutral": 0.0}.get(sig.whale_bias, 0.0))
            weights.append(1.2 * conf)
            labels.append("of_whale_bias")
        if sig.funding_rate is not None:
            # Extreme funding (>0.05% or <-0.05%) is a stronger contrarian signal
            funding_extreme_threshold = 0.0005
            funding_weight = 0.7 if abs(sig.funding_rate) > funding_extreme_threshold else 0.5
            votes.append(-float(np.clip(sig.funding_rate / funding_extreme, -1, 1)))
            weights.append(funding_weight * conf)
            labels.append("of_funding")

        # CVD-price divergence: strongest microstructure signal
        if sig.cvd_price_divergence != "none":
            if sig.cvd_price_divergence == "bullish_div":
                votes.append(1.0)
            elif sig.cvd_price_divergence == "bearish_div":
                votes.append(-1.0)
            weights.append(1.0 * conf)  # high weight — divergence is meaningful
            labels.append("of_cvd_divergence")

        # Spot vs Futures divergence: structural flow signal
        if sig.spot_futures_divergence != "none":
            if sig.spot_futures_divergence == "spot_led_bullish":
                votes.append(1.0)
            elif sig.spot_futures_divergence == "spec_led_bearish":
                votes.append(-1.0)
            weights.append(1.1 * conf)  # high weight — capital flow divergence
            labels.append("of_spot_futures_div")

        # OI-Price divergence: squeeze and demand detection
        if sig.oi_price_divergence != "none":
            if sig.oi_price_divergence == "genuine_demand":
                # OI + price both rising — confirms the move
                votes.append(1.0)
            elif sig.oi_price_divergence == "leverage_unwind":
                # OI + price both falling — capitulation, contrarian bullish
                votes.append(0.5)
            elif sig.oi_price_divergence == "squeeze_building":
                # OI rising, price flat — direction depends on funding
                # If funding is very positive, longs are crowded → lean bearish
                # If funding is negative, shorts are crowded → lean bullish
                if sig.funding_rate is not None:
                    votes.append(-1.0 if sig.funding_rate > 0.0001 else 1.0)
                else:
                    votes.append(0.0)  # can't determine direction without funding
            weights.append(0.7 * conf)
            labels.append("of_oi_price_div")

        return votes, weights, labels

    def liquidity_guard(
        self,
        sig: OrderFlowSignal,
        position_size_usd: float = 0.0,
        symbol: str = "",
    ) -> Optional[str]:
        """Return a rejection reason if the book is too thin / too wide to
        trade safely, else None. Wire this into RiskEngine as a 17th check.

        The depth threshold scales with position size so micro-test trades
        ($10) are not blocked by a $50K requirement.  Formula:
            effective_threshold = max(position_size_usd * 10, min_book_depth_usd)
        For large-cap pairs (BTC/ETH/SOL) a $50K floor is enforced because
        those books should always carry that depth.

        Fail-OPEN by design: if the book never resolved we cannot judge
        liquidity, so we do not block on missing data here -- the analyzer's
        confidence already reflects that uncertainty elsewhere."""
        if "book" not in sig.components_ok:
            return None
        if sig.spread_bps > self.config.max_spread_bps:
            return (f"LIQUIDITY: spread {sig.spread_bps:.1f}bps > "
                    f"{self.config.max_spread_bps}bps")

        # Determine the effective depth threshold
        min_depth = self.config.min_top_depth_usd
        effective_symbol = symbol or sig.symbol
        if effective_symbol in self.config.LARGE_CAP_SYMBOLS:
            # Large-cap pairs should carry more depth, but scale with position
            min_depth = max(min_depth, 10_000.0)

        if position_size_usd > 0:
            scaled = position_size_usd * 10.0
            effective_threshold = max(scaled, min_depth)
        else:
            effective_threshold = min_depth

        top_depth = min(sig.bid_depth_usd, sig.ask_depth_usd)
        if top_depth < effective_threshold:
            return (f"LIQUIDITY: thin book, min side depth ${top_depth:,.0f} < "
                    f"${effective_threshold:,.0f} (position=${position_size_usd:,.0f})")

        # Top-of-book executable-depth check (opt-in, default OFF; deep-audit
        # medium). The check above sums ~25 levels, which overstates fillable
        # liquidity. When enabled, additionally require the top-N executable
        # depth to cover the position notional — a naturally position-scaled
        # tightening (no separate threshold), so a book that is deep when summed
        # but thin at the top no longer passes.
        if self.config.guard_top_depth_enabled and position_size_usd > 0:
            top_exec = min(sig.bid_depth_top_usd, sig.ask_depth_top_usd)
            if top_exec < position_size_usd:
                return (f"LIQUIDITY: thin top-of-book, executable ${top_exec:,.0f} < "
                        f"position ${position_size_usd:,.0f} "
                        f"(top {self.config.book_top_levels} levels)")
        return None

    # -- Gate 2: Taker 3-Bar Rule --

    @staticmethod
    def _bar_ratio(entry) -> float:
        """Extract the bar ratio from a stored entry. Entries are bare floats
        (legacy / time-bars OFF) or (timestamp, ratio) tuples (time-bars ON)."""
        if isinstance(entry, tuple):
            return float(entry[1])
        return float(entry)

    @staticmethod
    def _bar_ts(entry):
        """Extract the wall-clock timestamp from a stored entry, or None when
        the entry carries no timestamp (legacy bare-float bars)."""
        if isinstance(entry, tuple):
            return float(entry[0])
        return None

    @staticmethod
    def _taker_span_ok(entries, min_span: float, max_span: float) -> tuple:
        """Check the wall-clock span of the last 3 bars. Returns (ok, span).

        Fail-open: if any of the last 3 bars lacks a timestamp (mixed legacy
        data after enabling the flag), span can't be verified, so we don't veto
        a confirmation — (True, None). Otherwise the span (newest - oldest of
        the 3) must fall within [min_span, max_span]: too short = burst, too
        long = stale/gappy."""
        ts = [OrderFlowAnalyzer._bar_ts(e) for e in entries[-3:]]
        if any(t is None for t in ts):
            return True, None
        span = ts[-1] - ts[0]
        return (min_span <= span <= max_span), span

    def check_taker_3bar_gate(self, symbol: str, direction: str) -> dict:
        """Gate 2: Require 3 consecutive taker bars confirming direction.

        For LONG: 3 consecutive bars where buy_vol > sell_vol (ratio > 1.0)
        For SHORT: 3 consecutive bars where sell_vol > buy_vol (ratio < 1.0)

        When time-awareness is enabled (config.time_bars_enabled), the last-3
        window must also span a sane amount of wall-clock time — bursts (too
        short) and stale/gappy windows (too long) no longer confirm a trend.

        Returns dict with: passed, direction, ratios, streak_count, reason.
        """
        with self._lock:
            ratios_deque = self._taker_bar_ratios.get(symbol)
            if not ratios_deque or len(ratios_deque) < 3:
                # Fail-OPEN on missing data: bars accrue one per scan, so a
                # freshly scanned symbol has <3 bars for its first ~3 scans.
                # Fail-closed here made every new symbol untradeable (and, at
                # scan cadence, most symbols most of the time). The gate still
                # REJECTS when 3 bars exist and actively misalign.
                n = len(ratios_deque) if ratios_deque else 0
                return {
                    "passed": True,
                    "direction": direction,
                    "ratios": [round(self._bar_ratio(e), 3) for e in ratios_deque] if ratios_deque else [],
                    "streak_count": 0,
                    "reason": f"insufficient taker data ({n}/3 bars) — fail-open",
                }
            entries = list(ratios_deque)

        ratios = [self._bar_ratio(e) for e in entries]
        last_3 = ratios[-3:]
        dir_upper = direction.upper()

        if dir_upper == "LONG":
            # Count from the end until broken
            actual_streak = 0
            for r in reversed(ratios):
                if r > 1.0:
                    actual_streak += 1
                else:
                    break
            confirmed = all(r > 1.0 for r in last_3)
        else:  # SHORT
            actual_streak = 0
            for r in reversed(ratios):
                if r < 1.0:
                    actual_streak += 1
                else:
                    break
            confirmed = all(r < 1.0 for r in last_3)

        reason = "3-bar taker gate confirmed" if confirmed else (
            f"taker flow not aligned: last 3 ratios {[round(r, 3) for r in last_3]}"
        )

        # Time-awareness: a confirmed streak can still be vetoed if the 3-bar
        # window spans too little (burst) or too much (stale/gappy) wall-clock.
        if confirmed and self.config.time_bars_enabled:
            span_ok, span = self._taker_span_ok(
                entries,
                self.config.taker_bar_min_span_sec,
                self.config.taker_bar_max_span_sec,
            )
            if not span_ok:
                confirmed = False
                lo = self.config.taker_bar_min_span_sec
                hi = self.config.taker_bar_max_span_sec
                kind = "burst" if span is not None and span < lo else "stale/gappy"
                reason = (
                    f"taker 3-bar span {span:.0f}s outside [{lo:.0f},{hi:.0f}]s "
                    f"({kind}); confirmation rejected"
                )

        return {
            "passed": confirmed,
            "direction": dir_upper,
            "ratios": [round(r, 3) for r in last_3],
            "streak_count": actual_streak,
            "reason": reason,
        }

    # -- Rule 20: Bid Dominance Gate --

    def check_bid_dominance(self, sig: OrderFlowSignal, direction: str) -> dict:
        """Rule 20: require book-side dominance in the TRADE direction —
        bids for LONG, asks for SHORT. (Previously LONG-only, giving shorts a
        structural free pass, and it compared the full ~25-level sums that the
        module header itself calls trivially spoofable — the top-N executable
        depth is used now, falling back to the full sums when top-N is absent.)

        Returns dict with: passed, bid_ask_ratio, required_ratio, reason.
        bid_ask_ratio is always favored-side : opposing-side.
        """
        dir_upper = direction.upper()
        required = self.config.dominance_required_ratio

        if "book" not in sig.components_ok:
            return {
                "passed": False,
                "bid_ask_ratio": None,
                "required_ratio": required,
                "reason": "order book data unavailable (fail-closed)",
            }

        bid_depth = sig.bid_depth_top_usd if sig.bid_depth_top_usd > 0 else sig.bid_depth_usd
        ask_depth = sig.ask_depth_top_usd if sig.ask_depth_top_usd > 0 else sig.ask_depth_usd
        if dir_upper == "LONG":
            fav, opp, side = bid_depth, ask_depth, "bid"
        else:
            fav, opp, side = ask_depth, bid_depth, "ask"

        ratio = (fav / opp) if opp > 0 else 0.0
        passed = ratio >= required

        return {
            "passed": passed,
            "bid_ask_ratio": round(ratio, 2),
            "required_ratio": required,
            "reason": (
                f"{side} dominance {ratio:.2f}:1 confirmed (top-depth)"
                if passed else
                f"{side} side ratio {ratio:.2f}:1 < {required:.1f}:1 required"
            ),
        }

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
        # NOTE: Pruning is FIFO (oldest-inserted symbols evicted first), not LRU.
        # LRU would require tracking access timestamps per symbol across all stores,
        # adding complexity and lock contention for negligible benefit — the stores
        # are bounded by max_tracked_symbols and rarely hit the limit in practice.
        with self._lock:
            for store in (self._cvd_history, self._price_history, self._oi_history,
                          self._taker_bar_ratios, self._spot_vol_history,
                          self._oi_val_history, self._price_snap_history):
                if len(store) > self.config.max_tracked_symbols:
                    for k in list(store)[:-self.config.max_tracked_symbols]:
                        del store[k]
