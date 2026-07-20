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


# Coins the STAKE money-path may touch. Execution is stables-only on purpose:
# units == USD, so there is no price-conversion step that could size a
# subscription off a stale or wrong price. Other coins stay info-only rows.
STAKEABLE_COINS = ("USDT", "USDC")


@dataclass
class YieldRow:
    coin: str
    idle_amount: float          # units of the coin sitting idle
    idle_usd: float             # est. USD value of the idle amount
    stakeable_usd: float        # after the margin reserve haircut
    apy_flexible: Optional[float] = None   # best flexible APY (percent)
    apy_fixed: Optional[float] = None      # best fixed APY (info only)
    # SPOT-2: every fixed/locked term [{days, apy, product_id}] — locks are
    # surfaced with their full duration so the user can weigh term vs rate;
    # a lock is not revocable until the term ends.
    fixed_terms: list = field(default_factory=list)
    est_year_usd: float = 0.0   # stakeable_usd * apy_flexible
    source: str = ""            # "futures free" | "spot"
    product_id: str = ""        # Bitget productId of the best flexible product
    alt_note: str = ""          # cross-venue info, e.g. "Bybit pays 9.1%"


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


def fetch_savings_catalog(client) -> dict[str, dict]:
    """Best APY per coin from Bitget Earn savings, split flexible vs fixed.

    Returns {coin: {"flexible": apy, "fixed": apy, "flexible_id": productId}}
    (keys may be missing). Empty dict on any API/schema failure.
    """
    try:
        resp = client.request("GET", "/api/v2/earn/savings/product?filter=available")
    except Exception as exc:
        log.warning("Yield radar: savings catalog fetch failed: %s", exc)
        return {}
    if not isinstance(resp, dict) or str(resp.get("code")) not in ("00000", "0"):
        log.warning("Yield radar: savings catalog error: %s", resp)
        return {}
    catalog: dict[str, dict] = {}
    for p in resp.get("data") or []:
        coin = str(p.get("coin", "")).upper()
        apy = _best_apy(p)
        if not coin or apy is None:
            continue
        period = str(p.get("periodType", "")).lower()
        bucket = "flexible" if "flexible" in period else "fixed"
        slot = catalog.setdefault(coin, {})
        if apy >= slot.get(bucket, 0.0):
            slot[bucket] = apy
            if bucket == "flexible":
                # Keep the productId alongside the winning flexible rate so
                # the stake path subscribes to exactly the product it quoted.
                slot["flexible_id"] = str(p.get("productId", "") or "")
        if bucket == "fixed":
            # SPOT-2 (operator directive): keep EVERY fixed term, not just
            # the best number — the Staking center shows all lock options
            # with their durations. A lock is not revocable until the term
            # ends; the surface must let the user weigh term vs rate.
            try:
                days = int(float(p.get("period") or 0))
            except (TypeError, ValueError):
                days = 0
            slot.setdefault("fixed_terms", []).append({
                "days": days,
                "apy": apy,
                "product_id": str(p.get("productId", "") or ""),
            })
    for slot in catalog.values():
        terms = slot.get("fixed_terms")
        if terms:
            terms.sort(key=lambda t: (t["days"], -t["apy"]))
            del terms[12:]          # bounded per coin
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
            fixed_terms=list(apys.get("fixed_terms") or []),
            source=source,
            product_id=str(apys.get("flexible_id", "") or ""),
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
            + (f"\n- ℹ️ {r.alt_note} (info only — /stake executes on Bitget)"
               if r.alt_note else "")
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


# ─── Phase 2: the STAKE money path (explicit admin confirmation required) ────
# Everything below moves real funds. The Telegram layer only calls these from
# an inline-button press by an admin — never automatically — and every amount
# is recomputed and re-clamped HERE at execution time, so a stale button can
# never stake more than the account's current idle balance allows.

@dataclass
class ActionResult:
    ok: bool
    message: str            # human-readable, safe for Telegram HTML


def _post(client, path: str, body: dict) -> ActionResult:
    """Signed POST with uniform code checking. Never raises."""
    try:
        resp = client.request("POST", path, body)
    except Exception as exc:
        return ActionResult(False, f"request failed: {exc}")
    if isinstance(resp, dict) and str(resp.get("code")) in ("00000", "0"):
        return ActionResult(True, "ok")
    msg = resp.get("msg", "unknown error") if isinstance(resp, dict) else str(resp)
    return ActionResult(False, f"Bitget error: {msg}")


