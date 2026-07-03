"""Order prices must land on Bitget's real tick grid, even when ccxt mis-parses
the pricePlace/priceEndStep pair (the 45115 "price should be a multiple of X"
rejection). _round_price_to_market applies the authoritative tick snap on top of
ccxt's price_to_precision.
"""

from bot.core.live_executor import LiveExecutor


class _FakeExchange:
    """price_to_precision deliberately returns a FINER value than the real tick,
    reproducing ccxt's Bitget misparse; market() carries the true precision pair."""

    def __init__(self, price_place, price_end_step, ccxt_returns):
        self._pp = price_place
        self._pes = price_end_step
        self._ret = ccxt_returns

    def price_to_precision(self, symbol, price):
        return self._ret

    def market(self, symbol):
        return {"info": {"pricePlace": self._pp, "priceEndStep": self._pes}}


def test_btc_snaps_to_tenth_when_ccxt_returns_hundredths():
    # BTC: pricePlace=1, priceEndStep=1 → tick 0.1. ccxt wrongly gives 2 dp.
    ex = _FakeExchange("1", "1", "61750.37")
    out = LiveExecutor._round_price_to_market(ex, "BTC/USDT:USDT", 61750.37)
    assert float(out) == 61750.4               # snapped to the 0.1 grid
    assert (float(out) / 0.1) == round(float(out) / 0.1)  # exact multiple


def test_fractional_tick_pair_snaps_to_0_0005():
    # pricePlace=4, priceEndStep=5 → tick 0.0005; ccxt gives plain 4dp.
    ex = _FakeExchange("4", "5", "0.3303")
    out = LiveExecutor._round_price_to_market(ex, "X/USDT:USDT", 0.33031)
    val = float(out)
    assert abs(round(val / 0.0005) * 0.0005 - val) < 1e-9  # on the 0.0005 grid


def test_falls_back_to_ccxt_when_no_tick_info():
    class _NoInfo(_FakeExchange):
        def market(self, symbol):
            return {"info": {}}
    ex = _NoInfo("", "", "123.45")
    out = LiveExecutor._round_price_to_market(ex, "Y/USDT:USDT", 123.456)
    assert out == "123.45"  # unchanged ccxt value when no tick to snap to


def test_price_to_precision_failure_returns_none_for_caller_fallback():
    # When ccxt can't price (market data unavailable) the contract is None so
    # the caller uses its own heuristic — unchanged by the tick-snap addition.
    class _Boom(_FakeExchange):
        def price_to_precision(self, symbol, price):
            raise RuntimeError("ccxt precision unavailable")
    ex = _Boom("1", "1", None)
    assert LiveExecutor._round_price_to_market(ex, "BTC/USDT:USDT", 61750.37) is None
