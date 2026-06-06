#!/usr/bin/env python3
"""
RUNECLAW Enhanced Training Dataset Generator v2
================================================
Extends v1 with 4 additional data sources:
  - decision_memory.jsonl  (246 audit records)
  - reflection_memory.jsonl (71 post-trade reflections)
  - audit_chain.jsonl       (337 compliance events)
  - system.jsonl            (304K events → backtest, order flow, scans)

Plus the original:
  - trade.jsonl   (147K trade ideas)
  - risk.jsonl    (155K risk verdicts)

Output: JSONL files with {instruction, input, output} alpaca format
"""

import json
import sys
import random
from pathlib import Path
from collections import defaultdict

# ── Paths ──────────────────────────────────────────────────────
TRADE_LOG = Path("/workspace/output/runeclaw/logs/trade.jsonl")
RISK_LOG = Path("/workspace/output/runeclaw/logs/risk.jsonl")
DECISION_MEM = Path("/workspace/output/runeclaw/data/learning/decision_memory.jsonl")
REFLECTION_MEM = Path("/workspace/output/runeclaw/data/learning/reflection_memory.jsonl")
AUDIT_CHAIN = Path("/workspace/output/runeclaw/logs/audit_chain.jsonl")
SYSTEM_LOG = Path("/workspace/output/runeclaw/logs/system.jsonl")
OUTPUT_DIR = Path("/workspace/output/runeclaw/ollama/training_data")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MAX_SAMPLES_PER_TYPE = 5000
random.seed(42)


# ── Helper ─────────────────────────────────────────────────────
def safe_load_jsonl(path, max_lines=None):
    """Load JSONL file, skip bad lines."""
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if max_lines and i >= max_lines:
                break
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


# ── Dataset 1: Trade Analysis (from trade.jsonl) ────────────────
# [UNCHANGED from v1 — kept for completeness]

