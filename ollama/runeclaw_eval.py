"""
RUNECLAW Eval Script
Tests a fine-tuned model's outputs against the full RUNECLAW spec.
Scores structural correctness, math validity, and rule compliance.

Usage:
    python runeclaw_eval.py --model pbdes2022/HUMANOID-TRADERS
    python runeclaw_eval.py --model ./models/runeclaw-8b --output results.json
"""

import json
import math
import re
import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

# ── Thresholds (from config.py / risk_manifest.yaml) ─────────────────────────
MAX_RISK_PCT        = 2.0
MAX_DAILY_LOSS_PCT  = 5.0
MAX_DRAWDOWN_PCT    = 10.0
MIN_RISK_REWARD     = 1.2
MIN_CONFIDENCE      = 0.55
MAX_OPEN_POSITIONS  = 5
MAX_PORTFOLIO_EXP   = 80.0
MAX_SYMBOL_EXP      = 20.0
MAX_VOLATILITY_ATR  = 7.0
ACTIONABLE_CONF     = 0.60   # Confluence Engine threshold

# ── Required fields per output type ──────────────────────────────────────────
TRADE_IDEA_REQUIRED = [
    "asset", "direction", "entry_price", "stop_loss", "take_profit",
    "confidence", "reasoning", "signals_used", "risk_reward_ratio",
]
RISK_CHECK_REQUIRED = [
    "verdict", "position_size_usd", "position_pct",
    "checks_passed", "checks_failed", "reason",
]
VALID_DIRECTIONS = {"LONG", "SHORT"}
VALID_VERDICTS   = {"APPROVED", "REJECTED"}
KNOWN_SIGNALS    = {
    # Core 12 from analyzer.py confluence engine
    "RSI", "RSI-14", "MACD", "MACD Histogram", "Bollinger Bands", "Bollinger %B",
    "EMA Cross", "EMA Ribbon", "EMA 9/21", "Volume Profile", "Volume Spike",
    "OBV", "OBV Trend", "Stochastic RSI", "ADX", "Ichimoku Cloud",
    "VWAP", "Fibonacci Retracement", "Fibonacci Zone", "Fibonacci", "ATR",
    # Extended voters
    "POC Magnet", "Taker Imbalance", "Keltner Squeeze",
}
MEME_COINS       = {"PEPE", "DOGE", "SHIB", "FLOKI", "WIF", "BONK", "BRETT", "MEME"}
MEME_ATR_MAX     = 4.0   # Tighter ATR limit for meme coins (vs 7% default)

