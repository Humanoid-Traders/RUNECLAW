"""Read-only position telemetry.

Surfaces the geometry the engine already computes internally — trail stage +
next-trigger threshold, ATR%, liquidation distance, limit-expiry countdown, and
an exchange-sourced "did the trail actually fire" check — formatted to match the
external Playbook readout so the two line up.

PURE by design: every function takes primitives and returns numbers/strings.
No I/O, no clock reads (callers pass timestamps), and nothing here can touch the
order path — so it is safe to surface on a live account.

The trail geometry mirrors bot/utils/trailing.py exactly (multi-stage:
Stage 1 @ 1R, Stage 2 @ 2R, Stage 3 @ 3R), so the threshold/stage shown is what
the bot will actually do.
"""

from __future__ import annotations

from typing import Optional

# R-multiple of favorable profit required to ENTER each trail stage — must match
# bot/utils/trailing.py _STAGES r_threshold values.
_STAGE_R: tuple[float, ...] = (0.0, 1.0, 2.0, 3.0)
_MAX_STAGE = len(_STAGE_R) - 1


def initial_risk(entry_price: float, stop_loss: float) -> float:
    """1R, the absolute entry→initial-SL price distance."""
    return abs(entry_price - stop_loss)


def profit_in_r(direction: str, entry_price: float, mark: float, init_risk: float) -> float:
    """Current favorable profit expressed in R-multiples (negative = underwater)."""
    if init_risk <= 0:
        return 0.0
    profit = (mark - entry_price) if direction == "LONG" else (entry_price - mark)
    return profit / init_risk


def current_stage(profit_r: float) -> int:
    """Trail stage (0..3) for a given profit-in-R."""
    stage = 0
    for s in range(1, _MAX_STAGE + 1):
        if profit_r >= _STAGE_R[s]:
            stage = s
    return stage


def next_stage_threshold(direction: str, entry_price: float, init_risk: float,
                         stage: int) -> Optional[float]:
    """Mark price at which the NEXT trail stage activates (profit reaches the next
    R threshold), or None if already at the final stage / risk unknown."""
    if stage >= _MAX_STAGE or init_risk <= 0:
        return None
    next_r = _STAGE_R[stage + 1]
    if direction == "LONG":
        return entry_price + next_r * init_risk
    return entry_price - next_r * init_risk


def threshold_gap(direction: str, mark: float, threshold: Optional[float]) -> Optional[float]:
    """Favorable distance the mark must still travel to reach `threshold`.

    Positive ⇒ threshold not yet reached (trail not demanded). For LONG the mark
    must rise to the threshold; for SHORT it must fall to it.
    """
    if threshold is None:
        return None
    return (threshold - mark) if direction == "LONG" else (mark - threshold)


def atr_pct(atr: float, mark: float) -> Optional[float]:
    """ATR as a percentage of mark price."""
    if atr <= 0 or mark <= 0:
        return None
    return atr / mark * 100.0


def liq_distance_pct(mark: float, liq_price: Optional[float]) -> Optional[float]:
    """Distance from mark to the liquidation price, as a percent of mark."""
    if not liq_price or liq_price <= 0 or mark <= 0:
        return None
    return abs(liq_price - mark) / mark * 100.0


def expiry_remaining_seconds(opened_at_ts: float, expire_seconds: float,
                             now_ts: float) -> float:
    """Seconds until a limit order's expiry (negative once past it)."""
    return (opened_at_ts + expire_seconds) - now_ts


def sl_trail_fired(sl_update_ms: Optional[float], created_ms: Optional[float],
                   tol_ms: float = 5000.0) -> Optional[bool]:
    """Whether the exchange SL order's update time is meaningfully later than the
    position-creation time — i.e. the trail actually moved the stop on the venue.

    Mirrors the Playbook's Δ(uTime−cTime) check. Returns None when either
    timestamp is unavailable. A small delta ⇒ the SL was placed at open and has
    not been re-issued ⇒ trail has NOT fired.
    """
    if not sl_update_ms or not created_ms:
        return None
    return (sl_update_ms - created_ms) > tol_ms


def trail_read(direction: str, entry_price: float, stop_loss: float, mark: float,
               *, atr: float = 0.0) -> dict:
    """Full trail geometry for a position. Pure; mirrors update_trailing_stop."""
    init = initial_risk(entry_price, stop_loss)
    pr = profit_in_r(direction, entry_price, mark, init)
    stage = current_stage(pr)
    threshold = next_stage_threshold(direction, entry_price, init, stage)
    gap = threshold_gap(direction, mark, threshold)
    active = stage >= 1

    if active:
        verdict = f"TRAILING ACTIVE — stage {stage} (SL tightening)"
    elif threshold is None:
        verdict = "MAX STAGE"
    elif gap is not None and gap > 0:
        verdict = "GEOMETRY NOT DEMANDED — frozen SL correct, not a valid test"
    else:
        verdict = "AT THRESHOLD — trail arming"

    return {
        "stage": stage,
        "profit_r": pr,
        "active": active,
        "next_threshold": threshold,
        "gap": gap,
        "atr_pct": atr_pct(atr, mark),
        "verdict": verdict,
    }


# ── Playbook-style formatting ────────────────────────────────────────────────

def format_trail_read(read: dict) -> list[str]:
    """Render trail_read() output as Playbook-style lines."""
    lines = ["🔷 TRAIL READ"]
    thr = read.get("next_threshold")
    gap = read.get("gap")
    ap = read.get("atr_pct")
    lines.append(f"- Stage: {read['stage']} | Profit: {read['profit_r']:+.2f}R"
                 + (f" | ATR ~{ap:.2f}%" if ap is not None else ""))
    if thr is not None:
        lines.append(f"- Next trigger: ${thr:,.4f}")
    if gap is not None:
        side = "ABOVE" if gap >= 0 else "BELOW"
        lines.append(f"- Gap: {gap:+.4f} ({side} threshold)")
    lines.append(f"- VERDICT: {read['verdict']}")
    return lines


def format_expiry(remaining_s: float) -> str:
    """Human countdown for a limit-order expiry (e.g. '~34m to expiry')."""
    if remaining_s <= 0:
        return "⏰ EXPIRED — pending cancel"
    mins = int(remaining_s // 60)
    if mins < 60:
        return f"⚠️ ~{mins}m to expiry"
    return f"~{mins // 60}h {mins % 60}m to expiry"


def format_trail_fired(fired: Optional[bool]) -> str:
    """Render the SL uTime trail-fired check."""
    if fired is None:
        return "Trail-fired: — (SL update time unavailable)"
    return "Trail-fired: ✅ SL re-issued by trail" if fired else \
        "Trail-fired: ❄️ NOT FIRED (SL unchanged since open)"
