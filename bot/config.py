"""
RUNECLAW Configuration -- AI Trading Command Core
All settings loaded from environment with safe defaults.
Simulation mode is ON by default; live trading requires explicit opt-in.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


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
    max_correlation: float = _env_float("MAX_CORRELATION", 0.85)
    # NEW fields for institutional-grade risk
    min_risk_reward: float = _env_float("MIN_RISK_REWARD", 1.2)
    # SIGNAL QUALITY: 0.60 is the sweet spot -- enough trades to be meaningful,
    # but still filters weak setups (backtest cliff between 0.60-0.65)
    min_confidence: float = _env_float("MIN_CONFIDENCE", 0.60)
    max_consecutive_losses: int = int(_env_float("MAX_CONSECUTIVE_LOSSES", 5))
    cooldown_after_loss_seconds: int = int(_env_float("COOLDOWN_AFTER_LOSS_SEC", 300))
    max_portfolio_exposure_pct: float = _env_float("MAX_PORTFOLIO_EXPOSURE_PCT", 80.0)
    max_symbol_exposure_pct: float = _env_float("MAX_SYMBOL_EXPOSURE_PCT", 20.0)
    max_correlation_per_group: int = int(_env_float("MAX_CORRELATION_PER_GROUP", 2))
    # Crypto-appropriate threshold: BTC hourly ATR is typically 2-5% of price
    volatility_guard_atr_mult: float = _env_float("VOLATILITY_GUARD_ATR_MULT", 6.0)
    stale_data_max_age_seconds: int = int(_env_float("STALE_DATA_MAX_AGE_SEC", 300))
    require_stop_loss: bool = _env_bool("REQUIRE_STOP_LOSS", True)


@dataclass(frozen=True)
class ExchangeConfig:
    """Bitget API credentials."""
    api_key: str = _env("BITGET_API_KEY")
    api_secret: str = _env("BITGET_API_SECRET")
    passphrase: str = _env("BITGET_PASSPHRASE")
    sandbox: bool = True  # Always sandbox unless explicitly overridden


@dataclass(frozen=True)
class TelegramConfig:
    """Telegram bot settings."""
    bot_token: str = _env("TELEGRAM_BOT_TOKEN")
    chat_id: str = _env("TELEGRAM_CHAT_ID")
    rate_limit_per_minute: int = 20


@dataclass(frozen=True)
class LLMConfig:
    """LLM provider settings for trade analysis."""
    api_key: str = _env("LLM_API_KEY")
    model: str = _env("LLM_MODEL", "gpt-4o")
    temperature: float = 0.3
    max_tokens: int = 1024


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
    tp_atr_mult_default: float = 3.0   # was 4.5 -- TPs now reachable, >1.2 R:R with 2.5x SL
    min_candles: int = 30


@dataclass(frozen=True)
class AppConfig:
    """Top-level application configuration."""

    # -- Safety switches (fail-closed defaults) --
    simulation_mode: bool = _env_bool("SIMULATION_MODE", True)
    live_trading_enabled: bool = _env_bool("LIVE_TRADING_ENABLED", False)

    # -- Paper trading --
    paper_balance_usd: float = _env_float("PAPER_BALANCE_USD", 10_000.0)

    # -- Scan settings --
    scan_interval_seconds: int = int(_env_float("SCAN_INTERVAL", 60))
    top_movers_count: int = 10

    # -- Sub-configs --
    risk: RiskLimits = field(default_factory=RiskLimits)
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    analyzer: AnalyzerConfig = field(default_factory=AnalyzerConfig)

    def is_live(self) -> bool:
        """Live trading requires BOTH flags to be set explicitly."""
        return self.live_trading_enabled and not self.simulation_mode


# Singleton used across the application
CONFIG = AppConfig()
