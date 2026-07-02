"""Audit top-25 fixes, batch 2 (signal core).

#4  Direction-aware Fibonacci (bearish leg framing + symmetric votes)
#5  Pattern de-dup flag default ON
#8  Structured no-trade reasons
#9  Candle ordering/duplicate validation
#10 Data-quality stamp + thin-window penalty flags
#13 Candlestick trend context
#14 Strength-weighted candlestick vote
#16 Voter dilution guard (skip-don't-dilute)
"""
from __future__ import annotations

import numpy as np
import pytest

from bot.config import CONFIG
from bot.core.analyzer import Analyzer, _compute_fibonacci, _detect_candlestick_patterns
from bot.core.ta_utils import Regime
from bot.utils.models import MarketSignal


def _sig(price=100.0, chg=1.0, spike=False):
    return MarketSignal(symbol="BTC/USDT", price=price, change_pct_24h=chg,
                        volume_usd_24h=1e8, volume_spike=spike)


class TestDirectionAwareFib:
    def _up_leg(self, n=60):
        # Low early, high late: a rising leg.
        closes = np.linspace(100.0, 200.0, n)
        return closes + 1.0, closes - 1.0, closes

    def _down_leg(self, n=60):
        closes = np.linspace(200.0, 100.0, n)
        return closes + 1.0, closes - 1.0, closes

    def test_up_leg_detected_and_levels_legacy(self):
        import pytest as _pt
        h, l, c = self._up_leg()
        fib = _compute_fibonacci(h, l, c)
        assert fib["fib_trend"] == "up"
        diff = fib["fib_swing_high"] - fib["fib_swing_low"]
        assert fib["fib_618"] == _pt.approx(
            fib["fib_swing_high"] - 0.618 * diff, abs=1e-4)
        # Price at the top of an up-leg → shallow retracement zone.
        assert fib["fib_zone"] == "above_236"

    def test_down_leg_detected_and_levels_mirrored(self):
        import pytest as _pt
        h, l, c = self._down_leg()
        fib = _compute_fibonacci(h, l, c)
        assert fib["fib_trend"] == "down"
        diff = fib["fib_swing_high"] - fib["fib_swing_low"]
        # Down-leg: retracement levels ascend FROM the low.
        assert fib["fib_618"] == _pt.approx(
            fib["fib_swing_low"] + 0.618 * diff, abs=1e-4)
        # Price at the bottom of a down-leg → shallow retracement zone.
        assert fib["fib_zone"] == "above_236"

    def test_down_leg_deep_bounce_zone(self):
        h, l, c = self._down_leg()
        # First pass to learn the 50-bar window's swing levels, then set the
        # close to a 70% bounce of THAT leg → deep retracement zone.
        base = _compute_fibonacci(h, l, c)
        c = c.copy()
        c[-1] = base["fib_swing_low"] + 0.70 * (
            base["fib_swing_high"] - base["fib_swing_low"])
        fib = _compute_fibonacci(h, l, c)
        assert fib["fib_trend"] == "down"
        assert fib["fib_zone"] == "618_786"

    def test_voter_flips_sign_on_down_leg(self):
        sig = _sig()
        base = {"fib_zone": "618_786"}
        up = Analyzer._score_confluence({**base, "fib_trend": "up"}, Regime.RANGE, sig)
        down = Analyzer._score_confluence({**base, "fib_trend": "down"}, Regime.RANGE, sig)
        # Deep retrace: bullish on an up-leg, bearish on a down-leg.
        assert up > 0.5 > down

    def test_flag_defaults_on(self):
        assert CONFIG.analyzer.fib_direction_aware_enabled is True


