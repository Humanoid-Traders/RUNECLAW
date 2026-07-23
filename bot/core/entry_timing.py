"""
RUNECLAW — Entry-timing engine (degree-nested confirmation).

The live diagnosis (292 trades) puts the bot's one measured bleed at
stop-outs: entries fire the moment a signal qualifies, which in a
pullback-driven system means EARLY — and early on a leveraged perp is
indistinguishable from wrong, because the stop gets tagged before the
thesis plays out (SL bucket −$264 vs TP +$167).

This module turns a qualified idea into an ARMED SETUP instead of an
instant order. The setup fires only when the sub-degree confirms the
turn; it disarms silently when the invalidation level (the idea's own
stop) is touched first, or when the validity window expires — a trade
that never happened instead of a stop-out that did.

Confirmation doctrine (per direction, on the sub-degree series):
  1. STRUCTURE: the most recent confirmed ATR-ZigZag pivot is a swing
     LOW for a LONG (the pullback leg ended and reversed by >= 1.5xATR —
     confirmed by construction, no repaint); swing HIGH for a SHORT.
  2. TRIGGER: the last CLOSED candle is a with-trade momentum bar —
     for a LONG it closes green above the prior bar's high (buyers took
     out the previous bar); mirror for a SHORT.

Pure math on plain arrays/floats — no I/O, no config reads. Callers
(backtest engine now; live engine in stage 2) own persistence, candle
supply and the actual order placement.
"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

# Verdicts returned by evaluate_armed()
FIRE = "fire"
WAIT = "wait"
DISARM_INVALIDATED = "disarm_invalidated"
DISARM_EXPIRED = "disarm_expired"


def timing_active(regime: str = "") -> bool:
    """Whether entry-timing confirmation gates ideas in this market regime.

    True when the global flag is ON (all regimes), or when ``regime`` is in
    the ENTRY_TIMING_REGIMES set (csv). PR #359's pre-registered A/B split
    cleanly by regime — timing HELPED the losing/choppy cells and HURT the
    winning/trending ones — so the regime-conditional variant enables the
    confirmation gate only where it measured well, instead of the
    all-or-nothing global flag. Empty regime string only matches the
    global flag (never a regime set)."""
    from bot.config import CONFIG
    if CONFIG.execution.entry_timing_enabled:
        return True
    regs = str(getattr(CONFIG.execution, "entry_timing_regimes", "") or "")
    if not regs or not regime:
        return False
    return regime.strip().upper() in {
        r.strip().upper() for r in regs.split(",") if r.strip()}


def subdegree_turn_confirmed(
    direction: str,
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    opens: Optional[Sequence[float]] = None,
    zigzag_atr_mult: float = 1.5,
) -> tuple[bool, str]:
    """True when the sub-degree series confirms the turn in ``direction``.

    Requires BOTH the structural condition (newest confirmed ZigZag pivot
    opposes the pullback: a low for LONG, a high for SHORT) and the
    trigger candle (last closed bar takes out the prior bar's extreme in
    the trade direction). Returns (confirmed, reason).
    """
    try:
        from bot.core.elliott import atr_zigzag_pivots

        n = len(closes)
        if n < 10:
            return False, "insufficient sub-degree history"
        h = np.asarray(highs, dtype=float)
        lo = np.asarray(lows, dtype=float)
        c = np.asarray(closes, dtype=float)
        piv = atr_zigzag_pivots(h, lo, c, atr_mult=zigzag_atr_mult)
        swing_highs = piv.get("swing_highs") or []
        swing_lows = piv.get("swing_lows") or []
        if not (swing_highs or swing_lows):
            return False, "no confirmed structural pivot yet"
        last_high_i = swing_highs[-1][0] if swing_highs else -1
        last_low_i = swing_lows[-1][0] if swing_lows else -1

        is_long = direction == "LONG"
        # 1. STRUCTURE: newest pivot must oppose the pullback.
        if is_long and last_low_i <= last_high_i:
            return False, "sub-degree pullback low not confirmed yet"
        if (not is_long) and last_high_i <= last_low_i:
            return False, "sub-degree pullback high not confirmed yet"

        # 2. TRIGGER: last closed bar takes out the prior bar's extreme.
        o_last = float(opens[-1]) if opens is not None else float(c[-2])
        if is_long:
            if c[-1] > o_last and c[-1] > h[-2]:
                return True, "pullback low confirmed + bullish trigger bar"
            return False, "structure turned, awaiting bullish trigger bar"
        if c[-1] < o_last and c[-1] < lo[-2]:
            return True, "pullback high confirmed + bearish trigger bar"
        return False, "structure turned, awaiting bearish trigger bar"
    except Exception:  # noqa: BLE001 — a timing helper must never raise
        return False, "confirmation check error"


def auto_entry_allowed(
    regime: str,
    direction: str,
    opens: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
) -> tuple[bool, str]:
    """Fail-safe gate for AUTONOMOUS (auto-confirm) entries — the live analog of
    the backtest's armed-setup arming, scoped to ENTRY_TIMING_REGIMES.

    Returns ``(allowed, reason)``. When the timing gate is active for this
    regime, an autonomous entry is allowed only once the sub-degree confirms
    the turn; otherwise it is DEFERRED (the engine re-checks on a later scan).
    A human who explicitly confirms is never routed through this gate.

    Fail-open by construction: an inactive gate, too little history, or ANY
    error returns ``allowed=True`` — the gate can never block trading on a bug.
    """
    try:
        if not timing_active(regime or ""):
            return True, "timing gate inactive for this regime"
        if not closes or len(closes) < 10:
            return True, "fail-safe: insufficient sub-degree history"
        confirmed, reason = subdegree_turn_confirmed(direction, highs, lows, closes, opens=opens)
        return (bool(confirmed), reason)
    except Exception:  # noqa: BLE001 — never block an entry on a timing bug
        return True, "fail-safe: timing check error"


def invalidated(direction: str, stop_loss: float, bar_high: float,
                bar_low: float) -> bool:
    """True when the would-be stop was touched while still ARMED — the
    setup was wrong before it ever became a position."""
    if stop_loss <= 0:
        return False
    if direction == "LONG":
        return bar_low <= stop_loss
    return bar_high >= stop_loss


def evaluate_armed(
    direction: str,
    stop_loss: float,
    armed_ts: float,
    now_ts: float,
    max_wait_sec: float,
    bar_high: float,
    bar_low: float,
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    opens: Optional[Sequence[float]] = None,
    zigzag_atr_mult: float = 1.5,
) -> tuple[str, str]:
    """One evaluation tick for an armed setup.

    Order of precedence (pessimistic, mirrors the stop-first convention):
      1. invalidation — the stop level was touched while armed;
      2. expiry — the validity window ran out;
      3. confirmation — structure + trigger fire the entry;
      4. wait.
    Returns (verdict, reason) with verdict one of FIRE / WAIT /
    DISARM_INVALIDATED / DISARM_EXPIRED.
    """
    if invalidated(direction, stop_loss, bar_high, bar_low):
        return DISARM_INVALIDATED, (
            f"invalidation touched while armed (stop {stop_loss})")
    if max_wait_sec > 0 and now_ts - armed_ts > max_wait_sec:
        return DISARM_EXPIRED, (
            f"validity window expired ({max_wait_sec:.0f}s)")
    ok, reason = subdegree_turn_confirmed(
        direction, highs, lows, closes, opens=opens,
        zigzag_atr_mult=zigzag_atr_mult)
    if ok:
        return FIRE, reason
    return WAIT, reason
