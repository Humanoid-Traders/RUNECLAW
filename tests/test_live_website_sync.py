"""
Live trading state and real engine-generated signals now reach the website.

Before this fix, the website dashboard was disconnected from LiveExecutor
entirely: bot/core/live_executor.py never imported website_sync, and the
only automatic push wired into engine.py (_on_trade_close_composite) was
bound to the PAPER portfolio's close callback. A live-trading user's
dashboard therefore showed paper/default data (equity 10000, no positions),
never their real Bitget account. Separately, the website's "Signals" feed
only updated from manual Telegram /scan/swing/scalp commands (a different,
simpler scanner) -- the autonomous engine loop that actually generates and
trades on real signals never pushed anything.

Fix: _on_live_position_closed (already the hook both the operator executor
and any per-user executor route through) now also calls
_sync_live_state_to_website, which pushes the live executor's real open
positions, recent closed trades, and equity via the SAME sync_in_background
full-replace path the paper flow already uses. Separately, every TradeIdea
that clears the confidence filter in _tick() (i.e. becomes a pending idea)
is now also pushed to the signal stream via _build_signal_sync_payloads.
"""

import types
from datetime import datetime, timezone

import bot.core.engine as eng_mod
from bot.config import CONFIG
from bot.core.engine import RuneClawEngine, _build_signal_sync_payloads

UTC = timezone.utc


def _live_pos(trade_id, symbol, status="open", pnl=None, close_price=None):
    return types.SimpleNamespace(
        trade_id=trade_id, symbol=symbol, direction="SHORT",
        entry_price=100.0, quantity=1.5, commission=0.3,
        signal_type="momentum_confluence", stop_loss=105.0, take_profit=90.0,
        opened_at=datetime(2026, 6, 30, 12, 0, tzinfo=UTC),
        closed_at=datetime(2026, 6, 30, 13, 0, tzinfo=UTC) if status == "closed" else None,
        close_price=close_price, pnl_usd=pnl, status=status,
    )


def _stub_engine_for_sync(open_positions, closed_positions, equity=326.55, user_id=None):
    executor = types.SimpleNamespace(
        open_positions=open_positions, closed_positions=closed_positions,
        user_id=user_id,
    )
    stub = types.SimpleNamespace(
        live_executor=executor,
        # Truthful-equity source: (equity, source) — None means "unavailable".
        resolve_display_equity_sync=lambda: (equity, "live" if equity is not None else "unavailable"),
    )
    return stub


class TestSyncLiveStateToWebsite:
    def test_pushes_open_and_closed_positions_with_equity(self, monkeypatch):
        captured = {}
        import bot.utils.website_sync as ws

        def _fake_sync_in_background(user_id, equity, positions, closed_trades):
            captured["user_id"] = user_id
            captured["equity"] = equity
            captured["positions"] = positions
            captured["closed_trades"] = closed_trades

        monkeypatch.setattr(ws, "sync_in_background", _fake_sync_in_background)

        opened = _live_pos("TI-1", "HYPE/USDT:USDT")
        closed = _live_pos("TI-2", "ENA/USDT:USDT", status="closed", pnl=0.09, close_price=63.6)
        stub = _stub_engine_for_sync([opened], [closed], equity=326.55, user_id=7)

        RuneClawEngine._sync_live_state_to_website(stub)

        assert captured["user_id"] == 7
        assert captured["equity"] == 326.55
        assert len(captured["positions"]) == 1
        assert captured["positions"][0]["asset"] == "HYPE/USDT:USDT"
        assert captured["positions"][0]["direction"] == "SHORT"
        assert captured["positions"][0]["stop_loss"] == 105.0
        assert len(captured["closed_trades"]) == 1
        assert captured["closed_trades"][0]["exit_price"] == 63.6
        assert captured["closed_trades"][0]["pnl"] == 0.09

    def test_defaults_to_user_id_1_when_executor_has_none(self, monkeypatch):
        import bot.utils.website_sync as ws
        captured = {}
        monkeypatch.setattr(
            ws, "sync_in_background",
            lambda user_id, equity, positions, closed_trades: captured.update(user_id=user_id))
        stub = _stub_engine_for_sync([], [], user_id=None)
        RuneClawEngine._sync_live_state_to_website(stub)
        assert captured["user_id"] == 1

    def test_only_last_50_closed_trades_are_sent(self, monkeypatch):
        import bot.utils.website_sync as ws
        captured = {}
        monkeypatch.setattr(
            ws, "sync_in_background",
            lambda user_id, equity, positions, closed_trades: captured.update(closed=closed_trades))
        closed = [_live_pos(f"TI-{i}", "BTC/USDT", status="closed", pnl=1.0, close_price=100.0)
                  for i in range(75)]
        stub = _stub_engine_for_sync([], closed)
        RuneClawEngine._sync_live_state_to_website(stub)
        assert len(captured["closed"]) == 50


