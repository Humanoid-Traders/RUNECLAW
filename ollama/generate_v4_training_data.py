#!/usr/bin/env python3
"""
RUNECLAW V4 Training Dataset Generator

Builds on V3 with 5 new high-impact training categories:
  1. Confidence Calibration — maps confidence to actual win rates
  2. Multi-Timeframe Reasoning — 1H+4H+1D joint analysis
  3. Risk-Aware Thesis — generates trades within risk constraints
  4. Order Flow Intelligence — full smart-money signal integration
  5. Trade Outcome Feedback — learns from wins/losses

Output: JSONL with {instruction, input, output} format
"""

import json
import random
import math
from pathlib import Path

random.seed(42)

OUTPUT_DIR = Path("/workspace/output/runeclaw/ollama/training_data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Market data ──────────────────────────────────────────────────

PAIRS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
    "ADA/USDT", "AVAX/USDT", "DOGE/USDT", "DOT/USDT", "MATIC/USDT",
    "LINK/USDT", "UNI/USDT", "ATOM/USDT", "FIL/USDT", "APT/USDT",
    "ARB/USDT", "OP/USDT", "SUI/USDT", "SEI/USDT", "INJ/USDT",
    "TIA/USDT", "JUP/USDT", "WIF/USDT", "NEAR/USDT", "RENDER/USDT",
    "FET/USDT", "RUNE/USDT", "STX/USDT", "ICP/USDT", "PEPE/USDT",
    "EDGE/USDT", "MEGA/USDT", "BAN/USDT", "PROS/USDT", "DOGE/USDT",
    "KAS/USDT", "BONK/USDT", "ONDO/USDT", "PENDLE/USDT", "ENA/USDT",
]

PRICES = {
    "BTC": 105000, "ETH": 2750, "SOL": 178, "BNB": 720, "XRP": 2.55,
    "ADA": 0.78, "AVAX": 25, "DOGE": 0.088, "DOT": 4.8, "MATIC": 0.25,
    "LINK": 16, "UNI": 7.5, "ATOM": 5.2, "FIL": 3.3, "APT": 6.1,
    "ARB": 0.45, "OP": 0.95, "SUI": 4.2, "SEI": 0.28, "INJ": 14,
    "TIA": 3.8, "JUP": 0.62, "WIF": 1.1, "PEPE": 0.000014, "RENDER": 5.5,
    "FET": 1.2, "NEAR": 3.8, "ICP": 6.5, "RUNE": 2.1, "STX": 0.85,
    "EDGE": 0.46, "MEGA": 0.052, "BAN": 0.070, "PROS": 0.60, "KAS": 0.12,
    "BONK": 0.000022, "ONDO": 1.45, "PENDLE": 3.2, "ENA": 0.38,
}

REGIMES = ["TREND_UP", "TREND_DOWN", "EXPANSION", "RANGE", "CHOP"]
REGIME_MULTS = {
    "TREND_UP": 1.2, "TREND_DOWN": 1.2, "EXPANSION": 1.3,
    "RANGE": 0.7, "CHOP": 0.5,
}

INDICATORS = [
    "RSI-14", "MACD", "Bollinger Bands", "EMA 20/50", "Volume Profile",
    "OBV", "Stochastic RSI", "ADX", "Ichimoku", "VWAP",
    "Fibonacci", "ATR", "SuperTrend", "Keltner Channels",
]

def rp(base, pct=0.05):
    v = base * (1 + random.uniform(-pct, pct))
    if base < 0.001: return round(v, 8)
    elif base < 0.1: return round(v, 6)
    elif base < 10: return round(v, 4)
    elif base < 1000: return round(v, 2)
    return round(v, 1)

def rpct(lo, hi): return round(random.uniform(lo, hi), 2)
def gpp(pair): return PRICES.get(pair.split('/')[0], 10.0)


# ═══════════════════════════════════════════════════════════════
# CATEGORY 1: Confidence Calibration (4000 samples)
# Teaches the model that confidence = expected win probability
# ═══════════════════════════════════════════════════════════════

