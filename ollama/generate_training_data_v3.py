#!/usr/bin/env python3
"""
RUNECLAW - Enhanced Training Data Generator v3
===============================================
Generates 30,000+ high-quality synthetic training samples covering:

  1. Original data (from v2 logs)           — ~10K samples
  2. Multi-timeframe analysis               — 3K samples
  3. No-trade / rejection scenarios          — 3K samples
  4. Confluence scoring breakdowns           — 3K samples
  5. Risk management edge cases              — 2K samples
  6. Position sizing calculations            — 2K samples
  7. Market regime classification            — 2K samples
  8. Multi-pair correlation analysis         — 2K samples
  9. Drawdown & recovery scenarios           — 1.5K samples
  10. Chain-of-thought reasoning             — 1.5K samples

Usage:
  python generate_training_data_v3.py

Output:
  ./training_data/combined_training_v3.jsonl
"""

import json
import os
import random
import sys

random.seed(42)

OUTPUT_DIR = "./training_data"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "combined_training_v3.jsonl")

# ── Shared constants ──────────────────────────────────────────

PAIRS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
    "ADA/USDT", "AVAX/USDT", "DOGE/USDT", "DOT/USDT", "MATIC/USDT",
    "LINK/USDT", "UNI/USDT", "ATOM/USDT", "FIL/USDT", "APT/USDT",
    "ARB/USDT", "OP/USDT", "SUI/USDT", "SEI/USDT", "INJ/USDT",
    "TIA/USDT", "JUP/USDT", "WIF/USDT", "PEPE/USDT", "RENDER/USDT",
    "FET/USDT", "NEAR/USDT", "ICP/USDT", "RUNE/USDT", "STX/USDT",
]

TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d", "1w"]

INDICATORS = [
    "RSI", "MACD", "Bollinger Bands", "EMA Cross", "Volume Profile",
    "OBV", "Stochastic RSI", "ADX", "Ichimoku Cloud", "VWAP",
    "Fibonacci Retracement", "ATR",
]

RISK_CHECKS = [
    "max_position_size", "portfolio_heat", "correlation_limit",
    "drawdown_threshold", "volatility_filter", "liquidity_check",
    "spread_limit", "funding_rate", "open_interest_divergence",
    "whale_activity", "exchange_reserve", "news_sentiment",
    "time_of_day", "consecutive_losses", "win_rate_threshold",
    "max_leverage", "stop_loss_required", "take_profit_required",
    "risk_reward_minimum", "daily_loss_limit", "weekly_loss_limit",
    "margin_utilization", "circuit_breaker",
]

REGIMES = ["trending_up", "trending_down", "ranging", "volatile", "accumulation", "distribution"]
DIRECTIONS = ["long", "short"]


def rand_price(base, pct=0.1):
    """Generate a random price near base. Preserves precision for small prices."""
    raw = base * (1 + random.uniform(-pct, pct))
    if base < 0.01:
        return round(raw, 8)
    elif base < 1:
        return round(raw, 6)
    elif base < 100:
        return round(raw, 4)
    return round(raw, 2)


def rand_pct(lo, hi):
    return round(random.uniform(lo, hi), 2)


def get_pair_price(pair):
    """Return a realistic base price for a pair."""
    prices = {
        "BTC": 68000, "ETH": 3800, "SOL": 180, "BNB": 620, "XRP": 0.62,
        "ADA": 0.48, "AVAX": 38, "DOGE": 0.16, "DOT": 7.5, "MATIC": 0.72,
        "LINK": 18, "UNI": 12, "ATOM": 9.5, "FIL": 6.2, "APT": 9.8,
        "ARB": 1.2, "OP": 2.8, "SUI": 1.4, "SEI": 0.45, "INJ": 28,
        "TIA": 8.5, "JUP": 1.1, "WIF": 2.8, "PEPE": 0.000012, "RENDER": 8.5,
        "FET": 2.3, "NEAR": 7.2, "ICP": 14, "RUNE": 5.5, "STX": 2.1,
    }
    symbol = pair.split("/")[0]
    return prices.get(symbol, 10.0)


# ── Generator functions ──────────────────────────────────────

