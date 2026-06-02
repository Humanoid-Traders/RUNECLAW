"""
RUNECLAW Risk Engine -- FAIL-CLOSED pre-trade gatekeeper.

21 independent pre-trade checks. ANY failure = REJECTED. No overrides.
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
  19. Multi-timeframe trend alignment (MTF_ALIGNMENT)
  20. Portfolio concentration via PCA on correlation matrix (CONCENTRATION_PCA)
  21. Portfolio VaR — parametric Value at Risk (PORTFOLIO_VAR)
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from bot.compat import UTC
from typing import Any, Optional

from bot.config import CONFIG
from bot.utils.logger import audit, risk_log
from bot.utils.models import RiskCheck, RiskVerdict, TradeIdea

# Persistence file for safety state (circuit breaker, loss streak, daily PnL).
# Survives restarts so a crash cannot silently clear protective limits.
# F-15 FIX: validate state dir path to prevent traversal.
_state_dir = os.environ.get("RUNECLAW_STATE_DIR", "data")
if os.path.isabs(_state_dir) and not _state_dir.startswith(os.getcwd()):
    import warnings
    warnings.warn(
        f"RUNECLAW_STATE_DIR={_state_dir!r} is an absolute path outside cwd. "
        "Using default 'data' instead.",
        stacklevel=1,
    )
    _state_dir = "data"
_STATE_FILE = os.path.join(_state_dir, "risk_state.json")


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
    # Solana ecosystem (non-meme) — correlated via SOL beta
    "JUP/USDT": "SOLANA_ECO", "JTO/USDT": "SOLANA_ECO",
    "PYTH/USDT": "SOLANA_ECO", "RAY/USDT": "SOLANA_ECO",
    "ORCA/USDT": "SOLANA_ECO", "JITO/USDT": "SOLANA_ECO",
    "TENSOR/USDT": "SOLANA_ECO", "DRIFT/USDT": "SOLANA_ECO",
    "HNT/USDT": "SOLANA_ECO", "MOBILE/USDT": "SOLANA_ECO",
    "W/USDT": "SOLANA_ECO",
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
    21 independent checks -- all must pass (16 in-engine + #17 liquidity in engine.py + #18 macro + #19 MTF alignment + #20 concentration PCA + #21 portfolio VaR).

    Threading model: RUNECLAW runs on a single-threaded asyncio event loop.
    The RLock exists as a defensive measure but does NOT guarantee correctness
    under true multi-threaded use.  Known lock-ordering issue: evaluate() holds
    _lock → calls portfolio.snapshot() (portfolio._lock), while
    portfolio.close_position() holds portfolio._lock → calls record_trade_result()
    (_lock).  This is safe only because both paths execute on the same thread.
    If RUNECLAW is ever made multi-threaded, the lock ordering must be resolved first.
    """

    def __init__(self, portfolio: "PortfolioTracker", state_file: Optional[str] = None,
                 macro_calendar: Optional["MacroCalendar"] = None,
                 macro_provider: Optional[Any] = None) -> None:  # noqa: F821
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
        self._macro_provider = macro_provider  # v2: enhanced macro-event provider
        # Regime-aware risk (Feature #3)
        self._current_regime: str = "UNKNOWN"
        self._current_vol_state: str = "NORMAL"
        # v2: macro size multiplier from last evaluation
        self._last_macro_size_multiplier: float = 1.0
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
        Run all 21 pre-trade checks (16 in-engine + #17 liquidity + #18 macro + #19 MTF alignment + #20 concentration PCA + #21 portfolio VaR).
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
                    failed.append(f"POSITION_SIZE: notional {notional_pct:.1f}% exceeds {max_notional_pct}% cap")
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
            open_value = self._portfolio.get_position_value()
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
            symbol_value = self._portfolio.get_position_value(asset=idea.asset)
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
            # 16. Volatility guard (fail-closed: ATR required and must be > 0)
            # Meme coins get a tighter threshold (4% vs 6% default)
            symbol = getattr(idea, "asset", "") or ""
            meme_group = _CORRELATION_GROUPS.get(f"{symbol}/USDT" if "/" not in symbol else symbol)
            is_meme = meme_group == "MEME"
            vol_threshold = min(CONFIG.risk.volatility_guard_atr_pct, 4.0) if is_meme else CONFIG.risk.volatility_guard_atr_pct

            if atr is None:
                failed.append("VOLATILITY: ATR data unavailable (fail-closed)")
            elif atr <= 0:
                failed.append(f"VOLATILITY: ATR={atr} is zero or negative — bad data (fail-closed)")
            elif idea.entry_price > 0:
                atr_pct = (atr / idea.entry_price) * 100
                tag = " (meme-coin limit)" if is_meme else ""
                if atr_pct > vol_threshold:
                    failed.append(f"VOLATILITY: ATR {atr_pct:.2f}% > {vol_threshold}% guard{tag}")
                else:
                    passed.append(f"VOLATILITY: ATR {atr_pct:.2f}% OK{tag}")
            else:
                failed.append("VOLATILITY: invalid entry price")
        except Exception as exc:
            failed.append(f"VOLATILITY: evaluation error ({exc})")

        try:
            # 18. Macro event risk state (v2: enhanced macro provider with size throttling)
            macro_checked = False
            if self._macro_provider is not None:
                try:
                    ctx = self._macro_provider.get_context(symbol=idea.asset)
                    self._last_macro_size_multiplier = ctx.size_multiplier
                    if ctx.risk_state == "BLOCK_NEW_ENTRIES":
                        failed.append(f"MACRO_EVENT: BLOCK — {ctx.explanation}")
                    elif ctx.risk_state == "REDUCE":
                        passed.append(f"MACRO_EVENT: REDUCE (size×{ctx.size_multiplier}) — {ctx.explanation}")
                        position_usd = position_usd * ctx.size_multiplier
                    else:
                        passed.append(f"MACRO_EVENT: CLEAR")
                    macro_checked = True
                except Exception as exc:
                    failed.append(f"MACRO_EVENT: v2 provider error ({exc}) — fail-closed")
                    macro_checked = True

            if not macro_checked and self._macro_calendar is not None:
                from bot.macro.models import MacroRiskState
                macro_snap = self._macro_calendar.evaluate()
                if macro_snap.state == MacroRiskState.EVENT_LOCKDOWN:
                    ev_label = macro_snap.active_event.label if macro_snap.active_event else "unknown"
                    failed.append(f"MACRO_EVENT: {macro_snap.state.value} - {ev_label}")
                elif macro_snap.state == MacroRiskState.BLACKOUT:
                    failed.append("MACRO_EVENT: BLACKOUT - calendar evaluation failed (fail-closed)")
                else:
                    passed.append(f"MACRO_EVENT: {macro_snap.state.value}")
            elif not macro_checked:
                passed.append("MACRO_EVENT: no calendar configured (skipped)")
        except Exception as exc:
            failed.append(f"MACRO_EVENT: evaluation error ({exc})")

        # 19. Multi-timeframe alignment (Feature #2) — graceful skip if no data
        try:
            mtf_result = self._check_mtf_alignment(idea)
            if mtf_result is not None:
                failed.append(mtf_result)
            else:
                passed.append("MTF_ALIGNMENT: aligned or skipped (no data)")
        except Exception as exc:
            failed.append(f"MTF_ALIGNMENT: evaluation error ({exc})")

        # 20. Portfolio concentration / PCA (Feature #4) — graceful skip if no data
        try:
            conc_result = self._check_concentration()
            if conc_result is not None:
                failed.append(conc_result)
            else:
                passed.append("CONCENTRATION_PCA: OK or skipped (no data)")
        except Exception as exc:
            failed.append(f"CONCENTRATION_PCA: evaluation error ({exc})")

        # 21. Portfolio VaR (parametric Value at Risk)
        try:
            current_var, proposed_var = self._compute_portfolio_var(position_usd)
            max_var = CONFIG.risk.max_portfolio_var_pct
            if proposed_var < 0:
                # Not enough data — skip (fewer than 5 closed trades)
                passed.append("PORTFOLIO_VAR: skipped (insufficient trade history)")
            elif proposed_var > max_var:
                failed.append(
                    f"PORTFOLIO_VAR: proposed {proposed_var:.2f}% > {max_var}% limit "
                    f"(current {current_var:.2f}%)"
                )
            else:
                passed.append(f"PORTFOLIO_VAR: {proposed_var:.2f}% <= {max_var}% limit")
        except Exception as exc:
            failed.append(f"PORTFOLIO_VAR: evaluation error ({exc})")

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

    # ── Feature #1: Adaptive Position Sizing (Kelly Criterion) ────────

    @staticmethod
    def kelly_position_size(
        confidence: float, win_rate: float, avg_win: float, avg_loss: float
    ) -> float:
        """Return optimal position size as a fraction of equity using half-Kelly.

        Parameters:
            confidence: trade confidence [0,1] — used as a scaling factor
            win_rate: historical win rate [0,1]
            avg_win: average winning trade return (positive)
            avg_loss: average losing trade return (positive magnitude)

        Returns:
            Position fraction [0, max_position_pct/100], capped at config limit.
        """
        # Edge cases: no edge → 0
        if win_rate <= 0 or avg_win <= 0 or avg_loss <= 0:
            return 0.0
        if win_rate >= 1.0:
            # Perfect win rate — still cap at config limit
            cap = CONFIG.risk.max_position_pct / 100.0
            return min(0.5 * confidence, cap)

        # Kelly fraction: f* = (p * b - q) / b
        # where p = win_rate, q = 1 - p, b = avg_win / avg_loss
        b = avg_win / avg_loss
        q = 1.0 - win_rate
        kelly_f = (win_rate * b - q) / b

        if kelly_f <= 0:
            return 0.0  # Negative edge — don't bet

        # Half-Kelly for safety, scaled by confidence
        half_kelly = kelly_f * 0.5 * confidence
        cap = CONFIG.risk.max_position_pct / 100.0
        return min(max(half_kelly, 0.0), cap)

    def get_recommended_size(self, idea: TradeIdea) -> float:
        """Compute recommended position size in USD for a given TradeIdea.

        Uses trade history to derive win_rate, avg_win, avg_loss.
        Falls back to fixed-fractional sizing if insufficient history.
        """
        history = self._portfolio.trade_history
        closed = [t for t in history if t.exit_price is not None]

        try:
            state = self._portfolio.snapshot()
            equity = state.equity_usd
        except Exception:
            return 0.0

        if equity <= 0:
            return 0.0

        # Need at least 10 trades for meaningful stats
        if len(closed) < 10:
            # Fallback: fixed-fractional
            return equity * (CONFIG.risk.max_position_pct / 100.0)

        wins = [t for t in closed if t.pnl > 0]
        losses = [t for t in closed if t.pnl <= 0]
        win_rate = len(wins) / len(closed) if closed else 0.0
        avg_win = (sum(t.pnl for t in wins) / len(wins)) if wins else 0.0
        avg_loss = (abs(sum(t.pnl for t in losses)) / len(losses)) if losses else 0.0

        fraction = self.kelly_position_size(idea.confidence, win_rate, avg_win, avg_loss)
        return round(equity * fraction, 2)

    # ── Feature #2: Multi-Timeframe Confirmation ─────────────────────

    @staticmethod
    def check_timeframe_alignment(trends: dict[str, str]) -> tuple[bool, str]:
        """Check if multi-timeframe trends are aligned (2-of-3 rule).

        Parameters:
            trends: dict mapping timeframe labels to trend direction strings
                    e.g. {"1h": "UP", "4h": "UP", "1d": "DOWN"}

        Returns:
            (aligned, reason) — aligned is True if at least 2 of 3 timeframes agree.
        """
        if not trends or len(trends) < 2:
            return True, "insufficient timeframes for alignment check"

        values = [v.upper() for v in trends.values()]
        total = len(values)

        # Count occurrences of each direction
        counts: dict[str, int] = {}
        for v in values:
            counts[v] = counts.get(v, 0) + 1

        # Find the most common direction
        dominant = max(counts, key=lambda k: counts[k])
        dominant_count = counts[dominant]

        # Require at least 2 aligned (or majority if more than 3 timeframes)
        threshold = 2 if total <= 3 else (total // 2 + 1)
        aligned = dominant_count >= threshold

        if aligned:
            tfs = [k for k, v in trends.items() if v.upper() == dominant]
            return True, f"aligned {dominant} on {', '.join(tfs)} ({dominant_count}/{total})"
        else:
            return False, f"no alignment: {counts} across {total} timeframes"

    def _check_mtf_alignment(self, idea: TradeIdea) -> Optional[str]:
        """Internal check for multi-timeframe alignment in risk flow.

        Reads MTF trends from the idea's signals_used if available.
        Gracefully skips (returns None = pass) when no MTF data present.
        """
        # Look for MTF data attached to the idea via signals_used
        mtf_trends: dict[str, str] = {}
        for sig in idea.signals_used:
            # Convention: "MTF:1h=UP", "MTF:4h=DOWN", etc.
            if sig.upper().startswith("MTF:"):
                parts = sig[4:].split("=", 1)
                if len(parts) == 2:
                    mtf_trends[parts[0].strip()] = parts[1].strip()

        if len(mtf_trends) < 2:
            return None  # No MTF data — graceful skip

        aligned, reason = self.check_timeframe_alignment(mtf_trends)
        if not aligned:
            return f"MTF_ALIGNMENT: {reason}"
        return None

    # ── Feature #3: Regime-Aware Risk Parameters ─────────────────────

    _REGIME_MULTIPLIERS: dict[str, dict[str, float]] = {
        "CHOPPY": {"position_size_mult": 0.5, "cooldown_mult": 2.0, "stop_width_mult": 1.0},
        "STRONG_TREND_UP": {"position_size_mult": 1.5, "cooldown_mult": 0.5, "stop_width_mult": 1.0},
        "STRONG_TREND_DOWN": {"position_size_mult": 1.5, "cooldown_mult": 0.5, "stop_width_mult": 1.0},
        "HIGH_VOLATILITY": {"position_size_mult": 0.3, "cooldown_mult": 1.0, "stop_width_mult": 1.5},
        "RANGING": {"position_size_mult": 0.7, "cooldown_mult": 1.5, "stop_width_mult": 1.0},
    }

    _DEFAULT_MULTIPLIERS: dict[str, float] = {
        "position_size_mult": 1.0, "cooldown_mult": 1.0, "stop_width_mult": 1.0,
    }

    def get_regime_adjusted_params(self, regime: str, volatility_state: str) -> dict:
        """Return adjusted risk parameter multipliers based on market regime.

        Parameters:
            regime: market regime string (e.g. "CHOPPY", "STRONG_TREND_UP")
            volatility_state: volatility descriptor (e.g. "HIGH", "NORMAL", "LOW")

        Returns:
            dict with keys: position_size_mult, cooldown_mult, stop_width_mult
        """
        # Update instance state
        self._current_regime = regime
        self._current_vol_state = volatility_state

        base = dict(self._REGIME_MULTIPLIERS.get(regime.upper(), self._DEFAULT_MULTIPLIERS))

        # Overlay volatility adjustments
        vol = volatility_state.upper()
        if vol == "HIGH" and regime.upper() != "HIGH_VOLATILITY":
            # Reduce position size further in high-vol environments
            base["position_size_mult"] = base.get("position_size_mult", 1.0) * 0.7
            base["stop_width_mult"] = base.get("stop_width_mult", 1.0) * 1.3
        elif vol == "LOW":
            # Tighter stops in low-vol
            base["stop_width_mult"] = base.get("stop_width_mult", 1.0) * 0.8

        return base

    # ── Feature #4: Correlation-Weighted Portfolio Risk (PCA) ────────

    @staticmethod
    def check_portfolio_concentration(
        returns_matrix: list[list[float]],
    ) -> tuple[bool, str]:
        """Check portfolio concentration using PCA on correlation matrix.

        Parameters:
            returns_matrix: list of return series (rows=assets, cols=time periods).
                            Each inner list is one asset's returns over time.

        Returns:
            (ok, reason) — ok is False if PC1 explains > 70% of variance.
        """
        n_assets = len(returns_matrix)
        if n_assets < 2:
            return True, "single asset — concentration check not applicable"

        n_periods = min(len(r) for r in returns_matrix) if returns_matrix else 0
        if n_periods < 3:
            return True, "insufficient return history for concentration check"

        # Trim all series to same length
        trimmed = [r[:n_periods] for r in returns_matrix]

        # Compute means
        means = [sum(r) / n_periods for r in trimmed]

        # Compute std devs
        stddevs = []
        for i, r in enumerate(trimmed):
            var = sum((x - means[i]) ** 2 for x in r) / n_periods
            stddevs.append(var ** 0.5)

        # Build correlation matrix (n x n)
        corr = [[0.0] * n_assets for _ in range(n_assets)]
        for i in range(n_assets):
            for j in range(n_assets):
                if i == j:
                    corr[i][j] = 1.0
                    continue
                if stddevs[i] < 1e-12 or stddevs[j] < 1e-12:
                    corr[i][j] = 0.0
                    continue
                cov = sum(
                    (trimmed[i][k] - means[i]) * (trimmed[j][k] - means[j])
                    for k in range(n_periods)
                ) / n_periods
                corr[i][j] = cov / (stddevs[i] * stddevs[j])

        # Power iteration to find largest eigenvalue of correlation matrix
        # Initialize vector
        vec = [1.0 / (n_assets ** 0.5)] * n_assets
        for _ in range(100):  # iterations
            # Matrix-vector multiply
            new_vec = [0.0] * n_assets
            for i in range(n_assets):
                for j in range(n_assets):
                    new_vec[i] += corr[i][j] * vec[j]
            # Compute norm
            norm = sum(x * x for x in new_vec) ** 0.5
            if norm < 1e-12:
                break
            vec = [x / norm for x in new_vec]

        # Eigenvalue = Rayleigh quotient: v^T A v / v^T v
        av = [0.0] * n_assets
        for i in range(n_assets):
            for j in range(n_assets):
                av[i] += corr[i][j] * vec[j]
        eigenvalue = sum(vec[i] * av[i] for i in range(n_assets))

        # Total variance = trace of correlation matrix = n_assets
        total_variance = float(n_assets)
        pc1_explained = eigenvalue / total_variance if total_variance > 0 else 0.0

        if pc1_explained > 0.70:
            return False, (
                f"PC1 explains {pc1_explained:.1%} of variance (> 70% threshold) — "
                f"portfolio too concentrated"
            )
        return True, f"PC1 explains {pc1_explained:.1%} — diversification OK"

    def _check_concentration(self) -> Optional[str]:
        """Internal check for portfolio concentration in risk flow.

        Builds a returns matrix from trade history. Gracefully skips when
        insufficient data is available.
        """
        positions = self._portfolio.open_positions
        if len(positions) < 2:
            return None  # Not enough positions to check

        history = self._portfolio.trade_history
        if len(history) < 5:
            return None  # Not enough history

        # Group closed trades by asset to build return series
        asset_returns: dict[str, list[float]] = {}
        for t in history:
            if t.exit_price is not None and t.entry_price > 0:
                ret = (t.exit_price - t.entry_price) / t.entry_price
                if t.direction.value == "SHORT":
                    ret = -ret
                asset_returns.setdefault(t.asset, []).append(ret)

        # Need at least 2 assets with 3+ returns each
        valid = {a: rets for a, rets in asset_returns.items() if len(rets) >= 3}
        if len(valid) < 2:
            return None  # Graceful skip

        returns_matrix = list(valid.values())
        ok, reason = self.check_portfolio_concentration(returns_matrix)
        if not ok:
            return f"CONCENTRATION_PCA: {reason}"
        return None

    def _compute_portfolio_var(self, position_usd: float, confidence_level: float = 0.95) -> tuple[float, float]:
        """Compute parametric VaR for portfolio including proposed position.

        Returns (current_var_pct, proposed_var_pct) as percentage of equity.
        Uses historical per-trade returns to estimate volatility.

        Returns (-1, -1) when there is insufficient data to compute VaR
        (fewer than 5 closed trades), signalling the caller to skip the check.
        """
        import math

        history = self._portfolio.trade_history
        closed = [t for t in history if t.exit_price is not None and t.entry_price > 0]

        if len(closed) < 5:
            return (-1.0, -1.0)

        state = self._portfolio.snapshot()
        equity = state.equity_usd
        if equity <= 0:
            return (0.0, 100.0)  # Zero equity with pending position = max risk

        # Compute per-trade return percentages
        returns = []
        for t in closed:
            notional = t.entry_price * t.quantity
            if notional > 0:
                returns.append(t.pnl / notional)

        if len(returns) < 5:
            return (-1.0, -1.0)

        # Portfolio volatility from trade returns
        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
        vol = math.sqrt(variance)

        # z-score for confidence level (95% = 1.645)
        z_score = 1.645 if confidence_level == 0.95 else abs(
            math.sqrt(2) * math.erfc(2 * confidence_level - 1)  # rough approx
        )

        # Holding period: 1 day (sqrt(1) = 1)
        holding_period = 1.0

        # Current portfolio exposure (sum of open position notionals)
        current_exposure = 0.0
        for pos in self._portfolio.open_positions:
            current_exposure += pos.entry_price * pos.quantity

        # VaR = z * vol * sqrt(T) * exposure / equity * 100
        sqrt_t = math.sqrt(holding_period)
        current_var_pct = (z_score * vol * sqrt_t * current_exposure / equity * 100) if equity > 0 else 0.0
        proposed_exposure = current_exposure + position_usd
        proposed_var_pct = (z_score * vol * sqrt_t * proposed_exposure / equity * 100) if equity > 0 else 0.0

        return (round(current_var_pct, 4), round(proposed_var_pct, 4))

    def _check_correlation(self, idea: TradeIdea) -> Optional[str]:
        """Prevent concentrated bets in the same correlation group."""
        # W-P2-2 FIX: Normalize asset to /USDT format for consistent lookup.
        # _CORRELATION_GROUPS keys use "BTC/USDT" format.
        asset_key = idea.asset if "/" in idea.asset else f"{idea.asset}/USDT"
        new_group = _CORRELATION_GROUPS.get(asset_key, idea.asset)
        open_groups: list[str] = []

        for pos in self._portfolio.open_positions:
            pos_key = pos.asset if "/" in pos.asset else f"{pos.asset}/USDT"
            group = _CORRELATION_GROUPS.get(pos_key, pos.asset)
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
                f.flush()
                os.fsync(f.fileno())
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
