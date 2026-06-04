"""
Multi-user portfolio manager for RUNECLAW.

Wraps PortfolioTracker to provide per-user isolated paper wallets.
Each user gets their own balance, positions, trade history, and PNL.
The engine's shared portfolio remains as a fallback for system-level
operations (risk checks, stop monitoring).
"""

from __future__ import annotations

import glob
import logging
import os
import re
import threading
from typing import Optional, Callable

from bot.config import CONFIG
from bot.risk.portfolio import PortfolioTracker, TrailingStopConfig
from bot.utils.models import PortfolioState, TradeExecution, TradeIdea

log = logging.getLogger("runeclaw.multi_portfolio")

DEFAULT_PAPER_BALANCE = 10_000.0
DATA_DIR = "data"


class MultiUserPortfolio:
    """Manages per-user PortfolioTracker instances.

    Usage:
        multi = MultiUserPortfolio(default_balance=10_000.0)
        portfolio = multi.get("user_123")  # creates on first access
        portfolio.open_position(idea, size_usd)
    """

    def __init__(
        self,
        default_balance: float = DEFAULT_PAPER_BALANCE,
        on_trade_close: Optional[Callable[[float], None]] = None,
        trailing_config: Optional[TrailingStopConfig] = None,
    ) -> None:
        self._default_balance = default_balance
        self._on_trade_close = on_trade_close
        self._trailing_config = trailing_config
        self._portfolios: dict[str, PortfolioTracker] = {}
        self._lock = threading.Lock()
        # Auto-load existing portfolios from disk
        self._load_existing()

    def _load_existing(self) -> None:
        """Scan data/ for existing portfolio_*.json files and load them."""
        pattern = os.path.join(DATA_DIR, "portfolio_*.json")
        for path in glob.glob(pattern):
            filename = os.path.basename(path)
            # Extract user_id from "portfolio_{user_id}.json"
            if not filename.startswith("portfolio_") or not filename.endswith(".json"):
                continue
            user_id = filename[len("portfolio_"):-len(".json")]
            if not user_id:
                continue
            try:
                portfolio = PortfolioTracker(
                    initial_balance=None,  # None triggers _load_state_on_init()
                    on_trade_close=self._on_trade_close,
                    state_file=path,
                    trailing_config=self._trailing_config,
                )
                self._portfolios[user_id] = portfolio
                snap = portfolio.snapshot()
                log.info(
                    "Restored portfolio for user %s: balance=$%.2f, "
                    "%d open positions, %d trades",
                    user_id, snap.balance_usd,
                    snap.open_positions, snap.total_trades,
                )
            except Exception as e:
                log.error("Failed to load portfolio for user %s from %s: %s",
                          user_id, path, e)

    def get(self, user_id: str) -> PortfolioTracker:
        """Get or create a portfolio for the given user."""
        if user_id in self._portfolios:
            return self._portfolios[user_id]

        with self._lock:
            # Double-check after acquiring lock
            if user_id not in self._portfolios:
                user_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(user_id))
                if not user_id:
                    raise ValueError("Invalid user_id: empty after sanitization")
                state_file = f"data/portfolio_{user_id}.json"
                portfolio = PortfolioTracker(
                    initial_balance=self._default_balance,
                    on_trade_close=self._on_trade_close,
                    state_file=state_file,
                    trailing_config=self._trailing_config,
                )
                self._portfolios[user_id] = portfolio
                log.info("Created portfolio for user %s (balance=$%.2f)",
                         user_id, self._default_balance)
            return self._portfolios[user_id]

    def has_user(self, user_id: str) -> bool:
        """Check if a user has an active portfolio."""
        return user_id in self._portfolios

    def all_user_ids(self) -> list[str]:
        """Return all user IDs with active portfolios."""
        return list(self._portfolios.keys())

    def all_portfolios(self) -> dict[str, PortfolioTracker]:
        """Return all user portfolios."""
        return dict(self._portfolios)

    def mark_to_market_all(self, prices: dict[str, float]) -> None:
        """Update mark-to-market prices across ALL user portfolios."""
        for portfolio in self._portfolios.values():
            portfolio.mark_to_market(prices)

    def check_stops_all(self, prices: dict[str, float]) -> dict[str, list[TradeExecution]]:
        """Check stop-losses/take-profits for ALL users. Returns {user_id: [closed_trades]}."""
        results: dict[str, list[TradeExecution]] = {}
        for user_id, portfolio in self._portfolios.items():
            closed = portfolio.check_stops(prices)
            if closed:
                results[user_id] = closed
        return results

    def snapshot_all(self) -> dict[str, PortfolioState]:
        """Get portfolio snapshots for all users."""
        return {uid: p.snapshot() for uid, p in self._portfolios.items()}

    def total_open_positions(self) -> int:
        """Total open positions across all users."""
        return sum(len(p.open_positions) for p in self._portfolios.values())

    def combined_snapshot(self) -> PortfolioState:
        """Combined portfolio state for system-level risk checks."""
        total_balance = 0.0
        total_equity = 0.0
        total_trades = 0
        total_pnl = 0.0
        total_open = 0
        total_daily_pnl = 0.0
        max_dd = 0.0
        total_initial_balance = 0.0
        total_gross_pnl = 0.0
        total_commission = 0.0

        for p in self._portfolios.values():
            snap = p.snapshot()
            total_balance += snap.balance_usd
            total_equity += snap.equity_usd
            total_trades += snap.total_trades
            total_pnl += snap.total_pnl
            total_open += snap.open_positions
            total_daily_pnl += snap.daily_pnl
            total_gross_pnl += snap.total_gross_pnl
            total_commission += snap.total_commission
            # NOTE: max_dd here is the worst individual drawdown, not a true
            # combined drawdown.  We also compute an approximate combined
            # drawdown below from combined equity vs sum of initial balances.
            max_dd = max(max_dd, snap.max_drawdown_pct)
            total_initial_balance += getattr(p, '_initial_balance', snap.balance_usd)

        # Approximate combined drawdown from combined equity vs sum of initial balances.
        # This is more meaningful than the per-user max when portfolios diverge.
        if total_initial_balance > 0:
            combined_peak = max(total_initial_balance, total_equity)
            combined_dd = ((combined_peak - total_equity) / combined_peak * 100) if combined_peak > 0 else 0.0
            max_dd = max(max_dd, combined_dd)

        wins = 0
        total_count = 0
        for p in self._portfolios.values():
            for t in p.trade_history:
                total_count += 1
                if t.pnl > 0:
                    wins += 1

        return PortfolioState(
            balance_usd=round(total_balance, 2),
            equity_usd=round(total_equity, 2),
            open_positions=total_open,
            total_trades=total_count,
            win_rate=round(wins / total_count, 2) if total_count > 0 else 0.0,
            total_pnl=round(total_pnl, 2),
            total_gross_pnl=round(total_gross_pnl, 2),
            total_commission=round(total_commission, 2),
            daily_pnl=round(total_daily_pnl, 2),
            max_drawdown_pct=round(max_dd, 2),
        )
