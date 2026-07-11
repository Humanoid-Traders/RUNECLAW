"""
RUNECLAW Live Executor — places real orders on Bitget via ccxt.

Safety invariants:
  - MICRO_TEST_MODE caps every position at $10 and total exposure at $50
  - Every order is audited before and after submission
  - Market AND limit orders supported (configurable via DEFAULT_ORDER_TYPE)
  - Trailing stops: activates after 1R profit, trails at 1.5x ATR (shared with paper)
  - Fail-closed: any API error aborts the trade and logs the failure
  - The executor never modifies risk limits or bypasses any gate
  - SL/TP are placed as separate stop-market / take-profit-market orders
  - Trailing SL updates cancel+replace exchange strategy orders
  - F-07 FIX: Positions are persisted to disk and reconciled on restart
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from bot.compat import UTC
from typing import Any, Callable, Optional, cast

import ccxt.async_support as ccxt

from bot.config import CONFIG
from bot.utils.logger import audit, trade_log, system_log
from bot.utils.models import Direction, TradeIdea
from bot.utils.trailing import make_trailing_state, update_trailing_stop
from bot.utils.close_reason import stop_exit_label
from bot.core.order_rules import (
    is_market_open, is_weekend_queued, adjust_sl_for_gap_risk,
    adjust_size_for_weekend, should_defer_tp_sl,
)
from bot.core.limit_entry import calculate_entry
from bot.core.market_scanner import _classify_symbol

logger = logging.getLogger(__name__)


def normalize_symbol(s: str) -> str:
    """Canonical symbol normalizer — strips ccxt suffixes to a bare base.

    Examples:
        MEGA/USDT:USDT  →  MEGA
        MEGA/USDT       →  MEGA
        MEGAUSDT        →  MEGAUSDT  (no destructive mid-string strip)
        XAU/USDT:USDT   →  XAU
        BTC/USDC:USDC   →  BTC
    """
    result = s.upper()
    # L-01 FIX: Strip any :XXX settle suffix (not just :USDT)
    colon_idx = result.rfind(":")
    if colon_idx > 0:
        result = result[:colon_idx]
    if result.endswith("/USDT"):
        result = result[:-5]
    elif result.endswith("/USDC"):
        result = result[:-5]
    return result


def display_symbol(s: str) -> str:
    """Format a ccxt symbol for user-facing display.

    Examples:
        MEGA/USDT:USDT  →  MEGAUSDT
        MEGA/USDT       →  MEGAUSDT
        MEGAUSDT        →  MEGAUSDT
        BTC/USDC:USDC   →  BTCUSDC
    """
    # L-01 FIX: Strip any :XXX settle suffix, not just :USDT
    result = s.replace("/", "")
    colon_idx = result.rfind(":")
    if colon_idx > 0:
        result = result[:colon_idx]
    return result


# ── Safety limits ────────────────────────────────────────────────────
# Live position caps (MARGIN). Now operator-configurable via env
# (MICRO_MAX_POSITION_USD / MICRO_MAX_TOTAL_EXPOSURE / MICRO_MAX_OPEN_POSITIONS),
# sourced from CONFIG.execution. Defaults are the conservative micro-test values
# ($100 margin/trade, $500 total, 5 positions) — raise them for real-size live
# trading. Read once at import, so set the env before launch.
MICRO_MAX_POSITION_USD = CONFIG.execution.max_live_position_usd          # Max margin per trade
MICRO_MAX_TOTAL_EXPOSURE = CONFIG.execution.max_live_total_exposure_usd  # Max total margin exposure
MICRO_MAX_OPEN_POSITIONS = CONFIG.execution.max_live_open_positions      # Max concurrent positions


# ── Execution result classification (audit F-1) ──────────────────────
# execute() and the limit-order path return a human-readable string. The
# engine must decide from that string whether a LIVE position actually
# resulted — if not, the pending idea is retried and nothing is recorded.
#
# This classifier lives next to the return statements so the producer and
# the consumer (engine.confirm_trade) cannot drift. The prior engine-side
# prefix list missed several block strings ("REFUSED:", "EXECUTION BLOCKED:",
# "Live execution blocked:") AND could never match the emoji/HTML-prefixed
# strings ("⚠️ <b>EXECUTION ABORTED…", "🚨 <b>URGENT…"), so blocked trades
# were recorded as phantom live fills. We use distinctive substring TOKENS
# (not startswith) so the leading icon/markup does not defeat the match.
#
# A position-was-CLOSED outcome (EXECUTION ABORTED) counts as a failure: no
# live position remains. The "URGENT — LIVE with NO stop-loss" outcome does
# NOT count as a failure, because a real position exists and retrying would
# open a second one — that case must be recorded so it is tracked.
_EXECUTION_FAILURE_TOKENS = (
    "EXECUTION FAILED",
    "EXECUTION BLOCKED",
    "EXECUTION ABORTED",   # position opened then closed for safety — none remains
    "INSUFFICIENT FUNDS",
    "INVALID ORDER",
    "BLOCKED:",
    "PREFLIGHT FAILED",
    "REFUSED:",
    "Live execution blocked",
)


def min_amount_step(precision_amount: Any) -> float:
    """Interpret ccxt ``market['precision']['amount']`` as an amount STEP.

    Bitget (TICK_SIZE mode) reports the step directly (e.g. 0.001); older ccxt
    builds report DECIMAL PLACES (e.g. 3 → 10**-3). Defensive: values in (0, 1]
    are treated as an already-a-step; integers > 1 as decimal places; anything
    else → 0.0 (unknown, never inflate the floor)."""
    try:
        p = float(precision_amount) if precision_amount is not None else 0.0
    except (TypeError, ValueError):
        return 0.0
    if 0 < p <= 1:
        return p
    if p > 1 and p.is_integer():
        return 10.0 ** -int(p)
    return 0.0


def resolve_exchange_min_quantity(
    quantity: float, floor: float, step: float, min_cost: float, price: float,
    roundup_enabled: bool, max_mult: float,
) -> tuple[Optional[float], float, float]:
    """Decide what to do with a quantity that is below the venue minimum.

    Returns ``(resolved_qty, q_min, mult)``:
      * ``resolved_qty`` is the quantity to use (== ``q_min``) when the overshoot
        is small enough to round UP, or ``None`` when the trade should be
        SKIPPED (round-up disabled, or overshoot beyond ``max_mult``).
      * ``q_min`` is the smallest quantity clearing both the amount floor and the
        min-notional, snapped up to the step grid.
      * ``mult`` is ``q_min / quantity`` (the overshoot ratio; inf if qty ≤ 0).

    Pure — no config/exchange access, so the round-up policy is unit-testable.
    """
    import math as _math
    q_min = float(floor or 0.0)
    if min_cost > 0 and price > 0:
        need = min_cost / price
        if step > 0:
            need = _math.ceil(need / step - 1e-9) * step
        q_min = max(q_min, need)
    if step > 0 and q_min > 0:
        q_min = _math.ceil(q_min / step - 1e-9) * step
    mult = (q_min / quantity) if quantity > 0 else float("inf")
    if roundup_enabled and q_min > 0 and quantity > 0 and mult <= float(max_mult):
        return q_min, q_min, mult
    return None, q_min, mult


def recalc_sl_tp_for_shifted_entry(
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    limit_price: float,
    natural_sl: Optional[float],
    side: str,
) -> tuple[float, float, bool, str]:
    """Pure SL/TP adjustment for a recalculated resting limit price.

    1. Shift SL/TP by the entry displacement so the stop DISTANCE — which
       drove position sizing and the leverage margin-risk cap — is preserved
       at the new entry. Without this a LONG can fill at/below its own
       unshifted stop when the limit moves up to ~2 ATR.
    2. Apply the structure-based natural SL only if it TIGHTENS the stop and
       sits on the correct side of the entry: a wider stop after sizing
       silently raises dollar risk, and a wrong-side natural (entry cluster
       below the session low) would stop out instantly.

    Returns (new_sl, new_tp, shifted, natural_outcome) where natural_outcome
    is "applied", "rejected_wrong_side", "rejected_wider" or "".
    """
    new_sl, new_tp = stop_loss, take_profit
    shifted = False
    entry_shift = limit_price - entry_price
    if entry_shift != 0:
        cand_sl = stop_loss + entry_shift
        cand_tp = take_profit + entry_shift
        if cand_sl > 0 and cand_tp > 0:
            new_sl, new_tp = cand_sl, cand_tp
            shifted = True

    natural_outcome = ""
    if natural_sl:
        correct_side = (natural_sl < limit_price) if side == "buy" else (natural_sl > limit_price)
        current_dist = abs(limit_price - new_sl) / limit_price
        natural_dist = abs(limit_price - natural_sl) / limit_price
        if not correct_side:
            natural_outcome = "rejected_wrong_side"
        elif 0 < natural_dist < current_dist:
            new_sl = natural_sl
            natural_outcome = "applied"
        else:
            natural_outcome = "rejected_wider"
    return new_sl, new_tp, shifted, natural_outcome


def execution_indicates_failure(result: str) -> bool:
    """True when execute()'s result string means NO live position resulted.

    Fail-closed: anything that is not a recognized success is treated as a
    failure by the caller is NOT the contract here — this returns True only
    for known no-position outcomes. The caller treats the inverse (a real
    fill, including the unprotected-but-live emergency case) as executed.
    """
    if not isinstance(result, str):
        return True
    return any(token in result for token in _EXECUTION_FAILURE_TOKENS)

# F-07 FIX: Persistence file for live positions
_POSITIONS_FILE = os.path.join(
    os.environ.get("RUNECLAW_STATE_DIR", "data"), "live_positions.json"
)
# F-14 FIX: Separate persistence for closed trades (survives restarts)
_CLOSED_TRADES_FILE = os.path.join(
    os.environ.get("RUNECLAW_STATE_DIR", "data"), "closed_trades.json"
)
_MAX_CLOSED_TRADES = 500  # Cap closed trade history
# F-13 FIX: Maximum order history retained in memory
_MAX_ORDER_HISTORY = 200
# Orphan-adoption false-positive guard (see _recent_local_opens): grace period
# after a genuine local open during which the same symbol is never
# re-"adopted" as an orphan by adopt_exchange_positions()/
# adopt_exchange_limit_orders(), regardless of exact-match quirks.
_RECENT_LOCAL_OPEN_GRACE = 300  # seconds


def _user_state_path(base_file: str, state_dir: Optional[str], user_id) -> str:
    """Resolve a per-user state file path.

    Per-user live trading (PER_USER_LIVE_ENABLED, default OFF) runs one
    LiveExecutor per user, each bound to its own position/closed-trade files so
    accounts never share state. For the **shared operator executor**
    (``user_id is None`` and ``state_dir is None``) this returns the original
    module-level path UNCHANGED — the operator's on-disk layout is byte-identical
    to before this refactor. When a ``user_id`` is given, the filename is suffixed
    with it (e.g. ``live_positions_12345.json``).
    """
    if user_id is None and state_dir is None:
        return base_file
    d: str = state_dir or os.environ.get("RUNECLAW_STATE_DIR") or "data"
    name = os.path.basename(base_file)
    if user_id is not None:
        stem, ext = os.path.splitext(name)
        name = f"{stem}_{user_id}{ext}"
    return os.path.join(d, name)


@dataclass
class LiveOrder:
    """Record of a live order placed on the exchange."""
    order_id: str
    symbol: str
    side: str          # "buy" or "sell"
    order_type: str    # "market", "limit"
    amount: float      # quantity in base currency
    price: float       # fill price (0 if pending)
    cost_usd: float    # total cost in USDT
    status: str        # "filled", "open", "canceled", "failed"
    client_oid: str = ""  # idempotency key (Bitget clientOid)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    raw: dict = field(default_factory=dict)


@dataclass
class LivePosition:
    """A tracked live position with SL/TP order IDs."""
    trade_id: str
    symbol: str
    direction: str         # "LONG" or "SHORT"
    entry_price: float
    quantity: float        # base currency amount
    cost_usd: float
    stop_loss: float
    take_profit: float
    leverage: int = 1      # leverage multiplier (1 = no leverage)
    is_spot: bool = False   # DEPRECATED: always False (futures-only mode)
    sl_order_id: Optional[str] = None
    tp_order_id: Optional[str] = None
    opened_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    closed_at: Optional[datetime] = None
    close_price: Optional[float] = None
    pnl_usd: Optional[float] = None
    status: str = "open"   # "open", "closed", "error", "pending_fill"
    # Trailing stop state (managed by bot/utils/trailing.py)
    trailing_state: Optional[dict] = None
    # Partial take-profit ladder state (bot/core/partial_tp.py), serialized dict.
    partial_tp_state: Optional[dict] = None
    # Order type: "market" (filled immediately) or "limit" (pending fill)
    order_type: str = "market"
    # For limit orders: the exchange order ID to poll for fills
    limit_order_id: Optional[str] = None
    # ATR at entry time — needed for trailing stop initialization
    atr_at_entry: float = 0.0
    # Strategy type: "scalp" | "intraday" | "swing" | "position"
    strategy_type: str = "swing"
    # Signal type: "momentum_confluence" | "vwap_reversion" | "regime_trend" | "volume_spike" | "funding_arb" | "unknown"
    signal_type: str = "momentum_confluence"
    # Fee tracking: commission deducted from PnL
    gross_pnl: Optional[float] = None
    commission: Optional[float] = None
    # Reason the position was closed (e.g. "SL", "TP", "manual", error status)
    close_reason: Optional[str] = None
    # Provenance (forensic aid — TI-a4ba8a82 was unrecoverable without it):
    #   origin      — how the position began: "executed" (normal signal),
    #                 "adopted" (orphan on exchange), "reclaimed" (own limit
    #                 order re-tracked).
    #   fill_source — how the CLOSE record was sourced:
    #                 "bitget_position_history"/"exchange_fill_*"/"closed_order"
    #                 (authoritative) vs "ticker_fallback" (inferred, not truth).
    origin: str = "executed"
    fill_source: Optional[str] = None


class LiveExecutor:
    """Executes real trades on Bitget with micro-test safety limits.

    Usage:
        executor = LiveExecutor()
        result = await executor.execute(idea, size_usd=100.0)
    """

    def __init__(self, user_id=None, credentials: Optional[dict] = None,
                 state_dir: Optional[str] = None) -> None:
        # Per-user live trading (PER_USER_LIVE_ENABLED, default OFF): when a
        # user_id + credentials are supplied, this executor trades THAT user's
        # own Bitget account and persists to per-user state files. The default
        # (all None) is the shared operator executor — credentials come from
        # CONFIG.exchange and the state files are the original module paths, so
        # the operator path is byte-identical to before. credentials, when set,
        # is {"api_key", "api_secret", "passphrase"} (from the encrypted store).
        self.user_id = user_id
        self._credentials = credentials
        self._positions_file = _user_state_path(_POSITIONS_FILE, state_dir, user_id)
        self._closed_trades_file = _user_state_path(_CLOSED_TRADES_FILE, state_dir, user_id)
        self._exchange: Optional[ccxt.Exchange] = None
        self._positions: dict[str, LivePosition] = {}
        self._closed_trades: list[LivePosition] = []  # F-14: persisted closed trades
        self._order_history: list[LiveOrder] = []
        self._hedge_mode: Optional[bool] = None  # None=unknown, True=hedge, False=one-way
        self._is_uta: Optional[bool] = None  # None=unknown, cached after first detection
        self._actual_margin_mode: Optional[str] = None  # Actual margin mode reported by exchange
        self._persistence_broken: bool = False  # C-02: set True if position save fails
        self._last_close_data: Optional[dict] = None  # Structured data from most recent close
        # C2-02 FIX: Per-trade-id locks to prevent double-close race condition.
        # close_position() is called from check_positions, reconcile_positions,
        # and Telegram handler — all can race on the same trade_id.
        self._close_locks: dict[str, asyncio.Lock] = {}
        # C2-27: Track consecutive ticker fetch failures per symbol
        self._ticker_failure_count: dict[str, int] = {}
        # Callback: invoked after any position is closed (for balance cache invalidation)
        self.on_position_closed: Optional[Callable] = None
        # Exchange sync: periodically check for untracked positions
        self._last_exchange_sync: float = 0
        self._EXCHANGE_SYNC_INTERVAL: float = 300  # 5 minutes
        # Orphan-adoption false-positive guard: symbol -> time.time() of the
        # most recent GENUINE local open (fresh order placement, not an
        # adoption itself). adopt_exchange_positions()/adopt_exchange_limit_orders()
        # skip a symbol here for a grace window so a bot-placed trade can never
        # be re-"adopted" as its own orphan while local bookkeeping is still
        # settling (SL/TP placement, fill confirmation, etc. all take multiple
        # await points after the position/order is first recorded).
        self._recent_local_opens: dict[str, float] = {}
        # Last SL/TP placement rejection per symbol (Bitget code + msg), so the
        # UNPROTECTED-position operator alert can say WHY the stop couldn't land
        # (precision/min-distance/no-position/etc.) instead of a bare "could not
        # be placed". Diagnostic only — never gates any order logic. Cleared on a
        # successful placement.
        self._last_sltp_error: dict[str, str] = {}
        # Trade IDs whose "closing" status was reset to "open" by
        # _load_positions()'s startup recovery (see there for why). Their
        # true state is ambiguous — a close order may have already reached
        # the exchange before the process died mid-close. Local SL/TP/
        # time-stop heuristics in check_positions() skip these (they'd
        # submit a REDUNDANT close order priced off a stale ticker) and
        # defer entirely to reconcile_positions(), which queries the
        # exchange directly and, on finding the position already gone,
        # finalizes it from Bitget's own authoritative close record instead
        # of guessing — and does so quietly (no duplicate notification),
        # since the original close very likely already notified the user
        # before the process died. Cleared once reconcile has resolved the
        # position's true state (closed for real, or confirmed still open).
        self._recovered_from_closing: set[str] = set()
        # Dynamic leverage: ATR-based volatility ratios per symbol
        self._last_atr_pct: dict[str, float] = {}  # symbol -> ATR/price ratio
        # Slippage tracker: set by engine
        self._slippage_tracker = None  # set by engine
        # Graceful degradation state
        self._degraded_mode: bool = False
        self._ws_last_seen: float = time.time()
        self._api_error_count: int = 0
        # Warning rate circuit breaker: reference to risk engine (set by engine.py)
        self._risk_engine: Optional[Any] = None
        # Real-time price feed handle (set by engine.py). When present,
        # check_degradation reads the feed's true last-message age instead of the
        # coarse _ws_last_seen shadow clock. None in paper/tests → shadow clock.
        self._ws_feed: Optional[Any] = None
        # F-07 FIX: Load persisted positions on startup
        self._load_positions()
        # F-14 FIX: Load persisted closed trades on startup
        self._load_closed_trades()

    # ── Dynamic leverage & graceful degradation helpers ──────────────

    def _record_warning(self, key: str) -> None:
        """Forward infrastructure warning to risk engine for rate tracking."""
        if self._risk_engine is not None:
            try:
                self._risk_engine.record_warning(key)
            except Exception:
                pass  # risk engine itself is broken — don't recurse

    def update_atr(self, symbol: str, atr_pct: float) -> None:
        """Update ATR ratio for dynamic leverage calculation."""
        self._last_atr_pct[symbol] = atr_pct

    def check_degradation(self) -> str:
        """Check if execution should be degraded. Returns mode: 'normal', 'reduce_only', 'paused'."""
        now = time.time()

        # WebSocket freshness check. Prefer the feed's REAL last-message age over
        # the _ws_last_seen shadow clock. The shadow is refreshed only once per
        # engine scan tick (engine._tick), and in a calm market smart-scan
        # stretches that tick to ~90s (scan_interval × 1.5) — longer than the
        # ws_disconnect_pause_sec (60s default) threshold. Reading the shadow
        # alone therefore falsely reports "disconnected" during the tail of every
        # quiet cycle even while the socket streams a tick every second, blocking
        # market orders spuriously (observed live: AAVE LONG "feed disconnected").
        # seconds_since_last_msg() is the authoritative price-staleness signal the
        # pause is meant to guard. Fail-safe: any error falls back to the shadow.
        ws_gap = now - self._ws_last_seen
        feed = self._ws_feed
        if feed is not None:
            try:
                age = feed.seconds_since_last_msg()
                if age is not None:
                    ws_gap = age
            except Exception:
                pass
        ws_pause = getattr(getattr(CONFIG, 'execution', None), 'ws_disconnect_pause_sec', 120)
        if ws_gap > ws_pause:
            if not self._degraded_mode:
                self._degraded_mode = True
                audit(system_log,
                      f"Graceful degradation: PAUSED (WS gap {ws_gap:.0f}s)",
                      action="degradation", result="PAUSED")
            return "paused"

        # API error accumulation
        api_degrade = getattr(getattr(CONFIG, 'execution', None), 'api_degrade_reduce_only', True)
        if self._api_error_count >= 5 and api_degrade:
            return "reduce_only"

        if self._degraded_mode:
            self._degraded_mode = False
            self._api_error_count = 0
            audit(system_log, "Graceful degradation: RESTORED",
                  action="degradation", result="RESTORED")

        return "normal"

    def record_ws_heartbeat(self) -> None:
        """Record WebSocket activity."""
        self._ws_last_seen = time.time()

    def record_api_error(self) -> None:
        """Record an API error for degradation tracking."""
        self._api_error_count += 1

    def record_api_success(self) -> None:
        """Reset API error count on success."""
        self._api_error_count = 0

    async def _get_exchange(self) -> ccxt.Exchange:
        """Get authenticated Bitget exchange instance."""
        if self._exchange is None:
            cfg = CONFIG.exchange
            # Per-user executors authenticate with the user's OWN linked keys
            # (decrypted from the credential store); the shared operator executor
            # uses CONFIG.exchange exactly as before. Non-credential settings
            # (trade_mode, sandbox, leverage, margin) remain operator-configured.
            if self._credentials:
                api_key = self._credentials.get("api_key") or ""
                api_secret = self._credentials.get("api_secret") or ""
                passphrase = self._credentials.get("passphrase") or ""
            else:
                api_key, api_secret, passphrase = cfg.api_key, cfg.api_secret, cfg.passphrase
            if not api_key or not api_secret:
                raise RuntimeError(
                    "BITGET_API_KEY and BITGET_API_SECRET required for live trading. "
                    "Set them in .env and restart."
                    if not self._credentials else
                    "This user has no linked Bitget credentials. Use /connect to "
                    "link an account before trading live."
                )
            is_futures = cfg.trade_mode == "futures"
            self._exchange = ccxt.bitget({
                "aiohttp_trust_env": True,  # honor HTTPS_PROXY/CA env (no-op without proxy)
                "apiKey": api_key,
                "secret": api_secret,
                "password": passphrase,
                "sandbox": cfg.sandbox,
                "timeout": 30000,
                "enableRateLimit": True,
                "options": {
                    "defaultType": "swap" if is_futures else "spot",
                    "uta": True,  # Support Bitget Unified Trading Account
                },
            })
            # Set leverage and margin mode for futures
            if is_futures:
                logger.info("Futures mode: leverage=%dx, margin=%s",
                            cfg.default_leverage, cfg.margin_mode)
        return self._exchange

    def _compute_target_leverage(self, symbol: str) -> int:
        """Single source of truth for dynamic leverage (deep-audit medium).

        Used by BOTH the set-leverage path (_ensure_leverage) and the sizing path
        (execute), which had diverged: the set path still scaled leverage UP
        ×1.4 in low vol while sizing was reduce-only, so the exchange leverage
        and the leverage used to size the order disagreed.

        Dynamic leverage only ever REDUCES from the configured default — never
        increases it — because up-scaling in low realized vol amplified losses on
        full-stop hits (the live drawdown). High vol de-leverages (protective);
        low/normal vol keeps the default. Fail-safe: any error → 1x (safest).
        Returns an int ≥ 1."""
        cfg = CONFIG.exchange
        default_lev = max(1, int(cfg.default_leverage))
        if not getattr(cfg, "dynamic_leverage_enabled", False):
            return default_lev
        try:
            sym_base = normalize_symbol(symbol)
            atr_pct = self._last_atr_pct.get(sym_base, 0.02)
            min_lev = int(getattr(cfg, "min_leverage", 1))
            lev = default_lev
            if atr_pct > 0.04:        # high vol (>4% ATR) → halve
                lev = max(min_lev, lev // 2)
            elif atr_pct > 0.03:      # elevated vol → ×0.7
                lev = max(min_lev, int(lev * 0.7))
            # low/normal vol: keep the default (never up-scale).
            lev = min(lev, default_lev)  # defensive cap: never exceed default
            lev = max(1, lev)
            audit(trade_log,
                  f"Dynamic leverage for {symbol}: {default_lev}x → {lev}x (ATR={atr_pct:.3%})",
                  action="dynamic_leverage", result="ADJUSTED")
            return lev
        except Exception as exc:
            logger.warning(
                "Dynamic leverage calc failed for %s — using 1x (safe default): %s",
                symbol, exc)
            audit(trade_log,
                  f"Dynamic leverage FAILED for {symbol}: using 1x safe default",
                  action="dynamic_leverage", result="EXCEPTION",
                  data={"error": str(exc)[:200]})
            self._record_warning("dynamic_leverage")
            return 1

    async def _ensure_leverage(self, symbol: str) -> None:
        """Set leverage and margin mode for a symbol (futures only).

        For Bitget UTA accounts, ccxt's set_margin_mode may silently succeed
        without actually changing the mode. We verify by fetching the account
        info and log a CRITICAL warning if the mode doesn't match config.
        """
        cfg = CONFIG.exchange
        if cfg.trade_mode != "futures":
            return
        exchange = await self._get_exchange()

        # ── Set margin mode (best-effort; the verification read below is the
        #    authority on whether it actually applied) ──
        try:
            await exchange.set_margin_mode(
                cfg.margin_mode, symbol,
                params={"productType": "USDT-FUTURES"})
        except Exception as exc:
            exc_str = str(exc)
            # Some errors are expected (e.g., already in the desired mode).
            if "already" in exc_str.lower() or "same" in exc_str.lower():
                pass  # already in the desired mode — nothing to do
            else:
                logger.warning("Margin mode set failed for %s: %s", symbol, exc)

        # ── Verify margin mode actually applied (the authority — the set call
        #    above is best-effort; this read is what gates the result) ──
        # Bitget UTA may silently ignore set_margin_mode
        try:
            raw_symbol = symbol.replace("/USDT", "USDT").replace(":USDT", "")
            resp = await exchange.privateMixGetV2MixAccountAccount(
                {"symbol": raw_symbol, "productType": "USDT-FUTURES"})
            data = resp.get("data", {})
            if isinstance(data, list) and data:
                data = data[0]
            if isinstance(data, dict):
                actual_margin = (data.get("marginMode") or "").lower()
                if actual_margin:
                    self._actual_margin_mode = actual_margin
                expected = cfg.margin_mode.lower()
                # Bitget uses "crossed" not "cross"
                expected_normalized = "crossed" if expected == "cross" else expected
                if actual_margin and actual_margin != expected_normalized:
                    # Try Bitget v2 endpoint to force margin mode
                    try:
                        await exchange.privateMixPostV2MixAccountSetMarginMode({
                            "symbol": raw_symbol,
                            "productType": "USDT-FUTURES",
                            "marginMode": expected_normalized,
                        })
                        audit(trade_log,
                              f"Margin mode forced via v2 API: {symbol} -> {expected_normalized}",
                              action="margin_mode_force", result="OK",
                              data={"symbol": symbol, "from": actual_margin,
                                    "to": expected_normalized})
                    except Exception as force_exc:
                        force_str = str(force_exc)
                        # If the position already exists with different margin mode,
                        # we can't change it — log critical warning
                        audit(trade_log,
                              f"MARGIN MODE MISMATCH: {symbol} is {actual_margin}, "
                              f"wanted {expected_normalized}. Cannot change with open position. "
                              f"Force attempt: {force_str}",
                              action="margin_mode_mismatch", result="CRITICAL",
                              data={"symbol": symbol, "actual": actual_margin,
                                    "expected": expected_normalized,
                                    "error": force_str})
                        logger.critical(
                            "MARGIN MODE MISMATCH for %s: actual=%s, config=%s — "
                            "CROSS margin exposes entire account balance to liquidation risk. "
                            "Change margin mode on Bitget web UI or close all positions first.",
                            symbol, actual_margin, expected_normalized)
        except Exception as verify_exc:
            err_str = str(verify_exc)
            if "40085" in err_str:
                # UTA account — v2 account endpoint not available
                # Try fetching position info to check margin mode
                try:
                    ccxt_sym = symbol if ":USDT" in symbol else f"{symbol}:USDT"
                    positions = await exchange.fetch_positions(
                        [ccxt_sym], params={"productType": "USDT-FUTURES"})
                    for p in positions:
                        info = p.get("info", {})
                        actual_margin = (info.get("marginMode") or p.get("marginMode") or "").lower()
                        if actual_margin:
                            self._actual_margin_mode = actual_margin
                            expected = cfg.margin_mode.lower()
                            expected_normalized = "crossed" if expected == "cross" else expected
                            if actual_margin != expected_normalized:
                                logger.critical(
                                    "MARGIN MODE MISMATCH (UTA) for %s: actual=%s, config=%s — "
                                    "CROSS margin exposes entire account to liquidation",
                                    symbol, actual_margin, expected_normalized)
                                audit(trade_log,
                                      f"MARGIN MODE MISMATCH (UTA): {symbol} is {actual_margin}, "
                                      f"config says {expected_normalized}",
                                      action="margin_mode_mismatch", result="CRITICAL",
                                      data={"symbol": symbol, "actual": actual_margin,
                                            "expected": expected_normalized})
                            break
                except Exception:
                    logger.debug("Could not verify margin mode for %s via positions", symbol)
            else:
                logger.debug("Margin mode verification failed for %s: %s", symbol, verify_exc)

        # ── Set leverage (dynamic scaling via the shared, reduce-only helper) ──
        _target_leverage = self._compute_target_leverage(symbol)

        try:
            await exchange.set_leverage(
                _target_leverage, symbol,
                params={"productType": "USDT-FUTURES"})
        except Exception as exc:
            logger.warning("Leverage set failed for %s (may use exchange default): %s", symbol, exc)

        # C2-04 FIX: Verify leverage was actually applied — retry once if mismatch
        try:
            lev_info = await exchange.fetch_leverage(symbol, params={"productType": "USDT-FUTURES"})
            actual_lev = None
            if isinstance(lev_info, dict):
                actual_lev = lev_info.get("longLeverage") or lev_info.get("leverage") or lev_info.get("long")
                if actual_lev is not None:
                    actual_lev = int(float(actual_lev))
            if actual_lev is not None and actual_lev != _target_leverage:
                # Retry: set both long and short leverage explicitly
                logger.warning(
                    "LEVERAGE MISMATCH for %s: wanted %dx, exchange reports %dx — retrying",
                    symbol, _target_leverage, actual_lev)
                try:
                    await exchange.set_leverage(
                        _target_leverage, symbol,
                        params={"productType": "USDT-FUTURES", "holdSide": "long"})
                    await exchange.set_leverage(
                        _target_leverage, symbol,
                        params={"productType": "USDT-FUTURES", "holdSide": "short"})
                except Exception:
                    pass
                # Re-verify after retry
                try:
                    lev_info2 = await exchange.fetch_leverage(symbol, params={"productType": "USDT-FUTURES"})
                    if isinstance(lev_info2, dict):
                        actual_lev2 = lev_info2.get("longLeverage") or lev_info2.get("leverage") or lev_info2.get("long")
                        if actual_lev2 is not None:
                            actual_lev2 = int(float(actual_lev2))
                        if actual_lev2 is not None and actual_lev2 != _target_leverage:
                            logger.critical(
                                "LEVERAGE STILL MISMATCHED for %s after retry: wanted %dx, exchange reports %dx — "
                                "ABORTING order to prevent incorrect risk exposure",
                                symbol, _target_leverage, actual_lev2)
                            raise RuntimeError(
                                f"Cannot set leverage to {_target_leverage}x for {symbol} "
                                f"(exchange stuck at {actual_lev2}x). Aborting order.")
                except RuntimeError:
                    raise  # propagate abort
                except Exception:
                    pass  # fetch_leverage failed — proceed with caution
        except RuntimeError:
            raise  # propagate leverage abort
        except Exception:
            logger.debug("Could not verify leverage for %s (fetch_leverage unavailable)", symbol)

        # Detect hold mode (one-way vs hedge) on first call
        if self._hedge_mode is None:
            await self._detect_hold_mode()

    async def _detect_hold_mode(self) -> None:
        """Detect Bitget account position hold mode (one-way vs hedge).

        One-way mode: tradeSide/posSide must NOT be sent.
        Hedge mode: tradeSide (v2) or posSide (v3/UTA) is required.

        Tries v2 API first (classic accounts), falls back to v3 settings
        endpoint for UTA accounts.
        """
        exchange = await self._get_exchange()

        # ── Attempt 1: v2 API (classic accounts) ──
        try:
            resp = await exchange.privateMixGetV2MixAccountAccount(
                {"symbol": CONFIG.exchange.hold_mode_probe_symbol, "productType": "USDT-FUTURES"})
            data = resp.get("data", {})
            if isinstance(data, list) and data:
                data = data[0]
            hold_mode = data.get("holdMode", "") if isinstance(data, dict) else ""
            self._hedge_mode = (hold_mode == "double_hold")
            self._is_uta = False
            logger.info("Bitget position mode (v2): %s (hedge=%s)", hold_mode, self._hedge_mode)
            return
        except Exception as exc:
            err_str = str(exc)
            if "40085" not in err_str:
                logger.debug("Hold mode detection failed: %s, defaulting to one-way", exc)
                self._hedge_mode = False
                return
            logger.info("UTA account detected (40085), trying v3 settings endpoint")
            self._is_uta = True

        # ── Attempt 2: v3 /api/v3/account/settings (UTA accounts) ──
        try:
            from bot.core.bitget_v3_client import BitgetV3Client
            # AUDIT FIX: offload blocking urlopen to thread to avoid
            # freezing the event loop (dashboard, WS feeds, Telegram).
            resp_data = await asyncio.to_thread(
                BitgetV3Client.from_config().request, "GET", "/api/v3/account/settings")

            if resp_data.get("code") == "00000":
                hold_mode = resp_data.get("data", {}).get("holdMode", "")
                self._hedge_mode = (hold_mode == "hedge_mode")
                logger.info("Bitget position mode (v3 settings): %s (hedge=%s)",
                            hold_mode, self._hedge_mode)
                return
        except Exception as exc2:
            logger.debug("v3 settings detection failed: %s", exc2)

        # Default to one-way (most common)
        self._hedge_mode = False
        logger.info("Hold mode detection exhausted, defaulting to one-way")

    async def close(self) -> None:
        """Clean up exchange connection."""
        if self._exchange:
            await self._exchange.close()
            self._exchange = None

    # ── Pre-flight checks ────────────────────────────────────────

    def _preflight_check(self, size_usd: float, symbol: str = "") -> Optional[str]:
        """Run micro-test safety checks. Returns error string or None."""
        # Cap position size
        if size_usd > MICRO_MAX_POSITION_USD:
            return (
                f"Position size ${size_usd:.2f} exceeds micro-test limit "
                f"${MICRO_MAX_POSITION_USD:.2f}"
            )

        # Check total exposure
        total_exposure = sum(
            p.cost_usd for p in self._positions.values()
            if p.status == "open"
        )
        if total_exposure + size_usd > MICRO_MAX_TOTAL_EXPOSURE:
            return (
                f"Total exposure ${total_exposure + size_usd:.2f} would exceed "
                f"micro-test limit ${MICRO_MAX_TOTAL_EXPOSURE:.2f}"
            )

        # GETCLAW: Capital buffer guard — keep minimum reserve after trade.
        # Deploying too much leaves no buffer for margin calls or new opportunities.
        # Warn (don't block) if remaining equity drops below 20% of limit.
        MIN_RESERVE_PCT = 20.0
        remaining = MICRO_MAX_TOTAL_EXPOSURE - total_exposure - size_usd
        reserve_needed = MICRO_MAX_TOTAL_EXPOSURE * (MIN_RESERVE_PCT / 100.0)
        if remaining < reserve_needed and remaining > 0:
            audit(trade_log,
                  f"Capital buffer warning: ${remaining:.2f} remaining after trade "
                  f"(reserve target: ${reserve_needed:.2f})",
                  action="capital_buffer", result="WARN",
                  data={"remaining": remaining, "reserve": reserve_needed,
                        "exposure": total_exposure, "new_size": size_usd})

        # Check open positions count
        open_count = sum(1 for p in self._positions.values() if p.status == "open")
        if open_count >= MICRO_MAX_OPEN_POSITIONS:
            return f"Already {open_count} open positions (max {MICRO_MAX_OPEN_POSITIONS})"

        # DUPLICATE SYMBOL GUARD: block opening a second position on the same symbol
        if symbol:
            norm = normalize_symbol(symbol)
            for p in self._positions.values():
                if p.status != "open":
                    continue
                p_norm = normalize_symbol(p.symbol)
                if p_norm == norm:
                    return (
                        f"Already have an open {p.direction} position on {p.symbol} "
                        f"(trade {p.trade_id}). Close it first or wait for SL/TP."
                    )

        return None

    # ── Order idempotency (UPGRADE: clientOid + timeout-safe recovery) ────
    @staticmethod
    def _client_oid(trade_id: str) -> str:
        """Build a deterministic, Bitget-safe clientOid for a trade idea.

        The same trade_id always maps to the same clientOid, so a retried or
        timed-out submission can never create a duplicate exchange order:
        Bitget rejects a second order carrying a clientOid it has already seen.
        Output is alphanumeric and <= 32 chars (well within Bitget's 64 limit).
        When the cleaned input exceeds 30 chars, we hash to avoid collisions
        from prefix-truncation.
        """
        safe = "".join(ch for ch in str(trade_id) if ch.isalnum())
        if not safe or len(safe) > 30:
            safe = hashlib.sha256(str(trade_id).encode()).hexdigest()[:30]
        return ("rc" + safe)[:32]

    @staticmethod
    def _validate_order_limits(
        market: Optional[dict], quantity: float, notional_usd: float
    ) -> Optional[str]:
        """Check an order against the exchange's min amount / min notional filters.

        Returns an error string if the order would be rejected by the venue, else
        None. Catching this locally turns a confusing exchange rejection into a
        clean, auditable BLOCK before any capital leaves the account.
        """
        if not market:
            return None
        limits = market.get("limits") or {}
        amt_min = (limits.get("amount") or {}).get("min")
        cost_min = (limits.get("cost") or {}).get("min")
        try:
            if amt_min is not None and quantity < float(amt_min):
                return (f"quantity {quantity} below exchange minimum "
                        f"{amt_min} {market.get('base', '')}")
        except (TypeError, ValueError):
            pass
        try:
            if cost_min is not None and notional_usd < float(cost_min):
                return (f"notional ${notional_usd:.4f} below exchange minimum "
                        f"${float(cost_min):.4f}")
        except (TypeError, ValueError):
            pass
        return None

    @staticmethod
    def _round_price_to_market(exchange: "ccxt.Exchange", symbol: str, price: float) -> Optional[str]:
        """Round a price onto the symbol's tick grid.

        ccxt's ``price_to_precision`` respects the rounding mode but can mis-parse
        Bitget's pricePlace/priceEndStep precision pair and emit a price the venue
        rejects with 45115 ("price should be a multiple of X"). So this applies
        the authoritative Bitget tick snap (``_bitget_tick_safety_net``) as the
        LAST step on top of ccxt — matching the entry-order path — so SL/TP and
        trailing-stop triggers land on the real grid too. Returns None if the
        price is unusable so the caller can fall back.
        """
        try:
            ccxt_str = exchange.price_to_precision(symbol, price)
        except Exception as exc:  # noqa: BLE001
            # Venue/market data unavailable — return None so the caller falls
            # back to its own heuristic (unchanged contract).
            logger.debug("price_to_precision failed for %s @ %s: %s", symbol, price, exc)
            return None
        # ccxt succeeded but can still emit an off-tick price (it mis-parses
        # Bitget's pricePlace/priceEndStep pair). Snap it onto the real grid.
        market = None
        try:
            market = exchange.market(symbol)
        except Exception:  # noqa: BLE001
            try:
                market = (getattr(exchange, "markets", {}) or {}).get(symbol)
            except Exception:  # noqa: BLE001
                market = None
        # ccxt's market() is synchronous and returns a dict; guard against any
        # non-dict (e.g. a coroutine from a mocked/misbehaving client) so the
        # tick snap never raises into the caller.
        if not isinstance(market, dict):
            market = None
        snapped = LiveExecutor._bitget_tick_safety_net(market, float(ccxt_str))
        if snapped and snapped > 0:
            # Clean numeric string — no sci-notation / float noise.
            s = f"{snapped:.10f}".rstrip("0").rstrip(".")
            if s:
                return s
        return cast(Optional[str], ccxt_str)

    @staticmethod
    def _bitget_tick_safety_net(market: Optional[dict], limit_price: float) -> float:
        """Double-check tick alignment using Bitget's own market info fields.

        Bitget contracts report pricePlace (decimal-place count) AND
        priceEndStep (last-digit step multiplier) as a PAIR — the real tick is
        priceEndStep * 10^-pricePlace (e.g. pricePlace=1, priceEndStep=1 -> 0.1;
        pricePlace=4, priceEndStep=5 -> 0.0005). ccxt's own market parser
        combines them the same way. A prior version of this safety net treated
        whichever field it checked first as EITHER "the tick itself" (if < 1)
        OR "a flat decimal-place count" (if >= 1), which only worked by
        coincidence for symbols where pricePlace and priceEndStep are
        numerically equal (e.g. BTC, both "1") — for any symbol where they
        differ, it silently produced the wrong tick (e.g. pricePlace=4,
        priceEndStep=5 was rounded to 5 decimal places instead of the nearest
        0.0005), which Bitget then rejects as "price should be a multiple of X".

        Falls back to a standalone ``priceTick`` field (an absolute tick, not a
        pair) if pricePlace/priceEndStep aren't both present. Returns
        ``limit_price`` unchanged if no usable tick info is found.
        """
        if not isinstance(market, dict):
            return limit_price
        info = market.get("info", {}) or {}
        price_place = info.get("pricePlace")
        price_end_step = info.get("priceEndStep")
        tick = None
        if price_place is not None and price_end_step is not None:
            try:
                tick = float(price_end_step) * (10 ** -int(float(price_place)))
            except (ValueError, TypeError):
                tick = None
        if tick is None:
            price_tick = info.get("priceTick")
            if price_tick is not None:
                try:
                    tick = float(price_tick)
                except (ValueError, TypeError):
                    tick = None
        if tick is None or tick <= 0:
            return limit_price
        try:
            rounded = round(limit_price / tick) * tick
            return round(rounded, 10)  # clean float artifacts
        except (ValueError, TypeError, ZeroDivisionError):
            return limit_price

    async def _find_order_by_client_oid(
        self, exchange: "ccxt.Exchange", symbol: str, coid: str
    ) -> tuple[Optional[dict], bool]:
        """Best-effort lookup of an order by its clientOid.

        Used after a network failure/timeout to determine whether an order
        actually landed on the exchange before deciding to treat it as failed.

        Returns (order, verified):
          order    — the matching order dict, or None if not found.
          verified — True only if at least one venue query succeeded, so a None
                     order can be trusted as "confirmed absent". False means
                     every query failed (e.g. outage) and absence is UNVERIFIED;
                     callers must then fail-closed (RC-AUD-006).
        """
        def _matches(o: dict) -> bool:
            if not isinstance(o, dict):
                return False
            if o.get("clientOrderId") == coid:
                return True
            info = o.get("info") or {}
            return isinstance(info, dict) and info.get("clientOid") == coid

        # 1) ccxt unified fetch by clientOrderId (params), if the venue supports it
        verified = False
        for fetcher in ("fetch_open_orders", "fetch_closed_orders"):
            fn = getattr(exchange, fetcher, None)
            if fn is None:
                continue
            try:
                orders = await fn(symbol)
                verified = True
                for o in orders or []:
                    if _matches(o):
                        return o, True
            except Exception as exc:  # noqa: BLE001 — best effort, never fatal
                logger.debug("clientOid lookup via %s failed: %s", fetcher, exc)
        return None, verified

    async def _create_order_idempotent(
        self,
        exchange: "ccxt.Exchange",
        *,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        coid: str,
        price: Optional[float] = None,
        params: Optional[dict] = None,
    ) -> dict:
        """Place an order with an idempotency key, recovering from timeouts.

        Flow:
          1. Inject clientOid into params (Bitget dedups on it).
          2. Try create_order normally.
          3. On ANY exception, query the exchange by clientOid. If the order
             actually landed, return it (so a timed-out-but-filled order is
             reconciled instead of lost — and never re-submitted). Only if the
             lookup confirms the order is absent do we re-raise.
        """
        params = dict(params or {})
        params.setdefault("clientOid", coid)       # Bitget raw param
        params.setdefault("clientOrderId", coid)   # ccxt unified alias
        try:
            return cast(dict, await exchange.create_order(
                symbol=symbol, type=type, side=side, amount=amount,
                price=price, params=params
            ))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "create_order raised for %s (coid=%s): %s — checking whether it landed",
                symbol, coid, exc,
            )
            audit(trade_log, f"Order submit error for {symbol}; reconciling by clientOid",
                  action="live_execute", result="SUBMIT_ERROR_RECONCILE",
                  data={"symbol": symbol, "coid": coid, "error": str(exc)[:200]})
            found, _coid_verified = await self._find_order_by_client_oid(exchange, symbol, coid)
            if found is not None:
                logger.warning("Recovered order for %s via clientOid %s — NOT resubmitting",
                               symbol, coid)
                audit(trade_log, f"Recovered order via clientOid for {symbol}",
                      action="live_execute", result="RECOVERED_BY_COID",
                      data={"symbol": symbol, "coid": coid,
                            "order_id": found.get("id", "unknown")})
                return found
            # Confirmed absent — safe to surface the failure to the caller.
            raise

    # ── Post-trade verification (GetClaw-style) ─────────────────────
    async def _verify_order_fill(
        self,
        exchange: "ccxt.Exchange",
        order_id: str,
        symbol: str,
        expected_qty: float,
        max_retries: int = 3,
        delay: float = 1.5,
    ) -> dict:
        """Post-check: query the order to confirm actual fill.

        Returns dict with:
          confirmed: bool — True if order is filled/closed with qty > 0
          fill_price: float — average fill price (0 if unconfirmed)
          fill_qty: float — confirmed filled quantity
          fees: float — exchange-reported fees
          status: str — order status from exchange
          failure_stage: str — empty if confirmed, else stage that failed
          raw: dict — raw order response from exchange
        """
        result = {
            "confirmed": False,
            "fill_price": 0.0,
            "fill_qty": 0.0,
            "fees": 0.0,
            "status": "unknown",
            "failure_stage": "",
            "raw": {},
        }
        for attempt in range(max_retries):
            try:
                fetched = await exchange.fetch_order(order_id, symbol)
                result["raw"] = fetched
                status = str(fetched.get("status", "")).lower()
                result["status"] = status
                filled = float(fetched.get("filled", 0) or 0)
                avg_price = float(fetched.get("average", 0) or 0)
                fee_info = fetched.get("fee") or {}
                fee_cost = float(fee_info.get("cost", 0) or 0) if isinstance(fee_info, dict) else 0

                if status in ("closed", "filled") and filled > 0:
                    result["confirmed"] = True
                    result["fill_price"] = avg_price if avg_price > 0 else float(fetched.get("price", 0) or 0)
                    result["fill_qty"] = filled
                    result["fees"] = abs(fee_cost)
                    logger.info("Order %s CONFIRMED: filled=%.6f @ %.4f, fees=%.4f",
                                order_id, filled, result["fill_price"], result["fees"])
                    return result

                if status in ("canceled", "cancelled", "expired", "rejected"):
                    result["failure_stage"] = "order_cancelled"
                    logger.warning("Order %s was %s", order_id, status)
                    return result

                # Still open/partial — retry after delay
                if attempt < max_retries - 1:
                    await asyncio.sleep(delay)

            except Exception as exc:
                logger.warning("Verify order %s attempt %d failed: %s", order_id, attempt + 1, exc)
                if attempt < max_retries - 1:
                    await asyncio.sleep(delay)

        # Exhausted retries — order submitted but not confirmed
        result["failure_stage"] = "post_check_unconfirmed"
        return result

    async def _verify_position_exists(
        self,
        exchange: "ccxt.Exchange",
        symbol: str,
        expected_direction: str,
    ) -> dict:
        """Post-check: verify a position exists on the exchange after opening.

        Returns dict with:
          confirmed: bool — True if position found with contracts > 0
          exchange_qty: float — actual quantity on exchange
          exchange_entry: float — exchange-reported entry price
          mark_price: float — current mark price
          unrealized_pnl: float — current unrealized PnL
          margin: float — margin used
          leverage: int — actual leverage set on exchange
        """
        result = {
            "confirmed": False,
            "exchange_qty": 0.0,
            "exchange_entry": 0.0,
            "mark_price": 0.0,
            "unrealized_pnl": 0.0,
            "margin": 0.0,
            "leverage": 0,
        }
        try:
            positions = await exchange.fetch_positions([symbol])
            for p in (positions or []):
                if not isinstance(p, dict):
                    continue
                p_symbol = p.get("symbol", "")
                contracts = float(p.get("contracts", 0) or 0)
                p_side = str(p.get("side", "")).lower()
                expected_side = "long" if expected_direction == "LONG" else "short"
                if p_symbol == symbol and contracts > 0 and p_side == expected_side:
                    result["confirmed"] = True
                    result["exchange_qty"] = contracts
                    result["exchange_entry"] = float(p.get("entryPrice", 0) or 0)
                    result["mark_price"] = float(p.get("markPrice", 0) or 0)
                    result["unrealized_pnl"] = float(p.get("unrealizedPnl", 0) or 0)
                    result["margin"] = float(p.get("initialMargin", 0) or p.get("collateral", 0) or 0)
                    result["leverage"] = int(float(p.get("leverage", 0) or 0))
                    logger.info("Position VERIFIED on exchange: %s %s qty=%.6f entry=%.4f",
                                expected_direction, symbol, contracts, result["exchange_entry"])
                    return result
        except Exception as exc:
            logger.warning("Position verification failed for %s: %s", symbol, exc)
        return result

    async def _verify_position_closed(
        self,
        exchange: "ccxt.Exchange",
        symbol: str,
        direction: str,
        close_order_id: str,
    ) -> dict:
        """Post-check: verify a position is fully closed after close order.

        Returns dict with:
          confirmed: bool — True if position is gone or contracts == 0
          fill_price: float — actual close fill price from order
          fill_qty: float — actual closed quantity
          fees: float — exchange-reported fees on close
          remaining_qty: float — if partial close, qty still open
          failure_stage: str — empty if confirmed
        """
        result = {
            "confirmed": False,
            "fill_price": 0.0,
            "fill_qty": 0.0,
            "fees": 0.0,
            "remaining_qty": 0.0,
            "failure_stage": "",
        }
        # Step 1: Verify the close order filled
        order_check = await self._verify_order_fill(
            exchange, close_order_id, symbol, expected_qty=0, max_retries=3, delay=1.5
        )
        result["fill_price"] = order_check["fill_price"]
        result["fill_qty"] = order_check["fill_qty"]
        result["fees"] = order_check["fees"]

        if not order_check["confirmed"]:
            result["failure_stage"] = order_check.get("failure_stage", "close_order_unconfirmed")
            return result

        # Step 2: Verify position is gone/reduced on exchange
        try:
            await asyncio.sleep(1.0)  # Brief delay for exchange settlement
            positions = await exchange.fetch_positions([symbol])
            expected_side = "long" if direction == "LONG" else "short"
            for p in (positions or []):
                if not isinstance(p, dict):
                    continue
                p_side = str(p.get("side", "")).lower()
                contracts = float(p.get("contracts", 0) or 0)
                if p.get("symbol") == symbol and p_side == expected_side and contracts > 0:
                    result["remaining_qty"] = contracts
                    result["confirmed"] = False
                    result["failure_stage"] = "position_still_open"
                    logger.warning("Position still open after close: %s %s remaining=%.6f",
                                   direction, symbol, contracts)
                    return result
            # Position not found — fully closed
            result["confirmed"] = True
            logger.info("Position CLOSE VERIFIED: %s %s — no remaining position on exchange",
                        direction, symbol)
        except Exception as exc:
            # Close order confirmed but position check failed — trust the order fill
            logger.warning("Post-close position check failed for %s: %s — trusting order fill",
                           symbol, exc)
            result["confirmed"] = True  # Order was confirmed, position check is supplementary
        return result

    async def detect_untracked_positions(self) -> dict:
        """Detect exchange positions that RUNECLAW is NOT tracking locally.

        Complements ``reconcile_positions()`` (which handles the opposite
        direction — local-open / exchange-closed). This catches *orphans*: a
        live position on Bitget with no local record — the exact failure mode a
        timed-out-but-landed order could create. Read-only: it reports and
        audits, and never touches money state automatically.

        Returns {"untracked": [symbols], "errors": [...]}.
        """
        report: dict[str, Any] = {"untracked": [], "errors": []}
        if not CONFIG.is_live():
            report["errors"].append("not in live mode")
            return report
        try:
            exchange = await self._get_exchange()
            try:
                ex_positions = await exchange.fetch_positions(
                    params={"productType": "USDT-FUTURES"})
            except Exception as exc:  # noqa: BLE001
                report["errors"].append(f"fetch_positions failed: {exc}")
                return report

            tracked = {
                normalize_symbol(p.symbol)
                for p in self._positions.values()
                if p.status == "open"
            }
            for p in ex_positions or []:
                if not isinstance(p, dict):
                    continue
                try:
                    if float(p.get("contracts") or 0) == 0:
                        continue
                except (TypeError, ValueError):
                    continue
                raw_sym = (p.get("symbol") or "")
                sym = normalize_symbol(raw_sym)
                if sym and sym not in tracked:
                    report["untracked"].append(sym)
                    audit(trade_log,
                          f"ORPHAN: exchange position {sym} has no local record — manual review needed",
                          action="reconcile", result="UNTRACKED_ON_EXCHANGE",
                          data={"symbol": sym, "contracts": p.get("contracts")})
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(str(exc))
            logger.warning("detect_untracked_positions() failed: %s", exc)
        return report

    def dedupe_duplicate_positions(self) -> list[str]:
        """Merge local records that refer to the SAME real exchange position
        or order but ended up tracked twice.

        Real incident: adopt_exchange_positions()/adopt_exchange_limit_orders()
        falsely "adopted" a position/order the bot had already placed and was
        correctly tracking, creating a SECOND local record (trade_id prefixed
        "ORPHAN-" or "TI-adopted-") for the exact same symbol+direction —
        /openorders and /livepositions then disagreed about which trade_id was
        "the" order, since each happened to read a different record. Both
        records are never safe to leave standing: if either one's own
        stale/expiry logic tried to cancel "its" limit order, it could cancel
        the single real order shared by both, leaving the other record
        believing the order is still resting when it is not.

        This never touches the exchange — it only closes the redundant LOCAL
        record. Keeps the bot's own original record (it has real SL/TP levels;
        an adoption artifact only has safety-default 3%/6% levels) or, failing
        that, the earliest-opened record.

        Returns human-readable messages describing each merge.
        """
        messages: list[str] = []
        groups: dict[tuple[str, str], list[LivePosition]] = {}
        for p in self._positions.values():
            if p.status not in ("open", "pending_fill"):
                continue
            groups.setdefault((normalize_symbol(p.symbol), p.direction), []).append(p)

        def _is_adoption_artifact(p: LivePosition) -> bool:
            return p.trade_id.startswith("ORPHAN-") or p.trade_id.startswith("TI-adopted-")

        _epoch = datetime.fromtimestamp(0, tz=UTC)
        for (sym, direction), group in groups.items():
            if len(group) < 2:
                continue
            group.sort(key=lambda p: (_is_adoption_artifact(p), p.opened_at or _epoch))
            keeper, *dupes = group
            for dup in dupes:
                dup.status = "closed"
                dup.closed_at = datetime.now(UTC)
                dup.pnl_usd = 0.0
                dup.close_reason = "duplicate_merged"
                msg = (
                    f"Merged duplicate local record for {sym} {direction}: "
                    f"kept {keeper.trade_id}, dropped {dup.trade_id} "
                    f"(no exchange action taken)"
                )
                messages.append(msg)
                audit(trade_log, msg, action="dedupe_position", result="MERGED",
                      data={"symbol": sym, "direction": direction,
                            "kept": keeper.trade_id, "dropped": dup.trade_id})
        if messages:
            self._save_positions()
        return messages

    async def adopt_exchange_positions(self) -> list[str]:
        """Adopt any exchange positions not tracked locally into _positions.

        Called on startup after detect_untracked_positions(). This ensures
        every open position on the exchange has a corresponding LivePosition
        so /open_positions, /close, and performance all work correctly.

        Cooldown: positions on symbols recently closed (within 120s) are skipped
        to prevent re-adopting reverse positions created by hedge mode bugs.

        Returns list of adopted symbol names.
        """
        adopted: list[str] = []
        if not CONFIG.is_live():
            return adopted
        self.dedupe_duplicate_positions()

        # Build cooldown set from recently closed positions
        _now = time.time()
        _ADOPT_COOLDOWN = 120  # seconds
        recently_closed_symbols: set[str] = set()
        for p in self._closed_trades:
            closed_at = getattr(p, 'closed_at', None)
            if closed_at:
                if isinstance(closed_at, str):
                    try:
                        closed_at = datetime.fromisoformat(closed_at)
                    except (ValueError, TypeError):
                        continue
                if closed_at.tzinfo is None:
                    closed_at = closed_at.replace(tzinfo=UTC)
                age = _now - closed_at.timestamp()
                if age < _ADOPT_COOLDOWN:
                    recently_closed_symbols.add(normalize_symbol(p.symbol))
        try:
            exchange = await self._get_exchange()
            ex_positions = await exchange.fetch_positions(
                params={"productType": "USDT-FUTURES"})

            tracked = {
                (normalize_symbol(p.symbol), p.direction)
                for p in self._positions.values()
                # Include "closing": a position mid-close must NOT be re-adopted
                # as an orphan (TOCTOU with a concurrent close → duplicate record,
                # double-counted PnL, and stray safety SL/TP on a dead position).
                if p.status in ("open", "pending_fill", "closing")
            }
            # Also cover the window after close_position() takes the per-trade
            # close lock but before it flips the status to "closing".
            tracked |= {
                (normalize_symbol(p.symbol), p.direction)
                for p in self._positions.values()
                if p.trade_id in self._close_locks and self._close_locks[p.trade_id].locked()
            }

            for p in ex_positions or []:
                if not isinstance(p, dict):
                    continue
                try:
                    contracts = float(p.get("contracts") or 0)
                except (TypeError, ValueError):
                    continue
                if contracts <= 0:
                    continue

                raw_sym = p.get("symbol") or ""
                sym = normalize_symbol(raw_sym)
                side = (p.get("side") or "long").upper()
                if (sym, side) in tracked:
                    continue

                # Cooldown: skip symbols recently closed to prevent re-adoption
                # of reverse positions created by hedge mode bugs
                if sym in recently_closed_symbols:
                    logger.info("Skipping adoption of %s %s — recently closed (cooldown %ds)",
                                sym, side, _ADOPT_COOLDOWN)
                    continue

                # Grace window: skip symbols the bot itself just opened locally.
                # Real incident: a just-filled bot position ("AMD LONG") was
                # re-"adopted" as an orphan on the very next sync tick, sending a
                # confusing duplicate notification for a position that was
                # already fully tracked. The (sym, side) tuple match above is
                # exact-match only, so ANY transient disagreement between local
                # and exchange side/direction reporting in the first moments
                # after a fill falls through to adoption. Give fresh local
                # opens a grace period to settle before treating them as
                # possible orphans; a GENUINE orphan is unaffected since it
                # persists well past this window.
                _last_local_open = self._recent_local_opens.get(sym)
                if _last_local_open is not None and _now - _last_local_open < _RECENT_LOCAL_OPEN_GRACE:
                    logger.info("Skipping adoption of %s %s — opened locally %ds ago (grace %ds)",
                                sym, side, int(_now - _last_local_open), _RECENT_LOCAL_OPEN_GRACE)
                    continue

                # Adopt this position — always use raw exchange data (info.openPriceAvg)
                # as primary source, with ccxt's entryPrice as fallback.
                # RULE: exchange numbers are the only truth, no exceptions.
                info = p.get("info", {})
                entry_price = float(
                    info.get("openPriceAvg")
                    or p.get("entryPrice")
                    or info.get("averageOpenPrice")
                    or 0
                )
                margin = float(
                    info.get("margin")
                    or info.get("im")
                    or p.get("initialMargin")
                    or p.get("collateral")
                    or 0
                )
                leverage = int(float(
                    info.get("leverage")
                    or p.get("leverage")
                    or 1
                ))
                # Quantity: prefer raw exchange totalQty/available over ccxt contracts
                quantity = float(
                    info.get("totalQty")
                    or info.get("available")
                    or contracts
                )
                ts = p.get("timestamp")
                if ts:
                    opened_at = datetime.fromtimestamp(ts / 1000, tz=UTC)
                else:
                    opened_at = datetime.now(UTC)

                trade_id = f"TI-adopted-{raw_sym.replace('/', '-')}-{int(opened_at.timestamp())}"
                lp = LivePosition(
                    trade_id=trade_id,
                    symbol=raw_sym,
                    direction=side,
                    entry_price=entry_price,
                    quantity=quantity,
                    cost_usd=margin,
                    stop_loss=0,
                    take_profit=0,
                    leverage=leverage,
                    is_spot=False,
                    opened_at=opened_at,
                    status="open",
                    origin="adopted",
                )

                # Read SL/TP directly from v2 position data (exchange is source of truth)
                info = p.get("info", {})
                ex_sl = float(info.get("stopLoss") or 0)
                ex_tp = float(info.get("takeProfit") or 0)
                ex_sl_id = info.get("stopLossId") or ""
                ex_tp_id = info.get("takeProfitId") or ""
                if ex_sl > 0:
                    lp.stop_loss = ex_sl
                    if ex_sl_id:
                        lp.sl_order_id = ex_sl_id
                if ex_tp > 0:
                    lp.take_profit = ex_tp
                    if ex_tp_id:
                        lp.tp_order_id = ex_tp_id

                # Fallback: if position data didn't have SL/TP, try open orders
                if lp.stop_loss <= 0 or lp.take_profit <= 0:
                    try:
                        open_orders = await exchange.fetch_open_orders(raw_sym)
                        for o in (open_orders or []):
                            trigger = float(o.get("triggerPrice") or o.get("stopPrice") or 0)
                            if trigger <= 0:
                                continue
                            otype = (o.get("type") or "").lower()
                            if ("stop" in otype or "loss" in otype) and lp.stop_loss <= 0:
                                lp.stop_loss = trigger
                                lp.sl_order_id = o.get("id")
                            elif ("take" in otype or "profit" in otype) and lp.take_profit <= 0:
                                lp.take_profit = trigger
                                lp.tp_order_id = o.get("id")
                    except Exception as _sltp_adopt_exc:
                        logger.warning("SL/TP extraction failed during adoption of %s: %s",
                                       raw_sym, _sltp_adopt_exc)  # position adopted without SL/TP

                # If SL or TP missing, calculate safety defaults (3% SL, 6% TP)
                need_sl = lp.stop_loss <= 0 and entry_price > 0
                need_tp = lp.take_profit <= 0 and entry_price > 0
                if need_sl or need_tp:
                    default_sl_pct = 0.03
                    default_tp_pct = 0.06
                    if need_sl:
                        if side == "LONG":
                            lp.stop_loss = round(entry_price * (1 - default_sl_pct), 8)
                        else:
                            lp.stop_loss = round(entry_price * (1 + default_sl_pct), 8)
                    if need_tp:
                        if side == "LONG":
                            lp.take_profit = round(entry_price * (1 + default_tp_pct), 8)
                        else:
                            lp.take_profit = round(entry_price * (1 - default_tp_pct), 8)

                    # Place exchange-side SL/TP for safety.
                    # RC-AUD-022: the same class as RC-AUD-001 — when the safety
                    # STOP-LOSS for an adopted position cannot be placed, the
                    # position is otherwise adopted WITHOUT protection and WITHOUT
                    # any alert. Gate the "unprotected" condition on the SL id alone
                    # (not on both SL and TP), retry the placement once, and if the
                    # stop still cannot be placed emit a LOUD operator alert and
                    # record the unprotected state on the position. We do NOT
                    # auto-close adopted positions — they may be pre-existing /
                    # intentional — so this only alerts; it never places a closing
                    # order.
                    direction = Direction.LONG if side == "LONG" else Direction.SHORT
                    sl_id: Optional[str] = None
                    tp_id: Optional[str] = None
                    _place_exc: Optional[Exception] = None
                    try:
                        # Size the safety stop off the RECORDED position size
                        # (lp.quantity = totalQty/available preferred over ccxt
                        # `contracts`, per the same distrust applied at adoption
                        # above), so the stop protects the whole adopted position
                        # rather than the ccxt-parsed contract count. reduceOnly
                        # clamps the upper bound, so this can only ever protect
                        # more, never over-close.
                        sl_id, tp_id = await self._place_sl_tp(
                            exchange, raw_sym, direction, lp.quantity,
                            lp.stop_loss, lp.take_profit,
                        )
                    except Exception as exc:
                        _place_exc = exc
                        logger.warning(
                            "ADOPTED position: safety SL/TP placement raised for %s: %s",
                            raw_sym, exc)

                    # Retry the SL once if it did not land (transient venue errors).
                    if sl_id is None:
                        try:
                            retry_sl, retry_tp = await self._place_sl_tp(
                                exchange, raw_sym, direction, lp.quantity,
                                lp.stop_loss, lp.take_profit,
                            )
                            sl_id = retry_sl
                            if tp_id is None:
                                tp_id = retry_tp
                        except Exception as exc:
                            _place_exc = exc
                            logger.warning(
                                "ADOPTED position: safety SL retry raised for %s: %s",
                                raw_sym, exc)

                    if sl_id:
                        lp.sl_order_id = sl_id
                    if tp_id:
                        lp.tp_order_id = tp_id

                    if sl_id is None:
                        # UNPROTECTED adopted position — alert loudly, do NOT close.
                        setattr(lp, "unprotected", True)  # runtime marker (not persisted schema)
                        # _place_sl_tp swallows venue errors and returns (None, None),
                        # so _place_exc is usually None — fall back to the recorded
                        # per-symbol rejection so the alert names the real reason.
                        _reason = (str(_place_exc) if _place_exc is not None
                                   else self._last_sltp_reason(sym))
                        _err_suffix = f": {_reason}" if _reason else ""
                        logger.critical(
                            "UNPROTECTED ADOPTED POSITION (%s %s): safety stop-loss "
                            "could not be placed%s — position adopted with NO stop. "
                            "Manual intervention required: place a stop on Bitget.",
                            sym, side, _err_suffix)
                        audit(trade_log,
                              f"ADOPTED position UNPROTECTED: stop-loss could not be placed "
                              f"for {raw_sym} (SL=${lp.stop_loss:.4f}) — manual intervention required",
                              action="adopt_safety_sltp", result="UNPROTECTED",
                              data={"trade_id": trade_id, "symbol": raw_sym,
                                    "stop_loss": lp.stop_loss, "take_profit": lp.take_profit,
                                    "tp_order_id": tp_id,
                                    "error": (str(_place_exc)[:200] if _place_exc is not None else None)})
                        self._record_warning("adopt_unprotected")
                    else:
                        audit(trade_log,
                              f"ADOPTED position safety SL/TP placed: {raw_sym} SL=${lp.stop_loss:.4f} TP=${lp.take_profit:.4f}",
                              action="adopt_safety_sltp", result="OK")

                self._positions[trade_id] = lp
                adopted.append(sym)
                audit(trade_log,
                      f"ADOPTED exchange position: {sym} {side} entry={entry_price} qty={lp.quantity} lev={leverage}x",
                      action="adopt_position", result="OK",
                      data={"trade_id": trade_id, "symbol": raw_sym,
                            "entry_price": entry_price, "quantity": lp.quantity,
                            "contracts": contracts})

            if adopted:
                self._save_positions()

        except Exception as exc:
            logger.warning("adopt_exchange_positions() failed: %s", exc)
        return adopted

    async def adopt_exchange_limit_orders(self) -> list[str]:
        """Adopt orphaned limit orders from exchange that aren't tracked locally.

        On restart, the bot may lose track of limit orders that were placed
        but not saved to live_positions.json. This method detects those
        orphaned limit orders and creates local pending_fill records so
        the status card, /positions, and expiry logic all work correctly.

        Uses real exchange data only — leverage from the exchange's clientOid
        mapping or the bot's own config (since Bitget UTA has no GET leverage
        API for unfilled orders). Margin mode comes from the order's own
        marginMode field.

        Returns list of adopted symbol names.
        """
        adopted: list[str] = []
        reclaimed_any = False  # True once a bot-placed order is quietly reclaimed
        if not CONFIG.is_live():
            return adopted
        try:
            exchange = await self._get_exchange()
            ex_orders = await exchange.fetch_open_orders(
                params={"productType": "USDT-FUTURES"})

            # Only consider limit orders (not SL/TP trigger orders)
            limit_orders = [
                o for o in (ex_orders or [])
                if isinstance(o, dict) and (o.get("type") or "").lower() == "limit"
            ]

            # Build set of exchange order IDs we already track
            tracked_order_ids: set[str] = set()
            # Also track symbol+direction+price combos to avoid duplicates
            tracked_combos: set[tuple[str, str, float]] = set()
            # Map clientOid prefix to trade_id for matching
            tracked_trade_ids: set[str] = set()
            for p in self._positions.values():
                if p.limit_order_id:
                    tracked_order_ids.add(p.limit_order_id)
                if p.status in ("open", "pending_fill"):
                    tracked_combos.add((
                        normalize_symbol(p.symbol),
                        p.direction,
                        round(p.entry_price, 4),
                    ))
                    tracked_trade_ids.add(p.trade_id)

            for o in limit_orders:
                oid = o.get("id", "")
                if not oid or oid in tracked_order_ids:
                    continue

                # Check clientOid — the bot prefixes with "rc" + trade_id
                # e.g. clientOid="rcTIf6798581" → trade_id="TI-f6798581"
                raw_info = o.get("info", {})
                client_oid = raw_info.get("clientOid", "") or o.get("clientOrderId", "")
                # Ownership: an "rc"-prefixed clientOid means THE BOT placed this
                # order. That fact is age- and local-state-independent — it holds
                # even after a restart that lost the local record, and even when
                # the order-id echo drifts. A bot-placed order must never be
                # surfaced as an EXTERNAL "opened in a previous session — SL/TP
                # may not be set" orphan (the reported false alarm). We still
                # adopt it so it's tracked, but under its real trade_id and
                # WITHOUT the external-orphan notification.
                own_order = client_oid.startswith("rc")
                own_tid = ""
                if own_order:
                    # Reconstruct trade_id: "rcTIf6798581" → "TI-f6798581"
                    own_tid = client_oid[2:]  # strip "rc"
                    # Insert hyphen after "TI" if missing: "TIf6798581" → "TI-f6798581"
                    if own_tid.startswith("TI") and not own_tid.startswith("TI-"):
                        own_tid = "TI-" + own_tid[2:]
                    if own_tid in tracked_trade_ids:
                        # Already tracked — just link the order ID
                        for p in self._positions.values():
                            if p.trade_id == own_tid and not p.limit_order_id:
                                p.limit_order_id = oid
                                self._save_positions()
                        continue

                # This is an untracked limit order — adopt it
                raw_sym = o.get("symbol") or ""
                side = (o.get("side") or "").upper()
                price = float(o.get("price") or 0)
                amount = float(o.get("amount") or o.get("remaining") or 0)
                created = o.get("datetime", "")

                if not raw_sym or price <= 0 or amount <= 0:
                    continue

                # Freshness guard (instance- and normalization-proof): if the
                # EXCHANGE itself reports this order was created within the grace
                # window, it is almost certainly one just placed this session —
                # by the bot or the operator — not a genuine orphan from a prior
                # session. The local-open grace above relies on this executor's
                # in-memory _recent_local_opens and an exact symbol/price/id
                # match; if any of those disagree (order-id format drift between
                # place and fetch, a second executor instance whose map is empty,
                # a stale in-memory _positions), a freshly-placed order falls
                # through and gets re-"adopted" seconds later with a false
                # "SL/TP may not be set" alarm — the exact incident this guards.
                # The order's own creation timestamp is immune to all of those.
                # A REAL orphan from a previous session is hours old and sails
                # past this window, so it is unaffected.
                order_ts = o.get("timestamp")
                if isinstance(order_ts, (int, float)) and order_ts > 0:
                    age_s = time.time() - (order_ts / 1000.0)
                    if 0 <= age_s < _RECENT_LOCAL_OPEN_GRACE:
                        logger.info(
                            "Skipping adoption of fresh limit order %s for %s %s "
                            "— exchange created it %ds ago (grace %ds); too new to "
                            "be a prior-session orphan",
                            oid, raw_sym, side, int(age_s), _RECENT_LOCAL_OPEN_GRACE)
                        continue

                direction = "LONG" if side == "BUY" else "SHORT"
                # A bot-placed order (own_order) is reclaimed under its real
                # trade_id and NOT reported as an external orphan. A genuinely
                # external order keeps the ORPHAN- prefix and is surfaced.
                trade_id = own_tid if own_order else f"ORPHAN-{oid[:8]}"

                # Skip if we already have a position for this trade_id
                if trade_id in self._positions:
                    continue

                # Skip if we already track a position with same symbol/direction/price
                # Prevents duplicate adoption of orders the bot placed
                combo = (normalize_symbol(raw_sym), direction, round(price, 4))
                if combo in tracked_combos:
                    logger.debug(
                        "Skipping duplicate limit order %s for %s %s @ %.4f — already tracked",
                        oid, raw_sym, direction, price)
                    continue
                # Tolerant fallback: the combo above is EXACT-match (price to
                # 4dp), so the maker-limit reprice feature (or any float drift
                # in the exchange echo) moves the price off the combo and the
                # bot's own order gets re-adopted as a duplicate (live incident:
                # UNI double-tracked → double fill notification → double close
                # booking). Same symbol + direction with price within 0.1% of a
                # tracked pending/open record is the bot's own order.
                _dup = False
                for (_ts, _td, _tp) in tracked_combos:
                    if (_ts == normalize_symbol(raw_sym) and _td == direction
                            and _tp > 0 and abs(_tp - price) / _tp <= 0.001):
                        _dup = True
                        break
                if _dup:
                    logger.info(
                        "Skipping near-duplicate limit order %s for %s %s @ %.4f "
                        "— tracked at a price within 0.1%% (repriced/echo drift)",
                        oid, raw_sym, direction, price)
                    continue

                # Grace window: skip symbols the bot itself just placed an order
                # for. Real incident: a bot-placed pending limit order ("XPT
                # SHORT") was re-"adopted" as an orphan moments after being
                # placed, sending a confusing duplicate notification for an
                # order that was already fully tracked. The combo match above
                # is exact-match only (symbol+direction+price to 4dp), so any
                # transient disagreement in how the exchange echoes the order
                # back falls through to adoption. Give fresh local opens a
                # grace period to settle; a GENUINE orphan is unaffected since
                # it persists well past this window.
                _sym_norm = normalize_symbol(raw_sym)
                _last_local_open = self._recent_local_opens.get(_sym_norm)
                if _last_local_open is not None and time.time() - _last_local_open < _RECENT_LOCAL_OPEN_GRACE:
                    logger.info(
                        "Skipping adoption of limit order %s for %s %s — opened locally %ds ago (grace %ds)",
                        oid, raw_sym, direction, int(time.time() - _last_local_open), _RECENT_LOCAL_OPEN_GRACE)
                    continue

                # ── Get real data from exchange order info ──
                # marginMode comes from the order itself
                margin_mode = raw_info.get("marginMode", "crossed")

                # Leverage: Bitget UTA has no GET leverage API for unfilled orders.
                # The order response doesn't include leverage.
                # Use the exchange config leverage as the source of truth —
                # this is what was set via set_leverage before the order was placed.
                # Audit F-5: ExchangeConfig has no `leverage` attribute (only
                # `default_leverage`); the old reference raised AttributeError,
                # which the broad except below swallowed at debug level — so
                # limit-order adoption silently never ran. Orphaned limit orders
                # were left untracked on the exchange.
                leverage = CONFIG.exchange.default_leverage or 10

                notional = price * amount
                margin = round(notional / leverage, 2)

                # Parse creation time from exchange data
                opened_at = datetime.now(UTC)
                if created:
                    try:
                        opened_at = datetime.fromisoformat(
                            created.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        pass

                pos = LivePosition(
                    trade_id=trade_id,
                    symbol=raw_sym,
                    direction=direction,
                    entry_price=price,
                    quantity=amount,
                    cost_usd=margin,
                    stop_loss=0,
                    take_profit=0,
                    leverage=leverage,
                    opened_at=opened_at,
                    status="pending_fill",
                    order_type="limit",
                    limit_order_id=oid,
                    origin="reclaimed" if own_order else "adopted",
                )
                self._positions[trade_id] = pos
                reclaimed_any = reclaimed_any or own_order
                # Only EXTERNAL orders drive the "Adopted Exchange Positions —
                # opened in a previous session, SL/TP may not be set" alarm. A
                # reclaimed bot-placed order is tracked silently — it is ours,
                # its SL/TP is handled by the normal post-fill path, and it must
                # not read as a stranger to the operator.
                if not own_order:
                    adopted.append(raw_sym)

                audit(trade_log,
                      (f"Reclaimed own limit order: {raw_sym} {direction} "
                       if own_order else
                       f"Adopted orphan limit order: {raw_sym} {direction} ")
                      + f"@ ${price:.4f} qty={amount} lev={leverage}x "
                      f"margin=${margin:.2f} marginMode={margin_mode} (order {oid})",
                      action=("reclaim_limit_order" if own_order else "adopt_limit_order"),
                      result="OK",
                      data={"trade_id": trade_id, "symbol": raw_sym,
                            "order_id": oid, "price": price, "amount": amount,
                            "leverage": leverage, "margin": margin,
                            "margin_mode": margin_mode,
                            "client_oid": client_oid, "own_order": own_order})

            if adopted or reclaimed_any:
                self._save_positions()

        except Exception as exc:
            logger.warning("adopt_exchange_limit_orders() failed: %s", exc)
        return adopted

    @staticmethod
    def _emergency_leverage(leverage_mult, is_futures: bool) -> int:
        """Leverage to record on an emergency (post-order-crash) position: the
        ACTUAL leverage used to size the order for futures, else 1 (spot).

        Recording the config default here would over-state leverage — dynamic
        leverage only ever reduces from the default — and therefore under-count
        the position's margin when pos.leverage later drives the
        cost_usd = notional / leverage recomputation. Returns at least 1."""
        if not is_futures:
            return 1
        try:
            return max(1, int(leverage_mult))
        except (TypeError, ValueError):
            return 1

    async def execute(self, idea: TradeIdea, size_usd: float,
                      order_type: str = "", atr_value: float = 0.0) -> str:
        """Execute a live trade on Bitget.

        Args:
            idea: The approved TradeIdea
            size_usd: Position size in USD (will be clamped to micro limits)
            order_type: "market" or "limit" (empty = use config default)
            atr_value: ATR at entry time (for trailing stop initialization)

        Returns:
            Human-readable result string
        """
        # C-04: Work on a copy of the idea to avoid mutating the caller's object
        import copy as _copy
        idea = _copy.copy(idea)
        # Resolve order type: explicit > config > default
        if self._persistence_broken:
            return "REFUSED: position persistence is broken — cannot open new trades until resolved"
        if not order_type:
            order_type = CONFIG.limit_orders.default_order_type if CONFIG.limit_orders.enabled else "market"
        order_type = order_type.lower()
        if order_type not in ("market", "limit"):
            order_type = "market"
        # Clamp to micro limit
        size_usd = min(size_usd, MICRO_MAX_POSITION_USD)

        # ── GETCLAW ORDER RULES: market hours + weekend adjustments ──
        asset_class = _classify_symbol(idea.asset)
        mkt_open, mkt_reason = is_market_open(asset_class)
        is_weekend = is_weekend_queued(asset_class)

        # Log market hours status for non-crypto assets
        if asset_class != "Crypto" and not mkt_open:
            audit(trade_log,
                  f"Market closed for {idea.asset} ({asset_class}): {mkt_reason}",
                  action="market_hours", result="QUEUED",
                  data={"asset": idea.asset, "class": asset_class, "reason": mkt_reason})
            # For market orders on closed markets, force to limit
            if order_type == "market" and asset_class not in ("Crypto", "Pre-IPO"):
                order_type = "limit"
                audit(trade_log,
                      f"Market order → limit: {idea.asset} market is closed",
                      action="order_type_override", result="LIMIT")

        # Weekend size reduction for metals/commodities (GetClaw: 30-40%)
        if is_weekend:
            old_size = size_usd
            size_usd = adjust_size_for_weekend(size_usd, asset_class, is_weekend)
            if size_usd != old_size:
                audit(trade_log,
                      f"Weekend size reduction: ${old_size:.2f} → ${size_usd:.2f} ({asset_class})",
                      action="weekend_size_adjust", result="REDUCED",
                      data={"old_size": old_size, "new_size": size_usd, "class": asset_class})

        # Weekend SL widening for gap-risk assets (GetClaw: widen 25-50%)
        if is_weekend:
            old_sl = idea.stop_loss
            new_sl = adjust_sl_for_gap_risk(
                idea.stop_loss, idea.entry_price,
                idea.direction.value, asset_class, is_weekend,
            )
            if new_sl != old_sl:
                idea.stop_loss = new_sl
                audit(trade_log,
                      f"Weekend SL widened: ${old_sl:.4f} → ${new_sl:.4f} ({asset_class})",
                      action="weekend_sl_widen", result="WIDENED",
                      data={"old_sl": old_sl, "new_sl": new_sl, "class": asset_class})

        # Check if TP/SL should be deferred until after fill (gap-risk limit orders)
        defer_tp_sl = should_defer_tp_sl(asset_class, is_weekend, order_type)

        # ── GETCLAW: Funding rate awareness ──────────────────────────
        # Negative funding = longs get paid (favorable for longs)
        # Positive funding = longs pay (unfavorable, factor into R:R)
        # 0% funding on metals/stocks = market likely closed
        try:
            exchange_pre = await self._get_exchange()
            funding_info = await exchange_pre.fetch_funding_rate(idea.asset)
            funding_rate = float(funding_info.get("fundingRate", 0) or 0)
            if funding_rate != 0:
                direction_favored = (
                    (idea.direction == Direction.LONG and funding_rate < 0) or
                    (idea.direction == Direction.SHORT and funding_rate > 0)
                )
                if not direction_favored and abs(funding_rate) > 0.001:
                    # Funding > 0.1% against us — log warning but don't block
                    audit(trade_log,
                          f"Funding rate {funding_rate*100:.3f}% unfavorable for "
                          f"{idea.direction.value} {idea.asset}",
                          action="funding_check", result="WARN",
                          data={"funding_rate": funding_rate, "direction": idea.direction.value})
        except Exception:
            pass  # Non-critical — don't block trade on funding fetch failure

        # ── GETCLAW: Funding settlement clock guard ──────────────────
        # Funding settles at 00:00 / 08:00 / 16:00 UTC.
        # Opening a position within 5 minutes BEFORE settlement means
        # you pay funding almost immediately. Warn and log.
        try:
            now_utc = datetime.now(UTC)
            minutes_in_day = now_utc.hour * 60 + now_utc.minute
            # Settlement times in minutes: 0, 480, 960
            settlement_times = [0, 480, 960]
            for st in settlement_times:
                mins_until = (st - minutes_in_day) % 1440
                if 0 < mins_until <= 5:  # within 5 minutes before settlement (exclude exact moment)
                    audit(trade_log,
                          f"Funding settlement in {mins_until}m — entry will incur "
                          f"immediate funding charge on {idea.asset}",
                          action="funding_clock", result="WARN",
                          data={"mins_until_settlement": mins_until,
                                "direction": idea.direction.value})
                    break
        except Exception:
            pass  # Non-critical timing check

        # Pre-flight
        preflight_err = self._preflight_check(size_usd, symbol=idea.asset)
        if preflight_err:
            audit(trade_log, f"Live execution blocked: {preflight_err}",
                  action="live_execute", result="BLOCKED",
                  data={"asset": idea.asset, "size_usd": size_usd})
            return f"BLOCKED: {preflight_err}"

        # AUDIT FIX: Re-assert live mode at execution time (not just at call time)
        # This prevents a race where /golive is revoked between confirmation and execution
        if not CONFIG.is_live():
            audit(trade_log, f"LIVE EXECUTION BLOCKED: is_live() returned False at execution time for {idea.asset}",
                  action="live_execute", result="BLOCKED_NOT_LIVE")
            return "Live execution blocked: live mode was deactivated before order placement."

        audit(trade_log, f"Live execution starting: {idea.direction.value} {idea.asset}",
              action="live_execute", result="STARTING",
              data={
                  "trade_id": idea.id, "asset": idea.asset,
                  "direction": idea.direction.value,
                  "size_usd": size_usd,
                  "entry": idea.entry_price,
                  "sl": idea.stop_loss, "tp": idea.take_profit,
              })

        order = None  # H-02 FIX: sentinel for emergency position path
        current_price = idea.entry_price  # N-01 FIX: sentinel for emergency path
        quantity = 0.0  # N-01 FIX: sentinel for emergency path
        # Sentinel so the emergency-position record can store the ACTUAL leverage
        # used (deep-audit medium). The dynamic block below overwrites this with
        # the real value before any order is placed; the default here only acts
        # as a fail-safe if a crash somehow precedes that (order would be None then).
        leverage_mult = CONFIG.exchange.default_leverage
        try:
            exchange = await self._get_exchange()
            is_futures = CONFIG.exchange.trade_mode == "futures"

            # Graceful degradation check
            # User-confirmed limit orders bypass WS degradation — the user
            # explicitly chose to trade and limit orders don't need real-time
            # WS data; the REST API is still functional.
            deg_mode = self.check_degradation()
            if deg_mode == "paused":
                is_user_limit = getattr(idea, 'order_type', '') == 'limit'
                if is_user_limit:
                    audit(trade_log,
                          "WS degraded but proceeding with user-confirmed limit order",
                          action="execute", result="DEGRADE_OVERRIDE")
                    # Reset degraded flag since we're about to hit the REST API
                    self._ws_last_seen = time.time()
                else:
                    audit(trade_log, "Order blocked: system degraded (paused)",
                          action="execute", result="DEGRADED")
                    return (
                        "EXECUTION BLOCKED: real-time price feed disconnected "
                        "(degraded mode, paused) — market orders are held so none "
                        "fires into a stale price. This auto-resumes once the feed "
                        "reconnects (usually within ~60s). To trade right now, "
                        "resend as a LIMIT order — limit orders don't need the live "
                        "feed and are not blocked."
                    )
            if deg_mode == "reduce_only":
                # Only allow closing positions, not opening new ones
                audit(trade_log, "Order blocked: reduce-only mode",
                      action="execute", result="REDUCE_ONLY")
                return "EXECUTION BLOCKED: system is in reduce-only mode — too many API errors"

            # UPGRADE: deterministic idempotency key for this trade idea.
            # Reused for every order/cancel below so a timeout-retry can never
            # double-submit (Bitget dedups on clientOid).
            coid = self._client_oid(idea.id)

            # Check if futures market exists for this symbol
            # Some tokens (e.g., SNEK) are spot-only on Bitget
            symbol = idea.asset
            if is_futures:
                markets = await exchange.load_markets()
                # ccxt uses "SYMBOL/USDT:USDT" for swap markets
                # Don't double-append :USDT if already present
                if ":USDT" in symbol:
                    swap_symbol = symbol
                else:
                    swap_symbol = symbol.replace("/USDT", "/USDT:USDT")
                has_futures = swap_symbol in markets or any(
                    m.get("swap") and m.get("symbol") == swap_symbol
                    for m in markets.values()
                    if isinstance(m, dict)
                )
                if not has_futures:
                    # FUTURES ONLY MODE: block trade if no futures market exists
                    audit(trade_log,
                          f"BLOCKED: {symbol} has no futures/perpetual market on this exchange",
                          action="live_execute", result="BLOCKED_NO_FUTURES",
                          data={"asset": symbol, "direction": idea.direction.value})
                    return (f"EXECUTION FAILED: {symbol} has no futures market — "
                            f"only USDT-M perpetual futures are supported.")

            # Set leverage for this symbol (futures only)
            if is_futures:
                # AUDIT-FIX: Use swap symbol format for leverage API calls
                swap_sym = idea.asset if ":USDT" in idea.asset else f"{idea.asset}:USDT"
                await self._ensure_leverage(swap_sym)

            # Convert symbol to the perpetual/swap format for the futures order
            # path so the market lookup, price rounding, tick snap and
            # create_order all use the FUTURES market's real tick grid.
            # idea.asset is spot format ("HOME/USDT"); for sub-cent tokens the
            # spot tick (e.g. 1e-6) is FINER than the perp tick (1e-5), so a
            # spot-rounded price like 0.016815 is off the perp's 0.00001 grid
            # and Bitget rejects the order with 45115. ccxt re-applies
            # price_to_precision on the order's symbol, so the ORDER symbol
            # itself must be the perp form. The recorded position still uses
            # idea.asset (spot format) below, so reconciliation is unchanged.
            if is_futures:
                symbol = idea.asset if ":USDT" in idea.asset else f"{idea.asset}:USDT"
            else:
                symbol = idea.asset

            # Futures-only: always use the main swap exchange
            active_exchange = exchange

            # Fetch current price to calculate quantity
            try:
                ticker = await active_exchange.fetch_ticker(symbol)
            except Exception:
                # Spot exchange may need markets loaded first
                await active_exchange.load_markets()
                ticker = await active_exchange.fetch_ticker(symbol)
            _last_raw = ticker.get("last") if isinstance(ticker, dict) else None
            if _last_raw is None:
                return f"EXECUTION FAILED: exchange returned no price for {symbol}"
            current_price = float(_last_raw)

            # ── SAFEGUARD 1: Pre-trade price validation ──
            # Block trades where the market has already moved past the SL level.
            # This prevents opening a position that will be instantly stopped out.
            if idea.direction == Direction.LONG and current_price <= idea.stop_loss:
                audit(trade_log,
                      f"BLOCKED: {symbol} price ${current_price:.4f} already at/below SL ${idea.stop_loss:.4f}",
                      action="live_execute", result="BLOCKED_PRICE_PAST_SL",
                      data={"asset": symbol, "price": current_price,
                            "sl": idea.stop_loss, "direction": "LONG"})
                return (f"EXECUTION BLOCKED: {symbol} price ${current_price:.4f} is already "
                        f"at/below SL ${idea.stop_loss:.4f} — would be instantly stopped out.")
            elif idea.direction == Direction.SHORT and current_price >= idea.stop_loss:
                audit(trade_log,
                      f"BLOCKED: {symbol} price ${current_price:.4f} already at/above SL ${idea.stop_loss:.4f}",
                      action="live_execute", result="BLOCKED_PRICE_PAST_SL",
                      data={"asset": symbol, "price": current_price,
                            "sl": idea.stop_loss, "direction": "SHORT"})
                return (f"EXECUTION BLOCKED: {symbol} price ${current_price:.4f} is already "
                        f"at/above SL ${idea.stop_loss:.4f} — would be instantly stopped out.")

            # Calculate quantity
            # For futures with leverage: size_usd is the margin (collateral).
            # Notional exposure = margin * leverage, so qty = (size_usd * leverage) / price.
            # Dynamic leverage scaling — shared, reduce-only helper so the
            # leverage used to SIZE the order matches the leverage SET on the
            # exchange in _ensure_leverage (they had diverged: deep-audit medium).
            leverage_mult = self._compute_target_leverage(symbol)
            # Honor the risk engine's margin-risk-capped leverage. When SL distance
            # × leverage would exceed max_margin_risk_pct, RiskEngine.evaluate()
            # reduces leverage and writes idea._adjusted_leverage "for the executor"
            # — but it was never read, so orders sized at full leverage and blew
            # through the very cap the engine reported enforcing. Clamp reduce-only:
            # this can only LOWER the sized leverage, never raise it, so it is a
            # no-op whenever the risk gate left leverage unchanged.
            _risk_lev = getattr(idea, "_adjusted_leverage", None)
            if _risk_lev:
                try:
                    leverage_mult = min(int(leverage_mult), int(_risk_lev))
                except (TypeError, ValueError):
                    pass
            quantity = (size_usd * leverage_mult) / current_price

            # Determine side
            side = "buy" if idea.direction == Direction.LONG else "sell"

            # Load markets for precision rounding
            markets = await active_exchange.load_markets()
            market = markets.get(symbol)

            # ── Pre-flight exchange-minimum check (live incident: XPT) ──
            # A risk-sized position on a small account meeting a high-priced
            # asset (optionally at low leverage) can produce a quantity below
            # the venue's minimum amount step. ccxt's amount_to_precision then
            # RAISES ("amount ... must be greater than minimum amount precision
            # of X") and the operator saw a raw exchange error. Check the
            # market's minimums FIRST. Operator-requested: round the quantity UP
            # to the minimum when the overshoot is SMALL (within
            # exchange_min_roundup_max_mult of the approved quantity); otherwise
            # skip cleanly with an actionable message.
            if market:
                _limits = market.get("limits", {}) or {}
                _min_amt = (_limits.get("amount", {}) or {}).get("min")
                _prec_amt = (market.get("precision", {}) or {}).get("amount")
                # precision.amount is a STEP under ccxt TICK_SIZE mode (bitget:
                # e.g. 0.001) but DECIMAL PLACES on older builds (e.g. 3).
                # Interpret defensively: <=1 → already a step; integer >1 →
                # decimal places → 10^-n. Never let a misread inflate the floor.
                _step = min_amount_step(_prec_amt)
                _floor = max(float(_min_amt or 0), _step)
                _min_cost = float((_limits.get("cost", {}) or {}).get("min") or 0.0)
                _too_small = (_floor > 0 and quantity < _floor)
                _too_cheap = _min_cost > 0 and (quantity * current_price) < _min_cost
                if _too_small or _too_cheap:
                    _roundup_on = getattr(
                        CONFIG.exchange, "exchange_min_roundup_enabled", False) is True
                    _max_mult = float(getattr(
                        CONFIG.exchange, "exchange_min_roundup_max_mult", 1.5))
                    _resolved, _q_min, _mult = resolve_exchange_min_quantity(
                        quantity, _floor, _step, _min_cost, current_price,
                        _roundup_on, _max_mult)
                    _need_notional = max((_floor * current_price) if _floor > 0 else 0.0,
                                         _min_cost)
                    _need_margin = _need_notional / max(int(leverage_mult or 1), 1)
                    if _resolved is not None:
                        _old_qty = quantity
                        quantity = _resolved
                        audit(trade_log,
                              f"Rounded {symbol} UP to exchange minimum: "
                              f"qty {_old_qty:.8f} -> {quantity:.8f} "
                              f"({_mult:.2f}x approved, notional "
                              f"~${quantity * current_price:.2f})",
                              action="live_execute", result="ROUNDED_TO_MIN",
                              data={"asset": symbol, "old_qty": _old_qty,
                                    "new_qty": quantity, "mult": round(_mult, 3),
                                    "min_amount": _floor, "min_cost": _min_cost,
                                    "price": current_price})
                        # Falls through to amount_to_precision + the notional
                        # ceiling hard-block below (which still bounds the result).
                    else:
                        _why = ("round-up disabled" if not _roundup_on
                                else f"overshoot {_mult:.1f}x exceeds {_max_mult:.1f}x cap")
                        audit(trade_log,
                              f"BLOCKED: {symbol} size below exchange minimum "
                              f"(qty {quantity:.8f} < min {_q_min:.8f}; {_why})",
                              action="live_execute", result="BELOW_EXCHANGE_MIN",
                              data={"asset": symbol, "size_usd": round(size_usd, 4),
                                    "leverage": leverage_mult, "price": current_price,
                                    "qty": quantity, "min_amount": _floor,
                                    "min_cost": _min_cost, "mult": round(_mult, 3)})
                        return (f"BLOCKED: {symbol} position too small for the exchange — "
                                f"sized ${size_usd * leverage_mult:.2f} notional at "
                                f"{leverage_mult}x, but Bitget requires ≥ "
                                f"${_need_notional:.2f} notional (≈ ${_need_margin:.2f} "
                                f"margin at {leverage_mult}x). Skipped ({_why}) — not "
                                f"worth exceeding the risk-approved size.")
                try:
                    _rounded = active_exchange.amount_to_precision(symbol, quantity)
                except Exception as _prec_exc:
                    # Defense-in-depth: never surface a raw venue precision
                    # error — classify it as a clean skip.
                    audit(trade_log,
                          f"BLOCKED: {symbol} amount precision rejected: {_prec_exc}",
                          action="live_execute", result="BELOW_EXCHANGE_MIN",
                          data={"asset": symbol, "qty": quantity,
                                "error": str(_prec_exc)[:200]})
                    return (f"BLOCKED: {symbol} position too small for the "
                            f"exchange's precision rules ({quantity:.8f}). Skipped.")
                if _rounded is None:
                    return f"EXECUTION FAILED: exchange returned no precision data for {symbol}"
                quantity = float(_rounded)

            if quantity <= 0:
                audit(trade_log, f"Quantity too small after precision: {symbol} ${size_usd}",
                      action="live_execute", result="QUANTITY_TOO_SMALL",
                      data={"asset": symbol, "size_usd": size_usd, "price": current_price})
                return f"BLOCKED: quantity too small after precision rounding for {symbol}"

            # ── Audit F-3: notional vs margin boundary check ──
            # size_usd is MARGIN; the real exchange exposure is notional =
            # quantity * price = size_usd * leverage. The risk engine's %-caps are
            # margin-based, so they validate size_usd, not this notional. Make the
            # relationship explicit in the audit trail, and HARD-BLOCK only when
            # notional exceeds the design envelope (the configured margin cap times
            # the max allowed leverage, with a small rounding tolerance) — this
            # catches a sizing/leverage misconfiguration without touching the
            # legitimate per-trade range. Manual-margin trades intentionally exceed
            # the micro cap, so the ceiling scales with the actual margin used.
            _notional = quantity * current_price
            _margin_basis = max(size_usd, MICRO_MAX_POSITION_USD)
            _max_lev = max(int(getattr(CONFIG.exchange, "max_leverage", leverage_mult) or 1),
                           int(leverage_mult or 1))
            _notional_ceiling = _margin_basis * _max_lev * 1.05  # 5% rounding headroom
            audit(trade_log,
                  f"Notional check {symbol}: notional=${_notional:.2f} "
                  f"(margin=${size_usd:.2f} x {leverage_mult}x), ceiling=${_notional_ceiling:.2f}",
                  action="notional_boundary", result="OK",
                  data={"symbol": symbol, "notional": round(_notional, 2),
                        "margin": round(size_usd, 2), "leverage": leverage_mult,
                        "ceiling": round(_notional_ceiling, 2)})
            if _notional > _notional_ceiling:
                audit(trade_log,
                      f"Notional ${_notional:.2f} exceeds design ceiling "
                      f"${_notional_ceiling:.2f} for {symbol} — BLOCKED (audit F-3)",
                      action="notional_boundary", result="EXCEEDS_CEILING",
                      data={"symbol": symbol, "notional": round(_notional, 2),
                            "ceiling": round(_notional_ceiling, 2),
                            "margin": round(size_usd, 2), "leverage": leverage_mult})
                return (f"BLOCKED: order notional ${_notional:,.2f} exceeds the design "
                        f"ceiling ${_notional_ceiling:,.2f} (margin x max-leverage)")

            # UPGRADE: validate against the venue's min-amount / min-notional
            # filters so a sub-minimum order is BLOCKED cleanly here instead of
            # being rejected by Bitget after submission.
            limit_err = self._validate_order_limits(market, quantity, quantity * current_price)
            if limit_err:
                audit(trade_log, f"Order below exchange limits: {symbol} — {limit_err}",
                      action="live_execute", result="BELOW_EXCHANGE_MIN",
                      data={"asset": symbol, "size_usd": size_usd,
                            "quantity": quantity, "price": current_price})
                return f"BLOCKED: {limit_err}"

            # Place order (market or limit)
            use_limit = (order_type == "limit" and CONFIG.limit_orders.enabled)
            # Limit orders use the idea's entry_price; for spot cost-based buys,
            # limit is placed at entry_price and the exchange fills at that price or better.
            limit_price = idea.entry_price if use_limit else None

            # ── LIMIT ORDER PRICE VALIDATION ──
            # A limit order that's on the wrong side of the market fills instantly
            # as a taker (effectively a market order). Recalculate the limit price
            # using the CURRENT price with an offset to ensure it rests on the book.
            if use_limit and limit_price and current_price > 0:
                needs_recalc = False
                if side == "buy" and limit_price >= current_price:
                    # LONG limit buy above market = instant fill = market order
                    needs_recalc = True
                elif side == "sell" and limit_price <= current_price:
                    # SHORT limit sell below market = instant fill = market order
                    needs_recalc = True

                if needs_recalc and atr_value > 0:
                    # GETCLAW: confluence-based limit entry calculation
                    # Fetch recent 1H OHLCV for VWAP/EMA computation
                    ohlcv_data = None
                    try:
                        ohlcv_data = await active_exchange.fetch_ohlcv(
                            symbol, "1h", limit=50)
                        # Repaint guard: VWAP/EMA/session levels must come from
                        # CLOSED bars, same policy as every analysis path.
                        from bot.utils.candles import drop_forming_candle
                        ohlcv_data = drop_forming_candle(ohlcv_data, "1h")
                    except Exception as ohlcv_exc:
                        logger.debug("Could not fetch OHLCV for limit calc: %s", ohlcv_exc)

                    entry_result = calculate_entry(
                        current_price=current_price,
                        direction=idea.direction.value,
                        atr_value=atr_value,
                        ohlcv=ohlcv_data,
                    )
                    limit_price = entry_result.limit_price

                    # Apply entry tier size adjustment
                    if entry_result.tier == "D":
                        # Tier D = no confluence — downgrade to market order
                        use_limit = False
                        limit_price = None
                        audit(trade_log,
                              f"Limit downgraded to market: Tier D (no confluence) for {symbol}",
                              action="limit_tier_d", result="MARKET_FALLBACK",
                              data={"symbol": symbol, "tier": "D"})
                    elif entry_result.size_multiplier < 1.0:
                        # Tier C = marginal confluence — reduce size
                        old_sz = size_usd
                        size_usd = round(size_usd * entry_result.size_multiplier, 2)
                        audit(trade_log,
                              f"Tier C size reduced: ${old_sz:,.2f} → ${size_usd:,.2f} "
                              f"(×{entry_result.size_multiplier:.2f}) for {symbol}",
                              action="limit_tier_c", result="SIZE_REDUCED",
                              data={"symbol": symbol, "old_size": old_sz,
                                    "new_size": size_usd,
                                    "multiplier": entry_result.size_multiplier})
                        # Recalculate quantity with new size
                        quantity = (size_usd * leverage_mult) / current_price
                        if market:
                            _re_rounded = active_exchange.amount_to_precision(symbol, quantity)
                            if _re_rounded:
                                quantity = float(_re_rounded)

                    # ── Keep SL/TP geometry attached to the RECALCULATED entry
                    # and gate the structure SL (see recalc_sl_tp_for_shifted_entry) ──
                    if limit_price:
                        old_sl, old_tp = idea.stop_loss, idea.take_profit
                        new_sl, new_tp, _shifted, _nat_outcome = recalc_sl_tp_for_shifted_entry(
                            entry_price=idea.entry_price,
                            stop_loss=idea.stop_loss,
                            take_profit=idea.take_profit,
                            limit_price=limit_price,
                            natural_sl=entry_result.natural_sl,
                            side=side,
                        )
                        idea.stop_loss = new_sl
                        idea.take_profit = new_tp
                        if _shifted or _nat_outcome:
                            audit(trade_log,
                                  f"Limit-recalc SL/TP: SL ${old_sl:,.4f} → ${new_sl:,.4f}, "
                                  f"TP ${old_tp:,.4f} → ${new_tp:,.4f} "
                                  f"(shifted={_shifted}, natural_sl={_nat_outcome or 'n/a'})",
                                  action="limit_recalc_sltp",
                                  result=_nat_outcome.upper() if _nat_outcome else "SHIFTED",
                                  data={"old_sl": old_sl, "new_sl": new_sl,
                                        "old_tp": old_tp, "new_tp": new_tp,
                                        "limit_price": limit_price,
                                        "natural_sl": entry_result.natural_sl,
                                        "natural_outcome": _nat_outcome,
                                        "shifted": _shifted})

                    if limit_price:
                        audit(trade_log,
                              f"Confluence entry: {entry_result.explanation}",
                              action="limit_recalc_exec", result="RECALCULATED",
                              data={"old_limit": idea.entry_price, "new_limit": limit_price,
                                    "market_price": current_price, "atr": atr_value,
                                    "tier": entry_result.tier,
                                    "confluence": entry_result.confluence_count,
                                    "levels": entry_result.levels_used})
                elif needs_recalc and atr_value <= 0:
                    # No ATR available — fall back to market order
                    use_limit = False
                    limit_price = None
                    audit(trade_log,
                          "Limit order downgraded to market: no ATR for offset calculation",
                          action="limit_downgrade", result="MARKET_FALLBACK",
                          data={"symbol": symbol})

            if use_limit and limit_price:
                # Round limit price to exchange tick grid
                _prec_price = None
                if market:
                    try:
                        _prec_price = active_exchange.price_to_precision(symbol, limit_price)
                    except Exception:
                        pass
                if _prec_price is not None:
                    limit_price = float(_prec_price)
                else:
                    # Fallback: round to tick size from market info, or safe default
                    tick_size = None
                    if market:
                        tick_size = (market.get("precision", {}).get("price")
                                     or market.get("info", {}).get("pricePlace"))
                    if tick_size is not None:
                        try:
                            ts = float(tick_size)
                            if ts >= 1:
                                # tick_size is decimal places count
                                limit_price = round(limit_price, int(ts))
                            else:
                                # tick_size is actual step (e.g. 0.001)
                                limit_price = round(limit_price / ts) * ts
                        except (ValueError, TypeError) as _tick_exc:
                            logger.warning("Tick size rounding failed for %s: %s", symbol, _tick_exc)
                            self._record_warning("tick_size_rounding")
                    # Ultimate fallback: round based on price magnitude
                    if _prec_price is None:
                        if limit_price >= 10000:
                            limit_price = round(limit_price, 1)
                        elif limit_price >= 1000:
                            limit_price = round(limit_price, 2)
                        elif limit_price >= 1:
                            limit_price = round(limit_price, 3)
                        elif limit_price >= 0.01:
                            limit_price = round(limit_price, 4)
                        else:
                            limit_price = round(limit_price, 6)
                    logger.debug("Limit price fallback rounding: %s -> %s", _prec_price, limit_price)

                # Safety net: double-check tick alignment via market info fields
                # (see _bitget_tick_safety_net for why this needs pricePlace and
                # priceEndStep combined, not treated as alternatives).
                limit_price = self._bitget_tick_safety_net(market, limit_price)

            if is_futures:
                # Futures: use USDT-FUTURES product type
                # tradeSide only required in hedge (double_hold) mode
                leverage = leverage_mult  # Use dynamically-adjusted leverage

                # Pre-check: verify balance is accessible
                # UTA accounts pool all margin — try swap first, fall back to default
                try:
                    bal_free = 0.0
                    try:
                        fut_bal = await exchange.fetch_balance({"type": "swap"})
                        fut_usdt = fut_bal.get("USDT", {})
                        bal_free = float(fut_usdt.get("free", 0) if isinstance(fut_usdt, dict) else 0)
                    except Exception:
                        # UTA mode: fetch_balance without type returns unified balance
                        uni_bal = await exchange.fetch_balance()
                        uni_usdt = uni_bal.get("USDT", {})
                        bal_free = float(uni_usdt.get("free", 0) if isinstance(uni_usdt, dict) else 0)
                    logger.info("Balance pre-check: free=%.2f USDT for %s", bal_free, symbol)
                    if bal_free < size_usd:
                        audit(trade_log,
                              f"Low balance warning: ${bal_free:.2f} available, need ~${size_usd:.2f} margin for {symbol}",
                              action="live_execute", result="BALANCE_WARN",
                              data={"balance_free": bal_free, "margin_needed": size_usd})
                except Exception as exc:
                    logger.debug("Balance pre-check failed: %s", exc)

                futures_params = {
                    "productType": "USDT-FUTURES",
                    "marginMode": CONFIG.exchange.margin_mode,
                    "leverage": str(leverage),
                }
                if self._hedge_mode:
                    futures_params["tradeSide"] = "open"
                else:
                    # Even in one-way mode, explicitly set tradeSide for safety
                    futures_params["tradeSide"] = "open"

                # NOTE: v3 UTA Place Order supports inline takeProfit/stopLoss
                # params which would place SL/TP atomically with the position.
                # However, the response doesn't return SL/TP order IDs, and
                # _place_sl_tp_v3 already cancels existing plans before placing
                # new ones, so inline SL/TP would just get replaced. Keeping
                # the two-step flow (entry + separate SL/TP) for now since it
                # returns usable order IDs for reconciliation tracking.

                otype = "limit" if use_limit else "market"
                # TIME IN FORCE — asset-class aware:
                # GETCLAW: metals/stocks need GTC (session queue for overnight).
                # Crypto gets POST_ONLY for maker-only fee savings.
                if use_limit:
                    asset_class = _classify_symbol(symbol)
                    if asset_class in ("Metal", "Commodity", "Stock", "Pre-IPO"):
                        # GTC: stays live through session close/reopen
                        futures_params["timeInForce"] = "GTC"
                    elif CONFIG.limit_orders.post_only:
                        # POST_ONLY: maker-only, rejects if would fill as taker
                        futures_params["timeInForce"] = "post_only"

                create_kwargs: dict[str, Any] = {
                    "symbol": symbol, "type": otype, "side": side,
                    "amount": quantity, "coid": coid, "params": futures_params,
                }
                if use_limit and limit_price:
                    # FINAL authoritative re-validation, right at the point of
                    # submission. Multiple upstream paths can produce
                    # limit_price (confluence recalc, the precision/tick-size
                    # rounding chain, the bitget tick safety net) — rather than
                    # trust whichever one ran last, re-run ccxt's own
                    # price_to_precision one more time here so whatever gets
                    # submitted is unconditionally what the exchange's own
                    # market data says is valid. Logged at INFO so a repeat of
                    # error 45115 ("price should be a multiple of X") shows the
                    # exact value that was actually sent, not just the error.
                    _final_price = limit_price
                    if market:
                        try:
                            _final_str = active_exchange.price_to_precision(symbol, limit_price)
                            if _final_str is not None:
                                _final_price = float(_final_str)
                        except Exception as _fp_exc:
                            logger.warning(
                                "Final price_to_precision re-check failed for %s @ %s: %s "
                                "— submitting pre-rounded value",
                                symbol, limit_price, _fp_exc)
                    if _final_price != limit_price:
                        logger.warning(
                            "Final price re-validation changed %s limit price %.10g -> %.10g "
                            "before submission (upstream rounding didn't match market precision)",
                            symbol, limit_price, _final_price)
                    limit_price = _final_price
                    # AUTHORITATIVE final snap: price_to_precision above can
                    # UN-snap a tick-aligned price when ccxt mis-parses Bitget's
                    # pricePlace/priceEndStep pair — the exact 45115 rejection.
                    # Apply the real tick as the LAST word before submission.
                    limit_price = self._bitget_tick_safety_net(market, limit_price)
                    logger.info(
                        "Submitting %s limit order @ %s (pricePlace=%s priceEndStep=%s)",
                        symbol, limit_price,
                        (market or {}).get("info", {}).get("pricePlace"),
                        (market or {}).get("info", {}).get("priceEndStep"))
                    # ccxt requires price as a top-level param for limit orders
                    create_kwargs["price"] = limit_price

                # Order splitting for large market positions.
                # Roadmap P0-3: tranching is NOT implemented — the old code logged
                # "SPLITTING" and then placed the FULL order as a single market
                # fill, so the audit trail claimed market-impact protection that
                # never happened. Until real tranche execution (fill aggregation +
                # weighted-average pricing) lands, BLOCK the oversized order rather
                # than silently take full market impact while pretending otherwise.
                _split_enabled = getattr(getattr(CONFIG, 'execution', None), 'order_split_enabled', False)
                _split_threshold = getattr(getattr(CONFIG, 'execution', None), 'order_split_threshold_usd', 50000)
                if (_split_enabled and otype == "market" and
                        size_usd > _split_threshold):
                    audit(trade_log,
                          f"Order ${size_usd:.2f} exceeds split threshold "
                          f"${_split_threshold:.2f} but tranching is not implemented "
                          f"— BLOCKING to avoid full market impact",
                          action="order_split", result="BLOCKED_NOT_IMPLEMENTED",
                          data={"symbol": symbol, "size_usd": round(size_usd, 2),
                                "threshold": _split_threshold})
                    return (
                        f"BLOCKED: order ${size_usd:,.2f} exceeds the split threshold "
                        f"${_split_threshold:,.2f} and order-splitting is not yet "
                        f"implemented — refusing to send it as a single market order. "
                        f"Lower the size or raise ORDER_SPLIT_THRESHOLD_USD."
                    )

                # Try to place the order — handle POST_ONLY rejection gracefully
                try:
                    order = await self._create_order_idempotent(exchange, **create_kwargs)
                except Exception as post_only_exc:
                    exc_str = str(post_only_exc).lower()
                    # Bitget rejects POST_ONLY orders that would cross the book
                    # with "post only order failed" or similar. Retry with wider offset.
                    if use_limit and CONFIG.limit_orders.post_only and (
                        "post only" in exc_str or "post_only" in exc_str
                        or "would immediately" in exc_str
                    ):
                        # RC-AUD-005/006: the retry below regenerates the clientOid,
                        # which bypasses the venue's dedup. Before resubmitting, make
                        # sure the ORIGINAL order did not actually land. If it did,
                        # use it; if its status cannot be verified, fail-closed
                        # rather than risk a double-fill.
                        _orig, _orig_verified = await self._find_order_by_client_oid(
                            exchange, symbol, coid)
                        if _orig is not None:
                            logger.warning(
                                "POST_ONLY: original order for %s actually landed — "
                                "using it instead of resubmitting", symbol)
                            order = _orig
                        elif not _orig_verified:
                            audit(trade_log,
                                  f"POST_ONLY retry ABORTED for {symbol}: original order "
                                  f"status unverifiable — not resubmitting (double-fill guard)",
                                  action="post_only_retry", result="ABORT_UNVERIFIED",
                                  data={"symbol": symbol, "coid": coid})
                            raise
                        else:
                            # Audit F-10: the resubmit below uses a fresh clientOid
                            # (coid+"-r1"), so the venue will NOT dedup it against
                            # the original. The check above can miss an order that
                            # landed in the few ms before the lookup (fetch_open_orders
                            # index lag). Settle briefly and re-verify once more so a
                            # just-landed original is caught before we risk a second fill.
                            await asyncio.sleep(0.5)
                            _orig2, _orig2_verified = await self._find_order_by_client_oid(
                                exchange, symbol, coid)
                            if _orig2 is not None:
                                logger.warning(
                                    "POST_ONLY: original order for %s found on re-check — "
                                    "using it instead of resubmitting (audit F-10)", symbol)
                                order = _orig2
                            elif not _orig2_verified:
                                audit(trade_log,
                                      f"POST_ONLY retry ABORTED for {symbol}: original status "
                                      f"unverifiable on re-check — not resubmitting (double-fill guard)",
                                      action="post_only_retry", result="ABORT_UNVERIFIED_RECHECK",
                                      data={"symbol": symbol, "coid": coid})
                                raise
                            else:
                                audit(trade_log,
                                      f"POST_ONLY rejected for {symbol} @ ${limit_price:,.4f} — "
                                      f"widening offset and retrying",
                                      action="post_only_retry", result="WIDENING",
                                      data={"symbol": symbol, "rejected_price": limit_price})
                                # Double the offset and retry
                                wider_offset = 1.0 * atr_value if atr_value > 0 else current_price * 0.005
                                if side == "buy":
                                    limit_price = round(current_price - wider_offset, 8)
                                else:
                                    limit_price = round(current_price + wider_offset, 8)
                                _prec_price = active_exchange.price_to_precision(symbol, limit_price)
                                limit_price = float(_prec_price) if _prec_price is not None else limit_price
                                create_kwargs["price"] = limit_price
                                # Generate new coid for retry
                                retry_coid = coid + "-r1"
                                create_kwargs["coid"] = retry_coid
                                create_kwargs["params"]["clientOid"] = retry_coid
                                create_kwargs["params"]["clientOrderId"] = retry_coid
                                order = await self._create_order_idempotent(exchange, **create_kwargs)
                    else:
                        raise  # Not a POST_ONLY rejection — propagate
            else:
                # FUTURES-ONLY MODE: all non-futures order paths are removed.
                # This branch should never execute when trade_mode="futures".
                raise RuntimeError(
                    f"Unreachable: non-futures order path hit for {symbol} "
                    f"(side={side}, is_futures={is_futures}). "
                    f"Check CONFIG.exchange.trade_mode setting."
                )

            # ── CRITICAL SAFETY NET ──
            # Everything below runs AFTER the order was submitted to the exchange.
            # If parsing/tracking crashes, the position is LIVE on the exchange
            # but untracked locally — creating an orphan with no SL protection.
            # This except block ensures we always record a minimal position.

            # Handle limit orders that haven't filled yet
            order_status = order.get("status", "unknown")
            order_id = order.get("id", "unknown")
            filled_amount = float(order.get("filled", 0) or 0)

            # A limit order is pending if:
            # 1. The status says open/new/pending, OR
            # 2. It's a limit order with zero/negligible fill amount
            #    (Bitget's create_order response may not include a standard status)
            is_pending_limit = False
            if use_limit:
                if order_status in ("open", "new", "pending", "live", "init"):
                    is_pending_limit = True
                elif order_status not in ("closed", "filled") and filled_amount <= 0:
                    # Status is unknown/missing but no fill → treat as pending
                    is_pending_limit = True
                    logger.info("Limit order %s has status=%s, filled=%.6f — treating as pending",
                                order_id, order_status, filled_amount)

            if is_pending_limit:
                # Limit order placed but not yet filled — track as pending
                # CRITICAL FIX: use the ACTUAL limit_price sent to exchange,
                # not idea.entry_price (which may differ after confluence
                # recalculation). The exchange fills at limit_price, not
                # the original signal price.
                fill_price = limit_price if (use_limit and limit_price) else idea.entry_price
                filled_qty = quantity  # expected quantity
                raw_cost = fill_price * filled_qty
                if is_futures and leverage_mult > 1:
                    cost = raw_cost / leverage_mult
                else:
                    cost = raw_cost
            else:
                fill_price = float(order.get("average", 0) or order.get("price", 0) or current_price)
                filled_qty = float(order.get("filled") or 0)

                # GETCLAW: Enhanced fill verification — try fetch_my_trades first
                # (most accurate), then fetch_order as fallback.
                # fetch_my_trades returns actual execution data with fees and PnL.
                if not filled_qty or filled_qty <= 0:
                    # 1. Try fetch_my_trades (most reliable source)
                    try:
                        my_trades = await active_exchange.fetch_my_trades(symbol, limit=10)
                        # Match trades by order ID
                        order_trades = [t for t in my_trades if t.get("order") == order_id]
                        if order_trades:
                            filled_qty = sum(float(t.get("amount", 0) or 0) for t in order_trades)
                            # Weighted average fill price
                            total_cost = sum(
                                float(t.get("price", 0) or 0) * float(t.get("amount", 0) or 0)
                                for t in order_trades
                            )
                            if filled_qty > 0 and total_cost > 0:
                                fill_price = total_cost / filled_qty
                            audit(trade_log,
                                  f"Fill verified via trades: {symbol} qty={filled_qty:.6f} @ ${fill_price:,.4f}",
                                  action="fill_verify", result="TRADES",
                                  data={"order_id": order_id, "trade_count": len(order_trades)})
                    except Exception as trades_exc:
                        logger.debug("fetch_my_trades failed for %s: %s", symbol, trades_exc)

                    # 2. Fallback: fetch_order
                    if not filled_qty or filled_qty <= 0:
                        try:
                            confirmed = await active_exchange.fetch_order(order_id, symbol)
                            filled_qty = float(confirmed.get("filled", 0) or 0)
                            if confirmed.get("average"):
                                fill_price = float(confirmed["average"])
                        except Exception as fetch_exc:
                            logger.warning("Could not confirm fill for order %s: %s", order_id, fetch_exc)
                            self._record_warning("order_fill_confirm")

                # Final fallback: if still no fill data, use requested quantity
                # but flag it as estimated in the audit log.
                # RC-AUD-023a: the exchange-confirmed filled qty is already
                # preferred above (fetch_my_trades, then fetch_order) and only
                # this last-resort path books the REQUESTED quantity. On a partial
                # fill whose confirmation also fails, that over-states size, so the
                # SL/TP below are sized to an inflated quantity. This is bounded
                # because SL/TP go out reduceOnly (the venue clamps to the real
                # position, so it cannot reverse-open), and the close path's
                # RC-AUD-023b residual check reconciles any leftover. We keep this
                # fallback unchanged to avoid breaking the happy path (a full fill
                # of `quantity` whose confirmation merely lagged is correctly
                # booked as `quantity`).
                if not filled_qty or filled_qty <= 0:
                    filled_qty = quantity
                    audit(trade_log,
                          f"Fill quantity unconfirmed for {symbol} — using requested qty {quantity:.6f}",
                          action="fill_fallback", result="ESTIMATED",
                          data={"order_id": order_id})

                # cost_usd = margin (collateral), not notional. For futures, notional / leverage.
                raw_cost = float(order.get("cost", 0) or fill_price * filled_qty)
                if is_futures and leverage_mult > 1:
                    cost = raw_cost / leverage_mult  # store margin, not notional
                else:
                    cost = raw_cost

            live_order = LiveOrder(
                order_id=order_id,
                symbol=idea.asset,
                side=side,
                order_type=order_type,
                amount=filled_qty,
                price=fill_price,
                cost_usd=cost,
                status=order_status,
                client_oid=coid,
                raw=order,
            )
            self._order_history.append(live_order)

            # Track position
            leverage = leverage_mult if is_futures else 1
            spot_fallback = False  # Futures-only mode: no spot trading

            # Initialize trailing stop state — strategy-type-aware
            trailing_st = None
            pos_strategy = getattr(idea, 'strategy_type', 'swing')
            pos_signal_type = getattr(idea, 'signal_type', 'momentum_confluence')
            trailing_enabled = CONFIG.strategy_types.get_trailing_enabled(pos_strategy)
            if trailing_enabled and atr_value > 0:
                initial_risk = abs(fill_price - idea.stop_loss)
                trailing_st = make_trailing_state(
                    entry_price=fill_price,
                    direction=idea.direction.value,
                    initial_risk=initial_risk,
                    atr_value=atr_value,
                )

            position = LivePosition(
                trade_id=idea.id,
                symbol=idea.asset,
                direction=idea.direction.value,
                entry_price=fill_price,
                quantity=filled_qty,
                cost_usd=cost,
                stop_loss=idea.stop_loss,
                take_profit=idea.take_profit,
                leverage=leverage,
                is_spot=spot_fallback,
                trailing_state=trailing_st,
                order_type=order_type,
                limit_order_id=order_id if is_pending_limit else None,
                atr_at_entry=atr_value,
                strategy_type=pos_strategy,
                signal_type=pos_signal_type,
                status="pending_fill" if is_pending_limit else "open",
            )
            self._positions[idea.id] = position
            self._recent_local_opens[normalize_symbol(idea.asset)] = time.time()

            # F-07 FIX: persist after opening
            self._save_positions()
            # F-13 FIX: prune order history
            self._prune_order_history()

            if is_pending_limit:
                audit(trade_log, f"Limit order PLACED: {side} {idea.asset} @ ${fill_price:,.4f}",
                      action="live_execute", result="LIMIT_PLACED",
                      data={
                          "order_id": order_id, "trade_id": idea.id,
                          "side": side, "limit_price": fill_price,
                          "quantity": filled_qty, "cost_usd": cost,
                      })
                lev_info = f" | {leverage}x" if leverage > 1 else ""
                mode_label = "FUTURES" if is_futures else "SPOT"
                dir_icon = "🟢" if side == "buy" else "🔴"
                st_label = getattr(idea, 'strategy_type', 'swing').upper()
                return (
                    f"{dir_icon} <b>LIMIT ORDER {side.upper()} {idea.asset}</b> ({mode_label}{lev_info}) [{st_label}]\n"
                    f"{'─' * 16}\n"
                    f"- Limit: <code>${fill_price:,.4f}</code>\n"
                    f"- Current: <code>${current_price:,.4f}</code>\n"
                    f"- Qty: <code>{filled_qty:.6f}</code>\n"
                    f"- Cost: <code>${cost:.2f}</code>\n"
                    f"- SL: <code>${idea.stop_loss:,.4f}</code>\n"
                    f"- TP: <code>${idea.take_profit:,.4f}</code>\n"
                    f"- Order: <code>{order_id}</code>\n"
                    f"- Status: ⏳ PENDING FILL\n"
                    f"- Mode: 🔥 Live {mode_label}"
                )

            # ── POST-TRADE VERIFICATION (GetClaw-style) ────────────────
            # Step 1: Verify order fill via exchange query
            verify = await self._verify_order_fill(
                active_exchange, order_id, symbol, expected_qty=filled_qty,
                max_retries=3, delay=1.5,
            )
            confirmed = verify["confirmed"]

            # Use verified fill data when available (never guess)
            if confirmed:
                if verify["fill_price"] > 0:
                    fill_price = verify["fill_price"]
                if verify["fill_qty"] > 0:
                    filled_qty = verify["fill_qty"]
                    # Update position with actual fill
                    position.entry_price = fill_price
                    position.quantity = filled_qty
                exchange_fees = verify["fees"]
            else:
                exchange_fees = 0.0

            # Step 2: Verify position exists on exchange
            pos_verify = await self._verify_position_exists(
                active_exchange, symbol,
                "LONG" if idea.direction == Direction.LONG else "SHORT",
            )
            position_confirmed = pos_verify["confirmed"]

            # Update position with exchange-verified data
            if position_confirmed:
                if pos_verify["exchange_entry"] > 0:
                    position.entry_price = pos_verify["exchange_entry"]
                    fill_price = pos_verify["exchange_entry"]
                if pos_verify["exchange_qty"] > 0:
                    position.quantity = pos_verify["exchange_qty"]
                    filled_qty = pos_verify["exchange_qty"]
                if pos_verify["leverage"] > 0:
                    position.leverage = pos_verify["leverage"]
                    leverage = pos_verify["leverage"]

            # Recalculate cost with verified data
            raw_cost = fill_price * filled_qty
            if is_futures and leverage > 1:
                cost = raw_cost / leverage
            else:
                cost = raw_cost
            position.cost_usd = cost

            # Persist verified position data
            self._save_positions()

            # Record slippage (expected vs actual fill)
            try:
                if hasattr(self, '_slippage_tracker') and self._slippage_tracker:
                    self._slippage_tracker.record(
                        symbol=symbol,
                        expected_price=idea.entry_price,
                        actual_price=fill_price,
                        direction=idea.direction.value,
                        order_type=order_type,
                        size_usd=size_usd,
                    )
            except Exception:
                pass

            # ── Roadmap P0-2: slippage guard ──
            # The risk engine approved this trade against idea.entry_price and the
            # resulting stop distance / R:R. If a market fill lands far enough from
            # the expected entry to consume a large fraction of the planned stop
            # buffer, that approval no longer holds. CONFIG.execution.slippage_
            # guard_enabled defaulted ON but was never enforced — only recorded.
            # Now: flatten an adverse over-slipped fill instead of holding a
            # position whose risk:reward is broken.
            try:
                _exec_cfg = getattr(CONFIG, "execution", None)
                if (_exec_cfg is not None
                        and getattr(_exec_cfg, "slippage_guard_enabled", False)
                        and idea.entry_price > 0 and fill_price > 0):
                    _stop_dist = abs(idea.entry_price - idea.stop_loss) / idea.entry_price
                    _slip = abs(fill_price - idea.entry_price) / idea.entry_price
                    _max_slip = _exec_cfg.max_slippage_edge_ratio * _stop_dist
                    _adverse = (
                        (idea.direction == Direction.LONG and fill_price > idea.entry_price)
                        or (idea.direction == Direction.SHORT and fill_price < idea.entry_price)
                    )
                    if _adverse and _stop_dist > 0 and _slip > _max_slip:
                        audit(trade_log,
                              f"Slippage guard tripped for {idea.asset}: fill "
                              f"${fill_price:.4f} vs entry ${idea.entry_price:.4f} "
                              f"({_slip:.2%}) exceeds {_exec_cfg.max_slippage_edge_ratio:.0%} "
                              f"of stop distance ({_stop_dist:.2%}) — flattening",
                              action="slippage_guard", result="FLATTEN",
                              data={"trade_id": idea.id, "symbol": idea.asset,
                                    "fill_price": fill_price, "entry": idea.entry_price,
                                    "slippage_pct": round(_slip, 5),
                                    "stop_dist_pct": round(_stop_dist, 5),
                                    "limit_pct": round(_max_slip, 5)})
                        try:
                            close_msg = await self.close_position(
                                idea.id, reason="slippage_guard")
                            return (
                                f"⚠️ <b>EXECUTION ABORTED — {idea.asset}</b>\n"
                                f"Fill slipped {_slip:.2%} from the planned entry "
                                f"(> {_exec_cfg.max_slippage_edge_ratio:.0%} of the stop "
                                f"buffer), so the position was CLOSED for safety.\n{close_msg}"
                            )
                        except Exception as _sl_close_exc:
                            logger.error("Slippage-guard flatten FAILED for %s: %s",
                                         idea.asset, _sl_close_exc)
                            return (
                                f"🚨 <b>URGENT — {idea.asset} filled with excessive slippage</b>\n"
                                f"Automatic close also FAILED ({_sl_close_exc}). "
                                f"Close this position MANUALLY on Bitget immediately."
                            )
            except Exception as _slip_guard_exc:
                # Fail open: the SL placement below still protects the position.
                logger.warning("Slippage guard error for %s (continuing): %s",
                               idea.asset, _slip_guard_exc)

            # Record API success for degradation tracking
            self.record_api_success()

            audit(trade_log, f"Live order FILLED: {side} {idea.asset}",
                  action="live_execute", result="FILLED",
                  data={
                      "order_id": order_id, "trade_id": idea.id,
                      "side": side, "fill_price": fill_price,
                      "quantity": filled_qty, "cost_usd": cost,
                      "status": order_status,
                      "confirmed": confirmed,
                      "position_confirmed": position_confirmed,
                      "exchange_fees": exchange_fees,
                      "verify_failure_stage": verify.get("failure_stage", ""),
                  })

            # Try to place SL/TP orders (best-effort — not all exchanges support this for spot)
            # GETCLAW: For gap-risk limit orders (weekend metals/stocks),
            # defer TP/SL until after fill to avoid instant trigger on gap.
            if defer_tp_sl and is_pending_limit:
                sl_id, tp_id = None, None
                audit(trade_log,
                      f"TP/SL deferred until fill: {idea.asset} (weekend-queued limit)",
                      action="defer_tp_sl", result="DEFERRED",
                      data={"symbol": idea.asset, "class": asset_class})
            else:
                sl_id, tp_id = await self._place_sl_tp(
                    exchange, idea.asset, idea.direction,
                    filled_qty, idea.stop_loss, idea.take_profit
                )
                # RC-AUD-001: a missing STOP-LOSS (not merely a missing TP) leaves a
                # live, leveraged position with no downside protection. Retry once;
                # if the stop still cannot be placed, FLATTEN the just-opened
                # position rather than reporting success with no stop.
                if sl_id is None:
                    audit(trade_log,
                          f"SL placement failed for {idea.asset} — retrying once",
                          action="sl_retry", result="RETRY",
                          data={"trade_id": idea.id, "symbol": idea.asset})
                    try:
                        retry_sl, retry_tp = await self._place_sl_tp(
                            exchange, idea.asset, idea.direction,
                            filled_qty, idea.stop_loss, idea.take_profit
                        )
                        sl_id = retry_sl
                        if tp_id is None:
                            tp_id = retry_tp
                    except Exception as _sl_exc:
                        logger.warning("SL retry raised for %s: %s", idea.asset, _sl_exc)
                    if sl_id is None:
                        position.sl_order_id = None
                        position.tp_order_id = tp_id
                        self._save_positions()
                        audit(trade_log,
                              f"UNPROTECTED position {idea.asset}: stop-loss could not be "
                              f"placed — flattening for safety",
                              action="sl_tp_failed", result="FLATTEN",
                              data={"trade_id": idea.id, "symbol": idea.asset,
                                    "stop_loss": idea.stop_loss})
                        try:
                            close_msg = await self.close_position(
                                idea.id, reason="sl_placement_failed")
                            return (
                                f"⚠️ <b>EXECUTION ABORTED — {idea.asset}</b>\n"
                                f"Position opened but the stop-loss could not be placed, "
                                f"so it was CLOSED for safety.\n{close_msg}"
                            )
                        except Exception as _close_exc:
                            logger.error("Emergency flatten FAILED for %s: %s",
                                         idea.asset, _close_exc)
                            return (
                                f"🚨 <b>URGENT — {idea.asset} is LIVE with NO stop-loss</b>\n"
                                f"Automatic close also FAILED ({_close_exc}). "
                                f"Close this position MANUALLY on Bitget immediately."
                            )
            position.sl_order_id = sl_id
            position.tp_order_id = tp_id
            # Persist SL/TP order IDs to disk immediately
            self._save_positions()

            # RC-AUD-001: for non-deferred orders, a None sl_id has already been
            # handled (retried + flattened) above, so reaching here means the stop
            # is in place. This warning now only covers the deferred/edge cases.
            if sl_id is None and tp_id is None:
                audit(trade_log,
                      f"SL/TP placement FAILED for {idea.asset} — position is UNPROTECTED",
                      action="sl_tp_failed",
                      data={"trade_id": idea.id, "symbol": idea.asset,
                            "stop_loss": idea.stop_loss, "take_profit": idea.take_profit})

            sl_info = f" | SL order: {sl_id}" if sl_id else " | SL: pending"
            tp_info = f" | TP order: {tp_id}" if tp_id else " | TP: pending"

            lev_info = f" | {leverage}x" if leverage > 1 else ""
            mode_label = "FUTURES" if is_futures else "SPOT"
            dir_icon = "🟢" if side == "buy" else "🔴"
            trail_info = ""
            if trailing_st:
                trail_info = "\n- Trailing: ✅ armed (activates at 1R)"

            # Verification status line
            if confirmed and position_confirmed:
                verify_line = "- Verified: ✅ CONFIRMED (order + position)"
            elif confirmed:
                verify_line = "- Verified: ✅ order confirmed, ⚠️ position check pending"
            else:
                verify_line = f"- Verified: ⚠️ UNCONFIRMED ({verify.get('failure_stage', 'pending')})"

            fee_line = ""
            if exchange_fees > 0:
                fee_line = f"\n- Fees: <code>${exchange_fees:.4f}</code>"

            sl_tp_warn = ""
            if sl_id is None and tp_id is None:
                sl_tp_warn = "\n⚠️ SL/TP FAILED — position unprotected!"

            st_label = getattr(idea, 'strategy_type', 'swing').upper()

            return (
                f"{dir_icon} <b>LIVE {side.upper()} {idea.asset}</b> ({mode_label}{lev_info}) [{st_label}]\n"
                f"{'─' * 16}\n"
                f"- Fill: <code>${fill_price:,.4f}</code>\n"
                f"- Qty: <code>{filled_qty:.6f}</code>\n"
                f"- Cost: <code>${cost:.2f}</code>\n"
                f"- Notional: <code>${fill_price * filled_qty:.2f}</code>\n"
                f"- Leverage: <code>{leverage}x</code>\n"
                f"- SL: <code>${idea.stop_loss:,.4f}</code>{sl_info}\n"
                f"- TP: <code>${idea.take_profit:,.4f}</code>{tp_info}\n"
                f"- Order: <code>{order_id}</code>{fee_line}\n"
                f"- Risk: ✅ APPROVED{trail_info}\n"
                f"- {verify_line}\n"
                f"- Mode: 🔥 Live {mode_label}{sl_tp_warn}"
            )

        except ccxt.InsufficientFunds as exc:
            self.record_api_error()
            audit(trade_log, f"Insufficient funds: {exc}",
                  action="live_execute", result="INSUFFICIENT_FUNDS",
                  data={"asset": idea.asset, "size_usd": size_usd,
                        "is_futures": CONFIG.exchange.trade_mode == "futures"})
            hint = ""
            if CONFIG.exchange.trade_mode == "futures":
                hint = ("\n\n💡 <i>Tip: Your Bitget UTA may need funds transferred "
                        "to the futures account. Check your Bitget app → Assets → Transfer.</i>")
            return f"INSUFFICIENT FUNDS: {exc}{hint}"

        except ccxt.InvalidOrder as exc:
            self.record_api_error()
            audit(trade_log, f"Invalid order: {exc}",
                  action="live_execute", result="INVALID_ORDER",
                  data={"asset": idea.asset, "size_usd": size_usd, "error": str(exc)})
            return f"INVALID ORDER: {exc}"

        except Exception as exc:
            self.record_api_error()
            # Check if the order was already submitted to the exchange.
            # If 'order' exists, create_order succeeded but post-processing crashed.
            # The position is LIVE on the exchange — we MUST record it locally.
            if order is not None and isinstance(order, dict) and order.get("id"):
                logger.error("Post-order crash for %s: %s — creating emergency position",
                             idea.asset, exc)
                _side_upper = ("buy" if idea.direction == Direction.LONG else "sell").upper()
                emergency_pos = LivePosition(
                    trade_id=idea.id,
                    symbol=idea.asset,
                    direction="LONG" if idea.direction == Direction.LONG else "SHORT",
                    entry_price=current_price,
                    quantity=quantity,
                    cost_usd=size_usd,
                    stop_loss=idea.stop_loss,
                    take_profit=idea.take_profit,
                    # Record the ACTUAL leverage used to size this order, not the
                    # config default — pos.leverage drives cost_usd/exposure
                    # recomputation, and dynamic leverage only ever reduces from
                    # the default, so the default over-stated leverage and
                    # under-counted this position's margin.
                    leverage=self._emergency_leverage(leverage_mult, is_futures),
                    is_spot=False,
                    opened_at=datetime.now(UTC),
                    status="open",
                )
                self._positions[idea.id] = emergency_pos
                self._recent_local_opens[normalize_symbol(idea.asset)] = time.time()
                self._save_positions()
                audit(trade_log,
                      f"EMERGENCY position created for {idea.asset} after post-order crash: {exc}",
                      action="emergency_position", result="CREATED",
                      data={"trade_id": idea.id, "asset": idea.asset,
                            "order_id": order.get("id"), "error": str(exc)})
                # Best-effort SL/TP
                try:
                    _ex = await self._get_exchange()
                    sl_id, tp_id = await self._place_sl_tp(
                        _ex, idea.asset, idea.direction,
                        quantity,
                        idea.stop_loss, idea.take_profit,
                    )
                    if sl_id:
                        emergency_pos.sl_order_id = sl_id
                    if tp_id:
                        emergency_pos.tp_order_id = tp_id
                    self._save_positions()
                except Exception as _sltp_exc:
                    # CRITICAL FIX: SL/TP failed on emergency position — log loudly
                    # Determine position state from order data
                    _order_status = (order.get("status") or "unknown").lower()
                    _filled_qty = float(order.get("filled", 0) or 0)
                    _pos_state = "filled" if (_order_status in ("closed", "filled") or _filled_qty > 0) else "pending"
                    logger.critical(
                        "UNPROTECTED POSITION (%s): SL/TP placement failed for %s: %s — "
                        "position has NO stop-loss. Manual intervention required: %s.",
                        _pos_state.upper(), idea.asset, _sltp_exc,
                        "place stops manually" if _pos_state == "filled" else "consider cancelling order")
                    audit(trade_log,
                          f"SL/TP FAILED on emergency position {idea.asset} — UNPROTECTED ({_pos_state})",
                          action="sltp_emergency", result="CRITICAL_FAIL",
                          data={"error": str(_sltp_exc)[:200], "trade_id": emergency_pos.trade_id,
                                "position_state": _pos_state, "order_status": _order_status,
                                "filled_qty": _filled_qty})
                    self._record_warning("sltp_emergency")
                return (f"LIVE {idea.direction.value} {idea.asset} opened "
                        f"(emergency record — parse error: {exc}). "
                        f"SL/TP may need manual verification.")
            else:
                # Order was never submitted — safe to report as failed
                audit(trade_log, f"Live execution failed: {exc}",
                      action="live_execute", result="ERROR",
                      data={"asset": idea.asset, "size_usd": size_usd, "error": str(exc)})
                return f"EXECUTION FAILED: {exc}"

    @staticmethod
    def _sltp_side_error(
        direction: Direction, stop_loss: float, take_profit: float
    ) -> Optional[str]:
        """Side-sanity check for a stop/target pair. Returns an error string if the
        levels are on the wrong side for the direction, else None.

        A LONG must have SL below TP (stop under entry, target above); a SHORT the
        reverse. Both must be positive. A wrong-side (inverted) pair would place a
        stop that fails to protect or a target that fills the instant it's posted —
        so we refuse to place either and let the unprotected-position machinery
        alert/flatten instead. Fires only on genuinely-invalid input.
        """
        try:
            if stop_loss is None or take_profit is None or stop_loss <= 0 or take_profit <= 0:
                return f"non-positive SL/TP (sl={stop_loss}, tp={take_profit})"
            is_long = direction == Direction.LONG
            if is_long and not (stop_loss < take_profit):
                return f"LONG requires SL<TP but sl={stop_loss} tp={take_profit}"
            if (not is_long) and not (stop_loss > take_profit):
                return f"SHORT requires SL>TP but sl={stop_loss} tp={take_profit}"
            return None
        except Exception as exc:
            return f"side-check error: {exc}"

    def _note_sltp_error(self, symbol: str, reason: str) -> None:
        """Record the latest SL/TP placement rejection for a symbol so the
        unprotected-position alert can name the venue reason. Diagnostic only —
        never gates order logic; never raises."""
        try:
            self._last_sltp_error[normalize_symbol(symbol)] = str(reason)[:180]
        except Exception:
            pass

    def _clear_sltp_error(self, symbol: str) -> None:
        try:
            self._last_sltp_error.pop(normalize_symbol(symbol), None)
        except Exception:
            pass

    def _last_sltp_reason(self, symbol: str) -> str:
        try:
            return self._last_sltp_error.get(normalize_symbol(symbol), "")
        except Exception:
            return ""

    async def _place_sl_tp(
        self, exchange: ccxt.Exchange, symbol: str,
        direction: Direction, quantity: float,
        stop_loss: float, take_profit: float
    ) -> tuple[Optional[str], Optional[str]]:
        """Attempt to place SL/TP orders. Returns (sl_order_id, tp_order_id).

        GETCLAW: Always checks existing plan orders first to prevent duplicates.
        Cancels stale SL/TP before placing new ones.

        For UTA futures accounts: uses Bitget v3 REST API directly because
        ccxt's triggerPrice param executes immediately as a market order in
        UTA mode instead of creating a pending trigger order.

        For non-UTA futures: falls back to ccxt trigger orders.
        For spot: best-effort (may not be supported).
        """
        sl_id = None
        tp_id = None
        close_side = "sell" if direction == Direction.LONG else "buy"

        # Side-sanity: never post a wrong-side (inverted) SL/TP — it would fail to
        # protect or fill instantly. Refuse both and leave the position to the
        # unprotected-position alert/escalation (safer than a bad stop on-venue).
        _side_err = self._sltp_side_error(direction, stop_loss, take_profit)
        if _side_err:
            audit(trade_log,
                  f"SL/TP side-sanity FAILED for {symbol} ({direction}): {_side_err} "
                  f"— refusing to place SL/TP",
                  action="sltp_side_check", result="REJECTED", level=logging.ERROR,
                  data={"symbol": symbol, "direction": str(direction),
                        "stop_loss": stop_loss, "take_profit": take_profit})
            return None, None

        # GETCLAW: Check and cancel existing plan orders before placing new ones.
        # Prevents duplicate SL/TP orders that can cause double-closes.
        ccxt_sym = symbol if ":USDT" in symbol else f"{symbol}:USDT"
        try:
            existing_plans = await exchange.fetch_open_orders(
                ccxt_sym, params={"productType": "USDT-FUTURES", "isPlan": "plan_order"})
            if existing_plans:
                cancelled = 0
                for plan in existing_plans:
                    try:
                        await exchange.cancel_order(plan["id"], ccxt_sym)
                        cancelled += 1
                    except Exception:
                        pass
                if cancelled > 0:
                    audit(trade_log,
                          f"Cleared {cancelled} existing plan order(s) for {symbol} before placing new SL/TP",
                          action="plan_order_cleanup", result="OK",
                          data={"symbol": symbol, "cancelled": cancelled})
        except Exception as plan_exc:
            # Non-critical: some exchanges don't support isPlan filter
            logger.debug("Plan order check failed for %s: %s", symbol, plan_exc)

        # Futures-only mode: spot SL/TP path removed

        # Use cached UTA detection result instead of making an extra API call.
        # _detect_hold_mode already ran during _ensure_leverage and set _is_uta.
        use_v3 = self._is_uta if self._is_uta is not None else False
        if self._is_uta is None:
            # First call — haven't detected yet; probe once
            try:
                await exchange.privateMixGetV2MixAccountAccount(
                    {"symbol": "BTCUSDT", "productType": "USDT-FUTURES"})
            except Exception as exc:
                if "40085" in str(exc):
                    use_v3 = True
                    self._is_uta = True
                else:
                    self._is_uta = False

        if use_v3:
            # UTA mode: place SL/TP via Bitget v3 REST API directly
            # Brief delay to let position settle on Bitget before placing SL/TP.
            # Prevents error 31008 ("no position") on fast fills.
            import asyncio as _aio_delay
            await _aio_delay.sleep(1.5)
            # Get tick size from exchange markets for proper price precision
            price_precision = None
            # Try both spot and swap symbol formats for market lookup
            swap_symbol = symbol if ":USDT" in symbol else f"{symbol}:USDT"
            lookup_symbols = [symbol, swap_symbol]
            try:
                if not exchange.markets:
                    await exchange.load_markets()
                for sym in lookup_symbols:
                    mkt = exchange.markets.get(sym)
                    if mkt and mkt.get("precision", {}).get("price") is not None:
                        price_precision = mkt["precision"]["price"]
                        break
            except Exception:
                pass
            # UPGRADE: round SL/TP onto the symbol's tick grid via ccxt's own
            # price_to_precision (tick-aware) rather than a decimal-places
            # heuristic. Try swap symbol format first, then spot.
            sl_rounded = None
            tp_rounded = None
            for sym in lookup_symbols:
                sl_rounded = self._round_price_to_market(exchange, sym, stop_loss)
                tp_rounded = self._round_price_to_market(exchange, sym, take_profit)
                if sl_rounded is not None and tp_rounded is not None:
                    break
            sl_id, tp_id = await self._place_sl_tp_v3(
                symbol, direction, quantity, stop_loss, take_profit,
                price_precision=price_precision,
                sl_str=sl_rounded, tp_str=tp_rounded,
            )
        else:
            # Classic mode: use ccxt trigger orders
            # Always send tradeSide=close + reduceOnly for SL/TP to prevent reverse opens
            extra_params = {"productType": "USDT-FUTURES", "tradeSide": "close", "reduceOnly": True}

            # Audit fix #21: round trigger prices onto the symbol's tick grid —
            # previously only the v3 path applied precision and the classic path
            # sent raw floats (venue may reject or silently round them).
            _sl_r = self._round_price_to_market(exchange, symbol, stop_loss)
            _tp_r = self._round_price_to_market(exchange, symbol, take_profit)
            if _sl_r is not None:
                try:
                    stop_loss = float(_sl_r)
                except (TypeError, ValueError):
                    pass
            if _tp_r is not None:
                try:
                    take_profit = float(_tp_r)
                except (TypeError, ValueError):
                    pass

            # Stop-loss
            try:
                sl_order = await exchange.create_order(
                    symbol=symbol,
                    type="market",
                    side=close_side,
                    amount=quantity,
                    params={
                        "triggerPrice": stop_loss,
                        "triggerType": "last",
                        **extra_params,
                    },
                )
                sl_id = sl_order.get("id")
                if sl_id:
                    self._clear_sltp_error(symbol)
                audit(trade_log, f"SL order placed: {sl_id}",
                      action="sl_order", result="OK",
                      data={"symbol": symbol, "trigger": stop_loss, "futures": True})
            except Exception as exc:
                logger.warning("SL order failed for %s: %s", symbol, exc)
                self._note_sltp_error(symbol, str(exc))
                audit(trade_log, f"SL order not placed: {exc}",
                      action="sl_order", result="SKIP",
                      data={"symbol": symbol, "reason": str(exc)[:200]})

            # Take-profit
            try:
                tp_order = await exchange.create_order(
                    symbol=symbol,
                    type="market",
                    side=close_side,
                    amount=quantity,
                    params={
                        "triggerPrice": take_profit,
                        "triggerType": "last",
                        **extra_params,
                    },
                )
                tp_id = tp_order.get("id")
                audit(trade_log, f"TP order placed: {tp_id}",
                      action="tp_order", result="OK",
                      data={"symbol": symbol, "trigger": take_profit, "futures": True})
            except Exception as exc:
                logger.warning("TP order failed for %s: %s", symbol, exc)
                audit(trade_log, f"TP order not placed: {exc}",
                      action="tp_order", result="SKIP",
                      data={"symbol": symbol, "reason": str(exc)[:200]})

        return sl_id, tp_id

    @staticmethod
    def _fetch_v3_positions_raw() -> list[dict]:
        """Fetch all open positions from Bitget v3 API.

        Returns list of raw position dicts.  Handles both response shapes:
        ``{"data": [...]}`` and ``{"data": {"list": [...]}}``.
        Synchronous — callers must wrap in ``asyncio.to_thread``.
        """
        from bot.core.bitget_v3_client import BitgetV3Client
        client = BitgetV3Client.from_config()
        if not client.has_credentials:
            return []
        path = "/api/v3/position/current-position?category=USDT-FUTURES"
        try:
            data = client.get(path)
            if data.get("code") != "00000":
                return []
            payload = data.get("data", [])
            # Handle both {"data": [...]} and {"data": {"list": [...]}}
            if isinstance(payload, dict):
                payload = payload.get("list", [])
            return [item for item in payload if isinstance(item, dict)]
        except Exception:
            return []

    @staticmethod
    def _fetch_position_margin_mode_v3(bitget_symbol: str) -> Optional[str]:
        """Query v3 position API to get the actual marginMode for a specific symbol.

        Returns 'crossed' or 'isolated', or None if lookup fails.
        Synchronous — callers must wrap in asyncio.to_thread.
        """
        positions = LiveExecutor._fetch_v3_positions_raw()
        for item in positions:
            if item.get("symbol") == bitget_symbol:
                mm = (item.get("marginMode") or "").lower()
                if mm in ("crossed", "isolated"):
                    return mm
        return None

    async def sync_positions_from_exchange(self) -> None:
        """Sync tracked position metadata (leverage, margin mode) with exchange.

        Called on startup after position loading. Queries v3 position API and
        updates any tracked positions whose leverage or margin mode differs
        from what the exchange reports. This prevents risk calculation errors
        from stale data (e.g., leverage changed manually on exchange).
        """
        import asyncio as _aio_sync
        open_pos = [p for p in self._positions.values() if p.status == "open"]
        if not open_pos:
            return

        try:
            v3_positions = await _aio_sync.get_event_loop().run_in_executor(
                None, LiveExecutor._fetch_v3_positions_raw
            )
        except Exception as exc:
            logger.warning("sync_positions_from_exchange: v3 fetch failed: %s", exc)
            return

        if not v3_positions:
            return

        # Build lookup: Bitget symbol → position data
        exchange_map: dict[str, dict] = {}
        for ep in v3_positions:
            sym = ep.get("symbol", "")
            if sym:
                exchange_map[sym] = ep

        synced = 0
        for pos in open_pos:
            bitget_sym = pos.symbol.replace("/USDT", "USDT").replace(":USDT", "")
            ex_data = exchange_map.get(bitget_sym)
            if not ex_data:
                continue

            changed = False

            # Sync leverage
            ex_lev_raw = ex_data.get("leverage")
            if ex_lev_raw is not None:
                try:
                    ex_lev = int(float(ex_lev_raw))
                except (ValueError, TypeError):
                    ex_lev = 0
                if ex_lev > 0 and ex_lev != pos.leverage:
                    logger.warning(
                        "LEVERAGE SYNC %s: tracked=%dx, exchange=%dx — updating to exchange value",
                        pos.symbol, pos.leverage, ex_lev)
                    audit(trade_log,
                          f"Leverage sync: {pos.symbol} {pos.leverage}x → {ex_lev}x",
                          action="leverage_sync", result="UPDATED",
                          data={"trade_id": pos.trade_id, "old": pos.leverage, "new": ex_lev})
                    pos.leverage = ex_lev
                    # Recalculate cost_usd with correct leverage
                    if pos.entry_price > 0 and pos.quantity > 0:
                        raw_notional = pos.entry_price * pos.quantity
                        pos.cost_usd = raw_notional / ex_lev
                    changed = True

            if changed:
                synced += 1

        if synced > 0:
            self._save_positions()
            logger.info("Position sync: updated %d/%d positions from exchange", synced, len(open_pos))
        else:
            logger.info("Position sync: all %d positions match exchange", len(open_pos))

    async def _place_sl_tp_v3(
        self, symbol: str, direction: Direction, quantity: float,
        stop_loss: float, take_profit: float,
        price_precision: object = None,
        sl_str: Optional[str] = None,
        tp_str: Optional[str] = None,
    ) -> tuple[Optional[str], Optional[str]]:
        """Place SL/TP via Bitget v3 REST API for UTA accounts.

        Uses /api/v3/trade/place-strategy-order which creates pending
        TP/SL orders attached to the position (not immediate market orders).
        """
        import json as _json

        from bot.core.bitget_v3_client import BitgetV3Client

        sl_id = None
        tp_id = None

        # Side-sanity: when BOTH levels are set, refuse an inverted pair (a
        # wrong-side stop/target that fails to protect or fills instantly). Lenient
        # on a single-sided update (one level 0), which is an intentional SL-only
        # or TP-only call (e.g. trailing-stop moves). Covers the direct v3 callers
        # that bypass _place_sl_tp.
        if stop_loss and take_profit and stop_loss > 0 and take_profit > 0:
            _side_err = self._sltp_side_error(direction, stop_loss, take_profit)
            if _side_err:
                audit(trade_log,
                      f"SL/TP side-sanity FAILED (v3) for {symbol} ({direction}): "
                      f"{_side_err} — refusing to place SL/TP",
                      action="sltp_side_check", result="REJECTED", level=logging.ERROR,
                      data={"symbol": symbol, "direction": str(direction),
                            "stop_loss": stop_loss, "take_profit": take_profit})
                return None, None

        # Strip "/USDT" from ccxt symbol format to get Bitget symbol
        bitget_symbol = symbol.replace("/USDT", "USDT").replace(":USDT", "")

        # v3 strategy order API posSide:
        #   Bitget UTA returns posSide="long"/"short" even in one-way mode.
        #   Always use direction-based posSide; the retry loop handles edge
        #   cases by cycling through net/omitted if needed.
        pos_side = "long" if direction == Direction.LONG else "short"

        def _v3_post(path: str, body_dict: dict) -> dict:
            # Signing/transport via BitgetV3Client. Preserves the original
            # contract: RETURN an error-shaped dict (never raise) so the retry
            # loop below branches on the response code, and recover the JSON
            # error body off an HTTPError exactly as before.
            try:
                return cast(dict, BitgetV3Client.from_config().request("POST", path, body_dict))
            except Exception as e:
                if hasattr(e, 'read'):
                    try:
                        raw_body = e.read().decode()
                        return cast(dict, _json.loads(raw_body))
                    except (ValueError, UnicodeDecodeError) as parse_exc:
                        logger.warning("Non-JSON error response from exchange: %s (parse error: %s)",
                                       getattr(e, 'code', '?'), parse_exc)
                        return {"code": str(getattr(e, 'code', 'ERROR')), "msg": raw_body[:500] if raw_body else str(e)}
                return {"code": "ERROR", "msg": str(e)}

        # Round SL/TP prices to the symbol's tick precision.
        # Bitget ccxt precision is typically the number of decimal places.
        def _round_price(price: float) -> str:
            """Round price to exchange-allowed precision."""
            if price_precision is not None:
                # ccxt returns precision as decimal places (int) for Bitget
                if isinstance(price_precision, int):
                    dp = price_precision
                elif isinstance(price_precision, float) and price_precision < 1:
                    # tick-size format (e.g. 0.0001 → 4 decimals)
                    import math
                    dp = max(0, -int(math.floor(math.log10(price_precision))))
                else:
                    dp = int(cast(Any, price_precision))
                return f"{price:.{dp}f}"
            # Fallback: conservative rounding by magnitude
            # Use fewer decimals to avoid precision rejection (25606)
            if price >= 1000:
                return f"{price:.1f}"
            elif price >= 10:
                return f"{price:.2f}"
            elif price >= 1:
                return f"{price:.3f}"
            elif price >= 0.1:
                return f"{price:.4f}"
            elif price >= 0.01:
                return f"{price:.5f}"
            elif price >= 0.001:
                return f"{price:.5f}"
            else:
                return f"{price:.6f}"

        # Place combined TP/SL strategy order
        # AUDIT FIX: offload blocking _v3_post to thread pool
        import asyncio as _asyncio
        tp_final = tp_str if tp_str is not None else _round_price(take_profit)
        sl_final = sl_str if sl_str is not None else _round_price(stop_loss)

        # Build payload:
        # Bitget v3 UTA requires:
        # - posSide = "long"/"short" (required even in one-way mode)
        # - marginMode = "isolated"/"crossed" (CRITICAL: required for isolated positions,
        #   without it Bitget returns 31008 "no position")
        # Determine the ACTUAL margin mode for this specific position.
        # Different positions can have different margin modes (e.g., AAVE=isolated,
        # BIO=crossed). Using a single global `_actual_margin_mode` fails for
        # mixed-margin accounts. Query v3 position data to get the truth.
        position_margin_mode = self._actual_margin_mode or CONFIG.exchange.margin_mode or "crossed"
        try:
            import asyncio as _aio_mm
            v3_pos_data = await _aio_mm.to_thread(
                LiveExecutor._fetch_position_margin_mode_v3, bitget_symbol)
            if v3_pos_data:
                position_margin_mode = v3_pos_data
        except Exception:
            pass  # Fall back to global/config value

        payload: dict[str, str] = {
            "category": "USDT-FUTURES",
            "symbol": bitget_symbol,
            "type": "tpsl",
            "tpslMode": "full",
            "takeProfit": tp_final,
            "stopLoss": sl_final,
            "tpOrderType": "market",
            "slOrderType": "market",
            "posSide": pos_side,
            "marginMode": position_margin_mode,
            "clientOid": self._client_oid(f"{bitget_symbol}_{pos_side}_sltp_{int(time.time())}"),
        }

        logger.info("v3 SL/TP request: symbol=%s hedge=%s posSide=%s marginMode=%s TP=%s SL=%s (raw TP=%s SL=%s, rounded=%s/%s, precision=%s)",
                     bitget_symbol, self._hedge_mode, payload["posSide"], payload["marginMode"],
                     tp_final, sl_final,
                     take_profit, stop_loss, tp_str, sl_str, price_precision)

        # Retry logic for error 31008 ("no position") — position may not be
        # settled on exchange yet after fill.  Wait and retry up to 4 times
        # with increasing delays: 2s, 4s, 6s, 8s.
        _MAX_31008_RETRIES = 5
        _31008_CODES = ("31008", "31009")  # 31009 = variant on some API versions
        _PRECISION_CODES = ("25606", "25607")  # precision mismatch errors

        for attempt in range(_MAX_31008_RETRIES + 1):
            try:
                # Regenerate clientOid on retries to avoid duplicate rejection
                if attempt > 0:
                    payload["clientOid"] = self._client_oid(
                        f"{bitget_symbol}_{pos_side}_sltp_{int(time.time())}_{attempt}")

                result = await _asyncio.to_thread(_v3_post, "/api/v3/trade/place-strategy-order", payload)

                if result.get("code") == "00000":
                    data = result.get("data", {})
                    # Bitget v3 returns orderId for the combined strategy order
                    order_id = data.get("orderId") or data.get("slOrderId") or data.get("tpOrderId")
                    # Audit F-9: a "success" code with no usable order id must NOT
                    # be treated as a placed stop. The old code substituted the
                    # literal "v3-strategy" sentinel and set sl_id/tp_id to it, so
                    # the position looked protected (truthy sl_id ⇒ the RC-AUD-001
                    # flatten and grace retries skip it) while no cancellable
                    # trigger order existed, and cancel_order("v3-strategy") would
                    # fail on close. Leave sl_id/tp_id = None ⇒ caller retries/flattens.
                    if not order_id:
                        self._note_sltp_error(symbol, "success code but no order id returned")
                        logger.warning(
                            "v3 SL/TP returned success code but NO order id for %s "
                            "— treating as failure (audit F-9): %s",
                            bitget_symbol, str(data)[:200])
                        audit(trade_log,
                              f"v3 SL/TP success code with no order id for {bitget_symbol}",
                              action="sl_tp_v3", result="NO_ORDER_ID",
                              data={"symbol": bitget_symbol, "data": str(data)[:200],
                                    "attempt": attempt + 1})
                        break  # no usable stop — exit with sl_id/tp_id = None
                    sl_id = order_id
                    tp_id = order_id
                    self._clear_sltp_error(symbol)
                    retry_note = f" (attempt {attempt + 1})" if attempt > 0 else ""
                    audit(trade_log, f"v3 SL/TP strategy order placed: order={order_id}{retry_note}",
                          action="sl_tp_v3", result="OK",
                          data={"symbol": bitget_symbol, "sl": sl_final, "tp": tp_final,
                                "order_id": order_id, "hedge_mode": self._hedge_mode,
                                "attempt": attempt + 1})
                    break  # Success — exit retry loop
                else:
                    error_msg = result.get("msg", str(result))
                    error_code = result.get("code", "")

                    # Error 31008: "There is no position in this position"
                    # Root cause: posSide or marginMode doesn't match the
                    # actual position on exchange.  Retry cycle tries all
                    # combinations: posSide (net/long/short) x marginMode
                    # (isolated/crossed).
                    # Error 25606: "trigger price does not meet precision requirements"
                    # Root cause: ccxt precision doesn't match Bitget strategy order API.
                    # Retry with reduced decimal places.
                    _RETRYABLE_CODES = ("31008", "31009", "40019", "40020", "25606", "25607")
                    if error_code in _RETRYABLE_CODES and attempt < _MAX_31008_RETRIES:
                        delay = (attempt + 1) * 2  # 2s, 4s, 6s, 8s, 10s

                        # Precision error: reduce decimal places on TP/SL
                        if error_code in _PRECISION_CODES:
                            def _reduce_precision(price_str: str) -> str:
                                """Remove one trailing decimal digit."""
                                if "." in price_str:
                                    # Strip trailing zeros first, then remove last digit
                                    stripped = price_str.rstrip("0")
                                    if stripped.endswith("."):
                                        return stripped + "0"  # keep at least X.0
                                    return stripped[:-1] if len(stripped.split(".")[1]) > 1 else stripped
                                return price_str
                            payload["takeProfit"] = _reduce_precision(payload["takeProfit"])
                            payload["stopLoss"] = _reduce_precision(payload["stopLoss"])
                            tp_final = payload["takeProfit"]
                            sl_final = payload["stopLoss"]
                            logger.warning(
                                "v3 SL/TP precision error %s for %s — retry %d/%d with reduced precision TP=%s SL=%s",
                                error_code, bitget_symbol, attempt + 1, _MAX_31008_RETRIES,
                                tp_final, sl_final)
                        else:
                            # Cycle through combinations systematically:
                            #   attempt 0 (initial): pos_side + config marginMode
                            #   attempt 1: toggle marginMode
                            #   attempt 2: toggle posSide (net ↔ long/short)
                            #   attempt 3: toggle marginMode again
                            #   attempt 4: toggle posSide + remove
                            if attempt % 2 == 0:
                                # Even retries: toggle posSide
                                current_ps = payload.get("posSide", "")
                                dir_side = "long" if direction == Direction.LONG else "short"
                                if current_ps == "net":
                                    payload["posSide"] = dir_side
                                elif current_ps == dir_side:
                                    payload["posSide"] = "net"
                                else:
                                    payload["posSide"] = "net"
                            else:
                                # Odd retries: toggle marginMode
                                current_mm = payload.get("marginMode", "")
                                if current_mm == "isolated":
                                    payload["marginMode"] = "crossed"
                                else:
                                    payload["marginMode"] = "isolated"
                        logger.warning(
                            "v3 SL/TP error %s for %s — retry %d/%d in %ds (marginMode=%s, posSide=%s)",
                            error_code, bitget_symbol, attempt + 1, _MAX_31008_RETRIES, delay,
                            payload.get("marginMode", "N/A"), payload.get("posSide", "OMITTED"))
                        audit(trade_log,
                              f"v3 SL/TP {error_code} retry {attempt + 1}/{_MAX_31008_RETRIES} for {bitget_symbol}",
                              action="sl_tp_v3_retry_cycle", result="RETRY",
                              data={"symbol": bitget_symbol, "attempt": attempt + 1, "delay": delay,
                                    "marginMode": payload.get("marginMode"),
                                    "posSide": payload.get("posSide", "OMITTED"),
                                    "error_code": error_code})
                        await _asyncio.sleep(delay)
                        continue  # Retry

                    self._note_sltp_error(symbol, f"{error_code}: {error_msg}")
                    logger.warning("v3 strategy order failed (code=%s): %s", error_code, error_msg)
                    audit(trade_log, f"v3 SL/TP failed: {error_msg}",
                          action="sl_tp_v3", result="FAIL",
                          data={"symbol": bitget_symbol, "response": str(result)[:300],
                                "payload": {k: v for k, v in payload.items() if k != "clientOid"},
                                "attempt": attempt + 1})
                    break  # Non-retryable error — exit loop
            except Exception as exc:
                logger.warning("v3 SL/TP placement error for %s (attempt %d): %s",
                               bitget_symbol, attempt + 1, exc)
                if attempt < _MAX_31008_RETRIES:
                    await _asyncio.sleep(2)
                    continue
                self._note_sltp_error(symbol, f"exception: {exc}")
                audit(trade_log, f"v3 SL/TP error: {exc}",
                      action="sl_tp_v3", result="ERROR",
                      data={"symbol": bitget_symbol, "error": str(exc)[:200]})

        return sl_id, tp_id

    # ── Position management ──────────────────────────────────────

    async def _partial_close(self, exchange, pos, qty: float, stage: str) -> float:
        """Close `qty` of a position with a reduceOnly market order. Returns the
        quantity actually submitted (0.0 on failure). Used by the partial-TP
        ladder; reduceOnly means the venue clamps to the live position size, so
        it can never flip or over-close."""
        try:
            _q = exchange.amount_to_precision(pos.symbol, qty)
            qty = float(_q) if _q is not None else qty
        except Exception:
            pass
        if qty <= 0:
            return 0.0
        close_side = "sell" if pos.direction == "LONG" else "buy"
        params = {"productType": "USDT-FUTURES", "reduceOnly": True}
        if not getattr(self, "_is_uta", False):
            params["tradeSide"] = "close"
        await exchange.create_order(
            symbol=pos.symbol, type="market", side=close_side,
            amount=qty, params=params,
        )
        return qty

    async def _run_partial_tp(self, exchange, pos, price: float) -> None:
        """Partial take-profit ladder (bot/core/partial_tp.py), applied as an
        additive overlay on the exchange SL/TP backstops:
          - TP1 (1.5R): close 50%, move SL to breakeven
          - TP2 (2.5R): close 30%, lock 1R of profit
          - Runner (20%): SL ratchets via the trail; the EXISTING static SL check
            closes it when hit (we never lower the SL, and we don't double-close).
        Banks profit early to fix the realized R:R asymmetry without removing the
        exchange-side safety net."""
        import dataclasses as _dc
        from bot.core.partial_tp import (
            create_partial_tp_state, check_partial_tp, PartialTPState,
        )

        is_long = pos.direction == "LONG"

        if not pos.partial_tp_state:
            st = create_partial_tp_state(
                trade_id=pos.trade_id, direction=pos.direction,
                entry_price=pos.entry_price, stop_loss=pos.stop_loss,
                take_profit=pos.take_profit, quantity=pos.quantity,
                atr=getattr(pos, "atr_at_entry", 0.0) or pos.entry_price * 0.02,
            )
            st.current_sl = pos.stop_loss
            st.remaining_qty = pos.quantity
        else:
            try:
                st = PartialTPState(**pos.partial_tp_state)
            except Exception:
                # Schema drift — rebuild from the live position.
                st = create_partial_tp_state(
                    trade_id=pos.trade_id, direction=pos.direction,
                    entry_price=pos.entry_price, stop_loss=pos.stop_loss,
                    take_profit=pos.take_profit, quantity=pos.quantity,
                    atr=getattr(pos, "atr_at_entry", 0.0) or pos.entry_price * 0.02,
                )

        def _ratchet_sl(new_sl: float) -> bool:
            """Raise (LONG) / lower (SHORT) the stop only — never loosen it."""
            better = new_sl > pos.stop_loss if is_long else new_sl < pos.stop_loss
            if better:
                pos.stop_loss = new_sl
                st.current_sl = new_sl
                return True
            return False

        changed = False
        for act in check_partial_tp(st, price):
            if act.action == "close_partial":
                qty = min(act.qty_to_close, pos.quantity)
                if qty > 0:
                    submitted = await self._partial_close(exchange, pos, qty, act.stage)
                    if submitted > 0:
                        pos.quantity = max(0.0, pos.quantity - submitted)
                        changed = True
                        audit(trade_log,
                              f"Partial TP {act.stage} for {pos.symbol}: closed {submitted} "
                              f"@ ${price:.4f} ({act.reason})",
                              action="partial_tp", result=act.stage.upper(),
                              data={"trade_id": pos.trade_id, "symbol": pos.symbol,
                                    "stage": act.stage, "qty_closed": submitted,
                                    "remaining": pos.quantity, "price": price})
                if act.new_sl and _ratchet_sl(act.new_sl):
                    changed = True
                    try:
                        await self._update_exchange_sl(exchange, pos, pos.stop_loss)
                    except Exception as exc:
                        logger.debug("Partial-TP SL update failed for %s: %s", pos.symbol, exc)
            elif act.action == "move_sl" and act.new_sl:
                if _ratchet_sl(act.new_sl):
                    changed = True
                    try:
                        await self._update_exchange_sl(exchange, pos, pos.stop_loss)
                    except Exception as exc:
                        logger.debug("Partial-TP runner SL update failed for %s: %s", pos.symbol, exc)
            # act.action == "close_runner" is intentionally NOT executed here:
            # the runner exits through the existing static SL check, which uses
            # the ratcheted pos.stop_loss — keeping a single, locked close path.

        # Persist the ladder state (and any qty/SL change) onto the position.
        pos.partial_tp_state = _dc.asdict(st)
        if changed:
            self._save_positions()

    def _local_stop_breached(self, pos, price: float) -> tuple[bool, str]:
        """Whether `price` has hit `pos`'s local stop or target.

        Pure mirror of the per-tick static SL/TP check (kept in lock-step with
        it) so the grace sub-loop and the monitor agree on what "breached"
        means. Guards stop_loss/take_profit > 0 so an unset level (0.0) can
        never be read as an instant TP/SL hit.
        """
        if price <= 0:
            return False, ""
        trailing = bool(pos.trailing_state and pos.trailing_state.get("trailing_active"))
        sl, tp = pos.stop_loss, pos.take_profit
        if pos.direction == "LONG":
            if sl > 0 and price <= sl:
                return True, "TRAILING SL HIT" if trailing else "SL HIT"
            if tp > 0 and price >= tp:
                return True, "TP HIT"
        else:  # SHORT
            if sl > 0 and price >= sl:
                return True, "TRAILING SL HIT" if trailing else "SL HIT"
            if tp > 0 and price <= tp:
                return True, "TP HIT"
        return False, ""

    async def _guard_unprotected_grace(self, exchange, pos) -> Optional[str]:
        """Tight, BOUNDED sub-loop for a just-opened position that still has no
        exchange stop.

        The per-tick monitor only revisits a position once per scan interval
        (~10-60s). A freshly-opened, stop-less position on a leveraged perp is
        therefore blind for a full interval — exactly the window in which an
        adverse move would have hit its (un-placed) stop. Rather than wait, run
        a short local loop NOW: each pass re-attempts the exchange stop and, if
        price has already breached the intended stop, closes the position.

        Safe by construction:
          * Inline on the single monitor task that already owns position
            mutation — no background task, so no double-close race.
          * Bounded by ``unprotected_guard_max_iterations`` so it can never
            wedge monitoring of the other positions.
          * Purely protective — only places a stop or closes; never opens.

        Returns a close message if it had to flatten locally, else None
        (stop placed, price never breached, or the bound was reached and the
        per-tick monitor takes over).
        """
        if not CONFIG.execution.unprotected_guard_enabled:
            return None
        max_iter = max(1, CONFIG.execution.unprotected_guard_max_iterations)
        interval = max(0.1, CONFIG.execution.unprotected_guard_interval_s)
        direction = Direction.LONG if pos.direction == "LONG" else Direction.SHORT

        for i in range(max_iter):
            # 1. The real fix: get the exchange stop on so the venue protects it.
            if not pos.sl_order_id and pos.stop_loss > 0:
                try:
                    sl_id, tp_id = await self._place_sl_tp(
                        exchange, pos.symbol, direction,
                        pos.quantity, pos.stop_loss, pos.take_profit,
                    )
                    if sl_id and not pos.sl_order_id:
                        pos.sl_order_id = sl_id
                    if tp_id and not pos.tp_order_id:
                        pos.tp_order_id = tp_id
                    if pos.sl_order_id:
                        self._save_positions()
                        audit(trade_log,
                              f"Grace sub-loop placed exchange stop for {pos.symbol} "
                              f"after {i + 1} attempt(s)",
                              action="grace_guard", result="PLACED",
                              data={"trade_id": pos.trade_id, "symbol": pos.symbol,
                                    "sl_id": pos.sl_order_id, "iterations": i + 1})
                        return None  # protected — done
                except Exception as exc:
                    logger.debug("Grace sub-loop SL placement failed for %s: %s",
                                 pos.symbol, exc)

            # 2. Still no exchange stop — close locally if price has breached it.
            price = 0.0
            try:
                t = await exchange.fetch_ticker(pos.symbol)
                price = float(t.get("last", 0) or 0)
            except Exception as exc:
                logger.debug("Grace sub-loop ticker fetch failed for %s: %s",
                             pos.symbol, exc)
            breached, reason = self._local_stop_breached(pos, price)
            if breached:
                msg = await self.close_position(
                    pos.trade_id, f"{reason} (grace sub-loop)", price)
                audit(trade_log,
                      f"Grace sub-loop closed UNPROTECTED {pos.symbol} on {reason} "
                      f"@ ${price:.6f}",
                      action="grace_guard", result="CLOSED_LOCAL",
                      data={"trade_id": pos.trade_id, "symbol": pos.symbol,
                            "reason": reason, "price": price, "iterations": i + 1})
                return msg

            # 3. Unprotected but not breached — wait a beat, then retry.
            if i < max_iter - 1:
                await asyncio.sleep(interval)

        # Bound reached without resolution — hand back to the per-tick monitor.
        audit(trade_log,
              f"Grace sub-loop exhausted for {pos.symbol} — still unprotected, "
              f"per-tick monitor continues",
              action="grace_guard", result="EXHAUSTED",
              data={"trade_id": pos.trade_id, "symbol": pos.symbol,
                    "iterations": max_iter})
        return None

    @staticmethod
    def _ticker_too_old(ticker, max_age_sec: float, now_sec: float) -> bool:
        """True if the ticker's timestamp is older than ``max_age_sec`` — so its
        ``last`` price must not drive stop logic. ``max_age_sec <= 0`` disables the
        check. A missing/zero timestamp returns False (can't verify freshness →
        don't disable monitoring). Fail-safe: any error → False (not stale)."""
        if max_age_sec <= 0:
            return False
        try:
            ts = (ticker or {}).get("timestamp")
            if not ts:
                return False
            return (now_sec - float(ts) / 1000.0) > max_age_sec
        except Exception:
            return False

    async def check_positions(self) -> list[str]:
        """Check open positions against current prices. Returns list of close/update messages.

        Handles:
        1. Static SL/TP hits → close position
        2. Trailing stop updates → tighten SL when price moves favorably
        3. Pending limit order fills → transition to open position
        4. Pending limit order expiry → cancel stale limit orders
        """
        if not self._positions:
            return []

        closed_messages = []
        try:
            exchange = await self._get_exchange()
            # C2-27 FIX: Fetch tickers per-symbol instead of batch.
            # A single delisted/erroring symbol in fetch_tickers() would block
            # SL/TP checks for ALL positions. Per-symbol isolation ensures
            # monitoring continues for healthy symbols.
            open_symbols = [p.symbol for p in self._positions.values() if p.status in ("open", "pending_fill")]
            tickers: dict = {}
            for sym in open_symbols:
                try:
                    t = await exchange.fetch_ticker(sym)
                    tickers[sym] = t
                except Exception as e:
                    # Track consecutive failures per symbol
                    count = self._ticker_failure_count.get(sym, 0) + 1
                    self._ticker_failure_count[sym] = count
                    level = "warning" if count < 3 else "error"
                    getattr(trade_log, level)(
                        "fetch_ticker failed for %s (%d consecutive): %s",
                        sym, count, e,
                    )
                    continue
            # Reset failure count for symbols that succeeded
            for sym in open_symbols:
                if sym in tickers:
                    self._ticker_failure_count.pop(sym, None)

            for trade_id, pos in list(self._positions.items()):
                # ── Handle pending limit orders ──
                if pos.status == "pending_fill":
                    msg = await self._check_pending_limit(exchange, trade_id, pos)
                    if msg:
                        closed_messages.append(msg)
                    continue

                if pos.status != "open":
                    continue

                # ── Duplicate-record guard (live incident 2026-07-07) ──
                # A second internal record for an already-booked close (adoption
                # sweeps mint different trade_ids for one exchange position) must
                # be suppressed BEFORE local SL/TP monitoring: otherwise its SL
                # breach fires a close against a flat book → 25227 → a second
                # booking with a second notification, double-counted PnL, and a
                # double-fed learning store. Silent by design (audit-logged).
                if self._is_duplicate_close_booking(pos):
                    self._suppress_duplicate_record(pos)
                    continue

                # ── Defer startup-recovered "closing" positions to reconcile ──
                # This position's true state is ambiguous (see
                # _recovered_from_closing's docstring) -- a close order may
                # have already reached the exchange before the process died
                # mid-close. Placing a SECOND close here, priced off a
                # possibly-stale local ticker, is exactly the duplicate-close
                # incident this guards against. reconcile_positions() (called
                # right after check_positions() every tick) queries the
                # exchange directly and resolves this authoritatively.
                if trade_id in self._recovered_from_closing:
                    continue

                # ── Clear a stale "unprotected" alarm ──
                # `unprotected` is a runtime marker set when an exchange stop
                # could not be placed (adoption / emergency / residual). It was
                # never cleared, so a position stayed flagged forever even after
                # a later retry (grace, grace-guard, or the per-tick retry below)
                # got the stop on. Clear it the moment an exchange stop exists,
                # on whichever path placed it.
                if getattr(pos, "unprotected", False) and pos.sl_order_id:
                    setattr(pos, "unprotected", False)
                    audit(trade_log,
                          f"Position {pos.symbol} now protected — clearing unprotected marker",
                          action="unprotected_cleared", result="PROTECTED",
                          data={"trade_id": trade_id, "symbol": pos.symbol,
                                "sl_id": pos.sl_order_id})

                # ── SAFEGUARD 2: Grace period after open ──
                # Skip local SL/TP monitoring for the first 90 seconds after a
                # position opens. This gives the exchange SL/TP orders time to be
                # placed and prevents instant stop-outs from stale price data.
                #
                # Audit F-4: the grace skip must apply ONLY to positions that
                # actually have an exchange stop in place. Emergency positions
                # (post-order-crash) and adopted orphans can be `open` with
                # sl_order_id=None and a fresh opened_at — for those, skipping
                # local monitoring left them with NO protection at all for the
                # full 90s on a leveraged perp. If no exchange SL exists after we
                # attempt placement below, we fall through to local monitoring.
                age_secs = (datetime.now(UTC) - pos.opened_at).total_seconds() if pos.opened_at else 999
                if age_secs < 90:
                    # ── SAFEGUARD 3: Wait for SL/TP confirmation ──
                    # During the grace period, still attempt to place SL/TP if missing,
                    # but don't run local SL/TP monitoring until orders are confirmed.
                    if (not pos.sl_order_id or not pos.tp_order_id) and pos.stop_loss > 0 and pos.take_profit > 0:
                        try:
                            direction = Direction.LONG if pos.direction == "LONG" else Direction.SHORT
                            sl_id, tp_id = await self._place_sl_tp(
                                exchange, pos.symbol, direction,
                                pos.quantity, pos.stop_loss, pos.take_profit
                            )
                            if sl_id and not pos.sl_order_id:
                                pos.sl_order_id = sl_id
                            if tp_id and not pos.tp_order_id:
                                pos.tp_order_id = tp_id
                            if sl_id or tp_id:
                                self._save_positions()
                                audit(trade_log,
                                      f"SL/TP placed during grace period: {pos.symbol}",
                                      action="sltp_grace", result="PLACED",
                                      data={"trade_id": trade_id, "sl_id": sl_id, "tp_id": tp_id,
                                            "age_secs": round(age_secs, 1)})
                        except Exception as exc:
                            logger.debug("SL/TP grace placement failed for %s: %s", pos.symbol, exc)
                    # Audit F-4: only skip local monitoring when an exchange stop
                    # is actually in place. A still-unprotected position (no
                    # sl_order_id) must be monitored locally NOW rather than left
                    # exposed for the rest of the grace window.
                    if pos.sl_order_id:
                        continue  # protected by exchange SL — skip local check
                    # Still unprotected within grace. Don't wait for the next scan
                    # tick (~10-60s of blind exposure on a leveraged perp): run a
                    # tight, bounded local sub-loop NOW to place the stop or close
                    # on breach (audit F-4 / roadmap risk-depth #1).
                    guard_msg = await self._guard_unprotected_grace(exchange, pos)
                    if guard_msg:
                        closed_messages.append(guard_msg)
                        continue  # sub-loop flattened it
                    if pos.sl_order_id:
                        continue  # sub-loop got the exchange stop on — protected
                    logger.warning(
                        "Position %s still has NO exchange stop at %.0fs into grace "
                        "— running local SL monitoring immediately (audit F-4)",
                        pos.symbol, age_secs)
                    audit(trade_log,
                          f"Unprotected position {pos.symbol} monitored locally during grace",
                          action="grace_unprotected_monitor", result="LOCAL_SL_ACTIVE",
                          data={"trade_id": trade_id, "age_secs": round(age_secs, 1)})

                price = float(tickers.get(pos.symbol, {}).get("last", 0))
                if price <= 0:
                    continue

                # ── Staleness guard ──
                # A frozen/old REST `last` must not drive a trailing tighten or a
                # local stop-out. If the ticker is stale, skip local monitoring for
                # this symbol this cycle — the exchange-side stop remains the
                # protection. (Pairs with the WS staleness guard; this covers the
                # REST path the WS guard does not.)
                if self._ticker_too_old(
                    tickers.get(pos.symbol), CONFIG.execution.live_ticker_max_age_sec, time.time()
                ):
                    _ts = (tickers.get(pos.symbol) or {}).get("timestamp")
                    _age = time.time() - float(_ts) / 1000.0 if _ts else -1.0
                    audit(trade_log,
                          f"Stale ticker for {pos.symbol} ({_age:.0f}s old) — skipping "
                          f"local SL/TP this cycle; exchange stop still active",
                          action="ticker_stale", result="SKIPPED", level=logging.WARNING,
                          data={"symbol": pos.symbol, "age_sec": round(_age, 1),
                                "max_age": CONFIG.execution.live_ticker_max_age_sec})
                    continue

                # ── Trailing stop update ──
                if CONFIG.trailing.enabled and pos.trailing_state is not None:
                    old_sl = pos.stop_loss
                    pos_strategy = getattr(pos, 'strategy_type', 'swing')
                    trail_mult = CONFIG.strategy_types.get_trailing_atr_mult(pos_strategy)
                    new_sl, trailing_active = update_trailing_stop(
                        pos.trailing_state, price, pos.stop_loss, pos.direction,
                        trail_atr_mult=trail_mult,
                        rule=CONFIG.trailing.trail_rule,
                        playbook_atr_mult=CONFIG.trailing.playbook_atr_mult,
                    )
                    # Structure ratchet (mirrors the backtest): once trailing
                    # is active, tighten behind the newest CONFIRMED 1h swing.
                    # Closed candles only, cached 5 min, fail-open.
                    if (CONFIG.trailing.structure_trail_enabled and trailing_active):
                        try:
                            _hl = await self._struct_candles(exchange, pos.symbol)
                            if _hl:
                                from bot.utils.trailing import structure_ratchet
                                _buf = (CONFIG.trailing.structure_trail_buffer_atr
                                        * float(pos.trailing_state.get("atr") or 0.0))
                                new_sl = structure_ratchet(
                                    _hl[0], _hl[1], pos.direction, new_sl, _buf)
                        except Exception as _st_exc:
                            logger.debug("structure ratchet skipped for %s: %s",
                                         pos.symbol, _st_exc)
                    if new_sl != old_sl:
                        # Check if the SL moved enough to update on exchange
                        sl_change_pct = abs(new_sl - old_sl) / old_sl * 100 if old_sl > 0 else 100
                        if sl_change_pct >= CONFIG.trailing.min_sl_update_pct:
                            # M-02 FIX: Only update local SL when exchange update also fires
                            # to prevent local/exchange SL drift
                            pos.stop_loss = new_sl
                            self._save_positions()
                            await self._update_exchange_sl(
                                exchange, pos, new_sl
                            )
                            audit(trade_log,
                                  f"Trailing SL updated: {pos.symbol} SL ${old_sl:.4f} -> ${new_sl:.4f}",
                                  action="trailing_sl", result="UPDATED",
                                  data={"trade_id": trade_id, "old_sl": old_sl,
                                        "new_sl": new_sl, "price": price,
                                        "trailing_active": trailing_active})

                # ── Partial take-profit ladder ──
                # Banks 50% at 1.5R (SL→breakeven), 30% at 2.5R (lock 1R), runner
                # rides the ratcheted stop. Additive overlay on the exchange SL/TP
                # (reduceOnly backstops clamp to the shrinking position). The
                # runner's exit is the existing static SL/TP check below.
                if CONFIG.partial_tp.enabled and pos.status == "open" and pos.quantity > 0:
                    try:
                        await self._run_partial_tp(exchange, pos, price)
                    except Exception as _ptp_exc:
                        logger.warning("Partial-TP check failed for %s: %s",
                                       pos.symbol, _ptp_exc)

                # ── Retry SL/TP placement if missing ──
                if (not pos.sl_order_id or not pos.tp_order_id) and pos.stop_loss > 0 and pos.take_profit > 0:
                    try:
                        direction = Direction.LONG if pos.direction == "LONG" else Direction.SHORT
                        sl_id, tp_id = await self._place_sl_tp(
                            exchange, pos.symbol, direction,
                            pos.quantity, pos.stop_loss, pos.take_profit
                        )
                        if sl_id or tp_id:
                            # AUDIT-FIX: Only update missing order IDs to avoid
                            # orphaning existing exchange orders
                            if sl_id and not pos.sl_order_id:
                                pos.sl_order_id = sl_id
                            if tp_id and not pos.tp_order_id:
                                pos.tp_order_id = tp_id
                            self._save_positions()
                            audit(trade_log,
                                  f"SL/TP retry succeeded: {pos.symbol} SL={pos.stop_loss:.4f} TP={pos.take_profit:.4f}",
                                  action="sltp_retry", result="PLACED",
                                  data={"trade_id": trade_id, "sl_id": sl_id, "tp_id": tp_id})
                    except Exception as exc:
                        logger.debug("SL/TP retry failed for %s: %s", pos.symbol, exc)

                # ── Escalate a persistently-unprotected position ──
                # If the per-tick retry above STILL could not place an exchange
                # stop, the position is live with no venue-side protection (it is
                # only price-monitored locally by the static check below). The
                # operator was alerted once at adoption and then went silent —
                # re-alert on a throttle until the stop lands. Alert only; an
                # adopted position is never force-closed (it may be intentional).
                if (CONFIG.execution.unprotected_escalation_enabled
                        and not pos.sl_order_id and pos.stop_loss > 0):
                    _now_ts = time.time()
                    _last_alert = getattr(pos, "_unprotected_alert_at", 0.0) or 0.0
                    if _now_ts - _last_alert >= CONFIG.execution.unprotected_alert_interval_s:
                        setattr(pos, "_unprotected_alert_at", _now_ts)
                        setattr(pos, "unprotected", True)
                        logger.critical(
                            "UNPROTECTED POSITION (%s %s): no exchange stop-loss after "
                            "retry — live with NO venue stop (price-monitored locally). "
                            "Place a stop on Bitget manually.", pos.symbol, pos.direction)
                        audit(trade_log,
                              f"UNPROTECTED position {pos.symbol} still has no exchange stop "
                              f"after retry — operator re-alerted",
                              action="unprotected_escalation", result="UNPROTECTED",
                              data={"trade_id": trade_id, "symbol": pos.symbol,
                                    "stop_loss": pos.stop_loss, "price": price})
                        self._record_warning("unprotected_persist")
                        _why = self._last_sltp_reason(pos.symbol)
                        _why_line = f"\nVenue reason: <code>{_why}</code>" if _why else ""
                        closed_messages.append(
                            f"🚨 <b>UNPROTECTED POSITION — {pos.symbol} {pos.direction}</b>\n"
                            f"No exchange stop-loss could be placed (still retrying each "
                            f"scan; price-monitored locally as a backstop).{_why_line}\n"
                            f"Stop level: <code>${pos.stop_loss:.4f}</code> — place a stop "
                            f"on Bitget manually."
                        )

                # ── GETCLAW: Time-stop check (Rules 6/17) ──
                # Uses per-strategy-type thresholds from StrategyTypeConfig
                if CONFIG.time_stop.enabled:
                    hold_hours = (datetime.now(UTC) - pos.opened_at).total_seconds() / 3600
                    # Get strategy-type-aware thresholds
                    pos_strategy = getattr(pos, 'strategy_type', 'intraday')
                    close_threshold = CONFIG.strategy_types.get_time_close_hours(pos_strategy)
                    warn_threshold = CONFIG.strategy_types.get_time_warn_hours(pos_strategy)
                    if hold_hours >= close_threshold:
                        # Check if position is in profit
                        if pos.direction == "LONG":
                            in_profit = price > pos.entry_price
                        else:
                            in_profit = price < pos.entry_price
                        if not in_profit:
                            # Time-stop: no profit after threshold → close
                            msg = await self.close_position(
                                trade_id, f"TIME_STOP ({hold_hours:.1f}h/{close_threshold:.0f}h max, {pos_strategy}, no profit)", price)
                            closed_messages.append(msg)
                            audit(trade_log,
                                  f"Time-stop triggered: {pos.symbol} held {hold_hours:.1f}h ({pos_strategy} max={close_threshold:.0f}h) with no profit",
                                  action="time_stop", result="CLOSED",
                                  data={"trade_id": trade_id, "hold_hours": hold_hours,
                                        "strategy_type": pos_strategy,
                                        "close_threshold": close_threshold,
                                        "entry": pos.entry_price, "current": price})
                            continue  # Skip SL/TP check — already closing
                    elif hold_hours >= warn_threshold:
                        # Approaching time-stop — log warning (once per cycle is fine)
                        remaining = close_threshold - hold_hours
                        logger.debug("Time-stop warning: %s (%s) held %.1fh, %.1fh until auto-close",
                                     pos.symbol, pos_strategy, hold_hours, remaining)

                # ── Static SL/TP check ──
                should_close = False
                reason = ""

                if pos.direction == "LONG":
                    if price <= pos.stop_loss:
                        should_close = True
                        reason = "TRAILING SL HIT" if (pos.trailing_state and pos.trailing_state.get("trailing_active")) else "SL HIT"
                    elif price >= pos.take_profit:
                        should_close = True
                        reason = "TP HIT"
                else:  # SHORT
                    if price >= pos.stop_loss:
                        should_close = True
                        reason = "TRAILING SL HIT" if (pos.trailing_state and pos.trailing_state.get("trailing_active")) else "SL HIT"
                    elif price <= pos.take_profit:
                        should_close = True
                        reason = "TP HIT"

                if should_close:
                    # Close manually if no exchange SL/TP, or if SL/TP exists but
                    # price has blown through the level (exchange SL/TP may have
                    # been cancelled or failed).
                    msg = await self.close_position(trade_id, reason, price)
                    closed_messages.append(msg)

        except Exception as exc:
            logger.warning("Position check error: %s", exc)

        # ── Periodic exchange sync ──
        # Every 5 minutes, check if the exchange has positions we're not tracking.
        # This is the definitive fix for "lost positions" — the exchange is always
        # the source of truth.
        now_ts = time.time()
        if now_ts - self._last_exchange_sync > self._EXCHANGE_SYNC_INTERVAL:
            self._last_exchange_sync = now_ts
            try:
                adopted = await self.adopt_exchange_positions()
                for sym in adopted:
                    audit(trade_log, f"Periodic sync adopted orphan: {sym}",
                          action="periodic_sync", result="ADOPTED")
                    closed_messages.append(
                        f"SYNC: Adopted untracked position {sym} from exchange"
                    )
                # Also adopt orphaned limit orders
                adopted_orders = await self.adopt_exchange_limit_orders()
                for sym in adopted_orders:
                    audit(trade_log, f"Periodic sync adopted orphan limit order: {sym}",
                          action="periodic_sync", result="ADOPTED_LIMIT")
                    closed_messages.append(
                        f"SYNC: Adopted untracked limit order {sym} from exchange"
                    )
            except Exception as sync_exc:
                logger.debug("Periodic exchange sync failed: %s", sync_exc)

        return closed_messages

    async def _reattempt_post_fill_sl(
        self, exchange: "ccxt.Exchange", pos: "LivePosition",
        direction, qty: float, sl_id, tp_id, trade_id: Optional[str] = None,
    ):
        """Post-fill stop-loss-failure handling for the limit-fill and
        drift-market-fallback entry paths.

        The synchronous market entry path enforces RC-AUD-001: if the stop-loss
        cannot be placed, retry once and FLATTEN. These two post-fill paths
        previously did neither — the limit path warned only when BOTH legs failed
        and the drift path was silent — so an SL-only failure left a live,
        leveraged position with no exchange stop and no operator alert until a
        later ``check_positions`` tick noticed.

        This mirrors the market path's *retry + unprotected-marker + alert* so the
        grace/escalation machinery engages immediately. It deliberately does NOT
        flatten here: unlike the synchronous market path, these run inside the
        monitoring context where the bounded grace sub-loop (audit F-4) is the
        designed remediation (re-protect within grace, flatten on breach).

        Byte-identical when the stop-loss placed (the common case): it returns the
        ids unchanged and takes no action. Returns the resolved ``(sl_id, tp_id)``.
        """
        if sl_id is None and pos.stop_loss > 0:
            audit(trade_log,
                  f"SL placement failed post-fill for {pos.symbol} — retrying once",
                  action="sl_retry", result="RETRY",
                  data={"trade_id": trade_id, "symbol": pos.symbol})
            try:
                retry_sl, retry_tp = await self._place_sl_tp(
                    exchange, pos.symbol, direction, qty,
                    pos.stop_loss, pos.take_profit)
                if retry_sl:
                    sl_id = retry_sl
                if tp_id is None and retry_tp:
                    tp_id = retry_tp
            except Exception as exc:
                logger.warning("Post-fill SL retry raised for %s: %s", pos.symbol, exc)
        if sl_id is None and pos.stop_loss > 0:
            # A stop was intended but didn't place. Flag so the unprotected
            # escalation/grace machinery in check_positions treats this position
            # as unprotected immediately. (sl=0 means no stop was intended, so it
            # is not flagged — matching how the rest of the executor treats it.)
            setattr(pos, "unprotected", True)
            audit(trade_log,
                  f"UNPROTECTED position {pos.symbol}: stop-loss not placed post-fill "
                  f"— flagged for grace re-protection / escalation",
                  action="sl_tp_failed", result="UNPROTECTED",
                  data={"trade_id": trade_id, "symbol": pos.symbol,
                        "stop_loss": getattr(pos, "stop_loss", None)})
        return sl_id, tp_id

    async def _check_pending_limit(self, exchange: "ccxt.Exchange",
                                    trade_id: str, pos: LivePosition) -> Optional[str]:
        """Check if a pending limit order has been filled or should be cancelled.

        Returns a message string if status changed, else None.
        """
        if not pos.limit_order_id:
            return None

        # ── HARD TIMEOUT: stale pending_fill safety net ──
        # If a pending_fill position has been stuck for 2x the normal expiry
        # (e.g. 8 hours by default), force-close it regardless of exchange
        # state.  This prevents positions from being stuck forever when
        # fetch_order keeps failing or the exchange silently cancelled the
        # order.
        hard_timeout = 2 * CONFIG.limit_orders.expire_seconds
        stale_age = (datetime.now(UTC) - pos.opened_at).total_seconds() if pos.opened_at else 0
        if stale_age > hard_timeout:
            # Best-effort cancel on exchange
            try:
                await exchange.cancel_order(pos.limit_order_id, pos.symbol)
            except Exception as cancel_exc:
                logger.warning(
                    "Stale pending hard-timeout: cancel attempt failed for %s order %s: %s",
                    pos.symbol, pos.limit_order_id, cancel_exc,
                )

            pos.status = "closed"
            pos.closed_at = datetime.now(UTC)
            pos.pnl_usd = 0.0
            pos.close_reason = "stale_pending"
            self._save_positions()
            self._append_closed_trade(pos)

            audit(
                trade_log,
                f"Stale pending_fill FORCE-CLOSED after {stale_age / 3600:.1f}h: {pos.symbol}",
                action="stale_pending_close",
                result="FORCE_CLOSED",
                data={
                    "trade_id": trade_id,
                    "age_sec": stale_age,
                    "hard_timeout_sec": hard_timeout,
                    "limit_order_id": pos.limit_order_id,
                },
            )

            return (
                f"STALE PENDING CLOSED: {pos.direction} {pos.symbol} — "
                f"stuck for {stale_age / 3600:.1f}h (hard timeout {hard_timeout / 3600:.1f}h)"
            )

        try:
            order = await exchange.fetch_order(pos.limit_order_id, pos.symbol)
            order_status = order.get("status", "unknown")

            if order_status in ("closed", "filled", "partially_filled"):
                # Limit order filled (or partially filled) — transition to open position
                fill_price = float(order.get("average", 0) or order.get("price", 0) or pos.entry_price)
                filled_qty = float(order.get("filled", 0) or pos.quantity)

                # ── Duplicate-fill guard (live incident: UNI double "TRADE
                # OPENED"). The adoption sweep can mint a SECOND pending_fill
                # record for an order the bot already tracks (order-id echo
                # drift / clientOid loss / reprice moving the price off the
                # exact-match combo). Both records then transition on the SAME
                # exchange fill: two fill notifications, one of them racing SL
                # placement (the plan-order cleanup cancels the other's stop),
                # and later two close bookings. If another record already went
                # OPEN on this symbol+direction at this entry (±0.05%), this
                # record is a duplicate tracker of the same fill — retire it
                # quietly instead of double-tracking. Same signature + trade-off
                # as _is_duplicate_close_booking (a real second fill at the
                # identical price within the same sweep is near-impossible).
                if self._is_duplicate_fill(pos, fill_price):
                    pos.status = "closed"
                    pos.closed_at = datetime.now(UTC)
                    pos.pnl_usd = 0.0
                    pos.close_reason = "duplicate_fill_suppressed"
                    pos.limit_order_id = None
                    self._save_positions()
                    audit(trade_log,
                          f"Duplicate fill suppressed: {pos.symbol} {pos.direction} "
                          f"@ ${fill_price:,.4f} — another record already tracks this fill",
                          action="duplicate_fill_guard", result="SUPPRESSED",
                          data={"trade_id": trade_id, "symbol": pos.symbol,
                                "fill_price": fill_price})
                    return None

                # GETCLAW: partially_filled = some qty matched, rest still open.
                # Use actual filled qty, not original order size.
                if order_status == "partially_filled" and filled_qty > 0:
                    audit(trade_log,
                          f"Limit PARTIAL FILL: {pos.symbol} filled {filled_qty} of {pos.quantity}",
                          action="partial_fill", result="PARTIAL",
                          data={"trade_id": trade_id, "filled": filled_qty,
                                "original": pos.quantity})

                pos.entry_price = fill_price
                pos.quantity = filled_qty
                pos.status = "open"
                pos.order_type = "limit"  # GETCLAW: limit fill = maker fee rate

                # M-01 FIX: Cancel remaining unfilled quantity to prevent untracked fills
                if order_status == "partially_filled" and pos.limit_order_id:
                    try:
                        await exchange.cancel_order(pos.limit_order_id, pos.symbol)
                        audit(trade_log, f"Cancelled remaining limit order after partial fill: {pos.symbol}",
                              action="partial_cancel", result="OK")
                    except Exception as cancel_exc:
                        logger.warning("Failed to cancel remaining limit after partial fill %s: %s", pos.symbol, cancel_exc)

                pos.limit_order_id = None

                # Recalculate cost
                raw_cost = fill_price * filled_qty
                if pos.leverage > 1:
                    pos.cost_usd = raw_cost / pos.leverage
                else:
                    pos.cost_usd = raw_cost

                # Initialize trailing state now that we have a real fill
                if CONFIG.trailing.enabled and pos.atr_at_entry > 0:
                    initial_risk = abs(fill_price - pos.stop_loss)
                    pos.trailing_state = make_trailing_state(
                        entry_price=fill_price,
                        direction=pos.direction,
                        initial_risk=initial_risk,
                        atr_value=pos.atr_at_entry,
                    )

                # Place SL/TP now that position is filled
                direction = Direction.LONG if pos.direction == "LONG" else Direction.SHORT
                sl_id, tp_id = await self._place_sl_tp(
                    exchange, pos.symbol, direction,
                    filled_qty, pos.stop_loss, pos.take_profit
                )
                # RC-AUD-001 parity: retry-once + mark-unprotected on SL failure
                # (the bare market path flattens; here the grace sub-loop remediates).
                sl_id, tp_id = await self._reattempt_post_fill_sl(
                    exchange, pos, direction, filled_qty, sl_id, tp_id, trade_id)
                pos.sl_order_id = sl_id
                pos.tp_order_id = tp_id

                self._save_positions()

                if sl_id is None and tp_id is None:
                    audit(trade_log,
                          f"SL/TP placement FAILED for {pos.symbol} — position is UNPROTECTED",
                          action="sl_tp_failed",
                          data={"trade_id": trade_id, "symbol": pos.symbol,
                                "stop_loss": pos.stop_loss, "take_profit": pos.take_profit})

                audit(trade_log, f"Limit order FILLED: {pos.symbol} @ ${fill_price:,.4f}",
                      action="limit_fill", result="FILLED",
                      data={"trade_id": trade_id, "fill_price": fill_price,
                            "quantity": filled_qty})

                # Show the SL/TP PRICE levels, not the raw exchange order ids.
                # (Regression: this printed sl_id/tp_id — 19-digit order ids —
                # where the operator expects the price, e.g. "SL: 0.32808".)
                protection = self._fmt_fill_protection(
                    pos.stop_loss, pos.take_profit, sl_id, tp_id, pos.trailing_state)
                st_label = getattr(pos, 'strategy_type', 'swing').upper()
                sl_tp_warn = ""
                if sl_id is None:
                    # No exchange stop-loss is the safety-critical case (a missing
                    # TP alone is not). Surface it to the operator regardless of TP.
                    sl_tp_warn = "\n⚠️ STOP-LOSS not placed — position unprotected (monitoring active)!"
                return (
                    f"LIMIT FILLED: {pos.direction} {pos.symbol} [{st_label}]\n"
                    f"Fill: ${fill_price:,.4f} | Qty: {filled_qty:.6f}{protection}{sl_tp_warn}"
                )

            elif order_status in ("canceled", "cancelled", "rejected", "expired"):
                # Limit order cancelled/rejected — remove position
                pos.status = "closed"
                pos.closed_at = datetime.now(UTC)
                pos.pnl_usd = 0.0
                pos.close_reason = order_status
                self._save_positions()
                # C2-14 FIX: Write to closed_trades.json so cancelled/rejected
                # limit orders are visible in trade history, not silently dropped.
                self._append_closed_trade(pos)

                audit(trade_log, f"Limit order {order_status}: {pos.symbol}",
                      action="limit_cancel", result=order_status.upper(),
                      data={"trade_id": trade_id})

                return f"LIMIT {order_status.upper()}: {pos.direction} {pos.symbol} — order not filled"

            else:
                # Still open — check price drift and time expiry
                age_sec = (datetime.now(UTC) - pos.opened_at).total_seconds()
                cancel_reason = None

                # ── PRICE DRIFT CANCEL (from Getclaw) ──
                # If price has moved >X% away from the limit, the setup is stale.
                # No point waiting for a fill that's unlikely to come.
                # MARKET FALLBACK: if drift is detected but momentum is strong
                # and in the trade's direction, convert to market order instead
                # of cancelling (catches momentum breakouts that moved past limit).
                drift_pct = CONFIG.limit_orders.price_drift_cancel_pct
                if drift_pct > 0 and pos.entry_price > 0:
                    try:
                        ticker = await exchange.fetch_ticker(pos.symbol)
                        cur_price = float(ticker.get("last", 0) or 0)
                        if cur_price > 0:
                            pct_away = abs(cur_price - pos.entry_price) / pos.entry_price * 100
                            if pct_away > drift_pct:
                                # Check if we should convert to market instead of cancelling
                                should_market_fallback = False
                                if CONFIG.limit_orders.drift_market_fallback:
                                    should_market_fallback = await self._check_drift_market_fallback(
                                        exchange, pos, cur_price)

                                if should_market_fallback:
                                    # Convert to market order
                                    audit(trade_log,
                                          f"Limit drift → MARKET FALLBACK: {pos.symbol} "
                                          f"drifted {pct_away:.1f}% but momentum is strong and aligned",
                                          action="limit_drift_market_fallback", result="CONVERTING",
                                          data={"trade_id": trade_id, "pct_away": pct_away,
                                                "limit_price": pos.entry_price,
                                                "market_price": cur_price})
                                    fallback_msg = await self._execute_drift_market_fallback(
                                        exchange, trade_id, pos, cur_price)
                                    if fallback_msg:
                                        return fallback_msg
                                    # If fallback failed, fall through to normal cancel
                                    cancel_reason = "price_drift"
                                else:
                                    cancel_reason = "price_drift"
                                    audit(trade_log,
                                          f"Price drifted {pct_away:.1f}% from limit "
                                          f"(threshold {drift_pct}%): {pos.symbol} "
                                          f"limit=${pos.entry_price:,.4f} mkt=${cur_price:,.4f}",
                                          action="limit_drift_cancel", result="CANCELLING",
                                          data={"trade_id": trade_id, "pct_away": pct_away,
                                                "limit_price": pos.entry_price,
                                                "market_price": cur_price})
                    except Exception as drift_exc:
                        logger.debug("Price drift check failed for %s: %s",
                                     pos.symbol, drift_exc)

                # ── TIME EXPIRY ──
                if not cancel_reason and age_sec > CONFIG.limit_orders.expire_seconds:
                    cancel_reason = "expired"

                if cancel_reason:
                    # Cancel the limit order
                    cancel_confirmed = False
                    try:
                        await exchange.cancel_order(pos.limit_order_id, pos.symbol)
                        cancel_confirmed = True
                    except Exception as exc:
                        logger.warning("Failed to cancel %s limit order %s: %s",
                                       cancel_reason, pos.limit_order_id, exc)

                    # C2-16 FIX: Verify cancel before marking closed — if cancel
                    # failed, the order may have filled in the meantime.
                    if not cancel_confirmed:
                        try:
                            order_info = await exchange.fetch_order(pos.limit_order_id, pos.symbol)
                            actual_status = order_info.get("status", "")
                            if actual_status in ("filled", "closed"):
                                logger.warning("Limit order %s filled during cancel attempt", pos.limit_order_id)
                                return None  # next check cycle will process the fill
                            elif actual_status not in ("canceled", "cancelled", "expired"):
                                logger.warning("Limit order %s still %s after cancel attempt",
                                               pos.limit_order_id, actual_status)
                                return None
                        except Exception as verify_exc:
                            logger.warning("Could not verify limit order status: %s", verify_exc)
                            # Cannot confirm cancel and cannot verify — leave as pending_fill for retry
                            return None

                    if not cancel_confirmed:
                        logger.warning("Cancel NOT confirmed for %s order %s — leaving as pending_fill for retry",
                                       cancel_reason, pos.limit_order_id)
                        return None

                    pos.status = "closed"
                    pos.closed_at = datetime.now(UTC)
                    pos.pnl_usd = 0.0
                    pos.close_reason = cancel_reason
                    self._save_positions()
                    self._append_closed_trade(pos)

                    if cancel_reason == "price_drift":
                        audit(trade_log, f"Limit order CANCELLED (price drift): {pos.symbol}",
                              action="limit_drift_cancel", result="CANCELLED",
                              data={"trade_id": trade_id, "age_sec": age_sec})
                        return f"LIMIT CANCELLED (price drift): {pos.direction} {pos.symbol} — market moved away"
                    else:
                        audit(trade_log, f"Limit order EXPIRED after {age_sec:.0f}s: {pos.symbol}",
                              action="limit_expire", result="EXPIRED",
                              data={"trade_id": trade_id, "age_sec": age_sec})
                        return f"LIMIT EXPIRED: {pos.direction} {pos.symbol} — cancelled after {age_sec/3600:.1f}h"

        except Exception as exc:
            logger.warning("Pending limit check failed for %s: %s", trade_id, exc)

        return None

    async def _check_drift_market_fallback(
        self, exchange: "ccxt.Exchange", pos: "LivePosition", cur_price: float,
    ) -> bool:
        """Check if price drift should trigger a market order fallback.

        Returns True if:
          1. Momentum is strong (ADX > threshold)
          2. Price moved in the TRADE's direction (not against it)
             - LONG: price drifted UP past limit (breakout above our buy)
             - SHORT: price drifted DOWN past limit (breakdown below our sell)
        """
        try:
            min_adx = CONFIG.limit_orders.drift_market_min_adx

            # Check direction alignment: only fallback if price moved
            # favorably (we're chasing a breakout, not averaging into a loser)
            if pos.direction == "LONG":
                # Price moved UP past our buy limit → breakout
                if cur_price <= pos.entry_price:
                    return False  # price is below limit, that's normal for a buy
            else:
                # SHORT: price moved DOWN past our sell limit → breakdown
                if cur_price >= pos.entry_price:
                    return False

            # Fetch recent candles for ADX (closed bars only — repaint guard)
            ohlcv = await exchange.fetch_ohlcv(pos.symbol, "15m", limit=30)
            from bot.utils.candles import drop_forming_candle
            ohlcv = drop_forming_candle(ohlcv, "15m")
            if not ohlcv or len(ohlcv) < 14:
                return False

            import numpy as np
            closes = np.array([c[4] for c in ohlcv])
            highs = np.array([c[2] for c in ohlcv])
            lows = np.array([c[3] for c in ohlcv])

            # Simple ADX calculation
            period = 14
            tr = np.maximum(highs[1:] - lows[1:],
                            np.maximum(np.abs(highs[1:] - closes[:-1]),
                                       np.abs(lows[1:] - closes[:-1])))
            plus_dm = np.maximum(highs[1:] - highs[:-1], 0)
            minus_dm = np.maximum(lows[:-1] - lows[1:], 0)

            # Zero out when other DM is larger
            mask = plus_dm > minus_dm
            minus_dm[mask & (plus_dm > minus_dm)] = 0
            plus_dm[~mask & (minus_dm > plus_dm)] = 0

            # Smoothed averages (simple rolling for efficiency)
            if len(tr) < period:
                return False
            atr_vals = np.convolve(tr, np.ones(period)/period, mode='valid')
            plus_di = np.convolve(plus_dm, np.ones(period)/period, mode='valid')
            minus_di = np.convolve(minus_dm, np.ones(period)/period, mode='valid')

            if len(atr_vals) == 0 or atr_vals[-1] == 0:
                return False

            plus_di_pct = (plus_di[-1] / atr_vals[-1]) * 100
            minus_di_pct = (minus_di[-1] / atr_vals[-1]) * 100

            dx = abs(plus_di_pct - minus_di_pct) / max(plus_di_pct + minus_di_pct, 1e-10) * 100
            # Use current DX as ADX proxy (simplified)
            adx_value = dx

            # Direction alignment check via DI
            if pos.direction == "LONG" and plus_di_pct <= minus_di_pct:
                return False  # momentum is bearish, don't chase
            elif pos.direction == "SHORT" and minus_di_pct <= plus_di_pct:
                return False  # momentum is bullish, don't chase

            if adx_value >= min_adx:
                audit(trade_log,
                      f"Drift market fallback: ADX={adx_value:.1f} >= {min_adx}, "
                      f"+DI={plus_di_pct:.1f}, -DI={minus_di_pct:.1f} → converting {pos.symbol}",
                      action="drift_market_check", result="ELIGIBLE",
                      data={"adx": round(adx_value, 1), "plus_di": round(plus_di_pct, 1),
                            "minus_di": round(minus_di_pct, 1)})
                return True

            return False
        except Exception as exc:
            logger.debug("Drift market fallback check failed for %s: %s", pos.symbol, exc)
            return False

    async def _execute_drift_market_fallback(
        self, exchange: "ccxt.Exchange", trade_id: str,
        pos: "LivePosition", cur_price: float,
    ) -> Optional[str]:
        """Cancel the pending limit and place a market order at current price.

        Updates the position entry price, recalculates cost, places SL/TP.
        Returns a status message or None if failed.
        """
        try:
            # 1. Cancel the existing limit order
            try:
                await exchange.cancel_order(pos.limit_order_id, pos.symbol)
            except Exception as cancel_exc:
                # Check if it filled during cancellation
                try:
                    check = await exchange.fetch_order(pos.limit_order_id, pos.symbol)
                    if check.get("status") in ("filled", "closed"):
                        return None  # filled — next cycle will handle
                except Exception as _check_exc:
                    logger.warning("Order fill check during cancel failed for %s: %s",
                                   pos.symbol, _check_exc)
                logger.warning("Market fallback: cancel failed for %s: %s",
                               pos.symbol, cancel_exc)
                return None

            # 2. Place market order
            side = "buy" if pos.direction == "LONG" else "sell"
            qty = pos.quantity
            if qty <= 0:
                return None

            order = await exchange.create_order(
                pos.symbol, "market", side, qty,
                params={"productType": "USDT-FUTURES"})

            fill_price = float(order.get("average", 0) or order.get("price", 0) or cur_price)
            filled_qty = float(order.get("filled", 0) or qty)

            # 3. Update position
            old_entry = pos.entry_price
            pos.entry_price = fill_price
            pos.quantity = filled_qty
            pos.status = "open"
            pos.order_type = "market"
            pos.limit_order_id = None

            # Recalculate cost
            raw_cost = fill_price * filled_qty
            pos.cost_usd = raw_cost / pos.leverage if pos.leverage > 1 else raw_cost

            # Recalculate SL/TP relative to new entry, maintaining the same distances
            if pos.stop_loss and old_entry > 0:
                sl_dist_pct = abs(old_entry - pos.stop_loss) / old_entry
                if pos.direction == "LONG":
                    pos.stop_loss = round(fill_price * (1 - sl_dist_pct), 8)
                else:
                    pos.stop_loss = round(fill_price * (1 + sl_dist_pct), 8)

            if pos.take_profit and old_entry > 0:
                tp_dist_pct = abs(pos.take_profit - old_entry) / old_entry
                if pos.direction == "LONG":
                    pos.take_profit = round(fill_price * (1 + tp_dist_pct), 8)
                else:
                    pos.take_profit = round(fill_price * (1 - tp_dist_pct), 8)

            # Initialize trailing state
            if CONFIG.trailing.enabled and pos.atr_at_entry > 0:
                from bot.utils.trailing import make_trailing_state
                initial_risk = abs(fill_price - pos.stop_loss) if pos.stop_loss else pos.atr_at_entry
                pos.trailing_state = make_trailing_state(
                    entry_price=fill_price,
                    direction=pos.direction,
                    initial_risk=initial_risk,
                    atr_value=pos.atr_at_entry,
                )

            # Place SL/TP on exchange
            from bot.utils.models import Direction
            direction = Direction.LONG if pos.direction == "LONG" else Direction.SHORT
            sl_id, tp_id = await self._place_sl_tp(
                exchange, pos.symbol, direction,
                filled_qty, pos.stop_loss, pos.take_profit)
            # RC-AUD-001 parity: a drift→market fallback is a real market entry,
            # so apply the same retry-once + mark-unprotected on SL failure as the
            # primary market path (previously this path had no SL-failure handling).
            sl_id, tp_id = await self._reattempt_post_fill_sl(
                exchange, pos, direction, filled_qty, sl_id, tp_id, trade_id)
            pos.sl_order_id = sl_id
            pos.tp_order_id = tp_id

            self._save_positions()

            audit(trade_log,
                  f"Limit → Market FALLBACK executed: {pos.symbol} @ ${fill_price:,.4f} "
                  f"(was limit @ ${old_entry:,.4f})",
                  action="limit_drift_market_fallback", result="EXECUTED",
                  data={"trade_id": trade_id, "old_entry": old_entry,
                        "fill_price": fill_price, "quantity": filled_qty,
                        "sl": pos.stop_loss, "tp": pos.take_profit})

            # Show SL/TP PRICES (not the exchange order IDs) — the order IDs are
            # meaningless to the operator and were rendering as huge integers.
            sl_info = f" | SL: ${pos.stop_loss:,.4f}" if pos.stop_loss else ""
            tp_info = f" | TP: ${pos.take_profit:,.4f}" if pos.take_profit else ""
            return (
                f"LIMIT → MARKET FALLBACK: {pos.direction} {pos.symbol}\n"
                f"Original limit: ${old_entry:,.4f} → Market fill: ${fill_price:,.4f}\n"
                f"Qty: {filled_qty:.6f}{sl_info}{tp_info}\n"
                f"Reason: momentum breakout past limit price"
            )
        except Exception as exc:
            audit(trade_log,
                  f"Market fallback execution failed for {pos.symbol}: {exc}",
                  action="limit_drift_market_fallback", result="ERROR",
                  data={"trade_id": trade_id, "error": str(exc)})
            return None

    async def _struct_candles(self, exchange, symbol: str):
        """Last ~40 CLOSED 1h bar (highs, lows) for the structure ratchet,
        cached 5 minutes per symbol. Returns (highs, lows) or None."""
        cache = getattr(self, "_struct_cache", None)
        if cache is None:
            cache = {}
            self._struct_cache = cache
        now = time.time()
        hit = cache.get(symbol)
        if hit and now - hit[0] < 300:
            return hit[1]
        try:
            ohlcv = await exchange.fetch_ohlcv(symbol, "1h", limit=45)
            from bot.utils.candles import drop_forming_candle
            ohlcv = drop_forming_candle(ohlcv, "1h")
            if not ohlcv or len(ohlcv) < 8:
                return None
            hl = ([float(c[2]) for c in ohlcv[-40:]],
                  [float(c[3]) for c in ohlcv[-40:]])
            cache[symbol] = (now, hl)
            if len(cache) > 50:
                cache.pop(next(iter(cache)))
            return hl
        except Exception:
            return None

    async def _update_exchange_sl(self, exchange: "ccxt.Exchange",
                                   pos: LivePosition, new_sl: float) -> None:
        """Place new SL order first, then cancel old one — no protection gap.

        C2-03 FIX: Previous logic cancelled old SL before placing new one,
        leaving the position unprotected if the new placement failed.
        Now: place new SL first, then cancel old. If new placement fails,
        old SL remains active.  Best-effort: trailing stop still works
        locally even if exchange update fails — check_positions() will
        close at the new SL.
        """
        # Futures-only mode: all positions are futures
        old_sl_id = pos.sl_order_id
        direction = Direction.LONG if pos.direction == "LONG" else Direction.SHORT
        # Resolve UTA mode BEFORE choosing the path. If it isn't cached yet, probe
        # (mirrors _place_sl_tp) — never default to the classic ccxt triggerPrice
        # path on an unresolved account: on a UTA account that path executes as an
        # IMMEDIATE market order and flat-closes the position on a trailing update.
        use_v3 = self._is_uta if self._is_uta is not None else False
        if self._is_uta is None:
            try:
                await exchange.privateMixGetV2MixAccountAccount(
                    {"symbol": "BTCUSDT", "productType": "USDT-FUTURES"})
                self._is_uta = False  # v2 account call worked -> classic account
            except Exception as exc:
                if "40085" in str(exc):
                    use_v3 = True
                    self._is_uta = True
                else:
                    self._is_uta = False

        # Step 1: Place new SL at tightened level FIRST
        new_sl_id = None
        if use_v3:
            # Round to tick grid
            swap_symbol = pos.symbol if ":USDT" in pos.symbol else f"{pos.symbol}:USDT"
            sl_rounded = self._round_price_to_market(exchange, swap_symbol, new_sl)
            if sl_rounded is None:
                sl_rounded = self._round_price_to_market(exchange, pos.symbol, new_sl)

            sl_id, _ = await self._place_sl_tp_v3(
                pos.symbol, direction, pos.quantity,
                new_sl, pos.take_profit,
                sl_str=sl_rounded,
            )
            if sl_id:
                new_sl_id = sl_id
        else:
            # Classic mode: place trigger order
            close_side = "sell" if direction == Direction.LONG else "buy"
            # Snap the trigger to the market tick grid — an unrounded triggerPrice
            # is rejected by Bitget (45115) and the SL update silently fails,
            # leaving the LOOSER old stop in place (audit: classic path missed the
            # rounding the v3 path already does).
            _sl_r = self._round_price_to_market(exchange, pos.symbol, new_sl)
            sl_trigger = float(_sl_r) if _sl_r else new_sl
            # Always send tradeSide=close + reduceOnly for SL/TP to prevent reverse opens
            extra_params = {"productType": "USDT-FUTURES", "tradeSide": "close", "reduceOnly": True}
            try:
                sl_order = await exchange.create_order(
                    symbol=pos.symbol, type="market", side=close_side,
                    amount=pos.quantity,
                    params={"triggerPrice": sl_trigger, "triggerType": "last", **extra_params},
                )
                new_sl_id = sl_order.get("id")
            except Exception as exc:
                logger.warning("Failed to place new exchange SL for %s: %s", pos.symbol, exc)

        # Step 2: Only cancel old SL AFTER new one is confirmed placed
        if new_sl_id:
            pos.sl_order_id = new_sl_id
            self._save_positions()
            if old_sl_id:
                try:
                    await exchange.cancel_order(old_sl_id, pos.symbol)
                except Exception as exc:
                    logger.debug("Cancel old SL order %s failed (new SL active): %s", old_sl_id, exc)
        else:
            # New placement failed — old SL remains active, no gap
            logger.warning("Trailing SL update skipped for %s — new placement failed, old SL preserved", pos.symbol)

    async def close_all_positions(self, reason: str = "emergency") -> list[str]:
        """Emergency close ALL open positions in a single sweep.

        GETCLAW: Uses per-position close with tradeSide=close + reduceOnly
        for safety. Returns list of result messages.
        """
        results = []
        open_pos = [p for p in self._positions.values()
                    if p.status in ("open", "pending_fill")]

        if not open_pos:
            return ["No open positions to close."]

        for pos in open_pos:
            try:
                result = await self.close_position(pos.trade_id, reason=reason)
                results.append(result)
            except Exception as exc:
                results.append(f"Failed to close {pos.symbol}: {exc}")

        audit(trade_log,
              f"Emergency close all: {len(results)} positions processed",
              action="close_all", result="DONE",
              data={"count": len(results), "reason": reason})

        return results

    @staticmethod
    def _reconcile_exchange_close_pnl(
        exchange_pnl: float, exchange_close_fees: float, pnl_is_net: bool,
        entry_notional: float, entry_fee_pct: float,
    ) -> tuple[float, float, float]:
        """Reconstruct (gross_pnl, net_pnl, commission) from an exchange-
        reported close, honoring whether exchange_pnl is already fee-adjusted.

        Bitget's position-history endpoint exposes achievedProfits (gross)
        and netProfit (fee-adjusted) as SEPARATE fields; the per-fill
        "profit" value used by the fetch_my_trades fallback paths follows
        the same "achieved profit" convention and is gross too. Treating
        every exchange_pnl as already-net (the old behavior) double-counted
        the close fee into gross_pnl and reported a still-gross figure as
        net_pnl for any close that fell through to a fetch_my_trades path.

        exchange_close_fees is the FULL round trip (open+close) only when
        pnl_is_net is True (it comes from position-history's openFee+
        closeFee sum); the fetch_my_trades paths only ever see the closing
        fill, so entry_notional/entry_fee_pct estimate the missing entry-side
        fee the same way the fully-local fallback does.
        """
        if pnl_is_net:
            net_pnl = exchange_pnl
            commission = exchange_close_fees
            gross_pnl = net_pnl + commission
        else:
            gross_pnl = exchange_pnl
            estimated_entry_fee = entry_notional * entry_fee_pct / 100.0
            commission = exchange_close_fees + estimated_entry_fee
            net_pnl = gross_pnl - commission
        return gross_pnl, net_pnl, commission

    def _resolve_trade_id(self, ident: str) -> Optional[str]:
        """Resolve a user-supplied id to an internal trade_id.

        Accepts the internal trade_id directly, OR an exchange order id — the
        "Order IDs (for cancel)" list and the Open-Orders card surface the
        exchange order id (limit/SL/TP), not the internal TI-… id, so a user who
        copies the id the bot showed them would otherwise get "not found". Match
        those exchange ids back to the owning position. Returns None if unknown.
        """
        if not ident:
            return None
        if ident in self._positions:
            return ident
        for tid, p in self._positions.items():
            if ident in (getattr(p, "limit_order_id", None),
                         getattr(p, "sl_order_id", None),
                         getattr(p, "tp_order_id", None)):
                return tid
        return None

    async def close_position(self, trade_id: str, reason: str = "bot_auto",
                              close_price: float = 0) -> str:
        """Close a live position by placing the opposite order."""
        # Accept an exchange order id too, not just the internal trade_id.
        trade_id = self._resolve_trade_id(trade_id) or trade_id
        # C2-02 FIX: Per-trade lock prevents double-close race.
        lock = self._close_locks.setdefault(trade_id, asyncio.Lock())
        async with lock:
            result = await self._close_position_inner(trade_id, reason, close_price)
        # H-04 FIX: Do NOT pop the lock here — a concurrent caller could
        # create a new Lock() via setdefault() between our release and pop,
        # defeating the mutual-exclusion guarantee.  Stale locks are pruned
        # in _save_positions() instead.
        return result

    async def _close_position_inner(self, trade_id: str, reason: str = "bot_auto",
                              close_price: float = 0) -> str:
        """Inner close logic, called under per-trade lock."""
        pos = self._positions.get(trade_id)
        if not pos or pos.status not in ("open", "pending_fill"):
            return f"Position {trade_id} not found or already closed/closing."

        # ── PENDING_FILL: cancel the limit order, don't try to close a position ──
        # A pending_fill position has no open position on exchange — only an
        # unfilled limit order. We must cancel that order, not place a market close.
        if pos.status == "pending_fill" and pos.limit_order_id:
            pos.status = "closing"
            self._save_positions()
            try:
                exchange = await self._get_exchange()
                await exchange.cancel_order(pos.limit_order_id, pos.symbol)

                # Verify the order is actually cancelled
                cancelled = False
                try:
                    order_info = await exchange.fetch_order(
                        pos.limit_order_id, pos.symbol,
                        params={"productType": "USDT-FUTURES"})
                    status = (order_info.get("status") or "").lower()
                    filled = float(order_info.get("filled") or 0)
                    if status in ("canceled", "cancelled", "expired", "closed"):
                        cancelled = True
                    elif filled > 0:
                        # It filled while we were cancelling — handle as a fill
                        audit(trade_log,
                              f"Limit order {pos.limit_order_id} filled during cancel for {pos.symbol}",
                              action="cancel_pending", result="FILLED_DURING_CANCEL")
                        pos.status = "open"
                        self._save_positions()
                        return (f"Limit order for {pos.symbol} filled while cancelling. "
                                f"Position is now open — use Close to exit.")
                except Exception:
                    # fetch_order failed — assume cancel worked if cancel_order didn't throw
                    cancelled = True

                if cancelled:
                    pos.status = "closed"
                    pos.close_reason = reason
                    pos.closed_at = datetime.now(UTC)
                    pos.pnl_usd = 0
                    pos.gross_pnl = 0
                    pos.commission = 0
                    pos.close_price = 0
                    del self._positions[trade_id]
                    self._save_positions()

                    audit(trade_log,
                          f"Cancelled pending limit order for {pos.symbol} (order {pos.limit_order_id})",
                          action="cancel_pending", result="CANCELLED",
                          data={"trade_id": trade_id, "order_id": pos.limit_order_id,
                                "symbol": pos.symbol, "reason": reason})
                    return f"CANCELLED pending {pos.direction} {pos.symbol} limit order"
                else:
                    # Cancel didn't work — revert status
                    pos.status = "pending_fill"
                    self._save_positions()
                    return (f"Failed to cancel limit order for {pos.symbol} — "
                            f"order may still be active on exchange. Please cancel manually on Bitget.")

            except Exception as exc:
                exc_str = str(exc)
                # 25204 = order doesn't exist (already cancelled or filled)
                if "25204" in exc_str or "Order does not exist" in exc_str:
                    # Check if it filled
                    try:
                        exchange = await self._get_exchange()
                        ccxt_sym = pos.symbol if ":USDT" in pos.symbol else f"{pos.symbol}:USDT"
                        ex_positions = await exchange.fetch_positions(
                            [ccxt_sym], params={"productType": "USDT-FUTURES"})
                        has_pos = any(
                            abs(float(p.get("contracts", 0) or 0)) > 0 for p in ex_positions
                        )
                        if has_pos:
                            pos.status = "open"
                            self._save_positions()
                            return (f"Limit order for {pos.symbol} already filled. "
                                    f"Position is now open — use Close to exit.")
                    except Exception as _pos_chk_exc:
                        logger.warning("Position check during pending cancel failed for %s: %s",
                                       pos.symbol, _pos_chk_exc)

                    # Order doesn't exist and no position — already cancelled
                    del self._positions[trade_id]
                    self._save_positions()
                    audit(trade_log,
                          f"Pending limit order already gone for {pos.symbol}: {exc_str}",
                          action="cancel_pending", result="ALREADY_GONE")
                    return f"CANCELLED pending {pos.direction} {pos.symbol} (order already gone)"
                else:
                    pos.status = "pending_fill"
                    self._save_positions()
                    logger.warning("Failed to cancel limit order %s for %s: %s",
                                   pos.limit_order_id, pos.symbol, exc)
                    return (f"Failed to cancel limit order for {pos.symbol}: {str(exc)[:100]}. "
                            f"Please cancel manually on Bitget.")

        # C2-02 FIX: Set transitional state BEFORE any await — concurrent callers
        # will see "closing" and bail out at the guard above.
        pos.status = "closing"
        self._save_positions()

        try:
            exchange = await self._get_exchange()
            close_side = "sell" if pos.direction == "LONG" else "buy"

            # Cancel SL/TP orders BEFORE closing — prevents race condition where
            # a trigger fires between close-fill and cancel, opening an opposite pos.
            cancel_failed = []
            for oid in [pos.sl_order_id, pos.tp_order_id]:
                if oid:
                    is_sl = (oid == pos.sl_order_id)
                    order_label = "SL" if is_sl else "TP"
                    try:
                        cancel_resp = await exchange.cancel_order(oid, pos.symbol)
                        cancel_status = cancel_resp.get("status", "") if isinstance(cancel_resp, dict) else ""
                        if cancel_status and cancel_status not in ("canceled", "cancelled", "closed"):
                            # Verify it is actually cancelled
                            try:
                                order_info = await exchange.fetch_order(oid, pos.symbol)
                                if order_info.get("status") not in ("canceled", "cancelled", "closed", "expired"):
                                    logger.warning("SL/TP order %s may not be cancelled (status=%s), proceeding with close anyway",
                                                   oid, order_info.get("status"))
                                    cancel_failed.append(oid)
                            except Exception:
                                pass  # Fetch failed — assume cancel worked
                    except Exception as cancel_exc:
                        exc_str = str(cancel_exc)
                        # 25204 = "Order does not exist" — exchange already executed
                        # it (SL/TP fired). We still send the reduceOnly market close
                        # below: it no-ops if the position is already flat, but
                        # guarantees closure if the 25204 was a stale/expired order
                        # rather than a real trigger. The audit records the event.
                        if "25204" in exc_str or "Order does not exist" in exc_str:
                            audit(trade_log,
                                  f"{order_label} order already executed by exchange: {pos.symbol} (order {oid})",
                                  action="sltp_exchange_trigger", result="TRIGGERED",
                                  data={"trade_id": trade_id, "order_id": oid,
                                        "order_type": order_label, "symbol": pos.symbol})
                        else:
                            audit(trade_log,
                                  f"Failed to cancel {order_label} order {oid} for {pos.symbol}: {exc_str}",
                                  action="sltp_cancel_fail", result="ERROR",
                                  data={"trade_id": trade_id, "order_id": oid,
                                        "order_type": order_label, "symbol": pos.symbol,
                                        "error": exc_str})
                        cancel_failed.append(oid)

            # Futures-only mode: all positions close via swap exchange
            # UTA v3 does NOT support tradeSide — use reduceOnly instead.
            # DO NOT send marginMode on close — the exchange knows the
            # position's actual margin mode.  Sending the wrong mode
            # (e.g. "isolated" when position is "crossed") causes the
            # exchange to miss the position and open a new SHORT instead.
            close_params = {
                "productType": "USDT-FUTURES",
                "reduceOnly": True,
            }
            # Only add tradeSide for non-UTA (v2 classic) accounts
            if not self._is_uta:
                close_params["tradeSide"] = "close"
            order = await exchange.create_order(
                symbol=pos.symbol,
                type="market",
                side=close_side,
                amount=pos.quantity,
                params=close_params,
            )
            close_order_id = str(order.get("id", ""))

            # ── POST-CLOSE VERIFICATION (GetClaw-style) ──────────────
            close_verify = await self._verify_position_closed(
                exchange, pos.symbol, pos.direction, close_order_id,
            )
            close_confirmed = close_verify["confirmed"]

            # Use verified fill data when available
            if close_verify["fill_price"] > 0:
                fill_price = close_verify["fill_price"]
            else:
                # Fallback: extract from create_order response
                fill_price = float(order.get("average", 0) or order.get("price", 0) or 0)

            if close_verify["fill_qty"] > 0:
                closed_qty = close_verify["fill_qty"]
            else:
                closed_qty = pos.quantity

            exchange_close_fees = close_verify["fees"]

            # ── RC-AUD-023b: residual-close reconciliation ──────────────
            # A partial market close can leave residual exchange exposure while
            # the local record would otherwise be marked fully closed — a silent,
            # unmonitored, money-losing state. `_verify_position_closed` already
            # detects this (confirmed=False + remaining_qty>0 when the exchange
            # still shows contracts on this symbol/side). When that happens, do
            # NOT mark the position fully closed and do NOT remove it from
            # tracking. Instead, re-open local tracking for the remaining
            # quantity (so price-based SL/TP monitoring re-protects it and the
            # next adoption/reconcile sweep can finish the job) and warn LOUDLY.
            # No new order is placed here — the close order already sent is
            # untouched. This guard fires only when BOTH the close is unconfirmed
            # AND a positive residual is reported; a mere verification hiccup
            # leaves confirmed=True/remaining_qty=0 (see _verify_position_closed),
            # so a genuinely-closed position is never spuriously re-opened.
            remaining_qty = float(close_verify.get("remaining_qty", 0) or 0)
            if (not close_confirmed) and remaining_qty > 0:
                logger.critical(
                    "RESIDUAL EXPOSURE after close of %s %s: exchange still shows "
                    "%.8f contracts — keeping position OPEN (tracking the remainder) "
                    "instead of marking closed. Stop is best-effort / price-monitored; "
                    "manual review recommended.",
                    pos.direction, pos.symbol, remaining_qty)
                audit(trade_log,
                      f"RESIDUAL after close: {pos.symbol} {pos.direction} remaining="
                      f"{remaining_qty:.8f} — re-opening tracking for remainder (NOT marked closed)",
                      action="close_residual", result="RESIDUAL",
                      data={"trade_id": trade_id, "symbol": pos.symbol,
                            "direction": pos.direction, "remaining_qty": remaining_qty,
                            "closed_qty": closed_qty, "close_order_id": close_order_id})
                self._record_warning("close_residual")
                # Track the real remainder so monitoring/sizing reflect it. The
                # exchange SL/TP orders were cancelled pre-close, so leave the
                # order ids cleared — check_positions() closes on price for a
                # position with sl_order_id=None.
                pos.quantity = remaining_qty
                pos.sl_order_id = None
                pos.tp_order_id = None
                pos.status = "open"
                # The "closing" transition above triggered a _save_positions()
                # which prunes non-(open|pending_fill) records out of the
                # in-memory dict — so re-insert this position before saving the
                # re-opened remainder, otherwise it would be dropped.
                self._positions[trade_id] = pos
                self._save_positions()
                # RC-AUD-023b (V5.2): actively re-place exchange-side SL/TP on the
                # remainder so protection isn't left to price-monitoring alone.
                # Best-effort: on failure, flag UNPROTECTED (RC-AUD-022 style) so the
                # operator is warned and price-monitoring still covers the remainder.
                # No closing order is placed — only a protective stop/target.
                try:
                    _ex = await self._get_exchange()
                    _dir = Direction.LONG if pos.direction == "LONG" else Direction.SHORT
                    re_sl, re_tp = await self._place_sl_tp(
                        _ex, pos.symbol, _dir, remaining_qty,
                        pos.stop_loss, pos.take_profit,
                    )
                except Exception as _resl_exc:
                    re_sl, re_tp = None, None
                    logger.warning("Residual SL/TP re-placement raised for %s: %s",
                                   pos.symbol, _resl_exc)
                pos.sl_order_id = re_sl
                pos.tp_order_id = re_tp
                if re_sl is None:
                    setattr(pos, "unprotected", True)
                    logger.critical(
                        "RESIDUAL UNPROTECTED (%s %s): could not re-place stop-loss on "
                        "the %.8f remainder — price-monitoring only. Review on Bitget.",
                        pos.symbol, pos.direction, remaining_qty)
                    audit(trade_log,
                          f"RESIDUAL stop-loss re-placement FAILED for {pos.symbol} "
                          f"remainder={remaining_qty:.8f} — UNPROTECTED (price-monitored)",
                          action="close_residual", result="UNPROTECTED",
                          data={"trade_id": trade_id, "symbol": pos.symbol,
                                "remaining_qty": remaining_qty})
                    self._record_warning("residual_unprotected")
                else:
                    audit(trade_log,
                          f"RESIDUAL re-protected: {pos.symbol} SL re-placed on "
                          f"remainder={remaining_qty:.8f} (sl={re_sl})",
                          action="close_residual", result="REPROTECTED",
                          data={"trade_id": trade_id, "symbol": pos.symbol,
                                "remaining_qty": remaining_qty, "sl_order_id": re_sl})
                self._save_positions()
                return (
                    f"⚠️ PARTIAL CLOSE — RESIDUAL REMAINS: {pos.direction} {pos.symbol}\n"
                    f"Exchange still shows {remaining_qty:.6f} open after the close order.\n"
                    f"Position kept OPEN (tracking the remainder); it will be "
                    f"re-protected/closed by monitoring. Review on Bitget."
                )

            if fill_price == 0:
                # Derive from cost/filled (proceeds / qty sold)
                cost_val = float(order.get("cost", 0) or 0)
                filled_val = float(order.get("filled", 0) or 0)
                if cost_val > 0 and filled_val > 0:
                    fill_price = cost_val / filled_val
            if fill_price == 0:
                # Last resort: fetch ticker for current price
                try:
                    main_exchange = await self._get_exchange()
                    ticker = await main_exchange.fetch_ticker(pos.symbol)
                    fill_price = float(ticker.get("last", 0) or 0)
                except Exception as _tick_exc:
                    logger.warning("Close price ticker fallback failed for %s: %s",
                                   pos.symbol, _tick_exc)
            if fill_price == 0:
                fill_price = pos.entry_price  # absolute fallback — no phantom PnL

            # Calculate PnL — try exchange-reported profit first (source of truth)
            # Priority: 1) Bitget position history (netProfit, most accurate)
            #           2) fetch_my_trades (profit, excludes funding)
            #           3) entry/exit price calculation (fallback)
            exchange_pnl = None
            # Whether exchange_pnl above is already fee-adjusted. Only the
            # position-history endpoint's netProfit field is; achievedProfits
            # (its own fallback) and every per-fill "profit" value below are
            # gross, same Bitget "achieved profit" convention. See the
            # gross/net reconstruction below — treating a gross figure as
            # already-net understated total fees and reported gross PnL to
            # the user as "net".
            _pnl_is_net = False

            # Try position history first (includes funding fees — most accurate)
            try:
                import asyncio as _aio_pnl
                await _aio_pnl.sleep(2)  # brief delay for Bitget to finalize
                pos_hist_data = await self._fetch_bitget_close_data(pos)
                if pos_hist_data and pos_hist_data.get("pnl") is not None:
                    exchange_pnl = pos_hist_data["pnl"]
                    exchange_close_fees = pos_hist_data.get("fees", 0) or 0
                    _pnl_is_net = pos_hist_data.get("pnl_is_net", False)
                    if pos_hist_data.get("close_price", 0) > 0:
                        fill_price = pos_hist_data["close_price"]
                    logger.info("Using Bitget position history PnL for %s: $%.4f (fees $%.4f)",
                                pos.symbol, exchange_pnl, exchange_close_fees)
            except Exception as _hist_exc:
                logger.debug("Position history lookup failed for %s: %s", pos.symbol, _hist_exc)

            # Fallback to fetch_my_trades if position history didn't work
            if exchange_pnl is None:
                try:
                    close_trades = await exchange.fetch_my_trades(
                        pos.symbol, limit=10)
                    close_fills = [
                        t for t in close_trades
                        if t.get("order") == close_order_id
                    ]
                    if close_fills:
                        total_profit = 0.0
                        total_fees = 0.0
                        for cf in close_fills:
                            cf_info = cf.get("info", {})
                            profit = float(cf_info.get("profit", 0) or 0)
                            total_profit += profit
                            fee_detail = cf_info.get("feeDetail", {})
                            if isinstance(fee_detail, dict):
                                total_fees += abs(float(fee_detail.get("totalFee", 0) or 0))
                        if total_profit != 0 or total_fees != 0:
                            exchange_pnl = total_profit
                            if total_fees > 0:
                                exchange_close_fees = total_fees
                except Exception as _fee_exc:
                    # CRITICAL FIX: use pessimistic fee assumption (20bp round-trip)
                    # instead of 0 when exchange data unavailable
                    _notional = fill_price * pos.quantity if fill_price > 0 else pos.entry_price * pos.quantity
                    exchange_close_fees = _notional * 0.002  # 20bp pessimistic
                    logger.warning(
                        "Exchange fee fetch failed for %s — using pessimistic 20bp (%.4f): %s",
                        pos.symbol, exchange_close_fees, _fee_exc)
                    audit(trade_log,
                          f"Fee fetch FAILED for {pos.symbol}: using pessimistic 20bp estimate",
                          action="fee_fetch", result="EXCEPTION",
                          data={"error": str(_fee_exc)[:200],
                                "pessimistic_fee": round(exchange_close_fees, 6)})
                    self._record_warning("fee_fetch")

            if exchange_pnl is not None:
                is_limit_entry = getattr(pos, 'order_type', '') == 'limit'
                entry_fee_pct = CONFIG.risk.maker_fee_pct if is_limit_entry else CONFIG.risk.taker_fee_pct
                gross_pnl, net_pnl, commission = self._reconcile_exchange_close_pnl(
                    exchange_pnl, exchange_close_fees, _pnl_is_net,
                    entry_notional=pos.entry_price * pos.quantity,
                    entry_fee_pct=entry_fee_pct,
                )
            else:
                # Fallback: calculate from entry/exit prices
                if pos.direction == "LONG":
                    gross_pnl = (fill_price - pos.entry_price) * pos.quantity
                else:
                    gross_pnl = (pos.entry_price - fill_price) * pos.quantity

                # Exchange commission: entry + exit notional x fee rate
                entry_notional = pos.entry_price * pos.quantity
                exit_notional = fill_price * pos.quantity
                # GETCLAW: use maker rate if limit order (POST_ONLY), taker for market
                is_limit_entry = getattr(pos, 'order_type', '') == 'limit'
                entry_fee_pct = CONFIG.risk.maker_fee_pct if is_limit_entry else CONFIG.risk.taker_fee_pct
                exit_fee_pct = CONFIG.risk.taker_fee_pct  # exits are usually market
                commission = (entry_notional * entry_fee_pct / 100.0) + (exit_notional * exit_fee_pct / 100.0)
                net_pnl = gross_pnl - commission

            pos.close_reason = reason
            pos.status = "closed"
            pos.close_price = fill_price
            pos.gross_pnl = round(gross_pnl, 4)
            pos.commission = round(commission, 4)
            pos.pnl_usd = round(net_pnl, 4)
            pos.closed_at = datetime.now(UTC)

            # AUDIT-FIX: Append to closed trades BEFORE save_positions, because
            # save_positions prunes closed entries from _positions dict. If a crash
            # occurs between save_positions and append_closed_trade, the trade
            # would vanish from both data stores.
            self._append_closed_trade(pos)

            # F-07 FIX: persist after closing (removes from open positions file)
            self._save_positions()

            # C-09 FIX: post-close cleanup — cancel any remaining open orders on this symbol
            # to prevent orphaned SL/TP triggers from opening opposite positions.
            if cancel_failed:
                for stale_oid in cancel_failed:
                    try:
                        await exchange.cancel_order(stale_oid, pos.symbol)
                    except Exception:
                        pass  # Best-effort cleanup
                try:
                    open_orders = await exchange.fetch_open_orders(pos.symbol)
                    for oo in open_orders:
                        try:
                            await exchange.cancel_order(oo["id"], pos.symbol)
                            logger.info("Post-close cleanup: cancelled orphan order %s on %s", oo["id"], pos.symbol)
                        except Exception:
                            pass
                except Exception as cleanup_exc:
                    logger.debug("Post-close order cleanup failed for %s: %s", pos.symbol, cleanup_exc)
            # Notify engine to invalidate balance cache
            self._fire_position_closed(pos)

            audit(trade_log, f"Live position closed: {pos.symbol} net=${net_pnl:.4f} (gross=${gross_pnl:.4f}, fee=${commission:.4f})",
                  action="live_close", result="CLOSED",
                  data={
                      "trade_id": trade_id, "reason": reason,
                      "entry": pos.entry_price, "exit": fill_price,
                      "pnl_usd": round(net_pnl, 4),
                      "gross_pnl": round(gross_pnl, 4),
                      "commission": round(commission, 4),
                      "confirmed": close_confirmed,
                      "exchange_fees": exchange_close_fees,
                      "close_order_id": close_order_id,
                      "close_failure_stage": close_verify.get("failure_stage", ""),
                  })

            pnl_str = f"+${net_pnl:.4f}" if net_pnl >= 0 else f"-${abs(net_pnl):.4f}"
            # C2-58 FIX: Show both leveraged (margin) and unleveraged (notional) PnL%
            pnl_pct = ((fill_price - pos.entry_price) / pos.entry_price * 100)
            if pos.direction == "SHORT":
                pnl_pct = -pnl_pct
            lev = pos.leverage or 1
            pnl_pct_margin = pnl_pct * lev  # leveraged return — what hits the account
            hold_secs = (pos.closed_at - pos.opened_at).total_seconds() if pos.closed_at and pos.opened_at else 0
            if hold_secs < 3600:
                hold_str = f"{hold_secs / 60:.0f}m"
            elif hold_secs < 86400:
                hold_str = f"{hold_secs / 3600:.1f}h"
            else:
                hold_str = f"{hold_secs / 86400:.1f}d"
            fee_str = f"${commission:.2f}"
            # C2-58: Show leveraged return when leverage > 1
            if lev > 1:
                pnl_pct_str = f"{pnl_pct_margin:+.2f}% margin / {pnl_pct:+.2f}% notional, {lev}×"
            else:
                pnl_pct_str = f"{pnl_pct:+.2f}%"

            # Close verification status
            if close_confirmed:
                verify_str = "✅ CONFIRMED"
            else:
                stage = close_verify.get("failure_stage", "unconfirmed")
                verify_str = f"⚠️ {stage}"

            close_msg = (
                f"CLOSED {pos.direction} {pos.symbol} ({reason})\n"
                f"Entry: ${pos.entry_price:,.4f} → Exit: ${fill_price:,.4f}\n"
                f"PnL: {pnl_str} ({pnl_pct_str}) | Fees: {fee_str} | Hold: {hold_str}\n"
                f"Verified: {verify_str}"
            )

            # Store structured close data for rich rendering
            self._last_close_data = {
                "symbol": pos.symbol,
                "direction": pos.direction,
                "reason": reason,
                "entry": pos.entry_price,
                "exit": fill_price,
                "pnl_pct": pnl_pct,
                "pnl_pct_margin": pnl_pct_margin,  # C2-58: leveraged return
                "pnl_usd": round(net_pnl, 4),
                "gross_pnl": round(gross_pnl, 4),
                "fees": round(commission, 4),
                "exchange_fees": round(exchange_close_fees, 4),
                "size_usd": round(pos.cost_usd, 2) if pos.cost_usd > 0 else round(pos.entry_price * pos.quantity, 2),
                "leverage": pos.leverage or 1,
                "hold_time": hold_str,
                "confirmed": close_confirmed,
                "close_order_id": close_order_id,
            }

            return close_msg

        except Exception as exc:
            exc_str = str(exc)

            # ── CRITICAL FIX: Handle "position already closed on exchange" ──
            # When exchange returns 25227 ("No position available to close"),
            # it could mean:
            #   a) Position was already closed by exchange-side SL/TP trigger
            #   b) Close order had wrong parameters (missing marginMode etc.)
            # MUST verify the position is actually gone before recording a close.
            if "25227" in exc_str or "No position available" in exc_str:
                try:
                    verify_exchange = await self._get_exchange()
                    ccxt_sym = pos.symbol if ":USDT" in pos.symbol else f"{pos.symbol}:USDT"
                    ex_positions = await verify_exchange.fetch_positions(
                        [ccxt_sym], params={"productType": "USDT-FUTURES"})
                    still_open = any(
                        abs(float(p.get("contracts", 0) or 0)) > 0 for p in ex_positions
                    )
                except Exception as verify_exc:
                    logger.debug("25227 position verification failed: %s", verify_exc)
                    still_open = True  # Assume still open if we can't verify

                if still_open:
                    # Position is still on exchange — 25227 was likely wrong params.
                    # Try v2 Flash Close as fallback (simpler endpoint, no marginMode).
                    audit(trade_log,
                          f"25227 but position still on exchange — trying flash close: {pos.symbol}",
                          action="live_close_25227", result="STILL_OPEN_FLASH_CLOSE")
                    try:
                        flash_result = await self._flash_close_position(pos)
                        if flash_result and flash_result.get("code") == "00000":
                            # Flash close worked — now look up fill data
                            await asyncio.sleep(1.0)  # Let fill settle
                            close_result = await self._handle_already_closed_position(pos)
                            if close_result:
                                return close_result
                    except Exception as flash_exc:
                        logger.warning("Flash close fallback failed for %s: %s",
                                       pos.symbol, flash_exc)
                else:
                    # Position is truly gone — look up actual fill data
                    audit(trade_log,
                          f"Position {pos.symbol} confirmed closed on exchange — looking up fill data",
                          action="live_close_25227", result="LOOKUP")
                    try:
                        close_result = await self._handle_already_closed_position(pos)
                        if close_result:
                            return close_result
                    except Exception as lookup_exc:
                        logger.debug("Fill lookup after 25227 failed for %s: %s",
                                     pos.symbol, lookup_exc)

            # H-01 FIX: Revert status so position is retried next cycle
            pos.status = "open"
            self._save_positions()
            audit(trade_log, f"Live close failed: {exc}",
                  action="live_close", result="ERROR",
                  data={"trade_id": trade_id, "error": exc_str})
            return f"CLOSE FAILED for {trade_id}: {exc}"

    # ── Bitget Position History — single source of truth for closed PnL ──

    async def _fetch_bitget_close_data(
        self, pos: LivePosition,
    ) -> dict | None:
        """Query Bitget position history API for actual close price and PnL.

        Returns dict with keys: close_price, pnl, fees, reason, source
        or None if the lookup fails.

        This is the authoritative source — Bitget's own closed-position
        record with the real fill price, realized PnL, and fees.
        """
        exchange = await self._get_exchange()
        ccxt_symbol = pos.symbol if ":USDT" in pos.symbol else f"{pos.symbol}:USDT"
        # Bitget raw symbol: strip /USDT:USDT → e.g. "BTCUSDT"
        # Handle all possible formats: "BZ/USDT:USDT" → "BZUSDT", "BZUSDT" stays
        raw_symbol = pos.symbol.split(":")[0].replace("/", "")
        if not raw_symbol.endswith("USDT"):
            raw_symbol = raw_symbol + "USDT"

        # ── 1. Bitget position history endpoint (most accurate) ────────
        # GET /api/v2/mix/position/history-position
        # Returns (v2 field names): openAvgPrice, closeAvgPrice, pnl (gross),
        # netProfit (fee-adjusted), openFee, closeFee. NOTE: the v1 endpoint used
        # openPrice/achievedProfits — reading those v1 names off a v2 response
        # yields None -> 0, which silently skipped every row (entry_price_hist<=0
        # -> continue) and made this authoritative lookup always return None,
        # forcing the ticker/inference fallback (incident TI-a4ba8a82).
        #
        # Try with progressively wider time windows:
        #   Pass 1: from 5 min before position opened (tight)
        #   Pass 2: from 1 hour before position opened (wider)
        #   Pass 3: no startTime filter at all (widest — gets last 20 positions)
        time_windows: list[Optional[int]] = []
        if pos.opened_at:
            ts_ms = int(pos.opened_at.timestamp() * 1000)
            time_windows.append(ts_ms - 300_000)    # 5 min before open
            time_windows.append(ts_ms - 3_600_000)  # 1 hour before open
        time_windows.append(None)  # no filter

        for since_ms in time_windows:
            try:
                params: dict = {
                    "productType": "USDT-FUTURES",
                    "symbol": raw_symbol,
                }
                if since_ms is not None:
                    params["startTime"] = str(since_ms)
                resp = await exchange.privateMixGetV2MixPositionHistoryPosition(params)
                entries = resp.get("data", {}).get("list", []) if isinstance(resp.get("data"), dict) else []

                logger.debug(
                    "Position history for %s (window=%s): %d entries returned",
                    raw_symbol, since_ms, len(entries),
                )

                # Match by entry price — use 0.5% tolerance (partial fills shift avg)
                best_match = None
                best_price_diff = float("inf")
                for entry in entries:
                    # v2 name is openAvgPrice; keep the v1 openPrice as a
                    # defensive fallback so a legacy payload still matches.
                    entry_price_hist = float(
                        entry.get("openAvgPrice") or entry.get("openPrice") or 0)
                    if entry_price_hist <= 0:
                        continue
                    price_diff = abs(entry_price_hist - pos.entry_price) / pos.entry_price
                    if price_diff < 0.005 and price_diff < best_price_diff:  # within 0.5%
                        best_match = entry
                        best_price_diff = price_diff

                if best_match:
                    entry = best_match
                    close_price = float(entry.get("closeAvgPrice", 0) or 0)
                    # v2 name is pnl (gross); keep achievedProfits (v1) as fallback.
                    pnl = float(entry.get("pnl") or entry.get("achievedProfits") or 0)
                    open_fee = abs(float(entry.get("openFee", 0) or 0))
                    close_fee = abs(float(entry.get("closeFee", 0) or 0))
                    total_fees = open_fee + close_fee
                    net_profit = float(entry.get("netProfit", 0) or 0)
                    # Leverage as the exchange applied it. Not present on every
                    # history payload; captured best-effort so the caller can
                    # reconcile a stale/config-derived pos.leverage when available.
                    hist_leverage = int(float(
                        entry.get("leverage") or entry.get("openLeverage") or 0))

                    if close_price > 0:
                        # netProfit is the fee-adjusted figure. When it is 0 the
                        # field is (almost always) just unpopulated, not a true
                        # break-even, so fall back to gross `pnl`. But total_fees
                        # here is the FULL round trip (openFee+closeFee), so derive
                        # net locally and flag it NET — otherwise the caller's
                        # _reconcile_exchange_close_pnl adds a SECOND estimated
                        # entry fee on top of fees that already include the open
                        # leg (entry-fee double-count).
                        if net_profit != 0:
                            final_pnl = net_profit
                            _pnl_is_net = True
                        elif total_fees > 0:
                            final_pnl = pnl - total_fees
                            _pnl_is_net = True
                        else:
                            final_pnl = pnl
                            _pnl_is_net = False
                        close_type = (entry.get("closeType") or "").lower()
                        if "tp" in close_type or "take" in close_type:
                            reason = "TP HIT (exchange)"
                        elif "sl" in close_type or "stop" in close_type:
                            # A trailing/breakeven stop that ratcheted onto the
                            # profit side fills at a GAIN — never label that a loss.
                            reason = stop_exit_label(
                                pos.direction == "LONG", pos.entry_price,
                                pos.stop_loss, close_price,
                                bool(pos.trailing_state
                                     and pos.trailing_state.get("trailing_active")),
                                final_pnl,
                            ) + " (exchange)"
                        elif "liquidat" in close_type:
                            reason = "LIQUIDATED"
                        else:
                            # Bitget reported a closeType we don't classify (could
                            # be a user close on the app, an ADL, a partial-ladder
                            # reduceOnly, etc.). It is NOT necessarily a manual
                            # close — say so honestly instead of asserting "MANUAL".
                            reason = "CLOSED (unknown)"

                        logger.info(
                            "Bitget position history for %s: close=%.4f, pnl=%.4f, fees=%.4f, lev=%dx (price_diff=%.4f%%)",
                            pos.symbol, close_price, final_pnl, total_fees, hist_leverage, best_price_diff * 100,
                        )
                        return {
                            "close_price": close_price,
                            "pnl": final_pnl,
                            "fees": total_fees,
                            "reason": reason,
                            "source": "bitget_position_history",
                            "leverage": hist_leverage,
                            # True when pnl is already fee-adjusted: either
                            # netProfit was populated, or we derived net locally
                            # from the full round-trip fees above.
                            "pnl_is_net": _pnl_is_net,
                        }
            except Exception as e:
                logger.debug("Bitget position history lookup failed for %s (window=%s): %s",
                             pos.symbol, since_ms, e)

        # ── 2. fetchMyTrades — match any recent close trade by symbol ──
        # Not just SL/TP order IDs — also find manual close fills
        # Try without since filter if first attempt returns nothing useful
        for attempt, use_since in enumerate([(True,), (False,)]):
            try:
                if use_since[0] and pos.opened_at:
                    since_ms_trades = int(pos.opened_at.timestamp() * 1000) - 300_000
                else:
                    since_ms_trades = None
                trades = await exchange.fetch_my_trades(
                    ccxt_symbol, since=since_ms_trades, limit=50,
                    params={"productType": "USDT-FUTURES"},
                )

                # First try matching by SL/TP order IDs
                if pos.sl_order_id or pos.tp_order_id:
                    relevant = [
                        t for t in trades
                        if t.get("order") in (pos.sl_order_id, pos.tp_order_id)
                    ]
                    if relevant:
                        fill_price = float(relevant[-1].get("price", 0) or 0)
                        total_profit = 0.0
                        total_fees = 0.0
                        for rt in relevant:
                            info = rt.get("info", {})
                            profit = float(info.get("profit", 0) or 0)
                            total_profit += profit
                            fee_detail = info.get("feeDetail", {})
                            if isinstance(fee_detail, dict):
                                total_fees += abs(float(fee_detail.get("totalFee", 0) or 0))
                        matched_order = relevant[-1].get("order")
                        if matched_order == pos.tp_order_id:
                            reason = "TP HIT (exchange)"
                        elif matched_order == pos.sl_order_id:
                            reason = stop_exit_label(
                                pos.direction == "LONG", pos.entry_price,
                                pos.stop_loss, fill_price,
                                bool(pos.trailing_state
                                     and pos.trailing_state.get("trailing_active")),
                                total_profit,
                            ) + " (exchange)"
                        else:
                            # Close-side fill not tied to our SL/TP order IDs —
                            # mechanism unknown (manual, ADL, partial ladder, …).
                            reason = "CLOSED (unknown)"
                        if fill_price > 0 and total_profit != 0:
                            return {
                                "close_price": fill_price,
                                "pnl": total_profit,
                                "fees": total_fees,
                                "reason": reason,
                                "source": "exchange_fill_sltp",
                                # Bitget's per-fill "profit" is gross (same
                                # convention as achievedProfits above); fees
                                # here are close-side only, never the entry
                                # leg — caller must not treat this as net.
                                "pnl_is_net": False,
                            }

                # Then try matching by reduceOnly / close side trades
                close_side = "sell" if pos.direction == "LONG" else "buy"
                close_fills = [
                    t for t in trades
                    if t.get("side") == close_side
                    and t.get("order") not in (getattr(pos, 'limit_order_id', None),)
                ]
                if close_fills:
                    last_fill = close_fills[-1]
                    fill_price = float(last_fill.get("price", 0) or 0)
                    info = last_fill.get("info", {})
                    profit = float(info.get("profit", 0) or 0)
                    total_fees = 0.0
                    fee_detail = info.get("feeDetail", {})
                    if isinstance(fee_detail, dict):
                        total_fees = abs(float(fee_detail.get("totalFee", 0) or 0))
                    if fill_price > 0 and profit != 0:
                        return {
                            "close_price": fill_price,
                            "pnl": profit,
                            "fees": total_fees,
                            # Unrecognized close-side fill — see note above.
                            "reason": "CLOSED (unknown)",
                            "source": "exchange_fill_recent",
                            # Gross, close-side fee only — see exchange_fill_sltp note.
                            "pnl_is_net": False,
                        }

                # If we got trades but none matched, try without since filter
                if trades and use_since[0]:
                    continue
                break
            except Exception as e:
                logger.debug("fetchMyTrades lookup failed for %s (attempt %d): %s",
                             pos.symbol, attempt, e)
                if attempt == 0:
                    continue
                break

        # ── 3. fetchClosedOrders — only actually filled orders ─────────
        if pos.sl_order_id or pos.tp_order_id:
            try:
                closed_orders = await exchange.fetch_closed_orders(
                    ccxt_symbol, limit=20,
                    params={"productType": "USDT-FUTURES"},
                )
                for o in closed_orders:
                    if o.get("id") in (pos.sl_order_id, pos.tp_order_id):
                        filled = float(o.get("filled", 0) or 0)
                        status = (o.get("status") or "").lower()
                        if filled <= 0 or status in ("cancelled", "canceled", "expired"):
                            continue
                        avg = o.get("average") or o.get("price")
                        if avg and float(avg) > 0:
                            if o["id"] == pos.tp_order_id:
                                reason = "TP HIT (exchange)"
                            else:
                                reason = stop_exit_label(
                                    pos.direction == "LONG", pos.entry_price,
                                    pos.stop_loss, float(avg),
                                    bool(pos.trailing_state
                                         and pos.trailing_state.get("trailing_active")),
                                ) + " (exchange)"
                            return {
                                "close_price": float(avg),
                                "pnl": None,  # Not available from orders
                                "fees": 0.0,
                                "reason": reason,
                                "source": "closed_order",
                            }
            except Exception as e:
                logger.debug("fetchClosedOrders failed for %s: %s", pos.symbol, e)

        # ── 4. No exchange data found — return None (never estimate) ───
        logger.warning(
            "No exchange close data found for %s — all lookups failed "
            "(raw_symbol=%s). Will use ticker price as last resort.",
            pos.symbol, raw_symbol,
        )
        return None

    @staticmethod
    def _fmt_fill_protection(stop_loss: float, take_profit: float,
                            sl_id, tp_id, trailing) -> str:
        """The ' | SL: .. | TP: .. | Trailing: armed' suffix for a fill notice.

        Shows the PRICE levels the stop/target sit at — NOT the exchange order
        ids. A fill card that printed the 19-digit order id where the operator
        expects a price ("SL: 1456972736727867394") is the regression this
        guards. The id truthiness still gates the field: no id -> the protective
        order was not placed, so its price is omitted.
        """
        sl = f" | SL: {stop_loss:.6g}" if (sl_id and stop_loss) else ""
        tp = f" | TP: {take_profit:.6g}" if (tp_id and take_profit) else ""
        tr = " | Trailing: armed" if trailing else ""
        return f"{sl}{tp}{tr}"

    def _is_duplicate_fill(self, pos: "LivePosition", fill_price: float) -> bool:
        """True when another OPEN record (different trade_id) already tracks a
        fill on the same normalized symbol + direction at this entry (±0.05%).

        Live incident (UNI): the adoption sweep minted a second pending_fill
        record for an order the bot already tracked; both transitioned on the
        same exchange fill → double "TRADE OPENED", racing SL placement (the
        plan-order cleanup cancels the other record's stop), and later two
        close bookings. Same signature + trade-off as
        _is_duplicate_close_booking; fail-safe: errors return False.
        """
        try:
            sym = normalize_symbol(pos.symbol)
            price = float(fill_price or 0.0)
            if not sym or price <= 0:
                return False
            for other in self._positions.values():
                if other.trade_id == pos.trade_id:
                    continue
                if other.status != "open":
                    continue
                if normalize_symbol(other.symbol) != sym:
                    continue
                if other.direction != pos.direction:
                    continue
                o_entry = float(other.entry_price or 0.0)
                if o_entry > 0 and abs(o_entry - price) / price <= 0.0005:
                    return True
        except Exception:
            return False
        return False

    def _is_duplicate_close_booking(self, pos: "LivePosition") -> bool:
        """True when ``pos`` looks like a SECOND internal record of a close that
        is already booked in ``_closed_trades`` under a DIFFERENT trade_id.

        Live incident 2026-07-07: the adoption sweeps can mint a second record
        (``TI-adopted-…`` / ``ORPHAN-…`` / drifted clientOid reconstruction) for
        one exchange position. When the real close was booked under the original
        trade_id, every downstream guard compared trade_id strings only, so the
        duplicate record was re-booked ~1 minute later (second notification;
        realized PnL / trade count / loss streak / learning store all counted
        twice). Signature: same normalized symbol + same direction + entry
        within 0.05% + the existing booking closed within the window below.

        Window: 2 hours (was 10 min). Live incident (UNI, 2026-07-11): the
        duplicate record's close was booked by a reconcile sweep 30 MINUTES
        after the first booking — outside the old window — so the operator got
        an identical second close card. Two genuinely distinct fills at the
        same entry to 0.05% within 2h remain near-impossible (the re-entry
        cooldown spaces same-symbol entries, and a real re-entry fills at a
        different price); partial closes of the SAME position share the
        trade_id and are exempted above. A false positive costs one suppressed
        stat row, a false negative double-counts money. Fail-safe: errors
        return False (never blocks a legitimate booking).
        """
        try:
            def _norm(sym: str) -> str:
                return (sym or "").replace("/", "").replace(":USDT", "").upper()

            sym = _norm(pos.symbol)
            entry = float(pos.entry_price or 0.0)
            if not sym or entry <= 0:
                return False
            now = datetime.now(UTC)
            for ct in self._closed_trades:
                if ct.trade_id == pos.trade_id:
                    continue  # same-id replacement is handled (and allowed)
                if _norm(ct.symbol) != sym or ct.direction != pos.direction:
                    continue
                ct_entry = float(ct.entry_price or 0.0)
                if ct_entry <= 0 or abs(ct_entry - entry) / entry > 0.0005:
                    continue
                if ct.closed_at is None:
                    continue
                ct_closed = ct.closed_at
                if ct_closed.tzinfo is None:
                    ct_closed = ct_closed.replace(tzinfo=UTC)
                if abs((now - ct_closed).total_seconds()) <= 7200:
                    return True
        except Exception:
            return False
        return False

    def _suppress_duplicate_record(self, pos: "LivePosition") -> None:
        """Mark a duplicate record closed WITHOUT booking it: no _closed_trades
        row, no _fire_position_closed (learning/streaks), no _last_close_data
        write. _save_positions() then prunes it from the open dict."""
        pos.status = "closed"
        pos.close_reason = "duplicate_suppressed"
        pos.closed_at = datetime.now(UTC)
        self._save_positions()
        audit(trade_log,
              f"Duplicate close suppressed: {pos.symbol} {pos.direction} "
              f"(trade {pos.trade_id}) — same close already booked under another id",
              action="duplicate_close", result="SUPPRESSED",
              data={"trade_id": pos.trade_id, "symbol": pos.symbol,
                    "entry": pos.entry_price})

    def _infer_close_reason(self, pos: "LivePosition", exit_price: float) -> str:
        """Infer whether TP or SL was hit based on exit price proximity.

        When exchange history is unavailable, we compare the exit price
        to the stored TP and SL levels to determine the most likely trigger.
        If exit price is not close to either TP or SL, the mechanism is unknown
        (a user close, ADL, or partial-ladder fill all look the same here) — we
        report "CLOSED (unknown)" rather than asserting a manual close.
        """
        if pos.stop_loss <= 0 or pos.take_profit <= 0 or exit_price <= 0:
            return "CLOSED (unknown)"

        dist_to_sl = abs(exit_price - pos.stop_loss)
        dist_to_tp = abs(exit_price - pos.take_profit)
        sl_tp_range = abs(pos.take_profit - pos.stop_loss)

        # Proximity threshold: within 0.5% of SL/TP range counts as "near"
        proximity_threshold = sl_tp_range * 0.05 if sl_tp_range > 0 else 0

        # Check if exit price is at or beyond TP/SL level
        if pos.direction == "LONG":
            tp_hit = exit_price >= pos.take_profit * 0.998  # within 0.2%
            sl_hit = exit_price <= pos.stop_loss * 1.002
        else:  # SHORT
            tp_hit = exit_price <= pos.take_profit * 1.002
            sl_hit = exit_price >= pos.stop_loss * 0.998

        # A stop that ratcheted onto the profit side of entry (trailing/breakeven)
        # fills at a GAIN — label it "TRAILING SL HIT", never a bare "SL HIT"
        # (which every dashboard/win-loss tally reads as a loss). Incident
        # TI-a4ba8a82: LONG entry 0.5638, trailing stop 0.5679, exit a profit.
        sl_label = stop_exit_label(
            pos.direction == "LONG", pos.entry_price, pos.stop_loss, exit_price,
            bool(pos.trailing_state and pos.trailing_state.get("trailing_active")),
        ) + " (inferred)"

        if tp_hit and not sl_hit:
            return "TP HIT (inferred)"
        elif sl_hit and not tp_hit:
            return sl_label
        elif tp_hit and sl_hit:
            # Both triggered (very tight range) — pick closer
            if dist_to_tp < dist_to_sl:
                return "TP HIT (inferred)"
            else:
                return sl_label

        # Exit price is between SL and TP — check if it's close to either
        if dist_to_tp <= proximity_threshold:
            return "TP HIT (inferred)"
        elif dist_to_sl <= proximity_threshold:
            return sl_label

        # Exit sits between SL and TP and near neither — most likely a deliberate
        # close, but we can't prove it was the user vs ADL/partial-fill, so report
        # the honest "unknown" rather than asserting MANUAL.
        return "CLOSED (unknown)"

    async def _handle_already_closed_position(self, pos: LivePosition) -> str | None:
        """Handle a position that was already closed on exchange (25227).

        Uses Bitget position history API for actual close price and PnL.
        Never estimates — only uses real exchange data.

        Returns close message string if successful, None if lookup fails.
        """
        # ── Cross-record duplicate guard (live incident 2026-07-07) ──
        # This handler used to book UNCONDITIONALLY (it never consulted
        # _closed_trades), so a second internal record for an already-booked
        # close produced a second "SL HIT (inferred)" booking with a
        # ticker-estimated exit. Suppress instead — and return a message (not
        # None) so the caller does NOT revert the record to "open" and retry
        # this dead close forever.
        if self._is_duplicate_close_booking(pos):
            self._suppress_duplicate_record(pos)
            return (f"Position {pos.symbol} already booked closed — "
                    f"duplicate record suppressed (no PnL re-counted)")

        exchange = await self._get_exchange()
        ccxt_symbol = pos.symbol if ":USDT" in pos.symbol else f"{pos.symbol}:USDT"

        # ── Get real close data from Bitget ──────────────────────────
        close_data = await self._fetch_bitget_close_data(pos)

        if close_data and close_data["close_price"] > 0:
            est_exit = close_data["close_price"]
            reason = close_data["reason"]
            fill_source = close_data["source"]
            exchange_reported_pnl = close_data["pnl"]  # may be None for closed_order source
            # Reconcile leverage from the exchange record when it carries one
            # (belt-and-suspenders: the position-history payload does not always
            # include leverage — the periodic sync_positions_from_exchange keeps
            # pos.leverage current while OPEN so the record is right at close).
            hist_lev = int(close_data.get("leverage") or 0)
            if hist_lev > 0 and hist_lev != pos.leverage:
                logger.warning(
                    "Leverage reconcile on close %s: tracked=%dx, exchange=%dx",
                    pos.symbol, pos.leverage, hist_lev)
                pos.leverage = hist_lev
        else:
            # All exchange lookups failed — use current ticker (real price, not SL/TP)
            try:
                ticker = await exchange.fetch_ticker(ccxt_symbol)
                est_exit = float(ticker.get("last", 0) or 0)
            except Exception:
                est_exit = 0
            if est_exit <= 0:
                return None  # Can't determine anything
            # Infer whether TP or SL was hit based on exit price proximity
            reason = self._infer_close_reason(pos, est_exit)
            fill_source = "ticker_fallback"
            exchange_reported_pnl = None
            logger.warning(
                "Using ticker price for %s close — exchange history unavailable (inferred: %s)",
                pos.symbol, reason,
            )

        # ── Record accurate close ────────────────────────────────────
        if pos.direction == "LONG":
            gross_pnl = (est_exit - pos.entry_price) * pos.quantity
        else:
            gross_pnl = (pos.entry_price - est_exit) * pos.quantity

        # Commission calculation
        if exchange_reported_pnl is not None:
            # Honor whether the exchange PnL is gross or net (pnl_is_net) rather
            # than assuming net — a gross value (netProfit==0 fallback / fetch_my
            # _trades paths) otherwise dropped the fees and overstated realized
            # PnL. Mirrors _close_position_inner.
            is_limit_entry = getattr(pos, 'order_type', '') == 'limit'
            entry_fee_pct = CONFIG.risk.maker_fee_pct if is_limit_entry else CONFIG.risk.taker_fee_pct
            gross_pnl, net_pnl, commission = self._reconcile_exchange_close_pnl(
                exchange_reported_pnl,
                float((close_data or {}).get("fees", 0.0) or 0.0),
                bool((close_data or {}).get("pnl_is_net", False)),
                entry_notional=pos.entry_price * pos.quantity,
                entry_fee_pct=entry_fee_pct,
            )
        else:
            entry_notional = pos.entry_price * pos.quantity
            exit_notional = est_exit * pos.quantity
            is_limit_entry = getattr(pos, 'order_type', '') == 'limit'
            entry_fee = CONFIG.risk.maker_fee_pct if is_limit_entry else CONFIG.risk.taker_fee_pct
            exit_fee = CONFIG.risk.taker_fee_pct
            commission = (entry_notional * entry_fee / 100.0) + (exit_notional * exit_fee / 100.0)
            net_pnl = gross_pnl - commission

        pos.close_reason = reason
        pos.status = "closed"
        pos.close_price = est_exit
        pos.gross_pnl = round(gross_pnl, 4)
        pos.commission = round(commission, 4)
        pos.pnl_usd = round(net_pnl, 4)
        pos.closed_at = datetime.now(UTC)
        # Provenance: how this close was sourced — "ticker_fallback" flags a
        # record whose exit/PnL are inferred, not exchange-authoritative, so a
        # future forensic pass can tell a fabricated record from a real fill.
        pos.fill_source = fill_source

        self._append_closed_trade(pos)
        self._save_positions()
        self._fire_position_closed(pos)

        pnl_str = f"+${net_pnl:.4f}" if net_pnl >= 0 else f"-${abs(net_pnl):.4f}"
        pnl_pct = ((est_exit - pos.entry_price) / pos.entry_price * 100) if pos.entry_price else 0
        if pos.direction == "SHORT":
            pnl_pct = -pnl_pct
        lev = pos.leverage or 1
        pnl_pct_margin = pnl_pct * lev
        hold_secs = (pos.closed_at - pos.opened_at).total_seconds() if pos.closed_at and pos.opened_at else 0
        if hold_secs < 3600:
            hold_str = f"{hold_secs / 60:.0f}m"
        elif hold_secs < 86400:
            hold_str = f"{hold_secs / 3600:.1f}h"
        else:
            hold_str = f"{hold_secs / 86400:.1f}d"

        if lev > 1:
            pnl_pct_str = f"{pnl_pct_margin:+.2f}% margin / {pnl_pct:+.2f}% notional, {lev}×"
        else:
            pnl_pct_str = f"{pnl_pct:+.2f}%"

        close_msg = (
            f"CLOSED {pos.direction} {pos.symbol} ({reason})\n"
            f"Entry: ${pos.entry_price:,.4f} → Exit: ${est_exit:,.4f}\n"
            f"PnL: {pnl_str} ({pnl_pct_str}) | Fees: ${commission:.2f} | Hold: {hold_str}\n"
            f"Fill source: {fill_source}"
        )

        self._last_close_data = {
            "symbol": pos.symbol,
            "direction": pos.direction,
            "reason": reason,
            "entry": pos.entry_price,
            "exit": est_exit,
            "pnl_pct": pnl_pct,
            "pnl_pct_margin": pnl_pct_margin,
            "pnl_usd": round(net_pnl, 4),
            "gross_pnl": round(gross_pnl, 4),
            "fees": round(commission, 4),
            "exchange_fees": 0,
            "size_usd": round(pos.cost_usd, 2) if pos.cost_usd > 0 else round(pos.entry_price * pos.quantity, 2),
            "leverage": lev,
            "hold_time": hold_str,
            "confirmed": True,
            "close_order_id": "",
        }

        audit(trade_log,
              f"Position already closed on exchange — recorded: {pos.symbol} net=${net_pnl:.4f} ({fill_source})",
              action="live_close_25227", result="CLOSED",
              data={
                  "trade_id": pos.trade_id, "reason": reason,
                  "entry": pos.entry_price, "exit": est_exit,
                  "pnl_usd": round(net_pnl, 4),
                  "gross_pnl": round(gross_pnl, 4),
                  "commission": round(commission, 4),
                  "fill_source": fill_source,
              })

        return close_msg

    # ── v3 Flash Close (UTA endpoint, no marginMode needed) ──

    async def _flash_close_position(self, pos: LivePosition) -> dict | None:
        """Close a position using Bitget v3 UTA Close All Positions endpoint.

        POST /api/v3/trade/close-positions
        Uses `category` + `symbol` + `posSide`. No marginMode required.
        Rate limit: 5 req/sec/UID.

        Returns the exchange response dict on success, None on failure.
        """
        import json as _json

        from bot.core.bitget_v3_client import BitgetV3Client

        bitget_symbol = pos.symbol.replace("/USDT", "USDT").replace(":USDT", "")
        pos_side = "long" if pos.direction == "LONG" else "short"

        path = "/api/v3/trade/close-positions"
        body_dict = {
            "category": "USDT-FUTURES",
            "symbol": bitget_symbol,
            "posSide": pos_side,
        }

        try:
            # Signing/transport via BitgetV3Client (offloaded to a thread; raises
            # on error so the except below recovers the JSON error body off the
            # HTTPError exactly as before).
            result = await asyncio.to_thread(
                BitgetV3Client.from_config().request, "POST", path, body_dict)
        except Exception as e:
            if hasattr(e, 'read'):
                try:
                    result = _json.loads(e.read().decode())
                except Exception:
                    logger.warning("Flash close failed for %s: %s", pos.symbol, e)
                    return None
            else:
                logger.warning("Flash close failed for %s: %s", pos.symbol, e)
                return None

        if result.get("code") == "00000":
            data = result.get("data", {})
            close_list = data.get("list", [])
            # v3 returns list items with orderId, clientOid, code, msg
            success = [item for item in close_list if not item.get("code") or item.get("code") == "00000"]
            failed = [item for item in close_list if item.get("code") and item.get("code") != "00000"]
            audit(trade_log,
                  f"v3 flash close for {pos.symbol}: {len(success)} success, {len(failed)} fail",
                  action="flash_close_v3", result="OK",
                  data={"symbol": bitget_symbol, "posSide": pos_side,
                        "close_list": close_list})
            return cast(Optional[dict], result)
        else:
            error_code = result.get("code", "")
            error_msg = result.get("msg", str(result))
            audit(trade_log,
                  f"v3 flash close failed for {pos.symbol}: {error_code} — {error_msg}",
                  action="flash_close_v3", result="FAIL",
                  data={"symbol": bitget_symbol, "response": str(result)[:300]})
            return None

    async def _sync_sl_tp_from_exchange(self, pos: LivePosition) -> bool:
        """Read SL/TP order IDs directly from the exchange position data.

        Uses v2 Get All Positions endpoint which returns:
          takeProfit, stopLoss, takeProfitId, stopLossId

        Updates the local LivePosition in-place.
        Returns True if SL/TP info was updated.
        """
        try:
            exchange = await self._get_exchange()
            ccxt_sym = pos.symbol if ":USDT" in pos.symbol else f"{pos.symbol}:USDT"
            positions = await exchange.fetch_positions(
                [ccxt_sym], params={"productType": "USDT-FUTURES"})

            for p in positions:
                if abs(float(p.get("contracts", 0) or 0)) <= 0:
                    continue
                info = p.get("info", {})

                # v2 position data includes SL/TP info directly
                tp_price = float(info.get("takeProfit") or 0)
                sl_price = float(info.get("stopLoss") or 0)
                tp_id = info.get("takeProfitId") or ""
                sl_id = info.get("stopLossId") or ""

                updated = False
                if tp_price > 0 and pos.take_profit != tp_price:
                    pos.take_profit = tp_price
                    updated = True
                if sl_price > 0 and pos.stop_loss != sl_price:
                    pos.stop_loss = sl_price
                    updated = True
                if tp_id and pos.tp_order_id != tp_id:
                    pos.tp_order_id = tp_id
                    updated = True
                if sl_id and pos.sl_order_id != sl_id:
                    pos.sl_order_id = sl_id
                    updated = True

                if updated:
                    self._save_positions()
                    logger.info("Synced SL/TP from exchange for %s: SL=%s TP=%s",
                                pos.symbol, sl_price or "none", tp_price or "none")
                return updated
        except Exception as exc:
            logger.debug("_sync_sl_tp_from_exchange failed for %s: %s", pos.symbol, exc)
        return False

    # ── Account info ─────────────────────────────────────────────

    async def fetch_balance(self) -> dict:
        """Fetch USDT balance and all spot holdings from Bitget.

        Returns 'equity' (includes unrealized PnL) when available from the
        exchange response; falls back to 'total' (wallet balance only).
        The 'total' key is always the equity-aware value for display purposes.
        """
        try:
            exchange = await self._get_exchange()
            balance = await exchange.fetch_balance()
            usdt = balance.get("USDT", {})

            # ── Extract equity from raw Bitget response ──
            # Bitget USDT-FUTURES returns equity/usdtEquity/accountEquity in
            # the raw info, which includes unrealized PnL.  ccxt's 'total'
            # field is only wallet balance (free + used) and excludes unrealized.
            wallet_total = float(usdt.get("total", 0))
            equity = wallet_total  # default: wallet balance
            raw_info = balance.get("info", {})
            raw_data = raw_info.get("data", []) if isinstance(raw_info, dict) else []
            if isinstance(raw_data, dict):
                raw_data = [raw_data]
            for item in (raw_data if isinstance(raw_data, list) else []):
                if not isinstance(item, dict):
                    continue
                # Try multiple field names Bitget uses for equity
                for key in ("usdtEquity", "accountEquity", "equity"):
                    val = item.get(key)
                    if val is not None:
                        try:
                            eq_val = float(val)
                            if eq_val > 0:
                                equity = eq_val
                                break
                        except (ValueError, TypeError):
                            continue
                if equity != wallet_total:
                    break

            # Collect all non-zero spot holdings
            holdings = []
            for asset, info in balance.items():
                if asset in ("info", "free", "used", "total", "timestamp", "datetime"):
                    continue
                total_val = float(info.get("total", 0) if isinstance(info, dict) else 0)
                if total_val > 0 and asset != "USDT":
                    holdings.append({
                        "asset": asset,
                        "total": total_val,
                        "free": float(info.get("free", 0) if isinstance(info, dict) else 0),
                    })

            return {
                "free": float(usdt.get("free", 0)),
                "used": float(usdt.get("used", 0)),
                "total": equity,  # equity-aware value for display
                "wallet_total": wallet_total,  # raw wallet balance
                "holdings": holdings,
            }
        except Exception as exc:
            return {"error": str(exc), "free": 0, "used": 0, "total": 0, "holdings": []}

    @property
    def open_positions(self) -> list[LivePosition]:
        return [p for p in self._positions.values() if p.status in ("open", "pending_fill")]

    @property
    def closed_positions(self) -> list[LivePosition]:
        """All closed trades: in-memory + persisted from disk."""
        in_mem = [p for p in self._positions.values() if p.status == "closed"]
        # Merge: persisted closed trades + any in-memory closures not yet persisted
        seen_ids = {p.trade_id for p in self._closed_trades}
        merged = list(self._closed_trades)
        for p in in_mem:
            if p.trade_id not in seen_ids:
                merged.append(p)
        return merged

    @property
    def total_exposure_usd(self) -> float:
        return sum(p.cost_usd for p in self.open_positions)

    def status_summary(self) -> str:
        """Human-readable status."""
        open_pos = self.open_positions
        closed = self.closed_positions
        total_pnl = sum(p.pnl_usd or 0 for p in closed)
        return (
            f"Open: {len(open_pos)} | Closed: {len(closed)} | "
            f"Exposure: ${self.total_exposure_usd:.2f} | "
            f"Realized PnL: ${total_pnl:.4f}"
        )

    # ── Balance cache invalidation callback ────────────────────────

    @staticmethod
    def _missing_classic_legs(sl_id, tp_id, live_ids) -> tuple[bool, bool]:
        """Given stored classic SL/TP order IDs and the set of order IDs that are
        currently live on the exchange, return (sl_missing, tp_missing). A stored
        ID that is no longer live means that protective leg was lost (filled /
        cancelled while offline) and must be re-placed. A falsy stored ID counts
        as missing."""
        live = {str(x) for x in (live_ids or set())}
        sl_missing = (not sl_id) or (str(sl_id) not in live)
        tp_missing = (not tp_id) or (str(tp_id) not in live)
        return sl_missing, tp_missing

    async def _live_protective_order_ids(self, pos):
        """Best-effort set of order IDs currently protecting `pos` on the
        exchange: open plan/trigger orders for the symbol (classic two-order
        SL/TP live here) PLUS the position-attached stopLossId / takeProfitId.

        Returns the union set on success, or None when the exchange could not be
        queried at all (BOTH sources errored) — the caller then trusts the stored
        IDs as before (fail-open), so a transient query failure never triggers a
        spurious re-placement."""
        try:
            exchange = await self._get_exchange()
        except Exception as exc:
            logger.debug("_live_protective_order_ids: no exchange for %s: %s", pos.symbol, exc)
            return None
        ccxt_sym = pos.symbol if ":USDT" in pos.symbol else f"{pos.symbol}:USDT"
        ids: set[str] = set()
        plan_ok = False
        pos_ok = False
        # Open plan/trigger orders (classic two-order SL/TP).
        try:
            plans = await exchange.fetch_open_orders(
                ccxt_sym, params={"productType": "USDT-FUTURES", "isPlan": "plan_order"})
            for o in (plans or []):
                oid = o.get("id") or (o.get("info", {}) or {}).get("orderId")
                if oid:
                    ids.add(str(oid))
            plan_ok = True
        except Exception as exc:
            logger.debug("plan-order fetch failed for %s: %s", pos.symbol, exc)
        # Position-attached SL/TP IDs (accounts that attach protection to the position).
        try:
            positions = await exchange.fetch_positions(
                [ccxt_sym], params={"productType": "USDT-FUTURES"})
            for p in (positions or []):
                info = p.get("info", {}) or {}
                for k in ("stopLossId", "takeProfitId"):
                    v = info.get(k)
                    if v:
                        ids.add(str(v))
            pos_ok = True
        except Exception as exc:
            logger.debug("position fetch failed for %s: %s", pos.symbol, exc)
        if not (plan_ok or pos_ok):
            return None  # couldn't verify either source → fail-open
        return ids

    async def verify_and_fix_sltp(self) -> None:
        """Verify all open positions have SL/TP on exchange and re-place if missing.

        Called on startup and periodically. For each open position, attempts
        to place SL/TP if the stored order IDs look invalid (same ID for both,
        or empty). The v3 place-strategy-order is idempotent — placing over
        an existing order just returns the existing order ID.
        """
        open_pos = [p for p in self._positions.values() if p.status == "open"]
        if not open_pos:
            return

        exchange = await self._get_exchange()
        fixed = 0
        for pos in open_pos:
            # Check if SL/TP IDs look valid
            needs_fix = False
            if not pos.sl_order_id and not pos.tp_order_id:
                needs_fix = True
            elif pos.stop_loss <= 0 or pos.take_profit <= 0:
                continue  # No SL/TP levels to place
            elif pos.sl_order_id == pos.tp_order_id:
                # Same ID for both is normal for v3 combined orders — but re-place
                # to ensure they're actually on exchange (could be stale from a
                # restart). The v3 API is idempotent so this is safe.
                needs_fix = True
            elif getattr(CONFIG.execution, "verify_classic_sltp_on_restart", False):
                # Distinct, present classic IDs (two SEPARATE orders). The stored
                # IDs alone don't prove the legs are still live — a leg lost while
                # offline (filled / cancelled on-venue) leaves the position
                # half-protected. Verify each leg against the exchange and re-place
                # the pair if either is gone (placement cancels survivors first).
                live_ids = await self._live_protective_order_ids(pos)
                if live_ids is not None:  # None = couldn't verify → trust stored IDs
                    sl_missing, tp_missing = self._missing_classic_legs(
                        pos.sl_order_id, pos.tp_order_id, live_ids)
                    if sl_missing or tp_missing:
                        needs_fix = True
                        audit(trade_log,
                              f"Classic SL/TP leg missing on exchange for {pos.symbol} "
                              f"(sl_missing={sl_missing} tp_missing={tp_missing}) — re-placing",
                              action="startup_sltp_verify", result="LEG_MISSING",
                              data={"trade_id": pos.trade_id, "symbol": pos.symbol,
                                    "sl_order_id": pos.sl_order_id,
                                    "tp_order_id": pos.tp_order_id})

            if needs_fix and pos.stop_loss > 0 and pos.take_profit > 0:
                direction = Direction.LONG if pos.direction == "LONG" else Direction.SHORT
                try:
                    sl_id, tp_id = await self._place_sl_tp(
                        exchange, pos.symbol, direction,
                        pos.quantity, pos.stop_loss, pos.take_profit
                    )
                    if sl_id:
                        pos.sl_order_id = sl_id
                    if tp_id:
                        pos.tp_order_id = tp_id
                    if sl_id or tp_id:
                        fixed += 1
                        audit(trade_log,
                              f"Startup SL/TP fix: {pos.symbol} sl={sl_id} tp={tp_id}",
                              action="startup_sltp_fix", result="FIXED",
                              data={"trade_id": pos.trade_id, "symbol": pos.symbol,
                                    "sl": pos.stop_loss, "tp": pos.take_profit})
                    else:
                        logger.warning(
                            "SL/TP placement returned no IDs for %s — position may be UNPROTECTED",
                            pos.symbol)
                except Exception as exc:
                    logger.warning("Startup SL/TP fix failed for %s: %s", pos.symbol, exc)

        if fixed > 0:
            self._save_positions()
            logger.info("Startup SL/TP verification: fixed %d/%d positions", fixed, len(open_pos))

    def _fire_position_closed(self, pos: LivePosition) -> None:
        """Notify listeners (engine) that a position was closed so balance cache refreshes."""
        if self.on_position_closed:
            try:
                self.on_position_closed(pos)
            except Exception:
                pass  # Non-critical — don't break close flow

    # ── F-07 FIX: Position persistence ──────────────────────────────

    def _save_positions(self) -> None:
        """Persist open positions to disk so they survive restarts.

        Safety: uses atomic write (tmp + rename) and keeps a .bak copy
        to prevent data loss from crashes mid-write.
        """
        try:
            data: dict[str, Any] = {}
            for tid, pos in self._positions.items():
                if pos.status not in ("open", "pending_fill"):
                    continue
                data[tid] = {
                    "trade_id": pos.trade_id,
                    "symbol": pos.symbol,
                    "direction": pos.direction,
                    "entry_price": pos.entry_price,
                    "quantity": pos.quantity,
                    "cost_usd": pos.cost_usd,
                    "stop_loss": pos.stop_loss,
                    "take_profit": pos.take_profit,
                    "leverage": pos.leverage,
                    "is_spot": pos.is_spot,
                    "sl_order_id": pos.sl_order_id,
                    "tp_order_id": pos.tp_order_id,
                    "opened_at": pos.opened_at.isoformat() if pos.opened_at else None,
                    "status": pos.status,
                    "trailing_state": pos.trailing_state,
                    "order_type": pos.order_type,
                    "limit_order_id": pos.limit_order_id,
                    "atr_at_entry": pos.atr_at_entry,
                    "close_reason": pos.close_reason,
                }
            path = Path(self._positions_file)
            path.parent.mkdir(parents=True, exist_ok=True)

            # Keep backup of non-empty file before overwriting
            if path.exists():
                try:
                    existing = path.read_text().strip()
                    if existing and existing != "{}":
                        bak = str(path) + ".bak"
                        import shutil
                        shutil.copy2(str(path), bak)
                except Exception:
                    pass

            tmp = str(path) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2, default=str)
                # H-05 FIX: fsync before atomic rename to guarantee durability
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, str(path))
            # M-06 FIX: prune closed entries from in-memory dict
            self._positions = {k: v for k, v in self._positions.items()
                               if v.status in ("open", "pending_fill")}
            # H-04 FIX: prune close_locks for trade_ids no longer in positions
            stale_lock_ids = [tid for tid in self._close_locks if tid not in self._positions]
            for tid in stale_lock_ids:
                self._close_locks.pop(tid, None)
        except Exception as exc:
            logger.error("Failed to save live positions: %s", exc)
            self._persistence_broken = True

    def _load_positions(self) -> None:
        """Load persisted positions on startup.

        Falls back to .bak file if main file is empty or corrupt.
        """
        path = Path(self._positions_file)
        bak_path = Path(str(path) + ".bak")

        # Try main file first, fall back to backup
        for source in [path, bak_path]:
            if not source.exists():
                continue
            try:
                with open(source, "r") as f:
                    data = json.load(f)
                if not data:
                    # Empty dict — try backup
                    if source == path and bak_path.exists():
                        audit(trade_log,
                              "Main positions file is empty, trying backup",
                              action="load_positions", result="FALLBACK_TO_BAK")
                        continue
                    return
                for tid, pdata in data.items():
                    opened_at = datetime.fromisoformat(pdata["opened_at"]) if pdata.get("opened_at") else datetime.now(UTC)
                    self._positions[tid] = LivePosition(
                        trade_id=pdata["trade_id"],
                        symbol=pdata["symbol"],
                        direction=pdata["direction"],
                        entry_price=float(pdata["entry_price"]),
                        quantity=float(pdata["quantity"]),
                        cost_usd=float(pdata["cost_usd"]),
                        stop_loss=float(pdata["stop_loss"]),
                        take_profit=float(pdata["take_profit"]),
                        leverage=int(pdata.get("leverage", 1)),
                        is_spot=bool(pdata.get("is_spot", False)),
                        sl_order_id=pdata.get("sl_order_id"),
                        tp_order_id=pdata.get("tp_order_id"),
                        opened_at=opened_at,
                        status=pdata.get("status", "open"),
                        trailing_state=pdata.get("trailing_state"),
                        order_type=pdata.get("order_type", "market"),
                        limit_order_id=pdata.get("limit_order_id"),
                        atr_at_entry=float(pdata.get("atr_at_entry", 0)),
                        close_reason=pdata.get("close_reason"),
                    )
                source_label = "backup" if source == bak_path else "disk"
                if self._positions:
                    audit(trade_log, f"Loaded {len(self._positions)} live positions from {source_label}",
                          action="load_positions", result="OK")
                    # Startup recovery: reset any positions stuck in "closing" status.
                    # The close order may or may not have succeeded on the exchange —
                    # resetting to "open" lets reconcile_positions() re-check and handle.
                    # Flagged in _recovered_from_closing so check_positions()'s local
                    # SL/TP/time-stop heuristics defer to reconcile instead of racing
                    # it with a second, redundant close attempt (see that set's
                    # docstring in __init__ for the full incident this guards against).
                    for tid, p in self._positions.items():
                        if p.status == "closing":
                            audit(trade_log,
                                  f"Startup recovery: position {tid} ({p.symbol}) stuck in 'closing' — resetting to 'open'",
                                  action="load_positions", result="RECOVERY")
                            p.status = "open"
                            self._recovered_from_closing.add(tid)
                return
            except Exception as exc:
                audit(trade_log, f"Failed to load positions from {source}: {exc}",
                      action="load_positions", result="ERROR")
                continue

    # ── F-14 FIX: Closed trades persistence ───────────────────────

    def _append_closed_trade(self, pos: LivePosition) -> None:
        """Append a closed trade to the persisted closed trades file.

        Deduplicates by trade_id: if a record with the same trade_id already
        exists, it is replaced (the newer close has more accurate data).
        This prevents the triple/double-counting bug where reconciliation,
        manual close, and limit expiry all append independently for the
        same underlying position.
        """
        # ── Dedup: replace existing record with same trade_id ──
        existing_idx = None
        for idx, t in enumerate(self._closed_trades):
            if t.trade_id == pos.trade_id:
                existing_idx = idx
                break
        if existing_idx is not None:
            self._closed_trades[existing_idx] = pos
            logger.info("Replaced existing closed trade record: %s", pos.trade_id)
        else:
            # ── Cross-record backstop (live incident 2026-07-07) ──
            # trade_id-only dedup misses a second record for the SAME close
            # minted under a different id (adoption sweeps / clientOid drift).
            # The booking sites guard this upstream; keep a last-resort check
            # here so no future path can persist a double-counted PnL row.
            if self._is_duplicate_close_booking(pos):
                logger.info(
                    "Skipped duplicate closed-trade row: %s %s (trade %s) — "
                    "same close already recorded under another id",
                    pos.symbol, pos.direction, pos.trade_id)
                return
            self._closed_trades.append(pos)
        # Cap to prevent unbounded growth
        if len(self._closed_trades) > _MAX_CLOSED_TRADES:
            self._closed_trades = self._closed_trades[-_MAX_CLOSED_TRADES:]
        self._save_closed_trades()

    def _save_closed_trades(self) -> None:
        """Persist all closed trades to disk."""
        try:
            data = []
            for pos in self._closed_trades:
                data.append({
                    "trade_id": pos.trade_id,
                    "symbol": pos.symbol,
                    "direction": pos.direction,
                    "entry_price": pos.entry_price,
                    "quantity": pos.quantity,
                    "cost_usd": pos.cost_usd,
                    "stop_loss": pos.stop_loss,
                    "take_profit": pos.take_profit,
                    "leverage": pos.leverage,
                    "close_price": pos.close_price,
                    "pnl_usd": pos.pnl_usd,
                    "gross_pnl": pos.gross_pnl,
                    "commission": pos.commission,
                    "opened_at": pos.opened_at.isoformat() if pos.opened_at else None,
                    "closed_at": pos.closed_at.isoformat() if pos.closed_at else None,
                    "status": "closed",
                    "close_reason": pos.close_reason,
                    "origin": pos.origin,
                    "fill_source": pos.fill_source,
                })
            path = Path(self._closed_trades_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(path) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2, default=str)
                # H-05 FIX: fsync before atomic rename to guarantee durability
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, str(path))
        except Exception as exc:
            logger.debug("Failed to save closed trades: %s", exc)

    def _load_closed_trades(self) -> None:
        """Load persisted closed trades on startup."""
        path = Path(self._closed_trades_file)
        if not path.exists():
            return
        try:
            with open(path, "r") as f:
                data = json.load(f)
            for item in data:
                opened_at = datetime.fromisoformat(item["opened_at"]) if item.get("opened_at") else datetime.now(UTC)
                closed_at = datetime.fromisoformat(item["closed_at"]) if item.get("closed_at") else datetime.now(UTC)
                pos = LivePosition(
                    trade_id=item["trade_id"],
                    symbol=item["symbol"],
                    direction=item["direction"],
                    entry_price=float(item.get("entry_price") or 0),
                    quantity=float(item.get("quantity") or 0),
                    cost_usd=float(item.get("cost_usd") or 0),
                    stop_loss=float(item.get("stop_loss") or 0),
                    take_profit=float(item.get("take_profit") or 0),
                    leverage=int(item.get("leverage") or 1),
                    close_price=float(item.get("close_price") or 0),
                    pnl_usd=float(item.get("pnl_usd") or 0),
                    gross_pnl=float(item.get("gross_pnl") or 0) if item.get("gross_pnl") is not None else None,
                    commission=float(item.get("commission") or 0) if item.get("commission") is not None else None,
                    opened_at=opened_at,
                    closed_at=closed_at,
                    status="closed",
                    close_reason=item.get("close_reason"),
                    origin=item.get("origin") or "executed",
                    fill_source=item.get("fill_source"),
                )
                self._closed_trades.append(pos)
            # ── Dedup on load: keep last record per trade_id ──
            if self._closed_trades:
                seen: dict[str, int] = {}
                deduped: list[LivePosition] = []
                for p in self._closed_trades:
                    if p.trade_id in seen:
                        # Replace earlier record with this one (later = more accurate)
                        deduped[seen[p.trade_id]] = p
                    else:
                        seen[p.trade_id] = len(deduped)
                        deduped.append(p)
                if len(deduped) < len(self._closed_trades):
                    logger.info("Deduped closed trades on load: %d -> %d",
                                len(self._closed_trades), len(deduped))
                self._closed_trades = deduped
            # ── Cap to _MAX_CLOSED_TRADES, keeping only the most recent ──
            if len(self._closed_trades) > _MAX_CLOSED_TRADES:
                logger.info("Trimming closed trades on load: %d -> %d",
                            len(self._closed_trades), _MAX_CLOSED_TRADES)
                self._closed_trades = self._closed_trades[-_MAX_CLOSED_TRADES:]
            if self._closed_trades:
                total_pnl = sum(p.pnl_usd or 0 for p in self._closed_trades)
                audit(trade_log,
                      f"Loaded {len(self._closed_trades)} closed trades from disk (total PnL: ${total_pnl:.4f})",
                      action="load_closed_trades", result="OK")
        except Exception as exc:
            audit(trade_log, f"Failed to load closed trades: {exc}",
                  action="load_closed_trades", result="ERROR")

    # ── Exchange reconciliation ───────────────────────────────────

    async def reconcile_positions(self) -> list[str]:
        """Check tracked open positions against exchange. Close any that no longer exist.

        This catches positions closed by exchange-side SL/TP triggers that the bot
        didn't process (e.g., during downtime or missed webhook).
        Returns list of reconciliation messages.
        """
        open_pos = self.open_positions
        if not open_pos:
            return []

        messages = []
        try:
            exchange = await self._get_exchange()

            for pos in open_pos:
                # ── Skip pending_fill (unfilled limit orders) ──
                # A pending_fill position means the limit order was placed but
                # hasn't filled yet — no exchange position exists. Reconciling
                # these creates phantom closes with fake PnL.
                if pos.status == "pending_fill":
                    continue

                # ── RACE FIX: Skip positions being closed by another path ──
                # If close_position() is actively closing this position (status
                # "closing"), reconciliation must NOT also process it — that
                # causes duplicate notifications and conflicting PnL writes.
                if pos.status == "closing":
                    logger.debug("Reconcile: skipping %s — status is 'closing' (another close in progress)", pos.symbol)
                    continue

                # Also skip if position was already closed/removed concurrently
                if pos.status == "closed" or pos.trade_id not in self._positions:
                    continue

                try:
                    # Check if position still exists on exchange
                    ccxt_symbol = pos.symbol if ":USDT" in pos.symbol else f"{pos.symbol}:USDT"
                    positions = await exchange.fetch_positions(
                        [ccxt_symbol],
                        params={"productType": "USDT-FUTURES"},
                    )
                    if self._hedge_mode:
                        # Hedge mode: the account can hold BOTH a long and a short
                        # on the same symbol at once. A side-agnostic check would
                        # see the OPPOSITE side's position and conclude ours still
                        # exists, so a closed long is never reconciled while a short
                        # remains (its PnL never realized). Match the tracked side.
                        # Fail-safe: if ccxt doesn't report a usable side, treat it
                        # as present (broad check) so we never falsely close a live
                        # position. The downstream real-close-data requirement
                        # backstops this either way.
                        _want = pos.direction.lower()

                        def _present(p):
                            if abs(float(p.get("contracts", 0) or 0)) <= 0:
                                return False
                            _side = (p.get("side") or "").lower()
                            return _side == _want or _side not in ("long", "short")

                        has_position = any(_present(p) for p in positions)
                    else:
                        has_position = any(
                            abs(float(p.get("contracts", 0) or 0)) > 0 for p in positions
                        )

                    if not has_position:
                        # ── Duplicate-close hardening (ops tip): serialize with
                        # close_position()'s per-trade lock. The status checks
                        # above are read WITHOUT the lock, so a close that starts
                        # between our snapshot and here could otherwise be
                        # processed twice (double notification / conflicting PnL
                        # writes). If a close is mid-flight, defer to next tick.
                        _rec_lock = self._close_locks.setdefault(pos.trade_id, asyncio.Lock())
                        if _rec_lock.locked():
                            logger.debug("Reconcile: %s close in flight (lock held) — deferring", pos.symbol)
                            continue
                        async with _rec_lock:
                            # ── RACE RE-CHECK: status may have changed during await ──
                            if pos.status in ("closing", "closed") or pos.trade_id not in self._positions:
                                logger.debug("Reconcile: %s status changed to '%s' during fetch — skipping",
                                             pos.symbol, pos.status)
                                continue

                            # ── Double-counting guard ──
                            # If this trade_id is already in closed_trades (e.g., from a
                            # previous bot instance that closed it), skip re-reconciling.
                            # This prevents stale-ticker PnL from overwriting the real close.
                            already_closed = any(
                                ct.trade_id == pos.trade_id for ct in self._closed_trades
                            )
                            if already_closed:
                                audit(trade_log,
                                      f"Reconcile skip: {pos.symbol} (trade {pos.trade_id}) already in closed_trades",
                                      action="reconcile_skip", result="ALREADY_CLOSED")
                                # Remove from open positions — it's already tracked as closed
                                pos.status = "closed"
                                self._save_positions()
                                continue

                            # ── Cross-record duplicate guard ──
                            # The trade_id check above misses a SECOND record for
                            # the same exchange position minted under a different
                            # id (adoption sweeps / clientOid drift). Booking it
                            # here re-uses the same Bitget history row (matched
                            # by entry price) and double-counts everything.
                            if self._is_duplicate_close_booking(pos):
                                self._suppress_duplicate_record(pos)
                                continue

                            # Position no longer on exchange — get real close data from Bitget
                            # SAFETY: retry up to 3 times before giving up
                            close_data = None
                            for _retry in range(3):
                                close_data = await self._fetch_bitget_close_data(pos)
                                if close_data and close_data["close_price"] > 0:
                                    break
                                if _retry < 2:
                                    import asyncio as _aio
                                    await _aio.sleep(2)  # brief pause before retry

                            if close_data and close_data["close_price"] > 0:
                                est_exit = close_data["close_price"]
                                reason = close_data["reason"]
                                fill_source = close_data["source"]
                                exchange_reported_pnl = close_data["pnl"]
                            else:
                                # SAFETY: do NOT close with ticker fallback.
                                # Mark for retry on next tick — exchange data will
                                # become available once Bitget history propagates.
                                _retries = getattr(pos, '_reconcile_retries', 0) + 1
                                setattr(pos, "_reconcile_retries", _retries)
                                if _retries <= 10:
                                    logger.warning(
                                        "Reconcile: %s not on exchange but no close data yet "
                                        "(retry %d/10). Will retry next tick.",
                                        pos.symbol, _retries)
                                    continue  # skip closing — try again next cycle
                                # After 10 retries, use ticker as absolute last resort
                                logger.warning(
                                    "Reconcile: %s — exhausted %d retries, falling back to ticker",
                                    pos.symbol, _retries)
                                try:
                                    ticker = await exchange.fetch_ticker(ccxt_symbol)
                                    est_exit = float(ticker.get("last", 0) or 0)
                                except Exception:
                                    est_exit = 0
                                if est_exit <= 0:
                                    est_exit = pos.entry_price  # absolute fallback
                                reason = self._infer_close_reason(pos, est_exit)
                                fill_source = f"ticker_fallback_after_{_retries}_retries"
                                exchange_reported_pnl = None

                            # Compute PnL — prefer exchange-reported profit (source of truth)
                            if exchange_reported_pnl is not None:
                                pnl = exchange_reported_pnl
                                fill_source = fill_source + "+exchange_pnl"
                            elif pos.direction == "LONG":
                                pnl = (est_exit - pos.entry_price) * pos.quantity
                            else:
                                pnl = (pos.entry_price - est_exit) * pos.quantity

                            pos.close_reason = reason
                            pos.status = "closed"
                            pos.close_price = est_exit

                            # ── Use exchange-reported PnL when available (most accurate) ──
                            if exchange_reported_pnl is not None:
                                # Honor pnl_is_net (gross vs net) instead of
                                # assuming net — otherwise fees are dropped and
                                # net PnL overstated on SL/TP-triggered closes.
                                # Mirrors _close_position_inner.
                                _is_limit = getattr(pos, 'order_type', '') == 'limit'
                                _entry_fee_pct = CONFIG.risk.maker_fee_pct if _is_limit else CONFIG.risk.taker_fee_pct
                                gross_pnl, net_pnl, commission = self._reconcile_exchange_close_pnl(
                                    exchange_reported_pnl,
                                    float((close_data or {}).get("fees", 0.0) or 0.0),
                                    bool((close_data or {}).get("pnl_is_net", False)),
                                    entry_notional=pos.entry_price * pos.quantity,
                                    entry_fee_pct=_entry_fee_pct,
                                )
                                pnl = gross_pnl
                                logger.info("Using exchange-reported PnL for %s: $%.4f",
                                            pos.symbol, net_pnl)
                            else:
                                # Deduct commission on reconciled close (same as manual close)
                                entry_notional = pos.entry_price * pos.quantity
                                exit_notional = est_exit * pos.quantity
                                # GETCLAW: maker/taker fee split
                                is_limit_entry = getattr(pos, 'order_type', '') == 'limit'
                                entry_fee = CONFIG.risk.maker_fee_pct if is_limit_entry else CONFIG.risk.taker_fee_pct
                                exit_fee = CONFIG.risk.taker_fee_pct  # SL/TP triggers = market = taker
                                commission = (entry_notional * entry_fee / 100.0) + (exit_notional * exit_fee / 100.0)
                                gross_pnl = pnl
                                net_pnl = gross_pnl - commission
                            pos.gross_pnl = round(gross_pnl, 4)
                            pos.commission = round(commission, 4)
                            pos.pnl_usd = round(net_pnl, 4)
                            pos.closed_at = datetime.now(UTC)

                            self._save_positions()
                            self._append_closed_trade(pos)
                            # Invalidate balance cache on reconciled close
                            self._fire_position_closed(pos)

                            pnl_str = f"+${net_pnl:.4f}" if net_pnl >= 0 else f"-${abs(net_pnl):.4f}"
                            pnl_pct = ((est_exit - pos.entry_price) / pos.entry_price * 100) if pos.entry_price else 0
                            if pos.direction == "SHORT":
                                pnl_pct = -pnl_pct
                            hold_secs = (pos.closed_at - pos.opened_at).total_seconds() if pos.closed_at and pos.opened_at else 0
                            if hold_secs < 3600:
                                hold_str = f"{hold_secs / 60:.0f}m"
                            elif hold_secs < 86400:
                                hold_str = f"{hold_secs / 3600:.1f}h"
                            else:
                                hold_str = f"{hold_secs / 86400:.1f}d"
                            msg = (
                                f"RECONCILED {pos.direction} {pos.symbol} ({reason})\n"
                                f"Entry: ${pos.entry_price:,.4f} -> Exit: ~${est_exit:,.4f}\n"
                                f"PnL: {pnl_str} ({pnl_pct:+.2f}%) | Hold: {hold_str}"
                            )
                            self._last_close_data = {
                                "symbol": pos.symbol,
                                "direction": pos.direction,
                                "reason": reason,
                                "entry": pos.entry_price,
                                "exit": est_exit,
                                "pnl_pct": pnl_pct,
                                "pnl_pct_margin": pnl_pct * (pos.leverage or 1),
                                "pnl_usd": round(net_pnl, 4),
                                "gross_pnl": round(gross_pnl, 4),
                                "fees": round(commission, 4),
                                "size_usd": round(pos.cost_usd, 2) if pos.cost_usd > 0 else round(pos.entry_price * pos.quantity, 2),
                                "leverage": pos.leverage or 1,
                                "hold_time": hold_str,
                            }
                            # Real incident: a position stuck in "closing" across a
                            # restart got reset to "open" (see _load_positions()),
                            # its local SL/TP heuristic then submitted a SECOND,
                            # redundant close order (priced off a stale ticker,
                            # since the real close already happened), and THIS
                            # reconcile pass discovered the true close and sent
                            # its own notification -- the user saw two conflicting
                            # "closed" messages for one trade. The first was
                            # already noise from a doomed redundant order; suppress
                            # the notification here too since the user almost
                            # certainly already saw a close message for this trade
                            # before the process restarted.
                            was_recovered = pos.trade_id in self._recovered_from_closing
                            self._recovered_from_closing.discard(pos.trade_id)
                            if not was_recovered:
                                messages.append(msg)

                            audit(trade_log,
                                  f"Position reconciled (closed on exchange): {pos.symbol} PnL=${pnl:.4f}",
                                  action="reconcile_close", result="CLOSED",
                                  data={
                                      "trade_id": pos.trade_id, "reason": reason,
                                      "entry": pos.entry_price, "exit": est_exit,
                                      "pnl_usd": round(pnl, 4),
                                      "notification_suppressed": was_recovered,
                                  })

                    else:
                        # Position still on exchange — confirmed genuinely
                        # open, so a startup-recovered "closing" position is
                        # no longer ambiguous; resume normal local monitoring.
                        self._recovered_from_closing.discard(pos.trade_id)
                        # Position still on exchange — sync SL/TP from exchange data
                        for ep in positions:
                            if abs(float(ep.get("contracts", 0) or 0)) > 0:
                                info = ep.get("info", {})
                                ex_sl = float(info.get("stopLoss") or 0)
                                ex_tp = float(info.get("takeProfit") or 0)
                                ex_sl_id = info.get("stopLossId") or ""
                                ex_tp_id = info.get("takeProfitId") or ""
                                synced = False
                                if ex_sl > 0 and pos.stop_loss != ex_sl:
                                    pos.stop_loss = ex_sl
                                    synced = True
                                if ex_tp > 0 and pos.take_profit != ex_tp:
                                    pos.take_profit = ex_tp
                                    synced = True
                                if ex_sl_id and pos.sl_order_id != ex_sl_id:
                                    pos.sl_order_id = ex_sl_id
                                    synced = True
                                if ex_tp_id and pos.tp_order_id != ex_tp_id:
                                    pos.tp_order_id = ex_tp_id
                                    synced = True
                                if synced:
                                    self._save_positions()
                                break

                except Exception as exc:
                    logger.debug("Reconciliation error for %s: %s", pos.trade_id, exc)

        except Exception as exc:
            logger.debug("Reconciliation error: %s", exc)

        return messages

    def _prune_order_history(self) -> None:
        """F-13 FIX: Cap order history to prevent unbounded growth."""
        if len(self._order_history) > _MAX_ORDER_HISTORY:
            self._order_history = self._order_history[-(_MAX_ORDER_HISTORY // 2):]
