"""
Yield Radar — find the best return for idle wallet assets (READ-ONLY).

The operator's wallet holds assets that sit idle between trades (free USDT
margin, spot coins). Bitget Earn pays APR on exactly those balances. This
module answers one question: *"what could the idle part of the account earn
right now?"* — it discovers idle balances, pulls the current Earn savings
catalog, and matches the best FLEXIBLE product per asset.

Deliberately read-only (Phase 1): it never subscribes, redeems, or moves a
cent. Auto-subscription is a money-path feature and ships separately behind
an admin confirmation + reserve rules (see docs/ROADMAP.md guardrails).

Why flexible-only for recommendations: flexible savings redeem instantly, so
staked margin can be recalled the moment the engine needs it for a position.
Fixed-term products lock funds and are surfaced as info only.

All Bitget calls go through the signed BitgetV3Client (same HMAC scheme for
/api/v2 paths); every parse is defensive — a schema drift degrades to an
empty report, never an exception into the caller.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger(__name__)

# Ignore dust: don't recommend staking positions worth less than this.
MIN_IDLE_USD = 5.0

# Never recommend staking 100% of free futures margin — the engine needs
# headroom to open positions. Recommendation = free * (1 - reserve).
MARGIN_RESERVE_PCT = 0.30


@dataclass
class YieldRow:
    coin: str
    idle_amount: float          # units of the coin sitting idle
    idle_usd: float             # est. USD value of the idle amount
    stakeable_usd: float        # after the margin reserve haircut
    apy_flexible: Optional[float] = None   # best flexible APY (percent)
    apy_fixed: Optional[float] = None      # best fixed APY (info only)
    est_year_usd: float = 0.0   # stakeable_usd * apy_flexible
    source: str = ""            # "futures free" | "spot"


@dataclass
class YieldReport:
    rows: list[YieldRow] = field(default_factory=list)
    total_idle_usd: float = 0.0
    total_est_year_usd: float = 0.0
    error: str = ""             # non-empty when the radar could not run


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _best_apy(product: dict) -> Optional[float]:
    """Highest advertised APY on a savings product, across the tier list.

    Bitget spells the rate field a few ways across product types — check the
    known spellings and take the max. Values arrive as percent strings.
    """
    candidates: list[float] = []
    for tier in product.get("apyList") or []:
        for key in ("currentApy", "apy", "rate"):
            if tier.get(key) is not None:
                candidates.append(_f(tier.get(key)))
    for key in ("apy", "currentApy"):
        if product.get(key) is not None:
            candidates.append(_f(product.get(key)))
    candidates = [c for c in candidates if c > 0]
    return max(candidates) if candidates else None


def fetch_savings_catalog(client) -> dict[str, dict[str, float]]:
    """Best APY per coin from Bitget Earn savings, split flexible vs fixed.

    Returns {coin: {"flexible": apy, "fixed": apy}} (either key may be
    missing). Empty dict on any API/schema failure.
    """
    try:
        resp = client.request("GET", "/api/v2/earn/savings/product?filter=available")
    except Exception as exc:
        log.warning("Yield radar: savings catalog fetch failed: %s", exc)
        return {}
    if not isinstance(resp, dict) or str(resp.get("code")) not in ("00000", "0"):
        log.warning("Yield radar: savings catalog error: %s", resp)
        return {}
    catalog: dict[str, dict[str, float]] = {}
    for p in resp.get("data") or []:
        coin = str(p.get("coin", "")).upper()
        apy = _best_apy(p)
        if not coin or apy is None:
            continue
        period = str(p.get("periodType", "")).lower()
        bucket = "flexible" if "flexible" in period else "fixed"
        slot = catalog.setdefault(coin, {})
        slot[bucket] = max(slot.get(bucket, 0.0), apy)
    return catalog


def fetch_spot_idle(client) -> dict[str, float]:
    """Available (unfrozen) spot balances per coin. {} on failure."""
    try:
        resp = client.request("GET", "/api/v2/spot/account/assets")
    except Exception as exc:
        log.warning("Yield radar: spot assets fetch failed: %s", exc)
        return {}
    if not isinstance(resp, dict) or str(resp.get("code")) not in ("00000", "0"):
        return {}
    out: dict[str, float] = {}
    for a in resp.get("data") or []:
        coin = str(a.get("coin", "")).upper()
        avail = _f(a.get("available"))
        if coin and avail > 0:
            out[coin] = avail
    return out


def build_report(client, futures_free_usdt: float = 0.0,
                 prices: Optional[dict[str, float]] = None) -> YieldReport:
    """Assemble the idle-asset yield report (pure given its inputs).

    ``futures_free_usdt`` — free USDT margin from the engine's live balance
    cache (the venue-aware executor number). ``prices`` — {coin: usd} for
    valuing non-stable spot coins; missing prices value stables at 1 and skip
    the rest (never invent a price).
    """
    prices = prices or {}
    report = YieldReport()

    catalog = fetch_savings_catalog(client)
    if not catalog:
        report.error = ("Earn catalog unavailable — Bitget Earn API not "
                        "reachable with the operator keys.")
        return report

    idle: dict[str, tuple[float, str]] = {}
    if futures_free_usdt > 0:
        idle["USDT"] = (futures_free_usdt, "futures free")
    for coin, amount in fetch_spot_idle(client).items():
        prev = idle.get(coin)
        if prev:
            idle[coin] = (prev[0] + amount, prev[1] + " + spot")
        else:
            idle[coin] = (amount, "spot")

    for coin, (amount, source) in sorted(idle.items()):
        if coin in ("USDT", "USDC"):
            usd = amount
        elif coin in prices:
            usd = amount * prices[coin]
        else:
            continue  # unknown price — skip rather than invent a value
        if usd < MIN_IDLE_USD:
            continue
        apys = catalog.get(coin, {})
        # Free futures margin keeps a reserve; pure spot holdings don't need one.
        reserve = MARGIN_RESERVE_PCT if "futures" in source else 0.0
        stakeable = usd * (1 - reserve)
        row = YieldRow(
            coin=coin, idle_amount=amount, idle_usd=usd,
            stakeable_usd=stakeable,
            apy_flexible=apys.get("flexible"),
            apy_fixed=apys.get("fixed"),
            source=source,
        )
        if row.apy_flexible:
            row.est_year_usd = stakeable * row.apy_flexible / 100.0
        report.rows.append(row)
        report.total_idle_usd += usd
        report.total_est_year_usd += row.est_year_usd

    # Highest earners first — the recommendation reads top-down.
    report.rows.sort(key=lambda r: r.est_year_usd, reverse=True)
    return report


def format_report_html(report: YieldReport) -> str:
    """Telegram-HTML rendering of the report (b/i/code only)."""
    if report.error:
        return f"🟡 <b>Yield Radar</b>\n{report.error}"
    if not report.rows:
        return ("🟡 <b>Yield Radar</b>\nNo idle assets above the $"
                f"{MIN_IDLE_USD:.0f} dust threshold — nothing to stake.")
    sep = "─" * 16
    lines = [f"⚡ <b>Yield Radar — idle assets could be earning</b>\n{sep}"]
    for r in report.rows:
        flex = f"{r.apy_flexible:.2f}%" if r.apy_flexible else "—"
        fixed = f" (fixed up to {r.apy_fixed:.2f}%)" if r.apy_fixed else ""
        per_day = r.est_year_usd / 365.0
        lines.append(
            f"<b>{r.coin}</b> · idle <code>${r.idle_usd:,.2f}</code> ({r.source})\n"
            f"- Best flexible APY: <code>{flex}</code>{fixed}\n"
            f"- Stakeable after reserve: <code>${r.stakeable_usd:,.2f}</code>"
            + (f" → est <code>${r.est_year_usd:,.2f}/yr</code>"
               f" (<code>${per_day:,.2f}/day</code>)" if r.est_year_usd else "")
        )
    lines.append(sep)
    lines.append(
        f"Total idle: <code>${report.total_idle_usd:,.2f}</code> · "
        f"est. missed yield: <code>${report.total_est_year_usd:,.2f}/yr</code>")
    lines.append(
        "<i>Read-only radar — flexible savings redeem instantly, so staked "
        f"margin stays recallable. A {MARGIN_RESERVE_PCT:.0%} margin reserve "
        "is always kept free. Auto-staking ships separately behind an admin "
        "confirmation.</i>")
    return "\n\n".join(lines)