def gen_trade_analysis(n=3000):
    """Detailed trade analysis with entry, exit, risk parameters."""
    samples = []
    for _ in range(n):
        pair = random.choice(PAIRS)
        tf = random.choice(["15m", "1h", "4h", "1d"])
        direction = random.choice(DIRECTIONS)
        base = get_pair_price(pair)
        entry = rand_price(base, 0.02)

        if direction == "long":
            stop = round(entry * (1 - random.uniform(0.01, 0.04)), 6)
            tp1 = round(entry * (1 + random.uniform(0.02, 0.06)), 6)
            tp2 = round(entry * (1 + random.uniform(0.06, 0.12)), 6)
        else:
            stop = round(entry * (1 + random.uniform(0.01, 0.04)), 6)
            tp1 = round(entry * (1 - random.uniform(0.02, 0.06)), 6)
            tp2 = round(entry * (1 - random.uniform(0.06, 0.12)), 6)

        risk_pct = round(abs(entry - stop) / entry * 100, 2) if entry > 0 else 1.0
        reward_pct = round(abs(tp1 - entry) / entry * 100, 2) if entry > 0 else 1.0
        rr = round(reward_pct / risk_pct, 2) if risk_pct > 0 else 0

        confluence = rand_pct(0.45, 0.95)
        n_indicators = random.randint(6, 12)
        bullish = random.randint(3, n_indicators)
        bearish = n_indicators - bullish

        active_indicators = random.sample(INDICATORS, n_indicators)
        bullish_list = active_indicators[:bullish]
        bearish_list = active_indicators[bullish:]

        regime = random.choice(REGIMES)

        instruction = f"Analyze {pair} on the {tf} timeframe for trade setups."
        output = (
            f"Trade Idea: {pair}\n"
            f"Direction: {direction.upper()}\n"
            f"Timeframe: {tf}\n"
            f"Market Regime: {regime}\n\n"
            f"Entry: {entry}\n"
            f"Stop Loss: {stop} ({risk_pct}%)\n"
            f"Take Profit 1: {tp1} ({reward_pct}%)\n"
            f"Take Profit 2: {tp2}\n"
            f"Risk:Reward: 1:{rr}\n\n"
            f"Confluence Score: {confluence} ({n_indicators} indicators)\n"
            f"Bullish ({bullish}): {', '.join(bullish_list)}\n"
            f"Bearish ({bearish}): {', '.join(bearish_list)}\n\n"
            f"Position Size: {rand_pct(1, 5)}% of portfolio\n"
            f"Confidence: {'HIGH' if confluence > 0.75 else 'MEDIUM' if confluence > 0.55 else 'LOW'}\n"
            f"Status: {'APPROVED' if confluence > 0.6 and rr >= 1.5 else 'REQUIRES_REVIEW'}\n"
            f"Circuit Breaker: {'ACTIVE' if confluence < 0.5 else 'CLEAR'}"
        )

        samples.append({"instruction": instruction, "input": "", "output": output})
    return samples


def gen_multi_timeframe(n=3000):
    """Multi-timeframe confluence analysis."""
    samples = []
    for _ in range(n):
        pair = random.choice(PAIRS)
        base = get_pair_price(pair)

        tf_analysis = {}
        for tf in ["15m", "1h", "4h", "1d"]:
            trend = random.choice(["bullish", "bearish", "neutral"])
            strength = rand_pct(0.2, 0.95)
            tf_analysis[tf] = {"trend": trend, "strength": strength}

        # Overall alignment
        trends = [v["trend"] for v in tf_analysis.values()]
        bullish_count = trends.count("bullish")
        bearish_count = trends.count("bearish")

        if bullish_count >= 3:
            alignment = "STRONG BULLISH"
            direction = "long"
        elif bearish_count >= 3:
            alignment = "STRONG BEARISH"
            direction = "short"
        elif bullish_count == 2:
            alignment = "WEAK BULLISH"
            direction = "long"
        elif bearish_count == 2:
            alignment = "WEAK BEARISH"
            direction = "short"
        else:
            alignment = "MIXED — NO TRADE"
            direction = "none"

        instruction = f"Perform multi-timeframe analysis on {pair}."

        lines = [f"Multi-Timeframe Analysis: {pair}\n"]
        for tf, data in tf_analysis.items():
            lines.append(f"  {tf}: {data['trend'].upper()} (strength: {data['strength']})")
        lines.append(f"\nAlignment: {alignment}")
        lines.append(f"Timeframes aligned: {max(bullish_count, bearish_count)}/4")

        if direction != "none":
            entry = rand_price(base, 0.01)
            lines.append(f"\nRecommendation: {direction.upper()} from {tf_analysis['4h']['trend']} 4h trend")
            lines.append(f"Entry zone: {entry}")
            lines.append(f"Confirm on: 15m for precise entry")
        else:
            lines.append(f"\nRecommendation: STAND ASIDE")
            lines.append(f"Reason: Conflicting signals across timeframes")
            lines.append(f"Re-evaluate when higher timeframes align")

        samples.append({"instruction": instruction, "input": "", "output": "\n".join(lines)})
    return samples