def build_trade_analysis_dataset():
    """Convert trade ideas and skips into instruction-tuning pairs."""
    print("Building trade analysis dataset...")
    if not TRADE_LOG.exists():
        print("  SKIP: trade.jsonl not found")
        return 0

    ideas, skips = [], []
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

    print(f"  Found {len(ideas)} ideas, {len(skips)} skips")
    samples = []

    for record in random.sample(ideas, min(MAX_SAMPLES_PER_TYPE // 2, len(ideas))):
        d = record["data"]
        symbol = d.get("asset", "UNKNOWN")
        direction = d.get("direction", "?")
        entry = d.get("entry_price", 0)
        sl = d.get("stop_loss", 0)
        tp = d.get("take_profit", 0)
        conf = d.get("confidence", 0)
        reasoning = d.get("reasoning", "")
        signals = d.get("signals_used", [])
        if direction == "LONG" and entry > 0 and sl > 0:
            risk, reward = entry - sl, tp - entry
        elif direction == "SHORT" and entry > 0 and sl > 0:
            risk, reward = sl - entry, entry - tp
        else:
            risk = reward = 0
        rr = round(reward / risk, 2) if risk > 0 else 0
        sl_pct = round(abs(sl - entry) / entry * 100, 2) if entry > 0 else 0
        tp_pct = round(abs(tp - entry) / entry * 100, 2) if entry > 0 else 0

        samples.append({
            "instruction": "Analyze the following market data and generate a structured trade idea using the RUNECLAW confluence engine.",
            "input": f"Symbol: {symbol}\nPrice: ${entry:,.2f}\nTechnical Data: {reasoning}\nSignals: {', '.join(signals)}",
            "output": (
                f"TRADE IDEA\nDirection: {direction}\nAsset: {symbol}\nEntry: ${entry:,.2f}\n"
                f"Stop Loss: ${sl:,.2f} (-{sl_pct}%)\nTake Profit: ${tp:,.2f} (+{tp_pct}%)\n"
                f"Risk:Reward: 1:{rr}\nConfidence: {int(conf*100)}%\n\n"
                f"Reasoning:\n- {reasoning}\nSignals Used: {', '.join(signals)}"
            ),
        })

    for record in random.sample(skips, min(MAX_SAMPLES_PER_TYPE // 2, len(skips))):
        d = record["data"]
        symbol = d.get("symbol", "UNKNOWN")
        conf = d.get("confidence", 0)
        samples.append({
            "instruction": "Analyze the following market data and determine if a trade should be taken.",
            "input": f"Symbol: {symbol}\nConfluence Score: {conf:.2f}\nSignal: Low confidence detected",
            "output": (
                f"NO TRADE\nVerdict: SKIP\n"
                f"Reason: Confidence {int(conf*100)}% is below the minimum threshold of 55%.\n"
                f"The confluence engine did not find sufficient indicator agreement.\n\n"
                f"Action: Continue monitoring. Wait for stronger confluence."
            ),
        })

    random.shuffle(samples)
    outfile = OUTPUT_DIR / "trade_analysis.jsonl"
    with open(outfile, "w") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"  Wrote {len(samples)} samples → {outfile.name}")
    return len(samples)


# ── Dataset 2: Risk Evaluation (from risk.jsonl) ────────────────
# [UNCHANGED from v1]

def build_risk_evaluation_dataset():
    """Convert risk check results into instruction-tuning pairs."""
    print("Building risk evaluation dataset...")
    if not RISK_LOG.exists():
        print("  SKIP: risk.jsonl not found")
        return 0

    approved, rejected = [], []
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

    for record in random.sample(approved, min(MAX_SAMPLES_PER_TYPE // 2, len(approved))):
        d = record["data"]
        trade_id = d.get("trade_id", "TI-unknown")
        symbol = record.get("message", "").replace("Risk APPROVED for ", "").replace("Risk REJECTED for ", "")
        pos_size = d.get("position_size_usd", 0)
        pos_pct = d.get("position_pct", 0)
        checks = d.get("checks_passed", [])
        checks_str = "\n".join(f"  PASS: {c}" for c in checks)
        samples.append({
            "instruction": "Evaluate the following trade against RUNECLAW's 23-point risk framework.",
            "input": f"Trade ID: {trade_id}\nSymbol: {symbol}\nPosition Size: ${pos_size:,.2f} ({pos_pct:.1f}%)\nDaily Loss: {d.get('daily_loss_pct',0):.1f}%\nDrawdown: {d.get('drawdown_pct',0):.1f}%",
            "output": f"RISK CHECK: APPROVED\nTrade {trade_id} passes all risk checks.\n\nChecks Passed:\n{checks_str}\n\nVerdict: Proceed with human confirmation.",
        })

    for record in random.sample(rejected, min(MAX_SAMPLES_PER_TYPE // 2, len(rejected))):
        d = record["data"]
        trade_id = d.get("trade_id", "TI-unknown")
        symbol = record.get("message", "").replace("Risk APPROVED for ", "").replace("Risk REJECTED for ", "")
        pos_size = d.get("position_size_usd", 0)
        pos_pct = d.get("position_pct", 0)
        passed = d.get("checks_passed", [])
        failed = d.get("checks_failed", [])
        reason = d.get("reason", "Unknown")
        passed_str = "\n".join(f"  PASS: {c}" for c in passed) if passed else "  (none)"
        failed_str = "\n".join(f"  FAIL: {c}" for c in failed) if failed else "  (none)"
        samples.append({
            "instruction": "Evaluate the following trade against RUNECLAW's 23-point risk framework.",
            "input": f"Trade ID: {trade_id}\nSymbol: {symbol}\nPosition Size: ${pos_size:,.2f} ({pos_pct:.1f}%)\nDaily Loss: {d.get('daily_loss_pct',0):.1f}%\nDrawdown: {d.get('drawdown_pct',0):.1f}%",
            "output": (
                f"RISK CHECK: REJECTED\nTrade {trade_id} FAILS risk evaluation.\n\n"
                f"Checks Passed:\n{passed_str}\n\nChecks Failed:\n{failed_str}\n\n"
                f"Primary Reason: {reason}\n\n"
                f"Action: Do NOT proceed. Capital preservation takes priority."
            ),
        })

    random.shuffle(samples)
    outfile = OUTPUT_DIR / "risk_evaluation.jsonl"
    with open(outfile, "w") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"  Wrote {len(samples)} samples → {outfile.name}")
    return len(samples)


# ══════════════════════════════════════════════════════════════════
# NEW IN V2: Four additional data sources
# ══════════════════════════════════════════════════════════════════


# ── Dataset 3: Decision Audit Review (decision_memory.jsonl) ─────

def build_decision_audit_dataset():
    """Convert decision memory audit records into training samples."""
    print("Building decision audit dataset...")
    if not DECISION_MEM.exists():
        print("  SKIP: decision_memory.jsonl not found")
        return 0

    records = safe_load_jsonl(DECISION_MEM)
    print(f"  Found {len(records)} decision audit records")
    samples = []

    for rec in records:
        symbol = rec.get("symbol", "UNKNOWN")
        direction = rec.get("direction", "?")
        mode = rec.get("mode", "paper")
        decision = rec.get("decision", "")
        conf = rec.get("confidence", 0)
        confluence = rec.get("confluence_score", 0)
        entry = rec.get("entry_price", 0)
        sl = rec.get("stop_loss", 0)
        tp = rec.get("take_profit", 0)
        rr = rec.get("risk_reward", 0)
        pos_size = rec.get("position_size_usd", 0)
        risk_result = rec.get("risk_engine_result", "")
        checks_passed = rec.get("checks_passed", [])
        checks_failed = rec.get("checks_failed", [])
        rejected_reason = rec.get("rejected_reason", "")
        ts = rec.get("timestamp_utc", "")

        input_text = (
            f"Audit ID: {rec.get('audit_id','')}\n"
            f"Symbol: {symbol}\n"
            f"Direction: {direction}\n"
            f"Mode: {mode}\n"
            f"Entry: ${entry:,.2f}\n"
            f"Stop Loss: ${sl:,.2f}\n"
            f"Take Profit: ${tp:,.2f}\n"
            f"Risk:Reward: {rr}\n"
            f"Position Size: ${pos_size:,.2f}\n"
            f"Confidence: {int(conf*100)}%\n"
            f"Confluence Score: {confluence:.2f}"
        )

        if risk_result == "APPROVED":
            checks_str = "\n".join(f"  - {c}" for c in checks_passed[:10])
            output_text = (
                f"AUDIT REVIEW: TRADE ACCEPTED ({mode.upper()})\n\n"
                f"Decision: {decision}\n"
                f"Risk Engine: {risk_result}\n\n"
                f"The trade passed all {len(checks_passed)} risk checks:\n{checks_str}\n\n"
                f"Assessment: This is a properly structured trade with adequate risk controls. "
                f"Confidence {int(conf*100)}% meets the 55% minimum threshold. "
                f"Risk:Reward of {rr} {'meets' if rr >= 1.2 else 'is below'} the 1.2 minimum. "
                f"The position size of ${pos_size:,.2f} is within acceptable limits.\n\n"
                f"All safety checks passed. The trade was correctly approved for {mode} execution."
            )
        else:
            failed_str = "\n".join(f"  - {c}" for c in checks_failed) if checks_failed else "  (see reason)"
            output_text = (
                f"AUDIT REVIEW: TRADE REJECTED\n\n"
                f"Decision: {decision}\n"
                f"Risk Engine: {risk_result}\n"
                f"Rejection Reason: {rejected_reason}\n\n"
                f"Failed Checks:\n{failed_str}\n\n"
                f"Assessment: The risk engine correctly blocked this trade. "
                f"Capital preservation takes priority over any individual trade opportunity. "
                f"The rejection reason should be addressed before re-attempting."
            )

        samples.append({
            "instruction": "Review the following trade audit record from RUNECLAW's decision memory. Analyze whether the risk engine decision was correct and provide a detailed assessment.",
            "input": input_text,
            "output": output_text,
        })

    # Deduplicate (many records are identical test trades)
    seen = set()
    unique_samples = []
    for s in samples:
        key = (s["output"][:100],)
        if key not in seen:
            seen.add(key)
            unique_samples.append(s)

    outfile = OUTPUT_DIR / "decision_audit.jsonl"
    with open(outfile, "w") as f:
        for s in unique_samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"  Wrote {len(unique_samples)} unique samples → {outfile.name}")
    return len(unique_samples)


# ── Dataset 4: Trade Reflection (reflection_memory.jsonl) ────────

def build_reflection_dataset():
    """Convert post-trade reflections into training samples."""
    print("Building trade reflection dataset...")
    if not REFLECTION_MEM.exists():
        print("  SKIP: reflection_memory.jsonl not found")
        return 0

    records = safe_load_jsonl(REFLECTION_MEM)
    print(f"  Found {len(records)} reflection records")
    samples = []

    for rec in records:
        symbol = rec.get("symbol", "UNKNOWN")
        mode = rec.get("mode", "paper")
        expected = rec.get("what_was_expected", "")
        happened = rec.get("what_happened", "")
        lesson = rec.get("lesson_learned", "")
        improvement = rec.get("recommended_improvement", "")
        conf = rec.get("confidence", 0)
        classification = rec.get("change_classification", "")

        input_text = (
            f"Symbol: {symbol}\n"
            f"Mode: {mode}\n"
            f"Expected: {expected}\n"
            f"What Happened: {happened}\n"
            f"Confidence at Signal: {int(conf*100)}%"
        )

        output_text = (
            f"TRADE REFLECTION\n\n"
            f"Symbol: {symbol}\n"
            f"Expectation: {expected}\n"
            f"Reality: {happened}\n\n"
            f"Lesson Learned:\n{lesson}\n\n"
            f"Recommended Improvement:\n{improvement}\n\n"
            f"Classification: {classification}\n\n"
        )

        # Add contextual analysis based on the rejection pattern
        if "SYMBOL_EXPOSURE" in happened:
            output_text += (
                "Analysis: The risk engine correctly blocked this trade due to excessive "
                "concentration in a single symbol. Even when the signal quality is acceptable, "
                "portfolio-level risk limits must be respected. Diversification across symbols "
                "reduces the impact of any single position going wrong.\n\n"
                "Takeaway: Before generating a new trade idea, check existing exposure to the "
                "same symbol. If exposure is already at the limit, look for opportunities in "
                "other symbols instead."
            )
        elif "MAX_POSITIONS" in happened:
            output_text += (
                "Analysis: The maximum open position limit was reached. This is a portfolio-level "
                "safety mechanism that prevents overtrading and excessive exposure. Even strong "
                "signals must wait until an existing position is closed.\n\n"
                "Takeaway: Quality over quantity. Having fewer, higher-conviction trades with "
                "proper sizing is better than many overlapping positions."
            )
        elif "DAILY_LOSS" in happened:
            output_text += (
                "Analysis: Daily loss limit was breached, triggering the circuit breaker. "
                "No new trades should be taken until the daily risk budget resets. This prevents "
                "emotional revenge-trading after losses.\n\n"
                "Takeaway: When the circuit breaker trips, step away. Review what went wrong. "
                "The market will still be there tomorrow."
            )
        else:
            output_text += (
                "Analysis: Post-trade review is essential for continuous improvement. "
                "Each rejected or failed trade provides data that can improve future decisions. "
                "The key is to identify patterns in failures and adjust the strategy accordingly."
            )

        samples.append({
            "instruction": "Review the following post-trade reflection from RUNECLAW's learning engine. Analyze the trade outcome, extract lessons learned, and provide actionable improvement suggestions.",
            "input": input_text,
            "output": output_text,
        })

    # Deduplicate
    seen = set()
    unique = []
    for s in samples:
        key = s["input"][:80]
        if key not in seen:
            seen.add(key)
            unique.append(s)

    outfile = OUTPUT_DIR / "trade_reflections.jsonl"
    with open(outfile, "w") as f:
        for s in unique:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"  Wrote {len(unique)} unique samples → {outfile.name}")
    return len(unique)


# ── Dataset 5: Compliance & Authorization (audit_chain.jsonl) ────

def build_compliance_dataset():
    """Convert audit chain events into compliance training samples."""
    print("Building compliance dataset...")
    if not AUDIT_CHAIN.exists():
        print("  SKIP: audit_chain.jsonl not found")
        return 0

    records = safe_load_jsonl(AUDIT_CHAIN)
    print(f"  Found {len(records)} audit chain events")
    samples = []
    seen_types = set()

    for rec in records:
        event_type = rec.get("event_type", "")
        payload = rec.get("payload", {})
        actor = rec.get("actor", "system")
        seq = rec.get("sequence", 0)

        if event_type == "DECISION":
            symbol = payload.get("symbol", "UNKNOWN")
            idea = payload.get("idea", {})
            risk = payload.get("risk", {})
            outcome = payload.get("outcome", "")
            is_paper = payload.get("is_paper", True)
            macro = payload.get("macro", {}) or {}
            compliance = payload.get("compliance", {}) or {}

            direction = idea.get("direction", "?")
            conf = idea.get("confidence", 0)
            verdict = risk.get("verdict", "?")

            input_text = (
                f"Event: Trade Decision\n"
                f"Symbol: {symbol}\n"
                f"Direction: {direction}\n"
                f"Confidence: {int(conf*100)}%\n"
                f"Risk Verdict: {verdict}\n"
                f"Mode: {'Paper' if is_paper else 'LIVE'}\n"
                f"Actor: {actor}"
            )

            if verdict == "APPROVED":
                entry = idea.get("entry", 0)
                sl = idea.get("sl", 0)
                tp = idea.get("tp", 0)
                size = risk.get("size_usd", 0)
                passed = risk.get("passed", 0)
                macro_state = macro.get("risk_state", "UNKNOWN")
                output_text = (
                    f"COMPLIANCE REVIEW: APPROVED\n\n"
                    f"Decision Chain Sequence: {seq}\n"
                    f"Outcome: {outcome}\n\n"
                    f"Trade Details:\n"
                    f"  Entry: ${entry:,.2f}\n"
                    f"  Stop Loss: ${sl:,.2f}\n"
                    f"  Take Profit: ${tp:,.2f}\n"
                    f"  Position Size: ${size:,.2f}\n\n"
                    f"Risk Assessment:\n"
                    f"  Checks Passed: {passed}\n"
                    f"  Macro State: {macro_state}\n"
                    f"  Compliance: {'Granted' if compliance.get('granted') else 'Denied'}\n\n"
                    f"Audit: This trade followed proper procedure. The risk engine approved "
                    f"the trade after passing all {passed} checks. Macro conditions were "
                    f"{macro_state}. The trade was executed in {'paper' if is_paper else 'LIVE'} mode."
                )
            else:
                reason = risk.get("reason", "Unknown")
                output_text = (
                    f"COMPLIANCE REVIEW: REJECTED\n\n"
                    f"Decision Chain Sequence: {seq}\n"
                    f"Outcome: {outcome}\n\n"
                    f"Rejection Reason: {reason}\n\n"
                    f"Audit: The risk engine correctly blocked this trade. "
                    f"Reason: {reason}. This is the system working as designed — "
                    f"no trade should proceed when risk limits are breached. "
                    f"Capital preservation is always the priority."
                )

            # Only add unique patterns
            key = f"{verdict}_{outcome}"
            if key not in seen_types or len(samples) < 20:
                seen_types.add(key)
                samples.append({
                    "instruction": "Review the following audit chain event for compliance. Verify that the decision followed proper risk management procedures and regulatory requirements.",
                    "input": input_text,
                    "output": output_text,
                })

        elif event_type == "AUTH_DENIED":
            reasons = payload.get("reasons", [])
            locks_failed = payload.get("locks_failed", [])
            trade_id = payload.get("trade_id", "")
            asset = payload.get("asset", "")

            input_text = (
                f"Event: Authorization Denied\n"
                f"Trade ID: {trade_id}\n"
                f"Asset: {asset}\n"
                f"Actor: {actor}\n"
                f"Reasons: {'; '.join(reasons)}\n"
                f"Failed Locks: {', '.join(locks_failed)}"
            )

            output_text = (
                f"COMPLIANCE REVIEW: AUTHORIZATION DENIED\n\n"
                f"Trade {trade_id} on {asset} was correctly blocked.\n\n"
                f"Denial Reasons:\n"
                + "\n".join(f"  - {r}" for r in reasons) + "\n\n"
                f"Failed Security Locks:\n"
                + "\n".join(f"  - {l}" for l in locks_failed) + "\n\n"
                f"Assessment: The authorization system is working correctly. "
                f"RUNECLAW requires explicit human approval for live trades and proper "
                f"permission tokens for execution. This is a critical safety feature — "
                f"no AI system should autonomously execute live trades without human consent.\n\n"
                f"The correct response is to:\n"
                f"1. Verify the user has the required permissions\n"
                f"2. Obtain explicit human approval via the /approve command\n"
                f"3. Retry the trade with proper authorization"
            )

            key = f"AUTH_{';'.join(locks_failed)}"
            if key not in seen_types:
                seen_types.add(key)
                samples.append({
                    "instruction": "Review the following authorization denial event. Explain why the trade was blocked and what steps are needed to proceed safely.",
                    "input": input_text,
                    "output": output_text,
                })

    outfile = OUTPUT_DIR / "compliance.jsonl"
    with open(outfile, "w") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"  Wrote {len(samples)} samples → {outfile.name}")
    return len(samples)


# ── Dataset 6: Backtest Analysis (system.jsonl) ──────────────────

def build_backtest_dataset():
    """Convert backtest results into performance analysis training samples."""
    print("Building backtest analysis dataset...")
    if not SYSTEM_LOG.exists():
        print("  SKIP: system.jsonl not found")
        return 0

    backtests = []
    print("  Scanning system.jsonl for backtest_complete events...")
    with open(SYSTEM_LOG, "r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line.strip())
            except json.JSONDecodeError:
                continue
            if rec.get("action") == "backtest_complete" and rec.get("data"):
                backtests.append(rec)

    print(f"  Found {len(backtests)} backtest results")
    samples = []

    # Deduplicate by unique parameter combinations
    seen = set()
    for rec in backtests:
        d = rec["data"]
        symbol = d.get("symbol", "UNKNOWN")
        tf = d.get("timeframe", "1h")
        start = d.get("start_date", "")
        end = d.get("end_date", "")
        trades = d.get("total_trades", 0)
        win_rate = d.get("win_rate", 0)
        ret = d.get("total_return_pct", 0)
        pf = d.get("profit_factor", 0)
        max_dd = d.get("max_drawdown_pct", 0)
        sharpe = d.get("sharpe_ratio", 0)
        initial = d.get("initial_balance", 10000)
        final_eq = d.get("final_equity", 0)
        avg_win = d.get("avg_win_usd", 0)
        avg_loss = d.get("avg_loss_usd", 0)
        max_consec = d.get("max_consecutive_losses", 0)
        signals_gen = d.get("total_signals_generated", 0)
        ideas_gen = d.get("total_ideas_generated", 0)
        rejected_risk = d.get("total_ideas_rejected_risk", 0)
        rejected_conf = d.get("total_ideas_rejected_confidence", 0)

        key = f"{symbol}_{tf}_{start}_{end}_{initial}_{d.get('commission_pct',0)}_{d.get('slippage_pct',0)}"
        if key in seen:
            continue
        seen.add(key)

        input_text = (
            f"Backtest Parameters:\n"
            f"  Symbol: {symbol}\n"
            f"  Timeframe: {tf}\n"
            f"  Period: {start} to {end}\n"
            f"  Initial Balance: ${initial:,.2f}\n"
            f"  Commission: {d.get('commission_pct',0)}%\n"
            f"  Slippage: {d.get('slippage_pct',0)}%\n\n"
            f"Results:\n"
            f"  Total Trades: {trades}\n"
            f"  Win Rate: {win_rate*100:.1f}%\n"
            f"  Return: {ret:.2f}%\n"
            f"  Profit Factor: {pf:.2f}\n"
            f"  Sharpe Ratio: {sharpe:.2f}\n"
            f"  Max Drawdown: {max_dd:.2f}%\n"
            f"  Max Consecutive Losses: {max_consec}\n"
            f"  Final Equity: ${final_eq:,.2f}"
        )

        # Generate analysis based on results
        if ret > 0 and pf > 1.0:
            verdict = "PROFITABLE"
            assessment = (
                f"The strategy produced a positive return of {ret:.2f}% with a profit factor of {pf:.2f}. "
                f"This indicates the strategy's edge is present in this market period."
            )
        elif ret > -1.0:
            verdict = "MARGINAL"
            assessment = (
                f"The strategy was roughly breakeven ({ret:.2f}% return). "
                f"The edge may be too small to overcome transaction costs consistently."
            )
        else:
            verdict = "UNPROFITABLE"
            assessment = (
                f"The strategy lost {abs(ret):.2f}% during this period. "
                f"A profit factor of {pf:.2f} indicates losses outweigh wins."
            )

        if win_rate < 0.35:
            win_analysis = f"Win rate of {win_rate*100:.1f}% is below the 35% threshold. This is concerning even with good R:R ratios."
        elif win_rate < 0.50:
            win_analysis = f"Win rate of {win_rate*100:.1f}% is moderate. Acceptable if risk:reward compensates."
        else:
            win_analysis = f"Win rate of {win_rate*100:.1f}% is healthy."

        dd_analysis = ""
        if max_dd > 5:
            dd_analysis = f"\n\nRISK WARNING: Max drawdown of {max_dd:.2f}% exceeded the 5% daily loss limit. The circuit breaker should have been triggered."
        elif max_dd > 3:
            dd_analysis = f"\n\nCaution: Max drawdown of {max_dd:.2f}% is approaching the 5% daily loss limit."

        output_text = (
            f"BACKTEST ANALYSIS: {verdict}\n\n"
            f"Performance Summary:\n"
            f"  Return: {ret:.2f}% (${final_eq - initial:,.2f})\n"
            f"  {trades} trades over {start} to {end}\n"
            f"  {win_analysis}\n"
            f"  Average Win: ${avg_win:.2f} | Average Loss: ${avg_loss:.2f}\n"
            f"  Max Consecutive Losses: {max_consec}\n\n"
            f"Assessment: {assessment}\n\n"
            f"Signal Quality:\n"
            f"  Total signals generated: {signals_gen}\n"
            f"  Ideas generated: {ideas_gen} ({ideas_gen/max(signals_gen,1)*100:.0f}% signal-to-idea rate)\n"
            f"  Rejected by risk engine: {rejected_risk}\n"
            f"  Rejected by confidence filter: {rejected_conf}\n"
            f"  The confidence filter removed {rejected_conf} low-quality signals, which is the primary gatekeeper."
            f"{dd_analysis}\n\n"
            f"Recommendation: {'Continue with this strategy configuration.' if ret > 0 else 'Review confluence weights and entry conditions. Consider tightening the confidence threshold.'}"
        )

        samples.append({
            "instruction": "Analyze the following backtest results from RUNECLAW's strategy engine. Evaluate performance metrics, identify strengths and weaknesses, and provide actionable recommendations.",
            "input": input_text,
            "output": output_text,
        })

    outfile = OUTPUT_DIR / "backtest_analysis.jsonl"
    with open(outfile, "w") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"  Wrote {len(samples)} unique samples → {outfile.name}")
    return len(samples)


# ── Dataset 7: System Operations (system.jsonl — other events) ───

def build_system_ops_dataset():
    """Convert system operational events into training samples."""
    print("Building system operations dataset...")
    if not SYSTEM_LOG.exists():
        print("  SKIP: system.jsonl not found")
        return 0

    events_by_action = defaultdict(list)
    target_actions = {"order_flow", "scan", "proactive_alert", "halt", "rebalance_check"}

    print("  Scanning system.jsonl for operational events...")
    with open(SYSTEM_LOG, "r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line.strip())
            except json.JSONDecodeError:
                continue
            action = rec.get("action", "")
            if action in target_actions:
                events_by_action[action].append(rec)

    samples = []

    # Order flow events → microstructure analysis
    flows = events_by_action.get("order_flow", [])
    if flows:
        sampled = random.sample(flows, min(200, len(flows)))
        for rec in sampled:
            d = rec.get("data", {})
            msg = rec.get("message", "")
            samples.append({
                "instruction": "Analyze the following order flow data from the exchange. Interpret the market microstructure signals and explain their implications for trading decisions.",
                "input": f"Event: Order Flow Update\nTimestamp: {rec.get('ts','')}\nMessage: {msg}\nData: {json.dumps(d, default=str)[:500]}",
                "output": (
                    f"ORDER FLOW ANALYSIS\n\n"
                    f"Event: {msg}\n\n"
                    f"Interpretation: Order flow data provides real-time insight into buying/selling pressure. "
                    f"Key metrics to evaluate:\n"
                    f"- Taker buy vs sell volume ratio\n"
                    f"- Bid/ask depth imbalance\n"
                    f"- Large order clustering\n\n"
                    f"For RUNECLAW's confluence engine, order flow feeds into the TAKER_3BAR and "
                    f"BID_DOMINANCE checks. Three consecutive bars of directional taker flow with "
                    f"2:1 bid:ask depth ratio provides a microstructure confirmation signal."
                ),
            })

    # Scan events → market scanning
    scans = events_by_action.get("scan", [])
    if scans:
        sampled = random.sample(scans, min(100, len(scans)))
        for rec in sampled:
            msg = rec.get("message", "")
            d = rec.get("data", {})
            samples.append({
                "instruction": "Explain the results of a RUNECLAW market scan. What symbols are showing potential setups?",
                "input": f"Scan Event: {msg}\nData: {json.dumps(d, default=str)[:500]}",
                "output": (
                    f"MARKET SCAN RESULTS\n\n"
                    f"{msg}\n\n"
                    f"The scan evaluates all active USDT pairs for:\n"
                    f"1. Volume spikes (>2x 20-bar average)\n"
                    f"2. Momentum score (weighted indicator composite)\n"
                    f"3. Regime detection (trending vs ranging)\n"
                    f"4. Confluence pre-score\n\n"
                    f"Symbols that pass the scan threshold are queued for full confluence analysis. "
                    f"The scan is a lightweight filter — it does NOT generate trade ideas directly. "
                    f"Full analysis with all 12 indicators runs only on scan-selected symbols."
                ),
            })

    # Halt events → circuit breaker education
    halts = events_by_action.get("halt", [])
    if halts:
        sampled = random.sample(halts, min(50, len(halts)))
        for rec in sampled:
            msg = rec.get("message", "")
            samples.append({
                "instruction": "A halt event has been triggered in RUNECLAW. Explain what this means and what steps should be taken.",
                "input": f"HALT Event: {msg}\nTimestamp: {rec.get('ts','')}",
                "output": (
                    f"CIRCUIT BREAKER ACTIVATED\n\n"
                    f"Event: {msg}\n\n"
                    f"What this means:\n"
                    f"- All trading has been paused immediately\n"
                    f"- No new trade ideas will be generated\n"
                    f"- Existing open positions remain (but no new entries)\n\n"
                    f"Why this happened:\n"
                    f"The circuit breaker trips when safety thresholds are breached — typically "
                    f"daily loss exceeding 5% or total drawdown exceeding 10%.\n\n"
                    f"What to do:\n"
                    f"1. Do NOT attempt to override the halt\n"
                    f"2. Review all open positions for stop loss placement\n"
                    f"3. Wait for the daily reset at 00:00 UTC, or manually reset with /reset\n"
                    f"4. After reset, reduce position sizes temporarily\n\n"
                    f"Capital preservation is always the priority. The market will be there tomorrow."
                ),
            })

    # Proactive alerts
    alerts = events_by_action.get("proactive_alert", [])
    if alerts:
        for rec in alerts:
            msg = rec.get("message", "")
            d = rec.get("data", {})
            samples.append({
                "instruction": "A proactive alert has been triggered by RUNECLAW. Interpret the alert and explain what action should be taken.",
                "input": f"Alert: {msg}\nData: {json.dumps(d, default=str)[:400]}",
                "output": (
                    f"PROACTIVE ALERT\n\n"
                    f"Alert: {msg}\n\n"
                    f"Proactive alerts are generated when RUNECLAW detects conditions that may "
                    f"require attention — such as approaching risk limits, unusual market conditions, "
                    f"or positions nearing stop/target levels.\n\n"
                    f"Recommended response:\n"
                    f"1. Review the alert details carefully\n"
                    f"2. Check open positions related to this alert\n"
                    f"3. Decide if any manual intervention is needed\n"
                    f"4. RUNECLAW will continue monitoring automatically\n\n"
                    f"Alerts are informational — they do not automatically change positions."
                ),
            })

    random.shuffle(samples)
    outfile = OUTPUT_DIR / "system_operations.jsonl"
    with open(outfile, "w") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"  Wrote {len(samples)} samples → {outfile.name}")
    return len(samples)


# ── Knowledge Q&A (unchanged from v1) ───────────────────────────

def build_knowledge_qa_dataset():
    """Generate Q&A pairs about RUNECLAW's trading methodology."""
    print("Building trading knowledge Q&A dataset...")
    # [Same as v1 — omitted for brevity, loaded from existing file]
    outfile = OUTPUT_DIR / "knowledge_qa.jsonl"
    if outfile.exists():
        count = sum(1 for _ in open(outfile))
        print(f"  Existing {count} Q&A pairs → {outfile.name} (kept)")
        return count
    print("  SKIP: no existing knowledge_qa.jsonl")
    return 0


def build_conversation_dataset():
    """Keep existing conversation dataset."""
    print("Building conversation dataset...")
    outfile = OUTPUT_DIR / "conversations.jsonl"
    if outfile.exists():
        count = sum(1 for _ in open(outfile))
        print(f"  Existing {count} conversations → {outfile.name} (kept)")
        return count
    print("  SKIP: no existing conversations.jsonl")
    return 0


# ── Combine All Datasets ────────────────────────────────────────

def combine_datasets():
    """Combine all JSONL files into combined_training.jsonl."""
    print("\nCombining all datasets...")
    all_samples = []
    for f in sorted(OUTPUT_DIR.glob("*.jsonl")):
        if f.name == "combined_training.jsonl":
            continue
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    all_samples.append(line)

    random.shuffle(all_samples)
    outfile = OUTPUT_DIR / "combined_training.jsonl"
    with open(outfile, "w") as f:
        for line in all_samples:
            f.write(line + "\n")
    size_mb = outfile.stat().st_size / 1024 / 1024
    print(f"  Combined: {len(all_samples)} total samples ({size_mb:.1f} MB)")
    return len(all_samples)


# ── Main ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("RUNECLAW Enhanced Training Dataset Generator v2")
    print("=" * 60)

    total = 0
    total += build_trade_analysis_dataset()
    total += build_risk_evaluation_dataset()
    total += build_decision_audit_dataset()     # NEW
    total += build_reflection_dataset()          # NEW
    total += build_compliance_dataset()          # NEW
    total += build_backtest_dataset()            # NEW
    total += build_system_ops_dataset()          # NEW
    total += build_knowledge_qa_dataset()
    total += build_conversation_dataset()

    combined = combine_datasets()

    print(f"\n{'=' * 60}")
    print(f"Total training samples: {combined}")
    print(f"Output directory: {OUTPUT_DIR}")
    print(f"\nFiles generated:")
    for f in sorted(OUTPUT_DIR.glob("*.jsonl")):
        lines = sum(1 for _ in open(f))
        size = f.stat().st_size
        print(f"  {f.name}: {lines:,} samples ({size / 1024:.1f} KB)")
    print(f"\nDataset breakdown:")
    print(f"  Original (v1): trade_analysis + risk_evaluation + knowledge_qa + conversations")
    print(f"  New (v2):      decision_audit + trade_reflections + compliance + backtest_analysis + system_operations")
    print(f"\nTo train: python train_local.py")