def gen_confidence_calibration():
    """Generate samples where confidence maps to realistic win rates."""
    samples = []

    # Define win rate bands — the model must learn these relationships
    bands = [
        (0.50, 0.55, 0.48, 0.55),  # 50-55% conf → ~48-55% actual win rate
        (0.55, 0.60, 0.52, 0.60),  # 55-60% → slightly better
        (0.60, 0.65, 0.58, 0.66),  # 60-65% → moderate edge
        (0.65, 0.70, 0.62, 0.72),  # 65-70% → solid setup
        (0.70, 0.75, 0.68, 0.78),  # 70-75% → high conviction
        (0.75, 0.80, 0.72, 0.82),  # 75-80% → very strong
        (0.80, 0.90, 0.75, 0.88),  # 80-90% → rare, exceptional
    ]

    for _ in range(4000):
        pair = random.choice(PAIRS)
        base = gpp(pair)
        entry = rp(base, 0.03)
        if entry == 0: entry = base
        direction = random.choice(["LONG", "SHORT"])
        regime = random.choice(REGIMES)

        # Pick a confidence band
        band = random.choice(bands)
        raw_conf = rpct(band[0], band[1])
        actual_wr = rpct(band[2], band[3])

        # Generate supporting indicator context
        rsi = rpct(25, 75)
        adx = rpct(12, 55)
        macd_hist = rpct(-0.5, 0.5)
        bb_pctb = rpct(0.1, 0.9)
        obv = random.choice(["rising", "falling", "flat"])
        cvd = random.choice(["rising", "falling", "flat"])
        n_bullish = random.randint(4, 10)
        n_bearish = random.randint(2, 8)
        n_total = n_bullish + n_bearish

        # Higher confidence = more alignment
        if raw_conf > 0.70:
            n_bullish = random.randint(8, 12) if direction == "LONG" else random.randint(2, 4)
            n_bearish = 12 - n_bullish
            adx = rpct(25, 50)
        elif raw_conf < 0.55:
            n_bullish = random.randint(5, 7)
            n_bearish = random.randint(5, 7)
            adx = rpct(12, 22)

        # SL/TP
        sl_pct = rpct(1.5, 4.0)
        rr = rpct(1.2, 3.5)
        tp_pct = round(sl_pct * rr, 2)

        if direction == "LONG":
            sl = round(entry * (1 - sl_pct / 100), 6)
            tp = round(entry * (1 + tp_pct / 100), 6)
        else:
            sl = round(entry * (1 + sl_pct / 100), 6)
            tp = round(entry * (1 - tp_pct / 100), 6)

        # Outcome based on actual win rate
        is_win = random.random() < actual_wr
        if is_win:
            # Win: hit TP or partial TP
            exit_mult = rpct(0.6, 1.0)
            if direction == "LONG":
                exit_price = round(entry + (tp - entry) * exit_mult, 6)
            else:
                exit_price = round(entry - (entry - tp) * exit_mult, 6)
            pnl_pct = round(abs(exit_price - entry) / entry * 100, 2)
            outcome = "WIN"
        else:
            # Loss: hit SL or partial SL
            exit_mult = rpct(0.7, 1.0)
            if direction == "LONG":
                exit_price = round(entry - (entry - sl) * exit_mult, 6)
            else:
                exit_price = round(entry + (sl - entry) * exit_mult, 6)
            pnl_pct = round(abs(exit_price - entry) / entry * 100, 2)
            outcome = "LOSS"

        input_text = (
            f"Symbol: {pair}\n"
            f"Price: {entry}\n"
            f"Direction: {direction}\n"
            f"Regime: {regime}\n"
            f"RSI: {rsi} | ADX: {adx} | MACD_hist: {macd_hist}\n"
            f"BB_%B: {bb_pctb} | OBV: {obv} | CVD: {cvd}\n"
            f"Bullish indicators: {n_bullish}/{n_total} | Bearish: {n_bearish}/{n_total}\n"
            f"SL: {sl} ({sl_pct}%) | TP: {tp} ({tp_pct}%) | R:R: 1:{rr}"
        )

        output_text = (
            f"CONFIDENCE ASSESSMENT: {pair} {direction}\n"
            f"{'='*45}\n\n"
            f"Confluence Score: {raw_conf:.2f}\n"
            f"Calibrated Confidence: {raw_conf:.0%}\n\n"
            f"Signal Breakdown:\n"
            f"  Aligned: {n_bullish if direction == 'LONG' else n_bearish}/{n_total} indicators\n"
            f"  Regime: {regime} (mult: {REGIME_MULTS[regime]}x)\n"
            f"  Trend strength: ADX {adx} ({'strong' if adx > 25 else 'weak'})\n"
            f"  Momentum: RSI {rsi} ({'overbought' if rsi > 70 else 'oversold' if rsi < 30 else 'neutral'})\n"
            f"  Volume: OBV {obv}, CVD {cvd}\n\n"
            f"Risk Assessment:\n"
            f"  R:R: 1:{rr} {'PASS' if rr >= 1.2 else 'FAIL'}\n"
            f"  SL distance: {sl_pct}%\n"
            f"  Expected win rate at {raw_conf:.0%} confidence: ~{actual_wr:.0%}\n\n"
            f"Expected Outcome Distribution:\n"
            f"  ~{actual_wr:.0%} chance of hitting TP ({tp_pct}% gain)\n"
            f"  ~{1-actual_wr:.0%} chance of hitting SL ({sl_pct}% loss)\n"
            f"  Expected value: {(actual_wr * tp_pct - (1-actual_wr) * sl_pct):+.2f}% per trade\n\n"
            f"Actual Result: {outcome} at {exit_price} ({'+' if outcome == 'WIN' else '-'}{pnl_pct}%)\n\n"
            f"Verdict: {'APPROVED — positive expected value' if raw_conf > 0.60 and rr >= 1.2 else 'REJECTED — insufficient edge'}"
        )

        samples.append({
            "instruction": f"Assess trade confidence for {pair} {direction}. Calibrate confidence to expected win rate.",
            "input": input_text,
            "output": output_text,
        })

    return samples


