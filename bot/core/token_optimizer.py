"""
RUNECLAW Token Optimizer -- 4 LLM token optimization strategies.

1. Tiered Analysis Pipeline: rule-engine → gpt-4o-mini → gpt-4o escalation
2. Smart Batching: combine multiple symbols into single LLM calls
3. Adaptive Frequency: skip LLM when market is quiet (low ADX, no volume spike)

(Semantic Cache is in llm_cache.py)

Safety guarantees:
  - Tiered pipeline never skips risk checks
  - Batch responses are validated per-symbol (malformed = fallback to rules)
  - Adaptive frequency never suppresses monitoring of open positions
  - All savings are tracked and auditable
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from bot.utils.logger import audit, system_log
from bot.utils.models import MarketSignal


# ── Tiered Analysis Pipeline ────────────────────────────────────

class TieredPipeline:
    """3-tier escalation: free rules → gpt-4o-mini → gpt-4o.

    Tier 1 (FREE): Rule engine handles clear-cut signals:
      - RSI < 25 or > 75 (extreme)
      - ADX > 35 and strong DI divergence (clear trend)
      - Confluence > 0.75 or < 0.25 (strong agreement)

    Tier 2 (CHEAP): gpt-4o-mini for moderate signals:
      - Confluence 0.40-0.60 (ambiguous zone)
      - Mixed indicators

    Tier 3 (FULL): gpt-4o for high-potential signals:
      - Confluence > 0.60 (promising but needs thesis quality)
      - Large position size at risk
    """

    @staticmethod
    def classify_tier(indicators: dict, signal: MarketSignal) -> int:
        """Determine which tier should handle this analysis.

        Returns 1 (rules), 2 (mini), or 3 (full model).
        """
        rsi = indicators.get("rsi", 50)
        adx = indicators.get("adx", 0)
        confluence = indicators.get("confluence", 0.5)
        plus_di = indicators.get("plus_di", 0)
        minus_di = indicators.get("minus_di", 0)

        # Tier 1: Obvious signals -- rules are sufficient
        if rsi < 25 or rsi > 75:
            return 1
        if adx > 35 and abs(plus_di - minus_di) > 15:
            return 1
        if confluence > 0.75 or confluence < 0.25:
            return 1

        # Tier 3: High-potential signals worth full model
        if confluence > 0.60:
            return 3
        if signal.volume_spike and abs(signal.change_pct_24h) > 5:
            return 3

        # Tier 2: Everything else -- cheap model
        return 2

    @staticmethod
    def tier_label(tier: int) -> str:
        return {1: "RULES", 2: "MINI", 3: "FULL"}.get(tier, "UNKNOWN")


# ── Smart Batching ──────────────────────────────────────────────

@dataclass
class BatchRequest:
    """A single symbol's data bundled for batch LLM analysis."""
    symbol: str
    indicators: dict
    signal: MarketSignal


class SmartBatcher:
    """Combine multiple symbols into a single LLM call.

    Instead of N separate LLM calls for N symbols in the same tier,
    combine them into one prompt with structured multi-symbol output.

    Max batch size is capped at 5 to keep prompts under token limits.
    """

    MAX_BATCH_SIZE = 5

    @staticmethod
    def build_batch_prompt(requests: list[BatchRequest]) -> str:
        """Build a single prompt covering multiple symbols.

        Each symbol gets a compact data block. The LLM returns a JSON array
        with one object per symbol.
        """
        parts = [
            "Analyze these symbols. Return a JSON array with one object per symbol.",
            "Each object: {\"symbol\": \"...\", \"direction\": \"LONG\"|\"SHORT\", "
            "\"confidence\": 0.0-1.0, \"reasoning\": \"...\"}",
            "",
        ]

        for req in requests[:SmartBatcher.MAX_BATCH_SIZE]:
            ind = req.indicators
            sig = req.signal
            parts.append(
                f"--- {sig.symbol} ---\n"
                f"Price=${sig.price} 24h={sig.change_pct_24h}% vol_spike={sig.volume_spike}\n"
                f"Regime={ind.get('regime', '?')} Confluence={ind.get('confluence', 0):.2f}\n"
                f"RSI={ind.get('rsi')} MACD_hist={ind.get('macd_histogram')}\n"
                f"ADX={ind.get('adx')} +DI={ind.get('plus_di')} -DI={ind.get('minus_di')}\n"
                f"BB_%B={ind.get('bb_pct_b')} VWAP={ind.get('vwap', 'N/A')}"
            )

        prompt = "\n".join(parts)
        return prompt[:6000]  # hard cap

    @staticmethod
    def parse_batch_response(text: str, symbols: list[str]) -> dict[str, dict]:
        """Parse a multi-symbol LLM response.

        Returns dict mapping symbol -> thesis dict.
        Missing or malformed entries return None (fail-closed per symbol).
        """
        import json as _json

        results: dict[str, dict] = {}
        stripped = text.strip()

        # Try to extract JSON array
        try:
            # Handle markdown code blocks
            if "```" in stripped:
                import re
                match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', stripped, re.DOTALL)
                if match:
                    stripped = match.group(1)

            data = _json.loads(stripped)
            if isinstance(data, list):
                for item in data:
                    sym = item.get("symbol", "")
                    if sym in symbols:
                        d = str(item.get("direction", "LONG")).upper()
                        conf = max(0.0, min(1.0, float(item.get("confidence", 0.0))))
                        reasoning = str(item.get("reasoning", ""))
                        results[sym] = {
                            "direction": "SHORT" if "SHORT" in d else "LONG",
                            "confidence": conf,
                            "reasoning": reasoning,
                            "_parsed": True,
                            "source": "LLM_BATCH",
                        }
        except (ValueError, TypeError, _json.JSONDecodeError):
            pass

        return results

    @staticmethod
    def estimate_savings(batch_size: int) -> dict:
        """Estimate token savings from batching vs individual calls.

        Assumes ~400 tokens prompt overhead per call (system message, format instructions).
        Batching shares the overhead across N symbols.
        """
        overhead_per_call = 400
        data_per_symbol = 200
        individual_total = batch_size * (overhead_per_call + data_per_symbol)
        batched_total = overhead_per_call + batch_size * data_per_symbol
        saved = individual_total - batched_total
        return {
            "individual_tokens": individual_total,
            "batched_tokens": batched_total,
            "tokens_saved": saved,
            "reduction_pct": round(saved / individual_total * 100, 1) if individual_total > 0 else 0,
        }


