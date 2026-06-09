#!/usr/bin/env python3
"""
RUNECLAW — Opus 4.8 Knowledge Distillation Engine
====================================================
Uses Claude Opus 4.8 as the teacher model for highest-quality training data.

WHY OPUS 4.8:
  - Best reasoning model available (Jan 2026 knowledge cutoff)
  - Deeper chain-of-thought than Sonnet/Haiku
  - Better at multi-step trade logic, edge cases, and nuanced rejections
  - Produces richer, more varied language (less formulaic)
  - 128K max output — can generate longer reasoning chains

WHAT'S NEW vs generate_claude_data.py:
  1. JSON-format outputs (bot integration — the model needs these)
  2. Order flow / smart money analysis (new category)
  3. Backtest interpretation & walk-forward reasoning
  4. Regime-linked position sizing with explicit math chains
  5. Multi-step reasoning with self-correction ("wait, let me reconsider...")
  6. Adversarial examples (convincing setups that should be rejected)
  7. Better prompt engineering — more specific, more constrained

COST ESTIMATE (Opus 4.8):
  $5 / MTok input, $25 / MTok output
  ~8000 samples × ~2000 tokens avg output ≈ 16M output tokens
  Input: ~8M tokens × $5 = ~$40
  Output: ~16M tokens × $25 = ~$400
  TOTAL: ~$440

  To reduce cost, use --samples 200 for a test run (~$18)
  Or use --samples 100 for a quick sanity check (~$9)

Usage:
  export ANTHROPIC_API_KEY=sk-ant-...
  python generate_opus_data.py                       # full run (~$440)
  python generate_opus_data.py --samples 200         # test run (~$18)
  python generate_opus_data.py --samples 100 --dry   # preview prompts only
  python generate_opus_data.py --resume               # continue interrupted run

Output:
  ./training_data/opus_training.jsonl
  ./training_data/combined_training_opus.jsonl  (merged with all existing data)
"""

import os
import sys
import json
import time
import random
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

random.seed(2026)

# ── Configuration ─────────────────────────────────────────────

OUTPUT_DIR = "./training_data"
OPUS_FILE = os.path.join(OUTPUT_DIR, "opus_training.jsonl")
COMBINED_FILE = os.path.join(OUTPUT_DIR, "combined_training_opus.jsonl")
STATS_FILE = os.path.join(OUTPUT_DIR, "opus_generation_stats.json")

SAMPLES_PER_CATEGORY = 800    # 800 × 10 categories = 8000 total
MAX_CONCURRENT = 3            # Opus rate limits are tighter
RETRY_DELAY = 3
MAX_RETRIES = 5

# Opus 4.8 model ID — matches console.anthropic.com naming
OPUS_MODEL = "claude-opus-4-8"

# ── Market Data Constants ─────────────────────────────────────

PAIRS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
    "ADA/USDT", "AVAX/USDT", "DOGE/USDT", "LINK/USDT", "UNI/USDT",
    "ARB/USDT", "OP/USDT", "SUI/USDT", "INJ/USDT", "TIA/USDT",
    "NEAR/USDT", "FET/USDT", "RENDER/USDT", "RUNE/USDT", "APT/USDT",
    "WIF/USDT", "PEPE/USDT", "JUP/USDT", "SEI/USDT", "STX/USDT",
    "TON/USDT", "AAVE/USDT", "MKR/USDT", "TAO/USDT", "WLD/USDT",
]

TIMEFRAMES = ["5m", "15m", "1h", "4h", "1d"]

REGIMES = [
    "TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE",
    "ACCUMULATION", "DISTRIBUTION", "CHOPPY", "BREAKOUT",
]

INDICATORS = [
    "RSI-14", "MACD (12,26,9)", "Bollinger Bands (20,2)", "EMA Cross (9/21)",
    "Volume Profile", "OBV", "Stochastic RSI", "ADX-14", "Ichimoku Cloud",
    "VWAP", "ATR-14", "Fibonacci Retracements",
]

RISK_CHECKS = [
    "position_size", "daily_loss_limit", "max_drawdown", "open_positions_cap",
    "correlation_guard", "risk_reward_ratio", "confidence_threshold",
    "consecutive_losses", "cooldown_timer", "portfolio_exposure",
    "symbol_exposure", "correlation_per_group", "volatility_guard",
    "stale_data_check", "stop_loss_required", "portfolio_var",
    "spread_check", "circuit_breaker", "regime_penalty",
    "liquidity_guard", "funding_rate_check", "whale_activity_check",
]

# Realistic price ranges for each pair (approximate mid-2026)
PRICE_RANGES = {
    "BTC/USDT": (85000, 130000), "ETH/USDT": (3200, 5500),
    "SOL/USDT": (140, 280), "BNB/USDT": (550, 850),
    "XRP/USDT": (1.8, 4.2), "ADA/USDT": (0.5, 1.2),
    "AVAX/USDT": (25, 65), "DOGE/USDT": (0.12, 0.35),
    "LINK/USDT": (12, 28), "UNI/USDT": (8, 18),
    "ARB/USDT": (0.8, 2.5), "OP/USDT": (1.5, 4.0),
    "SUI/USDT": (1.2, 3.8), "INJ/USDT": (15, 45),
    "TIA/USDT": (5, 15), "NEAR/USDT": (4, 12),
    "FET/USDT": (1.5, 5.0), "RENDER/USDT": (6, 18),
    "RUNE/USDT": (3, 10), "APT/USDT": (8, 22),
    "WIF/USDT": (0.8, 3.5), "PEPE/USDT": (0.000008, 0.00003),
    "JUP/USDT": (0.5, 2.0), "SEI/USDT": (0.3, 1.2),
    "STX/USDT": (1.0, 3.5), "TON/USDT": (4, 12),
    "AAVE/USDT": (150, 400), "MKR/USDT": (1500, 3500),
    "TAO/USDT": (300, 800), "WLD/USDT": (1.5, 5.0),
}

REGIME_MULTIPLIERS = {
    "TRENDING_UP": 1.0, "TRENDING_DOWN": 1.0, "RANGING": 0.7,
    "VOLATILE": 0.5, "ACCUMULATION": 0.8, "DISTRIBUTION": 0.6,
    "CHOPPY": 0.5, "BREAKOUT": 0.9,
}


def _price(pair):
    lo, hi = PRICE_RANGES.get(pair, (10, 100))
    return round(random.uniform(lo, hi), _decimals(pair))

