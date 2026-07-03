"""Seed macro calendar staleness must key on event COVERAGE, not file age.

A seed calendar of scheduled events (FOMC/CPI dates fixed a year ahead) does
not become wrong because the file's generated_utc is old — only when it stops
covering the forward horizon. The old logic marked ANY seed older than 72 h
stale and fail-closed the whole bot (BLOCK_NEW_ENTRIES on every trade), a
self-inflicted outage while the calendar still had valid events through
December. Now: aged-but-still-covering seed is trusted; only an exhausted
seed fails closed.
"""

import json
from datetime import datetime, timedelta

from bot.compat import UTC
from bot.core.macro_events import MacroEventProvider


def _seed(tmp_path, generated_days_ago, event_offsets_hours):
    now = datetime.now(UTC)
    gen = now - timedelta(days=generated_days_ago)
    events = [{"name": f"EV{i}", "scheduled_utc":
               (now + timedelta(hours=h)).isoformat().replace("+00:00", "Z"),
               "severity": "high"}
              for i, h in enumerate(event_offsets_hours)]
    p = tmp_path / "seed.json"
    p.write_text(json.dumps({"generated_utc": gen.isoformat().replace("+00:00", "Z"),
                             "events": events}))
    return p


class TestSeedCoverageStaleness:
    def test_aged_seed_with_forward_coverage_is_trusted(self, tmp_path):
        # 30-day-old file, but events 200h/500h out (past the 72h horizon).
        p = _seed(tmp_path, generated_days_ago=30, event_offsets_hours=[200, 500])
        prov = MacroEventProvider(seed_path=p, max_stale_hours=72)
        assert prov._calendar_stale is False
        assert prov._calendar_blind is False
        # And it does NOT block: a clear (non-event) window trades normally.
        ctx = prov.get_context(now=datetime.now(UTC) + timedelta(hours=50))
        assert ctx.risk_state != "BLOCK_NEW_ENTRIES"

    def test_aged_seed_without_forward_coverage_is_stale(self, tmp_path):
        # 30-day-old file whose only events are already in the past / inside
        # the horizon → no forward coverage → genuinely blind → fail-closed.
        p = _seed(tmp_path, generated_days_ago=30, event_offsets_hours=[-100, 10])
        prov = MacroEventProvider(seed_path=p, max_stale_hours=72, failsafe=True)
        assert prov._calendar_stale is True
        ctx = prov.get_context()
        assert ctx.risk_state == "BLOCK_NEW_ENTRIES"

    def test_fresh_seed_never_stale(self, tmp_path):
        p = _seed(tmp_path, generated_days_ago=1, event_offsets_hours=[10, 300])
        prov = MacroEventProvider(seed_path=p, max_stale_hours=72)
        assert prov._calendar_stale is False

    def test_failsafe_off_never_blocks_even_when_stale(self, tmp_path):
        p = _seed(tmp_path, generated_days_ago=30, event_offsets_hours=[-100])
        prov = MacroEventProvider(seed_path=p, max_stale_hours=72, failsafe=False)
        ctx = prov.get_context()
        assert ctx.risk_state != "BLOCK_NEW_ENTRIES"
