# RUNECLAW — Implementation Blueprint
## Monorepo Build Plan | Humanoid Traders

---

# 1. MONOREPO STRUCTURE

```
runeclaw/
│
├── apps/
│   ├── bot/                          # Telegram + CLI agent runtime
│   ├── api/                          # FastAPI backend service
│   └── web/                          # Next.js dashboard + landing
│
├── packages/
│   ├── core/                         # Agent loop, orchestrator, mode manager
│   ├── perception/                   # Market data ingestion + feature extraction
│   ├── decision/                     # Scoring, hypothesis, LLM thesis
│   ├── risk/                         # Risk engine, circuit breaker, portfolio
│   ├── execution/                    # Adapter layer: paper, backtest, dry, live
│   ├── journal/                      # Trade log, decision log, audit trail
│   ├── exchange/                     # Bitget adapter (isolates exchange specifics)
│   ├── models/                       # Pydantic schemas, data contracts, enums
│   └── utils/                        # Shared config, logging, time helpers
│
├── docs/
│   ├── gitbook/                      # Published documentation (8 pages)
│   ├── MASTER_BUILD_PLAN.md          # Strategic plan
│   ├── IMPLEMENTATION_BLUEPRINT.md   # This document
│   └── api/                          # API reference (auto-generated)
│
├── infra/
│   ├── docker/
│   │   ├── Dockerfile.bot
│   │   ├── Dockerfile.api
│   │   └── docker-compose.yml
│   ├── ci/
│   │   ├── lint.yml
│   │   ├── test.yml
│   │   └── typecheck.yml
│   └── deploy/
│       └── fly.toml                  # Optional deployment config
│
├── scripts/
│   ├── demo_script.md
│   ├── seed_portfolio.py             # Populate paper portfolio with sample data
│   ├── run_backtest.py               # CLI backtest runner
│   ├── export_journal.py             # Export trade journal to CSV
│   └── health_check.py               # Verify all modules load correctly
│
├── examples/
│   ├── sample_trade_idea.json
│   ├── sample_risk_check.json
│   ├── sample_portfolio.json
│   ├── sample_backtest_result.json
│   └── custom_skill_example.py       # How to write a custom skill
│
├── tests/
│   ├── unit/
│   │   ├── test_risk_engine.py
│   │   ├── test_portfolio.py
│   │   ├── test_analyzer.py
│   │   ├── test_scanner.py
│   │   ├── test_models.py
│   │   └── test_skill_registry.py
│   ├── integration/
│   │   ├── test_scan_to_idea.py      # Scanner → Analyzer pipeline
│   │   ├── test_idea_to_risk.py      # Analyzer → Risk gate pipeline
│   │   └── test_full_loop.py         # Full agent loop with paper execution
│   └── smoke/
│       ├── test_bot_startup.py
│       ├── test_api_health.py
│       └── test_telegram_commands.py
│
├── .env.example
├── .gitignore
├── Makefile
├── pyproject.toml                    # Single project with optional extras
├── README.md
└── LICENSE
```

### Migration Path from Current Structure

Current `bot/` directory maps to the monorepo as follows:

```
CURRENT                          →  MONOREPO TARGET
bot/config.py                    →  packages/utils/config.py
bot/core/engine.py               →  packages/core/engine.py
bot/core/market_scanner.py       →  packages/perception/scanner.py
bot/core/analyzer.py             →  packages/decision/analyzer.py
bot/risk/risk_engine.py          →  packages/risk/engine.py
bot/risk/portfolio.py            →  packages/risk/portfolio.py
bot/skills/skill_registry.py     →  packages/core/skills.py
bot/skills/telegram_handler.py   →  apps/bot/telegram.py
bot/utils/models.py              →  packages/models/schemas.py
bot/utils/logger.py              →  packages/journal/logger.py
bot/main.py                      →  apps/bot/main.py
```

**MVP approach:** Keep current flat `bot/` structure for hackathon submission. The monorepo layout is the target architecture — migrate incrementally. For the hackathon, the logical separation matters more than physical directory restructuring.

---

# 2. PACKAGE BREAKDOWN

## 2.1 `packages/core` — Agent Orchestrator

| Field | Value |
|-------|-------|
| **Responsibility** | Agent lifecycle, mode management, skill dispatch, pipeline sequencing |
| **Inputs** | Configuration, skill registry, mode selection |
| **Outputs** | Pipeline execution results, state transitions |
| **Public interfaces** | `RuneClawEngine.run_cycle()`, `RuneClawEngine.set_mode()`, `SkillRegistry.execute()` |
| **Dependencies** | `packages/models`, `packages/utils` |
| **Test strategy** | Unit test mode transitions. Integration test full cycle with mocked perception. Verify pipeline ordering invariant (risk always runs before execute). |

```python
# Public interface
class RuneClawEngine:
    async def run_cycle(self) -> CycleResult: ...
    async def run_skill(self, name: str, params: dict) -> SkillResult: ...
    def set_mode(self, mode: AgentMode) -> None: ...
    def get_state(self) -> EngineState: ...
    def halt(self, reason: str) -> None: ...

class SkillRegistry:
    def register(self, skill: BaseSkill) -> None: ...
    def get(self, name: str) -> BaseSkill: ...
    def list_skills(self) -> list[SkillInfo]: ...
    async def execute(self, name: str, params: dict) -> SkillResult: ...
```

## 2.2 `packages/perception` — Market Data + Feature Extraction

| Field | Value |
|-------|-------|
| **Responsibility** | Fetch raw market data, compute indicators, detect regime, produce typed signals |
| **Inputs** | Exchange adapter, symbol list, timeframe config |
| **Outputs** | `MarketSignal[]`, `FeatureVector`, `RegimeState` |
| **Public interfaces** | `Scanner.scan()`, `FeatureExtractor.extract()`, `RegimeDetector.classify()` |
| **Dependencies** | `packages/exchange`, `packages/models` |
| **Test strategy** | Unit test with fixture OHLCV data. Verify indicator math against known values. Test regime classifier with synthetic trend/range/chop data. |

