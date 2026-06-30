"""
Per-strategy notional cap (deep-audit low #47).

The per-strategy risk BUDGET (get_max_risk_pct) shapes size by stop distance, but
the notional CAP was a single global max_position_pct, washing the per-strategy
budget back out. When PER_STRATEGY_NOTIONAL_CAP_ENABLED is on, the size cap and
the POSITION_SIZE check use StrategyTypeConfig.get_max_position_pct(strategy_type)
so a scalp rides a tighter ceiling than a position trade. Default OFF →
byte-identical (both use max_position_pct).
"""

import os
import tempfile

import pytest

from bot.config import CONFIG
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeIdea


def _risk(equity=10_000.0):
    state = os.path.join(tempfile.mkdtemp(prefix="rc-cap-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=equity), state_file=state)


def _scalp_idea():
    # Very tight stop → uncapped fixed-fractional size is enormous, so the
    # notional cap is the binding constraint in both modes.
    return TradeIdea(id="TI-scalp", asset="BTC/USDT", direction=Direction.LONG,
                     entry_price=100.0, stop_loss=99.9, take_profit=100.5,
                     confidence=0.8, reasoning="t", strategy_type="scalp")


class TestConfigMethod:
    def test_per_strategy_values(self):
        st = CONFIG.strategy_types
        assert st.get_max_position_pct("scalp", 13.0) == 8.0
        assert st.get_max_position_pct("position", 13.0) == 15.0

    def test_unknown_strategy_falls_back_to_default(self):
        assert CONFIG.strategy_types.get_max_position_pct("nonsense", 13.0) == 13.0

    def test_flag_defaults_off(self):
        assert CONFIG.risk.per_strategy_notional_cap_enabled is False


class TestCapBinds:
    def test_scalp_capped_tighter_when_enabled(self):
        idea = _scalp_idea()
        # OFF (default): global 13% cap.
        off = _risk().evaluate(idea, atr=2.0, max_position_usd=None)
        assert off.position_size_usd == pytest.approx(10_000 * 0.13, rel=1e-3)

        # ON: scalp's per-strategy 8% cap (flip the frozen flag, then restore).
        object.__setattr__(CONFIG.risk, "per_strategy_notional_cap_enabled", True)
        try:
            on = _risk().evaluate(idea, atr=2.0, max_position_usd=None)
        finally:
            object.__setattr__(CONFIG.risk, "per_strategy_notional_cap_enabled", False)
        assert on.position_size_usd == pytest.approx(10_000 * 0.08, rel=1e-3)
        assert on.position_size_usd < off.position_size_usd
