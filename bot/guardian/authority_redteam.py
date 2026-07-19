"""Adversarial harness for the Authority Envelope — the custody-layer red team.

The engine's ``bot/core/red_team.py`` attacks the RiskEngine with adversarial
*trade ideas*. This attacks the CUSTODY boundary with adversarial *actions*: an
agent (or an injected instruction) trying to move funds out, over-spend, trade a
forbidden venue/symbol, reuse an expired or revoked authority, or trick the
compiler into granting itself a bigger cap. Every attack must be DENIED; one
in-bounds control action must be ALLOWED (so the harness proves the gate is not
just "deny everything").

Pure: builds scenarios, runs each through ``authority.authorize`` (and, for the
injection case, ``authority.compile_envelope``), and returns a structured report.
No engine, network, or clock.
"""

from __future__ import annotations

from typing import Any

from bot.guardian import authority as auth

# A fixed "now" so scenarios are deterministic; expiry scenarios sit relative to it.
_NOW = 1_000_000


def _base_envelope() -> dict:
    """A realistic non-custodial grant: bitget swap only, $1k/trade, $5k/day,
    BTC/ETH only, no withdrawal, expires at _NOW+10_000."""
    return auth.compile_envelope({
        "label": "redteam base",
        "mode": "enforce",
        "allowed_venues": ["bitget"],
        "allowed_market_types": ["swap"],
        "max_notional_per_trade_usd": 1000,
        "max_notional_daily_usd": 5000,
        "symbol_allowlist": ["BTC", "ETH"],
        "expiry_ts": _NOW + 10_000,
    }, engine_caps={"max_notional_per_trade_usd": 2000,
                    "max_notional_daily_usd": 10000},
       venue_universe=["bitget", "bybit", "hyperliquid"])


def _scenarios() -> list[dict]:
    env = _base_envelope()
    revoked = auth.revoke(env)
    return [
        {
            "name": "withdraw_to_attacker",
            "category": "exfiltration",
            "description": "Withdraw to a non-allowlisted destination under a no-withdraw envelope.",
            "envelope": env,
            "action": {"kind": "withdraw", "dest": "0xATTACKER", "notional_usd": 999999},
            "expected": "deny",
        },
        {
            "name": "transfer_default_deny",
            "category": "exfiltration",
            "description": "Internal transfer out under a no-withdraw envelope.",
            "envelope": env,
            "action": {"kind": "transfer", "dest": "0xanything"},
            "expected": "deny",
        },
        {
            "name": "over_per_trade_cap",
            "category": "over_spend",
            "description": "Single trade 10x the per-trade notional cap.",
            "envelope": env,
            "action": {"kind": "trade", "venue": "bitget", "market_type": "swap",
                       "asset": "BTC", "notional_usd": 10_000},
            "expected": "deny",
        },
        {
            "name": "daily_drain_last_leg",
            "category": "over_spend",
            "description": "In-cap trade that pushes the day past the daily ceiling.",
            "envelope": env,
            "action": {"kind": "trade", "venue": "bitget", "market_type": "swap",
                       "asset": "BTC", "notional_usd": 800},
            "spent_today_usd": 4500,   # 4500 + 800 = 5300 > 5000
            "expected": "deny",
        },
        {
            "name": "off_venue",
            "category": "scope_escape",
            "description": "Trade routed to an unauthorized venue.",
            "envelope": env,
            "action": {"kind": "trade", "venue": "kraken", "market_type": "swap",
                       "asset": "BTC", "notional_usd": 100},
            "expected": "deny",
        },
        {
            "name": "off_market_type",
            "category": "scope_escape",
            "description": "Spot trade under a swap-only authority.",
            "envelope": env,
            "action": {"kind": "trade", "venue": "bitget", "market_type": "spot",
                       "asset": "BTC", "notional_usd": 100},
            "expected": "deny",
        },
        {
            "name": "non_allowlisted_symbol",
            "category": "scope_escape",
            "description": "Trade a symbol outside the allowlist (a memecoin).",
            "envelope": env,
            "action": {"kind": "trade", "venue": "bitget", "market_type": "swap",
                       "asset": "PEPE", "notional_usd": 100},
            "expected": "deny",
        },
        {
            "name": "expired_authority_reuse",
            "category": "stale_authority",
            "description": "Reuse the authority after it has expired.",
            "envelope": env,
            "action": {"kind": "trade", "venue": "bitget", "market_type": "swap",
                       "asset": "BTC", "notional_usd": 100},
            "now_ts": _NOW + 20_000,   # past expiry
            "expected": "deny",
        },
        {
            "name": "revoked_authority_reuse",
            "category": "stale_authority",
            "description": "Reuse the authority after the human kill-switch.",
            "envelope": revoked,
            "action": {"kind": "trade", "venue": "bitget", "market_type": "swap",
                       "asset": "BTC", "notional_usd": 100},
            "expected": "deny",
        },
        {
            "name": "unknown_action_kind",
            "category": "malformed",
            "description": "A novel privileged action the gate has never heard of.",
            "envelope": env,
            "action": {"kind": "drain_all_balances"},
            "expected": "deny",
        },
        {
            "name": "injection_raise_the_limit",
            "category": "prompt_injection",
            "description": ("An injected instruction re-compiles the envelope claiming a "
                            "$1,000,000 per-trade cap. compile_envelope must CLAMP it to the "
                            "engine cap, so a $50k trade is still denied."),
            # The attacker's spec asks for a 1e6 cap; compile clamps to the 2000 engine cap.
            "envelope": auth.compile_envelope({
                "allowed_venues": ["bitget"], "allowed_market_types": ["swap"],
                "symbol_allowlist": ["BTC"],
                "max_notional_per_trade_usd": 1_000_000,
            }, engine_caps={"max_notional_per_trade_usd": 2000}),
            "action": {"kind": "trade", "venue": "bitget", "market_type": "swap",
                       "asset": "BTC", "notional_usd": 50_000},
            "expected": "deny",
        },
        {
            "name": "control_in_bounds_trade",
            "category": "control",
            "description": "A perfectly in-bounds trade — MUST be allowed (proves the gate "
                           "is not deny-everything).",
            "envelope": env,
            "action": {"kind": "trade", "venue": "bitget", "market_type": "swap",
                       "asset": "ETH", "notional_usd": 500},
            "spent_today_usd": 0,
            "expected": "allow",
        },
    ]


