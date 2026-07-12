"""
Parity data hygiene (2026-07-12) — from the first live /parity report:

  1. 75 of 292 "trades" were never-filled order records (expired /
     canceled / price_drift / stale_pending, all $0 PnL) diluting the
     headline win rate → performance stats now exclude them.
  2. The "By setup" / "By signal type" parity buckets were empty for
     every trade ever closed: LivePosition carried strategy_type /
     signal_type since the strategy-type work but the serializer never
     wrote them → now round-tripped through closed_trades.json.
  3. "SL HIT (inferred)" contained 29% winners: the local stop checks
     labeled breakeven-ratcheted stops (profit side of entry, trailing
     flag unset — e.g. adopted positions) as bare "SL HIT" → both local
     check sites now route through stop_exit_label's geometry test.
"""

from __future__ import annotations

import json

import pytest

from bot.utils.close_reason import (NON_FILL_CLOSE_REASONS, is_filled_close,
                                    stop_exit_label)


# ── 1. non-fill exclusion ────────────────────────────────────────────
def test_is_filled_close_truth_table():
    for r in NON_FILL_CLOSE_REASONS:
        assert is_filled_close(r, 0.0) is False        # lifecycle noise
        assert is_filled_close(r.upper(), 0.0) is False  # case-insensitive
        assert is_filled_close(r, -3.2) is True        # real PnL never dropped
    assert is_filled_close("SL HIT", -5.0) is True
    assert is_filled_close("TP HIT", 5.0) is True
    assert is_filled_close(None, 0.0) is True          # unknown -> keep
    assert is_filled_close("", 0.0) is True


def _trade(reason, pnl, symbol="BTC/USDT:USDT", **extra):
    t = {"symbol": symbol, "pnl_usd": pnl, "gross_pnl": pnl,
         "commission": 0.1, "cost_usd": 20.0, "leverage": 5,
         "close_reason": reason, "signal_type": "momentum_confluence",
         "strategy_type": "swing"}
    t.update(extra)
    return t


def test_parity_summary_excludes_non_fills():
    from bot.backtest.parity import parity_summary, format_report
    trades = [
        _trade("TP HIT", 10.0),
        _trade("SL HIT", -4.0),
        _trade("canceled", 0.0),
        _trade("price_drift", 0.0),
        _trade("expired", 0.0),
    ]
    s = parity_summary(trades, modeled_commission_pct=0.06)
    assert s["trades"] == 2                    # only the real fills
    assert s["excluded_non_fills"] == 3
    assert s["win_rate"] == 0.5                # 1 of 2, not 1 of 5
    report = format_report(s)
    assert "3 never-filled records excluded" in report


def test_parity_summary_no_exclusion_line_when_clean():
    from bot.backtest.parity import parity_summary, format_report
    s = parity_summary([_trade("TP HIT", 10.0)], modeled_commission_pct=0.06)
    assert s["excluded_non_fills"] == 0
    assert "excluded" not in format_report(s)


# ── 2. provenance round trip ─────────────────────────────────────────
def test_closed_trade_provenance_round_trips(tmp_path):
    from datetime import datetime, timezone
    from bot.core.live_executor import LiveExecutor, LivePosition
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._closed_trades = [LivePosition(
        trade_id="T1", symbol="ETH/USDT:USDT", direction="LONG",
        entry_price=100.0, quantity=1.0, cost_usd=20.0,
        stop_loss=95.0, take_profit=110.0, leverage=5,
        close_price=110.0, pnl_usd=9.0, status="closed",
        close_reason="TP HIT", strategy_type="scalp",
        signal_type="vwap_reversion",
        opened_at=datetime.now(timezone.utc),
        closed_at=datetime.now(timezone.utc),
    )]
    ex._save_closed_trades()
    raw = json.loads(open(ex._closed_trades_file).read())
    assert raw[0]["strategy_type"] == "scalp"
    assert raw[0]["signal_type"] == "vwap_reversion"

    ex2 = LiveExecutor(state_dir=str(tmp_path))
    t = ex2._closed_trades[0]
    assert t.strategy_type == "scalp"
    assert t.signal_type == "vwap_reversion"


def test_parity_setup_bucket_renders_with_provenance():
    """With provenance recorded, the By-setup section must render (it was
    invisible when every record fell into the single (unknown) bucket)."""
    from bot.backtest.parity import parity_summary, format_report
    trades = [_trade("TP HIT", 10.0, strategy_type="scalp"),
              _trade("SL HIT", -4.0, strategy_type="swing")]
    report = format_report(parity_summary(trades, 0.06))
    assert "By setup" in report
    assert "scalp" in report and "swing" in report


# ── 3. profit-locking stop labels ────────────────────────────────────
class _Pos:
    def __init__(self, direction, entry, sl, tp=0.0, trailing=False):
        self.direction = direction
        self.entry_price = entry
        self.stop_loss = sl
        self.take_profit = tp
        self.trailing_state = {"trailing_active": True} if trailing else None


def test_local_stop_check_labels_profit_lock_by_geometry():
    from bot.core.live_executor import LiveExecutor
    ex = LiveExecutor.__new__(LiveExecutor)
    # LONG whose stop ratcheted ABOVE entry, trailing flag UNSET (adopted
    # position shape) — closing there is a profit-lock, not a loss.
    hit, label = ex._local_stop_breached(
        _Pos("LONG", entry=100.0, sl=104.0), price=103.5)
    assert hit and label == "TRAILING SL HIT"
    # Genuine protective stop below entry stays a bare SL HIT.
    hit, label = ex._local_stop_breached(
        _Pos("LONG", entry=100.0, sl=95.0), price=94.9)
    assert hit and label == "SL HIT"
    # SHORT mirror: stop below entry = profit side.
    hit, label = ex._local_stop_breached(
        _Pos("SHORT", entry=100.0, sl=96.0), price=96.2)
    assert hit and label == "TRAILING SL HIT"


def test_static_check_uses_stop_exit_label():
    import inspect
    from bot.core.live_executor import LiveExecutor
    src = inspect.getsource(LiveExecutor.check_positions)
    assert 'reason = "TRAILING SL HIT" if' not in src   # inline form gone
    assert "stop_exit_label(" in src


def test_stop_exit_label_geometry_unchanged():
    # The shared helper's contract (pinned): profit-side stop -> trailing.
    assert stop_exit_label(True, 100.0, 104.0) == "TRAILING SL HIT"
    assert stop_exit_label(True, 100.0, 95.0) == "SL HIT"
    assert stop_exit_label(False, 100.0, 96.0) == "TRAILING SL HIT"