def gen_no_trade(n=3000):
    """Scenarios where the correct answer is NO TRADE."""
    reasons = [
        ("Low confluence score ({score}/12 indicators aligned)", "confluence_low"),
        ("Risk:reward ratio {rr}:1 below minimum 1.5:1", "rr_low"),
        ("Portfolio heat at {heat}% exceeds 6% maximum", "portfolio_heat"),
        ("Consecutive losses: {losses} (max allowed: 3)", "consec_losses"),
        ("Daily drawdown at {dd}% exceeds 2% daily limit", "daily_dd"),
        ("Weekly drawdown at {dd}% exceeds 5% weekly limit", "weekly_dd"),
        ("Spread {spread}% exceeds 0.1% maximum", "spread"),
        ("Funding rate {rate}% indicates crowded trade", "funding"),
        ("Low liquidity: 24h volume ${vol}M below $10M threshold", "liquidity"),
        ("Circuit breaker active: {reason}", "circuit_breaker"),
        ("Conflicting timeframe signals (2 bullish, 2 bearish)", "tf_conflict"),
        ("News event pending in {hours} hours — risk elevated", "news_event"),
        ("After-hours: reduced liquidity period", "after_hours"),
        ("Correlation with open {corr_pair} position too high ({corr}%)", "correlation"),
        ("Volatility spike: ATR {atr}x above 20-day average", "volatility"),
    ]

    samples = []
    for _ in range(n):
        pair = random.choice(PAIRS)
        reason_template, reason_type = random.choice(reasons)
        direction = random.choice(DIRECTIONS)

        # Fill template variables
        reason = reason_template.format(
            score=random.randint(2, 5),
            rr=rand_pct(0.5, 1.4),
            heat=rand_pct(6.1, 12.0),
            losses=random.randint(3, 7),
            dd=rand_pct(2.1, 5.0),
            spread=rand_pct(0.11, 0.5),
            rate=rand_pct(0.05, 0.3),
            vol=rand_pct(0.5, 9.9),
            reason="3 consecutive stop-outs in 4 hours",
            hours=random.randint(1, 12),
            corr_pair=random.choice([p for p in PAIRS if p != pair]),
            corr=random.randint(75, 98),
            atr=rand_pct(2.0, 5.0),
        )

        n_failed = random.randint(1, 5)
        failed_checks = random.sample(RISK_CHECKS, n_failed)

        instruction = f"Scan {pair} for {direction} trade setups."
        output = (
            f"Trade Scan: {pair} ({direction.upper()})\n"
            f"Status: REJECTED\n\n"
            f"Primary rejection: {reason}\n\n"
            f"Risk checks failed ({n_failed}/{len(RISK_CHECKS)}):\n"
        )
        for check in failed_checks:
            output += f"  FAIL: {check}\n"
        output += (
            f"\nAction: NO TRADE\n"
            f"Capital preservation takes priority.\n"
            f"Re-evaluate when conditions improve.\n"
            f"Next scan in: {random.choice(['15m', '1h', '4h'])}"
        )

        samples.append({"instruction": instruction, "input": "", "output": output})
    return samples