```python
# Public interface
class Scanner:
    async def scan(self, symbols: list[str] | None = None) -> list[MarketSignal]: ...

class FeatureExtractor:
    def extract(self, candles: list[Candle]) -> FeatureVector: ...

class RegimeDetector:
    def classify(self, candles: list[Candle]) -> RegimeState: ...
    # RegimeState: TREND_UP | TREND_DOWN | RANGE | CHOP | UNKNOWN
```

## 2.3 `packages/decision` — Scoring + Hypothesis

| Field | Value |
|-------|-------|
| **Responsibility** | Transform perception outputs into scored trade hypotheses with reasoning |
| **Inputs** | `MarketSignal`, `FeatureVector`, `RegimeState` |
| **Outputs** | `TradeIdea` with direction, levels, confidence, reasoning |
| **Public interfaces** | `Analyzer.analyze()`, `ConfluenceScorer.score()`, `ThesisGenerator.generate()` |
| **Dependencies** | `packages/perception`, `packages/models`, `packages/utils` (for LLM client) |
| **Test strategy** | Unit test confluence scoring with known inputs. Test LLM fallback path (no API key). Verify every TradeIdea has non-empty reasoning. |

```python
# Public interface
class Analyzer:
    async def analyze(self, symbol: str, timeframe: str = "4h") -> TradeIdea: ...

class ConfluenceScorer:
    def score(self, features: FeatureVector, regime: RegimeState) -> float: ...

class ThesisGenerator:
    async def generate(self, signal: MarketSignal, features: FeatureVector) -> str: ...
    def generate_fallback(self, signal: MarketSignal, features: FeatureVector) -> str: ...
```

## 2.4 `packages/risk` — Risk Engine + Portfolio

| Field | Value |
|-------|-------|
| **Responsibility** | Independent validation of every trade proposal. Portfolio state tracking. |
| **Inputs** | `TradeIdea`, `PortfolioState` |
| **Outputs** | `RiskVerdict` (pass/reject with reasons), updated `PortfolioState` |
| **Public interfaces** | `RiskEngine.evaluate()`, `Portfolio.open_position()`, `Portfolio.close_position()` |
| **Dependencies** | `packages/models` |
| **Test strategy** | Unit test each of the 7 risk checks independently. Test circuit breaker trigger and reset. Test fail-closed behavior (inject error in one check, verify rejection). Fuzz test with random TradeIdea values. |

```python
# Public interface
class RiskEngine:
    def evaluate(self, idea: TradeIdea, portfolio: PortfolioState) -> RiskVerdict: ...
    def is_halted(self) -> bool: ...
    def reset_circuit_breaker(self) -> None: ...  # Manual reset only

class Portfolio:
    def open_position(self, trade: TradeExecution) -> Position: ...
    def close_position(self, position_id: str, exit_price: float) -> ClosedTrade: ...
    def check_stops(self, prices: dict[str, float]) -> list[StopEvent]: ...
    def get_state(self) -> PortfolioState: ...
    def save(self, path: str) -> None: ...
    def load(cls, path: str) -> "Portfolio": ...
```

## 2.5 `packages/execution` — Adapter Layer

| Field | Value |
|-------|-------|
| **Responsibility** | Route execution intents to the correct environment. Same interface for all modes. |
| **Inputs** | `ExecutionIntent`, mode config |
| **Outputs** | `OrderEvent` (fill confirmation or rejection) |
| **Public interfaces** | `ExecutionAdapter.execute()` — single method, polymorphic by mode |
| **Dependencies** | `packages/models`, `packages/exchange` (for live mode only) |
| **Test strategy** | Unit test paper fill logic. Verify backtest fill-at-close. Verify dry-run logs intent but does not execute. Verify live mode raises if not explicitly enabled. |

```python
# Public interface
class ExecutionAdapter(ABC):
    @abstractmethod
    async def execute(self, intent: ExecutionIntent) -> OrderEvent: ...

class PaperExecutor(ExecutionAdapter):      # Default
    async def execute(self, intent: ExecutionIntent) -> OrderEvent: ...

class BacktestExecutor(ExecutionAdapter):
    async def execute(self, intent: ExecutionIntent) -> OrderEvent: ...

class DryRunExecutor(ExecutionAdapter):      # Logs only
    async def execute(self, intent: ExecutionIntent) -> OrderEvent: ...

class LiveExecutor(ExecutionAdapter):        # Disabled by default
    async def execute(self, intent: ExecutionIntent) -> OrderEvent: ...

def create_executor(config: Config) -> ExecutionAdapter:
    """Factory. Returns PaperExecutor unless explicitly configured otherwise."""
```

## 2.6 `packages/journal` — Audit + Memory

| Field | Value |
|-------|-------|
| **Responsibility** | Append-only structured logging for every decision, trade, and risk event |
| **Inputs** | Decision events, trade events, risk events, system events |
| **Outputs** | Queryable log entries, performance statistics |
| **Public interfaces** | `Journal.record()`, `Journal.query()`, `Journal.stats()` |
| **Dependencies** | `packages/models` |
| **Test strategy** | Verify append-only (no overwrites). Verify every event type serializes correctly. Test stats computation against known trade set. |

```python
# Public interface
class Journal:
    def record(self, entry: JournalEntry) -> None: ...
    def query(self, filters: JournalQuery) -> list[JournalEntry]: ...
    def stats(self) -> PerformanceStats: ...
    def export_csv(self, path: str) -> None: ...
```

## 2.7 `packages/exchange` — Bitget Adapter

