"""Guardian web sync — the bot→web flight-records payload carries the posture.

PR-8 extends the Flight Recorder sync to also push the read-only guardian_status
console snapshot so the dashboard can mirror the Telegram /guardian view. These
tests pin that the new field rides the existing POST body (backward-compatibly)
and that an all-empty call is still a no-op.
"""
import bot.utils.website_sync as ws


def _capture(monkeypatch):
    calls = []
    monkeypatch.setattr(ws, "_post", lambda path, body: (calls.append((path, body)) or {"ok": True, "stored": 0}))
    return calls


def test_guardian_status_rides_the_flight_post(monkeypatch):
    calls = _capture(monkeypatch)
    gs = {"posture": "high", "flags": {"firewall": True}, "twin": {"risk": "high"}}
    ok = ws.sync_flight_records([{"decision_id": "d1"}], {"ok": True}, {"policy_id": "p"}, gs)
    assert ok is True
    assert len(calls) == 1
    path, body = calls[0]
    assert path == "/api/bot/sync/flight"
    assert body["guardian_status"] == gs            # the new field is in the payload
    assert body["policy"] == {"policy_id": "p"}     # existing fields unchanged


def test_guardian_status_defaults_to_none(monkeypatch):
    calls = _capture(monkeypatch)
    ws.sync_flight_records([{"decision_id": "d1"}])
    _path, body = calls[0]
    assert body["guardian_status"] is None          # backward-compatible default


def test_all_empty_is_still_a_noop(monkeypatch):
    calls = _capture(monkeypatch)
    assert ws.sync_flight_records([], None, None, None) is True
    assert calls == []                              # nothing to send → no POST


def test_posture_alone_triggers_a_send(monkeypatch):
    # Even with no records/chain/policy, a posture snapshot is worth syncing.
    calls = _capture(monkeypatch)
    ws.sync_flight_records([], None, None, {"posture": "medium"})
    assert len(calls) == 1
    assert calls[0][1]["guardian_status"] == {"posture": "medium"}
