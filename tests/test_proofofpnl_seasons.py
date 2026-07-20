"""Verifiable seasons (community C5) — frozen calendar-month standings.

Hard lines under test: a season freezes only statements SEALED inside its
window (a stale registry entry from last month never enters); past seasons
are immutable; standings rank through the SAME re-verify-or-exclude,
size-agnostic path as the live board (a tampered frozen bundle is dropped
at read time, and no dollar magnitude appears in a season row).
"""
import json

from bot.proofofpnl import seasons as sn
from bot.proofofpnl.assemble import assemble_track_record
from bot.proofofpnl.publish import build_publication

# 2026-07-15 12:00 UTC — squarely inside season 2026-07.
_NOW = 1_784_116_800
_JULY = "2026-07"


def _trade(tid, side, price, ts):
    return {"id": tid, "order": tid, "symbol": "BTC/USDT:USDT", "side": side,
            "price": price, "amount": 1.0, "timestamp": ts,
            "fee": {"cost": 0.0, "currency": "USDT"}}


def _publication(published_at):
    trades = [_trade("a1", "buy", 100.0, 1_700_000_001_000),
              _trade("a2", "sell", 120.0, 1_700_000_002_000)]
    bundle = assemble_track_record(trades, account_ids=["h"],
                                   range_start=published_at - 100000,
                                   range_end=published_at)
    return build_publication(bundle, published_at_ts=published_at)


def _store(tmp_path):
    return sn.SeasonStore(str(tmp_path / "seasons.json"))


def test_season_math_including_year_boundary():
    assert sn.season_id_for(_NOW) == _JULY
    start, end = sn.season_window(_JULY)
    assert start <= _NOW < end
    dec = sn.season_window("2026-12")
    jan = sn.season_window("2027-01")
    assert dec[1] == jan[0], "December ends exactly where January begins"
    for bad in ("2026-13", "2026-00", "junk", "", "2026-7", "9999-01"):
        assert sn.season_window(bad) is None, bad


def test_freezes_only_statements_sealed_in_window(tmp_path):
    store = _store(tmp_path)
    in_window = _publication(_NOW - 3600)
    stale = _publication(_NOW - 45 * 86400)      # sealed back in May/June
    n = store.record_current(
        [{"handle": "fresh", "publication": in_window},
         {"handle": "stale", "publication": stale}], _NOW)
    assert n == 1
    rows = store.ranked(_JULY)
    assert [r["handle"] for r in rows] == ["fresh"]


def test_upsert_keeps_the_latest_in_window_seal(tmp_path):
    store = _store(tmp_path)
    early = _publication(_NOW - 7200)
    late = _publication(_NOW - 60)
    store.record_current([{"handle": "h", "publication": early}], _NOW)
    store.record_current([{"handle": "h", "publication": late}], _NOW)
    rows = store.ranked(_JULY)
    assert len(rows) == 1
    assert rows[0]["published_at"] == late["published_at"]


def test_past_seasons_are_immutable(tmp_path):
    store = _store(tmp_path)
    july_pub = _publication(_NOW - 3600)
    store.record_current([{"handle": "h", "publication": july_pub}], _NOW)
    july_before = store.ranked(_JULY)
    # A month later, a fresh statement lands: it must enter 2026-08 only.
    aug_now = _NOW + 31 * 86400
    aug_pub = _publication(aug_now - 60)
    store.record_current([{"handle": "h", "publication": aug_pub}], aug_now)
    assert store.ranked(_JULY) == july_before, "closed season never changes"
    assert [r["handle"] for r in store.ranked("2026-08")] == ["h"]
    assert store.season_ids() == ["2026-08", _JULY]


def test_tampered_frozen_publication_is_excluded_at_read(tmp_path):
    store = _store(tmp_path)
    pub = _publication(_NOW - 3600)
    store.record_current([{"handle": "h", "publication": pub}], _NOW)
    # Tamper the frozen bundle on disk — read-time re-verification must drop it.
    path = str(tmp_path / "seasons.json")
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    data[_JULY]["h"]["bundle"]["statement"]["metrics"]["pf"] = "999"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    assert store.ranked(_JULY) == []


def test_season_rows_carry_no_dollar_magnitudes(tmp_path):
    store = _store(tmp_path)
    store.record_current([{"handle": "h", "publication": _publication(_NOW - 60)}], _NOW)
    row = store.ranked(_JULY)[0]
    for leaky in ("net_pnl", "fees", "funding", "max_dd", "balance"):
        assert leaky not in row
    assert row["publish_hash"]


def test_gateway_payload_serves_seasons(tmp_path, monkeypatch):
    monkeypatch.setenv("PROOFOFPNL_SEASONS_PATH", str(tmp_path / "s.json"))
    sn.reset_season_store()
    try:
        sn.get_season_store().record_current(
            [{"handle": "h", "publication": _publication(_NOW - 60)}], _NOW)
        from bot.web.user_gateway import _leaderboard_payload
        p = _leaderboard_payload(_JULY)
        assert p["season"] == _JULY
        assert [r["handle"] for r in p["rows"]] == ["h"]
        assert _JULY in p["seasons"]
        # Live payload advertises the seasons list without a season key.
        live = _leaderboard_payload("")
        assert "season" not in live and _JULY in live["seasons"]
        # Unknown season: empty board, never an error.
        assert _leaderboard_payload("2031-01")["rows"] == []
    finally:
        sn.reset_season_store()


def test_engine_wires_the_snapshot_tick():
    import inspect
    from bot.core.engine import RuneClawEngine
    assert "_maybe_snapshot_board_season" in inspect.getsource(RuneClawEngine.run)
    src = inspect.getsource(RuneClawEngine._maybe_snapshot_board_season)
    assert "feature_enabled" in src and "record_current" in src
