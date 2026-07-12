#!/usr/bin/env python3
"""
Cross-venue divergence vote A/B — run on RECORDED live data.

Usage (on the bot server, after ~2 weeks of OF_CROSS_VENUE_FUNDING
recording; snapshots accumulate automatically at OF_SNAPSHOT_PATH):

    python scripts/ab_cross_venue_vote.py [data/learning/order_flow_snapshots.jsonl]
    python scripts/ab_cross_venue_vote.py --min-samples 200 --horizons 1,4,24

What it answers: does the cross-venue funding divergence (home funding
minus the Bybit/Hyperliquid mean) PREDICT forward returns in the
contrarian direction the gated vote (OF_CROSS_VENUE_VOTE_ENABLED)
would trade it?

Method:
  1. Load snapshots; keep those carrying cross_venue_funding + the home
     funding_rate. Refuse to conclude below --min-samples (default 200).
  2. For each snapshot compute the vote's driver: dnorm = clip(delta /
     OF_FUNDING_EXTREME, -1, 1), vote direction = -sign(dnorm).
  3. Join forward returns at the requested horizons (hours) from Bitget
     USDT-FUTURES 1h OHLCV (fetched once per symbol, cached in-process).
  4. Report per horizon: Spearman rank IC of (-dnorm) vs forward return,
     a bootstrap 90% CI on the IC, directional hit rate on the
     above-median-|divergence| half, and top-vs-bottom divergence
     quintile mean forward returns.
  5. Also quantify what enabling the vote would DO: re-score every
     snapshot with the vote OFF vs ON and summarize the score shift.

Verdict rule (printed at the end): recommend enabling only when the IC
is positive with a bootstrap CI excluding zero on at least two horizons
AND the crowded-side quintile spread agrees. Otherwise keep the flag
OFF and keep collecting.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.core.order_flow import (OrderFlowAnalyzer, OrderFlowConfig,  # noqa: E402
                                 OrderFlowSignal)

DEFAULT_SNAPSHOTS = "data/learning/order_flow_snapshots.jsonl"


def _parse_ts(s: str) -> float | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def load_snapshots(path: str) -> list[dict]:
    rows: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            sig = e.get("signal") or {}
            xr = sig.get("cross_venue_funding")
            fr = sig.get("funding_rate")
            ts = _parse_ts(e.get("ts", ""))
            if not xr or fr is None or ts is None:
                continue
            rows.append({"symbol": e.get("symbol", ""), "ts": ts,
                         "funding_rate": float(fr),
                         "cross": {k: float(v) for k, v in xr.items()},
                         "signal": sig})
    return rows


def spearman(xs: list[float], ys: list[float]) -> float:
    def _ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for rank, i in enumerate(order):
            r[i] = float(rank)
        return r
    rx, ry = _ranks(xs), _ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return cov / (vx * vy) if vx > 0 and vy > 0 else 0.0


def bootstrap_ci(xs, ys, n_boot=500, alpha=0.10):
    n = len(xs)
    rng = random.Random(42)
    stats = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        stats.append(spearman([xs[i] for i in idx], [ys[i] for i in idx]))
    stats.sort()
    lo = stats[int(n_boot * alpha / 2)]
    hi = stats[int(n_boot * (1 - alpha / 2)) - 1]
    return lo, hi


class Klines:
    """1h OHLCV per symbol from Bitget USDT-FUTURES, fetched once."""

    def __init__(self) -> None:
        import ccxt
        self.ex = ccxt.bitget({"options": {"defaultType": "swap"},
                               "enableRateLimit": True})
        self.cache: dict[str, list] = {}

    def _perp(self, symbol: str) -> str:
        base = symbol.split("/")[0].split(":")[0]
        return f"{base}/USDT:USDT"

    def forward_return(self, symbol: str, ts: float, horizon_h: int):
        sym = self._perp(symbol)
        if sym not in self.cache:
            try:
                self.cache[sym] = self.ex.fetch_ohlcv(
                    sym, "1h", limit=1000) or []
            except Exception:
                self.cache[sym] = []
        candles = self.cache[sym]
        if not candles:
            return None
        ms = ts * 1000
        # first candle at/after the snapshot
        start = next((c for c in candles if c[0] >= ms), None)
        end = next((c for c in candles
                    if c[0] >= ms + horizon_h * 3_600_000), None)
        if not start or not end or not start[4]:
            return None
        return (end[4] - start[4]) / start[4]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("snapshots", nargs="?", default=DEFAULT_SNAPSHOTS)
    ap.add_argument("--min-samples", type=int, default=200)
    ap.add_argument("--horizons", default="1,4,24",
                    help="forward-return horizons in hours")
    args = ap.parse_args()

    rows = load_snapshots(args.snapshots)
    print(f"Snapshots with cross-venue data: {len(rows)} "
          f"({args.snapshots})")
    if rows:
        t0 = datetime.fromtimestamp(min(r['ts'] for r in rows), timezone.utc)
        t1 = datetime.fromtimestamp(max(r['ts'] for r in rows), timezone.utc)
        per_sym = defaultdict(int)
        for r in rows:
            per_sym[r["symbol"]] += 1
        print(f"Range: {t0:%Y-%m-%d %H:%M} .. {t1:%Y-%m-%d %H:%M} UTC | "
              f"{len(per_sym)} symbols")
    if len(rows) < args.min_samples:
        print(f"\nDATA NOT READY: need >= {args.min_samples} samples. "
              "Keep OF_CROSS_VENUE_FUNDING recording and re-run later.")
        return 1

    cfg = OrderFlowConfig()
    extreme = cfg.funding_extreme

    # Divergence driver per snapshot (the exact quantity the vote uses)
    for r in rows:
        mean_other = sum(r["cross"].values()) / len(r["cross"])
        delta = r["funding_rate"] - mean_other
        r["dnorm"] = max(-1.0, min(1.0, delta / extreme))
        r["vote"] = -r["dnorm"]          # contrarian direction

    kl = Klines()
    horizons = [int(h) for h in args.horizons.split(",")]
    verdicts = []
    for h in horizons:
        pairs = []
        for r in rows:
            fr = kl.forward_return(r["symbol"], r["ts"], h)
            if fr is not None:
                pairs.append((r["vote"], fr, abs(r["dnorm"])))
        if len(pairs) < args.min_samples // 2:
            print(f"\n[{h}h] insufficient joined samples ({len(pairs)}) — "
                  "snapshots may be older than the 1000-candle window")
            continue
        votes = [p[0] for p in pairs]
        rets = [p[1] for p in pairs]
        ic = spearman(votes, rets)
        lo, hi = bootstrap_ci(votes, rets)
        # hit rate where the signal is actually loud
        med = sorted(p[2] for p in pairs)[len(pairs) // 2]
        loud = [(v, fr) for v, fr, m in pairs if m >= med and v != 0]
        hits = sum(1 for v, fr in loud if (v > 0) == (fr > 0))
        hit_rate = hits / len(loud) * 100 if loud else 0.0
        # quintile spread
        by_vote = sorted(pairs, key=lambda p: p[0])
        q = max(1, len(pairs) // 5)
        bot_q = sum(p[1] for p in by_vote[:q]) / q
        top_q = sum(p[1] for p in by_vote[-q:]) / q
        print(f"\n[{h}h] n={len(pairs)}  IC={ic:+.4f}  "
              f"90% CI [{lo:+.4f}, {hi:+.4f}]  "
              f"loud-half hit rate {hit_rate:.1f}%  "
              f"top-vs-bottom vote quintile {top_q - bot_q:+.4%}")
        verdicts.append(ic > 0 and lo > 0 and (top_q - bot_q) > 0)

    # What enabling the vote would do to scores
    import dataclasses
    a_off = OrderFlowAnalyzer(OrderFlowConfig())
    a_on = OrderFlowAnalyzer(dataclasses.replace(
        OrderFlowConfig(), cross_venue_vote_enabled=True))
    shifts = []
    for r in rows[:2000]:
        try:
            s1 = OrderFlowSignal(**r["signal"])
            s2 = OrderFlowSignal(**r["signal"])
            a_off._fill_composite(s1, ok=list(s1.components_ok))
            a_on._fill_composite(s2, ok=list(s2.components_ok))
            shifts.append(abs(s2.smart_money_score - s1.smart_money_score))
        except Exception:
            continue
    if shifts:
        shifts.sort()
        print(f"\nScore shift if enabled: median "
              f"{shifts[len(shifts) // 2]:.4f}, p90 "
              f"{shifts[int(len(shifts) * 0.9)]:.4f} "
              f"(n={len(shifts)})")

    print("\n" + "=" * 60)
    if len(verdicts) >= 2 and sum(verdicts) >= 2:
        print("VERDICT: ENABLE — the divergence predicts contrarian "
              "forward returns on multiple horizons.")
        print("  .env: OF_CROSS_VENUE_VOTE_ENABLED=true   (weight "
              f"OF_W_CROSS_VENUE={cfg.w_cross_venue})")
    else:
        print("VERDICT: KEEP OFF — no robust predictive edge in this "
              "sample. Keep recording and re-run with more data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