# ── Held-out test prompts ─────────────────────────────────────────────────────
TEST_PROMPTS = [
    # --- LONG setups ---
    {
        "id": "eval-001",
        "category": "long_strong_signal",
        "prompt": (
            "Analyze BTC/USDT on the 4H timeframe.\n"
            "RSI-14: 28 (oversold). MACD Histogram: turning positive. "
            "Bollinger %B: 0.12. Volume Spike: 2.3x average aligned with price bounce. "
            "EMA9 crossed above EMA21. OBV rising. VWAP: price at 1.008x VWAP. "
            "Current price: 67,450. ATR: 1,200 (1.78% of price). "
            "Generate a RUNECLAW TradeIdea."
        ),
        "expected_direction": "LONG",
        "expected_verdict": "APPROVED",
    },
    {
        "id": "eval-002",
        "category": "long_fibonacci",
        "prompt": (
            "Analyze ETH/USDT 1H.\n"
            "Price pulled back to 0.618-0.786 Fibonacci zone at 3,180. "
            "RSI-14: 38. MACD Histogram slightly positive. ADX: 34 with +DI > -DI. "
            "Volume normal. EMA9 < EMA21 (short-term bearish but Fib zone strong). "
            "ATR: 45 (1.41%). Current price: 3,182. "
            "Generate a RUNECLAW TradeIdea."
        ),
        "expected_direction": "LONG",
        "expected_verdict": None,  # could go either way on confidence
    },
    # --- SHORT setups ---
    {
        "id": "eval-003",
        "category": "short_strong_signal",
        "prompt": (
            "Analyze SOL/USDT 4H.\n"
            "RSI-14: 74 (overbought). MACD Histogram: negative. "
            "Bollinger %B: 0.87. Volume Spike: 1.8x average against price. "
            "EMA9 below EMA21. OBV falling. Taker buy ratio: 0.38 (seller dominant). "
            "Current price: 185.50. ATR: 6.20 (3.34%). "
            "Generate a RUNECLAW TradeIdea."
        ),
        "expected_direction": "SHORT",
        "expected_verdict": "APPROVED",
    },
    {
        "id": "eval-004",
        "category": "short_high_confidence",
        "prompt": (
            "Analyze DOGE/USDT 15M.\n"
            "Keltner Squeeze active: BB inside KC. MACD direction: down. "
            "RSI: 68. Bollinger %B: 0.82. Volume declining. "
            "Price above VWAP by 0.3% only. ATR: 0.0018 (1.5% of price 0.12). "
            "Generate a RUNECLAW TradeIdea."
        ),
        "expected_direction": "SHORT",
        "expected_verdict": None,
    },
    # --- No-trade / rejection cases ---
    {
        "id": "eval-005",
        "category": "no_trade_low_confidence",
        "prompt": (
            "Analyze LINK/USDT 1H.\n"
            "RSI-14: 51 (neutral). MACD Histogram: 0.002 (barely positive). "
            "Bollinger %B: 0.48. Volume: flat. ADX: 18 (weak trend). "
            "EMA9 and EMA21 nearly flat and crossed twice in last 6 bars. "
            "Current price: 14.82. ATR: 0.31 (2.09%). "
            "Generate a RUNECLAW TradeIdea."
        ),
        "expected_direction": None,
        "expected_verdict": "REJECTED",
    },
    {
        "id": "eval-006",
        "category": "no_trade_high_volatility",
        "prompt": (
            "Analyze PEPE/USDT 5M.\n"
            "RSI: 82. MACD: positive. Massive volume spike 5x. "
            "ATR: 0.0000012 which is 9.6% of current price 0.0000125 (MEME COIN - max ATR 4%). "
            "Bollinger %B: 0.95. "
            "Generate a RUNECLAW TradeIdea — apply meme coin ATR rules."
        ),
        "expected_direction": None,
        "expected_verdict": "REJECTED",  # ATR exceeds meme coin 4% limit
    },
    {
        "id": "eval-007",
        "category": "no_trade_poor_rr",
        "prompt": (
            "Analyze ADA/USDT 4H.\n"
            "RSI-14: 31. MACD: bullish crossover. Volume spike aligned. "
            "Entry zone: 0.485. Nearest resistance (take profit target): 0.492. "
            "Natural stop below recent swing low: 0.479. "
            "Calculate risk:reward and determine if this trade meets RUNECLAW minimums."
        ),
        "expected_direction": "LONG",
        "expected_verdict": "REJECTED",  # TP-entry = 0.007, entry-SL = 0.006 → R:R ≈ 1.17 < 1.2 min
    },
    # --- Regime-aware cases ---
    {
        "id": "eval-008",
        "category": "choppy_regime",
        "prompt": (
            "Market regime: CHOPPY. "
            "Analyze BNB/USDT 1H.\n"
            "RSI: 48, MACD flat, price oscillating between 580-595, "
            "ADX: 12, volume declining, multiple failed breakouts today. "
            "Apply RUNECLAW regime rules. Generate TradeIdea with position sizing."
        ),
        "expected_direction": None,
        "expected_verdict": "REJECTED",
        "regime_check": "CHOPPY",  # Position size should be 0.5x, cooldown 2x
    },
    {
        "id": "eval-009",
        "category": "strong_trend_up",
        "prompt": (
            "Market regime: STRONG_TREND_UP. "
            "Analyze XRP/USDT 4H.\n"
            "RSI: 61, MACD: histogram strong positive, EMA9 > EMA21 by 3.2%, "
            "OBV rising steeply, Taker buy ratio: 0.67, ADX: 42 +DI dominant. "
            "Bollinger %B: 0.72. Volume: 1.6x average. ATR: 0.021 (3.5%). "
            "Apply STRONG_TREND_UP regime multipliers. Generate TradeIdea."
        ),
        "expected_direction": "LONG",
        "expected_verdict": "APPROVED",
        "regime_check": "STRONG_TREND_UP",  # Position size should be 1.5x
    },
]


