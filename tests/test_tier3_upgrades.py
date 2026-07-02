"""Tier 3 batch 2: MFI, per-bar volume spike, SMC voters (FVG / pools /
premium-discount), learned-weight coverage for direct-append voters, sweep
age decay + per-bar dedup, and the wired mode min-confidence floor."""
from __future__ import annotations

import numpy as np

from bot.config import CONFIG
from bot.core.analyzer import Analyzer
from bot.core.liquidity_sweep import SweepSignal, sweep_to_confluence_votes
from bot.core.smc import (FVG, equal_level_pools, find_fvgs, fvg_vote,
                          premium_discount)


def _ind(closes, highs=None, lows=None, volumes=None):
    closes = np.asarray(closes, dtype=float)
    highs = np.asarray(highs, dtype=float) if highs is not None else closes + 0.5
    lows = np.asarray(lows, dtype=float) if lows is not None else closes - 0.5
    volumes = (np.asarray(volumes, dtype=float) if volumes is not None
               else np.full(len(closes), 10.0))
    return Analyzer._compute_indicators(highs, lows, closes, volumes,
                                        opens=closes.copy())


class TestMFI:
    def test_mfi_computed_and_bounded(self):
        closes = 100 + np.cumsum(np.sin(np.arange(60)))
        ind = _ind(closes)
        assert "mfi" in ind and 0.0 <= ind["mfi"] <= 100.0

    def test_uptrend_money_flow_high(self):
        closes = np.linspace(100, 130, 60)   # relentless buying
        ind = _ind(closes)
        assert ind["mfi"] > 80


class TestVolSpikeBar:
    def test_spike_detected_with_direction(self):
        closes = np.full(40, 100.0)
        closes[-1] = 101.0
        volumes = np.full(40, 10.0)
        volumes[-1] = 40.0   # 4x the prior average
        ind = _ind(closes, volumes=volumes)
        assert ind["vol_spike_bar"] is True
        assert ind["vol_spike_bar_dir"] == 1
        assert ind["vol_spike_bar_ratio"] >= 3.5

    def test_quiet_bar_no_spike(self):
        closes = np.full(40, 100.0)
        ind = _ind(closes)
        assert ind.get("vol_spike_bar") is False


class TestSMC:
    def test_bullish_fvg_detected_and_fill_tracked(self):
        n = 30
        highs = np.full(n, 101.0)
        lows = np.full(n, 99.0)
        closes = np.full(n, 100.0)
        # Displacement at i=20: candle 20's low gaps above candle 18's high.
        lows[20], highs[20], closes[20] = 103.0, 106.0, 105.0
        lows[21:], highs[21:], closes[21:] = 104.0, 106.0, 105.0  # unfilled
        fvgs = find_fvgs(highs, lows, closes)
        bulls = [g for g in fvgs if g.kind == "bullish"]
        assert bulls and not bulls[0].filled
        # Price 104.5 sits within 1 ATR above the gap top (103) → support vote.
        vote, weight = fvg_vote(bulls, price=104.0, atr=2.0)
        assert vote == 1.0 and weight > 0

    def test_filled_fvg_does_not_vote(self):
        g = FVG("bullish", top=103.0, bottom=101.0, bar_index=5, filled=True)
        vote, weight = fvg_vote([g], price=103.5, atr=2.0)
        assert (vote, weight) == (0.0, 0.0)

    def test_equal_lows_pool(self):
        n = 60
        highs = np.full(n, 101.0)
        lows = np.full(n, 100.0)
        for i in (15, 30, 45):   # three separated equal swing lows
            lows[i] = 95.02 if i == 30 else 95.0
        pools = equal_level_pools(highs, lows, atr=1.0)
        assert any(abs(p - 95.0) < 0.2 for p in pools["eql"])

    def test_premium_discount_positions(self):
        highs = np.linspace(100, 120, 100)
        lows = highs - 1
        closes = highs - 0.5
        pd = premium_discount(highs, lows, closes)
        assert pd is not None and pd > 0.9   # at the top of the range


class TestSweepDecayDedup:
    def _sig(self, bars_ago, conf=0.8, kind="bullish_sweep"):
        return SweepSignal(
            sweep_type=kind, level_price=100.0, sweep_low=99.0,
            sweep_high=101.0, close_price=100.5, depth_pct=0.3,
            reversal_strength=0.7, volume_ratio=1.5, level_touches=3,
            confidence=conf, suggested_entry=100.4, suggested_sl=98.9,
            description="t", bars_ago=bars_ago)

    def test_same_bar_stacked_levels_deduped(self):
        # One candle sweeping two stacked levels = ONE vote, not two.
        votes, weights = sweep_to_confluence_votes(
            [self._sig(0, 0.8), self._sig(0, 0.6)])
        assert len(votes) == 1

    def test_age_decays_weight(self):
        (v0, w0) = sweep_to_confluence_votes([self._sig(0)])
        (v4, w4) = sweep_to_confluence_votes([self._sig(4)])
        assert w4[0] < w0[0]
        assert w4[0] >= 0.3 * w0[0] - 1e-9

    def test_distinct_bars_both_vote(self):
        votes, weights = sweep_to_confluence_votes(
            [self._sig(0, 0.8), self._sig(3, 0.9, "bearish_sweep")])
        assert len(votes) == 2


class TestModeMinConfidence:
    def test_flag_default_on(self):
        assert CONFIG.analyzer.mode_min_confidence_enabled is True

    def test_mode_configs_have_no_dead_sltp_fields(self):
        from bot.core.strategy_modes import MODE_CONFIGS
        for cfg in MODE_CONFIGS.values():
            assert not hasattr(cfg, "sl_mult")
            assert not hasattr(cfg, "tp_mult")
            assert cfg.min_confidence >= 0.5   # the live knob