def gen_confluence_breakdown(n=3000):
    """Detailed confluence scoring with individual indicator analysis."""
    samples = []
    for _ in range(n):
        pair = random.choice(PAIRS)
        tf = random.choice(["1h", "4h", "1d"])
        base = get_pair_price(pair)

        scores = {}
        for ind in INDICATORS:
            signal = random.choice(["bullish", "bearish", "neutral"])
            weight = round(random.uniform(0.5, 2.0), 1)
            confidence = rand_pct(0.3, 0.99)
            scores[ind] = {"signal": signal, "weight": weight, "confidence": confidence}

        total_weight = sum(v["weight"] for v in scores.values())
        bullish_weight = sum(v["weight"] for v in scores.values() if v["signal"] == "bullish")
        bearish_weight = sum(v["weight"] for v in scores.values() if v["signal"] == "bearish")
        confluence = round(max(bullish_weight, bearish_weight) / total_weight, 2)

        instruction = f"Break down the GetClaw confluence score for {pair} on {tf}."

        lines = [
            f"GetClaw Confluence Engine: {pair} ({tf})",
            f"{'='*50}\n",
        ]
        for ind, data in scores.items():
            icon = "+" if data["signal"] == "bullish" else "-" if data["signal"] == "bearish" else "~"
            lines.append(
                f"  [{icon}] {ind}: {data['signal'].upper()} "
                f"(weight: {data['weight']}, confidence: {data['confidence']})"
            )

        lines.extend([
            f"\n{'='*50}",
            f"Bullish weight:  {bullish_weight:.1f}/{total_weight:.1f}",
            f"Bearish weight:  {bearish_weight:.1f}/{total_weight:.1f}",
            f"Neutral weight:  {round(total_weight - bullish_weight - bearish_weight, 1)}/{total_weight:.1f}",
            f"Confluence score: {confluence}",
            f"Signal: {'BULLISH' if bullish_weight > bearish_weight else 'BEARISH' if bearish_weight > bullish_weight else 'NEUTRAL'}",
            f"Strength: {'STRONG' if confluence > 0.75 else 'MODERATE' if confluence > 0.55 else 'WEAK'}",
            f"Actionable: {'YES' if confluence > 0.6 else 'NO — insufficient confluence'}",
        ])

        samples.append({"instruction": instruction, "input": "", "output": "\n".join(lines)})
    return samples


def gen_risk_edge_cases(n=2000):
    """Risk management edge cases and circuit breaker scenarios."""
    samples = []
    scenarios = [
        "flash_crash", "liquidation_cascade", "exchange_downtime",
        "api_error", "network_congestion", "whale_manipulation",
        "funding_rate_spike", "open_interest_divergence", "stablecoin_depeg",
        "regulatory_news", "max_drawdown_hit", "correlation_spike",
    ]

    for _ in range(n):
        pair = random.choice(PAIRS)
        scenario = random.choice(scenarios)
        base = get_pair_price(pair)

        instruction = f"How should RUNECLAW handle a {scenario.replace('_', ' ')} event for {pair}?"

        if scenario == "flash_crash":
            drop = random.randint(5, 30)
            output = (
                f"CIRCUIT BREAKER ACTIVATED: Flash Crash Detected\n"
                f"Pair: {pair}\n"
                f"Price drop: {drop}% in < 5 minutes\n\n"
                f"Immediate actions:\n"
                f"  1. HALT all new orders for {pair}\n"
                f"  2. Tighten stop-losses on open positions to breakeven\n"
                f"  3. Cancel all pending limit orders\n"
                f"  4. Reduce position size by 50% if in profit\n"
                f"  5. Do NOT open new positions during volatility spike\n\n"
                f"Recovery protocol:\n"
                f"  - Wait for ATR to normalize (< 2x 20-period average)\n"
                f"  - Confirm price stabilization on 15m chart (3 candles)\n"
                f"  - Resume with 25% of normal position size\n"
                f"  - Scale back to full size after 2 successful trades\n\n"
                f"Human confirmation required: YES"
            )
        elif scenario == "max_drawdown_hit":
            dd = rand_pct(3, 8)
            output = (
                f"DRAWDOWN LIMIT REACHED\n"
                f"Current drawdown: {dd}%\n"
                f"Daily limit: 2.0% | Weekly limit: 5.0%\n\n"
                f"Mandatory actions:\n"
                f"  1. CLOSE all open positions at market\n"
                f"  2. HALT all trading for remainder of {'day' if dd < 5 else 'week'}\n"
                f"  3. Generate post-mortem analysis\n"
                f"  4. Review all trades that contributed to drawdown\n"
                f"  5. Adjust position sizing: reduce by 50% for next session\n\n"
                f"Resume conditions:\n"
                f"  - {'Next trading day' if dd < 5 else 'Next Monday'}\n"
                f"  - Human review and sign-off required\n"
                f"  - Paper trade first 3 signals before going live\n\n"
                f"Capital preservation is non-negotiable."
            )
        elif scenario == "correlation_spike":
            pair2 = random.choice([p for p in PAIRS if p != pair])
            corr = random.randint(80, 98)
            output = (
                f"CORRELATION ALERT\n"
                f"{pair} and {pair2}: {corr}% correlation detected\n"
                f"Threshold: 70%\n\n"
                f"Risk: Combined exposure creates outsized directional bet.\n\n"
                f"Actions:\n"
                f"  1. Reduce total exposure: keep only the stronger setup\n"
                f"  2. If both open: close the weaker conviction trade\n"
                f"  3. Combined position must not exceed 3% portfolio risk\n"
                f"  4. Monitor correlation daily — re-evaluate if < 60%\n\n"
                f"Diversification is a core risk management principle."
            )
        else:
            output = (
                f"EVENT: {scenario.replace('_', ' ').upper()}\n"
                f"Pair: {pair}\n"
                f"Severity: {'HIGH' if random.random() > 0.5 else 'MEDIUM'}\n\n"
                f"Protocol:\n"
                f"  1. Pause all automated scanning\n"
                f"  2. Review open positions for exposure\n"
                f"  3. Tighten risk parameters by 50%\n"
                f"  4. Await human confirmation before resuming\n"
                f"  5. Log event in audit chain\n\n"
                f"RUNECLAW never acts without human confirmation in edge cases.\n"
                f"Capital preservation above all."
            )

        samples.append({"instruction": instruction, "input": "", "output": output})
    return samples