# ── Score result container ────────────────────────────────────────────────────
@dataclass
class CheckResult:
    name: str
    passed: bool
    score: float      # 0.0 – 1.0
    detail: str = ""

@dataclass
class EvalResult:
    prompt_id: str
    category: str
    raw_output: str
    parsed: Optional[dict]
    checks: list[CheckResult] = field(default_factory=list)
    total_score: float = 0.0
    grade: str = ""
    direction_correct: Optional[bool] = None
    verdict_correct: Optional[bool] = None
    parse_error: str = ""

    def compute_score(self):
        if not self.checks:
            self.total_score = 0.0
        else:
            self.total_score = sum(c.score for c in self.checks) / len(self.checks) * 100
        if self.total_score >= 90:
            self.grade = "A"
        elif self.total_score >= 75:
            self.grade = "B"
        elif self.total_score >= 60:
            self.grade = "C"
        elif self.total_score >= 40:
            self.grade = "D"
        else:
            self.grade = "F"


# ── Parser ────────────────────────────────────────────────────────────────────
def extract_json(text: str) -> Optional[dict]:
    """Try multiple strategies to extract a JSON object from model output."""
    # Strategy 1: find ```json block
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Strategy 2: find outermost { } 
    brace_start = text.find("{")
    brace_end   = text.rfind("}")
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        try:
            return json.loads(text[brace_start:brace_end + 1])
        except json.JSONDecodeError:
            pass

    # Strategy 3: parse key: value lines (fallback for non-JSON models)
    data = {}
    patterns = {
        # JSON-style keys AND training data format (Entry:, Stop Loss:, etc.)
        # Handles: $584.23, 584.23, $0.40, 67,450.00
        "asset":            r"(?:asset|Trade Idea|Pair|Asset)[:\s]+([A-Z]+/[A-Z]+)",
        "direction":        r"(?:direction|Direction)[:\s]+(LONG|SHORT)",
        "entry_price":      r"(?:entry[_\s]?price|Entry)[:\s]+\$?([\d][\d.,]*\d)",
        "stop_loss":        r"(?:stop[_\s]?loss|Stop Loss|Stop)[:\s]+\$?([\d][\d.,]*\d)",
        "take_profit":      r"(?:take[_\s]?profit|Take Profit|Take Profit 1|TP1?)[:\s]+\$?([\d][\d.,]*\d)",
        "confidence":       r"(?:confidence|Confluence(?:\s+Score)?|Confluence)[:\s]+\$?([\d.]+)",
        "risk_reward_ratio":r"(?:risk[_\s]?reward[_\s]?ratio|Risk:Reward|R:R|Risk Reward)[:\s]+(?:1:)?([\d.]+)",
        "verdict":          r"(?:verdict|Status|Decision|DECISION)[:\s]+(APPROVED|REJECTED|REQUIRES_REVIEW)",
        "position_pct":     r"(?:position[_\s]?pct|Position Size|Portfolio %|Position)[:\s]+\$?([\d.]+)\s*%?",
    }
    for key, pattern in patterns.items():
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            val = m.group(1)
            # Strip $ signs, thousand separators, and placeholder text
            val = val.replace(",", "").replace("$", "")
            # Skip placeholder values like XXX.XX
            if "X" in val.upper() or "x" in val:
                continue
            # Map REQUIRES_REVIEW → REJECTED for eval purposes
            if val == "REQUIRES_REVIEW":
                val = "REJECTED"
            try:
                data[key] = float(val)
            except ValueError:
                data[key] = val

    # Extract signals_used from text if not in JSON
    if "signals_used" not in data:
        found_signals = [s for s in KNOWN_SIGNALS if s.lower() in text.lower()]
        if found_signals:
            data["signals_used"] = found_signals

    # Extract reasoning from full text if not in JSON
    if "reasoning" not in data and len(text) > 100:
        data["reasoning"] = text

    return data if data else None