| Field | Value |
|-------|-------|
| **Responsibility** | Isolate all exchange-specific API calls behind a generic interface |
| **Inputs** | Symbol, timeframe, order parameters |
| **Outputs** | Typed market data, account state |
| **Public interfaces** | `ExchangeClient` interface with Bitget implementation |
| **Dependencies** | `packages/models`, `httpx` |
| **Test strategy** | Mock HTTP responses for unit tests. Integration test with Bitget sandbox. Verify all responses are parsed into typed models. |

```python
# Public interface — exchange-agnostic
class ExchangeClient(ABC):
    @abstractmethod
    async def get_tickers(self) -> list[Ticker]: ...
    @abstractmethod
    async def get_candles(self, symbol: str, interval: str, limit: int) -> list[Candle]: ...
    @abstractmethod
    async def get_orderbook(self, symbol: str, depth: int) -> Orderbook: ...
    @abstractmethod
    async def get_funding_rate(self, symbol: str) -> FundingRate: ...

class BitgetClient(ExchangeClient):
    """Bitget-specific implementation. Only file that imports Bitget SDK."""

class MockClient(ExchangeClient):
    """Returns fixture data. Used in tests and demos."""
```

## 2.8 `packages/models` — Data Contracts

| Field | Value |
|-------|-------|
| **Responsibility** | Single source of truth for all data structures |
| **Inputs** | None (schema definitions only) |
| **Outputs** | Pydantic models, enums, type aliases |
| **Public interfaces** | All model classes |
| **Dependencies** | `pydantic` only |
| **Test strategy** | Verify serialization round-trip for every model. Verify validation catches invalid data. |

## 2.9 `packages/utils` — Shared Utilities

| Field | Value |
|-------|-------|
| **Responsibility** | Configuration loading, time helpers, LLM client wrapper |
| **Inputs** | Environment variables, `.env` file |
| **Outputs** | Typed config object, utility functions |
| **Public interfaces** | `Config`, `get_llm_client()`, `utc_now()` |
| **Dependencies** | `python-dotenv`, `httpx` |
| **Test strategy** | Test config loading with missing values (verify defaults). Test LLM client fallback. |

---

# 3. DATA CONTRACTS

All models use Pydantic v2. Every model is serializable to JSON and includes a `timestamp` field.

## 3.1 Market Signal

```python
class MarketSignal(BaseModel):
    symbol: str                          # "BTC/USDT"
    exchange: str = "bitget"
    price: float
    change_24h_pct: float
    volume_24h: float
    volume_ratio: float                  # vs rolling average (e.g., 2.1x)
    momentum_score: float                # 0.0–1.0
    regime: RegimeState                  # TREND_UP | TREND_DOWN | RANGE | CHOP
    timestamp: datetime

class RegimeState(str, Enum):
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    RANGE = "range"
    CHOP = "chop"
    UNKNOWN = "unknown"
```

## 3.2 Feature Vector

```python
class FeatureVector(BaseModel):
    symbol: str
    timeframe: str                       # "1h", "4h", "1d"
    rsi_14: float
    macd_line: float
    macd_signal: float
    macd_histogram: float
    bb_upper: float
    bb_middle: float
    bb_lower: float
    bb_width: float
    adx_14: float | None = None         # For regime detection
    atr_14: float | None = None         # For stop-loss sizing
    vwap: float | None = None
    volume_sma_20: float | None = None
    price_vs_vwap: float | None = None  # % distance from VWAP
    timestamp: datetime
```

## 3.3 Decision Object (Trade Idea)

```python
class TradeIdea(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    symbol: str
    direction: Direction                 # LONG | SHORT
    entry_price: float
    stop_loss: float
    take_profit: float
    position_size_pct: float             # % of portfolio (e.g., 0.02 = 2%)
    confidence: float                    # 0.0–1.0
    risk_reward_ratio: float
    reasoning: str                       # Natural language thesis
    features: FeatureVector
    regime: RegimeState
    signal_source: str                   # "scanner" | "manual" | "backtest"
    timestamp: datetime

class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"
```

## 3.4 Risk Verdict

```python
class RiskVerdict(BaseModel):
    idea_id: str
    passed: bool
    checks: list[RiskCheckResult]
    rejection_reasons: list[str]         # Empty if passed
    circuit_breaker_active: bool
    portfolio_exposure_pct: float        # After this trade would execute
    timestamp: datetime

class RiskCheckResult(BaseModel):
    name: str                            # "position_size", "daily_loss", etc.
    passed: bool
    value: float                         # Actual measured value
    limit: float                         # Configured limit
    message: str
```

## 3.5 Execution Intent

```python
class ExecutionIntent(BaseModel):
    idea_id: str
    symbol: str
    direction: Direction
    entry_price: float
    stop_loss: float
    take_profit: float
    quantity: float                       # Computed from position_size_pct
    mode: ExecutionMode                   # PAPER | BACKTEST | DRY_RUN | LIVE
    confirmed_by: str                    # "human_telegram" | "human_cli" | "backtest_auto"
    risk_verdict_id: str
    timestamp: datetime

class ExecutionMode(str, Enum):
    PAPER = "paper"
    BACKTEST = "backtest"
    DRY_RUN = "dry_run"
    LIVE = "live"
```

## 3.6 Order Event

```python
class OrderEvent(BaseModel):
    intent_id: str
    order_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    status: OrderStatus                  # FILLED | REJECTED | SIMULATED | LOGGED
    fill_price: float
    fill_quantity: float
    fees: float = 0.0
    slippage_bps: float = 0.0           # Basis points of slippage
    mode: ExecutionMode
    exchange_order_id: str | None = None # Only for live orders
    timestamp: datetime

class OrderStatus(str, Enum):
    FILLED = "filled"
    REJECTED = "rejected"
    SIMULATED = "simulated"              # Paper trade
    LOGGED = "logged"                    # Dry run (intent only)
```