def gen_position_sizing(n=2000):
    """Position sizing calculations with Kelly criterion and risk-based sizing."""
    samples = []
    for _ in range(n):
        pair = random.choice(PAIRS)
        base = get_pair_price(pair)
        portfolio = random.choice([1000, 5000, 10000, 25000, 50000, 100000])
        risk_per_trade = rand_pct(0.5, 2.0)
        entry = rand_price(base, 0.02)
        stop_pct = rand_pct(1.0, 4.0)
        stop = round(entry * (1 - stop_pct / 100), 6)
        direction = "long"

        risk_amount = round(portfolio * risk_per_trade / 100, 2)
        price_risk = abs(entry - stop)
        position_units = round(risk_amount / price_risk, 4) if price_risk > 0 else 0
        position_value = round(position_units * entry, 2)
        position_pct = round(position_value / portfolio * 100, 2)

        # Kelly criterion
        win_rate = rand_pct(0.40, 0.65)
        avg_win = rand_pct(2.0, 6.0)
        avg_loss = rand_pct(1.0, 3.0)
        kelly = round(win_rate - (1 - win_rate) / (avg_win / avg_loss), 4)
        half_kelly = round(kelly / 2, 4)

        instruction = f"Calculate position size for a {direction} trade on {pair} with a ${portfolio:,} portfolio."
        output = (
            f"Position Sizing: {pair} ({direction.upper()})\n"
            f"{'='*45}\n\n"
            f"Portfolio:        ${portfolio:,.2f}\n"
            f"Risk per trade:   {risk_per_trade}% (${risk_amount:.2f})\n"
            f"Entry:            {entry}\n"
            f"Stop Loss:        {stop} ({stop_pct}% from entry)\n\n"
            f"--- Risk-Based Sizing ---\n"
            f"Price risk/unit:  {price_risk:.6f}\n"
            f"Position size:    {position_units} units\n"
            f"Position value:   ${position_value:,.2f}\n"
            f"Portfolio %:      {position_pct}%\n\n"
            f"--- Kelly Criterion ---\n"
            f"Win rate:         {win_rate*100:.0f}%\n"
            f"Avg win:          {avg_win}%\n"
            f"Avg loss:         {avg_loss}%\n"
            f"Full Kelly:       {kelly*100:.1f}%\n"
            f"Half Kelly:       {half_kelly*100:.1f}% (recommended)\n\n"
            f"Final size: min(risk-based, half-kelly) = "
            f"{'risk-based' if position_pct < half_kelly * 100 else 'half-kelly'}\n"
            f"Max allowed: 5% of portfolio (${portfolio * 0.05:,.2f})\n"
            f"{'APPROVED' if position_pct <= 5 else 'REDUCED to 5% cap'}"
        )

        samples.append({"instruction": instruction, "input": "", "output": output})
    return samples


