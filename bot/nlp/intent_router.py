"""
RUNECLAW Natural-Language Intent Router.

Maps free-text user messages to registered skills via:
  1. Rule-based keyword matching (no LLM key needed)
  2. Optional LLM intent classification (when configured)

The router ONLY resolves to existing skills — it never invents actions
and never touches the risk gate. Every resolved intent is audited.

Safety invariant: the router is a dispatcher, not an executor.
It returns a (skill_name, kwargs) tuple; the caller decides whether
to execute and under what auth/risk constraints.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from bot.utils.logger import audit, system_log

logger = logging.getLogger(__name__)


# ── Intent result ─────────────────────────────────────────────────────

@dataclass
class IntentResult:
    """Result of intent classification."""
    skill: str = ""                 # Skill name to dispatch to (empty = no match)
    kwargs: dict = field(default_factory=dict)
    confidence: float = 0.0         # 0.0–1.0 (rules always return 1.0)
    source: str = "rules"           # "rules" or "llm"
    raw_text: str = ""              # Original user message
    explanation: str = ""           # Why this intent was chosen

    @property
    def matched(self) -> bool:
        return bool(self.skill)


# ── Symbol extraction ─────────────────────────────────────────────────

# Common crypto symbols (without /USDT suffix)
_KNOWN_SYMBOLS = {
    "btc", "bitcoin", "eth", "ethereum", "sol", "solana", "bnb", "xrp",
    "doge", "ada", "avax", "dot", "link", "matic", "uni", "atom",
    "near", "apt", "sui", "arb", "op", "sei", "jup", "jto", "wif",
    "bonk", "pepe", "shib", "render", "inj", "fet", "ondo", "pyth",
    "ron", "ray", "ton", "trx", "ltc", "bch", "etc", "fil", "icp",
    "hbar", "vet", "algo", "ftm", "mana", "sand", "gala",
}

# Map common names to ticker
_NAME_TO_TICKER = {
    "bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL",
    "dogecoin": "DOGE", "doge": "DOGE", "cardano": "ADA",
    "avalanche": "AVAX", "polkadot": "DOT", "chainlink": "LINK",
    "polygon": "MATIC", "uniswap": "UNI", "cosmos": "ATOM",
    "arbitrum": "ARB", "optimism": "OP",
}


def _extract_symbol(text: str) -> Optional[str]:
    """Extract a crypto symbol from free text. Returns 'BTC/USDT' format or None."""
    lower = text.lower()
    words = re.findall(r'[a-zA-Z]+', lower)

    for word in words:
        # Check name mapping first
        if word in _NAME_TO_TICKER:
            return f"{_NAME_TO_TICKER[word]}/USDT"
        # Check known tickers
        if word in _KNOWN_SYMBOLS:
            return f"{word.upper()}/USDT"

    # Try explicit patterns like "BTC/USDT" or "$BTC"
    explicit = re.search(r'\$?([A-Z]{2,10})/USDT', text.upper())
    if explicit:
        return f"{explicit.group(1)}/USDT"
    dollar = re.search(r'\$([A-Z]{2,10})\b', text.upper())
    if dollar and dollar.group(1).lower() in _KNOWN_SYMBOLS:
        return f"{dollar.group(1)}/USDT"

    return None


# ── Rule-based intent patterns ────────────────────────────────────────

# Each entry: (compiled_pattern, skill_name, needs_symbol, explanation)
_INTENT_RULES: list[tuple[re.Pattern, str, bool, str]] = []


def _rule(pattern: str, skill: str, needs_symbol: bool = False, explanation: str = ""):
    """Register a rule-based intent pattern."""
    _INTENT_RULES.append((
        re.compile(pattern, re.IGNORECASE),
        skill,
        needs_symbol,
        explanation or f"Matched pattern for {skill}",
    ))


# --- Scan / market overview ---
_rule(r"\b(scan|what.?s moving|anything moving|top movers?|market|overview|movers)\b",
      "scan_market", explanation="Market scan request")
_rule(r"\b(volume spike|big moves?|alert|unusual)\b",
      "scan_market", explanation="Volume/movement alert request")

# --- Analyze specific asset ---
_rule(r"\b(analy[sz]e|look at|check out|how.?s|what about|thoughts on|price of|entry)\b",
      "analyze_asset", needs_symbol=True, explanation="Asset analysis request")
_rule(r"\b(should i (buy|sell|long|short)|setup|signal|trade idea)\b",
      "analyze_asset", needs_symbol=True, explanation="Trade signal request")
_rule(r"\b(support|resistance|levels?|targets?|fibonacci|fib)\b",
      "analyze_asset", needs_symbol=True, explanation="Technical level request")

# --- Portfolio ---
_rule(r"\b(portfolio|my (positions?|book|trades?|holdings?)|pnl|profit|loss|balance|equity)\b",
      "get_portfolio", explanation="Portfolio status request")
_rule(r"\b(open positions?|what.?s open|current trades?)\b",
      "get_portfolio", explanation="Open positions request")

# --- Risk ---
_rule(r"\b(risk|exposure|drawdown|circuit.?breaker|health|safety)\b",
      "check_risk", explanation="Risk status request")

# --- Status / dashboard ---
_rule(r"\b(status|dashboard|state|how.?s the bot|engine|running)\b",
      "status", explanation="System status request")

# --- Journal ---
_rule(r"\b(journal|trade history|recent trades?|past trades?|log)\b",
      "trade_journal", explanation="Trade journal request")

# --- Macro ---
_rule(r"\b(macro|fomc|cpi|fed|inflation|nfp|economic|calendar|rate)\b",
      "macro_calendar", explanation="Macro event request")

# --- Backtest ---
_rule(r"\b(backtest|replay|simulate|historical|test strategy)\b",
      "run_backtest", explanation="Backtest request")

# --- Costs ---
_rule(r"\b(cost|spending|budget|llm cost|api cost)\b",
      "costs", explanation="Cost breakdown request")

# --- Halt/emergency ---
_rule(r"\b(halt|stop|emergency|kill|pause)\b",
      "halt", explanation="Emergency halt request")

# --- Patterns ---
_rule(r"\b(pattern|recurring|learned|strategy score)\b",
      "patterns", explanation="Pattern recognition request")

# --- Help ---
_rule(r"\b(help|commands?|what can you do|features?|how to)\b",
      "help", explanation="Help request")

# --- Learning ---
_rule(r"\b(learn|improve|self.?improve|adaptation)\b",
      "learning", explanation="Learning dashboard request")


# ── Intent Router ─────────────────────────────────────────────────────

class IntentRouter:
    """Routes free-text messages to skills.

    Uses rule-based matching first (fast, no API call).
    Falls back to LLM classification when rules don't match
    and an LLM is available.
    """

    def __init__(self) -> None:
        self._rules = _INTENT_RULES

    def classify_rules(self, text: str) -> IntentResult:
        """Pure rule-based classification. No LLM call."""
        symbol = _extract_symbol(text)

        for pattern, skill, needs_symbol, explanation in self._rules:
            if pattern.search(text):
                kwargs = {}
                if needs_symbol:
                    if symbol:
                        kwargs["symbol"] = symbol
                    else:
                        # Skill needs a symbol but we couldn't extract one
                        # Return a partial match -- caller can ask for clarification
                        return IntentResult(
                            skill=skill,
                            kwargs={},
                            confidence=0.5,
                            source="rules",
                            raw_text=text,
                            explanation=f"{explanation} (no symbol detected)",
                        )
                return IntentResult(
                    skill=skill,
                    kwargs=kwargs,
                    confidence=1.0,
                    source="rules",
                    raw_text=text,
                    explanation=explanation,
                )

        # No rule matched
        return IntentResult(raw_text=text)

    async def classify(self, text: str, llm_fn=None) -> IntentResult:
        """Classify intent using rules first, then optional LLM fallback.

        Args:
            text: User's free-text message
            llm_fn: Optional async callable(prompt) -> str for LLM classification
        """
        # Try rules first (instant, free)
        result = self.classify_rules(text)
        if result.matched and result.confidence >= 0.8:
            audit(system_log,
                  f"Intent matched by rules: {result.skill}",
                  action="intent_route", result="RULES",
                  data={"skill": result.skill, "text": text[:100]})
            return result

        # If rules gave a partial match (needs symbol), return it
        if result.matched and result.confidence >= 0.5:
            return result

        # No match from rules — try LLM if available
        if llm_fn is not None:
            try:
                llm_result = await self._classify_with_llm(text, llm_fn)
                if llm_result.matched:
                    audit(system_log,
                          f"Intent matched by LLM: {llm_result.skill}",
                          action="intent_route", result="LLM",
                          data={"skill": llm_result.skill, "text": text[:100]})
                    return llm_result
            except Exception as exc:
                logger.debug("LLM intent classification failed: %s", exc)

        # No match at all — return empty (caller falls back to chat)
        return IntentResult(raw_text=text)

    async def _classify_with_llm(self, text: str, llm_fn) -> IntentResult:
        """Use LLM to classify intent when rules don't match."""
        prompt = (
            "Classify this user message into ONE of these skills. "
            "Respond with ONLY the skill name, nothing else.\n\n"
            "Skills:\n"
            "- scan_market: scanning/overview of market movers\n"
            "- analyze_asset: analysis of a specific crypto (include symbol)\n"
            "- get_portfolio: portfolio, positions, PnL\n"
            "- check_risk: risk status, exposure, drawdown\n"
            "- trade_journal: trade history\n"
            "- macro_calendar: macro events, FOMC, CPI\n"
            "- costs: spending, budget\n"
            "- help: how to use the bot\n"
            "- NONE: doesn't match any skill (general chat)\n\n"
            f"User message: \"{text[:500]}\"\n\n"
            "Skill name:"
        )
        # F-08 FIX: Explicit timeout on LLM classification call
        import asyncio
        try:
            raw = await asyncio.wait_for(llm_fn(prompt), timeout=10.0)
        except asyncio.TimeoutError:
            logger.debug("LLM intent classification timed out")
            return IntentResult(raw_text=text)
        skill_name = raw.strip().lower().replace('"', '').replace("'", "")

        # Validate the response is a known skill
        valid_skills = {
            "scan_market", "analyze_asset", "get_portfolio", "check_risk",
            "trade_journal", "macro_calendar", "costs", "help",
        }
        if skill_name in valid_skills:
            kwargs = {}
            if skill_name == "analyze_asset":
                symbol = _extract_symbol(text)
                if symbol:
                    kwargs["symbol"] = symbol
            return IntentResult(
                skill=skill_name,
                kwargs=kwargs,
                confidence=0.7,
                source="llm",
                raw_text=text,
                explanation=f"LLM classified as {skill_name}",
            )

        return IntentResult(raw_text=text)
