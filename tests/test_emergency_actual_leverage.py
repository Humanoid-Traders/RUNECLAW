"""
Emergency post-crash position records ACTUAL leverage (deep-audit medium).

When create_order succeeds but post-processing crashes, execute() reconstructs
the live position locally. It used to hard-code CONFIG.exchange.default_leverage
for that record, but the order was sized with the dynamically-adjusted
leverage_mult (which only ever REDUCES from the default). Since pos.leverage
later drives cost_usd = notional / leverage recomputation, recording the
(higher) default under-counted the position's margin/exposure — feeding the
exposure-based risk caps too little, so they believed there was more room.

The fix records the in-scope leverage_mult via _emergency_leverage and seeds a
leverage_mult sentinel before the order try-block.
"""

import inspect

from bot.core.live_executor import LiveExecutor

_lev = LiveExecutor._emergency_leverage


class TestEmergencyLeverage:
    def test_futures_records_actual_leverage(self):
        assert _lev(3, is_futures=True) == 3

    def test_futures_reduced_leverage_preserved(self):
        # Dynamic leverage halved 10x → 5x; the record must show 5, not 10.
        assert _lev(5, is_futures=True) == 5

    def test_spot_is_one(self):
        assert _lev(10, is_futures=False) == 1

    def test_float_leverage_is_coerced(self):
        assert _lev(4.0, is_futures=True) == 4
        assert _lev(2.9, is_futures=True) == 2  # int() truncates

    def test_floor_is_one(self):
        assert _lev(0, is_futures=True) == 1

    def test_bad_value_falls_back_to_one(self):
        assert _lev(None, is_futures=True) == 1
        assert _lev("x", is_futures=True) == 1


class TestWiring:
    def test_emergency_block_uses_actual_leverage_not_default(self):
        src = inspect.getsource(LiveExecutor.execute)
        # The emergency position must record the actual leverage via the helper…
        assert "leverage=self._emergency_leverage(leverage_mult, is_futures)" in src
        # …and a leverage_mult sentinel is seeded before the order try-block so the
        # record is always defined.
        assert "leverage_mult = CONFIG.exchange.default_leverage" in src

    def test_no_default_leverage_in_emergency_record(self):
        # Guard against regressing to the hard-coded default in the record line.
        src = inspect.getsource(LiveExecutor.execute)
        assert "leverage=CONFIG.exchange.default_leverage if is_futures else 1" not in src
