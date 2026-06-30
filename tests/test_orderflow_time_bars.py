"""
Taker 3-bar gate time-awareness (deep-audit HIGH: order-flow time-bars).

The taker 3-bar confirmation gate counts the last 3 *polled* bars as if they
were uniform time bars. Polls are irregular, so 3 bars fired in a burst confirm
a "3-bar trend" spanning only seconds, and a stale/gappy window confirms on data
that no longer reflects live flow. With OF_TIME_BARS_ENABLED, the gate also
requires the last-3 wall-clock span to fall within [min, max] seconds.

This is tightening-only (it can only reject a confirmation), default OFF, and
the legacy bare-float storage path stays byte-identical when disabled. The
backward-compat extractors handle both float and (ts, ratio) tuple entries.
"""

from collections import deque

from bot.core.order_flow import OrderFlowAnalyzer, OrderFlowConfig

_ratio = OrderFlowAnalyzer._bar_ratio
_ts = OrderFlowAnalyzer._bar_ts
_span_ok = OrderFlowAnalyzer._taker_span_ok


def _cfg(**kw):
    return OrderFlowConfig(**kw)


def _analyzer(**kw):
    return OrderFlowAnalyzer(config=_cfg(**kw))


def _seed(an, symbol, entries):
    an._taker_bar_ratios[symbol] = deque(entries, maxlen=10)


class TestExtractors:
    def test_bar_ratio_from_float(self):
        assert _ratio(1.5) == 1.5

    def test_bar_ratio_from_tuple(self):
        assert _ratio((1000.0, 1.5)) == 1.5

    def test_bar_ts_from_float_is_none(self):
        assert _ts(0.9) is None

    def test_bar_ts_from_tuple(self):
        assert _ts((1234.0, 0.9)) == 1234.0


class TestSpanCheck:
    def test_valid_span_ok(self):
        entries = [(0.0, 1.2), (30.0, 1.2), (60.0, 1.2)]
        ok, span = _span_ok(entries, 20.0, 300.0)
        assert ok is True and span == 60.0

    def test_burst_too_short(self):
        entries = [(0.0, 1.2), (2.0, 1.2), (5.0, 1.2)]
        ok, span = _span_ok(entries, 20.0, 300.0)
        assert ok is False and span == 5.0

    def test_stale_too_long(self):
        entries = [(0.0, 1.2), (200.0, 1.2), (600.0, 1.2)]
        ok, span = _span_ok(entries, 20.0, 300.0)
        assert ok is False and span == 600.0

    def test_missing_timestamp_fails_open(self):
        # Mixed legacy data (a bare float among the last 3) → can't verify → ok.
        entries = [(0.0, 1.2), 1.2, (60.0, 1.2)]
        ok, span = _span_ok(entries, 20.0, 300.0)
        assert ok is True and span is None

    def test_boundaries_inclusive(self):
        assert _span_ok([(0.0, 1), (10.0, 1), (20.0, 1)], 20.0, 300.0)[0] is True
        assert _span_ok([(0.0, 1), (150.0, 1), (300.0, 1)], 20.0, 300.0)[0] is True


class TestGateDisabledIsIdentity:
    def test_disabled_confirms_burst_long(self):
        # OFF: storage is bare floats, no span check — a burst still confirms.
        an = _analyzer(time_bars_enabled=False)
        _seed(an, "BTC/USDT", [1.3, 1.4, 1.5])
        res = an.check_taker_3bar_gate("BTC/USDT", "LONG")
        assert res["passed"] is True
        assert res["ratios"] == [1.3, 1.4, 1.5]

    def test_disabled_rejects_misaligned(self):
        an = _analyzer(time_bars_enabled=False)
        _seed(an, "BTC/USDT", [1.3, 0.9, 1.5])
        res = an.check_taker_3bar_gate("BTC/USDT", "LONG")
        assert res["passed"] is False

    def test_insufficient_data(self):
        an = _analyzer(time_bars_enabled=False)
        _seed(an, "BTC/USDT", [1.3, 1.4])
        res = an.check_taker_3bar_gate("BTC/USDT", "LONG")
        assert res["passed"] is False
        assert "insufficient" in res["reason"]


class TestGateEnabled:
    def test_valid_span_confirms_long(self):
        an = _analyzer(time_bars_enabled=True,
                       taker_bar_min_span_sec=20.0, taker_bar_max_span_sec=300.0)
        _seed(an, "BTC/USDT", [(0.0, 1.3), (30.0, 1.4), (90.0, 1.5)])
        res = an.check_taker_3bar_gate("BTC/USDT", "LONG")
        assert res["passed"] is True
        assert res["ratios"] == [1.3, 1.4, 1.5]

    def test_burst_rejected_long(self):
        an = _analyzer(time_bars_enabled=True,
                       taker_bar_min_span_sec=20.0, taker_bar_max_span_sec=300.0)
        _seed(an, "BTC/USDT", [(0.0, 1.3), (2.0, 1.4), (5.0, 1.5)])
        res = an.check_taker_3bar_gate("BTC/USDT", "LONG")
        assert res["passed"] is False
        assert "burst" in res["reason"]

    def test_stale_rejected_short(self):
        an = _analyzer(time_bars_enabled=True,
                       taker_bar_min_span_sec=20.0, taker_bar_max_span_sec=300.0)
        _seed(an, "BTC/USDT", [(0.0, 0.7), (200.0, 0.6), (600.0, 0.5)])
        res = an.check_taker_3bar_gate("BTC/USDT", "SHORT")
        assert res["passed"] is False
        assert "stale/gappy" in res["reason"]

    def test_misaligned_still_rejected_before_span(self):
        # A non-confirming streak is rejected on alignment regardless of span.
        an = _analyzer(time_bars_enabled=True)
        _seed(an, "BTC/USDT", [(0.0, 1.3), (30.0, 0.9), (90.0, 1.5)])
        res = an.check_taker_3bar_gate("BTC/USDT", "LONG")
        assert res["passed"] is False
        assert "not aligned" in res["reason"]

    def test_enabled_legacy_floats_fail_open(self):
        # If bare floats are present after enabling (no timestamps yet), the span
        # check can't run, so a valid alignment still confirms (fail-open).
        an = _analyzer(time_bars_enabled=True)
        _seed(an, "BTC/USDT", [1.3, 1.4, 1.5])
        res = an.check_taker_3bar_gate("BTC/USDT", "LONG")
        assert res["passed"] is True


class TestDefaultsOff:
    def test_flag_defaults_off(self):
        assert OrderFlowConfig().time_bars_enabled is False
