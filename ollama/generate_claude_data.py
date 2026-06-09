#!/usr/bin/env python3
"""
RUNECLAW - Claude-Powered Training Data Generator
===================================================
Uses Claude API to generate expert-quality training data via
knowledge distillation. Claude acts as a "senior trading analyst"
producing nuanced, reasoning-rich examples that our smaller
model learns from.

This produces dramatically higher quality data than template-based
generators because Claude can:
  - Reason about WHY a setup is valid or invalid
  - Generate natural, varied language (not formulaic)
  - Produce multi-step chain-of-thought analysis
  - Handle edge cases with nuanced judgment

Usage:
  set ANTHROPIC_API_KEY=sk-ant-...
  python generate_claude_data.py

  Or pass key directly:
  python generate_claude_data.py --api-key sk-ant-...

Output:
  ./training_data/claude_training.jsonl
  ./training_data/combined_training_claude.jsonl  (merged with existing)

Cost estimate:
  ~5000 samples x ~1500 tokens each ≈ 7.5M tokens
  Sonnet: ~$22.50 input + $37.50 output ≈ $60 total
  Haiku:  ~$3.75 input + $6.25 output ≈ $10 total
"""

import os
import sys
import json
import time
import random
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

random.seed(42)

# ── Configuration ─────────────────────────────────────────────

OUTPUT_DIR = "./training_data"
CLAUDE_FILE = os.path.join(OUTPUT_DIR, "claude_training.jsonl")
COMBINED_FILE = os.path.join(OUTPUT_DIR, "combined_training_claude.jsonl")

# How many samples to generate per category
samples_per_category = 500
MAX_CONCURRENT = 5      # parallel API calls
RETRY_DELAY = 2         # seconds between retries
MAX_RETRIES = 3

SYSTEM_PROMPT = (
    "You are RUNECLAW, an AI trading analyst. You analyze cryptocurrency "
    "markets using the GetClaw Confluence Engine (12 weighted indicators), "
    "enforce strict risk management through 23 automated checks, and generate "
    "structured trade ideas. You never execute without human confirmation. "
    "Capital preservation above all."
)

PAIRS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
    "ADA/USDT", "AVAX/USDT", "DOGE/USDT", "LINK/USDT", "UNI/USDT",
    "ARB/USDT", "OP/USDT", "SUI/USDT", "INJ/USDT", "TIA/USDT",
    "NEAR/USDT", "FET/USDT", "RENDER/USDT", "RUNE/USDT", "APT/USDT",
]

TIMEFRAMES = ["5m", "15m", "1h", "4h", "1d"]


# ── Claude API Client ─────────────────────────────────────────

class ClaudeClient:
    def __init__(self, api_key, model=None):
        try:
            import anthropic
        except ImportError:
            print("Installing anthropic package...")
            import subprocess
            subprocess.run([sys.executable, "-m", "pip", "install", "anthropic", "-q"])
            import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)

        # Auto-detect best model
        if model:
            self.model = model
        else:
            # Try models from newest to oldest
            candidates = [
                "claude-opus-4-20250514",
                "claude-sonnet-4-20250514",
                "claude-3-5-sonnet-20241022",
                "claude-3-5-haiku-20241022",
                "claude-3-haiku-20240307",
            ]
            self.model = None
            for m in candidates:
                try:
                    self.client.messages.create(
                        model=m,
                        max_tokens=10,
                        messages=[{"role": "user", "content": "test"}],
                    )
                    self.model = m
                    break
                except Exception as e:
                    print(f"  {m}: not available ({type(e).__name__})")
                    continue

            if not self.model:
                print("\n  ERROR: No Claude model available with your API key!")
                print("  Check your API key and plan at console.anthropic.com")
                sys.exit(1)

        print(f"  Using model: {self.model}")

    def generate(self, system, user_prompt, max_tokens=1500):
        """Generate a response from Claude."""
        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                return response.content[0].text
            except Exception as e:
                if "rate" in str(e).lower() or "429" in str(e):
                    wait = RETRY_DELAY * (attempt + 1) * 2
                    print(f"    Rate limited, waiting {wait}s...")
                    time.sleep(wait)
                elif attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY)
                else:
                    print(f"    ERROR: {e}")
                    return None


