"""Tier 1: MTF confluence resurrection.

The MTFConfluence module (HH/HL/BOS/CHoCH structure + 1h/4h/1d alignment) was
dead code — no caller ever passed candles_4h/candles_1d into analyze(), so the
mtf_alignment / mtf_structure / mtf_bos voters never fired live OR in
backtests. These tests pin: the analyzer now derives HTF candles from the
engine's mtf_candles dict (gated MTF_CONFLUENCE_ENABLED, default ON), the
backtest resamples the primary series into CLOSED 4h/1d groups (no lookahead),
and the BOS/CHoCH votes are directional (a bearish break votes bearish even
under residual bullish alignment).
"""
from __future__ import annotations

import numpy as np
import pytest

from bot.config import CONFIG
from bot.core.multi_timeframe import MTFConfluence, MTFResult, _analyze_structure
from bot.utils.candles import resample_ohlcv

_H = 3_600_000


def _rows(n, tf_ms=_H, start=1_700_000_000_000 - (1_700_000_000_000 % 86_400_000),
          price=100.0, step=0.0):
    out = []
    p = price
    for i in range(n):
        out.append([start + i * tf_ms, p, p + 1, p - 1, p + step, 10.0])
        p += step
    return out


class TestResampleOhlcv:
    def test_aggregates_ohlcv_correctly(self):
        rows = _rows(8)  # aligned to a day boundary → two full 4h groups
        out = resample_ohlcv(rows, "1h", "4h")
        assert len(out) == 2
        g = out[0]
        assert g[1] == rows[0][1]                      # open of first source bar
        assert g[2] == max(r[2] for r in rows[:4])     # max high
        assert g[3] == min(r[3] for r in rows[:4])     # min low
        assert g[4] == rows[3][4]                      # close of last source bar
        assert g[5] == pytest.approx(sum(r[5] for r in rows[:4]))

    def test_trailing_partial_group_dropped(self):
        # 6 hourly bars from a 4h boundary = one full group + 2-bar partial.
        out = resample_ohlcv(_rows(6), "1h", "4h")
        assert len(out) == 1

    def test_non_divisible_or_equal_tf_rejected(self):
        assert resample_ohlcv(_rows(10), "1h", "90m") == []
        assert resample_ohlcv(_rows(10), "1h", "1h") == []
        assert resample_ohlcv(_rows(10), "4h", "1h") == []
        assert resample_ohlcv([], "1h", "4h") == []

    def test_daily_resample(self):
        out = resample_ohlcv(_rows(49), "1h", "1d")  # 2 full days + 1 bar
        assert len(out) == 2


def _structured_series(pattern: str, n=80):
    """Build highs/lows/closes with clear swings; pattern 'break_down' ends
    with a close below the last swing low."""
    rng = np.linspace(0, 6 * np.pi, n)
    base = 100 + 3 * np.sin(rng)
    highs = base + 0.5
    lows = base - 0.5
    closes = base.copy()
    if pattern == "break_down":
        closes[-1] = lows.min() - 2.0
        lows[-1] = closes[-1] - 0.2
        highs[-1] = closes[-1] + 0.4
    elif pattern == "break_up":
        closes[-1] = highs.max() + 2.0
        highs[-1] = closes[-1] + 0.2
        lows[-1] = closes[-1] - 0.4
    return highs, lows, closes


class TestDirectionalStructure:
    def test_bos_direction_recorded(self):
        h, l, c = _structured_series("break_down")
        res = _analyze_structure(h, l, c)
        assert res["bos"] is True and res["bos_dir"] == -1
        h, l, c = _structured_series("break_up")
        res = _analyze_structure(h, l, c)
        assert res["bos"] is True and res["bos_dir"] == 1

    def test_bearish_break_votes_bearish_despite_bullish_alignment(self):
        # Regression: old code voted alignment_score for mtf_bos, so a bearish
        # break under bullish EMA alignment cast a BULLISH "mtf_bos" vote.
        result = MTFResult(alignment_score=0.6, structure_bias=-0.3,
                           bos_detected=True, bos_dir=-1, confidence=1.0)
        votes, weights, labels = MTFConfluence.to_confluence_votes(result)
        bos_vote = votes[labels.index("mtf_bos")]
        assert bos_vote < 0

    def test_choch_votes_in_new_direction(self):
        result = MTFResult(alignment_score=0.0, structure_bias=0.0,
                           choch_detected=True, choch_dir=-1, confidence=1.0)
        votes, weights, labels = MTFConfluence.to_confluence_votes(result)
        assert "mtf_choch" in labels
        assert votes[labels.index("mtf_choch")] == -1.0


