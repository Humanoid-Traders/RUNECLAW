# Memecoin execution — Solana / Jupiter (planner slice)

Scoped **"trade, don't launch"**, chain **Solana**, DEX **Jupiter** (operator's
choices). This is the first execution slice: the **planner/preflight**. It moves
no funds and never signs — `would_execute` is always `False` here. Signing on
the user's own key is a later, separately-gated slice.

## The pipeline (once complete)

```
intent → plan_swap (this slice) → [feature flag] → [Authority Envelope]
       → [meme-buy safety gate] → Jupiter quote → sign on USER's key
       → broadcast → Flight Recorder → Proof-of-PnL
```

## What `bot/core/meme_executor.py::plan_swap` does

Turns a buy/sell intent into a fail-closed Jupiter swap plan. Every **buy**
clears, in order (all fail-closed — a missing input denies):

1. **`MEME_TRADING_ENABLED`** — master feature flag, **default OFF**.
2. **Authority Envelope** — the human-set, revocable authority must authorize
   this trade (passed in as `envelope_authorized`; wired to
   `bot/guardian/authority.authorize` at the call site).
3. **Meme-buy safety gate** (`bot/core/meme_gate`) — rug/honeypot verdict +
   liquidity/age/exit-ability + sizing.

**Sells are exits** and are *never* blocked by the safety gate — being able to
dump a rug is itself the safety property. Sells still require the flag +
envelope.

Returns `{allowed, would_execute: False, side, preconditions[], gate,
jupiter_request:{inputMint, outputMint, amount_usd, slippageBps}, venue}`. Buy
legs are `USDC → token`; sell legs are `token → USDC`.

## Discipline

- **Custody-safe** — RUNECLAW never holds funds; the future execution slice
  signs on the user's OWN key. The agent never mints tokens.
- **Default-OFF, fail-closed** — nothing trades unless the flag is on, the
  envelope authorizes, and (for buys) the gate passes.
- **Built before the money-path** — this planner is a pure function with no
  signing/broadcast, verified in isolation, exactly as the CEX web-live path was
  (gate → plan → execute, execution last).

## Tests

`tests/test_meme_executor.py` (X1–X8): feature-off blocks; full pass plans but
never executes; no-envelope fails closed; unsafe token blocks the buy; **a sell
skips the gate so a rug is always exitable**; malformed intent blocks; env flag
parsing; human-readable states no signing. 8 green; ruff + mypy clean.

## Next slices (each gated, on your go)

1. Live **Jupiter quote** fetch (read-only) to fill real `min_out`/route.
2. **Sign + broadcast** on the user's own Solana key — the actual money-path,
   behind the flag + envelope + per-user loss breakers, Flight-Recorder-logged.
