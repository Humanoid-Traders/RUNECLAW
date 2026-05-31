"""
RUNECLAW End-to-End Live Pipeline — Full Trading Cycle Stress Test.

Exercises the COMPLETE loop against live Bitget data:
  Scan → OHLCV fetch → Order flow → LLM analysis → Risk gate → Paper trade
  → Portfolio update → Quant analysis → Feedback loop → Learning check

Reports every decision point with raw data so we can find weak links.
"""
import asyncio, sys, os, time, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
from bot.config import CONFIG
from bot.core.engine import RuneClawEngine
from bot.core.analyzer import Analyzer
from bot.core.order_flow import OrderFlowAnalyzer
from bot.core.exchange_flow import ExchangeFlowProvider
from bot.skills.skill_registry import build_default_registry
from bot.utils.models import MarketSignal, Direction
from bot.utils.logger import audit, system_log
import ccxt.async_support as ccxt


def div(t="", w=64):
    if t: return f"\n{'='*((w-len(t)-2)//2)} {t} {'='*((w-len(t)-2)//2)}"
    return "=" * w

def pct(v, d=2):
    return f"{v:+.{d}f}%" if v is not None else "N/A"

def usd(v):
    if v is None: return "N/A"
    if abs(v) >= 1e9: return f"${v/1e9:.2f}B"
    if abs(v) >= 1e6: return f"${v/1e6:.2f}M"
    if abs(v) >= 1e3: return f"${v/1e3:.1f}K"
    return f"${v:.2f}"


