"""
Dead `_fill_deriv_metrics` removed from OrderFlowAnalyzer (deep-audit low #53).

`_fill_deriv_metrics` duplicated the inline funding/OI path that `analyze()`
actually uses, but had no callers — a stale second copy that had already diverged
(it carried OI-snapshot tracking the live path handles separately). It is now
deleted; the live funding/OI metrics still come from analyze()'s inline path.
This test locks the removal so the dead duplicate can't quietly reappear.
"""

from bot.core.order_flow import OrderFlowAnalyzer


def test_fill_deriv_metrics_is_gone():
    assert not hasattr(OrderFlowAnalyzer, "_fill_deriv_metrics")


def test_funding_and_oi_fields_still_exist_on_signal():
    # The live path still populates these; only the dead helper was removed.
    from bot.core.order_flow import OrderFlowSignal
    sig = OrderFlowSignal(symbol="BTC/USDT")
    assert hasattr(sig, "funding_rate")
    assert hasattr(sig, "open_interest_usd")
    assert hasattr(sig, "oi_change_pct")
