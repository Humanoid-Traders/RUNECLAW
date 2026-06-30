"""
Paper trades feed the calibration / voter-weight learners (opt-in).

confidence_calibration and voter_weights JOIN a DECISION row to an OUTCOME row by
paper_trade_id. Per-user paper (practice) fills recorded an outcome row but never a
decision row, so paper trades contributed ZERO to those two learners. With
LEARN_CALIBRATION_FROM_PAPER on, _simulate_paper_fill also logs a decision row
(tagged source="paper_decision", keyed by trade.trade_id) so the pair joins.

Default OFF: those learners adjust LIVE confidence + the admin auto-trade gate, so
paper-derived calibration influencing live is an explicit operator opt-in.
"""

import inspect

import bot.core.engine as engine_mod
from bot.learning.confidence_calibration import ConfidenceCalibrator
from bot.learning.models import DecisionMemory


def _decision(tid, conf):
    return DecisionMemory(
        source="paper_decision", symbol="BTC/USDT", direction="LONG",
        confidence=conf, blended_confidence_raw=conf, confluence_score=conf,
        paper_trade_id=tid, decision="TRADE_ACCEPTED_PAPER",
    )


def _outcome(tid, pnl):
    return DecisionMemory(
        source="paper_outcome", symbol="BTC/USDT", direction="LONG",
        pnl_result=pnl, paper_trade_id=tid, decision=f"OUTCOME:{tid}",
    )


class TestPaperPairJoins:
    def test_paper_decision_joins_to_paper_outcome(self):
        # A paper decision row + its paper outcome row → one (confidence, won) pair.
        rows = [_decision("TI-x", 0.72), _outcome("TI-x", 12.0)]
        pairs = ConfidenceCalibrator.samples_from_decisions(rows)
        assert (0.72, True) in pairs

    def test_loss_outcome_marks_not_won(self):
        rows = [_decision("TI-y", 0.6), _outcome("TI-y", -8.0)]
        pairs = ConfidenceCalibrator.samples_from_decisions(rows)
        assert (0.6, False) in pairs

    def test_decision_without_outcome_is_dropped(self):
        # No matching outcome → no pair (join is by paper_trade_id).
        pairs = ConfidenceCalibrator.samples_from_decisions([_decision("TI-z", 0.8)])
        assert pairs == []


class TestEngineWiring:
    def test_paper_fill_logs_gated_decision(self):
        src = inspect.getsource(engine_mod.RuneClawEngine._simulate_paper_fill)
        # Gated on the new opt-in flag, logs a decision tagged paper_decision,
        # keyed by the SAME id the outcome row uses (trade.trade_id).
        assert "learn_calibration_from_paper_enabled" in src
        assert 'source="paper_decision"' in src
        assert "paper_trade_id=trade.trade_id" in src
        assert "log_decision" in src


class TestConfigDefault:
    def test_flag_defaults_off(self, monkeypatch):
        # Opt-in: paper-derived calibration must not influence live by default.
        monkeypatch.delenv("LEARN_CALIBRATION_FROM_PAPER", raising=False)
        from bot.config import LearningConfig
        assert LearningConfig().learn_calibration_from_paper_enabled is False
