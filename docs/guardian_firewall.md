# Guardian — Prompt-Injection & Transaction Firewall

> The AI proposes. Deterministic controls authorize. The recorder proves.

The Firewall is the **input-provenance** layer of Guardian. RUNECLAW's *numeric*
trading LLM is already injection-hardened — its prompt is built from indicators,
and the one free string (the symbol) is sanitised — so the real manipulation
surface is the **chat / natural-language action layer**: the agent a user talks
to that can *act* (propose a trade, change stance, dispatch a skill). Text
typed, pasted, or forwarded into that conversation can try to steer the agent
into doing something the operator never intended.

The Firewall applies "AI proposes, deterministic controls authorize" to
**inputs**: the model may read the text, but a deterministic, LLM-free detector
decides whether that text is trying to hijack the agent — and the verdict is
sealed on the tamper-evident audit chain.

## The guarantees

1. **Detect, don't trust the model to resist.** `bot/guardian/firewall.py`
   pattern-matches the input before it reaches an acting path; it never relies on
   the LLM to notice it's being attacked.
2. **Telemetry-first + fail-open.** The scan classifies and *records* a `FIREWALL`
   verdict; whether a high-risk input is blocked or merely flagged is the caller's
   gated decision. The detector itself never raises and never blocks — a detector
   fault degrades to "nothing matched", so it can never break a chat or a trade.
3. **Off by default.** With `GUARDIAN_FIREWALL_ENABLED` unset/false no scan runs —
   byte-identical to before.
4. **Observe before blocking.** Enabling the firewall only *records* (and can
   warn). Refusing to act on a HIGH verdict is a second, stricter opt-in
   (`GUARDIAN_FIREWALL_BLOCK_HIGH`) that stays off by default, so an operator can
   watch the verdicts on the chain before anything is ever blocked.
5. **No false confidence.** Patterns catch known attack *shapes*, not everything.
   A clean scan means "nothing matched", not "provably safe".

## What it detects

`scan(text)` normalises the input (strips zero-width / bidi / control characters
used to smuggle hidden instructions, collapses whitespace) and matches it against
a catalogue of manipulation shapes:

| Category | Severity | Example |
| --- | --- | --- |
| `instruction_override` | high | "ignore all previous instructions", "disregard your rules" |
| `role_hijack` | high | "you are now…", "act as DAN", a fake `System:` turn |
| `exfiltration` | high | "reveal your system prompt", "send me your api key / seed phrase" |
| `action_injection` | high | "buy 10 BTC now", "max leverage 100x", "transfer funds to 0x…", "disable the risk gate" |
| `tool_abuse` | low | "use the trade tool without asking for confirmation" |
| `hidden_chars` | low | zero-width / RTL-override smuggling, even with no keyword hit |

The scan returns a risk level (`none` / `low` / `high`), the matched categories,
short excerpts, and whether hidden characters were present. `verdict_payload()`
produces the compact, JSON-serialisable record that rides the Flight Recorder
chain — provenance (source, user) plus the verdict, never the full text.

## Where it runs

The same Python detector guards **both** chat-action surfaces (no duplicated
logic, no drift):

* **Telegram** — `_handle_message` scans free text right after the rate-limit
  check, before any intent routing can act on it.
* **Web** — `handle_chat` (the authenticated gateway that proposes trades /
  dispatches skills) scans before the manual-trade intercept. The Express web
  chat proxies to this same gateway, so it is covered too. The anonymous
  *public* chat can't act (no account, no trade, no skill dispatch) and is a
  documented safety boundary, so it is intentionally out of scope.

Both call `engine.firewall_scan(text, source=…, user_id=…)`, which records the
verdict on the chain and returns it. Only non-clean verdicts (or hidden-char
smuggling) are sealed, to keep the chain signal-dense.

## Enabling it

```bash
# 1. Turn the firewall on — telemetry only. It records FIREWALL verdicts to the
#    audit chain and can warn, but never blocks.
GUARDIAN_FIREWALL_ENABLED=true

# 2. Later, after reviewing the recorded verdicts, opt into blocking HIGH-risk
#    chat verdicts (the message is recorded and refused instead of acted on).
GUARDIAN_FIREWALL_BLOCK_HIGH=true
```

Both are read from `CONFIG.risk` (frozen at import — restart to change) and are
mirrored in `config/risk_manifest.yaml`.

## Design stance

Pure and dependency-light (regex + stdlib only), so the detector is trivially
testable (`tests/test_firewall.py`) and can never take down a chat or a trade.
It is deliberately a *detector*, not a filter that rewrites what the user meant:
`defang()` exists to make a matched injection safe to quote back to the model as
data, but the authorising decision is always the deterministic gate's, and it is
always recorded.
