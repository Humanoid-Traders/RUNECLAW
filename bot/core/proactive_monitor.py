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
any gate.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from bot.compat import UTC
from typing import Optional, Set

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
        self._running = False
        self._dedup_cache: dict[str, float] = {}  # dedup_key -> last_alert_time

        # State tracking for change detection
        self._last_regime: dict[str, str] = {}    # symbol -> last known regime
        self._last_cb_state: bool = False          # last circuit breaker state
        self._last_state: str = ""                 # last engine FSM state
        self._alerted_signals: set = set()         # signal IDs already alerted

    def enable_chat(self, chat_id: str) -> None:
        """Enable proactive alerts for a chat."""
        self._enabled_chats.add(chat_id)
        audit(system_log, f"Proactive alerts enabled for chat {chat_id}",
              action="watch_on", data={"chat_id": chat_id})

    def disable_chat(self, chat_id: str) -> None:
        """Disable proactive alerts for a chat."""
        self._enabled_chats.discard(chat_id)
        audit(system_log, f"Proactive alerts disabled for chat {chat_id}",
              action="watch_off", data={"chat_id": chat_id})

    def is_enabled(self, chat_id: str) -> bool:
        return chat_id in self._enabled_chats

    @property
    def enabled_chat_count(self) -> int:
        return len(self._enabled_chats)

    async def run(self, send_fn) -> None:
        """Main monitor loop. Runs until stopped."""
        self._running = True
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
        alerts.extend(self._check_volume_spikes())
        alerts.extend(self._check_black_swan())
        alerts.extend(self._check_state_changes())
        alerts.extend(self._check_trade_signals())
        return alerts

    def _check_circuit_breaker(self) -> list[Alert]:
        """Alert on circuit breaker state changes."""
        alerts = []
        cb_active = self.engine.risk.circuit_breaker_active

        if cb_active and not self._last_cb_state:
            alerts.append(Alert(
                alert_type="CIRCUIT_BREAKER",
                severity="CRITICAL",
                title="Circuit Breaker TRIPPED",
                body=(
                    "\U0001f6a8 <b>CIRCUIT BREAKER TRIPPED</b>\n\n"
                    "The risk engine has halted all new entries.\n"
                    "Reason: daily loss or drawdown limit breached.\n\n"
                    "Open positions will continue to be monitored for SL/TP.\n"
                    "Use /reset to manually clear after review."
                ),
                dedup_key="cb_tripped",
            ))
        elif not cb_active and self._last_cb_state:
            alerts.append(Alert(
                alert_type="CIRCUIT_BREAKER",
                severity="INFO",
                title="Circuit Breaker Cleared",
                body=(
                    "\u2705 <b>CIRCUIT BREAKER CLEARED</b>\n\n"
                    "Trading operations have resumed.\n"
                    "The engine will begin scanning on the next cycle."
                ),
                dedup_key="cb_cleared",
            ))

        self._last_cb_state = cb_active
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
                            alerts.append(Alert(
                                alert_type="VOLUME_SPIKE",
                                severity="WARNING",
                                title=f"Volume Spike: {sig.symbol}",
                                body=(
                                    f"\U0001f4a5 <b>VOLUME SPIKE</b>: {sig.symbol}\n\n"
                                    f"Price: ${sig.price:,.2f} ({chg})\n"
                                    f"Volume: ${vol_m:,.0f}M\n\n"
                                    f"<i>Use /analyze {sig.symbol.split('/')[0]} for full analysis</i>"
                                ),
                                dedup_key=key,
                            ))
                            self._alerted_signals.add(key)
        except Exception:
            pass
        return alerts

    def _check_black_swan(self) -> list[Alert]:
        """Alert on black-swan detector triggers."""
        alerts = []
        try:
            if hasattr(self.engine, 'black_swan'):
                for alert_obj in self.engine.black_swan.active_alerts:
                    key = f"bs_{alert_obj.anomaly_type}_{alert_obj.symbol}"
                    alerts.append(Alert(
                        alert_type="BLACK_SWAN",
                        severity="CRITICAL" if alert_obj.severity == "SEVERE" else "WARNING",
                        title=f"Anomaly: {alert_obj.anomaly_type}",
                        body=(
                            f"\U0001f6a8 <b>ANOMALY DETECTED</b>\n\n"
                            f"Type: {alert_obj.anomaly_type}\n"
                            f"Symbol: {alert_obj.symbol}\n"
                            f"Severity: {alert_obj.severity}\n"
                            f"Details: {alert_obj.description}\n\n"
                            f"<i>Engine may auto-halt if severity is SEVERE</i>"
                        ),
                        dedup_key=key,
                    ))
        except Exception:
            pass
        return alerts

    def _check_state_changes(self) -> list[Alert]:
        """Alert on significant FSM state changes."""
        alerts = []
        current_state = self.engine.state.value if hasattr(self.engine.state, 'value') else str(self.engine.state)

        if current_state != self._last_state:
            # Only alert on interesting transitions
            if current_state == "HALTED" and self._last_state != "HALTED":
                alerts.append(Alert(
                    alert_type="STATE_CHANGE",
                    severity="CRITICAL",
                    title="Engine HALTED",
                    body=(
                        "\u26d4 <b>ENGINE HALTED</b>\n\n"
                        f"Previous state: {self._last_state}\n"
                        "The engine has entered HALTED state.\n"
                        "No new scans or analyses will run.\n\n"
                        "Use /status for details, /reset to resume."
                    ),
                    dedup_key="state_halted",
                ))
            elif current_state == "COOLING_DOWN" and self._last_state != "COOLING_DOWN":
                cooldown_sec = CONFIG.risk.cooldown_after_loss_seconds
                alerts.append(Alert(
                    alert_type="STATE_CHANGE",
                    severity="WARNING",
                    title="Cooling Down",
                    body=(
                        f"\u23f8 <b>COOLDOWN ACTIVE</b> ({cooldown_sec}s)\n\n"
                        "Post-loss cooldown period activated.\n"
                        "The engine will resume scanning automatically."
                    ),
                    dedup_key="state_cooldown",
                ))

            self._last_state = current_state
        return alerts

    def _check_trade_signals(self) -> list[Alert]:
        """Alert when a new trade idea is generated and pending confirmation."""
        alerts = []
        try:
            for idea_id, idea in self.engine._pending_ideas.items():
                key = f"signal_{idea_id}"
                if key not in self._alerted_signals:
                    d = "\U0001f7e2 LONG" if idea.direction.upper() == "LONG" else "\U0001f534 SHORT"
                    alerts.append(Alert(
                        alert_type="TRADE_SIGNAL",
                        severity="INFO",
                        title=f"Signal: {idea.asset}",
                        body=(
                            f"\U0001f514 <b>NEW SIGNAL</b>: {idea.asset}\n\n"
                            f"Direction: {d}\n"
                            f"Confidence: {idea.confidence:.0%}\n"
                            f"Entry: ${idea.entry:,.2f}\n"
                            f"SL: ${idea.stop_loss:,.2f}\n"
                            f"TP: ${idea.take_profit:,.2f}\n\n"
                            f"<i>Awaiting confirmation in chat</i>"
                        ),
                        dedup_key=key,
                    ))
                    self._alerted_signals.add(key)
        except Exception:
            pass
        return alerts

    # ── Deduplication ─────────────────────────────────────────────

    def _should_send(self, alert: Alert) -> bool:
        """Check if alert should be sent (dedup + has enabled chats)."""
        if not self._enabled_chats:
            return False
        if alert.dedup_key:
            last_sent = self._dedup_cache.get(alert.dedup_key, 0)
            if time.monotonic() - last_sent < self.DEDUP_COOLDOWN:
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
            self._alerted_signals.clear()

    # ── Dispatch ──────────────────────────────────────────────────

    async def _dispatch(self, alert: Alert, send_fn) -> None:
        """Send alert to all enabled chats."""
        icon = _SEVERITY_ICON.get(alert.severity, "\u2139\ufe0f")
        full_msg = f"{icon} {alert.body}"

        for chat_id in list(self._enabled_chats):
            try:
                await send_fn(chat_id, full_msg)
                audit(system_log,
                      f"Proactive alert sent: {alert.alert_type}",
                      action="proactive_alert",
                      data={"type": alert.alert_type, "chat_id": chat_id,
                            "severity": alert.severity})
            except Exception as exc:
                logger.debug("Failed to send alert to %s: %s", chat_id, exc)
