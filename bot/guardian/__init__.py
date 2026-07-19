"""RUNECLAW Guardian — the safety, control, evidence, and recovery layer for
autonomous crypto capital.

    The AI proposes. Deterministic controls authorize. The wallet enforces.
    The recorder proves. The escape agent recovers.

Modules land incrementally:

* ``flight_recorder`` — a provenance-complete, tamper-evident record of every
  trading decision, a thin layer over the engine's hash-chained audit log.
* ``intent_policy`` — the Formal Strategy Intent Compiler: plain-language intent
  compiled into a deterministic, tighten-only policy the risk gate enforces.

Import the submodules directly (``from bot.guardian.flight_recorder import …``,
``from bot.guardian.intent_policy import …``). The package deliberately does NOT
eagerly re-export them: the risk engine imports ``intent_policy`` on its hot
path, and pulling the recorder's audit-chain/attestation dependencies in behind
it would widen the type-check/import graph for no benefit. Everything here is
telemetry-only and fail-open — nothing in this package may ever block, delay, or
alter a trade.
"""
