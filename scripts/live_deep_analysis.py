"""
RUNECLAW Deep Live Analysis — exercises full pipeline against Bitget live data.

Runs: scanner → OHLCV fetch → analyzer (TA + LLM) → risk gate → exchange flow → quant report.
"""

from __future__ import annotations
import asyncio
import json
import sys
import os
import time

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bot.config import CONFIG
from bot.core.market_scanner import MarketScanner
from bot.core.analyzer import Analyzer
from bot.core.engine import RuneClawEngine
from bot.core.exchange_flow import ExchangeFlowProvider
from bot.risk.risk_engine import RiskEngine
from bot.risk.portfolio import PortfolioTracker
from bot.utils.models import MarketSignal, Direction
from bot.utils.logger import audit, system_log

import ccxt.async_support as ccxt


# ── Formatting helpers ──────────────────────────────────────

def fmt_pct(v, decimals=2):
    if v is None: return "N/A"
    return f"{v:+.{decimals}f}%"

def fmt_usd(v):
    if v is None: return "N/A"
    if v >= 1e9: return f"${v/1e9:.2f}B"
    if v >= 1e6: return f"${v/1e6:.2f}M"
    if v >= 1e3: return f"${v/1e3:.1f}K"
    return f"${v:.2f}"

def divider(title=""):
    w = 60
    if title:
        pad = (w - len(title) - 2) // 2
        return f"\n{'='*pad} {title} {'='*pad}"
    return "=" * w


# ── Main analysis ───────────────────────────────────────────

