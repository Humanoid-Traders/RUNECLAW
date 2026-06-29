"""
Live risk hardening (audit High-priority: portfolio risk ON for live).

When LIVE_RISK_HARDENING_ENABLED is ON *and* the bot is running live, the risk
engine applies a stricter posture for real money — without touching
paper/backtest: correlation-aware sizing forced on, covariance VaR forced on,
and a tighter live max-drawdown cap. These tests exercise the pure gating
helpers _live_hardening() and _effective_max_drawdown_pct() in isolation.
"""

from unittest.mock import patch

from bot.risk.risk_engine import RiskEngine


def _engine():
    return RiskEngine.__new__(RiskEngine)


def _cfg(hardening=False, live=False, max_dd=10.0, live_max_dd=7.0):
    p = patch("bot.risk.risk_engine.CONFIG")
    m = p.start()
    m.risk.live_risk_hardening_enabled = hardening
    m.risk.max_drawdown_pct = max_dd
    m.risk.live_max_drawdown_pct = live_max_dd
    m.is_live.return_value = live
    return p, m


class TestLiveHardeningGate:
    def test_off_when_flag_off(self):
        p, _ = _cfg(hardening=False, live=True)
        try:
            assert _engine()._live_hardening() is False
        finally:
            p.stop()

    def test_off_when_paper_even_if_flag_on(self):
        # Hardening must NEVER apply in paper mode.
        p, _ = _cfg(hardening=True, live=False)
        try:
            assert _engine()._live_hardening() is False
        finally:
            p.stop()

    def test_on_when_flag_on_and_live(self):
        p, _ = _cfg(hardening=True, live=True)
        try:
            assert _engine()._live_hardening() is True
        finally:
            p.stop()

    def test_fail_safe_returns_false_on_error(self):
        # is_live() raising must not raise out of the helper.
        p, m = _cfg(hardening=True, live=True)
        m.is_live.side_effect = RuntimeError("boom")
        try:
            assert _engine()._live_hardening() is False
        finally:
            p.stop()


class TestEffectiveMaxDrawdown:
    def test_uses_tighter_live_cap_when_hardened(self):
        p, _ = _cfg(hardening=True, live=True, max_dd=10.0, live_max_dd=7.0)
        try:
            assert _engine()._effective_max_drawdown_pct() == 7.0
        finally:
            p.stop()

    def test_uses_standard_cap_in_paper(self):
        p, _ = _cfg(hardening=True, live=False, max_dd=10.0, live_max_dd=7.0)
        try:
            assert _engine()._effective_max_drawdown_pct() == 10.0
        finally:
            p.stop()

    def test_uses_standard_cap_when_flag_off(self):
        p, _ = _cfg(hardening=False, live=True, max_dd=10.0, live_max_dd=7.0)
        try:
            assert _engine()._effective_max_drawdown_pct() == 10.0
        finally:
            p.stop()
