"""INCIDENT fix (feed resilience): a failed feed POST is RE-QUEUED for a
bounded number of retries instead of being silently dropped, so a transient
web outage doesn't lose the agent mind-stream. Retry bookkeeping never reaches
the wire.
"""

import bot.utils.website_sync as ws
from bot.core.agent_feed import MAX_RETRIES, AgentFeed


def test_failed_flush_requeues_until_retry_cap(monkeypatch):
    monkeypatch.setattr(ws, "sync_agent_events", lambda events: False)
    f = AgentFeed()
    f.emit("scan", "scanning the book")
    assert f.pending() == 1

    # Each failed flush re-queues the batch until the retry cap is reached.
    for _ in range(MAX_RETRIES):
        assert f.flush_once() == 0
        assert f.pending() == 1

    # The next flush exhausts the cap and drops the event.
    assert f.flush_once() == 0
    assert f.pending() == 0


def test_successful_flush_sends_clean_events_without_retry_marker(monkeypatch):
    captured = {}

    def fake_sync(events):
        captured["events"] = events
        return True

    monkeypatch.setattr(ws, "sync_agent_events", fake_sync)
    f = AgentFeed()
    f.emit("thesis", "long thesis on ETH")
    # Simulate a prior retry so we can prove the marker is stripped on the wire.
    f._queue[0]["_retries"] = 2

    assert f.flush_once() == 1
    assert f.pending() == 0
    assert captured["events"] and "_retries" not in captured["events"][0]
    assert captured["events"][0]["event_type"] == "thesis"


def test_recovered_web_after_failures_still_delivers(monkeypatch):
    state = {"up": False}
    monkeypatch.setattr(ws, "sync_agent_events",
                        lambda events: state["up"])
    f = AgentFeed()
    f.emit("scan", "cycle")
    assert f.flush_once() == 0      # web down -> re-queued
    assert f.pending() == 1
    state["up"] = True
    assert f.flush_once() == 1      # web back -> delivered, not lost
    assert f.pending() == 0
