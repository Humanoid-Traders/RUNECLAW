"""
Boot-health helpers — make an env-wipe LOUD and keep the Telegram poller ALIVE.

Two failure modes bit the platform and both were silent:

  1. A redeploy that wiped .env brought the process up MISSING its secrets. The
     bot exited on the first missing var it happened to check, hiding the rest —
     so recovery was a guessing game. ``env_preflight`` names EVERY missing
     critical/important var at once, in one loud line.

  2. The engine has a supervise-and-restart loop, but the Telegram updater did
     not: if polling stalled (a 409 getUpdates conflict from two instances
     overlapping on a redeploy, a network blip, a transient Telegram error) the
     bot went silent and never recovered until a full restart.
     ``poller_should_restart`` is the pure predicate the watchdog ticks on.

Everything here is a PURE function of its inputs (no os.environ, no telegram
imports) so the boot path stays unit-testable and the wiring in main.py is a
thin shell.
"""

from __future__ import annotations

from typing import Iterable, Mapping

# Without this the bot cannot start at all — Telegram never connects.
CRITICAL_ENV: tuple[str, ...] = ("TELEGRAM_BOT_TOKEN",)

# The bot still trades without these, but a whole surface silently breaks:
#   BOT_SYNC_SECRET     — the website's bot⇄web bridge (dashboard sync rejected)
#   WEB_GATEWAY_SECRET  — web chat + web trade (routes 503)
#   WEB_CREDS_KEY       — decrypting stored per-user exchange keys
#   DASHBOARD_TOKEN     — the aggregate /api/* dashboard gate (fail-closed 403)
IMPORTANT_ENV: tuple[str, ...] = (
    "BOT_SYNC_SECRET", "WEB_GATEWAY_SECRET", "WEB_CREDS_KEY", "DASHBOARD_TOKEN",
)


def missing_env(names: Iterable[str], env: Mapping[str, str]) -> list[str]:
    """Names from ``names`` that are absent or blank in ``env`` (order-preserving)."""
    return [n for n in names if not str(env.get(n, "")).strip()]


def env_preflight(env: Mapping[str, str]) -> dict[str, list[str]]:
    """Classify the environment once, loudly. Returns
    ``{"critical": [...], "important": [...]}`` — the missing names in each tier.

    Pure: the caller decides whether a missing critical var is fatal (telegram
    mode) or merely logged (other modes), and does the logging."""
    return {
        "critical": missing_env(CRITICAL_ENV, env),
        "important": missing_env(IMPORTANT_ENV, env),
    }


def format_preflight(report: Mapping[str, list[str]]) -> str:
    """One human line summarizing a preflight report, for a boot log."""
    crit = report.get("critical") or []
    imp = report.get("important") or []
    if not crit and not imp:
        return "env preflight: all critical and important secrets present."
    parts = []
    if crit:
        parts.append("MISSING CRITICAL (bot cannot run): " + ", ".join(crit))
    if imp:
        parts.append("missing important (a web surface will be degraded): "
                     + ", ".join(imp))
    return "env preflight — " + " | ".join(parts)


def poller_should_restart(running: bool, stopping: bool) -> bool:
    """True when the Telegram updater is NOT running yet we are not shutting
    down — i.e. polling stalled and the watchdog must revive it. Never restart
    during an intentional shutdown (that would fight ``updater.stop()``)."""
    return (not running) and (not stopping)