# ═══════════════════════════════════════════════════════════════
# CATEGORY 2: Multi-Timeframe Reasoning (3000 samples)
# Joint 1H+4H+1D analysis with conflict resolution
# ═══════════════════════════════════════════════════════════════

def gen_multi_timeframe():
    """Generate multi-timeframe joint analysis samples."""
    samples = []

    for _ in range(3000):
        pair = random.choice(PAIRS)
        base = gpp(pair)

        # Generate per-timeframe data
        tf_data = {}
        for tf in ["1H", "4H", "1D"]:
            trend = random.choice(["bullish", "bearish", "neutral"])
            rsi = rpct(20, 80)
            adx = rpct(10, 55)
            macd = random.choice(["bullish_cross", "bearish_cross", "above_zero", "below_zero"])
            ema_align = random.choice(["bullish", "bearish", "mixed"])
            bb_squeeze = random.random() < 0.25
            volume = random.choice(["above_avg", "below_avg", "spike"])
            key_level = rp(base, 0.08)

            tf_data[tf] = {
                "trend": trend, "rsi": rsi, "adx": adx, "macd": macd,
                "ema": ema_align, "squeeze": bb_squeeze, "volume": volume,
                "support": round(key_level * 0.95, 6),
                "resistance": round(key_level * 1.05, 6),
            }

        # Determine alignment
        trends = [tf_data[tf]["trend"] for tf in ["1H", "4H", "1D"]]
        bull_count = trends.count("bullish")
        bear_count = trends.count("bearish")

        if bull_count == 3:
            alignment = "FULL_BULLISH"
            direction = "LONG"
            conf_base = rpct(0.72, 0.88)
        elif bear_count == 3:
            alignment = "FULL_BEARISH"
            direction = "SHORT"
            conf_base = rpct(0.72, 0.88)
        elif bull_count == 2:
            alignment = "PARTIAL_BULLISH"
            direction = "LONG"
            conf_base = rpct(0.58, 0.68)
        elif bear_count == 2:
            alignment = "PARTIAL_BEARISH"
            direction = "SHORT"
            conf_base = rpct(0.58, 0.68)
        else:
            alignment = "CONFLICTING"
            direction = "NONE"
            conf_base = rpct(0.35, 0.50)

        # Higher timeframe overrides
        daily_trend = tf_data["1D"]["trend"]
        hourly_trend = tf_data["1H"]["trend"]

        if direction != "NONE" and daily_trend != hourly_trend:
            htf_warning = f"WARNING: 1D is {daily_trend} but 1H is {hourly_trend}. Higher TF prevails."
            conf_base *= 0.85  # reduce confidence
        else:
            htf_warning = "Higher timeframe confirms lower timeframe direction."

        entry = rp(base, 0.02)
        if entry == 0: entry = base

        input_text = (
            f"Symbol: {pair}\nCurrent Price: {entry}\n\n"
            f"1H Data:\n"
            f"  Trend: {tf_data['1H']['trend']} | RSI: {tf_data['1H']['rsi']} | ADX: {tf_data['1H']['adx']}\n"
            f"  MACD: {tf_data['1H']['macd']} | EMA: {tf_data['1H']['ema']} | Vol: {tf_data['1H']['volume']}\n"
            f"  Support: {tf_data['1H']['support']} | Resistance: {tf_data['1H']['resistance']}\n"
            f"  BB Squeeze: {'YES' if tf_data['1H']['squeeze'] else 'NO'}\n\n"
            f"4H Data:\n"
            f"  Trend: {tf_data['4H']['trend']} | RSI: {tf_data['4H']['rsi']} | ADX: {tf_data['4H']['adx']}\n"
            f"  MACD: {tf_data['4H']['macd']} | EMA: {tf_data['4H']['ema']} | Vol: {tf_data['4H']['volume']}\n"
            f"  Support: {tf_data['4H']['support']} | Resistance: {tf_data['4H']['resistance']}\n\n"
            f"1D Data:\n"
            f"  Trend: {tf_data['1D']['trend']} | RSI: {tf_data['1D']['rsi']} | ADX: {tf_data['1D']['adx']}\n"
            f"  MACD: {tf_data['1D']['macd']} | EMA: {tf_data['1D']['ema']} | Vol: {tf_data['1D']['volume']}\n"
            f"  Support: {tf_data['1D']['support']} | Resistance: {tf_data['1D']['resistance']}"
        )

        if direction != "NONE":
            sl_pct = rpct(1.5, 3.5)
            rr = rpct(1.3, 3.0)
            tp_pct = round(sl_pct * rr, 2)
            if direction == "LONG":
                sl = round(entry * (1 - sl_pct / 100), 6)
                tp = round(entry * (1 + tp_pct / 100), 6)
            else:
                sl = round(entry * (1 + sl_pct / 100), 6)
                tp = round(entry * (1 - tp_pct / 100), 6)

            output_text = (
                f"MULTI-TIMEFRAME ANALYSIS: {pair}\n{'='*45}\n\n"
                f"Timeframe Alignment: {alignment}\n"
                f"  1H: {tf_data['1H']['trend'].upper()} (ADX {tf_data['1H']['adx']}, RSI {tf_data['1H']['rsi']})\n"
                f"  4H: {tf_data['4H']['trend'].upper()} (ADX {tf_data['4H']['adx']}, RSI {tf_data['4H']['rsi']})\n"
                f"  1D: {tf_data['1D']['trend'].upper()} (ADX {tf_data['1D']['adx']}, RSI {tf_data['1D']['rsi']})\n\n"
                f"HTF Override: {htf_warning}\n\n"
                f"Direction: {direction}\n"
                f"Confidence: {conf_base:.2f}\n"
                f"Entry: {entry}\n"
                f"Stop Loss: {sl} ({sl_pct}%)\n"
                f"Take Profit: {tp} ({tp_pct}%)\n"
                f"R:R: 1:{rr}\n\n"
                f"Key Levels:\n"
                f"  Nearest support: {tf_data['4H']['support']}\n"
                f"  Nearest resistance: {tf_data['4H']['resistance']}\n\n"
                f"Verdict: {'APPROVED' if conf_base > 0.60 and rr >= 1.2 else 'REJECTED'}"
            )
        else:
            output_text = (
                f"MULTI-TIMEFRAME ANALYSIS: {pair}\n{'='*45}\n\n"
                f"Timeframe Alignment: {alignment}\n"
                f"  1H: {tf_data['1H']['trend'].upper()}\n"
                f"  4H: {tf_data['4H']['trend'].upper()}\n"
                f"  1D: {tf_data['1D']['trend'].upper()}\n\n"
                f"Direction: NONE — STAND ASIDE\n"
                f"Confidence: {conf_base:.2f}\n\n"
                f"Timeframes are conflicting. No clear directional bias.\n"
                f"Wait for alignment before entering. Capital preservation above all."
            )

        samples.append({
            "instruction": f"Analyze {pair} across 1H, 4H, and 1D timeframes. Determine alignment and generate trade decision.",
            "input": input_text,
            "output": output_text,
        })

    return samples


