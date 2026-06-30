"""
Feed paper/sim closes into the learning loop (deep-audit medium).

record_closed_outcome (the learning loop's WRITE side) fired only on LIVE
position closes, so in simulation-first operation the learners saw almost no
data. With LEARN_FROM_PAPER_CLOSES on, each paper close also records an outcome
tagged source="paper_outcome" (live stays "live_outcome"), so similar-setup
lookups / calibration accumulate from the abundant paper history. Default OFF
keeps the write side live-only (byte-identical).
"""

import inspect

import bot.core.engine as engine_mod
from bot.learning.orchestrator import LearningOrchestrator


def _orch(tmp_path):
    return LearningOrchestrator(data_dir=str(tmp_path))


class TestSourceTag:
    def test_default_source_is_live(self, tmp_path):
        rec = _orch(tmp_path).record_closed_outcome(
            symbol="BTC/USDT", direction="LONG", pnl_result=10.0)
        assert rec.source == "live_outcome"

    def test_paper_source_is_tagged(self, tmp_path):
        rec = _orch(tmp_path).record_closed_outcome(
            symbol="BTC/USDT", direction="LONG", pnl_result=-5.0,
            source="paper_outcome")
        assert rec.source == "paper_outcome"

    def test_blank_source_falls_back_to_live(self, tmp_path):
        rec = _orch(tmp_path).record_closed_outcome(
            symbol="BTC/USDT", direction="LONG", pnl_result=1.0, source="")
        assert rec.source == "live_outcome"


class TestPaperFeedsLearning:
    def test_paper_outcome_counts_in_learning_context(self, tmp_path):
        orch = _orch(tmp_path)
        orch.record_closed_outcome(
            symbol="ETH/USDT", direction="LONG", pnl_result=12.0,
            market_regime="TREND_UP", source="paper_outcome")
        ctx = orch.get_learning_context(
            symbol="ETH/USDT", market_regime="TREND_UP", direction="LONG")
        # The READ side (get_similar_setups) filters on pnl_result, not source,
        # so a paper outcome contributes to the learning context.
        assert ctx["similar_past_setups"] >= 1
        assert ctx["avg_past_pnl"] == 12.0


class TestEngineWiring:
    def test_engine_records_paper_closes_gated(self):
        src = inspect.getsource(engine_mod)
        # The closed-trade loop records the paper outcome, tagged + gated.
        assert 'source="paper_outcome"' in src
        assert "CONFIG.learning.learn_from_paper_closes_enabled" in src

    def test_live_close_path_still_records(self):
        # The live close hook keeps recording (unchanged) — no source override,
        # so it defaults to live_outcome.
        src = inspect.getsource(engine_mod.RuneClawEngine._on_live_position_closed)
        assert "record_closed_outcome" in src


class TestConfigDefault:
    def test_flag_defaults_on(self, monkeypatch):
        # Enabled by default (operator-requested activation); explicit env still wins.
        monkeypatch.delenv("LEARN_FROM_PAPER_CLOSES", raising=False)
        from bot.config import LearningConfig
        assert LearningConfig().learn_from_paper_closes_enabled is True
