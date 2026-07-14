"""Prompt-injection sanitizers for free-form user text sent to the LLM.

Moved out of bot/skills/telegram_handler.py so surfaces that don't depend on
the python-telegram-bot package (the web user gateway) can share the exact
same defense. Telegram keeps importing these under its original private names.

DEFENSE-IN-DEPTH ONLY (RC-AUD-014): this denylist is thin and trivially
bypassable. It is NOT a security boundary. The real boundary is the execution
gate — LLM chat output has no execution authority; trades still require
confirm_trade -> compliance -> executor with numeric re-validation. Keep it
light so it never blocks legitimate trading commands.
"""

from __future__ import annotations

import re

# AG-H1: Prompt-injection sanitizer for free-form user text sent to LLM
INJECTION_PATTERNS = re.compile(
    r"(ignore\s+(all\s+)?previous\s+instructions"
    r"|ignore\s+above"
    r"|disregard\s+(all\s+)?previous"
    r"|system\s*:"
    r"|<\|?(system|im_start|endoftext)\|?>"
    r"|you\s+are\s+now\s+"
    r"|act\s+as\s+if"
    r"|pretend\s+you\s+are"
    r"|new\s+instructions?\s*:"
    r"|override\s+(previous\s+)?instructions"
    r"|forget\s+(all\s+)?previous"
    r"|do\s+not\s+follow\s+(the\s+)?(above|previous))",
    re.IGNORECASE,
)

MAX_CHAT_INPUT_LEN = 500


def sanitize_chat_input(text: str) -> str:
    """Sanitize free-form user text before sending to LLM.

    - Strips prompt-injection patterns FIRST
    - Then truncates to 500 characters
    """
    sanitized = INJECTION_PATTERNS.sub("[filtered]", text)
    truncated = sanitized[:MAX_CHAT_INPUT_LEN]
    return truncated.strip()


def sanitize_history_for_llm(history: list[dict]) -> list[dict]:
    """Sanitize replayed USER turns before they reach the LLM.

    RC-AUD-014: conversation-memory replay (``get_recent_as_llm_messages``)
    returns raw user text that was stored unsanitized. This closes that path by
    applying the same sanitizer to ``role == "user"`` turns on read; assistant
    turns are left intact.
    """
    out: list[dict] = []
    for m in history:
        if isinstance(m, dict) and m.get("role") == "user":
            out.append({**m, "content": sanitize_chat_input(m.get("content", ""))})
        else:
            out.append(m)
    return out
