"""OI history warm-start: restarts must not blind the OI classifiers.

The OI-trend and OI-price-divergence classifiers key off in-memory rolling
history that used to reset on every restart, re-warming over N live scans
(minutes apart each). warm_oi_history() reloads the tail of the recorded
order-flow snapshots (the same OF_RECORD_SNAPSHOTS dataset the backtest
replays), age-filtered so stale OI never seeds a fresh process.
"""

import json
from datetime import datetime, timedelta

from bot.compat import UTC
from bot.core.order_flow import OrderFlowAnalyzer


def _snapshot_line(symbol, oi, price, age_hours):
    ts = (datetime.now(UTC) - timedelta(hours=age_hours)).isoformat()
    return json.dumps({"symbol": symbol, "ts": ts,
                       "signal": {"symbol": symbol, "open_interest_usd": oi,
                                  "mid_price": price}})


def _write(tmp_path, lines):
    p = tmp_path / "of_snapshots.jsonl"
    p.write_text("\n".join(lines) + "\n")
    return str(p)


class TestWarmOIHistory:
    def test_recent_snapshots_warm_history(self, tmp_path):
        path = _write(tmp_path, [
            _snapshot_line("BTC/USDT:USDT", 1e9 + i * 1e6, 50000 + i, 2 - i * 0.1)
            for i in range(5)])
        an = OrderFlowAnalyzer()
        assert an.warm_oi_history(path) == 1
        hist = an._oi_val_history["BTC/USDT:USDT"]
        assert len(hist) == 5
        assert hist[-1] == 1e9 + 4e6
        assert an._oi_history["BTC/USDT:USDT"] == 1e9 + 4e6
        assert len(an._price_snap_history["BTC/USDT:USDT"]) == 5

    def test_stale_snapshots_are_ignored(self, tmp_path):
        path = _write(tmp_path, [_snapshot_line("ETH/USDT:USDT", 5e8, 3000, 30)])
        an = OrderFlowAnalyzer()
        assert an.warm_oi_history(path, max_age_hours=24.0) == 0
        assert "ETH/USDT:USDT" not in an._oi_val_history

    def test_garbage_lines_and_missing_oi_skipped(self, tmp_path):
        path = _write(tmp_path, [
            "not json at all",
            json.dumps({"symbol": "X/USDT:USDT", "ts": "bad-ts", "signal": {}}),
            _snapshot_line("SOL/USDT:USDT", 0, 100, 1),      # zero OI: skip
            _snapshot_line("SOL/USDT:USDT", 7e8, 150, 1),
        ])
        an = OrderFlowAnalyzer()
        assert an.warm_oi_history(path) == 1
        assert list(an._oi_val_history["SOL/USDT:USDT"]) == [7e8]

    def test_missing_file_is_cold_start(self, tmp_path):
        an = OrderFlowAnalyzer()
        assert an.warm_oi_history(str(tmp_path / "nope.jsonl")) == 0

    def test_multiple_symbols_counted(self, tmp_path):
        path = _write(tmp_path, [
            _snapshot_line("BTC/USDT:USDT", 1e9, 50000, 1),
            _snapshot_line("ETH/USDT:USDT", 4e8, 3000, 1),
        ])
        an = OrderFlowAnalyzer()
        assert an.warm_oi_history(path) == 2
