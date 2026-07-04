"""
RUNECLAW Signal Tracker — per-pair signal history and win rate tracking.

Thread-safe in-memory storage of signal outcomes for War Room reporting.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from bot.compat import UTC
from typing import Optional


@dataclass
class SignalRecord:
    """A single signal with optional outcome."""
    signal_id: str
    symbol: str
    direction: str
    confidence: float
    entry_price: float
    timestamp: str
    # Outcome fields (filled after close)
    pnl: Optional[float] = None
    exit_price: Optional[float] = None
    closed: bool = False


class SignalTracker:
    """Track signals per pair and compute win rates for War Room reporting."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._signals: dict[str, list[SignalRecord]] = {}  # symbol -> records
        self._by_id: dict[str, SignalRecord] = {}  # signal_id -> record

    # ── Recording ────────────────────────────────────────────

    def record_signal(
        self,
        symbol: str,
        direction: str,
        confidence: float,
        entry_price: float,
        signal_id: str,
    ) -> None:
        """Log a new signal."""
        with self._lock:
            rec = SignalRecord(
                signal_id=signal_id,
                symbol=symbol,
                direction=direction,
                confidence=confidence,
                entry_price=entry_price,
                timestamp=datetime.now(UTC).isoformat(),
            )
            self._signals.setdefault(symbol, []).append(rec)
            self._by_id[signal_id] = rec

    def record_outcome(
        self, signal_id: str, pnl: float, exit_price: float
    ) -> None:
        """Record how a signal performed."""
        with self._lock:
            rec = self._by_id.get(signal_id)
            if rec is None:
                return
            rec.pnl = pnl
            rec.exit_price = exit_price
            rec.closed = True

    # ── Queries ──────────────────────────────────────────────

    def get_pair_stats(self, symbol: str) -> dict:
        """Per-pair statistics."""
        with self._lock:
            return self._pair_stats_locked(symbol)

    def _pair_stats_locked(self, symbol: str) -> dict:
        records = self._signals.get(symbol, [])
        if not records:
            return {
                "total_signals": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "avg_pnl": 0.0,
                "best_pnl": 0.0,
                "worst_pnl": 0.0,
                "last_signal_time": None,
            }
        closed = [r for r in records if r.closed]
        wins = [r for r in closed if r.pnl is not None and r.pnl > 0]
        losses = [r for r in closed if r.pnl is not None and r.pnl <= 0]
        pnls = [r.pnl for r in closed if r.pnl is not None]
        return {
            "total_signals": len(records),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(closed) if closed else 0.0,
            "avg_pnl": sum(pnls) / len(pnls) if pnls else 0.0,
            "best_pnl": max(pnls) if pnls else 0.0,
            "worst_pnl": min(pnls) if pnls else 0.0,
            "last_signal_time": records[-1].timestamp,
        }

    def get_all_pair_stats(self) -> dict[str, dict]:
        """All pairs."""
        with self._lock:
            return {
                sym: self._pair_stats_locked(sym)
                for sym in self._signals
            }

    # ── Formatting ───────────────────────────────────────────

    def format_for_telegram(self) -> str:
        """War Room styled HTML table of per-pair performance."""
        with self._lock:
            all_stats = {
                sym: self._pair_stats_locked(sym)
                for sym in self._signals
            }

        if not all_stats:
            return (
                "<b>📊 SIGNAL HISTORY</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n\n"
                "<i>No signals recorded yet.</i>"
            )

        # Group per-pair stats by asset category (Crypto, Metal, Stock, …), a
        # <pre> table per category. Within a category, keep the sort by signal
        # count. Shared grouping helper so /signals matches the scan commands.
        from bot.core.market_scanner import (
            group_by_category, category_icon, category_for_symbol,
        )
        entries = sorted(
            all_stats.items(), key=lambda x: x[1]["total_signals"], reverse=True
        )
        grouped = group_by_category(entries, lambda e: category_for_symbol(e[0]))

        lines = [
            "<b>📊 SIGNAL HISTORY</b>",
            "━━━━━━━━━━━━━━━━━━━━━",
            "",
        ]
        for cat, cat_entries in grouped.items():
            lines.append(f"{category_icon(cat)} <b>{cat}</b>")
            lines.append("<pre>")
            lines.append(
                f" {'PAIR':<12}{'SIG':>4}{'W':>4}{'L':>4}{'WR':>7}{'AVG':>9}"
            )
            lines.append(f" {'─'*12}{'─'*4}{'─'*4}{'─'*4}{'─'*7}{'─'*9}")
            for sym, stats in cat_entries:
                short = sym.replace("/USDT", "").replace("/", "")
                wr = stats["win_rate"]
                # Win rate bar: 5 chars
                filled = round(wr * 5)
                bar = "█" * filled + "░" * (5 - filled)
                lines.append(
                    f" {short:<12}{stats['total_signals']:>4}"
                    f"{stats['wins']:>4}{stats['losses']:>4}"
                    f"  {bar}"
                    f" ${stats['avg_pnl']:>+7.2f}"
                )
            lines.append("</pre>")

        return "\n".join(lines)
