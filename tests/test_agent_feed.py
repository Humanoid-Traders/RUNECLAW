"""Agent mind-stream feed emitter: bounded queue, sanitization, fail-soft flush.

The feed is public telemetry riding next to the money path, so the contract
under test is: emit() never raises, never blocks, bounds its memory, whitelists
event types, truncates strings, and a failed website POST just drops the batch.
"""

from __future__ import annotations

import bot.core.agent_feed as agent_feed_mod
from bot.core.agent_feed import (
    ALLOWED_TYPES, MAX_BATCH, MAX_QUEUE, AgentFeed, BODY_MAX, TITLE_MAX,
)


def _drain_all(feed: AgentFeed) -> list[dict]:
    out = []
    while True:
        batch = feed._drain()
        if not batch:
            return out
        out.extend(batch)


def test_emit_sanitizes_type_severity_and_truncates():
    feed = AgentFeed()
    feed.emit("not-a-real-type", "T" * (TITLE_MAX + 50),
              body="B" * (BODY_MAX + 50), severity="apocalyptic",
              symbol="X" * 99, data="not-a-dict")
    [ev] = _drain_all(feed)
    assert ev["event_type"] == "info"          # unknown type falls back
    assert ev["severity"] == "info"            # unknown severity falls back
    assert len(ev["title"]) == TITLE_MAX
    assert len(ev["body"]) == BODY_MAX
    assert len(ev["symbol"]) == 32
    assert ev["data"] == {}                    # non-dict data dropped
    assert ev["ts"]                            # stamped


def test_emit_requires_title_and_respects_env_gate(monkeypatch):
    feed = AgentFeed()
    feed.emit("scan", "")                      # no title -> dropped
    assert feed.pending() == 0
    monkeypatch.setenv("AGENT_FEED_ENABLED", "false")
    feed.emit("scan", "disabled")
    assert feed.pending() == 0
    monkeypatch.setenv("AGENT_FEED_ENABLED", "true")
    feed.emit("scan", "enabled")
    assert feed.pending() == 1


def test_queue_drops_oldest_beyond_cap():
    feed = AgentFeed()
    for i in range(MAX_QUEUE + 25):
        feed.emit("info", f"ev {i}")
    events = _drain_all(feed)
    assert len(events) == MAX_QUEUE
    assert events[0]["title"] == "ev 25"       # oldest 25 dropped
    assert events[-1]["title"] == f"ev {MAX_QUEUE + 24}"


def test_flush_once_batches_and_posts(monkeypatch):
    feed = AgentFeed()
    sent: list[list[dict]] = []
    monkeypatch.setattr("bot.utils.website_sync.sync_agent_events",
                        lambda evs: sent.append(list(evs)) or True)
    for i in range(MAX_BATCH + 5):
        feed.emit("scan", f"scan {i}")
    assert feed.flush_once() == MAX_BATCH      # one bounded batch per flush
    assert feed.flush_once() == 5              # remainder on the next pass
    assert feed.flush_once() == 0              # then a clean no-op
    assert [len(b) for b in sent] == [MAX_BATCH, 5]


def test_flush_failure_requeues_then_drops_without_raising(monkeypatch):
    # A failed POST now RE-QUEUES for a bounded number of retries (so a
    # transient web outage doesn't silently lose the feed), then drops — never
    # a hot retry-loop, never raising.
    from bot.core.agent_feed import MAX_RETRIES
    feed = AgentFeed()
    monkeypatch.setattr("bot.utils.website_sync.sync_agent_events",
                        lambda evs: False)
    feed.emit("alert", "website is down")
    for _ in range(MAX_RETRIES):
        assert feed.flush_once() == 0          # failed POST reports 0
        assert feed.pending() == 1             # ...and is re-queued for retry
    assert feed.flush_once() == 0              # retry cap hit
    assert feed.pending() == 0                 # ...then dropped, no loop


def test_allowed_types_match_web_whitelist():
    # The web route (app/routes/sync.js FEED_TYPES) must accept every type the
    # bot can emit — keep this frozen set in sync with it.
    assert ALLOWED_TYPES == {"scan", "thesis", "trade_open", "trade_close",
                             "sl_move", "alert", "stance", "info"}


def test_module_singleton_exists():
    assert isinstance(agent_feed_mod.FEED, AgentFeed)
