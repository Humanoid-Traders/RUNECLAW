"""`/liveclose` must accept the id the bot actually shows the operator. The
"Order IDs (for cancel)" list and the Open-Orders card surface the EXCHANGE
order id (e.g. 145736281252), but close_position historically resolved only the
internal trade_id (e.g. TI-3455cd75) — so copying the displayed id returned
"not found or already closed/closing". `_resolve_trade_id` accepts either.
"""
from types import SimpleNamespace

from bot.core.live_executor import LiveExecutor


def _exec_with(positions):
    ex = LiveExecutor.__new__(LiveExecutor)
    ex._positions = positions
    return ex


def _pos(limit=None, sl=None, tp=None):
    return SimpleNamespace(limit_order_id=limit, sl_order_id=sl, tp_order_id=tp)


def test_internal_trade_id_resolves_to_itself():
    ex = _exec_with({"TI-abc": _pos(limit="999")})
    assert ex._resolve_trade_id("TI-abc") == "TI-abc"


def test_exchange_limit_order_id_resolves_to_trade_id():
    # The TRUMP field report: displayed 145736281252, internal TI-3455cd75.
    ex = _exec_with({"TI-3455cd75": _pos(limit="145736281252")})
    assert ex._resolve_trade_id("145736281252") == "TI-3455cd75"


def test_sl_and_tp_order_ids_resolve():
    ex = _exec_with({"TI-x": _pos(sl="SL1", tp="TP1")})
    assert ex._resolve_trade_id("SL1") == "TI-x"
    assert ex._resolve_trade_id("TP1") == "TI-x"


def test_unknown_or_empty_id_returns_none():
    ex = _exec_with({"TI-x": _pos(limit="1")})
    assert ex._resolve_trade_id("does-not-exist") is None
    assert ex._resolve_trade_id("") is None


def test_internal_id_takes_precedence_over_scan():
    # A direct trade_id hit short-circuits (no accidental cross-match).
    ex = _exec_with({"TI-a": _pos(limit="TI-b"), "TI-b": _pos(limit="x")})
    assert ex._resolve_trade_id("TI-b") == "TI-b"
