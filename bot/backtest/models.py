"""
RUNECLAW Backtest Data Models -- Pydantic schemas for backtesting.
Extends the core models with backtest-specific structures.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional
from pydantic import BaseModel, Field


class BacktestConfig(BaseModel):
    """Configuration for a single backtest run."""
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    start_date: Optional[str] = None       # ISO format, e.g. "2025-01-01"
    end_date: Optional[str] = None
    initial_balance: float = 10_000.0
    commission_pct: float = 0.1            # 0.1% per trade (Bitget taker fee)
    slippage_pct: float = 0.05             # 0.05% simulated slippage
    max_position_pct: float = 2.0          # matches live risk config
    max_open_positions: int = 5
    confidence_threshold: float = 0.5
    use_llm: bool = False                  # default: rule-based for reproducibility
    lookback_size: int = 100               # bars needed for indicator calculation
    scan_interval: int = 4                 # check for signals every N bars


class BacktestBar(BaseModel):
    """A single OHLCV bar for replay."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class BacktestTrade(BaseModel):
    """Record of a single trade during a backtest."""
    trade_id: str
    symbol: str
    direction: str                         # "LONG" or "SHORT"
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    quantity: float
    size_usd: float
    pnl_usd: float
    pnl_pct: float
    commission_usd: float
    slippage_usd: float
    net_pnl_usd: float                     # pnl - commission - slippage
    exit_reason: str                        # "SL", "TP", "END_OF_DATA"
    confidence: float
    risk_verdict: str                       # "APPROVED" or "REJECTED"
    reasoning: str = ""
    signals_used: list[str] = Field(default_factory=list)


class EquityPoint(BaseModel):
    """Single point on the equity curve."""
    timestamp: datetime
    equity: float
    drawdown_pct: float
    open_positions: int


class BacktestResult(BaseModel):
    """Complete output of a backtest run."""
    # Config
    symbol: str
    timeframe: str
    start_date: str
    end_date: str
    initial_balance: float
    commission_pct: float
    slippage_pct: float

    # Performance
    final_equity: float
    total_return_pct: float
    total_pnl: float
    total_commission: float
    total_slippage: float
    net_pnl: float

    # Trade stats
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_win_usd: float
    avg_loss_usd: float
    largest_win_usd: float
    largest_loss_usd: float
    avg_trade_duration_hours: float

    # Risk stats
    max_drawdown_pct: float
    max_drawdown_usd: float
    max_consecutive_losses: int
    profit_factor: float                    # gross profit / gross loss
    sharpe_ratio: float                     # annualized, using hourly returns
    sortino_ratio: float                    # downside deviation only
    calmar_ratio: float                     # annual return / max drawdown
    risk_reward_avg: float

    # Rejection stats
    total_signals_generated: int
    total_ideas_generated: int
    total_ideas_rejected_risk: int
    total_ideas_rejected_confidence: int

    # Data
    trades: list[BacktestTrade] = Field(default_factory=list)
    equity_curve: list[EquityPoint] = Field(default_factory=list)

    # Metadata
    duration_seconds: float = 0.0
    bars_processed: int = 0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
