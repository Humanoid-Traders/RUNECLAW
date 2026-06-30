"""
Walk-forward confidence-threshold is now honored (deep-audit finding).

--wf-optimize sweeps BacktestConfig.confidence_threshold, but the engine never
read it, so every grid entry produced identical in-sample results and the
optimization was a degenerate no-op. The engine now applies it as a per-run
minimum-confidence gate in _evaluate_bar, and its default is 0.0 so DEFAULT
backtests are unchanged (entries still governed by the analyzer/risk floors).
"""

from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestConfig

_gate = BacktestEngine._below_confidence_gate


class TestConfidenceGate:
    def test_disabled_when_threshold_zero(self):
        assert _gate(0.10, 0.0) is False
        assert _gate(0.99, 0.0) is False

    def test_below_threshold_is_skipped(self):
        assert _gate(0.55, 0.60) is True

    def test_at_threshold_passes(self):
        # Strict `<`: exactly at the threshold is allowed.
        assert _gate(0.60, 0.60) is False

    def test_above_threshold_passes(self):
        assert _gate(0.72, 0.60) is False

    def test_negative_threshold_disabled(self):
        assert _gate(0.10, -1.0) is False


class TestDefaultUnchanged:
    def test_default_threshold_is_zero(self):
        # Default backtests must be byte-identical: 0.0 = no extra gate, so the
        # analyzer/risk floors alone govern entries (as before this fix).
        assert BacktestConfig().confidence_threshold == 0.0

    def test_swept_value_is_carried_on_config(self):
        # An explicit override (as the WF grid supplies) is honored by the config.
        cfg = BacktestConfig(confidence_threshold=0.6)
        assert cfg.confidence_threshold == 0.6
        # ...and the gate would filter a sub-0.6 idea under that run.
        assert BacktestEngine._below_confidence_gate(0.58, cfg.confidence_threshold) is True
