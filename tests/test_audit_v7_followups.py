"""
Regression tests for the V7 audit follow-ups:

  1. Manual-margin double-leverage: /trade ... margin N must commit N of MARGIN
     (notional N*leverage), not N*leverage (which the executor then multiplied
     again into N*leverage**2 notional).

  2. F-3 notional/margin reconciliation: the executor's notional ceiling and the
     explicit margin/notional audit make the leverage relationship safe + visible.
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.compat import UTC
from bot.utils.models import TradeIdea, Direction
from datetime import datetime


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_engine():
    from bot.core.engine import RuneClawEngine
    engine = RuneClawEngine()
    engine.risk._state_file = "/dev/null"
    engine.risk._circuit_open = False
    engine.risk._consecutive_losses = 0
    engine.risk._last_loss_time = None
    engine._cooldown_until = 0.0
    if engine.macro_provider is not None:
        engine.macro_provider._calendar_stale = False
        engine.macro_provider._calendar_blind = False
    engine.scanner.scan = AsyncMock(return_value=[])
    engine.scanner._get_exchange = AsyncMock()
    engine.scanner._get_futures_exchange = AsyncMock()
    return engine


def _pending_manual_idea(engine):
    idea = TradeIdea(
        id="TI-MANUAL1", asset="BTC/USDT", direction=Direction.LONG,
        entry_price=65000, stop_loss=63050, take_profit=68120,
        confidence=1.0, reasoning="manual", signals_used=["manual"],
        source="manual", timestamp=datetime.now(UTC), order_type="market",
    )
    engine._pending_ideas[idea.id] = idea
    engine._pending_atr[idea.id] = 500.0
    return idea


class TestManualMarginDoubleLeverage:
    def test_manual_margin_passes_margin_not_notional(self):
        """confirm_trade must hand the LiveExecutor the MARGIN the user named, so
        notional = margin * leverage — not margin * leverage**2."""
        from bot.config import CONFIG
        from types import SimpleNamespace

        engine = _make_engine()
        idea = _pending_manual_idea(engine)
        engine.portfolio.balance = 50000.0
        engine.portfolio._peak_equity = 50000.0
        engine._live_balance_cache = {}
        # Operator sets a manual margin of $250.
        engine._manual_margin_override = {idea.id: 250.0}

        mock_exchange = AsyncMock()
        mock_exchange.fetch_ticker = AsyncMock(return_value={"last": idea.entry_price})
        engine.scanner._get_exchange = AsyncMock(return_value=mock_exchange)
        engine.scanner._get_futures_exchange = AsyncMock(return_value=mock_exchange)

        engine.live_executor._positions = {}
        engine.live_executor.execute = AsyncMock(return_value="LIVE LONG BTC/USDT opened")
        engine.compliance.issue_approval_token = MagicMock(return_value="tok")
        engine.compliance.authorize = MagicMock(return_value=SimpleNamespace(
            granted=True, reasons=[], locks_failed=[], locks_passed=["L1"]))
        engine._live_execution_vetoed_by_simulation = lambda: False

        with patch.object(type(CONFIG), "is_live", return_value=True), \
             patch("bot.core.engine.get_exchange_position_count", new=AsyncMock(return_value=0)), \
             patch("bot.core.engine.invalidate_position_count_cache"):
            # Human confirmation so Lock 5 mints (F-8).
            _run(engine.confirm_trade(idea.id, user_id="123456"))

        engine.live_executor.execute.assert_awaited_once()
        size_arg = engine.live_executor.execute.await_args.args[1]
        # The executor receives the MARGIN ($250), not margin*leverage ($1250).
        assert size_arg == pytest.approx(250.0), (
            f"expected margin 250 passed to executor, got {size_arg} "
            f"(double-leverage if == 250*leverage)")
        assert size_arg != pytest.approx(250.0 * CONFIG.exchange.default_leverage)

    def test_engine_source_no_longer_premultiplies(self):
        import inspect
        from bot.core.engine import RuneClawEngine
        src = inspect.getsource(RuneClawEngine.confirm_trade) + inspect.getsource(RuneClawEngine._confirm_trade_inner)
        # The pre-multiply form must be gone.
        assert "size_usd = manual_margin * leverage" not in src
        assert "size_usd = manual_margin" in src


class TestNotionalVisibility:
    def test_risk_audit_logs_leverage_and_notional(self):
        """Every evaluation logs the margin->notional relationship explicitly."""
        import logging
        import os, tempfile
        from bot.risk.risk_engine import RiskEngine
        from bot.risk.portfolio import PortfolioTracker
        from bot.config import CONFIG

        names = ("runeclaw.risk",)
        saved = {n: logging.getLogger(n).propagate for n in names}
        for n in names:
            logging.getLogger(n).propagate = True
        try:
            import pytest as _pytest
            eng = RiskEngine(PortfolioTracker(initial_balance=10_000.0),
                             state_file=os.path.join(tempfile.mkdtemp(), "s.json"))
            idea = TradeIdea(
                asset="BTC/USDT", direction=Direction.LONG,
                entry_price=100.0, stop_loss=98.0, take_profit=104.0,
                confidence=0.8, reasoning="x", source="t",
            )
            import logging as _l
            records = []
            handler = _l.Handler()
            handler.emit = lambda r: records.append(r)
            logging.getLogger("runeclaw.risk").addHandler(handler)
            try:
                eng.evaluate(idea, atr=1.0)
            finally:
                logging.getLogger("runeclaw.risk").removeHandler(handler)
            risk_checks = [r for r in records if getattr(r, "action", "") == "risk_check"]
            assert risk_checks, "no risk_check audit emitted"
            data = getattr(risk_checks[-1], "data", {})
            assert "leverage" in data
            assert "approx_notional_usd" in data
            assert data["approx_notional_usd"] == _pytest.approx(
                data["position_size_usd"] * CONFIG.exchange.default_leverage, rel=1e-6)
        finally:
            for n, v in saved.items():
                logging.getLogger(n).propagate = v

    def test_units_documented_in_module(self):
        import bot.risk.risk_engine as re_mod
        assert "notional = margin * leverage" in (re_mod.__doc__ or "")
