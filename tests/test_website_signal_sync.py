"""
Website signal-stream sync (Stage 1a of the web signals/wallet feature).

The bot pushes every generated signal — taken or not — to the website's global
signal stream (POST /api/bot/sync/signals, UPSERT by signal_key). These cover the
pure payload shaping (scan entry_cards -> signal rows; TradeIdea -> signal row)
and that sync_signals posts the right envelope. No network: _post is stubbed.
"""

from types import SimpleNamespace

import bot.utils.website_sync as ws
from bot.skills.scan_skill import _scan_signal_rows


def _scan_payload():
    return {
        "regime": {"label": "TREND_UP"},
        "timestamp": "2026-06-30 19:30 UTC",
        "entry_cards": [
            {"symbol": "BTC", "direction": "LONG", "score": 0.72,
             "entry": "65000", "stop_loss": "64000", "tp1": "67000",
             "rr": "2.0", "trigger": "RSI 41, Vol 1.8x", "thesis": "long bias"},
            {"symbol": "ETH", "direction": "SHORT", "score": 0.61,
             "entry": "3200", "stop_loss": "3260", "tp1": "3080",
             "rr": "2.0", "trigger": "double top", "thesis": "short bias"},
        ],
    }


class TestScanSignalRows:
    def test_maps_each_card(self):
        rows = _scan_signal_rows(_scan_payload())
        assert len(rows) == 2
        btc = rows[0]
        assert btc["symbol"] == "BTC" and btc["direction"] == "LONG"
        assert btc["confidence"] == 0.72 and btc["score"] == 0.72
        assert btc["entry_price"] == 65000 and btc["stop_loss"] == 64000
        assert btc["take_profit"] == 67000 and btc["rr"] == 2.0
        assert btc["regime"] == "TREND_UP"
        assert btc["status"] == "NEW" and btc["pnl"] is None

    def test_signal_key_is_stable_per_symbol_dir_scan(self):
        rows1 = _scan_signal_rows(_scan_payload())
        rows2 = _scan_signal_rows(_scan_payload())
        # Same scan timestamp -> identical keys (re-push UPSERTs, no duplicate).
        assert rows1[0]["signal_key"] == rows2[0]["signal_key"]
        assert rows1[0]["signal_key"] != rows1[1]["signal_key"]  # per symbol/dir

    def test_skips_cards_missing_symbol_or_direction(self):
        p = _scan_payload()
        p["entry_cards"].append({"direction": "LONG", "entry": "1"})  # no symbol
        p["entry_cards"].append({"symbol": "SOL", "entry": "1"})       # no direction
        assert len(_scan_signal_rows(p)) == 2

    def test_empty_payload_is_empty(self):
        assert _scan_signal_rows({}) == []


class TestBuildSignalPayload:
    def test_from_trade_idea_like(self):
        idea = SimpleNamespace(
            asset="BTC/USDT", direction=SimpleNamespace(value="LONG"),
            confidence=0.8, entry_price=65000.0, stop_loss=64000.0,
            take_profit=67000.0, risk_reward_ratio=2.0, reasoning="setup")
        # direction str() of SimpleNamespace isn't "LONG"; pass the enum-ish value
        idea.direction = "LONG"
        row = ws.build_signal_payload("k1", idea, score=0.8, regime="RANGE")
        assert row["signal_key"] == "k1"
        assert row["symbol"] == "BTC/USDT" and row["direction"] == "LONG"
        assert row["entry_price"] == 65000.0 and row["take_profit"] == 67000.0
        assert row["rr"] == 2.0 and row["regime"] == "RANGE"
        assert row["status"] == "NEW"

    def test_rr_computed_when_absent(self):
        idea = {"asset": "ETH/USDT", "direction": "LONG", "confidence": 0.5,
                "entry_price": 100.0, "stop_loss": 90.0, "take_profit": 120.0}
        row = ws.build_signal_payload("k2", idea)
        # reward 20 / risk 10 = 2.0
        assert row["rr"] == 2.0


class TestSyncSignalsPost:
    def test_posts_envelope(self, monkeypatch):
        captured = {}

        def _fake_post(path, data):
            captured["path"] = path
            captured["data"] = data
            return {"ok": True, "upserted": len(data.get("signals", []))}

        monkeypatch.setattr(ws, "_post", _fake_post)
        rows = _scan_signal_rows(_scan_payload())
        assert ws.sync_signals(rows) is True
        assert captured["path"] == "/api/bot/sync/signals"
        assert captured["data"]["signals"] == rows

    def test_empty_is_noop_true(self, monkeypatch):
        called = {"n": 0}
        monkeypatch.setattr(ws, "_post", lambda p, d: called.__setitem__("n", called["n"] + 1))
        assert ws.sync_signals([]) is True
        assert called["n"] == 0
