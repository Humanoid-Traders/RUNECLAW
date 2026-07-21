"""Open positions + stop-loss PROTECTION TRUTH web endpoint.

The serializers are pure (object-in, dict-out) so they're unit-tested directly;
the async handler needs a live aiohttp request + engine, so its read-only /
gated contract is checked by source assertion (same approach as the other
gateway handlers).
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from bot.web import user_gateway as ug


# ── LIVE serializer: sl_order_id IS the protection truth ─────────────────────

def _live(**kw):
    base = dict(symbol="BTC/USDT:USDT", direction="LONG", entry_price=100.0,
                stop_loss=95.0, take_profit=115.0, quantity=1.0, cost_usd=20.0,
                leverage=5.0, sl_order_id="x1", tp_order_id="y1",
                strategy_type="swing", opened_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_live_row_marks_protected_when_sl_order_id_present():
    row = ug._live_position_row(_live(sl_order_id="abc"))
    assert row["sl_order"] == "exchange"
    assert row["sl_protected"] is True
    assert row["unprotected"] is False
    assert row["pair"] == "BTC"
    # SL distance from entry (100 → 95) = 5%.
    assert row["sl_dist_pct"] == 5.0


def test_live_row_flags_unprotected_when_no_sl_order_but_stop_set():
    row = ug._live_position_row(_live(sl_order_id=None, stop_loss=95.0))
    assert row["sl_order"] == "manual"
    assert row["sl_protected"] is False
    assert row["unprotected"] is True          # live + stop set + no exchange order = real risk


def test_live_row_respects_runtime_unprotected_marker():
    pos = _live(sl_order_id="abc")
    setattr(pos, "unprotected", True)          # escalation path marked it
    row = ug._live_position_row(pos)
    assert row["unprotected"] is True


# ── PAPER serializer: bot-managed, never an "unprotected" alarm ──────────────

def _paper():
    from enum import Enum

    class D(Enum):
        LONG = "LONG"
    return SimpleNamespace(
        trade_id="t1", asset="ETH/USDT:USDT", direction=D.LONG,
        entry_price=100.0, exit_price=None, quantity=2.0, stop_loss=90.0,
        take_profit=120.0, leverage=5.0, pnl=0.0, commission=0.0,
        strategy_type="swing", opened_at=None, closed_at=None)


def test_paper_row_is_bot_managed_not_unprotected():
    row = ug._paper_position_row(_paper())
    # No exchange in paper — the stop is bot-managed in-sim, which is truthful,
    # NOT the red "unprotected" alarm (that only means a LIVE missing stop).
    assert row["sl_order"] == "manual"
    assert row["sl_protected"] is False
    assert row["unprotected"] is False
    assert row["pair"] == "ETH"
    assert row["sl_dist_pct"] == 10.0


# ── handler contract: registered, read-only, gated, sl_order_id truth ────────

def test_positions_route_is_registered():
    src = inspect.getsource(ug.build_gateway)
    assert 'add_get("/positions", handle_positions)' in src


def test_positions_handler_is_read_only_and_uses_sl_order_truth():
    src = inspect.getsource(ug.handle_positions)
    assert '"read_only": True' in src
    assert "_executor_for" in src              # reaches the live executor (SL truth source)
    assert "unprotected_count" in src
    # No order placement / position-close call in the read path (the docstring
    # says it "closes" nothing; assert on actual method calls, not prose).
    assert "place_order" not in src
    assert "close_position" not in src and ".close(" not in src
