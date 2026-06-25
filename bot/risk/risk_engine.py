"""
RUNECLAW Risk Engine -- FAIL-CLOSED pre-trade gatekeeper.

23 independent pre-trade checks. ANY failure = REJECTED. No overrides.
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
  22. Taker 3-bar gate (Gate 2) — 3 consecutive bars confirming direction
  23. Bid dominance gate (Rule 20) — bid:ask >= 2:1 for LONG entries
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime
from bot.compat import UTC
from typing import Any, Callable, Optional

from bot.config import CONFIG
from bot.utils.logger import audit, risk_log
from bot.utils.models import RiskCheck, RiskVerdict, TradeIdea

# Persistence file for safety state (circuit breaker, loss streak, daily PnL).
# Survives restarts so a crash cannot silently clear protective limits.
# F-15 FIX: validate state dir path to prevent traversal.
# C2-44 FIX: resolve relative paths and validate containment.
_state_dir = os.environ.get("RUNECLAW_STATE_DIR", "data")
_resolved_state_dir = os.path.realpath(_state_dir)
if os.path.isabs(_state_dir) and not _resolved_state_dir.startswith(os.getcwd()):
    import warnings
    warnings.warn(
        f"RUNECLAW_STATE_DIR={_state_dir!r} resolves to {_resolved_state_dir!r} "
        "which is outside cwd. Using default 'data' instead.",
        stacklevel=1,
    )
    _state_dir = "data"
elif ".." in os.path.normpath(_state_dir).split(os.sep):
    import warnings
    warnings.warn(
        f"RUNECLAW_STATE_DIR={_state_dir!r} contains '..' traversal. "
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
    # US Stock tokenized (Track 3) — grouped by sector
    # Primary tokenized ("ON" suffix)
    "AAPLON/USDT": "STOCK_TECH", "MSFTON/USDT": "STOCK_TECH",
    "GOOGLON/USDT": "STOCK_TECH", "AMZNON/USDT": "STOCK_TECH",
    "METAON/USDT": "STOCK_TECH", "NVDAON/USDT": "STOCK_TECH",
    "AMDON/USDT": "STOCK_TECH", "TSLAON/USDT": "STOCK_TECH",
    "QQQON/USDT": "STOCK_INDEX", "SPYON/USDT": "STOCK_INDEX",
    # Replica RWA ("R" prefix)
    "RAAPL/USDT": "STOCK_TECH", "RMSFT/USDT": "STOCK_TECH",
    "RGOOGL/USDT": "STOCK_TECH", "RAMZN/USDT": "STOCK_TECH",
    "RMETA/USDT": "STOCK_TECH", "RNVDA/USDT": "STOCK_TECH",
    "RAMD/USDT": "STOCK_TECH", "RTSLA/USDT": "STOCK_TECH",
    "RSPY/USDT": "STOCK_INDEX", "RQQQ/USDT": "STOCK_INDEX",
    "RCOIN/USDT": "STOCK_CRYPTO", "RHOOD/USDT": "STOCK_CRYPTO",
    "RARM/USDT": "STOCK_SEMI", "RMRVL/USDT": "STOCK_SEMI",
    "RDELL/USDT": "STOCK_TECH", "RINTC/USDT": "STOCK_SEMI",
}


class RiskEngine:
    """
    Pre-trade and post-trade risk checks.
    Design principle: if ANY check cannot be evaluated, the trade is REJECTED.
    23 independent checks -- all must pass (20 in-engine + #17 liquidity in engine.py via OrderFlowAnalyzer + #22 taker 3-bar + #23 bid dominance).

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
                 macro_provider: Optional[Any] = None,
                 order_flow_analyzer: Optional[Any] = None) -> None:  # noqa: F821
        self._portfolio = portfolio
        self._circuit_open = False
        self._consecutive_losses = 0
        self._last_loss_time: Optional[float] = None  # epoch seconds
        self._circuit_breaker_trips = 0
        self._total_checks = 0
        self._total_rejections = 0
        # C2-45 FIX: deque with maxlen auto-prunes, no manual size checks needed
        self._rejection_history: deque[dict] = deque(maxlen=50)
        self._lock = threading.RLock()
        self._state_file = state_file or _STATE_FILE
        self._macro_calendar = macro_calendar
        self._macro_provider = macro_provider  # v2: enhanced macro-event provider
        self._order_flow = order_flow_analyzer  # Gate 2 + Rule 20
        # Regime-aware risk (Feature #3)
        self._current_regime: str = "UNKNOWN"
        self._current_vol_state: str = "NORMAL"
        # v2: macro size multiplier from last evaluation
        self._last_macro_size_multiplier: float = 1.0
        # C2-42: persist last computed daily loss for fail-safe fallback
        self._last_known_daily_loss_pct: float = 0.0
        # Last order flow signal for gate checks
        self._last_of_signal: Optional[Any] = None
        # C2-34: combined state saver — when set, _save_state delegates to this
        self._combined_saver: Optional[Callable] = None
        # F-01: reload persisted safety state so restarts don't clear the breaker
        self._load_state()
        # Feature: Equity curve circuit breaker
        self._equity_history: list[float] = []
        self._equity_curve_halved: bool = False
        self._equity_curve_paused: bool = False
        # Feature: Drawdown recovery mode
        self._in_drawdown_recovery: bool = False
        self._recovery_start_dd: float = 0.0
        # Feature: Rolling return correlation (V2)
        self._price_history: dict[str, list[float]] = {}
        # Feature: Warning rate circuit breaker
        # Tracks infrastructure warnings (EXCEPTION/CRITICAL_FAIL audit events).
        # If any single warning key fires > threshold times in the sliding window,
        # signal generation is paused until the rate drops.
        self._warning_events: deque[tuple[float, str]] = deque(maxlen=500)
        self._warning_rate_window: float = 3600.0   # 1 hour sliding window
        self._warning_rate_threshold: int = 5        # >5 of same key per hour = trip
        self._warning_rate_tripped: bool = False
        self._warning_rate_trip_key: str = ""

    @property
    def circuit_breaker_active(self) -> bool:
        return self._circuit_open

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses

    @property
    def rejection_history(self) -> list[dict]:
        """Recent risk rejections for audit/display.
        C2-43 FIX: deepcopy prevents callers from mutating internal state."""
        import copy
        return copy.deepcopy(list(self._rejection_history))

    # ── Warning rate circuit breaker ──────────────────────────────────────
    @property
    def warning_rate_breaker_active(self) -> bool:
        """True if infrastructure warnings are firing too frequently."""
        return self._warning_rate_tripped

    def record_warning(self, key: str) -> None:
        """Record an infrastructure warning event (EXCEPTION, CRITICAL_FAIL, etc).

        Called from audit sites in live_executor/analyzer when a catch block
        fires.  If the same ``key`` fires more than ``_warning_rate_threshold``
        times within ``_warning_rate_window`` seconds, the warning rate breaker
        trips and blocks new trades until the rate subsides.
        """
        now = time.time()
        self._warning_events.append((now, key))
        # Prune stale events
        cutoff = now - self._warning_rate_window
        while self._warning_events and self._warning_events[0][0] < cutoff:
            self._warning_events.popleft()
        # Count occurrences of this key in the window
        count = sum(1 for _, k in self._warning_events if k == key)
        if count > self._warning_rate_threshold:
            if not self._warning_rate_tripped:
                self._warning_rate_tripped = True
                self._warning_rate_trip_key = key
                audit(risk_log,
                      f"WARNING RATE BREAKER TRIPPED: '{key}' fired {count}x "
                      f"in last {self._warning_rate_window:.0f}s "
                      f"(threshold: {self._warning_rate_threshold})",
                      action="warning_rate_breaker", result="TRIPPED",
                      data={"key": key, "count": count,
                            "threshold": self._warning_rate_threshold},
                      level=logging.CRITICAL)
        else:
            # Auto-recover when rate drops below threshold
            if self._warning_rate_tripped and self._warning_rate_trip_key == key:
                self._warning_rate_tripped = False
                audit(risk_log,
                      f"Warning rate breaker cleared: '{key}' rate back to {count}",
                      action="warning_rate_breaker", result="CLEARED",
                      data={"key": key, "count": count})

    def warning_rate_summary(self) -> dict[str, int]:
        """Return counts of each warning key in the current window."""
        now = time.time()
        cutoff = now - self._warning_rate_window
        counts: dict[str, int] = {}
        for ts, key in self._warning_events:
            if ts >= cutoff:
                counts[key] = counts.get(key, 0) + 1
        return counts

    def set_order_flow_signal(self, signal) -> None:
        """Cache the latest OrderFlowSignal for Gate 2 / Rule 20 checks."""
        self._last_of_signal = signal

    def set_order_flow_analyzer(self, analyzer) -> None:
        """Set the order flow analyzer for Gate 2 / Rule 20."""
        self._order_flow = analyzer

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
        if pnl < 0:
            self._consecutive_losses += 1
            self._last_loss_time = time.time()
            self._save_state()
            if self._consecutive_losses >= CONFIG.risk.max_consecutive_losses:
                self._trip_circuit_breaker(
                    f"consecutive loss streak: {self._consecutive_losses}"
                )
        elif pnl > 0:
            # C-06 FIX: A single small win should not erase a long loss streak.
            # Decrement by 1 instead of resetting to 0, so recovery from e.g.
            # 4 consecutive losses requires multiple wins, not just one dust win.
            self._consecutive_losses = max(0, self._consecutive_losses - 1)
            self._save_state()
        # C2-09 FIX: pnl == 0.0 (breakeven) — no change to streak.
        # A breakeven trade is neither a win nor a loss and should not
        # decrement the loss streak counter.

    def record_equity_snapshot(self, equity: float) -> None:
        """Record equity for equity curve circuit breaker analysis."""
        self._equity_history.append(equity)
        # Cap history
        max_len = CONFIG.risk.equity_curve_ma_period * 3
        if len(self._equity_history) > max_len:
            self._equity_history = self._equity_history[-max_len:]

        # Check equity curve health
        ma_period = CONFIG.risk.equity_curve_ma_period
        if len(self._equity_history) >= ma_period:
            ma = sum(self._equity_history[-ma_period:]) / ma_period
            std = (sum((x - ma) ** 2 for x in self._equity_history[-ma_period:]) / ma_period) ** 0.5

            pause_threshold = ma - std * CONFIG.risk.equity_curve_pause_stddev

            if equity < pause_threshold:
                if not self._equity_curve_paused:
                    self._equity_curve_paused = True
                    audit(risk_log,
                          f"Equity curve circuit breaker: PAUSED (equity ${equity:.0f} < {pause_threshold:.0f})",
                          action="equity_curve_cb", result="PAUSED")
            elif equity < ma:
                if not self._equity_curve_halved:
                    self._equity_curve_halved = True
                    audit(risk_log,
                          f"Equity curve: HALVED sizing (equity ${equity:.0f} < MA ${ma:.0f})",
                          action="equity_curve_cb", result="HALVED")
                # Reset pause if we're above pause threshold
                if self._equity_curve_paused:
                    self._equity_curve_paused = False
                    audit(risk_log, "Equity curve: un-paused (above pause threshold)",
                          action="equity_curve_cb", result="UNPAUSED")
            else:
                # Above MA = healthy
                if self._equity_curve_halved or self._equity_curve_paused:
                    self._equity_curve_halved = False
                    self._equity_curve_paused = False
                    audit(risk_log, "Equity curve: restored to full sizing",
                          action="equity_curve_cb", result="RESTORED")

    def check_drawdown_recovery(self, current_dd_pct: float) -> None:
        """Check if we should enter/exit drawdown recovery mode."""
        max_dd = CONFIG.risk.max_drawdown_pct
        recovery_threshold = max_dd * 0.7  # enter recovery at 70% of max DD
        exit_threshold = max_dd * 0.3       # exit when DD drops to 30% of max

        if not self._in_drawdown_recovery and current_dd_pct >= recovery_threshold:
            self._in_drawdown_recovery = True
            self._recovery_start_dd = current_dd_pct
            audit(risk_log,
                  f"Drawdown recovery mode ACTIVATED (DD={current_dd_pct:.1f}%)",
                  action="dd_recovery", result="ACTIVATED")
        elif self._in_drawdown_recovery and current_dd_pct <= exit_threshold:
            self._in_drawdown_recovery = False
            audit(risk_log,
                  f"Drawdown recovery mode DEACTIVATED (DD={current_dd_pct:.1f}%)",
                  action="dd_recovery", result="DEACTIVATED")

    @property
    def equity_curve_size_multiplier(self) -> float:
        """Get current equity curve sizing multiplier."""
        if self._equity_curve_paused:
            return 0.0  # no trading
        if self._equity_curve_halved:
            return 0.5
        return 1.0

    @property
    def in_drawdown_recovery(self) -> bool:
        return self._in_drawdown_recovery

    def update_price_history(self, symbol: str, price: float) -> None:
        """Record a price point for rolling correlation calculation."""
        if symbol not in self._price_history:
            self._price_history[symbol] = []
        self._price_history[symbol].append(price)
        if len(self._price_history[symbol]) > 100:
            self._price_history[symbol] = self._price_history[symbol][-100:]

    def evaluate(self, idea: TradeIdea, atr: Optional[float] = None, live_equity: Optional[float] = None, max_position_usd: Optional[float] = None, live_open_count: Optional[int] = None) -> RiskCheck:
        """
        Run all 23 pre-trade checks (16 in-engine + #17 liquidity + #18 macro + #19 MTF + #20 PCA + #21 VaR + #22 taker 3-bar + #23 bid dominance).
        Returns RiskCheck with APPROVED or REJECTED.
        Pass atr= for volatility guard check.
        Pass live_equity= to override paper equity for sizing in LIVE mode.
        Pass max_position_usd= to cap sizing at execution limit (e.g. micro-test $10).
        Pass live_open_count= to override paper open position count in LIVE mode.
        """
        with self._lock:
            return self._evaluate_locked(idea, atr, live_equity=live_equity, max_position_usd=max_position_usd, live_open_count=live_open_count)

    def _evaluate_locked(self, idea: TradeIdea, atr: Optional[float] = None, live_equity: Optional[float] = None, max_position_usd: Optional[float] = None, live_open_count: Optional[int] = None) -> RiskCheck:
        self._total_checks += 1
        passed: list[str] = []
        failed: list[str] = []
        is_manual = getattr(idea, 'source', '') == 'manual'

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

        # LIVE FIX: In LIVE mode, use actual exchange equity for sizing
        # instead of paper portfolio equity.  This prevents sizing $2K
        # positions against $10K paper when the real account has $50.
        sizing_equity = state.equity_usd
        if live_equity is not None and live_equity > 0:
            sizing_equity = live_equity

        position_usd = sizing_equity * (CONFIG.risk.max_position_pct / 100.0)

        # Fixed-fractional risk sizing: size by stop distance, not flat notional.
        # risk_budget = equity * max_position_pct (the max we're willing to lose)
        # position_usd = risk_budget / (stop_distance / entry_price)
        # The notional cap (20%) is enforced by check #2 below, NOT here.
        # This separation gives the check real authority: if a tight stop would
        # produce an oversized position, the check catches it and caps it.
        stop_distance_pct = abs(idea.entry_price - idea.stop_loss) / idea.entry_price if idea.entry_price > 0 else 0
        # C2-24 FIX: Floor at 0.1% to prevent near-zero stop distances from
        # producing astronomically large intermediate position values.
        stop_distance_pct = max(stop_distance_pct, 0.001)
        uncapped_position_usd = position_usd  # fallback: flat notional
        if stop_distance_pct > 0:
            risk_budget = sizing_equity * (CONFIG.risk.max_position_pct / 100.0)
            # Per-strategy-type risk budget scaling
            _st = getattr(idea, 'strategy_type', 'swing')
            st_risk_pct = CONFIG.strategy_types.get_max_risk_pct(_st)
            risk_budget = sizing_equity * (st_risk_pct / 100.0)
            uncapped_position_usd = risk_budget / stop_distance_pct
            position_usd = uncapped_position_usd

        # Apply execution cap (e.g., micro-test $10 limit).
        # The risk engine must evaluate the ACTUAL position size that will
        # be executed, not the theoretical uncapped size.  Without this,
        # a $10 micro-test position on a $100 account (10% exposure) gets
        # rejected because the theoretical size (e.g., $43) exceeds 20%.
        if max_position_usd is not None and max_position_usd > 0:
            position_usd = min(position_usd, max_position_usd)
            uncapped_position_usd = min(uncapped_position_usd, max_position_usd)

        # C2-11 FIX: Compute macro size multiplier BEFORE the notional cap and
        # check #2, so the capped value reflects the macro-adjusted size.
        # The multiplier is applied defensively: only reductions (<=1.0) are
        # applied pre-check; any multiplier >1.0 is clamped to 1.0 here.
        _macro_size_mult = 1.0
        _macro_ctx = None
        if self._macro_provider is not None:
            try:
                _macro_ctx = self._macro_provider.get_context(symbol=idea.asset)
                self._last_macro_size_multiplier = _macro_ctx.size_multiplier
                if _macro_ctx.risk_state == "REDUCE" and _macro_ctx.size_multiplier < 1.0:
                    _macro_size_mult = _macro_ctx.size_multiplier
                elif _macro_ctx.size_multiplier > 1.0:
                    _macro_size_mult = 1.0  # never increase pre-cap
            except Exception:
                pass  # macro check #18 below handles errors with fail-closed

        # C2-29 FIX: Apply regime multiplier BEFORE the notional cap, so the cap
        # always has final authority.  Previously the multiplier was applied after
        # the cap, allowing STRONG_TREND_UP (1.5x) to push position 50% above it.
        # ── Apply regime-aware position size adjustment (Feature #3) ──
        # C2-36: set_regime is called by the scan/analyze pipeline before evaluate().
        # Here we just read the current regime params without mutating state.
        regime_params = self.get_regime_adjusted_params(self._current_regime, self._current_vol_state)
        regime_mult = regime_params.get("position_size_mult", 1.0)
        if regime_mult != 1.0:
            position_usd *= regime_mult

        # Session-aware position sizing: reduce size in low-liquidity sessions.
        # Only reductions (mult < 1.0) applied pre-cap; never increases.
        try:
            from bot.core.session_aware import get_current_session
            _session = get_current_session()
            _session_mult = _session.size_multiplier
            if _session_mult < 1.0:
                position_usd *= _session_mult
        except Exception:
            pass  # fail-open: session check must never block risk evaluation

        # Equity curve circuit breaker sizing
        _eq_mult = self.equity_curve_size_multiplier
        if _eq_mult < 1.0:
            if _eq_mult <= 0:
                failed.append("EQUITY_CURVE: trading paused — equity below 2σ of MA")
            else:
                position_usd *= _eq_mult

        # Drawdown recovery mode: require higher confidence, reduce size
        if self._in_drawdown_recovery:
            if idea.confidence < CONFIG.risk.drawdown_recovery_conf_min:
                failed.append(f"DD_RECOVERY: confidence {idea.confidence:.2f} < {CONFIG.risk.drawdown_recovery_conf_min} (recovery mode)")
            position_usd *= CONFIG.risk.drawdown_recovery_size_mult

        # C2-11: Apply macro reduction pre-cap
        if _macro_size_mult < 1.0:
            position_usd *= _macro_size_mult

        # C-03 FIX: Cap position_usd at max_notional BEFORE check #2 runs.
        # The fixed-fractional formula (risk_budget / stop_distance) routinely
        # produces notional sizes far exceeding the risk budget itself (e.g.,
        # 13% budget / 3% stop = 433% of equity).  The cap reduces the actual
        # position to max_position_pct of equity — meaning the per-trade risk
        # is smaller than the budget (conservative), but the notional exposure
        # stays bounded.  Check #2 then verifies the CAPPED value against
        # max_symbol_exposure_pct (a wider limit), giving it real authority to
        # reject only when exposure is genuinely dangerous.
        max_notional_usd = sizing_equity * (CONFIG.risk.max_position_pct / 100.0)
        if max_notional_usd > 0 and position_usd > max_notional_usd:
            position_usd = max_notional_usd

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
            # 1b. Warning rate circuit breaker — infrastructure health
            if self._warning_rate_tripped:
                failed.append(
                    f"WARNING_RATE_BREAKER: infrastructure warnings firing too "
                    f"frequently (key={self._warning_rate_trip_key!r})")
            else:
                passed.append("WARNING_RATE_BREAKER: OK")
        except Exception as exc:
            failed.append(f"WARNING_RATE_BREAKER: evaluation error ({exc})")

        try:
            # 2. Position size — enforces notional cap (the check has real authority)
            if sizing_equity <= 0:
                failed.append("EQUITY: zero or negative equity")
            else:
                notional_pct = (position_usd / sizing_equity * 100)
                max_notional_pct = CONFIG.risk.max_symbol_exposure_pct  # 20% default
                # C2-41 FIX: Use floating-point epsilon, not 1% overage tolerance
                if notional_pct < max_notional_pct + 1e-9:  # floating-point tolerance only
                    passed.append(f"POSITION_SIZE: notional {notional_pct:.1f}% <= {max_notional_pct}%")
                else:
                    failed.append(f"POSITION_SIZE: notional {notional_pct:.1f}% exceeds {max_notional_pct}% cap")
        except Exception as exc:
            failed.append(f"POSITION_SIZE: evaluation error ({exc})")

        daily_loss_pct = 0.0
        try:
            # 3. Daily loss (realized + unrealized) — measured against equity, not free cash
            loss_base = min(sizing_equity, state.equity_usd) if sizing_equity > 0 and state.equity_usd > 0 else max(sizing_equity, state.equity_usd)
            daily_loss_pct = abs(state.daily_pnl / loss_base * 100) if loss_base > 0 else 0
            self._last_known_daily_loss_pct = daily_loss_pct  # C2-42: persist for fallback
            if state.daily_pnl < 0 and daily_loss_pct >= CONFIG.risk.max_daily_loss_pct:
                failed.append(f"DAILY_LOSS: {daily_loss_pct:.1f}% >= {CONFIG.risk.max_daily_loss_pct}%")
                # C-05 FIX: trip circuit breaker AND reject the CURRENT trade
                self._trip_circuit_breaker("daily loss limit breached")
                if "CIRCUIT_BREAKER: tripped during evaluation" not in failed:
                    failed.append("CIRCUIT_BREAKER: tripped during evaluation — current trade rejected")
            else:
                passed.append(f"DAILY_LOSS: {daily_loss_pct:.1f}% OK")
        except Exception as exc:
            failed.append(f"DAILY_LOSS: evaluation error ({exc})")
            # C2-42 FIX: Use last known value instead of zeroing, so we don't
            # mask an actual loss that was previously computed.
            daily_loss_pct = self._last_known_daily_loss_pct

        try:
            # 4. Drawdown
            if state.max_drawdown_pct >= CONFIG.risk.max_drawdown_pct:
                failed.append(f"DRAWDOWN: {state.max_drawdown_pct:.1f}% >= {CONFIG.risk.max_drawdown_pct}%")
                # C-05 FIX: trip circuit breaker AND reject the CURRENT trade
                self._trip_circuit_breaker("max drawdown breached")
                if "CIRCUIT_BREAKER: tripped during evaluation" not in failed:
                    failed.append("CIRCUIT_BREAKER: tripped during evaluation — current trade rejected")
            else:
                passed.append(f"DRAWDOWN: {state.max_drawdown_pct:.1f}% OK")
        except Exception as exc:
            failed.append(f"DRAWDOWN: evaluation error ({exc})")

        try:
            # 5. Open positions limit
            # LIVE FIX: use live executor's position count when available
            effective_open = live_open_count if live_open_count is not None else state.open_positions
            if effective_open >= CONFIG.risk.max_open_positions:
                failed.append(f"MAX_POSITIONS: {effective_open} >= {CONFIG.risk.max_open_positions}")
            else:
                passed.append(f"OPEN_POSITIONS: {effective_open} OK")
        except Exception as exc:
            failed.append(f"MAX_POSITIONS: evaluation error ({exc})")

        is_limit = getattr(idea, 'order_type', '') == 'limit'
        if is_manual:
            passed.append("RISK_REWARD: skipped (manual trade)")
        elif is_limit:
            # User explicitly confirmed entry/SL/TP levels — don't re-reject
            rr = idea.risk_reward_ratio
            passed.append(f"RISK_REWARD: {rr} OK (limit order, user-confirmed)")
        else:
            try:
                # 6. Risk-reward ratio (0.01 tolerance for float rounding at boundary)
                rr = idea.risk_reward_ratio
                _st = getattr(idea, 'strategy_type', 'swing')
                min_rr = CONFIG.strategy_types.get_min_rr(_st)
                if rr < min_rr - 0.01:
                    failed.append(f"RISK_REWARD: {rr:.2f} < {min_rr:.1f} minimum ({_st})")
                else:
                    passed.append(f"RISK_REWARD: {rr:.2f} OK (min {min_rr:.1f} for {_st})")
            except Exception as exc:
                failed.append(f"RISK_REWARD: evaluation error ({exc})")

        try:
            # 6b. Leverage-aware margin risk cap
            # SL distance % × leverage must not exceed max_margin_risk_pct
            leverage = CONFIG.exchange.default_leverage
            if leverage > 1 and idea.entry_price > 0:
                sl_dist_pct = abs(idea.entry_price - idea.stop_loss) / idea.entry_price * 100
                margin_risk = sl_dist_pct * leverage
                max_margin_risk = CONFIG.risk.max_margin_risk_pct
                if margin_risk > max_margin_risk + 0.5:  # small tolerance
                    # Dynamic leverage: reduce leverage to fit within cap
                    if getattr(CONFIG.exchange, 'dynamic_leverage_enabled', False) and sl_dist_pct > 0:
                        safe_lev = int(max_margin_risk / sl_dist_pct)
                        min_lev = getattr(CONFIG.exchange, 'min_leverage', 2)
                        safe_lev = max(min_lev, safe_lev)
                        new_margin = sl_dist_pct * safe_lev
                        passed.append(
                            f"MARGIN_RISK: reduced leverage {leverage}x→{safe_lev}x "
                            f"(SL {sl_dist_pct:.1f}% × {safe_lev}x = {new_margin:.1f}% ≤ {max_margin_risk:.1f}%)")
                        # Store adjusted leverage on idea for executor
                        try:
                            idea._adjusted_leverage = safe_lev
                        except Exception:
                            pass
                    else:
                        failed.append(f"MARGIN_RISK: {margin_risk:.1f}% (SL {sl_dist_pct:.1f}% × {leverage}x) exceeds {max_margin_risk:.1f}% cap")
                else:
                    passed.append(f"MARGIN_RISK: {margin_risk:.1f}% OK (SL {sl_dist_pct:.1f}% × {leverage}x)")
            else:
                passed.append("MARGIN_RISK: no leverage, skipped")
        except Exception as exc:
            failed.append(f"MARGIN_RISK: evaluation error ({exc})")

        if is_manual:
            passed.append("CONFIDENCE: skipped (manual trade)")
        else:
            try:
                # 7. Confidence threshold
                if idea.confidence < CONFIG.risk.min_confidence:
                    failed.append(f"CONFIDENCE: {idea.confidence} < {CONFIG.risk.min_confidence} minimum")
                else:
                    passed.append(f"CONFIDENCE: {idea.confidence} OK")
            except Exception as exc:
                failed.append(f"CONFIDENCE: evaluation error ({exc})")

        # RC-AUD-008: correlation is a portfolio-safety check, not a signal-opinion
        # check — it binds for manual trades too (a manual entry can still
        # over-concentrate a correlation group).
        try:
            # 8. Correlation / concentration check
            corr_result = self._check_correlation(idea)
            if corr_result:
                failed.append(corr_result)
            else:
                passed.append("CORRELATION: no concentrated exposure")
        except Exception as exc:
            failed.append(f"CORRELATION: evaluation error ({exc})")

        # RC-AUD-008: the loss-streak guard exists to stop revenge trading, which
        # manifests most in manual entries — so it must bind for manual trades too.
        try:
            # 9. Consecutive loss streak
            # C2-35 FIX: soft limit derived from config, not hardcoded to 3.
            # Always stays 2 below the hard circuit-breaker limit.
            soft_limit = max(2, CONFIG.risk.max_consecutive_losses - 2)
            if self._consecutive_losses >= soft_limit:
                failed.append(f"LOSS_STREAK: {self._consecutive_losses} consecutive losses (>= {soft_limit})")
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
            # Limit orders get 2x timeout — user needs time to review and set price
            is_limit_order = getattr(idea, 'order_type', '') == 'limit'
            max_age = CONFIG.risk.stale_data_max_age_seconds * (2 if is_limit_order else 1)
            data_age = (datetime.now(UTC) - idea.timestamp).total_seconds()
            if data_age > max_age:
                failed.append(f"STALE_DATA: idea is {data_age:.0f}s old > {max_age}s max")
            else:
                passed.append(f"STALE_DATA: {data_age:.0f}s old OK")
        except Exception as exc:
            failed.append(f"STALE_DATA: evaluation error ({exc})")

        # RC-AUD-008: cooldown-after-loss also binds for manual trades (anti-revenge).
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

        margin_equiv_position_usd = 0.0

        try:
            # 14. Portfolio exposure limit (mark-to-market)
            # C2-10 FIX: get_position_value() returns margin + unrealized PnL (per C-01),
            # so we must normalize position_usd to margin-equivalent too.
            # Otherwise a 5x leveraged new position overstates exposure by 5x.
            leverage = getattr(CONFIG.exchange, 'default_leverage', 1) or 1
            margin_equiv_position_usd = position_usd / leverage
            open_value = self._portfolio.get_position_value()
            exposure_pct = (open_value / sizing_equity * 100) if sizing_equity > 0 else 0
            new_exposure = exposure_pct + (margin_equiv_position_usd / sizing_equity * 100 if sizing_equity > 0 else 0)
            if new_exposure > CONFIG.risk.max_portfolio_exposure_pct:
                failed.append(f"PORTFOLIO_EXPOSURE: {new_exposure:.1f}% > {CONFIG.risk.max_portfolio_exposure_pct}%")
            else:
                passed.append(f"PORTFOLIO_EXPOSURE: {new_exposure:.1f}% OK")
        except Exception as exc:
            failed.append(f"PORTFOLIO_EXPOSURE: evaluation error ({exc})")

        try:
            # 15. Per-symbol exposure limit (mark-to-market)
            symbol_value = self._portfolio.get_position_value(asset=idea.asset)
            new_symbol_value = symbol_value + margin_equiv_position_usd
            symbol_exposure_pct = (new_symbol_value / sizing_equity * 100) if sizing_equity > 0 else 0
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
            # Meme coins get a HIGHER threshold (10%) to avoid false rejections
            # on inherently volatile tokens; large-caps use the default (7%).
            symbol = getattr(idea, "asset", "") or ""
            meme_group = _CORRELATION_GROUPS.get(f"{symbol}/USDT" if "/" not in symbol else symbol)
            is_meme = meme_group == "MEME"
            vol_threshold = max(CONFIG.risk.volatility_guard_atr_pct, 10.0) if is_meme else CONFIG.risk.volatility_guard_atr_pct

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
            # NOTE: size_multiplier was already applied pre-cap (C2-11 fix).
            # This check only records pass/fail — no further size modification.
            macro_checked = False
            if self._macro_provider is not None:
                try:
                    ctx = _macro_ctx if _macro_ctx is not None else self._macro_provider.get_context(symbol=idea.asset)
                    self._last_macro_size_multiplier = ctx.size_multiplier
                    if ctx.risk_state == "BLOCK_NEW_ENTRIES":
                        failed.append(f"MACRO_EVENT: BLOCK — {ctx.explanation}")
                    elif ctx.risk_state == "REDUCE":
                        passed.append(f"MACRO_EVENT: REDUCE (size×{ctx.size_multiplier}) — {ctx.explanation}")
                        # C2-11: size reduction already applied pre-cap above
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

        # 22. Taker 3-bar gate (Gate 2) — fail-open if no order flow analyzer
        try:
            if self._order_flow is not None:
                direction_str = idea.direction.value if hasattr(idea.direction, 'value') else str(idea.direction)
                gate2 = self._order_flow.check_taker_3bar_gate(idea.asset, direction_str)
                if gate2["passed"]:
                    passed.append(f"TAKER_3BAR: {gate2['reason']}")
                else:
                    failed.append(f"TAKER_3BAR: {gate2['reason']}")
            else:
                if self._order_flow is None or self._last_of_signal is None:
                    risk_log.warning("Order flow check #22 (taker gate) skipped — no signal")
                passed.append("TAKER_3BAR: skipped (no order flow analyzer)")
        except Exception as exc:
            failed.append(f"TAKER_3BAR: evaluation error ({exc})")

        # 23. Bid dominance gate (Rule 20) — bid:ask >= 2:1 for LONG
        try:
            if self._order_flow is not None and self._last_of_signal is not None:
                direction_str = idea.direction.value if hasattr(idea.direction, 'value') else str(idea.direction)
                gate20 = self._order_flow.check_bid_dominance(self._last_of_signal, direction_str)
                if gate20["passed"]:
                    passed.append(f"BID_DOMINANCE: {gate20['reason']}")
                else:
                    failed.append(f"BID_DOMINANCE: {gate20['reason']}")
            else:
                if self._order_flow is None or self._last_of_signal is None:
                    risk_log.warning("Order flow check #23 (bid dominance) skipped — no signal")
                passed.append("BID_DOMINANCE: skipped (no order flow data)")
        except Exception as exc:
            failed.append(f"BID_DOMINANCE: evaluation error ({exc})")

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
            # C2-45: deque(maxlen=50) auto-prunes, no manual check needed

        check = RiskCheck(
            trade_id=idea.id,
            verdict=verdict,
            position_size_usd=round(position_usd, 2),
            position_pct=round(
                (position_usd / sizing_equity * 100) if sizing_equity > 0 else 0, 2
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
        except Exception as e:
            risk_log.debug("Portfolio snapshot unavailable in get_recommended_size: %s", e)
            return 0.0

        if equity <= 0:
            return 0.0

        # Need at least 10 trades for meaningful stats
        if len(closed) < 10:
            # Fallback: fixed-fractional
            return equity * (CONFIG.risk.max_position_pct / 100.0)

        wins = [t for t in closed if t.pnl > 0]
        losses = [t for t in closed if t.pnl < 0]
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
        "CHOP": {"position_size_mult": 0.5, "cooldown_mult": 2.0, "stop_width_mult": 1.0},
        "STRONG_TREND_UP": {"position_size_mult": 1.5, "cooldown_mult": 0.5, "stop_width_mult": 1.0},
        "STRONG_TREND_DOWN": {"position_size_mult": 1.5, "cooldown_mult": 0.5, "stop_width_mult": 1.0},
        "TREND_UP": {"position_size_mult": 1.2, "cooldown_mult": 0.7, "stop_width_mult": 1.0},
        "TREND_DOWN": {"position_size_mult": 1.2, "cooldown_mult": 0.7, "stop_width_mult": 1.0},
        "EXPANSION": {"position_size_mult": 1.3, "cooldown_mult": 0.5, "stop_width_mult": 0.9},
        "HIGH_VOLATILITY": {"position_size_mult": 0.3, "cooldown_mult": 1.0, "stop_width_mult": 1.5},
        "RANGING": {"position_size_mult": 0.7, "cooldown_mult": 1.5, "stop_width_mult": 1.0},
        "RANGE": {"position_size_mult": 0.7, "cooldown_mult": 1.5, "stop_width_mult": 1.0},
    }

    _DEFAULT_MULTIPLIERS: dict[str, float] = {
        "position_size_mult": 1.0, "cooldown_mult": 1.0, "stop_width_mult": 1.0,
    }

    def set_regime(self, regime: str, volatility_state: str) -> None:
        """C2-36 FIX: Explicit setter for regime state.
        Called once per evaluation cycle; subsequent get calls are pure."""
        self._current_regime = regime
        self._current_vol_state = volatility_state

    def get_regime_adjusted_params(self, regime: str, volatility_state: str) -> dict:
        """Return adjusted risk parameter multipliers based on market regime.

        Parameters:
            regime: market regime string (e.g. "CHOPPY", "STRONG_TREND_UP")
            volatility_state: volatility descriptor (e.g. "HIGH", "NORMAL", "LOW")

        Returns:
            dict with keys: position_size_mult, cooldown_mult, stop_width_mult

        C2-36 FIX: Pure accessor — does NOT mutate _current_regime/_current_vol_state.
        Use set_regime() to update state explicitly.
        """
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

        Statistical convention: sample variance (ddof=1) throughout this module (C2-46).
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

        # Compute std devs (C2-46 FIX: sample variance, ddof=1)
        stddevs = []
        for i, r in enumerate(trimmed):
            var = sum((x - means[i]) ** 2 for x in r) / (n_periods - 1)
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
                # C2-46 FIX: sample covariance (ddof=1)
                cov = sum(
                    (trimmed[i][k] - means[i]) * (trimmed[j][k] - means[j])
                    for k in range(n_periods)
                ) / (n_periods - 1)
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

        H-05 LIMITATION: This VaR uses per-trade returns (individual trade P&L /
        notional) as a proxy for portfolio return volatility. This does NOT capture
        cross-asset correlations, concurrent position overlap, or true portfolio-level
        return distribution. A proper portfolio VaR would require time-series of
        daily portfolio mark-to-market returns and a covariance matrix across held
        assets. The current approach overstates diversification benefit and may
        understate tail risk for concentrated portfolios. Do not rely on this VaR
        as a standalone risk metric — it is a rough directional guard only.
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

        # C2-28 FIX: z-score lookup table replaces erfc formula that produced
        # wildly wrong values (~0.007 at 99% instead of correct 2.326).
        _VAR_Z_SCORES = {0.90: 1.282, 0.95: 1.645, 0.99: 2.326, 0.999: 3.090}
        if confidence_level in _VAR_Z_SCORES:
            z_score = _VAR_Z_SCORES[confidence_level]
        else:
            # Nearest-key fallback for non-standard confidence levels
            nearest = min(_VAR_Z_SCORES.keys(), key=lambda k: abs(k - confidence_level))
            z_score = _VAR_Z_SCORES[nearest]
            risk_log.warning(
                "No exact z-score for confidence %.4f — using nearest %.3f (z=%.3f)",
                confidence_level, nearest, z_score,
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

        # V2: Rolling return correlation check
        # If we have price history, compute actual pairwise correlation
        # with existing open positions
        try:
            if hasattr(self, '_price_history') and idea.asset in self._price_history:
                for tid, pos in self._portfolio._positions.items():
                    if pos.asset == idea.asset:
                        continue
                    if pos.asset in self._price_history:
                        prices_new = self._price_history[idea.asset]
                        prices_existing = self._price_history[pos.asset]
                        min_len = min(len(prices_new), len(prices_existing), 30)
                        if min_len >= 10:
                            import numpy as np
                            r1 = np.diff(prices_new[-min_len:]) / prices_new[-min_len:-1]
                            r2 = np.diff(prices_existing[-min_len:]) / prices_existing[-min_len:-1]
                            corr = float(np.corrcoef(r1, r2)[0, 1])
                            if abs(corr) > CONFIG.risk.max_correlation:
                                # Same direction + high correlation = reject
                                if (idea.direction.value == pos.direction.value and corr > CONFIG.risk.max_correlation):
                                    return f"CORRELATION_V2: {idea.asset} corr={corr:.2f} with {pos.asset} (>{CONFIG.risk.max_correlation})"
        except Exception as _corr_exc:
            logger.warning("Correlation v2 check failed (fail-open): %s", _corr_exc)

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
        """Persist safety-critical state to disk. Called on every state change.
        C2-34: delegates to combined saver when wired, for atomic consistency."""
        if self._combined_saver is not None:
            try:
                self._combined_saver()
            except Exception as exc:
                audit(risk_log, f"Combined save failed, falling back to individual: {exc}",
                      action="save_state", result="FALLBACK")
                self._save_state_individual()
        else:
            self._save_state_individual()

    def _save_state_individual(self) -> None:
        """Write risk state to its own file (legacy path or fallback)."""
        try:
            os.makedirs(os.path.dirname(self._state_file) or ".", exist_ok=True)
            data = self._export_state_dict()
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

    def _export_state_dict(self) -> dict:
        """C2-34: Extract risk state as a dict without writing to disk."""
        return {
            "circuit_open": self._circuit_open,
            "consecutive_losses": self._consecutive_losses,
            "last_loss_time": self._last_loss_time,
            "circuit_breaker_trips": self._circuit_breaker_trips,
            "saved_at": datetime.now(UTC).isoformat(),
        }

    def _load_from_state_dict(self, data: dict) -> None:
        """C2-34: Restore risk state from a dict (no file I/O).
        Uses fail-closed semantics matching _load_state."""
        self._circuit_open = data.get("circuit_open", False)
        self._consecutive_losses = data.get("consecutive_losses", 0)
        self._last_loss_time = data.get("last_loss_time")
        self._circuit_breaker_trips = data.get("circuit_breaker_trips", 0)
        if self._circuit_open:
            audit(risk_log, "Circuit breaker state restored from combined state: ACTIVE",
                  action="state_restore", result="LOADED")

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