# ═══════════════════════════════════════════════════════════════
# CATEGORY 3: Risk-Aware Thesis (3000 samples)
# Generate trades that fit within given risk constraints
# ═══════════════════════════════════════════════════════════════

def gen_risk_aware_thesis():
    """Generate trades that respect pre-specified risk constraints."""
    samples = []

    for _ in range(3000):
        pair = random.choice(PAIRS)
        base = gpp(pair)
        entry = rp(base, 0.02)
        if entry == 0: entry = base

        # Risk constraints
        portfolio = random.choice([500, 1000, 5000, 10000, 25000])
        max_loss_usd = round(portfolio * rpct(0.01, 0.03), 2)
        min_rr = random.choice([1.2, 1.5, 2.0])
        max_exposure_pct = random.choice([10, 15, 20])
        max_positions = random.choice([3, 5, 7])
        current_positions = random.randint(0, max_positions)
        leverage = random.choice([1, 3, 5])
        regime = random.choice(REGIMES)
        mult = REGIME_MULTS[regime]

        direction = random.choice(["LONG", "SHORT"])
        rsi = rpct(25, 75)
        adx = rpct(15, 50)
        confluence = rpct(0.45, 0.85)

        # Calculate position size from constraints
        atr_pct = rpct(1.5, 5.0)
        sl_pct = round(atr_pct * 1.0, 2)  # SL = 1x ATR

        if direction == "LONG":
            sl = round(entry * (1 - sl_pct / 100), 6)
        else:
            sl = round(entry * (1 + sl_pct / 100), 6)

        risk_per_unit = abs(entry - sl)
        max_units = round(max_loss_usd / risk_per_unit, 4) if risk_per_unit > 0 else 0
        notional = round(max_units * entry, 2)
        exposure_pct = round(notional / portfolio * 100, 2) if portfolio > 0 else 0

        # Apply exposure cap
        if exposure_pct > max_exposure_pct:
            max_units = round((portfolio * max_exposure_pct / 100) / entry, 4)
            notional = round(max_units * entry, 2)
            exposure_pct = max_exposure_pct
            capped = True
        else:
            capped = False

        # Regime-adjusted
        adjusted_units = round(max_units * mult, 4)
        adjusted_notional = round(adjusted_units * entry, 2)

        tp_pct = round(sl_pct * min_rr, 2)
        if direction == "LONG":
            tp = round(entry * (1 + tp_pct / 100), 6)
        else:
            tp = round(entry * (1 - tp_pct / 100), 6)

        # Feasibility check
        can_trade = (
            current_positions < max_positions
            and confluence > 0.55
            and exposure_pct <= max_exposure_pct
        )

        input_text = (
            f"RISK CONSTRAINTS:\n"
            f"  Portfolio: ${portfolio:,}\n"
            f"  Max loss per trade: ${max_loss_usd}\n"
            f"  Min R:R: 1:{min_rr}\n"
            f"  Max exposure: {max_exposure_pct}%\n"
            f"  Max positions: {max_positions} (current: {current_positions})\n"
            f"  Leverage: {leverage}x\n\n"
            f"MARKET DATA:\n"
            f"  Symbol: {pair} | Price: {entry}\n"
            f"  Direction: {direction}\n"
            f"  Regime: {regime} (mult: {mult}x)\n"
            f"  RSI: {rsi} | ADX: {adx}\n"
            f"  Confluence: {confluence:.2f}\n"
            f"  ATR: {atr_pct}%"
        )

        if can_trade:
            output_text = (
                f"RISK-AWARE TRADE: {pair} {direction}\n{'='*45}\n\n"
                f"Position Sizing (within constraints):\n"
                f"  Max loss: ${max_loss_usd} / risk per unit {risk_per_unit:.6f} = {max_units} units\n"
                f"  Notional: ${notional:,.2f} ({exposure_pct}% of portfolio)\n"
                f"  {'CAPPED at ' + str(max_exposure_pct) + '% max exposure' if capped else 'Within exposure limit'}\n"
                f"  Regime adjustment: {mult}x ({regime}) → {adjusted_units} units (${adjusted_notional:,.2f})\n\n"
                f"Trade Plan:\n"
                f"  Entry: {entry}\n"
                f"  Stop Loss: {sl} ({sl_pct}%)\n"
                f"  Take Profit: {tp} ({tp_pct}%)\n"
                f"  R:R: 1:{min_rr}\n"
                f"  Leverage: {leverage}x\n\n"
                f"Risk Budget:\n"
                f"  Max loss if SL hit: ${round(adjusted_units * risk_per_unit, 2)}\n"
                f"  Portfolio impact: {round(adjusted_units * risk_per_unit / portfolio * 100, 2)}%\n\n"
                f"Verdict: APPROVED — trade fits within all risk constraints"
            )
        else:
            reasons = []
            if current_positions >= max_positions:
                reasons.append(f"MAX_POSITIONS: {current_positions}/{max_positions}")
            if confluence <= 0.55:
                reasons.append(f"LOW_CONFLUENCE: {confluence:.2f} < 0.55 minimum")
            if exposure_pct > max_exposure_pct:
                reasons.append(f"EXPOSURE: {exposure_pct}% > {max_exposure_pct}% max")

            output_text = (
                f"RISK-AWARE TRADE: {pair} {direction}\n{'='*45}\n\n"
                f"Verdict: REJECTED\n\n"
                f"Failed Constraints:\n"
                + "\n".join(f"  FAIL: {r}" for r in reasons) +
                f"\n\nAction: NO TRADE. Wait for constraints to be met.\n"
                f"Capital preservation above all."
            )

        samples.append({
            "instruction": f"Generate a trade for {pair} that fits within the given risk constraints. Show position sizing math.",
            "input": input_text,
            "output": output_text,
        })

    return samples


