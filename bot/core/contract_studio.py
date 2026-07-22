"""Contract Studio — the §4-compliant core of the AI smart-contract dev feature.

RUNECLAW can draft Solidity with the tier-routed LLM, but AI is strong at
*code-level* bugs and weak at *economic* exploits (2026 benchmarks). So the
product NEVER claims a contract is "audited" or "safe". This module is the
compliance spine that keeps it honest:

  * ``scan_security_flags`` — a PURE, deterministic heuristic scanner that RAISES
    FLAGS (with the matched reason + a fix hint), never a verdict. It reports
    what to LOOK AT, not what is safe. Zero flags ≠ "safe" — it means the cheap
    heuristics found nothing, which is a very different claim.
  * ``build_generation_prompt`` — the system prompt for drafting Solidity, which
    bakes the "draft, not audited; get a professional audit before mainnet"
    disclaimer into the model's own instructions.
  * ``AUDIT_DISCLAIMER`` — the single sentence every surface must show.

No network, no LLM call, no money-path here — this is pure text analysis so it is
trivially testable and can never move a coin or leak a key. The LLM generation
call and the web/deploy surfaces are separate, later slices that build on this.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The one sentence every Contract Studio surface must carry. AI code review finds
# code-level bugs, not economic exploits — so this is advisory, never a verdict.
AUDIT_DISCLAIMER = (
    "Heuristic flags only — an AI review finds code-level issues, not economic "
    "exploits. Get a professional audit before deploying to mainnet or holding "
    "real value."
)


@dataclass(frozen=True)
class SecurityFlag:
    """One heuristic finding. A FLAG (look here), never a verdict (it's safe)."""
    id: str                       # stable slug, e.g. "tx-origin-auth"
    severity: str                 # "high" | "medium" | "low" | "info"
    title: str                    # short human label
    detail: str                   # what was matched + why it matters
    hint: str                     # what to check / how to fix
    line: int = 0                 # 1-based line of the first match (0 = file-level)


# Each rule: (id, severity, compiled pattern, title, detail, hint). Patterns are
# deliberately conservative — better a false flag the user dismisses than a
# missed class. This is a REVIEW AID, not a linter of record.
_RULES = [
    ("tx-origin-auth", "high", re.compile(r"\btx\.origin\b"),
     "Authorization via tx.origin",
     "tx.origin is the original EOA, not the immediate caller — phishing "
     "contracts can pass a tx.origin==owner check.",
     "Use msg.sender for authorization; reserve tx.origin for rare, explicit cases."),
    ("selfdestruct", "high", re.compile(r"\bselfdestruct\s*\(|\bsuicide\s*\("),
     "selfdestruct present",
     "selfdestruct can permanently remove the contract and force-send its ETH; "
     "a reachable one is a common rug/footgun.",
     "Confirm it is unreachable by untrusted callers, or remove it."),
    ("delegatecall", "high", re.compile(r"\.delegatecall\s*\("),
     "Low-level delegatecall",
     "delegatecall runs foreign code in THIS contract's storage context — the "
     "classic proxy-storage-collision and takeover vector.",
     "Verify the target is trusted/immutable and storage layouts match."),
    ("unchecked-lowlevel-call", "high",
     re.compile(r"\.call\s*\{[^}]*\}\s*\(|\.call\s*\(|\.send\s*\("),
     "Low-level call/send — check the return value",
     "The return value of .call/.send is not reverted automatically; ignoring it "
     "silently swallows failures (and .send caps gas at 2300).",
     "Check the boolean return and revert on failure, or prefer a pull pattern."),
    ("arbitrary-external-call", "medium",
     re.compile(r"\.transfer\s*\(\s*(?:address\s*\()?\s*msg\.sender"),
     "State-changing external transfer to msg.sender",
     "Sending value to msg.sender while mutating state can enable reentrancy if "
     "effects run after the transfer.",
     "Apply checks-effects-interactions or a nonReentrant guard."),
    ("block-timestamp", "medium", re.compile(r"\bblock\.timestamp\b|\bnow\b(?!\w)"),
     "block.timestamp used in logic",
     "Miners/validators can nudge block.timestamp by seconds — unsafe as a "
     "randomness source or a tight deadline.",
     "Don't use it for randomness; allow a tolerance window for deadlines."),
    ("weak-randomness", "high",
     re.compile(r"keccak256\s*\([^)]*block\.(?:timestamp|number|difficulty|prevrandao)"),
     "On-chain pseudo-randomness",
     "Hashing block fields for randomness is predictable/manipulable by the "
     "proposer — a classic lottery/NFT-mint exploit.",
     "Use a VRF (e.g. Chainlink VRF) or commit-reveal."),
    ("unbounded-loop", "medium",
     re.compile(r"for\s*\([^;]*;[^;]*\.length\s*;"),
     "Loop bounded by an array length",
     "Iterating an attacker-growable array can exceed the block gas limit and "
     "brick the function (griefing/DoS).",
     "Bound the iteration, paginate, or use a pull pattern."),
    ("missing-spdx", "low", None,        # handled specially (file-level absence)
     "No SPDX license identifier",
     "A missing SPDX-License-Identifier trips solc warnings and licensing checks.",
     "Add a `// SPDX-License-Identifier: <license>` header."),
    ("floating-pragma", "low", re.compile(r"pragma\s+solidity\s+\^"),
     "Floating pragma (^)",
     "A caret pragma lets the contract compile under future compilers with "
     "different behavior than what was tested/audited.",
     "Pin an exact compiler version for deployed contracts."),
    ("no-pragma", "medium", None,        # handled specially (file-level absence)
     "No solidity pragma",
     "Without a `pragma solidity` the compiler version is unconstrained.",
     "Declare a `pragma solidity` version."),
]


def _line_of(src: str, idx: int) -> int:
    return src.count("\n", 0, idx) + 1


def scan_security_flags(source: str) -> list[SecurityFlag]:
    """Heuristically scan Solidity source and return FLAGS to review, ordered
    high→low severity then by line. Pure + deterministic. An empty list means the
    cheap heuristics matched nothing — NOT that the contract is safe. Never
    raises: malformed / empty input returns an empty list."""
    src = source if isinstance(source, str) else ""
    if not src.strip():
        return []
    flags: list[SecurityFlag] = []
    for rid, sev, pat, title, detail, hint in _RULES:
        if pat is None:
            continue                                  # file-level rules handled below
        m = pat.search(src)
        if m:
            flags.append(SecurityFlag(id=rid, severity=sev, title=title,
                                      detail=detail, hint=hint,
                                      line=_line_of(src, m.start())))
    # File-level absence checks.
    if "SPDX-License-Identifier" not in src:
        r = next(x for x in _RULES if x[0] == "missing-spdx")
        flags.append(SecurityFlag(id=r[0], severity=r[1], title=r[3],
                                  detail=r[4], hint=r[5], line=0))
    if not re.search(r"pragma\s+solidity", src):
        r = next(x for x in _RULES if x[0] == "no-pragma")
        flags.append(SecurityFlag(id=r[0], severity=r[1], title=r[3],
                                  detail=r[4], hint=r[5], line=0))
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    flags.sort(key=lambda f: (order.get(f.severity, 9), f.line))
    return flags


def flags_summary(flags: list[SecurityFlag]) -> dict:
    """A compact, UI-friendly summary — counts by severity + the disclaimer.
    Always advisory: ``clean`` means 'no heuristic flags', never 'audited safe'."""
    by_sev = {"high": 0, "medium": 0, "low": 0, "info": 0}
    for f in flags:
        by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
    return {
        "count": len(flags),
        "by_severity": by_sev,
        "clean": len(flags) == 0,          # NOT "safe" — see disclaimer
        "disclaimer": AUDIT_DISCLAIMER,
    }


def build_generation_prompt(spec: str, *, license: str = "MIT",
                            pragma: str = "0.8.24") -> str:
    """The system prompt for drafting a Solidity contract from a natural-language
    spec. Bakes the compliance posture into the model's own instructions: produce
    a clear DRAFT, pin the pragma + SPDX, and end on the audit disclaimer — never
    claim the result is audited or safe."""
    spec = (spec or "").strip()
    return (
        "You are a senior Solidity engineer drafting a contract for review. "
        "Write clear, idiomatic, well-commented Solidity.\n"
        f"- Start with `// SPDX-License-Identifier: {license}` and "
        f"`pragma solidity {pragma};` (pinned, not floating).\n"
        "- Prefer battle-tested patterns (checks-effects-interactions, pull "
        "payments, OpenZeppelin building blocks where apt).\n"
        "- Use msg.sender (never tx.origin) for auth; avoid selfdestruct, "
        "unchecked low-level calls, and on-chain randomness.\n"
        "- After the code, list the key assumptions and what a human auditor "
        "should scrutinize.\n"
        "- This is a DRAFT for review, NOT an audited or production-safe "
        f"contract. {AUDIT_DISCLAIMER}\n\n"
        f"Contract to draft:\n{spec}"
    )
