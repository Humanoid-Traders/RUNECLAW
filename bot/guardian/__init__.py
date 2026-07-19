"""RUNECLAW Guardian — the safety, control, evidence, and recovery layer for
autonomous crypto capital.

    The AI proposes. Deterministic controls authorize. The wallet enforces.
    The recorder proves. The escape agent recovers.

Modules land incrementally. The first is the Flight Recorder: a
provenance-complete, tamper-evident record of every trading decision, built as a
thin layer over the engine's existing hash-chained audit log. Everything here is
telemetry-only and fail-open — nothing in this package may ever block, delay, or
alter a trade.
"""

from bot.guardian.flight_recorder import (
    decision_idea_payload,
    decision_risk_payload,
    outcome_event_payload,
    assemble_flight_records,
    verify_entries,
)

__all__ = [
    "decision_idea_payload",
    "decision_risk_payload",
    "outcome_event_payload",
    "assemble_flight_records",
    "verify_entries",
]
