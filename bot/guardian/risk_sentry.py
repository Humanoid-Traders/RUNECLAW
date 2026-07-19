"""Per-user risk sentry — proactive watch over a user's live posture.

Where the Authority Envelope AUTHORIZES a new order at confirm time, the sentry
WATCHES the standing book and warns when the current state drifts toward
trouble: a position that no longer fits the envelope, an oversized position,
concentration in one asset, stacked correlated risk, or a 24h spend nearing the
cap. It makes the envelope *proactive*, not just a gate.

Pure, deterministic, DETECTION-ONLY — it emits ranked alerts, never places,
closes, or resizes anything (that stays the user's confirm-gated action). No
network, no LLM: same posture → same alerts, and every number is verifiable.
"""

from __future__ import annotations

from typing import Any, Optional

# Alert severities, low→high, so the worst can be picked deterministically.
_ORDER = {"info": 0, "caution": 1, "warn": 2}

# Correlated majors — a stacked same-side book across these is one bet, not two.
_CORRELATED = frozenset({"BTC", "ETH", "SOL", "BNB", "XRP", "AVAX", "MATIC", "POL"})


def _f(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def _base(sym: Any) -> str:
    return str(sym or "").split("/")[0].split(":")[0].upper().strip()


def _side(v: Any) -> str:
    s = str(v or "").lower()
    if s in ("long", "buy"):
        return "long"
    if s in ("short", "sell"):
        return "short"
    return ""


def assess(positions: list[dict], *,
           envelope: Optional[dict] = None,
           equity_usd: Optional[float] = None,
           spent_today_usd: float = 0.0,
           daily_cap: Optional[float] = None,
           concentration_pct: float = 40.0) -> dict:
    """Assess a user's standing posture. Returns
    ``{alerts:[{level,category,symbol?,msg}], count, worst_level, gross_usd}``.

    ``positions``: ``[{symbol, side, notional_usd}]`` (open positions).
    ``envelope``: the user's compiled Authority Envelope (or None).
    """
    alerts: list[dict] = []
    norm: list[dict] = []
    for p in (positions or []):
        n = _f(p.get("notional_usd"))
        base = _base(p.get("symbol"))
        if not base or n is None or n <= 0:
            continue
        norm.append({"symbol": base, "side": _side(p.get("side")), "notional_usd": n})

    gross = round(sum(p["notional_usd"] for p in norm), 2)

    # ── Envelope drift: does the STANDING book still fit the authority? ──
    if envelope:
        allow = set(envelope.get("symbol_allowlist") or [])
        block = set(envelope.get("symbol_blocklist") or [])
        per_trade = _f(envelope.get("max_notional_per_trade_usd"))
        for p in norm:
            if allow and p["symbol"] not in allow:
                alerts.append({"level": "warn", "category": "outside_authority",
                               "symbol": p["symbol"],
                               "msg": f"{p['symbol']} is held but no longer in your "
                                      "authorized symbol set — your envelope tightened "
                                      "under an open position."})
            if p["symbol"] in block:
                alerts.append({"level": "warn", "category": "blocklisted_held",
                               "symbol": p["symbol"],
                               "msg": f"{p['symbol']} is on your envelope's blocklist "
                                      "but is currently held."})
            if per_trade is not None and p["notional_usd"] > per_trade + 1e-9:
                alerts.append({"level": "warn", "category": "over_cap",
                               "symbol": p["symbol"],
                               "msg": f"{p['symbol']} position ${p['notional_usd']:,.0f} "
                                      f"exceeds your ${per_trade:,.0f} per-trade cap."})

    # ── 24h spend nearing the daily cap ──
    cap = _f(daily_cap) if daily_cap is not None else _f((envelope or {}).get("max_notional_daily_usd"))
    spent = _f(spent_today_usd) or 0.0
    if cap is not None and cap > 0 and spent >= 0.8 * cap:
        pct = spent / cap * 100
        lvl = "warn" if spent >= cap else "caution"
        alerts.append({"level": lvl, "category": "daily_spend",
                       "msg": f"You've used ${spent:,.0f} of your ${cap:,.0f} daily "
                              f"notional cap ({pct:.0f}%)."})

    # ── Concentration: one asset dominating gross exposure ──
    if gross > 0:
        by_sym: dict[str, float] = {}
        for p in norm:
            by_sym[p["symbol"]] = by_sym.get(p["symbol"], 0.0) + p["notional_usd"]
        top_sym, top_usd = max(by_sym.items(), key=lambda kv: kv[1])
        share = top_usd / gross * 100
        if share > concentration_pct and len(by_sym) > 1:
            alerts.append({"level": "caution", "category": "concentration",
                           "symbol": top_sym,
                           "msg": f"{top_sym} is {share:.0f}% of your gross exposure "
                                  f"(${top_usd:,.0f} of ${gross:,.0f}) — concentrated."})

    # ── Stacked correlated same-side risk ──
    for side in ("long", "short"):
        corr = sorted({p["symbol"] for p in norm
                       if p["side"] == side and p["symbol"] in _CORRELATED})
        if len(corr) >= 2:
            alerts.append({"level": "caution", "category": "stacked_correlated",
                           "msg": f"{len(corr)} correlated majors held {side} "
                                  f"({', '.join(corr)}) — this is closer to one bet than "
                                  "several; a market move hits them together."})

    # ── Size vs equity (whole-book leverage sanity) ──
    eq = _f(equity_usd)
    if eq and eq > 0 and gross > 3.0 * eq:
        alerts.append({"level": "warn", "category": "book_leverage",
                       "msg": f"Gross exposure ${gross:,.0f} is {gross/eq:.1f}× your "
                              f"${eq:,.0f} equity — a sharp move is amplified."})

    alerts.sort(key=lambda a: _ORDER.get(a["level"], 0), reverse=True)
    worst = alerts[0]["level"] if alerts else "clear"
    return {"alerts": alerts, "count": len(alerts), "worst_level": worst,
            "gross_usd": gross}


def human_readable(report: Optional[dict]) -> str:
    """Plain-text render (no markup)."""
    if not report or not report.get("alerts"):
        return "🟢 Risk sentry: nothing flagged in your current posture."
    icon = {"warn": "🔴", "caution": "🟠", "info": "🔵"}
    lines = [f"Risk sentry — {report['count']} flag(s):"]
    for a in report["alerts"]:
        lines.append(f"{icon.get(a['level'], '•')} {a['msg']}")
    return "\n".join(lines)
