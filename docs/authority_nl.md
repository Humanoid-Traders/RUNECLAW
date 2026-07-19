# Say-it-in-words trading authority (NL → Authority Envelope)

> *"Only majors, max $500 a trade, $2,000 a day, only on Bitget."*
> The user says what their agent may do; RUNECLAW compiles it to a revocable,
> tighten-only **Authority Envelope** that caps and authorizes every live order.

This is the custody keystone that turns the web live-trading gate
(`docs/web_live_trading.md`) from *possible* into *usable*: the gate's fifth
precondition is "a bound Authority Envelope in enforce mode", and this is how a
user creates one — in plain words, self-serve.

## Pipeline

1. **`bot/guardian/authority_nl.py`** — pure NL → spec. Maps the phrases the
   *custody boundary* actually enforces: per-trade / daily notional ceilings,
   symbol allow/block, venues, market type. Discipline:
   - No fabricated caps — a percent limit ("2% per trade") only becomes a dollar
     ceiling when account equity is supplied; otherwise it's `pending`, not guessed.
   - Withdrawal is never opened by NL (the envelope's double opt-in is separate).
   - Direction / confidence / drawdown *trade-filter* rules are a different layer
     (`intent_policy`) and are reported as `unmatched`, never invented here.
2. **`authority.compile_envelope`** — validates, **clamps to engine ceilings
   (tighten-only)**, hashes, and produces the enforce-able envelope.
3. **`bot/guardian/user_authority_store.py`** — binds one envelope per user
   (Telegram or `web:<id>`). Fresh envelopes start in `shadow`; reaching
   `enforce` is an explicit, separate step; `revoke` is the human kill-switch.
   `is_enforcing(user_id)` is exactly what the web live gate reads.

## Surfaces

- **Gateway** (`/gateway/authority/*`, per-user, self-serve): `preview` (compile,
  no bind), `apply` (recompile bot-side from the text + bind), `mode`
  (off/shadow/enforce), `status` (bound envelope + the live-ready checklist),
  `revoke`. Apply always recompiles from the text — the browser never sends a
  policy blob the bot would trust.
- **Web** (`app/routes/authority.js` → `/api/authority/*`, JWT-authed) + a
  "Your trading authority" card in the Trade view: a textarea → preview →
  save (shadow) → enforce toggle, with the live-on-your-own-keys checklist
  (operator switch · bot live · your opt-in · your keys · envelope enforcing).

## Predictions (tests)

`tests/test_authority_nl.py` (A1–A6): phrase mapping; percent-needs-equity (no
fabrication); compiles + clamps into a real envelope; store bind/mode/revoke
round-trip + persistence; enforce flips the gate precondition; gibberish is
honestly `unmatched`. `tests/test_web_gateway.py`: the preview→apply→enforce→
status→revoke flow end-to-end.

## Discipline recap

Recommendation → **authorization**, never silent execution. The envelope only
*tightens* (clamped to engine caps), withdrawal stays default-deny, and nothing
is enforced until the user explicitly arms it. *The AI proposes, the envelope
authorizes, the wallet/venue enforces, the recorder proves.*
