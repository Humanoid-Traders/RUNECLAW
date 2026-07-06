"""
Fee-aware entry gate: reject trades whose TP reward doesn't clear round-trip
cost (fees + slippage) by a safety multiple. Complements min-RR (a ratio) by
enforcing an ABSOLUTE fee-clearing edge. Gated, default OFF, skips manual trades.
"""
import os, tempfile
from contextlib import contextmanager
from datetime import datetime

from bot.compat import UTC
from bot.config import CONFIG
from bot.risk.risk_engine import RiskEngine
from bot.risk.portfolio import PortfolioTracker
from bot.utils.models import TradeIdea, Direction, RiskVerdict


@contextmanager
def _gate(enabled):
    # CONFIG.risk is a frozen dataclass — toggle via object.__setattr__ + restore.
    old = CONFIG.risk.fee_aware_entry_gate_enabled
    object.__setattr__(CONFIG.risk, "fee_aware_entry_gate_enabled", enabled)
    try:
        yield
    finally:
        object.__setattr__(CONFIG.risk, "fee_aware_entry_gate_enabled", old)


def _engine():
    state = os.path.join(tempfile.mkdtemp(prefix="rc-fee-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)


def _idea(tp=100.30, sl=99.85, source="scan"):
    # reward 0.30% / risk 0.15% -> RR 2.0 (clears min-RR), but the 0.30% TP is
    # below 2x the ~0.22% round-trip cost -> a fee-loser the min-RR gate misses.
    return TradeIdea(asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
                     stop_loss=sl, take_profit=tp, confidence=0.9,
                     reasoning="fee gate test", source=source,
                     timestamp=datetime.now(UTC))


def _has_fee_reject(check):
    return any("FEE_AWARE" in c for c in check.checks_failed)


def test_gate_off_does_not_fee_reject():
    with _gate(False):
        check = _engine().evaluate(_idea(), atr=2.0)
    assert not _has_fee_reject(check)


def test_gate_on_rejects_fee_loser():
    with _gate(True):
        check = _engine().evaluate(_idea(tp=100.30), atr=2.0)
    assert check.verdict == RiskVerdict.REJECTED
    assert _has_fee_reject(check)


def test_gate_on_allows_clearing_trade():
    with _gate(True):
        check = _engine().evaluate(_idea(tp=105.0, sl=97.5), atr=2.0)
    assert not _has_fee_reject(check)


def test_gate_skips_manual_trades():
    with _gate(True):
        check = _engine().evaluate(_idea(tp=100.30, source="manual"), atr=2.0)
    assert not _has_fee_reject(check)
