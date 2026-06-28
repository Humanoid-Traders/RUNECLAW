"""
_infer_close_reason must not assert "MANUAL CLOSE" for closes it can't actually
attribute to the user. A close on the exchange by an unrecognized mechanism
(ADL, partial-ladder reduceOnly, an unclassified Bitget closeType, or a genuine
user close) all look the same to the bot — it reports "CLOSED (unknown)".
"""

from bot.core.live_executor import LiveExecutor, LivePosition


def _pos(direction="LONG", sl=98.0, tp=110.0):
    return LivePosition(
        trade_id="T", symbol="BTC/USDT:USDT", direction=direction,
        entry_price=100.0, quantity=1.0, cost_usd=100.0,
        stop_loss=sl, take_profit=tp, atr_at_entry=2.0, status="open",
    )


def _exe():
    return LiveExecutor()


class TestInferCloseReason:
    def test_tp_hit_still_inferred(self):
        exe = _exe()
        assert exe._infer_close_reason(_pos("LONG"), 110.0) == "TP HIT (inferred)"

    def test_sl_hit_still_inferred(self):
        exe = _exe()
        assert exe._infer_close_reason(_pos("LONG"), 98.0) == "SL HIT (inferred)"

    def test_between_sl_tp_is_unknown_not_manual(self):
        exe = _exe()
        # Exit mid-range, near neither level → unknown, not a manual assertion.
        reason = exe._infer_close_reason(_pos("LONG"), 104.0)
        assert reason == "CLOSED (unknown)"
        assert "MANUAL" not in reason

    def test_missing_levels_is_unknown(self):
        exe = _exe()
        reason = exe._infer_close_reason(_pos("LONG", sl=0.0, tp=0.0), 104.0)
        assert reason == "CLOSED (unknown)"
