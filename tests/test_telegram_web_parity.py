"""Telegram web-parity commands (/networth /exposure /research /rwa) — PR EE.

One brain, one implementation: exposure/research/rwa are Node-side libs the
web panels already use; the Telegram commands fetch the SAME payloads over the
shared-secret sync channel (bot/utils/web_data_pull.py) and only FORMAT them.
Net worth reuses the gateway's own read-only primitives. Commands degrade to a
"link the web app" hint when the channel is unconfigured — never a crash.
"""
import inspect

import bot.utils.web_data_pull as wdp
from bot.skills.telegram_handler import TelegramHandler


# ── Pull module (mirror the leaderboard_pull tri-state idiom) ────────────────

class TestWebDataPull:
    def test_unconfigured_returns_none(self, monkeypatch):
        monkeypatch.setattr(wdp, "SYNC_SECRET", "")
        assert wdp.fetch_exposure("111") is None
        assert wdp.fetch_research("BTC") is None
        assert wdp.fetch_rwa() is None

    def test_paths_and_sanitization(self, monkeypatch):
        monkeypatch.setattr(wdp, "SYNC_SECRET", "s" * 48)
        calls = []
        monkeypatch.setattr(wdp, "_request",
                            lambda path, body=None: calls.append(path) or {"ok": 1})
        wdp.fetch_exposure("111")
        wdp.fetch_research("pendle/usdt")           # junk stripped, USDT dropped
        wdp.fetch_rwa()
        assert calls == ["/api/bot/sync/exposure?telegram_id=111",
                         "/api/bot/sync/research/PENDLE",
                         "/api/bot/sync/rwa"]

    def test_bad_symbol_never_reaches_the_wire(self, monkeypatch):
        monkeypatch.setattr(wdp, "SYNC_SECRET", "s" * 48)
        monkeypatch.setattr(wdp, "_request",
                            lambda path, body=None: (_ for _ in ()).throw(
                                AssertionError("must not be called")))
        assert wdp.fetch_research("!!!") is None
        assert wdp.fetch_research("") is None


# ── Formatters (pure) ────────────────────────────────────────────────────────

class TestFormatters:
    def test_networth_connected_and_not(self):
        msg = TelegramHandler._format_networth(
            {"equity_usd": 10140.0, "total_pnl": 140.0},
            {"connected": True, "venue": "bitget", "equity_usd": 2500.5})
        assert "$10,140.00" in msg and "Bitget" in msg and "$2,500.50" in msg
        msg2 = TelegramHandler._format_networth(None, {"connected": False})
        assert "not connected" in msg2 and "no snapshot" in msg2

    def test_exposure_rows_flags_and_warnings(self):
        msg = TelegramHandler._format_exposure({
            "net_total_usd": 900.0, "gross_total_usd": 1100.0, "cash_usd": 50.0,
            "assets": [{"base": "ETH", "net_usd": 900.0, "perp_long_usd": 500.0,
                        "perp_short_usd": 0.0, "spot_usd": 400.0,
                        "flags": ["stacked_long"]}],
            "warnings": ["ETH: you hold it on-chain AND are long the perp"],
        })
        assert "ETH" in msg and "stacked_long" in msg
        assert "$900.00" in msg and "⚠️ ETH:" in msg
        assert "nothing here can resize" in msg

    def test_research_strips_web_html_to_telegram_subset(self):
        msg = TelegramHandler._format_research({
            "base": "PENDLE",
            "sections": [{"title": "Market",
                          "html": "Price <b>$3.2</b><br><span data-x=1>vol up</span>"}],
            "disclaimer": "Not financial advice.",
        })
        assert "Research: PENDLE" in msg
        assert "<b>$3.2</b>" in msg and "vol up" in msg
        assert "<span" not in msg and "<br" not in msg
        assert "Not financial advice." in msg

    def test_rwa_mirrors_the_web_radar(self):
        msg = TelegramHandler._format_rwa({
            "sector": {"listed": 5, "change_24h_pct": 1.2, "vs_btc_pct": -0.4,
                       "volume_24h_usd": 2_400_000_000},
            "categories": [{"title": "Treasuries", "listed": 2,
                            "change_24h_pct": 0.8,
                            "tokens": [{"base": "ONDO", "change_24h_pct": 2.1}]}],
        })
        assert "+1.2%" in msg and "-0.4% vs BTC" in msg and "$2.4B" in msg
        assert "Treasuries" in msg and "ONDO +2.1%" in msg
        empty = TelegramHandler._format_rwa({"sector": {"listed": 0}})
        assert "None of the tracked tokens" in empty


# ── Wiring pins ──────────────────────────────────────────────────────────────

def test_commands_registered_and_guarded():
    src = inspect.getsource(TelegramHandler)
    for cmd in ("networth", "exposure", "research", "rwa"):
        assert f'("{cmd}", self._cmd_{cmd})' in src, f"/{cmd} not registered"
    for meth in ("_cmd_networth", "_cmd_exposure", "_cmd_research", "_cmd_rwa"):
        fn_src = inspect.getsource(getattr(TelegramHandler, meth))
        assert "@guard(" in fn_src, f"{meth} must run the auth gate"


def test_commands_fetch_off_the_event_loop():
    # The sync-channel fetch is blocking urllib — it must run in a thread so a
    # slow website can never stall the Telegram event loop.
    for meth in ("_cmd_exposure", "_cmd_research", "_cmd_rwa"):
        src = inspect.getsource(getattr(TelegramHandler, meth))
        assert "to_thread" in src, f"{meth} must not block the loop"
