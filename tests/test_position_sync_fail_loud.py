"""sync_positions_from_exchange — fail-loud contract (reliability audit #4).

The v3 fetch used to return [] for BOTH "no positions" and "channel failed"
(API error, exception, bad code), so a broken sync passed silently while stale
leverage corrupted margin/risk math. The channel now distinguishes: [] =
genuinely empty / not-applicable, None = FAILED. The sync audits ERROR + feeds
the risk engine's warning-rate breaker on failure, WARNs on an anomalous empty
book, counts unmatched positions, and audits quantity drift REPORT-ONLY.
"""
import pytest
from unittest.mock import MagicMock

import bot.core.live_executor as le
from bot.core.live_executor import LiveExecutor, LivePosition


def _pos(symbol="XPT/USDT", lev=10, qty=2.0, entry=1000.0):
    return LivePosition(
        trade_id="T1", symbol=symbol, direction="SHORT", entry_price=entry,
        quantity=qty, cost_usd=entry * qty / lev, stop_loss=1100.0,
        take_profit=900.0, leverage=lev, status="open",
    )


def _exec(tmp_path, fetch_result):
    e = LiveExecutor(state_dir=str(tmp_path))
    e._risk_engine = MagicMock()
    e._save_positions = MagicMock()
    p = _pos()
    e._positions = {"T1": p}
    return e, p


def _patch_fetch(monkeypatch, result):
    # The sync calls the CLASS staticmethod directly — patch at class level.
    if isinstance(result, Exception):
        def _boom():
            raise result
        monkeypatch.setattr(LiveExecutor, "_fetch_v3_positions_raw",
                            staticmethod(_boom))
    else:
        monkeypatch.setattr(LiveExecutor, "_fetch_v3_positions_raw",
                            staticmethod(lambda: result))


def _audits(monkeypatch):
    calls = []

    def _fake_audit(log, msg, **kw):
        calls.append({"msg": msg, **kw})

    monkeypatch.setattr(le, "audit", _fake_audit)
    return calls


@pytest.mark.asyncio
async def test_fetch_failure_none_audits_error_and_fires_breaker(tmp_path, monkeypatch):
    e, p = _exec(tmp_path, None)
    _patch_fetch(monkeypatch, None)
    calls = _audits(monkeypatch)
    await e.sync_positions_from_exchange()
    errs = [c for c in calls if c.get("action") == "position_sync" and c.get("result") == "ERROR"]
    assert errs, "channel failure must audit position_sync/ERROR"
    e._risk_engine.record_warning.assert_called_with("position_sync_fetch")
    assert p.leverage == 10                      # nothing mutated on failure


@pytest.mark.asyncio
async def test_fetch_raise_audits_error_and_fires_breaker(tmp_path, monkeypatch):
    e, p = _exec(tmp_path, None)
    _patch_fetch(monkeypatch, RuntimeError("auth outage"))
    calls = _audits(monkeypatch)
    await e.sync_positions_from_exchange()
    errs = [c for c in calls if c.get("result") == "ERROR"]
    assert errs
    e._risk_engine.record_warning.assert_called_with("position_sync_fetch")


@pytest.mark.asyncio
async def test_empty_book_with_open_positions_warns_not_errors(tmp_path, monkeypatch):
    e, p = _exec(tmp_path, [])
    _patch_fetch(monkeypatch, [])
    calls = _audits(monkeypatch)
    await e.sync_positions_from_exchange()
    empt = [c for c in calls if c.get("action") == "position_sync" and c.get("result") == "EMPTY"]
    assert empt, "anomalous empty book must be surfaced"
    # Breaker NOT fired for empty (that's reconcile territory, not an outage).
    e._risk_engine.record_warning.assert_not_called()


@pytest.mark.asyncio
async def test_leverage_drift_corrects_and_recomputes_cost(tmp_path, monkeypatch):
    e, p = _exec(tmp_path, None)
    _patch_fetch(monkeypatch, [{"symbol": "XPTUSDT", "leverage": "20", "total": "2.0"}])
    _audits(monkeypatch)
    await e.sync_positions_from_exchange()
    assert p.leverage == 20                       # symbol-map + drift correct
    assert p.cost_usd == pytest.approx(1000.0 * 2.0 / 20)
    e._save_positions.assert_called_once()


@pytest.mark.asyncio
async def test_unparseable_leverage_warns_and_does_not_mutate(tmp_path, monkeypatch):
    e, p = _exec(tmp_path, None)
    _patch_fetch(monkeypatch, [{"symbol": "XPTUSDT", "leverage": "abc"}])
    _audits(monkeypatch)
    await e.sync_positions_from_exchange()        # must not raise
    assert p.leverage == 10


@pytest.mark.asyncio
async def test_unmatched_position_counted_not_silent(tmp_path, monkeypatch, caplog):
    e, p = _exec(tmp_path, None)
    _patch_fetch(monkeypatch, [{"symbol": "OTHERUSDT", "leverage": "5"}])
    _audits(monkeypatch)
    import logging as _l
    with caplog.at_level(_l.WARNING, logger="bot.core.live_executor"):
        await e.sync_positions_from_exchange()
    assert any("not in exchange payload" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_qty_drift_is_report_only(tmp_path, monkeypatch):
    e, p = _exec(tmp_path, None)
    # Exchange reports 1.0 vs tracked 2.0 — >1% drift, must audit but NOT write.
    _patch_fetch(monkeypatch, [{"symbol": "XPTUSDT", "leverage": "10", "total": "1.0"}])
    calls = _audits(monkeypatch)
    await e.sync_positions_from_exchange()
    drifts = [c for c in calls if c.get("action") == "qty_sync" and c.get("result") == "DRIFT"]
    assert drifts, "qty drift must be audited"
    assert p.quantity == 2.0                      # never auto-written


@pytest.mark.asyncio
async def test_no_open_positions_never_fetches(tmp_path, monkeypatch):
    e = LiveExecutor(state_dir=str(tmp_path))
    e._positions = {}
    called = {"n": 0}

    def _count():
        called["n"] += 1
        return []

    monkeypatch.setattr(LiveExecutor, "_fetch_v3_positions_raw", staticmethod(_count))
    await e.sync_positions_from_exchange()
    assert called["n"] == 0


def test_margin_mode_lookup_tolerates_failed_channel(monkeypatch):
    # None from the fetch (channel failed) must read as lookup-failed, not crash.
    monkeypatch.setattr(LiveExecutor, "_fetch_v3_positions_raw",
                        staticmethod(lambda: None))
    assert LiveExecutor._fetch_position_margin_mode_v3("XPTUSDT") is None
