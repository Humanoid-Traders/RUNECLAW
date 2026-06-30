"""
Proactive alerts apply the WS staleness bound (deep-audit low #45).

The proactive SL-proximity / time-stop monitors read prices from the WS feed but
called get_prices() with no freshness bound, so a silently-stalled feed could
drive an alert off a frozen price. Both sites now pass
max_age_sec=CONFIG.execution.ws_max_tick_age_sec — the same bound the SL/TP
monitor uses (0 disables it).
"""

import inspect

import bot.core.proactive_monitor as pm
from bot.core.ws_feed import BitgetWSFeed


class TestWiring:
    def test_both_sites_pass_max_age(self):
        src = inspect.getsource(pm)
        # Every WS price read in this module is now staleness-bounded.
        assert "get_prices()" not in src
        assert src.count("max_age_sec=getattr(CONFIG.execution, \"ws_max_tick_age_sec\", 0)") >= 2


class TestBoundActuallyFilters:
    def test_stale_tick_excluded_when_bounded(self):
        # Sanity that the bound get_prices honours actually drops a stale tick.
        from datetime import datetime, timedelta, timezone
        feed = BitgetWSFeed()
        from bot.core.ws_feed import PriceTick
        now = datetime.now(timezone.utc)
        feed._ticks = {
            "FRESH/USDT": PriceTick(symbol="FRESH/USDT", last=100.0, bid=99.0,
                                    ask=101.0, volume_24h=0, change_pct_24h=0,
                                    timestamp=now),
            "STALE/USDT": PriceTick(symbol="STALE/USDT", last=50.0, bid=49.0,
                                    ask=51.0, volume_24h=0, change_pct_24h=0,
                                    timestamp=now - timedelta(seconds=120)),
        }
        bounded = feed.get_prices(max_age_sec=15)
        assert "FRESH/USDT" in bounded and "STALE/USDT" not in bounded
        # 0/None → no filtering (both present).
        assert set(feed.get_prices(max_age_sec=0)) == {"FRESH/USDT", "STALE/USDT"}
