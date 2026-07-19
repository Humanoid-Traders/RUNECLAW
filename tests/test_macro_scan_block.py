"""Macro block on the scan sync payload (telemetry-only, fail-open).

`_macro_block()` serialises the macro calendar's risk-state + next event so the
website's Macro AI page can show a countdown / event-risk banner. It must be
JSON-serialisable (it rides the scan sync) and must NEVER raise — any failure
returns None and the block is simply omitted.
"""
import json

import bot.macro.calendar as cal
from bot.macro.models import MacroEventType, MacroRiskState
from bot.skills.scan_skill import _macro_block


def test_macro_block_shape_and_json_serializable():
    m = _macro_block()
    assert m is not None
    assert set(m.keys()) >= {
        "state", "stale", "next_event", "active_event",
        "seconds_until_next", "evaluated_at",
    }
    assert m["state"] in {s.value for s in MacroRiskState}
    assert isinstance(m["stale"], bool)
    # Rides the scan sync payload — must round-trip through JSON untouched.
    json.dumps(m)
    ev = m["next_event"] or m["active_event"]
    if ev is not None:
        assert set(ev.keys()) == {"type", "label", "scheduled_utc", "impact"}
        assert ev["type"] in {t.value for t in MacroEventType}
        # ISO-8601 UTC timestamp the browser can parse.
        assert ev["scheduled_utc"].endswith("+00:00") or ev["scheduled_utc"].endswith("Z")


def test_macro_block_fails_open(monkeypatch):
    """A broken calendar returns None (block omitted), never propagates."""
    class Boom:
        def evaluate(self):
            raise RuntimeError("calendar exploded")

    monkeypatch.setattr(cal, "MacroCalendar", lambda *a, **k: Boom())
    assert _macro_block() is None
