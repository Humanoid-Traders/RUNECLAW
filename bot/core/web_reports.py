"""
RUNECLAW — Web reports builder.

Gathers the read-only intelligence reports that were previously
Telegram-only — cross-venue funding scan, funding-arb paper tracker,
live↔backtest parity, and the idle-asset yield radar — into one JSON
payload the bot pushes to the website (POST /api/bot/sync/reports) on an
hourly cadence, so the dashboard reaches parity with the bot.

Every section is independently fail-soft: a venue outage, a missing
closed-trades file, or absent Earn credentials just nulls that section
(with a reason) — it never raises out of build_reports_payload and never
touches the trading path. All work here is synchronous/blocking by design;
the caller (proactive monitor) runs it in a worker thread.

Privacy: funding/arb/parity are public-safe (parity headline stats are
already published on /track). The yield section contains OPERATOR account
balances — the web app must only serve it to admin-plan users
(app/routes/reports.js enforces this).
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Optional

log = logging.getLogger(__name__)

FUNDING_BASES = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "AVAX", "LINK"]


def _funding_section() -> Optional[dict]:
    from bot.core.funding_radar import build_comparison
    rows = build_comparison(FUNDING_BASES[:12])
    if not rows:
        return None
    return {"rows": [asdict(r) for r in rows]}


def _arb_section() -> Optional[dict]:
    from bot.core.arb_tracker import (
        PAPER_NOTIONAL_USD, compute_paper_carry, load_snapshots)
    snapshots = load_snapshots()
    if not snapshots:
        return None
    carries = compute_paper_carry(snapshots)
    return {
        "notional_usd": PAPER_NOTIONAL_USD,
        "snapshots": len(snapshots),
        "carries": [asdict(c) for c in carries[:10]],
    }


def _parity_section(engine) -> Optional[dict]:
    from bot.backtest.parity import load_closed_trades, parity_summary
    from bot.config import CONFIG
    executor = getattr(engine, "live_executor", None)
    path = getattr(executor, "_closed_trades_file", None)
    if not path:
        return None
    trades = load_closed_trades(path)
    if not trades:
        return None
    summary = parity_summary(trades, CONFIG.risk.commission_pct)
    # Headline stats only — the bucket breakdowns stay in Telegram /parity
    # (they're long) and the web panel links there for the full report.
    keep = ("trades", "excluded_non_fills", "win_rate", "net_pnl", "pf",
            "total_fees", "realized_fee_rate", "modeled_fee_rate",
            "fee_vs_model", "inferred_fills")
    return {k: summary.get(k) for k in keep}


def _yield_section(engine) -> Optional[dict]:
    """OPERATOR-SENSITIVE (account balances) — web serves admins only."""
    from bot.core.bitget_v3_client import BitgetV3Client
    from bot.core.yield_radar import build_report
    client = BitgetV3Client.from_config()
    if not client.has_credentials:
        return None
    free_usdt = 0.0
    try:
        cache = getattr(engine, "_live_balance_cache", None) or {}
        free_usdt = float(cache.get("free") or 0.0)
    except Exception:
        pass
    report = build_report(client, free_usdt)
    if report.error and not report.rows:
        return None
    return {
        "total_idle_usd": report.total_idle_usd,
        "total_est_year_usd": report.total_est_year_usd,
        "rows": [asdict(r) for r in report.rows[:12]],
    }


def build_reports_payload(engine=None) -> dict:
    """Assemble all report sections; each fails soft to None independently."""
    payload: dict = {"generated_at": datetime.now(UTC).isoformat()}
    for key, builder, needs_engine in (
        ("funding", _funding_section, False),
        ("arb", _arb_section, False),
        ("parity", _parity_section, True),
        ("yield", _yield_section, True),
    ):
        try:
            if needs_engine and engine is None:
                payload[key] = None
                continue
            payload[key] = builder(engine) if needs_engine else builder()
        except Exception as exc:  # noqa: BLE001 — sections must fail independently
            log.debug("web report section %s skipped: %s", key, exc)
            payload[key] = None
    return payload