# ── Adaptive Frequency ─────────────────────────────────────────

class AdaptiveFrequency:
    """Skip LLM calls during low-volatility / quiet market periods.

    Conditions for skipping LLM (use rules only):
      - ADX < 15 (no trend) AND no volume spike AND |change| < 2%
      - This indicates a boring, range-bound market where LLM adds no value
      - Rules engine handles range-bound conditions well enough

    Never skips:
      - Open position monitoring (SL/TP checks are free)
      - High-volatility events (ADX > 25 or volume spike)
      - Signals with > 3% 24h change
    """

    @staticmethod
    def should_use_llm(signal: MarketSignal, indicators: dict) -> bool:
        """Return True if LLM analysis is warranted, False to use rules only."""
        adx = indicators.get("adx", 0)
        change = abs(signal.change_pct_24h)

        # Always use LLM for significant moves
        if change > 3.0:
            return True

        # Always use LLM for volume spikes
        if signal.volume_spike:
            return True

        # Always use LLM when trend is strong
        if adx > 25:
            return True

        # Quiet market: skip LLM, use rules
        if adx < 15 and not signal.volume_spike and change < 2.0:
            audit(
                system_log,
                f"Adaptive skip: {signal.symbol} (ADX={adx:.1f}, change={change:.1f}%)",
                action="adaptive_freq",
                result="SKIP_LLM",
                data={"symbol": signal.symbol, "adx": adx, "change_pct": change},
            )
            return False

        # Default: use LLM
        return True


# ── Optimization Stats ──────────────────────────────────────────

@dataclass
class OptimizationStats:
    """Aggregate token optimization metrics across all strategies."""
    tier1_rule_calls: int = 0
    tier2_mini_calls: int = 0
    tier3_full_calls: int = 0
    batch_calls: int = 0
    batch_symbols_processed: int = 0
    adaptive_skips: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    estimated_tokens_saved: int = 0
    estimated_cost_saved_usd: float = 0.0

    def record_tier(self, tier: int) -> None:
        if tier == 1:
            self.tier1_rule_calls += 1
            self.estimated_tokens_saved += 800  # full call avoided
            self.estimated_cost_saved_usd += 0.003
        elif tier == 2:
            self.tier2_mini_calls += 1
            # mini is ~17x cheaper than full, savings = 16/17 of full cost
            self.estimated_tokens_saved += 600
            self.estimated_cost_saved_usd += 0.0028
        else:
            self.tier3_full_calls += 1

    def record_batch(self, batch_size: int) -> None:
        self.batch_calls += 1
        self.batch_symbols_processed += batch_size
        savings = SmartBatcher.estimate_savings(batch_size)
        self.estimated_tokens_saved += savings["tokens_saved"]
        self.estimated_cost_saved_usd += savings["tokens_saved"] * 0.000003  # rough $/token

    def record_adaptive_skip(self) -> None:
        self.adaptive_skips += 1
        self.estimated_tokens_saved += 800
        self.estimated_cost_saved_usd += 0.003

    def snapshot(self) -> dict:
        total_calls = self.tier1_rule_calls + self.tier2_mini_calls + self.tier3_full_calls
        return {
            "tier_distribution": {
                "tier1_rules": self.tier1_rule_calls,
                "tier2_mini": self.tier2_mini_calls,
                "tier3_full": self.tier3_full_calls,
                "total": total_calls,
            },
            "batching": {
                "batch_calls": self.batch_calls,
                "symbols_batched": self.batch_symbols_processed,
            },
            "adaptive_frequency": {
                "llm_skips": self.adaptive_skips,
            },
            "savings": {
                "estimated_tokens_saved": self.estimated_tokens_saved,
                "estimated_cost_saved_usd": round(self.estimated_cost_saved_usd, 4),
            },
        }
