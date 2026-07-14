"""Scan payload `features` block (bot/skills/scan_skill.py::_build_features_block).

The features block surfaces newer engine modules (venue, funding clock,
equity throttle, entry timing, shadow book, catalog watch) to the website via
the existing scan sync. It must be fail-open: a partial/absent engine yields
missing keys, never an exception, and the overall scan payload always carries
a `features` dict.
"""

from types import SimpleNamespace

from bot.skills.scan_skill import _build_scan_payload, _build_features_block


def test_payload_carries_features_even_without_engine():
    payload = _build_scan_payload([], engine=None)
    assert isinstance(payload.get("features"), dict)


def test_features_block_never_raises_on_partial_engine():
    # An engine missing every expected attribute must not raise.
    features = _build_features_block(SimpleNamespace())
    assert isinstance(features, dict)
    # funding_clock and entry_timing derive from CONFIG alone, so they are
    # present even with a bare engine.
    assert "funding_clock" in features
    assert set(features["funding_clock"]) == {"enabled", "seconds_to_settlement", "window_min"}
    assert 0 <= features["funding_clock"]["seconds_to_settlement"] <= 8 * 3600
    assert "entry_timing" in features
    assert isinstance(features["entry_timing"]["regimes"], list)


def test_features_venue_and_throttle_from_engine():
    venue = SimpleNamespace(id="bybit", display_name="Bybit")
    engine = SimpleNamespace(
        live_executor=SimpleNamespace(_venue=venue),
        risk=SimpleNamespace(equity_throttle_state=lambda: {
            "enabled": True, "samples": 12, "pf": 1.1,
            "multiplier": 0.9, "status": "THROTTLED"}),
    )
    features = _build_features_block(engine)
    assert features["venue"] == {"id": "bybit", "name": "Bybit"}
    assert features["equity_throttle"]["status"] == "THROTTLED"
    assert features["equity_throttle"]["multiplier"] == 0.9


def test_features_catalog_watch_reads_recent():
    watch = SimpleNamespace(recent=lambda n=10: [
        {"symbol": "NEW/USDT:USDT", "category": "Crypto", "vol_usd": 1e6}])
    engine = SimpleNamespace(scanner=SimpleNamespace(_catalog_watch=watch))
    features = _build_features_block(engine)
    # catalog_watch_enabled defaults ON, so the recent listing shows up.
    assert features["catalog_watch"]["recent"][0]["symbol"] == "NEW/USDT:USDT"
