"""
Calibrated-confidence auto-trade gate.

The 0.85 admin auto-trade fired on raw blended confidence (LLM+voters), not a
measured win rate. When AUTO_CONFIRM_USE_CALIBRATED is ON and a fitted
calibrator exists, the threshold is tested against min(raw, calibrated) — so a
real-money auto-trade requires BOTH the raw blend AND the measured win-rate to
clear the bar. Strictly a tightening: never loosens, no-op until data diverges.
"""

from types import SimpleNamespace
from unittest.mock import patch

from bot.core.engine import RuneClawEngine


def _engine(calibrator):
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.analyzer = SimpleNamespace(_get_calibrator=lambda: calibrator)
    return eng


def _idea(conf):
    return SimpleNamespace(confidence=conf)


def _cal(ready=True, mapping=None):
    return SimpleNamespace(
        is_ready=lambda: ready,
        calibrate=lambda c: (mapping(c) if mapping else c),
    )


def _cfg(use_calibrated):
    p = patch("bot.core.engine.CONFIG")
    m = p.start()
    m.auto_confirm_use_calibrated = use_calibrated
    return p


class TestGateValue:
    def test_flag_off_returns_raw(self):
        p = _cfg(use_calibrated=False)
        try:
            assert _engine(_cal())._auto_confirm_gate_value(_idea(0.90)) == 0.90
        finally:
            p.stop()

    def test_no_calibrator_returns_raw(self):
        p = _cfg(use_calibrated=True)
        try:
            assert _engine(False)._auto_confirm_gate_value(_idea(0.90)) == 0.90
        finally:
            p.stop()

    def test_not_ready_returns_raw(self):
        p = _cfg(use_calibrated=True)
        try:
            assert _engine(_cal(ready=False))._auto_confirm_gate_value(_idea(0.90)) == 0.90
        finally:
            p.stop()

    def test_overconfident_tightens(self):
        # Calibrator maps 0.90 raw → 0.70 realized → gate uses 0.70 (tighter).
        p = _cfg(use_calibrated=True)
        try:
            cal = _cal(mapping=lambda c: 0.70)
            assert _engine(cal)._auto_confirm_gate_value(_idea(0.90)) == 0.70
        finally:
            p.stop()

    def test_underconfident_does_not_loosen(self):
        # Calibrator maps 0.80 raw → 0.95 → min keeps raw 0.80 (never loosens).
        p = _cfg(use_calibrated=True)
        try:
            cal = _cal(mapping=lambda c: 0.95)
            assert _engine(cal)._auto_confirm_gate_value(_idea(0.80)) == 0.80
        finally:
            p.stop()

    def test_calibrator_error_fails_open_to_raw(self):
        p = _cfg(use_calibrated=True)
        try:
            def _boom(c):
                raise RuntimeError("cal boom")
            cal = SimpleNamespace(is_ready=lambda: True, calibrate=_boom)
            assert _engine(cal)._auto_confirm_gate_value(_idea(0.88)) == 0.88
        finally:
            p.stop()


class TestThresholdEffect:
    def test_overconfident_idea_drops_below_threshold(self):
        # 0.90 raw clears 0.85, but calibrated 0.78 does not → gated out.
        p = _cfg(use_calibrated=True)
        try:
            cal = _cal(mapping=lambda c: 0.78)
            gated = _engine(cal)._auto_confirm_gate_value(_idea(0.90))
            assert gated < 0.85          # would NOT auto-trade
        finally:
            p.stop()