# ── Prompt Templates ──────────────────────────────────────────

TEACHER_SYSTEM = """You are a senior cryptocurrency trading analyst and risk manager with 15 years of experience.
You are training an AI assistant called RUNECLAW. Generate realistic, detailed trading analysis
that demonstrates expert-level reasoning.

IMPORTANT RULES:
- Always include specific numbers (prices, percentages, ratios)
- Show your reasoning step-by-step
- Be decisive — give a clear recommendation (trade or no trade)
- Always mention risk management
- Never recommend trading without stop loss
- Capital preservation is the #1 priority
- Use the GetClaw Confluence Engine (12 weighted indicators)
- Reference the 23 automated risk checks where relevant
- Human confirmation is always required before execution

Keep responses between 200-500 words. Be professional and structured."""


def make_prompts(samples_per_category=500):
    """Create diverse prompt sets for Claude to respond to."""
    prompts = []

    # Category 1: Detailed Trade Analysis
    for _ in range(samples_per_category):
        pair = random.choice(PAIRS)
        tf = random.choice(TIMEFRAMES)
        direction = random.choice(["bullish", "bearish", "neutral"])
        regime = random.choice(["strong uptrend", "downtrend", "range-bound",
                                "high volatility", "accumulation phase", "distribution phase"])

        prompts.append({
            "category": "trade_analysis",
            "instruction": f"Analyze {pair} on the {tf} timeframe for trade setups.",
            "claude_prompt": (
                f"Generate a detailed RUNECLAW trade analysis for {pair} on the {tf} timeframe.\n\n"
                f"Context: The market is currently {regime} and the bias appears {direction}.\n\n"
                f"Include:\n"
                f"1. GetClaw Confluence Engine score with specific indicators\n"
                f"2. Entry, stop loss, and take profit levels (use realistic prices)\n"
                f"3. Risk:reward ratio calculation\n"
                f"4. Position sizing recommendation\n"
                f"5. Key risk factors and which of the 23 checks pass/fail\n"
                f"6. Clear APPROVED/REJECTED verdict with reasoning\n\n"
                f"Format as a structured trade report that RUNECLAW would output."
            ),
        })

    # Category 2: Complex Multi-Timeframe Reasoning
    for _ in range(samples_per_category):
        pair = random.choice(PAIRS)
        scenarios = [
            "Higher timeframes bullish but 15m showing bearish divergence",
            "Daily in downtrend but 4h forming double bottom",
            "Weekly range-bound, daily bullish, 4h bearish — conflicting signals",
            "All timeframes aligned bullish with volume confirmation",
            "4h trend exhaustion signals while daily still bullish",
            "Monthly support level being tested on high volume",
        ]
        scenario = random.choice(scenarios)

        prompts.append({
            "category": "multi_timeframe",
            "instruction": f"Perform multi-timeframe analysis on {pair}.",
            "claude_prompt": (
                f"As RUNECLAW, perform multi-timeframe analysis for {pair}.\n\n"
                f"Scenario: {scenario}\n\n"
                f"Analyze each timeframe (15m, 1h, 4h, 1d) and explain:\n"
                f"1. What each timeframe is telling you\n"
                f"2. Where they agree and conflict\n"
                f"3. Which timeframe takes priority and why\n"
                f"4. Your overall recommendation (trade, wait, or stand aside)\n"
                f"5. If trading: which timeframe to use for entry timing\n\n"
                f"Show the chain of reasoning that leads to your decision."
            ),
        })

    # Category 3: Risk Management Decision
    for _ in range(samples_per_category):
        pair = random.choice(PAIRS)
        situations = [
            f"You have 3 open positions and {pair} shows a strong setup. Portfolio heat is at 5.2%.",
            f"Your last 3 trades were losses. A high-confluence setup appears on {pair}.",
            f"{pair} is pumping 15% in 2 hours. FOMO is tempting. Should you chase?",
            f"Your {pair} long is up 8% but momentum is weakening. Take profit or hold?",
            f"A whale just dumped $50M of {pair.split('/')[0]}. Your long is at -2%. What now?",
            f"Breaking news about {pair.split('/')[0]} regulation. You have an open position.",
            f"Your stop loss was hunted by a wick. Price recovered. Re-enter or wait?",
            f"Funding rates are extremely negative on {pair}. Is this a contrarian long signal?",
            f"{pair} is at a key support level but volume is declining. Trade or wait?",
            f"Your winning streak: 7 in a row. Position size temptation is growing.",
        ]
        situation = random.choice(situations)

        prompts.append({
            "category": "risk_management",
            "instruction": f"How should RUNECLAW handle this situation with {pair}?",
            "claude_prompt": (
                f"As RUNECLAW's risk management engine, advise on this situation:\n\n"
                f"{situation}\n\n"
                f"Provide:\n"
                f"1. Your immediate assessment of the risk\n"
                f"2. Which of the 23 risk checks are relevant\n"
                f"3. Your specific recommendation (with exact actions)\n"
                f"4. The reasoning behind your decision\n"
                f"5. What could go wrong if you ignore the risk management rules\n\n"
                f"Always prioritize capital preservation over profit opportunity."
            ),
        })

    # Category 4: Market Regime & Strategy Adaptation
    for _ in range(samples_per_category):
        pair = random.choice(PAIRS)
        transitions = [
            "trending to ranging", "ranging to breakout", "low volatility to high volatility",
            "accumulation to markup", "distribution to markdown", "bull trap forming",
            "bear trap forming", "liquidity grab above resistance", "spring below support",
            "volatility squeeze about to expand",
        ]
        transition = random.choice(transitions)

        prompts.append({
            "category": "regime_adaptation",
            "instruction": f"Classify the market regime for {pair} and adapt strategy.",
            "claude_prompt": (
                f"As RUNECLAW, analyze a regime transition for {pair}.\n\n"
                f"The market appears to be transitioning from {transition}.\n\n"
                f"Explain:\n"
                f"1. How you identified this regime change (which indicators)\n"
                f"2. What the current regime means for trading strategy\n"
                f"3. How to adapt position sizing, stop distances, and trade selection\n"
                f"4. Common traps in this transition and how to avoid them\n"
                f"5. What would confirm or invalidate the regime change\n\n"
                f"Be specific with indicator values and thresholds."
            ),
        })

    # Category 5: No-Trade Reasoning
    for _ in range(samples_per_category):
        pair = random.choice(PAIRS)
        why_no_trade = [
            "confluence score is borderline at 0.58",
            "the setup looks good but risk:reward is only 1.2:1",
            "higher timeframes conflict with the entry timeframe",
            "volume doesn't confirm the breakout",
            "it's a weekend/low-liquidity period",
            "there's a major economic event in 4 hours",
            "the spread is abnormally wide (0.15%)",
            "open interest is diverging from price",
            "similar position already open (correlation risk)",
            "portfolio is at maximum heat (6%)",
        ]
        reason = random.choice(why_no_trade)

        prompts.append({
            "category": "no_trade",
            "instruction": f"Should RUNECLAW take this {pair} setup?",
            "claude_prompt": (
                f"As RUNECLAW, evaluate a {pair} trade setup where {reason}.\n\n"
                f"Generate a detailed REJECTION analysis that explains:\n"
                f"1. What the setup looks like (make it tempting)\n"
                f"2. Why RUNECLAW rejects it despite the temptation\n"
                f"3. Which specific risk checks failed\n"
                f"4. What conditions would need to change for approval\n"
                f"5. The discipline message: why saying NO is the real edge\n\n"
                f"This teaches the model that NOT trading is often the best trade."
            ),
        })

    # Category 6: Post-Trade Analysis / Reflection
    for _ in range(samples_per_category):
        pair = random.choice(PAIRS)
        outcomes = [
            ("won", "hit TP1", random.uniform(2, 8)),
            ("won", "hit TP2", random.uniform(5, 15)),
            ("lost", "hit stop loss", random.uniform(-1, -4)),
            ("lost", "closed early on signal change", random.uniform(-0.5, -2)),
            ("breakeven", "trailed to breakeven and stopped out", 0),
            ("partial win", "hit TP1 but stopped on remainder", random.uniform(1, 3)),
        ]
        outcome, detail, pnl = random.choice(outcomes)

        prompts.append({
            "category": "post_trade",
            "instruction": f"Post-trade analysis for {pair} ({outcome}).",
            "claude_prompt": (
                f"As RUNECLAW, write a post-trade reflection for a {pair} trade.\n\n"
                f"Outcome: {outcome} — {detail} ({pnl:+.1f}%)\n\n"
                f"Include:\n"
                f"1. Original thesis and why the trade was taken\n"
                f"2. What actually happened vs what was expected\n"
                f"3. What was done well\n"
                f"4. What could be improved\n"
                f"5. Lessons learned and adjustments for future trades\n"
                f"6. Updated confidence in the strategy\n\n"
                f"Be honest about mistakes. Growth comes from objective self-assessment."
            ),
        })

    # Category 7: Educational / Concept Explanation
    concepts = [
        "How does the GetClaw Confluence Engine weight its 12 indicators?",
        "Explain the 23 automated risk checks and when each triggers.",
        "What is the difference between confluence and confirmation?",
        "How does RUNECLAW calculate position size using Kelly criterion?",
        "Explain how RUNECLAW handles correlated positions.",
        "What is a circuit breaker and when does RUNECLAW activate it?",
        "How does RUNECLAW adapt to different market regimes?",
        "Explain the drawdown management protocol (2% daily, 5% weekly).",
        "What role does volume play in RUNECLAW's analysis?",
        "How does RUNECLAW avoid revenge trading after losses?",
        "Explain multi-timeframe alignment scoring.",
        "What is portfolio heat and why does RUNECLAW cap it at 6%?",
        "How does RUNECLAW handle news events and black swan scenarios?",
        "Explain the difference between a good loss and a bad loss.",
        "What makes a trade setup 'A+' quality vs 'B' quality?",
    ]

    for concept in concepts:
        for _ in range(samples_per_category // len(concepts) + 1):
            prompts.append({
                "category": "educational",
                "instruction": concept,
                "claude_prompt": (
                    f"As RUNECLAW, answer this question from a user:\n\n"
                    f"\"{concept}\"\n\n"
                    f"Provide a clear, detailed explanation with:\n"
                    f"1. The core concept\n"
                    f"2. Practical examples with specific numbers\n"
                    f"3. Why this matters for trading performance\n"
                    f"4. Common mistakes to avoid\n\n"
                    f"Be educational but practical. Use specific examples."
                ),
            })

    random.shuffle(prompts)
    return prompts


# ── Generation Pipeline ───────────────────────────────────────

def generate_sample(client, prompt):
    """Generate a single training sample using Claude."""
    response = client.generate(TEACHER_SYSTEM, prompt["claude_prompt"])
    if response is None:
        return None

    return {
        "instruction": prompt["instruction"],
        "input": "",
        "output": response,
        "category": prompt["category"],
        "source": "claude",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", default=os.environ.get("ANTHROPIC_API_KEY"))
    parser.add_argument("--model", default=None, help="Claude model to use")
    parser.add_argument("--samples", type=int, default=samples_per_category,
                        help="Samples per category (default 500)")
    parser.add_argument("--concurrent", type=int, default=MAX_CONCURRENT)
    parser.add_argument("--resume", action="store_true",
                        help="Resume from existing claude_training.jsonl")
    args = parser.parse_args()

    if not args.api_key:
        print("ERROR: No API key found!")
        print("  Set ANTHROPIC_API_KEY environment variable, or pass --api-key")
        print("  Example: set ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    samples_per_cat = args.samples

    print("=" * 60)
    print("RUNECLAW - Claude Knowledge Distillation")
    print("=" * 60)

    # Initialize client
    print("\nInitializing Claude API...")
    client = ClaudeClient(args.api_key, model=args.model)

    # Generate prompts
    print(f"\nGenerating prompt set ({samples_per_cat} per category)...")
    prompts = make_prompts(samples_per_cat)
    print(f"  Total prompts: {len(prompts)}")

    # Resume support
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    existing_count = 0
    if args.resume and os.path.exists(CLAUDE_FILE):
        with open(CLAUDE_FILE) as f:
            existing_count = sum(1 for _ in f)
        prompts = prompts[existing_count:]
        print(f"  Resuming from {existing_count} existing samples")
        print(f"  Remaining: {len(prompts)} prompts")

    # Estimate cost
    est_tokens = len(prompts) * 1500  # ~1500 tokens per sample
    if "haiku" in client.model:
        est_cost = est_tokens / 1_000_000 * 1.25  # ~$1.25/M tokens
    else:
        est_cost = est_tokens / 1_000_000 * 8.0   # ~$8/M tokens (blended)
    print(f"\n  Estimated output tokens: {est_tokens:,}")
    print(f"  Estimated cost: ~${est_cost:.2f}")

    # Generate
    print(f"\n{'='*60}")
    print(f"Generating {len(prompts)} samples ({args.concurrent} concurrent)...")
    print(f"{'='*60}\n")

    generated = 0
    failed = 0
    mode = "a" if args.resume else "w"

    with open(CLAUDE_FILE, mode, encoding="utf-8") as f:
        # Process in batches to show progress
        batch_size = args.concurrent * 2
        for batch_start in range(0, len(prompts), batch_size):
            batch = prompts[batch_start:batch_start + batch_size]

            with ThreadPoolExecutor(max_workers=args.concurrent) as executor:
                futures = {
                    executor.submit(generate_sample, client, p): p
                    for p in batch
                }

                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        f.write(json.dumps(result, ensure_ascii=False) + "\n")
                        f.flush()
                        generated += 1
                    else:
                        failed += 1

            total_done = existing_count + generated + failed
            total_all = existing_count + len(prompts)
            pct = total_done / total_all * 100
            print(f"  [{pct:5.1f}%] {generated} generated, {failed} failed "
                  f"({total_done}/{total_all})")

    print(f"\n{'='*60}")
    print(f"Claude generation complete!")
    print(f"  Generated: {generated}")
    print(f"  Failed: {failed}")
    print(f"  File: {CLAUDE_FILE}")
    print(f"{'='*60}")

    # Merge with existing training data
    print(f"\nMerging with existing data...")
    all_samples = []

    # Load existing v2/v3 data
    for path in [
        os.path.join(OUTPUT_DIR, "combined_training_v3.jsonl"),
        os.path.join(OUTPUT_DIR, "combined_training.jsonl"),
    ]:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        all_samples.append(json.loads(line))
            print(f"  Loaded {len(all_samples)} existing samples from {os.path.basename(path)}")
            break

    # Load Claude data
    claude_count = 0
    with open(CLAUDE_FILE, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                sample = json.loads(line)
                # Remove metadata fields not needed for training
                clean = {
                    "instruction": sample["instruction"],
                    "input": sample.get("input", ""),
                    "output": sample["output"],
                }
                all_samples.append(clean)
                claude_count += 1

    print(f"  Added {claude_count} Claude-generated samples")

    random.shuffle(all_samples)

    with open(COMBINED_FILE, "w", encoding="utf-8") as f:
        for s in all_samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    print(f"\n  Combined total: {len(all_samples)} samples")
    print(f"  Output: {COMBINED_FILE}")

    print(f"""
Next steps:

  1. Update train_max_8b.py to use the Claude-enriched data:
     Change data_paths to include "combined_training_claude.jsonl"

  2. Train:
     python train_max_8b.py

  3. Export & convert:
     python export_model.py
     python convert_official.py

  4. Test:
     ollama run runeclaw "Scan BTC/USDT for trade setups"
""")


if __name__ == "__main__":
    main()
