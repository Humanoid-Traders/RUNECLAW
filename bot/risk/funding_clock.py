"""
RUNECLAW — funding-clock and liquidation-cascade timing (pure math).

Two timing edges perps have that spot doesn't:

FUNDING CLOCK — USDT perps settle funding every 8h (00/08/16 UTC on
Bitget). Entering minutes before settlement on the PAYING side of an
extreme rate is a measurable head start given away: the position pays
immediately, and extreme funding marks crowded positioning that tends to
unwind around the settle. The gate blocks only that narrow case — paying
side + extreme rate + inside the pre-settle window. Everything else
passes; missing funding data passes (fail-open). Every block lands in
the shadow book, so the gate's price tag accrues from day one.

LIQUIDATION CASCADE — a bar whose range and volume both explode (forced
liquidations flushing a liquidity pool) is a terrible bar to CHASE: entry
in the flush direction fills at the extreme of a move that mean-reverts
more often than it continues once the forced flow is spent. The veto
blocks with-cascade-direction entries for a few bars after the flush;
counter-trend entries (fading the cascade) stay allowed. OHLCV-only, so
it backtests on the frozen benchmarks.
"""

from __future__ import annotations

from typing import Optional, Sequence

SETTLEMENT_INTERVAL_SEC = 8 * 3600.0  # Bitget USDT perps: 00/08/16 UTC


def seconds_to_settlement(now_ts: float) -> float:
    """Seconds until the next 8h funding settlement (UTC-aligned)."""
    return SETTLEMENT_INTERVAL_SEC - (float(now_ts) % SETTLEMENT_INTERVAL_SEC)


def pays_funding(direction: str, funding_rate: float) -> bool:
    """Whether this side PAYS the current funding rate.
    Positive rate: longs pay shorts. Negative: shorts pay longs."""
    if funding_rate > 0:
        return direction == "LONG"
    if funding_rate < 0:
        return direction == "SHORT"
    return False


def funding_clock_verdict(direction: str,
                          funding_rate: Optional[float],
                          now_ts: float,
                          window_sec: float = 1800.0,
                          extreme_rate: float = 0.0005) -> tuple[bool, str]:
    """(blocked, reason) for the funding-clock gate.

    Blocks ONLY when all three hold: inside the pre-settlement window,
    |rate| at/above the extreme threshold, and the trade is on the paying
    side. Missing funding data never blocks (fail-open)."""
    if funding_rate is None:
        return False, "no funding data (skip)"
    rate = float(funding_rate)
    secs = seconds_to_settlement(now_ts)
    if secs > window_sec:
        return False, f"settlement {secs / 60:.0f}m away"
    if abs(rate) < extreme_rate:
        return False, f"funding {rate:+.4%} below extreme threshold"
    if not pays_funding(direction, rate):
        return False, f"extreme funding {rate:+.4%} is paid TO this side"
    return True, (f"would pay extreme funding {rate:+.4%} settling in "
                  f"{secs / 60:.0f}m — re-enter after the settle")


def cascade_state(highs: Sequence[float], lows: Sequence[float],
                  closes: Sequence[float], volumes: Sequence[float],
                  atr: float,
                  range_atr_mult: float = 2.5,
                  vol_mult: float = 3.0,
                  recent_bars: int = 3,
                  baseline_bars: int = 20) -> dict:
    """Detect a liquidation-cascade bar in the last ``recent_bars`` CLOSED
    bars: range >= range_atr_mult x ATR AND volume >= vol_mult x the
    average of the preceding ``baseline_bars``. Direction is the flush
    direction (close vs open proxy: close vs prior close).

    Returns {"cascade": bool, "direction": "UP"|"DOWN"|"", "bars_ago": int}.
    Pure; degenerate input (short series, zero ATR/volume) → no cascade."""
    out = {"cascade": False, "direction": "", "bars_ago": -1}
    try:
        n = len(closes)
        if (n < baseline_bars + recent_bars + 1 or atr is None or atr <= 0
                or len(highs) != n or len(lows) != n or len(volumes) != n):
            return out
        for bars_ago in range(1, recent_bars + 1):
            i = n - bars_ago
            rng = float(highs[i]) - float(lows[i])
            base = [float(v) for v in volumes[i - baseline_bars:i]]
            avg_vol = sum(base) / len(base) if base else 0.0
            if avg_vol <= 0:
                continue
            if rng >= range_atr_mult * atr and float(volumes[i]) >= vol_mult * avg_vol:
                direction = "UP" if float(closes[i]) >= float(closes[i - 1]) else "DOWN"
                return {"cascade": True, "direction": direction,
                        "bars_ago": bars_ago}
        return out
    except Exception:
        return out


def cascade_veto(direction: str, state: dict) -> Optional[str]:
    """Veto reason when the entry CHASES a recent cascade (same direction
    as the flush), else None. Fading the cascade is never vetoed."""
    if not state or not state.get("cascade"):
        return None
    flush = state.get("direction", "")
    if (direction == "LONG" and flush == "UP") or \
            (direction == "SHORT" and flush == "DOWN"):
        return (f"chasing a liquidation cascade ({flush} flush "
                f"{state.get('bars_ago', '?')} bar(s) ago) — forced flow, "
                f"entry at the extreme")
    return None
