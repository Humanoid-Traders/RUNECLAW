"""
Close-reason attribution fixes (2026-07-14 parity report).

/parity showed 89 realized trades (net −$73.46) booked "CLOSED (unknown)"
— 39% of the book unattributed — plus one structural mislabel: v3
combined TPSL orders share ONE id for both legs, and the tp-first id
match labeled every combined stop fill "TP HIT (exchange)".

Fixes under test: a venue closeType classifier covering Bitget's full
vocabulary (burst/adl/track/delivery), price-inference fallbacks at every
site that previously booked a flat unknown, the combined-TPSL leg
decision by fill price, metals scan default flipped off on parity
evidence, and the scalp time-stop label no longer rendering "0h max".
"""

import inspect
from datetime import UTC, datetime

from bot.core.live_executor import LiveExecutor, LivePosition


def _executor(tmp_path):
    return LiveExecutor(state_dir=str(tmp_path))


def _pos(direction="LONG", entry=100.0, sl=95.0, tp=110.0, trailing=None):
    return LivePosition(
        trade_id="TI-attr", symbol="BTC/USDT:USDT", direction=direction,
        entry_price=entry, quantity=1.0, cost_usd=10.0,
        stop_loss=sl, take_profit=tp, leverage=10, status="open",
        opened_at=datetime.now(UTC), trailing_state=trailing)


# ── closeType classifier ─────────────────────────────────────────────

class TestCloseTypeClassifier:
    def test_tp_and_sl_variants(self, tmp_path):
        ex = _executor(tmp_path)
        p = _pos()
        assert ex._close_reason_from_type(p, "profit_take", 110.0) == "TP HIT (exchange)"
        r = ex._close_reason_from_type(p, "stop_loss", 95.0, pnl=-5.0)
        assert r is not None and "SL HIT" in r and "(exchange)" in r

    def test_bitget_specific_vocabulary(self, tmp_path):
        ex = _executor(tmp_path)
        p = _pos()
        assert ex._close_reason_from_type(p, "burst_long", 90.0) == "LIQUIDATED"
        assert "ADL" in ex._close_reason_from_type(p, "adl_close", 100.0)
        assert "DELIVERY" in ex._close_reason_from_type(p, "delivery_long", 100.0)
        # track = Bitget trailing/track order → stop-family label
        r = ex._close_reason_from_type(p, "track_close", 95.0, pnl=-5.0)
        assert r is not None and "SL" in r

    def test_bare_close_returns_none_for_inference(self, tmp_path):
        ex = _executor(tmp_path)
        p = _pos()
        assert ex._close_reason_from_type(p, "close_long", 102.0) is None
        assert ex._close_reason_from_type(p, "", 102.0) is None

    def test_trailing_stop_in_profit_never_reads_as_loss(self, tmp_path):
        # A ratcheted stop above entry fills at a GAIN — the label must be
        # the trailing variant, not a bare "SL HIT".
        ex = _executor(tmp_path)
        p = _pos(entry=100.0, sl=104.0, tp=110.0,
                 trailing={"trailing_active": True})
        r = ex._close_reason_from_type(p, "stop_loss", 104.0, pnl=4.0)
        assert r is not None and "TRAILING" in r.upper()


# ── inference fallback replaces flat unknowns ─────────────────────────

class TestInferenceFallbacks:
    def test_history_path_falls_back_to_inference(self):
        src = inspect.getsource(LiveExecutor._fetch_bitget_close_data)
        assert "_close_reason_from_type" in src
        # every remaining unknown must have gone through inference first
        assert '"reason": "CLOSED (unknown)"' not in src

    def test_combined_tpsl_decides_by_price_not_id(self):
        src = inspect.getsource(LiveExecutor._fetch_bitget_close_data)
        assert "pos.sl_order_id == pos.tp_order_id" in src
        # the combined branch must run BEFORE the tp-id match that caused
        # the every-combined-fill-is-TP mislabel
        assert (src.index("pos.sl_order_id == pos.tp_order_id")
                < src.index('matched_order == pos.tp_order_id'))

    def test_inference_labels_a_stop_fill(self, tmp_path):
        ex = _executor(tmp_path)
        assert "SL HIT" in ex._infer_close_reason(_pos(), 94.9)
        assert "TP HIT" in ex._infer_close_reason(_pos(), 110.1)
        assert ex._infer_close_reason(_pos(), 102.0) == "CLOSED (unknown)"


# ── config + render fixes ─────────────────────────────────────────────

def test_metals_scan_default_off():
    import os
    import pytest
    if os.environ.get("SCAN_CLASS_METALS"):
        pytest.skip("env override present")
    from bot.config import CONFIG
    assert CONFIG.scan_class_metals is False


def test_time_stop_label_keeps_subhour_thresholds():
    src = inspect.getsource(LiveExecutor)
    # scalp's 0.5h threshold rendered "0h max" under :.0f
    assert "{close_threshold:g}h max" in src
    assert "{close_threshold:.0f}h max" not in src
