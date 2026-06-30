"""
Calibrator trains on the same field it applies to (deep-audit low #35).

The confidence calibrator is APPLIED to the analyzer-stage blended confidence
(before the calibration remap and setup-expectancy nudge), but it used to TRAIN
on the decision record's `confidence` — the post-adjustment value. Fitting one
distribution and remapping another is a systematic miscalibration. The decision
record now carries `blended_confidence_raw` (the apply-target), and
samples_from_decisions trains on it, falling back to `confidence` for older rows
that predate the field.
"""

from bot.learning.confidence_calibration import ConfidenceCalibrator
from bot.learning.models import DecisionMemory


def _decision(tid, *, confidence=0.0, blended_raw=0.0, pnl=None):
    return DecisionMemory(symbol="BTC/USDT", direction="LONG",
                          confidence=confidence, blended_confidence_raw=blended_raw,
                          paper_trade_id=tid, pnl_result=pnl)


class TestTrainsOnApplyField:
    def test_uses_blended_raw_when_present(self):
        # Decision row carries the apply-target (0.7); the outcome row sets pnl.
        decisions = [
            _decision("t1", confidence=0.9, blended_raw=0.7),       # decision
            _decision("t1", pnl=5.0),                               # winning outcome
        ]
        samples = ConfidenceCalibrator.samples_from_decisions(decisions)
        # Trains on the blended_raw (0.7), NOT the post-adjustment confidence 0.9.
        assert samples == [(0.7, True)]

    def test_falls_back_to_confidence_for_legacy_rows(self):
        # Old decision rows have no blended_confidence_raw (0.0) → use confidence.
        decisions = [
            _decision("t2", confidence=0.8, blended_raw=0.0),
            _decision("t2", pnl=-3.0),                              # losing outcome
        ]
        samples = ConfidenceCalibrator.samples_from_decisions(decisions)
        assert samples == [(0.8, False)]

    def test_blended_raw_takes_precedence_over_confidence(self):
        decisions = [
            _decision("t3", confidence=0.95, blended_raw=0.55),
            _decision("t3", pnl=1.0),
        ]
        (conf, won), = ConfidenceCalibrator.samples_from_decisions(decisions)
        assert conf == 0.55 and won is True


class TestModelsCarryField:
    def test_decision_memory_defaults_zero(self):
        assert DecisionMemory().blended_confidence_raw == 0.0

    def test_trade_idea_carries_field(self):
        from bot.utils.models import Direction, TradeIdea
        idea = TradeIdea(asset="BTC/USDT", direction=Direction.LONG,
                         entry_price=100.0, stop_loss=95.0, take_profit=110.0,
                         confidence=0.8, reasoning="t", blended_confidence_raw=0.72)
        assert idea.blended_confidence_raw == 0.72
