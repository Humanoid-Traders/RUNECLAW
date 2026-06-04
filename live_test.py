"""
RUNECLAW Live Data Integration Test
Exercises the full pipeline against real Bitget market data:
  1. Exchange connectivity
  2. Market scanner (top movers)
  3. Full analysis on a real asset
  4. Risk engine evaluation
  5. Order flow analysis
  6. Portfolio paper trade simulation
"""

import asyncio
import json
import sys
import time
from datetime import datetime

# Ensure we're in the right directory
sys.path.insert(0, ".")

from bot.compat import UTC
from bot.config import CONFIG
from bot.core.engine import RuneClawEngine
from bot.core.market_scanner import MarketScanner
from bot.core.analyzer import Analyzer
from bot.core.order_flow import OrderFlowAnalyzer
from bot.risk.risk_engine import RiskEngine
from bot.risk.portfolio import PortfolioTracker
from bot.utils.models import MarketSignal, TradeIdea, RiskVerdict


# ── Formatting helpers ──────────────────────────────────────────

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"
DIM = "\033[2m"

def header(text):
    print(f"\n{BOLD}{CYAN}{'═'*60}{RESET}")
    print(f"{BOLD}{CYAN}  {text}{RESET}")
    print(f"{BOLD}{CYAN}{'═'*60}{RESET}")

def step(n, text):
    print(f"\n{BOLD}[{n}] {text}{RESET}")

def ok(text):
    print(f"  {GREEN}✓{RESET} {text}")

def warn(text):
    print(f"  {YELLOW}⚠{RESET} {text}")

def fail(text):
    print(f"  {RED}✗{RESET} {text}")

def info(text):
    print(f"  {DIM}{text}{RESET}")


results = {}


async def test_exchange_connectivity():
    """Test 1: Can we connect to Bitget and fetch data?"""
    step(1, "Exchange Connectivity")

    scanner = MarketScanner()
    exchange = await scanner._get_exchange()

    # Test basic connectivity
    markets = await exchange.load_markets()
    usdt_pairs = [s for s in markets if s.endswith("/USDT")]
    ok(f"Connected to Bitget — {len(markets)} markets loaded, {len(usdt_pairs)} USDT pairs")
    results["markets"] = len(markets)
    results["usdt_pairs"] = len(usdt_pairs)

    # Fetch BTC ticker
    ticker = await exchange.fetch_ticker("BTC/USDT")
    btc_price = float(ticker["last"])
    btc_change = float(ticker.get("percentage", 0) or 0)
    btc_volume = float(ticker.get("quoteVolume", 0) or 0)
    ok(f"BTC/USDT: ${btc_price:,.2f} ({btc_change:+.2f}%) — Vol: ${btc_volume/1e6:,.0f}M")
    results["btc_price"] = btc_price

    # Fetch ETH ticker
    ticker_eth = await exchange.fetch_ticker("ETH/USDT")
    eth_price = float(ticker_eth["last"])
    eth_change = float(ticker_eth.get("percentage", 0) or 0)
    ok(f"ETH/USDT: ${eth_price:,.2f} ({eth_change:+.2f}%)")
    results["eth_price"] = eth_price

    # Fetch SOL ticker
    ticker_sol = await exchange.fetch_ticker("SOL/USDT")
    sol_price = float(ticker_sol["last"])
    sol_change = float(ticker_sol.get("percentage", 0) or 0)
    ok(f"SOL/USDT: ${sol_price:,.2f} ({sol_change:+.2f}%)")
    results["sol_price"] = sol_price

    await scanner.close()
    return True


async def test_market_scanner():
    """Test 2: Does the scanner find real movers?"""
    step(2, "Market Scanner — Top Movers")

    scanner = MarketScanner()
    t0 = time.monotonic()
    signals = await scanner.scan()
    elapsed = time.monotonic() - t0

    ok(f"Scan completed in {elapsed:.1f}s — {len(signals)} signals")
    results["scan_signals"] = len(signals)
    results["scan_time"] = round(elapsed, 1)

    # Show top 5 movers
    for i, sig in enumerate(signals[:5]):
        vol_m = sig.volume_usd_24h / 1e6 if sig.volume_usd_24h else 0
        spike = "🔥" if sig.volume_spike else "  "
        print(f"  {spike} {i+1}. {sig.symbol:<14} ${sig.price:>10,.4f}  "
              f"{sig.change_pct_24h:>+6.1f}%  Vol: ${vol_m:>6,.0f}M")

    results["top_mover"] = signals[0].symbol if signals else "N/A"
    results["top_mover_change"] = signals[0].change_pct_24h if signals else 0

    await scanner.close()
    return signals


