"""
Regression tests for the V5 deep-audit fixes (docs/AUDIT_REPORT_V5.md).

Covers:
  RC-AUD-002 — auto-confirm disabled by default; live auto-confirm is opt-in.
  RC-AUD-008 — manual trades still bind portfolio-safety checks
               (loss-streak, cooldown), only signal-opinion checks are skipped.
  RC-AUD-006 — _find_order_by_client_oid distinguishes "verified absent" from
               "lookup failed" (the fail-closed precondition for the
               double-submit guard).
"""
import os
import tempfile
import time

import pytest

from bot.config import CONFIG
from bot.core.live_executor import LiveExecutor
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, RiskVerdict, TradeIdea


# ── RC-AUD-002: auto-confirm — operator-activated defaults ──────────
# The operator activated autonomous live execution (see FLAG_ACTIVATION.md):
# the in-code defaults now enable auto-confirm at the 0.85 admin bar, gated on
# CALIBRATED confidence (only tightens). The RC-AUD-002 mechanism is intact —
# live auto-execution still flows through the explicit auto_confirm_live_enabled
# gate in engine._tick (asserted structurally in test_audit_v7_fixes.py) — only
# the default of that opt-in flipped, by deliberate operator choice.

def test_auto_confirm_operator_activated_defaults():
    """Auto-confirm defaults reflect the operator's activation: 0.85 threshold,
    live execution enabled, calibrated gating on (tightens the bar to realized
    win-rate). Set the env vars to 1.0 / false to restore the fail-closed posture."""
    assert CONFIG.auto_confirm_threshold == 0.85
    assert CONFIG.auto_confirm_live_enabled is True
    assert CONFIG.auto_confirm_use_calibrated is True


# ── RC-AUD-008: manual trades still bind portfolio-safety checks ────

def _make_engine(balance: float = 10_000.0) -> RiskEngine:
    state_file = os.path.join(tempfile.mkdtemp(), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=balance), state_file=state_file)


def _manual_idea(**kw) -> TradeIdea:
    defaults = dict(
        asset="BTC/USDT", direction=Direction.LONG,
        entry_price=100.0, stop_loss=95.0, take_profit=115.0,
        confidence=0.75, reasoning="manual test", source="manual",
    )
    defaults.update(kw)
    return TradeIdea(**defaults)


def test_manual_trade_blocked_by_loss_streak():
    """RC-AUD-008: a manual trade is still rejected during a loss streak."""
    eng = _make_engine()
    soft_limit = max(2, CONFIG.risk.max_consecutive_losses - 2)
    eng._consecutive_losses = soft_limit
    result = eng.evaluate(_manual_idea(), atr=1.0)
    assert result.verdict == RiskVerdict.REJECTED
    assert any("LOSS_STREAK" in c for c in result.checks_failed)


def test_manual_trade_blocked_by_cooldown():
    """RC-AUD-008: a manual trade still respects the post-loss cooldown."""
    eng = _make_engine()
    eng._last_loss_time = time.time()  # just lost — cooldown active
    result = eng.evaluate(_manual_idea(), atr=1.0)
    assert result.verdict == RiskVerdict.REJECTED
    assert any("COOLDOWN" in c for c in result.checks_failed)


# ── RC-AUD-006: order lookup distinguishes absent from unverified ───

class _LookupExchange:
    """Fake exchange whose order-lookup behaviour is configurable."""

    def __init__(self, mode):
        self.mode = mode

    async def fetch_open_orders(self, symbol):
        if self.mode == "raise":
            raise TimeoutError("venue unreachable")
        if self.mode == "found":
            return [{"id": "O1", "clientOrderId": "C1", "info": {"clientOid": "C1"}}]
        return []  # absent

    async def fetch_closed_orders(self, symbol):
        if self.mode == "raise":
            raise TimeoutError("venue unreachable")
        return []


@pytest.mark.asyncio
async def test_find_order_verified_absent():
    """Lookup succeeds and finds nothing → (None, verified=True)."""
    ex = LiveExecutor()
    order, verified = await ex._find_order_by_client_oid(
        _LookupExchange("absent"), "BTC/USDT", "C1")
    assert order is None
    assert verified is True


@pytest.mark.asyncio
async def test_find_order_unverified_on_outage():
    """RC-AUD-006: every lookup fails → (None, verified=False) so callers fail-closed."""
    ex = LiveExecutor()
    order, verified = await ex._find_order_by_client_oid(
        _LookupExchange("raise"), "BTC/USDT", "C1")
    assert order is None
    assert verified is False


@pytest.mark.asyncio
async def test_find_order_found_is_verified():
    """A matching order is returned with verified=True."""
    ex = LiveExecutor()
    order, verified = await ex._find_order_by_client_oid(
        _LookupExchange("found"), "BTC/USDT", "C1")
    assert order is not None
    assert verified is True