## 3.7 Position Snapshot

```python
class Position(BaseModel):
    id: str
    symbol: str
    direction: Direction
    entry_price: float
    current_price: float
    quantity: float
    stop_loss: float
    take_profit: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    opened_at: datetime
    idea_id: str

class PortfolioState(BaseModel):
    balance: float
    equity: float                        # balance + unrealized PnL
    open_positions: list[Position]
    closed_trades: list[ClosedTrade]
    total_pnl: float
    win_rate: float
    max_drawdown_pct: float
    daily_pnl: float
    daily_pnl_pct: float
    total_trades: int
    updated_at: datetime
```

## 3.8 Journal Entry

```python
class JournalEntry(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:16])
    category: JournalCategory            # TRADE | RISK | DECISION | SYSTEM
    action: str                          # "scan", "analyze", "risk_check", "execute", etc.
    symbol: str | None = None
    data: dict                           # Flexible payload (serialized model)
    reasoning: str | None = None
    outcome: str | None = None           # "passed", "rejected", "filled", etc.
    timestamp: datetime

class JournalCategory(str, Enum):
    TRADE = "trade"
    RISK = "risk"
    DECISION = "decision"
    SYSTEM = "system"
```

## 3.9 Backtest Result

```python
class BacktestResult(BaseModel):
    id: str
    symbol: str
    timeframe: str
    period_start: datetime
    period_end: datetime
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    total_pnl_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float | None = None
    avg_risk_reward: float
    avg_hold_time_hours: float
    trades: list[ClosedTrade]
    equity_curve: list[EquityPoint]
    parameters: dict                     # Config used for this run
    timestamp: datetime

class EquityPoint(BaseModel):
    timestamp: datetime
    equity: float
    drawdown_pct: float
```

## 3.10 Paper Trading Session

```python
class PaperSession(BaseModel):
    id: str
    started_at: datetime
    ended_at: datetime | None = None
    initial_balance: float
    current_balance: float
    total_trades: int
    active_positions: int
    total_pnl: float
    total_pnl_pct: float
    status: SessionStatus                # ACTIVE | PAUSED | COMPLETED

class SessionStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
```

## 3.11 Agent Health Status

```python
class AgentHealth(BaseModel):
    status: HealthStatus                 # HEALTHY | DEGRADED | HALTED
    mode: ExecutionMode
    circuit_breaker_active: bool
    uptime_seconds: int
    last_scan_at: datetime | None
    last_trade_at: datetime | None
    open_positions: int
    daily_pnl_pct: float
    error_count_24h: int
    exchange_connected: bool
    llm_available: bool
    version: str
    timestamp: datetime

class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"               # Non-critical component down
    HALTED = "halted"                    # Circuit breaker or fatal error
```

---

# 4. BACKEND DESIGN

## 4.1 API Routes

```
PREFIX: /api/v1

# Health
GET  /health                           → AgentHealth

# Market Perception
GET  /scan                             → list[MarketSignal]
GET  /scan/{symbol}                    → MarketSignal
GET  /features/{symbol}                → FeatureVector
GET  /regime/{symbol}                  → RegimeState

# Decision
POST /analyze/{symbol}                 → TradeIdea
GET  /ideas                            → list[TradeIdea]       (recent)
GET  /ideas/{id}                       → TradeIdea

# Risk
POST /risk/evaluate                    → RiskVerdict           (body: TradeIdea)
GET  /risk/status                      → RiskStatus            (circuit breaker, limits)
GET  /risk/checks                      → list[RiskCheckConfig] (current limits)

# Execution
POST /trade/confirm/{idea_id}          → OrderEvent            (paper trade)
POST /trade/reject/{idea_id}           → {status: "rejected"}

# Portfolio
GET  /portfolio                        → PortfolioState
GET  /portfolio/positions              → list[Position]
GET  /portfolio/history                → list[ClosedTrade]
GET  /portfolio/stats                  → PerformanceStats

# Journal
GET  /journal                          → list[JournalEntry]    (paginated, filterable)
GET  /journal/{id}                     → JournalEntry
GET  /journal/export                   → CSV download

# Backtest
POST /backtest                         → BacktestResult        (body: BacktestConfig)
GET  /backtest/results                 → list[BacktestResult]
GET  /backtest/results/{id}            → BacktestResult

# Session
GET  /session                          → PaperSession
POST /session/reset                    → PaperSession          (new session)
```

## 4.2 Service Layer

```
                    ┌─────────────┐
                    │  API Router  │
                    └──────┬──────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
   ┌──────▼──────┐  ┌─────▼──────┐  ┌──────▼──────┐
   │  ScanService │  │TradeService│  │ RiskService  │
   │              │  │            │  │              │
   │ · scan()     │  │ · analyze()│  │ · evaluate() │
   │ · features() │  │ · confirm()│  │ · status()   │
   │ · regime()   │  │ · reject() │  │ · checks()   │
   └──────┬───────┘  └─────┬──────┘  └──────┬──────┘
          │                │                │
   ┌──────▼────────────────▼────────────────▼──────┐
   │              Package Layer                     │
   │  perception / decision / risk / execution      │
   └───────────────────────────────────────────────┘
```

## 4.3 Background Workers

| Worker | Trigger | Function |
|--------|---------|----------|
| **Scheduled Scanner** | Cron (configurable, default 15m) | Run `scan()`, filter, log results |
| **Position Monitor** | Every 30s while positions are open | Check SL/TP against current prices |
| **Circuit Breaker Check** | Every 60s | Verify daily PnL and drawdown limits |
| **Health Reporter** | Every 60s | Update `AgentHealth` state |

