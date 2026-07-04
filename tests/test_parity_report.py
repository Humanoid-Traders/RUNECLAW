"""The parity report reads live realized trades and reports the same lens as the
frozen benchmark (PF / win / net + fee parity + per-family breakdown), so live
can be compared to the +0.31% / PF 1.14 backtest and any fills/fees gap shows up.
Pure, read-only; fail-soft on missing/malformed data.
"""
import json

from bot.backtest import parity


def _t(net, fees=0.12, entry=100.0, qty=1.0, sig="regime_trend",
       setup="swing", reason="TP", fill="exchange_fill_history", gross=None):
    return {"entry_price": entry, "quantity": qty, "cost_usd": entry * qty / 10,
            "leverage": 10, "pnl_usd": net, "gross_pnl": net if gross is None else gross,
            "commission": fees, "signal_type": sig, "strategy_type": setup,
            "close_reason": reason, "fill_source": fill}


def test_missing_file_is_empty_report():
    assert parity.load_closed_trades("/no/such/file.json") == []
    assert "No closed live trades" in parity.format_report(parity.parity_summary([], 0.06))


def test_malformed_file_is_fail_soft(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    assert parity.load_closed_trades(p) == []


def test_wrapper_dict_is_unwrapped(tmp_path):
    p = tmp_path / "w.json"
    p.write_text(json.dumps({"closed": [_t(1.0)]}))
    assert len(parity.load_closed_trades(p)) == 1


def test_realized_pf_and_net():
    s = parity.parity_summary([_t(10), _t(10), _t(-5)], 0.06)
    assert s["trades"] == 3
    assert s["net_pnl"] == 15.0
    assert round(s["pf"], 2) == 4.0  # gross win 20 / gross loss 5
    assert s["win_rate"] == 2 / 3


def test_fee_parity_ratio():
    # 2 trades, $100 notional each ($200 total), $0.24 fees total.
    # realized round-trip rate = 0.24/200 = 0.0012 (0.12%). modeled = 2×0.06% = 0.12%.
    s = parity.parity_summary([_t(1, fees=0.12), _t(1, fees=0.12)], 0.06)
    assert abs(s["realized_fee_rate"] - 0.0012) < 1e-9
    assert abs(s["modeled_fee_rate"] - 0.0012) < 1e-9
    assert abs(s["fee_vs_model"] - 1.0) < 1e-6  # live fees match the model


def test_fee_worse_than_model_flagged():
    s = parity.parity_summary([_t(1, fees=0.60)], 0.06)  # 5× the modeled fee
    assert s["fee_vs_model"] > 1.25
    assert "WORSE than model" in parity.format_report(s)


def test_notional_falls_back_to_cost_times_leverage():
    # No entry/qty -> use cost_usd × leverage.
    t = {"pnl_usd": 1.0, "commission": 0.1, "cost_usd": 20.0, "leverage": 5}
    s = parity.parity_summary([t], 0.06)
    assert s["notional"] == 100.0  # 20 × 5


def test_grouping_by_signal_and_exit_reason():
    trades = [_t(10, sig="regime_trend", reason="TP"),
              _t(-4, sig="momentum_confluence", reason="SL"),
              _t(6, sig="regime_trend", reason="TP")]
    s = parity.parity_summary(trades, 0.06)
    assert s["by_signal_type"]["regime_trend"]["net"] == 16.0
    assert s["by_signal_type"]["momentum_confluence"]["net"] == -4.0
    assert s["by_exit_reason"]["TP"]["trades"] == 2


def test_inferred_fill_count_and_warning():
    trades = [_t(1, fill="ticker_fallback"), _t(1, fill="exchange_fill_history")]
    s = parity.parity_summary(trades, 0.06)
    assert s["inferred_fills"] == 1
    assert "inferred" in parity.format_report(s)
