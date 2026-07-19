"""Prompt-Injection & Transaction Firewall — the input-provenance layer of Guardian.

RUNECLAW's *numeric* trading LLM is already injection-hardened (its prompt is
built from indicators, and the one free string — the symbol — is sanitised). The
real manipulation surface is the **chat / natural-language action layer**: a user
(or content pasted, forwarded, or fetched into a conversation) can try to steer
the agent that can *act* — place a trade, change stance, call a tool — into doing
something the user never intended.

``scan(text)`` is a **pure, deterministic detector** (no LLM, no network) that
classifies free text against known manipulation patterns and returns a risk
level, the matched categories, and a defanged copy of the text. It is the "AI
proposes, deterministic controls authorize" principle applied to *inputs*: the
model may read the text, but a deterministic gate decides whether that text is
trying to hijack the agent, and the decision is recorded.

Design stance:

* **Detect, don't trust the model to resist.** Pattern-match the input before it
  reaches an acting agent; never rely on the LLM to notice it's being attacked.
* **Telemetry-first.** The scan classifies and records; whether a high-risk input
  is blocked or merely flagged is the caller's (gated) decision. The detector
  itself never raises and never blocks.
* **No false-confidence.** Patterns catch known attack shapes, not everything —
  ``scan`` reports what it matched, and a clean scan is "nothing matched", not
  "provably safe".

Pure and dependency-light (regex + stdlib only), so it is trivially testable and
can never break a chat or a trade.
"""

from __future__ import annotations

import re
from typing import Any

# Risk ordering for comparisons / rollups.
RISK_ORDER = {"none": 0, "low": 1, "high": 2}

# ── Pattern catalogue ─────────────────────────────────────────────────
# Each category maps to (severity, [compiled patterns]). Patterns are lowercase,
# matched case-insensitively against a normalised copy of the text. Severity is
# the risk a single match contributes; the scan takes the max.
_CATEGORIES: dict[str, tuple[str, list[str]]] = {
    # "ignore the above / previous instructions", "disregard your rules"
    "instruction_override": ("high", [
        r"(?:ignore|disregard|forget|bypass)\s+(?:all |any |the |everything )?"
        r"(?:(?:previous|above|prior|earlier|preceding|your|these|those|system)\s+){1,3}"
        r"(?:instruction|instructions|prompt|prompts|context|message|rule|rules|guidance|guidelines|directive|directives)",
        r"forget (?:all |any |the |everything )?(?:you were told|what you were told)",
        r"(?:new|updated|revised|real|actual)\s+(?:instruction|instructions|system prompt|directive)s?\s*:",
        r"override (?:your |the )?(?:instruction|instructions|system|rules|safety|guardrail)",
    ]),
    # "you are now DAN", "act as", "pretend you are", "system: ..."
    "role_hijack": ("high", [
        r"you are (?:now|no longer)\b",
        r"pretend (?:to be|you are|that you)",
        r"\bact as (?:if you are |a |an |the )?(?:developer|admin|root|dan|jailbreak|unrestricted|nobody)",
        r"\bfrom now on\b.{0,40}\b(?:you|respond|answer|ignore|no rules|no restrictions)\b",
        r"(?:^|\n)\s*(?:system|assistant|developer)\s*:",   # fake role turn
        r"enable (?:dev(?:eloper)? mode|jailbreak|god mode|unrestricted mode)",
    ]),
    # "reveal your system prompt", "print your instructions", "what are your rules"
    "exfiltration": ("high", [
        r"(?:reveal|show|print|repeat|output|display|tell me)\s+(?:me\s+)?(?:your |the )?(?:system\s+)?(?:prompt|instructions|rules|guidelines|configuration|api key|secret|seed phrase|private key|password)",
        r"what (?:are|were) (?:your|the) (?:exact )?(?:instructions|system prompt|rules|guidelines)",
        r"(?:send|transfer|give|leak|exfiltrate)\s+(?:me\s+)?(?:your |the |all )?(?:api key|secret|seed|private key|password|credentials|mnemonic)",
    ]),
    # "buy 10 BTC now", "market order", "max leverage", "transfer funds to 0x..."
    "action_injection": ("high", [
        r"(?:buy|sell|long|short|market[\s-]?order|open a position|place (?:a |an )?(?:order|trade))\b.{0,40}\b(?:now|immediately|right away|asap|all in|everything|max)",
        r"\b(?:all[\s-]?in|max(?:imum)? leverage|100x|full (?:size|balance|position))\b",
        r"(?:transfer|send|withdraw|move)\s+(?:all\s+)?(?:my |the |your )?(?:funds|balance|money|usdt|usdc|btc|eth|crypto)\s+to\b",
        r"\b(?:to|address)\s*[:=]?\s*0x[a-f0-9]{40}\b",     # a hard wallet address in an instruction
        r"disable (?:the )?(?:risk|safety|confirmation|gate|guardrail|limit)s?",
    ]),
    # "use the trade tool", "call your ... function to ..."
    "tool_abuse": ("low", [
        r"(?:call|invoke|use|run|execute)\s+(?:your |the )?(?:\w+\s+)?(?:tool|function|command|mcp|api|endpoint)\b",
        r"without (?:asking|confirmation|permission|telling)",
        r"do (?:not|n't) (?:ask|confirm|require confirmation|tell)",
    ]),
}

