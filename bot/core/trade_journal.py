"""
RUNECLAW Trade Journal — structured trade documentation and AI review.

For each closed trade, generates a structured journal entry with:
  - Trade parameters (entry, exit, SL, TP, size, leverage)
  - What signals were used and their accuracy
  - Market conditions at entry (regime, session, volatility)
  - Outcome analysis (PnL, R-multiple, holding time)
  - Lessons learned (auto-generated from pattern matching)

Weekly AI-generated performance review summarizing patterns.
Persisted to disk for historical analysis.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class JournalEntry:
    """Structured trade journal entry."""
    trade_id: str
    symbol: str
    direction: str
    strategy_type: str

    # Prices
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float

    # Results
    pnl: float
    pnl_pct: float
    r_multiple: float  # actual PnL / initial risk
    holding_hours: float

    # Context at entry
    regime: str = ""
    session: str = ""
    volatility: str = ""
    confidence: float = 0.0
    signals_used: list = field(default_factory=list)

    # Analysis
    exit_reason: str = ""  # "sl_hit", "tp_hit", "trailing", "manual", "partial_tp"
    lessons: list = field(default_factory=list)
    tags: list = field(default_factory=list)  # "winner", "loser", "breakeven", "runner", etc.

    timestamp: float = 0.0


class TradeJournal:
    """Manages trade journal entries with persistence."""

    def __init__(self, journal_file: str = "data/trade_journal.json") -> None:
        self._entries: list[JournalEntry] = []
        self._journal_file = journal_file
        self._max_entries = 1000
        self._load()

    def record_trade(
        self,
        trade_id: str,
        symbol: str,
        direction: str,
        strategy_type: str,
        entry_price: float,
        exit_price: float,
        stop_loss: float,
        take_profit: float,
        pnl: float,
        confidence: float = 0.0,
        signals_used: Optional[list] = None,
        regime: str = "",
        session: str = "",
        volatility: str = "",
        holding_hours: float = 0.0,
        exit_reason: str = "",
    ) -> JournalEntry:
        """Record a completed trade in the journal."""
        # Calculate R-multiple
        initial_risk = abs(entry_price - stop_loss)
        r_multiple = pnl / (initial_risk * (1 if direction == "LONG" else -1)) if initial_risk > 0 else 0

        # Calculate PnL %
        pnl_pct = (pnl / (entry_price * 1)) * 100 if entry_price > 0 else 0  # simplified

        # Auto-generate lessons
        lessons = self._generate_lessons(
            pnl=pnl, r_multiple=r_multiple, exit_reason=exit_reason,
            confidence=confidence, holding_hours=holding_hours,
            direction=direction, regime=regime,
        )

        # Auto-tag
        tags = self._generate_tags(pnl, r_multiple, exit_reason, holding_hours)

        entry = JournalEntry(
            trade_id=trade_id,
            symbol=symbol,
            direction=direction,
            strategy_type=strategy_type,
            entry_price=entry_price,
            exit_price=exit_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            pnl=round(pnl, 2),
            pnl_pct=round(pnl_pct, 4),
            r_multiple=round(r_multiple, 2),
            holding_hours=round(holding_hours, 2),
            regime=regime,
            session=session,
            volatility=volatility,
            confidence=confidence,
            signals_used=signals_used or [],
            exit_reason=exit_reason,
            lessons=lessons,
            tags=tags,
            timestamp=time.time(),
        )

        self._entries.append(entry)

        # Cap entries
        if len(self._entries) > self._max_entries:
            self._entries = self._entries[-self._max_entries:]

        self._save()
        return entry
    def get_weekly_review(self, lookback_days: int = 7) -> dict:
        """Generate a weekly performance review summary."""
        cutoff = time.time() - (lookback_days * 86400)
        recent = [e for e in self._entries if e.timestamp >= cutoff]

        if not recent:
            return {"period": f"Last {lookback_days} days", "trades": 0, "summary": "No trades in period"}

        wins = [e for e in recent if e.pnl > 0]
        losses = [e for e in recent if e.pnl < 0]
        total_pnl = sum(e.pnl for e in recent)

        # Best and worst trades
        best = max(recent, key=lambda e: e.pnl)
        worst = min(recent, key=lambda e: e.pnl)

        # By regime
        regime_stats = defaultdict(lambda: {"trades": 0, "pnl": 0, "wins": 0})
        for e in recent:
            r = e.regime or "unknown"
            regime_stats[r]["trades"] += 1
            regime_stats[r]["pnl"] += e.pnl
            if e.pnl > 0:
                regime_stats[r]["wins"] += 1

        # By strategy
        strat_stats = defaultdict(lambda: {"trades": 0, "pnl": 0, "wins": 0})
        for e in recent:
            s = e.strategy_type or "unknown"
            strat_stats[s]["trades"] += 1
            strat_stats[s]["pnl"] += e.pnl
            if e.pnl > 0:
                strat_stats[s]["wins"] += 1

        # Common lessons
        all_lessons = []
        for e in recent:
            all_lessons.extend(e.lessons)
        lesson_counts = defaultdict(int)
        for lesson in all_lessons:
            lesson_counts[lesson] += 1
        top_lessons = sorted(lesson_counts.items(), key=lambda x: -x[1])[:5]

        # Average holding time
        avg_hold = sum(e.holding_hours for e in recent) / len(recent)

        # Average R-multiple
        avg_r = sum(e.r_multiple for e in recent) / len(recent)

        return {
            "period": f"Last {lookback_days} days",
            "trades": len(recent),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(recent) * 100, 1),
            "total_pnl": round(total_pnl, 2),
            "avg_r_multiple": round(avg_r, 2),
            "avg_holding_hours": round(avg_hold, 1),
            "best_trade": {"symbol": best.symbol, "pnl": best.pnl, "r": best.r_multiple},
            "worst_trade": {"symbol": worst.symbol, "pnl": worst.pnl, "r": worst.r_multiple},
            "by_regime": dict(regime_stats),
            "by_strategy": dict(strat_stats),
            "top_lessons": top_lessons,
        }
    def _generate_lessons(self, **kwargs) -> list[str]:
        """Auto-generate lessons from trade outcome patterns."""
        lessons = []
        pnl = kwargs.get("pnl", 0)
        r_mult = kwargs.get("r_multiple", 0)
        exit_reason = kwargs.get("exit_reason", "")
        confidence = kwargs.get("confidence", 0)
        holding = kwargs.get("holding_hours", 0)
        direction = kwargs.get("direction", "")
        regime = kwargs.get("regime", "")

        # Exit analysis
        if exit_reason == "sl_hit" and r_mult < -0.8:
            lessons.append("Full stop hit — consider if SL was too tight")
        if exit_reason == "tp_hit" and r_mult > 2.5:
            lessons.append("TP hit at good R — setup quality was high")
        if exit_reason == "trailing" and r_mult > 1.0:
            lessons.append("Trailing stop locked profit — good trade management")

        # Confidence analysis
        if pnl < 0 and confidence < 0.60:
            lessons.append("Low confidence trade lost — stick to high-conf setups")
        if pnl > 0 and confidence >= 0.80:
            lessons.append("High confidence = high win rate confirmed")

        # Holding time
        if holding < 0.5 and abs(pnl) > 0:
            lessons.append("Very short hold — possible overreaction or noise stop")
        if holding > 48 and pnl < 0:
            lessons.append("Long holding loser — consider time-based exits")

        # Regime alignment
        if regime in ("RANGE", "CHOP") and direction == "LONG" and pnl < 0:
            lessons.append("Long in choppy market lost — reduce directional bias in ranges")
        if "TREND" in regime and pnl > 0:
            lessons.append("Profitable trend trade — regime alignment works")

        return lessons
    def _generate_tags(self, pnl: float, r_mult: float, exit_reason: str, holding: float) -> list[str]:
        """Auto-tag the trade for filtering."""
        tags = []
        if pnl > 0:
            tags.append("winner")
            if r_mult >= 3.0:
                tags.append("runner")
            elif r_mult >= 2.0:
                tags.append("solid_win")
        elif pnl < 0:
            tags.append("loser")
            if r_mult <= -1.0:
                tags.append("full_stop")
        else:
            tags.append("breakeven")

        if exit_reason == "trailing":
            tags.append("trailed")
        if exit_reason == "partial_tp":
            tags.append("partial")
        if holding < 1:
            tags.append("quick")
        if holding > 24:
            tags.append("swing")

        return tags
    def _save(self) -> None:
        """Persist journal to disk."""
        try:
            os.makedirs(os.path.dirname(self._journal_file) or ".", exist_ok=True)
            data = []
            for e in self._entries[-500:]:  # save last 500
                data.append({
                    "trade_id": e.trade_id, "symbol": e.symbol,
                    "direction": e.direction, "strategy_type": e.strategy_type,
                    "entry": e.entry_price, "exit": e.exit_price,
                    "sl": e.stop_loss, "tp": e.take_profit,
                    "pnl": e.pnl, "pnl_pct": e.pnl_pct,
                    "r_mult": e.r_multiple, "hold_hrs": e.holding_hours,
                    "regime": e.regime, "session": e.session,
                    "vol": e.volatility, "conf": e.confidence,
                    "signals": e.signals_used, "exit_reason": e.exit_reason,
                    "lessons": e.lessons, "tags": e.tags, "ts": e.timestamp,
                })
            with open(self._journal_file, "w") as f:
                json.dump(data, f)
        except Exception as exc:
            logger.debug("Journal save failed: %s", exc)
    def _load(self) -> None:
        """Load journal from disk."""
        try:
            if not os.path.exists(self._journal_file):
                return
            with open(self._journal_file) as f:
                data = json.load(f)
            for d in data:
                self._entries.append(JournalEntry(
                    trade_id=d["trade_id"], symbol=d["symbol"],
                    direction=d["direction"], strategy_type=d.get("strategy_type", "swing"),
                    entry_price=d["entry"], exit_price=d["exit"],
                    stop_loss=d["sl"], take_profit=d["tp"],
                    pnl=d["pnl"], pnl_pct=d.get("pnl_pct", 0),
                    r_multiple=d.get("r_mult", 0), holding_hours=d.get("hold_hrs", 0),
                    regime=d.get("regime", ""), session=d.get("session", ""),
                    volatility=d.get("vol", ""), confidence=d.get("conf", 0),
                    signals_used=d.get("signals", []), exit_reason=d.get("exit_reason", ""),
                    lessons=d.get("lessons", []), tags=d.get("tags", []),
                    timestamp=d.get("ts", 0),
                ))
            logger.info("Loaded %d journal entries", len(self._entries))
        except Exception as exc:
            logger.debug("Journal load failed: %s", exc)
