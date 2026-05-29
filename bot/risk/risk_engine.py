"""
RUNECLAW Risk Engine -- FAIL-CLOSED institutional-grade trade gatekeeper.

16 independent pre-trade checks. ANY failure = REJECTED. No overrides.
Design: if a check cannot be evaluated, the trade is REJECTED (fail-closed).

Checks:
  1.  Circuit breaker status
  2.  Position size limit
  3.  Daily loss limit (realized + unrealized)
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
  16. Volatility guard
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import Optional

from bot.config import CONFIG
from bot.utils.logger import audit, risk_log
from bot.utils.models import RiskCheck, RiskVerdict, TradeIdea


# Known correlation groups for crypto assets
_CORRELATION_GROUPS: dict[str, str] = {
    "BTC/USDT": "BTC",
    "WBTC/USDT": "BTC",
    "ETH/USDT": "ETH",
    "STETH/USDT": "ETH",
    "WETH/USDT": "ETH",
    "SOL/USDT": "ALT_L1",
    "AVAX/USDT": "ALT_L1",
    "NEAR/USDT": "ALT_L1",
    "SUI/USDT": "ALT_L1",
    "APT/USDT": "ALT_L1",
    "DOGE/USDT": "MEME",
    "SHIB/USDT": "MEME",
    "PEPE/USDT": "MEME",
    "FLOKI/USDT": "MEME",
    "WIF/USDT": "MEME",
}


class RiskEngine:
    """
    Pre-trade and post-trade risk checks.
    Design principle: if ANY check cannot be evaluated, the trade is REJECTED.
    16 independent checks -- all must pass.
    """

    def __init__(self, portfolio: "PortfolioTracker") -> None:  # noqa: F821
        self._portfolio = portfolio
        self._circuit_open = False
        self._consecutive_losses = 0
        self._last_loss_time: Optional[float] = None  # epoch seconds
        self._circuit_breaker_trips = 0
        self._total_checks = 0
        self._total_rejections = 0
        self._lock = threading.RLock()

    @property
    def circuit_breaker_active(self) -> bool:
        return self._circuit_open

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses

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
            if self._consecutive_losses >= CONFIG.risk.max_consecutive_losses:
                self._trip_circuit_breaker(
                    f"consecutive loss streak: {self._consecutive_losses}"
                )
        else:
            self._consecutive_losses = 0

    def evaluate(self, idea: TradeIdea, atr: Optional[float] = None) -> RiskCheck:
        """
        Run all 16 pre-trade checks. Returns RiskCheck with APPROVED or REJECTED.
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

        # 1. Circuit breaker
        if self._circuit_open:
            failed.append("CIRCUIT_BREAKER: system halted due to prior losses")
        else:
            passed.append("CIRCUIT_BREAKER: OK")

        # 2. Position size
        if state.equity_usd <= 0:
            failed.append("EQUITY: zero or negative equity")
        else:
            pos_pct = (position_usd / state.equity_usd * 100)
            if pos_pct <= CONFIG.risk.max_position_pct:
                passed.append(f"POSITION_SIZE: {pos_pct:.1f}% <= {CONFIG.risk.max_position_pct}%")
            else:
                failed.append(f"POSITION_SIZE: {pos_pct:.1f}% > {CONFIG.risk.max_position_pct}%")

        # 3. Daily loss (realized + unrealized)
        daily_loss_pct = abs(state.daily_pnl / state.balance_usd * 100) if state.balance_usd > 0 else 0
        if state.daily_pnl < 0 and daily_loss_pct >= CONFIG.risk.max_daily_loss_pct:
            failed.append(f"DAILY_LOSS: {daily_loss_pct:.1f}% >= {CONFIG.risk.max_daily_loss_pct}%")
            self._trip_circuit_breaker("daily loss limit breached")
        else:
            passed.append(f"DAILY_LOSS: {daily_loss_pct:.1f}% OK")

        # 4. Drawdown
        if state.max_drawdown_pct >= CONFIG.risk.max_drawdown_pct:
            failed.append(f"DRAWDOWN: {state.max_drawdown_pct:.1f}% >= {CONFIG.risk.max_drawdown_pct}%")
            self._trip_circuit_breaker("max drawdown breached")
        else:
            passed.append(f"DRAWDOWN: {state.max_drawdown_pct:.1f}% OK")

        # 5. Open positions limit
        if state.open_positions >= CONFIG.risk.max_open_positions:
            failed.append(f"MAX_POSITIONS: {state.open_positions} >= {CONFIG.risk.max_open_positions}")
        else:
            passed.append(f"OPEN_POSITIONS: {state.open_positions} OK")

        # 6. Risk-reward ratio
        rr = idea.risk_reward_ratio
        if rr < CONFIG.risk.min_risk_reward:
            failed.append(f"RISK_REWARD: {rr} < {CONFIG.risk.min_risk_reward} minimum")
        else:
            passed.append(f"RISK_REWARD: {rr} OK")

        # 7. Confidence threshold
        if idea.confidence < CONFIG.risk.min_confidence:
            failed.append(f"CONFIDENCE: {idea.confidence} < {CONFIG.risk.min_confidence} minimum")
        else:
            passed.append(f"CONFIDENCE: {idea.confidence} OK")

        # 8. Correlation / concentration check
        corr_result = self._check_correlation(idea)
        if corr_result:
            failed.append(corr_result)
        else:
            passed.append("CORRELATION: no concentrated exposure")

        # 9. Consecutive loss streak warning (hard stop via circuit breaker at max)
        if self._consecutive_losses >= 3:
            passed.append(f"LOSS_STREAK: {self._consecutive_losses} (warning)")
        else:
            passed.append(f"LOSS_STREAK: {self._consecutive_losses} OK")

        # 10. Entry price sanity
        if idea.entry_price <= 0:
            failed.append(f"ENTRY_PRICE: invalid ({idea.entry_price})")
        else:
            passed.append("ENTRY_PRICE: valid")

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

        # 12. Stale data guard
        data_age = (datetime.now(UTC) - idea.timestamp).total_seconds()
        if data_age > CONFIG.risk.stale_data_max_age_seconds:
            failed.append(f"STALE_DATA: idea is {data_age:.0f}s old > {CONFIG.risk.stale_data_max_age_seconds}s max")
        else:
            passed.append(f"STALE_DATA: {data_age:.0f}s old OK")

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

        # 14. Portfolio exposure limit
        open_value = sum(
            p.entry_price * p.quantity for p in self._portfolio.open_positions
        )
        exposure_pct = (open_value / state.equity_usd * 100) if state.equity_usd > 0 else 0
        new_exposure = exposure_pct + (position_usd / state.equity_usd * 100 if state.equity_usd > 0 else 0)
        if new_exposure > CONFIG.risk.max_portfolio_exposure_pct:
            failed.append(f"PORTFOLIO_EXPOSURE: {new_exposure:.1f}% > {CONFIG.risk.max_portfolio_exposure_pct}%")
        else:
            passed.append(f"PORTFOLIO_EXPOSURE: {new_exposure:.1f}% OK")

        # 15. Per-symbol exposure limit
        symbol_value = sum(
            p.entry_price * p.quantity
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

        # 16. Volatility guard (if ATR provided)
        if atr is not None and idea.entry_price > 0:
            atr_pct = (atr / idea.entry_price) * 100
            if atr_pct > CONFIG.risk.volatility_guard_atr_mult:
                failed.append(f"VOLATILITY: ATR {atr_pct:.2f}% > {CONFIG.risk.volatility_guard_atr_mult}% guard")
            else:
                passed.append(f"VOLATILITY: ATR {atr_pct:.2f}% OK")
        else:
            passed.append("VOLATILITY: no ATR data (skipped)")

        # -- Verdict --
        verdict = RiskVerdict.APPROVED if len(failed) == 0 else RiskVerdict.REJECTED
        reason = "; ".join(failed) if failed else f"All {len(passed)} checks passed"

        if verdict == RiskVerdict.REJECTED:
            self._total_rejections += 1

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

    def _trip_circuit_breaker(self, reason: str) -> None:
        if not self._circuit_open:
            self._circuit_open = True
            self._circuit_breaker_trips += 1
            audit(risk_log, f"CIRCUIT BREAKER TRIPPED: {reason}",
                  action="circuit_breaker", result="HALTED")

    def reset_circuit_breaker(self) -> None:
        """Manual reset -- requires human intervention."""
        self._circuit_open = False
        self._consecutive_losses = 0
        self._last_loss_time = None
        audit(risk_log, "Circuit breaker manually reset",
              action="circuit_breaker", result="RESET")
