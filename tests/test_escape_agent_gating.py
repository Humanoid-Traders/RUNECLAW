"""Guardian Escape Agent — the engine-level gating + recording contract.

``RuneClawEngine.run_escape_agent`` bridges the pure exit planner and the
tamper-evident chain. Tested against a minimal fake ``self`` (a full engine is
heavy): it always returns the read-only plan, but only *seals* an ESCAPE event
when the flag is on. It plans only — it never closes anything.
"""
import types

import bot.core.engine as engine_mod
from bot.core.engine import RuneClawEngine


class _Recorder:
    def __init__(self):
        self.events = []

    def append(self, event_type, payload, actor=""):
        self.events.append((event_type, payload, actor))


_BOOK = [
    {"symbol": "BTCUSDT", "direction": "LONG", "entry": 100.0, "qty": 5.0,
     "leverage": 20, "group": "BTC", "cost_usd": 25.0},
    {"symbol": "ETHUSDT", "direction": "SHORT", "entry": 100.0, "qty": 1.0,
     "leverage": 3, "group": "ETH", "cost_usd": 33.3},
]


class _FakeEngine:
    def __init__(self, positions):
        self._positions = positions
        self.audit_chain = _Recorder()

    def _twin_positions(self, user_id=""):
        return self._positions

    run_escape_agent = RuneClawEngine.run_escape_agent


def _set_flag(monkeypatch, on):
    fake = types.SimpleNamespace(risk=types.SimpleNamespace(guardian_escape_enabled=on))
    monkeypatch.setattr(engine_mod, "CONFIG", fake)


def test_no_positions_is_a_noop(monkeypatch):
    _set_flag(monkeypatch, True)
    eng = _FakeEngine([])
    assert eng.run_escape_agent() is None
    assert eng.audit_chain.events == []


def test_flag_off_previews_but_records_nothing(monkeypatch):
    _set_flag(monkeypatch, False)
    eng = _FakeEngine(_BOOK)
    report = eng.run_escape_agent()
    assert report is not None and report["steps"]
    # The 20x BTC is the most dangerous → planned first.
    assert report["steps"][0]["symbol"] == "BTCUSDT"
    assert eng.audit_chain.events == []


def test_flag_on_seals_one_escape_event(monkeypatch):
    _set_flag(monkeypatch, True)
    eng = _FakeEngine(_BOOK)
    report = eng.run_escape_agent()
    assert report is not None and report["steps"]
    assert len(eng.audit_chain.events) == 1
    etype, payload, actor = eng.audit_chain.events[0]
    assert etype == "ESCAPE"
    assert payload["position_count"] == 2 and actor == "operator"
    assert payload["order"][0]["symbol"] == "BTCUSDT"


def test_recorder_fault_never_raises(monkeypatch):
    _set_flag(monkeypatch, True)

    class _Boom:
        def append(self, *a, **k):
            raise RuntimeError("chain unavailable")

    eng = _FakeEngine(_BOOK)
    eng.audit_chain = _Boom()
    report = eng.run_escape_agent()
    assert report is not None and report["steps"]
