"""
RUNECLAW Risk Engine -- FAIL-CLOSED pre-trade gatekeeper.

18 independent pre-trade checks. ANY failure = REJECTED. No overrides.
Design: if a check cannot be evaluated, the trade is REJECTED (fail-closed).

Note: Check #17 (liquidity guard) lives in engine.py via OrderFlowAnalyzer,
not in this module.  It is fail-open (no data = pass) by design.

Checks:
  1.  Circuit breaker status
  2.  Position size limit (fixed-fractional, capped at 20% notional)
  3.  Daily loss limit (realized + unrealized PnL)
  4.  Max drawdown limit
  5.  Max open positions
  6.  Risk-reward ratio minimum
  7.  Confidence threshold
  8.  Correlation / concentration per group
  9.  Consecutive loss streak
  10. Entry price sanity
  11. Stop-loss required
  12. Stale data guard
  13. Cooldown after loss
  14. Portfolio exposure limit
  15. Per-symbol exposure limit
  16. Volatility guard (ATR as % of price)
  17. Liquidity guard (fail-open, runs in engine.py via OrderFlowAnalyzer)
  18. Macro event risk state (EVENT_LOCKDOWN/BLACKOUT = reject)
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import UTC, datetime
from typing import Optional

from bot.config import CONFIG
from bot.utils.logger import audit, risk_log
from bot.utils.models import RiskCheck, RiskVerdict, TradeIdea

# Persistence file for safety state (circuit breaker, loss streak, daily PnL).
# Survives restarts so a crash cannot silently clear protective limits.
_STATE_FILE = os.path.join(
    os.environ.get("RUNECLAW_STATE_DIR", "data"), "risk_state.json"
)


# Known correlation groups for crypto assets.
# Assets NOT in this map are treated as their own group (group=symbol),
# so correlation limits only bind for mapped pairs.
_CORRELATION_GROUPS: dict[str, str] = {
    # Bitcoin ecosystem
    "BTC/USDT": "BTC", "WBTC/USDT": "BTC",
    # Ethereum ecosystem
    "ETH/USDT": "ETH", "STETH/USDT": "ETH", "WETH/USDT": "ETH",
    # Alt L1s — tend to move together in risk-off
    "SOL/USDT": "ALT_L1", "AVAX/USDT": "ALT_L1", "NEAR/USDT": "ALT_L1",
    "SUI/USDT": "ALT_L1", "APT/USDT": "ALT_L1", "DOT/USDT": "ALT_L1",
    "ADA/USDT": "ALT_L1", "ATOM/USDT": "ALT_L1", "TON/USDT": "ALT_L1",
    "HBAR/USDT": "ALT_L1", "TRX/USDT": "ALT_L1", "FTM/USDT": "ALT_L1",
    "SEI/USDT": "ALT_L1", "INJ/USDT": "ALT_L1",
    # Meme coins
    "DOGE/USDT": "MEME", "SHIB/USDT": "MEME", "PEPE/USDT": "MEME",
    "FLOKI/USDT": "MEME", "WIF/USDT": "MEME", "BONK/USDT": "MEME",
    "BRETT/USDT": "MEME", "MEME/USDT": "MEME",
    # DeFi blue chips
    "UNI/USDT": "DEFI", "AAVE/USDT": "DEFI", "LINK/USDT": "DEFI",
    "MKR/USDT": "DEFI", "SNX/USDT": "DEFI", "CRV/USDT": "DEFI",
    "LDO/USDT": "DEFI", "COMP/USDT": "DEFI",
    # L2s
    "ARB/USDT": "L2", "OP/USDT": "L2", "MATIC/USDT": "L2",
    "STRK/USDT": "L2", "ZK/USDT": "L2",
    # AI narrative
    "FET/USDT": "AI", "RENDER/USDT": "AI", "TAO/USDT": "AI",
    "RNDR/USDT": "AI", "AGIX/USDT": "AI",
    # Exchange tokens
    "BNB/USDT": "CEX", "CRO/USDT": "CEX", "OKB/USDT": "CEX",
}


class RiskEngine:
    """
    Pre-trade and post-trade risk checks.
    Design principle: if ANY check cannot be evaluated, the trade is REJECTED.
    18 independent checks -- all must pass (16 in-engine + #17 liquidity in engine.py + #18 macro).

    Threading model: RUNECLAW runs on a single-threaded asyncio event loop.
    The RLock exists as a defensive measure but does NOT guarantee correctness
    under true multi-threaded use.  Known lock-ordering issue: evaluate() holds
    _lock → calls portfolio.snapshot() (portfolio._lock), while
    portfolio.close_position() holds portfolio._lock → calls record_trade_result()
    (_lock).  This is safe only because both paths execute on the same thread.
    If RUNECLAW is ever made multi-threaded, the lock ordering must be resolved first.
    """

    def __init__(self, portfolio: "PortfolioTracker", state_file: Optional[str] = None,
                 macro_calendar: Optional["MacroCalendar"] = None) -> None:  # noqa: F821
        self._portfolio = portfolio
        self._circuit_open = False
        self._consecutive_losses = 0
        self._last_loss_time: Optional[float] = None  # epoch seconds
        self._circuit_breaker_trips = 0
        self._total_checks = 0
        self._total_rejections = 0
        self._rejection_history: list[dict] = []  # recent rejections for /rejected command
        self._lock = threading.RLock()
        self._state_file = state_file or _STATE_FILE
        self._macro_calendar = macro_calendar
        # F-01: reload persisted safety state so restarts don't clear the breaker
        self._load_state()

    @property
    def circuit_breaker_active(self) -> bool:
        return self._circuit_open

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses

    @property
    def rejection_history(self) -> list[dict]:
        """Recent risk rejections for audit/display."""
        return list(self._rejection_history)

    @property
    def stats(self) -> dict:
        return {
            "total_checks": self._total_checks,
            "total_rejections": self._total_rejections,
            "circuit_breaker_trips": self._circuit_breaker_trips,
            "consecutive_losses": self._consecutive_losses,
        }

    def record_trade_result(self, pnl: float) -> None:
        """Track consecutive losses for streak-based circuit breaker."""
        with self._lock:
            self._record_trade_result_locked(pnl)

    def _record_trade_result_locked(self, pnl: float) -> None:
        if pnl <= 0:
            self._consecutive_losses += 1
            self._last_loss_time = time.time()
            self._save_state()
            if self._consecutive_losses >= CONFIG.risk.max_consecutive_losses:
                self._trip_circuit_breaker(
                    f"consecutive loss streak: {self._consecutive_losses}"
                )
        else:
            self._consecutive_losses = 0
            self._save_state()

    def evaluate(self, idea: TradeIdea, atr: Optional[float] = None) -> RiskCheck:
        """
        Run all 18 pre-trade checks (16 in-engine + #17 liquidity + #18 macro).
        Returns RiskCheck with APPROVED or REJECTED.
        Pass atr= for volatility guard check.
        """
        with self._lock:
            return self._evaluate_locked(idea, atr)

    def _evaluate_locked(self, idea: TradeIdea, atr: Optional[float] = None) -> RiskCheck:
        self._total_checks += 1
        passed: list[str] = []
        failed: list[str] = []

        try:
            state = self._portfolio.snapshot()
        except Exception as exc:
            self._total_rejections += 1
            return RiskCheck(
                trade_id=idea.id,
                verdict=RiskVerdict.REJECTED,
                reason=f"Portfolio state unavailable: {exc}",
                checks_failed=[f"PORTFOLIO_STATE: {exc}"],
                timestamp=datetime.now(UTC),
            )

        position_usd = state.equity_usd * (CONFIG.risk.max_position_pct / 100.0)

        # Fixed-fractional risk sizing: size by stop distance, not flat notional.
        # risk_budget = equity * max_position_pct (the max we're willing to lose)
        # position_usd = risk_budget / (stop_distance / entry_price)
        # The notional cap (20%) is enforced by check #2 below, NOT here.
        # This separation gives the check real authority: if a tight stop would
        # produce an oversized position, the check catches it and caps it.
        stop_distance_pct = abs(idea.entry_price - idea.stop_loss) / idea.entry_price if idea.entry_price > 0 else 0
        uncapped_position_usd = position_usd  # fallback: flat notional
        if stop_distance_pct > 0:
            risk_budget = state.equity_usd * (CONFIG.risk.max_position_pct / 100.0)
            uncapped_position_usd = risk_budget / stop_distance_pct
            position_usd = uncapped_position_usd  # uncapped -- check #2 will cap if needed

        # ── Individual checks — each wrapped so a raised exception → REJECTED ──
        # This is the fail-closed contract: if ANY check cannot be evaluated,
        # the trade is REJECTED.  No silent pass-through on errors.

        try:
            # 1. Circuit breaker
            if self._circuit_open:
                failed.append("CIRCUIT_BREAKER: system halted due to prior losses")
            else:
                passed.append("CIRCUIT_BREAKER: OK")
        except Exception as exc:
            failed.append(f"CIRCUIT_BREAKER: evaluation error ({exc})")

        try:
            # 2. Position size — enforces notional cap (the check has real authority)
            if state.equity_usd <= 0:
                failed.append("EQUITY: zero or negative equity")
            else:
                notional_pct = (position_usd / state.equity_usd * 100)
                max_notional_pct = CONFIG.risk.max_symbol_exposure_pct  # 20% default
                if notional_pct <= max_notional_pct + 0.01:  # tiny epsilon for float math
                    passed.append(f"POSITION_SIZE: notional {notional_pct:.1f}% <= {max_notional_pct}%")
                else:
                    # Cap position to max notional and log the clamping
                    max_notional = state.equity_usd * (max_notional_pct / 100.0)
                    passed.append(f"POSITION_SIZE: clamped {notional_pct:.1f}% -> {max_notional_pct}% (${position_usd:.0f} -> ${max_notional:.0f})")
                    position_usd = max_notional
        except Exception as exc:
            failed.append(f"POSITION_SIZE: evaluation error ({exc})")

        try:
            # 3. Daily loss (realized + unrealized) — measured against equity, not free cash
            daily_loss_pct = abs(state.daily_pnl / state.equity_usd * 100) if state.equity_usd > 0 else 0
            if state.daily_pnl < 0 and daily_loss_pct >= CONFIG.risk.max_daily_loss_pct:
                failed.append(f"DAILY_LOSS: {daily_loss_pct:.1f}% >= {CONFIG.risk.max_daily_loss_pct}%")
                self._trip_circuit_breaker("daily loss limit breached")
            else:
                passed.append(f"DAILY_LOSS: {daily_loss_pct:.1f}% OK")
        except Exception as exc:
            failed.append(f"DAILY_LOSS: evaluation error ({exc})")
            daily_loss_pct = 0  # safe default for downstream

        try:
            # 4. Drawdown
            if state.max_drawdown_pct >= CONFIG.risk.max_drawdown_pct:
                failed.append(f"DRAWDOWN: {state.max_drawdown_pct:.1f}% >= {CONFIG.risk.max_drawdown_pct}%")
                self._trip_circuit_breaker("max drawdown breached")
            else:
                passed.append(f"DRAWDOWN: {state.max_drawdown_pct:.1f}% OK")
        except Exception as exc:
            failed.append(f"DRAWDOWN: evaluation error ({exc})")

        try:
            # 5. Open positions limit
            if state.open_positions >= CONFIG.risk.max_open_positions:
                failed.append(f"MAX_POSITIONS: {state.open_positions} >= {CONFIG.risk.max_open_positions}")
            else:
                passed.append(f"OPEN_POSITIONS: {state.open_positions} OK")
        except Exception as exc:
            failed.append(f"MAX_POSITIONS: evaluation error ({exc})")

        try:
            # 6. Risk-reward ratio (0.01 tolerance for float rounding at boundary)
            rr = idea.risk_reward_ratio
            if rr < CONFIG.risk.min_risk_reward - 0.01:
                failed.append(f"RISK_REWARD: {rr} < {CONFIG.risk.min_risk_reward} minimum")
            else:
                passed.append(f"RISK_REWARD: {rr} OK")
        except Exception as exc:
            failed.append(f"RISK_REWARD: evaluation error ({exc})")

        try:
            # 7. Confidence threshold
            if idea.confidence < CONFIG.risk.min_confidence:
                failed.append(f"CONFIDENCE: {idea.confidence} < {CONFIG.risk.min_confidence} minimum")
            else:
                passed.append(f"CONFIDENCE: {idea.confidence} OK")
        except Exception as exc:
            failed.append(f"CONFIDENCE: evaluation error ({exc})")

        try:
            # 8. Correlation / concentration check
            corr_result = self._check_correlation(idea)
            if corr_result:
                failed.append(corr_result)
            else:
                passed.append("CORRELATION: no concentrated exposure")
        except Exception as exc:
            failed.append(f"CORRELATION: evaluation error ({exc})")

        try:
            # 9. Consecutive loss streak (H4 fix: 3+ streak = soft reject, hard stop via circuit breaker at max)
            if self._consecutive_losses >= 3:
                failed.append(f"LOSS_STREAK: {self._consecutive_losses} consecutive losses (>= 3)")
            else:
                passed.append(f"LOSS_STREAK: {self._consecutive_losses} OK")
        except Exception as exc:
            failed.append(f"LOSS_STREAK: evaluation error ({exc})")

        try:
            # 10. Entry price sanity
            if idea.entry_price <= 0:
                failed.append(f"ENTRY_PRICE: invalid ({idea.entry_price})")
            else:
                passed.append("ENTRY_PRICE: valid")
        except Exception as exc:
            failed.append(f"ENTRY_PRICE: evaluation error ({exc})")

        try:
            # 11. Stop-loss required
            if CONFIG.risk.require_stop_loss:
                if idea.stop_loss <= 0:
                    failed.append("STOP_LOSS: required but missing or invalid")
                elif idea.stop_loss == idea.entry_price:
                    failed.append("STOP_LOSS: cannot equal entry price")
                else:
                    passed.append("STOP_LOSS: present and valid")
            else:
                passed.append("STOP_LOSS: not required (config)")
        except Exception as exc:
            failed.append(f"STOP_LOSS: evaluation error ({exc})")

        try:
            # 12. Stale data guard
            data_age = (datetime.now(UTC) - idea.timestamp).total_seconds()
            if data_age > CONFIG.risk.stale_data_max_age_seconds:
                failed.append(f"STALE_DATA: idea is {data_age:.0f}s old > {CONFIG.risk.stale_data_max_age_seconds}s max")
            else:
                passed.append(f"STALE_DATA: {data_age:.0f}s old OK")
        except Exception as exc:
            failed.append(f"STALE_DATA: evaluation error ({exc})")

        try:
            # 13. Cooldown after loss
            if self._last_loss_time is not None:
                elapsed = time.time() - self._last_loss_time
                if elapsed < CONFIG.risk.cooldown_after_loss_seconds:
                    remaining = CONFIG.risk.cooldown_after_loss_seconds - elapsed
                    failed.append(f"COOLDOWN: {remaining:.0f}s remaining after last loss")
                else:
                    passed.append("COOLDOWN: cooldown period elapsed")
            else:
                passed.append("COOLDOWN: no recent losses")
        except Exception as exc:
            failed.append(f"COOLDOWN: evaluation error ({exc})")

        try:
            # 14. Portfolio exposure limit (mark-to-market)
            open_value = sum(
                self._portfolio._last_prices.get(p.asset, p.entry_price) * p.quantity
                for p in self._portfolio.open_positions
            )
            exposure_pct = (open_value / state.equity_usd * 100) if state.equity_usd > 0 else 0
            new_exposure = exposure_pct + (position_usd / state.equity_usd * 100 if state.equity_usd > 0 else 0)
            if new_exposure > CONFIG.risk.max_portfolio_exposure_pct:
                failed.append(f"PORTFOLIO_EXPOSURE: {new_exposure:.1f}% > {CONFIG.risk.max_portfolio_exposure_pct}%")
            else:
                passed.append(f"PORTFOLIO_EXPOSURE: {new_exposure:.1f}% OK")
        except Exception as exc:
            failed.append(f"PORTFOLIO_EXPOSURE: evaluation error ({exc})")

        try:
            # 15. Per-symbol exposure limit (mark-to-market)
            symbol_value = sum(
                self._portfolio._last_prices.get(p.asset, p.entry_price) * p.quantity
                for p in self._portfolio.open_positions
                if p.asset == idea.asset
            )
            new_symbol_value = symbol_value + position_usd
            symbol_exposure_pct = (new_symbol_value / state.equity_usd * 100) if state.equity_usd > 0 else 0
            if symbol_exposure_pct > CONFIG.risk.max_symbol_exposure_pct:
                failed.append(
                    f"SYMBOL_EXPOSURE: {idea.asset} at {symbol_exposure_pct:.1f}% > "
                    f"{CONFIG.risk.max_symbol_exposure_pct}% max"
                )
            else:
                passed.append(f"SYMBOL_EXPOSURE: {idea.asset} {symbol_exposure_pct:.1f}% OK")
        except Exception as exc:
            failed.append(f"SYMBOL_EXPOSURE: evaluation error ({exc})")

        try:
            # 16. Volatility guard (if ATR provided)
            if atr is not None and idea.entry_price > 0:
                atr_pct = (atr / idea.entry_price) * 100
                if atr_pct > CONFIG.risk.volatility_guard_atr_pct:
                    failed.append(f"VOLATILITY: ATR {atr_pct:.2f}% > {CONFIG.risk.volatility_guard_atr_pct}% guard")
                else:
                    passed.append(f"VOLATILITY: ATR {atr_pct:.2f}% OK")
            else:
                passed.append("VOLATILITY: no ATR data (skipped)")
        except Exception as exc:
            failed.append(f"VOLATILITY: evaluation error ({exc})")

        try:
            # 18. Macro event risk state
            if self._macro_calendar is not None:
                from bot.macro.models import MacroRiskState
                macro_snap = self._macro_calendar.evaluate()
                if macro_snap.state == MacroRiskState.EVENT_LOCKDOWN:
                    ev_label = macro_snap.active_event.label if macro_snap.active_event else "unknown"
                    failed.append(f"MACRO_EVENT: {macro_snap.state.value} - {ev_label}")
                elif macro_snap.state == MacroRiskState.BLACKOUT:
                    failed.append("MACRO_EVENT: BLACKOUT - calendar evaluation failed (fail-closed)")
                else:
                    passed.append(f"MACRO_EVENT: {macro_snap.state.value}")
            else:
                passed.append("MACRO_EVENT: no calendar configured (skipped)")
        except Exception as exc:
            failed.append(f"MACRO_EVENT: evaluation error ({exc})")

        # -- Verdict --
        verdict = RiskVerdict.APPROVED if len(failed) == 0 else RiskVerdict.REJECTED
        reason = "; ".join(failed) if failed else f"All {len(passed)} checks passed"

        if verdict == RiskVerdict.REJECTED:
            self._total_rejections += 1
            self._rejection_history.append({
                "trade_id": idea.id,
                "asset": idea.asset,
                "direction": idea.direction.value,
                "confidence": idea.confidence,
                "checks_failed": failed,
                "reason": reason,
                "timestamp": datetime.now(UTC).isoformat(),
            })
            # Cap rejection history to prevent unbounded growth
            if len(self._rejection_history) > 50:
                self._rejection_history = self._rejection_history[-25:]

        check = RiskCheck(
            trade_id=idea.id,
            verdict=verdict,
            position_size_usd=round(position_usd, 2),
            position_pct=round(
                (position_usd / state.equity_usd * 100) if state.equity_usd > 0 else 0, 2
            ),
            daily_loss_pct=round(daily_loss_pct, 2),
            drawdown_pct=round(state.max_drawdown_pct, 2),
            checks_passed=passed,
            checks_failed=failed,
            reason=reason,
            timestamp=datetime.now(UTC),
        )

        audit(risk_log, f"Risk {verdict.value} for {idea.asset} [{len(passed)}P/{len(failed)}F]",
              action="risk_check", result=verdict.value,
              data=check.model_dump(mode="json"))
        return check

    def _check_correlation(self, idea: TradeIdea) -> Optional[str]:
        """Prevent concentrated bets in the same correlation group."""
        new_group = _CORRELATION_GROUPS.get(idea.asset, idea.asset)
        open_groups: list[str] = []

        for pos in self._portfolio.open_positions:
            group = _CORRELATION_GROUPS.get(pos.asset, pos.asset)
            open_groups.append(group)

        group_count = open_groups.count(new_group)
        max_per_group = CONFIG.risk.max_correlation_per_group
        if group_count >= max_per_group:
            return (
                f"CORRELATION: already {group_count} positions in group '{new_group}' "
                f"(max {max_per_group} per group)"
            )
        return None

    # -- Persistence (F-01) --

    def _load_state(self) -> None:
        """Restore safety state from disk.
        Fix 3 (fail-closed persistence):
          - Missing file → fresh start (no prior state to honor).
          - Empty file → fresh start (equivalent to missing).
          - Corrupt file (non-empty but invalid JSON) → assume breaker TRIPPED (fail-closed).
        """
        try:
            if not os.path.exists(self._state_file):
                return
            with open(self._state_file) as f:
                raw = f.read()
            if not raw.strip():
                # Empty file = no prior state (same as missing)
                return
            data = json.loads(raw)
            self._circuit_open = data.get("circuit_open", False)
            self._consecutive_losses = data.get("consecutive_losses", 0)
            self._last_loss_time = data.get("last_loss_time")
            self._circuit_breaker_trips = data.get("circuit_breaker_trips", 0)
            if self._circuit_open:
                audit(risk_log, "Circuit breaker state restored from disk: ACTIVE",
                      action="state_restore", result="LOADED")
        except (json.JSONDecodeError, ValueError, KeyError):
            # Corrupt file (non-empty but invalid) → fail-closed: assume breaker was tripped
            self._circuit_open = True
            self._circuit_breaker_trips += 1
            audit(risk_log, "Corrupt state file — assuming circuit breaker ACTIVE (fail-closed)",
                  action="state_restore", result="CORRUPT_FAIL_CLOSED")
        except Exception:
            # Other I/O errors (permissions, etc.) → also fail-closed
            self._circuit_open = True
            self._circuit_breaker_trips += 1
            audit(risk_log, "State file unreadable — assuming circuit breaker ACTIVE (fail-closed)",
                  action="state_restore", result="IO_FAIL_CLOSED")

    def _save_state(self) -> None:
        """Persist safety-critical state to disk. Called on every state change."""
        try:
            os.makedirs(os.path.dirname(self._state_file) or ".", exist_ok=True)
            data = {
                "circuit_open": self._circuit_open,
                "consecutive_losses": self._consecutive_losses,
                "last_loss_time": self._last_loss_time,
                "circuit_breaker_trips": self._circuit_breaker_trips,
                "saved_at": datetime.now(UTC).isoformat(),
            }
            tmp = self._state_file + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, self._state_file)  # atomic on POSIX
        except Exception as exc:
            # Log save failure -- circuit breaker state is safety-critical
            audit(risk_log, f"Failed to persist risk state: {exc}",
                  action="save_state", result="ERROR")

    def _trip_circuit_breaker(self, reason: str) -> None:
        if not self._circuit_open:
            self._circuit_open = True
            self._circuit_breaker_trips += 1
            audit(risk_log, f"CIRCUIT BREAKER TRIPPED: {reason}",
                  action="circuit_breaker", result="HALTED")
            self._save_state()

    def emergency_halt(self, reason: str) -> None:
        """Manual emergency halt — persists via _trip_circuit_breaker so it
        survives restarts (audit finding B: /halt must persist)."""
        with self._lock:
            self._trip_circuit_breaker(reason)

    def reset_circuit_breaker(self) -> None:
        """Manual reset -- requires human intervention."""
        with self._lock:
            self._circuit_open = False
            self._consecutive_losses = 0
            self._last_loss_time = None
            audit(risk_log, "Circuit breaker manually reset",
                  action="circuit_breaker", result="RESET")
            self._save_state()
