"""
Regime hard gates (Audit framework — Part 3 regime policy).

The analyzer already SOFTENS low-edge regimes (counter-trend ×0.5 penalty,
RANGE/CHOP confidence haircuts). The hard gates (opt-in, default OFF) turn the
worst into outright no-trades: CHOP/UNKNOWN have no directional edge, and a
counter-trend entry into a STRONG trend is where drawdowns cluster. These tests
exercise the pure decision helper _regime_hard_gate_reason in isolation.
"""

from unittest.mock import patch

from bot.core.analyzer import Analyzer
from bot.core.ta_utils import Regime
from bot.utils.models import Direction


def _analyzer():
    # Bare instance — the helper only reads CONFIG + its args, no init needed.
    return Analyzer.__new__(Analyzer)


def _cfg(strong_adx=30.0):
    p = patch("bot.core.analyzer.CONFIG")
    m = p.start()
    m.analyzer.regime_strong_adx = strong_adx
    return p


class TestBlocked:
    def test_chop_blocked(self):
        p = _cfg()
        try:
            r = _analyzer()._regime_hard_gate_reason(Regime.CHOP, Direction.LONG, 15.0)
        finally:
            p.stop()
        assert r is not None and "CHOP" in r

    def test_unknown_blocked(self):
        p = _cfg()
        try:
            r = _analyzer()._regime_hard_gate_reason(Regime.UNKNOWN, Direction.SHORT, 0.0)
        finally:
            p.stop()
        assert r is not None and "UNKNOWN" in r

    def test_counter_trend_short_in_strong_uptrend_blocked(self):
        p = _cfg(strong_adx=30.0)
        try:
            r = _analyzer()._regime_hard_gate_reason(Regime.TREND_UP, Direction.SHORT, 35.0)
        finally:
            p.stop()
        assert r is not None and "counter-trend SHORT" in r

    def test_counter_trend_long_in_strong_downtrend_blocked(self):
        p = _cfg(strong_adx=30.0)
        try:
            r = _analyzer()._regime_hard_gate_reason(Regime.TREND_DOWN, Direction.LONG, 31.0)
        finally:
            p.stop()
        assert r is not None and "counter-trend LONG" in r


class TestAllowed:
    def test_counter_trend_in_weak_trend_allowed(self):
        # ADX below the strong threshold → not a hard gate (soft penalty still
        # applies upstream).
        p = _cfg(strong_adx=30.0)
        try:
            r = _analyzer()._regime_hard_gate_reason(Regime.TREND_UP, Direction.SHORT, 25.0)
        finally:
            p.stop()
        assert r is None

    def test_with_trend_allowed(self):
        p = _cfg(strong_adx=30.0)
        try:
            a = _analyzer()
            assert a._regime_hard_gate_reason(Regime.TREND_UP, Direction.LONG, 45.0) is None
            assert a._regime_hard_gate_reason(Regime.TREND_DOWN, Direction.SHORT, 45.0) is None
        finally:
            p.stop()

    def test_range_and_expansion_allowed(self):
        # RANGE/EXPANSION are handled by soft penalties/bonuses, not hard-gated.
        p = _cfg()
        try:
            a = _analyzer()
            assert a._regime_hard_gate_reason(Regime.RANGE, Direction.LONG, 12.0) is None
            assert a._regime_hard_gate_reason(Regime.EXPANSION, Direction.LONG, 28.0) is None
        finally:
            p.stop()

    def test_threshold_is_inclusive(self):
        # ADX exactly at the threshold blocks (>=).
        p = _cfg(strong_adx=30.0)
        try:
            r = _analyzer()._regime_hard_gate_reason(Regime.TREND_UP, Direction.SHORT, 30.0)
        finally:
            p.stop()
        assert r is not None
