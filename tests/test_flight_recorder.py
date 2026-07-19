"""Guardian Flight Recorder — provenance-complete records over the audit chain.

The recorder is pure and fail-open: it widens what the engine seals (votes,
explainability, provenance), links a decision to its outcome, and reads the
chain back as joined flight records. These tests pin the record shape, the
DECISION↔OUTCOME join, tamper detection, and the fail-open contract (garbage in
never raises).
"""
import math

import pytest

from bot.guardian import flight_recorder as fr
from bot.utils.audit_chain import AuditChain, DecisionRecord


# ── lightweight stand-ins (no engine/analyzer import needed) ──────────

class _Enum:
    def __init__(self, value):
        self.value = value


class _Idea:
    """Minimal TradeIdea-shaped object with the provenance attrs the recorder reads."""
    def __init__(self):
        self.id = "TI-abc12345"
        self.asset = "BTC/USDT"
        self.direction = _Enum("LONG")
        self.entry_price = 65000.0
        self.stop_loss = 63000.0
        self.take_profit = 69000.0
        self.confidence = 0.72
        self.blended_confidence_raw = 0.68
        self.reasoning = "Trend continuation with MTF alignment " * 20  # long → trimmed
        self.signals_used = ["ema_cross", "vwap_reclaim", "bos"]
        self.htf_trend = "bullish"
        self.source = "auto"
        self.strategy_type = "swing"
        self.signal_type = "momentum_confluence"
        self.timeframe = "4h"
        self.llm_confidence = 0.7
        self.confluence_score = 0.66
        self.model_provider = "runeclaw-1"
        self.prompt_hash = "deadbeefcafe1234"
        self.analysis_version = "v7"
        self.data_bars = 240
        self.data_thin = False
        self._confluence_votes = [
            ("ema_cross", 1.0, 0.3),      # contribution 0.30
            ("rsi", -0.3, 0.1),           # contribution -0.03 (bearish)
            ("vwap_reclaim", 0.8, 0.5),   # contribution 0.40 → top
            ("noise", 0.0, 0.0),          # zero impact → dropped
        ]
        self._explain_report = {
            "top_bullish": ["EMA cross up", "VWAP reclaim", "BOS confirmed"],
            "top_bearish": ["RSI cooling"],
            "factors": [{"name": "vwap_reclaim", "contribution_pct": 41.2, "direction": "bullish"}],
            "compliance": {"overall": 0.83},
            "summary": "Bullish confluence across MTF.",
        }

    @property
    def risk_reward_ratio(self):
        return round(abs(self.take_profit - self.entry_price) / abs(self.entry_price - self.stop_loss), 2)


class _Risk:
    def __init__(self, verdict="APPROVED"):
        self.verdict = _Enum(verdict)
        self.position_size_usd = 250.0
        self.position_pct = 0.05
        self.daily_loss_pct = 0.01
        self.drawdown_pct = 0.02
        self.checks_passed = [f"check_{i}" for i in range(21)]
        self.checks_failed = []
        self.reason = ""


class _Pos:
    def __init__(self):
        self.trade_id = "TI-abc12345"
        self.symbol = "BTC/USDT"
        self.direction = _Enum("LONG")
        self.pnl_usd = 42.5
        self.exit_price = 67000.0
        self.entry_price = 65000.0
        self.close_reason = "TP_HIT"


# ── idea payload ──────────────────────────────────────────────────────

def test_idea_payload_is_provenance_complete_and_bounded():
    p = fr.decision_idea_payload(_Idea())
    assert p["direction"] == "LONG"
    assert p["confidence"] == 0.72
    assert p["entry"] == 65000.0 and p["sl"] == 63000.0 and p["tp"] == 69000.0
    assert p["rr"] == 2.0
    # reasoning is trimmed to the bound
    assert len(p["reasoning"]) <= fr._REASONING_MAX
    # provenance carried through
    prov = p["provenance"]
    assert prov["model_provider"] == "runeclaw-1"
    assert prov["prompt_hash"] == "deadbeefcafe1234"
    assert prov["analysis_version"] == "v7"
    assert prov["data_bars"] == 240 and prov["data_thin"] is False
    # votes ranked by |contribution|, zero-impact vote dropped, top is vwap (0.8*0.5=0.40)
    votes = p["votes"]
    assert votes[0]["name"] == "vwap_reclaim"
    assert votes[0]["direction"] == "bullish"
    assert all(v["name"] != "noise" for v in votes)  # 0-contribution dropped
    bearish = [v for v in votes if v["direction"] == "bearish"]
    assert bearish and bearish[0]["name"] == "rsi"
    # explainability slice
    assert p["explain"]["top_bullish"][0] == "EMA cross up"
    assert p["explain"]["compliance"] == 0.83