def gen_market_regime(n=2000):
    """Market regime classification and strategy adaptation."""
    samples = []
    for _ in range(n):
        pair = random.choice(PAIRS)

        regime = random.choice(REGIMES)
        adx = rand_pct(10, 60)
        bb_width = rand_pct(0.5, 8.0)
        volume_ratio = rand_pct(0.3, 3.0)

        regime_data = {
            "trending_up": {
                "desc": "Strong uptrend with higher highs and higher lows",
                "strategy": "Trend following — buy dips to EMA support",
                "indicators": "ADX > 25, Price above EMA 20/50, MACD bullish",
                "risk_adj": "Normal position sizing, trail stops with ATR",
            },
            "trending_down": {
                "desc": "Strong downtrend with lower highs and lower lows",
                "strategy": "Short rallies to resistance or stand aside",
                "indicators": "ADX > 25, Price below EMA 20/50, MACD bearish",
                "risk_adj": "Reduce long exposure, favor cash or hedges",
            },
            "ranging": {
                "desc": "Sideways price action between defined support/resistance",
                "strategy": "Mean reversion — buy support, sell resistance",
                "indicators": "ADX < 20, Bollinger Band squeeze, low volume",
                "risk_adj": "Tight stops, small positions, scalp mode",
            },
            "volatile": {
                "desc": "High volatility with erratic price swings",
                "strategy": "Reduce exposure, widen stops, or stand aside",
                "indicators": "ATR spike, Bollinger Band expansion, volume surge",
                "risk_adj": "50% position size, 2x normal stop distance",
            },
            "accumulation": {
                "desc": "Institutional buying after markdown phase",
                "strategy": "Scale into longs on volume confirmation",
                "indicators": "OBV rising while price flat, Wyckoff spring pattern",
                "risk_adj": "Small initial positions, add on breakout confirmation",
            },
            "distribution": {
                "desc": "Institutional selling after markup phase",
                "strategy": "Reduce longs, prepare short setups",
                "indicators": "OBV declining while price flat, Wyckoff UTAD pattern",
                "risk_adj": "Tighten stops on longs, prepare hedge positions",
            },
        }

        rd = regime_data[regime]
        instruction = f"Classify the current market regime for {pair} and recommend strategy adjustments."
        output = (
            f"Market Regime Analysis: {pair}\n"
            f"{'='*45}\n\n"
            f"Regime: {regime.upper().replace('_', ' ')}\n"
            f"Description: {rd['desc']}\n\n"
            f"Key metrics:\n"
            f"  ADX: {adx} ({'trending' if adx > 25 else 'ranging'})\n"
            f"  Bollinger Width: {bb_width}% ({'expanded' if bb_width > 4 else 'normal' if bb_width > 2 else 'squeezed'})\n"
            f"  Volume ratio: {volume_ratio}x average ({'high' if volume_ratio > 1.5 else 'normal' if volume_ratio > 0.7 else 'low'})\n\n"
            f"Confirming indicators: {rd['indicators']}\n\n"
            f"Strategy: {rd['strategy']}\n"
            f"Risk adjustment: {rd['risk_adj']}\n\n"
            f"Regime persistence: {'HIGH' if adx > 35 else 'MODERATE' if adx > 20 else 'LOW — regime change likely'}\n"
            f"Re-classify in: {'4h' if adx < 20 else '1d'}"
        )

        samples.append({"instruction": instruction, "input": "", "output": output})
    return samples


def gen_correlation_analysis(n=2000):
    """Multi-pair correlation and portfolio construction."""
    samples = []
    for _ in range(n):
        pair1 = random.choice(PAIRS)
        pair2 = random.choice([p for p in PAIRS if p != pair1])
        pair3 = random.choice([p for p in PAIRS if p not in [pair1, pair2]])

        corr_12 = random.randint(-30, 98)
        corr_13 = random.randint(-30, 98)
        corr_23 = random.randint(-30, 98)

        instruction = f"Analyze correlation between {pair1}, {pair2}, and {pair3} for portfolio construction."
        output = (
            f"Correlation Analysis\n"
            f"{'='*45}\n\n"
            f"  {pair1} <-> {pair2}: {corr_12}% {'(HIGH)' if abs(corr_12) > 70 else '(MODERATE)' if abs(corr_12) > 40 else '(LOW)'}\n"
            f"  {pair1} <-> {pair3}: {corr_13}% {'(HIGH)' if abs(corr_13) > 70 else '(MODERATE)' if abs(corr_13) > 40 else '(LOW)'}\n"
            f"  {pair2} <-> {pair3}: {corr_23}% {'(HIGH)' if abs(corr_23) > 70 else '(MODERATE)' if abs(corr_23) > 40 else '(LOW)'}\n\n"
        )

        high_corr = any(abs(c) > 70 for c in [corr_12, corr_13, corr_23])
        if high_corr:
            output += (
                f"WARNING: High correlation detected.\n"
                f"Holding simultaneous positions creates concentrated risk.\n\n"
                f"Recommendation:\n"
                f"  - Pick the highest-conviction setup only\n"
                f"  - If holding multiple: combined risk must not exceed 3%\n"
                f"  - Monitor correlation shifts on 4h timeframe\n"
            )
        else:
            output += (
                f"Diversification: GOOD\n"
                f"These pairs offer meaningful diversification.\n\n"
                f"Recommendation:\n"
                f"  - Can hold simultaneous positions\n"
                f"  - Individual position limits apply (2% risk each)\n"
                f"  - Total portfolio heat: max 6%\n"
            )

        samples.append({"instruction": instruction, "input": "", "output": output})
    return samples


