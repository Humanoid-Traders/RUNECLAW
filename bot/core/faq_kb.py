"""Built-in FAQ knowledge base for the public chat.

The website's landing-page chat shows five starter questions. These get an
instant, deterministic, §4-safe answer straight from here — no LLM, no cost, and
it works even before any provider is connected (the default for anonymous web
visitors). This is what stops the public chat from ever replying with a raw
internal error (e.g. an "add a key to .env" hint), which is both a broken first
impression and a config leak.

Answers are compliance-aware by construction: simulation-first framing, risk
warnings, no dollar amounts, no performance promises. Free-form questions are
NOT answered here — they fall through to the LLM; only close matches to the
known FAQ topics return an answer, so precision stays high.
"""
from __future__ import annotations

import re
from typing import Optional

# The five landing-page starter questions, each a canned answer + the trigger
# phrases that map a user message onto it. Triggers are distinctive multi-word
# phrases so free-form questions ("should I use leverage on SOL?") do NOT match
# and instead reach the model.
_FAQ: list[dict] = [
    {
        "id": "what_is",
        "triggers": ["what is runeclaw", "whats runeclaw", "what's runeclaw",
                     "what is rune claw", "about runeclaw", "tell me about runeclaw",
                     "what does runeclaw do", "who are you", "what are you"],
        "answer": (
            "RUNECLAW is a simulation-first, explainable AI trading agent for "
            "crypto. It scans the market through a multi-check analysis pipeline, "
            "forms a trade thesis you can actually read (the “decision "
            "picture”), and passes every idea through a hard risk gate before "
            "anything can execute. It runs on the web, as a Telegram bot, and via "
            "an MCP server — the same agent everywhere.\n\n"
            "You stay in control: the AI proposes, deterministic controls "
            "authorize, and you confirm. Start in paper mode to watch it think, "
            "then connect an exchange when you're ready — everything it does "
            "is explainable and auditable."
        ),
    },
    {
        "id": "risk",
        "triggers": ["manage risk", "manage the risk", "risk management",
                     "how does it manage risk", "how do you manage risk",
                     "control risk", "how is risk managed"],
        "answer": (
            "Risk is enforced by deterministic controls — not the AI's "
            "judgment. Every idea passes a risk gate before it can execute:\n\n"
            "• Position-size caps, plus per-trade and daily loss limits\n"
            "• A circuit breaker that halts trading on a drawdown or losing "
            "streak\n"
            "• Correlation / exposure caps so you're not stacking the same "
            "bet\n"
            "• A stop-loss placed with the order\n\n"
            "Funds only ever move inside limits a human has set and can revoke: "
            "the AI proposes, the controls authorize, your wallet enforces. It "
            "defaults to paper trading so you can see the risk logic work before "
            "any real capital is involved."
        ),
    },
    {
        "id": "sweep",
        "triggers": ["liquidity sweep", "what is a liquidity sweep",
                     "liquidity grab", "stop hunt", "stop-loss hunt",
                     "liquidity sweeps"],
        "answer": (
            "A liquidity sweep is when price briefly pushes through an obvious "
            "level — just past a swing high or low where stop-loss orders "
            "cluster — triggering those stops, then reverses. Larger players "
            "do this to fill big orders against the liquidity those stops "
            "provide.\n\n"
            "RUNECLAW reads sweeps as context: one that fails and reverses can "
            "mark a high-probability turning point, while getting caught on the "
            "wrong side of one is a classic stop-out. It's one of roughly twenty "
            "checks the engine weighs — never a signal on its own."
        ),
    },
    {
        "id": "leverage",
        "triggers": ["how does leverage work", "what is leverage",
                     "explain leverage", "how leverage works",
                     "how does leverage", "leverage work"],
        "answer": (
            "Leverage lets your margin control a position several times its size "
            "— e.g. 5x means your margin controls five times its own value in "
            "exposure. It multiplies both gains AND losses, and it sets your "
            "liquidation price: the more leverage, the smaller the move against "
            "you that wipes the position.\n\n"
            "RUNECLAW defaults to a conservative 5x and lets you set your own, and "
            "its risk gate sizes every position off your real margin so one trade "
            "can't over-extend the account. Leverage is a risk multiplier, not "
            "free money — most blow-ups come from too much of it."
        ),
    },
    {
        "id": "exchanges",
        "triggers": ["which exchanges", "which exchange", "what exchanges",
                     "exchanges are supported", "supported exchanges",
                     "which venues", "what venues", "exchanges supported",
                     "exchange supported"],
        "answer": (
            "Bitget USDT-M futures is the primary trading venue. Hyperliquid "
            "(on-chain USDC perps) is supported too, and you can link read-only "
            "balances across major exchanges and on-chain wallets for one unified "
            "net-worth and exposure view.\n\n"
            "A venue router compares fees and liquidity per pair and recommends "
            "the cheapest place to trade — recommendations only; you decide. "
            "You connect an exchange with your own API keys, which are encrypted "
            "per user; RUNECLAW never takes custody of your funds."
        ),
    },
]


def _norm(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for phrase matching."""
    t = (text or "").lower().replace("’", "'")
    t = re.sub(r"[^a-z0-9'\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def faq_answer(question: str) -> Optional[str]:
    """The canned §4-safe answer for a landing-page starter question, or None if
    the message isn't a close match (so free-form questions reach the LLM)."""
    q = _norm(question)
    if not q:
        return None
    for item in _FAQ:
        for trig in item["triggers"]:
            if _norm(trig) in q:
                return item["answer"]
    return None


def public_fallback() -> str:
    """Friendly, no-leak reply when the live model isn't reachable and the
    message didn't match a FAQ topic. Never mentions provider config, keys, or
    env — it guides the visitor to the starter topics and to signing in."""
    return (
        "I'm RUNECLAW, the AI trading agent. I can walk you through the "
        "essentials — try one of these:\n\n"
        "• What is RUNECLAW?\n"
        "• How does it manage risk?\n"
        "• What is a liquidity sweep?\n"
        "• How does leverage work?\n"
        "• Which exchanges are supported?\n\n"
        "Sign in and connect an exchange to unlock live market scans, your own "
        "portfolio, and full conversational analysis."
    )
