# Meme & AI-agent token radar (read-only, safety-forward)

The first slice of memecoin support, scoped **"trade, don't launch"** (the
operator's explicit choice). This slice is pure intelligence — no money-path,
no execution, no minting.

## What it does

`app/lib/meme.js` sources live DEX pairs from **DEXScreener** (public API, no
key) and presents trending on-chain tokens ranked by **real 24h volume** — not
price pumps — each with an explicit **safety read**:

- `liquidity_usd` → `very-low-liquidity` (<$10k) / `low-liquidity` (<$50k)
- pair age → `under-24h-old` / `under-1w-old`
- 24h flow → `no-sells-yet` (can you even exit?), `buys-only-skew`
- a composite `risk.tier` of `high` (memecoin default) or `extreme`

These are the **same signals a future agent-buy will gate on**, surfaced up
front so nothing hides behind a green number.

## Surfaces

- `GET /api/market/meme` — the radar JSON (30 s cache).
- MCP `get_meme_radar` — same, machine-readable for other agents.
- Chat: "meme radar", "dexscreener", "degen", "ai-agent tokens" → a read-only
  snapshot with the safety framing.

## Discipline (§4 hard lines)

- **Never launches tokens.** The agent minting a coin and then trading it is
  RUNECLAW's one hard "no" (token-launch retail extraction). This module has no
  creation path and the disclaimer says so on every surface.
- **Read-only.** No execution, no signals fed to the engine, no bot behavior
  change — like the RWA radar.
- **Honest by default.** Every surface states memecoins are extremely high risk
  and most go to zero; the radar ranks by volume (activity), and boosted
  ("promoted") tokens are treated as a *risk* signal, never as quality.

## Pure + tested

`buildRadar(pairs, nowMs)` is a pure function over DEXScreener pair objects
(the network fetch is injectable + best-effort, returning `[]` on any failure).
`app/test/meme.test.js`: normalization, the risk-read escalation, dedupe,
volume ranking, age, chain grouping, and junk-input tolerance. 238/238 app
tests green.

## Next (the trade path, still to come — each its own gated PR)

1. Fold the on-chain **token-safety scanner** (`bot/core/token_safety.py`, rug/
   honeypot/mint-authority checks) in as a **hard precondition** to any buy.
2. DEX execution on the user's **own keys** via the existing custody-safe rails
   (Authority Envelope → gate → per-trade `authorize()` → own-keys executor →
   Flight Recorder → Proof-of-PnL). Default-OFF, opt-in, per-user loss breakers.