# Zero-width / bidi / control characters used to smuggle hidden instructions.
_HIDDEN_CHARS = re.compile(
    "[​‌‍‎‏‪-‮⁠⁦-⁩﻿\x00-\x08\x0b\x0c\x0e-\x1f]"
)
_MAX_SCAN = 20000   # scan at most this many chars (chat inputs are small)


def _compiled() -> dict[str, tuple[str, list[re.Pattern]]]:
    # Compile once, cache on the function object.
    cache = getattr(_compiled, "_cache", None)
    if cache is None:
        cache = {
            cat: (sev, [re.compile(p, re.IGNORECASE) for p in pats])
            for cat, (sev, pats) in _CATEGORIES.items()
        }
        _compiled._cache = cache   # type: ignore[attr-defined]
    return cache


def _normalise(text: str) -> tuple[str, bool]:
    """Strip hidden/bidi/control chars and collapse whitespace so obfuscated
    injections (zero-width splits, RTL overrides) match. Returns (clean, had_hidden)."""
    had_hidden = bool(_HIDDEN_CHARS.search(text))
    clean = _HIDDEN_CHARS.sub("", text)
    clean = re.sub(r"\s+", " ", clean)
    return clean, had_hidden


def scan(text: Any) -> dict:
    """Classify free text against manipulation patterns. Pure, never raises.

    Returns::

        {
          "risk": "none" | "low" | "high",
          "score": int,                 # number of distinct category hits
          "categories": [str, ...],     # which categories matched
          "matches": [{"category","severity","excerpt"}, ...],  # capped
          "hidden_chars": bool,         # zero-width/bidi smuggling detected
          "length": int,
        }
    """
    result = {"risk": "none", "score": 0, "categories": [],
              "matches": [], "hidden_chars": False, "length": 0}
    try:
        if not text:
            return result
        raw = str(text)[:_MAX_SCAN]
        result["length"] = len(raw)
        clean, had_hidden = _normalise(raw)
        result["hidden_chars"] = had_hidden
        cats: list[str] = []
        matches: list[dict] = []
        worst = "none"
        for cat, (sev, patterns) in _compiled().items():
            for pat in patterns:
                m = pat.search(clean)
                if m:
                    cats.append(cat)
                    if RISK_ORDER[sev] > RISK_ORDER[worst]:
                        worst = sev
                    if len(matches) < 12:
                        s = m.start()
                        matches.append({
                            "category": cat, "severity": sev,
                            "excerpt": clean[max(0, s - 12): s + 48].strip(),
                        })
                    break   # one hit per category is enough
        # Hidden/bidi smuggling is itself a low-risk signal even with no keyword hit.
        if had_hidden and worst == "none":
            worst = "low"
            cats.append("hidden_chars")
        result["categories"] = cats
        result["score"] = len(cats)
        result["matches"] = matches
        result["risk"] = worst
        return result
    except Exception:
        # A detector fault must never break a chat — fail to "unknown/none".
        return result


def defang(text: Any, verdict: dict | None = None) -> str:
    """Return a copy of the text safe to embed in an LLM prompt as *quoted data*.

    Strips hidden/control chars and neutralises obvious role-turn markers so a
    matched injection can't re-assert itself when the input is later shown to the
    model as context. Not a guarantee — pair with the caller's gating on
    ``scan().risk``.
    """
    try:
        if not text:
            return ""
        clean = _HIDDEN_CHARS.sub("", str(text)[:_MAX_SCAN])
        # Defuse leading role turns like "System:" / "Assistant:" at line starts.
        clean = re.sub(r"(?im)^(\s*)(system|assistant|developer)\s*:",
                       r"\1[\2]", clean)
        return clean
    except Exception:
        try:
            return str(text)[:_MAX_SCAN]
        except Exception:
            return ""


def verdict_payload(text: Any, source: str = "chat", user_id: str = "") -> dict:
    """A compact, JSON-serialisable firewall record for the Flight Recorder /
    telemetry: the scan verdict plus provenance (where the text came from, who
    sent it) — never the full text (only a short excerpt on a hit)."""
    v = scan(text)
    return {
        "source": str(source)[:32],
        "user_id": str(user_id)[:64],
        "risk": v["risk"],
        "score": v["score"],
        "categories": v["categories"][:12],
        "hidden_chars": v["hidden_chars"],
        "length": v["length"],
        "excerpt": (v["matches"][0]["excerpt"] if v["matches"] else ""),
    }