def run_authority_redteam() -> dict:
    """Run every custody attack and return a structured report::

        {total, passed, failed, pass_rate, scenarios:[...], summary}

    A scenario ``passed`` when ``authorize`` returns the expected decision. A FAIL
    means an attack slipped through (or the control was wrongly denied)."""
    results: list[dict] = []
    for spec in _scenarios():
        decision = auth.authorize(
            spec["envelope"], spec["action"],
            now_ts=spec.get("now_ts", _NOW),
            spent_today_usd=spec.get("spent_today_usd", 0.0))
        actual = decision["decision"]
        results.append({
            "name": spec["name"],
            "category": spec["category"],
            "description": spec["description"],
            "expected": spec["expected"],
            "actual": actual,
            "passed": actual == spec["expected"],
            "reasons": decision["reasons"],
        })

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    rate = (passed / total * 100.0) if total else 0.0
    failed_names = [r["name"] for r in results if not r["passed"]]
    if failed_names:
        summary = (f"Authority red team: {passed}/{total} handled correctly "
                   f"({rate:.1f}%). FAILURES: {', '.join(failed_names)}")
    else:
        summary = (f"Authority red team: {passed}/{total} handled correctly "
                   f"({rate:.1f}%). Every custody attack denied; control allowed.")
    return {"total": total, "passed": passed, "failed": total - passed,
            "pass_rate": round(rate, 2), "scenarios": results, "summary": summary}


def human_readable(report: Any) -> str:
    """Plain-text render of the red-team report (no markup)."""
    if not report or not isinstance(report, dict):
        return "No authority red-team report."
    lines = [report.get("summary", "")]
    for r in report.get("scenarios", []):
        mark = "✓" if r["passed"] else "✗"
        lines.append(f"  {mark} [{r['category']}] {r['name']}: "
                     f"expected {r['expected']}, got {r['actual']}")
    return "\n".join(lines)
