"""
RUNECLAW — Advanced Elliott Wave helpers.

Pure, side-effect-free functions that deepen the existing Elliott Wave
pattern detectors (bot/core/chart_patterns.py) along four axes the operator
asked for:

  1. atr_zigzag_pivots     — a structural ATR/%-normalized ZigZag pivot
                              engine, shape-compatible with
                              multi_timeframe._find_swings, so it is a
                              drop-in swings provider for every EW detector.
                              Filters out noise wiggles the fixed 5-bar
                              fractal keeps, which matters most on crypto.
  2. timeframe_for_strategy — maps a strategy_type (scalp/intraday/swing/
                              position) to the candle timeframe whose wave
                              *degree* is the correct one to trade that
                              setup. A scalp reads a lower degree than a
                              swing; giving both the same 1H read (today's
                              behaviour) is the core gap.
  3. wave_action           — turns a detected wave's *position* into an
                              action: end-of-W2/W4 = enter (with-trend),
                              W3-in-progress = enter (momentum), W5 / ending
                              diagonal = avoid/exit (exhaustion). Today a
                              terminal wave 5 still votes plain "bullish".
  4. project_targets       — Fibonacci-projected price targets (W3=1.618xW1,
                              W5 from the W1..W3 range) and the wave-
                              invalidation level (e.g. W2 low for a W3 long)
                              for wave-anchored stops.

Everything here is pure math on plain numbers / dicts. All wiring into the
analyzer is gated behind config flags (ELLIOTT_* — default ON, each
env-overridable), so importing or even calling these functions can never
change live behaviour on its own.
"""

from __future__ import annotations

from typing import Optional

import numpy as np


# ── 1. ATR-normalized ZigZag pivot engine ───────────────────────────────

def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
    """Wilder-style ATR of the last `period` bars (simple mean of true range).

    Returns 0.0 on insufficient data so callers can fall back to a percentage
    threshold instead.
    """
    n = len(closes)
    if n < 2:
        return 0.0
    period = max(1, min(period, n - 1))
    trs = np.empty(period, dtype=float)
    for k in range(period):
        i = n - period + k
        prev_close = closes[i - 1]
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - prev_close)
        lc = abs(lows[i] - prev_close)
        trs[k] = max(hl, hc, lc)
    return float(np.mean(trs))


def atr_zigzag_pivots(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
    atr_period: int = 14, atr_mult: float = 1.5, min_pct: float = 0.0,
    max_points: int = 8,
) -> dict:
    """Structural ZigZag pivots, shape-compatible with `_find_swings`.

    A new pivot is only registered once price reverses by at least
    `threshold` from the current running extreme, where
    ``threshold = max(atr_mult * ATR, min_pct/100 * price)``. This suppresses
    the local wiggles a fixed N-bar fractal treats as swings, so the pivots
    that reach the Elliott detectors are genuine structural turns.

    Returns ``{"swing_highs": [(idx, price), ...],
               "swing_lows":  [(idx, price), ...]}`` with the pivots in
    chronological order (same tuple shape `_find_swings` returns), truncated
    to the last ``max_points`` of each so downstream ``[-2]``/``[-4]`` indexing
    behaves like the fractal version.
    """
    n = len(closes)
    empty = {"swing_highs": [], "swing_lows": []}
    if n < 3:
        return empty

    atr = _atr(highs, lows, closes, atr_period)
    ref_price = float(closes[-1]) if closes[-1] > 0 else 1.0
    pct_thresh = (min_pct / 100.0) * ref_price if min_pct > 0 else 0.0
    threshold = max(atr_mult * atr, pct_thresh)
    if threshold <= 0:
        # Degenerate (flat / zero-vol) window: nothing structural to report.
        return empty

    pivots: list[tuple[int, float, str]] = []  # (index, price, 'H'|'L')
    # Seed direction from the first meaningful move off bar 0.
    last_ext_idx = 0
    last_ext_high = float(highs[0])
    last_ext_low = float(lows[0])
    direction = 0  # +1 = seeking higher high, -1 = seeking lower low, 0 = undecided

    for i in range(1, n):
        hi = float(highs[i])
        lo = float(lows[i])
        if direction >= 0:
            # Extend the up-leg's high.
            if hi > last_ext_high:
                last_ext_high = hi
                last_ext_idx = i
            # Reversal down confirmed?
            if last_ext_high - lo >= threshold:
                pivots.append((last_ext_idx, last_ext_high, "H"))
                direction = -1
                last_ext_low = lo
                last_ext_idx = i
        if direction <= 0:
            if lo < last_ext_low:
                last_ext_low = lo
                last_ext_idx = i
            if hi - last_ext_low >= threshold:
                pivots.append((last_ext_idx, last_ext_low, "L"))
                direction = 1
                last_ext_high = hi
                last_ext_idx = i

    swing_highs = [(idx, price) for idx, price, kind in pivots if kind == "H"]
    swing_lows = [(idx, price) for idx, price, kind in pivots if kind == "L"]
    if max_points > 0:
        swing_highs = swing_highs[-max_points:]
        swing_lows = swing_lows[-max_points:]
    return {"swing_highs": swing_highs, "swing_lows": swing_lows}


