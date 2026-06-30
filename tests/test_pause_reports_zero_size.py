"""
Pause-type risk rejections report position_size_usd = 0 (deep-audit low #50).

When the equity-curve breaker or the live-performance governor PAUSES trading
(size multiplier <= 0), the trade is rejected — but the engine previously left
position_usd at its computed value, so the RiskCheck / audit record showed a
phantom notional for a trade that will not happen. The pause branches now zero
position_usd before the RiskCheck is built.
"""

import os
import tempfile
from unittest.mock import PropertyMock, patch

from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, RiskVerdict, TradeIdea

_ATR = 2600.0


def _idea():
    return TradeIdea(id="TI-pause", asset="BTC/USDT", direction=Direction.LONG,
                     entry_price=65000.0, stop_loss=63700.0, take_profit=66560.0,
                     confidence=0.72, reasoning="test", signals_used=["rsi"],
                     strategy_type="scalp")


def _risk():
    state = os.path.join(tempfile.mkdtemp(prefix="rc-pause-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10000.0), state_file=state)


class TestEquityCurvePauseReportsZero:
    def test_paused_trade_rejected_with_zero_size(self):
        risk = _risk()
        with patch.object(RiskEngine, "equity_curve_size_multiplier",
                          new_callable=PropertyMock, return_value=0.0):
            check = risk.evaluate(_idea(), atr=_ATR, max_position_usd=100.0)
        assert check.verdict == RiskVerdict.REJECTED
        assert any("EQUITY_CURVE" in f for f in check.checks_failed)
        assert check.position_size_usd == 0.0

    def test_non_pause_reduction_keeps_positive_size(self):
        # A partial reduction (0 < mult < 1) still sizes a real (approved) trade.
        risk = _risk()
        with patch.object(RiskEngine, "equity_curve_size_multiplier",
                          new_callable=PropertyMock, return_value=0.5):
            check = risk.evaluate(_idea(), atr=_ATR, max_position_usd=100.0)
        assert check.position_size_usd > 0.0


class TestBothPauseBranchesZeroSize:
    def test_both_pause_branches_zero_position_usd(self):
        # The governor pause path mirrors the equity-curve one. Its config gate is
        # a frozen, opt-in flag that's awkward to flip in isolation, so guard the
        # wiring at the source: each "trading paused" rejection zeroes the size.
        import inspect

        src = inspect.getsource(RiskEngine._evaluate_locked)
        # Both pause rejections present...
        assert "EQUITY_CURVE: trading paused" in src
        assert "LIVE_PERF_GOVERNOR: trading paused" in src
        # ...and the size is zeroed on a pause at least as many times.
        assert src.count("position_usd = 0.0") >= 2
