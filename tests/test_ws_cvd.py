"""Tier 3: WebSocket trade-tape CVD.

The advertised "cumulative volume delta" was a sum of overlapping/gappy
200-trade REST windows — double-counting on fast polls, missing everything
between slow polls. The WS trade channel now feeds a per-symbol TRUE
cumulative delta (deduped by trade id, per-minute buckets), and order flow
uses it — with trend/divergence computed on the real cumulative series —
whenever fresh, falling back to the REST approximation otherwise.
"""
from __future__ import annotations

import time

from bot.config import CONFIG
from bot.core.order_flow import OrderFlowAnalyzer
from bot.core.ws_feed import BitgetWSFeed


def _trade(tid, price, size, side, ts_ms=None):
    return {"tradeId": tid, "price": price, "size": size, "side": side,
            "ts": ts_ms if ts_ms is not None else time.time() * 1000}


class TestTapeAccumulation:
    def test_signed_accumulation(self):
        feed = BitgetWSFeed()
        feed._process_trades("BTC/USDT", [
            _trade("1", 100.0, 2.0, "buy"),     # +200
            _trade("2", 100.0, 1.0, "sell"),    # -100
        ])
        cvd = feed.get_cvd("BTC/USDT")
        assert cvd is not None
        assert cvd["cum_delta_usd"] == 100.0
        assert cvd["trades"] == 2

    def test_duplicate_trade_ids_ignored(self):
        # A reconnect replays the recent tape — replays must not double-count.
        feed = BitgetWSFeed()
        batch = [_trade("7", 100.0, 1.0, "buy")]
        feed._process_trades("BTC/USDT", batch)
        feed._process_trades("BTC/USDT", batch)
        cvd = feed.get_cvd("BTC/USDT")
        assert cvd["cum_delta_usd"] == 100.0 and cvd["trades"] == 1

    def test_malformed_items_skipped(self):
        feed = BitgetWSFeed()
        feed._process_trades("BTC/USDT", [
            {"tradeId": "x", "price": -1, "size": 1, "side": "buy"},
            {"tradeId": "y", "price": 100, "size": 1, "side": "hold"},
            _trade("z", 100.0, 1.0, "sell"),
        ])
        cvd = feed.get_cvd("BTC/USDT")
        assert cvd["trades"] == 1 and cvd["cum_delta_usd"] == -100.0

    def test_minute_buckets_track_cumulative(self):
        feed = BitgetWSFeed()
        t0 = 1_700_000_000_000
        feed._process_trades("BTC/USDT", [
            _trade("1", 100.0, 1.0, "buy", t0),            # min 0: cum 100
            _trade("2", 100.0, 1.0, "buy", t0 + 10_000),   # min 0: cum 200
            _trade("3", 100.0, 1.0, "sell", t0 + 61_000),  # min 1: cum 100
        ])
        cvd = feed.get_cvd("BTC/USDT")
        assert cvd["series"] == [200.0, 100.0]

    def test_stale_returns_none(self):
        feed = BitgetWSFeed()
        feed._process_trades("BTC/USDT", [_trade("1", 100.0, 1.0, "buy")])
        feed._cvd["BTC/USDT"]["last_update"] = time.time() - 999
        assert feed.get_cvd("BTC/USDT") is None


class TestOrderFlowUsesTape:
    def test_series_divergence_on_cumulative(self):
        # Price higher high, cumulative delta lower high → bearish.
        cum = [100, 200, 300, 280, 260, 250]
        px = [10, 11, 12, 12.5, 13, 13.5]
        assert OrderFlowAnalyzer._series_divergence(cum, px) == "bearish_div"
        # Price lower low, cum higher low → bullish.
        cum2 = [-300, -280, -200, -150, -120, -100]
        px2 = [13, 12, 11, 10.5, 10, 9.5]
        assert OrderFlowAnalyzer._series_divergence(cum2, px2) == "bullish_div"
        assert OrderFlowAnalyzer._series_divergence([1, 2], [1, 2]) == "none"

    def test_flag_default_on(self):
        assert CONFIG.execution.ws_cvd_enabled is True

    def test_fallback_when_no_feed(self):
        an = OrderFlowAnalyzer()
        assert getattr(an, "_ws_feed", "missing") is None  # explicit None init
