"""
RUNECLAW Alert Manager — escalation chain for signals and risk events.

Thread-safe alert classification, push logic, and repeating CRITICAL alerts.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass


# Alert levels in order of severity
CRITICAL = "CRITICAL"
HIGH = "HIGH"
MEDIUM = "MEDIUM"
LOW = "LOW"
INFO = "INFO"

_LEVEL_ORDER = {CRITICAL: 4, HIGH: 3, MEDIUM: 2, LOW: 1, INFO: 0}


@dataclass
class Alert:
    """A single alert in the pending queue."""
    alert_id: str
    level: str
    title: str
    body: str
    created_at: float
    acknowledged: bool = False
    last_pushed: float = 0.0
    push_count: int = 0


class AlertManager:
    """Classify signals and risk events, manage escalation and repeats."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending_alerts: list[Alert] = []
        self._acknowledged: set[str] = set()

    # ── Signal classification ────────────────────────────────

    @staticmethod
    def classify_signal(
        confidence: float, regime_aligned: bool, quant_score: float
    ) -> str:
        """Classify a trading signal into an alert level.

        Args:
            confidence: 0-100 scale (percentage).
            regime_aligned: whether signal aligns with current regime.
            quant_score: 0-1 scale quantitative score.
        """
        if confidence > 80 and regime_aligned and quant_score > 0.65:
            return HIGH
        if confidence > 70 and quant_score > 0.45:
            return MEDIUM
        if confidence > 60:
            return LOW
        return INFO

    # ── Risk event classification ────────────────────────────

    @staticmethod
    def classify_risk_event(drawdown_pct: float, daily_limit_pct: float) -> str:
        """Classify a risk event based on drawdown vs daily limit.

        Args:
            drawdown_pct: current drawdown percentage.
            daily_limit_pct: configured daily loss limit percentage.
        """
        if daily_limit_pct <= 0:
            return LOW
        ratio = drawdown_pct / daily_limit_pct
        if ratio > 0.80:
            return CRITICAL
        if ratio > 0.60:
            return HIGH
        if ratio > 0.40:
            return MEDIUM
        return LOW

    # ── Push / repeat logic ──────────────────────────────────

    @staticmethod
    def should_push(level: str) -> bool:
        """Whether this alert level should be pushed to the user."""
        return level in (CRITICAL, HIGH, MEDIUM)

    @staticmethod
    def should_repeat(level: str) -> bool:
        """Whether this alert level should repeat until acknowledged."""
        return level == CRITICAL

    @staticmethod
    def get_repeat_interval(level: str) -> int:
        """Repeat interval in seconds. Only CRITICAL repeats."""
        if level == CRITICAL:
            return 300
        return 0

    # ── Alert queue management ───────────────────────────────

    def create_alert(self, level: str, title: str, body: str) -> str:
        """Create and queue a new alert. Returns alert_id."""
        with self._lock:
            alert_id = str(uuid.uuid4())[:8]
            alert = Alert(
                alert_id=alert_id,
                level=level,
                title=title,
                body=body,
                created_at=time.time(),
            )
            self._pending_alerts.append(alert)
            return alert_id

    def acknowledge(self, alert_id: str) -> None:
        """Stop repeating a CRITICAL alert."""
        with self._lock:
            self._acknowledged.add(alert_id)
            for alert in self._pending_alerts:
                if alert.alert_id == alert_id:
                    alert.acknowledged = True

    def get_pending(self) -> list[Alert]:
        """Return alerts that need to be pushed."""
        with self._lock:
            result = []
            for alert in self._pending_alerts:
                if alert.acknowledged:
                    continue
                if not self.should_push(alert.level):
                    continue
                result.append(alert)
            return result

    # ── Formatting ───────────────────────────────────────────

    @staticmethod
    def format_alert(level: str, title: str, body: str) -> str:
        """Format an alert as HTML with level-appropriate styling."""
        if level == CRITICAL:
            return (
                f"🚨 <b style='color:red'>CRITICAL: {title}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>{body}</b>"
            )
        if level == HIGH:
            return (
                f"⚡ <b>HIGH: {title}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"{body}"
            )
        if level == MEDIUM:
            return (
                f"📊 <b>{title}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"{body}"
            )
        if level == LOW:
            return (
                f"📝 <i>{title}</i>\n"
                f"{body}"
            )
        # INFO
        return f"{title}\n{body}"
