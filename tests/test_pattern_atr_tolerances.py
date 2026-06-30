"""
ATR-normalized chart-pattern symmetry tolerances (deep-audit medium #24).

Head & Shoulders and Double Top/Bottom gated "roughly equal" on a hard-coded
absolute percentage (5% / 3%) regardless of the instrument's volatility — too
loose on a placid pair, too tight on a violent one — and derived confidence from
that fixed gate. The detectors now scale the tolerance with recent ATR-as-%-of-
price when PATTERN_ATR_TOLERANCES_ENABLED is on, clamped so it can only TIGHTEN
the legacy gate (never loosen past it). Default OFF → byte-identical.
"""

import numpy as np
import pytest

import bot.core.chart_patterns as cp
from bot.core.chart_patterns import (
    _atr_pct,
    _sym_tolerance,
    detect_head_and_shoulders,
)


def _flat_series(n=40, price=100.0, half_range=1.0):
    closes = np.full(n, price, dtype=float)
    highs = closes + half_range
    lows = closes - half_range
    return highs, lows, closes


class TestAtrPct:
    def test_flat_series_atr_pct(self):
        # high-low = 2 every bar, prev close = price → TR = 2, ATR = 2, price 100.
        highs, lows, closes = _flat_series(price=100.0, half_range=1.0)
        assert _atr_pct(highs, lows, closes) == pytest.approx(2.0)

    def test_insufficient_data_returns_zero(self):
        assert _atr_pct(np.array([1.0]), np.array([1.0]), np.array([1.0])) == 0.0

    def test_nonpositive_price_returns_zero(self):
        highs, lows, closes = _flat_series()
        closes = closes.copy()
        closes[-1] = 0.0
        assert _atr_pct(highs, lows, closes) == 0.0


class TestSymTolerance:
    def test_flag_off_returns_fixed_exactly(self, monkeypatch):
        monkeypatch.setenv("PATTERN_ATR_TOLERANCES_ENABLED", "0")
        highs, lows, closes = _flat_series()
        assert _sym_tolerance(highs, lows, closes, 5.0) == 5.0
        assert _sym_tolerance(highs, lows, closes, 3.0) == 3.0

    def test_on_scales_with_atr(self, monkeypatch):
        monkeypatch.setenv("PATTERN_ATR_TOLERANCES_ENABLED", "1")
        # ATR% = 2.0 → tol = 1.5 * 2 = 3.0 (within [0.4*5=2, 5]).
        highs, lows, closes = _flat_series(price=100.0, half_range=1.0)
        assert _sym_tolerance(highs, lows, closes, 5.0) == pytest.approx(3.0)

    def test_on_clamps_to_floor_for_low_vol(self, monkeypatch):
        monkeypatch.setenv("PATTERN_ATR_TOLERANCES_ENABLED", "1")
        # Tiny ranges → ATR% ≈ 0.1 → 1.5*0.1=0.15 < floor 0.4*5=2.0 → clamps to 2.0.
        highs, lows, closes = _flat_series(price=100.0, half_range=0.05)
        assert _sym_tolerance(highs, lows, closes, 5.0) == pytest.approx(2.0)

    def test_on_never_loosens_past_fixed(self, monkeypatch):
        monkeypatch.setenv("PATTERN_ATR_TOLERANCES_ENABLED", "1")
        # Huge ranges → ATR% large → tol would exceed fixed → capped at fixed.
        highs, lows, closes = _flat_series(price=100.0, half_range=20.0)
        assert _sym_tolerance(highs, lows, closes, 5.0) == 5.0

    def test_on_zero_atr_falls_back_to_fixed(self, monkeypatch):
        monkeypatch.setenv("PATTERN_ATR_TOLERANCES_ENABLED", "1")
        highs, lows, closes = _flat_series()
        closes = closes.copy()
        closes[-1] = 0.0  # _atr_pct → 0 → fall back
        assert _sym_tolerance(highs, lows, closes, 5.0) == 5.0


# ── Behavioural: H&S detection through the gate ────────────────────

_SWINGS = {
    "swing_highs": [(10, 100.0), (20, 110.0), (30, 103.0)],  # shoulders 3% apart
    "swing_lows": [(15, 98.0), (25, 99.0)],
}


class TestHeadAndShouldersGate:
    @pytest.fixture
    def _patched_swings(self, monkeypatch):
        monkeypatch.setattr(cp, "_find_swings", lambda h, lw, lb: _SWINGS)

    def test_detected_when_flag_off(self, _patched_swings, monkeypatch):
        monkeypatch.setenv("PATTERN_ATR_TOLERANCES_ENABLED", "0")
        # Low-vol closes; shoulder_diff = 3% < fixed 5% → H&S detected.
        highs, lows, closes = _flat_series(price=100.0, half_range=0.05)
        res = detect_head_and_shoulders(highs, lows, closes)
        assert res is not None and res["name"] == "Head & Shoulders"

    def test_rejected_when_flag_on_low_vol(self, _patched_swings, monkeypatch):
        monkeypatch.setenv("PATTERN_ATR_TOLERANCES_ENABLED", "1")
        # Low vol → ATR-tightened tol ≈ floor 2.0% < the 3% shoulder gap → rejected.
        highs, lows, closes = _flat_series(price=100.0, half_range=0.05)
        assert detect_head_and_shoulders(highs, lows, closes) is None

    def test_still_detected_when_flag_on_high_vol(self, _patched_swings, monkeypatch):
        monkeypatch.setenv("PATTERN_ATR_TOLERANCES_ENABLED", "1")
        # High vol → tol capped at fixed 5% → 3% gap still passes.
        highs, lows, closes = _flat_series(price=100.0, half_range=20.0)
        res = detect_head_and_shoulders(highs, lows, closes)
        assert res is not None and res["name"] == "Head & Shoulders"

    def test_confidence_derived_against_tightened_tolerance(self, monkeypatch):
        # A more-symmetric pattern (shoulders 1% apart) passes both ways; the
        # ON path scores it against the tighter tolerance → strictly lower conf.
        swings = {"swing_highs": [(10, 100.0), (20, 110.0), (30, 101.0)],
                  "swing_lows": [(15, 98.0), (25, 99.0)]}
        monkeypatch.setattr(cp, "_find_swings", lambda h, lw, lb: swings)
        highs, lows, closes = _flat_series(price=100.0, half_range=1.0)  # ATR%≈2 → tol 3

        monkeypatch.setenv("PATTERN_ATR_TOLERANCES_ENABLED", "0")
        conf_off = detect_head_and_shoulders(highs, lows, closes)["confidence"]
        monkeypatch.setenv("PATTERN_ATR_TOLERANCES_ENABLED", "1")
        conf_on = detect_head_and_shoulders(highs, lows, closes)["confidence"]
        assert conf_on < conf_off