Implementation: Python `asyncio` tasks managed by the engine. No external queue required for MVP.

## 4.4 Event Flow

```
User Command (/scan)
    │
    ▼
TelegramHandler.handle_scan()
    │
    ▼
ScanService.scan()
    ├── ExchangeClient.get_tickers()
    ├── FeatureExtractor.extract()         (for top movers)
    ├── RegimeDetector.classify()          (for top movers)
    └── return list[MarketSignal]
    │
    ▼
Journal.record(category=DECISION, action="scan", data=signals)
    │
    ▼
Format + send Telegram response
```

```
User Command (/analyze BTC)
    │
    ▼
TelegramHandler.handle_analyze()
    │
    ▼
TradeService.analyze("BTC/USDT")
    ├── ExchangeClient.get_candles()
    ├── FeatureExtractor.extract()
    ├── RegimeDetector.classify()
    ├── ConfluenceScorer.score()
    ├── ThesisGenerator.generate()
    └── return TradeIdea
    │
    ├── RiskEngine.evaluate(idea, portfolio)
    │   └── return RiskVerdict
    │
    ├── Journal.record(category=DECISION, action="analyze", data=idea)
    ├── Journal.record(category=RISK, action="evaluate", data=verdict)
    │
    ▼
Format idea + verdict → Telegram with Confirm/Reject buttons
    │
    ▼
User taps [Confirm]
    │
    ▼
TradeService.confirm(idea_id)
    ├── RiskEngine.evaluate(idea, portfolio)     ← RE-CHECK at confirmation time
    ├── ExecutionAdapter.execute(intent)
    ├── Portfolio.open_position(order_event)
    ├── Journal.record(category=TRADE, action="execute", data=order_event)
    └── return OrderEvent
```

## 4.5 Logging Model

Three log channels, all JSON, all append-only:

```
logs/
├── trade.jsonl        # Every execution: open, close, fill
├── risk.jsonl         # Every risk evaluation: pass, reject, circuit breaker
├── decision.jsonl     # Every scan, analysis, idea, thesis
└── system.jsonl       # Startup, shutdown, errors, health
```

Each line is a `JournalEntry` serialized as JSON with newline delimiter.

## 4.6 Telemetry Model

```python
# Computed metrics (not exported — calculated on demand)
class TelemetrySnapshot(BaseModel):
    scans_24h: int
    ideas_generated_24h: int
    ideas_rejected_by_risk_24h: int
    ideas_rejected_by_human_24h: int
    trades_executed_24h: int
    win_rate_7d: float
    avg_rr_7d: float
    max_drawdown_7d: float
    circuit_breaker_trips_7d: int
    avg_scan_latency_ms: float
    avg_analysis_latency_ms: float
    error_rate_24h: float
```

---

# 5. FRONTEND DESIGN

## Page Map

```
/                     → Landing page (current Viking-AI page)
/dashboard            → Main command center
/dashboard/signals    → Market scan results
/dashboard/decisions  → Trade ideas + risk verdicts
/dashboard/risk       → Risk monitor + circuit breaker status
/dashboard/journal    → Trade journal + decision log
/dashboard/backtest   → Backtest runner + results
/dashboard/session    → Paper trading session overview
/docs                 → Architecture + API reference (link to GitBook)
```

## Page Specifications

### `/dashboard` — Command Center

```
┌──────────────────────────────────────────────────────────┐
│  RUNECLAW ◆ Command Core          PAPER MODE    ● LIVE   │
├──────────────┬───────────────────────────────────────────┤
│              │                                           │
│  Portfolio   │   ┌─────────┐  ┌──────────┐  ┌────────┐ │
│  $10,247.30  │   │ Equity  │  │ Win Rate │  │ Today  │ │
│  +2.47%      │   │  curve  │  │  chart   │  │  PnL   │ │
│              │   │  (tiny) │  │  (donut) │  │  bar   │ │
│  Positions:2 │   └─────────┘  └──────────┘  └────────┘ │
│  Daily PnL:  │                                          │
│  +$47.30     │   RECENT ACTIVITY                        │
│              │   ┌──────────────────────────────────┐   │
│  Risk Status │   │ 12:34 ✓ BTC/USDT LONG opened    │   │
│  ● All clear │   │ 12:30 ✗ SOL idea rejected (risk)│   │
│              │   │ 12:15 ◆ Scan: 4 signals found   │   │
│  Circuit Brk │   │ 11:45 ✓ ETH/USDT TP hit +1.8%  │   │
│  ○ Inactive  │   └──────────────────────────────────┘   │
│              │                                           │
│  [Scan Now]  │   OPEN POSITIONS                         │
│  [Full Scan] │   ┌──────────────────────────────────┐   │
│              │   │ BTC/USDT LONG  +1.2%  SL:-1.1%  │   │
│              │   │ ETH/USDT LONG  +0.3%  SL:-1.5%  │   │
│              │   └──────────────────────────────────┘   │
└──────────────┴──────────────────────────────────────────┘
```

Data source: `GET /api/v1/portfolio`, `GET /api/v1/health`, `GET /api/v1/journal?limit=10`

### `/dashboard/signals` — Market Scan

- Table of latest scan results
- Columns: symbol, price, 24h change, volume ratio, momentum, regime
- Color-coded regime badges
- Click row → navigate to `/dashboard/decisions` filtered by symbol
- "Scan Now" button triggers `GET /api/v1/scan`

### `/dashboard/decisions` — Trade Ideas

- List of generated trade ideas
- Each card shows: direction, entry/SL/TP, confidence gauge, R:R, reasoning excerpt
- Risk verdict badge: PASSED (green) / REJECTED (red)
- Expandable detail: full reasoning, feature vector, risk check breakdown
- Confirm/Reject buttons for pending ideas

### `/dashboard/risk` — Risk Monitor

