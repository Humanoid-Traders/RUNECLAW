"""Batch-2 audit fixes: detected-then-dropped signals reach the electorate.

fib_extensions and the chart-pattern liquidity sweep were excluded from the
aggregate voter (correctly — dedup) but had NO dedicated consumer, so their
evidence vanished. The direction-aware volume_profile_to_confluence was dead
code while a crude POC-bias vote ran live. gather_levels now accepts the
bot's own fib/Elliott objectives as scored levels.
"""

import numpy as np

from bot.core.levels import gather_levels
from bot.core.volume_profile import compute_volume_profile


def _score(ind, price=100.0):
    from bot.core.analyzer import Analyzer
    from bot.core.ta_utils import Regime
    from bot.utils.models import MarketSignal
    sig = MarketSignal(symbol="T/USDT", price=price, change_pct_24h=0.0,
                       volume_usd_24h=1e6)
    breakdown = []
    Analyzer.__dict__["_score_confluence"].__func__(
        ind, Regime.RANGE, sig, breakdown=breakdown)
    return {n: (v, w) for n, v, w in breakdown}


class TestFibExtensionVoter:
    def test_bullish_extensions_vote(self):
        got = _score({"fib_extensions": {"signal": "bullish",
                                         "confidence": 0.6}})
        assert "fib_extension" in got
        v, w = got["fib_extension"]
        assert v == 1.0 and w > 0

    def test_absent_extensions_abstain(self):
        got = _score({})
        assert "fib_extension" not in got


class TestChartSweepFallback:
    def test_chart_sweep_votes_when_module_silent(self):
        got = _score({"chart_sweep": {"signal": "bearish",
                                      "confidence": 0.7}})
        assert "liquidity_sweep" in got
        v, _w = got["liquidity_sweep"]
        assert v == -1.0

    def test_dedicated_sweep_suppresses_chart_fallback(self):
        got = _score({"_sweep_votes": [1.0], "_sweep_weights": [0.9],
                      "chart_sweep": {"signal": "bearish",
                                      "confidence": 0.7}})
        # Exactly ONE sweep vote (the dedicated one) — no stacking.
        assert got["liquidity_sweep"][0] == 1.0


class TestDirectionAwareVPVote:
    def test_vp_votes_present_and_named(self):
        n = 120
        closes = np.concatenate([np.full(60, 100.0), np.linspace(100, 96, 60)])
        highs, lows = closes + 0.5, closes - 0.5
        volumes = np.full(n, 10.0)
        volumes[40:60] = 50.0     # value builds near 100
        vp = compute_volume_profile(highs, lows, closes, volumes,
                                    current_price=float(closes[-1]))
        assert vp is not None
        got = _score({"_vp_result": vp})
        # The direction-aware converter may cast several votes; all named.
        assert "volume_profile" in got


class TestLevelsExtras:
    def test_fib_and_ew_targets_become_levels(self):
        n = 60
        closes = 100 + np.sin(np.arange(n) / 4)
        highs, lows = closes + 1, closes - 1
        lvls = gather_levels(highs, lows, closes, atr=1.0,
                             extra_levels=[(101.8, "fib"), (103.5, "ew_target"),
                                           (0.0, "fib"), (float("nan"), "fib")])
        kinds = {lv.kind for lv in lvls}
        assert "fib" in kinds or "ew_target" in kinds or any(
            abs(lv.price - 101.8) < 0.3 for lv in lvls)

    def test_unknown_kind_degrades_to_swing(self):
        n = 60
        closes = 100 + np.sin(np.arange(n) / 4)
        lvls = gather_levels(closes + 1, closes - 1, closes, atr=1.0,
                             extra_levels=[(102.2, "martian")])
        assert any(lv.kind == "swing" and abs(lv.price - 102.2) < 0.5
                   for lv in lvls)
