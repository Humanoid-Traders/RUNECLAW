"""Public verifiable leaderboard — LB1-LB8.

Anonymous, opted-in agents ranked by SIZE-AGNOSTIC fills-derived metrics
(profit factor primary), each row independently re-verifiable. Tampered or
unsafe publications are dropped; no dollar magnitude ever appears.
"""
from bot.proofofpnl import leaderboard as lb
from bot.proofofpnl.assemble import assemble_track_record
from bot.proofofpnl.publish import build_publication


def _trade(tid, side, price, ts):
    return {"id": tid, "order": tid, "symbol": "BTC/USDT:USDT", "side": side,
            "price": price, "amount": 1.0, "timestamp": ts,
            "fee": {"cost": 0.0, "currency": "USDT"}}


def _publication(trades, *, ts=1_700_000_000):
    bundle = assemble_track_record(trades, account_ids=["acct"],
                                   range_start=ts - 100000, range_end=ts)
    return build_publication(bundle, published_at_ts=ts)


# A member with profit factor ~1.0 (one +10 round-trip, one -10).
def _pf1_trades():
    return [_trade("a1", "buy", 100.0, 1_700_000_001_000),
            _trade("a2", "sell", 110.0, 1_700_000_002_000),
            _trade("a3", "buy", 100.0, 1_700_000_003_000),
            _trade("a4", "sell", 90.0, 1_700_000_004_000)]


# A member with profit factor ~4.0 (one +20 round-trip, one -5).
def _pf4_trades():
    return [_trade("b1", "buy", 100.0, 1_700_000_001_000),
            _trade("b2", "sell", 120.0, 1_700_000_002_000),
            _trade("b3", "buy", 100.0, 1_700_000_003_000),
            _trade("b4", "sell", 95.0, 1_700_000_004_000)]


def test_lb1_ranks_by_profit_factor_and_is_anonymous():
    entries = [{"handle": "alice", "publication": _publication(_pf1_trades())},
               {"handle": "bob", "publication": _publication(_pf4_trades())}]
    rows = lb.rank_entries(entries)
    assert [r["handle"] for r in rows] == ["bob", "alice"]   # higher PF first
    assert [r["rank"] for r in rows] == [1, 2]
    assert all(r["verified"] is True for r in rows)


def test_lb2_no_dollar_magnitude_ever_appears():
    rows = lb.rank_entries([{"handle": "alice", "publication": _publication(_pf1_trades())}])
    row = rows[0]
    for leaky in ("net_pnl", "fees", "funding", "max_dd", "balance"):
        assert leaky not in row, f"{leaky} must never surface on the board"
    # Only ratios / counts / verifiability fields are present.
    assert row["profit_factor"] is not None
    assert isinstance(row["round_trips"], int)
    assert row["publish_hash"]


def test_lb3_tampered_publication_is_excluded():
    pub = _publication(_pf1_trades())
    # Inflate the profit factor WITHOUT resealing — the hash no longer matches.
    pub["bundle"]["statement"]["metrics"]["pf"] = "999"
    rows = lb.rank_entries([{"handle": "cheater", "publication": pub}])
    assert rows == []                                # dropped on re-verification


def test_lb4_missing_or_duplicate_handles_are_dropped_and_deduped():
    p = _publication(_pf4_trades())
    entries = [{"handle": "", "publication": p},           # no handle -> drop
               {"handle": "dup", "publication": p},
               {"handle": "DUP", "publication": p}]         # case-insensitive dupe
    rows = lb.rank_entries(entries)
    assert len(rows) == 1 and rows[0]["handle"] == "dup"


def test_lb5_min_round_trips_filter():
    # A single unmatched buy => zero completed round-trips.
    thin = _publication([_trade("x1", "buy", 100.0, 1_700_000_001_000)])
    rows = lb.rank_entries([{"handle": "thin", "publication": thin}], min_round_trips=1)
    assert rows == []


def test_lb6_infinite_profit_factor_sorts_to_top():
    # No losing round-trip => pf 'inf'; must outrank a finite-PF member.
    flawless = _publication([_trade("f1", "buy", 100.0, 1_700_000_001_000),
                             _trade("f2", "sell", 130.0, 1_700_000_002_000)])
    entries = [{"handle": "finite", "publication": _publication(_pf4_trades())},
               {"handle": "flawless", "publication": flawless}]
    rows = lb.rank_entries(entries)
    assert rows[0]["handle"] == "flawless"
    assert rows[0]["profit_factor"] == "inf"


def test_lb7_registry_roundtrip_and_refusal(tmp_path):
    reg = lb.LeaderboardRegistry(path=str(tmp_path / "board.json"))
    good = _publication(_pf4_trades())
    assert reg.put("bob", good) is True
    assert reg.put("", good) is False                # empty handle refused
    # A tampered publication is refused at the registry boundary too.
    bad = _publication(_pf1_trades())
    bad["bundle"]["statement"]["metrics"]["pf"] = "999"
    assert reg.put("cheater", bad) is False
    entries = reg.all_entries()
    assert [e["handle"] for e in entries] == ["bob"]
    ranked = reg.ranked()
    assert ranked and ranked[0]["handle"] == "bob"
    assert reg.remove("bob") is True
    assert reg.all_entries() == []


def test_lb8_registry_singleton_env_path(monkeypatch, tmp_path):
    lb.reset_leaderboard_registry()
    monkeypatch.setenv("PROOFOFPNL_LEADERBOARD_PATH", str(tmp_path / "s.json"))
    r1 = lb.get_leaderboard_registry()
    r2 = lb.get_leaderboard_registry()
    assert r1 is r2
    lb.reset_leaderboard_registry()
