"""
Liquidity sweep checks the sweeping bar's OWN close (deep-audit medium).

A sweep-and-reclaim is a property of ONE bar: it wicks through a swing level AND
closes back on the right side. The detector verified the reclaim with the LATEST
close (closes[-1]) even for a candidate bar 2-3 back, so an old bar's wick plus
the current bar's position could fire a false sweep. With LIQUIDITY_SWEEP_OWN_CLOSE
on, each candidate bar is checked against its own close. Default OFF is
byte-identical.

Data layout (20 bars, lookback 5): a single swing low of 100 at index 7 (the
nearest swing low); the sweep candidates are the last few bars (excluded from
swing detection).
"""

import numpy as np

from bot.core.chart_patterns import detect_liquidity_sweep


def _arrays(*, low18, close18, low19, close19):
    lows = [105.0] * 20
    closes = [105.0] * 20
    highs = [110.0] * 20
    lows[7] = 100.0           # swing low at index 7 → nearest_sl = 100
    lows[18], closes[18] = low18, close18   # offset 2 candidate
    lows[19], closes[19] = low19, close19   # offset 1 (latest) candidate
    return np.array(highs), np.array(lows), np.array(closes)


class TestOwnCloseGate:
    def test_disabled_fires_false_sweep(self, monkeypatch):
        monkeypatch.delenv("LIQUIDITY_SWEEP_OWN_CLOSE", raising=False)
        # Bar -2 wicked below 100 but closed BELOW (no reclaim); the latest bar
        # merely sits above 100. Legacy uses closes[-1] → false bullish sweep.
        h, lw, c = _arrays(low18=99.0, close18=99.5, low19=100.5, close19=101.0)
        res = detect_liquidity_sweep(h, lw, c)
        assert res is not None and res["signal"] == "bullish"

    def test_enabled_rejects_false_sweep(self, monkeypatch):
        monkeypatch.setenv("LIQUIDITY_SWEEP_OWN_CLOSE", "1")
        # Same data: bar -2's OWN close (99.5) did NOT reclaim → no sweep.
        h, lw, c = _arrays(low18=99.0, close18=99.5, low19=100.5, close19=101.0)
        assert detect_liquidity_sweep(h, lw, c) is None

    def test_enabled_fires_on_genuine_reclaim(self, monkeypatch):
        monkeypatch.setenv("LIQUIDITY_SWEEP_OWN_CLOSE", "1")
        # Bar -2 wicked below AND closed back above on its own bar → real sweep.
        h, lw, c = _arrays(low18=99.0, close18=101.0, low19=105.0, close19=105.0)
        res = detect_liquidity_sweep(h, lw, c)
        assert res is not None and res["signal"] == "bullish"
        assert res["key_levels"]["reclaim_close"] == 101.0

    def test_offset1_latest_bar_identical_both_modes(self, monkeypatch):
        # When the LATEST bar itself sweeps+reclaims, closes[-offset]==closes[-1],
        # so both modes detect it identically.
        kw = dict(low18=105.0, close18=105.0, low19=99.0, close19=101.0)
        monkeypatch.delenv("LIQUIDITY_SWEEP_OWN_CLOSE", raising=False)
        off = detect_liquidity_sweep(*_arrays(**kw))
        monkeypatch.setenv("LIQUIDITY_SWEEP_OWN_CLOSE", "1")
        on = detect_liquidity_sweep(*_arrays(**kw))
        assert off is not None and on is not None
        assert off["signal"] == on["signal"] == "bullish"
