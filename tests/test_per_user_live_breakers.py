"""
C1 (CRITICAL): a per-user live close must feed the OWNING user's risk engine.

Before the fix, _on_live_position_closed always called self.risk (the operator
engine), so a per-user live trader's realized losses were recorded against the
operator's account-level breakers: their OWN daily-loss / drawdown / streak
gates never tripped, and the operator's breakers absorbed every user's losses
(the exact cross-account coupling per-user isolation promised to prevent).

The fix threads the executor's user_id into _on_live_position_closed and routes
the result through risk_for(user_id). With PER_USER_LIVE_ENABLED off, risk_for
always returns the operator engine — byte-identical to before.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from bot.core.engine import RuneClawEngine


def _pos(pnl, symbol="BTC/USDT", direction="LONG", close_reason="take_profit"):
    return SimpleNamespace(
        pnl_usd=pnl, symbol=symbol, direction=direction,
        close_reason=close_reason, trade_id="T1",
    )


def _engine():
    """Minimal engine: the close handler's collaborators are all fail-open, so
    only the risk registry needs to be real for the routing assertion."""
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng._invalidate_live_balance_cache = lambda: None
    eng.learning = Mock()
    eng._outcome_regime = lambda s: ""
    eng._auto_refit = Mock()
    eng._symbol_cooldowns = {}
    eng._symbol_cooldown_seconds = 60
    eng._symbol_loss_streaks = {}
    eng._sync_live_state_to_website = lambda: None

    operator = Mock(name="operator_risk")
    users: dict = {}

    def _risk_for(uid=""):
        if not uid or uid in ("auto", ""):
            return operator
        return users.setdefault(uid, Mock(name=f"risk_{uid}"))

    eng.risk = operator
    eng.risk_for = _risk_for
    return eng, operator, users


# ── routing: the core C1 assertion ─────────────────────────────────────

class TestCloseRouting:
    def test_operator_close_hits_operator_engine(self):
        eng, operator, users = _engine()
        eng._on_live_position_closed(_pos(-5.0))          # no user_id → operator
        operator.record_live_trade_result.assert_called_once_with(-5.0)
        assert users == {}                                # no per-user engine built

    def test_per_user_close_hits_that_users_engine(self):
        eng, operator, users = _engine()
        eng._on_live_position_closed(_pos(-3.0), "alice")
        assert "alice" in users
        users["alice"].record_live_trade_result.assert_called_once_with(-3.0)
        # The operator engine must NOT absorb the user's loss.
        operator.record_live_trade_result.assert_not_called()

    def test_two_users_isolated(self):
        eng, operator, users = _engine()
        eng._on_live_position_closed(_pos(-2.0), "alice")
        eng._on_live_position_closed(_pos(-7.0), "bob")
        users["alice"].record_live_trade_result.assert_called_once_with(-2.0)
        users["bob"].record_live_trade_result.assert_called_once_with(-7.0)
        operator.record_live_trade_result.assert_not_called()

    def test_missing_pnl_records_nothing(self):
        eng, operator, users = _engine()
        p = _pos(None)
        eng._on_live_position_closed(p, "alice")
        # risk_for may be consulted, but no result is recorded for a None pnl.
        if "alice" in users:
            users["alice"].record_live_trade_result.assert_not_called()


# ── wiring: the per-user executor callback carries the right id ─────────

class TestExecutorCallbackBinding:
    def test_per_user_executor_callback_routes_to_owner(self):
        # Guards the late-binding closure trap: the executor built for "bob"
        # must, when it closes a position, feed bob's engine — not whatever the
        # loop variable happened to end on.
        eng, operator, users = _engine()
        eng.ws_feed = None
        eng.slippage = None
        eng._user_executors = {}

        creds = {"api_key": "K" * 16, "api_secret": "S" * 16, "passphrase": "pp"}

        class _Store:
            def get(self, uid):
                return creds if str(uid) == "bob" else None

        cfg = patch("bot.core.engine.CONFIG")
        m = cfg.start()
        m.per_user_live_enabled = True
        store = patch("bot.core.exchange_credentials.get_credential_store",
                      return_value=_Store())
        store.start()
        try:
            ex = eng._executor_for("bob")            # per-user executor for bob
            # Fire the executor's own close callback → must hit bob's engine.
            ex.on_position_closed(_pos(-4.0))
            assert "bob" in users
            users["bob"].record_live_trade_result.assert_called_once_with(-4.0)
            operator.record_live_trade_result.assert_not_called()
        finally:
            store.stop()
            cfg.stop()