# ── 2. Strategy-type → wave-degree timeframe ────────────────────────────

# Default degree map. Scalp trades a lower wave degree than a swing; giving
# both the same primary-timeframe read is the core gap this addresses. These
# are the *preferred* timeframes; the analyzer falls back to whatever series
# it actually has when the preferred one wasn't fetched.
_DEFAULT_TF_FOR_STRATEGY = {
    "scalp": "15m",
    "intraday": "1h",
    "swing": "4h",
    "position": "1d",
}


def timeframe_for_strategy(strategy_type: str, overrides: Optional[dict] = None) -> str:
    """Return the candle timeframe whose wave degree fits ``strategy_type``.

    ``overrides`` (e.g. from config) may remap any strategy_type; unknown
    types fall back to the intraday/1h degree.
    """
    table = dict(_DEFAULT_TF_FOR_STRATEGY)
    if overrides:
        table.update({k: v for k, v in overrides.items() if v})
    return table.get(strategy_type, "1h")


# ── 3. Wave position → action ───────────────────────────────────────────

def wave_action(pattern: Optional[dict]) -> dict:
    """Translate a detected Elliott pattern into a trade action.

    Returns ``{"action": one of "enter"|"wait"|"avoid"|"exit",
               "bias": "with"|"against"|"neutral",
               "weight_mult": float,  # multiply the EW voter weight by this
               "reason": str}``.

    Rationale (classic Elliott trading):
      - Impulse currently in wave 3       -> enter (strongest, with-trend).
      - Impulse currently in wave 4       -> enter (buy the pullback for W5).
      - Impulse in a truncated / wave 5   -> avoid/exit (trend exhaustion).
      - Ending diagonal (W5/C)            -> exit/avoid (reversal imminent).
      - Leading diagonal (W1/A)           -> enter small (new trend starting).
      - ABC corrective complete           -> enter with the resuming trend.
      - Complex WXY/WXYXZ                  -> wait (consolidation, unclear).

    ``weight_mult`` lets the caller *dampen* (or zero) an EW vote rather than
    blindly voting the pattern's raw signal — a terminal wave 5 should not
    add trend-continuation conviction.
    """
    if not pattern:
        return {"action": "wait", "bias": "neutral", "weight_mult": 1.0, "reason": "no pattern"}

    name = str(pattern.get("name", ""))
    levels = pattern.get("key_levels", {}) or {}
    current_wave = str(levels.get("current_wave", "")) or _infer_current_wave(pattern)

    # Ending diagonal / truncated 5th = exhaustion → do not add trend conviction.
    if "Ending Diagonal" in name or "Truncated" in name:
        return {"action": "avoid", "bias": "against", "weight_mult": 0.3,
                "reason": "terminal structure (ending diagonal / truncated 5th) — exhaustion"}
    if "Leading Diagonal" in name:
        return {"action": "enter", "bias": "with", "weight_mult": 0.8,
                "reason": "leading diagonal — new trend starting (enter small)"}
    if "ABC" in name:
        # A completed correction resolves in favour of the prior trend.
        complete = "partial" not in name.lower()
        return {"action": "enter" if complete else "wait", "bias": "with",
                "weight_mult": 1.0 if complete else 0.6,
                "reason": "ABC correction complete — trend resumes" if complete
                          else "ABC correction still forming — wait"}
    if "WXY" in name or "WXYXZ" in name:
        # The detector only fires on a COMPLETED W-X-Y geometry (the final
        # swing is a confirmed pivot), so like a complete ABC the tradeable
        # signal is the RESUMPTION of the prior trend — bias "with" + "enter"
        # makes the analyzer's corrective flip reachable (audit: the old
        # "wait"/neutral return left the flip branch dead and the raw vote
        # pointed in the CORRECTION's direction, exactly backwards). Slightly
        # lower conviction than ABC: complex corrections extend more often.
        return {"action": "enter", "bias": "with", "weight_mult": 0.85,
                "reason": "complex correction (WXY/WXYXZ) complete — prior trend resumes"}
    if "Impulse" in name:
        if current_wave == "5":
            return {"action": "exit", "bias": "against", "weight_mult": 0.35,
                    "reason": "impulse in wave 5 — trend maturing, avoid fresh entries"}
        if current_wave == "4":
            return {"action": "enter", "bias": "with", "weight_mult": 1.15,
                    "reason": "impulse in wave 4 pullback — buy for the wave 5 leg"}
        if current_wave == "3":
            return {"action": "enter", "bias": "with", "weight_mult": 1.25,
                    "reason": "impulse in wave 3 — strongest momentum leg"}
        # Partial (waves 1-3 visible) or unknown position: mild with-trend.
        return {"action": "enter", "bias": "with", "weight_mult": 1.0,
                "reason": "impulse forming — with-trend"}

    return {"action": "wait", "bias": "neutral", "weight_mult": 1.0, "reason": "unclassified wave"}


