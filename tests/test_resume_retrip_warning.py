"""Honest /resume: the resume card must not claim a clean 'Circuit Breaker
CLEAR' when the daily-loss/drawdown condition still holds and the breaker will
re-trip on the next evaluation (the 'BOT RESUMED' vs 'Paused' mismatch)."""
from __future__ import annotations

import os
import tempfile

from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.warroom.warroom_bot import render_resume


def _engine(balance: float = 10_000.0) -> RiskEngine:
    state = os.path.join(tempfile.mkdtemp(prefix="rc-retrip-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=balance), state_file=state)


class TestPendingRetripReason:
    def test_clean_state_returns_none(self):
        assert _engine().pending_retrip_reason() is None

    def test_daily_loss_breach_reports_retrip(self):
        eng = _engine()
        # Force a realized daily loss beyond the limit on the tracker (the
        # tracker keys realized PnL by date string).
        from datetime import datetime
        from bot.compat import UTC
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        eng._portfolio._daily_pnl[today] = -5_000.0  # -50% of 10k equity
        reason = eng.pending_retrip_reason()
        assert reason is not None
        assert "daily loss" in reason
        assert "re-trips" in reason

    def test_error_is_fail_open(self):
        eng = _engine()
        eng._portfolio = None  # snapshot() will raise
        assert eng.pending_retrip_reason() is None


class TestRenderResume:
    def test_clean_resume_unchanged(self):
        text = render_resume()["text"]
        assert "BOT RESUMED" in text
        assert "CLEAR" in text
        assert "Heads up" not in text

    def test_warning_rendered(self):
        text = render_resume(retrip_warning="daily loss 12.0% still >= 5.0% limit")["text"]
        assert "Heads up" in text
        assert "daily loss 12.0%" in text
        assert "CLEAR*" in text
        assert "Paused again" in text
