"""
WebSocket price staleness guard.

The live SL/TP monitoring loop prefers sub-second WS prices, but is_connected()
reflects socket state, not data freshness — a silently-stalled-but-connected feed
would serve a stale 'last' price to stop logic. get_prices(max_age_sec=...) now
excludes ticks older than the threshold, so the loop falls back to REST (and the
exchange-side stop remains the backstop). Default behaviour (no max_age) is
unchanged.
"""

import time
from datetime import timedelta

from bot.compat import UTC
from datetime import datetime
from bot.core.ws_feed import BitgetWSFeed, PriceTick


def _tick(sym, age_sec):
    return PriceTick(
        symbol=sym, last=100.0, bid=99.9, ask=100.1,
        volume_24h=1.0, change_pct_24h=0.0,
        timestamp=datetime.now(UTC) - timedelta(seconds=age_sec),
    )


def _feed(ticks):
    f = BitgetWSFeed.__new__(BitgetWSFeed)
    f._ticks = dict(ticks)
    f._last_msg_ts = 0.0
    return f


class TestGetPricesStaleness:
    def test_no_max_age_returns_all(self):
        f = _feed({"BTC/USDT": _tick("BTC/USDT", 1), "ETH/USDT": _tick("ETH/USDT", 999)})
        assert set(f.get_prices()) == {"BTC/USDT", "ETH/USDT"}

    def test_zero_max_age_disables_guard(self):
        f = _feed({"BTC/USDT": _tick("BTC/USDT", 999)})
        assert set(f.get_prices(max_age_sec=0)) == {"BTC/USDT"}

    def test_filters_stale_ticks(self):
        f = _feed({
            "BTC/USDT": _tick("BTC/USDT", 2),     # fresh
            "ETH/USDT": _tick("ETH/USDT", 60),    # stale
        })
        out = f.get_prices(max_age_sec=15)
        assert set(out) == {"BTC/USDT"}
        assert out["BTC/USDT"] == 100.0

    def test_all_stale_returns_empty(self):
        f = _feed({"BTC/USDT": _tick("BTC/USDT", 60), "ETH/USDT": _tick("ETH/USDT", 90)})
        assert f.get_prices(max_age_sec=15) == {}

    def test_unreadable_timestamp_is_excluded_under_filter(self):
        class _BadTick:
            last = 100.0
            timestamp = "not-a-datetime"
        f = _feed({"BTC/USDT": _BadTick()})
        # Under a freshness filter, an unreadable timestamp is treated as stale.
        assert f.get_prices(max_age_sec=15) == {}
        # Without a filter it's still returned (original behaviour).
        assert set(f.get_prices()) == {"BTC/USDT"}


class TestSecondsSinceLastMsg:
    def test_none_when_never_received(self):
        f = _feed({})
        assert f.seconds_since_last_msg() is None

    def test_age_when_set(self):
        f = _feed({})
        f._last_msg_ts = time.time() - 5.0
        age = f.seconds_since_last_msg()
        assert age is not None and 4.0 <= age <= 7.0
