"""Natural language → Authority Envelope spec (pure, deterministic).

The custody keystone for self-serve web live trading: a user says, in plain
words, what their agent is allowed to do — *"trade only majors, max $500 a
trade, $2,000 a day, only on Bitget, never withdraw"* — and this compiles it to
a ``spec`` for :func:`bot.guardian.authority.compile_envelope`, which validates,
clamps (tighten-only), hashes, and produces the enforce-able envelope.

Scope is the CUSTODY boundary the envelope actually enforces — notional ceilings,
symbol allow/block, venues, market type, and the default-deny withdrawal switch.
Direction / confidence / drawdown *trade-filter* rules are a different layer
(``intent_policy``) and are deliberately NOT invented here; a phrase we don't map
is reported in ``unmatched`` rather than silently dropped or fabricated.

Discipline:
* No fabricated caps. A percent limit ("2% per trade") only becomes a dollar
  ceiling when the caller supplies ``equity_usd``; otherwise it is surfaced as
  ``pending`` (honest), never guessed.
* Withdrawal stays DENIED. This compiler never emits ``withdraw_allowed`` — the
  envelope's double opt-in is a deliberate, separate action, not an NL side effect.
* Default mode is ``shadow`` — an authored envelope observes before it enforces;
  going to ``enforce`` is an explicit, separate step.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from bot.guardian.intent_policy import _MAJORS

# Known venues we can grant authority over (compile_envelope drops anything not
# in the platform's real venue universe anyway; this just helps NL matching).
_KNOWN_VENUES = ("bitget", "bybit", "bingx", "okx", "gate", "kucoin",
                 "hyperliquid", "paradex")
_STABLES = frozenset({"USDT", "USDC", "DAI", "BUSD", "TUSD", "USD"})

_NUM = r"\$?\s*([\d,]+(?:\.\d+)?)\s*(k|m)?"          # $1,000 / 2k / 1.5m
_PCT = r"(\d+(?:\.\d+)?)\s*%"


def _money(num: str, suffix: Optional[str]) -> Optional[float]:
    try:
        v = float(num.replace(",", ""))
    except (TypeError, ValueError):
        return None
    if suffix == "k":
        v *= 1_000
    elif suffix == "m":
        v *= 1_000_000
    return v if v > 0 else None


def _tickers(fragment: str) -> list[str]:
    """Bare uppercase-ish tickers from a fragment ('btc, eth and sol')."""
    _STOP = {"AND", "ONLY", "JUST", "THE", "TRADE", "TRADES", "COINS", "TOKENS",
             "OR", "MAX", "MIN", "PER", "DAY", "DAILY", "A", "AN", "UP", "TO",
             "EACH", "MY", "WITH", "FOR", "OF", "ON", "IN", "NO", "NOT", "NEVER",
             "AVOID", "ONLY", "CAP", "LIMIT"}
    out: list[str] = []
    for tok in re.findall(r"[A-Za-z]{2,6}", fragment):
        up = tok.upper()
        if up in _STOP:
            continue
        out.append(up)
    return out


def compile_nl_envelope(text: str, *, equity_usd: Optional[float] = None) -> dict:
    """Compile NL → ``{spec, matched, unmatched, pending}``.

    ``spec`` feeds ``authority.compile_envelope``. ``matched`` are human strings
    for the phrases understood; ``pending`` flags percent caps that need account
    equity; ``unmatched`` is True when nothing was recognised.
    """
    t = " " + (text or "").lower().strip() + " "
    spec: dict[str, Any] = {"source_text": (text or "").strip()[:500], "mode": "shadow"}
    matched: list[str] = []
    pending: list[str] = []

    # ── per-trade notional ceiling ────────────────────────────────────
    m = re.search(_NUM + r"\s*(?:per|a|each|/)\s*(?:trade|position|order)", t)
    if m:
        v = _money(m.group(1), m.group(2))
        if v is not None:
            spec["max_notional_per_trade_usd"] = v
            matched.append(f"max ${v:,.0f} per trade")
    else:
        m = re.search(_PCT + r"\s*(?:per|a|each)\s*(?:trade|position)", t)
        if m:
            pct = float(m.group(1))
            if equity_usd and equity_usd > 0:
                v = round(equity_usd * pct / 100.0, 2)
                spec["max_notional_per_trade_usd"] = v
                matched.append(f"max {pct:g}% (${v:,.0f}) per trade")
            else:
                pending.append(f"{pct:g}% per trade (needs your account equity to set a $ cap)")

    # ── daily notional ceiling ────────────────────────────────────────
    m = re.search(_NUM + r"\s*(?:per|a|/)\s*(?:day|daily|24h)", t) \
        or re.search(r"(?:daily|per day)\s*(?:limit|cap|max)?\s*(?:of\s*)?" + _NUM, t)
    if m:
        v = _money(m.group(1), m.group(2))
        if v is not None:
            spec["max_notional_daily_usd"] = v
            matched.append(f"max ${v:,.0f} per day")

    # ── symbol allowlist ──────────────────────────────────────────────
    if re.search(r"\b(only|just)\s+(?:trade\s+)?majors\b|\bmajors only\b", t):
        spec["symbol_allowlist"] = list(_MAJORS)
        matched.append("only majors (" + ", ".join(_MAJORS) + ")")
    else:
        m = re.search(r"\b(?:only|just)\s+(?:trade\s+)?"
                      r"((?:[a-z]{2,6}(?:[,\s]+(?:and\s+)?)?){1,8})\b", t)
        if m:
            syms = [s for s in _tickers(m.group(1)) if s not in _KNOWN_VENUES]
            syms = [s for s in syms if s not in {"MAJORS"}]
            if syms:
                spec["symbol_allowlist"] = sorted(set(syms))
                matched.append("only " + ", ".join(spec["symbol_allowlist"]))

    # ── symbol blocklist ──────────────────────────────────────────────
    m = re.search(r"\b(?:no|never|avoid|exclude|not?)\s+(?:trade\s+)?"
                  r"((?:[a-z]{2,6}(?:[,\s]+(?:and\s+)?)?){1,8})\b", t)
    if m:
        blk = [s for s in _tickers(m.group(1)) if s not in _KNOWN_VENUES
               and s not in {"SHORTS", "SHORT", "LONGS", "WITHDRAW", "WITHDRAWALS", "MEMES", "MEME"}]
        if blk:
            spec["symbol_blocklist"] = sorted(set(blk))
            matched.append("never " + ", ".join(spec["symbol_blocklist"]))

    # ── venues ────────────────────────────────────────────────────────
    venues = [v for v in _KNOWN_VENUES if re.search(r"\b" + v + r"\b", t)]
    if venues and re.search(r"\bonly\b|\bon\b|\bvia\b|\bthrough\b", t):
        spec["allowed_venues"] = venues
        matched.append("only on " + ", ".join(venues))

    # ── market type ───────────────────────────────────────────────────
    if re.search(r"\b(perps?|perpetuals?|futures?)\s*only\b|\bonly\s*(perps?|futures?)\b", t):
        spec["allowed_market_types"] = ["swap"]
        matched.append("perps/futures only")

    return {
        "spec": spec,
        "matched": matched,
        "pending": pending,
        "unmatched": not matched,
    }
