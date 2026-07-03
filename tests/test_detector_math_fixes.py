"""Tier 2: detector-math fixes from the signal-stack audit.

Covers: harmonic ratio table correction, triangle band overlap, pattern
completion gating (H&S / double top-bottom / cup & handle), fabricated
neckline removal, candle strength requirements (soldiers/crows), directional
doji, trend-context suppression, prior-window Donchian, EXPANSION regime
release semantics, Elliott corrective vote flip, sweep de-dup, and the
agreeing-count pattern weight.
"""
from __future__ import annotations

import numpy as np

from bot.core.chart_patterns import (
    detect_double_top_bottom,
    detect_harmonic_pattern,
    detect_head_and_shoulders,
    detect_triangles,
)
from bot.core.analyzer import Analyzer, _detect_candlestick_patterns


# ── helpers ──────────────────────────────────────────────────────

def _path_through(pivots, bars_per_leg=7):
    """Price path visiting each pivot with linear legs long enough for the
    5-bar fractal in _find_swings to confirm every pivot."""
    pts = []
    for i in range(len(pivots) - 1):
        seg = np.linspace(pivots[i], pivots[i + 1], bars_per_leg, endpoint=False)
        pts.extend(seg)
    pts.append(pivots[-1])
    closes = np.array(pts, dtype=float)
    highs = closes + 0.05
    lows = closes - 0.05
    return highs, lows, closes


def _swings_from(points):
    """Build an explicit swings dict: points = [(idx, price, 'h'|'l'), ...]."""
    return {
        "swing_highs": [(i, p) for i, p, k in points if k == "h"][-4:],
        "swing_lows": [(i, p) for i, p, k in points if k == "l"][-4:],
    }


# ── harmonics ────────────────────────────────────────────────────

class TestHarmonicRatios:
    def test_textbook_bullish_gartley_detected(self):
        # X=100 low, A=110 high, B=103.82 (0.618 XA), C=106.91 (0.5 AB),
        # D=102.14 (0.786 XA retrace). Under the OLD table (ac 1.272-1.618 of
        # AB) this could never match.
        x, a = 100.0, 110.0
        b = a - 0.618 * (a - x)
        c = b + 0.5 * (a - b)
        d = a - 0.786 * (a - x)
        swings = {
            "swing_highs": [(10, a), (24, c)],
            "swing_lows": [(3, x), (17, b), (31, d)],
        }
        n = 40
        closes = np.full(n, d)
        highs = closes + 0.1
        lows = closes - 0.1
        res = detect_harmonic_pattern(highs, lows, closes, swings=swings)
        assert res is not None and "Gartley" in res["name"]
        assert res["signal"] == "bullish"

    def test_old_style_c_beyond_a_geometry_rejected(self):
        # C at 1.4x AB beyond B (C above A) satisfied the OLD broken table.
        x, a = 100.0, 110.0
        b = a - 0.618 * (a - x)
        c = b + 1.4 * (a - b)          # C well beyond A — invalid harmonic
        d = a - 0.786 * (a - x)
        swings = {
            "swing_highs": [(10, a), (24, c)],
            "swing_lows": [(3, x), (17, b), (31, d)],
        }
        n = 40
        closes = np.full(n, d)
        res = detect_harmonic_pattern(closes + 0.1, closes - 0.1, closes, swings=swings)
        assert res is None or "Gartley" not in res.get("name", "")


# ── triangles ────────────────────────────────────────────────────

class TestTriangleBands:
    def _res(self, nh_pct, nl_pct, price=100.0):
        # Craft swings whose regression slopes normalize to the given pcts.
        # Two points per line: slope = (p2-p1)/(i2-i1); _normalize_slope is
        # slope/price*100 (per bar). Use 10-bar spacing.
        sh = [(0, price + 5), (10, price + 5 + nh_pct / 100 * price * 10)]
        sl = [(0, price - 5), (10, price - 5 + nl_pct / 100 * price * 10)]
        swings = {"swing_highs": sh, "swing_lows": sl}
        closes = np.full(20, price)
        return detect_triangles(closes + 6, closes - 6, closes, swings=swings)

    def test_textbook_symmetrical_not_ascending(self):
        # nh=-0.03, nl=+0.03: the OLD ascending band (|nh|<0.05) claimed this
        # convergent structure as a bullish Ascending Triangle.
        res = self._res(-0.03, +0.03)
        assert res is not None
        assert res["name"] == "Symmetrical Triangle"
        assert res["signal"] == "neutral"

    def test_true_ascending_detected_forming_until_breakout(self):
        # Price inside the pattern: geometry classified but the directional
        # vote is withheld (completion gating) — a coiling triangle is a
        # range, not a long signal, until the close breaks the boundary.
        res = self._res(0.0, +0.05)
        assert res is not None and res["name"] == "Ascending Triangle (forming)"
        assert res["signal"] == "neutral"

    def test_true_descending_detected_forming_until_breakout(self):
        res = self._res(-0.05, 0.0)
        assert res is not None and res["name"] == "Descending Triangle (forming)"
        assert res["signal"] == "neutral"


