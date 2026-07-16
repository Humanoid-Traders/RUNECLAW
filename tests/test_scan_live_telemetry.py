"""
Scan telemetry must not masquerade paper as the live account.

The website showed paper $10,000 while the live Bitget account was real: the
scan's live-data readout used a parallel path that built its own exchange client
from RAW os.getenv creds (no _env_secret quote-strip), so it threw where
/livebalance worked — and the scan silently fell back to the paper portfolio.

Fix 1: _fetch_live_exchange_data uses CONFIG.exchange creds (the same the live
executor uses). Fix 2: when live mode is on but the balance can't be read, the
payload flags live_unavailable=True and equity=None instead of reporting paper.
"""

from unittest.mock import MagicMock, patch

import bot.skills.scan_skill as ss


def _live_cfg():
    cfg = MagicMock()
    cfg.simulation_mode = False
    cfg.live_trading_enabled = True
    cfg.risk.max_open_positions = 5
    return cfg


def test_live_unavailable_when_fetch_fails_not_paper():
    cfg = _live_cfg()
    with patch("bot.config.CONFIG", cfg), \
         patch.object(ss, "_fetch_live_exchange_data", return_value=None), \
         patch.object(ss, "_build_features_block", return_value={}):
        payload = ss._build_scan_payload([], MagicMock())
    cb = payload["circuit_breaker"]
    assert cb["live_mode"] is True
    assert cb["live_unavailable"] is True, "must flag unavailable, not fake paper"
    assert cb["equity"] is None, "equity must be unknown, never the $10k baseline"


def test_live_available_reports_real_equity():
    cfg = _live_cfg()
    live = {
        "equity": 17.30, "net_pnl": 0.25, "win_rate": 50.0,
        "total_trades": 1, "open_count": 1,
        "open_positions": [], "closed_trades": [],
    }
    with patch("bot.config.CONFIG", cfg), \
         patch.object(ss, "_fetch_live_exchange_data", return_value=live), \
         patch.object(ss, "_build_features_block", return_value={}):
        payload = ss._build_scan_payload([], MagicMock())
    cb = payload["circuit_breaker"]
    assert cb["live_unavailable"] is False
    assert cb["equity"] == 17.30            # the REAL balance, not paper $10k


def test_paper_mode_still_reports_paper_equity():
    cfg = MagicMock()
    cfg.simulation_mode = True               # genuine paper mode
    cfg.live_trading_enabled = False
    cfg.risk.max_open_positions = 5
    engine = MagicMock()
    engine.portfolio.snapshot.return_value = MagicMock(equity_usd=10000.0,
                                                       open_positions=0, daily_pnl=0.0)
    engine.portfolio._history = []
    with patch("bot.config.CONFIG", cfg), \
         patch.object(ss, "_build_features_block", return_value={}):
        payload = ss._build_scan_payload([], engine)
    cb = payload["circuit_breaker"]
    assert cb["live_mode"] is False
    assert cb["live_unavailable"] is False   # paper is legitimately the account


def test_engine_cache_fallback_when_parallel_readout_fails():
    # The parallel Bitget-only readout fails (vault-restored creds it can't
    # see, /venue switch, UTA quirks) but the engine's balance cache — fed by
    # the authenticated live executor every tick — has the real number. The
    # payload must report THAT, not "unavailable".
    from types import SimpleNamespace
    cfg = _live_cfg()
    engine = MagicMock()
    engine._live_balance_cache = {"total": 123.45, "free": 100.0}
    engine.live_executor.open_positions = [
        SimpleNamespace(symbol="HOOD/USDT", direction="LONG",
                        entry_price=115.37, quantity=0.25),
    ]
    with patch("bot.config.CONFIG", cfg), \
         patch.object(ss, "_fetch_live_exchange_data", return_value=None), \
         patch.object(ss, "_build_features_block", return_value={}):
        payload = ss._build_scan_payload([], engine)
    cb = payload["circuit_breaker"]
    assert cb["live_unavailable"] is False
    assert cb["equity"] == 123.45
    assert cb["open_positions"] and cb["open_positions"][0]["symbol"] == "HOODUSDT"


def test_engine_cache_overrides_zero_equity_readout():
    # Venue-gated readout returns equity 0 (e.g. operator switched off Bitget
    # via /venue) — the dashboard treated 0 as unavailable forever. The engine
    # cache is venue-aware; its real total must win.
    cfg = _live_cfg()
    engine = MagicMock()
    engine._live_balance_cache = {"total": 852.10}
    live = {
        "equity": 0, "net_pnl": 0, "win_rate": 0,
        "total_trades": 0, "open_count": 0,
        "open_positions": [], "closed_trades": [],
    }
    engine.live_executor.open_positions = []
    with patch("bot.config.CONFIG", cfg), \
         patch.object(ss, "_fetch_live_exchange_data", return_value=live), \
         patch.object(ss, "_build_features_block", return_value={}):
        payload = ss._build_scan_payload([], engine)
    cb = payload["circuit_breaker"]
    assert cb["live_unavailable"] is False
    assert cb["equity"] == 852.10


def test_still_unavailable_when_cache_empty_too():
    # No readout AND an empty engine cache -> honest "unavailable", never a
    # fabricated number.
    cfg = _live_cfg()
    engine = MagicMock()
    engine._live_balance_cache = {}
    with patch("bot.config.CONFIG", cfg), \
         patch.object(ss, "_fetch_live_exchange_data", return_value=None), \
         patch.object(ss, "_build_features_block", return_value={}):
        payload = ss._build_scan_payload([], engine)
    cb = payload["circuit_breaker"]
    assert cb["live_unavailable"] is True
    assert cb["equity"] is None
