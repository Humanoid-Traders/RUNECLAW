"""Deep-audit opt-in flag status — which staged fixes are currently ON/OFF.

Backs the ``/flags`` Telegram command and the activation runbook
(``docs/FLAG_ACTIVATION.md``). Reads the EFFECTIVE configuration (CONFIG plus
env for the order-flow / chart-pattern flags that live on their own dataclasses)
so the operator can see, at a glance, which improvements are live and which are
still staged behind a flag.

Most deep-audit fixes ship gated default-OFF so live behaviour never changes
without the operator's say-so; this surfaces that state instead of leaving it
buried in env vars.
"""

from __future__ import annotations

import os

from bot.config import CONFIG


def _env_on(key: str) -> bool:
    """Mirror the order-flow / chart-pattern modules' _env_bool semantics."""
    return (os.getenv(key) or "").strip().lower() in ("1", "true", "yes", "on")


def _attr(section: str, name: str, default=False):
    sec = getattr(CONFIG, section, None)
    return getattr(sec, name, default) if sec is not None else default


def _pos(section: str, name: str) -> bool:
    """A bounded float guard is 'on' when > 0 (0 disables it)."""
    try:
        return float(_attr(section, name, 0) or 0) > 0
    except (TypeError, ValueError):
        return False


# group title -> list of (env_var, label, is_on)
def audit_flag_report() -> list[tuple[str, list[tuple[str, str, bool]]]]:
    """Structured ON/OFF status of every deep-audit gated flag, grouped by how
    safe it is to enable. Pure read — never mutates config."""
    return [
        ("Active by default (no flag needed)", [
            ("LIVE_TICKER_MAX_AGE_SEC", "REST ticker staleness guard",
             _pos("execution", "live_ticker_max_age_sec")),
            ("WS_MAX_TICK_AGE_SEC", "WS tick-age staleness guard",
             _pos("execution", "ws_max_tick_age_sec")),
        ]),
        ("Safety / observability — recommended ON", [
            ("WS_IDLE_TIMEOUT_SEC", "WS idle-stall watchdog (set seconds, e.g. 90)",
             _pos("execution", "ws_idle_timeout_sec")),
            ("VERIFY_CLASSIC_SLTP_ON_RESTART", "Re-place a lost SL/TP leg on restart",
             bool(_attr("execution", "verify_classic_sltp_on_restart"))),
            ("LLM_FALLBACK_COST_ACCOUNTING", "Count fallback LLM calls vs daily budgets",
             bool(_attr("llm", "fallback_cost_accounting_enabled"))),
            ("OF_GUARD_TOP_DEPTH_ENABLED", "Top-of-book executable-depth guard",
             _env_on("OF_GUARD_TOP_DEPTH_ENABLED")),
            ("LLM_CACHE_SCOPED_KEY", "Per-model/tier LLM cache key (multi-user)",
             bool(_attr("analyzer", "llm_cache_scoped_key"))),
        ]),
        ("Signal-changing — backtest before enabling on live money", [
            ("OF_FUNDING_VOTE_FIXED_SCALE", "Funding confluence vote (real scale)",
             _env_on("OF_FUNDING_VOTE_FIXED_SCALE")),
            ("VWAP_SESSION_ANCHORED", "Session-anchored VWAP for vwap voters",
             bool(_attr("analyzer", "vwap_session_anchored"))),
            ("LEADING_DIAGONAL_PRETREND_FIX", "Leading-diagonal pre-trend window",
             _env_on("LEADING_DIAGONAL_PRETREND_FIX")),
            ("LIQUIDITY_SWEEP_OWN_CLOSE", "Liquidity-sweep own-close check",
             _env_on("LIQUIDITY_SWEEP_OWN_CLOSE")),
            ("OF_TIME_BARS_ENABLED", "Taker 3-bar gate time-awareness",
             _env_on("OF_TIME_BARS_ENABLED")),
        ]),
        ("Learning — enable the write first, apply once history builds", [
            ("LEARN_FROM_PAPER_CLOSES", "Feed paper/sim closes to the learners",
             bool(_attr("learning", "learn_from_paper_closes_enabled"))),
            ("SETUP_EXPECTANCY_ENABLED", "Apply the per-setup expectancy nudge",
             bool(_attr("analyzer", "setup_expectancy_enabled"))),
            ("CONFIDENCE_CALIBRATION_ENABLED", "Apply confidence calibration",
             bool(_attr("analyzer", "confidence_calibration_enabled"))),
            ("ADAPTIVE_CONFIDENCE_ENABLED", "Apply the adaptive-confidence nudge",
             bool(_attr("learning", "adaptive_confidence_enabled"))),
            ("LEARNING_AUTO_REFIT_ENABLED", "Auto-refit learners on closed trades",
             bool(_attr("analyzer", "learning_auto_refit_enabled"))),
        ]),
        ("Judgment calls (operator decision)", [
            ("DAILY_LOSS_BREAKER_AUTORESET", "Auto-reset daily-loss breaker at day rollover",
             bool(_attr("risk", "daily_loss_breaker_autoreset_enabled"))),
            ("DROP_UNCLOSED_CANDLE_ENABLED", "Drop the still-forming candle (repaint fix)",
             bool(_attr("analyzer", "drop_unclosed_candle_enabled"))),
            ("REGIME_SIZING_ENABLED", "Regime→sizing bridge (also fills _current_regime)",
             bool(_attr("risk", "regime_sizing_enabled"))),
        ]),
    ]


def format_flag_report(report=None) -> str:
    """Render the report as a Telegram-friendly HTML string."""
    report = report if report is not None else audit_flag_report()
    on_total = sum(1 for _, items in report for _, _, on in items if on)
    total = sum(len(items) for _, items in report)
    lines = ["\U0001f6a9 <b>Deep-audit flags</b> "
             f"({on_total}/{total} ON)", "─" * 28]
    for title, items in report:
        lines.append(f"\n<b>{title}</b>")
        for env, label, on in items:
            mark = "✅" if on else "⬜"
            lines.append(f"{mark} <code>{env}</code> — {label}")
    lines.append("\n<i>Set a flag in your .env and restart. "
                 "See docs/FLAG_ACTIVATION.md.</i>")
    return "\n".join(lines)