# ═══════════════════════════════════════════════════════════════
# CATEGORY 4: Order Flow Intelligence (3000 samples)
# Full smart-money signal interpretation
# ═══════════════════════════════════════════════════════════════

def gen_order_flow():
    """Generate order flow analysis with smart money signals."""
    samples = []

    for _ in range(3000):
        pair = random.choice(PAIRS)
        base = gpp(pair)
        entry = rp(base, 0.03)
        if entry == 0: entry = base

        # Generate order flow signals
        book_imbalance = rpct(-0.8, 0.8)
        cvd_trend = random.choice(["rising", "falling", "flat"])
        cvd_divergence = random.choice(["bullish_div", "bearish_div", "none"])
        whale_bias = random.choice(["accumulation", "distribution", "neutral"])
        funding_rate = round(random.uniform(-0.03, 0.08), 4)
        oi_usd = round(random.uniform(5e6, 500e6), 0)
        oi_change = rpct(-15, 20)
        spot_vol_24h = round(random.uniform(1e6, 200e6), 0)
        smart_money = rpct(-0.9, 0.9)
        taker_ratio = rpct(0.3, 1.8)

        # Spot-futures divergence
        spot_vol_trend = random.choice(["rising", "falling", "flat"])
        oi_trend = random.choice(["rising", "falling", "flat"])
        if spot_vol_trend == "rising" and oi_trend == "flat":
            sf_div = "spot_led_bullish"
        elif oi_trend == "rising" and spot_vol_trend == "flat":
            sf_div = "spec_led_bearish"
        else:
            sf_div = "none"

        # OI-price divergence
        price_trend = random.choice(["up", "down", "flat"])
        if oi_trend == "rising" and price_trend == "flat":
            oi_price_div = "squeeze_building"
        elif oi_trend == "rising" and price_trend == "up":
            oi_price_div = "genuine_demand"
        elif oi_trend == "falling" and price_trend == "down":
            oi_price_div = "leverage_unwind"
        else:
            oi_price_div = "none"

        # Determine bias from order flow
        bull_signals = 0
        bear_signals = 0
        if book_imbalance > 0.2: bull_signals += 1
        elif book_imbalance < -0.2: bear_signals += 1
        if cvd_trend == "rising": bull_signals += 1
        elif cvd_trend == "falling": bear_signals += 1
        if whale_bias == "accumulation": bull_signals += 1
        elif whale_bias == "distribution": bear_signals += 1
        if funding_rate > 0.03: bear_signals += 1  # crowded longs
        elif funding_rate < -0.01: bull_signals += 1  # shorts paying
        if sf_div == "spot_led_bullish": bull_signals += 1
        elif sf_div == "spec_led_bearish": bear_signals += 1
        if taker_ratio > 1.1: bull_signals += 1
        elif taker_ratio < 0.8: bear_signals += 1

        if bull_signals > bear_signals + 1:
            of_bias = "BULLISH"
            direction = "LONG"
        elif bear_signals > bull_signals + 1:
            of_bias = "BEARISH"
            direction = "SHORT"
        else:
            of_bias = "NEUTRAL"
            direction = "NONE"

        input_text = (
            f"Symbol: {pair} | Price: {entry}\n\n"
            f"ORDER FLOW DATA:\n"
            f"  Book imbalance: {book_imbalance:+.2f}\n"
            f"  CVD trend: {cvd_trend} | CVD-price divergence: {cvd_divergence}\n"
            f"  Whale bias: {whale_bias} | Smart money score: {smart_money:+.2f}\n"
            f"  Funding rate: {funding_rate:+.4f}\n"
            f"  Open interest: ${oi_usd/1e6:.1f}M (change: {oi_change:+.1f}%)\n"
            f"  Spot volume 24h: ${spot_vol_24h/1e6:.1f}M\n"
            f"  Taker buy/sell ratio: {taker_ratio:.2f}\n\n"
            f"DIVERGENCE SIGNALS:\n"
            f"  Spot-futures divergence: {sf_div}\n"
            f"  OI-price divergence: {oi_price_div}\n"
            f"  Spot vol trend: {spot_vol_trend} | OI trend: {oi_trend} | Price trend: {price_trend}"
        )

        output_text = (
            f"ORDER FLOW ANALYSIS: {pair}\n{'='*45}\n\n"
            f"Smart Money Positioning:\n"
            f"  Book imbalance: {book_imbalance:+.2f} → {'buyers dominant' if book_imbalance > 0.2 else 'sellers dominant' if book_imbalance < -0.2 else 'balanced'}\n"
            f"  CVD: {cvd_trend} → {'accumulation' if cvd_trend == 'rising' else 'distribution' if cvd_trend == 'falling' else 'no directional flow'}\n"
            f"  Whale activity: {whale_bias} (smart money score: {smart_money:+.2f})\n"
            f"  Taker ratio: {taker_ratio:.2f} → {'aggressive buying' if taker_ratio > 1.1 else 'aggressive selling' if taker_ratio < 0.8 else 'balanced'}\n\n"
            f"Derivatives Positioning:\n"
            f"  Funding: {funding_rate:+.4f} → {'overleveraged longs (bearish)' if funding_rate > 0.03 else 'shorts paying (bullish)' if funding_rate < -0.01 else 'neutral'}\n"
            f"  OI: ${oi_usd/1e6:.1f}M ({oi_change:+.1f}%) → {'leverage building' if oi_change > 5 else 'leverage unwinding' if oi_change < -5 else 'stable'}\n\n"
            f"Divergence Signals:\n"
            f"  Spot-futures: {sf_div}\n"
            f"    {'Spot volume rising with stable OI — real demand, bullish' if sf_div == 'spot_led_bullish' else 'OI rising with flat spot volume — speculative leverage, bearish squeeze risk' if sf_div == 'spec_led_bearish' else 'No significant divergence'}\n"
            f"  OI-price: {oi_price_div}\n"
            f"    {'OI building with flat price — squeeze imminent, direction set by funding' if oi_price_div == 'squeeze_building' else 'OI and price rising together — genuine demand rally' if oi_price_div == 'genuine_demand' else 'OI and price both falling — leverage unwind, capitulation risk' if oi_price_div == 'leverage_unwind' else 'No divergence'}\n\n"
            f"Overall Bias: {of_bias} (bull signals: {bull_signals}, bear signals: {bear_signals})\n"
            f"Direction: {direction}\n"
            f"{'Action: Wait for technical confirmation before entering.' if direction == 'NONE' else f'Action: {direction} bias confirmed by order flow. Proceed to technical analysis for entry.'}"
        )

        samples.append({
            "instruction": f"Analyze order flow data for {pair}. Assess smart money positioning, divergence signals, and directional bias.",
            "input": input_text,
            "output": output_text,
        })

    return samples