async def test_ohlcv_fetch(symbol="BTC/USDT"):
    """Test 3: Fetch and validate OHLCV candles."""
    step(3, f"OHLCV Candles — {symbol}")

    scanner = MarketScanner()
    exchange = await scanner._get_exchange()

    # Fetch 1h candles
    ohlcv = await exchange.fetch_ohlcv(symbol, "1h", limit=100)
    ok(f"Fetched {len(ohlcv)} 1h candles")

    if ohlcv:
        latest = ohlcv[-1]
        ts = datetime.fromtimestamp(latest[0] / 1000, tz=UTC)
        o, h, l, c, v = latest[1], latest[2], latest[3], latest[4], latest[5]
        ok(f"Latest: {ts.strftime('%Y-%m-%d %H:%M')} UTC")
        ok(f"O=${o:,.2f} H=${h:,.2f} L=${l:,.2f} C=${c:,.2f}")
        ok(f"Volume: {v:,.2f}")

        # Validate candle integrity
        issues = []
        for i, bar in enumerate(ohlcv):
            if bar[2] < bar[3]:  # high < low
                issues.append(f"Bar {i}: high < low")
            if bar[1] <= 0 or bar[4] <= 0:
                issues.append(f"Bar {i}: zero/negative price")
        if issues:
            fail(f"Data issues: {issues[:3]}")
        else:
            ok(f"All {len(ohlcv)} candles pass integrity check")

    results["ohlcv_bars"] = len(ohlcv)
    await scanner.close()
    return ohlcv


async def test_full_analysis(symbol="BTC/USDT", ohlcv=None):
    """Test 4: Run the full AI analyzer on a real asset."""
    step(4, f"Full Analysis Pipeline — {symbol}")

    scanner = MarketScanner()
    exchange = await scanner._get_exchange()

    if ohlcv is None:
        ohlcv = await exchange.fetch_ohlcv(symbol, "1h", limit=100)

    ticker = await exchange.fetch_ticker(symbol)
    price = float(ticker["last"])
    change = float(ticker.get("percentage", 0) or 0)
    volume = float(ticker.get("quoteVolume", 0) or 0)

    signal = MarketSignal(
        symbol=symbol,
        price=price,
        change_pct_24h=change,
        volume_usd_24h=volume,
        volume_spike=False,
        timestamp=datetime.now(UTC),
    )

    # Run analyzer
    analyzer = Analyzer()
    t0 = time.monotonic()
    idea = await analyzer.analyze(signal, ohlcv)
    elapsed = time.monotonic() - t0

    if idea:
        ok(f"Analysis completed in {elapsed:.1f}s")
        ok(f"Direction: {idea.direction.value}")
        ok(f"Confidence: {idea.confidence:.0%}")
        ok(f"Entry: ${idea.entry_price:,.2f}")
        ok(f"Stop Loss: ${idea.stop_loss:,.2f}")
        ok(f"Take Profit: ${idea.take_profit:,.2f}")
        ok(f"Risk/Reward: {idea.risk_reward_ratio:.2f}x")
        info(f"Reasoning: {idea.reasoning[:120]}...")
        results["analysis_direction"] = idea.direction.value
        results["analysis_confidence"] = idea.confidence
        results["analysis_rr"] = idea.risk_reward_ratio
    else:
        warn(f"No trade idea generated (confidence too low or filtered)")
        results["analysis_direction"] = "FILTERED"
        results["analysis_confidence"] = 0

    results["analysis_time"] = round(elapsed, 1)
    await scanner.close()
    return idea