- Current risk engine status
- Gauges: daily PnL % (limit line at -5%), drawdown % (limit line at -10%)
- Portfolio exposure breakdown
- Circuit breaker status with trip history
- List of recent risk rejections with reasons

### `/dashboard/journal` — Trade Journal

- Filterable log viewer
- Filters: category (trade/risk/decision/system), date range, symbol
- Each entry expandable to show full data payload
- Export to CSV button

### `/dashboard/backtest` — Backtest Runner

- Configuration form: symbol, timeframe, period, parameters
- Results: equity curve chart, trade list, statistics table
- Compare multiple runs side-by-side

### `/dashboard/session` — Session Overview

- Paper trading session metrics
- Balance over time chart
- Session history (previous sessions)
- Reset session button

### Technology

| Choice | Rationale |
|--------|-----------|
| **Next.js 14+ (App Router)** | SSR for landing page SEO, client components for dashboard interactivity |
| **Tailwind CSS** | Consistent with dark theme, rapid iteration |
| **Recharts or Lightweight Charts** | Trading charts without heavy dependencies |
| **SWR or React Query** | Data fetching with polling for real-time feel |

### MVP vs Ambitious

| MVP | Ambitious |
|-----|-----------|
| Landing page (done) + Telegram | Full dashboard with all pages |
| Static portfolio view | Real-time WebSocket updates |
| No charts | Equity curve, PnL charts, candlestick |

---

# 6. BITGET ADAPTER LAYER

## Design Principle

All Bitget-specific code lives in `packages/exchange/bitget.py`. Every other module interacts through the `ExchangeClient` abstract interface. This means:

1. Bitget can be replaced with Binance, OKX, or a mock without touching any other code
2. Tests use `MockClient` — no exchange dependency
3. API keys are only loaded in the Bitget implementation

## Interface

```python
class ExchangeClient(ABC):
    """Exchange-agnostic interface. All methods return typed models."""

    # Market Data (public, no auth required)
    async def get_tickers(self) -> list[Ticker]: ...
    async def get_candles(
        self, symbol: str, interval: str, limit: int = 100
    ) -> list[Candle]: ...
    async def get_orderbook(self, symbol: str, depth: int = 20) -> Orderbook: ...
    async def get_funding_rate(self, symbol: str) -> FundingRate: ...

    # Account (requires auth)
    async def get_balance(self) -> AccountBalance: ...
    async def get_positions(self) -> list[ExchangePosition]: ...

    # Orders (requires auth, LIVE MODE ONLY)
    async def place_order(self, order: OrderRequest) -> OrderResponse: ...
    async def cancel_order(self, order_id: str) -> bool: ...
    async def get_order(self, order_id: str) -> OrderResponse: ...
```

## Bitget Implementation

```python
class BitgetClient(ExchangeClient):
    BASE_URL = "https://api.bitget.com"

    def __init__(self, api_key: str = "", secret: str = "", passphrase: str = ""):
        self._api_key = api_key
        self._secret = secret
        self._passphrase = passphrase
        self._client = httpx.AsyncClient(base_url=self.BASE_URL, timeout=10)

    async def get_tickers(self) -> list[Ticker]:
        # GET /api/v2/spot/market/tickers (public, no auth)
        resp = await self._client.get("/api/v2/spot/market/tickers")
        return [Ticker.from_bitget(t) for t in resp.json()["data"]]

    async def get_candles(self, symbol: str, interval: str, limit: int = 100) -> list[Candle]:
        # GET /api/v2/spot/market/candles
        params = {"symbol": symbol, "granularity": interval, "limit": str(limit)}
        resp = await self._client.get("/api/v2/spot/market/candles", params=params)
        return [Candle.from_bitget(c) for c in resp.json()["data"]]

    async def place_order(self, order: OrderRequest) -> OrderResponse:
        if not self._api_key:
            raise ExchangeError("Live trading requires API credentials")
        # Signed request to order endpoint
        ...
```

## Mock Implementation

```python
class MockClient(ExchangeClient):
    """Fixture-based client for tests, demos, and backtesting."""

    def __init__(self, fixture_dir: str = "tests/fixtures"):
        self._fixtures = fixture_dir

    async def get_tickers(self) -> list[Ticker]:
        data = json.loads(Path(f"{self._fixtures}/tickers.json").read_text())
        return [Ticker(**t) for t in data]

    async def get_candles(self, symbol: str, interval: str, limit: int = 100) -> list[Candle]:
        data = json.loads(Path(f"{self._fixtures}/candles_{symbol}_{interval}.json").read_text())
        return [Candle(**c) for c in data[:limit]]
```

## Paper Trading Abstraction

Paper trading does NOT go through the exchange client. It is handled entirely by `PaperExecutor` + `Portfolio`:

```
TradeIdea → RiskEngine → HumanConfirm → PaperExecutor
                                              │
                                              ▼
                                    Portfolio.open_position()
                                    (local state, no API call)
```

The `PaperExecutor` fills at the current market price (fetched from `ExchangeClient.get_tickers()` for price reference only). No orders are placed.

## Optional: MCP Integration Points

If integrating with Model Context Protocol (MCP) for Bitget Agent Hub:

```python
# Each skill maps to an MCP tool
MCP_TOOLS = {
    "runeclaw_scan":     {"handler": scan_skill,     "description": "Scan markets for signals"},
    "runeclaw_analyze":  {"handler": analyze_skill,  "description": "Analyze asset and generate trade idea"},
    "runeclaw_risk":     {"handler": risk_skill,     "description": "Evaluate trade against risk limits"},
    "runeclaw_execute":  {"handler": execute_skill,  "description": "Execute confirmed paper trade"},
    "runeclaw_portfolio":{"handler": portfolio_skill, "description": "Get portfolio state"},
    "runeclaw_explain":  {"handler": explain_skill,  "description": "Explain a trade decision"},
}
```

