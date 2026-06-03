"""
System Health Monitor for RUNECLAW.

Tracks API latency, error rates, exchange connectivity, and uptime.
Provides a health snapshot for risk engine and Telegram status display.
"""

from __future__ import annotations

import time
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class HealthSnapshot:
    """Point-in-time system health report."""
    uptime_seconds: float = 0.0
    api_latency_ms: float = 0.0          # rolling average
    api_latency_p99_ms: float = 0.0      # 99th percentile
    error_rate_pct: float = 0.0          # errors / total calls last 5 min
    total_api_calls: int = 0
    total_errors: int = 0
    last_successful_scan: Optional[str] = None
    last_error: Optional[str] = None
    exchange_connected: bool = True
    ws_connected: bool = False
    status: str = "HEALTHY"              # HEALTHY | DEGRADED | CRITICAL
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class SystemHealthMonitor:
    """Tracks API performance and system health metrics.

    Thread-safe. Call record_api_call() after each exchange/LLM API call.
    Call snapshot() to get current health status.
    """

    def __init__(self, window_seconds: float = 300.0, max_samples: int = 500):
        self._start_time = time.monotonic()
        self._lock = threading.Lock()
        self._window = window_seconds

        # Rolling latency samples: (timestamp, latency_ms, success)
        self._samples: deque = deque(maxlen=max_samples)
        self._total_calls = 0
        self._total_errors = 0
        self._last_success_time: Optional[str] = None
        self._last_error_msg: Optional[str] = None
        self._exchange_ok = True
        self._ws_ok = False

    def record_api_call(
        self, latency_ms: float, success: bool = True, error_msg: str = ""
    ) -> None:
        """Record an API call result."""
        with self._lock:
            now = time.monotonic()
            self._samples.append((now, latency_ms, success))
            self._total_calls += 1
            if success:
                self._last_success_time = datetime.now(timezone.utc).isoformat()
            else:
                self._total_errors += 1
                self._last_error_msg = error_msg or "Unknown error"

    def record_scan(self) -> None:
        """Record a successful market scan completion."""
        with self._lock:
            self._last_success_time = datetime.now(timezone.utc).isoformat()

    def set_exchange_status(self, connected: bool) -> None:
        with self._lock:
            self._exchange_ok = connected

    def set_ws_status(self, connected: bool) -> None:
        with self._lock:
            self._ws_ok = connected

    def snapshot(self) -> HealthSnapshot:
        """Get current health status."""
        with self._lock:
            now = time.monotonic()
            uptime = now - self._start_time
            cutoff = now - self._window

            # Filter to recent samples
            recent = [(t, lat, ok) for t, lat, ok in self._samples if t > cutoff]

            if recent:
                latencies = [lat for _, lat, _ in recent]
                avg_lat = sum(latencies) / len(latencies)
                sorted_lat = sorted(latencies)
                p99_idx = min(int(len(sorted_lat) * 0.99), len(sorted_lat) - 1)
                p99_lat = sorted_lat[p99_idx]
                errors = sum(1 for _, _, ok in recent if not ok)
                err_rate = (errors / len(recent)) * 100
            else:
                avg_lat = 0.0
                p99_lat = 0.0
                err_rate = 0.0

            # Determine status
            if not self._exchange_ok or err_rate > 50:
                status = "CRITICAL"
            elif err_rate > 10 or avg_lat > 5000:
                status = "DEGRADED"
            else:
                status = "HEALTHY"

            return HealthSnapshot(
                uptime_seconds=round(uptime, 1),
                api_latency_ms=round(avg_lat, 1),
                api_latency_p99_ms=round(p99_lat, 1),
                error_rate_pct=round(err_rate, 2),
                total_api_calls=self._total_calls,
                total_errors=self._total_errors,
                last_successful_scan=self._last_success_time,
                last_error=self._last_error_msg,
                exchange_connected=self._exchange_ok,
                ws_connected=self._ws_ok,
                status=status,
            )

    def format_telegram(self) -> str:
        """Format health snapshot for Telegram display."""
        s = self.snapshot()
        uptime_h = s.uptime_seconds / 3600
        uptime_d = int(uptime_h // 24)
        uptime_rem_h = uptime_h % 24

        status_icon = {
            "HEALTHY": "\u2705",
            "DEGRADED": "\u26a0\ufe0f",
            "CRITICAL": "\U0001f6a8",
        }.get(s.status, "\u2753")

        exchange_icon = "\U0001f7e2" if s.exchange_connected else "\U0001f534"
        exchange_str = "Connected" if s.exchange_connected else "DISCONNECTED"
        ws_icon = "\U0001f7e2" if s.ws_connected else "\u26aa"
        ws_str = "Connected" if s.ws_connected else "Disconnected"

        if uptime_d > 0:
            uptime_str = f"{uptime_d}d {uptime_rem_h:.1f}h"
        else:
            uptime_str = f"{uptime_h:.1f}h"

        lines = [
            f"{status_icon} <b>SYSTEM HEALTH: {s.status}</b>",
            "────────────────",
            f"- Uptime: <code>{uptime_str}</code>",
            f"- API Latency: <code>{s.api_latency_ms:.0f}ms</code> (p99: <code>{s.api_latency_p99_ms:.0f}ms</code>)",
            f"- Error Rate: <code>{s.error_rate_pct:.1f}%</code> ({s.total_errors}/{s.total_api_calls})",
            f"- Exchange: {exchange_icon} {exchange_str}",
            f"- WebSocket: {ws_icon} {ws_str}",
        ]
        if s.last_successful_scan:
            lines.append(f"- Last Scan: <code>{s.last_successful_scan[:19]}</code>")
        if s.last_error:
            lines.append(f"- Last Error: <code>{s.last_error[:60]}</code>")
        lines.append("────────────────")
        lines.append("\U0001f449 /status — engine overview")
        lines.append("\U0001f449 /watch on — enable proactive alerts")
        return "\n".join(lines)