# ── 3b. Cross-degree (all-timeframes) wave alignment ────────────────────

# Higher wave degrees carry more weight: a 1d structure outranks a 15m one.
_DEGREE_WEIGHT = {"15m": 0.7, "1h": 1.0, "4h": 1.3, "1d": 1.5}
_TERMINAL_DEGREES = ("4h", "1d")   # terminal structure here = exhaustion risk


def mtf_wave_map(patterns_by_tf: dict) -> dict:
    """Cross-degree Elliott alignment across every supplied timeframe.

    ``patterns_by_tf`` maps a timeframe string ("15m"/"1h"/"4h"/"1d") to the
    best detected Elliott pattern on that series (or None). Each pattern's
    raw signal is converted to an EFFECTIVE signed vote using the same
    doctrine the confluence voter applies on the primary degree:

      - completed corrective (ABC/WXY, bias "with", action "enter") votes
        the RESUMPTION direction — the flip of the correction's own label;
      - wave_action's weight_mult scales conviction (terminal W5 / ending
        diagonal contributes little; W3 contributes most);
      - higher degrees outrank lower ones (_DEGREE_WEIGHT).

    Returns::

        {"by_tf": {tf: {"name", "signal", "action", "bias",
                        "weight_mult", "effective"}},
         "alignment": float,             # [-1, 1], + = bullish agreement
         "dominant_bias": "bullish" | "bearish" | "neutral",
         "higher_degree_terminal": bool, # 4h/1d in W5/ending diag/truncated
         "n_timeframes": int}

    Pure math, never raises; empty/None input yields a neutral map.
    """
    by_tf: dict = {}
    num = 0.0
    den = 0.0
    terminal = False
    for tf, pat in (patterns_by_tf or {}).items():
        if not pat:
            continue
        try:
            act = wave_action(pat)
            sig = str(pat.get("signal", "neutral"))
            conf = float(pat.get("confidence", 0.5) or 0.5)
            raw = conf if sig == "bullish" else -conf if sig == "bearish" else 0.0
            name = str(pat.get("name", ""))
            # Completed corrective doctrine: the tradeable direction is the
            # RESUMPTION of the prior trend — flip the correction's label.
            corrective = ("ABC" in name or "WXY" in name or "WXYXZ" in name)
            if (corrective and act.get("bias") == "with"
                    and act.get("action") == "enter"):
                raw = -raw
            effective = raw * float(act.get("weight_mult", 1.0))
            if tf in _TERMINAL_DEGREES and (
                    "Ending Diagonal" in name or "Truncated" in name
                    or (act.get("action") in ("exit", "avoid"))):
                terminal = True
            w = _DEGREE_WEIGHT.get(tf, 1.0)
            num += w * effective
            den += w
            by_tf[tf] = {"name": name, "signal": sig,
                         "action": act.get("action", "wait"),
                         "bias": act.get("bias", "neutral"),
                         "weight_mult": round(float(act.get("weight_mult", 1.0)), 3),
                         "effective": round(effective, 4)}
        except Exception:  # noqa: BLE001 — one bad pattern never voids the map
            continue
    alignment = (num / den) if den > 0 else 0.0
    alignment = max(-1.0, min(1.0, alignment))
    dominant = ("bullish" if alignment > 0.15
                else "bearish" if alignment < -0.15 else "neutral")
    return {"by_tf": by_tf, "alignment": round(alignment, 4),
            "dominant_bias": dominant,
            "higher_degree_terminal": terminal,
            "n_timeframes": len(by_tf)}


