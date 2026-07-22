"""Tier 0 money-path safety: limit-recalc SL/TP handling, forming-candle
policy in the executor, and MTF refinement stop tightening.

Signal-stack audit findings: the executor's limit-recalc path could (a) move
the entry up to ~2 ATR without shifting SL/TP — a LONG could fill at/below
its own stop; (b) accept a structure "natural SL" that WIDENED the stop up to
2x AFTER position sizing, silently violating the leverage margin-risk cap;
(c) accept a natural SL on the wrong side of the recalculated entry — an
instant stop-out. And _refine_entry_mtf's "tighter stop" used min()/max() the
wrong way round, so it could only ever widen risk.
"""
from __future__ import annotations

import time

import pytest

from bot.compat import UTC
from datetime import datetime

from bot.core.live_executor import recalc_sl_tp_for_shifted_entry
from bot.utils.candles import drop_forming_candle, timeframe_to_ms
from bot.utils.models import Direction, TradeIdea


class TestRecalcSlTp:
    def test_entry_shift_preserves_stop_distance(self):
        # LONG limit moved DOWN 2 ATR from the idea entry: SL/TP must shift
        # by the same amount so risk-per-unit (sizing basis) is unchanged.
        sl, tp, shifted, nat = recalc_sl_tp_for_shifted_entry(
            entry_price=100.0, stop_loss=98.0, take_profit=106.0,
            limit_price=96.0, natural_sl=None, side="buy")
        assert shifted is True
        assert sl == pytest.approx(94.0)
        assert tp == pytest.approx(102.0)
        # Stop distance identical to pre-shift geometry.
        assert (96.0 - sl) == pytest.approx(100.0 - 98.0)

    def test_unshifted_when_limit_equals_entry(self):
        sl, tp, shifted, _ = recalc_sl_tp_for_shifted_entry(
            entry_price=100.0, stop_loss=98.0, take_profit=106.0,
            limit_price=100.0, natural_sl=None, side="buy")
        assert (sl, tp, shifted) == (98.0, 106.0, False)

    def test_long_no_longer_fills_below_own_stop(self):
        # Regression for the audit failure scenario: entry recalculated to
        # 97.5 with an unshifted SL at 98 meant filling BELOW the stop.
        sl, _tp, _s, _n = recalc_sl_tp_for_shifted_entry(
            entry_price=100.0, stop_loss=98.0, take_profit=106.0,
            limit_price=97.5, natural_sl=None, side="buy")
        assert sl < 97.5

    def test_natural_sl_tightens_only(self):
        # Natural SL closer than the shifted stop → applied.
        sl, _tp, _s, nat = recalc_sl_tp_for_shifted_entry(
            entry_price=100.0, stop_loss=98.0, take_profit=106.0,
            limit_price=100.0, natural_sl=99.0, side="buy")
        assert nat == "applied" and sl == 99.0
        # Natural SL WIDER than current stop → rejected (old code accepted
        # anything up to 2x wider, after sizing).
        sl, _tp, _s, nat = recalc_sl_tp_for_shifted_entry(
            entry_price=100.0, stop_loss=98.0, take_profit=106.0,
            limit_price=100.0, natural_sl=96.5, side="buy")
        assert nat == "rejected_wider" and sl == 98.0

    def test_natural_sl_wrong_side_rejected(self):
        # LONG with a "natural SL" ABOVE the limit price (cluster entry below
        # session low) must be rejected, not applied via abs() distance.
        sl, _tp, _s, nat = recalc_sl_tp_for_shifted_entry(
            entry_price=100.0, stop_loss=98.0, take_profit=106.0,
            limit_price=97.0, natural_sl=97.8, side="buy")
        assert nat == "rejected_wrong_side"
        assert sl < 97.0  # shifted stop retained, below entry

    def test_short_side_mirrors(self):
        sl, tp, shifted, nat = recalc_sl_tp_for_shifted_entry(
            entry_price=100.0, stop_loss=102.0, take_profit=94.0,
            limit_price=103.0, natural_sl=104.0, side="sell")
        assert shifted is True
        assert sl == pytest.approx(104.0)  # natural 104 < shifted 105 → tighter, applied
        assert nat == "applied"
        assert tp == pytest.approx(97.0)
        # Wrong side for a short: natural below entry.
        _sl, _tp, _s, nat = recalc_sl_tp_for_shifted_entry(
            entry_price=100.0, stop_loss=102.0, take_profit=94.0,
            limit_price=103.0, natural_sl=101.0, side="sell")
        assert nat == "rejected_wrong_side"


