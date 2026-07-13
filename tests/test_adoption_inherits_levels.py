"""
Adoption level inheritance (2026-07-13) — live LTC incident.

An LTC SHORT limit (SL 43.7373 / TP 42.6388) filled on the exchange; the
fill report lagged, the sweep adopted the position as an untracked
orphan, and the local record was booked "LIMIT CANCELED — order not
filled". The adopted position carried NO strategy levels (generic 3%/6%
safety defaults at best — 3.4x wider than the intended 0.87% stop) even
though the bot knew the stops all along.

_find_adoption_level_donor now matches an adopted position back to the
local record it came from (same symbol+direction, entry within 0.5%,
protective-side stop) — live pending/open records first, then records
closed as never-filled within 24h — and adoption inherits SL/TP plus
setup provenance before falling back to safety defaults.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

from bot.core.live_executor import LiveExecutor, LivePosition

UTC = timezone.utc


def _executor(tmp_path):
    return LiveExecutor(state_dir=str(tmp_path))


def _rec(status="pending_fill", symbol="LTC/USDT:USDT", direction="SHORT",
         entry=43.36, sl=43.7373, tp=42.6388, close_reason=None,
         closed_hours_ago=None, strategy_type="scalp",
         signal_type="vwap_reversion"):
    closed_at = (datetime.now(UTC) - timedelta(hours=closed_hours_ago)
                 if closed_hours_ago is not None else None)
    return LivePosition(
        trade_id=f"TI-donor-{status}", symbol=symbol, direction=direction,
        entry_price=entry, quantity=1.5, cost_usd=6.5,
        stop_loss=sl, take_profit=tp, leverage=10, status=status,
        close_reason=close_reason, closed_at=closed_at,
        strategy_type=strategy_type, signal_type=signal_type,
        opened_at=datetime.now(UTC))


# ── donor matching ───────────────────────────────────────────────────
def test_pending_record_is_the_donor(tmp_path):
    ex = _executor(tmp_path)
    ex._positions["T1"] = _rec(status="pending_fill")
    d = ex._find_adoption_level_donor("LTC/USDT:USDT", "SHORT", 43.36)
    assert d is not None and d.stop_loss == 43.7373


def test_just_cancelled_record_is_the_donor(tmp_path):
    """The exact live shape: record booked canceled moments before the
    sweep adopted its fill."""
    ex = _executor(tmp_path)
    ex._closed_trades = [_rec(status="closed", close_reason="canceled",
                              closed_hours_ago=0.1)]
    d = ex._find_adoption_level_donor("LTC/USDT:USDT", "SHORT", 43.37)  # 0.02% off
    assert d is not None and d.take_profit == 42.6388


def test_no_donor_on_mismatches(tmp_path):
    ex = _executor(tmp_path)
    ex._closed_trades = [
        _rec(status="closed", close_reason="canceled", closed_hours_ago=1,
             direction="LONG"),                          # wrong direction
        _rec(status="closed", close_reason="canceled", closed_hours_ago=1,
             entry=44.5),                                # entry >0.5% away
        _rec(status="closed", close_reason="SL HIT", closed_hours_ago=1),  # real fill
        _rec(status="closed", close_reason="canceled", closed_hours_ago=30),  # stale
        _rec(status="closed", close_reason="canceled", closed_hours_ago=1,
             sl=0.0),                                    # no stop to inherit
    ]
    assert ex._find_adoption_level_donor("LTC/USDT:USDT", "SHORT", 43.36) is None


def test_inverted_stop_is_never_inherited(tmp_path):
    """A SHORT donor whose stop is BELOW entry (inverted/corrupt) must not
    become the adopted position's stop."""
    ex = _executor(tmp_path)
    ex._positions["T1"] = _rec(status="pending_fill", sl=42.0)  # below entry
    assert ex._find_adoption_level_donor("LTC/USDT:USDT", "SHORT", 43.36) is None


def test_newest_closed_match_wins(tmp_path):
    ex = _executor(tmp_path)
    old = _rec(status="closed", close_reason="expired", closed_hours_ago=5,
               sl=43.9)
    new = _rec(status="closed", close_reason="canceled", closed_hours_ago=0.2,
               sl=43.7373)
    ex._closed_trades = [old, new]                      # chronological order
    d = ex._find_adoption_level_donor("LTC/USDT:USDT", "SHORT", 43.36)
    assert d is new


# ── adoption wiring ──────────────────────────────────────────────────
def test_adoption_wires_inheritance_before_safety_defaults():
    src = inspect.getsource(LiveExecutor.adopt_exchange_positions)
    assert "_find_adoption_level_donor" in src
    assert "adoption_inherit_levels" in src
    # Inheritance must run BEFORE the generic 3%/6% fallback.
    assert src.index("_find_adoption_level_donor") < src.index("default_sl_pct")
    # Exchange-reported SL/TP still takes precedence (inherit only if missing).
    assert src.index("stopLossId") < src.index("_find_adoption_level_donor")


def test_provenance_travels_with_the_levels(tmp_path):
    ex = _executor(tmp_path)
    ex._positions["T1"] = _rec(status="pending_fill")
    d = ex._find_adoption_level_donor("LTC/USDT:USDT", "SHORT", 43.36)
    assert d.strategy_type == "scalp" and d.signal_type == "vwap_reversion"