# ═══════════════════════════════════════════════════════════════
# CATEGORY 5: Trade Outcome Feedback (3000 samples)
# Learn from wins and losses — what worked, what didn't
# ═══════════════════════════════════════════════════════════════

def gen_trade_feedback():
    """Generate trade reflection samples — post-mortem analysis."""
    samples = []

    for _ in range(3000):
        pair = random.choice(PAIRS)
        base = gpp(pair)
        entry = rp(base, 0.03)
        if entry == 0: entry = base
        direction = random.choice(["LONG", "SHORT"])
        regime = random.choice(REGIMES)
        confidence = rpct(0.50, 0.85)

        sl_pct = rpct(1.5, 4.0)
        rr = rpct(1.0, 3.5)
        tp_pct = round(sl_pct * rr, 2)

        if direction == "LONG":
            sl = round(entry * (1 - sl_pct / 100), 6)
            tp = round(entry * (1 + tp_pct / 100), 6)
        else:
            sl = round(entry * (1 + sl_pct / 100), 6)
            tp = round(entry * (1 - tp_pct / 100), 6)

        hold_hours = rpct(0.5, 72)

        # Determine outcome with realistic distribution
        outcome_roll = random.random()
        if outcome_roll < 0.35:
            # TP hit
            outcome = "TP_HIT"
            exit_price = tp
            pnl_pct = tp_pct
        elif outcome_roll < 0.65:
            # SL hit
            outcome = "SL_HIT"
            exit_price = sl
            pnl_pct = -sl_pct
        elif outcome_roll < 0.80:
            # Partial win (exit before TP)
            outcome = "PARTIAL_WIN"
            frac = rpct(0.3, 0.8)
            if direction == "LONG":
                exit_price = round(entry + (tp - entry) * frac, 6)
            else:
                exit_price = round(entry - (entry - tp) * frac, 6)
            pnl_pct = round(tp_pct * frac, 2)
        elif outcome_roll < 0.90:
            # Breakeven
            outcome = "BREAKEVEN"
            exit_price = entry
            pnl_pct = 0.0
        else:
            # Partial loss
            outcome = "PARTIAL_LOSS"
            frac = rpct(0.3, 0.8)
            if direction == "LONG":
                exit_price = round(entry - (entry - sl) * frac, 6)
            else:
                exit_price = round(entry + (sl - entry) * frac, 6)
            pnl_pct = round(-sl_pct * frac, 2)

        is_win = pnl_pct > 0

        # Generate lessons based on outcome
        if outcome == "TP_HIT":
            lessons = [
                f"Regime {regime} correctly identified — trade aligned with trend",
                f"Confluence {confidence:.2f} was sufficient for this setup",
                f"SL placement at {sl_pct}% gave adequate room for price action",
                "Full TP reached — consider trailing stop for extended moves next time",
            ]
        elif outcome == "SL_HIT":
            # Identify what went wrong
            wrong_reasons = random.sample([
                f"Counter-trend entry in {regime} regime — should have reduced size or skipped",
                f"SL too tight at {sl_pct}% — ATR-based stop would have given more room",
                f"Entered at resistance/support without confirmation candle",
                f"Confluence {confidence:.2f} was marginal — raised threshold would filter this",
                f"Funding rate indicated crowded trade — should have checked derivatives positioning",
                "Volume declining during entry — weak momentum, should have waited for confirmation",
                "Higher timeframe was counter-directional — MTF conflict not weighted enough",
            ], k=3)
            lessons = wrong_reasons
        elif outcome == "PARTIAL_WIN":
            lessons = [
                "Exited early due to momentum weakening",
                f"Captured {pnl_pct}% of {tp_pct}% target — consider scaling out at TP1",
                "Price action showed reversal signals before TP — good manual management",
            ]
        elif outcome == "BREAKEVEN":
            lessons = [
                "Trade went to profit but reversed — trailing stop saved capital",
                "Neither thesis confirmed nor invalidated — regime transition detected",
                "Breakeven exit is a win in risk management terms",
            ]
        else:
            lessons = [
                f"Partial loss of {pnl_pct}% — managed exit before full SL hit",
                "Recognized setup invalidation early — good risk discipline",
                "Capital preservation: losing less than planned is acceptable",
            ]

        input_text = (
            f"TRADE RESULT:\n"
            f"  Pair: {pair} | Direction: {direction}\n"
            f"  Regime: {regime} | Confidence: {confidence:.2f}\n"
            f"  Entry: {entry} | Exit: {exit_price}\n"
            f"  SL: {sl} ({sl_pct}%) | TP: {tp} ({tp_pct}%)\n"
            f"  R:R planned: 1:{rr}\n"
            f"  Hold time: {hold_hours:.1f}h\n"
            f"  Outcome: {outcome} | PnL: {pnl_pct:+.2f}%"
        )

        output_text = (
            f"TRADE REFLECTION: {pair} {direction}\n{'='*45}\n\n"
            f"Outcome: {outcome} ({pnl_pct:+.2f}%)\n"
            f"Result: {'WIN' if is_win else 'LOSS' if pnl_pct < 0 else 'BREAKEVEN'}\n\n"
            f"Entry Analysis:\n"
            f"  Confidence: {confidence:.2f} — {'justified' if (is_win and confidence > 0.6) or (not is_win and confidence < 0.6) else 'miscalibrated'}\n"
            f"  Regime: {regime} — {'correctly identified' if is_win else 'may have been transitioning'}\n"
            f"  R:R achieved: 1:{abs(pnl_pct / sl_pct):.1f} vs planned 1:{rr}\n\n"
            f"Lessons Learned:\n"
            + "\n".join(f"  {i+1}. {l}" for i, l in enumerate(lessons)) +
            f"\n\nAdjustments for Next Trade:\n"
        )

        if not is_win:
            output_text += (
                f"  - {'Raise' if confidence < 0.65 else 'Maintain'} confidence threshold\n"
                f"  - {'Widen' if sl_pct < 2.5 else 'Keep'} stop loss distance\n"
                f"  - Check higher timeframe alignment before entry\n"
                f"  - Review order flow for smart money confirmation"
            )
        else:
            output_text += (
                f"  - Setup quality was good — repeat similar conditions\n"
                f"  - Consider TP1/TP2 scaling for extended moves\n"
                f"  - Log regime + confluence combo for pattern recognition"
            )

        samples.append({
            "instruction": f"Reflect on this {pair} {direction} trade. Analyze what worked, what didn't, and extract lessons for future trades.",
            "input": input_text,
            "output": output_text,
        })

    return samples