async def main():
    print(div("RUNECLAW END-TO-END LIVE PIPELINE"))
    print(f"  {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print(f"  Exchange: Bitget (LIVE read-only)  |  Mode: PAPER SIMULATION")
    print(div())

    engine = RuneClawEngine()
    registry = build_default_registry()
    exchange = await engine.get_exchange()
    order_flow = OrderFlowAnalyzer()
    analyzer = Analyzer()

    # Exchange flow for funding/OI
    flow = ExchangeFlowProvider(exchange_factory=lambda: ccxt.bitget({
        "apiKey": CONFIG.exchange.api_key, "secret": CONFIG.exchange.api_secret,
        "password": CONFIG.exchange.passphrase, "sandbox": False,
        "timeout": 30000, "enableRateLimit": True,
    }))

    # ═══════════════════════════════════════════════════════════════
    # PHASE 1: MARKET SCAN
    # ═══════════════════════════════════════════════════════════════
    print(div("PHASE 1: MARKET SCAN"))
    signals = await engine.scanner.scan()
    print(f"  {len(signals)} signals from Bitget spot market\n")

    # Add majors if missing
    major_syms = {"BTC/USDT", "ETH/USDT", "SOL/USDT"}
    existing = {s.symbol for s in signals}
    tickers = await exchange.fetch_tickers()
    for sym in major_syms - existing:
        if sym in tickers:
            t = tickers[sym]
            from datetime import datetime, UTC
            signals.append(MarketSignal(
                symbol=sym, price=float(t.get("last",0) or 0),
                change_pct_24h=round(float(t.get("percentage",0) or 0),2),
                volume_usd_24h=round(float(t.get("quoteVolume",0) or 0),2),
                volume_spike=False, momentum_score=0.0, timestamp=datetime.now(UTC),
            ))

    targets = signals[:8]  # top 5 movers + majors
    for i, s in enumerate(targets, 1):
        spike = " [SPIKE]" if s.volume_spike else ""
        print(f"  {i}. {s.symbol:<16} ${s.price:<14.6f} {pct(s.change_pct_24h):>8}  vol={usd(s.volume_usd_24h)}{spike}")

    # ═══════════════════════════════════════════════════════════════
    # PHASE 2: DEEP ANALYSIS PER SYMBOL
    # ═══════════════════════════════════════════════════════════════
    all_ideas = []
    all_diagnostics = []

    for sig in targets:
        print(div(f"PHASE 2: {sig.symbol}"))

        diag = {"symbol": sig.symbol, "price": sig.price, "change_24h": sig.change_pct_24h}

        # 2a. OHLCV
        candles_1h, candles_4h, candles_1d = [], None, None
        try:
            candles_1h = await exchange.fetch_ohlcv(sig.symbol, "1h", limit=100)
            candles_4h = await exchange.fetch_ohlcv(sig.symbol, "4h", limit=50)
            candles_1d = await exchange.fetch_ohlcv(sig.symbol, "1d", limit=30)
            print(f"  OHLCV: 1h={len(candles_1h)} 4h={len(candles_4h)} 1d={len(candles_1d)} bars")
        except Exception as e:
            print(f"  OHLCV fetch error: {e}")

        if len(candles_1h) < 30:
            print(f"  SKIP: insufficient data")
            diag["status"] = "SKIP_DATA"
            all_diagnostics.append(diag)
            continue

        # 2b. Compute raw indicators for diagnostics
        closes = np.array([c[4] for c in candles_1h], dtype=float)
        highs = np.array([c[2] for c in candles_1h], dtype=float)
        lows = np.array([c[3] for c in candles_1h], dtype=float)
        volumes = np.array([c[5] for c in candles_1h], dtype=float)

        atr_vals = np.maximum(highs[1:]-lows[1:], np.maximum(np.abs(highs[1:]-closes[:-1]), np.abs(lows[1:]-closes[:-1])))
        atr = float(np.mean(atr_vals[-14:]))
        atr_pct = atr / sig.price * 100

        # RSI
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains[-14:])
        avg_loss = np.mean(losses[-14:])
        rsi = 100 - (100 / (1 + avg_gain/avg_loss)) if avg_loss > 0 else 100

        # Volume trend
        vol_recent = float(np.mean(volumes[-5:]))
        vol_older = float(np.mean(volumes[-20:-5])) if len(volumes) >= 20 else float(np.mean(volumes))
        vol_ratio = vol_recent / vol_older if vol_older > 0 else 1.0

        print(f"  RSI(14)={rsi:.1f}  ATR={atr_pct:.2f}%  VolRatio={vol_ratio:.2f}x  Close=${closes[-1]:.6f}")
        diag["rsi"] = round(rsi, 1)
        diag["atr_pct"] = round(atr_pct, 2)
        diag["vol_ratio"] = round(vol_ratio, 2)

        # 2c. Order flow
        print(f"  --- Order Flow ---")
        of_signal = None
        try:
            of_signal = await order_flow.analyze(exchange, sig.symbol)
            print(f"  Score={of_signal.smart_money_score:+.2f}  Whale={of_signal.whale_bias}  "
                  f"CVD={of_signal.cvd_trend}  Imbal={of_signal.book_imbalance:+.2f}")
            diag["order_flow"] = {
                "score": round(of_signal.smart_money_score, 2),
                "whale": of_signal.whale_bias,
                "cvd": of_signal.cvd_trend,
                "imbalance": round(of_signal.book_imbalance, 2),
            }
        except Exception as e:
            print(f"  Order flow error: {e}")

        # 2d. Exchange flow (funding + OI)
        print(f"  --- Exchange Flow ---")
        try:
            flow_data = await flow.get_flow_summary(sig.symbol)
            fr = flow_data.get("funding_rate")
            oi = flow_data.get("oi_usd")
            squeeze = flow_data.get("squeeze_risk", "NONE")
            trend = flow_data.get("funding_trend", "N/A")
            print(f"  Funding={fr*100:+.4f}%/8h" if fr else "  Funding=N/A", end="")
            print(f"  OI={usd(oi)}" if oi else "  OI=N/A", end="")
            print(f"  Squeeze={squeeze}  Trend={trend}")
            diag["exchange_flow"] = {
                "funding_rate": fr, "oi_usd": oi,
                "squeeze_risk": squeeze, "funding_trend": trend,
            }
        except Exception as e:
            print(f"  Exchange flow error: {e}")

        # 2e. Full analyzer (TA + LLM)
        print(f"  --- Analyzer ---")
        try:
            idea = await analyzer.analyze(
                sig, candles_1h,
                order_flow=of_signal,
                candles_4h=candles_4h,
                candles_1d=candles_1d,
            )
            if idea:
                rr = abs(idea.take_profit - idea.entry_price) / abs(idea.entry_price - idea.stop_loss) if abs(idea.entry_price - idea.stop_loss) > 0 else 0
                print(f"  IDEA: {idea.direction.value} {sig.symbol}  conf={idea.confidence:.0%}  R:R={rr:.2f}x")
                print(f"  Entry=${idea.entry_price:.6f}  SL=${idea.stop_loss:.6f}  TP=${idea.take_profit:.6f}")
                print(f"  Source: {idea.source}  |  {idea.reasoning[:100]}...")
                diag["idea"] = {
                    "direction": idea.direction.value, "confidence": idea.confidence,
                    "entry": idea.entry_price, "sl": idea.stop_loss, "tp": idea.take_profit,
                    "rr": round(rr, 2), "source": idea.source,
                }

                # 2f. Risk gate
                print(f"  --- Risk Gate ---")
                risk_result = engine.risk.evaluate(idea, atr=atr)
                verdict = risk_result.verdict
                pos_usd = risk_result.position_size_usd
                print(f"  Verdict: {verdict}  |  Size: {usd(pos_usd)}  |  {len(risk_result.checks_passed)}P/{len(risk_result.checks_failed)}F")
                if risk_result.checks_failed:
                    for f in risk_result.checks_failed:
                        print(f"    FAIL: {f}")
                diag["risk"] = {
                    "verdict": verdict, "position_usd": pos_usd,
                    "passed": len(risk_result.checks_passed),
                    "failed": risk_result.checks_failed,
                }

                if verdict == "APPROVED":
                    all_ideas.append((idea, atr, sig, diag))
            else:
                print(f"  NO TRADE — filtered by regime or low confluence")
                diag["status"] = "FILTERED"
        except Exception as e:
            print(f"  Analyzer error: {e}")
            import traceback; traceback.print_exc()
            diag["status"] = f"ERROR: {e}"

        all_diagnostics.append(diag)

    # ═══════════════════════════════════════════════════════════════
    # PHASE 3: PAPER TRADE EXECUTION
    # ═══════════════════════════════════════════════════════════════
    print(div("PHASE 3: PAPER TRADE EXECUTION"))
    executed = []

    if not all_ideas:
        print("  No risk-approved ideas to execute.")
    else:
        for idea, atr, sig, diag in all_ideas[:3]:  # max 3 trades
            print(f"\n  Executing: {idea.direction.value} {idea.asset} @ ${idea.entry_price:.6f}")
            try:
                pos = engine.portfolio.open_position(idea)
                if pos:
                    print(f"  OPENED: {pos.asset} qty={pos.quantity:.6f} entry=${pos.entry_price:.6f}")
                    executed.append((idea, pos))
                    diag["executed"] = True
                else:
                    print(f"  FAILED to open position")
                    diag["executed"] = False
            except Exception as e:
                print(f"  Execution error: {e}")
                diag["executed"] = False

    # ═══════════════════════════════════════════════════════════════
    # PHASE 4: PORTFOLIO STATE
    # ═══════════════════════════════════════════════════════════════
    print(div("PHASE 4: PORTFOLIO STATE"))
    port_skill = registry.get("get_portfolio")
    port_out = await port_skill.execute(engine)
    # Strip HTML for readability
    import re
    clean = re.sub(r'<[^>]+>', '', port_out)
    for line in clean.split('\n'):
        if line.strip():
            print(f"  {line.strip()}")

    # ═══════════════════════════════════════════════════════════════
    # PHASE 5: MARK-TO-MARKET + STOP CHECK
    # ═══════════════════════════════════════════════════════════════
    if executed:
        print(div("PHASE 5: MARK-TO-MARKET"))
        # Fetch fresh prices
        fresh_tickers = await exchange.fetch_tickers()
        prices = {}
        for idea, pos in executed:
            sym = pos.asset
            if sym in fresh_tickers:
                prices[sym] = float(fresh_tickers[sym].get("last", pos.entry_price))

        engine.portfolio.mark_to_market(prices)
        state = engine.portfolio.get_state()
        print(f"  Equity:       {usd(state.equity_usd)}")
        print(f"  Unrealized:   {usd(state.unrealized_pnl)}")
        print(f"  Positions:    {state.open_positions}")
        print(f"  Exposure:     {state.exposure_pct:.1f}%")

        # Check stops
        stopped = engine.portfolio.check_stops(prices)
        if stopped:
            print(f"  STOPPED OUT:  {[s.asset for s in stopped]}")

    # ═══════════════════════════════════════════════════════════════
    # PHASE 6: QUANT ANALYSIS ON TRADED SYMBOLS
    # ═══════════════════════════════════════════════════════════════
    print(div("PHASE 6: QUANT DEPTH"))
    quant_skill = registry.get("quant_analyze")
    for idea, pos in executed[:2]:
        print(f"\n  --- {pos.asset} ---")
        qout = await quant_skill.execute(engine, symbol=pos.asset)
        # Print key lines
        for line in qout.split('\n'):
            if any(k in line for k in ['Regime', 'ADX', 'Hurst', 'GARCH', 'Score', 'GATE']):
                print(f"  {line.strip()}")

    # Also run quant on BTC if not already
    if not any(p.asset == "BTC/USDT" for _, p in executed):
        print(f"\n  --- BTC/USDT (benchmark) ---")
        qout = await quant_skill.execute(engine, symbol="BTC/USDT")
        for line in qout.split('\n'):
            if any(k in line for k in ['Regime', 'ADX', 'Hurst', 'GARCH', 'Score', 'GATE']):
                print(f"  {line.strip()}")

    # ═══════════════════════════════════════════════════════════════
    # PHASE 7: LEARNING SYSTEM FEED
    # ═══════════════════════════════════════════════════════════════
    print(div("PHASE 7: LEARNING SYSTEM"))
    learn_out = await registry.get("learning").execute(engine)
    clean = re.sub(r'<[^>]+>', '', learn_out)
    for line in clean.split('\n')[:15]:
        if line.strip():
            print(f"  {line.strip()}")

    # ═══════════════════════════════════════════════════════════════
    # PHASE 8: FULL DIAGNOSTICS SUMMARY
    # ═══════════════════════════════════════════════════════════════
    print(div("PHASE 8: DIAGNOSTICS SUMMARY"))

    approved = [d for d in all_diagnostics if d.get("risk", {}).get("verdict") == "APPROVED"]
    rejected = [d for d in all_diagnostics if d.get("risk", {}).get("verdict") == "REJECTED"]
    filtered = [d for d in all_diagnostics if d.get("status") == "FILTERED"]
    skipped = [d for d in all_diagnostics if d.get("status") == "SKIP_DATA"]
    errored = [d for d in all_diagnostics if str(d.get("status","")).startswith("ERROR")]

    print(f"  Symbols scanned  : {len(targets)}")
    print(f"  Trade ideas      : {len(approved) + len(rejected)}")
    print(f"  Risk APPROVED    : {len(approved)}")
    print(f"  Risk REJECTED    : {len(rejected)}")
    print(f"  Filtered (low)   : {len(filtered)}")
    print(f"  Skipped (data)   : {len(skipped)}")
    print(f"  Errors           : {len(errored)}")
    print(f"  Trades executed  : {len(executed)}")

    if approved:
        print(f"\n  APPROVED TRADES:")
        for d in approved:
            idea = d.get("idea", {})
            risk = d.get("risk", {})
            ef = d.get("exchange_flow", {})
            print(f"    {d['symbol']:<16} {idea.get('direction','?'):<6} conf={idea.get('confidence',0):.0%}  "
                  f"R:R={idea.get('rr',0):.1f}x  size={usd(risk.get('position_usd',0))}  "
                  f"squeeze={ef.get('squeeze_risk','N/A')}")

    if rejected:
        print(f"\n  REJECTED BY RISK:")
        for d in rejected:
            fails = d.get("risk", {}).get("failed", [])
            print(f"    {d['symbol']:<16} — {'; '.join(fails[:2])}")

    if filtered:
        print(f"\n  FILTERED (regime/confidence):")
        for d in filtered:
            print(f"    {d['symbol']:<16} RSI={d.get('rsi','?')}  ATR={d.get('atr_pct','?')}%  VolR={d.get('vol_ratio','?')}")

    # Weakness analysis
    print(f"\n  WEAKNESS ANALYSIS:")
    filter_reasons = []
    if len(filtered) > len(targets) * 0.6:
        filter_reasons.append("HIGH FILTER RATE — most symbols filtered by regime/confluence")
    if all(d.get("idea",{}).get("direction") == "SHORT" for d in approved + rejected):
        filter_reasons.append("DIRECTIONAL BIAS — all ideas are SHORT, no LONG ideas generated")
    if len(approved) == 0:
        filter_reasons.append("NO APPROVED TRADES — risk gate blocked everything")
    no_flow = [d for d in all_diagnostics if not d.get("exchange_flow", {}).get("funding_rate")]
    if len(no_flow) > len(targets) * 0.5:
        filter_reasons.append(f"EXCHANGE FLOW GAPS — {len(no_flow)}/{len(targets)} symbols have no funding/OI data")

    for r in filter_reasons:
        print(f"    ⚠ {r}")
    if not filter_reasons:
        print(f"    ✓ No major weaknesses detected")

    # Save full report
    report = {
        "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        "symbols_scanned": len(targets),
        "ideas": len(approved) + len(rejected),
        "approved": len(approved),
        "rejected": len(rejected),
        "filtered": len(filtered),
        "executed": len(executed),
        "diagnostics": all_diagnostics,
        "weaknesses": filter_reasons,
    }
    with open("e2e_report.json", "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n  Full report saved to e2e_report.json")

    await engine.stop()
    print(div())
    print("  Pipeline complete.\n")


if __name__ == "__main__":
    asyncio.run(main())
