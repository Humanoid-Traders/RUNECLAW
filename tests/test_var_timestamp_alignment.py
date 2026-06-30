"""
Covariance VaR aligns return series by timestamp, not list position (#49).

_price_history now stores (timestamp, price) points. When a symbol misses some
monitor ticks, positional alignment would pair its stale returns against another
symbol's fresher ones. _aligned_returns instead intersects timestamps so only
genuinely-contemporaneous points are paired; the VaR path falls back to the old
positional alignment when timestamps don't overlap (e.g. legacy bare-float data).
"""

import os
import tempfile

from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine


def _risk():
    state = os.path.join(tempfile.mkdtemp(prefix="rc-var-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)


class TestStorageFormat:
    def test_update_stores_timestamped_tuples(self):
        r = _risk()
        r.update_price_history("BTC/USDT", 100.0, ts=1.0)
        r.update_price_history("BTC/USDT", 101.0, ts=2.0)
        assert r._price_history["BTC/USDT"] == [(1.0, 100.0), (2.0, 101.0)]

    def test_trims_to_100(self):
        r = _risk()
        for i in range(150):
            r.update_price_history("BTC/USDT", 100.0 + i, ts=float(i))
        assert len(r._price_history["BTC/USDT"]) == 100
        assert r._price_history["BTC/USDT"][0][0] == 50.0  # oldest kept


class TestTimestampAlignment:
    def test_aligns_on_common_timestamps_when_one_missed_a_tick(self):
        r = _risk()
        # A is present on ts 1..6; B missed ts=3.
        for t in (1, 2, 3, 4, 5, 6):
            r.update_price_history("A/USDT", 100.0 + t, ts=float(t))
        for t in (1, 2, 4, 5, 6):
            r.update_price_history("B/USDT", 200.0 + t, ts=float(t))
        aligned = r._aligned_returns(["A/USDT", "B/USDT"], min_points=3)
        assert aligned is not None
        # Common grid = {1,2,4,5,6} → 5 points → 4 returns, equal length.
        assert len(aligned["A/USDT"]) == len(aligned["B/USDT"]) == 4

    def test_returns_none_when_overlap_too_small(self):
        r = _risk()
        r.update_price_history("A/USDT", 100.0, ts=1.0)
        r.update_price_history("A/USDT", 101.0, ts=2.0)
        r.update_price_history("B/USDT", 200.0, ts=9.0)  # no shared timestamps
        assert r._aligned_returns(["A/USDT", "B/USDT"], min_points=3) is None

    def test_bare_float_history_bails_to_positional(self):
        # Legacy/direct fixtures may inject bare floats — not time-alignable.
        r = _risk()
        r._price_history = {"A/USDT": [100.0, 101.0, 102.0], "B/USDT": [200.0, 201.0, 202.0]}
        assert r._aligned_returns(["A/USDT", "B/USDT"], min_points=2) is None