def gen_drawdown_recovery(n=1500):
    """Drawdown scenarios and recovery protocols."""
    samples = []
    for _ in range(n):
        dd_pct = rand_pct(1.5, 15.0)
        dd_type = "daily" if dd_pct < 3 else "weekly" if dd_pct < 7 else "monthly"
        n_losses = random.randint(2, 10)
        portfolio = random.choice([10000, 25000, 50000, 100000])
        dd_amount = round(portfolio * dd_pct / 100, 2)

        instruction = f"Handle a {dd_pct}% {dd_type} drawdown ({n_losses} consecutive losses)."
        output = (
            f"DRAWDOWN MANAGEMENT PROTOCOL\n"
            f"{'='*45}\n\n"
            f"Current state:\n"
            f"  Drawdown: {dd_pct}% (${dd_amount:,.2f})\n"
            f"  Period: {dd_type}\n"
            f"  Consecutive losses: {n_losses}\n"
            f"  Portfolio value: ${portfolio - dd_amount:,.2f} (from ${portfolio:,.2f})\n\n"
        )

        if dd_pct < 2:
            output += (
                f"Severity: NORMAL\n"
                f"Action: Continue trading with standard parameters.\n"
                f"Drawdowns under 2% are expected and healthy.\n"
                f"Review: Check if stops were hit at optimal levels."
            )
        elif dd_pct < 5:
            output += (
                f"Severity: ELEVATED\n"
                f"Actions:\n"
                f"  1. Reduce position size by 50% for next 5 trades\n"
                f"  2. Increase minimum confluence threshold to 0.70\n"
                f"  3. Only take A+ setups (multi-TF alignment required)\n"
                f"  4. Review last {n_losses} trades for pattern errors\n"
                f"  5. Resume normal sizing after 3 consecutive wins"
            )
        elif dd_pct < 10:
            output += (
                f"Severity: HIGH — TRADING PAUSE REQUIRED\n"
                f"Actions:\n"
                f"  1. HALT all live trading immediately\n"
                f"  2. Close any open positions at market\n"
                f"  3. Paper trade for minimum 20 signals\n"
                f"  4. Conduct full strategy audit\n"
                f"  5. Resume at 25% position size after audit\n"
                f"  6. Human sign-off required to resume live trading"
            )
        else:
            output += (
                f"Severity: CRITICAL — SYSTEM REVIEW REQUIRED\n"
                f"Actions:\n"
                f"  1. EMERGENCY HALT — all trading stopped\n"
                f"  2. Close ALL positions immediately\n"
                f"  3. Full system audit: check for bugs, data issues\n"
                f"  4. Review market regime — is strategy compatible?\n"
                f"  5. Consider strategy overhaul if regime has shifted\n"
                f"  6. Minimum 1 week cooling-off period\n"
                f"  7. Paper trade 50 signals before going live\n"
                f"  8. Resume at 10% of original position size"
            )

        samples.append({"instruction": instruction, "input": "", "output": output})
    return samples


