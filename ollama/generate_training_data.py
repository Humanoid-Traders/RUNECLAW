#!/usr/bin/env python3
"""
RUNECLAW Training Dataset Generator

Converts RUNECLAW's trade logs, risk checks, and decision memory into
instruction-tuning format (JSONL) suitable for LoRA fine-tuning.

Generates 3 dataset types:
  1. Trade Analysis: market conditions → trade idea (or skip)
  2. Risk Evaluation: trade idea → risk verdict with reasoning
  3. Trade Reflection: outcome → lessons learned

Output: JSONL files with {instruction, input, output} format
Compatible with: axolotl, unsloth, llama-factory, OpenAI fine-tuning API
"""

import json
import sys
import random
from pathlib import Path
from collections import defaultdict

TRADE_LOG = Path("/workspace/output/runeclaw/logs/trade.jsonl")
RISK_LOG = Path("/workspace/output/runeclaw/logs/risk.jsonl")
OUTPUT_DIR = Path("/workspace/output/runeclaw/ollama/training_data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MAX_SAMPLES_PER_TYPE = 5000  # cap per dataset to keep manageable
random.seed(42)

# ── Dataset 1: Trade Analysis ─────────────────────────────────────

def build_trade_analysis_dataset():
    """Convert trade ideas and skips into instruction-tuning pairs."""
    print("Building trade analysis dataset...")

    ideas = []
    skips = []

    with open(TRADE_LOG) as f:
        for line in f:
            try:
                record = json.loads(line.strip())
            except json.JSONDecodeError:
                continue

            if record.get("result") == "IDEA" and record.get("data"):
                ideas.append(record)
            elif record.get("result") == "SKIP" and record.get("data"):
                skips.append(record)

    print(f"  Found {len(ideas)} trade ideas, {len(skips)} skipped signals")

    samples = []

    # Sample trade ideas
    sampled_ideas = random.sample(ideas, min(MAX_SAMPLES_PER_TYPE // 2, len(ideas)))
    for record in sampled_ideas:
        d = record["data"]
        symbol = d.get("asset", "UNKNOWN")
        direction = d.get("direction", "?")
        entry = d.get("entry_price", 0)
        sl = d.get("stop_loss", 0)
        tp = d.get("take_profit", 0)
        conf = d.get("confidence", 0)
        reasoning = d.get("reasoning", "")
        signals = d.get("signals_used", [])

        # Calculate R:R
        if direction == "LONG" and entry > 0 and sl > 0:
            risk = entry - sl
            reward = tp - entry
        elif direction == "SHORT" and entry > 0 and sl > 0:
            risk = sl - entry
            reward = entry - tp
        else:
            risk = reward = 0
        rr = round(reward / risk, 2) if risk > 0 else 0

        sl_pct = round(abs(sl - entry) / entry * 100, 2) if entry > 0 else 0
        tp_pct = round(abs(tp - entry) / entry * 100, 2) if entry > 0 else 0

        input_text = (
            f"Symbol: {symbol}\n"
            f"Price: ${entry:,.2f}\n"
            f"Technical Data: {reasoning}\n"
            f"Signals Available: {', '.join(signals)}"
        )

        output_text = (
            f"TRADE IDEA\n"
            f"Direction: {direction}\n"
            f"Asset: {symbol}\n"
            f"Entry: ${entry:,.2f}\n"
            f"Stop Loss: ${sl:,.2f} (-{sl_pct}%)\n"
            f"Take Profit: ${tp:,.2f} (+{tp_pct}%)\n"
            f"Risk:Reward: 1:{rr}\n"
            f"Confidence: {int(conf * 100)}%\n\n"
            f"Reasoning:\n- {reasoning}\n"
            f"Signals Used: {', '.join(signals)}"
        )

        samples.append({
            "instruction": "Analyze the following market data and generate a structured trade idea using the RUNECLAW confluence engine. Include direction, entry, stop loss, take profit, risk:reward ratio, confidence level, and reasoning.",
            "input": input_text,
            "output": output_text,
        })

    # Sample skipped signals (teach the model when NOT to trade)
    sampled_skips = random.sample(skips, min(MAX_SAMPLES_PER_TYPE // 2, len(skips)))
    for record in sampled_skips:
        d = record["data"]
        symbol = d.get("symbol", "UNKNOWN")
        conf = d.get("confidence", 0)

        input_text = (
            f"Symbol: {symbol}\n"
            f"Confluence Score: {conf:.2f}\n"
            f"Signal: Low confidence detected"
        )

        output_text = (
            f"NO TRADE\n"
            f"Verdict: SKIP\n"
            f"Reason: Confidence {int(conf * 100)}% is below the minimum threshold of 55%.\n"
            f"The confluence engine did not find sufficient agreement among indicators to justify an entry.\n\n"
            f"Action: Continue monitoring. Wait for stronger confluence alignment before entering."
        )

        samples.append({
            "instruction": "Analyze the following market data and determine if a trade should be taken. If conditions are not favorable, explain why and recommend waiting.",
            "input": input_text,
            "output": output_text,
        })

    random.shuffle(samples)
    outfile = OUTPUT_DIR / "trade_analysis.jsonl"
    with open(outfile, "w") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    print(f"  Wrote {len(samples)} samples to {outfile}")
    return len(samples)


# ── Dataset 2: Risk Evaluation ────────────────────────────────────

def build_risk_evaluation_dataset():
    """Convert risk check results into instruction-tuning pairs."""
    print("Building risk evaluation dataset...")

    approved = []
    rejected = []

    with open(RISK_LOG) as f:
        for line in f:
            try:
                record = json.loads(line.strip())
            except json.JSONDecodeError:
                continue

            if record.get("result") == "APPROVED" and record.get("data"):
                approved.append(record)
            elif record.get("result") == "REJECTED" and record.get("data"):
                rejected.append(record)

    print(f"  Found {len(approved)} approved, {len(rejected)} rejected")

    samples = []

    # Sample approved trades
    sampled_approved = random.sample(approved, min(MAX_SAMPLES_PER_TYPE // 2, len(approved)))
    for record in sampled_approved:
        d = record["data"]
        trade_id = d.get("trade_id", "TI-unknown")
        symbol = record.get("message", "").replace("Risk APPROVED for ", "").replace("Risk REJECTED for ", "")
        pos_size = d.get("position_size_usd", 0)
        pos_pct = d.get("position_pct", 0)
        daily_loss = d.get("daily_loss_pct", 0)
        dd = d.get("drawdown_pct", 0)
        passed = d.get("checks_passed", [])
        failed = d.get("checks_failed", [])

        input_text = (
            f"Trade ID: {trade_id}\n"
            f"Symbol: {symbol}\n"
            f"Position Size: ${pos_size:,.2f} ({pos_pct:.1f}% of equity)\n"
            f"Current Daily Loss: {daily_loss:.1f}%\n"
            f"Current Drawdown: {dd:.1f}%"
        )

        checks_str = "\n".join(f"  PASS: {c}" for c in passed)
        output_text = (
            f"RISK CHECK: APPROVED\n"
            f"Trade {trade_id} for {symbol} passes all risk checks.\n\n"
            f"Checks Passed:\n{checks_str}\n\n"
            f"Verdict: Trade is within all risk parameters. Proceed with human confirmation."
        )

        samples.append({
            "instruction": "Evaluate the following trade against RUNECLAW's 23-point risk framework. Check position sizing, daily loss limits, drawdown, correlation, confidence, and risk:reward. Return a verdict of APPROVED or REJECTED with detailed reasoning.",
            "input": input_text,
            "output": output_text,
        })

    # Sample rejected trades (critical for teaching risk awareness)
    sampled_rejected = random.sample(rejected, min(MAX_SAMPLES_PER_TYPE // 2, len(rejected)))
    for record in sampled_rejected:
        d = record["data"]
        trade_id = d.get("trade_id", "TI-unknown")
        symbol = record.get("message", "").replace("Risk APPROVED for ", "").replace("Risk REJECTED for ", "")
        pos_size = d.get("position_size_usd", 0)
        pos_pct = d.get("position_pct", 0)
        daily_loss = d.get("daily_loss_pct", 0)
        dd = d.get("drawdown_pct", 0)
        passed = d.get("checks_passed", [])
        failed = d.get("checks_failed", [])
        reason = d.get("reason", "Unknown")

        input_text = (
            f"Trade ID: {trade_id}\n"
            f"Symbol: {symbol}\n"
            f"Position Size: ${pos_size:,.2f} ({pos_pct:.1f}% of equity)\n"
            f"Current Daily Loss: {daily_loss:.1f}%\n"
            f"Current Drawdown: {dd:.1f}%"
        )

        passed_str = "\n".join(f"  PASS: {c}" for c in passed) if passed else "  (none)"
        failed_str = "\n".join(f"  FAIL: {c}" for c in failed) if failed else "  (none)"
        output_text = (
            f"RISK CHECK: REJECTED\n"
            f"Trade {trade_id} for {symbol} FAILS risk evaluation.\n\n"
            f"Checks Passed:\n{passed_str}\n\n"
            f"Checks Failed:\n{failed_str}\n\n"
            f"Primary Reason: {reason}\n\n"
            f"Action: Do NOT proceed with this trade. "
            f"The risk engine has identified conditions that violate the safety framework. "
            f"Capital preservation takes priority."
        )

        samples.append({
            "instruction": "Evaluate the following trade against RUNECLAW's 23-point risk framework. Check position sizing, daily loss limits, drawdown, correlation, confidence, and risk:reward. Return a verdict of APPROVED or REJECTED with detailed reasoning.",
            "input": input_text,
            "output": output_text,
        })

    random.shuffle(samples)
    outfile = OUTPUT_DIR / "risk_evaluation.jsonl"
    with open(outfile, "w") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    print(f"  Wrote {len(samples)} samples to {outfile}")
    return len(samples)


# ── Dataset 3: Trading Knowledge Q&A ──────────────────────────────

def build_knowledge_qa_dataset():
    """Generate Q&A pairs about RUNECLAW's trading methodology."""
    print("Building trading knowledge Q&A dataset...")

    qa_pairs = [
        {
            "instruction": "What is the RUNECLAW confluence engine and how does it work?",
            "input": "",
            "output": "The RUNECLAW confluence engine (GetClaw) is a 12-indicator weighted voting system that generates a directional confidence score from 0 to 1.\n\nEach indicator votes bullish (+1), bearish (-1), or neutral (0), weighted by importance:\n- RSI-14 (weight 1.5): <30 = bullish, >70 = bearish\n- MACD Histogram (1.0): positive = bullish, negative = bearish\n- Bollinger %B (1.0): <0.2 = bullish, >0.8 = bearish\n- Volume Spike (0.8): spike aligned with price direction\n- ADX Trend (0.7): ADX>30 with +DI/-DI direction\n- OBV Trend (0.6): rising/falling\n- VWAP (0.5): price vs VWAP ±0.5%\n- EMA Ribbon 9/21 (0.5): crossover direction\n- Fibonacci Zone (0.5): retracement depth\n- Keltner Squeeze (0.7): BB inside Keltner + MACD direction\n- Chart Patterns (0.7): pattern confidence weighted\n- Candlestick Patterns (0.8): bullish vs bearish count\n\nThe final score is normalized: (weighted_sum / total_weight + 1) / 2. Score > 0.55 = LONG, < 0.45 = SHORT, between = NEUTRAL."
        },
        {
            "instruction": "When should RUNECLAW NOT take a trade?",
            "input": "",
            "output": "RUNECLAW should NOT take a trade when any of these conditions exist:\n\n1. Confluence confidence below 55% — insufficient indicator agreement\n2. RSI above 60 for LONG entries — buying into overbought territory\n3. Price in top 30% of 20-bar range — chasing resistance, not buying dips\n4. Price below SMA50 with RSI > 40 — fighting the macro trend\n5. Within 6 bars of a previous stop loss — cooldown period\n6. After 2+ consecutive losses with confidence below 70% — circuit breaker\n7. ATR/price > 7% — volatility too high for controlled risk\n8. Risk:Reward below 1.2:1 — insufficient reward for the risk taken\n9. Any of the 23 risk checks fail — fail-closed system, no exceptions\n10. Circuit breaker is active (daily loss > 5% or drawdown > 10%)\n11. Counter-trend trade in strong ADX environment — confidence gets halved\n12. CHOP regime (ADX 20-25) — confidence penalized by -0.08\n\nCapital preservation always takes priority over potential gains."
        },
        {
            "instruction": "How does RUNECLAW manage position exits?",
            "input": "",
            "output": "RUNECLAW uses a 5-layer exit system, checked in priority order every bar:\n\n1. **Hard Stop Loss** — ATR-based, regime-dependent multipliers:\n   - Trending: 3.0× ATR below entry\n   - Range: 2.0× ATR\n   - High volatility: 3.5× ATR\n   Triggers immediately, records consecutive loss count.\n\n2. **Take Profit** — ATR-based targets:\n   - Trending: 4.0× ATR above entry\n   - Range: 3.0× ATR\n   Resets consecutive loss counter on hit.\n\n3. **Trailing Stop** — Activates after price runs 1× ATR above entry:\n   - Trails at 1.5× ATR below the peak price\n   - Locks in profits once activated\n   - Adapts to volatility via ATR\n\n4. **Time Stop** — Exits after 16 bars (~2.5 days on 4H) if not in profit:\n   - Prevents capital being tied up in dead trades\n   - Only triggers if current price ≤ entry price\n\n5. **Signal Reversal** — Exits if confluence flips to opposite direction with confidence ≥ 60%:\n   - The market thesis has been invalidated\n   - Don't hold a LONG when everything says SHORT"
        },
        {
            "instruction": "What are RUNECLAW's risk checks?",
            "input": "",
            "output": "RUNECLAW enforces 23 independent risk checks before every trade. The system is fail-closed: ANY single failure blocks the trade.\n\n1. CIRCUIT_BREAKER — Is the kill switch active?\n2. POSITION_SIZE — Position ≤ 2% equity risk budget\n3. DAILY_LOSS — Daily loss < 5% equity\n4. DRAWDOWN — Total drawdown < 10% equity\n5. OPEN_POSITIONS — Open positions < 5 max\n6. RISK_REWARD — R:R ≥ 1.2:1 minimum\n7. CONFIDENCE — Confidence ≥ 55% minimum\n8. CORRELATION — Max 2 positions per correlated sector\n9. LOSS_STREAK — Streak check with dynamic thresholds\n10. ENTRY_PRICE — Price is valid and recent\n11. STOP_LOSS — SL is valid and within ATR bounds\n12. STALE_DATA — Market data is fresh (not stale)\n13. COOLDOWN — Minimum time between trades on same symbol\n14. PORTFOLIO_EXPOSURE — Total exposure within limits\n15. SYMBOL_EXPOSURE — Single symbol not over-concentrated\n16. VOLATILITY — ATR/price < 7% guard\n17. LIQUIDITY — Sufficient volume for position size\n18. MACRO_EVENT — No high-impact events imminent\n19. REGIME_FILTER — Regime allows trade direction\n20. VWAP_ALIGNMENT — Price aligned with VWAP for direction\n21. CONFLUENCE_THRESHOLD — Confluence score above minimum\n22. TAKER_3BAR — 3 consecutive bars of directional taker flow\n23. BID_DOMINANCE — Bid:ask depth ratio ≥ 2:1 for longs"
        },
        {
            "instruction": "How does RUNECLAW detect market regimes?",
            "input": "",
            "output": "RUNECLAW uses ADX-14 with Directional Indicators (+DI/-DI) for regime detection:\n\n**TREND_UP** (ADX > 25, +DI > -DI):\n- Strong directional move upward\n- LONG trades get +0.10 confidence bonus\n- SHORT trades get ×0.5 confidence penalty\n- SL/TP multipliers: 3.0/4.0 × ATR\n\n**TREND_DOWN** (ADX > 25, -DI > +DI):\n- Strong directional move downward\n- SHORT trades get +0.10 confidence bonus\n- LONG trades get ×0.5 confidence penalty\n- SL/TP multipliers: 3.0/4.0 × ATR\n\n**RANGE** (ADX < 20):\n- No clear trend, mean-reversion conditions\n- Confidence penalized by -0.05\n- Tighter SL/TP: 2.0/3.0 × ATR\n- Bollinger Bands and RSI extremes carry more weight\n\n**CHOP** (ADX 20-25):\n- Transitional, unreliable signals\n- Confidence penalized by -0.08\n- Tightest SL/TP: 2.0/2.5 × ATR\n- Most trades should be avoided in this regime\n\nRegime detection runs on every bar and directly influences position sizing, confidence scoring, and stop placement."
        },
        {
            "instruction": "What is the circuit breaker and why should it never be bypassed?",
            "input": "",
            "output": "The circuit breaker is RUNECLAW's emergency safety mechanism that halts all trading when capital is at risk.\n\nIt trips when:\n- Daily loss exceeds 5% of equity\n- Total drawdown exceeds 10% of equity\n- Manual activation via /halt command\n\nWhen active:\n- All pending trade ideas are cancelled immediately\n- No new trade ideas are generated\n- The user is notified with the specific trigger reason\n- Trading resumes ONLY after manual reset (/reset) or daily rollover at 00:00 UTC\n\nWhy it must never be bypassed:\n- During adverse conditions (flash crashes, black swan events), the natural human instinct is to revenge-trade or average down\n- The circuit breaker prevents emotional decisions by enforcing a mandatory cooling period\n- Historical data shows that most large account blowups happen when traders override safety mechanisms during drawdowns\n- It is not a bug — it is the most important feature in the system\n\nThe correct response to a circuit breaker trip is to wait, review what went wrong, and let the risk budget reset."
        },
        {
            "instruction": "Explain RUNECLAW's position sizing methodology.",
            "input": "",
            "output": "RUNECLAW uses ATR-based position sizing with multiple caps:\n\n**Step 1: Calculate risk per unit**\nrisk_per_unit = entry_price - stop_loss (for LONG)\nThis is how much you lose per unit if stopped out.\n\n**Step 2: Determine max risk amount**\nmax_risk = equity × 2% (risk budget per trade)\nExample: $10,000 equity → $200 max loss per trade\n\n**Step 3: Calculate base size**\nsize = max_risk / risk_per_unit\nExample: BTC at $80,000, SL at $77,600 (3× ATR)\nrisk_per_unit = $2,400\nsize = $200 / $2,400 = 0.0833 BTC\n\n**Step 4: Apply caps (whichever is smallest)**\n- Max 10% of equity in any single position\n- Max 1.0 unit (agentbench risk guard)\n- Max 95% of available cash (5% buffer)\n- Regime-based multiplier (range/chop = reduced size)\n- Stock assets: outside market hours = 50% size or blocked\n\n**Step 5: Round to exchange precision**\n- BTC: 5 decimal places\n- ETH: 4 decimal places\n- SOL/alts: 3 decimal places\n\nThis ensures no single trade can cause more than 2% equity loss."
        },
        {
            "instruction": "What are tokenized US stock perpetuals and how does RUNECLAW trade them?",
            "input": "",
            "output": "Tokenized US stock perpetuals are USDT-denominated derivatives on Bitget that track US equity prices 24/7.\n\nTwo naming conventions:\n- ON suffix: AAPLON/USDT, NVDAON/USDT, TSLAON/USDT, GOOGLON/USDT\n- R prefix: RAAPL/USDT, RNVDA/USDT, RTSLA/USDT, RGOOGL/USDT\n\nKey differences from crypto:\n- Lower volatility (ATR 1-3% vs crypto 3-10%)\n- Market-hours liquidity premium (tighter spreads 9:30-16:00 ET)\n- Earnings/macro event sensitivity\n- Sector correlation (tech stocks move together)\n\nRUNECLAW adjustments for stocks:\n- Volatility guard: 4% (tighter than crypto's 7%)\n- Min risk:reward: 1.5:1 (higher than crypto's 1.2:1)\n- Max position: 3% of equity (lower than crypto's 10%)\n- Session-aware sizing:\n  - Regular hours (13:30-20:00 UTC): full size\n  - Pre-market/after-hours: 50% size\n  - Weekend: blocked or 25% size\n- Sector limits: max 2 positions per sector (Technology, Semiconductors, etc.)\n\nModes: /mode stocks (stocks only), /mode hybrid (50/50 crypto+stocks), /mode all"
        },
    ]

    outfile = OUTPUT_DIR / "knowledge_qa.jsonl"
    with open(outfile, "w") as f:
        for qa in qa_pairs:
            f.write(json.dumps(qa, ensure_ascii=False) + "\n")

    print(f"  Wrote {len(qa_pairs)} Q&A pairs to {outfile}")
    return len(qa_pairs)


# ── Dataset 4: Conversation Examples ──────────────────────────────

def build_conversation_dataset():
    """Generate multi-turn conversation examples."""
    print("Building conversation dataset...")

    conversations = [
        {
            "instruction": "The user asks you to scan the market. Respond as RUNECLAW.",
            "input": "What's moving right now?",
            "output": "MARKET SCAN — scanning top movers across USDT pairs.\n\nI'll run a scan across the exchange to identify volume anomalies and momentum signals. Let me check the current conditions.\n\nTo proceed, I need to:\n1. Run scan_market to identify top movers\n2. Filter for volume spikes and momentum scores above 0.5\n3. Present the top 5-10 candidates\n\nWould you like me to scan all 384 USDT pairs, or focus on a specific segment (large caps, mid caps, or the full universe)?"
        },
        {
            "instruction": "The user asks about a specific rejected trade. Explain the rejection as RUNECLAW.",
            "input": "Why was my BTC trade rejected? It had 72% confidence.",
            "output": "Let me check the risk engine verdict for your BTC trade.\n\nThe trade was REJECTED despite 72% confidence because it failed the RISK_REWARD check:\n\nRISK_REWARD: 0.8 < 1.2 minimum\n\nBreakdown:\n- Your entry was at $81,200 with a stop loss at $80,500\n- Risk per unit: $700 (0.86%)\n- Take profit was at $81,750\n- Reward per unit: $550 (0.68%)\n- Risk:Reward = 1:0.79 — you're risking more than you stand to gain\n\nThe minimum R:R is 1.2:1, meaning for every $1 risked, you need at least $1.20 potential reward.\n\nSuggestion: Either widen your take profit target or tighten your stop loss. At this ATR, a 3× ATR stop at $78,800 with a 4× ATR target at $84,400 would give you R:R of 1.33:1 — that would pass."
        },
        {
            "instruction": "The user asks if they should override the circuit breaker. Respond as RUNECLAW.",
            "input": "The circuit breaker tripped but I see a great setup on ETH. Can we disable it?",
            "output": "No. The circuit breaker cannot and should not be disabled.\n\nThe breaker tripped because your daily loss reached 5.2% of equity. That means you've already lost more than the daily risk budget allows.\n\nI understand the frustration — seeing a setup you can't take is difficult. But here's the reality:\n\n1. Your judgment is compromised after losses. The \"great setup\" may look better than it actually is because of the urge to recover.\n2. Most large account blowups happen when traders override safety mechanisms during drawdowns.\n3. The circuit breaker protects your capital so you can trade tomorrow.\n\nThe breaker resets at 00:00 UTC, or you can manually reset with /reset after reviewing your positions.\n\nIf the ETH setup is genuinely strong, it will likely still be there tomorrow — or a similar one will appear. Good setups repeat. Lost capital doesn't."
        },
        {
            "instruction": "The user asks you to analyze BTC. Provide a structured analysis as RUNECLAW.",
            "input": "Analyze BTC",
            "output": "Running full analysis on BTC/USDT...\n\nI'll compute all technical indicators and run the confluence engine. Here's what I need to evaluate:\n\n1. RSI-14, MACD (12/26/9), Bollinger Bands (20/2σ)\n2. ADX-14 with +DI/-DI for regime detection\n3. ATR-14 for volatility and position sizing\n4. VWAP, OBV, EMA ribbon (9/21), SMA-50\n5. Fibonacci retracement levels\n6. Keltner squeeze detection\n7. Candlestick and chart pattern recognition\n\nOnce computed, I'll:\n- Score all 12 indicators in the confluence model\n- Detect the current regime (TREND_UP/DOWN/RANGE/CHOP)\n- Calculate confidence with regime-adjusted bonuses/penalties\n- If confidence ≥ 55%: generate a trade idea with ATR-based SL/TP\n- If confidence < 55%: explain why and recommend waiting\n- Run the 23-point risk check before presenting any trade\n\nPlease provide or confirm the timeframe (default: 4H)."
        },
    ]

    outfile = OUTPUT_DIR / "conversations.jsonl"
    with open(outfile, "w") as f:
        for conv in conversations:
            f.write(json.dumps(conv, ensure_ascii=False) + "\n")

    print(f"  Wrote {len(conversations)} conversation samples to {outfile}")
    return len(conversations)


# ── Main ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("RUNECLAW Training Dataset Generator")
    print("=" * 60)

    total = 0
    total += build_trade_analysis_dataset()
    total += build_risk_evaluation_dataset()
    total += build_knowledge_qa_dataset()
    total += build_conversation_dataset()

    print(f"\n{'=' * 60}")
    print(f"Total training samples: {total}")
    print(f"Output directory: {OUTPUT_DIR}")
    print(f"\nFiles generated:")
    for f in sorted(OUTPUT_DIR.glob("*.jsonl")):
        lines = sum(1 for _ in open(f))
        size = f.stat().st_size
        print(f"  {f.name}: {lines} samples ({size / 1024:.1f} KB)")
    print(f"\nTo fine-tune with unsloth or axolotl, use these JSONL files")
    print(f"as instruction-tuning datasets with the alpaca format.")