class TestDropFormingCandle:
    def _bars(self, n, tf_ms, last_open_ms):
        return [[last_open_ms - (n - 1 - i) * tf_ms, 1, 2, 0.5, 1.5, 10]
                for i in range(n)]

    def test_forming_last_bar_dropped(self):
        tf_ms = timeframe_to_ms("1h")
        now_ms = time.time() * 1000
        bars = self._bars(10, tf_ms, last_open_ms=now_ms - tf_ms / 2)  # mid-bar
        out = drop_forming_candle(bars, "1h")
        assert len(out) == 9 and out[-1][0] == bars[-2][0]

    def test_closed_last_bar_kept(self):
        tf_ms = timeframe_to_ms("1h")
        now_ms = time.time() * 1000
        bars = self._bars(10, tf_ms, last_open_ms=now_ms - 1.5 * tf_ms)
        assert len(drop_forming_candle(bars, "1h")) == 10

    def test_short_series_passthrough(self):
        bars = [[0, 1, 2, 0.5, 1.5, 10]]
        assert drop_forming_candle(bars, "1h") == bars

    def test_matches_engine_policy(self):
        # The engine method must remain a delegate of the shared util so the
        # executor and analysis paths can never diverge.
        from bot.core.engine import RuneClawEngine
        eng = RuneClawEngine.__new__(RuneClawEngine)
        tf_ms = timeframe_to_ms("15m")
        now_ms = time.time() * 1000
        bars = self._bars(8, tf_ms, last_open_ms=now_ms - tf_ms / 3)
        assert eng._drop_forming_candle(bars, "15m") == drop_forming_candle(bars, "15m")


class TestRefineEntryMtfStopTighten:
    def _idea(self, direction=Direction.LONG, entry=100.0, sl=97.0, tp=106.0):
        return TradeIdea(
            asset="BTC/USDT", direction=direction, entry_price=entry,
            stop_loss=sl, take_profit=tp, confidence=0.8,
            reasoning="t", source="scan", timestamp=datetime.now(UTC),
            strategy_type="swing",
        )

    def _candles_with_support(self, support=99.2, current=100.0, n=48):
        """15m candles whose recent window has a clear pivot low at `support`
        and whose last close is `current` (support ~0.8% below → refine zone)."""
        ts0 = 1_700_000_000_000
        bars = []
        for i in range(n):
            base = 100.4
            low = base - 0.2
            if i == n - 8:            # pivot low inside recent_lows window
                low = support
            bars.append([ts0 + i * 900_000, base, base + 0.3, low, current, 10.0])
        return bars

    @pytest.mark.asyncio
    async def test_long_stop_never_widened(self):
        from bot.core.engine import RuneClawEngine
        eng = RuneClawEngine.__new__(RuneClawEngine)
        candles = self._candles_with_support()

        async def _fake_ohlcv(exchange, symbol, tf, limit=48, ttl=60):
            return candles
        eng._cached_ohlcv = _fake_ohlcv

        # Original SL is TIGHT (99.5, above the 15m structure low ~99.0):
        # the old code took min(SL, min_low*0.998) and WIDENED it. Now the
        # stop must never end up further from entry than it started.
        idea = self._idea(sl=99.5)
        refined = await eng._refine_entry_mtf(idea, exchange=None)
        assert refined.stop_loss >= idea.stop_loss - 1e-9

    @pytest.mark.asyncio
    async def test_long_stop_tightened_to_structure(self):
        from bot.core.engine import RuneClawEngine
        eng = RuneClawEngine.__new__(RuneClawEngine)
        candles = self._candles_with_support(support=99.2)

        async def _fake_ohlcv(exchange, symbol, tf, limit=48, ttl=60):
            return candles
        eng._cached_ohlcv = _fake_ohlcv

        # Wide original stop (97): structure low buffer (~99.0) is a valid
        # TIGHTER stop below the refined entry → should be adopted.
        idea = self._idea(sl=97.0)
        refined = await eng._refine_entry_mtf(idea, exchange=None)
        if refined.entry_price != idea.entry_price:  # refinement triggered
            assert refined.stop_loss > idea.stop_loss
            assert refined.stop_loss < refined.entry_price
