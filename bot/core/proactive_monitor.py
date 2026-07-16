"""
RUNECLAW Proactive Alert Monitor.

Runs as a background coroutine alongside the engine, pushing unsolicited
alerts to the operator when thresholds are crossed:
  - Volume spikes on watched assets
  - Regime flips (TREND → CHOP, etc.)
  - Black-swan detector triggers
  - Circuit breaker state changes
  - Trade SL/TP proximity warnings
  - Macro event approaching

Gated behind /watch on|off toggle per chat. Only sends to authorized
admin users in the allow-list (F-04 compliant).

Safety: the monitor is read-only. It observes engine state and emits
alerts. It never creates trades, modifies risk limits, or bypasses
any gate. Proposal alerts may ATTACH inline action buttons, but those
buttons route to already-guarded handlers (admin re-check + live-amount
recompute happen there) — the monitor itself still moves nothing.
"""

from __future__ import annotations

import asyncio
import html as _html
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from bot.compat import UTC
from typing import Any, Callable, Optional, Set

from bot.config import CONFIG
from bot.utils.logger import audit, system_log

logger = logging.getLogger(__name__)


# ── Alert types ───────────────────────────────────────────────────────

@dataclass
class Alert:
    """A single proactive alert to send to the operator."""
    alert_type: str       # VOLUME_SPIKE, REGIME_FLIP, BLACK_SWAN, CIRCUIT_BREAKER, etc.
    severity: str         # INFO, WARNING, CRITICAL
    title: str            # Short title for the alert
    body: str             # Full message (HTML formatted for Telegram)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    dedup_key: str = ""   # For deduplication (same key = don't re-alert within cooldown)
    idea: Any = None      # optional TradeIdea — enables an attached setup chart
    # Optional inline action buttons as (label, callback_data) pairs. Kept as
    # plain tuples so this core module never imports telegram — the handler's
    # send_fn converts them to an InlineKeyboardMarkup. Callback data must be
    # an already-guarded route (e.g. "yld:s:USDT" re-checks admin + amounts).
    buttons: list = field(default_factory=list)


# ── Alert severity icons ──────────────────────────────────────────────

_SEVERITY_ICON = {
    "INFO": "\U0001f535",       # Blue circle
    "WARNING": "\U0001f7e0",    # Orange circle
    "CRITICAL": "\U0001f534",   # Red circle
}


# ── Proactive Monitor ─────────────────────────────────────────────────