# ── Individual checks ─────────────────────────────────────────────────────────
def check_required_fields(data: dict) -> CheckResult:
    missing = [f for f in TRADE_IDEA_REQUIRED if f not in data]
    if not missing:
        return CheckResult("required_fields", True, 1.0, "All required fields present")
    score = 1.0 - (len(missing) / len(TRADE_IDEA_REQUIRED))
    return CheckResult("required_fields", False, score, f"Missing: {missing}")


def check_direction(data: dict) -> CheckResult:
    d = str(data.get("direction", "")).upper().strip()
    if d in VALID_DIRECTIONS:
        return CheckResult("direction_valid", True, 1.0, f"direction={d}")
    return CheckResult("direction_valid", False, 0.0, f"Invalid direction: {repr(d)}")


def check_entry_price(data: dict) -> CheckResult:
    try:
        ep = float(data.get("entry_price", 0))
        if ep > 0:
            return CheckResult("entry_price_positive", True, 1.0, f"entry={ep}")
        return CheckResult("entry_price_positive", False, 0.0, "entry_price <= 0")
    except (TypeError, ValueError):
        return CheckResult("entry_price_positive", False, 0.0, "entry_price not numeric")


def check_sl_tp_direction(data: dict) -> CheckResult:
    """LONG: SL < entry < TP. SHORT: SL > entry > TP."""
    try:
        ep  = float(data["entry_price"])
        sl  = float(data["stop_loss"])
        tp  = float(data["take_profit"])
        dir = str(data.get("direction", "")).upper()

        if dir == "LONG":
            if sl < ep < tp:
                return CheckResult("sl_tp_geometry", True, 1.0, f"LONG ✓ SL={sl} < entry={ep} < TP={tp}")
            return CheckResult("sl_tp_geometry", False, 0.0,
                f"LONG geometry violated: SL={sl}, entry={ep}, TP={tp}")
        elif dir == "SHORT":
            if sl > ep > tp:
                return CheckResult("sl_tp_geometry", True, 1.0, f"SHORT ✓ SL={sl} > entry={ep} > TP={tp}")
            return CheckResult("sl_tp_geometry", False, 0.0,
                f"SHORT geometry violated: SL={sl}, entry={ep}, TP={tp}")
        return CheckResult("sl_tp_geometry", False, 0.0, "direction unknown, can't validate geometry")
    except (KeyError, TypeError, ValueError) as e:
        return CheckResult("sl_tp_geometry", False, 0.0, f"Parse error: {e}")


def check_risk_reward(data: dict) -> CheckResult:
    """Verify stated R:R matches computed R:R, and meets 1.2:1 minimum."""
    try:
        ep  = float(data["entry_price"])
        sl  = float(data["stop_loss"])
        tp  = float(data["take_profit"])
        dir = str(data.get("direction", "")).upper()

        if dir == "LONG":
            reward = tp - ep
            risk   = ep - sl
        elif dir == "SHORT":
            reward = ep - tp
            risk   = sl - ep
        else:
            return CheckResult("risk_reward", False, 0.0, "unknown direction")

        if risk <= 0:
            return CheckResult("risk_reward", False, 0.0, f"Risk ≤ 0: risk={risk:.6f}")

        computed_rr = reward / risk
        stated_rr   = float(data.get("risk_reward_ratio", -1))

        # Check minimum threshold
        if computed_rr < MIN_RISK_REWARD:
            return CheckResult("risk_reward", False, 0.0,
                f"R:R {computed_rr:.2f} < minimum {MIN_RISK_REWARD} | "
                f"reward={reward:.6f}, risk={risk:.6f}")

        # Check stated vs computed match (within 5% tolerance)
        if stated_rr > 0:
            tolerance = abs(stated_rr - computed_rr) / computed_rr
            if tolerance > 0.05:
                return CheckResult("risk_reward", False, 0.5,
                    f"R:R mismatch: stated={stated_rr:.2f}, computed={computed_rr:.2f} (>{5:.0f}% off)")

        return CheckResult("risk_reward", True, 1.0,
            f"R:R={computed_rr:.2f} ✓ (min {MIN_RISK_REWARD})")
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as e:
        return CheckResult("risk_reward", False, 0.0, f"Compute error: {e}")


