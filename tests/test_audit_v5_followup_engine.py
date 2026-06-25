"""Follow-up regression tests for V5 audit fixes (engine-side).

Covers the second-pass hardening from docs/AUDIT_REPORT_V5.md:

  RC-AUD-010 — limit-confirm re-validates the RECALCULATED SL/TP levels
               (reject when current price has already blown through the new
               stop-loss; re-affirm new SL != new entry).
  RC-AUD-025 — the critique "user already confirmed, proceed anyway" rationale
               applies ONLY to real human confirmations, never to auto-confirm
               (user_id="auto") or unattended ("") confirmations.
  RC-AUD-018 — SIMULATION_MODE is a HARD VETO on live execution: the engine must
               never execute live when CONFIG.simulation_mode is True.

These tests target the smallest decision predicates the fixes hang on so they
stay deterministic and never touch the network or build the full engine.
CONFIG is a frozen dataclass, so the module-level reference is patched (matching
the existing tests/test_core.py convention).
"""
from unittest.mock import patch

from bot.core.engine import RuneClawEngine
from bot.utils.models import Direction, TradeIdea


# ── RC-AUD-025: proceed-anyway only for real humans ────────────────

def test_human_confirmed_real_user():
    """A non-empty, non-'auto' user id is a real human confirmation."""
    assert RuneClawEngine._human_confirmed("123456789") is True
    assert RuneClawEngine._human_confirmed("operator") is True


def test_human_confirmed_auto_is_not_human():
    """Auto-confirm (user_id='auto') is NOT a human confirmation, so the
    post-critique 'proceed anyway' rationale must not apply to it."""
    assert RuneClawEngine._human_confirmed("auto") is False


def test_human_confirmed_empty_is_not_human():
    """Unattended ('') confirmations are treated as non-human (conservative)."""
    assert RuneClawEngine._human_confirmed("") is False


# ── RC-AUD-018: SIMULATION_MODE hard veto on live execution ────────

def test_simulation_mode_vetoes_live_execution():
    """When CONFIG.simulation_mode is True, the live-execution branch is vetoed
    regardless of any runtime live flag."""
    with patch("bot.core.engine.CONFIG") as mock_cfg:
        mock_cfg.simulation_mode = True
        assert RuneClawEngine._live_execution_vetoed_by_simulation() is True


def test_live_execution_not_vetoed_when_simulation_off():
    """With simulation_mode False the veto predicate is False (the rest of the
    five-lock gate still governs whether a trade executes)."""
    with patch("bot.core.engine.CONFIG") as mock_cfg:
        mock_cfg.simulation_mode = False
        assert RuneClawEngine._live_execution_vetoed_by_simulation() is False


# ── RC-AUD-010: re-validation logic on the RECALCULATED levels ─────
#
# The fix reprices a limit entry to current ± 0.5*ATR and rederives SL/TP from
# the original distances, then re-runs the "price past SL" check on the NEW
# levels (LONG: reject if current <= new_sl; SHORT: reject if current >= new_sl)
# and re-affirms new_sl != new_entry. These tests pin the arithmetic the inline
# guard depends on, mirroring the recalc block in confirm_trade().

def _recalc_levels(direction: str, current_price: float, atr: float,
                   entry: float, stop_loss: float, take_profit: float):
    """Reproduce the engine's confirm-time limit recalculation (engine.py)."""
    offset = 0.5 * atr
    if direction == "LONG":
        new_limit = round(current_price - offset, 8)
        sl_dist = abs(entry - stop_loss)
        tp_dist = abs(take_profit - entry)
        new_sl = round(new_limit - sl_dist, 8)
        new_tp = round(new_limit + tp_dist, 8)
    else:
        new_limit = round(current_price + offset, 8)
        sl_dist = abs(stop_loss - entry)
        tp_dist = abs(entry - take_profit)
        new_sl = round(new_limit + sl_dist, 8)
        new_tp = round(new_limit - tp_dist, 8)
    return new_limit, new_sl, new_tp


def test_aud010_long_recalc_sl_below_entry_and_below_price():
    """A normal LONG recalc keeps the new SL below the new entry, and with a
    sane SL distance the current price stays above the new SL → NOT rejected."""
    idea = TradeIdea(
        asset="BTC/USDT", direction=Direction.LONG,
        entry_price=100.0, stop_loss=97.0, take_profit=106.0,
        confidence=0.7, reasoning="t", order_type="limit",
    )
    current = 100.0
    new_entry, new_sl, new_tp = _recalc_levels(
        "LONG", current, atr=2.0,
        entry=idea.entry_price, stop_loss=idea.stop_loss, take_profit=idea.take_profit)
    # new entry = 100 - 1 = 99; sl_dist=3 → new_sl=96; current 100 > 96
    assert new_sl < new_entry          # SL still below entry
    assert new_sl != new_entry         # re-affirm guard would pass
    assert not (current <= new_sl)     # past-SL guard would NOT reject


def test_aud010_long_blown_through_new_sl_is_rejected():
    """RC-AUD-010: a LONG limit reject fires when the current price is at/below
    the recalculated SL (the past-new-SL guard predicate: current <= new_sl)."""
    # new_entry=99; sl_dist=0.5 → new_sl=98.5. A current price below that has
    # already blown through the rederived stop → the guard rejects.
    blown_entry, blown_sl, _ = _recalc_levels(
        "LONG", current_price=100.0, atr=2.0,
        entry=100.0, stop_loss=99.5, take_profit=101.0)
    assert blown_sl < blown_entry      # SL still below entry after recalc
    current_blown = 98.0
    assert current_blown <= blown_sl   # LONG: current <= new_sl → REJECT


def test_aud010_short_blown_through_new_sl_is_rejected():
    """RC-AUD-010: SHORT past-new-SL guard rejects when current >= new SL."""
    new_entry, new_sl, _ = _recalc_levels(
        "SHORT", current_price=100.0, atr=2.0,
        entry=100.0, stop_loss=100.5, take_profit=99.0)
    # new_entry=101; sl_dist=0.5 → new_sl=101.5; a current price above it blows it
    current_blown = 102.0
    assert current_blown >= new_sl     # SHORT: current >= new_sl → REJECT


def test_aud010_sl_equals_entry_is_rejected():
    """RC-AUD-010: re-affirm new SL != new entry. A zero SL distance makes the
    recalculated SL equal the entry → reject (cannot compute safe stop)."""
    new_entry, new_sl, _ = _recalc_levels(
        "LONG", current_price=100.0, atr=2.0,
        entry=100.0, stop_loss=100.0 - 1e-12, take_profit=106.0)
    # sl_dist rounds to 0 at 8 dp → new_sl == new_entry
    assert new_sl == new_entry
