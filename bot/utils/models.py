"""
RUNECLAW Data Models -- strict Pydantic schemas for every domain object.
Mutable Pydantic BaseModel instances (not frozen). Mutation is possible but
discouraged outside the owning module. If immutability is needed for a model,
add `model_config = ConfigDict(frozen=True)` explicitly.
"""

from __future__ import annotations

import math
from datetime import datetime
from uuid import uuid4
from bot.compat import UTC
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, model_validator


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class RiskVerdict(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class TradeStatus(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class AgentState(str, Enum):
    """Formal FSM states for the RUNECLAW agent."""
    IDLE = "IDLE"
    SCANNING = "SCANNING"
    ANALYZING = "ANALYZING"
    RISK_CHECK = "RISK_CHECK"
    CONFIRMING = "CONFIRMING"
    EXECUTING = "EXECUTING"
    MONITORING = "MONITORING"
    COOLING_DOWN = "COOLING_DOWN"
    HALTED = "HALTED"


# -- Market Scanner Output --

class MarketSignal(BaseModel):
    """A structured signal emitted by the market scanner."""
    symbol: str
    price: float = Field(..., ge=0)  # C2-60 FIX: non-negative constraint
    change_pct_24h: float
    volume_usd_24h: float = Field(..., ge=0)  # C2-60 FIX: non-negative constraint
    volume_spike: bool = False
    volume_spike_ratio: float = Field(default=0.0, ge=0)  # C2-22 FIX: actual field for spike filter
    momentum_score: float = Field(default=0.0, ge=-1.0, le=1.0)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    asset_category: str = "Crypto"  # Crypto | Metal | Commodity | ETF | Pre-IPO | Stock


class StateTransition(BaseModel):
    """Record of an agent state change."""
    from_state: AgentState
    to_state: AgentState
    reason: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# -- AI Analyzer Output --

class TradeIdea(BaseModel):
    """A fully-formed trade thesis produced by the AI analyzer."""
    id: str = Field(default_factory=lambda: f"TI-{uuid4().hex[:8]}")
    asset: str
    direction: Direction
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence: float = Field(ge=0.0, le=1.0)
    # #35: the analyzer-stage blended confidence BEFORE the calibration/expectancy
    # adjustments — the exact value the calibrator is applied to. Carried so the
    # decision record can persist it as the calibrator's training field (train and
    # apply on the same field). None = not set by the analyzer.
    blended_confidence_raw: Optional[float] = None
    reasoning: str
    signals_used: list[str] = Field(default_factory=list)
    source: str = "unknown"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    order_type: str = "market"  # "market" or "limit"
    strategy_type: str = "swing"  # "scalp" | "intraday" | "swing" | "position"
    signal_type: str = "momentum_confluence"  # momentum_confluence | vwap_reversion | regime_trend | volume_spike | funding_arb | unknown
    # Provenance + evidence fields (audit fix #18) — all optional so every
    # existing construction site stays valid; the analyzer populates them.
    timeframe: Optional[str] = None
    llm_confidence: Optional[float] = None       # raw thesis confidence pre-blend
    confluence_score: Optional[float] = None     # deterministic voter consensus
    model_provider: Optional[str] = None         # LLM provider/model when used
    prompt_hash: Optional[str] = None            # sha256[:16] of the thesis prompt
    analysis_version: Optional[str] = None       # analyzer schema/version tag
    data_bars: Optional[int] = None              # bars the analysis actually saw
    data_thin: Optional[bool] = None             # <50-bar window (see audit #10)

    @property
    def risk_reward_ratio(self) -> float:
        risk = abs(self.entry_price - self.stop_loss)
        reward = abs(self.take_profit - self.entry_price)
        return round(reward / risk, 2) if risk > 0 else 0.0

    @model_validator(mode="after")
    def _validate_directional_sanity(self) -> "TradeIdea":
        """Ensure SL/TP are on the correct side of entry for the given direction."""
        # Audit F-6: reject non-finite prices first. NaN defeats every
        # subsequent comparison (`nan <= 0`, `nan >= entry` are all False), so a
        # NaN/inf entry/SL/TP would otherwise pass directional sanity and most
        # risk checks, leaving only the volatility guard as an incidental catch.
        for _name in ("entry_price", "stop_loss", "take_profit"):
            _val = getattr(self, _name)
            if not math.isfinite(_val):
                raise ValueError(f"{_name} must be a finite number, got {_val}")
        # C2-59 FIX: Reject non-positive entry price instead of silently skipping
        if self.entry_price <= 0:
            raise ValueError(f"entry_price must be positive, got {self.entry_price}")
        if self.direction == Direction.LONG:
            if self.stop_loss >= self.entry_price:
                raise ValueError(
                    f"LONG stop_loss ({self.stop_loss}) must be below entry ({self.entry_price})"
                )
            if self.take_profit <= self.entry_price:
                raise ValueError(
                    f"LONG take_profit ({self.take_profit}) must be above entry ({self.entry_price})"
                )
        elif self.direction == Direction.SHORT:
            if self.stop_loss <= self.entry_price:
                raise ValueError(
                    f"SHORT stop_loss ({self.stop_loss}) must be above entry ({self.entry_price})"
                )
            if self.take_profit >= self.entry_price:
                raise ValueError(
                    f"SHORT take_profit ({self.take_profit}) must be below entry ({self.entry_price})"
                )
        return self


# -- Risk Engine Output --

class RiskCheck(BaseModel):
    """Result of a pre-trade risk evaluation."""
    trade_id: str
    verdict: RiskVerdict
    position_size_usd: float = 0.0
    position_pct: float = 0.0
    daily_loss_pct: float = 0.0
    drawdown_pct: float = 0.0
    checks_passed: list[str] = Field(default_factory=list)
    checks_failed: list[str] = Field(default_factory=list)
    reason: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# -- Execution Record --

class TradeExecution(BaseModel):
    """Record of a trade (paper or live)."""
    trade_id: str
    asset: str
    direction: Direction
    entry_price: float
    quantity: float
    stop_loss: float
    take_profit: float
    status: TradeStatus = TradeStatus.PENDING
    pnl: float = 0.0          # net PnL (after commission)
    gross_pnl: float = 0.0    # PnL before commission
    commission: float = 0.0   # exchange commission (entry + exit)
    exit_price: Optional[float] = None
    is_paper: bool = True
    leverage: float = 1.0      # leverage multiplier (1 = spot / no leverage)
    entry_atr: float = 0.0     # C2-48: actual ATR at trade entry (0 = legacy/unavailable)
    strategy_type: str = "swing"  # "scalp" | "intraday" | "swing" | "position"
    signal_type: str = "momentum_confluence"  # momentum_confluence | vwap_reversion | regime_trend | volume_spike | funding_arb | unknown
    opened_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    closed_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _validate_execution_sanity(self):
        if self.entry_price <= 0:
            raise ValueError(f"entry_price must be > 0, got {self.entry_price}")
        if self.quantity <= 0:
            raise ValueError(f"quantity must be > 0, got {self.quantity}")
        if self.commission < 0:
            raise ValueError(f"commission must be >= 0, got {self.commission}")
        if self.stop_loss <= 0:
            raise ValueError(f"stop_loss must be > 0, got {self.stop_loss}")
        if self.take_profit <= 0:
            raise ValueError(f"take_profit must be > 0, got {self.take_profit}")
        # H-11: Directional SL/TP validation (same logic as TradeIdea)
        if self.direction == Direction.LONG:
            if self.stop_loss >= self.entry_price:
                raise ValueError(
                    f"LONG stop_loss ({self.stop_loss}) must be below entry ({self.entry_price})"
                )
            if self.take_profit <= self.entry_price:
                raise ValueError(
                    f"LONG take_profit ({self.take_profit}) must be above entry ({self.entry_price})"
                )
        elif self.direction == Direction.SHORT:
            if self.stop_loss <= self.entry_price:
                raise ValueError(
                    f"SHORT stop_loss ({self.stop_loss}) must be above entry ({self.entry_price})"
                )
            if self.take_profit >= self.entry_price:
                raise ValueError(
                    f"SHORT take_profit ({self.take_profit}) must be below entry ({self.entry_price})"
                )
        return self


# -- Portfolio Snapshot --

class PortfolioState(BaseModel):
    """Point-in-time snapshot of the portfolio."""
    balance_usd: float
    equity_usd: float
    open_positions: int
    total_trades: int
    win_rate: float = 0.0
    total_pnl: float = 0.0
    total_gross_pnl: float = 0.0
    total_commission: float = 0.0
    daily_pnl: float = 0.0
    max_drawdown_pct: float = 0.0
    # Reserved — not currently populated by _snapshot_locked()
    portfolio_exposure_pct: float = 0.0
    # Operating costs (LLM + infra) — separate from trade PnL
    operating_cost_usd: float = 0.0
    cost_per_trade: float = 0.0       # operating cost / total trades
    net_of_cost_equity: float = 0.0   # equity - operating cost
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MetricsSnapshot(BaseModel):
    """Performance analytics snapshot."""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    avg_holding_period_hours: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    current_streak: int = 0  # positive = wins, negative = losses
    total_pnl: float = 0.0
    total_commission: float = 0.0
    net_pnl: float = 0.0
    equity_high: float = 0.0
    risk_checks_total: int = 0
    risk_checks_rejected: int = 0
    circuit_breaker_trips: int = 0
    per_symbol_stats: dict[str, dict] = Field(default_factory=dict)
    per_strategy_stats: dict[str, dict] = Field(default_factory=dict)
    signals_attribution: dict[str, dict] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