async def test_order_flow(symbol="BTC/USDT"):
    """Test 5: Order flow analysis — book depth, CVD, funding."""
    step(5, f"Order Flow Analysis — {symbol}")

    scanner = MarketScanner()
    exchange = await scanner._get_exchange()
    of = OrderFlowAnalyzer()

    t0 = time.monotonic()
    try:
        signal = await of.analyze(exchange, symbol)
        elapsed = time.monotonic() - t0

        if signal:
            ok(f"Order flow completed in {elapsed:.1f}s")
            ok(f"Book imbalance: {signal.book_imbalance:+.2f} "
               f"({'bid-heavy' if signal.book_imbalance > 0 else 'ask-heavy'})")
            ok(f"Bid depth: ${signal.bid_depth_usd/1e6:,.1f}M  |  Ask depth: ${signal.ask_depth_usd/1e6:,.1f}M")
            ok(f"Spread: {signal.spread_bps:.1f} bps")
            if signal.cvd_trend != "flat":
                ok(f"CVD trend: {signal.cvd_trend}  |  Divergence: {signal.cvd_price_divergence}")
            if signal.funding_rate is not None:
                ok(f"Funding rate: {signal.funding_rate:.4%}")
            if signal.whale_trade_count > 0:
                ok(f"Whale trades: {signal.whale_trade_count} (bias: {signal.whale_bias})")
            ok(f"Smart money score: {signal.smart_money_score:+.2f}  |  Confidence: {signal.confidence:.0%}")

            # Liquidity guard check
            liq_reason = of.liquidity_guard(signal)
            if liq_reason:
                warn(f"Liquidity guard: {liq_reason}")
            else:
                ok(f"Liquidity guard: PASS")

            # Confluence votes
            votes = signal.to_confluence_votes()
            ok(f"Confluence votes: {len(votes)} signals")
            for v in votes:
                info(f"  {v}")

            results["book_imbalance"] = round(signal.book_imbalance, 3)
            results["bid_depth_m"] = round(signal.bid_depth_usd / 1e6, 1)
            results["ask_depth_m"] = round(signal.ask_depth_usd / 1e6, 1)
            results["smart_money_score"] = round(signal.smart_money_score, 3)
            results["liquidity_ok"] = liq_reason is None
        else:
            warn("No order flow data returned")
    except Exception as exc:
        warn(f"Order flow error (expected for some pairs): {exc}")
        results["book_imbalance"] = "N/A"

    results["order_flow_time"] = round(time.monotonic() - t0, 1)
    await scanner.close()


async def test_risk_engine(idea=None):
    """Test 6: Risk engine evaluation on a real trade idea."""
    step(6, "Risk Engine — 20-Check Gate")

    portfolio = PortfolioTracker()
    risk = RiskEngine(portfolio)
    portfolio._on_trade_close = risk.record_trade_result

    if idea is None:
        # Create a synthetic idea for testing
        info("No live idea — creating synthetic BTC LONG for risk check")
        idea = TradeIdea(
            asset="BTC/USDT",
            direction="LONG",
            confidence=0.72,
            entry_price=results.get("btc_price", 100000),
            stop_loss=results.get("btc_price", 100000) * 0.975,
            take_profit=results.get("btc_price", 100000) * 1.04,
            reasoning="Live data integration test",
        )

    # Compute ATR from recent candles
    atr_value = results.get("btc_price", 100000) * 0.015  # ~1.5% ATR estimate

    t0 = time.monotonic()
    verdict = risk.evaluate(idea, atr=atr_value)
    elapsed = time.monotonic() - t0

    if verdict.verdict == RiskVerdict.APPROVED:
        ok(f"Verdict: APPROVED in {elapsed*1000:.0f}ms")
        ok(f"Position size: ${verdict.position_size_usd:,.2f}")
    else:
        warn(f"Verdict: REJECTED — {verdict.reason}")

    ok(f"Checks passed: {len(verdict.checks_passed)}")
    for c in verdict.checks_passed:
        info(f"  ✓ {c}")

    if verdict.checks_failed:
        warn(f"Checks failed: {len(verdict.checks_failed)}")
        for c in verdict.checks_failed:
            info(f"  ✗ {c}")

    results["risk_verdict"] = verdict.verdict.value
    results["risk_passed"] = len(verdict.checks_passed)
    results["risk_failed"] = len(verdict.checks_failed)
    results["position_size"] = verdict.position_size_usd if verdict.verdict == RiskVerdict.APPROVED else 0

    return verdict


async def test_paper_trade(idea=None):
    """Test 7: Execute a paper trade and check portfolio state."""
    step(7, "Paper Trade Execution")

    if idea is None:
        info("No live idea — creating synthetic for paper trade test")
        idea = TradeIdea(
            asset="BTC/USDT",
            direction="LONG",
            confidence=0.72,
            entry_price=results.get("btc_price", 100000),
            stop_loss=results.get("btc_price", 100000) * 0.975,
            take_profit=results.get("btc_price", 100000) * 1.04,
            reasoning="Live data integration test",
        )

    portfolio = PortfolioTracker()
    snap_before = portfolio.snapshot()
    ok(f"Balance before: ${snap_before.equity_usd:,.2f}")

    # Open position
    size_usd = min(200.0, snap_before.equity_usd * 0.02)  # 2% of equity
    trade = portfolio.open_position(idea, size_usd)
    ok(f"Opened {trade.direction.value} {trade.asset}")
    ok(f"Size: ${size_usd:,.2f}  Entry: ${trade.entry_price:,.2f}")
    ok(f"SL: ${trade.stop_loss:,.2f}  TP: ${trade.take_profit:,.2f}")

    snap_after = portfolio.snapshot()
    ok(f"Open positions: {snap_after.open_positions}")
    ok(f"Balance after entry: ${snap_after.equity_usd:,.2f}")

    # Mark-to-market with current price
    current_price = results.get("btc_price", idea.entry_price)
    portfolio.mark_to_market({"BTC/USDT": current_price})
    snap_mtm = portfolio.snapshot()
    ok(f"Equity after MTM: ${snap_mtm.equity_usd:,.2f}")

    results["paper_trade_id"] = trade.trade_id
    results["paper_trade_size"] = size_usd
    results["paper_positions"] = snap_after.open_positions

    return portfolio


