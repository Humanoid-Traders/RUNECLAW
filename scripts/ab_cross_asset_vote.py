#!/usr/bin/env python3
"""
Cross-asset regime vote A/B — run on REAL market data (direct measurement).

Usage:
    python scripts/ab_cross_asset_vote.py
    python scripts/ab_cross_asset_vote.py --horizons 1,4,24 --warmup 30

What it answers: do the dark cross-asset voter's net votes
(CROSS_ASSET_VOTER_ENABLED — cross_regime + dollar_wind + alt_season from
CrossAssetContext.to_confluence_votes) PREDICT forward returns on the alt
symbols they would vote on?

Method (no synthetic data — synthetic cross-market correlation would bake
the verdict into the generator):
  1. Fetch ~1000 real 1h closes per symbol from Bitget USDT-FUTURES for
     BTC, ETH and an alt basket; align on common timestamps.
  2. Replay chronologically through a fresh CrossAssetTracker exactly as
     live would feed it (feed_price with the BAR timestamp — the tracker's
     windows are index-based, so this is lookahead-free), forcing
     get_context(force=True) past its wall-clock cache.
  3. At each bar, for each alt, net vote = sum(vote * weight) over
     to_confluence_votes(symbol). Join forward returns at the requested
     horizons from the same aligned closes.
  4. Report per horizon: Spearman rank IC of net vote vs forward return,
     bootstrap 90% CI, directional hit rate on non-zero votes, and the
     bullish-vote minus bearish-vote mean forward-return spread.
     For 24h also report a non-overlapping (every 24th bar) IC — the
     overlapping-sample bootstrap understates CI width.

Verdict rule (printed at the end): recommend DEFAULT ON only when the IC
is positive with a bootstrap CI excluding zero on at least two horizons
AND the bull-bear spread agrees on those horizons. Otherwise keep the
flag OFF and re-run later with more data.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.core.cross_asset import CrossAssetTracker  # noqa: E402

ALTS = ("SOL", "XRP", "BNB", "DOGE", "ADA", "LINK")


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


def fetch_closes(bases: list[str], granularity: str = "1H", limit: int = 1000):
    """{base: {ts_sec: close}} from the Bitget v2 public candles endpoint
    (plain requests — no auth, no ccxt), honoring a custom CA bundle env
    (REQUESTS_CA_BUNDLE / SSL_CERT_FILE) when one is set."""
    import requests
    verify = (os.environ.get("REQUESTS_CA_BUNDLE")
              or os.environ.get("SSL_CERT_FILE") or True)
    out: dict[str, dict[int, float]] = {}
    for base in bases:
        r = requests.get(
            "https://api.bitget.com/api/v2/mix/market/candles",
            params={"symbol": f"{base}USDT", "productType": "USDT-FUTURES",
                    "granularity": granularity, "limit": str(limit)},
            verify=verify, timeout=30)
        d = r.json()
        if d.get("code") != "00000":
            raise RuntimeError(f"Bitget candles {base}: {d.get('msg')}")
        out[base] = {int(int(c[0]) // 1000): float(c[4])
                     for c in d.get("data") or [] if c[4]}
        print(f"  {base}: {len(out[base])} bars")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizons", default="1,4,24")
    ap.add_argument("--warmup", type=int, default=30)
    ap.add_argument("--limit", type=int, default=1000)
    args = ap.parse_args()
    horizons = [int(h) for h in args.horizons.split(",")]

    bases = ["BTC", "ETH", *ALTS]
    print(f"Fetching {args.limit} 1h bars for {', '.join(bases)} …")
    closes = fetch_closes(bases, limit=args.limit)

    ts_common = sorted(set.intersection(*[set(m.keys()) for m in closes.values()]))
    n = len(ts_common)
    if n < args.warmup + max(horizons) + 50:
        print(f"REFUSING to conclude: only {n} aligned bars.")
        return 1
    print(f"{n} aligned hourly bars "
          f"({(ts_common[-1] - ts_common[0]) / 86400:.1f} days)\n")

    # Chronological replay — one tracker, fed exactly like live.
    tracker = CrossAssetTracker()
    samples: list[tuple[str, int, float]] = []   # (alt, bar_index, net_vote)
    for i, ts in enumerate(ts_common):
        for base in bases:
            tracker.feed_price(f"{base}USDT", closes[base][ts], ts=float(ts))
        if i < args.warmup:
            continue
        ctx = tracker.get_context(force=True)
        for alt in ALTS:
            votes = ctx.to_confluence_votes(f"{alt}USDT")
            net = sum(v * w for _n, v, w in votes)
            samples.append((alt, i, net))

    nonzero = sum(1 for _s, _i, v in samples if v != 0.0)
    print(f"{len(samples)} alt-bar samples; {nonzero} "
          f"({100 * nonzero / len(samples):.1f}%) carry a non-zero net vote\n")

    ok_horizons = 0
    for h in horizons:
        xs, ys = [], []
        for alt, i, v in samples:
            if i + h >= n:
                continue
            p0 = closes[alt][ts_common[i]]
            p1 = closes[alt][ts_common[i + h]]
            xs.append(v)
            ys.append((p1 - p0) / p0)
        ic = spearman(xs, ys)
        lo, hi = bootstrap_ci(xs, ys)
        nz = [(v, r) for v, r in zip(xs, ys) if v != 0.0]
        hits = sum(1 for v, r in nz if (v > 0) == (r > 0)) if nz else 0
        bull = [r for v, r in nz if v > 0]
        bear = [r for v, r in nz if v < 0]
        spread = ((sum(bull) / len(bull)) if bull else 0.0) - \
                 ((sum(bear) / len(bear)) if bear else 0.0)
        agree = ic > 0 and lo > 0 and spread > 0
        ok_horizons += 1 if agree else 0
        print(f"h={h:>2}h  IC={ic:+.4f}  90%CI[{lo:+.4f},{hi:+.4f}]  "
              f"hit={100 * hits / len(nz) if nz else 0:.1f}% (n={len(nz)})  "
              f"bull-bear spread={spread * 100:+.3f}%  "
              f"{'AGREES' if agree else 'no'}")
        if h >= 24:
            # Non-overlapping robustness slice: every h-th bar only.
            xs2 = [v for j, (v, _r) in enumerate(zip(xs, ys)) if j % h == 0]
            ys2 = [r for j, (_v, r) in enumerate(zip(xs, ys)) if j % h == 0]
            if len(xs2) >= 30:
                print(f"        non-overlap IC={spearman(xs2, ys2):+.4f} "
                      f"(n={len(xs2)}) — overlap-free robustness check")

    print("\nVERDICT (rule: IC>0 with CI excluding zero AND positive "
          "bull-bear spread on >=2 horizons):")
    if ok_horizons >= 2:
        print("  RECOMMEND DEFAULT ON — the cross-asset net vote predicted "
              "forward returns on this window.")
    else:
        print(f"  KEEP DARK — only {ok_horizons} horizon(s) met the rule on "
              "this window. Re-run later; do not flip on one weak window.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