class TestAnalyzerWiring:
    def test_flag_default_on(self):
        assert CONFIG.analyzer.mtf_confluence_enabled is True

    @pytest.mark.asyncio
    async def test_mtf_receives_htf_candles_from_mtf_candles(self):
        from bot.core.analyzer import Analyzer
        from bot.utils.models import MarketSignal

        analyzer = Analyzer()
        analyzer._llm = None
        seen = {}
        real = analyzer._mtf.analyze

        def spy(candles_1h=None, candles_4h=None, candles_1d=None):
            seen["c1h"] = candles_1h
            seen["c4h"] = candles_4h
            seen["c1d"] = candles_1d
            return real(candles_1h=candles_1h, candles_4h=candles_4h,
                        candles_1d=candles_1d)
        analyzer._mtf.analyze = spy

        candles = _rows(200, price=100.0, step=0.05)
        mtf = {"1h": candles, "4h": resample_ohlcv(candles, "1h", "4h"),
               "1d": resample_ohlcv(candles, "1h", "1d")}
        sig = MarketSignal(symbol="BTC/USDT", price=float(candles[-1][4]),
                           change_pct_24h=1.0, volume_usd_24h=1e6)
        await analyzer.analyze(sig, candles, mtf_candles=mtf, timeframe="1h")
        assert seen.get("c4h"), "4h candles must reach MTFConfluence"
        assert seen["c1h"] is candles  # primary IS 1h → allowed in the 1h slot

    @pytest.mark.asyncio
    async def test_flag_off_keeps_mtf_dead(self):
        from bot.core.analyzer import Analyzer
        from bot.utils.models import MarketSignal

        analyzer = Analyzer()
        analyzer._llm = None
        called = {"n": 0}

        def spy(**kwargs):
            called["n"] += 1
            return MTFResult()
        analyzer._mtf.analyze = spy

        candles = _rows(200, price=100.0, step=0.05)
        mtf = {"4h": resample_ohlcv(candles, "1h", "4h")}
        sig = MarketSignal(symbol="BTC/USDT", price=float(candles[-1][4]),
                           change_pct_24h=1.0, volume_usd_24h=1e6)
        old = CONFIG.analyzer.mtf_confluence_enabled
        object.__setattr__(CONFIG.analyzer, "mtf_confluence_enabled", False)
        try:
            await analyzer.analyze(sig, candles, mtf_candles=mtf, timeframe="1h")
        finally:
            object.__setattr__(CONFIG.analyzer, "mtf_confluence_enabled", old)
        assert called["n"] == 0

    @pytest.mark.asyncio
    async def test_non_1h_primary_not_mislabeled(self):
        from bot.core.analyzer import Analyzer
        from bot.utils.models import MarketSignal

        analyzer = Analyzer()
        analyzer._llm = None
        seen = {}

        def spy(candles_1h=None, candles_4h=None, candles_1d=None):
            seen["c1h"] = candles_1h
            return MTFResult()
        analyzer._mtf.analyze = spy

        candles = _rows(200, tf_ms=4 * _H, price=100.0, step=0.05)  # 4h primary
        mtf = {"1d": resample_ohlcv(candles, "4h", "1d")}
        sig = MarketSignal(symbol="BTC/USDT", price=float(candles[-1][4]),
                           change_pct_24h=1.0, volume_usd_24h=1e6)
        await analyzer.analyze(sig, candles, mtf_candles=mtf, timeframe="4h")
        assert seen.get("c1h") is None  # 4h primary must NOT enter the 1h slot


class TestBacktestParity:
    @pytest.mark.asyncio
    async def test_backtest_runs_with_mtf_and_stays_deterministic(self):
        from bot.backtest.data_loader import DataLoader
        from bot.backtest.engine import BacktestEngine
        from bot.backtest.models import BacktestConfig

        bars = DataLoader.generate_synthetic(bars=900, seed=11)
        results = []
        for _ in range(2):
            eng = BacktestEngine(BacktestConfig(initial_balance=10_000.0))
            res = await eng.run(bars)
            eng.cleanup()
            results.append((res.total_trades, round(res.total_return_pct, 4)))
        assert results[0] == results[1]