class ProactiveMonitor:
    """Background monitor that generates alerts from engine state.

    Usage:
        monitor = ProactiveMonitor(engine)
        asyncio.create_task(monitor.run(send_fn))

    The send_fn is an async callable(chat_id: str, text: str) -> None
    that sends a Telegram message. The monitor calls it for each alert.
    """

    # How often to check (seconds)
    CHECK_INTERVAL = 30

    # Deduplication cooldown (don't re-alert same event within this window)
    DEDUP_COOLDOWN = 300  # 5 minutes

    def __init__(self, engine) -> None:
        self.engine = engine
        self._enabled_chats: Set[str] = set()   # Chat IDs with /watch on
        # NOTE: the persisted watch list is loaded (and the operator auto-
        # enrolled) by hydrate(), called from start_monitor — NOT here — so a
        # bare ProactiveMonitor(engine) in tests stays empty and deterministic.
        self._running = False
        self._dedup_cache: dict[str, float] = {}  # dedup_key -> last_alert_time

        # State tracking for change detection
        self._last_regime: dict[str, str] = {}    # symbol -> last known regime
        self._last_cb_state: bool = False          # last circuit breaker state
        self._last_state: str = ""                 # last engine FSM state
        self._alerted_signals: set = set()         # signal IDs already alerted
        # Early-warning state (Tier 1a hardening): track the highest drawdown
        # tier already alerted (re-arms only after recovery), WS/health/balance
        # and warning-rate breaker last-states so each transition alerts once.
        self._last_dd_tier: int = 0                # 0=none, 50/75/85 = pct-of-limit tier
        self._last_ws_ok: bool = True              # last WS-connected state (live)
        self._ws_down_since: float = 0.0           # monotonic ts WS first seen down
        self._last_warn_rate: bool = False         # last warning-rate-breaker state
        self._last_tick_degraded: bool = False     # last tick-failure alert state
        self._last_llm_degraded: bool = False       # last LLM-brain-offline state
        # Strangle watchdog: rolling (wall_ts, evaluated, approved, fails_by_gate)
        # snapshots of the risk engine's cumulative counters, plus our own
        # last-alert time (the condition PERSISTS, so the generic 5-min dedup
        # would spam — this re-alerts at most once per window).
        self._strangle_snaps: deque = deque()
        self._last_strangle_alert: float = 0.0
        # Learning readiness: last known per-component state, so a component
        # BECOMING ready alerts exactly once (not every tick it stays ready).
        self._readiness_states: dict[str, str] = {}
        self._readiness_next_check: float = 0.0
        # Idle-cash nudge: when free margin sits stakeable for hours, propose
        # /stake with a confirm button (once per cooldown, re-arms on spend).
        self._idle_since: float = 0.0        # monotonic ts idle threshold first met
        self._last_idle_nudge: float = 0.0   # monotonic ts of the last nudge sent
        # Daily digest: morning plan + evening wrap, sent once per UTC day each.
        self._digest_sent: dict[str, str] = {}   # kind -> "YYYY-MM-DD" last sent
        # Funding-arb paper tracker: hourly background snapshot + per-coin
        # per-day big-spread alert dedup. The send_fn is captured by run()
        # so the background task can dispatch outside the check cycle.
        self._last_arb_snapshot: float = 0.0
        self._arb_alerted: set = set()
        self._arb_send_fn = None
        # Optional async callback(chat_id, idea) -> None to push a setup chart
        # alongside a signal alert. Set via set_chart_fn(); never required.
        self._chart_fn: Optional[Callable] = None

    def set_chart_fn(self, chart_fn) -> None:
        """Register an async callback(chat_id, idea) that pushes a setup chart
        for signal alerts. Optional — alerts work fine without it."""
        self._chart_fn = chart_fn

    def enable_chat(self, chat_id: str) -> None:
        """Enable proactive alerts for a chat."""
        self._enabled_chats.add(str(chat_id))
        self._save_enabled_chats()
        audit(system_log, f"Proactive alerts enabled for chat {chat_id}",
              action="watch_on", data={"chat_id": chat_id})

    def disable_chat(self, chat_id: str) -> None:
        """Disable proactive alerts for a chat."""
        self._enabled_chats.discard(str(chat_id))
        self._save_enabled_chats()
        audit(system_log, f"Proactive alerts disabled for chat {chat_id}",
              action="watch_off", data={"chat_id": chat_id})

    # ── Watch-list persistence + admin auto-enroll ────────────────
    # The watch list was in-memory only, so every restart silenced CRITICAL
    # safety alerts until someone re-ran /watch on. Persist it and, on a fresh
    # deploy with an empty list, auto-enroll the operator so alerts flow by
    # default. All best-effort / fail-open — a persistence hiccup must never
    # break the monitor.

    def hydrate(self) -> None:
        """Load the persisted watch list and auto-enroll the operator if empty.

        Called once at startup (start_monitor). Kept out of __init__ so a bare
        monitor constructed in tests is deterministically empty.
        """
        self._load_enabled_chats()
        self._maybe_auto_enroll_admin()

    def _watch_state_path(self) -> str:
        try:
            from bot.config import CONFIG
            return CONFIG.proactive_watch_state_file
        except Exception:
            return "data/proactive_watch.json"

    def _load_enabled_chats(self) -> None:
        import json
        import os
        path = self._watch_state_path()
        # Whether a state file already exists distinguishes a FRESH deploy (no
        # file -> auto-enroll the operator) from an operator who explicitly
        # emptied the list (file present but empty -> respect their choice, do
        # NOT re-enroll on every restart).
        self._watch_state_existed = os.path.exists(path)
        try:
            if not self._watch_state_existed:
                return
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            chats = data.get("enabled_chats", []) if isinstance(data, dict) else data
            if isinstance(chats, list):
                self._enabled_chats = {str(c) for c in chats if c not in (None, "")}
        except Exception as exc:
            logger.debug("proactive watch-list load skipped: %s", exc)

    def _save_enabled_chats(self) -> None:
        import json
        import os
        path = self._watch_state_path()
        try:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"enabled_chats": sorted(self._enabled_chats)}, fh)
            os.replace(tmp, path)  # atomic
        except Exception as exc:
            logger.debug("proactive watch-list save skipped: %s", exc)

    def _maybe_auto_enroll_admin(self) -> None:
        """When nobody is watching on a FRESH deploy, enroll the operator chat
        so CRITICAL safety alerts still reach them. Bounded: only fires when no
        state file existed yet (never when the operator explicitly emptied the
        list) and only if TELEGRAM_CHAT_ID is configured."""
        if self._enabled_chats:
            return
        # Operator has interacted before (file present) — respect their empty
        # list instead of re-enrolling every restart.
        if getattr(self, "_watch_state_existed", False):
            return
        try:
            from bot.config import CONFIG
            if not CONFIG.proactive_auto_enroll_admin:
                return
            admin = str(CONFIG.telegram.chat_id or "").strip()
            if admin:
                self._enabled_chats.add(admin)
                self._save_enabled_chats()
                audit(system_log,
                      f"Proactive alerts auto-enrolled operator chat {admin} "
                      f"(empty watch list on startup)",
                      action="watch_auto_enroll", data={"chat_id": admin})
        except Exception as exc:
            logger.debug("proactive admin auto-enroll skipped: %s", exc)

    def is_enabled(self, chat_id: str) -> bool:
        return chat_id in self._enabled_chats

    @property
    def enabled_chat_count(self) -> int:
        return len(self._enabled_chats)

    async def run(self, send_fn) -> None:
        """Main monitor loop. Runs until stopped."""
        self._running = True
        self._arb_send_fn = send_fn
        logger.info("Proactive monitor started")

        while self._running:
            try:
                alerts = self._check_all()
                for alert in alerts:
                    if self._should_send(alert):
                        await self._dispatch(alert, send_fn)
                        self._mark_sent(alert)
            except Exception as exc:
                logger.debug("Monitor check error: %s", exc)

            await asyncio.sleep(self.CHECK_INTERVAL)

    def stop(self) -> None:
        self._running = False

    # ── Alert generation ──────────────────────────────────────────

    def _check_all(self) -> list[Alert]:
        """Run all alert checks and return any triggered alerts."""
        alerts: list[Alert] = []
        alerts.extend(self._check_circuit_breaker())
        alerts.extend(self._check_drawdown_tiers())
        alerts.extend(self._check_tick_failures())
        alerts.extend(self._check_warning_rate_breaker())
        alerts.extend(self._check_llm_degraded())
        alerts.extend(self._check_ws_health())
        alerts.extend(self._check_stale_balance())
        alerts.extend(self._check_macro_calendar_stale())
        alerts.extend(self._check_unprotected_positions())
        alerts.extend(self._check_slippage())
        alerts.extend(self._check_volume_spikes())
        alerts.extend(self._check_black_swan())
        alerts.extend(self._check_state_changes())
        alerts.extend(self._check_trade_signals())
        alerts.extend(self._check_sl_tp_proximity())
        alerts.extend(self._check_time_stops())
        alerts.extend(self._check_signal_strangle())
        alerts.extend(self._check_learning_readiness())
        alerts.extend(self._check_new_listings())
        alerts.extend(self._check_self_audit())
        alerts.extend(self._check_idle_cash())
        alerts.extend(self._check_daily_digest())
        alerts.extend(self._check_parity_digest())
        alerts.extend(self._check_arb_tracker())
        return alerts

    # ── Funding-arb paper tracker: hourly snapshot + big-spread alert ─

    def _check_arb_tracker(self) -> list[Alert]:
        """Once an hour, snapshot cross-venue funding spreads (background
        thread — three public HTTP calls must never block the monitor loop)
        and alert when a coin's spread crosses the alert threshold. The
        tracker is 100% paper: it records and reports, never trades."""
        if os.environ.get("ARB_TRACKER_ENABLED", "true").strip().lower() \
                not in ("1", "true", "yes", "on"):
            return []
        alerts: list[Alert] = []
        try:
            now = time.monotonic()
            interval_s = self._env_f("ARB_SNAPSHOT_MIN", 60.0) * 60
            # 0.0 means "never ran" — fire immediately. (monotonic() starts
            # near zero at boot, so `now - 0 < interval` would wrongly pace
            # out the first snapshot for up to an hour after every restart.)
            if self._last_arb_snapshot and now - self._last_arb_snapshot < interval_s:
                return []
            self._last_arb_snapshot = now
            import asyncio as _aio

            async def _snap_and_alert() -> None:
                try:
                    from bot.core.arb_tracker import (load_snapshots,
                                                      snapshot_opportunities)
                    wrote = await _aio.to_thread(snapshot_opportunities)
                    if not wrote:
                        return
                    threshold = self._env_f("ARB_ALERT_SPREAD_APR", 10.0)
                    snaps = (await _aio.to_thread(load_snapshots))[-wrote:]
                    today = datetime.now(UTC).strftime("%Y-%m-%d")
                    for s in snaps:
                        spread = float(s.get("spread_apr", 0) or 0)
                        key = f"arb_{s.get('base')}_{today}"
                        if spread < threshold or key in self._arb_alerted:
                            continue
                        self._arb_alerted.add(key)
                        alert = Alert(
                            alert_type="FUNDING_ARB", severity="INFO",
                            title="Wide funding spread",
                            body=(f"⚖️ <b>Wide funding spread: "
                                  f"{s.get('base')}</b>\n\n"
                                  f"<code>{spread:.1f}%/yr</code> — long "
                                  f"{s.get('long_venue')} / short "
                                  f"{s.get('short_venue')}.\n"
                                  "<i>Info only — /arb shows the paper "
                                  "tracker; nothing is traded.</i>"),
                            dedup_key=key)
                        if self._should_send(alert) and self._arb_send_fn:
                            await self._dispatch(alert, self._arb_send_fn)
                            self._mark_sent(alert)
                except Exception as exc:
                    logger.debug("arb tracker snapshot failed: %s", exc)

            _aio.get_running_loop().create_task(_snap_and_alert())
        except Exception as exc:
            logger.debug("arb tracker check skipped: %s", exc)
        return alerts

    # ── Weekly live↔backtest parity digest ────────────────────────

    def _check_parity_digest(self) -> list[Alert]:
        """Once a week, surface whether live execution still matches the
        model: realized PF, fee drag vs the modeled commission, win rate.
        Drift here is the earliest sign the backtest no longer describes
        reality. Local file read only — no network, no orders."""
        if os.environ.get("PARITY_DIGEST_ENABLED", "true").strip().lower() \
                not in ("1", "true", "yes", "on"):
            return []
        try:
            now = datetime.now(UTC)
            dow = int(self._env_f("PARITY_DIGEST_DOW", 0))       # 0 = Monday
            hour = int(self._env_f("PARITY_DIGEST_HOUR_UTC", 7))
            week = now.strftime("%G-W%V")
            if now.weekday() != dow or now.hour < hour \
                    or self._digest_sent.get("parity") == week:
                return []
            self._digest_sent["parity"] = week
            from bot.backtest.parity import load_closed_trades, parity_summary
            path = getattr(getattr(self.engine, "live_executor", None),
                           "_closed_trades_file", None)
            if not path:
                return []
            trades = load_closed_trades(path)
            if not trades:
                return []
            s = parity_summary(trades, CONFIG.risk.commission_pct)
            fee_x = s.get("fee_vs_model", 0.0)
            drift = " ⚠️ fees running above model — /parity for the breakdown" \
                if fee_x > 1.5 else ""
            body = (
                "📏 <b>Weekly parity — live vs model</b>\n\n"
                f"Filled trades: <b>{s['trades']}</b> · win rate "
                f"<code>{s['win_rate'] * 100:.0f}%</code> · PF "
                f"<code>{s['pf']:.2f}</code>\n"
                f"Net <code>${s['net_pnl']:+,.2f}</code> · fees "
                f"<code>${s['total_fees']:,.2f}</code> "
                f"(<code>{fee_x:.1f}×</code> the modeled rate)"
                f"{drift}\n\n<i>/parity for the full bucketed report.</i>")
            return [Alert(alert_type="PARITY_DIGEST", severity="INFO",
                          title="Weekly parity digest", body=body,
                          dedup_key=f"parity_{week}")]
        except Exception as exc:
            logger.debug("parity digest skipped: %s", exc)
            return []

    # ── Proactive proposals: idle cash → stake nudge ──────────────

    @staticmethod
    def _env_f(name: str, default: float) -> float:
        try:
            return float(os.environ.get(name, "") or default)
        except (TypeError, ValueError):
            return default

    def _check_idle_cash(self) -> list[Alert]:
        """Propose staking when free margin has sat idle for hours.

        Read-only: the alert only carries a button to the yld:s route, which
        re-checks admin and recomputes/clamps the amount from live balances
        at press time. Re-arms after the cooldown or once the cash is used.
        """
        if os.environ.get("IDLE_CASH_NUDGE_ENABLED", "true").strip().lower() \
                not in ("1", "true", "yes", "on"):
            return []
        try:
            from bot.core.yield_radar import MARGIN_RESERVE_PCT
            cache = getattr(self.engine, "_live_balance_cache", None) or {}
            free = float(cache.get("free", 0) or 0)
            stakeable = free * (1 - MARGIN_RESERVE_PCT)
            threshold = self._env_f("IDLE_CASH_NUDGE_USD", 25.0)
            now = time.monotonic()
            if stakeable < threshold:
                self._idle_since = 0.0   # cash got used — re-arm the timer
                return []
            if not self._idle_since:
                self._idle_since = now
            idle_hours = self._env_f("IDLE_CASH_NUDGE_HOURS", 6.0)
            cooldown_h = self._env_f("IDLE_CASH_NUDGE_COOLDOWN_H", 24.0)
            if (now - self._idle_since) < idle_hours * 3600:
                return []
            if self._last_idle_nudge and \
                    (now - self._last_idle_nudge) < cooldown_h * 3600:
                return []
            self._last_idle_nudge = now
            return [Alert(
                alert_type="IDLE_CASH",
                severity="INFO",
                title="Idle cash could be earning",
                body=(
                    "💤 <b>Idle cash could be earning</b>\n\n"
                    f"≈<code>${stakeable:,.2f}</code> of free margin has sat "
                    f"unused for {idle_hours:.0f}h+ (after the "
                    f"{MARGIN_RESERVE_PCT:.0%} reserve the engine keeps).\n"
                    "Flexible Earn redeems instantly, so it stays recallable.\n\n"
                    "<i>The button recomputes the exact amount from live "
                    "balances — /yield shows current rates, /unstake redeems.</i>"),
                dedup_key="idle_cash_nudge",
                buttons=[("✅ Stake idle USDT", "yld:s:USDT"),
                         ("Not now", "yld:x")],
            )]
        except Exception as exc:
            logger.debug("idle-cash check skipped: %s", exc)
            return []

    # ── Proactive digests: morning plan + evening wrap ────────────

    def _check_daily_digest(self) -> list[Alert]:
        """Send a morning plan and an evening wrap once per UTC day each."""
        if os.environ.get("DAILY_DIGEST_ENABLED", "true").strip().lower() \
                not in ("1", "true", "yes", "on"):
            return []
        alerts: list[Alert] = []
        try:
            now = datetime.now(UTC)
            today = now.strftime("%Y-%m-%d")
            schedule = {
                "brief": int(self._env_f("DAILY_BRIEF_HOUR_UTC", 6)),
                "wrap": int(self._env_f("DAILY_WRAP_HOUR_UTC", 20)),
            }
            for kind, hour in schedule.items():
                if now.hour >= hour and self._digest_sent.get(kind) != today:
                    self._digest_sent[kind] = today
                    body = self._digest_body(kind)
                    if body:
                        alerts.append(Alert(
                            alert_type=f"DAILY_{kind.upper()}",
                            severity="INFO",
                            title=f"Daily {kind}",
                            body=body,
                            dedup_key=f"digest_{kind}_{today}"))
        except Exception as exc:
            logger.debug("daily digest check skipped: %s", exc)
        return alerts

    def _digest_body(self, kind: str) -> str:
        """Compact, truthful engine digest. Everything best-effort — a field
        we can't read is omitted, never invented."""
        e = self.engine
        lines: list[str] = []
        try:
            mode = "LIVE" if CONFIG.is_live() else "PAPER"
        except Exception:
            mode = "?"
        state = str(getattr(e, "state", "") or "").replace("EngineState.", "")

        # Open positions (operator book; live executor first).
        positions = []
        try:
            ex = getattr(e, "live_executor", None)
            if ex is not None and getattr(ex, "open_positions", None):
                positions = list(ex.open_positions)
            elif getattr(e, "portfolio", None) is not None:
                positions = list(e.portfolio.open_positions)
        except Exception:
            pass
        pos_bits = []
        for p in positions[:6]:
            sym = str(getattr(p, "symbol", getattr(p, "asset", "?")))
            sym = sym.replace("/USDT", "").replace(":USDT", "")
            side = str(getattr(p, "direction", getattr(p, "side", "")))[:5].upper()
            pos_bits.append(f"{sym} {side}")

        # Free margin / equity from the venue-aware cache (may be absent).
        equity_bit = ""
        try:
            cache = getattr(e, "_live_balance_cache", None) or {}
            eq = float(cache.get("equity", 0) or 0)
            free = float(cache.get("free", 0) or 0)
            if eq > 0:
                equity_bit = (f"Equity <code>${eq:,.2f}</code> · free margin "
                              f"<code>${free:,.2f}</code>")
        except Exception:
            pass

        if kind == "brief":
            lines.append("🌅 <b>Morning brief — today's plan</b>")
            lines.append(f"Mode <b>{mode}</b>" + (f" · engine <code>{_html.escape(state)}</code>" if state else ""))
            if equity_bit:
                lines.append(equity_bit)
            lines.append(
                f"Carrying <b>{len(positions)}</b> open position(s)"
                + (f": {_html.escape(', '.join(pos_bits))}" if pos_bits else "")
                + " — managing SL/TP and scanning the universe for setups "
                  "at or above the auto-trade confidence gate.")
            lines.append("<i>/status for detail · /whynot SYMBOL to see why "
                         "something isn't being traded.</i>")
        else:
            lines.append("🌙 <b>Evening wrap</b>")
            lines.append(f"Mode <b>{mode}</b>" + (f" · engine <code>{_html.escape(state)}</code>" if state else ""))
            if equity_bit:
                lines.append(equity_bit)
            # Recent closed trades (live book) — count + net, best/worst.
            try:
                ex = getattr(e, "live_executor", None)
                closed = list(getattr(ex, "closed_positions", []) or [])[-20:]
                if closed:
                    net = sum(float(getattr(t, "pnl_usd", 0) or 0) for t in closed)
                    wins = sum(1 for t in closed
                               if float(getattr(t, "pnl_usd", 0) or 0) > 0)
                    lines.append(
                        f"Recent closes: <b>{len(closed)}</b> "
                        f"(<b>{wins}</b> wins) · net <code>${net:+,.2f}</code>")
            except Exception:
                pass
            lines.append(
                f"Still open: <b>{len(positions)}</b>"
                + (f" — {_html.escape(', '.join(pos_bits))}" if pos_bits else ""))
            lines.append("<i>/daily_report for the full report · "
                         "/yield checks what idle cash could earn.</i>")
        return "\n\n".join(lines)

    def _check_learning_readiness(self) -> list[Alert]:
        """Alert when a learner BECOMES validated-and-ready — the moment the
        operator can act on the learning loop instead of remembering to poll
        /readiness. Assessment reads the decision store, so it runs on a slow
        cadence (hourly), not every 30s tick."""
        alerts: list[Alert] = []
        if not CONFIG.analyzer.learning_readiness_alert_enabled:
            return alerts
        now = time.time()
        if now < self._readiness_next_check:
            return alerts
        self._readiness_next_check = now + 3600.0
        try:
            from bot.learning.readiness import assess_readiness, render_report
            assessment = assess_readiness()
        except Exception as exc:
            logger.debug("readiness check failed: %s", exc)
            return alerts
        for name, comp in assessment.get("components", {}).items():
            state = comp.get("state", "?")
            prev = self._readiness_states.get(name)
            self._readiness_states[name] = state
            # First observation seeds the baseline silently; only a genuine
            # transition INTO READY (while not yet applied) alerts.
            if prev is None or state != "READY" or prev == "READY":
                continue
            if comp.get("applied") is True:
                continue
            alerts.append(Alert(
                alert_type="LEARNING_READY",
                severity="INFO",
                title=f"Learning component ready: {name}",
                body=("\U0001f9e0 <b>LEARNING COMPONENT VALIDATED</b>\n"
                      "────────────────\n"
                      f"<b>{name}</b> now clears its evidence bar and is "
                      "ready to apply.\n\n" + render_report(assessment) +
                      "\n\n\U0001f449 /readiness — full report"),
                dedup_key=f"learning_ready_{name}",
            ))
        return alerts

    _CLASS_ICON = {
        "Crypto": "\U0001fa99", "Stock": "\U0001f4c8", "ETF": "\U0001f4ca",
        "Commodity": "\U0001f6e2", "Metal": "⚙️",
        "Pre-IPO": "\U0001f680", "Forex": "\U0001f4b1",
    }

    def _check_new_listings(self) -> list[Alert]:
        """Surface new exchange listings the catalog watch queued during
        scans. New crypto / *STOCK perps already trade automatically; the
        point here is telling the operator the catalog changed — above all
        for bare-ticker TradFi listings that the classifier can only call
        Crypto until a config entry names them."""
        alerts: list[Alert] = []
        try:
            watch = getattr(getattr(self.engine, "scanner", None),
                            "_catalog_watch", None)
            if watch is None:
                return alerts
            events = watch.drain_pending()
            if not events:
                return alerts
            lines = []
            for ev in events[:25]:
                sym = str(ev.get("symbol", "?"))
                cat = str(ev.get("category", "Crypto"))
                icon = self._CLASS_ICON.get(cat, "\U0001fa99")
                vol = float(ev.get("vol_usd", 0.0) or 0.0)
                vol_s = f" · ${vol/1e6:.1f}M/day" if vol > 0 else ""
                lines.append(f"{icon} <code>{sym}</code> — {cat}{vol_s}")
            more = len(events) - 25
            if more > 0:
                lines.append(f"…and {more} more")
            syms = sorted(str(ev.get("symbol", "")) for ev in events)
            alerts.append(Alert(
                alert_type="NEW_LISTINGS",
                severity="INFO",
                title=f"{len(events)} new exchange listing(s)",
                body=("\U0001f195 <b>NEW EXCHANGE LISTINGS</b>\n"
                      "────────────────\n"
                      + "\n".join(lines) +
                      "\n────────────────\n"
                      "New crypto and *STOCK perps join the scan universe "
                      "automatically. If a name above is really a stock/"
                      "commodity/ETF but shows as Crypto, it needs a config "
                      "entry to get the right volume floor and session "
                      "sizing — say the word and I'll add it."),
                dedup_key="new_listings_" + ",".join(syms)[:120],
            ))
        except Exception as exc:
            logger.debug("new-listings check failed: %s", exc)
        return alerts

    def _check_self_audit(self) -> list[Alert]:
        """Deliver the nightly self-audit report the moment a run finishes.
        The audit itself runs in the engine (background task); this check
        only drains its queue — same pattern as the new-listings watch."""
        alerts: list[Alert] = []
        try:
            from bot.core.self_audit import SELF_AUDIT
            for item in SELF_AUDIT.drain_pending():
                report = str(item.get("report", "")).strip()
                if not report:
                    continue
                alerts.append(Alert(
                    alert_type="SELF_AUDIT",
                    severity="INFO",
                    title="Nightly self-audit report",
                    body=report + "\n\n\U0001f449 /audit — re-show this report",
                    dedup_key=f"self_audit_{int(item.get('ts', 0))}",
                ))
        except Exception as exc:
            logger.debug("self-audit check failed: %s", exc)
        return alerts

    def _check_signal_strangle(self) -> list[Alert]:
        """Silent-strangle watchdog: ideas keep flowing but NOTHING has been
        approved for a whole window — the failure shape of a silently latched
        gate. (The soft loss-streak latch ran a production backtest dry for
        ~8 months with zero operator-visible signal: the bot scans, generates
        ideas, and rejects every one.) Names the top rejecting gate so the
        operator knows WHERE the flow died, not just that it died."""
        alerts: list[Alert] = []
        window_s = CONFIG.risk.strangle_alert_hours * 3600.0
        if window_s <= 0:
            return alerts
        try:
            stats = self.engine.risk.eval_stats()
            fails = {k: v.get("failed", 0)
                     for k, v in self.engine.risk.gate_stats().items()}
        except Exception:
            return alerts

        now = time.time()
        snaps = self._strangle_snaps
        snaps.append((now, stats["evaluated"], stats["approved"], fails))
        while snaps and now - snaps[0][0] > 2 * window_s:
            snaps.popleft()

        # Baseline = the newest snapshot that is at least one window old.
        base = None
        for s in snaps:
            if now - s[0] >= window_s:
                base = s
            else:
                break
        if base is None:
            return alerts

        evals_d = stats["evaluated"] - base[1]
        approved_d = stats["approved"] - base[2]
        if evals_d < CONFIG.risk.strangle_min_ideas or approved_d > 0:
            return alerts
        if now - self._last_strangle_alert < window_s:
            return alerts   # persists — re-alert once per window, not per tick

        gate_deltas = {k: v - base[3].get(k, 0) for k, v in fails.items()}
        top_gate, top_fails = max(gate_deltas.items(),
                                  key=lambda kv: kv[1], default=("?", 0))
        streak = {}
        try:
            streak = self.engine.risk.streak_state()
        except Exception:
            pass
        probe_line = ""
        if streak.get("latched"):
            p = streak.get("probe_in_seconds")
            probe_line = (
                f"- Loss streak: <code>{streak.get('consecutive_losses')}"
                f"/{streak.get('soft_limit')} soft</code>"
                + (f" — probe trade in <code>{p / 3600.0:.1f}h</code>\n"
                   if p is not None and p > 0 else
                   " — probe trade ALLOWED now\n" if p is not None else
                   " — probing disabled\n"))

        hours = CONFIG.risk.strangle_alert_hours
        self._last_strangle_alert = now
        alerts.append(Alert(
            alert_type="SIGNAL_STRANGLE",
            severity="WARNING",
            title="Signal flow strangled",
            body=(
                "⚠️ <b>SIGNAL FLOW STRANGLED</b>\n"
                "────────────────\n"
                f"<code>{evals_d}</code> ideas evaluated in the last "
                f"<code>{hours:.0f}h</code> — <b>zero approved</b>.\n\n"
                f"- Top rejecting gate: <code>{top_gate}</code> "
                f"(<code>{top_fails}</code> rejections)\n"
                + probe_line +
                "\nThe bot is scanning but cannot trade. If this is not "
                "intentional (breaker/streak protection doing its job), a "
                "gate may be latched or misconfigured.\n"
                "────────────────\n"
                "\U0001f449 /gates — per-gate pass/fail counters\n"
                "\U0001f449 /status — engine + breaker state\n"
                "\U0001f449 /whynot — why the last idea was rejected"
            ),
            dedup_key="signal_strangle",
        ))
        return alerts

    def _check_circuit_breaker(self) -> list[Alert]:
        """Alert on circuit breaker state changes."""
        alerts = []
        cb_active = self.engine.risk.circuit_breaker_active

        if cb_active and not self._last_cb_state:
            # Gather live context for the alert. Read the REAL trip cause and the
            # live accumulators — the old code read non-existent attrs
            # (risk.current_drawdown_pct / risk.daily_pnl) and the empty PAPER
            # portfolios, so a live trip always showed "Drawdown N/A, Daily P&L
            # N/A, Open Positions 0" even with real positions (operator report:
            # "message is not correct").
            cause = getattr(self.engine.risk, 'circuit_trip_cause', '') or 'unknown'
            _dl = getattr(self.engine.risk, 'last_known_daily_loss_pct', None)
            daily_pnl_str = f"-{_dl:.2f}% (of equity)" if _dl else "N/A"
            # Drawdown reason is shown via the cause line; the exact live % isn't
            # separately retained, so present it only when it IS the cause.
            drawdown_str = "see cause" if cause == "drawdown" else "N/A"
            # Live open-position count (operator account), not the paper books.
            positions_count = 0
            try:
                ex = getattr(self.engine, 'live_executor', None)
                if ex is not None:
                    positions_count = len(getattr(ex, 'open_positions', []) or [])
            except Exception:
                pass
            ts = datetime.now(UTC).strftime("%H:%M:%S UTC")

            alerts.append(Alert(
                alert_type="CIRCUIT_BREAKER",
                severity="CRITICAL",
                title="Circuit Breaker TRIPPED",
                body=(
                    "\U0001f6a8 <b>CIRCUIT BREAKER TRIPPED</b>\n"
                    "────────────────\n"
                    "The risk engine has <b>halted all new entries</b>.\n\n"
                    f"- Reason: <code>{cause}</code>\n"
                    f"- Drawdown: <code>{drawdown_str}</code>\n"
                    f"- Daily loss: <code>{daily_pnl_str}</code>\n"
                    f"- Open Positions: <code>{positions_count}</code>\n"
                    f"- Triggered At: <code>{ts}</code>\n\n"
                    "If the reason looks wrong (e.g. a stale drawdown after an "
                    "auth blip), <code>/resume</code> re-seeds the high-water "
                    "mark and clears it.\n\n"
                    "\U0001f6e1 Open positions are still monitored for SL/TP.\n"
                    "────────────────\n"
                    "\U0001f449 /status — review engine state\n"
                    "\U0001f449 /positions — inspect open trades\n"
                    "\U0001f449 /reset — clear after review"
                ),
                dedup_key="cb_tripped",
            ))
        elif not cb_active and self._last_cb_state:
            ts = datetime.now(UTC).strftime("%H:%M:%S UTC")
            alerts.append(Alert(
                alert_type="CIRCUIT_BREAKER",
                severity="INFO",
                title="Circuit Breaker Cleared",
                body=(
                    "\u2705 <b>CIRCUIT BREAKER CLEARED</b>\n"
                    "────────────────\n"
                    "Risk limits are back within tolerance.\n"
                    "Trading operations have <b>resumed</b>.\n\n"
                    f"- Cleared At: <code>{ts}</code>\n\n"
                    "\U0001f680 The engine will begin scanning on the next cycle.\n"
                    "────────────────\n"
                    "\U0001f449 /status — confirm engine state\n"
                    "\U0001f449 /health — check system vitals"
                ),
                dedup_key="cb_cleared",
            ))

        self._last_cb_state = cb_active
        return alerts

    def _check_drawdown_tiers(self) -> list[Alert]:
        """Early-warning alerts as drawdown approaches the circuit-breaker limit.

        Fires once at 50%, 75%, 85% of MAX_DRAWDOWN_PCT so the operator can act
        BEFORE the breaker halts trading. Re-arms only after drawdown recovers to
        a lower tier (tracked via _last_dd_tier), so it doesn't spam."""
        alerts: list[Alert] = []
        try:
            dd = getattr(self.engine.risk, "current_drawdown_pct", None)
            limit = float(getattr(CONFIG.risk, "max_drawdown_pct", 0) or 0)
            if dd is None or limit <= 0:
                return alerts
            frac = float(dd) / limit
            tier = 85 if frac >= 0.85 else (75 if frac >= 0.75 else (50 if frac >= 0.50 else 0))
            if tier > self._last_dd_tier and tier > 0:
                sev = "CRITICAL" if tier >= 85 else "WARNING"
                alerts.append(Alert(
                    alert_type="DRAWDOWN_TIER", severity=sev,
                    title=f"Drawdown {tier}% of limit",
                    body=(
                        f"⚠️ <b>DRAWDOWN AT {tier}% OF LIMIT</b>\n"
                        "────────────────\n"
                        f"- Current drawdown: <code>{float(dd):.2f}%</code>\n"
                        f"- Circuit-breaker limit: <code>{limit:.2f}%</code>\n\n"
                        "The risk engine halts all entries at 100% of the limit.\n"
                        "Consider reducing size or reviewing open risk now.\n"
                        "────────────────\n"
                        "\U0001f449 /status — review engine state\n"
                        "\U0001f449 /positions — inspect open trades"),
                    dedup_key=f"dd_tier_{tier}"))
            # Re-arm tiers once we drop below them (frac fell).
            self._last_dd_tier = tier
        except Exception as exc:
            system_log.debug("drawdown-tier check failed: %s", exc)
        return alerts

    def _check_tick_failures(self) -> list[Alert]:
        """Alert when the engine's main loop has failed repeatedly — positions
        may be silently unmonitored (SL/TP not firing)."""
        alerts: list[Alert] = []
        try:
            fails = int(getattr(self.engine, "_tick_consecutive_failures", 0) or 0)
            degraded = fails >= 3
            if degraded and not self._last_tick_degraded:
                alerts.append(Alert(
                    alert_type="TICK_FAILURE", severity="CRITICAL",
                    title="Engine loop degraded",
                    body=(
                        "\U0001f6a8 <b>ENGINE LOOP DEGRADED</b>\n"
                        "────────────────\n"
                        f"The main loop has failed <b>{fails}</b> times in a row.\n"
                        "Scanning and position monitoring may be impaired — "
                        "open positions could be <b>unmonitored</b>.\n"
                        "────────────────\n"
                        "\U0001f449 /status — check engine state\n"
                        "\U0001f449 /positions — verify SL/TP are in place"),
                    dedup_key="tick_degraded"))
            self._last_tick_degraded = degraded
        except Exception as exc:
            system_log.debug("tick-failure check failed: %s", exc)
        return alerts

    def _check_warning_rate_breaker(self) -> list[Alert]:
        """Alert when the infrastructure warning-rate breaker trips — signal
        generation is being suppressed by repeated errors (API/auth/WS)."""
        alerts: list[Alert] = []
        try:
            tripped = bool(getattr(self.engine.risk, "warning_rate_breaker_active", False))
            if tripped and not self._last_warn_rate:
                key = getattr(self.engine.risk, "_warning_rate_trip_key", "")
                alerts.append(Alert(
                    alert_type="WARNING_RATE", severity="WARNING",
                    title="Warning-rate breaker tripped",
                    body=(
                        "\U0001f7e0 <b>WARNING-RATE BREAKER TRIPPED</b>\n"
                        "────────────────\n"
                        "Repeated infrastructure warnings have <b>suppressed new "
                        "entries</b> (existing positions are still monitored).\n"
                        f"- Trigger: <code>{key or 'n/a'}</code>\n\n"
                        "Usually transient (exchange API / WS). It clears as the "
                        "error rate falls.\n"
                        "────────────────\n"
                        "\U0001f449 /status — review engine health"),
                    dedup_key="warn_rate_tripped"))
            self._last_warn_rate = tripped
        except Exception as exc:
            system_log.debug("warning-rate check failed: %s", exc)
        return alerts

    def _check_llm_degraded(self) -> list[Alert]:
        """Alert when the LLM brain has gone offline — every provider failed for
        N consecutive theses and the analyzer is running on the rule engine. This
        is the live "free-tier quota exhausted" signature that was previously
        silent: the bot keeps trading, but blind, on the rule engine only. Fires
        once when the streak crosses the threshold, and once more (INFO) when a
        live provider answers again. Rule-engine-by-design never trips it."""
        alerts: list[Alert] = []
        try:
            if not CONFIG.analyzer.llm_degraded_alert_enabled:
                return alerts
            analyzer = getattr(self.engine, "analyzer", None)
            if analyzer is None or not hasattr(analyzer, "llm_health"):
                return alerts
            health = analyzer.llm_health()
            streak = int(health.get("degraded_streak", 0) or 0)
            min_streak = int(getattr(
                CONFIG.analyzer, "llm_degraded_alert_min_streak", 3) or 3)
            degraded = streak >= min_streak
            if degraded and not self._last_llm_degraded:
                mins = float(health.get("degraded_seconds", 0.0) or 0.0) / 60.0
                alerts.append(Alert(
                    alert_type="LLM_DEGRADED", severity="CRITICAL",
                    title="LLM brain offline",
                    body=(
                        "\U0001f6a8 <b>LLM BRAIN OFFLINE — RUNNING ON RULES</b>\n"
                        "────────────────\n"
                        f"Every LLM provider has failed for <b>{streak}</b> "
                        "analyses in a row"
                        + (f" (~{mins:.0f} min)" if mins >= 1 else "") + ".\n"
                        "The bot is still scanning and trading, but on the "
                        "<b>rule engine only</b> — no AI thesis, weaker signals.\n\n"
                        + (("Last error: <code>"
                            + _html.escape(str(health.get("last_error", ""))[:160])
                            + "</code>\n")
                           if health.get("last_error") else
                           "Usual cause: free-tier API quota exhausted (429 / "
                           "RESOURCE_EXHAUSTED) across every provider.\n")
                        + "────────────────\n"
                        "\U0001f449 Add or rotate an LLM API key (paid tier "
                        "avoids the daily quota wall).\n"
                        "\U0001f449 /llmstatus — current provider + key"),
                    dedup_key="llm_degraded"))
            elif not degraded and self._last_llm_degraded:
                alerts.append(Alert(
                    alert_type="LLM_RESTORED", severity="INFO",
                    title="LLM brain restored",
                    body="✅ <b>LLM brain restored</b> — a provider answered "
                         "again. AI theses are back online.",
                    dedup_key="llm_restored"))
            self._last_llm_degraded = degraded
        except Exception as exc:
            system_log.debug("llm-degraded check failed: %s", exc)
        return alerts

    def _check_ws_health(self) -> list[Alert]:
        """Alert when the price WebSocket has been disconnected for a sustained
        window in live mode (SL/TP monitoring falls back to slower REST polling)."""
        alerts: list[Alert] = []
        try:
            if not CONFIG.is_live():
                return alerts
            ws = getattr(self.engine, "ws_feed", None)
            if ws is None:
                return alerts
            connected = bool(ws.is_connected())
            now = time.monotonic()
            if not connected:
                if self._ws_down_since == 0.0:
                    self._ws_down_since = now
                # Alert once it's been down for > 5 minutes.
                if (now - self._ws_down_since) > 300 and self._last_ws_ok:
                    self._last_ws_ok = False
                    alerts.append(Alert(
                        alert_type="WS_DOWN", severity="WARNING",
                        title="Price feed disconnected",
                        body=(
                            "\U0001f7e0 <b>PRICE WEBSOCKET DISCONNECTED</b>\n"
                            "────────────────\n"
                            "The real-time price feed has been down for "
                            "&gt;5 minutes. SL/TP monitoring is on slower REST "
                            "polling until it reconnects.\n"
                            "────────────────\n"
                            "\U0001f449 /health — check system vitals"),
                        dedup_key="ws_down"))
            else:
                if not self._last_ws_ok:
                    alerts.append(Alert(
                        alert_type="WS_UP", severity="INFO",
                        title="Price feed reconnected",
                        body="✅ <b>Price WebSocket reconnected</b> — "
                             "real-time monitoring restored.",
                        dedup_key="ws_up"))
                self._ws_down_since = 0.0
                self._last_ws_ok = True
        except Exception as exc:
            system_log.debug("ws-health check failed: %s", exc)
        return alerts

    def _check_stale_balance(self) -> list[Alert]:
        """Alert when the live balance cache is very stale — position sizing may
        be based on out-of-date equity."""
        alerts: list[Alert] = []
        try:
            if not CONFIG.is_live():
                return alerts
            ts = float(getattr(self.engine, "_live_balance_cache_ts", 0.0) or 0.0)
            if ts <= 0:
                return alerts
            age = time.monotonic() - ts
            if age > 300:        # > 5 minutes stale
                alerts.append(Alert(
                    alert_type="STALE_BALANCE", severity="WARNING",
                    title="Live balance stale",
                    body=(
                        "\U0001f7e0 <b>LIVE BALANCE CACHE STALE</b>\n"
                        "────────────────\n"
                        f"Exchange equity hasn't refreshed in <code>{age/60:.0f} min</code>. "
                        "Position sizing may use out-of-date equity.\n"
                        "────────────────\n"
                        "\U0001f449 /livebalance — force a refresh"),
                    dedup_key="stale_balance"))
        except Exception as exc:
            system_log.debug("stale-balance check failed: %s", exc)
        return alerts

    def _check_unprotected_positions(self) -> list[Alert]:
        """CRITICAL alert (live only) when an open position has NO exchange
        stop-loss after the grace window — i.e. SL placement / self-heal FAILED
        and the position is live with no venue-side protection. A naked leveraged
        perp is account-threatening and was otherwise only logged. Independent of
        the executor's check_positions message flow, so it can't be mislabeled or
        missed. Covers every executor (operator + per-user)."""
        alerts: list[Alert] = []
        try:
            if not CONFIG.is_live():
                return alerts
            grace = float(getattr(CONFIG.execution, "unprotected_alert_grace_seconds", 120.0))
            executors = []
            try:
                executors = list(self.engine._all_live_executors())
            except Exception:
                ex = getattr(self.engine, "live_executor", None)
                if ex is not None:
                    executors = [ex]
            now = datetime.now(UTC)
            for ex in executors:
                for pos in (getattr(ex, "open_positions", []) or []):
                    if getattr(pos, "status", "") != "open":
                        continue
                    opened_at = getattr(pos, "opened_at", None)
                    age = (now - opened_at).total_seconds() if opened_at else 1e9
                    has_sl = bool(getattr(pos, "sl_order_id", None))
                    marked = bool(getattr(pos, "unprotected", False))
                    # Unprotected = no exchange stop (or explicitly flagged) AND
                    # past the placement grace, so self-heal has had its chance.
                    if age < grace or (has_sl and not marked):
                        continue
                    sym = getattr(pos, "symbol", "?")
                    tid = getattr(pos, "trade_id", sym)
                    sl = getattr(pos, "stop_loss", 0.0) or 0.0
                    direction = getattr(pos, "direction", "")
                    # Surface the LAST venue rejection reason for this symbol so
                    # the operator can tell a transient retry apart from a hard
                    # rejection (min-size / wrong-symbol / bad tick) that needs a
                    # different manual fix — not just "it's naked". Best-effort.
                    reason = ""
                    try:
                        _r = ex._last_sltp_reason(sym)
                        if _r:
                            reason = (f"- Venue rejected the stop: "
                                      f"<code>{_html.escape(str(_r)[:160])}</code>\n")
                    except Exception:
                        reason = ""
                    alerts.append(Alert(
                        alert_type="POSITION_UNPROTECTED", severity="CRITICAL",
                        title=f"Unprotected: {sym}",
                        body=(
                            "\U0001f6a8 <b>POSITION UNPROTECTED — NO EXCHANGE STOP</b>\n"
                            "────────────────\n"
                            f"- {sym} <b>{direction}</b> "
                            f"open <code>{age/60:.0f} min</code> with NO venue stop-loss.\n"
                            f"- Intended stop: <code>${sl:,.4f}</code>\n"
                            + reason +
                            "- Self-heal keeps retrying and the local price check is the "
                            "only backstop — a gap/outage could run it unbounded.\n"
                            "────────────────\n"
                            "\U0001f449 Place a stop on Bitget manually now.\n"
                            "\U0001f449 /livepositions — review · /health — vitals"),
                        dedup_key=f"unprotected_{tid}"))
        except Exception as exc:
            system_log.debug("unprotected-position check failed: %s", exc)
        return alerts

    def _check_slippage(self) -> list[Alert]:
        """Alert (live only) when a symbol's mean absolute slippage drifts above
        the configured threshold, once it has enough recorded fills. Execution
        quality silently drains equity over many trades — surfacing it lets the
        operator switch to limit orders, trim size, or drop the symbol."""
        alerts: list[Alert] = []
        try:
            if not CONFIG.is_live():
                return alerts
            tracker = getattr(self.engine, "slippage", None)
            if tracker is None:
                return alerts
            _exec = getattr(CONFIG, "execution", None)
            if _exec is None:
                return alerts
            thresh = float(getattr(_exec, "slippage_alert_mean_pct", 0.20))
            min_trades = int(getattr(_exec, "slippage_alert_min_trades", 10))
            for symbol, stats in (tracker.get_all_stats() or {}).items():
                if stats.total_trades < min_trades:
                    continue
                if stats.mean_slippage_pct <= thresh:
                    continue
                alerts.append(Alert(
                    alert_type="SLIPPAGE_HIGH", severity="WARNING",
                    title=f"High slippage: {symbol}",
                    body=(
                        "\U0001f7e0 <b>EXECUTION SLIPPAGE ELEVATED</b>\n"
                        "────────────────\n"
                        f"- Symbol: <code>{symbol}</code>\n"
                        f"- Mean slippage: <code>{stats.mean_slippage_pct:.3f}%</code> "
                        f"(&gt; {thresh:.3f}% limit)\n"
                        f"- p95: <code>{stats.p95_slippage_pct:.3f}%</code>\n"
                        f"- Fills: <code>{stats.total_trades}</code>, "
                        f"adverse <code>{stats.adverse_count}</code>\n"
                        f"- Est. lost: <code>${stats.total_slippage_usd:,.2f}</code>\n"
                        "────────────────\n"
                        "\U0001f449 Consider limit orders, smaller size, or dropping "
                        "this symbol.\n"
                        "\U0001f449 /slippage — full execution-quality report"),
                    # Dedup per symbol; the 5-min cooldown prevents repeat spam.
                    dedup_key=f"slippage_high_{symbol}"))
        except Exception as exc:
            system_log.debug("slippage check failed: %s", exc)
        return alerts

    def _check_macro_calendar_stale(self) -> list[Alert]:
        """Alert when the macro calendar is EXHAUSTED — the hardcoded schedule has
        aged out, so all macro event protection (FOMC/CPI lockdowns) has silently
        disappeared. With the fail-safe ON the risk engine is now blocking new
        entries (BLACKOUT); either way the operator must refresh the schedule."""
        alerts: list[Alert] = []
        try:
            cal = getattr(self.engine, "macro_calendar", None)
            if cal is None or not hasattr(cal, "is_exhausted"):
                return alerts
            if not cal.is_exhausted():
                return alerts
            fail_closed = bool(getattr(
                CONFIG.risk, "macro_calendar_fail_closed_when_stale", True))
            posture = (
                "New entries are <b>blocked</b> (BLACKOUT) until refreshed."
                if fail_closed else
                "Event protection is <b>OFF</b> (fail-closed disabled) — trades "
                "are running with no macro lockdown."
            )
            alerts.append(Alert(
                alert_type="MACRO_CALENDAR_STALE", severity="CRITICAL",
                title="Macro calendar exhausted",
                body=(
                    "\U0001f7e0 <b>MACRO CALENDAR EXHAUSTED</b>\n"
                    "────────────────\n"
                    "Every scheduled macro event is now in the past — there are "
                    "no future FOMC/CPI/PCE/NFP events to gate against. The "
                    "hardcoded schedule needs regenerating.\n"
                    f"{posture}\n"
                    "────────────────\n"
                    "\U0001f449 Refresh the macro schedule (extend the calendar "
                    "or wire a live feed)."),
                dedup_key="macro_calendar_stale"))
        except Exception as exc:
            system_log.debug("macro-calendar-stale check failed: %s", exc)
        return alerts

    def _check_volume_spikes(self) -> list[Alert]:
        """Alert when the scanner detects volume spikes."""
        alerts = []
        try:
            # Check last scan results from the scanner cache
            if hasattr(self.engine, '_last_scan_signals'):
                for sig in self.engine._last_scan_signals:
                    if sig.volume_spike:
                        key = f"vol_spike_{sig.symbol}"
                        if key not in self._alerted_signals:
                            chg = f"{sig.change_pct_24h:+.1f}%" if sig.change_pct_24h else "N/A"
                            vol_m = sig.volume_usd_24h / 1_000_000 if sig.volume_usd_24h else 0
                            base = sig.symbol.split('/')[0] if '/' in sig.symbol else sig.symbol

                            # Direction hint from 24h change
                            if sig.change_pct_24h and sig.change_pct_24h > 0:
                                direction = "\U0001f7e2 Bullish momentum"
                            elif sig.change_pct_24h and sig.change_pct_24h < 0:
                                direction = "\U0001f534 Bearish pressure"
                            else:
                                direction = "\u26aa Neutral"

                            # Optional RSI
                            rsi = getattr(sig, 'rsi', None)
                            rsi_str = f"<code>{rsi:.1f}</code>" if rsi is not None else "—"

                            # Optional VWAP distance
                            vwap = getattr(sig, 'vwap', None)
                            if vwap and sig.price:
                                vwap_dist = ((sig.price - vwap) / vwap) * 100
                                vwap_str = f"<code>{vwap_dist:+.2f}%</code>"
                            else:
                                vwap_str = "—"

                            alerts.append(Alert(
                                alert_type="VOLUME_SPIKE",
                                severity="WARNING",
                                title=f"Volume Spike: {sig.symbol}",
                                body=(
                                    f"\U0001f4a5 <b>VOLUME SPIKE — {sig.symbol}</b>\n"
                                    "────────────────\n"
                                    f"- Price: <code>${sig.price:,.2f}</code> ({chg})\n"
                                    f"- 24h Volume: <code>${vol_m:,.1f}M</code>\n"
                                    f"- RSI: {rsi_str}\n"
                                    f"- vs VWAP: {vwap_str}\n"
                                    f"- Bias: {direction}\n"
                                    "────────────────\n"
                                    f"\U0001f449 Say \"analyze {base}\" for full technical breakdown\n"
                                    f"\U0001f449 Say \"chart {base}\" to view price chart"
                                ),
                                dedup_key=key,
                            ))
                            self._alerted_signals.add(key)
        except Exception as exc:
            logger.debug("_check_volume_spikes error: %s", exc)
        return alerts

    def _check_black_swan(self) -> list[Alert]:
        """Alert on black-swan detector triggers."""
        alerts = []
        try:
            if hasattr(self.engine, 'black_swan'):
                for alert_obj in self.engine.black_swan.active_alerts:
                    key = f"bs_{alert_obj.anomaly_type}_{alert_obj.symbol}"
                    sev = "CRITICAL" if alert_obj.severity == "SEVERE" else "WARNING"
                    sev_icon = "\U0001f534" if alert_obj.severity == "SEVERE" else "\U0001f7e0"
                    ts = datetime.now(UTC).strftime("%H:%M:%S UTC")
                    alerts.append(Alert(
                        alert_type="BLACK_SWAN",
                        severity=sev,
                        title=f"Anomaly: {alert_obj.anomaly_type}",
                        body=(
                            f"\U0001f6a8 <b>ANOMALY DETECTED</b>\n"
                            "────────────────\n"
                            f"- Type: <code>{alert_obj.anomaly_type}</code>\n"
                            f"- Symbol: <code>{alert_obj.symbol}</code>\n"
                            f"- Severity: {sev_icon} <code>{alert_obj.severity}</code>\n"
                            f"- Detected At: <code>{ts}</code>\n\n"
                            f"<i>{alert_obj.description}</i>\n"
                            "────────────────\n"
                            "\u26a0\ufe0f Engine may auto-halt if severity is SEVERE.\n"
                            f"\U0001f449 /status — check engine state\n"
                            f"\U0001f449 Say \"positions\" to review exposure"
                        ),
                        dedup_key=key,
                    ))
        except Exception as exc:
            logger.debug("_check_black_swan error: %s", exc)
        return alerts

    def _check_state_changes(self) -> list[Alert]:
        """Alert on significant FSM state changes."""
        alerts = []
        current_state = self.engine.state.value if hasattr(self.engine.state, 'value') else str(self.engine.state)

        if current_state != self._last_state:
            # Only alert on interesting transitions
            if current_state == "HALTED" and self._last_state != "HALTED":
                ts = datetime.now(UTC).strftime("%H:%M:%S UTC")
                alerts.append(Alert(
                    alert_type="STATE_CHANGE",
                    severity="CRITICAL",
                    title="Engine HALTED",
                    body=(
                        "\u26d4 <b>ENGINE HALTED</b>\n"
                        "────────────────\n"
                        f"- Previous State: <code>{self._last_state or 'UNKNOWN'}</code>\n"
                        f"- Halted At: <code>{ts}</code>\n\n"
                        "No new scans or analyses will run.\n"
                        "All automated trading is paused.\n"
                        "────────────────\n"
                        "\U0001f449 /status — review engine details\n"
                        "\U0001f449 /health — check system vitals\n"
                        "\U0001f449 /reset — resume after review"
                    ),
                    dedup_key="state_halted",
                ))
            elif current_state == "COOLING_DOWN" and self._last_state != "COOLING_DOWN":
                cooldown_sec = CONFIG.risk.cooldown_after_loss_seconds
                cooldown_min = cooldown_sec / 60
                alerts.append(Alert(
                    alert_type="STATE_CHANGE",
                    severity="WARNING",
                    title="Cooling Down",
                    body=(
                        f"\u23f8 <b>COOLDOWN ACTIVE</b>\n"
                        "────────────────\n"
                        f"- Duration: <code>{cooldown_min:.0f} min</code> ({cooldown_sec}s)\n"
                        f"- Previous State: <code>{self._last_state or 'UNKNOWN'}</code>\n\n"
                        "Post-loss cooldown period activated.\n"
                        "The engine will resume scanning automatically.\n"
                        "────────────────\n"
                        "\U0001f449 /status — check countdown\n"
                        "\U0001f449 /positions — review open trades"
                    ),
                    dedup_key="state_cooldown",
                ))

            self._last_state = current_state
        return alerts

    def _check_trade_signals(self) -> list[Alert]:
        """Alert when a new trade idea is generated and pending confirmation.

        Only higher-conviction ideas ping Telegram: confidence must clear
        ``risk.signal_display_min_confidence`` (default 0.70). Lower-conviction
        ideas (0.60-0.70) still queue and trade normally — they just don't
        message the operator, cutting notification noise. (Auto-execution is a
        separate, stricter gate: ``auto_confirm_threshold``, default 0.85.)
        """
        alerts = []
        try:
            min_alert_conf = CONFIG.risk.signal_display_min_confidence
            for idea_id, idea in list(self.engine._pending_ideas.items()):
                key = f"signal_{idea_id}"
                if key in self._alerted_signals:
                    continue
                # Mark seen once so a sub-threshold idea isn't re-evaluated each tick.
                self._alerted_signals.add(key)
                # Only higher-conviction ideas message the operator; lower ones
                # (0.60-0.70) still queue and trade, they just don't ping Telegram.
                if float(getattr(idea, "confidence", 0.0) or 0.0) >= min_alert_conf:
                    d = "\U0001f7e2 LONG" if idea.direction.value == "LONG" else "\U0001f534 SHORT"
                    risk_amt = abs(idea.entry_price - idea.stop_loss)
                    reward_amt = abs(idea.take_profit - idea.entry_price)
                    rr_ratio = reward_amt / risk_amt if risk_amt > 0 else 0
                    base = idea.asset.split('/')[0] if '/' in idea.asset else idea.asset
                    alerts.append(Alert(
                        alert_type="TRADE_SIGNAL",
                        severity="INFO",
                        title=f"Signal: {idea.asset}",
                        body=(
                            f"\U0001f514 <b>NEW SIGNAL — {idea.asset}</b>\n"
                            "────────────────\n"
                            f"- Direction: {d}\n"
                            f"- Confidence: <code>{idea.confidence:.0%}</code>\n"
                            f"- Entry: <code>${idea.entry_price:,.2f}</code>\n"
                            f"- Stop Loss: <code>${idea.stop_loss:,.2f}</code>\n"
                            f"- Take Profit: <code>${idea.take_profit:,.2f}</code>\n"
                            f"- R:R Ratio: <code>{rr_ratio:.1f}</code>\n"
                            "────────────────\n"
                            "\u23f3 Awaiting operator confirmation.\n"
                            f"\U0001f449 Say \"analyze {base}\" to review analysis\n"
                            f"\U0001f449 Say \"confirm\" to approve this trade"
                        ),
                        dedup_key=key,
                        idea=idea,
                    ))
        except Exception as exc:
            logger.debug("_check_trade_signals error: %s", exc)
        return alerts

    def _check_sl_tp_proximity(self) -> list[Alert]:
        """Alert when open positions approach their SL or TP levels."""
        alerts = []
        proximity_threshold = 0.015  # 1.5%
        try:
            # Collect positions from all user portfolios and the shared portfolio
            all_positions = []
            if self.engine.user_portfolios.all_portfolios():
                for uid in self.engine.user_portfolios.all_portfolios():
                    portfolio = self.engine.user_portfolios.get(uid)
                    all_positions.extend(portfolio.open_positions)
            else:
                all_positions.extend(self.engine.portfolio.open_positions)

            if not all_positions:
                return alerts

            # Get current prices from WS feed. Apply the same staleness bound the
            # SL/TP monitor uses so a silently-stalled feed can't drive a
            # proactive alert off a frozen price (0 = no filter).
            ws_prices = {}
            if self.engine.ws_feed.is_connected():
                ws_prices = self.engine.ws_feed.get_prices(
                    max_age_sec=getattr(CONFIG.execution, "ws_max_tick_age_sec", 0)) or {}

            for pos in all_positions:
                current_price = ws_prices.get(pos.asset)
                if not current_price or current_price <= 0:
                    continue
                if not pos.stop_loss or not pos.take_profit or pos.entry_price <= 0:
                    continue

                # Check SL proximity
                sl_distance_pct = abs(current_price - pos.stop_loss) / current_price
                if sl_distance_pct <= proximity_threshold:
                    key = f"sl_prox_{pos.asset}_{pos.trade_id}"
                    base = pos.asset.split('/')[0] if '/' in pos.asset else pos.asset
                    alerts.append(Alert(
                        alert_type="SL_PROXIMITY",
                        severity="WARNING",
                        title=f"SL Proximity: {pos.asset}",
                        body=(
                            f"\u26a0\ufe0f <b>STOP LOSS APPROACHING — {pos.asset}</b>\n"
                            "────────────────\n"
                            f"- Current Price: <code>${current_price:,.4f}</code>\n"
                            f"- Stop Loss: <code>${pos.stop_loss:,.4f}</code>\n"
                            f"- Distance: <code>{sl_distance_pct:.2%}</code>\n"
                            f"- Entry: <code>${pos.entry_price:,.4f}</code>\n"
                            "────────────────\n"
                            f"\U0001f449 /positions — review open trades\n"
                            f"\U0001f449 Say \"analyze {base}\" for updated analysis"
                        ),
                        dedup_key=key,
                    ))

                # Check TP proximity
                tp_distance_pct = abs(current_price - pos.take_profit) / current_price
                if tp_distance_pct <= proximity_threshold:
                    key = f"tp_prox_{pos.asset}_{pos.trade_id}"
                    base = pos.asset.split('/')[0] if '/' in pos.asset else pos.asset
                    alerts.append(Alert(
                        alert_type="TP_PROXIMITY",
                        severity="INFO",
                        title=f"TP Proximity: {pos.asset}",
                        body=(
                            f"\U0001f3af <b>TAKE PROFIT APPROACHING — {pos.asset}</b>\n"
                            "────────────────\n"
                            f"- Current Price: <code>${current_price:,.4f}</code>\n"
                            f"- Take Profit: <code>${pos.take_profit:,.4f}</code>\n"
                            f"- Distance: <code>{tp_distance_pct:.2%}</code>\n"
                            f"- Entry: <code>${pos.entry_price:,.4f}</code>\n"
                            "────────────────\n"
                            f"\U0001f449 /positions — review open trades\n"
                            f"\U0001f449 Say \"analyze {base}\" for updated analysis"
                        ),
                        dedup_key=key,
                    ))
        except Exception as exc:
            logger.debug("_check_sl_tp_proximity error: %s", exc)
        return alerts

    # ── Deduplication ─────────────────────────────────────────────

    def _should_send(self, alert: Alert) -> bool:
        """Check if alert should be sent (dedup + has enabled chats)."""
        if not self._enabled_chats:
            return False
        if alert.dedup_key:
            # Roadmap P0-1: the sentinel must be None, not 0. time.monotonic()'s
            # epoch is arbitrary (process uptime, often < DEDUP_COOLDOWN seconds),
            # so a 0 sentinel made `monotonic() - 0 < COOLDOWN` true on a fresh
            # process — silently suppressing the FIRST alert for any key during
            # the first ~5 minutes of uptime, exactly when a freshly-deployed bot
            # is most fragile (circuit-breaker trips, SL proximity).
            last_sent = self._dedup_cache.get(alert.dedup_key)
            if last_sent is not None and time.monotonic() - last_sent < self.DEDUP_COOLDOWN:
                return False
        return True

    def _mark_sent(self, alert: Alert) -> None:
        """Record that alert was sent for dedup tracking."""
        if alert.dedup_key:
            self._dedup_cache[alert.dedup_key] = time.monotonic()

        # Prune old dedup entries (keep last 200)
        if len(self._dedup_cache) > 200:
            sorted_keys = sorted(self._dedup_cache, key=self._dedup_cache.get)
            for k in sorted_keys[:100]:
                del self._dedup_cache[k]

        # Prune alerted signals set
        if len(self._alerted_signals) > 500:
            # Evict oldest half instead of clearing all
            to_remove = list(self._alerted_signals)[:250]
            self._alerted_signals -= set(to_remove)

    # ── Dispatch ──────────────────────────────────────────────────

    async def _dispatch(self, alert: Alert, send_fn) -> None:
        """Send alert to all enabled chats."""
        icon = _SEVERITY_ICON.get(alert.severity, "\u2139\ufe0f")
        full_msg = f"{icon} {alert.body}"

        async def _send_to_chat(chat_id: str) -> None:
            try:
                if alert.buttons:
                    # 3-arg form only when needed, so existing 2-arg send_fns
                    # (tests, custom integrations) keep working untouched.
                    await send_fn(chat_id, full_msg, alert.buttons)
                else:
                    await send_fn(chat_id, full_msg)
                if alert.idea is not None and self._chart_fn is not None:
                    try:
                        await self._chart_fn(chat_id, alert.idea)
                    except Exception as cexc:  # noqa: BLE001 — charts are best-effort
                        logger.warning("proactive chart send failed: %s", cexc, exc_info=True)
                audit(system_log,
                      f"Proactive alert sent: {alert.alert_type}",
                      action="proactive_alert",
                      data={"type": alert.alert_type, "chat_id": chat_id,
                            "severity": alert.severity})
            except Exception as exc:
                logger.debug("Failed to send alert to %s: %s", chat_id, exc)

        await asyncio.gather(*[_send_to_chat(cid) for cid in list(self._enabled_chats)])

    # ── Time Stops (Rules 6/17) ──────────────────────────────────

    def _check_time_stops(self) -> list[Alert]:
        """Alert when positions exceed time limits without profit."""
        alerts = []
        if not CONFIG.time_stop.enabled:
            return alerts

        try:
            all_positions = []
            if self.engine.user_portfolios.all_portfolios():
                for uid in self.engine.user_portfolios.all_portfolios():
                    portfolio = self.engine.user_portfolios.get(uid)
                    all_positions.extend(portfolio.open_positions)
            else:
                all_positions.extend(self.engine.portfolio.open_positions)

            if not all_positions:
                return alerts

            now = datetime.now(UTC)
            cfg = CONFIG.time_stop

            # Get current prices (staleness-bounded, as in the SL/TP monitor) so
            # a frozen WS price can't trigger a time-stop/SL-proximity alert.
            ws_prices = {}
            if self.engine.ws_feed.is_connected():
                ws_prices = self.engine.ws_feed.get_prices(
                    max_age_sec=getattr(CONFIG.execution, "ws_max_tick_age_sec", 0)) or {}

            for pos in all_positions:
                opened_at = getattr(pos, 'opened_at', None)
                if not opened_at:
                    continue

                # Calculate age in hours
                age_hours = (now - opened_at).total_seconds() / 3600.0

                # Determine trade type from SL distance: tight SL = intraday, wide = swing
                # Heuristic: if SL distance < 2% = intraday, else swing
                sl_pct = abs(pos.entry_price - pos.stop_loss) / pos.entry_price if pos.entry_price > 0 and pos.stop_loss > 0 else 0
                is_intraday = sl_pct < 0.02
                warn_hours = cfg.intraday_warn_hours if is_intraday else cfg.swing_warn_hours
                close_hours = cfg.intraday_close_hours if is_intraday else cfg.swing_close_hours
                trade_type = "intraday" if is_intraday else "swing"

                # Check if position is in profit
                current_price = ws_prices.get(pos.asset) or 0
                if current_price <= 0:
                    continue
                if pos.direction.value == "LONG":
                    in_profit = current_price > pos.entry_price
                else:
                    in_profit = current_price < pos.entry_price

                if in_profit:
                    continue  # Time stops only apply to positions NOT in profit

                base = pos.asset.split('/')[0] if '/' in pos.asset else pos.asset

                # Force close check
                if age_hours >= close_hours:
                    key = f"time_close_{pos.trade_id}"
                    alerts.append(Alert(
                        alert_type="TIME_STOP_CLOSE",
                        severity="CRITICAL",
                        title=f"Time Stop: {pos.asset}",
                        body=(
                            f"\u23f0 <b>TIME STOP — {pos.asset}</b>\n"
                            "────────────────\n"
                            f"- Type: <code>{trade_type}</code>\n"
                            f"- Open: <code>{age_hours:.1f}h</code> (limit: {close_hours:.0f}h)\n"
                            f"- Entry: <code>${pos.entry_price:,.4f}</code>\n"
                            f"- Current: <code>${current_price:,.4f}</code>\n"
                            f"- Status: <b>NOT in profit — AUTO-CLOSE recommended</b>\n"
                            "────────────────\n"
                            f"\U0001f449 /close {base} — close position manually\n"
                            "\U0001f449 /positions — review all open trades"
                        ),
                        dedup_key=key,
                    ))
                # Warning check
                elif age_hours >= warn_hours:
                    key = f"time_warn_{pos.trade_id}"
                    remaining = close_hours - age_hours
                    alerts.append(Alert(
                        alert_type="TIME_STOP_WARN",
                        severity="WARNING",
                        title=f"Time Warning: {pos.asset}",
                        body=(
                            f"\u23f3 <b>TIME WARNING — {pos.asset}</b>\n"
                            "────────────────\n"
                            f"- Type: <code>{trade_type}</code>\n"
                            f"- Open: <code>{age_hours:.1f}h</code>\n"
                            f"- Auto-close in: <code>{remaining:.1f}h</code>\n"
                            f"- Entry: <code>${pos.entry_price:,.4f}</code>\n"
                            f"- Current: <code>${current_price:,.4f}</code>\n"
                            f"- Status: NOT in profit\n"
                            "────────────────\n"
                            f"Position will be flagged for close at {close_hours:.0f}h if not profitable."
                        ),
                        dedup_key=key,
                    ))
        except Exception as exc:
            logger.debug("_check_time_stops error: %s", exc)
        return alerts
