"""
Funding-arb paper tracker — evidence before capital, and strictly paper.

Pins the carry accrual math (pro-rata spread on fixed notional), the
observed-time-only rule (gaps break the position, nothing is extrapolated),
the entry threshold, entry counting, snapshot persistence, and the report
render including the fee reality check.
"""

import json
from datetime import datetime, timedelta

from bot.compat import UTC
import bot.core.arb_tracker as at
from bot.core.arb_tracker import (
    PAPER_NOTIONAL_USD,
    compute_paper_carry,
    format_arb_html,
    load_snapshots,
    snapshot_opportunities,
)

T0 = datetime(2026, 7, 16, 0, 0, tzinfo=UTC)


def _snap(base, ts, spread, long_v="bitget", short_v="hyperliquid"):
    return {"ts": ts.isoformat(), "base": base, "spread_apr": spread,
            "long_venue": long_v, "short_venue": short_v,
            "rates": {long_v: 0.0, short_v: spread}}


def test_carry_accrues_pro_rata_on_notional():
    # 8.76%/yr on $1000 = $87.60/yr = exactly $0.01/hour.
    snaps = [_snap("BTC", T0 + timedelta(hours=i), 8.76) for i in range(11)]
    (pc,) = compute_paper_carry(snaps, min_spread_apr=3.0)
    assert pc.base == "BTC"
    assert abs(pc.earned_usd - 0.10) < 1e-9        # 10 observed hours
    assert pc.held_hours == 10 and pc.entries == 1


def test_gaps_break_the_position_and_earn_nothing():
    snaps = [
        _snap("ETH", T0, 20.0),
        _snap("ETH", T0 + timedelta(hours=1), 20.0),
        # 12h unobserved gap — must NOT be credited.
        _snap("ETH", T0 + timedelta(hours=13), 20.0),
        _snap("ETH", T0 + timedelta(hours=14), 20.0),
    ]
    (pc,) = compute_paper_carry(snaps, min_spread_apr=3.0)
    assert pc.held_hours == 2                      # 2x 1h intervals only
    assert pc.entries == 2                         # gap forced a re-entry
    expected = PAPER_NOTIONAL_USD * 0.20 * (2 / (24 * 365))
    assert abs(pc.earned_usd - expected) < 1e-9


def test_sub_threshold_intervals_are_flat():
    snaps = [
        _snap("SOL", T0, 1.0),                      # below threshold -> flat
        _snap("SOL", T0 + timedelta(hours=1), 10.0),
        _snap("SOL", T0 + timedelta(hours=2), 10.0),
    ]
    (pc,) = compute_paper_carry(snaps, min_spread_apr=3.0)
    assert pc.held_hours == 1 and pc.entries == 1
    assert pc.observed_hours == 2                   # flat time still observed


def test_snapshot_writes_rows_via_injected_comparison(monkeypatch, tmp_path):
    from bot.core.funding_radar import FundingRow
    monkeypatch.setattr(at, "_RECORD_FILE", tmp_path / "arb.jsonl")
    monkeypatch.setattr(
        "bot.core.funding_radar.build_comparison",
        lambda bases: [FundingRow(base="BTC", rates={"a": 1.0, "b": 9.0},
                                  spread_apr=8.0, long_venue="a",
                                  short_venue="b")])
    assert snapshot_opportunities(["BTC"]) == 1
    rows = load_snapshots(tmp_path / "arb.jsonl")
    assert rows[0]["base"] == "BTC" and rows[0]["spread_apr"] == 8.0
    # Failure path returns 0, never raises.
    monkeypatch.setattr("bot.core.funding_radar.build_comparison",
                        lambda bases: (_ for _ in ()).throw(RuntimeError("x")))
    assert snapshot_opportunities(["BTC"]) == 0


def test_report_renders_fee_reality_check():
    snaps = [_snap("BTC", T0 + timedelta(hours=i), 8.76) for i in range(25)]
    html = format_arb_html(compute_paper_carry(snaps, min_spread_apr=3.0))
    assert "paper tracker" in html.lower()
    assert "BTC" in html and "Fee reality check" in html
    assert "never places orders" in html
    # Empty state still renders and explains how tracking starts.
    assert "ARB_TRACKER_ENABLED" in format_arb_html([])


def test_monitor_check_paces_hourly(monkeypatch):
    import asyncio
    import types
    from bot.core.proactive_monitor import ProactiveMonitor
    monkeypatch.setenv("ARB_TRACKER_ENABLED", "true")
    m = ProactiveMonitor(types.SimpleNamespace())
    spawned = []

    async def main():
        loop = asyncio.get_running_loop()
        orig = loop.create_task

        def counting(coro):
            spawned.append(1)
            coro.close()           # don't actually run the network task
            return orig(asyncio.sleep(0))
        loop.create_task = counting
        try:
            m._check_arb_tracker()          # first call spawns
            m._check_arb_tracker()          # within the hour -> paced out
        finally:
            loop.create_task = orig
    asyncio.run(main())
    assert len(spawned) == 1


def test_monitor_check_disabled_by_env(monkeypatch):
    import asyncio
    import types
    from bot.core.proactive_monitor import ProactiveMonitor
    monkeypatch.setenv("ARB_TRACKER_ENABLED", "false")
    m = ProactiveMonitor(types.SimpleNamespace())

    async def main():
        assert m._check_arb_tracker() == []
        assert m._last_arb_snapshot == 0.0   # never even started pacing
    asyncio.run(main())


def test_snapshot_lines_are_valid_jsonl(monkeypatch, tmp_path):
    from bot.core.funding_radar import FundingRow
    monkeypatch.setattr(at, "_RECORD_FILE", tmp_path / "arb.jsonl")
    monkeypatch.setattr(
        "bot.core.funding_radar.build_comparison",
        lambda bases: [FundingRow(base=b, rates={"a": 0.0, "b": 5.0},
                                  spread_apr=5.0, long_venue="a",
                                  short_venue="b") for b in ("BTC", "ETH")])
    assert snapshot_opportunities() == 2
    assert snapshot_opportunities() == 2       # appends, second batch
    raw = (tmp_path / "arb.jsonl").read_text().splitlines()
    assert len(raw) == 4
    for line in raw:
        json.loads(line)
