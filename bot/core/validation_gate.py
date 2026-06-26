"""
RUNECLAW Backtest Validation Gate — strategy validation before live deployment.

NOTE: Stub implementation — not yet wired to backtest harness.
record_validation() must be called manually; no automated pipeline invokes it.
The engine (bot/core/engine.py) does not consult this gate before executing trades.

Thread-safe in-memory storage of validation results with badge formatting
for War Room integration.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from bot.compat import UTC
from typing import Optional


@dataclass
class ValidationResult:
    """Stored result of a strategy validation run."""
    strategy_name: str
    sharpe: float
    max_drawdown: float
    win_rate: float
    total_trades: int
    walk_forward_score: float
    validated_at: str
    passed: bool


class BacktestValidationGate:
    """Gate that blocks unvalidated strategies from live trading."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._validations: dict[str, ValidationResult] = {}

    # ── Recording ────────────────────────────────────────────

    def record_validation(
        self,
        strategy_name: str,
        sharpe: float,
        max_drawdown: float,
        win_rate: float,
        total_trades: int,
        walk_forward_score: float,
    ) -> None:
        """Store a validation result for a strategy."""
        with self._lock:
            passed = self._check_pass(sharpe, max_drawdown, win_rate, total_trades)
            self._validations[strategy_name] = ValidationResult(
                strategy_name=strategy_name,
                sharpe=sharpe,
                max_drawdown=max_drawdown,
                win_rate=win_rate,
                total_trades=total_trades,
                walk_forward_score=walk_forward_score,
                validated_at=datetime.now(UTC).isoformat(),
                passed=passed,
            )

    @staticmethod
    def _check_pass(
        sharpe: float,
        max_drawdown: float,
        win_rate: float,
        total_trades: int,
        min_sharpe: float = 0.6,
    ) -> bool:
        """Internal check: does the strategy meet minimum validation criteria?"""
        return sharpe >= min_sharpe and total_trades >= 10

    # ── Queries ──────────────────────────────────────────────

    def is_validated(self, strategy_name: str, min_sharpe: float = 0.6) -> bool:
        """Check if a strategy has passed validation."""
        with self._lock:
            v = self._validations.get(strategy_name)
            if v is None:
                return False
            return v.sharpe >= min_sharpe and v.passed

    def get_validation_status(self, strategy_name: str) -> dict:
        """Full validation status for a strategy."""
        with self._lock:
            v = self._validations.get(strategy_name)
            if v is None:
                return {
                    "validated": False,
                    "sharpe": 0.0,
                    "badge": "NEVER TESTED",
                    "last_validated": None,
                    "details": {},
                }
            badge = "VALIDATED \u2713" if v.passed else "UNVALIDATED \u2717"
            return {
                "validated": v.passed,
                "sharpe": v.sharpe,
                "badge": badge,
                "last_validated": v.validated_at,
                "details": {
                    "max_drawdown": v.max_drawdown,
                    "win_rate": v.win_rate,
                    "total_trades": v.total_trades,
                    "walk_forward_score": v.walk_forward_score,
                },
            }

    def get_all_validations(self) -> dict[str, dict]:
        """All strategies' validation status."""
        # Snapshot keys under the lock, then resolve each status WITHOUT holding
        # the lock.  get_validation_status() re-acquires self._lock, and
        # threading.Lock is non-reentrant, so calling it from inside the lock
        # here deadlocked the calling thread permanently.
        with self._lock:
            names = list(self._validations)
        return {name: self.get_validation_status(name) for name in names}

    # ── Formatting ───────────────────────────────────────────

    def format_for_telegram(self) -> str:
        """War Room styled validation status card."""
        with self._lock:
            validations = dict(self._validations)

        if not validations:
            return (
                "<b>🧪 VALIDATION GATE</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n\n"
                "<i>No strategies validated yet.</i>"
            )

        lines = [
            "<b>🧪 VALIDATION GATE</b>",
            "━━━━━━━━━━━━━━━━━━━━━",
            "",
            "<pre>",
            f" {'STRATEGY':<18}{'SHARPE':>7}{'WR':>6}{'TRADES':>7} STATUS",
            f" {'─'*18}{'─'*7}{'─'*6}{'─'*7}{'─'*12}",
        ]

        for name, v in sorted(validations.items()):
            badge = "✓" if v.passed else "✗"
            icon = "🟢" if v.passed else "🔴"
            lines.append(
                f" {icon} {name:<16}"
                f"{v.sharpe:>7.2f}"
                f"{v.win_rate*100:>5.0f}%"
                f"{v.total_trades:>7}"
                f"  {badge}"
            )

        lines.append("</pre>")

        passed = sum(1 for v in validations.values() if v.passed)
        total = len(validations)
        lines.append(
            f"\n<b>{passed}/{total}</b> strategies validated"
        )

        return "\n".join(lines)

    def format_badge(self) -> str:
        """Short inline badge for the War Room dashboard."""
        with self._lock:
            validations = dict(self._validations)

        if not validations:
            return "🧪 <code>NO VALIDATIONS</code>"

        passed = sum(1 for v in validations.values() if v.passed)
        total = len(validations)

        if passed == total and total > 0:
            return f"🧪 <code>ALL VALIDATED ({passed}/{total})</code>"
        if passed > 0:
            return f"🧪 <code>PARTIAL ({passed}/{total})</code>"
        return f"🧪 <code>NONE VALIDATED (0/{total})</code>"
