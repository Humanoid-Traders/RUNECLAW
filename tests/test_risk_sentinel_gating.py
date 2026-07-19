"""Guardian Risk Sentinel — the engine-level gating + recording contract.

``RuneClawEngine.run_risk_sentinel`` bridges the pure crowding detector and the
tamper-evident chain. Tested against a minimal fake ``self`` (a full engine is
heavy): it always returns the read-only assessment, but only *seals* a SENTINEL
event when the flag is on.
"""
import types

import bot.core.engine as engine_mod
from bot.core.engine import RuneClawEngine


class _Recorder:
    def __init__(self):
        self.events = []

    def append(self, event_type, payload, actor=""):
        self.events.append((event_type, payload, actor))


# A crowded book: two longs, same correlation group → concentration high.
_CROWDED = [
    {"symbol": "BTCUSDT", "direction": "LONG", "entry": 100.0, "qty": 1.0,
     "leverage": 5, "group": "BTC"},
    {"symbol": "ETHUSDT", "direction": "LONG", "entry": 100.0, "qty": 1.0,
     "leverage": 5, "group": "BTC"},
]


class _FakeEngine:
    def __init__(self, positions):
        self._positions = positions
        self.audit_chain = _Recorder()

    def _twin_positions(self, user_id=""):
        return self._positions

    run_risk_sentinel = RuneClawEngine.run_risk_sentinel


def _set_flag(monkeypatch, on):
    fake = types.SimpleNamespace(risk=types.SimpleNamespace(guardian_risk_sentinel_enabled=on))
    monkeypatch.setattr(engine_mod, "CONFIG", fake)


def test_no_positions_is_a_noop(monkeypatch):
    _set_flag(monkeypatch, True)
    eng = _FakeEngine([])
    assert eng.run_risk_sentinel() is None
    assert eng.audit_chain.events == []


def test_flag_off_previews_but_records_nothing(monkeypatch):
    _set_flag(monkeypatch, False)
    eng = _FakeEngine(_CROWDED)
    report = eng.run_risk_sentinel()
    assert report is not None and report["risk"] == "high"
    assert eng.audit_chain.events == []


def test_flag_on_seals_one_sentinel_event(monkeypatch):
    _set_flag(monkeypatch, True)
    eng = _FakeEngine(_CROWDED)
    report = eng.run_risk_sentinel()
    assert report is not None and report["risk"] == "high"
    assert len(eng.audit_chain.events) == 1
    etype, payload, actor = eng.audit_chain.events[0]
    assert etype == "SENTINEL"
    assert payload["risk"] == "high" and actor == "operator"


def test_recorder_fault_never_raises(monkeypatch):
    _set_flag(monkeypatch, True)

    class _Boom:
        def append(self, *a, **k):
            raise RuntimeError("chain unavailable")

    eng = _FakeEngine(_CROWDED)
    eng.audit_chain = _Boom()
    report = eng.run_risk_sentinel()
    assert report is not None and report["risk"] == "high"