def check_confidence(data: dict) -> CheckResult:
    try:
        conf = float(data.get("confidence", -1))
        if 0.0 <= conf <= 1.0:
            if conf < MIN_CONFIDENCE:
                # Below threshold is valid IF direction is effectively "no trade"
                return CheckResult("confidence_range", True, 0.8,
                    f"confidence={conf:.2f} (below actionable {MIN_CONFIDENCE} — should reject)")
            return CheckResult("confidence_range", True, 1.0, f"confidence={conf:.2f} ✓")
        return CheckResult("confidence_range", False, 0.0, f"confidence {conf} out of [0,1]")
    except (TypeError, ValueError):
        return CheckResult("confidence_range", False, 0.0, "confidence not numeric")


def check_signals_used(data: dict) -> CheckResult:
    signals = data.get("signals_used", [])
    if not isinstance(signals, list) or len(signals) == 0:
        return CheckResult("signals_used", False, 0.0, "signals_used missing or empty")
    # Partial credit for recognizable signals
    known_count = sum(1 for s in signals if any(k.lower() in s.lower() for k in KNOWN_SIGNALS))
    score = min(1.0, known_count / max(len(signals), 1))
    return CheckResult("signals_used", score >= 0.5, score,
        f"{known_count}/{len(signals)} recognized signals: {signals[:3]}...")


def check_reasoning_present(data: dict) -> CheckResult:
    reasoning = str(data.get("reasoning", ""))
    if len(reasoning) < 30:
        return CheckResult("reasoning_quality", False, 0.0, f"reasoning too short ({len(reasoning)} chars)")
    # Check for key trading terms
    keywords = ["confluence", "signal", "support", "resistance", "trend", "RSI", "MACD",
                "risk", "entry", "stop", "target", "bearish", "bullish"]
    hits = sum(1 for k in keywords if k.lower() in reasoning.lower())
    score = min(1.0, hits / 3)   # 3+ keywords = full score
    return CheckResult("reasoning_quality", score >= 0.5, score,
        f"reasoning: {len(reasoning)} chars, {hits} keywords hit")


def check_verdict_valid(data: dict) -> CheckResult:
    v = str(data.get("verdict", "")).upper().strip()
    if v in VALID_VERDICTS:
        return CheckResult("verdict_valid", True, 1.0, f"verdict={v}")
    return CheckResult("verdict_valid", False, 0.0, f"Invalid verdict: {repr(v)}")


def check_position_pct(data: dict) -> CheckResult:
    try:
        pct = float(data.get("position_pct", -1))
        if pct < 0:
            return CheckResult("position_pct", False, 0.0, "position_pct missing")
        if pct > MAX_SYMBOL_EXP:
            return CheckResult("position_pct", False, 0.0,
                f"position_pct {pct:.1f}% > max {MAX_SYMBOL_EXP}%")
        return CheckResult("position_pct", True, 1.0, f"position_pct={pct:.2f}% ✓")
    except (TypeError, ValueError):
        return CheckResult("position_pct", False, 0.0, "position_pct not numeric")


def check_expected_direction(data: dict, expected: Optional[str]) -> Optional[CheckResult]:
    if expected is None:
        return None
    actual = str(data.get("direction", "")).upper()
    correct = actual == expected.upper()
    return CheckResult("direction_match", correct, 1.0 if correct else 0.0,
        f"expected={expected}, got={actual}")


def check_expected_verdict(data: dict, expected: Optional[str]) -> Optional[CheckResult]:
    if expected is None:
        return None
    actual = str(data.get("verdict", "")).upper()
    if not actual:
        # Try to infer from confidence
        conf = float(data.get("confidence", 0))
        actual = "APPROVED" if conf >= MIN_CONFIDENCE else "REJECTED"
    correct = actual == expected.upper()
    return CheckResult("verdict_match", correct, 1.0 if correct else 0.0,
        f"expected={expected}, got={actual}")


def check_regime_mentions(raw_output: str, regime: Optional[str]) -> Optional[CheckResult]:
    if regime is None:
        return None
    found = regime.lower().replace("_", " ") in raw_output.lower() or \
            regime.lower() in raw_output.lower()
    if not found:
        return CheckResult("regime_awareness", False, 0.0,
            f"regime '{regime}' NOT mentioned in output")
    return CheckResult("regime_awareness", True, 1.0,
        f"regime '{regime}' mentioned in output")