async def main():
    print(divider("RUNECLAW DEEP LIVE ANALYSIS"))
    print(f"  Timestamp : {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print(f"  Exchange  : Bitget (live)")
    print(f"  Mode      : SIMULATION (read-only)")
    print(divider())

    # 1. Exchange connection
    exchange = ccxt.bitget({
        "apiKey": CONFIG.exchange.api_key,
        "secret": CONFIG.exchange.api_secret,
        "password": CONFIG.exchange.passphrase,
        "sandbox": CONFIG.exchange.sandbox,
        "timeout": 30000,
        "enableRateLimit": True,
    })

    # 2. Scan market
    print(divider("PHASE 1: MARKET SCAN"))
    scanner = MarketScanner()
    signals = await scanner.scan()
    print(f"  Detected {len(signals)} signals\n")

    # Pick top 5 by absolute momentum for deep analysis
    # Also ensure major pairs are included for comprehensive testing
    top_signals = signals[:5]

    # Add major pairs (BTC, ETH, SOL) if not already in the list
    major_symbols = {"BTC/USDT", "ETH/USDT", "SOL/USDT"}
    existing = {s.symbol for s in top_signals}

    # Build major signals from ticker data
    try:
        tickers = await exchange.fetch_tickers()
        for msym in major_symbols - existing:
            if msym in tickers:
                t = tickers[msym]
                top_signals.append(MarketSignal(
                    symbol=msym,
                    price=float(t.get("last", 0) or 0),
                    change_pct_24h=round(float(t.get("percentage", 0) or 0), 2),
                    volume_usd_24h=round(float(t.get("quoteVolume", 0) or 0), 2),
                    volume_spike=False,
                    momentum_score=0.0,
                    timestamp=__import__("datetime").datetime.now(__import__("datetime").UTC),
                ))
    except Exception as e:
        print(f"  (Could not add major pairs: {e})")
    for i, s in enumerate(top_signals, 1):
        vol_flag = " [VOL SPIKE]" if s.volume_spike else ""
        print(f"  {i}. {s.symbol:<16} ${s.price:<12.6f} {fmt_pct(s.change_pct_24h):>8}  mom={s.momentum_score:+.3f}{vol_flag}")

    # 3. Exchange flow provider
    def _exchange_factory():
        return ccxt.bitget({
            "apiKey": CONFIG.exchange.api_key,
            "secret": CONFIG.exchange.api_secret,
            "password": CONFIG.exchange.passphrase,
            "sandbox": False,
            "timeout": 30000,
            "enableRateLimit": True,
        })

    flow_provider = ExchangeFlowProvider(exchange_factory=_exchange_factory)

    # 4. Deep analysis per symbol
    analyzer = Analyzer()
    portfolio = PortfolioTracker(initial_balance=CONFIG.paper_balance_usd)
    risk_engine = RiskEngine(portfolio)

    results = []

    for sig in top_signals:
        print(divider(f"PHASE 2: DEEP ANALYSIS — {sig.symbol}"))

        # 4a. Fetch OHLCV candles (1h, 100 bars)
        try:
            candles_1h = await exchange.fetch_ohlcv(sig.symbol, "1h", limit=100)
            print(f"  Candles (1h) : {len(candles_1h)} bars loaded")
        except Exception as e:
            print(f"  Candles (1h) : FAILED — {e}")
            candles_1h = []

        # 4b. Fetch 4h candles for MTF
        try:
            candles_4h = await exchange.fetch_ohlcv(sig.symbol, "4h", limit=50)
            print(f"  Candles (4h) : {len(candles_4h)} bars loaded")
        except Exception as e:
            print(f"  Candles (4h) : FAILED — {e}")
            candles_4h = None

        # 4c. Fetch 1d candles for MTF
        try:
            candles_1d = await exchange.fetch_ohlcv(sig.symbol, "1d", limit=30)
            print(f"  Candles (1d) : {len(candles_1d)} bars loaded")
        except Exception as e:
            print(f"  Candles (1d) : FAILED — {e}")
            candles_1d = None

        if len(candles_1h) < 30:
            print(f"  SKIP: insufficient candle data ({len(candles_1h)} < 30)")
            continue

        # 4d. Exchange flow (funding rate + OI)
        print(f"\n  --- Exchange Flow ---")
        try:
            flow_summary = await flow_provider.get_flow_summary(sig.symbol)
            fr = flow_summary.get("funding_rate")
            oi = flow_summary.get("oi_usd")
            oi_chg = flow_summary.get("oi_change_pct")
            trend = flow_summary.get("funding_trend", "N/A")
            squeeze = flow_summary.get("squeeze_risk", "NONE")
            interp = flow_summary.get("interpretation", "")

            print(f"  Funding Rate  : {fr*100:+.4f}%/8h" if fr else "  Funding Rate  : N/A")
            print(f"  Open Interest : {fmt_usd(oi)}" if oi else "  Open Interest : N/A")
            print(f"  OI Change     : {fmt_pct(oi_chg)}" if oi_chg is not None else "  OI Change     : N/A")
            print(f"  Funding Trend : {trend}")
            print(f"  Squeeze Risk  : {squeeze}")
            if interp:
                print(f"  Assessment    : {interp}")
        except Exception as e:
            print(f"  Exchange flow error: {e}")
            flow_summary = {}

        # 4e. Run full analyzer (TA + LLM)
        print(f"\n  --- Technical Analysis + AI ---")

        # Pre-compute ATR from candles for the risk engine
        import numpy as np
        atr_val = None
        if len(candles_1h) >= 14:
            highs = np.array([c[2] for c in candles_1h], dtype=float)
            lows = np.array([c[3] for c in candles_1h], dtype=float)
            closes = np.array([c[4] for c in candles_1h], dtype=float)
            tr = np.maximum(
                highs[1:] - lows[1:],
                np.maximum(
                    np.abs(highs[1:] - closes[:-1]),
                    np.abs(lows[1:] - closes[:-1])
                )
            )
            atr_val = float(np.mean(tr[-14:]))
            print(f"  ATR (14)      : ${atr_val:.6f} ({atr_val/sig.price*100:.2f}% of price)")

        try:
            idea = await analyzer.analyze(
                sig, candles_1h,
                candles_4h=candles_4h,
                candles_1d=candles_1d,
            )
            if idea:
                print(f"  Direction     : {idea.direction.value}")
                print(f"  Confidence    : {idea.confidence:.0%}")
                print(f"  Entry         : ${idea.entry_price:.6f}")
                print(f"  Stop Loss     : ${idea.stop_loss:.6f}")
                print(f"  Take Profit   : ${idea.take_profit:.6f}")
                rr = abs(idea.take_profit - idea.entry_price) / abs(idea.entry_price - idea.stop_loss) if abs(idea.entry_price - idea.stop_loss) > 0 else 0
                print(f"  R:R Ratio     : {rr:.2f}x")
                print(f"  Reasoning     : {idea.reasoning[:120]}...")
                print(f"  Source        : {idea.source}")

                # 4f. Risk gate check
                print(f"\n  --- Risk Gate ---")
                risk_result = risk_engine.evaluate(idea, atr=atr_val)
                passed = risk_result.verdict == "APPROVED"
                failed = risk_result.checks_failed
                position_usd = risk_result.position_size_usd
                status = "APPROVED" if passed else "REJECTED"
                print(f"  Status        : {status}")
                print(f"  Position Size : {fmt_usd(position_usd)}")
                print(f"  Checks Passed : {len(risk_result.checks_passed)}")
                print(f"  Checks Failed : {len(failed)}")
                if failed:
                    for f_check in failed:
                        print(f"  FAILED        : {f_check}")

                results.append({
                    "symbol": sig.symbol,
                    "price": sig.price,
                    "change_24h": sig.change_pct_24h,
                    "direction": idea.direction.value,
                    "confidence": idea.confidence,
                    "entry": idea.entry_price,
                    "stop_loss": idea.stop_loss,
                    "take_profit": idea.take_profit,
                    "rr_ratio": round(rr, 2),
                    "risk_passed": passed,
                    "risk_failed": failed,
                    "funding_rate": flow_summary.get("funding_rate"),
                    "squeeze_risk": flow_summary.get("squeeze_risk", "NONE"),
                    "source": idea.source,
                })
            else:
                print(f"  Result: NO TRADE — conviction too low or filtered by regime")
                results.append({
                    "symbol": sig.symbol,
                    "price": sig.price,
                    "change_24h": sig.change_pct_24h,
                    "direction": "NONE",
                    "confidence": 0,
                    "risk_passed": False,
                    "reason": "Low conviction / regime filter",
                })
        except Exception as e:
            print(f"  Analyzer error: {e}")
            import traceback
            traceback.print_exc()
            results.append({
                "symbol": sig.symbol,
                "error": str(e),
            })

    # 5. Summary
    print(divider("PHASE 3: SUMMARY"))
    tradeable = [r for r in results if r.get("risk_passed")]
    filtered = [r for r in results if r.get("direction") == "NONE"]
    blocked = [r for r in results if r.get("direction") and r.get("direction") != "NONE" and not r.get("risk_passed")]

    print(f"  Symbols analyzed : {len(results)}")
    print(f"  Trade ideas      : {len(results) - len(filtered)}")
    print(f"  Risk-approved    : {len(tradeable)}")
    print(f"  Risk-blocked     : {len(blocked)}")
    print(f"  Filtered (low)   : {len(filtered)}")

    if tradeable:
        print(f"\n  ACTIONABLE SIGNALS:")
        for r in tradeable:
            print(f"    {r['symbol']:<16} {r['direction']:<6} conf={r['confidence']:.0%}  "
                  f"R:R={r.get('rr_ratio', 0):.1f}x  squeeze={r.get('squeeze_risk', 'N/A')}")

    if blocked:
        print(f"\n  BLOCKED BY RISK:")
        for r in blocked:
            fails = r.get("risk_failed", [])
            print(f"    {r['symbol']:<16} {r['direction']:<6} — {'; '.join(fails[:2])}")

    # 6. Save full results
    report_path = os.path.join(os.path.dirname(__file__), "..", "output_analysis.json")
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Full report saved to output_analysis.json")

    # Cleanup
    await exchange.close()
    await scanner.close()
    print(divider())
    print("  Analysis complete.\n")


if __name__ == "__main__":
    asyncio.run(main())