def _infer_current_wave(pattern: dict) -> str:
    """Best-effort current-wave label when the detector didn't embed one."""
    desc = str(pattern.get("description", ""))
    for w in ("5", "4", "3", "2", "1"):
        if f"wave {w}" in desc:
            return w
    return ""


# ── 4. Fibonacci projected targets + wave invalidation ──────────────────

def project_targets(pattern: Optional[dict]) -> dict:
    """Fibonacci-projected targets and the wave-invalidation level.

    For a bullish impulse where waves 1-3 (and the W2 low) are known:
      - tp1 = W3 top + 0.618 * (W3 range)         (conservative W5 target)
      - tp2 = W2 low + 1.618 * (W1 length)        (classic W3/W5 projection)
      - invalidation = W2 low  (a break there voids the bullish count)

    Mirror logic for a bearish impulse. Returns a dict with any of
    ``{"tp1", "tp2", "invalidation", "basis"}`` that could be computed;
    empty dict when the pattern lacks the needed levels. All prices are
    absolute (same units as the input candles).
    """
    if not pattern:
        return {}
    levels = pattern.get("key_levels", {}) or {}
    signal = pattern.get("signal", "")

    if signal == "bullish":
        w1_start = levels.get("w1_start")
        w1_top = levels.get("w1_top")
        w2_low = levels.get("w2_low")
        w3_top = levels.get("w3_top")
        if None in (w1_start, w1_top, w2_low):
            return {}
        w1_len = w1_top - w1_start
        if w1_len <= 0:
            return {}
        out = {"invalidation": float(w2_low), "basis": "elliott_impulse_bull"}
        # Classic W3 (or W5) projection from the W2 low.
        out["tp2"] = float(w2_low + 1.618 * w1_len)
        if w3_top is not None and w3_top > w2_low:
            w3_range = w3_top - w2_low
            out["tp1"] = float(w3_top + 0.618 * w3_range)
        else:
            out["tp1"] = float(w2_low + 1.0 * w1_len)
        return out

    if signal == "bearish":
        w1_start = levels.get("w1_start")
        w1_low = levels.get("w1_low")
        w2_high = levels.get("w2_high")
        w3_low = levels.get("w3_low")
        if None in (w1_start, w1_low, w2_high):
            return {}
        w1_len = w1_start - w1_low
        if w1_len <= 0:
            return {}
        out = {"invalidation": float(w2_high), "basis": "elliott_impulse_bear"}
        out["tp2"] = float(w2_high - 1.618 * w1_len)
        if w3_low is not None and w2_high > w3_low:
            w3_range = w2_high - w3_low
            out["tp1"] = float(w3_low - 0.618 * w3_range)
        else:
            out["tp1"] = float(w2_high - 1.0 * w1_len)
        return out

    return {}
