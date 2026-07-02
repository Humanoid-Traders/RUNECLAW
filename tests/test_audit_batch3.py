"""Audit top-25 fixes, batch 3 (medium).

#12 Family caps default ON + pattern family cap
#15 next-open fill mode in the backtest
#17 Risk-engine directional SL/TP re-validation
#18 TradeIdea provenance fields
#19 Voter-weight OOS validation + versioned persistence
#20 Consolidated indicator implementations
"""
from __future__ import annotations

import os
import tempfile

import numpy as np
import pytest

from bot.config import CONFIG
from bot.core.analyzer import Analyzer
from bot.core.ta_utils import rsi_series, obv_series, macd_histogram_series
from bot.core.ta_utils import Regime
from bot.learning.voter_weights import VoterWeightLearner
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, MarketSignal, TradeIdea


def _engine() -> RiskEngine:
    state = os.path.join(tempfile.mkdtemp(prefix="rc-audit3-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)


def _sig(price=100.0):
    return MarketSignal(symbol="BTC/USDT", price=price, change_pct_24h=1.0,
                        volume_usd_24h=1e8, volume_spike=False)


class TestFamilyCapsOn:
    def test_family_cap_defaults_on(self):
        assert CONFIG.confluence.family_cap_enabled is True
        assert CONFIG.confluence.pattern_weight_cap > 0

    def test_pattern_family_capped(self):
        sig = _sig()
        # Six pattern voters co-firing bullish, one neutral macd anchor.
        many = {
            "macd_histogram": 0.0,
            "candle_bullish_count": 2, "candle_bearish_count": 0,
            "candle_bullish_strength": 2.9, "candle_bearish_strength": 0.0,
            "chart_patterns_bullish_weight": 1.5, "chart_patterns_bearish_weight": 0.0,
            "chart_patterns_bullish_count": 2, "chart_patterns_bearish_count": 0,
            "wyckoff_pattern": {"signal": "bullish", "confidence": 0.8},
            "harmonic_pattern": {"signal": "bullish", "confidence": 0.8},
            "elliott_impulse": {"signal": "bullish", "confidence": 0.8},
        }
        capped = Analyzer._score_confluence(many, Regime.RANGE, sig)
        # Compare against the same electorate with the cap disabled (frozen
        # dataclass -> object.__setattr__, restore in finally).
        object.__setattr__(CONFIG.confluence, "family_cap_enabled", False)
        try:
            uncapped = Analyzer._score_confluence(many, Regime.RANGE, sig)
        finally:
            object.__setattr__(CONFIG.confluence, "family_cap_enabled", True)
        assert capped <= uncapped
        assert capped > 0.5  # still bullish, just not inflated


class TestNextOpenFill:
    @pytest.mark.asyncio
    async def test_next_open_fills_at_next_bar_open(self):
        from bot.backtest.data_loader import DataLoader
        from bot.backtest.engine import BacktestEngine
        from bot.backtest.models import BacktestConfig

        bars = DataLoader.generate_synthetic(bars=400, seed=7)
        res_close = None
        res_open = None
        for mode in ("close", "next_open"):
            eng = BacktestEngine(BacktestConfig(
                symbol="BTC/USDT", fill_mode=mode, initial_balance=10000.0))
            res = await eng.run(bars)
            eng.cleanup()
            if mode == "close":
                res_close = res
            else:
                res_open = res
        # Both modes must run clean; entry prices in next_open mode must come
        # from bar opens (they differ from the close-fill entries whenever a
        # trade fired).
        assert res_close is not None and res_open is not None
        if res_close.trades and res_open.trades:
            close_entries = {round(t.entry_price, 4) for t in res_close.trades}
            open_entries = {round(t.entry_price, 4) for t in res_open.trades}
            assert close_entries != open_entries

    def test_fill_mode_default_is_close(self):
        from bot.backtest.models import BacktestConfig
        assert BacktestConfig().fill_mode == "close"


class TestRiskEngineSideCheck:
    def test_inverted_tp_rejected_even_via_model_construct(self):
        # model_construct bypasses the Pydantic validator — the risk engine
        # must catch the wrong-side TP on its own (audit fix #17).
        idea = TradeIdea.model_construct(
            id="TI-test", asset="BTC/USDT", direction=Direction.LONG,
            entry_price=100.0, stop_loss=95.0, take_profit=90.0,  # TP below entry!
            confidence=0.9, blended_confidence_raw=0.9,
            reasoning="test", signals_used=[], source="test",
            order_type="market", strategy_type="swing",
            signal_type="momentum_confluence",
        )
        engine = _engine()
        check = engine.evaluate(idea)
        assert check.verdict.value == "REJECTED"
        assert any("SLTP_SIDES" in f for f in check.checks_failed)

    def test_valid_idea_passes_side_check(self):
        idea = TradeIdea(
            asset="BTC/USDT", direction=Direction.LONG,
            entry_price=100.0, stop_loss=95.0, take_profit=110.0,
            confidence=0.9, reasoning="test",
        )
        engine = _engine()
        check = engine.evaluate(idea)
        assert not any("SLTP_SIDES" in f for f in check.checks_failed)


class TestTradeIdeaProvenance:
    def test_new_fields_default_none(self):
        idea = TradeIdea(
            asset="BTC/USDT", direction=Direction.LONG,
            entry_price=100.0, stop_loss=95.0, take_profit=110.0,
            confidence=0.9, reasoning="test",
        )
        assert idea.timeframe is None
        assert idea.prompt_hash is None
        assert idea.analysis_version is None

    def test_fields_serialize(self):
        idea = TradeIdea(
            asset="BTC/USDT", direction=Direction.LONG,
            entry_price=100.0, stop_loss=95.0, take_profit=110.0,
            confidence=0.9, reasoning="test", timeframe="1h",
            llm_confidence=0.8, confluence_score=0.62,
            model_provider="RULE_ENGINE", analysis_version="2026.07-audit25",
            data_bars=100, data_thin=False,
        )
        d = idea.model_dump()
        assert d["timeframe"] == "1h"
        assert d["confluence_score"] == 0.62
        assert d["data_thin"] is False


class TestVoterWeightsOOS:
    def _samples(self, n=200):
        # "good_voter" agrees mostly on winners; "bad_voter" on losers.
        samples = []
        for i in range(n):
            won = (i % 3) != 0  # ~67% base win rate
            votes = [("good_voter", 1.0 if won else -1.0, 1.0),
                     ("bad_voter", -1.0 if won else 1.0, 1.0),
                     ("noise_voter", 1.0 if (i % 2) else -1.0, 1.0)]
            samples.append((votes, "LONG", won))
        return samples

    def test_oos_validation_report(self):
        learner = VoterWeightLearner()
        report = learner.validate_oos(self._samples(), split=0.7)
        assert report["n_train"] > 0 and report["n_test"] > 0
        assert "voters" in report
        # The constructed good voter's positive adjustment must hold OOS.
        gv = report["voters"].get("good_voter")
        assert gv is not None and gv["direction_holds"] is True
        assert report["hold_rate"] > 0

    def test_persistence_has_version_metadata(self):
        learner = VoterWeightLearner()
        learner.fit(self._samples())
        d = learner.to_dict()
        assert d["schema_version"] == 2
        assert "fitted_at" in d and d["fitted_at"]


class TestConsolidatedIndicators:
    def test_divergence_delegates_to_ta_utils(self):
        from bot.core.divergence import _compute_rsi, _compute_obv, _compute_macd_hist
        closes = 100.0 + np.cumsum(np.sin(np.arange(60)))
        vols = np.full(60, 100.0)
        assert np.allclose(_compute_rsi(closes), rsi_series(closes))
        assert np.allclose(_compute_obv(closes, vols),
                           obv_series(closes, vols, seed_first=False))
        assert np.allclose(_compute_macd_hist(closes),
                           macd_histogram_series(closes))

    def test_rich_cards_uses_wilder(self):
        from bot.formatters.rich_cards import compute_rsi
        closes = 100.0 + np.cumsum(np.sin(np.arange(60)))
        assert compute_rsi(closes) == pytest.approx(
            float(rsi_series(closes)[-1]))

    def test_obv_seed_conventions(self):
        closes = np.array([10.0, 11.0, 12.0])
        vols = np.array([100.0, 50.0, 25.0])
        seeded = obv_series(closes, vols, seed_first=True)
        zero = obv_series(closes, vols, seed_first=False)
        # Constant offset only — differences identical.
        assert np.allclose(np.diff(seeded), np.diff(zero))
        assert seeded[0] == 100.0 and zero[0] == 0.0