def gen_chain_of_thought(n=1500):
    """Chain-of-thought reasoning for complex trade decisions."""
    samples = []
    for _ in range(n):
        pair = random.choice(PAIRS)
        base = get_pair_price(pair)
        direction = random.choice(DIRECTIONS)
        tf = random.choice(["1h", "4h", "1d"])

        confluence = rand_pct(0.40, 0.90)
        rr = rand_pct(0.8, 4.0)
        portfolio_heat = rand_pct(0, 8)
        regime = random.choice(REGIMES)
        consec_losses = random.randint(0, 5)

        should_trade = (
            confluence > 0.6
            and rr >= 1.5
            and portfolio_heat < 6
            and consec_losses < 3
        )

        instruction = f"Walk me through your reasoning for evaluating a {direction} setup on {pair}."
        output = (
            f"Chain-of-Thought Analysis: {pair} ({direction.upper()})\n"
            f"{'='*50}\n\n"
            f"Step 1: CONFLUENCE CHECK\n"
            f"  Score: {confluence} (threshold: 0.60)\n"
            f"  Result: {'PASS' if confluence > 0.6 else 'FAIL — insufficient indicator agreement'}\n\n"
            f"Step 2: RISK:REWARD EVALUATION\n"
            f"  R:R ratio: 1:{rr}\n"
            f"  Minimum: 1:1.5\n"
            f"  Result: {'PASS' if rr >= 1.5 else 'FAIL — reward does not justify risk'}\n\n"
            f"Step 3: PORTFOLIO RISK CHECK\n"
            f"  Current heat: {portfolio_heat}%\n"
            f"  Maximum: 6.0%\n"
            f"  Result: {'PASS' if portfolio_heat < 6 else 'FAIL — too much open risk'}\n\n"
            f"Step 4: CONSECUTIVE LOSS CHECK\n"
            f"  Recent losses: {consec_losses}\n"
            f"  Maximum: 3\n"
            f"  Result: {'PASS' if consec_losses < 3 else 'FAIL — need wins before new entries'}\n\n"
            f"Step 5: REGIME COMPATIBILITY\n"
            f"  Regime: {regime.replace('_', ' ')}\n"
            f"  Direction: {direction}\n"
            f"  Compatible: {'YES' if (direction == 'long' and regime in ['trending_up', 'accumulation']) or (direction == 'short' and regime in ['trending_down', 'distribution']) or regime == 'ranging' else 'CAUTION — counter-trend'}\n\n"
            f"{'='*50}\n"
            f"FINAL DECISION: {'APPROVED — execute trade' if should_trade else 'REJECTED — conditions not met'}\n"
        )

        if not should_trade:
            reasons = []
            if confluence <= 0.6:
                reasons.append("low confluence")
            if rr < 1.5:
                reasons.append("poor risk:reward")
            if portfolio_heat >= 6:
                reasons.append("portfolio heat exceeded")
            if consec_losses >= 3:
                reasons.append("consecutive loss limit")
            output += f"Rejection reasons: {', '.join(reasons)}\n"
            output += f"Action: Stand aside. Capital preservation above all."

        samples.append({"instruction": instruction, "input": "", "output": output})
    return samples


# ── Main ──────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("RUNECLAW - Enhanced Training Data Generator v3")
    print("=" * 60)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Load existing v2 data if available
    existing_data = []
    v2_paths = [
        os.path.join(OUTPUT_DIR, "combined_training.jsonl"),
        "./combined_training.jsonl",
    ]
    for p in v2_paths:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        existing_data.append(json.loads(line))
            print(f"\nLoaded {len(existing_data)} existing samples from {p}")
            break

    # Generate new synthetic data
    generators = [
        ("Trade Analysis", gen_trade_analysis, 3000),
        ("Multi-Timeframe", gen_multi_timeframe, 3000),
        ("No-Trade Scenarios", gen_no_trade, 3000),
        ("Confluence Breakdown", gen_confluence_breakdown, 3000),
        ("Risk Edge Cases", gen_risk_edge_cases, 2000),
        ("Position Sizing", gen_position_sizing, 2000),
        ("Market Regime", gen_market_regime, 2000),
        ("Correlation Analysis", gen_correlation_analysis, 2000),
        ("Drawdown Recovery", gen_drawdown_recovery, 1500),
        ("Chain-of-Thought", gen_chain_of_thought, 1500),
    ]

    all_samples = list(existing_data)
    print(f"\nGenerating synthetic training data...\n")

    for name, func, count in generators:
        samples = func(count)
        all_samples.extend(samples)
        print(f"  {name}: {len(samples)} samples")

    # Shuffle
    random.shuffle(all_samples)

    # Write
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for sample in all_samples:
            f.write(json.dumps(sample, ensure_ascii=False) + "\n")

    print(f"\n{'='*60}")
    print(f"TOTAL: {len(all_samples)} training samples")
    print(f"Output: {OUTPUT_FILE}")
    print(f"{'='*60}")
    print(f"""
Breakdown:
  Existing (v2) data:     {len(existing_data)}
  New synthetic data:     {len(all_samples) - len(existing_data)}
  Total:                  {len(all_samples)}

Next step:
  python train_max_8b.py
""")


if __name__ == "__main__":
    main()
