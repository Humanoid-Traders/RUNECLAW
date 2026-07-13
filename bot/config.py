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

# ── Env precedence (RC-AUD-019, flipped 2026-07-11) ─────────────────────────
# .env is now the SOURCE OF TRUTH: load_dotenv(override=True), so a key present
# in .env replaces any value inherited from the process/OS environment.
#     .env file  >  process/OS env  >  in-code default
#
# Live incident (2026-07-11): the operator edited the LLM_TIER_* model ids in
# .env, but stale exports inherited from the shell/supervisor silently WON
# (the old override=False precedence), so every tier kept calling a dead model
# id and the bot ran on the rule engine while looking configured. The operator
# edits exactly one file — .env — and that file must take effect on restart.
#
# Escape hatch: set RUNECLAW_ENV_INHERIT=1 in the PROCESS environment to keep
# the old behaviour (process env wins) for deployments that deliberately inject
# config via the environment (docker/systemd/CI). Note that keys NOT present in
# .env are never touched under either mode, so one-off CLI exports for A/B runs
# (e.g. RSI_OVERSOLD_BLOCK=32 python -m bot.backtest.runner) keep working as
# long as the key is not also set in .env.
#
# To tell "inherited from the process env" apart from "loaded from .env" we
# snapshot os.environ BEFORE load_dotenv — afterwards the two sources are
# indistinguishable because load_dotenv injects .env keys into os.environ.
_ENV_INHERIT_MODE: bool = (
    os.environ.get("RUNECLAW_ENV_INHERIT", "").strip().lower()
    in ("1", "true", "yes", "on"))
_PRE_DOTENV_ENV: dict[str, str] = dict(os.environ)
_PRE_DOTENV_ENV_KEYS: frozenset[str] = frozenset(_PRE_DOTENV_ENV)

# Deployment robustness: resolve .env from the REPO ROOT (parent of bot/)
# rather than relying on the process CWD. Bare load_dotenv() only searches from
# the current working directory upward, so a bot started from any other
# directory silently loads NO .env — the exact fragility flagged in the deploy
# report (config relied on CWD being the repo dir at startup). We pass an
# explicit path when the repo-root .env exists, and fall back to the default
# CWD search otherwise so env-var-only / alternate-location deployments (e.g. a
# systemd unit that injects vars directly, or a .env placed elsewhere) still
# work exactly as before.
from pathlib import Path as _Path  # noqa: E402

_REPO_ROOT_ENV = _Path(__file__).resolve().parent.parent / ".env"
_DOTENV_OVERRIDE: bool = not _ENV_INHERIT_MODE
if _REPO_ROOT_ENV.is_file():
    load_dotenv(dotenv_path=_REPO_ROOT_ENV, override=_DOTENV_OVERRIDE)
else:
    load_dotenv(override=_DOTENV_OVERRIDE)


def _detect_replaced_inherited_keys(
        pre_env: dict, post_env: dict) -> list[str]:
    """Keys whose inherited process-env value was REPLACED by .env under
    override mode. Pure function of the two snapshots — testable without
    touching the ambient environment. Sorted for deterministic logging."""
    return sorted(
        k for k, v in pre_env.items()
        if k in post_env and post_env[k] != v)


