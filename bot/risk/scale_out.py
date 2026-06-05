"""
RUNECLAW Scale-Out Ladder (Rule 9) & Two-Tranche Entry (Rule 11).

Rule 9: Partial profit-taking ladder:
  - Tranche 1: Close 50% at +3.5% profit
  - Tranche 2: Close 25% at +7.0% profit
  - Runner: 25% trails at 1x ATR

Rule 11: Split entries into two tranches:
  - Tranche 1: 60% of position at signal price
  - Tranche 2: 40% on confirmation (price holds N bars or retests)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from bot.compat import UTC
from typing import Optional

from bot.config import CONFIG

logger = logging.getLogger(__name__)


# ── Scale-Out Ladder (Rule 9) ────────────────────────────────────────

@dataclass
class ScaleOutAction:
    """Describes a partial close action to execute."""
    tranche_id: int          # 1, 2, or 3 (runner)
    close_pct: float         # fraction of ORIGINAL position to close (0.50, 0.25, 0.25)
    trigger_price: float     # price that triggered this action
    action_type: str         # "partial_close" or "trail_active"
    reason: str


@dataclass
class ScaleOutState:
    """Tracks which scale-out tranches have been triggered for a position."""
    trade_id: str
    entry_price: float
    direction: str           # "LONG" or "SHORT"
    original_qty: float
    tranche1_done: bool = False
    tranche2_done: bool = False
    runner_trailing: bool = False
    runner_trail_price: Optional[float] = None
    best_price: float = 0.0  # best favorable price seen


class ScaleOutLadder:
    """Manages scale-out state for all open positions."""

    def __init__(self) -> None:
        self._states: dict[str, ScaleOutState] = {}

    def register(self, trade_id: str, entry_price: float, direction: str, qty: float) -> None:
        """Register a new position for scale-out tracking."""
        self._states[trade_id] = ScaleOutState(
            trade_id=trade_id,
            entry_price=entry_price,
            direction=direction.upper(),
            original_qty=qty,
            best_price=entry_price,
        )
        logger.debug("Scale-out registered: %s @ %.4f qty=%.4f", trade_id, entry_price, qty)

    def unregister(self, trade_id: str) -> None:
        """Remove a closed position from tracking."""
        self._states.pop(trade_id, None)

    def check(self, trade_id: str, current_price: float, atr: float = 0.0) -> list[ScaleOutAction]:
        """Check if any scale-out tranches should fire.

        Returns list of ScaleOutAction to execute (may be empty).
        """
        if not CONFIG.scale_out.enabled:
            return []

        state = self._states.get(trade_id)
        if state is None:
            return []

        actions: list[ScaleOutAction] = []
        cfg = CONFIG.scale_out

        # Compute profit %
        if state.direction == "LONG":
            pnl_pct = (current_price - state.entry_price) / state.entry_price * 100
            state.best_price = max(state.best_price, current_price)
        else:
            pnl_pct = (state.entry_price - current_price) / state.entry_price * 100
            state.best_price = min(state.best_price, current_price) if state.best_price > 0 else current_price

        # Tranche 1: close 50% at +3.5%
        if not state.tranche1_done and pnl_pct >= cfg.tranche1_target_pct:
            state.tranche1_done = True
            actions.append(ScaleOutAction(
                tranche_id=1,
                close_pct=cfg.tranche1_pct / 100.0,
                trigger_price=current_price,
                action_type="partial_close",
                reason=f"Scale-out T1: +{pnl_pct:.1f}% >= +{cfg.tranche1_target_pct}% target",
            ))

        # Tranche 2: close 25% at +7.0%
        if not state.tranche2_done and pnl_pct >= cfg.tranche2_target_pct:
            state.tranche2_done = True
            actions.append(ScaleOutAction(
                tranche_id=2,
                close_pct=cfg.tranche2_pct / 100.0,
                trigger_price=current_price,
                action_type="partial_close",
                reason=f"Scale-out T2: +{pnl_pct:.1f}% >= +{cfg.tranche2_target_pct}% target",
            ))

        # Runner: activate trailing stop at 1x ATR after T2
        if state.tranche2_done and not state.runner_trailing and atr > 0:
            state.runner_trailing = True
            if state.direction == "LONG":
                state.runner_trail_price = current_price - (atr * cfg.runner_trail_atr_mult)
            else:
                state.runner_trail_price = current_price + (atr * cfg.runner_trail_atr_mult)
            actions.append(ScaleOutAction(
                tranche_id=3,
                close_pct=0.0,  # not closing yet, just activating trail
                trigger_price=current_price,
                action_type="trail_active",
                reason=f"Runner trail activated at {state.runner_trail_price:.4f} (1x ATR={atr:.4f})",
            ))

        # Runner trail check: close runner if trail hit
        if state.runner_trailing and state.runner_trail_price is not None and atr > 0:
            # Update trail (only tightens)
            if state.direction == "LONG":
                new_trail = current_price - (atr * cfg.runner_trail_atr_mult)
                if new_trail > state.runner_trail_price:
                    state.runner_trail_price = new_trail
                if current_price <= state.runner_trail_price:
                    actions.append(ScaleOutAction(
                        tranche_id=3,
                        close_pct=cfg.runner_pct / 100.0,
                        trigger_price=current_price,
                        action_type="partial_close",
                        reason=f"Runner trail hit: price {current_price:.4f} <= trail {state.runner_trail_price:.4f}",
                    ))
            else:
                new_trail = current_price + (atr * cfg.runner_trail_atr_mult)
                if new_trail < state.runner_trail_price:
                    state.runner_trail_price = new_trail
                if current_price >= state.runner_trail_price:
                    actions.append(ScaleOutAction(
                        tranche_id=3,
                        close_pct=cfg.runner_pct / 100.0,
                        trigger_price=current_price,
                        action_type="partial_close",
                        reason=f"Runner trail hit: price {current_price:.4f} >= trail {state.runner_trail_price:.4f}",
                    ))

        return actions

    @property
    def active_count(self) -> int:
        return len(self._states)


# ── Two-Tranche Entry (Rule 11) ──────────────────────────────────────

@dataclass
class PendingTranche2:
    """Tracks a pending second-tranche entry."""
    trade_id: str
    asset: str
    direction: str
    entry_price: float       # original signal price
    tranche2_size_usd: float  # USD amount for second tranche
    stop_loss: float
    take_profit: float
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    bars_held: int = 0       # bars since tranche 1 filled
    confirmed: bool = False


class TwoTrancheManager:
    """Manages pending second-tranche entries."""

    def __init__(self) -> None:
        self._pending: dict[str, PendingTranche2] = {}

    def split_entry(self, total_size_usd: float) -> tuple[float, float]:
        """Split a position size into tranche 1 and tranche 2 amounts.

        Returns (tranche1_usd, tranche2_usd).
        If two-tranche is disabled, returns (total, 0).
        """
        if not CONFIG.two_tranche.enabled:
            return (total_size_usd, 0.0)
        t1 = total_size_usd * (CONFIG.two_tranche.tranche1_pct / 100.0)
        t2 = total_size_usd * (CONFIG.two_tranche.tranche2_pct / 100.0)
        return (round(t1, 2), round(t2, 2))

    def register_pending(self, trade_id: str, asset: str, direction: str,
                         entry_price: float, tranche2_usd: float,
                         stop_loss: float, take_profit: float) -> None:
        """Register a pending tranche 2 after tranche 1 fills."""
        if tranche2_usd <= 0:
            return
        self._pending[trade_id] = PendingTranche2(
            trade_id=trade_id,
            asset=asset,
            direction=direction.upper(),
            entry_price=entry_price,
            tranche2_size_usd=tranche2_usd,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        logger.debug("Tranche 2 pending: %s %s $%.2f", asset, direction, tranche2_usd)

    def check_confirmation(self, trade_id: str, current_price: float) -> Optional[PendingTranche2]:
        """Check if tranche 2 should be filled.

        Confirmation: price must stay within retest_tolerance of entry for
        confirmation_bars consecutive checks.

        Returns PendingTranche2 if confirmed, None otherwise.
        """
        pt = self._pending.get(trade_id)
        if pt is None or pt.confirmed:
            return None

        cfg = CONFIG.two_tranche
        tolerance = pt.entry_price * (cfg.retest_tolerance_pct / 100.0)

        # Check if price is near entry zone
        if abs(current_price - pt.entry_price) <= tolerance:
            pt.bars_held += 1
        else:
            # Price moved away — check if it's favorable (already confirming)
            if pt.direction == "LONG" and current_price > pt.entry_price:
                pt.bars_held += 1  # price above entry = confirmation for long
            elif pt.direction == "SHORT" and current_price < pt.entry_price:
                pt.bars_held += 1  # price below entry = confirmation for short
            else:
                pt.bars_held = 0  # reset — price moved against us

        if pt.bars_held >= cfg.confirmation_bars:
            pt.confirmed = True
            return pt

        return None

    def remove(self, trade_id: str) -> None:
        """Remove a pending tranche (filled or cancelled)."""
        self._pending.pop(trade_id, None)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def pending_list(self) -> list[PendingTranche2]:
        return list(self._pending.values())
