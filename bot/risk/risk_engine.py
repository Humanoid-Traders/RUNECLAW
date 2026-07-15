"""
RUNECLAW Risk Engine -- FAIL-CLOSED pre-trade gatekeeper.

23 independent pre-trade checks. ANY failure = REJECTED. No overrides.
Design: if a check cannot be evaluated, the trade is REJECTED (fail-closed).

Note: Check #17 (liquidity guard) lives in engine.py via OrderFlowAnalyzer,
not in this module.  It is fail-open (no data = pass) by design.

Units (audit V7, F-3) — RECONCILED:
  * Canonical unit: ``position_size_usd`` is MARGIN. The portfolio
    (``open_position``) and the LIVE executor both commit it as collateral and
    derive exchange notional = margin * leverage; ``get_position_value()`` also
    returns margin. The exposure checks (#2/#14/#15) therefore compare margin to
    margin directly — they no longer divide by leverage (that earlier
    normalization treated ``position_usd`` as a notional and understated each new
    position's committed margin by the leverage factor, i.e. the guards were ~5x
    too lenient). So these caps are margin/equity caps, which is also what makes
    the micro design viable ($100 margin -> $500 notional at 5x).
  * Loss-risk checks reason about NOTIONAL: the Portfolio VaR (#21) sums open
    position notionals and now adds the proposed position's notional
    (margin * leverage), not its margin, so it no longer mixes units.
  * The executor enforces a hard notional ceiling (margin * max_leverage) as a
    backstop, and every evaluation logs ``leverage`` + ``approx_notional_usd`` so
    the margin->notional relationship is explicit in the audit trail.

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
import math
import os
import threading
import time
from collections import deque
from datetime import datetime
from bot.compat import UTC
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    # Import only for type-checking so the forward-ref annotations on __init__
    # resolve (no runtime import cycle). These are the real types behind the
    # "PortfolioTracker"/"MacroCalendar" string annotations below.
    from bot.macro.calendar import MacroCalendar
    from bot.risk.portfolio import PortfolioTracker

from dataclasses import dataclass

from bot.config import CONFIG
from bot.utils.durable_io import fsync_dir
from bot.utils.logger import audit, risk_log
from bot.utils.models import RiskCheck, RiskVerdict, TradeIdea


# RC-AUD-007: explicit VaR result type, replacing the former magic-tuple
# sentinels ((-1,-1) = skip / (0.0,100.0) = zero-equity reject).  The dual-meaning
# tuple was correct but fragile: a future edit treating current_var==0.0 as "no
# data" could silently disable the VaR check.  An explicit status makes the
# skip-vs-evaluate decision unambiguous at the call site.
class VarStatus:
    """Explicit VaR evaluation status."""
    SKIP = "SKIP"   # insufficient data (<5 closed trades) — caller passes the check
    OK = "OK"       # VaR computed — caller compares proposed_var_pct against the limit


@dataclass(frozen=True)
class VarResult:
    """Result of a portfolio-VaR computation.

    status is one of VarStatus.{SKIP, OK}.  The zero-equity "max risk = reject"
    case is encoded as status=OK with proposed_var_pct=100.0 so the call site's
    single ``proposed_var_pct > max_var`` comparison stays authoritative (a 100%
    VaR always exceeds the configured limit and therefore rejects).
    """
    status: str
    current_var_pct: float
    proposed_var_pct: float


# RC-AUD-011: conservative default size-reduction multiplier applied when a
# size-reduction provider (macro / session) raises.  Failing toward SAFETY means
# we shrink the position rather than silently leaving it full-size.
_PROVIDER_FALLBACK_SIZE_MULT = 0.5

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

# Shared bucket for any symbol NOT in _CORRELATION_GROUPS. Pooling unmapped
# symbols here (instead of treating each as its own group) stops a basket of
# alts from collectively dodging the per-group correlation cap.
_UNMAPPED_GROUP = "UNMAPPED_ALT"


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

    def __init__(self, portfolio: "PortfolioTracker", state_file: Optional[str] = None,  # noqa: F821
                 macro_calendar: Optional["MacroCalendar"] = None,  # noqa: F821
                 macro_provider: Optional[Any] = None,
                 order_flow_analyzer: Optional[Any] = None) -> None:  # noqa: F821
        self._portfolio = portfolio
        self._circuit_open = False
        self._consecutive_losses = 0
        self._last_loss_time: Optional[float] = None  # epoch seconds
        # Re-entry cooldown ledger: last REAL fill time per symbol (epoch/sim
        # seconds), stamped by note_symbol_entry() at the actual open. Read by
        # the REENTRY_COOLDOWN check in _evaluate_locked. In-memory only (a
        # restart clears it — fail-open for a short-horizon churn guard). Stays
        # empty when REENTRY_COOLDOWN_ENABLED is off, so behaviour is unchanged.
        self._last_entry_by_symbol: dict[str, float] = {}
        # Backtests pin this to the replayed bar time (epoch seconds) so
        # time-based guards measure SIMULATED elapsed time, not wall-clock —
        # a replay covers months of bars in seconds of wall time, so a
        # wall-clock cooldown-after-loss would otherwise latch the whole
        # remaining run after the first loss. Live code never sets this.
        self._sim_now: Optional[float] = None
        self._circuit_breaker_trips = 0
        # Why/when the breaker last tripped, so a DAILY-LOSS trip can auto-reset
        # at day rollover while drawdown/streak/manual trips stay manual.
        self._circuit_trip_cause: str = ""        # "daily_loss" | "drawdown" | "streak" | "manual"
        self._circuit_trip_day: str = ""          # UTC YYYY-MM-DD of the trip
        self._total_checks = 0
        self._total_rejections = 0
        # Gate telemetry + strangle-watchdog counters (see gate_stats() /
        # eval_stats()): per-gate pass/fail/skip buckets, cumulative
        # evaluated/approved totals, and when something last passed.
        self._gate_stats: dict[str, dict[str, int]] = {}
        self._eval_total = 0
        self._approved_total = 0
        self._last_approval_time: Optional[float] = None
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
        # Feature: Live-performance governor — rolling window of realized closed
        # trade PnLs. In-memory only (rebuilds after restart from live closes),
        # like _equity_history; the governor fails OPEN until min_samples accrue,
        # which is safe because it only ever REDUCES size, never grows it.
        self._realized_pnl_window: deque[float] = deque(maxlen=100_000)
        # LIVE account-level loss tracking. In pure-live mode the paper
        # portfolio is never updated (the exchange is the source of truth),
        # so the daily-loss and drawdown breakers — which read the paper
        # snapshot — could never trip on real losses. These accumulate the
        # LIVE realized PnL (fed by record_live_trade_result from the live
        # close callback) and the LIVE equity high-water mark, so the same
        # breakers protect a live account. UTC-day reset; in-memory (rebuild
        # after restart is safe — a fresh day starts flat).
        self._live_daily_pnl: float = 0.0
        self._live_daily_day: str = ""      # "YYYY-MM-DD" the accumulator is for
        self._live_equity_peak: float = 0.0  # high-water mark for live drawdown
        # Feature: Rolling return correlation (V2)
        # #49: (timestamp, price) points so cross-asset returns align on a common
        # time grid, not by list position. In-memory only (not persisted).
        self._price_history: dict[str, list[tuple[float, float]]] = {}
        # Round 7 Phase 1: forward-looking correlation cap. Approved-but-not-yet-
        # filled entries keyed by idea id → (correlation_group, direction, ts).
        # Registered by the caller (backtest/live) between risk approval and fill;
        # _check_correlation counts them alongside OPEN positions so a correlated
        # same-bar cluster can't all pass the per-group cap. Empty (and unused)
        # while correlation_forward_intents_enabled is off → byte-identical.
        self._pending_intents: dict[str, tuple[str, str, float]] = {}
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
    def circuit_trip_cause(self) -> str:
        """Why the breaker is currently open: 'daily_loss' | 'drawdown' |
        'streak' | 'manual' | '' (not tripped). Lets alerts state the real
        reason instead of guessing."""
        return self._circuit_trip_cause

    @property
    def last_known_daily_loss_pct(self) -> float:
        """Most recent computed daily-loss percentage (persists across a failed
        recompute). For truthful breaker alerts."""
        return self._last_known_daily_loss_pct

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

    def _refresh_warning_rate_breaker(self) -> None:
        """Time-based auto-clear for the warning-rate breaker.

        record_warning()'s inline clear only fires on a FRESH event of the tripped
        key — so a transient burst that simply STOPS would latch the breaker
        forever (nothing re-evaluates once the key goes quiet). Called on every
        evaluate(): prune events out of the window and clear the breaker when the
        tripped key's rate has genuinely subsided below threshold.
        """
        if not self._warning_rate_tripped:
            return
        cutoff = self._now() - self._warning_rate_window
        while self._warning_events and self._warning_events[0][0] < cutoff:
            self._warning_events.popleft()
        key = self._warning_rate_trip_key
        count = sum(1 for _, k in self._warning_events if k == key)
        if count <= self._warning_rate_threshold:
            self._warning_rate_tripped = False
            audit(risk_log,
                  f"Warning rate breaker auto-cleared: '{key}' rate subsided to {count}",
                  action="warning_rate_breaker", result="AUTO_CLEARED",
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

    def gate_stats(self) -> dict[str, dict]:
        """Per-gate pass/fail/skip counters accumulated over this process's
        evaluations — the evidence base for tuning gate thresholds."""
        return {k: dict(v) for k, v in sorted(
            getattr(self, "_gate_stats", {}).items())}

    def eval_stats(self) -> dict:
        """Cumulative idea-evaluation counters for the strangle watchdog:
        total evaluated, total approved, and when something last passed."""
        return {
            "evaluated": getattr(self, "_eval_total", 0),
            "approved": getattr(self, "_approved_total", 0),
            "last_approval_time": getattr(self, "_last_approval_time", None),
        }

    def streak_state(self) -> dict:
        """Loss-streak / probe posture for operator surfaces (/status,
        dashboard). probe_in describes the half-open recovery: seconds until
        a probe trade is allowed (0 = allowed now, None = gate not latched
        or probing disabled)."""
        soft = self._soft_loss_streak_limit(CONFIG.risk.max_consecutive_losses)
        latched = self._consecutive_losses >= soft
        probe_in = None
        probe_s = CONFIG.risk.loss_streak_probe_hours * 3600.0
        if latched and probe_s > 0 and self._last_loss_time is not None:
            probe_in = max(0.0, probe_s - (self._now() - self._last_loss_time))
        return {
            "consecutive_losses": self._consecutive_losses,
            "soft_limit": soft,
            "hard_limit": CONFIG.risk.max_consecutive_losses,
            "latched": latched,
            "probe_in_seconds": probe_in,
            "circuit_breaker_open": self._circuit_open,
        }

    @property
    def stats(self) -> dict:
        return {
            "total_checks": self._total_checks,
            "total_rejections": self._total_rejections,
            "circuit_breaker_trips": self._circuit_breaker_trips,
            "consecutive_losses": self._consecutive_losses,
        }

    def set_sim_time(self, when: "datetime") -> None:
        """Pin the engine's clock to a replayed bar timestamp (backtest only).

        While set, loss timestamps are recorded in simulated time and the
        cooldown-after-loss guard measures simulated elapsed seconds. Live
        code never calls this, so live behavior is byte-identical.
        """
        self._sim_now = when.timestamp()

    def _now(self) -> float:
        """Current time in epoch seconds — simulated bar time under backtest
        replay (see set_sim_time), wall-clock otherwise."""
        return self._sim_now if self._sim_now is not None else time.time()

    def record_trade_result(self, pnl: float) -> None:
        """Track consecutive losses for streak-based circuit breaker."""
        with self._lock:
            self._record_trade_result_locked(pnl)

    def record_live_trade_result(self, pnl: float) -> None:
        """Record a LIVE realized close into every account-level protection.

        The live close callback calls this instead of record_trade_result so
        that, in pure-live mode (paper portfolio never updated), the
        consecutive-loss breaker, live-performance governor, and equity
        throttle all see real outcomes — AND the daily-loss breaker gets a
        live daily-PnL accumulator to gate on (see the DAILY_LOSS check).
        UTC-day roll resets the accumulator. Never raises."""
        try:
            with self._lock:
                self._record_trade_result_locked(float(pnl))
                day = time.strftime("%Y-%m-%d", time.gmtime(int(self._now())))
                if day != self._live_daily_day:
                    self._live_daily_day = day
                    self._live_daily_pnl = 0.0
                self._live_daily_pnl += float(pnl)
        except Exception as exc:  # never let accounting break the close path
            risk_log.debug("record_live_trade_result skipped: %s", exc)

    def _record_trade_result_locked(self, pnl: float) -> None:
        # Live-performance governor: record EVERY realized close (win/loss/flat)
        # so the rolling window reflects the true recent win rate + net PnL.
        self._realized_pnl_window.append(float(pnl))
        if pnl < 0:
            self._consecutive_losses += 1
            self._last_loss_time = self._now()
            self._save_state()
            if self._consecutive_losses >= CONFIG.risk.max_consecutive_losses:
                self._trip_circuit_breaker(
                    f"consecutive loss streak: {self._consecutive_losses}",
                    cause="streak",
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
        max_dd = self._effective_max_drawdown_pct()
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
    def live_performance_size_multiplier(self) -> float:
        """Sizing multiplier from the live-performance governor.

        Scores the most-recent ``live_perf_window`` realized closed trades:
          - **1.0 (healthy)** — win rate above the reduce threshold AND the
            window is net-positive, OR fewer than ``live_perf_min_samples``
            trades so far (fail OPEN — never penalise a cold start).
          - **0.0 (pause)** — win rate at/below ``live_perf_pause_winrate`` AND
            the window is net-negative: the strategy is losing both often and on
            balance, so stop trading.
          - **live_perf_reduce_mult (reduce)** — otherwise underperforming
            (win rate at/below ``live_perf_reduce_winrate`` OR net-negative).

        Tighten-only: the return is always in [0.0, 1.0]. Caller applies it as a
        pre-cap size reduction (and treats 0.0 as a pause/reject), exactly like
        the equity-curve breaker. Fail-open on any error → 1.0.
        """
        try:
            window = CONFIG.risk.live_perf_window
            recent = list(self._realized_pnl_window)[-window:]
            if len(recent) < CONFIG.risk.live_perf_min_samples:
                return 1.0
            wins = sum(1 for p in recent if p > 0)
            win_rate = wins / len(recent)
            net = sum(recent)
            if win_rate <= CONFIG.risk.live_perf_pause_winrate and net < 0:
                return 0.0
            if win_rate <= CONFIG.risk.live_perf_reduce_winrate or net < 0:
                return CONFIG.risk.live_perf_reduce_mult
            return 1.0
        except Exception:
            return 1.0

    @property
    def equity_throttle_multiplier(self) -> float:
        """Continuous size multiplier from the rolling-PF equity throttle.

        Maps the profit factor of the most recent ``equity_throttle_window``
        realized closes to [floor_mult, 1.0] via a linear ramp (see
        bot/risk/equity_throttle.py). Fails OPEN to 1.0 below
        ``equity_throttle_min_samples``, when PF is undefined (no losses in
        the window), or on any error. Tighten-only; never pauses.
        """
        try:
            from bot.risk.equity_throttle import (
                rolling_profit_factor, throttle_multiplier)
            cfg = CONFIG.risk
            recent = list(self._realized_pnl_window)[-cfg.equity_throttle_window:]
            if len(recent) < cfg.equity_throttle_min_samples:
                return 1.0
            pf = rolling_profit_factor(recent)
            return throttle_multiplier(
                pf, pf_full=cfg.equity_throttle_pf_full,
                pf_floor=cfg.equity_throttle_pf_floor,
                floor_mult=cfg.equity_throttle_floor_mult)
        except Exception:
            return 1.0

    def equity_throttle_state(self) -> dict:
        """Read-only diagnostic snapshot of the equity throttle.

        Returns ``{enabled, samples, pf, multiplier, status}`` where status is
        ``OFF``, ``WARMUP``, ``OK`` (full size), or ``THROTTLED``. PF is None
        while undefined (no losses in the window). Pure and fail-safe."""
        try:
            from bot.risk.equity_throttle import rolling_profit_factor
            cfg = CONFIG.risk
            enabled = bool(cfg.equity_throttle_enabled)
            recent = list(self._realized_pnl_window)[-cfg.equity_throttle_window:]
            n = len(recent)
            pf = rolling_profit_factor(recent) if n else None
            mult = self.equity_throttle_multiplier
            if not enabled:
                status = "OFF"
            elif n < cfg.equity_throttle_min_samples:
                status = "WARMUP"
            elif mult < 1.0:
                status = "THROTTLED"
            else:
                status = "OK"
            return {"enabled": enabled, "samples": n,
                    "pf": round(pf, 3) if pf is not None else None,
                    "multiplier": round(mult, 3), "status": status}
        except Exception:
            return {"enabled": False, "samples": 0, "pf": None,
                    "multiplier": 1.0, "status": "OFF"}

    def live_performance_state(self) -> dict:
        """Read-only diagnostic snapshot of the live-performance governor.

        Returns ``{enabled, samples, win_rate, net_pnl, multiplier, status}`` where
        status is one of: ``OFF`` (flag off), ``WARMUP`` (fewer than
        ``live_perf_min_samples`` closed trades), ``OK``, ``REDUCE``, or ``PAUSE``.
        Pure — never mutates state; fail-safe to an OFF/zeroed snapshot on error.
        Used by the admin /accounts view to surface why an account is throttled.
        """
        try:
            enabled = bool(CONFIG.risk.live_performance_governor_enabled)
            window = CONFIG.risk.live_perf_window
            recent = list(self._realized_pnl_window)[-window:]
            n = len(recent)
            wins = sum(1 for p in recent if p > 0)
            win_rate = (wins / n) if n else 0.0
            net = sum(recent)
            mult = self.live_performance_size_multiplier
            if not enabled:
                status = "OFF"
            elif n < CONFIG.risk.live_perf_min_samples:
                status = "WARMUP"
            elif mult <= 0:
                status = "PAUSE"
            elif mult < 1.0:
                status = "REDUCE"
            else:
                status = "OK"
            return {
                "enabled": enabled, "samples": n, "win_rate": round(win_rate, 3),
                "net_pnl": round(net, 2), "multiplier": mult, "status": status,
            }
        except Exception:
            return {"enabled": False, "samples": 0, "win_rate": 0.0,
                    "net_pnl": 0.0, "multiplier": 1.0, "status": "OFF"}

    @property
    def in_drawdown_recovery(self) -> bool:
        return self._in_drawdown_recovery

    def update_price_history(self, symbol: str, price: float, ts: Optional[float] = None) -> None:
        """Record a (timestamp, price) point for rolling correlation / VaR.

        #49: the point is timestamped so cross-asset return series can be aligned
        on a common time grid rather than by list position. Callers feeding a whole
        watchlist on one tick should pass a SHARED ``ts`` for that tick, so a symbol
        that misses some ticks doesn't get its stale returns paired against another
        symbol's fresh ones. ``ts=None`` stamps wall-clock now (each call distinct →
        the VaR path then falls back to positional alignment, the prior behaviour)."""
        if symbol not in self._price_history:
            self._price_history[symbol] = []
        self._price_history[symbol].append((float(ts) if ts is not None else time.time(), float(price)))
        if len(self._price_history[symbol]) > 100:
            self._price_history[symbol] = self._price_history[symbol][-100:]

    def evaluate(self, idea: TradeIdea, atr: Optional[float] = None, live_equity: Optional[float] = None, max_position_usd: Optional[float] = None, live_open_count: Optional[int] = None, as_of: Optional[datetime] = None) -> RiskCheck:
        """
        Run all 23 pre-trade checks (16 in-engine + #17 liquidity + #18 macro + #19 MTF + #20 PCA + #21 VaR + #22 taker 3-bar + #23 bid dominance).
        Returns RiskCheck with APPROVED or REJECTED.
        Pass atr= for volatility guard check.
        Pass live_equity= to override paper equity for sizing in LIVE mode.
        Pass max_position_usd= to cap sizing at execution limit (e.g. micro-test $10).
        Pass live_open_count= to override paper open position count in LIVE mode.
        """
        with self._lock:
            return self._evaluate_locked(idea, atr, live_equity=live_equity, max_position_usd=max_position_usd, live_open_count=live_open_count, as_of=as_of)

    def _evaluate_locked(self, idea: TradeIdea, atr: Optional[float] = None, live_equity: Optional[float] = None, max_position_usd: Optional[float] = None, live_open_count: Optional[int] = None, as_of: Optional[datetime] = None) -> RiskCheck:
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

        # ── Drive the previously-inert breaker feeders ───────────────────
        # Bug 20 (always on): time-based auto-clear so a stopped warning burst
        # can't latch the warning-rate breaker forever.
        self._refresh_warning_rate_breaker()
        # Bug 9 (opt-in): feed the equity-curve breaker. Its feeder was never
        # called, so the breaker was permanently inert. Gated — it adds a pause.
        if CONFIG.risk.equity_curve_breaker_enabled:
            try:
                self.record_equity_snapshot(state.equity_usd)
            except Exception:
                pass
        # Bug 21 (opt-in): update drawdown-recovery mode from the LIVE (recoverable)
        # drawdown so it can actually activate/deactivate. Gated — it adds a
        # higher-confidence + reduced-size restriction while underwater.
        if CONFIG.risk.drawdown_recovery_enabled:
            try:
                self.check_drawdown_recovery(
                    getattr(state, "current_drawdown_pct", state.max_drawdown_pct))
            except Exception:
                pass

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
        # Fixed-fractional size from the per-strategy risk budget. stop_distance_pct
        # is floored above so the branch is always taken; if it ever weren't,
        # position_usd retains the flat-notional value computed above as the
        # fallback. #54: dropped the redundant pre-assignment of risk_budget and
        # the uncapped_position_usd mirror — position_usd is the only value read below.
        if stop_distance_pct > 0:
            # Per-strategy-type risk budget scaling
            _st = getattr(idea, 'strategy_type', 'swing')
            st_risk_pct = CONFIG.strategy_types.get_max_risk_pct(_st)
            risk_budget = sizing_equity * (st_risk_pct / 100.0)
            position_usd = risk_budget / stop_distance_pct

        # Apply execution cap (e.g., micro-test $10 limit).
        # The risk engine must evaluate the ACTUAL position size that will
        # be executed, not the theoretical uncapped size.  Without this,
        # a $10 micro-test position on a $100 account (10% exposure) gets
        # rejected because the theoretical size (e.g., $43) exceeds 20%.
        if max_position_usd is not None and max_position_usd > 0:
            position_usd = min(position_usd, max_position_usd)

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
            except Exception as _macro_exc:
                # RC-AUD-011: fail toward SAFETY — a provider hiccup must not
                # silently drop the protective size reduction (leaving full size).
                # Apply a conservative default reduction and audit it.  Macro
                # check #18 below still runs fail-closed and may reject outright.
                _macro_size_mult = _PROVIDER_FALLBACK_SIZE_MULT
                audit(risk_log,
                      f"Macro provider error — applying conservative size×"
                      f"{_PROVIDER_FALLBACK_SIZE_MULT} fallback ({_macro_exc})",
                      action="macro_size_reduction", result="PROVIDER_ERROR_FALLBACK",
                      data={"multiplier": _PROVIDER_FALLBACK_SIZE_MULT,
                            "error": str(_macro_exc)},
                      level=logging.WARNING)

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
            # as_of lets the backtest pass the simulated bar time so session
            # sizing is causal and reproducible; live passes None → now.
            _session = get_current_session(now=as_of)
            _session_mult = _session.size_multiplier
            if _session_mult < 1.0:
                position_usd *= _session_mult
        except Exception as _session_exc:
            # RC-AUD-011: fail toward SAFETY.  The session sizer must never *block*
            # risk evaluation (it reduces, never rejects), but a provider error
            # must not silently leave the position full-size — apply a conservative
            # default reduction and audit it.
            position_usd *= _PROVIDER_FALLBACK_SIZE_MULT
            audit(risk_log,
                  f"Session provider error — applying conservative size×"
                  f"{_PROVIDER_FALLBACK_SIZE_MULT} fallback ({_session_exc})",
                  action="session_size_reduction", result="PROVIDER_ERROR_FALLBACK",
                  data={"multiplier": _PROVIDER_FALLBACK_SIZE_MULT,
                        "error": str(_session_exc)},
                  level=logging.WARNING)

        # Equity curve circuit breaker sizing
        _eq_mult = self.equity_curve_size_multiplier
        if _eq_mult < 1.0:
            if _eq_mult <= 0:
                failed.append("EQUITY_CURVE: trading paused — equity below 2σ of MA")
                position_usd = 0.0  # #50: a paused trade reports size 0, not a phantom notional
            else:
                position_usd *= _eq_mult

        # Live-performance governor (opt-in, default OFF): de-risk on REALIZED
        # recent results. Reduces size when the recent window underperforms and
        # pauses (rejects) when it is both losing often and net-negative. Only
        # ever tightens; no-op until live_perf_min_samples closed trades accrue.
        if CONFIG.risk.live_performance_governor_enabled:
            _gov_mult = self.live_performance_size_multiplier
            if _gov_mult < 1.0:
                if _gov_mult <= 0:
                    failed.append(
                        "LIVE_PERF_GOVERNOR: trading paused — realized win rate "
                        "and net PnL below floor over recent window")
                    position_usd = 0.0  # #50: paused → report size 0, not a phantom notional
                else:
                    position_usd *= _gov_mult

        # Continuous equity throttle (opt-in, default OFF): scale size off the
        # rolling PF of recent realized closes — proportional degradation as
        # performance drifts, instead of waiting for a discrete breaker step.
        # Never pauses (floor > 0): reduced-size trades keep the window
        # refreshing so the throttle can observe recovery and re-scale.
        if CONFIG.risk.equity_throttle_enabled:
            _thr_mult = self.equity_throttle_multiplier
            if _thr_mult < 1.0:
                position_usd *= _thr_mult
                passed.append(f"EQUITY_THROTTLE: size×{_thr_mult:.2f} "
                              "(rolling PF below full-size band)")

        # Drawdown recovery mode: require higher confidence, reduce size
        if self._in_drawdown_recovery:
            if idea.confidence < CONFIG.risk.drawdown_recovery_conf_min:
                failed.append(f"DD_RECOVERY: confidence {idea.confidence:.2f} < {CONFIG.risk.drawdown_recovery_conf_min} (recovery mode)")
            position_usd *= CONFIG.risk.drawdown_recovery_size_mult

        # C2-11: Apply macro reduction pre-cap
        if _macro_size_mult < 1.0:
            position_usd *= _macro_size_mult

        # Portfolio-aware correlation sizing (opt-in, default OFF). Shrink the
        # new trade when it stacks on existing open positions in the SAME
        # correlation group AND direction — the marginal portfolio risk of each
        # additional correlated, same-side bet is larger. Only reduces (mult in
        # [floor, 1.0]); the notional cap and check #2 below stay authoritative.
        if CONFIG.risk.correlation_sizing_enabled or self._live_hardening():
            _corr_mult = self._correlation_size_factor(idea)
            if _corr_mult < 1.0:
                position_usd *= _corr_mult

        # C-03 FIX: Cap position_usd at max_notional BEFORE check #2 runs.
        # The fixed-fractional formula (risk_budget / stop_distance) routinely
        # produces notional sizes far exceeding the risk budget itself (e.g.,
        # 13% budget / 3% stop = 433% of equity).  The cap reduces the actual
        # position to max_position_pct of equity — meaning the per-trade risk
        # is smaller than the budget (conservative), but the notional exposure
        # stays bounded.  Check #2 then re-asserts that this per-trade cap held
        # (a fail-closed invariant); the per-SYMBOL aggregate exposure limit
        # (max_symbol_exposure_pct) is enforced separately by check #15.
        # Kelly sizing (opt-in, default OFF; tighten-only). Take the SMALLER of
        # the fixed-fractional size and the half-Kelly size from realized history.
        # Kelly can only shrink the position, never grow it — a no-edge or
        # no-history Kelly returns 0.0, which is treated as "leave size as-is"
        # (it never forces the size to zero). The hard cap + check #2 below remain
        # authoritative. Default OFF makes this byte-identical to prior behaviour.
        if CONFIG.risk.kelly_sizing_enabled:
            kelly_usd = self._kelly_size_usd(idea, sizing_equity)
            if kelly_usd > 0:
                position_usd = min(position_usd, kelly_usd)

        # #47: the notional cap %. Per-strategy when enabled (a scalp can ride a
        # tighter ceiling than a position trade), else the global max_position_pct.
        # Resolved ONCE so the cap here and the POSITION_SIZE check below agree.
        if CONFIG.risk.per_strategy_notional_cap_enabled:
            _cap_strategy = getattr(idea, "strategy_type", "swing")
            _cap_pct = CONFIG.strategy_types.get_max_position_pct(
                _cap_strategy, CONFIG.risk.max_position_pct)
        else:
            _cap_pct = CONFIG.risk.max_position_pct

        max_notional_usd = sizing_equity * (_cap_pct / 100.0)
        # Volatility-targeted cap (opt-in, default OFF; tighten-only). The notional
        # cap binds on ~every crypto trade, so the engine effectively runs flat
        # margin — realized per-trade risk = margin×lev×stop% ∝ ATR%, scaling UP
        # with volatility (the inverse of risk parity). Float the binding cap
        # INVERSELY with ATR% so per-trade dollar risk is normalized toward
        # vol_target_atr_pct. Fail-open (mult=1.0) on missing/zero ATR or entry;
        # clamped to [floor, 1.0] so it only ever REDUCES the cap (preserves the
        # check #2 hard-cap invariant).
        if (CONFIG.risk.vol_target_sizing_enabled and atr and atr > 0
                and idea.entry_price > 0):
            _cur_atr_pct = atr / idea.entry_price * 100.0
            if _cur_atr_pct > 0:
                _vt = CONFIG.risk.vol_target_atr_pct / _cur_atr_pct
                _vt = max(CONFIG.risk.vol_target_floor, min(1.0, _vt))
                max_notional_usd *= _vt
        # Regime down-sizing also tightens the CAP, not just pre-cap
        # position_usd. The fixed-fractional formula routinely produces sizes
        # far above the notional cap (see comment above), so the cap is
        # binding on ~every trade — multiplying the already-oversized pre-cap
        # position_usd by a sub-1.0 regime_mult was previously a no-op (still
        # clamped to the same cap): this silently neutered EVERY regime
        # reduction (CHOP 0.5x, RANGE 0.7x, and TREND_UP once A/B'd down to
        # 0.7x), not just one. Frozen-benchmark A/B'd for all three
        # (docs/FROZEN_BENCHMARK.md) with no downside on either universe, so
        # this now applies generally rather than being scoped to a single
        # regime name. regime_mult>=1.0 (TREND_DOWN/EXPANSION boosts) stays
        # cap-only-not-exceeding, per C2-29 above — boosts must never let a
        # trade exceed the hard cap.
        if regime_mult < 1.0:
            max_notional_usd *= regime_mult
        # Equity throttle tightens the CAP as well as pre-cap size — the cap
        # binds on ~every trade (see the regime_mult note above), so a pre-cap
        # multiplication alone would be silently clamped away. Net effect:
        # final size = throttle × min(pre-cap size, cap).
        if CONFIG.risk.equity_throttle_enabled:
            _thr_cap_mult = self.equity_throttle_multiplier
            if _thr_cap_mult < 1.0:
                max_notional_usd *= _thr_cap_mult
        if max_notional_usd > 0 and position_usd > max_notional_usd:
            position_usd = max_notional_usd

        # Auto-reset a DAILY-LOSS breaker trip once the UTC day has rolled over
        # (opt-in, default OFF). Without it a single bad day latches the breaker
        # until a human runs /reset, even after daily_pnl rolls back to ~0. Only
        # the daily-loss cause is cleared; drawdown/streak/manual stay manual, and
        # if today is also a loss the daily-loss check below re-trips immediately.
        _today_utc = (as_of or datetime.now(UTC)).strftime("%Y-%m-%d")
        if self._should_autoreset_daily_breaker(
                self._circuit_open, self._circuit_trip_cause, self._circuit_trip_day,
                _today_utc, CONFIG.risk.daily_loss_breaker_autoreset_enabled,
                self._consecutive_losses, CONFIG.risk.max_consecutive_losses):
            _prev_day = self._circuit_trip_day
            self._circuit_open = False
            self._circuit_trip_cause = ""
            self._circuit_trip_day = ""
            audit(risk_log,
                  f"Daily-loss circuit breaker auto-reset at day rollover "
                  f"(tripped {_prev_day}, now {_today_utc})",
                  action="circuit_breaker", result="AUTO_RESET",
                  data={"tripped_day": _prev_day, "today": _today_utc})
            self._save_state()

        # Streak-breaker self-recovery (opt-in, default OFF). A consecutive-
        # loss trip is manual-reset only, so an unattended live bot latches
        # PAUSED. When STREAK_BREAKER_AUTORESET_HOURS > 0, clear it (and zero
        # the streak, exactly like /resume) once that many hours have elapsed
        # since the last loss. Only the STREAK cause — daily-loss/drawdown/
        # manual keep their own paths, so account-drain protection is intact.
        _sbh = CONFIG.risk.streak_breaker_autoreset_hours
        if (_sbh > 0 and self._circuit_open
                and self._circuit_trip_cause == "streak"
                and self._last_loss_time is not None
                and self._now() - self._last_loss_time >= _sbh * 3600.0):
            self._circuit_open = False
            self._circuit_trip_cause = ""
            self._circuit_trip_day = ""
            self._consecutive_losses = 0
            _cool_h = (self._now() - self._last_loss_time) / 3600.0
            audit(risk_log,
                  f"Streak circuit breaker auto-recovered after {_cool_h:.1f}h "
                  f"cool-off (>= {_sbh}h)",
                  action="circuit_breaker", result="AUTO_RESET",
                  data={"cause": "streak", "cool_off_hours": round(_cool_h, 2)})
            self._save_state()

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
            # 2. Position size — fail-closed invariant that the per-trade cap held.
            # Audit F-3: position_usd is the MARGIN committed; the live executor
            # multiplies it by leverage to derive exchange notional. position_usd
            # was just clamped above to max_position_pct of equity, so this check
            # verifies that clamp against the SAME limit (max_position_pct): in
            # normal operation it always passes, but if the cap is ever bypassed
            # or miscomputed it rejects (fail-closed) rather than waving the
            # oversized trade through. It is NOT the per-symbol aggregate guard —
            # that is max_symbol_exposure_pct, enforced by check #15 against the
            # symbol's total (existing + new) exposure. (Was wrongly comparing a
            # single capped trade against max_symbol_exposure_pct (20% > the 13%
            # cap), which made this branch unreachable — deep-audit medium.)
            if sizing_equity <= 0:
                failed.append("EQUITY: zero or negative equity")
            else:
                max_margin_pct = _cap_pct  # #47: same per-trade cap clamped above
                ok, margin_pct = self._position_within_cap(
                    position_usd, sizing_equity, max_margin_pct)
                if ok:
                    passed.append(f"POSITION_SIZE: margin {margin_pct:.1f}% <= {max_margin_pct}%")
                else:
                    failed.append(f"POSITION_SIZE: margin {margin_pct:.1f}% exceeds {max_margin_pct}% cap")
        except Exception as exc:
            failed.append(f"POSITION_SIZE: evaluation error ({exc})")

        daily_loss_pct = 0.0
        try:
            # 3. Daily loss (realized + unrealized) — measured against equity, not free cash.
            # LIVE mode (live_equity passed): the paper snapshot's daily_pnl is
            # ~0 because live fills never touch the paper portfolio, so gate on
            # the LIVE daily-PnL accumulator (fed by record_live_trade_result)
            # against live equity. Without this the daily-loss breaker could
            # never trip on real losses (audit CRITICAL, 2026-07-14).
            if live_equity is not None and live_equity > 0:
                _daily_pnl = self._live_daily_pnl
                loss_base = live_equity
            else:
                _daily_pnl = state.daily_pnl
                loss_base = min(sizing_equity, state.equity_usd) if sizing_equity > 0 and state.equity_usd > 0 else max(sizing_equity, state.equity_usd)
            daily_loss_pct = abs(_daily_pnl / loss_base * 100) if loss_base > 0 else 0
            self._last_known_daily_loss_pct = daily_loss_pct  # C2-42: persist for fallback
            if _daily_pnl < 0 and daily_loss_pct >= CONFIG.risk.max_daily_loss_pct:
                failed.append(f"DAILY_LOSS: {daily_loss_pct:.1f}% >= {CONFIG.risk.max_daily_loss_pct}%")
                # C-05 FIX: trip circuit breaker AND reject the CURRENT trade
                self._trip_circuit_breaker("daily loss limit breached", cause="daily_loss")
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
            # 4. Drawdown — the live-hardened cap is tighter when live + opted in.
            # Gate on CURRENT drawdown (live peak-vs-current equity), NOT the
            # monotonic historical-max: max_drawdown_pct never recovers, so a
            # single deep dip latched this gate forever — the breaker re-tripped
            # on every evaluation and /resume could never clear it ("still paused
            # after reset"). Current drawdown falls as equity recovers, so once
            # the account climbs back under the limit the gate passes and a manual
            # /resume actually sticks. The account-protection intent is preserved:
            # while drawdown is genuinely >= the cap, it still trips and rejects.
            _max_dd = self._effective_max_drawdown_pct()
            # LIVE mode: derive current drawdown from the live equity
            # high-water mark (updated each live evaluation), not the paper
            # snapshot which never moves in pure-live mode (audit CRITICAL).
            if live_equity is not None and live_equity > 0:
                if live_equity > self._live_equity_peak:
                    self._live_equity_peak = live_equity
                _cur_dd = (100.0 * (self._live_equity_peak - live_equity)
                           / self._live_equity_peak) if self._live_equity_peak > 0 else 0.0
            else:
                _cur_dd = getattr(state, "current_drawdown_pct", state.max_drawdown_pct)
            if _cur_dd >= _max_dd:
                failed.append(f"DRAWDOWN: {_cur_dd:.1f}% >= {_max_dd}%")
                # C-05 FIX: trip circuit breaker AND reject the CURRENT trade
                self._trip_circuit_breaker("max drawdown breached", cause="drawdown")
                if "CIRCUIT_BREAKER: tripped during evaluation" not in failed:
                    failed.append("CIRCUIT_BREAKER: tripped during evaluation — current trade rejected")
            else:
                passed.append(f"DRAWDOWN: {_cur_dd:.1f}% OK")
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

        # Fee-aware entry gate (opt-in, default OFF). min-RR is a ratio and can
        # pass a tight-stop trade whose absolute TP distance barely clears fees.
        # Reject unless reward-to-TP >= fee_aware_min_multiple × round-trip cost
        # (entry+exit fees + entry+exit slippage). Skips manual trades. Fail-open.
        if CONFIG.risk.fee_aware_entry_gate_enabled and not is_manual:
            try:
                entry_px = float(idea.entry_price)
                tp_px = float(idea.take_profit)
                if entry_px > 0 and tp_px > 0:
                    reward_pct = abs(tp_px - entry_px) / entry_px
                    taker = CONFIG.risk.taker_fee_pct / 100.0
                    slip = CONFIG.risk.fee_aware_slippage_pct / 100.0
                    round_trip = 2.0 * taker + 2.0 * slip
                    k = float(CONFIG.risk.fee_aware_min_multiple)
                    if reward_pct < k * round_trip:
                        failed.append(
                            f"FEE_AWARE: TP {reward_pct*100:.2f}% < {k:.1f}x round-trip "
                            f"cost {round_trip*100:.2f}% (fees+slippage) — fee-losing edge")
                    else:
                        passed.append(
                            f"FEE_AWARE: TP {reward_pct*100:.2f}% clears {k:.1f}x cost "
                            f"{round_trip*100:.2f}% OK")
            except Exception as exc:
                passed.append(f"FEE_AWARE: skipped (eval error {exc})")

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
                        # Store adjusted leverage on idea for executor (dynamic
                        # attr; setattr keeps the runtime behaviour identical while
                        # not tripping the typed-attribute check).
                        try:
                            setattr(idea, "_adjusted_leverage", safe_lev)
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
                # 7. Confidence threshold. Per-strategy-type when enabled (the
                # analyzer already gated idea generation on this same per-type
                # floor; this stops it being silently overridden by a flat
                # global re-gate), else the single global min_confidence.
                if CONFIG.risk.per_strategy_confidence_floor_enabled:
                    _conf_strategy = getattr(idea, "strategy_type", "swing")
                    min_conf = CONFIG.strategy_types.get_min_confidence(_conf_strategy)
                else:
                    min_conf = CONFIG.risk.min_confidence
                if idea.confidence < min_conf:
                    failed.append(f"CONFIDENCE: {idea.confidence} < {min_conf} minimum")
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
            # Always strictly below the hard circuit-breaker limit (see helper).
            # Half-open recovery: the streak only decays on a WIN, so without
            # a probe path this gate is a permanent latch (blocked trading ->
            # no wins -> blocked forever, silently, below the visible hard
            # breaker). After loss_streak_probe_hours since the last loss, ONE
            # probe trade is allowed at a time (only while flat). A losing
            # probe re-arms the gate via _last_loss_time; a winning probe
            # decays the streak. Unknown last-loss time fails closed.
            soft_limit = self._soft_loss_streak_limit(CONFIG.risk.max_consecutive_losses)
            if self._consecutive_losses >= soft_limit:
                probe_s = CONFIG.risk.loss_streak_probe_hours * 3600.0
                _open_ct = (live_open_count if live_open_count is not None
                            else state.open_positions)
                if (probe_s > 0 and self._last_loss_time is not None
                        and self._now() - self._last_loss_time >= probe_s
                        and _open_ct == 0):
                    _cool_h = (self._now() - self._last_loss_time) / 3600.0
                    passed.append(
                        f"LOSS_STREAK: {self._consecutive_losses} losses, "
                        f"probe allowed after {_cool_h:.1f}h cool-off")
                else:
                    failed.append(f"LOSS_STREAK: {self._consecutive_losses} consecutive losses (>= {soft_limit})")
            else:
                passed.append(f"LOSS_STREAK: {self._consecutive_losses} OK")
        except Exception as exc:
            failed.append(f"LOSS_STREAK: evaluation error ({exc})")

        try:
            # 10. Entry price sanity
            # Audit F-6: reject non-finite (NaN/inf) explicitly — `nan <= 0` is
            # False, so a NaN entry would otherwise report "valid". The model
            # validator already blocks this at construction; this is
            # defense-in-depth for ideas built via model_construct / other paths.
            if not math.isfinite(idea.entry_price) or idea.entry_price <= 0:
                failed.append(f"ENTRY_PRICE: invalid ({idea.entry_price})")
            else:
                passed.append("ENTRY_PRICE: valid")
        except Exception as exc:
            failed.append(f"ENTRY_PRICE: evaluation error ({exc})")

        try:
            # 11. Stop-loss required
            if CONFIG.risk.require_stop_loss:
                # Audit F-6: non-finite SL is invalid (NaN defeats the <= 0 test).
                if not math.isfinite(idea.stop_loss) or idea.stop_loss <= 0:
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
            # 11b. Directional SL/TP side re-validation (audit fix #17).
            # The TradeIdea model validator enforces LONG: SL<entry<TP and
            # SHORT: TP<entry<SL at construction, but ideas built via
            # model_construct (or mutated after construction) bypass it, and
            # the risk-reward check uses abs() so it cannot see an inverted
            # TP. Defense-in-depth: re-check the sides independently here.
            _dir = getattr(idea.direction, "value", str(idea.direction))
            _e, _sl, _tp = idea.entry_price, idea.stop_loss, idea.take_profit
            if all(math.isfinite(x) for x in (_e, _sl, _tp)):
                if _dir == "LONG" and not (_sl < _e < _tp):
                    failed.append(
                        f"SLTP_SIDES: LONG requires SL<entry<TP "
                        f"(sl={_sl}, entry={_e}, tp={_tp})")
                elif _dir == "SHORT" and not (_tp < _e < _sl):
                    failed.append(
                        f"SLTP_SIDES: SHORT requires TP<entry<SL "
                        f"(sl={_sl}, entry={_e}, tp={_tp})")
                else:
                    passed.append("SLTP_SIDES: directionally valid")
            else:
                failed.append("SLTP_SIDES: non-finite level")
        except Exception as exc:
            failed.append(f"SLTP_SIDES: evaluation error ({exc})")

        try:
            # 12. Stale data guard
            # Limit orders get 2x timeout — user needs time to review and set price
            is_limit_order = getattr(idea, 'order_type', '') == 'limit'
            max_age = CONFIG.risk.stale_data_max_age_seconds * (2 if is_limit_order else 1)
            data_age = (datetime.now(UTC) - idea.timestamp).total_seconds()
            # Audit F-7: a future-dated timestamp yields a negative age, which is
            # never > max_age, so the staleness guard would silently pass on
            # clock-skewed or replayed/forward-dated ideas. Reject ages more than
            # a small skew tolerance into the future.
            _CLOCK_SKEW_TOLERANCE_S = 30
            if data_age < -_CLOCK_SKEW_TOLERANCE_S:
                failed.append(
                    f"STALE_DATA: idea timestamp is {-data_age:.0f}s in the FUTURE "
                    f"(> {_CLOCK_SKEW_TOLERANCE_S}s skew tolerance)"
                )
            elif data_age > max_age:
                failed.append(f"STALE_DATA: idea is {data_age:.0f}s old > {max_age}s max")
            else:
                passed.append(f"STALE_DATA: {data_age:.0f}s old OK")
        except Exception as exc:
            failed.append(f"STALE_DATA: evaluation error ({exc})")

        # RC-AUD-008: cooldown-after-loss also binds for manual trades (anti-revenge).
        try:
            # 13. Cooldown after loss. Under backtest replay both the loss
            # stamp (via _now/set_sim_time) and this comparison use SIMULATED
            # bar time — wall-clock here would keep the cooldown armed for
            # months of replayed bars after a single loss.
            if self._last_loss_time is not None:
                _now_epoch = as_of.timestamp() if as_of is not None else self._now()
                elapsed = _now_epoch - self._last_loss_time
                if elapsed < CONFIG.risk.cooldown_after_loss_seconds:
                    remaining = CONFIG.risk.cooldown_after_loss_seconds - elapsed
                    failed.append(f"COOLDOWN: {remaining:.0f}s remaining after last loss")
                else:
                    passed.append("COOLDOWN: cooldown period elapsed")
            else:
                passed.append("COOLDOWN: no recent losses")
        except Exception as exc:
            failed.append(f"COOLDOWN: evaluation error ({exc})")

        # Re-entry cooldown: throttle rapid same-symbol re-entries to curb fee
        # churn. Unlike check #13 (loss-only), this fires after ANY close and
        # measures from the last REAL fill on this symbol (note_symbol_entry),
        # on the same simulated/live clock. Read-only here — the stamp happens
        # at the actual open, so /whynot correctly reports it as a reason.
        # Skips manual trades (deliberate). No-op when the flag is off, the
        # window is 0, or no prior entry is recorded. Fail-safe.
        try:
            if (CONFIG.risk.reentry_cooldown_enabled is True and not is_manual
                    and getattr(self, "_last_entry_by_symbol", None)):
                _cd = float(CONFIG.risk.reentry_cooldown_seconds)
                _last_entry_ts = self._last_entry_by_symbol.get(idea.asset)
                if _cd > 0 and _last_entry_ts is not None:
                    _now_epoch = as_of.timestamp() if as_of is not None else self._now()
                    _elapsed = _now_epoch - _last_entry_ts
                    if _elapsed < _cd:
                        failed.append(
                            f"REENTRY_COOLDOWN: {(_cd - _elapsed):.0f}s remaining "
                            f"on {idea.asset} (churn guard)")
                    else:
                        passed.append(f"REENTRY_COOLDOWN: {idea.asset} cooldown elapsed")
                else:
                    passed.append("REENTRY_COOLDOWN: no recent same-symbol entry")
        except Exception as exc:
            passed.append(f"REENTRY_COOLDOWN: skipped (eval error {exc})")

        margin_equiv_position_usd = 0.0

        try:
            # 14. Portfolio exposure limit (mark-to-market)
            # Audit F-3: position_usd is MARGIN — the portfolio (open_position) and
            # the live executor both commit it as collateral and derive
            # notional = margin * leverage. get_position_value() likewise returns
            # MARGIN (+ unrealized PnL), so position_usd is already the right unit
            # to add; it must NOT be divided by leverage. The previous
            # `position_usd / default_leverage` treated it as a notional and
            # understated each new position's committed margin by the leverage
            # factor (e.g. a $100 micro margin counted as only $20), making the
            # portfolio/symbol exposure guards ~5x too lenient.
            margin_equiv_position_usd = position_usd
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
                        passed.append("MACRO_EVENT: CLEAR")
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
                    if getattr(macro_snap, "stale", False):
                        failed.append(
                            "MACRO_EVENT: BLACKOUT - macro calendar exhausted "
                            "(no future events; refresh the schedule) (fail-closed)")
                    else:
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
        # RC-AUD-007: branch on the explicit VarResult.status instead of a magic
        # negative-value sentinel, so a future change can't silently turn a
        # reject (zero-equity → 100%) into a skip.
        try:
            var_result = self._compute_portfolio_var(position_usd, idea=idea)
            max_var = CONFIG.risk.max_portfolio_var_pct
            if var_result.status == VarStatus.SKIP:
                # Not enough data — skip (fewer than 5 closed trades)
                passed.append("PORTFOLIO_VAR: skipped (insufficient trade history)")
            elif var_result.proposed_var_pct > max_var:
                failed.append(
                    f"PORTFOLIO_VAR: proposed {var_result.proposed_var_pct:.2f}% > {max_var}% limit "
                    f"(current {var_result.current_var_pct:.2f}%)"
                )
            else:
                passed.append(f"PORTFOLIO_VAR: {var_result.proposed_var_pct:.2f}% <= {max_var}% limit")
        except Exception as exc:
            failed.append(f"PORTFOLIO_VAR: evaluation error ({exc})")

        # RC-AUD-011: Checks #22 and #23 are DELIBERATE fail-open exceptions to
        # this module's otherwise fail-closed contract (the same posture as the
        # #17 liquidity guard noted in the module header).  When no order-flow
        # analyzer / signal is wired, the order-flow gates cannot be evaluated and
        # must PASS — hard-failing here would block every trade whenever order flow
        # is simply absent (the common case outside live order-flow streaming).
        # The skip is now made EXPLICIT and AUDITED (rather than a bare warning),
        # so a missing analyzer is visible in the audit trail.  If a future
        # deployment wants these to fail-closed, gate them behind a config flag
        # that defaults to the current fail-open behavior.

        # 22. Taker 3-bar gate (Gate 2) — fail-open (audited) if no order flow analyzer
        try:
            if self._order_flow is not None:
                direction_str = idea.direction.value if hasattr(idea.direction, 'value') else str(idea.direction)
                gate2 = self._order_flow.check_taker_3bar_gate(idea.asset, direction_str)
                if gate2["passed"]:
                    passed.append(f"TAKER_3BAR: {gate2['reason']}")
                else:
                    failed.append(f"TAKER_3BAR: {gate2['reason']}")
            else:
                # Deliberate fail-open: no analyzer wired → pass, but audit it.
                audit(risk_log,
                      "Order flow check #22 (taker 3-bar gate) skipped — no analyzer wired "
                      "(deliberate fail-open)",
                      action="order_flow_gate", result="SKIPPED_NO_ANALYZER",
                      data={"check": "TAKER_3BAR"})
                passed.append("TAKER_3BAR: skipped (no order flow analyzer)")
        except Exception as exc:
            failed.append(f"TAKER_3BAR: evaluation error ({exc})")

        # 23. Bid dominance gate (Rule 20) — book-side dominance in the trade
        # direction; fail-open (audited) when unwired. The cached signal is
        # used ONLY if it belongs to THIS symbol and is fresh — the engine
        # caches the last successful analysis, so without these guards a LONG
        # on symbol B could be gated by symbol A's book from minutes ago.
        _OF_SIGNAL_MAX_AGE_S = 300
        try:
            _sig = self._last_of_signal
            if _sig is not None:
                if getattr(_sig, "symbol", None) != idea.asset:
                    _sig = None
                else:
                    try:
                        _age = (datetime.now(UTC) - _sig.timestamp).total_seconds()
                        if _age > _OF_SIGNAL_MAX_AGE_S:
                            _sig = None
                    except Exception:
                        _sig = None
            if self._order_flow is not None and _sig is not None:
                direction_str = idea.direction.value if hasattr(idea.direction, 'value') else str(idea.direction)
                gate20 = self._order_flow.check_bid_dominance(_sig, direction_str)
                if gate20["passed"]:
                    passed.append(f"BID_DOMINANCE: {gate20['reason']}")
                else:
                    failed.append(f"BID_DOMINANCE: {gate20['reason']}")
            else:
                # Deliberate fail-open: no analyzer, or no fresh same-symbol
                # signal → pass, but audit it.
                audit(risk_log,
                      "Order flow check #23 (bid dominance) skipped — no analyzer or no "
                      "fresh signal for this symbol (deliberate fail-open)",
                      action="order_flow_gate", result="SKIPPED_NO_ANALYZER",
                      data={"check": "BID_DOMINANCE", "idea_symbol": idea.asset,
                            "cached_symbol": getattr(self._last_of_signal, "symbol", None)})
                passed.append("BID_DOMINANCE: skipped (no fresh order flow data)")
        except Exception as exc:
            failed.append(f"BID_DOMINANCE: evaluation error ({exc})")

        # -- Funding clock (default ON, narrow by construction) --
        # Blocks ONLY an entry that would sit on the PAYING side of an
        # extreme funding rate inside the pre-settlement window (Bitget
        # settles 00/08/16 UTC): the position pays immediately, and extreme
        # funding marks crowded positioning that unwinds around the settle.
        # Fail-open on missing/stale/wrong-symbol funding data — backtests
        # carry no funding stream, so the gate self-skips there. Every
        # block is priced by the shadow book (/shadow shows whether the
        # gate earns or eats edge).
        try:
            if CONFIG.risk.funding_clock_gate_enabled:
                from bot.risk.funding_clock import funding_clock_verdict
                _fc_sig = self._last_of_signal
                _fc_rate = None
                if (_fc_sig is not None
                        and getattr(_fc_sig, "symbol", None) == idea.asset):
                    _fc_rate = getattr(_fc_sig, "funding_rate", None)
                _dir = (idea.direction.value
                        if hasattr(idea.direction, "value")
                        else str(idea.direction))
                _blocked, _fc_reason = funding_clock_verdict(
                    _dir, _fc_rate, self._now(),
                    window_sec=CONFIG.risk.funding_clock_window_min * 60.0,
                    extreme_rate=CONFIG.risk.funding_clock_extreme_rate)
                if _blocked:
                    failed.append(f"FUNDING_CLOCK: {_fc_reason}")
                else:
                    passed.append(f"FUNDING_CLOCK: {_fc_reason}")
        except Exception as exc:
            # Fail-open: a broken clock must never block trading.
            passed.append(f"FUNDING_CLOCK: skipped (error: {exc})")

        # -- Verdict --
        verdict = RiskVerdict.APPROVED if len(failed) == 0 else RiskVerdict.REJECTED

        # Strangle-watchdog inputs: cumulative evaluated/approved counts and
        # the time of the last approval (engine time, sim-aware). The
        # proactive monitor diffs these to detect "ideas flow but nothing is
        # ever approved" — the failure shape of a silently latched gate.
        self._eval_total += 1
        if verdict == RiskVerdict.APPROVED:
            self._approved_total += 1
            self._last_approval_time = self._now()

        # Gate telemetry: per-check pass/fail/skip counters so newly-wired
        # gates (taker 3-bar, book dominance, ...) can be threshold-tuned on
        # live evidence instead of judgment. Prefix before ':' is the gate
        # name; "skipped"/"fail-open" entries count as skips, not passes.
        stats = self._gate_stats
        for entry in passed:
            name_key = entry.split(":", 1)[0].strip()
            low = entry.lower()
            bucket = "skipped" if ("skipped" in low or "fail-open" in low) else "passed"
            rec = stats.setdefault(name_key, {"passed": 0, "failed": 0, "skipped": 0})
            rec[bucket] += 1
        for entry in failed:
            name_key = entry.split(":", 1)[0].strip()
            rec = stats.setdefault(name_key, {"passed": 0, "failed": 0, "skipped": 0})
            rec["failed"] += 1

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

        # Audit V7 follow-up: make the margin/notional/leverage relationship
        # explicit in every evaluation. position_size_usd is the engine's sizing
        # figure; the live executor commits it as MARGIN and applies leverage, so
        # the real exchange notional is position_size_usd * leverage. The micro
        # caps and the executor's hard notional ceiling are the binding live
        # safeguards. This is visibility only — it changes no pass/fail logic.
        _lev = getattr(CONFIG.exchange, "default_leverage", 1) or 1
        _audit_data = check.model_dump(mode="json")
        _audit_data["leverage"] = _lev
        _audit_data["approx_notional_usd"] = round(position_usd * _lev, 2)
        audit(risk_log, f"Risk {verdict.value} for {idea.asset} [{len(passed)}P/{len(failed)}F]",
              action="risk_check", result=verdict.value,
              data=_audit_data)
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

    def _kelly_size_usd(self, idea: TradeIdea, sizing_equity: float) -> float:
        """Half-Kelly size in USD from realized history for the opt-in tighten-only
        path in evaluate(). Returns 0.0 (a NO-OP signal — caller leaves size as-is)
        when there is not yet enough history to estimate an edge, or when the
        estimate has no positive edge. Never used to GROW size: the caller takes
        ``min(fixed_fractional, this)`` only when this is > 0.
        """
        if sizing_equity <= 0:
            return 0.0
        try:
            closed = [t for t in self._portfolio.trade_history if t.exit_price is not None]
        except Exception as exc:
            risk_log.debug("Kelly history unavailable: %s", exc)
            return 0.0
        if len(closed) < CONFIG.risk.kelly_min_trades:
            return 0.0  # no edge estimate yet → leave fixed-fractional size intact
        wins = [t for t in closed if t.pnl > 0]
        losses = [t for t in closed if t.pnl < 0]
        win_rate = len(wins) / len(closed)
        avg_win = (sum(t.pnl for t in wins) / len(wins)) if wins else 0.0
        avg_loss = (abs(sum(t.pnl for t in losses)) / len(losses)) if losses else 0.0
        fraction = self.kelly_position_size(idea.confidence, win_rate, avg_win, avg_loss)
        return round(sizing_equity * fraction, 2)

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
        """Higher-timeframe alignment gate (opt-in, MTF_ALIGNMENT_GATE_ENABLED).

        Rejects a COUNTER-TREND entry — a LONG when the higher-timeframe trend
        is bearish, or a SHORT when it is bullish. The HTF trend comes from the
        analyzer's MTF confluence (EMA20/50 across 1h/4h/1d, daily-weighted),
        carried on ``idea.htf_trend``. A legacy "MTF:1h=UP" convention in
        ``signals_used`` is honored as a fallback source. Neutral / unknown HTF
        → no opinion (skip). Off → byte-identical to the legacy dead-skip: the
        gate always returned None because nothing ever produced the MTF: tags it
        used to parse.
        """
        # Disabled → behave exactly as before (the gate was effectively dead).
        if CONFIG.risk.mtf_alignment_gate_enabled is not True:
            return None

        htf = str(getattr(idea, "htf_trend", "") or "").lower()

        # Fallback: derive a direction from legacy "MTF:1h=UP" tags in
        # signals_used when the analyzer didn't stamp htf_trend.
        if htf not in ("bullish", "bearish"):
            mtf_trends: dict[str, str] = {}
            for sig in idea.signals_used:
                if sig.upper().startswith("MTF:"):
                    parts = sig[4:].split("=", 1)
                    if len(parts) == 2:
                        mtf_trends[parts[0].strip()] = parts[1].strip()
            if len(mtf_trends) >= 2:
                vals = [v.upper() for v in mtf_trends.values()]
                ups, downs = vals.count("UP"), vals.count("DOWN")
                if ups > downs:
                    htf = "bullish"
                elif downs > ups:
                    htf = "bearish"

        if htf not in ("bullish", "bearish"):
            return None  # no clear HTF trend → no opinion

        direction = getattr(idea.direction, "value", str(idea.direction)).upper()
        is_long = ("LONG" in direction) or (direction == "BUY")
        if htf == "bearish" and is_long:
            return "MTF_ALIGNMENT: higher-timeframe trend BEARISH opposes LONG entry"
        if htf == "bullish" and not is_long:
            return "MTF_ALIGNMENT: higher-timeframe trend BULLISH opposes SHORT entry"
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
        if regime.upper() == "TREND_UP":
            base["position_size_mult"] = CONFIG.risk.trend_up_size_mult

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

    def _compute_portfolio_var(self, position_usd: float, confidence_level: float = 0.95,
                               idea: Optional[TradeIdea] = None) -> VarResult:
        """Compute parametric VaR for portfolio including proposed position.

        Returns a VarResult (RC-AUD-007) carrying current/proposed VaR as a
        percentage of equity, plus an explicit status:
          - VarStatus.SKIP  → insufficient data (<5 closed trades); caller passes.
          - VarStatus.OK    → VaR computed; caller compares proposed vs the limit.
        Zero/negative equity with a pending position is returned as
        VarStatus.OK with proposed_var_pct=100.0 (max risk) so the caller rejects.

        Uses historical per-trade returns to estimate volatility.

        H-05 LIMITATION: This VaR uses per-trade returns (individual trade P&L /
        notional) as a proxy for portfolio return volatility. This does NOT capture
        cross-asset correlations, concurrent position overlap, or true portfolio-level
        return distribution. A proper portfolio VaR would require time-series of
        daily portfolio mark-to-market returns and a covariance matrix across held
        assets. The current approach overstates diversification benefit and may
        understate tail risk for concentrated portfolios. Do not rely on this VaR
        as a standalone risk metric — it is a rough directional guard only.

        ROADMAP H-05: when ``var_covariance_enabled`` is set AND every held +
        proposed asset has enough aligned price history, the covariance-based
        path below supersedes this proxy (it models cross-asset correlation and
        nets opposing hedges). If that path can't compute, we fall through to the
        per-trade proxy unchanged — the check is never silently downgraded.
        """
        import math

        # Opt-in covariance VaR (roadmap H-05). Default OFF → this is a no-op and
        # the per-trade proxy below runs byte-for-byte as before. Returns None
        # (fall through) whenever it lacks the data to compute a real matrix.
        if (CONFIG.risk.var_covariance_enabled or self._live_hardening()) and idea is not None:
            cov_result = self._compute_portfolio_var_covariance(
                position_usd, confidence_level, idea)
            if cov_result is not None:
                return cov_result

        history = self._portfolio.trade_history
        closed = [t for t in history if t.exit_price is not None and t.entry_price > 0]

        if len(closed) < 5:
            return VarResult(VarStatus.SKIP, -1.0, -1.0)

        state = self._portfolio.snapshot()
        equity = state.equity_usd
        if equity <= 0:
            # Zero equity with a pending position = max risk → reject (encoded as
            # OK + 100% so the caller's proposed>max comparison rejects it).
            return VarResult(VarStatus.OK, 0.0, 100.0)

        # Compute per-trade return percentages
        returns = []
        for t in closed:
            notional = t.entry_price * t.quantity
            if notional > 0:
                returns.append(t.pnl / notional)

        if len(returns) < 5:
            return VarResult(VarStatus.SKIP, -1.0, -1.0)

        # Portfolio volatility from trade returns
        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
        vol = math.sqrt(variance)

        # C2-28 FIX: z-score lookup table replaces erfc formula that produced
        # wildly wrong values (~0.007 at 99% instead of correct 2.326).
        z_score = self._var_z_score(confidence_level)

        # Holding period: 1 day (sqrt(1) = 1)
        holding_period = 1.0

        # Current portfolio exposure (sum of open position notionals)
        current_exposure = 0.0
        for pos in self._portfolio.open_positions:
            current_exposure += pos.entry_price * pos.quantity

        # VaR = z * vol * sqrt(T) * exposure / equity * 100
        # Audit F-3: current_exposure above is NOTIONAL (entry * qty). The
        # proposed position_usd is MARGIN, so it must be converted to notional
        # (margin * leverage) before being added — otherwise VaR mixes units and
        # understates the new position's risk contribution by the leverage factor.
        _lev = getattr(CONFIG.exchange, "default_leverage", 1) or 1
        proposed_notional = position_usd * _lev
        sqrt_t = math.sqrt(holding_period)
        current_var_pct = (z_score * vol * sqrt_t * current_exposure / equity * 100) if equity > 0 else 0.0
        proposed_exposure = current_exposure + proposed_notional
        proposed_var_pct = (z_score * vol * sqrt_t * proposed_exposure / equity * 100) if equity > 0 else 0.0

        return VarResult(VarStatus.OK, round(current_var_pct, 4), round(proposed_var_pct, 4))

    @staticmethod
    def _returns_from_prices(prices: list[float]) -> list[float]:
        """Simple per-step returns from a price series (skips non-positive prices)."""
        out: list[float] = []
        for i in range(1, len(prices)):
            prev = prices[i - 1]
            if prev > 0:
                out.append((prices[i] - prev) / prev)
        return out

    def _aligned_returns(self, assets: list[str], min_points: int) -> Optional[dict[str, list[float]]]:
        """#49: per-asset return series aligned on a COMMON timestamp grid.

        Builds {ts: price} per asset (last price wins per timestamp), intersects the
        timestamps across all assets, and computes returns over that shared, sorted
        grid — so a symbol that missed some ticks contributes only timestamps it
        actually has, instead of having its returns paired positionally against
        another symbol's. Returns equal-length series, or None when the common
        overlap is too small (caller then falls back to positional alignment)."""
        maps: dict[str, dict[float, float]] = {}
        for a in assets:
            hist = self._price_history.get(a) or []
            m: dict[float, float] = {}
            for pt in hist:
                # Only timestamped (ts, price) points can be time-aligned. Bare
                # floats (legacy / direct test fixtures) → bail to positional.
                if not (isinstance(pt, (tuple, list)) and len(pt) == 2):
                    return None
                m[float(pt[0])] = float(pt[1])
            if not m:
                return None
            maps[a] = m
        common = set(maps[assets[0]].keys())
        for a in assets[1:]:
            common &= set(maps[a].keys())
        grid = sorted(common)
        if len(grid) < min_points + 1:
            return None
        out: dict[str, list[float]] = {}
        for a in assets:
            px = [maps[a][t] for t in grid]
            rets = [((px[k] - px[k - 1]) / px[k - 1]) if px[k - 1] > 0 else 0.0
                    for k in range(1, len(px))]
            out[a] = rets
        return out

    def _compute_portfolio_var_covariance(
        self, position_usd: float, confidence_level: float, idea: TradeIdea
    ) -> Optional[VarResult]:
        """Covariance-matrix portfolio VaR (roadmap H-05).

        Models the portfolio as a vector of equity-fraction weights w (signed by
        position direction: long = +, short = −) over the held + proposed assets,
        with a covariance matrix Σ estimated from each asset's recent price
        returns. Portfolio variance is wᵀΣw, so two correlated longs ADD risk
        while a long+short hedge NETS it — exactly the cross-asset structure the
        per-trade proxy is blind to. VaR% = z·√(wᵀΣw)·100.

        Returns None (caller falls back to the per-trade proxy) whenever it can't
        compute a trustworthy matrix: equity ≤ 0, or any required asset lacks at
        least ``var_covariance_min_points`` aligned return observations. It never
        returns a SKIP — fall-back, not downgrade.
        """
        import math

        state = self._portfolio.snapshot()
        equity = state.equity_usd
        if equity <= 0:
            return None

        min_points = CONFIG.risk.var_covariance_min_points

        def _signed_notional(asset: str, direction_val: str, notional: float,
                             acc: dict[str, float]) -> None:
            sign = -1.0 if str(direction_val).upper() == "SHORT" else 1.0
            acc[asset] = acc.get(asset, 0.0) + sign * notional

        # Current portfolio weights (open positions only).
        current_notional: dict[str, float] = {}
        for pos in self._portfolio.open_positions:
            notional = pos.entry_price * pos.quantity
            dir_val = pos.direction.value if hasattr(pos.direction, "value") else str(pos.direction)
            _signed_notional(pos.asset, dir_val, notional, current_notional)

        # Proposed adds margin→notional (margin × leverage, matching the F-3 unit
        # fix in the per-trade path) to the proposed asset, signed by its side.
        _lev = getattr(CONFIG.exchange, "default_leverage", 1) or 1
        proposed_notional_usd = position_usd * _lev
        idea_dir = idea.direction.value if hasattr(idea.direction, "value") else str(idea.direction)
        proposed_notional = dict(current_notional)
        _signed_notional(idea.asset, idea_dir, proposed_notional_usd, proposed_notional)

        z_score = self._var_z_score(confidence_level)

        def _var_pct(weights_usd: dict[str, float]) -> Optional[float]:
            """VaR% for a signed-notional book, or None if history is insufficient."""
            assets = [a for a, n in weights_usd.items() if n != 0.0]
            if not assets:
                return 0.0
            # #49: prefer returns aligned on a COMMON timestamp grid so a symbol
            # that missed ticks isn't paired positionally against a fresher one.
            aligned = self._aligned_returns(assets, min_points)
            if aligned is not None:
                window = len(next(iter(aligned.values())))
            else:
                # Fallback (insufficient timestamp overlap, e.g. unshared stamps):
                # the prior trailing-common-window positional alignment.
                series: dict[str, list[float]] = {}
                for a in assets:
                    hist = self._price_history.get(a) if hasattr(self, "_price_history") else None
                    prices = [(pt[1] if isinstance(pt, (tuple, list)) else pt) for pt in hist] if hist else None
                    rets = self._returns_from_prices(prices) if prices else []
                    if len(rets) < min_points:
                        return None
                    series[a] = rets
                window = min(len(series[a]) for a in assets)
                if window < min_points:
                    return None
                aligned = {a: series[a][-window:] for a in assets}
            means = {a: sum(aligned[a]) / window for a in assets}
            w = {a: weights_usd[a] / equity for a in assets}
            # Portfolio variance = Σ_i Σ_j w_i w_j cov(i, j), sample cov (ddof=1).
            denom = window - 1
            if denom <= 0:
                return None
            variance = 0.0
            for ai in assets:
                wi = w[ai]
                ri = aligned[ai]
                mi = means[ai]
                for aj in assets:
                    rj = aligned[aj]
                    mj = means[aj]
                    cov_ij = sum((ri[k] - mi) * (rj[k] - mj) for k in range(window)) / denom
                    variance += wi * w[aj] * cov_ij
            vol = math.sqrt(variance) if variance > 0 else 0.0
            return z_score * vol * 100.0

        proposed_var = _var_pct(proposed_notional)
        if proposed_var is None:
            return None
        current_var = _var_pct(current_notional)
        if current_var is None:
            current_var = 0.0

        return VarResult(VarStatus.OK, round(current_var, 4), round(proposed_var, 4))

    @staticmethod
    def _var_z_score(confidence_level: float) -> float:
        """Z-score for a VaR confidence level (shared by both VaR paths)."""
        _VAR_Z_SCORES = {0.90: 1.282, 0.95: 1.645, 0.99: 2.326, 0.999: 3.090}
        if confidence_level in _VAR_Z_SCORES:
            return _VAR_Z_SCORES[confidence_level]
        nearest = min(_VAR_Z_SCORES.keys(), key=lambda k: abs(k - confidence_level))
        z = _VAR_Z_SCORES[nearest]
        risk_log.warning(
            "No exact z-score for confidence %.4f — using nearest %.3f (z=%.3f)",
            confidence_level, nearest, z,
        )
        return z

    def _correlation_group(self, asset: str) -> str:
        """Map an asset to its correlation group.

        W-P2-2: normalize to "BASE/USDT" (the _CORRELATION_GROUPS key format)
        before lookup. Symbols not in the map are pooled into ONE shared bucket
        (_UNMAPPED_GROUP) instead of each becoming its own singleton group — so
        a basket of unmapped alts cannot collectively dodge the per-group cap.

        Round 7: the bot trades USDT-perps whose ccxt id is "SOL/USDT:USDT"; the
        map keys are spot-style "SOL/USDT". Without stripping the ":SETTLE" suffix
        every futures symbol missed the map and fell through to _UNMAPPED_GROUP,
        pooling ALL alts into one bucket. That pooling is a BUG (the ALT_L1/MEME/
        DEFI taxonomy never applied), but it accidentally acted as a global
        correlated-exposure cap (max_unmapped_correlated across everything). On a
        dense multi-group benchmark, switching to correct per-group caps LOOSENS
        aggregate exposure (per-group budgets sum higher than the one pooled cap)
        and materially raised max drawdown. So the corrected mapping is gated
        behind correlation_perp_group_mapping_enabled (default OFF) to preserve
        the tighter live behaviour until it's paired with a global correlated cap.
        """
        key = asset if "/" in asset else f"{asset}/USDT"
        # Gated: only strip the perp suffix (→ correct per-group mapping) when the
        # operator opts in. Default OFF keeps the current pooled behaviour, which
        # bounds total correlated exposure more tightly (lower drawdown on the
        # dense A/B). Fail-safe: any error leaves the suffix in place (pooled).
        try:
            if ":" in key and CONFIG.risk.correlation_perp_group_mapping_enabled is True:
                key = key.split(":", 1)[0]
        except Exception:
            pass
        return _CORRELATION_GROUPS.get(key, _UNMAPPED_GROUP)

    # ── Round 7 Phase 1: forward-looking correlation cap ─────────────

    def register_pending_intent(self, idea: TradeIdea) -> None:
        """Record an APPROVED-but-not-yet-filled entry so the per-group
        correlation cap counts it (see CORRELATION_FORWARD_INTENTS_ENABLED).
        Called by the caller between risk approval and fill. No-op when the flag
        is off, so the ledger stays empty and behaviour is byte-identical.
        Fail-safe: never raises."""
        if not CONFIG.risk.correlation_forward_intents_enabled:
            return
        try:
            with self._lock:
                self._pending_intents[idea.id] = (
                    self._correlation_group(idea.asset),
                    getattr(idea.direction, "value", str(idea.direction)),
                    self._now(),
                )
        except Exception:
            pass

    def note_symbol_entry(self, symbol: str, as_of: Optional[datetime] = None) -> None:
        """Stamp the time of a REAL fill on ``symbol`` so the re-entry cooldown
        (REENTRY_COOLDOWN_ENABLED) can throttle same-symbol churn. Called at the
        actual open — backtest ``_execute_fill`` / live post-execute success —
        NOT at evaluation, because ``evaluate()`` runs twice per trade (scan +
        confirm-recheck) and stamping there would self-trip the cooldown. Uses
        the same simulated/live clock as the loss cooldown (pass the bar time as
        ``as_of`` under replay). No-op when the flag is off, so the ledger stays
        empty and behaviour is byte-identical. Fail-safe: never raises."""
        if getattr(CONFIG.risk, "reentry_cooldown_enabled", False) is not True:
            return
        try:
            ts = as_of.timestamp() if as_of is not None else self._now()
            with self._lock:
                self._last_entry_by_symbol[str(symbol)] = float(ts)
        except Exception:
            pass

    def clear_pending_intent(self, idea_id: str) -> None:
        """Drop a pending intent once its entry has filled or been cancelled.
        Idempotent; safe even when the flag is off or the id is absent."""
        try:
            with self._lock:
                self._pending_intents.pop(idea_id, None)
        except Exception:
            pass

    def _prune_expired_intents(self, now: float) -> None:
        """Remove intents older than the safety TTL so a leaked intent (a missed
        clear) can't latch the cap. Caller holds self._lock."""
        try:
            ttl = float(getattr(CONFIG.risk, "correlation_intent_ttl_sec", 7200.0) or 0.0)
        except (TypeError, ValueError):
            return  # non-numeric (e.g. mocked) config → skip pruning, keep intents
        if ttl <= 0 or not self._pending_intents:
            return
        stale = [k for k, (_g, _d, ts) in self._pending_intents.items()
                 if now - ts > ttl]
        for k in stale:
            self._pending_intents.pop(k, None)

    def _pending_intent_group_count(self, group: str, exclude_id: str) -> int:
        """Live pending intents in ``group`` (excluding the idea being evaluated).
        Prunes expired intents first. Caller holds self._lock."""
        self._prune_expired_intents(self._now())
        return sum(1 for iid, (g, _d, _ts) in self._pending_intents.items()
                   if g == group and iid != exclude_id)

    def _check_correlation(self, idea: TradeIdea) -> Optional[str]:
        """Prevent concentrated bets in the same correlation group."""
        new_group = self._correlation_group(idea.asset)
        open_groups: list[str] = [
            self._correlation_group(pos.asset)
            for pos in self._portfolio.open_positions
        ]

        group_count = open_groups.count(new_group)
        # Round 7 Phase 1: make the cap FORWARD-LOOKING. Also count approved-but-
        # not-yet-filled intents in this group so a correlated same-bar cluster
        # can't all pass while each sees zero OPEN group members (the cluster fills
        # next bar and blows past max_correlation_per_group). Gated OFF by default.
        # Short-circuit on an empty/absent ledger so this is a strict no-op (and
        # never touches _now/_pending_intents) unless intents are actually live.
        # Fail-safe: any error leaves the open-only count untouched.
        try:
            if (CONFIG.risk.correlation_forward_intents_enabled
                    and getattr(self, "_pending_intents", None)):
                group_count += self._pending_intent_group_count(new_group, exclude_id=idea.id)
        except Exception:
            pass
        # The shared unmapped-alt bucket gets its own, more generous cap (its
        # members aren't all mutually correlated); mapped groups keep the
        # tighter per-group cap.
        if new_group == _UNMAPPED_GROUP:
            max_per_group = CONFIG.risk.max_unmapped_correlated
            where = f"unmapped-alt bucket '{_UNMAPPED_GROUP}'"
        else:
            max_per_group = CONFIG.risk.max_correlation_per_group
            where = f"group '{new_group}'"
        if group_count >= max_per_group:
            return (
                f"CORRELATION: already {group_count} positions in {where} "
                f"(max {max_per_group})"
            )

        # Round 7 (revised Phase 2): global correlated-exposure cap. Per-group
        # caps don't bound TOTAL exposure (each group has its own budget), so a
        # market-wide move can still stack many same-direction correlated bets —
        # the tail the pooled-bucket bug was accidentally bounding. Cap concurrent
        # SAME-DIRECTION positions across ALL correlated groups (open + pending
        # intents). Gated: only when the perp mapping is enabled AND the cap is
        # >0. Fail-safe: any error skips the cap (no spurious reject).
        try:
            cap_total = int(getattr(CONFIG.risk, "max_correlated_same_dir_positions", 0) or 0)
            # `is True` (not truthiness) so a wholesale-mocked CONFIG — whose
            # attrs are truthy Mocks — can't spuriously activate this gate.
            if CONFIG.risk.correlation_perp_group_mapping_enabled is True and cap_total > 0:
                new_dir = getattr(idea.direction, "value", str(idea.direction))
                same_dir = sum(
                    1 for pos in self._portfolio.open_positions
                    if getattr(pos.direction, "value", str(pos.direction)) == new_dir)
                if CONFIG.risk.correlation_forward_intents_enabled and getattr(self, "_pending_intents", None):
                    same_dir += sum(
                        1 for iid, (_g, d, _ts) in self._pending_intents.items()
                        if d == new_dir and iid != idea.id)
                if same_dir >= cap_total:
                    return (
                        f"CORRELATION_TOTAL: {same_dir} concurrent {new_dir} "
                        f"correlated positions (max {cap_total})"
                    )
        except Exception:
            pass

        # V2: Rolling return correlation check
        # If we have price history, compute actual pairwise correlation
        # with existing open positions
        try:
            if hasattr(self, '_price_history') and idea.asset in self._price_history:
                for tid, pos in self._portfolio._positions.items():
                    if pos.asset == idea.asset:
                        continue
                    if pos.asset in self._price_history:
                        # #49: _price_history now holds (ts, price); strip to prices
                        # for this positional correlation gate (unchanged behaviour).
                        # Tolerant of legacy bare-float points too.
                        prices_new = [(pt[1] if isinstance(pt, (tuple, list)) else pt)
                                      for pt in self._price_history[idea.asset]]
                        prices_existing = [(pt[1] if isinstance(pt, (tuple, list)) else pt)
                                           for pt in self._price_history[pos.asset]]
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
            # Pre-existing latent bug: `logger` is undefined in this module
            # (only `risk_log`/`audit` are imported), so this fail-open path
            # actually raised NameError → caught upstream as a CORRELATION
            # evaluation error → reject. Use risk_log so v2 correlation truly
            # fails open as intended.
            risk_log.warning("Correlation v2 check failed (fail-open): %s", _corr_exc)

        return None

    def _live_hardening(self) -> bool:
        """True when the stricter live risk posture is active — i.e. the operator
        opted in (live_risk_hardening_enabled) AND we are running live. In paper
        / backtest it is always False, so paper behaviour is never affected.
        Fail-safe: any error returns False (no hardening) rather than raising."""
        try:
            return bool(CONFIG.risk.live_risk_hardening_enabled) and CONFIG.is_live()
        except Exception:
            return False

    def _effective_max_drawdown_pct(self) -> float:
        """The max-drawdown limit in force: the tighter live cap when live
        hardening is active, otherwise the standard paper limit.

        On live, an admin may temporarily override the live cap at runtime
        (RUNTIME.live_drawdown_override_pct) — e.g. to keep testing live after
        the account has drawn down past the default. The override is bounded in
        its setter (never disables the breaker) and only ever consulted on live.
        Paper/backtest are unaffected. Fail-safe: any error falls back to the
        configured live cap."""
        if self._live_hardening():
            try:
                from bot.config import RUNTIME
                override = RUNTIME.live_drawdown_override_pct
                if override is not None:
                    return float(override)
            except Exception:
                pass
            return CONFIG.risk.live_max_drawdown_pct
        return CONFIG.risk.max_drawdown_pct

    def _correlation_size_factor(self, idea: TradeIdea) -> float:
        """Graduated size reduction for correlated, same-direction stacking.

        Returns a multiplier in ``[correlation_sizing_floor, 1.0]``. The new
        trade's size is reduced by ``correlation_sizing_step`` for EACH
        already-open position that shares the same correlation group AND trade
        direction. Rationale: the count-cap in ``_check_correlation`` either
        rejects or fully admits a trade, but the marginal portfolio risk of
        piling a second/third *correlated, same-side* bet on top of existing ones
        is larger than the first — so those are sized down rather than admitted at
        full size. The shared unmapped-alt bucket is excluded (its members are not
        all mutually correlated, so co-membership isn't a concentrated bet).
        Floored so size is never cut below the configured fraction. Fail-open: any
        error returns 1.0 (no reduction) so this can never block a trade.
        """
        try:
            new_group = self._correlation_group(idea.asset)
            if new_group == _UNMAPPED_GROUP:
                return 1.0
            new_dir = idea.direction.value if hasattr(idea.direction, "value") else str(idea.direction)
            same = 0
            for pos in self._portfolio.open_positions:
                if self._correlation_group(pos.asset) != new_group:
                    continue
                pos_dir = pos.direction.value if hasattr(pos.direction, "value") else str(pos.direction)
                if pos_dir == new_dir:
                    same += 1
            if same <= 0:
                return 1.0
            step = CONFIG.risk.correlation_sizing_step
            floor = CONFIG.risk.correlation_sizing_floor
            return max(floor, 1.0 - step * same)
        except Exception as exc:
            risk_log.warning("Correlation sizing failed (fail-open): %s", exc)
            return 1.0

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
            self._circuit_trip_cause = data.get("circuit_trip_cause", "")
            self._circuit_trip_day = data.get("circuit_trip_day", "")
            self._restore_dd_override(data)
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
            # Persist the rename itself (not just the tmp contents) so the
            # circuit-breaker state survives a crash/power loss. Best-effort.
            fsync_dir(self._state_file)
        except Exception as exc:
            # Log save failure -- circuit breaker state is safety-critical
            audit(risk_log, f"Failed to persist risk state: {exc}",
                  action="save_state", result="ERROR")

    def _export_state_dict(self) -> dict:
        """C2-34: Extract risk state as a dict without writing to disk."""
        # Persist the admin live-drawdown override so it survives restarts —
        # an operator who loosened the cap via /drawdownlimit to keep testing
        # live should not have it silently snap back to the default the next
        # time the bot is redeployed. Best-effort; None when unset.
        _dd_override = None
        try:
            from bot.config import RUNTIME
            _dd_override = RUNTIME.live_drawdown_override_pct
        except Exception:
            pass
        return {
            "circuit_open": self._circuit_open,
            "consecutive_losses": self._consecutive_losses,
            "last_loss_time": self._last_loss_time,
            "circuit_breaker_trips": self._circuit_breaker_trips,
            "circuit_trip_cause": self._circuit_trip_cause,
            "circuit_trip_day": self._circuit_trip_day,
            "live_drawdown_override_pct": _dd_override,
            "saved_at": datetime.now(UTC).isoformat(),
        }

    @staticmethod
    def _restore_dd_override(data: dict) -> None:
        """Reapply a persisted admin live-drawdown override to RUNTIME on load.
        The setter re-clamps into the safe band, so a tampered/out-of-range
        value can never disable the breaker. Best-effort."""
        try:
            from bot.config import RUNTIME
            val = data.get("live_drawdown_override_pct")
            RUNTIME.live_drawdown_override_pct = val  # setter handles None + clamp
        except Exception:
            pass

    def _load_from_state_dict(self, data: dict) -> None:
        """C2-34: Restore risk state from a dict (no file I/O).
        Uses fail-closed semantics matching _load_state."""
        self._circuit_open = data.get("circuit_open", False)
        self._consecutive_losses = data.get("consecutive_losses", 0)
        self._last_loss_time = data.get("last_loss_time")
        self._circuit_breaker_trips = data.get("circuit_breaker_trips", 0)
        self._circuit_trip_cause = data.get("circuit_trip_cause", "")
        self._circuit_trip_day = data.get("circuit_trip_day", "")
        self._restore_dd_override(data)
        if self._circuit_open:
            audit(risk_log, "Circuit breaker state restored from combined state: ACTIVE",
                  action="state_restore", result="LOADED")

    @staticmethod
    def _soft_loss_streak_limit(hard_limit) -> int:
        """Soft loss-streak warning threshold (risk check #9): fires ~2 losses
        before the hard circuit-breaker limit, but ALWAYS strictly below it.

        The old `max(2, hard - 2)` equalled or EXCEEDED the hard limit when it
        was configured <= 2 (e.g. hard=1 → soft=2 > hard; hard=2 → soft=2 = hard),
        so the soft warning could never fire before the breaker (or fired at the
        same count). For hard <= 1 the breaker dominates, so the soft limit just
        equals it. Default config (hard=5 → soft=3) is unchanged."""
        hard = max(1, int(hard_limit))
        if hard < 2:
            return hard
        return min(max(2, hard - 2), hard - 1)

    @staticmethod
    def _position_within_cap(position_usd: float, sizing_equity: float,
                             max_position_pct: float) -> tuple[bool, float]:
        """Check #2 invariant: is the committed margin within the per-trade cap
        (max_position_pct of equity)? Returns (ok, margin_pct).

        position_usd is clamped to this same cap just before check #2, so in
        normal operation this passes — but if that clamp is ever bypassed or
        miscomputed, ok=False rejects the trade (fail-closed) instead of waving
        an oversized position through. Uses a float epsilon so a value exactly
        at the cap passes. (max_position_pct is the per-TRADE cap; the per-SYMBOL
        aggregate limit is max_symbol_exposure_pct, checked separately.)"""
        if sizing_equity <= 0:
            return False, 0.0
        margin_pct = position_usd / sizing_equity * 100
        return (margin_pct < max_position_pct + 1e-9), margin_pct

    def _trip_circuit_breaker(self, reason: str, cause: str = "manual") -> None:
        if not self._circuit_open:
            self._circuit_open = True
            self._circuit_breaker_trips += 1
            # Record the owning cause + UTC day so a daily-loss trip can
            # auto-reset at rollover while drawdown/streak/manual stay manual.
            self._circuit_trip_cause = cause
            self._circuit_trip_day = datetime.now(UTC).strftime("%Y-%m-%d")
            audit(risk_log, f"CIRCUIT BREAKER TRIPPED: {reason}",
                  action="circuit_breaker", result="HALTED",
                  data={"cause": cause, "day": self._circuit_trip_day})
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
            self._circuit_trip_cause = ""
            self._circuit_trip_day = ""
            # Re-seed the live drawdown high-water mark from the NEXT live
            # evaluation's current equity. Without this a peak corrupted by a
            # transient too-high equity reading (e.g. a stale/paper-fallback
            # balance during an auth blip) kept current-drawdown pinned above the
            # cap, so the breaker re-tripped on the very next evaluate() and a
            # manual /resume never stuck — the "still halted after reset" report.
            self._live_equity_peak = 0.0
            # ALSO clear the DAILY-LOSS condition, for the same reason: a manual
            # reset is a deliberate operator override, but the day's realized
            # loss stays on the books, so the daily-loss check re-trips on the
            # very next evaluate() ("after breaker reset it keeps falling back").
            # Re-seed the LIVE daily-PnL accumulator to a fresh budget for the
            # current UTC day and clear the cached loss %, mirroring the peak
            # re-seed. Losses that accrue after the reset accumulate from zero and
            # will trip the breaker again — so protection is refreshed, not lost.
            self._live_daily_pnl = 0.0
            self._live_daily_day = time.strftime("%Y-%m-%d", time.gmtime(int(self._now())))
            self._last_known_daily_loss_pct = 0.0
            audit(risk_log, "Circuit breaker manually reset (live peak + daily-loss re-seeded)",
                  action="circuit_breaker", result="RESET")
            self._save_state()

    def reset_performance_window(self) -> None:
        """Clear the live-performance governor's rolling realized-PnL window.

        The governor de-risks off recent CLOSED-trade outcomes; that window is
        deliberately lagging. After a manual intervention or a config change
        that invalidates the recent history (or in the red-team harness, to
        isolate one scenario from another's closes), an operator can wipe it so
        the governor restarts from a clean warm-up instead of penalising future
        trades for a superseded losing streak. Does not touch the circuit
        breaker or consecutive-loss counter."""
        with self._lock:
            self._realized_pnl_window.clear()

    def drawdown_status(self) -> dict:
        """Read-only snapshot for operator control: current drawdown %, the
        effective live/paper max-drawdown limit in force, and whether a runtime
        override is active. Best-effort; returns empty on any error."""
        try:
            state = self._portfolio.snapshot()
            from bot.config import RUNTIME
            return {
                # "drawdown_pct" is the LIVE (recoverable) drawdown the breaker
                # actually gates on; max_drawdown_pct is the monotonic worst-ever.
                "drawdown_pct": float(getattr(state, "current_drawdown_pct", state.max_drawdown_pct)),
                "max_drawdown_pct": float(state.max_drawdown_pct),
                "effective_limit_pct": float(self._effective_max_drawdown_pct()),
                "config_live_limit_pct": float(CONFIG.risk.live_max_drawdown_pct),
                "override_pct": RUNTIME.live_drawdown_override_pct,
                "live_hardening": self._live_hardening(),
            }
        except Exception:
            return {}

    def pending_retrip_reason(self) -> Optional[str]:
        """Why the just-reset breaker would RE-TRIP on the next evaluation, or
        None if it would stay clear.

        A manual /resume clears the breaker flag, but the daily-loss and
        drawdown checks re-trip it the moment the next trade is evaluated if
        their underlying condition still holds — the operator then sees a
        'BOT RESUMED / breaker CLEAR' card immediately contradicted by a
        'Paused' status. This mirrors evaluate()'s conditions read-only so the
        resume card can warn honestly instead. Best-effort: returns None on
        any error.
        """
        try:
            state = self._portfolio.snapshot()
            base = state.equity_usd
            if base and base > 0 and state.daily_pnl < 0:
                daily_loss_pct = abs(state.daily_pnl / base * 100)
                if daily_loss_pct >= CONFIG.risk.max_daily_loss_pct:
                    return (f"daily loss {daily_loss_pct:.1f}% still >= "
                            f"{CONFIG.risk.max_daily_loss_pct}% limit — the breaker "
                            f"re-trips on the next trade check until equity recovers "
                            f"or the UTC day rolls over")
            _max_dd = self._effective_max_drawdown_pct()
            _cur_dd = getattr(state, "current_drawdown_pct", state.max_drawdown_pct)
            if _cur_dd >= _max_dd:
                return (f"drawdown {_cur_dd:.1f}% still >= "
                        f"{_max_dd}% limit — the breaker re-trips on the next "
                        f"trade check until equity recovers")
        except Exception:
            return None
        return None

    @staticmethod
    def _should_autoreset_daily_breaker(circuit_open: bool, cause: str, trip_day: str,
                                        today: str, enabled: bool,
                                        streak: int, max_streak: int) -> bool:
        """Whether a tripped breaker should auto-reset at day rollover.

        Applies ONLY to a daily-loss-caused trip, once the UTC day has rolled
        over past the trip day, and only when a loss-streak block is not itself
        active (so we never resume into a maxed streak). Drawdown / streak /
        manual trips never auto-reset — they require human intervention."""
        return bool(
            enabled and circuit_open and cause == "daily_loss"
            and trip_day and today and trip_day != today
            and streak < max_streak
        )