def _decimals(pair):
    lo, _ = PRICE_RANGES.get(pair, (10, 100))
    if lo < 0.001: return 8
    if lo < 0.1: return 4
    if lo < 10: return 2
    if lo < 1000: return 1
    return 0

def _pct(base, pct):
    return round(base * (1 + pct / 100), _decimals_from_price(base))

def _decimals_from_price(p):
    if p < 0.001: return 8
    if p < 0.1: return 4
    if p < 10: return 2
    if p < 1000: return 1
    return 0


# ── Claude Opus Client ────────────────────────────────────────

class OpusClient:
    def __init__(self, api_key, model=None):
        try:
            import anthropic
            # Ensure latest version for new model ID support
            ver = getattr(anthropic, '__version__', '0.0.0')
            if ver < '0.40':
                print(f"  anthropic package v{ver} may be outdated, upgrading...")
                import subprocess
                subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "anthropic", "-q"])
                import importlib
                importlib.reload(anthropic)
        except ImportError:
            print("Installing anthropic package...")
            import subprocess
            subprocess.run([sys.executable, "-m", "pip", "install", "anthropic", "-q"])
            import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model or OPUS_MODEL
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cache_read = 0
        self.total_cache_create = 0

        # Verify model access
        print(f"  Testing model: {self.model}...")
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=20,
                messages=[{"role": "user", "content": "Say OK"}],
            )
            self.total_input_tokens += resp.usage.input_tokens
            self.total_output_tokens += resp.usage.output_tokens
            print(f"  Model verified: {self.model}")
        except Exception as e:
            print(f"\n  ERROR: Cannot access {self.model}: {e}")
            print("  Falling back to model detection...")
            self.model = self._detect_model()

    def _detect_model(self):
        import warnings
        warnings.filterwarnings("ignore", category=DeprecationWarning)

        fallbacks = [
            # Current model names (from console.anthropic.com)
            "claude-opus-4-8",
            "claude-opus-4-7",
            "claude-opus-4-6",
            "claude-opus-4-5",
            "claude-opus-4-1",
            "claude-sonnet-4-6",
            "claude-haiku-4-5",
            # Latest aliases
            "claude-opus-4-latest",
            "claude-sonnet-4-latest",
            "claude-haiku-4-latest",
            # Dated versions
            "claude-opus-4-20260514",
            "claude-sonnet-4-20260514",
            "claude-opus-4-20250514",
            "claude-sonnet-4-20250514",
            # Legacy
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
        ]
        for m in fallbacks:
            try:
                self.client.messages.create(
                    model=m, max_tokens=10,
                    messages=[{"role": "user", "content": "test"}],
                )
                print(f"  Model found: {m}")
                if "opus" not in m:
                    print("  NOTE: Opus not available — using best available model.")
                    print("  Data quality will be good but not Opus-tier.")
                return m
            except Exception as e:
                err = str(e)
                if "404" in err or "not_found" in err:
                    print(f"    {m}: not available")
                elif "401" in err or "403" in err:
                    print(f"    {m}: auth error")
                else:
                    # Might be a rate limit or transient error — model may exist
                    print(f"    {m}: {type(e).__name__}: {str(e)[:80]}")
                continue

        print("\n  FATAL: No Claude model available!")
        print("  Check your API key at console.anthropic.com")
        print("  Your key needs access to at least one Claude model.")
        sys.exit(1)

    def generate(self, system, user_prompt, max_tokens=2500):
        # Use cache_control on the system prompt — it's the same across
        # thousands of requests, so caching saves 90% on input costs.
        for attempt in range(MAX_RETRIES):
            try:
                resp = self.client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=[
                        {
                            "type": "text",
                            "text": system,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": user_prompt}],
                )
                self.total_input_tokens += resp.usage.input_tokens
                self.total_output_tokens += resp.usage.output_tokens
                # Track cache performance
                cache_read = getattr(resp.usage, 'cache_read_input_tokens', 0) or 0
                cache_create = getattr(resp.usage, 'cache_creation_input_tokens', 0) or 0
                self.total_cache_read += cache_read
                self.total_cache_create += cache_create
                return resp.content[0].text
            except Exception as e:
                err = str(e).lower()
                if "rate" in err or "429" in err or "overloaded" in err:
                    wait = RETRY_DELAY * (2 ** attempt)
                    print(f"    Rate limited, waiting {wait}s...")
                    time.sleep(wait)
                elif attempt < MAX_RETRIES - 1:
                    print(f"    Retry {attempt+1}: {type(e).__name__}")
                    time.sleep(RETRY_DELAY)
                else:
                    print(f"    FAILED after {MAX_RETRIES} attempts: {e}")
                    return None

    def cost_estimate(self):
        # Cached input tokens cost 90% less ($0.50/MTok vs $5/MTok for Opus)
        uncached_input = self.total_input_tokens - self.total_cache_read
        input_cost = uncached_input / 1_000_000 * 5
        cache_read_cost = self.total_cache_read / 1_000_000 * 0.50  # 90% discount
        cache_create_cost = self.total_cache_create / 1_000_000 * 6.25  # 25% surcharge on first write
        output_cost = self.total_output_tokens / 1_000_000 * 25
        return input_cost + cache_read_cost + cache_create_cost + output_cost

    def cost_savings_report(self):
        """Show how much caching saved."""
        if self.total_cache_read == 0:
            return "  No cache hits yet."
        # What it would have cost without caching
        full_input_cost = (self.total_input_tokens + self.total_cache_read) / 1_000_000 * 5
        actual_cost = self.cost_estimate()
        saved = full_input_cost + (self.total_output_tokens / 1_000_000 * 25) - actual_cost
        cache_hit_rate = self.total_cache_read / (self.total_input_tokens + self.total_cache_read) * 100
        return (
            f"  Cache hits: {self.total_cache_read:,} tokens ({cache_hit_rate:.0f}% hit rate)\n"
            f"  Cache saved: ~${saved:.2f}"
        )


# ── Teacher System Prompts ────────────────────────────────────

TEACHER_CORE = """You are a senior cryptocurrency trading analyst with 15+ years of experience across TradFi and crypto markets. You are generating training data for RUNECLAW, an AI trading assistant.

RUNECLAW SYSTEM FACTS (use these exactly):
- GetClaw Confluence Engine: 12 weighted indicators (RSI-14 w=1.5, MACD w=1.0, Bollinger w=1.2, EMA Cross w=1.0, Volume Profile w=1.3, OBV w=0.8, Stoch RSI w=0.7, ADX w=1.1, Ichimoku w=0.9, VWAP w=1.4, ATR w=0.8, Fibonacci w=1.0)
- 22 automated risk checks, ALL must pass (fail-closed)
- Min confidence threshold: 0.55 (55%)
- Min risk:reward ratio: 1.2:1
- Max position size: 2% of equity risked per trade
- Max portfolio exposure: 80%
- Max consecutive losses before cooldown: 5
- Circuit breaker triggers at 10% drawdown
- Position sizing: fixed-fractional (risk_budget / stop_distance)
- Regime multipliers: TRENDING=1.0x, RANGING=0.7x, CHOPPY=0.5x, VOLATILE=0.5x, BREAKOUT=0.9x
- Human confirmation required before ANY execution
- Capital preservation is the #1 priority

QUALITY RULES:
- Always use specific numbers (prices, percentages, R-multiples)
- Show multi-step reasoning — explain WHY, not just WHAT
- When rejecting: make the setup sound tempting first, then explain the flaw
- Include self-correction where natural ("Wait — checking the higher timeframe...")
- Vary your language. Don't use the same phrases every time
- Be decisive. Clear APPROVED or REJECTED, never "maybe"
- Every trade needs entry, stop-loss, take-profit, position size
- Reference specific risk checks by name when relevant"""

TEACHER_JSON = """You are generating training data for RUNECLAW's bot integration layer. The bot expects structured JSON output.

OUTPUT FORMAT (strict):
{
  "direction": "LONG" or "SHORT",
  "confidence": 0.0 to 1.0,
  "entry": <price>,
  "stop_loss": <price>,
  "take_profit": <price>,
  "risk_reward": <float>,
  "regime": "TRENDING_UP"|"TRENDING_DOWN"|"RANGING"|"VOLATILE"|"CHOPPY"|"ACCUMULATION"|"DISTRIBUTION"|"BREAKOUT",
  "confluence_score": 0.0 to 1.0,
  "position_size_pct": <float>,
  "reasoning": "<2-4 sentences explaining the trade thesis>",
  "risk_checks_passed": <int out of 22>,
  "verdict": "APPROVED" or "REJECTED",
  "key_indicators": ["RSI", "MACD", ...],
  "timeframe": "1h"|"4h"|etc
}

RULES:
- LONG: stop_loss < entry < take_profit (always)
- SHORT: take_profit < entry < stop_loss (always)
- confidence must be >= 0.55 for APPROVED trades
- risk_reward must be >= 1.2 for APPROVED trades
- Use realistic prices for the given pair
- Position size: base 2% × regime multiplier
- If REJECTED, still fill all fields but explain why in reasoning"""


# ── Prompt Categories ─────────────────────────────────────────

def make_prompts(n_per_cat=800):
    prompts = []

    # ── 1. DETAILED TRADE ANALYSIS (text format) ──────────────
    for _ in range(n_per_cat):
        pair = random.choice(PAIRS)
        tf = random.choice(TIMEFRAMES)
        regime = random.choice(REGIMES)
        price = _price(pair)
        direction = random.choice(["bullish", "bearish"])

        # Randomize indicator readings for realism
        rsi = round(random.uniform(20, 80), 1)
        adx = round(random.uniform(15, 50), 1)
        confluence = round(random.uniform(0.3, 0.85), 2)
        vol_spike = random.choice([True, False])

        prompts.append({
            "category": "trade_analysis",
            "system": TEACHER_CORE,
            "instruction": f"Analyze {pair} on the {tf} timeframe for trade setups.",
            "input": (
                f"Symbol: {pair}\nPrice: ${price}\nTimeframe: {tf}\n"
                f"Regime: {regime}\nRSI: {rsi}\nADX: {adx}\n"
                f"Confluence: {confluence}\nVolume Spike: {vol_spike}"
            ),
            "prompt": (
                f"Generate a complete RUNECLAW trade analysis for {pair} at ${price} on {tf}.\n\n"
                f"Market context:\n"
                f"  - Regime: {regime}\n"
                f"  - RSI-14: {rsi}\n"
                f"  - ADX: {adx}\n"
                f"  - Confluence score: {confluence}\n"
                f"  - Volume spike: {vol_spike}\n"
                f"  - Bias appears: {direction}\n\n"
                f"Produce a structured analysis with:\n"
                f"1. GetClaw Confluence Engine breakdown (which indicators agree/disagree)\n"
                f"2. Specific entry, stop-loss, and take-profit prices\n"
                f"3. Risk:reward calculation\n"
                f"4. Position sizing with regime multiplier math: base_risk × {REGIME_MULTIPLIERS[regime]}x ({regime}) = final\n"
                f"5. Which risk checks pass/fail\n"
                f"6. Clear APPROVED or REJECTED verdict\n\n"
                f"Show your reasoning chain. If something doesn't add up, say so."
            ),
        })

    # ── 2. JSON-FORMAT TRADES (bot integration) ───────────────
    for _ in range(n_per_cat):
        pair = random.choice(PAIRS)
        tf = random.choice(TIMEFRAMES)
        regime = random.choice(REGIMES)
        price = _price(pair)
        approved = random.random() > 0.35  # 65% approval rate

        prompts.append({
            "category": "json_trade",
            "system": TEACHER_JSON,
            "instruction": f"Analyze {pair} at ${price} on {tf} and output a structured JSON trade decision.",
            "input": f"Symbol: {pair}\nPrice: ${price}\nTimeframe: {tf}\nRegime: {regime}",
            "prompt": (
                f"Generate a RUNECLAW structured trade decision for {pair} at ${price} on {tf}.\n\n"
                f"Regime: {regime}\n"
                f"Target verdict: {'APPROVED' if approved else 'REJECTED'}\n\n"
                f"Output ONLY valid JSON matching the schema. Include:\n"
                f"- Realistic entry/SL/TP for the current price\n"
                f"- Position sizing: 2% base × {REGIME_MULTIPLIERS[regime]}x ({regime}) = {2 * REGIME_MULTIPLIERS[regime]:.1f}%\n"
                f"- Confluence score and key indicators\n"
                f"- {'At least 2 reasons for approval' if approved else 'Specific reason for rejection (which check failed)'}\n\n"
                f"The JSON must be parseable. No markdown fencing, just raw JSON."
            ),
        })

    # ── 3. NO-TRADE / ADVERSARIAL REJECTIONS ──────────────────
    for _ in range(n_per_cat):
        pair = random.choice(PAIRS)
        tf = random.choice(TIMEFRAMES)
        price = _price(pair)

        traps = [
            f"RSI is at {random.randint(25, 35)} suggesting oversold bounce, but ADX is {random.randint(10, 18)} (no trend). "
            f"The bounce looks tempting but there's no directional conviction.",

            f"Price broke above the 20-period Bollinger Band upper with a {random.uniform(1.5, 3.0):.1f}% candle. "
            f"Looks like a breakout, but volume is 40% below average — likely a fakeout.",

            f"Perfect confluence score of {random.uniform(0.70, 0.82):.2f} on {tf}, but the 4h shows a massive "
            f"bearish engulfing and the daily is in distribution. Higher TF conflict.",

            f"Strong trend on all timeframes. But you already have 4 open positions and portfolio "
            f"heat is at 7.8%. This is a concentration/exposure risk check failure.",

            f"Setup has 1.8:1 R:R which passes, but the stop is {random.uniform(8, 15):.1f}% away. "
            f"ATR is only {random.uniform(1, 2):.1f}% — the stop is way too wide for the current volatility.",

            f"Last 4 trades were losses. This setup scores {random.uniform(0.60, 0.72):.2f} confluence. "
            f"Normally approved, but consecutive loss cooldown check (#8) blocks it.",

            f"Funding rate on {pair} is {random.uniform(-0.08, -0.15):.3f}% (extremely negative). "
            f"Contrarian long is tempting but funding can stay negative for days — don't fight it.",

            f"Clean setup but current spread is {random.uniform(0.12, 0.25):.2f}% — 3x normal. "
            f"Low liquidity period. Execution quality will be poor, real R:R collapses.",

            f"Everything aligns for a SHORT, but {pair.split('/')[0]} has a major protocol upgrade "
            f"announcement in 6 hours. Binary event risk makes any position a coin flip.",

            f"Confluence at {random.uniform(0.48, 0.54):.2f} — just below the 0.55 threshold. "
            f"Looks close enough to trade, but the threshold exists for a reason. REJECT.",
        ]
        trap = random.choice(traps)

        prompts.append({
            "category": "no_trade",
            "system": TEACHER_CORE,
            "instruction": f"Should RUNECLAW take this {pair} setup on {tf}?",
            "input": f"Symbol: {pair}\nPrice: ${price}\nTimeframe: {tf}",
            "prompt": (
                f"As RUNECLAW, evaluate this {pair} setup at ${price} on {tf}.\n\n"
                f"Situation: {trap}\n\n"
                f"Write a REJECTION analysis that:\n"
                f"1. Acknowledges what looks good about the setup (don't dismiss it)\n"
                f"2. Identifies the specific flaw or risk check failure\n"
                f"3. Explains why passing on this trade IS the edge\n"
                f"4. States what would need to change for you to take it\n"
                f"5. Ends with clear REJECTED verdict\n\n"
                f"The model needs to learn that the best traders are defined by the trades they DON'T take."
            ),
        })

    # ── 4. ORDER FLOW & SMART MONEY ANALYSIS ──────────────────
    for _ in range(n_per_cat):
        pair = random.choice(PAIRS)
        tf = random.choice(["15m", "1h", "4h"])
        price = _price(pair)

        scenarios = [
            f"CVD is rising while price is falling — hidden accumulation. "
            f"Large buy wall at ${_pct(price, -2)} absorbing sells.",

            f"Whale deposited {random.randint(500, 5000)} {pair.split('/')[0]} to exchange. "
            f"Historical pattern: 70% chance of selling within 24h.",

            f"Open interest up {random.uniform(5, 20):.0f}% in 4h while price flat — "
            f"leverage building. Liquidation cascade risk above ${_pct(price, 3)} or below ${_pct(price, -3)}.",

            f"Funding rate: {random.uniform(0.02, 0.08):.3f}% (positive, longs paying). "
            f"Long/short ratio: {random.uniform(1.5, 3.0):.1f}. Crowded long — squeeze risk.",

            f"Large market sell orders hitting the book ({random.randint(50, 500)}K USDT). "
            f"But passive bid absorption is eating them — the selling isn't moving price.",

            f"Book imbalance: {random.uniform(60, 85):.0f}% bids vs {random.uniform(15, 40):.0f}% asks. "
            f"Smart money is stacking bids. Price hasn't moved yet — front-running incoming demand.",

            f"Liquidation heatmap shows ${random.randint(10, 50)}M in long liquidations at "
            f"${_pct(price, -5)}. Market makers may hunt this level.",
        ]
        scenario = random.choice(scenarios)

        prompts.append({
            "category": "order_flow",
            "system": TEACHER_CORE,
            "instruction": f"Analyze order flow data for {pair} and assess smart money positioning.",
            "input": f"Symbol: {pair}\nPrice: ${price}\nTimeframe: {tf}",
            "prompt": (
                f"As RUNECLAW's order flow analyzer, interpret this data for {pair} at ${price}:\n\n"
                f"{scenario}\n\n"
                f"Provide:\n"
                f"1. What the order flow data is telling you (bullish/bearish/neutral)\n"
                f"2. How this integrates with the technical picture\n"
                f"3. Whether this confirms or contradicts the current trade thesis\n"
                f"4. Specific risk implications (liquidation cascades, squeeze potential)\n"
                f"5. Actionable recommendation: enter, wait, or reduce exposure\n\n"
                f"Order flow is the footprint of institutional activity. Read it honestly."
            ),
        })

    # ── 5. MULTI-TIMEFRAME WITH SELF-CORRECTION ──────────────
    for _ in range(n_per_cat):
        pair = random.choice(PAIRS)
        price = _price(pair)

        conflicts = [
            ("15m LONG signal (RSI bounce, MACD cross up)", "4h showing bearish divergence on RSI", "1d in clear uptrend above 50 EMA"),
            ("1h bearish engulfing candle", "4h at key Fibonacci 0.618 support", "1d trend is bullish"),
            ("5m scalp setup: clean breakout above range", "15m shows exhaustion (shooting star)", "1h trend supports the breakout"),
            ("4h double bottom confirmed", "1d death cross (50/200 EMA)", "Weekly still in uptrend"),
            ("15m bull flag breakout", "1h resistance overhead at ${:.0f}".format(price * 1.02), "4h volume declining — distribution?"),
            ("1h hammer at support", "4h MACD still negative", "1d demand zone confluence"),
        ]
        c15, c4h, c1d = random.choice(conflicts)

        prompts.append({
            "category": "multi_timeframe",
            "system": TEACHER_CORE,
            "instruction": f"Perform multi-timeframe analysis on {pair} with conflicting signals.",
            "input": f"Symbol: {pair}\nPrice: ${price}",
            "prompt": (
                f"As RUNECLAW, perform multi-timeframe analysis for {pair} at ${price}.\n\n"
                f"Signals:\n"
                f"  - Lower TF: {c15}\n"
                f"  - Mid TF: {c4h}\n"
                f"  - Higher TF: {c1d}\n\n"
                f"IMPORTANT: Show your reasoning process, including moments where you reconsider.\n"
                f"Example: 'Initially this looks bullish on the 15m, but wait — checking the 4h...'\n\n"
                f"Walk through:\n"
                f"1. What each timeframe tells you independently\n"
                f"2. Where they agree and where they conflict\n"
                f"3. Which timeframe gets priority and WHY\n"
                f"4. A moment of self-correction if the initial read was wrong\n"
                f"5. Final recommendation: trade (with levels), wait, or pass\n\n"
                f"The model needs to learn to CHANGE ITS MIND when evidence contradicts."
            ),
        })

    # ── 6. POSITION SIZING WITH MATH CHAINS ───────────────────
    for _ in range(n_per_cat):
        pair = random.choice(PAIRS)
        price = _price(pair)
        regime = random.choice(REGIMES)
        equity = random.choice([5000, 10000, 25000, 50000])
        atr_pct = round(random.uniform(0.8, 5.0), 1)
        sl_mult = round(random.uniform(1.5, 3.0), 1)

        prompts.append({
            "category": "position_sizing",
            "system": TEACHER_CORE,
            "instruction": f"Calculate position size for {pair} trade using RUNECLAW's risk framework.",
            "input": (
                f"Symbol: {pair}\nPrice: ${price}\nEquity: ${equity:,}\n"
                f"Regime: {regime}\nATR: {atr_pct}%\nSL Multiplier: {sl_mult}x ATR"
            ),
            "prompt": (
                f"Calculate the exact position size for a {pair} trade.\n\n"
                f"Given:\n"
                f"  - Current price: ${price}\n"
                f"  - Portfolio equity: ${equity:,}\n"
                f"  - Market regime: {regime}\n"
                f"  - ATR-14: {atr_pct}% of price\n"
                f"  - Stop-loss distance: {sl_mult}x ATR = {atr_pct * sl_mult:.1f}% from entry\n\n"
                f"Show EVERY step of the math:\n"
                f"1. Base risk budget: 2% of ${equity:,} = $___\n"
                f"2. Regime multiplier: {REGIME_MULTIPLIERS[regime]}x ({regime})\n"
                f"3. Adjusted risk: $__ × {REGIME_MULTIPLIERS[regime]} = $___\n"
                f"4. Stop distance in $: ${price} × {atr_pct * sl_mult:.1f}% = $___\n"
                f"5. Position size (units): risk_$ / stop_$ = ___ units\n"
                f"6. Position size (notional): ___ units × ${price} = $___\n"
                f"7. Position as % of equity: check against 20% max symbol exposure\n"
                f"8. If oversized: cap and recalculate (min of uncapped vs max_notional)\n\n"
                f"The model must learn the MATH, not just the concept."
            ),
        })

    # ── 7. RISK MANAGEMENT EDGE CASES ─────────────────────────
    for _ in range(n_per_cat):
        pair = random.choice(PAIRS)
        price = _price(pair)

        edges = [
            ("Circuit breaker just cleared after 10.5% drawdown. First trade back. "
             "How does RUNECLAW approach re-entry?",
             "circuit_breaker_recovery"),

            (f"Stop-loss on {pair} LONG was at ${_pct(price, -3)}. A wick hit ${_pct(price, -3.2)} "
             f"then price recovered to ${_pct(price, 1)}. Stop was triggered. "
             f"Should you re-enter? Under what conditions?",
             "stop_hunt_reentry"),

            (f"You have {pair} LONG (+5.2% unrealized). Trailing stop is at +2.8%. "
             f"Suddenly BTC drops 3% in 10 minutes. Your {pair.split('/')[0]} hasn't moved yet. "
             f"What do you do?",
             "correlated_risk"),

            (f"Portfolio has 4 altcoin longs open. All are BTC-correlated at 0.85+. "
             f"A new {pair} setup appears with 0.73 confluence. Do you take it?",
             "concentration_risk"),

            (f"You're up +22R this month. Your best month ever. A marginal setup "
             f"appears on {pair} — confluence 0.58, R:R 1.3:1. Take it or protect the gains?",
             "overconfidence_trap"),

            (f"It's Sunday evening. {pair} volume is 30% of weekday average. "
             f"Spread is 0.18% (normally 0.05%). Clean setup though. Trade or wait?",
             "low_liquidity"),

            (f"Your {pair} SHORT is up +3R. You set TP at +5R. Price is stalling. "
             f"Funding just flipped positive (longs paying). Lock profit or hold for TP?",
             "profit_management"),

            (f"Black swan: major exchange hack reported. All crypto dropping 8-15% in 1h. "
             f"You have 3 open longs totaling 4.2% portfolio heat. RUNECLAW protocol?",
             "black_swan"),
        ]
        scenario, subcat = random.choice(edges)

        prompts.append({
            "category": "risk_edge_case",
            "system": TEACHER_CORE,
            "instruction": f"How should RUNECLAW handle this risk scenario with {pair}?",
            "input": f"Symbol: {pair}\nPrice: ${price}\nScenario: {subcat}",
            "prompt": (
                f"As RUNECLAW's risk engine, handle this situation:\n\n"
                f"{scenario}\n\n"
                f"Provide:\n"
                f"1. Immediate risk assessment (severity: LOW/MEDIUM/HIGH/CRITICAL)\n"
                f"2. Which of the 22 risk checks are relevant and their status\n"
                f"3. Exact actions to take (be specific: close X, trail Y, wait Z)\n"
                f"4. The reasoning — why this is the right call\n"
                f"5. What you learned: how to prevent this situation next time\n\n"
                f"Capital preservation first. Always."
            ),
        })

    # ── 8. BACKTEST INTERPRETATION ────────────────────────────
    for _ in range(n_per_cat):
        # Generate realistic backtest metrics
        n_trades = random.randint(40, 200)
        win_rate = round(random.uniform(0.35, 0.65), 2)
        avg_win_r = round(random.uniform(1.2, 3.5), 2)
        avg_loss_r = round(random.uniform(-1.2, -0.8), 2)
        expectancy = round(win_rate * avg_win_r + (1 - win_rate) * avg_loss_r, 3)
        pf = round(abs(win_rate * avg_win_r / ((1 - win_rate) * avg_loss_r)) if avg_loss_r != 0 else 0, 2)
        max_dd = round(random.uniform(5, 25), 1)
        sharpe = round(random.uniform(-0.5, 2.5), 2)
        cost_drag = round(random.uniform(0.5, 3.0), 1)

        prompts.append({
            "category": "backtest_interpretation",
            "system": TEACHER_CORE,
            "instruction": "Interpret these backtest results and give an honest assessment.",
            "input": (
                f"Trades: {n_trades}\nWin Rate: {win_rate*100:.0f}%\n"
                f"Avg Win: +{avg_win_r}R\nAvg Loss: {avg_loss_r}R\n"
                f"Expectancy: {expectancy:+.3f}R\nProfit Factor: {pf}\n"
                f"Max Drawdown: {max_dd}%\nSharpe: {sharpe}\n"
                f"Cost Drag: ~{cost_drag}R"
            ),
            "prompt": (
                f"Interpret these RUNECLAW backtest results honestly:\n\n"
                f"  Trades:       {n_trades}\n"
                f"  Win rate:     {win_rate*100:.0f}%\n"
                f"  Avg win:      +{avg_win_r}R\n"
                f"  Avg loss:     {avg_loss_r}R\n"
                f"  EXPECTANCY:   {expectancy:+.3f}R per trade\n"
                f"  Profit factor: {pf}\n"
                f"  Max drawdown: {max_dd}%\n"
                f"  Sharpe:       {sharpe}\n"
                f"  Cost drag:    ~{cost_drag}R total\n\n"
                f"Provide:\n"
                f"1. Is this a real edge or noise? (with reasoning)\n"
                f"2. Red flags in the numbers (if any)\n"
                f"3. What the cost drag means — is the strategy surviving after fees?\n"
                f"4. Statistical significance: {n_trades} trades — enough to trust?\n"
                f"5. Your verdict: PROMISING / MARGINAL / NO EDGE / INSUFFICIENT DATA\n"
                f"6. What would make this stronger\n\n"
                f"Be brutally honest. A backtest that flatters is worse than useless."
            ),
        })

    # ── 9. REGIME ADAPTATION & STRATEGY SWITCHING ─────────────
    for _ in range(n_per_cat):
        pair = random.choice(PAIRS)
        price = _price(pair)
        from_regime = random.choice(REGIMES)
        to_regime = random.choice([r for r in REGIMES if r != from_regime])

        prompts.append({
            "category": "regime_adaptation",
            "system": TEACHER_CORE,
            "instruction": f"Adapt RUNECLAW strategy as {pair} transitions from {from_regime} to {to_regime}.",
            "input": f"Symbol: {pair}\nPrice: ${price}\nFrom: {from_regime}\nTo: {to_regime}",
            "prompt": (
                f"As RUNECLAW, the market regime for {pair} is shifting from {from_regime} to {to_regime}.\n\n"
                f"Current price: ${price}\n\n"
                f"Walk through the full adaptation:\n"
                f"1. How you detected the regime change (which indicators shifted)\n"
                f"2. Position sizing change: {REGIME_MULTIPLIERS[from_regime]}x → {REGIME_MULTIPLIERS[to_regime]}x\n"
                f"   - Old risk: 2% × {REGIME_MULTIPLIERS[from_regime]} = {2*REGIME_MULTIPLIERS[from_regime]:.1f}%\n"
                f"   - New risk: 2% × {REGIME_MULTIPLIERS[to_regime]} = {2*REGIME_MULTIPLIERS[to_regime]:.1f}%\n"
                f"3. Stop-loss distance adjustment (tighter/wider and why)\n"
                f"4. What setups to look for vs avoid in the new regime\n"
                f"5. Any open positions that need adjustment\n"
                f"6. Common traps in this specific transition\n\n"
                f"Show the math. The model must learn concrete numbers, not abstractions."
            ),
        })

    # ── 10. EDUCATIONAL DEEP DIVES ────────────────────────────
    topics = [
        ("How does the GetClaw Confluence Engine weight its 12 indicators and why those weights?",
         "Include specific weights, what each measures, and how the final score is computed."),
        ("Walk through all 22 risk checks in order. What does each one prevent?",
         "Name each check, its threshold, and give a concrete example of it blocking a trade."),
        ("Explain R-multiples and why RUNECLAW uses them instead of dollar P&L.",
         "Show how R normalizes across different position sizes and makes strategies comparable."),
        ("What makes a backtest honest vs a lying backtest?",
         "Cover look-ahead bias, conservative intrabar rules, cost sensitivity, and walk-forward."),
        ("How does RUNECLAW handle correlated positions and why is this critical?",
         "Explain concentration risk, correlation groups, and portfolio heat calculation."),
        ("Explain the circuit breaker mechanism: when it triggers, what happens, how to recover.",
         "Include the math: 10% drawdown threshold, recovery conditions, and re-entry protocol."),
        ("What is position sizing by fixed-fractional risk and why is it superior to fixed-lot?",
         "Show the math: risk_budget / stop_distance, and how it automatically adjusts to volatility."),
        ("Why does RUNECLAW cap regime multipliers below 1.0 for CHOPPY and VOLATILE markets?",
         "Explain how reduced sizing in adverse regimes protects capital and preserves edge."),
        ("How does order flow analysis complement technical indicators?",
         "Cover CVD, book imbalance, whale tracking, funding rates, and liquidation heatmaps."),
        ("What is the difference between a good loss and a bad loss in RUNECLAW's framework?",
         "Good loss: proper setup, proper sizing, stop hit = system working. Bad loss: rule violation."),
        ("Explain drawdown management: 2% per trade, 5% daily, 10% circuit breaker.",
         "Show how these nested limits create layers of protection against ruin."),
        ("Why does RUNECLAW require human confirmation before execution?",
         "Discuss the human-in-the-loop philosophy: AI analyzes, human decides, system executes."),
        ("How does RUNECLAW avoid revenge trading after a loss streak?",
         "Cover cooldown timers, consecutive loss checks, and the psychology behind the rule."),
        ("What makes a trade setup A+ quality vs B quality vs untradeable?",
         "Define confluence thresholds, R:R tiers, and regime compatibility for each grade."),
        ("Explain walk-forward analysis and why a single backtest is not enough.",
         "Cover rolling windows, out-of-sample validation, and how it detects overfitting."),
        ("How does RUNECLAW calculate and use the Sharpe and Sortino ratios?",
         "Use per-trade returns (not equity snapshots). Explain the difference and why it matters."),
    ]

    for topic, detail in topics:
        n = n_per_cat // len(topics) + 1
        for _ in range(n):
            prompts.append({
                "category": "educational",
                "system": TEACHER_CORE,
                "instruction": topic,
                "input": "",
                "prompt": (
                    f"As RUNECLAW, answer this question thoroughly:\n\n"
                    f"\"{topic}\"\n\n"
                    f"Additional guidance: {detail}\n\n"
                    f"Provide:\n"
                    f"1. Clear explanation of the concept\n"
                    f"2. Concrete examples with specific numbers\n"
                    f"3. Why it matters for trading performance\n"
                    f"4. Common mistakes to avoid\n"
                    f"5. How RUNECLAW implements it specifically\n\n"
                    f"Be educational but practical. This is training data — teach clearly."
                ),
            })

    random.shuffle(prompts)
    return prompts


# ── Generation Pipeline ───────────────────────────────────────

def generate_sample(client, prompt_data):
    """Generate one training sample via Opus."""
    response = client.generate(
        prompt_data["system"],
        prompt_data["prompt"],
        max_tokens=2500,
    )
    if response is None:
        return None

    return {
        "instruction": prompt_data["instruction"],
        "input": prompt_data.get("input", ""),
        "output": response,
        "category": prompt_data["category"],
        "source": "opus_4.8",
    }


def main():
    parser = argparse.ArgumentParser(description="RUNECLAW Opus 4.8 Knowledge Distillation")
    parser.add_argument("--api-key", default=os.environ.get("ANTHROPIC_API_KEY"))
    parser.add_argument("--model", default=None, help=f"Override model (default: {OPUS_MODEL})")
    parser.add_argument("--samples", type=int, default=SAMPLES_PER_CATEGORY,
                        help=f"Samples per category (default {SAMPLES_PER_CATEGORY})")
    parser.add_argument("--concurrent", type=int, default=MAX_CONCURRENT)
    parser.add_argument("--resume", action="store_true", help="Resume from existing file")
    parser.add_argument("--dry", action="store_true", help="Preview prompts without API calls")
    parser.add_argument("--merge-only", action="store_true", help="Skip generation, just merge")
    parser.add_argument("--check", action="store_true", help="Check which models your key can access")
    args = parser.parse_args()

    if args.check:
        if not args.api_key:
            print("ERROR: Set ANTHROPIC_API_KEY first")
            sys.exit(1)
        import warnings
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        try:
            import anthropic
        except ImportError:
            import subprocess
            subprocess.run([sys.executable, "-m", "pip", "install", "anthropic", "-q"])
            import anthropic
        # Ensure latest version
        print("  Upgrading anthropic package to latest...")
        import subprocess as _sp
        _sp.run([sys.executable, "-m", "pip", "install", "--upgrade", "anthropic", "-q"])
        import importlib
        importlib.reload(anthropic)
        print(f"  anthropic version: {getattr(anthropic, '__version__', 'unknown')}")

        client = anthropic.Anthropic(api_key=args.api_key)
        models_to_test = [
            # Current names (from console.anthropic.com)
            "claude-opus-4-8",
            "claude-opus-4-7",
            "claude-opus-4-6",
            "claude-opus-4-5",
            "claude-opus-4-1",
            "claude-sonnet-4-6",
            "claude-haiku-4-5",
            # Aliases
            "claude-opus-4-latest",
            "claude-sonnet-4-latest",
            "claude-haiku-4-latest",
            # Dated
            "claude-opus-4-20260514",
            "claude-sonnet-4-20260514",
            "claude-opus-4-20250514",
            "claude-sonnet-4-20250514",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
        ]
        print("=" * 60)
        print("  Checking model access for your API key...")
        print("=" * 60)
        found = []
        for m in models_to_test:
            try:
                client.messages.create(
                    model=m, max_tokens=5,
                    messages=[{"role": "user", "content": "hi"}],
                )
                print(f"  OK   {m}")
                found.append(m)
            except Exception as e:
                err = str(e)
                if "404" in err or "not_found" in err:
                    print(f"  --   {m} (not available)")
                elif "401" in err:
                    print(f"  XX   {m} (auth error)")
                else:
                    print(f"  ??   {m} ({type(e).__name__})")

        print(f"\n  Available models: {len(found)}")
        if found:
            best = found[0]
            print(f"  Best available: {best}")
            print(f"\n  To use it, run:")
            print(f"  python generate_opus_data.py --model {best} --samples 200")
        else:
            print("\n  No models accessible! Check your API key at console.anthropic.com")
        return

    print("=" * 60)
    print("  RUNECLAW — Opus 4.8 Knowledge Distillation")
    print("=" * 60)

    # Generate prompts
    print(f"\n  Generating prompt set ({args.samples} per category)...")
    prompts = make_prompts(args.samples)

    # Category breakdown
    cats = {}
    for p in prompts:
        cats[p["category"]] = cats.get(p["category"], 0) + 1
    print(f"  Total prompts: {len(prompts)}")
    for cat, count in sorted(cats.items()):
        print(f"    {cat:30s} {count:5d}")

    if args.dry:
        print(f"\n  DRY RUN — showing 3 sample prompts:\n")
        for p in prompts[:3]:
            print(f"  [{p['category']}] {p['instruction']}")
            print(f"  Prompt: {p['prompt'][:200]}...")
            print()
        est_output = len(prompts) * 2000
        est_cost = est_output / 1_000_000 * 25 + len(prompts) * 500 / 1_000_000 * 5
        print(f"  Estimated cost: ~${est_cost:.0f}")
        return

    if not args.merge_only:
        if not args.api_key:
            print("\n  ERROR: No API key!")
            print("  Set ANTHROPIC_API_KEY or pass --api-key")
            sys.exit(1)

        # Initialize client
        print(f"\n  Initializing Opus client...")
        client = OpusClient(args.api_key, model=args.model)

        # Resume support
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        existing_count = 0
        if args.resume and os.path.exists(OPUS_FILE):
            with open(OPUS_FILE) as f:
                existing_count = sum(1 for line in f if line.strip())
            prompts = prompts[existing_count:]
            print(f"  Resuming from {existing_count} existing samples")
            print(f"  Remaining: {len(prompts)} prompts")

        # Cost estimate
        est_output = len(prompts) * 2000
        est_input = len(prompts) * 500
        est_cost = est_output / 1_000_000 * 25 + est_input / 1_000_000 * 5
        print(f"\n  Estimated tokens: ~{est_input + est_output:,}")
        print(f"  Estimated cost:   ~${est_cost:.0f}")

        # Generate
        print(f"\n{'='*60}")
        print(f"  Generating {len(prompts)} samples ({args.concurrent} concurrent)")
        print(f"  Model: {client.model}")
        print(f"{'='*60}\n")

        generated = 0
        failed = 0
        start_time = time.time()
        mode = "a" if args.resume else "w"

        with open(OPUS_FILE, mode, encoding="utf-8") as f:
            batch_size = args.concurrent * 2
            for batch_start in range(0, len(prompts), batch_size):
                batch = prompts[batch_start:batch_start + batch_size]

                with ThreadPoolExecutor(max_workers=args.concurrent) as executor:
                    futures = {
                        executor.submit(generate_sample, client, p): p
                        for p in batch
                    }
                    for future in as_completed(futures):
                        try:
                            result = future.result()
                            if result:
                                f.write(json.dumps(result, ensure_ascii=False) + "\n")
                                f.flush()
                                generated += 1
                            else:
                                failed += 1
                        except Exception as e:
                            print(f"    Exception: {e}")
                            failed += 1

                total_done = existing_count + generated + failed
                total_all = existing_count + len(prompts) + (0 if not args.resume else 0)
                elapsed = time.time() - start_time
                rate = generated / elapsed * 3600 if elapsed > 0 else 0
                cost_so_far = client.cost_estimate()

                print(f"  [{total_done:5d}/{existing_count + len(prompts)}] "
                      f"+{generated} ok, {failed} fail | "
                      f"${cost_so_far:.2f} spent | "
                      f"{rate:.0f}/hr")

        elapsed_total = time.time() - start_time
        final_cost = client.cost_estimate()

        print(f"\n{'='*60}")
        print(f"  Opus generation complete!")
        print(f"  Generated:  {generated}")
        print(f"  Failed:     {failed}")
        print(f"  Time:       {elapsed_total/60:.1f} min")
        print(f"  Cost:       ${final_cost:.2f}")
        print(client.cost_savings_report())
        print(f"  File:       {OPUS_FILE}")
        print(f"{'='*60}")

        # Save stats
        stats = {
            "timestamp": datetime.now().isoformat(),
            "model": client.model,
            "generated": generated,
            "failed": failed,
            "elapsed_min": round(elapsed_total / 60, 1),
            "cost_usd": round(final_cost, 2),
            "input_tokens": client.total_input_tokens,
            "output_tokens": client.total_output_tokens,
            "cache_read_tokens": client.total_cache_read,
            "cache_create_tokens": client.total_cache_create,
        }
        with open(STATS_FILE, "w") as f:
            json.dump(stats, f, indent=2)

    # ── Merge with all existing data ──────────────────────────
    print(f"\n  Merging all training data...")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    all_samples = []
    seen_instructions = set()

    # Load existing sources (in priority order)
    sources = [
        ("combined_training_claude.jsonl", "existing_claude"),
        ("combined_training_v3.jsonl", "existing_v3"),
        ("combined_training.jsonl", "existing_v2"),
    ]

    for filename, label in sources:
        path = os.path.join(OUTPUT_DIR, filename)
        if os.path.exists(path):
            count = 0
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            sample = json.loads(line)
                            key = sample.get("instruction", "")[:100]
                            if key not in seen_instructions:
                                all_samples.append({
                                    "instruction": sample["instruction"],
                                    "input": sample.get("input", ""),
                                    "output": sample["output"],
                                })
                                seen_instructions.add(key)
                                count += 1
                        except json.JSONDecodeError:
                            pass
            print(f"    {filename}: {count} samples (deduped)")
            break  # only load the best existing source

    # Load Opus data
    opus_count = 0
    if os.path.exists(OPUS_FILE):
        with open(OPUS_FILE, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        sample = json.loads(line)
                        all_samples.append({
                            "instruction": sample["instruction"],
                            "input": sample.get("input", ""),
                            "output": sample["output"],
                        })
                        opus_count += 1
                    except json.JSONDecodeError:
                        pass
        print(f"    opus_training.jsonl: {opus_count} Opus samples")

    random.shuffle(all_samples)

    with open(COMBINED_FILE, "w", encoding="utf-8") as f:
        for s in all_samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    print(f"\n  Combined dataset: {len(all_samples)} samples")
    print(f"    Existing: {len(all_samples) - opus_count}")
    print(f"    Opus 4.8: {opus_count}")
    print(f"  Output: {COMBINED_FILE}")

    print(f"""
  ╔══════════════════════════════════════════════════════════╗
  ║  NEXT STEPS                                              ║
  ╠══════════════════════════════════════════════════════════╣
  ║                                                          ║
  ║  1. Upload combined_training_opus.jsonl to Colab          ║
  ║     or copy to BTO desktop for local training             ║
  ║                                                          ║
  ║  2. Train with V2 notebook or train_max_8b.py            ║
  ║     The Opus data will produce better reasoning chains    ║
  ║                                                          ║
  ║  3. Export → GGUF → Ollama                               ║
  ║     ollama create runeclaw -f Modelfile                  ║
  ║                                                          ║
  ║  4. Run eval:                                            ║
  ║     python runeclaw_eval.py                              ║
  ║     Compare against 56.8/100 baseline                    ║
  ║                                                          ║
  ╚══════════════════════════════════════════════════════════╝
""")


if __name__ == "__main__":
    main()