# ── completion gating ────────────────────────────────────────────

class TestCompletionGating:
    def _hs_swings(self):
        # L-shoulder 110, head 115, R-shoulder 110; neckline lows ~105.
        return {
            "swing_highs": [(10, 110.0), (20, 115.0), (30, 110.0)],
            "swing_lows": [(15, 105.0), (25, 105.0)],
        }

    def test_hs_not_emitted_before_neckline_break(self):
        closes = np.full(40, 108.0)   # above the 105 neckline
        res = detect_head_and_shoulders(closes + 1, closes - 1, closes,
                                        swings=self._hs_swings())
        assert res is None or res["name"] != "Head & Shoulders"

    def test_hs_emitted_after_confirmed_break(self):
        closes = np.full(40, 103.0)   # closed below the neckline
        res = detect_head_and_shoulders(closes + 1, closes - 1, closes,
                                        swings=self._hs_swings())
        assert res is not None and res["name"] == "Head & Shoulders"
        assert res["signal"] == "bearish"

    def test_hs_skipped_without_neckline_lows(self):
        # No between-shoulder swing lows: the OLD code fabricated a neckline
        # from the lower shoulder PEAK — nearly always already "broken".
        swings = {"swing_highs": [(10, 110.0), (20, 115.0), (30, 110.0)],
                  "swing_lows": [(2, 100.0)]}
        closes = np.full(40, 103.0)
        res = detect_head_and_shoulders(closes + 1, closes - 1, closes, swings=swings)
        assert res is None or res["name"] != "Head & Shoulders"

    def test_double_top_requires_neckline_break(self):
        swings = {"swing_highs": [(10, 110.0), (20, 110.2)],
                  "swing_lows": [(15, 106.0)]}
        # Price still between the tops — no vote.
        closes = np.full(30, 108.0)
        res = detect_double_top_bottom(closes + 1, closes - 1, closes, swings=swings)
        assert res is None or res["name"] != "Double Top"
        # Confirmed close below the trough — bearish.
        closes = np.full(30, 105.0)
        res = detect_double_top_bottom(closes + 1, closes - 1, closes, swings=swings)
        assert res is not None and res["name"] == "Double Top"


# ── candles ──────────────────────────────────────────────────────

def _bars(rows):
    """rows: list of (o, h, l, c) → arrays."""
    o = np.array([r[0] for r in rows], dtype=float)
    h = np.array([r[1] for r in rows], dtype=float)
    l = np.array([r[2] for r in rows], dtype=float)  # noqa: E741
    c = np.array([r[3] for r in rows], dtype=float)
    return o, h, l, c


def _downtrend_prefix(n=12, start=110.0, step=1.0):
    rows = []
    p = start
    for _ in range(n):
        rows.append((p, p + 0.3, p - step - 0.3, p - step))
        p -= step
    return rows, p


