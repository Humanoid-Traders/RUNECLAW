# Explain-my-fill — a plain-English "why" for every recorded decision

The Flight Recorder seals each decision's full provenance into a SHA-256
hash-chained record (thesis, voters, model/version, risk, outcome). This turns
one such record into a sentence a human reads — and pairs the story with the
verifiable Proof-of-PnL, so the explanation is only as trustworthy as the chain
it cites, and the chain is re-derivable.

## What it produces (`bot/guardian/explain_fill.py`, pure & deterministic)

`explain(record)` →
- **headline** — *what* was decided (took/rejected, live/paper, direction,
  symbol, confidence).
- **why** — *why*, drawn strictly from the record: setup (signal/strategy), the
  strongest bullish/bearish factors, the top ranked voters, reward:risk +
  geometry, and the thesis text.
- **provenance** — *by whom*: model, analysis version, prompt hash.
- **outcome** — *how it turned out* (won/lost/flat + exit reason), only when the
  position has closed.
- **verification** — the chain sequence + entry hash, "re-derivable from the
  record".
- **narrative** — a one-paragraph synthesis of all of the above.

**Faithful, not generative** — it narrates the sealed record and invents
nothing (a record with no voters yields no "voters" line). Same record → same
narrative; no LLM, no network. A caller may layer an LLM rephrase on top, but the
substance is drawn strictly from what actually happened.

## How it flows

The narration is attached inside the shared `assemble_flight_records` assembler,
so **every consumer gets it for free** — the web Flight Recorder view, the MCP
tool, any future surface. The Portfolio → Flight Recorder card now leads each
entry with the 🗣️ plain-English narrative, above the existing provenance details.

## Tests

`tests/test_explain_fill.py` (E1–E6): headline reflects the decision;
why draws from factors + voters + geometry + thesis; provenance surfaced;
win/loss/open outcomes narrated correctly; verification cites the chain;
faithful + deterministic + attached by the assembler. 10 green; the flight
recorder suite stays green (additive field); `mypy` clean.
