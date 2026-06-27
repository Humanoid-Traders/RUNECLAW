"""
RUNECLAW FULL Deep Backtest — entire 67-symbol universe.

Expands run_deep_backtest.py to the full scan universe (bot/skills/scan_skill.py),
suppresses per-bar audit logging for speed, and parallelizes runs across cores.

67 symbols x 5 regimes x 5 seeds = 1675 runs.

Output: backtest_deep_full_results.json  (NOT the committed baseline file).
"""
import asyncio
import json
import logging
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Silence the bot's verbose per-bar audit JSON logging — it dominated runtime in
# the 20-symbol run. We only want the aggregate metrics here.
logging.disable(logging.WARNING)

import run_deep_backtest as rdb  # noqa: E402

BARS = 1500
rdb.BARS = BARS  # used inside rdb.generate_bars_for_regime / run_single_backtest

REGIMES = rdb.REGIMES
SEEDS = rdb.SEEDS
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "backtest_deep_full_results.json")

# Full 67-symbol universe from bot/skills/scan_skill.py, with plausible synthetic
# start prices + per-bar volatility. Absolute price is irrelevant to returns
# (they are relative); it only affects tick rounding and is otherwise cosmetic.
# Known majors reuse the tuned values from run_deep_backtest.py; the rest get
# category-reasonable defaults (memecoins: higher vol + tiny price).
_KNOWN = {s["symbol"]: s for s in rdb.SYMBOLS}

# (price, vol) defaults for symbols not in the original 20.
_EXTRA = {
    "BCH/USDT": (480.0, 0.020),  "ICP/USDT": (11.0, 0.028),
    "AAVE/USDT": (310.0, 0.026), "OP/USDT": (1.70, 0.030),
    "WLD/USDT": (2.40, 0.038),   "WIF/USDT": (1.05, 0.045),
    "ORDI/USDT": (16.0, 0.040),  "ARB/USDT": (0.65, 0.032),
    "XLM/USDT": (0.40, 0.024),   "ETC/USDT": (28.0, 0.024),
    "HBAR/USDT": (0.22, 0.030),  "BONK/USDT": (0.000022, 0.050),
    "PENDLE/USDT": (5.50, 0.038),"XMR/USDT": (190.0, 0.020),
    "ALGO/USDT": (0.30, 0.030),  "CRV/USDT": (0.85, 0.038),
    "TIA/USDT": (4.20, 0.040),   "RENDER/USDT": (6.50, 0.038),
    "INJ/USDT": (20.0, 0.038),   "JUP/USDT": (0.95, 0.040),
    "FET/USDT": (1.30, 0.038),   "APE/USDT": (1.20, 0.038),
    "SEI/USDT": (0.40, 0.040),   "LDO/USDT": (1.80, 0.034),
    "ENA/USDT": (0.70, 0.042),   "ONDO/USDT": (1.40, 0.038),
    "TAO/USDT": (440.0, 0.042),  "HYPE/USDT": (28.0, 0.045),
    "JTO/USDT": (3.20, 0.040),   "DYDX/USDT": (1.50, 0.036),
    "DASH/USDT": (45.0, 0.026),  "ZEC/USDT": (55.0, 0.030),
    "LAB/USDT": (1.50, 0.050),   "VIRTUAL/USDT": (2.80, 0.050),
    "PUMP/USDT": (0.0080, 0.055),"FARTCOIN/USDT": (1.10, 0.060),
    "TRUMP/USDT": (12.0, 0.050), "BIO/USDT": (0.25, 0.050),
    "M/USDT": (1.00, 0.050),     "CHIP/USDT": (0.20, 0.055),
    "B/USDT": (1.00, 0.050),     "ASTER/USDT": (1.50, 0.055),
    "SIREN/USDT": (0.50, 0.055), "SKYAI/USDT": (0.30, 0.055),
    "PENGU/USDT": (0.035, 0.050),"WLFI/USDT": (0.25, 0.050),
    "RAVE/USDT": (0.50, 0.055),  "XPL/USDT": (1.20, 0.050),
}

_UNIVERSE_SYMS = (
    "BTC ETH SOL TON XRP DOGE BNB SUI ADA LINK BCH AVAX DOT ICP NEAR "
    "LTC AAVE UNI OP WLD WIF ORDI ARB TRX XLM ETC APT HBAR BONK PENDLE "
    "XMR ALGO CRV TIA RENDER INJ JUP FET APE SEI ATOM LDO FIL ENA ONDO "
    "TAO HYPE JTO DYDX DASH ZEC LAB VIRTUAL PUMP FARTCOIN TRUMP BIO M "
    "CHIP B ASTER SIREN SKYAI PENGU WLFI RAVE XPL"
).split()


def _build_symbols():
    out = []
    for s in _UNIVERSE_SYMS:
        sym = f"{s}/USDT"
        if sym in _KNOWN:
            out.append(_KNOWN[sym])
        elif sym in _EXTRA:
            price, vol = _EXTRA[sym]
            out.append({"symbol": sym, "price": price, "vol": vol, "name": s})
        else:
            out.append({"symbol": sym, "price": 1.0, "vol": 0.035, "name": s})
    return out


SYMBOLS = _build_symbols()


def _worker(args):
    """Run a single backtest in a worker process. Returns the summary dict."""
    sym, regime, seed = args
    logging.disable(logging.WARNING)
    rdb.BARS = BARS
    try:
        return asyncio.run(rdb.run_single_backtest(sym, regime, seed))
    except Exception as e:  # noqa: BLE001 — record, don't abort the sweep
        return {"symbol": sym["symbol"], "name": sym["name"],
                "regime": regime["label"], "seed": seed, "error": str(e)}


