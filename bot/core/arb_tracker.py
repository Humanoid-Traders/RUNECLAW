"""
Funding-arb paper tracker — evidence before capital.

The funding radar (bot.core.funding_radar) shows cross-venue spreads at a
moment in time; this module answers the question that actually gates the
roadmap's capture strategy: *"had we run a delta-neutral pair on these
spreads, what would it have earned — and does it survive fees?"*

It records hourly spread snapshots to ``data/learning/funding_arb.jsonl``
and, at read time, accrues hypothetical carry on a FIXED paper notional:
while a coin's spread stays at or above the entry threshold, the pair is
treated as continuously held and earns ``spread_apr`` pro-rata over the
observed interval. Gaps longer than a few hours break the position (we
refuse to extrapolate across unobserved time).

Strictly paper: nothing here places, sizes, or even proposes an order.
The /arb report includes the fee reality check — a real pair pays 4 taker
fees (open+close on two venues) plus slippage, which at ~0.06%/side is
~0.24% of notional; spreads must out-earn that before the strategy is
worth gating in.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from bot.compat import UTC

log = logging.getLogger(__name__)

_RECORD_FILE = (Path(__file__).resolve().parent.parent.parent
                / "data" / "learning" / "funding_arb.jsonl")

PAPER_NOTIONAL_USD = 1_000.0     # fixed hypothetical size per tracked pair
DEFAULT_MIN_SPREAD_APR = 3.0     # % / yr — below this the pair is "flat"
MAX_GAP_HOURS = 3.0              # unobserved gaps break the paper position
ROUND_TRIP_FEE_PCT = 0.24        # 4 taker legs @ ~0.06% — the reality check

_HOURS_PER_YEAR = 24 * 365


def _env_f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def snapshot_opportunities(bases: Optional[list[str]] = None) -> int:
    """Fetch the current cross-venue comparison and append one snapshot line
    per >=2-venue coin. Returns rows written (0 on any failure — best-effort,
    called from the proactive monitor's background thread)."""
    from bot.core.funding_radar import build_comparison
    bases = bases or ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "AVAX", "LINK"]
    try:
        rows = build_comparison(bases[:20])
    except Exception as exc:
        log.debug("arb tracker: comparison failed: %s", exc)
        return 0
    if not rows:
        return 0
    ts = datetime.now(UTC).isoformat()
    lines = [json.dumps({
        "ts": ts, "base": r.base, "rates": r.rates,
        "spread_apr": round(r.spread_apr, 4),
        "long_venue": r.long_venue, "short_venue": r.short_venue,
    }) for r in rows]
    try:
        _RECORD_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_RECORD_FILE, "a") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as exc:
        log.debug("arb tracker: write failed: %s", exc)
        return 0
    return len(lines)


def load_snapshots(path: Optional[Path] = None) -> list[dict]:
    p = Path(path) if path else _RECORD_FILE
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


@dataclass
class PaperCarry:
    base: str
    earned_usd: float = 0.0      # accrued carry on the paper notional
    held_hours: float = 0.0      # time the pair counted as "on"
    observed_hours: float = 0.0  # total observed span (incl. flat time)
    entries: int = 0             # distinct on-periods
    last_spread_apr: float = 0.0
    venues: str = ""             # latest long/short pairing


def compute_paper_carry(snapshots: list[dict],
                        notional: float = PAPER_NOTIONAL_USD,
                        min_spread_apr: Optional[float] = None,
                        max_gap_hours: float = MAX_GAP_HOURS) -> list[PaperCarry]:
    """Accrue hypothetical carry per coin from the snapshot history.

    Between consecutive snapshots of the same coin (gap <= max_gap_hours):
    if the EARLIER snapshot's spread was at/above the threshold, the pair is
    treated as held for that interval and earns spread_apr pro-rata on the
    notional. Unobserved gaps and sub-threshold intervals earn nothing —
    the estimator only credits time it actually watched.
    """
    if min_spread_apr is None:
        min_spread_apr = _env_f("ARB_MIN_SPREAD_APR", DEFAULT_MIN_SPREAD_APR)
    per_base: dict[str, list[tuple[datetime, dict]]] = {}
    for s in snapshots:
        try:
            ts = datetime.fromisoformat(str(s.get("ts")))
            ts = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            continue
        per_base.setdefault(str(s.get("base", "")).upper(), []).append((ts, s))

    out: list[PaperCarry] = []
    for base, seq in per_base.items():
        if not base:
            continue
        seq.sort(key=lambda p: p[0])
        pc = PaperCarry(base=base)
        was_on = False
        for (t0, s0), (t1, _s1) in zip(seq, seq[1:]):
            gap_h = (t1 - t0).total_seconds() / 3600.0
            if gap_h <= 0 or gap_h > max_gap_hours:
                was_on = False
                continue
            pc.observed_hours += gap_h
            spread = float(s0.get("spread_apr", 0) or 0)
            if spread >= min_spread_apr:
                pc.earned_usd += notional * (spread / 100.0) * (gap_h / _HOURS_PER_YEAR)
                pc.held_hours += gap_h
                if not was_on:
                    pc.entries += 1
                was_on = True
            else:
                was_on = False
        last = seq[-1][1]
        pc.last_spread_apr = float(last.get("spread_apr", 0) or 0)
        pc.venues = f"long {last.get('long_venue', '?')} / short {last.get('short_venue', '?')}"
        if pc.observed_hours > 0:
            out.append(pc)
    out.sort(key=lambda p: p.earned_usd, reverse=True)
    return out


def format_arb_html(carries: list[PaperCarry], current_rows=None,
                    notional: float = PAPER_NOTIONAL_USD) -> str:
    """Telegram-HTML report: tracked paper carry + fee reality check."""
    lines = ["🧪 <b>Funding-arb paper tracker</b>"]
    if current_rows:
        top = current_rows[0]
        lines.append(
            f"Widest spread now: <b>{top.base}</b> "
            f"<code>{top.spread_apr:.1f}%/yr</code> "
            f"(long {top.long_venue} / short {top.short_venue})")
    if not carries:
        lines.append(
            "No tracked history yet — snapshots accrue hourly once the bot "
            "runs with <code>ARB_TRACKER_ENABLED=true</code> (default on).")
        return "\n\n".join(lines)

    total = sum(c.earned_usd for c in carries)
    total_entries = sum(c.entries for c in carries)
    fee_cost = notional * ROUND_TRIP_FEE_PCT / 100.0
    for c in carries[:8]:
        days = c.observed_hours / 24.0
        lines.append(
            f"<b>{c.base}</b>: paper carry <code>${c.earned_usd:+.2f}</code> "
            f"on ${notional:,.0f} · held {c.held_hours:.0f}h of "
            f"{days:.1f}d observed · {c.entries} entr{'y' if c.entries == 1 else 'ies'}\n"
            f"- now <code>{c.last_spread_apr:.1f}%/yr</code> ({c.venues})")
    lines.append(
        f"Total paper carry: <code>${total:+.2f}</code> across "
        f"{len(carries)} coin(s)")
    lines.append(
        f"<i>Fee reality check: a REAL pair pays ~{ROUND_TRIP_FEE_PCT:.2f}% "
        f"of notional per round trip (≈${fee_cost:.2f} on ${notional:,.0f}) "
        f"— {total_entries} tracked entr{'y' if total_entries == 1 else 'ies'} "
        f"would have cost ≈${fee_cost * total_entries:.2f}. Carry must beat "
        "that before the capture strategy is worth gating in. This tracker "
        "is 100% paper — it never places orders.</i>")
    return "\n\n".join(lines)
