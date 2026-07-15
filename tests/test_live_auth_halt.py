"""
Live-auth safe-halt (recurring naked-position incident class).

When the trading account cannot authenticate with the venue (missing
passphrase, Bitget 40006 / 40012), it cannot place the protective stop — so it
must NOT open new positions it could not protect. The engine tracks per-account
auth health; the pre-execute gate refuses NEW live entries on an account marked
down (open positions stay monitored). The boot preflight sets operator health;
a per-user sweep probes every linked account so a revoked key surfaces at boot,
not at first stop-placement (live-readiness audit C3).
"""

from unittest.mock import AsyncMock, Mock, patch

from bot.core.engine import RuneClawEngine


# ── per-account auth-health state machine ──────────────────────────────

class TestAuthStatus:
    def _eng(self):
        e = RuneClawEngine.__new__(RuneClawEngine)
        e._live_auth_ok = {}
        e._live_auth_detail = {}
        return e

    def test_unknown_defaults_healthy(self):
        e = self._eng()
        assert e.live_auth_healthy() is True          # operator
        assert e.live_auth_healthy("alice") is True    # any user

    def test_mark_down_blocks_then_recovers(self):
        e = self._eng()
        e.set_live_auth_status(False, "40012 apikey/password is incorrect")
        assert e.live_auth_healthy() is False
        assert "40012" in e._live_auth_detail[""]
        e.set_live_auth_status(True)
        assert e.live_auth_healthy() is True

    def test_per_account_isolation(self):
        e = self._eng()
        e.set_live_auth_status(False, "bad key", user_id="alice")
        assert e.live_auth_healthy("alice") is False
        assert e.live_auth_healthy() is True           # operator unaffected
        assert e.live_auth_healthy("bob") is True       # other user unaffected


# ── per-user boot sweep (C3) ───────────────────────────────────────────

class _Exec:
    def __init__(self, bal):
        self._bal = bal

    async def fetch_balance(self):
        return self._bal


def _cfg(per_user=True, live=True):
    p = patch("bot.main.CONFIG")
    m = p.start()
    m.simulation_mode = not live
    m.live_trading_enabled = live
    m.per_user_live_enabled = per_user
    m.telegram.chat_id = "999"
    m.telegram.admin_ids = ""
    return p


class TestPerUserSweep:
    async def test_marks_failing_accounts_down_and_alerts(self):
        from bot import main as m

        operator = object()
        marks = []
        eng = Mock()
        eng.live_executor = operator
        execs = {
            "alice": _Exec({"total": 100.0}),                       # healthy
            "bob": _Exec({"error": "40012 apikey/password is incorrect"}),
        }
        eng._executor_for = lambda uid: execs.get(uid, operator)
        eng.set_live_auth_status = (
            lambda ok, detail="", user_id="": marks.append((user_id, ok, detail)))

        store = Mock()
        store.user_ids = lambda: ["alice", "bob"]
        bot = Mock()
        bot.send_message = AsyncMock()

        p = _cfg(per_user=True)
        st = patch("bot.core.exchange_credentials.get_credential_store",
                   return_value=store)
        st.start()
        try:
            await m._per_user_credential_preflight(eng, bot)
        finally:
            st.stop()
            p.stop()

        by_user = {u: (ok, det) for u, ok, det in marks}
        assert by_user["alice"][0] is True
        assert by_user["bob"][0] is False
        assert "40012" in by_user["bob"][1]
        bot.send_message.assert_awaited()               # operator alerted re: bob

    async def test_noop_when_per_user_off(self):
        from bot import main as m

        eng = Mock()
        called = []
        eng.set_live_auth_status = lambda *a, **k: called.append(1)
        bot = Mock()
        bot.send_message = AsyncMock()

        p = _cfg(per_user=False)
        try:
            await m._per_user_credential_preflight(eng, bot)
        finally:
            p.stop()

        assert called == []
        bot.send_message.assert_not_awaited()

    async def test_skips_accounts_that_fell_back_to_operator(self):
        from bot import main as m

        operator = object()
        marks = []
        eng = Mock()
        eng.live_executor = operator
        # "carol" has no usable keys → _executor_for returns the operator exec.
        eng._executor_for = lambda uid: operator
        eng.set_live_auth_status = (
            lambda ok, detail="", user_id="": marks.append((user_id, ok, detail)))

        store = Mock()
        store.user_ids = lambda: ["carol"]
        bot = Mock()
        bot.send_message = AsyncMock()

        p = _cfg(per_user=True)
        st = patch("bot.core.exchange_credentials.get_credential_store",
                   return_value=store)
        st.start()
        try:
            await m._per_user_credential_preflight(eng, bot)
        finally:
            st.stop()
            p.stop()

        assert marks == []                              # nothing probed/marked
        bot.send_message.assert_not_awaited()