class TestOnLivePositionClosedWiresWebsiteSync:
    def _stub_engine(self):
        stub = types.SimpleNamespace()
        stub._symbol_cooldowns = {}
        stub._symbol_loss_streaks = {}
        stub._symbol_cooldown_seconds = 1800.0
        stub.learning = types.SimpleNamespace(record_closed_outcome=lambda **kw: None)
        stub._auto_refit = types.SimpleNamespace(note_closed_trade=lambda *a, **k: None)
        stub._outcome_regime = lambda symbol: ""
        stub._invalidate_live_balance_cache = lambda: None
        return stub

    def test_sync_is_called_on_close(self, monkeypatch):
        monkeypatch.setattr(eng_mod, "CONFIG", CONFIG)
        stub = self._stub_engine()
        called = {"n": 0}
        stub._sync_live_state_to_website = lambda: called.__setitem__("n", called["n"] + 1)
        eng_mod.RuneClawEngine._on_live_position_closed(
            stub, _live_pos("TI-1", "BTC/USDT", status="closed", pnl=1.0))
        assert called["n"] == 1

    def test_sync_failure_does_not_propagate(self, monkeypatch):
        """Fail-open: a website-sync error must never break the close flow
        (cooldowns/learning outcome recording already happened above it)."""
        monkeypatch.setattr(eng_mod, "CONFIG", CONFIG)
        stub = self._stub_engine()

        def _boom():
            raise RuntimeError("website unreachable")

        stub._sync_live_state_to_website = _boom
        # Must not raise.
        eng_mod.RuneClawEngine._on_live_position_closed(
            stub, _live_pos("TI-1", "BTC/USDT", status="closed", pnl=-1.0))

    def test_missing_sync_method_fails_open(self, monkeypatch):
        """Stubs (like the pre-existing symbol-loss-streak tests) that don't
        define _sync_live_state_to_website at all must still work (AttributeError
        is swallowed by the same fail-open try/except)."""
        monkeypatch.setattr(eng_mod, "CONFIG", CONFIG)
        stub = self._stub_engine()
        eng_mod.RuneClawEngine._on_live_position_closed(
            stub, _live_pos("TI-1", "BTC/USDT", status="closed", pnl=1.0))


def _idea(idea_id, asset, confidence=0.7):
    return types.SimpleNamespace(
        id=idea_id, asset=asset, direction="LONG", confidence=confidence,
        entry_price=100.0, stop_loss=95.0, take_profit=110.0,
        reasoning="setup", timestamp=datetime(2026, 6, 30, 12, 0, tzinfo=UTC),
    )


class TestBuildSignalSyncPayloads:
    def test_shapes_each_idea_as_a_new_signal(self):
        ideas = [_idea("TI-a", "BTC/USDT"), _idea("TI-b", "ETH/USDT", confidence=0.9)]
        rows = _build_signal_sync_payloads(ideas, regime_fn=lambda a: "TREND_UP")
        assert len(rows) == 2
        assert rows[0]["signal_key"] == "TI-a"
        assert rows[0]["symbol"] == "BTC/USDT"
        assert rows[0]["status"] == "NEW"
        assert rows[0]["regime"] == "TREND_UP"
        assert rows[1]["score"] == 0.9

    def test_empty_list_is_empty(self):
        assert _build_signal_sync_payloads([], regime_fn=lambda a: "") == []

    def test_regime_fn_called_per_symbol(self):
        seen = []
        ideas = [_idea("TI-a", "BTC/USDT"), _idea("TI-b", "ETH/USDT")]
        _build_signal_sync_payloads(ideas, regime_fn=lambda a: seen.append(a) or "RANGE")
        assert seen == ["BTC/USDT", "ETH/USDT"]


class TestTickWiresSignalStreamSync:
    def test_tick_pushes_synced_ideas_via_sync_signals_in_background(self):
        import inspect
        src = inspect.getsource(RuneClawEngine._tick)
        assert "_synced_ideas.append(idea)" in src
        assert "sync_signals_in_background(" in src
        assert "_build_signal_sync_payloads(_synced_ideas, self._outcome_regime)" in src
        # Fail-open: the push is wrapped so a sync failure can't break the tick.
        assert "logger.debug(\"Signal stream sync skipped: %s\", _sig_sync_exc)" in src


def test_live_unavailable_equity_syncs_none_not_paper(monkeypatch):
    # LIVE mode with an empty balance cache: the sync must carry None (website
    # renders "unavailable"), never the paper baseline get_effective_equity()
    # silently falls back to.
    captured = {}
    import bot.utils.website_sync as ws
    monkeypatch.setattr(
        ws, "sync_in_background",
        lambda user_id, equity, positions, closed_trades: captured.update(equity=equity))
    stub = _stub_engine_for_sync([], [], equity=None, user_id=7)
    RuneClawEngine._sync_live_state_to_website(stub)
    assert captured["equity"] is None
