"""
Per-user risk isolation.

Each live user now gets their OWN stateful safety breakers — consecutive-loss
streak, circuit breaker, daily-loss, drawdown — evaluated against their OWN
account, instead of one global RiskEngine whose halt one user could trip for
everyone. Gated behind PER_USER_LIVE_ENABLED (default OFF): with the flag off,
``risk_for()`` ALWAYS returns the shared operator engine, so the operator path
is byte-identical. Market-wide context (regime, order-flow, price history) stays
shared so the per-user split can only separate account breakers, never loosen a
market gate.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bot.core.engine import RuneClawEngine
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.risk.multi_portfolio import MultiUserPortfolio


# ── Engine harness ──────────────────────────────────────────────────
# Build only the slice of RuneClawEngine that risk_for()/_route_user_trade_close
# touch — a real shared RiskEngine + a real MultiUserPortfolio — without the full
# (heavy, network-touching) constructor.

@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.macro_calendar = None
    eng.macro_provider = None
    eng._user_store = None
    eng.risk = RiskEngine(
        PortfolioTracker(initial_balance=10_000.0),
        state_file=str(tmp_path / "data" / "risk_state_op.json"),
    )
    eng._user_risk = {}
    eng.user_portfolios = MultiUserPortfolio(default_balance=10_000.0)
    eng.user_portfolios._on_trade_close = eng.risk.record_trade_result
    eng.user_portfolios._on_trade_close_user = eng._route_user_trade_close
    # Default: nobody is an operator, so test users are treated as regular.
    eng._is_operator_user = lambda uid: False
    return eng


def _cfg(per_user: bool):
    """Patch engine-module CONFIG with just the flag risk_for() reads."""
    p = patch("bot.core.engine.CONFIG", SimpleNamespace(per_user_live_enabled=per_user))
    p.start()
    return p


def _bust(engine_obj, n: int):
    """Record n losing trades to trip the 5-loss streak breaker."""
    for _ in range(n):
        engine_obj.record_trade_result(-100.0)


# ── risk_for() routing ──────────────────────────────────────────────

class TestRiskForRouting:
    def test_flag_off_returns_shared(self, engine):
        p = _cfg(per_user=False)
        try:
            assert engine.risk_for("12345") is engine.risk
            assert engine.risk_for("auto") is engine.risk
            assert engine.risk_for("") is engine.risk
        finally:
            p.stop()

    def test_auto_and_empty_return_shared_even_when_on(self, engine):
        p = _cfg(per_user=True)
        try:
            assert engine.risk_for("auto") is engine.risk
            assert engine.risk_for("") is engine.risk
        finally:
            p.stop()

    def test_operator_returns_shared(self, engine):
        engine._is_operator_user = lambda uid: True
        p = _cfg(per_user=True)
        try:
            assert engine.risk_for("999") is engine.risk
        finally:
            p.stop()

    def test_regular_user_gets_own_cached_engine(self, engine):
        p = _cfg(per_user=True)
        try:
            e1 = engine.risk_for("alice")
            assert e1 is not engine.risk
            assert isinstance(e1, RiskEngine)
            # Cached — same object on the second call.
            assert engine.risk_for("alice") is e1
            # Different user → different engine.
            assert engine.risk_for("bob") is not e1
        finally:
            p.stop()

    def test_own_engine_bound_to_own_portfolio(self, engine):
        p = _cfg(per_user=True)
        try:
            e1 = engine.risk_for("alice")
            assert e1._portfolio is engine.user_portfolios.get("alice")
        finally:
            p.stop()


# ── Isolation of breaker state ──────────────────────────────────────

class TestBreakerIsolation:
    def test_two_users_independent_circuit_breakers(self, engine):
        p = _cfg(per_user=True)
        try:
            a = engine.risk_for("alice")
            b = engine.risk_for("bob")
            _bust(a, 5)  # trip alice's streak breaker
            assert a.circuit_breaker_active is True
            assert b.circuit_breaker_active is False
            assert engine.risk.circuit_breaker_active is False
            assert b._consecutive_losses == 0
            assert engine.risk._consecutive_losses == 0
        finally:
            p.stop()

    def test_route_user_trade_close_isolates_streak(self, engine):
        p = _cfg(per_user=True)
        try:
            for _ in range(5):
                engine._route_user_trade_close("alice", -100.0)
            assert engine.risk_for("alice").circuit_breaker_active is True
            # Shared + other users untouched.
            assert engine.risk.circuit_breaker_active is False
            assert engine.risk_for("bob").circuit_breaker_active is False
        finally:
            p.stop()

    def test_flag_off_close_feeds_shared(self, engine):
        p = _cfg(per_user=False)
        try:
            for _ in range(5):
                engine._route_user_trade_close("alice", -100.0)
            # With per-user OFF, every close lands on the shared engine.
            assert engine.risk.circuit_breaker_active is True
        finally:
            p.stop()

    def test_win_does_not_trip_other_user(self, engine):
        p = _cfg(per_user=True)
        try:
            a = engine.risk_for("alice")
            _bust(a, 4)
            engine.risk_for("bob").record_trade_result(50.0)  # bob wins
            assert a._consecutive_losses == 4
            assert engine.risk_for("bob")._consecutive_losses == 0
        finally:
            p.stop()


# ── Market context stays shared ─────────────────────────────────────

class TestMarketContextShared:
    def test_market_context_mirrored_onto_user_engine(self, engine):
        engine.risk._current_regime = "STRONG_TREND_UP"
        engine.risk._current_vol_state = "HIGH"
        sentinel = object()
        engine.risk._last_of_signal = sentinel
        p = _cfg(per_user=True)
        try:
            a = engine.risk_for("alice")
            assert a._current_regime == "STRONG_TREND_UP"
            assert a._current_vol_state == "HIGH"
            assert a._last_of_signal is sentinel
            # Price history is shared by reference (one global series).
            assert a._price_history is engine.risk._price_history
        finally:
            p.stop()

    def test_context_resyncs_on_each_call(self, engine):
        p = _cfg(per_user=True)
        try:
            a = engine.risk_for("alice")
            engine.risk._current_regime = "CHOPPY"
            a2 = engine.risk_for("alice")  # same engine, re-synced
            assert a2 is a
            assert a._current_regime == "CHOPPY"
        finally:
            p.stop()


# ── MultiUserPortfolio close-callback routing ───────────────────────

class TestCloseCallbackRouting:
    def test_user_aware_callback_receives_user_id(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        seen = []
        m = MultiUserPortfolio(default_balance=1_000.0)
        m._on_trade_close_user = lambda uid, pnl: seen.append((uid, pnl))
        tracker = m.get("alice")
        tracker._on_trade_close(-42.0)
        assert seen == [("alice", -42.0)]

    def test_falls_back_to_plain_callback(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        plain = []
        m = MultiUserPortfolio(default_balance=1_000.0)
        m._on_trade_close = lambda pnl: plain.append(pnl)
        # No user-aware callback set → plain (pnl)->None behaviour preserved.
        tracker = m.get("bob")
        tracker._on_trade_close(-7.0)
        assert plain == [-7.0]

    def test_callback_resolved_live_after_tracker_created(self, tmp_path, monkeypatch):
        # Restored/early-created trackers must route to a callback wired AFTER
        # construction — the closure resolves the attribute live, not at build.
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        m = MultiUserPortfolio(default_balance=1_000.0)
        tracker = m.get("carol")          # created BEFORE any callback is wired
        seen = []
        m._on_trade_close_user = lambda uid, pnl: seen.append((uid, pnl))
        tracker._on_trade_close(-9.0)
        assert seen == [("carol", -9.0)]

    def test_no_callbacks_is_safe_noop(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        m = MultiUserPortfolio(default_balance=1_000.0)
        tracker = m.get("dave")
        # Neither callback set — must not raise.
        tracker._on_trade_close(-1.0)
