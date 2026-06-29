"""
RUNECLAW Trading Engine -- the central orchestrator.
FSM States: IDLE -> SCANNING -> ANALYZING -> RISK_CHECK -> CONFIRMING -> EXECUTING -> MONITORING
Fail-closed: any unhandled error aborts the trade pipeline.
Human confirmation is REQUIRED before execution.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime
from bot.compat import UTC
from typing import Callable, Optional

from pathlib import Path

from bot.config import CONFIG
from bot.core.analyzer import Analyzer
from bot.core.cost import CostTracker
from bot.core.system_health import SystemHealthMonitor
from bot.core.exchange_flow import ExchangeFlowProvider
from bot.core.macro_events import MacroEventProvider
from bot.core.live_executor import LiveExecutor, normalize_symbol
from bot.core.exchange_sync import sync_portfolio_with_exchange, get_exchange_position_count, invalidate_position_count_cache
from bot.core.market_scanner import MarketScanner, _classify_symbol
from bot.core.order_flow import OrderFlowAnalyzer
from bot.core.ws_feed import BitgetWSFeed
from bot.compliance.compliance_engine import ComplianceEngine, Permission, default_demo_profile
from bot.learning.orchestrator import LearningOrchestrator
from bot.macro.calendar import MacroCalendar, build_2026_calendar
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.risk.multi_portfolio import MultiUserPortfolio
from bot.core.dashboard_pusher import DashboardPusher
from bot.utils.audit_chain import AuditChain, DecisionRecord
from bot.utils.logger import audit, system_log, trade_log, scan_log
from bot.utils.models import (
    AgentState,
    MarketSignal,
    RiskVerdict,
    StateTransition,
    TradeIdea,
)
from bot.core.smart_exits import TimeOfDayEdge, AdaptiveLimitDistance

logger = logging.getLogger(__name__)


class RuneClawEngine:
    """
    Main event loop that ties scanner, analyzer, risk, and execution together.
    Uses a formal FSM via AgentState for every lifecycle transition.
    The engine never executes a trade without explicit human confirmation.
    """

    def __init__(self) -> None:
        self.portfolio = PortfolioTracker()
        self.scanner = MarketScanner()
        self.cost = CostTracker()
        self.analyzer = Analyzer(cost_tracker=self.cost)
        # Learning auto-refit: keeps calibration/voter/expectancy learners fresh
        # as closed outcomes accrue (gated by CONFIG.analyzer.learning_auto_refit_*).
        from bot.learning.auto_refit import LearningAutoRefit
        self._auto_refit = LearningAutoRefit(CONFIG.analyzer.learning_auto_refit_interval)
        self.order_flow = OrderFlowAnalyzer()
        # Exchange flow provider: real-time funding rates + OI from Bitget
        self.exchange_flow = ExchangeFlowProvider(
            exchange_factory=self.scanner._get_exchange,
        )
        self.macro_calendar = MacroCalendar(
            events=build_2026_calendar(),
            fail_closed_when_stale=CONFIG.risk.macro_calendar_fail_closed_when_stale,
        )
        self.macro_provider = MacroEventProvider(
            seed_path=Path("config/macro_calendar.seed.json"),
            funding_provider=self.exchange_flow.funding_rate_provider,
        )
        self.risk = RiskEngine(
            self.portfolio,
            macro_calendar=self.macro_calendar,
            macro_provider=self.macro_provider,
        )
        self.compliance = ComplianceEngine()
        self.compliance_profile = default_demo_profile()
        # LIVE FIX: if env-level config enables live trading, auto-grant
        # LIVE_TRADE permission so the bot doesn't require /golive CONFIRM
        # after every restart.  The five-lock compliance engine still enforces
        # all other gates (risk, macro, notional cap, human approval).
        if CONFIG.is_live():
            from bot.compliance.compliance_engine import Permission as _Perm
            self.compliance_profile.permissions.add(_Perm.LIVE_TRADE)
            # RC-AUD-018: live mode armed from environment with NO per-session
            # human action. Emit a prominent one-time startup WARNING (not just
            # info) so the operator/audit trail records that Lock 1 (LIVE_TRADE)
            # was granted for the whole process lifetime by env config alone.
            system_log.warning(
                "LIVE ARMED FROM ENV (RC-AUD-018): LIVE_TRADE permission "
                "auto-granted from env config (SIMULATION_MODE=false, "
                "LIVE_TRADING_ENABLED=true) with no per-session human arming. "
                "Lock 1 is satisfied for the entire process lifetime."
            )
        self.audit_chain = AuditChain("logs/audit_chain.jsonl")
        self.learning = LearningOrchestrator()
        # WebSocket feed for real-time price monitoring (supplements REST polling)
        self.ws_feed = BitgetWSFeed()
        # Live executor for real Bitget orders (micro-test mode). This is the
        # SHARED OPERATOR executor (CONFIG.exchange keys) — the only one used
        # unless per-user live trading is enabled.
        self.live_executor = LiveExecutor()
        # Wire balance cache invalidation + per-symbol SL cooldown
        self.live_executor.on_position_closed = lambda pos: self._on_live_position_closed(pos)
        # Wire risk engine for warning rate circuit breaker
        self.live_executor._risk_engine = self.risk
        # Per-user live executors (PER_USER_LIVE_ENABLED, default OFF): keyed by
        # telegram user_id, each bound to that user's OWN linked Bitget account.
        # Empty + unused while the flag is off, so the operator path is unchanged.
        self._user_executors: dict[str, LiveExecutor] = {}
        self.health = SystemHealthMonitor()
        # Multi-user portfolio manager: per-user isolated paper wallets
        self.user_portfolios = MultiUserPortfolio(
            default_balance=CONFIG.paper_balance_usd,
            on_trade_close=None,  # wired after risk engine init
        )
        # Dashboard pusher — pushes portfolio snapshots to the live dashboard
        self.dashboard_pusher = DashboardPusher(self)
        # C1 fix: wire trade-close callback so portfolio closes feed risk streak tracking
        # Also sync trade events to the website dashboard
        def _on_trade_close_composite(net_pnl: float) -> None:
            self.risk.record_trade_result(net_pnl)
            # Auto-sync to website dashboard
            try:
                from bot.utils.website_sync import sync_in_background
                state = self.portfolio.snapshot()
                sync_in_background(
                    user_id=1,  # default user; multi-user resolves via telegram handler
                    equity=state.equity_usd,
                    positions=list(self.portfolio.open_positions),
                    closed_trades=list(self.portfolio._history[-50:]),
                )
            except Exception as exc:
                # C2-52 FIX: log website sync errors instead of silently swallowing
                logger.warning("Website sync failed: %s", exc)
        self.portfolio._on_trade_close = _on_trade_close_composite
        self.user_portfolios._on_trade_close = self.risk.record_trade_result
        # C2-34: Wire combined state saver for atomic portfolio+risk persistence.
        # Both components delegate their saves to this function, which writes
        # a single combined_state.json via fsync + os.replace.
        self._combined_state_file = os.path.join(
            os.path.dirname(self.portfolio._state_file) or "data",
            "combined_state.json"
        )
        self._wire_combined_state_saver()
        self.state: AgentState = AgentState.IDLE
        self._state_history: list[StateTransition] = []
        self._max_state_history = 1000  # F-13 FIX: cap state history
        self._running = False
        self._confirm_callback: Optional[Callable] = None
        self._close_notify_callback: Optional[Callable] = None
        self._fill_notify_callback: Optional[Callable] = None
        self._adopt_notify_callback: Optional[Callable] = None
        self._auto_confirm_notify_callback: Optional[Callable] = None
        self._pending_ideas: dict[str, TradeIdea] = {}
        self._last_confirmed_idea: Optional[TradeIdea] = None
        self._pending_atr: dict[str, Optional[float]] = {}  # H1: store ATR for re-check
        self._pending_pyramid: dict[str, bool] = {}  # Track pyramid add flags
        self._user_store = None  # Set by TelegramHandler for role-based execution
        self._cooldown_until: float = 0.0
        # Per-symbol cooldown after SL hit — prevents immediate re-entry
        self._symbol_cooldowns: dict[str, float] = {}  # symbol_key -> monotonic expiry
        self._symbol_cooldown_seconds: float = float(os.environ.get("SYMBOL_SL_COOLDOWN_SEC", "1800"))  # 30 min default
        self._last_rebalance_check: float = 0.0  # monotonic timestamp
        self._rebalance_interval: float = 4 * 3600  # 4 hours minimum between checks
        # /whynot: store last RiskCheck per symbol when risk rejects a trade
        self._last_rejections: dict[str, dict] = {}
        self._last_scan_signals: list = []
        self._ohlcv_cache: dict[str, tuple[float, list]] = {}
        # M-13 FIX: live balance cache as instance attributes (not class-level mutables)
        self._live_balance_cache: dict = {}
        self._live_balance_cache_ts: float = 0.0
        # Consecutive engine-tick failures, mirrored from the run loop so the
        # proactive monitor can alert when the main loop is degraded/unmonitored.
        self._tick_consecutive_failures: int = 0
        # Throttle for the periodic SL/TP self-heal (re-place stops that went
        # missing DURING operation, not just at startup). monotonic seconds.
        self._last_sltp_verify_ts: float = 0.0
        self._SLTP_VERIFY_INTERVAL: float = 300.0  # 5 minutes
        self._LIVE_BALANCE_TTL: float = 30.0  # cache live balance for 30 seconds
        # H-05 FIX: track last known valid prices for WS sanity checks
        self._last_known_prices: dict[str, float] = {}
        # Watchdog: track when the FSM last changed state so _tick() can
        # detect and recover from stuck non-IDLE states.
        self._last_state_change: float = time.time()

        # Cross-asset correlation tracker
        from bot.core.cross_asset import CrossAssetTracker
        self.cross_asset = CrossAssetTracker()

        # Slippage tracker. Wire it into the operator executor so realized
        # slippage (intended entry vs actual fill) is actually recorded — the
        # executor's record() call is a no-op until _slippage_tracker is set.
        from bot.core.slippage import SlippageTracker
        self.slippage = SlippageTracker()
        self.live_executor._slippage_tracker = self.slippage

        # Trade journal
        from bot.core.trade_journal import TradeJournal
        self.journal = TradeJournal()

        # Time-of-day edge filter
        self.time_of_day = TimeOfDayEdge()

        # Adaptive limit distance learner
        self.adaptive_limits = AdaptiveLimitDistance()

        # Hold-time analytics tracker
        from bot.core.smart_exits import HoldTimeAnalytics
        self.hold_analytics = HoldTimeAnalytics()

        # VWAP cache for VWAP reversion exits
        self._last_vwap: dict[str, float] = {}

        # Smart scan scheduling
        self._last_scan_time: float = 0.0
        self._current_scan_interval: float = CONFIG.scan_interval_seconds
        self._recent_atr_values: dict[str, float] = {}  # symbol -> latest ATR

    # -- State management --

    def _compute_smart_scan_interval(self) -> float:
        """Dynamically adjust scan interval based on market volatility.

        High volatility → scan more frequently (min interval)
        Low volatility → scan less frequently (max interval)
        """
        if not CONFIG.adaptive.smart_scan_enabled:
            return CONFIG.scan_interval_seconds

        if not self._recent_atr_values:
            return CONFIG.scan_interval_seconds

        min_interval = CONFIG.adaptive.smart_scan_min_interval
        max_interval = CONFIG.adaptive.smart_scan_max_interval
        base = CONFIG.scan_interval_seconds

        # Count how many symbols have "hot" ATR (above their recent average)
        hot_symbols = 0
        for symbol, atr_pct in self._recent_atr_values.items():
            if atr_pct > 0.03:  # ATR > 3% of price = high vol
                hot_symbols += 1

        if hot_symbols >= 3:
            # Multiple volatile symbols = market-wide event, scan fast
            interval = min_interval
        elif hot_symbols >= 1:
            # Some volatility, moderate speed
            interval = base * 0.5
        else:
            # Quiet market, slow down
            interval = min(max_interval, base * 1.5)

        interval = max(min_interval, min(max_interval, interval))

        if abs(interval - self._current_scan_interval) > 10:
            audit(system_log,
                  f"Smart scan interval: {self._current_scan_interval:.0f}s \u2192 {interval:.0f}s "
                  f"(hot_symbols={hot_symbols})",
                  action="smart_scan", result="ADJUSTED")

        self._current_scan_interval = interval
        return interval

    async def get_exchange(self, category: str = "Crypto"):
        """Public accessor for the exchange instance (for skills that need OHLCV).

        Args:
            category: Asset category — "Crypto" uses spot, anything else uses futures.
        """
        if category != "Crypto":
            return await self.scanner._get_futures_exchange()
        return await self.scanner._get_exchange()

    async def get_futures_exchange(self):
        """Public accessor for the futures exchange instance."""
        return await self.scanner._get_futures_exchange()

    # -- Live equity cache --

    # _live_balance_cache, _live_balance_cache_ts, and _LIVE_BALANCE_TTL
    # are initialised in __init__() as instance attributes (M-13 fix).

    async def get_live_equity(self) -> Optional[dict]:
        """Fetch real exchange balance in LIVE mode (cached).

        Returns dict with 'equity', 'free', 'used', 'holdings' or None if
        not in live mode or fetch fails.
        """
        if not CONFIG.is_live():
            return None
        now = time.monotonic()
        if (now - self._live_balance_cache_ts) < self._LIVE_BALANCE_TTL and self._live_balance_cache:
            return self._live_balance_cache
        try:
            bal = await self.live_executor.fetch_balance()
            if "error" not in bal or bal.get("total", 0) > 0:
                self._live_balance_cache = bal
                self._live_balance_cache_ts = now
                return bal
        except Exception as exc:
            # C2-55 FIX: log staleness so risk calculation accuracy is visible
            age_s = time.monotonic() - self._live_balance_cache_ts
            if self._live_balance_cache:
                system_log.warning(
                    "Live balance fetch failed (%s) — returning cached value (%.1fs old)",
                    exc, age_s,
                )
                if age_s > 300:
                    system_log.error("Balance cache is >5m stale — risk calculations may be wrong")
            else:
                system_log.debug("Live balance fetch failed (no cache): %s", exc)
        return self._live_balance_cache if self._live_balance_cache else None

    def _invalidate_live_balance_cache(self) -> None:
        """Force a fresh balance fetch on the next equity check."""
        self._live_balance_cache = {}
        self._live_balance_cache_ts = 0.0

    def _on_live_position_closed(self, pos) -> None:
        """Handle live position close: invalidate cache + set SL cooldown."""
        self._invalidate_live_balance_cache()

        # ── Close the learning loop's WRITE side ──────────────────────────
        # Record the realized outcome as a complete, queryable experience record
        # (symbol + direction + regime + pnl). Done ALWAYS (not gated by the
        # adaptive-confidence flag) so history accumulates and is ready the moment
        # an operator opts in. Cheap append; fail-open.
        try:
            _pnl = getattr(pos, "pnl_usd", None)
            if _pnl is not None:
                self.learning.record_closed_outcome(
                    symbol=getattr(pos, "symbol", ""),
                    direction=str(getattr(pos, "direction", "") or ""),
                    pnl_result=float(_pnl),
                    market_regime=str(getattr(self.risk, "_current_regime", "") or ""),
                    trade_id=getattr(pos, "trade_id", ""),
                )
        except Exception as _lo_exc:
            logger.debug("Learning outcome record skipped: %s", _lo_exc)
        # Auto-refit the learners every N closed outcomes (gated, fail-open).
        # Keeps calibration/voter/expectancy fresh without a manual /calibration
        # refit. Only updates persisted learner state — never changes a decision
        # unless the learners' own application flags are on.
        try:
            if CONFIG.analyzer.learning_auto_refit_enabled:
                self._auto_refit.note_closed_trade(getattr(self, "analyzer", None))
        except Exception as _ar_exc:
            logger.debug("Learning auto-refit skipped: %s", _ar_exc)
        # If closed adversely (SL / stop / liquidation), set a per-symbol cooldown
        # to prevent immediate re-entry.  A liquidation ("LIQUIDATED") is the most
        # adverse close of all, so it must arm the cooldown too.
        close_reason = getattr(pos, "close_reason", "") or ""
        _cr = close_reason.upper()
        if "SL" in _cr or "STOP" in _cr or "LIQUID" in _cr:
            sym_key = normalize_symbol(getattr(pos, "symbol", ""))
            if sym_key:
                self._symbol_cooldowns[sym_key] = (
                    time.monotonic() + self._symbol_cooldown_seconds
                )
                logger.info(
                    "Symbol cooldown set: %s blocked for %ds after SL hit",
                    sym_key, int(self._symbol_cooldown_seconds))

    def _executor_for(self, user_id: str = ""):
        """Return the LiveExecutor that should place THIS caller's live order.

        Default (PER_USER_LIVE_ENABLED off): ALWAYS the shared operator executor
        — byte-identical to before. When per-user live trading is enabled AND the
        caller is a real human user (not '' / 'auto') who has linked + decryptable
        keys, returns that user's OWN executor (created lazily, cached, rebuilt if
        the user's key changes). If per-user is on but the user has no usable
        credentials, falls back to the operator executor so behaviour never
        silently breaks — eligibility enforcement is a Phase 5 access-policy
        concern layered on top, not here.
        """
        if not getattr(CONFIG, "per_user_live_enabled", False):
            return self.live_executor
        # Auto-trade ('auto') and unattended ('') paths run on the operator
        # account, not an individual user's.
        if not user_id or user_id in ("auto", ""):
            return self.live_executor
        try:
            from bot.core.exchange_credentials import get_credential_store
            creds = get_credential_store().get(user_id)
        except Exception as exc:
            logger.warning("Per-user executor: credential lookup failed for %s: %s "
                           "— using operator executor", user_id, exc)
            creds = None
        if not creds:
            return self.live_executor
        key = str(user_id)
        ex = self._user_executors.get(key)
        # Rebuild if absent or the user's api_key changed (e.g. re-/connect).
        if ex is None or (ex._credentials or {}).get("api_key") != creds.get("api_key"):
            ex = LiveExecutor(user_id=user_id, credentials=creds)
            ex.on_position_closed = lambda pos: self._on_live_position_closed(pos)
            ex._risk_engine = self.risk
            # Record realized slippage into the shared tracker (no-op until set).
            ex._slippage_tracker = getattr(self, "slippage", None)
            self._user_executors[key] = ex
            audit(system_log, f"Per-user live executor bound for user {user_id}",
                  action="per_user_executor", result="BOUND", data={"user": key})
        return ex

    def invalidate_user_executor(self, user_id: str) -> None:
        """Drop any cached per-user executor (e.g. after /connect or /disconnect)
        so the next trade rebuilds it from the current stored credentials. Safe to
        call when none exists. Never touches the shared operator executor."""
        self._user_executors.pop(str(user_id), None)

    def _is_operator_user(self, user_id) -> bool:
        """True if this user trades on the OPERATOR account — i.e. an admin or a
        member of the operator/admin env allowlist. A regular user is NOT an
        operator and, under per-user live trading, must link their own keys.
        """
        uid = str(user_id)
        for raw in (CONFIG.telegram.chat_id, CONFIG.telegram.admin_ids):
            if raw and uid in {s.strip() for s in str(raw).split(",") if s.strip()}:
                return True
        store = getattr(self, "_user_store", None)
        if store is not None:
            try:
                u = store.get(uid)
                if u and u.get("role") == "admin":
                    return True
            except Exception:
                pass
        return False

    def per_user_live_eligibility(self, user_id) -> tuple:
        """Whether THIS human user's confirmed live trade may execute, and why.

        Returns ``(ok, reason)``. Only meaningful while PER_USER_LIVE_ENABLED is
        on and for a real human confirm (not 'auto'/''). The rule: an operator/
        admin trades on the operator account (always ok); a regular user must
        have their OWN linked, decryptable keys — otherwise their trade is
        REJECTED rather than silently placed on the operator's account.
        """
        if not getattr(CONFIG, "per_user_live_enabled", False):
            return True, "per-user live trading disabled (operator account)"
        if not self._human_confirmed(user_id):
            return True, "operator/auto path"
        if self._is_operator_user(user_id):
            return True, "operator/admin user"
        try:
            from bot.core.exchange_credentials import get_credential_store
            if get_credential_store().get(user_id):
                return True, "user has linked keys"
        except Exception as exc:
            return False, f"credential lookup failed: {exc}"
        return False, "no linked Bitget account — use /connect to link one"

    def _all_live_executors(self) -> list:
        """The shared operator executor plus every active per-user executor.

        Monitoring/reconciliation loops iterate this so every account's open
        positions get SL/TP enforcement and reconciliation. With per-user live
        trading off (default) ``_user_executors`` is empty, so this is just
        ``[operator]`` and every loop runs exactly as it did before.
        """
        return [self.live_executor, *self._user_executors.values()]

    def _rehydrate_user_executors(self) -> None:
        """Rebuild per-user executors for all linked users at startup so their
        PERSISTED live positions resume being monitored after a restart (per-user
        executors are otherwise created lazily on the next trade). No-op when
        per-user live trading is off, so the operator path is unchanged.
        """
        if not getattr(CONFIG, "per_user_live_enabled", False):
            return
        try:
            from bot.core.exchange_credentials import get_credential_store
            ids = get_credential_store().user_ids()
        except Exception as exc:
            logger.warning("Per-user executor rehydrate skipped: %s", exc)
            return
        for uid in ids:
            try:
                # _executor_for builds, caches, and (via __init__) loads that
                # user's persisted positions; skips users with no usable keys.
                self._executor_for(uid)
            except Exception as exc:
                logger.warning("Rehydrate executor for %s failed: %s", uid, exc)
        if self._user_executors:
            audit(system_log,
                  f"Rehydrated {len(self._user_executors)} per-user executor(s) at startup",
                  action="per_user_rehydrate", result="OK")

    def get_effective_equity(self, user_id: str = "") -> float:
        """Return the equity figure to display/use for sizing.

        In LIVE mode: returns cached live exchange equity (USDT balance).
        In PAPER mode: returns the user's paper portfolio equity.
        """
        if CONFIG.is_live() and self._live_balance_cache:
            return self._live_balance_cache.get("total", 0.0)
        portfolio = self.user_portfolios.get(user_id) if user_id else self.portfolio
        return portfolio.snapshot().equity_usd

    async def get_effective_equity_async(self, user_id: str = "") -> float:
        """Async version that fetches live balance if cache is empty.

        Use this in Telegram command handlers to ensure fresh data.
        """
        if CONFIG.is_live():
            if not self._live_balance_cache:
                await self.get_live_equity()
            if self._live_balance_cache:
                return self._live_balance_cache.get("total", 0.0)
        portfolio = self.user_portfolios.get(user_id) if user_id else self.portfolio
        return portfolio.snapshot().equity_usd

    # -- C2-34: Combined State Persistence --

    def _wire_combined_state_saver(self) -> None:
        """Set up atomic combined state persistence.

        On first boot: if combined_state.json exists, load from it.
        Otherwise, if portfolio already loaded from legacy files, write combined.
        Wire both portfolio and risk_engine to use the combined saver.

        Skips loading when persistence is not active (e.g. in tests where
        portfolio is created fresh with no state file on disk).
        """
        import json as _json
        combined_path = Path(self._combined_state_file)

        # Only load/migrate if persistence is active (production mode) or
        # a combined state file exists from a prior run.
        if not self.portfolio._persistence_active and not combined_path.exists():
            # No persistence — just wire the saver for future use
            self.portfolio._combined_saver = self._save_combined_state
            self.risk._combined_saver = self._save_combined_state
            return

        if combined_path.exists():
            # Load from combined state file
            try:
                with open(combined_path) as f:
                    raw = f.read()
                if raw.strip():
                    combined = _json.loads(raw)
                    if "portfolio" in combined:
                        self.portfolio._load_from_state_dict(combined["portfolio"])
                        self.portfolio._persistence_active = True
                    if "risk" in combined:
                        self.risk._load_from_state_dict(combined["risk"])
                    system_log.info(
                        "C2-34: Loaded combined state (v%s, saved %s)",
                        combined.get("version", "?"),
                        combined.get("written_at", "?"),
                    )
            except Exception as exc:
                # Combined file corrupt — fall back to individual files
                # (which were already loaded by each component's __init__)
                system_log.warning(
                    "C2-34: Combined state corrupt (%s), using individual files",
                    exc,
                )
        else:
            # Legacy migration: individual files were already loaded by
            # portfolio.__init__ and risk_engine.__init__. Write the combined
            # file so subsequent boots use it.
            if self.portfolio._persistence_active:
                try:
                    self._save_combined_state()
                    system_log.info(
                        "C2-34: Migrated legacy state files to combined_state.json"
                    )
                except Exception as exc:
                    system_log.warning(
                        "C2-34: Migration write failed (%s), will retry on next save",
                        exc,
                    )

        # Wire both components to use combined saver
        self.portfolio._combined_saver = self._save_combined_state
        self.risk._combined_saver = self._save_combined_state

    def _save_combined_state(self) -> None:
        """Atomically write portfolio + risk state to a single file.
        Called by either portfolio._auto_save() or risk._save_state()
        whenever either component's state changes."""
        import json as _json
        combined = {
            "version": 1,
            "portfolio": self.portfolio._export_state_dict(),
            "risk": self.risk._export_state_dict(),
            "written_at": datetime.now(UTC).isoformat(),
        }
        combined_path = Path(self._combined_state_file)
        combined_path.parent.mkdir(parents=True, exist_ok=True)
        # Keep one backup
        if combined_path.exists():
            backup = combined_path.with_suffix(".json.bak")
            try:
                import shutil
                shutil.copy2(str(combined_path), str(backup))
            except Exception:
                pass  # best-effort
        tmp = str(combined_path) + ".tmp"
        with open(tmp, "w") as f:
            _json.dump(combined, f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(combined_path))

    def _transition(self, new_state: AgentState, reason: str = "") -> None:
        """Transition the FSM to a new state. Every transition is audit-logged."""
        old_state = self.state
        transition = StateTransition(
            from_state=old_state,
            to_state=new_state,
            reason=reason,
        )
        self._state_history.append(transition)
        # L7: cap state history to prevent unbounded growth
        if len(self._state_history) > 1000:
            self._state_history = self._state_history[-500:]
        self.state = new_state
        self._last_state_change = time.time()
        audit(
            system_log,
            f"State transition: {old_state.value} -> {new_state.value}"
            + (f" ({reason})" if reason else ""),
            action="state_transition",
            data={"from": old_state.value, "to": new_state.value, "reason": reason},
        )

    @property
    def state_history(self) -> list[StateTransition]:
        """Full history of state transitions."""
        return self._state_history

    def set_confirmation_callback(self, cb: Callable) -> None:
        """Register the human-confirmation gate (e.g. Telegram inline keyboard)."""
        self._confirm_callback = cb

    def set_close_notify_callback(self, cb: Callable) -> None:
        """Register a callback to notify users when a trade is closed."""
        self._close_notify_callback = cb

    def set_fill_notify_callback(self, cb: Callable) -> None:
        """Register a callback to notify users when a limit order is filled (opened)."""
        self._fill_notify_callback = cb

    def set_adopt_notify_callback(self, cb: Callable) -> None:
        """Register a callback to notify users when an exchange position is adopted."""
        self._adopt_notify_callback = cb

    def set_auto_confirm_notify_callback(self, cb: Callable) -> None:
        """Register a callback to notify when a trade is auto-confirmed."""
        self._auto_confirm_notify_callback = cb

    # -- Main loop --

    async def run(self) -> None:
        """Start the continuous scan-analyze-monitor loop."""
        self._running = True
        self._transition(AgentState.IDLE, "engine started")
        audit(
            system_log,
            "Engine started",
            action="start",
            data={"simulation": CONFIG.simulation_mode},
        )
        # Start WebSocket feed for real-time price monitoring
        try:
            await self.ws_feed.start()
        except Exception as e:
            system_log.warning("WebSocket feed failed to start: %s", e)
        # Start dashboard pusher
        try:
            await self.dashboard_pusher.start()
        except Exception as e:
            system_log.warning("Dashboard pusher failed to start: %s", e)
        # Subscribe to core symbols so the WS connection stays alive
        # even when no positions are open.  Position-specific symbols
        # are added dynamically in _check_open_positions().
        self.ws_feed.subscribe(["BTC/USDT", "ETH/USDT", "SOL/USDT"])

        # Startup reconciliation: sync local state with exchange before
        # accepting any new signals. Catches positions closed/opened
        # during downtime or crashes.
        if CONFIG.is_live():
            # Rebuild per-user executors so their persisted positions are
            # reconciled/monitored from startup (no-op while per-user is off).
            self._rehydrate_user_executors()
            # Reconcile every account (operator + any per-user). With per-user
            # off this loops once over the operator — identical to before.
            for _ex in self._all_live_executors():
                try:
                    reconciled = await _ex.reconcile_positions()
                    for msg in reconciled:
                        audit(trade_log, f"Startup reconcile: {msg}",
                              action="startup_reconcile", result="CLOSED")
                except Exception as exc:
                    audit(system_log, f"Startup reconciliation error: {exc}",
                          action="startup_reconcile", result="ERROR")

                # Startup position sync: ensure tracked leverage and margin mode
                # match exchange reality. Catches manual changes on exchange or
                # mismatches from dynamic leverage not applying.
                try:
                    await _ex.sync_positions_from_exchange()
                except Exception as exc:
                    audit(system_log, f"Startup position sync error: {exc}",
                          action="startup_position_sync", result="ERROR")

                # Startup SL/TP verification: ensure all open positions have
                # SL/TP orders on exchange. Catches cases where SL/TP placement
                # failed silently (margin mode mismatch, precision errors, etc.)
                try:
                    await _ex.verify_and_fix_sltp()
                except Exception as exc:
                    audit(system_log, f"Startup SL/TP verification error: {exc}",
                          action="startup_sltp_verify", result="ERROR")

            # EXCHANGE = SOURCE OF TRUTH: sync the OPERATOR portfolio with the
            # exchange (per-user portfolios are isolated and not part of this
            # operator-level ghost/orphan sweep).
            try:
                sync_msgs = await sync_portfolio_with_exchange(self)
                for msg in sync_msgs:
                    audit(system_log, f"Exchange sync: {msg}",
                          action="startup_exchange_sync", result="SYNCED")
            except Exception as exc:
                audit(system_log, f"Startup exchange sync error: {exc}",
                      action="startup_exchange_sync", result="ERROR")

        # Roadmap P0: exponential backoff on repeated tick failures. Previously a
        # persistent error (exchange outage, auth failure) retried every scan
        # interval forever, hammering the API (ban risk) and masking a degraded
        # state where positions may be unmonitored. Now we back off and escalate.
        _consecutive_failures = 0
        _BACKOFF_CAP_S = 300.0
        while self._running:
            try:
                await self._tick()
                _consecutive_failures = 0
                self._tick_consecutive_failures = 0
            except Exception as exc:
                _consecutive_failures += 1
                self._tick_consecutive_failures = _consecutive_failures
                audit(
                    system_log,
                    f"Engine tick error (#{_consecutive_failures}): {exc}",
                    action="tick",
                    result="ERROR",
                    data={"consecutive_failures": _consecutive_failures},
                )
                # Feed the warning-rate breaker so sustained failures can trip it.
                try:
                    self.risk.record_warning("engine_tick_failure")
                except Exception:
                    pass
                if _consecutive_failures >= 3:
                    audit(
                        system_log,
                        f"Engine tick has failed {_consecutive_failures} times in a row "
                        f"— trading may be degraded/unmonitored",
                        action="tick", result="CRITICAL_CONSECUTIVE_FAILURES",
                        data={"consecutive_failures": _consecutive_failures},
                    )
                # Exponential backoff (2x per failure, capped) instead of a tight retry loop.
                base = self._compute_smart_scan_interval()
                backoff = min(base * (2 ** _consecutive_failures), _BACKOFF_CAP_S)
                await asyncio.sleep(backoff)
                continue
            await asyncio.sleep(self._compute_smart_scan_interval())

    async def stop(self) -> None:
        self._running = False
        await self.ws_feed.stop()
        await self.scanner.close()
        # AUDIT-FIX: Close live executor exchange connection to avoid session leaks
        if hasattr(self, 'live_executor') and self.live_executor:
            await self.live_executor.close()
        # Close any per-user executors' exchange connections too.
        for _ex in list(getattr(self, "_user_executors", {}).values()):
            try:
                await _ex.close()
            except Exception as _close_exc:
                logger.debug("Per-user executor close failed: %s", _close_exc)
        self._transition(AgentState.IDLE, "engine stopped")
        audit(system_log, "Engine stopped", action="stop")

    # -- Pipeline stages --

    async def _tick(self) -> None:
        """One full scan-analyze cycle."""
        # ── Watchdog: force-recover if stuck in a non-IDLE state for >2 minutes ──
        # H-09 FIX: Don't interrupt active trade execution — use longer timeout
        if self.state != AgentState.IDLE and time.time() - self._last_state_change > 120:
            if self.state == AgentState.EXECUTING:
                # Allow up to 300s for active trade execution before forcing IDLE
                if time.time() - self._last_state_change <= 300:
                    pass  # Don't interrupt active trade execution
                else:
                    logger.warning(
                        "State timeout watchdog: stuck in %s for >300s, forcing IDLE",
                        self.state.value,
                    )
                    self._transition(AgentState.IDLE, "state timeout watchdog (executing)")
            else:
                logger.warning(
                    "State timeout watchdog: stuck in %s for >120s, forcing IDLE",
                    self.state.value,
                )
                self._transition(AgentState.IDLE, "state timeout watchdog")

        # Refresh live balance cache if in live mode
        if CONFIG.is_live():
            try:
                await self.get_live_equity()
            except Exception:
                pass  # non-fatal: use cached value

        # Sync WebSocket status to health monitor
        self.health.set_ws_status(self.ws_feed.is_connected())
        # Sync WS heartbeat to live executor so degradation check stays current
        if self.ws_feed.is_connected() and hasattr(self, 'live_executor') and self.live_executor:
            self.live_executor.record_ws_heartbeat()

        # Check circuit breaker — no new scans, but still monitor open positions
        # so SL/TP can fire even while halted (Fix 2: monitoring while halted).
        if self.risk.circuit_breaker_active:
            if self.state != AgentState.HALTED:
                self._transition(AgentState.HALTED, "circuit breaker active")
            await self._check_open_positions()
            return
        elif self.state == AgentState.HALTED:
            self._transition(AgentState.IDLE, "circuit breaker cleared")

        # Check cooldown
        if self._cooldown_until and time.monotonic() < self._cooldown_until:
            if self.state != AgentState.COOLING_DOWN:
                self._transition(AgentState.COOLING_DOWN, "post-loss cooldown active")
            # C2-25 FIX: Still monitor open positions during cooldown — they need
            # SL/TP protection even when new scanning is paused.
            await self._check_open_positions()
            return
        elif self._cooldown_until and time.monotonic() >= self._cooldown_until:
            self._cooldown_until = 0.0

        # TTL: expire stale pending ideas
        now = datetime.now(UTC)
        idea_ttl = CONFIG.pending_idea_ttl
        expired_ids = [
            idea_id
            for idea_id, idea in self._pending_ideas.items()
            if (now - idea.timestamp).total_seconds() > idea_ttl
        ]
        for idea_id in expired_ids:
            expired_idea = self._pending_ideas.pop(idea_id, None)
            self._pending_atr.pop(idea_id, None)  # clean up stored ATR
            self._pending_pyramid.pop(idea_id, None)  # L-02 FIX: clean up pyramid flag
            if expired_idea:
                audit(
                    trade_log,
                    f"Trade idea {idea_id} expired (TTL)",
                    action="ttl_expire",
                    result="EXPIRED",
                    data={"asset": expired_idea.asset, "age_seconds": (now - expired_idea.timestamp).total_seconds()},
                )

        # C2-26 FIX: Skip scanning when ideas are awaiting confirmation.
        # A concurrent confirm_trade call while mid-scan creates a race on
        # shared _pending_ideas state.
        if self._pending_ideas:
            system_log.debug(
                "Skipping scan tick — %d ideas awaiting confirmation",
                len(self._pending_ideas),
            )
            self._transition(AgentState.MONITORING, "checking positions (scan skipped, pending confirms)")
            await self._check_open_positions()
            self._transition(AgentState.IDLE, "tick cycle complete (scan skipped)")
            return

        self._transition(AgentState.SCANNING, "beginning scan cycle")
        signals = await self.scanner.scan()
        # Cache scan results for the proactive monitor (Move 2)
        self._last_scan_signals = signals or []

        # ── Structured scan logging ──
        scan_summary = {
            "cycle_ts": datetime.now(UTC).isoformat(),
            "pairs_scanned": len(self._last_scan_signals),
            "signals_found": len(signals) if signals else 0,
            "top_signals": [
                {
                    "symbol": s.symbol,
                    "price": s.price,
                    "change_24h": round(s.change_pct_24h, 2),
                    "volume_usd": round(s.volume_usd_24h, 0),
                    "volume_spike": s.volume_spike,
                    "momentum": round(s.momentum_score, 3),
                }
                for s in (signals or [])[:5]
            ],
        }
        audit(scan_log, f"Scan cycle: {scan_summary['signals_found']} signals from market",
              action="scan_cycle", result="OK" if signals else "NO_SIGNALS",
              data=scan_summary)

        if not signals:
            self._transition(AgentState.IDLE, "no signals found")
            return

        self._transition(AgentState.ANALYZING, "signals detected")

        # Analyze all scanner-selected signals concurrently (scanner already caps via slot allocation)
        async def _safe_analyze(sig):
            try:
                return await self._analyze_signal(sig)
            except Exception as e:
                logger.debug("Signal analysis error for %s: %s", sig.symbol, e)
                return None

        tasks = [_safe_analyze(sig) for sig in signals]
        results = await asyncio.gather(*tasks)
        for idea in results:
            if idea:
                # Filter: don't present ideas below min_confidence threshold
                # Prevents user frustration of confirming a trade that gets rejected
                if idea.confidence < CONFIG.risk.min_confidence:
                    audit(scan_log,
                          f"Filtered sub-threshold idea: {idea.asset} conf={idea.confidence:.2f} < {CONFIG.risk.min_confidence}",
                          action="filter_idea", result="BELOW_MIN_CONFIDENCE",
                          data={"asset": idea.asset, "confidence": idea.confidence,
                                "threshold": CONFIG.risk.min_confidence})
                    continue
                # Dedup: if an idea for the same asset already exists, replace it
                existing_id = None
                idea_key = normalize_symbol(idea.asset)
                for eid, eidea in list(self._pending_ideas.items()):
                    if normalize_symbol(eidea.asset) == idea_key:
                        existing_id = eid
                        break
                if existing_id:
                    self._pending_ideas.pop(existing_id)
                    self._pending_atr.pop(existing_id, None)
                    self._pending_pyramid.pop(existing_id, None)  # C2-31 FIX: clean stale pyramid flag
                self._pending_ideas[idea.id] = idea

        # ── Adaptive Confidence Threshold ──
        # Auto-adjust threshold based on recent win rate
        from bot.config import RUNTIME
        if CONFIG.adaptive.adaptive_threshold_enabled:
            try:
                recent_trades = self.portfolio._history[-CONFIG.adaptive.adaptive_threshold_lookback:]
                if len(recent_trades) >= 5:
                    recent_closed = [t for t in recent_trades if t.closed_at is not None]
                    if len(recent_closed) >= 5:
                        recent_wins = sum(1 for t in recent_closed if t.pnl > 0)
                        recent_wr = recent_wins / len(recent_closed)

                        if recent_wr >= CONFIG.adaptive.adaptive_threshold_high_wr:
                            # Winning streak: lower threshold to capture more
                            new_thresh = max(CONFIG.adaptive.adaptive_threshold_min,
                                           RUNTIME.auto_confirm_threshold - 0.05)
                        elif recent_wr <= CONFIG.adaptive.adaptive_threshold_low_wr:
                            # Losing streak: raise threshold to be selective
                            new_thresh = min(CONFIG.adaptive.adaptive_threshold_max,
                                           RUNTIME.auto_confirm_threshold + 0.05)
                        else:
                            new_thresh = RUNTIME.auto_confirm_threshold

                        if new_thresh != RUNTIME.auto_confirm_threshold:
                            audit(system_log,
                                  f"Adaptive threshold: {RUNTIME.auto_confirm_threshold:.2f} → {new_thresh:.2f} "
                                  f"(WR={recent_wr:.0%} over last {len(recent_closed)} trades)",
                                  action="adaptive_threshold", result="ADJUSTED")
                            RUNTIME.auto_confirm_threshold = new_thresh
            except Exception:
                pass  # fail-open

        # ── Auto-confirmation for high-confidence signals ──
        # If confidence exceeds threshold, bypass human confirmation gate
        # and auto-execute. Notifications still go to Telegram with
        # "[AUTO]" tag so the operator can see what happened.
        # RC-AUD-002: auto-confirm bypasses the human-decision gate. It is
        # disabled by default (threshold 1.0) and, in LIVE mode, refuses to place
        # real-money orders unless AUTO_CONFIRM_LIVE_ENABLED is explicitly set.
        auto_threshold = RUNTIME.auto_confirm_threshold
        auto_ideas = [
            (tid, tidea) for tid, tidea in list(self._pending_ideas.items())
            if tidea.confidence >= auto_threshold
        ]
        if auto_ideas and CONFIG.is_live() and not CONFIG.auto_confirm_live_enabled:
            for tid, tidea in auto_ideas:
                audit(trade_log,
                      f"Auto-confirm SUPPRESSED in live mode for {tidea.asset} "
                      f"(conf={tidea.confidence:.2f}) — human confirmation required. "
                      f"Set AUTO_CONFIRM_LIVE_ENABLED=true to allow live auto-execution.",
                      action="auto_confirm", result="SUPPRESSED_LIVE",
                      data={"trade_id": tid, "confidence": tidea.confidence,
                            "threshold": auto_threshold})
            auto_ideas = []
        for tid, tidea in auto_ideas:
            audit(trade_log,
                  f"Auto-confirming {tidea.asset} (conf={tidea.confidence:.2f} >= {auto_threshold})",
                  action="auto_confirm", result="TRIGGERING",
                  data={"trade_id": tid, "confidence": tidea.confidence,
                        "threshold": auto_threshold})
            try:
                result = await self.confirm_trade(tid, user_id="auto")
                audit(trade_log,
                      f"Auto-confirm result for {tidea.asset}: {result[:120]}",
                      action="auto_confirm", result="DONE",
                      data={"trade_id": tid, "result_preview": result[:200]})
                # Notify via Telegram if callback is set
                if self._auto_confirm_notify_callback:
                    try:
                        await self._auto_confirm_notify_callback(tidea, result)
                    except Exception:
                        pass
            except Exception as exc:
                audit(trade_log,
                      f"Auto-confirm failed for {tidea.asset}: {exc}",
                      action="auto_confirm", result="ERROR",
                      data={"trade_id": tid, "error": str(exc)})

        self._transition(AgentState.MONITORING, "checking open positions")
        await self._check_open_positions()
        self._transition(AgentState.IDLE, "tick cycle complete")

    async def _cached_ohlcv(self, exchange, symbol, timeframe, limit=100, ttl=120):
        """Fetch OHLCV with a simple TTL cache to avoid refetching within `ttl` seconds."""
        key = f"{symbol}:{timeframe}"
        now = time.monotonic()
        if key in self._ohlcv_cache:
            cached_time, cached_data = self._ohlcv_cache[key]
            if now - cached_time < ttl:
                return cached_data
        data = await exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        self._ohlcv_cache[key] = (now, data)
        # C2-54 FIX: Hard size cap + TTL eviction to prevent unbounded growth.
        # First try TTL-based eviction; if still over limit, evict oldest entries.
        if len(self._ohlcv_cache) > 200:
            cutoff = now - ttl * 2
            self._ohlcv_cache = {k: v for k, v in self._ohlcv_cache.items() if v[0] > cutoff}
        if len(self._ohlcv_cache) > 200:
            # Still over limit — evict oldest entries
            sorted_keys = sorted(self._ohlcv_cache, key=lambda k: self._ohlcv_cache[k][0])
            for old_key in sorted_keys[:len(self._ohlcv_cache) - 200]:
                del self._ohlcv_cache[old_key]
        return data

    async def _refine_entry_mtf(self, idea: TradeIdea, exchange) -> TradeIdea:
        """Zoom into lower timeframe to find optimal entry within the setup zone.

        After identifying a setup on 1H/4H, check 15m candles for:
        - Better entry near support/resistance within the zone
        - Momentum confirmation on lower timeframe
        - Tighter stop placement based on lower-TF structure

        Returns refined TradeIdea (or original if refinement fails/not applicable).
        """
        try:
            # Only refine for swing/intraday strategies (not scalps)
            if idea.strategy_type == "scalp":
                return idea

            symbol = idea.asset

            # Fetch 15m candles for the last ~12 hours (48 candles)
            candles_15m = await self._cached_ohlcv(exchange, symbol, "15m", limit=48, ttl=60)
            if not candles_15m or len(candles_15m) < 20:
                return idea

            import numpy as np
            closes = np.array([c[4] for c in candles_15m])
            highs = np.array([c[2] for c in candles_15m])
            lows = np.array([c[3] for c in candles_15m])

            current_price = float(closes[-1])

            # Find recent support/resistance on 15m
            recent_lows = lows[-20:]
            recent_highs = highs[-20:]

            is_long = idea.direction.value == "LONG"

            if is_long:
                # For longs, look for nearest support level below current price
                # as a better entry point
                support_candidates = []
                for i in range(2, len(recent_lows) - 2):
                    if recent_lows[i] <= recent_lows[i-1] and recent_lows[i] <= recent_lows[i-2] and \
                       recent_lows[i] <= recent_lows[i+1] and recent_lows[i] <= recent_lows[i+2]:
                        support_candidates.append(float(recent_lows[i]))

                if support_candidates:
                    # Find the nearest support below current price
                    supports_below = [s for s in support_candidates if s < current_price]
                    if supports_below:
                        best_support = max(supports_below)  # closest support below
                        # Only refine if support is within 1% of current price
                        pct_diff = (current_price - best_support) / current_price
                        if 0.001 < pct_diff < 0.01:
                            # Refined entry is at support + small buffer
                            refined_entry = best_support + (current_price - best_support) * 0.2
                            # Tighter SL based on 15m structure
                            min_low = float(np.min(recent_lows[-10:]))
                            refined_sl = min(idea.stop_loss, min_low * 0.998)
                            # Preserve R:R ratio for TP
                            original_rr = abs(idea.take_profit - idea.entry_price) / abs(idea.entry_price - idea.stop_loss)
                            new_risk = abs(refined_entry - refined_sl)
                            refined_tp = refined_entry + new_risk * original_rr

                            audit(system_log,
                                  f"MTF entry refined for {symbol}: {idea.entry_price:.4f} -> {refined_entry:.4f} "
                                  f"(SL: {idea.stop_loss:.4f} -> {refined_sl:.4f})",
                                  action="mtf_refine", result="REFINED")

                            idea = idea.model_copy(update={
                                "entry_price": round(refined_entry, 8),
                                "stop_loss": round(refined_sl, 8),
                                "take_profit": round(refined_tp, 8),
                            })
            else:
                # For shorts, look for nearest resistance level above current price
                resistance_candidates = []
                for i in range(2, len(recent_highs) - 2):
                    if recent_highs[i] >= recent_highs[i-1] and recent_highs[i] >= recent_highs[i-2] and \
                       recent_highs[i] >= recent_highs[i+1] and recent_highs[i] >= recent_highs[i+2]:
                        resistance_candidates.append(float(recent_highs[i]))

                if resistance_candidates:
                    resistances_above = [r for r in resistance_candidates if r > current_price]
                    if resistances_above:
                        best_resistance = min(resistances_above)  # closest resistance above
                        pct_diff = (best_resistance - current_price) / current_price
                        if 0.001 < pct_diff < 0.01:
                            refined_entry = best_resistance - (best_resistance - current_price) * 0.2
                            max_high = float(np.max(recent_highs[-10:]))
                            refined_sl = max(idea.stop_loss, max_high * 1.002)
                            original_rr = abs(idea.take_profit - idea.entry_price) / abs(idea.entry_price - idea.stop_loss)
                            new_risk = abs(refined_entry - refined_sl)
                            refined_tp = refined_entry - new_risk * original_rr

                            audit(system_log,
                                  f"MTF entry refined for {symbol}: {idea.entry_price:.4f} -> {refined_entry:.4f} "
                                  f"(SL: {idea.stop_loss:.4f} -> {refined_sl:.4f})",
                                  action="mtf_refine", result="REFINED")

                            idea = idea.model_copy(update={
                                "entry_price": round(refined_entry, 8),
                                "stop_loss": round(refined_sl, 8),
                                "take_profit": round(refined_tp, 8),
                            })
        except Exception as exc:
            # Fail-open: return original idea if refinement fails
            logger.debug("MTF refinement failed for %s: %s", idea.asset, exc)

        return idea

    async def _analyze_signal(self, signal: MarketSignal, *, timeframe: str = "1h", is_admin: bool = False, user_id=None, user_tier=None) -> Optional[TradeIdea]:
        """Run full analysis pipeline on a single signal.

        Args:
            signal: Market signal to analyze.
            timeframe: OHLCV timeframe to fetch (e.g. "5m", "15m", "1h", "4h").
        """
        try:
            # Use futures exchange for non-Crypto categories (metals, commodities, etc.)
            category = getattr(signal, "asset_category", "Crypto") or "Crypto"
            if category != "Crypto":
                exchange = await self.scanner._get_futures_exchange()
            else:
                exchange = await self.scanner._get_exchange()
            # Parallelize OHLCV fetch and order flow analysis
            ohlcv_task = self._cached_ohlcv(exchange, signal.symbol, timeframe, limit=100)
            of_task = self.order_flow.analyze(exchange, signal.symbol)
            results = await asyncio.gather(ohlcv_task, of_task, return_exceptions=True)
            ohlcv = results[0] if not isinstance(results[0], Exception) else None
            of_signal = results[1] if not isinstance(results[1], Exception) else None
            if isinstance(results[0], Exception):
                audit(
                    system_log,
                    f"OHLCV fetch failed: {results[0]}",
                    action="fetch_candles",
                    result="ERROR",
                )
                return None
            if isinstance(results[1], Exception):
                audit(system_log, f"Order flow analysis failed: {results[1]}",
                      action="order_flow", result="ERROR")
        except Exception as exc:
            audit(
                system_log,
                f"OHLCV fetch failed: {exc}",
                action="fetch_candles",
                result="ERROR",
            )
            return None

        idea = await self.analyzer.analyze(signal, ohlcv, order_flow=of_signal, is_admin=is_admin, user_id=user_id, user_tier=user_tier)
        if idea is None:
            audit(scan_log, f"Analysis produced no idea for {signal.symbol}",
                  action="analyze_signal", result="NO_IDEA",
                  data={"symbol": signal.symbol, "timeframe": timeframe})
            return None

        # Log trade idea generation
        audit(scan_log, f"Trade idea generated: {idea.direction.value} {idea.asset}",
              action="trade_idea", result="GENERATED",
              data={
                  "id": idea.id,
                  "asset": idea.asset,
                  "direction": idea.direction.value,
                  "confidence": round(idea.confidence, 3),
                  "entry": idea.entry_price,
                  "sl": idea.stop_loss,
                  "tp": idea.take_profit,
                  "rr": round(idea.risk_reward_ratio, 2),
                  "timeframe": timeframe,
              })

        # ── Closed-loop learning nudge (opt-in, default OFF) ──────────────
        # The orchestrator already logs every decision + outcome; here we read
        # that experience back. Down-weight setups (same symbol + direction +
        # regime) that have historically LOST, slightly up-weight winners. The
        # nudge is small, capped, asymmetric, additive — it never overrides the
        # risk engine (every check still runs below); it only shifts confidence,
        # which can push a chronically-losing setup under the entry threshold.
        if CONFIG.learning.adaptive_confidence_enabled:
            try:
                _regime = str(getattr(self.risk, "_current_regime", "") or "")
                # Query on symbol + direction across ALL regimes (empty regime =
                # match any): a live bot accumulates too few same-symbol+direction
                # +regime samples to be useful, and direction already carries the
                # dominant signal (e.g. longs on a symbol chronically losing).
                _lctx = self.learning.get_learning_context(
                    symbol=idea.asset, market_regime="",
                    macro_state="", direction=idea.direction.value)
                _n = _lctx.get("similar_past_setups", 0) or 0
                _avg = _lctx.get("avg_past_pnl")
                if _n >= CONFIG.learning.adaptive_confidence_min_samples and _avg is not None:
                    if _avg < 0:
                        _delta = -CONFIG.learning.adaptive_confidence_max_penalty
                    elif _avg > 0:
                        _delta = CONFIG.learning.adaptive_confidence_max_boost
                    else:
                        _delta = 0.0
                    if _delta:
                        _old = idea.confidence
                        idea.confidence = round(max(0.0, min(1.0, _old + _delta)), 4)
                        audit(scan_log,
                              f"Learning nudge {idea.asset} {idea.direction.value}: "
                              f"conf {_old:.2f} -> {idea.confidence:.2f} "
                              f"(avg_past_pnl=${_avg:.2f} over {_n} setups)",
                              action="learning_confidence_nudge",
                              result="PENALIZED" if _delta < 0 else "BOOSTED",
                              data={"symbol": idea.asset, "direction": idea.direction.value,
                                    "regime": _regime, "delta": _delta,
                                    "avg_past_pnl": round(_avg, 4), "samples": _n,
                                    "old_conf": round(_old, 4), "new_conf": idea.confidence})
            except Exception as _learn_exc:
                # Fail-open: learning must never block or crash trade evaluation.
                logger.debug("Learning nudge skipped for %s: %s", idea.asset, _learn_exc)

        # Compute ATR from candles for the volatility guard (check #16)
        atr_value = None
        if len(ohlcv) >= 15:
            true_ranges = []
            for j in range(1, min(15, len(ohlcv))):
                h = float(ohlcv[-j][2])
                l = float(ohlcv[-j][3])
                pc = float(ohlcv[-j - 1][4])
                tr = max(h - l, abs(h - pc), abs(l - pc))
                true_ranges.append(tr)
            atr_value = sum(true_ranges) / len(true_ranges)

        # Smart scan: track ATR for interval adjustment
        if atr_value is not None and signal.price > 0:
            self._recent_atr_values[signal.symbol] = atr_value / signal.price

        # Strategy router: select optimal strategy for regime
        try:
            from bot.core.strategy_router import select_strategy, get_strategy_adjustments
            strategy_profile = select_strategy(
                getattr(self.risk, '_current_regime', 'unknown'),
                getattr(self.risk, '_current_vol_state', 'normal'),
                adx=atr_value if atr_value else 0,
            )
            adjustments = get_strategy_adjustments(
                strategy_profile,
                idea.stop_loss,
                idea.take_profit,
                idea.confidence,
            )
            # Apply strategy-specific adjustments
            if adjustments.get("strategy_type"):
                # Override strategy type based on regime
                pass  # strategy_type is set from the router
        except Exception as _strat_exc:
            logger.warning("Strategy router selection failed for %s: %s", idea.asset, _strat_exc)

        # ── Per-symbol cooldown after SL hit ─────────────────────────
        # Prevents immediate re-entry into a symbol that just stopped out
        symbol_key = normalize_symbol(idea.asset)
        _sym_cd = self._symbol_cooldowns.get(symbol_key, 0)
        if _sym_cd and time.monotonic() < _sym_cd:
            _remaining = int(_sym_cd - time.monotonic())
            audit(scan_log,
                  f"Signal skipped: {idea.asset} on post-SL cooldown ({_remaining}s remaining)",
                  action="symbol_cooldown", result="SKIPPED")
            return None
        elif _sym_cd and time.monotonic() >= _sym_cd:
            self._symbol_cooldowns.pop(symbol_key, None)

        # ── Smart pyramid / duplicate symbol guard ─────────────────
        # Rules: max 2 entries per symbol, same direction adds require
        # 1R profit + 70% confidence. Opposite direction with high
        # confidence triggers a flip (close existing + open new).
        existing_positions = []  # list of (position, is_live, current_price)

        if CONFIG.is_live() and hasattr(self, 'live_executor'):
            for lp in self.live_executor.open_positions:
                lp_key = normalize_symbol(lp.symbol)
                if lp_key == symbol_key:
                    existing_positions.append((lp, True))

        if not existing_positions and hasattr(self, 'portfolio'):
            for pp in self.portfolio.open_positions:
                pp_key = normalize_symbol(pp.asset)
                if pp_key == symbol_key:
                    existing_positions.append((pp, False))

        is_pyramid_add = False
        if existing_positions:
            # Max 2 entries per symbol
            if len(existing_positions) >= 2:
                audit(scan_log, f"Signal skipped: max 2 positions on {idea.asset}",
                      action="pyramid_maxed", result="SKIPPED")
                return None

            pos, is_live = existing_positions[0]
            pos_dir = pos.direction if isinstance(pos.direction, str) else pos.direction.value
            idea_dir = idea.direction.value

            same_direction = (pos_dir.upper() == idea_dir.upper())

            if same_direction:
                # ── Same direction: pyramid add ──
                # Condition 1: confidence >= 70%
                if idea.confidence < 0.70:
                    audit(scan_log, f"Pyramid skipped: confidence {idea.confidence:.0%} < 70% for {idea.asset}",
                          action="pyramid_low_conf", result="SKIPPED")
                    return None

                # Condition 2: existing position is at least 1R in profit
                entry_px = pos.entry_price
                sl_px = pos.stop_loss if hasattr(pos, 'stop_loss') else getattr(pos, 'stop_loss', 0)
                initial_risk = abs(entry_px - sl_px) if sl_px else 0
                current_price = idea.entry_price  # new signal's entry = current price
                if pos_dir.upper() == "LONG":
                    unrealized = current_price - entry_px
                else:
                    unrealized = entry_px - current_price

                r_achieved = unrealized / initial_risk if initial_risk > 0 else 0
                if initial_risk <= 0 or unrealized < initial_risk:
                    audit(scan_log,
                          f"Pyramid skipped: {idea.asset} only {r_achieved:.2f}R in profit (need 1R)",
                          action="pyramid_insufficient_profit", result="SKIPPED")
                    return None

                # All conditions met — flag as pyramid add
                is_pyramid_add = True
                audit(scan_log,
                      f"Pyramid APPROVED: {idea.asset} {r_achieved:.2f}R profit, conf {idea.confidence:.0%}",
                      action="pyramid_approved", result="APPROVED",
                      data={"r_achieved": round(r_achieved, 2), "confidence": idea.confidence})
            else:
                # ── Opposite direction: NEVER auto-flip ──
                # Don't automatically close and reverse positions.
                # Skip the idea — user must manually close first.
                audit(scan_log, f"Flip BLOCKED: {idea.asset} {pos_dir} -> {idea_dir} (auto-flip disabled)",
                      action="flip_blocked", result="SKIPPED",
                      data={"confidence": idea.confidence, "existing": pos_dir, "proposed": idea_dir})
                return None

        # Store pyramid flag for confirm_trade to apply half-size + SL-to-breakeven
        if is_pyramid_add:
            self._pending_pyramid[idea.id] = True

        # Risk gate — pass ATR so all 18 checks run
        # LIVE FIX: pass actual exchange equity so sizing is based on real capital
        live_eq = self._live_balance_cache.get("total", 0.0) if (CONFIG.is_live() and self._live_balance_cache) else None
        # Pass micro-test cap so risk evaluates the actual execution size
        from bot.core.live_executor import MICRO_MAX_POSITION_USD
        exec_cap = MICRO_MAX_POSITION_USD if CONFIG.is_live() else None
        # LIVE FIX: pass live open position count so risk check #5 is accurate
        # CRITICAL: count BOTH filled positions AND pending limit orders.
        # Pending limit orders can fill at any time, so they must count
        # toward the max_open_positions limit. Otherwise auto-confirm can
        # place 20+ limit orders that all fill simultaneously.
        live_open = None
        if CONFIG.is_live():
            try:
                exchange_count = await get_exchange_position_count(self)
                # Add pending (unfilled) limit orders — they occupy margin and
                # will become positions when filled
                pending_count = sum(
                    1 for p in self.live_executor.open_positions
                    if p.status == "pending_fill"
                )
                live_open = exchange_count + pending_count
            except Exception:
                # Fallback: use local state (includes both open + pending_fill)
                live_open = len(self.live_executor.open_positions)
        # N-03 FIX: removed _transition(RISK_CHECK) — runs in parallel, parent manages state
        # Wire order flow signal to risk engine so check #23 (bid dominance) runs
        if of_signal is not None:
            self.risk.set_order_flow_signal(of_signal)
        risk_check = self.risk.evaluate(idea, atr=atr_value, live_equity=live_eq, max_position_usd=exec_cap, live_open_count=live_open)

        # Log risk evaluation to scan log
        audit(scan_log, f"Risk evaluation: {risk_check.verdict.value} for {idea.asset}",
              action="risk_evaluation", result=risk_check.verdict.value,
              data={
                  "asset": idea.asset,
                  "direction": idea.direction.value,
                  "checks_passed": risk_check.checks_passed,
                  "checks_failed": risk_check.checks_failed,
                  "reason": risk_check.reason,
                  "position_size_usd": round(risk_check.position_size_usd, 2),
                  "atr_pct": round((atr_value / idea.entry_price) * 100, 2) if atr_value and idea.entry_price else None,
              })

        # Cross-asset confidence adjustment
        try:
            ca_conf_adj, ca_size_mult = self.cross_asset.get_symbol_adjustment(
                signal.symbol, idea.direction.value)
            if ca_conf_adj != 0:
                # Store for risk engine
                pass
        except Exception:
            pass

        # Check #17: liquidity guard from order flow (fail-open if no data)
        if of_signal is not None:
            liq_size = risk_check.position_size_usd if risk_check else 0.0
            liq_reason = self.order_flow.liquidity_guard(
                of_signal,
                position_size_usd=liq_size,
                symbol=signal.symbol,
            )
            if liq_reason:
                audit(trade_log, f"Trade REJECTED by liquidity guard: {liq_reason}",
                      action="liquidity_guard", result="REJECTED")
                audit(scan_log, f"Liquidity guard rejected {idea.asset}: {liq_reason}",
                      action="liquidity_guard", result="REJECTED",
                      data={
                          "asset": idea.asset,
                          "bid_depth": round(of_signal.bid_depth_usd, 0) if of_signal.bid_depth_usd else 0,
                          "ask_depth": round(of_signal.ask_depth_usd, 0) if of_signal.ask_depth_usd else 0,
                          "spread_bps": round(of_signal.spread_bps, 1) if of_signal.spread_bps else 0,
                          "position_size": round(liq_size, 2),
                      })
                return None

        if risk_check.verdict == RiskVerdict.REJECTED:
            # Store rejection for /whynot command
            symbol_key = idea.asset.replace("/USDT", "").upper()
            self._last_rejections[symbol_key] = {
                "symbol": idea.asset,
                "direction": idea.direction.value,
                "confidence": idea.confidence,
                "entry_price": idea.entry_price,
                "stop_loss": idea.stop_loss,
                "take_profit": idea.take_profit,
                "checks_passed": risk_check.checks_passed,
                "checks_failed": risk_check.checks_failed,
                "reason": risk_check.reason,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            # Cap stored rejections
            if len(self._last_rejections) > 100:
                oldest_keys = list(self._last_rejections.keys())[:-50]
                for k in oldest_keys:
                    self._last_rejections.pop(k, None)
            audit(
                trade_log,
                f"Trade REJECTED by risk: {risk_check.reason}",
                action="risk_gate",
                result="REJECTED",
            )
            # Learning: log rejected trade decision
            decision = self.learning.log_decision(
                symbol=signal.symbol,
                direction=idea.direction.value,
                confidence=idea.confidence,
                confluence_score=idea.confidence,
                entry_price=idea.entry_price,
                stop_loss=idea.stop_loss,
                take_profit=idea.take_profit,
                risk_reward=idea.risk_reward_ratio,
                position_size_usd=risk_check.position_size_usd,
                risk_engine_result="REJECTED",
                checks_passed=risk_check.checks_passed,
                checks_failed=risk_check.checks_failed,
                rejected_reason=risk_check.reason,
                decision="TRADE_REJECTED_FAIL_CLOSED",
                confluence_votes=getattr(idea, "_confluence_votes", []),
            )
            self.learning.review_rejection(decision)
            return None

        # N-03 FIX: removed _transition(CONFIRMING) — runs in parallel, parent manages state
        audit(
            trade_log,
            f"Trade idea awaiting human confirmation: {idea.id}",
            action="confirmation_gate",
            result="PENDING",
        )
        # H1: store ATR alongside idea for re-check in confirm_trade
        self._pending_atr[idea.id] = atr_value

        # MTF entry refinement: zoom into 15m for better entry within zone
        idea = await self._refine_entry_mtf(idea, exchange)

        return idea

    @staticmethod
    def _human_confirmed(user_id: str) -> bool:
        """RC-AUD-025: True only when the confirmation came from a real human.

        Auto-confirm passes ``user_id="auto"`` and some unattended paths pass
        ``""`` — neither represents a deliberate human button press, so the
        "user already confirmed, proceed anyway" rationale must NOT apply to
        them. A real human confirmation carries a non-empty, non-"auto" id.
        """
        return user_id not in ("", "auto")

    @staticmethod
    def _live_execution_vetoed_by_simulation() -> bool:
        """RC-AUD-018: hard veto on live execution when SIMULATION_MODE is True.

        ``CONFIG.simulation_mode`` is an independent, fail-closed kill switch:
        if it is set, the engine must NEVER place a real order, regardless of
        any runtime flag (e.g. ``RUNTIME.live_mode``) that might otherwise arm
        live mode. Returns True when live execution must be vetoed.
        """
        return bool(CONFIG.simulation_mode)

    async def _simulate_paper_fill(
        self, idea, recheck, user_id: str, trade_id: str
    ) -> str:
        """Open a SIMULATED position in the user's paper portfolio. Pure in-memory
        (no exchange interaction whatsoever) — the per-user sim opt-in path. The
        position is then monitored for SL/TP by the existing paper loop
        (``check_stops_all``). Never calls ``live_executor``.
        """
        size_usd = recheck.position_size_usd
        try:
            leverage = int(CONFIG.exchange.default_leverage)
        except (TypeError, ValueError):
            leverage = 1
        portfolio = self.user_portfolios.get(user_id)
        try:
            trade = portfolio.open_position(idea, size_usd, leverage=leverage)
        except Exception as exc:
            self._pending_ideas.pop(trade_id, None)
            self._transition(AgentState.IDLE, f"paper fill error {trade_id}")
            return f"⚠️ [PAPER] Simulated fill failed: {str(exc)[:160]}"

        self._pending_ideas.pop(trade_id, None)
        audit(trade_log,
              f"PAPER fill: {idea.direction.value} {idea.asset} @ {idea.entry_price} "
              f"size ${size_usd:.2f} (user {user_id})",
              action="paper_fill", result="FILLED",
              data={"trade_id": trade_id, "asset": idea.asset,
                    "direction": idea.direction.value, "size_usd": round(size_usd, 2),
                    "user_id": user_id, "is_paper": True})
        self._transition(AgentState.IDLE, f"paper filled {trade_id}")
        _dir = idea.direction.value
        return (
            f"📝 <b>[PAPER]</b> Simulated {_dir} <b>{idea.asset}</b>\n"
            f"Entry <code>${idea.entry_price:,.4f}</code> | "
            f"SL <code>${idea.stop_loss:,.4f}</code> | "
            f"TP <code>${idea.take_profit:,.4f}</code>\n"
            f"Size <code>${size_usd:,.2f}</code> @ {leverage}x  •  "
            f"<i>practice mode — no real order placed</i>\n"
            f"Trade ID: <code>{trade.trade_id}</code>"
        )

    async def confirm_trade(self, trade_id: str, user_id: str = "") -> str:
        """
        Human confirms a pending trade idea.  This is the ONLY path to execution.
        If user_id is provided, the trade is recorded in that user's isolated portfolio.
        """
        idea = self._pending_ideas.get(trade_id, None)
        if idea is None:
            return "Trade not found or expired."

        # Store for marketing forwarder access
        self._last_confirmed_idea = idea

        # H1 fix: re-check with stored ATR so volatility guard runs
        stored_atr = self._pending_atr.get(trade_id, None)

        # F-05 FIX: reject if market price has drifted significantly from
        # the idea's entry price. Prevents executing at stale levels.
        # Skip price drift check for manual trades — user specified exact entry
        is_manual = getattr(idea, 'source', '') == 'manual'
        try:
            idea_category = _classify_symbol(idea.asset)
            exchange = await self.get_exchange(idea_category)
            ticker = await exchange.fetch_ticker(idea.asset)
            current_price = float(ticker.get("last") or 0)
            if current_price > 0 and idea.entry_price > 0:
                drift_pct = abs(current_price - idea.entry_price) / idea.entry_price * 100
                max_drift = 2.0  # reject if price moved more than 2%
                is_limit = getattr(idea, 'order_type', '') == 'limit'
                if not is_manual and not is_limit and drift_pct > max_drift:
                    audit(trade_log,
                          f"Price drift {drift_pct:.2f}% exceeds {max_drift}% threshold",
                          action="price_drift", result="REJECTED",
                          data={"trade_id": trade_id, "asset": idea.asset,
                                "idea_entry": idea.entry_price,
                                "current_price": current_price,
                                "drift_pct": round(drift_pct, 2)})
                    self._pending_pyramid.pop(trade_id, None)
                    self._transition(AgentState.IDLE, f"price drift for {trade_id}")
                    return (f"Trade REJECTED: price drifted {drift_pct:.1f}% since analysis "
                            f"(${idea.entry_price:,.2f} → ${current_price:,.2f}). Re-analyze.")

                # ── Validate price hasn't already blown through SL ──
                # If market price is already past the SL, the trade would be
                # instantly stopped out. Reject before wasting an execution.
                if idea.direction.value == "LONG" and current_price <= idea.stop_loss:
                    self._pending_pyramid.pop(trade_id, None)
                    self._transition(AgentState.IDLE, f"price past SL for {trade_id}")
                    return (f"Trade REJECTED: price ${current_price:,.4f} already below "
                            f"SL ${idea.stop_loss:,.4f} — would be instantly stopped out.")
                elif idea.direction.value == "SHORT" and current_price >= idea.stop_loss:
                    self._pending_pyramid.pop(trade_id, None)
                    self._transition(AgentState.IDLE, f"price past SL for {trade_id}")
                    return (f"Trade REJECTED: price ${current_price:,.4f} already above "
                            f"SL ${idea.stop_loss:,.4f} — would be instantly stopped out.")

                # ── Validate remaining R:R hasn't deteriorated ──
                # If price has eaten more than 50% of the SL distance, the setup
                # no longer offers a favorable risk:reward. Reject stale signals.
                # Skip for manual trades and limit orders — user chose these exact levels.
                if not is_manual and not is_limit:
                    sl_dist = abs(idea.entry_price - idea.stop_loss)
                    if sl_dist > 0:
                        if idea.direction.value == "LONG":
                            consumed = max(0, idea.entry_price - current_price)
                        else:
                            consumed = max(0, current_price - idea.entry_price)
                        consumed_pct = consumed / sl_dist
                        if consumed_pct > 0.5:
                            self._pending_pyramid.pop(trade_id, None)
                            self._transition(AgentState.IDLE, f"R:R deteriorated for {trade_id}")
                            return (f"Trade REJECTED: price moved {consumed_pct:.0%} toward SL "
                                    f"(${current_price:,.4f} vs entry ${idea.entry_price:,.4f}). "
                                    f"R:R no longer favorable — re-analyze.")
        except Exception as exc:
            # H-08 FIX: fail-closed — reject if exchange is unreachable
            audit(trade_log, f"Price drift check failed (rejecting): {exc}",
                  action="price_drift", result="REJECTED")
            self._pending_pyramid.pop(trade_id, None)
            self._transition(AgentState.IDLE, f"price drift check failed for {trade_id}")
            return "Trade REJECTED: unable to verify current price. Try again."

        # ── Limit order price recalculation at confirm time ──
        # When order_type is "limit", the idea.entry_price was set at analysis time.
        # Only recalculate if the limit price would cause an instant fill:
        #   - LONG buy limit ABOVE current price fills immediately
        #   - SHORT sell limit BELOW current price fills immediately
        # If the limit price is already on the correct side, keep it.
        if idea.order_type == "limit" and current_price > 0 and stored_atr and stored_atr > 0:
            _needs_recalc = False
            if idea.direction.value == "LONG" and idea.entry_price >= current_price:
                _needs_recalc = True
            elif idea.direction.value != "LONG" and idea.entry_price <= current_price:
                _needs_recalc = True

            if _needs_recalc:
                # Use 0.5*ATR offset (not 0.1) so the limit is far enough from
                # current price to actually rest on the book as a maker order.
                offset = 0.5 * stored_atr
                if idea.direction.value == "LONG":
                    new_limit = round(current_price - offset, 8)
                    # Also update SL/TP relative to new entry
                    sl_dist = abs(idea.entry_price - idea.stop_loss)
                    tp_dist = abs(idea.take_profit - idea.entry_price)
                    new_sl = round(new_limit - sl_dist, 8)
                    new_tp = round(new_limit + tp_dist, 8)
                else:
                    new_limit = round(current_price + offset, 8)
                    sl_dist = abs(idea.stop_loss - idea.entry_price)
                    tp_dist = abs(idea.entry_price - idea.take_profit)
                    new_sl = round(new_limit + sl_dist, 8)
                    new_tp = round(new_limit - tp_dist, 8)

                old_entry = idea.entry_price
                idea = idea.model_copy(update={
                    "entry_price": new_limit,
                    "stop_loss": new_sl,
                    "take_profit": new_tp,
                })
                audit(trade_log,
                      f"Limit price recalculated at confirm: ${old_entry:,.4f} → ${new_limit:,.4f} "
                      f"(market=${current_price:,.4f}, offset={offset:.4f})",
                      action="limit_price_update", result="UPDATED",
                      data={"old_entry": old_entry, "new_entry": new_limit,
                            "current_price": current_price, "offset": offset,
                            "new_sl": new_sl, "new_tp": new_tp})

                # ── RC-AUD-010: re-validate the NEW levels after recalc ──
                # The drift / past-SL / R:R guards above ran against the OLD
                # levels and are skipped for limit orders. Now that the entry
                # was repriced to current ± 0.5*ATR (with SL/TP rederived from
                # the original distances), re-affirm the new SL is sane and
                # that current price has not already blown through the new SL,
                # mirroring the "price past SL" check earlier in this function.
                if idea.stop_loss == idea.entry_price:
                    self._pending_pyramid.pop(trade_id, None)
                    self._transition(AgentState.IDLE, f"recalc SL==entry for {trade_id}")
                    return (f"Trade REJECTED: recalculated SL ${idea.stop_loss:,.4f} equals "
                            f"entry — cannot compute safe stop distance.")
                if idea.direction.value == "LONG" and current_price <= idea.stop_loss:
                    self._pending_pyramid.pop(trade_id, None)
                    self._transition(AgentState.IDLE, f"price past new SL for {trade_id}")
                    return (f"Trade REJECTED: price ${current_price:,.4f} already below "
                            f"recalculated SL ${idea.stop_loss:,.4f} — would be instantly stopped out.")
                elif idea.direction.value == "SHORT" and current_price >= idea.stop_loss:
                    self._pending_pyramid.pop(trade_id, None)
                    self._transition(AgentState.IDLE, f"price past new SL for {trade_id}")
                    return (f"Trade REJECTED: price ${current_price:,.4f} already above "
                            f"recalculated SL ${idea.stop_loss:,.4f} — would be instantly stopped out.")

        # Re-check risk (portfolio state may have changed -- new positions, daily PnL, drawdown.
        # HONEST LIMITATION: price drift is now checked above (F-05 fix).
        # Stale-data check #12 guards against time drift (>300s = reject).
        self._transition(AgentState.RISK_CHECK, f"re-checking risk for {trade_id}")
        try:
            # LIVE FIX: pass live equity for re-check sizing too
            live_eq_recheck = self._live_balance_cache.get("total", 0.0) if (CONFIG.is_live() and self._live_balance_cache) else None
            from bot.core.live_executor import MICRO_MAX_POSITION_USD
            recheck_cap = MICRO_MAX_POSITION_USD if CONFIG.is_live() else None
            # CRITICAL: count filled + pending positions (same as scan path)
            live_open_recheck = None
            if CONFIG.is_live():
                try:
                    exchange_ct = await get_exchange_position_count(self)
                    pending_ct = sum(
                        1 for p in self.live_executor.open_positions
                        if p.status == "pending_fill"
                    )
                    live_open_recheck = exchange_ct + pending_ct
                except Exception:
                    live_open_recheck = len(self.live_executor.open_positions)
            recheck = self.risk.evaluate(idea, atr=stored_atr, live_equity=live_eq_recheck, max_position_usd=recheck_cap, live_open_count=live_open_recheck)
        except Exception as exc:
            # Fix 6: if re-check raises, do NOT silently lose the idea.
            # Log it as a failed re-check and return a clear message.
            audit(
                trade_log,
                f"Risk re-check crashed for {trade_id}: {exc}",
                action="recheck",
                result="ERROR",
                data={"trade_id": trade_id, "asset": idea.asset, "error": str(exc)},
            )
            self._pending_pyramid.pop(trade_id, None)
            self._transition(AgentState.IDLE, f"re-check error for {trade_id}")
            return f"Trade REJECTED: re-check failed (error logged): {exc}"
        if recheck.verdict == RiskVerdict.REJECTED:
            self._pending_pyramid.pop(trade_id, None)
            self._transition(AgentState.IDLE, f"re-check rejected {trade_id}")
            # Seal rejection to audit chain
            self.audit_chain.seal_decision(DecisionRecord(
                decision_id=trade_id, symbol=idea.asset,
                idea={"direction": idea.direction.value, "confidence": idea.confidence},
                risk={"verdict": "REJECTED", "reason": recheck.reason},
                outcome="REJECTED_ON_RECHECK", is_paper=not CONFIG.is_live(),
            ))
            return f"Trade REJECTED on re-check: {recheck.reason}"

        # Adversarial self-critique gate (fail-open: errors = proceed with warning)
        try:
            from bot.core.critique import TradeCritique
            critique = TradeCritique()
            snapshot = self.user_portfolios.combined_snapshot() if self.user_portfolios.all_portfolios() else self.portfolio.snapshot()
            macro_ctx_for_critique = self.macro_provider.get_context(symbol=idea.asset)
            critique_result = critique.evaluate(idea, recheck, snapshot, macro_ctx_for_critique)

            if critique_result.verdict == "HALT":
                self.audit_chain.append("CRITIQUE_HALT", {
                    "trade_id": trade_id, "asset": idea.asset,
                    "bear_case": critique_result.bear_case,
                    "concerns": critique_result.concerns,
                    "confidence_adjustment": critique_result.confidence_adjustment,
                })
                self._pending_pyramid.pop(trade_id, None)
                self._transition(AgentState.IDLE, f"critique halted {trade_id}")
                return f"Trade HALTED by adversarial review: {critique_result.bear_case}\nConcerns: {'; '.join(critique_result.concerns)}"
            # Apply critique confidence adjustment
            if critique_result.confidence_adjustment != 0:
                idea.confidence = max(0.0, min(1.0, idea.confidence + critique_result.confidence_adjustment))
                audit(trade_log, f"Critique adjusted confidence by {critique_result.confidence_adjustment:+.2f} to {idea.confidence:.3f}",
                      action="critique_adjust", result="ADJUSTED",
                      data={"adjustment": critique_result.confidence_adjustment, "new_confidence": idea.confidence})
                if idea.confidence < CONFIG.risk.min_confidence:
                    # RC-AUD-025: the "user already confirmed, proceed anyway"
                    # rationale only holds when a REAL human pressed Confirm.
                    # Auto-confirm (user_id="auto") and unattended ("") paths
                    # have no deliberate human decision, so a post-critique
                    # sub-min-confidence result must REJECT for them instead of
                    # proceeding.
                    if self._human_confirmed(user_id):
                        # Human made a deliberate decision — warn but proceed.
                        audit(trade_log,
                              f"Post-critique confidence {idea.confidence:.2f} below min {CONFIG.risk.min_confidence} "
                              f"— proceeding anyway (human-confirmed trade via confirm_trade)",
                              action="critique_adjust", result="WARN_OVERRIDE",
                              data={"confidence": idea.confidence, "min": CONFIG.risk.min_confidence,
                                    "user_id": user_id,
                                    "source": getattr(idea, 'source', 'unknown')})
                    else:
                        audit(trade_log,
                              f"Post-critique confidence {idea.confidence:.2f} below min {CONFIG.risk.min_confidence} "
                              f"— REJECTING (not human-confirmed; user_id={user_id!r})",
                              action="critique_adjust", result="REJECT",
                              data={"confidence": idea.confidence, "min": CONFIG.risk.min_confidence,
                                    "user_id": user_id,
                                    "source": getattr(idea, 'source', 'unknown')})
                        self._pending_pyramid.pop(trade_id, None)
                        self._transition(AgentState.IDLE, f"critique sub-min (auto) {trade_id}")
                        return (f"Trade REJECTED: post-critique confidence "
                                f"{idea.confidence:.2f} below minimum "
                                f"{CONFIG.risk.min_confidence} (auto-confirm not permitted "
                                f"to override).")

            if critique_result.verdict == "WARN":
                audit(trade_log, f"Critique WARNING for {trade_id}: {critique_result.bear_case}",
                      action="critique", result="WARN",
                      data={"concerns": critique_result.concerns})
        except Exception as exc:
            # Audit F-13: the critique is the strongest discretionary brake. In
            # paper mode a crash can fail-open (advisory). In LIVE mode a crash
            # must fail CLOSED — a malformed idea/snapshot that crashes the
            # bear-case review should not silently disable it before a real order.
            if CONFIG.is_live():
                audit(trade_log, f"Critique gate error (fail-CLOSED in LIVE): {exc}",
                      action="critique", result="ERROR_FAILCLOSED",
                      data={"trade_id": trade_id, "error": str(exc)[:200]})
                self._pending_pyramid.pop(trade_id, None)
                self._transition(AgentState.IDLE, f"critique error (live) {trade_id}")
                return ("Trade REJECTED: adversarial critique could not complete "
                        "and live mode fails closed on critique errors.")
            audit(trade_log, f"Critique gate error (fail-open): {exc}",
                  action="critique", result="ERROR")

        # Compliance gate: authorize before execution
        action = Permission.LIVE_TRADE if CONFIG.is_live() else Permission.PAPER_TRADE
        macro_ctx = self.macro_provider.get_context(symbol=idea.asset)
        macro_ok = macro_ctx.risk_state != "BLOCK_NEW_ENTRIES"

        # Issue a human-approval token for live-mode compliance (Lock 5).
        # The Telegram /confirm flow is the human approval gate — reaching
        # this point means the operator already tapped "Confirm".
        approval_token = None
        if CONFIG.is_live():
            human = self._human_confirmed(user_id)
            # Audit F-8: only mint the Lock 5 human-approval token for a REAL
            # human confirmation. For non-human callers (user_id "" / "auto" —
            # e.g. auto-confirm or a skill dispatch) require the explicit
            # AUTO_CONFIRM_LIVE_ENABLED opt-in; otherwise leave the token
            # unminted so compliance Lock 5 fails CLOSED and the live trade is
            # denied rather than executed with no human approval at all.
            if human or CONFIG.auto_confirm_live_enabled:
                approval_token = self.compliance.issue_approval_token(
                    trade_id, self.compliance_profile.subject_id,
                )
                if not human:
                    # RC-AUD-018: unattended live execution explicitly opted in.
                    system_log.warning(
                        "AUTO-MINT APPROVAL TOKEN (RC-AUD-018): engine minted the "
                        "Lock 5 token for UNATTENDED trade %s (user_id=%r) under "
                        "AUTO_CONFIRM_LIVE_ENABLED — no human callback occurred.",
                        trade_id, user_id,
                    )
            else:
                system_log.warning(
                    "Lock 5 NOT minted for non-human confirm of %s (user_id=%r) "
                    "and AUTO_CONFIRM_LIVE_ENABLED is off — live execution will be "
                    "denied (audit F-8).", trade_id, user_id,
                )

        compliance_decision = self.compliance.authorize(
            action=action,
            profile=self.compliance_profile,
            live_mode=CONFIG.is_live(),
            risk_passed=(recheck.verdict == RiskVerdict.APPROVED),
            macro_ok=macro_ok,
            notional_usd=recheck.position_size_usd,
            trade_id=trade_id,
            approval_token=approval_token,
        )
        if not compliance_decision.granted:
            self.audit_chain.append("AUTH_DENIED", {
                "trade_id": trade_id, "asset": idea.asset,
                "reasons": compliance_decision.reasons,
                "locks_failed": compliance_decision.locks_failed,
            }, actor=self.compliance_profile.subject_id)
            self._pending_pyramid.pop(trade_id, None)
            self._transition(AgentState.IDLE, f"compliance denied {trade_id}")
            return f"Execution denied: {compliance_decision.reasons[-1] if compliance_decision.reasons else 'compliance check failed'}"

        # ── Per-user PAPER (sim) opt-in ──────────────────────────────────────
        # A user who has opted into practice mode (and the feature is enabled)
        # has THEIR confirmed trade SIMULATED into their paper portfolio instead
        # of sent to the exchange. This branch runs BEFORE the EXECUTING
        # transition, the pyramid SL move (which mutates an exchange stop), and
        # live_executor.execute() — so a paper trade can NEVER place or modify a
        # real order. Default OFF and per-user, so live users are unaffected.
        if (CONFIG.paper_sim_opt_in_enabled and user_id
                and self._user_store is not None
                and self._user_store.sim_opt_in(user_id)):
            self._pending_pyramid.pop(trade_id, None)
            return await self._simulate_paper_fill(idea, recheck, user_id, trade_id)

        # LIVE-ONLY: this bot only executes live trades. Paper mode is disabled.
        if not CONFIG.is_live():
            self._pending_pyramid.pop(trade_id, None)
            self._transition(AgentState.IDLE, "paper mode disabled")
            return "⛔ Paper trading is disabled on this bot. This bot is LIVE-ONLY."

        # ── RC-AUD-018 / Audit F-14: SIMULATION_MODE hard veto ──
        # Final, independent fail-closed gate. It must run BEFORE the EXECUTING
        # transition and before any exchange-mutating side-effect (the pyramid
        # SL→breakeven below calls _update_exchange_sl on a *different* live
        # position). Previously the veto sat just before execute(), so a vetoed
        # confirm could still have modified another position's stop on the
        # exchange. This guard never enables execution — it only ever blocks it.
        if self._live_execution_vetoed_by_simulation():
            self._pending_pyramid.pop(trade_id, None)
            audit(trade_log,
                  f"Live execution VETOED by SIMULATION_MODE for {trade_id}",
                  action="confirm", result="VETO_SIMULATION",
                  data={"trade_id": trade_id, "asset": idea.asset})
            self._transition(AgentState.IDLE, f"simulation hard veto {trade_id}")
            return ("Trade REJECTED: SIMULATION_MODE=true — live execution "
                    "vetoed (hard safety switch).")

        # ── Per-user eligibility gate ────────────────────────────────────────
        # When per-user live trading is ON, a regular (non-operator) human user
        # may only place a live order on THEIR OWN linked account. If they have
        # not linked keys, REJECT here — never silently route their trade to the
        # operator account. No-op while the flag is off, and operator/admin/auto
        # paths always pass, so the operator path is unchanged.
        _elig_ok, _elig_reason = self.per_user_live_eligibility(user_id)
        if not _elig_ok:
            self._pending_pyramid.pop(trade_id, None)
            audit(trade_log,
                  f"Live execution blocked — per-user eligibility: {_elig_reason}",
                  action="confirm", result="REJECT_NOT_ELIGIBLE",
                  data={"trade_id": trade_id, "user_id": user_id, "reason": _elig_reason})
            self._transition(AgentState.IDLE, f"not eligible {trade_id}")
            return (f"Trade REJECTED: {_elig_reason}. Your live trades execute on "
                    "your OWN Bitget account — link it with /connect first.")

        # Live mode — execute via LiveExecutor with micro-test safety limits
        self._transition(AgentState.EXECUTING, f"executing LIVE trade {trade_id}")
        size_usd = recheck.position_size_usd

        # Resolve WHICH executor places this order. With PER_USER_LIVE_ENABLED off
        # (default) this is always the shared operator executor, so everything
        # below is byte-identical to before. With it on, a human user's confirmed
        # trade routes to THEIR own linked account.
        executor = self._executor_for(user_id)

        # ── Pyramid add: half size + move existing SL to breakeven ──
        _pending_pyramid = getattr(self, '_pending_pyramid', {})
        if _pending_pyramid.pop(trade_id, False):
            original_size = size_usd
            size_usd = size_usd * 0.5
            audit(trade_log,
                  f"Pyramid add: half size ${original_size:.2f} -> ${size_usd:.2f}",
                  action="pyramid_half_size", result="APPLIED")

            # Move existing position's SL to breakeven (within the SAME executor's
            # account — a user only ever pyramids onto their own positions).
            symbol_key = normalize_symbol(idea.asset)
            for lp in executor.open_positions:
                lp_key = normalize_symbol(lp.symbol)
                if lp_key == symbol_key and lp.trade_id != trade_id:
                    old_sl = lp.stop_loss
                    lp.stop_loss = lp.entry_price  # breakeven
                    audit(trade_log,
                          f"Pyramid: moved {lp.symbol} SL to breakeven ${lp.entry_price:.4f} (was ${old_sl:.4f})",
                          action="pyramid_sl_breakeven", result="MOVED")
                    # Update exchange SL
                    try:
                        exchange = await executor._get_exchange()
                        await executor._update_exchange_sl(
                            exchange, lp, lp.entry_price)
                    except Exception as exc:
                        logger.debug("Failed to update exchange SL to BE: %s", exc)
                    executor._save_positions()
                    break

        # LIVE FIX: Cap position size at actual exchange equity to prevent
        # InsufficientFunds errors.  The risk engine sizes based on paper
        # portfolio equity; in LIVE mode the real account may be smaller.
        live_bal = self._live_balance_cache
        if live_bal:
            available = live_bal.get("free", 0.0)
            if size_usd > available:
                audit(trade_log,
                      f"Live size clamped: ${size_usd:.2f} -> ${available:.2f} (exchange available)",
                      action="live_size_clamp", result="CLAMPED",
                      data={"requested": round(size_usd, 2), "available": round(available, 2)})
                size_usd = available

        # Manual margin override: if user specified a fixed margin via /trade command.
        # Audit V7 follow-up (double-leverage fix): size_usd is MARGIN everywhere —
        # the live executor multiplies it by leverage to get notional
        # (quantity = size_usd * leverage / price). The old code pre-multiplied
        # here (size_usd = margin * leverage) and the executor multiplied AGAIN,
        # placing margin * leverage**2 notional (e.g. /trade margin 250 at 5x put
        # on $6,250 instead of $1,250). Pass the margin itself so manual trades
        # match the auto path and the user's stated margin.
        if hasattr(self, '_manual_margin_override') and idea.id in self._manual_margin_override:
            manual_margin = self._manual_margin_override.pop(idea.id)
            leverage = CONFIG.exchange.default_leverage
            size_usd = manual_margin  # margin; executor applies leverage for notional
            audit(system_log,
                  f"Manual margin override: ${manual_margin:.2f} margin "
                  f"(≈${manual_margin * leverage:.2f} notional at {leverage}x)",
                  action="manual_margin_override", result="APPLIED",
                  data={"margin": round(manual_margin, 2), "leverage": leverage,
                        "approx_notional": round(manual_margin * leverage, 2)})

        # C2-53 FIX: Reject trade when ATR is missing or zero.
        # A zero ATR produces SL at entry price = immediate stop-out.
        # Skip ATR check for manual trades — user provided explicit SL/TP levels.
        if getattr(idea, 'source', '') == 'manual':
            if not stored_atr or stored_atr <= 0:
                # Use a synthetic ATR based on SL distance so executor can function
                stored_atr = abs(idea.entry_price - idea.stop_loss)
                audit(trade_log,
                      f"Manual trade: synthetic ATR={stored_atr:.4f} from SL distance",
                      action="manual_atr_synthetic", result="OK")
        elif not stored_atr or stored_atr <= 0:
            audit(trade_log,
                  f"No valid ATR for {idea.asset} — aborting to avoid SL-at-entry",
                  action="confirm", result="REJECT",
                  data={"trade_id": trade_id, "stored_atr": stored_atr})
            self._pending_pyramid.pop(trade_id, None)
            self._transition(AgentState.IDLE, f"no ATR for {trade_id}")
            return "Trade REJECTED: no valid ATR available — cannot compute safe SL distance"

        result = await executor.execute(
            idea, size_usd,
            order_type=idea.order_type,
            atr_value=stored_atr,
        )

        # Only record the trade if a LIVE position actually resulted.
        # Audit F-1: classification is centralized in live_executor next to the
        # return strings so the two cannot drift. The old prefix list here
        # missed "REFUSED:" / "EXECUTION BLOCKED:" / "Live execution blocked:"
        # and could not match emoji/HTML-prefixed strings, so blocked trades
        # were sealed to the audit chain as phantom live fills.
        from bot.core.live_executor import execution_indicates_failure
        live_failed = execution_indicates_failure(result)

        if not live_failed:
            # Exchange is single source of truth — no paper duplicate.
            # Position count comes from get_exchange_position_count().
            invalidate_position_count_cache()
            # C-05 FIX: only remove idea and ATR after successful execution
            self._pending_ideas.pop(trade_id, None)
            self._pending_atr.pop(trade_id, None)
            # Cache VWAP at entry for VWAP reversion exit monitoring
            if hasattr(idea, 'signal_type') and idea.signal_type == "vwap_reversion":
                # Extract VWAP from the idea's signals_used/indicators (stored at analysis time)
                entry_vwap = getattr(idea, '_entry_vwap', None) or idea.entry_price
                self._last_vwap[idea.asset] = entry_vwap

        # Seal decision to tamper-evident audit chain
        self.audit_chain.seal_decision(DecisionRecord(
            decision_id=trade_id, symbol=idea.asset,
            idea={"direction": idea.direction.value, "confidence": idea.confidence,
                  "entry": idea.entry_price, "sl": idea.stop_loss, "tp": idea.take_profit},
            risk={"verdict": "APPROVED", "passed": len(recheck.checks_passed),
                  "failed": len(recheck.checks_failed), "size_usd": size_usd},
            macro={"risk_state": macro_ctx.risk_state, "multiplier": macro_ctx.size_multiplier},
            compliance={"granted": True, "locks_passed": compliance_decision.locks_passed},
            outcome="EXECUTED_LIVE", is_paper=False,
        ))
        # Learning: log accepted trade decision
        self.learning.log_decision(
            symbol=idea.asset,
            direction=idea.direction.value,
            confidence=idea.confidence,
            confluence_score=idea.confidence,
            entry_price=idea.entry_price,
            stop_loss=idea.stop_loss,
            take_profit=idea.take_profit,
            risk_reward=idea.risk_reward_ratio,
            position_size_usd=size_usd,
            risk_engine_result="APPROVED",
            checks_passed=recheck.checks_passed,
            checks_failed=[],
            decision="TRADE_ACCEPTED_LIVE",
            paper_trade_id=trade_id,
            confluence_votes=getattr(idea, "_confluence_votes", []),
        )
        self._transition(AgentState.IDLE, "live trade executed")
        return result

    def reject_trade(self, trade_id: str) -> str:
        """Human explicitly rejects a pending idea."""
        idea = self._pending_ideas.pop(trade_id, None)
        self._pending_atr.pop(trade_id, None)  # clean up stored ATR
        self._pending_pyramid.pop(trade_id, None)  # C2-30 FIX: clean up pyramid flag
        if idea:
            audit(
                trade_log,
                f"Trade manually rejected: {trade_id}",
                action="human_reject",
                result="REJECTED",
            )
            return "Trade REJECTED."
        return "Trade not found."

    async def force_scan(self) -> dict:
        """Force an immediate scan cycle, bypassing cooldown and pending gates.

        Called by /forcescan command. Clears pending ideas, resets cooldown,
        and runs a full scan-analyze cycle. Returns a summary dict.
        """
        audit(system_log, "Force scan triggered", action="force_scan", result="START")

        # Clear gates that would block a normal tick
        old_pending = len(self._pending_ideas)
        self._pending_ideas.clear()
        self._pending_atr.clear()
        self._pending_pyramid.clear()
        self._cooldown_until = 0.0

        # Run scan
        self._transition(AgentState.SCANNING, "force scan")
        try:
            signals = await self.scanner.scan()
            self._last_scan_signals = signals or []
        except Exception as exc:
            self._transition(AgentState.IDLE, "force scan error")
            return {"error": str(exc), "signals": 0, "ideas": 0}

        if not signals:
            self._transition(AgentState.IDLE, "force scan: no signals")
            return {"signals": 0, "ideas": 0, "cleared_pending": old_pending}

        # Analyze
        self._transition(AgentState.ANALYZING, "force scan analyzing")

        async def _safe_analyze(sig):
            try:
                return await self._analyze_signal(sig)
            except Exception:
                return None

        tasks = [_safe_analyze(sig) for sig in signals]
        results = await asyncio.gather(*tasks)

        ideas_found = 0
        for idea in results:
            if idea and idea.confidence >= CONFIG.risk.min_confidence:
                idea_key = normalize_symbol(idea.asset)
                for eid, eidea in list(self._pending_ideas.items()):
                    if normalize_symbol(eidea.asset) == idea_key:
                        self._pending_ideas.pop(eid)
                        self._pending_atr.pop(eid, None)
                        break
                self._pending_ideas[idea.id] = idea
                ideas_found += 1

        # Auto-confirm high-confidence ideas (same as normal tick)
        from bot.config import RUNTIME
        auto_threshold = RUNTIME.auto_confirm_threshold
        auto_confirmed = 0
        for tid, tidea in list(self._pending_ideas.items()):
            if tidea.confidence >= auto_threshold:
                try:
                    result = await self.confirm_trade(tid, user_id="auto")
                    auto_confirmed += 1
                    if self._auto_confirm_notify_callback:
                        try:
                            await self._auto_confirm_notify_callback(tidea, result)
                        except Exception:
                            pass
                except Exception:
                    pass

        self._transition(AgentState.IDLE, "force scan complete")

        summary = {
            "signals": len(signals),
            "ideas": ideas_found,
            "auto_confirmed": auto_confirmed,
            "pending": len(self._pending_ideas),
            "cleared_pending": old_pending,
        }
        audit(system_log, f"Force scan complete: {summary}",
              action="force_scan", result="OK", data=summary)
        return summary

    async def _fetch_prices_by_category(self, positions) -> dict[str, float]:
        """Fetch ticker prices using the correct exchange per asset category.

        Splits positions into spot (Crypto) and futures (Metal/Commodity/ETF/etc.)
        groups and fetches from the appropriate exchange in parallel.
        """
        spot_syms = []
        futures_syms = []
        for p in positions:
            sym = p.asset if hasattr(p, "asset") else p.symbol
            cat = _classify_symbol(sym)
            if cat != "Crypto":
                futures_syms.append(sym)
            else:
                spot_syms.append(sym)

        prices: dict[str, float] = {}

        async def _fetch_spot():
            if not spot_syms:
                return {}
            ex = await self.scanner._get_exchange()
            tickers = await ex.fetch_tickers(spot_syms)
            return {s: float(t.get("last", 0)) for s, t in tickers.items()}

        async def _fetch_futures():
            if not futures_syms:
                return {}
            ex = await self.scanner._get_futures_exchange()
            tickers = await ex.fetch_tickers(futures_syms)
            return {s: float(t.get("last", 0)) for s, t in tickers.items()}

        results = await asyncio.gather(
            _fetch_spot(), _fetch_futures(), return_exceptions=True
        )
        for r in results:
            if isinstance(r, dict):
                prices.update(r)
            elif isinstance(r, Exception):
                logger.debug("Price fetch error: %s", r)

        return prices

    async def _evaluate_live_smart_exits(self, executor) -> None:
        """Gated (default OFF): auto-close LIVE positions whose thesis has
        invalidated, instead of letting them ride to the exchange stop-loss.

        Runs the SAME smart-exit checks the paper path already applies in
        ``_check_paper_positions`` — time stop, signal-hold limit, VWAP-reversion
        done/failed, volume-signal decay — against the executor's open positions,
        and closes a fired position at market via ``executor.close_position``.

        Gated behind ``CONFIG.time_stop.enabled`` AND
        ``CONFIG.time_stop.live_auto_close_enabled`` (both must be true; the
        latter defaults False), so live behaviour is byte-identical until an
        operator opts in. Fail-open throughout: any error is swallowed so this
        can never disrupt the SL/TP monitoring that runs alongside it. Never
        bypasses the risk engine — it only ever CLOSES an existing position.
        """
        cfg = CONFIG.time_stop
        if not (cfg.enabled and getattr(cfg, "live_auto_close_enabled", False)):
            return
        try:
            from bot.core.smart_exits import (
                should_time_exit,
                check_signal_hold_limit,
                check_vwap_reversion_exit,
                should_volume_decay_exit,
            )
            prices: dict = {}
            try:
                if self.ws_feed.is_connected():
                    prices = self.ws_feed.get_prices() or {}
            except Exception:
                prices = {}

            for pos in list(getattr(executor, "_positions", {}).values()):
                if getattr(pos, "status", "") != "open":
                    continue
                price = prices.get(pos.symbol) or 0
                if price <= 0 or pos.entry_price <= 0:
                    continue

                hold_h = (datetime.now(UTC) - pos.opened_at).total_seconds() / 3600.0
                candles_held = int(hold_h)  # 1H candles
                if pos.direction == "LONG":
                    risk = pos.entry_price - pos.stop_loss
                    pnl_raw = price - pos.entry_price
                else:
                    risk = pos.stop_loss - pos.entry_price
                    pnl_raw = pos.entry_price - price
                r_mult = pnl_raw / risk if risk > 0 else 0.0

                sig = getattr(pos, "signal_type", "momentum_confluence")
                stype = getattr(pos, "strategy_type", "swing")

                should_exit, reason = should_time_exit(stype, candles_held, r_mult)
                if not should_exit:
                    should_exit, reason = check_signal_hold_limit(sig, hold_h, r_mult)
                if not should_exit:
                    should_exit, reason = should_volume_decay_exit(sig, candles_held, r_mult)
                if not should_exit:
                    vwap = self._last_vwap.get(pos.symbol, 0)
                    if vwap > 0:
                        should_exit, reason = check_vwap_reversion_exit(
                            sig, price, vwap, pos.direction)

                if not should_exit:
                    continue

                audit(trade_log, f"Live smart-exit auto-close: {pos.symbol} — {reason}",
                      action="live_smart_exit", result="CLOSED",
                      data={"symbol": pos.symbol, "r_multiple": round(r_mult, 2),
                            "hold_hours": round(hold_h, 1), "signal_type": sig,
                            "strategy_type": stype})
                try:
                    await executor.close_position(
                        pos.trade_id, reason=f"smart_exit:{reason[:48]}")
                    if self._close_notify_callback:
                        try:
                            await self._close_notify_callback(
                                f"Smart-exit closed {pos.symbol}: {reason}")
                        except Exception as nexc:
                            logger.debug("Smart-exit notify failed: %s", nexc)
                except Exception as cexc:
                    audit(system_log,
                          f"Live smart-exit close failed for {pos.symbol}: {cexc}",
                          action="live_smart_exit", result="ERROR")
        except Exception as exc:
            system_log.debug("Live smart-exit evaluation failed: %s", exc)

    async def _check_open_positions(self) -> None:
        """Monitor open positions for SL/TP hits."""
        positions = self.portfolio.open_positions
        # Run paper monitoring when the shared portfolio OR any per-user (sim
        # opt-in) portfolio has open positions, so opted-in paper trades get
        # SL/TP monitoring even when the shared paper portfolio is empty.
        _user_paper_open = any(
            pf.open_positions for pf in self.user_portfolios.all_portfolios().values())
        if positions or _user_paper_open:
            await self._check_paper_positions(positions)
        # Live positions are checked independently below — do NOT return early
        # when paper portfolio is empty, or live SL/TP monitoring is skipped entirely.

        # Also check live positions if in live mode
        if CONFIG.is_live():
            # SL/TP self-heal: re-place any stop that went missing DURING
            # operation (adopted-unprotected, cancelled SL, deferred-then-filled).
            # verify_and_fix_sltp is idempotent; throttled so it isn't run every
            # tick. Previously this ran ONLY at startup, so a position that became
            # naked mid-session stayed naked until the next restart.
            _now = time.monotonic()
            if (_now - self._last_sltp_verify_ts) >= self._SLTP_VERIFY_INTERVAL:
                self._last_sltp_verify_ts = _now
                for _ex in self._all_live_executors():
                    try:
                        await _ex.verify_and_fix_sltp()
                    except Exception as _vexc:
                        audit(system_log, f"Periodic SL/TP self-heal error: {_vexc}",
                              action="periodic_sltp_verify", result="ERROR")

            # Monitor every account (operator + any per-user). With per-user off
            # this loops once over the operator — identical to before.
            for _ex in self._all_live_executors():
                try:
                    live_closed = await _ex.check_positions()
                    for msg in live_closed:
                        # Distinguish limit fills from actual closes
                        is_fill = msg.startswith("LIMIT FILLED:")
                        if is_fill:
                            audit(trade_log, f"Limit order filled: {msg}",
                                  action="limit_fill_notify", result="FILLED")
                            if self._fill_notify_callback:
                                try:
                                    await self._fill_notify_callback(msg)
                                except Exception as exc:
                                    logger.debug("Fill notify failed: %s", exc)
                            continue

                        audit(trade_log, f"Live position auto-closed: {msg}",
                              action="live_auto_close", result="CLOSED")
                        # C-08 FIX: trigger cooldown on live losses
                        last_close = getattr(_ex, '_last_close_data', None)
                        if last_close and last_close.get('pnl_usd', 0) < 0:
                            self._cooldown_until = (
                                time.monotonic() + CONFIG.risk.cooldown_after_loss_seconds
                            )
                            self._transition(
                                AgentState.COOLING_DOWN,
                                f"live loss on {last_close.get('symbol', '?')} "
                                f"(PnL=${last_close['pnl_usd']}), "
                                f"cooling down {CONFIG.risk.cooldown_after_loss_seconds}s",
                            )
                        if self._close_notify_callback:
                            try:
                                await self._close_notify_callback(msg)
                            except Exception as exc:
                                logger.debug("Close notify failed: %s", exc)
                except Exception as exc:
                    audit(system_log, f"Live position monitor error: {exc}",
                          action="live_monitor", result="ERROR")

                # F-14 FIX: Reconcile tracked positions with exchange
                # Detects positions closed by exchange-side SL/TP triggers
                try:
                    reconciled = await _ex.reconcile_positions()
                    for msg in reconciled:
                        audit(trade_log, f"Position reconciled: {msg}",
                              action="reconcile", result="CLOSED")
                        if self._close_notify_callback:
                            try:
                                await self._close_notify_callback(msg)
                            except Exception as exc:
                                logger.debug("Close notify (reconcile) failed: %s", exc)
                except Exception as exc:
                    audit(system_log, f"Reconciliation error: {exc}",
                          action="reconcile", result="ERROR")

                # Gated (default OFF): auto-close live positions whose thesis has
                # invalidated (time stop / signal-hold limit / VWAP reversion /
                # volume decay) instead of letting them ride to the exchange SL.
                await self._evaluate_live_smart_exits(_ex)

            # Periodic orphan adoption: catch positions opened on exchange
            # but not tracked locally (e.g., after bot restart, manual trades,
            # or failed adoption on startup).  Runs every tick alongside
            # reconciliation to keep local state in sync.
            try:
                sync_msgs = await sync_portfolio_with_exchange(self)
                for msg in sync_msgs:
                    if "Adopted" in msg or "Ghost" in msg or "Orphan" in msg:
                        audit(system_log, f"Periodic sync: {msg}",
                              action="periodic_exchange_sync", result="SYNCED")
                        if self._adopt_notify_callback and "Adopted" in msg:
                            try:
                                await self._adopt_notify_callback(msg)
                            except Exception as exc:
                                logger.debug("Adopt notify failed: %s", exc)
            except Exception as exc:
                audit(system_log, f"Periodic exchange sync error: {exc}",
                      action="periodic_exchange_sync", result="ERROR")

    async def _check_paper_positions(self, positions) -> None:
        """Monitor paper portfolio positions for SL/TP hits."""
        try:
            # Price every paper symbol: the shared portfolio's positions PLUS all
            # per-user (sim opt-in) portfolios, so opted-in paper positions are
            # priced and SL/TP-monitored (via check_stops_all below) even when the
            # shared portfolio is empty. The exit loops below still iterate only
            # the shared portfolio, so this never closes a user position in the
            # wrong book.
            _priced_positions = list(positions) + [
                p for pf in self.user_portfolios.all_portfolios().values()
                for p in pf.open_positions]

            # Prefer WebSocket prices (sub-second) over REST (polling)
            if self.ws_feed.is_connected():
                ws_prices = self.ws_feed.get_prices()
                if ws_prices:
                    prices = ws_prices
                else:
                    prices = await self._fetch_prices_by_category(_priced_positions)
            else:
                prices = await self._fetch_prices_by_category(_priced_positions)

            # H-05 FIX: validate prices before use — reject any price that
            # deviates more than 50% from the last known good price for that symbol.
            validated_prices: dict[str, float] = {}
            for sym, px in prices.items():
                if px <= 0:
                    continue
                last_px = self._last_known_prices.get(sym)
                if last_px is not None and last_px > 0:
                    deviation = abs(px - last_px) / last_px
                    if deviation > 0.50:
                        logger.warning(
                            "Price validation: %s price %.6f deviates %.1f%% from last known %.6f — skipped",
                            sym, px, deviation * 100, last_px,
                        )
                        continue
                # Price is valid — update last known and include in validated set
                self._last_known_prices[sym] = px
                validated_prices[sym] = px
            prices = validated_prices

            # Feed prices to cross-asset tracker
            for _ca_sym, _ca_px in prices.items():
                try:
                    self.cross_asset.feed_price(_ca_sym, _ca_px)
                except Exception:
                    pass

            # Feed prices to risk engine for correlation v2
            for _rp_sym, _rp_px in prices.items():
                try:
                    self.risk.update_price_history(_rp_sym, _rp_px)
                except Exception:
                    pass

            # Subscribe open position symbols to WS feed for future ticks
            pos_symbols = [p.asset for p in _priced_positions]
            self.ws_feed.subscribe(pos_symbols)
            # Mark-to-market: feed current prices so snapshot() reflects unrealized PnL
            self.portfolio.mark_to_market(prices)
            # Also update all per-user portfolios
            self.user_portfolios.mark_to_market_all(prices)

            # ── Time-based exit: close dead trades with no R progress ──
            try:
                from bot.core.smart_exits import should_time_exit
                for pos in list(positions):
                    # Calculate candles held (1H candles)
                    hold_secs = (datetime.now(UTC) - pos.opened_at).total_seconds()
                    candles_held = int(hold_secs / 3600)  # 1H candles

                    # Calculate current R-multiple
                    current_price = prices.get(pos.asset)
                    if current_price and pos.entry_price > 0:
                        if pos.direction.value == "LONG":
                            risk = pos.entry_price - pos.stop_loss
                            pnl_raw = current_price - pos.entry_price
                        else:
                            risk = pos.stop_loss - pos.entry_price
                            pnl_raw = pos.entry_price - current_price
                        r_multiple = pnl_raw / risk if risk > 0 else 0.0

                        should_exit, reason = should_time_exit(
                            strategy_type=pos.strategy_type,
                            candles_held=candles_held,
                            current_r_multiple=r_multiple,
                        )

                        if should_exit:
                            audit(trade_log, f"Time exit triggered: {pos.asset} — {reason}",
                                  action="time_exit", result="CLOSED",
                                  data={"symbol": pos.asset, "candles": candles_held,
                                        "r_multiple": round(r_multiple, 2),
                                        "strategy_type": pos.strategy_type})
                            # Close the position at current price
                            self.portfolio.close_position(
                                pos.trade_id, current_price
                            )
            except Exception as exc:
                system_log.debug("Time-based exit check failed: %s", exc)

            # ── Signal-type hold limit check ──
            try:
                from bot.core.smart_exits import check_signal_hold_limit, check_vwap_reversion_exit
                for pos in list(self.portfolio.open_positions):
                    current_price = prices.get(pos.asset)
                    if not current_price or current_price <= 0:
                        continue

                    hold_secs = (datetime.now(UTC) - pos.opened_at).total_seconds()
                    holding_hours = hold_secs / 3600

                    # R-multiple calculation
                    if pos.direction.value == "LONG":
                        risk = pos.entry_price - pos.stop_loss
                        pnl_raw = current_price - pos.entry_price
                    else:
                        risk = pos.stop_loss - pos.entry_price
                        pnl_raw = pos.entry_price - current_price
                    r_multiple = pnl_raw / risk if risk > 0 else 0.0

                    signal_type = getattr(pos, 'signal_type', 'momentum_confluence')

                    # Signal hold limit
                    should_exit, reason = check_signal_hold_limit(
                        signal_type=signal_type,
                        holding_hours=holding_hours,
                        current_r_multiple=r_multiple,
                    )
                    if should_exit:
                        audit(trade_log, f"Signal hold exit: {pos.asset} — {reason}",
                              action="signal_hold_exit", result="CLOSED")
                        self.portfolio.close_position(pos.trade_id, current_price)
                        continue

                    # VWAP reversion exit (best-effort: skip if no VWAP available)
                    vwap = self._last_vwap.get(pos.asset, 0)
                    if vwap > 0:
                        should_exit, reason = check_vwap_reversion_exit(
                            signal_type=signal_type,
                            current_price=current_price,
                            vwap=vwap,
                            direction=pos.direction.value,
                        )
                        if should_exit:
                            audit(trade_log, f"VWAP exit: {pos.asset} — {reason}",
                                  action="vwap_exit", result="CLOSED")
                            self.portfolio.close_position(pos.trade_id, current_price)
            except Exception as exc:
                system_log.debug("Signal hold check failed: %s", exc)

            closed = self.portfolio.check_stops(prices)
            # Check stops for per-user portfolios too
            user_closed = self.user_portfolios.check_stops_all(prices)
            # Merge user-closed trades into the main notification flow
            for uid, user_trades in user_closed.items():
                closed.extend(user_trades)
            for c in closed:
                audit(
                    trade_log,
                    f"Position auto-closed: {c.asset} PnL=${c.pnl}",
                    action="auto_close",
                    result="CLOSED",
                )
                # Enter cooldown after a loss
                if c.pnl < 0:
                    self._cooldown_until = (
                        time.monotonic() + CONFIG.risk.cooldown_after_loss_seconds
                    )
                    self._transition(
                        AgentState.COOLING_DOWN,
                        f"loss on {c.asset} (PnL=${c.pnl}), "
                        f"cooling down {CONFIG.risk.cooldown_after_loss_seconds}s",
                    )
                # Record to trade journal
                try:
                    self.journal.record_trade(
                        trade_id=getattr(c, 'trade_id', '') or '',
                        symbol=c.asset,
                        direction=c.direction.value if hasattr(c.direction, 'value') else str(c.direction),
                        strategy_type=getattr(c, 'strategy_type', ''),
                        entry_price=c.entry_price,
                        exit_price=getattr(c, 'exit_price', None) or 0,
                        stop_loss=c.stop_loss,
                        take_profit=c.take_profit,
                        pnl=c.pnl,
                        confidence=getattr(c, '_confidence', 0),
                        signals_used=getattr(c, '_signals_used', []),
                        regime=getattr(self.risk, '_current_regime', ''),
                        holding_hours=((c.closed_at - c.opened_at).total_seconds() / 3600) if getattr(c, 'closed_at', None) and getattr(c, 'opened_at', None) else 0,
                    )
                except Exception:
                    pass
                # Record time-of-day outcome
                try:
                    from datetime import datetime as _dt
                    hour_utc = _dt.now(UTC).hour
                    is_win = c.pnl > 0
                    self.time_of_day.record(c.asset, hour_utc, is_win)
                except Exception:
                    pass
                # Record hold-time analytics
                try:
                    hold_h = ((c.closed_at - c.opened_at).total_seconds() / 3600) if getattr(c, 'closed_at', None) and getattr(c, 'opened_at', None) else 0
                    if hold_h > 0 and c.entry_price > 0:
                        if c.direction.value == "LONG":
                            risk = c.entry_price - c.stop_loss
                        else:
                            risk = c.stop_loss - c.entry_price
                        final_r = c.pnl / (risk * c.quantity) if risk > 0 and c.quantity > 0 else 0
                        self.hold_analytics.record(
                            strategy_type=c.strategy_type,
                            holding_hours=hold_h,
                            r_multiple=final_r,
                            is_win=c.pnl > 0,
                        )
                except Exception:
                    pass
        except Exception as exc:
            audit(
                system_log,
                f"Position monitor error: {exc}",
                action="monitor",
                result="ERROR",
            )
            self._transition(AgentState.IDLE, f"monitor error: {exc}")

    @property
    def pending_ideas(self) -> list[TradeIdea]:
        return list(self._pending_ideas.values())

    # -- Portfolio Heat / Auto-Rebalance --

    def check_portfolio_heat(self) -> dict:
        """Compute portfolio exposure and determine if rebalancing is needed.

        Returns dict with:
          - total_exposure_pct: sum of position values / equity
          - max_single_exposure_pct: largest single position / equity
          - needs_rebalance: True if total > 60% or single > 30%
          - rebalance_actions: list of suggested reduction actions
        """
        snap = self.portfolio.snapshot()
        equity = snap.equity_usd
        if equity <= 0:
            return {
                "total_exposure_pct": 0.0,
                "max_single_exposure_pct": 0.0,
                "needs_rebalance": False,
                "rebalance_actions": [],
            }

        positions = self.portfolio.open_positions
        if not positions:
            return {
                "total_exposure_pct": 0.0,
                "max_single_exposure_pct": 0.0,
                "needs_rebalance": False,
                "rebalance_actions": [],
            }

        # Compute per-position exposure
        exposures: dict[str, float] = {}
        total_exposure = 0.0
        for pos in positions:
            pos_value = self.portfolio.get_position_value(pos.asset)
            exposure_pct = (pos_value / equity) * 100
            exposures[pos.asset] = exposure_pct
            total_exposure += exposure_pct

        max_single = max(exposures.values()) if exposures else 0.0

        # Determine rebalance need
        needs_rebalance = total_exposure > 60.0 or max_single > 30.0

        # Generate suggested actions
        actions: list[str] = []
        if needs_rebalance:
            for asset, exp_pct in sorted(exposures.items(), key=lambda x: -x[1]):
                if exp_pct > 30.0:
                    # Suggest reducing to 25%
                    reduce_pct = round((1 - 25.0 / exp_pct) * 100)
                    actions.append(f"Reduce {asset} by {reduce_pct}% (currently {exp_pct:.1f}% of equity)")
                elif total_exposure > 60.0 and exp_pct > 15.0:
                    # Suggest reducing larger positions proportionally
                    target = exp_pct * (55.0 / total_exposure)
                    reduce_pct = round((1 - target / exp_pct) * 100)
                    if reduce_pct > 5:
                        actions.append(f"Reduce {asset} by {reduce_pct}% (currently {exp_pct:.1f}% of equity)")

        return {
            "total_exposure_pct": round(total_exposure, 2),
            "max_single_exposure_pct": round(max_single, 2),
            "needs_rebalance": needs_rebalance,
            "rebalance_actions": actions,
        }

    def get_rebalance_signals(self) -> list[dict]:
        """Return rebalance signals for the War Room display.

        Respects a minimum 4-hour interval between checks to avoid
        excessive computation. Returns empty list if checked too recently.
        """
        now = time.monotonic()
        if self._last_rebalance_check and (now - self._last_rebalance_check) < self._rebalance_interval:
            return []

        self._last_rebalance_check = now
        heat = self.check_portfolio_heat()

        if not heat["needs_rebalance"]:
            return []

        signals = []
        for action in heat["rebalance_actions"]:
            signals.append({
                "type": "REBALANCE",
                "action": action,
                "total_exposure_pct": heat["total_exposure_pct"],
                "max_single_exposure_pct": heat["max_single_exposure_pct"],
                "timestamp": datetime.now(UTC).isoformat(),
            })

        if signals:
            audit(
                system_log,
                f"Rebalance needed: total={heat['total_exposure_pct']:.1f}%, "
                f"max_single={heat['max_single_exposure_pct']:.1f}%",
                action="rebalance_check",
                result="REBALANCE_NEEDED",
                data=heat,
            )

        return signals