def main():
    jobs = [(sym, regime, seed)
            for sym in SYMBOLS for regime in REGIMES for seed in SEEDS]
    total = len(jobs)
    workers = max(1, (os.cpu_count() or 2) - 1)
    print(f"FULL Deep Backtest: {len(SYMBOLS)} symbols x {len(REGIMES)} regimes "
          f"x {len(SEEDS)} seeds = {total} runs, {BARS} bars, {workers} workers")
    print("=" * 100)

    all_results = []
    done = 0
    start = time.time()
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_worker, j): j for j in jobs}
        for fut in as_completed(futs):
            r = fut.result()
            all_results.append(r)
            done += 1
            if done % 25 == 0 or done == total:
                el = time.time() - start
                rate = el / done
                eta = rate * (total - done)
                errs = sum(1 for x in all_results if "error" in x)
                print(f"  [{done:4d}/{total}] {done/total*100:5.1f}% | "
                      f"{el:6.0f}s elapsed | ETA {eta:5.0f}s | {errs} errors")

    elapsed = time.time() - start
    valid = [r for r in all_results if "error" not in r]
    errors = [r for r in all_results if "error" in r]
    print("=" * 100)
    print(f"Completed {len(all_results)} runs in {elapsed:.0f}s ({elapsed/len(all_results):.2f}s/run)")
    print(f"Valid: {len(valid)} | Errors: {len(errors)}")
    for e in errors[:20]:
        print(f"  ERROR {e['symbol']:14s} {e['regime']:16s} seed={e['seed']}: {e['error']}")

    if not valid:
        print("No valid runs — aborting summary.")
        return

    n = len(valid)
    def avg(k):
        return sum(r[k] for r in valid) / n

    total_trades = sum(r["total_trades"] for r in valid)
    avg_return = avg("total_return_pct")
    avg_dd = avg("max_drawdown_pct")
    avg_wr = avg("win_rate")
    avg_sharpe = avg("sharpe_ratio")
    avg_sortino = avg("sortino_ratio")
    avg_pf = avg("profit_factor")
    worst_dd = max(r["max_drawdown_pct"] for r in valid)
    best_ret = max(r["total_return_pct"] for r in valid)
    worst_ret = min(r["total_return_pct"] for r in valid)
    crashed = sum(1 for r in valid if r["max_drawdown_pct"] > 20)
    total_commission = sum(r["total_commission"] for r in valid)
    total_slippage = sum(r["total_slippage"] for r in valid)
    profitable = sum(1 for r in valid if r["total_return_pct"] > 0)

    print("\n" + "=" * 100)
    print("GLOBAL SUMMARY (FULL UNIVERSE)")
    print("=" * 100)
    print(f"  Valid runs:            {n}  ({len(errors)} errors)")
    print(f"  Total trades:          {total_trades}")
    print(f"  Profitable runs:       {profitable}/{n} ({profitable/n*100:.1f}%)")
    print(f"  Avg return:            {avg_return:+.2f}%   (best {best_ret:+.2f}% / worst {worst_ret:+.2f}%)")
    print(f"  Avg max drawdown:      {avg_dd:.2f}%   (worst {worst_dd:.2f}%)")
    print(f"  Avg win rate:          {avg_wr * 100:.1f}%")
    print(f"  Avg Sharpe / Sortino:  {avg_sharpe:+.2f} / {avg_sortino:+.2f}")
    print(f"  Avg profit factor:     {avg_pf:.2f}")
    print(f"  Crashed runs (DD>20%): {crashed}")
    print(f"  Total commission:      ${total_commission:,.2f}")
    print(f"  Total slippage:        ${total_slippage:,.2f}")
    print(f"  Runtime:               {elapsed:.0f}s")

    # Per-regime aggregate
    print("\nPER-REGIME:")
    print(f"  {'Regime':16s} {'Runs':>5s} {'AvgRet%':>9s} {'AvgDD%':>8s} {'WR%':>7s} {'Sharpe':>8s} {'PF':>8s}")
    for regime in REGIMES:
        rs = [r for r in valid if r["regime"] == regime["label"]]
        if not rs:
            continue
        m = len(rs)
        print(f"  {regime['label']:16s} {m:5d} "
              f"{sum(r['total_return_pct'] for r in rs)/m:+9.2f} "
              f"{sum(r['max_drawdown_pct'] for r in rs)/m:8.2f} "
              f"{sum(r['win_rate'] for r in rs)/m*100:7.1f} "
              f"{sum(r['sharpe_ratio'] for r in rs)/m:+8.2f} "
              f"{sum(r['profit_factor'] for r in rs)/m:8.2f}")

    with open(OUTPUT_PATH, "w") as f:
        json.dump({
            "summary": {
                "symbols": len(SYMBOLS), "regimes": len(REGIMES), "seeds": len(SEEDS),
                "bars_per_run": BARS, "total_runs": total, "valid_runs": n,
                "error_runs": len(errors), "total_trades": total_trades,
                "profitable_runs": profitable,
                "avg_return_pct": round(avg_return, 2), "best_return_pct": round(best_ret, 2),
                "worst_return_pct": round(worst_ret, 2), "avg_max_drawdown_pct": round(avg_dd, 2),
                "worst_drawdown_pct": round(worst_dd, 2), "avg_win_rate": round(avg_wr, 4),
                "avg_sharpe": round(avg_sharpe, 2), "avg_sortino": round(avg_sortino, 2),
                "avg_profit_factor": round(avg_pf, 2), "crashed_runs": crashed,
                "total_commission": round(total_commission, 2),
                "total_slippage": round(total_slippage, 2),
                "runtime_seconds": round(elapsed, 1),
            },
            "results": all_results,
        }, f, indent=2, default=str)
    print(f"\nFull results saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
