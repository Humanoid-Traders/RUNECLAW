"""
SL/TP side-sanity check.

A wrong-side (inverted) stop/target would place a stop that fails to protect or a
target that fills the instant it's posted. _sltp_side_error rejects such pairs
(LONG needs SL<TP, SHORT needs SL>TP, both positive); _place_sl_tp /
_place_sl_tp_v3 then refuse to place either order and leave the position to the
unprotected-position alert/escalation. Fires only on genuinely-invalid input.
"""

from unittest.mock import AsyncMock, MagicMock

from bot.core.live_executor import LiveExecutor
from bot.utils.models import Direction


_err = LiveExecutor._sltp_side_error


class TestValidator:
    def test_long_valid(self):
        assert _err(Direction.LONG, 90.0, 110.0) is None

    def test_long_inverted_rejected(self):
        assert _err(Direction.LONG, 110.0, 90.0) is not None

    def test_long_equal_rejected(self):
        assert _err(Direction.LONG, 100.0, 100.0) is not None

    def test_short_valid(self):
        assert _err(Direction.SHORT, 110.0, 90.0) is None

    def test_short_inverted_rejected(self):
        assert _err(Direction.SHORT, 90.0, 110.0) is not None

    def test_non_positive_rejected(self):
        assert _err(Direction.LONG, 0.0, 110.0) is not None
        assert _err(Direction.LONG, 90.0, 0.0) is not None
        assert _err(Direction.LONG, -1.0, 110.0) is not None


class TestPlaceSlTpEarlyReturn:
    async def test_inverted_pair_refuses_to_place(self):
        ex = MagicMock()
        ex.fetch_open_orders = AsyncMock()  # must NOT be called
        executor = LiveExecutor.__new__(LiveExecutor)
        # LONG with SL above TP → inverted → early (None, None), no exchange calls.
        out = await executor._place_sl_tp(ex, "BTC/USDT", Direction.LONG, 1.0,
                                          stop_loss=110.0, take_profit=90.0)
        assert out == (None, None)
        ex.fetch_open_orders.assert_not_called()


class TestPlaceSlTpV3EarlyReturn:
    async def test_inverted_both_positive_refused(self):
        executor = LiveExecutor.__new__(LiveExecutor)
        out = await executor._place_sl_tp_v3("BTC/USDT", Direction.SHORT, 1.0,
                                             stop_loss=90.0, take_profit=110.0)
        assert out == (None, None)
