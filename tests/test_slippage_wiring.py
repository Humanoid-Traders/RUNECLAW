"""
Realized-slippage accounting wiring (audit execution-quality item).

The slippage GUARD (flatten on excessive adverse fill) and the SlippageTracker
(record / stats / /slippage report) both already existed — but the executor's
self._slippage_tracker was never assigned from the engine's tracker, so
record() was a silent no-op and /slippage always showed "no data". This wires
the engine's tracker into the operator + per-user executors and verifies the
data path the report consumes.
"""

import inspect

from bot.core.engine import RuneClawEngine
from bot.core.slippage import SlippageTracker


class TestEngineWiresTracker:
    def test_operator_executor_wired_in_init(self):
        src = inspect.getsource(RuneClawEngine.__init__)
        assert "self.live_executor._slippage_tracker = self.slippage" in src

    def test_per_user_executor_wired(self):
        src = inspect.getsource(RuneClawEngine._executor_for)
        assert "_slippage_tracker" in src and "slippage" in src


class TestTrackerDataPath:
    def test_adverse_long_fill_recorded_as_negative(self):
        t = SlippageTracker(state_file="/tmp/rc_slip_test_a.json")
        t._records.clear()
        # LONG filled HIGHER than expected → adverse → signed slippage negative.
        t.record(symbol="BTC/USDT", expected_price=100.0, actual_price=100.5,
                 direction="LONG", order_type="market", size_usd=1000.0)
        stats = t.get_stats("BTC/USDT")
        assert stats is not None
        assert stats.total_trades == 1
        assert stats.adverse_count == 1
        assert stats.favorable_count == 0
        assert stats.total_slippage_usd > 0  # $ lost to adverse slippage

    def test_favorable_short_fill_recorded_as_positive(self):
        t = SlippageTracker(state_file="/tmp/rc_slip_test_b.json")
        t._records.clear()
        # SHORT filled HIGHER than expected → favorable.
        t.record(symbol="ETH/USDT", expected_price=100.0, actual_price=100.5,
                 direction="SHORT", order_type="market", size_usd=500.0)
        stats = t.get_stats("ETH/USDT")
        assert stats.favorable_count == 1
        assert stats.adverse_count == 0

    def test_all_stats_feeds_report(self):
        t = SlippageTracker(state_file="/tmp/rc_slip_test_c.json")
        t._records.clear()
        t.record(symbol="BTC/USDT", expected_price=100.0, actual_price=100.3,
                 direction="LONG", size_usd=1000.0)
        all_stats = t.get_all_stats()  # what /slippage renders
        assert "BTC/USDT" in all_stats
        assert all_stats["BTC/USDT"].total_trades == 1