Each tool receives structured input and returns structured output — the same Pydantic models used internally.

---

# 7. RISK SYSTEM

## Complete Risk Check Matrix

### Pre-Trade Checks (ALL must pass)

| # | Check | Parameter | Default | Behavior on Fail |
|---|-------|-----------|---------|------------------|
| 1 | **Circuit breaker** | `circuit_breaker_active` | — | If active, reject immediately. No further checks. |
| 2 | **Position size** | `max_position_pct` | 2% | Reject if `position_size_pct > max_position_pct` |
| 3 | **Daily loss** | `max_daily_loss_pct` | 5% | Reject if `daily_pnl_pct < -max_daily_loss_pct` |
| 4 | **Max drawdown** | `max_drawdown_pct` | 10% | Reject if `current_drawdown > max_drawdown_pct`. Also triggers circuit breaker. |
| 5 | **Max positions** | `max_open_positions` | 5 | Reject if `len(open_positions) >= max_open_positions` |
| 6 | **Risk-reward ratio** | `min_risk_reward` | 1.5 | Reject if `risk_reward_ratio < min_risk_reward` |
| 7 | **Confidence threshold** | `min_confidence` | 0.6 | Reject if `confidence < min_confidence` |

### Extended Checks (Phase 2)

| # | Check | Parameter | Default | Behavior |
|---|-------|-----------|---------|----------|
| 8 | **Symbol exposure** | `max_symbol_pct` | 4% | Max total exposure per symbol across all positions |
| 9 | **Sector correlation** | `max_corr_positions` | 3 | Max positions in correlated assets (e.g., BTC+ETH+SOL = 3 "large cap L1") |
| 10 | **Leverage guard** | `max_leverage` | 1x (spot) | Reject if implied leverage exceeds limit |
| 11 | **Cooldown** | `cooldown_minutes` | 15 | Minimum time between trades on the same symbol |
| 12 | **Regime filter** | `allowed_regimes` | all | Reject if current regime is in blacklist (e.g., no trading in CHOP) |
| 13 | **Volatility guard** | `max_atr_pct` | 5% | Reject if ATR% exceeds threshold (too volatile) |
| 14 | **Time-of-day filter** | `no_trade_hours` | [] | UTC hours during which no new trades are opened |

### Circuit Breaker Logic

```
TRIGGERS (any one activates):
  - daily_pnl_pct < -max_daily_loss_pct          (default: -5%)
  - current_drawdown > max_drawdown_pct           (default: -10%)
  - consecutive_losses >= max_consecutive_losses   (default: 5)
  - manual activation via /halt command

WHEN ACTIVE:
  - All new trade proposals are rejected immediately
  - Existing positions continue to be monitored (SL/TP still active)
  - No new scans trigger trade ideas
  - Status displayed in all interfaces

RESET:
  - Manual only: /reset_circuit_breaker command
  - Requires explicit human action
  - Logged as SYSTEM event in journal
  - NEVER auto-resets
```

### Kill Switch

```python
class KillSwitch:
    """Emergency halt. Closes all positions, halts all activity."""

    def activate(self, reason: str) -> None:
        # 1. Set circuit_breaker_active = True
        # 2. Close all open positions at market (paper)
        # 3. Log SYSTEM event with reason
        # 4. Send Telegram alert
        # 5. Refuse all subsequent commands except /status

    def is_active(self) -> bool: ...
```

### Anomaly Detection (Ambitious)

```
Monitor for:
  - Sudden volume spike > 5x average on open position → alert
  - Price gap > 2x ATR in one candle → alert
  - Funding rate extreme (>0.1% per 8h) → alert
  - Exchange connectivity loss > 60s → auto-halt new trades
  - Multiple risk rejections in short period → warn user

These are alerts, not automatic actions (except connectivity loss).
```

---

# 8. IMPLEMENTATION ROADMAP

## Phase 0: Bootstrap (Day 0)

| Task | Output | Time |
|------|--------|------|
| Initialize repo structure | Clean monorepo skeleton | — |
| Set up `pyproject.toml` with dependencies | Installable project | — |
| Create `.env.example` with all variables | Configuration reference | — |
| Verify `make install` works on clean machine | Reproducible setup | — |
| Set up GitHub repo, push initial commit | Live repo | — |

**Status: DONE.** Current repo has all of this.

## Phase 1: Backend Core (Days 1-2)

| Task | Priority | Depends On | Output |
|------|----------|------------|--------|
| Verify all Pydantic models serialize correctly | P0 | — | Type-safe data layer |
| Test scanner with live Bitget API | P0 | — | Working perception |
| Test analyzer end-to-end (with and without LLM key) | P0 | — | Working decision engine |
| Test risk engine with edge cases | P0 | — | Validated risk gate |
| Test paper portfolio open/close/PnL | P0 | — | Working execution |
| Add portfolio persistence (save/load JSON) | P1 | Portfolio | Survives restart |
| Add regime detection (ADX + BB width) | P1 | FeatureExtractor | Differentiator |
| Write unit tests for risk engine | P1 | Risk engine | Test coverage |

**Status: Core implemented.** Needs end-to-end testing and regime detection.

## Phase 2: Agent Loop (Days 2-3)

| Task | Priority | Depends On | Output |
|------|----------|------------|--------|
| Test full Telegram flow: scan → analyze → risk → confirm → execute | P0 | Phase 1 | Working demo path |
| Add re-check at confirmation time (risk may have changed) | P0 | Risk engine | Safety invariant |
| Add position monitor (SL/TP checking loop) | P1 | Portfolio | Auto-exit on stops |
| Add `/explain {trade_id}` command | P1 | Journal | Explainability demo |
| Add `/backtest BTC 7d` command | P2 | Analyzer, Portfolio | Backtest capability |
| Add circuit breaker auto-check worker | P1 | Risk engine | Auto-halt safety |

