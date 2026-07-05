"""
Per-strategy confidence floor (round 3 item 3).

The analyzer already gates idea generation on StrategyTypeConfig's per-type
confidence floors (scalp 0.65 / intraday 0.55 / swing 0.50 / position 0.45),
but the risk engine re-gates downstream on a single flat CONFIG.risk.min_confidence
-- for swing/intraday/position (floors below the flat default) that silently
rejects trades the analyzer already approved at its own tuned threshold. When
PER_STRATEGY_CONFIDENCE_FLOOR_ENABLED is on, the CONFIDENCE check uses the
per-type floor instead. Default OFF -> byte-identical (both use the flat floor).
"""

import os
import tempfile

import pytest

from bot.config import CONFIG
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeIdea


def _risk(equity=10_000.0):
    state = os.path.join(tempfile.mkdtemp(prefix="rc-conf-floor-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=equity), state_file=state)


def _idea(strategy_type, confidence):
    return TradeIdea(id=f"TI-{strategy_type}", asset="BTC/USDT", direction=Direction.LONG,
                     entry_price=100.0, stop_loss=98.0, take_profit=106.0,
                     confidence=confidence, reasoning="t", strategy_type=strategy_type)


class TestConfigMethod:
    def test_per_strategy_values(self):
        st = CONFIG.strategy_types
        assert st.get_min_confidence("scalp") == 0.65
        assert st.get_min_confidence("intraday") == 0.55
        assert st.get_min_confidence("swing") == 0.50
        assert st.get_min_confidence("position") == 0.45

    def test_unknown_strategy_falls_back_to_default(self):
        assert CONFIG.strategy_types.get_min_confidence("nonsense") == pytest.approx(0.5)

    def test_flag_defaults_off(self, monkeypatch):
        monkeypatch.delenv("PER_STRATEGY_CONFIDENCE_FLOOR_ENABLED", raising=False)
        from bot.config import RiskLimits
        assert RiskLimits().per_strategy_confidence_floor_enabled is False


class TestConfidenceGate:
    def _toggle(self, value):
        _orig = CONFIG.risk.per_strategy_confidence_floor_enabled
        object.__setattr__(CONFIG.risk, "per_strategy_confidence_floor_enabled", value)
        return _orig

    def _restore(self, orig):
        object.__setattr__(CONFIG.risk, "per_strategy_confidence_floor_enabled", orig)

    def test_swing_trade_below_global_but_above_own_floor_passes_when_enabled(self):
        # confidence 0.55: below the flat global 0.60, above swing's own 0.50 floor.
        idea = _idea("swing", 0.55)
        orig = self._toggle(False)
        try:
            off = _risk().evaluate(idea, atr=2.0, max_position_usd=None)
            assert any("CONFIDENCE" in f for f in off.checks_failed)

            self._toggle(True)
            on = _risk().evaluate(idea, atr=2.0, max_position_usd=None)
            assert not any("CONFIDENCE" in f for f in on.checks_failed)
        finally:
            self._restore(orig)

    def test_scalp_trade_above_global_but_below_own_floor_still_rejects_when_enabled(self):
        # confidence 0.62: above the flat global 0.60, below scalp's own 0.65 floor.
        idea = _idea("scalp", 0.62)
        orig = self._toggle(False)
        try:
            off = _risk().evaluate(idea, atr=2.0, max_position_usd=None)
            assert not any("CONFIDENCE" in f for f in off.checks_failed)

            self._toggle(True)
            on = _risk().evaluate(idea, atr=2.0, max_position_usd=None)
            assert any("CONFIDENCE" in f for f in on.checks_failed)
        finally:
            self._restore(orig)

    def test_flag_off_is_byte_identical_to_flat_global(self):
        idea = _idea("position", 0.48)
        orig = self._toggle(False)
        try:
            off = _risk().evaluate(idea, atr=2.0, max_position_usd=None)
            # position's own floor (0.45) would pass at 0.48, but flag is OFF
            # so the flat global (0.60) still rejects it.
            assert any("CONFIDENCE" in f for f in off.checks_failed)
        finally:
            self._restore(orig)
