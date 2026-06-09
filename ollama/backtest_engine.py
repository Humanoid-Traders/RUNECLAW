"""
RUNECLAW Backtest — Execution Engine
The heart of the harness. Takes a trade idea (entry/SL/TP/direction) and the
FORWARD price bars the model never saw, and simulates what actually happened —
honestly.

Design principles (every one of these exists to prevent a lying backtest):

1. LOOK-AHEAD PROTECTION
   A trade decided at bar T is executed only on bars > T. The engine never
   peeks at the decision bar's close to fill.

2. CONSERVATIVE INTRABAR RESOLUTION
   If a single bar's range touches BOTH stop and target, we cannot know from
   OHLC which came first. We assume the STOP hit first (the worse outcome).
   This is the standard anti-optimism rule. A backtest that assumes TP-first
   is fantasy.

3. REALISTIC FILLS
   - Entry is not guaranteed at the exact quoted price. We model entry slippage.
   - A limit entry that price never reaches = no trade (not a free fill).
   - Fees charged on both entry and exit (round-trip taker cost).

4. EXPLICIT COSTS
   fee_bps and slippage_bps are first-class. Small costs compound; a strategy
   that's profitable at 0 bps and dead at 10 bps was never profitable.

5. TIMEOUT
   A trade that neither stops nor targets within max_hold_bars is closed at
   market — open trades don't get to dangle as unrealized hope.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class Outcome(str, Enum):
    TARGET   = "target"        # hit take-profit
    STOP     = "stop"          # hit stop-loss
    TIMEOUT  = "timeout"       # closed at max hold
    NO_FILL  = "no_fill"       # entry never reached
    NO_DATA  = "no_data"       # no forward bars to test


@dataclass
class TradeIdea:
    """Structured trade the model produced. Prices in quote currency."""
    symbol: str
    direction: str             # "LONG" | "SHORT"
    entry_price: float
    stop_loss: float
    take_profit: float
    decision_ts: pd.Timestamp  # the bar the model decided on; fill happens AFTER
    confidence: float = 0.0
    entry_type: str = "market" # "market" fills next bar; "limit" waits for price
    risk_pct: float = 2.0      # % of equity risked (RUNECLAW max 2%)


@dataclass
class TradeResult:
    idea: TradeIdea
    outcome: Outcome
    entry_fill: Optional[float] = None
    exit_fill: Optional[float] = None
    entry_ts: Optional[pd.Timestamp] = None
    exit_ts: Optional[pd.Timestamp] = None
    bars_held: int = 0
    gross_return_pct: float = 0.0   # before costs, on the position
    net_return_pct: float = 0.0     # after fees + slippage
    pnl_quote: float = 0.0          # absolute P&L given position size
    position_size_quote: float = 0.0
    r_multiple: float = 0.0         # P&L in units of initial risk (the honest metric)
    cost_quote: float = 0.0
    notes: str = ""


@dataclass
class CostModel:
    fee_bps: float = 5.0        # per side, basis points (5 bps = 0.05%, ~taker)
    slippage_bps: float = 3.0   # entry/exit slippage, basis points
    # Slippage always works against you: worse entry, worse exit.


def _apply_slippage(price: float, direction: str, is_entry: bool,
                    slippage_bps: float) -> float:
    """Slippage always hurts. Buy fills higher, sell fills lower."""
    slip = price * slippage_bps / 10_000
    if direction == "LONG":
        # entry = buy (fill higher), exit = sell (fill lower)
        return price + slip if is_entry else price - slip
    else:  # SHORT
        # entry = sell (fill lower), exit = buy (fill higher)
        return price - slip if is_entry else price + slip


def simulate_trade(idea: TradeIdea, forward_bars: pd.DataFrame,
                   equity: float, costs: CostModel,
                   max_hold_bars: int = 200) -> TradeResult:
    """
    Simulate one trade against the bars that came AFTER the decision.
    forward_bars: OHLCV indexed by timestamp, all strictly after decision_ts.
    """
    res = TradeResult(idea=idea, outcome=Outcome.NO_DATA)

    if forward_bars is None or len(forward_bars) == 0:
        res.notes = "no forward bars after decision"
        return res

    direction = idea.direction.upper()
    if direction not in ("LONG", "SHORT"):
        res.outcome = Outcome.NO_DATA
        res.notes = f"invalid direction {idea.direction}"
        return res

    # ── Validate geometry; a malformed idea can't be honestly simulated ──
    ep, sl, tp = idea.entry_price, idea.stop_loss, idea.take_profit
    if direction == "LONG" and not (sl < ep < tp):
        res.notes = f"invalid LONG geometry SL={sl} EP={ep} TP={tp}"
        return res
    if direction == "SHORT" and not (sl > ep > tp):
        res.notes = f"invalid SHORT geometry SL={sl} EP={ep} TP={tp}"
        return res

    bars = forward_bars.iloc[:max_hold_bars]

    # ── 1. ENTRY ─────────────────────────────────────────────────────────
    entry_fill = None
    entry_ts = None
    entry_offset = 0

    if idea.entry_type == "market":
        # Fill at NEXT bar's open (can't fill on the decision bar — look-ahead)
        first = bars.iloc[0]
        entry_fill = _apply_slippage(first["open"], direction, True, costs.slippage_bps)
        entry_ts = bars.index[0]
        entry_offset = 0
    else:
        # Limit: wait for price to reach entry. If it never does, no fill.
        for i, (ts, bar) in enumerate(bars.iterrows()):
            touched = (bar["low"] <= ep <= bar["high"])
            if touched:
                entry_fill = _apply_slippage(ep, direction, True, costs.slippage_bps)
                entry_ts = ts
                entry_offset = i
                break
        if entry_fill is None:
            res.outcome = Outcome.NO_FILL
            res.notes = "limit entry never reached"
            return res

    # ── 2. POSITION SIZE from risk rule ──────────────────────────────────
    # Risk = entry-to-stop distance. Size so that a stop-out loses risk_pct.
    risk_per_unit = abs(entry_fill - sl)
    if risk_per_unit <= 0:
        res.notes = "zero risk distance"
        return res
    risk_quote = equity * idea.risk_pct / 100
    units = risk_quote / risk_per_unit
    position_size = units * entry_fill
    res.position_size_quote = position_size

    # ── 3. WALK FORWARD bar by bar to find STOP / TARGET / TIMEOUT ───────
    exit_fill = None
    exit_ts = None
    outcome = Outcome.TIMEOUT
    walk = bars.iloc[entry_offset:]

    for i, (ts, bar) in enumerate(walk.iterrows()):
        if i == 0 and idea.entry_type == "market":
            # On the entry bar itself, only allow stop/target if the bar's
            # range crosses them AFTER a market open fill. Conservative: check both.
            pass

        hi, lo = bar["high"], bar["low"]

        if direction == "LONG":
            hit_stop   = lo <= sl
            hit_target = hi >= tp
        else:
            hit_stop   = hi >= sl
            hit_target = lo <= tp

        # CONSERVATIVE INTRABAR RULE: if both in one bar, assume STOP first.
        if hit_stop and hit_target:
            exit_fill = _apply_slippage(sl, direction, False, costs.slippage_bps)
            exit_ts = ts
            outcome = Outcome.STOP
            res.notes = "ambiguous bar — assumed stop-first (conservative)"
            break
        elif hit_stop:
            exit_fill = _apply_slippage(sl, direction, False, costs.slippage_bps)
            exit_ts = ts
            outcome = Outcome.STOP
            break
        elif hit_target:
            exit_fill = _apply_slippage(tp, direction, False, costs.slippage_bps)
            exit_ts = ts
            outcome = Outcome.TARGET
            break

    if exit_fill is None:
        # Timeout: close at last available bar's close
        last_ts = walk.index[-1]
        last_close = walk.iloc[-1]["close"]
        exit_fill = _apply_slippage(last_close, direction, False, costs.slippage_bps)
        exit_ts = last_ts
        outcome = Outcome.TIMEOUT

    # ── 4. P&L with costs ────────────────────────────────────────────────
    if direction == "LONG":
        gross_per_unit = exit_fill - entry_fill
    else:
        gross_per_unit = entry_fill - exit_fill

    gross_pnl = gross_per_unit * units
    # Fees on both legs, charged on notional
    entry_notional = entry_fill * units
    exit_notional  = exit_fill * units
    fees = (entry_notional + exit_notional) * costs.fee_bps / 10_000
    net_pnl = gross_pnl - fees

    res.outcome = outcome
    res.entry_fill = entry_fill
    res.exit_fill = exit_fill
    res.entry_ts = entry_ts
    res.exit_ts = exit_ts
    res.bars_held = len(walk[:walk.index.get_loc(exit_ts) + 1]) if exit_ts in walk.index else len(walk)
    res.gross_return_pct = (gross_per_unit / entry_fill) * 100
    res.cost_quote = fees
    res.pnl_quote = net_pnl
    res.net_return_pct = (net_pnl / position_size) * 100 if position_size else 0.0
    # R-multiple: net P&L divided by the dollar amount risked. The honest unit.
    res.r_multiple = net_pnl / risk_quote if risk_quote else 0.0
    return res


def run_portfolio(ideas: list[TradeIdea], data_by_symbol: dict,
                  starting_equity: float = 10_000.0,
                  costs: CostModel = None, max_hold_bars: int = 200,
                  compound: bool = True) -> list[TradeResult]:
    """
    Run a sequence of trade ideas as a portfolio, updating equity as trades
    close. Trades are processed in decision-timestamp order.

    data_by_symbol: {symbol: OHLCVData}
    compound: if True, position sizing uses current (updated) equity.
    """
    costs = costs or CostModel()
    ordered = sorted(ideas, key=lambda x: x.decision_ts)
    equity = starting_equity
    results = []

    for idea in ordered:
        data = data_by_symbol.get(idea.symbol)
        if data is None:
            r = TradeResult(idea=idea, outcome=Outcome.NO_DATA, notes="no data for symbol")
            results.append(r)
            continue
        forward = data.slice_after(idea.decision_ts)
        size_equity = equity if compound else starting_equity
        r = simulate_trade(idea, forward, size_equity, costs, max_hold_bars)
        if r.outcome not in (Outcome.NO_FILL, Outcome.NO_DATA):
            equity += r.pnl_quote
        results.append(r)

    return results


if __name__ == "__main__":
    # Self-test: a known LONG that should hit target, with and without costs
    from backtest_data import synth_ohlcv
    data = synth_ohlcv(n_bars=500, regime="trend_up", seed=1)
    px = data.df["close"].iloc[100]
    decision_ts = data.df.index[100]

    idea = TradeIdea(
        symbol="TEST/USDT", direction="LONG",
        entry_price=px, stop_loss=px * 0.97, take_profit=px * 1.06,
        decision_ts=decision_ts, confidence=0.7, risk_pct=2.0,
    )
    forward = data.slice_after(decision_ts)

    no_cost = simulate_trade(idea, forward, 10_000, CostModel(0, 0))
    with_cost = simulate_trade(idea, forward, 10_000, CostModel(5, 3))
    print(f"No costs : {no_cost.outcome.value:8} R={no_cost.r_multiple:+.2f}  net%={no_cost.net_return_pct:+.2f}")
    print(f"5/3 bps  : {with_cost.outcome.value:8} R={with_cost.r_multiple:+.2f}  net%={with_cost.net_return_pct:+.2f}  cost=${with_cost.cost_quote:.2f}")
    print(f"Cost drag on R: {no_cost.r_multiple - with_cost.r_multiple:.3f}")