def fetch_savings_assets(client) -> list[dict]:
    """Current FLEXIBLE savings holdings: [{product_id, coin, amount, apy}].

    [] on any failure — the caller treats that as "nothing to redeem".
    """
    try:
        resp = client.request(
            "GET", "/api/v2/earn/savings/assets?periodType=flexible")
    except Exception as exc:
        log.warning("Yield radar: savings assets fetch failed: %s", exc)
        return []
    if not isinstance(resp, dict) or str(resp.get("code")) not in ("00000", "0"):
        return []
    data = resp.get("data") or {}
    rows = data.get("resultList") if isinstance(data, dict) else data
    out: list[dict] = []
    for a in rows or []:
        coin = str(a.get("productCoin") or a.get("coin") or "").upper()
        amount = _f(a.get("holdAmount") or a.get("amount"))
        pid = str(a.get("productId", "") or "")
        if coin and pid and amount > 0:
            out.append({"product_id": pid, "coin": coin, "amount": amount,
                        "apy": _f(a.get("apy"))})
    return out


def transfer_futures_to_spot(client, amount: float, coin: str = "USDT") -> ActionResult:
    return _post(client, "/api/v2/spot/wallet/transfer", {
        "fromType": "usdt_futures", "toType": "spot",
        "amount": f"{amount:.2f}", "coin": coin,
    })


def transfer_spot_to_futures(client, amount: float, coin: str = "USDT") -> ActionResult:
    return _post(client, "/api/v2/spot/wallet/transfer", {
        "fromType": "spot", "toType": "usdt_futures",
        "amount": f"{amount:.2f}", "coin": coin,
    })


def execute_stake(client, coin: str, futures_free_usdt: float = 0.0) -> ActionResult:
    """Stake a stable coin's CURRENT stakeable amount into the best flexible
    savings product. The plan the operator confirmed is recomputed from live
    balances here — the button carries only the coin, never an amount.

    Steps: rebuild the radar -> clamp to stakeable (reserve already applied)
    -> top up spot from free futures margin only for the shortfall -> subscribe.
    """
    coin = str(coin).upper()
    if coin not in STAKEABLE_COINS:
        return ActionResult(False, f"{coin} staking is not enabled — "
                            "execution is stables-only (USDT/USDC).")
    report = build_report(client, futures_free_usdt=futures_free_usdt)
    if report.error:
        return ActionResult(False, report.error)
    row = next((r for r in report.rows if r.coin == coin), None)
    if row is None or row.stakeable_usd < MIN_IDLE_USD:
        return ActionResult(False, f"Nothing stakeable in {coin} right now "
                            f"(min ${MIN_IDLE_USD:.0f} after the "
                            f"{MARGIN_RESERVE_PCT:.0%} margin reserve).")
    if not row.apy_flexible or not row.product_id:
        return ActionResult(False, f"No flexible Earn product for {coin}.")
    amount = float(int(row.stakeable_usd * 100)) / 100.0  # round DOWN to cents

    steps: list[str] = []
    # Earn subscribes from the SPOT account; top it up from free futures
    # margin only by the shortfall. By construction the shortfall is at most
    # futures_free * (1 - reserve), so the reserve always stays free.
    spot_avail = fetch_spot_idle(client).get(coin, 0.0)
    shortfall = amount - spot_avail
    if shortfall > 0.01:
        moved = transfer_futures_to_spot(client, shortfall, coin)
        if not moved.ok:
            return ActionResult(False, "Transfer futures→spot failed before "
                                f"any subscription: {moved.message}")
        steps.append(f"moved ${shortfall:,.2f} futures→spot")

    sub = _post(client, "/api/v2/earn/savings/subscribe", {
        "productId": row.product_id, "periodType": "flexible",
        "amount": f"{amount:.2f}",
    })
    if not sub.ok:
        note = f" ({steps[0]} — funds are in spot)" if steps else ""
        return ActionResult(False, f"Subscribe failed: {sub.message}{note}")
    steps.append(f"subscribed ${amount:,.2f} {coin} @ "
                 f"{row.apy_flexible:.2f}% flexible")
    return ActionResult(True, "; ".join(steps))


