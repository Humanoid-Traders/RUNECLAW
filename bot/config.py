"""
RUNECLAW Configuration -- AI Trading Command Core
All settings loaded from environment with safe defaults.
Simulation mode is ON by default; live trading requires explicit opt-in.

C2-08: CONFIG is a frozen dataclass instantiated at import time from environment
variables. Changes to env vars after import have NO effect without a full process
restart. RuntimeState handles hot-reloadable runtime flags (e.g. kill switch,
simulation override).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

# ── Env precedence (RC-AUD-019) ───────────────────────────────────────────
# We call load_dotenv(override=False), which means a variable already present
# in the PROCESS/OS environment WINS over the value in .env. Precedence is:
#     process/OS env  >  .env file  >  in-code default
# For the safety switches (SIMULATION_MODE / LIVE_TRADING_ENABLED /
# BITGET_SANDBOX) this is a footgun: an inherited SIMULATION_MODE=false or
# LIVE_TRADING_ENABLED=true silently overrides what the operator wrote in .env.
# We do NOT change the precedence (keep override=False for backward compat),
# but we surface a clear WARNING when a safety switch comes from the inherited
# environment. To tell "inherited from the process env" apart from "loaded from
# .env", we must snapshot os.environ BEFORE load_dotenv — afterwards the two
# sources are indistinguishable because load_dotenv injects .env keys into
# os.environ.
_PRE_DOTENV_ENV_KEYS: frozenset[str] = frozenset(os.environ.keys())

load_dotenv(override=False)

# Safety switches whose accidental inheritance from the process environment is
# dangerous enough to warn about at import time.
_SAFETY_SWITCH_KEYS: tuple[str, ...] = (
    "SIMULATION_MODE",
    "LIVE_TRADING_ENABLED",
    "BITGET_SANDBOX",
)


def _detect_inherited_safety_switches(pre_keys: frozenset[str] | set[str]) -> list[str]:
    """Return the safety-switch keys that were present in the process env
    *before* load_dotenv (i.e. inherited, not sourced from .env).

    Pure function (operates only on the passed-in key set) so it is
    deterministic and testable independent of the ambient environment.
    """
    return [k for k in _SAFETY_SWITCH_KEYS if k in pre_keys]


# Keys that were inherited from the process/OS environment and therefore
# override .env for the safety switches (RC-AUD-019).
_INHERITED_SAFETY_SWITCHES: list[str] = _detect_inherited_safety_switches(_PRE_DOTENV_ENV_KEYS)


def _warn_inherited_safety_switches() -> None:
    """RC-AUD-019: warn when a safety switch was inherited from the process
    environment and thus overrides .env (load_dotenv(override=False))."""
    if not _INHERITED_SAFETY_SWITCHES:
        return
    import logging as _logging
    _log = _logging.getLogger(__name__)
    for _key in _INHERITED_SAFETY_SWITCHES:
        _log.warning(
            "Safety switch %s=%r came from the INHERITED process environment and "
            "OVERRIDES any value in .env (precedence: process env > .env because "
            "load_dotenv(override=False)). If this was not intended, unset it in the "
            "process/container environment so the .env value takes effect.",
            _key, os.environ.get(_key, ""),
        )


_warn_inherited_safety_switches()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_bool(key: str, default: bool = False) -> bool:
    raw = _env(key, "").strip().lower()
    if raw in ("", "false", "0", "no"):
        # C2-07 FIX: If the env var is SET but empty AND default is True (safety switch),
        # treat as True to prevent accidental live trading enablement.
        if key not in os.environ:
            return default
        if raw == "" and default is True:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "Safety switch %s is set to empty string — treating as True (safe default). "
                "Set explicitly to 'false' to disable.", key,
            )
            return True
        return False
    if raw in ("true", "1", "yes"):
        return True
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "Unrecognised boolean env var %s=%r — using default %s", key, raw, default,
    )
    return default


def _env_float(key: str, default: float = 0.0) -> float:
    try:
        val = float(_env(key, str(default)))
    except ValueError:
        return default
    # Reject inf/nan: float() parses them without error, but a non-finite risk
    # limit silently disables guards (every `x > nan` / `x < nan` is False), so
    # fail back to the safe default instead.
    import math as _math
    if not _math.isfinite(val):
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "Env var %s=%r is not finite — using default %r", key, val, default,
        )
        return default
    return val


def _env_float_bounded(key: str, default: float, min_val: float, max_val: float) -> float:
    """Read an env-var float and clamp it to [min_val, max_val]."""
    val = _env_float(key, default)
    if val < min_val or val > max_val:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "Env var %s=%.4g is outside [%.4g, %.4g] — clamping", key, val, min_val, max_val,
        )
        val = max(min_val, min(max_val, val))
    return val


@dataclass(frozen=True)
class RiskLimits:
    """Hard risk limits -- breaching any one triggers circuit breaker."""
    max_position_pct: float = _env_float_bounded("MAX_POSITION_PCT", 13.0, 1, 100)
    max_daily_loss_pct: float = _env_float_bounded("MAX_DAILY_LOSS_PCT", 5.0, 0.1, 50)
    # CFG-2: clamp risk-gate limits so an operator typo or a negative value
    # (which would invert the `>`/`<` comparisons and silently disable the guard)
    # cannot load. Bounds are generous enough to never reject a legitimate value.
    max_drawdown_pct: float = _env_float_bounded("MAX_DRAWDOWN_PCT", 10.0, 0.1, 100.0)
    max_open_positions: int = int(_env_float_bounded("MAX_OPEN_POSITIONS", 5, 1, 100))
    # Note: max_correlation coefficient is reserved for a future pairwise correlation
    # matrix check. Currently, concentration is enforced by max_correlation_per_group
    # (a group-count limit), not by this coefficient value.
    max_correlation: float = _env_float("MAX_CORRELATION", 0.85)
    # Extended risk checks (checks 6-16)
    min_risk_reward: float = _env_float_bounded("MIN_RISK_REWARD", 1.2, 0.0, 100.0)
    # SIGNAL QUALITY: 0.55 is the tuned threshold -- relaxed from 0.60 to allow
    # more signals through while still filtering weak setups
    min_confidence: float = _env_float_bounded("MIN_CONFIDENCE", 0.55, 0.1, 1.0)
    # Minimum confidence shown in "Latest Signal" display (filters UI noise)
    signal_display_min_confidence: float = _env_float_bounded(
        "SIGNAL_DISPLAY_MIN_CONFIDENCE", 0.70, 0.1, 1.0)
    max_consecutive_losses: int = int(_env_float_bounded("MAX_CONSECUTIVE_LOSSES", 5, 1, 50))
    cooldown_after_loss_seconds: int = int(_env_float("COOLDOWN_AFTER_LOSS_SEC", 120))
    max_portfolio_exposure_pct: float = _env_float_bounded("MAX_PORTFOLIO_EXPOSURE_PCT", 80.0, 0.0, 1000.0)
    max_symbol_exposure_pct: float = _env_float_bounded("MAX_SYMBOL_EXPOSURE_PCT", 20.0, 0.0, 1000.0)
    max_correlation_per_group: int = int(_env_float("MAX_CORRELATION_PER_GROUP", 2))
    # Symbols not in the known correlation map were each treated as their OWN
    # group, so a basket of unmapped alts could collectively dodge the per-group
    # cap (the live report's many-correlated-alts exposure). They are now pooled
    # into ONE shared "unmapped alt" bucket with its own, more generous cap
    # (unmapped symbols aren't all mutually correlated). Set high to disable.
    max_unmapped_correlated: int = int(_env_float_bounded("MAX_UNMAPPED_CORRELATED", 3, 1, 100))
    # Volatility guard: reject trades when ATR exceeds this % of price.
    # BTC hourly ATR is typically 1-4%; 7% allows for elevated-vol periods
    # while blocking extreme conditions.
    volatility_guard_atr_pct: float = _env_float_bounded("VOLATILITY_GUARD_ATR_PCT", 7.0, 0.1, 100.0)
    # Reject trade ideas whose market data is older than this. Restored to the
    # conservative 5 minutes (300s): it had been doubled to 600s in an unrelated
    # commit (226858e) with no rationale, and acting on up-to-10-minute-old data
    # is the wrong direction for a risk-first bot. Bounded so a typo/negative
    # can't disable the staleness guard.
    stale_data_max_age_seconds: int = int(_env_float_bounded("STALE_DATA_MAX_AGE_SEC", 300, 1, 86400))
    require_stop_loss: bool = _env_bool("REQUIRE_STOP_LOSS", True)
    # Portfolio VaR: reject trades that would push parametric VaR above this %.
    max_portfolio_var_pct: float = _env_float_bounded("MAX_PORTFOLIO_VAR_PCT", 15.0, 0.1, 100.0)
    # Macro calendar staleness fail-safe (default ON = safe). The macro event
    # schedule is hardcoded; once every event is in the past it is EXHAUSTED and
    # there is no future event to gate against. With this ON, an exhausted
    # calendar fails closed (BLACKOUT → new entries blocked) and the monitor
    # alerts, instead of silently reporting NORMAL with all event protection
    # gone. An operator who knowingly accepts the gap can set this False; the
    # staleness alert still fires. No effect while future events remain.
    macro_calendar_fail_closed_when_stale: bool = _env_bool(
        "MACRO_CALENDAR_FAIL_CLOSED_WHEN_STALE", True)
    # Covariance-based portfolio VaR (roadmap H-05). Default OFF: the live guard
    # keeps using the per-trade-return proxy until an operator opts in. When ON
    # AND every held + proposed asset has at least var_covariance_min_points of
    # aligned price history, VaR is computed from a real covariance matrix across
    # held assets (signed by position direction, so an opposing hedge correctly
    # lowers portfolio variance). If the data is insufficient it falls back to the
    # per-trade VaR — it never silently downgrades the check to a skip.
    var_covariance_enabled: bool = _env_bool("VAR_COVARIANCE_ENABLED", False)
    var_covariance_min_points: int = int(
        _env_float_bounded("VAR_COVARIANCE_MIN_POINTS", 20, 5, 1000))
    # Exchange commission per side.
    # Bitget USDT perps: taker ~0.060%, maker ~0.020% (standard tier).
    # commission_pct is the DEFAULT rate used in risk calcs (taker).
    commission_pct: float = _env_float("COMMISSION_PCT", 0.06)
    # Split rates for accurate PnL when order type is known.
    taker_fee_pct: float = _env_float("TAKER_FEE_PCT", 0.06)
    maker_fee_pct: float = _env_float("MAKER_FEE_PCT", 0.02)
    # Liquidity guard: minimum order-book depth (per side) in USD.
    # Scaled dynamically by position size; this is the absolute floor.
    # Default $2K allows micro-test trades ($10-$50) to pass on smaller pairs.
    min_book_depth_usd: float = _env_float("MIN_BOOK_DEPTH_USD", 2_000.0)
    # Leverage-aware margin risk cap: max % of margin (cost) that can be lost
    # on a single trade.  SL distance × leverage must not exceed this.
    # With 10x leverage, 30% means SL can be at most 3% from entry.
    max_margin_risk_pct: float = _env_float_bounded("MAX_MARGIN_RISK_PCT", 30.0, 0.1, 1000.0)
    # Equity curve circuit breaker: if equity drops below its N-period MA, halve sizes
    equity_curve_ma_period: int = int(_env_float("EQUITY_CURVE_MA_PERIOD", 20))
    equity_curve_pause_stddev: float = _env_float("EQUITY_CURVE_PAUSE_STDDEV", 2.0)
    # Drawdown recovery mode: after hitting max DD, enter conservative mode
    drawdown_recovery_conf_min: float = _env_float("DRAWDOWN_RECOVERY_CONF_MIN", 0.85)
    drawdown_recovery_size_mult: float = _env_float("DRAWDOWN_RECOVERY_SIZE_MULT", 0.5)
    # Kelly-criterion sizing (opt-in, default OFF). When enabled, evaluate() also
    # derives a half-Kelly size from realized trade history and takes the SMALLER
    # of {fixed-fractional, Kelly}: Kelly can only TIGHTEN size, never grow it, and
    # the notional/margin caps below stay authoritative. Below kelly_min_trades
    # closed trades there is no edge estimate, so it is a no-op (size unchanged).
    kelly_sizing_enabled: bool = _env_bool("KELLY_SIZING_ENABLED", False)
    kelly_min_trades: int = int(_env_float_bounded("KELLY_MIN_TRADES", 20, 1, 100000))
    # Portfolio-aware correlation sizing (opt-in, default OFF). The existing
    # correlation check (_check_correlation) is a count-cap CONCENTRATION GATE:
    # it rejects once a group is full but does nothing for the trades it lets
    # through. This adds a graduated size REDUCTION for a new trade that shares a
    # correlation group AND direction with already-open positions, so the second
    # and third correlated bet are smaller (the marginal portfolio risk they add
    # is larger). It can only SHRINK size (multiplier in [floor, 1.0]); the
    # notional/margin caps and every gate below stay authoritative. Default OFF
    # makes this byte-identical to prior behaviour.
    correlation_sizing_enabled: bool = _env_bool("CORRELATION_SIZING_ENABLED", False)
    # Reduction per same-group same-direction open position (0.20 → −20% each).
    correlation_sizing_step: float = _env_float_bounded("CORRELATION_SIZING_STEP", 0.20, 0.0, 1.0)
    # Floor on the multiplier — size is never reduced below this fraction.
    correlation_sizing_floor: float = _env_float_bounded("CORRELATION_SIZING_FLOOR", 0.5, 0.1, 1.0)
    # Live risk hardening (opt-in, default OFF). When ON *and* running live, it
    # applies a stricter portfolio-risk posture for real money WITHOUT touching
    # paper/backtest behaviour:
    #   - forces correlation-aware position sizing on (even if its own flag is off),
    #   - forces covariance-based portfolio VaR on (falls back to the per-trade
    #     proxy whenever data is insufficient — never a downgrade to skip),
    #   - caps drawdown at live_max_drawdown_pct (tighter than the paper limit).
    # Default OFF → byte-identical until enabled; in paper mode it never applies.
    live_risk_hardening_enabled: bool = _env_bool("LIVE_RISK_HARDENING_ENABLED", False)
    live_max_drawdown_pct: float = _env_float_bounded("LIVE_MAX_DRAWDOWN_PCT", 7.0, 0.1, 100.0)
    # Live-performance governor (opt-in, default OFF). A closed-loop backstop ON
    # TOP of the pre-trade checks: it scores REALIZED closed-trade outcomes over a
    # rolling window and de-risks when the strategy is actually losing — a graduated
    # SIZE REDUCTION when the recent window underperforms (low win rate OR net
    # negative), and a PAUSE (size 0, trade rejected) only when it is BOTH losing
    # often AND net-negative. It can only tighten (reduce/pause), never grow size or
    # loosen a gate, and is a no-op below live_perf_min_samples closed trades (fails
    # OPEN = normal sizing). Distinct from the equity-curve breaker (equity vs MA)
    # and the consecutive-loss breaker (streak): this reads realized win rate + net
    # PnL of the most recent trades. Default OFF → byte-identical until enabled.
    # Regime-aware position sizing (opt-in, default OFF). The analyzer already
    # classifies a per-symbol market regime (TREND_UP/TREND_DOWN/EXPANSION/RANGE/
    # CHOP), but it was never bridged into the risk engine, so _current_regime
    # stayed "UNKNOWN" and the per-regime size multipliers were always 1.0×. When
    # ON, the engine sets the risk engine's regime from the analyzer before each
    # evaluate(), so get_regime_adjusted_params applies the per-regime multiplier
    # (e.g. CHOP 0.5× / RANGE 0.7× reduce, TREND 1.2× / EXPANSION 1.3× increase).
    # The notional/margin cap stays the final authority, so increases can never
    # exceed it. Default OFF → regime stays UNKNOWN → byte-identical (1.0×).
    regime_sizing_enabled: bool = _env_bool("REGIME_SIZING_ENABLED", False)
    live_performance_governor_enabled: bool = _env_bool("LIVE_PERFORMANCE_GOVERNOR_ENABLED", False)
    # Rolling window of most-recent CLOSED trades the governor scores.
    live_perf_window: int = int(_env_float_bounded("LIVE_PERF_WINDOW", 20, 2, 100000))
    # Minimum closed trades before the governor acts (below this → full size).
    live_perf_min_samples: int = int(_env_float_bounded("LIVE_PERF_MIN_SAMPLES", 10, 1, 100000))
    # Win rate at/below which the window counts as underperforming → reduce.
    live_perf_reduce_winrate: float = _env_float_bounded("LIVE_PERF_REDUCE_WINRATE", 0.40, 0.0, 1.0)
    # Win rate at/below which (AND net-negative) the governor pauses trading.
    live_perf_pause_winrate: float = _env_float_bounded("LIVE_PERF_PAUSE_WINRATE", 0.25, 0.0, 1.0)
    # Size multiplier applied while in the reduce zone.
    live_perf_reduce_mult: float = _env_float_bounded("LIVE_PERF_REDUCE_MULT", 0.5, 0.05, 1.0)


@dataclass(frozen=True)
class ExchangeConfig:
    """Bitget API credentials and trading mode."""
    api_key: str = _env("BITGET_API_KEY")
    api_secret: str = _env("BITGET_API_SECRET")
    passphrase: str = _env("BITGET_PASSPHRASE")
    sandbox: bool = _env_bool("BITGET_SANDBOX", True)  # Sandbox by default; override via env
    # Asset universe filter: "all" scans everything, "solana" adds Solana ecosystem priority
    asset_universe: str = _env("ASSET_UNIVERSE", "all_markets")  # all_markets | all | solana | metals | tradfi | etc.
    # Trading mode: "spot" for no leverage, "futures" for USDT-M perpetual
    trade_mode: str = _env("TRADE_MODE", "futures")
    # Default leverage (1x = no leverage, 5x = default for futures)
    default_leverage: int = int(_env_float_bounded("DEFAULT_LEVERAGE", 5, 1, 125))
    # Dynamic leverage scaling
    dynamic_leverage_enabled: bool = _env_bool("DYNAMIC_LEVERAGE_ENABLED", True)
    min_leverage: int = int(_env_float_bounded("MIN_LEVERAGE", 2, 1, 125))
    max_leverage: int = int(_env_float_bounded("MAX_LEVERAGE", 10, 1, 125))
    # Margin mode: "isolated" mandatory (GetClaw rule: prevents runaway losses on gap-risk assets)
    margin_mode: str = _env("MARGIN_MODE", "isolated")
    # C2-57: Configurable hold mode probe symbol (used for account mode detection)
    hold_mode_probe_symbol: str = _env("HOLD_MODE_PROBE_SYMBOL", "BTCUSDT")


# C2-61: Warn if leverage is dangerously high
_leverage_val = _env_float_bounded("DEFAULT_LEVERAGE", 5, 1, 125)
if _leverage_val > 20:
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "DEFAULT_LEVERAGE=%.0f× is above 20× — high leverage dramatically increases "
        "liquidation risk. Confirm this is intentional.", _leverage_val,
    )



# ── Priority Trading Symbols ────────────────────────────────────
# These symbols ALWAYS get included in the scan output regardless
# of momentum ranking. Ensures core trading universe is never
# filtered out by the top-movers limit.
# Format: spot "BTC/USDT" (scanner normalizes to futures automatically)
PRIORITY_SYMBOLS: list[str] = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "LAB/USDT", "ZEC/USDT",
    "XRP/USDT", "DOGE/USDT", "TAO/USDT", "HYPE/USDT", "JTO/USDT",
    "BNB/USDT", "SUI/USDT", "FIL/USDT", "ADA/USDT", "LINK/USDT",
    "ENA/USDT", "ONDO/USDT", "SKYAI/USDT", "BCH/USDT", "AVAX/USDT",
    "PENGU/USDT", "SIREN/USDT", "DOT/USDT", "ICP/USDT", "NEAR/USDT",
    "LTC/USDT", "VIRTUAL/USDT", "PUMP/USDT", "DYDX/USDT", "WLFI/USDT",
    "FARTCOIN/USDT", "AAVE/USDT", "UNI/USDT", "OP/USDT", "ASTER/USDT",
    "DASH/USDT", "WLD/USDT", "WIF/USDT", "ORDI/USDT", "ARB/USDT",
    "TRX/USDT", "XPL/USDT", "RAVE/USDT", "XLM/USDT", "ETC/USDT",
    "TRUMP/USDT", "APT/USDT", "HBAR/USDT", "1000BONK/USDT", "VVV/USDT",
    "BIO/USDT", "M/USDT", "CHIP/USDT", "PENDLE/USDT", "XMR/USDT",
    "ALGO/USDT", "CRV/USDT", "TIA/USDT", "RENDER/USDT", "INJ/USDT",
    "JUP/USDT", "FET/USDT", "APE/USDT", "SEI/USDT", "ATOM/USDT",
    "LDO/USDT",
]

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


# TradFi Metal Perpetual Contracts on Bitget.
# These track commodity spot prices via USDT-M futures.
# Gold, Silver, Platinum, Palladium, Copper — tradeable 24/7.
# Playbook: GetAgent Metal Perpetuals strategy.
METAL_PERPETUALS: list[str] = [
    "XAU/USDT:USDT",     # Gold (XAU/USD) — Bitget USDT-M perpetual
    "XAG/USDT:USDT",     # Silver (XAG/USD)
    "PAXG/USDT:USDT",    # PAX Gold (tokenized gold)
    "XPT/USDT:USDT",     # Platinum (XPT/USD)
    "COPPER/USDT:USDT",  # Copper
    "XPD/USDT:USDT",     # Palladium (XPD/USD)
]

# Metal classification for risk / correlation awareness
METAL_SECTORS: dict[str, str] = {
    "XAU/USDT:USDT": "Precious",
    "XAG/USDT:USDT": "Precious",
    "PAXG/USDT:USDT": "Precious",
    "XPT/USDT:USDT": "Precious",
    "COPPER/USDT:USDT": "Industrial",
    "XPD/USDT:USDT": "Precious",
}

# ── Commodity Perpetuals ─────────────────────────────────────────
# Energy futures on Bitget — USDT-M perpetual contracts.
COMMODITY_PERPETUALS: list[str] = [
    "CL/USDT:USDT",       # WTI Crude Oil
    "BZ/USDT:USDT",       # Brent Crude Oil
    "NATGAS/USDT:USDT",   # Natural Gas
]

# ── Pre-IPO Stock Perpetuals ─────────────────────────────────────
# Pre-IPO tech company tokens on Bitget — USDT-M perpetual contracts.
PRE_IPO_PERPETUALS: list[str] = [
    "OPENAI/USDT:USDT",      # OpenAI (preOPAI)
    "ANTHROPIC/USDT:USDT",   # Anthropic
]

# ── ETF Perpetuals ───────────────────────────────────────────────
# Exchange-Traded Fund perpetual contracts on Bitget — USDT-M.
ETF_PERPETUALS: list[str] = [
    "XLK/USDT:USDT",     # Technology Select Sector SPDR ETF
    "DFEN/USDT:USDT",    # Direxion Aero & Def Bull 3X ETF
    "KWEB/USDT:USDT",    # KraneShares CSI China Internet ETF
    "SGOV/USDT:USDT",    # iShares 0-3M Treasury ETF
    "EWH/USDT:USDT",     # iShares MSCI Hong Kong ETF
    "INDA/USDT:USDT",    # iShares MSCI India ETF
]

# ── Stock Perpetual Contracts ────────────────────────────────────
# US equity USDT-M perpetual futures on Bitget (separate from spot tokenized).
# These are actual futures contracts, tradeable 24/7 with leverage.
STOCK_PERPETUALS: list[str] = [
    "TSLA/USDT:USDT",    # Tesla
    "AAPL/USDT:USDT",    # Apple
    "MSFT/USDT:USDT",    # Microsoft
    "GOOGL/USDT:USDT",   # Alphabet
    "AMZN/USDT:USDT",    # Amazon
    "META/USDT:USDT",    # Meta Platforms
    "NVDA/USDT:USDT",    # NVIDIA
    "AMD/USDT:USDT",     # AMD
    "COIN/USDT:USDT",    # Coinbase
    "MSTR/USDT:USDT",    # MicroStrategy
    "HOOD/USDT:USDT",    # Robinhood
    "PLTR/USDT:USDT",    # Palantir
    "ARM/USDT:USDT",     # ARM Holdings
    "MRVL/USDT:USDT",    # Marvell
    "INTC/USDT:USDT",    # Intel
]

# ── Combined TradFi Universe ────────────────────────────────────
# All non-crypto USDT-M perpetuals: metals + commodities + ETFs + pre-IPO + stocks
TRADFI_PERPETUALS: list[str] = (
    METAL_PERPETUALS + COMMODITY_PERPETUALS + PRE_IPO_PERPETUALS
    + ETF_PERPETUALS + STOCK_PERPETUALS
)

# US stock market hours — DST-aware via zoneinfo (C2-06 FIX)
# Previously hardcoded to EDT (UTC-4), off by 1 hour Nov–Mar during EST (UTC-5).
try:
    from zoneinfo import ZoneInfo as _ZoneInfo
    from datetime import datetime as _dt, timezone as _tz

    _NY = _ZoneInfo("America/New_York")

    def _us_market_hour_utc(et_hour: int, et_minute: int = 0) -> int:
        """Convert an Eastern Time hour to current-day UTC hour, DST-aware."""
        now_ny = _dt.now(_NY)
        local_time = now_ny.replace(hour=et_hour, minute=et_minute, second=0, microsecond=0)
        return local_time.astimezone(_tz.utc).hour

    def us_market_open_hour_utc() -> int:
        return _us_market_hour_utc(9, 0)

    def us_market_close_hour_utc() -> int:
        return _us_market_hour_utc(17, 0)

    def us_regular_open_hour_utc() -> int:
        return _us_market_hour_utc(9, 30)

    def us_regular_close_hour_utc() -> int:
        return _us_market_hour_utc(16, 0)

except Exception:
    # Fallback: compute from month-based DST approximation
    # EDT (UTC-4) Apr–Oct, EST (UTC-5) Nov–Mar
    def _fallback_offset() -> int:
        from datetime import datetime as _fdt
        month = _fdt.now().month
        return 4 if 3 < month < 11 else 5

    def us_market_open_hour_utc() -> int:
        return 9 + _fallback_offset()

    def us_market_close_hour_utc() -> int:
        return 17 + _fallback_offset()

    def us_regular_open_hour_utc() -> int:
        return 9 + _fallback_offset()

    def us_regular_close_hour_utc() -> int:
        return 16 + _fallback_offset()


@dataclass(frozen=True)
class TelegramConfig:
    """Telegram bot settings."""
    bot_token: str = _env("TELEGRAM_BOT_TOKEN")
    chat_id: str = _env("TELEGRAM_CHAT_ID")
    admin_ids: str = _env("ADMIN_TELEGRAM_IDS", "")  # Comma-separated admin user IDs — get premium LLM routing
    # Comma-separated Telegram IDs allowed to trade LIVE on their OWN linked
    # account WITHOUT operator/admin identity. Members are permitted onto the
    # bot + live-trade allowlist, but are NOT operators (no admin commands, no
    # operator-account routing) — so per-user risk isolation, own-account
    # execution, and own-equity sizing apply to them. Use this (not
    # ADMIN_TELEGRAM_IDS) to onboard regular live users under PER_USER_LIVE_ENABLED.
    live_trader_ids: str = _env("LIVE_TRADER_TELEGRAM_IDS", "")
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
    temperature: float = _env_float("LLM_TEMPERATURE", 0.3)
    max_tokens: int = int(_env_float("LLM_MAX_TOKENS", 1024))
    timeout_seconds: float = _env_float("LLM_TIMEOUT_SEC", 15.0)
    daily_call_limit: int = int(_env_float("LLM_DAILY_LIMIT", 500))
    daily_budget_usd: float = _env_float("LLM_DAILY_BUDGET_USD", 1.0)  # fail to rules if exceeded
    est_cost_per_analysis: float = _env_float("LLM_EST_COST_PER_ANALYSIS", 0.003)  # for backtest projection


@dataclass(frozen=True)
class AnalyzerConfig:
    """Tunable parameters for the AI analyzer / confluence engine."""
    # Confidence-blend weights: blended = llm_conf*llm_weight + confluence*confluence_weight.
    # Now env-configurable (LLM Optimization Plan Phase 5) so the split can be
    # tuned from evidence without a code change. Defaults preserve the prior
    # hardcoded 0.6 / 0.4.
    llm_weight: float = _env_float_bounded("LLM_BLEND_WEIGHT", 0.6, 0.0, 1.0)
    confluence_weight: float = _env_float_bounded("CONFLUENCE_BLEND_WEIGHT", 0.4, 0.0, 1.0)
    # Uncalibrated-LLM weight cap (opt-in, default OFF). The LLM drives `llm_weight`
    # (0.6) of the blended confidence, but until confidence calibration is ON its
    # confidence is unproven against realized outcomes — a hallucinated or
    # overconfident thesis flows straight into sizing. When this is ON *and*
    # calibration is OFF, the LLM's weight is capped at
    # `uncalibrated_llm_weight_cap` and the freed weight is shifted to the
    # deterministic, auditable confluence score (so the weights still sum to the
    # same total). Once calibration is enabled the cap lifts automatically.
    # Default OFF makes the blend byte-identical to today.
    uncalibrated_llm_weight_cap_enabled: bool = _env_bool("UNCALIBRATED_LLM_WEIGHT_CAP_ENABLED", False)
    uncalibrated_llm_weight_cap: float = _env_float_bounded("UNCALIBRATED_LLM_WEIGHT_CAP", 0.4, 0.0, 1.0)
    # Per-user BYOK LLM routing (opt-in, default OFF). When ON, the LLM thesis for
    # a command a user runs by hand (/analyze) uses THAT user's own provider key
    # (from their encrypted settings) instead of the operator's — so their
    # analysis draws on their own quota/billing. Fail-open: if the user has no key
    # / an invalid provider / the client can't build, it silently falls back to
    # the operator config. The continuous background scan is unaffected (analysis
    # there is shared/operator-funded). Default OFF → byte-identical until enabled.
    per_user_llm_enabled: bool = _env_bool("PER_USER_LLM_ENABLED", False)
    # Tier-based operator LLM routing (opt-in, default OFF; LLM Optimization Plan
    # P2). When ON, a user's TIER (admin / elite / pro from the user store) maps
    # to operator-funded LLM quality for their hand-run /analyze: elite → Sonnet
    # thesis, pro → Gemini thesis, admin → Sonnet everywhere. basic / free / unknown
    # tiers keep the existing default routing (never downgraded). A user's OWN key
    # (per_user_llm_enabled / BYOK) takes precedence over their tier. Fail-open to
    # default routing. Default OFF → byte-identical until enabled.
    per_user_llm_tiers_enabled: bool = _env_bool("PER_USER_LLM_TIERS_ENABLED", False)
    # Scoped semantic-LLM-cache key (opt-in, default OFF; deep-audit medium). The
    # semantic cache keys on bucketed market conditions only, NOT on which model
    # answers — but the answering model depends on the pipeline tier (rule vs
    # scan vs thesis), the admin/basic boundary, a user's BYOK key, and their
    # premium tier. With a single namespace, an admin/premium/BYOK thesis (or a
    # tier-1 rule result) is served to any user with the same indicator buckets,
    # and vice-versa. When ON, the cache key is additionally salted with that
    # routing identity so responses never cross those boundaries. Making the key
    # MORE specific is strictly safe-direction (it can only avoid a wrong reuse,
    # never create one); it costs some cache sharing. RECOMMENDED ON whenever
    # per_user_llm_enabled / per_user_llm_tiers_enabled is ON. Default OFF →
    # cache key byte-identical to the legacy single-namespace behaviour.
    llm_cache_scoped_key: bool = _env_bool("LLM_CACHE_SCOPED_KEY", False)
    # Confidence calibration (Phase A): when ON, the final blended confidence is
    # remapped through a monotonic reliability curve fitted from the bot's own
    # closed-trade history, so a confidence value reflects realized win rate.
    # Default OFF — the curve is computed in shadow-mode (logged, not applied)
    # until deliberately enabled. See bot/learning/confidence_calibration.py.
    confidence_calibration_enabled: bool = _env_bool("CONFIDENCE_CALIBRATION_ENABLED", False)
    # Per-setup expectancy (Phase C): when ON, a setup's own historical win rate
    # (symbol + regime + direction, from completed trades) applies a small bounded
    # nudge to confidence. Default OFF — computed in shadow-mode (logged, not
    # applied) until enabled. See bot/learning/setup_expectancy.py.
    setup_expectancy_enabled: bool = _env_bool("SETUP_EXPECTANCY_ENABLED", False)
    # Voter-weight learning application (Phase B2): when ON, each confluence
    # voter's hand-tuned weight is multiplied by a learned, bounded ([0.5,1.5])
    # multiplier reflecting how well that voter has predicted winning trades.
    # Default OFF — until enabled, weights are byte-identical to hand-tuned.
    # See bot/learning/voter_weights.py and docs/VOTER_WEIGHT_LEARNING.md.
    voter_weight_learning_enabled: bool = _env_bool("VOTER_WEIGHT_LEARNING_ENABLED", False)
    # External sentiment: when ON, the sentiment voter blends the live market-wide
    # Fear & Greed index (alternative.me) as a bounded contrarian signal. Default
    # OFF — until enabled the voter is purely price-derived (no external network
    # call). See bot/core/sentiment.py.
    external_sentiment_enabled: bool = _env_bool("EXTERNAL_SENTIMENT_ENABLED", False)
    # Funding carry-cost awareness: when ON, apply a small bounded confidence
    # haircut when a trade would PAY adverse funding over its expected hold (the
    # carry-cost dimension the instantaneous funding signals miss). Default OFF —
    # only ever reduces confidence. See bot/core/funding.py.
    funding_cost_aware_enabled: bool = _env_bool("FUNDING_COST_AWARE_ENABLED", False)
    # Learning auto-refit: when ON, the three learners (calibration, voter weights,
    # setup expectancy) are re-fitted from closed-trade history every
    # LEARNING_AUTO_REFIT_INTERVAL closed trades, so they don't go stale. Refitting
    # only updates persisted learner state — it never changes a decision unless the
    # learners' own application flags are on. Default OFF. See bot/learning/auto_refit.py.
    learning_auto_refit_enabled: bool = _env_bool("LEARNING_AUTO_REFIT_ENABLED", False)
    learning_auto_refit_interval: int = int(_env_float("LEARNING_AUTO_REFIT_INTERVAL", 25))
    # Drop the in-progress (unclosed) candle before computing indicators/patterns.
    # Live OHLCV from the exchange includes the current forming bar as the last
    # element; reading closes[-1] on it makes every voter flicker pre-close
    # (repaint). When ON, the still-forming last candle is dropped before analysis
    # so all TA uses CLOSED bars only — aligning live with the (bar-closed)
    # backtest. Entry/price logic is unaffected (it uses the live ticker price,
    # not the last candle). Default OFF → byte-identical until enabled.
    drop_unclosed_candle_enabled: bool = _env_bool("DROP_UNCLOSED_CANDLE_ENABLED", False)
    sma_period: int = 50
    trend_alignment_bonus: float = 0.10
    trend_misalignment_penalty: float = 0.08
    sl_atr_mult_trending: float = 2.5
    tp_atr_mult_trending: float = 3.0   # was 3.5 -- tightened: only 1/35 trades hit TP at 3.5x
    sl_atr_mult_default: float = 2.5
    tp_atr_mult_default: float = 2.8   # was 3.05 -- tightened to capture more wins
    min_candles: int = 30
    # Volatility-adaptive SL/TP overrides (audit C8: externalized from analyzer.py)
    high_vol_threshold: float = 0.03    # ATR/price above this = high volatility
    low_vol_threshold: float = 0.01     # ATR/price below this = low volatility
    high_vol_sl_mult: float = 3.0       # wider stops in high vol
    high_vol_tp_mult: float = 3.8       # was 4.5 -- tightened for reachable TPs
    low_vol_sl_mult: float = 2.0        # tighter stops in low vol
    low_vol_tp_mult: float = 3.0        # R:R = 1.5
    # Regime-specific overrides
    range_sl_mult: float = 1.5
    range_tp_mult: float = 2.5
    range_confidence_penalty: float = 0.10
    chop_sl_mult: float = 1.5
    chop_tp_mult: float = 2.0
    chop_confidence_penalty: float = 0.15
    # Regime HARD gates (opt-in, default OFF). The penalties above only SOFTEN
    # the lowest-edge regimes; with this ON they become hard no-trades:
    #   - CHOP / UNKNOWN regime  -> skip the signal entirely.
    #   - Counter-trend entry in a STRONG trend (ADX >= regime_strong_adx)
    #     (SHORT in TREND_UP / LONG in TREND_DOWN) -> skip entirely.
    # Default OFF preserves the current soft-penalty behaviour byte-for-byte.
    regime_hard_gates_enabled: bool = _env_bool("REGIME_HARD_GATES_ENABLED", False)
    regime_strong_adx: float = _env_float_bounded("REGIME_STRONG_ADX", 30.0, 20.0, 60.0)
    # RSI hard block: reject LONG when RSI >= this, reject SHORT when RSI <= inverse
    rsi_overbought_block: float = _env_float("RSI_OVERBOUGHT_BLOCK", 72.0)
    rsi_oversold_block: float = _env_float("RSI_OVERSOLD_BLOCK", 28.0)
    # Divergence scanner: lookback periods
    divergence_lookback: int = int(_env_float("DIVERGENCE_LOOKBACK", 50))
    divergence_min_swings: int = int(_env_float("DIVERGENCE_MIN_SWINGS", 2))
    # Volume profile: lookback for POC/VAH/VAL
    volume_profile_lookback: int = int(_env_float("VOLUME_PROFILE_LOOKBACK", 100))
    volume_profile_bins: int = int(_env_float("VOLUME_PROFILE_BINS", 50))


@dataclass(frozen=True)
class LearningConfig:
    """Closed-loop learning adjustments.

    The orchestrator already LOGS every decision + outcome; this controls whether
    that accumulated experience is read back to nudge new-trade confidence.
    Default OFF: it changes live entry behavior, so it is opt-in. The nudge is
    small, capped, asymmetric (penalize historically-losing setups more than it
    rewards winners), additive only, and never overrides the 23 risk checks.
    """
    adaptive_confidence_enabled: bool = _env_bool("ADAPTIVE_CONFIDENCE_ENABLED", False)
    # Require at least this many similar (same symbol+direction+regime) closed
    # setups before any adjustment — avoids reacting to noise.
    adaptive_confidence_min_samples: int = int(
        _env_float_bounded("ADAPTIVE_CONFIDENCE_MIN_SAMPLES", 5, 1, 1000))
    # Max downward nudge for a historically-losing setup (penalty).
    adaptive_confidence_max_penalty: float = _env_float_bounded(
        "ADAPTIVE_CONFIDENCE_MAX_PENALTY", 0.05, 0.0, 0.5)
    # Max upward nudge for a historically-winning setup (smaller — risk-first
    # asymmetry: we trust losses to teach more than wins).
    adaptive_confidence_max_boost: float = _env_float_bounded(
        "ADAPTIVE_CONFIDENCE_MAX_BOOST", 0.02, 0.0, 0.5)


@dataclass(frozen=True)
class PartialTPConfig:
    """Partial take-profit ladder configuration."""
    enabled: bool = _env_bool("PARTIAL_TP_ENABLED", True)
    # TP1: close 50% at 1.5R, move SL to breakeven
    tp1_r_multiple: float = _env_float("PARTIAL_TP1_R", 1.5)
    tp1_close_pct: float = _env_float("PARTIAL_TP1_CLOSE_PCT", 50.0)
    # TP2: close 30% at 2.5R, tighten trail
    tp2_r_multiple: float = _env_float("PARTIAL_TP2_R", 2.5)
    tp2_close_pct: float = _env_float("PARTIAL_TP2_CLOSE_PCT", 30.0)
    # Runner: remaining 20% rides with aggressive trailing stop
    runner_trail_atr_mult: float = _env_float("PARTIAL_RUNNER_TRAIL_ATR", 0.8)


@dataclass(frozen=True)
class AdaptiveConfig:
    """Adaptive threshold and smart scan settings."""
    # Adaptive confidence threshold
    adaptive_threshold_enabled: bool = _env_bool("ADAPTIVE_THRESHOLD_ENABLED", True)
    adaptive_threshold_lookback: int = int(_env_float("ADAPTIVE_THRESHOLD_LOOKBACK", 10))
    adaptive_threshold_high_wr: float = _env_float("ADAPTIVE_THRESHOLD_HIGH_WR", 0.70)
    adaptive_threshold_low_wr: float = _env_float("ADAPTIVE_THRESHOLD_LOW_WR", 0.40)
    adaptive_threshold_min: float = _env_float("ADAPTIVE_THRESHOLD_MIN", 0.60)
    adaptive_threshold_max: float = _env_float("ADAPTIVE_THRESHOLD_MAX", 0.90)
    # Smart scan scheduling: scan interval adjustment
    smart_scan_enabled: bool = _env_bool("SMART_SCAN_ENABLED", True)
    smart_scan_min_interval: int = int(_env_float("SMART_SCAN_MIN_INTERVAL", 60))   # seconds
    smart_scan_max_interval: int = int(_env_float("SMART_SCAN_MAX_INTERVAL", 600))  # seconds
    smart_scan_vol_threshold: float = _env_float("SMART_SCAN_VOL_THRESHOLD", 2.0)   # ATR multiplier for urgency


@dataclass(frozen=True)
class ExecutionConfig:
    """Execution quality and order management settings."""
    # Slippage guard
    slippage_guard_enabled: bool = _env_bool("SLIPPAGE_GUARD_ENABLED", True)
    max_slippage_edge_ratio: float = _env_float("MAX_SLIPPAGE_EDGE_RATIO", 0.30)
    # Slippage alert: the proactive monitor warns (live only) when a symbol's
    # mean absolute slippage exceeds this %, once it has at least N recorded
    # fills. Surfaces execution-quality drift before it quietly drains equity.
    slippage_alert_mean_pct: float = _env_float_bounded("SLIPPAGE_ALERT_MEAN_PCT", 0.20, 0.0, 100.0)
    slippage_alert_min_trades: int = int(_env_float_bounded("SLIPPAGE_ALERT_MIN_TRADES", 10, 1, 100000))
    # Live position caps (hard safety limits enforced by the live executor's
    # preflight). Defaults are the conservative micro-test values ($100 margin /
    # trade, $500 total, 5 positions); raise them via env for real-size live
    # trading. These are MARGIN figures — exchange notional is margin × leverage.
    max_live_position_usd: float = _env_float_bounded("MICRO_MAX_POSITION_USD", 100.0, 1.0, 10_000_000.0)
    max_live_total_exposure_usd: float = _env_float_bounded("MICRO_MAX_TOTAL_EXPOSURE", 500.0, 1.0, 100_000_000.0)
    max_live_open_positions: int = int(_env_float_bounded("MICRO_MAX_OPEN_POSITIONS", 5, 1, 1000))
    # WebSocket price staleness guard. The live SL/TP monitoring loop prefers
    # sub-second WS prices over REST, but is_connected() reflects socket state,
    # not data freshness — a silently-stalled-but-connected feed would serve a
    # stale 'last' price to stop logic. When >0, WS ticks older than this many
    # seconds are excluded from the monitoring price set, so the loop falls back
    # to REST (and the exchange-side stop remains the ultimate backstop). 0
    # disables the guard (use every WS tick regardless of age).
    ws_max_tick_age_sec: float = _env_float_bounded("WS_MAX_TICK_AGE_SEC", 15.0, 0.0, 3600.0)
    # REST ticker staleness guard for the live SL/TP monitor (check_positions).
    # The WS guard above only covers the WS price path; the executor's local
    # SL/TP loop reads `last` from REST fetch_ticker, where a frozen/old value
    # (illiquidity, partial outage) could drive a false trailing tighten, a
    # premature local stop-out, or a missed breach. When a ticker's timestamp is
    # older than this many seconds, local monitoring is skipped for that symbol
    # that cycle and the exchange-side stop remains the protection. A missing
    # timestamp is NOT treated as stale (can't verify → don't disable). 0 disables.
    live_ticker_max_age_sec: float = _env_float_bounded("LIVE_TICKER_MAX_AGE_SEC", 120.0, 0.0, 3600.0)
    # Order splitting
    order_split_enabled: bool = _env_bool("ORDER_SPLIT_ENABLED", True)
    order_split_threshold_usd: float = _env_float("ORDER_SPLIT_THRESHOLD_USD", 500.0)
    order_split_tranches: int = int(_env_float("ORDER_SPLIT_TRANCHES", 3))
    order_split_delay_sec: float = _env_float("ORDER_SPLIT_DELAY_SEC", 30.0)
    # OCO bracket orders
    oco_enabled: bool = _env_bool("OCO_BRACKET_ENABLED", True)
    # Graceful degradation
    ws_disconnect_pause_sec: float = _env_float("WS_DISCONNECT_PAUSE_SEC", 60.0)
    api_degrade_reduce_only: bool = _env_bool("API_DEGRADE_REDUCE_ONLY", True)
    # Unprotected-position grace guard.
    # A just-opened position whose exchange stop has not yet been placed is
    # only monitored on the next scan tick (~10-60s away) — a real blind window
    # on a leveraged perp. When True, the monitor runs a tight, BOUNDED inline
    # sub-loop the moment it sees such a position: each iteration re-attempts the
    # exchange stop and, if price has already breached the local stop, closes the
    # position — instead of waiting for the next tick. Purely protective (it
    # never opens or rejects a trade), so it defaults ON.
    unprotected_guard_enabled: bool = _env_bool("UNPROTECTED_GUARD_ENABLED", True)
    # Max sub-loop iterations (bounds worst-case time it can delay the rest of
    # the monitor: max_iterations * interval). Clamped to keep it from wedging.
    unprotected_guard_max_iterations: int = int(
        _env_float_bounded("UNPROTECTED_GUARD_MAX_ITER", 8, 1, 60))
    # Seconds between sub-loop iterations.
    unprotected_guard_interval_s: float = _env_float_bounded(
        "UNPROTECTED_GUARD_INTERVAL_S", 1.0, 0.1, 10.0)
    # Persistently-unprotected position escalation.
    # An adopted/emergency position whose exchange stop still cannot be placed
    # is retried every scan tick and price-monitored locally, but the operator
    # was only alerted ONCE (at adoption). When True, re-alert on the throttle
    # below until the stop lands, and clear the stale "unprotected" marker the
    # moment it does. This only ALERTS — it never force-closes an adopted
    # position (which may be pre-existing / intentional); the local static SL
    # check remains the close-on-breach backstop.
    unprotected_escalation_enabled: bool = _env_bool("UNPROTECTED_ESCALATION_ENABLED", True)
    # Minimum seconds between repeat operator alerts for the same still-
    # unprotected position (throttle so it never spams).
    unprotected_alert_interval_s: float = _env_float_bounded(
        "UNPROTECTED_ALERT_INTERVAL_S", 300.0, 10.0, 86400.0)
    # Proactive-monitor unprotected-position alert: grace seconds before an open
    # live position with NO exchange stop is treated as a self-heal FAILURE and
    # alerted (must exceed the ~90s placement grace so a normal just-opened
    # position isn't flagged while its stop is still being placed).
    unprotected_alert_grace_seconds: float = _env_float_bounded(
        "UNPROTECTED_ALERT_GRACE_SECONDS", 120.0, 90.0, 3600.0)


@dataclass(frozen=True)
class ConfluenceConfig:
    """Confluence-scoring controls."""
    # De-correlate the co-firing mean-reversion OSCILLATOR family — RSI,
    # Bollinger %B, Stochastic and Fibonacci all measure "price is low/high in
    # its recent range", so on an oversold bar they all vote bullish together
    # and inflate the confluence score with what is really ONE piece of
    # information. When enabled, their COMBINED weight is scaled down to
    # mr_oscillator_weight_cap so the family counts as ~one strong voter rather
    # than four independent confirmations.
    #
    # Default OFF: this changes which signals clear min_confidence, so enable it
    # only after validating the trade-set delta on the backtest harness.
    family_cap_enabled: bool = _env_bool("CONFLUENCE_FAMILY_CAP_ENABLED", False)
    # Max COMBINED weight the mean-reversion oscillator family may contribute.
    # The default (2.0) is ~the single largest member (RSI at 1.5) plus a little,
    # vs. an uncapped ~4.2 when all four co-fire.
    mr_oscillator_weight_cap: float = _env_float_bounded(
        "CONFLUENCE_MR_OSC_WEIGHT_CAP", 2.0, 0.1, 100.0)


@dataclass(frozen=True)
class CacheConfig:
    """LLM semantic cache settings."""
    ttl_seconds: float = _env_float("CACHE_TTL_SECONDS", 300.0)
    max_size: int = int(_env_float("CACHE_MAX_SIZE", 200))


@dataclass(frozen=True)
class TrailingStopConfig:
    """Trailing stop configuration for live positions.

    Strategy: trailing stop activates after 1R profit, then trails at
    trail_atr_mult * ATR behind the best favorable price.
    """
    enabled: bool = _env_bool("TRAILING_STOP_ENABLED", True)
    # ATR multiplier for trailing distance (1.5 = trail at 1.5x ATR)
    trail_atr_mult: float = _env_float("TRAILING_ATR_MULT", 1.5)
    # Minimum price move (%) before updating exchange SL order.
    # Avoids spamming the exchange with tiny SL adjustments.
    min_sl_update_pct: float = _env_float("TRAILING_MIN_SL_UPDATE_PCT", 0.3)
    # Trail rule (LIVE stop management):
    #   "multistage" (default) — 1R-activation 4-stage trail behind best price.
    #   "playbook"             — trail the SL playbook_atr_mult·ATR behind the
    #                            MARK, tighten-only, NO 1R activation gate (matches
    #                            the external Playbook geometry). This FIRES EARLIER
    #                            and tightens sooner, so it changes realized P&L —
    #                            validate before enabling. Default keeps the proven
    #                            multistage behaviour unchanged.
    trail_rule: str = _env("TRAILING_RULE", "multistage")
    playbook_atr_mult: float = _env_float("TRAILING_PLAYBOOK_ATR_MULT", 2.0)


@dataclass(frozen=True)
class LimitOrderConfig:
    """Limit order support configuration."""
    enabled: bool = _env_bool("LIMIT_ORDERS_ENABLED", True)
    # Default order type: "market" or "limit"
    default_order_type: str = _env("DEFAULT_ORDER_TYPE", "limit")
    # Max seconds to wait for a limit order fill before cancelling
    expire_seconds: int = int(_env_float("LIMIT_ORDER_EXPIRE_SEC", 14400))  # 4 hours
    # Check interval for pending limit orders (seconds)
    check_interval_seconds: int = int(_env_float("LIMIT_CHECK_INTERVAL_SEC", 30))
    # Use POST_ONLY time-in-force to guarantee maker-only (rejects if would fill)
    post_only: bool = _env_bool("LIMIT_POST_ONLY", True)
    # Cancel pending limit if price drifts more than this % away from limit price
    price_drift_cancel_pct: float = _env_float("LIMIT_DRIFT_CANCEL_PCT", 2.0)
    # Market order fallback: if price drifts AND momentum is strong,
    # convert to market order instead of just cancelling the limit.
    drift_market_fallback: bool = _env_bool("LIMIT_DRIFT_MARKET_FALLBACK", True)
    # Minimum ADX to consider momentum "strong enough" for market fallback
    drift_market_min_adx: float = _env_float("LIMIT_DRIFT_MARKET_MIN_ADX", 20.0)


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
    # Auto-close LIVE positions on smart-exit triggers (time stop, signal-hold
    # limit, VWAP-reversion done/failed, volume-signal decay). Default OFF: a
    # live position whose thesis has invalidated otherwise rides all the way to
    # its exchange stop-loss. Paper positions already auto-close on these
    # triggers in _check_paper_positions; this opt-in extends the SAME checks to
    # real positions, closing at market via the executor. Byte-identical live
    # behaviour until enabled.
    live_auto_close_enabled: bool = _env_bool("TIME_STOP_LIVE_AUTO_CLOSE", False)


@dataclass(frozen=True)
class StrategyTypeConfig:
    """Per-strategy-type SL/TP/trailing/time-stop overrides.

    Each strategy type has its own risk parameters:
    - scalp:     tight stops, fast exit, no trailing, 30 min time-stop
    - intraday:  moderate stops, trailing after 1R, 4h time-stop
    - swing:     wide stops, trailing after 1R, 24h time-stop
    - position:  widest stops, trailing after 1.5R, 72h time-stop
    """
    # ── SCALP (hold: 5-30 min) ──
    scalp_sl_atr_mult: float = _env_float("SCALP_SL_ATR_MULT", 1.5)
    scalp_tp_atr_mult: float = _env_float("SCALP_TP_ATR_MULT", 2.0)
    scalp_trailing_enabled: bool = _env_bool("SCALP_TRAILING_ENABLED", False)
    scalp_trailing_atr_mult: float = _env_float("SCALP_TRAILING_ATR_MULT", 1.0)
    scalp_time_close_hours: float = _env_float("SCALP_TIME_CLOSE_H", 0.5)
    scalp_time_warn_hours: float = _env_float("SCALP_TIME_WARN_H", 0.25)

    # ── INTRADAY (hold: 30 min - 4h) ──
    intraday_sl_atr_mult: float = _env_float("INTRADAY_SL_ATR_MULT", 2.0)
    intraday_tp_atr_mult: float = _env_float("INTRADAY_TP_ATR_MULT", 2.5)
    intraday_trailing_enabled: bool = _env_bool("INTRADAY_TRAILING_ENABLED", True)
    intraday_trailing_atr_mult: float = _env_float("INTRADAY_TRAILING_ATR_MULT", 1.2)
    intraday_time_close_hours: float = _env_float("INTRADAY_TIME_CLOSE_H", 4.0)
    intraday_time_warn_hours: float = _env_float("INTRADAY_TIME_WARN_H", 2.0)

    # ── SWING (hold: 4h - 7 days) ──
    swing_sl_atr_mult: float = _env_float("SWING_SL_ATR_MULT", 2.5)
    swing_tp_atr_mult: float = _env_float("SWING_TP_ATR_MULT", 3.5)
    swing_trailing_enabled: bool = _env_bool("SWING_TRAILING_ENABLED", True)
    swing_trailing_atr_mult: float = _env_float("SWING_TRAILING_ATR_MULT", 1.5)
    swing_time_close_hours: float = _env_float("SWING_TIME_CLOSE_H", 48.0)
    swing_time_warn_hours: float = _env_float("SWING_TIME_WARN_H", 12.0)

    # ── POSITION (hold: 1-30 days) ──
    position_sl_atr_mult: float = _env_float("POSITION_SL_ATR_MULT", 3.0)
    position_tp_atr_mult: float = _env_float("POSITION_TP_ATR_MULT", 5.0)
    position_trailing_enabled: bool = _env_bool("POSITION_TRAILING_ENABLED", True)
    position_trailing_atr_mult: float = _env_float("POSITION_TRAILING_ATR_MULT", 2.0)
    position_time_close_hours: float = _env_float("POSITION_TIME_CLOSE_H", 168.0)  # 7 days
    position_time_warn_hours: float = _env_float("POSITION_TIME_WARN_H", 72.0)

    # ── Per-type risk parameters ──
    # Min confidence threshold per type
    scalp_min_confidence: float = _env_float("SCALP_MIN_CONFIDENCE", 0.65)
    intraday_min_confidence: float = _env_float("INTRADAY_MIN_CONFIDENCE", 0.55)
    swing_min_confidence: float = _env_float("SWING_MIN_CONFIDENCE", 0.50)
    position_min_confidence: float = _env_float("POSITION_MIN_CONFIDENCE", 0.45)

    # Max risk per trade (% of equity)
    scalp_max_risk_pct: float = _env_float("SCALP_MAX_RISK_PCT", 1.0)
    intraday_max_risk_pct: float = _env_float("INTRADAY_MAX_RISK_PCT", 1.5)
    swing_max_risk_pct: float = _env_float("SWING_MAX_RISK_PCT", 2.0)
    position_max_risk_pct: float = _env_float("POSITION_MAX_RISK_PCT", 2.0)

    # Min risk:reward ratio per type
    scalp_min_rr: float = _env_float("SCALP_MIN_RR", 1.2)
    intraday_min_rr: float = _env_float("INTRADAY_MIN_RR", 1.5)
    swing_min_rr: float = _env_float("SWING_MIN_RR", 1.5)
    position_min_rr: float = _env_float("POSITION_MIN_RR", 2.0)

    # Smart money weight multiplier per type (applied to SM confluence votes)
    scalp_smart_money_weight: float = _env_float("SCALP_SM_WEIGHT", 0.5)
    intraday_smart_money_weight: float = _env_float("INTRADAY_SM_WEIGHT", 1.0)
    swing_smart_money_weight: float = _env_float("SWING_SM_WEIGHT", 1.5)
    position_smart_money_weight: float = _env_float("POSITION_SM_WEIGHT", 2.0)

    # Volume spike confidence bonus per type
    scalp_volume_bonus: float = _env_float("SCALP_VOL_BONUS", 0.10)
    intraday_volume_bonus: float = _env_float("INTRADAY_VOL_BONUS", 0.05)
    swing_volume_bonus: float = _env_float("SWING_VOL_BONUS", 0.03)
    position_volume_bonus: float = _env_float("POSITION_VOL_BONUS", 0.02)

    def get_sl_mult(self, strategy_type: str) -> float:
        return getattr(self, f"{strategy_type}_sl_atr_mult", 2.5)

    def get_tp_mult(self, strategy_type: str) -> float:
        return getattr(self, f"{strategy_type}_tp_atr_mult", 3.0)

    def get_trailing_enabled(self, strategy_type: str) -> bool:
        return getattr(self, f"{strategy_type}_trailing_enabled", True)

    def get_trailing_atr_mult(self, strategy_type: str) -> float:
        return getattr(self, f"{strategy_type}_trailing_atr_mult", 1.5)

    def get_time_close_hours(self, strategy_type: str) -> float:
        return getattr(self, f"{strategy_type}_time_close_hours", 24.0)

    def get_time_warn_hours(self, strategy_type: str) -> float:
        return getattr(self, f"{strategy_type}_time_warn_hours", 12.0)

    def get_min_confidence(self, strategy_type: str) -> float:
        return getattr(self, f"{strategy_type}_min_confidence", 0.50)

    def get_max_risk_pct(self, strategy_type: str) -> float:
        return getattr(self, f"{strategy_type}_max_risk_pct", 2.0)

    def get_min_rr(self, strategy_type: str) -> float:
        return getattr(self, f"{strategy_type}_min_rr", 1.5)

    def get_smart_money_weight(self, strategy_type: str) -> float:
        return getattr(self, f"{strategy_type}_smart_money_weight", 1.0)

    def get_volume_bonus(self, strategy_type: str) -> float:
        return getattr(self, f"{strategy_type}_volume_bonus", 0.05)


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

    # Per-user live trading: when enabled, a user's confirmed live trades execute
    # on THEIR OWN linked Bitget account (via /connect, encrypted at rest) instead
    # of the shared operator account. Default OFF — until set, the bot behaves
    # exactly as before (single operator account). This is the master switch for
    # the per-user-accounts feature; see docs/LIVE_TRADING_ENABLEMENT.md.
    per_user_live_enabled: bool = _env_bool("PER_USER_LIVE_ENABLED", False)

    # -- Auto-confirmation --
    # Signals with blended confidence >= this threshold auto-execute without
    # waiting for a human button press. Default 1.0 = DISABLED (require manual
    # confirm for all trades). Range: 0.0–1.0.
    # SECURITY (RC-AUD-002): auto-confirm bypasses the human-decision gate, so it
    # is OFF by default. Lowering the threshold permits PAPER auto-execution only;
    # placing LIVE orders with no human press additionally requires the explicit
    # AUTO_CONFIRM_LIVE_ENABLED opt-in below (fail-closed).
    auto_confirm_threshold: float = _env_float("AUTO_CONFIRM_THRESHOLD", 1.0)
    # Allow auto-confirm to place LIVE (real-money) orders with no human press.
    # Even with a low threshold, auto-confirm cannot trade live unless this is set.
    auto_confirm_live_enabled: bool = _env_bool("AUTO_CONFIRM_LIVE_ENABLED", False)
    # Gate auto-confirm on CALIBRATED confidence (opt-in, default OFF). When ON
    # AND a fitted confidence calibrator exists, the auto-confirm threshold is
    # tested against min(raw, calibrated) confidence — so a real-money auto-trade
    # requires BOTH the raw blend AND the measured (calibrated) win-rate to clear
    # the bar. This can only TIGHTEN auto-confirm, never loosen it: with no
    # calibration data the calibrator is identity, so it is a no-op until evidence
    # shows the raw confidence is over-optimistic. Makes the 0.85 admin auto-trade
    # mean "~85% realized win rate", not a raw LLM+voter blend.
    auto_confirm_use_calibrated: bool = _env_bool("AUTO_CONFIRM_USE_CALIBRATED", False)
    # TTL for pending ideas in seconds (default 300 = 5 min)
    pending_idea_ttl: int = int(_env_float("PENDING_IDEA_TTL", 300))

    # -- Paper trading --
    paper_balance_usd: float = _env_float("PAPER_BALANCE_USD", 10_000.0)
    portfolio_state_file: str = _env("PORTFOLIO_STATE_FILE", "data/portfolio_state.json")
    # Per-user PAPER (sim) opt-in. When enabled (default OFF), a user who has
    # opted in via /paper has THEIR confirmed trades SIMULATED into their paper
    # portfolio instead of sent to the exchange — risk-free practice on a live
    # bot. This NEVER affects other users or the live execution path: the opt-in
    # branch runs before any exchange call. Default OFF = byte-identical to today.
    paper_sim_opt_in_enabled: bool = _env_bool("PAPER_SIM_OPT_IN_ENABLED", False)

    # -- Scan settings --
    scan_interval_seconds: int = int(_env_float("SCAN_INTERVAL", 60))
    top_movers_count: int = int(_env_float("TOP_MOVERS_COUNT", 80))

    # -- Sub-configs --
    risk: RiskLimits = field(default_factory=RiskLimits)
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    analyzer: AnalyzerConfig = field(default_factory=AnalyzerConfig)
    learning: LearningConfig = field(default_factory=LearningConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    time_stop: TimeStopConfig = field(default_factory=TimeStopConfig)
    trailing: TrailingStopConfig = field(default_factory=TrailingStopConfig)
    limit_orders: LimitOrderConfig = field(default_factory=LimitOrderConfig)
    stocks: StockTradingConfig = field(default_factory=StockTradingConfig)
    strategy_types: StrategyTypeConfig = field(default_factory=StrategyTypeConfig)
    partial_tp: PartialTPConfig = field(default_factory=PartialTPConfig)
    adaptive: AdaptiveConfig = field(default_factory=AdaptiveConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    confluence: ConfluenceConfig = field(default_factory=ConfluenceConfig)

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
        self._auto_confirm_threshold: float = CONFIG.auto_confirm_threshold

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
        if value not in ("all_markets", "all", "solana", "stocks", "hybrid", "metals",
                         "commodities", "etfs", "pre_ipo", "tradfi"):
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

    @property
    def auto_confirm_threshold(self) -> float:
        with self._lock:
            return self._auto_confirm_threshold

    @auto_confirm_threshold.setter
    def auto_confirm_threshold(self, value: float) -> None:
        with self._lock:
            self._auto_confirm_threshold = max(0.0, min(1.0, value))


RUNTIME = RuntimeState()
