"""Leaderboard A2 — operator self-registration + public gateway serving.

The board goes live: the operator (opted in via PROOFOFPNL_LEADERBOARD_HANDLE)
registers its sealed statement, and the gateway serves the ranked, anonymous,
re-verifiable board with no auth.
"""
import inspect

from bot.proofofpnl import leaderboard as lb
from bot.proofofpnl.assemble import assemble_track_record
from bot.proofofpnl.publish import build_publication


def _trade(tid, side, price, ts):
    return {"id": tid, "order": tid, "symbol": "BTC/USDT:USDT", "side": side,
            "price": price, "amount": 1.0, "timestamp": ts,
            "fee": {"cost": 0.0, "currency": "USDT"}}


def _publication(ts=1_700_000_000):
    trades = [_trade("a1", "buy", 100.0, 1_700_000_001_000),
              _trade("a2", "sell", 120.0, 1_700_000_002_000),
              _trade("a3", "buy", 100.0, 1_700_000_003_000),
              _trade("a4", "sell", 95.0, 1_700_000_004_000)]
    bundle = assemble_track_record(trades, account_ids=["acct"],
                                   range_start=ts - 1000, range_end=ts)
    return build_publication(bundle, published_at_ts=ts)


def test_gateway_serves_ranked_anonymous_board(monkeypatch, tmp_path):
    monkeypatch.setenv("PROOFOFPNL_LEADERBOARD_PATH", str(tmp_path / "board.json"))
    lb.reset_leaderboard_registry()
    lb.get_leaderboard_registry().put("skywalker", _publication())

    from bot.web import user_gateway
    payload = user_gateway._leaderboard_payload()

    assert payload["format"] == "runeclaw.proofofpnl.leaderboard.v0"
    assert payload["count"] == 1
    row = payload["rows"][0]
    assert row["handle"] == "skywalker" and row["rank"] == 1 and row["verified"] is True
    # Anonymous + size-agnostic: no dollar figure ever crosses the wire.
    for leaky in ("net_pnl", "fees", "max_dd", "balance"):
        assert leaky not in row
    lb.reset_leaderboard_registry()


def test_public_route_is_registered_no_auth():
    src = inspect.getsource(__import__("bot.web.user_gateway", fromlist=["x"]))
    assert 'add_get("/public/leaderboard", handle_leaderboard_public)' in src
    # The public handler must not go through the per-user guard.
    h = inspect.getsource(
        __import__("bot.web.user_gateway", fromlist=["x"]).handle_leaderboard_public)
    assert "_guard_user" not in h


def test_engine_registers_operator_only_when_opted_in():
    from bot.core.engine import RuneClawEngine
    src = inspect.getsource(RuneClawEngine._maybe_publish_proofofpnl)
    assert "PROOFOFPNL_LEADERBOARD_HANDLE" in src        # opt-in gate
    assert "get_leaderboard_registry" in src
    # Registration is gated on a non-empty handle (default OFF) and fail-open.
    assert "if handle:" in src
    assert "pub is not None" in src
