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


def atr_from_candles(highs: list, lows: list, closes: list, period: int = 14) -> float:
    """Wilder ATR (last value) from OHLC arrays — pure.

    Lets /livepositions feed a ROLLING ATR (recomputed off live candles) into
    trail_read so the threshold drifts tick-for-tick like the Playbook, instead
    of the static atr_at_entry. Matches the analyzer's Wilder smoothing.
    """
    n = len(closes)
    if n < 2:
        return 0.0
    trs = []
    for i in range(1, n):
        high, low, prev_close = highs[i], lows[i], closes[i - 1]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    if not trs:
        return 0.0
    if len(trs) < period:
        return sum(trs) / len(trs)
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def playbook_trail_threshold(direction: str, sl_trigger: float,
                             atr_pct_frac: Optional[float]) -> Optional[float]:
    """The mark price at which the trail ratchet is DEMANDED, per the Playbook's
    geometry: ratchet when the mark has moved 2·ATR past the current SL.

        SHORT: mark + 2·ATR < SL  →  threshold = SL / (1 + 2·ATR_pct)
        LONG:  mark − 2·ATR > SL  →  threshold = SL / (1 − 2·ATR_pct)

    ATR_pct is ATR as a FRACTION of the live mark (rolling), so the threshold
    drifts tick-to-tick. Returns None if ATR% is unavailable, or (LONG) if
    2·ATR_pct ≥ 1 which would make the geometry degenerate.
    """
    if atr_pct_frac is None or sl_trigger <= 0:
        return None
    if direction == "SHORT":
        return sl_trigger / (1.0 + 2.0 * atr_pct_frac)
    denom = 1.0 - 2.0 * atr_pct_frac
    if denom <= 0:
        return None
    return sl_trigger / denom


def playbook_gap(direction: str, mark: float, threshold: Optional[float]) -> Optional[float]:
    """Favorable distance from the mark to the ratchet threshold.

    Positive ⇒ the threshold has NOT been reached (ratchet not demanded). For a
    SHORT the mark sits ABOVE the (lower) threshold; for a LONG it sits BELOW
    the (higher) threshold.
    """
    if threshold is None:
        return None
    return (mark - threshold) if direction == "SHORT" else (threshold - mark)


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


def trail_engine_read(trailing_state: Optional[dict]) -> dict:
    """Read the engine's AUTHORITATIVE trail state (set by update_trailing_stop).

    This is the bot's own record of whether the trail has activated and moved
    the SL — the honest "did my trail fire". The engine cancel+replaces the SL
    on each trail step, so the exchange order's own uTime−cTime delta (what the
    Playbook reads) would NOT reveal it here; the engine flag does.

    Returns fired=None when there is no trailing state (e.g. trailing disabled).
    """
    if not trailing_state:
        return {"fired": None, "stage": None, "best_price": None}
    return {
        "fired": bool(trailing_state.get("trailing_active")),
        "stage": trailing_state.get("stage"),
        "best_price": trailing_state.get("best_price"),
    }


def trail_read(direction: str, entry_price: float, stop_loss: float, mark: float,
               *, atr: float = 0.0, trailing_active: Optional[bool] = None) -> dict:
    """Playbook-aligned trail geometry (pure).

    The ratchet threshold uses the Playbook formula — SL / (1 ± 2·ATR_pct), with
    ATR_pct recomputed from the live ``mark`` each tick — so the displayed number
    tracks the external readout. ``trailing_active`` (the engine's own flag, set
    by update_trailing_stop) is the authoritative "has the trail actually fired":
    the Playbook confirms this from the SL order's uTime; the bot confirms it
    from its trailing state (the engine cancel+replaces the SL, so its uTime
    would not reveal it). Mirrors the Playbook decision tree:

        mark beyond threshold → ratchet demanded → check fired
        else                  → geometry not demanded → frozen SL is correct
    """
    init = initial_risk(entry_price, stop_loss)
    pr = profit_in_r(direction, entry_price, mark, init)
    ap = atr_pct(atr, mark)                       # percent
    ap_frac = (ap / 100.0) if ap is not None else None
    threshold = playbook_trail_threshold(direction, stop_loss, ap_frac)
    gap = playbook_gap(direction, mark, threshold)
    demanded = gap is not None and gap <= 0
    fired = bool(trailing_active) if trailing_active is not None else None

    if threshold is None:
        verdict = "ATR unavailable — geometry not computed"
    elif not demanded:
        verdict = "GEOMETRY NOT DEMANDED — frozen SL correct, not a valid test"
    elif fired:
        verdict = "RATCHET DEMANDED — trail fired ✅"
    elif fired is False:
        verdict = "RATCHET DEMANDED — trail has NOT fired ⚠️"
    else:
        verdict = "RATCHET DEMANDED — check trail"

    return {
        "profit_r": pr,
        "atr_pct": ap,
        "threshold": threshold,
        "gap": gap,
        "demanded": demanded,
        "fired": fired,
        "verdict": verdict,
    }


# ── Playbook-style formatting ────────────────────────────────────────────────

def format_trail_read(read: dict) -> list[str]:
    """Render trail_read() output as Playbook-style lines."""
    lines = ["🔷 TRAIL READ"]
    ap = read.get("atr_pct")
    thr = read.get("threshold")
    gap = read.get("gap")
    pr = read.get("profit_r")
    head = f"- Mark profit: {pr:+.2f}R" if pr is not None else "-"
    if ap is not None:
        head += f" | ATR ~{ap:.2f}%"
    lines.append(head)
    if thr is not None:
        lines.append(f"- Est. threshold: ${thr:,.4f}")
    if gap is not None:
        side = "ABOVE" if gap >= 0 else "BELOW"
        lines.append(f"- Gap: {gap:+.4f} ({side} threshold)")
    fired = read.get("fired")
    if fired is not None:
        lines.append(f"- {format_trail_fired(fired)}")
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
