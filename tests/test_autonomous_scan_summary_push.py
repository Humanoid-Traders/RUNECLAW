"""
The autonomous scan loop now pushes a real regime/circuit-breaker/key-call
summary to the website every cycle, instead of that data only ever
refreshing from a manual Telegram /scan (or DeepScanSkill/PlaybookSkill
query).

Real incident: a user's dashboard showed the BTC REGIME / CIRCUIT BREAKER /
KEY CALL panels frozen on data from hours earlier (the connection-health
indicator correctly flagged this as "DISCONNECTED") even though the bot was
running and trading normally the whole time -- because those panels only
ever updated when someone happened to type /scan in Telegram. Trade/signal
sync (PR #230) already auto-refreshed; this closes the same gap for the
scan summary.

_build_scan_payload's circuit_breaker section (equity, net_pnl, win_rate,
trades, open positions, rules) only reads from engine.risk/engine.portfolio/
live exchange data -- NOT from the caller's `results` list -- so calling it
with an empty list still yields an accurate circuit-breaker section. Only
regime/key_call need real data from the autonomous scanner's own signal
shape (.symbol/.price/.change_pct_24h/.momentum_score -- different from the
manual scanner's .sym/.rsi/.dir shape), which _push_scan_summary_to_website
derives directly.
"""

import types
from datetime import datetime, timezone

import bot.core.engine as eng_mod
from bot.config import CONFIG
from bot.core.engine import RuneClawEngine

UTC = timezone.utc


def _sig(symbol, price=100.0, change_pct_24h=0.0, momentum_score=0.0):
    return types.SimpleNamespace(
        symbol=symbol, price=price, change_pct_24h=change_pct_24h,
        momentum_score=momentum_score, volume_usd_24h=1_000_000, volume_spike=False,
    )


def _stub_engine():
    history = []
    portfolio = types.SimpleNamespace(
        snapshot=lambda: types.SimpleNamespace(equity_usd=800.0, open_positions=0, daily_pnl=0.0),
        _history=history,
    )
    risk = types.SimpleNamespace(circuit_breaker_active=False)
    stub = types.SimpleNamespace(risk=risk, portfolio=portfolio)
    stub._build_strategy_config_summary = lambda: RuneClawEngine._build_strategy_config_summary(stub)
    return stub


class TestPushScanSummaryToWebsite:
    def test_pushes_circuit_breaker_data_even_with_no_btc_signal(self, monkeypatch):
        monkeypatch.setattr(eng_mod, "CONFIG", CONFIG)
        import bot.utils.website_sync as ws
        captured = {}
        monkeypatch.setattr(ws, "sync_scan_in_background", lambda payload: captured.update(payload=payload))

        stub = _stub_engine()
        RuneClawEngine._push_scan_summary_to_website(stub, [])

        assert captured["payload"]["circuit_breaker"]["equity"] == 800.0
        assert captured["payload"]["regime"]["label"] == "NEUTRAL"  # no BTC signal -> placeholder default

    def test_payload_includes_real_strategy_config(self, monkeypatch):
        monkeypatch.setattr(eng_mod, "CONFIG", CONFIG)
        import bot.utils.website_sync as ws
        captured = {}
        monkeypatch.setattr(ws, "sync_scan_in_background", lambda payload: captured.update(payload=payload))

        stub = _stub_engine()
        RuneClawEngine._push_scan_summary_to_website(stub, [])

        cfg = captured["payload"]["config"]
        assert cfg["min_confidence"] == CONFIG.risk.min_confidence
        assert cfg["max_open_positions"] == CONFIG.risk.max_open_positions
        assert cfg["mode"] in ("LIVE", "PAPER")
        assert cfg["strategy_types"]["scalp"]["time_close_hours"] == CONFIG.strategy_types.get_time_close_hours("scalp")
        assert cfg["strategy_types"]["swing"]["min_confidence"] == CONFIG.strategy_types.get_min_confidence("swing")

    def test_bullish_regime_derived_from_btc_signal(self, monkeypatch):
        monkeypatch.setattr(eng_mod, "CONFIG", CONFIG)
        import bot.utils.website_sync as ws
        captured = {}
        monkeypatch.setattr(ws, "sync_scan_in_background", lambda payload: captured.update(payload=payload))

        stub = _stub_engine()
        signals = [_sig("BTC/USDT:USDT", price=65000.0, change_pct_24h=2.5, momentum_score=0.4)]
        RuneClawEngine._push_scan_summary_to_website(stub, signals)

        assert captured["payload"]["regime"]["label"] == "BULLISH"
        assert captured["payload"]["regime"]["gate"] == 65000.0
        assert "Autonomous scan" in captured["payload"]["key_call"]
        assert "+2.50%" in captured["payload"]["key_call"]

    def test_bearish_regime_derived_from_btc_signal(self, monkeypatch):
        monkeypatch.setattr(eng_mod, "CONFIG", CONFIG)
        import bot.utils.website_sync as ws
        captured = {}
        monkeypatch.setattr(ws, "sync_scan_in_background", lambda payload: captured.update(payload=payload))

        stub = _stub_engine()
        signals = [_sig("BTC/USDT:USDT", change_pct_24h=-3.0, momentum_score=-0.5)]
        RuneClawEngine._push_scan_summary_to_website(stub, signals)

        assert captured["payload"]["regime"]["label"] == "BEARISH"

    def test_neutral_when_btc_signal_is_flat(self, monkeypatch):
        monkeypatch.setattr(eng_mod, "CONFIG", CONFIG)
        import bot.utils.website_sync as ws
        captured = {}
        monkeypatch.setattr(ws, "sync_scan_in_background", lambda payload: captured.update(payload=payload))

        stub = _stub_engine()
        signals = [_sig("BTC/USDT:USDT", change_pct_24h=0.1, momentum_score=0.02)]
        RuneClawEngine._push_scan_summary_to_website(stub, signals)

        assert captured["payload"]["regime"]["label"] == "NEUTRAL"

    def test_tolerates_symbol_format_differences_for_btc_match(self, monkeypatch):
        """The autonomous scanner's symbol format (e.g. BTC/USDT:USDT) must
        still be recognised as BTC via normalize_symbol, same as elsewhere
        in the engine (_outcome_regime already relies on this tolerance)."""
        monkeypatch.setattr(eng_mod, "CONFIG", CONFIG)
        import bot.utils.website_sync as ws
        captured = {}
        monkeypatch.setattr(ws, "sync_scan_in_background", lambda payload: captured.update(payload=payload))

        stub = _stub_engine()
        signals = [_sig("BTC/USDT:USDT", change_pct_24h=5.0, momentum_score=0.9)]
        RuneClawEngine._push_scan_summary_to_website(stub, signals)
        assert captured["payload"]["regime"]["label"] == "BULLISH"


class TestTickWiresScanSummaryPush:
    def test_tick_calls_push_scan_summary_in_a_fail_open_try_except(self):
        import inspect
        src = inspect.getsource(RuneClawEngine._tick)
        assert "self._push_scan_summary_to_website(signals)" in src
        assert 'logger.debug("Autonomous scan summary push skipped: %s", _scan_push_exc)' in src
