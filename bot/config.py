"""
RUNECLAW Configuration -- AI Trading Command Core
All settings loaded from environment with safe defaults.
Simulation mode is ON by default; live trading requires explicit opt-in.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv(override=True)


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_bool(key: str, default: bool = False) -> bool:
    return _env(key, str(default)).lower() in ("true", "1", "yes")


def _env_float(key: str, default: float = 0.0) -> float:
    try:
        return float(_env(key, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class RiskLimits:
    """Hard risk limits -- breaching any one triggers circuit breaker."""
    max_position_pct: float = _env_float("MAX_POSITION_PCT", 2.0)
    max_daily_loss_pct: float = _env_float("MAX_DAILY_LOSS_PCT", 5.0)
    max_drawdown_pct: float = _env_float("MAX_DRAWDOWN_PCT", 10.0)
    max_open_positions: int = int(_env_float("MAX_OPEN_POSITIONS", 5))
    # Note: max_correlation coefficient is reserved for a future pairwise correlation
    # matrix check. Currently, concentration is enforced by max_correlation_per_group
    # (a group-count limit), not by this coefficient value.
    max_correlation: float = _env_float("MAX_CORRELATION", 0.85)
    # Extended risk checks (checks 6-16)
    min_risk_reward: float = _env_float("MIN_RISK_REWARD", 1.2)
    # SIGNAL QUALITY: 0.55 is the tuned threshold -- relaxed from 0.60 to allow
    # more signals through while still filtering weak setups
    min_confidence: float = _env_float("MIN_CONFIDENCE", 0.55)
    max_consecutive_losses: int = int(_env_float("MAX_CONSECUTIVE_LOSSES", 5))
    cooldown_after_loss_seconds: int = int(_env_float("COOLDOWN_AFTER_LOSS_SEC", 120))
    max_portfolio_exposure_pct: float = _env_float("MAX_PORTFOLIO_EXPOSURE_PCT", 80.0)
    max_symbol_exposure_pct: float = _env_float("MAX_SYMBOL_EXPOSURE_PCT", 20.0)
    max_correlation_per_group: int = int(_env_float("MAX_CORRELATION_PER_GROUP", 2))
    # Volatility guard: reject trades when ATR exceeds this % of price.
    # BTC hourly ATR is typically 1-4%; 7% allows for elevated-vol periods
    # while blocking extreme conditions.
    volatility_guard_atr_pct: float = _env_float("VOLATILITY_GUARD_ATR_PCT", 7.0)
    stale_data_max_age_seconds: int = int(_env_float("STALE_DATA_MAX_AGE_SEC", 300))
    require_stop_loss: bool = _env_bool("REQUIRE_STOP_LOSS", True)
    # Portfolio VaR: reject trades that would push parametric VaR above this %.
    max_portfolio_var_pct: float = _env_float("MAX_PORTFOLIO_VAR_PCT", 15.0)
    # Exchange commission per side (taker fee).  0.1% = Bitget taker default.
    commission_pct: float = _env_float("COMMISSION_PCT", 0.1)
    # Liquidity guard: minimum order-book depth (per side) in USD.
    # Scaled dynamically by position size; this is the absolute floor.
    # Default $2K allows micro-test trades ($10-$50) to pass on smaller pairs.
    min_book_depth_usd: float = _env_float("MIN_BOOK_DEPTH_USD", 2_000.0)


@dataclass(frozen=True)
class ExchangeConfig:
    """Bitget API credentials and trading mode."""
    api_key: str = _env("BITGET_API_KEY")
    api_secret: str = _env("BITGET_API_SECRET")
    passphrase: str = _env("BITGET_PASSPHRASE")
    sandbox: bool = _env_bool("BITGET_SANDBOX", True)  # Sandbox by default; override via env
    # Asset universe filter: "all" scans everything, "solana" adds Solana ecosystem priority
    asset_universe: str = _env("ASSET_UNIVERSE", "all")  # all | solana | custom
    # Trading mode: "spot" for no leverage, "futures" for USDT-M perpetual
    trade_mode: str = _env("TRADE_MODE", "futures")
    # Default leverage (1x = no leverage, 5x = default for futures)
    default_leverage: int = int(_env_float("DEFAULT_LEVERAGE", 5))
    # Margin mode: "crossed" or "isolated"
    margin_mode: str = _env("MARGIN_MODE", "crossed")


# Solana ecosystem tokens tracked on Bitget (centralized pairs).
# Updated 2026-05. Used when ASSET_UNIVERSE=solana to prioritize
# these symbols and add ecosystem-level correlation awareness.
SOLANA_ECOSYSTEM_SYMBOLS: list[str] = [
    "SOL/USDT", "JUP/USDT", "JTO/USDT", "BONK/USDT", "WIF/USDT",
    "PYTH/USDT", "RAY/USDT", "ORCA/USDT", "RENDER/USDT", "HNT/USDT",
    "MOBILE/USDT", "W/USDT", "JITO/USDT", "TENSOR/USDT", "DRIFT/USDT",
]


# US Stock tokenized perpetual contracts on Bitget.
# Bitget uses two formats for tokenized equities:
#   - "ON" suffix: AAPLON/USDT (primary tokenized derivatives)
#   - "R" prefix: RAAPL/USDT (replica/RWA tokens)
# These track US equity prices, tradeable 24/7 on spot market.
# Track 3: US Stock AI Trading capability.
US_STOCK_SYMBOLS: list[str] = [
    # Primary tokenized ("ON" suffix) — higher liquidity
    "AAPLON/USDT", "MSFTON/USDT", "GOOGLON/USDT", "AMZNON/USDT",
    "METAON/USDT", "NVDAON/USDT", "TSLAON/USDT", "AMDON/USDT",
    "QQQON/USDT", "SPYON/USDT",
    # Replica RWA tokens ("R" prefix) — broader coverage
    "RAAPL/USDT", "RMSFT/USDT", "RGOOGL/USDT", "RAMZN/USDT",
    "RMETA/USDT", "RNVDA/USDT", "RTSLA/USDT", "RAMD/USDT",
    "RSPY/USDT", "RQQQ/USDT",
    "RCOIN/USDT",  # Coinbase — crypto-adjacent
    "RHOOD/USDT",  # Robinhood — crypto-adjacent
    "RARM/USDT",   # ARM Holdings
    "RMRVL/USDT",  # Marvell
    "RDELL/USDT",  # Dell
    "RINTC/USDT",  # Intel
    "RNOK/USDT",   # Nokia
    "RANET/USDT",  # Arista Networks
]

# US stock market hours (Eastern Time / UTC-4 during EDT)
# Regular session: 09:30-16:00 ET
# Pre-market: 04:00-09:30 ET
# After-hours: 16:00-20:00 ET
US_MARKET_OPEN_HOUR_UTC = 13   # 09:00 ET in UTC (pre-market starts)
US_MARKET_CLOSE_HOUR_UTC = 21  # 17:00 ET in UTC (after-hours end)
US_REGULAR_OPEN_HOUR_UTC = 13  # 09:30 ET = 13:30 UTC
US_REGULAR_CLOSE_HOUR_UTC = 20  # 16:00 ET = 20:00 UTC


@dataclass(frozen=True)
class TelegramConfig:
    """Telegram bot settings."""
    bot_token: str = _env("TELEGRAM_BOT_TOKEN")
    chat_id: str = _env("TELEGRAM_CHAT_ID")
    rate_limit_per_minute: int = 20
    # Opt-in: attach a rendered price/EMA/RSI chart to analysis cards.
    # Off by default — requires the optional `charts` extra (mplfinance).
    send_charts: bool = _env_bool("TELEGRAM_SEND_CHARTS", False)
    chart_theme: str = _env("TELEGRAM_CHART_THEME", "dark")  # "dark" | "light"
    # Comma-separated timeframes for setup charts, highest first (e.g. "4h,1h").
    # 2+ are delivered as a Telegram album; a single value sends one photo.
    chart_timeframes: str = _env("TELEGRAM_CHART_TIMEFRAMES", "1h")


@dataclass(frozen=True)
class LLMConfig:
    """LLM provider settings for trade analysis.

    Multi-provider BYOK system supporting 10 providers:
      - OpenAI (default), Anthropic Claude, Google Gemini, Groq,
        Mistral, DeepSeek, Together AI, Ollama, OpenRouter, Custom.

    Set LLM_PROVIDER to select. LLM_BASE_URL auto-resolves from catalog.
    Runtime switching via Telegram /setllm (keys stay in memory only).
    """
    provider: str = _env("LLM_PROVIDER", "openai")
    api_key: str = _env("LLM_API_KEY")
    base_url: str = _env("LLM_BASE_URL")  # auto-resolved from provider if empty
    model: str = _env("LLM_MODEL", "")     # auto-resolved from provider if empty
    temperature: float = 0.3
    max_tokens: int = 1024
    timeout_seconds: float = _env_float("LLM_TIMEOUT_SEC", 15.0)
    daily_call_limit: int = int(_env_float("LLM_DAILY_LIMIT", 500))
    daily_budget_usd: float = _env_float("LLM_DAILY_BUDGET_USD", 1.0)  # fail to rules if exceeded
    est_cost_per_analysis: float = _env_float("LLM_EST_COST_PER_ANALYSIS", 0.003)  # for backtest projection


@dataclass(frozen=True)
class AnalyzerConfig:
    """Tunable parameters for the AI analyzer / confluence engine."""
    llm_weight: float = 0.6
    confluence_weight: float = 0.4
    sma_period: int = 50
    trend_alignment_bonus: float = 0.10
    trend_misalignment_penalty: float = 0.08
    sl_atr_mult_trending: float = 2.5
    tp_atr_mult_trending: float = 3.5   # was 5.0 -- TPs now reachable, >1.4 R:R with 2.5x SL
    sl_atr_mult_default: float = 2.5
    tp_atr_mult_default: float = 3.05   # was 3.0 -- avoids floating-point boundary at min R:R 1.2
    min_candles: int = 30
    # Volatility-adaptive SL/TP overrides (audit C8: externalized from analyzer.py)
    high_vol_threshold: float = 0.03    # ATR/price above this = high volatility
    low_vol_threshold: float = 0.01     # ATR/price below this = low volatility
    high_vol_sl_mult: float = 3.0       # wider stops in high vol
    high_vol_tp_mult: float = 4.5       # R:R = 1.5
    low_vol_sl_mult: float = 2.0        # tighter stops in low vol
    low_vol_tp_mult: float = 3.0        # R:R = 1.5
    # Regime-specific overrides
    range_sl_mult: float = 1.5
    range_tp_mult: float = 2.5
    range_confidence_penalty: float = 0.10
    chop_sl_mult: float = 1.5
    chop_tp_mult: float = 2.0
    chop_confidence_penalty: float = 0.15


@dataclass(frozen=True)
class CacheConfig:
    """LLM semantic cache settings."""
    ttl_seconds: float = _env_float("CACHE_TTL_SECONDS", 300.0)
    max_size: int = int(_env_float("CACHE_MAX_SIZE", 200))


@dataclass(frozen=True)
class ScaleOutConfig:
    """Rule 9: Scale-out ladder for partial profit taking."""
    enabled: bool = _env_bool("SCALE_OUT_ENABLED", True)
    tranche1_pct: float = _env_float("SCALE_OUT_T1_PCT", 50.0)   # close 50% at first target
    tranche1_target_pct: float = _env_float("SCALE_OUT_T1_TARGET", 3.5)  # +3.5% profit
    tranche2_pct: float = _env_float("SCALE_OUT_T2_PCT", 25.0)   # close 25% at second target
    tranche2_target_pct: float = _env_float("SCALE_OUT_T2_TARGET", 7.0)  # +7.0% profit
    runner_pct: float = _env_float("SCALE_OUT_RUNNER_PCT", 25.0)  # 25% runner with ATR trail
    runner_trail_atr_mult: float = _env_float("SCALE_OUT_RUNNER_ATR", 1.0)  # trail at 1x ATR


@dataclass(frozen=True)
class TwoTrancheConfig:
    """Rule 11: Split entries into two tranches."""
    enabled: bool = _env_bool("TWO_TRANCHE_ENABLED", True)
    tranche1_pct: float = _env_float("TRANCHE1_PCT", 60.0)       # 60% first entry
    tranche2_pct: float = _env_float("TRANCHE2_PCT", 40.0)       # 40% on confirmation
    confirmation_bars: int = int(_env_float("TRANCHE2_CONFIRM_BARS", 3))
    retest_tolerance_pct: float = _env_float("TRANCHE2_RETEST_TOL", 0.5)


@dataclass(frozen=True)
class TimeStopConfig:
    """Rules 6/17: Time-based position auto-close."""
    enabled: bool = _env_bool("TIME_STOP_ENABLED", True)
    intraday_warn_hours: float = _env_float("TIME_STOP_INTRA_WARN_H", 2.0)
    intraday_close_hours: float = _env_float("TIME_STOP_INTRA_CLOSE_H", 4.0)
    swing_warn_hours: float = _env_float("TIME_STOP_SWING_WARN_H", 12.0)
    swing_close_hours: float = _env_float("TIME_STOP_SWING_CLOSE_H", 24.0)
    limit_expire_intraday_hours: float = _env_float("LIMIT_EXPIRE_INTRA_H", 4.0)
    limit_expire_swing_hours: float = _env_float("LIMIT_EXPIRE_SWING_H", 48.0)


@dataclass(frozen=True)
class StockTradingConfig:
    """US Stock tokenized trading parameters.

    Stocks have different characteristics than crypto:
    - Lower volatility (ATR typically 1-3% vs crypto's 3-10%)
    - Market-hours liquidity concentration
    - Earnings/macro event sensitivity
    - Correlation to indices (SPY/QQQ)
    """
    enabled: bool = _env_bool("STOCK_TRADING_ENABLED", True)
    # Risk parameters tuned for stock volatility
    volatility_guard_atr_pct: float = _env_float("STOCK_VOL_GUARD_ATR_PCT", 4.0)
    min_risk_reward: float = _env_float("STOCK_MIN_RR", 1.5)
    max_position_pct: float = _env_float("STOCK_MAX_POS_PCT", 3.0)
    max_symbol_exposure_pct: float = _env_float("STOCK_MAX_SYMBOL_EXP_PCT", 15.0)
    # SL/TP multipliers (tighter for stocks)
    sl_atr_mult: float = _env_float("STOCK_SL_ATR_MULT", 2.0)
    tp_atr_mult: float = _env_float("STOCK_TP_ATR_MULT", 3.0)
    # Market hours: reduce size or block outside regular hours
    block_outside_hours: bool = _env_bool("STOCK_BLOCK_OUTSIDE_HOURS", False)
    reduce_size_outside_hours: float = _env_float("STOCK_REDUCE_OFF_HOURS", 0.5)  # 50% size
    # Earnings lockout: hours before/after earnings to avoid
    earnings_lockout_hours: float = _env_float("STOCK_EARNINGS_LOCKOUT_H", 4.0)
    # Max correlated stock positions (e.g., don't hold 5 tech stocks)
    max_sector_positions: int = int(_env_float("STOCK_MAX_SECTOR_POS", 2))


@dataclass(frozen=True)
class AppConfig:
    """Top-level application configuration."""

    # -- Safety switches (fail-closed defaults) --
    simulation_mode: bool = _env_bool("SIMULATION_MODE", True)
    live_trading_enabled: bool = _env_bool("LIVE_TRADING_ENABLED", False)

    # -- Paper trading --
    paper_balance_usd: float = _env_float("PAPER_BALANCE_USD", 10_000.0)
    portfolio_state_file: str = _env("PORTFOLIO_STATE_FILE", "data/portfolio_state.json")

    # -- Scan settings --
    scan_interval_seconds: int = int(_env_float("SCAN_INTERVAL", 60))
    top_movers_count: int = 15

    # -- Sub-configs --
    risk: RiskLimits = field(default_factory=RiskLimits)
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    analyzer: AnalyzerConfig = field(default_factory=AnalyzerConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    scale_out: ScaleOutConfig = field(default_factory=ScaleOutConfig)
    two_tranche: TwoTrancheConfig = field(default_factory=TwoTrancheConfig)
    time_stop: TimeStopConfig = field(default_factory=TimeStopConfig)
    stocks: StockTradingConfig = field(default_factory=StockTradingConfig)

    def is_live(self) -> bool:
        """Live trading requires BOTH flags AND a Telegram chat allow-list.

        Can be activated either via env vars (SIMULATION_MODE=false +
        LIVE_TRADING_ENABLED=true) or at runtime via /golive CONFIRM
        which sets RUNTIME.live_mode = True.
        """
        env_live = self.live_trading_enabled and not self.simulation_mode
        try:
            runtime_live = RUNTIME.live_mode
        except NameError:
            runtime_live = False
        if not (env_live or runtime_live):
            return False
        # F-04 FIX: refuse to arm live mode without a configured chat ID
        try:
            chat_id = self.telegram.chat_id
        except AttributeError:
            chat_id = ""
        if not chat_id:
            import logging
            logging.getLogger(__name__).error(
                "LIVE MODE BLOCKED: TELEGRAM_CHAT_ID is empty. "
                "Set a chat allow-list before enabling live trading."
            )
            return False
        return True


# Singleton used across the application
CONFIG = AppConfig()


# ---------------------------------------------------------------------------
# RuntimeState — mutable runtime state that MUST NOT live on frozen CONFIG
# ---------------------------------------------------------------------------

class RuntimeState:
    """Mutable runtime state, separate from the frozen CONFIG singleton.

    C1 FIX: Previously, ``/mode`` used ``object.__setattr__`` to mutate a
    frozen dataclass field.  That bypasses dataclass invariants and can
    cause subtle bugs.  All mutable runtime values now live here.
    """

    def __init__(self) -> None:
        import threading
        self._lock = threading.Lock()
        self._asset_universe: str = CONFIG.exchange.asset_universe
        self._strategy_mode: str = "balanced"
        self._live_mode: bool = False  # toggled by /golive CONFIRM

    @property
    def live_mode(self) -> bool:
        with self._lock:
            return self._live_mode

    @live_mode.setter
    def live_mode(self, value: bool) -> None:
        with self._lock:
            self._live_mode = value

    @property
    def asset_universe(self) -> str:
        with self._lock:
            return self._asset_universe

    @asset_universe.setter
    def asset_universe(self, value: str) -> None:
        if value not in ("all", "solana", "stocks", "hybrid"):
            raise ValueError(f"Invalid asset universe: {value!r}")
        with self._lock:
            self._asset_universe = value

    @property
    def strategy_mode(self) -> str:
        with self._lock:
            return self._strategy_mode

    @strategy_mode.setter
    def strategy_mode(self, value: str) -> None:
        valid = ("defensive", "balanced", "aggressive", "manual")
        if value not in valid:
            raise ValueError(f"Invalid strategy mode: {value!r}")
        with self._lock:
            self._strategy_mode = value


RUNTIME = RuntimeState()