class TestCandleTrendContext:
    def _bars(self, closes):
        c = np.asarray(closes, dtype=float)
        o = c - 0.1
        h = c + 3.0
        l = c - 3.0
        return o, h, l, c

    def test_hammer_requires_downtrend(self):
        # Hammer geometry on the last bar of a clear UPTREND -> rejected.
        n = 12
        closes = list(100.0 + np.arange(n) * 2.0)
        o = np.array([c - 0.2 for c in closes])
        h = np.array([c + 0.1 for c in closes])
        l = np.array([c - 0.2 for c in closes])
        c = np.array(closes)
        # Craft hammer geometry on the final candle: tiny body, long lower wick.
        o[-1] = c[-1] - 0.05
        h[-1] = c[-1] + 0.02
        l[-1] = c[-1] - 2.00
        pats = _detect_candlestick_patterns(o, h, l, c)
        assert "hammer" not in pats

    def test_hammer_fires_in_downtrend(self):
        n = 12
        closes = list(130.0 - np.arange(n) * 2.0)
        o = np.array([c + 0.2 for c in closes])
        h = np.array([c + 0.3 for c in closes])
        l = np.array([c - 0.3 for c in closes])
        c = np.array(closes)
        o[-1] = c[-1] - 0.05  # small bullish body
        h[-1] = c[-1] + 0.02
        l[-1] = c[-1] - 2.00  # long lower wick
        pats = _detect_candlestick_patterns(o, h, l, c)
        assert pats.get("hammer") == "bullish"

    def test_flags_default_on(self):
        assert CONFIG.analyzer.candle_trend_context_enabled is True
        assert CONFIG.analyzer.candle_strength_vote_enabled is True


class TestStrengthWeightedCandleVote:
    def test_three_soldiers_outvote_lone_hammer(self):
        sig = _sig()
        strong = {"candle_bullish_count": 1, "candle_bearish_count": 1,
                  "candle_bullish_strength": 1.5,   # three_white_soldiers
                  "candle_bearish_strength": 1.0}   # lone shooting_star
        score = Analyzer._score_confluence(strong, Regime.RANGE, sig)
        # Net-positive strength → bullish lean despite equal counts.
        assert score > 0.5


class TestVoterDilutionGuard:
    def test_missing_inputs_skip_not_dilute(self):
        sig = _sig(spike=False)
        # Only one real voter input: strongly bullish RSI. Legacy behaviour
        # appended 0-votes for macd/bb/adx/volume_spike, dragging the score
        # toward 0.5; with the guard they are skipped.
        only_rsi = {"rsi": 25}
        score = Analyzer._score_confluence(only_rsi, Regime.RANGE, sig)
        assert score > 0.85

    def test_flag_defaults_on(self):
        assert CONFIG.analyzer.voter_skip_missing_enabled is True
        assert CONFIG.analyzer.pattern_dedup_enabled is True


class TestNoTradeReasons:
    @pytest.mark.asyncio
    async def test_insufficient_candles_recorded(self):
        a = Analyzer()
        sig = _sig()
        candles = [[i, 100, 101, 99, 100, 1000] for i in range(5)]
        result = await a.analyze(sig, candles)
        assert result is None
        reason = a.get_no_trade_reason("BTC/USDT")
        assert reason is not None
        assert reason["stage"] == "data"
        assert "candles" in reason["reason"] or reason.get("candles") == 5


class TestCandleOrderingValidation:
    def _mk_candles(self, n=60, start=100.0):
        out = []
        price = start
        for i in range(n):
            price += 0.5
            out.append([i * 3600_000, price, price + 1, price - 1, price + 0.5, 1000.0])
        return out

    @pytest.mark.asyncio
    async def test_out_of_order_candles_are_sorted_not_rejected(self):
        a = Analyzer()
        sig = _sig(price=130.0)
        candles = self._mk_candles()
        # Shuffle two blocks out of order.
        candles = candles[30:] + candles[:30]
        result = await a.analyze(sig, candles)
        # Must not crash; either an idea or a structured non-data skip.
        reason = a.get_no_trade_reason("BTC/USDT")
        if result is None and reason is not None:
            assert reason["stage"] != "data" or "invalid" not in reason["reason"]

    @pytest.mark.asyncio
    async def test_duplicate_candles_deduped(self):
        a = Analyzer()
        sig = _sig(price=130.0)
        candles = self._mk_candles()
        candles = candles + [candles[-1]]  # duplicate final bar
        result = await a.analyze(sig, candles)
        reason = a.get_no_trade_reason("BTC/USDT")
        if result is None and reason is not None:
            assert "invalid" not in reason["reason"]


class TestDataQualityStamp:
    def test_flags_default_on(self):
        assert CONFIG.analyzer.data_quality_penalty_enabled is True
        assert 0.0 < CONFIG.analyzer.data_thin_penalty <= 0.5