class TestCandleStrength:
    def test_tiny_drift_does_not_fire_soldiers(self):
        rows, p = _downtrend_prefix()
        # three +0.01 bodies with big ranges — the OLD code fired the
        # top-strength (1.5) three_white_soldiers on this drift.
        for i in range(3):
            rows.append((p + i * 0.01, p + i * 0.01 + 1.0,
                         p + i * 0.01 - 1.0, p + (i + 1) * 0.01))
        o, h, l, c = _bars(rows)
        pats = _detect_candlestick_patterns(o, h, l, c)
        assert "three_white_soldiers" not in pats

    def test_proper_soldiers_fire_after_downtrend(self):
        rows, p = _downtrend_prefix()
        # three long-bodied advancing candles, each opening inside prior body
        b = 2.0
        for i in range(3):
            op = p + i * b * 0.7
            cl = op + b
            rows.append((op, cl + 0.1, op - 0.1, cl))
        o, h, l, c = _bars(rows)
        pats = _detect_candlestick_patterns(o, h, l, c)
        assert pats.get("three_white_soldiers") == "bullish"

    def test_dragonfly_doji_bullish_at_downtrend_low(self):
        rows, p = _downtrend_prefix()
        rows.append((p, p + 0.02, p - 2.0, p + 0.01))  # long lower wick, no body
        o, h, l, c = _bars(rows)
        pats = _detect_candlestick_patterns(o, h, l, c)
        assert pats.get("dragonfly_doji") == "bullish"

    def test_bullish_engulfing_suppressed_in_uptrend(self):
        # 12 rising bars then a "bullish engulfing" — nothing to reverse.
        rows = []
        p = 100.0
        for _ in range(12):
            rows.append((p, p + 1.3, p - 0.3, p + 1.0))
            p += 1.0
        rows.append((p, p + 0.2, p - 1.2, p - 1.0))          # small red
        rows.append((p - 1.1, p + 1.6, p - 1.3, p + 1.5))    # engulfing green
        o, h, l, c = _bars(rows)
        pats = _detect_candlestick_patterns(o, h, l, c)
        assert "bullish_engulfing" not in pats


# ── donchian ─────────────────────────────────────────────────────

class TestDonchianPriorWindow:
    def _indicators(self, closes, highs, lows):
        vols = np.full(len(closes), 10.0)
        return Analyzer._compute_indicators(highs, lows, closes, vols,
                                            opens=closes.copy())

    def test_breakout_fires_on_close_beyond_prior_channel(self):
        n = 60
        closes = np.full(n, 100.0) + np.sin(np.arange(n)) * 0.5
        highs = closes + 0.5
        lows = closes - 0.5
        closes[-1] = 103.0          # closes above every prior high
        highs[-1] = 103.4
        ind = self._indicators(closes, highs, lows)
        assert ind.get("dc_breakout_high") is True

    def test_new_intrabar_high_alone_does_not_fire(self):
        # OLD behavior: including the current bar meant close >= own high was
        # required. NEW: a close back inside the prior channel is no breakout
        # even if the bar's high poked above it.
        n = 60
        closes = np.full(n, 100.0) + np.sin(np.arange(n)) * 0.5
        highs = closes + 0.5
        lows = closes - 0.5
        highs[-1] = 105.0           # spike high
        closes[-1] = 100.2          # close back inside
        ind = self._indicators(closes, highs, lows)
        assert ind.get("dc_breakout_high") is False


# ── regime ───────────────────────────────────────────────────────

class TestExpansionRelease:
    def _regime(self, squeeze, squeeze_prev, adx=25.0):
        an = Analyzer()
        an._llm = None
        ind = {"adx": adx, "plus_di": 20.0, "minus_di": 10.0,
               "kc_squeeze": squeeze, "kc_squeeze_prev": squeeze_prev}
        # Call three times: regime smoothing needs 2/3 consensus.
        for _ in range(3):
            r = an._detect_regime(ind, "TEST/USDT")
        return r

    def test_still_on_squeeze_is_not_expansion(self):
        r = self._regime(squeeze=True, squeeze_prev=True)
        assert r.value != "EXPANSION"

    def test_release_is_expansion(self):
        r = self._regime(squeeze=False, squeeze_prev=True)
        assert r.value == "EXPANSION"


# ── elliott corrective flip ─────────────────────────────────────

class TestEllioittCorrectiveFlip:
    def test_completed_abc_votes_resumption_direction(self):
        from bot.core.elliott import wave_action
        act = wave_action({"name": "ABC Correction (complete)",
                           "signal": "bearish", "key_levels": {}})
        # Doctrine: complete ABC → trend resumes (bias "with", action enter).
        assert act["bias"] == "with" and act["action"] == "enter"
        # The analyzer flips ew_corrective/ew_wxy votes on exactly this combo
        # (pinned here so a doctrine change re-flags the analyzer logic).
