"""Guardian console — the unified read-only status aggregator.

``RuneClawEngine.guardian_status`` composes the six Guardian modules into one
safety-posture snapshot. The critical contract: it is a PURE READ — viewing the
console must never seal a chain event (unlike the /twin, /sentinel, /escape
helpers). Tested against a minimal fake ``self`` (a full engine is heavy).
"""
import types

import bot.core.engine as engine_mod
from bot.core.engine import RuneClawEngine


class _Recorder:
    def __init__(self):
        self.events = []

    def append(self, event_type, payload, actor=""):
        self.events.append((event_type, payload, actor))

    # audit_chain surface the status reads
    def get_chain_length(self):
        return 7

    def get_entries(self, limit=1):
        return []

    _path = "/tmp/does-not-exist-guardian-console.jsonl"

    @staticmethod
    def verify(path):
        return True, []


# A dangerous book: a 20x long → high twin/escape urgency, concentrated group.
_BOOK = [
    {"symbol": "BTCUSDT", "direction": "LONG", "entry": 100.0, "qty": 5.0,
     "leverage": 20, "group": "BTC", "cost_usd": 25.0},
]


class _FakeEngine:
    def __init__(self, positions):
        self._positions = positions
        self.audit_chain = _Recorder()

    def _twin_positions(self, user_id=""):
        return self._positions

    def get_effective_equity(self, user_id=""):
        return 100.0

    def _intent_policy_summary(self):
        return None

    guardian_status = RuneClawEngine.guardian_status


def _flags(monkeypatch, **vals):
    risk = types.SimpleNamespace(
        intent_policy_enabled=vals.get("intent_policy", False),
        guardian_firewall_enabled=vals.get("firewall", False),
        guardian_firewall_block_high=vals.get("firewall_block", False),
        guardian_digital_twin_enabled=vals.get("digital_twin", False),
        guardian_risk_sentinel_enabled=vals.get("risk_sentinel", False),
        guardian_escape_enabled=vals.get("escape", False),
    )
    monkeypatch.setattr(engine_mod, "CONFIG", types.SimpleNamespace(risk=risk))


def test_status_is_a_pure_read_seals_nothing(monkeypatch):
    # Even with every flag armed, a status view writes no chain event.
    _flags(monkeypatch, firewall=True, digital_twin=True, risk_sentinel=True, escape=True)
    eng = _FakeEngine(_BOOK)
    s = eng.guardian_status()
    assert eng.audit_chain.events == []          # THE contract: no side effects


def test_status_reports_flags_and_chain(monkeypatch):
    _flags(monkeypatch, firewall=True, firewall_block=True)
    eng = _FakeEngine(_BOOK)
    s = eng.guardian_status()
    assert s["flags"]["firewall"] is True and s["flags"]["firewall_block"] is True
    assert s["flags"]["escape"] is False
    assert s["chain"]["length"] == 7 and s["chain"]["ok"] is True


def test_posture_is_worst_live_book_risk(monkeypatch):
    _flags(monkeypatch)
    eng = _FakeEngine(_BOOK)
    s = eng.guardian_status()
    # A 20x long is near liquidation → twin & escape both high → posture high.
    assert s["twin"]["risk"] == "high"
    assert s["escape"]["risk"] == "high"
    assert s["posture"] == "high"


def test_flat_book_is_calm(monkeypatch):
    _flags(monkeypatch)
    eng = _FakeEngine([])
    s = eng.guardian_status()
    assert s["posture"] == "none"
    assert s["twin"]["position_count"] == 0
    assert eng.audit_chain.events == []


def test_status_never_raises_on_broken_chain(monkeypatch):
    _flags(monkeypatch)

    class _BrokenChain:
        def get_chain_length(self):
            raise RuntimeError("io")

        def get_entries(self, limit=1):
            raise RuntimeError("io")

    eng = _FakeEngine(_BOOK)
    eng.audit_chain = _BrokenChain()
    s = eng.guardian_status()                    # degrades, never raises
    assert isinstance(s, dict) and s["chain"]["length"] == 0
