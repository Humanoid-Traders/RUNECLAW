"""
C2 (HIGH): the LIVE free-margin size clamp must use the EXECUTING account's
balance, not the shared operator's.

Bug: confirm_trade clamped size_usd against ``self._live_balance_cache`` — always
the operator account. Under per-user live trading a user's order was therefore
sized against the OPERATOR's free margin: too loose when the operator holds more
(→ InsufficientFunds on the user's smaller account) or too tight when less.

Fix: clamp against ``get_user_live_equity(user_id)``, which resolves to the
operator balance for the operator / non-per-user paths (byte-identical) and to
the user's OWN linked account otherwise. The clamp lives inline in the ~large
confirm_trade method; this guards the wiring by source inspection (matching the
repo's style for deep methods) and pins the resolver's account routing.
"""

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

from bot.core.engine import RuneClawEngine


def _clamp_block() -> str:
    src = inspect.getsource(RuneClawEngine._confirm_trade_inner)
    i = src.index("LIVE FIX: Cap position size")
    j = src.index("Manual margin override", i)
    return src[i:j]


class TestClampUsesExecutingAccount:
    def test_clamp_reads_executing_account_balance(self):
        block = _clamp_block()
        # Resolves the balance for THIS user's executing account…
        assert "live_bal = await self.get_user_live_equity(user_id)" in block
        # …and no longer clamps against the shared operator balance cache (the
        # old `live_bal = self._live_balance_cache` line is gone).
        assert "live_bal = self._live_balance_cache" not in block

    def test_clamp_records_user_id_in_audit(self):
        block = _clamp_block()
        assert '"user_id": user_id' in block


class TestUserLiveEquityRouting:
    """get_user_live_equity: operator/default → shared balance; per-user → own."""

    def _engine(self):
        eng = RuneClawEngine.__new__(RuneClawEngine)
        eng.live_executor = MagicMock()
        eng._user_live_balance_cache = {}
        eng._user_live_balance_cache_ts = {}
        eng._LIVE_BALANCE_TTL = 30.0
        return eng

    async def test_operator_path_uses_shared_balance(self):
        eng = self._engine()
        eng.get_live_equity = AsyncMock(return_value={"free": 1000.0, "total": 1000.0})
        eng._executor_for = MagicMock(return_value=eng.live_executor)
        cfg = MagicMock(); cfg.is_live.return_value = True
        cfg.per_user_live_enabled = False
        with patch("bot.core.engine.CONFIG", cfg):
            bal = await eng.get_user_live_equity("")
        assert bal["free"] == 1000.0
        eng.get_live_equity.assert_awaited()  # routed to the shared operator balance

    async def test_per_user_path_uses_own_account(self):
        eng = self._engine()
        # A distinct per-user executor whose balance differs from the operator's.
        user_ex = MagicMock()
        user_ex.fetch_balance = AsyncMock(return_value={"free": 7.07, "total": 7.07})
        eng._executor_for = MagicMock(return_value=user_ex)
        eng._is_operator_user = MagicMock(return_value=False)
        eng.get_live_equity = AsyncMock(return_value={"free": 1000.0, "total": 1000.0})
        cfg = MagicMock(); cfg.is_live.return_value = True
        cfg.per_user_live_enabled = True
        with patch("bot.core.engine.CONFIG", cfg):
            bal = await eng.get_user_live_equity("alice")
        assert bal["free"] == 7.07, "per-user clamp must use the user's OWN balance"
        eng.get_live_equity.assert_not_awaited()
