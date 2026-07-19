"""Explain-my-fill — pre-registered predictions E1–E6.

E1 headline reflects the decision; E2 why draws from the record's factors/voters;
E3 provenance surfaced; E4 outcome narrated when closed; E5 verification cites the
chain; E6 faithful + deterministic (no fabrication) + assembler attaches it.
"""
from bot.guardian.explain_fill import explain
from bot.guardian.flight_recorder import assemble_flight_records


def _record(**over):
    rec = {
        "decision_id": "d1", "symbol": "SOL/USDT", "outcome": "taken",
        "is_paper": False,
        "idea": {
            "direction": "LONG", "confidence": 0.72, "entry": 100, "sl": 97, "tp": 109,
            "rr": 3.0, "strategy_type": "breakout", "signal_type": "range_break",
            "reasoning": "Reclaimed the range high on rising volume.",
            "votes": [{"name": "vwap"}, {"name": "structure"}, {"name": "momentum"}],
            "explain": {"top_bullish": ["VWAP reclaim", "HH/HL structure"], "top_bearish": []},
            "provenance": {"model_provider": "runeclaw-1", "analysis_version": "v42",
                           "prompt_hash": "abcdef123456", "data_thin": False},
        },
        "result": None,
        "chain": {"sequence": 7, "entry_hash": "deadbeefcafe0001"},
    }
    rec.update(over)
    return rec


# ── E1 — headline ─────────────────────────────────────────────────────

def test_e1_headline_reflects_decision():
    e = explain(_record())
    assert "took" in e["headline"] and "live" in e["headline"]
    assert "LONG" in e["headline"] and "SOL" in e["headline"]
    assert "72%" in e["headline"]


def test_e1_rejected_and_paper():
    e = explain(_record(outcome="rejected", is_paper=True))
    assert "rejected" in e["headline"] and "paper" in e["headline"]


# ── E2 — why draws from the record ────────────────────────────────────

def test_e2_why_uses_factors_and_voters():
    e = explain(_record())
    joined = " ".join(e["why"])
    assert "VWAP reclaim" in joined            # top_bullish for a long
    assert "vwap" in joined and "structure" in joined   # top voters
    assert "Reward:risk 3" in joined
    assert "Reclaimed the range high" in joined


# ── E3 — provenance ───────────────────────────────────────────────────

def test_e3_provenance_surfaced():
    e = explain(_record())
    assert e["provenance"]["model"] == "runeclaw-1"
    assert e["provenance"]["analysis_version"] == "v42"
    assert e["provenance"]["prompt_hash"] == "abcdef123456"


# ── E4 — outcome ──────────────────────────────────────────────────────

def test_e4_win_outcome_narrated():
    e = explain(_record(result={"realized_pnl": 42.5, "exit_reason": "take_profit"}))
    assert e["outcome"]["closed"] is True
    assert e["outcome"]["pnl_usd"] == 42.5
    assert "won $42.50" in e["narrative"] and "take_profit" in e["narrative"]


def test_e4_loss_outcome_narrated():
    e = explain(_record(result={"realized_pnl": -18.0}))
    assert "lost $18.00" in e["narrative"]


def test_e4_open_has_no_outcome():
    e = explain(_record())
    assert e["outcome"] == {}
    assert "won" not in e["narrative"] and "lost" not in e["narrative"]


# ── E5 — verification cites the chain ─────────────────────────────────

def test_e5_verification_cites_chain():
    e = explain(_record())
    assert e["verification"]["sequence"] == 7
    assert e["verification"]["entry_hash"] == "deadbeefcaf"[:11] or \
        e["verification"]["entry_hash"] == "deadbeefcafe"[:12]
    assert "re-derivable" in e["verification"]["note"].lower()


# ── E6 — faithful, deterministic, and attached by the assembler ───────

def test_e6_deterministic_and_faithful():
    a = explain(_record())
    b = explain(_record())
    assert a == b
    # nothing invented: a record with no factors/votes yields no such lines
    bare = explain({"symbol": "BTC/USDT", "outcome": "taken",
                    "idea": {"direction": "LONG"}, "chain": {}})
    assert not any("voters" in w.lower() for w in bare["why"])


def test_e6_assembler_attaches_explanation():
    # assemble_flight_records should attach an 'explanation' per record.
    class _E:
        def __init__(self, d):
            self._d = d
        def to_dict(self):
            return self._d
    decision = {"event_type": "DECISION", "timestamp": "t", "sequence": 1,
                "entry_hash": "h1", "prev_hash": "g",
                "payload": {"decision_id": "d9", "symbol": "ETH/USDT",
                            "outcome": "taken", "is_paper": True,
                            "idea": {"direction": "SHORT", "confidence": 0.6}}}
    out = assemble_flight_records([decision])
    assert out and "explanation" in out[0]
    assert "SHORT" in out[0]["explanation"]["headline"]
