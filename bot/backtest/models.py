"""
RUNECLAW Backtest Data Models -- Pydantic schemas for backtesting.
Extends the core models with backtest-specific structures.
"""

from __future__ import annotations

from datetime import datetime
from bot.compat import UTC
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
    # Extra minimum-confidence entry gate applied ON TOP of the analyzer's
    # per-strategy floors. 0.0 = no extra gate (the default — entries are governed
    # by the analyzer/risk thresholds, unchanged). The walk-forward optimizer
    # (--wf-optimize) sweeps this to find the entry cutoff that generalizes OOS,
    # so it must actually filter trades; the engine honors it in _evaluate_bar.
    confidence_threshold: float = 0.0
    # Entry fill convention (audit fix #15). "close" = fill at the same bar's
    # close that generated the signal (legacy; optimistic — assumes you can
    # transact at the closing print). "next_open" = queue the approved idea and
    # fill at the NEXT bar's open (conservative; closer to live execution).
    # Run both and compare: a large edge gap between them means the strategy's
    # backtested edge lives in the fill assumption, not the signal.
    fill_mode: str = "close"               # "close" | "next_open"
    # Auto-reset a tripped circuit breaker after this many bars (0 = never,
    # the default — breaker halts are preserved exactly like live). A
    # drawdown/streak trip normally requires MANUAL reset; in a months-long
    # unattended backtest one early losing streak otherwise halts trading for
    # the remainder of the run, so the result measures the halt rather than
    # the strategy. Set e.g. 24 (a day of 1h bars) to measure strategy edge
    # with an operator assumed to reset the breaker daily.
    breaker_reset_bars: int = 0
    use_llm: bool = False                  # default: rule-based for reproducibility
    # Deterministic backtest parity: replay recorded LLM theses
    # (data/learning/llm_calibration.jsonl) so the run exercises the live blended
    # path with no network. Takes precedence over use_llm. Default OFF preserves
    # the rule-based default.
    use_recorded_llm: bool = False
    recorded_llm_path: str = "data/learning/llm_calibration.jsonl"
    # Deep-audit #17: replay shadow-recorded order-flow snapshots so the backtest
    # exercises the SAME microstructure path live runs (smart-money voter,
    # order-flow confluence/veto, funding haircut) instead of order_flow=None.
    # Default OFF → analyzer runs without order flow, identical to today.
    use_recorded_order_flow: bool = False
    recorded_order_flow_path: str = "data/learning/order_flow_snapshots.jsonl"
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
    symbol: str = "BTC/USDT"


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
    net_pnl_usd: float                     # pnl - commission (slippage is baked into entry/exit prices)
    exit_reason: str                        # "SL", "TP", "END_OF_DATA"
    confidence: float
    risk_verdict: str                       # "APPROVED" or "REJECTED"
    reasoning: str = ""
    signals_used: list[str] = Field(default_factory=list)
    entry_regime: str = ""                   # market regime at entry (attribution)
    setup: str = ""                          # strategy_type: scalp/intraday/swing/position
    signal_type: str = ""                    # momentum_confluence / vwap_reversion / ...


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

    # Per-gate risk-rejection tally (gate name -> count) so an A/B diff can SEE
    # which gates diverged. `stateful_rejections` sums the path-dependent gates
    # (breaker / governor / loss-streak / cooldown / daily-loss / drawdown) whose
    # firing depends on prior-trade outcomes — divergence there means an A/B
    # metric change came from a different trade SET, not the parameter under test.
    rejections_by_gate: dict = Field(default_factory=dict)
    stateful_rejections: int = 0

    # Projected operating costs (when use_llm=False, estimated from signal count)
    projected_llm_cost_usd: float = 0.0
    est_cost_per_analysis: float = 0.0
    net_pnl_after_projected_cost: float = 0.0

    # Data
    trades: list[BacktestTrade] = Field(default_factory=list)
    equity_curve: list[EquityPoint] = Field(default_factory=list)

    # Metadata
    duration_seconds: float = 0.0
    bars_processed: int = 0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Effective config snapshot for reproducibility (fix L)
    effective_config: dict = Field(default_factory=dict)

    # Data provenance (deep-audit medium): make a saved result self-describing so
    # a synthetic-fallback run is never mistaken for a real backtest. data_source
    # is one of "csv" | "bitget_real" | "synthetic" | "synthetic_fallback";
    # used_synthetic is True for the latter two. Stamped by the runner.
    used_synthetic: bool = False
    data_source: str = "unknown"