def test_risk_payload_summarises_verdict():
    r = fr.decision_risk_payload(_Risk("APPROVED"), size_usd=250.0)
    assert r["verdict"] == "APPROVED"
    assert r["passed"] == 21 and r["failed"] == 0
    assert r["size_usd"] == 250.0
    rej = fr.decision_risk_payload(_Risk("REJECTED"))
    assert rej["verdict"] == "REJECTED"


def test_outcome_payload_keys_to_decision():
    o = fr.outcome_event_payload(_Pos())
    assert o["decision_id"] == "TI-abc12345"
    assert o["pnl_usd"] == 42.5
    assert o["exit_price"] == 67000.0
    assert o["close_reason"] == "TP_HIT"


# ── fail-open contract ────────────────────────────────────────────────

def test_builders_never_raise_on_garbage():
    class Bomb:
        def __getattr__(self, k):
            raise RuntimeError("boom")
    # None, empty, and exploding objects all yield a dict, never an exception.
    for bad in (None, object(), {}, Bomb()):
        assert isinstance(fr.decision_idea_payload(bad), dict)
        assert isinstance(fr.decision_risk_payload(bad), dict)
        assert isinstance(fr.outcome_event_payload(bad), dict)
    assert fr.assemble_flight_records(None) == []
    assert fr.assemble_flight_records([]) == []


def test_nan_and_inf_prices_are_dropped_not_serialised():
    idea = _Idea()
    idea.entry_price = float("nan")
    idea.confidence = float("inf")
    p = fr.decision_idea_payload(idea)
    assert p["entry"] is None
    assert p["confidence"] is None


# ── join + chain integrity over a real AuditChain ─────────────────────

def test_assemble_joins_decision_to_outcome_over_real_chain(tmp_path):
    chain = AuditChain(str(tmp_path / "chain.jsonl"))
    idea, risk = _Idea(), _Risk("APPROVED")
    chain.seal_decision(DecisionRecord(
        decision_id="TI-abc12345", symbol="BTC/USDT",
        idea=fr.decision_idea_payload(idea),
        risk=fr.decision_risk_payload(risk, size_usd=250.0),
        outcome="EXECUTED_LIVE", is_paper=False,
    ))
    # An unrelated decision with no outcome yet.
    chain.append("DECISION", {
        "decision_id": "TI-ffff0000", "symbol": "ETH/USDT",
        "idea": {"direction": "SHORT"}, "risk": {"verdict": "APPROVED"},
        "outcome": "EXECUTED_LIVE", "is_paper": False,
        "timestamp": "2026-07-19T00:00:00+00:00",
    })
    # Outcome for the first decision, appended later.
    chain.append("OUTCOME", fr.outcome_event_payload(_Pos()))

    records = fr.assemble_flight_records(chain.get_entries(limit=100))
    assert len(records) == 2
    # newest-first: the ETH decision (sealed second) comes first
    assert records[0]["symbol"] == "ETH/USDT"
    assert records[0]["result"] is None  # no outcome yet
    btc = records[1]
    assert btc["symbol"] == "BTC/USDT"
    assert btc["outcome"] == "EXECUTED_LIVE"
    assert btc["result"]["pnl_usd"] == 42.5     # joined by decision_id
    assert btc["idea"]["provenance"]["model_provider"] == "runeclaw-1"
    assert btc["chain"]["entry_hash"]           # chain fields ride along


def test_verify_entries_detects_tamper(tmp_path):
    chain = AuditChain(str(tmp_path / "chain.jsonl"))
    for i in range(3):
        chain.append("DECISION", {"decision_id": f"TI-{i}", "symbol": "BTC/USDT"})
    entries = [e.to_dict() for e in chain.get_entries(limit=100)]
    ok, problems = fr.verify_entries(entries)
    assert ok and problems == []
    # Tamper with a payload — entry_hash no longer matches.
    entries[1]["payload"]["symbol"] = "ETH/USDT"
    ok2, problems2 = fr.verify_entries(entries)
    assert not ok2
    assert any("tampered" in p or "mismatch" in p for p in problems2)


def test_verify_entries_detects_reorder(tmp_path):
    chain = AuditChain(str(tmp_path / "chain.jsonl"))
    for i in range(3):
        chain.append("DECISION", {"decision_id": f"TI-{i}"})
    entries = [e.to_dict() for e in chain.get_entries(limit=100)]
    entries[1], entries[2] = entries[2], entries[1]  # swap → linkage breaks
    ok, problems = fr.verify_entries(entries)
    assert not ok and problems