def check_regime_sizing(raw_output: str, data: dict, regime: Optional[str]) -> Optional[CheckResult]:
    """Verify the model actually applies regime multipliers, not just name-drops.

    CHOPPY:           position_size_mult = 0.5x → size should be ≤ 2.5% (half of 5% max)
    STRONG_TREND_UP:  position_size_mult = 1.5x → size should be > standard
    HIGH_VOLATILITY:  position_size_mult = 0.3x → very small or rejected
    RANGING:          position_size_mult = 0.7x → slightly reduced
    """
    if regime is None:
        return None

    # Try to find position size in parsed data or raw text
    pos_pct = data.get("position_pct")
    if pos_pct is None:
        # Try regex on raw output
        m = re.search(r"(?:position\s*size|size)[:\s]+([\d.]+)\s*%", raw_output, re.IGNORECASE)
        if m:
            try:
                pos_pct = float(m.group(1))
            except ValueError:
                pass

    # Check for sizing keywords that indicate regime adjustment
    text_lower = raw_output.lower()
    regime_upper = regime.upper().replace("_", " ")

    REGIME_EXPECTATIONS = {
        "CHOPPY": {
            "keywords": ["reduce", "half", "0.5x", "50%", "smaller", "cut size"],
            "max_pct": 2.5,   # 0.5x of normal 5% max
            "description": "CHOPPY → should reduce size (0.5x multiplier)",
        },
        "STRONG_TREND_UP": {
            "keywords": ["increase", "1.5x", "larger", "full size", "trending", "ride"],
            "min_pct": 2.0,   # should not be tiny
            "description": "STRONG_TREND_UP → should increase size (1.5x multiplier)",
        },
        "HIGH_VOLATILITY": {
            "keywords": ["reduce", "0.3x", "minimal", "small", "30%", "widen stops"],
            "max_pct": 1.5,   # 0.3x of normal 5%
            "description": "HIGH_VOLATILITY → should heavily reduce (0.3x multiplier)",
        },
        "RANGING": {
            "keywords": ["reduce", "0.7x", "smaller", "mean reversion"],
            "max_pct": 3.5,   # 0.7x of normal 5%
            "description": "RANGING → should slightly reduce (0.7x multiplier)",
        },
    }

    expectation = REGIME_EXPECTATIONS.get(regime)
    if not expectation:
        return None  # Unknown regime, skip

    score = 0.0
    details = []

    # Check 1: Does the text mention sizing adjustment? (0.5 weight)
    keyword_hits = sum(1 for k in expectation["keywords"] if k in text_lower)
    if keyword_hits > 0:
        score += 0.5
        details.append(f"{keyword_hits} sizing keywords found")
    else:
        details.append("no sizing adjustment keywords")

    # Check 2: Is the actual position % in the right range? (0.5 weight)
    if pos_pct is not None:
        max_pct = expectation.get("max_pct")
        min_pct = expectation.get("min_pct")
        if max_pct and pos_pct <= max_pct:
            score += 0.5
            details.append(f"size {pos_pct:.1f}% ≤ {max_pct}% cap")
        elif min_pct and pos_pct >= min_pct:
            score += 0.5
            details.append(f"size {pos_pct:.1f}% ≥ {min_pct}% floor")
        elif max_pct and pos_pct > max_pct:
            details.append(f"size {pos_pct:.1f}% EXCEEDS {max_pct}% (regime not applied)")
        elif min_pct and pos_pct < min_pct:
            details.append(f"size {pos_pct:.1f}% BELOW {min_pct}% (over-reduced)")
    else:
        details.append("position_pct not found — can't verify sizing")
        # Give partial credit if keywords were found
        if keyword_hits > 0:
            score += 0.25

    passed = score >= 0.5
    return CheckResult("regime_sizing", passed, score,
        f"{expectation['description']} | {'; '.join(details)}")