# Inherited keys that .env replaced (empty in inherit mode, where .env never
# overrides). Logged at startup so a precedence flip is never silent.
_REPLACED_INHERITED_KEYS: list[str] = _detect_replaced_inherited_keys(
    _PRE_DOTENV_ENV, dict(os.environ))

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
    """RC-AUD-019 (updated for the precedence flip): warn when a safety
    switch's EFFECTIVE value comes from the inherited process environment.

    Under the new default (.env wins), an inherited safety switch only
    governs when .env did not replace it — i.e. the key is absent from .env,
    or RUNECLAW_ENV_INHERIT=1 keeps the old precedence. Also logs every
    inherited key that .env replaced, so the precedence flip is never silent.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)
    if _REPLACED_INHERITED_KEYS:
        _log.warning(
            ".env OVERRODE %d inherited process-env key(s): %s "
            "(precedence: .env > process env; set RUNECLAW_ENV_INHERIT=1 to "
            "keep inherited values instead).",
            len(_REPLACED_INHERITED_KEYS), ", ".join(_REPLACED_INHERITED_KEYS),
        )
    for _key in _INHERITED_SAFETY_SWITCHES:
        if _key in _REPLACED_INHERITED_KEYS:
            continue  # .env replaced it — the operator's file governs.
        _log.warning(
            "Safety switch %s=%r comes from the INHERITED process environment "
            "(not replaced by .env — the key is absent from .env%s). If this "
            "was not intended, unset it in the process/container environment "
            "or set it explicitly in .env.",
            _key, os.environ.get(_key, ""),
            " or RUNECLAW_ENV_INHERIT=1 is keeping inherited values"
            if _ENV_INHERIT_MODE else "",
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
    # Volatility-targeted position cap (design bet; default ON, tighten-only). The
    # notional cap binds on ~every crypto trade, so sizing is effectively flat
    # margin and realized per-trade risk scales UP with ATR%. The binding cap
    # floats INVERSELY with ATR% (target vol_target_atr_pct) so per-trade dollar
    # risk is normalized toward a constant. Floor bounds how far the cap can
    # shrink. A/B-validated on the honest benchmark (mean OOS -1.14%->-0.91%,
    # worst fold -4.12%->-2.72%, zero downside); disable with =0.
    vol_target_sizing_enabled: bool = _env_bool("VOL_TARGET_SIZING_ENABLED", True)
    vol_target_atr_pct: float = _env_float("VOL_TARGET_ATR_PCT", 3.0)
    vol_target_floor: float = _env_float("VOL_TARGET_FLOOR", 0.33)
    # #47: when ON, the notional cap + the POSITION_SIZE check use the per-strategy
    # cap (StrategyTypeConfig.get_max_position_pct) instead of the single global
    # max_position_pct, so a scalp can ride a tighter notional ceiling than a
    # position trade. Default ON; disable to fall back to max_position_pct.
    per_strategy_notional_cap_enabled: bool = _env_bool("PER_STRATEGY_NOTIONAL_CAP_ENABLED", True)
    # When ON, the CONFIDENCE check uses the per-strategy-type floor
    # (StrategyTypeConfig.get_min_confidence: scalp 0.65/intraday 0.55/swing
    # 0.50/position 0.45) instead of the single global min_confidence. The
    # analyzer already gates idea generation on these per-type floors
    # (bot/core/analyzer.py), but the risk engine re-gates on the flat global
    # value downstream -- for swing/intraday/position (floors below the
    # global default) that flat re-gate silently rejects trades the analyzer
    # already approved at its own tuned threshold. Frozen-benchmark A/B'd;
    # see docs/FROZEN_BENCHMARK.md. Default OFF; disable to keep the flat gate.
    per_strategy_confidence_floor_enabled: bool = _env_bool("PER_STRATEGY_CONFIDENCE_FLOOR_ENABLED", False)
    max_daily_loss_pct: float = _env_float_bounded("MAX_DAILY_LOSS_PCT", 5.0, 0.1, 50)
    # Auto-reset a DAILY-LOSS circuit-breaker trip at UTC day rollover (opt-in,
    # default OFF; deep-audit medium). The daily-loss limit is a per-day guard,
    # but the breaker is a single latch with no record of why it tripped, so a
    # single bad day halts trading until a human runs /reset — even after
    # daily_pnl rolls back to ~0. When ON, ONLY a daily-loss-caused trip is
    # cleared once the day has rolled over (and the loss-streak guard is not
    # itself active); drawdown / streak / manual trips stay manual. If the new
    # day is also bad, the daily-loss check re-trips immediately. Default ON;
    # disable to keep the breaker fully manual.
    daily_loss_breaker_autoreset_enabled: bool = _env_bool("DAILY_LOSS_BREAKER_AUTORESET", True)
    # Streak-breaker self-recovery (default 0 = OFF, opt-in). The consecutive-
    # loss breaker (trips at max_consecutive_losses) is manual-reset only, so a
    # live bot sits PAUSED until a human runs /resume — and re-pauses the next
    # time it loses a streak. Set this to a positive number of hours to let the
    # streak breaker auto-clear that many hours after the LAST loss (resets the
    # streak counter to 0, exactly like /resume), so an unattended admin
    # account keeps trading instead of latching. Daily-loss and DRAWDOWN
    # breakers are unaffected — those protect the account balance and still
    # need their own recovery. NOTE: this automates resuming; if the strategy
    # is losing, it resumes INTO more losses — the drawdown breaker remains the
    # backstop. 0 keeps the safe manual-only behavior.
    streak_breaker_autoreset_hours: float = _env_float_bounded(
        "STREAK_BREAKER_AUTORESET_HOURS", 0.0, 0.0, 168.0)
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
    # SIGNAL QUALITY: 0.60 (raised from 0.55). The frozen honest-fidelity
    # benchmark (docs/FROZEN_BENCHMARK.md, --confidence-threshold sweep) showed
    # 0.55 is a no-op on top of this gate (identical trades/PF to no extra
    # floor) while 0.60 cuts ~10-13% of the lowest-conviction trades and raises
    # both universes: majors +0.49%->+0.62% (PF 1.24->1.38), combined
    # majors+alts +0.68%->+1.00% (PF 1.29->1.55). 0.65 also beat baseline but
    # underperformed 0.60.
    min_confidence: float = _env_float_bounded("MIN_CONFIDENCE", 0.60, 0.1, 1.0)
    # Minimum confidence shown in "Latest Signal" display (filters UI noise)
    signal_display_min_confidence: float = _env_float_bounded(
        "SIGNAL_DISPLAY_MIN_CONFIDENCE", 0.70, 0.1, 1.0)
    max_consecutive_losses: int = int(_env_float_bounded("MAX_CONSECUTIVE_LOSSES", 5, 1, 50))
    # Half-open recovery for the SOFT loss-streak gate (risk check #9). The
    # soft gate rejects new ideas at (hard - 2) consecutive losses, but the
    # streak only decays on a WIN — with trading blocked no win can ever
    # happen, so the gate was a PERMANENT silent latch two losses below the
    # visible circuit breaker (production-data backtest: 3 early losses froze
    # the strategy for the remaining ~8 months; live freezes the same way,
    # the operator just sees a bot that scans but never trades). After this
    # cool-off since the last loss, ONE probe trade is allowed at a time
    # (only while flat): a losing probe re-arms the gate and walks the streak
    # toward the hard breaker (still manual-reset); a winning probe decays
    # it. 0 disables probing (legacy permanent latch).
    loss_streak_probe_hours: float = _env_float_bounded(
        "LOSS_STREAK_PROBE_HOURS", 24.0, 0.0, 720.0)
    # Silent-strangle watchdog (proactive monitor): WARN the operator when
    # ideas keep flowing but NOTHING is approved for a whole window — the
    # failure shape of a silently latched gate (the loss-streak latch ran a
    # production backtest dry for ~8 months with zero operator-visible
    # signal). Names the top rejecting gate from gate telemetry. 0 = off.
    strangle_alert_hours: float = _env_float_bounded(
        "STRANGLE_ALERT_HOURS", 12.0, 0.0, 168.0)
    strangle_min_ideas: int = int(_env_float_bounded(
        "STRANGLE_MIN_IDEAS", 10, 1, 10000))
    cooldown_after_loss_seconds: int = int(_env_float("COOLDOWN_AFTER_LOSS_SEC", 120))
    # Per-SYMBOL loss-streak cooldown. The account-wide streak above decays on
    # ANY win, so a chronically-losing symbol keeps getting re-entered as long
    # as OTHER symbols occasionally win — and the existing post-SL cooldown
    # (SYMBOL_SL_COOLDOWN_SEC, 30 min) only guards against immediate re-entry
    # after a single stop-out, not a demonstrated repeat-loser. This tracks
    # consecutive losses PER SYMBOL (same decay-on-win logic as the account-
    # wide streak) and, once a symbol hits the threshold, blocks new entries on
    # THAT symbol for a much longer cooldown by reusing the same
    # engine._symbol_cooldowns mechanism the post-SL cooldown already uses.
    symbol_loss_streak_enabled: bool = _env_bool("SYMBOL_LOSS_STREAK_ENABLED", True)
    symbol_loss_streak_threshold: int = int(
        _env_float_bounded("SYMBOL_LOSS_STREAK_THRESHOLD", 3, 1, 20))
    symbol_loss_streak_cooldown_seconds: float = _env_float_bounded(
        "SYMBOL_LOSS_STREAK_COOLDOWN_SEC", 43200.0, 60.0, 604800.0)  # 12h default
    max_portfolio_exposure_pct: float = _env_float_bounded("MAX_PORTFOLIO_EXPOSURE_PCT", 80.0, 0.0, 1000.0)
    max_symbol_exposure_pct: float = _env_float_bounded("MAX_SYMBOL_EXPOSURE_PCT", 20.0, 0.0, 1000.0)
    max_correlation_per_group: int = int(_env_float("MAX_CORRELATION_PER_GROUP", 2))
    # Symbols not in the known correlation map were each treated as their OWN
    # group, so a basket of unmapped alts could collectively dodge the per-group
    # cap (the live report's many-correlated-alts exposure). They are now pooled
    # into ONE shared "unmapped alt" bucket with its own, more generous cap
    # (unmapped symbols aren't all mutually correlated). Set high to disable.
    max_unmapped_correlated: int = int(_env_float_bounded("MAX_UNMAPPED_CORRELATED", 3, 1, 100))
    # Round 7 Phase 1: make the per-group correlation cap FORWARD-LOOKING. The cap
    # counts only already-OPEN positions, so a correlated cluster that all signal
    # on the same bar each see zero open group members and all pass — silently
    # bypassing max_correlation_per_group exactly when correlation risk is highest
    # (a synchronized, market-wide move). When enabled, the risk engine also counts
    # APPROVED-but-not-yet-filled intents (registered by the caller between risk
    # approval and fill), so the Nth correlated same-bar entry is blocked. Default
    # OFF pending A/B validation on the frozen benchmarks.
    correlation_forward_intents_enabled: bool = _env_bool("CORRELATION_FORWARD_INTENTS_ENABLED", False)
    # Safety TTL for a pending intent (seconds; sim-time in backtest via
    # set_sim_time, wall-time live). Explicit clear on fill/cancel is primary —
    # this only backstops a leaked intent (e.g. an exception between register and
    # fill) so the ledger can never latch the cap. Generous so it never prunes a
    # legitimately-pending next-bar fill.
    correlation_intent_ttl_sec: float = _env_float_bounded("CORRELATION_INTENT_TTL_SEC", 7200.0, 1.0, 604800.0)
    # Round 7: correct per-group correlation mapping for ccxt perp symbols.
    # _correlation_group never stripped the ":SETTLE" suffix, so every futures
    # symbol ("SOL/USDT:USDT") missed the spot-keyed map and pooled into ONE
    # unmapped bucket — a bug, but one that accidentally bounded TOTAL correlated
    # exposure (the pooled cap applied across all alts). Enabling the strip
    # restores the intended ALT_L1/MEME/DEFI per-group caps, but on a dense
    # multi-group A/B that LOOSENS aggregate exposure and roughly doubled max
    # drawdown. Default OFF preserves the tighter pooled behaviour until the
    # corrected mapping is paired with a global correlated-position cap.
    correlation_perp_group_mapping_enabled: bool = _env_bool("CORRELATION_PERP_GROUP_MAPPING_ENABLED", False)
    # Round 7 (revised Phase 2): global correlated-exposure cap. Correct per-group
    # mapping alone doesn't bound TOTAL correlated exposure — each group carries
    # its own budget, so a market-wide move can still stack many same-direction
    # correlated bets (up to max_open_positions), which is exactly the tail the
    # pooled-bucket bug was accidentally bounding. This caps concurrent SAME-
    # DIRECTION positions across ALL correlated groups (open + pending intents),
    # restoring the aggregate bound while keeping correct per-group attribution.
    # Only active when the perp mapping is enabled. 0 = disabled (default).
    max_correlated_same_dir_positions: int = int(_env_float_bounded("MAX_CORRELATED_SAME_DIR_POSITIONS", 0, 0, 100))
    # Fee-aware entry gate (opt-in, default OFF). The min-RR gate is a RATIO — it
    # can pass a tight-stop scalp whose absolute take-profit distance barely
    # exceeds round-trip cost. This rejects an entry unless the reward to the TP
    # clears (round-trip fees + slippage) by fee_aware_min_multiple. Kills
    # fee-losing trades directly. Skips manual trades (operator chose the levels).
    fee_aware_entry_gate_enabled: bool = _env_bool("FEE_AWARE_ENTRY_GATE_ENABLED", False)
    # Safety multiple: the TP reward must be at least this × the round-trip cost.
    fee_aware_min_multiple: float = _env_float_bounded("FEE_AWARE_MIN_MULTIPLE", 2.0, 1.0, 100.0)
    # Per-side slippage estimate (%) used in the round-trip cost (2× entry+exit).
    fee_aware_slippage_pct: float = _env_float_bounded("FEE_AWARE_SLIPPAGE_PCT", 0.05, 0.0, 100.0)
    # Re-entry cooldown (opt-in, default OFF). The existing cooldown-after-loss
    # (check #13) only fires after a LOSS; it does nothing to stop rapid
    # re-entry churn on the SAME symbol after a win/flat close. Each such
    # round-trip pays 2×(fee+slip). This throttles a fresh entry on a symbol
    # within reentry_cooldown_seconds of the last REAL fill on that symbol,
    # measured on the same simulated/live clock as the loss cooldown. Skips
    # manual trades (deliberate). 0s or flag OFF = no-op (byte-identical).
    reentry_cooldown_enabled: bool = _env_bool("REENTRY_COOLDOWN_ENABLED", False)
    reentry_cooldown_seconds: float = _env_float_bounded("REENTRY_COOLDOWN_SECONDS", 0.0, 0.0, 604800.0)
    # MTF-alignment gate (default ON — operator-activated after a positive A/B).
    # The analyzer computes a higher-timeframe trend (EMA20/50 confluence across
    # 1h/4h/1d, daily-weighted); this gate rejects a COUNTER-TREND entry: a LONG
    # when the HTF trend is bearish, or a SHORT when it is bullish. Neutral /
    # unknown HTF → no opinion (skip). A/B on corr_dense_1h (--honest, 16-month):
    # removed exactly one counter-trend loser and kept all winners (PF 1.87→2.23,
    # +1.40%→+1.66%); neutral on alts_1h_v2. Set 0 in .env to restore the legacy
    # behaviour (the gate was historically dead — it parsed "MTF:1h=UP" tags that
    # nothing produced, so it skipped every trade).
    mtf_alignment_gate_enabled: bool = _env_bool("MTF_ALIGNMENT_GATE_ENABLED", True)
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
    # Feeds record_equity_snapshot() from evaluate() so the equity-curve breaker
    # is actually driven. Default OFF: it ADDS a de-risk/pause condition, so it is
    # opt-in (its feeder was never called, leaving the breaker permanently inert).
    equity_curve_breaker_enabled: bool = _env_bool("EQUITY_CURVE_BREAKER_ENABLED", False)
    # Drawdown recovery mode: after hitting max DD, enter conservative mode
    drawdown_recovery_conf_min: float = _env_float("DRAWDOWN_RECOVERY_CONF_MIN", 0.85)
    drawdown_recovery_size_mult: float = _env_float("DRAWDOWN_RECOVERY_SIZE_MULT", 0.5)
    # Feeds check_drawdown_recovery() from evaluate() so recovery mode can actually
    # activate. Default OFF: it ADDS a higher-confidence + reduced-size restriction,
    # so it is opt-in (its feeder was never called, leaving recovery mode inert).
    drawdown_recovery_enabled: bool = _env_bool("DRAWDOWN_RECOVERY_ENABLED", False)
    # Kelly-criterion sizing (default ON; runbook stage 2, tighten-only). evaluate() also
    # derives a half-Kelly size from realized trade history and takes the SMALLER
    # of {fixed-fractional, Kelly}: Kelly can only TIGHTEN size, never grow it, and
    # the notional/margin caps below stay authoritative. Below kelly_min_trades
    # closed trades there is no edge estimate, so it is a no-op (size unchanged).
    kelly_sizing_enabled: bool = _env_bool("KELLY_SIZING_ENABLED", True)
    kelly_min_trades: int = int(_env_float_bounded("KELLY_MIN_TRADES", 20, 1, 100000))
    # Portfolio-aware correlation sizing (default ON; shrink-only). The existing
    # correlation check (_check_correlation) is a count-cap CONCENTRATION GATE:
    # it rejects once a group is full but does nothing for the trades it lets
    # through. This adds a graduated size REDUCTION for a new trade that shares a
    # correlation group AND direction with already-open positions, so the second
    # and third correlated bet are smaller (the marginal portfolio risk they add
    # is larger). It can only SHRINK size (multiplier in [floor, 1.0]); the
    # notional/margin caps and every gate below stay authoritative. Default OFF
    # makes this byte-identical to prior behaviour.
    correlation_sizing_enabled: bool = _env_bool("CORRELATION_SIZING_ENABLED", True)
    # Reduction per same-group same-direction open position (0.20 → −20% each).
    correlation_sizing_step: float = _env_float_bounded("CORRELATION_SIZING_STEP", 0.20, 0.0, 1.0)
    # Floor on the multiplier — size is never reduced below this fraction.
    correlation_sizing_floor: float = _env_float_bounded("CORRELATION_SIZING_FLOOR", 0.5, 0.1, 1.0)
    # Live risk hardening (default ON; runbook stage 1). When ON *and* running live, it
    # applies a stricter portfolio-risk posture for real money WITHOUT touching
    # paper/backtest behaviour:
    #   - forces correlation-aware position sizing on (even if its own flag is off),
    #   - forces covariance-based portfolio VaR on (falls back to the per-trade
    #     proxy whenever data is insufficient — never a downgrade to skip),
    #   - caps drawdown at live_max_drawdown_pct (tighter than the paper limit).
    # Default OFF → byte-identical until enabled; in paper mode it never applies.
    live_risk_hardening_enabled: bool = _env_bool("LIVE_RISK_HARDENING_ENABLED", True)
    live_max_drawdown_pct: float = _env_float_bounded("LIVE_MAX_DRAWDOWN_PCT", 7.0, 0.1, 100.0)
    # Live-performance governor (default ON; runbook stage 2). A closed-loop backstop ON
    # TOP of the pre-trade checks: it scores REALIZED closed-trade outcomes over a
    # rolling window and de-risks when the strategy is actually losing — a graduated
    # SIZE REDUCTION when the recent window underperforms (low win rate OR net
    # negative), and a PAUSE (size 0, trade rejected) only when it is BOTH losing
    # often AND net-negative. It can only tighten (reduce/pause), never grow size or
    # loosen a gate, and is a no-op below live_perf_min_samples closed trades (fails
    # OPEN = normal sizing). Distinct from the equity-curve breaker (equity vs MA)
    # and the consecutive-loss breaker (streak): this reads realized win rate + net
    # PnL of the most recent trades. Default OFF → byte-identical until enabled.
    # Regime-aware position sizing (default ON). The analyzer already
    # classifies a per-symbol market regime (TREND_UP/TREND_DOWN/EXPANSION/RANGE/
    # CHOP), but it was never bridged into the risk engine, so _current_regime
    # stayed "UNKNOWN" and the per-regime size multipliers were always 1.0×. When
    # ON, the engine sets the risk engine's regime from the analyzer before each
    # evaluate(), so get_regime_adjusted_params applies the per-regime multiplier
    # (e.g. CHOP 0.5× / RANGE 0.7× reduce, TREND 1.2× / EXPANSION 1.3× increase).
    # The notional/margin cap stays the final authority, so increases can never
    # exceed it. Default ON; when disabled regime stays UNKNOWN (1.0×).
    regime_sizing_enabled: bool = _env_bool("REGIME_SIZING_ENABLED", True)
    # TREND_UP position-size multiplier override (default 0.7, DOWN from the
    # static table's 1.2x boost). Frozen-benchmark attribution repeatedly
    # showed TREND_UP as the weakest/most inconsistent regime bucket (e.g.
    # majors-only PF 0.22-0.29 vs TREND_DOWN's 1.25-1.74). A/B on both
    # benchmarks (docs/FROZEN_BENCHMARK.md): combined majors+alts +1.12% vs
    # +1.00% baseline (PF 1.67 vs 1.55); majors-only a wash on return (+0.62%
    # both) but better PF (1.39 vs 1.38) and better worst fold (-1.06% vs
    # -1.22%). No universe got worse -> enabled. 0.75-0.8 tested slightly
    # higher on the combined universe but landed on a sharp, noisy trade-
    # count discontinuity (overfit risk on a single window); 0.7 sits solidly
    # inside the improved basin. Overrides only TREND_UP; every other regime
    # keeps its static-table value.
    trend_up_size_mult: float = _env_float_bounded("TREND_UP_SIZE_MULT", 0.7, 0.1, 3.0)
    live_performance_governor_enabled: bool = _env_bool("LIVE_PERFORMANCE_GOVERNOR_ENABLED", True)
    # Rolling window of most-recent CLOSED trades the governor scores.
    live_perf_window: int = int(_env_float_bounded("LIVE_PERF_WINDOW", 20, 2, 100000))
    # Minimum closed trades before the governor acts (below this → full size).
    # Lowered 10→5 after Round-6 OOS A/B (docs/FROZEN_BENCHMARK.md): on the 2×-
    # longer v2 window the governor was blind for the first 10 closes of every
    # walk-forward fold, so the fold-3/4 drawdowns ran unchecked. At 5 the
    # governor engages a fold sooner: combined v2 OOS −1.58%→−1.42% (PF 0.23→
    # 0.25), and BYTE-IDENTICAL on the original in-sample window (+1.13%/PF 1.67
    # unchanged) — a strict OOS improvement with zero in-sample cost. Tightening
    # window/pause/reduce alongside it (min5+window10+pause35+reduce50) added no
    # further OOS gain, so only this single, realized-outcome-driven parameter
    # moved rather than an overfit 4-param combo.
    live_perf_min_samples: int = int(_env_float_bounded("LIVE_PERF_MIN_SAMPLES", 5, 1, 100000))
    # Win rate at/below which the window counts as underperforming → reduce.
    live_perf_reduce_winrate: float = _env_float_bounded("LIVE_PERF_REDUCE_WINRATE", 0.40, 0.0, 1.0)
    # Win rate at/below which (AND net-negative) the governor pauses trading.
    live_perf_pause_winrate: float = _env_float_bounded("LIVE_PERF_PAUSE_WINRATE", 0.25, 0.0, 1.0)
    # Size multiplier applied while in the reduce zone.
    live_perf_reduce_mult: float = _env_float_bounded("LIVE_PERF_REDUCE_MULT", 0.5, 0.05, 1.0)


@dataclass(frozen=True)
class ExchangeConfig:
    """Exchange venue selection, API credentials and trading mode."""
    # Live trading venue: "bitget" (default) or "hyperliquid". Applies to the
    # operator's shared executor; per-user (/connect) executors are Bitget-only.
    venue: str = _env("VENUE", "bitget")
    api_key: str = _env("BITGET_API_KEY")
    api_secret: str = _env("BITGET_API_SECRET")
    passphrase: str = _env("BITGET_PASSPHRASE")
    sandbox: bool = _env_bool("BITGET_SANDBOX", True)  # Sandbox by default; override via env
    # Hyperliquid venue credentials (USDC perps DEX): API wallet address +
    # private key. Use a dedicated API wallet (agent wallet), NOT the main
    # wallet key. HYPERLIQUID_TESTNET=true routes to the testnet.
    hyperliquid_wallet_address: str = _env("HYPERLIQUID_WALLET_ADDRESS")
    hyperliquid_private_key: str = _env("HYPERLIQUID_PRIVATE_KEY")
    hyperliquid_testnet: bool = _env_bool("HYPERLIQUID_TESTNET", False)
    # Bybit venue credentials (USDT linear perps). One-way position mode.
    bybit_api_key: str = _env("BYBIT_API_KEY")
    bybit_api_secret: str = _env("BYBIT_API_SECRET")
    # BingX venue credentials (USDT perps, $2 min order). One-way mode.
    bingx_api_key: str = _env("BINGX_API_KEY")
    bingx_api_secret: str = _env("BINGX_API_SECRET")
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
    # Exchange-minimum round-up (operator-requested). When a risk-sized order
    # falls just below the venue's minimum amount step / min-notional (common on
    # a small account meeting a high-priced asset — the XPT incident), round the
    # quantity UP to the minimum instead of skipping — BUT only when the minimum
    # is within exchange_min_roundup_max_mult of the risk-approved quantity, so a
    # "just below the step" case fills while a "triple the size to hit min
    # notional" case still skips (that would trade materially more than the risk
    # engine approved). The downstream notional-ceiling block and INSUFFICIENT
    # FUNDS classification remain as backstops. Set enabled=false to restore the
    # always-skip behaviour.
    exchange_min_roundup_enabled: bool = _env_bool("EXCHANGE_MIN_ROUNDUP_ENABLED", True)
    exchange_min_roundup_max_mult: float = _env_float_bounded("EXCHANGE_MIN_ROUNDUP_MAX_MULT", 1.5, 1.0, 10.0)
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
    # Universe expansion 2026-07-12 (catalog-verified live on Bitget):
    "QQQ/USDT:USDT",     # Invesco QQQ (Nasdaq-100) — $1.1M/day
    "SPY/USDT:USDT",     # SPDR S&P 500
    "TQQQ/USDT:USDT",    # ProShares UltraPro QQQ 3X
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
    # Universe expansion 2026-07-12 (catalog-verified live on Bitget):
    "CRCL/USDT:USDT",      # Circle — $4.7M/day, top new listing
    "ORCL/USDT:USDT",      # Oracle
    "NFLX/USDT:USDT",      # Netflix
    "OPEN/USDT:USDT",      # Opendoor
    "MCD/USDT:USDT",       # McDonald's
    "GME/USDT:USDT",       # GameStop
    "QNTSTOCK/USDT:USDT",  # Quantum Computing Inc
    "BBSTOCK/USDT:USDT",   # BlackBerry
    "STXSTOCK/USDT:USDT",  # Seagate
    "NOKSTOCK/USDT:USDT",  # Nokia
    "RTXSTOCK/USDT:USDT",  # RTX Corp
    "DIASTOCK/USDT:USDT",  # SPDR Dow Jones Industrial Average
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
    # Async request-rate cap (requests/minute) for the LLM client. Dedicated
    # per-provider RPM bound — independent of the DAILY budget (deep-audit #43).
    # The previous limiter derived RPM from daily_call_limit (≈thousands/min), so
    # it never actually throttled and 429-prevention was a no-op. 40 RPM is a safe
    # default that won't delay normal hourly-scan operation but caps real bursts.
    max_rpm: int = int(_env_float("LLM_MAX_RPM", 40))
    daily_budget_usd: float = _env_float("LLM_DAILY_BUDGET_USD", 1.0)  # fail to rules if exceeded
    est_cost_per_analysis: float = _env_float("LLM_EST_COST_PER_ANALYSIS", 0.003)  # for backtest projection
    # Account cascading-fallback LLM calls against the daily budgets (opt-in,
    # default OFF; deep-audit medium). The primary-provider path increments the
    # daily call counter and records token/dollar cost, but the cascading
    # fallback (_try_llm_fallback) makes real billable calls — including the
    # priciest provider, Anthropic Sonnet — without touching either counter. So
    # a flapping primary lets the bot silently exceed BOTH the daily call limit
    # and the daily dollar cap via fallbacks. When ON, each successful fallback
    # call increments _llm_calls_today and records cost (exact token usage on
    # OpenAI-compatible providers; a char-length estimate on the Anthropic path,
    # whose helper discards usage). This makes the budget guards trip on true
    # spend, so the bot may fall back to the rule engine sooner — the intended
    # correction. Default ON so the configured budgets actually bind on
    # live money; disable to revert to primary-path-only accounting.
    fallback_cost_accounting_enabled: bool = _env_bool("LLM_FALLBACK_COST_ACCOUNTING", True)


@dataclass(frozen=True)
class AnalyzerConfig:
    """Tunable parameters for the AI analyzer / confluence engine."""
    # Confidence-blend weights: blended = llm_conf*llm_weight + confluence*confluence_weight.
    # Now env-configurable (LLM Optimization Plan Phase 5) so the split can be
    # tuned from evidence without a code change. Defaults preserve the prior
    # hardcoded 0.6 / 0.4.
    llm_weight: float = _env_float_bounded("LLM_BLEND_WEIGHT", 0.6, 0.0, 1.0)
    confluence_weight: float = _env_float_bounded("CONFLUENCE_BLEND_WEIGHT", 0.4, 0.0, 1.0)
    # Uncalibrated-LLM weight cap (default ON; audit fix #2). The LLM drives
    # `llm_weight` (0.6) of the blended confidence, but until confidence
    # calibration is ON its confidence is unproven against realized outcomes — a
    # hallucinated or overconfident thesis flows straight into sizing. When this
    # is ON *and* calibration is OFF, the LLM's weight is capped at
    # `uncalibrated_llm_weight_cap` and the freed weight is shifted to the
    # deterministic, auditable confluence score (so the weights still sum to the
    # same total). Once calibration is enabled the cap lifts automatically, so
    # with calibration at its default (ON) this is a pure safety net.
    uncalibrated_llm_weight_cap_enabled: bool = _env_bool("UNCALIBRATED_LLM_WEIGHT_CAP_ENABLED", True)
    uncalibrated_llm_weight_cap: float = _env_float_bounded("UNCALIBRATED_LLM_WEIGHT_CAP", 0.4, 0.0, 1.0)
    # LLM direction guard (default ON; audit fix #1). The thesis (LLM) chooses
    # the trade direction, but it must not overrule a CLEAR deterministic
    # consensus unchecked: when the confluence score opposes the thesis
    # direction by >= the haircut margin (confluence 0.60+ the other way) the
    # thesis confidence is halved before blending; by >= the veto margin
    # (0.70+ the other way) the idea is rejected outright. Voters propose,
    # the LLM narrates — not the reverse. Each margin is env-tunable.
    llm_direction_guard_enabled: bool = _env_bool("LLM_DIRECTION_GUARD_ENABLED", True)
    llm_direction_haircut_margin: float = _env_float_bounded("LLM_DIRECTION_HAIRCUT_MARGIN", 0.10, 0.0, 0.5)
    llm_direction_veto_margin: float = _env_float_bounded("LLM_DIRECTION_VETO_MARGIN", 0.20, 0.0, 0.5)
    # Session-anchored VWAP (default ON). The "vwap"
    # indicator is a cumulative VWAP over the WHOLE fetched window (~100 bars,
    # anchored to bar[0]), which drifts as the window slides and is not the
    # session VWAP traders mean. The proper session VWAP — anchored to the
    # current UTC day's first bar — is always exposed as "vwap_session"; when
    # this flag is ON (the default) the "vwap" key consumers read
    # (vwap_reversion classifier, S/R candidate) is set to that session value.
    # Set VWAP_SESSION_ANCHORED=false to keep the legacy full-window value.
    vwap_session_anchored: bool = _env_bool("VWAP_SESSION_ANCHORED", True)
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
    # per_user_llm_enabled / per_user_llm_tiers_enabled is ON. Default ON;
    # disable to revert to the legacy single-namespace cache key.
    llm_cache_scoped_key: bool = _env_bool("LLM_CACHE_SCOPED_KEY", True)
    # Confidence calibration (Phase A): when ON (the default), the final blended
    # confidence is remapped through a monotonic reliability curve fitted from
    # the bot's own closed-trade history, so a confidence value reflects
    # realized win rate. When disabled the curve is computed in shadow-mode
    # (logged, not applied). See bot/learning/confidence_calibration.py.
    confidence_calibration_enabled: bool = _env_bool("CONFIDENCE_CALIBRATION_ENABLED", True)
    # Per-setup expectancy (Phase C): when ON (the default), a setup's own
    # historical win rate (symbol + regime + direction, from completed trades)
    # applies a small bounded nudge to confidence. When disabled it is computed
    # in shadow-mode (logged, not applied). See bot/learning/setup_expectancy.py.
    setup_expectancy_enabled: bool = _env_bool("SETUP_EXPECTANCY_ENABLED", True)
    # Voter-weight learning application (Phase B2): when ON, each confluence
    # voter's hand-tuned weight is multiplied by a learned, bounded ([0.5,1.5])
    # multiplier reflecting how well that voter has predicted winning trades.
    # Default OFF — until enabled, weights are byte-identical to hand-tuned.
    # See bot/learning/voter_weights.py and docs/VOTER_WEIGHT_LEARNING.md.
    voter_weight_learning_enabled: bool = _env_bool("VOTER_WEIGHT_LEARNING_ENABLED", False)
    # External sentiment: when ON (default; the final runbook stage-4 item),
    # the sentiment voter blends the live market-wide Fear & Greed index
    # (alternative.me) as a BOUNDED contrarian adjustment (max ±0.3 on one
    # 0.6-weight voter), cached 1h, fail-open to the price-derived read on any
    # fetch failure. Disable to avoid the external network call.
    external_sentiment_enabled: bool = _env_bool("EXTERNAL_SENTIMENT_ENABLED", True)
    # Funding carry-cost awareness: when ON (default; ops tip), apply a small
    # bounded confidence haircut when a trade would PAY adverse funding over
    # its expected hold (the carry-cost dimension the instantaneous funding
    # signals miss). Haircut-only: it can only ever reduce confidence, so a
    # thin edge is not silently eaten by perp funding on longer holds.
    # See bot/core/funding.py.
    funding_cost_aware_enabled: bool = _env_bool("FUNDING_COST_AWARE_ENABLED", True)
    # Learning auto-refit: when ON, the three learners (calibration, voter weights,
    # setup expectancy) are re-fitted from closed-trade history every
    # LEARNING_AUTO_REFIT_INTERVAL closed trades, so they don't go stale. Refitting
    # only updates persisted learner state — it never changes a decision unless the
    # learners' own application flags are on. Default ON. See bot/learning/auto_refit.py.
    learning_auto_refit_enabled: bool = _env_bool("LEARNING_AUTO_REFIT_ENABLED", True)
    learning_auto_refit_interval: int = int(_env_float("LEARNING_AUTO_REFIT_INTERVAL", 25))
    # Learning readiness alerting: assess hourly whether each learner has
    # enough resolved outcomes AND clears its out-of-sample bar, and push a
    # proactive alert the moment a component BECOMES ready to apply (see
    # bot/learning/readiness.py and /readiness). Assessment only — never
    # flips an application flag by itself.
    learning_readiness_alert_enabled: bool = _env_bool(
        "LEARNING_READINESS_ALERT_ENABLED", True)
    # LLM-degraded alerting: the proactive monitor warns the operator when the
    # analyzer has fallen through EVERY provider to the rule engine for N theses
    # in a row — the live "free-tier quota exhausted → bot trading brain-dead on
    # rules" signature, which was previously silent. Rule-engine-by-design (tier
    # 1, no LLM call attempted) never trips this. Streak-based so a single
    # transient 429 doesn't alert; only sustained degradation does.
    llm_degraded_alert_enabled: bool = _env_bool(
        "LLM_DEGRADED_ALERT_ENABLED", True)
    # Run the ENGINE's own autonomous analyses in ADMIN context (default ON).
    # Live incident 2026-07-11: the operator pointed every tier at their paid
    # Anthropic key, but the autonomous scan path defaulted to is_admin=False —
    # and the non-admin guard in resolve_tier_config deliberately SKIPS any
    # step resolving to Anthropic (it protects the operator's Claude key from
    # OTHER USERS), so the bot's own trading brain could never reach the paid
    # key: routing fell to the cheap default chain (Alibaba/Gemini), both
    # exhausted, and the bot ran on the rule engine. The autonomous engine IS
    # the operator's own process running the operator's keys — admin context is
    # the correct identity for it. Set OFF to restore cheap-tier-only scans.
    # NOTE: with this ON, autonomous analyses use ADMIN_TIER_ROUTING (Sonnet on
    # every tier) and spend real money — the LLM_DAILY_BUDGET_USD guard binds.
    engine_analysis_as_admin: bool = _env_bool(
        "ENGINE_ANALYSIS_AS_ADMIN", True)
    llm_degraded_alert_min_streak: int = int(_env_float_bounded(
        "LLM_DEGRADED_ALERT_MIN_STREAK", 3, 1, 1000))
    # Drop the in-progress (unclosed) candle before computing indicators/patterns.
    # Live OHLCV from the exchange includes the current forming bar as the last
    # element; reading closes[-1] on it makes every voter flicker pre-close
    # (repaint). When ON (the default), the still-forming last candle is dropped
    # before analysis so all TA uses CLOSED bars only — aligning live with the
    # (bar-closed) backtest. Entry/price logic is unaffected (it uses the live
    # ticker price, not the last candle). Setting DROP_UNCLOSED_CANDLE_ENABLED=
    # false re-enables intrabar repainting — do not do that in live trading.
    drop_unclosed_candle_enabled: bool = _env_bool("DROP_UNCLOSED_CANDLE_ENABLED", True)
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
    # Env-tunable so the high-vol widening can be A/B'd against the frozen
    # benchmark (round 4: the global swing-SL sweep showed 3.0 worse than 2.5,
    # raising the question whether this override's widening helps or hurts).
    high_vol_sl_mult: float = _env_float("HIGH_VOL_SL_MULT", 3.0)
    high_vol_tp_mult: float = _env_float("HIGH_VOL_TP_MULT", 3.8)
    low_vol_sl_mult: float = 2.0        # tighter stops in low vol
    low_vol_tp_mult: float = 3.0        # R:R = 1.5
    # Minimum stop-distance floor (safety). An ATR-derived stop can fall below a
    # sane, venue-placeable distance on low-vol or low-priced assets — Bitget then
    # rejects the conditional order (position left UNPROTECTED) or market noise
    # trips it instantly. Floor |entry - SL| to this fraction of entry, widening
    # the stop away from entry BEFORE sizing (so risk is measured on the real
    # stop). 0.4% sits well below a normal ATR stop on liquid majors, so it only
    # engages on pathologically-tight stops. 0 disables.
    min_stop_distance_pct: float = _env_float_bounded("MIN_STOP_DISTANCE_PCT", 0.004, 0.0, 0.5)
    # Skip these signal-type families entirely (comma-separated). For gating a
    # family the frozen-benchmark attribution shows is a persistent negative-edge
    # drag under the LIVE (partial-TP) exit. Evidence, not intuition — set only
    # from a benchmark A/B. Empty = trade every family (the historical behavior).
    skip_signal_types: str = _env("SKIP_SIGNAL_TYPES", "")
    # Regime-specific overrides
    range_sl_mult: float = 1.5
    range_tp_mult: float = 2.5
    range_confidence_penalty: float = 0.10
    chop_sl_mult: float = 1.5
    chop_tp_mult: float = 2.0
    chop_confidence_penalty: float = 0.15
    # Regime HARD gates (default ON; runbook stage 1). The penalties above only SOFTEN
    # the lowest-edge regimes; with this ON they become hard no-trades:
    #   - CHOP / UNKNOWN regime  -> skip the signal entirely.
    #   - Counter-trend entry in a STRONG trend (ADX >= regime_strong_adx)
    #     (SHORT in TREND_UP / LONG in TREND_DOWN) -> skip entirely.
    # Disable to restore the soft-penalty-only behaviour.
    regime_hard_gates_enabled: bool = _env_bool("REGIME_HARD_GATES_ENABLED", True)
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

    # ── Advanced Elliott Wave (bot/core/elliott.py) — now default ON at the
    # operator's request. Each toggle stays independent and env-overridable, so
    # any one can be turned back off (e.g. ELLIOTT_MTF_ENABLED=false) without
    # touching the others. All fail-open; none bypasses the risk engine.
    #   zigzag:      feed the EW detectors structural ATR-ZigZag pivots instead
    #                of the fixed 5-bar fractal (filters noise wiggles).
    #   wave_action: dampen the EW confluence vote by the wave's *position*
    #                (a terminal wave 5 / ending diagonal stops adding trend
    #                conviction) instead of voting the raw pattern signal.
    #   fib_targets: wave-anchor the stop to the invalidation level (only ever
    #                TIGHTENS) and extend the target to the Fib projection.
    #   mtf:         run wave detection on the timeframe whose degree matches the
    #                setup's strategy_type (scalp<intraday<swing<position). This
    #                one fetches extra candle timeframes per symbol — cached and
    #                fail-open, but it raises Bitget API load; set
    #                ELLIOTT_MTF_ENABLED=false to disable just that if scans
    #                start hitting rate limits.
    elliott_zigzag_enabled: bool = _env_bool("ELLIOTT_ZIGZAG_ENABLED", True)
    elliott_zigzag_atr_mult: float = _env_float("ELLIOTT_ZIGZAG_ATR_MULT", 1.5)
    elliott_wave_action_enabled: bool = _env_bool("ELLIOTT_WAVE_ACTION_ENABLED", True)
    elliott_fib_targets_enabled: bool = _env_bool("ELLIOTT_FIB_TARGETS_ENABLED", True)
    elliott_mtf_enabled: bool = _env_bool("ELLIOTT_MTF_ENABLED", True)
    #   mtf_alignment: run the wave detectors on EVERY fetched timeframe
    #                (15m/1h/4h/1d — already in memory for mtf, zero extra API
    #                calls) and add ONE bounded cross-degree agreement vote:
    #                nested with-trend structure across degrees boosts, a
    #                terminal 4h/1d wave 5 / ending diagonal halves the vote
    #                (don't chase a lower-degree entry into higher-degree
    #                exhaustion). Map exposed as indicators["elliott_mtf"].
    elliott_mtf_alignment_enabled: bool = _env_bool(
        "ELLIOTT_MTF_ALIGNMENT_ENABLED", True)

    # Multi-timeframe confluence (default ON): feed the engine's already
    # fetched 4h/1d candles into MTFConfluence so the HH/HL/BOS/CHoCH
    # structure + HTF alignment voters actually fire. Before this flag the
    # module was dead code — no caller ever supplied candles_4h/candles_1d.
    # Backtests resample the primary bars into closed 4h/1d groups for parity.
    mtf_confluence_enabled: bool = _env_bool("MTF_CONFLUENCE_ENABLED", True)
    # ATR-ZigZag swings for HH/HL/BOS/CHoCH structure (default ON): the 5-bar
    # fractal starved on 30-bar HTF windows and missed equal highs/lows; the
    # reversal-threshold ZigZag (same engine Elliott uses) resolves structure
    # on short windows and registers plateaus. Fractal fallback whenever the
    # ZigZag can't produce two swings per side.
    structure_zigzag_enabled: bool = _env_bool("STRUCTURE_ZIGZAG_ENABLED", True)

    # Level-aware SL/TP (default ON): snap the ATR stop just beyond the
    # nearest scored support/resistance (swing wicks, POC/VAH/VAL, prior-day
    # high/low, round numbers) — tighten-only — and clip the target just
    # inside an opposing wall at 50-105% of the ATR distance. See
    # bot/core/levels.py; the leverage margin-risk cap still runs after.
    level_aware_sltp_enabled: bool = _env_bool("LEVEL_AWARE_SLTP_ENABLED", True)

    # Smart-money-concept voters (default OFF — measured): fair value gaps,
    # equal-highs/lows liquidity pools and premium/discount positioning
    # (bot/core/smc.py). Production-venue re-measurement (jointly with the
    # MFI + vol-spike voters): full-period +1.44%/PF 1.53 vs baseline
    # +0.07%/1.02 — but 6-fold walk-forward 0/6 profitable, mean OOS -1.27%
    # vs baseline's -1.14%. The full-period gain doesn't survive the
    # stricter OOS standard, so these stay dark until live evidence or a
    # WF-positive configuration.
    smc_voters_enabled: bool = _env_bool("SMC_VOTERS_ENABLED", False)

    # Strategy-mode confidence floor (default ON): a SPECIFIC selected
    # mode's min_confidence (e.g. BREAKOUT 0.65, LIQUIDITY_SWEEP 0.68)
    # RAISES the per-strategy-type bar when stricter. The CONSERVATIVE
    # catch-all default is exempt — applying its bar to every uncertain
    # scan measurably collapsed trade flow. These per-mode bars existed in
    # MODE_CONFIGS since day one but were dead — nothing ever read them.
    # MEASURED default OFF: even scoped to specific modes, the per-mode bars
    # (BREAKOUT 0.65 / TURTLE 0.60) suppress exactly the Donchian-driven
    # trades the Tier 2 fixes unlocked — and those trades measured
    # PROFITABLE (floor alone cost ~1.7pp and ~13 trades on the benchmark;
    # combined with the structure trail it collapsed flow to 12 trades).
    # Production-venue re-validation is AMBIGUOUS: the clean 10-symbol arm
    # measured +0.22%/31/PF 1.07 vs baseline +0.07%/24/1.02, but a 9-symbol
    # variant of the same arm measured -0.86%/7/PF 0.34 — universe-
    # composition sensitivity, not robust benefit. Stays dark pending live
    # evidence.
    mode_min_confidence_enabled: bool = _env_bool("MODE_MIN_CONFIDENCE_ENABLED", False)

    # MFI voter (default OFF — measured): the MFI(14) indicator is always
    # computed; the VOTER ships dark. Production re-measurement (jointly
    # with SMC + vol-spike): full-period positive but walk-forward 0/6 and
    # worse than baseline — see the smc_voters_enabled note.
    mfi_voter_enabled: bool = _env_bool("MFI_VOTER_ENABLED", False)
    # Per-bar volume-spike voter upgrade (default OFF — measured): the
    # bar-level spike indicator is always computed; the voter trigger ships
    # dark on the same joint production measurement as SMC/MFI above.
    vol_spike_bar_vote_enabled: bool = _env_bool("VOL_SPIKE_BAR_VOTE_ENABLED", False)

    # Advanced VWAP (bot/core/vwap.py). Default ON at the operator's request;
    # each is env-overridable. These activate VWAP math that used to be computed
    # but unused, and match the anchor to the setup horizon:
    #   bands_vote:      fade ±1σ/±2σ VWAP-band extremes back to VWAP in
    #                    range/chop (volatility-adaptive mean reversion), and
    #                    use the ±1σ band for the vwap_reversion classifier
    #                    instead of a fixed 0.5% distance.
    #   slope_vote:      dampen an above/below-VWAP bias that fights the VWAP's
    #                    own slope (holding above a *rising* VWAP > a falling one).
    #   setup_anchoring: re-point the "vwap" consumers read to the anchor whose
    #                    horizon matches strategy_type (scalp/intraday→session,
    #                    swing→rolling-50, position→full window).
    #   anchored_pivot:  expose an AVWAP anchored at the last structural ZigZag
    #                    pivot (reuses the Elliott pivot engine) as an S/R level.
    vwap_bands_vote_enabled: bool = _env_bool("VWAP_BANDS_VOTE_ENABLED", True)
    vwap_slope_vote_enabled: bool = _env_bool("VWAP_SLOPE_VOTE_ENABLED", True)
    vwap_setup_anchoring_enabled: bool = _env_bool("VWAP_SETUP_ANCHORING_ENABLED", True)
    vwap_anchored_pivot_enabled: bool = _env_bool("VWAP_ANCHORED_PIVOT_ENABLED", True)
    # Scalp/intraday session VWAP built from the 15m series (default ON;
    # audit follow-up). A UTC-day session VWAP of <=24 HOURLY points is coarse
    # for a scalp; when mtf_candles supplies "15m", recompute vwap_session on
    # it so the session anchor scalps read has real intraday granularity.
    # Fail-open: falls back to the 1h session VWAP when 15m is absent.
    scalp_session_vwap_enabled: bool = _env_bool("SCALP_SESSION_VWAP_ENABLED", True)
    # Cross-layer confirmation bonus (default OFF — MEASURED). The family caps
    # stop the SAME concept double-counting; this rewards genuinely
    # INDEPENDENT confirmation — when >=2 distinct signal families (liquidity
    # sweep / reversal candle / structure break / order-flow aggression) agree
    # with the net confluence direction, nudge confidence a bounded amount
    # toward it (breadth the weighted average alone can't see). Ships dark
    # until it measures non-harmful on the honest benchmark.
    cross_layer_confirmation_enabled: bool = _env_bool("CROSS_LAYER_CONFIRMATION_ENABLED", False)

    # Unify /scan with the real analyzer (default ON — correctness). When set,
    # the market scanner derives each symbol's direction + score from
    # Analyzer.scan_read (the same indicators, regime, confluence electorate and
    # rule-based thesis the trade decision uses) instead of a lightweight RSI/SMA
    # heuristic that could show the OPPOSITE direction from the per-asset
    # analysis. LLM-free and side-effect-free; falls back to the heuristic on any
    # error. Set false to restore the old fast heuristic.
    scan_use_analyzer_engine: bool = _env_bool("SCAN_USE_ANALYZER_ENGINE", True)

    # Direction-aware Fibonacci (default ON; audit fix #4). The legacy fib
    # module force-fit every market into a bullish low->high retracement and
    # its voter could only lean long. When ON, the dominant leg is inferred
    # from the ORDER of the window extremes (high before low = down-leg) and a
    # down-leg gets the mirrored high->low retracement plus symmetric bearish
    # votes; the emitted "fib_trend" key tells consumers which framing applies.
    # Disable to restore the legacy bullish-only behaviour.
    fib_direction_aware_enabled: bool = _env_bool("FIB_DIRECTION_AWARE_ENABLED", True)
    # Pattern de-correlation (default ON; audit fix #5). Wyckoff / Harmonic /
    # Elliott / Fib-extension detections vote through DEDICATED voters; when ON
    # they are excluded from the aggregate chart_patterns vote so the same
    # evidence is counted once, not twice.
    pattern_dedup_enabled: bool = _env_bool("PATTERN_DEDUP_ENABLED", True)
    # Data-quality penalty (default ON; audit fix #10). Signals produced from a
    # thin window (<50 bars: SMA-50, vwap_50 and the full fib window are all
    # unavailable or shrunken) carry less confirmation; apply a small bounded
    # confidence penalty and stamp data_bars/data_thin into the indicators so
    # the gap is visible instead of silent.
    data_quality_penalty_enabled: bool = _env_bool("DATA_QUALITY_PENALTY_ENABLED", True)
    data_thin_penalty: float = _env_float_bounded("DATA_THIN_PENALTY", 0.05, 0.0, 0.5)
    # Candlestick upgrades (default ON; audit fixes #13/#14):
    #   trend context — a hammer only counts in a downtrend and a shooting star
    #     in an uptrend (pure geometry fires constantly in the wrong context);
    #     morning/evening stars additionally require the third candle to close
    #     into the first candle's body.
    #   strength vote — the candlestick confluence vote scales by pattern
    #     strength (3-candle formations > 2-candle > single) instead of a raw
    #     bull-vs-bear key count where a lone doji-adjacent hammer equalled
    #     three white soldiers.
    candle_trend_context_enabled: bool = _env_bool("CANDLE_TREND_CONTEXT_ENABLED", True)
    candle_strength_vote_enabled: bool = _env_bool("CANDLE_STRENGTH_VOTE_ENABLED", True)
    # Candle-pattern entry veto (opt-in, default OFF). When a PULLBACK LIMIT
    # entry is about to be placed and the last closed bar prints a strong
    # reversal pattern OPPOSING the trade (bearish engulfing/shooting-star/
    # gravestone-doji/bearish-marubozu for a LONG; the bullish mirror for a
    # SHORT), skip the idea — the "pullback" may be a breakdown through the
    # fill zone. Honest expectation: weak literature; expected to be a marginal
    # or negative A/B. Off = byte-identical (no idea is ever vetoed).
    candle_entry_veto_enabled: bool = _env_bool("CANDLE_ENTRY_VETO_ENABLED", False)
    # Voter dilution fix (default ON; audit fix #16). The five always-vote
    # voters (rsi/macd/bb/adx/volume_spike) appended a 0-vote even when their
    # input was missing or neutral-by-default, inflating the denominator and
    # compressing every real signal toward 0.5; sentiment did the same when the
    # engine was present but had no data. When ON, a voter with no data is
    # SKIPPED (weight not appended) rather than voting 0.
    voter_skip_missing_enabled: bool = _env_bool("VOTER_SKIP_MISSING_ENABLED", True)


@dataclass(frozen=True)
class LearningConfig:
    """Closed-loop learning adjustments.

    The orchestrator already LOGS every decision + outcome; this controls whether
    that accumulated experience is read back to nudge new-trade confidence.
    Default OFF: it changes live entry behavior, so it is opt-in. The nudge is
    small, capped, asymmetric (penalize historically-losing setups more than it
    rewards winners), additive only, and never overrides the 23 risk checks.
    """
    adaptive_confidence_enabled: bool = _env_bool("ADAPTIVE_CONFIDENCE_ENABLED", True)
    # Feed PAPER/sim closes into the learning loop's write side (opt-in, default
    # OFF; deep-audit medium). Today record_closed_outcome fires only on LIVE
    # closes, so in simulation-first operation the learners (calibration / voter
    # weights / setup expectancy) see almost no data. When ON, each paper close
    # also records an outcome tagged source="paper_outcome" (live stays
    # "live_outcome"), so similar-setup lookups and calibration accumulate from
    # the abundant paper history. The records are LABELLED so live vs paper can
    # be weighted later; for now an opted-in operator consumes them equally.
    # Default ON; disable to record live outcomes only.
    learn_from_paper_closes_enabled: bool = _env_bool("LEARN_FROM_PAPER_CLOSES", True)
    # Also log a DECISION row for per-user paper (practice) fills, so the
    # confidence-calibration and voter-weight learners — which JOIN a decision row
    # to the outcome by paper_trade_id — can train on paper history too (without
    # it, paper trades have only an outcome row and contribute nothing to those
    # two learners). Default OFF: those learners apply to LIVE confidence and the
    # admin auto-trade gate, so paper-derived calibration influencing live is an
    # explicit operator opt-in. The paper outcome write itself stays governed by
    # LEARN_FROM_PAPER_CLOSES above.
    learn_calibration_from_paper_enabled: bool = _env_bool(
        "LEARN_CALIBRATION_FROM_PAPER", False)
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
    # WS idle-stall watchdog (opt-in, default 0 = OFF; deep-audit medium). The
    # read loop blocks on `async for raw in ws`, and ping/pong keepalive only
    # detects a truly dead socket — a feed that stays pong-alive but stops
    # pushing ticker data (server-side subscription drop, half-open stall) would
    # freeze prices indefinitely with no reconnect. When >0, a watchdog forces a
    # reconnect + resubscribe (and alerts) if no WS message has arrived for this
    # many seconds while connected. 0 disables → read loop byte-identical. Set
    # comfortably above the quietest symbol's natural tick gap to avoid spurious
    # reconnects (e.g. 60–120s). RECOMMENDED ON for live money.
    ws_idle_timeout_sec: float = _env_float_bounded("WS_IDLE_TIMEOUT_SEC", 90.0, 0.0, 3600.0)
    # WS trade-tape CVD (default ON): subscribe the public trade channel and
    # maintain true aggressor-side cumulative volume delta per symbol —
    # deduped by trade id, gap-free — replacing the overlapping 200-trade
    # REST-window approximation whenever fresh tape data exists. Fail-open:
    # no fresh tape -> the REST approximation is used exactly as before.
    ws_cvd_enabled: bool = _env_bool("WS_CVD_ENABLED", True)
    # REST ticker staleness guard for the live SL/TP monitor (check_positions).
    # The WS guard above only covers the WS price path; the executor's local
    # SL/TP loop reads `last` from REST fetch_ticker, where a frozen/old value
    # (illiquidity, partial outage) could drive a false trailing tighten, a
    # premature local stop-out, or a missed breach. When a ticker's timestamp is
    # older than this many seconds, local monitoring is skipped for that symbol
    # that cycle and the exchange-side stop remains the protection. A missing
    # timestamp is NOT treated as stale (can't verify → don't disable). 0 disables.
    live_ticker_max_age_sec: float = _env_float_bounded("LIVE_TICKER_MAX_AGE_SEC", 120.0, 0.0, 3600.0)
    # Verify classic (two-order) SL/TP legs against the exchange on restart
    # (deep-audit medium; default ON). verify_and_fix_sltp re-places
    # protection when the stored SL/TP IDs are both empty or identical (v3
    # combined order), but when they are DISTINCT and present (two separate
    # classic orders) it trusts them blindly — so a leg lost while the bot was
    # offline (filled / cancelled on-venue) leaves the position half-protected
    # and is never re-placed. When ON, each distinct classic leg is checked
    # against the exchange's live orders; if one is gone, the SL/TP pair is
    # re-placed (placement cancels survivors first, so no duplicates). Default
    # ON — recommended for live money; disable to trust restored state blindly.
    verify_classic_sltp_on_restart: bool = _env_bool("VERIFY_CLASSIC_SLTP_ON_RESTART", True)
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
    # Default ON (audit fix #12) — validated on the flag_compare backtest
    # harness; disable to restore uncapped co-firing.
    family_cap_enabled: bool = _env_bool("CONFLUENCE_FAMILY_CAP_ENABLED", True)
    # Max COMBINED weight the mean-reversion oscillator family may contribute.
    # The default (2.0) is ~the single largest member (RSI at 1.5) plus a little,
    # vs. an uncapped ~4.2 when all four co-fire.
    mr_oscillator_weight_cap: float = _env_float_bounded(
        "CONFLUENCE_MR_OSC_WEIGHT_CAP", 2.0, 0.1, 100.0)
    # PATTERN family cap (audit fix #12 extension). Candlesticks, geometric
    # chart patterns, reversal bars, Wyckoff, harmonics and the four Elliott
    # voters can co-fire up to ~7 weight on one structure read; cap their
    # combined actively-voting weight the same way. Applies only when
    # family_cap_enabled is on.
    pattern_weight_cap: float = _env_float_bounded(
        "CONFLUENCE_PATTERN_WEIGHT_CAP", 2.5, 0.1, 100.0)


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
    # Structure trailing (default ON): once trailing is active (>=1R), the
    # stop also ratchets to just beyond the most recent CONFIRMED swing
    # (3-bar fractal, excluding unconfirmed recent bars) — tighten-only, on
    # top of whichever ATR rule is active. Applied identically in the
    # backtest (bar window per position) and live (closed 1h candles,
    # cached, fail-open).
    # MEASURED default OFF: on the honest 10-perp benchmark the ratchet cut
    # winners short and compounded with the mode floor (together: 44 -> 12
    # trades, +3.66% -> -0.22%; trail alone cost ~1.3pp and ~17 trades).
    # Fully built and env-flippable for re-tuning (wider fractal / later
    # activation are the obvious knobs).
    structure_trail_enabled: bool = _env_bool("STRUCTURE_TRAIL_ENABLED", False)
    structure_trail_buffer_atr: float = _env_float("STRUCTURE_TRAIL_BUFFER_ATR", 0.25)
    # Wave-anchored trailing — the structure-trail retune the measured-OFF
    # note above invited. Same tighten-only ratchet, but pivots come from the
    # ATR-normalized ZigZag (the Elliott pivot engine): a pivot registers only
    # after a >= zigzag_atr_mult*ATR reversal, so the stop trails genuine wave
    # lows/highs, not the 3-bar wiggles that cut winners short. Live trails
    # the SUB-DEGREE of the entry (swing/4h entry -> 1h sub-wave pivots);
    # the backtest applies the same pivot engine on the run timeframe.
    # Takes precedence over structure_trail when both are on. Buffer reuses
    # structure_trail_buffer_atr. Default set by the frozen-benchmark A/B
    # (see the wave-trail PR); env-flippable either way.
    wave_trail_enabled: bool = _env_bool("WAVE_TRAIL_ENABLED", True)
    wave_trail_zigzag_atr_mult: float = _env_float("WAVE_TRAIL_ZIGZAG_ATR_MULT", 1.5)


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
    # Model/route take-profits as MAKER (post-only limit) instead of taker market.
    # Default OFF. In the backtest this charges the TP-exit leg at maker_fee_pct
    # instead of commission_pct (quantifies the fee saving); the live executor
    # wiring (resting reduce-only limit TPs) is a separate, carefully-scoped change
    # — this flag alone does NOT alter live order placement. Entry legs and non-TP
    # exits (SL/trailing/time) are unchanged. Byte-identical to today when OFF.
    maker_take_profit_enabled: bool = _env_bool("MAKER_TAKE_PROFIT_ENABLED", False)


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
    # limit, VWAP-reversion done/failed, volume-signal decay). Default ON
    # (runbook stage 1): without it a live position whose thesis has
    # invalidated rides all the way to its exchange stop-loss. Paper positions
    # already auto-close on these triggers in _check_paper_positions; this
    # extends the SAME checks to real positions, closing at market via the
    # executor. Set TIME_STOP_LIVE_AUTO_CLOSE=false to let stops do all exits.
    live_auto_close_enabled: bool = _env_bool("TIME_STOP_LIVE_AUTO_CLOSE", True)


@dataclass(frozen=True)
class StrategyTypeConfig:
    """Per-strategy-type SL/TP/trailing/time-stop overrides.

    Each strategy type has its own risk parameters:
    - scalp:     tight stops, fast exit, no trailing, 30 min time-stop
    - intraday:  moderate stops, trailing after 1R, 4h time-stop
    - swing:     wide stops, trailing after 1R, 24h time-stop
    - position:  widest stops, trailing after 1.5R, 72h time-stop
    """
    # ── SCALP (hold: 5 min - 2h) ──
    scalp_sl_atr_mult: float = _env_float("SCALP_SL_ATR_MULT", 1.5)
    scalp_tp_atr_mult: float = _env_float("SCALP_TP_ATR_MULT", 2.0)
    scalp_trailing_enabled: bool = _env_bool("SCALP_TRAILING_ENABLED", False)
    scalp_trailing_atr_mult: float = _env_float("SCALP_TRAILING_ATR_MULT", 1.0)
    scalp_time_close_hours: float = _env_float("SCALP_TIME_CLOSE_H", 2.0)
    scalp_time_warn_hours: float = _env_float("SCALP_TIME_WARN_H", 1.0)

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

    # Max NOTIONAL (margin) cap as % of equity, per type (#47). The per-type risk
    # budget above shapes size by stop distance, but the notional cap was a single
    # global value (RiskLimits.max_position_pct), which washed the per-type budget
    # back out. These let a scalp ride a tighter notional ceiling than a position
    # trade. Only consulted when RiskLimits.per_strategy_notional_cap_enabled is on.
    scalp_max_position_pct: float = _env_float("SCALP_MAX_POSITION_PCT", 8.0)
    intraday_max_position_pct: float = _env_float("INTRADAY_MAX_POSITION_PCT", 10.0)
    swing_max_position_pct: float = _env_float("SWING_MAX_POSITION_PCT", 13.0)
    position_max_position_pct: float = _env_float("POSITION_MAX_POSITION_PCT", 15.0)

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

    def get_max_position_pct(self, strategy_type: str, default: float) -> float:
        """Per-type notional (margin) cap as % of equity (#47). Unknown types fall
        back to ``default`` (the caller passes the global RiskLimits.max_position_pct
        so unmapped strategies are unchanged)."""
        return getattr(self, f"{strategy_type}_max_position_pct", default)

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
class MonitoringConfig:
    """External liveness monitoring (ops)."""
    # Dead-man's-switch ping (ops tip). When set to a monitor URL (e.g. a
    # healthchecks.io check), the engine GETs it after each successful tick,
    # throttled to the interval below — so a DEAD process or stalled tick loop
    # raises an external alarm at the monitor's grace timeout, the one failure
    # mode Telegram alerting can never report. Empty (the default) = disabled.
    # Fail-open: ping failures never affect trading.
    healthcheck_ping_url: str = _env("HEALTHCHECK_PING_URL", "")
    healthcheck_ping_interval_sec: float = _env_float_bounded(
        "HEALTHCHECK_PING_INTERVAL_SEC", 60.0, 5.0, 3600.0)


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
    # waiting for a human button press. OPERATOR-ACTIVATED default 0.85 (the admin
    # auto-trade bar); set to 1.0 to DISABLE and require manual confirm. Range: 0-1.
    # SECURITY (RC-AUD-002): auto-confirm bypasses the human-decision gate. The
    # live-execution path is still fail-closed behind AUTO_CONFIRM_LIVE_ENABLED
    # below; set that to 0 (or threshold to 1.0) to restore manual confirmation.
    auto_confirm_threshold: float = _env_float("AUTO_CONFIRM_THRESHOLD", 0.85)
    # Allow auto-confirm to place LIVE (real-money) orders with no human press.
    # OPERATOR-ACTIVATED default ON. Set AUTO_CONFIRM_LIVE_ENABLED=0 to require a
    # human tap for every live trade (the fail-closed posture).
    auto_confirm_live_enabled: bool = _env_bool("AUTO_CONFIRM_LIVE_ENABLED", True)
    # Gate auto-confirm on CALIBRATED confidence (OPERATOR-ACTIVATED default ON).
    # When ON AND a fitted confidence calibrator exists, the auto-confirm threshold
    # is tested against min(raw, calibrated) confidence — so a real-money auto-trade
    # requires BOTH the raw blend AND the measured (calibrated) win-rate to clear
    # the bar. This can only TIGHTEN auto-confirm, never loosen it: with no
    # calibration data the calibrator is identity, so it is a no-op until evidence
    # shows the raw confidence is over-optimistic. Makes the 0.85 admin auto-trade
    # mean "~85% realized win rate", not a raw LLM+voter blend.
    auto_confirm_use_calibrated: bool = _env_bool("AUTO_CONFIRM_USE_CALIBRATED", True)
    # TTL for pending ideas in seconds (default 300 = 5 min)
    pending_idea_ttl: int = int(_env_float("PENDING_IDEA_TTL", 300))

    # -- Paper trading --
    paper_balance_usd: float = _env_float("PAPER_BALANCE_USD", 10_000.0)
    portfolio_state_file: str = _env("PORTFOLIO_STATE_FILE", "data/portfolio_state.json")
    # Proactive-alert watch list persistence. The set of chats with /watch on is
    # saved here so it survives restarts — previously it was in-memory only, so
    # every deploy/watchdog restart SILENCED CRITICAL safety alerts (position
    # unprotected, circuit-breaker) until someone re-ran /watch on.
    proactive_watch_state_file: str = _env("PROACTIVE_WATCH_STATE_FILE", "data/proactive_watch.json")
    # Auto-enroll the operator chat (TELEGRAM_CHAT_ID) into proactive alerts when
    # the persisted watch list is empty (fresh deploy), so safety alerts always
    # reach the operator by default. The operator can /watch off (which persists).
    proactive_auto_enroll_admin: bool = _env_bool("PROACTIVE_AUTO_ENROLL_ADMIN", True)
    # Per-user PAPER (sim) opt-in. When enabled (default OFF), a user who has
    # opted in via /paper has THEIR confirmed trades SIMULATED into their paper
    # portfolio instead of sent to the exchange — risk-free practice on a live
    # bot. This NEVER affects other users or the live execution path: the opt-in
    # branch runs before any exchange call. Default OFF = byte-identical to today.
    paper_sim_opt_in_enabled: bool = _env_bool("PAPER_SIM_OPT_IN_ENABLED", False)

    # -- Scan settings --
    scan_interval_seconds: int = int(_env_float("SCAN_INTERVAL", 60))
    # Minimum 24h quote volume (USD) for a symbol to enter the scan universe.
    # Compared against ccxt `quoteVolume` (USDT ≈ USD) in _process_ticker.
    #
    # The crypto floor was raised 50_000 -> 1_500_000 after the live-log
    # diagnosis: at $50k, thin-book meme coins entered the universe, wasted
    # analysis + scarce free-tier LLM quota, and emitted ideas that could never
    # clear the execution liquidity guard on this account. $1.5M keeps the
    # liquid majors + established alts and drops the untradeable long tail. It
    # is a cheap volume proxy for tradeability; the exact order-book depth check
    # still runs at execution. Lower it (e.g. 50_000) to widen the universe, or
    # raise it further to trade only the deepest books. TradFi perps trade
    # thinner, so their floor stays lower ($5k unchanged).
    min_crypto_volume_usd: float = _env_float_bounded("MIN_CRYPTO_VOLUME_USD", 1_500_000, 0, 1e12)
    min_tradfi_volume_usd: float = _env_float_bounded("MIN_TRADFI_VOLUME_USD", 5_000, 0, 1e12)
    # Which market's 24h volume gates the CRYPTO universe in all_markets:
    # "futures" (default) measures the USDT-FUTURES perp — the market this
    # bot actually trades — and admits perp-only listings (no spot pair).
    # "spot" restores the legacy behavior: spot volume gating + spot-listing
    # requirement. The floor value above applies to whichever source is set.
    scan_volume_source: str = _env("SCAN_VOLUME_SOURCE", "futures")
    # Per-class TradFi toggles for the all_markets scan — evidence-driven
    # (live /classpf 2026-07-12, 292 closed trades): Commodity PF 2.30 (32
    # trades) and Stock PF 1.23 (18) earn their slots; Pre-IPO PF 0.24 (10)
    # on $40–100k/day books is spread-bleed and ships DISABLED. Metals and
    # ETFs stay ON — their samples (9 / 7 trades) are too small to condemn;
    # /classpf keeps scoring them. Explicit single-category universes
    # (ASSET_UNIVERSE=pre_ipo etc.) bypass these — an operator asking for a
    # class by name gets it.
    scan_class_commodities: bool = _env_bool("SCAN_CLASS_COMMODITIES", True)
    scan_class_stocks: bool = _env_bool("SCAN_CLASS_STOCKS", True)
    scan_class_metals: bool = _env_bool("SCAN_CLASS_METALS", True)
    scan_class_etfs: bool = _env_bool("SCAN_CLASS_ETFS", True)
    scan_class_pre_ipo: bool = _env_bool("SCAN_CLASS_PRE_IPO", False)
    # Venue-native discovery: when the active trading venue is NOT Bitget,
    # also scan that venue's own catalog for markets Bitget lacks (on
    # Hyperliquid: the XYZ- builder perps — WTI, S&P 500, gold, natgas —
    # plus HL-only crypto). No-op while trading on Bitget.
    scan_venue_native_markets: bool = _env_bool("SCAN_VENUE_NATIVE_MARKETS", True)
    # Catalog watch: diff the exchange futures catalog each scan cycle and
    # alert the operator on new listings. New crypto / *STOCK / HL-builder
    # perps already enter the universe automatically — this adds visibility,
    # especially for bare-ticker TradFi listings that classify as Crypto
    # until a config entry names them.
    catalog_watch_enabled: bool = _env_bool("CATALOG_WATCH_ENABLED", True)
    # How many (volume-filtered) symbols the scanner emits for analysis each
    # cycle. Raised 80 -> 200 for a wide sweep of the whole liquid universe; the
    # analysis loop bounds concurrency (scan_analysis_concurrency) so a wider
    # universe can't fan out hundreds of simultaneous exchange calls. Lower it
    # (TOP_MOVERS_COUNT) to trim always-on cost/latency.
    top_movers_count: int = int(_env_float("TOP_MOVERS_COUNT", 200))
    # Max symbols analyzed concurrently per scan (semaphore bound over the
    # OHLCV + order-flow + MTF + analyzer work). Keeps a wide universe from
    # overwhelming the exchange rate limiter / event loop.
    scan_analysis_concurrency: int = int(_env_float("SCAN_ANALYSIS_CONCURRENCY", 12))
    # How many symbols an INTERACTIVE force-scan analyzes (the "Latest Signal"
    # button). The full ~200 universe is analyzed by the background loop, but a
    # button tap must stay responsive: each analyzed symbol fires ~9 rate-limited
    # exchange calls, so analyzing 200 inline hangs the handler for minutes. Cap
    # the interactive path to the top-N ranked signals.
    interactive_scan_count: int = int(_env_float("INTERACTIVE_SCAN_COUNT", 40))
    # Hard timeout (seconds) on an interactive force-scan so the Telegram handler
    # can never hang unbounded; on timeout we show whatever pending ideas exist.
    interactive_scan_timeout_sec: int = int(_env_float("INTERACTIVE_SCAN_TIMEOUT_SEC", 45))
    # All-markets slot allocation for the non-Crypto (TradFi) categories.
    # When full-coverage is ON (default), EVERY present TradFi perp (metals,
    # stocks, ETFs, commodities, pre-IPO) is guaranteed a scan slot — the whole
    # curated TradFi universe (~32 symbols) is reserved before the crypto
    # priority list fills the rest. Turn it off to fall back to the per-category
    # minimum below. These only affect ASSET_UNIVERSE=all_markets.
    scan_tradfi_full_coverage: bool = _env_bool("SCAN_TRADFI_FULL_COVERAGE", True)
    scan_min_per_category: int = int(_env_float("SCAN_MIN_PER_CATEGORY", 2))

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
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)

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
        # Runtime override for the LIVE max-drawdown backstop. None => use the
        # configured CONFIG.risk.live_max_drawdown_pct. Set by an admin to
        # temporarily loosen (or tighten) the live drawdown cap WITHOUT a
        # redeploy — e.g. to keep testing live after the account has drawn down
        # past the default cap. Bounded hard in the setter so it can never be
        # disabled outright. Only consulted on LIVE; paper/backtest ignore it.
        self._live_drawdown_override_pct: float | None = None

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

    # Hard bounds for the live drawdown override. The ceiling is a real
    # backstop: no operator command can push the live drawdown cap past this,
    # so the account can never be left with the drawdown breaker effectively
    # disabled. The floor allows tightening below the default if desired.
    LIVE_DRAWDOWN_OVERRIDE_MIN = 1.0
    LIVE_DRAWDOWN_OVERRIDE_MAX = 30.0

    @property
    def live_drawdown_override_pct(self) -> float | None:
        with self._lock:
            return self._live_drawdown_override_pct

    @live_drawdown_override_pct.setter
    def live_drawdown_override_pct(self, value: float | None) -> None:
        with self._lock:
            if value is None:
                self._live_drawdown_override_pct = None
                return
            self._live_drawdown_override_pct = max(
                self.LIVE_DRAWDOWN_OVERRIDE_MIN,
                min(self.LIVE_DRAWDOWN_OVERRIDE_MAX, float(value)))

    def clear_live_drawdown_override(self) -> None:
        """Revert the live drawdown cap to the configured default."""
        with self._lock:
            self._live_drawdown_override_pct = None


RUNTIME = RuntimeState()