async def test_multi_asset_scan():
    """Test 8: Scan and analyze multiple assets from the real scanner."""
    step(8, "Multi-Asset Pipeline (Top 3 Movers)")

    engine = RuneClawEngine()
    scanner = engine.scanner
    signals = await scanner.scan()

    if not signals:
        warn("No signals from scanner")
        return

    analyzed = []
    for sig in signals[:3]:
        try:
            exchange = await scanner._get_exchange()
            ohlcv = await exchange.fetch_ohlcv(sig.symbol, "1h", limit=100)
            idea = await engine.analyzer.analyze(sig, ohlcv)

            status = "IDEA" if idea else "SKIP"
            conf = f"{idea.confidence:.0%}" if idea else "low"
            direction = idea.direction.value if idea else "-"
            vol_m = sig.volume_usd_24h / 1e6 if sig.volume_usd_24h else 0

            print(f"  {'🟢' if idea else '⚪'} {sig.symbol:<14} "
                  f"${sig.price:>10,.4f}  {sig.change_pct_24h:>+6.1f}%  "
                  f"Vol: ${vol_m:>6,.0f}M  → {status} {direction} {conf}")

            if idea:
                analyzed.append({
                    "symbol": sig.symbol,
                    "direction": idea.direction.value,
                    "confidence": idea.confidence,
                    "entry": idea.entry_price,
                    "sl": idea.stop_loss,
                    "tp": idea.take_profit,
                    "rr": idea.risk_reward_ratio,
                })
        except Exception as exc:
            warn(f"{sig.symbol}: {exc}")

    results["multi_asset_analyzed"] = len(analyzed)
    results["multi_asset_ideas"] = analyzed

    await engine.stop()


async def main():
    header("RUNECLAW Live Data Integration Test")
    print(f"  {DIM}Exchange: Bitget  |  Mode: {'SIMULATION' if CONFIG.simulation_mode else 'LIVE'}  |  "
          f"Balance: ${CONFIG.paper_balance_usd:,.0f}{RESET}")
    print(f"  {DIM}{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}{RESET}")

    t_start = time.monotonic()

    try:
        await test_exchange_connectivity()
        signals = await test_market_scanner()
        ohlcv = await test_ohlcv_fetch("BTC/USDT")
        idea = await test_full_analysis("BTC/USDT", ohlcv)
        await test_order_flow("BTC/USDT")
        verdict = await test_risk_engine(idea)
        await test_paper_trade(idea)
        await test_multi_asset_scan()
    except Exception as exc:
        fail(f"Test failed: {exc}")
        import traceback
        traceback.print_exc()

    total_time = time.monotonic() - t_start

    # Summary
    header("Test Summary")
    print(f"  Total time: {total_time:.1f}s")
    print(f"  Markets: {results.get('markets', 0)} | USDT pairs: {results.get('usdt_pairs', 0)}")
    print(f"  BTC: ${results.get('btc_price', 0):,.2f} | ETH: ${results.get('eth_price', 0):,.2f} | SOL: ${results.get('sol_price', 0):,.2f}")
    print(f"  Scanner: {results.get('scan_signals', 0)} signals in {results.get('scan_time', 0)}s")
    print(f"  Top mover: {results.get('top_mover', 'N/A')} ({results.get('top_mover_change', 0):+.1f}%)")
    print(f"  Analysis: {results.get('analysis_direction', 'N/A')} conf={results.get('analysis_confidence', 0):.0%}")
    print(f"  Risk: {results.get('risk_verdict', 'N/A')} ({results.get('risk_passed', 0)} passed / {results.get('risk_failed', 0)} failed)")
    print(f"  Paper trade: {'✓' if results.get('paper_trade_id') else '✗'}")
    print(f"  Multi-asset: {results.get('multi_asset_analyzed', 0)} ideas from top 3 movers")
    print()

    # Save results JSON
    results["total_time"] = round(total_time, 1)
    results["timestamp"] = datetime.now(UTC).isoformat()
    with open("live_test_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    ok("Results saved to live_test_results.json")


if __name__ == "__main__":
    asyncio.run(main())