# ── Run checks on one parsed output ──────────────────────────────────────────
def run_checks(result: EvalResult, prompt: dict) -> None:
    d = result.parsed or {}

    # Always run these
    result.checks.append(check_required_fields(d))
    result.checks.append(check_direction(d))
    result.checks.append(check_entry_price(d))
    result.checks.append(check_sl_tp_direction(d))
    result.checks.append(check_risk_reward(d))
    result.checks.append(check_confidence(d))
    result.checks.append(check_signals_used(d))
    result.checks.append(check_reasoning_present(d))

    # Risk check fields (may or may not be present)
    if "verdict" in d:
        result.checks.append(check_verdict_valid(d))
    if "position_pct" in d:
        result.checks.append(check_position_pct(d))

    # Expected-value checks (null = skip)
    dir_check = check_expected_direction(d, prompt.get("expected_direction"))
    if dir_check:
        result.checks.append(dir_check)
        result.direction_correct = dir_check.passed

    verdict_check = check_expected_verdict(d, prompt.get("expected_verdict"))
    if verdict_check:
        result.checks.append(verdict_check)
        result.verdict_correct = verdict_check.passed

    regime_check = check_regime_mentions(result.raw_output, prompt.get("regime_check"))
    if regime_check:
        result.checks.append(regime_check)

    # Regime sizing: verify multiplier was actually applied, not just mentioned
    regime_sizing = check_regime_sizing(result.raw_output, d, prompt.get("regime_check"))
    if regime_sizing:
        result.checks.append(regime_sizing)

    result.compute_score()


# ── Model runner ──────────────────────────────────────────────────────────────
def query_ollama(model: str, prompt: str, timeout: int = 120) -> str:
    """Query a local Ollama model."""
    try:
        proc = subprocess.run(
            ["ollama", "run", model, prompt],
            capture_output=True, text=True, timeout=timeout
        )
        if proc.returncode != 0:
            print(f"  ⚠ Ollama error: {proc.stderr[:200]}", file=sys.stderr)
            return proc.stdout or ""
        return proc.stdout.strip()
    except subprocess.TimeoutExpired:
        return "[TIMEOUT]"
    except FileNotFoundError:
        print("  ✗ ollama not found. Is Ollama installed and in PATH?", file=sys.stderr)
        return "[OLLAMA_NOT_FOUND]"


# ── Main eval loop ────────────────────────────────────────────────────────────
def run_eval(model: str, prompts: list[dict], verbose: bool = False) -> list[EvalResult]:
    results = []
    print(f"\n{'='*60}")
    print(f"  RUNECLAW EVAL — model: {model}")
    print(f"  {len(prompts)} test prompts | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}\n")

    for i, prompt in enumerate(prompts, 1):
        pid = prompt["id"]
        cat = prompt["category"]
        print(f"[{i:02d}/{len(prompts)}] {pid} ({cat})", end="  ", flush=True)

        raw = query_ollama(model, prompt["prompt"])

        result = EvalResult(
            prompt_id=pid,
            category=cat,
            raw_output=raw,
            parsed=None,
        )

        if raw in ("[TIMEOUT]", "[OLLAMA_NOT_FOUND]"):
            result.parse_error = raw
            result.total_score = 0.0
            result.grade = "F"
            print(f"✗ {raw}")
        else:
            parsed = extract_json(raw)
            if parsed:
                result.parsed = parsed
                run_checks(result, prompt)
            else:
                result.parse_error = "Could not extract JSON or key-value pairs from output"
                result.total_score = 0.0
                result.grade = "F"

            symbol = "✓" if result.grade in ("A", "B") else ("~" if result.grade == "C" else "✗")
            print(f"{symbol}  score={result.total_score:.1f}  grade={result.grade}")

        if verbose and raw not in ("[TIMEOUT]", "[OLLAMA_NOT_FOUND]"):
            print(f"     raw: {raw[:120]}...")
            for c in result.checks:
                mark = "✓" if c.passed else "✗"
                print(f"     {mark} {c.name:<25} {c.detail}")
            print()

        results.append(result)

    return results


