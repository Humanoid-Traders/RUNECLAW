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
    # SIGNAL QUALITY: 0.60 is the sweet spot -- enough trades to be meaningful,
    # but still filters weak setups (backtest cliff between 0.60-0.65)
    min_confidence: float = _env_float("MIN_CONFIDENCE", 0.60)
    max_consecutive_losses: int = int(_env_float("MAX_CONSECUTIVE_LOSSES", 5))
    cooldown_after_loss_seconds: int = int(_env_float("COOLDOWN_AFTER_LOSS_SEC", 300))
    max_portfolio_exposure_pct: float = _env_float("MAX_PORTFOLIO_EXPOSURE_PCT", 80.0)
    max_symbol_exposure_pct: float = _env_float("MAX_SYMBOL_EXPOSURE_PCT", 20.0)
    max_correlation_per_group: int = int(_env_float("MAX_CORRELATION_PER_GROUP", 2))
    # Volatility guard: reject trades when ATR exceeds this % of price.
    # BTC hourly ATR is typically 1-4%; 6% allows for elevated-vol periods
    # while blocking extreme conditions. Docs/tests must reference 6%.
    volatility_guard_atr_pct: float = _env_float("VOLATILITY_GUARD_ATR_PCT", 6.0)
    stale_data_max_age_seconds: int = int(_env_float("STALE_DATA_MAX_AGE_SEC", 300))
    require_stop_loss: bool = _env_bool("REQUIRE_STOP_LOSS", True)
    # Portfolio VaR: reject trades that would push parametric VaR above this %.
    max_portfolio_var_pct: float = _env_float("MAX_PORTFOLIO_VAR_PCT", 15.0)
    # Exchange commission per side (taker fee).  0.1% = Bitget taker default.
    commission_pct: float = _env_float("COMMISSION_PCT", 0.1)


@dataclass(frozen=True)
class ExchangeConfig:
    """Bitget API credentials."""
    api_key: str = _env("BITGET_API_KEY")
    api_secret: str = _env("BITGET_API_SECRET")
    passphrase: str = _env("BITGET_PASSPHRASE")
    sandbox: bool = _env_bool("BITGET_SANDBOX", True)  # Sandbox by default; override via env
    # Asset universe filter: "all" scans everything, "solana" adds Solana ecosystem priority
    asset_universe: str = _env("ASSET_UNIVERSE", "all")  # all | solana | custom


# Solana ecosystem tokens tracked on Bitget (centralized pairs).
# Updated 2026-05. Used when ASSET_UNIVERSE=solana to prioritize
# these symbols and add ecosystem-level correlation awareness.
SOLANA_ECOSYSTEM_SYMBOLS: list[str] = [
    "SOL/USDT", "JUP/USDT", "JTO/USDT", "BONK/USDT", "WIF/USDT",
    "PYTH/USDT", "RAY/USDT", "ORCA/USDT", "RENDER/USDT", "HNT/USDT",
    "MOBILE/USDT", "W/USDT", "JITO/USDT", "TENSOR/USDT", "DRIFT/USDT",
]


@dataclass(frozen=True)
class TelegramConfig:
    """Telegram bot settings."""
    bot_token: str = _env("TELEGRAM_BOT_TOKEN")
    chat_id: str = _env("TELEGRAM_CHAT_ID")
    rate_limit_per_minute: int = 20


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
    trend_misalignment_penalty: float = 0.15
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
    top_movers_count: int = 10

    # -- Sub-configs --
    risk: RiskLimits = field(default_factory=RiskLimits)
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    analyzer: AnalyzerConfig = field(default_factory=AnalyzerConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)

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
        self._lock = __import__("threading").Lock()
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
        if value not in ("all", "solana"):
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
