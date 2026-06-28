"""
Per-user PAPER (sim) opt-in mode. A user who opts in has their confirmed trades
SIMULATED into their paper portfolio instead of sent to the exchange — risk-free
practice on a live bot. Default OFF and per-user, so live users are unaffected.

The safety-critical guarantee under test: the paper-fill path NEVER calls the
live executor / exchange, and creates a real monitored paper position instead.
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from bot.compat import UTC
from bot.config import CONFIG
from bot.utils.models import Direction, RiskCheck, RiskVerdict, TradeIdea


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _idea():
    return TradeIdea(
        asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
        stop_loss=98.0, take_profit=106.0, confidence=0.8,
        reasoning="paper", source="scan", timestamp=datetime.now(UTC),
    )


def _recheck(size=100.0):
    return RiskCheck(trade_id="T1", verdict=RiskVerdict.APPROVED,
                     position_size_usd=size, position_pct=1.0)


# ── config / user-store plumbing ──────────────────────────────────────────────

class TestConfigAndStore:
    def test_feature_default_off(self):
        assert CONFIG.paper_sim_opt_in_enabled is False

    def test_user_store_opt_in_roundtrip(self, tmp_path):
        from bot.utils.user_store import UserStore
        store = UserStore(path=str(tmp_path / "users.json"))
        store.register(12345, name="t")
        assert store.sim_opt_in(12345) is False          # default OFF
        assert store.set_sim_opt_in(12345, True) is True
        assert store.sim_opt_in(12345) is True
        assert store.set_sim_opt_in(12345, False) is True
        assert store.sim_opt_in(12345) is False

    def test_opt_in_unknown_user_is_false(self, tmp_path):
        from bot.utils.user_store import UserStore
        store = UserStore(path=str(tmp_path / "users.json"))
        assert store.sim_opt_in(99999) is False
        assert store.set_sim_opt_in(99999, True) is False  # can't set unknown


# ── the safety-critical paper-fill path ───────────────────────────────────────

class TestSimulatePaperFill:
    def _engine(self):
        from bot.core.engine import RuneClawEngine
        eng = RuneClawEngine()
        # A live executor that MUST NOT be touched by the paper path.
        eng.live_executor = MagicMock()
        eng.live_executor.execute = AsyncMock(
            side_effect=AssertionError("paper fill must NEVER call live execute"))
        return eng

    def test_paper_fill_opens_position_without_exchange(self):
        eng = self._engine()
        eng._pending_ideas["T1"] = _idea()
        msg = _run(eng._simulate_paper_fill(_idea(), _recheck(100.0), "user-1", "T1"))
        # Never reached the exchange:
        eng.live_executor.execute.assert_not_called()
        # A real paper position now exists in the user's portfolio:
        pf = eng.user_portfolios.get("user-1")
        assert len(pf.open_positions) == 1
        assert pf.open_positions[0].is_paper is True
        # User-facing message is clearly labelled paper:
        assert "[PAPER]" in msg and "no real order" in msg
        # The pending idea was consumed.
        assert "T1" not in eng._pending_ideas

    def test_paper_fill_isolated_per_user(self):
        eng = self._engine()
        _run(eng._simulate_paper_fill(_idea(), _recheck(100.0), "user-A", "T1"))
        # Another user's portfolio is untouched.
        assert len(eng.user_portfolios.get("user-B").open_positions) == 0

    def test_paper_fill_insufficient_balance_is_graceful(self):
        eng = self._engine()
        # Size far exceeds the default paper balance → open_position raises;
        # the path must return a labelled failure, not crash or hit the exchange.
        msg = _run(eng._simulate_paper_fill(_idea(), _recheck(10_000_000.0), "user-1", "T1"))
        eng.live_executor.execute.assert_not_called()
        assert "[PAPER]" in msg
        assert len(eng.user_portfolios.get("user-1").open_positions) == 0