# ═══════════════════════════════════════════════════════════════
# MAIN: Generate all categories and merge
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("RUNECLAW V4 Training Data Generator")
    print("=" * 50)

    all_samples = []

    # V4 new categories
    print("\n[1/5] Confidence Calibration...")
    cat1 = gen_confidence_calibration()
    all_samples.extend(cat1)
    print(f"  Generated {len(cat1):,} samples")

    print("\n[2/5] Multi-Timeframe Reasoning...")
    cat2 = gen_multi_timeframe()
    all_samples.extend(cat2)
    print(f"  Generated {len(cat2):,} samples")

    print("\n[3/5] Risk-Aware Thesis...")
    cat3 = gen_risk_aware_thesis()
    all_samples.extend(cat3)
    print(f"  Generated {len(cat3):,} samples")

    print("\n[4/5] Order Flow Intelligence...")
    cat4 = gen_order_flow()
    all_samples.extend(cat4)
    print(f"  Generated {len(cat4):,} samples")

    print("\n[5/5] Trade Outcome Feedback...")
    cat5 = gen_trade_feedback()
    all_samples.extend(cat5)
    print(f"  Generated {len(cat5):,} samples")

    # Shuffle
    random.shuffle(all_samples)

    # Save V4-only dataset
    v4_file = OUTPUT_DIR / "v4_training.jsonl"
    with open(v4_file, "w") as f:
        for s in all_samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"\nV4-only dataset: {len(all_samples):,} samples → {v4_file}")

    # Load V3 data and merge
    v3_file = OUTPUT_DIR / "combined_training.jsonl"
    v3_samples = []
    if v3_file.exists():
        with open(v3_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        v3_samples.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        print(f"Loaded V3 dataset: {len(v3_samples):,} samples")

    # Also load Claude-enriched data
    claude_file = OUTPUT_DIR / "combined_training_claude.jsonl"
    claude_samples = []
    if claude_file.exists():
        with open(claude_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        claude_samples.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        print(f"Loaded Claude dataset: {len(claude_samples):,} samples")

    # Merge all
    merged = v3_samples + claude_samples + all_samples
    random.shuffle(merged)

    merged_file = OUTPUT_DIR / "combined_training_v4.jsonl"
    with open(merged_file, "w") as f:
        for s in merged:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    print(f"\n{'='*50}")
    print(f"  V4 COMBINED DATASET")
    print(f"{'='*50}")
    print(f"  V3 base:     {len(v3_samples):,}")
    print(f"  Claude:      {len(claude_samples):,}")
    print(f"  V4 new:      {len(all_samples):,}")
    print(f"  TOTAL:       {len(merged):,}")
    print(f"  Output:      {merged_file}")
