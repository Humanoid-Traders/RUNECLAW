"""
Cross-venue funding radar — compare perp funding across Bitget, Bybit and
Hyperliquid (READ-ONLY, public endpoints, no keys).

Funding is the carry cost of holding a perp. The same coin often funds at
meaningfully different rates per venue, which matters twice:

  1. carry — if the engine can open its long where funding is lowest (or
     most negative), the position gets paid to exist;
  2. arbitrage — long the low-funding venue / short the high-funding venue
     is delta-neutral and collects the spread (the roadmap's funding-arb
     backbone; this radar is its measurement layer).

Everything here is informational: one GET/POST per venue, normalized to
annualized percent (APR) so an 8h Bitget rate and an hourly Hyperliquid
rate are directly comparable. Every fetch is fail-soft — a venue that is
unreachable (e.g. Bybit is geo-fenced in some regions) simply drops out of
the comparison rather than erroring the caller.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger(__name__)

_TIMEOUT = 10
_HOURS_PER_YEAR = 24 * 365


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _get_json(url: str, post_body: Optional[dict] = None) -> Any:
    """One JSON request (honors HTTPS_PROXY via urllib). Raises on failure."""
    data = json.dumps(post_body).encode() if post_body is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json",
                 "User-Agent": "runeclaw-funding-radar"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def fetch_bitget_funding(bases: list[str]) -> dict[str, float]:
    """{base: annualized funding %} from Bitget USDT-M. Skips failed bases."""
    out: dict[str, float] = {}
    for base in bases:
        try:
            resp = _get_json(
                "https://api.bitget.com/api/v2/mix/market/current-fund-rate"
                f"?symbol={base}USDT&productType=usdt-futures")
            row = (resp.get("data") or [{}])[0]
            rate = _f(row.get("fundingRate"))
            interval_h = _f(row.get("fundingRateInterval")) or 8.0
            out[base] = rate * (_HOURS_PER_YEAR / interval_h) * 100
        except Exception as exc:
            log.debug("funding radar: bitget %s failed: %s", base, exc)
    return out


def fetch_bybit_funding(bases: list[str]) -> dict[str, float]:
    """{base: annualized funding %} from Bybit linear perps (one call).
    {} when Bybit is unreachable (geo-fence, outage)."""
    try:
        resp = _get_json("https://api.bybit.com/v5/market/tickers?category=linear")
        rows = ((resp.get("result") or {}).get("list")) or []
    except Exception as exc:
        log.debug("funding radar: bybit unreachable: %s", exc)
        return {}
    wanted = {f"{b}USDT": b for b in bases}
    out: dict[str, float] = {}
    for r in rows:
        base = wanted.get(str(r.get("symbol", "")))
        if base and r.get("fundingRate") not in (None, ""):
            # Bybit linear funding settles every 8h.
            out[base] = _f(r["fundingRate"]) * (_HOURS_PER_YEAR / 8.0) * 100
    return out


def fetch_hyperliquid_funding(bases: list[str]) -> dict[str, float]:
    """{base: annualized funding %} from Hyperliquid (funding is HOURLY)."""
    try:
        meta, ctxs = _get_json("https://api.hyperliquid.xyz/info",
                               {"type": "metaAndAssetCtxs"})
        universe = meta.get("universe") or []
    except Exception as exc:
        log.debug("funding radar: hyperliquid unreachable: %s", exc)
        return {}
    wanted = set(bases)
    out: dict[str, float] = {}
    for coin, ctx in zip(universe, ctxs):
        name = str(coin.get("name", ""))
        if name in wanted and not coin.get("isDelisted"):
            out[name] = _f(ctx.get("funding")) * _HOURS_PER_YEAR * 100
    return out


@dataclass
class FundingRow:
    base: str
    rates: dict[str, float] = field(default_factory=dict)  # venue -> APR %
    spread_apr: float = 0.0     # best short APR minus best long APR
    long_venue: str = ""        # venue where a LONG pays the least / earns
    short_venue: str = ""       # venue where a SHORT earns the most


def build_comparison(bases: list[str],
                     fetchers: Optional[dict] = None) -> list[FundingRow]:
    """Cross-venue rows for every base seen on >=2 venues, widest spread
    first. ``fetchers`` overrides the venue fetchers (tests)."""
    fetchers = fetchers or {
        "bitget": fetch_bitget_funding,
        "bybit": fetch_bybit_funding,
        "hyperliquid": fetch_hyperliquid_funding,
    }
    per_venue: dict[str, dict[str, float]] = {}
    for venue, fn in fetchers.items():
        try:
            per_venue[venue] = fn(bases) or {}
        except Exception as exc:
            log.debug("funding radar: %s fetcher failed: %s", venue, exc)
            per_venue[venue] = {}

    rows: list[FundingRow] = []
    for base in bases:
        rates = {v: r[base] for v, r in per_venue.items() if base in r}
        if len(rates) < 2:
            continue  # a spread needs two sides
        lo_v = min(rates, key=rates.get)   # cheapest place to be LONG
        hi_v = max(rates, key=rates.get)   # richest place to be SHORT
        rows.append(FundingRow(
            base=base, rates=rates,
            spread_apr=rates[hi_v] - rates[lo_v],
            long_venue=lo_v, short_venue=hi_v))
    rows.sort(key=lambda r: r.spread_apr, reverse=True)
    return rows


def format_funding_html(rows: list[FundingRow], top_n: int = 10) -> str:
    """Telegram-HTML rendering, widest spreads first."""
    if not rows:
        return ("🟡 <b>Funding radar</b>\nNo cross-venue rates available "
                "right now (venues unreachable or symbols not listed on "
                "two venues).")
    lines = ["⚖️ <b>Funding radar — annualized rates across venues</b>"]
    for r in rows[:top_n]:
        rates = " · ".join(f"{v} <code>{apr:+.1f}%</code>"
                           for v, apr in sorted(r.rates.items()))
        lines.append(
            f"<b>{r.base}</b>: {rates}\n"
            f"- spread <code>{r.spread_apr:.1f}%/yr</code> → long "
            f"{r.long_venue} / short {r.short_venue}")
    lines.append(
        "<i>Positive = longs pay shorts. Spread = what a delta-neutral "
        "long-low/short-high pair would collect, before fees/slippage. "
        "Read-only radar — no orders are placed.</i>")
    return "\n\n".join(lines)