# ── Summary report ────────────────────────────────────────────────────────────
def print_summary(results: list[EvalResult], model: str) -> dict:
    total     = len(results)
    scores    = [r.total_score for r in results]
    avg_score = sum(scores) / total if total else 0
    grades    = {g: sum(1 for r in results if r.grade == g) for g in "ABCDF"}

    dir_results = [r for r in results if r.direction_correct is not None]
    dir_acc = sum(r.direction_correct for r in dir_results) / len(dir_results) if dir_results else None

    verdict_results = [r for r in results if r.verdict_correct is not None]
    verdict_acc = sum(r.verdict_correct for r in verdict_results) / len(verdict_results) if verdict_results else None

    # Per-check failure rates
    check_failures: dict[str, list[float]] = {}
    for r in results:
        for c in r.checks:
            check_failures.setdefault(c.name, []).append(c.score)
    check_avg = {k: sum(v) / len(v) for k, v in check_failures.items()}
    weak_checks = sorted(check_avg.items(), key=lambda x: x[1])[:5]

    print(f"\n{'='*60}")
    print(f"  EVAL SUMMARY — {model}")
    print(f"{'='*60}")
    print(f"  Prompts tested : {total}")
    print(f"  Avg score      : {avg_score:.1f}/100")
    print(f"  Grade dist     : A={grades['A']} B={grades['B']} C={grades['C']} D={grades['D']} F={grades['F']}")
    if dir_acc is not None:
        print(f"  Direction acc  : {dir_acc*100:.1f}%  ({len(dir_results)} prompts with expected direction)")
    if verdict_acc is not None:
        print(f"  Verdict acc    : {verdict_acc*100:.1f}%  ({len(verdict_results)} prompts with expected verdict)")

    print(f"\n  Weakest checks (avg score):")
    for name, avg in weak_checks:
        bar = "█" * int(avg * 10) + "░" * (10 - int(avg * 10))
        print(f"    {bar}  {avg*100:.0f}%  {name}")

    print(f"\n  Per-prompt results:")
    for r in results:
        bar = "█" * int(r.total_score / 10) + "░" * (10 - int(r.total_score / 10))
        print(f"    [{r.grade}] {bar} {r.total_score:5.1f}  {r.prompt_id}  {r.category}")
        if r.parse_error:
            print(f"         ⚠ {r.parse_error}")

    print()

    summary = {
        "model": model,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_prompts": total,
        "avg_score": round(avg_score, 2),
        "grades": grades,
        "direction_accuracy": round(dir_acc * 100, 1) if dir_acc is not None else None,
        "verdict_accuracy": round(verdict_acc * 100, 1) if verdict_acc is not None else None,
        "check_averages": {k: round(v * 100, 1) for k, v in check_avg.items()},
        "weak_checks": [(k, round(v * 100, 1)) for k, v in weak_checks],
        "results": [
            {
                "id": r.prompt_id,
                "category": r.category,
                "score": round(r.total_score, 1),
                "grade": r.grade,
                "direction_correct": r.direction_correct,
                "verdict_correct": r.verdict_correct,
                "parse_error": r.parse_error,
                "checks": [asdict(c) for c in r.checks],
                "raw_output_preview": r.raw_output[:300],
            }
            for r in results
        ],
    }
    return summary


# ── CLI entry point ───────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="RUNECLAW model eval harness")
    parser.add_argument("--model",   default="pbdes2022/HUMANOID-TRADERS",
                        help="Ollama model tag to evaluate")
    parser.add_argument("--output",  default=None,
                        help="Save JSON results to this file (optional)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print raw output and per-check details")
    parser.add_argument("--prompt-ids", nargs="*",
                        help="Run only specific prompt IDs (e.g. eval-001 eval-005)")
    args = parser.parse_args()

    prompts = TEST_PROMPTS
    if args.prompt_ids:
        prompts = [p for p in TEST_PROMPTS if p["id"] in args.prompt_ids]
        if not prompts:
            print(f"No prompts matched: {args.prompt_ids}")
            sys.exit(1)

    results = run_eval(args.model, prompts, verbose=args.verbose)
    summary = print_summary(results, args.model)

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(json.dumps(summary, indent=2))
        print(f"  Results saved → {out_path}\n")
    else:
        # Always save a timestamped copy
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        auto_path = Path(f"eval_{ts}.json")
        auto_path.write_text(json.dumps(summary, indent=2))
        print(f"  Results auto-saved → {auto_path}\n")


if __name__ == "__main__":
    main()