**Status: Most implemented.** Needs integration testing and monitor loop.

## Phase 3: Frontend (Days 3-4)

| Task | Priority | Depends On | Output |
|------|----------|------------|--------|
| Landing page polish (current) | P0 | — | Already done |
| FastAPI wrapper around existing skills | P1 | Phase 2 | API for dashboard |
| Dashboard: portfolio view + recent activity | P2 | API | Visual demo |
| Dashboard: risk monitor gauge | P2 | API | Judge appeal |
| Dashboard: trade journal viewer | P3 | API | Explainability visual |

**Status: Landing page done. Dashboard is optional polish.**

## Phase 4: Docs + Demo (Days 4-5)

| Task | Priority | Depends On | Output |
|------|----------|------------|--------|
| Record demo video (3 min) | P0 | Phase 2 | Submission requirement |
| Pre-populate demo portfolio with sample trades | P0 | Portfolio | Realistic demo |
| Test setup instructions on clean machine | P0 | All | Reproducible |
| Verify all GitBook pages are published | P0 | GitBook account | Live docs |
| Verify all README links work | P0 | GitHub + GitBook | Clean submission |
| Prepare risk rejection demo (show trade blocked) | P0 | Risk engine | Money shot |
| Write 1-paragraph hackathon submission text | P0 | — | Submission form |

## Phase 5: Polish (Day 5+)

| Task | Impact | Effort |
|------|--------|--------|
| Add more unit tests (target 80% coverage on risk) | Medium | Medium |
| Add type checking with mypy (strict mode) | Low | Low |
| Add pre-commit hooks (ruff + mypy) | Low | Low |
| Docker build verification | Low | Low |
| Performance optimization (scan latency) | Low | Medium |
| Add more exchange perception modules | Low | High |

---

# 9. QUALITY CONTROLS

## Mandatory Tests

```python
# tests/unit/test_risk_engine.py — MINIMUM SET
def test_position_size_within_limit():          # 1.5% → pass
def test_position_size_exceeds_limit():         # 3% → reject
def test_daily_loss_within_limit():             # -3% → pass
def test_daily_loss_exceeds_limit():            # -6% → reject
def test_drawdown_triggers_circuit_breaker():   # -11% → reject + circuit breaker
def test_circuit_breaker_blocks_all_trades():   # Any idea → reject
def test_circuit_breaker_requires_manual_reset():
def test_max_positions_enforced():
def test_risk_reward_below_minimum():
def test_confidence_below_threshold():
def test_error_in_check_causes_rejection():     # CRITICAL: fail-closed
def test_all_checks_must_pass():                # One fail = overall reject

# tests/unit/test_portfolio.py
def test_open_position():
def test_close_position_profit():
def test_close_position_loss():
def test_pnl_calculation():
def test_drawdown_calculation():
def test_stop_loss_trigger():
def test_take_profit_trigger():
def test_portfolio_save_load():

# tests/unit/test_models.py
def test_trade_idea_serialization():
def test_risk_verdict_serialization():
def test_order_event_serialization():
def test_portfolio_state_serialization():
def test_invalid_confidence_rejected():         # >1.0 or <0.0
def test_invalid_direction_rejected():
```

## Integration Tests

```python
# tests/integration/test_full_loop.py
async def test_scan_returns_signals():
async def test_analyze_returns_trade_idea():
async def test_idea_passes_risk_and_executes_paper():
async def test_idea_fails_risk_and_is_rejected():
async def test_full_cycle_scan_to_paper_trade():
async def test_journal_records_every_step():
```

## Smoke Tests

```python
# tests/smoke/test_startup.py
def test_config_loads_from_env():
def test_engine_initializes():
def test_skill_registry_has_all_skills():
def test_risk_engine_initializes():
def test_portfolio_initializes():
def test_mock_client_works():
```

## Linting

```toml
# pyproject.toml
[tool.ruff]
target-version = "py311"
line-length = 100
select = ["E", "F", "I", "N", "UP", "B", "SIM", "RUF"]

[tool.ruff.lint.isort]
known-first-party = ["runeclaw"]
```

## Type Checking

```toml
[tool.mypy]
python_version = "3.11"
strict = true
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
```

## Simulation Scenarios

```python
# tests/scenarios/ — scripted scenarios that exercise the full system

def scenario_normal_trade():
    """Scan → find signal → analyze → risk pass → confirm → paper execute → monitor → TP hit"""

def scenario_risk_rejection():
    """Analyze → risk fail (position too large) → reject → log → no trade"""

def scenario_circuit_breaker():
    """5 losses in a row → circuit breaker trips → next idea rejected → manual reset"""

def scenario_multiple_positions():
    """Open 5 positions → 6th idea rejected (max positions) → close one → 6th passes"""

def scenario_drawdown_halt():
    """Portfolio drops 11% → circuit breaker → all new ideas rejected"""
```

## Demo Reliability Checks

Before every demo:

```bash
# Run this checklist
make install                    # Dependencies install cleanly
make lint                       # No linting errors
make test                       # All tests pass
python -c "from bot.config import Config; c = Config(); print(c.SIMULATION_MODE)"  # True
python -c "from bot.risk.risk_engine import RiskEngine; r = RiskEngine(); print(r.is_halted())"  # False
python scripts/health_check.py  # All modules load
python scripts/seed_portfolio.py # Demo data populated
# Then run the exact demo sequence from scripts/demo_script.md
```

---

*Document: Implementation Blueprint v1.0*
*Project: RUNECLAW — Humanoid Traders*
*Target: Bitget AI Builder Base Camp*
*Status: Core implemented. Integration testing phase.*