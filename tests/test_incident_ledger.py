"""GUARDIAN-INCIDENTS: the incident ledger assembler.

assemble_incident_records mirrors the safety controls that ACTED — firewall
blocks, risk-gate rejections, escape recoveries, sentinel/twin flags — from the
sealed audit chain into a compact, web-safe list. Pure + fail-open, newest
first, and NEVER emits a dollar amount (§4). Routine clean checks (a firewall
that passed, a sentinel with no concern) are not incidents.
"""

import json

from bot.guardian.flight_recorder import assemble_incident_records


def _e(seq, etype, payload, ts="2026-07-22T00:00:00Z"):
    return {"sequence": seq, "event_type": etype, "timestamp": ts,
            "entry_hash": f"h{seq}", "prev_hash": f"h{seq-1}", "payload": payload}


def test_empty_and_junk_are_safe():
    assert assemble_incident_records(None) == []
    assert assemble_incident_records([]) == []
    assert assemble_incident_records([{"nope": 1}, 42, "x"]) == []


def test_firewall_block_and_clean_pass_filtering():
    entries = [
        _e(1, "FIREWALL", {"risk": "high", "action": "blocked injection", "symbol": "BTC"}),
        _e(2, "FIREWALL", {"risk": "none"}),   # clean pass → NOT an incident
    ]
    out = assemble_incident_records(entries)
    assert len(out) == 1
    assert out[0]["kind"] == "block"
    assert out[0]["category"] == "Prompt-injection firewall"
    assert out[0]["severity"] == "high"
    assert "blocked injection" in out[0]["detail"]


def test_escape_is_a_recovery_and_needs_action():
    acted = _e(3, "ESCAPE", {"risk": "high", "recommended": True,
                             "steps": [{"a": 1}, {"b": 2}]})
    idle = _e(4, "ESCAPE", {"risk": "none", "position_count": 0})  # nothing to unwind
    out = assemble_incident_records([acted, idle])
    assert len(out) == 1
    assert out[0]["kind"] == "recovery"


def test_risk_gate_rejection_from_decision():
    entries = [_e(5, "DECISION", {
        "outcome": "REJECTED_ON_RECHECK", "symbol": "SOL",
        "risk": {"reason": "DAILY_LOSS: cap hit", "checks_failed": ["DAILY_LOSS"]},
    })]
    out = assemble_incident_records(entries)
    assert len(out) == 1
    assert out[0]["kind"] == "block"
    assert out[0]["category"] == "Risk-gate rejection"
    assert "DAILY_LOSS" in out[0]["detail"]


def test_executed_decisions_are_not_incidents():
    entries = [_e(6, "DECISION", {"outcome": "EXECUTED_LIVE", "symbol": "ETH"})]
    assert assemble_incident_records(entries) == []


def test_newest_first_and_limit():
    entries = [_e(i, "SENTINEL", {"risk": "high", "top_group": f"grp{i}"}) for i in range(1, 11)]
    out = assemble_incident_records(entries, limit=3)
    assert len(out) == 3
    # newest first → highest sequence first
    seqs = [i["chain"]["sequence"] for i in out]
    assert seqs == sorted(seqs, reverse=True)


def test_never_emits_dollar_amounts():
    entries = [
        _e(1, "FIREWALL", {"risk": "high", "action": "blocked", "notional_usd": 5000}),
        _e(2, "ESCAPE", {"risk": "high", "recommended": True, "steps": [1]}),
        _e(3, "DECISION", {"outcome": "REJECTED_ON_RECHECK",
                           "risk": {"reason": "POSITION_SIZE", "size_usd": 999}}),
    ]
    blob = json.dumps(assemble_incident_records(entries))
    assert "$" not in blob
    assert "usd" not in blob.lower()
