"""
LiveExecutor._bitget_tick_safety_net must correctly combine pricePlace and
priceEndStep, not treat whichever one it finds first as either "the tick
itself" or "a flat decimal-place count".

Real incident: a live BTC/USDT short was rejected by Bitget with error 45115
("price should be a multiple of 0.1"). Investigation found this safety net
(then inline in execute()) used a heuristic that only produced a correct tick
for symbols where pricePlace == priceEndStep numerically (like BTC, both "1")
-- for any symbol where they differ, it silently mis-rounded.
"""

from bot.core.live_executor import LiveExecutor


def _market(price_place=None, price_end_step=None, price_tick=None):
    info = {}
    if price_place is not None:
        info["pricePlace"] = price_place
    if price_end_step is not None:
        info["priceEndStep"] = price_end_step
    if price_tick is not None:
        info["priceTick"] = price_tick
    return {"info": info}


class TestBitgetTickSafetyNet:
    def test_btc_like_pair_equal_values(self):
        # pricePlace=1, priceEndStep=1 -> tick = 1 * 10^-1 = 0.1
        market = _market(price_place="1", price_end_step="1")
        result = LiveExecutor._bitget_tick_safety_net(market, 59009.04)
        assert result == 59009.0

    def test_differing_pair_values_use_combined_tick_not_flat_decimals(self):
        # pricePlace=4, priceEndStep=5 -> tick = 5 * 10^-4 = 0.0005.
        # The old heuristic picked priceEndStep="5" first, saw it was >= 1,
        # and treated it as "5 decimal places" -- a no-op for a price that
        # already has <=4 decimals, leaving invalid sub-tick values untouched.
        market = _market(price_place="4", price_end_step="5")
        result = LiveExecutor._bitget_tick_safety_net(market, 0.12345678)
        # Nearest multiple of 0.0005 to 0.12345678 is 0.1235.
        assert abs(result - 0.1235) < 1e-9

    def test_no_market_returns_price_unchanged(self):
        assert LiveExecutor._bitget_tick_safety_net(None, 59009.04) == 59009.04

    def test_no_usable_tick_info_returns_price_unchanged(self):
        market = _market()
        assert LiveExecutor._bitget_tick_safety_net(market, 59009.04) == 59009.04

    def test_standalone_price_tick_field_used_when_pair_absent(self):
        market = _market(price_tick="0.25")
        result = LiveExecutor._bitget_tick_safety_net(market, 100.30)
        # Nearest multiple of 0.25 to 100.30 is 100.25.
        assert abs(result - 100.25) < 1e-9

    def test_malformed_fields_fall_back_to_unchanged_price(self):
        market = _market(price_place="not-a-number", price_end_step="1")
        assert LiveExecutor._bitget_tick_safety_net(market, 59009.04) == 59009.04