def execute_unstake(client, product_id: str) -> ActionResult:
    """Redeem a flexible savings holding IN FULL and (for stables) move the
    proceeds back to futures margin so the engine can trade them again."""
    holding = next((h for h in fetch_savings_assets(client)
                    if h["product_id"] == str(product_id)), None)
    if holding is None:
        return ActionResult(False, "That savings position no longer exists — "
                            "already redeemed?")
    coin, amount = holding["coin"], holding["amount"]
    red = _post(client, "/api/v2/earn/savings/redeem", {
        "productId": str(product_id), "periodType": "flexible",
        "amount": f"{amount:.8f}".rstrip("0").rstrip("."),
    })
    if not red.ok:
        return ActionResult(False, f"Redeem failed: {red.message}")
    steps = [f"redeemed {amount:g} {coin} to spot"]
    if coin in STAKEABLE_COINS:
        back = transfer_spot_to_futures(client, amount, coin)
        if back.ok:
            steps.append("moved back to futures margin")
        else:
            steps.append("spot→futures transfer failed — funds are safe in "
                         f"spot, move them manually ({back.message})")
    return ActionResult(True, "; ".join(steps))


# ─── Cross-venue yield (informational) ───────────────────────────────────────
# The radar recommends where the money ALREADY IS (Bitget — /stake executes
# there). Other venues' Earn rates are surfaced as info so the operator can
# see when idle cash would earn meaningfully more elsewhere.

def fetch_bybit_savings_catalog() -> dict[str, dict]:
    """Best flexible-savings APY per coin from Bybit Earn, using the
    operator's BYBIT_API_KEY/SECRET from the environment (the v5 earn
    endpoints are authenticated). {} when keys are absent or the venue is
    unreachable — cross-venue info is strictly best-effort.
    """
    import hashlib
    import hmac
    import json as _json
    import os
    import time
    import urllib.request

    key = os.environ.get("BYBIT_API_KEY", "").strip()
    secret = os.environ.get("BYBIT_API_SECRET", "").strip()
    if not key or not secret:
        return {}
    try:
        ts = str(int(time.time() * 1000))
        recv = "5000"
        query = "category=FlexibleSaving"
        sign = hmac.new(secret.encode(), (ts + key + recv + query).encode(),
                        hashlib.sha256).hexdigest()
        req = urllib.request.Request(
            f"https://api.bybit.com/v5/earn/product?{query}",
            headers={"X-BAPI-API-KEY": key, "X-BAPI-TIMESTAMP": ts,
                     "X-BAPI-RECV-WINDOW": recv, "X-BAPI-SIGN": sign})
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = _json.loads(resp.read().decode())
    except Exception as exc:
        log.debug("Yield radar: bybit earn unreachable: %s", exc)
        return {}
    if str(payload.get("retCode")) != "0":
        log.debug("Yield radar: bybit earn error: %s", payload.get("retMsg"))
        return {}
    catalog: dict[str, dict] = {}
    for p in ((payload.get("result") or {}).get("list")) or []:
        coin = str(p.get("coin", "")).upper()
        # estimateApr arrives as a percent string like "3.50%".
        apr = _f(str(p.get("estimateApr", "")).rstrip("%"))
        if coin and apr > 0 and str(p.get("status", "")).lower() != "notavailable":
            slot = catalog.setdefault(coin, {})
            slot["flexible"] = max(slot.get("flexible", 0.0), apr)
    return catalog


def annotate_cross_venue(report: YieldReport,
                         alt_catalogs: dict[str, dict]) -> None:
    """Mark rows where another venue's flexible rate beats the local one.
    Info only — never changes amounts, recommendations, or the stake path."""
    for row in report.rows:
        best_venue, best_apy = "", row.apy_flexible or 0.0
        for venue, catalog in (alt_catalogs or {}).items():
            apy = (catalog.get(row.coin) or {}).get("flexible", 0.0)
            if apy > best_apy:
                best_venue, best_apy = venue, apy
        if best_venue:
            row.alt_note = f"{best_venue} pays {best_apy:.2f}%"
